import base64
import binascii
import io
import logging
import math
import os
import re
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
from sqlalchemy import func, select, update
from torchvision import models, transforms

from .config import (
    ARTIFACTS_DIR,
    EMBEDDING_MODEL,
    MAX_CLUSTER_RADIUS_KM,
    SAME_LOCATION_RADIUS_KM,
    SAME_LOCATION_SEMANTIC_THRESHOLD,
    THRESHOLD,
)
from .geo import MUMBAI_LAT_RANGE, MUMBAI_LNG_RANGE, _extract_location_candidates, cluster_issue_concepts, cluster_place_identity, infer_ward_from_text, normalize_cluster_text, resolve_location, ward_coord
from .models import Complaint

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
LABELS = ["Low", "Medium", "High"]
SEVERITY_VALUES = {"Low": 1, "Medium": 2, "High": 3}
VIDEO_BLUR_THRESHOLD = float(os.getenv("VIDEO_BLUR_THRESHOLD", "100"))
WHISPER_TIMEOUT_SECONDS = 45
CITIZEN_PIN_CLUSTER_RADIUS_KM = 0.1
MAX_SAFE_CLUSTER_RADIUS_KM = 1.5
DISTINCT_CLUSTER_ISSUES = {
    "pothole", "road damage", "muddy road", "garbage", "water leakage",
    "pipe burst", "footpath damage", "footpath encroachment", "encroachment",
    "illegal transport stand",
}

CATEGORY_PATTERNS = (
    ("Public health", (r"food poisoning|foodborne illness|contaminated food|unsafe food|food safety|adulterated food|विषबाधा|दूषित अन्न|दूषित भोजन|खाद्य विषाक्तता|असुरक्षित अन्न|भेसळयुक्त अन्न|मिलावटी खाना|खाद्य सुरक्षा",)),
    ("Solid Waste Management (SWM)", (r"garbage|rubbish|trash|waste collection|कचरा|कूड़ा|कूड़े|घनकचरा|kachra|kachrya",)),
    ("Water Supply", (r"water supply|water leak(?:age)?|water pipeline leak|pipeline (?:is )?leak(?:ing)?|pipe burst|pipeline burst|burst pipe|burst pipeline|pipeline (?:has )?broke|broken pipeline|पाण्य.{0,12}गळती|पाणी गळत|पाइपलाइन.{0,12}(?:लीक|फट|फुट|टूट)|पाईपलाईन.{0,12}(?:फुट|फाट)|पानी की लीकेज|pani leak|paani leak",)),
    ("Storm Water Drainage", (r"waterlog(?:ging)?|storm water|rain ?water drain|जलभराव|पाणी साच|पावसाचे पाणी|पावसाचं पाणी",)),
    ("Drainage", (r"drain(?:age)?|sewer|sewage|गटार|नाली|नाला|जलनिकासी|ड्रेनेज",)),
    ("Encroachment", (r"encroach(?:ment|ed|ing)?|occupied footpaths?|footpaths?.{0,20}(?:occupied|blocked|vendors?|stalls?)|illegal (?:food )?stalls?|unauthori[sz]ed (?:food )?stalls?|(?:illegal|unauthori[sz]ed|unlicensed) hawkers?|hawkers?.{0,28}(?:occup(?:y|ying)|block(?:ing)?|encroach|footpath|pavement)|street vendors?|(?:illegal|unauthori[sz]ed|unlawful) parking.{0,80}(?:block|obstruct|footpath|sidewalk|entrance)|(?:block|obstruct).{0,80}(?:illegal|unauthori[sz]ed|unlawful) parking|(?:बेकायदेशीर|अनधिकृत|अवैध).{0,18}पार्किंग.{0,80}(?:पदपथ|फुटपाथ|प्रवेशद्वार|अडव|अडथळा)|(?:अडव|अडथळा).{0,80}(?:बेकायदेशीर|अनधिकृत|अवैध).{0,18}पार्किंग|(?:अवैध|अनधिकृत).{0,18}पार्किंग.{0,80}(?:फुटपाथ|प्रवेश द्वार|रास्ता|अवरोध)|अतिक्रमण|अनधिकृत फेरीवाले|अवैध ठेले|बेकायदेशीर स्टॉल|अनधिकृत स्टॉल|पदपथावर दुकाने|फुटपाथ.{0,16}(?:कब्जा|ठेले|स्टॉल|दुकान|अतिक्रमण)",)),
    ("Roads", (r"potholes?|damaged road|broken road|road damage|road surface|\b(?:poor|bad|terrible|damaged|broken)\s+(?:road|roads)\s+condition\b|\b(?:poor|bad|terrible)\s+condition\s+of\s+(?:the\s+)?roads?\b|\broads?\s+(?:are\s+)?(?:in\s+)?(?:a\s+)?(?:poor|bad|terrible|damaged|broken)\s+condition\b|\broads?\s+condition\s+(?:is|are)\s+(?:poor|bad|terrible)\b|खड्ड(?:े|ा|े\b)|गड्ढे?|सड़क (?:टूटी|खराब)|सड़क(?:ों)? की (?:हालत|स्थिति) (?:खराब|बुरी)|रस्ता (?:खराब|तुटलेला)|रस्त(?:ा|्याची|्‍याची)(?:ची)? (?:स्थिती|अवस्था) (?:खराब|वाईट)|रस्त्यावर खड्डे|\b(?:bad|poor) roads?\b|\b(?:rasta|rastyachi|sadak) (?:kharaab|kharab|ki halat kharab|durdasha|duravastha)\b",)),
    ("Transport and mobility", (r"traffic jam|traffic congestion|blocked traffic|public transport|\b(?:traffic )?(?:lights?|signals?)\b.{0,32}\b(?:not working|malfunctioning|broken|damaged|out of order)\b|\b(?:not working|malfunctioning|broken|damaged|out of order)\b.{0,32}\b(?:traffic )?(?:lights?|signals?)\b|ट्रॅफिक.{0,20}सिग्नल.{0,20}(?:बंद|खराब|काम करत नाही|नादुरुस्त)|सिग्नल.{0,20}(?:काम करत नाही|बंद|खराब)|बस सेवा|वाहतूक कोंडी|ट्रैफिक जाम|यातायात",)),
    ("Public safety", (r"street ?lights? (?:are )?(?:off|broken|not working)|not working street ?lights?|पथदिवे? (?:बंद|नादुरुस्त)|स्ट्रीट ?लाइट (?:बंद|खराब)",)),
    ("Environment", (r"air pollution|noise pollution|pollution|प्रदूषण|ध्वनी प्रदूषण",)),
    ("Housing and building", (r"illegal construction|unsafe building|building collapse|अनधिकृत बांधकाम|अवैध निर्माण|इमारत (?:धोकादायक|जर्जर)",)),
    ("Parks and open spaces", (r"park maintenance|public garden|damaged park|उद्यान|बागेची देखभाल",)),
)


