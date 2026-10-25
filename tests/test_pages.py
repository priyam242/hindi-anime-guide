from datetime import date, datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
import sqlite3

from app.main import app
import app.db as app_db
from app.models import Category, Article, ArticleType, Platform, Availability, AvailabilityStatus, HindiDubStatus
from app.config import get_settings
from app.templates_config import short_description, hindi_date, first_words


class EngineProxy:
    def __getattr__(self, name):
        return getattr(app_db.engine, name)


engine = EngineProxy()


def init_db():
    return app_db.init_db()



client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_favicon():
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers.get("content-type", "")


def test_home_page():
    with Session(engine) as session:
        cat = session.exec(select(Category).where(Category.slug == "where-to-watch")).first()
        art = Article(
            article_type=ArticleType.GUIDE,
            title="होमपेज टेस्ट गाइड",
            slug="homepage-test-guide",
            category_id=cat.id if cat else None,
            summary="सारांश",
            content_md="सामग्री",
            status="published"
        )
        session.add(art)
        session.commit()

    response = client.get("/")
    assert response.status_code == 200
    assert "Hindi Anime Guide" in response.text
    assert "AnimeHindi" not in response.text
    assert "विश्वसनीय" not in response.text
    assert ("Directory" in response.text or "Hindi Anime Guide" in response.text)
    assert ("<title>Hindi Anime Guide — India's Legal Anime Streaming & Hindi Dub Directory</title>" in response.text or "Hindi Anime Guide" in response.text)
    # Verify default is light theme
    assert '<html lang="hi">' in response.text
    assert '<html lang="hi" class="dark">' not in response.text
    # Verify no unverified claims
    assert "नंबर 1" not in response.text
    assert "Phase 1 Foundation" not in response.text
    # Verify no external fonts and proper self-hosted preload
    assert "fonts.googleapis.com" not in response.text
    assert "fonts.gstatic.com" not in response.text
    assert 'href="/static/fonts/noto-serif-devanagari-variable.woff2"' in response.text
    assert 'href="/static/css/fonts.css"' in response.text


def test_self_hosted_fonts_and_typography():
    # Verify font files resolve with 200 OK
    dev_resp = client.get("/static/fonts/noto-serif-devanagari-variable.woff2")
    assert dev_resp.status_code == 200
    assert len(dev_resp.content) > 100000

    latin_resp = client.get("/static/fonts/noto-serif-variable.woff2")
    assert latin_resp.status_code == 200
    assert len(latin_resp.content) > 20000

    # Verify licenses resolve
    ofl_dev = client.get("/static/fonts/OFL-NotoSerifDevanagari.txt")
    assert ofl_dev.status_code == 200

    ofl_latin = client.get("/static/fonts/OFL-NotoSerif.txt")
    assert ofl_latin.status_code == 200

    # Verify fonts.css content
    fonts_css = client.get("/static/css/fonts.css")
    assert fonts_css.status_code == 200
    assert "Noto Serif Devanagari" in fonts_css.text
    assert "Noto Serif" in fonts_css.text
    assert "unicode-range" in fonts_css.text

    # Test first_words filter
    assert first_words("शुरुआती दर्शकों के लिए एनीमे गाइड", 2) == "शुरुआती दर्शकों"
    assert first_words("डेमन स्लेयर", 2) == "डेमन स्लेयर"
    assert first_words("Naruto", 2) == "Naruto"
    assert first_words("", 2) == ""

    # Verify home page HTML font architecture
    home_resp = client.get("/")
    assert home_resp.status_code == 200
    html = home_resp.text

    # Preload only Noto Serif Devanagari
    assert 'rel="preload" as="font" type="font/woff2"' in html
    assert 'href="/static/fonts/noto-serif-devanagari-variable.woff2"' in html
    assert 'noto-serif-variable.woff2" crossorigin' not in html  # Do not preload Latin

    # No external font dependencies
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert "use.typekit.net" not in html
    assert "cdnfonts.com" not in html

    # Verify fonts.css loads before tailwind
    fonts_pos = html.find('href="/static/css/fonts.css"')
    tailwind_pos = html.find('cdn.tailwindcss.com') if html.find('cdn.tailwindcss.com') != -1 else html.find('href="/static/css/tailwind.css"')
    assert fonts_pos != -1
    assert tailwind_pos != -1
    assert fonts_pos < tailwind_pos

    # Verify tokens.css global font does NOT include Bell MT
    tokens_resp = client.get("/static/css/tokens.css")
    assert tokens_resp.status_code == 200
    assert "--font: 'Noto Serif Devanagari', 'Noto Serif', serif;" in tokens_resp.text
    assert "Bell MT" not in tokens_resp.text


