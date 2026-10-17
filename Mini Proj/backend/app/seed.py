import base64
import csv

import requests
from sqlalchemy import select

from .config import ARTIFACTS_DIR, SEED_ON_STARTUP
from .db import SessionLocal
from .geo import ward_coord
from .models import Complaint
from .services import get_infer, rerank


def f(value):
    try:
        return float(value) if value not in ("", None) else None
    except ValueError:
        return None


def severity_bucket(score):
    if score is None or score <= 2: return "Low"
    if score == 3: return "Medium"
    return "High"


def fetch_image_data(url):
    if not url: return None
    try:
        response = requests.get(url, timeout=5, headers={"User-Agent": "BMC-Complaint-Demo/1.0"})
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
        if not content_type.startswith("image/"): return None
        return f"data:{content_type};base64," + base64.b64encode(response.content).decode("ascii")
    except requests.RequestException:
        return None


def seed():
    if not SEED_ON_STARTUP: return
    db = SessionLocal()
    try:
        if db.scalar(select(Complaint.id).limit(1)): return
        path = ARTIFACTS_DIR / "BMC_Complaints_FINAL_v4.csv"
        if not path.exists(): raise RuntimeError(f"Missing seed CSV: {path}")
        model = get_infer()
        with path.open(encoding="utf-8-sig") as file:
            for row in csv.DictReader(file):
                seed_text = row.get("text", "").strip()
                seed_location = row.get("location", "").strip()
                cluster_text = seed_text if not seed_location else f"{seed_text}\nLocation: {seed_location}"
                score = f(row.get("final_severity_score")) or f(row.get("text_severity_score")) or 2
                text_score = f(row.get("text_severity_score"))
                image_url = row.get("image_url")
                lat, lng = ward_coord(row.get("ward_code"))
                complaint = Complaint(
                    id=row["ID"], text=seed_text, language=row.get("language"), ward_code=row.get("ward_code"), ward_area=row.get("ward_area"), location=seed_location or None,
                    category=row.get("main_category"), sub_issue=row.get("sub_issue"), official_sla_days=f(row.get("official_sla_days")), has_media=row.get("has_media", "").lower() == "yes",
                    image_url=image_url, image_data=fetch_image_data(image_url), is_real=row.get("is_real", "").lower() == "yes", cluster_id=int(f(row.get("cluster_id")) or -1),
                    embedding=model.embedding(cluster_text).tolist(), text_severity=severity_bucket(text_score), final_severity=severity_bucket(score), final_score=score,
                    rank_in_cluster=int(f(row.get("rank_in_cluster")) or 1), lat=lat, lng=lng,
                )
                db.add(complaint)
        db.commit()
        for (cluster_id,) in db.execute(select(Complaint.cluster_id).distinct()): rerank(db, cluster_id)
        db.commit()
    finally:
        db.close()
