import argparse
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import text

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import engine  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
DAYS_HI = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]

ANILIST_URL = "https://graphql.anilist.co"
ANILIST_QUERY = """
query {
  schedule: Page(page: 1, perPage: 16) {
    media(type: ANIME, status: RELEASING, sort: POPULARITY_DESC) {
      id
      idMal
      title { english romaji }
      description(asHtml: false)
      coverImage { large }
      averageScore
      season
      seasonYear
      siteUrl
      nextAiringEpisode { airingAt }
    }
  }
  manga: Page(page: 1, perPage: 10) {
    media(type: MANGA, sort: TRENDING_DESC) {
      id
      title { english romaji }
      description(asHtml: false)
      coverImage { large }
      averageScore
      chapters
      volumes
      status
      siteUrl
    }
  }
}
"""

ANN_RSS_URL = "https://www.animenewsnetwork.com/all/rss.xml?ann-edition=in"

UPSERT_SQL = text(
    """
    INSERT INTO anime_draft (
        content_type, status, source_name, source_url, external_id,
        title_en, title_hi, summary_en, summary_hi, image_url,
        published_at, created_at, fetched_at, anilist_id, mal_id,
        season, season_year, chapters, volumes, publication_status,
        airing_day, airing_time, score, news_category,
        is_stale, is_featured, is_editor_pick, hindi_dub, country_of_origin
    ) VALUES (
        :content_type, :status, :source_name, :source_url, :external_id,
        :title_en, :title_hi, :summary_en, :summary_hi, :image_url,
        :published_at, :created_at, :fetched_at, :anilist_id, :mal_id,
        :season, :season_year, :chapters, :volumes, :publication_status,
        :airing_day, :airing_time, :score, :news_category,
        false, :is_featured, false, false, 'JP'
    )
    ON CONFLICT (external_id) DO UPDATE SET
        title_en = EXCLUDED.title_en,
        summary_en = COALESCE(EXCLUDED.summary_en, anime_draft.summary_en),
        image_url = COALESCE(EXCLUDED.image_url, anime_draft.image_url),
        airing_day = COALESCE(EXCLUDED.airing_day, anime_draft.airing_day),
        airing_time = COALESCE(EXCLUDED.airing_time, anime_draft.airing_time),
        score = COALESCE(EXCLUDED.score, anime_draft.score),
        chapters = COALESCE(EXCLUDED.chapters, anime_draft.chapters),
        volumes = COALESCE(EXCLUDED.volumes, anime_draft.volumes),
        fetched_at = EXCLUDED.fetched_at
    """
)


def _strip_html(val: str | None, limit: int = 450) -> str:
    txt = re.sub(r"<[^>]+>", " ", val or "")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limit]


def _ist_day_and_time(unix_ts: int | None) -> tuple[str, str]:
    if not unix_ts:
        return "रविवार", "TBD IST"
    dt = datetime.fromtimestamp(unix_ts, tz=IST)
    return DAYS_HI[dt.weekday()], dt.strftime("%I:%M %p IST").lstrip("0")


def fetch_anilist_records() -> list[dict]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    resp = httpx.post(ANILIST_URL, json={"query": ANILIST_QUERY}, timeout=15.0)
    resp.raise_for_status()
    data = resp.json().get("data", {})
    records: list[dict] = []

    for m in (data.get("schedule") or {}).get("media", []) or []:
        title_en = (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji") or ""
        if not title_en:
            continue
        airing_at = (m.get("nextAiringEpisode") or {}).get("airingAt")
        day_hi, time_ist = _ist_day_and_time(airing_at)
        records.append(
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
                "image_url": ((m.get("coverImage") or {}).get("large") or "")[:500] or None,
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
                "airing_day": day_hi[:20],
                "airing_time": time_ist[:50],
                "score": float(m["averageScore"]) if m.get("averageScore") else None,
                "news_category": "Announcement",
                "is_featured": bool(m.get("averageScore") and m["averageScore"] >= 82),
            }
        )

    for m in (data.get("manga") or {}).get("media", []) or []:
        title_en = (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji") or ""
        if not title_en:
            continue
        records.append(
            {
                "content_type": "manga",
                "status": "published",
                "source_name": "AniList Manga",
                "source_url": (m.get("siteUrl") or "https://anilist.co")[:500],
                "external_id": f"anilist_manga_{m['id']}",
                "title_en": title_en[:300],
                "title_hi": title_en[:300],
                "summary_en": _strip_html(m.get("description")),
                "summary_hi": None,
                "image_url": ((m.get("coverImage") or {}).get("large") or "")[:500] or None,
                "published_at": now,
                "created_at": now,
                "fetched_at": now,
                "anilist_id": m["id"],
                "mal_id": None,
                "season": None,
                "season_year": None,
                "chapters": m.get("chapters"),
                "volumes": m.get("volumes"),
                "publication_status": (m.get("status") or "RELEASING")[:50],
                "airing_day": None,
                "airing_time": None,
                "score": float(m["averageScore"]) if m.get("averageScore") else None,
                "news_category": "Manga",
                "is_featured": bool(m.get("averageScore") and m["averageScore"] >= 80),
            }
        )
    return records


def fetch_rss_news(limit: int = 8) -> list[dict]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    records: list[dict] = []
    try:
        resp = httpx.get(ANN_RSS_URL, timeout=12.0, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception:
        return records

    for item in root.findall("./channel/item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = _strip_html(item.findtext("description"))
        guid = (item.findtext("guid") or link or title).strip()
        if not title or not link:
            continue
        ext_hash = re.sub(r"[^a-zA-Z0-9_-]+", "_", guid)[-90:]
        records.append(
            {
                "content_type": "news",
                "status": "published",
                "source_name": "Anime News Network",
                "source_url": link[:500],
                "external_id": f"ann_rss_{ext_hash}"[:150],
                "title_en": title[:300],
                "title_hi": title[:300],
                "summary_en": desc,
                "summary_hi": None,
                "image_url": None,
                "published_at": now,
                "created_at": now,
                "fetched_at": now,
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
                "news_category": "Announcement",
                "is_featured": False,
            }
        )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync live anime/manga/news into Supabase anime_draft.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate records without writing DB.")
    args = parser.parse_args()

    anilist_records = fetch_anilist_records()
    news_records = fetch_rss_news()
    all_records = anilist_records + news_records

    counts: dict[str, int] = {}
    for rec in all_records:
        counts[rec["content_type"]] = counts.get(rec["content_type"], 0) + 1

    print(f"Fetched {len(all_records)} records -> breakdown: {counts}")

    if args.dry_run:
        print("DRY RUN complete (0 database writes).")
        return 0

    with engine.begin() as conn:
        for rec in all_records:
            conn.execute(UPSERT_SQL, rec)

    print(f"Supabase upsert complete: {len(all_records)} records synced cleanly into anime_draft.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
