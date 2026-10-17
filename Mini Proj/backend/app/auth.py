"""Citizen email authentication routes and JWT dependencies."""

import hashlib
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field, field_validator
from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import SessionLocal
from .email import EmailDeliveryError, send_password_reset_email, send_verification_email
from .models import EmailVerificationToken, PasswordResetToken, User, utc_now


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["citizen authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
password_hasher = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = password_hasher.hash("invalid-login-timing-protection")
_RATE_LOCK = threading.Lock()
_RATE_EVENTS: dict[tuple[str, str], list[float]] = {}
_LOGIN_FAILURES: dict[str, list[float]] = {}

RESEND_COOLDOWN_SECONDS = 60
PASSWORD_RESET_COOLDOWN_SECONDS = 60
VERIFY_TOKEN_HOURS = 24
RESET_TOKEN_MINUTES = 45
LOGIN_FAILURE_LIMIT = 8
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name is required")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value


class LoginRequest(EmailRequest):
    password: str = Field(min_length=1, max_length=256)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY", "")
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET_KEY must be configured with at least 32 characters")
    return secret


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _make_one_time_token() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)
    return raw_token, _token_hash(raw_token)


def _safe_user(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "email_verified": user.email_verified,
    }


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limited(bucket: str, key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    with _RATE_LOCK:
        events = [at for at in _RATE_EVENTS.get((bucket, key), []) if now - at < window_seconds]
        if len(events) >= limit:
            _RATE_EVENTS[(bucket, key)] = events
            return True
        events.append(now)
        _RATE_EVENTS[(bucket, key)] = events
        return False


def _newest_token(db: Session, model, user_id: int):
    return db.scalars(
        select(model).where(model.user_id == user_id).order_by(model.created_at.desc()).limit(1)
    ).first()


def _cooldown_active(db: Session, model, user_id: int, seconds: int, now: datetime) -> bool:
    latest = _newest_token(db, model, user_id)
    # Failed deliveries are marked used so users can retry immediately.
    if not latest or latest.used_at is not None:
        return False
    created = latest.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return now - created < timedelta(seconds=seconds)


def _deliver_safely(sender, email: str, token: str) -> bool:
    try:
        sender(email, token)
    except EmailDeliveryError as error:
        logger.warning("AUTH email delivery status=failed type=%s", error.classification)
        return False
    except Exception:
        logger.warning("AUTH email delivery status=failed type=unexpected_error")
        return False
    logger.info("AUTH email delivery status=sent")
    return True


def _create_access_token(user: User) -> str:
    try:
        expires_in = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    except ValueError as error:
        raise RuntimeError("JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be an integer") from error
    if expires_in < 1:
        raise RuntimeError("JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be positive")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user.id), "role": user.role, "iat": now, "exp": now + timedelta(minutes=expires_in)},
        _jwt_secret(),
        algorithm="HS256",
    )


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
def register(payload: RegisterRequest, request: Request, db: Annotated[Session, Depends(get_db)]):
    if _rate_limited("register", _client_key(request), 10, 3600):
        raise HTTPException(status_code=429, detail="Please try again later.")
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing:
        return {"message": "If registration can proceed, a verification email will be sent."}

    raw_token, token_hash = _make_one_time_token()
    now = utc_now()
    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=password_hasher.hash(payload.password),
        role="citizen",
        email_verified=False,
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
        verification_token = EmailVerificationToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=now + timedelta(hours=VERIFY_TOKEN_HOURS),
            created_at=now,
        )
        db.add(verification_token)
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"message": "If registration can proceed, a verification email will be sent."}
    if not _deliver_safely(send_verification_email, user.email, raw_token):
        verification_token.used_at = utc_now()
        db.commit()
    return {"message": "If registration can proceed, a verification email will be sent."}


