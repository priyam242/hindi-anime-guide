
"""Public pages router: Home, Explore/Search, Article, Category, Info pages, Sitemap, Robots."""
from __future__ import annotations
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Query, Form, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from sqlmodel import Session, select
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from .. import db
from ..db import get_session
from ..models import (
    Category, Article, Availability, Comment, Platform, Bookmark,
    Notification, Admin1, AnimeDraft, AwardCeremony, AwardEntry,
)
from ..markdown_utils import render_markdown_with_toc
from ..templates_config import render, extract_episode, extract_youtube_id
from automation.fetchers.anilist import (
    fetch_live_weekly_schedule,
    get_season_cycle,
    normalize_studio_name,
    PLATFORM_CLASSES,
    IST_TZ,
)
from automation.fetchers.trailers import fetch_live_trailers

router = APIRouter()
logger = logging.getLogger(__name__)

# In-memory rate-limiting for reporting outdated availability info (IP -> list of timestamps)
_report_rate_limit: Dict[str, List[float]] = {}

# ---------------------------------------------------------
# In-Memory SWR (Stale-While-Revalidate) Caching
# - Fresh: Return cache immediately (0 ms delay)
# - Stale: Return cache immediately and refresh via background thread (0 ms visitor delay)
# - Cold: Compute synchronously on startup / first request
# ---------------------------------------------------------
SWR_FRESH_TTL: float = 300.0   # 5 minutes fresh
SWR_STALE_TTL: float = 3600.0  # 60 minutes stale-while-revalidate window

_CATEGORIES_CACHE: tuple[List[Category], float] | None = None
CATEGORIES_CACHE_TTL: float = 600.0

_PLATFORMS_CACHE: tuple[List[Platform], float] | None = None
PLATFORMS_CACHE_TTL: float = 600.0

_HOME_CACHE: dict[str, Any] | None = None
HOME_CACHE_TTL: float = 300.0

_DEFAULT_EXPLORE_CACHE: tuple[List[Article], float] | None = None

_NEWS_CACHE: Dict[tuple[int, int, Optional[str], bool], tuple[List[AnimeDraft], int, Optional[AnimeDraft], List[AnimeDraft], float]] = {}
_SCHEDULE_CACHE: Dict[tuple[Optional[str], Optional[int]], dict[str, Any]] = {}
_MANGA_CACHE: dict[str, Any] | None = None
_AWARDS_YEARS_CACHE: tuple[List[int], float] | None = None
_AWARDS_CACHE: Dict[int, dict[str, Any]] = {}
_TRAILERS_CACHE: Dict[tuple[int, int, str, int], tuple[list, int, float]] = {}

NEWS_CATEGORIES = [
    {"slug": "Announcement", "name_en": "Announcement", "name_hi": "घोषणाएं", "icon": "fa-bullhorn", "badge_class": "bg-blue-600"},
    {"slug": "Trailer", "name_en": "Trailer", "name_hi": "ट्रेलर्स", "icon": "fa-film", "badge_class": "bg-pink-600"},
    {"slug": "Release Date", "name_en": "Release Date", "name_hi": "रिलीज़ डेट्स", "icon": "fa-calendar-check", "badge_class": "bg-emerald-600"},
    {"slug": "Movie", "name_en": "Movie", "name_hi": "फ़िल्में", "icon": "fa-film", "badge_class": "bg-purple-600"},
    {"slug": "Interview", "name_en": "Interview", "name_hi": "इंटरव्यू", "icon": "fa-microphone", "badge_class": "bg-amber-600"},
    {"slug": "Event Recap", "name_en": "Event Recap", "name_hi": "इवेंट्स", "icon": "fa-award", "badge_class": "bg-indigo-600"},
    {"slug": "Industry", "name_en": "Industry", "name_hi": "इंडस्ट्री", "icon": "fa-chart-line", "badge_class": "bg-teal-600"},
]
_UPDATES_HUB_CACHE: tuple[dict, float] | None = None
HUB_CACHE_TTL: float = 300.0


def is_caching_enabled() -> bool:
    """Caching is active in development and production, bypassed during automated tests."""
    from ..config import get_settings
    return get_settings().env.lower() not in ("test", "testing")


def _revalidate_async(worker_fn, *args, **kwargs):
    """Run SWR cache revalidation asynchronously in background thread with an isolated DB session.
    Ensures zero delay for web visitors while caches stay continuously warm and up-to-date.
    """
    def _runner():
        try:
            from ..db import engine
            with Session(engine) as bg_session:
                worker_fn(bg_session, *args, **kwargs)
        except Exception as exc:
            import logging
            logging.getLogger("app.routers.pages").error("SWR background revalidation error: %s", exc)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()


def invalidate_public_caches() -> None:
    """Invalidate all public page and metadata in-memory caches."""
    global _CATEGORIES_CACHE, _PLATFORMS_CACHE, _HOME_CACHE, _DEFAULT_EXPLORE_CACHE, _MANGA_CACHE, _AWARDS_YEARS_CACHE, _AWARDS_CACHE, _NEWS_CACHE, _SCHEDULE_CACHE, _TRAILERS_CACHE, _UPDATES_HUB_CACHE
    _CATEGORIES_CACHE = None
    _PLATFORMS_CACHE = None
    _HOME_CACHE = None
    _DEFAULT_EXPLORE_CACHE = None
    _MANGA_CACHE = None
    _AWARDS_YEARS_CACHE = None
    _AWARDS_CACHE.clear()
    _NEWS_CACHE.clear()
    _SCHEDULE_CACHE.clear()
    _TRAILERS_CACHE.clear()
    _UPDATES_HUB_CACHE = None


def get_categories(session: Session) -> List[Category]:
    global _CATEGORIES_CACHE
    if not is_caching_enabled():
        return list(session.exec(select(Category).order_by(Category.display_order)).all())
    now = time.monotonic()
    if _CATEGORIES_CACHE and _CATEGORIES_CACHE[1] > now:
        return _CATEGORIES_CACHE[0]
    cats = list(session.exec(select(Category).order_by(Category.display_order)).all())
    _CATEGORIES_CACHE = (cats, now + CATEGORIES_CACHE_TTL)
    return cats


def get_platforms(session: Session) -> List[Platform]:
    global _PLATFORMS_CACHE
    if not is_caching_enabled():
        return list(session.exec(select(Platform).order_by(Platform.display_order)).all())
    now = time.monotonic()
    if _PLATFORMS_CACHE and _PLATFORMS_CACHE[1] > now:
        return _PLATFORMS_CACHE[0]
    plats = list(session.exec(select(Platform).order_by(Platform.display_order)).all())
    _PLATFORMS_CACHE = (plats, now + PLATFORMS_CACHE_TTL)
    return plats


def _related_articles(session: Session, article: Article) -> List[Article]:
    order_expr = func.coalesce(Article.published_at, Article.created_at).desc()
    cat_names = [c.name for c in (article.categories or [])] or ([article.category] if article.category else [])
    related = list(session.exec(
        select(Article)
        .where(
            or_(
                Article.category.in_(cat_names),
                Article.categories.any(Category.name.in_(cat_names))
            ),
            Article.id != article.id
        )
        .options(
            selectinload(Article.category_rel),
            selectinload(Article.categories),
            selectinload(Article.availabilities),
        )
        .order_by(order_expr, Article.id.desc())
        .limit(3)
    ).all())
    if len(related) < 3:
        excluded = {article.id}.union(a.id for a in related)
        top_up = list(session.exec(
            select(Article)
            .where(Article.id.notin_(excluded))
            .options(
                selectinload(Article.category_rel),
                selectinload(Article.categories),
                selectinload(Article.availabilities),
            )
            .order_by(order_expr, Article.id.desc())
            .limit(3 - len(related))
        ).all())
        related.extend(top_up)
    return related


def compute_home_page_data(session: Session) -> dict:
    """Compute fresh homepage articles and latest updates factual payload."""
    articles = list(session.exec(
        select(Article)
        .where(Article.published_at.is_not(None))
        .options(
            selectinload(Article.category_rel),
            selectinload(Article.categories),
            selectinload(Article.availabilities),
        )
        .order_by(Article.created_at.desc(), Article.id.desc())
        .limit(24)
    ).all())

    featured_article = None
    side_articles = []
    grid_articles = []
    if articles:
        featured_idx = 0
        for idx, art in enumerate(articles):
            if getattr(art, "is_featured", False):
                featured_idx = idx
                break
        featured_article = articles[featured_idx]
        other_articles = [a for i, a in enumerate(articles) if i != featured_idx]
        side_articles = other_articles[:3]
        grid_articles = other_articles[3:]

    # Strict 14-day cutoff for Home latest updates
    cutoff_14d = datetime.now(timezone.utc) - timedelta(days=14)
    latest_updates = list(session.exec(
        select(AnimeDraft)
        .where(
            AnimeDraft.status == "published",
            AnimeDraft.is_stale == False,
            AnimeDraft.published_at.is_not(None),
            AnimeDraft.source_url.is_not(None),
            AnimeDraft.source_url != "",
            AnimeDraft.published_at >= cutoff_14d,
        )
        .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
        .limit(4)
    ).all())

    return {
        "articles": articles,
        "latest_updates": latest_updates,
        "featured_article": featured_article,
        "side_articles": side_articles,
        "grid_articles": grid_articles,
    }


