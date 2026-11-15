"""Automation pipeline orchestrator for Hindi Anime Guide (Part 3).

Collects anime updates from multiple verified sources:
- AniList (airing schedule)
- LiveChart.me (RSS news headlines)
- MangaDex (manga chapter updates)
- Comic Natalie (audited, skipped per WAF/terms)
- Oricon News (audited, skipped per RSS 410 retirement)

Passes factual metadata through Gemini rewriter (or factual fallback) and stages as drafts.
Enforces per-source daily caps, respects rate limits, and provides isolated error handling.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Callable

from dotenv import load_dotenv

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from automation.fetchers.anilist import fetch_current_season_schedule, fetch_live_weekly_schedule
from automation.fetchers.livechart_rss import fetch_livechart_news
from automation.fetchers.manga import fetch_latest_manga_chapters, sync_manga_database
from automation.fetchers.natalie_rss import fetch_natalie_news
from automation.fetchers.news_rss import fetch_all_news_rss
from automation.fetchers.oricon_rss import fetch_oricon_news
from automation.fetchers.trailers import fetch_live_trailers
from automation.rewriter import rewrite_anime_update
from automation.saver import (
    cleanup_old_unpublished_drafts,
    count_today_drafts_by_source,
    count_today_total_drafts,
    get_recent_drafts,
    is_external_id_saved,
    save_draft,
)
from automation.seed_awards import seed_awards_database

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("automation.run_all")

DEFAULT_DAILY_CAP = 10
DEFAULT_GLOBAL_GEMINI_CAP = 40

def _fetch_news_pipeline(limit: int = 10) -> list[dict[str, Any]]:
    items = fetch_all_news_rss(limit_per_source=max(2, limit // 4))
    return items[:limit]


def _fetch_trailers_pipeline(limit: int = 10) -> list[dict[str, Any]]:
    trailers = fetch_live_trailers(force_refresh=True)
    items: list[dict[str, Any]] = []
    for t in trailers[:limit]:
        items.append({
            "external_id": f"trailer_{t.get('youtube_id', '')}",
            "content_type": "trailer",
            "source_name": t.get("source_channel") or "Official Anime Channel",
            "source_url": t.get("youtube_url", ""),
            "title_en": t.get("title_en") or t.get("title_romaji", ""),
            "summary_en": f"Official PV/Trailer for {t.get('title_en', '')}.",
            "image_url": t.get("thumbnail_url"),
            "published_at": t.get("published_at"),
            "season": t.get("season"),
            "season_year": t.get("season_year"),
        })
    return items


def _fetch_schedule_pipeline(limit: int = 10) -> list[dict[str, Any]]:
    schedule_data = fetch_live_weekly_schedule(force_refresh=True)
    items: list[dict[str, Any]] = []
    weekly_cards = schedule_data.get("weekly_items") or []
    for c in weekly_cards:
        items.append({
            "external_id": f"schedule_{c.get('media_id')}_ep{c.get('episode')}",
            "anilist_id": c.get("media_id"),
            "content_type": "schedule",
            "source_name": "AniList Airing Schedule",
            "source_url": f"https://anilist.co/anime/{c.get('media_id')}",
            "title_en": c.get("title_en") or c.get("title_romaji", ""),
            "title_hi": c.get("title_hi"),
            "summary_en": c.get("summary_en", ""),
            "summary_hi": c.get("summary_hi", ""),
            "image_url": c.get("image_url"),
            "airing_day": c.get("day_name_hi"),
            "airing_time": c.get("airing_ist_time"),
            "published_at": c.get("airing_at_iso"),
        })
    return items[:limit]


def _fetch_awards_pipeline(limit: int = 10) -> list[dict[str, Any]]:
    res = seed_awards_database()
    return [{
        "external_id": f"awards_verified_seed_{len(res)}",
        "content_type": "news",
        "source_name": "Official Awards Registry",
        "source_url": "https://animefestival.jp/en/",
        "title_en": "Anime Awards Database Seed & Verification",
        "summary_en": f"Verified {len(res)} official anime awards ceremonies.",
    }]


# Source mapping dictionary
SOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "anilist": {
        "display_name": "AniList Airing Schedule",
        "source_name": "AniList",
        "fetcher": fetch_current_season_schedule,
        "default_type": "schedule",
    },
    "schedule": {
        "display_name": "AniList Live Weekly Airing Schedule",
        "source_name": "AniList Airing Schedule",
        "fetcher": _fetch_schedule_pipeline,
        "default_type": "schedule",
    },
    "news": {
        "display_name": "Verified RSS News (ANN, Crunchyroll, Anime Corner, Otaku USA, LiveChart)",
        "source_name": "Anime News RSS",
        "fetcher": _fetch_news_pipeline,
        "default_type": "news",
    },
    "trailers": {
        "display_name": "Official Anime Trailers & PVs (YouTube & AniList)",
        "source_name": "Official YouTube / AniList",
        "fetcher": _fetch_trailers_pipeline,
        "default_type": "trailer",
    },
    "livechart": {
        "display_name": "LiveChart.me RSS News",
        "source_name": "LiveChart.me",
        "fetcher": fetch_livechart_news,
        "default_type": "news",
    },
    "manga": {
        "display_name": "MangaDex Manga Updates",
        "source_name": "MangaDex",
        "fetcher": fetch_latest_manga_chapters,
        "default_type": "manga",
    },
    "awards": {
        "display_name": "Verified Anime Awards (Crunchyroll & TAAF)",
        "source_name": "Official Awards Registry",
        "fetcher": _fetch_awards_pipeline,
        "default_type": "news",
    },
    "natalie": {
        "display_name": "Comic Natalie (Audited - Skipped)",
        "source_name": "Comic Natalie",
        "fetcher": fetch_natalie_news,
        "default_type": "news",
    },
    "oricon": {
        "display_name": "Oricon News (Audited - Skipped)",
        "source_name": "Oricon News",
        "fetcher": fetch_oricon_news,
        "default_type": "news",
    },
}


def get_configured_daily_cap() -> int:
    """Retrieve daily cap from app settings or environment, fallback to 10."""
    try:
        from app.config import get_settings
        return getattr(get_settings(), "daily_source_draft_cap", DEFAULT_DAILY_CAP)
    except Exception:
        env_val = os.getenv("DAILY_SOURCE_DRAFT_CAP")
        if env_val and env_val.isdigit():
            return int(env_val)
        return DEFAULT_DAILY_CAP


def run_source_pipeline(
    source_key: str,
    limit: int = 10,
    daily_cap: int = 10,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    """Execute pipeline for a single source with isolation and daily cap enforcement."""
    config = SOURCE_REGISTRY[source_key]
    display_name = config["display_name"]
    source_name = config["source_name"]
    fetcher_fn: Callable[..., list[dict[str, Any]]] = config["fetcher"]
    default_type = config["default_type"]

    logger.info("-" * 60)
    logger.info("Starting pipeline for source [%s]: %s", source_key, display_name)

    stats = {
        "source_key": source_key,
        "source_name": source_name,
        "fetched": 0,
        "new": 0,
        "skipped_existing": 0,
        "skipped_near_duplicate": 0,
        "skipped_cap": 0,
        "failed": 0,
        "samples": [],
    }

    # Check current daily draft count for this source
    today_count = count_today_drafts_by_source(source_name)
    logger.info("Current drafts today for '%s': %d / %d (Daily Cap)", source_name, today_count, daily_cap)

    if today_count >= daily_cap:
        logger.warning(
            "--> [DAILY CAP REACHED] Source '%s' already reached its daily cap of %d new drafts. Skipping fetch.",
            source_name,
            daily_cap,
        )
        stats["skipped_cap"] = limit
        return stats

    # 1. Fetch raw factual items
    try:
        fetched_items = fetcher_fn(limit=limit)
    except Exception as exc:
        logger.exception("Error executing fetcher for %s: %s", source_name, exc)
        stats["failed"] += 1
        return stats

    stats["fetched"] = len(fetched_items)
    if not fetched_items:
        logger.info("No items returned from fetcher for %s.", source_name)
        return stats

    # 2. Process each item
    for index, item in enumerate(fetched_items, start=1):
        ext_id = item.get("external_id", "unknown")
        title = item.get("title_en", "Untitled")

        # Check if daily cap was reached during this run
        if today_count + stats["new"] >= daily_cap:
            logger.info("--> [CAP HIT] Daily cap (%d) reached for '%s'. Skipping remaining items.", daily_cap, source_name)
            stats["skipped_cap"] += (len(fetched_items) - index + 1)
            break

        try:
            # Check for existing entry before calling rewriter
            if is_external_id_saved(ext_id):
                logger.info("--> [SKIPPED] '%s' already exists in anime_draft database.", ext_id)
                stats["skipped_existing"] += 1
                continue

            # Rewrite facts into English and Hindi (with polite pacing to stay inside Gemini 15 RPM limit)
            import time
            time.sleep(2.0)
            rewritten = rewrite_anime_update(
                item_facts=item,
                allow_offline_fallback=allow_fallback,
            )

            if not rewritten:
                logger.warning("--> [FAILED] Could not rewrite '%s'.", title)
                stats["failed"] += 1
                continue

            # Assemble draft data with source tracking and factual published_at
            draft_payload = {
                "content_type": item.get("content_type", default_type),
                "source_name": item.get("source_name", source_name),
                "source_url": item.get("source_url", ""),
                "external_id": ext_id,
                "title_en": rewritten.get("title_en", title),
                "title_hi": rewritten.get("title_hi"),
                "summary_en": rewritten.get("summary_en"),
                "summary_hi": rewritten.get("summary_hi"),
                "key_points_en": rewritten.get("key_points_en"),
                "key_points_hi": rewritten.get("key_points_hi"),
                "image_url": item.get("image_url"),
                "published_at": item.get("published_at") or item.get("pub_date_iso"),
                "anilist_id": item.get("anilist_id"),
                "mal_id": item.get("mal_id"),
                "season": item.get("season"),
                "season_year": item.get("season_year"),
                "chapters": item.get("chapters"),
                "volumes": item.get("volumes"),
                "publication_status": item.get("publication_status"),
                "airing_day": item.get("airing_day"),
                "airing_time": item.get("airing_time"),
            }

            # Save draft with quality gate & near-duplicate protection
            raw_source_text = item.get("raw_summary", "") or item.get("title_en", "")
            success, draft_id_or_reason = save_draft(
                draft_payload,
                raw_source_text=raw_source_text,
                check_near_duplicates=True,
            )
            if success:
                stats["new"] += 1
                draft_payload["db_id"] = draft_id_or_reason
                stats["samples"].append(draft_payload)
                logger.info("--> [SUCCESS] Staged new draft #%s into database (%s).", draft_id_or_reason, ext_id)
            elif draft_id_or_reason == "already_exists":
                stats["skipped_existing"] += 1
                logger.info("--> [SKIPPED] Draft already saved during concurrent run.")
            elif draft_id_or_reason == "near_duplicate":
                stats["skipped_near_duplicate"] += 1
                logger.info("--> [SKIPPED NEAR-DUPLICATE] Similar title already drafted in last 48 hours.")
            else:
                stats["failed"] += 1
                logger.error("--> [FAILED] Failed to save draft: %s", draft_id_or_reason)

        except Exception as exc:
            stats["failed"] += 1
            logger.exception("--> [ERROR] Unhandled exception processing '%s' (%s): %s", title, ext_id, exc)

    return stats


def run_pipeline(
    sources: list[str] | None = None,
    limit: int = 10,
    daily_cap: int | None = None,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    """Execute the multi-source anime updates pipeline with isolated try/except per source."""
    effective_cap = daily_cap if daily_cap is not None else get_configured_daily_cap()
    active_sources = sources or list(SOURCE_REGISTRY.keys())

    logger.info("=" * 70)
    logger.info("Hindi Anime Guide Automation Pipeline (Part 4 - Production Ready)")
    logger.info("Active Sources: %s", ", ".join(active_sources))
    logger.info("Per-source item limit: %d | Daily Cap: %d | Fallback: %s", limit, effective_cap, allow_fallback)

    # 1. Purge stale unpublished drafts older than 30 days
    try:
        purged = cleanup_old_unpublished_drafts(days=30)
        if purged > 0:
            logger.info("Auto-cleanup: Purged %d unreviewed drafts older than 30 days.", purged)
    except Exception as exc:
        logger.warning("Auto-cleanup encountered non-fatal error: %s", exc)

    # 2. Check global daily Gemini request cap (stay inside free tier)
    today_total = count_today_total_drafts()
    logger.info("Total drafts created today across all sources: %d / %d (Global Cap)", today_total, DEFAULT_GLOBAL_GEMINI_CAP)
    effective_fallback = allow_fallback
    if today_total >= DEFAULT_GLOBAL_GEMINI_CAP:
        logger.warning(
            "--> [GLOBAL CAP EXCEEDED] Today's global draft count (%d) reached global cap of %d. "
            "Switching to offline deterministic fallback to protect Gemini free quota.",
            today_total,
            DEFAULT_GLOBAL_GEMINI_CAP,
        )
        effective_fallback = True

    gemini_key_present = bool(os.getenv("GEMINI_API_KEY", "").strip())
    logger.info(
        "GEMINI_API_KEY: %s",
        "YES (Live Gemini API active)" if (gemini_key_present and not (today_total >= DEFAULT_GLOBAL_GEMINI_CAP)) else "NO / Bypassed by Cap (Offline factual preview active)",
    )
    logger.info("=" * 70)

    overall_stats: dict[str, Any] = {
        "sources": {},
        "total_fetched": 0,
        "total_new": 0,
        "total_skipped_existing": 0,
        "total_skipped_near_duplicate": 0,
        "total_skipped_cap": 0,
        "total_failed": 0,
        "all_samples": [],
    }

    for source_key in active_sources:
        if source_key not in SOURCE_REGISTRY:
            logger.error("Unknown source '%s'. Valid options: %s", source_key, list(SOURCE_REGISTRY.keys()))
            continue

        try:
            source_res = run_source_pipeline(
                source_key=source_key,
                limit=limit,
                daily_cap=effective_cap,
                allow_fallback=effective_fallback,
            )
            overall_stats["sources"][source_key] = source_res
            overall_stats["total_fetched"] += source_res["fetched"]
            overall_stats["total_new"] += source_res["new"]
            overall_stats["total_skipped_existing"] += source_res["skipped_existing"]
            overall_stats["total_skipped_near_duplicate"] += source_res.get("skipped_near_duplicate", 0)
            overall_stats["total_skipped_cap"] += source_res["skipped_cap"]
            overall_stats["total_failed"] += source_res["failed"]
            overall_stats["all_samples"].extend(source_res["samples"])
        except Exception as exc:
            logger.exception("Fatal error while running source '%s': %s", source_key, exc)
            overall_stats["sources"][source_key] = {"error": str(exc), "failed": 1}
            overall_stats["total_failed"] += 1

    _print_summary(overall_stats)
    return overall_stats


def _print_summary(stats: dict[str, Any]) -> None:
    """Print clean CLI telemetry summary."""
    print("\n" + "=" * 76)
    print("PIPELINE EXECUTION SUMMARY (PART 4 - PRODUCTION READY)")
    print("=" * 76)
    print(f"{'Source':<16} | {'Fetched':<8} | {'New':<6} | {'Duplicate':<10} | {'Near-Dup':<9} | {'Cap Hit':<8} | {'Failed':<6}")
    print("-" * 76)

    for src_key, res in stats["sources"].items():
        if "error" in res:
            print(f"{src_key:<16} | ERROR: {res['error']}")
        else:
            print(
                f"{src_key:<16} | {res['fetched']:<8} | {res['new']:<6} | "
                f"{res['skipped_existing']:<10} | {res.get('skipped_near_duplicate', 0):<9} | {res['skipped_cap']:<8} | {res['failed']:<6}"
            )

    print("-" * 76)
    print(
        f"{'TOTALS':<16} | {stats['total_fetched']:<8} | {stats['total_new']:<6} | "
        f"{stats['total_skipped_existing']:<10} | {stats['total_skipped_near_duplicate']:<9} | {stats['total_skipped_cap']:<8} | {stats['total_failed']:<6}"
    )
    print("=" * 76)
    print("=" * 70)

    # Display recent database samples
    recent = get_recent_drafts(limit=3)
    if recent:
        print(f"\nRECENT DATABASE DRAFTS ({len(recent)} displayed):")
        print("-" * 70)
        for i, draft in enumerate(recent, start=1):
            print(f"[{i}] ID: {draft.id} | [{draft.content_type.upper()}] from {draft.source_name}")
            print(f"    External ID:  {draft.external_id}")
            print(f"    Title (EN):   {draft.title_en}")
            print(f"    Title (HI):   {draft.title_hi}")
            print(f"    Source URL:   {draft.source_url}")
            print(f"    Cover Image:  {draft.image_url}")
            print("-" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="Hindi Anime Guide Multi-Source Automation Pipeline")
    parser.add_argument(
        "--source",
        type=str,
        default="all",
        help="Specify source to run ('all', or comma-separated list like 'news,trailers', 'schedule', 'manga', 'awards')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum items to fetch per source (default: 10)",
    )
    parser.add_argument(
        "--daily-cap",
        type=int,
        default=None,
        help="Override daily new draft cap per source (default from config/10)",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Disable offline deterministic factual fallback",
    )

    args = parser.parse_args()

    if args.source == "all":
        active_sources = None
    elif "," in args.source:
        active_sources = [s.strip() for s in args.source.split(",") if s.strip()]
    else:
        active_sources = [args.source.strip()]

    run_pipeline(
        sources=active_sources,
        limit=args.limit,
        daily_cap=args.daily_cap,
        allow_fallback=not args.no_fallback,
    )


if __name__ == "__main__":
    main()
