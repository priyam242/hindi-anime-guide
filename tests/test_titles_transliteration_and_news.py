import json
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.db import engine
from app.models import AwardEntry, AnimeDraft
from app.transliteration import (
    BLACKLIST_TRANSLATION_WORDS,
    REVIEWED_TRANSLITERATION_MAP,
    validate_transliteration,
    transliterate_anime_title,
)
from automation.rewriter import (
    bold_entities,
    bold_entities_hi,
    process_news_bilingual,
)

client = TestClient(app)


def test_blacklist_rejects_literal_machine_translations():
    """Unit test verifying blacklist catches and rejects forbidden literal translations."""
    forbidden_samples = [
        ("One Piece", "एक टुकड़ा"),
        ("SPY x FAMILY", "जासूस एक्स परिवार"),
        ("SPY x FAMILY", "ज़ासूस परिवार"),
        ("Chainsaw Man", "चेनसॉ आदमी"),
        ("The Colors Within", "भीतर के रंग"),
        ("Oshi no Ko", "ओशी नहीं को"),
        ("Demon Slayer", "दानव हत्यारा"),
        ("Demon Slayer", "दानव कातिल"),
        ("Black Clover", "काला तिपतिया घास"),
        ("Look Back", "पीछे देखना"),
        ("Delicious in Dungeon", "कालकोठरी में स्वादिष्ट"),
        ("The Summer Hikaru Died", "ग्रीष्म हिकारू की मृत्यु हो गई"),
        ("VIZ Media", "अशांत मीडिया"),
        ("TV Tokyo", "कृपया मुझे बताएं"),
        ("Solo Leveling", "अकेला स्तर"),
        ("Attack on Titan", "आक्रमण पर टाइटन"),
        ("Death Note", "मौत का नोट"),
        ("The Boy and the Heron", "लड़का और बगुला"),
    ]

    for title_en, bad_hi in forbidden_samples:
        is_ok, reason = validate_transliteration(title_en, bad_hi)
        assert not is_ok, f"Validator should have rejected bad translation '{bad_hi}' for '{title_en}'"
        assert any(bw in bad_hi for bw in BLACKLIST_TRANSLATION_WORDS)


def test_syllable_shape_and_canonical_transliterations():
    """Verify official English/romaji titles produce pure phonetic transliterations."""
    test_cases = [
        ("One Piece", "वन पीस"),
        ("Oshi no Ko", "ओशी नो को"),
        ("SPY x FAMILY", "स्पाई x फैमिली"),
        ("Chainsaw Man", "चेनसॉ मैन"),
        ("The Colors Within", "द कलर्स विदिन"),
        ("The Summer Hikaru Died", "द समर हिकारू डाइड"),
        ("Delicious in Dungeon", "डिलीशियस इन डंजन"),
        ("Black Clover", "ब्लैक क्लोवर"),
        ("Demon Slayer", "डेमन स्लेयर"),
        ("Takopi's Original Sin", "ताकोपीज़ ओरिजिनल सिन"),
        ("Suzume", "सुज़ुमे"),
        ("Bocchi the Rock!", "बोची द रॉक!"),
        ("JUJUTSU KAISEN", "जुजुत्सु काइसेन"),
        ("Solo Leveling", "सोलो लेवलिंग"),
        ("LOOK BACK", "लुक बैक"),
        ("Frieren: Beyond Journey's End", "फ़्रीरेन: बियॉन्ड जर्नीज़ एंड"),
        ("Demon Slayer: Kimetsu no Yaiba", "डेमन स्लेयर: किमेत्सु नो याइबा"),
        ("My Hero Academia", "माई हीरो एकेडेमिया"),
        ("THE FIRST SLAM DUNK", "द फर्स्ट स्लैम डंक"),
        ("Cyberpunk: Edgerunners", "साइबरपंक: एजरनर्स"),
        ("Dandadan", "दांदादान"),
        ("Sakamoto Days", "साकामोटो डेज़"),
        ("Blue Lock", "ब्लू लॉक"),
        ("Attack on Titan", "अटैक ऑन टाइटन"),
        ("Naruto", "नारुतो"),
        ("Death Note", "डेथ नोट"),
        ("Kaiju No. 8", "काइजू नंबर 8"),
        ("Haikyu!!", "हाइक्यू!!"),
    ]

    for title_en, expected_hi in test_cases:
        actual_hi = transliterate_anime_title(title_en)
        assert actual_hi == expected_hi, f"Mismatch for '{title_en}': expected '{expected_hi}', got '{actual_hi}'"
        is_ok, reason = validate_transliteration(title_en, actual_hi)
        assert is_ok, f"Validation failed for '{actual_hi}': {reason}"


