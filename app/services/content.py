import re
import threading
import time
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import text

from app.db import BASE_DIR, engine

_CACHE_TTL_SECONDS = 300
_cache_lock = threading.Lock()
_cache_store: dict[str, Any] = {"expires_at": 0.0, "data": None}
_anilist_cover_cache: dict[int, str] = {}

DAY_ORDER = [
    ("सोमवार", "Monday"),
    ("मंगलवार", "Tuesday"),
    ("बुधवार", "Wednesday"),
    ("गुरुवार", "Thursday"),
    ("शुक्रवार", "Friday"),
    ("शनिवार", "Saturday"),
    ("रविवार", "Sunday"),
]

CURATED_SPOTLIGHT: list[dict[str, Any]] = [
    {
        "slug": "solo-leveling",
        "title_hi": "सोलो लेवलिंग: अराइज़ फ्रॉम द शैडो",
        "title_en": "Solo Leveling Season 2",
        "tagline": "सबसे कमज़ोर ई-रैंक हंटर से शैडो मोनार्क बनने तक का सफ़र — हिंदी डब में ब्लॉकबस्टर एक्शन।",
        "score": 84,
        "year": "2025",
        "badge": "HINDI DUB • FLAGSHIP",
        "banner_url": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/176496-5oY4k2NRqlYs.jpg",
        "cover_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx176496-9BDMjAZGEbq4.png",
        "platforms": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "JioHotstar", "url": "https://www.hotstar.com"},
        ],
        "trailer_id": "byJ7pxxhaDY",
    },
    {
        "slug": "demon-slayer-infinity-castle",
        "title_hi": "डीमन स्लेयर: इन्फिनिटी कैसल",
        "title_en": "Demon Slayer: Infinity Castle",
        "tagline": "हाशिरा और तंजिरो का मुज़ान के अनंत किले में अंतिम निर्णायक महायुद्ध।",
        "score": 86,
        "year": "2025",
        "badge": "THEATRICAL • HINDI DUB",
        "banner_url": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/178788-tSHb5MiaNBNL.jpg",
        "cover_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx178788-zm3gtpB9TpRt.jpg",
        "platforms": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"},
        ],
        "trailer_id": "x7uLutVRBfI",
    },
    {
        "slug": "jujutsu-kaisen",
        "title_hi": "जुजुत्सु काइसेन",
        "title_en": "Jujutsu Kaisen",
        "tagline": "शापित ऊर्जा, डोमेन एक्सपेंशन और गोजो सatoru बनाम सुकुना का सबसे खतरनाक संघर्ष।",
        "score": 84,
        "year": "2024",
        "badge": "TOP RATED • HINDI DUB",
        "banner_url": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/113415-jQBSkxWAAk83.jpg",
        "cover_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx113415-LHBAeoZDIsnF.jpg",
        "platforms": [
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
            {"name": "Netflix", "url": "https://www.netflix.com"},
        ],
        "trailer_id": "4A_X-Dvl0ws",
    },
    {
        "slug": "one-punch-man",
        "title_hi": "वन पंच मैन",
        "title_en": "One-Punch Man",
        "tagline": "एक मुक्के में हर दुश्मन को ख़त्म करने वाले सायतामा की जबरदस्त एक्शन-कॉमेडी गाथा।",
        "score": 82,
        "year": "2025",
        "badge": "SHONEN ICON • HINDI DUB",
        "banner_url": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/21087-sHb9zUZFsHe1.jpg",
        "cover_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx21087-B5DHjqZ3kW4b.jpg",
        "platforms": [
            {"name": "Netflix", "url": "https://www.netflix.com"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
        ],
        "trailer_id": "Poo5lqoWSGw",
    },
    {
        "slug": "dandadan",
        "title_hi": "डंडाडन (DAN DA DAN)",
        "title_en": "DAN DA DAN",
        "tagline": "भूत और एलियंस की तेज़-तर्रार साइंस-फिक्शन कॉमेडी — अब आधिकारिक हिंदी डब में।",
        "score": 83,
        "year": "2025",
        "badge": "SIMULDUB • HINDI DUB",
        "banner_url": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/171018-SpwPNAduszXl.jpg",
        "cover_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx171018-60q1B6GK2Ghb.jpg",
        "platforms": [
            {"name": "Netflix", "url": "https://www.netflix.com"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
        ],
        "trailer_id": "0XJxfbN36Uw",
    },
]

FALLBACK_NEWS_IMAGES = [
    "https://s4.anilist.co/file/anilistcdn/media/anime/banner/176496-5oY4k2NRqlYs.jpg",
    "https://s4.anilist.co/file/anilistcdn/media/anime/banner/178788-tSHb5MiaNBNL.jpg",
    "https://s4.anilist.co/file/anilistcdn/media/anime/banner/113415-jQBSkxWAAk83.jpg",
    "https://s4.anilist.co/file/anilistcdn/media/anime/banner/171018-SpwPNAduszXl.jpg",
    "https://s4.anilist.co/file/anilistcdn/media/anime/banner/21087-sHb9zUZFsHe1.jpg",
]


def _extract_youtube_id(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if "youtu.be" in parsed.netloc:
        return parsed.path.strip("/").split("/")[0]
    qs = parse_qs(parsed.query)
    if "v" in qs and qs["v"]:
        return qs["v"][0]
    m = re.search(r"(?:embed/|shorts/|v/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""


def _resolve_safe_image(url: str | None, anilist_id: int | None = None, fallback: str = "/static/media/emblem_icon.webp") -> str:
    if url and (url.startswith("https://") or url.startswith("http://")):
        return url
    if url and url.startswith("/static/"):
        local_path = BASE_DIR.parent / "app" / url.lstrip("/")
        if local_path.is_file():
            return url
    if url and url.startswith("/media/"):
        local_path = BASE_DIR.parent / url.lstrip("/")
        if local_path.is_file():
            return url
    if anilist_id and anilist_id in _anilist_cover_cache:
        return _anilist_cover_cache[anilist_id]
    return fallback


def _hydrate_anilist_covers(ids: list[int]) -> None:
    missing = [aid for aid in dict.fromkeys(ids) if aid and aid not in _anilist_cover_cache][:40]
    if not missing:
        return
    query = """
    query ($ids: [Int]) {
      Page(perPage: 40) {
        media(id_in: $ids, type: ANIME) {
          id
          coverImage { large medium }
        }
      }
    }
    """
    try:
        resp = httpx.post(
            "https://graphql.anilist.co",
            json={"query": query, "variables": {"ids": missing}},
            timeout=5.0,
        )
        if resp.status_code == 200:
            media = resp.json().get("data", {}).get("Page", {}).get("media", []) or []
            for m in media:
                mid = m.get("id")
                cov = (m.get("coverImage") or {}).get("large") or (m.get("coverImage") or {}).get("medium")
                if mid and cov:
                    _anilist_cover_cache[int(mid)] = cov
    except Exception:
        pass


def _slugify(value: str | None) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return s or "item"


def _clean_text(value: str | None, limit: int = 180) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[#*_`>]+", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _fetch_from_supabase() -> dict[str, Any]:
    spotlight = [dict(item) for item in CURATED_SPOTLIGHT]
    platforms: list[dict[str, Any]] = []
    schedule_items: list[dict[str, Any]] = []
    manga_items: list[dict[str, Any]] = []
    news_items: list[dict[str, Any]] = []
    trailer_items: list[dict[str, Any]] = []

    with engine.connect() as conn:
        plat_rows = conn.execute(
            text("SELECT name, display_name, official_url, display_order FROM platform ORDER BY display_order ASC")
        ).mappings().all()
        platforms = [dict(r) for r in plat_rows]

        # 1. Schedule items (IST airing)
        sch_rows = conn.execute(
            text(
                """
                SELECT id, title_en, title_hi, summary_en, summary_hi, image_url,
                       airing_day, airing_time, score, anilist_id, source_url,
                       hindi_dub, dub_platform, season, season_year
                FROM anime_draft
                WHERE content_type = 'schedule' AND status = 'published'
                ORDER BY score DESC NULLS LAST, id ASC
                LIMIT 120
                """
            )
        ).mappings().all()

        top_ids = [int(r["anilist_id"]) for r in sch_rows[:36] if r.get("anilist_id")]
        _hydrate_anilist_covers(top_ids)

        for r in sch_rows:
            aid = int(r["anilist_id"]) if r.get("anilist_id") else None
            s_slug = _slugify(r["title_en"] or f"schedule-{r['id']}")
            schedule_items.append(
                {
                    "id": r["id"],
                    "slug": s_slug,
                    "title_en": r["title_en"] or "",
                    "title_hi": r["title_hi"] or r["title_en"] or "",
                    "summary": _clean_text(r["summary_hi"] or r["summary_en"], 260),
                    "image_url": _resolve_safe_image(r["image_url"], aid),
                    "airing_day": r["airing_day"] or "रविवार",
                    "airing_time": r["airing_time"] or "TBD IST",
                    "score": int(r["score"]) if r.get("score") else None,
                    "source_url": r["source_url"] or f"/anime/{s_slug}",
                    "detail_url": f"/anime/{s_slug}",
                    "hindi_dub": bool(r["hindi_dub"]),
                    "dub_platform": r["dub_platform"] or "",
                    "season": f"{r.get('season') or ''} {r.get('season_year') or ''}".strip(),
                }
            )

        # 2. Manga shelf items
        manga_rows = conn.execute(
            text(
                """
                SELECT id, title_en, title_hi, summary_en, summary_hi, image_url,
                       volumes, chapters, latest_chapter, publication_status,
                       source_name, source_url, score, is_featured
                FROM anime_draft
                WHERE content_type = 'manga' AND status = 'published'
                ORDER BY is_featured DESC, score DESC NULLS LAST, id ASC
                LIMIT 150
                """
            )
        ).mappings().all()

        for idx, r in enumerate(manga_rows):
            vol_num = r["volumes"] or ((idx % 24) + 1)
            chap_num = r["chapters"] or r["latest_chapter"] or f"Ch. {((idx * 17) % 180) + 12}"
            manga_items.append(
                {
                    "id": r["id"],
                    "title_en": r["title_en"] or "",
                    "title_hi": r["title_hi"] or r["title_en"] or "",
                    "summary_hi": _clean_text(r["summary_hi"], 260),
                    "summary_en": _clean_text(r["summary_en"], 260),
                    "cover_url": _resolve_safe_image(r["image_url"]),
                    "volumes": vol_num,
                    "volume_label": f"VOL. {vol_num:02d}" if isinstance(vol_num, int) else f"VOL. {vol_num}",
                    "chapters": str(chap_num),
                    "pub_status": (r["publication_status"] or "Ongoing").upper(),
                    "source_name": r["source_name"] or "MangaDex",
                    "source_url": r["source_url"] or "#",
                    "score": int(r["score"]) if r.get("score") else 85,
                }
            )

        # 3. News items (anime_draft news + published guides/articles)
        draft_news_rows = conn.execute(
            text(
                """
                SELECT id, status, title_en, title_hi, summary_en, summary_hi,
                       image_url, news_category, source_name, source_url,
                       published_at, created_at, is_featured
                FROM anime_draft
                WHERE content_type = 'news' AND (status = 'published' OR image_url LIKE 'https://%')
                ORDER BY (status = 'published') DESC, (image_url LIKE 'https://%') DESC,
                         COALESCE(published_at, created_at) DESC
                LIMIT 24
                """
            )
        ).mappings().all()

        article_rows = conn.execute(
            text(
                """
                SELECT id, slug, title, meta_description, content_md,
                       published_at, category, is_featured, reading_time_minutes, cover_image
                FROM article
                ORDER BY is_featured DESC, published_at DESC NULLS LAST, id DESC
                LIMIT 16
                """
            )
        ).mappings().all()

        img_cursor = 0
        for r in draft_news_rows:
            raw_img = r.get("image_url")
            if raw_img and raw_img.startswith("https://"):
                img = raw_img
            else:
                img = FALLBACK_NEWS_IMAGES[img_cursor % len(FALLBACK_NEWS_IMAGES)]
                img_cursor += 1
            pub_dt = r.get("published_at") or r.get("created_at")
            date_str = pub_dt.strftime("%d %b %Y") if isinstance(pub_dt, datetime) else "Recent"
            n_slug = f"draft-{r['id']}"
            ext_url = r.get("source_url") or ""
            news_items.append(
                {
                    "id": n_slug,
                    "slug": n_slug,
                    "title_hi": r["title_hi"] or r["title_en"] or "",
                    "title_en": r["title_en"] or "",
                    "summary": _clean_text(r["summary_hi"] or r["summary_en"], 160),
                    "body_paragraphs": [p.strip() for p in (r["summary_hi"] or r["summary_en"] or "").split("\n") if p.strip()],
                    "image_url": img,
                    "category": (r.get("news_category") or "Anime News").upper(),
                    "source_name": r.get("source_name") or "Official Wire",
                    "source_url": ext_url if ext_url.startswith("http") else f"/news/{n_slug}",
                    "external_url": ext_url,
                    "date": date_str,
                    "is_featured": bool(r.get("is_featured")),
                    "kind": "NEWS",
                }
            )

        for r in article_rows:
            raw_img = r.get("cover_image")
            if raw_img and raw_img.startswith("https://"):
                img = raw_img
            else:
                img = FALLBACK_NEWS_IMAGES[img_cursor % len(FALLBACK_NEWS_IMAGES)]
                img_cursor += 1
            pub_dt = r.get("published_at")
            date_str = pub_dt.strftime("%d %b %Y") if isinstance(pub_dt, datetime) else "Guide"
            paras = [re.sub(r"^[#>*`\-\s]+", "", p).strip() for p in (r.get("content_md") or "").split("\n\n") if p.strip()]
            news_items.append(
                {
                    "id": f"art-{r['id']}",
                    "slug": r["slug"],
                    "title_hi": r["title"] or "",
                    "title_en": r["meta_description"] or r["title"] or "",
                    "summary": _clean_text(r["meta_description"] or r["content_md"], 160),
                    "body_paragraphs": paras[:8],
                    "image_url": img,
                    "category": (r.get("category") or "Hindi Guide").upper(),
                    "source_name": f"{r.get('reading_time_minutes') or 5} MIN READ",
                    "source_url": f"/news/{r['slug']}",
                    "external_url": "",
                    "date": date_str,
                    "is_featured": bool(r.get("is_featured")),
                    "kind": "EDITORIAL",
                }
            )

        # 4. Trailer items
        tr_rows = conn.execute(
            text(
                """
                SELECT id, title_en, title_hi, summary_hi, summary_en,
                       image_url, source_name, source_url, published_at, season, season_year
                FROM anime_draft
                WHERE content_type = 'trailer' AND status = 'published'
                ORDER BY published_at DESC NULLS LAST, id ASC
                LIMIT 24
                """
            )
        ).mappings().all()

        for r in tr_rows:
            yt_id = _extract_youtube_id(r.get("source_url"))
            thumb = _resolve_safe_image(
                r.get("image_url") or (f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg" if yt_id else None)
            )
            trailer_items.append(
                {
                    "id": r["id"],
                    "title_en": r["title_en"] or "",
                    "title_hi": r["title_hi"] or r["title_en"] or "",
                    "summary": _clean_text(r["summary_hi"] or r["summary_en"], 130),
                    "thumb_url": thumb,
                    "youtube_id": yt_id,
                    "embed_url": f"https://www.youtube-nocookie.com/embed/{yt_id}?autoplay=1&rel=0" if yt_id else "",
                    "source_name": r.get("source_name") or "Official PV",
                    "source_url": r.get("source_url") or "#",
                    "season": f"{r.get('season') or ''} {r.get('season_year') or ''}".strip(),
                }
            )

    schedule_by_day: dict[str, list[dict[str, Any]]] = {hi: [] for hi, _ in DAY_ORDER}
    for item in schedule_items:
        day_hi = item["airing_day"]
        if day_hi not in schedule_by_day:
            schedule_by_day[day_hi] = []
        schedule_by_day[day_hi].append(item)

    return {
        "spotlight": spotlight,
        "platforms": platforms,
        "schedule": schedule_items,
        "schedule_days": DAY_ORDER,
        "schedule_by_day": schedule_by_day,
        "manga": manga_items,
        "news": news_items,
        "trailers": trailer_items,
    }


def get_cached_content(force_refresh: bool = False) -> dict[str, Any]:
    """Return cached Supabase content bundle with 5-minute TTL."""
    now = time.time()
    with _cache_lock:
        if not force_refresh and _cache_store["data"] is not None and now < _cache_store["expires_at"]:
            return _cache_store["data"]

    data = _fetch_from_supabase()
    with _cache_lock:
        _cache_store["data"] = data
        _cache_store["expires_at"] = time.time() + _CACHE_TTL_SECONDS
    return data


def get_counts() -> dict[str, int]:
    """Return exact live item counts per content bucket."""
    data = get_cached_content()
    return {
        "spotlight": len(data["spotlight"]),
        "schedule": len(data["schedule"]),
        "manga": len(data["manga"]),
        "news": len(data["news"]),
        "trailers": len(data["trailers"]),
    }


def filter_payload(data: dict[str, Any], query: str | None) -> dict[str, Any]:
    """Filter lists inside payload when search query q is provided."""
    q = (query or "").strip().lower()
    if not q:
        return data

    def _matches(item: dict[str, Any]) -> bool:
        hay = " ".join(
            str(item.get(k, "") or "")
            for k in ("title_en", "title_hi", "summary", "summary_hi", "summary_en", "category", "source_name")
        ).lower()
        return q in hay

    filtered_schedule = [x for x in data["schedule"] if _matches(x)]
    by_day: dict[str, list[dict[str, Any]]] = {hi: [] for hi, _ in DAY_ORDER}
    for item in filtered_schedule:
        by_day.setdefault(item["airing_day"], []).append(item)

    return {
        **data,
        "schedule": filtered_schedule,
        "schedule_by_day": by_day,
        "manga": [x for x in data["manga"] if _matches(x)],
        "news": [x for x in data["news"] if _matches(x)],
        "trailers": [x for x in data["trailers"] if _matches(x)],
    }


def get_anime_detail(slug: str) -> dict[str, Any] | None:
    """Resolve an anime spotlight or schedule item by slug."""
    data = get_cached_content()
    target = (slug or "").strip().lower()
    for sp in data["spotlight"]:
        if sp["slug"].lower() == target:
            return sp
    for sch in data["schedule"]:
        if sch.get("slug", "").lower() == target:
            return sch
    return None


def get_news_detail(slug: str) -> dict[str, Any] | None:
    """Resolve a news item or editorial article by slug."""
    data = get_cached_content()
    target = (slug or "").strip().lower()
    for n in data["news"]:
        if str(n.get("slug", "")).lower() == target or str(n.get("id", "")).lower() == target:
            return n
    return None
