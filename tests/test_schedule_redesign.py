import pytest
from fastapi.testclient import TestClient
from app.main import app
from automation.fetchers.anilist import clean_description, fetch_live_weekly_schedule


def test_clean_description_discards_placeholders():
    assert clean_description("Pan no Akachan (TV) schedule update.") == ""
    assert clean_description("Official TV schedule update for upcoming episodes.") == ""
    assert clean_description("No synopsis has been added to this title.") == ""
    assert clean_description("  ") == ""
    real_desc = "Kudo Shinichi is a high school detective who is turned into a child by a mysterious organization."
    assert clean_description(real_desc) == real_desc


def test_schedule_page_html_size_under_300kb():
    client = TestClient(app)
    res = client.get("/schedule")
    assert res.status_code == 200
    size_bytes = len(res.content)
    size_kb = size_bytes / 1024
    print(f"Schedule page rendered size: {size_kb:.2f} KB")
    # Must be under 300 KB
    assert size_kb < 300.0, f"HTML size {size_kb:.2f} KB exceeds 300 KB budget"


def test_schedule_cards_sorted_by_popularity():
    schedule = fetch_live_weekly_schedule()
    weekly_items = schedule.get("weekly_items", [])
    assert len(weekly_items) > 0

    # Test Saturday items (weekday 5)
    sat_items = [it for it in weekly_items if it.get("airing_ist_weekday") == 5]
    assert len(sat_items) > 0

    # Ensure popularity is monotonically descending
    pops = [it.get("popularity") or 0 for it in sat_items]
    assert pops == sorted(pops, reverse=True), "Saturday items must be sorted by popularity descending"


def test_no_schedule_cards_contain_placeholder_description():
    schedule = fetch_live_weekly_schedule()
    weekly_items = schedule.get("weekly_items", [])
    
    for it in weekly_items:
        s_en = (it.get("summary_en") or "").lower()
        s_hi = (it.get("summary_hi") or "").lower()
        assert "schedule update" not in s_en, f"Placeholder found in summary_en for {it.get('title_en')}"
        assert "schedule update" not in s_hi, f"Placeholder found in summary_hi for {it.get('title_en')}"


def test_mal_defaults_adult_and_shorts_filtered():
    schedule = fetch_live_weekly_schedule()
    weekly_items = schedule.get("weekly_items", [])

    for it in weekly_items:
        # Adult shows must be 100% excluded
        assert not it.get("is_adult"), f"Adult show {it.get('title_en')} should not be in schedule"
        genres = [g.lower() for g in it.get("genres") or []]
        assert "hentai" not in genres, f"Hentai genre found in {it.get('title_en')}"
