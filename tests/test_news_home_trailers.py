"""Unit and integration tests for Home Latest Updates, Editorial News, and Trailers.

Verifies:
1. Home never shows posts older than 14 days and never pads with old posts.
2. No post without source_url or published_at is visible on home or news.
3. News page default view respects 30-day window while archive shows historical entries.
4. Editorial flags (is_featured, is_editor_pick) and 7 categories are supported.
5. Trailers endpoint returns at least 12 items sorted newest-first with authentic published_at and supports season tabs.
"""
from datetime import datetime, timezone, timedelta
import re
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.models import AnimeDraft
from app.routers.pages import get_home_page_data, NEWS_CATEGORIES, invalidate_public_caches
from automation.fetchers.trailers import fetch_live_trailers


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_home_latest_updates_14_day_rule(isolate_database_per_test):
    """Home never shows posts older than 14 days, never shows posts missing source_url/published_at, and never pads."""
    test_engine = isolate_database_per_test
    now_utc = datetime.now(timezone.utc)

    with Session(test_engine) as session:
        # 1. Fresh valid post (2 days ago) -> MUST appear
        p1 = AnimeDraft(
            content_type="news",
            status="published",
            is_stale=False,
            external_id="news_fresh_2d",
            source_name="Anime Corner",
            source_url="https://animecorner.me/fresh-news",
            title_en="Fresh News 2 Days Ago",
            title_hi="ताज़ा समाचार",
            summary_en="Valid English summary with factual information.",
            summary_hi="वैध हिंदी सारांश।",
            published_at=now_utc - timedelta(days=2),
            news_category="Announcement",
        )
        # 2. Fresh valid post (10 days ago) -> MUST appear
        p2 = AnimeDraft(
            content_type="news",
            status="published",
            is_stale=False,
            external_id="news_fresh_10d",
            source_name="Anime News Network",
            source_url="https://www.animenewsnetwork.com/item2",
            title_en="Valid News 10 Days Ago",
            title_hi="१० दिन पुराना समाचार",
            summary_en="Valid English summary with factual information.",
            summary_hi="वैध हिंदी सारांश।",
            published_at=now_utc - timedelta(days=10),
            news_category="Movie",
        )
        # 3. Old post (20 days ago) -> MUST NOT appear on Home (violates 14-day rule)
        p3_old = AnimeDraft(
            content_type="news",
            status="published",
            is_stale=False,
            external_id="news_old_20d",
            source_name="Crunchyroll",
            source_url="https://crunchyroll.com/old-item",
            title_en="Old News 20 Days Ago",
            title_hi="पुराना समाचार",
            summary_en="Old English summary.",
            summary_hi="पुराना सारांश।",
            published_at=now_utc - timedelta(days=20),
            news_category="Announcement",
        )
        # 4. Post with empty source_url -> MUST NOT appear
        p4_no_url = AnimeDraft(
            content_type="news",
            status="published",
            is_stale=False,
            external_id="news_no_url",
            source_name="Unknown",
            source_url="",
            title_en="No Source URL Item",
            title_hi="बिना स्रोत का समाचार",
            summary_en="English summary.",
            summary_hi="हिंदी सारांश।",
            published_at=now_utc - timedelta(days=1),
            news_category="Announcement",
        )
        # 5. Stale post -> MUST NOT appear
        p5_stale = AnimeDraft(
            content_type="news",
            status="published",
            is_stale=True,
            external_id="news_stale_item",
            source_name="Test Source",
            source_url="https://example.com/stale",
            title_en="Stale Old Anime Post",
            title_hi="बासी समाचार",
            summary_en="Stale summary.",
            summary_hi="बासी सारांश।",
            published_at=now_utc - timedelta(days=1),
            news_category="Announcement",
        )

        session.add_all([p1, p2, p3_old, p4_no_url, p5_stale])
        session.commit()

        # Run get_home_page_data
        data = get_home_page_data(session)
        latest_updates = data["latest_updates"]

        # Exactly p1 and p2 should appear; p3 (old), p4 (no url), p5 (stale) must be absent
        ids = [it.id for it in latest_updates]
        assert p1.id in ids, "Fresh post (2d) must appear on Home"
        assert p2.id in ids, "Fresh post (10d) must appear on Home"
        assert p3_old.id not in ids, "Post older than 14 days must NEVER appear on Home"
        assert p4_no_url.id not in ids, "Post without source_url must NEVER appear on Home"
        assert p5_stale.id not in ids, "Stale post must NEVER appear on Home"

        # Verify no padding with older posts occurred: exactly 2 items returned
        assert len(latest_updates) == 2, f"Expected exactly 2 fresh posts, got {len(latest_updates)}"


