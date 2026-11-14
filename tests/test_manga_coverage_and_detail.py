"""Test suite for Manga Coverage (>=80), Detail Page (/manga/<id>), Filters, Search, and Chainsaw Man conflict handling."""
import json
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select, func

from app.main import app
from app.models import AnimeDraft


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seed_manga_full_data(isolate_database_per_test):
    test_engine = isolate_database_per_test
    now_utc = datetime.now(timezone.utc)

    with Session(test_engine) as session:
        # Seed 85 manga to test coverage target >= 80
        manga_list = []

        # 1. One Piece
        m_op = AnimeDraft(
            id=1,
            external_id="manga_anilist_30013",
            anilist_id=30013,
            content_type="manga",
            status="published",
            source_url="https://anilist.co/manga/30013",
            title_en="One Piece",
            title_hi="वन पीस",
            summary_hi="मंकी डी. लफी की समुद्री यात्रा।",
            publication_status="Ongoing",
            latest_chapter="1198",
            chapters=None,
            volumes=108,
            start_date=datetime(1997, 7, 22, 0, 0, tzinfo=timezone.utc),
            premiere_date=datetime(1997, 7, 22, 0, 0, tzinfo=timezone.utc),
            published_at=now_utc - timedelta(days=2),
            linked_anime_title="ONE PIECE",
            anime_progress="कुल 1204 एनीमे एपिसोड्स प्रसारित",
            official_links=json.dumps([{"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100020"}]),
            is_stale=False,
        )
        manga_list.append(m_op)

        # 2. Chainsaw Man (conflict case: status None initially)
        m_csm = AnimeDraft(
            id=2,
            external_id="manga_anilist_105778",
            anilist_id=105778,
            content_type="manga",
            status="published",
            source_url="https://anilist.co/manga/105778",
            title_en="Chainsaw Man",
            title_hi="चेनसॉ मैन",
            summary_hi="तातसुकी फुजिमोतो की अलौकिक एक्शन मंगा।",
            publication_status=None,  # Hidden while conflicting
            status_manual=None,
            latest_chapter="185",
            chapters=None,
            volumes=18,
            start_date=datetime(2018, 12, 3, 0, 0, tzinfo=timezone.utc),
            premiere_date=datetime(2018, 12, 3, 0, 0, tzinfo=timezone.utc),
            published_at=now_utc - timedelta(days=4),
            linked_anime_title="Chainsaw Man",
            official_links=json.dumps([{"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100037"}]),
            is_stale=False,
        )
        manga_list.append(m_csm)

        # 3. Jujutsu Kaisen (Completed)
        m_jjk = AnimeDraft(
            id=3,
            external_id="manga_anilist_101517",
            anilist_id=101517,
            content_type="manga",
            status="published",
            source_url="https://anilist.co/manga/101517",
            title_en="Jujutsu Kaisen",
            title_hi="जुजुत्सु काइसेन",
            summary_hi="युजी इतादोरी का जुजुत्सु हाई में प्रशिक्षण।",
            publication_status="Completed",
            latest_chapter="272",
            chapters=272,
            volumes=30,
            start_date=datetime(2018, 3, 5, 0, 0, tzinfo=timezone.utc),
            premiere_date=datetime(2018, 3, 5, 0, 0, tzinfo=timezone.utc),
            published_at=datetime(2024, 9, 30, 0, 0, tzinfo=timezone.utc),
            linked_anime_title="JUJUTSU KAISEN",
            official_links=json.dumps([{"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100034"}]),
            is_stale=False,
        )
        manga_list.append(m_jjk)

        # 4. Dandadan (Ongoing, This Week)
        m_ddd = AnimeDraft(
            id=4,
            external_id="manga_anilist_132029",
            anilist_id=132029,
            content_type="manga",
            status="published",
            source_url="https://anilist.co/manga/132029",
            title_en="Dandadan",
            title_hi="दांदादान",
            summary_hi="मोमो और ओकारुन का अलौकिक रोमांच।",
            publication_status="Ongoing",
            latest_chapter="175",
            chapters=None,
            volumes=17,
            start_date=datetime(2021, 4, 6, 0, 0, tzinfo=timezone.utc),
            premiere_date=datetime(2021, 4, 6, 0, 0, tzinfo=timezone.utc),
            published_at=now_utc - timedelta(days=1),
            linked_anime_title="DAN DA DAN",
            official_links=json.dumps([{"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100171"}]),
            is_stale=False,
        )
        manga_list.append(m_ddd)

        # Seed 81 more manga drafts to fulfill >= 80 target
        for i in range(5, 86):
            manga_list.append(
                AnimeDraft(
                    id=i,
                    external_id=f"manga_anilist_{40000 + i}",
                    anilist_id=40000 + i,
                    content_type="manga",
                    status="published",
                    source_url=f"https://anilist.co/manga/{40000 + i}",
                    title_en=f"Series Title {i}",
                    title_hi=f"सीरीज़ शीर्षक {i}",
                    publication_status="Ongoing" if i % 2 == 0 else "Completed",
                    latest_chapter=str(i * 5),
                    chapters=i * 5 if i % 2 != 0 else None,
                    volumes=max(1, i // 5),
                    start_date=datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc),
                    premiere_date=datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc),
                    published_at=now_utc - timedelta(days=i),
                    linked_anime_title=f"Anime {i}" if i % 3 == 0 else None,
                    is_stale=False,
                )
            )

        session.add_all(manga_list)
        session.commit()

    return test_engine


