import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
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
        "title_en": "Solo Leveling: Arise from the Shadow",
        "tagline": "सबसे कमज़ोर ई-रैंक हंटर से शैडो मोनार्क बनने तक का सफ़र — हिंदी डब में ब्लॉकबस्टर एक्शन।",
        "tagline_en": "From the weakest E-Rank hunter to the Shadow Monarch — blockbuster action now in Hindi Dub.",
        "score": 87,
        "year": "2026",
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
        "tagline_en": "The Hashira and Tanjiro enter Muzan's Infinity Castle for the final decisive battle.",
        "score": 89,
        "year": "2026",
        "badge": "THEATRICAL HIT • HINDI DUB",
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
        "title_hi": "जुजुत्सु काइसेन: शिबुया व कलिंग गेम युग",
        "title_en": "Jujutsu Kaisen",
        "tagline": "शापित ऊर्जा, डोमेन एक्सपेंशन और गोजो सतोरू बनाम सुकुना का सबसे खतरनाक संघर्ष।",
        "tagline_en": "Cursed energy, Domain Expansions, and the explosive clash between Gojo Satoru and Sukuna.",
        "score": 86,
        "year": "2026",
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
        "slug": "dandadan",
        "title_hi": "डंडाडन (DAN DA DAN)",
        "title_en": "DAN DA DAN",
        "tagline": "भूत और एलियंस की तेज़-तर्रार साइंस-फिक्शन कॉमेडी — अब आधिकारिक हिंदी डब में।",
        "tagline_en": "Ghosts, aliens, and chaotic supernatural action — streaming with official Hindi Dub.",
        "score": 85,
        "year": "2026",
        "badge": "SIMULDUB 2026 • HINDI DUB",
        "banner_url": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/171018-SpwPNAduszXl.jpg",
        "cover_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx171018-60q1B6GK2Ghb.jpg",
        "platforms": [
            {"name": "Netflix", "url": "https://www.netflix.com"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
        ],
        "trailer_id": "0XJxfbN36Uw",
    },
    {
        "slug": "one-punch-man",
        "title_hi": "वन पंच मैन",
        "title_en": "One-Punch Man",
        "tagline": "एक मुक्के में हर दुश्मन को ख़त्म करने वाले सायतामा की जबरदस्त एक्शन-कॉमेडी गाथा।",
        "tagline_en": "Saitama defeats any threat with a single punch in this high-octane action comedy icon.",
        "score": 84,
        "year": "2026",
        "badge": "SHONEN ICON • HINDI DUB",
        "banner_url": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/21087-sHb9zUZFsHe1.jpg",
        "cover_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx21087-B5DHjqZ3kW4b.jpg",
        "platforms": [
            {"name": "Netflix", "url": "https://www.netflix.com"},
            {"name": "Crunchyroll", "url": "https://www.crunchyroll.com"},
        ],
        "trailer_id": "Poo5lqoWSGw",
    },
]

# Verified November 2026 Manga Facts + Anime-to-Manga Continuation Map keyed by AniList Manga ID
MANGA_NOV2026_FACTS: dict[int, dict[str, Any]] = {
    30013: {  # One Piece
        "chapters": 1199, "volumes": 116, "status": "RELEASING",
        "cont": "Chapter 1125+ (After Egghead Anime) / Ch. 1086 (Post-Wano)",
        "cont_hi": "एगहेड एनीमे के बाद Chapter 1125+ (और वानो के बाद Ch. 1086) से पढ़ें",
    },
    101517: {  # Jujutsu Kaisen
        "chapters": 272, "volumes": 30, "status": "FINISHED",
        "cont": "Chapter 138 (Post-Season 2 Shibuya Incident)",
        "cont_hi": "सीज़न 2 (शिबुया आर्क) के बाद Chapter 138 (Culling Game) से शुरू करें",
    },
    105778: {  # Chainsaw Man
        "chapters": 232, "volumes": 24, "status": "FINISHED",
        "cont": "Chapter 39 (After Anime S1) / Chapter 53 (Post-Reze Movie)",
        "cont_hi": "सीज़न 1 के बाद Chapter 39 (और Reze आर्क के बाद Ch. 53) से पढ़ें",
    },
    108556: {  # SPY x FAMILY
        "chapters": 144, "volumes": 18, "status": "RELEASING",
        "cont": "Mission 60 (After Season 2 Cruise Arc)",
        "cont_hi": "सीज़न 2 क्रूज़ आर्क के बाद Mission 60 से आगे पढ़ें",
    },
    132029: {  # Dandadan
        "chapters": 254, "volumes": 26, "status": "RELEASING",
        "cont": "Chapter 34 (After Anime S1) / Chapter 72 (After S2 Cursed House & Kaiju)",
        "cont_hi": "एनीमे S1 के बाद Chapter 34 (और S2 के बाद Ch. 72) से जारी रखें",
    },
    105398: {  # Solo Leveling (Original)
        "chapters": 201, "volumes": 15, "status": "FINISHED",
        "cont": "Chapter 111 (After Anime Season 2 Jeju Island vs Beru)",
        "cont_hi": "सीज़न 2 (जेजू आइलैंड आर्क) के तुरंत बाद Chapter 111 से पढ़ें",
    },
    179445: {  # Solo Leveling: Ragnarok
        "chapters": 75, "volumes": 3, "status": "RELEASING",
        "cont": "Sequel Series — Read Original Solo Leveling Ch. 111–200 first, then Ragnarok Ep. 1",
        "cont_hi": "मूल Solo Leveling Ch. 200 पूरा करने के बाद Ragnarok Chapter 1 से शुरू करें",
    },
    125828: {  # Sakamoto Days
        "chapters": 284, "volumes": 29, "status": "FINISHED",
        "cont": "Chapter 38 (After Part 1) / Chapter 74 (After Cour 2 — JCC Infiltration)",
        "cont_hi": "एनीमे पार्ट 1 के बाद Chapter 38 (और पार्ट 2 के बाद Ch. 74) से पढ़ें",
    },
    106130: {  # Blue Lock
        "chapters": 370, "volumes": 41, "status": "RELEASING",
        "cont": "Chapter 150 (After Anime Season 2 U-20 Match — Neo Egoist League)",
        "cont_hi": "सीज़न 2 (U-20 मैच) के तुरंत बाद Chapter 150 से पढ़ें",
    },
    30002: {  # Berserk
        "chapters": 380, "volumes": 43, "status": "RELEASING",
        "cont": "Chapter 1 Mandatory (Anime adaptations skip Black Swordsman & Lost Children arcs)",
        "cont_hi": "सीधे Chapter 1 से शुरू करें (एनीमे में कई प्रमुख आर्क छूटे हुए हैं)",
    },
    30656: {  # Vagabond
        "chapters": 327, "volumes": 37, "status": "HIATUS",
        "cont": "Manga Exclusive Masterpiece — Start at Chapter 1 (No Anime Adaptation)",
        "cont_hi": "कोई एनीमे नहीं है — सीधे Chapter 1 से शुरुआत करें",
    },
    30026: {  # Hunter x Hunter
        "chapters": 420, "volumes": 39, "status": "RELEASING",
        "cont": "Chapter 340 (After 2011 Anime Election Arc — Succession Contest)",
        "cont_hi": "2011 एनीमे (Ep. 148) समाप्त होने के बाद Chapter 340 से आगे पढ़ें",
    },
    118586: {  # Frieren
        "chapters": 147, "volumes": 15, "status": "HIATUS",
        "cont": "Chapter 61 (After Anime Season 1 First-Class Mage Exam)",
        "cont_hi": "एनीमे सीज़न 1 (Ep. 28) के बाद Chapter 61 से आगे पढ़ें",
    },
    74347: {  # One-Punch Man
        "chapters": 245, "volumes": 35, "status": "RELEASING",
        "cont": "Chapter 85 (After Anime Season 2 Elder Centipede Finale)",
        "cont_hi": "सीज़न 2 समाप्त होने के बाद Chapter 85 (Monster Association) से पढ़ें",
    },
    30642: {  # Vinland Saga
        "chapters": 220, "volumes": 29, "status": "FINISHED",
        "cont": "Chapter 101 (After Season 2 Farmland Arc — Eastern Expedition)",
        "cont_hi": "सीज़न 2 (फ़ार्मलैंड आर्क) के बाद Chapter 101 से आगे पढ़ें",
    },
    86123: {  # Black Clover
        "chapters": 374, "volumes": 38, "status": "FINISHED",
        "cont": "Chapter 270 (After Anime Ep. 170 Spade Kingdom Raid)",
        "cont_hi": "एनीमे Ep. 170 के बाद Chapter 270 (Spade Raid) से पढ़ें",
    },
    120760: {  # Kaiju No. 8
        "chapters": 129, "volumes": 16, "status": "FINISHED",
        "cont": "Chapter 39 (After Anime Season 1 Finale)",
        "cont_hi": "एनीमे सीज़न 1 के बाद Chapter 39 से आगे पढ़ें",
    },
    132182: {  # Blue Box
        "chapters": 250, "volumes": 28, "status": "FINISHED",
        "cont": "Chapter 81 (After Anime Season 1 Nationals Prep)",
        "cont_hi": "एनीमे सीज़न 1 के बाद Chapter 81 से आगे पढ़ें",
    },
    87216: {  # Demon Slayer
        "chapters": 207, "volumes": 23, "status": "FINISHED",
        "cont": "Chapter 140 (Infinity Castle Arc After Hashira Training S4)",
        "cont_hi": "हशिरा ट्रेनिंग आर्क के बाद Chapter 140 (Infinity Castle) से पढ़ें",
    },
    65243: {  # Haikyu!!
        "chapters": 404, "volumes": 45, "status": "FINISHED",
        "cont": "Chapter 292 (Battle of the Garbage Dump) / Ch. 326 (Post-Movie)",
        "cont_hi": "सीज़न 4 के बाद Chapter 292 (और Dumpster Battle मूवी के बाद Ch. 326) से पढ़ें",
    },
    63327: {  # Tokyo Ghoul
        "chapters": 144, "volumes": 14, "status": "FINISHED",
        "cont": "Chapter 1 Recommended (Season 2 Root A diverges heavily from Manga)",
        "cont_hi": "Chapter 1 से पढ़ें (एनीमे Root A मंगा की असली कहानी से अलग है)",
    },
    85611: {  # Tokyo Ghoul:re
        "chapters": 181, "volumes": 16, "status": "FINISHED",
        "cont": "Read after Tokyo Ghoul Ch. 143 (Start at :re Chapter 1)",
        "cont_hi": "मूल Tokyo Ghoul Ch. 143 के बाद :re Chapter 1 से पढ़ें",
    },
    86399: {  # That Time I Got Reincarnated as a Slime
        "chapters": 148, "volumes": 29, "status": "RELEASING",
        "cont": "Chapter 119 (After Anime Season 3 Founder's Festival)",
        "cont_hi": "सीज़न 3 के बाद Chapter 119 से आगे पढ़ें",
    },
    119257: {  # Omniscient Reader
        "chapters": 314, "volumes": 12, "status": "RELEASING",
        "cont": "Webtoon Exclusive (Anime in Production — Start Episode 1)",
        "cont_hi": "वेबटून एक्सक्लूसिव — सीधे Episode 1 से पढ़ना शुरू करें",
    },
    85143: {  # Tower of God
        "chapters": 652, "volumes": 18, "status": "HIATUS",
        "cont": "Season 2 Ch. 111 / Ep. 191 (After Anime Season 2 Workshop Battle)",
        "cont_hi": "एनीमे सीज़न 2 के बाद Webtoon Ch. 191 से आगे पढ़ें",
    },
    87395: {  # Grand Blue Dreaming
        "chapters": 110, "volumes": 24, "status": "RELEASING",
        "cont": "Chapter 22 (After Anime Season 1)",
        "cont_hi": "सीज़न 1 के बाद Chapter 22 से आगे पढ़ें",
    },
    86551: {  # Made in Abyss
        "chapters": 72, "volumes": 14, "status": "RELEASING",
        "cont": "Chapter 61 (After Season 2 Golden City of the Scorching Sun)",
        "cont_hi": "सीज़न 2 समाप्त होने के बाद Chapter 61 से पढ़ें",
    },
    99324: {  # Welcome to Demon School Iruma-kun
        "chapters": 415, "volumes": 42, "status": "RELEASING",
        "cont": "Chapter 148 (After Anime Season 3 Harvest Festival)",
        "cont_hi": "सीज़न 3 के बाद Chapter 148 से आगे पढ़ें",
    },
}

