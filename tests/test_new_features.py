import io
import os
from pathlib import Path
from datetime import datetime, timezone
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
import app.db as app_db
from app.models import User1, Admin1, Article, Category, Platform, Availability, Bookmark, Notification, Comment
from app.auth import get_password_hash, create_access_token
from app.storage import save_cover, delete_cover, COVERS_DIR
from scripts.seed_sample_guides import seed_all


def _make_dummy_image_bytes(width=1600, height=900, fmt="JPEG") -> bytes:
    img = Image.new("L", (width, height), 128).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def test_feature1_cover_image_storage_save_and_crop(tmp_path):
    img_bytes = _make_dummy_image_bytes(1920, 1080)
    saved_rel_path = save_cover(img_bytes, "test-naruto")
    assert saved_rel_path is not None
    assert saved_rel_path.startswith("/media/covers/article_test-naruto_")
    assert saved_rel_path.endswith(".webp")

    full_path = Path(".") / saved_rel_path.lstrip("/")
    assert full_path.exists()

    with Image.open(full_path) as im:
        assert im.format == "WEBP"
        assert im.width <= 1200
        assert im.height <= 675
        aspect = im.width / im.height
        assert 1.7 <= aspect <= 1.8

    # Clean up
    delete_cover(saved_rel_path)
    assert not full_path.exists()


def test_feature1_admin_cover_upload_and_removal():
    client = TestClient(app)

    with Session(app_db.engine) as session:
        admin = Admin1(
            name="Admin Tester",
            email="coveradmin@example.com",
            hashed_password=get_password_hash("AdminPass123!"),
            is_active=True
        )
        session.add(admin)
        session.commit()
        session.refresh(admin)

    token = create_access_token(data={"sub": admin.email, "name": admin.name, "user_type": "admin"})
    client.cookies.set("auth_token", token)

    img_data = _make_dummy_image_bytes(1200, 800)

    # 1. Create Article with Cover Upload
    create_resp = client.post(
        "/admin/articles/create",
        data={
            "title": "Solo Leveling Hindi Guide",
            "slug": "solo-leveling-cover-test",
            "category": "where-to-watch",
            "content_md": "## Solo Leveling\n\nFull Hindi guide.",
            "meta_description": "Watch Solo Leveling in Hindi dub.",
            "reading_time_minutes": "5",
        },
        files={
            "cover_file": ("test_cover.jpg", io.BytesIO(img_data), "image/jpeg"),
        },
        follow_redirects=False,
    )
    assert create_resp.status_code == 303

    saved_cover_rel = None
    art_id = None
    with Session(app_db.engine) as session:
        art = session.exec(select(Article).where(Article.slug == "solo-leveling-cover-test")).first()
        assert art is not None
        assert art.cover_image is not None
        assert art.cover_image.startswith("/media/covers/article_solo-leveling-cover-test_")
        saved_cover_rel = art.cover_image
        art_id = art.id
        assert (Path(".") / saved_cover_rel.lstrip("/")).exists()

    # 2. Edit Article and Remove Cover
    edit_resp = client.post(
        f"/admin/articles/{art_id}/edit",
        data={
            "title": "Solo Leveling Hindi Guide Updated",
            "slug": "solo-leveling-cover-test",
            "category_id": "where-to-watch",
            "content_md": "## Solo Leveling\n\nUpdated.",
            "meta_description": "Watch Solo Leveling in Hindi dub.",
            "reading_time_minutes": "6",
            "remove_cover": "1",
        },
        follow_redirects=False,
    )
    assert edit_resp.status_code == 303

    with Session(app_db.engine) as session:
        art2 = session.exec(select(Article).where(Article.slug == "solo-leveling-cover-test")).first()
        assert art2.cover_image is None
        assert not (Path(".") / saved_cover_rel.lstrip("/")).exists()


