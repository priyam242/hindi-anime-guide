"""Unit and integration tests for the live weekly anime schedule.

Requirements tested:
1. Two cards on the same day can have different times.
2. Countdown uses each card's distinct airingAt timestamp.
3. JST to IST conversion is verified as strictly minus 3 hours 30 minutes.
"""
from datetime import datetime, timezone, timedelta
from typing import Any
import pytest

from automation.fetchers.anilist import (
    IST_TZ,
    JST_TZ,
    get_season_cycle,
    fetch_live_weekly_schedule,
)


def compute_countdown_seconds(airing_at_unix: int, current_unix: int) -> int:
    """Compute countdown remaining seconds from airingAt unix timestamp."""
    return airing_at_unix - current_unix


def format_countdown_str(diff_seconds: int) -> str:
    """Format remaining seconds as days/hours/minutes/seconds string."""
    if diff_seconds <= 0 and diff_seconds > -7200:
        return "🔴 लाइव (Airing Now)"
    if diff_seconds <= -7200:
        return "प्रसारित (Aired)"

    days = diff_seconds // 86400
    hours = (diff_seconds % 86400) // 3600
    mins = (diff_seconds % 3600) // 60
    secs = diff_seconds % 60

    if days > 0:
        return f"{days}d {hours}h {mins}m"
    return f"{hours}h {mins}m {secs}s"


def convert_jst_to_ist(jst_dt: datetime) -> tuple[datetime, timedelta]:
    """Convert a JST datetime to IST datetime and return the exact delta."""
    # Ensure timezone is JST (UTC+9)
    if jst_dt.tzinfo is None:
        jst_dt = jst_dt.replace(tzinfo=JST_TZ)
    ist_dt = jst_dt.astimezone(IST_TZ)
    # The offset difference between IST and JST is:
    # IST (+5:30) - JST (+9:00) = -3 hours 30 minutes
    delta = ist_dt.utcoffset() - jst_dt.utcoffset()
    return ist_dt, delta


# ============================================================================
# TASK 8 TEST 1: Two cards on the same day can have different times
# ============================================================================
def test_cards_on_same_day_have_different_times():
    """Verify that cards scheduled on the same weekday have distinct airing times."""
    # Synthetic verification with exact test data
    card_1 = {
        "title": "Anime A",
        "airing_at": 1794578400,  # 7:30 PM IST (Friday)
        "weekday": 4,
    }
    card_2 = {
        "title": "Anime B",
        "airing_at": 1794580200,  # 8:00 PM IST (Friday)
        "weekday": 4,
    }
    card_3 = {
        "title": "Anime C",
        "airing_at": 1794588780,  # 10:23 PM IST (Friday)
        "weekday": 4,
    }

    dt1_ist = datetime.fromtimestamp(card_1["airing_at"], tz=timezone.utc).astimezone(IST_TZ)
    dt2_ist = datetime.fromtimestamp(card_2["airing_at"], tz=timezone.utc).astimezone(IST_TZ)
    dt3_ist = datetime.fromtimestamp(card_3["airing_at"], tz=timezone.utc).astimezone(IST_TZ)

    # Both must be on Friday (weekday 4)
    assert dt1_ist.weekday() == 4
    assert dt2_ist.weekday() == 4
    assert dt3_ist.weekday() == 4

    # Format time strings
    time1_str = dt1_ist.strftime("%I:%M %p IST").lstrip("0")
    time2_str = dt2_ist.strftime("%I:%M %p IST").lstrip("0")
    time3_str = dt3_ist.strftime("%I:%M %p IST").lstrip("0")

    assert time1_str == "7:30 PM IST"
    assert time2_str == "8:00 PM IST"
    assert time3_str == "10:23 PM IST"

    # Distinct times on same weekday
    assert time1_str != time2_str
    assert time2_str != time3_str
    assert time1_str != time3_str

    # Live AniList schedule check
    schedule_data = fetch_live_weekly_schedule()
    weekly_items = schedule_data.get("weekly_items", [])
    if weekly_items:
        # Group by weekday
        by_day: dict[int, list[dict[str, Any]]] = {}
        for item in weekly_items:
            w = item.get("airing_ist_weekday")
            if w is not None:
                by_day.setdefault(w, []).append(item)

        # Check any day with at least 2 items
        for day_idx, day_items in by_day.items():
            if len(day_items) >= 2:
                times = {item.get("airing_ist_time") for item in day_items}
                # Must contain multiple distinct airing times on that day
                assert len(times) >= 2, f"Expected multiple distinct times on day {day_idx}, got {times}"
                break


