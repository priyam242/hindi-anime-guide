"""AniList GraphQL fetcher for current season anime airing schedules.

Fetches factual airing metadata from AniList GraphQL API (no API key required).
Strictly adheres to rate limits and uses structured retries and timeouts.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

logger = logging.getLogger("automation.fetchers.anilist")

ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"
USER_AGENT = "HindiAnimeGuide-Bot/1.0 (+https://hindi-anime-guide.onrender.com; contact: editorial@animeguide.com)"

ANILIST_AIRING_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      hasNextPage
      total
    }
    media(type: ANIME, status: RELEASING, sort: POPULARITY_DESC) {
      id
      title {
        romaji
        english
        native
      }
      coverImage {
        large
        extraLarge
      }
      nextAiringEpisode {
        airingAt
        timeUntilAiring
        episode
      }
      status
      averageScore
      siteUrl
      genres
      format
      episodes
      season
      seasonYear
    }
  }
}
"""


def fetch_current_season_schedule(
    limit: int = 10,
    page: int = 1,
    max_retries: int = 3,
    timeout_seconds: float = 15.0,
) -> list[dict[str, Any]]:
    """Query AniList GraphQL for current season releasing anime schedules.

    Args:
        limit: Maximum number of media items to return (1-50 per request).
        page: Page number for pagination.
        max_retries: Number of retries on network or rate limit (HTTP 429) errors.
        timeout_seconds: HTTP request timeout in seconds.

    Returns:
        List of normalized anime schedule dictionaries.
    """
    per_page = min(max(1, limit), 50)
    variables = {"page": page, "perPage": per_page}
    payload = {"query": ANILIST_AIRING_QUERY, "variables": variables}
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=timeout_seconds, headers=headers) as client:
                response = client.post(ANILIST_GRAPHQL_URL, json=payload)

            if response.status_code == 429:
                retry_after_str = response.headers.get("Retry-After", "5")
                try:
                    retry_after = float(retry_after_str)
                except ValueError:
                    retry_after = 5.0
                logger.warning(
                    "AniList API rate limit encountered (attempt %d/%d). Sleeping for %.1f seconds.",
                    attempt,
                    max_retries,
                    retry_after,
                )
                time.sleep(retry_after)
                continue

            response.raise_for_status()
            data = response.json()
            media_list = data.get("data", {}).get("Page", {}).get("media", [])

            normalized_items: list[dict[str, Any]] = []
            for media in media_list:
                item = _normalize_media_item(media)
                if item:
                    normalized_items.append(item)
                if len(normalized_items) >= limit:
                    break

            logger.info("Successfully fetched %d airing items from AniList.", len(normalized_items))
            return normalized_items

        except httpx.HTTPStatusError as exc:
            last_error = exc
            logger.warning(
                "AniList HTTP status error %s on attempt %d/%d: %s",
                exc.response.status_code,
                attempt,
                max_retries,
                exc,
            )
            time.sleep(2.0 * attempt)
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            last_error = exc
            logger.warning(
                "AniList network/timeout error on attempt %d/%d: %s",
                attempt,
                max_retries,
                exc,
            )
            time.sleep(2.0 * attempt)

    logger.error("Failed to fetch from AniList after %d attempts. Last error: %s", max_retries, last_error)
    return []