def infer_category(text):
    """Return an existing BMC seed-taxonomy category only for recognizable issues."""
    value = str(text or "")
    for category, patterns in CATEGORY_PATTERNS:
        if any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns):
            return category
    return None


VERIFIED_LOCATION_SOURCES = ("geoapify", "nominatim", "photon", "citizen_pin")
APPROXIMATE_LOCATION_SOURCES = (
    "ward_approximate", "geoapify_midpoint", "nominatim_midpoint", "photon_midpoint",
)


def location_confidence(source):
    if source in VERIFIED_LOCATION_SOURCES:
        return "verified"
    if source in APPROXIMATE_LOCATION_SOURCES:
        return "approximate"
    return "unknown"


def valid_location_coordinates(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(lat)
        and math.isfinite(lng)
        and MUMBAI_LAT_RANGE[0] <= lat <= MUMBAI_LAT_RANGE[1]
        and MUMBAI_LNG_RANGE[0] <= lng <= MUMBAI_LNG_RANGE[1]
    )


def has_verified_location(source, lat, lng):
    return source in VERIFIED_LOCATION_SOURCES and valid_location_coordinates(lat, lng)


def valid_embedding(value):
    try:
        vector = np.asarray(value, dtype=float)
        norm = float(np.linalg.norm(vector))
    except (TypeError, ValueError, OverflowError):
        return False
    return vector.ndim == 1 and vector.size > 0 and np.isfinite(vector).all() and math.isfinite(norm) and norm > 0


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


