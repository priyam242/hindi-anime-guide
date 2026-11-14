"""
Data-Quality Pytest Suite (verifies content integrity, not just HTML structure).

Requirements:
1. Fail if any page/draft contains placeholder like 'schedule update.'
2. Fail if any title contains a literal-translation blacklist string (e.g. 'एक टुकड़ा', 'चेनसॉ आदमी', 'ज़ासूस एक्स परिवार', etc.)
3. Fail if any trailer published_at is in the future
4. Fail if any trailer title was constructed rather than fetched from YouTube
5. Fail if any allowed trailer has an untranslated Japanese title
6. Fail if any manga next-volume / next-release date is in the past
"""
import re
import json
from datetime import datetime, timezone, timedelta
import pytest
from sqlmodel import Session, select
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_session
from app.models import AnimeDraft, Article, AwardEntry

LITERAL_BLACKLIST = [
    "एक टुकड़ा",
    "ओशी नहीं को",
    "ज़ासूस एक्स परिवार",
    "जासूस एक्स परिवार",
    "जासूस x",
    "ज़ासूस x",
    "चेनसॉ आदमी",
    "भीतर के रंग",
]

JAPANESE_CHAR_REGEX = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
CONSTRUCTED_TRAILER_PATTERN = re.compile(r"^.+\s*-\s*Official Main Trailer PV$", re.IGNORECASE)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_no_placeholder_descriptions_in_db_and_schedule(client):
    """Fail if any draft or schedule card contains a placeholder like 'schedule update.'"""
    with next(get_session()) as session:
        drafts = session.exec(select(AnimeDraft).where(AnimeDraft.is_stale == False)).all()
        for d in drafts:
            for field in [d.summary_en, d.summary_hi, d.description]:
                if field:
                    assert "schedule update." not in field.lower(), (
                        f"Placeholder 'schedule update.' found in draft ID {d.id} ({d.title_en}): {field}"
                    )

    # Check rendered schedule page
    resp = client.get("/schedule")
    assert resp.status_code == 200
    assert "schedule update." not in resp.text.lower(), "Placeholder found on rendered /schedule page"


def test_no_literal_translation_blacklist_titles():
    """Fail if any anime, manga, news, or award title contains a blacklisted machine-translation."""
    with next(get_session()) as session:
        # 1. AnimeDraft titles
        drafts = session.exec(select(AnimeDraft).where(AnimeDraft.is_stale == False)).all()
        for d in drafts:
            for bad_word in LITERAL_BLACKLIST:
                if d.title_hi:
                    assert bad_word not in d.title_hi, (
                        f"Literal translation blacklist '{bad_word}' found in AnimeDraft id={d.id} title_hi: '{d.title_hi}'"
                    )

        # 2. Article titles
        articles = session.exec(select(Article)).all()
        for a in articles:
            for bad_word in LITERAL_BLACKLIST:
                if a.title:
                    assert bad_word not in a.title, f"Blacklist '{bad_word}' found in Article id={a.id} title: '{a.title}'"
                if a.anime_title_hi:
                    assert bad_word not in a.anime_title_hi, (
                        f"Blacklist '{bad_word}' found in Article id={a.id} anime_title_hi: '{a.anime_title_hi}'"
                    )

        # 3. Award Entries
        entries = session.exec(select(AwardEntry)).all()
        for e in entries:
            for bad_word in LITERAL_BLACKLIST:
                if e.winner_title_hi:
                    assert bad_word not in e.winner_title_hi, (
                        f"Blacklist '{bad_word}' found in AwardEntry id={e.id} winner_title_hi: '{e.winner_title_hi}'"
                    )


def test_no_trailer_published_at_in_future():
    """Fail if any trailer published_at is in the future."""
    now_utc = datetime.now(timezone.utc)
    margin = timedelta(minutes=15)  # allow slight clock drift
    with next(get_session()) as session:
        trailers = session.exec(
            select(AnimeDraft).where(
                AnimeDraft.content_type == "trailer",
                AnimeDraft.is_stale == False,
            )
        ).all()
        for t in trailers:
            if t.published_at:
                pub = t.published_at
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                assert pub <= now_utc + margin, (
                    f"Trailer ID {t.id} '{t.title_en}' has future published_at {pub} (now is {now_utc})"
                )


def test_trailer_titles_fetched_from_youtube_not_constructed():
    """Fail if any allowed trailer title has the fake circular pattern '<Title> - Official Main Trailer PV'."""
    with next(get_session()) as session:
        trailers = session.exec(
            select(AnimeDraft).where(
                AnimeDraft.content_type == "trailer",
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
            )
        ).all()
        for t in trailers:
            assert not CONSTRUCTED_TRAILER_PATTERN.match(t.title_en or ""), (
                f"Trailer ID {t.id} has constructed title: '{t.title_en}'. Must use real YouTube metadata."
            )


def test_no_untranslated_japanese_title_on_allowed_trailers():
    """Fail if an allowed trailer displays untranslated Japanese kanji/kana in its primary title."""
    with next(get_session()) as session:
        trailers = session.exec(
            select(AnimeDraft).where(
                AnimeDraft.content_type == "trailer",
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
            )
        ).all()
        for t in trailers:
            title = t.title_en or ""
            assert not JAPANESE_CHAR_REGEX.search(title), (
                f"Allowed trailer ID {t.id} has untranslated Japanese text in title_en: '{title}'"
            )


def test_no_manga_next_volume_date_in_past():
    """Fail if any manga next-volume or next-release date is in the past."""
    now_utc = datetime.now(timezone.utc)
    with next(get_session()) as session:
        mangas = session.exec(
            select(AnimeDraft).where(
                AnimeDraft.content_type == "manga",
                AnimeDraft.is_stale == False,
            )
        ).all()
        for m in mangas:
            if getattr(m, "next_release", None):
                nr = m.next_release
                if nr.tzinfo is None:
                    nr = nr.replace(tzinfo=timezone.utc)
                assert nr >= now_utc - timedelta(days=1), (
                    f"Manga ID {m.id} '{m.title_en}' has past next_release date {nr} (now is {now_utc})"
                )