def get_home_page_data(session: Session) -> dict:
    """Stale-While-Revalidate Home page data getter."""
    global _HOME_CACHE
    caching = is_caching_enabled()
    if not caching:
        return compute_home_page_data(session)

    now = time.monotonic()
    if _HOME_CACHE:
        # 1. Fresh Cache Hit (< SWR_FRESH_TTL)
        if now < _HOME_CACHE["fresh_until"]:
            return _HOME_CACHE["data"]

        # 2. Stale Cache Hit (< SWR_STALE_TTL): Return immediately and refresh in background
        if now < _HOME_CACHE["stale_until"]:
            if not _HOME_CACHE.get("is_refreshing"):
                _HOME_CACHE["is_refreshing"] = True

                def _refresh_home(bg_s: Session):
                    global _HOME_CACHE
                    try:
                        fresh = compute_home_page_data(bg_s)
                        _HOME_CACHE = {
                            "data": fresh,
                            "fresh_until": time.monotonic() + SWR_FRESH_TTL,
                            "stale_until": time.monotonic() + SWR_STALE_TTL,
                            "is_refreshing": False,
                        }
                    except Exception:
                        if _HOME_CACHE:
                            _HOME_CACHE["is_refreshing"] = False

                _revalidate_async(_refresh_home)
            return _HOME_CACHE["data"]

    # 3. Cold Cache Miss: Compute synchronously
    data = compute_home_page_data(session)
    _HOME_CACHE = {
        "data": data,
        "fresh_until": now + SWR_FRESH_TTL,
        "stale_until": now + SWR_STALE_TTL,
        "is_refreshing": False,
    }
    return data


@router.get("/", response_class=HTMLResponse)
def home_page(request: Request, session: Session = Depends(get_session)):
    data = get_home_page_data(session)
    categories = get_categories(session)
    user = getattr(request.state, "user", None)

    return render(
        "home.html",
        articles=data["articles"],
        latest_updates=data["latest_updates"],
        featured_article=data["featured_article"],
        side_articles=data["side_articles"],
        grid_articles=data["grid_articles"],
        categories=categories,
        user=user,
        active_nav="home",
        request=request,
    )


# ---------- EXPLORE & SEARCH ----------
@router.get("/explore", response_class=HTMLResponse)
@router.get("/search", response_class=HTMLResponse)
def explore_page(
    request: Request,
    q: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    hindi_dub: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    sort: Optional[str] = Query("latest"),
    session: Session = Depends(get_session)
):
    query = (
        select(Article)
        .where(Article.published_at.is_not(None))
        .options(
            selectinload(Article.category_rel),
            selectinload(Article.categories),
            selectinload(Article.availabilities).selectinload(Availability.platform_rel),
        )
    )

    clean_q = (q or "").strip()
    if clean_q:
        q_filter = f"%{clean_q}%"
        query = query.where(
            or_(
                Article.title.ilike(q_filter),
                Article.content_md.ilike(q_filter),
                Article.meta_description.ilike(q_filter),
                Article.category.ilike(q_filter),
                Article.categories.any(Category.name.ilike(q_filter)),
                Article.categories.any(Category.display_name.ilike(q_filter)),
            )
        )

    if category and category.strip():
        c_clean = category.strip().lower()
        query = query.where(
            or_(
                func.lower(Article.category) == c_clean,
                Article.categories.any(func.lower(Category.name) == c_clean)
            )
        )

    if platform and platform.strip():
        query = query.where(
            Article.availabilities.any(Availability.platform_id == platform.strip())
        )

    is_hindi_dub = str(hindi_dub).lower() in ("1", "true", "yes", "on")
    if is_hindi_dub:
        query = query.where(
            Article.availabilities.any(
                or_(
                    func.lower(Availability.hindi_dub).in_(["yes", "available", "true", "1", "हाँ", "हां"]),
                    Availability.languages.ilike("%hindi%")
                )
            )
        )

    if sort == "az":
        query = query.order_by(Article.title.asc())
    else:
        order_expr = func.coalesce(Article.published_at, Article.created_at).desc()
        query = query.order_by(order_expr, Article.id.desc())

    global _DEFAULT_EXPLORE_CACHE
    caching = is_caching_enabled()
    now = time.monotonic()
    is_default = (not clean_q and not category and not platform and not is_hindi_dub and (not sort or sort == "latest"))
    if caching and is_default and _DEFAULT_EXPLORE_CACHE and _DEFAULT_EXPLORE_CACHE[1] > now:
        articles = _DEFAULT_EXPLORE_CACHE[0]
    else:
        articles = list(session.exec(query).all())
        if caching and is_default:
            _DEFAULT_EXPLORE_CACHE = (articles, now + HUB_CACHE_TTL)

    matching_updates = []
    if clean_q:
        q_filter = f"%{clean_q}%"
        matching_updates = list(session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                or_(
                    AnimeDraft.title_hi.ilike(q_filter),
                    AnimeDraft.title_en.ilike(q_filter),
                    AnimeDraft.summary_hi.ilike(q_filter),
                    AnimeDraft.summary_en.ilike(q_filter),
                )
            )
            .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .limit(8)
        ).all())

    categories = get_categories(session)
    platforms = get_platforms(session)
    user = getattr(request.state, "user", None)

    return render(
        "explore.html",
        articles=articles,
        matching_updates=matching_updates,
        total_count=len(articles),
        q=clean_q,
        selected_platform=platform or "",
        selected_hindi_dub=is_hindi_dub,
        selected_category=category or "",
        selected_sort=sort or "latest",
        categories=categories,
        platforms=platforms,
        user=user,
        active_nav="explore",
        request=request,
    )


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _find_manga_for_article(session: Session, article: Article) -> Optional[dict[str, Any]]:
    """Look up corresponding source manga metadata and progress for an anime guide article."""
    anime_title = (article.anime_title_en or "").lower()
    slug = (article.slug or "").lower()

    manga_items = session.exec(
        select(AnimeDraft).where(
            AnimeDraft.content_type == "manga",
            AnimeDraft.status == "published",
            AnimeDraft.is_stale == False,
        )
    ).all()

    matched: Optional[AnimeDraft] = None
    for m in manga_items:
        m_en = (m.title_en or "").lower()
        if anime_title and (anime_title in m_en or m_en in anime_title):
            matched = m
            break
        for token in ["one-piece", "jujutsu-kaisen", "demon-slayer", "solo-leveling", "attack-on-titan", "death-note", "naruto", "dragon-ball", "spy-x-family", "haikyuu", "blue-lock", "frieren"]:
            if token in slug and token.replace("-", " ") in m_en:
                matched = m
                break
        if matched:
            break

    if not matched:
        return None

    links = []
    if matched.official_links:
        try:
            links = json.loads(matched.official_links)
        except Exception:
            links = []

    now_utc = datetime.now(timezone.utc)
    dt_utc = _ensure_utc(matched.next_volume_date)
    future_next_vol = matched.next_volume_date if (dt_utc and dt_utc > now_utc) else None
    live_progress = matched.anime_progress if (matched.anime_progress and matched.anime_progress.strip()) else None

    return {
        "id": matched.id,
        "title_en": matched.title_en,
        "title_hi": matched.title_hi or matched.title_en,
        "image_url": matched.image_url,
        "status": matched.publication_status or "Ongoing",
        "latest_chapter": matched.latest_chapter,
        "chapters": matched.chapters,
        "volumes": matched.volumes,
        "score": matched.score,
        "rank": matched.rank,
        "next_volume_date": future_next_vol,
        "anime_progress": live_progress,
        "official_links": links,
        "source_name": matched.source_name,
        "source_url": matched.source_url,
    }


def _match_anime_article_for_manga(manga: AnimeDraft, articles: list[Article]) -> Optional[Article]:
    """Find corresponding anime guide article in DB for a manga draft."""
    m_en = (manga.title_en or "").lower()
    for a in articles:
        if a.anime_title_en and (a.anime_title_en.lower() in m_en or m_en in a.anime_title_en.lower()):
            return a
    for token in ["one-piece", "jujutsu-kaisen", "demon-slayer", "solo-leveling", "attack-on-titan", "death-note", "naruto", "dragon-ball", "spy-x-family", "haikyuu", "blue-lock", "frieren"]:
        if token.replace("-", " ") in m_en:
            for a in articles:
                if token in (a.slug or "").lower():
                    return a
    return None


