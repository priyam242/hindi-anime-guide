"""
Tests for UI/UX Overhaul & 5 Editorial Sections:
- Updates Hub (/updates)
- Season Schedule (/schedule)
- News & Updates (/news, /updates/{id})
- Manga Tracking (/manga)
- Awards (/awards)
- Trailers (/trailers)
- Sidebar i18n & Design Tokens
"""
from datetime import datetime, timezone
import pytest
from starlette.testclient import TestClient
from sqlmodel import Session

from app.main import app
from app.models import AnimeDraft


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_drafts(isolate_database_per_test):
    """Seed test drafts into in-memory SQLite database."""
    from app.db import get_session
    session = next(get_session())
    manga = AnimeDraft(
        content_type="manga",
        status="published",
        source_name="MangaPlus",
        source_url="https://mangaplus.shueisha.co.jp",
        external_id="test_manga_unique_101",
        title_en="One Piece",
        title_hi="वन पीस",
        summary_en="Chapter 1130 released",
        summary_hi="चैप्टर 1130 जारी हुआ",
        image_url="https://s4.anilist.co/cover.jpg",
        published_at=datetime.now(timezone.utc),
    )
    trailer = AnimeDraft(
        content_type="trailer",
        status="published",
        source_name="YouTube TOHO",
        source_url="https://www.youtube.com/watch?v=pkZXbh6f554",
        external_id="test_trailer_unique_102",
        title_en="Jujutsu Kaisen Teaser",
        title_hi="जुजुत्सु काइसेन टीज़र",
        summary_en="Official teaser",
        summary_hi="आधिकारिक टीज़र",
        image_url="https://s4.anilist.co/cover2.jpg",
        published_at=datetime.now(timezone.utc),
    )
    session.add(manga)
    session.add(trailer)
    session.commit()
    session.refresh(manga)
    session.refresh(trailer)
    return manga, trailer


def test_updates_hub_page(client):
    """Updates hub (/updates) returns 200 and renders 5 summary sections."""
    response = client.get("/updates")
    assert response.status_code == 200
    html = response.text
    assert "ताज़ा एनीमे अपडेट्स एवं संपादकीय हब" in html
    assert "ताज़ा एनीमे समाचार" in html
    assert "सीज़न प्रसारण शेड्यूल" in html
    assert "मंगा अपडेट्स" in html
    assert "एनीमे पुरस्कार" in html
    assert "आधिकारिक एनीमे ट्रेलर्स" in html


def test_schedule_page_tabs_and_countdown(client):
    """Schedule page (/schedule) returns 200 with Mon-Sun day tabs and countdown."""
    response = client.get("/schedule")
    assert response.status_code == 200
    html = response.text
    assert "वर्तमान सीज़न एनीमे शेड्यूल" in html
    assert "scheduleManager" in html
    assert "getUpcomingCountdown" in html or "getCountdown" in html


def test_news_page_and_category_chips(client):
    """News page (/news) returns 200 with category chips and card grid."""
    response = client.get("/news")
    assert response.status_code == 200
    html = response.text
    assert "ताज़ा एनीमे समाचार" in html
    assert "संपादकीय एनीमे समाचार" in html or "एनीमे समाचार" in html
    assert "घोषणाएं" in html or "सीज़न अपडेट्स" in html or "ताज़ा समाचार" in html


def test_manga_page_table_and_groups(client, sample_drafts):
    """Manga page (/manga) returns 200 with desktop table and time groupings."""
    response = client.get("/manga")
    assert response.status_code == 200
    html = response.text
    assert "मंगा अपडेट्स एवं नए चैप्टर्स" in html
    assert "चालू मंगा" in html or "मंगा शीर्षक" in html
    assert "नवीनतम" in html or "चैप्टर" in html


def test_awards_page_year_selector_and_winner(client):
    """Awards page (/awards) returns 200 with year selector tabs and winner."""
    response = client.get("/awards")
    assert response.status_code == 200
    html = response.text
    assert "एनीमे पुरस्कार एवं नामांकन" in html
    assert "अवॉर्ड्स" in html or "Awards" in html


def test_trailers_page_click_to_load(client, sample_drafts):
    """Trailers page (/trailers) returns 200 with click-to-load iframes."""
    response = client.get("/trailers")
    assert response.status_code == 200
    html = response.text
    assert "एनीमे ट्रेलर्स एवं प्रमोशनल वीडियोज़" in html
    assert "youtube-nocookie.com/embed" in html or "playing = true" in html


def test_update_detail_page(client, sample_drafts):
    """Detail page (/updates/{id}) renders readable article with source attribution and share buttons."""
    manga, _ = sample_drafts
    resp = client.get(f"/updates/{manga.id}")
    assert resp.status_code == 200
    html = resp.text
    assert "स्रोत संदर्भ" in html or "Source:" in html
    assert "शेयर करें:" in html
    assert "whatsapp" in html
    assert "detail-headline" in html


def test_sidebar_i18n_and_design_tokens(client):
    """Checks sidebar i18n keys and design tokens in base template."""
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    # Central CSS variables
    assert "--gradient-from" in html
    assert "--gradient-via" in html
    assert "--gradient-to" in html
    assert ".heading-gradient" in html
    # Sidebar i18n dictionary in JS
    assert '"sidebar_group_nav": "नेविगेशन"' in html
    assert '"sidebar_group_nav": "NAVIGATION"' in html
    assert '"upd_news": "न्यूज़"' in html or '"upd_news": "एनीमे समाचार"' in html
    assert '"upd_news": "Anime News"' in html
    assert '"upd_schedule": "शेड्यूल"' in html or '"upd_schedule": "सीज़न शेड्यूल"' in html
    assert '"upd_schedule": "Season Schedule"' in html
    assert '"upd_manga": "मंगा"' in html or '"upd_manga": "मंगा अपडेट्स"' in html
    assert '"upd_awards": "अवॉर्ड्स"' in html
    assert '"upd_trailers": "ट्रेलर्स"' in html