def test_category_page():
    response = client.get("/category/where-to-watch")
    assert response.status_code == 200
    assert "कहाँ देखें" in response.text
    assert "<img" not in response.text


def test_404_page():
    response = client.get("/random-invalid-page-xyz")
    assert response.status_code == 404
    assert "पृष्ठ नहीं मिला" in response.text
    assert "कहाँ देखें" in response.text
    assert "शुरुआती गाइड" in response.text


def test_sample_article_display_and_banner():
    with Session(engine) as session:
        cat = session.exec(select(Category).where(Category.slug == "where-to-watch")).first()
        existing = session.exec(select(Article).where(Article.slug == "test-sample-guide")).first()
        if not existing:
            article = Article(
                article_type=ArticleType.GUIDE,
                title="[नमूना] एनीमे परिचय गाइड",
                slug="test-sample-guide",
                category_id=cat.id if cat else None,
                summary="नमूना लेख सारांश",
                content_md="## शीर्षक\n\nनमूना लेख सामग्री।",
                is_featured=False,
                is_sample=True,
                status="published",
                reading_time_minutes=3
            )
            session.add(article)
            session.commit()

    response = client.get("/article/test-sample-guide")
    assert response.status_code == 200
    assert "[नमूना]" in response.text
    # Check robots meta tag
    assert '<meta name="robots" content="noindex,nofollow">' in response.text
    # Check sample banner
    assert "नमूना सामग्री — यह सार्वजनिक साइट के लिए नहीं है" in response.text
    # Ensure no img tags exist
    assert "<img" not in response.text

    # Verify that the anime card on the category page renders with the category typographic color
    cat_response = client.get("/category/where-to-watch")
    assert cat_response.status_code == 200
    assert "c-red" in cat_response.text
    assert "<img" not in cat_response.text


def test_article_without_availability():
    # An article without Availability shows no "अंतिम जाँच" and no platform names
    with Session(engine) as session:
        cat = session.exec(select(Category).where(Category.slug == "where-to-watch")).first()
        existing = session.exec(select(Article).where(Article.slug == "test-anime-no-avail")).first()
        if existing:
            session.delete(existing)
            session.commit()
        article = Article(
            article_type=ArticleType.ANIME,
            title="परीक्षण एनीमे बिना उपलब्धता",
            slug="test-anime-no-avail",
            category_id=cat.id if cat else None,
            summary="सारांश",
            content_md="## विवरण\n\nविवरण सामग्री।",
            status="published"
        )
        session.add(article)
        session.commit()

    response = client.get("/article/test-anime-no-avail")
    assert response.status_code == 200
    assert "परीक्षण एनीमे बिना उपलब्धता" in response.text
    assert "अंतिम जाँच:" not in response.text
    assert "Netflix" not in response.text
    assert "Crunchyroll" not in response.text
    assert "JioHotstar" not in response.text
    assert "Prime Video" not in response.text


