import json
import re
from datetime import date, datetime, timezone, timedelta
from typing import Optional, Union
from fastapi.templating import Jinja2Templates
from app.config import get_settings, BASE_DIR

IST = timezone(timedelta(hours=5, minutes=30))

HINDI_MONTHS = {
    1: "जनवरी", 2: "फ़रवरी", 3: "मार्च", 4: "अप्रैल",
    5: "मई", 6: "जून", 7: "जुलाई", 8: "अगस्त",
    9: "सितम्बर", 10: "अक्टूबर", 11: "नवम्बर", 12: "दिसम्बर"
}


def hindi_date(dt: Optional[Union[date, datetime, str]]) -> str:
    """
    Formats a date or datetime object into Hindi string like '18 अक्टूबर 2026'.
    If given a datetime, treats naive values as UTC, converts to IST (UTC+05:30), then formats.
    If given a plain date (e.g. last_checked), does NOT shift timezone.
    """
    if not dt:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return dt
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(IST)
    elif isinstance(dt, date):
        pass
    month_hi = HINDI_MONTHS.get(dt.month, str(dt.month))
    return f"{dt.day} {month_hi} {dt.year}"


def short_description(text: str, limit: int = 155) -> str:
    """
    Cuts at a word boundary (never in the middle of a Devanagari word/conjunct)
    and strips the '[नमूना]' prefix.
    """
    if not text:
        return ""
    
    cleaned = text.replace("[नमूना]", "").replace("**", "").strip()
    cleaned = re.sub(r'^\s+', '', cleaned)

    if len(cleaned) <= limit:
        return cleaned

    # Truncate to limit and find last word boundary
    sliced = cleaned[:limit]
    last_space = sliced.rfind(" ")
    if last_space > 0:
        sliced = sliced[:last_space].rstrip(" ,.;:!?।-")

    return sliced


def first_words(text: str, count: int = 2) -> str:
    """Returns approximately the first `count` words of the given text."""
    if not text:
        return ""
    words = text.strip().split()
    return " ".join(words[:count])


def extract_episode(val: Optional[str]) -> Optional[int]:
    """Extract episode or chapter number from external_id, title, or summary string."""
    if not val:
        return None
    # Check chapter patterns first (e.g., Chapter 1130, Ch. 182, चैप्टर 186)
    m_ch = re.search(r'(?:Chapter|Ch\.|चैप्टर)\s*(\d+)', val, re.IGNORECASE)
    if m_ch:
        return int(m_ch.group(1))
    m = re.search(r'_ep(\d+)', val)
    if m:
        return int(m.group(1))
    m2 = re.search(r'Episode\s+(\d+)', val, re.IGNORECASE)
    if m2:
        return int(m2.group(1))
    m3 = re.search(r'एपिसोड\s+(\d+)', val)
    if m3:
        return int(m3.group(1))
    return None


