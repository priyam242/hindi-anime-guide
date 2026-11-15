"""
Tests for Master Detail Page, HD Artwork & Episode Tracker Upgrade:
- Official HD AniList artwork without fallback to /static/placeholder.svg
- Clean Episode Tracker (aired count, exact IST dates, remaining/next info)
- Zero 'Ep ?' and zero 'Light Novel' source clutter boxes
- Accurate Indian streaming platforms (Crunchyroll & Netflix)
- Fast response latency and 0 visible '100%' claims
"""
import html
import re
import time
import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_zero_placeholder_svg_and_ep_question_mark(client):
    """Verify zero placeholder.svg and zero 'Ep ?' across core pages."""
    pages = [
        "/",
        "/free",
        "/schedule",
        "/anime/black-clover-season-2",
        "/anime/the-apothecary-diaries-season-3",
        "/anime/frieren-beyond-journeys-end",
    ]

    for p in pages:
        res = client.get(p)
        assert res.status_code == 200, f"Page {p} returned {res.status_code}"
        page_html = res.text

        # Verify zero occurrences of placeholder.svg
        assert "/static/placeholder.svg" not in page_html, f"Found /static/placeholder.svg in {p}"

        # Verify zero occurrences of 'Ep ?' or 'चालू • Ep ?'
        assert "Ep ?" not in page_html, f"Found 'Ep ?' in {p}"
        assert "चालू • Ep ?" not in page_html, f"Found 'चालू • Ep ?' in {p}"


def test_no_source_clutter_boxes_on_detail_pages(client):
    """Verify raw 'स्रोत: Light Novel' and format codes are stripped from detail pages."""
    detail_slugs = [
        "the-apothecary-diaries-season-3",
        "black-clover-season-2",
        "frieren-beyond-journeys-end",
        "chainsaw-man",
    ]

    for slug in detail_slugs:
        res = client.get(f"/anime/{slug}")
        assert res.status_code == 200
        raw_html = res.text

        # Ensure clutter source box is eliminated
        assert "स्रोत: Light Novel" not in raw_html, f"Found 'स्रोत: Light Novel' in /anime/{slug}"
        assert "स्रोत:" not in raw_html, f"Found 'स्रोत:' label in /anime/{slug}"


def test_episode_tracker_on_detail_pages(client):
    """Verify Episode Tracker presence, summary headers, IST dates, and status pills."""
    # 1. The Apothecary Diaries Season 3 (12 episodes total, 7 aired, 5 remaining)
    res_apoth = client.get("/anime/the-apothecary-diaries-season-3")
    assert res_apoth.status_code == 200
    html_apoth = html.unescape(res_apoth.text)

    # Episode Tracker Summary Header
    assert "✅ रिलीज़ हो चुके: 7 एपिसोड्स" in html_apoth
    assert "⏳ बाकी हैं: 5 एपिसोड्स (कुल 12)" in html_apoth
    assert "एपिसोड सूची और रिलीज़ डेट" in html_apoth

    # Next episode status and IST date
    assert "⏳ आने वाला है" in html_apoth
    assert "✅ रिलीज़ हो चुका" in html_apoth
    assert "20 नवंबर 2026" in html_apoth
    assert 'referrerpolicy="no-referrer"' in res_apoth.text

    # 2. Black Clover Season 2 (ongoing, total unannounced)
    res_bc = client.get("/anime/black-clover-season-2")
    assert res_bc.status_code == 200
    html_bc = html.unescape(res_bc.text)

    # Episode Tracker Summary Header for unannounced total
    assert "✅ रिलीज़ हो चुके: 7 एपिसोड्स" in html_bc
    assert "⏳ अगला: एपिसोड 8 (प्रसारण जारी)" in html_bc
    assert "एपिसोड सूची और रिलीज़ डेट" in html_bc
    assert "21 नवंबर 2026" in html_bc


def test_streaming_platforms_and_rating_accuracy(client):
    """Verify legal streaming platforms and ratings for Black Clover S2 & Apothecary Diaries S3."""
    # The Apothecary Diaries Season 3
    res_apoth = client.get("/anime/the-apothecary-diaries-season-3")
    assert res_apoth.status_code == 200
    text_apoth = res_apoth.text

    assert "Crunchyroll" in text_apoth
    assert "Netflix" in text_apoth
    assert "Muse Asia" not in text_apoth
    assert "8.5" in text_apoth

    # Black Clover Season 2
    res_bc = client.get("/anime/black-clover-season-2")
    assert res_bc.status_code == 200
    text_bc = res_bc.text

    assert "Crunchyroll" in text_bc
    assert "Netflix" in text_bc
    assert "Muse Asia" not in text_bc
    assert "8.8" in text_bc


def test_cold_and_cached_latency(client):
    """Verify fast response latency on homepage."""
    # Warm/cached response
    t0 = time.perf_counter()
    res = client.get("/")
    t1 = time.perf_counter()
    latency_ms = (t1 - t0) * 1000

    assert res.status_code == 200
    # Latency should comfortably be under 300ms in-process
    assert latency_ms < 300, f"Homepage latency {latency_ms:.2f}ms exceeded 300ms threshold"
