import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import (
    Settings,
    get_settings,
    validate_secret_key,
    BASE_DIR,
    SECRET_KEY_PLACEHOLDER,
)
import app.db as app_db
from app.models import Category, Article, ArticleType
from app.main import app


def test_production_secret_key_validation():
    """production + empty/short/placeholder SECRET_KEY -> RuntimeError; production + strong key -> OK."""
    # 1. Empty secret_key in production
    s_empty = Settings(env="production", secret_key="")
    with pytest.raises(RuntimeError, match="SECRET_KEY must be set to a strong value in production"):
        validate_secret_key(s_empty)

    # 2. Short secret_key (< 32 characters) in production
    s_short = Settings(env="production", secret_key="short-secret-key-too-small")
    with pytest.raises(RuntimeError, match="SECRET_KEY must be set to a strong value in production"):
        validate_secret_key(s_short)

    # 3. Placeholder secret_key from .env.example
    s_placeholder = Settings(env="production", secret_key=SECRET_KEY_PLACEHOLDER)
    with pytest.raises(RuntimeError, match="SECRET_KEY must be set to a strong value in production"):
        validate_secret_key(s_placeholder)

    # 4. Strong key (>= 32 characters, not placeholder) -> OK
    strong_key = "a-very-long-and-secure-random-string-for-production-use"
    s_strong = Settings(env="production", secret_key=strong_key)
    validate_secret_key(s_strong)  # Should not raise


def test_default_settings_with_no_env_vars(monkeypatch):
    """default Settings() built with no env vars has env == 'production' and debug is False."""
    for key in ["ENV", "DEBUG", "SEED_SAMPLE_DATA", "SECRET_KEY", "HOST", "PORT"]:
        monkeypatch.delenv(key, raising=False)

    s = Settings(_env_file=None)
    assert s.env == "production"
    assert s.debug is False
    assert s.seed_sample_data is False


def test_docs_disabled_when_not_debug_and_secret_not_leaked(monkeypatch):
    """/docs returns 404 when not debug; rendered home HTML never contains the secret key value."""
    client = TestClient(app)
    settings = get_settings()

    # 1. When debug is False -> /docs returns 404
    monkeypatch.setattr(settings, "debug", False)
    resp_docs = client.get("/docs")
    assert resp_docs.status_code == 404

    # 2. When env is production (even if debug is True) -> /docs returns 404
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "env", "production")
    resp_docs_prod = client.get("/docs")
    assert resp_docs_prod.status_code == 404

    # 3. Rendered home HTML never contains the secret key value
    resp_home = client.get("/")
    assert resp_home.status_code == 200
    assert settings.secret_key != ""
    assert settings.secret_key not in resp_home.text


def test_get_settings_works_from_different_cwd(tmp_path, monkeypatch):
    """get_settings() works when cwd is a different directory (use monkeypatch.chdir(tmp_path))."""
    monkeypatch.chdir(tmp_path)
    s = Settings()
    assert s is not None

    # Check relative SQLite URL was resolved against BASE_DIR rather than tmp_path
    if s.database_url.startswith("sqlite:///"):
        path_part = s.database_url[len("sqlite:///"):]
        if path_part != ":memory:":
            assert str(tmp_path).lower() not in path_part.lower()
            assert str(BASE_DIR).lower() in path_part.lower()


def test_article_author_byline_conditional(monkeypatch):
    """article page shows no 'लेखक' line when AUTHOR_NAME is empty, and shows the name when set."""
    client = TestClient(app)
    settings = get_settings()

    # Seed an article into the isolated test DB
    with Session(app_db.engine) as session:
        cat = session.exec(select(Category)).first()
        art = Article(
            title="परीक्षण एनीमे गाइड",
            slug="test-author-guide",
            category_id=cat.id if cat else None,
            article_type=ArticleType.GUIDE,
            summary="मार्गदर्शिका परीक्षण सारांश",
            content_md="मार्गदर्शिका परीक्षण सामग्री",
            status="published",
            reading_time_minutes=3
        )
        session.add(art)
        session.commit()

    # 1. When AUTHOR_NAME is empty -> no "लेखक" line
    monkeypatch.setattr(settings, "author_name", "")
    resp_no_author = client.get("/article/test-author-guide")
    assert resp_no_author.status_code == 200
    assert "लेखक" not in resp_no_author.text

    # 2. When AUTHOR_NAME is set -> shows author line
    monkeypatch.setattr(settings, "author_name", "समीर वर्मा")
    resp_with_author = client.get("/article/test-author-guide")
    assert resp_with_author.status_code == 200
    assert "लेखक:" in resp_with_author.text
    assert "समीर वर्मा" in resp_with_author.text


def test_testing_env_clears_smtp_and_google_secrets():
    """Regardless of .env values, testing environment guarantees SMTP and Google secrets are empty strings."""
    s = Settings(env="testing")
    assert s.env == "testing"
    assert s.smtp_host == ""
    assert s.smtp_user == ""
    assert s.smtp_password == ""
    assert s.google_client_id == ""
    assert s.google_client_secret == ""