# ---------- ARTICLE DETAIL ----------
@router.get("/article/{slug}", response_class=HTMLResponse)
def article_page(request: Request, slug: str, session: Session = Depends(get_session)):
    stmt = (
        select(Article)
        .where(Article.slug == slug)
        .options(
            selectinload(Article.category_rel),
            selectinload(Article.categories),
            selectinload(Article.availabilities).selectinload(Availability.platform_rel),
        )
    )
    article = session.exec(stmt).first()
    if article is None:
        raise HTTPException(status_code=404, detail="लेख नहीं मिला")
    
    # Load approved comments with user info
    comments = session.exec(
        select(Comment)
        .where(Comment.article_id == article.id)
        .where(Comment.is_approved == True)
        .order_by(Comment.created_at.desc())
    ).all()
    for c in comments:
        _ = c.user_rel
    
    categories = get_categories(session)
    related = _related_articles(session, article)
    user = getattr(request.state, "user", None)
    content_html, toc_html = render_markdown_with_toc(article.content_md)
    valid_availabilities = [a for a in article.availabilities if getattr(a, "last_checked", None) is not None]

    # Look up corresponding source manga and progress
    manga_info = _find_manga_for_article(session, article)

    is_bookmarked = False
    user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    user_type = user.get("user_type") if isinstance(user, dict) else "user"
    if user_id and user_type != "admin":
        bm = session.exec(
            select(Bookmark).where(Bookmark.user_id == user_id, Bookmark.article_id == article.id)
        ).first()
        is_bookmarked = bm is not None

    return render(
        "article.html",
        article=article,
        availabilities=article.availabilities,
        valid_availabilities=valid_availabilities,
        related_articles=related,
        categories=categories,
        comments=comments,
        user=user,
        is_bookmarked=is_bookmarked,
        content_html=content_html,
        toc_html=toc_html,
        manga_info=manga_info,
        request=request,
    )


# ---------- REPORT OUTDATED STREAMING INFO ----------
@router.post("/article/{slug}/report-outdated")
async def report_outdated_info(
    request: Request,
    slug: str,
    note: str = Form(...),
    platform_name: Optional[str] = Form(""),
    session: Session = Depends(get_session)
):
    article = session.exec(select(Article).where(Article.slug == slug)).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    # Simple IP rate limiting: max 5 reports per 10 minutes
    client_ip = request.client.host if request.client else "unknown"
    now_ts = datetime.now().timestamp()
    timestamps = _report_rate_limit.get(client_ip, [])
    timestamps = [ts for ts in timestamps if now_ts - ts < 600]
    if len(timestamps) >= 5:
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", ""):
            return JSONResponse(status_code=429, content={"error": "Too many requests. Please wait a few minutes."})
        return HTMLResponse(status_code=429, content="Too many requests. Please wait a few minutes.")
    timestamps.append(now_ts)
    _report_rate_limit[client_ip] = timestamps

    clean_note = note.strip()[:1000]
    if not clean_note:
        return JSONResponse(status_code=400, content={"error": "Note cannot be empty."})
        
    p_info = f" (प्लेटफॉर्म: {platform_name.strip()})" if platform_name and platform_name.strip() else ""
    user = getattr(request.state, "user", None)
    username = (user.get("username") or user.get("name")) if isinstance(user, dict) else getattr(user, "username", None)
    user_str = f"User @{username}" if username else f"Guest ({client_ip})"
    
    admins = session.exec(select(Admin1)).all()
    if admins:
        for admin in admins:
            notif = Notification(
                admin_id=admin.id,
                user_id=None,
                title="स्ट्रीमिंग अपडेट रिपोर्ट",
                message=f"{user_str} ने '{article.title}' के लिए रिपोर्ट किया: {clean_note}{p_info}",
                notification_type="warning",
                link=f"/article/{article.slug}",
                is_read=False,
                created_at=datetime.now(timezone.utc)
            )
            session.add(notif)
    else:
        notif = Notification(
            admin_id=None,
            user_id=None,
            title="स्ट्रीमिंग अपडेट रिपोर्ट",
            message=f"{user_str} ने '{article.title}' के लिए रिपोर्ट किया: {clean_note}{p_info}",
            notification_type="warning",
            link=f"/article/{article.slug}",
            is_read=False,
            created_at=datetime.now(timezone.utc)
        )
        session.add(notif)
    session.commit()

    if request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"success": True, "message": "स्ट्रीमिंग जानकारी रिपोर्ट दर्ज कर ली गई है। धन्यवाद!"})
    return RedirectResponse(url=f"/article/{slug}?reported=1", status_code=303)


# ---------- CATEGORY PAGE ----------
@router.get("/category/{slug}", response_class=HTMLResponse)
def category_page(request: Request, slug: str, session: Session = Depends(get_session)):
    category = session.exec(select(Category).where(func.lower(Category.name) == slug.strip().lower())).first()
    categories = get_categories(session)
    if not category:
        raise HTTPException(status_code=404, detail="श्रेणी नहीं मिली")
    articles = session.exec(
        select(Article)
        .where(
            or_(
                Article.category == category.name,
                Article.categories.any(Category.name == category.name)
            )
        )
        .options(
            selectinload(Article.category_rel),
            selectinload(Article.categories),
            selectinload(Article.availabilities),
        )
        .order_by(Article.created_at.desc(), Article.id.desc())
    ).all()
    user = getattr(request.state, "user", None)
    return render(
        "category.html",
        category=category,
        articles=articles,
        categories=categories,
        user=user,
        active_nav=slug,
        request=request,
    )


# ---------- AUTOMATION PUBLIC SECTIONS & NEWS/UPDATES ----------
@router.get("/updates", response_class=HTMLResponse)
def updates_hub_page(
    request: Request,
    session: Session = Depends(get_session),
):
    global _UPDATES_HUB_CACHE
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    lang = request.cookies.get("site_lang", "hi")

    caching = is_caching_enabled()
    now = time.monotonic()
    if caching and _UPDATES_HUB_CACHE and _UPDATES_HUB_CACHE[1] > now:
        data = _UPDATES_HUB_CACHE[0]
    else:
        # 1. Latest News (try pure news, fallback to latest published updates)
        news_items = list(session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.content_type == "news"
            )
            .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .limit(4)
        ).all())
        if not news_items:
            news_items = list(session.exec(
                select(AnimeDraft)
                .where(
                    AnimeDraft.status == "published",
                    AnimeDraft.is_stale == False
                )
                .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
                .limit(4)
            ).all())

        # 2. Season Schedule / Airing
        schedule_items = list(session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.content_type == "schedule"
            )
            .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .limit(4)
        ).all())

        # 3. Manga Updates
        manga_items = list(session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.content_type == "manga"
            )
            .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .limit(4)
        ).all())

        # 4. Awards
        award_items = list(session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.content_type == "award"
            )
            .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .limit(4)
        ).all())

        # 5. Trailers
        trailer_items = list(session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                or_(
                    AnimeDraft.content_type == "trailer",
                    AnimeDraft.title_en.ilike("%trailer%"),
                    AnimeDraft.title_hi.ilike("%ट्रेलर%"),
                    AnimeDraft.summary_en.ilike("%trailer%"),
                    AnimeDraft.summary_hi.ilike("%ट्रेलर%"),
                ),
            )
            .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .limit(4)
        ).all())

        data = {
            "news": news_items,
            "schedule": schedule_items,
            "manga": manga_items,
            "awards": award_items,
            "trailers": trailer_items,
        }
        if caching:
            _UPDATES_HUB_CACHE = (data, now + HUB_CACHE_TTL)

    return render(
        "updates.html",
        categories=categories,
        data=data,
        lang=lang,
        user=user,
        active_nav="updates",
        request=request,
    )