def test_database_awards_titles_clean_transliteration():
    """Verify all awards entries pass the blacklist and use clean transliteration."""
    from automation.seed_awards import VERIFIED_DATA

    total_entries = 0
    for block in VERIFIED_DATA:
        for entry in block["entries"]:
            total_entries += 1
            winner_en = entry["winner_title"]
            winner_hi = entry["winner_title_hi"]
            assert winner_hi, f"Award entry ({winner_en}) missing winner_title_hi"

            # Must pass transliteration validator
            is_ok, reason = validate_transliteration(winner_en, winner_hi)
            assert is_ok, f"Award entry ({winner_en}) failed transliteration validation: {reason}"

            # Must not contain ugly English bracket tags like (LOOK BACK) or (Solo Leveling)
            assert "(LOOK BACK)" not in winner_hi
            assert "(Solo Leveling)" not in winner_hi
            assert "कीओशी ओशियामा" not in winner_hi

    assert total_entries >= 20


def test_news_bilingual_processing_with_bold_bullets():
    """Verify narrative RSS news items generate bold bullets and short items do not."""
    # 1. Narrative source (>= 20 words)
    narrative_facts = {
        "title_en": "Gushing Over Magical Girls Season 2 Anime Confirms 2027 Broadcast in New Visual: Latest News and Official Announcement",
        "source_name": "Crunchyroll News",
        "source_url": "https://www.crunchyroll.com/news/123",
        "raw_summary": "To commemorate the birthday of protagonist Magia Baiser on November 14, the official website for the Gushing Over Magical Girls anime today unveiled an ultra teaser visual and a 2027 broadcast date for Season 2. Atsushi Otsuki returns to direct at studio Asahi Production. The series will stream on Crunchyroll in 2027.",
    }
    res_narrative = process_news_bilingual(narrative_facts, api_key="")
    # Check title suffix stripped
    assert ": Latest News and Official Announcement" not in res_narrative["title_en"]
    assert "Gushing Over Magical Girls" in res_narrative["title_en"]
    # Check bullets generated
    assert res_narrative["key_points_en"] is not None
    bullets_en = json.loads(res_narrative["key_points_en"])
    assert len(bullets_en) >= 1
    # Check bold formatting in bullets
    assert any("**" in b for b in bullets_en), "Bullets must bold key names, dates, or numbers"
    assert "Read full story via official link" in res_narrative["summary_en"]

    # 2. Short source (< 20 words)
    short_facts = {
        "title_en": "Japanese Animation TV Ranking, November 2-8",
        "source_name": "Anime News Network",
        "source_url": "https://www.animenewsnetwork.com/ranking",
        "raw_summary": "The Apothecary Diaries season 3 earned 3.5% rating last week.",
    }
    res_short = process_news_bilingual(short_facts, api_key="")
    assert res_short["key_points_en"] is None, "Short text must not have bullets"
    assert res_short["key_points_hi"] is None
    assert "Anime News Network" in res_short["summary_en"]


def test_news_page_and_home_updates_bilingual_toggle():
    """Verify /news and / routes render bilingual toggle controls and return 200 OK."""
    # Test /news
    r_news = client.get("/news")
    assert r_news.status_code == 200
    assert "news-lang-hi-btn" in r_news.text
    assert "news-lang-en-btn" in r_news.text
    assert "setNewsPageLang" in r_news.text

    # Test / (Home)
    r_home = client.get("/")
    assert r_home.status_code == 200
    assert "home-upd-hi-btn" in r_home.text
    assert "home-upd-en-btn" in r_home.text
    assert "setHomeUpdatesLang" in r_home.text


def test_update_detail_bilingual_bullets_and_source_link():
    """Verify update detail page renders key points and 'Read full story' link."""
    # Create an isolated test news draft in the session
    import app.db as app_db
    with Session(app_db.engine) as session:
        draft = AnimeDraft(
            content_type="news",
            status="published",
            source_name="Crunchyroll News",
            source_url="https://www.crunchyroll.com/news/test-123",
            external_id="test_bilingual_news_999",
            title_en="Gushing Over Magical Girls Season 2 Confirms 2027 Broadcast",
            title_hi="गशिंग ओवर मैजिकल गर्ल्स सीज़न 2",
            summary_en="To commemorate the birthday, official announcement unveiled broadcast date. (Source: Crunchyroll News. Read full story via official link.)",
            summary_hi="जन्मदिन के उपलक्ष्य में आधिकारिक घोषणा की पुष्टि। (स्रोत: Crunchyroll News। पूर्ण विवरण के लिए मूल लेख पढ़ें।)",
            key_points_en=json.dumps(["The series streams on **Crunchyroll** in **2027**."]),
            key_points_hi=json.dumps(["यह श्रृंखला **2027** में **क्रंचीरोल** पर स्ट्रीम होगी।"]),
        )
        session.add(draft)
        session.commit()
        session.refresh(draft)
        draft_id = draft.id

    r_det = client.get(f"/updates/{draft_id}")
    assert r_det.status_code == 200
    assert "Read full story" in r_det.text
    assert "detail-headline" in r_det.text
    assert "toggleDetailLang" in r_det.text
    assert "bullets-hi-list" in r_det.text
    assert "bullets-en-list" in r_det.text