def _cluster_place_text(text, location):
    """Include the resolved provider label when comparing report landmarks.

    The stored complaint text is intentionally unchanged. A verified address
    is useful clustering evidence when two language variants name the same
    site differently or omit its name in the complaint prose.
    """
    # Put provider-verified place text first: complaint phrasing may have an
    # ambiguous nearby landmark, while the accepted geocoder label is the
    # strongest shared identity for cross-language duplicates.
    location_text = str(location or "").strip()
    if location_text.casefold().startswith("selected map point"):
        location_text = ""
    # Prefer the place explicitly named in the complaint when it can be
    # extracted. Provider labels often append a nearby micro-locality (for
    # example, "Kirol") to a station name, which can otherwise make an exact
    # citizen pin look like a different place from the Hindi/Marathi reports.
    if text and cluster_place_identity(text):
        return str(text).strip()
    return "\n".join(part.strip() for part in (location_text, text or "") if part and part.strip())


def _verified_location_key(location):
    """Canonical key for an identical provider-verified address label."""
    raw = str(location or "").strip()
    if not raw or raw.casefold().startswith("selected map point"):
        return ""
    value = unicodedata.normalize("NFKC", raw).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    value = " ".join(value.split())
    # Avoid treating short, generic locality labels as an exact landmark.
    return value if len(value) >= 24 and len(value.split()) >= 4 else ""


def _places_share_locality(first, second):
    """Recognize a shared locality when nearby POI labels differ by facility.

    For example, a precise railway-station pin can be reverse-geocoded to a
    nearby police station. The shared locality token is only useful alongside
    a shared issue and the existing strict distance bounds.
    """
    if not first or not second:
        return False
    if first == second:
        return True
    first_tokens = set(re.findall(r"[\w]+", first.casefold(), flags=re.UNICODE))
    second_tokens = set(re.findall(r"[\w]+", second.casefold(), flags=re.UNICODE))
    shared = first_tokens & second_tokens
    return bool(shared) and (len(first_tokens) == 1 or len(second_tokens) == 1 or len(shared) >= 1)