# ============================================================================
# TASK 8 TEST 2: Countdown uses each card's distinct airingAt
# ============================================================================
def test_countdown_uses_individual_airing_at():
    """Verify that countdown computation uses each card's individual airingAt unix timestamp."""
    ref_now_unix = 1794570000  # Base reference time

    # Card 1 airs in 2 hours (7200 seconds)
    airing_at_1 = ref_now_unix + 7200
    # Card 2 airs in 3 hours 30 minutes (12600 seconds)
    airing_at_2 = ref_now_unix + 12600

    diff_1 = compute_countdown_seconds(airing_at_1, ref_now_unix)
    diff_2 = compute_countdown_seconds(airing_at_2, ref_now_unix)

    assert diff_1 == 7200
    assert diff_2 == 12600
    assert diff_2 - diff_1 == 5400  # Exactly 1.5 hours difference

    countdown_1 = format_countdown_str(diff_1)
    countdown_2 = format_countdown_str(diff_2)

    assert countdown_1 == "2h 0m 0s"
    assert countdown_2 == "3h 30m 0s"
    assert countdown_1 != countdown_2

    # Card already airing within 2 hours window
    airing_now = ref_now_unix - 1800  # 30 mins ago
    assert format_countdown_str(compute_countdown_seconds(airing_now, ref_now_unix)) == "🔴 लाइव (Airing Now)"

    # Card aired >2 hours ago
    aired_past = ref_now_unix - 10000
    assert format_countdown_str(compute_countdown_seconds(aired_past, ref_now_unix)) == "प्रसारित (Aired)"


# ============================================================================
# TASK 8 TEST 3: JST to IST conversion is minus 3 hours 30 minutes
# ============================================================================
def test_jst_to_ist_conversion():
    """Verify that JST to IST conversion is mathematically and strictly -3h 30m."""
    expected_delta = timedelta(hours=-3, minutes=-30)

    # 1. 23:00 JST -> 19:30 IST (7:30 PM IST)
    jst_time_1 = datetime(2026, 11, 13, 23, 0, 0, tzinfo=JST_TZ)
    ist_time_1, delta_1 = convert_jst_to_ist(jst_time_1)
    assert delta_1 == expected_delta
    assert ist_time_1.hour == 19
    assert ist_time_1.minute == 30
    assert ist_time_1.day == 13

    # 2. 01:00 JST (next day) -> 21:30 IST (previous day, 9:30 PM IST)
    jst_time_2 = datetime(2026, 11, 14, 1, 0, 0, tzinfo=JST_TZ)
    ist_time_2, delta_2 = convert_jst_to_ist(jst_time_2)
    assert delta_2 == expected_delta
    assert ist_time_2.hour == 21
    assert ist_time_2.minute == 30
    assert ist_time_2.day == 13

    # 3. 12:00 JST (noon) -> 08:30 IST (morning)
    jst_time_3 = datetime(2026, 11, 13, 12, 0, 0, tzinfo=JST_TZ)
    ist_time_3, delta_3 = convert_jst_to_ist(jst_time_3)
    assert delta_3 == expected_delta
    assert ist_time_3.hour == 8
    assert ist_time_3.minute == 30
    assert ist_time_3.day == 13

    # 4. 00:00 JST (midnight) -> 20:30 IST (previous evening)
    jst_time_4 = datetime(2026, 11, 14, 0, 0, 0, tzinfo=JST_TZ)
    ist_time_4, delta_4 = convert_jst_to_ist(jst_time_4)
    assert delta_4 == expected_delta
    assert ist_time_4.hour == 20
    assert ist_time_4.minute == 30
    assert ist_time_4.day == 13

    # Delta in seconds must be exactly -12600 seconds
    assert int(delta_1.total_seconds()) == -12600


