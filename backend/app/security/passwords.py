"""Password verification against ``login_ref.pwd_hash`` (bcrypt or argon2)."""

from passlib.context import CryptContext
from passlib.exc import MissingBackendError

_pwd = CryptContext(schemes=["bcrypt", "argon2"], deprecated="auto")


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not plain_password or not password_hash:
        return False
    try:
        return _pwd.verify(plain_password, password_hash)
    except ValueError:
        return False


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password for storage in ``login_ref.pwd_hash`` (argon2)."""
    if not plain_password:
        raise ValueError("password is required")
    try:
        return _pwd.hash(plain_password, scheme="argon2")
    except MissingBackendError as exc:
        raise RuntimeError(
            "argon2 backend not available; run: pip install argon2-cffi"
        ) from exc
