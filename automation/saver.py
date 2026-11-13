"""Saver module for staging rewritten anime drafts into the database.

Connects via DATABASE_URL, saves entries strictly as 'draft', and skips
existing items based on external_id to prevent duplicates.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from sqlmodel import Session, create_engine, select, func

from app.models import AnimeDraft

load_dotenv()

logger = logging.getLogger("automation.saver")


def get_automation_engine():
    """Get SQLAlchemy/SQLModel engine using DATABASE_URL or app config fallback."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        try:
            from app.config import get_settings
            db_url = get_settings().database_url
        except Exception:
            db_url = "sqlite:///./anime_hindi.db"

    connect_args: dict[str, Any] = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(db_url, connect_args=connect_args)


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = get_automation_engine()
    return _engine


def _normalize_title_slug(title: str) -> str:
    """Normalize title for fuzzy / near-duplicate matching."""
    return re.sub(r'[^a-zA-Z0-9]', '', title.lower())


def is_near_duplicate(title_en: str, hours: int = 48) -> bool:
    """Check if an anime update with a matching or nearly identical title exists within 48 hours."""
    if not title_en or len(title_en.strip()) < 5:
        return False

    norm_target = _normalize_title_slug(title_en)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    engine = _get_engine()
    with Session(engine) as session:
        statement = select(AnimeDraft.title_en).where(AnimeDraft.created_at >= cutoff)
        recent_titles = session.exec(statement).all()

        for rt in recent_titles:
            norm_recent = _normalize_title_slug(rt)
            if norm_target == norm_recent:
                logger.info("Near-duplicate detected within %d hours: '%s' vs '%s'", hours, title_en, rt)
                return True
            # Substring containment for long titles
            if len(norm_target) > 20 and len(norm_recent) > 20:
                if norm_target in norm_recent or norm_recent in norm_target:
                    logger.info("Near-duplicate substring match within %d hours: '%s' vs '%s'", hours, title_en, rt)
                    return True

    return False


def evaluate_quality_gate(
    draft_data: dict[str, Any],
    raw_source_text: str = "",
) -> tuple[str, list[str]]:
    """Quality gate evaluating summary length, language cleanliness, and copying.

    Returns:
        Tuple of (status: 'draft' | 'needs_review', reasons: list[str]).
    """
    reasons: list[str] = []
    summary_en = (draft_data.get("summary_en") or "").strip()
    summary_hi = (draft_data.get("summary_hi") or "").strip()

    if not summary_en or not summary_hi:
        reasons.append("Empty or missing English/Hindi summary")

    # Word count check (< 60 words triggers review)
    en_words = len(summary_en.split())
    hi_words = len(summary_hi.split())
    if en_words < 60 or hi_words < 60:
        reasons.append(f"Summary below 60 words minimum (EN: {en_words}, HI: {hi_words})")

    # Japanese character check in final drafts (Kanji/Hiragana/Katakana should be translated)
    # \u3040-\u30ff (Kana), \u4e00-\u9fff (Kanji)
    jp_regex = re.compile(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]')
    if jp_regex.search(summary_en) or jp_regex.search(summary_hi):
        reasons.append("Contains raw untranslated Japanese characters in summary copy")

    # High similarity / copying check against raw source text
    if raw_source_text and summary_en:
        raw_tokens = set(re.findall(r'\b[a-zA-Z]{4,}\b', raw_source_text.lower()))
        summary_tokens = set(re.findall(r'\b[a-zA-Z]{4,}\b', summary_en.lower()))
        if raw_tokens and summary_tokens:
            intersection = raw_tokens.intersection(summary_tokens)
            overlap_ratio = len(intersection) / len(summary_tokens)
            if overlap_ratio > 0.70:
                reasons.append(f"High token overlap with source text ({overlap_ratio:.1%})")

    status = "needs_review" if reasons else "draft"
    return status, reasons


