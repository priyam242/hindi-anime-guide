"""Admin panel routes — full CRUD, user management, comment moderation, account settings, activity logs, notifications."""
from __future__ import annotations
import re
from datetime import timedelta, datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Form, HTTPException, Request, status, Depends, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select
import sqlmodel
from sqlalchemy.orm import selectinload

from ..auth import (
    create_access_token, get_password_hash, require_admin,
    verify_password, set_auth_cookie, clear_auth_cookie,
    delete_user_completely, get_current_user,
)
from .. import db
from ..db import get_session
from ..models import Admin1, User1, Comment, Article, Availability, Category, Platform, Notification, ActivityLog, ArticleCategoryLink
from ..templates_config import render
from ..config import get_settings
from ..storage import save_cover, delete_cover
from ..utils import (
    validate_english_name, validate_password_strength,
    get_password_strength, get_avatar_color, get_initials,
)

settings = get_settings()

router = APIRouter(prefix="/admin", tags=["admin"])


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def log_activity(
    session: Session,
    admin_id: Optional[int],
    action: str,
    target_type: str = "",
    target_id: Optional[int] = None,
    details: str = "",
    ip_address: str = ""
):
    try:
        log = ActivityLog(
            admin_id=admin_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            ip_address=ip_address,
            created_at=datetime.now(timezone.utc)
        )
        session.add(log)
        session.commit()
    except Exception as e:
        print(f"Error logging activity: {e}")


def create_notification(
    session: Session,
    title: str,
    message: str,
    notification_type: str = "info",
    link: str = "",
    admin_id: Optional[int] = None,
):
    try:
        if admin_id:
            admin_ids = [admin_id]
        else:
            admin_ids = [a.id for a in session.exec(select(Admin1)).all() if a.id is not None]
        for aid in admin_ids:
            notif = Notification(
                admin_id=aid,
                title=title,
                message=message,
                notification_type=notification_type,
                link=link,
                is_read=False,
                created_at=datetime.now(timezone.utc)
            )
            session.add(notif)
        session.commit()
    except Exception as e:
        print(f"Error creating notification: {e}")


def create_user_notification(
    session: Session,
    user_id: int,
    title: str,
    message: str,
    notification_type: str = "success",
    link: str = "",
) -> None:
    try:
        notif = Notification(
            admin_id=None,
            user_id=user_id,
            title=title,
            message=message,
            notification_type=notification_type,
            link=link,
            is_read=False,
            created_at=datetime.now(timezone.utc)
        )
        session.add(notif)
        session.commit()
    except Exception as e:
        print(f"Error creating user notification: {e}")



def get_admin_context(session: Session, admin_payload: dict) -> dict:
    admin_user = session.exec(select(Admin1).where(Admin1.email == admin_payload["email"])).first()
    unread_count = 0
    if admin_user and admin_user.id:
        unread_count = len(session.exec(
            select(Notification).where(Notification.admin_id == admin_user.id, Notification.is_read == False)
        ).all())
    return {
        "admin_user": admin_user,
        "unread_count": unread_count,
        "user_email": admin_payload["email"],
        "user_name": admin_user.name if admin_user else admin_payload.get("name", "Admin")
    }


# ---------- AUTH ----------
@router.get("/login")
async def admin_login_page(request: Request, registered: str = ""):
    user = await get_current_user(request)
    if user and user.get("user_type") == "admin":
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    target = "/auth/login"
    if registered:
        target += f"?registered={registered}"
    return RedirectResponse(url=target, status_code=303)


@router.post("/login", response_class=HTMLResponse)
async def admin_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session)
):
    user = session.exec(select(Admin1).where(Admin1.email == email)).first()
    if not user or not verify_password(password, user.hashed_password):
        return HTMLResponse(status_code=400, content="Invalid email or password / अमान्य ईमेल या पासवर्ड")
    if not user.is_active:
        return HTMLResponse(status_code=400, content="Account deactivated")
    
    # Admin simple login — directly issue token and navigate to /admin/dashboard
    tv = getattr(user, "token_version", 1) or 1
    token = create_access_token(
        data={"sub": user.email, "name": user.name, "user_type": "admin"},
        expires_delta=timedelta(hours=24), token_version=tv,
    )
    log_activity(session, user.id, "admin_login", target_type="admin",
                 target_id=user.id, details="Admin logged in (email + password)",
                 ip_address=get_client_ip(request))
    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    set_auth_cookie(response, token, cookie_name="admin_token", max_age=86400)
    set_auth_cookie(response, token, cookie_name="auth_token", max_age=86400)
    return response


