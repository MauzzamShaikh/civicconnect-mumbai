import base64
import binascii
import io
import logging
import math
import os
import tempfile
import threading
import unicodedata
import uuid

import cv2
import joblib
import numpy as np
import torch
from faster_whisper import WhisperModel
from PIL import Image
from sentence_transformers import SentenceTransformer
from sqlalchemy import func, select
from torchvision import models, transforms

from .config import (
    ARTIFACTS_DIR,
    EMBEDDING_MODEL,
    MAX_CLUSTER_RADIUS_KM,
    SAME_LOCATION_RADIUS_KM,
    SAME_LOCATION_SEMANTIC_THRESHOLD,
    THRESHOLD,
)
from .geo import MUMBAI_LAT_RANGE, MUMBAI_LNG_RANGE, _extract_location_candidates, infer_ward_from_text, normalize_cluster_text, resolve_location, ward_coord
from .models import Complaint

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
LABELS = ["Low", "Medium", "High"]
SEVERITY_VALUES = {"Low": 1, "Medium": 2, "High": 3}
VIDEO_BLUR_THRESHOLD = float(os.getenv("VIDEO_BLUR_THRESHOLD", "100"))
WHISPER_TIMEOUT_SECONDS = 45
CITIZEN_PIN_CLUSTER_RADIUS_KM = 0.1


class VideoProcessingError(ValueError):
    """A user-safe failure while decoding or analysing an uploaded video."""