@router.get("/news", response_class=HTMLResponse)
@router.get("/news/archive", response_class=HTMLResponse)
def news_page(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(12, ge=1, le=50),
    category: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    lang = request.cookies.get("site_lang", "hi")
    is_archive = request.url.path.rstrip("/").endswith("/archive")

    caching = is_caching_enabled()
    now = time.monotonic()
    cache_key = (page, per_page, category, is_archive)
    cached = _NEWS_CACHE.get(cache_key) if caching else None
    if cached and cached[4] > now:
        items, total_count, featured_item, editor_picks = cached[0], cached[1], cached[2], cached[3]
    else:
        # Strict editorial news base query: published, not stale, verified source_url & factual published_at
        base_query = select(AnimeDraft).where(
            AnimeDraft.status == "published",
            AnimeDraft.is_stale == False,
            AnimeDraft.published_at.is_not(None),
            AnimeDraft.source_url.is_not(None),
            AnimeDraft.source_url != "",
        )

        cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)
        if not is_archive:
            base_query = base_query.where(AnimeDraft.published_at >= cutoff_30d)

        # Category Filter (7 standard categories + content_type fallback)
        if category and category != "all":
            valid_cats = {c["slug"] for c in NEWS_CATEGORIES}
            if category in valid_cats:
                if category == "Trailer":
                    base_query = base_query.where(
                        or_(
                            AnimeDraft.news_category == "Trailer",
                            AnimeDraft.content_type == "trailer",
                        )
                    )
                else:
                    base_query = base_query.where(AnimeDraft.news_category == category)
            elif category in ["news", "schedule", "manga", "award", "trailer"]:
                base_query = base_query.where(AnimeDraft.content_type == category)

        total_count = session.exec(select(func.count()).select_from(base_query.subquery())).one()
        offset = (page - 1) * per_page
        items = list(session.exec(
            base_query.order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .offset(offset)
            .limit(per_page)
        ).all())

        # Editorial Highlights (AnimeCorner-style Featured Hero & Editor's Picks on Page 1)
        featured_item = None
        editor_picks = []
        if page == 1:
            feat_query = select(AnimeDraft).where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.is_featured == True,
                AnimeDraft.published_at.is_not(None),
                AnimeDraft.source_url.is_not(None),
            )
            if not is_archive:
                feat_query = feat_query.where(AnimeDraft.published_at >= cutoff_30d)
            featured_item = session.exec(feat_query.order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())).first()

            if not featured_item and items:
                featured_item = items[0]

            ed_query = select(AnimeDraft).where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.is_editor_pick == True,
                AnimeDraft.published_at.is_not(None),
                AnimeDraft.source_url.is_not(None),
            )
            if not is_archive:
                ed_query = ed_query.where(AnimeDraft.published_at >= cutoff_30d)
            if featured_item:
                ed_query = ed_query.where(AnimeDraft.id != featured_item.id)
            editor_picks = list(session.exec(ed_query.order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc()).limit(4)).all())

            if len(editor_picks) < 3:
                excluded = {featured_item.id} if featured_item else set()
                excluded.update(e.id for e in editor_picks)
                fill_query = select(AnimeDraft).where(
                    AnimeDraft.status == "published",
                    AnimeDraft.is_stale == False,
                    AnimeDraft.published_at.is_not(None),
                    AnimeDraft.source_url.is_not(None),
                    AnimeDraft.id.notin_(excluded),
                )
                if not is_archive:
                    fill_query = fill_query.where(AnimeDraft.published_at >= cutoff_30d)
                fill_items = list(session.exec(fill_query.order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc()).limit(3 - len(editor_picks))).all())
                editor_picks.extend(fill_items)

        if caching:
            _NEWS_CACHE[cache_key] = (items, total_count, featured_item, editor_picks, now + HUB_CACHE_TTL)

    total_pages = max(1, (total_count + per_page - 1) // per_page)

    return render(
        "news.html",
        categories=categories,
        items=items,
        featured_item=featured_item,
        editor_picks=editor_picks,
        news_categories=NEWS_CATEGORIES,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        current_news_category=category,
        is_archive=is_archive,
        lang=lang,
        user=user,
        active_nav="news",
        request=request,
    )


def compute_schedule_page_data(session: Session, season: Optional[str] = None, year: Optional[int] = None) -> dict[str, Any]:
    """Fetch and aggregate live weekly airing schedule and season premieres."""
    schedule_data = fetch_live_weekly_schedule(season=season, year=year)
    season_info = schedule_data.get("season_info") or get_season_cycle()
    weekly_items = list(schedule_data.get("weekly_items") or [])
    premiering_items = schedule_data.get("premiering_items") or []
    today_idx = schedule_data.get("today_idx", 0)

    # Query active non-stale database schedule drafts and merge
    base_query = select(AnimeDraft).where(
        AnimeDraft.status == "published",
        AnimeDraft.is_stale == False,
        AnimeDraft.content_type == "schedule",
    )
    db_items = list(session.exec(
        base_query.order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
    ).all())

    seen_titles = {str(item.get("title_en", "")).lower() for item in weekly_items}
    for db_item in db_items:
        title_en = db_item.title_en or ""
        if title_en and title_en.lower() not in seen_titles:
            airing_ts = int(db_item.published_at.timestamp()) if db_item.published_at else 0
            if db_item.published_at:
                dt_ist = db_item.published_at.astimezone(IST_TZ) if db_item.published_at.tzinfo else db_item.published_at.replace(tzinfo=timezone.utc).astimezone(IST_TZ)
            else:
                dt_ist = datetime.now(IST_TZ)
            ep_num = extract_episode(db_item.external_id) or extract_episode(db_item.title_en)
            has_hindi = bool(getattr(db_item, "hindi_dub", False))
            dub_links = []
            if getattr(db_item, "dub_platform", None):
                p_name = db_item.dub_platform
                p_cls = PLATFORM_CLASSES.get(p_name, {"name": p_name, "short": p_name[:2].upper(), "class": "bg-indigo-500/20 text-indigo-400 border-indigo-500/30"})
                dub_links.append({
                    "name": p_cls["name"],
                    "short": p_cls["short"],
                    "url": getattr(db_item, "dub_source_url", "") or "#",
                    "class": p_cls["class"],
                })

            item_dict = {
                "id": db_item.id,
                "media_id": db_item.anilist_id or db_item.id,
                "mal_id": db_item.mal_id,
                "episode": ep_num,
                "airing_at": airing_ts,
                "airing_at_iso": db_item.published_at.isoformat() if db_item.published_at else "",
                "airing_ist_weekday": dt_ist.weekday(),
                "airing_ist_time": db_item.airing_time or dt_ist.strftime("%I:%M %p IST").lstrip("0"),
                "title_en": title_en,
                "title_romaji": db_item.title_en,
                "title_hi": db_item.title_hi or title_en,
                "image_url": db_item.image_url,
                "studio": normalize_studio_name("Official Studio"),
                "source": "MANGA",
                "source_display_hi": "मंगा",
                "score": 85,
                "score_display": "8.5",
                "score_source": "AniList",
                "popularity": 100000,
                "popularity_display": "100.0k",
                "members": 100000,
                "members_display": "100.0k",
                "streaming_links": dub_links,
                "has_verified_hindi_dub": has_hindi,
                "summary_en": db_item.summary_en or "",
                "summary_hi": db_item.summary_hi or db_item.summary_en or "",
                "source_url": db_item.source_url or "",
                "status": "RELEASING",
                "format": "TV",
                "is_kids_or_short": False,
            }
            weekly_items.insert(0, item_dict)
            seen_titles.add(title_en.lower())

    DAY_NAMES_HI = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]
    for it in weekly_items:
        w = it.get("airing_ist_weekday")
        if w is not None and 0 <= w <= 6 and not it.get("day_name_hi"):
            it["day_name_hi"] = DAY_NAMES_HI[w]
        if "is_kids_or_short" not in it:
            it["is_kids_or_short"] = False

    # Sort all weekly items by AniList popularity (descending)
    weekly_items.sort(key=lambda x: (-int(x.get("popularity") or 0), int(x.get("airing_at") or 0)))

    return {
        "weekly_items": weekly_items,
        "season_info": season_info,
        "premiering_items": premiering_items,
        "today_idx": today_idx,
    }


def get_schedule_page_data(session: Session, season: Optional[str] = None, year: Optional[int] = None) -> dict[str, Any]:
    """Stale-While-Revalidate Schedule page data getter."""
    global _SCHEDULE_CACHE
    caching = is_caching_enabled()
    if not caching:
        return compute_schedule_page_data(session, season, year)

    now = time.monotonic()
    cache_key = (season.upper() if season else None, int(year) if year else None)
    cached = _SCHEDULE_CACHE.get(cache_key)

    if cached:
        # 1. Fresh hit (< SWR_FRESH_TTL)
        if now < cached["fresh_until"]:
            return cached["data"]

        # 2. Stale hit (< SWR_STALE_TTL): Serve immediately, refresh in background
        if now < cached["stale_until"]:
            if not cached.get("is_refreshing"):
                cached["is_refreshing"] = True

                def _refresh_sched(bg_s: Session):
                    global _SCHEDULE_CACHE
                    try:
                        fresh = compute_schedule_page_data(bg_s, season, year)
                        _SCHEDULE_CACHE[cache_key] = {
                            "data": fresh,
                            "fresh_until": time.monotonic() + SWR_FRESH_TTL,
                            "stale_until": time.monotonic() + SWR_STALE_TTL,
                            "is_refreshing": False,
                        }
                    except Exception:
                        if cache_key in _SCHEDULE_CACHE:
                            _SCHEDULE_CACHE[cache_key]["is_refreshing"] = False

                _revalidate_async(_refresh_sched)
            return cached["data"]

    # 3. Cold miss
    data = compute_schedule_page_data(session, season, year)
    _SCHEDULE_CACHE[cache_key] = {
        "data": data,
        "fresh_until": now + SWR_FRESH_TTL,
        "stale_until": now + SWR_STALE_TTL,
        "is_refreshing": False,
    }
    return data


