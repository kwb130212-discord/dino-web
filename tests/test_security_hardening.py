import os
import unittest

os.environ.setdefault("SESSION_SECRET", "test-session-secret-with-enough-entropy")
os.environ.setdefault("RECOVERY_KEY_PEPPER", "test-recovery-pepper")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "test-token-key")

from security_hardening import _decrypt_token, _encrypt_token, _hash_recovery_key


class SecurityHardeningTests(unittest.TestCase):
    def test_token_round_trip(self):
        token = "discord-oauth-token-example"
        encrypted = _encrypt_token(token)
        self.assertNotEqual(encrypted, token)
        self.assertTrue(encrypted.startswith("fernet$"))
        self.assertEqual(_decrypt_token(encrypted), token)

    def test_recovery_key_is_one_way_and_deterministic(self):
        key = "REC-ABCD-1234"
        digest_a = _hash_recovery_key(key)
        digest_b = _hash_recovery_key(key)
        self.assertEqual(digest_a, digest_b)
        self.assertTrue(digest_a.startswith("hmac$"))
        self.assertNotIn(key, digest_a)

    def test_recovery_key_changes_with_input(self):
        self.assertNotEqual(_hash_recovery_key("REC-AAAA-1111"), _hash_recovery_key("REC-BBBB-2222"))


if __name__ == "__main__":
    unittest.main()
