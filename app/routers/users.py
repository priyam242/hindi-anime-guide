"""Public user registration, login, profile, settings, comments."""
from __future__ import annotations
import logging
import os
import re
import time
from collections import defaultdict
from datetime import timedelta, datetime, timezone
from threading import Lock
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select
import sqlmodel

from authlib.integrations.starlette_client import OAuth

from ..config import get_settings, settings
from ..auth import (
    create_access_token, get_password_hash, require_user,
    verify_password, get_current_user, set_auth_cookie, clear_auth_cookie,
    DUMMY_PASSWORD_HASH, delete_user_completely,
)
from ..totp import generate_csrf_token, verify_csrf_token
from .. import db
from ..models import User1, Admin1, Comment, Article, Notification, Bookmark, Category, EmailOtp, TrustedDevice
from ..templates_config import render
from ..utils import (
    validate_english_name, validate_username, validate_password_strength,
)
from .admin import create_notification

router = APIRouter(tags=["users"])
_auth_log = logging.getLogger(__name__)


# ---------- RATE LIMITING & SECURITY HELPERS ----------
class RateLimiter:
    """Thread-safe in-memory rate limiter for auth attempts."""
    def __init__(self, max_attempts: int = 5, window_seconds: int = 900):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts = defaultdict(list)
        self._lock = Lock()

    def is_limited(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            self._attempts[key] = [t for t in self._attempts[key] if t > cutoff]
            return len(self._attempts[key]) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._attempts[key].append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


auth_limiter = RateLimiter(max_attempts=5, window_seconds=900)


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def safe_redirect_url(url: Optional[str], default: str = "/") -> str:
    """Validate open redirect safety: only relative paths starting with single '/'."""
    if not url:
        return default
    url = url.strip()
    if url.startswith("/") and not url.startswith("//") and not url.startswith("/\\") and "://" not in url:
        return url
    return default


def get_csrf_context(request: Request) -> tuple[str, str]:
    """Return (csrf_token, csrf_token_id) for form embedding and cookie setting."""
    import secrets
    csrf_id = request.cookies.get("csrf_token_id")
    if not csrf_id:
        csrf_id = secrets.token_hex(16)
    csrf_token = generate_csrf_token(csrf_id)
    return csrf_token, csrf_id


def verify_request_csrf(request: Request, submitted_csrf: Optional[str]) -> bool:
    """Verify submitted CSRF token against secret in cookie."""
    settings = get_settings()
    csrf_id = request.cookies.get("csrf_token_id")
    if settings.env.lower() in ("test", "testing") and not submitted_csrf:
        return True
    if not csrf_id or not submitted_csrf:
        return False
    return verify_csrf_token(submitted_csrf, csrf_id)


def is_google_enabled() -> bool:
    settings = get_settings()
    cid = (settings.google_client_id or "").strip()
    csecret = (settings.google_client_secret or "").strip()
    return bool(cid and csecret and cid not in ("set_in_env", ""))


# Starlette OAuth instance
oauth = OAuth()
_google_registered = False


def get_oauth_client():
    global _google_registered
    settings = get_settings()
    if not _google_registered and is_google_enabled():
        oauth.register(
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        _google_registered = True
    return oauth


# ---------- AUTH: LOGIN & LOGOUT ----------
@router.get("/auth/login", response_class=HTMLResponse)
async def user_login_page(
    request: Request,
    registered: str = "",
    tab: str = "signin",
    error: Optional[str] = None,
):
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url="/admin/dashboard" if user.get("user_type") == "admin" else "/", status_code=303)
    
    settings = get_settings()
    csrf_token, csrf_id = get_csrf_context(request)
    google_enabled = is_google_enabled()
    next_url = safe_redirect_url(request.query_params.get("next"), default="")
    
    html = render(
        "auth/login.html",
        error=error,
        registered=registered,
        active_tab=tab,
        csrf_token=csrf_token,
        google_enabled=google_enabled,
        next_url=next_url,
        request=request,
    )
    response = HTMLResponse(content=html)
    is_secure = (settings.env.lower() == "production")
    response.set_cookie("csrf_token_id", csrf_id, httponly=True, samesite="lax", secure=is_secure)
    return response


@router.post("/auth/login", response_class=HTMLResponse)
async def user_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: Optional[str] = Form(None),
    next: Optional[str] = Form(None),
):
    settings = get_settings()
    csrf_tok, csrf_id = get_csrf_context(request)
    google_enabled = is_google_enabled()
    target_next = safe_redirect_url(next, default="/")
    
    # 1. CSRF Verification
    if not verify_request_csrf(request, csrf_token):
        html = render(
            "auth/login.html",
            error="सुरक्षा टोकन (CSRF) अमान्य या समाप्त हो गया है। कृपया पुनः प्रयास करें।",
            registered=None,
            active_tab="signin",
            csrf_token=csrf_tok,
            google_enabled=google_enabled,
            next_url=target_next,
            request=request,
        )
        return HTMLResponse(content=html, status_code=403)
    
    clean_email = email.strip().lower()
    rate_key = f"{get_client_ip(request)}:{clean_email}"
    
    # 2. Rate Limiting Check
    if auth_limiter.is_limited(rate_key):
        html = render(
            "auth/login.html",
            error="बहुत अधिक असफल प्रयास। थोड़ी देर बाद कोशिश करें। (Too many attempts. Please try again after 15 minutes)",
            registered=None,
            active_tab="signin",
            csrf_token=csrf_tok,
            google_enabled=google_enabled,
            next_url=target_next,
            request=request,
        )
        return HTMLResponse(content=html, status_code=429)

    with Session(db.engine) as session:
        # Check regular user first (case-insensitive)
        user = session.exec(select(User1).where(func.lower(User1.email) == clean_email)).first()
        
        # Google-only user attempting password login
        if user and not user.hashed_password:
            auth_limiter.record_failure(rate_key)
            html = render(
                "auth/login.html",
                error="इस खाते में Google से लॉगिन करें (Please log in with Google for this account)",
                registered=None,
                active_tab="signin",
                csrf_token=csrf_tok,
                google_enabled=google_enabled,
                next_url=target_next,
                request=request,
            )
            return HTMLResponse(content=html, status_code=400)

        if user and verify_password(password, user.hashed_password):
            if user.is_banned:
                auth_limiter.record_failure(rate_key)
                html = render(
                    "auth/login.html",
                    error="आपका खाता निलंबित कर दिया गया है (Your account has been suspended)",
                    registered=None,
                    active_tab="signin",
                    csrf_token=csrf_tok,
                    google_enabled=google_enabled,
                    next_url=target_next,
                    request=request,
                )
                return HTMLResponse(content=html, status_code=403)
            if not user.is_active:
                auth_limiter.record_failure(rate_key)
                html = render(
                    "auth/login.html",
                    error="आपका खाता निष्क्रिय है (Account is deactivated)",
                    registered=None,
                    active_tab="signin",
                    csrf_token=csrf_tok,
                    google_enabled=google_enabled,
                    next_url=target_next,
                    request=request,
                )
                return HTMLResponse(content=html, status_code=403)

            auth_limiter.reset(rate_key)

            from ..totp import _check_trusted_device_raw, create_pre2fa_token
            device_trusted = False
            if settings.otp_trust_device_days > 0:
                device_trusted = _check_trusted_device_raw(session, "user", user.id, request)

            if device_trusted:
                tv = getattr(user, "token_version", 1) or 1
                token = create_access_token(
                    data={"sub": user.email, "name": user.name, "user_type": "user"},
                    expires_delta=timedelta(hours=24 * 7), token_version=tv,
                )
                response = RedirectResponse(url=target_next, status_code=303)
                set_auth_cookie(response, token, cookie_name="auth_token", max_age=604800)
                return response

            # Untrusted device: Send mandatory Email OTP
            from ..email_otp import create_and_send_email_otp
            client_ip = get_client_ip(request)
            ok, err_msg, otp_row, code = await create_and_send_email_otp(
                session, user.email, purpose="login", ip=client_ip
            )
            if not ok:
                status_c = 429 if ("सीमा" in (err_msg or "") or "सेकंड" in (err_msg or "")) else 400
                html = render(
                    "auth/login.html",
                    error=err_msg or "सत्यापन कोड भेजने में त्रुटि हुई",
                    registered=None,
                    active_tab="signin",
                    csrf_token=csrf_tok,
                    google_enabled=google_enabled,
                    next_url=target_next,
                    request=request,
                )
                return HTMLResponse(content=html, status_code=status_c)

            pre_token = create_pre2fa_token("user", user.id, user.email, scope="otp_pending", purpose="login", next_url=target_next)
            response = RedirectResponse(url="/auth/verify-otp", status_code=303)
            set_auth_cookie(response, pre_token, cookie_name="pre_2fa_token", max_age=600)
            return response

        # Check admin user as well so admin can login from /auth/login
        admin = session.exec(select(Admin1).where(func.lower(Admin1.email) == clean_email)).first()
        if admin and verify_password(password, admin.hashed_password):
            if not admin.is_active:
                auth_limiter.record_failure(rate_key)
                html = render(
                    "auth/login.html",
                    error="Account is deactivated",
                    registered=None,
                    active_tab="signin",
                    csrf_token=csrf_tok,
                    google_enabled=google_enabled,
                    next_url=target_next,
                    request=request,
                )
                return HTMLResponse(content=html, status_code=403)

            auth_limiter.reset(rate_key)
            # Admin login via /auth/login — direct access to /admin/dashboard
            tv = getattr(admin, "token_version", 1) or 1
            token = create_access_token(
                data={"sub": admin.email, "name": admin.name, "user_type": "admin"},
                expires_delta=timedelta(hours=24), token_version=tv,
            )
            response = RedirectResponse(url="/admin/dashboard", status_code=303)
            set_auth_cookie(response, token, cookie_name="admin_token", max_age=86400)
            set_auth_cookie(response, token, cookie_name="auth_token", max_age=86400)
            return response

        # Timing attack mitigation: verify against dummy hash when account not found
        verify_password(password, DUMMY_PASSWORD_HASH)
        auth_limiter.record_failure(rate_key)
        html = render(
            "auth/login.html",
            error="ईमेल या पासवर्ड गलत है (Invalid email or password)",
            registered=None,
            active_tab="signin",
            csrf_token=csrf_tok,
            google_enabled=google_enabled,
            next_url=target_next,
            request=request,
        )
        return HTMLResponse(content=html, status_code=400)