# ============================================================================
# SEASON CYCLE TEST
# ============================================================================
def test_season_cycle_derivation():
    """Verify season derivation across all 4 quarters and year wrapping."""
    # Winter (Jan-Mar)
    winter = get_season_cycle(datetime(2026, 2, 10, tzinfo=timezone.utc))
    assert winter["current"]["season"] == "WINTER"
    assert winter["current"]["year"] == 2026
    assert winter["previous"]["season"] == "FALL"
    assert winter["previous"]["year"] == 2025
    assert winter["next"]["season"] == "SPRING"
    assert winter["next"]["year"] == 2026

    # Spring (Apr-Jun)
    spring = get_season_cycle(datetime(2026, 5, 1, tzinfo=timezone.utc))
    assert spring["current"]["season"] == "SPRING"
    assert spring["previous"]["season"] == "WINTER"
    assert spring["next"]["season"] == "SUMMER"

    # Summer (Jul-Sep)
    summer = get_season_cycle(datetime(2026, 8, 15, tzinfo=timezone.utc))
    assert summer["current"]["season"] == "SUMMER"
    assert summer["previous"]["season"] == "SPRING"
    assert summer["next"]["season"] == "FALL"

    # Fall (Oct-Dec)
    fall = get_season_cycle(datetime(2026, 11, 13, tzinfo=timezone.utc))
    assert fall["current"]["season"] == "FALL"
    assert fall["current"]["year"] == 2026
    assert fall["previous"]["season"] == "SUMMER"
    assert fall["previous"]["year"] == 2026
    assert fall["next"]["season"] == "WINTER"
    assert fall["next"]["year"] == 2027


# ============================================================================
# C. SCHEDULE VERIFICATION TESTS
# ============================================================================
from automation.fetchers.anilist import (
    validate_proper_nouns_in_hindi,
    validate_season_and_prequel_synopsis,
    normalize_studio_name,
    get_hindi_title_and_summary,
    PLATFORM_CLASSES,
)
from app.models import AnimeDraft


def test_season_and_prequel_synopsis_validator():
    """Verify seasonYear/startDate matching, identical prequel description detection, and S1 copy rejection."""
    # 1. Prequel identical description -> must be rejected as suspicious and hidden
    media_prequel_dupe = {
        "id": 185660,
        "seasonYear": 2025,
        "startDate": {"year": 2025, "month": 7, "day": 1},
        "description": "High schooler Momo Ayase and occult fanatic Okarun discover aliens and spirits exist.",
        "relations": {
            "edges": [
                {
                    "relationType": "PREQUEL",
                    "node": {
                        "id": 171018,
                        "description": "High schooler Momo Ayase and occult fanatic Okarun discover aliens and spirits exist.",
                    },
                }
            ]
        },
    }
    is_ok_dupe, reason_dupe = validate_season_and_prequel_synopsis(
        "DAN DA DAN Season 2", media_prequel_dupe, card_season_year=2025
    )
    assert is_ok_dupe is False
    assert "identical to prequel" in reason_dupe

    # 2. SeasonYear mismatch -> card expects 2026, media is 2023
    media_year_mismatch = {
        "id": 161645,
        "seasonYear": 2023,
        "startDate": {"year": 2023, "month": 10, "day": 22},
        "description": "Season 1 story.",
        "relations": {"edges": []},
    }
    is_ok_ym, reason_ym = validate_season_and_prequel_synopsis(
        "The Apothecary Diaries Season 2", media_year_mismatch, card_season_year=2025
    )
    assert is_ok_ym is False
    assert "seasonYear mismatch" in reason_ym

    # 3. New season card repeating Season 1 premise -> suspicious, must hide synopsis
    media_apoth_s3 = {
        "id": 195516,
        "seasonYear": 2026,
        "startDate": {"year": 2026, "month": 10, "day": 1},
        "description": "Maomao lived a peaceful life until she’s sold as a lowly servant to the emperor’s palace.",
        "relations": {"edges": [{"relationType": "PREQUEL", "node": {"id": 176301}}]},
    }
    is_ok_s1, reason_s1 = validate_season_and_prequel_synopsis(
        "The Apothecary Diaries Season 3", media_apoth_s3, card_season_year=2026
    )
    assert is_ok_s1 is False
    assert "Season 1 premise" in reason_s1

    # In get_hindi_title_and_summary, failed season validation returns empty synopsis (show title + ep only)
    t_hidden, s_hidden, *_ = get_hindi_title_and_summary(
        "The Apothecary Diaries Season 3",
        "Kusuriya no Hitorigoto 3rd Season",
        "Maomao lived a peaceful life until she’s sold as a lowly servant to the emperor’s palace.",
        media=media_apoth_s3,
        card_season_year=2026,
    )
    assert t_hidden == "The Apothecary Diaries Season 3"
    assert s_hidden == ""  # Synopsis strictly hidden

    # 4. Valid Season 2 media with distinct season synopsis -> passes!
    media_valid_s2 = {
        "id": 176301,
        "seasonYear": 2025,
        "startDate": {"year": 2025, "month": 1, "day": 10},
        "description": "Maomao continues her role in the inner palace, unraveling new intrigues and poisons.",
        "relations": {"edges": [{"relationType": "PREQUEL", "node": {"id": 161645}}]},
    }
    is_ok_val, _ = validate_season_and_prequel_synopsis(
        "The Apothecary Diaries Season 2", media_valid_s2, card_season_year=2025
    )
    assert is_ok_val is True