def format_airing_ist(val: Any) -> str:
    """Format UTC datetime, timestamp, or natural broadcast text (e.g. 'Sundays at 23:15 JST') into readable IST string."""
    if not val:
        return ""
    if isinstance(val, (int, float)):
        dt = datetime.fromtimestamp(val, tz=timezone.utc)
        ist_dt = dt.astimezone(IST)
        month_hi = HINDI_MONTHS.get(ist_dt.month, str(ist_dt.month))
        time_str = ist_dt.strftime("%I:%M %p")
        return f"{ist_dt.day} {month_hi} {ist_dt.year}, {time_str} IST"

    if isinstance(val, datetime):
        dt = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        ist_dt = dt.astimezone(IST)
        month_hi = HINDI_MONTHS.get(ist_dt.month, str(ist_dt.month))
        time_str = ist_dt.strftime("%I:%M %p")
        return f"{ist_dt.day} {month_hi} {ist_dt.year}, {time_str} IST"

    if isinstance(val, str):
        # Look for YYYY-MM-DD HH:MM
        match = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', val)
        if match:
            date_p, time_p = match.groups()
            try:
                dt = datetime.strptime(f"{date_p} {time_p}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                ist_dt = dt.astimezone(IST)
                month_hi = HINDI_MONTHS.get(ist_dt.month, str(ist_dt.month))
                time_str = ist_dt.strftime("%I:%M %p")
                return f"{ist_dt.day} {month_hi} {ist_dt.year}, {time_str} IST"
            except Exception:
                pass

        # Try ISO format
        try:
            dt = datetime.fromisoformat(val)
            ist_dt = dt.astimezone(IST)
            month_hi = HINDI_MONTHS.get(ist_dt.month, str(ist_dt.month))
            time_str = ist_dt.strftime("%I:%M %p")
            return f"{ist_dt.day} {month_hi} {ist_dt.year}, {time_str} IST"
        except Exception:
            pass

        # Check for natural broadcast string like "Sundays at 23:15 JST" or "23:15 JST"
        jst_match = re.search(r'(\d{1,2}):(\d{2})\s*(?:JST)', val, re.IGNORECASE)
        if jst_match:
            h, m = int(jst_match.group(1)), int(jst_match.group(2))
            total_minutes = h * 60 + m - (3 * 60 + 30)  # Convert JST to IST (-3h 30m)
            if total_minutes < 0:
                total_minutes += 24 * 60
            ist_h = (total_minutes // 60) % 24
            ist_m = total_minutes % 60
            ampm = "PM" if ist_h >= 12 else "AM"
            display_h = ist_h % 12 or 12
            ist_time_str = f"{display_h}:{ist_m:02d} {ampm} IST"
            day_str = ""
            for en_day, hi_day in [
                ("sunday", "प्रत्येक रविवार"), ("monday", "प्रत्येक सोमवार"),
                ("tuesday", "प्रत्येक मंगलवार"), ("wednesday", "प्रत्येक बुधवार"),
                ("thursday", "प्रत्येक गुरुवार"), ("friday", "प्रत्येक शुक्रवार"),
                ("saturday", "प्रत्येक शनिवार")
            ]:
                if en_day in val.lower():
                    day_str = f"{hi_day}, "
                    break
            return f"{day_str}{ist_time_str}"

        # If it has simple IST or 24hr time like "20:00"
        time_match = re.search(r'(\d{1,2}):(\d{2})', val)
        if time_match:
            h, m = int(time_match.group(1)), int(time_match.group(2))
            ampm = "PM" if h >= 12 else "AM"
            display_h = h % 12 or 12
            return f"{display_h}:{m:02d} {ampm} IST"

        return val

    return str(val)


def airing_iso(val: Any) -> str:
    """Return ISO 8601 string for javascript new Date()."""
    if not val:
        return ""
    dt = None
    if isinstance(val, (int, float)):
        dt = datetime.fromtimestamp(val, tz=timezone.utc)
    elif isinstance(val, datetime):
        dt = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    elif isinstance(val, str):
        match = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', val)
        if match:
            date_p, time_p = match.groups()
            try:
                dt = datetime.strptime(f"{date_p} {time_p}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            except Exception:
                pass
        if not dt:
            try:
                dt = datetime.fromisoformat(val)
            except Exception:
                return ""
    if dt:
        return dt.isoformat()
    return ""


def extract_youtube_id(url: Optional[str]) -> Optional[str]:
    """Extract 11-char YouTube ID from url or external_id string."""
    if not url:
        return None
    url_str = str(url).strip()
    if len(url_str) == 11 and re.match(r'^[0-9A-Za-z_-]{11}$', url_str):
        return url_str
    if url_str.startswith("trailer_"):
        candidate = url_str[len("trailer_"):]
        if len(candidate) == 11 and re.match(r'^[0-9A-Za-z_-]{11}$', candidate):
            return candidate
    m = re.search(r'(?:v=|youtu\.be\/|embed\/|trailer_|\/vi\/|\/)([0-9A-Za-z_-]{11})', url_str)
    if m:
        return m.group(1)
    return None


def youtube_thumbnail(val: Optional[str]) -> Optional[str]:
    """Given a YouTube video ID or a URL, return the crisp 720p/1080p maxresdefault thumbnail URL."""
    if not val:
        return None
    yt_id = extract_youtube_id(val) if ("http" in val or "/" in val) else (val if len(val) == 11 else extract_youtube_id(val))
    if yt_id:
        return f"https://img.youtube.com/vi/{yt_id}/maxresdefault.jpg"
    return None


def strip_parenthetical_english(title: Optional[str]) -> str:
    """Strip redundant trailing parenthetical English text from a Hindi headline/title."""
    if not title:
        return ""
    cleaned = re.sub(r"\s*\([A-Za-z0-9\s:,\'\-\.\?!\/—–]+\)\s*$", "", str(title)).strip()
    return cleaned if cleaned else str(title)


def airing_weekday(val: Any) -> int:
    """Return weekday integer 0=Mon, 1=Tue, ..., 6=Sun in IST."""
    if not val:
        return 0
    if isinstance(val, str):
        cleaned = val.strip().lower()
        day_map = {
            "monday": 0, "mon": 0, "सोमवार": 0, "सोम": 0,
            "tuesday": 1, "tue": 1, "मंगलवार": 1, "मंगल": 1,
            "wednesday": 2, "wed": 2, "बुधवार": 2, "बुध": 2,
            "thursday": 3, "thu": 3, "गुरुवार": 3, "बृहस्पतिवार": 3, "गुरु": 3,
            "friday": 4, "fri": 4, "शुक्रवार": 4, "शुक्र": 4,
            "saturday": 5, "sat": 5, "शनिवार": 5, "शनि": 5,
            "sunday": 6, "sun": 6, "रविवार": 6, "रवि": 6,
        }
        for k, v in day_map.items():
            if k in cleaned:
                return v

    dt = None
    if isinstance(val, (int, float)):
        dt = datetime.fromtimestamp(val, tz=timezone.utc)
    elif isinstance(val, datetime):
        dt = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    elif isinstance(val, str):
        match = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', val)
        if match:
            date_p, time_p = match.groups()
            try:
                dt = datetime.strptime(f"{date_p} {time_p}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            except Exception:
                pass
        if not dt:
            try:
                dt = datetime.fromisoformat(val)
            except Exception:
                return 0
    if dt:
        ist_dt = dt.astimezone(IST)
        return ist_dt.weekday()
    return 0


def relative_time_hindi(val: Any) -> str:
    """Returns Hindi relative time like '2 घंटे पहले' or '3 दिन पहले'."""
    if not val:
        return ""
    dt = None
    if isinstance(val, (int, float)):
        dt = datetime.fromtimestamp(val, tz=timezone.utc)
    elif isinstance(val, datetime):
        dt = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    elif isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val)
        except Exception:
            return val
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 0:
        return "जल्द ही"
    if seconds < 60:
        return "अभी-अभी"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} मिनट पहले"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} घंटे पहले"
    days = hours // 24
    if days == 1:
        return "कल"
    if days < 7:
        return f"{days} दिन पहले"
    weeks = days // 7
    if weeks < 4:
        return f"{weeks} हफ़्ते पहले"
    return hindi_date(dt)


class CurrentYearValue(int):
    """Jinja-compatible callable and int representing current year in IST."""
    def __new__(cls):
        val = datetime.now(IST).year
        return super().__new__(cls, val)

    def __call__(self) -> int:
        return datetime.now(IST).year

    def __str__(self) -> str:
        return str(datetime.now(IST).year)


def current_year() -> CurrentYearValue:
    return CurrentYearValue()


class SiteProxy:
    """Read-only proxy exposing ONLY name, description, and author_name to templates."""
    @property
    def name(self) -> str:
        return get_settings().app_name

    @property
    def description(self) -> str:
        return get_settings().app_description

    @property
    def author_name(self) -> str:
        return get_settings().author_name

    @property
    def google_site_verification(self) -> str:
        return get_settings().google_site_verification

    def __getitem__(self, item: str):
        if item == "name":
            return self.name
        elif item == "description":
            return self.description
        elif item == "author_name":
            return self.author_name
        elif item == "google_site_verification":
            return self.google_site_verification
        raise KeyError(item)

    def __repr__(self) -> str:
        return f"Site(name={self.name!r}, description={self.description!r}, author_name={self.author_name!r})"


templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
def from_json(val: Any) -> Any:
    if not val:
        return []
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return []


def render_bold(val: Any) -> str:
    """Convert **bold** markers to strong HTML tags safely."""
    if not val:
        return ""
    import html as py_html
    safe_text = py_html.escape(str(val))
    return re.sub(r'\*\*(.+?)\*\*', r'<strong class="text-white font-bold">\1</strong>', safe_text)


templates.env.filters["from_json"] = from_json
templates.env.filters["render_bold"] = render_bold
templates.env.filters["hindi_date"] = hindi_date
templates.env.filters["short_description"] = short_description
templates.env.filters["first_words"] = first_words
templates.env.filters["format_airing_ist"] = format_airing_ist
templates.env.filters["extract_episode"] = extract_episode
templates.env.filters["airing_iso"] = airing_iso
templates.env.filters["airing_weekday"] = airing_weekday
templates.env.filters["relative_time_hindi"] = relative_time_hindi
templates.env.filters["extract_youtube_id"] = extract_youtube_id
templates.env.filters["youtube_thumbnail"] = youtube_thumbnail
templates.env.filters["strip_parenthetical_english"] = strip_parenthetical_english

from app.utils import get_initials, get_avatar_color
templates.env.globals["get_initials"] = get_initials
templates.env.globals["get_avatar_color"] = get_avatar_color
templates.env.globals["format_airing_ist"] = format_airing_ist
templates.env.globals["extract_episode"] = extract_episode
templates.env.globals["airing_iso"] = airing_iso
templates.env.globals["airing_weekday"] = airing_weekday
templates.env.globals["relative_time_hindi"] = relative_time_hindi
templates.env.globals["extract_youtube_id"] = extract_youtube_id
templates.env.globals["youtube_thumbnail"] = youtube_thumbnail
templates.env.globals["strip_parenthetical_english"] = strip_parenthetical_english
templates.env.filters["initials"] = get_initials
templates.env.filters["avatar_color"] = get_avatar_color

templates.env.globals["current_year"] = current_year
templates.env.globals["site"] = SiteProxy()
env = templates.env


class _DummyUrl:
    path = ""


class _DummyRequest:
    url = _DummyUrl()
    cookies = {}
    query_params = {}


def render(template: str, **context) -> str:
    context.setdefault("categories", [])
    context.setdefault("site_name", "Hindi Anime Guide")
    context.setdefault("site_tagline", "भारत में एनीमे कहाँ देखें — हिंदी डब गाइड")
    context.setdefault("current_year", CurrentYearValue())
    context.setdefault("user", None)
    if "request" not in context or context["request"] is None:
        context["request"] = _DummyRequest()
    req = context["request"]
    if "lang" not in context:
        extracted_lang = "hi"
        try:
            qp_lang = getattr(req, "query_params", {}).get("lang") if hasattr(req, "query_params") else None
            ck = getattr(req, "cookies", {}) if hasattr(req, "cookies") else {}
            ck_lang = ck.get("site_lang") or ck.get("lang")
            extracted_lang = qp_lang or ck_lang or "hi"
        except Exception:
            extracted_lang = "hi"
        context["lang"] = "en" if extracted_lang == "en" else "hi"
    tmpl = env.get_template(template)
    return tmpl.render(**context)