@router.get("/auth/register", response_class=HTMLResponse)
async def user_register_page(request: Request):
    user = await get_current_user(request)
    if user and user.get("user_type") == "user":
        return RedirectResponse(url="/", status_code=303)
    
    settings = get_settings()
    csrf_token, csrf_id = get_csrf_context(request)
    google_enabled = is_google_enabled()
    html = render("auth/register.html", error=None, csrf_token=csrf_token, google_enabled=google_enabled, request=request)
    response = HTMLResponse(content=html)
    is_secure = (settings.env.lower() == "production")
    response.set_cookie("csrf_token_id", csrf_id, httponly=True, samesite="lax", secure=is_secure)
    return response


@router.get("/auth/google")
async def auth_google_login(request: Request):
    """Start real Google OAuth 2.0 flow."""
    if not is_google_enabled():
        return RedirectResponse(
            url=f"/auth/login?error={quote('Google लॉगिन अभी उपलब्ध नहीं है')}",
            status_code=303
        )
    
    settings = get_settings()
    redirect_uri = f"{settings.public_base_url.rstrip('/')}/auth/google/callback"
    client = get_oauth_client()
    return await client.google.authorize_redirect(request, redirect_uri, prompt="select_account")


@router.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    """Handle Google OAuth 2.0 callback, ID token verification, and user resolution."""
    if not is_google_enabled():
        return RedirectResponse(
            url=f"/auth/login?error={quote('Google लॉगिन अभी उपलब्ध नहीं है')}",
            status_code=303
        )

    # Check if Google returned an error (e.g. user cancelled, access_denied)
    err = request.query_params.get("error")
    if err:
        return RedirectResponse(
            url=f"/auth/login?error={quote('Google प्रमाणीकरण रद्द या अस्वीकृत कर दिया गया।')}",
            status_code=303
        )

    client = get_oauth_client()
    try:
        token = await client.google.authorize_access_token(request)
    except Exception as exc:
        _auth_log.error("Google authorize_access_token failed: %s - %s", type(exc).__name__, exc)
        return RedirectResponse(
            url=f"/auth/login?error={quote('Google प्रमाणीकरण विफल रहा। कृपया पुनः प्रयास करें।')}",
            status_code=303
        )

    user_info = token.get("userinfo")
    if not user_info:
        try:
            user_info = await client.google.parse_id_token(request, token)
        except Exception as exc:
            _auth_log.error("Google parse_id_token failed: %s - %s", type(exc).__name__, exc)
            user_info = None

    if not user_info:
        return RedirectResponse(
            url=f"/auth/login?error={quote('Google से उपयोगकर्ता जानकारी प्राप्त नहीं हो सकी।')}",
            status_code=303
        )

    # Require email_verified == True
    if not user_info.get("email_verified"):
        return RedirectResponse(
            url=f"/auth/login?error={quote('Google पर यह ईमेल सत्यापित नहीं है।')}",
            status_code=303
        )

    google_sub = str(user_info.get("sub"))
    google_email = str(user_info.get("email")).strip().lower()
    google_name = (user_info.get("name") or "").strip()
    google_picture = user_info.get("picture")

    with Session(db.engine) as session:
        # a) Find user by google_sub
        user = session.exec(select(User1).where(User1.google_sub == google_sub)).first()

        # b) Else find by email and link google_sub (verified email)
        if not user:
            user = session.exec(select(User1).where(func.lower(User1.email) == google_email)).first()
            if user:
                user.google_sub = google_sub
                user.email_verified = True
                user.auth_provider = "both" if user.hashed_password else "google"
                if not user.avatar_url and google_picture:
                    user.avatar_url = google_picture
                user.updated_at = datetime.now(timezone.utc)
                session.add(user)
                session.commit()
                session.refresh(user)

        # c) Else create new user
        if not user:
            base_user = re.sub(r"[^a-zA-Z0-9_]", "", google_email.split("@")[0].lower())
            if not base_user or not base_user[0].isalpha():
                base_user = "user_" + base_user
            base_user = base_user[:20]
            clean_username = base_user
            counter = 1
            while session.exec(select(User1).where(func.lower(User1.username) == clean_username.lower())).first():
                clean_username = f"{base_user[:16]}_{counter}"
                counter += 1

            clean_name = google_name if google_name else clean_username
            user = User1(
                name=clean_name,
                username=clean_username,
                email=google_email,
                hashed_password=None,
                avatar_url=google_picture,
                google_sub=google_sub,
                email_verified=True,
                auth_provider="google",
                is_active=True,
                is_banned=False,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(user)
            session.commit()
            session.refresh(user)

            create_notification(
                session,
                "New Google User Registered",
                f"Google user {clean_username} ({google_email}) joined.",
                notification_type="success",
                link="/admin/users"
            )

        # Refuse if user.is_banned or not user.is_active
        if user.is_banned:
            return RedirectResponse(
                url=f"/auth/login?error={quote('आपका खाता निलंबित कर दिया गया है')}",
                status_code=303
            )
        if not user.is_active:
            return RedirectResponse(
                url=f"/auth/login?error={quote('आपका खाता निष्क्रिय है')}",
                status_code=303
            )

        from ..totp import _check_trusted_device_raw, create_pre2fa_token
        device_trusted = False
        if settings.otp_trust_device_days > 0:
            device_trusted = _check_trusted_device_raw(session, "user", user.id, request)

        if device_trusted:
            tv = getattr(user, "token_version", 1) or 1
            token = create_access_token(
                data={"sub": user.email, "name": user.name, "user_type": "user"},
                expires_delta=timedelta(days=7),
                token_version=tv,
            )
            response = RedirectResponse(url="/", status_code=303)
            set_auth_cookie(response, token, cookie_name="auth_token", max_age=604800)
            return response

        # Untrusted device: Send mandatory Email OTP
        from ..email_otp import create_and_send_email_otp
        client_ip = get_client_ip(request)
        ok, err_msg, otp_row, code = await create_and_send_email_otp(
            session, user.email, purpose="login", ip=client_ip
        )
        if not ok:
            return RedirectResponse(
                url=f"/auth/login?error={quote(err_msg or 'सत्यापन कोड भेजने में त्रुटि हुई')}",
                status_code=303
            )

        pre_token = create_pre2fa_token("user", user.id, user.email, scope="otp_pending", purpose="login", next_url="/")
        response = RedirectResponse(url="/auth/verify-otp", status_code=303)
        set_auth_cookie(response, pre_token, cookie_name="pre_2fa_token", max_age=600)
        return response


@router.post("/auth/register", response_class=HTMLResponse)
async def user_register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    name: Optional[str] = Form(None),
    username: Optional[str] = Form(None),
    confirm_password: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    from_page: Optional[str] = Form(None),
):
    settings = get_settings()
    csrf_tok, csrf_id = get_csrf_context(request)
    google_enabled = is_google_enabled()
    clean_email = email.strip().lower() if email else ""

    def render_error(msg: str, status_code: int = 400) -> HTMLResponse:
        target_template = "auth/login.html" if from_page == "login" else "auth/register.html"
        html = render(
            target_template,
            error=msg,
            active_tab="register",
            registered=None,
            csrf_token=csrf_tok,
            google_enabled=google_enabled,
            entered_email=clean_email,
            entered_name=name or "",
            next_url="/",
            request=request,
        )
        return HTMLResponse(content=html, status_code=status_code)

    # 1. CSRF Verification
    if not verify_request_csrf(request, csrf_token):
        return render_error("सुरक्षा टोकन (CSRF) अमान्य या समाप्त हो गया है। कृपया पुनः प्रयास करें।", status_code=403)

    rate_key = f"{get_client_ip(request)}:{clean_email}"

    # 2. Rate limiting check
    if auth_limiter.is_limited(rate_key):
        return render_error(
            "बहुत अधिक प्रयास। थोड़ी देर बाद कोशिश करें। (Too many attempts. Please try again after 15 minutes)",
            status_code=429,
        )

    # 3. Email format validation
    if not clean_email or not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", clean_email):
        auth_limiter.record_failure(rate_key)
        return render_error("कृपया मान्य ईमेल दर्ज करें (Please enter a valid email address)", status_code=400)

    # 4. Confirm password check
    if confirm_password and password != confirm_password:
        auth_limiter.record_failure(rate_key)
        return render_error("पासवर्ड मेल नहीं खा रहे हैं (Passwords do not match)", status_code=400)

    # 5. Password rules (8-128 chars, common check)
    valid_p, err_p = validate_password_strength(password)
    if not valid_p:
        auth_limiter.record_failure(rate_key)
        return render_error(err_p, status_code=400)

    with Session(db.engine) as session:
        # Case-insensitive email uniqueness
        if session.exec(select(User1).where(func.lower(User1.email) == clean_email)).first():
            auth_limiter.record_failure(rate_key)
            return render_error("यह ईमेल पहले से पंजीकृत है। कृपया साइन इन करें।", status_code=400)

        # Name resolution
        if name and name.strip():
            clean_name = name.strip()
            valid_name, err_name = validate_english_name(clean_name)
            if not valid_name:
                auth_limiter.record_failure(rate_key)
                return render_error(err_name, status_code=400)
        else:
            raw_prefix = clean_email.split("@")[0].replace(".", " ").replace("_", " ")
            clean_name = raw_prefix.title() if re.match(r"^[a-zA-Z\s]{2,50}$", raw_prefix) else "Anime Fan"

        # Username resolution
        if username and username.strip():
            clean_username = username.strip()
            valid_u, err_u = validate_username(clean_username)
            if not valid_u:
                auth_limiter.record_failure(rate_key)
                return render_error(err_u, status_code=400)
            if session.exec(select(User1).where(func.lower(User1.username) == clean_username.lower())).first():
                auth_limiter.record_failure(rate_key)
                return render_error("Username already taken", status_code=400)
        else:
            base_user = re.sub(r"[^a-zA-Z0-9_]", "", clean_email.split("@")[0])
            if not base_user or not base_user[0].isalpha():
                base_user = "user_" + base_user
            base_user = base_user[:20]
            clean_username = base_user
            counter = 1
            while session.exec(select(User1).where(func.lower(User1.username) == clean_username.lower())).first():
                clean_username = f"{base_user[:17]}_{counter}"
                counter += 1
        
        # Prepare pending signup payload (name, username, argon2 password hash - never plain text)
        import json
        pending_payload = json.dumps({
            "name": clean_name,
            "username": clean_username,
            "password_hash": get_password_hash(password),
        })

        from ..email_otp import create_and_send_email_otp
        from ..totp import create_pre2fa_token
        client_ip = get_client_ip(request)
        ok, err_msg, otp_row, code = await create_and_send_email_otp(
            session, clean_email, purpose="signup", pending_payload=pending_payload, ip=client_ip
        )
        if not ok:
            status_c = 429 if ("सीमा" in (err_msg or "") or "सेकंड" in (err_msg or "")) else 400
            return render_error(err_msg or "सत्यापन कोड भेजने में त्रुटि हुई", status_code=status_c)

        auth_limiter.reset(rate_key)
        pre_token = create_pre2fa_token("user", None, clean_email, scope="otp_pending", purpose="signup", next_url="/")
        response = RedirectResponse(url="/auth/verify-otp", status_code=303)
        set_auth_cookie(response, pre_token, cookie_name="pre_2fa_token", max_age=600)
        return response


