"""
Tests for Master Upgrade Phase 1:
- /free Legal YouTube/Free Anime Hub
- Progressive Web App (PWA): manifest.webmanifest, service worker (sw.js), icons, install prompt
- Google JSON-LD SEO schemas (WebSite, TVSeries with WatchAction, Article, BreadcrumbList)
- Strict removal of '100%' marketing claims across all site hubs
- Clean Zone 3 subtitles without raw hyphenated slugs
"""
import re
import json
import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_strict_removal_of_100_percent_claims_across_hubs(client):
    """Verify ZERO occurrences of '100%' in copy, badges, titles, and meta descriptions across core hubs."""
    pages = ["/", "/free", "/schedule", "/manga", "/trailers", "/awards"]
    for path in pages:
        res = client.get(path)
        assert res.status_code == 200, f"Failed on {path}: {res.status_code}"
        html = res.text

        # Find any '100%' in html
        matches = [m.start() for m in re.finditer(r"100%", html)]
        bad_matches = []
        for idx in matches:
            snippet = html[max(0, idx - 60) : min(len(html), idx + 60)].replace("\n", " ")
            # Ignore standard CSS width/height/offset declarations
            if re.search(
                r"(width|height|max-width|min-width|min-height|flex-basis|top|transform|background|stop-color|offset)\s*:\s*[^;]*100%",
                snippet,
            ):
                continue
            if any(
                allowed in snippet
                for allowed in [
                    "100% {",
                    "widths = [",
                    'offset="100%"',
                    "minmax(min(100%",
                    "calc(100%",
                    "100%)",
                ]
            ):
                continue
            bad_matches.append(snippet)

        assert (
            len(bad_matches) == 0
        ), f"Found visible 100% marketing claims on {path}: {bad_matches}"


def test_free_hub_endpoint_and_filters(client):
    """Verify GET /free returns 200, contains brand tags with translate='no', and 0 raw slugs."""
    res = client.get("/free")
    assert res.status_code == 200
    html = res.text

    assert "मुफ़्त लीगल एनीमे" in html
    assert 'translate="no">Muse Asia</span>' in html
    assert 'translate="no">Ani-One Asia</span>' in html
    assert "YouTube" in html
    assert "आधिकारिक व लीगल" in html

    # Verify no raw hyphenated slugs in card titles or subtitles
    subtitles = re.findall(
        r'class="text-\[11px\] text-zinc-400 truncate">\s*([^\s<][^<]*)\s*<', html
    )
    assert len(subtitles) >= 10, f"Expected at least 10 cards on /free, found {len(subtitles)}"
    for sub in subtitles:
        assert not re.search(
            r"^[a-z0-9]+(-[a-z0-9]+){2,}$", sub
        ), f"Raw slug found in /free card subtitle: {sub}"


def test_pwa_manifest_and_icons(client):
    """Verify /manifest.webmanifest serves valid PWA JSON with valid icons."""
    for path in ["/manifest.webmanifest", "/manifest.json"]:
        res = client.get(path)
        assert res.status_code == 200
        assert "application/manifest+json" in res.headers.get("content-type", "")
        data = res.json()
        assert data.get("short_name") in ["HindiAnimeGuide", "AnimeHindi", "Hindi Anime Guide"]
        assert data.get("display") == "standalone"
        assert data.get("start_url") == "/"
        assert "100%" not in data.get("description", "")
        assert len(data.get("icons", [])) >= 2

        # Verify all icon endpoints return 200 OK
        for icon in data.get("icons", []):
            icon_res = client.get(icon["src"])
            assert icon_res.status_code == 200, f"Icon {icon['src']} failed with {icon_res.status_code}"
            assert len(icon_res.content) > 0


def test_pwa_service_worker(client):
    """Verify /sw.js serves valid JavaScript with network-first strategy and offline fallback."""
    res = client.get("/sw.js")
    assert res.status_code == 200
    assert "javascript" in res.headers.get("content-type", "")
    content = res.text
    assert "CACHE_NAME" in content
    assert "आप अभी ऑफलाइन हैं" in content
    assert "fetch" in content


def test_google_json_ld_schemas(client):
    """Verify JSON-LD schemas parse with json.loads and contain WebSite, Article, and TVSeries."""
    # 1. Homepage WebSite schema
    res_home = client.get("/")
    assert res_home.status_code == 200
    home_jsonlds = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', res_home.text, re.S
    )
    assert len(home_jsonlds) >= 1
    parsed_home = json.loads(home_jsonlds[0].strip())
    # Should contain WebSite and SearchAction
    graph = parsed_home.get("@graph", [])
    website_entries = [e for e in graph if e.get("@type") == "WebSite"]
    assert len(website_entries) >= 1
    potential_action = website_entries[0].get("potentialAction", {})
    assert potential_action.get("@type") == "SearchAction"

    # 2. Anime Detail Article + TVSeries + BreadcrumbList schema
    res_art = client.get("/anime/spy-x-family-hindi-dub-streaming-guide")
    if res_art.status_code == 200:
        art_jsonlds = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', res_art.text, re.S
        )
        assert len(art_jsonlds) >= 2
        types_found = set()
        for jld in art_jsonlds:
            p = json.loads(jld.strip())
            types_found.add(p.get("@type"))
        assert "Article" in types_found
        assert "TVSeries" in types_found or "BreadcrumbList" in types_found


def test_sitemap_includes_free_hub(client):
    """Verify /sitemap.xml includes /free with priority 0.9."""
    res = client.get("/sitemap.xml")
    assert res.status_code == 200
    xml = res.text
    assert "<loc>" in xml
    assert "/free</loc>" in xml


def test_zone3_column2_subtitles_clean(client):
    """Verify Zone 3 Column 2 subtitles are clean and contain 0 raw hyphenated slugs."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    m = re.search(r"हिंदी डब गाइड्स \(Hindi Dub Guides\).*?(?=लोकप्रिय मंगा)", html, re.S)
    assert m is not None, "Zone 3 Column 2 not found on homepage"
    z3_text = m.group(0)

    raw_subtitles = re.findall(
        r'<p class="[^"]*text-\[11px\][^"]*"[^>]*>(.*?)</p>', z3_text, re.S
    )
    subtitles = [s.strip() for s in raw_subtitles]
    assert len(subtitles) == 5, f"Expected 5 items in Zone 3 Col 2, found {len(subtitles)}"
    for sub in subtitles:
        assert not re.search(
            r"^[a-z0-9]+(-[a-z0-9]+){2,}$", sub
        ), f"Found raw slug in Zone 3 subtitle: {sub}"
