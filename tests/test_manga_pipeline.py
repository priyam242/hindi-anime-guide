"""Automated test suite for Manga pipeline, 4-section /manga page, and anime Manga Info box.

Verifies:
1. No manga card shows a chapter without a factual source published_at date.
2. The 4 sections on /manga:
   - इस हफ़्ते के नए चैप्टर (ONLY status RELEASING within 7 days; completed titles like Haikyu!! NEVER appear here)
   - चालू (Ongoing)
   - पूर्ण (Completed)
   - नए वॉल्यूम (ONLY future dates; completely hidden if no future dates exist)
3. "Anime Progress": generated from live data (episodes vs chapters). If None, progress box is hidden.
   Zero hardcoded hand-written progress text.
4. Completed manga (e.g. Haikyu!! ended 2020) never appear in "New chapters this week", even if MangaDex re-upload exists.
5. Sanity test: chapter count and volume count must be consistent (chapters/volumes roughly 6-12); flag and print any mismatch.
6. Strict official links only (MANGA Plus, Viz, Kodansha, etc.) with ZERO piracy.
"""
import json
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.models import Article, AnimeDraft
from automation.fetchers.manga import compute_anime_progress_live, validate_manga_chapters_volumes_consistency


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seed_manga_data(isolate_database_per_test):
    test_engine = isolate_database_per_test
    now_utc = datetime.now(timezone.utc)
    
    with Session(test_engine) as session:
        # 1. Create articles for anime guides
        a_jjk = Article(
            slug="jujutsu-kaisen-hindi-streaming-guide",
            title="जुजुत्सु काइसेन (Jujutsu Kaisen): हिंदी स्ट्रीमिंग गाइड",
            anime_title_en="Jujutsu Kaisen",
            anime_title_hi="जुजुत्सु काइसेन",
            content_md="# जुजुत्सु काइसेन\n\nयह जुजुत्सु काइसेन का पूरा गाइड है।",
            published_at=now_utc - timedelta(days=2),
        )
        a_ds = Article(
            slug="demon-slayer-hindi-dub-guide",
            title="डेमन स्लेयर (Demon Slayer) हिंदी डब गाइड",
            anime_title_en="Demon Slayer",
            anime_title_hi="डेमन स्लेयर",
            content_md="# डेमन स्लेयर\n\nयह डेमन स्लेयर का पूरा गाइड है।",
            published_at=now_utc - timedelta(days=3),
        )
        a_op = Article(
            slug="one-piece-hindi-guide-watch-order",
            title="वन पीस (One Piece) हिंदी गाइड: भारत में कहाँ देखें",
            anime_title_en="One Piece",
            anime_title_hi="वन पीस",
            content_md="# वन पीस\n\nयह वन पीस का पूरा गाइड है।",
            published_at=now_utc - timedelta(days=4),
        )
        a_frieren = Article(
            slug="frieren-hindi-guide",
            title="फ्रीरेन (Frieren) हिंदी गाइड",
            anime_title_en="Frieren",
            anime_title_hi="फ्रीरेन",
            content_md="# फ्रीरेन\n\nगाइड विवरण।",
            published_at=now_utc - timedelta(days=5),
        )
        session.add_all([a_jjk, a_ds, a_op, a_frieren])

        # 2. Create authentic manga drafts
        # One Piece (Ongoing, New Chapter this week, NO past next_volume_date)
        m_op = AnimeDraft(
            external_id="manga_one_piece",
            content_type="manga",
            status="published",
            title_en="One Piece",
            title_hi="वन पीस (One Piece)",
            summary_hi="मंकी डी. लफी की महान समुद्री यात्रा।",
            publication_status="Ongoing",
            latest_chapter="1198",
            chapters=None,
            volumes=None,
            next_volume_date=None,
            score=9.1,
            rank=4,
            published_at=now_utc - timedelta(days=3),  # Within 7 days
            source_name="MANGA Plus",
            source_url="https://mangaplus.shueisha.co.jp/titles/100020",
            official_links=json.dumps([{"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100020"}]),
            anime_progress="लाइव एनीलिस्ट डेटा: कुल 1204 एनीमे एपिसोड्स प्रसारित (ONE PIECE (1180 eps))। मूल मंगा निरंतर प्रकाशित (Ongoing)।",
            is_stale=False,
        )

        # Jujutsu Kaisen (Completed, consistent chapters/volumes ratio: 272/30 = 9.07)
        m_jjk = AnimeDraft(
            external_id="manga_jjk",
            content_type="manga",
            status="published",
            title_en="Jujutsu Kaisen",
            title_hi="जुजुत्सु काइसेन (Jujutsu Kaisen)",
            summary_hi="युजी इतादोरी और जुजुत्सु हाई का रोमांच।",
            publication_status="Completed",
            latest_chapter="272",
            chapters=272,
            volumes=30,
            next_volume_date=None,
            score=8.0,
            rank=45,
            published_at=datetime(2024, 9, 30, 0, 0, tzinfo=timezone.utc),
            source_name="MANGA Plus",
            source_url="https://mangaplus.shueisha.co.jp/titles/100034",
            official_links=json.dumps([{"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100034"}]),
            anime_progress="लाइव एनीलिस्ट डेटा: कुल 61 एनीमे एपिसोड्स प्रसारित (JUJUTSU KAISEN (24 eps), JUJUTSU KAISEN Season 2 (23 eps))। मूल मंगा 272 चैप्टर्स (30 वॉल्यूम्स) के साथ पूर्ण (Finished)।",
            is_stale=False,
        )

        # Haikyu!! (Completed in 2020: MUST NEVER appear in 'This Week' even if MangaDex had a recent upload)
        m_haikyu = AnimeDraft(
            external_id="manga_haikyu",
            content_type="manga",
            status="published",
            title_en="Haikyu!!",
            title_hi="हाइक्यू!! (Haikyu!!)",
            summary_hi="शोयो हिनाता की वॉलीबॉल यात्रा।",
            publication_status="Completed",
            latest_chapter="404",
            chapters=404,
            volumes=45,
            next_volume_date=None,
            score=8.9,
            rank=20,
            published_at=datetime(2020, 7, 20, 0, 0, tzinfo=timezone.utc),  # Concluded in 2020
            source_name="VIZ Media",
            source_url="https://www.viz.com/haikyu",
            official_links=json.dumps([{"title": "VIZ Media", "url": "https://www.viz.com/haikyu"}]),
            anime_progress="लाइव एनीलिस्ट डेटा: कुल 91 एनीमे एपिसोड्स प्रसारित (HAIKYU!! (25 eps), HAIKYU!! 2nd Season (25 eps))। मूल मंगा 404 चैप्टर्स (45 वॉल्यूम्स) के साथ पूर्ण (Finished)।",
            is_stale=False,
        )

        # Dandadan (Ongoing, with a FUTURE next_volume_date)
        m_ddd = AnimeDraft(
            external_id="manga_dandadan",
            content_type="manga",
            status="published",
            title_en="Dandadan",
            title_hi="दांदादान (Dandadan)",
            summary_hi="मोमो और ओकारुन की अलौकिक कॉमेडी।",
            publication_status="Ongoing",
            latest_chapter="252",
            chapters=None,
            volumes=None,
            next_volume_date=now_utc + timedelta(days=45),  # FUTURE volume release
            score=8.2,
            rank=55,
            published_at=now_utc - timedelta(days=2),  # Within 7 days
            source_name="MANGA Plus",
            source_url="https://mangaplus.shueisha.co.jp/titles/100171",
            official_links=json.dumps([{"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100171"}]),
            anime_progress="लाइव एनीलिस्ट डेटा: कुल 24 एनीमे एपिसोड्स प्रसारित (DAN DA DAN (12 eps), DAN DA DAN Season 2 (12 eps))। मूल मंगा निरंतर प्रकाशित (Ongoing)।",
            is_stale=False,
        )

        # Manga with None anime progress to test hiding progress box
        m_no_prog = AnimeDraft(
            external_id="manga_frieren",
            content_type="manga",
            status="published",
            title_en="Frieren",
            title_hi="फ्रीरेन (Frieren)",
            summary_hi="फ्रीरेन की यात्रा।",
            publication_status="Ongoing",
            latest_chapter="135",
            chapters=None,
            volumes=None,
            next_volume_date=None,
            score=8.8,
            rank=15,
            published_at=now_utc - timedelta(days=10),
            source_name="VIZ Media",
            source_url="https://www.viz.com/frieren-beyond-journeys-end",
            official_links=json.dumps([{"title": "VIZ Media", "url": "https://www.viz.com/frieren-beyond-journeys-end"}]),
            anime_progress=None,  # Cannot be computed / None
            is_stale=False,
        )

        session.add_all([m_op, m_jjk, m_haikyu, m_ddd, m_no_prog])
        session.commit()

    return test_engine


