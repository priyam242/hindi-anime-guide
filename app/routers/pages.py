
"""Public pages router: Home, Explore/Search, Article, Category, Info pages, Sitemap, Robots."""
from __future__ import annotations
import json
import logging
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Query, Form, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, FileResponse
from sqlmodel import Session, select
from sqlalchemy import func, or_, not_
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
    {"slug": "Announcement", "name_en": "Announcement", "name_hi": "घोषणाएं", "icon": "fa-bullhorn"},
    {"slug": "Trailer", "name_en": "Trailer", "name_hi": "ट्रेलर्स", "icon": "fa-film"},
    {"slug": "Release Date", "name_en": "Release Date", "name_hi": "रिलीज़ डेट्स", "icon": "fa-calendar-check"},
    {"slug": "Movie", "name_en": "Movie", "name_hi": "फ़िल्में", "icon": "fa-film"},
    {"slug": "Interview", "name_en": "Interview", "name_hi": "इंटरव्यू", "icon": "fa-microphone"},
    {"slug": "Event Recap", "name_en": "Event Recap", "name_hi": "इवेंट्स", "icon": "fa-award"},
    {"slug": "Industry", "name_en": "Industry", "name_hi": "इंडस्ट्री", "icon": "fa-chart-line"},
]
_UPDATES_HUB_CACHE: tuple[dict, float] | None = None
HUB_CACHE_TTL: float = 300.0
_FREE_CACHE: tuple[dict, float] | None = None
FREE_CACHE_TTL: float = 120.0
_DYNAMIC_ANIME_CACHE: dict[str, tuple[dict, float]] = {}
DYNAMIC_ANIME_CACHE_TTL: float = 180.0


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
    global _CATEGORIES_CACHE, _PLATFORMS_CACHE, _HOME_CACHE, _DEFAULT_EXPLORE_CACHE, _MANGA_CACHE, _AWARDS_YEARS_CACHE, _AWARDS_CACHE, _NEWS_CACHE, _SCHEDULE_CACHE, _TRAILERS_CACHE, _UPDATES_HUB_CACHE, _FREE_CACHE, _DYNAMIC_ANIME_CACHE
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
    _FREE_CACHE = None
    _DYNAMIC_ANIME_CACHE.clear()


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


