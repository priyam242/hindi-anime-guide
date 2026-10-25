import io
import os
from pathlib import Path
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
import app.db as app_db
from app.models import User1, Admin1, Article, Comment, Category
from app.auth import get_password_hash, create_access_token
from app.storage import save_avatar, delete_avatar, MAX_AVATAR_SIZE, AVATARS_DIR


def test_footer_security_and_header_auth():
    client = TestClient(app)

    # 1. Anonymous visitor
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    # Public footer must NOT expose Admin Portal or Account links
    assert "Admin Portal" not in html
    assert "/admin/login" not in html
    assert "/settings" not in html
    assert ">खाता<" not in html

    # Public footer must have गाइड्स, साइट, कानूनी columns
    assert "गाइड्स" in html
    assert "साइट" in html
    assert "कानूनी" in html

    # Guest buttons in topbar
    assert "लॉग इन" in html
    assert "साइन अप" in html

    # 2. Regular user logged in
    with Session(app_db.engine) as session:
        user = User1(
            name="John Doe",
            username="johndoe",
            email="john@example.com",
            hashed_password=get_password_hash("Secret123!"),
            is_active=True
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        user_email = user.email
        user_name = user.name

    token = create_access_token(data={"sub": user_email, "name": user_name, "user_type": "user"})
    client.cookies.set("auth_token", token)

    resp_user = client.get("/")
    assert resp_user.status_code == 200
    user_html = resp_user.text

    # User dropdown present
    assert "खाता सेटिंग्स" in user_html
    assert "लॉग आउट" in user_html
    # Admin portal NOT visible in regular user dropdown
    assert "Admin Portal" not in user_html
    assert "/admin/dashboard" not in user_html

    # 3. Admin user logged in
    with Session(app_db.engine) as session:
        admin = Admin1(
            name="Super Admin",
            email="admin@example.com",
            hashed_password=get_password_hash("AdminPass123!"),
            is_active=True
        )
        session.add(admin)
        session.commit()
        session.refresh(admin)
        admin_email = admin.email
        admin_name = admin.name

    admin_token = create_access_token(data={"sub": admin_email, "name": admin_name, "user_type": "admin"})
    client.cookies.set("auth_token", admin_token)
    client.cookies.set("admin_token", admin_token)

    resp_admin = client.get("/")
    assert resp_admin.status_code == 200
    admin_html = resp_admin.text

    # Admin dropdown has admin portal & articles management
    assert "Admin Portal" in admin_html
    assert "Articles Management" in admin_html
    assert "खाता सेटिंग्स" in admin_html


def test_article_page_layout_and_community_discussions():
    client = TestClient(app)

    # Create an article with comments
    with Session(app_db.engine) as session:
        cat = session.exec(select(Category)).first()
        article = Article(
            title="Naruto Hindi Guide",
            slug="naruto-hindi-guide",
            summary="A comprehensive Naruto Hindi Guide.",
            content_md="## परिचय\nयह एक गाइड है।\n### विवरण\nविस्तृत जानकारी।",
            category_id=cat.id if cat else None,
            is_sample=False
        )
        session.add(article)
        session.commit()
        session.refresh(article)
        article_slug = article.slug

        user = User1(
            name="Alice Walker",
            username="alicew",
            email="alice@example.com",
            hashed_password=get_password_hash("Secret123!"),
            is_active=True
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_access_token(data={"sub": user.email, "name": user.name, "user_type": "user"})

        comment = Comment(
            article_id=article.id,
            user_id=user.id,
            content="Great article on Hindi anime guide!",
            is_approved=True
        )
        session.add(comment)
        session.commit()

    resp = client.get(f"/article/{article_slug}")
    assert resp.status_code == 200
    html = resp.text

    # 1. Unified 820px rail container
    assert "article-container" in html
    assert "max-width: 820px" in html

    # 2. Breadcrumb format with › and no empty double slash
    assert "›" in html
    assert "होम /  /" not in html

    # 3. Community Discussions in Hindi
    assert "समुदाय चर्चा" in html
    assert "चर्चा में शामिल हों" in html
    assert "चर्चा में भाग लेने के लिए कृपया लॉग इन करें" in html

    # Now request as logged in user to see the comment submit button
    client.cookies.set("auth_token", token)
    resp_logged_in = client.get(f"/article/{article_slug}")
    assert resp_logged_in.status_code == 200
    logged_in_html = resp_logged_in.text
    assert "टिप्पणी पोस्ट करें →" in logged_in_html
    assert "Alice Walker" in logged_in_html

    # 4. Vertical order: Composer card occurs BEFORE the comments list
    composer_pos = html.find("चर्चा में शामिल हों")
    comment_content_pos = html.find("Great article on Hindi anime guide!")
    assert composer_pos != -1
    assert comment_content_pos != -1
    assert composer_pos < comment_content_pos, "Comment composer must appear before comments list"

    # 5. Collapsible Table of Contents toggle
    if "विषय सूची" in html:
        assert "छुपाएँ ▲" in html
        assert "scroll-margin-top: 84px" in html


def test_password_change_flow_end_to_end():
    client = TestClient(app)

    with Session(app_db.engine) as session:
        user = User1(
            name="Bob Builder",
            username="bobbuilder",
            email="bob@example.com",
            hashed_password=get_password_hash("OldPassword123!"),
            is_active=True
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        user_email = user.email

    token = create_access_token(data={"sub": user_email, "name": "Bob Builder", "user_type": "user"})
    client.cookies.set("auth_token", token)

    # 1. Wrong current password
    resp = client.post(
        "/settings/change-password",
        data={
            "current_password": "WrongPassword123!",
            "new_password": "NewPassword123!",
            "confirm_password": "NewPassword123!",
        },
        follow_redirects=False
    )
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert "वर्तमान" in resp.headers["location"] or "%E0%A4%B5%E0%A4%B0%E0%A5%8D%E0%A4%A4%E0%A4%AE%E0%A4%BE%E0%A4%A8" in resp.headers["location"]

    # 2. Mismatched confirm password
    resp = client.post(
        "/settings/change-password",
        data={
            "current_password": "OldPassword123!",
            "new_password": "NewPassword123!",
            "confirm_password": "DifferentPassword123!",
        },
        follow_redirects=False
    )
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]

    # 3. Short new password (< 8 chars)
    resp = client.post(
        "/settings/change-password",
        data={
            "current_password": "OldPassword123!",
            "new_password": "Short1!",
            "confirm_password": "Short1!",
        },
        follow_redirects=False
    )
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]

    # 4. Valid new password change
    resp = client.post(
        "/settings/change-password",
        data={
            "current_password": "OldPassword123!",
            "new_password": "BrandNewPassword123!",
            "confirm_password": "BrandNewPassword123!",
        },
        follow_redirects=False
    )
    assert resp.status_code == 303
    assert "password_changed=1" in resp.headers["location"]
    # Session cookie preserved
    assert "auth_token" in resp.cookies or "auth_token" in resp.headers.get("set-cookie", "")

    # Verify new password in DB
    with Session(app_db.engine) as session:
        db_user = session.exec(select(User1).where(User1.email == user_email)).first()
        from app.auth import verify_password
        assert verify_password("BrandNewPassword123!", db_user.hashed_password)
        assert not verify_password("OldPassword123!", db_user.hashed_password)


def test_avatar_upload_10mb_and_storage():
    client = TestClient(app)

    with Session(app_db.engine) as session:
        user = User1(
            name="Charlie Brown",
            username="charlieb",
            email="charlie@example.com",
            hashed_password=get_password_hash("Password123!"),
            is_active=True
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        user_email = user.email

    token = create_access_token(data={"sub": user_email, "name": "Charlie Brown", "user_type": "user"})
    client.cookies.set("auth_token", token)

    # 1. Create a high-res PIL image (e.g. 600x400)
    img = Image.new("RGB", (600, 400), color=(200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    img_bytes = buf.getvalue()

    # Upload avatar
    resp = client.post(
        "/uploads/avatar",
        files={"file": ("photo.jpg", img_bytes, "image/jpeg")}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    avatar_url = data["url"]
    assert avatar_url.startswith("/media/avatars/")
    assert avatar_url.endswith(".webp")

    # Check database: stored as relative URL string, NEVER BYTEA/binary
    with Session(app_db.engine) as session:
        db_user = session.exec(select(User1).where(User1.email == user_email)).first()
        assert db_user.avatar_url == avatar_url
        assert isinstance(db_user.avatar_url, str)

    # Check file exists on filesystem and is max 400x400 square WebP
    filename = Path(avatar_url).name
    saved_file = AVATARS_DIR / filename
    assert saved_file.exists()
    
    with Image.open(saved_file) as saved_img:
        assert saved_img.format == "WEBP"
        assert saved_img.size == (400, 400)  # 1:1 square cropped & resized

    # 2. Test remove avatar
    resp_remove = client.post("/uploads/avatar/remove")
    assert resp_remove.status_code == 200
    assert resp_remove.json()["success"] is True

    # Database updated
    with Session(app_db.engine) as session:
        db_user = session.exec(select(User1).where(User1.email == user_email)).first()
        assert db_user.avatar_url is None

    # Disk file removed
    assert not saved_file.exists()

    # 3. Over 10MB rejection
    oversized_bytes = b"x" * (MAX_AVATAR_SIZE + 1024)
    resp_over = client.post(
        "/uploads/avatar",
        files={"file": ("huge.jpg", oversized_bytes, "image/jpeg")}
    )
    assert resp_over.status_code == 400
    assert "10MB" in resp_over.text


def test_user_email_change_and_settings_tabs():
    client = TestClient(app)

    with Session(app_db.engine) as session:
        user = User1(
            name="David Miller",
            username="davidm",
            email="david@example.com",
            hashed_password=get_password_hash("Password123!"),
            is_active=True
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        initial_id = user.id

    token = create_access_token(data={"sub": "david@example.com", "name": "David Miller", "user_type": "user"})
    client.cookies.set("auth_token", token)

    # 1. Check settings page structure
    resp_settings = client.get("/settings")
    assert resp_settings.status_code == 200
    html = resp_settings.text

    assert 'id="pane-profile"' in html
    assert 'id="pane-security"' in html
    assert 'id="lang-card-en"' in html
    assert 'id="lang-card-hi"' in html
    assert 'action="/settings/delete-account"' in html
    assert 'name="email"' in html
    assert 'showSettingsTab' in html

    # 2. Update email
    resp_update = client.post(
        "/settings/update",
        data={
            "name": "David Miller",
            "username": "davidm",
            "email": "david.new@example.com",
            "bio": "Anime lover"
        },
        follow_redirects=False
    )
    assert resp_update.status_code == 303
    if "/auth/verify-otp" in resp_update.headers["location"]:
        pre_token = resp_update.cookies.get("pre_2fa_token")
        if pre_token:
            client.cookies.set("pre_2fa_token", pre_token)
        from app.email_otp import EMAIL_OUTBOX
        otp_code = EMAIL_OUTBOX[-1]["code"]
        resp_update = client.post(
            "/auth/verify-otp",
            data={"code": otp_code},
            follow_redirects=False
        )
        assert resp_update.status_code == 303

    assert "updated=1" in resp_update.headers["location"]
    # Check that new token cookie was set
    assert "auth_token" in resp_update.cookies or "auth_token" in resp_update.headers.get("set-cookie", "")

    # Verify DB update
    with Session(app_db.engine) as session:
        updated_user = session.get(User1, initial_id)
        assert updated_user.email == "david.new@example.com"

    # Set new cookie on client and visit settings again to verify persistent auth
    client.cookies.set("auth_token", resp_update.cookies.get("auth_token"))
    resp_after = client.get("/settings")
    assert resp_after.status_code == 200
    assert "david.new@example.com" in resp_after.text

