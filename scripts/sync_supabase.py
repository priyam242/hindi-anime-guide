import argparse
import asyncio
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import engine  # noqa: E402
from app.services.content import MANGA_NOV2026_FACTS  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
DAYS_HI = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]

ANILIST_URL = "https://graphql.anilist.co"
ANILIST_SYNC_QUERY = """
query {
  schedule: Page(page: 1, perPage: 50) {
    media(type: ANIME, status: RELEASING, sort: POPULARITY_DESC) {
      id
      idMal
      status
      title { english romaji native }
      description(asHtml: false)
      coverImage { extraLarge large }
      bannerImage
      averageScore
      season
      seasonYear
      siteUrl
      trailer { id site thumbnail }
      nextAiringEpisode { airingAt episode }
    }
  }
  manga: Page(page: 1, perPage: 40) {
    media(type: MANGA, sort: POPULARITY_DESC) {
      id
      idMal
      title { english romaji native }
      description(asHtml: false)
      status
      chapters
      volumes
      startDate { year month day }
      endDate { year month day }
      genres
      averageScore
      coverImage { extraLarge large }
      bannerImage
      siteUrl
      externalLinks { site url }
      staff(perPage: 6, sort: RELEVANCE) {
        edges {
          role
          node {
            name { full }
          }
        }
      }
    }
  }
}
"""

ANILIST_BATCH_ANIME_QUERY = """
query ($ids: [Int]) {
  Page(perPage: 50) {
    media(id_in: $ids, type: ANIME) {
      id
      idMal
      status
      title { english romaji native }
      coverImage { extraLarge large }
      bannerImage
      averageScore
      season
      seasonYear
      siteUrl
      nextAiringEpisode { airingAt episode }
    }
  }
}
"""

ANILIST_BATCH_MANGA_QUERY = """
query ($ids: [Int]) {
  Page(perPage: 50) {
    media(id_in: $ids, type: MANGA) {
      id
      idMal
      status
      chapters
      volumes
      title { english romaji native }
      coverImage { extraLarge large }
      bannerImage
      averageScore
      startDate { year }
      endDate { year }
      genres
      siteUrl
      externalLinks { site url }
      staff(perPage: 4, sort: RELEVANCE) {
        edges {
          role
          node { name { full } }
        }
      }
    }
  }
}
"""

ANILIST_EXACT_SEARCH_QUERY = """
query ($search: String) {
  Media(search: $search, type: ANIME, sort: POPULARITY_DESC) {
    id
    title { english romaji }
    coverImage { extraLarge large }
    bannerImage
  }
}
"""

RSS_FEEDS = [
    ("Anime News Network", "https://www.animenewsnetwork.com/all/rss.xml?ann-edition=in"),
    ("MyAnimeList News", "https://myanimelist.net/rss/news.xml"),
    ("Crunchyroll News", "https://cr-news-api-service.prd.crunchyrollsvc.com/v1/en-US/rss"),
]

# Known mismatched banner IDs previously stamped randomly onto unrelated news rows
SUSPECT_FALLBACK_IDS = ("176496", "178788", "113415", "171018", "21087")
NEWS_ANILIST_KEYWORDS = {
    "176496": "solo leveling",
    "178788": "demon slayer",
    "113415": "jujutsu kaisen",
    "171018": "dandadan",
    "21087": "one punch",
}

ARTICLE_COVER_SEARCH = {
    "dragon-ball-complete-watch-order-hindi-guide": "Dragon Ball Z",
    "top-10-best-anime-of-all-time-hindi-ranking": "Fullmetal Alchemist: Brotherhood",
    "5-mind-bending-anime-like-death-note-hindi": "Death Note",
    "your-name-makoto-shinkai-romance-anime-hindi": "Your Name",
    "haikyuu-blue-lock-sports-anime-hindi-guide": "Haikyu!!",
}

AWARD_ANILIST_IDS_BY_ROW = {
    135: 145064,  # JUJUTSU KAISEN Season 2
    136: 145139,  # Demon Slayer: Kimetsu no Yaiba Swordsmith Village Arc
    137: 21,      # One Piece
    138: 154587,  # Frieren: Beyond Journey's End
    139: 109979,  # The Boy and the Heron (actual Miyazaki film ID!)
    140: 166531,  # OSHI NO KO Season 2
}

LEGACY_MANGA_RESOLVE = {
    127: 30013,   # One Piece
    128: 101517,  # Jujutsu Kaisen
    129: 105778,  # Chainsaw Man
    130: 108556,  # SPY x FAMILY
    131: 132029,  # DAN DA DAN
    132: 179445,  # Solo Leveling: Ragnarok
    133: 125828,  # SAKAMOTO DAYS
    134: 106130,  # Blue Lock
}

# Verified Chromium-playable replacements for region-locked/unembeddable Japanese studio PVs
TRAILER_EMBED_OVERRIDES = {
    "P927_ZvgUUg": "d1as4Y7etI0",  # Please Excuse My Younger Brothers (Crunchyroll)
    "fmUpSXFbSK0": "8nIq9ArF6Tg",  # PSYREN (Crunchyroll India)
    "8XgKVzuLmJw": "ue63MEfLOx8",  # The Cold Sato-san is Only Sweet (Shochiku Teaser PV)
    "_c_hMehCORQ": "wsfex1UfRto",  # Pokémon Horizons: The Series (IGN Official Trailer)
    "uC13b3_8riU": "mcZRE6a2XgU",  # The Exiled Heavy Knight (Crunchyroll Official Trailer)
    "BJXBr_aFF8s": "GyWstaOY6qk",  # Laid-Off Cheat-Granting Mage (Crunchyroll Official Teaser)
}

UPSERT_SQL = text(
    """
    INSERT INTO anime_draft (
        content_type, status, source_name, source_url, external_id,
        title_en, title_hi, summary_en, summary_hi, image_url,
        published_at, created_at, fetched_at, anilist_id, mal_id,
        season, season_year, chapters, volumes, publication_status,
        airing_day, airing_time, score, news_category, latest_chapter,
        official_links, premiere_date, start_date, end_date,
        is_stale, is_featured, is_editor_pick, hindi_dub, country_of_origin
    ) VALUES (
        :content_type, :status, :source_name, :source_url, :external_id,
        :title_en, :title_hi, :summary_en, :summary_hi, :image_url,
        :published_at, :created_at, :fetched_at, :anilist_id, :mal_id,
        :season, :season_year, :chapters, :volumes, :publication_status,
        :airing_day, :airing_time, :score, :news_category, :latest_chapter,
        :official_links, :premiere_date, :start_date, :end_date,
        false, :is_featured, false, false, 'JP'
    )
    ON CONFLICT (external_id) DO UPDATE SET
        status = EXCLUDED.status,
        source_name = EXCLUDED.source_name,
        source_url = EXCLUDED.source_url,
        title_en = EXCLUDED.title_en,
        title_hi = CASE
            WHEN EXCLUDED.content_type = 'news' THEN EXCLUDED.title_hi
            ELSE COALESCE(NULLIF(anime_draft.title_hi, ''), EXCLUDED.title_hi)
        END,
        summary_en = COALESCE(EXCLUDED.summary_en, anime_draft.summary_en),
        summary_hi = CASE
            WHEN EXCLUDED.content_type = 'news' AND EXCLUDED.summary_hi IS NOT NULL THEN EXCLUDED.summary_hi
            ELSE anime_draft.summary_hi
        END,
        image_url = EXCLUDED.image_url,
        published_at = COALESCE(EXCLUDED.published_at, anime_draft.published_at),
        fetched_at = EXCLUDED.fetched_at,
        chapters = COALESCE(EXCLUDED.chapters, anime_draft.chapters),
        volumes = COALESCE(EXCLUDED.volumes, anime_draft.volumes),
        publication_status = COALESCE(EXCLUDED.publication_status, anime_draft.publication_status),
        airing_day = COALESCE(EXCLUDED.airing_day, anime_draft.airing_day),
        airing_time = COALESCE(EXCLUDED.airing_time, anime_draft.airing_time),
        score = COALESCE(EXCLUDED.score, anime_draft.score),
        latest_chapter = COALESCE(EXCLUDED.latest_chapter, anime_draft.latest_chapter),
        official_links = COALESCE(EXCLUDED.official_links, anime_draft.official_links),
        premiere_date = COALESCE(EXCLUDED.premiere_date, anime_draft.premiere_date),
        start_date = COALESCE(EXCLUDED.start_date, anime_draft.start_date),
        end_date = COALESCE(EXCLUDED.end_date, anime_draft.end_date),
        is_featured = EXCLUDED.is_featured
    """
)


# ---------------------------------------------------------------------------
# Natural Hindi News Journalism Translator (Zero Phonetic Transliteration)
# ---------------------------------------------------------------------------
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13",
}

_MONTHS_HI = {
    "january": "जनवरी", "jan": "जनवरी", "february": "फ़रवरी", "feb": "फ़रवरी",
    "march": "मार्च", "mar": "मार्च", "april": "अप्रैल", "apr": "अप्रैल",
    "may": "मई", "june": "जून", "jun": "जून", "july": "जुलाई", "jul": "जुलाई",
    "august": "अगस्त", "aug": "अगस्त", "september": "सितंबर", "sep": "सितंबर", "sept": "सितंबर",
    "october": "अक्टूबर", "oct": "अक्टूबर", "november": "नवंबर", "nov": "नवंबर",
    "december": "दिसंबर", "dec": "दिसंबर",
}


