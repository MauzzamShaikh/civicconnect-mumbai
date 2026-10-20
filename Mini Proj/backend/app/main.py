from contextlib import asynccontextmanager
import base64
import binascii
import hmac
import json
import logging
import math
import os
from datetime import timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from langdetect import LangDetectException, detect
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .db import Base, SessionLocal, engine
from .models import Complaint, ComplaintStatusHistory, ComplaintSubscription, Notification, User, utc_now
from .auth import get_current_user, router as auth_router
from .seed import seed
from . import services
from .services import VideoProcessingError, process_submission
from .geo import MUMBAI_LAT_RANGE, MUMBAI_LNG_RANGE


logger = logging.getLogger(__name__)
# Keep the legacy value queryable for existing records without using it in the current workflow.
COMPLAINT_STATUSES = {"open", "inspection_scheduled", "contractor_allotted", "in_progress", "resolved", "pending_review", "rejected", "pending_human_review"}
STAFF_TRANSITIONS = {
    "open": {"inspection_scheduled", "contractor_allotted", "in_progress", "resolved"},
    "inspection_scheduled": {"open", "contractor_allotted", "in_progress", "resolved"},
    "contractor_allotted": {"inspection_scheduled", "in_progress", "resolved"},
    "in_progress": {"inspection_scheduled", "contractor_allotted", "resolved"},
    "resolved": {"open"},
}
SEVERITY_FILTERS = {"Low", "Medium", "High"}
SUBMISSION_EVENT_PERSISTENCE_ATTEMPTS = 3