@router.get("/logout")
async def admin_logout(request: Request, session: Session = Depends(get_session)):
    admin_cookie = request.cookies.get("admin_token")
    if admin_cookie:
        from ..auth import decode_token
        payload = decode_token(admin_cookie)
        if payload and payload.email:
            admin_user = session.exec(select(Admin1).where(Admin1.email == payload.email)).first()
            if admin_user:
                log_activity(session, admin_user.id, "admin_logout", target_type="admin", target_id=admin_user.id, details="Admin logged out", ip_address=get_client_ip(request))
    
    response = RedirectResponse(url="/auth/login", status_code=303)
    response.delete_cookie("admin_token")
    response.delete_cookie("auth_token")
    response.delete_cookie("pre_2fa_token")
    response.delete_cookie("trusted_device")
    response.delete_cookie("site_lang")
    return response


@router.get("/register", response_class=HTMLResponse)
async def admin_register_page(request: Request):
    return render("admin/register.html", error=None, request=request)


@router.post("/register", response_class=HTMLResponse)
async def admin_register(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    session: Session = Depends(get_session)
):
    valid_name, err = validate_english_name(name)
    if not valid_name:
        return HTMLResponse(status_code=400, content=render("admin/register.html", error=err, request=request))
    
    if password != confirm_password:
        return HTMLResponse(status_code=400, content=render("admin/register.html", error="Passwords do not match", request=request))
    
    if len(password) < 8:
        return HTMLResponse(status_code=400, content=render("admin/register.html", error="Password must be at least 8 characters", request=request))
    
    if session.exec(select(Admin1).where(Admin1.email == email.strip().lower())).first():
        return HTMLResponse(status_code=400, content=render("admin/register.html", error="Email already registered", request=request))
    
    new_admin = Admin1(
        name=name.strip(),
        email=email.strip().lower(),
        hashed_password=get_password_hash(password),
        is_active=True
    )
    session.add(new_admin)
    session.commit()
    
    log_activity(session, new_admin.id, "admin_registered", target_type="admin", target_id=new_admin.id, details=f"New admin registered: {email}", ip_address=get_client_ip(request))
    create_notification(session, "New Admin Registered", f"Admin {name} ({email}) was registered.", notification_type="info", link="/admin/activity")
    
    return RedirectResponse(url="/auth/login?registered=1", status_code=303)


# ---------- DASHBOARD ----------
@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    article_count = len(session.exec(select(Article)).all())
    category_count = len(session.exec(select(Category)).all())
    platform_count = len(session.exec(select(Platform)).all())
    user_count = len(session.exec(select(User1)).all())
    comment_count = len(session.exec(select(Comment)).all())
    pending_comments = len(session.exec(select(Comment).where(Comment.is_approved == False)).all())
    
    # Recent activity logs
    recent_activities = session.exec(
        select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(8)
    ).all()
    
    # Unread notifications
    admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
    unread_notifications = []
    if admin_id:
        unread_notifications = session.exec(
            select(Notification).where(Notification.admin_id == admin_id, Notification.is_read == False).order_by(Notification.created_at.desc()).limit(5)
        ).all()
    
    return render(
        "admin/dashboard.html",
        **ctx,
        article_count=article_count,
        category_count=category_count,
        platform_count=platform_count,
        user_count=user_count,
        comment_count=comment_count,
        pending_comments=pending_comments,
        recent_activities=recent_activities,
        unread_notifications=unread_notifications,
        request=request,
    )


