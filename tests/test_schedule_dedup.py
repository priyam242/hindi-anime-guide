import re
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app

DAY_NAMES_HI = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]

def clean_base_series_title(title: str) -> str:
    if not title:
        return ""
    pattern = r"\s*(?:[-–—:]?\s*)?(?:Episode|Ep\.?|एपिसोड|Part|भाग)\s*\d+.*$"
    cleaned = re.sub(pattern, "", title, flags=re.IGNORECASE).strip()
    return cleaned if cleaned else title


def test_schedule_zero_duplicate_series_in_dataset():
    """Verify that schedule_json has 0 duplicate series titles across all weekly cards."""
    client = TestClient(app)
    response = client.get("/schedule")
    assert response.status_code == 200

    html = response.text
    # Extract embedded JSON data
    m = re.search(r'<script id="schedule-raw" type="application/json">\s*([\s\S]*?)\s*</script>', html)
    assert m is not None, "schedule-raw script element not found in HTML"

    cards = json.loads(m.group(1))
    assert len(cards) > 0, "No cards loaded in schedule dataset"

    seen_series = set()
    duplicates = []

    for card in cards:
        t = card.get("t", "")
        base_t = re.sub(r'[^a-zA-Z0-9\u0900-\u097f]', '', clean_base_series_title(t).lower())
        if not base_t:
            continue
        if base_t in seen_series:
            duplicates.append((card.get("id"), t))
        seen_series.add(base_t)

    assert not duplicates, f"Found duplicate series cards in schedule: {duplicates}"


def test_schedule_weekday_tabs_strict_label_alignment():
    """Verify across all 7 weekday tabs (?day=0..6) that every card matches the weekday label."""
    client = TestClient(app)

    for day_idx in range(7):
        expected_day_name = DAY_NAMES_HI[day_idx]
        response = client.get(f"/schedule?day={day_idx}")
        assert response.status_code == 200

        html = response.text
        # Extract embedded JSON dataset
        m = re.search(r'<script id="schedule-raw" type="application/json">\s*([\s\S]*?)\s*</script>', html)
        assert m is not None, "schedule-raw script element not found"

        cards = json.loads(m.group(1))
        day_cards = [c for c in cards if c.get("w") == day_idx]

        for c in day_cards:
            assert c.get("d") == expected_day_name, (
                f"Card ID {c.get('id')} in day={day_idx} tab has mismatch: "
                f"c['d']='{c.get('d')}', expected='{expected_day_name}'"
            )
            # Ensure tm contains valid IST timestamp
            assert "IST" in c.get("tm", ""), f"Card ID {c.get('id')} does not have IST timestamp: {c.get('tm')}"
            assert "JST" not in c.get("tm", ""), f"Card ID {c.get('id')} has forbidden JST timestamp: {c.get('tm')}"


def test_schedule_no_episode_text_cluttering_titles():
    """Verify that zero cards have 'Episode \\d+' or 'एपिसोड \\d+' cluttering the title when Ep badge exists."""
    client = TestClient(app)
    response = client.get("/schedule")
    assert response.status_code == 200

    m = re.search(r'<script id="schedule-raw" type="application/json">\s*([\s\S]*?)\s*</script>', html := response.text)
    assert m is not None
    cards = json.loads(m.group(1))

    episode_pattern = re.compile(r'\b(?:Episode|Ep\.?|एपिसोड)\s*\d+', re.IGNORECASE)

    violations = []
    for c in cards:
        t = c.get("t", "")
        th = c.get("th", "")
        if episode_pattern.search(t):
            violations.append((c.get("id"), "title", t))
        if episode_pattern.search(th):
            violations.append((c.get("id"), "subtitle", th))

    assert not violations, f"Cards have redundant episode text in title/subtitle: {violations}"


def test_schedule_updates_legacy_id_remains_accessible():
    """Verify /updates/153 and other numeric DB IDs return HTTP 200 OK (no 404 SEO breakage)."""
    client = TestClient(app)
    for test_id in [152, 153, 154, 155, 156, 157]:
        resp = client.get(f"/updates/{test_id}")
        assert resp.status_code == 200, f"/updates/{test_id} failed with HTTP {resp.status_code}"
