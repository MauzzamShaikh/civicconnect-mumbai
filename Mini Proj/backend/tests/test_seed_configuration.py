import unittest
from pathlib import Path
from unittest.mock import patch

from app import seed


class SeedConfigurationTests(unittest.TestCase):
    def test_no_seed_mode_starts_without_opening_database_or_private_csv(self):
        with patch.object(seed, "SEED_ON_STARTUP", False), patch.object(
            seed, "SessionLocal", side_effect=AssertionError("no-seed mode must not query the database")
        ) as session_factory:
            seed.seed()
        session_factory.assert_not_called()

    def test_backend_defaults_to_no_private_dataset_seed(self):
        config_source = Path(__file__).resolve().parents[1] / "app" / "config.py"
        self.assertIn('os.getenv("SEED_ON_STARTUP", "false")', config_source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
