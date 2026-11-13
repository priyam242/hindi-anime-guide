"""AniList GraphQL fetcher for current season anime airing schedules.

Fetches factual airing metadata from AniList GraphQL API (no API key required).
Strictly adheres to rate limits and uses structured retries and timeouts.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("automation.fetchers.anilist")

ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"
USER_AGENT = "HindiAnimeGuide-Bot/1.0 (+https://hindi-anime-guide.onrender.com; contact: editorial@animeguide.com)"

ANILIST_AIRING_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      hasNextPage
      total
    }
    media(type: ANIME, status: RELEASING, sort: POPULARITY_DESC) {
      id
      title {
        romaji
        english
        native
      }
      coverImage {
        large
        extraLarge
      }
      nextAiringEpisode {
        airingAt
        timeUntilAiring
        episode
      }
      status
      averageScore
      siteUrl
      genres
      format
      episodes
      season
      seasonYear
    }
  }
}
"""


def fetch_current_season_schedule(
    limit: int = 10,
    page: int = 1,
    max_retries: int = 3,
    timeout_seconds: float = 15.0,
) -> list[dict[str, Any]]:
    """Query AniList GraphQL for current season releasing anime schedules.

    Args:
        limit: Maximum number of media items to return (1-50 per request).
        page: Page number for pagination.
        max_retries: Number of retries on network or rate limit (HTTP 429) errors.
        timeout_seconds: HTTP request timeout in seconds.

    Returns:
        List of normalized anime schedule dictionaries.
    """
    per_page = min(max(1, limit), 50)
    variables = {"page": page, "perPage": per_page}
    payload = {"query": ANILIST_AIRING_QUERY, "variables": variables}
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=timeout_seconds, headers=headers) as client:
                response = client.post(ANILIST_GRAPHQL_URL, json=payload)

            if response.status_code == 429:
                retry_after_str = response.headers.get("Retry-After", "5")
                try:
                    retry_after = float(retry_after_str)
                except ValueError:
                    retry_after = 5.0
                logger.warning(
                    "AniList API rate limit encountered (attempt %d/%d). Sleeping for %.1f seconds.",
                    attempt,
                    max_retries,
                    retry_after,
                )
                time.sleep(retry_after)
                continue

            response.raise_for_status()
            data = response.json()
            media_list = data.get("data", {}).get("Page", {}).get("media", [])

            normalized_items: list[dict[str, Any]] = []
            for media in media_list:
                item = _normalize_media_item(media)
                if item:
                    normalized_items.append(item)
                if len(normalized_items) >= limit:
                    break

            logger.info("Successfully fetched %d airing items from AniList.", len(normalized_items))
            return normalized_items

        except httpx.HTTPStatusError as exc:
            last_error = exc
            logger.warning(
                "AniList HTTP status error %s on attempt %d/%d: %s",
                exc.response.status_code,
                attempt,
                max_retries,
                exc,
            )
            time.sleep(2.0 * attempt)
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            last_error = exc
            logger.warning(
                "AniList network/timeout error on attempt %d/%d: %s",
                attempt,
                max_retries,
                exc,
            )
            time.sleep(2.0 * attempt)

    logger.error("Failed to fetch from AniList after %d attempts. Last error: %s", max_retries, last_error)
    return []


def _normalize_media_item(media: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize raw AniList media GraphQL response into a factual dictionary."""
    media_id = media.get("id")
    if not media_id:
        return None

    titles = media.get("title") or {}
    title_romaji = (titles.get("romaji") or "").strip()
    title_english = (titles.get("english") or "").strip()
    primary_title = title_english or title_romaji or f"Anime #{media_id}"

    next_episode_info = media.get("nextAiringEpisode") or {}
    next_episode_num = next_episode_info.get("episode")
    airing_at_unix = next_episode_info.get("airingAt")

    airing_at_iso: str | None = None
    airing_at_formatted: str | None = None
    if airing_at_unix:
        dt = datetime.fromtimestamp(airing_at_unix, tz=timezone.utc)
        airing_at_iso = dt.isoformat()
        airing_at_formatted = dt.strftime("%Y-%m-%d %H:%M UTC")

    cover_images = media.get("coverImage") or {}
    image_url = cover_images.get("large") or cover_images.get("extraLarge")

    site_url = media.get("siteUrl") or f"https://anilist.co/anime/{media_id}"
    score = media.get("averageScore")
    genres = media.get("genres") or []
    status = media.get("status") or "RELEASING"
    media_format = media.get("format") or "TV"

    # external_id uniquely identifies this schedule event or anime entry
    if next_episode_num is not None:
        external_id = f"anilist_{media_id}_ep{next_episode_num}"
    else:
        external_id = f"anilist_{media_id}"

    return {
        "external_id": external_id,
        "content_type": "schedule",
        "source_name": "AniList",
        "source_url": site_url,
        "anime_id": media_id,
        "title_en": primary_title,
        "title_romaji": title_romaji,
        "title_english": title_english,
        "next_episode": next_episode_num,
        "airing_at_iso": airing_at_iso,
        "airing_at_formatted": airing_at_formatted,
        "airing_at_timestamp": airing_at_unix,
        "image_url": image_url,
        "average_score": score,
        "status": status,
        "genres": genres,
        "format": media_format,
    }


ANILIST_SEARCH_QUERY = """
query ($search: String) {
  Media(search: $search, type: ANIME) {
    id
    title {
      romaji
      english
    }
    coverImage {
      large
      extraLarge
    }
  }
}
"""


def search_anilist_cover(query_title: str, timeout_seconds: float = 8.0) -> str | None:
    """Search AniList for an anime cover image URL matching the given title.

    Safely falls back to None if no match is found or on network timeout.
    Never downloads or saves images; returns only AniList CDN URLs.
    """
    import re

    if not query_title:
        return None

    # Clean title to improve search hit rate
    clean = re.sub(r'["\']', '', query_title)
    clean = re.split(
        r'\b(?:reveals|teaser|trailer|announced|release|debut|episode|chapter|key visual|anime|broadcast|season)\b',
        clean,
        flags=re.IGNORECASE,
    )[0].strip()

    if len(clean) < 2:
        return None

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "query": ANILIST_SEARCH_QUERY,
        "variables": {"search": clean[:80]},
    }

    try:
        with httpx.Client(timeout=timeout_seconds, headers=headers) as client:
            resp = client.post(ANILIST_GRAPHQL_URL, json=payload)
        if resp.status_code == 200:
            media = resp.json().get("data", {}).get("Media")
            if media:
                cov = media.get("coverImage") or {}
                return cov.get("large") or cov.get("extraLarge")
    except Exception as exc:
        logger.debug("AniList cover search failed for '%s': %s", clean, exc)

    return None


