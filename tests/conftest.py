import os
import sys
import sqlite3
from typing import Generator
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, create_engine, select

# 1. Force test environment settings before any app modules or settings are loaded
os.environ["ENV"] = "testing"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

# Force empty credentials so no real .env secrets are loaded or used during tests
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_PORT"] = "587"
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["SMTP_FROM"] = "noreply@animeguide.com"
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["GOOGLE_CLIENT_SECRET"] = ""

from app.config import get_settings, BASE_DIR
# Clear cache to guarantee Settings reflects the test environment variables
get_settings.cache_clear()
test_settings = get_settings()

import aiosmtplib
import smtplib
from app.email_otp import EMAIL_OUTBOX

import app.db as app_db
import app.main as app_main
from app.main import app
from app.db import get_session, seed_data
from app.models import Article

# Resolve the absolute path of the development database
DEV_DB_PATH = os.path.abspath(str(BASE_DIR / "anime_hindi.db"))


def assert_not_dev_db(path_or_url: str):
    """Safety check: fails immediately if path matches the dev database."""
    if not path_or_url:
        return
    clean_path = path_or_url.replace("sqlite:///", "")
    resolved = os.path.abspath(clean_path)
    if resolved == DEV_DB_PATH:
        pytest.fail(
            f"CRITICAL SAFETY VIOLATION: Test attempted to use the development database at '{DEV_DB_PATH}'! "
            "All tests must run against isolated temporary databases."
        )


# Global Engine event listener to intercept and prevent any sqlite connection to anime_hindi.db
@event.listens_for(Engine, "connect")
def guard_engine_connect(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        try:
            for row in cursor.execute("PRAGMA database_list").fetchall():
                db_file = row[2]
                if db_file and os.path.abspath(db_file) == DEV_DB_PATH:
                    raise RuntimeError(
                        f"CRITICAL SAFETY VIOLATION: Database connection opened to dev DB '{DEV_DB_PATH}' during tests!"
                    )
        finally:
            cursor.close()


@pytest.fixture(autouse=True)
def isolate_database_per_test(tmp_path, monkeypatch):
    """
    Ensures every test runs against its OWN fresh temporary SQLite database in tmp_path.
    Overrides get_session, app.db.engine, app.main.engine, and test_pages.engine.
    """
    test_db_file = tmp_path / "test_isolated.db"
    assert_not_dev_db(str(test_db_file))

    test_engine = create_engine(
        f"sqlite:///{test_db_file}",
        echo=False,
        connect_args={"check_same_thread": False}
    )

    # Create all tables and seed standard categories and platforms
    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        seed_data(session)

    # Dependency override for FastAPI get_session
    def override_get_session() -> Generator[Session, None, None]:
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    # Monkeypatch engine in app.db, app.main, and app.auth
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_main, "engine", test_engine)
    if "app.auth" in sys.modules:
        monkeypatch.setattr(sys.modules["app.auth"], "engine", test_engine)
    monkeypatch.setattr(app_db, "settings", get_settings())

    # If test_pages is loaded, monkeypatch its engine reference as well
    if "tests.test_pages" in sys.modules:
        monkeypatch.setattr(sys.modules["tests.test_pages"], "engine", test_engine)

    # Monkeypatch init_db so any test calling init_db operates strictly on the test engine
    def safe_init_db():
        current_env = get_settings().env.lower()
        if current_env != "production":
            SQLModel.metadata.create_all(test_engine)
        with Session(test_engine) as session:
            seed_data(session)
            if current_env == "production":
                sample_article = session.exec(select(Article).where(Article.is_sample == True)).first()
                if sample_article:
                    raise RuntimeError("Production mode cannot start with sample articles in the database!")

    monkeypatch.setattr(app_db, "init_db", safe_init_db)
    if "tests.test_pages" in sys.modules:
        monkeypatch.setattr(sys.modules["tests.test_pages"], "init_db", safe_init_db)

    yield test_engine

    app.dependency_overrides.clear()
    test_engine.dispose()


@pytest.fixture(autouse=True)
def block_real_email_sending(monkeypatch):
    """
    Autouse fixture that monkeypatches the email-sending function (aiosmtplib/smtplib)
    so it can NEVER open a network connection during tests.
    Tests capture sent messages in an in-memory list (EMAIL_OUTBOX).
    """
    EMAIL_OUTBOX.clear()

    async def mock_aiosmtplib_send(message, *args, **kwargs):
        to_addr = message.get("To") if hasattr(message, "get") else None
        subject = message.get("Subject") if hasattr(message, "get") else None
        record = {
            "message": message,
            "to": to_addr,
            "subject": subject,
            "args": args,
            "kwargs": kwargs,
        }
        EMAIL_OUTBOX.append(record)
        return ("250", "OK")

    def mock_smtplib_connect(*args, **kwargs):
        raise RuntimeError(
            "CRITICAL SAFETY VIOLATION: smtplib attempted real network connection during tests!"
        )

    # Monkeypatch aiosmtplib.send
    monkeypatch.setattr(aiosmtplib, "send", mock_aiosmtplib_send)

    # Monkeypatch smtplib.SMTP.connect and SMTP_SSL.connect to prevent any synchronous socket creation
    monkeypatch.setattr(smtplib.SMTP, "connect", mock_smtplib_connect)
    monkeypatch.setattr(smtplib.SMTP_SSL, "connect", mock_smtplib_connect)

    yield EMAIL_OUTBOX


@pytest.fixture
def email_outbox():
    """Provides access to the in-memory email outbox for test assertions."""
    return EMAIL_OUTBOX

