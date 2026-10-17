"""Synchronous SMTP delivery for citizen authentication messages."""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from html import escape
from urllib.parse import urlencode


logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """Raised when configured SMTP delivery is unavailable."""

    SAFE_CLASSIFICATIONS = {
        "smtp_configuration",
        "smtp_authentication",
        "smtp_recipient_rejected",
        "smtp_rejected",
        "smtp_connection",
        "smtp_delivery",
    }

    def __init__(self, classification: str):
        super().__init__("Authentication email delivery failed")
        self.classification = classification if classification in self.SAFE_CLASSIFICATIONS else "smtp_delivery"


def _send_auth_email(recipient: str, subject: str, heading: str, link: str, expiry: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM_EMAIL", "").strip()
    if not host or not sender:
        logger.warning("AUTH email delivery status=failed type=smtp_configuration")
        raise EmailDeliveryError("smtp_configuration")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"BMC Civic Complaint Portal\n\n{heading}\n\nOpen this link: {link}\n\n{expiry}\n"
    )
    message.add_alternative(
        "<html><body><h1>BMC Civic Complaint Portal</h1>"
        f"<h2>{escape(heading)}</h2><p><a href=\"{escape(link, quote=True)}\">Continue</a></p>"
        f"<p>{escape(expiry)}</p></body></html>",
        subtype="html",
    )

    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        username = os.getenv("SMTP_USERNAME", "")
        password = os.getenv("SMTP_PASSWORD", "")
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if port == 587:
                smtp.starttls(context=ssl.create_default_context())
            if username:
                smtp.login(username, password)
            refused = smtp.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
    except Exception as error:
        # SMTP exception messages may contain server details; keep only a safe category.
        if isinstance(error, smtplib.SMTPAuthenticationError):
            classification = "smtp_authentication"
        elif isinstance(error, smtplib.SMTPRecipientsRefused):
            classification = "smtp_recipient_rejected"
        elif isinstance(error, smtplib.SMTPResponseException):
            classification = "smtp_rejected"
        elif isinstance(error, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError, OSError)):
            classification = "smtp_connection"
        else:
            classification = "smtp_delivery"
        logger.warning("AUTH email delivery status=failed type=%s", classification)
        raise EmailDeliveryError(classification) from error


def _auth_link(path: str, token: str) -> str:
    frontend_url = os.getenv("CITIZEN_FRONTEND_URL", "http://localhost:5174").rstrip("/")
    return f"{frontend_url}/{path}?{urlencode({'token': token})}"


def send_verification_email(recipient: str, token: str) -> None:
    link = _auth_link("verify-email", token)
    _send_auth_email(
        recipient,
        "Verify your BMC Civic Complaint Portal email",
        "Verify your email address",
        link,
        "This verification link expires in 24 hours.",
    )


def send_password_reset_email(recipient: str, token: str) -> None:
    link = _auth_link("reset-password", token)
    _send_auth_email(
        recipient,
        "Reset your BMC Civic Complaint Portal password",
        "Reset your password",
        link,
        "This password reset link expires in 45 minutes.",
    )
