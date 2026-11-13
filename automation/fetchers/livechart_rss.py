"""LiveChart.me RSS fetcher for verified anime news and headline updates.

Fetches factual news headlines strictly from LiveChart.me's public RSS feed:
https://www.livechart.me/feeds/headlines

Does not scrape HTML; uses polite timeouts, retries, and User-Agent headers.
Uses AniList GraphQL lookup for cover images (never downloads or stores third-party images).
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from automation.fetchers.anilist import USER_AGENT, search_anilist_cover

logger = logging.getLogger("automation.fetchers.livechart")

LIVECHART_HEADLINES_RSS_URL = "https://www.livechart.me/feeds/headlines"


def fetch_livechart_news(
    limit: int = 10,
    max_retries: int = 3,
    timeout_seconds: float = 15.0,
    resolve_covers: bool = True,
) -> list[dict[str, Any]]:
    """Fetch factual anime news headlines from LiveChart.me RSS feed.

    Args:
        limit: Maximum number of items to return (1-50).
        max_retries: Number of retries on network or temporary errors.
        timeout_seconds: HTTP request timeout in seconds.
        resolve_covers: Whether to look up AniList covers for matching anime titles.

    Returns:
        List of normalized news update dictionaries.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml; q=0.9, */*; q=0.8",
    }

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=timeout_seconds, headers=headers, follow_redirects=True) as client:
                response = client.get(LIVECHART_HEADLINES_RSS_URL)

            if response.status_code == 429:
                wait_sec = 5.0 * attempt
                logger.warning(
                    "LiveChart rate limit (HTTP 429) on attempt %d/%d. Waiting %.1fs...",
                    attempt,
                    max_retries,
                    wait_sec,
                )
                time.sleep(wait_sec)
                continue

            response.raise_for_status()

            # Parse RSS XML
            root = ET.fromstring(response.content)
            channel = root.find("channel")
            if channel is None:
                logger.warning("LiveChart RSS response missing <channel> tag.")
                return []

            items = channel.findall("item")
            logger.info("Fetched %d raw RSS items from LiveChart.me.", len(items))

            normalized: list[dict[str, Any]] = []
            for item in items:
                entry = _normalize_rss_item(item, resolve_covers=resolve_covers)
                if entry:
                    normalized.append(entry)
                if len(normalized) >= limit:
                    break

            return normalized

        except (httpx.RequestError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            last_error = exc
            logger.warning("LiveChart fetch error on attempt %d/%d: %s", attempt, max_retries, exc)
            time.sleep(2.0 * attempt)
        except ET.ParseError as exc:
            logger.error("Failed to parse LiveChart RSS XML: %s", exc)
            return []

    logger.error("Failed to fetch from LiveChart.me after %d attempts. Last error: %s", max_retries, last_error)
    return []


def _normalize_rss_item(item: ET.Element, resolve_covers: bool = True) -> dict[str, Any] | None:
    """Normalize a single RSS <item> element into the standard factual dictionary."""
    title_raw = (item.findtext("title") or "").strip()
    link_raw = (item.findtext("link") or "").strip()
    guid_raw = (item.findtext("guid") or "").strip()
    pub_date_raw = (item.findtext("pubDate") or "").strip()
    desc_raw = (item.findtext("description") or "").strip()

    if not title_raw:
        return None

    # Derive unique external_id
    headline_id_match = re.search(r'/headlines/(\d+)', guid_raw or link_raw)
    if headline_id_match:
        external_id = f"livechart_headline_{headline_id_match.group(1)}"
    else:
        # Fallback to sanitized URL slug
        sanitized_slug = re.sub(r'[^a-zA-Z0-9_]', '_', (guid_raw or link_raw))[-40:]
        external_id = f"livechart_news_{sanitized_slug}"

    # Parse publish date to ISO
    pub_date_iso = None
    if pub_date_raw:
        try:
            dt = parsedate_to_datetime(pub_date_raw)
            pub_date_iso = dt.isoformat()
        except Exception:
            pub_date_iso = pub_date_raw

    # Strip HTML tags from description if present
    clean_desc = re.sub(r'<[^>]+>', ' ', desc_raw).strip()
    clean_desc = ' '.join(clean_desc.split())

    # Optional AniList cover lookup
    cover_image_url: str | None = None
    if resolve_covers:
        cover_image_url = search_anilist_cover(title_raw)

    return {
        "external_id": external_id,
        "content_type": "news",
        "source_name": "LiveChart.me",
        "source_url": link_raw or guid_raw or "https://www.livechart.me",
        "title_en": title_raw,
        "title_romaji": title_raw,
        "title_english": title_raw,
        "pub_date_iso": pub_date_iso,
        "raw_summary": clean_desc,
        "image_url": cover_image_url,
    }