@router.get("/auth/verify-otp", response_class=HTMLResponse)
async def verify_otp_page(request: Request, error: Optional[str] = None):
    settings = get_settings()
    pre_token = request.cookies.get("pre_2fa_token")
    if not pre_token:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    from ..totp import decode_pre2fa_token
    payload = decode_pre2fa_token(pre_token, expected_scope="otp_pending")
    if not payload:
        response = RedirectResponse(url="/auth/login?error=" + quote("सत्यापन सत्र समाप्त हो गया है। कृपया पुनः प्रयास करें।"), status_code=303)
        clear_auth_cookie(response, "pre_2fa_token")
        return response
    
    from ..email_otp import mask_email
    email = payload.get("email", "")
    purpose = payload.get("purpose", "login")
    next_url = safe_redirect_url(payload.get("next"), default="/")
    masked = mask_email(email)
    
    csrf_token, csrf_id = get_csrf_context(request)
    html = render(
        "auth/verify_otp.html",
        masked_email=masked,
        email_raw=email,
        purpose=purpose,
        csrf_token=csrf_token,
        next_url=next_url,
        trust_device_days=settings.otp_trust_device_days,
        error=error,
        request=request,
    )
    response = HTMLResponse(content=html)
    is_secure = (settings.env.lower() == "production")
    response.set_cookie("csrf_token_id", csrf_id, httponly=True, samesite="lax", secure=is_secure)
    return response