@router.get("/verify-email")
def verify_email(token: str, db: Annotated[Session, Depends(get_db)]):
    now = utc_now()
    token_row = db.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == _token_hash(token),
            EmailVerificationToken.used_at.is_(None),
            EmailVerificationToken.expires_at > now,
        )
    )
    if not token_row:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link.")
    user = db.get(User, token_row.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link.")
    user.email_verified = True
    user.email_verified_at = now
    token_row.used_at = now
    db.commit()
    return {"message": "Email address verified. You can now log in."}


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
def resend_verification(payload: EmailRequest, request: Request, db: Annotated[Session, Depends(get_db)]):
    generic = {"message": "If the account exists and needs verification, a verification email has been sent."}
    if _rate_limited("resend", _client_key(request), 10, 3600):
        return generic
    user = db.scalar(select(User).where(User.email == payload.email))
    if not user or user.email_verified or not user.is_active:
        return generic
    now = utc_now()
    if _cooldown_active(db, EmailVerificationToken, user.id, RESEND_COOLDOWN_SECONDS, now):
        return generic
    db.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user.id, EmailVerificationToken.used_at.is_(None))
        .values(used_at=now)
    )
    raw_token, token_hash = _make_one_time_token()
    verification_token = EmailVerificationToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=now + timedelta(hours=VERIFY_TOKEN_HOURS),
        created_at=now,
    )
    db.add(verification_token)
    db.commit()
    if not _deliver_safely(send_verification_email, user.email, raw_token):
        verification_token.used_at = utc_now()
        db.commit()
    return generic


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Annotated[Session, Depends(get_db)]):
    generic_error = HTTPException(status_code=401, detail="Invalid email or password.", headers={"WWW-Authenticate": "Bearer"})
    now_mono = time.monotonic()
    with _RATE_LOCK:
        failures = [at for at in _LOGIN_FAILURES.get(payload.email, []) if now_mono - at < LOGIN_FAILURE_WINDOW_SECONDS]
        _LOGIN_FAILURES[payload.email] = failures
    if len(failures) >= LOGIN_FAILURE_LIMIT or _rate_limited("login", _client_key(request), 30, 900):
        raise HTTPException(status_code=429, detail="Too many login attempts. Please try again later.")

    user = db.scalar(select(User).where(User.email == payload.email))
    password_hash = user.password_hash if user else _DUMMY_PASSWORD_HASH
    try:
        password_ok = password_hasher.verify(payload.password, password_hash)
    except Exception:
        password_ok = False
    if not user or not password_ok or not user.email_verified or not user.is_active:
        with _RATE_LOCK:
            _LOGIN_FAILURES[payload.email] = failures + [now_mono]
        raise generic_error
    with _RATE_LOCK:
        _LOGIN_FAILURES.pop(payload.email, None)
    return {"access_token": _create_access_token(user), "token_type": "bearer", "user": _safe_user(user)}


def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    unauthorized = HTTPException(status_code=401, detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"})
    if not token:
        raise unauthorized
    try:
        claims = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
        subject = claims.get("sub")
        role = claims.get("role")
        user_id = int(subject)
    except (jwt.InvalidTokenError, TypeError, ValueError, RuntimeError):
        raise unauthorized
    user = db.get(User, user_id)
    if not user or not user.is_active or user.role != role:
        raise unauthorized
    return user


@router.get("/me")
def me(current_user: Annotated[User, Depends(get_current_user)]):
    return _safe_user(current_user)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(payload: EmailRequest, request: Request, db: Annotated[Session, Depends(get_db)]):
    generic = {"message": "If the account exists, password reset instructions will be sent."}
    if _rate_limited("forgot", _client_key(request), 10, 3600):
        return generic
    user = db.scalar(select(User).where(User.email == payload.email, User.is_active.is_(True)))
    if not user:
        return generic
    now = utc_now()
    if _cooldown_active(db, PasswordResetToken, user.id, PASSWORD_RESET_COOLDOWN_SECONDS, now):
        return generic
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    raw_token, token_hash = _make_one_time_token()
    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=now + timedelta(minutes=RESET_TOKEN_MINUTES),
        created_at=now,
    )
    db.add(reset_token)
    db.commit()
    if not _deliver_safely(send_password_reset_email, user.email, raw_token):
        reset_token.used_at = utc_now()
        db.commit()
    return generic


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Annotated[Session, Depends(get_db)]):
    now = utc_now()
    token_row = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == _token_hash(payload.token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
    )
    if not token_row:
        raise HTTPException(status_code=400, detail="Invalid or expired password reset link.")
    user = db.get(User, token_row.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="Invalid or expired password reset link.")
    user.password_hash = password_hasher.hash(payload.new_password)
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    db.commit()
    return {"message": "Password updated. You can now log in."}
