import os
import re
import sqlite3
from datetime import date
from typing import Generator
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session, select

from app.main import app
from app.config import get_settings
from app.db import get_session, seed_data, init_db
import app.db as app_db
from app.models import Category, Article, ArticleType, Platform, Availability, AvailabilityStatus, HindiDubStatus
from app.markdown_utils import render_markdown_with_toc


@pytest.fixture
def seeded_db(tmp_path):
    """Sets up a test SQLite DB with SEED_SAMPLE_DATA=True and returns (client, test_engine)."""
    db_file = tmp_path / "seeded_rules.db"
    test_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)

    # Seed official categories, platforms, and sample articles
    orig_seed = app_db.settings.seed_sample_data
    app_db.settings.seed_sample_data = True
    with Session(test_engine) as session:
        seed_data(session)

        # Add Article 1: Guide without any Availability rows
        cat = session.exec(select(Category).where(Category.slug == "beginners-guide")).first()
        art_no_avail = Article(
            title="शुरुआती पाठकों के लिए संपूर्ण मार्गदर्शिका",
            slug="shuruati-pathak-guide",
            category_id=cat.id if cat else None,
            article_type=ArticleType.GUIDE,
            summary="शुरुआती पाठकों के लिए एक व्यवस्थित मार्गदर्शिका बिना किसी उपलब्धता विवरण के।",
            content_md="## मार्गदर्शिका विवरण\n\nयह एक सामान्य मार्गदर्शिका लेख है।",
            is_featured=False,
            is_sample=False,
            status="published",
            reading_time_minutes=3
        )
        session.add(art_no_avail)
        session.commit()
        session.refresh(art_no_avail)

        # Add Article 2: Anime with two Availability rows (one with last_checked=NULL, one with valid date)
        cat_watch = session.exec(select(Category).where(Category.slug == "where-to-watch")).first()
        platform_netflix = session.exec(select(Platform).where(Platform.slug == "netflix")).first()
        platform_crunchy = session.exec(select(Platform).where(Platform.slug == "crunchyroll")).first()

        art_with_avail = Article(
            title="एनीमे उपलब्धता परीक्षण गाइड",
            slug="anime-availability-test-guide",
            anime_title_hi="परीक्षण एनीमे",
            anime_title_en="Test Anime",
            category_id=cat_watch.id if cat_watch else None,
            article_type=ArticleType.ANIME,
            summary="उपलब्धता पंक्तियों के सत्यापन हेतु विशेष परीक्षण लेख।",
            content_md="## परीक्षण एनीमे सारांश\n\nआधिकारिक स्ट्रीमिंग उपलब्धता विवरण।",
            is_featured=False,
            is_sample=False,
            status="published",
            reading_time_minutes=4
        )
        session.add(art_with_avail)
        session.commit()
        session.refresh(art_with_avail)

        if platform_netflix:
            avail_dated = Availability(
                article_id=art_with_avail.id,
                platform_id=platform_netflix.id,
                status=AvailabilityStatus.AVAILABLE,
                hindi_dub=HindiDubStatus.YES,
                last_checked=date(2026, 10, 18),
                note="सीजन 1 से 4 उपलब्ध"
            )
            session.add(avail_dated)

        if platform_crunchy:
            avail_null_date = Availability(
                article_id=art_with_avail.id,
                platform_id=platform_crunchy.id,
                status=AvailabilityStatus.AVAILABLE,
                hindi_dub=HindiDubStatus.NO,
                last_checked=None,
                note="अनिश्चित स्थिति"
            )
            session.add(avail_null_date)

        session.commit()

    app_db.settings.seed_sample_data = orig_seed

    def override_get_session() -> Generator[Session, None, None]:
        with Session(test_engine) as session:
            yield session

    orig_engine = app_db.engine
    app_db.engine = test_engine
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    try:
        yield client, test_engine
    finally:
        app.dependency_overrides.clear()
        app_db.engine = orig_engine
        test_engine.dispose()