@router.get("/schedule", response_class=HTMLResponse)
def schedule_page(
    request: Request,
    season: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(60, ge=1, le=150),
    session: Session = Depends(get_session),
):
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    lang = request.cookies.get("site_lang", "hi")

    data = get_schedule_page_data(session, season, year)
    weekly_items = data["weekly_items"]
    season_info = data["season_info"]
    premiering_items = data["premiering_items"]
    today_idx = data["today_idx"]

    # Initial render: show top 24 cards for today (MAL defaults: hide shorts/kids initially)
    # This keeps the initial HTML payload ~200 KB (well under the 300 KB budget)
    initial_items = [
        it for it in weekly_items
        if it.get("airing_ist_weekday") == today_idx
        and not it.get("is_kids_or_short")
    ][:24]
    if not initial_items:
        initial_items = [it for it in weekly_items if it.get("airing_ist_weekday") == today_idx][:24]

    # Compact dataset for instant client-side tab switching and 'Load More'
    all_cards_compact = []
    for it in weekly_items:
        all_cards_compact.append({
            "id": it.get("id"),
            "t": it.get("title_en"),
            "th": it.get("title_hi") or "",
            "img": it.get("image_url") or "",
            "ep": it.get("episode"),
            "sc": it.get("score_display") or "",
            "hi": bool(it.get("has_verified_hindi_dub")),
            "ts": it.get("airing_at") or 0,
            "d": it.get("day_name_hi") or "",
            "tm": it.get("airing_ist_time") or "",
            "w": it.get("airing_ist_weekday") if it.get("airing_ist_weekday") is not None else -1,
            "f": it.get("format") or "TV",
            "ks": bool(it.get("is_kids_or_short")),
        })
    schedule_json = json.dumps(all_cards_compact, ensure_ascii=False)

    total_count = len(weekly_items)
    offset = (page - 1) * per_page
    paged_items = weekly_items[offset : offset + per_page]
    total_pages = max(1, (total_count + per_page - 1) // per_page)

    return render(
        "schedule.html",
        categories=categories,
        items=initial_items,
        schedule_json=schedule_json,
        season_info=season_info,
        premiering_items=premiering_items,
        today_idx=today_idx,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        lang=lang,
        user=user,
        active_nav="schedule",
        request=request,
    )


def compute_manga_page_data(session: Session) -> dict[str, Any]:
    """Fetch and aggregate published manga updates, links, and ongoing/completed groups."""
    items = list(session.exec(
        select(AnimeDraft)
        .where(
            AnimeDraft.status == "published",
            AnimeDraft.is_stale == False,
            AnimeDraft.content_type == "manga",
            AnimeDraft.published_at.is_not(None)
        )
        .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
    ).all())

    articles = list(session.exec(select(Article)).all())
    anime_articles_map = {}
    manga_links_map = {}
    for m in items:
        if m.id:
            art = _match_anime_article_for_manga(m, articles)
            if art:
                anime_articles_map[m.id] = art
            try:
                manga_links_map[m.id] = json.loads(m.official_links) if m.official_links else []
            except Exception:
                manga_links_map[m.id] = []

    now_utc = datetime.now(timezone.utc)
    cutoff_7d = now_utc - timedelta(days=7)

    this_week_chapters = [
        m for m in items
        if (m.publication_status or "").lower() in ("ongoing", "releasing")
        and m.published_at and _ensure_utc(m.published_at) >= cutoff_7d
    ]
    ongoing_series = [m for m in items if (m.publication_status or "").lower() in ("ongoing", "releasing")]
    completed_series = [m for m in items if (m.publication_status or "").lower() in ("completed", "finished")]

    new_volumes = sorted(
        [m for m in items if m.next_volume_date and _ensure_utc(m.next_volume_date) > now_utc],
        key=lambda x: _ensure_utc(x.next_volume_date),
    )

    return {
        "items": items,
        "this_week_chapters": this_week_chapters,
        "ongoing_series": ongoing_series,
        "completed_series": completed_series,
        "new_volumes": new_volumes,
        "anime_articles_map": anime_articles_map,
        "manga_links_map": manga_links_map,
        "now_utc": now_utc,
    }


def get_manga_page_data(session: Session) -> dict[str, Any]:
    """Stale-While-Revalidate Manga page data getter."""
    global _MANGA_CACHE
    caching = is_caching_enabled()
    if not caching:
        return compute_manga_page_data(session)

    now = time.monotonic()
    if _MANGA_CACHE:
        # 1. Fresh hit
        if now < _MANGA_CACHE["fresh_until"]:
            return _MANGA_CACHE["data"]

        # 2. Stale hit: Return immediately, refresh in background
        if now < _MANGA_CACHE["stale_until"]:
            if not _MANGA_CACHE.get("is_refreshing"):
                _MANGA_CACHE["is_refreshing"] = True

                def _refresh_manga(bg_s: Session):
                    global _MANGA_CACHE
                    try:
                        fresh = compute_manga_page_data(bg_s)
                        _MANGA_CACHE = {
                            "data": fresh,
                            "fresh_until": time.monotonic() + SWR_FRESH_TTL,
                            "stale_until": time.monotonic() + SWR_STALE_TTL,
                            "is_refreshing": False,
                        }
                    except Exception:
                        if _MANGA_CACHE:
                            _MANGA_CACHE["is_refreshing"] = False

                _revalidate_async(_refresh_manga)
            return _MANGA_CACHE["data"]

    # 3. Cold miss
    data = compute_manga_page_data(session)
    _MANGA_CACHE = {
        "data": data,
        "fresh_until": now + SWR_FRESH_TTL,
        "stale_until": now + SWR_STALE_TTL,
        "is_refreshing": False,
    }
    return data


@router.get("/manga", response_class=HTMLResponse)
def manga_page(
    request: Request,
    filter: str = Query("all"),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=1, le=100),
    session: Session = Depends(get_session),
):
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    lang = request.cookies.get("site_lang", "hi")

    data = get_manga_page_data(session)
    all_items = data["items"]
    this_week_chapters = data["this_week_chapters"]
    ongoing_series = data["ongoing_series"]
    completed_series = data["completed_series"]
    new_volumes = data["new_volumes"]
    anime_articles_map = data["anime_articles_map"]
    manga_links_map = data["manga_links_map"]
    now_utc = data["now_utc"]

    # Filter selection
    filter_norm = (filter or "all").lower().strip()
    if filter_norm in ("ongoing", "releasing"):
        target_items = ongoing_series
    elif filter_norm in ("completed", "finished"):
        target_items = completed_series
    elif filter_norm in ("this_week", "this-week", "new"):
        target_items = this_week_chapters
    else:
        filter_norm = "all"
        target_items = all_items

    # Text Search Filter
    search_query = (q or "").strip()
    if search_query:
        sq_lower = search_query.lower()
        target_items = [
            m for m in target_items
            if sq_lower in (m.title_en or "").lower()
            or sq_lower in (m.title_hi or "").lower()
            or sq_lower in (m.linked_anime_title or "").lower()
        ]

    # Pagination
    total_count = len(target_items)
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    offset = (page - 1) * per_page
    paged_items = target_items[offset : offset + per_page]

    return render(
        "manga.html",
        categories=categories,
        items=paged_items,
        all_items_count=len(all_items),
        total_count=total_count,
        page=page,
        total_pages=total_pages,
        current_filter=filter_norm,
        search_query=search_query,
        this_week_chapters=this_week_chapters,
        ongoing_series=ongoing_series,
        completed_series=completed_series,
        new_volumes=new_volumes,
        anime_articles_map=anime_articles_map,
        manga_links_map=manga_links_map,
        lang=lang,
        user=user,
        now_utc=now_utc,
        active_nav="manga",
        request=request,
    )


@router.get("/manga/{item_id}", response_class=HTMLResponse)
def manga_detail_page(
    request: Request,
    item_id: str,
    session: Session = Depends(get_session),
):
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    lang = request.cookies.get("site_lang", "hi")

    manga = None
    if item_id.isdigit():
        m_id = int(item_id)
        manga = session.exec(
            select(AnimeDraft).where(
                AnimeDraft.id == m_id,
                AnimeDraft.content_type == "manga",
            )
        ).first()
        if not manga:
            manga = session.exec(
                select(AnimeDraft).where(
                    AnimeDraft.anilist_id == m_id,
                    AnimeDraft.content_type == "manga",
                )
            ).first()
    else:
        manga = session.exec(
            select(AnimeDraft).where(
                AnimeDraft.external_id == item_id,
                AnimeDraft.content_type == "manga",
            )
        ).first()

    is_admin = bool(user and getattr(user, "is_admin", False))
    if not manga or (manga.status != "published" and not is_admin) or manga.is_stale:
        raise HTTPException(status_code=404, detail="मंगा नहीं मिला / Manga not found")

    try:
        official_links = json.loads(manga.official_links) if manga.official_links else []
    except Exception:
        official_links = []

    articles = list(session.exec(select(Article)).all())
    anime_art = _match_anime_article_for_manga(manga, articles)

    return render(
        "manga_detail.html",
        categories=categories,
        manga=manga,
        official_links=official_links,
        anime_art=anime_art,
        lang=lang,
        user=user,
        active_nav="manga",
        request=request,
    )