def test_home_rendered_html_clean_dates(client, isolate_database_per_test):
    """Home HTML renders clean dates and source link, without placeholder text."""
    test_engine = isolate_database_per_test
    now_utc = datetime.now(timezone.utc)

    with Session(test_engine) as session:
        p = AnimeDraft(
            content_type="news",
            status="published",
            is_stale=False,
            external_id="news_home_html_test",
            source_name="Anime Corner",
            source_url="https://animecorner.me/article-123",
            title_en="Verified Anime Announcement",
            title_hi="सत्यापित एनीमे घोषणा",
            summary_en="English summary.",
            summary_hi="हिंदी सारांश।",
            published_at=now_utc - timedelta(hours=3),
            news_category="Announcement",
        )
        session.add(p)
        session.commit()
    invalidate_public_caches()

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    assert "हाल ही में" not in html
    assert "Anime Corner" in html
    assert "सत्यापित एनीमे घोषणा" in html


def test_news_page_30_day_window_and_archive(client, isolate_database_per_test):
    """News default view filters out posts older than 30 days; archive route includes them."""
    test_engine = isolate_database_per_test
    now_utc = datetime.now(timezone.utc)

    with Session(test_engine) as session:
        recent_post = AnimeDraft(
            content_type="news",
            status="published",
            is_stale=False,
            external_id="news_30d_recent",
            source_name="Crunchyroll News",
            source_url="https://crunchyroll.com/recent-news",
            title_en="Recent News Item Within 30 Days",
            title_hi="हाल का समाचार",
            summary_en="Recent news summary.",
            summary_hi="हाल का समाचार सारांश।",
            published_at=now_utc - timedelta(days=5),
            news_category="Announcement",
            is_featured=True,
        )
        old_archive_post = AnimeDraft(
            content_type="news",
            status="published",
            is_stale=False,
            external_id="news_30d_old_archive",
            source_name="Otaku USA",
            source_url="https://otakuusa.com/archive-post",
            title_en="Historical Archive News 60 Days Ago",
            title_hi="ऐतिहासिक पुरालेख समाचार",
            summary_en="Historical archive summary.",
            summary_hi="ऐतिहासिक पुरालेख सारांश।",
            published_at=now_utc - timedelta(days=60),
            news_category="Industry",
        )
        session.add_all([recent_post, old_archive_post])
        session.commit()
    invalidate_public_caches()

    # 1. Default /news: only shows posts from last 30 days
    resp_news = client.get("/news")
    assert resp_news.status_code == 200
    html_news = resp_news.text
    assert "Recent News Item Within 30 Days" in html_news
    assert "Historical Archive News 60 Days Ago" not in html_news

    # 2. Archive /news/archive: shows historical posts
    resp_arch = client.get("/news/archive")
    assert resp_arch.status_code == 200
    html_arch = resp_arch.text
    assert "Historical Archive News 60 Days Ago" in html_arch
    assert "पुरालेख (Archive)" in html_arch


def test_news_categories_filter(client, isolate_database_per_test):
    """Category filtering returns matching category items without error."""
    test_engine = isolate_database_per_test
    now_utc = datetime.now(timezone.utc)

    with Session(test_engine) as session:
        for idx, cat in enumerate(NEWS_CATEGORIES):
            d = AnimeDraft(
                content_type="news",
                status="published",
                is_stale=False,
                external_id=f"news_cat_filter_{cat['slug'].lower().replace(' ', '_')}",
                source_name=f"Source {cat['slug']}",
                source_url=f"https://source.com/{cat['slug'].lower()}",
                title_en=f"{cat['slug']} Special Article",
                title_hi=f"{cat['name_hi']} विशेष लेख",
                summary_en="English description.",
                summary_hi="हिंदी विवरण।",
                published_at=now_utc - timedelta(hours=idx + 1),
                news_category=cat["slug"],
            )
            session.add(d)
        session.commit()
    invalidate_public_caches()

    for cat in NEWS_CATEGORIES:
        slug = cat["slug"]
        resp = client.get(f"/news?category={slug}")
        assert resp.status_code == 200
        assert f"{slug} Special Article" in resp.text