def assign_cluster(session, embedding, lat=None, lng=None, *, complaint_text=None, location_label=None):
    active_cluster_statuses = ("open", "inspection_scheduled", "contractor_allotted", "in_progress")
    next_cluster_id = lambda: int(session.scalar(select(func.max(Complaint.cluster_id)).where(
        Complaint.cluster_id != -1, Complaint.review_state == "accepted",
        Complaint.status.in_(active_cluster_statuses),
    )) or 0) + 1
    if not valid_location_coordinates(lat, lng):
        logger.info("CLUSTER: report has no valid Mumbai coordinates; retaining unmapped state")
        return -1
    try:
        embedding = np.asarray(embedding, dtype=float)
        embedding_norm = float(np.linalg.norm(embedding))
    except (TypeError, ValueError):
        logger.debug("CLUSTER: malformed submission embedding; retaining unmapped state")
        return -1

    if not np.isfinite(embedding_norm) or embedding_norm == 0:
        logger.debug("CLUSTER: invalid submission embedding; retaining unmapped state")
        return -1

    # A report without a resolved/pinned location must not receive a map pin
    # or join a semantic cluster. -1 is the existing unmapped sentinel.
    if lat is None or lng is None:
        logger.info("CLUSTER: report has no coordinates; retaining unmapped state")
        return -1

    rows = session.execute(
        select(
            Complaint.cluster_id,
            Complaint.id,
            Complaint.embedding,
            Complaint.lat,
            Complaint.lng,
            Complaint.location_source,
            Complaint.text,
            Complaint.location,
        ).where(
            Complaint.cluster_id != -1,
            Complaint.embedding.is_not(None),
            Complaint.review_state == "accepted",
            Complaint.status.in_(active_cluster_statuses),
            Complaint.location_source.in_(VERIFIED_LOCATION_SOURCES),
            Complaint.lat.is_not(None),
            Complaint.lng.is_not(None),
        )
    ).all()
    groups = {}
    for cluster_id, _member_id, vector, member_lat, member_lng, _location_source, member_text, member_location in rows:
        try:
            member_lat = float(member_lat)
            member_lng = float(member_lng)
        except (TypeError, ValueError, OverflowError):
            continue
        if not (
            math.isfinite(member_lat)
            and math.isfinite(member_lng)
            and MUMBAI_LAT_RANGE[0] <= member_lat <= MUMBAI_LAT_RANGE[1]
            and MUMBAI_LNG_RANGE[0] <= member_lng <= MUMBAI_LNG_RANGE[1]
        ):
            logger.debug("CLUSTER: cluster=%s skipped invalid member coordinates", cluster_id)
            continue
        try:
            member_vector = np.asarray(vector, dtype=float)
        except (TypeError, ValueError):
            logger.debug(
                "CLUSTER: cluster=%s skipped malformed member embedding",
                cluster_id,
            )
            continue
        groups.setdefault(cluster_id, []).append((
            member_vector, member_lat, member_lng, member_text or "",
            _cluster_place_text(member_text, member_location),
            _verified_location_key(member_location),
        ))

    valid = []
    requested_issues = cluster_issue_concepts(complaint_text or "") & DISTINCT_CLUSTER_ISSUES
    requested_place = cluster_place_identity(complaint_text or "")
    requested_location_key = _verified_location_key(location_label)
    for cluster_id, members in groups.items():
        similarities = []
        comparable_members = members
        if requested_issues:
            comparable_members = []
            for member in members:
                member_issues = cluster_issue_concepts(member[3]) & DISTINCT_CLUSTER_ISSUES
                # Shared place wording (such as "footpath") must not merge
                # different events, such as a broken footpath and hawker
                # encroachment. Reports without a recognized issue stay on the
                # existing embedding-only path for backward compatibility.
                if not member_issues or requested_issues.intersection(member_issues):
                    comparable_members.append(member)
            if not comparable_members:
                logger.debug("CLUSTER: cluster=%s rejected reason=issue_type_mismatch", cluster_id)
                continue
        if requested_place:
            place_compatible_members = []
            for member in comparable_members:
                member_place = cluster_place_identity(member[4])
                if (
                    not member_place
                    or member_place == requested_place
                    or _places_share_locality(member_place, requested_place)
                    or (requested_location_key and member[5] == requested_location_key)
                ):
                    place_compatible_members.append(member)
            if not place_compatible_members:
                logger.debug("CLUSTER: cluster=%s rejected reason=named_place_mismatch", cluster_id)
                continue
            comparable_members = place_compatible_members
        for vector, _, _, _member_text, _member_place_text, _member_location_key in comparable_members:
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
        same_named_place = bool(requested_place) and bool(comparable_members) and all(
            _places_share_locality(cluster_place_identity(member[4]), requested_place)
            for member in comparable_members
        )
        same_verified_address = bool(requested_location_key) and bool(comparable_members) and all(
            member[5] == requested_location_key for member in comparable_members
        )
        shared_issue = bool(requested_issues) and any(
            requested_issues & (cluster_issue_concepts(member[3]) & DISTINCT_CLUSTER_ISSUES)
            for member in comparable_members
        )
        coordinates = [
            (member_lat, member_lng)
            for _, member_lat, member_lng, _member_text, _member_place_text, _member_location_key in comparable_members
            if member_lat is not None and member_lng is not None
        ]
        distance = None
        if coordinates:
            centroid_lat = float(np.mean([coordinate[0] for coordinate in coordinates]))
            centroid_lng = float(np.mean([coordinate[1] for coordinate in coordinates]))
            distance = haversine_distance(lat, lng, centroid_lat, centroid_lng)

        max_cluster_radius = min(MAX_CLUSTER_RADIUS_KM, MAX_SAFE_CLUSTER_RADIUS_KM)
        geo_ok = distance is not None and distance <= max_cluster_radius
        relaxed_same_location = (
            distance is not None and distance <= SAME_LOCATION_RADIUS_KM
        )
        same_site_issue = (same_named_place or same_verified_address) and shared_issue and relaxed_same_location
        threshold_used = (
            SAME_LOCATION_SEMANTIC_THRESHOLD
            if relaxed_same_location
            else THRESHOLD
        )
        semantic_ok = best_similarity >= threshold_used or same_site_issue
        accepted = semantic_ok and geo_ok
        if distance is None:
            logger.debug("CLUSTER: cluster=%s rejected reason=no_member_coordinates", cluster_id)
        logger.debug(
            "CLUSTER: cluster=%s similarity=%.4f distance=%s km "
            "threshold_used=%.2f relaxed_same_location=%s exact_address=%s shared_issue=%s accepted=%s "
            "max_radius_km=%.2f",
            cluster_id,
            best_similarity,
            "n/a" if distance is None else f"{distance:.2f}",
            threshold_used,
            relaxed_same_location,
            same_verified_address,
            shared_issue,
            accepted,
            max_cluster_radius,
        )
        if accepted:
            valid.append((best_similarity, cluster_id, distance, centroid_lat, centroid_lng, same_site_issue))

    if valid:
        valid.sort(reverse=True)
        selected_cluster = valid[0][1]
        # If a new report bridges duplicate clusters at the same place,
        # consolidate them under the best match. Candidate clusters have
        # already passed issue/place compatibility and the same-site/shared-
        # issue exception used for initial matching.
        selected_centroid = (valid[0][3], valid[0][4])
        duplicate_clusters = set()
        for similarity, cluster_id, distance, centroid_lat, centroid_lng, same_site_issue in valid[1:]:
            centroid_distance = haversine_distance(
                selected_centroid[0], selected_centroid[1], centroid_lat, centroid_lng
            )
            if (
                (similarity >= SAME_LOCATION_SEMANTIC_THRESHOLD or same_site_issue)
                and distance is not None
                and distance <= SAME_LOCATION_RADIUS_KM
                and centroid_distance <= SAME_LOCATION_RADIUS_KM
                and cluster_id != selected_cluster
            ):
                duplicate_clusters.add(cluster_id)
        for duplicate_cluster in duplicate_clusters:
            session.execute(
                update(Complaint)
                .where(
                    Complaint.cluster_id == duplicate_cluster,
                    Complaint.review_state == "accepted",
                    Complaint.status.in_(active_cluster_statuses),
                )
                .values(cluster_id=selected_cluster)
            )
            logger.info(
                "CLUSTER: duplicate cluster=%s consolidated_into=%s",
                duplicate_cluster,
                selected_cluster,
            )
        return selected_cluster
    return next_cluster_id()


