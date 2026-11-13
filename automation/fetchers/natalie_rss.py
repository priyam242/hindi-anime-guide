"""Comic Natalie (natalie.mu) RSS fetcher module.

LEGAL & ROBOTS COMPLIANCE AUDIT:
- Target: https://natalie.mu/comic
- Audit Date: November 2026
- Finding: natalie.mu blocks automated requests with an AWS WAF CAPTCHA challenge
  (HTTP 405 "Human Verification" / awsWafToken).
- Comic Natalie does not provide an open, unauthenticated public RSS feed.
- Site Terms and robots.txt forbid automated scraping.

DECISION PER USER RULES:
- As mandated by project source rules ("If not allowed or no RSS, DO NOT scrape; skip that source and tell me"),
  this source is strictly SKIPPED.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("automation.fetchers.natalie")


def fetch_natalie_news(
    limit: int = 10,
    max_retries: int = 1,
    timeout_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    """Fetch handler for Comic Natalie news.

    Returns an empty list because Comic Natalie is protected by an AWS WAF CAPTCHA
    and lacks an open public RSS feed. Direct scraping is disabled for legal compliance.
    """
    logger.warning(
        "[SKIPPED SOURCE] Comic Natalie (natalie.mu) is gated behind an AWS WAF CAPTCHA "
        "and has no open public RSS feed. Scraping is prohibited by site terms. Skipping."
    )
    return []

