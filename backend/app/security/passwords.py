"""Password verification against ``login_ref.pwd_hash`` (bcrypt or argon2)."""

from passlib.context import CryptContext

_pwd = CryptContext(schemes=["bcrypt", "argon2"], deprecated="auto")


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not plain_password or not password_hash:
        return False
    try:
        return _pwd.verify(plain_password, password_hash)
    except ValueError:
        return False
