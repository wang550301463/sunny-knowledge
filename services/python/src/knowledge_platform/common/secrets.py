"""Authenticated encryption for provider credentials, bound to their owning record."""
import base64
import binascii
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretBox:
    def __init__(self, key: str):
        try:
            raw = base64.b64decode(key, altchars=b'-_', validate=True)
        except (binascii.Error, ValueError):
            raise ValueError('Credential encryption key must be base64-encoded 32 bytes') from None
        if len(raw) != 32:
            raise ValueError('Credential encryption key must be base64-encoded 32 bytes')
        self._cipher = AESGCM(raw)

    def encrypt(self, value: str, context: str) -> str:
        if not context:
            raise ValueError('Credential owner context is required')
        nonce = secrets.token_bytes(12)
        encrypted = self._cipher.encrypt(nonce, value.encode(), context.encode())
        return 'v1:' + base64.urlsafe_b64encode(nonce + encrypted).decode()

    def decrypt(self, value: str, context: str) -> str:
        try:
            if not context or not value.startswith('v1:'):
                raise ValueError
            payload = base64.b64decode(value[3:], altchars=b'-_', validate=True)
            if len(payload) < 28:
                raise ValueError
            return self._cipher.decrypt(payload[:12], payload[12:], context.encode()).decode()
        except (InvalidTag, UnicodeDecodeError, binascii.Error, ValueError):
            raise ValueError('Credential could not be decrypted for this owner') from None