def compute_awards_page_data(session: Session, year: Optional[int] = None) -> dict[str, Any]:
    """Fetch and aggregate verified award ceremonies, winner entries, and edition archives."""
    available_years = list(session.exec(
        select(AwardCeremony.edition_year)
        .where(AwardCeremony.status == "verified")
        .distinct()
        .order_by(AwardCeremony.edition_year.desc())
    ).all())

    latest_year = available_years[0] if available_years else None
    if not year or year not in available_years:
        selected_year = latest_year
    else:
        selected_year = year

    is_latest_edition = bool(latest_year and selected_year == latest_year)

    ceremonies_data = []
    hero_entry = None
    hero_ceremony = None
    hero_nominees = []
    article_links_map = {}

    if selected_year:
        ceremonies = session.exec(
            select(AwardCeremony)
            .where(
                AwardCeremony.edition_year == selected_year,
                AwardCeremony.status == "verified",
            )
            .order_by(AwardCeremony.id)
        ).all()

        for c in ceremonies:
            entries = session.exec(
                select(AwardEntry)
                .where(
                    AwardEntry.ceremony_id == c.id,
                    AwardEntry.status == "verified",
                )
                .order_by(AwardEntry.id)
            ).all()

            entries_with_nominees = []
            for e in entries:
                try:
                    nominees = json.loads(e.nominee_media_ids) if e.nominee_media_ids else []
                except Exception:
                    nominees = []
                entries_with_nominees.append((e, nominees))

                if e.winner_title and e.id not in article_links_map:
                    matched_art = session.exec(
                        select(Article).where(
                            or_(
                                Article.anime_title_en.ilike(f"%{e.winner_title}%"),
                                Article.title.ilike(f"%{e.winner_title}%"),
                            )
                        )
                    ).first()
                    if matched_art:
                        article_links_map[e.id] = matched_art.slug

            ceremonies_data.append({
                "ceremony": c,
                "entries": entries_with_nominees,
            })

            # Task 4: Anything older than the latest edition goes to the archive, never the hero
            if is_latest_edition and not hero_entry and "crunchyroll" in c.name.lower():
                for e in entries:
                    if "anime of the year" in e.category.lower() or "grand prize" in e.category.lower():
                        hero_entry = e
                        hero_ceremony = c
                        try:
                            hero_nominees = json.loads(e.nominee_media_ids) if e.nominee_media_ids else []
                        except Exception:
                            hero_nominees = []
                        break

        if is_latest_edition and ceremonies_data and not hero_entry:
            for c_block in ceremonies_data:
                for (e, noms) in c_block["entries"]:
                    cat_lower = e.category.lower()
                    if "anime of the year" in cat_lower or "grand prize" in cat_lower:
                        hero_entry = e
                        hero_ceremony = c_block["ceremony"]
                        hero_nominees = noms
                        break
                if hero_entry:
                    break
            if not hero_entry and ceremonies_data[0]["entries"]:
                hero_entry, hero_nominees = ceremonies_data[0]["entries"][0]
                hero_ceremony = ceremonies_data[0]["ceremony"]

    # Archive summary for older years
    archive_years = []
    for y in available_years:
        if y != latest_year:
            c_count = len(session.exec(
                select(AwardCeremony.id).where(
                    AwardCeremony.edition_year == y,
                    AwardCeremony.status == "verified",
                )
            ).all())
            e_count = len(session.exec(
                select(AwardEntry.id)
                .join(AwardCeremony)
                .where(
                    AwardCeremony.edition_year == y,
                    AwardCeremony.status == "verified",
                    AwardEntry.status == "verified",
                )
            ).all())
            archive_years.append({
                "year": y,
                "ceremonies_count": c_count,
                "entries_count": e_count,
            })

    return {
        "ceremonies_data": ceremonies_data,
        "hero_entry": hero_entry,
        "hero_ceremony": hero_ceremony,
        "hero_nominees": hero_nominees,
        "archive_years": archive_years,
        "article_links_map": article_links_map,
        "available_years": available_years,
        "selected_year": selected_year,
        "latest_year": latest_year,
        "is_latest_edition": is_latest_edition,
    }


def get_awards_page_data(session: Session, year: Optional[int] = None) -> dict[str, Any]:
    """Stale-While-Revalidate Awards page data getter."""
    global _AWARDS_CACHE
    caching = is_caching_enabled()
    if not caching:
        return compute_awards_page_data(session, year)

    now = time.monotonic()
    cache_key = year or 0
    cached = _AWARDS_CACHE.get(cache_key)

    if cached:
        # 1. Fresh hit
        if now < cached["fresh_until"]:
            return cached["data"]

        # 2. Stale hit: Return immediately, refresh in background
        if now < cached["stale_until"]:
            if not cached.get("is_refreshing"):
                cached["is_refreshing"] = True

                def _refresh_awards(bg_s: Session):
                    global _AWARDS_CACHE
                    try:
                        fresh = compute_awards_page_data(bg_s, year)
                        _AWARDS_CACHE[cache_key] = {
                            "data": fresh,
                            "fresh_until": time.monotonic() + SWR_FRESH_TTL,
                            "stale_until": time.monotonic() + SWR_STALE_TTL,
                            "is_refreshing": False,
                        }
                    except Exception:
                        if cache_key in _AWARDS_CACHE:
                            _AWARDS_CACHE[cache_key]["is_refreshing"] = False

                _revalidate_async(_refresh_awards)
            return cached["data"]

    # 3. Cold miss
    data = compute_awards_page_data(session, year)
    _AWARDS_CACHE[cache_key] = {
        "data": data,
        "fresh_until": now + SWR_FRESH_TTL,
        "stale_until": now + SWR_STALE_TTL,
        "is_refreshing": False,
    }
    return data


@router.get("/awards", response_class=HTMLResponse)
def awards_page(
    request: Request,
    year: Optional[int] = Query(None),
    session: Session = Depends(get_session),
):
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    lang = request.cookies.get("site_lang", "hi")

    data = get_awards_page_data(session, year)
    available_years = data["available_years"]
    selected_year = data["selected_year"]
    latest_year = data["latest_year"]
    is_latest_edition = data["is_latest_edition"]
    ceremonies_data = data["ceremonies_data"]
    hero_entry = data["hero_entry"]
    hero_ceremony = data["hero_ceremony"]
    hero_nominees = data["hero_nominees"]
    archive_years = data["archive_years"]
    article_links_map = data["article_links_map"]

    return render(
        "awards.html",
        categories=categories,
        available_years=available_years,
        selected_year=selected_year,
        latest_year=latest_year,
        is_latest_edition=is_latest_edition,
        ceremonies_data=ceremonies_data,
        hero_entry=hero_entry,
        hero_ceremony=hero_ceremony,
        hero_nominees=hero_nominees,
        archive_years=archive_years,
        article_links_map=article_links_map,
        lang=lang,
        user=user,
        active_nav="awards",
        request=request,
    )


def prewarm_public_pages() -> dict[str, Any]:
    """Pre-warm in-memory caches for Home, Schedule, Manga, and Awards pages.
    Guarantees 0ms cold wait for incoming visitors across all Gunicorn workers.
    """
    from ..db import engine
    stats = {}
    try:
        with Session(engine) as s:
            get_categories(s)
            get_platforms(s)
            h = get_home_page_data(s)
            stats["home_articles"] = len(h.get("articles", []))
            sc = get_schedule_page_data(s, None, None)
            stats["schedule_items"] = len(sc.get("weekly_items", []))
            m = get_manga_page_data(s)
            stats["manga_items"] = len(m.get("items", []))
            a = get_awards_page_data(s, None)
            stats["awards_ceremonies"] = len(a.get("ceremonies_data", []))
    except Exception as e:
        logger.debug(f"Prewarm skipped (e.g. database not initialized yet): {e}")
    return stats


@router.get("/trailers", response_class=HTMLResponse)
def trailers_page(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(12, ge=1, le=50),
    season: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    session: Session = Depends(get_session),
):
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    lang = request.cookies.get("site_lang", "hi")
    cycle = get_season_cycle()

    target_season = (season or cycle["current"]["season"]).upper() if season != "all" else "all"
    target_year = int(year or cycle["current"]["year"])

    caching = is_caching_enabled()
    now = time.monotonic()
    cache_key = (page, per_page, target_season, target_year)
    cached = _TRAILERS_CACHE.get(cache_key) if caching else None
    if cached and cached[2] > now:
        items, total_count = cached[0], cached[1]
    else:
        # 1. Fetch published trailers from DB
        db_query = select(AnimeDraft).where(
            AnimeDraft.status == "published",
            AnimeDraft.is_stale == False,
            AnimeDraft.published_at.is_not(None),
            AnimeDraft.source_url.is_not(None),
            AnimeDraft.source_url != "",
            or_(
                AnimeDraft.content_type == "trailer",
                AnimeDraft.news_category == "Trailer",
            ),
        )
        if target_season != "all":
            db_query = db_query.where(
                or_(
                    AnimeDraft.season == target_season,
                    AnimeDraft.season.is_(None),
                )
            )
        db_trailers = list(session.exec(db_query.order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())).all())

        # 2. Fetch live trailers from AniList & official YouTube channel feeds
        live_trailers = fetch_live_trailers(
            season=target_season if target_season != "all" else None,
            year=target_year if target_season != "all" else None,
        )

        # 3. Merge & Deduplicate by YouTube ID
        all_trailers = []
        seen_yt_ids = set()

        for item in db_trailers:
            yt_id = extract_youtube_id(item.source_url or "") or extract_youtube_id(item.external_id or "")
            if yt_id:
                seen_yt_ids.add(yt_id)
            all_trailers.append(item)

        for live in live_trailers:
            yt_id = live.get("youtube_id")
            if yt_id and yt_id not in seen_yt_ids:
                seen_yt_ids.add(yt_id)
                all_trailers.append(live)

        # Sort strictly newest first by published_at
        def _get_pub(x):
            dt = getattr(x, "published_at", None) or (x.get("published_at") if isinstance(x, dict) else None)
            if dt:
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            return datetime.min.replace(tzinfo=timezone.utc)

        all_trailers.sort(key=_get_pub, reverse=True)

        total_count = len(all_trailers)
        offset = (page - 1) * per_page
        items = all_trailers[offset : offset + per_page]

        if caching:
            _TRAILERS_CACHE[cache_key] = (items, total_count, now + HUB_CACHE_TTL)

    total_pages = max(1, (total_count + per_page - 1) // per_page)

    return render(
        "trailers.html",
        categories=categories,
        items=items,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        season_cycle=cycle,
        current_season=target_season,
        selected_year=target_year,
        lang=lang,
        user=user,
        active_nav="trailers",
        request=request,
    )


@router.get("/updates/{item_id}", response_class=HTMLResponse)
@router.get("/news/{item_id}", response_class=HTMLResponse)
@router.get("/anime/{item_id}", response_class=HTMLResponse)
def update_detail_page(
    request: Request,
    item_id: str,
    session: Session = Depends(get_session),
):
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    lang = request.cookies.get("site_lang", "hi")

    item = None
    # 1. Try DB integer ID
    if item_id.isdigit():
        item = session.get(AnimeDraft, int(item_id))
        if item and item.status != "published":
            is_admin = user and (user.get("user_type") == "admin" or user.get("is_superuser"))
            if not is_admin:
                raise HTTPException(status_code=404, detail="Update is not published")

    # 2. Try DB external_id (only if published or admin)
    if not item:
        db_item = session.exec(select(AnimeDraft).where(AnimeDraft.external_id == item_id)).first()
        if db_item:
            if db_item.status == "published":
                item = db_item
            else:
                is_admin = user and (user.get("user_type") == "admin" or user.get("is_superuser"))
                if is_admin:
                    item = db_item

    # 3. If not resolved from DB, resolve from in-memory live schedule or AniList API
    if not item:
        from automation.fetchers.anilist import find_schedule_item_by_id, fetch_anilist_media_by_id
        media_id = None
        m = re.search(r'(?:anilist_)?(\d+)(?:_ep\d+)?', item_id)
        if m:
            media_id = int(m.group(1))

        # Check in-memory live schedule cache first (0ms, 0 external network calls)
        live_item = find_schedule_item_by_id(item_id, media_id=media_id)
        if not live_item and media_id:
            live_item = fetch_anilist_media_by_id(media_id)

        if live_item:
            class ItemWrapper:
                def __init__(self, d):
                    self._d = d
                    for k, v in d.items():
                        setattr(self, k, v)
                def __getitem__(self, key):
                    return self._d.get(key)
                def get(self, key, default=None):
                    return self._d.get(key, default)
            item = ItemWrapper(live_item)

    if not item:
        raise HTTPException(status_code=404, detail="Anime update not found")

    item_db_id = getattr(item, "id", None)
    if isinstance(item_db_id, int):
        related_items = session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.id != item_db_id
            )
            .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .limit(4)
        ).all()
    else:
        related_items = session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False
            )
            .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .limit(4)
        ).all()

    return render(
        "update_detail.html",
        item=item,
        related_items=related_items,
        categories=categories,
        lang=lang,
        user=user,
        request=request,
    )