@pytest.fixture
def empty_db(tmp_path):
    """Sets up a test SQLite DB with no articles or categories."""
    db_file = tmp_path / "empty_rules.db"
    test_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)

    def override_get_session() -> Generator[Session, None, None]:
        with Session(test_engine) as session:
            yield session

    orig_engine = app_db.engine
    app_db.engine = test_engine
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    try:
        yield client, test_engine
    finally:
        app.dependency_overrides.clear()
        app_db.engine = orig_engine
        test_engine.dispose()


# --- Rule 1: No <img tag on home, category and article pages ---
def test_no_img_tag_on_seeded_db_pages(seeded_db):
    seeded_client, _ = seeded_db

    # On seeded DB
    pages_to_check = [
        "/",
        "/category/where-to-watch",
        "/category/beginners-guide",
        "/article/shuruati-pathak-guide",
        "/article/anime-availability-test-guide",
        "/article/namuna-anime-shuruat-guide",
    ]
    for url in pages_to_check:
        resp = seeded_client.get(url)
        assert resp.status_code == 200, f"Page {url} returned {resp.status_code}"
        assert "<img" not in resp.text, f"Forbidden <img tag detected on page: {url}"


def test_no_img_tag_on_empty_db(empty_db):
    empty_client, _ = empty_db
    resp_empty = empty_client.get("/")
    assert resp_empty.status_code == 200
    assert "<img" not in resp_empty.text, "Forbidden <img tag detected on empty home page"


# --- Rule 2: Rendered HTML never contains "★", "/10", "rating" ---
def test_rendered_html_never_contains_ratings_on_seeded_db(seeded_db):
    seeded_client, _ = seeded_db

    urls = [
        "/",
        "/category/where-to-watch",
        "/article/shuruati-pathak-guide",
        "/article/anime-availability-test-guide",
        "/article/namuna-anime-shuruat-guide",
    ]
    for url in urls:
        resp = seeded_client.get(url)
        assert resp.status_code == 200
        html = resp.text
        assert "★" not in html, f"Star '★' symbol found in {url}"
        assert "/10" not in html, f"Score '/10' found in {url}"
        assert "rating" not in html.lower(), f"Word 'rating' found in {url}"


def test_rendered_html_never_contains_ratings_on_empty_db(empty_db):
    empty_client, _ = empty_db
    empty_html = empty_client.get("/").text
    assert "★" not in empty_html
    assert "/10" not in empty_html
    assert "rating" not in empty_html.lower()


# --- Rule 3: folders images/ and app/static/images/anime/ do not exist ---
def test_image_folders_do_not_exist():
    assert not os.path.exists("images"), "Folder 'images/' must not exist"
    assert not os.path.exists("app/static/images/anime"), "Folder 'app/static/images/anime/' must not exist"


# --- Rule 4: Hex/rgb()/hsl() color literals appear in no file under app/, tailwind.config.js, or templates,
# EXCEPT app/static/css/tokens.css (allow-list: one theme-color meta tag) ---
def test_no_color_literals_except_tokens_css():
    color_pattern = re.compile(r'#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)')
    theme_color_pattern = re.compile(r'<meta\s+name=["\']theme-color["\']\s+content=["\']#[0-9a-fA-F]{3,8}["\']')

    theme_color_count = 0
    violations = []

    # Check app/
    for root, dirs, files in os.walk("app"):
        for f in files:
            file_path = os.path.join(root, f)
            # Skip tokens.css, font binaries, compiled pyc
            if file_path.replace("\\", "/").endswith("app/static/css/tokens.css"):
                continue
            if f.endswith((".woff2", ".ttf", ".woff", ".pyc", ".db", ".db-wal", ".db-shm")):
                continue

            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                for line_num, line in enumerate(fh, 1):
                    # Check if line matches allow-listed theme-color meta tag
                    if "theme-color" in line and theme_color_pattern.search(line):
                        theme_color_count += 1
                        continue

                    matches = color_pattern.findall(line)
                    if matches:
                        violations.append(f"{file_path}:{line_num} -> {matches} in line: {line.strip()}")

    # Check tailwind.config.js
    if os.path.exists("tailwind.config.js"):
        with open("tailwind.config.js", "r", encoding="utf-8") as fh:
            for line_num, line in enumerate(fh, 1):
                matches = color_pattern.findall(line)
                if matches:
                    violations.append(f"tailwind.config.js:{line_num} -> {matches} in line: {line.strip()}")

    assert theme_color_count <= 1, f"Expected at most 1 theme-color meta tag, found {theme_color_count}"
    assert not violations, f"Color literals found in files:\n" + "\n".join(violations)


