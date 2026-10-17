"""Provision the fixed set of BMC staff accounts from a trusted operator shell.

Run with ``python -m app.provision_bmc_staff`` inside the backend environment.
Generated passwords are printed only after the complete transaction commits.
"""

import secrets
import sys
import argparse

from sqlalchemy import select

from .auth import password_hasher
from .db import SessionLocal
from .models import User, utc_now


STAFF_IDENTITIES = tuple(
    (f"BMC Staff {number}", f"bmc.staff{number}@local.bmc")
    for number in range(1, 6)
)


class ProvisioningConflict(RuntimeError):
    """The configured staff identities conflict with existing user records."""


def provision_staff(session_factory=SessionLocal):
    """Create all five staff accounts, or safely leave the database untouched.

    Returns the newly generated credentials only when this call created the
    accounts. A fully provisioned set is an idempotent no-op.
    """
    session = session_factory()
    credentials = []
    try:
        with session.begin():
            emails = [email for _name, email in STAFF_IDENTITIES]
            existing = {
                user.email: user
                for user in session.scalars(select(User).where(User.email.in_(emails))).all()
            }
            if existing:
                if len(existing) == len(STAFF_IDENTITIES) and all(
                    existing[email].role == "admin"
                    and existing[email].is_active
                    and existing[email].email_verified
                    for email in emails
                ):
                    return []
                conflicts = ", ".join(sorted(existing))
                raise ProvisioningConflict(
                    "Staff provisioning stopped: the configured identities are only "
                    f"partially present or conflict with existing roles ({conflicts}). "
                    "No accounts were changed. Resolve this set through the trusted "
                    "operator process before retrying."
                )

            now = utc_now()
            for name, email in STAFF_IDENTITIES:
                password = secrets.token_urlsafe(36)
                session.add(
                    User(
                        name=name,
                        email=email,
                        password_hash=password_hasher.hash(password),
                        role="admin",
                        email_verified=True,
                        email_verified_at=now,
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
                credentials.append((name, email, password))
        return credentials
    finally:
        session.close()


def rotate_staff_password(email: str, session_factory=SessionLocal) -> str:
    """Set a fresh random password for one existing administrator account."""
    normalized_email = email.strip().lower()
    session = session_factory()
    password = secrets.token_urlsafe(36)
    try:
        with session.begin():
            user = session.scalar(select(User).where(User.email == normalized_email))
            if not user or user.role != "admin" or not user.is_active:
                raise ProvisioningConflict("The requested active BMC administrator account was not found.")
            user.password_hash = password_hasher.hash(password)
            user.updated_at = utc_now()
        return password
    finally:
        session.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Provision or rotate BMC staff credentials.")
    parser.add_argument("--rotate", metavar="EMAIL", help="issue a new random password for an existing active admin")
    args = parser.parse_args(argv)
    try:
        if args.rotate:
            rotated_password = rotate_staff_password(args.rotate)
            print(f"Rotated password for {args.rotate.strip().lower()}. Save it securely; it will not be shown again.")
            print(rotated_password)
            return 0
        credentials = provision_staff()
    except Exception as error:
        # These errors describe operator actions only; never print credentials
        # or SQL/driver exception details.
        safe_message = str(error) if isinstance(error, ProvisioningConflict) else "Database operation failed."
        print(f"BMC staff provisioning failed: {safe_message}", file=sys.stderr)
        return 1

    if not credentials:
        print("All five BMC staff accounts already exist as administrators; no changes made.")
        return 0

    print("Created five BMC staff accounts. Save and distribute these credentials securely; they will not be shown again.")
    for name, email, password in credentials:
        print(f"{name} | {email} | {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