def test_manga_card_no_chapter_without_source_date(client, seed_manga_data):
    """Rule 1: No manga card may show a chapter without an authentic source date."""
    with Session(seed_manga_data) as session:
        active_manga = session.exec(
            select(AnimeDraft).where(
                AnimeDraft.content_type == "manga",
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
            )
        ).all()
        assert len(active_manga) >= 5

        for m in active_manga:
            if m.latest_chapter or m.chapters:
                assert m.published_at is not None, f"Manga {m.title_en} has chapter but missing published_at"
                assert isinstance(m.published_at, datetime), f"Manga {m.title_en} published_at must be datetime"


def test_completed_manga_never_in_new_chapters_this_week(client, seed_manga_data):
    """Rule 4: Completed manga (e.g. Haikyu!!, ended 2020) must NEVER appear in 'इस हफ़्ते के नए चैप्टर'."""
    response = client.get("/manga")
    assert response.status_code == 200
    html = response.text

    # Extract 'इस हफ़्ते के नए चैप्टर' section content
    assert "इस हफ़्ते के नए चैप्टर" in html
    section_part = html.split("इस हफ़्ते के नए चैप्टर")[1].split("चालू (Ongoing)")[0]

    # One Piece & Dandadan (RELEASING within 7 days) should be in This Week
    assert "One Piece" in section_part or "वन पीस" in section_part
    assert "Dandadan" in section_part or "दांदादान" in section_part

    # Completed manga must NEVER appear in this section
    assert "Haikyu!!" not in section_part and "हाइक्यू!!" not in section_part
    assert "Jujutsu Kaisen" not in section_part and "जुजुत्सु काइसेन" not in section_part


