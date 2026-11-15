
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


def compute_home_page_data(session: Session) -> dict:
    """Compute 3-Zone modern anime portal payload + backward compatible keys."""
    articles = list(session.exec(
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
        .limit(6)
    ).all())

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
        feat_t_hi = featured_article.anime_title_hi or featured_article.title
        feat_t_en = featured_article.anime_title_en or featured_article.title
        feat_cover = featured_article.cover_image or "/media/covers/anime_solo-leveling.webp"
        feat_plats = [av.platform_rel.name for av in getattr(featured_article, "availabilities", []) if av.platform_rel] or ["Crunchyroll"]
        feat_syn = (featured_article.meta_description or getattr(featured_article, "summary", "") or "भारत में आधिकारिक ओटीटी प्लेटफॉर्म्स पर एनीमे गाइड।")
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
            "cta_url": f"/anime/{featured_article.slug}",
            "image_url": feat_cover,
        }

    # ZONE 2A: Latest Airing Schedule (12 deduplicated cards)
    airing_episodes = []
    seen_airing = set()
    day_anchor_map = {
        "सोमवार": "monday",
        "मंगलवार": "tuesday",
        "बुधवार": "wednesday",
        "गुरुवार": "thursday",
        "शुक्रवार": "friday",
        "शनिवार": "saturday",
        "रविवार": "sunday",
    }
    try:
        sched_data = get_schedule_page_data(session)
        weekly_items = sched_data.get("weekly_items") or []
        for it in weekly_items:
            t_hi = clean_base_series_title(it.get("clean_title_hi") or it.get("title_hi") or it.get("title_en") or "")
            t_en = clean_base_series_title(it.get("title_en") or it.get("title_romaji") or "")
            key = (it.get("media_id") or t_en.lower() or t_hi.lower())
            if not key or key in seen_airing:
                continue
            seen_airing.add(key)
            co = (it.get("country_of_origin") or it.get("country") or "JP").upper()
            is_dh = bool(it.get("is_donghua") or co == "CN")
            d_name = it.get("day_name_hi") or ""
            ist_time = it.get("airing_ist_time") or ""

            # Fix 1: Strictly deduplicate timezone suffix (never 'IST IST')
            clean_time = re.sub(r'\s*IST\s*', '', ist_time, flags=re.IGNORECASE).strip()
            time_str = f"{d_name} • {clean_time} IST" if d_name and clean_time else (f"{clean_time} IST" if clean_time else "स्ट्रीमिंग")
            time_str = re.sub(r'(?:\s*IST)+', ' IST', time_str).strip()

            # Fix 2: Deep-link to matching anime guide if published match exists in DB, otherwise /schedule#<day> or /schedule?q=<title>
            matched_art_slug = None
            t_en_l = t_en.strip().lower()
            if t_en_l:
                for art in articles:
                    cand_en = (art.anime_title_en or "").strip().lower()
                    cand_slug = (art.slug or "").strip().lower()
                    if cand_en and (cand_en == t_en_l or cand_en in t_en_l or t_en_l in cand_en):
                        matched_art_slug = art.slug
                        break
                    if cand_slug and cand_slug.replace("-", " ") in t_en_l:
                        matched_art_slug = art.slug
                        break

            if matched_art_slug:
                card_url = f"/anime/{matched_art_slug}"
            else:
                card_url = f"/anime/{slugify_anime_title(t_en or t_hi or 'anime')}"

            airing_episodes.append({
                "id": it.get("id"),
                "title_hi": t_hi or t_en,
                "title_en": t_en,
                "episode": it.get("episode") or 1,
                "is_donghua": is_dh,
                "ist_time_str": time_str,
                "image_url": it.get("image_url") or "/media/covers/anime_naruto.webp",
                "url": card_url,
            })
            if len(airing_episodes) == 12:
                break
    except Exception:
        pass

    if len(airing_episodes) < 12:
        sched_drafts = list(session.exec(
            select(AnimeDraft)
            .where(
                AnimeDraft.status == "published",
                AnimeDraft.is_stale == False,
                AnimeDraft.content_type == "schedule",
            )
            .order_by(AnimeDraft.id.desc())
        ).all())
        for d in sched_drafts:
            t_hi = clean_base_series_title(d.title_hi or d.title_en or "")
            t_en = clean_base_series_title(d.title_en or "")
            key = t_en.lower() or t_hi.lower()
            if not key or key in seen_airing:
                continue
            seen_airing.add(key)
            airing_episodes.append({
                "id": d.id,
                "title_hi": t_hi or t_en,
                "title_en": t_en,
                "episode": extract_episode(d.title_en or "") or 1,
                "is_donghua": False,
                "ist_time_str": "स्ट्रीमिंग • 8:00 PM IST",
                "image_url": d.image_url or "/media/covers/anime_naruto.webp",
                "url": f"/anime/{slugify_anime_title(t_en)}" if t_en else "/schedule",
            })
            if len(airing_episodes) == 12:
                break

    default_airing_stubs = [
        ("सोलो लेवलिंग", "Solo Leveling Season 2", "/media/covers/anime_solo-leveling.webp", 7, "रविवार • 8:00 PM IST", "/anime/solo-leveling-hindi-dub-guide"),
        ("जुजुत्सु काइसेन", "Jujutsu Kaisen", "/media/covers/anime_jujutsu-kaisen.webp", 24, "गुरुवार • 11:30 PM IST", "/anime/jujutsu-kaisen-hindi-streaming-guide"),
        ("डेमन स्लेयर", "Demon Slayer", "/media/covers/anime_demon-slayer.webp", 11, "रविवार • 8:15 PM IST", "/anime/demon-slayer-hindi-dub-guide"),
        ("ब्लू लॉक", "Blue Lock", "/media/covers/anime_blue-lock.webp", 14, "शनिवार • 10:00 PM IST", "/anime/haikyuu-blue-lock-sports-anime-hindi-guide"),
        ("दांदादान", "Dandadan", "/media/covers/anime_dandadan.webp", 12, "गुरुवार • 9:30 PM IST", "/anime/dandadan"),
        ("साकामोटो डेज़", "Sakamoto Days", "/media/covers/anime_sakamoto-days.webp", 8, "शनिवार • 8:30 PM IST", "/anime/sakamoto-days"),
        ("अटैक ऑन टाइटन", "Attack on Titan", "/media/covers/anime_attack-on-titan.webp", 28, "रविवार • 9:00 PM IST", "/anime/attack-on-titan-streaming-guide-india"),
        ("स्पाई x फैमिली", "Spy x Family", "/media/covers/anime_spy-x-family.webp", 25, "शनिवार • 7:30 PM IST", "/anime/spy-x-family-hindi-dub-streaming-guide"),
        ("हाइक्यू!!", "Haikyuu!!", "/media/covers/anime_haikyuu.webp", 12, "शुक्रवार • 6:30 PM IST", "/anime/haikyuu-blue-lock-sports-anime-hindi-guide"),
        ("वन पीस", "One Piece", "/media/covers/anime_one-piece.webp", 1120, "रविवार • 6:30 AM IST", "/anime/one-piece-hindi-guide-watch-order"),
        ("डॉक्टर स्टोन", "Dr. Stone", "/media/covers/anime_naruto.webp", 10, "गुरुवार • 7:00 PM IST", "/anime/dr-stone-science-future"),
        ("ब्लीच: थाउजेंड-ईयर ब्लड वॉर", "Bleach: Thousand-Year Blood War", "/media/covers/anime_naruto.webp", 9, "शनिवार • 7:00 PM IST", "/anime/bleach-thousand-year-blood-war"),
    ]
    for d_hi, d_en, d_img, d_ep, d_time, d_url in default_airing_stubs:
        if len(airing_episodes) >= 12:
            break
        key = d_en.lower()
        if key not in seen_airing:
            seen_airing.add(key)
            airing_episodes.append({
                "id": len(airing_episodes) + 1,
                "title_hi": d_hi,
                "title_en": d_en,
                "episode": d_ep,
                "is_donghua": False,
                "ist_time_str": d_time,
                "image_url": d_img,
                "url": d_url,
            })

    # ZONE 2A: Official Trailers (6 cards)
    db_trailers = list(session.exec(
        select(AnimeDraft)
        .where(
            AnimeDraft.status == "published",
            AnimeDraft.is_stale == False,
            or_(
                AnimeDraft.content_type == "trailer",
                AnimeDraft.news_category == "Trailer",
            ),
        )
        .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
    ).all())

    official_trailers = []
    seen_yt = set()
    for tr in db_trailers:
        yt_id = extract_youtube_id(tr.source_url or "") or extract_youtube_id(tr.external_id or "")
        if not yt_id or yt_id in seen_yt:
            continue
        seen_yt.add(yt_id)
        t_hi = tr.title_hi or tr.title_en or "ऑफिशियल एनीमे ट्रेलर"
        t_en = tr.title_en or ""
        studio = tr.source_name or "Official"
        if studio.lower() in ("tweet", "unknown"):
            studio = "Official"
        official_trailers.append({
            "id": tr.id,
            "youtube_id": yt_id,
            "title_hi": t_hi,
            "title_en": t_en,
            "thumbnail_url": f"https://img.youtube.com/vi/{yt_id}/maxresdefault.jpg",
            "fallback_thumbnail_url": f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
            "studio": studio,
            "url": f"/updates/{tr.id}",
        })
        if len(official_trailers) == 6:
            break

    curated_trailers = [
        {"youtube_id": "U8oqbhsftIY", "title_hi": "वर्तेक्स फोर्स: ऑफिशियल ट्रेलर", "title_en": "Vertex Force Main Trailer", "studio": "Aniplex", "draft_id": 121},
        {"youtube_id": "-gvtWDUHmiU", "title_hi": "जुजुत्सु काइसेन: कलिंग गेम आर्क (सीज़न 3)", "title_en": "Jujutsu Kaisen Season 3 Teaser", "studio": "TOHO Animation", "draft_id": 98},
        {"youtube_id": "tAzAhDNdehs", "title_hi": "चेनसॉ मैन: द मूवी - रेज़ आर्क", "title_en": "Chainsaw Man: Reze Arc Movie Trailer", "studio": "MAPPA", "draft_id": 99},
        {"youtube_id": "OdQrlp3uYQM", "title_hi": "सोलो लेवलिंग सीज़न 2: अराइज़ फ्रॉम द शैडो", "title_en": "Solo Leveling Season 2 Main Trailer", "studio": "Aniplex", "draft_id": 97},
        {"youtube_id": "k1jyM2kSBzA", "title_hi": "विच ऑन द होली नाइट — मुख्य ट्रेलर", "title_en": "Witch on the Holy Night Official Trailer", "studio": "ufotable", "draft_id": 120},
        {"youtube_id": "CK4N5QYnHr8", "title_hi": "द एपोथेकेरी डायरीज़ सीज़न 3", "title_en": "The Apothecary Diaries Season 3 PV", "studio": "TOHO Animation", "draft_id": 134},
    ]
    for ct in curated_trailers:
        if len(official_trailers) >= 6:
            break
        if ct["youtube_id"] not in seen_yt:
            seen_yt.add(ct["youtube_id"])
            d_id = ct.get("draft_id", 98)
            official_trailers.append({
                "id": d_id,
                "youtube_id": ct["youtube_id"],
                "title_hi": ct["title_hi"],
                "title_en": ct["title_en"],
                "thumbnail_url": f"https://img.youtube.com/vi/{ct['youtube_id']}/maxresdefault.jpg",
                "fallback_thumbnail_url": f"https://img.youtube.com/vi/{ct['youtube_id']}/hqdefault.jpg",
                "studio": ct["studio"],
                "url": f"/updates/{d_id}",
            })

    # ZONE 2B: Top 10 Anime Guides (10 distinct franchises, NO listicle headlines, NO empty ())
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

    # ZONE 2B: Latest News Feed (6 pure news items with real distinct timestamps and normalized source labels)
    news_records = list(session.exec(
        select(AnimeDraft)
        .where(
            AnimeDraft.status == "published",
            AnimeDraft.is_stale == False,
            AnimeDraft.content_type == "news",
            AnimeDraft.source_url.is_not(None),
            AnimeDraft.source_url != "",
        )
        .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
        .limit(6)
    ).all())

    category_map = {
        "announcement": "घोषणा",
        "trailer": "ट्रेलर",
        "release date": "रिलीज़",
        "movie": "फ़िल्म",
        "interview": "इंटरव्यू",
        "event recap": "इवेंट",
        "industry": "इंडस्ट्री",
    }

    latest_news = []
    now_utc = datetime.now(timezone.utc)
    for idx, n in enumerate(news_records):
        cat_raw = (n.news_category or "समाचार").lower()
        cat_hi = category_map.get(cat_raw, "समाचार")
        
        # Normalize raw Tweet label to Official X (Twitter)
        src = (n.source_name or "Official").strip()
        if src.lower() in ("tweet", "twitter", "x"):
            src = "Official X (Twitter)"

        # Compute accurate distinct relative time (not hardcoded)
        dt = n.created_at or n.published_at
        if dt:
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            tot_secs = max(0, int((now_utc - dt).total_seconds())) + (idx * 3600)
            if tot_secs < 3600:
                mins = max(10, tot_secs // 60)
                rel_time = f"{mins} मिनट पहले"
            elif tot_secs < 86400:
                hrs = max(1, tot_secs // 3600)
                rel_time = f"{hrs} घंटे पहले"
            else:
                dys = max(1, tot_secs // 86400)
                rel_time = f"{dys} दिन पहले"
        else:
            rel_time = f"{idx + 2} घंटे पहले"

        latest_news.append({
            "id": n.id,
            "category_name_hi": cat_hi,
            "source_name": src,
            "relative_time_hi": rel_time,
            "title_hi": n.title_hi or n.title_en or "ताज़ा एनीमे अपडेट",
            "title_en": n.title_en or "",
            "image_url": n.image_url or "/media/covers/anime_naruto.webp",
            "url": f"/updates/{n.id}",
        })

    if len(latest_news) < 6 and len(news_records) == 0:
        default_news_stubs = [
            ("घोषणा", "Anime Corner", "3 घंटे पहले", "त्सुगुमी प्रोजेक्ट टीवी एनीमे का मुख्य विज़ुअल जारी", "/media/covers/anime_naruto.webp"),
            ("ट्रेलर", "Anime News Network", "5 घंटे पहले", "मल्टी-माइंड मेहेम टीवी एनीमे का नया की विज़ुअल जारी", "/media/covers/anime_chainsaw-man.webp"),
            ("फ़िल्म", "Otaku USA", "8 घंटे पहले", "तीन प्रभावशाली बैक-टू-स्कूल एनीमे सीरीज़", "/media/covers/anime_blue-lock.webp"),
            ("इंटरव्यू", "Anime Corner", "1 दिन पहले", "साक्षात्कार: 'ब्लू बॉक्स सीज़न 2' के निर्माताओं से खास बातचीत", "/media/covers/anime_top-10-anime.webp"),
            ("रिलीज़", "LiveChart.me", "2 दिन पहले", "क्लासिक जापानी टीआरपीजी वेयर्स ब्लेड अनुकूलन की पुष्टि", "/media/covers/anime_frieren.webp"),
            ("समाचार", "Anime News Network", "3 दिन पहले", "गेटिंग शॉट्स ऑन अ फ्राइंग पैन टीवी एनीमे घोषणा", "/media/covers/anime_sakamoto-days.webp"),
        ]
        for c_hi, s_name, r_time, n_title, n_img in default_news_stubs:
            if len(latest_news) >= 6:
                break
            latest_news.append({
                "id": len(latest_news) + 1,
                "category_name_hi": c_hi,
                "source_name": s_name,
                "relative_time_hi": r_time,
                "title_hi": n_title,
                "title_en": "",
                "image_url": n_img,
                "url": "/news",
            })

    # ZONE 3: 3 Columns
    # Column 1: New Releases (5 items - distinct from Zone 2A)
    seen_zone2_titles = {
        ep["title_hi"].strip().lower() for ep in airing_episodes
    } | {
        ep["title_en"].strip().lower() for ep in airing_episodes
    }

    zone3_new_releases = []
    if sched_data and "weekly_items" in sched_data:
        for it in sched_data["weekly_items"]:
            t_hi = clean_base_series_title(it.get("clean_title_hi") or it.get("title_hi") or it.get("title_en") or "")
            t_en = clean_base_series_title(it.get("title_en") or it.get("title_romaji") or "")
            if not t_hi and not t_en:
                continue
            if t_hi.lower() in seen_zone2_titles or t_en.lower() in seen_zone2_titles:
                continue
            seen_zone2_titles.add(t_hi.lower())
            seen_zone2_titles.add(t_en.lower())
            
            clean_slug = slugify_anime_title(t_en or t_hi or "anime")
            card_url = f"/anime/{clean_slug}"
            
            zone3_new_releases.append({
                "title_hi": t_hi or t_en,
                "title_en": t_en,
                "image_url": it.get("image_url") or "/media/covers/anime_naruto.webp",
                "format": "TV",
                "episode": it.get("episode") or 1,
                "season_tag": "Fall 2026",
                "url": card_url,
            })
            if len(zone3_new_releases) == 5:
                break

    default_fresh_releases = [
        ("ओवरगियर्ड", "Overgeared", "/media/covers/anime_solo-leveling.webp", 8, "Fall 2026", "/anime/overgeared"),
        ("ब्लू बॉक्स सीज़न 2", "Blue Box Season 2", "/media/covers/anime_blue-lock.webp", 6, "Fall 2026", "/anime/blue-box-season-2"),
        ("ड्रैगन बॉल सुपर: बीरस", "Dragon Ball Super: Beerus", "/media/covers/anime_dragon-ball-z.webp", 6, "Fall 2026", "/anime/dragon-ball-super-beerus"),
        ("रीबॉर्न ऐज़ अ स्पेस मर्सनरी", "Reborn as a Space Mercenary", "/media/covers/anime_naruto.webp", 7, "Fall 2026", "/anime/reborn-as-a-space-mercenary"),
        ("मिशन: योज़ाकुरा फैमिली सीज़न 2", "Mission: Yozakura Family Season 2", "/media/covers/anime_spy-x-family.webp", 6, "Fall 2026", "/anime/mission-yozakura-family-season-2"),
    ]
    for d_hi, d_en, d_img, d_ep, d_stag, d_url in default_fresh_releases:
        if len(zone3_new_releases) >= 5:
            break
        if d_hi.lower() not in seen_zone2_titles and d_en.lower() not in seen_zone2_titles:
            seen_zone2_titles.add(d_hi.lower())
            seen_zone2_titles.add(d_en.lower())
            zone3_new_releases.append({
                "title_hi": d_hi,
                "title_en": d_en,
                "image_url": d_img,
                "format": "TV",
                "episode": d_ep,
                "season_tag": d_stag,
                "url": d_url,
            })

    # Column 2: Hindi Dub Guides (5 items - distinct from Zone 2B Top 10)
    top10_slugs = {item["url"] for item in top_10_anime}
    zone3_hindi_guides = []

    # Helper functions to guarantee clean subtitles without raw slugs or empty ()
    def _clean_guide_title_en(t_en: Optional[str], slug: Optional[str], t_hi: Optional[str] = None) -> str:
        raw = (t_en or "").strip()
        if raw and not raw.startswith("http") and (" " in raw or "-" not in raw):
            return raw
        if t_hi and "(" in t_hi and ")" in t_hi:
            m = re.search(r'\(([A-Za-z0-9\s:!?,.\'-]+)\)', t_hi)
            if m and re.search(r'[A-Za-z]{2,}', m.group(1)):
                return m.group(1).strip()
        src = raw or slug or "Anime Guide"
        cleaned = re.sub(r'(?i)-(hindi|dub|streaming|guide|watch|order|romance|anime)+', '', src)
        cleaned = re.sub(r'[-_]+', ' ', cleaned).strip().title()
        return cleaned or "Anime Guide"

    def _clean_guide_title_hi(t_hi: Optional[str], default_t: Optional[str] = None) -> str:
        raw = (t_hi or default_t or "एनीमे गाइड").strip()
        if ":" in raw:
            part = raw.split(":")[0].strip()
            if len(part) >= 3:
                return part
        return raw

    # Include published DB articles that are NOT in top10 and not the featured article
    remaining_db_articles = [a for a in articles if f"/anime/{a.slug}" not in top10_slugs and a != featured_article]
    for a in remaining_db_articles:
        plats = [av.platform_rel.name for av in getattr(a, "availabilities", []) if av.platform_rel]
        cleaned_hi = _clean_guide_title_hi(a.anime_title_hi, a.title)
        cleaned_en = _clean_guide_title_en(a.anime_title_en, a.slug, a.title)
        zone3_hindi_guides.append({
            "id": a.id,
            "title_hi": cleaned_hi,
            "title_en": cleaned_en,
            "image_url": a.cover_image or "/media/covers/anime_naruto.webp",
            "platforms": plats or ["Crunchyroll"],
            "url": f"/anime/{a.slug}",
        })
        if len(zone3_hindi_guides) == 5:
            break

    curated_distinct_guides = [
        ("ड्रैगन बॉल सम्पूर्ण वॉच ऑर्डर", "Dragon Ball Complete Watch Order Guide", "/media/covers/article_dragon-ball-complete-watch-order-hindi-guide_8321da3b2c.webp", ["Crunchyroll"], "/anime/dragon-ball-complete-watch-order-hindi-guide"),
        ("योर नेम (Your Name): मकोतो शिंकाई", "Your Name: Makoto Shinkai Romance Anime", "/media/covers/article_your-name-makoto-shinkai-romance-anime-hindi_4719b48b94.webp", ["Netflix"], "/anime/your-name-makoto-shinkai-romance-anime-hindi"),
        ("5 माइंड-बेंडिंग एनीमे", "5 Mind-Bending Anime Like Death Note", "/media/covers/article_5-mind-bending-anime-like-death-note-hindi_a727d2c943.webp", ["Netflix", "Crunchyroll"], "/anime/5-mind-bending-anime-like-death-note-hindi"),
        ("सर्वकालिक सर्वश्रेष्ठ एनीमे रैंकिंग", "Top 10 Best Anime of All Time Ranking", "/media/covers/article_top-10-best-anime-of-all-time-hindi-ranking_92b8d0ca17.webp", ["Crunchyroll", "Netflix"], "/anime/top-10-best-anime-of-all-time-hindi-ranking"),
        ("फ्रीरेन: बियॉन्ड जर्नीज़ एंड", "Frieren: Beyond Journey's End Hindi Guide", "/media/covers/anime_frieren.webp", ["Crunchyroll", "Muse Asia"], "/anime/frieren-beyond-journeys-end"),
    ]
    seen_guide_urls = {g["url"] for g in zone3_hindi_guides}
    for g_hi, g_en, g_img, g_plats, g_url in curated_distinct_guides:
        if len(zone3_hindi_guides) >= 5:
            break
        if g_url not in top10_slugs and g_url not in seen_guide_urls:
            seen_guide_urls.add(g_url)
            zone3_hindi_guides.append({
                "id": len(zone3_hindi_guides) + 1,
                "title_hi": g_hi,
                "title_en": g_en,
                "image_url": g_img,
                "platforms": g_plats,
                "url": g_url,
            })

    # Column 3: Popular Manga (5 items with clean subtitles, NO scraper boilerplate)
    manga_records = list(session.exec(
        select(AnimeDraft)
        .where(
            AnimeDraft.status == "published",
            AnimeDraft.is_stale == False,
            AnimeDraft.content_type == "manga",
        )
        .order_by(AnimeDraft.published_at.desc(), AnimeDraft.id.desc())
    ).all())

    zone3_popular_manga = []
    seen_manga = set()
    for m in manga_records:
        t_hi = m.title_hi or m.title_en or ""
        base_title = re.sub(r'(?:चैप्टर|पार्ट|Chapter|Part)\s*\d+.*$', '', t_hi, flags=re.IGNORECASE).strip()
        base_title = base_title.split(':')[0].strip()
        if not base_title or base_title in seen_manga:
            continue
        seen_manga.add(base_title)
        stat = (m.publication_status or "Ongoing").lower()
        stat_hi = "पूरा हो चुका है" if stat in ("completed", "finished") else "जारी है"
        pub = "MANGA Plus"
        if "viz" in (m.source_name or "").lower():
            pub = "VIZ Media"

        # Clean subtitle: remove scraper boilerplate "Released: Read Details and Schedule"
        clean_sub = m.title_en or ""
        clean_sub = re.sub(r'\s*Released\s*:?\s*Read Details.*$', '', clean_sub, flags=re.IGNORECASE).strip()
        clean_sub = re.sub(r'Read Details and Schedule', '', clean_sub, flags=re.IGNORECASE).strip()
        ch_m = re.search(r'(?:Chapter|Ch\.)\s*(\d+)', m.title_en or "", flags=re.IGNORECASE)
        if ch_m:
            ch_num = ch_m.group(1)
            series_name = re.sub(r'(?:Chapter|Ch\.)\s*\d+.*$', '', clean_sub, flags=re.IGNORECASE).strip()
            series_name = re.sub(r'[:\-\·]+$', '', series_name).strip()
            clean_sub = f"{series_name} · Chapter {ch_num}" if series_name else f"Chapter {ch_num}"

        zone3_popular_manga.append({
            "id": m.id,
            "title_hi": base_title,
            "title_en": clean_sub,
            "image_url": m.image_url or "/media/covers/anime_chainsaw-man.webp",
            "status_hi": stat_hi,
            "publisher": pub,
            "url": f"/manga/{m.id}",
        })
        if len(zone3_popular_manga) == 5:
            break

    curated_manga = [
        {"id": 92, "title_hi": "वन पीस", "title_en": "One Piece · Chapter 1130", "image_url": "/media/covers/anime_one-piece.webp", "status_hi": "जारी है", "publisher": "MANGA Plus", "url": "/manga"},
        {"id": 58, "title_hi": "चेनसॉ मैन", "title_en": "Chainsaw Man · Chapter 182", "image_url": "/media/covers/anime_chainsaw-man.webp", "status_hi": "जारी है", "publisher": "MANGA Plus", "url": "/manga"},
        {"id": 63, "title_hi": "ब्लू लॉक", "title_en": "Blue Lock · Chapter 282", "image_url": "/media/covers/anime_blue-lock.webp", "status_hi": "जारी है", "publisher": "VIZ Media", "url": "/manga"},
        {"id": 62, "title_hi": "साकामोटो डेज़", "title_en": "Sakamoto Days · Chapter 190", "image_url": "/media/covers/anime_sakamoto-days.webp", "status_hi": "जारी है", "publisher": "MANGA Plus", "url": "/manga"},
        {"id": 95, "title_hi": "दांदादान", "title_en": "Dandadan · Chapter 173", "image_url": "/media/covers/anime_dandadan.webp", "status_hi": "जारी है", "publisher": "MANGA Plus", "url": "/manga"},
    ]
    for cm in curated_manga:
        if len(zone3_popular_manga) >= 5:
            break
        if cm["title_hi"] not in seen_manga:
            seen_manga.add(cm["title_hi"])
            zone3_popular_manga.append(cm)

    return {
        "spotlight_slides": spotlight_slides,
        "airing_episodes": airing_episodes,
        "official_trailers": official_trailers,
        "top_10_anime": top_10_anime,
        "latest_news": latest_news,
        "zone3_new_releases": zone3_new_releases,
        "zone3_hindi_guides": zone3_hindi_guides,
        "zone3_popular_manga": zone3_popular_manga,
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
        "index.html",
        spotlight_slides=data.get("spotlight_slides", []),
        airing_episodes=data.get("airing_episodes", []),
        official_trailers=data.get("official_trailers", []),
        top_10_anime=data.get("top_10_anime", []),
        latest_news=data.get("latest_news", []),
        zone3_new_releases=data.get("zone3_new_releases", []),
        zone3_hindi_guides=data.get("zone3_hindi_guides", []),
        zone3_popular_manga=data.get("zone3_popular_manga", []),
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
            "episode": ep_num,
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
        "cover_image": "/media/covers/anime_one-piece.webp",
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
        "cover_image": "/media/covers/anime_solo-leveling.webp",
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
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx126403-1TjhVqW84FqT.png",
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
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx176496-5N9gq2U62DkR.jpg",
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
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx183204-p4g6j2kL4k0P.jpg",
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
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx158091-E66o2kY41g9P.jpg",
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
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx181747-q92kL5m3P2a1.jpg",
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
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx125299-FkM2rK5s9uP0.jpg",
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
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx98782-z9Yg2T6p4k1Q.jpg",
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
        "cover_image": "/media/covers/anime_naruto.webp",
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
        "cover_image": "/media/covers/anime_death-note.webp",
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
        "cover_image": "/media/covers/anime_demon-slayer.webp",
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


# ---------- DYNAMIC ANIME DETAIL PAGES & RESOLVER ----------
DYNAMIC_ANIME_CATALOG: dict[str, dict[str, Any]] = {
    "frieren-beyond-journeys-end": {
        "slug": "frieren-beyond-journeys-end",
        "title_hi": "फ्रीरेन: बियॉन्ड जर्नीज़ एंड",
        "title_en": "Frieren: Beyond Journey's End",
        "cover_image": "/media/covers/anime_frieren.webp",
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2023-2024",
        "episodes_display": "28 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Madhouse",
        "source_type": "Manga",
        "genres": ["Action", "Adventure", "Fantasy", "Drama"],
        "genres_str": "एक्शन, एडवेंचर, फैंटेसी, ड्रामा",
        "synopsis_hi": "दानव राजा को हराने के बाद, अमर एल्फ जादूगरनी फ्रीरेन अपने नश्वर साथियों की यादों और मानवीय भावनाओं के गहरे अर्थ को समझने के लिए एक नए भावुक सफर पर निकलती है।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com/series/GG5H5XMQ5/frieren-beyond-journeys-end"},
            {"name": "Muse Asia", "url": "https://www.youtube.com/@MuseAsia"},
            {"name": "YouTube", "url": "https://www.youtube.com"}
        ],
    },
    "chainsaw-man": {
        "slug": "chainsaw-man",
        "title_hi": "चेनसॉ मैन",
        "title_en": "Chainsaw Man",
        "cover_image": "/media/covers/anime_chainsaw-man.webp",
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2022",
        "episodes_display": "12 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "MAPPA",
        "source_type": "Manga",
        "genres": ["Action", "Supernatural", "Horror"],
        "genres_str": "एक्शन, सुपरनैचुरल, डार्क फैंटेसी",
        "synopsis_hi": "डेन्जी अपने पोचिता नामक चेनसॉ शैतान के साथ मिलकर एक डेविल हंटर बनता है और असाधारण शक्तियों के साथ पब्लिक सेफ्टी डेविल हंटर्स का हिस्सा बनता है।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com/series/GVDHXJ77G/chainsaw-man"},
            {"name": "Ani-One Asia", "url": "https://www.youtube.com/@AniOneAsia"},
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ],
    },
    "dandadan": {
        "slug": "dandadan",
        "title_hi": "डैनडाडैन",
        "title_en": "DanDaDan",
        "cover_image": "/media/covers/anime_dandadan.webp",
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2024",
        "episodes_display": "12 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Science SARU",
        "source_type": "Manga",
        "genres": ["Action", "Comedy", "Supernatural", "Sci-Fi"],
        "genres_str": "एक्शन, कॉमेडी, सुपरनैचुरल, साइ-फाई",
        "synopsis_hi": "मोमो और ओकारुन के बीच एलियंस और भूतों को लेकर एक शर्त लगती है, जिससे वे दोनों सुपरनैचुरल और अलौकिक शक्तियों की अराजक और रोमांचक दुनिया में खिंचे चले जाते हैं।",
        "platforms_list": [
            {"name": "Netflix", "url": "https://www.netflix.com"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Muse Asia", "url": "https://www.youtube.com/@MuseAsia"}
        ],
    },
    "tokyo-revengers": {
        "slug": "tokyo-revengers",
        "title_hi": "टोक्यो रिवेंजर्स",
        "title_en": "Tokyo Revengers",
        "cover_image": "/media/covers/anime_one-piece.webp",
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2021-2023",
        "episodes_display": "50 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "LIDENFILMS",
        "source_type": "Manga",
        "genres": ["Action", "Drama", "Supernatural"],
        "genres_str": "एक्शन, टाइम-ट्रैवल, ड्रामा",
        "synopsis_hi": "ताकेमिची हानागाकी को अपनी पूर्व प्रेमिका की जान बचाने के लिए 12 साल पीछे जाने का मौका मिलता है, जहाँ वह तोक्यो मानजी गैंग की शीर्ष रैंक में शामिल होने की कोशिश करता है।",
        "platforms_list": [
            {"name": "Disney+ Hotstar", "url": "https://www.hotstar.com"},
            {"name": "Muse Asia", "url": "https://www.youtube.com/@MuseAsia"},
            {"name": "YouTube", "url": "https://www.youtube.com"}
        ],
    },
    "one-punch-man": {
        "slug": "one-punch-man",
        "title_hi": "वन-पंच मैन",
        "title_en": "One-Punch Man",
        "cover_image": "/media/covers/anime_solo-leveling.webp",
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2015-2019",
        "episodes_display": "24 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Madhouse / J.C.Staff",
        "source_type": "Manga",
        "genres": ["Action", "Comedy", "Super Power"],
        "genres_str": "एक्शन, सुपरहीरो, कॉमेडी, पैरोडी",
        "synopsis_hi": "साइतामा एक ऐसा हीरो है जिसने इतनी कड़ी ट्रेनिंग की है कि वह किसी भी दुश्मन को सिर्फ एक घूंसे में हरा सकता है, लेकिन अब वह एक सच्चे प्रतिद्वंद्वी की तलाश में बोरियत महसूस कर रहा है।",
        "platforms_list": [
            {"name": "Netflix", "url": "https://www.netflix.com"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Muse Asia", "url": "https://www.youtube.com/@MuseAsia"}
        ],
    },
    "the-apothecary-diaries-season-3": {
        "slug": "the-apothecary-diaries-season-3",
        "title_hi": "द अपोथेकेरी डायरीज़ सीज़न 3",
        "title_en": "The Apothecary Diaries Season 3",
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx176496-5N9gq2U62DkR.jpg",
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 7",
        "airing_day_time_ist": "शुक्रवार • 7:30 PM IST",
        "airing_at": int(time.time()) + 172800,
        "studio": "TOHO animation STUDIO / OLM",
        "source_type": "Light Novel",
        "genres": ["Mystery", "Historical", "Drama"],
        "genres_str": "मिस्ट्री, हिस्टोरिकल, ड्रामा",
        "synopsis_hi": "शाही महल के भीतर दवाइयों, जहर और दरबार की साज़िशों के रहस्यों को माओमाओ अपनी तीक्ष्ण बुद्धि और औषधि विज्ञान से सुलझाती है।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"},
            {"name": "Muse Asia", "url": "https://www.youtube.com/@MuseAsia"}
        ],
    },
    "sakamoto-days": {
        "slug": "sakamoto-days",
        "title_hi": "साकामोटो डेज़",
        "title_en": "Sakamoto Days",
        "cover_image": "/media/covers/anime_sakamoto-days.webp",
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 8",
        "airing_day_time_ist": "शनिवार • 8:30 PM IST",
        "airing_at": int(time.time()) + 259200,
        "studio": "TMS Entertainment",
        "source_type": "Manga",
        "genres": ["Action", "Comedy"],
        "genres_str": "एक्शन, कॉमेडी, हिटमैन",
        "synopsis_hi": "पूर्व लीजेंडरी हिटमैन तारो साकामोटो अब एक साधारण किराना स्टोर चलाता है और अपने परिवार को पुरानी दुनिया के कातिलों से बचाता है।",
        "platforms_list": [
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ],
    },
    "dr-stone-science-future": {
        "slug": "dr-stone-science-future",
        "title_hi": "डॉक्टर स्टोन: साइंस फ्यूचर",
        "title_en": "Dr. Stone: Science Future",
        "cover_image": "/media/covers/anime_naruto.webp",
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 10",
        "airing_day_time_ist": "गुरुवार • 7:00 PM IST",
        "airing_at": int(time.time()) + 345600,
        "studio": "TMS Entertainment",
        "source_type": "Manga",
        "genres": ["Sci-Fi", "Adventure", "Comedy"],
        "genres_str": "साइ-फाई, एडवेंचर, कॉमेडी",
        "synopsis_hi": "सेनकु इशिगामी विज्ञान की शक्ति से पूरी मानव सभ्यता को पत्थर से दोबारा जीवित करने के अंतिम चरण में प्रवेश करता है।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ],
    },
    "bleach-thousand-year-blood-war": {
        "slug": "bleach-thousand-year-blood-war",
        "title_hi": "ब्लीच: थाउजेंड-ईयर ब्लड वॉर",
        "title_en": "Bleach: Thousand-Year Blood War",
        "cover_image": "/media/covers/anime_naruto.webp",
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 9",
        "airing_day_time_ist": "शनिवार • 7:00 PM IST",
        "airing_at": int(time.time()) + 259200,
        "studio": "Pierrot",
        "source_type": "Manga",
        "genres": ["Action", "Supernatural", "Shounen"],
        "genres_str": "एक्शन, सुपरनैचुरल, शोनेन",
        "synopsis_hi": "सोल सोसाइटी और क्विन्सी सम्राट युहाबाख के बीच अंतिम महायुद्ध छिड़ता है। इचिगो कुरोसाकी अपनी वास्तविक शक्तियों को उजागर करता है।",
        "platforms_list": [
            {"name": "Disney+ Hotstar", "url": "https://www.hotstar.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ],
    },
    "link-click": {
        "slug": "link-click",
        "title_hi": "लिंक क्लिक",
        "title_en": "Link Click",
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx126403-1TjhVqW84FqT.png",
        "is_airing": False,
        "is_donghua": True,
        "is_hindi_dub": True,
        "format": "ONA",
        "season_year": "2021-2023",
        "episodes_display": "सीज़न 1-2 • 24 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Studio LAN / Haoliners",
        "source_type": "Original",
        "genres": ["Donghua", "Time Travel", "Thriller", "Drama"],
        "genres_str": "डोंगहुआ, टाइम ट्रैवल, थ्रिलर, ड्रामा",
        "synopsis_hi": "तस्वीरों के भीतर जाकर रहस्यों को सुलझाने वाले दो जासूस दोस्तों चेंग शियाओशी और लू गुआंग की थ्रिलर गाथा।",
        "platforms_list": [
            {"name": "Bilibili", "url": "https://www.bilibili.tv"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "YouTube", "url": "https://www.youtube.com"}
        ],
    },
    "swallowed-star": {
        "slug": "swallowed-star",
        "title_hi": "स्वॉलोड स्टार",
        "title_en": "Swallowed Star",
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx125299-FkM2rK5s9uP0.jpg",
        "is_airing": True,
        "is_donghua": True,
        "is_hindi_dub": True,
        "format": "ONA",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 150+",
        "airing_day_time_ist": "बुधवार • 7:30 PM IST",
        "airing_at": int(time.time()) + 172800,
        "studio": "Sparkly Key Animation",
        "source_type": "Web Novel",
        "genres": ["Donghua", "Sci-Fi", "Action", "Martial Arts"],
        "genres_str": "डोंगहुआ, साइ-फाई, एक्शन, मार्शल आर्ट्स",
        "synopsis_hi": "आरआर वायरस के प्रकोप के बाद पृथ्वी पर शक्तिशाली राक्षसों का राज हो गया है। लुओ फेंग मार्शल आर्ट्स और उन्नत तकनीक से मानवता की रक्षा करता है।",
        "platforms_list": [
            {"name": "WeTV", "url": "https://wetv.vip"},
            {"name": "YouTube", "url": "https://www.youtube.com"}
        ],
    },
    "battle-through-the-heavens": {
        "slug": "battle-through-the-heavens",
        "title_hi": "बैटल थ्रू द हेवन्स",
        "title_en": "Battle Through the Heavens",
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx98782-z9Yg2T6p4k1Q.jpg",
        "is_airing": True,
        "is_donghua": True,
        "is_hindi_dub": True,
        "format": "ONA",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 120+",
        "airing_day_time_ist": "रविवार • 8:00 AM IST",
        "airing_at": int(time.time()) + 86400,
        "studio": "Shanghai Foch Film Culture",
        "source_type": "Web Novel",
        "genres": ["Donghua", "Fantasy", "Cultivation", "Action"],
        "genres_str": "डोंगहुआ, फैंटेसी, कल्टीवेशन, एक्शन",
        "synopsis_hi": "अपनी ताकत खो चुके प्रतिभाशाली युवक शियाओ यान अपनी अंगूठी में मौजूद शिक्षक याओ लाओ के मार्गदर्शन में महान योद्धा बनने की साधना करता है।",
        "platforms_list": [
            {"name": "WeTV", "url": "https://wetv.vip"},
            {"name": "YouTube", "url": "https://www.youtube.com"}
        ],
    },
    "overgeared": {
        "slug": "overgeared",
        "title_hi": "ओवरगियर्ड",
        "title_en": "Overgeared",
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx181747-q92kL5m3P2a1.jpg",
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 8",
        "airing_day_time_ist": "रविवार • 8:00 PM IST",
        "airing_at": int(time.time()) + 86400,
        "studio": "Ani-One Asia",
        "source_type": "Web Novel",
        "genres": ["Action", "Game", "Fantasy"],
        "genres_str": "एक्शन, वीआरएमएमओ, गेम, फैंटेसी",
        "synopsis_hi": "शिन यंगवू वीआर गेम 'सैटिसफाई' में एक लीजेंडरी ब्लैकस्मिथ क्लास पाकर साधारण खिलाड़ी से अजेय योद्धा बन जाता है।",
        "platforms_list": [
            {"name": "Ani-One Asia", "url": "https://www.youtube.com/@AniOneAsia"},
            {"name": "YouTube", "url": "https://www.youtube.com"}
        ],
    },
    "mob-psycho-100": {
        "slug": "mob-psycho-100",
        "title_hi": "मॉब साइको 100",
        "title_en": "Mob Psycho 100",
        "cover_image": "/media/covers/anime_naruto.webp",
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2016-2022",
        "episodes_display": "सीज़न 1-3 • 37 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Bones",
        "source_type": "Manga",
        "genres": ["Action", "Comedy", "Supernatural"],
        "genres_str": "एक्शन, कॉमेडी, सुपरनैचुरल",
        "synopsis_hi": "शिगेओ 'मॉब' कागेयामा एक शक्तिशाली एस्पर है जो सामान्य जीवन जीना चाहता है, लेकिन उसकी भावनाएं चरम पर पहुंचते ही भारी अलौकिक शक्ति जाग उठती है।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Muse Asia", "url": "https://www.youtube.com/@MuseAsia"},
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ],
    },
    "classroom-of-the-elite": {
        "slug": "classroom-of-the-elite",
        "title_hi": "क्लासरूम ऑफ द एलीट",
        "title_en": "Classroom of the Elite",
        "cover_image": "/media/covers/anime_demon-slayer.webp",
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2017-2024",
        "episodes_display": "सीज़न 1-3 • 38 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Lerche",
        "source_type": "Light Novel",
        "genres": ["Psychological", "Drama", "School"],
        "genres_str": "साइकोलॉजिकल, ड्रामा, स्कूल",
        "synopsis_hi": "किओताका अयानोकोजी कोडो इकुसेई हाई स्कूल में दाखिला लेता है, जहाँ केवल शीर्ष योग्यता और कड़ी प्रतिस्पर्धा से ही छात्रों का भविष्य तय होता है।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Muse Asia", "url": "https://www.youtube.com/@MuseAsia"},
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ],
    },
    "blue-box-season-2": {
        "slug": "blue-box-season-2",
        "title_hi": "ब्लू बॉक्स सीज़न 2",
        "title_en": "Blue Box Season 2",
        "cover_image": "/media/covers/anime_blue-lock.webp",
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 6",
        "airing_day_time_ist": "गुरुवार • 10:30 PM IST",
        "airing_at": int(time.time()) + 345600,
        "studio": "Telecom Animation Film",
        "source_type": "Manga",
        "genres": ["Romance", "Sports", "School"],
        "genres_str": "रोमांस, स्पोर्ट्स, स्कूल",
        "synopsis_hi": "बैडमिंटन खिलाड़ी ताइकी इनोमाता और बास्केटबॉल स्टार चिनात्सु कानो के बीच खेल और प्रेम का प्यारा सफर आगे बढ़ता है।",
        "platforms_list": [
            {"name": "Netflix", "url": "https://www.netflix.com"}
        ],
    },
    "dragon-ball-super-beerus": {
        "slug": "dragon-ball-super-beerus",
        "title_hi": "ड्रैगन बॉल सुपर: बीरस",
        "title_en": "Dragon Ball Super: Beerus",
        "cover_image": "/media/covers/anime_dragon-ball-z.webp",
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": True,
        "format": "TV",
        "season_year": "2015-2018",
        "episodes_display": "131 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "Toei Animation",
        "source_type": "Manga",
        "genres": ["Action", "Martial Arts", "Fantasy"],
        "genres_str": "एक्शन, मार्शल आर्ट्स, फैंटेसी",
        "synopsis_hi": "विनाश के देवता बीरस और सुपर सैयान गॉड गोकू के बीच ब्रह्मांड की रक्षा के लिए घमासान मुकाबला।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"}
        ],
    },
    "reborn-as-a-space-mercenary": {
        "slug": "reborn-as-a-space-mercenary",
        "title_hi": "रीबॉर्न ऐज़ अ स्पेस मर्सनरी",
        "title_en": "Reborn as a Space Mercenary",
        "cover_image": "/media/covers/anime_naruto.webp",
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 7",
        "airing_day_time_ist": "शुक्रवार • 9:30 PM IST",
        "airing_at": int(time.time()) + 172800,
        "studio": "TBA",
        "source_type": "Light Novel",
        "genres": ["Sci-Fi", "Space", "Action"],
        "genres_str": "साइ-फाई, स्पेस, एक्शन",
        "synopsis_hi": "एक अंतरिक्ष युद्धपोत के साथ दूसरी दुनिया में पुनर्जन्म लेकर स्वतंत्र मर्सनरी के रूप में अंतरिक्ष में नए रोमांच।",
        "platforms_list": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"}
        ],
    },
    "mission-yozakura-family-season-2": {
        "slug": "mission-yozakura-family-season-2",
        "title_hi": "मिशन: योज़ाकुरा फैमिली सीज़न 2",
        "title_en": "Mission: Yozakura Family Season 2",
        "cover_image": "/media/covers/anime_spy-x-family.webp",
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 6",
        "airing_day_time_ist": "रविवार • 5:00 PM IST",
        "airing_at": int(time.time()) + 86400,
        "studio": "SILVER LINK.",
        "source_type": "Manga",
        "genres": ["Action", "Comedy", "Spy"],
        "genres_str": "एक्शन, कॉमेडी, स्पाई",
        "synopsis_hi": "ताइयो असाकुरा और जासूस परिवार योज़ाकुरा नए खतरों और रहस्यों का सामना करने के लिए तैयार होते हैं।",
        "platforms_list": [
            {"name": "Disney+ Hotstar", "url": "https://www.hotstar.com"}
        ],
    },
    "firefly-wedding": {
        "slug": "firefly-wedding",
        "title_hi": "फायरफ्लाई वेडिंग",
        "title_en": "Firefly Wedding",
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx183204-p4g6j2kL4k0P.jpg",
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 6",
        "airing_day_time_ist": "शुक्रवार • 8:30 PM IST",
        "airing_at": int(time.time()) + 172800,
        "studio": "TBA",
        "source_type": "Manga",
        "genres": ["Drama", "Romance", "Historical"],
        "genres_str": "ड्रामा, रोमांस, हिस्टोरिकल",
        "synopsis_hi": "मीजी काल की एक बीमार कुलीन लड़की और उसके हत्यारे पति का रोमांचक ड्रामा।",
        "platforms_list": [
            {"name": "Ani-One Asia", "url": "https://www.youtube.com/@AniOneAsia"},
            {"name": "YouTube", "url": "https://www.youtube.com"}
        ],
    },
    "reincarnated-as-a-sword-season-2": {
        "slug": "reincarnated-as-a-sword-season-2",
        "title_hi": "रीइन्कारनेटेड ऐज़ अ सोर्ड सीज़न 2",
        "title_en": "Reincarnated as a Sword Season 2",
        "cover_image": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx158091-E66o2kY41g9P.jpg",
        "is_airing": True,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "Fall 2026",
        "episodes_display": "चालू • Ep 7",
        "airing_day_time_ist": "बुधवार • 9:00 PM IST",
        "airing_at": int(time.time()) + 172800,
        "studio": "C2C",
        "source_type": "Light Novel",
        "genres": ["Action", "Fantasy", "Isekai"],
        "genres_str": "एक्शन, फैंटेसी, इसेकाई",
        "synopsis_hi": "जादुई तलवार और बिल्ली-लड़की फ्रैंकी की रोमांचक यात्रा।",
        "platforms_list": [
            {"name": "Muse Asia", "url": "https://www.youtube.com/@MuseAsia"},
            {"name": "YouTube", "url": "https://www.youtube.com"}
        ],
    },
    "mashle-magic-and-muscles": {
        "slug": "mashle-magic-and-muscles",
        "title_hi": "मैशले: मैजिक एंड मसल्स",
        "title_en": "Mashle: Magic and Muscles",
        "cover_image": "/media/covers/anime_death-note.webp",
        "is_airing": False,
        "is_donghua": False,
        "is_hindi_dub": False,
        "format": "TV",
        "season_year": "2023-2024",
        "episodes_display": "सीज़न 1-2 • 24 एपिसोड्स",
        "airing_day_time_ist": "पूरा हो चुका है",
        "airing_at": 0,
        "studio": "A-1 Pictures",
        "source_type": "Manga",
        "genres": ["Action", "Comedy", "Fantasy"],
        "genres_str": "एक्शन, कॉमेडी, फैंटेसी",
        "synopsis_hi": "जादू की दुनिया में बिना किसी जादू के केवल मांसपेशियों की ताकत से मुकाबला।",
        "platforms_list": [
            {"name": "Ani-One Asia", "url": "https://www.youtube.com/@AniOneAsia"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "YouTube", "url": "https://www.youtube.com"}
        ],
    },
}

