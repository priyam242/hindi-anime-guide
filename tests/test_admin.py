import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import get_password_hash, create_access_token
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import AdminUser, Article, Category, Platform, Availability, AvailabilityStatus, HindiDubStatus
from app.routers.admin import ensure_admin_user

settings = get_settings()


@pytest.fixture
def auth_client():
    client = TestClient(app)
    # Seed admin user
    with Session(client.app.state.engine if hasattr(client.app.state, "engine") else None or app.dependency_overrides[get_session]().__next__().bind) as session:
        ensure_admin_user(session)
    token = create_access_token(data={"sub": settings.admin_email})
    client.cookies.set("admin_token", token)
    return client


def test_login_page_renders():
    client = TestClient(app)
    resp = client.get("/admin/login", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"
    # Following redirect renders normal unified login page
    resp_followed = client.get("/admin/login", follow_redirects=True)
    assert resp_followed.status_code == 200
    assert "Hindi Anime" in resp_followed.text
    assert "साइन इन" in resp_followed.text


def test_login_invalid_credentials():
    client = TestClient(app)
    resp = client.post(
        "/admin/login",
        data={"email": "wrong@admin.com", "password": "wrongpassword"}
    )
    assert resp.status_code == 400
    assert "अमान्य ईमेल या पासवर्ड" in resp.text


def test_login_success():
    client = TestClient(app)
    # First ensure admin user exists in test DB
    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        ensure_admin_user(session)

    resp = client.post(
        "/admin/login",
        data={"email": settings.admin_email, "password": settings.admin_password},
        follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/dashboard"
    assert "admin_token" in resp.cookies


def test_logout():
    client = TestClient(app)
    token = create_access_token(data={"sub": settings.admin_email})
    client.cookies.set("admin_token", token)
    resp = client.get("/admin/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_protected_routes_without_login_return_401():
    client = TestClient(app)
    # Clear any cookies
    client.cookies.clear()
    assert client.get("/admin/dashboard").status_code == 401
    assert client.get("/admin/articles").status_code == 401
    assert client.get("/admin/articles/create").status_code == 401
    assert client.get("/admin/categories").status_code == 401
    assert client.get("/admin/platforms").status_code == 401


def test_dashboard_with_authenticated_admin(auth_client):
    resp = auth_client.get("/admin/dashboard")
    assert resp.status_code == 200
    assert "डैशबोर्ड" in resp.text
    assert settings.admin_email in resp.text


def test_articles_crud(auth_client):
    # 1. Fetch create page
    resp_create_page = auth_client.get("/admin/articles/create")
    assert resp_create_page.status_code == 200
    assert "नया लेख बनाएं" in resp_create_page.text

    # 2. Get category & platform from test DB
    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        cat = session.exec(select(Category)).first()
        plat = session.exec(select(Platform)).first()
        cat_id = cat.id
        plat_id = plat.id

    # 3. Create an article
    create_data = {
        "title": "परीक्षण एनीमे हिंदी गाइड",
        "slug": "test-anime-hindi-guide",
        "category_id": cat_id,
        "content_md": "## सामग्री\n\nयह एक परीक्षण सामग्री है।",
        "meta_description": "परीक्षण मेटा विवरण",
        "platform_ids": [plat_id],
    }
    resp_create = auth_client.post("/admin/articles/create", data=create_data, follow_redirects=False)
    assert resp_create.status_code == 303
    assert resp_create.headers["location"] == "/admin/articles"

    # 4. Verify in DB and list
    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        art = session.exec(select(Article).where(Article.slug == "test-anime-hindi-guide")).first()
        assert art is not None
        assert art.title == "परीक्षण एनीमे हिंदी गाइड"
        assert len(art.availabilities) == 1
        assert art.availabilities[0].platform_id == plat_id
        art_id = art.id

    resp_list = auth_client.get("/admin/articles")
    assert resp_list.status_code == 200
    assert "test-anime-hindi-guide" in resp_list.text

    # 5. Fetch edit page
    resp_edit_page = auth_client.get(f"/admin/articles/{art_id}/edit")
    assert resp_edit_page.status_code == 200
    assert "परीक्षण एनीमे हिंदी गाइड" in resp_edit_page.text

    # 6. Post update
    update_data = {
        "title": "अपडेटेड परीक्षण एनीमे",
        "slug": "updated-test-anime",
        "category_id": cat_id,
        "content_md": "## अपडेटेड सामग्री",
        "meta_description": "अपडेटेड मेटा",
        "platform_ids": [],
    }
    resp_edit = auth_client.post(f"/admin/articles/{art_id}/edit", data=update_data, follow_redirects=False)
    assert resp_edit.status_code == 303
    assert resp_edit.headers["location"] == "/admin/articles"

    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        art = session.exec(select(Article).where(Article.id == art_id)).first()
        assert art.title == "अपडेटेड परीक्षण एनीमे"
        assert art.slug == "updated-test-anime"
        assert len(art.availabilities) == 0

    # 7. Delete article
    resp_delete = auth_client.post(f"/admin/articles/{art_id}/delete", follow_redirects=False)
    assert resp_delete.status_code == 303
    assert resp_delete.headers["location"] == "/admin/articles"

    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        art_deleted = session.exec(select(Article).where(Article.id == art_id)).first()
        assert art_deleted is None


def test_categories_crud(auth_client):
    # 1. List
    resp_list = auth_client.get("/admin/categories")
    assert resp_list.status_code == 200
    assert "नई श्रेणी जोड़ें" in resp_list.text

    # 2. Create
    cat_data = {
        "name": "movies",
        "display_name": "फ़िल्में",
        "display_order": 5,
    }
    resp_create = auth_client.post("/admin/categories/create", data=cat_data, follow_redirects=False)
    assert resp_create.status_code == 303
    assert resp_create.headers["location"] == "/admin/categories"

    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        cat = session.exec(select(Category).where(Category.slug == "movies")).first()
        assert cat is not None
        assert (cat.name == "फ़िल्में" or cat.display_name == "फ़िल्में")
        cat_id = cat.id

    # 3. Delete
    resp_del = auth_client.post(f"/admin/categories/{cat_id}/delete", follow_redirects=False)
    assert resp_del.status_code == 303
    assert resp_del.headers["location"] == "/admin/categories"

    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        cat_deleted = session.exec(select(Category).where(Category.id == cat_id)).first()
        assert cat_deleted is None


def test_platforms_crud(auth_client):
    # 1. List
    resp_list = auth_client.get("/admin/platforms")
    assert resp_list.status_code == 200
    assert "नया प्लेटफ़ॉर्म जोड़ें" in resp_list.text

    # 2. Validation: HTTP must fail with 400
    bad_plat = {
        "name": "insecure",
        "official_url": "http://insecure-platform.com",
        "display_name": "असुरक्षित",
        "display_order": 9,
    }
    resp_bad = auth_client.post("/admin/platforms/create", data=bad_plat)
    assert resp_bad.status_code == 400
    assert "https://" in resp_bad.text

    # 3. Valid platform: HTTPS succeeds
    good_plat = {
        "name": "Prime",
        "official_url": "https://prime.amazon.com",
        "display_name": "प्राइम",
        "display_order": 10,
    }
    resp_create = auth_client.post("/admin/platforms/create", data=good_plat, follow_redirects=False)
    assert resp_create.status_code == 303
    assert resp_create.headers["location"] == "/admin/platforms"

    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        p = session.exec(select(Platform).where(Platform.official_url == "https://prime.amazon.com")).first()
        assert p is not None
        p_id = p.id

    # 4. Delete platform
    resp_del = auth_client.post(f"/admin/platforms/{p_id}/delete", follow_redirects=False)
    assert resp_del.status_code == 303
    assert resp_del.headers["location"] == "/admin/platforms"

    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        p_deleted = session.exec(select(Platform).where(Platform.id == p_id)).first()
        assert p_deleted is None


def test_register_page_renders():
    client = TestClient(app)
    resp = client.get("/admin/register")
    assert resp.status_code == 200
    assert "Create Admin Account" in resp.text
    assert "Full Name" in resp.text


def test_register_validation():
    client = TestClient(app)
    # Password mismatch
    resp1 = client.post(
        "/admin/register",
        data={
            "name": "Test User",
            "email": "mismatch@admin.com",
            "password": "password123",
            "confirm_password": "differentpassword"
        }
    )
    assert resp1.status_code == 400
    assert "Passwords do not match" in resp1.text

    # Password too short
    resp2 = client.post(
        "/admin/register",
        data={
            "name": "Test User",
            "email": "short@admin.com",
            "password": "short",
            "confirm_password": "short"
        }
    )
    assert resp2.status_code == 400
    assert "Password must be at least 8 characters" in resp2.text

    # Duplicate email with existing admin
    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        ensure_admin_user(session)

    resp3 = client.post(
        "/admin/register",
        data={
            "name": "Admin Clone",
            "email": settings.admin_email,
            "password": "password123",
            "confirm_password": "password123"
        }
    )
    assert resp3.status_code == 400
    assert "Email already registered" in resp3.text


def test_registration_and_multiuser_login_flow():
    client = TestClient(app)
    # 1. Register new user
    new_email = "newadmin@animeguide.com"
    new_name = "Rajesh Prajapati"
    resp_reg = client.post(
        "/admin/register",
        data={
            "name": new_name,
            "email": new_email,
            "password": "securepassword123",
            "confirm_password": "securepassword123"
        },
        follow_redirects=False
    )
    assert resp_reg.status_code == 303
    assert resp_reg.headers["location"] == "/auth/login?registered=1"

    # 2. Check login page displays success banner
    resp_login_page = client.get("/auth/login?registered=1")
    assert resp_login_page.status_code == 200
    assert "खाता सफलतापूर्वक बन गया" in resp_login_page.text

    # 3. Login with newly created user
    resp_login = client.post(
        "/admin/login",
        data={"email": new_email, "password": "securepassword123"},
        follow_redirects=False
    )
    assert resp_login.status_code == 303
    assert "admin_token" in resp_login.cookies

    # 4. Access dashboard with newly registered user
    client.cookies.set("admin_token", resp_login.cookies["admin_token"])
    resp_dash = client.get("/admin/dashboard")
    assert resp_dash.status_code == 200
    assert new_name in resp_dash.text
    assert "Welcome back" in resp_dash.text


def test_admin_account_update_name_email_bio(auth_client):
    from app.models import Admin1
    # 1. Update admin name, email, and bio
    new_email = "superadmin.updated@hindianime.com"
    new_name = "Vikram Aditya"
    new_bio = "Senior Platform Lead and Editor"
    resp = auth_client.post(
        "/admin/account/update",
        data={
            "name": new_name,
            "email": new_email,
            "bio": new_bio,
            "avatar_url": ""
        },
        follow_redirects=False
    )
    assert resp.status_code == 303
    assert "/admin/account?updated=1#profile" in resp.headers["location"]
    # Token cookie must be updated to new email
    assert "admin_token" in resp.cookies
    auth_client.cookies.set("admin_token", resp.cookies["admin_token"])

    # Verify admin page reflects updated details
    page_resp = auth_client.get("/admin/account")
    assert page_resp.status_code == 200
    assert new_name in page_resp.text
    assert new_email in page_resp.text
    assert new_bio in page_resp.text


def test_admin_create_new_admin(auth_client):
    # Appoint a brand new administrator directly
    new_admin_email = "appointed.admin@hindianime.com"
    new_admin_name = "Pooja Hegde"
    resp = auth_client.post(
        "/admin/create-admin",
        data={
            "name": new_admin_name,
            "email": new_admin_email,
            "password": "StrongPassword!2026",
            "bio": "Regional Hindi Content Moderator"
        },
        follow_redirects=False
    )
    assert resp.status_code == 303
    assert "/admin/account?admin_created=1#admins" in resp.headers["location"]

    # Verify appointed admin appears in account directory
    page_resp = auth_client.get("/admin/account")
    assert page_resp.status_code == 200
    assert new_admin_name in page_resp.text
    assert new_admin_email in page_resp.text


def test_admin_promote_user_to_admin(auth_client):
    from app.models import User1
    # 1. Create a regular user in DB
    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        user = User1(
            name="Rohan Verma",
            username="rohan_anime",
            email="rohan.reader@hindianime.com",
            hashed_password=get_password_hash("ReaderPass@123"),
            bio="Passionate anime reviewer",
            is_active=True
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        user_id = user.id

    # 2. Promote user to administrator
    resp = auth_client.post(f"/admin/users/{user_id}/make-admin", follow_redirects=False)
    assert resp.status_code == 303
    assert "/admin/users?promoted=1" in resp.headers["location"]

    # 3. Check users list renders Admin badge
    list_resp = auth_client.get("/admin/users")
    assert list_resp.status_code == 200
    assert "rohan_anime" in list_resp.text
    assert "👑 Admin" in list_resp.text


def test_admin_toggle_secondary_admin_status(auth_client):
    from app.models import Admin1
    # Create secondary admin
    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        sec_admin = Admin1(
            name="Karan Johar",
            email="karan.editor@hindianime.com",
            hashed_password=get_password_hash("EditorPass@123"),
            is_active=True
        )
        session.add(sec_admin)
        session.commit()
        session.refresh(sec_admin)
        sec_id = sec_admin.id

    # Deactivate secondary admin
    resp = auth_client.post(f"/admin/admins/{sec_id}/toggle-status", follow_redirects=False)
    assert resp.status_code == 303
    assert "/admin/account?status_toggled=1#admins" in resp.headers["location"]

    with Session(app.dependency_overrides[get_session]().__next__().bind) as session:
        reloaded = session.get(Admin1, sec_id)
        assert reloaded.is_active is False