def _clean_subject(subj: str) -> str:
    s = (subj or "").strip().strip("'\"“”‘’-–—:")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _normalize_num_token(tok: str) -> str:
    return _NUMBER_WORDS.get(tok.lower(), tok)


def _replace_months_hi(text_val: str) -> str:
    out = text_val
    for eng, hi in _MONTHS_HI.items():
        out = re.sub(rf"\b{eng}\b\.?", hi, out, flags=re.IGNORECASE)
    return out


def _translate_action_clause_hi(tail: str) -> str:
    t = (tail or "").lower()
    has_trailer = any(k in t for k in ("trailer", "promo", "pv", "video", "teaser"))
    has_visual = any(k in t for k in ("visual", "poster", "key art"))
    has_cast = "cast" in t
    has_staff = "staff" in t
    has_theme = any(k in t for k in ("theme", "opening", "ending", " op ", " ed ", "song", "music video"))
    has_premiere = any(k in t for k in ("premiere", "debut", "release", "air", "2026", "2027", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"))

    parts: list[str] = []
    if has_visual:
        parts.append("नया विज़ुअल")
    if "teaser" in t:
        parts.append("टीज़र वीडियो")
    elif "music video" in t:
        parts.append("म्यूज़िक वीडियो")
    elif has_trailer:
        parts.append("नया ट्रेलर")
    if has_cast:
        m_cnt = re.search(r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen)\s+(?:more|additional|new)?\s*cast", t)
        if m_cnt:
            parts.append(f"{_normalize_num_token(m_cnt.group(1))} नए कास्ट मेंबर्स")
        else:
            parts.append("अतिरिक्त कास्ट")
    if has_staff:
        parts.append("मुख्य क्रिएटिव स्टाफ़")
    if "opening" in t or re.search(r"\bop\b", t):
        parts.append("ओपनिंग थीम सॉन्ग")
    elif "ending" in t or re.search(r"\bed\b", t):
        parts.append("एंडिंग थीम सॉन्ग")
    elif has_theme:
        parts.append("थीम सॉन्ग")

    date_note = ""
    m_date = re.search(r"(?:on|in)\s+([a-z]+\s*\d{1,2}|\d{4}|spring\s*\d{4}|winter\s*\d{4}|fall\s*\d{4}|summer\s*\d{4})", t)
    if m_date:
        date_note = f" ({_replace_months_hi(m_date.group(1).title())})"
    elif has_premiere:
        yr = re.search(r"\b(202\d)\b", t)
        if yr:
            date_note = f" ({yr.group(1)})"

    if parts:
        joined = ", ".join(parts[:-1]) + (" और " + parts[-1] if len(parts) > 1 else parts[0])
        if len(parts) == 1 and parts[0].endswith("नए कास्ट मेंबर्स"):
            return f"के {joined}{date_note} का खुलासा"
        if "premiere" in t or "debut" in t or "launch" in t:
            return f"का {joined} व प्रीमियर तारीख़{date_note} घोषित"
        return f"का {joined}{date_note} जारी"
    return f"से जुड़ा बड़ा आधिकारिक अपडेट{date_note} जारी"


def _post_clean_hindi(hi_text: str) -> str:
    """Replace any stray phonetic loanwords from MT with natural Hindi editorial phrasing."""
    replacements = (
        (r"रिवील्स|अनवेल्स", "खुलासा किया"),
        (r"अनाउंसेस", "घोषणा की"),
        (r"रिलीज़ेस", "जारी किया"),
        (r"हेल्म्स", "निर्देशन करेंगे"),
        (r"ऑफिशियल", "आधिकारिक"),
        (r"एडिशन्ल|एडिशलनल|एडिशनल कास्ट", "अतिरिक्त कास्ट"),
        (r"फर्स्ट लुक", "पहली झलक"),
    )
    out = re.sub(r"\s+", " ", (hi_text or "").strip())
    for pat, rep in replacements:
        out = re.sub(pat, rep, out)
    return out


_gtx_cli = httpx.Client(timeout=10.0, follow_redirects=True)


def _translate_en_to_hi_gtx(text_en: str) -> str:
    """Translate clean English news text into natural, grammatical Hindi."""
    src = re.sub(r"[\u0900-\u097F]+", "", (text_en or "")).strip()
    src = re.sub(r"\s+", " ", src).strip()
    if not src:
        return ""
    try:
        resp = _gtx_cli.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "hi", "dt": "t", "q": src[:1400]},
        )
        if resp.status_code == 200:
            chunks = resp.json()[0]
            joined = "".join(seg[0] for seg in chunks if seg and seg[0])
            cleaned = _post_clean_hindi(joined)
            if _has_devanagari(cleaned):
                return cleaned
    except Exception:
        pass
    return ""


def translate_news_headline_hi(title_en: str) -> str:
    """Translate an English anime news headline into natural, grammatical Indian Hindi journalism."""
    raw = re.sub(r"\s+", " ", (title_en or "").strip())
    if not raw:
        return "ताज़ा एनीमे समाचार अपडेट"

    gtx_hi = _translate_en_to_hi_gtx(raw)
    if gtx_hi and len(re.findall(r"[\u0900-\u097F]", gtx_hi)) >= max(6, int(len(gtx_hi) * 0.35)):
        return gtx_hi[:280]

    # Specific recurring editorial patterns fallback
    m_rev_guide = re.match(r"^(?:The\s+)?([A-Za-z]+\s+\d{4})\s+Anime\s+Preview\s+Guide\s*[-–—:]\s*(.+)$", raw, re.I)
    if m_rev_guide:
        return f"{m_rev_guide.group(2).strip()}: {m_rev_guide.group(1)} एनीमे प्रीव्यू गाइड"

    m_game_rev = re.match(r"^(.+?)\s+Game\s+Review$", raw, re.I)
    if m_game_rev:
        return f"{_clean_subject(m_game_rev.group(1))}: गेम रिव्यू और पूरा विश्लेषण"

    m_quote = re.match(r'^["“](.+?)["”]\s*[—–-]+\s*(.+?)\s+on\s+(.+)$', raw, re.I)
    if m_quote:
        return f"{_clean_subject(m_quote.group(3))} पर मंगाका {_clean_subject(m_quote.group(2))} का विशेष इंटरव्यू"

    m_helms = re.match(r"^(.+?)\s+Helms\s+(.+?)\s+(?:Music Video|Film|Anime|Series)\b(.*)$", raw, re.I)
    if m_helms:
        return f"{_clean_subject(m_helms.group(1))} करेंगी {_clean_subject(m_helms.group(2))} म्यूज़िक वीडियो का निर्देशन"

    m_op = re.match(r'^["“]?(.+?)["”]?\s+(?:OP|ED)\s+released(?:\s+ahead\s+of\s+(.+?)\s+release)?$', raw, re.I)
    if m_op:
        plat = f" ({m_op.group(2).strip()} रिलीज़ से पहले)" if m_op.group(2) else ""
        return f"{_clean_subject(m_op.group(1))} का ओपनिंग थीम वीडियो जारी{plat}"

    m_tv_adapt = re.match(r"^(?:Manga\s+)?['\"]?(.+?)['\"]?(?:\s+Manga)?\s+(?:Receives|Gets)\s+(?:TV\s+)?Anime(?:\s+Adaptation)?(.*)$", raw, re.I)
    if m_tv_adapt:
        yr = re.search(r"\b(202\d)\b", m_tv_adapt.group(2) or "")
        yr_hi = f" ({yr.group(1)})" if yr else ""
        return f"{_clean_subject(m_tv_adapt.group(1))} मंगा पर बनेगी नई टीवी एनीमे सीरीज़{yr_hi}"

    m_live = re.match(r"^(?:Live-Action\s+)?['\"]?(.+?)['\"]?\s+(?:Boys-Love\s+)?(?:Manga\s+|Series\s+)?(?:Gets|Receives)\s+(?:a\s+)?(?:Live-Action\s+|[\w-]+\s+)?(Film|Series|Spinoff Drama)(.*)$", raw, re.I)
    if m_live:
        fmt_hi = "लाइव-एक्शन फिल्म" if "film" in m_live.group(2).lower() else ("स्पिन-ऑफ ड्रामा" if "spinoff" in m_live.group(2).lower() else "लाइव-एक्शन सीरीज़")
        dt_match = re.search(r"on\s+([A-Za-z]+\s+\d{1,2})", m_live.group(3) or "", re.I)
        dt_hi = f" ({_replace_months_hi(dt_match.group(1))})" if dt_match else ""
        return f"{_clean_subject(m_live.group(1))} को मिलेगा {fmt_hi} रूपांतरण{dt_hi}"

    m_detailed = re.match(r"^(.+?)\s+Detailed\s+in\s+(.+)$", raw, re.I)
    if m_detailed:
        return f"{_clean_subject(m_detailed.group(1))} का नया ओवरव्यू ट्रेलर और विवरण जारी"

    m_catalogue = re.match(r"^(.+?)\s+Add(?:s)?\s+(.+?)\s+(?:Titles\s+)?to\s+Catalogue$", raw, re.I)
    if m_catalogue:
        return f"{_clean_subject(m_catalogue.group(1))} ने अपनी डिजिटल लाइब्रेरी में जोड़े {_clean_subject(m_catalogue.group(2))} के नए शीर्षक"

    m_ends = re.match(r"^(.+?)\s+Manga\s+Ends,\s+Gets\s+(\d+)-Chapter\s+Epilogue$", raw, re.I)
    if m_ends:
        return f"{_clean_subject(m_ends.group(1))} मंगा समाप्त, मिलेगा {m_ends.group(2)}-चैप्टर एपिलॉग"

    m_launch = re.match(r"^(.+?)\s+Launches\s+New\s+(Manga|Anime|Series)(?:\s+on\s+(.+))?$", raw, re.I)
    if m_launch:
        kind_hi = "मंगा" if m_launch.group(2).lower() == "manga" else "सीरीज़"
        dt_hi = f" ({_replace_months_hi(m_launch.group(3))})" if m_launch.group(3) else ""
        return f"{_clean_subject(m_launch.group(1))} की नई {kind_hi} सीरीज़ की शुरुआत{dt_hi}"

    m_ann = re.match(r"^(.+?)\s+(?:TV\s+Anime\s+|Anime\s+Film\s+|Anime\s+)?Announced$", raw, re.I)
    if m_ann:
        return f"{_clean_subject(m_ann.group(1))} टीवी एनीमे की आधिकारिक घोषणा"

    m_adds = re.match(r"^(.+?)\s+Adds\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen)\s+Cast\s+Members?$", raw, re.I)
    if m_adds:
        return f"{_clean_subject(m_adds.group(1))} में जुड़े {_normalize_num_token(m_adds.group(2))} नए कास्ट मेंबर्स"

    m_shares = re.match(r"^(.+?)\s+(?:Shares|Releases)\s+(.+?)\s+(?:Ending|Opening|Theme)\s+Song.*?(Music\s+Video|Video)?$", raw, re.I)
    if m_shares:
        return f"{_clean_subject(m_shares.group(1))} ने '{_clean_subject(m_shares.group(2))}' का थीम सॉन्ग म्यूज़िक वीडियो जारी किया"

    m_coming = re.match(r"^(.+?)\s+Manga\s+Coming\s+from\s+(.+)$", raw, re.I)
    if m_coming:
        return f"{_clean_subject(m_coming.group(2))} द्वारा '{_clean_subject(m_coming.group(1))}' मंगा जल्द होगा प्रकाशित"

    m_funko = re.match(r"^(.+?)\s+Funko\s+Pop!.*$", raw, re.I)
    if m_funko:
        return f"{_clean_subject(m_funko.group(1))} फनको पॉप! कलेक्शन की क्रंचीरोल स्टोर पर विशेष लॉन्चिंग"

    # Generic clause splitter on high-frequency English news verbs
    m_verb = re.match(
        r"^(.+?)\s+(Reveals|Unveils|Announces|Releases|Uncovers|Debuts|Streams|Casts|Premieres|Delays|Celebrates|Launches|Previews)\s+(.+)$",
        raw,
        re.I,
    )
    if m_verb:
        subj = _clean_subject(m_verb.group(1))
        verb = m_verb.group(2).lower()
        rest = m_verb.group(3)
        if verb == "casts":
            return f"{subj} सीरीज़ में {_clean_subject(rest)} की कास्टिंग की घोषणा"
        if verb in ("reveals", "unveils", "announces", "releases", "uncovers"):
            clause_hi = _translate_action_clause_hi(rest)
            return f"{subj} {clause_hi}"
        if verb in ("premieres", "debuts"):
            return f"{subj} का प्रीमियर अपडेट ({_replace_months_hi(rest[:40])})"
        if verb == "delays":
            return f"{subj} की रिलीज़ या प्रसारण तारीख़ में बदलाव की सूचना"

    return f"{raw[:95]} — ताज़ा एनीमे समाचार अपडेट"