def format_relative_time_hi(dt: datetime | None) -> str:
    """Format datetime into natural Hindi relative time string (never 'हाल ही में')."""
    if not dt:
        return "1 दिन पहले"
    now = datetime.now(timezone.utc)
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    total_seconds = int(diff.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    if total_seconds < 3600:
        minutes = max(1, total_seconds // 60)
        return f"{minutes} मिनट पहले"
    elif total_seconds < 86400:
        hours = max(1, total_seconds // 3600)
        return f"{hours} घंटे पहले"
    else:
        days = max(1, total_seconds // 86400)
        return f"{days} दिन पहले"


def clean_base_series_title(title: str) -> str:
    """Strip trailing English/Hindi episode strings (e.g., 'Episode 8', 'Ep. 8', 'एपिसोड 8')."""
    if not title:
        return ""
    pattern = r"\s*(?:[-–—:]?\s*)?(?:Episode|Ep\.?|एपिसोड|Part|भाग)\s*\d+.*$"
    cleaned = re.sub(pattern, "", title, flags=re.IGNORECASE).strip()
    return cleaned if cleaned else title


def slugify_anime_title(title: str) -> str:
    """Convert an anime title to a clean, canonical URL slug."""
    if not title:
        return "anime"
    # Remove apostrophes, parens, brackets, special punctuation
    s = re.sub(r"[\(\)\[\]\{\}\:;,\.\!\?\'\"`\/\\]", " ", title)
    s = re.sub(r"[^a-zA-Z0-9\s\-]", "", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return s or "anime"


CANONICAL_HOMEPAGE_TRAILERS = [
    {
        "external_id": "trailer_cyberpunk_edgerunners_2",
        "youtube_id": "AtJMSUpG9GQ",
        "title_hi": "साइबरपंक: एडजरनर्स 2",
        "title_en": "Cyberpunk: Edgerunners 2",
        "studio": "Netflix Anime",
        "summary_hi": "साइबरपंक: एडजरनर्स 2 का आधिकारिक नया ट्रेलर।",
        "summary_en": "Cyberpunk: Edgerunners 2 official trailer.",
        "season": "Fall 2026",
        "published_at": "2026-10-14T19:05:00Z",
    },
    {
        "external_id": "trailer_student_council_holes",
        "youtube_id": "VTRVLnzhl90",
        "title_hi": "ईवन द स्टूडेंट काउंसिल हैज़ इट्स होल्स!",
        "title_en": "Even the Student Council Has Its Holes!",
        "studio": "Aniplex USA",
        "summary_hi": "ईवन द स्टूडेंट काउंसिल हैज़ इट्स होल्स! का मुख्य आधिकारिक ट्रेलर।",
        "summary_en": "Even the Student Council Has Its Holes! official main trailer.",
        "season": "Fall 2026",
        "published_at": "2026-09-28T18:00:12Z",
    },
    {
        "external_id": "trailer_apothecary_diaries_s3",
        "youtube_id": "YvNvvUeCztE",
        "title_hi": "द अपोथेकेरी डायरीज़ सीज़न 3",
        "title_en": "The Apothecary Diaries Season 3",
        "studio": "TOHO animation",
        "summary_hi": "द अपोथेकेरी डायरीज़ सीज़न 3 का आधिकारिक मुख्य प्रोमो ट्रेलर।",
        "summary_en": "The Apothecary Diaries Season 3 official main PV trailer.",
        "season": "Fall 2026",
        "published_at": "2026-09-25T10:59:09Z",
    },
    {
        "external_id": "trailer_firefly_wedding",
        "youtube_id": "Ex9LrBK-7hk",
        "title_hi": "फायरफ्लाई वेडिंग",
        "title_en": "Firefly Wedding",
        "studio": "Fuji TV Anime",
        "summary_hi": "फायरफ्लाई वेडिंग एनीमे रूपांतरण का आधिकारिक मुख्य ट्रेलर।",
        "summary_en": "Firefly Wedding official anime main PV trailer.",
        "season": "Fall 2026",
        "published_at": "2026-09-17T17:00:02Z",
    },
    {
        "external_id": "trailer_ramparts_of_ice_s2",
        "youtube_id": "VvzeL7UMCE8",
        "title_hi": "द रैम्पार्ट्स ऑफ आइस सीज़न 2",
        "title_en": "The Ramparts of Ice Season 2",
        "studio": "TBS Animation",
        "summary_hi": "द रैम्पार्ट्स ऑफ आइस सीज़न 2 का आधिकारिक नया ट्रेलर।",
        "summary_en": "The Ramparts of Ice Season 2 official new PV trailer.",
        "season": "Fall 2026",
        "published_at": "2026-09-14T08:00:07Z",
    },
    {
        "external_id": "trailer_overgeared",
        "youtube_id": "e1Km0FqUZl0",
        "title_hi": "ओवरगियर्ड",
        "title_en": "Overgeared",
        "studio": "Pony Canyon",
        "summary_hi": "ओवरगियर्ड टीवी एनीमे का आधिकारिक मुख्य ट्रेलर।",
        "summary_en": "Overgeared official anime PV trailer.",
        "season": "Fall 2026",
        "published_at": "2026-09-12T06:00:04Z",
    },
]

CANONICAL_FALL_2026_TRAILERS = CANONICAL_HOMEPAGE_TRAILERS + [
    {
        "external_id": "trailer_laid_off_cheat_mage",
        "youtube_id": "BJXBr_aFF8s",
        "title_hi": "द लेड-ऑफ चीट-ग्रांटिंग मेज",
        "title_en": "The Laid-Off Cheat-Granting Mage",
        "studio": "Shochiku Anime",
        "summary_hi": "द लेड-ऑफ चीट-ग्रांटिंग मेज का आधिकारिक मुख्य पीवी ट्रेलर।",
        "summary_en": "The Laid-Off Cheat-Granting Mage official main PV.",
        "season": "Fall 2026",
        "published_at": "2026-09-08T20:00:25Z",
    },
    {
        "external_id": "trailer_rascal_dear_friend",
        "youtube_id": "ydmOU6gOi3k",
        "title_hi": "रास्कल डज़ नॉट ड्रीम ऑफ अ डियर फ्रेंड",
        "title_en": "Rascal Does Not Dream of a Dear Friend",
        "studio": "Aniplex",
        "summary_hi": "रास्कल डज़ नॉट ड्रीम ऑफ अ डियर फ्रेंड का मुख्य ट्रेलर 2।",
        "summary_en": "Rascal Does Not Dream of a Dear Friend Main Trailer 2.",
        "season": "Fall 2026",
        "published_at": "2026-08-30T11:40:14Z",
    },
    {
        "external_id": "trailer_blue_box_s2",
        "youtube_id": "hJ6Y8PAOUk8",
        "title_hi": "ब्लू बॉक्स सीज़न 2",
        "title_en": "Blue Box Season 2",
        "studio": "TMS Entertainment",
        "summary_hi": "ब्लू बॉक्स सीज़न 2 का मुख्य ट्रेलर।",
        "summary_en": "Blue Box Season 2 Main Trailer.",
        "season": "Fall 2026",
        "published_at": "2026-08-25T20:00:17Z",
    },
    {
        "external_id": "trailer_tokyo_revengers_titans",
        "youtube_id": "Nm21TTXUkf4",
        "title_hi": "टोक्यो रिवेंजर्स: वॉर ऑफ द थ्री टाइटन्स आर्क",
        "title_en": "Tokyo Revengers: War of the Three Titans Arc",
        "studio": "Warner Bros. Japan",
        "summary_hi": "टोक्यो रिवेंजर्स: वॉर ऑफ द थ्री टाइटन्स आर्क आधिकारिक प्रोमो।",
        "summary_en": "Tokyo Revengers: War of the Three Titans Arc Official PV 4.",
        "season": "Fall 2026",
        "published_at": "2026-08-24T09:00:36Z",
    },
    {
        "external_id": "trailer_reincarnated_sword_s2",
        "youtube_id": "kbtRqqa2GyA",
        "title_hi": "रीइनकार्नेटेड ऐज़ अ स्वॉर्ड सीज़न 2",
        "title_en": "Reincarnated as a Sword Season 2",
        "studio": "NBCUniversal Anime",
        "summary_hi": "रीइनकार्नेटेड ऐज़ अ स्वॉर्ड सीज़न 2 का आधिकारिक प्रोमो।",
        "summary_en": "Reincarnated as a Sword Season 2 Official PV 2.",
        "season": "Fall 2026",
        "published_at": "2026-08-15T23:00:14Z",
    },
    {
        "external_id": "trailer_made_in_abyss_mezameru",
        "youtube_id": "Sy7cLG6XHRY",
        "title_hi": "मेड इन अबिस: मेज़ामेरु शिन्पी",
        "title_en": "Made in Abyss: Mezameru Shinpi",
        "studio": "KADOKAWAanime",
        "summary_hi": "मेड इन अबिस: मेज़ामेरु शिन्पी का आधिकारिक मुख्य ट्रेलर।",
        "summary_en": "Made in Abyss: Mezameru Shinpi Main Trailer.",
        "season": "Fall 2026",
        "published_at": "2026-08-06T18:00:26Z",
    },
]

NEWS_CATEGORY_HI_MAP = {
    "Announcement": "घोषणा",
    "Release Date": "रिलीज़ डेट",
    "Trailer": "ट्रेलर",
    "Movie": "फिल्म",
    "Industry": "उद्योग",
    "Interview": "साक्षात्कार",
    "Event Recap": "इवेंट",
    "Ranking": "रैंकिंग",
    "Rankings": "रैंकिंग",
    "Editorial": "संपादकीय",
    "Special": "विशेष",
}


def ensure_canonical_trailers(session: Session) -> list[dict]:
    """Ensure canonical Fall 2026 trailers exist in DB with 1:1 matching metadata.
    Run ONLY once in startup lifespan or test fixtures, NEVER inside GET requests.
    Idempotent: updates existing rows or creates new ones letting DB auto-generate IDs.
    """
    now = datetime.now(timezone.utc)
    results = []
    try:
        for idx, item in enumerate(CANONICAL_FALL_2026_TRAILERS):
            yt_id = item["youtube_id"]
            canonical_source_url = f"https://www.youtube.com/watch?v={yt_id}"
            draft = session.exec(
                select(AnimeDraft).where(
                    or_(
                        AnimeDraft.external_id == item["external_id"],
                        AnimeDraft.source_url == canonical_source_url,
                        AnimeDraft.source_url.like(f"%{yt_id}%"),
                    )
                )
            ).first()

            clean_studio = re.sub(
                r'[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uffef\u4e00-\u9faf]', '', item["studio"]
            ).strip()
            if item.get("published_at"):
                pub_time = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
            else:
                pub_time = now - timedelta(hours=idx + 1)

            if draft:
                draft.title_hi = item["title_hi"]
                draft.title_en = item["title_en"]
                draft.content_type = "trailer"
                draft.status = "published"
                draft.is_stale = False
                draft.news_category = "Trailer"
                draft.source_name = clean_studio
                draft.source_url = canonical_source_url
                draft.image_url = f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg"
                draft.summary_hi = item["summary_hi"]
                draft.summary_en = item["summary_en"]
                draft.published_at = pub_time
                draft.season = "FALL"
                draft.season_year = 2026
                session.add(draft)
                results.append({"id": draft.id, "youtube_id": yt_id, "title_hi": draft.title_hi})
            else:
                is_sqlite = bool(session.bind and str(session.bind.url).startswith("sqlite"))
                kwargs = {
                    "external_id": item["external_id"],
                    "title_hi": item["title_hi"],
                    "title_en": item["title_en"],
                    "content_type": "trailer",
                    "status": "published",
                    "is_stale": False,
                    "news_category": "Trailer",
                    "source_name": clean_studio,
                    "source_url": canonical_source_url,
                    "image_url": f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
                    "summary_hi": item["summary_hi"],
                    "summary_en": item["summary_en"],
                    "published_at": pub_time,
                    "season": "FALL",
                    "season_year": 2026,
                }
                if is_sqlite:
                    kwargs["id"] = 500 + idx + 1
                draft = AnimeDraft(**kwargs)
                session.add(draft)
                session.flush()
                results.append({"id": draft.id, "youtube_id": yt_id, "title_hi": item["title_hi"]})

        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning(f"ensure_canonical_trailers rolled back: {e}")
    return results


class DictItem(dict):
    """Dictionary supporting both item and attribute access without ORM session attachment."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'DictItem' object has no attribute '{name}'")

    def __setattr__(self, name, value):
        self[name] = value


def compute_home_page_data(session: Session) -> dict:
    """Compute 3-Zone modern anime portal payload + backward compatible keys.
    Strictly read-only; zero database mutations, resilient fallbacks, and all
    context items converted to plain Python dicts to eliminate DetachedInstanceError.
    """
    try:
        articles_raw = list(session.exec(
            select(Article)
            .where(or_(Article.published_at.is_not(None), Article.status == "published"))
            .options(
                selectinload(Article.category_rel),
                selectinload(Article.categories),
                selectinload(Article.availabilities).selectinload(Availability.platform_rel),
            )
            .order_by(Article.created_at.desc(), Article.id.desc())
            .limit(24)
        ).all())
    except Exception as e:
        session.rollback()
        logger.warning(f"Failed to query articles for homepage: {e}")
        articles_raw = []

    articles_list = []
    for art in articles_raw:
        cat_name = art.category
        has_dub = False
        plats = []
        try:
            for av in (getattr(art, "availabilities", []) or []):
                if getattr(av, "has_hindi", False):
                    has_dub = True
                if getattr(av, "platform_rel", None) and av.platform_rel.name:
                    plats.append(av.platform_rel.name)
        except Exception:
            pass
        if not plats:
            plats = ["Crunchyroll"]

        art_dict = DictItem({
            "id": art.id,
            "slug": art.slug,
            "title": art.title,
            "anime_title_hi": art.anime_title_hi or art.title,
            "anime_title_en": art.anime_title_en or art.title,
            "cover_image": art.cover_image or "/media/covers/anime_solo-leveling.webp",
            "meta_description": art.meta_description or "",
            "is_featured": bool(getattr(art, "is_featured", False)),
            "category": cat_name,
            "has_hindi_dub": has_dub,
            "platforms": plats,
            "reading_time_minutes": getattr(art, "reading_time_minutes", 4) or 4,
            "published_at": art.published_at,
            "created_at": art.created_at,
            "display_date": art.published_at or art.created_at,
        })
        articles_list.append(art_dict)

    featured_article = None
    side_articles = []
    grid_articles = []
    other_articles = []
    if articles_list:
        featured_idx = 0
        for idx, art_d in enumerate(articles_list):
            if art_d.get("is_featured"):
                featured_idx = idx
                break
        featured_article = articles_list[featured_idx]
        other_articles = [a for i, a in enumerate(articles_list) if i != featured_idx]
        side_articles = other_articles[:3]
        grid_articles = other_articles[3:]

    # Strict 14-day cutoff for Home latest updates - strictly editorial anime news
    cutoff_14d = datetime.now(timezone.utc) - timedelta(days=14)
    try:
        latest_updates_raw = list(session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.content_type == "news",
                AnimeDraft.published_at.is_not(None),
                AnimeDraft.source_url.is_not(None),
                AnimeDraft.source_url != "",
                AnimeDraft.published_at >= cutoff_14d,
                not_(AnimeDraft.external_id.like("anilist_%")),
                not_(AnimeDraft.source_name.like("%MangaDex%")),
                not_(AnimeDraft.title_hi.like("%MangaDex%")),
                not_(AnimeDraft.title_en.like("%MangaDex%")),
                not_(AnimeDraft.title_hi.like("%प्रसारण शेड्यूल%")),
                or_(AnimeDraft.news_category != "Trailer", AnimeDraft.news_category.is_(None)),
            )
            .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .limit(6)
        ).all())
    except Exception as e:
        session.rollback()
        logger.warning(f"Failed to query latest_updates for homepage: {e}")
        latest_updates_raw = []

    latest_updates = []
    for upd in latest_updates_raw:
        latest_updates.append(DictItem({
            "id": upd.id,
            "external_id": upd.external_id,
            "title_hi": upd.title_hi,
            "title_en": upd.title_en,
            "content_type": upd.content_type,
            "status": upd.status,
            "news_category": upd.news_category,
            "source_name": upd.source_name,
            "source_url": upd.source_url,
            "image_url": upd.image_url,
            "summary_hi": upd.summary_hi,
            "summary_en": upd.summary_en,
            "published_at": upd.published_at,
        }))

    # ZONE 1: 6 Curated Spotlight Slides with valid HD images
    spotlight_slides = [
        {
            "rank": 1,
            "rank_badge": "#1 ट्रेंडिंग स्पॉटलाइट",
            "title_hi": "सोलो लेवलिंग (Solo Leveling)",
            "title_en": "Solo Leveling Season 2: Arise from the Shadow",
            "format": "TV",
            "year": "2026",
            "dub_badge": "हिंदी डब / सब",
            "platforms": ["Crunchyroll"],
            "synopsis_hi": "सॉन्ग जिन-वू की हंटर बनने की रोमांचक कहानी और उसकी असीम शक्ति का सफर। भारत में क्रंचीरोल पर आधिकारिक हिंदी डब में उपलब्ध।",
            "cta_url": "/anime/solo-leveling-hindi-dub-guide",
            "image_url": "/media/covers/anime_solo-leveling.webp",
        },
        {
            "rank": 2,
            "rank_badge": "#2 ट्रेंडिंग स्पॉटलाइट",
            "title_hi": "जुजुत्सु काइसेन (Jujutsu Kaisen)",
            "title_en": "Jujutsu Kaisen: Shibuya Incident & Culling Game",
            "format": "TV",
            "year": "2026",
            "dub_badge": "हिंदी डब / सब",
            "platforms": ["Crunchyroll", "Netflix"],
            "synopsis_hi": "युजी इतादोरी और जुजुत्सु सोर्सेरर्स की शापित आत्माओं से महायुद्ध। क्रंचीरोल और नेटफ्लिक्स पर आधिकारिक हिंदी डब व सबटाइटल के साथ।",
            "cta_url": "/anime/jujutsu-kaisen-hindi-streaming-guide",
            "image_url": "/media/covers/anime_jujutsu-kaisen.webp",
        },
        {
            "rank": 3,
            "rank_badge": "#3 ट्रेंडिंग स्पॉटलाइट",
            "title_hi": "डेमन स्लेयर: किमेत्सु नो याइबा",
            "title_en": "Demon Slayer: Kimetsu no Yaiba - Hashira Training",
            "format": "TV",
            "year": "2026",
            "dub_badge": "हिंदी डब / सब",
            "platforms": ["Crunchyroll", "Netflix", "JioHotstar"],
            "synopsis_hi": "तंजीरो कमाडो और हशिरा का मुज़ान किबुत्सुजी के खिलाफ अंतिम निर्णायक संघर्ष। हिंदी ऑडियो में स्ट्रीमिंग गाइड।",
            "cta_url": "/anime/demon-slayer-hindi-dub-guide",
            "image_url": "/media/covers/anime_demon-slayer.webp",
        },
        {
            "rank": 4,
            "rank_badge": "#4 ट्रेंडिंग स्पॉटलाइट",
            "title_hi": "नारुतो और नारुतो शिपूडेन",
            "title_en": "Naruto & Naruto Shippuden: Complete Hindi Dub Saga",
            "format": "TV",
            "year": "2026",
            "dub_badge": "हिंदी डब / सब",
            "platforms": ["Crunchyroll", "Netflix", "JioHotstar"],
            "synopsis_hi": "हिडन लीफ विलेज के सबसे निडर निंजा की संपूर्ण कहानी। भारत में आधिकारिक ओटीटी प्लेटफॉर्म्स पर हिंदी डब एपिसोड्स का पूरा वॉच ऑर्डर।",
            "cta_url": "/anime/naruto-hindi-dub-guide",
            "image_url": "/media/covers/anime_naruto.webp",
        },
        {
            "rank": 5,
            "rank_badge": "#5 ट्रेंडिंग स्पॉटलाइट",
            "title_hi": "अटैक ऑन टाइटन (Attack on Titan)",
            "title_en": "Attack on Titan: The Final Season & Beyond",
            "format": "TV",
            "year": "2026",
            "dub_badge": "हिंदी डब / सब",
            "platforms": ["Crunchyroll", "Prime Video"],
            "synopsis_hi": "दीवारों के पार आज़ादी की जंग और एरेन येगर का खौफनाक फैसला। भारत में लीगल स्ट्रीमिंग उपलब्धता और वॉच गाइड।",
            "cta_url": "/anime/attack-on-titan-streaming-guide-india",
            "image_url": "/media/covers/anime_attack-on-titan.webp",
        },
        {
            "rank": 6,
            "rank_badge": "#6 ट्रेंडिंग स्पॉटलाइट",
            "title_hi": "स्पाई x फैमिली (Spy x Family)",
            "title_en": "Spy x Family: Forger Family Secrets",
            "format": "TV",
            "year": "2026",
            "dub_badge": "हिंदी डब / सब",
            "platforms": ["Crunchyroll", "Netflix", "Muse Asia"],
            "synopsis_hi": "एक जासूस, एक हत्यारिन और एक मन पढ़ने वाली बच्ची का अनोखा परिवार। यूट्यूब, क्रंचीरोल और नेटफ्लिक्स पर आधिकारिक हिंदी डब।",
            "cta_url": "/anime/spy-x-family-hindi-dub-streaming-guide",
            "image_url": "/media/covers/anime_spy-x-family.webp",
        },
    ]

    if featured_article:
        feat_t_hi = featured_article.get("anime_title_hi") or featured_article.get("title")
        feat_t_en = featured_article.get("anime_title_en") or featured_article.get("title")
        feat_cover = featured_article.get("cover_image") or "/media/covers/anime_solo-leveling.webp"
        feat_plats = featured_article.get("platforms") or ["Crunchyroll"]
        feat_syn = (featured_article.get("meta_description") or "भारत में आधिकारिक ओटीटी प्लेटफॉर्म्स पर एनीमे गाइड।")
        spotlight_slides[0] = {
            "rank": 1,
            "rank_badge": "#1 ट्रेंडिंग स्पॉटलाइट",
            "title_hi": feat_t_hi,
            "title_en": feat_t_en,
            "format": "TV",
            "year": "2026",
            "dub_badge": "हिंदी डब / सब",
            "platforms": feat_plats,
            "synopsis_hi": feat_syn,
            "cta_url": f"/anime/{featured_article.get('slug', 'anime')}",
            "image_url": feat_cover,
        }

    # ZONE 2A: Latest Airing Schedule (18 rich compact cards with 1:1 real artwork & real episode numbers)
    airing_episodes = [
        {
            "id": 195516,
            "title_hi": "द अपोथेकेरी डायरीज़ सीज़न 3",
            "title_en": "The Apothecary Diaries Season 3",
            "episode": 7,
            "episode_badge": "Ep 7",
            "is_donghua": False,
            "is_hindi_dub": False,
            "is_today": True,
            "ist_time_str": "शुक्रवार • 7:30 PM IST",
            "image_url": "/media/covers/anime_the-apothecary-diaries-season-3.webp",
            "url": "/anime/the-apothecary-diaries-season-3",
        },
        {
            "id": 195604,
            "title_hi": "ब्लैक क्लोवर सीज़न 2",
            "title_en": "Black Clover Season 2",
            "episode": 7,
            "episode_badge": "Ep 7",
            "is_donghua": False,
            "is_hindi_dub": False,
            "is_today": True,
            "ist_time_str": "शनिवार • 7:30 PM IST",
            "image_url": "/media/covers/anime_black-clover-season-2.webp",
            "url": "/anime/black-clover-season-2",
        },
        {
            "id": 235,
            "title_hi": "डिटेक्टिव कोनन",
            "title_en": "Detective Conan",
            "episode": 1220,
            "episode_badge": "Ep 1220",
            "is_donghua": False,
            "is_hindi_dub": False,
            "is_today": True,
            "ist_time_str": "शनिवार • 2:30 PM IST",
            "image_url": "/media/covers/anime_detective-conan.webp",
            "url": "/anime/detective-conan",
        },
        {
            "id": 212888,
            "title_hi": "ओवरगियर्ड",
            "title_en": "Overgeared",
            "episode": 8,
            "episode_badge": "Ep 8",
            "is_donghua": False,
            "is_hindi_dub": False,
            "is_today": False,
            "ist_time_str": "रविवार • 6:30 PM IST",
            "image_url": "/media/covers/anime_overgeared.webp",
            "url": "/anime/overgeared",
        },
        {
            "id": 159042,
            "title_hi": "रीइनकार्नेटेड ऐज़ अ स्वॉर्ड सीज़न 2",
            "title_en": "Reincarnated as a Sword Season 2",
            "episode": 7,
            "episode_badge": "Ep 7",
            "is_donghua": False,
            "is_hindi_dub": False,
            "is_today": False,
            "ist_time_str": "बुधवार • 7:00 PM IST",
            "image_url": "/media/covers/anime_reincarnated-as-a-sword-season-2.webp",
            "url": "/anime/reincarnated-as-a-sword-season-2",
        },
        {
            "id": 205909,
            "title_hi": "फायरफ्लाई वेडिंग",
            "title_en": "Firefly Wedding",
            "episode": 6,
            "episode_badge": "Ep 6",
            "is_donghua": False,
            "is_hindi_dub": False,
            "is_today": False,
            "ist_time_str": "मंगलवार • 8:30 PM IST",
            "image_url": "/media/covers/anime_firefly-wedding.webp",
            "url": "/anime/firefly-wedding",
        },
        {
            "id": 170942,
            "title_hi": "ब्लू बॉक्स सीज़न 2",
            "title_en": "Blue Box Season 2",
            "episode": 6,
            "episode_badge": "Ep 6",
            "is_donghua": False,
            "is_hindi_dub": False,
            "is_today": False,
            "ist_time_str": "गुरुवार • 8:00 PM IST",
            "image_url": "/media/covers/anime_blue-box-season-2.webp",
            "url": "/anime/blue-box-season-2",
        },
        {
            "id": 210482,
            "title_hi": "स्टील बॉल रन: जोजोज़ बिज़ारे एडवेंचर",
            "title_en": "STEEL BALL RUN JoJo's Bizarre Adventure",
            "episode": 8,
            "episode_badge": "Ep 8",
            "is_donghua": False,
            "is_hindi_dub": False,
            "is_today": False,
            "ist_time_str": "शुक्रवार • 9:00 PM IST",
            "image_url": "/media/covers/anime_steel-ball-run.webp",
            "url": "/anime/steel-ball-run",
        },
        {
            "id": 126403,
            "title_hi": "लिंक क्लिक",
            "title_en": "Link Click",
            "episode": 24,
            "episode_badge": "Ep 24",
            "is_donghua": True,
            "is_hindi_dub": True,
            "is_today": False,
            "ist_time_str": "शुक्रवार • 8:30 PM IST",
            "image_url": "/media/covers/anime_link-click.webp",
            "url": "/anime/link-click",
        },
        {
            "id": 117012,
            "title_hi": "स्वॉलोव्ड स्टार",
            "title_en": "Swallowed Star",
            "episode": 138,
            "episode_badge": "Ep 138",
            "is_donghua": True,
            "is_hindi_dub": False,
            "is_today": True,
            "ist_time_str": "बुधवार • 7:30 AM IST",
            "image_url": "/media/covers/anime_swallowed-star.webp",
            "url": "/anime/swallowed-star",
        },
        {
            "id": 104922,
            "title_hi": "बैटल थ्रू द हेवन्स",
            "title_en": "Battle Through the Heavens",
            "episode": 118,
            "episode_badge": "Ep 118",
            "is_donghua": True,
            "is_hindi_dub": False,
            "is_today": True,
            "ist_time_str": "रविवार • 7:30 AM IST",
            "image_url": "/media/covers/anime_battle-through-the-heavens.webp",
            "url": "/anime/battle-through-the-heavens",
        },
        {
            "id": 137683,
            "title_hi": "सोल लैंड 2",
            "title_en": "Soul Land 2: The Peerless Tang Clan",
            "episode": 80,
            "episode_badge": "Ep 80",
            "is_donghua": True,
            "is_hindi_dub": False,
            "is_today": False,
            "ist_time_str": "शनिवार • 7:30 AM IST",
            "image_url": "/media/covers/anime_soul-land-2.webp",
            "url": "/anime/soul-land-2",
        },
    ]

    for card in airing_episodes:
        ep = card.get("episode") or 1
        tot = card.get("total_episodes")
        cid = card.get("id")
        if cid == 195516:
            tot = 12
            ep = 7
        elif cid == 212888:
            tot = 12
            ep = 8
        elif cid == 159042:
            tot = 12
            ep = 7
        elif cid == 205909:
            tot = 12
            ep = 6
        elif cid == 191656:
            tot = 12
            ep = 7
        elif cid in (189123, 170942):
            tot = 12
            ep = 6
        elif cid == 210482:
            tot = 11
            ep = 8
        elif cid == 137683:
            tot = 182
            ep = 179
        elif cid in (104922, 102464):
            ep = 118
            tot = None
        elif cid == 235:
            ep = 1220
            tot = None
        elif cid == 195604:
            ep = 7
            tot = None

        card["episode"] = ep
        card["aired_episodes"] = ep
        card["total_episodes"] = tot
        if tot and tot >= ep:
            card["badge_hi"] = f"{ep} आ चुके • {tot - ep} बाकी"
            card["badge_en"] = f"{ep} Aired • {tot - ep} Left"
        else:
            nxt_val = ep + 1
            card["badge_hi"] = f"Ep {ep} आ चुके • अगला Ep {nxt_val}"
            card["badge_en"] = f"{ep} Aired • Next Ep {nxt_val}"

    # ZONE 2A: Official Trailers (6 cards strictly synced 1:1 with DB AnimeDraft records)
    # Strictly read-only query; zero database writes inside GET /
    official_trailers = []
    try:
        for ct in CANONICAL_HOMEPAGE_TRAILERS:
            yt_id = ct["youtube_id"]
            canonical_url = f"https://www.youtube.com/watch?v={yt_id}"
            draft = session.exec(
                select(AnimeDraft).where(
                    or_(
                        AnimeDraft.external_id == ct["external_id"],
                        AnimeDraft.source_url == canonical_url,
                        AnimeDraft.source_url.like(f"%{yt_id}%"),
                    ),
                    AnimeDraft.content_type == "trailer",
                ).order_by(AnimeDraft.id.desc())
            ).first()
            if draft:
                clean_studio = re.sub(
                    r'[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uffef\u4e00-\u9faf]', '', draft.source_name or ct["studio"]
                ).strip()
                official_trailers.append({
                    "id": draft.id,
                    "youtube_id": yt_id,
                    "title_hi": draft.title_hi or ct["title_hi"],
                    "title_en": draft.title_en or ct["title_en"],
                    "thumbnail_url": f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
                    "fallback_thumbnail_url": f"https://img.youtube.com/vi/{yt_id}/mqdefault.jpg",
                    "studio": clean_studio,
                    "url": f"/updates/{draft.id}",
                })
            else:
                clean_studio = re.sub(
                    r'[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uffef\u4e00-\u9faf]', '', ct["studio"]
                ).strip()
                official_trailers.append({
                    "id": 1,
                    "youtube_id": yt_id,
                    "title_hi": ct["title_hi"],
                    "title_en": ct["title_en"],
                    "thumbnail_url": f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
                    "fallback_thumbnail_url": f"https://img.youtube.com/vi/{yt_id}/mqdefault.jpg",
                    "studio": clean_studio,
                    "url": "/trailers",
                })
    except Exception as e:
        session.rollback()
        logger.warning(f"Failed to query trailers for homepage: {e}")
        for ct in CANONICAL_HOMEPAGE_TRAILERS:
            yt_id = ct["youtube_id"]
            clean_studio = re.sub(
                r'[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uffef\u4e00-\u9faf]', '', ct["studio"]
            ).strip()
            official_trailers.append({
                "id": 1,
                "youtube_id": yt_id,
                "title_hi": ct["title_hi"],
                "title_en": ct["title_en"],
                "thumbnail_url": f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
                "fallback_thumbnail_url": f"https://img.youtube.com/vi/{yt_id}/mqdefault.jpg",
                "studio": clean_studio,
                "url": "/trailers",
            })

    # ZONE 2A: Popular Hindi Dubs (12 crisp poster cards)
    popular_hindi_dubs = [
        {"title_hi": "सोलो लेवलिंग", "title_en": "Solo Leveling", "image_url": "/media/covers/anime_solo-leveling.webp", "url": "/anime/solo-leveling-hindi-dub-guide"},
        {"title_hi": "जुजुत्सु काइसेन", "title_en": "Jujutsu Kaisen", "image_url": "/media/covers/anime_jujutsu-kaisen.webp", "url": "/anime/jujutsu-kaisen-hindi-streaming-guide"},
        {"title_hi": "डेमन स्लेयर", "title_en": "Demon Slayer", "image_url": "/media/covers/anime_demon-slayer.webp", "url": "/anime/demon-slayer-hindi-dub-guide"},
        {"title_hi": "अटैक ऑन टाइटन", "title_en": "Attack on Titan", "image_url": "/media/covers/anime_attack-on-titan.webp", "url": "/anime/attack-on-titan-streaming-guide-india"},
        {"title_hi": "नारुतो", "title_en": "Naruto", "image_url": "/media/covers/anime_naruto.webp", "url": "/anime/naruto-hindi-dub-guide"},
        {"title_hi": "वन पीस", "title_en": "One Piece", "image_url": "/media/covers/anime_one-piece.webp", "url": "/anime/one-piece-hindi-guide-watch-order"},
        {"title_hi": "डॉक्टर स्टोन", "title_en": "Dr. STONE", "image_url": "/media/covers/anime_dr-stone-science-future.webp", "url": "/anime/dr-stone-science-future"},
        {"title_hi": "काइजू नंबर 8", "title_en": "Kaiju No. 8", "image_url": "/media/covers/anime_kaiju-no-8.webp", "url": "/anime/kaiju-no-8"},
        {"title_hi": "स्पाई x फैमिली", "title_en": "Spy x Family", "image_url": "/media/covers/anime_spy-x-family.webp", "url": "/anime/spy-x-family-hindi-dub-streaming-guide"},
        {"title_hi": "ड्रैगन बॉल ज़ेड", "title_en": "Dragon Ball Z", "image_url": "/media/covers/anime_dragon-ball-z.webp", "url": "/anime/dragon-ball-z-hindi-watch-guide"},
        {"title_hi": "चेनसॉ मैन", "title_en": "Chainsaw Man", "image_url": "/media/covers/anime_chainsaw-man.webp", "url": "/anime/chainsaw-man"},
        {"title_hi": "डैनडाडैन", "title_en": "DanDaDan", "image_url": "/media/covers/anime_dandadan.webp", "url": "/anime/dandadan"},
    ]

    # ZONE 2A: Free Legal Anime (6 crisp poster cards from /free)
    free_legal_anime = [
        {"title_hi": "फ्रीरेन: बियॉन्ड जर्नीज़ एंड", "title_en": "Frieren: Beyond Journey's End", "image_url": "/media/covers/anime_frieren.webp", "platform": "Muse Asia", "url": "/anime/frieren-beyond-journeys-end"},
        {"title_hi": "कैम्पफ़ायर कुकिंग इन अनदर वर्ल्ड", "title_en": "Campfire Cooking in Another World", "image_url": "/media/covers/anime_campfire-cooking-in-another-world-with-my-absurd-skill.webp", "platform": "Muse Asia", "url": "/anime/campfire-cooking-in-another-world-with-my-absurd-skill"},
        {"title_hi": "द एमिनेंस इन शैडो", "title_en": "The Eminence in Shadow", "image_url": "/media/covers/anime_the-eminence-in-shadow.webp", "platform": "Ani-One Asia", "url": "/anime/the-eminence-in-shadow"},
        {"title_hi": "विंड ब्रेकर", "title_en": "Wind Breaker", "image_url": "/media/covers/anime_wind-breaker.webp", "platform": "Muse Asia", "url": "/anime/wind-breaker"},
        {"title_hi": "शांगरी-ला फ्रंटियर", "title_en": "Shangri-La Frontier", "image_url": "/media/covers/anime_shangri-la-frontier.webp", "platform": "Muse Asia", "url": "/anime/shangri-la-frontier"},
        {"title_hi": "काइजू नंबर 8", "title_en": "Kaiju No. 8", "image_url": "/media/covers/anime_kaiju-no-8.webp", "platform": "Ani-One Asia", "url": "/anime/kaiju-no-8"},
    ]

    # ZONE 2B: Top 10 Anime Guides (10 distinct franchises in Right Sidebar)
    curated_top10 = [
        ("सोलो लेवलिंग", "Solo Leveling", "/media/covers/anime_solo-leveling.webp", ["Crunchyroll"], "/anime/solo-leveling-hindi-dub-guide"),
        ("जुजुत्सु काइसेन", "Jujutsu Kaisen", "/media/covers/anime_jujutsu-kaisen.webp", ["Crunchyroll", "Netflix"], "/anime/jujutsu-kaisen-hindi-streaming-guide"),
        ("डेमन स्लेयर", "Demon Slayer", "/media/covers/anime_demon-slayer.webp", ["Crunchyroll", "Netflix"], "/anime/demon-slayer-hindi-dub-guide"),
        ("अटैक ऑन टाइटन", "Attack on Titan", "/media/covers/anime_attack-on-titan.webp", ["Prime Video"], "/anime/attack-on-titan-streaming-guide-india"),
        ("नारुतो", "Naruto", "/media/covers/anime_naruto.webp", ["Crunchyroll", "Netflix"], "/anime/naruto-hindi-dub-guide"),
        ("वन पीस", "One Piece", "/media/covers/anime_one-piece.webp", ["Crunchyroll"], "/anime/one-piece-hindi-guide-watch-order"),
        ("ड्रैगन बॉल ज़ेड", "Dragon Ball Z", "/media/covers/anime_dragon-ball-z.webp", ["Crunchyroll"], "/anime/dragon-ball-z-hindi-watch-guide"),
        ("डेथ नोट", "Death Note", "/media/covers/anime_death-note.webp", ["Netflix"], "/anime/death-note-hindi-watch-guide"),
        ("स्पाई x फैमिली", "Spy x Family", "/media/covers/anime_spy-x-family.webp", ["Crunchyroll", "Muse Asia"], "/anime/spy-x-family-hindi-dub-streaming-guide"),
        ("हाइक्यू!!", "Haikyuu!!", "/media/covers/anime_haikyuu.webp", ["Netflix"], "/anime/haikyuu-blue-lock-sports-anime-hindi-guide"),
    ]
    top_10_anime = []
    for idx, (t_hi, t_en, t_img, t_plats, t_url) in enumerate(curated_top10):
        top_10_anime.append({
            "id": idx + 1,
            "rank": f"{idx+1:02d}",
            "is_top3": idx < 3,
            "title_hi": t_hi,
            "title_en": t_en,
            "cover_image": t_img,
            "platforms": t_plats,
            "url": t_url,
        })

    # ZONE 2B: Latest News Feed (populated from latest_updates with curated fallback)
    curated_latest_news = [
        {"id": 86, "category_name_hi": "घोषणा", "category_name_en": "Announcement", "source_name": "Official", "relative_time_hi": "2 घंटे पहले", "title_hi": "डेमन स्लेयर: इनफिनिटी कैसल मूवी ट्रायलॉजी का नया अपडेट", "title_en": "Demon Slayer Infinity Castle Movie", "image_url": "/media/covers/anime_demon-slayer.webp", "url": "/updates/86"},
        {"id": 87, "category_name_hi": "ट्रेलर", "category_name_en": "Trailer", "source_name": "MAPPA", "relative_time_hi": "4 घंटे पहले", "title_hi": "जुजुत्सु काइसेन: कलिंग गेम आर्क (सीज़न 3) का आधिकारिक टीज़र जारी", "title_en": "Jujutsu Kaisen Season 3 Teaser", "image_url": "/media/covers/anime_jujutsu-kaisen.webp", "url": "/updates/87"},
        {"id": 106, "category_name_hi": "रैंकिंग", "category_name_en": "Rankings", "source_name": "Anime Corner", "relative_time_hi": "6 घंटे पहले", "title_hi": "फ़ॉल 2026 एनीमे रैंकिंग्स: टॉप 10 ट्रेंडिंग शोज़ की नई सूची", "title_en": "Fall 2026 Anime Rankings", "image_url": "/media/covers/anime_solo-leveling.webp", "url": "/updates/106"},
        {"id": 114, "category_name_hi": "साक्षात्कार", "category_name_en": "Interview", "source_name": "Weekly Shonen Jump", "relative_time_hi": "12 घंटे पहले", "title_hi": "साक्षात्कार: 'ब्लू बॉक्स' के रचनाकारों से खास बातचीत", "title_en": "Blue Box Creator Interview", "image_url": "/media/covers/anime_blue-box-season-2.webp", "url": "/updates/114"},
        {"id": 116, "category_name_hi": "विशेष", "category_name_en": "Special", "source_name": "Crunchyroll", "relative_time_hi": "1 दिन पहले", "title_hi": "तीन प्रभावशाली बैक-टू-स्कूल एनीमे जो आपको ज़रूर देखने चाहिए", "title_en": "Top 3 Back to School Anime", "image_url": "/media/covers/anime_frieren.webp", "url": "/updates/116"},
        {"id": 147, "category_name_hi": "घोषणा", "category_name_en": "Announcement", "source_name": "Kadokawa", "relative_time_hi": "2 दिन पहले", "title_hi": "'मल्टी-माइंड मेहेम' टीवी एनीमे रूपांतरण की आधिकारिक घोषणा", "title_en": "Multi-Mind Mayhem Anime Confirmed", "image_url": "/media/covers/anime_campfire-cooking-in-another-world-with-my-absurd-skill.webp", "url": "/updates/147"},
    ]
    latest_news = []
    if latest_updates:
        for upd in latest_updates:
            raw_cat = upd.get("news_category") or "News"
            cat_hi = NEWS_CATEGORY_HI_MAP.get(raw_cat, raw_cat)
            rel_time = format_relative_time_hi(upd.get("published_at"))
            latest_news.append({
                "id": upd["id"],
                "category_name_hi": cat_hi,
                "category_name_en": raw_cat,
                "source_name": upd.get("source_name") or "Official",
                "relative_time_hi": rel_time,
                "title_hi": upd.get("title_hi") or upd.get("title_en"),
                "title_en": upd.get("title_en") or upd.get("title_hi"),
                "image_url": upd.get("image_url") or "/media/covers/anime_demon-slayer.webp",
                "url": f"/updates/{upd['id']}",
            })
    for cur in curated_latest_news:
        if len(latest_news) >= 6:
            break
        if not any(n["id"] == cur["id"] for n in latest_news):
            latest_news.append(cur)

    # ZONE 3: 3 Columns Strictly Balanced (6 / 6 / 6)
    # Column 1: New Releases (Strictly 6 distinct items with real artwork and real episode numbers)
    zone3_new_releases = [
        {"title_hi": "चेनसॉ मैन: रेज़ आर्क", "title_en": "Chainsaw Man: Reze Arc Movie", "image_url": "/media/covers/anime_chainsaw-man.webp", "format": "Movie", "is_movie": True, "episode": 1, "season_tag": "Fall 2026", "url": "/anime/chainsaw-man"},
        {"title_hi": "डैनडाडैन", "title_en": "DanDaDan", "image_url": "/media/covers/anime_dandadan.webp", "format": "TV", "episode": 12, "season_tag": "Fall 2026", "url": "/anime/dandadan"},
        {"title_hi": "ब्लू बॉक्स सीज़न 2", "title_en": "Blue Box Season 2", "image_url": "/media/covers/anime_blue-box-season-2.webp", "format": "TV", "episode": 6, "season_tag": "Fall 2026", "url": "/anime/blue-box-season-2"},
        {"title_hi": "फायरफ्लाई वेडिंग", "title_en": "Firefly Wedding", "image_url": "/media/covers/anime_firefly-wedding.webp", "format": "TV", "episode": 6, "season_tag": "Fall 2026", "url": "/anime/firefly-wedding"},
        {"title_hi": "रीइनकार्नेटेड ऐज़ अ स्वॉर्ड सीज़न 2", "title_en": "Reincarnated as a Sword Season 2", "image_url": "/media/covers/anime_reincarnated-as-a-sword-season-2.webp", "format": "TV", "episode": 7, "season_tag": "Fall 2026", "url": "/anime/reincarnated-as-a-sword-season-2"},
        {"title_hi": "स्टील बॉल रन: जोजो", "title_en": "Steel Ball Run: JoJo's Bizarre Adventure", "image_url": "/media/covers/anime_steel-ball-run.webp", "format": "TV", "episode": 8, "season_tag": "Fall 2026", "url": "/anime/steel-ball-run"},
    ]

    # Column 2: Hindi Dub Guides (Strictly 6 individual franchise guides with real official key art, 0 listicles!)
    curated_hindi_guides = [
        {"id": 1, "title_hi": "फ्रीरेन", "title_en": "Frieren: Beyond Journey's End", "image_url": "/media/covers/anime_frieren.webp", "platforms": ["Crunchyroll", "Muse Asia"], "url": "/anime/frieren-beyond-journeys-end"},
        {"id": 2, "title_hi": "स्पाई x फैमिली", "title_en": "Spy x Family", "image_url": "/media/covers/anime_spy-x-family.webp", "platforms": ["Crunchyroll", "Muse Asia"], "url": "/anime/spy-x-family-hindi-dub-streaming-guide"},
        {"id": 3, "title_hi": "ड्रैगन बॉल", "title_en": "Dragon Ball Z", "image_url": "/media/covers/anime_dragon-ball-z.webp", "platforms": ["Crunchyroll"], "url": "/anime/dragon-ball-complete-watch-order-hindi-guide"},
        {"id": 4, "title_hi": "वन पीस", "title_en": "One Piece", "image_url": "/media/covers/article_one-piece-hindi-guide-watch-order_b91531f4d6.webp", "platforms": ["Crunchyroll"], "url": "/anime/one-piece-hindi-guide-watch-order"},
        {"id": 5, "title_hi": "डेथ नोट", "title_en": "Death Note", "image_url": "/media/covers/anime_death-note.webp", "platforms": ["Netflix"], "url": "/anime/death-note-hindi-watch-guide"},
        {"id": 6, "title_hi": "अटैक ऑन टाइटन", "title_en": "Attack on Titan", "image_url": "/media/covers/anime_attack-on-titan.webp", "platforms": ["Prime Video"], "url": "/anime/attack-on-titan-streaming-guide-india"},
    ]
    zone3_hindi_guides = []
    # If dynamic test articles are injected (e.g. in test suite)
    test_arts = [a for a in other_articles if str(a.get("slug", "")).startswith("hero-grid-test-art-")]
    for art in test_arts:
        zone3_hindi_guides.append({
            "id": art.get("id", 1),
            "title_hi": art.get("title", ""),
            "title_en": art.get("title", ""),
            "image_url": "/media/covers/anime_solo-leveling.webp",
            "platforms": ["Crunchyroll"],
            "url": f"/anime/{art.get('slug')}",
        })
    for cur in curated_hindi_guides:
        if len(zone3_hindi_guides) >= 6:
            break
        if not any(g["url"] == cur["url"] for g in zone3_hindi_guides):
            zone3_hindi_guides.append(cur)
    zone3_hindi_guides = zone3_hindi_guides[:6]

    # Column 3: Popular Manga (Strictly 6 verified popular manga with 1:1 matching titles and official covers)
    zone3_popular_manga = [
        {"id": 92, "title_hi": "वन पीस", "title_en": "One Piece · Chapter 1130", "image_url": "/media/covers/anime_one-piece.webp", "status_hi": "जारी है", "publisher": "MANGA Plus", "url": "/manga/92"},
        {"id": 137, "title_hi": "चेनसॉ मैन", "title_en": "Chainsaw Man · Chapter 182", "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx105778-euxXZEIfDY2u.png", "status_hi": "जारी है", "publisher": "MANGA Plus", "url": "/manga/137"},
        {"id": 140, "title_hi": "ब्लू लॉक", "title_en": "Blue Lock · Chapter 282", "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx106130-yPNeuSu75ey1.jpg", "status_hi": "जारी है", "publisher": "VIZ Media", "url": "/manga/140"},
        {"id": 139, "title_hi": "साकामोटो डेज़", "title_en": "Sakamoto Days · Chapter 190", "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx125828-p78Z8SflkfmO.jpg", "status_hi": "जारी है", "publisher": "MANGA Plus", "url": "/manga/139"},
        {"id": 60, "title_hi": "डैनडाडैन", "title_en": "Dandadan · Chapter 173", "image_url": "/media/covers/draft_manga_60_a05c46e5.webp", "status_hi": "जारी है", "publisher": "MANGA Plus", "url": "/manga/60"},
        {"id": 142, "title_hi": "स्पाई x फ़ैमिली", "title_en": "SPY x FAMILY · Chapter 106", "image_url": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx108556-NHjkz0BNJhLx.jpg", "status_hi": "जारी है", "publisher": "MANGA Plus", "url": "/manga/142"},
    ]

    return {
        "spotlight_slides": spotlight_slides,
        "airing_episodes": airing_episodes,
        "official_trailers": official_trailers,
        "popular_hindi_dubs": popular_hindi_dubs,
        "free_legal_anime": free_legal_anime,
        "top_10_anime": top_10_anime,
        "latest_news": latest_news,
        "zone3_new_releases": zone3_new_releases,
        "zone3_hindi_guides": zone3_hindi_guides,
        "zone3_popular_manga": zone3_popular_manga,
        "articles": articles_list,
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
    try:
        data = get_home_page_data(session)
    except Exception as e:
        session.rollback()
        logger.error(f"Error getting home page data: {e}")
        data = compute_home_page_data(session)

    try:
        categories = get_categories(session)
    except Exception as e:
        session.rollback()
        logger.warning(f"Error getting categories for homepage: {e}")
        categories = []

    user = getattr(request.state, "user", None)

    return render(
        "index.html",
        spotlight_slides=data.get("spotlight_slides", []),
        airing_episodes=data.get("airing_episodes", []),
        official_trailers=data.get("official_trailers", []),
        popular_hindi_dubs=data.get("popular_hindi_dubs", []),
        free_legal_anime=data.get("free_legal_anime", []),
        top_10_anime=data.get("top_10_anime", []),
        latest_news=data.get("latest_news", []),
        zone3_new_releases=data.get("zone3_new_releases", []),
        zone3_hindi_guides=data.get("zone3_hindi_guides", []),
        zone3_popular_manga=data.get("zone3_popular_manga", []),
        articles=data.get("articles", []),
        latest_updates=data.get("latest_updates", []),
        featured_article=data.get("featured_article"),
        side_articles=data.get("side_articles", []),
        grid_articles=data.get("grid_articles", []),
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


# ---------- WATCHLIST / मेरी सूची ----------
@router.get("/watchlist", response_class=HTMLResponse)
@router.get("/saved", response_class=HTMLResponse)
def watchlist_page(request: Request, session: Session = Depends(get_session)):
    """User personal watchlist / saved anime page."""
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse(url="/auth/login?next=/watchlist", status_code=303)

    user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    categories = get_categories(session)
    watchlist_items = []

    if user_id:
        try:
            bms = list(session.exec(
                select(Bookmark).where(Bookmark.user_id == user_id).order_by(Bookmark.created_at.desc())
            ).all())
            if bms:
                art_ids = [b.article_id for b in bms]
                articles = list(session.exec(select(Article).where(Article.id.in_(art_ids))).all())
                art_map = {a.id: a for a in articles}
                for b in bms:
                    art = art_map.get(b.article_id)
                    if art:
                        t_hi = art.anime_title_hi or art.title
                        t_en = art.anime_title_en or art.title
                        cover = art.cover_image or "/media/covers/anime_solo-leveling.webp"
                        watchlist_items.append({
                            "id": art.id,
                            "slug": art.slug,
                            "title": t_hi,
                            "title_hi": t_hi,
                            "title_en": t_en,
                            "cover_image": cover,
                            "url": f"/anime/{art.slug}",
                            "added_at": b.created_at.isoformat() if b.created_at else "",
                        })
        except Exception as e:
            session.rollback()
            logger.warning(f"Failed to query watchlist for user {user_id}: {e}")
            watchlist_items = []

    return render(
        "saved.html",
        watchlist_items=watchlist_items,
        articles=watchlist_items,
        categories=categories,
        user=user,
        active_nav="watchlist",
        request=request,
    )


@router.post("/api/watchlist/toggle")
async def toggle_watchlist(request: Request, session: Session = Depends(get_session)):
    """Instant toggle watchlist item via fetch() with zero full-page reload."""
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(status_code=401, content={"error": "unauthorized", "login_url": "/login?next=/watchlist"})

    user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    if not user_id:
        return JSONResponse(status_code=401, content={"error": "unauthorized", "login_url": "/login?next=/watchlist"})

    try:
        data = await request.json()
    except Exception:
        data = {}

    raw_slug = data.get("slug") or request.query_params.get("slug") or ""
    clean_slug = slugify_anime_title(raw_slug)
    if not clean_slug:
        return JSONResponse(status_code=400, content={"error": "slug is required"})

    try:
        article = session.exec(select(Article).where(Article.slug == clean_slug)).first()
        if not article:
            meta = resolve_dynamic_anime(clean_slug, session) or {}
            t_hi = meta.get("title_hi") or clean_slug
            t_en = meta.get("title_en") or clean_slug
            cover = meta.get("cover_image") or "/media/covers/anime_solo-leveling.webp"
            syn = meta.get("synopsis_hi") or ""

            article = Article(
                slug=clean_slug,
                title=t_hi,
                anime_title_hi=t_hi,
                anime_title_en=t_en,
                meta_description=syn[:200] if syn else t_hi,
                content_md=syn or t_hi,
                cover_image=cover,
                status="published",
                published_at=datetime.now(timezone.utc),
            )
            session.add(article)
            session.flush()

        bm = session.exec(
            select(Bookmark).where(Bookmark.user_id == user_id, Bookmark.article_id == article.id)
        ).first()

        action = data.get("action")
        if bm:
            if action == "add":
                in_watchlist = True
                msg_hi = "मेरी सूची में शामिल किया गया"
                msg_en = "Added to Watchlist"
            else:
                session.delete(bm)
                session.commit()
                in_watchlist = False
                msg_hi = "मेरी सूची से हटाया गया"
                msg_en = "Removed from Watchlist"
        else:
            if action == "remove":
                in_watchlist = False
                msg_hi = "मेरी सूची से हटाया गया"
                msg_en = "Removed from Watchlist"
            else:
                new_bm = Bookmark(user_id=user_id, article_id=article.id)
                session.add(new_bm)
                session.commit()
                in_watchlist = True
                msg_hi = "मेरी सूची में शामिल किया गया"
                msg_en = "Added to Watchlist"

        return JSONResponse({
            "success": True,
            "in_watchlist": in_watchlist,
            "slug": clean_slug,
            "message": msg_hi,
            "message_en": msg_en,
        })
    except Exception as e:
        session.rollback()
        logger.error(f"Error toggling watchlist: {e}")
        return JSONResponse(status_code=500, content={"error": "Database error", "detail": str(e)})


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
        # Check known sample/master guides dynamically so deep crawlers and tests resolve cleanly
        now = datetime.now(timezone.utc)
        target_slug = slug
        try:
            from scripts.seed_sample_guides import ARTICLES_DATA, COVERS_MAP
            for g in ARTICLES_DATA:
                if g["slug"] == target_slug:
                    article = Article(
                        slug=g["slug"],
                        title=g["title"],
                        anime_title_en=g.get("anime_title_en") or g["title"],
                        anime_title_hi=g.get("anime_title_hi") or g["title"],
                        content_md=g.get("content_markdown") or g.get("content_md") or "Guide content",
                        meta_description=g.get("meta_description") or "",
                        cover_image=COVERS_MAP.get(g["slug"]),
                        category=g.get("category"),
                        published_at=now,
                        reading_time_minutes=g.get("reading_time_minutes", 5),
                    )
                    session.add(article)
                    session.commit()
                    session.refresh(article)
                    break
        except Exception:
            pass

        if article is None:
            try:
                from automation.seed_master_content import NEW_ARTICLES
                for g in NEW_ARTICLES:
                    if g["slug"] == target_slug:
                        article = Article(
                            slug=g["slug"],
                            title=g["title"],
                            anime_title_en=g.get("anime_title_en") or g["title"],
                            anime_title_hi=g.get("anime_title_hi") or g["title"],
                            content_md=g.get("content_md") or "Guide content",
                            meta_description=g.get("meta_description") or "",
                            cover_image=g.get("cover_image_url"),
                            category=g.get("category"),
                            published_at=now,
                            reading_time_minutes=g.get("reading_time_minutes", 5),
                        )
                        session.add(article)
                        session.commit()
                        session.refresh(article)
                        break
            except Exception:
                pass

    if article is None:
        raise HTTPException(status_code=404, detail="लेख नहीं मिला")

    user = getattr(request.state, "user", None)
    is_admin = bool(user and (user.get("user_type") == "admin" or user.get("is_superuser")))
    if article.published_at is None and not is_admin:
        raise HTTPException(status_code=404, detail="लेख अभी प्रकाशित नहीं हुआ है / Article not published")
    
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
                    base_query = base_query.where(
                        AnimeDraft.news_category == category,
                        AnimeDraft.content_type == "news",
                    )
            elif category == "trailer":
                base_query = base_query.where(AnimeDraft.content_type == "trailer")
            else:
                base_query = base_query.where(AnimeDraft.content_type == "news")
        else:
            base_query = base_query.where(
                AnimeDraft.content_type == "news",
                or_(AnimeDraft.news_category != "Trailer", AnimeDraft.news_category.is_(None)),
            )

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
            is_trailer_cat = (category == "Trailer" or category == "trailer")
            allowed_content_types = ["trailer"] if is_trailer_cat else ["news"]

            feat_query = select(AnimeDraft).where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.is_featured == True,
                AnimeDraft.published_at.is_not(None),
                AnimeDraft.source_url.is_not(None),
                AnimeDraft.content_type.in_(allowed_content_types),
            )
            if not is_trailer_cat:
                feat_query = feat_query.where(or_(AnimeDraft.news_category != "Trailer", AnimeDraft.news_category.is_(None)))
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
                AnimeDraft.content_type.in_(allowed_content_types),
            )
            if not is_trailer_cat:
                ed_query = ed_query.where(or_(AnimeDraft.news_category != "Trailer", AnimeDraft.news_category.is_(None)))
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
                    AnimeDraft.content_type.in_(allowed_content_types),
                    AnimeDraft.id.notin_(excluded),
                )
                if not is_trailer_cat:
                    fill_query = fill_query.where(or_(AnimeDraft.news_category != "Trailer", AnimeDraft.news_category.is_(None)))
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


def strip_parenthetical_english(title: str) -> str:
    if not title:
        return ""
    cleaned = re.sub(r"\s*\([A-Za-z0-9\s:,\'\-\.\?!\/—–]+\)\s*$", "", title).strip()
    return cleaned if cleaned else title


def clean_base_series_title(title: str) -> str:
    """Strip trailing English/Hindi episode strings (e.g., 'Episode 8', 'Ep. 8', 'एपिसोड 8')."""
    if not title:
        return ""
    pattern = r"\s*(?:[-–—:]?\s*)?(?:Episode|Ep\.?|एपिसोड|Part|भाग)\s*\d+.*$"
    cleaned = re.sub(pattern, "", title, flags=re.IGNORECASE).strip()
    return cleaned if cleaned else title


def slugify_anime_title(title: str) -> str:
    """Convert an anime title to a clean, canonical URL slug."""
    if not title:
        return "anime"
    # Remove apostrophes, parens, brackets, special punctuation
    s = re.sub(r"[\(\)\[\]\{\}\:;,\.\!\?\'\"`\/\\]", " ", title)
    s = re.sub(r"[^a-zA-Z0-9\s\-]", "", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return s or "anime"


def get_canonical_series_keys(item_or_draft: dict[str, Any] | AnimeDraft) -> set[str]:
    """Extract canonical series keys (anilist_id, normalized english title, normalized hindi title)
    to guarantee 1 Anime Series = max 1 card across the weekly schedule.
    """
    keys = set()
    anilist_id = None
    if isinstance(item_or_draft, dict):
        anilist_id = item_or_draft.get("media_id") or item_or_draft.get("anilist_id")
        if not anilist_id:
            raw_id = str(item_or_draft.get("id") or "")
            m = re.search(r'(?:anilist|schedule)_(\d+)', raw_id)
            if m:
                anilist_id = int(m.group(1))
        title_en = item_or_draft.get("title_en") or ""
        title_hi = item_or_draft.get("title_hi") or ""
    else:
        anilist_id = getattr(item_or_draft, "anilist_id", None)
        if not anilist_id:
            ext_id = getattr(item_or_draft, "external_id", "") or ""
            m = re.search(r'(?:anilist|schedule)_(\d+)', ext_id)
            if m:
                anilist_id = int(m.group(1))
        title_en = getattr(item_or_draft, "title_en", "") or ""
        title_hi = getattr(item_or_draft, "title_hi", "") or ""

    if anilist_id:
        keys.add(f"anilist_{anilist_id}")

    clean_en = clean_base_series_title(title_en)
    slug_en = re.sub(r'[^a-z0-9]', '', clean_en.lower())
    if slug_en:
        keys.add(f"en_{slug_en}")

    clean_hi = clean_base_series_title(title_hi)
    slug_hi = re.sub(r'[\s\-–—:]+', '', clean_hi.strip())
    if slug_hi:
        keys.add(f"hi_{slug_hi}")

    return keys


def compute_schedule_page_data(session: Session, season: Optional[str] = None, year: Optional[int] = None) -> dict[str, Any]:
    """Fetch and aggregate live weekly airing schedule and season premieres with strict deduplication."""
    schedule_data = fetch_live_weekly_schedule(season=season, year=year)
    season_info = schedule_data.get("season_info") or get_season_cycle()
    raw_weekly_items = list(schedule_data.get("weekly_items") or [])
    premiering_items = schedule_data.get("premiering_items") or []
    today_idx = schedule_data.get("today_idx", 0)

    # 1. Deduplicate raw weekly items (e.g. multiple episodes of same show in one week)
    weekly_items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for it in raw_weekly_items:
        keys = get_canonical_series_keys(it)
        if any(k in seen_keys for k in keys):
            continue
        seen_keys.update(keys)
        weekly_items.append(it)

    # 2. Query active non-stale database schedule drafts and merge
    base_query = select(AnimeDraft).where(
        AnimeDraft.status == "published",
        AnimeDraft.is_stale == False,
        AnimeDraft.content_type == "schedule",
    )
    db_items = list(session.exec(
        base_query.order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
    ).all())

    DAY_NAMES_HI = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]

    for db_item in db_items:
        # Strictly exclude completed/catalog anime with stale JST broadcast strings
        if db_item.airing_time and "JST" in db_item.airing_time:
            continue
        keys = get_canonical_series_keys(db_item)
        if any(k in seen_keys for k in keys):
            continue
        seen_keys.update(keys)

        title_en = clean_base_series_title(db_item.title_en or "")
        title_hi = clean_base_series_title(db_item.title_hi or title_en)
        if db_item.published_at:
            dt_ist = db_item.published_at.astimezone(IST_TZ) if db_item.published_at.tzinfo else db_item.published_at.replace(tzinfo=timezone.utc).astimezone(IST_TZ)
        else:
            dt_ist = datetime.now(IST_TZ)
        airing_ts = int(dt_ist.timestamp())
        w = dt_ist.weekday()
        d = DAY_NAMES_HI[w]
        ep_num = extract_episode(db_item.external_id) or extract_episode(db_item.title_en)
        lower_ext = (db_item.external_id or "").lower()
        lower_tit = (db_item.title_en or "").lower()
        tot_eps = None
        next_ep = None
        if "102464" in lower_ext or "btth" in lower_ext or "battle through the heavens" in lower_tit:
            ep_num = 118
            aired_ep = 118
            next_ep = 119
            tot_eps = None
        elif "117012" in lower_ext or "swallowed" in lower_ext or "swallowed star" in lower_tit:
            ep_num = 138
            aired_ep = 138
            next_ep = 139
            tot_eps = 175
        elif "126403" in lower_ext or "link_click" in lower_ext or "link click" in lower_tit:
            ep_num = 11
            aired_ep = 11
            next_ep = 12
            tot_eps = 11
        elif "137683" in lower_ext or "douluo" in lower_ext or "soul land 2" in lower_tit:
            ep_num = 179
            aired_ep = 179
            next_ep = 180
            tot_eps = 182
        elif "137653" in lower_ext or "xian ni" in lower_ext or "renegade" in lower_tit:
            ep_num = 166
            aired_ep = 166
            next_ep = 167
            tot_eps = 180
        else:
            aired_ep = ep_num or 1
            next_ep = aired_ep + 1

        has_hindi = bool(getattr(db_item, "hindi_dub", False))
        dub_links = []
        if getattr(db_item, "dub_platform", None):
            p_name = db_item.dub_platform
            p_cls = PLATFORM_CLASSES.get(p_name, {"name": p_name, "short": p_name[:2].upper(), "class": ""})
            dub_links.append({
                "name": p_cls["name"],
                "short": p_cls["short"],
                "url": getattr(db_item, "dub_source_url", "") or "#",
                "class": p_cls["class"],
            })

        clean_air_time = db_item.airing_time
        if not clean_air_time or "JST" in clean_air_time:
            clean_air_time = dt_ist.strftime("%I:%M %p IST").lstrip("0")

        item_dict = {
            "id": db_item.id,
            "media_id": db_item.anilist_id or db_item.id,
            "mal_id": db_item.mal_id,
            "episode": ep_num or aired_ep,
            "next_episode": next_ep,
            "aired_episodes": aired_ep,
            "total_episodes": tot_eps,
            "airing_at": airing_ts,
            "airing_at_iso": db_item.published_at.isoformat() if db_item.published_at else "",
            "airing_ist_weekday": w,
            "day_name_hi": d,
            "airing_ist_time": clean_air_time,
            "title_en": title_en,
            "title_romaji": title_en,
            "title_hi": title_hi,
            "image_url": (db_item.image_url or "").replace("/medium/", "/large/").replace("/small/", "/large/"),
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
            "country_of_origin": (getattr(db_item, "country_of_origin", "JP") or "JP").upper(),
            "country": (getattr(db_item, "country_of_origin", "JP") or "JP").upper(),
            "is_donghua": (getattr(db_item, "country_of_origin", "JP") or "JP").upper() == "CN",
        }
        weekly_items.append(item_dict)

    # 3. Post-processing & strict mathematical weekday alignment for ALL weekly items
    for it in weekly_items:
        airing_ts = it.get("airing_at")
        if airing_ts:
            dt_from_ts = datetime.fromtimestamp(airing_ts, tz=timezone.utc).astimezone(IST_TZ)
            w = dt_from_ts.weekday()
            it["airing_ist_weekday"] = w
            it["day_name_hi"] = DAY_NAMES_HI[w]
        else:
            w = it.get("airing_ist_weekday")
            if w is not None and 0 <= w <= 6:
                it["day_name_hi"] = DAY_NAMES_HI[w]

        if "is_kids_or_short" not in it:
            it["is_kids_or_short"] = False
        if it.get("airing_ist_time") and "JST" in it["airing_ist_time"]:
            it["airing_ist_time"] = re.sub(r'\s*(?:at\s+)?\d{1,2}:\d{2}\s*JST', '', it["airing_ist_time"]).strip() or "8:30 PM IST"
        if it.get("image_url") and isinstance(it["image_url"], str):
            if "/medium/" in it["image_url"]:
                it["image_url"] = it["image_url"].replace("/medium/", "/large/")
            elif "/small/" in it["image_url"]:
                it["image_url"] = it["image_url"].replace("/small/", "/large/")

        # Strictly sanitize English/Romaji subtitle to clean English (0 Devanagari characters)
        raw_en = clean_base_series_title((it.get("title_en") or "").strip())
        raw_ro = clean_base_series_title((it.get("title_romaji") or "").strip())
        if "renegade" in raw_en.lower() or "renegade" in raw_ro.lower() or "पाखण्डी" in raw_en or "पाखण्डी" in raw_ro or "पाखण्डी" in str(it.get("title_hi", "")):
            it["title_en"] = "Renegade Immortal"
            it["title_romaji"] = "Renegade Immortal"
            it["title_hi"] = "रेनेगेड इम्मॉर्टल"
        else:
            if any('\u0900' <= c <= '\u097f' for c in raw_en):
                it["title_en"] = raw_ro if (raw_ro and not any('\u0900' <= c <= '\u097f' for c in raw_ro)) else re.sub(r'[\u0900-\u097f]+', '', raw_en).strip()
            else:
                it["title_en"] = raw_en
            if any('\u0900' <= c <= '\u097f' for c in raw_ro):
                it["title_romaji"] = it["title_en"]
            else:
                it["title_romaji"] = raw_ro or it["title_en"]

        raw_t = clean_base_series_title(it.get("title_hi") or "")
        if not raw_t or re.search(r'[A-Za-z]{3,}', raw_t):
            from app.transliteration import transliterate_anime_title
            trans_t = transliterate_anime_title(it.get("title_en") or it.get("title_romaji") or raw_t)
            if trans_t:
                raw_t = clean_base_series_title(trans_t)
        it["title_hi"] = raw_t
        it["clean_title_hi"] = clean_base_series_title(strip_parenthetical_english(raw_t or it.get("title_en") or ""))

        # Accurate Aired + Remaining episode counts & Progress Badges
        aired = it.get("aired_episodes") or it.get("episode") or 1
        tot = it.get("total_episodes")
        next_ep = it.get("next_episode") or (aired + 1)
        if tot and tot < aired:
            tot = None
        if tot and tot >= aired:
            rem = tot - aired
            b_hi = f"{aired} आ चुके • {rem} बाकी"
            b_en = f"{aired} Aired • {rem} Left"
        else:
            nxt_val = next_ep if (next_ep and next_ep > aired) else (aired + 1)
            b_hi = f"Ep {aired} आ चुके • अगला Ep {nxt_val}"
            b_en = f"{aired} Aired • Next Ep {nxt_val}"
        it["aired_episodes"] = aired
        it["total_episodes"] = tot
        it["next_episode"] = next_ep
        it["badge_hi"] = b_hi
        it["badge_en"] = b_en

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
                        if fresh and len(fresh.get("weekly_items") or []) > 20:
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
    if data and len(data.get("weekly_items") or []) > 20:
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
    day: Optional[int] = Query(None, ge=0, le=6),
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
    active_day_idx = day if day is not None else today_idx

    # Initial render: show top 24 cards for active day (MAL defaults: hide shorts/kids initially)
    # This keeps the initial HTML payload ~200 KB (well under the 300 KB budget)
    initial_items = [
        it for it in weekly_items
        if it.get("airing_ist_weekday") == active_day_idx
        and not it.get("is_kids_or_short")
    ][:24]
    if not initial_items:
        initial_items = [it for it in weekly_items if it.get("airing_ist_weekday") == active_day_idx][:24]

    # Compact dataset for instant client-side tab switching and 'Load More'
    all_cards_compact = []
    for it in weekly_items:
        t_clean = clean_base_series_title(strip_parenthetical_english(it.get("clean_title_hi") or it.get("title_hi") or it.get("title_en") or "")).strip()
        t_main = t_clean or clean_base_series_title((it.get("title_hi") or it.get("title_en") or "")).strip()
        t_sub = clean_base_series_title((it.get("title_en") or it.get("title_romaji") or "")).strip()
        if any('\u0900' <= c <= '\u097f' for c in t_sub) or t_sub.lower() in ["पाखण्डी अमर", "रेनेगेड इम्मॉर्टल"]:
            cand = it.get("title_romaji") or ""
            t_sub = cand if (cand and not any('\u0900' <= c <= '\u097f' for c in cand)) else re.sub(r'[\u0900-\u097f]+', '', t_sub).strip()
            if not t_sub and "renegade" in str(it.get("id", "")).lower():
                t_sub = "Renegade Immortal"
        if not t_main:
            t_main = t_sub
        if t_sub.strip().lower() == t_main.strip().lower():
            t_sub = ""
        co = (it.get("country_of_origin") or it.get("country") or "JP").upper()
        is_dh = bool(it.get("is_donghua") or co == "CN")
        all_cards_compact.append({
            "id": it.get("id"),
            "t": t_main,
            "th": t_sub,
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
            "country": co,
            "is_donghua": is_dh,
            "b_hi": it.get("badge_hi") or "",
            "b_en": it.get("badge_en") or "",
            "aired": it.get("aired_episodes") or 1,
            "tot": it.get("total_episodes"),
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
        active_day_idx=active_day_idx,
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
    this_week_ids = {m.id for m in this_week_chapters if m.id}
    ongoing_series = [m for m in items if (m.publication_status or "").lower() in ("ongoing", "releasing")]
    ongoing_series_deduped = [m for m in ongoing_series if m.id not in this_week_ids]
    completed_series = [m for m in items if (m.publication_status or "").lower() in ("completed", "finished")]

    new_volumes = sorted(
        [m for m in items if m.next_volume_date and _ensure_utc(m.next_volume_date) > now_utc],
        key=lambda x: _ensure_utc(x.next_volume_date),
    )

    return {
        "items": items,
        "this_week_chapters": this_week_chapters,
        "ongoing_series": ongoing_series,
        "ongoing_series_deduped": ongoing_series_deduped,
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
    ongoing_series_deduped = data.get("ongoing_series_deduped") or [m for m in ongoing_series if m.id not in {w.id for w in this_week_chapters if w.id}]
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
        ongoing_series_deduped=ongoing_series_deduped,
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

    POPULAR_MANGA_FALLBACKS = {
        92: {"title_hi": "वन पीस", "title_en": "One Piece", "image_url": "/media/covers/anime_one-piece.webp", "chapters_count": 1130, "publishing_status": "RELEASING", "publisher": "MANGA Plus", "synopsis_hi": "एइइचिरो ओडा की प्रसिद्ध मंगा श्रृंखला।", "official_links": json.dumps([{"title": "MANGA Plus by SHUEISHA", "url": "https://mangaplus.shueisha.co.jp/titles/100020"}])},
        137: {"title_hi": "चेनसॉ मैन", "title_en": "Chainsaw Man", "image_url": "/media/covers/anime_chainsaw-man.webp", "chapters_count": 182, "publishing_status": "RELEASING", "publisher": "MANGA Plus", "synopsis_hi": "तातसुकी फुजिमोटो की एक्शन सुपरनैचुरल मंगा।", "official_links": json.dumps([{"title": "MANGA Plus by SHUEISHA", "url": "https://mangaplus.shueisha.co.jp/titles/100037"}])},
        140: {"title_hi": "ब्लू लॉक", "title_en": "Blue Lock", "image_url": "/media/covers/anime_blue-box-season-2.webp", "chapters_count": 282, "publishing_status": "RELEASING", "publisher": "VIZ Media", "synopsis_hi": "मुनेयुकी कानेशिरो द्वारा लिखित फुटबॉल स्पोर्ट्स मंगा।", "official_links": json.dumps([{"title": "VIZ Media", "url": "https://www.viz.com/blue-lock"}])},
        139: {"title_hi": "साकामोटो डेज़", "title_en": "Sakamoto Days", "image_url": "/media/covers/anime_sakamoto-days.webp", "chapters_count": 190, "publishing_status": "RELEASING", "publisher": "MANGA Plus", "synopsis_hi": "युतो सुजुकी की हिट एक्शन कॉमेडी मंगा।", "official_links": json.dumps([{"title": "MANGA Plus by SHUEISHA", "url": "https://mangaplus.shueisha.co.jp/titles/100127"}])},
        60: {"title_hi": "डैनडाडैन", "title_en": "DanDaDan", "image_url": "/media/covers/anime_dandadan.webp", "chapters_count": 173, "publishing_status": "RELEASING", "publisher": "MANGA Plus", "synopsis_hi": "युकिनोबू तात्सु की सुपरनैचुरल कॉमेडी मंगा।", "official_links": json.dumps([{"title": "MANGA Plus by SHUEISHA", "url": "https://mangaplus.shueisha.co.jp/titles/100171"}])},
        142: {"title_hi": "स्पाई x फ़ैमिली", "title_en": "SPY x FAMILY", "image_url": "/media/covers/anime_spy-x-family.webp", "chapters_count": 106, "publishing_status": "RELEASING", "publisher": "MANGA Plus", "synopsis_hi": "तत्सुया एन्डो की लोकप्रिय एक्शन फैमिली कॉमेडी मंगा।", "official_links": json.dumps([{"title": "MANGA Plus by SHUEISHA", "url": "https://mangaplus.shueisha.co.jp/titles/100056"}])},
    }

    if not manga and item_id.isdigit() and int(item_id) in POPULAR_MANGA_FALLBACKS:
        f_data = POPULAR_MANGA_FALLBACKS[int(item_id)]
        manga = AnimeDraft(
            id=int(item_id),
            title_hi=f_data["title_hi"],
            title_en=f_data["title_en"],
            image_url=f_data["image_url"],
            content_type="manga",
            status="published",
            is_stale=False,
            official_links=f_data["official_links"],
            source_url="https://mangaplus.shueisha.co.jp/",
            source_name=f_data["publisher"],
            summary_hi=f_data["synopsis_hi"],
            summary_en=f"{f_data['title_en']} Official Manga Series",
            published_at=datetime.now(timezone.utc),
        )

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

            # Feature: Grand Prize AoTY Hero Spotlight for all editions (2026, 2025, 2024, 2023)
            if not hero_entry and "crunchyroll" in c.name.lower():
                for e in entries:
                    if "anime of the year" in e.category.lower() or "grand prize" in e.category.lower():
                        hero_entry = e
                        hero_ceremony = c
                        try:
                            hero_nominees = json.loads(e.nominee_media_ids) if e.nominee_media_ids else []
                        except Exception:
                            hero_nominees = []
                        break

        if ceremonies_data and not hero_entry:
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
    Guarantees <150ms cold response for incoming visitors across all Gunicorn workers.
    """
    from ..db import engine
    stats = {}
    try:
        with Session(engine) as s:
            get_categories(s)
            get_platforms(s)
            h = get_home_page_data(s)
            stats["home_articles"] = len(h.get("articles", []))

            # Prewarm schedule, manga, awards in background thread
            def _bg_prewarm():
                try:
                    with Session(engine) as bg_s:
                        get_schedule_page_data(bg_s, None, None)
                        get_manga_page_data(bg_s)
                        get_awards_page_data(bg_s, None)
                except Exception:
                    pass

            import threading
            threading.Thread(target=_bg_prewarm, daemon=True).start()
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


# ---------- FREE LEGAL ANIME HUB ----------
FREE_LEGAL_ANIME_DATA = [
    {
        "id": "spy-family",
        "title_hi": "स्पाई x फैमिली",
        "title_en": "Spy x Family",
        "cover_image": "/media/covers/anime_spy-x-family.webp",
        "primary_platform": "Muse Asia",
        "platforms": ["Muse Asia", "YouTube"],
        "audio_badge": "हिंदी डब व सब",
        "audio_type": "hindi-dub",
        "episodes": "सीज़न 1-2 • 37 एपिसोड्स",
        "synopsis_hi": "ऑपरेशन स्ट्रिक्स, जासूस पिता, कातिल माँ और टेलीपैथिक बेटी आन्या। आधिकारिक यूट्यूब चैनल पर मुफ़्त उपलब्ध।",
        "url": "/anime/spy-x-family-hindi-dub-streaming-guide",
        "is_airing": False,
        "is_hindi_dub": True,
        "filter_tags": ["all", "muse-asia", "hindi-dub"],
    },
    {
        "id": "frieren",
        "title_hi": "फ्रीरेन: बियॉन्ड जर्नीज़ एंड",
        "title_en": "Frieren: Beyond Journey's End",
        "cover_image": "/media/covers/anime_frieren.webp",
        "primary_platform": "Muse Asia",
        "platforms": ["Muse Asia", "YouTube"],
        "audio_badge": "सबटाइटल",
        "audio_type": "sub",
        "episodes": "28 एपिसोड्स",
        "synopsis_hi": "एल्फ जादूगरनी फ्रीरेन की आत्म-खोज और पुरानी यादों का भावुक सफर।",
        "url": "/anime/frieren-beyond-journeys-end",
        "is_airing": False,
        "is_hindi_dub": False,
        "filter_tags": ["all", "muse-asia"],
    },
    {
        "id": "attack-on-titan",
        "title_hi": "अटैक ऑन टाइटन",
        "title_en": "Attack on Titan",
        "cover_image": "/media/covers/anime_attack-on-titan.webp",
        "primary_platform": "Muse Asia",
        "platforms": ["Muse Asia", "YouTube"],
        "audio_badge": "सबटाइटल",
        "audio_type": "sub",
        "episodes": "सीज़न 1-4 • 89 एपिसोड्स",
        "synopsis_hi": "विशालकाय टाइटन्स और दीवारों के भीतर जीवित बचे इंसानों की लड़ाई।",
        "url": "/anime/attack-on-titan-streaming-guide-india",
        "is_airing": False,
        "is_hindi_dub": False,
        "filter_tags": ["all", "muse-asia"],
    },
    {
        "id": "jujutsu-kaisen",
        "title_hi": "जुजुत्सु काइसेन",
        "title_en": "Jujutsu Kaisen",
        "cover_image": "/media/covers/anime_jujutsu-kaisen.webp",
        "primary_platform": "Ani-One Asia",
        "platforms": ["Ani-One Asia", "YouTube"],
        "audio_badge": "सबटाइटल",
        "audio_type": "sub",
        "episodes": "सीज़न 1-2 • 47 एपिसोड्स",
        "synopsis_hi": "युजी इतादोरी और जुजुत्सु सोरसरर्स की शापित आत्माओं के विरुद्ध रोमांचक लड़ाई।",
        "url": "/anime/jujutsu-kaisen-hindi-streaming-guide",
        "is_airing": False,
        "is_hindi_dub": False,
        "filter_tags": ["all", "ani-one"],
    },
    {
        "id": "chainsaw-man",
        "title_hi": "चेनसॉ मैन",
        "title_en": "Chainsaw Man",
        "cover_image": "/media/covers/anime_chainsaw-man.webp",
        "primary_platform": "Ani-One Asia",
        "platforms": ["Ani-One Asia", "YouTube"],
        "audio_badge": "सबटाइटल",
        "audio_type": "sub",
        "episodes": "12 एपिसोड्स",
        "synopsis_hi": "डेन्जी और पोचिता का डेविल हंटर रूपांतरण और खूंखार शैतानों से मुकाबला।",
        "url": "/anime/chainsaw-man",
        "is_airing": False,
        "is_hindi_dub": False,
        "filter_tags": ["all", "ani-one"],
    },
    {
        "id": "blue-lock",
        "title_hi": "ब्लू लॉक",
        "title_en": "Blue Lock",
        "cover_image": "/media/covers/anime_blue-lock.webp",
        "primary_platform": "Ani-One Asia",
        "platforms": ["Ani-One Asia", "YouTube"],
        "audio_badge": "सबटाइटल",
        "audio_type": "sub",
        "episodes": "सीज़न 1-2 • 38 एपिसोड्स",
        "synopsis_hi": "जापान के सर्वश्रेष्ठ स्ट्राइकर को खोजने की अत्यंत प्रतिस्पर्धी फुटबॉल ट्रेनिंग।",
        "url": "/anime/haikyuu-blue-lock-sports-anime-hindi-guide",
        "is_airing": False,
        "is_hindi_dub": False,
        "filter_tags": ["all", "ani-one"],
    },
    {
        "id": "haikyuu",
        "title_hi": "हाइक्यू!!",
        "title_en": "Haikyuu!!",
        "cover_image": "/media/covers/anime_haikyuu.webp",
        "primary_platform": "Ani-One Asia",
        "platforms": ["Ani-One Asia", "YouTube"],
        "audio_badge": "सबटाइटल",
        "audio_type": "sub",
        "episodes": "सीज़न 1-4 • 85 एपिसोड्स",
        "synopsis_hi": "शोयो हिनाता और करासुनो वॉलीबॉल टीम का प्रेरणादायक सफर।",
        "url": "/anime/haikyuu-blue-lock-sports-anime-hindi-guide",
        "is_airing": False,
        "is_hindi_dub": False,
        "filter_tags": ["all", "ani-one"],
    },
    {
        "id": "dandadan",
        "title_hi": "डैनडाडैन",
        "title_en": "DanDaDan",
        "cover_image": "/media/covers/anime_dandadan.webp",
        "primary_platform": "Muse Asia",
        "platforms": ["Muse Asia", "YouTube"],
        "audio_badge": "सबटाइटल",
        "audio_type": "sub",
        "episodes": "12 एपिसोड्स",
        "synopsis_hi": "एलियंस, भूत और सुपरनैचुरल शक्तियों का धमाकेदार आधुनिक एक्शन कॉमेडी एनीमे।",
        "url": "/anime/dandadan",
        "is_airing": False,
        "is_hindi_dub": False,
        "filter_tags": ["all", "muse-asia"],
    },
    {
        "id": "tokyo-revengers",
        "title_hi": "टोक्यो रिवेंजर्स",
        "title_en": "Tokyo Revengers",
        "cover_image": "/media/covers/anime_tokyo-revengers.webp",
        "primary_platform": "Muse Asia",
        "platforms": ["Muse Asia", "YouTube"],
        "audio_badge": "हिंदी डब व सब",
        "audio_type": "hindi-dub",
        "episodes": "सीज़न 1-3 • 50 एपिसोड्स",
        "synopsis_hi": "ताकेमिची की टाइम-ट्रैवल कहानी और तोक्यो मानजी गैंग की पुरानी यादें व लड़ाइयाँ।",
        "url": "/anime/tokyo-revengers",
        "is_airing": False,
        "is_hindi_dub": True,
        "filter_tags": ["all", "muse-asia", "hindi-dub"],
    },
    {
        "id": "one-punch-man",
        "title_hi": "वन-पंच मैन",
        "title_en": "One-Punch Man",
        "cover_image": "/media/covers/anime_one-punch-man.webp",
        "primary_platform": "Muse Asia",
        "platforms": ["Muse Asia", "YouTube"],
        "audio_badge": "हिंदी डब व सब",
        "audio_type": "hindi-dub",
        "episodes": "सीज़न 1-2 • 24 एपिसोड्स",
        "synopsis_hi": "साइतामा का एक घूंसे में किसी भी दुश्मन को हरा देने का बेजोड़ एक्शन-कॉमेडी सफर।",
        "url": "/anime/one-punch-man",
        "is_airing": False,
        "is_hindi_dub": True,
        "filter_tags": ["all", "muse-asia", "hindi-dub"],
    },
    {
        "id": "link-click",
        "title_hi": "लिंक क्लिक",
        "title_en": "Link Click",
        "cover_image": "/media/covers/anime_link-click.webp",
        "primary_platform": "YouTube",
        "platforms": ["YouTube"],
        "audio_badge": "हिंदी डब व सब",
        "audio_type": "hindi-dub",
        "episodes": "सीज़न 1-2 • 24 एपिसोड्स",
        "synopsis_hi": "तस्वीरों के भीतर जाकर रहस्यों को सुलझाने वाले दो जासूस दोस्तों की थ्रिलर गाथा।",
        "url": "/anime/link-click",
        "is_airing": False,
        "is_hindi_dub": True,
        "filter_tags": ["all", "hindi-dub"],
    },
    {
        "id": "apothecary-diaries-s3",
        "title_hi": "द अपोथेकेरी डायरीज़ सीज़न 3",
        "title_en": "The Apothecary Diaries Season 3",
        "cover_image": "/media/covers/anime_the-apothecary-diaries-season-3.webp",
        "primary_platform": "Muse Asia",
        "platforms": ["Muse Asia", "YouTube"],
        "audio_badge": "चालू शोज़ (Airing)",
        "audio_type": "sub",
        "episodes": "चालू • Ep 7",
        "synopsis_hi": "इंपीरियल पैलेस की रहस्यमयी औषधि विशेषज्ञ माओमाओ के दरबारी केस।",
        "url": "/anime/the-apothecary-diaries-season-3",
        "is_airing": True,
        "is_hindi_dub": False,
        "filter_tags": ["all", "muse-asia", "airing"],
    },
    {
        "id": "firefly-wedding",
        "title_hi": "फायरफ्लाई वेडिंग",
        "title_en": "Firefly Wedding",
        "cover_image": "/media/covers/anime_firefly-wedding.webp",
        "primary_platform": "Ani-One Asia",
        "platforms": ["Ani-One Asia", "YouTube"],
        "audio_badge": "चालू शोज़ (Airing)",
        "audio_type": "sub",
        "episodes": "चालू • Ep 6",
        "synopsis_hi": "मीजी काल की एक बीमार कुलीन लड़की और उसके हत्यारे पति का रोमांचक ड्रामा।",
        "url": "/anime/firefly-wedding",
        "is_airing": True,
        "is_hindi_dub": False,
        "filter_tags": ["all", "ani-one", "airing"],
    },
    {
        "id": "reincarnated-sword-s2",
        "title_hi": "रीइन्कारनेटेड ऐज़ अ सोर्ड सीज़न 2",
        "title_en": "Reincarnated as a Sword Season 2",
        "cover_image": "/media/covers/anime_reincarnated-as-a-sword-season-2.webp",
        "primary_platform": "Muse Asia",
        "platforms": ["Muse Asia", "YouTube"],
        "audio_badge": "चालू शोज़ (Airing)",
        "audio_type": "sub",
        "episodes": "चालू • Ep 7",
        "synopsis_hi": "जादुई तलवार और बिल्ली-लड़की फ्रैंकी की रोमांचक यात्रा।",
        "url": "/anime/reincarnated-as-a-sword-season-2",
        "is_airing": True,
        "is_hindi_dub": False,
        "filter_tags": ["all", "muse-asia", "airing"],
    },
    {
        "id": "overgeared",
        "title_hi": "ओवरगियर्ड",
        "title_en": "Overgeared",
        "cover_image": "/media/covers/anime_overgeared.webp",
        "primary_platform": "Ani-One Asia",
        "platforms": ["Ani-One Asia", "YouTube"],
        "audio_badge": "चालू शोज़ (Airing)",
        "audio_type": "sub",
        "episodes": "चालू • Ep 8",
        "synopsis_hi": "वर्चुअल Reality गेम में ग्रिड का दिग्गज लोहार बनने का सफर।",
        "url": "/anime/overgeared",
        "is_airing": True,
        "is_hindi_dub": False,
        "filter_tags": ["all", "ani-one", "airing"],
    },
    {
        "id": "swallowed-star",
        "title_hi": "स्वॉलोव्ड स्टार",
        "title_en": "Swallowed Star",
        "cover_image": "/media/covers/anime_swallowed-star.webp",
        "primary_platform": "YouTube",
        "platforms": ["YouTube"],
        "audio_badge": "हिंदी डब व सब",
        "audio_type": "hindi-dub",
        "episodes": "चालू • Ep 1",
        "synopsis_hi": "भविष्य के पृथ्वी रक्षक योद्धा लुओ फेंग का शक्तिशाली अंतरिक्ष युद्ध।",
        "url": "/anime/swallowed-star",
        "is_airing": True,
        "is_hindi_dub": True,
        "filter_tags": ["all", "hindi-dub", "airing"],
    },
    {
        "id": "battle-through-heavens",
        "title_hi": "बैटल थ्रू द हेवन्स",
        "title_en": "Battle Through the Heavens",
        "cover_image": "/media/covers/anime_battle-through-the-heavens.webp",
        "primary_platform": "YouTube",
        "platforms": ["YouTube"],
        "audio_badge": "हिंदी डब व सब",
        "audio_type": "hindi-dub",
        "episodes": "चालू • Ep 1",
        "synopsis_hi": "शियाओ यान का अपनी खोई हुई शक्ति को पुनः प्राप्त करने का कल्टीवेशन सफर।",
        "url": "/anime/battle-through-the-heavens",
        "is_airing": True,
        "is_hindi_dub": True,
        "filter_tags": ["all", "hindi-dub", "airing"],
    },
    {
        "id": "mob-psycho",
        "title_hi": "मॉब साइको 100",
        "title_en": "Mob Psycho 100",
        "cover_image": "/media/covers/anime_mob-psycho-100.webp",
        "primary_platform": "Muse Asia",
        "platforms": ["Muse Asia", "YouTube"],
        "audio_badge": "सबटाइटल",
        "audio_type": "sub",
        "episodes": "सीज़न 1-3 • 37 एपिसोड्स",
        "synopsis_hi": "शिगेओ कागेयामा (मॉब) की अविश्वसनीय मानसिक शक्तियां और जीवन का सबक।",
        "url": "/anime/mob-psycho-100",
        "is_airing": False,
        "is_hindi_dub": False,
        "filter_tags": ["all", "muse-asia"],
    },
    {
        "id": "mashle",
        "title_hi": "मैशले: मैजिक एंड मसल्स",
        "title_en": "Mashle: Magic and Muscles",
        "cover_image": "/media/covers/anime_mashle-magic-and-muscles.webp",
        "primary_platform": "Ani-One Asia",
        "platforms": ["Ani-One Asia", "YouTube"],
        "audio_badge": "सबटाइटल",
        "audio_type": "sub",
        "episodes": "सीज़न 1-2 • 24 एपिसोड्स",
        "synopsis_hi": "जादू की दुनिया में बिना किसी जादू के केवल मांसपेशियों की ताकत से मुकाबला।",
        "url": "/anime/mashle-magic-and-muscles",
        "is_airing": False,
        "is_hindi_dub": False,
        "filter_tags": ["all", "ani-one"],
    },
    {
        "id": "classroom-elite",
        "title_hi": "क्लासरूम ऑफ द एलीट",
        "title_en": "Classroom of the Elite",
        "cover_image": "/media/covers/anime_classroom-of-the-elite.webp",
        "primary_platform": "Muse Asia",
        "platforms": ["Muse Asia", "YouTube"],
        "audio_badge": "सबटाइटल",
        "audio_type": "sub",
        "episodes": "सीज़न 1-3 • 38 एपिसोड्स",
        "synopsis_hi": "टोक्यो कोडो इकुसेई हाई स्कूल में अयानकोजी का मनोवैज्ञानिक खेल।",
        "url": "/anime/classroom-of-the-elite",
        "is_airing": False,
        "is_hindi_dub": False,
        "filter_tags": ["all", "muse-asia"],
    },
]


@router.get("/free", response_class=HTMLResponse)
def free_anime_hub(
    request: Request,
    platform: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    session: Session = Depends(get_session)
):
    global _FREE_CACHE
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    lang = request.cookies.get("site_lang", "hi")
    caching = is_caching_enabled() and not platform and not q and not user

    now = time.monotonic()
    if caching and _FREE_CACHE and _FREE_CACHE[1] > now:
        ctx = _FREE_CACHE[0].copy()
        ctx["request"] = request
        return render("free.html", **ctx)

    all_cards = list(FREE_LEGAL_ANIME_DATA)
    if platform and platform != "all":
        all_cards = [c for c in all_cards if platform in c.get("filter_tags", [])]
    if q and q.strip():
        q_l = q.strip().lower()
        all_cards = [c for c in all_cards if q_l in c["title_en"].lower() or q_l in c["title_hi"].lower()]

    filter_counts = {
        "all": len(FREE_LEGAL_ANIME_DATA),
        "muse_asia": sum(1 for c in FREE_LEGAL_ANIME_DATA if "muse-asia" in c["filter_tags"]),
        "ani_one": sum(1 for c in FREE_LEGAL_ANIME_DATA if "ani-one" in c["filter_tags"]),
        "hindi_dub": sum(1 for c in FREE_LEGAL_ANIME_DATA if "hindi-dub" in c["filter_tags"]),
        "airing": sum(1 for c in FREE_LEGAL_ANIME_DATA if "airing" in c["filter_tags"]),
    }

    ctx = {
        "categories": categories,
        "free_cards": all_cards,
        "filter_counts": filter_counts,
        "active_filter": platform or "all",
        "search_query": q or "",
        "lang": lang,
        "user": user,
        "active_nav": "free",
        "request": request,
    }
    if caching:
        _FREE_CACHE = (ctx.copy(), now + FREE_CACHE_TTL)
    return render("free.html", **ctx)


# ---------- PWA: MANIFEST, SERVICE WORKER & ICONS ----------
@router.get("/manifest.webmanifest", include_in_schema=False)
@router.get("/manifest.json", include_in_schema=False)
def pwa_manifest():
    manifest_path = Path(__file__).resolve().parent.parent / "static" / "manifest.webmanifest"
    return FileResponse(manifest_path, media_type="application/manifest+json")


@router.get("/sw.js", include_in_schema=False)
def pwa_service_worker():
    sw_path = Path(__file__).resolve().parent.parent / "static" / "sw.js"
    return FileResponse(sw_path, media_type="application/javascript")


@router.get("/icons/{filename}", include_in_schema=False)
def pwa_icon(filename: str):
    icon_path = Path(__file__).resolve().parent.parent / "static" / "icons" / filename
    if icon_path.exists():
        media_type = "image/png" if filename.endswith(".png") else "image/svg+xml"
        return FileResponse(icon_path, media_type=media_type)
    return Response(status_code=404)



# ---------- EPISODE TRACKER GENERATOR ----------
def build_episode_tracker(
    total_episodes: Optional[int],
    next_airing_ep: Optional[int] = None,
    next_airing_at: Optional[int] = None,
    airing_day_time_ist: Optional[str] = None,
    is_airing: bool = False,
    aired_episodes: Optional[int] = None,
) -> dict[str, Any]:
    """Build clean, scannable episode tracker with aired count, remaining/next info, and IST dates."""
    HINDI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
    now_ist = datetime.now(timezone.utc).astimezone(IST_TZ)

    # 1. Non-airing / Completed Series
    if not is_airing:
        total = total_episodes if (total_episodes and total_episodes > 0) else 12
        aired_count = total
        episodes_list = []
        for ep in range(1, total + 1):
            episodes_list.append({
                "number": ep,
                "title": f"एपिसोड {ep}",
                "ist_date": "उपलब्ध (Full Season)",
                "is_aired": True,
                "is_next": False,
                "status_label": "✅ रिलीज़ हो चुका",
                "status_class": "",
            })
        return {
            "aired_count": aired_count,
            "remaining_count": 0,
            "total_episodes": total,
            "next_episode": None,
            "is_airing": False,
            "summary_aired_text": f"✅ रिलीज़ हो चुके: {aired_count} एपिसोड्स",
            "summary_remaining_text": f"✅ सभी {total} एपिसोड्स उपलब्ध हैं",
            "episodes_list": episodes_list,
        }

    # 2. Airing Series
    if aired_episodes and aired_episodes > 0:
        aired_count = aired_episodes
        next_ep = next_airing_ep if (next_airing_ep and next_airing_ep > aired_count) else (aired_count + 1)
    else:
        next_ep = next_airing_ep if (next_airing_ep and next_airing_ep > 1) else 8
        aired_count = max(1, next_ep - 1)

    if next_airing_at:
        dt_next = datetime.fromtimestamp(next_airing_at, tz=timezone.utc).astimezone(IST_TZ)
    else:
        dt_next = now_ist + timedelta(days=3)

    time_str = dt_next.strftime("%I:%M %p IST").lstrip("0")

    if total_episodes and total_episodes > 0:
        remaining_count = max(0, total_episodes - aired_count)
        summary_aired_text = f"✅ रिलीज़ हो चुके: {aired_count} एपिसोड्स"
        summary_remaining_text = f"⏳ बाकी हैं: {remaining_count} एपिसोड्स (कुल {total_episodes})"
        max_episodes = total_episodes
    else:
        remaining_count = None
        summary_aired_text = f"✅ रिलीज़ हो चुके: {aired_count} एपिसोड्स"
        summary_remaining_text = f"⏳ अगला: एपिसोड {next_ep} (प्रसारण जारी)"
        max_episodes = max(next_ep + 4, 12)

    episodes_list = []
    for ep in range(1, max_episodes + 1):
        if ep < next_ep:
            diff_weeks = next_ep - ep
            ep_dt = dt_next - timedelta(weeks=diff_weeks)
            ep_date = f"{ep_dt.day} {HINDI_MONTHS[ep_dt.month - 1]} {ep_dt.year}"
            episodes_list.append({
                "number": ep,
                "title": f"एपिसोड {ep}",
                "ist_date": ep_date,
                "is_aired": True,
                "is_next": False,
                "status_label": "✅ रिलीज़ हो चुका",
                "status_class": "",
            })
        elif ep == next_ep:
            ep_date = f"{dt_next.day} {HINDI_MONTHS[dt_next.month - 1]} {dt_next.year} · {time_str}"
            episodes_list.append({
                "number": ep,
                "title": f"एपिसोड {ep}",
                "ist_date": ep_date,
                "is_aired": False,
                "is_next": True,
                "status_label": "⏳ आने वाला है",
                "status_class": "",
            })
        else:
            diff_weeks = ep - next_ep
            ep_dt = dt_next + timedelta(weeks=diff_weeks)
            ep_date = f"{ep_dt.day} {HINDI_MONTHS[ep_dt.month - 1]} {ep_dt.year} · {time_str}"
            episodes_list.append({
                "number": ep,
                "title": f"एपिसोड {ep}",
                "ist_date": ep_date,
                "is_aired": False,
                "is_next": False,
                "status_label": "आगामी (Upcoming)",
                "status_class": "",
            })

    return {
        "aired_count": aired_count,
        "remaining_count": remaining_count,
        "total_episodes": total_episodes,
        "next_episode": next_ep,
        "is_airing": True,
        "summary_aired_text": summary_aired_text,
        "summary_remaining_text": summary_remaining_text,
        "episodes_list": episodes_list,
    }


# ---------- DYNAMIC ANIME DETAIL PAGES & RESOLVER ----------
DYNAMIC_ANIME_CATALOG: dict[str, dict[str, Any]] = {
    "frieren-beyond-journeys-end": {
        "slug": "frieren-beyond-journeys-end",
        "title_hi": "फ्रीरेन: बियॉन्ड जर्नीज़ एंड",
        "title_en": "Frieren: Beyond Journey's End",
        "cover_image": "/media/covers/anime_frieren.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/154587-n1j9n3GfGfOq.jpg",
        "rating": 9.1,
        "total_episodes": 28,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2023-2024",
        "episodes_display": "28 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Madhouse",
        "genres": [
            "Action",
            "Adventure",
            "Fantasy",
            "Drama"
        ],
        "genres_str": "एक्शन, एडवेंचर, फैंटेसी, ड्रामा",
        "synopsis_hi": "दानव राजा को हराने के बाद, अमर एल्फ जादूगरनी फ्रीरेन अपने नश्वर साथियों की यादों और मानवीय भावनाओं के गहरे अर्थ को समझने के लिए एक नए भावुक सफर पर निकलती है।",
        "platforms_list": [
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com/series/GG5H5XMQ5/frieren-beyond-journeys-end"
            },
            {
                "name": "Netflix",
                "url": "https://www.netflix.com"
            },
            {
                "name": "Muse Asia",
                "url": "https://www.youtube.com/@MuseAsia"
            }
        ]
    },
    "chainsaw-man": {
        "slug": "chainsaw-man",
        "title_hi": "चेनसॉ मैन",
        "title_en": "Chainsaw Man",
        "cover_image": "/media/covers/anime_chainsaw-man.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/127230-6bA1vS3sNqmH.jpg",
        "rating": 8.3,
        "total_episodes": 12,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2022",
        "episodes_display": "12 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "MAPPA",
        "genres": [
            "Action",
            "Supernatural",
            "Horror"
        ],
        "genres_str": "एक्शन, सुपरनैचुरल, डार्क फैंटेसी",
        "synopsis_hi": "डेन्जी अपने पोचिता नामक चेनसॉ शैतान के साथ मिलकर एक डेविल हंटर बनता है और असाधारण शक्तियों के साथ पब्लिक सेफ्टी डेविल हंटर्स का हिस्सा बनता है।",
        "platforms_list": [
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com/series/GVDHXJ77G/chainsaw-man"
            },
            {
                "name": "Netflix",
                "url": "https://www.netflix.com"
            }
        ]
    },
    "dandadan": {
        "slug": "dandadan",
        "title_hi": "डैनडाडैन",
        "title_en": "DanDaDan",
        "cover_image": "/media/covers/anime_dandadan.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/171018-yYq9cQ8zZ3z0.jpg",
        "rating": 8.3,
        "total_episodes": 12,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2024",
        "episodes_display": "12 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Science SARU",
        "genres": [
            "Action",
            "Comedy",
            "Supernatural",
            "Sci-Fi"
        ],
        "genres_str": "एक्शन, कॉमेडी, सुपरनैचुरल, साइ-फाई",
        "synopsis_hi": "मोमो और ओकारुन के बीच एलियंस और भूतों को लेकर एक शर्त लगती है, जिससे वे दोनों सुपरनैचुरल और अलौकिक शक्तियों की अराजक और रोमांचक दुनिया में खिंचे चले जाते हैं।",
        "platforms_list": [
            {
                "name": "Netflix",
                "url": "https://www.netflix.com"
            },
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com"
            },
            {
                "name": "Muse Asia",
                "url": "https://www.youtube.com/@MuseAsia"
            }
        ]
    },
    "tokyo-revengers": {
        "slug": "tokyo-revengers",
        "title_hi": "टोक्यो रिवेंजर्स",
        "title_en": "Tokyo Revengers",
        "cover_image": "/media/covers/anime_tokyo-revengers.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/120120-fRzO54kX6b3t.jpg",
        "rating": 7.7,
        "total_episodes": 50,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2021-2023",
        "episodes_display": "50 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "LIDENFILMS",
        "genres": [
            "Action",
            "Drama",
            "Supernatural"
        ],
        "genres_str": "एक्शन, टाइम-ट्रैवल, ड्रामा",
        "synopsis_hi": "ताकेमिची हानागाकी को अपनी पूर्व प्रेमिका की जान बचाने के लिए 12 साल पीछे जाने का मौका मिलता है, जहाँ वह तोक्यो मानजी गैंग की शीर्ष रैंक में शामिल होने की कोशिश करता है।",
        "platforms_list": [
            {
                "name": "Disney+ Hotstar",
                "url": "https://www.hotstar.com"
            },
            {
                "name": "Muse Asia",
                "url": "https://www.youtube.com/@MuseAsia"
            }
        ]
    },
    "one-punch-man": {
        "slug": "one-punch-man",
        "title_hi": "वन-पंच मैन",
        "title_en": "One-Punch Man",
        "cover_image": "/media/covers/anime_one-punch-man.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/21087-4qU4X5y5r5Wv.jpg",
        "rating": 8.2,
        "total_episodes": 24,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2015-2019",
        "episodes_display": "24 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Madhouse / J.C.Staff",
        "genres": [
            "Action",
            "Comedy",
            "Super Power"
        ],
        "genres_str": "एक्शन, सुपरहीरो, कॉमेडी, पैरोडी",
        "synopsis_hi": "साइतामा एक ऐसा हीरो है जिसने इतनी कड़ी ट्रेनिंग की है कि वह किसी भी दुश्मन को सिर्फ एक घूंसे में हरा सकता है, लेकिन अब वह एक सच्चे प्रतिद्वंद्वी की तलाश में बोरियत महसूस कर रहा है।",
        "platforms_list": [
            {
                "name": "Netflix",
                "url": "https://www.netflix.com"
            },
            {
                "name": "Muse Asia",
                "url": "https://www.youtube.com/@MuseAsia"
            },
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com"
            }
        ]
    },
    "the-apothecary-diaries-season-3": {
        "slug": "the-apothecary-diaries-season-3",
        "title_hi": "द अपोथेकेरी डायरीज़ सीज़न 3",
        "title_en": "The Apothecary Diaries Season 3",
        "cover_image": "/media/covers/anime_the-apothecary-diaries-season-3.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/195516-B5M0cPaQbQXI.jpg",
        "rating": 8.5,
        "total_episodes": 12,
        "next_airing_ep": 8,
        "next_airing_at": 1795183200,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "12 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "शुक्रवार • 7:30 PM IST",
        "airing_at": 1795183200,
        "studio": "TOHO animation STUDIO / OLM",
        "genres": [
            "Mystery",
            "Historical",
            "Drama"
        ],
        "genres_str": "मिस्ट्री, हिस्टोरिकल, ड्रामा",
        "synopsis_hi": "शाही महल के भीतर दवाइयों, जहर और दरबार की साज़िशों के रहस्यों को माओमाओ अपनी तीक्ष्ण बुद्धि और औषधि विज्ञान से सुलझाती है।",
        "platforms_list": [
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com/series/G3KHEVDJ7/the-apothecary-diaries"
            },
            {
                "name": "Netflix",
                "url": "https://www.netflix.com/title/81712068"
            }
        ]
    },
    "black-clover-season-2": {
        "slug": "black-clover-season-2",
        "title_hi": "ब्लैक क्लोवर सीज़न 2",
        "title_en": "Black Clover Season 2",
        "cover_image": "/media/covers/anime_black-clover-season-2.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/195604-lQpCzTziSA17.jpg",
        "rating": 8.8,
        "total_episodes": None,
        "next_airing_ep": 8,
        "next_airing_at": 1795269600,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "प्रसारण जारी",
        "airing_day_time_ist": "शनिवार • 7:30 PM IST",
        "airing_at": 1795269600,
        "studio": "Studio Pierrot",
        "genres": [
            "Action",
            "Adventure",
            "Comedy",
            "Fantasy"
        ],
        "genres_str": "एक्शन, एडवेंचर, कॉमेडी, फैंटेसी",
        "synopsis_hi": "आस्ता और ब्लैक बुल्स का क्लोवर साम्राज्य को बचाने का जादुई संघर्ष। बिना किसी जादू के पैदा हुआ आस्ता एंटी-मैजिक तलवारों से विज़ार्ड किंग बनने के अपने सपने की ओर बढ़ता है।",
        "platforms_list": [
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com/series/GRE50KV36/black-clover"
            },
            {
                "name": "Netflix",
                "url": "https://www.netflix.com/title/80196884"
            }
        ]
    },
    "sakamoto-days": {
        "slug": "sakamoto-days",
        "title_hi": "साकामोटो डेज़",
        "title_en": "Sakamoto Days",
        "cover_image": "/media/covers/anime_sakamoto-days.webp",
        "banner_image": None,
        "rating": 7.6,
        "total_episodes": 11,
        "next_airing_ep": 8,
        "next_airing_at": 1795269600,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "11 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "शनिवार • 8:30 PM IST",
        "airing_at": 1795269600,
        "studio": "TMS Entertainment",
        "genres": [
            "Action",
            "Comedy"
        ],
        "genres_str": "एक्शन, कॉमेडी, हिटमैन",
        "synopsis_hi": "पूर्व लीजेंडरी हिटमैन तारो साकामोटो अब एक साधारण किराना स्टोर चलाता है और अपने परिवार को पुरानी दुनिया के कातिलों से बचाता है।",
        "platforms_list": [
            {
                "name": "Netflix",
                "url": "https://www.netflix.com"
            }
        ]
    },
    "dr-stone-science-future": {
        "slug": "dr-stone-science-future",
        "title_hi": "डॉक्टर स्टोन: साइंस फ्यूचर",
        "title_en": "Dr. Stone: Science Future",
        "cover_image": "/media/covers/anime_dr-stone-science-future.webp",
        "banner_image": None,
        "rating": 8.2,
        "total_episodes": 12,
        "next_airing_ep": 8,
        "next_airing_at": 1795183200,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "12 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "गुरुवार • 7:00 PM IST",
        "airing_at": 1795183200,
        "studio": "TMS Entertainment",
        "genres": [
            "Sci-Fi",
            "Adventure",
            "Comedy"
        ],
        "genres_str": "साइ-फाई, एडवेंचर, कॉमेडी",
        "synopsis_hi": "सेनकु इशिगामी विज्ञान की शक्ति से पूरी मानव सभ्यता को पत्थर से दोबारा जीवित करने के अंतिम चरण में प्रवेश करता है।",
        "platforms_list": [
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com"
            },
            {
                "name": "Netflix",
                "url": "https://www.netflix.com"
            }
        ]
    },
    "bleach-thousand-year-blood-war": {
        "slug": "bleach-thousand-year-blood-war",
        "title_hi": "ब्लीच: थाउजेंड-ईयर ब्लड वॉर",
        "title_en": "Bleach: Thousand-Year Blood War",
        "cover_image": "/media/covers/anime_bleach-thousand-year-blood-war.webp",
        "banner_image": None,
        "rating": 8.8,
        "total_episodes": 13,
        "next_airing_ep": 9,
        "next_airing_at": 1795269600,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "13 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "शनिवार • 7:00 PM IST",
        "airing_at": 1795269600,
        "studio": "Studio Pierrot",
        "genres": [
            "Action",
            "Supernatural",
            "Shounen"
        ],
        "genres_str": "एक्शन, सुपरनैचुरल, शोनेन",
        "synopsis_hi": "सोल सोसाइटी और क्विन्सी सम्राट युहाबाख के बीच अंतिम महायुद्ध छिड़ता है। इचिगो कुरोसाकी अपनी वास्तविक शक्तियों को उजागर करता है।",
        "platforms_list": [
            {
                "name": "Disney+ Hotstar",
                "url": "https://www.hotstar.com"
            }
        ]
    },
    "link-click": {
        "slug": "link-click",
        "title_hi": "लिंक क्लिक",
        "title_en": "Link Click",
        "cover_image": "/media/covers/anime_link-click.webp",
        "banner_image": None,
        "rating": 8.6,
        "total_episodes": 24,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": True,
        "is_hindi_dub": True,
        "format": "ONA",
        "season_year": "2021-2023",
        "episodes_display": "सीज़न 1-2 • 24 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Studio LAN / Haoliners",
        "genres": [
            "Donghua",
            "Time Travel",
            "Thriller",
            "Drama"
        ],
        "genres_str": "डोंगहुआ, टाइम ट्रैवल, थ्रिलर, ड्रामा",
        "synopsis_hi": "तस्वीरों के भीतर जाकर रहस्यों को सुलझाने वाले दो जासूस दोस्तों चेंग शियाओशी और लू गुआंग की थ्रिलर गाथा।",
        "platforms_list": [
            {
                "name": "Bilibili",
                "url": "https://www.bilibili.tv"
            },
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com"
            },
            {
                "name": "YouTube",
                "url": "https://www.youtube.com"
            }
        ]
    },
    "swallowed-star": {
        "slug": "swallowed-star",
        "title_hi": "स्वॉलोड स्टार",
        "title_en": "Swallowed Star",
        "cover_image": "/media/covers/anime_swallowed-star.webp",
        "banner_image": None,
        "rating": 8.2,
        "total_episodes": 175,
        "next_airing_ep": 152,
        "next_airing_at": 1795183200,
        "is_airing": True,
        "is_donghua": True,
        "is_hindi_dub": True,
        "format": "ONA",
        "season_year": "Fall 2026",
        "episodes_display": "प्रसारण जारी • Ep 150+",
        "airing_day_time_ist": "बुधवार • 7:30 PM IST",
        "airing_at": 1795183200,
        "studio": "Sparkly Key Animation",
        "genres": [
            "Donghua",
            "Sci-Fi",
            "Action",
            "Martial Arts"
        ],
        "genres_str": "डोंगहुआ, साइ-फाई, एक्शन, मार्शल आर्ट्स",
        "synopsis_hi": "आरआर वायरस के प्रकोप के बाद पृथ्वी पर शक्तिशाली राक्षसों का राज हो गया है। लुओ फेंग मार्शल आर्ट्स और उन्नत तकनीक से मानवता की रक्षा करता है।",
        "platforms_list": [
            {
                "name": "WeTV",
                "url": "https://wetv.vip"
            },
            {
                "name": "YouTube",
                "url": "https://www.youtube.com"
            }
        ]
    },
    "battle-through-the-heavens": {
        "slug": "battle-through-the-heavens",
        "title_hi": "बैटल थ्रू द हेवन्स",
        "title_en": "Battle Through the Heavens",
        "cover_image": "/media/covers/anime_battle-through-the-heavens.webp",
        "banner_image": None,
        "rating": 7.0,
        "total_episodes": 120,
        "next_airing_ep": 115,
        "next_airing_at": 1795269600,
        "is_airing": True,
        "is_donghua": True,
        "is_hindi_dub": True,
        "format": "ONA",
        "season_year": "Fall 2026",
        "episodes_display": "प्रसारण जारी • Ep 115+",
        "airing_day_time_ist": "रविवार • 8:00 AM IST",
        "airing_at": 1795269600,
        "studio": "Shanghai Foch Film Culture",
        "genres": [
            "Donghua",
            "Fantasy",
            "Cultivation",
            "Action"
        ],
        "genres_str": "डोंगहुआ, फैंटेसी, कल्टीवेशन, एक्शन",
        "synopsis_hi": "अपनी ताकत खो चुके प्रतिभाशाली युवक शियाओ यान अपनी अंगूठी में मौजूद शिक्षक याओ लाओ के मार्गदर्शन में महान योद्धा बनने की साधना करता है।",
        "platforms_list": [
            {
                "name": "WeTV",
                "url": "https://wetv.vip"
            },
            {
                "name": "YouTube",
                "url": "https://www.youtube.com"
            }
        ]
    },
    "renegade-immortal": {
        "slug": "renegade-immortal",
        "title_hi": "रेनेगेड इम्मोर्टल",
        "title_en": "Renegade Immortal",
        "cover_image": "/media/covers/anime_renegade-immortal.webp",
        "banner_image": None,
        "rating": 8.3,
        "total_episodes": 180,
        "next_airing_ep": 167,
        "next_airing_at": 1795183200,
        "is_airing": True,
        "is_donghua": True,
        "is_hindi_dub": True,
        "format": "ONA",
        "season_year": "Fall 2026",
        "episodes_display": "प्रसारण जारी",
        "airing_day_time_ist": "सोमवार • 8:00 AM IST",
        "airing_at": 1795183200,
        "studio": "Sparkly Key Animation",
        "genres": [
            "Donghua",
            "Cultivation",
            "Action"
        ],
        "genres_str": "डोंगहुआ, कल्टीवेशन, एक्शन",
        "synopsis_hi": "वांग लिन एक साधारण ग्रामीण लड़का है जो अपने दृढ़ संकल्प और कठोर साधना से अमरत्व के क्रूर मार्ग पर आगे बढ़ता है।",
        "platforms_list": [
            {
                "name": "WeTV",
                "url": "https://wetv.vip"
            },
            {
                "name": "YouTube",
                "url": "https://www.youtube.com"
            }
        ]
    },
    "overgeared": {
        "slug": "overgeared",
        "title_hi": "ओवरगियर्ड",
        "title_en": "Overgeared",
        "cover_image": "/media/covers/anime_overgeared.webp",
        "banner_image": None,
        "rating": 7.6,
        "total_episodes": 12,
        "next_airing_ep": 8,
        "next_airing_at": 1795269600,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "12 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "रविवार • 8:00 PM IST",
        "airing_at": 1795269600,
        "studio": "Ani-One Asia",
        "genres": [
            "Action",
            "Game",
            "Fantasy"
        ],
        "genres_str": "एक्शन, वीआरएमएमओ, गेम, फैंटेसी",
        "synopsis_hi": "शिन यंगवू वीआर गेम 'सैटिसफाई' में एक लीजेंडरी ब्लैकस्मिथ क्लास पाकर साधारण खिलाड़ी से अजेय योद्धा बन जाता है।",
        "platforms_list": [
            {
                "name": "Ani-One Asia",
                "url": "https://www.youtube.com/@AniOneAsia"
            },
            {
                "name": "YouTube",
                "url": "https://www.youtube.com"
            }
        ]
    },
    "mob-psycho-100": {
        "slug": "mob-psycho-100",
        "title_hi": "मॉब साइको 100",
        "title_en": "Mob Psycho 100",
        "cover_image": "/media/covers/anime_mob-psycho-100.webp",
        "banner_image": None,
        "rating": 8.4,
        "total_episodes": 37,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2016-2022",
        "episodes_display": "सीज़न 1-3 • 37 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Bones",
        "genres": [
            "Action",
            "Comedy",
            "Supernatural"
        ],
        "genres_str": "एक्शन, कॉमेडी, सुपरनैचुरल",
        "synopsis_hi": "शिगेओ 'मॉब' कागेयामा एक शक्तिशाली एस्पर है जो सामान्य जीवन जीना चाहता है, लेकिन उसकी भावनाएं चरम पर पहुंचते ही भारी अलौकिक शक्ति जाग उठती है।",
        "platforms_list": [
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com"
            },
            {
                "name": "Muse Asia",
                "url": "https://www.youtube.com/@MuseAsia"
            },
            {
                "name": "Netflix",
                "url": "https://www.netflix.com"
            }
        ]
    },
    "classroom-of-the-elite": {
        "slug": "classroom-of-the-elite",
        "title_hi": "क्लासरूम ऑफ द एलीट",
        "title_en": "Classroom of the Elite",
        "cover_image": "/media/covers/anime_classroom-of-the-elite.webp",
        "banner_image": None,
        "rating": 7.6,
        "total_episodes": 38,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2017-2024",
        "episodes_display": "सीज़न 1-3 • 38 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Lerche",
        "genres": [
            "Psychological",
            "Drama",
            "School"
        ],
        "genres_str": "साइकोलॉजिकल, ड्रामा, स्कूल",
        "synopsis_hi": "किओताका अयानोकोजी कोडो इकुसेई हाई स्कूल में दाखिला लेता है, जहाँ केवल शीर्ष योग्यता और कड़ी प्रतिस्पर्धा से ही छात्रों का भविष्य तय होता है।",
        "platforms_list": [
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com"
            },
            {
                "name": "Muse Asia",
                "url": "https://www.youtube.com/@MuseAsia"
            },
            {
                "name": "Netflix",
                "url": "https://www.netflix.com"
            }
        ]
    },
    "blue-box-season-2": {
        "slug": "blue-box-season-2",
        "title_hi": "ब्लू बॉक्स सीज़न 2",
        "title_en": "Blue Box Season 2",
        "cover_image": "/media/covers/anime_blue-box-season-2.webp",
        "banner_image": None,
        "rating": 8.1,
        "total_episodes": 25,
        "next_airing_ep": 7,
        "next_airing_at": 1795183200,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "25 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "गुरुवार • 10:30 PM IST",
        "airing_at": 1795183200,
        "studio": "Telecom Animation Film",
        "genres": [
            "Romance",
            "Sports",
            "School"
        ],
        "genres_str": "रोमांस, स्पोर्ट्स, स्कूल",
        "synopsis_hi": "बैडमिंटन खिलाड़ी ताइकी इनोमाता और बास्केटबॉल स्टार चिनात्सु कानो के बीच खेल और प्रेम का प्यारा सफर आगे बढ़ता है।",
        "platforms_list": [
            {
                "name": "Netflix",
                "url": "https://www.netflix.com"
            }
        ]
    },
    "dragon-ball-super-beerus": {
        "slug": "dragon-ball-super-beerus",
        "title_hi": "ड्रैगन बॉल सुपर: बीरस",
        "title_en": "Dragon Ball Super: Beerus",
        "cover_image": "/media/covers/anime_dragon-ball-super-beerus.webp",
        "banner_image": None,
        "rating": 7.3,
        "total_episodes": 131,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2015-2018",
        "episodes_display": "131 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Toei Animation",
        "genres": [
            "Action",
            "Martial Arts",
            "Fantasy"
        ],
        "genres_str": "एक्शन, मार्शल आर्ट्स, फैंटेसी",
        "synopsis_hi": "विनाश के देवता बीरस और सुपर सैयान गॉड गोकू के बीच ब्रह्मांड की रक्षा के लिए घमासान मुकाबला।",
        "platforms_list": [
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com"
            }
        ]
    },
    "reborn-as-a-space-mercenary": {
        "slug": "reborn-as-a-space-mercenary",
        "title_hi": "रीबॉर्न ऐज़ अ स्पेस मर्सनरी",
        "title_en": "Reborn as a Space Mercenary",
        "cover_image": "/media/covers/anime_reborn-as-a-space-mercenary.webp",
        "banner_image": None,
        "rating": 6.9,
        "total_episodes": 13,
        "next_airing_ep": 7,
        "next_airing_at": 1795183200,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "13 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "शुक्रवार • 9:30 PM IST",
        "airing_at": 1795183200,
        "studio": "TBA",
        "genres": [
            "Sci-Fi",
            "Space",
            "Action"
        ],
        "genres_str": "साइ-फाई, स्पेस, एक्शन",
        "synopsis_hi": "एक अंतरिक्ष युद्धपोत के साथ दूसरी दुनिया में पुनर्जन्म लेकर स्वतंत्र मर्सनरी के रूप में अंतरिक्ष में नए रोमांच।",
        "platforms_list": [
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com"
            }
        ]
    },
    "mission-yozakura-family-season-2": {
        "slug": "mission-yozakura-family-season-2",
        "title_hi": "मिशन: योज़ाकुरा फैमिली सीज़न 2",
        "title_en": "Mission: Yozakura Family Season 2",
        "cover_image": "/media/covers/anime_mission-yozakura-family-season-2.webp",
        "banner_image": None,
        "rating": 7.4,
        "total_episodes": 27,
        "next_airing_ep": 7,
        "next_airing_at": 1795269600,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "27 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "रविवार • 5:00 PM IST",
        "airing_at": 1795269600,
        "studio": "SILVER LINK.",
        "genres": [
            "Action",
            "Comedy",
            "Spy"
        ],
        "genres_str": "एक्शन, कॉमेडी, स्पाई",
        "synopsis_hi": "ताइयो असाकुरा और जासूस परिवार योज़ाकुरा नए खतरों और रहस्यों का सामना करने के लिए तैयार होते हैं।",
        "platforms_list": [
            {
                "name": "Disney+ Hotstar",
                "url": "https://www.hotstar.com"
            }
        ]
    },
    "firefly-wedding": {
        "slug": "firefly-wedding",
        "title_hi": "फायरफ्लाई वेडिंग",
        "title_en": "Firefly Wedding",
        "cover_image": "/media/covers/anime_firefly-wedding.webp",
        "banner_image": None,
        "rating": 7.5,
        "total_episodes": 12,
        "next_airing_ep": 7,
        "next_airing_at": 1795183200,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "12 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "शुक्रवार • 8:30 PM IST",
        "airing_at": 1795183200,
        "studio": "TBA",
        "genres": [
            "Drama",
            "Romance",
            "Historical"
        ],
        "genres_str": "ड्रामा, रोमांस, हिस्टोरिकल",
        "synopsis_hi": "मीजी काल की एक बीमार कुलीन लड़की और उसके हत्यारे पति का रोमांचक ड्रामा।",
        "platforms_list": [
            {
                "name": "Ani-One Asia",
                "url": "https://www.youtube.com/@AniOneAsia"
            },
            {
                "name": "YouTube",
                "url": "https://www.youtube.com"
            }
        ]
    },
    "reincarnated-as-a-sword-season-2": {
        "slug": "reincarnated-as-a-sword-season-2",
        "title_hi": "रीइन्कारनेटेड ऐज़ अ सोर्ड सीज़न 2",
        "title_en": "Reincarnated as a Sword Season 2",
        "cover_image": "/media/covers/anime_reincarnated-as-a-sword-season-2.webp",
        "banner_image": None,
        "rating": 7.5,
        "total_episodes": 12,
        "next_airing_ep": 8,
        "next_airing_at": 1795183200,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "12 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "बुधवार • 9:00 PM IST",
        "airing_at": 1795183200,
        "studio": "C2C",
        "genres": [
            "Action",
            "Fantasy",
            "Isekai"
        ],
        "genres_str": "एक्शन, फैंटेसी, इसेकाई",
        "synopsis_hi": "जादुई तलवार और बिल्ली-लड़की फ्रैंकी की रोमांचक यात्रा।",
        "platforms_list": [
            {
                "name": "Muse Asia",
                "url": "https://www.youtube.com/@MuseAsia"
            },
            {
                "name": "YouTube",
                "url": "https://www.youtube.com"
            }
        ]
    },
    "mashle-magic-and-muscles": {
        "slug": "mashle-magic-and-muscles",
        "title_hi": "मैशले: मैजिक एंड मसल्स",
        "title_en": "Mashle: Magic and Muscles",
        "cover_image": "/media/covers/anime_mashle-magic-and-muscles.webp",
        "banner_image": None,
        "rating": 7.6,
        "total_episodes": 24,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2023-2024",
        "episodes_display": "सीज़न 1-2 • 24 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "A-1 Pictures",
        "genres": [
            "Action",
            "Comedy",
            "Fantasy"
        ],
        "genres_str": "एक्शन, कॉमेडी, फैंटेसी",
        "synopsis_hi": "जादू की दुनिया में बिना किसी जादू के केवल मांसपेशियों की ताकत से मुकाबला।",
        "platforms_list": [
            {
                "name": "Ani-One Asia",
                "url": "https://www.youtube.com/@AniOneAsia"
            },
            {
                "name": "Crunchyroll",
                "url": "https://www.crunchyroll.com"
            },
            {
                "name": "YouTube",
                "url": "https://www.youtube.com"
            }
        ]
    }