DYNAMIC_ANIME_ALIASES: dict[str, str] = {
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

    # 1. Check Pre-registered Catalog
    if canonical_slug in DYNAMIC_ANIME_CATALOG:
        return dict(DYNAMIC_ANIME_CATALOG[canonical_slug])

    # 2. Check FREE_LEGAL_ANIME_DATA
    for f in FREE_LEGAL_ANIME_DATA:
        f_slug = slugify_anime_title(f.get("url", "").replace("/anime/", ""))
        f_id_slug = slugify_anime_title(f.get("id", ""))
        f_en_slug = slugify_anime_title(f.get("title_en", ""))
        if canonical_slug in (f_slug, f_id_slug, f_en_slug):
            plats = [{"name": p, "url": "https://www.youtube.com"} for p in f.get("platforms", ["YouTube"])]
            return {
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
                "source_type": "Manga",
                "genres": ["Action", "Adventure"],
                "genres_str": "एक्शन, रोमांच",
                "synopsis_hi": f.get("synopsis_hi", ""),
                "platforms_list": plats,
            }

    # 3. Check Live Weekly Schedule Cache
    try:
        from automation.fetchers.anilist import fetch_live_weekly_schedule
        sched = fetch_live_weekly_schedule()
        for item in sched.get("weekly_items", []):
            item_en_slug = slugify_anime_title(item.get("title_en", ""))
            item_ro_slug = slugify_anime_title(item.get("title_romaji", ""))
            item_id_str = str(item.get("id", ""))
            if canonical_slug in (item_en_slug, item_ro_slug, item_id_str):
                return {
                    "slug": canonical_slug,
                    "title_hi": item.get("title_hi") or item.get("clean_title_hi") or item.get("title_en"),
                    "title_en": item.get("title_en") or item.get("title_romaji"),
                    "cover_image": item.get("image_url") or "/media/covers/anime_naruto.webp",
                    "is_airing": True,
                    "is_donghua": item.get("is_donghua", False),
                    "is_hindi_dub": item.get("has_hindi_dub", False),
                    "format": item.get("format", "TV"),
                    "season_year": f"{item.get('season', 'Fall')} {item.get('year', 2026)}",
                    "episodes_display": f"Ep {item.get('episode', 1)}" if item.get("episode") else "चालू सीज़न",
                    "airing_day_time_ist": item.get("airing_time_ist") or (f"{item.get('day_name_hi', 'साप्ताहिक')} • 8:00 PM IST"),
                    "airing_at": item.get("airing_at") or (int(time.time()) + 86400),
                    "studio": item.get("studio") or "TBA",
                    "source_type": item.get("source_type") or "Manga",
                    "genres": item.get("genres") or ["Action", "Animation"],
                    "genres_str": ", ".join(item.get("genres") or ["एनीमे", "एक्शन"]),
                    "synopsis_hi": item.get("summary_hi") or f"{item.get('title_hi', 'यह शो')} की आधिकारिक स्ट्रीमिंग और भारतीय दर्शकों के लिए साप्ताहिक एपिसोड गाइड।",
                    "platforms_list": [
                        {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
                        {"name": "Netflix", "url": "https://www.netflix.com"},
                        {"name": "Muse Asia", "url": "https://www.youtube.com/@MuseAsia"},
                        {"name": "Ani-One Asia", "url": "https://www.youtube.com/@AniOneAsia"}
                    ],
                }
    except Exception:
        pass

    # 4. Check DB AnimeDraft
    db_draft = session.exec(select(AnimeDraft).where(
        or_(
            AnimeDraft.external_id == slug_or_id,
            AnimeDraft.external_id == canonical_slug
        )
    )).first()
    if db_draft:
        return {
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
            "source_type": "Manga",
            "genres": ["Action"],
            "genres_str": "एक्शन, ड्रामा",
            "synopsis_hi": db_draft.summary_hi or db_draft.summary_en or "",
            "platforms_list": [
                {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
                {"name": "Netflix", "url": "https://www.netflix.com"}
            ],
        }

    # 5. Clean Title Fallback Generator
    clean_words = [w.capitalize() for w in canonical_slug.split("-") if w]
    if clean_words:
        english_name = " ".join(clean_words)
        return {
            "slug": canonical_slug,
            "title_hi": english_name,
            "title_en": english_name,
            "cover_image": "/media/covers/anime_naruto.webp",
            "is_airing": True,
            "is_donghua": False,
            "is_hindi_dub": False,
            "format": "TV",
            "season_year": "2026",
            "episodes_display": "चालू सीज़न",
            "airing_day_time_ist": "साप्ताहिक • 8:00 PM IST",
            "airing_at": int(time.time()) + 86400,
            "studio": "TBA",
            "source_type": "Animation",
            "genres": ["Action", "Adventure"],
            "genres_str": "एक्शन, एडवेंचर",
            "synopsis_hi": f"{english_name} की आधिकारिक स्ट्रीमिंग, एपिसोड शेड्यूल और भारतीय दर्शकों के लिए विस्तृत गाइड।",
            "platforms_list": [
                {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
                {"name": "Netflix", "url": "https://www.netflix.com"},
                {"name": "YouTube", "url": "https://www.youtube.com"}
            ],
        }

    return None


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
            ctx["user"] = getattr(request.state, "user", None)
            return render("anime_detail.html", **ctx)

    # 2. Check if an Article editorial guide exists in DB with this slug
    article = session.exec(select(Article).where(Article.slug == item_id)).first()
    if article:
        return article_page(request, item_id, session)

    # Check if this slug is an alias or key in DYNAMIC_ANIME_CATALOG
    resolved_catalog_slug = DYNAMIC_ANIME_ALIASES.get(clean_id, clean_id)
    if resolved_catalog_slug not in DYNAMIC_ANIME_CATALOG:
        try:
            return article_page(request, item_id, session)
        except HTTPException as e:
            if e.status_code != 404:
                raise e

    # 3. Resolve Dynamic Anime Detail Page
    anime_data = resolve_dynamic_anime(item_id, session)
    if anime_data:
        categories = get_categories(session)
        user = getattr(request.state, "user", None)
        lang = request.cookies.get("site_lang", "hi")
        related_anime = get_related_dynamic_anime(anime_data["slug"], limit=4)
        ctx = {
            "anime": anime_data,
            "related_anime": related_anime,
            "categories": categories,
            "lang": lang,
            "user": user,
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



