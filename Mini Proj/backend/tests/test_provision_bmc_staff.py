"""Safe and idempotent BMC staff provisioning tests."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth
from app.db import Base
from app.models import User
from app import provision_bmc_staff
from app.provision_bmc_staff import (
    ProvisioningConflict,
    STAFF_IDENTITIES,
    provision_staff,
    rotate_staff_password,
)


class BmcStaffProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine, tables=[User.__table__])
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._old_secret = os.environ.get("JWT_SECRET_KEY")
        os.environ["JWT_SECRET_KEY"] = "test-only-secret-key-that-is-at-least-32-bytes"
        auth._LOGIN_FAILURES.clear()
        auth._RATE_EVENTS.clear()
        self.request = Request({
            "type": "http", "method": "POST", "path": "/auth/login", "headers": [],
            "client": ("provision-test", 1234), "server": ("test", 80), "scheme": "http",
        })

    def tearDown(self):
        self.engine.dispose()
        if self._old_secret is None:
            os.environ.pop("JWT_SECRET_KEY", None)
        else:
            os.environ["JWT_SECRET_KEY"] = self._old_secret

    def test_provisions_five_unique_admins_that_can_login_and_rerun_preserves_passwords(self):
        credentials = provision_staff(self.Session)
        self.assertEqual(len(credentials), 5)
        self.assertEqual(len({password for _name, _email, password in credentials}), 5)
        self.assertEqual({email for _name, email, _password in credentials}, {email for _name, email in STAFF_IDENTITIES})

        with self.Session() as db:
            original_hashes = {user.email: user.password_hash for user in db.scalars(select(User)).all()}
            for _name, email, password in credentials:
                user = db.scalar(select(User).where(User.email == email))
                self.assertEqual(user.role, "admin")
                self.assertTrue(user.is_active)
                self.assertTrue(user.email_verified)
                self.assertNotEqual(user.password_hash, password)
                result = auth.login(auth.LoginRequest(email=email, password=password), self.request, db)
                self.assertEqual(result["user"]["role"], "admin")

        self.assertEqual(provision_staff(self.Session), [])
        with self.Session() as db:
            self.assertEqual(
                {user.email: user.password_hash for user in db.scalars(select(User)).all()},
                original_hashes,
            )
            for _name, email, password in credentials:
                result = auth.login(auth.LoginRequest(email=email, password=password), self.request, db)
                self.assertEqual(result["user"]["email"], email)

    def test_partial_or_non_admin_identity_set_is_left_unchanged(self):
        with self.Session() as db:
            user = User(
                name="Existing Citizen", email=STAFF_IDENTITIES[0][1],
                password_hash=auth.password_hasher.hash("original-password"), role="citizen",
                email_verified=True, is_active=True,
            )
            db.add(user)
            db.commit()
            original_hash = user.password_hash

        with self.assertRaises(ProvisioningConflict):
            provision_staff(self.Session)

        with self.Session() as db:
            users = db.scalars(select(User)).all()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].role, "citizen")
            self.assertEqual(users[0].password_hash, original_hash)

    def test_operator_rotation_sets_new_password_without_needing_old_password(self):
        provision_staff(self.Session)
        email = STAFF_IDENTITIES[0][1]
        password = rotate_staff_password(email, self.Session)
        with self.Session() as db:
            result = auth.login(auth.LoginRequest(email=email, password=password), self.request, db)
            self.assertEqual(result["user"]["role"], "admin")
            with self.assertRaises(HTTPException) as caught:
                auth.login(auth.LoginRequest(email=email, password="not-the-new-password"), self.request, db)
            self.assertEqual(caught.exception.status_code, 401)

    def test_mid_provision_failure_rolls_back_entire_set(self):
        original_hash = provision_bmc_staff.password_hasher.hash
        calls = 0

        def fail_on_third(password):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("simulated hashing failure")
            return original_hash(password)

        with patch.object(provision_bmc_staff.password_hasher, "hash", side_effect=fail_on_third):
            with self.assertRaises(RuntimeError):
                provision_staff(self.Session)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(User.id)), None)


if __name__ == "__main__":
    unittest.main()