def assign_citizen_pinned_cluster(session, embedding, lat, lng, *, complaint_text=None, location_label=None):
    active_cluster_statuses = ("open", "inspection_scheduled", "contractor_allotted", "in_progress")
    next_cluster_id = lambda: int(session.scalar(select(func.max(Complaint.cluster_id)).where(
        Complaint.cluster_id != -1, Complaint.review_state == "accepted",
        Complaint.status.in_(active_cluster_statuses),
    )) or 0) + 1
    """Match citizen pins locally, with a guarded same-landmark exception."""
    if not valid_location_coordinates(lat, lng):
        return -1
    try:
        candidate = np.asarray(embedding, dtype=float)
        candidate_norm = float(np.linalg.norm(candidate))
    except (TypeError, ValueError):
        candidate_norm = 0.0
        candidate = np.asarray([], dtype=float)
    if not np.isfinite(candidate_norm) or candidate_norm == 0:
        return -1

    rows = session.execute(
        select(
            Complaint.cluster_id, Complaint.embedding, Complaint.lat,
            Complaint.lng, Complaint.text, Complaint.location,
        ).where(
            Complaint.cluster_id != -1,
            Complaint.embedding.is_not(None),
            Complaint.review_state == "accepted",
            Complaint.status.in_(active_cluster_statuses),
            Complaint.location_source.in_(VERIFIED_LOCATION_SOURCES),
            Complaint.lat.is_not(None),
            Complaint.lng.is_not(None),
        )
    ).all()
    clusters = {}
    for cluster_id, vector, member_lat, member_lng, text, location in rows:
        clusters.setdefault(cluster_id, []).append((vector, member_lat, member_lng, text or "", location or ""))

    matches = []
    requested_issues = cluster_issue_concepts(complaint_text or "") & DISTINCT_CLUSTER_ISSUES
    requested_place = cluster_place_identity(complaint_text or "")
    requested_location_key = _verified_location_key(location_label)
    for cluster_id, members in clusters.items():
        usable_coordinates = []
        similarities = []
        same_place_members = []
        for vector, member_lat, member_lng, member_text, member_location in members:
            member_place = cluster_place_identity(_cluster_place_text(member_text, member_location))
            member_issues = cluster_issue_concepts(member_text) & DISTINCT_CLUSTER_ISSUES
            issue_compatible = not requested_issues or not member_issues or bool(requested_issues & member_issues)
            member_location_key = _verified_location_key(member_location)
            place_compatible = (
                not requested_place or not member_place or requested_place == member_place
                or _places_share_locality(requested_place, member_place)
                or (requested_location_key and member_location_key == requested_location_key)
            )
            if not issue_compatible or not place_compatible:
                continue
            if not valid_location_coordinates(member_lat, member_lng):
                continue
            usable_coordinates.append((float(member_lat), float(member_lng)))
            same_place_members.append((member_place, member_issues, member_location_key))
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
        same_named_place = bool(requested_place) and bool(same_place_members) and all(
            member_place == requested_place
            for member_place, _member_issues, _member_location_key in same_place_members
        )
        same_locality = bool(requested_place) and bool(same_place_members) and all(
            _places_share_locality(member_place, requested_place)
            for member_place, _member_issues, _member_location_key in same_place_members
        )
        same_verified_address = bool(requested_location_key) and bool(same_place_members) and all(
            member_location_key == requested_location_key
            for _member_place, _member_issues, member_location_key in same_place_members
        )
        shared_issue = bool(requested_issues) and any(
            requested_issues & member_issues
            for _member_place, member_issues, _member_location_key in same_place_members
        )
        same_site_issue = (same_named_place or same_locality or same_verified_address) and shared_issue
        same_site_distance_limit = (
            MAX_SAFE_CLUSTER_RADIUS_KM
            if same_named_place or same_verified_address
            else SAME_LOCATION_RADIUS_KM
        )
        location_ok = (
            distance_km <= CITIZEN_PIN_CLUSTER_RADIUS_KM
            or (same_site_issue and distance_km <= same_site_distance_limit)
        )
        semantic_ok = best_similarity >= THRESHOLD or same_site_issue
        logger.debug(
            "CITIZEN PIN CLUSTER cluster=%s distance_m=%.1f location_ok=%s similarity=%.4f "
            "semantic_threshold=%.2f semantic_ok=%s named_place=%s shared_locality=%s shared_issue=%s",
            cluster_id,
            distance_km * 1000,
            location_ok,
            best_similarity,
            THRESHOLD,
            semantic_ok,
            same_named_place,
            same_locality,
            shared_issue,
        )
        if location_ok and semantic_ok:
            matches.append((best_similarity, -distance_km, cluster_id))

    if matches:
        return max(matches)[2]
    return next_cluster_id()