@router.post("/auth/verify-otp", response_class=HTMLResponse)
async def post_verify_otp(
    request: Request,
    csrf_token: Optional[str] = Form(None),
    code: Optional[str] = Form(None),
    digit1: Optional[str] = Form(None),
    digit2: Optional[str] = Form(None),
    digit3: Optional[str] = Form(None),
    digit4: Optional[str] = Form(None),
    digit5: Optional[str] = Form(None),
    digit6: Optional[str] = Form(None),
    remember_device: Optional[str] = Form(None),
    next: Optional[str] = Form(None),
):
    settings = get_settings()
    pre_token = request.cookies.get("pre_2fa_token")
    if not pre_token:
        return RedirectResponse(url="/auth/login", status_code=303)

    from ..totp import decode_pre2fa_token, create_pre2fa_token, generate_device_token, hash_device_token
    from ..email_otp import mask_email, verify_otp_hash

    payload = decode_pre2fa_token(pre_token, expected_scope="otp_pending")
    if not payload:
        response = RedirectResponse(url="/auth/login?error=" + quote("सत्यापन सत्र समाप्त हो गया है। कृपया पुनः प्रयास करें।"), status_code=303)
        clear_auth_cookie(response, "pre_2fa_token")
        return response

    email_clean = payload.get("email", "").strip().lower()
    purpose = payload.get("purpose", "login")
    target_next = safe_redirect_url(next or payload.get("next"), default="/")
    masked = mask_email(email_clean)

    def render_error_page(err: str, status_code: int = 400) -> HTMLResponse:
        csrf_tok, csrf_id = get_csrf_context(request)
        html = render(
            "auth/verify_otp.html",
            masked_email=masked,
            email_raw=email_clean,
            purpose=purpose,
            csrf_token=csrf_tok,
            next_url=target_next,
            trust_device_days=settings.otp_trust_device_days,
            error=err,
            request=request,
        )
        resp = HTMLResponse(content=html, status_code=status_code)
        is_sec = (settings.env.lower() == "production")
        resp.set_cookie("csrf_token_id", csrf_id, httponly=True, samesite="lax", secure=is_sec)
        return resp

    if not verify_request_csrf(request, csrf_token):
        return render_error_page("सुरक्षा टोकन (CSRF) अमान्य या समाप्त हो गया है। कृपया पुनः प्रयास करें।", status_code=403)

    # Reconstruct 6-digit code
    submitted_code = (code or "").strip()
    if not submitted_code:
        digits = [digit1, digit2, digit3, digit4, digit5, digit6]
        if all(d is not None for d in digits):
            submitted_code = "".join(str(d).strip() for d in digits)

    if not submitted_code or len(submitted_code) != 6 or not submitted_code.isdigit():
        return render_error_page("कृपया 6 अंकों का संख्यात्मक कोड दर्ज करें (Please enter 6-digit numeric OTP)")

    with Session(db.engine) as session:
        # Find latest unexpired EmailOtp for email + purpose
        now = datetime.now(timezone.utc)
        otp_row = session.exec(
            select(EmailOtp)
            .where(
                EmailOtp.email == email_clean,
                EmailOtp.purpose == purpose,
                EmailOtp.expires_at > now,
            )
            .order_by(EmailOtp.created_at.desc())
        ).first()

        if not otp_row or (otp_row.used and otp_row.attempts < 5):
            return render_error_page("कोड की समय सीमा समाप्त हो गई है या अमान्य है। कृपया नया कोड मंगाएं।")

        if otp_row.attempts >= 5 or otp_row.used:
            otp_row.used = True
            session.add(otp_row)
            session.commit()
            return render_error_page("बहुत अधिक असफल प्रयास। यह कोड रद्द कर दिया गया है। नया कोड मंगाएं।", status_code=429)

        # Verify hash
        if not verify_otp_hash(submitted_code, otp_row.code_hash, settings.secret_key, email_clean, purpose):
            otp_row.attempts += 1
            if otp_row.attempts >= 5:
                otp_row.used = True
                session.add(otp_row)
                session.commit()
                return render_error_page("गलत कोड। 5 असफल प्रयासों के बाद यह कोड अमान्य कर दिया गया है। कृपया पुनः प्रयास करें।", status_code=429)
            session.add(otp_row)
            session.commit()
            remaining = 5 - otp_row.attempts
            return render_error_page(f"गलत कोड। आपके पास {remaining} प्रयास शेष हैं।")

        # Success: mark OTP as used
        otp_row.used = True
        session.add(otp_row)
        session.commit()

        # Step 2: Handle user creation (signup), email update (email_change), or lookup (login)
        user: Optional[User1] = None

        if purpose == "signup":
            import json
            payload_data = {}
            if otp_row.pending_payload:
                try:
                    payload_data = json.loads(otp_row.pending_payload)
                except Exception:
                    pass

            existing_user = session.exec(select(User1).where(func.lower(User1.email) == email_clean)).first()
            if existing_user:
                user = existing_user
            else:
                user_name = payload_data.get("name") or email_clean.split("@")[0].title()
                user_uname = payload_data.get("username") or email_clean.split("@")[0][:20]
                pwd_hash = payload_data.get("password_hash")
                
                base_u = user_uname
                cnt = 1
                while session.exec(select(User1).where(func.lower(User1.username) == user_uname.lower())).first():
                    user_uname = f"{base_u[:16]}_{cnt}"
                    cnt += 1

                user = User1(
                    name=user_name,
                    username=user_uname,
                    email=email_clean,
                    hashed_password=pwd_hash,
                    email_verified=True,
                    auth_provider="password",
                    is_active=True,
                    is_banned=False,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(user)
                session.commit()
                session.refresh(user)

                create_notification(
                    session,
                    "New User Registered",
                    f"New user {user.username} ({user.email}) joined.",
                    notification_type="success",
                    link="/admin/users"
                )

        elif purpose == "email_change":
            # ---------- EMAIL CHANGE: अनिवार्य OTP सत्यापन के बाद ही असली ईमेल अपडेट होता है ----------
            import json
            payload_data = {}
            if otp_row.pending_payload:
                try:
                    payload_data = json.loads(otp_row.pending_payload)
                except Exception:
                    pass

            owner_kind = payload_data.get("owner_kind", "user")
            owner_id = payload_data.get("owner_id")

            if not owner_id:
                return RedirectResponse(
                    url="/settings?error=" + quote("सत्र अमान्य है। कृपया पुनः प्रयास करें।") + "#profile",
                    status_code=303,
                )

            account = session.get(Admin1, owner_id) if owner_kind == "admin" else session.get(User1, owner_id)
            if not account:
                return RedirectResponse(
                    url="/settings?error=" + quote("खाता नहीं मिला।") + "#profile",
                    status_code=303,
                )

            # Race-condition सुरक्षा: दोबारा uniqueness जाँचें
            dup_user = session.exec(
                select(User1).where(
                    func.lower(User1.email) == email_clean,
                    User1.id != (account.id if owner_kind == "user" else -1),
                )
            ).first()
            dup_admin = session.exec(
                select(Admin1).where(
                    func.lower(Admin1.email) == email_clean,
                    Admin1.id != (account.id if owner_kind == "admin" else -1),
                )
            ).first()
            if dup_user or dup_admin:
                return RedirectResponse(
                    url="/settings?error=" + quote("यह ईमेल अब किसी और के द्वारा उपयोग में है।") + "#profile",
                    status_code=303,
                )

            account.email = email_clean
            account.updated_at = datetime.now(timezone.utc)
            if owner_kind == "user":
                account.email_verified = True
            session.add(account)
            session.commit()
            session.refresh(account)

            tv = getattr(account, "token_version", 1) or 1
            token = create_access_token(
                data={"sub": account.email, "name": account.name, "user_type": owner_kind},
                expires_delta=timedelta(hours=24 * 7 if owner_kind == "user" else 24),
                token_version=tv,
            )
            response = RedirectResponse(url="/settings?updated=1#profile", status_code=303)
            clear_auth_cookie(response, "pre_2fa_token")
            if owner_kind == "admin":
                set_auth_cookie(response, token, cookie_name="admin_token", max_age=86400)
            set_auth_cookie(response, token, cookie_name="auth_token", max_age=604800)
            return response

        else:
            # login
            user = session.exec(select(User1).where(func.lower(User1.email) == email_clean)).first()
            if not user:
                return RedirectResponse(url="/auth/login?error=" + quote("उपयोगकर्ता खाता नहीं मिला"), status_code=303)
            if user.is_banned:
                return render_error_page("आपका खाता निलंबित कर दिया गया है", status_code=403)
            if not user.is_active:
                return render_error_page("आपका खाता निष्क्रिय है", status_code=403)

        # Step 3: Check if TOTP 2FA is enabled (signup / login only — email_change पहले ही return हो चुका है)
        if getattr(user, "totp_enabled", False):
            pre_token = create_pre2fa_token("user", user.id, user.email, scope="2fa_pending", next_url=target_next)
            response = RedirectResponse(url="/2fa/verify", status_code=303)
            set_auth_cookie(response, pre_token, cookie_name="pre_2fa_token", max_age=600)
            return response

        # Step 4: Issue final auth_token & handle Remember this device
        response = RedirectResponse(url=target_next, status_code=303)
        clear_auth_cookie(response, "pre_2fa_token")

        if remember_device and settings.otp_trust_device_days > 0:
            raw_token = generate_device_token()
            token_hash = hash_device_token(raw_token)
            expires = datetime.now(timezone.utc) + timedelta(days=settings.otp_trust_device_days)
            ua = request.headers.get("User-Agent", "")[:200]
            td = TrustedDevice(
                owner_kind="user",
                owner_id=user.id,
                token_hash=token_hash,
                label=ua,
                created_at=datetime.now(timezone.utc),
                expires_at=expires,
            )
            session.add(td)
            session.commit()
            
            is_sec = (settings.env.lower() == "production")
            response.set_cookie(
                "trusted_device",
                raw_token,
                max_age=settings.otp_trust_device_days * 86400,
                httponly=True,
                samesite="lax",
                secure=is_sec,
            )

        tv = getattr(user, "token_version", 1) or 1
        token = create_access_token(
            data={"sub": user.email, "name": user.name, "user_type": "user"},
            expires_delta=timedelta(hours=24 * 7),
            token_version=tv,
        )
        set_auth_cookie(response, token, cookie_name="auth_token", max_age=604800)
        return response


@router.post("/auth/resend-otp")
async def resend_otp(request: Request, csrf_token: Optional[str] = Form(None)):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")

    if not verify_request_csrf(request, csrf_token):
        if is_ajax:
            return JSONResponse(status_code=403, content={"ok": False, "error": "सुरक्षा टोकन (CSRF) अमान्य है।"})
        return RedirectResponse(url="/auth/verify-otp?error=" + quote("सुरक्षा टोकन (CSRF) अमान्य है।"), status_code=303)

    pre_token = request.cookies.get("pre_2fa_token")
    if not pre_token:
        if is_ajax:
            return JSONResponse(status_code=401, content={"ok": False, "error": "सत्र समाप्त हो गया है। कृपया पुनः लॉगिन करें।"})
        return RedirectResponse(url="/auth/login", status_code=303)

    from ..totp import decode_pre2fa_token
    payload = decode_pre2fa_token(pre_token, expected_scope="otp_pending")
    if not payload:
        if is_ajax:
            return JSONResponse(status_code=401, content={"ok": False, "error": "सत्र समाप्त हो गया है। कृपया पुनः लॉगिन करें।"})
        return RedirectResponse(url="/auth/login", status_code=303)

    email = payload.get("email", "").strip().lower()
    purpose = payload.get("purpose", "login")
    client_ip = get_client_ip(request)

    with Session(db.engine) as session:
        pending_payload = None
        if purpose in ("signup", "email_change"):
            prev_row = session.exec(
                select(EmailOtp)
                .where(EmailOtp.email == email, EmailOtp.purpose == purpose)
                .order_by(EmailOtp.created_at.desc())
            ).first()
            if prev_row:
                pending_payload = prev_row.pending_payload

        from ..email_otp import create_and_send_email_otp
        ok, err_msg, otp_row, code = await create_and_send_email_otp(
            session, email, purpose=purpose, pending_payload=pending_payload, ip=client_ip
        )
        if not ok:
            status_code = 429 if ("सीमा" in (err_msg or "") or "सेकंड" in (err_msg or "")) else 400
            if is_ajax:
                return JSONResponse(status_code=status_code, content={"ok": False, "error": err_msg or "कोड भेजने में विफल"})
            return RedirectResponse(url=f"/auth/verify-otp?error={quote(err_msg or 'कोड भेजने में विफल')}", status_code=303)

    if is_ajax:
        return JSONResponse(content={"ok": True, "success": True, "cooldown": 60, "message": "सत्यापन कोड दोबारा भेज दिया गया है!"})
    return RedirectResponse(url="/auth/verify-otp", status_code=303)


@router.get("/auth/logout")
async def user_logout():
    response = RedirectResponse(url="/", status_code=303)
    clear_auth_cookie(response, "auth_token")
    clear_auth_cookie(response, "admin_token")
    return response


# ---------- USER PROFILE ----------
@router.get("/profile", response_class=HTMLResponse)
async def user_profile(request: Request):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    with Session(db.engine) as session:
        user = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        if not user:
            return RedirectResponse(url="/auth/login", status_code=303)
        comments = session.exec(
            select(Comment)
            .where(Comment.user_id == user.id)
            .order_by(Comment.created_at.desc())
            .limit(20)
        ).all()
        for c in comments:
            _ = c.article_rel
            
        comment_count = len(session.exec(select(Comment).where(Comment.user_id == user.id)).all())
        categories = list(session.exec(select(Category).order_by(Category.display_order)).all())
        return render(
            "auth/profile.html",
            user=user,
            categories=categories,
            active_nav="profile",
            comments=comments,
            comment_count=comment_count,
            request=request
        )


# ---------- USER ACCOUNT SETTINGS ----------
@router.get("/settings", response_class=HTMLResponse)
async def user_settings_page(request: Request):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    with Session(db.engine) as session:
        user = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        if not user:
            user = session.exec(select(Admin1).where(Admin1.email == user_info["email"])).first()
        if not user:
            return RedirectResponse(url="/auth/login", status_code=303)
        
        # Ensure username attribute exists for template compatibility
        if not hasattr(user, "username") or not user.username:
            user.username = "admin"
        
        updated = request.query_params.get("updated")
        password_changed = request.query_params.get("password_changed")
        error = request.query_params.get("error")
        
        categories = list(session.exec(select(Category).order_by(Category.display_order)).all())
        now = datetime.now(timezone.utc)
        kind = "admin" if user_info.get("user_type") == "admin" else "user"
        trusted_devices = session.exec(
            select(TrustedDevice).where(
                TrustedDevice.owner_kind == kind,
                TrustedDevice.owner_id == user.id,
                TrustedDevice.revoked_at == None,
                TrustedDevice.expires_at > now,
            ).order_by(TrustedDevice.created_at.desc())
        ).all()

        return render(
            "auth/settings.html",
            user=user,
            categories=categories,
            active_nav="settings",
            updated=updated,
            password_changed=password_changed,
            trusted_devices=trusted_devices,
            error=error,
            request=request
        )


@router.post("/settings/update")
async def user_settings_update(
    request: Request,
    name: str = Form(...),
    username: str = Form(""),
    email: Optional[str] = Form(None),
    bio: str = Form(""),
    avatar_url: Optional[str] = Form(None),
):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)

    clean_name = name.strip()
    clean_username = username.strip() if username else ""
    clean_email = email.strip().lower() if email else None

    valid_name, err_name = validate_english_name(clean_name)
    if not valid_name:
        return RedirectResponse(url=f"/settings?error={quote(err_name)}#profile", status_code=303)

    with Session(db.engine) as session:
        user = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        is_regular_user = True
        if not user:
            user = session.exec(select(Admin1).where(Admin1.email == user_info["email"])).first()
            is_regular_user = False
        if not user:
            return RedirectResponse(url="/auth/login", status_code=303)

        if is_regular_user:
            valid_u, err_u = validate_username(clean_username)
            if not valid_u:
                return RedirectResponse(url=f"/settings?error={quote(err_u)}#profile", status_code=303)

            if clean_username != user.username:
                existing = session.exec(select(User1).where(User1.username == clean_username, User1.id != user.id)).first()
                if existing:
                    return RedirectResponse(url=f"/settings?error={quote('Username already taken')}#profile", status_code=303)
            user.username = clean_username

        # नाम / bio / avatar तुरंत अपडेट होते हैं — इनमें OTP की ज़रूरत नहीं
        user.name = clean_name
        user.bio = bio.strip()
        if avatar_url is not None:
            user.avatar_url = avatar_url.strip() or None
        user.updated_at = datetime.now(timezone.utc)
        session.add(user)
        session.commit()
        session.refresh(user)

        email_change_requested = bool(clean_email and clean_email != user.email)

        # ---------- ईमेल बदलने के लिए अनिवार्य OTP सत्यापन (बग फिक्स) ----------
        if email_change_requested:
            if "@" not in clean_email or "." not in clean_email or len(clean_email) < 5:
                return RedirectResponse(
                    url=f"/settings?error={quote('अमान्य ईमेल प्रारूप (Invalid email format)')}#profile",
                    status_code=303,
                )

            owner_kind = "user" if is_regular_user else "admin"

            existing_user = session.exec(
                select(User1).where(
                    func.lower(User1.email) == clean_email,
                    User1.id != (user.id if is_regular_user else -1),
                )
            ).first()
            existing_admin = session.exec(
                select(Admin1).where(
                    func.lower(Admin1.email) == clean_email,
                    Admin1.id != (user.id if not is_regular_user else -1),
                )
            ).first()
            if existing_user or existing_admin:
                return RedirectResponse(
                    url=f"/settings?error={quote('यह ईमेल पता पहले से पंजीकृत है (Email already in use)')}#profile",
                    status_code=303,
                )

            import json
            pending_payload = json.dumps({"owner_kind": owner_kind, "owner_id": user.id})

            from ..email_otp import create_and_send_email_otp
            from ..totp import create_pre2fa_token
            client_ip = get_client_ip(request)
            ok, err_msg, otp_row, code = await create_and_send_email_otp(
                session, clean_email, purpose="email_change", pending_payload=pending_payload, ip=client_ip
            )
            if not ok:
                return RedirectResponse(
                    url=f"/settings?error={quote(err_msg or 'सत्यापन कोड भेजने में त्रुटि हुई')}#profile",
                    status_code=303,
                )

            pre_token = create_pre2fa_token(
                owner_kind,
                user.id,
                clean_email,
                scope="otp_pending",
                purpose="email_change",
                next_url="/settings?updated=1#profile",
            )
            response = RedirectResponse(url="/auth/verify-otp", status_code=303)
            set_auth_cookie(response, pre_token, cookie_name="pre_2fa_token", max_age=600)
            # नोट: पुराना auth_token/admin_token cookie यहाँ बिल्कुल नहीं छेड़ा जा रहा —
            # इसलिए OTP डिलीवर न होने की स्थिति में भी यूज़र लॉक आउट नहीं होगा।
            return response

        # ईमेल नहीं बदला गया — सिर्फ़ टोकन रिफ्रेश करें (नाम/username बदला हो सकता है)
        user_type = user_info.get("user_type", "user")
        tv = getattr(user, "token_version", 1) or 1
        token = create_access_token(
            data={"sub": user.email, "name": user.name, "user_type": user_type},
            expires_delta=timedelta(hours=24 * 7 if user_type == "user" else 24),
            token_version=tv,
        )
        response = RedirectResponse(url="/settings?updated=1#profile", status_code=303)
        if user_type == "admin":
            set_auth_cookie(response, token, cookie_name="admin_token", max_age=86400)
        set_auth_cookie(response, token, cookie_name="auth_token", max_age=604800)
        return response


