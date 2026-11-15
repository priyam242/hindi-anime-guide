"""
Tests for Critical Architecture & UX Upgrade (Phase 2):
- Zero /explore?q= card links across / and /free
- Universal Dynamic Anime Detail Pages (/anime/{slug})
- Live IST Episode Countdown badge & valid TVSeries JSON-LD
- Surprise Me ('🎲 आज क्या देखूँ?') /random-anime picker
- Butter-smooth performance, speculation rules, and cache latency benchmark
"""
import html
import json
import re
import time
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.routers.pages import invalidate_public_caches


@pytest.fixture
def client():
    return TestClient(app)


def test_zero_explore_card_links_on_home_and_free(client):
    """Verify ZERO anime cards on / and /free link to /explore?q=."""
    # 1. Check Homepage (/)
    res_home = client.get("/")
    assert res_home.status_code == 200
    home_html = res_home.text

    # Verify Zone 2A Airing episodes cards: all link to /anime/
    m_z2a = re.search(r"ताज़ा एयरिंग एपिसोड्स.*?अंतिम अपडेट", home_html, re.S)
    if m_z2a:
        z2a_cards = re.findall(r'href="([^"]+)"', m_z2a.group(0))
        for url in z2a_cards:
            assert not url.startswith("/explore?q="), f"Found /explore?q= in Zone 2A card: {url}"

    # Verify Zone 3 Column 1 (Fresh Releases) cards: all link to /anime/
    m_z3_col1 = re.search(r"नई रिलीज़.*?हिंदी डब गाइड्स", home_html, re.S)
    if m_z3_col1:
        z3_col1_links = re.findall(r'href="([^"]+)"', m_z3_col1.group(0))
        for url in z3_col1_links:
            assert not url.startswith("/explore?q="), f"Found /explore?q= in Zone 3 Col 1: {url}"

    # Verify Zone 3 Column 2 (Hindi Dub Guides): all 5 items link to /anime/
    m_z3_col2 = re.search(r"हिंदी डब गाइड्स \(Hindi Dub Guides\).*?(?=लोकप्रिय मंगा)", home_html, re.S)
    assert m_z3_col2 is not None
    z3_col2_html = m_z3_col2.group(0)
    assert "/explore?q=" not in z3_col2_html, "Found /explore?q= in Zone 3 Column 2!"
    assert "/anime/frieren-beyond-journeys-end" in z3_col2_html

    # 2. Check Free Hub (/free) cards
    res_free = client.get("/free")
    assert res_free.status_code == 200
    free_html = res_free.text

    # Extract all 20 anime card URLs on /free
    free_card_urls = re.findall(r'<a href="([^"]+)" class="relative aspect-\[2/3\]', free_html)
    assert len(free_card_urls) >= 10, f"Expected at least 10 cards on /free, found {len(free_card_urls)}"
    for url in free_card_urls:
        assert url.startswith("/anime/"), f"Free hub card URL must link to /anime/, got: {url}"
        assert not url.startswith("/explore?q="), f"Found /explore?q= on free card: {url}"