def rerank(session, cluster_id):
    if cluster_id == -1:
        return
    rows = list(session.scalars(select(Complaint).where(
        Complaint.cluster_id == cluster_id,
        Complaint.review_state == "accepted",
        Complaint.status.in_(("open", "inspection_scheduled", "contractor_allotted", "in_progress")),
        Complaint.location_source.in_(VERIFIED_LOCATION_SOURCES),
        Complaint.lat.is_not(None),
        Complaint.lng.is_not(None),
    ).order_by(Complaint.final_score.desc(), Complaint.has_media.desc())))
    for rank, complaint in enumerate(rows, 1): complaint.rank_in_cluster = rank


def reconcile_named_place_clusters(session):
    """Consolidate existing duplicate clusters only for the same named site.

    This repairs overlapping clusters created before the stricter cluster
    matching rules were deployed. Requiring the same extracted place, a
    compatible issue, same ward when known, close coordinates and multilingual
    embedding similarity prevents this startup repair from merging nearby
    schools or unrelated incidents. An identical full verified address label
    with a shared issue is also accepted as strong site evidence. A citizen pin may be farther from the
    geocoder's campus point, so a shared named site and issue can extend that
    bound to the normal safe cluster radius.
    """
    rows = session.execute(
        select(
            Complaint.cluster_id, Complaint.embedding, Complaint.lat,
            Complaint.lng, Complaint.ward_code, Complaint.text, Complaint.location,
            Complaint.location_source,
        ).where(
            Complaint.cluster_id != -1,
            Complaint.embedding.is_not(None),
            Complaint.review_state == "accepted",
            Complaint.status.in_(("open", "inspection_scheduled", "contractor_allotted", "in_progress")),
            Complaint.location_source.in_(VERIFIED_LOCATION_SOURCES),
            Complaint.lat.is_not(None),
            Complaint.lng.is_not(None),
        )
    ).all()
    groups = {}
    for cluster_id, vector, lat, lng, ward, text, location, location_source in rows:
        groups.setdefault(cluster_id, []).append((vector, lat, lng, ward, text or "", location or "", location_source or "", _verified_location_key(location)))

    summaries = {}
    for cluster_id, members in groups.items():
        places = {cluster_place_identity(_cluster_place_text(member[4], member[5])) for member in members}
        places.discard("")
        # Don't infer a cluster's identity if some member reports a different
        # named place or if no member carries one.
        address_keys = {member[7] for member in members}
        address_keys.discard("")
        exact_shared_address = len(address_keys) == 1 and all(member[7] for member in members)
        if not exact_shared_address and (
            len(places) != 1
            or any(not cluster_place_identity(_cluster_place_text(member[4], member[5])) for member in members)
        ):
            continue
        vectors, coordinates = [], []
        issues = set()
        wards = {str(member[3]).strip() for member in members if member[3]}
        valid = True
        location_sources = {member[6] for member in members}
        for vector, lat, lng, _ward, text, _location, _location_source, _address_key in members:
            try:
                array = np.asarray(vector, dtype=float)
                lat, lng = float(lat), float(lng)
                if array.ndim != 1 or not np.all(np.isfinite(array)) or np.linalg.norm(array) == 0:
                    valid = False
                    break
                if not valid_location_coordinates(lat, lng):
                    valid = False
                    break
                vectors.append(array)
                coordinates.append((lat, lng))
            except (TypeError, ValueError, OverflowError):
                valid = False
                break
            issues.update(cluster_issue_concepts(text) & DISTINCT_CLUSTER_ISSUES)
        if not valid or not vectors or len({vector.shape for vector in vectors}) != 1:
            continue
        center = np.mean(vectors, axis=0)
        norm = float(np.linalg.norm(center))
        if not np.isfinite(norm) or norm == 0:
            continue
        center /= norm
        summaries[cluster_id] = {
            "place": ("address:" + next(iter(address_keys))) if len(address_keys) == 1 and all(member[7] for member in members) else next(iter(places)),
            "issues": issues,
            "wards": wards,
            "vector": center,
            "lat": float(np.mean([point[0] for point in coordinates])),
            "lng": float(np.mean([point[1] for point in coordinates])),
            "size": len(members),
            "location_sources": location_sources,
        }

    cluster_ids = sorted(summaries)
    merged = 0
    for index, first_id in enumerate(cluster_ids):
        if first_id not in summaries:
            continue
        first = summaries[first_id]
        for second_id in cluster_ids[index + 1:]:
            if second_id not in summaries:
                continue
            second = summaries[second_id]
            same_named_or_locality = _places_share_locality(first["place"], second["place"])
            if first["wards"] and second["wards"] and first["wards"].isdisjoint(second["wards"]):
                continue
            if first["issues"] and second["issues"] and first["issues"].isdisjoint(second["issues"]):
                continue
            if first["vector"].shape != second["vector"].shape:
                continue
            same_issue = bool(first["issues"] & second["issues"])
            same_named_site_pin = (
                "citizen_pin" in (first["location_sources"] | second["location_sources"])
                and same_issue and same_named_or_locality
            )
            if first["place"] != second["place"] and not same_named_site_pin:
                continue
            if same_named_site_pin and first["place"] == second["place"]:
                max_merge_distance = MAX_SAFE_CLUSTER_RADIUS_KM
            elif same_named_site_pin:
                max_merge_distance = SAME_LOCATION_RADIUS_KM
            elif same_issue:
                max_merge_distance = SAME_LOCATION_RADIUS_KM
            else:
                max_merge_distance = 0.15
            if haversine_distance(first["lat"], first["lng"], second["lat"], second["lng"]) > max_merge_distance:
                continue
            similarity = float(np.dot(first["vector"], second["vector"]))
            # At the same verified landmark, a shared language-independent
            # issue tag can outweigh embedding variation across languages.
            # The larger distance allowance is only available when one group
            # contains an explicit citizen pin.
            if not same_named_site_pin and not same_issue and (
                not np.isfinite(similarity)
                or similarity < SAME_LOCATION_SEMANTIC_THRESHOLD
            ):
                continue
            winner_id, loser_id = first_id, second_id
            session.execute(
                update(Complaint)
                .where(
                    Complaint.cluster_id == loser_id,
                    Complaint.review_state == "accepted",
                    Complaint.status.in_(("open", "inspection_scheduled", "contractor_allotted", "in_progress")),
                )
                .values(cluster_id=winner_id)
            )
            merged += 1
            old_size, added_size = first["size"], second["size"]
            combined_vector = first["vector"] * old_size + second["vector"] * added_size
            combined_norm = float(np.linalg.norm(combined_vector))
            if np.isfinite(combined_norm) and combined_norm > 0:
                first["vector"] = combined_vector / combined_norm
            first["lat"] = (first["lat"] * old_size + second["lat"] * added_size) / (old_size + added_size)
            first["lng"] = (first["lng"] * old_size + second["lng"] * added_size) / (old_size + added_size)
            first["size"] = old_size + added_size
            first["issues"].update(second["issues"])
            first["wards"].update(second["wards"])
            first["location_sources"].update(second["location_sources"])
            summaries.pop(loser_id, None)
            logger.info(
                "CLUSTER: startup_duplicate cluster=%s consolidated_into=%s place=%s similarity=%.3f",
                loser_id, winner_id, winner["place"], similarity,
            )
    if merged:
        session.commit()
        for cluster_id in summaries:
            rerank(session, cluster_id)
        session.commit()
    logger.info("CLUSTER: startup_reconcile merged_clusters=%s", merged)
    return merged


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
    if pending_review or not severity_analysis_text:
        cluster_id = -1
    elif citizen_id is not None and pinned_location is not None:
        cluster_id = assign_citizen_pinned_cluster(
            session, cluster_embedding, complaint_lat, complaint_lng,
            complaint_text=severity_analysis_text, location_label=resolved_location,
        )
    elif has_verified_location(location_source, complaint_lat, complaint_lng):
        cluster_id = assign_cluster(
            session,
            cluster_embedding,
            complaint_lat,
            complaint_lng,
            complaint_text=_cluster_place_text(severity_analysis_text, resolved_location),
            location_label=resolved_location,
        )
    else:
        # Ward centres, between-place estimates, and legacy unknown coordinates
        # remain visible as unresolved/approximate staff markers, never clusters.
        cluster_id = -1
    logger.debug("Cluster assignment complete: %s", cluster_id)

    if text_severity and photo_severity: score = 0.45 * SEVERITY_VALUES[text_severity] + 0.55 * SEVERITY_VALUES[photo_severity]
    elif photo_severity: score = SEVERITY_VALUES[photo_severity]
    elif text_severity: score = SEVERITY_VALUES[text_severity]
    else: score = 2
    complaint = Complaint(id="CMP-" + uuid.uuid4().hex[:10].upper(), citizen_id=citizen_id, source=source or ("citizen" if citizen_id is not None else "bmc_staff"), source_post_id=source_post_id, source_author=source_author, source_url=source_url, review_state="pending" if pending_review else "accepted", status="open", text=display_text, language=payload.language, ward_code=inferred_ward, location=resolved_location, location_source=location_source, category=infer_category(severity_analysis_text), cluster_id=cluster_id, embedding=cluster_embedding.tolist(), text_severity=text_severity, photo_severity=photo_severity, final_severity=LABELS[round(score) - 1], final_score=score, has_media=bool(photo_severity), image_data=image_data, uploaded_photo_data=uploaded_photo_data, has_video=has_video, video_data=video_data, transcript=transcript, is_real=True, lat=complaint_lat, lng=complaint_lng)
    session.add(complaint); session.flush();
    if not pending_review: rerank(session, cluster_id)
    session.commit(); session.refresh(complaint)
    return complaint
