"""Authentication route tests with SQLite and mocked email delivery."""

import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth
from app.db import Base
from app.models import EmailVerificationToken, PasswordResetToken, User, utc_now


class CitizenAuthTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[User.__table__, EmailVerificationToken.__table__, PasswordResetToken.__table__],
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.request = Request({
            "type": "http", "method": "POST", "path": "/auth/test", "headers": [],
            "client": ("auth-test", 1234), "server": ("test", 80), "scheme": "http",
        })
        self._old_secret = os.environ.get("JWT_SECRET_KEY")
        os.environ["JWT_SECRET_KEY"] = "test-only-secret-key-that-is-at-least-32-bytes"
        auth._LOGIN_FAILURES.clear()
        auth._RATE_EVENTS.clear()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if self._old_secret is None:
            os.environ.pop("JWT_SECRET_KEY", None)
        else:
            os.environ["JWT_SECRET_KEY"] = self._old_secret

    def make_user(self, email="citizen@example.com", verified=True, password="valid-password-123"):
        user = User(name="Test Citizen", email=email, password_hash=auth.password_hasher.hash(password),
                    role="citizen", email_verified=verified, is_active=True)
        self.db.add(user)
        self.db.commit()
        return user

    def register_user(self, email="citizen@example.com"):
        sent = []
        with patch.object(auth, "send_verification_email", side_effect=lambda address, token: sent.append((address, token))):
            response = auth.register(auth.RegisterRequest(name="Test Citizen", email=email.upper(), password="valid-password-123"), self.request, self.db)
        return response, sent

    def test_01_register_new_user(self):
        response, sent = self.register_user()
        self.assertIn("message", response)
        user = self.db.scalar(select(User).where(User.email == "citizen@example.com"))
        self.assertIsNotNone(user)
        self.assertFalse(user.email_verified)
        self.assertEqual(user.role, "citizen")
        self.assertEqual(len(sent), 1)

    def test_public_registration_cannot_assign_admin_role(self):
        sent = []
        payload = auth.RegisterRequest.model_validate({
            "name": "Attempted Admin",
            "email": "attempted-admin@example.com",
            "password": "valid-password-123",
            "role": "admin",
        })
        with patch.object(auth, "send_verification_email", side_effect=lambda address, token: sent.append(address)):
            auth.register(payload, self.request, self.db)
        user = self.db.scalar(select(User).where(User.email == "attempted-admin@example.com"))
        self.assertIsNotNone(user)
        self.assertEqual(user.role, "citizen")
        self.assertEqual(sent, [user.email])

    def test_02_duplicate_email_is_generic(self):
        self.register_user()
        first = auth.register(auth.RegisterRequest(name="Other", email="citizen@example.com", password="valid-password-123"), self.request, self.db)
        self.assertEqual(first["message"], "If registration can proceed, a verification email will be sent.")
        self.assertEqual(len(list(self.db.scalars(select(User)))), 1)

    def test_03_verification_token_created_hashed(self):
        _, sent = self.register_user()
        raw = sent[0][1]
        row = self.db.scalar(select(EmailVerificationToken))
        self.assertNotEqual(row.token_hash, raw)
        self.assertEqual(len(row.token_hash), 64)
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=utc_now().tzinfo)
        self.assertGreater(expires_at, utc_now())

    def test_04_verify_valid_token(self):
        _, sent = self.register_user()
        response = auth.verify_email(sent[0][1], self.db)
        user = self.db.scalar(select(User))
        row = self.db.scalar(select(EmailVerificationToken))
        self.assertTrue(user.email_verified)
        self.assertIsNotNone(user.email_verified_at)
        self.assertIsNotNone(row.used_at)
        self.assertIn("verified", response["message"])

    def test_05_reject_expired_verification_token(self):
        user = self.make_user(verified=False)
        raw, token_hash = auth._make_one_time_token()
        self.db.add(EmailVerificationToken(user_id=user.id, token_hash=token_hash, expires_at=utc_now() - timedelta(seconds=1)))
        self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            auth.verify_email(raw, self.db)
        self.assertEqual(caught.exception.status_code, 400)

    def test_06_reject_reused_verification_token(self):
        _, sent = self.register_user()
        auth.verify_email(sent[0][1], self.db)
        with self.assertRaises(HTTPException):
            auth.verify_email(sent[0][1], self.db)

    def test_07_login_before_verification_rejected(self):
        self.make_user(verified=False)
        with self.assertRaises(HTTPException) as caught:
            auth.login(auth.LoginRequest(email="citizen@example.com", password="valid-password-123"), self.request, self.db)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.detail, "Invalid email or password.")

    def test_08_login_after_verification(self):
        user = self.make_user()
        result = auth.login(auth.LoginRequest(email=user.email, password="valid-password-123"), self.request, self.db)
        self.assertEqual(result["token_type"], "bearer")
        self.assertEqual(result["user"]["id"], user.id)
        self.assertNotIn("password_hash", result["user"])

    def test_09_wrong_password_is_generic(self):
        self.make_user()
        with self.assertRaises(HTTPException) as caught:
            auth.login(auth.LoginRequest(email="citizen@example.com", password="wrong-password"), self.request, self.db)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.detail, "Invalid email or password.")

    def test_10_me_with_valid_jwt(self):
        user = self.make_user()
        token = auth._create_access_token(user)
        current = auth.get_current_user(token, self.db)
        self.assertEqual(auth.me(current)["email"], user.email)

    def test_jwt_signature_and_expiration_are_enforced(self):
        user = self.make_user()
        token = auth._create_access_token(user)
        with self.assertRaises(HTTPException) as bad_signature:
            auth.get_current_user(token + "tampered", self.db)
        self.assertEqual(bad_signature.exception.status_code, 401)

        expired = auth.jwt.encode(
            {"sub": str(user.id), "role": user.role, "iat": utc_now() - timedelta(minutes=2), "exp": utc_now() - timedelta(minutes=1)},
            os.environ["JWT_SECRET_KEY"],
            algorithm="HS256",
        )
        with self.assertRaises(HTTPException) as expired_token:
            auth.get_current_user(expired, self.db)
        self.assertEqual(expired_token.exception.status_code, 401)

    def test_jwt_role_claim_must_match_trusted_user_record(self):
        user = self.make_user()
        forged_role_token = auth.jwt.encode(
            {"sub": str(user.id), "role": "admin", "iat": utc_now(), "exp": utc_now() + timedelta(minutes=5)},
            os.environ["JWT_SECRET_KEY"],
            algorithm="HS256",
        )
        with self.assertRaises(HTTPException) as caught:
            auth.get_current_user(forged_role_token, self.db)
        self.assertEqual(caught.exception.status_code, 401)

    def test_11_me_without_jwt_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            auth.get_current_user(None, self.db)
        self.assertEqual(caught.exception.status_code, 401)

    def test_12_forgot_password_existing_user_sends_mock_email(self):
        user = self.make_user()
        sent = []
        with patch.object(auth, "send_password_reset_email", side_effect=lambda address, token: sent.append((address, token))):
            response = auth.forgot_password(auth.EmailRequest(email=user.email), self.request, self.db)
        row = self.db.scalar(select(PasswordResetToken))
        self.assertEqual(response["message"], "If the account exists, password reset instructions will be sent.")
        self.assertEqual(len(sent), 1)
        self.assertNotEqual(row.token_hash, sent[0][1])

    def test_13_forgot_password_unknown_email_is_generic(self):
        with patch.object(auth, "send_password_reset_email") as sender:
            response = auth.forgot_password(auth.EmailRequest(email="nobody@example.com"), self.request, self.db)
        sender.assert_not_called()
        self.assertEqual(response["message"], "If the account exists, password reset instructions will be sent.")

    def test_14_reset_password_valid_token(self):
        user = self.make_user()
        raw, token_hash = auth._make_one_time_token()
        row = PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=utc_now() + timedelta(minutes=30))
        self.db.add(row)
        self.db.commit()
        auth.reset_password(auth.ResetPasswordRequest(token=raw, new_password="new-valid-password"), self.db)
        self.assertTrue(auth.password_hasher.verify("new-valid-password", user.password_hash))
        self.assertIsNotNone(self.db.get(PasswordResetToken, row.id).used_at)

    def test_15_reset_password_expired_token(self):
        user = self.make_user()
        raw, token_hash = auth._make_one_time_token()
        self.db.add(PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=utc_now() - timedelta(seconds=1)))
        self.db.commit()
        with self.assertRaises(HTTPException):
            auth.reset_password(auth.ResetPasswordRequest(token=raw, new_password="new-valid-password"), self.db)

    def test_16_reset_password_token_cannot_be_reused(self):
        user = self.make_user()
        raw, token_hash = auth._make_one_time_token()
        self.db.add(PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=utc_now() + timedelta(minutes=30)))
        self.db.commit()
        payload = auth.ResetPasswordRequest(token=raw, new_password="new-valid-password")
        auth.reset_password(payload, self.db)
        with self.assertRaises(HTTPException):
            auth.reset_password(payload, self.db)

    def test_17_smtp_failure_is_safe_and_account_can_resend(self):
        sensitive_detail = "smtp-password=must-never-appear-in-logs"

        def fail_delivery(_address, _token):
            try:
                raise RuntimeError(sensitive_detail)
            except RuntimeError as cause:
                raise auth.EmailDeliveryError("smtp_authentication") from cause

        payload = auth.RegisterRequest(
            name="Test Citizen",
            email="citizen@example.com",
            password="valid-password-123",
        )
        with patch.object(auth, "send_verification_email", side_effect=fail_delivery):
            with self.assertLogs(auth.logger, level="WARNING") as captured:
                register_result = auth.register(payload, self.request, self.db)

        user = self.db.scalar(select(User).where(User.email == "citizen@example.com"))
        failed_token = self.db.scalars(
            select(EmailVerificationToken).where(EmailVerificationToken.user_id == user.id)
        ).first()
        self.assertFalse(user.email_verified)
        self.assertIsNotNone(failed_token)
        self.assertIsNotNone(failed_token.used_at)
        self.assertEqual(register_result["message"], "If registration can proceed, a verification email will be sent.")
        self.assertNotIn(sensitive_detail, "\n".join(captured.output))
        self.assertIn("type=smtp_authentication", "\n".join(captured.output))

        resent = []
        with patch.object(auth, "send_verification_email", side_effect=lambda address, token: resent.append((address, token))):
            resend_result = auth.resend_verification(auth.EmailRequest(email=user.email), self.request, self.db)
        newest_token = auth._newest_token(self.db, EmailVerificationToken, user.id)
        self.assertEqual(resend_result["message"], "If the account exists and needs verification, a verification email has been sent.")
        self.assertEqual(len(resent), 1)
        self.assertIsNone(newest_token.used_at)
        self.assertNotEqual(newest_token.token_hash, resent[0][1])


if __name__ == "__main__":
    unittest.main()