def test_feature1_public_display_cover_image_and_fallback():
    client = TestClient(app)

    img_data = _make_dummy_image_bytes(800, 450)
    rel_path = save_cover(img_data, "naruto-cover-demo")

    now = datetime.now(timezone.utc)
    with Session(app_db.engine) as session:
        art_with = Article(
            title="Naruto Shippuden With Cover",
            slug="naruto-with-cover",
            content_md="## Watch Naruto\n\nDetailed watch order.",
            category="where-to-watch",
            cover_image=rel_path,
            published_at=now,
        )
        art_without = Article(
            title="Bleach Without Cover",
            slug="bleach-without-cover",
            content_md="## Watch Bleach\n\nDetailed watch order.",
            category="where-to-watch",
            cover_image=None,
            published_at=now,
        )
        session.add(art_with)
        session.add(art_without)
        session.commit()

    try:
        # 1. Article with cover renders hero banner and card thumbnail
        resp1 = client.get("/article/naruto-with-cover")
        assert resp1.status_code == 200
        assert rel_path in resp1.text
        assert 'loading="lazy"' in resp1.text

        # 2. Article without cover does not render hero banner img
        resp2 = client.get("/article/bleach-without-cover")
        assert resp2.status_code == 200
        assert "article-hero-cover" not in resp2.text
    finally:
        delete_cover(rel_path)


def test_feature2_advanced_search_and_multi_filter_explorer():
    client = TestClient(app)
    now = datetime.now(timezone.utc)

    with Session(app_db.engine) as session:
        cat_action = Category(name="action-shonen", display_name="Action Shonen", display_order=10)
        session.add(cat_action)

        plat_crunchy = session.exec(select(Platform).where(Platform.name == "Crunchyroll")).first()
        if not plat_crunchy:
            plat_crunchy = Platform(name="Crunchyroll", display_name="Crunchyroll", official_url="https://crunchyroll.com")
            session.add(plat_crunchy)

        plat_netflix = session.exec(select(Platform).where(Platform.name == "Netflix")).first()
        if not plat_netflix:
            plat_netflix = Platform(name="Netflix", display_name="Netflix", official_url="https://netflix.com")
            session.add(plat_netflix)

        session.commit()
        session.refresh(plat_crunchy)
        session.refresh(plat_netflix)

        a1 = Article(
            title="Demon Slayer Kimetsu No Yaiba",
            slug="demon-slayer-guide",
            content_md="Tanjiro journey in Hindi",
            category="action-shonen",
            published_at=now,
            reading_time_minutes=4,
        )
        a2 = Article(
            title="Death Note Mystery Guide",
            slug="death-note-guide",
            content_md="Light Yagami notebook mystery",
            category="where-to-watch",
            published_at=now,
            reading_time_minutes=3,
        )
        session.add(a1)
        session.add(a2)
        session.commit()
        session.refresh(a1)
        session.refresh(a2)

        # a1 has Crunchyroll with Hindi Dub
        av1 = Availability(
            article_id=a1.id,
            platform_id=plat_crunchy.id,
            languages="जापानी, हिंदी डब",
            hindi_dub=True,
            status="उपलब्ध",
        )
        # a2 has Netflix Japanese only
        av2 = Availability(
            article_id=a2.id,
            platform_id=plat_netflix.id,
            languages="जापानी (उपशीर्षक)",
            hindi_dub=False,
            status="उपलब्ध",
        )
        session.add(av1)
        session.add(av2)
        session.commit()

    # 1. Query by text: "Demon Slayer"
    r_q = client.get("/explore?q=Demon")
    assert r_q.status_code == 200
    assert "Demon Slayer Kimetsu No Yaiba" in r_q.text
    assert "Death Note Mystery Guide" not in r_q.text

    # 2. Filter by Hindi Dub Only
    r_dub = client.get("/explore?hindi_dub=1")
    assert r_dub.status_code == 200
    assert "Demon Slayer Kimetsu No Yaiba" in r_dub.text
    assert "Death Note Mystery Guide" not in r_dub.text

    # 3. Filter by Platform: Netflix
    r_plat = client.get("/explore?platform=Netflix")
    assert r_plat.status_code == 200
    assert "Death Note Mystery Guide" in r_plat.text
    assert "Demon Slayer Kimetsu No Yaiba" not in r_plat.text

    # 4. Filter by Category
    r_cat = client.get("/explore?category=action-shonen")
    assert r_cat.status_code == 200
    assert "Demon Slayer Kimetsu No Yaiba" in r_cat.text
    assert "Death Note Mystery Guide" not in r_cat.text

    # 5. Sort A-Z
    r_sort = client.get("/explore?sort=az")
    assert r_sort.status_code == 200
    idx_death = r_sort.text.find("Death Note Mystery Guide")
    idx_demon = r_sort.text.find("Demon Slayer Kimetsu No Yaiba")
    assert idx_death < idx_demon

    # 6. Hindi dub badge rendered on card
    assert "हिंदी डब" in r_dub.text and "badge-dub" in r_dub.text


