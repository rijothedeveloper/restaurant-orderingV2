"""Hashing for staff PINs, with scrypt from the standard library."""

import base64
import hashlib
import hmac
import secrets

_N, _R, _P = 2**14, 8, 1
_KEY_BYTES = 32


def hash_pin(pin: str) -> str:
    """A salted hash, stored as `scrypt$n$r$p$salt$hash`."""
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(pin.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_BYTES)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(key)}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        expected_key = base64.b64decode(expected)
        key = hashlib.scrypt(
            pin.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_key),
        )
    except ValueError:
        return False
    return hmac.compare_digest(key, expected_key)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
