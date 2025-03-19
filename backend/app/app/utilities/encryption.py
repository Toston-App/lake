import hashlib
import hmac

from cryptography.fernet import Fernet

from app.core.config import settings


def get_fernet_key():
    """Get or create a Fernet key for encryption."""
    key = settings.ENCRYPTION_KEY

    if not key:
        raise ValueError("ENCRYPTION_KEY not set in environment variables")

    return Fernet(key.encode())

def encrypt_data(data: str) -> str:
    """Encrypt a string."""
    if not data:
        return data

    f = get_fernet_key()

    return f.encrypt(data.encode()).decode()

def decrypt_data(data: str) -> str:
    """Decrypt a string."""

    if not data:
        return data

    f = get_fernet_key()

    return f.decrypt(data.encode()).decode()

def hash_sha256(data: str) -> str:
    """Generate a SHA-256 hash with a fixed salt (for searching)."""
    key = settings.ENCRYPTION_KEY

    if not key:
        raise ValueError("ENCRYPTION_KEY not set in environment variables")

    return hmac.new(key.encode(), data.encode(), hashlib.sha256).hexdigest()