def test_feature3_reader_bookmark_system():
    client = TestClient(app)
    now = datetime.now(timezone.utc)

    with Session(app_db.engine) as session:
        user = User1(
            name="Otaku Reader",
            username="otakureader",
            email="otaku@example.com",
            hashed_password=get_password_hash("Otaku123!"),
            is_active=True,
        )
        session.add(user)

        art = Article(
            title="Jujutsu Kaisen Guide",
            slug="jujutsu-kaisen-bm-test",
            content_md="Gojo Satoru and Yuji Itadori.",
            category="where-to-watch",
            published_at=now,
        )
        session.add(art)
        session.commit()
        session.refresh(user)
        session.refresh(art)
        user_id = user.id
        art_id = art.id

    # 1. Guest attempt redirects to login
    guest_toggle = client.post("/article/jujutsu-kaisen-bm-test/bookmark", follow_redirects=False)
    assert guest_toggle.status_code in (302, 303, 401)
    if guest_toggle.status_code in (302, 303):
        assert "/auth/login" in guest_toggle.headers.get("location", "")

    # 2. Guest visiting /saved redirects to login
    guest_saved = client.get("/saved", follow_redirects=False)
    assert guest_saved.status_code in (302, 303)
    assert "/auth/login" in guest_saved.headers.get("location", "")

    # 3. Log in User
    token = create_access_token(data={"sub": "otaku@example.com", "name": "Otaku Reader", "user_type": "user"})
    client.cookies.set("auth_token", token)

    # 4. Toggle Bookmark ON (AJAX)
    toggle_on = client.post(
        "/article/jujutsu-kaisen-bm-test/bookmark",
        headers={"Accept": "application/json"},
    )
    assert toggle_on.status_code == 200
    data_on = toggle_on.json()
    assert data_on["success"] is True
    assert data_on["bookmarked"] is True

    # Verify Bookmark in DB
    with Session(app_db.engine) as session:
        bm = session.exec(select(Bookmark).where(Bookmark.user_id == user_id, Bookmark.article_id == art_id)).first()
        assert bm is not None

    # 5. Check /saved page lists the article
    saved_page = client.get("/saved")
    assert saved_page.status_code == 200
    assert "Jujutsu Kaisen Guide" in saved_page.text

    # 6. Article page reflects bookmarked state
    art_page = client.get("/article/jujutsu-kaisen-bm-test")
    assert art_page.status_code == 200
    assert "सेव किया गया" in art_page.text

    # 7. Toggle Bookmark OFF
    toggle_off = client.post(
        "/article/jujutsu-kaisen-bm-test/bookmark",
        headers={"Accept": "application/json"},
    )
    assert toggle_off.status_code == 200
    data_off = toggle_off.json()
    assert data_off["bookmarked"] is False

    with Session(app_db.engine) as session:
        bm_deleted = session.exec(select(Bookmark).where(Bookmark.user_id == user_id, Bookmark.article_id == art_id)).first()
        assert bm_deleted is None