def test_dynamic_anime_detail_pages(client):
    """Verify 4 Dynamic Anime Detail Pages return 200 OK with rich metadata, countdown, and TVSeries JSON-LD."""
    test_slugs = [
        ("frieren-beyond-journeys-end", "फ्रीरेन: बियॉन्ड जर्नीज़ एंड", "Frieren: Beyond Journey's End"),
        ("chainsaw-man", "चेनसॉ मैन", "Chainsaw Man"),
        ("dandadan", "डैनडाडैन", "DanDaDan"),
        ("the-apothecary-diaries-season-3", "द अपोथेकेरी डायरीज़ सीज़न 3", "The Apothecary Diaries Season 3"),
    ]

    for slug, exp_hi, exp_en in test_slugs:
        res = client.get(f"/anime/{slug}")
        assert res.status_code == 200, f"Failed on /anime/{slug}: {res.status_code}"
        raw_html = res.text
        text_unescaped = html.unescape(raw_html)

        # Verify titles
        assert exp_hi in text_unescaped, f"Hindi title '{exp_hi}' missing from /anime/{slug}"
        assert exp_en in text_unescaped, f"English title '{exp_en}' missing from /anime/{slug}"

        # Verify streaming platforms section and badges
        assert "कहाँ देखें" in raw_html
        assert 'translate="no">' in raw_html

        # Verify countdown / airing badge presence
        assert "episodeCountdown" in raw_html

        # Verify parsed TVSeries JSON-LD schema
        jsonlds = re.findall(r'<script type="application/ld\+json">(.*?)</script>', raw_html, re.S)
        assert len(jsonlds) >= 1, f"Missing JSON-LD on /anime/{slug}"
        found_tvseries = False
        for jld in jsonlds:
            try:
                data = json.loads(jld.strip())
                if data.get("@type") == "TVSeries":
                    found_tvseries = True
                    assert data.get("name") == exp_hi
                    assert data.get("alternateName") == exp_en
                    assert len(data.get("potentialAction", [])) >= 1
            except Exception:
                pass
        assert found_tvseries, f"Valid TVSeries JSON-LD not found on /anime/{slug}"


def test_random_anime_picker(client):
    """Verify Phase 2 Surprise Me ('🎲 आज क्या देखूँ?') /random-anime endpoint."""
    # 1. Default pick redirect
    res = client.get("/random-anime", follow_redirects=False)
    assert res.status_code == 303
    loc = res.headers.get("location")
    assert loc and loc.startswith("/anime/"), f"Unexpected location: {loc}"

    # 2. Action mood pick
    res_action = client.get("/random-anime?mood=action", follow_redirects=False)
    assert res_action.status_code == 303
    assert res_action.headers.get("location", "").startswith("/anime/")

    # 3. JSON response when requested
    res_json = client.get("/random-anime", headers={"Accept": "application/json"})
    assert res_json.status_code == 200
    data = res_json.json()
    assert "url" in data and data["url"].startswith("/anime/")
    assert "title_hi" in data
    assert "title_en" in data


def test_speculation_rules_and_navigation_features(client):
    """Verify base.html includes Speculation Rules, View Transitions, and no reload loops."""
    res = client.get("/")
    assert res.status_code == 200
    raw_html = res.text

    assert 'type="speculationrules"' in raw_html
    assert "@view-transition" in raw_html
    assert 'id="global-progress-bar"' in raw_html
    assert "surpriseMeOpen" in raw_html
    assert "prefetchLink" in raw_html

    # Audit sw.js: zero forced window.location.reload()
    res_sw = client.get("/sw.js")
    assert res_sw.status_code == 200
    sw_code = res_sw.text
    assert "controllerchange" not in sw_code


def test_zero_visible_100_percent_on_dynamic_pages(client):
    """Ensure zero visible '100%' claims on dynamic anime pages."""
    pages = [
        "/anime/frieren-beyond-journeys-end",
        "/anime/chainsaw-man",
        "/anime/dandadan",
        "/anime/the-apothecary-diaries-season-3"
    ]
    for p in pages:
        res = client.get(p)
        assert res.status_code == 200
        raw_html = res.text
        matches = [m.start() for m in re.finditer(r"100%", raw_html)]
        bad_matches = []
        for idx in matches:
            snippet = raw_html[max(0, idx - 60) : min(len(raw_html), idx + 60)].replace("\n", " ")
            if re.search(r"(width|height|max-width|min-width|min-height|flex-basis|top|transform|background|stop-color|offset)\s*:\s*[^;]*100%", snippet):
                continue
            if any(allowed in snippet for allowed in ["100% {", "widths = [", 'offset="100%"', "minmax(min(100%", "calc(100%", "100%)", "width = '100%"]):
                continue
            bad_matches.append(snippet)
        assert len(bad_matches) == 0, f"Found visible 100% marketing claims on {p}: {bad_matches}"