def _format_initial_countdown(airing_at: int | None, now_ts: int) -> str:
    """Return initial SSR countdown string (never blank '--:--:--') from Unix airing_at."""
    if not airing_at:
        return "LIVE"
    diff = max(0, int(airing_at) - int(now_ts))
    if diff == 0:
        return "LIVE NOW"
    days, rem = divmod(diff, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days > 0:
        return f"{days}d {hours:02d}h"
    return f"{hours:02d}:{mins:02d}:00"


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


def _parse_json_safe(raw: str | None) -> dict[str, Any]:
    if not raw or not isinstance(raw, str) or not raw.strip().startswith("{"):
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _ist_news_timing(dt: datetime | None) -> tuple[str, str, bool, int, bool, bool]:
    """Return (Hindi rel age, English rel age, is_fresh_48h, hours_left, is_brand_new, is_expiring_soon)."""
    if not isinstance(dt, datetime):
        return "अभी आया", "Just now", True, 47, True, False
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    delta_sec = max(0.0, (now_utc - dt).total_seconds())
    age_hours = delta_sec / 3600.0
    is_fresh = age_hours <= 48.0
    hours_left = max(1, min(48, int(round(48.0 - age_hours))))
    is_brand_new = age_hours <= 4.0
    is_expiring_soon = is_fresh and hours_left <= 12

    if delta_sec < 300:
        rel_hi, rel_en = "अभी आया", "Just now"
    elif delta_sec < 3600:
        mins = int(delta_sec // 60)
        rel_hi, rel_en = f"{mins} मिनट पहले", f"{mins}m ago"
    elif delta_sec < 86400:
        hrs = int(delta_sec // 3600)
        rel_hi, rel_en = f"{hrs} घंटे पहले", f"{hrs}h ago"
    else:
        days = int(delta_sec // 86400)
        rel_hi, rel_en = f"{days} दिन पहले", f"{days}d ago"
    return rel_hi, rel_en, is_fresh, hours_left, is_brand_new, is_expiring_soon


def _next_ist_weekday_ts(day_hi: str | None, time_str: str | None) -> int:
    """Compute next upcoming Unix timestamp for IST weekday/time when AniList airingAt missing."""
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    day_idx = next((i for i, (hi, _) in enumerate(DAY_ORDER) if hi == day_hi), now_ist.weekday())
    days_ahead = (day_idx - now_ist.weekday()) % 7
    hour, minute = 20, 0
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", time_str or "", re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        ampm = (m.group(3) or "").upper()
        if ampm == "PM" and hour < 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
    target = (now_ist + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now_ist:
        target += timedelta(days=7)
    return int(target.timestamp())


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

        # 1. Schedule items (Strictly RELEASING + deduplicated IST airing + real countdown timestamp)
        sch_rows = conn.execute(
            text(
                """
                SELECT id, title_en, title_hi, summary_en, summary_hi, image_url,
                       airing_day, airing_time, score, anilist_id, source_url,
                       hindi_dub, dub_platform, season, season_year,
                       official_links, latest_chapter, publication_status
                FROM anime_draft
                WHERE content_type = 'schedule'
                  AND status = 'published'
                  AND (publication_status IS NULL OR UPPER(publication_status) = 'RELEASING')
                ORDER BY (official_links IS NOT NULL AND official_links LIKE '{%') DESC, score DESC NULLS LAST, id ASC
                LIMIT 150
                """
            )
        ).mappings().all()

        missing_cov_ids = [
            int(r["anilist_id"])
            for r in sch_rows
            if r.get("anilist_id") and (not r.get("image_url") or not str(r.get("image_url")).startswith("http"))
        ]
        _hydrate_anilist_covers(missing_cov_ids)

        day_hi_to_en = {hi: en for hi, en in DAY_ORDER}
        now_ts = int(time.time())
        seen_sch_keys: set[str] = set()
        for r in sch_rows:
            aid = int(r["anilist_id"]) if r.get("anilist_id") else None
            dedup_key = f"aid:{aid}" if aid else re.sub(r"[^a-z0-9]+", "", (r["title_en"] or "").lower())
            if dedup_key in seen_sch_keys:
                continue
            seen_sch_keys.add(dedup_key)

            s_slug = _slugify(r["title_en"] or f"schedule-{r['id']}")
            meta = _parse_json_safe(r.get("official_links"))
            raw_airing_at = meta.get("airing_at")
            if isinstance(raw_airing_at, (int, float)) and raw_airing_at > now_ts:
                airing_at = int(raw_airing_at)
            else:
                airing_at = _next_ist_weekday_ts(r["airing_day"], r["airing_time"])
            day_hi = r["airing_day"] or "रविवार"
            schedule_items.append(
                {
                    "id": r["id"],
                    "anilist_id": aid,
                    "slug": s_slug,
                    "title_en": r["title_en"] or "",
                    "title_hi": r["title_hi"] or r["title_en"] or "",
                    "summary": _clean_text(r["summary_hi"] or r["summary_en"], 260),
                    "summary_en": _clean_text(r["summary_en"] or r["summary_hi"], 260),
                    "image_url": _resolve_safe_image(r["image_url"], aid),
                    "airing_day": day_hi,
                    "airing_day_en": day_hi_to_en.get(day_hi, "Sunday"),
                    "airing_time": r["airing_time"] or "TBD IST",
                    "airing_at": airing_at,
                    "initial_countdown": _format_initial_countdown(airing_at, now_ts),
                    "episode_label": r.get("latest_chapter") or (f"Ep. {meta['episode']}" if meta.get("episode") else "Ongoing"),
                    "score": int(r["score"]) if r.get("score") else None,
                    "source_url": r["source_url"] or f"/anime/{s_slug}",
                    "detail_url": f"/anime/{s_slug}",
                    "hindi_dub": bool(r["hindi_dub"]),
                    "dub_platform": r["dub_platform"] or "",
                    "season": f"{r.get('season') or ''} {r.get('season_year') or ''}".strip(),
                }
            )

        # 2. Manga shelf items (Nov 2026 chapter/volume facts + continuation guide + zero placeholders)
        manga_rows = conn.execute(
            text(
                """
                SELECT id, anilist_id, title_en, title_hi, summary_en, summary_hi, image_url,
                       volumes, chapters, latest_chapter, publication_status,
                       source_name, source_url, score, is_featured,
                       official_links, season_year, start_date, end_date
                FROM anime_draft
                WHERE content_type = 'manga' AND status = 'published'
                  AND LOWER(COALESCE(title_en, '')) NOT LIKE '%av joyuu%'
                  AND LOWER(COALESCE(title_en, '')) NOT LIKE '%big dick%'
                ORDER BY (id BETWEEN 127 AND 134) DESC, (chapters IS NOT NULL AND volumes IS NOT NULL) DESC, is_featured DESC, score DESC NULLS LAST, id ASC
                LIMIT 180
                """
            )
        ).mappings().all()

        seen_manga_keys: set[str] = set()
        for idx, r in enumerate(manga_rows):
            maid = int(r["anilist_id"]) if r.get("anilist_id") else None
            m_key = f"aid:{maid}" if maid else re.sub(r"[^a-z0-9]+", "", (r["title_en"] or "").lower())
            if m_key in seen_manga_keys:
                continue
            seen_manga_keys.add(m_key)

            meta = _parse_json_safe(r.get("official_links"))
            fact = MANGA_NOV2026_FACTS.get(maid or -1, {})
            vol_num = fact.get("volumes") or r["volumes"]
            ch_num = fact.get("chapters") or r["chapters"]
            pub_raw = (fact.get("status") or r["publication_status"] or "RELEASING").upper()
            is_hiatus = pub_raw == "HIATUS"
            is_finished = pub_raw in ("FINISHED", "COMPLETED")
            start_y = meta.get("start_year") or r.get("season_year") or (r["start_date"].year if isinstance(r.get("start_date"), datetime) else None)
            end_y = meta.get("end_year") or (r["end_date"].year if isinstance(r.get("end_date"), datetime) else None)
            since_lbl = f"Since {start_y}" if start_y else "Active Serial"

            if is_finished:
                latest_vol_label = f"Completed • {vol_num} Vols" if vol_num else "Complete Collected Edition"
                chapters_label = f"{ch_num} Chapters (Complete)" if ch_num else (f"{vol_num} Volumes" if vol_num else "Complete Edition")
                serialization_label = f"पूर्ण हो चुका (Completed {end_y})" if end_y else "पूर्ण (Completed)"
                serialization_en = f"Completed ({end_y})" if end_y else "Completed"
                pub_status = "COMPLETED"
                status_badge_hi = "पूर्ण • COMPLETED"
                status_badge_en = "COMPLETED"
            elif is_hiatus:
                latest_vol_label = f"Vol. {vol_num} • On Hiatus" if vol_num else f"Hiatus Archive ({since_lbl})"
                chapters_label = f"{ch_num} Chapters (Hiatus)" if ch_num else f"On Hiatus ({since_lbl})"
                serialization_label = "विराम पर (On Hiatus)"
                serialization_en = "On Hiatus"
                pub_status = "HIATUS"
                status_badge_hi = "विराम पर • HIATUS"
                status_badge_en = "ON HIATUS"
            else:
                latest_vol_label = f"Vol. {vol_num} Released" if vol_num else f"Digital Volume ({since_lbl})"
                raw_ch = str(r["latest_chapter"] or "").strip()
                if raw_ch in ("Weekly Serialization", "Ongoing Serialization", ""):
                    raw_ch = ""
                chapters_label = f"{ch_num} Chapters" if ch_num else (raw_ch or f"Active Serial ({since_lbl})")
                serialization_label = f"जारी है • साप्ताहिक/मासिक ({since_lbl})"
                serialization_en = f"Releasing ({since_lbl})"
                pub_status = "ONGOING"
                status_badge_hi = "जारी है • RELEASING"
                status_badge_en = "RELEASING"

            cont_en = fact.get("cont") or ("Complete from Chapter 1" if is_finished else f"Start at Chapter 1 ({since_lbl})")
            cont_hi = fact.get("cont_hi") or ("सीधे Chapter 1 से शुरुआत करें" if is_finished else f"Chapter 1 से पढ़ना शुरू करें ({since_lbl})")
            badge_vol = f"VOL. {int(vol_num):02d}" if isinstance(vol_num, int) else ("COMPLETE" if is_finished else ("HIATUS" if is_hiatus else (f"EST. {start_y}" if start_y else "SERIAL")))
            if ch_num and vol_num:
                card_ch_short = f"{ch_num} Ch • {vol_num} Vols"
            elif ch_num:
                card_ch_short = f"{ch_num} Ch"
            elif vol_num:
                card_ch_short = f"{vol_num} Vols"
            elif is_finished:
                card_ch_short = "Completed"
            elif is_hiatus:
                card_ch_short = "On Hiatus"
            else:
                card_ch_short = "Ongoing"

            manga_items.append(
                {
                    "id": r["id"],
                    "anilist_id": maid,
                    "title_en": r["title_en"] or "",
                    "title_hi": r["title_hi"] or r["title_en"] or "",
                    "native_title": meta.get("native_title") or "",
                    "author": meta.get("author") or "Original Manga Creator",
                    "genres": meta.get("genres") or ["Action", "Shonen"],
                    "summary_hi": _clean_text(r["summary_hi"], 320),
                    "summary_en": _clean_text(r["summary_en"] or r["summary_hi"], 320),
                    "cover_url": _resolve_safe_image(r["image_url"]),
                    "volumes": vol_num,
                    "latest_volume": vol_num,
                    "chapters_released": ch_num,
                    "volume_label": badge_vol,
                    "latest_volume_label": latest_vol_label,
                    "chapters": str(chapters_label),
                    "chapters_label": str(chapters_label),
                    "card_chapters_short": card_ch_short,
                    "continue_from_chapter": cont_en,
                    "continue_from_en": cont_en,
                    "continue_from_hi": cont_hi,
                    "pub_status": pub_status,
                    "status_short": pub_status,
                    "status_badge_hi": status_badge_hi,
                    "status_badge_en": status_badge_en,
                    "serialization_label": serialization_label,
                    "serialization_en": serialization_en,
                    "start_year_label": str(start_y) if start_y else "Featured",
                    "source_name": r["source_name"] or "MangaPlus",
                    "source_url": r["source_url"] or "#",
                    "read_platform": meta.get("official_platform") or r["source_name"] or "MangaPlus / Shonen Jump",
                    "read_url": meta.get("official_url") or r["source_url"] or "#",
                    "score": int(r["score"]) if r.get("score") else 85,
                }
            )

        # 3. News items (Rolling 48-Hour Window + editorial guides)
        draft_news_rows = conn.execute(
            text(
                """
                SELECT id, status, title_en, title_hi, summary_en, summary_hi,
                       image_url, news_category, source_name, source_url,
                       published_at, created_at, is_featured
                FROM anime_draft
                WHERE content_type = 'news'
                  AND (status = 'published' OR COALESCE(published_at, created_at) >= NOW() - INTERVAL '48 hours')
                ORDER BY (COALESCE(published_at, created_at) >= NOW() - INTERVAL '48 hours') DESC,
                         COALESCE(published_at, created_at) DESC
                LIMIT 28
                """
            )
        ).mappings().all()

        article_rows = conn.execute(
            text(
                """
                SELECT id, slug, title, meta_description, content_md,
                       published_at, category, anime_title_hi, anime_title_en,
                       is_featured, reading_time_minutes, cover_image
                FROM article
                ORDER BY is_featured DESC, published_at DESC NULLS LAST, id ASC
                LIMIT 30
                """
            )
        ).mappings().all()

        seen_news_titles: set[str] = set()
        for r in draft_news_rows:
            t_key = re.sub(r"[^a-z0-9]+", "", (r["title_en"] or r["title_hi"] or "").lower())
            if t_key in seen_news_titles:
                continue
            if t_key:
                seen_news_titles.add(t_key)
            raw_img = (r.get("image_url") or "").strip()
            img = raw_img if raw_img.startswith("https://") or raw_img.startswith("http://") else ""
            pub_dt = r.get("published_at") or r.get("created_at")
            date_str = pub_dt.strftime("%d %b %Y") if isinstance(pub_dt, datetime) else "Recent"
            rel_age, rel_age_en, is_fresh, hours_left, is_brand_new, is_expiring_soon = _ist_news_timing(pub_dt)
            n_slug = f"draft-{r['id']}"
            ext_url = r.get("source_url") or ""
            raw_shi = re.sub(r"\s*पूरी जानकारी,\s*कास्ट विवरण[^।]*।?", "", (r.get("summary_hi") or "")).strip()
            raw_sen = re.sub(r"[\u0900-\u097F]+", "", (r.get("summary_en") or "")).strip()
            raw_sen = re.sub(
                r"Stay on top of anime news and updates straight out of Japan with Crunchyroll News\.?",
                "",
                raw_sen,
                flags=re.IGNORECASE,
            )
            raw_sen = re.sub(
                r"From breaking stories to evergreen content,\s*get all your anime and manga news updates in one place\.?",
                "",
                raw_sen,
                flags=re.IGNORECASE,
            )
            raw_sen = re.sub(r"\s*You heard it here first!?", "", raw_sen, flags=re.IGNORECASE)
            raw_sen = re.sub(r"\s+", " ", raw_sen).strip()

            hi_paras = [p.strip() for p in re.split(r"\n+", raw_shi) if p.strip()]
            if not hi_paras:
                hi_paras = [r["title_hi"] or r["title_en"] or ""]
            elif len(hi_paras) == 1 and len(hi_paras[0]) > 260:
                sents = [s.strip() for s in re.split(r"(?<=[।.!?])\s+", hi_paras[0]) if s.strip()]
                if len(sents) >= 3:
                    mid = max(2, len(sents) // 2)
                    hi_paras = [" ".join(sents[:mid]), " ".join(sents[mid:])]

            en_paras = [p.strip() for p in re.split(r"\n+", raw_sen) if p.strip()]
            if not en_paras:
                en_paras = [
                    f"{r['title_en'] or ''} — reported via {r.get('source_name') or 'Official Wire'}."
                ]
            else:
                if len(en_paras) == 1 and len(en_paras[0]) > 260:
                    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", en_paras[0]) if s.strip()]
                    if len(sents) >= 3:
                        mid = max(2, len(sents) // 2)
                        en_paras = [" ".join(sents[:mid]), " ".join(sents[mid:])]
                elif len(" ".join(en_paras)) < 140:
                    en_paras.append(
                        f"Source dispatch ({r.get('source_name') or 'Anime Newswire'}, {date_str}): track release schedule, staff announcements, and licensed India streaming platforms across our Explore and IST Schedule hubs."
                    )

            news_items.append(
                {
                    "id": n_slug,
                    "slug": n_slug,
                    "title_hi": r["title_hi"] or r["title_en"] or "",
                    "title_en": r["title_en"] or r["title_hi"] or "",
                    "summary": _clean_text(hi_paras[0], 160),
                    "summary_en": _clean_text(en_paras[0], 160),
                    "body_paragraphs": hi_paras,
                    "body_paragraphs_hi": hi_paras,
                    "body_paragraphs_en": en_paras,
                    "image_url": img,
                    "category": (r.get("news_category") or "Anime News").upper(),
                    "source_name": r.get("source_name") or "Official Wire",
                    "source_url": f"/news/{n_slug}",
                    "external_url": ext_url,
                    "date": date_str,
                    "relative_age": rel_age,
                    "relative_age_en": rel_age_en,
                    "hours_left": hours_left,
                    "is_brand_new": is_brand_new,
                    "is_expiring_soon": is_expiring_soon,
                    "is_fresh": is_fresh,
                    "is_featured": bool(r.get("is_featured")),
                    "kind": "NEWS",
                }
            )

        for r in article_rows:
            raw_img = (r.get("cover_image") or "").strip()
            img = raw_img if raw_img.startswith("https://") or raw_img.startswith("http://") else ""
            pub_dt = r.get("published_at")
            date_str = pub_dt.strftime("%d %b %Y") if isinstance(pub_dt, datetime) else "Guide"
            rel_age, rel_age_en, is_fresh, hours_left, is_brand_new, is_expiring_soon = _ist_news_timing(pub_dt)
            all_paras = [re.sub(r"^[#>*`\-\s]+", "", p).strip() for p in (r.get("content_md") or "").split("\n\n") if p.strip()]
            hi_art_paras = [p for p in all_paras if re.search(r"[\u0900-\u097F]", p)][:6] or all_paras[:6]
            en_art_paras = [re.sub(r"[\u0900-\u097F]+", "", p).strip(" ()•-") for p in all_paras if not re.search(r"[\u0900-\u097F]", p) or len(re.sub(r"[\u0900-\u097F\W]+", "", p)) > 40][:6]
            en_art_paras = [p for p in en_art_paras if len(p) > 25]
            if not en_art_paras:
                en_art_paras = [f"{r.get('anime_title_en') or r['title']} — Full editorial guide available in English & Hindi."]
            en_title_guess = (r.get("slug") or "").replace("-", " ").title()
            news_items.append(
                {
                    "id": f"art-{r['id']}",
                    "slug": r["slug"],
                    "title_hi": r["title"] or "",
                    "title_en": r.get("anime_title_en") or en_title_guess or r["title"] or "",
                    "summary": _clean_text(r["meta_description"] or r["content_md"], 160),
                    "summary_en": _clean_text(re.sub(r"[\u0900-\u097F]+", "", r["meta_description"] or "") or en_title_guess or r["title"], 160),
                    "body_paragraphs": hi_art_paras,
                    "body_paragraphs_hi": hi_art_paras,
                    "body_paragraphs_en": en_art_paras,
                    "image_url": img,
                    "category": (r.get("category") or "Hindi Guide").upper(),
                    "source_name": f"{r.get('reading_time_minutes') or 5} MIN READ",
                    "source_url": f"/guides/{r['slug']}",
                    "external_url": "",
                    "date": date_str,
                    "relative_age": rel_age,
                    "relative_age_en": rel_age_en,
                    "hours_left": hours_left,
                    "is_brand_new": is_brand_new,
                    "is_expiring_soon": is_expiring_soon,
                    "is_fresh": is_fresh,
                    "is_featured": bool(r.get("is_featured")),
                    "kind": "EDITORIAL",
                }
            )

        # 4. Trailer items (valid YouTube ID + deduplicated)
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

        seen_yt: set[str] = set()
        for r in tr_rows:
            yt_id = _extract_youtube_id(r.get("source_url"))
            if not yt_id or yt_id in seen_yt:
                continue
            seen_yt.add(yt_id)
            thumb = _resolve_safe_image(
                r.get("image_url") or f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg"
            )
            t_en = (r["title_en"] or r["title_hi"] or "").strip()
            t_hi = (r["title_hi"] or t_en).strip()
            trailer_items.append(
                {
                    "id": r["id"],
                    "title_en": f"{t_en} — Official Trailer" if "trailer" not in t_en.lower() else t_en,
                    "title_base_en": t_en,
                    "title_hi": t_hi,
                    "summary": _clean_text(r["summary_hi"] or r["summary_en"], 130),
                    "thumb_url": thumb,
                    "youtube_id": yt_id,
                    "embed_url": f"https://www.youtube.com/embed/{yt_id}?autoplay=1&rel=0&modestbranding=1&playsinline=1",
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


def _build_streaming_matrix(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Build India official streaming platform matrix with Hindi Dub / Sub status pills."""
    item_plats = {str(p.get("name", "")).lower(): p for p in (item.get("platforms") or [])}
    dub_plat = str(item.get("dub_platform") or "").lower()
    has_dub = bool(item.get("hindi_dub")) or bool(item_plats)

    matrix_defs = [
        ("Crunchyroll", "https://www.crunchyroll.com", "PAID", "HINDI DUB + SUB" if (has_dub or "crunchyroll" in dub_plat or "crunchyroll" in item_plats) else "JP AUDIO + EN SUB"),
        ("Netflix", "https://www.netflix.com", "PAID", "HINDI DUB + SUB" if ("netflix" in item_plats or "netflix" in dub_plat) else "SELECT SEASONS"),
        ("SonyLIV", "https://www.sonyliv.com", "PAID", "HINDI / TAMIL / TELUGU" if has_dub else "REGIONAL CATALOG"),
        ("Prime Video", "https://www.primevideo.com", "PAID", "HINDI + SUB" if "prime" in item_plats else "GLOBAL SIMULCAST"),
        ("YouTube / Muse India", "https://www.youtube.com/@MuseIndia", "FREE", "FREE • EN / HI SUB"),
    ]
    out: list[dict[str, Any]] = []
    for name, default_url, tier, status in matrix_defs:
        key = name.lower()
        active = key in item_plats or key in dub_plat or (name == "Crunchyroll" and has_dub) or (tier == "FREE")
        url = item_plats[key]["url"] if key in item_plats and item_plats[key].get("url") else default_url
        out.append({"name": name, "url": url, "tier": tier, "audio_status": status, "active": active})
    return out


def _match_trailer_id(title_en: str, trailers: list[dict[str, Any]]) -> str:
    key = (title_en or "").lower().split(":")[0].strip()
    if not key:
        return ""
    for tr in trailers:
        if key in (tr.get("title_en") or "").lower() and tr.get("youtube_id"):
            return tr["youtube_id"]
    return ""


def get_anime_detail(slug: str) -> dict[str, Any] | None:
    """Resolve an anime spotlight or schedule item by slug with streaming matrix and related items."""
    data = get_cached_content()
    target = (slug or "").strip().lower()
    found: dict[str, Any] | None = None
    for sp in data["spotlight"]:
        if sp["slug"].lower() == target:
            found = dict(sp)
            break
    if not found:
        for sch in data["schedule"]:
            if sch.get("slug", "").lower() == target:
                found = dict(sch)
                break
    if not found:
        return None

    if not found.get("trailer_id"):
        found["trailer_id"] = _match_trailer_id(found.get("title_en", ""), data["trailers"])
    found["streaming_matrix"] = _build_streaming_matrix(found)

    related: list[dict[str, Any]] = []
    for sp in data["spotlight"]:
        if sp["slug"].lower() != target:
            related.append(
                {
                    "title_hi": sp["title_hi"],
                    "title_en": sp["title_en"],
                    "cover": sp["cover_url"],
                    "url": f"/anime/{sp['slug']}",
                    "tag": "HINDI DUB",
                }
            )
    for sch in data["schedule"][:8]:
        if len(related) >= 4:
            break
        if sch.get("slug", "").lower() != target:
            related.append(
                {
                    "title_hi": sch["title_hi"],
                    "title_en": sch["title_en"],
                    "cover": sch["image_url"],
                    "url": f"/anime/{sch['slug']}",
                    "tag": sch.get("airing_time", "IST"),
                }
            )
    found["related"] = related[:4]
    return found


def get_news_detail(slug: str) -> dict[str, Any] | None:
    """Resolve a news item or editorial article by slug with related items."""
    data = get_cached_content()
    target = (slug or "").strip().lower()
    found: dict[str, Any] | None = None
    for n in data["news"]:
        if str(n.get("slug", "")).lower() == target or str(n.get("id", "")).lower() == target:
            found = dict(n)
            break
    if not found:
        return None
    related = [
        {
            "title_hi": x["title_hi"],
            "title_en": x["title_en"],
            "cover": x["image_url"],
            "url": x["source_url"],
            "tag": x["category"],
        }
        for x in data["news"]
        if str(x.get("slug", "")).lower() != target and x.get("image_url")
    ][:6]
    if len(related) < 4:
        seen_urls = {r["url"] for r in related}
        for x in data["news"]:
            if str(x.get("slug", "")).lower() != target and x["source_url"] not in seen_urls:
                related.append(
                    {
                        "title_hi": x["title_hi"],
                        "title_en": x["title_en"],
                        "cover": x["image_url"],
                        "url": x["source_url"],
                        "tag": x["category"],
                    }
                )
            if len(related) >= 6:
                break
    found["related"] = related
    return found


def _render_markdown_html(md_text: str) -> str:
    """Convert structured Markdown into clean semantic HTML paragraphs, headings, and lists."""
    lines = (md_text or "").splitlines()
    html_parts: list[str] = []
    in_list = False

    def _inline(txt: str) -> str:
        escaped = (
            txt.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return re.sub(r"\*\*(.+?)\*\*", r'<strong class="text-white font-bold">\1</strong>', escaped)

    for raw in lines:
        line = raw.strip()
        if not line:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            continue
        if line.startswith("### "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f'<h3 class="font-heading text-lg sm:text-xl font-extrabold text-[#ff2e63] pt-3">{_inline(line[4:])}</h3>')
        elif line.startswith("## "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f'<h2 class="font-display-shonen text-xl sm:text-2xl text-white border-l-4 border-[#ff2e63] pl-3.5 pt-4">{_inline(line[3:])}</h2>')
        elif line.startswith("- ") or line.startswith("* "):
            if not in_list:
                html_parts.append('<ul class="list-disc pl-5 space-y-2 text-white/85">')
                in_list = True
            html_parts.append(f"<li>{_inline(line[2:])}</li>")
        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f'<p class="text-white/85 leading-relaxed">{_inline(line)}</p>')

    if in_list:
        html_parts.append("</ul>")
    return "\n".join(html_parts)


GUIDE_CATEGORY_LABELS = {
    "watch-order": ("वॉच ऑर्डर", "WATCH ORDER"),
    "rankings": ("टॉप रैंकिंग", "RANKINGS"),
    "recommendations": ("सिफारिशें", "PICKS"),
    "romance": ("रोमांस", "ROMANCE"),
    "sports": ("स्पोर्ट्स", "SPORTS"),
    "hindi-dub": ("हिंदी डब", "HINDI DUB"),
    "platforms": ("ओटीटी प्लेटफॉर्म", "OTT PLATFORMS"),
    "free-legal": ("फ्री व वैध", "FREE LEGAL"),
    "dossiers": ("डोज़ियर", "DOSSIER"),
    "manga-buying": ("मंगा गाइड", "MANGA GUIDE"),
    "beginners": ("बिगिनर्स", "BEGINNERS"),
    "seinen": ("सीनेन क्लासिक्स", "SEINEN"),
    "movies": ("एनीमे मूवीज", "MOVIES"),
    "industry": ("उद्योग विश्लेषण", "INDUSTRY"),
    "isekai": ("इसेकाई गाइड", "ISEKAI"),
}


def _strip_mixed_english_from_hindi_md(md_text: str) -> str:
    """Keep Hindi guide pure Hindi cleanly by filtering standalone all-ASCII English summary paragraphs and parenthesized English heading suffixes."""
    out_blocks: list[str] = []
    for raw_block in (md_text or "").split("\n\n"):
        blk = raw_block.strip()
        if not blk:
            continue
        if blk.startswith("#"):
            # Clean "(Introduction & Overview)" bilingual English tags off Hindi headings
            blk = re.sub(r"\s*\([A-Za-z0-9\s&/,:-]+\)\s*$", "", blk).strip()
            out_blocks.append(blk)
            continue
        if not re.search(r"[\u0900-\u097F]", blk):
            # Standalone English paragraph inserted inside original seed -> hide in Hindi view so HI stays pure Hindi
            continue
        out_blocks.append(blk)
    return "\n\n".join(out_blocks)


def get_guides_list(category: str = "") -> dict[str, Any]:
    """Load all published editorial guides from Postgres `article` table."""
    active_cat = (category or "").strip().lower()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, slug, title, COALESCE(title_en, anime_title_en, title) AS title_en_resolved,
                       meta_description, COALESCE(meta_description_en, '') AS meta_description_en,
                       content_md, COALESCE(content_en_md, '') AS content_en_md,
                       published_at, category, anime_title_hi, anime_title_en,
                       is_featured, reading_time_minutes, cover_image
                FROM article
                ORDER BY is_featured DESC, id ASC
                """
            )
        ).mappings().all()

    guides: list[dict[str, Any]] = []
    cat_counts: dict[str, int] = {}
    for r in rows:
        cat_key = (r.get("category") or "watch-order").lower()
        cat_counts[cat_key] = cat_counts.get(cat_key, 0) + 1
        cat_hi, cat_en = GUIDE_CATEGORY_LABELS.get(cat_key, (cat_key.upper(), cat_key.upper()))
        md_txt = r.get("content_md") or ""
        en_md_txt = r.get("content_en_md") or ""
        wcount = len(md_txt.split())
        wcount_en = len(en_md_txt.split()) if en_md_txt else wcount
        pub_dt = r.get("published_at")
        pub_lbl = pub_dt.strftime("%d %b %Y") if hasattr(pub_dt, "strftime") else "November 2026"
        cov = (r.get("cover_image") or "").strip()
        if cov and not cov.startswith("http"):
            cov = ""
        title_hi = r["title"]
        title_en = (r.get("title_en_resolved") or r.get("anime_title_en") or r["title"]).strip()
        meta_hi = r.get("meta_description") or ""
        meta_en = (r.get("meta_description_en") or meta_hi).strip()
        item = {
            "id": r["id"],
            "slug": r["slug"],
            "title": title_hi,
            "title_hi": title_hi,
            "title_en": title_en,
            "meta_description": meta_hi,
            "meta_description_hi": meta_hi,
            "meta_description_en": meta_en,
            "category": cat_key,
            "category_label_hi": cat_hi,
            "category_label_en": cat_en,
            "anime_title_hi": r.get("anime_title_hi") or title_hi,
            "anime_title_en": r.get("anime_title_en") or title_en,
            "is_featured": bool(r.get("is_featured")),
            "reading_time_minutes": r.get("reading_time_minutes") or max(5, round(wcount / 165)),
            "word_count": wcount,
            "word_count_en": wcount_en,
            "published_label": pub_lbl,
            "cover_image": cov,
        }
        if not active_cat or cat_key == active_cat:
            guides.append(item)

    cats_menu = [
        (ck, GUIDE_CATEGORY_LABELS.get(ck, (ck.upper(), ck.upper()))[0], f"{GUIDE_CATEGORY_LABELS.get(ck, (ck.upper(), ck.upper()))[1]} ({cnt})")
        for ck, cnt in cat_counts.items()
    ]
    return {
        "guides": guides,
        "total_guides": len(rows),
        "categories": cats_menu,
        "active_cat": active_cat,
    }


def get_guide_detail(slug: str) -> dict[str, Any] | None:
    """Resolve a single editorial guide by slug with pure Hindi and pure English semantic HTML."""
    target = (slug or "").strip().lower()
    if not target:
        return None
    bundle = get_guides_list()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, slug, title, COALESCE(title_en, anime_title_en, title) AS title_en_resolved,
                       meta_description, COALESCE(meta_description_en, '') AS meta_description_en,
                       content_md, COALESCE(content_en_md, '') AS content_en_md,
                       published_at, category, anime_title_hi, anime_title_en,
                       is_featured, reading_time_minutes, cover_image
                FROM article
                WHERE LOWER(slug) = :slug
                LIMIT 1
                """
            ),
            {"slug": target},
        ).mappings().first()

    if not row:
        return None
    cat_key = (row.get("category") or "watch-order").lower()
    cat_hi, cat_en = GUIDE_CATEGORY_LABELS.get(cat_key, (cat_key.upper(), cat_key.upper()))
    md_hi_raw = row.get("content_md") or ""
    md_hi_pure = _strip_mixed_english_from_hindi_md(md_hi_raw) or md_hi_raw
    md_en = (row.get("content_en_md") or "").strip()
    wcount = len(md_hi_raw.split())
    wcount_en = len(md_en.split()) if md_en else wcount
    pub_dt = row.get("published_at")
    pub_lbl = pub_dt.strftime("%d %b %Y") if hasattr(pub_dt, "strftime") else "November 2026"
    cov = (row.get("cover_image") or "").strip()
    if cov and not cov.startswith("http"):
        cov = ""

    title_hi = row["title"]
    title_en = (row.get("title_en_resolved") or row.get("anime_title_en") or title_hi).strip()
    meta_hi = row.get("meta_description") or ""
    meta_en = (row.get("meta_description_en") or meta_hi).strip()

    guide = {
        "id": row["id"],
        "slug": row["slug"],
        "title": title_hi,
        "title_hi": title_hi,
        "title_en": title_en,
        "meta_description": meta_hi,
        "meta_description_hi": meta_hi,
        "meta_description_en": meta_en,
        "content_md": md_hi_raw,
        "content_html": _render_markdown_html(md_hi_pure),
        "content_html_hi": _render_markdown_html(md_hi_pure),
        "content_html_en": _render_markdown_html(md_en) if md_en else _render_markdown_html(md_hi_raw),
        "category": cat_key,
        "category_label_hi": cat_hi,
        "category_label_en": cat_en,
        "anime_title_hi": row.get("anime_title_hi") or title_hi,
        "anime_title_en": row.get("anime_title_en") or title_en,
        "reading_time_minutes": row.get("reading_time_minutes") or max(5, round(wcount / 165)),
        "word_count": wcount,
        "word_count_en": wcount_en,
        "published_label": pub_lbl,
        "cover_image": cov,
    }
    related = [g for g in bundle["guides"] if g["slug"].lower() != target][:3]
    return {
        "guide": guide,
        "related_guides": related,
        "total_guides": bundle["total_guides"],
    }


def get_sitemap_urls() -> tuple[list[str], list[str], list[str]]:
    """Return all active anime, news, and editorial guide slugs for dynamic XML sitemap."""
    data = get_cached_content()
    anime_slugs = list(dict.fromkeys([sp["slug"] for sp in data["spotlight"]] + [s["slug"] for s in data["schedule"] if s.get("slug")]))
    news_slugs = list(dict.fromkeys([n["slug"] for n in data["news"] if n.get("slug")]))
    guide_slugs = list(dict.fromkeys([g["slug"] for g in get_guides_list()["guides"] if g.get("slug")]))
    return anime_slugs, news_slugs, guide_slugs


_poll_lock = threading.Lock()
_polls_ready = False
DEFAULT_POLL_KEY = "daily_clash_solo_vs_ds"


def ensure_community_polls() -> None:
    """Create community_polls table if needed and seed baseline daily clash row."""
    global _polls_ready
    if _polls_ready:
        return
    with _poll_lock:
        if _polls_ready:
            return
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS community_polls (
                        poll_key VARCHAR(100) PRIMARY KEY,
                        option_a_votes INTEGER NOT NULL DEFAULT 0,
                        option_b_votes INTEGER NOT NULL DEFAULT 0,
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO community_polls (poll_key, option_a_votes, option_b_votes, updated_at)
                    VALUES (:k, 148, 132, NOW())
                    ON CONFLICT (poll_key) DO NOTHING
                    """
                ),
                {"k": DEFAULT_POLL_KEY},
            )
        _polls_ready = True


def _format_poll_row(poll_key: str, a_votes: int, b_votes: int) -> dict[str, Any]:
    total = max(1, a_votes + b_votes)
    pct_a = round((a_votes * 100) / total)
    pct_b = 100 - pct_a
    return {
        "poll_key": poll_key,
        "question": "भारत में किसका एक्शन और हिंदी डब सबसे खतरनाक है?",
        "question_en": "Which anime has the most explosive action & Hindi Dub in India?",
        "option_a": {
            "label": "सोलो लेवलिंग (Solo Leveling)",
            "label_en": "Solo Leveling Season 2",
            "short": "SOLO LEVELING",
            "votes": a_votes,
            "pct": pct_a,
        },
        "option_b": {
            "label": "डीमन स्लेयर: इन्फिनिटी कैसल",
            "label_en": "Demon Slayer: Infinity Castle",
            "short": "DEMON SLAYER",
            "votes": b_votes,
            "pct": pct_b,
        },
        "option_a_votes": a_votes,
        "option_b_votes": b_votes,
        "total_votes": a_votes + b_votes,
        "pct_a": pct_a,
        "pct_b": pct_b,
    }


def get_poll_state(poll_key: str = DEFAULT_POLL_KEY) -> dict[str, Any]:
    """Read live fan vote counts from Supabase community_polls."""
    ensure_community_polls()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT option_a_votes, option_b_votes FROM community_polls WHERE poll_key = :k"),
            {"k": poll_key},
        ).fetchone()
    if not row:
        return _format_poll_row(poll_key, 148, 132)
    return _format_poll_row(poll_key, int(row[0]), int(row[1]))


def cast_poll_vote(poll_key: str, choice: str) -> dict[str, Any]:
    """Increment vote count for option 'a' or 'b' in Supabase and return live split."""
    ensure_community_polls()
    col = "option_a_votes" if choice == "a" else "option_b_votes"
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO community_polls (poll_key, option_a_votes, option_b_votes, updated_at)
                VALUES (:k, 148, 132, NOW())
                ON CONFLICT (poll_key) DO NOTHING
                """
            ),
            {"k": poll_key},
        )
        row = conn.execute(
            text(
                f"UPDATE community_polls SET {col} = {col} + 1, updated_at = NOW() "
                "WHERE poll_key = :k RETURNING option_a_votes, option_b_votes"
            ),
            {"k": poll_key},
        ).fetchone()
    a_votes, b_votes = (int(row[0]), int(row[1])) if row else (149, 132)
    res = _format_poll_row(poll_key, a_votes, b_votes)
    res["choice"] = choice
    return res


def get_roulette_pool() -> list[dict[str, Any]]:
    """Build curated mystery anime & manga pool for variable-reward Roulette modal."""
    data = get_cached_content()
    pool: list[dict[str, Any]] = []
    for sp in data["spotlight"]:
        pool.append(
            {
                "title_hi": sp["title_hi"],
                "title_en": sp["title_en"],
                "cover": sp["cover_url"],
                "badge": sp["badge"],
                "platform": sp["platforms"][0]["name"] if sp.get("platforms") else "Crunchyroll",
                "url": f"/anime/{sp['slug']}",
                "score": sp.get("score") or 98,
                "kind": "HINDI DUB ANIME",
            }
        )
    for sch in data["schedule"][:8]:
        pool.append(
            {
                "title_hi": sch["title_hi"],
                "title_en": sch["title_en"],
                "cover": sch["image_url"],
                "badge": "HINDI DUB" if sch.get("hindi_dub") else f"{sch['airing_day']} • {sch['airing_time']}",
                "platform": sch.get("dub_platform") or "Crunchyroll / AniList",
                "url": f"/anime/{sch['slug']}",
                "score": sch.get("score") or 94,
                "kind": "WEEKLY ANIME",
            }
        )
    for m in data["manga"][:6]:
        pool.append(
            {
                "title_hi": m["title_hi"],
                "title_en": f"{m['author']} • {m['chapters_label']}",
                "cover": m["cover_url"],
                "badge": m["status_badge_hi"],
                "platform": m["read_platform"],
                "url": f"/manga?q={m['title_en']}",
                "score": m.get("score") or 96,
                "kind": "MANGA DOSSIER",
            }
        )
    return pool


_contact_lock = threading.Lock()
_contact_ready = False


def save_contact_message(name: str, email: str, subject: str, message: str) -> int:
    """Create contact_messages table if needed and persist contact/sponsorship inquiry."""
    global _contact_ready
    with _contact_lock:
        if not _contact_ready:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS contact_messages (
                            id SERIAL PRIMARY KEY,
                            name VARCHAR(120) NOT NULL,
                            email VARCHAR(200) NOT NULL,
                            subject VARCHAR(80) NOT NULL,
                            message TEXT NOT NULL,
                            created_at TIMESTAMP NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                )
            _contact_ready = True

    with engine.begin() as conn:
        row_id = conn.execute(
            text(
                """
                INSERT INTO contact_messages (name, email, subject, message, created_at)
                VALUES (:name, :email, :subject, :message, NOW())
                RETURNING id
                """
            ),
            {"name": name[:120], "email": email[:200], "subject": subject[:80], "message": message[:4000]},
        ).scalar()
    return int(row_id or 1)


CURATED_AWARDS_FALLBACK: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Crunchyroll Anime Awards (10th Edition)",
        "name_hi": "क्रंचीरोल एनीमे अवार्ड्स (10वां संस्करण)",
        "edition_year": 2026,
        "location": "Tokyo, Japan",
        "source_url": "https://www.crunchyroll.com/animeawards",
        "status": "verified",
        "entries": [
            {
                "category": "Anime of the Year",
                "category_hi": "वर्ष का सर्वश्रेष्ठ एनीमे",
                "winner_title": "My Hero Academia FINAL SEASON",
                "winner_title_hi": "माय हीरो एकेडेमिया: फाइनल सीज़न",
                "winner_poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx182896-mvxTVHGdDB4q.jpg",
                "detail_url": "/explore?q=My%20Hero%20Academia",
                "nominees": [],
            },
            {
                "category": "Best Animation",
                "category_hi": "सर्वश्रेष्ठ एनिमेशन",
                "winner_title": "Solo Leveling Season 2 -Arise from the Shadow-",
                "winner_title_hi": "सोलो लेवलिंग: अराइज़ फ्रॉम द शैडो",
                "winner_poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx176496-9BDMjAZGEbq4.png",
                "detail_url": "/anime/solo-leveling",
                "nominees": [],
            },
            {
                "category": "Best Film",
                "category_hi": "सर्वश्रेष्ठ एनीमे फिल्म",
                "winner_title": "Demon Slayer: Kimetsu no Yaiba Infinity Castle",
                "winner_title_hi": "डीमन स्लेयर: इन्फिनिटी कैसल",
                "winner_poster": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx178788-zm3gtpB9TpRt.jpg",
                "detail_url": "/anime/demon-slayer-infinity-castle",
                "nominees": [],
            },
        ],
    }
]


def get_awards_data(year: int | None = None) -> dict[str, Any]:
    """Load anime award ceremonies and winner/nominee entries from Supabase with curated fallback."""
    try:
        content = get_cached_content()
        known_slugs = {sp["slug"]: sp["slug"] for sp in content["spotlight"]}
        for sch in content["schedule"]:
            known_slugs[sch["slug"]] = sch["slug"]

        def _resolve_award_url(title_en: str | None) -> str:
            low = (title_en or "").lower()
            if "solo leveling" in low:
                return "/anime/solo-leveling"
            if "infinity castle" in low or "demon slayer" in low:
                return "/anime/demon-slayer-infinity-castle"
            if "jujutsu kaisen" in low:
                return "/anime/jujutsu-kaisen"
            s = _slugify(title_en)
            if s in known_slugs:
                return f"/anime/{s}"
            return f"/explore?q={(title_en or '').strip()}"

        with engine.connect() as conn:
            c_rows = conn.execute(
                text(
                    """
                    SELECT id, name, name_hi, edition_year, date, location, source_url, status
                    FROM award_ceremony
                    ORDER BY edition_year DESC, id ASC
                    """
                )
            ).mappings().all()
            e_rows = conn.execute(
                text(
                    """
                    SELECT id, ceremony_id, category, category_hi, winner_title, winner_title_hi,
                           winner_poster, nominee_media_ids, source_url, status
                    FROM award_entry
                    ORDER BY ceremony_id ASC, id ASC
                    """
                )
            ).mappings().all()

        entries_by_ceremony: dict[int, list[dict[str, Any]]] = {}
        for r in e_rows:
            raw_nom = r.get("nominee_media_ids")
            nominees: list[dict[str, Any]] = []
            if isinstance(raw_nom, str) and raw_nom.strip().startswith("["):
                try:
                    parsed = json.loads(raw_nom)
                    if isinstance(parsed, list):
                        nominees = [n for n in parsed if isinstance(n, dict)]
                except Exception:
                    nominees = []
            elif isinstance(raw_nom, list):
                nominees = raw_nom
            cid = int(r["ceremony_id"])
            entries_by_ceremony.setdefault(cid, []).append(
                {
                    "id": r["id"],
                    "category": r.get("category") or "Award Winner",
                    "category_hi": r.get("category_hi") or r.get("category") or "विजेता",
                    "winner_title": r.get("winner_title") or "",
                    "winner_title_hi": r.get("winner_title_hi") or r.get("winner_title") or "",
                    "winner_poster": _resolve_safe_image(r.get("winner_poster")),
                    "detail_url": _resolve_award_url(r.get("winner_title")),
                    "nominees": nominees[:6],
                    "status": r.get("status") or "verified",
                }
            )

        all_ceremonies: list[dict[str, Any]] = []
        for c in c_rows:
            cid = int(c["id"])
            c_entries = entries_by_ceremony.get(cid, [])
            if not c_entries:
                continue
            ed_yr = int(c.get("edition_year") or 2026)
            all_ceremonies.append(
                {
                    "id": cid,
                    "name": c.get("name") or "Anime Awards",
                    "name_hi": c.get("name_hi") or c.get("name") or "एनीमे अवार्ड्स",
                    "edition_year": ed_yr,
                    "date_label": c["date"].strftime("%d %b %Y") if hasattr(c.get("date"), "strftime") else str(ed_yr),
                    "location": c.get("location") or "Tokyo, Japan",
                    "source_url": c.get("source_url") or "",
                    "status": "verified",
                    "aoty_entry": next(
                        (e for e in c_entries if "anime of the year" in (e["category"] or "").lower()),
                        next((e for e in c_entries if "animation of the year" in (e["category"] or "").lower()), c_entries[0]),
                    ),
                    "other_entries": [],
                    "entries": c_entries,
                }
            )
            aoty_id = all_ceremonies[-1]["aoty_entry"]["id"]
            all_ceremonies[-1]["other_entries"] = [e for e in c_entries if e["id"] != aoty_id]
        if all_ceremonies:
            years = sorted({int(c["edition_year"]) for c in all_ceremonies}, reverse=True)
            filtered_ceremonies = [c for c in all_ceremonies if (not year or int(c["edition_year"]) == int(year))]
            if not filtered_ceremonies:
                filtered_ceremonies = all_ceremonies
            aoty_hero = None
            for c in all_ceremonies:
                for ent in c["entries"]:
                    if "anime of the year" in (ent["category"] or "").lower():
                        aoty_hero = {
                            **ent,
                            "ceremony_name": c["name"],
                            "ceremony_name_hi": c["name_hi"],
                            "edition_year": c["edition_year"],
                        }
                        break
                if aoty_hero:
                    break
            showcase_winners: list[dict[str, Any]] = []
            seen_w: set[str] = set()
            for c in all_ceremonies:
                for ent in c["entries"]:
                    wkey = _slugify(ent["winner_title"])
                    if wkey in seen_w:
                        continue
                    seen_w.add(wkey)
                    showcase_winners.append(
                        {
                            **ent,
                            "ceremony_name": c["name"],
                            "ceremony_name_hi": c["name_hi"],
                            "edition_year": c["edition_year"],
                        }
                    )
                    if len(showcase_winners) >= 6:
                        break
                if len(showcase_winners) >= 6:
                    break
            return {
                "ceremonies": filtered_ceremonies,
                "years": years,
                "active_year": year if year else "all",
                "total_ceremonies": len(all_ceremonies),
                "total_entries": sum(len(c["entries"]) for c in all_ceremonies),
                "aoty_hero": aoty_hero or (showcase_winners[0] if showcase_winners else None),
                "showcase_winners": showcase_winners,
            }
    except Exception:
        pass
    return {
        "ceremonies": CURATED_AWARDS_FALLBACK,
        "years": [2026],
        "active_year": year if year else "all",
        "total_ceremonies": len(CURATED_AWARDS_FALLBACK),
        "total_entries": sum(len(c["entries"]) for c in CURATED_AWARDS_FALLBACK),
        "aoty_hero": CURATED_AWARDS_FALLBACK[0]["entries"][0],
        "showcase_winners": CURATED_AWARDS_FALLBACK[0]["entries"],
    }


def explore_catalog(query: str = "", kind: str = "all", hindi_only: bool = False) -> dict[str, Any]:
    """Aggregate normalized items across Anime, Manga, Schedule, News, and Trailers for /explore."""
    data = get_cached_content()
    q = (query or "").strip().lower()
    active_type = (kind or "all").strip().lower()
    if active_type not in ("all", "anime", "guides", "awards", "manga", "schedule", "news", "trailers"):
        active_type = "all"

    items: list[dict[str, Any]] = []
    seen_anime_slugs: set[str] = set()

    for sp in data["spotlight"]:
        seen_anime_slugs.add(sp["slug"])
        items.append(
            {
                "kind": "anime",
                "slug": sp["slug"],
                "title_hi": sp["title_hi"],
                "title_en": sp["title_en"],
                "summary_hi": sp["tagline"],
                "summary_en": sp.get("tagline_en") or sp["tagline"],
                "cover_url": sp["cover_url"],
                "url": f"/anime/{sp['slug']}",
                "badge": sp["badge"],
                "meta_left": f"★ {sp['score']}%",
                "meta_right": sp["year"],
                "hindi_dub": True,
            }
        )

    for sch in data["schedule"]:
        if sch["slug"] not in seen_anime_slugs:
            seen_anime_slugs.add(sch["slug"])
            items.append(
                {
                    "kind": "anime",
                    "slug": sch["slug"],
                    "title_hi": sch["title_hi"],
                    "title_en": sch["title_en"],
                    "summary_hi": sch["summary"],
                    "summary_en": sch.get("summary_en") or sch["summary"],
                    "cover_url": sch["image_url"],
                    "url": sch["detail_url"],
                    "badge": "HINDI DUB" if sch.get("hindi_dub") else "SIMULCAST",
                    "meta_left": sch["airing_day_en"].upper(),
                    "meta_right": sch["airing_time"],
                    "hindi_dub": bool(sch.get("hindi_dub")),
                }
            )
        items.append(
            {
                "kind": "schedule",
                "slug": f"sch-{sch['slug']}",
                "title_hi": sch["title_hi"],
                "title_en": sch["title_en"],
                "summary_hi": sch["summary"],
                "summary_en": sch.get("summary_en") or sch["summary"],
                "cover_url": sch["image_url"],
                "url": sch["detail_url"],
                "badge": f"{sch['airing_time']}",
                "meta_left": sch["airing_day"],
                "meta_left_en": sch["airing_day_en"],
                "meta_right": sch["episode_label"],
                "hindi_dub": bool(sch.get("hindi_dub")),
            }
        )

    for m in data["manga"]:
        items.append(
            {
                "kind": "manga",
                "slug": f"manga-{m['id']}",
                "title_hi": m["title_hi"],
                "title_en": m["title_en"],
                "summary_hi": m["summary_hi"] or m["summary_en"],
                "summary_en": m["summary_en"] or m["summary_hi"],
                "cover_url": m["cover_url"],
                "url": f"/manga?q={m['title_en']}",
                "badge": m["volume_label"],
                "meta_left": m["author"],
                "meta_right": m["chapters_label"],
                "hindi_dub": False,
            }
        )

    for n in data["news"]:
        items.append(
            {
                "kind": "news",
                "slug": n["slug"],
                "title_hi": n["title_hi"],
                "title_en": n["title_en"],
                "summary_hi": n["summary"],
                "summary_en": n.get("summary_en") or n["summary"],
                "cover_url": n["image_url"],
                "url": n["source_url"],
                "badge": n["category"],
                "meta_left": n["source_name"],
                "meta_right": n["date"],
                "hindi_dub": "hindi" in f"{n['title_hi']} {n['title_en']}".lower(),
            }
        )

    for tr in data["trailers"]:
        items.append(
            {
                "kind": "trailers",
                "slug": f"tr-{tr['id']}",
                "title_hi": tr["title_hi"],
                "title_en": tr["title_en"],
                "summary_hi": tr["summary"] or tr["source_name"],
                "summary_en": tr["summary"] or tr["source_name"],
                "cover_url": tr["thumb_url"],
                "url": "/trailers",
                "badge": "TRAILER PV",
                "meta_left": tr["source_name"],
                "meta_right": tr["season"] or "HD PV",
                "hindi_dub": "hindi" in f"{tr['title_hi']} {tr['title_en']}".lower(),
            }
        )

    for g in get_guides_list()["guides"]:
        items.append(
            {
                "kind": "guides",
                "slug": g["slug"],
                "title_hi": g["title"],
                "title_en": g["anime_title_en"] or g["title"],
                "summary_hi": g["meta_description"],
                "summary_en": g["meta_description"],
                "cover_url": g["cover_image"],
                "url": f"/guides/{g['slug']}",
                "badge": g["category_label_en"],
                "meta_left": f"{g['reading_time_minutes']} MIN READ",
                "meta_right": f"{g['word_count']} WORDS",
                "hindi_dub": True,
            }
        )

    for aw in get_awards_data()["showcase_winners"]:
        items.append(
            {
                "kind": "awards",
                "slug": f"award-{_slugify(aw['winner_title'])}",
                "title_hi": aw["winner_title_hi"],
                "title_en": aw["winner_title"],
                "summary_hi": f"{aw['ceremony_name_hi']} — {aw['category_hi']}",
                "summary_en": f"{aw['ceremony_name']} ({aw['edition_year']}) — {aw['category']}",
                "cover_url": aw["winner_poster"],
                "url": "/awards",
                "badge": f"🏆 WINNER {aw['edition_year']}",
                "meta_left": aw["category"],
                "meta_right": str(aw["edition_year"]),
                "hindi_dub": True,
            }
        )

    counts_by_type = {
        "all": len(items),
        "anime": sum(1 for x in items if x["kind"] == "anime"),
        "guides": sum(1 for x in items if x["kind"] == "guides"),
        "awards": sum(1 for x in items if x["kind"] == "awards"),
        "schedule": sum(1 for x in items if x["kind"] == "schedule"),
        "manga": sum(1 for x in items if x["kind"] == "manga"),
        "news": sum(1 for x in items if x["kind"] == "news"),
        "trailers": sum(1 for x in items if x["kind"] == "trailers"),
    }

    filtered: list[dict[str, Any]] = []
    for x in items:
        if active_type != "all" and x["kind"] != active_type:
            continue
        if hindi_only and not x.get("hindi_dub"):
            continue
        if q:
            hay = f"{x['title_hi']} {x['title_en']} {x['summary_hi']} {x['summary_en']} {x['badge']} {x['meta_left']}".lower()
            if q not in hay:
                continue
        filtered.append(x)

    return {
        "items": filtered,
        "total_matched": len(filtered),
        "counts_by_type": counts_by_type,
        "active_type": active_type,
        "hindi_only": hindi_only,
    }