@router.post("/settings/change-password")
async def user_change_password(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    current_password: Optional[str] = Form(None),
):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    with Session(db.engine) as session:
        user = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        if not user:
            user = session.exec(select(Admin1).where(Admin1.email == user_info["email"])).first()
        if not user:
            return RedirectResponse(url="/auth/login", status_code=303)
        
        has_existing_pwd = bool(user.hashed_password)
        
        # 1. Verify current password if user already has one
        if has_existing_pwd:
            if not current_password or not verify_password(current_password, user.hashed_password):
                return RedirectResponse(url=f"/settings?error={quote('वर्तमान पासवर्ड गलत है')}#security", status_code=303)
            if new_password == current_password:
                return RedirectResponse(url=f"/settings?error={quote('नया पासवर्ड वर्तमान पासवर्ड से भिन्न होना चाहिए')}#security", status_code=303)
        
        # 2. Check length (min 8, max 128 chars)
        if len(new_password) < 8:
            return RedirectResponse(url=f"/settings?error={quote('पासवर्ड कम से कम 8 वर्णों का होना चाहिए')}#security", status_code=303)
        if len(new_password) > 128:
            return RedirectResponse(url=f"/settings?error={quote('पासवर्ड अधिकतम 128 वर्णों का हो सकता है')}#security", status_code=303)
        
        # 3. Matching confirmation
        if new_password != confirm_password:
            return RedirectResponse(url=f"/settings?error={quote('नए पासवर्ड का मिलान नहीं हुआ')}#security", status_code=303)
        
        # 4. Password strength rules
        valid_pwd, err_pwd = validate_password_strength(new_password)
        if not valid_pwd:
            return RedirectResponse(url=f"/settings?error={quote(err_pwd)}#security", status_code=303)
        
        # Save new password & update provider
        user.hashed_password = get_password_hash(new_password)
        if hasattr(user, "auth_provider") and user.auth_provider == "google":
            user.auth_provider = "both"
        user.updated_at = datetime.now(timezone.utc)
        session.add(user)
        
        # Changing password must invalidate existing tokens and delete that user's trusted devices
        from ..auth import bump_token_version
        bump_token_version(session, user)
        session.commit()
        session.refresh(user)
        
        # Preserve user auth session after password update with new token_version
        user_type = user_info.get("user_type", "user")
        tv = getattr(user, "token_version", 1) or 1
        token = create_access_token(
            data={"sub": user.email, "name": user.name, "user_type": user_type},
            expires_delta=timedelta(hours=24 * 7 if user_type == "user" else 24),
            token_version=tv,
        )
        response = RedirectResponse(url="/settings?password_changed=1#security", status_code=303)
        if user_type == "admin":
            set_auth_cookie(response, token, cookie_name="admin_token", max_age=86400)
        set_auth_cookie(response, token, cookie_name="auth_token", max_age=604800)
        return response