def haversine_distance(lat1, lng1, lat2, lng2):
    radius_km = 6371.0
    lat_delta = math.radians(lat2 - lat1)
    lng_delta = math.radians(lng2 - lng1)
    value = math.sin(lat_delta / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(lng_delta / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(value))


def non_latin_script_families(text):
    prefixes = {"DEVANAGARI": "Devanagari", "CYRILLIC": "Cyrillic", "KANNADA": "Kannada", "ARABIC": "Arabic", "BENGALI": "Bengali", "GREEK": "Greek", "GUJARATI": "Gujarati", "GURMUKHI": "Gurmukhi", "TAMIL": "Tamil", "TELUGU": "Telugu", "MALAYALAM": "Malayalam"}
    families = set()
    for character in text:
        if character.isalpha():
            name = unicodedata.name(character, "")
            for prefix, family in prefixes.items():
                if name.startswith(prefix):
                    families.add(family)
                    break
    return families


class Inference:
    def __init__(self):
        self.embedder = SentenceTransformer(EMBEDDING_MODEL)
        bundle = joblib.load(next(iter(ARTIFACTS_DIR.glob("model1*.joblib"))))
        self.text_model = bundle["model"]
        self.scaler = bundle["scaler"]
        self.photo_model = models.mobilenet_v2()
        self.photo_model.classifier[1] = torch.nn.Linear(1280, 3)
        self.photo_model.load_state_dict(torch.load(next(iter(ARTIFACTS_DIR.glob("model2*.pth"))), map_location="cpu"))
        self.photo_model.eval()
        self.photo_transform = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(), transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
        self.whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        self._whisper_lock = threading.Lock()

    def embedding(self, text):
        return self.embedder.encode([text], normalize_embeddings=True)[0]

    def text_severity(self, embedding):
        predicted = int(self.text_model.predict(self.scaler.transform([embedding]))[0])
        if predicted in (1, 2): return "Low"
        if predicted == 3: return "Medium"
        if predicted in (4, 5): return "High"
        raise ValueError(f"Unexpected text severity prediction: {predicted}")

    def _photo_severity_from_image(self, image):
        with torch.no_grad():
            predicted = self.photo_model(self.photo_transform(image).unsqueeze(0)).argmax(1).item()
        return LABELS[int(predicted)]

    def photo_severity(self, data):
        return self._photo_severity_from_image(Image.open(io.BytesIO(base64.b64decode(data.split(",")[-1]))).convert("RGB"))

    def process_video(self, video_base64):
        logger.info("VIDEO: received")
        video_path = None
        capture = None
        thread = None
        timed_out = False
        try:
            if not isinstance(video_base64, str) or "," not in video_base64:
                raise VideoProcessingError("Video data is malformed")
            try:
                raw = base64.b64decode(video_base64.split(",", 1)[1], validate=True)
            except (ValueError, binascii.Error) as error:
                raise VideoProcessingError("Video data is malformed") from error
            if not raw:
                raise VideoProcessingError("Video data is empty")
            logger.info("VIDEO: decoded")
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as file:
                file.write(raw); video_path = file.name
            capture = cv2.VideoCapture(video_path)
            if not capture.isOpened():
                raise VideoProcessingError("Video could not be opened")
            total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            logger.info("VIDEO: frame_count=%s", total)
            if total <= 0:
                raise VideoProcessingError("Could not read video frames")
            candidates = []
            for position in [int(total * index / 9) for index in range(1, 9)]:
                capture.set(cv2.CAP_PROP_POS_FRAMES, position)
                ok, frame = capture.read()
                if not ok or frame is None:
                    continue
                blur = float(cv2.Laplacian(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
                candidates.append((blur, frame))
            logger.info("VIDEO: usable_frames=%s", len(candidates))
            if not candidates:
                raise VideoProcessingError("Could not extract usable video frames")
            sharp = [(blur, frame) for blur, frame in candidates if blur >= VIDEO_BLUR_THRESHOLD] or [max(candidates, key=lambda item: item[0])]
            scored = [(SEVERITY_VALUES[self._photo_severity_from_image(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))], blur, frame) for blur, frame in sharp]
            _, _, frame = max(scored, key=lambda item: (item[0], item[1]))
            frame_severity = LABELS[max(scored, key=lambda item: (item[0], item[1]))[0] - 1]
            logger.info("VIDEO: frame_scoring_complete severity=%s", frame_severity)
            ok, encoded = cv2.imencode(".jpg", frame)
            if not ok:
                raise VideoProcessingError("Could not encode selected video frame")

            outcome = {}
            def transcribe():
                if not self._whisper_lock.acquire(blocking=False):
                    outcome["failed"] = True
                    return
                try:
                    segments, info = self.whisper_model.transcribe(video_path, language=None, vad_filter=True)
                    outcome["text"] = " ".join(segment.text.strip() for segment in segments).strip()
                    outcome["probability"] = getattr(info, "language_probability", 0.0) or 0.0
                except Exception:
                    outcome["failed"] = True
                finally:
                    self._whisper_lock.release()
            thread = threading.Thread(target=transcribe, daemon=True)
            logger.info("VIDEO: whisper_started")
            thread.start()
            thread.join(WHISPER_TIMEOUT_SECONDS)
            timed_out = thread.is_alive()
            if timed_out:
                logger.warning("VIDEO: whisper_timeout")
            elif outcome.get("failed"):
                logger.warning("VIDEO: whisper_failed")
            else:
                logger.info("VIDEO: whisper_success")
            transcript = "" if timed_out else outcome.get("text", "")
            reliable = bool(transcript) and not timed_out and outcome.get("probability", 0) >= 0.5 and len(non_latin_script_families(transcript)) <= 1
            logger.info("VIDEO: processing_complete")
            return base64.b64encode(encoded.tobytes()).decode("ascii"), frame_severity, transcript, reliable
        except VideoProcessingError:
            logger.warning("VIDEO: processing_failed type=invalid_video")
            raise
        except (cv2.error, OSError, ValueError, TypeError) as error:
            logger.warning("VIDEO: processing_failed type=%s", type(error).__name__)
            raise VideoProcessingError("Video could not be processed") from error
        except Exception as error:
            logger.warning("VIDEO: processing_failed type=%s", type(error).__name__)
            raise VideoProcessingError("Video could not be processed") from error
        finally:
            if capture is not None:
                capture.release()
            if video_path:
                if timed_out and thread is not None and thread.is_alive():
                    def _cleanup(t=thread, path=video_path):
                        t.join()
                        try:
                            if os.path.exists(path):
                                os.unlink(path)
                        except (FileNotFoundError, OSError):
                            pass
                    threading.Thread(target=_cleanup, daemon=True).start()
                else:
                    try:
                        if os.path.exists(video_path):
                            os.unlink(video_path)
                    except (FileNotFoundError, OSError):
                        pass


infer = None
def get_infer():
    global infer
    if infer is None: infer = Inference()
    return infer


def assign_cluster(session, embedding, lat=None, lng=None):
    active_cluster_statuses = ("open", "inspection_scheduled", "contractor_allotted", "in_progress")
    next_cluster_id = lambda: int(session.scalar(select(func.max(Complaint.cluster_id)).where(
        Complaint.cluster_id != -1, Complaint.review_state == "accepted",
        Complaint.status.in_(active_cluster_statuses),
    )) or 0) + 1
    try:
        embedding = np.asarray(embedding, dtype=float)
        embedding_norm = float(np.linalg.norm(embedding))
    except (TypeError, ValueError):
        logger.debug("CLUSTER: malformed submission embedding; assigning a new cluster")
        return next_cluster_id()

    if not np.isfinite(embedding_norm) or embedding_norm == 0:
        logger.debug("CLUSTER: invalid submission embedding; assigning a new cluster")
        return next_cluster_id()

    # A report without a resolved/pinned location must not receive a map pin
    # or join a semantic cluster. -1 is the existing unmapped sentinel.
    if lat is None or lng is None:
        logger.info("CLUSTER: report has no coordinates; retaining unmapped state")
        return -1

    rows = session.execute(
        select(
            Complaint.cluster_id,
            Complaint.embedding,
            Complaint.lat,
            Complaint.lng,
        ).where(
            Complaint.cluster_id != -1,
            Complaint.embedding.is_not(None),
            Complaint.review_state == "accepted",
            Complaint.status.in_(active_cluster_statuses),
        )
    ).all()
    groups = {}
    for cluster_id, vector, member_lat, member_lng in rows:
        try:
            member_vector = np.asarray(vector, dtype=float)
        except (TypeError, ValueError):
            logger.debug(
                "CLUSTER: cluster=%s skipped malformed member embedding",
                cluster_id,
            )
            continue
        groups.setdefault(cluster_id, []).append((member_vector, member_lat, member_lng))

    valid = []
    for cluster_id, members in groups.items():
        similarities = []
        for vector, _, _ in members:
            vector_norm = float(np.linalg.norm(vector))
            if (
                vector.shape != embedding.shape
                or not np.isfinite(vector_norm)
                or vector_norm == 0
            ):
                logger.debug(
                    "CLUSTER: cluster=%s skipped invalid member embedding",
                    cluster_id,
                )
                continue
            similarity = float(np.dot(embedding, vector) / (embedding_norm * vector_norm))
            if np.isfinite(similarity):
                similarities.append(similarity)

        if not similarities:
            logger.debug("CLUSTER: cluster=%s rejected reason=no_valid_member_embedding", cluster_id)
            continue

        best_similarity = max(similarities)
        coordinates = [
            (member_lat, member_lng)
            for _, member_lat, member_lng in members
            if member_lat is not None and member_lng is not None
        ]
        distance = None
        if coordinates:
            centroid_lat = float(np.mean([coordinate[0] for coordinate in coordinates]))
            centroid_lng = float(np.mean([coordinate[1] for coordinate in coordinates]))
            distance = haversine_distance(lat, lng, centroid_lat, centroid_lng)

        geo_ok = distance is not None and distance <= MAX_CLUSTER_RADIUS_KM
        relaxed_same_location = (
            distance is not None and distance <= SAME_LOCATION_RADIUS_KM
        )
        threshold_used = (
            SAME_LOCATION_SEMANTIC_THRESHOLD
            if relaxed_same_location
            else THRESHOLD
        )
        semantic_ok = best_similarity >= threshold_used
        accepted = semantic_ok and geo_ok
        if distance is None:
            logger.debug("CLUSTER: cluster=%s rejected reason=no_member_coordinates", cluster_id)
        logger.debug(
            "CLUSTER: cluster=%s similarity=%.4f distance=%s km "
            "threshold_used=%.2f relaxed_same_location=%s accepted=%s "
            "max_radius_km=%.2f",
            cluster_id,
            best_similarity,
            "n/a" if distance is None else f"{distance:.2f}",
            threshold_used,
            relaxed_same_location,
            accepted,
            MAX_CLUSTER_RADIUS_KM,
        )
        if accepted:
            valid.append((best_similarity, cluster_id))

    if valid:
        return max(valid)[1]
    return next_cluster_id()


def assign_citizen_pinned_cluster(session, embedding, lat, lng):
    active_cluster_statuses = ("open", "inspection_scheduled", "contractor_allotted", "in_progress")
    next_cluster_id = lambda: int(session.scalar(select(func.max(Complaint.cluster_id)).where(
        Complaint.cluster_id != -1, Complaint.review_state == "accepted",
        Complaint.status.in_(active_cluster_statuses),
    )) or 0) + 1
    """Citizen pin matching: semantic threshold AND a usable cluster location within 100m."""
    try:
        candidate = np.asarray(embedding, dtype=float)
        candidate_norm = float(np.linalg.norm(candidate))
    except (TypeError, ValueError):
        candidate_norm = 0.0
        candidate = np.asarray([], dtype=float)
    if not np.isfinite(candidate_norm) or candidate_norm == 0:
        return next_cluster_id()

    rows = session.execute(
        select(Complaint.cluster_id, Complaint.embedding, Complaint.lat, Complaint.lng).where(
            Complaint.cluster_id != -1,
            Complaint.embedding.is_not(None),
            Complaint.review_state == "accepted",
            Complaint.status.in_(active_cluster_statuses),
        )
    ).all()
    clusters = {}
    for cluster_id, vector, member_lat, member_lng in rows:
        clusters.setdefault(cluster_id, []).append((vector, member_lat, member_lng))

    matches = []
    for cluster_id, members in clusters.items():
        usable_coordinates = []
        similarities = []
        for vector, member_lat, member_lng in members:
            if member_lat is not None and member_lng is not None:
                try:
                    coordinate = (float(member_lat), float(member_lng))
                    if all(math.isfinite(value) for value in coordinate):
                        usable_coordinates.append(coordinate)
                except (TypeError, ValueError, OverflowError):
                    pass
            try:
                member = np.asarray(vector, dtype=float)
                member_norm = float(np.linalg.norm(member))
            except (TypeError, ValueError):
                continue
            if member.shape != candidate.shape or not np.isfinite(member_norm) or member_norm == 0:
                continue
            similarity = float(np.dot(candidate, member) / (candidate_norm * member_norm))
            if np.isfinite(similarity):
                similarities.append(similarity)

        # Missing/invalid coordinates cannot be treated as a geographic match.
        if not usable_coordinates or not similarities:
            continue
        center_lat = float(np.mean([point[0] for point in usable_coordinates]))
        center_lng = float(np.mean([point[1] for point in usable_coordinates]))
        distance_km = haversine_distance(lat, lng, center_lat, center_lng)
        best_similarity = max(similarities)
        location_ok = distance_km <= CITIZEN_PIN_CLUSTER_RADIUS_KM
        semantic_ok = best_similarity >= THRESHOLD
        logger.debug(
            "CITIZEN PIN CLUSTER cluster=%s distance_m=%.1f location_ok=%s similarity=%.4f "
            "semantic_threshold=%.2f semantic_ok=%s",
            cluster_id,
            distance_km * 1000,
            location_ok,
            best_similarity,
            THRESHOLD,
            semantic_ok,
        )
        if location_ok and semantic_ok:
            matches.append((best_similarity, -distance_km, cluster_id))

    if matches:
        return max(matches)[2]
    return next_cluster_id()


def rerank(session, cluster_id):
    rows = list(session.scalars(select(Complaint).where(
        Complaint.cluster_id == cluster_id,
        Complaint.review_state == "accepted",
        Complaint.status.in_(("open", "inspection_scheduled", "contractor_allotted", "in_progress")),
    ).order_by(Complaint.final_score.desc(), Complaint.has_media.desc())))
    for rank, complaint in enumerate(rows, 1): complaint.rank_in_cluster = rank


def process_submission(session, payload, *, citizen_id=None, pinned_location=None, source=None, pending_review=False,
                       source_post_id=None, source_author=None, source_url=None):
    engine = get_infer()
    display_text = payload.text.strip()
    severity_analysis_text = display_text
    original_photo_data = payload.image_base64
    image_data, video_data, transcript = original_photo_data, None, None
    photo_severity = None
    uploaded_photo_severity = None
    video_severity = None
    has_video = bool(payload.video_base64)
    has_reliable_speech = False
    if has_video:
        frame, video_severity, raw_transcript, has_reliable_speech = engine.process_video(payload.video_base64)
        image_data, video_data = f"data:image/jpeg;base64,{frame}", payload.video_base64
        if has_reliable_speech:
            transcript = raw_transcript
            severity_analysis_text = " ".join(part for part in (severity_analysis_text, transcript) if part)
    severity_embedding = engine.embedding(severity_analysis_text or "(no caption or speech detected)")
    text_severity = engine.text_severity(severity_embedding) if severity_analysis_text else None
    if original_photo_data:
        uploaded_photo_severity = engine.photo_severity(original_photo_data)
    if uploaded_photo_severity and video_severity:
        photo_severity = max(
            (uploaded_photo_severity, video_severity),
            key=lambda severity: SEVERITY_VALUES[severity],
        )
    else:
        photo_severity = uploaded_photo_severity or video_severity
    uploaded_photo_data = original_photo_data if has_video and original_photo_data else None

    if pinned_location is not None:
        complaint_lat, complaint_lng = pinned_location
        if not (
            math.isfinite(complaint_lat)
            and math.isfinite(complaint_lng)
            and MUMBAI_LAT_RANGE[0] <= complaint_lat <= MUMBAI_LAT_RANGE[1]
            and MUMBAI_LNG_RANGE[0] <= complaint_lng <= MUMBAI_LNG_RANGE[1]
        ):
            raise ValueError("Pinned location is outside the supported Mumbai area")
        resolved_location = f"Selected map point ({complaint_lat:.5f}, {complaint_lng:.5f})"
        location_source = "citizen_pin"
        cluster_location = resolved_location
        # There is no coordinate-to-ward lookup in this project; use only an
        # explicit ward/locality found in the complaint text, otherwise leave it unset.
        inferred_ward = infer_ward_from_text(severity_analysis_text)
    else:
        resolved = resolve_location(severity_analysis_text)
        complaint_lat = resolved.get("lat")
        complaint_lng = resolved.get("lng")
        resolved_location = resolved.get("display_name")
        location_source = resolved.get("source", "none")
        cluster_location = resolved.get("canonical_name", resolved_location)
        inferred_ward = resolved.get("ward_code")
        if not inferred_ward and location_source != "none":
            inferred_ward = infer_ward_from_text(severity_analysis_text)
    logger.debug("LOCATION source=%s coordinates_available=%s", location_source, complaint_lat is not None and complaint_lng is not None)

    # Known localized place names are normalized for clustering only. Add
    # conservative multilingual issue tags only when the report has coordinates,
    # so reports from unrelated places cannot be pulled together by keywords.
    cluster_description = normalize_cluster_text(
        severity_analysis_text,
        include_issue_tags=complaint_lat is not None and complaint_lng is not None,
    )
    if pinned_location is not None:
        # Keep raw pin coordinates out of the embedding; geographic matching
        # uses the supplied coordinates directly.
        cluster_text = cluster_description
    elif cluster_location:
        cluster_text = f"{cluster_description}\nLocation: {cluster_location}"
    else:
        cluster_text = cluster_description
    cluster_embedding = engine.embedding(cluster_text or "(no caption or speech detected)")
    if pending_review:
        cluster_id = -1
    elif severity_analysis_text:
        if citizen_id is not None and pinned_location is not None:
            cluster_id = assign_citizen_pinned_cluster(session, cluster_embedding, complaint_lat, complaint_lng)
        else:
            cluster_id = assign_cluster(session, cluster_embedding, complaint_lat, complaint_lng)
    else:
        # A report without text or a resolvable map point is not a cluster pin.
        cluster_id = -1
    logger.debug("Cluster assignment complete: %s", cluster_id)

    if text_severity and photo_severity: score = 0.45 * SEVERITY_VALUES[text_severity] + 0.55 * SEVERITY_VALUES[photo_severity]
    elif photo_severity: score = SEVERITY_VALUES[photo_severity]
    elif text_severity: score = SEVERITY_VALUES[text_severity]
    else: score = 2
    complaint = Complaint(id="CMP-" + uuid.uuid4().hex[:10].upper(), citizen_id=citizen_id, source=source or ("citizen" if citizen_id is not None else "bmc_staff"), source_post_id=source_post_id, source_author=source_author, source_url=source_url, review_state="pending" if pending_review else "accepted", status="open", text=display_text, language=payload.language, ward_code=inferred_ward, location=resolved_location, location_source=location_source, cluster_id=cluster_id, embedding=cluster_embedding.tolist(), text_severity=text_severity, photo_severity=photo_severity, final_severity=LABELS[round(score) - 1], final_score=score, has_media=bool(photo_severity), image_data=image_data, uploaded_photo_data=uploaded_photo_data, has_video=has_video, video_data=video_data, transcript=transcript, is_real=True, lat=complaint_lat, lng=complaint_lng)
    session.add(complaint); session.flush();
    if not pending_review: rerank(session, cluster_id)
    session.commit(); session.refresh(complaint)
    return complaint
