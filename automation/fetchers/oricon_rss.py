"""Oricon News (oricon.co.jp) RSS fetcher module.

LEGAL & ROBOTS COMPLIANCE AUDIT:
- Target: https://www.oricon.co.jp
- Audit Date: November 2026
- Finding: Oricon has officially deprecated and discontinued public RSS feeds.
  Requests to https://www.oricon.co.jp/rss/ return HTTP 410 Gone.
  Endpoints /news/rss/ and /feed/ return HTTP 404 Not Found.
- Direct scraping of HTML articles is strictly prohibited by Oricon terms of service
  and restricted in robots.txt.

DECISION PER USER RULES:
- As mandated by project source rules ("If not allowed or no RSS, DO NOT scrape; skip that source and tell me"),
  this source is strictly SKIPPED.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("automation.fetchers.oricon")


def fetch_oricon_news(
    limit: int = 10,
    max_retries: int = 1,
    timeout_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    """Fetch handler for Oricon News updates.

    Returns an empty list because Oricon News has discontinued public RSS feeds (HTTP 410 Gone)
    and site terms forbid automated HTML scraping.
    """
    logger.warning(
        "[SKIPPED SOURCE] Oricon News public RSS feeds are retired (HTTP 410 Gone) "
        "and HTML scraping is prohibited. Skipping."
    )
    return []