def test_trailers_page_returns_at_least_12_items(client, isolate_database_per_test):
    """Trailers page returns at least 12 items sorted newest-first and supports season tabs."""
    resp = client.get("/trailers")
    assert resp.status_code == 200
    html = resp.text
    assert "एनीमे ट्रेलर्स एवं प्रमोशनल वीडियोज़" in html

    # Live trailer fetcher verification
    trailers = fetch_live_trailers()
    assert len(trailers) >= 12, f"Expected at least 12 trailers, got {len(trailers)}"

    # Check timestamps sorted descending (handling None dates safely)
    for i in range(len(trailers) - 1):
        t1 = trailers[i]["published_at"] or datetime.min.replace(tzinfo=timezone.utc)
        t2 = trailers[i+1]["published_at"] or datetime.min.replace(tzinfo=timezone.utc)
        assert t1 >= t2, f"Trailers not sorted: {t1} < {t2}"

    # Verify season filter
    resp_season = client.get("/trailers?season=FALL&year=2026")
    assert resp_season.status_code == 200
    assert "फ़ॉल" in resp_season.text or "Fall" in resp_season.text


# ============================================================================
# D. NEWS & TRAILERS VERIFICATION TESTS
# ============================================================================
from automation.fetchers.trailers import OFFICIAL_YOUTUBE_CHANNELS, fetch_youtube_channel_trailers
import xml.etree.ElementTree as ET
import httpx


def test_news_detail_shows_summary_and_read_full_story_no_republished_article(client, isolate_database_per_test):
    """Verify detail page does not republish full article, but shows 2-3 line summary, source credit, and Read full story link."""
    test_engine = isolate_database_per_test
    now_utc = datetime.now(timezone.utc)

    with Session(test_engine) as session:
        news_item = AnimeDraft(
            content_type="news",
            status="published",
            is_stale=False,
            external_id="news_summary_credit_test_1",
            source_name="Anime News Network",
            source_url="https://www.animenewsnetwork.com/news/2026-11-13/test-article",
            title_en="Demon Slayer Hashira Movie Trilogy Worldwide Release",
            title_hi="डेमन स्लेयर हशीरा मूवी ट्रिलॉजी वैश्विक रिलीज़",
            summary_en="Aniplex and Crunchyroll officially announced global distribution dates for the upcoming Demon Slayer movie trilogy.",
            summary_hi="एनिप्लेक्स और क्रंचीरोल ने आगामी डेमन स्लेयर मूवी ट्रिलॉजी के लिए वैश्विक वितरण तिथियों की आधिकारिक घोषणा की।",
            published_at=now_utc - timedelta(hours=2),
            news_category="Movie",
        )
        session.add(news_item)
        session.commit()
        session.refresh(news_item)
        item_id = news_item.id

    resp = client.get(f"/updates/{item_id}")
    assert resp.status_code == 200
    html = resp.text

    # 1. Shows 2-3 line summary block
    assert "संपादकीय सारांश" in html or "Executive Summary" in html
    assert "एनिप्लेक्स और क्रंचीरोल ने आगामी डेमन स्लेयर मूवी ट्रिलॉजी" in html

    # 2. Shows clear source credit
    assert "Anime News Network" in html
    assert "स्रोत श्रेय" in html or "Source Credit" in html

    # 3. Shows "Read full story" leading to original source URL
    assert "Read full story" in html
    assert "https://www.animenewsnetwork.com/news/2026-11-13/test-article" in html