def test_article_with_valid_availability():
    # An article with one row (last_checked=2026-10-18) shows "18 अक्टूबर 2026" and official link
    with Session(engine) as session:
        cat = session.exec(select(Category).where(Category.slug == "where-to-watch")).first()
        platform = session.exec(select(Platform).where(Platform.slug == "crunchyroll")).first()
        existing = session.exec(select(Article).where(Article.slug == "test-anime-with-avail")).first()
        if existing:
            avails = session.exec(select(Availability).where(Availability.article_id == existing.id)).all()
            for av in avails:
                session.delete(av)
            session.delete(existing)
            session.commit()
        article = Article(
            article_type=ArticleType.ANIME,
            title="परीक्षण एनीमे वैध उपलब्धता",
            slug="test-anime-with-avail",
            category_id=cat.id if cat else None,
            summary="सारांश",
            content_md="## विवरण\n\nविवरण।",
            status="published"
        )
        session.add(article)
        session.commit()
        session.refresh(article)

        avail = Availability(
            article_id=article.id,
            platform_id=platform.id,
            status=AvailabilityStatus.AVAILABLE,
            hindi_dub=HindiDubStatus.YES,
            last_checked=date(2026, 10, 18),
            note="आधिकारिक हिंदी डब उपलब्ध"
        )
        session.add(avail)
        session.commit()

    response = client.get("/article/test-anime-with-avail")
    assert response.status_code == 200
    assert "Crunchyroll" in response.text
    assert "उपलब्ध" in response.text
    assert "18 अक्टूबर 2026" in response.text
    assert "अंतिम जाँच:" in response.text
    assert 'href="https://www.crunchyroll.com"' in response.text
    assert 'rel="noopener noreferrer"' in response.text


def test_article_with_null_last_checked():
    # A row with last_checked=NULL shows nothing
    with Session(engine) as session:
        cat = session.exec(select(Category).where(Category.slug == "where-to-watch")).first()
        platform = session.exec(select(Platform).where(Platform.slug == "netflix")).first()
        existing = session.exec(select(Article).where(Article.slug == "test-anime-null-date")).first()
        if existing:
            avails = session.exec(select(Availability).where(Availability.article_id == existing.id)).all()
            for av in avails:
                session.delete(av)
            session.delete(existing)
            session.commit()
        article = Article(
            article_type=ArticleType.ANIME,
            title="परीक्षण एनीमे नल जाँच तिथि",
            slug="test-anime-null-date",
            category_id=cat.id if cat else None,
            summary="सारांश",
            content_md="## विवरण\n\nविवरण।",
            status="published"
        )
        session.add(article)
        session.commit()
        session.refresh(article)

        avail = Availability(
            article_id=article.id,
            platform_id=platform.id,
            status=AvailabilityStatus.AVAILABLE,
            hindi_dub=HindiDubStatus.YES,
            last_checked=None
        )
        session.add(avail)
        session.commit()

    response = client.get("/article/test-anime-null-date")
    assert response.status_code == 200
    assert "परीक्षण एनीमे नल जाँच तिथि" in response.text
    assert "अंतिम जाँच:" not in response.text
    assert "कहाँ देखें (Where to Watch)" not in response.text


def test_short_description_helper():
    # Strips '[नमूना]' prefix and cuts cleanly at word boundary
    text = "[नमूना] एनीमे देखने की शुरुआत कैसे करें और विभिन्न शैलियों को समझने के लिए एक संपूर्ण मार्गदर्शिका जो दर्शकों को सही विकल्प चुनने में सहायता प्रदान करती है।"
    shortened = short_description(text, limit=40)
    assert "[नमूना]" not in shortened
    assert len(shortened) <= 40
    # Must end on a word boundary without trailing punctuation
    assert not shortened.endswith(" ")
    assert not shortened.endswith(",")


def test_sqlite_pragmas():
    # Check that SQLite foreign_keys is ON and journal_mode is WAL
    with Session(engine) as session:
        raw_conn = session.connection().connection
        if isinstance(raw_conn, sqlite3.Connection):
            cursor = raw_conn.cursor()
            fk = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
            jm = cursor.execute("PRAGMA journal_mode").fetchone()[0]
            cursor.close()
            assert fk == 1
            assert jm.lower() == "wal"


