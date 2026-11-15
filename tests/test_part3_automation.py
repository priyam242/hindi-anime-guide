"""Tests for Part 3 of 4: Multi-Source Automation Pipeline and Awards handling."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.models import AnimeDraft, Admin1
from app.auth import create_access_token, get_password_hash
from app.totp import generate_csrf_token
from automation.fetchers.livechart_rss import _normalize_rss_item
from automation.fetchers.manga import _normalize_mangadex_chapter
from automation.fetchers.natalie_rss import fetch_natalie_news
from automation.fetchers.oricon_rss import fetch_oricon_news
from automation.fetchers.awards import create_award_fact_entry
from automation.rewriter import rewrite_anime_update
from automation.saver import count_today_drafts_by_source, save_draft
from automation.run_all import run_source_pipeline


def test_livechart_rss_normalization():
    """Verify LiveChart.me RSS item normalization into factual dictionary."""
    rss_xml = """
    <item>
        <title>"Chainsaw Man" Season 2 announced with teaser visual</title>
        <link>https://www.livechart.me/headlines/9999</link>
        <guid>https://www.livechart.me/headlines/9999</guid>
        <pubDate>Thu, 12 Nov 2026 12:00:00 +0000</pubDate>
        <description><![CDATA[Official production confirmed by Studio MAPPA.]]></description>
    </item>
    """
    elem = ET.fromstring(rss_xml.strip())
    with patch("automation.fetchers.livechart_rss.search_anilist_cover", return_value="https://cdn.anilist.co/cover.jpg"):
        res = _normalize_rss_item(elem, resolve_covers=True)

    assert res is not None
    assert res["content_type"] == "news"
    assert res["source_name"] == "LiveChart.me"
    assert res["external_id"] == "livechart_headline_9999"
    assert "Chainsaw Man" in res["title_en"]
    assert res["image_url"] == "https://cdn.anilist.co/cover.jpg"


def test_mangadex_chapter_normalization():
    """Verify MangaDex chapter normalization into factual dictionary."""
    chapter_data = {
        "id": "1111-2222-3333-4444",
        "attributes": {
            "chapter": "1131",
            "title": "Loki's Tale",
            "readableAt": "2026-11-12T10:00:00+00:00",
        },
        "relationships": [
            {
                "type": "manga",
                "id": "manga-9999",
                "attributes": {
                    "title": {"en": "One Piece"}
                }
            }
        ]
    }
    with patch("automation.fetchers.manga.search_anilist_cover", return_value="https://cdn.anilist.co/onepiece.jpg"):
        res = _normalize_mangadex_chapter(chapter_data, resolve_covers=True)

    assert res is not None
    assert res["content_type"] == "manga"
    assert res["source_name"] == "MangaDex"
    assert res["external_id"] == "mangadex_ch_1111-2222-3333-4444"
    assert "One Piece Chapter 1131" in res["title_en"]
    assert res["chapter_number"] == "1131"
    assert res["image_url"] == "https://cdn.anilist.co/onepiece.jpg"


def test_natalie_and_oricon_audited_skips():
    """Verify that audited sources Natalie and Oricon return empty lists with warnings."""
    natalie_items = fetch_natalie_news(limit=5)
    assert natalie_items == []

    oricon_items = fetch_oricon_news(limit=5)
    assert oricon_items == []


def test_awards_fact_entry_and_draft_rewriter():
    """Verify award fact normalization and bilingual synthesis."""
    award_entry = create_award_fact_entry(
        award_name="Crunchyroll Anime Awards 2026",
        category_name="Anime of the Year",
        winner_or_nominee="Frieren: Beyond Journey's End",
        award_date="2026-03-02",
        source_name="Crunchyroll Official",
        source_url="https://www.crunchyroll.com/animeawards",
        resolve_covers=False,
    )

    assert award_entry["content_type"] == "award"
    assert "award_crunchyroll_anime_awards_2026_anime_of_the_year" in award_entry["external_id"]
    assert "Frieren" in award_entry["title_en"]

    # Rewrite offline fallback
    with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
        rewritten = rewrite_anime_update(award_entry, allow_offline_fallback=True)
    assert rewritten is not None
    assert "Frieren" in rewritten["title_en"]
    assert "Anime of the Year" in rewritten["title_en"]
    assert "विजेता" in rewritten["summary_hi"] or "सम्मान" in rewritten["summary_hi"]


def test_admin_manual_award_entry_flow(isolate_database_per_test):
    """Verify admin form submission to create a manual award draft."""
    engine = isolate_database_per_test
    client = TestClient(app)

    # 1. Create admin user
    with Session(engine) as session:
        admin = Admin1(
            email="award_admin@animeguide.com",
            name="Award Admin",
            hashed_password=get_password_hash("SuperSecret123!"),
            is_active=True,
            totp_enabled=True,
        )
        session.add(admin)
        session.commit()
        session.refresh(admin)

    token = create_access_token({"sub": "award_admin@animeguide.com", "name": "Award Admin", "user_type": "admin"})
    client.cookies.set("admin_token", token)

    # 2. GET /admin/drafts/create-award
    get_resp = client.get("/admin/drafts/create-award")
    assert get_resp.status_code == 200
    assert "Add Anime Award Entry" in get_resp.text

    csrf_id = get_resp.cookies.get("csrf_token_id") or "test_csrf_id"
    csrf_token = generate_csrf_token(csrf_id)
    client.cookies.set("csrf_token_id", csrf_id)

    # 3. POST /admin/drafts/create-award
    payload = {
        "csrf_token": csrf_token,
        "award_name": "Tokyo Anime Award Festival 2026",
        "category_name": "Animation of the Year (Television)",
        "winner_or_nominee": "Dungeon Meshi",
        "award_date": "2026-02-15",
        "source_name": "TAAF Official",
        "source_url": "https://animefestival.jp/en/award/winners/",
        "raw_details": "Awarded for outstanding visual direction and culinary animation.",
    }

    with patch("automation.rewriter.rewrite_anime_update", return_value={
        "title_en": "Dungeon Meshi Wins Animation of the Year at TAAF 2026",
        "title_hi": "Dungeon Meshi ने TAAF 2026 में सर्वश्रेष्ठ एनीमेशन का पुरस्कार जीता",
        "summary_en": "Dungeon Meshi was officially honored at the Tokyo Anime Award Festival 2026 for its stellar creative direction.",
        "summary_hi": "Dungeon Meshi को टोक्यो एनीमे अवार्ड फेस्टिवल 2026 में सर्वश्रेष्ठ टेलीविजन एनीमेशन का सम्मान दिया गया।",
    }):
        post_resp = client.post("/admin/drafts/create-award", data=payload, follow_redirects=False)

    assert post_resp.status_code == 303
    redirect_url = post_resp.headers.get("location")
    assert "/admin/drafts/" in redirect_url
    assert "/edit" in redirect_url

    # Verify staged in DB
    with Session(engine) as session:
        draft = session.exec(select(AnimeDraft).where(AnimeDraft.content_type == "award")).first()
        assert draft is not None
        assert draft.status == "draft"
        assert draft.source_name == "TAAF Official"
        assert "Dungeon Meshi" in draft.title_en
