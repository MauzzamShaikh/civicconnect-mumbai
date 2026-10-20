"""Isolated tests for Brevo authentication email delivery."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import email


class BrevoEmailTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "BREVO_API_KEY": "test-api-key",
            "BREVO_SENDER_EMAIL": "no-reply@example.com",
            "BREVO_SENDER_NAME": "Test Sender",
            "CITIZEN_FRONTEND_URL": "https://citizen.example.test/",
        }, clear=False)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    @patch("app.email.requests.post")
    def test_success_sends_verification_and_reset_links_to_configured_frontend(self, post):
        post.return_value = Mock(ok=True, status_code=201)

        email.send_verification_email("person@example.com", "token +/=")
        email.send_password_reset_email("person@example.com", "reset-token")

        self.assertEqual(post.call_count, 2)
        for call in post.call_args_list:
            self.assertEqual(call.args[0], "https://api.brevo.com/v3/smtp/email")
            self.assertEqual(call.kwargs["headers"]["api-key"], "test-api-key")
            self.assertEqual(call.kwargs["timeout"], 15)
            self.assertEqual(call.kwargs["json"]["sender"], {"name": "Test Sender", "email": "no-reply@example.com"})
            self.assertEqual(call.kwargs["json"]["to"], [{"email": "person@example.com"}])
        self.assertIn("https://citizen.example.test/verify-email?token=token+%2B%2F%3D", post.call_args_list[0].kwargs["json"]["htmlContent"])
        self.assertIn("https://citizen.example.test/reset-password?token=reset-token", post.call_args_list[1].kwargs["json"]["htmlContent"])

    @patch("app.email.requests.post")
    def test_partial_brevo_configuration_fails_without_http_request(self, post):
        os.environ["BREVO_SENDER_EMAIL"] = ""
        with self.assertRaises(email.EmailDeliveryError) as raised:
            email.send_verification_email("person@example.com", "token")
        self.assertEqual(raised.exception.classification, "brevo_configuration")
        post.assert_not_called()

    @patch("app.email.requests.post")
    def test_api_rejection_is_classified(self, post):
        post.return_value = Mock(ok=False, status_code=401)
        with self.assertRaises(email.EmailDeliveryError) as raised:
            email.send_verification_email("person@example.com", "token")
        self.assertEqual(raised.exception.classification, "brevo_rejected")

    @patch("app.email.requests.post", side_effect=email.requests.ConnectionError("private network detail"))
    def test_network_error_is_classified_without_leaking_details(self, post):
        with self.assertRaises(email.EmailDeliveryError) as raised:
            email.send_verification_email("person@example.com", "token")
        self.assertEqual(raised.exception.classification, "brevo_connection")
        self.assertNotIn("private network detail", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
