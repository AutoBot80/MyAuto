import psycopg2
from psycopg2.extras import RealDictCursor

from .config import DATABASE_URL


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