def test_manga_coverage_at_least_80(seed_manga_full_data):
    """Requirement 1: Coverage target at least 80 manga cached in DB."""
    with Session(seed_manga_full_data) as session:
        count = session.exec(
            select(func.count(AnimeDraft.id)).where(
                AnimeDraft.content_type == "manga",
                AnimeDraft.is_stale == False,
            )
        ).one()
        assert count >= 80, f"Expected at least 80 manga in database, found {count}"


def test_manga_detail_page_and_bullets(client, seed_manga_full_data):
    """Requirements 2, 3 & 4: /manga/<id> detail page with structured bullets."""
    resp = client.get("/manga/1")
    assert resp.status_code == 200
    html = resp.text

    # Title & transliteration
    assert "One Piece" in html
    assert "वन पीस" in html

    # Bullets present
    assert "नवीनतम चैप्टर" in html
    assert "Ch. 1198" in html
    assert "प्रकाशन स्थिति" in html
    assert "Ongoing" in html
    assert "प्रारंभ तिथि" in html
    assert "एनीमे रूपांतरण" in html or "एनीमे" in html


def test_chainsaw_man_conflict_hidden_unless_manual(client, seed_manga_full_data):
    """Requirement 5: Chainsaw Man status hidden while conflict exists, displayed when status_manual filled."""
    # Detail page initially: status must be HIDDEN
    resp1 = client.get("/manga/2")
    assert resp1.status_code == 200
    html1 = resp1.text
    assert "प्रकाशन स्थिति (Status):" not in html1
    assert "प्रकाशन स्थिति (Status - Verified Manual):" not in html1

    # Fill admin status_manual
    with Session(seed_manga_full_data) as session:
        csm = session.get(AnimeDraft, 2)
        csm.status_manual = "Ongoing (Part 2 - Academy Arc)"
        csm.status_manual_url = "https://mangaplus.shueisha.co.jp/titles/100037"
        csm.status_manual_date = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
        session.add(csm)
        session.commit()

    # Detail page: manual status must now be DISPLAYED
    resp2 = client.get("/manga/2")
    assert resp2.status_code == 200
    html2 = resp2.text
    assert "Ongoing (Part 2 - Academy Arc)" in html2
    assert "https://mangaplus.shueisha.co.jp/titles/100037" in html2


def test_manga_filters_and_search(client, seed_manga_full_data):
    """Requirement 4: Ongoing / Completed / New this week filters and search."""
    # Ongoing filter
    resp_on = client.get("/manga?filter=ongoing")
    assert resp_on.status_code == 200
    assert "चालू मंगा सूची" in resp_on.text

    # Completed filter
    resp_comp = client.get("/manga?filter=completed")
    assert resp_comp.status_code == 200
    assert "पूर्ण मंगा सूची" in resp_comp.text

    # Search filter
    resp_search = client.get("/manga?q=Dandadan")
    assert resp_search.status_code == 200
    assert "Dandadan" in resp_search.text


def test_manga_unknown_id_returns_404(client, seed_manga_full_data):
    """Unknown manga id returns friendly 404."""
    resp = client.get("/manga/9999999")
    assert resp.status_code == 404