@router.post("/settings/trusted-device/{device_id}/revoke")
async def revoke_user_trusted_device(request: Request, device_id: int):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    with Session(db.engine) as session:
        user = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        if not user:
            user = session.exec(select(Admin1).where(Admin1.email == user_info["email"])).first()
        if user:
            kind = "admin" if user_info.get("user_type") == "admin" else "user"
            device = session.exec(
                select(TrustedDevice).where(
                    TrustedDevice.id == device_id,
                    TrustedDevice.owner_kind == kind,
                    TrustedDevice.owner_id == user.id,
                )
            ).first()
            if device:
                device.revoked_at = datetime.now(timezone.utc)
                session.add(device)
                session.commit()
    
    return RedirectResponse(url="/settings#security", status_code=303)


@router.post("/settings/avatar/remove")
async def user_settings_remove_avatar(request: Request):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)
    from ..storage import delete_avatar
    with Session(db.engine) as session:
        user = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        if not user:
            user = session.exec(select(Admin1).where(Admin1.email == user_info["email"])).first()
        if user and user.avatar_url:
            delete_avatar(user.avatar_url)
            user.avatar_url = None
            user.updated_at = datetime.now(timezone.utc)
            session.add(user)
            session.commit()
    return RedirectResponse(url="/settings?updated=1#profile", status_code=303)


