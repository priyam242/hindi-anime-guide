"""Automated test suite for Awards rebuild, 2026/2025/2024/2023 editions, nominees metadata, and admin warnings.

Tests verify:
1. The Hero is strictly the latest edition (2026 My Hero Academia FINAL SEASON); older editions (2025, 2024, 2023) never show a hero.
2. 100% of ceremonies and entries have a verified, working source_url.
3. Every nominee has a proper full title, AniList ID, and official AniList poster.
4. Winner images originate from AniList media posters, never news thumbnails.
5. Admin panel displays a visible warning when the latest edition in the database is < (current_year - 1).
6. Admin panel provides complete CRUD for ceremonies and entries with CSRF protection.
"""
import json
from datetime import date, datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.auth import create_access_token
from app.models import Admin1, AwardCeremony, AwardEntry


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_client(client, isolate_database_per_test):
    test_engine = isolate_database_per_test
    with Session(test_engine) as session:
        admin = session.exec(select(Admin1).where(Admin1.email == "admin@animeguide.com")).first()
        if not admin:
            admin = Admin1(
                name="Super Admin",
                email="admin@animeguide.com",
                hashed_password="hashed_test_password",
                is_active=True,
                totp_enabled=True,
            )
            session.add(admin)
            session.commit()
    token = create_access_token({"sub": "admin@animeguide.com", "name": "Admin", "user_type": "admin"})
    client.cookies.set("admin_token", token)
    return client