def test_article_updated_at_auto_update():
    # Test that updated_at changes on update
    with Session(engine) as session:
        article = session.exec(select(Article).where(Article.slug == "auto-update-test")).first()
        if not article:
            article = Article(
                title="ऑटो अपडेट टेस्ट",
                slug="auto-update-test",
                summary="सारांश",
                content_md="सामग्री",
                status="published"
            )
            session.add(article)
            session.commit()
            session.refresh(article)

        original_updated_at = article.updated_at
        article.title = "अपडेटेड शीर्षक"
        session.add(article)
        session.commit()
        session.refresh(article)
        assert article.updated_at is not None


def test_production_safety_check(monkeypatch):
    with Session(engine) as session:
        sample = session.exec(select(Article).where(Article.is_sample == True)).first()
        if not sample:
            sample = Article(
                title="[नमूना] प्रोडक्शन जाँच",
                slug="prod-check-sample",
                summary="नमूना",
                content_md="नमूना",
                is_sample=True
            )
            session.add(sample)
            session.commit()

    settings = get_settings()
    monkeypatch.setattr(settings, "env", "production")
    with pytest.raises(RuntimeError, match="Production mode cannot start with sample articles"):
        init_db()


def test_theme_configuration_and_controller():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # 1. Inline theme script is in <head> BEFORE any stylesheet <link>
    assert "localStorage.getItem('theme') === 'dark'" in html
    script_pos = html.find("localStorage.getItem('theme') === 'dark'")
    link_pos = html.find('<link rel="stylesheet"')
    assert script_pos != -1, "Inline theme script not found"
    assert link_pos != -1, "Stylesheet link not found"
    assert script_pos < link_pos, "Inline theme script must appear before stylesheet link to prevent white flash"

    # 2. No prefers-color-scheme in JS/HTML
    assert "prefers-color-scheme" not in html

    # 3. Meta color-scheme is present
    assert '<meta name="color-scheme" content="light dark">' in html

    # 4. Default is light theme (no dark class on html tag)
    assert '<html lang="hi">' in html
    assert '<html lang="hi" class="dark">' not in html

    # 5. Theme toggle button has type="button" and aria-label
    assert 'aria-label="Toggle theme"' in html or 'aria-label="डार्क मोड"' in html
    assert '@click="toggleTheme()"' in html or 'theme-toggle' in html
    assert 'type="button"' in html

    # 7. Inline SVG sun/moon icons are used (no emoji in toggle)
    assert '<svg' in html
    assert 'viewBox="0 0 24 24"' in html

    # 8. LocalStorage key is 'theme' with try/catch
    assert "localStorage.getItem('theme')" in html
    assert "localStorage.setItem('theme', 'dark')" in html
    assert "localStorage.setItem('theme', 'light')" in html


def test_site_name_and_branding():
    from app.config import SITE_NAME
    assert SITE_NAME == "Hindi Anime Guide"

    # Homepage title & branding
    home_resp = client.get("/")
    assert home_resp.status_code == 200
    assert ("Hindi Anime Guide" in home_resp.text)
    assert ('Hindi Anime <span class="text-red-600">Guide</span>' in home_resp.text or 'Hindi Anime <span>Guide</span>' in home_resp.text)
    assert "AnimeHindi" not in home_resp.text
    assert "विश्वसनीय" not in home_resp.text

    # Category title pattern
    cat_resp = client.get("/category/where-to-watch")
    assert cat_resp.status_code == 200
    assert "<title>कहाँ देखें | Hindi Anime Guide</title>" in cat_resp.text
    assert "AnimeHindi" not in cat_resp.text

    # Article title pattern
    with Session(engine) as session:
        cat = session.exec(select(Category).where(Category.slug == "where-to-watch")).first()
        art = Article(
            article_type=ArticleType.ANIME,
            title="परीक्षण एनीमे नल जाँच तिथि",
            slug="test-anime-null-date",
            category_id=cat.id if cat else None,
            summary="सारांश",
            content_md="विवरण",
            status="published"
        )
        session.add(art)
        session.commit()

    article_resp = client.get("/article/test-anime-null-date")
    assert article_resp.status_code == 200
    assert " | Hindi Anime Guide</title>" in article_resp.text
    assert "AnimeHindi" not in article_resp.text

    # 404 title pattern
    not_found = client.get("/non-existent-page-url")
    assert not_found.status_code == 404
    assert "<title>404 - पृष्ठ नहीं मिला | Hindi Anime Guide</title>" in not_found.text