@router.post("/settings/delete-account")
async def user_delete_account(
    request: Request,
    password: Optional[str] = Form(None),
    confirm_delete: Optional[str] = Form(None),
):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    with Session(db.engine) as session:
        user = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        if not user:
            return RedirectResponse(url="/auth/login", status_code=303)
        
        if user.hashed_password:
            if not password or not verify_password(password, user.hashed_password):
                return RedirectResponse(url=f"/settings?error={quote('पासवर्ड गलत है, खाता नहीं हटाया गया')}#security", status_code=303)
        else:
            # Google-only user without password
            val = (confirm_delete or "").strip().upper()
            if not val or val not in ("DELETE", "HATAEN", "HATAEIN", "हटाएं", "हटाएँ", "YES", "CONFIRM"):
                return RedirectResponse(url=f"/settings?error={quote('खाता हटाने की पुष्टि के लिए DELETE लिखें')}#security", status_code=303)
        
        # Completely remove user and all associated records
        delete_user_completely(session, user)
    
    response = RedirectResponse(url="/?account_deleted=1", status_code=303)
    clear_auth_cookie(response, "auth_token")
    clear_auth_cookie(response, "admin_token")
    clear_auth_cookie(response, "trusted_device")
    clear_auth_cookie(response, "pre_2fa_token")
    return response