# ---------- ACCOUNT SETTINGS ----------
@router.get("/account", response_class=HTMLResponse)
async def admin_account_page(request: Request, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    updated = request.query_params.get("updated")
    password_changed = request.query_params.get("password_changed")
    admin_created = request.query_params.get("admin_created")
    status_toggled = request.query_params.get("status_toggled")
    error = request.query_params.get("error")
    
    all_admins = list(session.exec(select(Admin1).order_by(Admin1.created_at.asc())).all())
    
    return render(
        "admin/account.html",
        **ctx,
        all_admins=all_admins,
        updated=updated,
        password_changed=password_changed,
        admin_created=admin_created,
        status_toggled=status_toggled,
        error=error,
        request=request
    )


@router.post("/account/update")
async def admin_account_update(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    bio: str = Form(""),
    avatar_url: Optional[str] = Form(None),
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    valid_name, err = validate_english_name(name)
    if not valid_name:
        return RedirectResponse(url=f"/admin/account?error={err}#profile", status_code=303)
    
    clean_email = email.strip().lower()
    if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", clean_email):
        return RedirectResponse(url="/admin/account?error=Invalid email address format#profile", status_code=303)
    
    admin_user = session.exec(select(Admin1).where(Admin1.email == admin["email"])).first()
    if not admin_user:
        raise HTTPException(status_code=404, detail="Admin not found")
    
    if clean_email != admin_user.email:
        conflict = session.exec(select(Admin1).where(Admin1.email == clean_email, Admin1.id != admin_user.id)).first()
        if conflict:
            return RedirectResponse(url="/admin/account?error=Email address is already in use by another admin#profile", status_code=303)
    
    old_email = admin_user.email
    admin_user.name = name.strip()
    admin_user.email = clean_email
    admin_user.bio = bio.strip()
    if avatar_url:
        admin_user.avatar_url = avatar_url.strip()
    admin_user.updated_at = datetime.now(timezone.utc)
    session.add(admin_user)
    session.commit()
    
    log_activity(session, admin_user.id, "admin_profile_updated", target_type="admin", target_id=admin_user.id, details=f"Profile updated for {admin_user.email} (was {old_email})", ip_address=get_client_ip(request))
    
    # Re-issue admin_token cookie with updated email so admin is not logged out
    new_token = create_access_token({
        "sub": admin_user.email,
        "name": admin_user.name,
        "user_type": "admin"
    })
    response = RedirectResponse(url="/admin/account?updated=1#profile", status_code=303)
    response.set_cookie(
        key="admin_token",
        value=new_token,
        httponly=True,
        max_age=3600 * 24 * 7,
        samesite="lax",
        secure=False,
    )
    return response


@router.post("/create-admin")
async def admin_create_admin(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    bio: str = Form(""),
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    valid_name, err = validate_english_name(name)
    if not valid_name:
        return RedirectResponse(url=f"/admin/account?error={err}#admins", status_code=303)
    
    clean_email = email.strip().lower()
    if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", clean_email):
        return RedirectResponse(url="/admin/account?error=Invalid email address format#admins", status_code=303)
    
    if session.exec(select(Admin1).where(Admin1.email == clean_email)).first():
        return RedirectResponse(url="/admin/account?error=Administrator with this email already exists#admins", status_code=303)
    
    valid_pwd, err = validate_password_strength(password)
    if not valid_pwd:
        return RedirectResponse(url=f"/admin/account?error={err}#admins", status_code=303)
    
    new_admin = Admin1(
        name=name.strip(),
        email=clean_email,
        hashed_password=get_password_hash(password),
        bio=bio.strip(),
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    session.add(new_admin)
    session.commit()
    
    admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
    log_activity(session, admin_id, "admin_appointed", target_type="admin", target_id=new_admin.id, details=f"Appointed new administrator: {new_admin.name} ({new_admin.email})", ip_address=get_client_ip(request))
    create_notification(session, "New Administrator Appointed", f"Admin {new_admin.name} ({new_admin.email}) was appointed.", notification_type="success", link="/admin/account#admins")
    
    return RedirectResponse(url="/admin/account?admin_created=1#admins", status_code=303)


@router.post("/admins/{target_admin_id}/toggle-status")
async def admin_toggle_status(
    request: Request,
    target_admin_id: int,
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    admin_user = ctx["admin_user"]
    
    if admin_user and admin_user.id == target_admin_id:
        return RedirectResponse(url="/admin/account?error=Cannot deactivate your own administrator account#admins", status_code=303)
    
    target = session.get(Admin1, target_admin_id)
    if not target:
        raise HTTPException(status_code=404, detail="Admin not found")
    
    target.is_active = not target.is_active
    target.updated_at = datetime.now(timezone.utc)
    session.add(target)
    session.commit()
    
    status_str = "activated" if target.is_active else "deactivated"
    log_activity(session, admin_user.id if admin_user else None, f"admin_{status_str}", target_type="admin", target_id=target.id, details=f"Admin {target.email} was {status_str}", ip_address=get_client_ip(request))
    
    return RedirectResponse(url="/admin/account?status_toggled=1#admins", status_code=303)


@router.post("/account/change-password")
async def admin_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    admin_user = session.exec(select(Admin1).where(Admin1.email == admin["email"])).first()
    if not admin_user:
        raise HTTPException(status_code=404, detail="Admin not found")
    
    if not verify_password(current_password, admin_user.hashed_password):
        return RedirectResponse(url="/admin/account?error=Current password is incorrect#security", status_code=303)
    
    if new_password != confirm_password:
        return RedirectResponse(url="/admin/account?error=New passwords do not match#security", status_code=303)
    
    valid_pwd, err = validate_password_strength(new_password)
    if not valid_pwd:
        return RedirectResponse(url=f"/admin/account?error={err}#security", status_code=303)
    
    admin_user.hashed_password = get_password_hash(new_password)
    admin_user.updated_at = datetime.now(timezone.utc)
    session.add(admin_user)
    session.commit()
    
    log_activity(session, admin_user.id, "admin_password_changed", target_type="admin", target_id=admin_user.id, details="Password changed successfully", ip_address=get_client_ip(request))
    return RedirectResponse(url="/admin/account?password_changed=1#security", status_code=303)


# ---------- ACTIVITY LOGS ----------
@router.get("/activity", response_class=HTMLResponse)
async def admin_activity_log(
    request: Request,
    action: Optional[str] = None,
    search: Optional[str] = None,
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    query = select(ActivityLog).order_by(ActivityLog.created_at.desc())
    if action:
        query = query.where(ActivityLog.action == action)
    
    logs = session.exec(query.limit(150)).all()
    if search:
        s_lower = search.lower()
        logs = [l for l in logs if s_lower in l.action.lower() or s_lower in (l.details or "").lower() or s_lower in (l.ip_address or "").lower()]
    
    return render(
        "admin/activity.html",
        **ctx,
        logs=logs,
        action_filter=action or "",
        search_filter=search or "",
        request=request
    )


# ---------- NOTIFICATIONS ----------
@router.get("/notifications", response_class=HTMLResponse)
async def admin_notifications_page(request: Request, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    notifications = []
    if ctx["admin_user"]:
        notifications = session.exec(
            select(Notification).where(Notification.admin_id == ctx["admin_user"].id).order_by(Notification.created_at.desc())
        ).all()
    
    return render(
        "admin/notifications.html",
        **ctx,
        notifications=notifications,
        request=request
    )


@router.post("/notifications/{notif_id}/read")
async def mark_notification_read(request: Request, notif_id: int, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    notif = session.get(Notification, notif_id)
    if notif:
        notif.is_read = True
        session.add(notif)
        session.commit()
    
    redirect_url = request.headers.get("referer", "/admin/notifications")
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/notifications/read-all")
async def mark_all_notifications_read(request: Request, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    if ctx["admin_user"]:
        unreads = session.exec(
            select(Notification).where(Notification.admin_id == ctx["admin_user"].id, Notification.is_read == False)
        ).all()
        for u in unreads:
            u.is_read = True
            session.add(u)
        session.commit()
    
    redirect_url = request.headers.get("referer", "/admin/notifications")
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/notifications/clear-all")
async def clear_all_notifications(request: Request, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    if ctx["admin_user"]:
        session.exec(sqlmodel.delete(Notification).where(Notification.admin_id == ctx["admin_user"].id))
        session.commit()
    return RedirectResponse(url="/admin/notifications", status_code=303)


# ---------- ARTICLES CRUD ----------
@router.get("/articles", response_class=HTMLResponse)
async def admin_articles_list(request: Request, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    articles = session.exec(
        select(Article)
        .options(
            selectinload(Article.category_rel),
            selectinload(Article.categories),
            selectinload(Article.availabilities).selectinload(Availability.platform_rel)
        )
        .order_by(Article.created_at.desc())
    ).all()
    categories = session.exec(select(Category).order_by(Category.display_order)).all()
    
    return render(
        "admin/articles_list.html",
        **ctx,
        articles=articles,
        categories=categories,
        request=request,
    )


@router.get("/articles/create", response_class=HTMLResponse)
async def admin_article_create_page(request: Request, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    categories = list(session.exec(select(Category).order_by(Category.display_order)).all())
    platforms = list(session.exec(select(Platform).order_by(Platform.display_order)).all())
    return render(
        "admin/article_form.html",
        **ctx,
        article=None,
        categories=categories,
        platforms=platforms,
        selected_category_names=["where-to-watch"],
        selected_platforms=[],
        request=request,
    )


@router.post("/articles/create")
async def admin_article_create(
    request: Request,
    slug: str = Form(...),
    title: str = Form(...),
    meta_description: str = Form(""),
    content_md: str = Form(...),
    category: Optional[str] = Form(None),
    category_id: Optional[str] = Form(None),
    category_names: List[str] = Form(default=[]),
    platform_ids: List[str] = Form(default=[]),
    cover_file: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    chosen_cats: List[str] = []
    if category_names:
        chosen_cats = [c.strip() for c in category_names if c.strip()]
    elif category:
        chosen_cats = [c.strip() for c in category.split(",") if c.strip()]
    elif category_id:
        chosen_cats = [category_id.strip()]

    if not chosen_cats:
        chosen_cats = ["where-to-watch"]

    cat_val = chosen_cats[0]
    now = datetime.now(timezone.utc)
    
    clean_slug = slug.strip()
    cover_url = None
    if cover_file and cover_file.filename:
        cover_bytes = await cover_file.read()
        if cover_bytes:
            cover_url = save_cover(cover_bytes, clean_slug, cover_file.filename)

    article = Article(
        slug=clean_slug,
        title=title.strip(),
        meta_description=meta_description.strip(),
        content_md=content_md,
        cover_image=cover_url,
        published_at=now,
        platform_links_count=len(platform_ids),
        category=cat_val,
        created_at=now,
        updated_at=now,
        last_checked=now
    )
    session.add(article)
    session.flush()

    for c_name in chosen_cats:
        session.add(ArticleCategoryLink(article_id=article.id, category_name=c_name))

    for pid in platform_ids:
        session.add(Availability(article_id=article.id, platform_id=pid, languages="Hindi"))
    session.commit()
    
    admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
    log_activity(session, admin_id, "article_created", target_type="article", target_id=article.id, details=f"Created article: {article.title}", ip_address=get_client_ip(request))
    return RedirectResponse(url="/admin/articles", status_code=303)


@router.get("/articles/{article_id}/edit", response_class=HTMLResponse)
async def admin_article_edit_page(request: Request, article_id: int, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    categories = list(session.exec(select(Category).order_by(Category.display_order)).all())
    platforms = list(session.exec(select(Platform).order_by(Platform.display_order)).all())
    avails = session.exec(select(Availability).where(Availability.article_id == article_id)).all()
    cat_links = session.exec(select(ArticleCategoryLink).where(ArticleCategoryLink.article_id == article_id)).all()
    selected_category_names = [l.category_name for l in cat_links]
    if not selected_category_names and article.category:
        selected_category_names = [article.category]

    return render(
        "admin/article_form.html",
        **ctx,
        article=article,
        categories=categories,
        platforms=platforms,
        selected_category_names=selected_category_names,
        selected_platforms=[a.platform_id for a in avails],
        request=request,
    )


@router.post("/articles/{article_id}/edit")
async def admin_article_edit(
    request: Request,
    article_id: int,
    slug: str = Form(...),
    title: str = Form(...),
    meta_description: str = Form(""),
    content_md: str = Form(...),
    category: Optional[str] = Form(None),
    category_id: Optional[str] = Form(None),
    category_names: List[str] = Form(default=[]),
    platform_ids: List[str] = Form(default=[]),
    cover_file: Optional[UploadFile] = File(None),
    remove_cover: Optional[str] = Form(None),
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404)
    
    chosen_cats: List[str] = []
    if category_names:
        chosen_cats = [c.strip() for c in category_names if c.strip()]
    elif category:
        chosen_cats = [c.strip() for c in category.split(",") if c.strip()]
    elif category_id:
        chosen_cats = [category_id.strip()]

    if not chosen_cats:
        chosen_cats = [article.category or "where-to-watch"]

    cat_val = chosen_cats[0]
    
    clean_slug = slug.strip()
    article.slug = clean_slug
    article.title = title.strip()
    article.meta_description = meta_description.strip()
    article.content_md = content_md
    article.category = cat_val
    article.platform_links_count = len(platform_ids)
    article.updated_at = datetime.now(timezone.utc)
    
    if remove_cover in ("1", "true", "on"):
        if article.cover_image:
            delete_cover(article.cover_image)
            article.cover_image = None
    elif cover_file and cover_file.filename:
        cover_bytes = await cover_file.read()
        if cover_bytes:
            new_cover_url = save_cover(cover_bytes, clean_slug, cover_file.filename, old_cover_url=article.cover_image)
            article.cover_image = new_cover_url

    session.exec(sqlmodel.delete(ArticleCategoryLink).where(ArticleCategoryLink.article_id == article_id))
    for c_name in chosen_cats:
        session.add(ArticleCategoryLink(article_id=article_id, category_name=c_name))

    session.exec(sqlmodel.delete(Availability).where(Availability.article_id == article_id))
    for pid in platform_ids:
        session.add(Availability(article_id=article_id, platform_id=pid, languages="Hindi"))
    session.add(article)
    session.commit()
    
    admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
    log_activity(session, admin_id, "article_edited", target_type="article", target_id=article_id, details=f"Edited article: {article.title}", ip_address=get_client_ip(request))
    return RedirectResponse(url="/admin/articles", status_code=303)


@router.post("/articles/{article_id}/delete")
async def admin_article_delete(request: Request, article_id: int, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    article = session.get(Article, article_id)
    if article:
        title = article.title
        session.delete(article)
        session.commit()
        admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
        log_activity(session, admin_id, "article_deleted", target_type="article", target_id=article_id, details=f"Deleted article: {title}", ip_address=get_client_ip(request))
    return RedirectResponse(url="/admin/articles", status_code=303)


@router.post("/articles/bulk-delete")
async def admin_articles_bulk_delete(
    request: Request,
    article_ids: List[int] = Form(...),
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    count = 0
    for aid in article_ids:
        art = session.get(Article, aid)
        if art:
            session.delete(art)
            count += 1
    session.commit()
    
    admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
    log_activity(session, admin_id, "articles_bulk_deleted", target_type="article", details=f"Bulk deleted {count} articles", ip_address=get_client_ip(request))
    return RedirectResponse(url="/admin/articles", status_code=303)


# ---------- CATEGORIES CRUD ----------
@router.get("/categories", response_class=HTMLResponse)
async def admin_categories_list(request: Request, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    categories = list(session.exec(select(Category).order_by(Category.display_order)).all())
    # Article counts per category
    category_counts = {}
    for c in categories:
        cnt = len(session.exec(select(Article).where(Article.category == c.name)).all())
        category_counts[c.name] = cnt
        
    return render(
        "admin/categories_list.html",
        **ctx,
        categories=categories,
        category_counts=category_counts,
        request=request,
    )


@router.post("/categories/create")
async def admin_category_create(
    request: Request,
    name: str = Form(...),
    display_name: str = Form(...),
    display_order: int = Form(0),
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    clean_name = name.strip().lower().replace(" ", "-")
    if session.get(Category, clean_name):
        raise HTTPException(status_code=400, detail="Category exists")
    session.add(Category(name=clean_name, display_name=display_name.strip(), display_order=display_order))
    session.commit()
    
    admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
    log_activity(session, admin_id, "category_created", target_type="category", details=f"Created category: {display_name} ({clean_name})", ip_address=get_client_ip(request))
    return RedirectResponse(url="/admin/categories", status_code=303)


@router.post("/categories/{name}/delete")
async def admin_category_delete(request: Request, name: str, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    cat = session.get(Category, name)
    if cat:
        display = cat.display_name
        session.delete(cat)
        session.commit()
        admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
        log_activity(session, admin_id, "category_deleted", target_type="category", details=f"Deleted category: {display} ({name})", ip_address=get_client_ip(request))
    return RedirectResponse(url="/admin/categories", status_code=303)


# ---------- PLATFORMS CRUD ----------
@router.get("/platforms", response_class=HTMLResponse)
async def admin_platforms_list(request: Request, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    platforms = list(session.exec(select(Platform).order_by(Platform.display_order)).all())
    platform_counts = {}
    for p in platforms:
        cnt = len(session.exec(select(Availability).where(Availability.platform_id == p.name)).all())
        platform_counts[p.name] = cnt
        
    return render(
        "admin/platforms_list.html",
        **ctx,
        platforms=platforms,
        platform_counts=platform_counts,
        request=request,
    )


@router.post("/platforms/create")
async def admin_platform_create(
    request: Request,
    name: str = Form(...),
    display_name: str = Form(...),
    official_url: str = Form(...),
    display_order: int = Form(0),
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    clean_name = name.strip()
    if session.get(Platform, clean_name):
        raise HTTPException(status_code=400, detail="Platform exists")
    if not official_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="URL must start with https://")
    session.add(Platform(name=clean_name, display_name=display_name.strip(), official_url=official_url.strip(), display_order=display_order))
    session.commit()
    
    admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
    log_activity(session, admin_id, "platform_created", target_type="platform", details=f"Created platform: {display_name} ({clean_name})", ip_address=get_client_ip(request))
    return RedirectResponse(url="/admin/platforms", status_code=303)


@router.post("/platforms/{name}/delete")
async def admin_platform_delete(request: Request, name: str, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    p = session.get(Platform, name)
    if p:
        display = p.display_name
        session.delete(p)
        session.commit()
        admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
        log_activity(session, admin_id, "platform_deleted", target_type="platform", details=f"Deleted platform: {display} ({name})", ip_address=get_client_ip(request))
    return RedirectResponse(url="/admin/platforms", status_code=303)


# ---------- USER MANAGEMENT ----------
@router.get("/users", response_class=HTMLResponse)
async def admin_users_list(request: Request, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    promoted = request.query_params.get("promoted")
    admin_created = request.query_params.get("admin_created")
    error = request.query_params.get("error")
    
    users = list(session.exec(select(User1).order_by(User1.created_at.desc())).all())
    user_comment_counts = {}
    for u in users:
        cnt = len(session.exec(select(Comment).where(Comment.user_id == u.id)).all())
        user_comment_counts[u.id] = cnt
        
    admin_emails = {a.email.lower() for a in session.exec(select(Admin1).where(Admin1.is_active == True)).all() if a.email}
        
    return render(
        "admin/users_list.html",
        **ctx,
        users=users,
        user_comment_counts=user_comment_counts,
        admin_emails=admin_emails,
        promoted=promoted,
        admin_created=admin_created,
        error=error,
        request=request,
    )


@router.post("/users/{user_id}/make-admin")
async def admin_make_user_admin(
    request: Request,
    user_id: int,
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    user = session.get(User1, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    clean_email = user.email.strip().lower()
    existing_admin = session.exec(select(Admin1).where(Admin1.email == clean_email)).first()
    
    if existing_admin:
        existing_admin.is_active = True
        existing_admin.hashed_password = user.hashed_password
        session.add(existing_admin)
    else:
        new_admin = Admin1(
            name=user.name,
            email=clean_email,
            hashed_password=user.hashed_password,
            bio=user.bio or f"Promoted from reader account @{user.username}",
            avatar_url=user.avatar_url,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        session.add(new_admin)
        
    session.commit()
    
    admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
    log_activity(session, admin_id, "user_promoted_to_admin", target_type="user", target_id=user.id, details=f"Promoted user {user.username} ({clean_email}) to Administrator", ip_address=get_client_ip(request))
    
    create_user_notification(
        session,
        user_id=user.id,
        title="व्यवस्थापक अधिकार (Admin Access Granted)",
        message="आपको Hindi Anime Guide का व्यवस्थापक बनाया गया है! अब आप /admin पर अपने पासवर्ड से सीधे लॉगिन कर सकते हैं।",
        notification_type="success",
        link="/admin"
    )
    
    create_notification(
        session,
        "User Promoted to Admin",
        f"User {user.name} (@{user.username}, {clean_email}) was promoted to Administrator.",
        notification_type="success",
        link="/admin/users"
    )
    
    return RedirectResponse(url="/admin/users?promoted=1", status_code=303)


@router.post("/users/{user_id}/toggle-ban")
async def admin_toggle_ban(request: Request, user_id: int, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    user = session.get(User1, user_id)
    if user:
        user.is_banned = not user.is_banned
        user.updated_at = datetime.now(timezone.utc)
        session.add(user)
        session.commit()
        
        status_text = "banned" if user.is_banned else "unbanned"
        admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
        log_activity(session, admin_id, f"user_{status_text}", target_type="user", target_id=user_id, details=f"User {user.username} was {status_text}", ip_address=get_client_ip(request))
        if user.is_banned:
            create_notification(session, "User Banned", f"User {user.username} ({user.email}) was suspended.", notification_type="warning", link="/admin/users")
            
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/delete")
async def admin_delete_user(request: Request, user_id: int, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    user = session.get(User1, user_id)
    if user:
        uname = user.username
        delete_user_completely(session, user)
        admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
        log_activity(session, admin_id, "user_deleted", target_type="user", target_id=user_id, details=f"Deleted user: {uname}", ip_address=get_client_ip(request))
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/bulk-action")
async def admin_users_bulk_action(
    request: Request,
    bulk_action: str = Form(...),
    user_ids: List[int] = Form(...),
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
    
    count = 0
    if bulk_action == "ban":
        for uid in user_ids:
            u = session.get(User1, uid)
            if u:
                u.is_banned = True
                session.add(u)
                count += 1
        session.commit()
        log_activity(session, admin_id, "users_bulk_banned", target_type="user", details=f"Bulk banned {count} users", ip_address=get_client_ip(request))
    elif bulk_action == "unban":
        for uid in user_ids:
            u = session.get(User1, uid)
            if u:
                u.is_banned = False
                session.add(u)
                count += 1
        session.commit()
        log_activity(session, admin_id, "users_bulk_unbanned", target_type="user", details=f"Bulk unbanned {count} users", ip_address=get_client_ip(request))
    elif bulk_action == "delete":
        for uid in user_ids:
            u = session.get(User1, uid)
            if u:
                delete_user_completely(session, u)
                count += 1
        log_activity(session, admin_id, "users_bulk_deleted", target_type="user", details=f"Bulk deleted {count} users", ip_address=get_client_ip(request))
        
    return RedirectResponse(url="/admin/users", status_code=303)


# ---------- COMMENT MODERATION ----------
@router.get("/comments", response_class=HTMLResponse)
async def admin_comments_list(request: Request, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    comments = session.exec(
        select(Comment)
        .options(selectinload(Comment.article_rel), selectinload(Comment.user_rel))
        .order_by(Comment.created_at.desc())
        .limit(100)
    ).all()
    
    return render(
        "admin/comments_list.html",
        **ctx,
        comments=comments,
        request=request,
    )


@router.post("/comments/{comment_id}/toggle-approve")
async def admin_toggle_approve(request: Request, comment_id: int, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    c = session.get(Comment, comment_id)
    if c:
        c.is_approved = not c.is_approved
        session.add(c)
        session.commit()
        action_name = "comment_approved" if c.is_approved else "comment_unapproved"
        admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
        log_activity(session, admin_id, action_name, target_type="comment", target_id=comment_id, details=f"{action_name.replace('_', ' ').capitalize()} id {comment_id}", ip_address=get_client_ip(request))
        
        if c.is_approved and c.user_id:
            art = session.get(Article, c.article_id)
            art_title = art.title if art else "लेख"
            art_slug = art.slug if art else ""
            create_user_notification(
                session,
                user_id=c.user_id,
                title="टिप्पणी प्रकाशित हुई (Comment Published)",
                message=f"आपकी टिप्पणी '{art_title}' पर प्रकाशित हो गई है!",
                notification_type="success",
                link=f"/article/{art_slug}#comments" if art_slug else ""
            )
    return RedirectResponse(url="/admin/comments", status_code=303)


@router.post("/comments/{comment_id}/approve")
async def admin_approve_comment(request: Request, comment_id: int, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    c = session.get(Comment, comment_id)
    if c and not c.is_approved:
        c.is_approved = True
        session.add(c)
        session.commit()
        admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
        log_activity(session, admin_id, "comment_approved", target_type="comment", target_id=comment_id, details=f"Approved comment id {comment_id}", ip_address=get_client_ip(request))
        if c.user_id:
            art = session.get(Article, c.article_id)
            art_title = art.title if art else "लेख"
            art_slug = art.slug if art else ""
            create_user_notification(
                session,
                user_id=c.user_id,
                title="टिप्पणी प्रकाशित हुई (Comment Published)",
                message=f"आपकी टिप्पणी '{art_title}' पर प्रकाशित हो गई है!",
                notification_type="success",
                link=f"/article/{art_slug}#comments" if art_slug else ""
            )
    return RedirectResponse(url="/admin/comments", status_code=303)


@router.post("/comments/{comment_id}/delete")
async def admin_delete_comment(request: Request, comment_id: int, session: Session = Depends(get_session)):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    
    c = session.get(Comment, comment_id)
    if c:
        session.delete(c)
        session.commit()
        admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
        log_activity(session, admin_id, "comment_deleted", target_type="comment", target_id=comment_id, details=f"Deleted comment id {comment_id}", ip_address=get_client_ip(request))
    return RedirectResponse(url="/admin/comments", status_code=303)


@router.post("/comments/bulk-action")
async def admin_comments_bulk_action(
    request: Request,
    bulk_action: str = Form(...),
    comment_ids: List[int] = Form(...),
    session: Session = Depends(get_session)
):
    admin = await require_admin(request)
    ctx = get_admin_context(session, admin)
    admin_id = ctx["admin_user"].id if ctx["admin_user"] else None
    
    count = 0
    if bulk_action == "approve":
        for cid in comment_ids:
            c = session.get(Comment, cid)
            if c:
                c.is_approved = True
                session.add(c)
                if c.user_id:
                    art = session.get(Article, c.article_id)
                    art_title = art.title if art else "लेख"
                    art_slug = art.slug if art else ""
                    create_user_notification(
                        session,
                        user_id=c.user_id,
                        title="टिप्पणी प्रकाशित हुई (Comment Published)",
                        message=f"आपकी टिप्पणी '{art_title}' पर प्रकाशित हो गई है!",
                        notification_type="success",
                        link=f"/article/{art_slug}#comments" if art_slug else ""
                    )
                count += 1
        session.commit()
        log_activity(session, admin_id, "comments_bulk_approved", target_type="comment", details=f"Bulk approved {count} comments", ip_address=get_client_ip(request))
    elif bulk_action == "delete":
        for cid in comment_ids:
            c = session.get(Comment, cid)
            if c:
                session.delete(c)
                count += 1
        session.commit()
        log_activity(session, admin_id, "comments_bulk_deleted", target_type="comment", details=f"Bulk deleted {count} comments", ip_address=get_client_ip(request))
        
    return RedirectResponse(url="/admin/comments", status_code=303)


# ---------- STARTUP / HELPER: Auto-create default admin ----------
def ensure_admin_user(session: Session) -> None:
    s = get_settings()
    existing = session.exec(select(Admin1).where(Admin1.email == s.admin_email)).first()
    if not existing:
        session.add(
            Admin1(
                name="Super Admin",
                email=s.admin_email,
                hashed_password=get_password_hash(s.admin_password),
                is_active=True,
                totp_enabled=(s.env.lower() in ("test", "testing")),
            )
        )
        session.commit()
        print(f"✅ Default admin created: {s.admin_email}")
    else:
        existing.hashed_password = get_password_hash(s.admin_password)
        existing.is_active = True
        if s.env.lower() in ("test", "testing"):
            existing.totp_enabled = True
        session.add(existing)
        session.commit()
        print(f"✅ Default admin password synced: {s.admin_email}")