# ---------- LEGAL / INFO PAGES ----------
# Last-updated dates keyed by route slug.
# To update a page's date: change only the matching entry here.
_LEGAL_DATES = {
    "privacy":               ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "terms":                 ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "cookies":               ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "community-guidelines":  ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "copyright":             ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "streaming-disclosure":  ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "disclaimer":            ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "about":                 ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "editorial-policy":      ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "contact":               ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
}

# Sidebar-style related-links shown at bottom of each legal page.
_RELATED_LINKS = [
    {"url": "/privacy",              "label_hi": "गोपनीयता नीति",       "label_en": "Privacy Policy"},
    {"url": "/terms",                "label_hi": "उपयोग की शर्तें",     "label_en": "Terms of Use"},
    {"url": "/cookies",              "label_hi": "कुकी नीति",           "label_en": "Cookie Policy"},
    {"url": "/community-guidelines", "label_hi": "सामुदायिक दिशानिर्देश", "label_en": "Community Guidelines"},
    {"url": "/copyright",            "label_hi": "कॉपीराइट",           "label_en": "Copyright"},
    {"url": "/streaming-disclosure", "label_hi": "Streaming प्रकटीकरण", "label_en": "Streaming Disclosure"},
    {"url": "/disclaimer",           "label_hi": "अस्वीकरण",           "label_en": "Disclaimer"},
    {"url": "/about",                "label_hi": "हमारे बारे में",        "label_en": "About Us"},
    {"url": "/editorial-policy",     "label_hi": "संपादकीय नीति",        "label_en": "Editorial Policy"},
    {"url": "/contact",              "label_hi": "संपर्क",             "label_en": "Contact Us"},
]


def _legal_ctx(
    request: Request,
    session: Session,
    slug: str,
    page_title_hi: str,
    page_title_en: str,
    toc: list = None,
) -> dict:
    """Build common context dict for a legal page."""
    lang = request.cookies.get("site_lang", "hi")
    date_entry = _LEGAL_DATES.get(slug, ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"))
    last_updated_hi = date_entry[0]
    last_updated_en = date_entry[1]
    last_updated_iso = date_entry[2]
    page_title = page_title_hi if lang == "hi" else page_title_en
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    related = [lnk for lnk in _RELATED_LINKS if lnk["url"] != f"/{slug}"]
    return dict(
        page_title=page_title,
        page_title_hi=page_title_hi,
        page_title_en=page_title_en,
        lang=lang,
        last_updated=last_updated_hi,
        last_updated_hi=last_updated_hi,
        last_updated_en=last_updated_en,
        last_updated_iso=last_updated_iso,
        related_links=related,
        categories=categories,
        user=user,
        request=request,
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "data-collected",   "title": "डेटा संग्रह" if lang=="hi" else "Data Collected"},
        {"id": "purpose",          "title": "उद्देश्य" if lang=="hi" else "Purpose"},
        {"id": "cookies-section",  "title": "कुकीज़" if lang=="hi" else "Cookies"},
        {"id": "sharing",          "title": "साझाकरण" if lang=="hi" else "Sharing"},
        {"id": "retention",        "title": "संधारण" if lang=="hi" else "Retention"},
        {"id": "rights",           "title": "आपके अधिकार" if lang=="hi" else "Your Rights"},
        {"id": "children",         "title": "बच्चों की गोपनीयता" if lang=="hi" else "Children"},
        {"id": "changes",          "title": "परिवर्तन" if lang=="hi" else "Changes"},
        {"id": "contact-priv",     "title": "संपर्क" if lang=="hi" else "Contact"},
    ]
    ctx = _legal_ctx(request, session, "privacy",
                     "गोपनीयता नीति", "Privacy Policy", toc)
    return render("legal/privacy.html", **ctx)


@router.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "acceptance",   "title": "स्वीकृति" if lang=="hi" else "Acceptance"},
        {"id": "account",      "title": "खाता" if lang=="hi" else "Account"},
        {"id": "comments",     "title": "टिप्पणियाँ" if lang=="hi" else "Comments"},
        {"id": "prohibited",   "title": "निषिद्ध" if lang=="hi" else "Prohibited"},
        {"id": "suspension",   "title": "निलंबन" if lang=="hi" else "Suspension"},
        {"id": "streaming",    "title": "Streaming" if lang=="hi" else "Streaming"},
        {"id": "liability",    "title": "दायित्व" if lang=="hi" else "Liability"},
        {"id": "law",          "title": "शासकीय कानून" if lang=="hi" else "Governing Law"},
    ]
    ctx = _legal_ctx(request, session, "terms",
                     "उपयोग की शर्तें", "Terms of Use", toc)
    return render("legal/terms.html", **ctx)


@router.get("/cookies", response_class=HTMLResponse)
def cookies_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "what-are-cookies", "title": "कुकीज़ क्या हैं" if lang=="hi" else "What Are Cookies"},
        {"id": "our-cookies",      "title": "हमारी कुकीज़" if lang=="hi" else "Our Cookies"},
        {"id": "control",          "title": "नियंत्रण" if lang=="hi" else "Control"},
    ]
    ctx = _legal_ctx(request, session, "cookies",
                     "कुकी नीति", "Cookie Policy", toc)
    return render("legal/cookies.html", **ctx)


@router.get("/community-guidelines", response_class=HTMLResponse)
def community_guidelines_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "purpose-cg",   "title": "उद्देश्य" if lang=="hi" else "Purpose"},
        {"id": "allowed",      "title": "प्रोत्साहित" if lang=="hi" else "Encouraged"},
        {"id": "prohibited-cg","title": "निषिद्ध" if lang=="hi" else "Prohibited"},
        {"id": "reporting",    "title": "रिपोर्ट" if lang=="hi" else "Reporting"},
        {"id": "moderation",   "title": "Moderation" if lang=="hi" else "Moderation"},
    ]
    ctx = _legal_ctx(request, session, "community-guidelines",
                     "सामुदायिक दिशानिर्देश", "Community Guidelines", toc)
    return render("legal/community-guidelines.html", **ctx)


@router.get("/copyright", response_class=HTMLResponse)
def copyright_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "scope",         "title": "दायरा" if lang=="hi" else "Scope"},
        {"id": "site-content",  "title": "साइट सामग्री" if lang=="hi" else "Site Content"},
        {"id": "takedown",      "title": "Takedown" if lang=="hi" else "Takedown"},
        {"id": "counter-notice","title": "Counter-Notice" if lang=="hi" else "Counter-Notice"},
    ]
    ctx = _legal_ctx(request, session, "copyright",
                     "कॉपीराइट और Takedown", "Copyright & Takedown", toc)
    return render("legal/copyright.html", **ctx)


@router.get("/streaming-disclosure", response_class=HTMLResponse)
def streaming_disclosure_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "what-we-do",   "title": "हम क्या करते हैं" if lang=="hi" else "What We Do"},
        {"id": "no-piracy",    "title": "पायरेसी नीति" if lang=="hi" else "Piracy Policy"},
        {"id": "platforms",    "title": "Platforms" if lang=="hi" else "Platforms"},
        {"id": "accuracy",     "title": "सटीकता" if lang=="hi" else "Accuracy"},
        {"id": "no-affiliate", "title": "Affiliate" if lang=="hi" else "Affiliate"},
    ]
    ctx = _legal_ctx(request, session, "streaming-disclosure",
                     "Streaming प्रकटीकरण", "Streaming Disclosure", toc)
    return render("legal/streaming-disclosure.html", **ctx)


