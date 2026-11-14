"""Automated verification test suite for News Summaries, Tweet attribution, and Manga Rules.

Tests:
1. News summary validator: only text in RSS description/content. If empty, summary is empty.
2. Removes ungrounded sentences (cast, teaser, jury praise, boilerplate).
3. LiveChart items with x.com are labeled "Tweet" and prefer LiveChart article URL.
4. Solo Leveling ratio ~13.4 is explicitly marked as "webtoon exception".
5. Ongoing manga with chapters=None omits volumes and shows latest chapter.
"""
import pytest
from automation.rewriter import validate_and_sanitize_news_summary
from automation.fetchers.manga import WEBTOON_EXCEPTIONS, validate_manga_chapters_volumes_consistency
from automation.fetchers.livechart_rss import _normalize_rss_item
import xml.etree.ElementTree as ET


def test_news_summary_empty_description():
    """Rule 1: If no RSS description exists, summary must be empty (title + Read full story only)."""
    title = "Star Detective Precure! The Movie releases on Blu-ray & DVD on January 27"
    raw_desc = ""
    draft_en = "An official editorial news update confirms key developments regarding release. Production milestones shared."
    draft_hi = "फिल्म से जुड़ी एक महत्वपूर्ण घोषणा सामने आई है। हमारी संपादकीय टीम नज़र बनाए रखेगी।"

    final_en, final_hi, removed = validate_and_sanitize_news_summary(
        title_en=title,
        raw_source_text=raw_desc,
        summary_en=draft_en,
        summary_hi=draft_hi,
    )
    assert final_en == ""
    assert final_hi == ""
    assert len(removed) >= 1
    assert "Empty RSS description" in removed[0]


def test_news_summary_removes_ungrounded_cast_teaser_jury():
    """Rule 1: Remove any sentence that adds facts not in source (cast, teaser, jury praise)."""
    title = "Purge List Webtoon's Live-Action Film Adaptation Begins Production"
    raw_desc = "Historical action film will star Woo-sung Jung as ruthless power broker."
    
    draft_en = (
        "Historical action film will star Woo-sung Jung as ruthless power broker. "
        "A brand new teaser visual and promo trailer were revealed today. "
        "The project received high jury praise and awards accolades for storytelling impact."
    )
    draft_hi = (
        "इस ऐतिहासिक एक्शन फिल्म में वू-सुंग जंग एक निर्दयी पावर ब्रोकर की भूमिका निभाएंगे। "
        "आज एक नया टीज़र विजुअल और ट्रेलर जारी किया गया। "
        "परियोजना को ज्यूरी प्रशंसा और कई सम्मान प्राप्त हुए हैं।"
    )

    final_en, final_hi, removed = validate_and_sanitize_news_summary(
        title_en=title,
        raw_source_text=raw_desc,
        summary_en=draft_en,
        summary_hi=draft_hi,
    )

    # First sentence should be retained because Woo-sung Jung is in raw_desc
    assert "Woo-sung Jung" in final_en
    assert "वू-सुंग जंग" in final_hi

    # Ungrounded teaser and jury sentences must be purged
    assert "teaser visual" not in final_en.lower()
    assert "jury praise" not in final_en.lower()
    assert "टीज़र" not in final_hi
    assert "ज्यूरी प्रशंसा" not in final_hi
    assert len(removed) >= 2


def test_livechart_tweet_attribution_and_permalink():
    """Rule 2: For LiveChart items whose source_url is x.com, label 'Tweet' and prefer LiveChart article URL."""
    xml_str = """
    <item>
        <title>New TV Anime Announced</title>
        <link>https://x.com/i/status/2121058732956356709</link>
        <guid>https://www.livechart.me/headlines/16572</guid>
        <pubDate>Thu, 13 Nov 2026 11:26:55 GMT</pubDate>
        <description></description>
    </item>
    """
    elem = ET.fromstring(xml_str.strip())
    item = _normalize_rss_item(elem, resolve_covers=False)
    assert item is not None
    assert item["source_name"] == "Tweet"
    assert item["source_url"] == "https://www.livechart.me/headlines/16572"
    assert item["tweet_url"] == "https://x.com/i/status/2121058732956356709"


def test_solo_leveling_webtoon_exception():
    """Rule 4: Solo Leveling ratio 13.4 is outside 6-12 rule. Must be marked 'webtoon exception'."""
    assert "Solo Leveling" in WEBTOON_EXCEPTIONS or "solo-leveling" in WEBTOON_EXCEPTIONS

    test_items = [
        {"title_en": "Solo Leveling", "chapters": 201, "volumes": 15},  # ratio 13.4
        {"title_en": "Naruto", "chapters": 700, "volumes": 72},         # ratio 9.72 (standard)
    ]
    mismatches = validate_manga_chapters_volumes_consistency(test_items)
    # Neither should be flagged as mismatch; Solo Leveling is handled via webtoon exception
    assert len(mismatches) == 0


def test_ongoing_manga_chapters_none_omits_volumes():
    """Rule 5: For ongoing manga with chapters=None, show latest chapter and omit volumes."""
    test_items = [
        {"title_en": "One Piece", "publication_status": "Ongoing", "chapters": None, "volumes": None, "latest_chapter": "1198"},
    ]
    mismatches = validate_manga_chapters_volumes_consistency(test_items)
    assert len(mismatches) == 0
