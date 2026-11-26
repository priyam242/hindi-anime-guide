import os
from pathlib import Path
from urllib.parse import quote, unquote

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")


def build_database_url(raw_url: str | None = None) -> str:
    """Return SQLAlchemy psycopg URL with safely encoded password credentials."""
    url = (raw_url or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return "sqlite:///./local_fallback.db"

    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]

    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        userinfo, hostpart = rest.rsplit("@", 1)
        if ":" in userinfo:
            user, password = userinfo.split(":", 1)
            encoded_user = quote(unquote(user), safe="")
            encoded_pw = quote(unquote(password), safe="")
            url = f"{scheme}://{encoded_user}:{encoded_pw}@{hostpart}"

    return url


DATABASE_URL = build_database_url()

_connect_args: dict = (
    {"prepare_threshold": None, "connect_timeout": 10}
    if DATABASE_URL.startswith("postgresql")
    else {"check_same_thread": False}
)

engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5 if DATABASE_URL.startswith("postgresql") else 1,
    max_overflow=5 if DATABASE_URL.startswith("postgresql") else 0,
    connect_args=_connect_args,
)


def ping_database() -> bool:
    """Check whether database connection succeeds cleanly."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