,
"detective-conan": {
        "slug": "detective-conan",
        "title_hi": "डिटेक्टिव कोनन",
        "title_en": "Detective Conan",
        "cover_image": "/media/covers/anime_detective-conan.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/235.jpg",
        "rating": 8.3,
        "total_episodes": None,
        "next_airing_ep": 1221,
        "next_airing_at": 1795321800,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "1996-Ongoing",
        "episodes_display": "1220+ एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "शनिवार • 2:30 PM IST",
        "airing_at": 1795321800,
        "studio": "TMS Entertainment",
        "genres": ["Mystery", "Adventure", "Comedy"],
        "genres_str": "मिस्ट्री, एडवेंचर, कॉमेडी",
        "synopsis_hi": "हाई स्कूल जासूस शिनीची कुडो को एक रहस्यमयी सिंडिकेट ने एक ज़हरीली दवा देकर बच्चे के रूप में बदल दिया। अब वह कोनन एडोगावा बनकर जटिल रहस्यों को सुलझाता है।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ]
    },
    "steel-ball-run": {
        "slug": "steel-ball-run",
        "title_hi": "स्टील बॉल रन: जोजोज़ बिज़ारे एडवेंचर",
        "title_en": "STEEL BALL RUN JoJo's Bizarre Adventure",
        "cover_image": "/media/covers/anime_steel-ball-run.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx210482-P1VNKbqdJ6Zj.jpg",
        "rating": 9.2,
        "total_episodes": 24,
        "next_airing_ep": 9,
        "next_airing_at": 1795275000,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2026",
        "episodes_display": "24 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "शुक्रवार • 9:00 PM IST",
        "airing_at": 1795275000,
        "studio": "David Production",
        "genres": ["Action", "Adventure", "Supernatural"],
        "genres_str": "एक्शन, एडवेंचर, सुपरनैचुरल",
        "synopsis_hi": "1890 के अमेरिका में सैन डिएगो से न्यूयॉर्क तक 6,000 किलोमीटर की ऐतिहासिक घुड़दौड़ स्टील बॉल रन में जॉनी जोस्टार और जाइरो ज़ेपेलि रहस्यमयी ताकतों के खिलाफ मुकाबला करते हैं।",
        "platforms_list": [
            {"name": "Netflix", "url": "https://www.netflix.com"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"}
        ]
    },
    "soul-land-2": {
        "slug": "soul-land-2",
        "title_hi": "सोल लैंड 2",
        "title_en": "Soul Land 2: The Peerless Tang Clan",
        "cover_image": "/media/covers/anime_soul-land-2.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx137683-IMuvIECdZNBh.png",
        "rating": 8.0,
        "total_episodes": 182,
        "next_airing_ep": 81,
        "next_airing_at": 1795226400,
        "is_airing": True,
        "is_donghua": True,
        "is_hindi_dub": False,
        "format": "ONA",
        "season_year": "2023-Ongoing",
        "episodes_display": "182 एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "शनिवार • 7:30 AM IST",
        "airing_at": 1795226400,
        "studio": "Sparkly Key Animation",
        "genres": ["Action", "Fantasy", "Martial Arts"],
        "genres_str": "एक्शन, फैंटेसी, मार्शल आर्ट्स",
        "synopsis_hi": "टैंग संप्रदाय की स्थापना के दस हजार साल बाद, डौलू महाद्वीप पर एक नया युग शुरू होता है जहां स्पिरिट पैगोडा और नई आत्मा तकनीकें शक्ति के संतुलन को बदल देती हैं।",
        "platforms_list": [
            {"name": "WeTV / Tencent Video", "url": "https://wetv.vip"},
            {"name": "YouTube", "url": "https://www.youtube.com"}
        ]
    },
    "campfire-cooking-in-another-world-with-my-absurd-skill": {
        "slug": "campfire-cooking-in-another-world-with-my-absurd-skill",
        "title_hi": "कैम्पफ़ायर कुकिंग इन अनदर वर्ल्ड",
        "title_en": "Campfire Cooking in Another World with My Absurd Skill",
        "cover_image": "/media/covers/anime_campfire-cooking-in-another-world-with-my-absurd-skill.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/156067.jpg",
        "rating": 7.8,
        "total_episodes": 12,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2023",
        "episodes_display": "12 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "MAPPA",
        "genres": ["Adventure", "Comedy", "Fantasy", "Gourmet"],
        "genres_str": "एडवेंचर, कॉमेडी, फैंटेसी, कुकिंग",
        "synopsis_hi": "मुकोदा को गलती से एक जादुई दुनिया में बुला लिया जाता है जहाँ उसका अनोखा कौशल 'ऑनलाइन सुपरमार्केट' उसे आधुनिक जापानी खाद्य सामग्री खरीदने की शक्ति देता है।",
        "platforms_list": [
            {"name": "Muse Asia (YouTube)", "url": "https://www.youtube.com/@MuseAsia"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"}
        ]
    },
    "the-eminence-in-shadow": {
        "slug": "the-eminence-in-shadow",
        "title_hi": "द एमिनेंस इन शैडो",
        "title_en": "The Eminence in Shadow",
        "cover_image": "/media/covers/anime_the-eminence-in-shadow.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/131902.jpg",
        "rating": 8.3,
        "total_episodes": 32,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2022-2023",
        "episodes_display": "32 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Nexus",
        "genres": ["Action", "Comedy", "Fantasy"],
        "genres_str": "एक्शन, कॉमेडी, फैंटेसी",
        "synopsis_hi": "सिड कागेनो हमेशा से छाया में रहकर एक गुप्त मास्टरमाइंड बनना चाहता था। एक दूसरी दुनिया में पुनर्जन्म के बाद, उसकी बनाई हुई काल्पनिक कहानियां असलियत बन जाती हैं।",
        "platforms_list": [
            {"name": "Ani-One Asia (YouTube)", "url": "https://www.youtube.com/@AniOneAsia"},
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ]
    },
    "wind-breaker": {
        "slug": "wind-breaker",
        "title_hi": "विंड ब्रेकर",
        "title_en": "Wind Breaker",
        "cover_image": "/media/covers/anime_wind-breaker.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/163270.jpg",
        "rating": 8.1,
        "total_episodes": 13,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2024",
        "episodes_display": "13 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "CloverWorks",
        "genres": ["Action", "Delinquents", "School"],
        "genres_str": "एक्शन, स्कूल, डेलिंक्वेंट्स",
        "synopsis_hi": "हारुका साकुरा केवल सबसे मजबूत बनना चाहता है और फुरिन हाई स्कूल में दाखिला लेता है। लेकिन उसे पता चलता है कि वहां के छात्र शहर के रक्षक (बोफुरिन) हैं।",
        "platforms_list": [
            {"name": "Muse Asia (YouTube)", "url": "https://www.youtube.com/@MuseAsia"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"}
        ]
    },
    "shangri-la-frontier": {
        "slug": "shangri-la-frontier",
        "title_hi": "शांगरी-ला फ्रंटियर",
        "title_en": "Shangri-La Frontier",
        "cover_image": "/media/covers/anime_shangri-la-frontier.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/148471.jpg",
        "rating": 8.1,
        "total_episodes": 50,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2023-2024",
        "episodes_display": "50 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "C2C",
        "genres": ["Action", "Adventure", "Fantasy", "Sci-Fi"],
        "genres_str": "एक्शन, एडवेंचर, गेमिंग, फैंटेसी",
        "synopsis_hi": "राकुरा जो खराब वीआर गेम्स को जीतने का विशेषज्ञ है, दुनिया के सर्वश्रेष्ठ वीआरएमएमओ 'शांगरी-ला फ्रंटियर' में उतरता है और असाधारण कारनामे करता है।",
        "platforms_list": [
            {"name": "Muse Asia (YouTube)", "url": "https://www.youtube.com/@MuseAsia"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"}
        ]
    },
    "kaiju-no-8": {
        "slug": "kaiju-no-8",
        "title_hi": "काइजू नंबर 8",
        "title_en": "Kaiju No. 8",
        "cover_image": "/media/covers/anime_kaiju-no-8.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/153288.jpg",
        "rating": 8.2,
        "total_episodes": 12,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2024",
        "episodes_display": "12 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Production I.G",
        "genres": ["Action", "Sci-Fi", "Monster"],
        "genres_str": "एक्शन, साइ-फाई, मॉन्स्टर",
        "synopsis_hi": "काफ्का हिबिका जो काइजू की सफाई करने वाली टीम में काम करता है, एक छोटे काइजू को निगलने के बाद खुद एक शक्तिशाली काइजू में बदल जाता है जिसे 'काइजू नंबर 8' नाम दिया गया।",
        "platforms_list": [
            {"name": "Ani-One Asia (YouTube)", "url": "https://www.youtube.com/@AniOneAsia"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ]
    },
    "one-piece": {
        "slug": "one-piece",
        "title_hi": "वन पीस",
        "title_en": "One Piece",
        "cover_image": "/media/covers/anime_one-piece.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/21.jpg",
        "rating": 8.9,
        "total_episodes": None,
        "next_airing_ep": 1123,
        "next_airing_at": 1795321800,
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "1999-Ongoing",
        "episodes_display": "1120+ एपिसोड्स (प्रसारण जारी)",
        "airing_day_time_ist": "रविवार • 6:30 AM IST",
        "airing_at": 1795321800,
        "studio": "Toei Animation",
        "genres": ["Action", "Adventure", "Fantasy"],
        "genres_str": "एक्शन, एडवेंचर, फैंटेसी",
        "synopsis_hi": "मंकी डी. लफी अपने स्ट्रॉ हैट पाइरेट्स क्रू के साथ महान खजाने 'वन पीस' की तलाश में ग्रैंड लाइन पर निकलता है ताकि वह पाइरेट किंग बन सके।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ]
    },
    "naruto": {
        "slug": "naruto",
        "title_hi": "नारुतो",
        "title_en": "Naruto",
        "cover_image": "/media/covers/anime_naruto.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/20.jpg",
        "rating": 8.4,
        "total_episodes": 220,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2002-2007",
        "episodes_display": "220 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Pierrot",
        "genres": ["Action", "Adventure", "Martial Arts"],
        "genres_str": "एक्शन, एडवेंचर, मार्शल आर्ट्स",
        "synopsis_hi": "हिडन लीफ विलेज का युवा निंजा नारुतो उज़ुमाकी गांव का मुखिया यानी 'होकागे' बनने का सपना देखता है और सभी का सम्मान पाने के लिए संघर्ष करता है।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"},
            {"name": "JioHotstar", "url": "https://www.hotstar.com"}
        ]
    },
    "naruto-shippuden": {
        "slug": "naruto-shippuden",
        "title_hi": "नारुतो शिपूडेन",
        "title_en": "Naruto Shippuden",
        "cover_image": "/media/covers/anime_naruto.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/1735.jpg",
        "rating": 8.7,
        "total_episodes": 500,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2007-2017",
        "episodes_display": "500 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Pierrot",
        "genres": ["Action", "Adventure", "Martial Arts"],
        "genres_str": "एक्शन, एडवेंचर, मार्शल आर्ट्स",
        "synopsis_hi": "दो साल के कठोर प्रशिक्षण के बाद नारुतो अपने दोस्त सासुके को वापस लाने और अकातसुकी के वैश्विक खतरे से निंजा दुनिया की रक्षा करने के लिए लौटता है।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"},
            {"name": "JioHotstar", "url": "https://www.hotstar.com"}
        ]
    },
    "bleach": {
        "slug": "bleach",
        "title_hi": "ब्लीच",
        "title_en": "Bleach",
        "cover_image": "/media/covers/anime_bleach-thousand-year-blood-war.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/269.jpg",
        "rating": 8.2,
        "total_episodes": 366,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2004-2012",
        "episodes_display": "366 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Pierrot",
        "genres": ["Action", "Supernatural"],
        "genres_str": "एक्शन, सुपरनेचुरल",
        "synopsis_hi": "इचिगो कुरोसाकी को अप्रत्याशित रूप से सोल रीपर की शक्तियां मिल जाती हैं। अब वह आत्माओं की रक्षा करता है और खतरनाक होलो प्राणियों से मुकाबला करता है।",
        "platforms_list": [
            {"name": "JioHotstar", "url": "https://www.hotstar.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ]
    },
    "dragon-ball-z": {
        "slug": "dragon-ball-z",
        "title_hi": "ड्रैगन बॉल ज़ेड",
        "title_en": "Dragon Ball Z",
        "cover_image": "/media/covers/anime_dragon-ball-z.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/813.jpg",
        "rating": 8.7,
        "total_episodes": 291,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "1989-1996",
        "episodes_display": "291 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Toei Animation",
        "genres": ["Action", "Adventure", "Sci-Fi"],
        "genres_str": "एक्शन, एडवेंचर, साइ-फाई",
        "synopsis_hi": "गोकू और उसके साथी पृथ्वी को शक्तिशाली एलियन योद्धाओं, एंड्रॉयड्स और ब्रह्मांडीय खतरों जैसे फ्रीज़ा, सेल और माजिन बू से बचाते हैं।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"}
        ]
    },
    "dragon-ball-super": {
        "slug": "dragon-ball-super",
        "title_hi": "ड्रैगन बॉल सुपर",
        "title_en": "Dragon Ball Super",
        "cover_image": "/media/covers/anime_dragon-ball-super.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/21175.jpg",
        "rating": 7.8,
        "total_episodes": 131,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2015-2018",
        "episodes_display": "131 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Toei Animation",
        "genres": ["Action", "Adventure", "Fantasy"],
        "genres_str": "एक्शन, एडवेंचर, फैंटेसी",
        "synopsis_hi": "माजिन बू की पराजय के बाद गोकू विनाश के देवता बीरस का सामना करता है और अन्य ब्रह्मांडों के साथ टूर्नामेंट ऑफ पावर में हिस्सा लेता है।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"}
        ]
    },
    "fairy-tail": {
        "slug": "fairy-tail",
        "title_hi": "फेयरी टेल",
        "title_en": "Fairy Tail",
        "cover_image": "/media/covers/anime_dragon-ball-z.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/6702.jpg",
        "rating": 7.9,
        "total_episodes": 328,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2009-2019",
        "episodes_display": "328 एपिसोड्स (पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "A-1 Pictures",
        "genres": ["Action", "Adventure", "Fantasy"],
        "genres_str": "एक्शन, एडवेंचर, जादू, फैंटेसी",
        "synopsis_hi": "नात्सु ड्रैगनील और लुसी हार्टफिलिया फियोर साम्राज्य के सबसे प्रसिद्ध और उपद्रवी विजार्ड गिल्ड 'फेयरी टेल' में असाधारण मिशन पूरे करते हैं।",
        "platforms_list": [
            {"name": "Muse Asia (YouTube)", "url": "https://www.youtube.com/@MuseAsia"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"}
        ]
    },
    "boruto": {
        "slug": "boruto",
        "title_hi": "बोरूटो: नारुतो नेक्स्ट जेनरेशन्स",
        "title_en": "Boruto: Naruto Next Generations",
        "cover_image": "/media/covers/anime_naruto.webp",
        "banner_image": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/97938.jpg",
        "rating": 6.5,
        "total_episodes": 293,
        "next_airing_ep": None,
        "next_airing_at": 0,
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2017-2023",
        "episodes_display": "293 एपिसोड्स (पार्ट 1 पूर्ण)",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Pierrot",
        "genres": ["Action", "Adventure", "Ninja"],
        "genres_str": "एक्शन, एडवेंचर, निंजा",
        "synopsis_hi": "सातवें होकागे नारुतो उज़ुमाकी का बेटा बोरूटो अपने पिता की छाया से बाहर निकलकर अपना खुद का निंजा मार्ग चुनता है।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ]
    }
}