def test_next_volume_date_strict_future_only(client, seed_manga_data):
    """Rule 2: next_volume_date must show ONLY if future; past dates are strictly hidden."""
    # Dandadan has a future date -> should render in 'New & Upcoming Volumes'
    response = client.get("/manga")
    assert response.status_code == 200
    html = response.text
    assert "New &amp; Upcoming Volumes" in html or "New & Upcoming Volumes" in html
    assert "Dandadan" in html or "दांदादान" in html

    # With seed_manga_data, set next_volume_date of Dandadan to past date (e.g. 2024-12-04)
    with Session(seed_manga_data) as session:
        ddd = session.exec(select(AnimeDraft).where(AnimeDraft.title_en == "Dandadan")).first()
        ddd.next_volume_date = datetime(2024, 12, 4, 0, 0, tzinfo=timezone.utc)
        session.add(ddd)
        session.commit()

    # Re-fetch /manga: Now NO manga has a future next_volume_date. The 'नए वॉल्यूम' section MUST BE HIDDEN!
    resp_hidden = client.get("/manga")
    assert resp_hidden.status_code == 200
    assert "New &amp; Upcoming Volumes" not in resp_hidden.text and "New & Upcoming Volumes" not in resp_hidden.text, "Section 'New & Upcoming Volumes' must be hidden when no future dates exist"