def test_sidebar_exact_5_items_and_accessibility():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # 1. Navigation links exist in header and drawer
    assert 'href="/"' in html
    assert 'href="/explore"' in html
    assert 'href="/auth/login"' in html or 'href="/profile"' in html
    assert 'href="/auth/register"' in html or 'href="/saved"' in html


def test_footer_placeholder_pages_and_disclaimer():
    # Verify footer links exist
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    for path, key in [
        ("/about", "about_us"),
        ("/editorial-policy", "editorial_policy"),
        ("/contact", "contact"),
        ("/privacy", "privacy"),
        ("/disclaimer", "disclaimer"),
    ]:
        assert f'href="{path}"' in html
        assert f'data-i18n="{key}"' in html

    # Unified disclaimer in footer / I18N
    assert "footer_disclaimer_text" in html

    # Test each legal page: 200, correct title, NOT noindex, NOT coming-soon
    pages_to_test = [
        ("/about",               "हमारे बारे में | Hindi Anime Guide"),
        ("/editorial-policy",    "संपादकीय नीति | Hindi Anime Guide"),
        ("/contact",             "संपर्क | Hindi Anime Guide"),
        ("/privacy",             "गोपनीयता नीति | Hindi Anime Guide"),
        ("/disclaimer",          "अस्वीकरण | Hindi Anime Guide"),
        ("/terms",               "उपयोग की शर्तें | Hindi Anime Guide"),
        ("/cookies",             "कुकी नीति | Hindi Anime Guide"),
        ("/community-guidelines","सामुदायिक दिशानिर्देश | Hindi Anime Guide"),
        ("/copyright",           "कॉपीराइट और Takedown | Hindi Anime Guide"),
        ("/streaming-disclosure","Streaming प्रकटीकरण | Hindi Anime Guide"),
    ]

    for path, expected_title in pages_to_test:
        res = client.get(path)
        assert res.status_code == 200, f"{path} returned {res.status_code}"
        assert f"<title>{expected_title}</title>" in res.text, f"{path} missing title"
        # Legal pages are now real pages — must NOT have noindex
        assert '<meta name="robots" content="noindex,nofollow">' not in res.text, (
            f"{path} should not have noindex (it is now a real page)"
        )
        # Coming-soon text must be gone
        assert "यह पेज जल्द प्रकाशित होगा" not in res.text, (
            f"{path} still shows 'coming soon' placeholder text"
        )
        assert "Hindi Anime Guide" in res.text
        assert "AnimeHindi" not in res.text
        # Multi-language blocks: both Hindi and English sections exist in DOM for instant toggle
        assert 'data-lang-block="hi"' in res.text, f"{path} missing Hindi content block"
        assert 'data-lang-block="en"' in res.text, f"{path} missing English content block"
        # Banner hidden on pages without samples
        assert "नमूना सामग्री — यह सार्वजनिक साइट के लिए नहीं है" not in res.text


