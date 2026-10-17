from celery import Celery

from .config import REDIS_URL


celery_app = Celery("bmc", broker=REDIS_URL, backend=REDIS_URL)


@celery_app.task
def queued_submission(payload):
    """Process non-demo submissions outside the live synchronous flow."""
    from .db import SessionLocal
    from .main import Submission
    from .services import process_submission

    db = SessionLocal()
    try:
        return process_submission(db, Submission(**payload)).id
    finally:
        db.close()