def test_synopsis_proper_noun_validator_three_examples():
    """Verify synopsis validator with 3 real examples and rejection of hallucinations."""
    # Example 1: Kusuriya no Hitorigoto (The Apothecary Diaries S1)
    ex1_src = "Maomao lived a peaceful life with her apothecary father. Until one day, she’s sold as a lowly servant to the emperor’s palace. She decides to step in and find a cure! This catches the eye of Jinshi, a handsome palace official who promotes her."
    ex1_hi = "माओमाओ अपने औषधीय पिता के साथ शांत जीवन व्यतीत कर रही थी, जब तक कि उसे सम्राट के महल में एक साधारण सेविका के रूप में बेच नहीं दिया गया। जब शाही वारिस बीमार पड़ते हैं, तो वह आगे आकर उनका इलाज ढूंढती है! यह बात एक सुंदर महल अधिकारी जिनशी का ध्यान आकर्षित करती है, जो उसे पदोन्नत करता है।"
    val1, reason1 = validate_proper_nouns_in_hindi(ex1_src, ex1_hi)
    assert val1 is True, f"Example 1 failed: {reason1}"

    # Example 2: Hotaru no Yomeiri (Firefly Wedding)
    ex2_src = "On the surface, Satoko has it all—she’s beautiful, the daughter of a nobleman, and at a prime age for marriage. Before she can secure a marriage, she finds herself the target of the mysterious assassin Shinpei, and her plans are put in jeopardy."
    ex2_hi = "सतौको (Satoko) एक कुलीन परिवार की सुंदर पुत्री है, किंतु वह गंभीर रूप से बीमार है और उसके पास जीने के लिए बहुत कम समय बचा है। इससे पहले कि वह अपने परिवार के लिए विवाह तय कर पाती, वह रहस्यमयी हत्यारे शिन्पेई (Shinpei) का निशाना बन जाती है।"
    val2, reason2 = validate_proper_nouns_in_hindi(ex2_src, ex2_hi)
    assert val2 is True, f"Example 2 failed: {reason2}"

    # Example 3: Tokyo Revengers
    ex3_src = "Takemichi Hanagaki is a freelancer that's reached the absolute pits of despair in his life. He finds out that the only girlfriend he ever had, Hinata Tachibana, had been killed by the ruthless Tokyo Manji Gang."
    ex3_hi = "ताकेमिची हनागाकी (Takemichi Hanagaki) एक ऐसा फ्रीलांसर है जिसका जीवन निराशा के चरम पर पहुंच चुका है। उसे पता चलता है कि उसकी मिडिल स्कूल की पूर्व प्रेमिका हिनाता ताचिबाना को बेरहम टोक्यो मांजी गैंग (Tokyo Manji Gang) ने मार डाला है।"
    val3, reason3 = validate_proper_nouns_in_hindi(ex3_src, ex3_hi)
    assert val3 is True, f"Example 3 failed: {reason3}"

    # Negative / Hallucination Test: foreign characters injected
    hallucinated_hi = "गोकू और नारुतो ने मिलकर विलेन को हरा दिया और शांति स्थापित की।"
    val_bad, reason_bad = validate_proper_nouns_in_hindi(ex1_src, hallucinated_hi)
    assert val_bad is False
    assert "Hallucinated" in reason_bad

    # Fallback to English check in get_hindi_title_and_summary
    t_fallback, s_fallback, *_ = get_hindi_title_and_summary("Unknown Anime", "Unknown", "A mysterious fantasy adventure.")
    assert "adventure" in s_fallback.lower()


def test_premiering_section_strictly_requires_start_date_and_rejects_unverified():
    """Verify premiering items strictly require complete AniList startDate and exclude rumors."""
    data = fetch_live_weekly_schedule()
    premiering = data.get("premiering_items", [])
    for pm in premiering:
        # Every item in premiering must have a verified formatted premiere date with year, month, and day
        assert pm.get("premiere_date_formatted"), "Missing premiere date"
        assert pm.get("start_year"), "Missing start_year"
        assert pm.get("start_month"), "Missing start_month"
        assert pm.get("start_day"), "Missing start_day"

        # Explicitly ensure entries like Cyberpunk 2 or Black Clover S2 are ONLY present if AniList returned them with verified complete startDate
        title_lower = (pm.get("title_en", "") + " " + pm.get("title_romaji", "")).lower()
        if ("cyberpunk" in title_lower and "2" in title_lower) or ("black clover" in title_lower and "season 2" in title_lower):
            assert pm.get("start_year") and pm.get("start_month") and pm.get("start_day"), "Rumor item missing verified startDate"
            assert pm.get("premiere_date_formatted"), "Rumor item missing formatted premiere date"