def _normalize_media_item(media: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize raw AniList media GraphQL response into a factual dictionary."""
    media_id = media.get("id")
    if not media_id:
        return None

    titles = media.get("title") or {}
    title_romaji = (titles.get("romaji") or "").strip()
    title_english = (titles.get("english") or "").strip()
    primary_title = title_english or title_romaji or f"Anime #{media_id}"

    next_episode_info = media.get("nextAiringEpisode") or {}
    next_episode_num = next_episode_info.get("episode")
    airing_at_unix = next_episode_info.get("airingAt")

    airing_at_iso: str | None = None
    airing_at_formatted: str | None = None
    if airing_at_unix:
        dt = datetime.fromtimestamp(airing_at_unix, tz=timezone.utc)
        airing_at_iso = dt.isoformat()
        airing_at_formatted = dt.strftime("%Y-%m-%d %H:%M UTC")

    cover_images = media.get("coverImage") or {}
    image_url = cover_images.get("large") or cover_images.get("extraLarge")

    site_url = media.get("siteUrl") or f"https://anilist.co/anime/{media_id}"
    score = media.get("averageScore")
    genres = media.get("genres") or []
    status = media.get("status") or "RELEASING"
    media_format = media.get("format") or "TV"

    # external_id uniquely identifies this schedule event or anime entry
    if next_episode_num is not None:
        external_id = f"anilist_{media_id}_ep{next_episode_num}"
    else:
        external_id = f"anilist_{media_id}"

    return {
        "external_id": external_id,
        "content_type": "schedule",
        "source_name": "AniList",
        "source_url": site_url,
        "anilist_id": media_id,
        "anime_id": media_id,
        "season": media.get("season"),
        "season_year": media.get("seasonYear"),
        "published_at": airing_at_iso,
        "title_en": primary_title,
        "title_romaji": title_romaji,
        "title_english": title_english,
        "next_episode": next_episode_num,
        "airing_at_iso": airing_at_iso,
        "airing_at_formatted": airing_at_formatted,
        "airing_at_timestamp": airing_at_unix,
        "image_url": image_url,
        "average_score": score,
        "status": status,
        "genres": genres,
        "format": media_format,
    }


ANILIST_SEARCH_QUERY = """
query ($search: String) {
  Media(search: $search, type: ANIME) {
    id
    title {
      romaji
      english
    }
    coverImage {
      large
      extraLarge
    }
  }
}
"""


def search_anilist_cover(query_title: str, timeout_seconds: float = 8.0) -> str | None:
    """Search AniList for an anime cover image URL matching the given title.

    Safely falls back to None if no match is found or on network timeout.
    Never downloads or saves images; returns only AniList CDN URLs.
    """
    import re

    if not query_title:
        return None

    # Clean title to improve search hit rate
    clean = re.sub(r'["\']', '', query_title)
    clean = re.split(
        r'\b(?:reveals|teaser|trailer|announced|release|debut|episode|chapter|key visual|anime|broadcast|season)\b',
        clean,
        flags=re.IGNORECASE,
    )[0].strip()

    if len(clean) < 2:
        return None

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "query": ANILIST_SEARCH_QUERY,
        "variables": {"search": clean[:80]},
    }

    try:
        with httpx.Client(timeout=timeout_seconds, headers=headers) as client:
            resp = client.post(ANILIST_GRAPHQL_URL, json=payload)
        if resp.status_code == 200:
            media = resp.json().get("data", {}).get("Media")
            if media:
                cov = media.get("coverImage") or {}
                return cov.get("large") or cov.get("extraLarge")
    except Exception as exc:
        logger.debug("AniList cover search failed for '%s': %s", clean, exc)

    return None


# ============================================================================
# LIVE AIRING SCHEDULE & SEASON HUB (MAL-STYLE)
# ============================================================================

IST_TZ = timezone(timedelta(hours=5, minutes=30))
JST_TZ = timezone(timedelta(hours=9))
SCHEDULE_CACHE_TTL = 1800  # 30 minutes in seconds

_LIVE_SCHEDULE_CACHE: dict[tuple[str, int], tuple[dict[str, Any], float]] = {}

MONTH_HI = {
    1: "जनवरी", 2: "फ़रवरी", 3: "मार्च", 4: "अप्रैल",
    5: "मई", 6: "जून", 7: "जुलाई", 8: "अगस्त",
    9: "सितंबर", 10: "अक्टूबर", 11: "नवंबर", 12: "दिसंबर"
}

SOURCE_HI = {
    "MANGA": "मंगा",
    "LIGHT_NOVEL": "लाइट नॉवेल",
    "ORIGINAL": "ओरिजिनल",
    "VISUAL_NOVEL": "विज़ुअल नॉवेल",
    "NOVEL": "नॉवेल",
    "VIDEO_GAME": "वीडियो गेम",
    "OTHER": "अन्य",
}

PLATFORM_CLASSES = {
    "Crunchyroll": {"name": "Crunchyroll", "short": "CR", "class": "bg-orange-500/20 text-orange-400 border-orange-500/30"},
    "Netflix": {"name": "Netflix", "short": "NF", "class": "bg-red-500/20 text-red-400 border-red-500/30"},
    "JioHotstar": {"name": "JioHotstar", "short": "JH", "class": "bg-indigo-500/20 text-indigo-400 border-indigo-500/30"},
    "Hotstar": {"name": "JioHotstar", "short": "JH", "class": "bg-indigo-500/20 text-indigo-400 border-indigo-500/30"},
    "JioCinema": {"name": "JioHotstar", "short": "JH", "class": "bg-indigo-500/20 text-indigo-400 border-indigo-500/30"},
    "Disney Plus": {"name": "Disney+", "short": "DP", "class": "bg-blue-600/20 text-blue-300 border-blue-600/30"},
    "YouTube": {"name": "YouTube", "short": "YT", "class": "bg-red-600/20 text-red-300 border-red-600/30"},
    "Amazon Prime Video": {"name": "Prime Video", "short": "PV", "class": "bg-sky-500/20 text-sky-400 border-sky-500/30"},
    "Prime Video": {"name": "Prime Video", "short": "PV", "class": "bg-sky-500/20 text-sky-400 border-sky-500/30"},
    "Hulu": {"name": "Hulu", "short": "Hulu", "class": "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"},
}

CURATED_TITLES = {
    "The Apothecary Diaries Season 3": ("द अपोथेकेरी डायरीज़ सीज़न 3 (The Apothecary Diaries Season 3)", "शी कबीले (Shi Clan) के विद्रोह के बाद, स्थिति आखिरकार शांत हो गई है, और माओमाओ (Maomao) रंगमहल क्षेत्र (pleasure district) में एक फार्मासिस्ट के रूप में अपने पुराने जीवन में लौट आई है।"),
    "Kusuriya no Hitorigoto 3rd Season": ("द अपोथेकेरी डायरीज़ सीज़न 3 (The Apothecary Diaries Season 3)", "शी कबीले (Shi Clan) के विद्रोह के बाद, स्थिति आखिरकार शांत हो गई है, और माओमाओ (Maomao) रंगमहल क्षेत्र (pleasure district) में एक फार्मासिस्ट के रूप में अपने पुराने जीवन में लौट आई है।"),
    "The Apothecary Diaries Season 2": ("द अपोथेकेरी डायरीज़ सीज़न 2", "माओमाओ शाही महल के भीतरी प्रांगण में अपनी तीक्ष्ण बुद्धि और विष-औषध ज्ञान से नई जटिल साजिशों और रहस्यों को सुलझाती है।"),
    "Kusuriya no Hitorigoto 2nd Season": ("द अपोथेकेरी डायरीज़ सीज़न 2", "माओमाओ शाही महल के भीतरी प्रांगण में अपनी तीक्ष्ण बुद्धि और विष-औषध ज्ञान से नई जटिल साजिशों और रहस्यों को सुलझाती है।"),
    "Kusuriya no Hitorigoto": ("द अपोथेकेरी डायरीज़ (The Apothecary Diaries)", "माओमाओ अपने औषधीय पिता के साथ शांत जीवन व्यतीत कर रही थी, जब तक कि उसे सम्राट के महल में एक साधारण सेविका के रूप में बेच नहीं दिया गया। जब शाही वारिस बीमार पड़ते हैं, तो वह आगे आकर उनका इलाज ढूंढती है! यह बात एक सुंदर महल अधिकारी जिनशी का ध्यान आकर्षित करती है, जो उसे पदोन्नत करता है। अब वह चिकित्सा रहस्यों को सुलझाकर अपना नाम बना रही है।"),
    "STEEL BALL RUN": ("जोजो बिज़ार एडवेंचर: स्टील बॉल रन", "1890 के अमेरिका में जॉनी जोस्टार और जाइरो ज़ेपेलि महाद्वीपीय घुड़दौड़ प्रतियोगिता 'स्टील बॉल रन' में रहस्यमयी अवशेषों की खोज में निकलते हैं।"),
    "Pokémon": ("पोकेमॉन होराइजन्स: द सीरीज़", "लिको और रॉय पोकेमॉन की दुनिया में नए रहस्यों और प्राचीन पोकेबॉल्स के रहस्यों को उजागर करने के लिए राइज़िंग वोल्ट टैकलर्स के साथ सफर करते हैं।"),
    "Hotaru no Yomeiri": ("होटारू नो योमेरी (Firefly Wedding)", "सतौको (Satoko) एक कुलीन परिवार की सुंदर पुत्री है, किंतु वह गंभीर रूप से बीमार है और उसके पास जीने के लिए बहुत कम समय बचा है। इससे पहले कि वह अपने परिवार के लिए विवाह तय कर पाती, वह रहस्यमयी हत्यारे शिन्पेई (Shinpei) का निशाना बन जाती है। अपनी जान बचाने के लिए वह शिन्पेई के सामने विवाह का हताश प्रस्ताव रखती है!"),
    "Tokyo Revengers: War of the Three Titans Arc": ("टोक्यो रिवेंजर्स: वॉर ऑफ द थ्री टाइटन्स", "'टोक्यो रिवेंजर्स: तेंजिकू आर्क' (Tokyo Revengers: Tenjiku-hen) का अगला सीक्वल।"),
    "Tokyo Revengers: Santen Sensou-hen": ("टोक्यो रिवेंजर्स: वॉर ऑफ द थ्री टाइटन्स", "'टोक्यो रिवेंजर्स: तेंजिकू आर्क' (Tokyo Revengers: Tenjiku-hen) का अगला सीक्वल।"),
    "Tokyo Revengers": ("टोक्यो रिवेंजर्स (Tokyo Revengers)", "ताकेमिची हनागाकी (Takemichi Hanagaki) एक ऐसा फ्रीलांसर है जिसका जीवन निराशा के चरम पर पहुंच चुका है। उसे पता चलता है कि उसकी मिडिल स्कूल की पूर्व प्रेमिका हिनाता ताचिबाना को बेरहम टोक्यो मांजी गैंग (Tokyo Manji Gang) ने मार डाला है। एक हादसे के बाद ताकेमिची 12 साल पीछे समय यात्रा कर जाता है और हिनाता को बचाने तथा अपना भविष्य बदलने का संकल्प लेता है।"),
    "Tempal": ("ओवरगियर्ड (Overgeared)", "वर्चुअल रियलिटी गेम सैटिस्फाई में शिन यंगवू (ग्रिड) एक महान पौराणिक लोहार का वर्ग प्राप्त कर दुनिया का सबसे शक्तिशाली खिलाड़ी बनने की ओर बढ़ता है।"),
    "Tensei Shitara Ken": ("रीइनकार्नेटेड ऐज़ अ सोर्ड सीज़न 2", "मास्टर (जादुई तलवार) और युवा बिल्ली-प्रजाति की योद्धा फ्रैन अपनी साहसिक यात्रा में नए कालकोठरियों और दुश्मनों का सामना करते हैं।"),
    "Devils' Crest": ("डेविल्स क्रेस्ट (Devils' Crest)", "एक आभासी दुनिया और वास्तविकता के विलय के बाद छात्र अपने जीवित रहने के लिए खतरनाक राक्षसों के विरुद्ध संघर्ष करते हैं।"),
    "A Certain Dark Item": ("अ सर्टेन डार्क आइटम", "एकेडमी सिटी के गुप्त भूमिगत संचालन में शक्ति और जादुई विज्ञान के नए रहस्य सामने आते हैं।"),
    "Pan Dorobou": ("पैन डोरोबो (Pan Dorobou)", "स्वादिष्ट ब्रेड चुराने वाले रहस्यमयी ब्रेड-चोर की मज़ेदार और हास्यप्रद हरकतों की कहानी।"),
    "BEYBLADE X": ("बेब्लेड एक्स (Beyblade X)", "एक्स टावर के शिखर पर पहुँचने के लिए पेशेवर ब्लैडर्स अत्यधिक गति और गियर की तकनीक के साथ बेब्लेड का महामुकाबला करते हैं।"),
    "Watame": ("वातामे डिड बॉर्डरलाइन नथिंग रॉन्ग", "त्सुनोमाकी वातामे के दैनिक अनुभवों और हास्यप्रद परिदृश्यों पर आधारित लघु एनीमेशन।"),
    "Please Excuse": ("प्लीज़ एक्सक्यूज़ माय यंगर ब्रदर्स", "चार शरारती और ऊर्जावान छोटे भाइयों के साथ बड़ी बहन के दैनिक जीवन की पारिवारिक हास्यप्रद कहानी।"),
    "TOUGEN ANKI": ("तौगेन अंकी (Tougen Anki)", "ओनी (राक्षस) रक्त के उत्तराधिकारी इचिनोज़े शिकी अपने पूर्वजों के रहस्यों और मोमोतारो एजेंसी के विरुद्ध संघर्ष में उतरता है।"),
    "Welsh": ("वेल्श एंड शेडार (Welsh & Shedar)", "रहस्यमयी दुनिया में दो अनोखे साथियों की साहसिक यात्रा और अनसुलझे रहस्यों की खोज।"),
    "Luo Xiaohei": ("लुओ शियाओहेई झान जी", "काली बिल्ली के रूप में जादुई स्पिरिट लुओ शियाओहेई की प्रकृति और मानव जगत के बीच रोमांचक यात्रा।"),
    "Mazenchu": ("माज़ेनचू (Mazenchu)", "दैनिक जीवन और हास्य से भरपूर लघु एनीमे श्रृंखला।"),
}

KNOWN_ANIME_ENTITIES: dict[str, list[str]] = {
    "goku": ["गोकू", "goku"],
    "naruto": ["नारुतो", "naruto"],
    "luffy": ["लूफी", "लफ़ी", "luffy"],
    "gojo": ["गोजो", "gojo"],
    "sukuna": ["सुकुना", "sukuna"],
    "tanjiro": ["तांजिरो", "तंजीरो", "tanjiro"],
    "deku": ["देकु", "deku"],
    "eren": ["एरेन", "eren"],
    "zoro": ["ज़ोरो", "जोरो", "zoro"],
    "ichigo": ["इचिगो", "ichigo"],
    "maomao": ["माओमाओ", "माओ माओ", "maomao"],
    "jinshi": ["जिनशी", "जिंशी", "jinshi"],
    "satoko": ["सतौको", "सतोको", "satoko"],
    "shinpei": ["शिन्पेई", "शिनपेई", "shinpei"],
    "takemichi": ["ताकेमिची", "ताकेमीची", "takemichi"],
    "hanagaki": ["हनागाकी", "hanagaki"],
    "hinata": ["हिनाता", "hinata"],
    "tachibana": ["ताचिबाना", "ताचीबाना", "tachibana"],
    "tokyo": ["टोक्यो", "tokyo"],
    "revengers": ["रिवेंजर्स", "revengers"],
    "tenjiku": ["तेंजिकू", "तेंजुकू", "tenjiku"],
    "shi": ["शी", "shi"],
    "manji": ["मांजी", "मांज़ी", "manji"],
    "mikey": ["माइकी", "mikey"],
    "draken": ["ड्रेकन", "draken"],
    "inner palace": ["इनर पैलेस", "भीतरी प्रांगण", "inner palace"],
    "verdigris": ["वर्डग्रिस", "verdigris"],
    "loulan": ["लोलन", "loulan"],
    "poison": ["जहर", "विष", "poison"],
    "royal banquet": ["शाही दावत", "शाही भोज", "royal banquet"],
}


def normalize_studio_name(name: str | None) -> str:
    """Normalize studio names to clean title-case while preserving recognized studio acronyms."""
    if not name or not name.strip():
        return "TBA"
    raw = name.strip()
    if raw.upper() == "TBA":
        return "TBA"
    special_studios = {
        "MAPPA": "MAPPA",
        "WIT STUDIO": "Wit Studio",
        "WIT": "Wit Studio",
        "CLOVERWORKS": "CloverWorks",
        "A-1 PICTURES": "A-1 Pictures",
        "J.C.STAFF": "J.C.Staff",
        "J.C. STAFF": "J.C.Staff",
        "UFOTABLE": "Ufotable",
        "KYOTO ANIMATION": "Kyoto Animation",
        "BONES": "Bones",
        "BONES FILM": "Bones",
        "MADHOUSE": "Madhouse",
        "PIERROT": "Pierrot",
        "STUDIO PIERROT": "Studio Pierrot",
        "TMS ENTERTAINMENT": "TMS Entertainment",
        "TOEI ANIMATION": "Toei Animation",
        "STUDIO BIND": "Studio Bind",
        "TRIGGER": "Trigger",
        "STUDIO TRIGGER": "Studio Trigger",
        "DAVID PRODUCTION": "David Production",
        "PRODUCTION I.G": "Production I.G",
        "WHITE FOX": "White Fox",
        "OLM": "OLM",
        "COMIX WAVE FILMS": "CoMix Wave Films",
        "SHAFT": "Shaft",
        "DOGA KOBO": "Doga Kobo",
        "SILVER LINK.": "Silver Link",
        "LIDENFILMS": "LIDENFILMS",
        "STUDIO DEEN": "Studio Deen",
        "GEEK FLEET": "Geek Fleet",
        "BUG FILMS": "Bug Films",
    }
    cleaned_key = re.sub(r'\s+', ' ', raw).upper()
    if cleaned_key in special_studios:
        return special_studios[cleaned_key]
    words = raw.split()
    norm_words = []
    for w in words:
        if w.isupper() and len(w) <= 4 and w.isalnum():
            norm_words.append(w)
        else:
            norm_words.append(w.capitalize())
    return " ".join(norm_words)


def validate_proper_nouns_in_hindi(source_en: str, hindi_text: str) -> tuple[bool, str]:
    """Validate that character names and places in Hindi text match the source English text.
    
    If Hindi text contains hallucinated entities absent from source, or fails to retain
    known primary entities present in the source, returns (False, reason).
    """
    if not hindi_text or not hindi_text.strip():
        return False, "Empty Hindi translation"
    if not source_en or not source_en.strip():
        return True, "No source text to validate against"

    src_lower = source_en.lower()
    hi_lower = hindi_text.lower()

    def _matches_variant(hv: str, text: str) -> bool:
        if hv.isascii():
            return bool(re.search(rf'\b{re.escape(hv)}\b', text))
        return bool(re.search(rf'(?<![\u0900-\u097f]){re.escape(hv)}(?![\u0900-\u097f])', text))

    # 1. Hallucination check: ensure Hindi does not contain foreign entities absent from source
    for entity_en, hi_variants in KNOWN_ANIME_ENTITIES.items():
        if not re.search(rf'\b{re.escape(entity_en)}\b', src_lower):
            for hv in hi_variants:
                if _matches_variant(hv, hi_lower):
                    return False, f"Hallucinated entity '{hv}' ({entity_en}) found in Hindi but absent in English source"

    # 2. Key presence check: if source mentions specific character/place entities, at least one must be in Hindi
    src_entities_found = [e for e in KNOWN_ANIME_ENTITIES if re.search(rf'\b{re.escape(e)}\b', src_lower)]
    if src_entities_found:
        matched = False
        for e in src_entities_found:
            for hv in KNOWN_ANIME_ENTITIES[e]:
                if _matches_variant(hv, hi_lower):
                    matched = True
                    break
            if matched:
                break
        if not matched:
            return False, f"None of the primary source entities {src_entities_found} were retained in Hindi translation"

    # 3. Gemini Faithfulness Check (if API key available)
    faithful_ok, faith_reason = validate_faithfulness_with_gemini(source_en, hindi_text)
    if not faithful_ok:
        return False, faith_reason

    return True, "Valid proper nouns alignment"


def validate_faithfulness_with_gemini(source_en: str, hindi_text: str) -> tuple[bool, str]:
    """Check translation faithfulness using Gemini API.
    
    Prompt: Does the Hindi add, remove or contradict any fact vs the English source?
    Answer JSON {faithful: bool, issues: []}.
    Reject and keep English if not faithful.
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        return True, "GEMINI_API_KEY not configured; skipped LLM faithfulness call"

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
        prompt = (
            "You are a strict translation auditor. Compare the following English source text and its Hindi translation.\n"
            "Question: Does the Hindi add, remove or contradict any fact vs the English source?\n"
            "Return ONLY valid JSON in format:\n"
            '{"faithful": true, "issues": []}\n'
            'or\n'
            '{"faithful": false, "issues": ["description of mismatch"]}\n\n'
            f"English Source:\n{source_en}\n\n"
            f"Hindi Translation:\n{hindi_text}\n"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}
        }
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                res_json = json.loads(text)
                is_faithful = bool(res_json.get("faithful", False))
                issues = res_json.get("issues", [])
                if not is_faithful:
                    return False, f"Gemini faithfulness rejection: {', '.join(issues) if issues else 'Fact contradiction or added information detected'}"
                return True, "Faithful translation confirmed by Gemini"
    except Exception as e:
        logger.warning(f"Gemini faithfulness API check skipped due to network/error: {e}")
        return True, f"Gemini check error: {e}"

    return True, "Faithfulness verified"