def cleanup_old_unpublished_drafts(days: int = 30) -> int:
    """Automatically purge unreviewed drafts older than 30 days.

    STRICT SAFETY RULE: Never deletes published items. Only deletes
    status='draft' or status='needs_review' records.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    engine = _get_engine()
    with Session(engine) as session:
        statement = select(AnimeDraft).where(
            AnimeDraft.status.in_(["draft", "needs_review"]),
            AnimeDraft.created_at < cutoff,
        )
        old_drafts = list(session.exec(statement).all())
        count = len(old_drafts)
        for d in old_drafts:
            session.delete(d)
        session.commit()

        if count > 0:
            logger.info("Purged %d stale unpublished drafts older than %d days.", count, days)
        return count


def is_external_id_saved(external_id: str) -> bool:
    """Check if a draft with the given external_id already exists in the database."""
    engine = _get_engine()
    with Session(engine) as session:
        statement = select(AnimeDraft).where(AnimeDraft.external_id == external_id)
        result = session.exec(statement).first()
        return result is not None


def save_draft(
    draft_data: dict[str, Any],
    raw_source_text: str = "",
    check_near_duplicates: bool = True,
) -> tuple[bool, Any]:
    """Insert an anime draft into the database, applying safety and quality gates.

    Args:
        draft_data: Dictionary containing draft fields:
            external_id, source_name, source_url, title_en, title_hi,
            summary_en, summary_hi, image_url, content_type.
        raw_source_text: Raw source facts or description for quality gate checking.
        check_near_duplicates: Whether to verify against similar titles in last 48h.

    Returns:
        Tuple of (success: bool, id_or_reason: Any).
    """
    external_id = draft_data.get("external_id")
    if not external_id:
        return False, "missing_external_id"

    title_en = str(draft_data.get("title_en", ""))

    # 1. Near-duplicate check (within 48 hours)
    if check_near_duplicates and is_near_duplicate(title_en, hours=48):
        logger.info("Skipping near-duplicate item: '%s'", title_en)
        return False, "near_duplicate"

    # 2. Quality gate evaluation
    status, reasons = evaluate_quality_gate(draft_data, raw_source_text=raw_source_text)
    if status == "needs_review":
        logger.warning(
            "Draft for '%s' triggered quality gate (%s). Setting status='needs_review'.",
            title_en,
            ", ".join(reasons),
        )

    engine = _get_engine()
    with Session(engine) as session:
        # Prevent exact duplicates
        existing = session.exec(
            select(AnimeDraft).where(AnimeDraft.external_id == external_id)
        ).first()

        if existing:
            logger.info("Draft with external_id '%s' already exists (ID: %s). Skipping.", external_id, existing.id)
            return False, "already_exists"

        # Construct safe draft entity
        draft = AnimeDraft(
            content_type=draft_data.get("content_type", "schedule"),
            status=status,  # Either 'draft' or 'needs_review'
            source_name=draft_data.get("source_name", "AniList"),
            source_url=str(draft_data.get("source_url", "")),
            external_id=external_id,
            title_en=title_en,
            title_hi=draft_data.get("title_hi"),
            summary_en=draft_data.get("summary_en"),
            summary_hi=draft_data.get("summary_hi"),
            image_url=draft_data.get("image_url"),
            published_at=None,
            created_at=datetime.now(timezone.utc),
        )

        session.add(draft)
        session.commit()
        session.refresh(draft)

        logger.info("Saved new draft ID %s [%s] for external_id '%s'", draft.id, draft.status, external_id)
        return True, draft.id


def get_recent_drafts(limit: int = 10) -> list[AnimeDraft]:
    """Retrieve recent drafts ordered by created_at descending."""
    engine = _get_engine()
    with Session(engine) as session:
        statement = select(AnimeDraft).order_by(AnimeDraft.id.desc()).limit(limit)
        return list(session.exec(statement).all())


def count_today_drafts_by_source(source_name: str) -> int:
    """Count how many drafts have been created today for the specified source.

    Used to enforce daily per-source draft caps to prevent flooding and token depletion.
    """
    engine = _get_engine()
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with Session(engine) as session:
        statement = select(func.count(AnimeDraft.id)).where(
            AnimeDraft.source_name == source_name,
            AnimeDraft.created_at >= today_start,
        )
        count = session.exec(statement).one()
        return int(count or 0)


def count_today_total_drafts() -> int:
    """Count all drafts created today across all sources to enforce global Gemini caps."""
    engine = _get_engine()
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with Session(engine) as session:
        statement = select(func.count(AnimeDraft.id)).where(AnimeDraft.created_at >= today_start)
        count = session.exec(statement).one()
        return int(count or 0)