# --- Rule 5: No Tailwind/CSS color class strings inside any .py file ---
def test_no_tailwind_color_classes_in_py_files():
    color_class_pattern = re.compile(
        r'\b(?:bg|text|border|ring|fill|stroke|from|to|via)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|\[#)[a-zA-Z0-9_\-\/\[\]#]*'
    )
    violations = []
    for root, dirs, files in os.walk("app"):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as fh:
                    for line_num, line in enumerate(fh, 1):
                        matches = color_class_pattern.findall(line)
                        if matches:
                            violations.append(f"{path}:{line_num} -> {matches}")

    assert not violations, f"Tailwind color classes found in Python files:\n" + "\n".join(violations)


# --- Rule 6: An article with no Availability rows: no "अंतिम जाँच", no platform name, no "डब" badge ---
def test_article_with_no_availability_rows(seeded_db):
    client, engine = seeded_db
    resp = client.get("/article/shuruati-pathak-guide")
    assert resp.status_code == 200
    html = resp.text

    assert "अंतिम जाँच" not in html, "Article without availability should not render 'अंतिम जाँच'"
    assert "हिंदी डब:" not in html, "Article without availability should not render 'हिंदी डब:'"

    # Ensure no platform names appear in where-to-watch streaming section
    with Session(engine) as session:
        platforms = session.exec(select(Platform)).all()
        for p in platforms:
            # Platform should not appear inside where to watch section
            assert f">{p.name}<" not in html, f"Platform '{p.name}' unexpectedly found in article without availability"


# --- Rule 7: Availability row with last_checked=NULL renders nothing; with a date renders Hindi date and no English months ---
def test_availability_row_last_checked_rendering(seeded_db):
    client, _ = seeded_db
    resp = client.get("/article/anime-availability-test-guide")
    assert resp.status_code == 200
    html = resp.text

    # The row with valid date (2026-10-18) should render Hindi date
    assert "18 अक्टूबर 2026" in html, "Expected '18 अक्टूबर 2026' in rendered HTML"

    # English month names must NEVER appear
    english_months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        "January", "February", "March", "April", "June", "July", "August", "September",
        "October", "November", "December"
    ]
    for month in english_months:
        assert f" {month} " not in html, f"English month '{month}' found in rendered HTML"
        assert f"{month} 2026" not in html, f"English month '{month}' found with year in HTML"

    # The row with last_checked=NULL must not render any 'अंतिम जाँच' for itself
    # Crunchyroll row had last_checked=None, so Crunchyroll should NOT have an 'अंतिम जाँच' entry
    assert "Crunchyroll" not in html, "Row with last_checked=NULL should be excluded from valid availabilities"


# --- Rule 8: Every external <a href="http..."> in rendered article pages is in Platform.official_url values ---
def test_external_links_in_article_are_official_platform_urls(seeded_db):
    client, engine = seeded_db

    with Session(engine) as session:
        official_urls = set(session.exec(select(Platform.official_url)).all())

    link_pattern = re.compile(r'<a\s+[^>]*href=["\'](https?://[^"\']+)["\']')

    urls_to_test = [
        "/article/anime-availability-test-guide",
        "/article/shuruati-pathak-guide",
        "/article/namuna-anime-shuruat-guide",
    ]
    for page_url in urls_to_test:
        resp = client.get(page_url)
        assert resp.status_code == 200
        found_links = link_pattern.findall(resp.text)
        for link in found_links:
            # Strip trailing slash for normalization comparison
            normalized_link = link.rstrip("/")
            normalized_officials = {u.rstrip("/") for u in official_urls}
            assert normalized_link in normalized_officials, (
                f"External link '{link}' on {page_url} is not an official platform URL. Allowed: {official_urls}"
            )


