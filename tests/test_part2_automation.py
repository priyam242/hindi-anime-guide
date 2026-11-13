"""Integration tests for Part 2 of Content Automation:
- Admin drafts management & permissions
- Public news, schedule, manga, awards, and update detail pages
- Legal attribution links (rel='nofollow noopener')
- SEO meta tags and sitemap.xml validation
- Strict exclusion of unpublished drafts from public views
"""
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.models import AnimeDraft, Admin1
from app.auth import create_access_token


@pytest.fixture
def client():
    return TestClient(app)


def test_part2_drafts_flow(client: TestClient, isolate_database_per_test):
    test_engine = isolate_database_per_test

    # 0. Seed admin user in isolated test database
    with Session(test_engine) as session:
        admin_user = session.exec(select(Admin1).where(Admin1.email == "admin@animeguide.com")).first()
        if not admin_user:
            admin_user = Admin1(
                name="Admin",
                email="admin@animeguide.com",
                hashed_password="dummy_password_hash",
                is_active=True,
            )
            session.add(admin_user)
            session.commit()
            session.refresh(admin_user)

    token = create_access_token({"sub": "admin@animeguide.com", "name": "Admin", "user_type": "admin"})
    client.cookies.set("admin_token", token)

    # 1. Create 3 sample drafts in database
    with Session(test_engine) as session:
        d1 = AnimeDraft(
            content_type="news",
            status="draft",
            source_name="Comic Natalie",
            source_url="https://natalie.mu/comic/news/555001",
            external_id="test_p2_news_1",
            title_en="Attack on Titan 10th Anniversary Special Event Announced",
            title_hi="अटैक ऑन टाइटन 10वीं वर्षगांठ विशेष कार्यक्रम की घोषणा",
            summary_en="The production committee for Attack on Titan has officially revealed plans for a special 10th anniversary commemoration showcase with voice actors.",
            summary_hi="अटैक ऑन टाइटन निर्माण समिति ने अपनी 10वीं वर्षगांठ के उपलक्ष्य में एक विशेष कार्यक्रम की आधिकारिक घोषणा की है।",
            image_url="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx16498-C6FPmWm59CyP.jpg"
        )
        d2 = AnimeDraft(
            content_type="manga",
            status="draft",
            source_name="MangaUpdates",
            source_url="https://www.mangaupdates.com/series/777002",
            external_id="test_p2_manga_2",
            title_en="Jujutsu Kaisen Manga Enters Climax Arc with New Volume Release",
            title_hi="जुजुत्सु काइसेन मंगा नए वॉल्यूम के साथ निर्णायक मोड़ में पहुंचा",
            summary_en="Gege Akutami serialized manga series Jujutsu Kaisen continues with confirmed volume schedules.",
            summary_hi="गेगे अकुतामी की प्रसिद्ध मंगा सीरीज़ जुजुत्सु काइसेन का नया वॉल्यूम शेड्यूल घोषित हुआ है।",
            image_url="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx113415-bbBWj4pEFseh.jpg"
        )
        d3 = AnimeDraft(
            content_type="award",
            status="draft",
            source_name="Crunchyroll Awards",
            source_url="https://www.crunchyroll.com/animeawards",
            external_id="test_p2_award_3",
            title_en="Anime Awards 2026 Nominees List Revealed for Global Voting",
            title_hi="एनीमे अवार्ड्स 2026: वैश्विक मतदान के लिए नामांकनों की घोषणा",
            summary_en="Nominees across top anime categories were officially announced for the 2026 season.",
            summary_hi="एनीमे ऑफ द ईयर सहित प्रमुख श्रेणियों के नामांकनों की घोषणा वैश्विक मतदान के लिए की गई है।",
            image_url="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx21-ELSYx3yMPcKM.jpg"
        )
        session.add_all([d1, d2, d3])
        session.commit()
        session.refresh(d1)
        session.refresh(d2)
        session.refresh(d3)
        d1_id = d1.id
        d2_id = d2.id
        d3_id = d3.id

    # 2. Check that all 3 appear in protected admin drafts list
    res_admin = client.get("/admin/drafts")
    assert res_admin.status_code == 200
    assert "Attack on Titan 10th Anniversary" in res_admin.text
    assert "Jujutsu Kaisen Manga Enters" in res_admin.text
    assert "Anime Awards 2026 Nominees" in res_admin.text

    # Verify anonymous user is rejected from admin drafts
    unauth_client = TestClient(app)
    res_unauth = unauth_client.get("/admin/drafts")
    assert res_unauth.status_code in (401, 303)

    # 3. Publish ONLY ONE draft (d1_id: news) via admin publish action
    res_pub = client.post(f"/admin/drafts/{d1_id}/publish", follow_redirects=True)
    assert res_pub.status_code == 200

    with Session(test_engine) as session:
        check_d1 = session.get(AnimeDraft, d1_id)
        check_d2 = session.get(AnimeDraft, d2_id)
        check_d3 = session.get(AnimeDraft, d3_id)
        assert check_d1.status == "published"
        assert check_d1.published_at is not None
        assert check_d2.status == "draft"
        assert check_d2.published_at is None
        assert check_d3.status == "draft"
        assert check_d3.published_at is None

    # 4. Confirm Public /news shows the published news, and excludes unpublished drafts
    res_news = unauth_client.get("/news")
    assert res_news.status_code == 200
    assert "Attack on Titan" in res_news.text or "अटैक ऑन टाइटन" in res_news.text
    assert "Jujutsu Kaisen Manga Enters" not in res_news.text
    assert "Anime Awards 2026 Nominees" not in res_news.text

    # 5. Confirm /sitemap.xml includes published item and strictly excludes unpublished drafts
    res_sitemap = unauth_client.get("/sitemap.xml")
    assert res_sitemap.status_code == 200
    assert f"/updates/{d1_id}" in res_sitemap.text
    assert f"/updates/{d2_id}" not in res_sitemap.text
    assert f"/updates/{d3_id}" not in res_sitemap.text
    assert "/news" in res_sitemap.text
    assert "/schedule" in res_sitemap.text

    # 6. Detail page for published update: 200 OK with attribution rel="nofollow noopener"
    res_detail_pub = unauth_client.get(f"/updates/{d1_id}")
    assert res_detail_pub.status_code == 200
    assert "Comic Natalie" in res_detail_pub.text
    assert 'rel="nofollow noopener"' in res_detail_pub.text
    assert "https://natalie.mu/comic/news/555001" in res_detail_pub.text
    assert "NewsArticle" in res_detail_pub.text

    # 7. Detail page for unpublished draft: 404 for anonymous public users
    res_detail_draft = unauth_client.get(f"/updates/{d2_id}")
    assert res_detail_draft.status_code == 404

    # 8. Unpublish action test: unpublish d1 and verify it immediately disappears from /sitemap.xml and /news
    res_unpub = client.post(f"/admin/drafts/{d1_id}/unpublish", follow_redirects=True)
    assert res_unpub.status_code == 200

    res_news_after = unauth_client.get("/news")
    assert "Attack on Titan 10th Anniversary" not in res_news_after.text

    res_sitemap_after = unauth_client.get("/sitemap.xml")
    assert f"/updates/{d1_id}" not in res_sitemap_after.text


