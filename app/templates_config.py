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
    
    cleaned = text.replace("[नमूना]", "").strip()
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
templates.env.filters["hindi_date"] = hindi_date
templates.env.filters["short_description"] = short_description
templates.env.filters["first_words"] = first_words

from app.utils import get_initials, get_avatar_color
templates.env.globals["get_initials"] = get_initials
templates.env.globals["get_avatar_color"] = get_avatar_color
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


def render(template: str, **context) -> str:
    context.setdefault("categories", [])
    context.setdefault("site_name", "Hindi Anime Guide")
    context.setdefault("site_tagline", "भारत में एनीमे कहाँ देखें — हिंदी डब गाइड")
    context.setdefault("current_year", CurrentYearValue())
    context.setdefault("user", None)
    if "request" not in context or context["request"] is None:
        context["request"] = _DummyRequest()
    tmpl = env.get_template(template)
    return tmpl.render(**context)
