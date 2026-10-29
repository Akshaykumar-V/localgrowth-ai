import base64
import hashlib
import hmac
import os
import re

from cryptography.fernet import Fernet


def normalize_phone(value: str) -> str:
    """Accept Indian national format or +91 E.164 and return canonical E.164."""
    digits = re.sub(r"[\s()-]", "", value)
    if re.fullmatch(r"[6-9]\d{9}", digits):
        digits = "+91" + digits
    elif re.fullmatch(r"\+91[6-9]\d{9}", digits):
        pass
    else:
        raise ValueError("Enter a valid Indian 10-digit phone number.")
    return digits


def mask_phone(e164: str) -> str:
    return "******" + e164[-4:]


def _encryption_key() -> bytes:
    value = os.getenv("PHONE_ENCRYPTION_KEY", "").strip()
    if not value:
        raise RuntimeError("PHONE_ENCRYPTION_KEY is not configured")
    try:
        key = base64.urlsafe_b64decode(value.encode("ascii"))
        if len(key) != 32:
            raise ValueError
        return value.encode("ascii")
    except (ValueError, UnicodeEncodeError):
        raise RuntimeError("PHONE_ENCRYPTION_KEY must be a URL-safe base64 encoded 32-byte key") from None


def _lookup_secret() -> bytes:
    value = os.getenv("PHONE_LOOKUP_SECRET", "").encode("utf-8")
    if len(value) < 32:
        raise RuntimeError("PHONE_LOOKUP_SECRET must contain at least 32 bytes")
    return value


def encrypt_phone(e164: str) -> str:
    return Fernet(_encryption_key()).encrypt(e164.encode("utf-8")).decode("ascii")


def decrypt_phone(ciphertext: str) -> str:
    return Fernet(_encryption_key()).decrypt(ciphertext.encode("ascii")).decode("utf-8")


def phone_lookup_hash(e164: str) -> str:
    return hmac.new(_lookup_secret(), e164.encode("ascii"), hashlib.sha256).hexdigest()


def validate_secrets() -> None:
    _encryption_key()
    _lookup_secret()