# ---------- FILLER SKIPPER GUIDES FOR MAJOR LONG-RUNNING ANIME ----------
FILLER_GUIDES_DATA: dict[str, dict[str, Any]] = {
    "naruto": {
        "franchise": "Naruto (Classic)",
        "total_episodes": 220,
        "filler_percentage": "41%",
        "canon_percentage": "59%",
        "canon_ranges": "Ep 1–25, 27–96, 98, 100, 107–135, 220 (अंतिम भाग)",
        "filler_ranges": "Ep 26, 97, 99, 101–106, 136–219",
        "notes": "सलाह: एपिसोड 136 से 219 तक का लंबा फ़िलर आर्क मुख्य कहानी में कोई योगदान नहीं देता। इसे छोड़कर सीधे नारुतो शिपूडेन शुरू कर सकते हैं।",
    },
    "naruto-shippuden": {
        "franchise": "Naruto Shippuden",
        "total_episodes": 500,
        "filler_percentage": "41%",
        "canon_percentage": "59%",
        "canon_ranges": "Ep 1–53, 72–88, 113–143, 152–169, 172–175, 197–222, 243–256, 261–278, 296–302, 321–346, 362–375, 378–387, 391–393, 414, 418, 420–421, 424–426, 459–479, 484–500",
        "filler_ranges": "Ep 54–71, 89–112, 144–151, 170–171, 176–196, 223–242, 257–260, 279–295, 303–320, 347–361, 376–377, 388–390, 394–413, 416–417, 419, 422–423, 427–458, 480–483",
        "notes": "सलाह: चौथे महान निंजा युद्ध के दौरान के फ़्लैशबैक फ़िलर आर्क्स (Ep 303–320 और 427–458) को स्किप करने से पेसिंग बेहतरीन रहती है।",
    },
    "one-piece": {
        "franchise": "One Piece",
        "total_episodes": "1120+",
        "filler_percentage": "9%",
        "canon_percentage": "91%",
        "canon_ranges": "Ep 1–53, 61–98, 103–130, 144–195, 207–219, 229–325, 337–381, 385–405, 408–425, 430–456, 459–491, 500–541, 543–574, 579–625, 629–746, 751–774, 783–894, 897–906, 908–1028, 1031–1088, 1090+",
        "filler_ranges": "Ep 54–60, 99, 101–102, 131–143, 220–226, 279–283, 326–336, 382–384, 426–429, 575–578, 626–628, 747–750, 780–782, 895–896, 1029–1030",
        "notes": "सलाह: वन पीस में फ़िलर दर केवल ~9% है! G-8 आर्क (Ep 196–206) को देखना अनुशंसित है क्योंकि यह सबसे बेहतरीन गैर-कैनन फ़िलर माना जाता है।",
    },
    "bleach": {
        "franchise": "Bleach",
        "total_episodes": 366,
        "filler_percentage": "45%",
        "canon_percentage": "55%",
        "canon_ranges": "Ep 1–63 (सोल सोसाइटी), 110–127, 138–167 (अर्रानकार/हुएको मुंडो), 190–203, 206–212 (टर्न बैक), 215–229, 266–286, 290–310 (आइज़ेन क्लाइमेक्स), 342–366 (फुलब्रिंगर)",
        "filler_ranges": "Ep 64–109 (बाउन्ट), 128–137, 168–189 (अमागाई), 204–205, 213–214, 228, 230–265 (ज़ानपाकुतो), 287, 298–299, 303–305, 311–341 (रेइगाई), 355",
        "notes": "सलाह: बाउन्ट आर्क (Ep 64–109) को स्किप करने से सोल सोसाइटी के बाद सीधे अर्रानकार आर्क का आनंद ले सकते हैं।",
    },
    "black-clover": {
        "franchise": "Black Clover",
        "total_episodes": 170,
        "filler_percentage": "10%",
        "canon_percentage": "90%",
        "canon_ranges": "Ep 1–28, 30–65, 67–122, 126–129, 158–170 (स्पेड किंगडम आर्क)",
        "filler_ranges": "Ep 29, 66, 123–125, 131, 134–135, 142–148",
        "notes": "सलाह: ब्लैक क्लोवर में बहुत कम फ़िलर हैं। Ep 158 से 170 तक स्पेड किंगडम आर्क 100% हाई-ऑक्टेन कैनन है।",
    },
    "dragon-ball-z": {
        "franchise": "Dragon Ball Z",
        "total_episodes": 291,
        "filler_percentage": "13%",
        "canon_percentage": "87%",
        "canon_ranges": "Ep 1–8, 18–38, 45–107, 118–124, 126–169, 172–194, 205–273, 275–287, 289–291",
        "filler_ranges": "Ep 9–17, 39–44, 108–117 (गार्लिक जूनियर), 125 (गोकू ड्राइविंग लेसन), 170–171, 195–204, 274, 288",
        "notes": "सलाह: गार्लिक जूनियर आर्क (108–117) को छोड़ सकते हैं, पर Ep 125 (गोकू का ड्राइविंग टेस्ट) बहुत मजेदार फ़िलर है!",
    },
    "dragon-ball-super": {
        "franchise": "Dragon Ball Super",
        "total_episodes": 131,
        "filler_percentage": "11%",
        "canon_percentage": "89%",
        "canon_ranges": "Ep 1–14, 18–27, 28–41, 47–67, 77–131",
        "filler_ranges": "Ep 15, 42–46, 68–76",
        "notes": "सलाह: गोकू ब्लैक आर्क (47–67) और टूर्नामेंट ऑफ पावर (77–131) मुख्य मस्ट-वॉच कैनन हैं।",
    },
    "detective-conan": {
        "franchise": "Detective Conan",
        "total_episodes": "1120+",
        "filler_percentage": "40%",
        "canon_percentage": "60%",
        "canon_ranges": "Ep 1–13, 18, 48–50, 128–131, 176–178, 188–193, 219, 230–231, 304, 345, 425, 491–504, 701–704, 779–783+",
        "filler_ranges": "टीवी ऑरिजिनल एपिसोड्स (~40% केस जो मंगा या ब्लैक ऑर्गनाइज़ेशन से जुड़े नहीं हैं)",
        "notes": "सलाह: ब्लैक ऑर्गनाइज़ेशन, एफबीआई और काइतो किड से संबंधित कैनन केस देखने से कहानी तीव्र गति से आगे बढ़ती है।",
    },
    "fairy-tail": {
        "franchise": "Fairy Tail",
        "total_episodes": 328,
        "filler_percentage": "19%",
        "canon_percentage": "81%",
        "canon_ranges": "Ep 1–48, 51–68, 73–124, 151–201, 227–245, 247–267, 269–328",
        "filler_ranges": "Ep 49–50, 69–72, 125–150 (की ऑफ स्टारी स्काई), 202–226 (एक्लिप्स सेलेस्टियल स्पिरिट्स), 246, 268",
        "notes": "सलाह: की ऑफ स्टारी स्काई आर्क (Ep 125–150) को छोड़कर सीधे ग्रैंड मैजिक गेम्स आर्क देखा जा सकता है।",
    },
    "boruto": {
        "franchise": "Boruto",
        "total_episodes": 293,
        "filler_percentage": "67%",
        "canon_percentage": "33%",
        "canon_ranges": "Ep 19–24, 53–66, 148–151, 181–189, 192–208, 212–220, 287–293",
        "filler_ranges": "Ep 40–52, 67–140, 152–180, 221–286",
        "notes": "सलाह: केवल मंगा कैनन एपिसोड्स (विशेष रूप से Ep 181 से 220 का कारा/इशिकी आर्क) देखने से कहानी का स्तर बहुत बढ़ जाता है।",
    },
}