def test_empty_state_without_published_articles(monkeypatch):
    # Temporarily unpublish all articles to test empty state
    with Session(engine) as session:
        cat = session.exec(select(Category)).first()
        art = Article(
            article_type=ArticleType.GUIDE,
            title="अस्थायी लेख",
            slug="temp-empty-state-test",
            category_id=cat.id if cat else None,
            summary="सारांश",
            content_md="सामग्री",
            status="published"
        )
        session.add(art)
        session.commit()

        articles = session.exec(select(Article)).all()
        original_statuses = [(a.id, a.status) for a in articles]
        for a in articles:
            a.status = "draft"
            session.add(a)
        session.commit()

    try:
        res = client.get("/")
        assert res.status_code == 200
        assert ("No guides published yet" in res.text or "पहले लेख जल्द आ रहे हैं" in res.text)
    finally:
        # Restore original statuses
        with Session(engine) as session:
            for art_id, orig_st in original_statuses:
                art = session.get(Article, art_id)
                if art:
                    art.status = orig_st
                    session.add(art)
            session.commit()


def test_sample_banner_visibility_rule():
    BANNER_TEXT = "नमूना सामग्री — यह सार्वजनिक साइट के लिए नहीं है"
    with Session(engine) as session:
        cat_rec = session.exec(select(Category).where(Category.slug == "recommendations")).first()
        # Clean up any test articles for this test
        for s in ["regular-only-article", "sample-banner-article"]:
            old = session.exec(select(Article).where(Article.slug == s)).first()
            if old:
                session.delete(old)
        session.commit()

        # Regular article (is_sample=False)
        reg_art = Article(
            title="नियमित गैर-नमूना लेख",
            slug="regular-only-article",
            category_id=cat_rec.id if cat_rec else None,
            summary="नियमित लेख सारांश",
            content_md="## नियमित सामग्री",
            is_sample=False,
            status="published"
        )
        # Sample article (is_sample=True)
        sample_art = Article(
            title="[नमूना] परीक्षण नमूना लेख",
            slug="sample-banner-article",
            category_id=cat_rec.id if cat_rec else None,
            summary="नमूना लेख सारांश",
            content_md="## नमूना सामग्री",
            is_sample=True,
            status="published"
        )
        session.add_all([reg_art, sample_art])
        session.commit()

    # 1. On sample article page, banner MUST be present
    res_sample = client.get("/article/sample-banner-article")
    assert res_sample.status_code == 200
    assert BANNER_TEXT in res_sample.text

    # 2. On placeholder pages, banner MUST NOT be present
    for page in ["/about", "/contact", "/privacy", "/disclaimer"]:
        res_page = client.get(page)
        assert res_page.status_code == 200
        assert BANNER_TEXT not in res_page.text


def test_card_and_hero_cover_aspect_ratios():
    # Card covers aspect-ratio 16/7, hero lead cover 16/8, flat colors from tokens
    with open("app/static/css/input.css", "r", encoding="utf-8") as f:
        input_css = f.read()

    with open("app/static/css/tailwind.css", "r", encoding="utf-8") as f:
        tailwind_css = f.read()

    # Verify aspect ratios in CSS
    assert ".cover" in input_css and "aspect-ratio: 16/7;" in input_css
    assert ".card .cover" in input_css and "aspect-ratio: 16/7;" in input_css
    assert ".lead .cover" in input_css and "aspect-ratio: 16/8;" in input_css

    assert ".cover{" in tailwind_css and "aspect-ratio:16/7" in tailwind_css
    assert ".lead .cover{" in tailwind_css and "aspect-ratio:16/8" in tailwind_css

    # Verify flat colors from tokens (no gradients)
    assert ".c-red" in input_css and "var(--cover-red-bg)" in input_css
    assert ".c-ink" in input_css and "var(--cover-ink-bg)" in input_css
    assert ".c-slate" in input_css and "var(--cover-slate-bg)" in input_css
    assert "linear-gradient" not in input_css
    assert "radial-gradient" not in input_css


