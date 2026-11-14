"""One-off cleanup script to identify and mark stale anime drafts.

Rule: Mark is_stale=True for posts with missing published_at, or whose
anime season is older than 1 year while the post date is recent.
Stale posts are automatically filtered out from home, /news, /schedule, and related feeds.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from sqlmodel import Session, select

from app.db import get_session
from app.models import AnimeDraft

# Known anime series / events with historical release years
HISTORICAL_ANIME_MAP = [
    (r'jujutsu kaisen.*season 2', 2023, "Jujutsu Kaisen Season 2 (Aired Summer/Fall 2023)"),
    (r'demon slayer.*hashira training', 2024, "Demon Slayer: Hashira Training Arc (Aired Spring 2024)"),
    (r'demon slayer.*swordsmith', 2023, "Demon Slayer: Swordsmith Village Arc (Aired Spring 2023)"),
    (r'demon slayer.*entertainment district', 2022, "Demon Slayer: Entertainment District Arc (Aired Winter 2022)"),
    (r'demon slayer.*mugen train', 2020, "Demon Slayer: Mugen Train (Released Oct 2020)"),
    (r'jujutsu kaisen 0', 2021, "JUJUTSU KAISEN 0 Movie (Released Dec 2021)"),
    (r'attack on titan.*final season', 2022, "Attack on Titan Final Season (Aired 2020-2023)"),
    (r'oshi no ko(?!\s*season\s*2)', 2023, "Oshi no Ko Season 1 (Aired Spring 2023)"),
    (r'tokyo anime award.*2024', 2024, "Tokyo Anime Award Festival 2024 (Announced March 2024)"),
    (r'crunchyroll.*awards?\s*2024', 2024, "Crunchyroll Anime Awards 2024 (Announced March 2024)"),
    (r'taaf.*2024', 2024, "TAAF 2024 Awards (Concluded March 2024)"),
    (r'award-taaf-2024', 2024, "TAAF 2024 Awards Entry"),
    (r'award-crunchyroll-2024', 2024, "Crunchyroll Awards 2024 Entry"),
]


def run_stale_cleanup(dry_run: bool = False) -> dict[str, Any]:
    session: Session = next(get_session())
    drafts = list(session.exec(select(AnimeDraft)).all())
    total_checked = len(drafts)

    marked_stale = []
    current_year = datetime.now(timezone.utc).year  # e.g. 2026

    for draft in drafts:
        reasons = []

        # 1. Missing published_at
        if draft.published_at is None:
            reasons.append("Missing published_at date")

        # Determine effective post date
        post_dt = draft.published_at or draft.created_at
        post_year = post_dt.year if post_dt else None

        # 2. Explicit season_year older than 1 year with recent post date
        if draft.season_year and post_year:
            if (post_year - draft.season_year) >= 1 and post_year >= 2025:
                reasons.append(
                    f"Stored anime season_year ({draft.season_year}) is >1 year older than post date ({post_year})"
                )

        # 3. Known historical anime pattern check
        combined_text = (
            f"{draft.title_en or ''} {draft.external_id or ''} "
            f"{draft.summary_en or ''} {draft.source_url or ''}"
        ).lower()

        for pattern, release_year, label in HISTORICAL_ANIME_MAP:
            if re.search(pattern, combined_text):
                if post_year and (post_year - release_year) >= 1 and post_year >= 2025:
                    reasons.append(
                        f"{label} released in {release_year}, but presented with recent {post_year} date"
                    )
                    break

        if reasons:
            draft.is_stale = True
            session.add(draft)
            marked_stale.append({
                "id": draft.id,
                "content_type": draft.content_type,
                "title_en": draft.title_en,
                "published_at": str(draft.published_at) if draft.published_at else "NULL",
                "reasons": "; ".join(reasons),
            })
        else:
            # If valid current item, ensure is_stale is False
            draft.is_stale = False
            session.add(draft)

    if not dry_run:
        session.commit()

    return {
        "total_checked": total_checked,
        "total_hidden": len(marked_stale),
        "examples": marked_stale[:10],
        "all_stale": marked_stale,
    }


def main():
    report = run_stale_cleanup(dry_run=False)
    print("=" * 80)
    print("CLEANUP STALE ANIME DRAFTS REPORT")
    print("=" * 80)
    print(f"Total Records Checked: {report['total_checked']}")
    print(f"Total Records Marked Stale (Hidden from public feeds): {report['total_hidden']}")
    print("-" * 80)
    print("10 CONCRETE EXAMPLES OF HIDDEN STALE POSTS:")
    print("-" * 80)
    for idx, ex in enumerate(report["examples"], start=1):
        print(f"[{idx:02d}] ID #{ex['id']:<3} | [{ex['content_type'].upper():<8}] | Pub: {ex['published_at'][:10]}")
        print(f"     Title: {ex['title_en']}")
        print(f"     Reason: {ex['reasons']}")
        print()


if __name__ == "__main__":
    main()