def test_feature4_report_outdated_streaming_info_and_notifications():
    client = TestClient(app)
    now = datetime.now(timezone.utc)

    with Session(app_db.engine) as session:
        art = Article(
            title="Attack on Titan Watch Order",
            slug="attack-on-titan-report-test",
            content_md="Eren Yeager titans.",
            category="where-to-watch",
            published_at=now,
        )
        user = User1(
            name="Eren Fan",
            username="erenfan",
            email="eren@example.com",
            hashed_password=get_password_hash("Eren123!"),
            is_active=True,
        )
        session.add(art)
        session.add(user)
        session.commit()
        session.refresh(user)
        session.refresh(art)
        user_id = user.id
        art_id = art.id

    # 1. Report outdated streaming info
    rep_resp = client.post(
        "/article/attack-on-titan-report-test/report-outdated",
        data={"note": "Crunchyroll has updated Hindi dub episodes 1 to 24."},
        headers={"Accept": "application/json"},
    )
    assert rep_resp.status_code == 200
    rep_data = rep_resp.json()
    assert rep_data["success"] is True

    # Verify notification created for Admin
    with Session(app_db.engine) as session:
        notif = session.exec(select(Notification).where(Notification.notification_type == "warning")).first()
        assert notif is not None
        assert "Attack on Titan" in notif.message
        assert "Crunchyroll has updated" in notif.message

    # 2. Comment approval creates notification for User1
    with Session(app_db.engine) as session:
        comment = Comment(
            article_id=art_id,
            user_id=user_id,
            content="Ye guide bohot badiya hai!",
            is_approved=False,
        )
        session.add(comment)
        session.commit()
        session.refresh(comment)
        comment_id = comment.id

    # Admin approves the comment
    admin = Admin1(
        name="Comment Admin",
        email="commentadmin@example.com",
        hashed_password=get_password_hash("AdminPass123!"),
        is_active=True,
    )
    with Session(app_db.engine) as session:
        session.add(admin)
        session.commit()

    admin_token = create_access_token(data={"sub": "commentadmin@example.com", "name": "Comment Admin", "user_type": "admin"})
    client.cookies.set("auth_token", admin_token)

    approve_resp = client.post(f"/admin/comments/{comment_id}/approve", follow_redirects=False)
    assert approve_resp.status_code == 303

    # Check notification for reader User1
    with Session(app_db.engine) as session:
        u_notif = session.exec(
            select(Notification).where(Notification.user_id == user_id, Notification.notification_type == "success")
        ).first()
        assert u_notif is not None
        assert "प्रकाशित" in u_notif.message or "approved" in u_notif.message.lower()

    # 3. User checks notifications endpoint and marks read
    user_token = create_access_token(data={"sub": "eren@example.com", "name": "Eren Fan", "user_type": "user"})
    client.cookies.set("auth_token", user_token)

    list_resp = client.get("/api/notifications")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["unread_count"] >= 1
    assert len(list_data["notifications"]) >= 1

    mark_resp = client.post("/api/notifications/mark-all-read")
    assert mark_resp.status_code == 200
    assert mark_resp.json()["success"] is True

    # Unread count is now 0
    list_resp2 = client.get("/api/notifications")
    assert list_resp2.json()["unread_count"] == 0


def test_feature5_related_guides_and_seo_engine():
    client = TestClient(app)
    now = datetime.now(timezone.utc)

    with Session(app_db.engine) as session:
        main_art = Article(
            title="Main Shonen Naruto Guide",
            slug="main-shonen-naruto",
            meta_description="Complete Hindi guide for Naruto Shippuden with watch order.",
            content_md="## Full Guide\n\nNaruto adventure in Hindi.",
            category="where-to-watch",
            published_at=now,
        )
        rel_art1 = Article(
            title="Related Shonen Bleach Guide",
            slug="related-shonen-bleach",
            meta_description="Bleach guide.",
            content_md="## Bleach Guide\n\nSoul reapers.",
            category="where-to-watch",
            published_at=now,
        )
        session.add(main_art)
        session.add(rel_art1)
        session.commit()

    resp = client.get("/article/main-shonen-naruto")
    assert resp.status_code == 200
    html = resp.text

    # 1. Related Guides section present
    assert "संबंधित एनीमे गाइड्स" in html or "Related Guides" in html
    assert "Related Shonen Bleach Guide" in html

    # 2. OpenGraph and Twitter meta tags
    assert '<meta property="og:title" content="Main Shonen Naruto Guide"' in html
    assert '<meta property="og:description" content="Complete Hindi guide for Naruto Shippuden with watch order."' in html
    assert '<meta name="twitter:card"' in html

    # 3. JSON-LD structured data
    assert 'application/ld+json' in html
    assert '"@type": "Article"' in html
    assert '"headline": "Main Shonen Naruto Guide"' in html
    assert '"@type": "BreadcrumbList"' in html

    # 4. Sitemap.xml
    sitemap_resp = client.get("/sitemap.xml")
    assert sitemap_resp.status_code == 200
    assert "application/xml" in sitemap_resp.headers.get("content-type", "")
    assert "<urlset" in sitemap_resp.text
    assert "/article/main-shonen-naruto</loc>" in sitemap_resp.text
    assert "<lastmod>" in sitemap_resp.text

    # 5. Robots.txt
    robots_resp = client.get("/robots.txt")
    assert robots_resp.status_code == 200
    assert "User-agent: *" in robots_resp.text
    assert "Sitemap:" in robots_resp.text
    assert "/sitemap.xml" in robots_resp.text