def test_schedule_and_bulk_actions(client: TestClient, isolate_database_per_test):
    test_engine = isolate_database_per_test

    with Session(test_engine) as session:
        admin_user = session.exec(select(Admin1).where(Admin1.email == "admin@animeguide.com")).first()
        if not admin_user:
            admin_user = Admin1(name="Admin", email="admin@animeguide.com", hashed_password="dummy", is_active=True)
            session.add(admin_user)
            session.commit()

        # Create schedule item
        sch = AnimeDraft(
            content_type="schedule",
            status="published",
            published_at=datetime.now(timezone.utc),
            source_name="AniList",
            source_url="https://anilist.co/anime/21",
            external_id="anilist_21_ep1181_test",
            title_en="ONE PIECE Episode 1181 Release Schedule",
            title_hi="ONE PIECE एपिसोड 1181 प्रसारण शेड्यूल",
            summary_en="Episode 1181 will broadcast on 2027-01-03 14:16 UTC as confirmed by official schedules.",
            summary_hi="एपिसोड 1181 का प्रसारण 2027-01-03 14:16 UTC को आधिकारिक तौर पर होगा।",
            image_url="https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21-ELSYx3yMPcKM.jpg"
        )
        # Create draft to test bulk action
        d_bulk1 = AnimeDraft(
            content_type="news",
            status="draft",
            source_name="Test Source",
            source_url="https://example.com/test1",
            external_id="test_bulk_1",
            title_en="Bulk Draft 1",
            title_hi="बल्क ड्राफ्ट 1",
            summary_en="Summary 1",
            summary_hi="विवरण 1",
        )
        d_bulk2 = AnimeDraft(
            content_type="manga",
            status="draft",
            source_name="Test Source",
            source_url="https://example.com/test2",
            external_id="test_bulk_2",
            title_en="Bulk Draft 2",
            title_hi="बल्क ड्राफ्ट 2",
            summary_en="Summary 2",
            summary_hi="विवरण 2",
        )
        session.add_all([sch, d_bulk1, d_bulk2])
        session.commit()
        session.refresh(sch)
        session.refresh(d_bulk1)
        session.refresh(d_bulk2)
        sch_id = sch.id
        b1_id = d_bulk1.id
        b2_id = d_bulk2.id

    token = create_access_token({"sub": "admin@animeguide.com", "name": "Admin", "user_type": "admin"})
    client.cookies.set("admin_token", token)

    # 1. Test /schedule renders published schedule
    unauth = TestClient(app)
    res_sch = unauth.get("/schedule")
    assert res_sch.status_code == 200
    assert "ONE PIECE" in res_sch.text
    assert "एपिसोड 1181" in res_sch.text
    assert "IST" in res_sch.text

    # 2. Test /manga & /awards render
    res_manga = unauth.get("/manga")
    assert res_manga.status_code == 200
    res_awards = unauth.get("/awards")
    assert res_awards.status_code == 200

    # 3. Test Bulk Publish Action
    res_bulk_pub = client.post(
        "/admin/drafts/bulk-action",
        data={"draft_ids": [b1_id, b2_id], "bulk_action": "publish"},
        follow_redirects=True,
    )
    assert res_bulk_pub.status_code == 200

    with Session(test_engine) as session:
        assert session.get(AnimeDraft, b1_id).status == "published"
        assert session.get(AnimeDraft, b2_id).status == "published"

    # 4. Test Bulk Delete Action
    res_bulk_del = client.post(
        "/admin/drafts/bulk-action",
        data={"draft_ids": [b1_id, b2_id], "bulk_action": "delete"},
        follow_redirects=True,
    )
    assert res_bulk_del.status_code == 200

    with Session(test_engine) as session:
        assert session.get(AnimeDraft, b1_id) is None
        assert session.get(AnimeDraft, b2_id) is None