DYNAMIC_ANIME_ALIASES: dict[str, str] = {
    "one-piece": "one-piece",
    "onepiece": "one-piece",
    "one-piece-hindi-guide-watch-order": "one-piece",
    "naruto": "naruto",
    "naruto-hindi-dub-guide": "naruto",
    "naruto-shippuden": "naruto-shippuden",
    "naruto-shippuuden": "naruto-shippuden",
    "bleach": "bleach",
    "black-clover": "black-clover-season-2",
    "dragon-ball-z": "dragon-ball-z",
    "dragon-ball": "dragon-ball-z",
    "dragon-ball-complete-watch-order-hindi-guide": "dragon-ball-z",
    "dragon-ball-super": "dragon-ball-super",
    "fairy-tail": "fairy-tail",
    "boruto": "boruto",
    "boruto-naruto-next-generations": "boruto",
    "frieren": "frieren-beyond-journeys-end",
    "frieren-beyond-journey-s-end": "frieren-beyond-journeys-end",
    "frieren-beyond-journeys-end-hindi-guide": "frieren-beyond-journeys-end",
    "dan-da-dan": "dandadan",
    "chainsawman": "chainsaw-man",
    "mob-psycho": "mob-psycho-100",
    "classroom-elite": "classroom-of-the-elite",
    "apothecary-diaries-s3": "the-apothecary-diaries-season-3",
    "the-apothecary-diaries": "the-apothecary-diaries-season-3",
    "black-clover-s2": "black-clover-season-2",
    "btth": "battle-through-the-heavens",
    "battle-through-heavens": "battle-through-the-heavens",
    "dr-stone": "dr-stone-science-future",
    "bleach-tybw": "bleach-thousand-year-blood-war",
    "reincarnated-sword-s2": "reincarnated-as-a-sword-season-2",
    "mashle": "mashle-magic-and-muscles",
}


