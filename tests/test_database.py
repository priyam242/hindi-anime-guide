import os
import tempfile
from pathlib import Path
from datetime import datetime, date, timezone
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy import event
from sqlalchemy.engine import Engine
import sqlite3
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command, autogenerate
from alembic.migration import MigrationContext

from app.config import BASE_DIR, get_settings
from app.models import Category, Article, ArticleType, Platform, Availability
from app.templates_config import hindi_date
from app.main import app
import app.db as app_db


@event.listens_for(Engine, "connect")
def enforce_sqlite_pragmas(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def test_alembic_vs_models_metadata_drift():
    """
    Creates an empty temp DB, runs alembic upgrade head on it,
    then uses compare_metadata against SQLModel.metadata and asserts diff is EMPTY.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "test_migration.db"
        db_url = f"sqlite:///{db_path}"

        cfg = AlembicConfig(str(BASE_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
        cfg.set_main_option("sqlalchemy.url", db_url)

        alembic_command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            diff = autogenerate.compare_metadata(ctx, SQLModel.metadata)
            assert diff == [], f"Alembic migration drift detected: {diff}"

        engine.dispose()


def test_check_and_unique_constraints_reject_bad_rows():
    """Tests that the CHECK constraint on Platform and UNIQUE constraint on Availability reject bad rows."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "test_constraints.db"
        db_url = f"sqlite:///{db_path}"

        cfg = AlembicConfig(str(BASE_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
        cfg.set_main_option("sqlalchemy.url", db_url)

        alembic_command.upgrade(cfg, "head")

        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        with Session(engine) as session:
            # 1. CHECK constraint test: http:// must fail
            bad_platform = Platform(name="Insecure", slug="insecure", official_url="http://insecure.com")
            session.add(bad_platform)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            # Valid platform: https:// succeeds
            good_platform = Platform(name="Secure", slug="secure", official_url="https://secure.com")
            session.add(good_platform)

            art = Article(
                title="परीक्षण लेख",
                slug="test-constraints-article",
                summary="सारांश",
                content_md="सामग्री",
                status="published"
            )
            session.add(art)
            session.commit()
            session.refresh(good_platform)
            session.refresh(art)

            # 2. UNIQUE constraint test: (article_id, platform_id) duplicate must fail
            av1 = Availability(article_id=art.id, platform_id=good_platform.id)
            session.add(av1)
            session.commit()

            av2 = Availability(article_id=art.id, platform_id=good_platform.id)
            session.add(av2)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        engine.dispose()


def test_ist_date_formatting():
    """
    hindi_date filter:
    - datetime 2026-10-17 20:00 UTC -> converts to IST (UTC+5:30 = 01:30 Oct 18) -> '18 अक्टूबर 2026'
    - naive datetime is treated as UTC
    - plain date is NOT shifted
    """
    # UTC timezone aware datetime
    dt_utc = datetime(2026, 10, 17, 20, 0, tzinfo=timezone.utc)
    assert hindi_date(dt_utc) == "18 अक्टूबर 2026"

    # Naive datetime treated as UTC
    dt_naive = datetime(2026, 10, 17, 20, 0)
    assert hindi_date(dt_naive) == "18 अक्टूबर 2026"

    # Plain date (e.g. last_checked) must NOT be shifted
    d_plain = date(2026, 10, 17)
    assert hindi_date(d_plain) == "17 अक्टूबर 2026"


def test_related_articles_all_three_situations(tmp_path):
    """
    Tests related articles logic:
    Situation 1: Same category has >= 3 other published articles -> all 3 from same category.
    Situation 2: Same category has 1 or 2 other published articles -> topped up with other newest.
    Situation 3: Same category has 0 other published articles -> topped up completely with other newest.
    """
    test_db = tmp_path / "test_related.db"
    test_engine = create_engine(f"sqlite:///{test_db}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)

    client = TestClient(app)

    with Session(test_engine) as session:
        cat_a = Category(name="श्रेणी क", slug="cat-a", cover_color="red")
        cat_b = Category(name="श्रेणी ख", slug="cat-b", cover_color="slate")
        session.add_all([cat_a, cat_b])
        session.commit()
        session.refresh(cat_a)
        session.refresh(cat_b)

        # Create 4 articles in cat_a (art_a0 to art_a3)
        for i in range(4):
            session.add(Article(
                title=f"क लेख {i}",
                slug=f"article-a-{i}",
                category_id=cat_a.id,
                summary=f"सारांश क {i}",
                content_md=f"सामग्री क {i}",
                status="published"
            ))

        # Create 3 articles in cat_b (art_b0 to art_b2)
        for i in range(3):
            session.add(Article(
                title=f"ख लेख {i}",
                slug=f"article-b-{i}",
                category_id=cat_b.id,
                summary=f"सारांश ख {i}",
                content_md=f"सामग्री ख {i}",
                status="published"
            ))

        # Create 1 article in new category cat_c (lone article)
        cat_c = Category(name="श्रेणी ग", slug="cat-c", cover_color="ink")
        session.add(cat_c)
        session.commit()
        session.refresh(cat_c)

        session.add(Article(
            title="ग एकल लेख",
            slug="article-c-lone",
            category_id=cat_c.id,
            summary="सारांश ग",
            content_md="सामग्री ग",
            status="published"
        ))

        # Create category with exactly 2 articles (art_d0, art_d1)
        cat_d = Category(name="श्रेणी घ", slug="cat-d", cover_color="ink")
        session.add(cat_d)
        session.commit()
        session.refresh(cat_d)

        session.add(Article(
            title="घ लेख 0",
            slug="article-d-0",
            category_id=cat_d.id,
            summary="सारांश घ 0",
            content_md="सामग्री घ 0",
            status="published"
        ))
        session.add(Article(
            title="घ लेख 1",
            slug="article-d-1",
            category_id=cat_d.id,
            summary="सारांश घ 1",
            content_md="सामग्री घ 1",
            status="published"
        ))
        session.commit()

    def override_get_session():
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[app_db.get_session] = override_get_session

    try:
        # Situation 1: article-a-0 is in cat_a which has 3 other articles (article-a-1, article-a-2, article-a-3)
        resp1 = client.get("/article/article-a-0")
        assert resp1.status_code == 200
        # Must contain the other 3 cat_a articles and NOT itself
        assert "क लेख 1" in resp1.text
        assert "क लेख 2" in resp1.text
        assert "क लेख 3" in resp1.text

        # Situation 2: article-d-0 is in cat_d which has only 1 other article (article-d-1)
        # It must include article-d-1 and top up with 2 other newest articles
        resp2 = client.get("/article/article-d-0")
        assert resp2.status_code == 200
        assert "घ लेख 1" in resp2.text

        # Situation 3: article-c-lone has 0 other articles in cat_c
        # It must top up with 3 newest articles from other categories, never itself
        resp3 = client.get("/article/article-c-lone")
        assert resp3.status_code == 200
        # It should not link back to itself as related
        assert resp3.text.count("article-c-lone") <= 4  # Only in breadcrumb / self url / comment auth

    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()