def _timestamp_iso(value):
    """Serialize all API timestamps as explicit UTC instants.

    Legacy complaint.created_at values are naive but were written with
    datetime.utcnow(), so treat those as UTC instead of letting browsers
    interpret them as local wall time.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE complaints "
                "ADD COLUMN IF NOT EXISTS has_video BOOLEAN DEFAULT FALSE"
            )
        )
        connection.execute(
            text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS video_data TEXT")
        )
        connection.execute(text(
            "DO $$ BEGIN "
            "IF (SELECT data_type FROM information_schema.columns "
            "WHERE table_name='complaints' AND column_name='created_at') = 'timestamp without time zone' THEN "
            "ALTER TABLE complaints ALTER COLUMN created_at TYPE TIMESTAMPTZ "
            "USING created_at AT TIME ZONE 'UTC'; "
            "END IF; END $$;"
        ))
        connection.execute(
            text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS uploaded_photo_data TEXT")
        )
        connection.execute(
            text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS transcript TEXT")
        )
        connection.execute(text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS citizen_id INTEGER"))
        connection.execute(text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS source VARCHAR(30) NOT NULL DEFAULT 'bmc_staff'"))
        connection.execute(text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS review_state VARCHAR(30) NOT NULL DEFAULT 'accepted'"))
        connection.execute(text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS rejection_reason VARCHAR(500)"))
        connection.execute(text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS location_source VARCHAR(30)"))
        connection.execute(text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS source_post_id VARCHAR(255)"))
        connection.execute(text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS source_author VARCHAR(255)"))
        connection.execute(text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS source_url TEXT"))
        duplicate_source_posts = connection.execute(text(
            "SELECT source, source_post_id, ARRAY_AGG(id ORDER BY id) AS complaint_ids "
            "FROM complaints WHERE source_post_id IS NOT NULL "
            "GROUP BY source, source_post_id HAVING COUNT(*) > 1"
        )).mappings().all()
        normalization_collisions = connection.execute(text(
            "WITH normalized AS ("
            "SELECT id, source, source_post_id, "
            "CASE WHEN citizen_id IS NOT NULL AND source = 'bmc_staff' THEN 'citizen' ELSE source END AS normalized_source "
            "FROM complaints WHERE source_post_id IS NOT NULL"
            ") SELECT normalized_source AS source, source_post_id, ARRAY_AGG(id ORDER BY id) AS complaint_ids "
            "FROM normalized GROUP BY normalized_source, source_post_id "
            "HAVING COUNT(*) > 1 AND BOOL_OR(normalized_source <> source)"
        )).mappings().all()
        if duplicate_source_posts or normalization_collisions:
            conflicts = [
                "existing duplicate: "
                f"(source={row['source']!r}, source_post_id={row['source_post_id']!r}), "
                f"complaint IDs={list(row['complaint_ids'])}"
                for row in duplicate_source_posts
            ]
            conflicts.extend(
                "source-normalization collision: "
                f"(normalized_source={row['source']!r}, source_post_id={row['source_post_id']!r}), "
                f"complaint IDs={list(row['complaint_ids'])}"
                for row in normalization_collisions
            )
            raise RuntimeError(
                "Cannot create CivicSocial idempotency index because source/post ID conflicts exist: "
                + "; ".join(conflicts)
                + ". Resolve conflicts explicitly without deleting, merging, or rewriting complaint records."
            )
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_complaints_source_post_id ON complaints (source, source_post_id)"))
        connection.execute(text("UPDATE complaints SET source='citizen' WHERE citizen_id IS NOT NULL AND source='bmc_staff'"))
        # Legacy resolution-evidence state is a work-status value, not a review
        # decision. Normalize it only when review_state is authoritative and no
        # longer pending; preserve pending records for the review queue.
        connection.execute(text("UPDATE complaints SET status='open' WHERE status='pending_human_review' AND review_state <> 'pending'"))
        connection.execute(text("CREATE TABLE IF NOT EXISTS complaint_subscriptions (id SERIAL PRIMARY KEY, complaint_id VARCHAR NOT NULL REFERENCES complaints(id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), CONSTRAINT uq_complaint_subscription_user UNIQUE (complaint_id,user_id))"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_complaint_subscriptions_complaint_id ON complaint_subscriptions (complaint_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_complaint_subscriptions_user_id ON complaint_subscriptions (user_id)"))
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_complaints_citizen_id ON complaints (citizen_id)")
        )
        connection.execute(text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'complaints_citizen_id_fkey') THEN "
            "ALTER TABLE complaints ADD CONSTRAINT complaints_citizen_id_fkey "
            "FOREIGN KEY (citizen_id) REFERENCES users(id) ON DELETE SET NULL; "
            "END IF; END $$;"
        ))
    seed()
    with SessionLocal() as startup_session:
        services.reconcile_named_place_clusters(startup_session)
    yield


app = FastAPI(title="BMC Civic Complaint Clustering", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)


class Submission(BaseModel):
    text: str = ""
    language: str | None = None
    lat: float | None = None
    lng: float | None = None
    image_base64: str | None = None
    video_base64: str | None = None

    @field_validator("image_base64", "video_base64")
    @classmethod
    def validate_media(cls, value, info):
        return _validate_media_data_url(value, info.field_name)


MAX_SUBMISSION_REQUEST_BYTES = 94 * 1024 * 1024
MAX_SUBMISSION_IMAGE_BYTES = 10 * 1024 * 1024
MAX_SUBMISSION_VIDEO_BYTES = 55 * 1024 * 1024
MAX_CITIZEN_REQUEST_BYTES = MAX_SUBMISSION_REQUEST_BYTES
MAX_CITIZEN_IMAGE_BYTES = MAX_SUBMISSION_IMAGE_BYTES
MAX_CITIZEN_VIDEO_BYTES = MAX_SUBMISSION_VIDEO_BYTES


def _ebml_document_type(raw: bytes):
    """Read the EBML DocType used to distinguish WebM from other EBML files."""
    def read_vint(offset, *, keep_marker):
        if offset >= len(raw):
            return None, 0
        first = raw[offset]
        marker = 0x80
        width = 1
        while width <= 8 and not first & marker:
            marker >>= 1
            width += 1
        if width > 8 or offset + width > len(raw):
            return None, 0
        value = first if keep_marker else first & (marker - 1)
        for byte in raw[offset + 1 : offset + width]:
            value = (value << 8) | byte
        return value, width

    if not raw.startswith(b"\x1a\x45\xdf\xa3"):
        return None
    header_size, width = read_vint(4, keep_marker=False)
    if not width:
        return None
    offset = 4 + width
    header_end = min(offset + header_size, len(raw))
    while offset < header_end:
        element_id, id_width = read_vint(offset, keep_marker=True)
        if not id_width:
            return None
        offset += id_width
        element_size, size_width = read_vint(offset, keep_marker=False)
        if not size_width:
            return None
        offset += size_width
        element_end = offset + element_size
        if element_end > header_end:
            return None
        if element_id == 0x4282:
            try:
                return raw[offset:element_end].decode("ascii").lower()
            except UnicodeDecodeError:
                return None
        offset = element_end
    return None


def _ftyp_brands(raw: bytes):
    if len(raw) < 16 or raw[4:8] != b"ftyp":
        return set()
    box_size = int.from_bytes(raw[:4], "big")
    if box_size < 16 or box_size > len(raw) or (box_size - 16) % 4:
        return set()
    brands = {raw[8:12]}
    brands.update(raw[offset : offset + 4] for offset in range(16, box_size, 4))
    return brands


def _validate_media_data_url(value, field_name):
    if value is None:
        return None
    if not isinstance(value, str) or "," not in value:
        raise ValueError("Media must be a supported data URL")
    header, encoded = value.split(",", 1)
    header = header.lower()
    if field_name == "image_base64":
        mime_types = {"data:image/jpeg;base64", "data:image/png;base64", "data:image/webp;base64", "data:image/gif;base64"}
        byte_limit = MAX_SUBMISSION_IMAGE_BYTES
    else:
        mime_types = {"data:video/mp4;base64", "data:video/quicktime;base64", "data:video/webm;base64"}
        byte_limit = MAX_SUBMISSION_VIDEO_BYTES
    if header not in mime_types:
        raise ValueError("Unsupported media type")
    if len(encoded) > 4 * ((byte_limit + 2) // 3):
        raise ValueError("Media exceeds the allowed size")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Invalid base64 media") from error
    if not raw or len(raw) > byte_limit:
        raise ValueError("Media is empty or exceeds the allowed size")

    if field_name == "image_base64":
        matches = {
            "data:image/jpeg;base64": raw.startswith(bytes((0xFF, 0xD8, 0xFF))),
            "data:image/png;base64": raw.startswith(bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))),
            "data:image/gif;base64": raw.startswith((b"GIF87a", b"GIF89a")),
            "data:image/webp;base64": len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP",
        }
    else:
        brands = _ftyp_brands(raw)
        mp4_brands = {
            b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"iso7", b"iso8", b"iso9",
            b"mp41", b"mp42", b"avc1", b"M4V ", b"M4VP", b"MSNV", b"dash", b"F4V ", b"F4P ", b"M4A ",
        }
        matches = {
            "data:video/mp4;base64": bool(brands & mp4_brands) or any(brand.startswith((b"3gp", b"3g2")) for brand in brands),
            "data:video/quicktime;base64": b"qt  " in brands,
            "data:video/webm;base64": _ebml_document_type(raw) == "webm",
        }
    if not matches.get(header, False):
        raise ValueError("Media content does not match its declared type")
    return value


class CitizenSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=5, max_length=10000)
    language: str | None = Field(default=None, max_length=20)
    image_base64: str | None = None
    video_base64: str | None = None
    lat: float | None = None
    lng: float | None = None

    @field_validator("text")
    @classmethod
    def validate_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Complaint description is required")
        return value

    @field_validator("image_base64", "video_base64")
    @classmethod
    def validate_media(cls, value, info):
        return _validate_media_data_url(value, info.field_name)

    @model_validator(mode="after")
    def validate_location_pin(self):
        if (self.lat is None) != (self.lng is None):
            raise ValueError("Both map coordinates are required")
        if self.lat is not None and not (
            math.isfinite(self.lat)
            and math.isfinite(self.lng)
            and MUMBAI_LAT_RANGE[0] <= self.lat <= MUMBAI_LAT_RANGE[1]
            and MUMBAI_LNG_RANGE[0] <= self.lng <= MUMBAI_LNG_RANGE[1]
        ):
            raise ValueError("Map pin must be within the supported Mumbai area")
        return self


class CivicSocialSubmission(CitizenSubmission):
    original_post_id: str = Field(min_length=1, max_length=255)
    author: str | None = Field(default=None, max_length=255)
    source_post_url: str | None = Field(default=None, max_length=2048)

    @field_validator("original_post_id", "author")
    @classmethod
    def trim_source_identity(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value

    @field_validator("source_post_url")
    @classmethod
    def validate_source_post_url(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Source post URL must be an HTTP or HTTPS URL")
        return value


class StatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=40)


class RejectDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def _complaint_media_flags(complaint):
    has_video = bool(complaint.has_video or complaint.video_data)
    has_uploaded_photo = bool(
        complaint.uploaded_photo_data
        or (complaint.image_data and not has_video)
    )
    return {
        "has_video": has_video,
        "has_image": has_uploaded_photo,
        "has_uploaded_photo": has_uploaded_photo,
        "has_media": bool(complaint.has_media or has_video or has_uploaded_photo or complaint.image_url),
    }


def _location_fields(complaint):
    confidence = services.location_confidence(complaint.location_source)
    coordinates_valid = services.valid_location_coordinates(complaint.lat, complaint.lng)
    # Legacy coordinates with no recognized source are deliberately serialized
    # as unresolved. The database values remain untouched for staff investigation.
    expose_coordinates = confidence != "unknown" and coordinates_valid
    return {
        "lat": complaint.lat if expose_coordinates else None,
        "lng": complaint.lng if expose_coordinates else None,
        "location_source": complaint.location_source,
        "location_confidence": confidence if expose_coordinates else "unknown",
    }


def public(complaint):
    media_flags = _complaint_media_flags(complaint)
    return {
        "id": complaint.id,
        "text": complaint.text,
        "language": complaint.language,
        "ward": complaint.ward_code,
        "location": complaint.location,
        "category": complaint.category or services.infer_category(complaint.text),
        "cluster_id": complaint.cluster_id,
        "severity": complaint.final_severity,
        "severity_score": complaint.final_score,
        "text_severity": complaint.text_severity,
        "photo_severity": complaint.photo_severity,
        **media_flags,
        "is_real": complaint.is_real,
        "rank_in_cluster": complaint.rank_in_cluster,
        "status": ("pending_review" if complaint.review_state == "pending" else "rejected" if complaint.review_state == "rejected" else complaint.status),
        **_location_fields(complaint),
        "created_at": _timestamp_iso(complaint.created_at),
    }


def _citizen_complaint_summary(complaint):
    media_flags = _complaint_media_flags(complaint)
    return {
        "id": complaint.id,
        "text": complaint.text,
        "created_at": _timestamp_iso(complaint.created_at),
        "category": complaint.category or services.infer_category(complaint.text),
        "ward": complaint.ward_code,
        "severity": complaint.final_severity,
        "severity_score": complaint.final_score,
        "status": ("pending_review" if complaint.review_state == "pending" else "rejected" if complaint.review_state == "rejected" else complaint.status),
        "rejection_reason": complaint.rejection_reason,
        "location": complaint.location,
        **_location_fields(complaint),
        **media_flags,
    }


def _add_history(db: Session, complaint: Complaint, event_type: str, *, from_status=None,
                 to_status=None, actor: User | None = None, actor_label="BMC Team", event_key: str):
    existing = db.scalar(select(ComplaintStatusHistory.id).where(ComplaintStatusHistory.event_key == event_key))
    if existing:
        return
    db.add(ComplaintStatusHistory(
        complaint_id=complaint.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor_user_id=actor.id if actor else None,
        actor_label=actor_label,
        event_key=event_key,
        created_at=utc_now(),
    ))


def _add_notification(db: Session, *, recipient: User, audience_role: str, complaint_id: str,
                      event_key: str, title: str, summary: str):
    if db.scalar(select(Notification.id).where(Notification.event_key == event_key)):
        return
    db.add(Notification(
        recipient_user_id=recipient.id,
        audience_role=audience_role,
        complaint_id=complaint_id,
        event_key=event_key,
        title=title,
        summary=summary,
        created_at=utc_now(),
    ))


def _record_submission_events(db: Session, complaint: Complaint):
    """Persist the initial timeline and role-scoped notices after complaint commit."""
    try:
        citizen = db.get(User, complaint.citizen_id) if complaint.citizen_id else None
        _add_history(
            db, complaint, "submitted", to_status="pending_review" if complaint.review_state == "pending" else complaint.status,
            actor=citizen,
            actor_label="Citizen" if complaint.citizen_id else "BMC Team",
            event_key=f"complaint-submitted:{complaint.id}",
        )
        if complaint.citizen_id and citizen:
            _add_notification(
                db, recipient=citizen, audience_role="citizen", complaint_id=complaint.id,
                event_key=f"complaint-received:{complaint.id}", title="Complaint received",
                summary=f"Complaint {complaint.id} was submitted and is pending BMC review.",
            )
        if complaint.review_state == "pending":
            staff = db.scalars(select(User).where(User.role == "admin", User.is_active.is_(True))).all()
            severity_note = f" Severity: {complaint.final_severity}." if complaint.final_severity in SEVERITY_FILTERS else ""
            for user in staff:
                _add_notification(
                    db, recipient=user, audience_role="admin", complaint_id=complaint.id,
                    event_key=f"new-review-complaint:{complaint.id}:user:{user.id}",
                    title="New complaint awaiting review", summary=f"Complaint {complaint.id} needs review.{severity_note}",
                )
        db.commit()
        return True
    except Exception as error:
        db.rollback()
        logger.error(
            "COMPLAINT event persistence failed complaint_id=%s type=%s; complaint remains committed",
            complaint.id,
            type(error).__name__,
        )
        return False


def _record_submission_events_with_retry(db: Session, complaint: Complaint) -> bool:
    """Retry only the idempotent event transaction; never rerun complaint processing."""
    for attempt in range(1, SUBMISSION_EVENT_PERSISTENCE_ATTEMPTS + 1):
        if _record_submission_events(db, complaint):
            return True
        if attempt < SUBMISSION_EVENT_PERSISTENCE_ATTEMPTS:
            logger.warning(
                "COMPLAINT event persistence retry complaint_id=%s attempt=%s",
                complaint.id,
                attempt + 1,
            )
    logger.error(
        "COMPLAINT persisted complaint_id=%s but initial history/notification persistence remains incomplete; no complaint retry requested",
        complaint.id,
    )
    return False


def _history_for(db: Session, complaint: Complaint):
    rows = db.scalars(
        select(ComplaintStatusHistory)
        .where(ComplaintStatusHistory.complaint_id == complaint.id)
        .order_by(ComplaintStatusHistory.created_at.asc(), ComplaintStatusHistory.id.asc())
    ).all()
    if not rows:
        return [{
            "id": None,
            "event_type": "submitted",
            "from_status": None,
            "to_status": complaint.status,
            "actor_label": "Citizen" if complaint.citizen_id else "BMC Team",
            "created_at": _timestamp_iso(complaint.created_at),
        }]
    return [{
        "id": row.id,
        "event_type": row.event_type,
        "from_status": row.from_status,
        "to_status": row.to_status,
        "actor_label": row.actor_label,
        "created_at": _timestamp_iso(row.created_at),
    } for row in rows]


def _notification_json(item: Notification, complaint: Complaint | None = None):
    title, summary = item.title, item.summary
    if title == "New complaint awaiting review" and complaint and complaint.review_state != "pending":
        result = "rejected" if complaint.review_state == "rejected" else "accepted"
        title = "Complaint review completed"
        summary = f"Complaint {complaint.id} was {result}; it is no longer awaiting review."
    return {
        "id": item.id,
        "complaint_id": item.complaint_id,
        "title": title,
        "summary": summary,
        "read": item.read_at is not None,
        "created_at": _timestamp_iso(item.created_at),
    }


def _complaint_query(db: Session, *, citizen_id=None, q=None, complaint_status=None,
                     category=None, ward=None, severity=None, sort="newest"):
    query = select(Complaint)
    if citizen_id is not None:
        query = query.where(Complaint.citizen_id == citizen_id)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(or_(
            Complaint.id.ilike(pattern), Complaint.text.ilike(pattern),
            Complaint.category.ilike(pattern), Complaint.ward_code.ilike(pattern),
            Complaint.ward_area.ilike(pattern), Complaint.location.ilike(pattern),
        ))
    if complaint_status:
        if complaint_status not in COMPLAINT_STATUSES:
            raise HTTPException(422, "Unsupported complaint status filter.")
        if complaint_status in {"pending_review", "pending_human_review"}:
            query = query.where(Complaint.review_state == "pending")
        elif complaint_status == "rejected":
            query = query.where(Complaint.review_state == "rejected")
        else:
            query = query.where(Complaint.review_state == "accepted", Complaint.status == complaint_status)
    elif citizen_id is None:
        query = query.where(Complaint.review_state == "accepted", Complaint.status.in_(("open", "inspection_scheduled", "contractor_allotted", "in_progress", "resolved")))
    if category:
        query = query.where(Complaint.category == category)
    if ward:
        query = query.where(Complaint.ward_code == ward)
    if severity:
        if severity not in SEVERITY_FILTERS:
            raise HTTPException(422, "Unsupported severity filter.")
        query = query.where(Complaint.final_severity == severity)
    order = Complaint.created_at.asc() if sort == "oldest" else Complaint.created_at.desc()
    return query.order_by(order, Complaint.id.asc())


def _page_complaints(db, query, limit, offset, serializer, category_owner=None):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    items = db.scalars(query.limit(limit).offset(offset)).all()
    categories_query = select(Complaint.category).where(Complaint.category.is_not(None))
    if category_owner is not None:
        categories_query = categories_query.where(Complaint.citizen_id == category_owner)
    categories = db.scalars(categories_query.distinct().order_by(Complaint.category)).all()
    return {"items": [serializer(item) for item in items], "total": total,
            "limit": limit, "offset": offset, "categories": categories}


def _read_submission(request: Request, schema, max_bytes=MAX_SUBMISSION_REQUEST_BYTES):
    """Read JSON with a hard cap before parsing embedded base64 media."""
    async def read_limited():
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > max_bytes:
                    raise HTTPException(status_code=413, detail="Submission is larger than the allowed limit.")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid request size.")
        chunks = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail="Submission is larger than the allowed limit.")
            chunks.append(chunk)
        try:
            data = json.loads(b"".join(chunks))
            return schema.model_validate(data)
        except ValidationError as error:
            safe_errors = [
                {key: detail[key] for key in ("loc", "msg", "type") if key in detail}
                for detail in error.errors()
            ]
            raise RequestValidationError(safe_errors) from error
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="Submission must be valid JSON.")
    return read_limited()


def _read_citizen_submission(request: Request):
    return _read_submission(request, CitizenSubmission, MAX_CITIZEN_REQUEST_BYTES)


def require_citizen(current_user: User = Depends(get_current_user)):
    if current_user.role != "citizen":
        raise HTTPException(status_code=403, detail="Citizen account required.")
    return current_user


def require_bmc_staff(current_user: User = Depends(get_current_user)):
    # The current project uses `admin` for trusted BMC staff; public registration
    # only creates `citizen` accounts.
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="BMC staff account required.")
    return current_user


def _process_citizen_submission(payload: CitizenSubmission, citizen_id: int, pinned_location):
    """Run synchronous ML and persistence with a Session created in this worker thread."""
    db = SessionLocal()
    try:
        complaint = process_submission(
            db,
            payload,
            citizen_id=citizen_id,
            pinned_location=pinned_location,
            source="citizen",
            pending_review=True,
        )
        events_recorded = _record_submission_events_with_retry(db, complaint)
        if not events_recorded:
            # Complaint processing already committed; keep the successful response to avoid
            # prompting a client retry that would create another complaint.
            logger.error("CITIZEN submission response retained complaint_id=%s", complaint.id)
        db.refresh(complaint)
        summary = _citizen_complaint_summary(complaint)
        return {"complaint": summary, "id": complaint.id, "status": summary["status"]}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _civicsocial_response(complaint: Complaint, *, created: bool):
    effective_status = (
        "pending_review" if complaint.review_state == "pending"
        else "rejected" if complaint.review_state == "rejected"
        else complaint.status
    )
    return {
        "complaint_reference": complaint.id,
        "reference": complaint.id,
        "review_state": complaint.review_state,
        "status": effective_status,
        "created": created,
        "already_existed": not created,
    }


def require_civicsocial_secret(authorization: str | None = Header(default=None)):
    expected = os.getenv("CIVICSOCIAL_INTEGRATION_SECRET", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="CivicSocial integration is not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid integration credentials.")


def _raise_civicsocial_event_persistence_error(complaint: Complaint):
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"Complaint {complaint.id} was saved, but its initial history or notifications "
            "could not be persisted. Retry with the same original_post_id to safely resume event persistence."
        ),
        headers={"Retry-After": "2"},
    )


def _process_civicsocial_submission(payload: CivicSocialSubmission):
    db = SessionLocal()
    try:
        def existing_submission():
            return db.scalar(select(Complaint).where(
                Complaint.source == "civicsocial",
                Complaint.source_post_id == payload.original_post_id,
            ))

        existing = existing_submission()
        if existing:
            if not _record_submission_events_with_retry(db, existing):
                _raise_civicsocial_event_persistence_error(existing)
            db.refresh(existing)
            return _civicsocial_response(existing, created=False)

        processing_payload = CitizenSubmission(
            text=payload.text,
            language=payload.language,
            image_base64=payload.image_base64,
            video_base64=payload.video_base64,
            lat=payload.lat,
            lng=payload.lng,
        )
        if not processing_payload.language:
            try:
                processing_payload.language = detect(processing_payload.text)
            except LangDetectException:
                processing_payload.language = "unknown"
        try:
            complaint = process_submission(
                db,
                processing_payload,
                pinned_location=(payload.lat, payload.lng) if payload.lat is not None else None,
                source="civicsocial",
                pending_review=True,
                source_post_id=payload.original_post_id,
                source_author=payload.author,
                source_url=payload.source_post_url,
            )
        except IntegrityError:
            # A concurrent delivery may have won the unique source/post key.
            db.rollback()
            existing = existing_submission()
            if not existing:
                raise
            if not _record_submission_events_with_retry(db, existing):
                _raise_civicsocial_event_persistence_error(existing)
            db.refresh(existing)
            return _civicsocial_response(existing, created=False)
        except SQLAlchemyError:
            # Recover when a concurrent request committed the unique
            # source/post row but this request saw a transient DB error.
            db.rollback()
            existing = existing_submission()
            if not existing:
                raise
            if not _record_submission_events_with_retry(db, existing):
                _raise_civicsocial_event_persistence_error(existing)
            db.refresh(existing)
            return _civicsocial_response(existing, created=False)
        except SQLAlchemyError:
            # A retry can hit the unique source/post key after another
            # concurrent request committed but lost its response.
            db.rollback()
            existing = existing_submission()
            if not existing:
                raise
            if not _record_submission_events_with_retry(db, existing):
                _raise_civicsocial_event_persistence_error(existing)
            db.refresh(existing)
            return _civicsocial_response(existing, created=False)

        events_recorded = _record_submission_events_with_retry(db, complaint)
        if not events_recorded:
            logger.error("CIVICSOCIAL submission response retained complaint_id=%s", complaint.id)
            _raise_civicsocial_event_persistence_error(complaint)
        db.refresh(complaint)
        return _civicsocial_response(complaint, created=True)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/citizen/complaints", status_code=status.HTTP_201_CREATED)
async def submit_citizen_complaint(
    request: Request,
    current_user: User = Depends(require_citizen),
):
    payload = await _read_citizen_submission(request)
    if not payload.language:
        try:
            payload.language = detect(payload.text)
        except LangDetectException:
            payload.language = "unknown"
    pinned_location = (payload.lat, payload.lng) if payload.lat is not None else None
    try:
        return await run_in_threadpool(_process_citizen_submission, payload, current_user.id, pinned_location)
    except VideoProcessingError as error:
        raise HTTPException(status_code=422, detail="Video could not be processed. Please choose another file.") from error
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=422, detail="The complaint media or location could not be processed.") from error


@app.post("/integrations/civicsocial/complaints", status_code=status.HTTP_201_CREATED)
async def submit_civicsocial_complaint(
    request: Request,
    response: Response,
    _service: None = Depends(require_civicsocial_secret),
):
    payload = await _read_submission(request, CivicSocialSubmission, MAX_CITIZEN_REQUEST_BYTES)
    try:
        result = await run_in_threadpool(_process_civicsocial_submission, payload)
        response.status_code = status.HTTP_201_CREATED if result["created"] else status.HTTP_200_OK
        return result
    except VideoProcessingError as error:
        raise HTTPException(status_code=422, detail="Video could not be processed. Please submit valid evidence.") from error
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=422, detail="The complaint media could not be processed.") from error


@app.get("/citizen/dashboard")
def citizen_dashboard(db: Session = Depends(get_db), current_user: User = Depends(require_citizen)):
    base = select(Complaint).where(Complaint.citizen_id == current_user.id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    accepted_states = ("open", "inspection_scheduled", "contractor_allotted", "in_progress", "resolved")
    resolved = db.scalar(select(func.count()).where(Complaint.citizen_id == current_user.id, Complaint.review_state == "accepted", Complaint.status == "resolved")) or 0
    active_count = db.scalar(select(func.count()).where(Complaint.citizen_id == current_user.id, Complaint.review_state == "accepted", Complaint.status.in_(accepted_states), Complaint.status != "resolved")) or 0
    recent = db.scalars(base.order_by(Complaint.created_at.desc()).limit(5)).all()
    history = db.scalars(select(ComplaintStatusHistory).join(Complaint).where(
        Complaint.citizen_id == current_user.id
    ).order_by(ComplaintStatusHistory.created_at.desc()).limit(10)).all()
    followed = db.scalars(select(Complaint).join(ComplaintSubscription, ComplaintSubscription.complaint_id == Complaint.id).where(
        ComplaintSubscription.user_id == current_user.id, ComplaintSubscription.active.is_(True)
    ).order_by(ComplaintSubscription.created_at.desc())).all()
    return {
        "total": total, "resolved": resolved, "active": active_count,
        "recent_complaints": [_citizen_complaint_summary(row) for row in recent],
        "followed_complaints": [_citizen_complaint_summary(row) for row in followed],
        "recent_activity": [{"complaint_id": row.complaint_id, "event_type": row.event_type,
                             "from_status": row.from_status, "to_status": row.to_status,
                             "created_at": _timestamp_iso(row.created_at)}
                            for row in history],
    }


@app.get("/citizen/complaints")
def list_citizen_complaints(
    q: str | None = None,
    status: str | None = None,
    category: str | None = None,
    sort: str = "newest",
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_citizen),
):
    if sort not in {"newest", "oldest"} or limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(422, "Invalid pagination or sort value.")
    query = _complaint_query(db, citizen_id=current_user.id, q=q, complaint_status=status,
                             category=category, sort=sort)
    return _page_complaints(db, query, limit, offset, _citizen_complaint_summary, category_owner=current_user.id)


def _owned_citizen_complaint(complaint_id: str, current_user: User, db: Session):
    complaint = db.scalar(
        select(Complaint).where(
            Complaint.id == complaint_id,
            Complaint.citizen_id == current_user.id,
        )
    )
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    return complaint


@app.get("/citizen/complaints/{complaint_id}")
def get_citizen_complaint(
    complaint_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_citizen),
):
    complaint = _owned_citizen_complaint(complaint_id, current_user, db)
    return {**_citizen_complaint_summary(complaint), "history": _history_for(db, complaint)}


@app.get("/citizen/complaints/{complaint_id}/media")
def get_citizen_complaint_media(
    complaint_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_citizen),
):
    complaint = _owned_citizen_complaint(complaint_id, current_user, db)
    return {
        "image_data": complaint.image_data,
        "uploaded_photo_data": complaint.uploaded_photo_data,
        "video_data": complaint.video_data,
        "transcript": complaint.transcript,
        **_complaint_media_flags(complaint),
        "legacy_external_media_unavailable": bool(
            complaint.image_url and not complaint.image_data and not complaint.uploaded_photo_data
        ),
    }


@app.get("/bmc/complaints/{complaint_id}/media")
def get_bmc_complaint_media(
    complaint_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_bmc_staff),
):
    complaint = db.get(Complaint, complaint_id)
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    has_video = complaint.has_video or bool(complaint.video_data)
    uploaded_photo = complaint.uploaded_photo_data
    if not uploaded_photo and not has_video:
        uploaded_photo = complaint.image_data
    return {
        "complaint_id": complaint.id,
        **_complaint_media_flags(complaint),
        "has_uploaded_photo": bool(uploaded_photo),
        "uploaded_photo_data": uploaded_photo,
        "has_video": has_video,
        "video_data": complaint.video_data,
        "representative_frame_data": complaint.image_data if has_video else None,
        "transcript": complaint.transcript,
        "legacy_external_media_unavailable": bool(
            complaint.image_url and not uploaded_photo and not complaint.video_data
        ),
    }


def _process_bmc_submission(payload: Submission):
    db = SessionLocal()
    try:
        complaint = process_submission(db, payload)
        events_recorded = _record_submission_events_with_retry(db, complaint)
        if not events_recorded:
            logger.error("BMC submission response retained complaint_id=%s", complaint.id)
        db.refresh(complaint)
        return complaint
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/complaints")
async def create(
    request: Request,
    _current_user: User = Depends(require_bmc_staff),
):
    payload = await _read_submission(request, Submission, MAX_SUBMISSION_REQUEST_BYTES)
    if not payload.language:
        try:
            payload.language = detect(payload.text)
        except LangDetectException:
            payload.language = "unknown"

    # Live submissions deliberately execute inline to meet the <2-second demo target.
    try:
        complaint = await run_in_threadpool(_process_bmc_submission, payload)
        return public(complaint)
    except VideoProcessingError as error:
        raise HTTPException(
            status_code=422,
            detail="Video could not be processed. Please choose a valid video file and try again.",
        ) from error


@app.get("/complaints")
def complaints(
    q: str | None = None,
    ward: str | None = None,
    category: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    sort: str = "newest",
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_bmc_staff),
):
    if sort not in {"newest", "oldest"} or limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(422, "Invalid pagination or sort value.")
    query = _complaint_query(db, q=q, ward=ward, category=category,
                             complaint_status=status, severity=severity, sort=sort)
    query = query.where(Complaint.review_state == "accepted", Complaint.status.in_(("open", "inspection_scheduled", "contractor_allotted", "in_progress", "resolved")))
    return _page_complaints(db, query, limit, offset, public)


@app.get("/bmc/complaints/{complaint_id}")
def get_bmc_complaint(complaint_id: str, db: Session = Depends(get_db),
                      _current_user: User = Depends(require_bmc_staff)):
    complaint = db.get(Complaint, complaint_id)
    if not complaint:
        raise HTTPException(404, "Complaint not found")
    return {**public(complaint), "source": complaint.source, "history": _history_for(db, complaint)}


@app.post("/bmc/complaints/{complaint_id}/resolve-location")
def retry_bmc_complaint_location(complaint_id: str, db: Session = Depends(get_db),
                                 _current_user: User = Depends(require_bmc_staff)):
    """Retry geocoding an unresolved report; only verified results can update or cluster it."""
    complaint = db.get(Complaint, complaint_id)
    if not complaint:
        raise HTTPException(404, "Complaint not found")
    # Retry only unresolved or approximate locations. Verified pins and geocoder
    # results remain final until a dedicated correction workflow exists.
    was_verified = services.has_verified_location(
        complaint.location_source, complaint.lat, complaint.lng
    )
    if was_verified:
        return {**public(complaint), "location_retry": "already_verified"}

    previous_cluster_id = complaint.cluster_id
    try:
        resolved = services.resolve_location(complaint.text)
    except Exception as error:
        logger.warning("LOCATION retry failed complaint_id=%s type=%s", complaint.id, type(error).__name__)
        resolved = None

    if not resolved or not services.has_verified_location(
        resolved.get("source"), resolved.get("lat"), resolved.get("lng")
    ):
        return {**public(complaint), "location_retry": "unresolved"}

    try:
        complaint.lat = float(resolved["lat"])
        complaint.lng = float(resolved["lng"])
        complaint.location_source = resolved["source"]
        complaint.location = str(resolved.get("display_name") or "Verified location")[:255]
        complaint.ward_code = resolved.get("ward_code") or services.infer_ward_from_text(complaint.text)
        complaint.cluster_id = -1
        complaint.rank_in_cluster = None
        db.flush()
        if complaint.review_state == "accepted" and complaint.status in (
            "open", "inspection_scheduled", "contractor_allotted", "in_progress",
        ):
            complaint.cluster_id = services.assign_cluster(
                db,
                complaint.embedding,
                complaint.lat,
                complaint.lng,
                complaint_text=services._cluster_place_text(complaint.text, complaint.location),
                location_label=complaint.location,
            )
        if previous_cluster_id != -1 and previous_cluster_id != complaint.cluster_id:
            services.rerank(db, previous_cluster_id)
        if complaint.cluster_id != -1:
            services.rerank(db, complaint.cluster_id)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(500, "The verified location could not be saved.") from error
    db.refresh(complaint)
    return {**public(complaint), "location_retry": "resolved"}


@app.get("/bmc/reviews")
def review_queue(limit: int = Query(default=25, ge=1, le=50), offset: int = Query(default=0, ge=0),
                 db: Session = Depends(get_db), _user: User = Depends(require_bmc_staff)):
    query = select(Complaint).where(Complaint.review_state == "pending").order_by(Complaint.created_at.desc(), Complaint.id.desc())
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.offset(offset).limit(limit)).all()
    return {"items": [{
        **public(row),
        "source": row.source,
        "source_post_id": row.source_post_id,
        "source_author": row.source_author,
        "source_url": row.source_url,
        "review_state": "pending",
    } for row in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/bmc/summary")
def bmc_summary(db: Session = Depends(get_db), _user: User = Depends(require_bmc_staff)):
    active = Complaint.review_state == "accepted"
    return {"active": db.scalar(select(func.count()).select_from(Complaint).where(active, Complaint.status.in_(("open", "inspection_scheduled", "contractor_allotted", "in_progress", "resolved")))) or 0,
        "pending_review": db.scalar(select(func.count()).select_from(Complaint).where(Complaint.review_state == "pending")) or 0,
        "rejected": db.scalar(select(func.count()).select_from(Complaint).where(Complaint.review_state == "rejected")) or 0,
        **{state: db.scalar(select(func.count()).select_from(Complaint).where(active, Complaint.status == state)) or 0 for state in ("open", "inspection_scheduled", "contractor_allotted", "in_progress", "resolved")}}


def _notify_transition(db, complaint, actor, old_status, new_status, event_key, reason=None):
    ids = set()
    if complaint.citizen_id:
        ids.add(complaint.citizen_id)
    ids.update(db.scalars(select(ComplaintSubscription.user_id).where(
        ComplaintSubscription.complaint_id == complaint.id, ComplaintSubscription.active.is_(True)
    )).all())
    for user_id in ids:
        if user_id == actor.id:
            continue
        recipient = db.get(User, user_id)
        if recipient and recipient.is_active:
            _add_notification(db, recipient=recipient, audience_role="citizen", complaint_id=complaint.id,
                event_key=f"{event_key}:user:{user_id}", title="Complaint status updated",
                summary=f"Complaint {complaint.id} changed from {old_status.replace('_', ' ')} to {new_status.replace('_', ' ')}." + (f" Reason: {reason}." if reason else "") + f" Track: /track?reference={complaint.id}")


@app.post("/bmc/reviews/{complaint_id}/accept")
def accept_review(complaint_id: str, db: Session = Depends(get_db), user: User = Depends(require_bmc_staff)):
    complaint = db.scalar(select(Complaint).where(Complaint.id == complaint_id).with_for_update())
    if not complaint:
        raise HTTPException(404, "Complaint not found")
    if complaint.review_state != "pending":
        raise HTTPException(409, "This complaint has already been reviewed.")
    previous = complaint.review_state
    try:
        complaint.review_state = "accepted"
        complaint.status = "open"
        if complaint.source == "citizen" and complaint.location_source == "citizen_pin":
            complaint.cluster_id = services.assign_citizen_pinned_cluster(
                db, complaint.embedding, complaint.lat, complaint.lng,
                complaint_text=complaint.text,
                location_label=complaint.location,
            )
            pinned_match = db.scalar(select(Complaint.id).where(
                Complaint.cluster_id == complaint.cluster_id,
                Complaint.id != complaint.id,
                Complaint.review_state == "accepted",
                Complaint.status.in_(("open", "inspection_scheduled", "contractor_allotted", "in_progress")),
            ))
            # A precise pin match is preferred. When no active cluster meets
            # the pin-specific 100m rule, use the same semantic/geographic
            # matching path as other submissions instead of forcing a new ID.
            if not pinned_match:
                complaint.cluster_id = -1
                complaint.cluster_id = services.assign_cluster(
                    db,
                    complaint.embedding,
                    complaint.lat,
                    complaint.lng,
                    complaint_text=services._cluster_place_text(complaint.text, complaint.location),
                    location_label=complaint.location,
                )
        elif services.has_verified_location(complaint.location_source, complaint.lat, complaint.lng):
            complaint.cluster_id = services.assign_cluster(
                db,
                complaint.embedding,
                complaint.lat,
                complaint.lng,
                complaint_text=services._cluster_place_text(complaint.text, complaint.location),
                location_label=complaint.location,
            )
        else:
            complaint.cluster_id = -1
        db.flush()
        services.rerank(db, complaint.cluster_id)
        key = f"review:{complaint.id}:accepted"
        _add_history(db, complaint, "review_accepted", from_status=previous, to_status="accepted", actor=user, event_key=key)
        _notify_transition(db, complaint, user, "pending_review", "open", key)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(500, "The review decision could not be saved.") from error
    db.refresh(complaint)
    return {**public(complaint), "review_state": complaint.review_state}


@app.post("/bmc/reviews/{complaint_id}/reject")
def reject_review(complaint_id: str, payload: RejectDecision, db: Session = Depends(get_db), user: User = Depends(require_bmc_staff)):
    complaint = db.scalar(select(Complaint).where(Complaint.id == complaint_id).with_for_update())
    if not complaint:
        raise HTTPException(404, "Complaint not found")
    if complaint.review_state != "pending":
        raise HTTPException(409, "This complaint has already been reviewed.")
    previous = complaint.review_state
    reason = payload.reason.strip() if payload.reason else None
    try:
        complaint.review_state = "rejected"
        complaint.rejection_reason = reason
        complaint.cluster_id = -1
        key = f"review:{complaint.id}:rejected"
        _add_history(db, complaint, "review_rejected", from_status=previous, to_status="rejected", actor=user, event_key=key)
        _notify_transition(db, complaint, user, "pending_review", "rejected", key, reason=reason)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(500, "The review decision could not be saved.") from error
    db.refresh(complaint)
    return {**_citizen_complaint_summary(complaint), "review_state": complaint.review_state}


@app.get("/citizen/track/{reference}")
def citizen_track(reference: str, db: Session = Depends(get_db), _user: User = Depends(require_citizen)):
    complaint = db.scalar(select(Complaint).where(Complaint.id == reference.upper()))
    if not complaint:
        raise HTTPException(404, "No complaint was found for that reference.")
    status_value = "rejected" if complaint.review_state == "rejected" else "pending_review" if complaint.review_state == "pending" else complaint.status
    events = _history_for(db, complaint)
    return {"reference": complaint.id, "text": complaint.text, "status": status_value, "created_at": _timestamp_iso(complaint.created_at),
        "category": complaint.category or services.infer_category(complaint.text), "ward": complaint.ward_code, "location": complaint.location,
        "timeline": [{"status": ("pending_review" if e["to_status"] == "pending" or e["event_type"] == "submitted" and complaint.review_state == "pending" else "rejected" if e["to_status"] == "rejected" else "open" if e["to_status"] == "accepted" else e["to_status"]), "event": e["event_type"], "created_at": e["created_at"]} for e in events]}


@app.put("/citizen/follows/{reference}")
def set_follow(reference: str, payload: dict, db: Session = Depends(get_db), user: User = Depends(require_citizen)):
    if not isinstance(payload, dict) or not isinstance(payload.get("active"), bool):
        raise HTTPException(422, "active must be a boolean")
    complaint = db.get(Complaint, reference)
    if not complaint:
        raise HTTPException(404, "Complaint not found")
    if complaint.citizen_id == user.id:
        raise HTTPException(409, "You already receive updates as the complaint owner.")
    sub = db.scalar(select(ComplaintSubscription).where(ComplaintSubscription.complaint_id == reference, ComplaintSubscription.user_id == user.id))
    if sub:
        sub.active = payload["active"]
    elif payload["active"]:
        db.add(ComplaintSubscription(complaint_id=reference, user_id=user.id, active=True))
    db.commit()
    return {"active": payload["active"]}


@app.get("/citizen/follows")
def list_follows(db: Session = Depends(get_db), user: User = Depends(require_citizen)):
    rows = db.scalars(select(Complaint).join(ComplaintSubscription, ComplaintSubscription.complaint_id == Complaint.id).where(ComplaintSubscription.user_id == user.id, ComplaintSubscription.active.is_(True)).order_by(ComplaintSubscription.created_at.desc())).all()
    return {"items": [_citizen_complaint_summary(row) for row in rows]}


@app.get("/citizen/follows/{reference}")
def get_follow(reference: str, db: Session = Depends(get_db), user: User = Depends(require_citizen)):
    complaint = db.get(Complaint, reference)
    if not complaint:
        raise HTTPException(404, "Complaint not found")
    sub = db.scalar(select(ComplaintSubscription).where(ComplaintSubscription.user_id == user.id, ComplaintSubscription.complaint_id == reference))
    return {"active": bool(sub and sub.active), "is_owner": complaint.citizen_id == user.id}


def _list_notifications(db: Session, user: User, role: str):
    rows = db.scalars(select(Notification).where(
        Notification.recipient_user_id == user.id,
        Notification.audience_role == role,
    ).order_by(Notification.created_at.desc())).all()
    unread = db.scalar(select(func.count()).where(
        Notification.recipient_user_id == user.id,
        Notification.audience_role == role,
        Notification.read_at.is_(None),
    )) or 0
    complaint_ids = {item.complaint_id for item in rows if item.complaint_id}
    complaints_by_id = {row.id: row for row in db.scalars(select(Complaint).where(Complaint.id.in_(complaint_ids))).all()} if complaint_ids else {}
    return {"items": [_notification_json(item, complaints_by_id.get(item.complaint_id)) for item in rows], "unread_count": unread}


def _mark_notification(db: Session, notification_id: int | None, user: User, role: str, all_read=False):
    query = update(Notification).where(
        Notification.recipient_user_id == user.id,
        Notification.audience_role == role,
        Notification.read_at.is_(None),
    ).values(read_at=utc_now())
    if notification_id is not None:
        query = query.where(Notification.id == notification_id)
    result = db.execute(query)
    if notification_id is not None and result.rowcount == 0:
        # Idempotently accept an already-read notification owned by this user.
        exists = db.scalar(select(Notification.id).where(
            Notification.id == notification_id,
            Notification.recipient_user_id == user.id,
            Notification.audience_role == role,
        ))
        if not exists:
            raise HTTPException(404, "Notification not found")
    db.commit()
    return _list_notifications(db, user, role)


@app.get("/citizen/notifications")
def citizen_notifications(db: Session = Depends(get_db), user: User = Depends(require_citizen)):
    return _list_notifications(db, user, "citizen")


@app.patch("/citizen/notifications/{notification_id}/read")
def citizen_notification_read(notification_id: int, db: Session = Depends(get_db), user: User = Depends(require_citizen)):
    return _mark_notification(db, notification_id, user, "citizen")


@app.post("/citizen/notifications/read-all")
def citizen_notifications_read_all(db: Session = Depends(get_db), user: User = Depends(require_citizen)):
    return _mark_notification(db, None, user, "citizen", all_read=True)


@app.get("/bmc/notifications")
def bmc_notifications(db: Session = Depends(get_db), user: User = Depends(require_bmc_staff)):
    return _list_notifications(db, user, "admin")


@app.patch("/bmc/notifications/{notification_id}/read")
def bmc_notification_read(notification_id: int, db: Session = Depends(get_db), user: User = Depends(require_bmc_staff)):
    return _mark_notification(db, notification_id, user, "admin")


@app.post("/bmc/notifications/read-all")
def bmc_notifications_read_all(db: Session = Depends(get_db), user: User = Depends(require_bmc_staff)):
    return _mark_notification(db, None, user, "admin", all_read=True)


@app.patch("/complaints/{complaint_id}/status")
def update_complaint_status(complaint_id: str, payload: StatusUpdate,
                            db: Session = Depends(get_db), user: User = Depends(require_bmc_staff)):
    complaint = db.scalar(select(Complaint).where(Complaint.id == complaint_id).with_for_update())
    if not complaint:
        raise HTTPException(404, "Complaint not found")
    old_status = complaint.status
    if complaint.review_state != "accepted":
        raise HTTPException(409, "Only accepted complaints can receive work status updates.")
    if payload.status not in STAFF_TRANSITIONS.get(old_status, set()):
        raise HTTPException(409, "This complaint status transition is not allowed.")
    transition_at = utc_now()
    try:
        complaint.status = payload.status
        db.flush()
        services.rerank(db, complaint.cluster_id)
        event_suffix = transition_at.isoformat()
        _add_history(
            db, complaint, "status_changed", from_status=old_status, to_status=payload.status,
            actor=user, event_key=f"status:{complaint.id}:{old_status}:{payload.status}:{event_suffix}",
        )
        _notify_transition(db, complaint, user, old_status, payload.status, f"status-notice:{complaint.id}:{old_status}:{payload.status}:{event_suffix}")
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.error(
            "COMPLAINT status transition failed complaint_id=%s from=%s to=%s type=%s",
            complaint.id, old_status, payload.status, type(error).__name__,
        )
        raise HTTPException(500, "The complaint status could not be saved. Please retry.") from error
    db.refresh(complaint)
    return {**public(complaint), "history": _history_for(db, complaint)}


@app.get("/clusters")
def clusters(
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_bmc_staff),
):
    results = []
    active_statuses = ("open", "inspection_scheduled", "contractor_allotted", "in_progress")
    cluster_ids = db.execute(select(Complaint.cluster_id).where(Complaint.review_state == "accepted", Complaint.status.in_(active_statuses), Complaint.cluster_id != -1, Complaint.location_source.in_(services.VERIFIED_LOCATION_SOURCES), Complaint.lat.is_not(None), Complaint.lng.is_not(None), Complaint.embedding.is_not(None)).distinct()).all()

    for (cluster_id,) in cluster_ids:
        query = (
            select(Complaint)
            .where(Complaint.cluster_id == cluster_id, Complaint.review_state == "accepted", Complaint.status.in_(active_statuses), Complaint.location_source.in_(services.VERIFIED_LOCATION_SOURCES), Complaint.lat.is_not(None), Complaint.lng.is_not(None), Complaint.embedding.is_not(None))
            .order_by(Complaint.rank_in_cluster)
        )
        items = [
            item for item in db.scalars(query)
            if services.valid_location_coordinates(item.lat, item.lng)
            and services.valid_embedding(item.embedding)
        ]
        if not items:
            continue

        # A citizen-selected map point is the most precise location evidence
        # for a cluster. Do not let less precise reverse-geocoder POI results
        # pull the visible marker away from that pin.
        pinned_items = [item for item in items if item.location_source == "citizen_pin"]
        centroid_items = pinned_items or items

        categories = [item.category or services.infer_category(item.text) or "Uncategorised" for item in items]
        dominant = max(set(categories), key=categories.count)
        results.append(
            {
                "cluster_id": cluster_id,
                "size": len(items),
                "dominant_category": dominant,
                "top_ranked_complaint": public(items[0]),
                "centroid": {
                    "lat": sum(item.lat for item in centroid_items) / len(centroid_items),
                    "lng": sum(item.lng for item in centroid_items) / len(centroid_items),
                },
            }
        )
    return results



@app.get("/clusters/{cluster_id}")
def cluster(
    cluster_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_bmc_staff),
):
    active_statuses = ("open", "inspection_scheduled", "contractor_allotted", "in_progress")
    query = (
        select(Complaint)
        .where(Complaint.cluster_id == cluster_id, Complaint.review_state == "accepted", Complaint.status.in_(active_statuses), Complaint.location_source.in_(services.VERIFIED_LOCATION_SOURCES), Complaint.lat.is_not(None), Complaint.lng.is_not(None), Complaint.embedding.is_not(None))
        .order_by(Complaint.final_score.desc())
    )
    items = [
        item for item in db.scalars(query)
        if services.valid_location_coordinates(item.lat, item.lng)
        and services.valid_embedding(item.embedding)
    ]
    if not items:
        raise HTTPException(404, "Cluster not found")

    return [
        {
            **public(item),
            "corroboration_count": len(items),
            "explanation": (
                f"Severity {item.final_severity}; corroborated by {len(items)} reports"
                + ("; photo evidence included." if item.has_media else ".")
            ),
        }
        for item in items
    ]


@app.get("/map-approximations")
def map_approximations(
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_bmc_staff),
):
    """Return explicitly approximate locations for a separate staff map layer."""
    active_statuses = ("open", "inspection_scheduled", "contractor_allotted", "in_progress")
    rows = db.scalars(select(Complaint).where(
        Complaint.review_state == "accepted",
        Complaint.status.in_(active_statuses),
        Complaint.location_source.in_(services.APPROXIMATE_LOCATION_SOURCES),
        Complaint.lat.is_not(None),
        Complaint.lng.is_not(None),
    ))
    markers = []
    labels = {
        "ward_approximate": "Approximate ward-centre location",
        "geoapify_midpoint": "Approximate midpoint between reported places",
        "nominatim_midpoint": "Approximate midpoint between reported places",
        "photon_midpoint": "Approximate midpoint between reported places",
    }
    for item in rows:
        if not services.valid_location_coordinates(item.lat, item.lng):
            continue
        markers.append({
            "id": item.id,
            "text": item.text,
            "location": item.location,
            "category": item.category or services.infer_category(item.text) or "Uncategorised",
            "severity": item.final_severity,
            "lat": item.lat,
            "lng": item.lng,
            "location_source": item.location_source,
            "location_confidence": "approximate",
            "marker_label": labels[item.location_source],
        })
    return markers


@app.get("/health")
def health():
    return {"ok": True}
