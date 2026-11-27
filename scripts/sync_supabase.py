import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import engine  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
DAYS_HI = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]

ANILIST_URL = "https://graphql.anilist.co"
ANILIST_SYNC_QUERY = """
query {
  schedule: Page(page: 1, perPage: 50) {
    media(type: ANIME, status: RELEASING, sort: POPULARITY_DESC) {
      id
      idMal
      status
      title { english romaji native }
      description(asHtml: false)
      coverImage { extraLarge large }
      bannerImage
      averageScore
      season
      seasonYear
      siteUrl
      trailer { id site thumbnail }
      nextAiringEpisode { airingAt episode }
    }
  }
  manga: Page(page: 1, perPage: 40) {
    media(type: MANGA, sort: POPULARITY_DESC) {
      id
      idMal
      title { english romaji native }
      description(asHtml: false)
      status
      chapters
      volumes
      startDate { year month day }
      endDate { year month day }
      genres
      averageScore
      coverImage { extraLarge large }
      bannerImage
      siteUrl
      externalLinks { site url }
      staff(perPage: 6, sort: RELEVANCE) {
        edges {
          role
          node {
            name { full }
          }
        }
      }
    }
  }
}
"""

ANILIST_BATCH_ANIME_QUERY = """
query ($ids: [Int]) {
  Page(perPage: 50) {
    media(id_in: $ids, type: ANIME) {
      id
      idMal
      status
      title { english romaji native }
      coverImage { extraLarge large }
      bannerImage
      averageScore
      season
      seasonYear
      siteUrl
      nextAiringEpisode { airingAt episode }
    }
  }
}
"""

ANILIST_BATCH_MANGA_QUERY = """
query ($ids: [Int]) {
  Page(perPage: 50) {
    media(id_in: $ids, type: MANGA) {
      id
      idMal
      status
      chapters
      volumes
      title { english romaji native }
      coverImage { extraLarge large }
      bannerImage
      averageScore
      startDate { year }
      endDate { year }
      genres
      siteUrl
      externalLinks { site url }
      staff(perPage: 4, sort: RELEVANCE) {
        edges {
          role
          node { name { full } }
        }
      }
    }
  }
}
"""

ANILIST_EXACT_SEARCH_QUERY = """
query ($search: String) {
  Media(search: $search, type: ANIME, sort: POPULARITY_DESC) {
    id
    title { english romaji }
    coverImage { extraLarge large }
    bannerImage
  }
}
"""

RSS_FEEDS = [
    ("Anime News Network", "https://www.animenewsnetwork.com/all/rss.xml?ann-edition=in"),
    ("MyAnimeList News", "https://myanimelist.net/rss/news.xml"),
    ("Crunchyroll News", "https://cr-news-api-service.prd.crunchyrollsvc.com/v1/en-US/rss"),
]

# Known mismatched banner IDs previously stamped randomly onto unrelated news rows
SUSPECT_FALLBACK_IDS = ("176496", "178788", "113415", "171018", "21087")
NEWS_ANILIST_KEYWORDS = {
    "176496": "solo leveling",
    "178788": "demon slayer",
    "113415": "jujutsu kaisen",
    "171018": "dandadan",
    "21087": "one punch",
}

ARTICLE_COVER_SEARCH = {
    "dragon-ball-complete-watch-order-hindi-guide": "Dragon Ball Z",
    "5-mind-bending-anime-like-death-note-hindi": "Death Note",
    "your-name-makoto-shinkai-romance-anime-hindi": "Your Name",
    "haikyuu-blue-lock-sports-anime-hindi-guide": "Haikyu!!",
}