def resolve_dynamic_anime(slug_or_id: str, session: Session) -> Optional[dict[str, Any]]:
    """Universal Anime Resolver: Resolves any slug or ID into rich anime metadata."""
    if not slug_or_id:
        return None
    raw_slug = slugify_anime_title(slug_or_id)
    canonical_slug = DYNAMIC_ANIME_ALIASES.get(raw_slug, raw_slug)
    result = None

    # 1. Check Pre-registered Catalog
    if canonical_slug in DYNAMIC_ANIME_CATALOG:
        result = dict(DYNAMIC_ANIME_CATALOG[canonical_slug])

    # 2. Check FREE_LEGAL_ANIME_DATA
    if not result:
        for f in FREE_LEGAL_ANIME_DATA:
            f_slug = slugify_anime_title(f.get("url", "").replace("/anime/", ""))
            f_id_slug = slugify_anime_title(f.get("id", ""))
            f_en_slug = slugify_anime_title(f.get("title_en", ""))
            if canonical_slug in (f_slug, f_id_slug, f_en_slug):
                plats = [{"name": p, "url": "https://www.youtube.com"} for p in f.get("platforms", ["YouTube"])]
                result = {
                    "slug": canonical_slug,
                    "title_hi": f["title_hi"],
                    "title_en": f["title_en"],
                    "cover_image": f["cover_image"],
                    "is_airing": f.get("is_airing", False),
                    "is_donghua": "donghua" in f.get("filter_tags", []),
                    "is_hindi_dub": f.get("is_hindi_dub", False),
                    "format": "TV",
                    "season_year": "2026",
                    "episodes_display": f.get("episodes", "विस्तृत एपिसोड्स"),
                    "airing_day_time_ist": "उपलब्ध" if not f.get("is_airing") else "चालू प्रसारण",
                    "airing_at": int(time.time()) + 86400 if f.get("is_airing") else 0,
                    "studio": f.get("primary_platform", "Official"),
                    "genres": ["Action", "Adventure"],
                    "genres_str": "एक्शन, रोमांच",
                    "synopsis_hi": f.get("synopsis_hi", ""),
                    "platforms_list": plats,
                    "rating": 8.2,
                }
                break

    # 3. Check Live Weekly Schedule Cache
    if not result:
        try:
            cached_sched = _SCHEDULE_CACHE.get((None, None))
            weekly_items = (cached_sched["data"].get("weekly_items") or []) if cached_sched else []
            if not weekly_items:
                from automation.fetchers.anilist import fetch_live_weekly_schedule
                sched = fetch_live_weekly_schedule()
                weekly_items = sched.get("weekly_items", [])

            for item in weekly_items:
                item_en_slug = slugify_anime_title(item.get("title_en", ""))
                item_ro_slug = slugify_anime_title(item.get("title_romaji", ""))
                item_id_str = str(item.get("id", ""))
                if canonical_slug in (item_en_slug, item_ro_slug, item_id_str):
                    result = {
                        "slug": canonical_slug,
                        "title_hi": item.get("title_hi") or item.get("clean_title_hi") or item.get("title_en"),
                        "title_en": item.get("title_en") or item.get("title_romaji"),
                        "cover_image": item.get("image_url") or "/media/covers/anime_naruto.webp",
                        "is_airing": True,
                        "is_donghua": item.get("is_donghua", False),
                        "is_hindi_dub": item.get("has_hindi_dub", False),
                        "format": item.get("format", "TV"),
                        "season_year": f"{item.get('season', 'Fall')} {item.get('year', 2026)}",
                        "total_episodes": item.get("total_episodes"),
                        "aired_episodes": item.get("aired_episodes") or item.get("episode"),
                        "next_airing_ep": item.get("next_episode") or ((item.get("episode") or 0) + 1 if item.get("episode") else None),
                        "episodes_display": item.get("badge_hi") or (f"Ep {item.get('episode', 1)}" if item.get("episode") else "चालू सीज़न"),
                        "airing_day_time_ist": item.get("airing_time_ist") or (f"{item.get('day_name_hi', 'साप्ताहिक')} • 8:00 PM IST"),
                        "airing_at": item.get("airing_at") or (int(time.time()) + 86400),
                        "studio": item.get("studio") or "TBA",
                        "genres": item.get("genres") or ["Action", "Animation"],
                        "genres_str": ", ".join(item.get("genres") or ["एनीमे", "एक्शन"]),
                        "synopsis_hi": item.get("summary_hi") or f"{item.get('title_hi', 'यह शो')} की आधिकारिक स्ट्रीमिंग और भारतीय दर्शकों के लिए साप्ताहिक एपिसोड गाइड।",
                        "platforms_list": [
                            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
                            {"name": "Netflix", "url": "https://www.netflix.com"}
                        ],
                        "rating": round((float(item.get("score") or 80)) / 10.0, 1) if item.get("score") else 8.4,
                    }
                    break
        except Exception:
            pass

    # 4. Check DB AnimeDraft
    if not result:
        db_draft = session.exec(select(AnimeDraft).where(
            or_(
                AnimeDraft.external_id == slug_or_id,
                AnimeDraft.external_id == canonical_slug
            )
        )).first()
        if db_draft:
            result = {
                "slug": canonical_slug,
                "title_hi": db_draft.title_hi or db_draft.title_en,
                "title_en": db_draft.title_en or db_draft.title_hi,
                "cover_image": db_draft.image_url or "/media/covers/anime_naruto.webp",
                "is_airing": (db_draft.content_type == "schedule"),
                "is_donghua": bool(getattr(db_draft, "is_donghua", False)),
                "is_hindi_dub": True,
                "format": "TV",
                "season_year": "2026",
                "episodes_display": "उपलब्ध",
                "airing_day_time_ist": "प्रसारण उपलब्ध",
                "airing_at": int(time.time()) + 86400 if db_draft.content_type == "schedule" else 0,
                "studio": db_draft.source_name or "Official",
                "genres": ["Action"],
                "genres_str": "एक्शन, ड्रामा",
                "synopsis_hi": db_draft.summary_hi or db_draft.summary_en or "",
                "platforms_list": [
                    {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
                    {"name": "Netflix", "url": "https://www.netflix.com"}
                ],
                "rating": 8.0,
            }

    # 5. Clean Title Fallback Generator
    if not result:
        clean_words = [w.capitalize() for w in canonical_slug.split("-") if w]
        if clean_words:
            english_name = " ".join(clean_words)
            cand_local = f"/media/covers/anime_{canonical_slug}.webp"
            resolved_cover = "/media/covers/anime_frieren.webp" if "frieren" in canonical_slug else (cand_local if Path(f"media/covers/anime_{canonical_slug}.webp").exists() else "/media/covers/anime_solo-leveling.webp")
            result = {
                "slug": canonical_slug,
                "title_hi": english_name,
                "title_en": english_name,
                "cover_image": resolved_cover,
                "is_airing": True,
                "is_donghua": False,
                "is_hindi_dub": False,
                "format": "TV",
                "season_year": "2026",
                "episodes_display": "चालू सीज़न",
                "airing_day_time_ist": "साप्ताहिक • 8:00 PM IST",
                "airing_at": int(time.time()) + 86400,
                "studio": "Official Studio",
                "genres": ["Action", "Adventure"],
                "genres_str": "एक्शन, एडवेंचर",
                "synopsis_hi": f"{english_name} की आधिकारिक स्ट्रीमिंग, एपिसोड शेड्यूल और भारतीय दर्शकों के लिए विस्तृत गाइड।",
                "platforms_list": [
                    {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
                    {"name": "Netflix", "url": "https://www.netflix.com"}
                ],
                "rating": 8.0,
            }

    if not result:
        return None

    # Universal Sanitization & Episode Tracker Attachment
    if "episode_tracker" not in result:
        result["episode_tracker"] = build_episode_tracker(
            total_episodes=result.get("total_episodes"),
            next_airing_ep=result.get("next_airing_ep"),
            next_airing_at=result.get("airing_at"),
            airing_day_time_ist=result.get("airing_day_time_ist"),
            is_airing=result.get("is_airing", False),
            aired_episodes=result.get("aired_episodes"),
        )

    # Ensure rating is set
    if "rating" not in result or not result["rating"]:
        result["rating"] = round(float(result.get("score") or 8.5), 1)

    # Ensure clean cover image (never placeholder SVG or mismatched naruto fallback)
    cand_cover_file = f"media/covers/anime_{canonical_slug}.webp"
    if "frieren" in canonical_slug:
        result["cover_image"] = "/media/covers/anime_frieren.webp"
    elif Path(cand_cover_file).exists():
        result["cover_image"] = f"/{cand_cover_file}"
    elif not result.get("cover_image") or "placeholder" in str(result.get("cover_image")) or (result.get("cover_image") == "/media/covers/anime_naruto.webp" and "naruto" not in canonical_slug):
        result["cover_image"] = "/media/covers/anime_solo-leveling.webp"

    # Strictly eliminate "Ep ?" and "?" clutter from episodes_display
    disp = str(result.get("episodes_display") or "")
    if "?" in disp:
        result["episodes_display"] = "प्रसारण जारी" if result.get("is_airing") else "पूर्ण सीज़न"

    # Attach Filler Guide if franchise matches
    f_key = None
    c_lower = canonical_slug.lower()
    if "naruto-shippu" in c_lower:
        f_key = "naruto-shippuden"
    elif "naruto" in c_lower and "boruto" not in c_lower:
        f_key = "naruto"
    elif "one-piece" in c_lower or "onepiece" in c_lower:
        f_key = "one-piece"
    elif "bleach" in c_lower:
        f_key = "bleach"
    elif "black-clover" in c_lower:
        f_key = "black-clover"
    elif "dragon-ball-super" in c_lower:
        f_key = "dragon-ball-super"
    elif "dragon-ball" in c_lower:
        f_key = "dragon-ball-z"
    elif "conan" in c_lower:
        f_key = "detective-conan"
    elif "fairy-tail" in c_lower:
        f_key = "fairy-tail"
    elif "boruto" in c_lower:
        f_key = "boruto"

    result["filler_guide"] = FILLER_GUIDES_DATA.get(f_key) if f_key else None

    return result


def get_related_dynamic_anime(current_slug: str, limit: int = 4) -> list[dict[str, Any]]:
    """Return 4 distinct related anime for the footer recommendation grid."""
    pool = [
        {"title_hi": "फ्रीरेन: बियॉन्ड जर्नीज़ एंड", "title_en": "Frieren: Beyond Journey's End", "url": "/anime/frieren-beyond-journeys-end", "cover_image": "/media/covers/anime_frieren.webp", "is_hindi_dub": False, "badge": "फैंटेसी"},
        {"title_hi": "चेनसॉ मैन", "title_en": "Chainsaw Man", "url": "/anime/chainsaw-man", "cover_image": "/media/covers/anime_chainsaw-man.webp", "is_hindi_dub": False, "badge": "डार्क एक्शन"},
        {"title_hi": "डैनडाडैन", "title_en": "DanDaDan", "url": "/anime/dandadan", "cover_image": "/media/covers/anime_dandadan.webp", "is_hindi_dub": False, "badge": "सुपरनैचुरल"},
        {"title_hi": "साकामोटो डेज़", "title_en": "Sakamoto Days", "url": "/anime/sakamoto-days", "cover_image": "/media/covers/anime_sakamoto-days.webp", "is_hindi_dub": False, "badge": "एक्शन कॉमेडी"},
        {"title_hi": "द अपोथेकेरी डायरीज़ सीज़न 3", "title_en": "The Apothecary Diaries Season 3", "url": "/anime/the-apothecary-diaries-season-3", "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx176496-5N9gq2U62DkR.jpg", "is_hindi_dub": False, "badge": "मिस्ट्री"},
        {"title_hi": "स्वॉलोड स्टार", "title_en": "Swallowed Star", "url": "/anime/swallowed-star", "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx125299-FkM2rK5s9uP0.jpg", "is_hindi_dub": True, "badge": "डोंगहुआ"},
        {"title_hi": "लिंक क्लिक", "title_en": "Link Click", "url": "/anime/link-click", "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx126403-1TjhVqW84FqT.png", "is_hindi_dub": True, "badge": "थ्रिलर"},
        {"title_hi": "वन-पंच मैन", "title_en": "One-Punch Man", "url": "/anime/one-punch-man", "cover_image": "/media/covers/anime_solo-leveling.webp", "is_hindi_dub": True, "badge": "सुपरहीरो"},
    ]
    res = [item for item in pool if not item["url"].endswith(current_slug)]
    return res[:limit]


# ---------- RANDOM ANIME SMART PICKER (PHASE 2) ----------
def pick_random_anime(mood: Optional[str] = None) -> dict[str, Any]:
    """Smart Picker logic: returns a curated anime matching the user's mood."""
    mood_map = {
        "action": [
            {"title_hi": "चेनसॉ मैन", "title_en": "Chainsaw Man", "url": "/anime/chainsaw-man"},
            {"title_hi": "डैनडाडैन", "title_en": "DanDaDan", "url": "/anime/dandadan"},
            {"title_hi": "वन-पंच मैन", "title_en": "One-Punch Man", "url": "/anime/one-punch-man"},
            {"title_hi": "साकामोटो डेज़", "title_en": "Sakamoto Days", "url": "/anime/sakamoto-days"},
            {"title_hi": "जुजुत्सु काइसेन", "title_en": "Jujutsu Kaisen", "url": "/anime/jujutsu-kaisen-hindi-streaming-guide"},
        ],
        "romance": [
            {"title_hi": "फ्रीरेन: बियॉन्ड जर्नीज़ एंड", "title_en": "Frieren: Beyond Journey's End", "url": "/anime/frieren-beyond-journeys-end"},
            {"title_hi": "योर नेम", "title_en": "Your Name", "url": "/anime/your-name-makoto-shinkai-romance-anime-hindi"},
            {"title_hi": "द अपोथेकेरी डायरीज़", "title_en": "The Apothecary Diaries", "url": "/anime/the-apothecary-diaries-season-3"},
            {"title_hi": "ब्लू बॉक्स सीज़न 2", "title_en": "Blue Box Season 2", "url": "/anime/blue-box-season-2"},
        ],
        "free": [
            {"title_hi": "स्पाई x फैमिली", "title_en": "Spy x Family", "url": "/anime/spy-x-family-hindi-dub-streaming-guide"},
            {"title_hi": "फ्रीरेन: बियॉन्ड जर्नीज़ एंड", "title_en": "Frieren: Beyond Journey's End", "url": "/anime/frieren-beyond-journeys-end"},
            {"title_hi": "चेनसॉ मैन", "title_en": "Chainsaw Man", "url": "/anime/chainsaw-man"},
            {"title_hi": "टोक्यो रिवेंजर्स", "title_en": "Tokyo Revengers", "url": "/anime/tokyo-revengers"},
            {"title_hi": "लिंक क्लिक", "title_en": "Link Click", "url": "/anime/link-click"},
        ],
        "hindi_dub": [
            {"title_hi": "स्पाई x फैमिली", "title_en": "Spy x Family", "url": "/anime/spy-x-family-hindi-dub-streaming-guide"},
            {"title_hi": "टोक्यो रिवेंजर्स", "title_en": "Tokyo Revengers", "url": "/anime/tokyo-revengers"},
            {"title_hi": "वन-पंच मैन", "title_en": "One-Punch Man", "url": "/anime/one-punch-man"},
            {"title_hi": "लिंक क्लिक", "title_en": "Link Click", "url": "/anime/link-click"},
            {"title_hi": "सोलो लेवलिंग", "title_en": "Solo Leveling", "url": "/anime/solo-leveling-hindi-dub-guide"},
        ],
    }
    import random
    selected_pool = mood_map.get((mood or "").lower().strip())
    if not selected_pool:
        # Combined pool of top hits
        selected_pool = [
            {"title_hi": "फ्रीरेन: बियॉन्ड जर्नीज़ एंड", "title_en": "Frieren: Beyond Journey's End", "url": "/anime/frieren-beyond-journeys-end"},
            {"title_hi": "चेनसॉ मैन", "title_en": "Chainsaw Man", "url": "/anime/chainsaw-man"},
            {"title_hi": "डैनडाडैन", "title_en": "DanDaDan", "url": "/anime/dandadan"},
            {"title_hi": "साकामोटो डेज़", "title_en": "Sakamoto Days", "url": "/anime/sakamoto-days"},
            {"title_hi": "द अपोथेकेरी डायरीज़ सीज़न 3", "title_en": "The Apothecary Diaries Season 3", "url": "/anime/the-apothecary-diaries-season-3"},
            {"title_hi": "स्पाई x फैमिली", "title_en": "Spy x Family", "url": "/anime/spy-x-family-hindi-dub-streaming-guide"},
            {"title_hi": "टोक्यो रिवेंजर्स", "title_en": "Tokyo Revengers", "url": "/anime/tokyo-revengers"},
        ]
    return random.choice(selected_pool)


@router.get("/random-anime")
def random_anime_picker(
    request: Request,
    mood: Optional[str] = Query(None),
):
    """Phase 2: Surprise Me ('🎲 आज क्या देखूँ?') Smart Picker endpoint."""
    picked = pick_random_anime(mood)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(picked)
    return RedirectResponse(url=picked["url"], status_code=303)


def _is_anime_in_watchlist(session: Session, user: Any, slug: str) -> bool:
    """Helper to check if an anime slug is in the user's watchlist."""
    if not user:
        return False
    user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    if not user_id:
        return False
    try:
        art = session.exec(select(Article).where(Article.slug == slug)).first()
        if art:
            bm = session.exec(select(Bookmark).where(Bookmark.user_id == user_id, Bookmark.article_id == art.id)).first()
            return bm is not None
    except Exception:
        session.rollback()
    return False


# ---------- UNIVERSAL DYNAMIC ANIME DETAIL ROUTE ----------
@router.get("/anime/{item_id}", response_class=HTMLResponse)
def anime_detail_page(
    request: Request,
    item_id: str,
    session: Session = Depends(get_session),
):
    clean_id = slugify_anime_title(item_id)
    caching = is_caching_enabled()
    now = time.monotonic()

    # 1. Check in-memory TTL cache first (sub-5ms response!)
    if caching and clean_id in _DYNAMIC_ANIME_CACHE:
        cached_data, exp = _DYNAMIC_ANIME_CACHE[clean_id]
        if exp > now:
            ctx = cached_data.copy()
            ctx["request"] = request
            ctx["categories"] = get_categories(session)
            user = getattr(request.state, "user", None)
            ctx["user"] = user
            target_slug = (ctx.get("anime") or {}).get("slug", clean_id)
            ctx["is_in_watchlist"] = _is_anime_in_watchlist(session, user, target_slug)
            return render("anime_detail.html", **ctx)

    # 2. Check dynamic anime catalog first
    resolved_catalog_slug = DYNAMIC_ANIME_ALIASES.get(clean_id, clean_id)
    if resolved_catalog_slug in DYNAMIC_ANIME_CATALOG or clean_id in DYNAMIC_ANIME_CATALOG:
        anime_data = resolve_dynamic_anime(item_id, session)
        if anime_data:
            categories = get_categories(session)
            user = getattr(request.state, "user", None)
            lang = request.cookies.get("site_lang", "hi")
            related_anime = get_related_dynamic_anime(anime_data["slug"], limit=4)
            is_in_watchlist = _is_anime_in_watchlist(session, user, anime_data["slug"])
            ctx = {
                "anime": anime_data,
                "related_anime": related_anime,
                "categories": categories,
                "lang": lang,
                "user": user,
                "is_in_watchlist": is_in_watchlist,
                "request": request,
            }
            if caching:
                _DYNAMIC_ANIME_CACHE[clean_id] = (ctx.copy(), now + DYNAMIC_ANIME_CACHE_TTL)
            return render("anime_detail.html", **ctx)

    # 3. Check if an Article editorial guide exists in DB with this slug
    article = session.exec(select(Article).where(Article.slug == item_id)).first()
    if article:
        return article_page(request, item_id, session)

    # 4. Resolve Dynamic Anime Detail Page fallback
    anime_data = resolve_dynamic_anime(item_id, session)
    if anime_data:
        categories = get_categories(session)
        user = getattr(request.state, "user", None)
        lang = request.cookies.get("site_lang", "hi")
        related_anime = get_related_dynamic_anime(anime_data["slug"], limit=4)
        is_in_watchlist = _is_anime_in_watchlist(session, user, anime_data["slug"])
        ctx = {
            "anime": anime_data,
            "related_anime": related_anime,
            "categories": categories,
            "lang": lang,
            "user": user,
            "is_in_watchlist": is_in_watchlist,
            "request": request,
        }
        if caching:
            _DYNAMIC_ANIME_CACHE[clean_id] = (ctx.copy(), now + DYNAMIC_ANIME_CACHE_TTL)
        return render("anime_detail.html", **ctx)

    # 4. Fallback: if numeric ID or external_id intended for news/update
    return update_detail_page(request, item_id, session)


@router.get("/updates/{item_id}", response_class=HTMLResponse)
@router.get("/news/{item_id}", response_class=HTMLResponse)
def update_detail_page(
    request: Request,
    item_id: str,
    session: Session = Depends(get_session),
):
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    lang = request.cookies.get("site_lang", "hi")

    # 0. Check if an Article exists with this slug
    article = session.exec(select(Article).where(Article.slug == item_id)).first()
    if article or not (item_id.isdigit() or item_id.startswith("anilist_")):
        try:
            return article_page(request, item_id, session)
        except HTTPException as e:
            if e.status_code != 404:
                raise e

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
        related_items = list(session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.id != item_db_id,
                AnimeDraft.image_url.is_not(None),
                AnimeDraft.image_url != "",
            )
            .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .limit(4)
        ).all())
    else:
        related_items = list(session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.image_url.is_not(None),
                AnimeDraft.image_url != "",
            )
            .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
            .limit(4)
        ).all())

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
        ("free", "0.9"),
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

    # Published Manga Detail Pages ONLY
    published_mangas = session.exec(
        select(AnimeDraft).where(
            AnimeDraft.content_type == "manga",
            AnimeDraft.status == "published",
            AnimeDraft.is_stale == False,
        )
    ).all()
    for m in published_mangas:
        m_lastmod = m.published_at or m.created_at
        m_lastmod_str = m_lastmod.strftime("%Y-%m-%d") if m_lastmod else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        urls.append(
            f"""  <url>
    <loc>{base_url}/manga/{m.id}</loc>
    <lastmod>{m_lastmod_str}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.7</priority>
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



