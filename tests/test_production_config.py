import os
import unittest

from production_config import (
    CANONICAL_BASE_URL,
    auth_redirect_uri,
    dashboard_redirect_uri,
    trial_redirect_uri,
)


class ProductionConfigTests(unittest.TestCase):
    def test_canonical_domain(self):
        self.assertEqual(CANONICAL_BASE_URL, "https://dinobotservice.64bit.kr")
        self.assertEqual(dashboard_redirect_uri(), "https://dinobotservice.64bit.kr/dashboard/callback")
        self.assertEqual(auth_redirect_uri(), "https://dinobotservice.64bit.kr/auth/callback")
        self.assertEqual(trial_redirect_uri(), "https://dinobotservice.64bit.kr/trial/callback")

    def test_explicit_base_url_is_normalized(self):
        old = os.environ.get("DINO_PUBLIC_BASE_URL")
        try:
            os.environ["DINO_PUBLIC_BASE_URL"] = "https://example.test///"
            self.assertEqual(dashboard_redirect_uri(), "https://example.test/dashboard/callback")
        finally:
            if old is None:
                os.environ.pop("DINO_PUBLIC_BASE_URL", None)
            else:
                os.environ["DINO_PUBLIC_BASE_URL"] = old


if __name__ == "__main__":
    unittest.main()
