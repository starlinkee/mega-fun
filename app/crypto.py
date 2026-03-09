"""
Symmetric encryption for sensitive settings (API keys, passwords).
Uses Fernet (AES-128-CBC + HMAC-SHA256).
The encryption key is auto-generated and stored in .secret_key file.
"""

import os
from cryptography.fernet import Fernet, InvalidToken

SECRET_KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".secret_key")


def _get_fernet() -> Fernet:
    """Load or generate the Fernet key."""
    if os.path.exists(SECRET_KEY_PATH):
        with open(SECRET_KEY_PATH, "rb") as f:
            key = f.read().strip()
    else:
        key = Fernet.generate_key()
        with open(SECRET_KEY_PATH, "wb") as f:
            f.write(key)
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """Encrypt a string, return base64-encoded ciphertext."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext back to string."""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # If decryption fails (e.g. old unencrypted value), return as-is
        return ciphertext