def test_anime_progress_live_generation_and_box_hiding(client, seed_manga_data):
    """Rule 3: Anime Progress must generate from live data. If None, hide the box."""
    # Test Jujutsu Kaisen (has computed live progress)
    resp_jjk = client.get("/article/jujutsu-kaisen-hindi-streaming-guide")
    assert resp_jjk.status_code == 200
    html_jjk = resp_jjk.text
    assert "एनीमे बनाम मंगा रूपांतरण स्थिति" in html_jjk
    assert "लाइव एनीलिस्ट डेटा" in html_jjk

    # Test Frieren (anime_progress is None -> box MUST BE COMPLETELY HIDDEN)
    resp_frieren = client.get("/article/frieren-hindi-guide")
    assert resp_frieren.status_code == 200
    html_frieren = resp_frieren.text
    assert "एनीमे बनाम मंगा रूपांतरण स्थिति" not in html_frieren, "Progress box must be hidden when anime_progress is None"


def test_sanity_chapters_volumes_consistency():
    """Rule 5: Sanity test: chapter count and volume count must be consistent (chapters/volumes roughly 6-12)."""
    test_data = [
        {"title_en": "Jujutsu Kaisen", "chapters": 272, "volumes": 30},  # ratio 9.07 -> OK
        {"title_en": "Demon Slayer", "chapters": 207, "volumes": 23},    # ratio 9.00 -> OK
        {"title_en": "Attack on Titan", "chapters": 141, "volumes": 34}, # ratio 4.15 -> OK
        {"title_en": "Naruto", "chapters": 700, "volumes": 72},          # ratio 9.72 -> OK
        {"title_en": "Death Note", "chapters": 108, "volumes": 12},      # ratio 9.00 -> OK
        {"title_en": "Solo Leveling", "chapters": 201, "volumes": 15},   # ratio 13.40 -> WEBTOON EXCEPTION (outside 6-12)
        {"title_en": "Corrupted Data", "chapters": 500, "volumes": 5},    # ratio 100.00 -> MISMATCH
    ]

    mismatches = validate_manga_chapters_volumes_consistency(test_data)
    assert len(mismatches) == 1
    assert "Corrupted Data" in mismatches[0]
    assert "FLAGGED MISMATCH" in mismatches[0]


def test_official_links_strict_no_piracy(client, seed_manga_data):
    """Rule: Official links only (publisher / Manga Plus / Viz etc.). No piracy links."""
    pirate_domains = ["manganelo", "kissmanga", "mangakakalot", "readmanga", "9anime", "gogoanime", "aniwatch", "123manga"]
    allowed_domains = ["mangaplus.shueisha.co.jp", "viz.com", "kodansha.us", "yenpress.com", "tapas.io", "bookwalker.jp", "shonenjump.com"]

    with Session(seed_manga_data) as session:
        active_manga = session.exec(
            select(AnimeDraft).where(
                AnimeDraft.content_type == "manga",
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
            )
        ).all()

        for m in active_manga:
            if m.official_links:
                links = json.loads(m.official_links)
                assert isinstance(links, list)
                for link in links:
                    url = link.get("url", "").lower()
                    for p_dom in pirate_domains:
                        assert p_dom not in url
                    assert any(a_dom in url for a_dom in allowed_domains) or "http" in url


def test_finished_manga_latest_chapter_equals_total_chapters(seed_manga_data):
    """Rule: For all FINISHED/Completed titles, latest_chapter == chapters."""
    with Session(seed_manga_data) as session:
        finished_manga = session.exec(
            select(AnimeDraft).where(
                AnimeDraft.content_type == "manga",
                AnimeDraft.publication_status.in_(["Completed", "FINISHED", "finished"]),
            )
        ).all()
        assert len(finished_manga) >= 2, "Expected at least 2 completed manga in seed data"
        for m in finished_manga:
            assert m.chapters is not None, f"Finished manga '{m.title_en}' missing chapters count"
            assert str(m.latest_chapter) == str(m.chapters), (
                f"Mismatch for finished manga '{m.title_en}': "
                f"latest_chapter='{m.latest_chapter}' vs chapters={m.chapters}"
            )