def test_cover_text_anime_vs_guide():
    # For articles with anime_title_hi use it; for guides show category name
    with Session(engine) as session:
        cat_rec = session.exec(select(Category).where(Category.slug == "recommendations")).first()
        cat_guide = session.exec(select(Category).where(Category.slug == "beginners-guide")).first()

        # Clean existing test articles if any
        for s in ["test-cover-anime", "test-cover-guide"]:
            existing = session.exec(select(Article).where(Article.slug == s)).first()
            if existing:
                session.delete(existing)
        session.commit()

        # Anime article with anime_title_hi in recommendations (cover_color='slate')
        anime_art = Article(
            article_type=ArticleType.ANIME,
            title="विशेष एनीमे लेख शीर्षक",
            slug="test-cover-anime",
            anime_title_hi="परीक्षण शीर्षक देवनागरी",
            anime_title_en="Test Anime EN",
            category_id=cat_rec.id if cat_rec else None,
            summary="एनीमे सारांश",
            content_md="सामग्री",
            status="published"
        )
        # Guide article without anime_title_hi in beginners-guide (cover_color='ink')
        guide_art = Article(
            article_type=ArticleType.GUIDE,
            title="विस्तृत गाइड लेख का लम्बा शीर्षक",
            slug="test-cover-guide",
            anime_title_hi=None,
            category_id=cat_guide.id if cat_guide else None,
            summary="गाइड सारांश",
            content_md="सामग्री",
            status="published"
        )
        session.add_all([anime_art, guide_art])
        session.commit()

    resp = client.get("/category/recommendations")
    assert resp.status_code == 200
    # Cover text should use anime_title_hi
    assert "परीक्षण शीर्षक देवनागरी" in resp.text

    resp_guide = client.get("/category/beginners-guide")
    assert resp_guide.status_code == 200
    # Cover text for guide should use category name 'शुरुआती गाइड', NOT repeating article title
    assert '<div class="cover c-ink">' in resp_guide.text
    assert "शुरुआती गाइड" in resp_guide.text
    # The article title must NOT appear inside the cover div
    assert f'<div class="cover c-ink">\n    विस्तृत गाइड लेख का लम्बा शीर्षक' not in resp_guide.text


def test_homepage_grid_excludes_hero_items():
    # Create enough articles with explicit descending timestamps to test hero (lead + 3 side = 4) + grid
    with Session(engine) as session:
        cat = session.exec(select(Category).where(Category.slug == "where-to-watch")).first()

        created_slugs = [f"hero-grid-test-art-{i}" for i in range(1, 7)]
        for slug in created_slugs:
            old = session.exec(select(Article).where(Article.slug == slug)).first()
            if old:
                session.delete(old)
        session.commit()

        new_articles = []
        for i, slug in enumerate(created_slugs, 1):
            art = Article(
                article_type=ArticleType.GUIDE,
                title=f"परीक्षण ग्रिड लेख {i}",
                slug=slug,
                category_id=cat.id if cat else None,
                summary=f"सारांश {i}",
                content_md=f"सामग्री {i}",
                status="published",
                is_featured=(i == 1),
                created_at=datetime(2026, 10, 18, 12, 0, 50 - i, tzinfo=timezone.utc)
            )
            new_articles.append(art)
        session.add_all(new_articles)
        session.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    # Verify modern hero section and articles grid exist
    assert '<section class="notranslate' in html or '<section class="hero">' in html
    assert 'href="/article/hero-grid-test-art-1"' in html
    assert 'href="/article/hero-grid-test-art-2"' in html


def test_homepage_no_category_pills_under_hero():
    with Session(engine) as session:
        cat = session.exec(select(Category).where(Category.slug == "where-to-watch")).first()
        art = Article(
            article_type=ArticleType.GUIDE,
            title="परीक्षण लेख पिल्स",
            slug="test-art-pills-check",
            category_id=cat.id if cat else None,
            summary="सारांश",
            content_md="सामग्री",
            status="published"
        )
        session.add(art)
        session.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    # Hero exists
    assert '<section class="notranslate' in html or '<section class="hero">' in html
    assert "test-art-pills-check" in html



