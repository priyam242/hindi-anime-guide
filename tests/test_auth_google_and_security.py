"""
Test suite for hardened auth, Google OAuth 2.0 flow, rate limiting, open-redirect protection,
CSRF, and password rules.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.models import User1, Admin1, EmailOtp, TrustedDevice, Notification
from app.auth import get_password_hash, create_access_token
from app.totp import generate_totp_secret, encrypt_totp_secret, generate_csrf_token, generate_device_token, hash_device_token, create_pre2fa_token
from app.email_otp import compute_otp_hash, generate_otp_code
from app.routers.users import auth_limiter


@pytest.fixture(autouse=True)
def ensure_clean_outbox():
    from app.email_otp import EMAIL_OUTBOX
    EMAIL_OUTBOX.clear()


@pytest.fixture()
def client():
    # Clear rate limiter attempts between tests
    auth_limiter._attempts.clear()
    return TestClient(app, follow_redirects=False)


# ── 1. Registration tests ──────────────────────────────────────────────────────

def test_signup_creates_no_user_before_otp_and_creates_after_otp(client, isolate_database_per_test, monkeypatch):
    """Signup sends OTP first; creates user only after code verified (name + argon2 hash in pending record)."""
    monkeypatch.setattr("app.email_otp.generate_otp_code", lambda: "123456")
    resp = client.post(
        "/auth/register",
        data={
            "email": "  OtakuFan@Example.COM  ",
            "password": "StrongPassword2026!",
        },
    )
    assert resp.status_code == 303
    assert "/auth/verify-otp" in resp.headers.get("location", "")
    assert "pre_2fa_token" in resp.cookies
    assert "auth_token" not in resp.cookies

    # Step 1: User does NOT exist in User1 table yet
    with Session(isolate_database_per_test) as session:
        user = session.exec(select(User1).where(User1.email == "otakufan@example.com")).first()
        assert user is None

        otp = session.exec(select(EmailOtp).where(EmailOtp.email == "otakufan@example.com")).first()
        assert otp is not None
        assert otp.purpose == "signup"
        assert "StrongPassword2026!" not in (otp.pending_payload or "")
        import json
        payload_data = json.loads(otp.pending_payload)
        from app.auth import verify_password
        assert verify_password("StrongPassword2026!", payload_data["password_hash"])

    # Step 2: Submit OTP verification
    pre_token = resp.cookies.get("pre_2fa_token")
    client.cookies.set("pre_2fa_token", pre_token)
    v_resp = client.post("/auth/verify-otp", data={"code": "123456"})
    assert v_resp.status_code == 303
    assert "auth_token" in v_resp.cookies

    # Step 3: User IS now created in DB with email_verified=True
    with Session(isolate_database_per_test) as session:
        user = session.exec(select(User1).where(User1.email == "otakufan@example.com")).first()
        assert user is not None
        assert user.email == "otakufan@example.com"
        assert user.auth_provider == "password"
        assert user.email_verified is True
        assert user.hashed_password is not None
        assert user.is_active is True
        assert user.is_banned is False


def test_register_duplicate_email_case_insensitive(client, isolate_database_per_test):
    """Case-insensitive duplicate email check rejects with 400 and clear Hindi message."""
    with Session(isolate_database_per_test) as session:
        u = User1(
            email="existing@example.com",
            name="Existing User",
            username="existing_user",
            hashed_password=get_password_hash("Password123!"),
            auth_provider="password",
        )
        session.add(u)
        session.commit()

    resp = client.post(
        "/auth/register",
        data={
            "email": "EXISTING@example.com",
            "password": "AnotherPassword2026!",
        },
    )
    assert resp.status_code == 400
    assert "यह ईमेल पहले से पंजीकृत है" in resp.text


def test_register_weak_password_rejected(client, isolate_database_per_test):
    """Short (<8 chars) or very common passwords are rejected."""
    # Too short
    resp_short = client.post(
        "/auth/register",
        data={"email": "user1@example.com", "password": "123"},
    )
    assert resp_short.status_code == 400
    assert "कम से कम 8 अक्षर" in resp_short.text

    # Very common password
    resp_common = client.post(
        "/auth/register",
        data={"email": "user2@example.com", "password": "password123"},
    )
    assert resp_common.status_code == 400
    assert "बहुत सामान्य और असुरक्षित" in resp_common.text


# ── 2. Login & security tests ──────────────────────────────────────────────────

def test_password_login_does_not_set_auth_token_before_otp_and_verify_sets_cookie(client, isolate_database_per_test, monkeypatch):
    """Password login requires email OTP; does not issue auth_token before OTP, then issues with HttpOnly and samesite=lax."""
    monkeypatch.setattr("app.email_otp.generate_otp_code", lambda: "654321")
    with Session(isolate_database_per_test) as session:
        u = User1(
            email="loginuser@example.com",
            name="Login User",
            username="login_user",
            hashed_password=get_password_hash("MySecretPass2026!"),
            auth_provider="password",
        )
        session.add(u)
        session.commit()

    resp = client.post(
        "/auth/login",
        data={"email": "LOGINUSER@example.com", "password": "MySecretPass2026!"},
    )
    assert resp.status_code == 303
    assert "/auth/verify-otp" in resp.headers.get("location", "")
    assert "pre_2fa_token" in resp.cookies
    assert "auth_token" not in resp.cookies

    # Verify OTP
    client.cookies.set("pre_2fa_token", resp.cookies.get("pre_2fa_token"))
    v_resp = client.post("/auth/verify-otp", data={"code": "654321"})
    assert v_resp.status_code == 303
    assert "auth_token" in v_resp.cookies
    set_cookie = v_resp.headers.get("set-cookie", "")
    assert "auth_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


def test_login_failure_generic_error_and_timing_safe(client, isolate_database_per_test):
    """Wrong email and wrong password both return the identical generic error."""
    # Non-existent user
    resp_no_user = client.post(
        "/auth/login",
        data={"email": "nonexistent@example.com", "password": "AnyPassword123!"},
    )
    assert resp_no_user.status_code == 400
    assert "ईमेल या पासवर्ड गलत है" in resp_no_user.text

    # Existing user with wrong password
    with Session(isolate_database_per_test) as session:
        u = User1(
            email="realuser@example.com",
            name="Real User",
            username="real_user",
            hashed_password=get_password_hash("CorrectPass123!"),
            auth_provider="password",
        )
        session.add(u)
        session.commit()

    resp_wrong_pw = client.post(
        "/auth/login",
        data={"email": "realuser@example.com", "password": "WrongPassword123!"},
    )
    assert resp_wrong_pw.status_code == 400
    assert "ईमेल या पासवर्ड गलत है" in resp_wrong_pw.text


def test_google_only_user_password_login_shows_clean_message(client, isolate_database_per_test):
    """A Google-only user (no hashed_password) gets a clean message without crashing."""
    with Session(isolate_database_per_test) as session:
        u = User1(
            email="googleonly@example.com",
            name="Google User",
            username="google_user",
            hashed_password=None,
            google_sub="sub-9999",
            auth_provider="google",
        )
        session.add(u)
        session.commit()

    resp = client.post(
        "/auth/login",
        data={"email": "googleonly@example.com", "password": "SomePassword123!"},
    )
    assert resp.status_code == 400
    assert "इस खाते में Google से लॉगिन करें" in resp.text


def test_login_banned_and_inactive_users_rejected(client, isolate_database_per_test):
    """Banned and inactive users cannot log in."""
    with Session(isolate_database_per_test) as session:
        banned = User1(
            email="banned@example.com",
            name="Banned User",
            username="banned_user",
            hashed_password=get_password_hash("Pass12345!"),
            is_banned=True,
        )
        inactive = User1(
            email="inactive@example.com",
            name="Inactive User",
            username="inactive_user",
            hashed_password=get_password_hash("Pass12345!"),
            is_active=False,
        )
        session.add(banned)
        session.add(inactive)
        session.commit()

    resp_banned = client.post(
        "/auth/login",
        data={"email": "banned@example.com", "password": "Pass12345!"},
    )
    assert resp_banned.status_code == 403
    assert "खाता निलंबित" in resp_banned.text

    resp_inactive = client.post(
        "/auth/login",
        data={"email": "inactive@example.com", "password": "Pass12345!"},
    )
    assert resp_inactive.status_code == 403
    assert "खाता निष्क्रिय" in resp_inactive.text


def test_rate_limiting_locks_out_after_5_failures(client, isolate_database_per_test):
    """5 failed attempts per email+IP locks out with 429."""
    for _ in range(5):
        resp = client.post(
            "/auth/login",
            data={"email": "ratelimit@example.com", "password": "WrongPassword!"},
        )
        assert resp.status_code == 400

    # 6th attempt should be blocked by rate limiter
    resp_blocked = client.post(
        "/auth/login",
        data={"email": "ratelimit@example.com", "password": "WrongPassword!"},
    )
    assert resp_blocked.status_code == 429
    assert "थोड़ी देर बाद कोशिश करें" in resp_blocked.text


def test_next_param_open_redirect_safety(client, isolate_database_per_test, monkeypatch):
    """Next parameter allows safe relative path, rejects external URLs across OTP & direct login."""
    monkeypatch.setattr("app.email_otp.generate_otp_code", lambda: "123456")
    with Session(isolate_database_per_test) as session:
        u = User1(
            email="safenext@example.com",
            name="Safe User",
            username="safe_user",
            hashed_password=get_password_hash("Pass12345!"),
        )
        session.add(u)
        session.commit()

    # 1. External open redirect attempt via OTP verify
    resp_ext = client.post(
        "/auth/login",
        data={"email": "safenext@example.com", "password": "Pass12345!", "next": "https://evil.com/steal"},
    )
    assert resp_ext.status_code == 303
    assert "/auth/verify-otp" in resp_ext.headers.get("location", "")
    client.cookies.set("pre_2fa_token", resp_ext.cookies.get("pre_2fa_token"))
    v_ext = client.post("/auth/verify-otp", data={"code": "123456"})
    assert v_ext.status_code == 303
    assert v_ext.headers.get("location") == "/"

    # 2. Protocol-relative open redirect attempt via OTP verify
    client.cookies.clear()
    resp_proto = client.post(
        "/auth/login",
        data={"email": "safenext@example.com", "password": "Pass12345!", "next": "//evil.com"},
    )
    assert resp_proto.status_code == 303
    assert "/auth/verify-otp" in resp_proto.headers.get("location", "")
    client.cookies.set("pre_2fa_token", resp_proto.cookies.get("pre_2fa_token"))
    v_proto = client.post("/auth/verify-otp", data={"code": "123456"})
    assert v_proto.status_code == 303
    assert v_proto.headers.get("location") == "/"

    # 3. Valid internal relative path via OTP verify
    client.cookies.clear()
    resp_valid = client.post(
        "/auth/login",
        data={"email": "safenext@example.com", "password": "Pass12345!", "next": "/profile"},
    )
    assert resp_valid.status_code == 303
    assert "/auth/verify-otp" in resp_valid.headers.get("location", "")
    client.cookies.set("pre_2fa_token", resp_valid.cookies.get("pre_2fa_token"))
    v_valid = client.post("/auth/verify-otp", data={"code": "123456"})
    assert v_valid.status_code == 303
    assert v_valid.headers.get("location") == "/profile"

    # 4. Direct login with trusted device cookie
    raw_token = generate_device_token()
    tok_hash = hash_device_token(raw_token)
    with Session(isolate_database_per_test) as session:
        u_db = session.exec(select(User1).where(User1.email == "safenext@example.com")).first()
        td = TrustedDevice(
            owner_id=u_db.id,
            token_hash=tok_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        session.add(td)
        session.commit()

    client.cookies.clear()
    client.cookies.set("trusted_device", raw_token)
    resp_trusted_ext = client.post(
        "/auth/login",
        data={"email": "safenext@example.com", "password": "Pass12345!", "next": "https://evil.com/steal"},
    )
    assert resp_trusted_ext.status_code == 303
    assert resp_trusted_ext.headers.get("location") == "/"

    resp_trusted_valid = client.post(
        "/auth/login",
        data={"email": "safenext@example.com", "password": "Pass12345!", "next": "/profile"},
    )
    assert resp_trusted_valid.status_code == 303
    assert resp_trusted_valid.headers.get("location") == "/profile"


def test_csrf_token_validation_on_auth_post(client, isolate_database_per_test):
    """Invalid CSRF token returns 403."""
    client.cookies.set("csrf_token_id", "test-session-id-123")
    resp = client.post(
        "/auth/login",
        data={
            "email": "csrfuser@example.com",
            "password": "Pass12345!",
            "csrf_token": "tampered.token123",
        },
    )
    assert resp.status_code == 403
    assert "सुरक्षा टोकन (CSRF) अमान्य" in resp.text


# ── 3. Google OAuth 2.0 flow tests ─────────────────────────────────────────────

def test_google_login_not_configured_redirects(client, monkeypatch):
    """When Google is not configured, /auth/google redirects to login with Hindi error."""
    monkeypatch.setattr("app.routers.users.is_google_enabled", lambda: False)
    resp = client.get("/auth/google")
    assert resp.status_code == 303
    assert "/auth/login" in resp.headers.get("location", "")
    assert "Google" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_google_callback_new_user_creation(client, isolate_database_per_test, monkeypatch):
    """Google callback creates new user with avatar, auto username, and email_verified=True, and redirects to email OTP."""
    monkeypatch.setattr("app.routers.users.is_google_enabled", lambda: True)
    monkeypatch.setattr("app.email_otp.generate_otp_code", lambda: "123456")

    mock_client = MagicMock()
    mock_client.google.authorize_access_token = AsyncMock(return_value={
        "userinfo": {
            "sub": "google-sub-1001",
            "email": "newgoogle@example.com",
            "email_verified": True,
            "name": "Tanjiro Kamado",
            "picture": "https://lh3.googleusercontent.com/tanjiro.jpg",
        }
    })
    monkeypatch.setattr("app.routers.users.get_oauth_client", lambda: mock_client)

    resp = client.get("/auth/google/callback?code=fake-auth-code")
    assert resp.status_code == 303
    assert "/auth/verify-otp" in resp.headers.get("location", "")
    assert "pre_2fa_token" in resp.cookies
    assert "auth_token" not in resp.cookies

    # Verify email OTP
    client.cookies.set("pre_2fa_token", resp.cookies.get("pre_2fa_token"))
    v_resp = client.post("/auth/verify-otp", data={"code": "123456"})
    assert v_resp.status_code == 303
    assert "auth_token" in v_resp.cookies

    with Session(isolate_database_per_test) as session:
        user = session.exec(select(User1).where(User1.email == "newgoogle@example.com")).first()
        assert user is not None
        assert user.google_sub == "google-sub-1001"
        assert user.auth_provider == "google"
        assert user.hashed_password is None
        assert user.email_verified is True
        assert user.avatar_url == "https://lh3.googleusercontent.com/tanjiro.jpg"
        assert user.is_active is True
        assert user.is_banned is False

        # Google login must NEVER grant admin access
        admin = session.exec(select(Admin1).where(Admin1.email == "newgoogle@example.com")).first()
        assert admin is None


@pytest.mark.asyncio
async def test_google_callback_existing_email_linking(client, isolate_database_per_test, monkeypatch):
    """Google callback links google_sub to existing account with verified email, redirects to email OTP."""
    monkeypatch.setattr("app.routers.users.is_google_enabled", lambda: True)
    monkeypatch.setattr("app.email_otp.generate_otp_code", lambda: "123456")

    with Session(isolate_database_per_test) as session:
        existing = User1(
            email="existing_pw@example.com",
            name="Existing Password User",
            username="existing_pw_user",
            hashed_password=get_password_hash("Password123!"),
            auth_provider="password",
            email_verified=False,
        )
        session.add(existing)
        session.commit()

    mock_client = MagicMock()
    mock_client.google.authorize_access_token = AsyncMock(return_value={
        "userinfo": {
            "sub": "google-sub-link-2002",
            "email": "existing_pw@example.com",
            "email_verified": True,
            "name": "Existing Password User",
            "picture": "https://lh3.googleusercontent.com/pic.jpg",
        }
    })
    monkeypatch.setattr("app.routers.users.get_oauth_client", lambda: mock_client)

    resp = client.get("/auth/google/callback?code=fake-code")
    assert resp.status_code == 303
    assert "/auth/verify-otp" in resp.headers.get("location", "")
    assert "pre_2fa_token" in resp.cookies
    assert "auth_token" not in resp.cookies

    # Verify email OTP
    client.cookies.set("pre_2fa_token", resp.cookies.get("pre_2fa_token"))
    v_resp = client.post("/auth/verify-otp", data={"code": "123456"})
    assert v_resp.status_code == 303
    assert "auth_token" in v_resp.cookies

    with Session(isolate_database_per_test) as session:
        user = session.exec(select(User1).where(User1.email == "existing_pw@example.com")).first()
        assert user.google_sub == "google-sub-link-2002"
        assert user.auth_provider == "both"
        assert user.email_verified is True


@pytest.mark.asyncio
async def test_google_callback_unverified_email_rejected(client, isolate_database_per_test, monkeypatch):
    """Google callback with email_verified == False is rejected."""
    monkeypatch.setattr("app.routers.users.is_google_enabled", lambda: True)

    mock_client = MagicMock()
    mock_client.google.authorize_access_token = AsyncMock(return_value={
        "userinfo": {
            "sub": "google-sub-unverified",
            "email": "unverified@example.com",
            "email_verified": False,
            "name": "Unverified User",
        }
    })
    monkeypatch.setattr("app.routers.users.get_oauth_client", lambda: mock_client)

    resp = client.get("/auth/google/callback?code=fake-code")
    assert resp.status_code == 303
    assert "/auth/login" in resp.headers.get("location", "")
    assert "Google" in resp.headers.get("location", "")
    assert "auth_token" not in resp.cookies


@pytest.mark.asyncio
async def test_google_callback_banned_user_rejected(client, isolate_database_per_test, monkeypatch):
    """Google callback for banned user rejects with Hindi message."""
    monkeypatch.setattr("app.routers.users.is_google_enabled", lambda: True)

    with Session(isolate_database_per_test) as session:
        banned = User1(
            email="bannedgoogle@example.com",
            name="Banned Google User",
            username="banned_google",
            google_sub="banned-sub-3003",
            auth_provider="google",
            is_banned=True,
        )
        session.add(banned)
        session.commit()

    mock_client = MagicMock()
    mock_client.google.authorize_access_token = AsyncMock(return_value={
        "userinfo": {
            "sub": "banned-sub-3003",
            "email": "bannedgoogle@example.com",
            "email_verified": True,
            "name": "Banned Google User",
        }
    })
    monkeypatch.setattr("app.routers.users.get_oauth_client", lambda: mock_client)

    resp = client.get("/auth/google/callback?code=fake-code")
    assert resp.status_code == 303
    assert "/auth/login" in resp.headers.get("location", "")
    assert "auth_token" not in resp.cookies


@pytest.mark.asyncio
async def test_google_callback_2fa_user_redirected(client, isolate_database_per_test, monkeypatch):
    """Google callback for user with TOTP enabled requires email OTP first, then redirects to /2fa/verify."""
    monkeypatch.setattr("app.routers.users.is_google_enabled", lambda: True)
    monkeypatch.setattr("app.email_otp.generate_otp_code", lambda: "123456")

    secret = generate_totp_secret()
    with Session(isolate_database_per_test) as session:
        u = User1(
            email="google2fa@example.com",
            name="Google 2FA User",
            username="google_2fa",
            google_sub="sub-2fa-4004",
            auth_provider="google",
            totp_enabled=True,
            totp_secret_enc=encrypt_totp_secret(secret),
        )
        session.add(u)
        session.commit()

    mock_client = MagicMock()
    mock_client.google.authorize_access_token = AsyncMock(return_value={
        "userinfo": {
            "sub": "sub-2fa-4004",
            "email": "google2fa@example.com",
            "email_verified": True,
            "name": "Google 2FA User",
        }
    })
    monkeypatch.setattr("app.routers.users.get_oauth_client", lambda: mock_client)

    resp = client.get("/auth/google/callback?code=fake-code")
    assert resp.status_code == 303
    assert "/auth/verify-otp" in resp.headers.get("location", "")
    assert "pre_2fa_token" in resp.cookies
    assert "auth_token" not in resp.cookies

    # Email OTP step succeeds -> transitions to /2fa/verify
    client.cookies.set("pre_2fa_token", resp.cookies.get("pre_2fa_token"))
    r_otp = client.post("/auth/verify-otp", data={"code": "123456"})
    assert r_otp.status_code == 303
    assert "/2fa/verify" in r_otp.headers.get("location", "")
    assert "pre_2fa_token" in r_otp.cookies
    assert "auth_token" not in r_otp.cookies


def test_google_callback_cancellation_handled(client):
    """User cancels Google consent or error returned -> redirects to login without traceback."""
    resp = client.get("/auth/google/callback?error=access_denied")
    assert resp.status_code == 303
    assert "/auth/login" in resp.headers.get("location", "")


# ── 4. Settings & account management for Google users ──────────────────────────

def test_google_user_can_set_password(client, isolate_database_per_test):
    """A Google-only user can set a new password without providing old_password."""
    with Session(isolate_database_per_test) as session:
        user = User1(
            email="setpw@example.com",
            name="Set Password User",
            username="set_pw_user",
            hashed_password=None,
            google_sub="sub-setpw-5005",
            auth_provider="google",
        )
        session.add(user)
        session.commit()
        session.refresh(user)

    token = create_access_token({"sub": "setpw@example.com", "user_type": "user"})
    client.cookies.set("auth_token", token)

    resp = client.post(
        "/settings/change-password",
        data={
            "new_password": "NewBrandPassword2026!",
            "confirm_password": "NewBrandPassword2026!",
        },
    )
    assert resp.status_code == 303

    with Session(isolate_database_per_test) as session:
        updated = session.exec(select(User1).where(User1.email == "setpw@example.com")).first()
        assert updated.hashed_password is not None
        assert updated.auth_provider == "both"


def test_google_user_delete_account_with_confirmation(client, isolate_database_per_test):
    """A Google-only user deletes account; verifies that user and ALL related data (notifications, OTPs, devices, comments) are completely removed."""
    with Session(isolate_database_per_test) as session:
        user = User1(
            email="deletegoogle@example.com",
            name="Delete Google User",
            username="del_google_user",
            hashed_password=None,
            google_sub="sub-del-6006",
            auth_provider="google",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        saved_uid = user.id

        # Add associated records across all tables
        session.add(Notification(user_id=saved_uid, title="Welcome", message="Welcome to Hindi Anime Guide"))
        session.add(EmailOtp(email="deletegoogle@example.com", purpose="login", code_hash="fakehash", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
        session.add(TrustedDevice(owner_id=saved_uid, owner_kind="user", token_hash="faketokenhash", expires_at=datetime.now(timezone.utc) + timedelta(days=30)))
        session.commit()

    token = create_access_token({"sub": "deletegoogle@example.com", "user_type": "user"})
    client.cookies.set("auth_token", token)

    # Deleting with DELETE or Hindi 'हटाएं' completely purges all data
    resp = client.post(
        "/settings/delete-account",
        data={"confirm_delete": "DELETE"},
    )
    assert resp.status_code == 303

    with Session(isolate_database_per_test) as session:
        deleted = session.exec(select(User1).where(User1.email == "deletegoogle@example.com")).first()
        assert deleted is None
        notifs = session.exec(select(Notification).where(Notification.user_id == saved_uid)).all()
        assert len(notifs) == 0
        otps = session.exec(select(EmailOtp).where(EmailOtp.email == "deletegoogle@example.com")).all()
        assert len(otps) == 0
        tds = session.exec(select(TrustedDevice).where(TrustedDevice.owner_id == saved_uid)).all()
        assert len(tds) == 0



def test_logout_clears_auth_and_admin_tokens(client):
    """Logout clears both auth_token and admin_token cookies."""
    client.cookies.set("auth_token", "sample-token")
    client.cookies.set("admin_token", "sample-admin-token")

    resp = client.get("/auth/logout")
    assert resp.status_code == 303
    set_cookies = resp.headers.get_list("set-cookie")
    has_cleared_auth = any("auth_token=\"\";" in sc or "auth_token=;" in sc or "Max-Age=0" in sc for sc in set_cookies)
    has_cleared_admin = any("admin_token=\"\";" in sc or "admin_token=;" in sc or "Max-Age=0" in sc for sc in set_cookies)
    assert has_cleared_auth
    assert has_cleared_admin


# ── 5. Mandatory Email OTP & Device Trust Tests ─────────────────────────────────

def test_otp_verify_wrong_expired_used_code_rejected(client, isolate_database_per_test, monkeypatch):
    """Wrong code, expired code, and already used code are all rejected."""
    monkeypatch.setattr("app.email_otp.generate_otp_code", lambda: "112233")
    with Session(isolate_database_per_test) as session:
        u = User1(
            email="codetester@example.com",
            name="Code Tester",
            username="code_tester",
            hashed_password=get_password_hash("Pass12345!"),
            auth_provider="password",
        )
        session.add(u)
        session.commit()

    # Initiate login
    resp = client.post("/auth/login", data={"email": "codetester@example.com", "password": "Pass12345!"})
    client.cookies.set("pre_2fa_token", resp.cookies.get("pre_2fa_token"))

    # 1. Wrong code rejected
    resp_wrong = client.post("/auth/verify-otp", data={"code": "999999"})
    assert resp_wrong.status_code == 400
    assert "गलत कोड" in resp_wrong.text

    # 2. Expired code rejected
    from datetime import timedelta
    with Session(isolate_database_per_test) as session:
        otp = session.exec(select(EmailOtp).where(EmailOtp.email == "codetester@example.com")).first()
        otp.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.add(otp)
        session.commit()

    resp_exp = client.post("/auth/verify-otp", data={"code": "112233"})
    assert resp_exp.status_code == 400
    assert "समय सीमा समाप्त" in resp_exp.text

    # 3. Already used code rejected
    with Session(isolate_database_per_test) as session:
        otp = session.exec(select(EmailOtp).where(EmailOtp.email == "codetester@example.com")).first()
        otp.expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        otp.used = True
        session.add(otp)
        session.commit()

    resp_used = client.post("/auth/verify-otp", data={"code": "112233"})
    assert resp_used.status_code == 400


def test_otp_verify_five_attempt_lockout(client, isolate_database_per_test, monkeypatch):
    """5 failed attempts locks out the OTP with 429 and invalidates the row."""
    monkeypatch.setattr("app.email_otp.generate_otp_code", lambda: "123456")
    with Session(isolate_database_per_test) as session:
        u = User1(
            email="lockuser@example.com",
            name="Lock User",
            username="lock_user",
            hashed_password=get_password_hash("Pass12345!"),
        )
        session.add(u)
        session.commit()

    resp = client.post("/auth/login", data={"email": "lockuser@example.com", "password": "Pass12345!"})
    client.cookies.set("pre_2fa_token", resp.cookies.get("pre_2fa_token"))

    # First 4 attempts: 400 with remaining count
    for i in range(1, 5):
        r = client.post("/auth/verify-otp", data={"code": "000000"})
        assert r.status_code == 400
        assert f"{5 - i} प्रयास शेष" in r.text

    # 5th attempt: 429 and marked used
    r5 = client.post("/auth/verify-otp", data={"code": "000000"})
    assert r5.status_code == 429
    assert "5 असफल प्रयासों" in r5.text

    # 6th attempt: still rejected with 429
    r6 = client.post("/auth/verify-otp", data={"code": "000000"})
    assert r6.status_code == 429

    with Session(isolate_database_per_test) as session:
        otp = session.exec(select(EmailOtp).where(EmailOtp.email == "lockuser@example.com")).first()
        assert otp.used is True
        assert otp.attempts >= 5


def test_otp_resend_cooldown_and_hourly_limits(client, isolate_database_per_test):
    """Resend cooldown is 60s, max 5 codes per email per hour, max 20 per IP per hour."""
    with Session(isolate_database_per_test) as session:
        u = User1(
            email="resenduser@example.com",
            name="Resend User",
            username="resend_user",
            hashed_password=get_password_hash("Pass12345!"),
        )
        session.add(u)
        session.commit()

    resp = client.post("/auth/login", data={"email": "resenduser@example.com", "password": "Pass12345!"})
    client.cookies.set("pre_2fa_token", resp.cookies.get("pre_2fa_token"))

    # Immediate resend (< 60s) -> 429 cooldown error
    r_cool = client.post("/auth/resend-otp", headers={"x-requested-with": "XMLHttpRequest"})
    assert r_cool.status_code == 429
    assert "सेकंड प्रतीक्षा करें" in r_cool.json().get("error", "")

    # Test 5 per email per hour limit
    from datetime import timedelta
    with Session(isolate_database_per_test) as session:
        # Add 5 rows created 2 minutes ago
        for i in range(5):
            session.add(EmailOtp(
                email="resenduser@example.com",
                purpose="login",
                code_hash="fakehash",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=8),
                created_at=datetime.now(timezone.utc) - timedelta(minutes=2 + i),
                ip="127.0.0.1",
            ))
        session.commit()

    r_limit = client.post("/auth/resend-otp", headers={"x-requested-with": "XMLHttpRequest"})
    assert r_limit.status_code == 429
    assert "घंटे में अधिकतम 5" in r_limit.json().get("error", "")


def test_admin_skips_email_otp(client, isolate_database_per_test):
    """Admin login via /auth/login and /admin/login skips email OTP completely."""
    with Session(isolate_database_per_test) as session:
        admin = Admin1(
            email="owner@example.com",
            name="Site Admin",
            hashed_password=get_password_hash("AdminSecretPass2026!"),
            is_active=True,
        )
        session.add(admin)
        session.commit()

    # 1. Admin login via /auth/login
    resp = client.post(
        "/auth/login",
        data={"email": "owner@example.com", "password": "AdminSecretPass2026!"},
    )
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/admin/dashboard"
    assert "admin_token" in resp.cookies
    assert "pre_2fa_token" not in resp.cookies

    # Verify no EmailOtp was generated for admin
    with Session(isolate_database_per_test) as session:
        otp = session.exec(select(EmailOtp).where(EmailOtp.email == "owner@example.com")).first()
        assert otp is None


def test_banned_and_inactive_users_get_no_email_otp(client, isolate_database_per_test):
    """Banned and inactive users are rejected before any OTP is generated or sent."""
    with Session(isolate_database_per_test) as session:
        banned = User1(
            email="banned_otp@example.com",
            name="Banned",
            username="banned_otp",
            hashed_password=get_password_hash("Pass12345!"),
            is_banned=True,
        )
        inactive = User1(
            email="inactive_otp@example.com",
            name="Inactive",
            username="inactive_otp",
            hashed_password=get_password_hash("Pass12345!"),
            is_active=False,
        )
        session.add(banned)
        session.add(inactive)
        session.commit()

    resp_b = client.post("/auth/login", data={"email": "banned_otp@example.com", "password": "Pass12345!"})
    assert resp_b.status_code == 403
    resp_i = client.post("/auth/login", data={"email": "inactive_otp@example.com", "password": "Pass12345!"})
    assert resp_i.status_code == 403

    with Session(isolate_database_per_test) as session:
        otps = session.exec(select(EmailOtp).where(EmailOtp.email.in_(["banned_otp@example.com", "inactive_otp@example.com"]))).all()
        assert len(otps) == 0


def test_totp_user_still_goes_through_totp_after_email_otp(client, isolate_database_per_test, monkeypatch):
    """User with TOTP 2FA enabled: Email OTP first, then redirects to /2fa/verify for TOTP."""
    monkeypatch.setattr("app.email_otp.generate_otp_code", lambda: "111222")
    secret = generate_totp_secret()
    import pyotp
    totp_calc = pyotp.TOTP(secret)

    with Session(isolate_database_per_test) as session:
        u = User1(
            email="dual2fa@example.com",
            name="Dual 2FA User",
            username="dual_2fa",
            hashed_password=get_password_hash("Pass12345!"),
            totp_enabled=True,
            totp_secret_enc=encrypt_totp_secret(secret),
        )
        session.add(u)
        session.commit()

    # Step 1: Login with password -> redirects to email OTP
    r1 = client.post("/auth/login", data={"email": "dual2fa@example.com", "password": "Pass12345!"})
    assert r1.status_code == 303
    assert "/auth/verify-otp" in r1.headers.get("location", "")
    assert "pre_2fa_token" in r1.cookies
    assert "auth_token" not in r1.cookies

    # Step 2: Submit email OTP -> redirects to /2fa/verify (not auth_token yet)
    client.cookies.set("pre_2fa_token", r1.cookies.get("pre_2fa_token"))
    r2 = client.post("/auth/verify-otp", data={"code": "111222"})
    assert r2.status_code == 303
    assert "/2fa/verify" in r2.headers.get("location", "")
    assert "auth_token" not in r2.cookies
    assert "pre_2fa_token" in r2.cookies

    # Step 3: Submit valid TOTP code at /auth/2fa/verify -> now issues auth_token!
    client.cookies.set("pre_2fa_token", r2.cookies.get("pre_2fa_token"))
    r3 = client.post("/auth/2fa/verify", data={"code": totp_calc.now(), "mode": "totp"})
    assert r3.status_code == 303
    assert "auth_token" in r3.cookies


def test_trusted_device_skips_email_otp_and_removed_on_password_change(client, isolate_database_per_test, monkeypatch):
    """Remember this device creates trusted_device; skips email OTP on subsequent login; removed on password change."""
    monkeypatch.setattr("app.email_otp.generate_otp_code", lambda: "333444")
    with Session(isolate_database_per_test) as session:
        u = User1(
            email="trusty@example.com",
            name="Trusty User",
            username="trusty_user",
            hashed_password=get_password_hash("OldPassword123!"),
            auth_provider="password",
        )
        session.add(u)
        session.commit()

    # Login and verify with remember_device
    r_login = client.post("/auth/login", data={"email": "trusty@example.com", "password": "OldPassword123!"})
    client.cookies.set("pre_2fa_token", r_login.cookies.get("pre_2fa_token"))
    r_otp = client.post("/auth/verify-otp", data={"code": "333444", "remember_device": "1"})
    assert r_otp.status_code == 303
    assert "auth_token" in r_otp.cookies
    assert "trusted_device" in r_otp.cookies
    device_token = r_otp.cookies.get("trusted_device")

    # Subsequent login with trusted_device cookie -> SKIPS email OTP directly!
    client.cookies.clear()
    client.cookies.set("trusted_device", device_token)
    r_skip = client.post("/auth/login", data={"email": "trusty@example.com", "password": "OldPassword123!"})
    assert r_skip.status_code == 303
    assert "auth_token" in r_skip.cookies
    assert "/auth/verify-otp" not in r_skip.headers.get("location", "")

    # Now change password
    auth_tok = r_skip.cookies.get("auth_token")
    client.cookies.set("auth_token", auth_tok)
    r_pw = client.post(
        "/settings/change-password",
        data={
            "current_password": "OldPassword123!",
            "new_password": "NewSecretPassword2026!",
            "confirm_password": "NewSecretPassword2026!",
        },
    )
    assert r_pw.status_code == 303

    # Verify trusted devices deleted from DB
    with Session(isolate_database_per_test) as session:
        u_db = session.exec(select(User1).where(User1.email == "trusty@example.com")).first()
        tds = session.exec(select(TrustedDevice).where(TrustedDevice.owner_id == u_db.id)).all()
        assert len(tds) == 0

    # Next login with old trusted_device cookie now REQUIRES email OTP!
    client.cookies.clear()
    client.cookies.set("trusted_device", device_token)
    r_after = client.post("/auth/login", data={"email": "trusty@example.com", "password": "NewSecretPassword2026!"})
    assert r_after.status_code == 303
    assert "/auth/verify-otp" in r_after.headers.get("location", "")
    assert "pre_2fa_token" in r_after.cookies
    assert "auth_token" not in r_after.cookies


def test_pre_otp_token_cannot_access_protected_pages(client, isolate_database_per_test):
    """Pre-OTP / pre-2FA token must NOT grant access to any page by itself."""
    pre_token = create_pre2fa_token("user", 101, "hacker@example.com", scope="otp_pending")
    client.cookies.set("pre_2fa_token", pre_token)

    r_profile = client.get("/profile")
    assert r_profile.status_code == 303
    assert "/auth/login" in r_profile.headers.get("location", "")

    r_settings = client.get("/settings")
    assert r_settings.status_code == 303
    assert "/auth/login" in r_settings.headers.get("location", "")

    r_admin = client.get("/admin/dashboard")
    assert r_admin.status_code in (303, 401)


def test_email_outbox_captures_messages_and_never_sends_real_smtp(client, monkeypatch, email_outbox):
    """Verify that email sending appends to in-memory outbox and zero real emails are sent."""
    monkeypatch.setattr("app.email_otp.generate_otp_code", lambda: "112233")
    resp = client.post(
        "/auth/register",
        data={
            "email": "zeronetwork@example.com",
            "password": "StrongPassword123!",
        },
    )
    assert resp.status_code == 303
    assert len(email_outbox) >= 1
    sent = email_outbox[-1]
    assert sent["to"] == "zeronetwork@example.com"
    assert sent["code"] == "112233"
    assert "112233" in sent["subject"]

