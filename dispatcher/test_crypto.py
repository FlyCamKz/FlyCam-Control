from __future__ import annotations

import base64
import os
import unittest
from unittest.mock import patch

try:
    from .crypto import (
        DataEncryptor,
        DecryptionError,
        EncryptionConfigurationError,
        encryptor_from_environment,
        parse_keyring,
    )
except ImportError:
    from crypto import (
        DataEncryptor,
        DecryptionError,
        EncryptionConfigurationError,
        encryptor_from_environment,
        parse_keyring,
    )


def encoded_key(byte: int) -> str:
    return base64.urlsafe_b64encode(bytes([byte]) * 32).decode("ascii")


class DataEncryptorTest(unittest.TestCase):
    def test_round_trip_uses_versioned_authenticated_envelope(self) -> None:
        encryptor = DataEncryptor({"primary": bytes(range(32))}, "primary")
        encrypted = encryptor.encrypt_text("секретная телеметрия", context="telemetry.payload")
        self.assertTrue(encrypted.startswith("flycam:v1:primary:"))
        self.assertNotIn("телеметрия", encrypted)
        self.assertEqual(
            encryptor.decrypt_text(encrypted, context="telemetry.payload"),
            "секретная телеметрия",
        )

    def test_ciphertext_tampering_is_detected(self) -> None:
        encryptor = DataEncryptor({"primary": bytes(range(32))}, "primary")
        encrypted = encryptor.encrypt_text("payload", context="events.payload")
        replacement = "A" if encrypted[-1] != "A" else "B"
        with self.assertRaises(DecryptionError):
            encryptor.decrypt_text(encrypted[:-1] + replacement, context="events.payload")

    def test_context_prevents_ciphertext_substitution(self) -> None:
        encryptor = DataEncryptor({"primary": bytes(range(32))}, "primary")
        encrypted = encryptor.encrypt_text("payload", context="events.payload")
        with self.assertRaises(DecryptionError):
            encryptor.decrypt_text(encrypted, context="telemetry.payload")

    def test_old_key_remains_available_during_rotation(self) -> None:
        old = DataEncryptor({"old": bytes([1]) * 32}, "old")
        encrypted = old.encrypt_text("payload", context="telemetry.payload")
        rotated = DataEncryptor(
            {"old": bytes([1]) * 32, "current": bytes([2]) * 32}, "current"
        )
        self.assertEqual(rotated.decrypt_text(encrypted, context="telemetry.payload"), "payload")
        self.assertTrue(
            rotated.encrypt_text("new", context="telemetry.payload").startswith(
                "flycam:v1:current:"
            )
        )

    def test_plaintext_rows_remain_readable_after_enabling_encryption(self) -> None:
        encryptor = DataEncryptor({"primary": bytes(range(32))}, "primary")
        self.assertEqual(encryptor.decrypt_text("legacy", context="telemetry.payload"), "legacy")

    def test_configuration_requires_unique_valid_256_bit_keys(self) -> None:
        with self.assertRaises(EncryptionConfigurationError):
            parse_keyring("primary:" + base64.urlsafe_b64encode(b"short").decode("ascii"))
        with self.assertRaises(EncryptionConfigurationError):
            parse_keyring(f"primary:{encoded_key(1)},primary:{encoded_key(2)}")
        with self.assertRaises(EncryptionConfigurationError):
            parse_keyring(f"ключ:{encoded_key(1)}")
        with self.assertRaises(EncryptionConfigurationError):
            parse_keyring("primary:not-base64!")

    def test_environment_requires_active_id_for_multiple_keys(self) -> None:
        environment = {
            "FLYCAM_DATA_KEYS": f"old:{encoded_key(1)},current:{encoded_key(2)}",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(EncryptionConfigurationError):
                encryptor_from_environment()


if __name__ == "__main__":
    unittest.main()