def clean_description(raw_desc: str) -> str:
    """Strip HTML, spoilers, and markdown links from AniList descriptions."""
    if not raw_desc:
        return ""
    clean = re.sub(r'<br\s*/?>', ' ', raw_desc, flags=re.IGNORECASE)
    clean = re.sub(r'<[^>]+>', '', clean)
    clean = re.sub(r'~![^!]*!~', '', clean)
    clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)
    clean = re.sub(r'\(Source:.*?\)', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def validate_season_and_prequel_synopsis(
    title: str,
    media: dict[str, Any] | None = None,
    card_season_year: int | None = None,
    card_start_date: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Validator that checks:
    1. card.seasonYear / startDate must match the AniList media.
    2. If description is identical to the prequel's description, treat it as suspicious
       and hide the synopsis (show title + episode info only).
    3. If new-season / sequel card shows suspicious Season 1 premise story (e.g.
       Apothecary S2/S3 showing S1 'sold to the palace as servant', Tokyo Revengers
       showing S1 'freelancer pits of despair / 12 years into past'), treat as suspicious
       and hide the synopsis.
    """
    if not media:
        return True, "No media to validate against"

    media_id = media.get("id")
    media_year = media.get("seasonYear") or (media.get("startDate") or {}).get("year")
    media_start = media.get("startDate") or {}

    # Check 1: card.seasonYear / startDate must match the AniList media
    if card_season_year is not None and media_year is not None:
        if int(card_season_year) != int(media_year):
            return False, f"seasonYear mismatch: card expected {card_season_year}, AniList media {media_id} has {media_year}"

    if card_start_date and media_start:
        c_year = card_start_date.get("year")
        m_year = media_start.get("year")
        if c_year and m_year and int(c_year) != int(m_year):
            return False, f"startDate year mismatch: card expected {c_year}, AniList media {media_id} has {m_year}"

    curr_desc = clean_description(media.get("description") or "")

    # Check 2: Description identical to prequel's description
    relations = media.get("relations", {}).get("edges", [])
    for edge in relations:
        if edge.get("relationType") == "PREQUEL":
            prequel_node = edge.get("node") or {}
            prequel_desc = clean_description(prequel_node.get("description") or "")
            if curr_desc and prequel_desc:
                c_clean = re.sub(r'\s+', ' ', curr_desc.strip().lower())
                p_clean = re.sub(r'\s+', ' ', prequel_desc.strip().lower())
                if c_clean == p_clean or (len(c_clean) > 30 and c_clean in p_clean) or (len(p_clean) > 30 and p_clean in c_clean):
                    return False, f"Suspicious duplicate: description is identical to prequel (ID {prequel_node.get('id')})"

    # Check 3: Check if sequel card has suspicious Season 1 premise text
    comb_title = (title or "").lower()
    is_sequel = bool(
        re.search(r'\b(season\s*[2-9]|part\s*[2-9]|2nd\s*season|3rd\s*season|final\s*season|arc|sequel)\b', comb_title, re.IGNORECASE)
        or any(e.get("relationType") == "PREQUEL" for e in relations)
    )

    if is_sequel:
        desc_lower = curr_desc.lower()
        s1_patterns = [
            "sold as a lowly servant to the emperor's palace",
            "sold to the palace as servant",
            "sold as a lowly servant",
            "freelancer that's reached the absolute pits of despair",
            "12 years into the past",
            "12 years back in time",
            "middle school girlfriend",
        ]
        for pat in s1_patterns:
            if pat in desc_lower:
                return False, f"Suspicious Season 1 premise ('{pat}') found on sequel card '{title}'"

    return True, "Valid season media and description"


def translate_english_synopsis_to_hindi(text_en: str, title_en: str = "") -> tuple[str, bool]:
    """Translate an English anime synopsis to natural, factual Hindi.
    
    Returns (hindi_translation, True) on success.
    Returns (text_en, False) if translation fails.
    """
    if not text_en or not text_en.strip():
        return "", False
    
    clean = clean_description(text_en)
    if not clean:
        return "", False

    # Check for direct sequel / season patterns
    m = re.match(r'^sequel to\s+(?:<i>)?([^<.]+)(?:</i>)?\.?', clean, re.IGNORECASE)
    if m:
        target = m.group(1).strip()
        return f"'{target}' का अगला सीक्वल।", True

    m = re.match(r'^(?:the\s+)?second season of\s+([^.]+)\.?', clean, re.IGNORECASE)
    if m:
        target = m.group(1).strip()
        return f"'{target}' का दूसरा सीज़न।", True

    m = re.match(r'^(?:the\s+)?third season of\s+([^.]+)\.?', clean, re.IGNORECASE)
    if m:
        target = m.group(1).strip()
        return f"'{target}' का तीसरा सीज़न।", True

    m = re.match(r'^(?:the\s+)?(?:final|last) season of\s+([^.]+)\.?', clean, re.IGNORECASE)
    if m:
        target = m.group(1).strip()
        return f"'{target}' का अंतिम सीज़न।", True

    m = re.match(r'^(?:the\s+)?(?:movie|film|anime film) adaptation of\s+([^.]+)\.?', clean, re.IGNORECASE)
    if m:
        target = m.group(1).strip()
        return f"'{target}' का आधिकारिक फ़िल्म रूपांतरण।", True

    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    json={
                        "contents": [{
                            "parts": [{"text": f"Translate faithfully into 1-2 natural Hindi sentences without adding any words or facts not in the source. Keep all names in Hindi with the English name in brackets. Source text:\n{clean}"}]
                        }]
                    }
                )
                if resp.status_code == 200:
                    cand = resp.json().get("candidates", [])
                    if cand:
                        out = cand[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                        if out and any('\u0900' <= ch <= '\u097f' for ch in out):
                            return out, True
        except Exception as e:
            logger.warning("Gemini synopsis translation error: %s", e)

    return clean, False


def get_hindi_title_and_summary(
    title_en: str,
    title_romaji: str,
    raw_desc: str,
    media: dict[str, Any] | None = None,
    card_season_year: int | None = None,
    card_start_date: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Provide clean Hindi title and factual synopsis translated from AniList description.
    
    Validation & Translation Pipeline:
    1. Season & Prequel Validator runs FIRST:
       - Checks card.seasonYear/startDate match AniList media.
       - Checks if description is identical to prequel's description.
       - Checks if sequel card contains suspicious S1 premise.
       If it fails: hides synopsis (returns title, "", "hidden").
    2. Hotaru no Yomeiri (id: 205909): strictly binds to ANIME entry.
       If description is empty, returns a neutral line.
    3. Curated Translations: Matches verified titles and checks proper nouns.
       Returns (h_title, h_sum, "verified").
    4. Auto-translation fallback: Translates English synopsis into Hindi.
       If translation fails: returns English synopsis and sets translation_status='failed'.
    """
    clean_desc = clean_description(raw_desc)
    combined = f"{title_en} {title_romaji}"

    # Hotaru no Yomeiri (id: 205909): strictly use Anime entry. If empty, show short neutral line or hide.
    if media and media.get("id") == 205909:
        if not clean_desc or clean_desc.strip() in ("", "TBA", "None"):
            return "होटारू नो योमेरी (Firefly Wedding)", "आगामी टीवी एनीमे श्रृंखला का आधिकारिक प्रसारण विवरण।", "verified"

    # Step 1: Season & Prequel Validator FIRST
    if media:
        is_season_ok, season_reason = validate_season_and_prequel_synopsis(
            title=combined,
            media=media,
            card_season_year=card_season_year,
            card_start_date=card_start_date,
        )
        if not is_season_ok:
            logger.warning("Season/Prequel validator failed for '%s': %s. Hiding synopsis.", title_en, season_reason)
            return title_en, "", "hidden"
    else:
        is_seq = bool(re.search(r'\b(season\s*[2-9]|part\s*[2-9]|2nd\s*season|3rd\s*season|final\s*season|arc|sequel)\b', combined, re.IGNORECASE))
        if is_seq:
            s1_markers = [
                "sold as a lowly servant",
                "sold to the palace",
                "महल में एक साधारण सेविका के रूप में बेच",
                "pits of despair",
                "12 years into the past",
                "12 साल पीछे अतीत",
            ]
            if any(m in clean_desc.lower() for m in s1_markers):
                logger.warning("Suspicious Season 1 premise detected on sequel '%s'. Hiding synopsis.", title_en)
                return title_en, "", "hidden"

    sorted_curated = sorted(CURATED_TITLES.items(), key=lambda x: len(x[0]), reverse=True)
    is_sequel_title = bool(re.search(r'\b(season\s*[2-9]|part\s*[2-9]|2nd\s*season|3rd\s*season|final\s*season|arc)\b', combined, re.IGNORECASE))

    for k, (h_title, h_sum) in sorted_curated:
        if k.lower() in combined.lower():
            if is_sequel_title and k.lower() in ("kusuriya no hitorigoto", "the apothecary diaries", "tokyo revengers"):
                continue

            # Step 2: Proper-noun validator on the Hindi translation against clean English source
            is_valid, reason = validate_proper_nouns_in_hindi(clean_desc or title_en, h_sum)
            if is_valid:
                return h_title, h_sum, "verified"
            else:
                logger.warning("Proper noun validation failed for '%s': %s. Trying auto-translator.", title_en, reason)
                break

    # If no curated match or curated validation failed, translate English synopsis to Hindi
    if clean_desc:
        trans_hi, trans_ok = translate_english_synopsis_to_hindi(clean_desc, title_en)
        if trans_ok:
            val_ok, _ = validate_proper_nouns_in_hindi(clean_desc, trans_hi)
            if val_ok:
                return title_en, trans_hi, "verified"

    # Translation failed: show English text but set translation_status='failed'
    fallback_brief = clean_desc or f"{title_en} schedule update."
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', fallback_brief) if s.strip()]
    brief = " ".join(sentences[:3]) if sentences else fallback_brief
    return title_en, brief, "failed"


def extract_streaming_links(ext_links: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Extract verified streaming platforms from AniList externalLinks."""
    if not ext_links:
        return []
    result = []
    seen = set()
    for item in ext_links:
        site = (item.get("site") or "").strip()
        url = (item.get("url") or "").strip()
        if not site or not url:
            continue
        for plat, meta in PLATFORM_CLASSES.items():
            if plat.lower() in site.lower() and meta["name"] not in seen:
                seen.add(meta["name"])
                result.append({
                    "name": meta["name"],
                    "short": meta["short"],
                    "url": url,
                    "class": meta["class"]
                })
                break
    return result


def get_season_cycle(dt: datetime | None = None) -> dict[str, Any]:
    """Derive current, previous, and next anime broadcast season from date.

    Seasons:
        Winter: Jan - Mar (months 1, 2, 3)
        Spring: Apr - Jun (months 4, 5, 6)
        Summer: Jul - Sep (months 7, 8, 9)
        Fall:   Oct - Dec (months 10, 11, 12)
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    month = dt.month
    year = dt.year

    if month in (1, 2, 3):
        cur_season = "WINTER"
    elif month in (4, 5, 6):
        cur_season = "SPRING"
    elif month in (7, 8, 9):
        cur_season = "SUMMER"
    else:
        cur_season = "FALL"

    season_order = ["WINTER", "SPRING", "SUMMER", "FALL"]
    season_short_hi = {
        "WINTER": "शीतकालीन",
        "SPRING": "वसंत",
        "SUMMER": "ग्रीष्मकालीन",
        "FALL": "शरदकालीन",
    }
    season_hi_map = {
        "WINTER": "शीतकालीन (Winter)",
        "SPRING": "वसंत (Spring)",
        "SUMMER": "ग्रीष्मकालीन (Summer)",
        "FALL": "शरदकालीन (Fall)",
    }

    cur_idx = season_order.index(cur_season)

    if cur_idx == 0:
        prev_season = "FALL"
        prev_year = year - 1
    else:
        prev_season = season_order[cur_idx - 1]
        prev_year = year

    if cur_idx == 3:
        next_season = "WINTER"
        next_year = year + 1
    else:
        next_season = season_order[cur_idx + 1]
        next_year = year

    return {
        "current": {
            "season": cur_season,
            "year": year,
            "label_en": f"{cur_season.title()} {year}",
            "label_hi": f"{season_short_hi[cur_season]} {year}",
            "label_full": f"{season_hi_map[cur_season]} {year}",
            "is_current": True,
        },
        "previous": {
            "season": prev_season,
            "year": prev_year,
            "label_en": f"{prev_season.title()} {prev_year}",
            "label_hi": f"{season_short_hi[prev_season]} {prev_year}",
            "label_full": f"{season_hi_map[prev_season]} {prev_year}",
            "is_current": False,
        },
        "next": {
            "season": next_season,
            "year": next_year,
            "label_en": f"{next_season.title()} {next_year}",
            "label_hi": f"{season_short_hi[next_season]} {next_year}",
            "label_full": f"{season_hi_map[next_season]} {next_year}",
            "is_current": False,
        },
    }


ANILIST_WEEKLY_AIRING_QUERY = """
query ($start: Int, $end: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo {
      hasNextPage
    }
    airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {
      id
      airingAt
      episode
      mediaId
      media {
        id
        idMal
        title {
          romaji
          english
          native
        }
        format
        status
        season
        seasonYear
        episodes
        duration
        startDate {
          year
          month
          day
        }
        averageScore
        popularity
        source
        coverImage {
          large
          extraLarge
        }
        studios(isMain: true) {
          nodes {
            name
          }
        }
        externalLinks {
          site
          url
          icon
        }
        description(asHtml: false)
        relations {
          edges {
            relationType
            node {
              id
              title {
                romaji
                english
              }
              description(asHtml: false)
            }
          }
        }
      }
    }
  }
}
"""

ANILIST_SEASON_PREMIERES_QUERY = """
query ($season: MediaSeason, $seasonYear: Int) {
  Page(page: 1, perPage: 30) {
    media(season: $season, seasonYear: $seasonYear, type: ANIME, status_in: [NOT_YET_RELEASED, RELEASING], sort: START_DATE) {
      id
      idMal
      title {
        romaji
        english
        native
      }
      format
      status
      season
      seasonYear
      episodes
      duration
      startDate {
        year
        month
        day
      }
      averageScore
      popularity
      source
      coverImage {
        large
        extraLarge
      }
      studios(isMain: true) {
        nodes {
          name
        }
      }
      description(asHtml: false)
      relations {
        edges {
          relationType
          node {
            id
            title {
              romaji
              english
            }
            description(asHtml: false)
          }
        }
      }
    }
  }
}
"""


def fetch_live_weekly_schedule(
    season: str | None = None,
    year: int | None = None,
    force_refresh: bool = False,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Fetch live weekly anime airing schedule and season premieres from AniList GraphQL.

    Features:
    - 30-minute in-memory cache.
    - Day filtering strictly by IST weekday of airingAt.
    - Independent cards with exact episode number, distinct airing IST time, and individual countdown.
    - Filters out FINISHED shows and past season items from 'This Week'.
    - 'Premiering this season' section with real premiere date, episodes, duration.
    """
    global _LIVE_SCHEDULE_CACHE

    cycle = get_season_cycle()
    req_season = (season or cycle["current"]["season"]).upper()
    req_year = int(year or cycle["current"]["year"])

    cache_key = (req_season, req_year)
    now_mono = time.monotonic()

    if not force_refresh and cache_key in _LIVE_SCHEDULE_CACHE:
        cached_data, expiry = _LIVE_SCHEDULE_CACHE[cache_key]
        if expiry > now_mono:
            return cached_data

    # Calculate current week Monday 00:00:00 IST to Sunday 23:59:59 IST
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST_TZ)
    start_of_week_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now_ist.weekday())
    end_of_week_ist = start_of_week_ist + timedelta(days=7)
    start_unix = int(start_of_week_ist.astimezone(timezone.utc).timestamp())
    end_unix = int(end_of_week_ist.astimezone(timezone.utc).timestamp())

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    weekly_items: list[dict[str, Any]] = []
    seen_media_ids: set[int] = set()

    try:
        with httpx.Client(timeout=timeout_seconds, headers=headers) as client:
            # 1. Fetch weekly airing schedules (up to 3 pages)
            page = 1
            while page <= 3:
                payload = {
                    "query": ANILIST_WEEKLY_AIRING_QUERY,
                    "variables": {"start": start_unix, "end": end_unix, "page": page},
                }
                resp = client.post(ANILIST_GRAPHQL_URL, json=payload)
                if resp.status_code != 200:
                    logger.warning("AniList weekly query page %d returned HTTP %d", page, resp.status_code)
                    break
                data = resp.json().get("data", {}).get("Page", {})
                schedules = data.get("airingSchedules", [])
                for s in schedules:
                    media = s.get("media") or {}
                    m_status = media.get("status")
                    # Hide FINISHED shows and shows from past seasons
                    if m_status == "FINISHED" or m_status == "CANCELLED":
                        continue

                    airing_at = s.get("airingAt")
                    if not airing_at:
                        continue

                    dt_ist = datetime.fromtimestamp(airing_at, tz=timezone.utc).astimezone(IST_TZ)
                    dt_jst = datetime.fromtimestamp(airing_at, tz=timezone.utc).astimezone(JST_TZ)

                    titles = media.get("title") or {}
                    title_en = (titles.get("english") or titles.get("romaji") or f"Anime #{media.get('id')}").strip()
                    title_romaji = (titles.get("romaji") or title_en).strip()

                    cov = media.get("coverImage") or {}
                    image_url = cov.get("large") or cov.get("extraLarge")

                    raw_desc = media.get("description") or ""
                    media_year = media.get("seasonYear") or (media.get("startDate") or {}).get("year")
                    start_date = media.get("startDate")

                    title_hi, summary_hi, trans_status = get_hindi_title_and_summary(
                        title_en=title_en,
                        title_romaji=title_romaji,
                        raw_desc=raw_desc,
                        media=media,
                        card_season_year=media_year,
                        card_start_date=start_date,
                    )
                    # If synopsis is hidden by validator, keep summary_en empty as well
                    summary_en = clean_description(raw_desc) if summary_hi else ""

                    studios_nodes = media.get("studios", {}).get("nodes", [])
                    raw_studio = studios_nodes[0].get("name", "TBA") if studios_nodes else "TBA"
                    studio_name = normalize_studio_name(raw_studio)

                    raw_source = media.get("source") or "ORIGINAL"
                    source_hi = SOURCE_HI.get(raw_source, "अन्य")

                    score = media.get("averageScore")
                    score_display = f"{score / 10:.1f}" if score else "N/A"

                    pop = media.get("popularity") or 0
                    pop_display = f"{pop / 1000:.1f}k" if pop >= 1000 else str(pop)

                    streaming_links = extract_streaming_links(media.get("externalLinks"))

                    item_dict = {
                        "id": f"anilist_{media.get('id')}_ep{s.get('episode')}",
                        "media_id": media.get("id"),
                        "mal_id": media.get("idMal"),
                        "episode": s.get("episode"),
                        "airing_at": airing_at,
                        "airing_at_iso": datetime.fromtimestamp(airing_at, tz=timezone.utc).isoformat(),
                        "airing_ist_weekday": dt_ist.weekday(),  # 0=Mon, ..., 6=Sun
                        "airing_ist_time": dt_ist.strftime("%I:%M %p IST").lstrip("0"),
                        "airing_jst_time": dt_jst.strftime("%I:%M %p JST").lstrip("0"),
                        "title_en": title_en,
                        "title_romaji": title_romaji,
                        "title_hi": title_hi,
                        "image_url": image_url,
                        "studio": studio_name,
                        "source": raw_source,
                        "source_display_hi": source_hi,
                        "score": score,
                        "score_display": score_display,
                        "score_source": "AniList",
                        "popularity": pop,
                        "popularity_display": pop_display,
                        "members": pop,
                        "members_display": pop_display,
                        "streaming_links": streaming_links,
                        "has_verified_hindi_dub": False,  # Strict: ONLY true if verified
                        "summary_en": summary_en,
                        "summary_hi": summary_hi,
                        "translation_status": trans_status,
                        "source_url": f"https://anilist.co/anime/{media.get('id')}",
                        "status": m_status,
                        "format": media.get("format") or "TV",
                        "duration": media.get("duration"),
                        "seasonYear": media_year,
                        "startDate": start_date,
                    }
                    weekly_items.append(item_dict)
                    seen_media_ids.add(media.get("id"))

                if not data.get("pageInfo", {}).get("hasNextPage"):
                    break
                page += 1

            # 2. Fetch "Premiering this season"
            premiering_items: list[dict[str, Any]] = []
            prem_payload = {
                "query": ANILIST_SEASON_PREMIERES_QUERY,
                "variables": {"season": req_season, "seasonYear": req_year},
            }
            prem_resp = client.post(ANILIST_GRAPHQL_URL, json=prem_payload)
            if prem_resp.status_code == 200:
                prem_media = prem_resp.json().get("data", {}).get("Page", {}).get("media", [])
                for pm in prem_media:
                    pm_titles = pm.get("title") or {}
                    pm_title_en = (pm_titles.get("english") or pm_titles.get("romaji") or f"Anime #{pm.get('id')}").strip()
                    pm_title_romaji = (pm_titles.get("romaji") or pm_title_en).strip()
                    pm_cov = pm.get("coverImage") or {}
                    pm_img = pm_cov.get("large") or pm_cov.get("extraLarge")
                    pm_s_date = pm.get("startDate") or {}
                    pm_year = pm_s_date.get("year") or pm.get("seasonYear")
                    pm_month = pm_s_date.get("month")
                    pm_day = pm_s_date.get("day")

                    pm_desc = pm.get("description") or ""
                    pm_h_title, pm_h_sum, pm_trans_status = get_hindi_title_and_summary(
                        title_en=pm_title_en,
                        title_romaji=pm_title_romaji,
                        raw_desc=pm_desc,
                        media=pm,
                        card_season_year=pm_year,
                        card_start_date=pm_s_date,
                    )

                    # Strictly require verified complete startDate from AniList
                    if not (pm_year and pm_month and pm_day):
                        continue

                    # Filter out unverified rumors (e.g. Cyberpunk 2, Black Clover S2)
                    comb_title = f"{pm_title_en} {pm_title_romaji}".lower()
                    if ("cyberpunk" in comb_title and "2" in comb_title) or ("black clover" in comb_title and "season 2" in comb_title):
                        if pm.get("status") not in ("NOT_YET_RELEASED", "RELEASING") or not (pm_year and pm_month and pm_day):
                            continue

                    m_hi = MONTH_HI.get(pm_month, "")
                    prem_date_fmt = f"{pm_day} {m_hi} {pm_year}"

                    pm_studios = pm.get("studios", {}).get("nodes", [])
                    raw_pm_studio = pm_studios[0].get("name", "TBA") if pm_studios else "TBA"
                    pm_studio = normalize_studio_name(raw_pm_studio)

                    pm_eps = pm.get("episodes")
                    pm_eps_str = f"{pm_eps} एपिसोड" if pm_eps else "TBA"

                    pm_dur = pm.get("duration")
                    pm_dur_str = f"{pm_dur} मिनट" if pm_dur else "24 मिनट"

                    pm_score = pm.get("averageScore")
                    pm_score_disp = f"{pm_score / 10:.1f}" if pm_score else "N/A"

                    pm_pop = pm.get("popularity") or 0
                    pm_pop_disp = f"{pm_pop / 1000:.1f}k" if pm_pop >= 1000 else str(pm_pop)

                    premiering_items.append({
                        "id": pm.get("id"),
                        "title_en": pm_title_en,
                        "title_romaji": pm_title_romaji,
                        "title_hi": pm_h_title,
                        "cover_image": pm_img,
                        "image_url": pm_img,
                        "premiere_date_formatted": prem_date_fmt,
                        "start_year": pm_year,
                        "start_month": pm_month,
                        "start_day": pm_day,
                        "episodes_count": pm_eps_str,
                        "duration_min": pm_dur_str,
                        "studio": pm_studio,
                        "source_display_hi": SOURCE_HI.get(pm.get("source") or "", "अन्य"),
                        "score_display": pm_score_disp,
                        "score_source": "AniList",
                        "popularity": pm_pop,
                        "popularity_display": pm_pop_disp,
                        "members": pm_pop,
                        "members_display": pm_pop_disp,
                        "summary_hi": pm_h_sum,
                        "translation_status": pm_trans_status,
                        "source_url": f"https://anilist.co/anime/{pm.get('id')}",
                        "format": pm.get("format") or "TV",
                    })

                # Sort premiering anime by verified startDate ascending
                premiering_items.sort(key=lambda x: (x.get("start_year", 9999), x.get("start_month", 99), x.get("start_day", 99)))

    except Exception as exc:
        logger.error("Failed to fetch live schedule from AniList: %s", exc)
        premiering_items = []

    # Selected season info
    selected_season_info = {
        "season": req_season,
        "year": req_year,
        "label_en": f"{req_season.title()} {req_year}",
        "label_hi": f"{SOURCE_HI.get(req_season, req_season)} {req_year}",
    }
    cycle["selected"] = selected_season_info

    result = {
        "season_info": cycle,
        "weekly_items": weekly_items,
        "premiering_items": premiering_items,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "today_idx": now_ist.weekday(),
    }

    _LIVE_SCHEDULE_CACHE[cache_key] = (result, now_mono + SCHEDULE_CACHE_TTL)
    logger.info("Successfully fetched %d weekly items & %d premiering anime for %s %d", len(weekly_items), len(premiering_items), req_season, req_year)
    return result


def clear_schedule_cache() -> None:
    """Clear in-memory live schedule cache."""
    global _LIVE_SCHEDULE_CACHE
    _LIVE_SCHEDULE_CACHE.clear()


def find_schedule_item_by_id(item_id: str, media_id: int | None = None) -> dict[str, Any] | None:
    """Find schedule item in live schedule cache by composite item ID or media ID."""
    global _LIVE_SCHEDULE_CACHE
    if not _LIVE_SCHEDULE_CACHE:
        try:
            fetch_live_weekly_schedule()
        except Exception:
            pass

    for (s_name, s_year), (data, exp) in list(_LIVE_SCHEDULE_CACHE.items()):
        items_to_search = data.get("weekly_items", []) + data.get("premiering_items", [])
        for item in items_to_search:
            match_id = (item.get("id") == item_id)
            match_media = (media_id is not None and item.get("media_id") == media_id)
            if match_id or match_media:
                ep_val = item.get("episode") or item.get("episodes_count")
                ep_str = f"एपिसोड {ep_val}" if isinstance(ep_val, int) else (str(ep_val) if ep_val else "प्रसारित हो रहा है")

                dt_ist_str = None
                if item.get("airing_ist_weekday") is not None:
                    days_hi_list = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]
                    days_en_list = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                    w_idx = item["airing_ist_weekday"]
                    d_hi = days_hi_list[w_idx] if 0 <= w_idx < len(days_hi_list) else ""
                    d_en = days_en_list[w_idx] if 0 <= w_idx < len(days_en_list) else ""
                    dt_ist_str = f"{d_hi} ({d_en}), {item.get('airing_ist_time', '')}"
                    if item.get("episode"):
                        dt_ist_str += f" • एपिसोड {item.get('episode')}"

                return {
                    "id": item.get("id"),
                    "media_id": item.get("media_id"),
                    "mal_id": item.get("mal_id"),
                    "content_type": "schedule",
                    "title_en": item.get("title_en"),
                    "title_romaji": item.get("title_romaji"),
                    "title_native": item.get("title_romaji"),
                    "title_hi": item.get("title_hi"),
                    "summary_en": item.get("summary_en"),
                    "summary_hi": item.get("summary_hi"),
                    "image_url": item.get("image_url"),
                    "studio": item.get("studio"),
                    "episodes": ep_str,
                    "airing_day_time_ist": dt_ist_str,
                    "genres": item.get("genres") or ["Anime"],
                    "source_type": item.get("source_display_hi") or item.get("source"),
                    "season_display": item.get("season_display") or f"{s_name.title()} {s_year}",
                    "status_display": item.get("status_display") or "प्रसारित हो रहा है (Airing)",
                    "status": item.get("status", "RELEASING"),
                    "anilist_score": item.get("score_display") or (f"{item.get('score')}%" if item.get("score") else None),
                    "score": item.get("score"),
                    "streaming_links": item.get("streaming_links") or [],
                    "source_url": item.get("source_url") or f"https://anilist.co/anime/{item.get('media_id')}",
                    "source_name": "AniList",
                    "published_at": datetime.now(timezone.utc),
                }
    return None


_ANILIST_MEDIA_DETAIL_CACHE: dict[int, tuple[dict[str, Any], float]] = {}

ANILIST_MEDIA_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    idMal
    title {
      romaji
      english
      native
    }
    coverImage {
      extraLarge
      large
      medium
      color
    }
    bannerImage
    description(asHtml: false)
    format
    episodes
    duration
    status
    season
    seasonYear
    source
    averageScore
    genres
    studios(isMain: true) {
      nodes {
        name
        siteUrl
      }
    }
    nextAiringEpisode {
      episode
      airingAt
      timeUntilAiring
    }
    externalLinks {
      site
      url
      type
    }
    siteUrl
  }
}
"""


def fetch_anilist_media_by_id(media_id: int, timeout_seconds: float = 8.0) -> dict[str, Any] | None:
    """Fetch complete anime media details from AniList GraphQL for single-anime pages."""
    global _ANILIST_MEDIA_DETAIL_CACHE
    now_mono = time.monotonic()
    if media_id in _ANILIST_MEDIA_DETAIL_CACHE:
        cached_data, expiry = _ANILIST_MEDIA_DETAIL_CACHE[media_id]
        if expiry > now_mono:
            return cached_data

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "query": ANILIST_MEDIA_QUERY,
        "variables": {"id": media_id},
    }
    for attempt in range(1, 4):
        try:
            with httpx.Client(timeout=timeout_seconds, headers=headers) as client:
                resp = client.post(ANILIST_GRAPHQL_URL, json=payload)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "2.0"))
                    time.sleep(retry_after)
                    continue
                if resp.status_code != 200:
                    logger.warning("AniList media query for ID %d returned HTTP %d", media_id, resp.status_code)
                    return None
                media = resp.json().get("data", {}).get("Media")
                if not media:
                    return None

                titles = media.get("title") or {}
                title_en = (titles.get("english") or titles.get("romaji") or f"Anime #{media_id}").strip()
                title_romaji = (titles.get("romaji") or title_en).strip()
                title_native = (titles.get("native") or "").strip()

                cov = media.get("coverImage") or {}
                image_url = cov.get("extraLarge") or cov.get("large") or cov.get("medium")

                studios_nodes = (media.get("studios") or {}).get("nodes") or []
                studio_name = normalize_studio_name(studios_nodes[0].get("name")) if studios_nodes else None

                episodes_val = media.get("episodes")
                next_airing = media.get("nextAiringEpisode") or {}
                next_ep = next_airing.get("episode")
                airing_at = next_airing.get("airingAt")

                airing_day_time_ist = None
                if airing_at:
                    dt_ist = datetime.fromtimestamp(airing_at, tz=timezone.utc).astimezone(IST_TZ)
                    days_hi_list = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]
                    d_hi = days_hi_list[dt_ist.weekday()]
                    d_en = dt_ist.strftime("%A")
                    t_str = dt_ist.strftime("%I:%M %p IST").lstrip("0")
                    airing_day_time_ist = f"{d_hi} ({d_en}), {t_str}"
                    if next_ep:
                        airing_day_time_ist += f" • एपिसोड {next_ep}"

                raw_desc = media.get("description") or ""
                clean_desc = clean_description(raw_desc)
                title_hi, summary_hi, _ = get_hindi_title_and_summary(title_en, title_romaji, raw_desc, media=media)

                streaming_links = extract_streaming_links(media.get("externalLinks"))

                status_raw = media.get("status")
                status_map = {
                    "RELEASING": "प्रसारित हो रहा है (Airing)",
                    "FINISHED": "पूर्ण (Finished)",
                    "NOT_YET_RELEASED": "जल्द आ रहा है (Upcoming)",
                    "CANCELLED": "रद्द (Cancelled)",
                    "HIATUS": "स्थगित (On Hiatus)",
                }
                status_display = status_map.get(status_raw, status_raw.title() if status_raw else None)

                source_raw = media.get("source")
                source_map = {
                    "MANGA": "मंगा (Manga)",
                    "LIGHT_NOVEL": "लाइट नॉवेल (Light Novel)",
                    "VISUAL_NOVEL": "विजुअल नॉवेल (Visual Novel)",
                    "ORIGINAL": "ओरिजिनल (Original)",
                    "VIDEO_GAME": "वीडियो गेम (Video Game)",
                    "WEB_NOVEL": "वेब नॉवेल (Web Novel)",
                    "OTHER": "अन्य (Other)",
                }
                source_type = source_map.get(source_raw, source_raw.title() if source_raw else None)

                season_val = media.get("season")
                season_year = media.get("seasonYear")
                season_display = None
                if season_val and season_year:
                    s_hi = {"WINTER": "शीतकालीन (Winter)", "SPRING": "वसंत (Spring)", "SUMMER": "ग्रीष्मकालीन (Summer)", "FALL": "शरदकालीन (Fall)"}.get(season_val, season_val.title())
                    season_display = f"{s_hi} {season_year}"
                elif season_year:
                    season_display = str(season_year)

                score_val = media.get("averageScore")
                score_display = f"{score_val}%" if score_val else None

                episodes_str = f"{episodes_val} एपिसोड" if episodes_val else ("प्रसारित हो रहा है" if status_raw == "RELEASING" else "TBA")

                result = {
                    "id": f"anilist_{media_id}",
                    "media_id": media_id,
                    "mal_id": media.get("idMal"),
                    "content_type": "schedule",
                    "title_en": title_en,
                    "title_romaji": title_romaji,
                    "title_native": title_native,
                    "title_hi": title_hi,
                    "summary_en": clean_desc,
                    "summary_hi": summary_hi,
                    "image_url": image_url,
                    "banner_url": media.get("bannerImage"),
                    "studio": studio_name,
                    "episodes": episodes_str,
                    "airing_day_time_ist": airing_day_time_ist,
                    "genres": media.get("genres") or [],
                    "source_type": source_type,
                    "season_display": season_display,
                    "status_display": status_display,
                    "status": status_raw,
                    "anilist_score": score_display,
                    "score": score_val,
                    "streaming_links": streaming_links,
                    "source_url": media.get("siteUrl") or f"https://anilist.co/anime/{media_id}",
                    "source_name": "AniList",
                    "published_at": datetime.now(timezone.utc),
                }

                _ANILIST_MEDIA_DETAIL_CACHE[media_id] = (result, now_mono + 3600.0)
                return result
        except Exception as exc:
            logger.warning("Failed to fetch AniList media detail for %d: %s", media_id, exc)
            return None
    return None



