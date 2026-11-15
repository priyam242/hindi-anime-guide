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


def test_zone3_equal_counts_and_zero_placeholder_text_images(client):
    """Verify Zone 3 strictly renders 6 / 6 / 6 items with 0 placeholder text-box images and matching manga titles."""
    res = client.get("/")
    assert res.status_code == 200
    html_text = res.text

    # Extract Zone 3 Column 1 (New Releases)
    m_z3_c1 = re.search(r"नई रिलीज़ \(New Releases\).*?(?=हिंदी डब गाइड्स)", html_text, re.S)
    assert m_z3_c1 is not None, "Zone 3 Column 1 not found"
    c1_cards = re.findall(r'<a href="(/anime/[^"]+)" class="group p-2\.5', m_z3_c1.group(0))
    assert len(c1_cards) == 6, f"Expected strictly 6 items in Zone 3 Column 1, got {len(c1_cards)}"

    # Extract Zone 3 Column 2 (Hindi Dub Guides)
    m_z3_c2 = re.search(r"हिंदी डब गाइड्स \(Hindi Dub Guides\).*?(?=लोकप्रिय मंगा)", html_text, re.S)
    assert m_z3_c2 is not None, "Zone 3 Column 2 not found"
    c2_cards = re.findall(r'<a href="(/anime/[^"]+)" class="group p-2\.5', m_z3_c2.group(0))
    assert len(c2_cards) == 6, f"Expected strictly 6 items in Zone 3 Column 2, got {len(c2_cards)}"

    # Extract Zone 3 Column 3 (Popular Manga)
    m_z3_c3 = re.search(r"लोकप्रिय मंगा \(Popular Manga\).*?(?=सभी मंगा देखें)", html_text, re.S)
    assert m_z3_c3 is not None, "Zone 3 Column 3 not found"
    c3_cards = re.findall(r'<a href="(/manga/[^"]+)" class="group p-2\.5', m_z3_c3.group(0))
    assert len(c3_cards) == 6, f"Expected strictly 6 items in Zone 3 Column 3, got {len(c3_cards)}"

    # Verify zero placeholder text-box images in Zone 3
    z3_html = m_z3_c1.group(0) + m_z3_c2.group(0) + m_z3_c3.group(0)
    assert "placeholder" not in z3_html, "Found placeholder in Zone 3!"
    assert ".svg" not in z3_html, "Found SVG placeholder in Zone 3!"

    # Verify Manga Title/Subtitle match (e.g. no Tang Yin != Ryuu Ron)
    assert "Ryuu: Ron" not in z3_html
    assert "Eurydice" not in z3_html
    assert "One Piece" in z3_html
    assert "Chainsaw Man" in z3_html
    assert "Blue Lock" in z3_html
    assert "Sakamoto Days" in z3_html


def test_homepage_airing_episodes_and_card_density(client):
    """Verify 18 airing cards, Alpine tabs, new Zone 2 Left sections, and >= 50 total anime cards."""
    from app.routers.pages import invalidate_public_caches
    invalidate_public_caches()
    res = client.get("/")
    assert res.status_code == 200
    html_text = res.text

    # Verify Alpine filter tabs
    assert "सभी (18)" in html_text
    assert "आज के शोज़" in html_text
    assert "हिंदी डब" in html_text
    assert "डोंगहुआ" in html_text

    # Verify Airing Schedule has 18 cards
    m_air = re.search(r"ताज़ा एपिसोड्स \(Latest Airing Schedule\).*?(?=नए ऑफिशियल ट्रेलर्स)", html_text, re.S)
    assert m_air is not None
    air_cards = re.findall(r'<a href="(/anime/[^"]+)"[^>]*x-show="tab', m_air.group(0))
    assert len(air_cards) == 18, f"Expected 18 airing cards, got {len(air_cards)}"

    # Verify NO fake 'Ep 1' hardcoded across airing cards
    ep_badges = re.findall(r'<span class="absolute top-2 left-2[^"]*">([^<]+)</span>', m_air.group(0))
    assert len(ep_badges) == 18
    assert ep_badges.count("Ep 1") == 0, f"Found fake 'Ep 1' in airing cards: {ep_badges}"
    assert "Ep 7" in ep_badges
    assert "Ep 1220" in ep_badges
    assert "Ep 138" in ep_badges

    # Verify New Section: लोकप्रिय हिंदी डब एनीमे (12 cards)
    m_dub = re.search(r"लोकप्रिय हिंदी डब एनीमे \(Popular Hindi Dubs\).*?(?=मुफ़्त में देखें)", html_text, re.S)
    assert m_dub is not None
    dub_cards = re.findall(r'<a href="(/anime/[^"]+)" class="group relative rounded-xl', m_dub.group(0))
    assert len(dub_cards) == 12, f"Expected 12 cards in Popular Hindi Dubs, got {len(dub_cards)}"

    # Verify New Section: मुफ़्त में देखें (6 cards)
    m_free = re.search(r"मुफ़्त में देखें \(Free Legal Anime\).*?(?=<!-- Right Sidebar)", html_text, re.S)
    assert m_free is not None
    free_cards = re.findall(r'<a href="(/anime/[^"]+)" class="group relative rounded-xl', m_free.group(0))
    assert len(free_cards) == 6, f"Expected 6 cards in Free Legal Anime, got {len(free_cards)}"

    # Verify Total Distinct Anime Cards on GET / is >= 50
    all_anime_links = re.findall(r'href="(/anime/[^"]+|/updates/\d+|/manga/\d+)"', html_text)
    distinct_cards = set(all_anime_links)
    assert len(distinct_cards) >= 50, f"Expected >= 50 distinct anime cards, got {len(distinct_cards)}"


def test_poster_urls_unique_per_franchise(client):
    """Verify poster URLs across Zone 2A and Zone 3 are 100% unique per franchise (0 duplicate fallbacks)."""
    from app.routers.pages import invalidate_public_caches
    invalidate_public_caches()
    res = client.get("/")
    assert res.status_code == 200
    html_text = res.text

    # Extract all poster image URLs from Zone 2A airing cards
    m_air = re.search(r"ताज़ा एपिसोड्स \(Latest Airing Schedule\).*?(?=नए ऑफिशियल ट्रेलर्स)", html_text, re.S)
    air_imgs = re.findall(r'<img[^>]+src="([^"]+)"', m_air.group(0))
    assert len(air_imgs) == 18
    assert len(set(air_imgs)) == 18, f"Found duplicate covers in Zone 2A airing cards! {len(set(air_imgs))} unique vs 18"

    # Extract all poster image URLs from Zone 3
    m_z3 = re.search(r"<!-- ZONE 3: Bottom 3-Category Equal Columns.*?(?=<footer|<script|id=\"footer\")", html_text, re.S)
    assert m_z3 is not None
    z3_imgs = re.findall(r'<img[^>]+src="([^"]+)"', m_z3.group(0))
    assert len(z3_imgs) == 18
    assert len(set(z3_imgs)) == 18, f"Found duplicate covers in Zone 3! {len(set(z3_imgs))} unique vs 18"
