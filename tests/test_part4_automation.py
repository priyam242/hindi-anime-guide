"""Tests for Part 4 of 4: Production Scheduling, Quality Gates, Near-Duplicate Detection & Admin Widget."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.models import Admin1, AnimeDraft
from app.auth import create_access_token, get_password_hash
from automation.saver import (
    cleanup_old_unpublished_drafts,
    evaluate_quality_gate,
    is_near_duplicate,
    save_draft,
)


def test_quality_gate_checks():
    """Verify quality gate triggers needs_review for short summaries, Japanese text, and copied text."""
    # 1. Short summary (< 60 words)
    short_draft = {
        "title_en": "Short Anime News",
        "summary_en": "This is a very short summary with only ten words.",
        "summary_hi": "यह केवल दस शब्दों का एक बहुत छोटा सारांश है।",
    }
    status, reasons = evaluate_quality_gate(short_draft)
    assert status == "needs_review"
    assert any("below 60 words" in r for r in reasons)

    # 2. Contains untranslated Japanese characters
    jp_draft = {
        "title_en": "Anime News Announcement",
        "summary_en": (
            "This is a long editorial summary describing the official production announcement of the anime series. "
            "The committee confirms that season two is officially scheduled for broadcast. "
            "Fans can look forward to character reveals and promotional videos. "
            "アニメ (Anime) broadcast starts next spring in Tokyo."
            + " Additional editorial context continues here to meet the word count threshold cleanly."
        ),
        "summary_hi": (
            "यह एनीमे श्रृंखला के आधिकारिक उत्पादन की घोषणा का विवरण देने वाला एक लंबा संपादकीय सारांश है। "
            "समिति पुष्टि करती है कि दूसरा सीज़न आधिकारिक तौर पर प्रसारण के लिए निर्धारित है। "
            "प्रशंसक नए कैरेक्टर खुलासे और प्रचार वीडियो की उम्मीद कर सकते हैं।"
            + " संपादकीय समीक्षा अतिरिक्त विवरणों की निगरानी करती रहेगी और दर्शकों को सटीक जानकारी देती रहेगी।"
        ),
    }
    status, reasons = evaluate_quality_gate(jp_draft)
    assert status == "needs_review"
    assert any("Japanese characters" in r for r in reasons)

    # 3. High similarity / copying with source
    source_text = "Studio MAPPA officially announced that Chainsaw Man movie Reze arc will debut in theaters nationwide next winter."
    copied_draft = {
        "title_en": "Chainsaw Man Movie Reze Arc",
        "summary_en": (
            "Studio MAPPA officially announced that Chainsaw Man movie Reze arc will debut in theaters nationwide next winter. "
            "Studio MAPPA officially announced that Chainsaw Man movie Reze arc will debut in theaters nationwide next winter. "
            "Studio MAPPA officially announced that Chainsaw Man movie Reze arc will debut in theaters nationwide next winter."
        ),
        "summary_hi": (
            "स्टूडियो माप्पा ने आधिकारिक घोषणा की है कि चेनसॉ मैन मूवी रेज़ आर्क अगले सर्दियों में देश भर के सिनेमाघरों में रिलीज़ होगी। "
            "यह फिल्म फ्रैंचाइज़ी के प्रशंसकों के लिए एक बड़ा मील का पत्थर साबित होगी। "
            "संपादकीय डेस्क इसके वैश्विक थिएट्रिकल डिस्ट्रीब्यूशन पर नज़र बनाए रखेगी।"
        ),
    }
    status, reasons = evaluate_quality_gate(copied_draft, raw_source_text=source_text)
    assert status == "needs_review"
    assert any("token overlap" in r for r in reasons)

    # 4. Clean, valid draft passes as 'draft'
    clean_draft = {
        "title_en": "Clean Anime News Announcement",
        "summary_en": (
            "The official production committee for the popular television anime series has officially released details "
            "regarding its upcoming broadcast schedule. Broadcast partners confirmed that episodes will begin streaming "
            "internationally across authorized digital services. Anime fans in India and worldwide can anticipate regular weekly "
            "releases following the premiere date. Editorial analysis highlights strong anticipation among community members. "
            "Our editorial team will continue tracking official licensing announcements and streaming availability updates."
        ),
        "summary_hi": (
            "लोकप्रिय टेलीविजन एनीमे श्रृंखला की निर्माण समिति ने आगामी प्रसारण कार्यक्रम के संबंध में सभी विवरण आधिकारिक तौर पर जारी किए हैं। "
            "वितरण भागीदारों ने पुष्टि की है कि एपिसोड्स अधिकृत डिजिटल सेवाओं पर अंतरराष्ट्रीय स्तर पर स्ट्रीम किए जाएंगे। "
            "भारत और दुनिया भर के एनीमे प्रशंसक प्रीमियर तिथि के बाद नियमित साप्ताहिक एपिसोड्स का आनंद ले सकते हैं। "
            "हमारी संपादकीय टीम आधिकारिक लाइसेंसिंग घोषणाओं और ओटीटी स्ट्रीमिंग पर नज़र बनाए रखेगी।"
        ),
    }
    status, reasons = evaluate_quality_gate(clean_draft, raw_source_text="Unrelated different brief facts.")
    assert status == "draft"
    assert len(reasons) == 0


def test_near_duplicate_detection(isolate_database_per_test):
    """Verify that near-duplicate titles within 48 hours are detected and skipped."""
    engine = isolate_database_per_test

    with Session(engine) as session:
        draft = AnimeDraft(
            content_type="news",
            status="draft",
            source_name="LiveChart.me",
            source_url="https://livechart.me/headlines/101",
            external_id="test_lc_101",
            title_en="Chainsaw Man Season 2 Official Announcement and Teaser PV",
            title_hi="चेनसॉ मैन सीज़न 2 आधिकारिक घोषणा",
            summary_en="Valid summary...",
            summary_hi="वैध सारांश...",
            created_at=datetime.now(timezone.utc) - timedelta(hours=6),
        )
        session.add(draft)
        session.commit()

    # Identical or near-identical title within 48 hours
    with patch("automation.saver._get_engine", return_value=engine):
        assert is_near_duplicate("Chainsaw Man Season 2 Official Announcement and Teaser PV", hours=48) is True
        assert is_near_duplicate("chainsaw man season 2 official announcement and teaser pv", hours=48) is True
        # Completely different title -> False
        assert is_near_duplicate("One Piece Episode 1130 Broadcast Details", hours=48) is False


def test_auto_cleanup_old_unpublished_drafts(isolate_database_per_test):
    """Verify that only unpublished drafts older than 30 days are purged, and published items are safe."""
    engine = isolate_database_per_test
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        # 1. Old unreviewed draft (>30 days) -> SHOULD BE PURGED
        old_draft = AnimeDraft(
            content_type="news",
            status="draft",
            source_name="AniList",
            source_url="https://anilist.co/1",
            external_id="old_draft_to_purge",
            title_en="Old Draft to Purge",
            created_at=now - timedelta(days=35),
        )
        # 2. Old published item (>30 days) -> MUST BE PRESERVED
        old_published = AnimeDraft(
            content_type="news",
            status="published",
            source_name="AniList",
            source_url="https://anilist.co/2",
            external_id="old_published_keep",
            title_en="Old Published Article Keep",
            published_at=now - timedelta(days=34),
            created_at=now - timedelta(days=35),
        )
        # 3. Recent unreviewed draft (<30 days) -> MUST BE PRESERVED
        recent_draft = AnimeDraft(
            content_type="news",
            status="draft",
            source_name="AniList",
            source_url="https://anilist.co/3",
            external_id="recent_draft_keep",
            title_en="Recent Draft Keep",
            created_at=now - timedelta(days=5),
        )
        session.add_all([old_draft, old_published, recent_draft])
        session.commit()

    with patch("automation.saver._get_engine", return_value=engine):
        purged_count = cleanup_old_unpublished_drafts(days=30)

    assert purged_count == 1

    with Session(engine) as session:
        remaining = session.exec(select(AnimeDraft)).all()
        remaining_ids = [d.external_id for d in remaining]
        assert "old_draft_to_purge" not in remaining_ids
        assert "old_published_keep" in remaining_ids
        assert "recent_draft_keep" in remaining_ids


def test_admin_dashboard_widget(isolate_database_per_test):
    """Verify the AI Content Automation telemetry widget renders on /admin."""
    engine = isolate_database_per_test
    client = TestClient(app)

    with Session(engine) as session:
        admin = Admin1(
            email="dash_admin@animeguide.com",
            name="Dashboard Admin",
            hashed_password=get_password_hash("Pass123!"),
            is_active=True,
            totp_enabled=True,
        )
        session.add(admin)

        draft1 = AnimeDraft(
            content_type="news",
            status="draft",
            source_name="LiveChart.me",
            source_url="https://livechart.me/1",
            external_id="dash_draft_1",
            title_en="Draft Waiting Review",
            created_at=datetime.now(timezone.utc),
        )
        draft2 = AnimeDraft(
            content_type="news",
            status="needs_review",
            source_name="LiveChart.me",
            source_url="https://livechart.me/2",
            external_id="dash_draft_2",
            title_en="Draft Flagged Review",
            created_at=datetime.now(timezone.utc),
        )
        session.add_all([draft1, draft2])
        session.commit()

    token = create_access_token({"sub": "dash_admin@animeguide.com", "name": "Dashboard Admin", "user_type": "admin"})
    client.cookies.set("admin_token", token)

    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert "AI Content Automation Pipeline" in response.text
    assert "Waiting Drafts" in response.text
    assert "Review Staged Drafts" in response.text


def test_github_actions_workflow_file_exists():
    """Verify that .github/workflows/fetch.yml exists and defines proper scheduling."""
    workflow_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".github", "workflows", "fetch.yml")
    assert os.path.exists(workflow_path), f"Workflow file not found at {workflow_path}"

    with open(workflow_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "cron: '0 */3 * * *'" in content
    assert "workflow_dispatch:" in content
    assert "secrets.DATABASE_URL" in content
    assert "secrets.GEMINI_API_KEY" in content
    assert "concurrency:" in content
    assert "timeout-minutes: 15" in content


def test_translation_number_and_date_validator():
    """Verify that Gemini outputs containing unauthorized dates/numbers are rejected."""
    from automation.rewriter import validate_translation_numbers_and_dates

    source_facts = {
        "title_en": "Solo Leveling Season 2 Episode 13 Announcement",
        "raw_summary": "Episode 13 is scheduled for broadcast on 2025-01-05.",
        "chapter_number": 13,
    }

    # 1. Valid translation with exact matching numbers
    valid_translation = {
        "title_en": "Solo Leveling Season 2 Episode 13 Announcement",
        "title_hi": "सोलो लेवलिंग सीज़न 2 एपिसोड 13 की आधिकारिक घोषणा",
        "summary_en": "Episode 13 broadcast on 2025-01-05.",
        "summary_hi": "एपिसोड 13 का प्रसारण 2025-01-05 को किया जाएगा।",
    }
    is_valid, reason = validate_translation_numbers_and_dates(source_facts, valid_translation)
    assert is_valid is True

    # 2. Hallucinated year (e.g. 2026 instead of 2025)
    invalid_translation = {
        "title_en": "Solo Leveling Season 2 Episode 13 Announcement",
        "title_hi": "सोलो लेवलिंग सीज़न 2 एपिसोड 13 की घोषणा 2026 में",
        "summary_en": "Episode 13 broadcast on 2025-01-05.",
        "summary_hi": "एपिसोड 13 का प्रसारण 2026 में किया जाएगा।",
    }
    is_valid, reason = validate_translation_numbers_and_dates(source_facts, invalid_translation)
    assert is_valid is False
    assert "2026" in reason

    # 3. Hallucinated episode number (e.g. episode 24 when source had 13)
    invalid_ep_translation = {
        "title_en": "Solo Leveling Season 2 Episode 13 Announcement",
        "title_hi": "सोलो लेवलिंग सीज़न 2 एपिसोड 24 की घोषणा",
        "summary_en": "Episode 13 broadcast on 2025-01-05.",
        "summary_hi": "एपिसोड 24 का प्रसारण विवरण।",
    }
    is_valid, reason = validate_translation_numbers_and_dates(source_facts, invalid_ep_translation)
    assert is_valid is False
    assert "24" in reason


def test_pipeline_deduplication_and_publishing_rules(isolate_database_per_test):
    """Verify composite deduplication and strict publishing rules."""
    from automation.saver import is_duplicate_pipeline_item, save_draft
    from sqlmodel import SQLModel
    test_engine = isolate_database_per_test
    SQLModel.metadata.create_all(test_engine)

    # 1. Test composite deduplication
    draft_data = {
        "external_id": "test_rule_ext_1",
        "source_name": "AniList",
        "source_url": "https://anilist.co/anime/99999",
        "anilist_id": 99999,
        "title_en": "Deduplication Test Anime",
        "title_hi": "डुप्लीकेशन टेस्ट एनीमे",
        "summary_en": "This is a comprehensive test summary describing the newly added anime show for deduplication testing in our test suite.",
        "summary_hi": "यह हमारे टेस्ट सुइट में डिडुप्लीकेशन टेस्टिंग के लिए जोड़े गए नए एनीमे शो का विवरण देने वाला एक व्यापक सारांश है।",
        "published_at": datetime(2025, 2, 1, tzinfo=timezone.utc),
        "season_year": 2025,
        "status": "draft",
    }
    success, res_id = save_draft(draft_data, check_near_duplicates=False, engine=test_engine)
    assert success is True

    # Same anilist_id and season_year must be identified as duplicate
    assert is_duplicate_pipeline_item(anilist_id=99999, season_year=2025, engine=test_engine) is True

    # Same normalized title + source_url must be identified as duplicate
    assert is_duplicate_pipeline_item(title_en="Deduplication Test Anime!", source_url="https://anilist.co/anime/99999", engine=test_engine) is True

    # 2. Strict publishing rule: Draft cannot be saved with status='published' if source_url or published_at is missing
    draft_no_pub = {
        "external_id": "test_rule_ext_2",
        "source_name": "AniList",
        "source_url": "https://anilist.co/anime/88888",
        "title_en": "Unpublished Draft Missing Date",
        "summary_en": "A long editorial summary to satisfy word count requirements cleanly for this unit test.",
        "summary_hi": "इस यूनिट टेस्ट के लिए शब्द संख्या आवश्यकताओं को पूरा करने के लिए एक लंबा संपादकीय सारांश।",
        "published_at": None,
        "status": "published",  # Requests published!
    }
    success, draft_id = save_draft(draft_no_pub, check_near_duplicates=False, engine=test_engine)
    assert success is True
    with Session(test_engine) as session:
        saved = session.get(AnimeDraft, draft_id)
        # Forced to needs_review because published_at is missing!
        assert saved.status == "needs_review"