UPSERT_SQL = text(
    """
    INSERT INTO anime_draft (
        content_type, status, source_name, source_url, external_id,
        title_en, title_hi, summary_en, summary_hi, image_url,
        published_at, created_at, fetched_at, anilist_id, mal_id,
        season, season_year, chapters, volumes, publication_status,
        airing_day, airing_time, score, news_category, latest_chapter,
        official_links, premiere_date, start_date, end_date,
        is_stale, is_featured, is_editor_pick, hindi_dub, country_of_origin
    ) VALUES (
        :content_type, :status, :source_name, :source_url, :external_id,
        :title_en, :title_hi, :summary_en, :summary_hi, :image_url,
        :published_at, :created_at, :fetched_at, :anilist_id, :mal_id,
        :season, :season_year, :chapters, :volumes, :publication_status,
        :airing_day, :airing_time, :score, :news_category, :latest_chapter,
        :official_links, :premiere_date, :start_date, :end_date,
        false, :is_featured, false, false, 'JP'
    )
    ON CONFLICT (external_id) DO UPDATE SET
        status = EXCLUDED.status,
        source_name = EXCLUDED.source_name,
        source_url = EXCLUDED.source_url,
        title_en = EXCLUDED.title_en,
        summary_en = COALESCE(EXCLUDED.summary_en, anime_draft.summary_en),
        image_url = EXCLUDED.image_url,
        published_at = COALESCE(EXCLUDED.published_at, anime_draft.published_at),
        fetched_at = EXCLUDED.fetched_at,
        chapters = COALESCE(EXCLUDED.chapters, anime_draft.chapters),
        volumes = COALESCE(EXCLUDED.volumes, anime_draft.volumes),
        publication_status = COALESCE(EXCLUDED.publication_status, anime_draft.publication_status),
        airing_day = COALESCE(EXCLUDED.airing_day, anime_draft.airing_day),
        airing_time = COALESCE(EXCLUDED.airing_time, anime_draft.airing_time),
        score = COALESCE(EXCLUDED.score, anime_draft.score),
        latest_chapter = COALESCE(EXCLUDED.latest_chapter, anime_draft.latest_chapter),
        official_links = COALESCE(EXCLUDED.official_links, anime_draft.official_links),
        premiere_date = COALESCE(EXCLUDED.premiere_date, anime_draft.premiere_date),
        start_date = COALESCE(EXCLUDED.start_date, anime_draft.start_date),
        end_date = COALESCE(EXCLUDED.end_date, anime_draft.end_date),
        is_featured = EXCLUDED.is_featured
    """
)


def _norm_title(val: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (val or "").lower())


def _has_devanagari(val: str | None) -> bool:
    return bool(val and re.search(r"[\u0900-\u097F]", val))


def _strip_html(val: str | None, limit: int = 500) -> str:
    txt = re.sub(r"<[^>]+>", " ", val or "")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limit]


def _ist_day_and_time(unix_ts: int | None) -> tuple[str, str]:
    if not unix_ts:
        return "रविवार", "TBD IST"
    dt = datetime.fromtimestamp(unix_ts, tz=IST)
    return DAYS_HI[dt.weekday()], dt.strftime("%I:%M %p IST").lstrip("0")


def _safe_date(date_obj: dict | None) -> datetime | None:
    if not date_obj or not date_obj.get("year"):
        return None
    y = int(date_obj["year"])
    m = int(date_obj.get("month") or 1)
    d = int(date_obj.get("day") or 1)
    try:
        return datetime(y, m, d)
    except ValueError:
        return datetime(y, 1, 1)


def _extract_mangaka(staff_obj: dict | None) -> str:
    edges = (staff_obj or {}).get("edges") or []
    names: list[str] = []
    for edge in edges:
        role = (edge.get("role") or "").lower()
        name = ((edge.get("node") or {}).get("name") or {}).get("full")
        if not name:
            continue
        if any(k in role for k in ("story", "art", "original", "creator", "author")):
            if name not in names:
                names.append(name)
    if not names:
        for edge in edges[:2]:
            name = ((edge.get("node") or {}).get("name") or {}).get("full")
            if name and name not in names:
                names.append(name)
    return " / ".join(names[:2]) if names else "Original Manga Studio"


