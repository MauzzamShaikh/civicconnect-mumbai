import os
from pathlib import Path


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://bmc:bmc@db:5432/bmc",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "/artifacts"))
THRESHOLD = float(os.getenv("CLUSTER_SIMILARITY_THRESHOLD", "0.75"))
MAX_CLUSTER_RADIUS_KM = float(os.getenv("MAX_CLUSTER_RADIUS_KM", "3.0"))
SAME_LOCATION_RADIUS_KM = float(os.getenv("SAME_LOCATION_RADIUS_KM", "0.75"))
SAME_LOCATION_SEMANTIC_THRESHOLD = float(
    os.getenv("SAME_LOCATION_SEMANTIC_THRESHOLD", "0.55")
)
SEED_ON_STARTUP = os.getenv("SEED_ON_STARTUP", "true").lower() == "true"
SYNC_LIVE_DEMO = os.getenv("SYNC_LIVE_DEMO", "true").lower() == "true"
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