# --- Rule 9: <title> contains "Hindi Anime Guide"; the repo contains no "AnimeHindi" ---
def test_title_and_repo_contains_no_animehindi(seeded_db):
    client, _ = seeded_db

    # Title verification
    home_resp = client.get("/")
    assert "Hindi Anime Guide" in home_resp.text
    assert "<title>" in home_resp.text
    assert "Hindi Anime Guide" in re.search(r'<title>(.*?)</title>', home_resp.text).group(1)

    article_resp = client.get("/article/shuruati-pathak-guide")
    assert "Hindi Anime Guide" in re.search(r'<title>(.*?)</title>', article_resp.text).group(1)

    # Repository scan: no "AnimeHindi" anywhere except tests/ and .git/
    ignore_dirs = {".git", "node_modules", "tests", ".pytest_cache", "__pycache__", ".venv"}
    ignore_exts = {".woff2", ".ttf", ".woff", ".pyc", ".db", ".db-wal", ".db-shm", ".exe", ".png", ".jpg", ".webp"}

    violations = []
    for root, dirs, files in os.walk("."):
        # Prune ignored directories
        dirs[:] = [d for d in dirs if d not in ignore_dirs]

        for f in files:
            if any(f.endswith(ext) for ext in ignore_exts):
                continue
            path = os.path.join(root, f)
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line_num, line in enumerate(fh, 1):
                    if "AnimeHindi" in line:
                        violations.append(f"{path}:{line_num} -> {line.strip()}")

    assert not violations, f"Occurrences of 'AnimeHindi' found in repository:\n" + "\n".join(violations)