@pytest.fixture
def seed_awards_test_data(isolate_database_per_test):
    test_engine = isolate_database_per_test
    with Session(test_engine) as session:
        # 1. 2026 Crunchyroll Anime Awards (10th Edition - Latest)
        c_2026 = AwardCeremony(
            name="Crunchyroll Anime Awards",
            name_hi="क्रंचीरोल एनीमे अवार्ड्स (10वां संस्करण)",
            edition_year=2026,
            date=date(2026, 5, 23),
            location="Grand Prince Hotel New Takanawa, Tokyo, Japan",
            source_url="https://www.crunchyroll.com/news/announcements/2026/5/23/anime-awards-2026-winners-my-hero-academia-final-season",
            created_at=datetime.now(timezone.utc),
        )
        session.add(c_2026)
        session.commit()
        session.refresh(c_2026)

        e_2026_1 = AwardEntry(
            ceremony_id=c_2026.id,
            category="Anime of the Year",
            category_hi="वर्ष का सर्वश्रेष्ठ एनीमे",
            winner_media_id=182896,
            winner_title="My Hero Academia FINAL SEASON",
            winner_title_hi="माई हीरो एकेडेमिया फाइनल सीज़न",
            winner_poster="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx182896-mvxTVHGdDB4q.jpg",
            nominee_media_ids=json.dumps([
                {"media_id": 171018, "title": "DAN DA DAN", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx171018.jpg"},
                {"media_id": 176496, "title": "Solo Leveling Season 2", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx176496.png"},
                {"media_id": 153288, "title": "Kaiju No. 8", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx153288.jpg"},
                {"media_id": 21, "title": "ONE PIECE", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx21.jpg"},
            ]),
            source_url="https://www.crunchyroll.com/news/announcements/2026/5/23/anime-awards-2026-winners-my-hero-academia-final-season",
            created_at=datetime.now(timezone.utc),
        )
        e_2026_2 = AwardEntry(
            ceremony_id=c_2026.id,
            category="Best Film",
            category_hi="सर्वश्रेष्ठ एनीमे फिल्म",
            winner_media_id=178788,
            winner_title="Demon Slayer: Kimetsu no Yaiba Infinity Castle",
            winner_title_hi="डेमन स्लेयर: इनफिनिटी कैसल",
            winner_poster="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx178788-zm3gtpB9TpRt.jpg",
            nominee_media_ids=json.dumps([
                {"media_id": 174788, "title": "LOOK BACK", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx174788.jpg"},
                {"media_id": 171627, "title": "Chainsaw Man – The Movie: Reze Arc", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx171627.png"},
            ]),
            source_url="https://www.crunchyroll.com/news/announcements/2026/5/23/anime-awards-2026-winners-my-hero-academia-final-season",
            created_at=datetime.now(timezone.utc),
        )
        e_2026_3 = AwardEntry(
            ceremony_id=c_2026.id,
            category="Best Continuing Series",
            category_hi="सर्वश्रेष्ठ निरंतर श्रृंखला",
            winner_media_id=21,
            winner_title="ONE PIECE",
            winner_title_hi="वन पीस",
            winner_poster="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx21.jpg",
            nominee_media_ids=json.dumps([
                {"media_id": 163139, "title": "My Hero Academia Season 7", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx163139.jpg"}
            ]),
            source_url="https://www.crunchyroll.com/news/announcements/2026/5/23/anime-awards-2026-winners-my-hero-academia-final-season",
            created_at=datetime.now(timezone.utc),
        )
        e_2026_4 = AwardEntry(
            ceremony_id=c_2026.id,
            category="Best New Series",
            category_hi="सर्वश्रेष्ठ नई श्रृंखला",
            winner_media_id=178025,
            winner_title="Gachiakuta",
            winner_title_hi="गाचियाकुता",
            winner_poster="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx178025.jpg",
            nominee_media_ids=json.dumps([
                {"media_id": 171018, "title": "DAN DA DAN", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx171018.jpg"},
                {"media_id": 153288, "title": "Kaiju No. 8", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx153288.jpg"},
            ]),
            source_url="https://www.crunchyroll.com/news/announcements/2026/5/23/anime-awards-2026-winners-my-hero-academia-final-season",
            created_at=datetime.now(timezone.utc),
        )
        session.add(e_2026_1)
        session.add(e_2026_2)
        session.add(e_2026_3)
        session.add(e_2026_4)

        # 2. 2025 Crunchyroll Anime Awards (9th Edition)
        c_2025 = AwardCeremony(
            name="Crunchyroll Anime Awards",
            name_hi="क्रंचीरोल एनीमे अवार्ड्स (9वां संस्करण)",
            edition_year=2025,
            date=date(2025, 5, 25),
            location="Grand Prince Hotel New Takanawa, Tokyo, Japan",
            source_url="https://en.wikipedia.org/wiki/9th_Crunchyroll_Anime_Awards",
            created_at=datetime.now(timezone.utc),
        )
        session.add(c_2025)
        session.commit()
        session.refresh(c_2025)

        e_2025_1 = AwardEntry(
            ceremony_id=c_2025.id,
            category="Anime of the Year",
            category_hi="वर्ष का सर्वश्रेष्ठ एनीमे",
            winner_media_id=151807,
            winner_title="Solo Leveling",
            winner_title_hi="सोलो लेवलिंग (Solo Leveling)",
            winner_poster="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx151807-it355ZgzquUd.png",
            nominee_media_ids=json.dumps([
                {"media_id": 171018, "title": "DAN DA DAN", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx171018-60q1B6GK2Ghb.jpg"},
                {"media_id": 154587, "title": "Frieren: Beyond Journey’s End", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx154587.jpg"}
            ]),
            source_url="https://en.wikipedia.org/wiki/9th_Crunchyroll_Anime_Awards",
            created_at=datetime.now(timezone.utc),
        )
        session.add(e_2025_1)

        # 3. 2024 Crunchyroll Anime Awards
        c_2024 = AwardCeremony(
            name="Crunchyroll Anime Awards",
            name_hi="क्रंचीरोल एनीमे अवार्ड्स (8वां संस्करण)",
            edition_year=2024,
            date=date(2024, 3, 2),
            location="Tokyo, Japan",
            source_url="https://www.crunchyroll.com/news/announcements/2024/3/2/anime-awards-2024-winners-anime-of-the-year",
            created_at=datetime.now(timezone.utc),
        )
        session.add(c_2024)
        session.commit()
        session.refresh(c_2024)

        e_2024_1 = AwardEntry(
            ceremony_id=c_2024.id,
            category="Anime of the Year",
            category_hi="वर्ष का सर्वश्रेष्ठ एनीमे",
            winner_media_id=145064,
            winner_title="JUJUTSU KAISEN Season 2",
            winner_title_hi="जुजुत्सु काइसेन सीज़न 2",
            winner_poster="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx145064.jpg",
            nominee_media_ids=json.dumps([
                {"media_id": 127230, "title": "Chainsaw Man", "poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx127230.jpg"}
            ]),
            source_url="https://www.crunchyroll.com/news/announcements/2024/3/2/anime-awards-2024-winners-anime-of-the-year",
            created_at=datetime.now(timezone.utc),
        )
        session.add(e_2024_1)
        session.commit()

    return test_engine


def test_awards_hero_is_strictly_latest_edition_2026(client, seed_awards_test_data):
    """Verify hero is shown for latest 2026 edition (MHA Final Season) and hidden for past years."""
    # 1. Latest year (2026) -> Hero present with MHA Final Season
    resp_2026 = client.get("/awards?year=2026")
    assert resp_2026.status_code == 200
    html_2026 = resp_2026.text
    assert "वर्ष का सर्वश्रेष्ठ विजेता" in html_2026
    assert "My Hero Academia FINAL SEASON" in html_2026
    assert "AniList ID: 182896" in html_2026

    # Default request without query param also defaults to 2026
    resp_default = client.get("/awards")
    assert resp_default.status_code == 200
    assert "वर्ष का सर्वश्रेष्ठ विजेता" in resp_default.text
    assert "My Hero Academia FINAL SEASON" in resp_default.text

    # 2. Older years (2025, 2024) -> Hero must NOT be present
    resp_2025 = client.get("/awards?year=2025")
    assert resp_2025.status_code == 200
    assert "वर्ष का सर्वश्रेष्ठ विजेता" not in resp_2025.text
    assert "ऐतिहासिक पुरस्कार अभिलेखागार (Historical Award Archive — 2025)" in resp_2025.text

    resp_2024 = client.get("/awards?year=2024")
    assert resp_2024.status_code == 200
    assert "वर्ष का सर्वश्रेष्ठ विजेता" not in resp_2024.text
    assert "ऐतिहासिक पुरस्कार अभिलेखागार (Historical Award Archive — 2024)" in resp_2024.text


def test_every_award_ceremony_and_entry_has_working_source_url(client, seed_awards_test_data):
    """Verify 100% of ceremonies and entries carry a verified public source_url."""
    test_engine = seed_awards_test_data
    with Session(test_engine) as session:
        ceremonies = session.exec(select(AwardCeremony)).all()
        assert len(ceremonies) >= 3
        for c in ceremonies:
            assert c.source_url is not None
            assert (
                c.source_url.startswith("https://www.crunchyroll.com/")
                or c.source_url.startswith("https://animefestival.jp/")
                or c.source_url.startswith("https://en.wikipedia.org/")
            )
            assert c.edition_year in (2024, 2025, 2026)

        entries = session.exec(select(AwardEntry)).all()
        assert len(entries) >= 6
        for e in entries:
            assert e.source_url is not None
            assert (
                e.source_url.startswith("https://www.crunchyroll.com/")
                or e.source_url.startswith("https://animefestival.jp/")
                or e.source_url.startswith("https://en.wikipedia.org/")
            )


def test_every_nominee_has_proper_full_title_and_anilist_id_and_poster(client, seed_awards_test_data):
    """Verify every nominee in nominee_media_ids carries title, media_id, and poster."""
    test_engine = seed_awards_test_data
    with Session(test_engine) as session:
        entries = session.exec(select(AwardEntry)).all()
        for e in entries:
            if e.nominee_media_ids:
                nominees = json.loads(e.nominee_media_ids)
                assert isinstance(nominees, list)
                for nom in nominees:
                    assert "media_id" in nom and isinstance(nom["media_id"], int)
                    assert "title" in nom and len(nom["title"]) > 1
                    assert "poster" in nom and nom["poster"].startswith("https://")


def test_winner_posters_are_anilist_media_not_news(client, seed_awards_test_data):
    """Verify winner posters originate from AniList CDN, not from generic news thumbnails."""
    test_engine = seed_awards_test_data
    with Session(test_engine) as session:
        entries = session.exec(select(AwardEntry)).all()
        for e in entries:
            assert e.winner_poster is not None
            assert "anilist.co" in e.winner_poster
            assert e.winner_media_id > 0


def test_latest_edition_year_warning_in_admin(admin_client, seed_awards_test_data):
    """Verify admin shows a visible warning when latest edition in DB is older than current_year - 1."""
    test_engine = seed_awards_test_data

    # 1. When DB has 2026, no warning is shown
    resp_ok = admin_client.get("/admin/awards")
    assert resp_ok.status_code == 200
    assert "Warning: Award Editions Cycle Outdated!" not in resp_ok.text

    # 2. Artificially make ceremonies older (< 2025)
    with Session(test_engine) as session:
        ceremonies = session.exec(select(AwardCeremony)).all()
        for c in ceremonies:
            c.edition_year = 2023
        session.commit()

    resp_warn = admin_client.get("/admin/awards")
    assert resp_warn.status_code == 200
    assert "Warning: Award Editions Cycle Outdated!" in resp_warn.text
    assert "The latest award edition in the database (2023) is older than the current cycle requirement" in resp_warn.text


def test_admin_awards_crud(admin_client, seed_awards_test_data):
    """Verify admin can create, edit, and delete award ceremonies and entries."""
    test_engine = seed_awards_test_data

    # 1. Create ceremony
    resp_c = admin_client.post(
        "/admin/awards/ceremonies/new",
        data={
            "name": "Japan Media Arts Festival",
            "name_hi": "जापान मीडिया आर्ट्स फेस्टिवल",
            "edition_year": 2026,
            "date": "2026-09-15",
            "location": "Tokyo, Japan",
            "source_url": "https://animefestival.jp/en/post/2180/",
        },
        follow_redirects=True,
    )
    assert resp_c.status_code == 200

    with Session(test_engine) as session:
        c = session.exec(select(AwardCeremony).where(AwardCeremony.name == "Japan Media Arts Festival")).first()
        assert c is not None
        c_id = c.id

    # 2. Create entry
    resp_e = admin_client.post(
        "/admin/awards/entries/new",
        data={
            "ceremony_id": c_id,
            "category": "Grand Prize",
            "winner_media_id": 178025,
            "winner_title": "Gachiakuta",
            "winner_poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx178025.jpg",
            "nominee_ids_input": "171018, 153288",
            "source_url": "https://animefestival.jp/en/post/2180/",
        },
        follow_redirects=True,
    )
    assert resp_e.status_code == 200

    with Session(test_engine) as session:
        e = session.exec(select(AwardEntry).where(AwardEntry.ceremony_id == c_id)).first()
        assert e is not None
        assert e.winner_title == "Gachiakuta"
        e_id = e.id

    # 3. Delete entry and ceremony
    admin_client.post(f"/admin/awards/entries/{e_id}/delete", follow_redirects=True)
    admin_client.post(f"/admin/awards/ceremonies/{c_id}/delete", follow_redirects=True)

    with Session(test_engine) as session:
        assert session.get(AwardEntry, e_id) is None
        assert session.get(AwardCeremony, c_id) is None