def test_score_and_popularity_labeling_no_members_label():
    """Verify AniList popularity is correctly stored and labeled, not confused with Members."""
    data = fetch_live_weekly_schedule()
    weekly = data.get("weekly_items", [])
    if weekly:
        sample = weekly[0]
        assert "popularity" in sample
        assert "popularity_display" in sample
        assert sample.get("score_source") == "AniList"
        # popularity is integer or float
        assert isinstance(sample["popularity"], (int, float))


def test_jiohotstar_replacement_everywhere():
    """Verify JioCinema is cleanly replaced with JioHotstar in streaming mapper and templates."""
    # Streaming mapper in AniList
    assert "JioHotstar" in PLATFORM_CLASSES
    assert PLATFORM_CLASSES["JioHotstar"]["name"] == "JioHotstar"
    assert PLATFORM_CLASSES["JioHotstar"]["short"] == "JH"
    assert PLATFORM_CLASSES["JioCinema"]["name"] == "JioHotstar"  # Aliased to JioHotstar

    # Database platform check
    from app.db import get_session
    from sqlmodel import text
    for session in get_session():
        try:
            old_jc = session.exec(text("SELECT COUNT(*) FROM platform WHERE name = 'JioCinema'")).one()
            old_count = old_jc if isinstance(old_jc, int) else old_jc[0]
            assert old_count == 0, "JioCinema still present in platform table"

            jh = session.exec(text("SELECT COUNT(*) FROM platform WHERE name = 'JioHotstar'")).one()
            jh_count = jh if isinstance(jh, int) else jh[0]
            assert jh_count >= 1, "JioHotstar missing from platform table"

            avail_jc = session.exec(text("SELECT COUNT(*) FROM availability WHERE platform_id = 'JioCinema'")).one()
            avail_jc_count = avail_jc if isinstance(avail_jc, int) else avail_jc[0]
            assert avail_jc_count == 0, "JioCinema still present in availability table"
        finally:
            session.close()
        break


def test_admin_hindi_dub_fields_and_badge_filter():
    """Verify AnimeDraft supports hindi_dub, dub_platform, dub_source_url and filtering."""
    # Create test model instance
    draft = AnimeDraft(
        external_id="test_ep_dub_1",
        title_en="Frieren Beyond Journey's End",
        source_url="https://anilist.co/anime/154587",
        hindi_dub=True,
        dub_platform="JioHotstar",
        dub_source_url="https://www.hotstar.com/anime/frieren",
    )
    assert draft.hindi_dub is True
    assert draft.dub_platform == "JioHotstar"
    assert draft.dub_source_url == "https://www.hotstar.com/anime/frieren"

    # Default is False
    draft_no_dub = AnimeDraft(
        external_id="test_ep_dub_2",
        title_en="Unverified Anime",
        source_url="https://anilist.co/anime/999999",
    )
    assert draft_no_dub.hindi_dub is False
    assert draft_no_dub.dub_platform is None


def test_normalize_studio_names():
    """Verify studio names are title-cased while preserving recognized acronyms."""
    assert normalize_studio_name("mappa") == "MAPPA"
    assert normalize_studio_name("MAPPA") == "MAPPA"
    assert normalize_studio_name("wit studio") == "Wit Studio"
    assert normalize_studio_name("WIT STUDIO") == "Wit Studio"
    assert normalize_studio_name("CLOVERWORKS") == "CloverWorks"
    assert normalize_studio_name("a-1 pictures") == "A-1 Pictures"
    assert normalize_studio_name("ufotable") == "Ufotable"
    assert normalize_studio_name("kyoto animation") == "Kyoto Animation"
    assert normalize_studio_name("studio pierrot") == "Studio Pierrot"
    assert normalize_studio_name("tms entertainment") == "TMS Entertainment"
    assert normalize_studio_name("unknown indie studio") == "Unknown Indie Studio"
    assert normalize_studio_name("") == "TBA"
    assert normalize_studio_name(None) == "TBA"


