import sqlite3
from datetime import datetime, timezone
from typing import Generator
from sqlalchemy import event, text as sa_text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, create_engine, select
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command

from app.config import get_settings, BASE_DIR
from app.models import Article, Availability, Category, Platform, Admin1, User, User1, Comment, Bookmark, Notification, ArticleCategoryLink

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=300,
)


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enforces SQLite foreign keys and WAL journal mode."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def seed_data(session: Session) -> None:
    """Seeds official categories and platforms."""
    existing_cat = session.exec(select(Category)).first()
    if not existing_cat:
        cats = [
            Category(name="where-to-watch", display_name="कहाँ देखें", display_order=1),
            Category(name="recommendations", display_name="सिफारिशें", display_order=2),
            Category(name="beginners-guide", display_name="शुरुआती गाइड", display_order=3),
            Category(name="dub-vs-sub", display_name="डब vs सब", display_order=4),
        ]
        session.add_all(cats)
        session.commit()

    existing_platform = session.exec(select(Platform)).first()
    if not existing_platform:
        platforms = [
            Platform(name="Netflix", display_name="Netflix", official_url="https://www.netflix.com", display_order=1),
            Platform(name="Crunchyroll", display_name="Crunchyroll", official_url="https://www.crunchyroll.com", display_order=2),
            Platform(name="JioHotstar", display_name="JioHotstar", official_url="https://www.hotstar.com", display_order=3),
            Platform(name="Prime Video", display_name="Prime Video", official_url="https://www.primevideo.com", display_order=4),
        ]
        session.add_all(platforms)
        session.commit()

    from app.auth import get_password_hash
    existing_dummy = session.exec(select(User).where(User.email == "suraj@gmail.com")).first()
    if not existing_dummy:
        dummy_user = User(
            name="Suraj",
            username="suraj",
            email="suraj@gmail.com",
            hashed_password=get_password_hash("suraj123"),
            is_active=True,
            is_banned=False,
        )
        session.add(dummy_user)
        session.commit()

    if getattr(settings, "seed_sample_data", False):
        existing_sample = session.exec(select(Article).where(Article.slug == "namuna-anime-shuruat-guide")).first()
        if not existing_sample:
            sample_art = Article(
                title="[नमूना] एनीमे देखने की शुरुआत कैसे करें",
                slug="namuna-anime-shuruat-guide",
                meta_description="एनीमे की विभिन्न शैलियों को समझने और अपनी पहली एनीमे श्रृंखला चुनने के लिए संपूर्ण हिंदी गाइड।",
                content_md="## एनीमे का परिचय\n\nयह एक सामान्य परिचयात्मक लेख है जो शुरुआती दर्शकों को एनीमे की दुनिया से परिचित कराता है।\n\n### प्रमुख श्रेणियां\n\n- शोनेन (Shonen)\n- सीनेन (Seinen)\n- स्लाइस ऑफ लाइफ (Slice of Life)\n\nआधिकारिक स्ट्रीमिंग प्लेटफॉर्म्स पर हिंदी सबटाइटल्स और डबिंग उपलब्ध है।",
                category="beginners-guide",
                is_featured=True,
                reading_time_minutes=4,
                published_at=datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc),
            )
            sample_art._is_sample = True
            session.add(sample_art)
            session.commit()


def run_alembic_migrations(db_url: str | None = None) -> None:
    """Runs alembic upgrade head programmatically using BASE_DIR/alembic.ini."""
    ini_path = str(BASE_DIR / "alembic.ini")
    alembic_cfg = AlembicConfig(ini_path)
    alembic_cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    target_url = db_url or settings.database_url_unpooled or settings.database_url
    alembic_cfg.set_main_option("sqlalchemy.url", target_url)
    try:
        alembic_command.upgrade(alembic_cfg, "head")
    except Exception as e:
        print(f"Alembic migration warning/note: {e}")


def init_db() -> None:
    """Initializes tables via Alembic / SQLModel metadata and seeds categories/platforms."""
    run_alembic_migrations()
    try:
        with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                conn.execute(sa_text("ALTER TABLE article ADD COLUMN IF NOT EXISTS cover_image VARCHAR(500);"))
                conn.execute(sa_text("ALTER TABLE notifications1 ADD COLUMN IF NOT EXISTS user_id INTEGER;"))
                conn.execute(sa_text("ALTER TABLE notifications1 ALTER COLUMN admin_id DROP NOT NULL;"))
                conn.commit()
    except Exception as e:
        print(f"Database column compatibility note: {e}")

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_data(session)
        # Ensure any article with category column has a matching link in article_category_link
        try:
            articles_to_link = session.exec(select(Article).where(Article.category.is_not(None))).all()
            for a in articles_to_link:
                if a.id and a.category:
                    exists = session.exec(
                        select(ArticleCategoryLink).where(
                            ArticleCategoryLink.article_id == a.id,
                            ArticleCategoryLink.category_name == a.category
                        )
                    ).first()
                    if not exists:
                        session.add(ArticleCategoryLink(article_id=a.id, category_name=a.category))
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"Article category link backfill note: {e}")

        if getattr(settings, "env", "").lower() == "production":
            sample_article = session.exec(select(Article).where(Article.is_sample == True)).first()
            if sample_article:
                raise RuntimeError("Production mode cannot start with sample articles in the database!")