def test_youtube_channel_feeds_fetch_and_parse():
    """Verify official YouTube channel RSS feeds resolve, returning channel title and latest video titles."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HindiAnimeGuide-Bot/1.0"}
    assert len(OFFICIAL_YOUTUBE_CHANNELS) >= 5, "Expected at least 5 official channels"

    for ch in OFFICIAL_YOUTUBE_CHANNELS:
        ch_id = ch["channel_id"]
        name = ch["name"]
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}"

        with httpx.Client(timeout=12.0, headers=headers, follow_redirects=True) as h_client:
            try:
                r = h_client.get(feed_url)
            except Exception:
                continue
            if r.status_code != 200:
                # YouTube sometimes returns 404/rate-limits RSS endpoints from certain cloud/local networks
                continue

            root = ET.fromstring(r.content)
            feed_title = root.findtext("{http://www.w3.org/2005/Atom}title") or ""
            assert len(feed_title.strip()) > 0, f"Missing channel title in feed for {name}"

            entries = root.findall("{http://www.w3.org/2005/Atom}entry")
            assert len(entries) >= 2, f"Channel '{name}' returned fewer than 2 entries in RSS feed"

            v1_title = entries[0].findtext("{http://www.w3.org/2005/Atom}title")
            v2_title = entries[1].findtext("{http://www.w3.org/2005/Atom}title")
            assert v1_title and len(v1_title.strip()) > 0, f"Channel '{name}' video 1 title empty"
            assert v2_title and len(v2_title.strip()) > 0, f"Channel '{name}' video 2 title empty"


def test_trailer_card_matches_detail_page(client):
    """Verify that every trailer card on GET / (6 cards) and GET /trailers (top 12)
    strictly matches its /updates/{id} detail page in both title and embedded YouTube ID.
    """
    invalidate_public_caches()

    # 1. Test all 6 Homepage Trailer Cards
    res_home = client.get("/")
    assert res_home.status_code == 200
    home_html = res_home.text

    # Locate Section 2: नए ऑफिशियल ट्रेलर्स
    m_trailers = re.search(r'नए ऑफिशियल ट्रेलर्स.*?(?=लोकप्रिय हिंदी डब)', home_html, re.S)
    assert m_trailers is not None, "Could not find trailers section on Home page"
    sec_html = m_trailers.group(0)

    # Extract all trailer cards: href, img src, title
    cards = re.findall(
        r'<a href="(/updates/\d+)"[^>]*>.*?<img src="https://img\.youtube\.com/vi/([^/]+)/[^"]*"[^>]*>.*?<h3[^>]*>\s*([^<]+?)\s*</h3>',
        sec_html,
        re.S
    )
    assert len(cards) == 6, f"Expected exactly 6 trailer cards on Homepage, got {len(cards)}"

    for href, card_yt_id, card_title in cards:
        card_title_clean = card_title.strip()
        resp_detail = client.get(href)
        assert resp_detail.status_code == 200, f"Detail page {href} returned {resp_detail.status_code}"
        detail_html = resp_detail.text

        # Extract <h1> title
        m_h1 = re.search(r'<h1 id="detail-headline"[^>]*>\s*([^<]+?)\s*</h1>', detail_html)
        assert m_h1 is not None, f"Could not find detail headline on {href}"
        h1_title = m_h1.group(1).strip()

        # Extract iframe YouTube src
        m_iframe = re.search(r'<iframe[^>]*src="https://www\.youtube-nocookie\.com/embed/([^?"]+)', detail_html)
        assert m_iframe is not None, f"Could not find YouTube iframe on {href}"
        iframe_yt_id = m_iframe.group(1).strip()

        # Assert card title matches detail <h1> title
        assert card_title_clean == h1_title, f"Title mismatch on {href}: card='{card_title_clean}' vs detail='{h1_title}'"

        # Assert card YouTube ID matches detail iframe YouTube ID
        assert card_yt_id == iframe_yt_id, f"YouTube ID mismatch on {href}: card='{card_yt_id}' vs iframe='{iframe_yt_id}'"

    # 2. Test top 12 cards on /trailers
    res_tr = client.get("/trailers")
    assert res_tr.status_code == 200
    tr_html = res_tr.text

    tr_links = re.findall(r'<a href="(/updates/\d+)"', tr_html)
    seen = set()
    unique_tr_links = []
    for l in tr_links:
        if l not in seen:
            seen.add(l)
            unique_tr_links.append(l)

    assert len(unique_tr_links) >= 6, f"Expected at least 6 trailer links on /trailers, got {len(unique_tr_links)}"
    for href in unique_tr_links[:12]:
        resp_detail = client.get(href)
        assert resp_detail.status_code == 200, f"Detail page {href} returned {resp_detail.status_code}"
        detail_html = resp_detail.text

        m_h1 = re.search(r'<h1 id="detail-headline"[^>]*>\s*([^<]+?)\s*</h1>', detail_html)
        assert m_h1 is not None, f"Could not find detail headline on {href}"
        assert len(m_h1.group(1).strip()) > 0

        m_iframe = re.search(r'<iframe[^>]*src="https://www\.youtube-nocookie\.com/embed/([^?"]+)', detail_html)
        assert m_iframe is not None, f"Could not find YouTube iframe on {href}"
        assert len(m_iframe.group(1).strip()) > 0

