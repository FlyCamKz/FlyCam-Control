"""Authenticated encryption primitives for FlyCam dispatcher data at rest.

The on-disk envelope is intentionally versioned so that a certified
cryptographic provider can replace AES-GCM without changing the database API.
Keys are supplied by the operator and are never written to the database.
"""

from __future__ import annotations

import base64
import binascii
import argparse
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENVELOPE_PREFIX = "flycam:v1:"
NONCE_BYTES = 12
KEY_BYTES = 32


class EncryptionConfigurationError(ValueError):
    """Raised when encryption key configuration is unsafe or malformed."""


class DecryptionError(ValueError):
    """Raised when an encrypted value cannot be authenticated or decrypted."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (UnicodeEncodeError, binascii.Error) as error:
        raise EncryptionConfigurationError("invalid base64 encryption key") from error


def parse_keyring(specification: str) -> dict[str, bytes]:
    """Parse ``key-id:base64-key`` entries separated by commas.

    Key identifiers are persisted in ciphertext envelopes and therefore use a
    deliberately small portable alphabet. Every key is exactly 256 bits.
    """

    keyring: dict[str, bytes] = {}
    for raw_entry in specification.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        key_id, separator, encoded_key = entry.partition(":")
        if not separator or not key_id or not encoded_key:
            raise EncryptionConfigurationError(
                "FLYCAM_DATA_KEYS must contain key-id:base64-key entries"
            )
        if not key_id.replace("-", "").replace("_", "").isalnum() or len(key_id) > 64:
            raise EncryptionConfigurationError("invalid encryption key identifier")
        if key_id in keyring:
            raise EncryptionConfigurationError(f"duplicate encryption key identifier: {key_id}")
        key = _b64decode(encoded_key)
        if len(key) != KEY_BYTES:
            raise EncryptionConfigurationError("each encryption key must contain exactly 32 bytes")
        keyring[key_id] = key
    return keyring


@dataclass(frozen=True)
class DataEncryptor:
    """Versioned AES-256-GCM envelope with explicit key rotation support."""

    keys: dict[str, bytes]
    active_key_id: str

    def __post_init__(self) -> None:
        if not self.keys:
            raise EncryptionConfigurationError("at least one encryption key is required")
        if self.active_key_id not in self.keys:
            raise EncryptionConfigurationError("active encryption key is not present in keyring")
        for key in self.keys.values():
            if len(key) != KEY_BYTES:
                raise EncryptionConfigurationError("each encryption key must contain exactly 32 bytes")

    @property
    def provider_id(self) -> str:
        return "aes-256-gcm"

    def encrypt_text(self, plaintext: str, *, context: str) -> str:
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(self.keys[self.active_key_id]).encrypt(
            nonce,
            plaintext.encode("utf-8"),
            context.encode("utf-8"),
        )
        return f"{ENVELOPE_PREFIX}{self.active_key_id}:{_b64encode(nonce + ciphertext)}"

    def decrypt_text(self, value: str, *, context: str) -> str:
        # Plaintext compatibility allows a deployment to enable encryption
        # without making its existing database unreadable. New writes are
        # always encrypted; an offline migration can later rewrite old rows.
        if not value.startswith(ENVELOPE_PREFIX):
            return value
        remainder = value[len(ENVELOPE_PREFIX) :]
        key_id, separator, encoded_payload = remainder.partition(":")
        if not separator or key_id not in self.keys:
            raise DecryptionError("encrypted value references an unavailable key")
        try:
            payload = _b64decode(encoded_payload)
            if len(payload) <= NONCE_BYTES:
                raise DecryptionError("encrypted value is truncated")
            plaintext = AESGCM(self.keys[key_id]).decrypt(
                payload[:NONCE_BYTES],
                payload[NONCE_BYTES:],
                context.encode("utf-8"),
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, EncryptionConfigurationError) as error:
            raise DecryptionError("encrypted value failed authentication") from error


def encryptor_from_environment() -> DataEncryptor | None:
    specification = os.getenv("FLYCAM_DATA_KEYS", "").strip()
    active_key_id = os.getenv("FLYCAM_ACTIVE_DATA_KEY", "").strip()
    if not specification:
        if active_key_id:
            raise EncryptionConfigurationError(
                "FLYCAM_ACTIVE_DATA_KEY requires FLYCAM_DATA_KEYS"
            )
        return None
    keyring = parse_keyring(specification)
    if not active_key_id:
        if len(keyring) != 1:
            raise EncryptionConfigurationError(
                "FLYCAM_ACTIVE_DATA_KEY is required when multiple data keys are configured"
            )
        active_key_id = next(iter(keyring))
    return DataEncryptor(keyring, active_key_id)


def generate_key() -> str:
    """Return a URL-safe base64 encoded 256-bit key for operator tooling."""

    return _b64encode(os.urandom(KEY_BYTES))


def main() -> None:
    parser = argparse.ArgumentParser(description="FlyCam dispatcher data-key utility")
    parser.add_argument("--generate-key", action="store_true", help="print a new 256-bit key")
    args = parser.parse_args()
    if not args.generate_key:
        parser.error("--generate-key is required")
    print(generate_key())


if __name__ == "__main__":
    main()