def test_feature5_seed_sample_guides_idempotency():
    # Execute seed_all on test engine twice to guarantee idempotency
    seed_all(target_engine=app_db.engine)
    seed_all(target_engine=app_db.engine)

    with Session(app_db.engine) as session:
        # Verify 6 guides seeded
        slugs = [
            "naruto-hindi-dub-guide",
            "demon-slayer-hindi-dub-guide",
            "jujutsu-kaisen-hindi-streaming-guide",
            "solo-leveling-hindi-dub-guide",
            "attack-on-titan-streaming-guide-india",
            "death-note-hindi-watch-guide",
        ]
        for s in slugs:
            art = session.exec(select(Article).where(Article.slug == s)).first()
            assert art is not None, f"Seeded guide '{s}' not found in database!"
            assert len(art.availabilities) >= 1, f"Guide '{s}' has no availability records!"


def test_comment_posting_prg_and_redirect():
    client = TestClient(app)

    with Session(app_db.engine) as session:
        user = User1(
            name="कमेंट परीक्षक",
            email="comment_tester@example.com",
            username="comment_tester",
            hashed_password=get_password_hash("Password123!"),
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        article = Article(
            title="टिप्पणी परीक्षण लेख",
            slug="comment-test-article-prg",
            summary="टिप्पणी परीक्षण सारांश",
            content_md="## परीक्षण\n\nटिप्पणी कार्यप्रणाली परीक्षण।",
            status="published",
        )
        session.add(article)
        session.commit()
        session.refresh(article)
        art_id = article.id
        art_slug = article.slug
        user_email = user.email
        user_name = user.name

    token = create_access_token(data={"sub": user_email, "name": user_name, "user_type": "user"})

    # 1. Unauthenticated post -> redirect to login
    anon_resp = client.post(
        f"/comment/{art_id}",
        data={"content": "बिना लॉगिन की टिप्पणी"},
        follow_redirects=False,
    )
    assert anon_resp.status_code == 303
    assert "/auth/login" in anon_resp.headers["location"]

    # 2. Authenticated valid post -> 303 PRG to /article/<slug>?comment_success=1#comment-<id>
    client.cookies.set("auth_token", token)
    valid_resp = client.post(
        f"/comment/{art_id}",
        data={"content": "यह एक अत्यंत उत्कृष्ट एनीमे गाइड है!"},
        follow_redirects=False,
    )
    assert valid_resp.status_code == 303
    loc = valid_resp.headers["location"]
    assert f"/article/{art_slug}?comment_success=1#comment-" in loc

    comment_id = int(loc.split("#comment-")[-1])

    # 3. GET /comment/<id> -> 302 redirect to /article/<slug>#comment-<id>
    get_comment_resp = client.get(f"/comment/{comment_id}", follow_redirects=False)
    assert get_comment_resp.status_code == 302
    assert get_comment_resp.headers["location"] == f"/article/{art_slug}#comment-{comment_id}"

    # 4. Honeypot anti-spam check -> rejects with error
    bot_resp = client.post(
        f"/comment/{art_id}",
        data={"content": "स्पैम बॉट टिप्पणी", "website": "http://spam.example.com"},
        follow_redirects=False,
    )
    assert bot_resp.status_code == 303
    assert f"/article/{art_slug}#comments" in bot_resp.headers["location"]
    with Session(app_db.engine) as session:
        bot_comment = session.exec(select(Comment).where(Comment.content == "स्पैम बॉट टिप्पणी")).first()
        assert bot_comment is None

    # 5. Empty comment -> rejects with error
    empty_resp = client.post(
        f"/comment/{art_id}",
        data={"content": "   "},
        follow_redirects=False,
    )
    assert empty_resp.status_code == 303
    assert "comment_error=" in empty_resp.headers["location"]

    # 6. Delete comment -> 303 redirect with comment_deleted=1
    del_resp = client.post(f"/comment/{comment_id}/delete", follow_redirects=False)
    assert del_resp.status_code == 303
    assert "comment_deleted=1" in del_resp.headers["location"]


def test_article_multiple_categories_flow():
    from app.models import ArticleCategoryLink
    client = TestClient(app)

    with Session(app_db.engine) as session:
        # Ensure categories exist
        for slug, name in [("action", "एक्शन"), ("where-to-watch", "कहाँ देखें"), ("recommendations", "सिफारिशें")]:
            c = session.exec(select(Category).where(Category.name == slug)).first()
            if not c:
                session.add(Category(name=slug, display_name=name))
        
        # Ensure admin exists
        admin = session.exec(select(Admin1).where(Admin1.email == "admin@animeguide.com")).first()
        if not admin:
            admin = Admin1(name="Admin", email="admin@animeguide.com", hashed_password=get_password_hash("admin123"), is_active=True)
            session.add(admin)
        session.commit()

    token = create_access_token(data={"sub": "admin@animeguide.com", "name": "Admin", "user_type": "admin"})
    client.cookies.set("admin_token", token)

    # 1. Admin creates article with multiple categories
    create_resp = client.post(
        "/admin/articles/create",
        data={
            "title": "मल्टी-कैटेगरी एनीमे गाइड",
            "slug": "multi-cat-test-guide",
            "content_md": "## मल्टी-कैटेगरी एनीमे गाइड सामग्री",
            "meta_description": "यह लेख एक से अधिक श्रेणियों में मौजूद है।",
            "category_names": ["action", "recommendations"],
            "platform_ids": [],
        },
        follow_redirects=False
    )
    assert create_resp.status_code == 303

    with Session(app_db.engine) as session:
        art = session.exec(select(Article).where(Article.slug == "multi-cat-test-guide")).first()
        assert art is not None
        assert art.category == "action"  # primary category
        cat_links = session.exec(select(ArticleCategoryLink).where(ArticleCategoryLink.article_id == art.id)).all()
        linked_names = {l.category_name for l in cat_links}
        assert "action" in linked_names
        assert "recommendations" in linked_names
        assert len(art.all_categories) == 2
        art_id = art.id

    # 2. Verify article appears on BOTH category pages
    resp_action = client.get("/category/action")
    assert resp_action.status_code == 200
    assert "multi-cat-test-guide" in resp_action.text

    resp_recom = client.get("/category/recommendations")
    assert resp_recom.status_code == 200
    assert "multi-cat-test-guide" in resp_recom.text

    # 3. Verify article appears on explore filtered by either category
    resp_exp1 = client.get("/explore?category=action")
    assert resp_exp1.status_code == 200
    assert "multi-cat-test-guide" in resp_exp1.text

    resp_exp2 = client.get("/explore?category=recommendations")
    assert resp_exp2.status_code == 200
    assert "multi-cat-test-guide" in resp_exp2.text

    # 4. Verify article page displays multiple category badges
    resp_page = client.get("/article/multi-cat-test-guide")
    assert resp_page.status_code == 200
    assert "एक्शन" in resp_page.text
    assert "सिफारिशें" in resp_page.text

    # 5. Admin edits article to update categories
    edit_resp = client.post(
        f"/admin/articles/{art_id}/edit",
        data={
            "title": "मल्टी-कैटेगरी एनीमे गाइड (अपडेटेड)",
            "slug": "multi-cat-test-guide",
            "content_md": "## अपडेटेड सामग्री",
            "meta_description": "अपडेटेड मेटा",
            "category_names": ["where-to-watch", "action"],
            "platform_ids": [],
        },
        follow_redirects=False
    )
    assert edit_resp.status_code == 303

    with Session(app_db.engine) as session:
        art = session.exec(select(Article).where(Article.id == art_id)).first()
        cat_links = session.exec(select(ArticleCategoryLink).where(ArticleCategoryLink.article_id == art.id)).all()
        linked_names = {l.category_name for l in cat_links}
        assert "where-to-watch" in linked_names
        assert "action" in linked_names
        assert "recommendations" not in linked_names