@router.get("/disclaimer", response_class=HTMLResponse)
def disclaimer_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "what-is-disclaimer", "title": "उद्देश्य" if lang=="hi" else "Purpose"},
        {"id": "informational",      "title": "केवल सूचनात्मक" if lang=="hi" else "Informational Only"},
        {"id": "third-party",        "title": "तृतीय पक्ष" if lang=="hi" else "Third Party"},
        {"id": "accuracy-dis",       "title": "सटीकता" if lang=="hi" else "Accuracy"},
        {"id": "copyright-dis",      "title": "Anime ट्रेडमार्क" if lang=="hi" else "Anime Trademarks"},
        {"id": "liability-dis",      "title": "दायित्व" if lang=="hi" else "Liability"},
    ]
    ctx = _legal_ctx(request, session, "disclaimer",
                     "अस्वीकरण", "Disclaimer", toc)
    return render("legal/disclaimer.html", **ctx)


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "who-we-are",    "title": "हम कौन हैं" if lang=="hi" else "Who We Are"},
        {"id": "mission",       "title": "मिशन" if lang=="hi" else "Mission"},
        {"id": "team",          "title": "टीम" if lang=="hi" else "Team"},
        {"id": "contact-about", "title": "संपर्क" if lang=="hi" else "Contact"},
    ]
    ctx = _legal_ctx(request, session, "about",
                     "हमारे बारे में", "About Us", toc)
    return render("legal/about.html", **ctx)


@router.get("/editorial-policy", response_class=HTMLResponse)
def editorial_policy_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "purpose-ep",  "title": "उद्देश्य" if lang=="hi" else "Purpose"},
        {"id": "sourcing",    "title": "स्रोत" if lang=="hi" else "Sourcing"},
        {"id": "corrections", "title": "सुधार" if lang=="hi" else "Corrections"},
        {"id": "independence","title": "स्वतंत्रता" if lang=="hi" else "Independence"},
        {"id": "ai-use",      "title": "AI" if lang=="hi" else "AI"},
    ]
    ctx = _legal_ctx(request, session, "editorial-policy",
                     "संपादकीय नीति", "Editorial Policy", toc)
    return render("legal/editorial-policy.html", **ctx)


@router.get("/contact", response_class=HTMLResponse)
def contact_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "contact-intro",      "title": "परिचय" if lang=="hi" else "Intro"},
        {"id": "contact-email",      "title": "ईमेल" if lang=="hi" else "Email"},
        {"id": "contact-categories", "title": "श्रेणियाँ" if lang=="hi" else "Categories"},
        {"id": "response",           "title": "प्रतिक्रिया" if lang=="hi" else "Response"},
    ]
    ctx = _legal_ctx(request, session, "contact",
                     "संपर्क", "Contact", toc)
    return render("legal/contact.html", **ctx)


# ---------- SEO: ROBOTS.TXT & SITEMAP.XML ----------
@router.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt(request: Request):
    from ..config import get_settings
    settings = get_settings()
    base_url = (settings.public_base_url or str(request.base_url)).rstrip("/")
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /settings/",
        "Disallow: /auth/",
        f"Sitemap: {base_url}/sitemap.xml",
    ]
    return PlainTextResponse("\n".join(lines), media_type="text/plain")


@router.get("/sitemap.xml")
def sitemap_xml(request: Request, session: Session = Depends(get_session)):
    from ..config import get_settings
    settings = get_settings()
    base_url = (settings.public_base_url or str(request.base_url)).rstrip("/")
    raw_articles = session.exec(select(Article).where(Article.published_at.is_not(None))).all()
    articles = [a for a in raw_articles if not a.is_sample]
    categories = session.exec(select(Category)).all()

    # Static legal pages added to sitemap
    _LEGAL_SLUGS = [
        "privacy", "terms", "cookies", "community-guidelines",
        "copyright", "streaming-disclosure", "disclaimer",
        "about", "editorial-policy", "contact",
    ]

    urls = [
        f"""  <url>
    <loc>{base_url}/</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>""",
        f"""  <url>
    <loc>{base_url}/explore</loc>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>""",
    ]

    for slug in _LEGAL_SLUGS:
        date_entry = _LEGAL_DATES.get(slug, ("", "", "2026-10-21"))
        iso = date_entry[2]
        urls.append(
            f"""  <url>
    <loc>{base_url}/{slug}</loc>
    <lastmod>{iso}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>"""
        )

    for cat in categories:
        urls.append(
            f"""  <url>
    <loc>{base_url}/category/{cat.slug}</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>"""
        )

    for art in articles:
        lastmod = art.updated_at or art.published_at or art.created_at
        lastmod_str = lastmod.strftime("%Y-%m-%d") if lastmod else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        urls.append(
            f"""  <url>
    <loc>{base_url}/article/{art.slug}</loc>
    <lastmod>{lastmod_str}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>"""
        )

    # Public Automation Hub Pages
    for hub_slug, hub_pri in [
        ("news", "0.9"),
        ("schedule", "0.9"),
        ("manga", "0.7"),
        ("awards", "0.7"),
        ("trailers", "0.7"),
    ]:
        urls.append(
            f"""  <url>
    <loc>{base_url}/{hub_slug}</loc>
    <changefreq>daily</changefreq>
    <priority>{hub_pri}</priority>
  </url>"""
        )

    # Published Anime Updates ONLY (Drafts strictly excluded)
    published_updates = session.exec(
        select(AnimeDraft).where(AnimeDraft.status == "published")
    ).all()
    for upd in published_updates:
        upd_lastmod = upd.published_at or upd.created_at
        upd_lastmod_str = upd_lastmod.strftime("%Y-%m-%d") if upd_lastmod else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        urls.append(
            f"""  <url>
    <loc>{base_url}/updates/{upd.id}</loc>
    <lastmod>{upd_lastmod_str}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>"""
        )

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""

    return Response(content=xml_content, media_type="application/xml")


@router.get("/placeholder.svg", response_class=Response)
def dynamic_placeholder_svg(
    title: Optional[str] = Query("Hindi Anime Guide"),
    category: Optional[str] = Query(None),
    color: Optional[str] = Query(None),
):
    """Generate a clean, high-contrast, theme-consistent SVG placeholder.

    Rule: Never use unrelated anime posters as fallbacks.
    Displays post title and category accent colour.
    """
    import html
    safe_title = html.escape((title or "Hindi Anime Guide").strip())
    if len(safe_title) > 50:
        safe_title = safe_title[:47] + "..."

    cat_colors = {
        "News": "#3b82f6",
        "Announcement": "#3b82f6",
        "Trailer": "#ec4899",
        "Release Date": "#10b981",
        "Schedule": "#8b5cf6",
        "Manga": "#f59e0b",
        "Awards": "#eab308",
        "Award": "#eab308",
        "Movie": "#a855f7",
    }
    accent = color if (color and color.startswith("#")) else cat_colors.get(category or "", "#6366f1")
    cat_label = html.escape((category or "Anime Guide").upper())

    line1 = safe_title[:25]
    line2 = safe_title[25:50] if len(safe_title) > 25 else ""

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 560" width="100%" height="100%">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0f172a" />
      <stop offset="100%" stop-color="#1e1b4b" />
    </linearGradient>
    <linearGradient id="glow" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{accent}" stop-opacity="0.25" />
      <stop offset="100%" stop-color="{accent}" stop-opacity="0.05" />
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#bg)" />
  <rect x="20" y="20" width="360" height="520" rx="16" fill="url(#glow)" stroke="{accent}" stroke-opacity="0.3" stroke-width="2" />

  <!-- Category Badge -->
  <rect x="50" y="50" width="130" height="30" rx="6" fill="{accent}" fill-opacity="0.2" stroke="{accent}" stroke-opacity="0.4" />
  <text x="115" y="70" fill="{accent}" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" text-anchor="middle" letter-spacing="1">{cat_label}</text>

  <!-- Anime Icon -->
  <circle cx="200" cy="220" r="54" fill="{accent}" fill-opacity="0.15" />
  <circle cx="200" cy="220" r="42" fill="{accent}" fill-opacity="0.25" />
  <path d="M190 195 L222 220 L190 245 Z" fill="#ffffff" opacity="0.9" />

  <!-- Title Text -->
  <text x="200" y="330" fill="#f8fafc" font-family="system-ui, -apple-system, sans-serif" font-size="17" font-weight="700" text-anchor="middle">
    <tspan x="200" dy="0">{line1}</tspan>
    {f'<tspan x="200" dy="24">{line2}</tspan>' if line2 else ''}
  </text>

  <text x="200" y="440" fill="#94a3b8" font-family="system-ui, -apple-system, sans-serif" font-size="13" font-weight="500" text-anchor="middle">Hindi Anime Guide</text>
  <text x="200" y="465" fill="#64748b" font-family="system-ui, -apple-system, sans-serif" font-size="11" text-anchor="middle">आधिकारिक एनीमे एवं मंगा गाइड</text>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})