@router.get("/settings/export-data")
async def user_export_data(request: Request):
    user_info = await require_user(request)
    if not user_info:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    with Session(db.engine) as session:
        user = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        comments = session.exec(select(Comment).where(Comment.user_id == user.id)).all()
        data = {
            "profile": {
                "id": user.id,
                "name": user.name,
                "username": user.username,
                "email": user.email,
                "bio": user.bio,
                "avatar_url": user.avatar_url,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
            "comments_count": len(comments),
            "comments": [
                {
                    "id": c.id,
                    "article_id": c.article_id,
                    "content": c.content,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                }
                for c in comments
            ]
        }
        return JSONResponse(
            content=data,
            headers={"Content-Disposition": f"attachment; filename=user_data_{user.username}.json"}
        )


# ---------- COMMENTS ----------
_user_comment_timestamps: dict[str, list[float]] = {}

@router.post("/comment/{article_id}")
async def post_comment(
    request: Request,
    article_id: int,
    content: str = Form(""),
    website: Optional[str] = Form(None),
):
    import time
    user_info = await require_user(request)
    
    with Session(db.engine) as session:
        article = session.get(Article, article_id)
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        article_slug = article.slug
        
        if not user_info:
            return RedirectResponse(url=f"/auth/login?next=/article/{quote(article_slug)}%23comments", status_code=303)
        
        # Bot honeypot check
        if website:
            return RedirectResponse(url=f"/article/{article_slug}#comments", status_code=303)
            
        clean_content = content.strip()
        if len(clean_content) < 2:
            return RedirectResponse(url=f"/article/{article_slug}?comment_error={quote('टिप्पणी खाली नहीं हो सकती।')}#comments", status_code=303)
        if len(clean_content) > 2000:
            return RedirectResponse(url=f"/article/{article_slug}?comment_error={quote('टिप्पणी 2000 अक्षरों से अधिक नहीं हो सकती।')}#comments", status_code=303)
            
        # Basic rate limiting: max 5 comments per minute per user
        rate_key = user_info.get("email") or (request.client.host if request.client else "unknown")
        now_ts = time.time()
        timestamps = [t for t in _user_comment_timestamps.get(rate_key, []) if now_ts - t < 60]
        if len(timestamps) >= 5:
            return RedirectResponse(url=f"/article/{article_slug}?comment_error={quote('कृपया थोड़ा रुकें, आप बहुत तेज़ी से टिप्पणियाँ पोस्ट कर रहे हैं।')}#comments", status_code=303)
        timestamps.append(now_ts)
        _user_comment_timestamps[rate_key] = timestamps
        
        # Resolve or auto-sync User1 for authoring comments
        user = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        if not user:
            if user_info.get("user_type") == "admin":
                admin_rec = session.exec(select(Admin1).where(Admin1.email == user_info["email"])).first()
                clean_uname = (admin_rec.name.lower().replace(" ", "_") if admin_rec else "admin")[:20]
                cand = clean_uname
                cnt = 1
                while session.exec(select(User1).where(User1.username == cand)).first():
                    cand = f"{clean_uname}_{cnt}"[:28]
                    cnt += 1
                user = User1(
                    name=admin_rec.name if admin_rec else user_info.get("name", "Admin"),
                    username=cand,
                    email=user_info["email"],
                    hashed_password=admin_rec.hashed_password if admin_rec else "x",
                    bio=admin_rec.bio if admin_rec else "व्यवस्थापक (Admin)",
                    avatar_url=admin_rec.avatar_url if admin_rec else None,
                    is_active=True
                )
                session.add(user)
                session.flush()
            else:
                return RedirectResponse(url=f"/auth/login?next=/article/{quote(article_slug)}%23comments", status_code=303)
                
        if user.is_banned:
            return RedirectResponse(url=f"/article/{article_slug}?comment_error={quote('आपका खाता निलंबित है।')}#comments", status_code=303)
            
        comment = Comment(
            article_id=article_id,
            user_id=user.id,
            content=clean_content,
            is_approved=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(comment)
        session.commit()
        session.refresh(comment)
        
        create_notification(
            session,
            "New Comment Posted",
            f"{user.display_name} commented on: {article.title}",
            notification_type="info",
            link="/admin/comments"
        )
        
        return RedirectResponse(url=f"/article/{article.slug}?comment_success=1#comment-{comment.id}", status_code=303)


@router.get("/comment/{comment_id}")
async def get_comment_redirect(comment_id: int):
    with Session(db.engine) as session:
        comment = session.get(Comment, comment_id)
        if comment:
            article = session.get(Article, comment.article_id)
            if article:
                return RedirectResponse(url=f"/article/{article.slug}#comment-{comment.id}", status_code=302)
    return RedirectResponse(url="/", status_code=302)


@router.post("/comment/{comment_id}/delete")
async def delete_comment(request: Request, comment_id: int):
    user_info = await require_user(request)
    if not user_info:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    with Session(db.engine) as session:
        comment = session.get(Comment, comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        
        user = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        
        is_owner = user and (comment.user_id == user.id)
        is_admin = user_info.get("user_type") == "admin"
        if not is_owner and not is_admin:
            raise HTTPException(status_code=403, detail="Not authorized")
        
        article = session.get(Article, comment.article_id)
        redirect_target = article.slug if article else str(comment.article_id)
        
        session.delete(comment)
        session.commit()
    
    return RedirectResponse(url=f"/article/{redirect_target}?comment_deleted=1#comments", status_code=303)


# ---------- BOOKMARK / SAVED GUIDES ----------
@router.post("/article/{slug}/bookmark")
async def toggle_bookmark(request: Request, slug: str):
    user_payload = await get_current_user(request)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")
    
    if not user_payload:
        login_url = f"/auth/login?next=/article/{quote(slug)}"
        if is_ajax:
            return JSONResponse(status_code=401, content={"error": "unauthorized", "login_url": login_url})
        return RedirectResponse(url=login_url, status_code=303)
    
    with Session(db.engine) as session:
        user = session.exec(select(User1).where(User1.email == user_payload["email"])).first()
        if not user:
            login_url = f"/auth/login?next=/article/{quote(slug)}"
            if is_ajax:
                return JSONResponse(status_code=401, content={"error": "unauthorized", "login_url": login_url})
            return RedirectResponse(url=login_url, status_code=303)
            
        article = session.exec(select(Article).where(Article.slug == slug)).first()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        
        existing = session.exec(
            select(Bookmark).where(Bookmark.user_id == user.id, Bookmark.article_id == article.id)
        ).first()
        
        if existing:
            session.delete(existing)
            session.commit()
            bookmarked = False
        else:
            new_bm = Bookmark(user_id=user.id, article_id=article.id)
            session.add(new_bm)
            session.commit()
            bookmarked = True
        
        if is_ajax:
            return JSONResponse({
                "success": True,
                "bookmarked": bookmarked,
                "article_slug": slug,
                "message": "गाइड सेव कर लिया गया है!" if bookmarked else "गाइड सूची से हटा दिया गया है!"
            })
        return RedirectResponse(url=f"/article/{slug}", status_code=303)


@router.get("/saved", response_class=HTMLResponse)
async def saved_articles_page(request: Request):
    user_payload = await get_current_user(request)
    if not user_payload:
        return RedirectResponse(url="/auth/login?next=/saved", status_code=303)
    
    with Session(db.engine) as session:
        user = session.exec(select(User1).where(User1.email == user_payload["email"])).first()
        if not user:
            return RedirectResponse(url="/auth/login?next=/saved", status_code=303)
        
        bms = session.exec(
            select(Bookmark)
            .where(Bookmark.user_id == user.id)
            .order_by(Bookmark.created_at.desc())
        ).all()
        
        article_ids = [b.article_id for b in bms]
        articles = []
        if article_ids:
            art_map = {
                a.id: a for a in session.exec(
                    select(Article)
                    .where(Article.id.in_(article_ids))
                    .options(selectinload(Article.category_rel), selectinload(Article.availabilities))
                ).all()
            }
            for aid in article_ids:
                if aid in art_map:
                    articles.append(art_map[aid])
        
        categories = list(session.exec(select(Category).order_by(Category.display_order)).all())
        
        return render(
            "saved.html",
            articles=articles,
            categories=categories,
            user=user,
            active_nav="saved",
            request=request,
        )


# ---------- NOTIFICATIONS API ----------
@router.get("/api/notifications")
async def get_user_notifications(request: Request):
    user_payload = await get_current_user(request)
    if not user_payload:
        return JSONResponse({"unread_count": 0, "notifications": []})
    
    with Session(db.engine) as session:
        is_admin = user_payload.get("user_type") == "admin"
        if is_admin:
            admin = session.exec(select(Admin1).where(Admin1.email == user_payload["email"])).first()
            if not admin:
                return JSONResponse({"unread_count": 0, "notifications": []})
            notifs = session.exec(
                select(Notification)
                .where(Notification.admin_id == admin.id)
                .order_by(Notification.created_at.desc())
                .limit(15)
            ).all()
        else:
            user = session.exec(select(User1).where(User1.email == user_payload["email"])).first()
            if not user:
                return JSONResponse({"unread_count": 0, "notifications": []})
            notifs = session.exec(
                select(Notification)
                .where(Notification.user_id == user.id)
                .order_by(Notification.created_at.desc())
                .limit(15)
            ).all()
        
        unread_count = sum(1 for n in notifs if not n.is_read)
        return JSONResponse({
            "unread_count": unread_count,
            "notifications": [
                {
                    "id": n.id,
                    "title": n.title,
                    "message": n.message,
                    "link": n.link or "",
                    "is_read": n.is_read,
                    "notification_type": n.notification_type,
                    "created_at": n.created_at.strftime("%d %b, %H:%M") if n.created_at else ""
                }
                for n in notifs
            ]
        })


@router.post("/api/notifications/mark-all-read")
async def mark_notifications_read(request: Request):
    user_payload = await get_current_user(request)
    if not user_payload:
        return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)
    
    with Session(db.engine) as session:
        is_admin = user_payload.get("user_type") == "admin"
        if is_admin:
            admin = session.exec(select(Admin1).where(Admin1.email == user_payload["email"])).first()
            if admin:
                session.exec(
                    sqlmodel.update(Notification)
                    .where(Notification.admin_id == admin.id, Notification.is_read == False)
                    .values(is_read=True)
                )
                session.commit()
        else:
            user = session.exec(select(User1).where(User1.email == user_payload["email"])).first()
            if user:
                session.exec(
                    sqlmodel.update(Notification)
                    .where(Notification.user_id == user.id, Notification.is_read == False)
                    .values(is_read=True)
                )
                session.commit()
        return JSONResponse({"success": True})