# --- Rule 10: App refuses to start (RuntimeError) when ENV=production and a sample article exists ---
def test_production_fails_to_start_with_sample_article(tmp_path):
    test_db = tmp_path / "prod_safety.db"
    test_engine = create_engine(f"sqlite:///{test_db}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)

    # Add a sample article to the DB
    with Session(test_engine) as session:
        art = Article(
            title="नमूना लेख",
            slug="namuna-test-article",
            summary="उत्पादन सुरक्षा परीक्षण सारांश",
            content_md="नमूना सामग्री",
            is_sample=True,
            article_type=ArticleType.GUIDE
        )
        session.add(art)
        session.commit()

    orig_engine = app_db.engine
    orig_env = app_db.settings.env
    try:
        app_db.engine = test_engine
        app_db.settings.env = "production"

        with pytest.raises(RuntimeError) as exc_info:
            init_db()
        assert "Production mode cannot start with sample articles" in str(exc_info.value)
    finally:
        app_db.engine = orig_engine
        app_db.settings.env = orig_env
        test_engine.dispose()


# --- Rule 11: Sample articles include noindex ---
def test_sample_articles_include_noindex(seeded_db):
    client, _ = seeded_db
    # Seeded sample article
    resp = client.get("/article/namuna-anime-shuruat-guide")
    assert resp.status_code == 200
    assert '<meta name="robots" content="noindex,nofollow">' in resp.text, (
        "Sample article must contain <meta name='robots' content='noindex,nofollow'>"
    )

    # Non-sample article must NOT have noindex
    resp_regular = client.get("/article/shuruati-pathak-guide")
    assert resp_regular.status_code == 200
    assert '<meta name="robots" content="noindex,nofollow">' not in resp_regular.text


# --- Rule 12: Empty DB shows clean Hindi message without hero ---
def test_empty_db_shows_clean_message_without_hero(empty_db):
    client, _ = empty_db
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    assert "पहले लेख जल्द आ रहे हैं" in html, "Empty state message missing on home page"
    assert "hero" not in html, "Hero section should not appear on empty home page"
    assert "Hindi Anime Guide" in html


# --- Rule 13: Dev database is never modified by tests and contains only [नमूना] articles ---
def test_dev_database_unmodified_by_test_suite():
    dev_db_path = os.path.abspath("anime_hindi.db")
    assert os.path.exists(dev_db_path), f"Dev database file not found at {dev_db_path}"

    initial_mtime = os.path.getmtime(dev_db_path)
    initial_size = os.path.getsize(dev_db_path)

    # Perform requests using test client
    test_client = TestClient(app)
    home_resp = test_client.get("/")
    assert home_resp.status_code == 200

    # Test slugs that were historically written to the dev database during tests
    test_slugs = [
        "test-sample-guide",
        "auto-update-test",
        "test-anime-no-avail",
        "test-anime-with-avail",
        "test-anime-null-date",
        "prod-check-sample",
        "shuruati-pathak-guide",
        "anime-availability-test-guide",
        "homepage-test-guide",
        "temp-empty-state-test",
    ]

    # Query the dev DB in read-only mode via raw sqlite3 (bypassing SQLAlchemy engine guard)
    conn = sqlite3.connect(f"file:{dev_db_path}?mode=ro", uri=True)
    cursor = conn.cursor()
    cursor.execute("SELECT slug, is_sample FROM articles")
    rows = cursor.fetchall()
    conn.close()

    existing_slugs = [r[0] for r in rows]

    # 1. Assert none of the test slugs exist in the dev database
    for slug in test_slugs:
        assert slug not in existing_slugs, (
            f"Dev database was polluted: test slug '{slug}' found in {dev_db_path}"
        )

    # 2. Assert every article in the dev database is a genuine [नमूना] sample
    for slug, is_sample in rows:
        assert is_sample == 1, f"Dev DB article '{slug}' has is_sample != 1"
        assert slug.startswith("namuna-"), f"Dev DB article '{slug}' does not start with 'namuna-'"

    # 3. Assert exactly the 3 initial sample articles exist
    assert len(rows) == 3, f"Expected exactly 3 sample articles in dev DB, found {len(rows)}"

    # 4. Assert file metadata (size and modification time) was not changed by tests
    final_mtime = os.path.getmtime(dev_db_path)
    final_size = os.path.getsize(dev_db_path)
    assert final_size == initial_size, "Dev database file size changed during test run"
    assert final_mtime == initial_mtime, "Dev database file mtime changed during test run"


# --- Rule 14: Footer contains 5 links with Hindi labels, no Latin word 'anime',
#     and /editorial-policy returns 200 as a real full page (no noindex) ---
def test_footer_redesign_and_editorial_policy(seeded_db):
    client, _ = seeded_db

    # Test home page footer
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    footer_match = re.search(r'<footer\b[^>]*>(.*?)</footer>', html, re.DOTALL)
    assert footer_match, "Footer element not found on home page"
    footer_html = footer_match.group(1)

    # 1. Footer contains the 5 links with Hindi labels
    expected_links = [
        ('<a href="/about" data-i18n="about_us">हमारे बारे में</a>', "/about", "हमारे बारे में"),
        ('<a href="/editorial-policy" data-i18n="editorial_policy">संपादकीय नीति</a>', "/editorial-policy", "संपादकीय नीति"),
        ('<a href="/contact" data-i18n="contact">संपर्क</a>', "/contact", "संपर्क"),
        ('<a href="/privacy" data-i18n="privacy">गोपनीयता नीति</a>', "/privacy", "गोपनीयता नीति"),
        ('<a href="/disclaimer" data-i18n="disclaimer">अस्वीकरण</a>', "/disclaimer", "अस्वीकरण"),
    ]
    for full_tag, href, label in expected_links:
        assert f'href="{href}"' in footer_html, f"Footer missing link href='{href}'"
        assert label in footer_html, f"Footer missing Hindi label '{label}'"
        assert full_tag in footer_html, f"Footer missing full link tag '{full_tag}'"

    # 2. Footer HTML does not contain the Latin word "anime"
    assert not re.search(r'\banime\b', footer_html), "Footer HTML contains the Latin word 'anime'"
    assert "anime" not in footer_html, "Footer HTML contains lowercase 'anime'"

    # 3. /editorial-policy is now a real page: 200, NO noindex, real content
    ed_resp = client.get("/editorial-policy")
    assert ed_resp.status_code == 200
    assert '<meta name="robots" content="noindex,nofollow">' not in ed_resp.text, (
        "/editorial-policy should not have noindex — it is now a real published page"
    )
    assert "संपादकीय नीति" in ed_resp.text
    assert "यह पेज जल्द प्रकाशित होगा" not in ed_resp.text, (
        "/editorial-policy still shows coming-soon text"
    )