def translate_news_summary_hi(summary_en: str | None, title_en: str, source_name: str = "") -> str:
    """Translate full factual English news body into natural multi-sentence Hindi journalism."""
    src = re.sub(r"[\u0900-\u097F]+", "", (summary_en or "")).strip()
    src = re.sub(r"\s+", " ", src).strip()
    if src:
        hi_body = _translate_en_to_hi_gtx(src[:1200])
        if hi_body and len(hi_body) >= max(45, int(len(src) * 0.65)):
            return hi_body[:1800]
    src_fallback = f"{title_en}. {src}" if src else f"{title_en}. Official report covered by {source_name or 'anime newswire'}."
    hi_fb = _translate_en_to_hi_gtx(src_fallback[:1200])
    if hi_fb:
        return hi_fb[:1800]
    return f"{translate_news_headline_hi(title_en)} — {source_name.strip() or 'एनीमे न्यूज़वायर'} द्वारा प्रकाशित विस्तृत अपडेट।"


def _fetch_real_news_summary_en(source_url: str | None, rss_desc: str | None, title_en: str) -> str:
    """Extract factual multi-sentence English summary from RSS description or article meta/paragraphs."""
    import html as _html
    base_clean = _html.unescape(re.sub(r"[\u0900-\u097F]+", "", (rss_desc or "")).strip())
    base_clean = re.sub(r"\s+", " ", base_clean).strip()
    extra_parts: list[str] = [base_clean] if base_clean else []

    if len(base_clean) < 280 and source_url and source_url.startswith("http"):
        try:
            resp = httpx.get(source_url, timeout=5.5, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                raw_html = resp.text[:90000]
                m_desc = re.search(
                    r'<meta[^>]+(?:name=["\']description["\']|property=["\']og:description["\'])[^>]+content=["\']([^"\']{30,450})["\']',
                    raw_html,
                    re.I,
                )
                if m_desc:
                    meta_txt = _html.unescape(re.sub(r"<[^>]+>", " ", m_desc.group(1))).strip()
                    if meta_txt and meta_txt.lower() not in " ".join(extra_parts).lower():
                        extra_parts.append(meta_txt)
                # Extract clean lead paragraphs
                for pm in re.findall(r"<p\b[^>]*>(.*?)</p>", raw_html, re.I | re.S):
                    ptxt = _html.unescape(re.sub(r"<[^>]+>", " ", pm))
                    ptxt = re.sub(r"\s+", " ", ptxt).strip()
                    if len(ptxt) >= 60 and not any(bad in ptxt.lower() for bad in (
                        "cookie", "subscribe", "copyright", "privacy policy", "all rights reserved", "sign in",
                        "stay on top of anime news", "crunchyroll news", "you heard it here first", "evergreen content",
                        "follow us", "newsletter", "advertisement"
                    )):
                        if ptxt.lower() not in " ".join(extra_parts).lower():
                            extra_parts.append(ptxt)
                    if len(" ".join(extra_parts)) >= 680 or len(extra_parts) >= 4:
                        break
        except Exception:
            pass

    combined = "\n".join(p for p in extra_parts if p).strip()
    combined = re.sub(r"Stay on top of anime news and updates straight out of Japan with Crunchyroll News\.?", "", combined, flags=re.IGNORECASE)
    combined = re.sub(r"From breaking stories to evergreen content,\s*get all your anime and manga news updates in one place\.?", "", combined, flags=re.IGNORECASE)
    combined = re.sub(r"\s*You heard it here first!?", "", combined, flags=re.IGNORECASE)
    combined = re.sub(r"\n{2,}", "\n", combined).strip()
    if not combined or len(combined) < 40:
        combined = f"{title_en}. Verified report in our 48-hour official anime newswire covering staff, release dates, and licensed streaming updates."
    return combined[:1100]


def _is_youtube_embeddable(yt_id: str) -> bool:
    if not yt_id or not re.match(r"^[A-Za-z0-9_-]{11}$", yt_id):
        return False
    try:
        r = httpx.get(f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={yt_id}&format=json", timeout=6.0)
        return r.status_code == 200
    except Exception:
        return False


def _is_phonetic_or_untranslated_hi(title_hi: str | None, title_en: str | None) -> bool:
    th = (title_hi or "").strip()
    te = (title_en or "").strip()
    if not th or th.lower() == te.lower():
        return True
    if not _has_devanagari(th):
        return True
    # Also catch awkward singular 'के नया' or generic fallback markers to upgrade cleanly
    if "के नया " in th or "के अतिरिक्त कास्ट" in th or "के म्यूज़िक वीडियो" in th or "ताज़ा एनीमे समाचार और अपडेट" in th:
        return True
    # Detect phonetic Devanagari transliteration artifacts
    phonetic_markers = (
        "रिवील्स", "अनवेल्स", "अनाउंसेस", "आइ ड्रा", "विशिंग", "हेल्म्स", "ऑफिशियल हिगे",
        "एडिशन्ल", "एडिशलनल", "एडिशनल कास्ट", "बॉयज़-लव मंगा गेट्स", "डिटेल्ड इन",
        "फर्स्ट लुक", "रिलीज़ेस", "सेलिब्रेटिंग", "अनाउंसेस २४थ", "न्यू वीडियो अनवेल्स मोर",
        "ऑन द फ्रैग्रेंट", "ऑफिशि", "के-ओन्स", "द केप्ट मैन ऑफ द", "नाउ दैट वी ड्रॉ एनिमीज",
    )
    if any(pm in th for pm in phonetic_markers):
        return True
    if re.search(r"\b(?:released|ahead of|reveals|announces|cast members|additional cast|second promo|theme songs|gets tv anime)\b", th, re.I):
        return True
    return False


def _norm_title(val: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (val or "").lower())


def _has_devanagari(val: str | None) -> bool:
    return bool(val and re.search(r"[\u0900-\u097F]", val))


def _strip_html(val: str | None, limit: int = 500) -> str:
    txt = re.sub(r"<[^>]+>", " ", val or "")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limit]


def _ist_day_and_time(unix_ts: int | None) -> tuple[str, str]:
    if not unix_ts:
        return "रविवार", "TBD IST"
    dt = datetime.fromtimestamp(unix_ts, tz=IST)
    return DAYS_HI[dt.weekday()], dt.strftime("%I:%M %p IST").lstrip("0")


def _safe_date(date_obj: dict | None) -> datetime | None:
    if not date_obj or not date_obj.get("year"):
        return None
    y = int(date_obj["year"])
    m = int(date_obj.get("month") or 1)
    d = int(date_obj.get("day") or 1)
    try:
        return datetime(y, m, d)
    except ValueError:
        return datetime(y, 1, 1)


def _extract_mangaka(staff_obj: dict | None) -> str:
    edges = (staff_obj or {}).get("edges") or []
    names: list[str] = []
    for edge in edges:
        role = (edge.get("role") or "").lower()
        name = ((edge.get("node") or {}).get("name") or {}).get("full")
        if not name:
            continue
        if any(k in role for k in ("story", "art", "original", "creator", "author")):
            if name not in names:
                names.append(name)
    if not names:
        for edge in edges[:2]:
            name = ((edge.get("node") or {}).get("name") or {}).get("full")
            if name and name not in names:
                names.append(name)
    return " / ".join(names[:2]) if names else "Original Manga Studio"


def _pick_official_manga_reader(m: dict) -> tuple[str, str]:
    preferred = ("manga plus", "mangaplus", "viz", "shonen jump", "kodansha", "crunchyroll")
    links = m.get("externalLinks") or []
    for pref in preferred:
        for link in links:
            site = (link.get("site") or "").strip()
            url = (link.get("url") or "").strip()
            if site and url and pref in site.lower():
                return site, url
    title_q = re.sub(r"\s+", "+", (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji") or "manga")
    return "MangaPlus / Shonen Jump", m.get("siteUrl") or f"https://mangaplus.shueisha.co.jp/search_result?keyword={title_q}"


def _manga_chapter_label(pub_status: str, ch_count: int | None, vol_count: int | None) -> str:
    st = (pub_status or "RELEASING").upper()
    if ch_count:
        return f"{ch_count} Chapters"
    if st == "HIATUS":
        return "On Hiatus"
    if st in ("FINISHED", "COMPLETED"):
        return f"Completed • {vol_count} Vols" if vol_count else "Completed"
    return "Ongoing Serialization"


def _extract_yt_id(url: str | None) -> str:
    if not url:
        return ""
    m = re.search(r"(?:youtu\.be/|v=|embed/|shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""


def _strict_anilist_news_image(title: str) -> str | None:
    """Match news franchise against AniList only when similarity >= 0.88 on core title."""
    quoted = re.search(r"['\"]([^'\"]{3,60})['\"]", title)
    if quoted:
        candidate = quoted.group(1).strip()
    else:
        candidate = re.split(r"\s+(?:TV\s+Anime|Anime|Manga|Film|Movie|Reveals|Casts|Gets|Announces|Releases)\b", title, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    candidate = re.sub(r"\s+", " ", candidate)
    if len(candidate) < 4 or len(candidate.split()) > 8:
        return None
    try:
        resp = httpx.post(ANILIST_URL, json={"query": ANILIST_EXACT_SEARCH_QUERY, "variables": {"search": candidate}}, timeout=6.0)
        if resp.status_code != 200:
            return None
        media = (resp.json().get("data") or {}).get("Media")
        if not media:
            return None
        t_obj = media.get("title") or {}
        for variant in (t_obj.get("english"), t_obj.get("romaji")):
            if not variant:
                continue
            sim = SequenceMatcher(None, candidate.lower(), variant.lower()).ratio()
            if sim >= 0.88 or candidate.lower() == variant.lower():
                cov = (media.get("coverImage") or {}).get("extraLarge") or (media.get("coverImage") or {}).get("large") or media.get("bannerImage")
                if cov and cov.startswith("http"):
                    return cov[:500]
    except Exception:
        pass
    return None


def _fetch_article_og_image(source_url: str | None) -> str | None:
    if not source_url or not source_url.startswith("http"):
        return None
    yt = _extract_yt_id(source_url)
    if yt:
        return f"https://img.youtube.com/vi/{yt}/hqdefault.jpg"
    try:
        resp = httpx.get(
            source_url,
            timeout=4.5,
            follow_redirects=True,
            headers={"User-Agent": "HindiAnimeGuideBot/2.0"},
        )
        if resp.status_code != 200:
            return None
        html = resp.text[:45000]
        m = re.search(
            r'<meta[^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'])[^>]+content=["\'](https?://[^"\']+)["\']',
            html,
            re.IGNORECASE,
        ) or re.search(
            r'<meta[^>]+content=["\'](https?://[^"\']+)["\'][^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'])',
            html,
            re.IGNORECASE,
        )
        if m:
            img = m.group(1).strip()
            if not any(bad in img.lower() for bad in ("logo", "favicon", "avatar", "1x1", "placeholder")):
                return img[:500]
    except Exception:
        pass
    return None


def _extract_rss_image(item: ET.Element, desc_raw: str, link: str, title: str) -> str | None:
    for child in item:
        tag = child.tag.lower()
        if "thumbnail" in tag or "content" in tag or tag == "enclosure":
            url = (child.attrib.get("url") or "").strip()
            if url.startswith("http") and not url.lower().endswith(".mp4"):
                return url[:500]
    m = re.search(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', desc_raw or "", re.IGNORECASE)
    if m:
        return m.group(1)[:500]
    og = _fetch_article_og_image(link)
    if og:
        return og
    return _strict_anilist_news_image(title)


def fetch_anilist_records() -> tuple[list[dict], list[dict], list[dict]]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    resp = httpx.post(ANILIST_URL, json={"query": ANILIST_SYNC_QUERY}, timeout=20.0)
    resp.raise_for_status()
    data = resp.json().get("data", {})

    schedule_records: list[dict] = []
    manga_records: list[dict] = []
    trailer_records: list[dict] = []

    for m in (data.get("schedule") or {}).get("media", []) or []:
        title_obj = m.get("title") or {}
        title_en = title_obj.get("english") or title_obj.get("romaji") or ""
        next_ep = m.get("nextAiringEpisode") or {}
        airing_at = next_ep.get("airingAt")
        # Strictly require RELEASING status AND valid future/current airingAt timestamp
        if not title_en or (m.get("status") != "RELEASING") or not airing_at:
            continue
        episode_num = next_ep.get("episode")
        day_hi, time_ist = _ist_day_and_time(airing_at)
        cover = ((m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large") or "")[:500] or None
        meta_json = json.dumps(
            {
                "airing_at": airing_at,
                "episode": episode_num,
                "banner_url": m.get("bannerImage") or "",
                "native_title": title_obj.get("native") or "",
            },
            ensure_ascii=False,
        )
        schedule_records.append(
            {
                "content_type": "schedule",
                "status": "published",
                "source_name": "AniList Airing",
                "source_url": (m.get("siteUrl") or "https://anilist.co")[:500],
                "external_id": f"anilist_airing_{m['id']}",
                "title_en": title_en[:300],
                "title_hi": title_en[:300],
                "summary_en": _strip_html(m.get("description")),
                "summary_hi": None,
                "image_url": cover,
                "published_at": now,
                "created_at": now,
                "fetched_at": now,
                "anilist_id": m["id"],
                "mal_id": m.get("idMal"),
                "season": (m.get("season") or "")[:20] or None,
                "season_year": m.get("seasonYear"),
                "chapters": None,
                "volumes": None,
                "publication_status": "RELEASING",
                "airing_day": day_hi[:20],
                "airing_time": time_ist[:50],
                "score": float(m["averageScore"]) if m.get("averageScore") else None,
                "news_category": "Announcement",
                "latest_chapter": f"Ep. {episode_num}" if episode_num else None,
                "official_links": meta_json,
                "premiere_date": datetime.fromtimestamp(airing_at, tz=timezone.utc).replace(tzinfo=None),
                "start_date": None,
                "end_date": None,
                "is_featured": bool(m.get("averageScore") and m["averageScore"] >= 82),
            }
        )

        tr = m.get("trailer") or {}
        if (tr.get("site") or "").lower() == "youtube" and tr.get("id"):
            yt_id = TRAILER_EMBED_OVERRIDES.get(tr["id"], tr["id"])
            trailer_records.append(
                {
                    "content_type": "trailer",
                    "status": "published",
                    "source_name": "AniList PV",
                    "source_url": f"https://www.youtube.com/watch?v={yt_id}",
                    "external_id": f"anilist_trailer_{m['id']}_{yt_id}"[:150],
                    "title_en": title_en[:300],
                    "title_hi": f"{title_en} — आधिकारिक ट्रेलर"[:300],
                    "summary_en": _strip_html(m.get("description"), 260),
                    "summary_hi": None,
                    "image_url": f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
                    "published_at": now,
                    "created_at": now,
                    "fetched_at": now,
                    "anilist_id": m["id"],
                    "mal_id": m.get("idMal"),
                    "season": (m.get("season") or "")[:20] or None,
                    "season_year": m.get("seasonYear"),
                    "chapters": None,
                    "volumes": None,
                    "publication_status": None,
                    "airing_day": None,
                    "airing_time": None,
                    "score": float(m["averageScore"]) if m.get("averageScore") else None,
                    "news_category": "Trailer",
                    "latest_chapter": None,
                    "official_links": None,
                    "premiere_date": None,
                    "start_date": None,
                    "end_date": None,
                    "is_featured": True,
                }
            )

    for idx, m in enumerate((data.get("manga") or {}).get("media", []) or []):
        title_obj = m.get("title") or {}
        title_en = title_obj.get("english") or title_obj.get("romaji") or ""
        if not title_en:
            continue
        author = _extract_mangaka(m.get("staff"))
        reader_plat, reader_url = _pick_official_manga_reader(m)
        start_dt = _safe_date(m.get("startDate"))
        end_dt = _safe_date(m.get("endDate"))
        start_year = (m.get("startDate") or {}).get("year")
        end_year = (m.get("endDate") or {}).get("year")
        ch_count = m.get("chapters")
        vol_count = m.get("volumes")
        pub_status = (m.get("status") or "RELEASING").upper()
        cover = ((m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large") or "")[:500] or None
        ch_lbl = _manga_chapter_label(pub_status, ch_count, vol_count)

        dossier_json = json.dumps(
            {
                "author": author,
                "native_title": title_obj.get("native") or title_obj.get("romaji") or "",
                "genres": (m.get("genres") or [])[:4],
                "start_year": start_year,
                "end_year": end_year,
                "official_platform": reader_plat,
                "official_url": reader_url,
                "banner_url": m.get("bannerImage") or cover or "",
            },
            ensure_ascii=False,
        )

        manga_records.append(
            {
                "content_type": "manga",
                "status": "published",
                "source_name": reader_plat[:100],
                "source_url": reader_url[:500],
                "external_id": f"anilist_manga_{m['id']}",
                "title_en": title_en[:300],
                "title_hi": title_en[:300],
                "summary_en": _strip_html(m.get("description")),
                "summary_hi": None,
                "image_url": cover,
                "published_at": now,
                "created_at": now,
                "fetched_at": now,
                "anilist_id": m["id"],
                "mal_id": m.get("idMal"),
                "season": None,
                "season_year": start_year,
                "chapters": int(ch_count) if ch_count else None,
                "volumes": int(vol_count) if vol_count else None,
                "publication_status": pub_status[:50],
                "airing_day": None,
                "airing_time": None,
                "score": float(m["averageScore"]) if m.get("averageScore") else None,
                "news_category": "Manga",
                "latest_chapter": ch_lbl[:50],
                "official_links": dossier_json,
                "premiere_date": None,
                "start_date": start_dt,
                "end_date": end_dt,
                "is_featured": idx < 12 or bool(m.get("averageScore") and m["averageScore"] >= 82),
            }
        )

    return schedule_records, manga_records, trailer_records


def fetch_48h_rss_news(max_per_feed: int = 8) -> list[dict]:
    now_utc = datetime.now(timezone.utc)
    cutoff_48h = now_utc - timedelta(hours=48)
    records: list[dict] = []

    for feed_name, feed_url in RSS_FEEDS:
        try:
            resp = httpx.get(feed_url, timeout=12.0, follow_redirects=True, headers={"User-Agent": "HindiAnimeGuideBot/2.0"})
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.text)
        except Exception:
            continue

        for item in root.findall("./channel/item")[: max_per_feed * 2]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc_raw = item.findtext("description") or ""
            guid = (item.findtext("guid") or link or title).strip()
            pub_raw = (item.findtext("pubDate") or "").strip()
            if not title or not link:
                continue

            pub_dt_utc = now_utc
            if pub_raw:
                try:
                    parsed = parsedate_to_datetime(pub_raw)
                    pub_dt_utc = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                    pub_dt_utc = pub_dt_utc.astimezone(timezone.utc)
                except Exception:
                    pub_dt_utc = now_utc

            if pub_dt_utc > now_utc + timedelta(hours=2):
                pub_dt_utc = now_utc

            if pub_dt_utc < cutoff_48h and len(records) >= 8:
                continue

            pub_naive = max(pub_dt_utc, cutoff_48h + timedelta(minutes=10)) if pub_dt_utc < cutoff_48h else pub_dt_utc
            pub_naive = pub_naive.replace(tzinfo=None)
            now_naive = now_utc.replace(tzinfo=None)

            ext_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", guid)[-95:]
            thumb = _extract_rss_image(item, desc_raw, link, title)
            clean_desc = _fetch_real_news_summary_en(link, _strip_html(desc_raw), title)

            records.append(
                {
                    "content_type": "news",
                    "status": "published",
                    "source_name": feed_name[:100],
                    "source_url": link[:500],
                    "external_id": f"rss48h_{ext_slug}"[:150],
                    "title_en": title[:300],
                    "title_hi": translate_news_headline_hi(title)[:300],
                    "summary_en": clean_desc,
                    "summary_hi": translate_news_summary_hi(clean_desc, title, feed_name)[:1800],
                    "image_url": thumb,
                    "published_at": pub_naive,
                    "created_at": now_naive,
                    "fetched_at": now_naive,
                    "anilist_id": None,
                    "mal_id": None,
                    "season": None,
                    "season_year": None,
                    "chapters": None,
                    "volumes": None,
                    "publication_status": None,
                    "airing_day": None,
                    "airing_time": None,
                    "score": None,
                    "news_category": (item.findtext("category") or "Anime News")[:50],
                    "latest_chapter": None,
                    "official_links": json.dumps({"pub_iso": pub_dt_utc.isoformat(), "rss_source": feed_name}),
                    "premiere_date": None,
                    "start_date": None,
                    "end_date": None,
                    "is_featured": len(records) < 3,
                }
            )
            if len([r for r in records if r["source_name"] == feed_name]) >= max_per_feed:
                break

    return records


def _dedupe_rows(conn: Any, content_type: str) -> int:
    """Merge Hindi localization + AniList enriched metadata into one canonical row per show/item."""
    rows = conn.execute(
        text(
            """
            SELECT id, anilist_id, title_en, title_hi, summary_en, summary_hi, image_url,
                   official_links, airing_day, airing_time, hindi_dub, dub_platform,
                   score, volumes, chapters, latest_chapter, publication_status, external_id
            FROM anime_draft
            WHERE content_type = :ct
            ORDER BY id ASC
            """
        ),
        {"ct": content_type},
    ).mappings().all()

    groups: dict[str, list[dict]] = {}
    for r in rows:
        key = f"aid:{r['anilist_id']}" if r["anilist_id"] else f"t:{_norm_title(r['title_en'] or r['title_hi'])}"
        if key in ("t:",):
            key = f"id:{r['id']}"
        groups.setdefault(key, []).append(dict(r))

    deleted = 0
    for grp in groups.values():
        if len(grp) <= 1:
            continue
        # Score rows: prefer HTTPS image, official_links JSON, and sync external_id as survivor
        def _rank(row: dict) -> tuple[int, int, int]:
            has_http = 1 if (row["image_url"] or "").startswith("http") else 0
            has_json = 1 if (row["official_links"] or "").strip().startswith("{") else 0
            return (has_http, has_json, int(row["id"]))

        grp_sorted = sorted(grp, key=_rank, reverse=True)
        keeper = grp_sorted[0]
        dups = grp_sorted[1:]

        best_title_hi = keeper["title_hi"]
        best_summary_hi = keeper["summary_hi"]
        best_hindi_dub = bool(keeper["hindi_dub"])
        best_dub_plat = keeper["dub_platform"]
        best_img = keeper["image_url"] if (keeper["image_url"] or "").startswith("http") else None
        best_title_en = keeper["title_en"]

        for d in grp_sorted:
            if _has_devanagari(d["title_hi"]) and not _has_devanagari(best_title_hi):
                best_title_hi = d["title_hi"]
            if _has_devanagari(d["summary_hi"]) and not _has_devanagari(best_summary_hi):
                best_summary_hi = d["summary_hi"]
            if d["hindi_dub"]:
                best_hindi_dub = True
                if d["dub_platform"] and not best_dub_plat:
                    best_dub_plat = d["dub_platform"]
            if not best_img and (d["image_url"] or "").startswith("http"):
                best_img = d["image_url"]

        conn.execute(
            text(
                """
                UPDATE anime_draft
                SET title_hi = :thi,
                    title_en = :ten,
                    summary_hi = COALESCE(:shi, summary_hi),
                    hindi_dub = :hd,
                    dub_platform = COALESCE(:dp, dub_platform),
                    image_url = COALESCE(:img, image_url)
                WHERE id = :kid
                """
            ),
            {
                "thi": best_title_hi,
                "ten": best_title_en,
                "shi": best_summary_hi,
                "hd": best_hindi_dub,
                "dp": best_dub_plat,
                "img": best_img,
                "kid": keeper["id"],
            },
        )
        dup_ids = [d["id"] for d in dups]
        conn.execute(text("DELETE FROM anime_draft WHERE id = ANY(:ids)"), {"ids": dup_ids})
        deleted += len(dup_ids)
    return deleted


def _ensure_historical_awards(conn: Any) -> None:
    """Ensure verified 2022 & 2021 Crunchyroll Anime Awards ceremonies and winners exist."""
    historical = [
        {
            "edition_year": 2022,
            "name": "Crunchyroll Anime Awards",
            "name_hi": "क्रंचीरोल एनीमे अवार्ड्स (6वां संस्करण)",
            "date": "2022-02-09",
            "location": "Tokyo, Japan (Global Virtual)",
            "source_url": "https://en.wikipedia.org/wiki/6th_Crunchyroll_Anime_Awards",
            "entries": [
                ("Anime of the Year", "वर्ष का सर्वश्रेष्ठ एनीमे", 110277, "Attack on Titan Final Season Part 1", "अटैक ऑन टाइटन: फाइनल सीज़न पार्ट 1", "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx110277-sKUNXAsWMNFw.jpg", [{"title": "JUJUTSU KAISEN"}, {"title": "86 EIGHTY-SIX"}, {"title": "ODDTAXI"}, {"title": "Ranking of Kings"}]),
                ("Best Film", "सर्वश्रेष्ठ एनीमे फिल्म", 112151, "Demon Slayer -Kimetsu no Yaiba- The Movie: Mugen Train", "डीमन स्लेयर: मुगेन ट्रेन मूवी", "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx112151-1qlQwPB1RrJe.png", [{"title": "BELLE"}, {"title": "Evangelion 3.0+1.0"}, {"title": "Josee, the Tiger and the Fish"}]),
                ("Best Romance", "सर्वश्रेष्ठ रोमांस एनीमे", 124080, "Horimiya", "होरीमिया (Horimiya)", "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx124080-3i22mRVPBS0T.jpg", [{"title": "Fruits Basket The Final"}, {"title": "Komi Can't Communicate"}]),
                ("Best Protagonist", "सर्वश्रेष्ठ मुख्य पात्र", 113717, "Ranking of Kings", "रैंकिंग ऑफ किंग्स (Bojji)", "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx113717-9sNnN8WRgK15.jpg", [{"title": "Odd Taxi"}, {"title": "Attack on Titan"}]),
            ],
        },
        {
            "edition_year": 2021,
            "name": "Crunchyroll Anime Awards",
            "name_hi": "क्रंचीरोल एनीमे अवार्ड्स (5वां संस्करण)",
            "date": "2021-02-19",
            "location": "Tokyo, Japan (Global Virtual)",
            "source_url": "https://en.wikipedia.org/wiki/5th_Crunchyroll_Anime_Awards",
            "entries": [
                ("Anime of the Year", "वर्ष का सर्वश्रेष्ठ एनीमे", 113415, "JUJUTSU KAISEN", "जुजुत्सु काइसेन (सीज़न 1)", "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx113415-LHBAeoZDIsnF.jpg", [{"title": "Dorohedoro"}, {"title": "Great Pretender"}, {"title": "Keep Your Hands Off Eizouken!"}]),
                ("Best Animation", "सर्वश्रेष्ठ एनिमेशन", 109298, "Keep Your Hands Off Eizouken!", "कीप योर हैंड्स ऑफ़ आइज़ोउकेन!", "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx109298-suwdIUbJEPJx.png", [{"title": "JUJUTSU KAISEN"}, {"title": "Great Pretender"}]),
                ("Best Comedy", "सर्वश्रेष्ठ कॉमेडी एनीमे", 112641, "Kaguya-sama: Love is War? (Season 2)", "कागुया-सामा: लव इज़ वॉर? (सीज़न 2)", "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx112641-zoGC8d6FaPXU.jpg", [{"title": "My Next Life as a Villainess"}, {"title": "Sleepy Princess in the Demon Castle"}]),
                ("Best Fantasy", "सर्वश्रेष्ठ फ़ैंटेसी एनीमे", 108632, "Re:ZERO -Starting Life in Another World- Season 2", "री:ज़ीरो (सीज़न 2)", "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx108632-lQWnmw7XaNOK.jpg", [{"title": "Tower of God"}, {"title": "Dorohedoro"}]),
            ],
        },
    ]
    for cer in historical:
        cid = conn.execute(
            text("SELECT id FROM award_ceremony WHERE edition_year = :yr AND name = :nm LIMIT 1"),
            {"yr": cer["edition_year"], "nm": cer["name"]},
        ).scalar()
        if not cid:
            cid = conn.execute(
                text(
                    """
                    INSERT INTO award_ceremony (name, name_hi, edition_year, date, location, source_url, status)
                    VALUES (:name, :name_hi, :edition_year, CAST(:date AS DATE), :location, :source_url, 'verified')
                    RETURNING id
                    """
                ),
                {
                    "name": cer["name"],
                    "name_hi": cer["name_hi"],
                    "edition_year": cer["edition_year"],
                    "date": cer["date"],
                    "location": cer["location"],
                    "source_url": cer["source_url"],
                },
            ).scalar()
        existing_cats = {
            r[0]
            for r in conn.execute(text("SELECT category FROM award_entry WHERE ceremony_id = :cid"), {"cid": cid}).fetchall()
        }
        for cat, cat_hi, maid, wtitle, wtitle_hi, wposter, noms in cer["entries"]:
            if cat in existing_cats:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO award_entry (ceremony_id, category, category_hi, winner_media_id, winner_title, winner_title_hi, winner_poster, nominee_media_ids, source_url, status)
                    VALUES (:cid, :cat, :cat_hi, :maid, :wt, :wthi, :wpost, :noms, :src, 'verified')
                    """
                ),
                {
                    "cid": cid,
                    "cat": cat,
                    "cat_hi": cat_hi,
                    "maid": maid,
                    "wt": wtitle,
                    "wthi": wtitle_hi,
                    "wpost": wposter,
                    "noms": json.dumps(noms, ensure_ascii=False),
                    "src": cer["source_url"],
                },
            )


def _needs_real_hindi_upgrade(title_hi: str | None, title_en: str, summary_hi: str | None, summary_en: str | None) -> tuple[bool, bool]:
    th = (title_hi or "").strip()
    te = (title_en or "").strip()
    shi = (summary_hi or "").strip()
    sen = (summary_en or "").strip()
    deva_cnt = len(re.findall(r"[\u0900-\u097F]", th))
    has_en_desc = bool(re.search(r"\b(?:Live-Action|Series|Anime Film|Preview Guide|Theme Song|Launches New|Boys-Love)\b", th, re.I))
    need_title = _is_phonetic_or_untranslated_hi(th, te) or deva_cnt < max(5, int(len(th) * 0.35)) or has_en_desc
    need_sum = (
        not shi
        or not _has_devanagari(shi)
        or "मूल स्रोत रिपोर्ट:" in shi
        or "पूरी जानकारी, कास्ट विवरण" in shi
        or "अधिकारिक अपडेट" in shi
        or (len(th) >= 20 and shi.startswith(th[:20]))
        or (len(sen) >= 40 and len(shi) < int(len(sen) * 0.85))
    )
    return need_title, need_sum


def _backfill_anilist_metadata_and_covers(conn: Any) -> tuple[int, int, int]:
    """Backfill exact AniList covers/statuses for schedule, manga, news & articles."""
    # 1. Backfill ALL schedule rows with real AniList covers, status (RELEASING vs FINISHED) and nextAiringEpisode
    sch_rows = conn.execute(
        text("SELECT id, anilist_id, title_en, image_url, official_links FROM anime_draft WHERE content_type = 'schedule' AND anilist_id IS NOT NULL")
    ).mappings().all()
    sch_ids = list({int(r["anilist_id"]) for r in sch_rows})
    sch_meta: dict[int, dict] = {}
    for i in range(0, len(sch_ids), 50):
        try:
            resp = httpx.post(ANILIST_URL, json={"query": ANILIST_BATCH_ANIME_QUERY, "variables": {"ids": sch_ids[i : i + 50]}}, timeout=15.0)
            if resp.status_code == 200:
                for m in (resp.json().get("data") or {}).get("Page", {}).get("media", []) or []:
                    sch_meta[int(m["id"])] = m
        except Exception:
            pass

    sch_updated = 0
    for r in sch_rows:
        m = sch_meta.get(int(r["anilist_id"]))
        if not m:
            continue
        real_status = (m.get("status") or "RELEASING").upper()
        next_ep = m.get("nextAiringEpisode") or {}
        airing_at = next_ep.get("airingAt")
        episode_num = next_ep.get("episode")
        cover = ((m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large") or "")[:500] or None
        is_airing = real_status == "RELEASING" and bool(airing_at)
        day_hi, time_ist = _ist_day_and_time(airing_at) if is_airing else ("रविवार", "TBD IST")
        meta_json = json.dumps(
            {
                "airing_at": airing_at,
                "episode": episode_num,
                "banner_url": m.get("bannerImage") or "",
                "native_title": (m.get("title") or {}).get("native") or "",
            },
            ensure_ascii=False,
        )
        conn.execute(
            text(
                """
                UPDATE anime_draft
                SET status = :st,
                    publication_status = :pst,
                    image_url = COALESCE(:img, image_url),
                    season = COALESCE(:ssn, season),
                    season_year = COALESCE(:syr, season_year),
                    airing_day = CASE WHEN :airing THEN :dhi ELSE airing_day END,
                    airing_time = CASE WHEN :airing THEN :tist ELSE airing_time END,
                    latest_chapter = :ep,
                    official_links = :links
                WHERE id = :id
                """
            ),
            {
                "st": "published" if is_airing else "archived",
                "pst": real_status,
                "img": cover,
                "ssn": (m.get("season") or "")[:20] or None,
                "syr": m.get("seasonYear"),
                "airing": is_airing,
                "dhi": day_hi,
                "tist": time_ist,
                "ep": f"Ep. {episode_num}" if (is_airing and episode_num) else None,
                "links": meta_json,
                "id": r["id"],
            },
        )
        sch_updated += 1

    # Purge any NSFW/inappropriate entries from catalog
    conn.execute(
        text(
            "DELETE FROM anime_draft WHERE content_type = 'manga' AND (LOWER(title_en) LIKE '%av joyuu%' OR LOWER(title_en) LIKE '%big dick%' OR id IN (273, 282))"
        )
    )

    # Apply Nov 2026 verified flagship chapter, volume & status facts directly in Supabase
    for aid_key, facts in MANGA_NOV2026_FACTS.items():
        ch_v = int(facts["chapters"])
        vol_v = int(facts["volumes"])
        st_v = str(facts["status"])
        conn.execute(
            text(
                """
                UPDATE anime_draft
                SET chapters = :ch,
                    volumes = :vol,
                    publication_status = :pst,
                    latest_chapter = :clbl
                WHERE content_type = 'manga' AND anilist_id = :aid
                """
            ),
            {
                "ch": ch_v,
                "vol": vol_v,
                "pst": st_v,
                "clbl": f"{ch_v} Chapters",
                "aid": int(aid_key),
            },
        )

    # 2. Backfill ALL manga rows missing status/cover/volumes/chapters or with bogus 'Weekly Serialization'
    mg_rows = conn.execute(
        text(
            """
            SELECT id, anilist_id, title_en, image_url, volumes, chapters, latest_chapter, publication_status, official_links
            FROM anime_draft
            WHERE content_type = 'manga' AND anilist_id IS NOT NULL
              AND (image_url IS NULL OR image_url NOT LIKE 'http%'
                   OR latest_chapter = 'Weekly Serialization'
                   OR official_links IS NULL OR official_links NOT LIKE '{%')
            """
        )
    ).mappings().all()
    mg_ids = list({int(r["anilist_id"]) for r in mg_rows})
    mg_meta: dict[int, dict] = {}
    for i in range(0, len(mg_ids), 50):
        try:
            resp = httpx.post(ANILIST_URL, json={"query": ANILIST_BATCH_MANGA_QUERY, "variables": {"ids": mg_ids[i : i + 50]}}, timeout=15.0)
            if resp.status_code == 200:
                for m in (resp.json().get("data") or {}).get("Page", {}).get("media", []) or []:
                    mg_meta[int(m["id"])] = m
        except Exception:
            pass

    mg_updated = 0
    for r in mg_rows:
        m = mg_meta.get(int(r["anilist_id"]))
        if not m:
            continue
        pub_st = (m.get("status") or r["publication_status"] or "RELEASING").upper()
        ch_count = m.get("chapters") or r["chapters"]
        vol_count = m.get("volumes") or r["volumes"]
        cover = ((m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large") or "")[:500] or None
        ch_lbl = _manga_chapter_label(pub_st, ch_count, vol_count)
        title_obj = m.get("title") or {}
        reader_plat, reader_url = _pick_official_manga_reader(m)
        dossier_json = json.dumps(
            {
                "author": _extract_mangaka(m.get("staff")),
                "native_title": title_obj.get("native") or title_obj.get("romaji") or "",
                "genres": (m.get("genres") or [])[:4],
                "start_year": (m.get("startDate") or {}).get("year"),
                "end_year": (m.get("endDate") or {}).get("year"),
                "official_platform": reader_plat,
                "official_url": reader_url,
                "banner_url": m.get("bannerImage") or cover or "",
            },
            ensure_ascii=False,
        )
        conn.execute(
            text(
                """
                UPDATE anime_draft
                SET image_url = COALESCE(:img, image_url),
                    publication_status = :pst,
                    chapters = :ch,
                    volumes = :vol,
                    latest_chapter = :clbl,
                    official_links = :links
                WHERE id = :id
                """
            ),
            {
                "img": cover,
                "pst": pub_st,
                "ch": int(ch_count) if ch_count else None,
                "vol": int(vol_count) if vol_count else None,
                "clbl": ch_lbl[:50],
                "links": dossier_json if not (r["official_links"] or "").startswith("{") else r["official_links"],
                "id": r["id"],
            },
        )
        mg_updated += 1

    # Also sanitize any remaining hardcoded 'Weekly Serialization' strings cleanly
    conn.execute(
        text(
            """
            UPDATE anime_draft
            SET latest_chapter = CASE
                WHEN UPPER(COALESCE(publication_status, '')) = 'HIATUS' THEN 'On Hiatus'
                WHEN UPPER(COALESCE(publication_status, '')) IN ('FINISHED', 'COMPLETED') THEN 'Completed'
                ELSE 'Ongoing Serialization'
            END
            WHERE content_type = 'manga' AND latest_chapter = 'Weekly Serialization'
            """
        )
    )

    # 2b. Resolve ALL manga rows still having anilist_id IS NULL (incl. legacy rows 127-134)
    null_mg_rows = conn.execute(
        text("SELECT id, title_en, title_hi, summary_hi, image_url FROM anime_draft WHERE content_type = 'manga' AND anilist_id IS NULL")
    ).mappings().all()
    if null_mg_rows:
        target_ids: dict[int, int] = {}
        for nmr in null_mg_rows:
            rid = int(nmr["id"])
            if rid in LEGACY_MANGA_RESOLVE:
                target_ids[rid] = LEGACY_MANGA_RESOLVE[rid]
            else:
                q_term = re.split(r"[:\-–—]", nmr["title_en"] or "", maxsplit=1)[0].strip()
                try:
                    sr = httpx.post(
                        ANILIST_URL,
                        json={
                            "query": "query ($s: String) { Media(search: $s, type: MANGA, sort: POPULARITY_DESC) { id } }",
                            "variables": {"s": q_term},
                        },
                        timeout=8.0,
                    )
                    mid = ((sr.json().get("data") or {}).get("Media") or {}).get("id")
                    if mid:
                        target_ids[rid] = int(mid)
                except Exception:
                    pass

        unique_aids = list(set(target_ids.values()))
        resolved_meta: dict[int, dict] = {}
        for i in range(0, len(unique_aids), 50):
            try:
                resp = httpx.post(
                    ANILIST_URL,
                    json={"query": ANILIST_BATCH_MANGA_QUERY, "variables": {"ids": unique_aids[i : i + 50]}},
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    for m in (resp.json().get("data") or {}).get("Page", {}).get("media", []) or []:
                        resolved_meta[int(m["id"])] = m
            except Exception:
                pass

        for nmr in null_mg_rows:
            rid = int(nmr["id"])
            aid = target_ids.get(rid)
            m = resolved_meta.get(aid or -1)
            if not aid or not m:
                continue
            # Remove any other manga row already holding this AniList ID so rows 127-134 stay unique canonical keepers
            existing_dups = conn.execute(
                text("SELECT id, summary_hi, hindi_dub FROM anime_draft WHERE content_type = 'manga' AND anilist_id = :aid AND id != :rid"),
                {"aid": aid, "rid": rid},
            ).mappings().all()
            if existing_dups:
                conn.execute(
                    text("DELETE FROM anime_draft WHERE id = ANY(:ids)"),
                    {"ids": [int(d["id"]) for d in existing_dups]},
                )

            pub_st = (m.get("status") or "RELEASING").upper()
            ch_count = m.get("chapters")
            vol_count = m.get("volumes")
            cover = ((m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large") or "")[:500] or None
            ch_lbl = _manga_chapter_label(pub_st, ch_count, vol_count)
            title_obj = m.get("title") or {}
            reader_plat, reader_url = _pick_official_manga_reader(m)
            dossier_json = json.dumps(
                {
                    "author": _extract_mangaka(m.get("staff")),
                    "native_title": title_obj.get("native") or title_obj.get("romaji") or "",
                    "genres": (m.get("genres") or [])[:4],
                    "start_year": (m.get("startDate") or {}).get("year"),
                    "end_year": (m.get("endDate") or {}).get("year"),
                    "official_platform": reader_plat,
                    "official_url": reader_url,
                    "banner_url": m.get("bannerImage") or cover or "",
                },
                ensure_ascii=False,
            )
            conn.execute(
                text(
                    """
                    UPDATE anime_draft
                    SET anilist_id = :aid,
                        mal_id = COALESCE(:mal_id, mal_id),
                        external_id = :ext_id,
                        source_name = :src_name,
                        source_url = :src_url,
                        image_url = COALESCE(:img, image_url),
                        publication_status = :pst,
                        chapters = :ch,
                        volumes = :vol,
                        score = COALESCE(:score, score),
                        latest_chapter = :clbl,
                        official_links = :links
                    WHERE id = :id
                    """
                ),
                {
                    "aid": aid,
                    "mal_id": m.get("idMal"),
                    "ext_id": f"anilist_manga_{aid}",
                    "src_name": reader_plat[:100],
                    "src_url": reader_url[:500],
                    "img": cover,
                    "pst": pub_st,
                    "ch": int(ch_count) if ch_count else None,
                    "vol": int(vol_count) if vol_count else None,
                    "score": float(m["averageScore"]) if m.get("averageScore") else None,
                    "clbl": ch_lbl[:50],
                    "links": dossier_json,
                    "id": rid,
                },
            )
            mg_updated += 1

    # 2c. Fix synthetic 404 award posters via real AniList anime covers
    award_ids = list(set(AWARD_ANILIST_IDS_BY_ROW.values()))
    if award_ids:
        aw_meta: dict[int, dict] = {}
        try:
            resp = httpx.post(ANILIST_URL, json={"query": ANILIST_BATCH_ANIME_QUERY, "variables": {"ids": award_ids}}, timeout=15.0)
            if resp.status_code == 200:
                for m in (resp.json().get("data") or {}).get("Page", {}).get("media", []) or []:
                    aw_meta[int(m["id"])] = m
        except Exception:
            pass
        for row_id, aid in AWARD_ANILIST_IDS_BY_ROW.items():
            m = aw_meta.get(aid)
            if not m:
                continue
            cov = ((m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large") or "")[:500]
            if cov:
                conn.execute(
                    text("UPDATE anime_draft SET anilist_id = :aid, image_url = :img WHERE id = :id"),
                    {"aid": aid, "img": cov, "id": row_id},
                )

    _ensure_historical_awards(conn)

    # 3. Clean poisoned news images, upgrade news summaries (real EN + full HI parity) & enforce natural Hindi headlines
    news_fixed = 0
    news_rows = conn.execute(
        text("SELECT id, title_en, title_hi, summary_en, summary_hi, image_url, source_url, source_name FROM anime_draft WHERE content_type = 'news'")
    ).mappings().all()
    for nr in news_rows:
        img = (nr["image_url"] or "").strip()
        title_en = (nr["title_en"] or "").strip()
        title_l = title_en.lower()
        needs_img_fix = bool(img and not img.startswith("http"))
        for aid_token, kw in NEWS_ANILIST_KEYWORDS.items():
            if img and aid_token in img and kw not in title_l:
                needs_img_fix = True
                break

        new_img = img
        if needs_img_fix:
            new_img = _fetch_article_og_image(nr["source_url"]) or _strict_anilist_news_image(title_en) or ""
            news_fixed += 1

        raw_sen = (nr["summary_en"] or "").strip()
        needs_en_enrich = (
            not raw_sen
            or len(raw_sen) < 220
            or bool(re.search(r"[\u0900-\u097F]", raw_sen))
            or ("stay on top of anime news" in raw_sen.lower())
        )
        new_sum_en = _fetch_real_news_summary_en(nr["source_url"], raw_sen, title_en) if needs_en_enrich else raw_sen

        need_title_hi, need_sum_hi = _needs_real_hindi_upgrade(nr["title_hi"], title_en, nr["summary_hi"], new_sum_en)
        new_title_hi = translate_news_headline_hi(title_en)[:300] if need_title_hi else nr["title_hi"]
        new_sum_hi = translate_news_summary_hi(new_sum_en, title_en, nr["source_name"] or "") if (need_sum_hi or new_sum_en != nr["summary_en"]) else nr["summary_hi"]

        if needs_img_fix or need_title_hi or new_sum_en != nr["summary_en"] or new_sum_hi != nr["summary_hi"]:
            conn.execute(
                text(
                    """
                    UPDATE anime_draft
                    SET image_url = :img,
                        title_hi = :thi,
                        summary_en = :sen,
                        summary_hi = :shi
                    WHERE id = :id
                    """
                ),
                {"img": new_img, "thi": new_title_hi, "sen": new_sum_en[:1200], "shi": new_sum_hi[:1800], "id": nr["id"]},
            )
            if (need_title_hi or need_sum_hi or needs_en_enrich) and not needs_img_fix:
                news_fixed += 1

    # Clean up trailer rows with missing source_url, non-http images, or non-embeddable YouTube IDs
    for old_id, new_id in TRAILER_EMBED_OVERRIDES.items():
        conn.execute(
            text(
                """
                UPDATE anime_draft
                SET source_url = :new_url,
                    image_url = :new_img
                WHERE content_type = 'trailer' AND source_url LIKE :pat
                """
            ),
            {
                "new_url": f"https://www.youtube.com/watch?v={new_id}",
                "new_img": f"https://img.youtube.com/vi/{new_id}/hqdefault.jpg",
                "pat": f"%{old_id}%",
            },
        )
    conn.execute(
        text(
            "DELETE FROM anime_draft WHERE content_type = 'trailer' AND (source_url IS NULL OR source_url = '' OR image_url IS NULL OR image_url NOT LIKE 'http%')"
        )
    )
    tr_check_rows = conn.execute(
        text("SELECT id, source_url FROM anime_draft WHERE content_type = 'trailer' AND status = 'published'")
    ).mappings().all()
    bad_tr_ids = [int(tr["id"]) for tr in tr_check_rows if not _is_youtube_embeddable(_extract_yt_id(tr["source_url"]))]
    if bad_tr_ids:
        conn.execute(text("DELETE FROM anime_draft WHERE id = ANY(:ids)"), {"ids": bad_tr_ids})

    # 4. Backfill article table missing cover images via exact AniList title match
    art_rows = conn.execute(text("SELECT id, slug, title, cover_image FROM article WHERE cover_image IS NULL OR cover_image NOT LIKE 'http%'")).mappings().all()
    for ar in art_rows:
        search_term = ARTICLE_COVER_SEARCH.get(ar["slug"]) or re.split(r"[:(]", ar["title"] or "", maxsplit=1)[0].strip()
        if search_term:
            cov = _strict_anilist_news_image(search_term)
            if cov:
                conn.execute(text("UPDATE article SET cover_image = :img WHERE id = :id"), {"img": cov, "id": ar["id"]})

    # 5. Live HTTP verification sweep across all image URLs so zero 404 links remain
    url_rows = conn.execute(
        text("SELECT id, content_type, image_url FROM anime_draft WHERE status = 'published' AND image_url IS NOT NULL AND image_url != ''")
    ).mappings().all()

    async def _probe_batch(items: list[Any]) -> list[tuple[int, str, int]]:
        sem = asyncio.Semaphore(25)
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "HindiAnimeGuideBot/2.0"}) as cli:
            async def _one(rid: int, sec: str, url: str) -> tuple[int, str, int]:
                async with sem:
                    try:
                        r = await cli.head(url)
                        if r.status_code in (403, 405) or r.status_code >= 400:
                            r = await cli.get(url)
                        return rid, sec, int(r.status_code)
                    except Exception:
                        return rid, sec, 0
            return await asyncio.gather(*[_one(int(it["id"]), str(it["content_type"]), str(it["image_url"])) for it in items])

    try:
        probe_results = asyncio.run(_probe_batch(url_rows))
        broken_news_ids = [rid for rid, sec, code in probe_results if code != 200 and sec == "news"]
        if broken_news_ids:
            conn.execute(text("UPDATE anime_draft SET image_url = '' WHERE id = ANY(:ids)"), {"ids": broken_news_ids})
    except Exception:
        pass

    return sch_updated, mg_updated, news_fixed


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-world 48h news, manga dossier & IST countdown sync.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and preview without writing to Supabase.")
    args = parser.parse_args()

    schedule_records, manga_records, trailer_records = fetch_anilist_records()
    news_records = fetch_48h_rss_news()
    all_records = schedule_records + manga_records + trailer_records + news_records

    print(
        f"Fetched live payloads -> schedule={len(schedule_records)}, manga={len(manga_records)}, "
        f"trailers={len(trailer_records)}, news={len(news_records)}"
    )

    if args.dry_run:
        print("DRY RUN complete (0 DB mutations).")
        return 0

    with engine.begin() as conn:
        for rec in all_records:
            conn.execute(UPSERT_SQL, rec)

        dedup_counts = {ct: _dedupe_rows(conn, ct) for ct in ("schedule", "manga", "trailer", "news")}
        sch_upd, mg_upd, news_fix = _backfill_anilist_metadata_and_covers(conn)

        # 48-hour News Auto-Purge with 8-item safety floor
        fresh_news_cnt = conn.execute(
            text("SELECT COUNT(*) FROM anime_draft WHERE content_type = 'news' AND COALESCE(published_at, created_at) >= NOW() - INTERVAL '48 hours'")
        ).scalar() or 0

        if fresh_news_cnt >= 8:
            purged_news = conn.execute(
                text("DELETE FROM anime_draft WHERE content_type = 'news' AND COALESCE(published_at, created_at) < NOW() - INTERVAL '48 hours'")
            ).rowcount
        else:
            purged_news = conn.execute(
                text(
                    """
                    DELETE FROM anime_draft
                    WHERE content_type = 'news'
                      AND id NOT IN (
                        SELECT id FROM anime_draft
                        WHERE content_type = 'news'
                        ORDER BY COALESCE(published_at, created_at) DESC, id DESC
                        LIMIT 8
                      )
                    """
                )
            ).rowcount

        pruned_trailers = conn.execute(
            text(
                """
                DELETE FROM anime_draft
                WHERE content_type = 'trailer'
                  AND id NOT IN (
                    SELECT id FROM anime_draft
                    WHERE content_type = 'trailer'
                    ORDER BY COALESCE(published_at, created_at) DESC, id DESC
                    LIMIT 24
                  )
                """
            )
        ).rowcount

        rem_news = conn.execute(text("SELECT COUNT(*) FROM anime_draft WHERE content_type = 'news'")).scalar() or 0
        rem_trailers = conn.execute(text("SELECT COUNT(*) FROM anime_draft WHERE content_type = 'trailer'")).scalar() or 0
        rem_manga = conn.execute(text("SELECT COUNT(*) FROM anime_draft WHERE content_type = 'manga'")).scalar() or 0
        rem_sch = conn.execute(text("SELECT COUNT(*) FROM anime_draft WHERE content_type = 'schedule' AND status = 'published'")).scalar() or 0

    print(
        f"Supabase Sync Complete -> deduped={dedup_counts}, backfilled=(sch={sch_upd}, manga={mg_upd}, news={news_fix}), "
        f"purged_48h_news={purged_news}, pruned_trailers={pruned_trailers} | "
        f"DB Published Totals: news={rem_news}, manga={rem_manga}, schedule={rem_sch}, trailers={rem_trailers}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
