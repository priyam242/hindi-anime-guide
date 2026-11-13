"""Anime Awards factual normalization and helper module.

Handles structured facts for annual anime awards (Crunchyroll Anime Awards,
Tokyo Anime Award Festival, Seiyu Awards, Japan Academy Film Prize).

Since awards are infrequent annual events with no dedicated public REST API,
this module standardizes manual admin submissions and factual announcement entries.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from automation.fetchers.anilist import search_anilist_cover

logger = logging.getLogger("automation.fetchers.awards")


def create_award_fact_entry(
    award_name: str,
    category_name: str,
    winner_or_nominee: str,
    award_date: str,
    source_name: str,
    source_url: str,
    raw_details: str = "",
    resolve_covers: bool = True,
) -> dict[str, Any]:
    """Create a normalized factual award dictionary for the drafting pipeline.

    Args:
        award_name: Name of the award ceremony (e.g. 'Crunchyroll Anime Awards 2026').
        category_name: Award category (e.g. 'Anime of the Year', 'Best Animation').
        winner_or_nominee: Winning anime title or nominee name.
        award_date: Date or year of the award announcement.
        source_name: Official source entity (e.g. 'Crunchyroll Official').
        source_url: Link to official announcement.
        raw_details: Any additional verified facts.
        resolve_covers: Whether to look up an AniList cover for the winner title.

    Returns:
        Standard normalized item dict matching automation pipeline schema.
    """
    clean_winner = winner_or_nominee.strip()
    clean_cat = category_name.strip()
    clean_award = award_name.strip()

    title_en = f"{clean_winner} Wins {clean_cat} at {clean_award}"

    # Generate unique slug-based external_id
    slug_base = f"{clean_award}_{clean_cat}_{clean_winner}"
    clean_slug = re.sub(r'[^a-zA-Z0-9_]', '_', slug_base).lower()[:60]
    external_id = f"award_{clean_slug}"

    cover_image_url: str | None = None
    if resolve_covers and clean_winner:
        cover_image_url = search_anilist_cover(clean_winner)

    raw_summary = (
        f"{clean_winner} was officially declared winner in the category '{clean_cat}' "
        f"at {clean_award}. Official announcement verified on {award_date}. {raw_details}".strip()
    )

    return {
        "external_id": external_id,
        "content_type": "award",
        "source_name": source_name.strip() or "Official Award Announcement",
        "source_url": source_url.strip(),
        "award_name": clean_award,
        "category_name": clean_cat,
        "winner_or_nominee": clean_winner,
        "award_date": award_date.strip(),
        "title_en": title_en,
        "title_romaji": clean_winner,
        "title_english": clean_winner,
        "raw_summary": raw_summary,
        "image_url": cover_image_url,
    }

