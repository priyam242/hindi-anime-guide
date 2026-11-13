"""Manga chapter release fetcher using MangaDex official REST API.

Fetches factual chapter release metadata for ongoing/popular manga from MangaDex API:
https://api.mangadex.org/chapter

Adheres to MangaDex rate limits (5 requests/sec), respects timeouts and retries.
Uses AniList GraphQL lookup for verified covers (never downloads third-party images).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from automation.fetchers.anilist import USER_AGENT, search_anilist_cover

logger = logging.getLogger("automation.fetchers.manga")

MANGADEX_API_BASE = "https://api.mangadex.org"


def fetch_latest_manga_chapters(
    limit: int = 10,
    max_retries: int = 3,
    timeout_seconds: float = 15.0,
    resolve_covers: bool = True,
) -> list[dict[str, Any]]:
    """Fetch latest translated manga chapter releases from MangaDex API.

    Args:
        limit: Maximum number of chapter items to return (1-50).
        max_retries: Maximum network retry attempts.
        timeout_seconds: Request timeout in seconds.
        resolve_covers: Whether to look up AniList covers for manga titles.

    Returns:
        List of normalized manga update dictionaries.
    """
    per_page = min(max(1, limit), 50)
    params = [
        ("limit", str(per_page)),
        ("translatedLanguage[]", "en"),
        ("order[readableAt]", "desc"),
        ("includes[]", "manga"),
    ]
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=timeout_seconds, headers=headers, follow_redirects=True) as client:
                response = client.get(f"{MANGADEX_API_BASE}/chapter", params=params)

            if response.status_code == 429:
                wait_sec = 5.0 * attempt
                logger.warning("MangaDex rate limit (429) hit. Backing off %.1fs...", wait_sec)
                time.sleep(wait_sec)
                continue

            response.raise_for_status()
            data = response.json()
            raw_chapters = data.get("data", [])
            logger.info("Fetched %d raw chapters from MangaDex API.", len(raw_chapters))

            normalized: list[dict[str, Any]] = []
            for item in raw_chapters:
                entry = _normalize_mangadex_chapter(item, resolve_covers=resolve_covers)
                if entry:
                    normalized.append(entry)
                if len(normalized) >= limit:
                    break

            return normalized

        except (httpx.RequestError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            last_error = exc
            logger.warning("MangaDex fetch error on attempt %d/%d: %s", attempt, max_retries, exc)
            time.sleep(2.0 * attempt)

    logger.error("Failed to fetch from MangaDex after %d attempts. Last error: %s", max_retries, last_error)
    return []


def _normalize_mangadex_chapter(
    chapter_data: dict[str, Any],
    resolve_covers: bool = True,
) -> dict[str, Any] | None:
    """Normalize a MangaDex chapter JSON item into standard factual format."""
    ch_id = chapter_data.get("id")
    if not ch_id:
        return None

    attrs = chapter_data.get("attributes") or {}
    ch_num = attrs.get("chapter")
    ch_title = (attrs.get("title") or "").strip()
    readable_at = attrs.get("readableAt")

    # Find associated manga relationship
    relationships = chapter_data.get("relationships") or []
    manga_rel = next((r for r in relationships if r.get("type") == "manga"), None)
    manga_id = manga_rel.get("id") if manga_rel else None

    # Manga title extraction
    manga_attrs = (manga_rel.get("attributes") or {}) if manga_rel else {}
    title_dict = manga_attrs.get("title") or {}
    manga_title = title_dict.get("en") or title_dict.get("ja-ro")
    if not manga_title and title_dict:
        manga_title = next(iter(title_dict.values()), None)
    if not manga_title:
        manga_title = f"Manga #{manga_id}" if manga_id else "Manga"

    # Composite titles
    if ch_num and ch_title:
        display_title = f"{manga_title} Chapter {ch_num}: {ch_title}"
    elif ch_num:
        display_title = f"{manga_title} Chapter {ch_num}"
    elif ch_title:
        display_title = f"{manga_title}: {ch_title}"
    else:
        display_title = f"{manga_title} Latest Chapter Update"

    external_id = f"mangadex_ch_{ch_id}"
    source_url = f"https://mangadex.org/chapter/{ch_id}"

    # AniList cover lookup
    cover_image_url: str | None = None
    if resolve_covers and manga_title:
        cover_image_url = search_anilist_cover(manga_title)

    raw_summary = (
        f"Chapter {ch_num} of {manga_title} was officially published on MangaDex "
        f"on {readable_at}."
    )

    return {
        "external_id": external_id,
        "content_type": "manga",
        "source_name": "MangaDex",
        "source_url": source_url,
        "manga_id": manga_id,
        "manga_title": manga_title,
        "chapter_number": ch_num,
        "chapter_title": ch_title,
        "title_en": display_title,
        "title_romaji": manga_title,
        "title_english": manga_title,
        "pub_date_iso": readable_at,
        "raw_summary": raw_summary,
        "image_url": cover_image_url,
    }

