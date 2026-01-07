"""Encryption utilities for sensitive data (credentials, etc.)."""

import os
import logging
from typing import Optional

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_fernet: Optional[Fernet] = None


def get_fernet() -> Fernet:
    """Get Fernet encryption instance.

    Uses ENCRYPTION_KEY environment variable.
    """
    global _fernet

    if _fernet is None:
        key = os.getenv("ENCRYPTION_KEY")
        if not key:
            raise ValueError(
                "ENCRYPTION_KEY environment variable required. "
                "Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        _fernet = Fernet(key.encode())

    return _fernet


def encrypt_value(value: str) -> bytes:
    """Encrypt a string value.

    Args:
        value: Plain text string to encrypt

    Returns:
        Encrypted bytes
    """
    fernet = get_fernet()
    return fernet.encrypt(value.encode())


def decrypt_value(encrypted: bytes) -> str:
    """Decrypt an encrypted value.

    Args:
        encrypted: Encrypted bytes

    Returns:
        Decrypted plain text string
    """
    fernet = get_fernet()
    return fernet.decrypt(encrypted).decode()