def _pick_official_manga_reader(m: dict) -> tuple[str, str]:
    preferred = ("manga plus", "mangaplus", "viz", "shonen jump", "kodansha", "crunchyroll")
    links = m.get("externalLinks") or []
    for pref in preferred:
        for link in links:
            site = (link.get("site") or "").strip()
            url = (link.get("url") or "").strip()
            if site and url and pref in site.lower():
                return site, url
    title_q = re.sub(r"\s+", "+", (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji") or "manga")
    return "MangaPlus / Shonen Jump", m.get("siteUrl") or f"https://mangaplus.shueisha.co.jp/search_result?keyword={title_q}"


def _manga_chapter_label(pub_status: str, ch_count: int | None, vol_count: int | None) -> str:
    st = (pub_status or "RELEASING").upper()
    if ch_count:
        return f"{ch_count} Chapters"
    if st == "HIATUS":
        return "On Hiatus"
    if st in ("FINISHED", "COMPLETED"):
        return f"Completed • {vol_count} Vols" if vol_count else "Completed"
    return "Ongoing Serialization"


def _extract_yt_id(url: str | None) -> str:
    if not url:
        return ""
    m = re.search(r"(?:youtu\.be/|v=|embed/|shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""


def _strict_anilist_news_image(title: str) -> str | None:
    """Match news franchise against AniList only when similarity >= 0.88 on core title."""
    quoted = re.search(r"['\"]([^'\"]{3,60})['\"]", title)
    if quoted:
        candidate = quoted.group(1).strip()
    else:
        candidate = re.split(r"\s+(?:TV\s+Anime|Anime|Manga|Film|Movie|Reveals|Casts|Gets|Announces|Releases)\b", title, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    candidate = re.sub(r"\s+", " ", candidate)
    if len(candidate) < 4 or len(candidate.split()) > 8:
        return None
    try:
        resp = httpx.post(ANILIST_URL, json={"query": ANILIST_EXACT_SEARCH_QUERY, "variables": {"search": candidate}}, timeout=6.0)
        if resp.status_code != 200:
            return None
        media = (resp.json().get("data") or {}).get("Media")
        if not media:
            return None
        t_obj = media.get("title") or {}
        for variant in (t_obj.get("english"), t_obj.get("romaji")):
            if not variant:
                continue
            sim = SequenceMatcher(None, candidate.lower(), variant.lower()).ratio()
            if sim >= 0.88 or candidate.lower() == variant.lower():
                cov = (media.get("coverImage") or {}).get("extraLarge") or (media.get("coverImage") or {}).get("large") or media.get("bannerImage")
                if cov and cov.startswith("http"):
                    return cov[:500]
    except Exception:
        pass
    return None


def _fetch_article_og_image(source_url: str | None) -> str | None:
    if not source_url or not source_url.startswith("http"):
        return None
    yt = _extract_yt_id(source_url)
    if yt:
        return f"https://img.youtube.com/vi/{yt}/hqdefault.jpg"
    try:
        resp = httpx.get(
            source_url,
            timeout=4.5,
            follow_redirects=True,
            headers={"User-Agent": "HindiAnimeGuideBot/2.0"},
        )
        if resp.status_code != 200:
            return None
        html = resp.text[:45000]
        m = re.search(
            r'<meta[^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'])[^>]+content=["\'](https?://[^"\']+)["\']',
            html,
            re.IGNORECASE,
        ) or re.search(
            r'<meta[^>]+content=["\'](https?://[^"\']+)["\'][^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'])',
            html,
            re.IGNORECASE,
        )
        if m:
            img = m.group(1).strip()
            if not any(bad in img.lower() for bad in ("logo", "favicon", "avatar", "1x1", "placeholder")):
                return img[:500]
    except Exception:
        pass
    return None


def _extract_rss_image(item: ET.Element, desc_raw: str, link: str, title: str) -> str | None:
    for child in item:
        tag = child.tag.lower()
        if "thumbnail" in tag or "content" in tag or tag == "enclosure":
            url = (child.attrib.get("url") or "").strip()
            if url.startswith("http") and not url.lower().endswith(".mp4"):
                return url[:500]
    m = re.search(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', desc_raw or "", re.IGNORECASE)
    if m:
        return m.group(1)[:500]
    og = _fetch_article_og_image(link)
    if og:
        return og
    return _strict_anilist_news_image(title)


def fetch_anilist_records() -> tuple[list[dict], list[dict], list[dict]]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    resp = httpx.post(ANILIST_URL, json={"query": ANILIST_SYNC_QUERY}, timeout=20.0)
    resp.raise_for_status()
    data = resp.json().get("data", {})

    schedule_records: list[dict] = []
    manga_records: list[dict] = []
    trailer_records: list[dict] = []

    for m in (data.get("schedule") or {}).get("media", []) or []:
        title_obj = m.get("title") or {}
        title_en = title_obj.get("english") or title_obj.get("romaji") or ""
        next_ep = m.get("nextAiringEpisode") or {}
        airing_at = next_ep.get("airingAt")
        # Strictly require RELEASING status AND valid future/current airingAt timestamp
        if not title_en or (m.get("status") != "RELEASING") or not airing_at:
            continue
        episode_num = next_ep.get("episode")
        day_hi, time_ist = _ist_day_and_time(airing_at)
        cover = ((m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large") or "")[:500] or None
        meta_json = json.dumps(
            {
                "airing_at": airing_at,
                "episode": episode_num,
                "banner_url": m.get("bannerImage") or "",
                "native_title": title_obj.get("native") or "",
            },
            ensure_ascii=False,
        )
        schedule_records.append(
            {
                "content_type": "schedule",
                "status": "published",
                "source_name": "AniList Airing",
                "source_url": (m.get("siteUrl") or "https://anilist.co")[:500],
                "external_id": f"anilist_airing_{m['id']}",
                "title_en": title_en[:300],
                "title_hi": title_en[:300],
                "summary_en": _strip_html(m.get("description")),
                "summary_hi": None,
                "image_url": cover,
                "published_at": now,
                "created_at": now,
                "fetched_at": now,
                "anilist_id": m["id"],
                "mal_id": m.get("idMal"),
                "season": (m.get("season") or "")[:20] or None,
                "season_year": m.get("seasonYear"),
                "chapters": None,
                "volumes": None,
                "publication_status": "RELEASING",
                "airing_day": day_hi[:20],
                "airing_time": time_ist[:50],
                "score": float(m["averageScore"]) if m.get("averageScore") else None,
                "news_category": "Announcement",
                "latest_chapter": f"Ep. {episode_num}" if episode_num else None,
                "official_links": meta_json,
                "premiere_date": datetime.fromtimestamp(airing_at, tz=timezone.utc).replace(tzinfo=None),
                "start_date": None,
                "end_date": None,
                "is_featured": bool(m.get("averageScore") and m["averageScore"] >= 82),
            }
        )

        tr = m.get("trailer") or {}
        if (tr.get("site") or "").lower() == "youtube" and tr.get("id"):
            yt_id = tr["id"]
            trailer_records.append(
                {
                    "content_type": "trailer",
                    "status": "published",
                    "source_name": "AniList PV",
                    "source_url": f"https://www.youtube.com/watch?v={yt_id}",
                    "external_id": f"anilist_trailer_{m['id']}_{yt_id}"[:150],
                    "title_en": title_en[:300],
                    "title_hi": f"{title_en} — आधिकारिक ट्रेलर"[:300],
                    "summary_en": _strip_html(m.get("description"), 260),
                    "summary_hi": None,
                    "image_url": f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
                    "published_at": now,
                    "created_at": now,
                    "fetched_at": now,
                    "anilist_id": m["id"],
                    "mal_id": m.get("idMal"),
                    "season": (m.get("season") or "")[:20] or None,
                    "season_year": m.get("seasonYear"),
                    "chapters": None,
                    "volumes": None,
                    "publication_status": None,
                    "airing_day": None,
                    "airing_time": None,
                    "score": float(m["averageScore"]) if m.get("averageScore") else None,
                    "news_category": "Trailer",
                    "latest_chapter": None,
                    "official_links": None,
                    "premiere_date": None,
                    "start_date": None,
                    "end_date": None,
                    "is_featured": True,
                }
            )

    for idx, m in enumerate((data.get("manga") or {}).get("media", []) or []):
        title_obj = m.get("title") or {}
        title_en = title_obj.get("english") or title_obj.get("romaji") or ""
        if not title_en:
            continue
        author = _extract_mangaka(m.get("staff"))
        reader_plat, reader_url = _pick_official_manga_reader(m)
        start_dt = _safe_date(m.get("startDate"))
        end_dt = _safe_date(m.get("endDate"))
        start_year = (m.get("startDate") or {}).get("year")
        end_year = (m.get("endDate") or {}).get("year")
        ch_count = m.get("chapters")
        vol_count = m.get("volumes")
        pub_status = (m.get("status") or "RELEASING").upper()
        cover = ((m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large") or "")[:500] or None
        ch_lbl = _manga_chapter_label(pub_status, ch_count, vol_count)

        dossier_json = json.dumps(
            {
                "author": author,
                "native_title": title_obj.get("native") or title_obj.get("romaji") or "",
                "genres": (m.get("genres") or [])[:4],
                "start_year": start_year,
                "end_year": end_year,
                "official_platform": reader_plat,
                "official_url": reader_url,
                "banner_url": m.get("bannerImage") or cover or "",
            },
            ensure_ascii=False,
        )

        manga_records.append(
            {
                "content_type": "manga",
                "status": "published",
                "source_name": reader_plat[:100],
                "source_url": reader_url[:500],
                "external_id": f"anilist_manga_{m['id']}",
                "title_en": title_en[:300],
                "title_hi": title_en[:300],
                "summary_en": _strip_html(m.get("description")),
                "summary_hi": None,
                "image_url": cover,
                "published_at": now,
                "created_at": now,
                "fetched_at": now,
                "anilist_id": m["id"],
                "mal_id": m.get("idMal"),
                "season": None,
                "season_year": start_year,
                "chapters": int(ch_count) if ch_count else None,
                "volumes": int(vol_count) if vol_count else None,
                "publication_status": pub_status[:50],
                "airing_day": None,
                "airing_time": None,
                "score": float(m["averageScore"]) if m.get("averageScore") else None,
                "news_category": "Manga",
                "latest_chapter": ch_lbl[:50],
                "official_links": dossier_json,
                "premiere_date": None,
                "start_date": start_dt,
                "end_date": end_dt,
                "is_featured": idx < 12 or bool(m.get("averageScore") and m["averageScore"] >= 82),
            }
        )

    return schedule_records, manga_records, trailer_records


def fetch_48h_rss_news(max_per_feed: int = 8) -> list[dict]:
    now_utc = datetime.now(timezone.utc)
    cutoff_48h = now_utc - timedelta(hours=48)
    records: list[dict] = []

    for feed_name, feed_url in RSS_FEEDS:
        try:
            resp = httpx.get(feed_url, timeout=12.0, follow_redirects=True, headers={"User-Agent": "HindiAnimeGuideBot/2.0"})
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.text)
        except Exception:
            continue

        for item in root.findall("./channel/item")[: max_per_feed * 2]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc_raw = item.findtext("description") or ""
            guid = (item.findtext("guid") or link or title).strip()
            pub_raw = (item.findtext("pubDate") or "").strip()
            if not title or not link:
                continue

            pub_dt_utc = now_utc
            if pub_raw:
                try:
                    parsed = parsedate_to_datetime(pub_raw)
                    pub_dt_utc = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                    pub_dt_utc = pub_dt_utc.astimezone(timezone.utc)
                except Exception:
                    pub_dt_utc = now_utc

            if pub_dt_utc > now_utc + timedelta(hours=2):
                pub_dt_utc = now_utc

            if pub_dt_utc < cutoff_48h and len(records) >= 8:
                continue

            pub_naive = max(pub_dt_utc, cutoff_48h + timedelta(minutes=10)) if pub_dt_utc < cutoff_48h else pub_dt_utc
            pub_naive = pub_naive.replace(tzinfo=None)
            now_naive = now_utc.replace(tzinfo=None)

            ext_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", guid)[-95:]
            thumb = _extract_rss_image(item, desc_raw, link, title)

            records.append(
                {
                    "content_type": "news",
                    "status": "published",
                    "source_name": feed_name[:100],
                    "source_url": link[:500],
                    "external_id": f"rss48h_{ext_slug}"[:150],
                    "title_en": title[:300],
                    "title_hi": title[:300],
                    "summary_en": _strip_html(desc_raw),
                    "summary_hi": None,
                    "image_url": thumb,
                    "published_at": pub_naive,
                    "created_at": now_naive,
                    "fetched_at": now_naive,
                    "anilist_id": None,
                    "mal_id": None,
                    "season": None,
                    "season_year": None,
                    "chapters": None,
                    "volumes": None,
                    "publication_status": None,
                    "airing_day": None,
                    "airing_time": None,
                    "score": None,
                    "news_category": (item.findtext("category") or "Anime News")[:50],
                    "latest_chapter": None,
                    "official_links": json.dumps({"pub_iso": pub_dt_utc.isoformat(), "rss_source": feed_name}),
                    "premiere_date": None,
                    "start_date": None,
                    "end_date": None,
                    "is_featured": len(records) < 3,
                }
            )
            if len([r for r in records if r["source_name"] == feed_name]) >= max_per_feed:
                break

    return records


def _dedupe_rows(conn: Any, content_type: str) -> int:
    """Merge Hindi localization + AniList enriched metadata into one canonical row per show/item."""
    rows = conn.execute(
        text(
            """
            SELECT id, anilist_id, title_en, title_hi, summary_en, summary_hi, image_url,
                   official_links, airing_day, airing_time, hindi_dub, dub_platform,
                   score, volumes, chapters, latest_chapter, publication_status, external_id
            FROM anime_draft
            WHERE content_type = :ct
            ORDER BY id ASC
            """
        ),
        {"ct": content_type},
    ).mappings().all()

    groups: dict[str, list[dict]] = {}
    for r in rows:
        key = f"aid:{r['anilist_id']}" if r["anilist_id"] else f"t:{_norm_title(r['title_en'] or r['title_hi'])}"
        if key in ("t:",):
            key = f"id:{r['id']}"
        groups.setdefault(key, []).append(dict(r))

    deleted = 0
    for grp in groups.values():
        if len(grp) <= 1:
            continue
        # Score rows: prefer HTTPS image, official_links JSON, and sync external_id as survivor
        def _rank(row: dict) -> tuple[int, int, int]:
            has_http = 1 if (row["image_url"] or "").startswith("http") else 0
            has_json = 1 if (row["official_links"] or "").strip().startswith("{") else 0
            return (has_http, has_json, int(row["id"]))

        grp_sorted = sorted(grp, key=_rank, reverse=True)
        keeper = grp_sorted[0]
        dups = grp_sorted[1:]

        best_title_hi = keeper["title_hi"]
        best_summary_hi = keeper["summary_hi"]
        best_hindi_dub = bool(keeper["hindi_dub"])
        best_dub_plat = keeper["dub_platform"]
        best_img = keeper["image_url"] if (keeper["image_url"] or "").startswith("http") else None
        best_title_en = keeper["title_en"]

        for d in grp_sorted:
            if _has_devanagari(d["title_hi"]) and not _has_devanagari(best_title_hi):
                best_title_hi = d["title_hi"]
            if _has_devanagari(d["summary_hi"]) and not _has_devanagari(best_summary_hi):
                best_summary_hi = d["summary_hi"]
            if d["hindi_dub"]:
                best_hindi_dub = True
                if d["dub_platform"] and not best_dub_plat:
                    best_dub_plat = d["dub_platform"]
            if not best_img and (d["image_url"] or "").startswith("http"):
                best_img = d["image_url"]

        conn.execute(
            text(
                """
                UPDATE anime_draft
                SET title_hi = :thi,
                    title_en = :ten,
                    summary_hi = COALESCE(:shi, summary_hi),
                    hindi_dub = :hd,
                    dub_platform = COALESCE(:dp, dub_platform),
                    image_url = COALESCE(:img, image_url)
                WHERE id = :kid
                """
            ),
            {
                "thi": best_title_hi,
                "ten": best_title_en,
                "shi": best_summary_hi,
                "hd": best_hindi_dub,
                "dp": best_dub_plat,
                "img": best_img,
                "kid": keeper["id"],
            },
        )
        dup_ids = [d["id"] for d in dups]
        conn.execute(text("DELETE FROM anime_draft WHERE id = ANY(:ids)"), {"ids": dup_ids})
        deleted += len(dup_ids)
    return deleted


def _backfill_anilist_metadata_and_covers(conn: Any) -> tuple[int, int, int]:
    """Backfill exact AniList covers/statuses for schedule, manga, news & articles."""
    # 1. Backfill ALL schedule rows with real AniList covers, status (RELEASING vs FINISHED) and nextAiringEpisode
    sch_rows = conn.execute(
        text("SELECT id, anilist_id, title_en, image_url, official_links FROM anime_draft WHERE content_type = 'schedule' AND anilist_id IS NOT NULL")
    ).mappings().all()
    sch_ids = list({int(r["anilist_id"]) for r in sch_rows})
    sch_meta: dict[int, dict] = {}
    for i in range(0, len(sch_ids), 50):
        try:
            resp = httpx.post(ANILIST_URL, json={"query": ANILIST_BATCH_ANIME_QUERY, "variables": {"ids": sch_ids[i : i + 50]}}, timeout=15.0)
            if resp.status_code == 200:
                for m in (resp.json().get("data") or {}).get("Page", {}).get("media", []) or []:
                    sch_meta[int(m["id"])] = m
        except Exception:
            pass

    sch_updated = 0
    for r in sch_rows:
        m = sch_meta.get(int(r["anilist_id"]))
        if not m:
            continue
        real_status = (m.get("status") or "RELEASING").upper()
        next_ep = m.get("nextAiringEpisode") or {}
        airing_at = next_ep.get("airingAt")
        episode_num = next_ep.get("episode")
        cover = ((m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large") or "")[:500] or None
        is_airing = real_status == "RELEASING" and bool(airing_at)
        day_hi, time_ist = _ist_day_and_time(airing_at) if is_airing else ("रविवार", "TBD IST")
        meta_json = json.dumps(
            {
                "airing_at": airing_at,
                "episode": episode_num,
                "banner_url": m.get("bannerImage") or "",
                "native_title": (m.get("title") or {}).get("native") or "",
            },
            ensure_ascii=False,
        )
        conn.execute(
            text(
                """
                UPDATE anime_draft
                SET status = :st,
                    publication_status = :pst,
                    image_url = COALESCE(:img, image_url),
                    airing_day = CASE WHEN :airing THEN :dhi ELSE airing_day END,
                    airing_time = CASE WHEN :airing THEN :tist ELSE airing_time END,
                    latest_chapter = :ep,
                    official_links = :links
                WHERE id = :id
                """
            ),
            {
                "st": "published" if is_airing else "archived",
                "pst": real_status,
                "img": cover,
                "airing": is_airing,
                "dhi": day_hi,
                "tist": time_ist,
                "ep": f"Ep. {episode_num}" if (is_airing and episode_num) else None,
                "links": meta_json,
                "id": r["id"],
            },
        )
        sch_updated += 1

    # 2. Backfill ALL manga rows missing status/cover/volumes/chapters or with bogus 'Weekly Serialization'
    mg_rows = conn.execute(
        text(
            """
            SELECT id, anilist_id, title_en, image_url, volumes, chapters, latest_chapter, publication_status, official_links
            FROM anime_draft
            WHERE content_type = 'manga' AND anilist_id IS NOT NULL
              AND (image_url IS NULL OR image_url NOT LIKE 'http%'
                   OR latest_chapter = 'Weekly Serialization'
                   OR official_links IS NULL OR official_links NOT LIKE '{%')
            """
        )
    ).mappings().all()
    mg_ids = list({int(r["anilist_id"]) for r in mg_rows})
    mg_meta: dict[int, dict] = {}
    for i in range(0, len(mg_ids), 50):
        try:
            resp = httpx.post(ANILIST_URL, json={"query": ANILIST_BATCH_MANGA_QUERY, "variables": {"ids": mg_ids[i : i + 50]}}, timeout=15.0)
            if resp.status_code == 200:
                for m in (resp.json().get("data") or {}).get("Page", {}).get("media", []) or []:
                    mg_meta[int(m["id"])] = m
        except Exception:
            pass

    mg_updated = 0
    for r in mg_rows:
        m = mg_meta.get(int(r["anilist_id"]))
        if not m:
            continue
        pub_st = (m.get("status") or r["publication_status"] or "RELEASING").upper()
        ch_count = m.get("chapters") or r["chapters"]
        vol_count = m.get("volumes") or r["volumes"]
        cover = ((m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large") or "")[:500] or None
        ch_lbl = _manga_chapter_label(pub_st, ch_count, vol_count)
        title_obj = m.get("title") or {}
        reader_plat, reader_url = _pick_official_manga_reader(m)
        dossier_json = json.dumps(
            {
                "author": _extract_mangaka(m.get("staff")),
                "native_title": title_obj.get("native") or title_obj.get("romaji") or "",
                "genres": (m.get("genres") or [])[:4],
                "start_year": (m.get("startDate") or {}).get("year"),
                "end_year": (m.get("endDate") or {}).get("year"),
                "official_platform": reader_plat,
                "official_url": reader_url,
                "banner_url": m.get("bannerImage") or cover or "",
            },
            ensure_ascii=False,
        )
        conn.execute(
            text(
                """
                UPDATE anime_draft
                SET image_url = COALESCE(:img, image_url),
                    publication_status = :pst,
                    chapters = :ch,
                    volumes = :vol,
                    latest_chapter = :clbl,
                    official_links = :links
                WHERE id = :id
                """
            ),
            {
                "img": cover,
                "pst": pub_st,
                "ch": int(ch_count) if ch_count else None,
                "vol": int(vol_count) if vol_count else None,
                "clbl": ch_lbl[:50],
                "links": dossier_json if not (r["official_links"] or "").startswith("{") else r["official_links"],
                "id": r["id"],
            },
        )
        mg_updated += 1

    # Also sanitize any remaining hardcoded 'Weekly Serialization' strings cleanly
    conn.execute(
        text(
            """
            UPDATE anime_draft
            SET latest_chapter = CASE
                WHEN UPPER(COALESCE(publication_status, '')) = 'HIATUS' THEN 'On Hiatus'
                WHEN UPPER(COALESCE(publication_status, '')) IN ('FINISHED', 'COMPLETED') THEN 'Completed'
                ELSE 'Ongoing Serialization'
            END
            WHERE content_type = 'manga' AND latest_chapter = 'Weekly Serialization'
            """
        )
    )

    # 3. Clean poisoned news fallback images & non-http image URLs + clean broken trailer 163
    news_fixed = 0
    news_rows = conn.execute(
        text("SELECT id, title_en, image_url, source_url FROM anime_draft WHERE content_type = 'news'")
    ).mappings().all()
    for nr in news_rows:
        img = (nr["image_url"] or "").strip()
        title_l = (nr["title_en"] or "").lower()
        needs_fix = (img and not img.startswith("http"))
        for aid_token, kw in NEWS_ANILIST_KEYWORDS.items():
            if img and aid_token in img and kw not in title_l:
                needs_fix = True
                break
        if needs_fix:
            real_img = _fetch_article_og_image(nr["source_url"]) or _strict_anilist_news_image(nr["title_en"] or "")
            conn.execute(text("UPDATE anime_draft SET image_url = :img WHERE id = :id"), {"img": real_img, "id": nr["id"]})
            news_fixed += 1

    # Clean up trailer rows with missing source_url or non-http images
    conn.execute(
        text(
            "DELETE FROM anime_draft WHERE content_type = 'trailer' AND (source_url IS NULL OR source_url = '' OR image_url IS NULL OR image_url NOT LIKE 'http%')"
        )
    )

    # 4. Backfill article table missing cover images via exact AniList title match
    art_rows = conn.execute(text("SELECT id, slug, cover_image FROM article WHERE cover_image IS NULL OR cover_image NOT LIKE 'http%'")).mappings().all()
    for ar in art_rows:
        search_term = ARTICLE_COVER_SEARCH.get(ar["slug"])
        if search_term:
            cov = _strict_anilist_news_image(search_term)
            if cov:
                conn.execute(text("UPDATE article SET cover_image = :img WHERE id = :id"), {"img": cov, "id": ar["id"]})

    return sch_updated, mg_updated, news_fixed


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-world 48h news, manga dossier & IST countdown sync.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and preview without writing to Supabase.")
    args = parser.parse_args()

    schedule_records, manga_records, trailer_records = fetch_anilist_records()
    news_records = fetch_48h_rss_news()
    all_records = schedule_records + manga_records + trailer_records + news_records

    print(
        f"Fetched live payloads -> schedule={len(schedule_records)}, manga={len(manga_records)}, "
        f"trailers={len(trailer_records)}, news={len(news_records)}"
    )

    if args.dry_run:
        print("DRY RUN complete (0 DB mutations).")
        return 0

    with engine.begin() as conn:
        for rec in all_records:
            conn.execute(UPSERT_SQL, rec)

        dedup_counts = {ct: _dedupe_rows(conn, ct) for ct in ("schedule", "manga", "trailer", "news")}
        sch_upd, mg_upd, news_fix = _backfill_anilist_metadata_and_covers(conn)

        # 48-hour News Auto-Purge with 8-item safety floor
        fresh_news_cnt = conn.execute(
            text("SELECT COUNT(*) FROM anime_draft WHERE content_type = 'news' AND COALESCE(published_at, created_at) >= NOW() - INTERVAL '48 hours'")
        ).scalar() or 0

        if fresh_news_cnt >= 8:
            purged_news = conn.execute(
                text("DELETE FROM anime_draft WHERE content_type = 'news' AND COALESCE(published_at, created_at) < NOW() - INTERVAL '48 hours'")
            ).rowcount
        else:
            purged_news = conn.execute(
                text(
                    """
                    DELETE FROM anime_draft
                    WHERE content_type = 'news'
                      AND id NOT IN (
                        SELECT id FROM anime_draft
                        WHERE content_type = 'news'
                        ORDER BY COALESCE(published_at, created_at) DESC, id DESC
                        LIMIT 8
                      )
                    """
                )
            ).rowcount

        pruned_trailers = conn.execute(
            text(
                """
                DELETE FROM anime_draft
                WHERE content_type = 'trailer'
                  AND id NOT IN (
                    SELECT id FROM anime_draft
                    WHERE content_type = 'trailer'
                    ORDER BY COALESCE(published_at, created_at) DESC, id DESC
                    LIMIT 24
                  )
                """
            )
        ).rowcount

        rem_news = conn.execute(text("SELECT COUNT(*) FROM anime_draft WHERE content_type = 'news'")).scalar() or 0
        rem_trailers = conn.execute(text("SELECT COUNT(*) FROM anime_draft WHERE content_type = 'trailer'")).scalar() or 0
        rem_manga = conn.execute(text("SELECT COUNT(*) FROM anime_draft WHERE content_type = 'manga'")).scalar() or 0
        rem_sch = conn.execute(text("SELECT COUNT(*) FROM anime_draft WHERE content_type = 'schedule' AND status = 'published'")).scalar() or 0

    print(
        f"Supabase Sync Complete -> deduped={dedup_counts}, backfilled=(sch={sch_upd}, manga={mg_upd}, news={news_fix}), "
        f"purged_48h_news={purged_news}, pruned_trailers={pruned_trailers} | "
        f"DB Published Totals: news={rem_news}, manga={rem_manga}, schedule={rem_sch}, trailers={rem_trailers}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
