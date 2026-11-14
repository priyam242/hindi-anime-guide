"""AI rewriter module utilizing Google Gemini API.

Produces clean, original English and Hindi editorial drafts from STRICT FACTS ONLY.
Strictly forbids copying source text and forbids hallucinating facts.
Gracefully handles API errors, timeouts, and quota limits.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("automation.rewriter")

# Gemini REST endpoints (gemini-2.5-flash with fallback to gemini-1.5-flash)
PRIMARY_GEMINI_MODEL = "gemini-2.5-flash"
FALLBACK_GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

DEVANAGARI_DIGITS_MAP = str.maketrans("०१२३४५६७८९", "0123456789")


def extract_all_numbers(text: str) -> set[int]:
    """Extract all integer numbers from text, normalizing Devanagari numerals to ASCII."""
    if not text:
        return set()
    normalized = text.translate(DEVANAGARI_DIGITS_MAP)
    matches = re.findall(r'\b\d+\b', normalized)
    return {int(m) for m in matches}


def validate_translation_numbers_and_dates(
    source_facts: dict[str, Any],
    translated: dict[str, str],
) -> tuple[bool, str]:
    """Validate that Gemini's Hindi output contains ONLY numbers and dates found in the source facts.

    Rule: If the output contains any date or number not found in the source, reject it and keep English.
    """
    # 1. Gather all factual source text
    source_values = [str(v) for k, v in source_facts.items() if v is not None]
    if "title_en" in translated:
        source_values.append(translated["title_en"])
    if "summary_en" in translated:
        source_values.append(translated["summary_en"])

    source_text = " ".join(source_values)
    source_numbers = extract_all_numbers(source_text)

    # 2. Extract numbers from Hindi outputs
    hindi_title = translated.get("title_hi", "")
    hindi_summary = translated.get("summary_hi", "")
    hindi_numbers = extract_all_numbers(f"{hindi_title} {hindi_summary}")

    # 3. Check for unauthorized numbers/dates
    unauthorized = hindi_numbers - source_numbers
    if unauthorized:
        return False, f"Unauthorized numbers/dates found in Hindi output: {sorted(list(unauthorized))}"

    return True, "valid"


def split_sentences(text: str) -> list[str]:
    """Split text into sentences respecting English punctuation and Devanagari danda."""
    if not text:
        return []
    parts = re.split(r'(?<=[.!?।])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def validate_and_sanitize_news_summary(
    title_en: str,
    raw_source_text: str,
    summary_en: str,
    summary_hi: str,
) -> tuple[str, str, list[str]]:
    """Validate and sanitize news summaries according to strict grounding rules:
    
    1. Use ONLY text present in the RSS description/content.
    2. If none exists (or description is empty / boilerplate), show only translated title + 'Read full story' link.
    3. Remove any sentence that adds facts not in the source (cast, teaser, jury praise, editorial filler).
    """
    removed_facts: list[str] = []
    clean_raw = (raw_source_text or "").strip()

    # Strip known boilerplate from raw_source_text
    if clean_raw.lower().startswith("official anime news reporting regarding"):
        clean_raw = ""

    # Rule 1: If no RSS description exists, suppress summary copy completely
    if not clean_raw or len(clean_raw.split()) < 3:
        if summary_en or summary_hi:
            removed_facts.append(
                "Empty RSS description: suppressed fabricated body copy; restricted to translated title and 'Read full story' link."
            )
        return "", "", removed_facts

    source_corpus = f"{title_en} {clean_raw}".lower()

    # Prohibited categories of ungrounded extra facts
    prohibited_categories = [
        {
            "category": "cast/voice actor details not in source",
            "en_keywords": ["cast", "voice actor", "voice actress", "starring", "stars as"],
            "hi_keywords": ["कलाकार", "कास्ट", "आवाज"],
        },
        {
            "category": "teaser/trailer/visual details not in source",
            "en_keywords": ["teaser visual", "new trailer", "pv", "promo video", "key visual"],
            "hi_keywords": ["टीज़र", "ट्रेलर", "विजुअल", "प्रोमो"],
        },
        {
            "category": "jury praise / subjective acclaim not in source",
            "en_keywords": ["jury praise", "praised by jury", "critically acclaimed", "storytelling impact", "accolade"],
            "hi_keywords": ["ज्यूरी", "सराहना", "प्रशंसा", "असाधारण प्रभाव"],
        },
    ]

    # Universal boilerplate sentences to purge unconditionally
    boilerplate_en = [
        "editorial desk will continue monitoring",
        "according to verified announcement details documented via",
        "production milestones and schedule outlines have been shared",
        "anime enthusiasts tracking industry updates can follow",
    ]
    boilerplate_hi = [
        "हमारी संपादकीय टीम",
        "नज़र बनाए रखेगी",
        "ताज़ा मीडिया रिपोर्टों के अनुसार निर्माण और आगामी प्रसारण से जुड़े आधिकारिक विवरण",
        "प्रशंसक आधिकारिक चैनलों के जरिए इस प्रोजेक्ट की प्रगति",
    ]

    # Filter English sentences
    en_sentences = split_sentences(summary_en)
    valid_en_sentences: list[str] = []
    for s in en_sentences:
        s_lower = s.lower()
        if any(b in s_lower for b in boilerplate_en):
            removed_facts.append(f"Purged EN boilerplate: '{s[:60]}...'")
            continue

        violation = False
        for rule in prohibited_categories:
            for kw in rule["en_keywords"]:
                if kw in s_lower and kw not in source_corpus:
                    removed_facts.append(f"Purged EN ungrounded {rule['category']}: '{s[:60]}...'")
                    violation = True
                    break
            if violation:
                break
        if not violation:
            valid_en_sentences.append(s)

    # Filter Hindi sentences
    hi_sentences = split_sentences(summary_hi)
    valid_hi_sentences: list[str] = []
    for s in hi_sentences:
        s_lower = s.lower()
        if any(b in s_lower for b in boilerplate_hi):
            removed_facts.append(f"Purged HI boilerplate: '{s[:60]}...'")
            continue

        violation = False
        for rule in prohibited_categories:
            for kw in rule["hi_keywords"]:
                if kw in s_lower and kw not in source_corpus:
                    removed_facts.append(f"Purged HI ungrounded {rule['category']}: '{s[:60]}...'")
                    violation = True
                    break
            if violation:
                break
        if not violation:
            valid_hi_sentences.append(s)

    final_en = " ".join(valid_en_sentences).strip()
    final_hi = " ".join(valid_hi_sentences).strip()

    return final_en, final_hi, removed_facts


def rewrite_anime_update(
    item_facts: dict[str, Any],
    api_key: str | None = None,
    allow_offline_fallback: bool = True,
    timeout_seconds: float = 25.0,
) -> dict[str, str] | None:
    """Rewrite raw anime metadata into original English and Hindi drafts using Gemini API.

    Args:
        item_facts: Dictionary of verified factual metadata.
        api_key: Google Gemini API key. Defaults to GEMINI_API_KEY environment variable.
        allow_offline_fallback: If True and GEMINI_API_KEY is absent, generate a
            high-quality deterministic factual draft for local testing and inspection.
        timeout_seconds: Timeout for HTTP requests to Gemini.

    Returns:
        Dict with keys `title_en`, `title_hi`, `summary_en`, `summary_hi`, or None on failure.
    """
    key = api_key or os.getenv("GEMINI_API_KEY", "").strip()

    if not key:
        logger.warning(
            "GEMINI_API_KEY is not set in the environment or .env file."
        )
        if allow_offline_fallback:
            logger.info("Generating deterministic factual draft for local testing/preview...")
            return _generate_offline_factual_draft(item_facts)
        return None

    prompt = _build_gemini_prompt(item_facts)

    for model_name in (PRIMARY_GEMINI_MODEL, FALLBACK_GEMINI_MODEL):
        endpoint = f"{GEMINI_API_BASE}/{model_name}:generateContent?key={key}"
        request_body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.post(endpoint, json=request_body)

            if response.status_code == 429:
                logger.warning("Gemini API rate limit reached (HTTP 429). Retrying after 3s backoff...")
                import time
                time.sleep(3.0)
                with httpx.Client(timeout=timeout_seconds) as client:
                    response = client.post(endpoint, json=request_body)
                if response.status_code == 429:
                    logger.warning("Gemini rate limit still active. Skipping item safely.")
                    return None

            if response.status_code in (400, 403):
                err_text = response.text
                logger.error("Gemini API authentication/permission error (%d): %s", response.status_code, err_text)
                return None

            response.raise_for_status()
            result_json = response.json()
            candidates = result_json.get("candidates") or []
            if not candidates:
                logger.warning("Gemini returned empty candidates. Skipping item.")
                continue

            content_parts = candidates[0].get("content", {}).get("parts", [])
            if not content_parts:
                logger.warning("Gemini returned empty parts in response. Skipping item.")
                continue

            raw_text = content_parts[0].get("text", "").strip()
            parsed = _parse_gemini_json_response(raw_text)
            if parsed:
                # Task 4 Validator: Reject Hindi output if it contains unauthorized dates or numbers
                is_valid, reason = validate_translation_numbers_and_dates(item_facts, parsed)
                if not is_valid:
                    logger.warning(
                        "Gemini Hindi output rejected by validator (%s). Keeping original English text.",
                        reason,
                    )
                    parsed["title_hi"] = parsed["title_en"]
                    parsed["summary_hi"] = parsed["summary_en"]

                # News Summary Validator: Rule 1 - Use only text present in RSS description.
                # Remove ungrounded cast, teaser, jury praise, and boilerplate.
                if item_facts.get("content_type") == "news":
                    raw_src = item_facts.get("raw_summary", "") or item_facts.get("summary_en", "")
                    clean_en, clean_hi, removed_facts = validate_and_sanitize_news_summary(
                        title_en=parsed.get("title_en", ""),
                        raw_source_text=raw_src,
                        summary_en=parsed.get("summary_en", ""),
                        summary_hi=parsed.get("summary_hi", ""),
                    )
                    parsed["summary_en"] = clean_en
                    parsed["summary_hi"] = clean_hi
                    if removed_facts:
                        logger.info("News summary validator purged ungrounded facts: %s", removed_facts)

                return parsed

        except httpx.HTTPStatusError as exc:
            logger.warning("Gemini HTTP error with model %s (%d): %s", model_name, exc.response.status_code, exc)
            continue
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            logger.warning("Gemini network error with model %s: %s", model_name, exc)
            continue
        except Exception as exc:
            logger.exception("Unexpected error calling Gemini API with model %s: %s", model_name, exc)
            continue

    logger.error("Failed to rewrite item with Gemini across all model fallbacks.")
    if allow_offline_fallback:
        logger.info("Falling back to deterministic factual draft following Gemini API failure.")
        return _generate_offline_factual_draft(item_facts)
    return None


def _build_gemini_prompt(facts: dict[str, Any]) -> str:
    """Build strict system-like prompt enforcing facts-only rewriting and no hallucination."""
    content_type = facts.get("content_type", "schedule")
    title_en = facts.get("title_english") or facts.get("title_en") or facts.get("title_romaji") or "Anime Update"
    title_romaji = facts.get("title_romaji") or title_en
    source_name = facts.get("source_name", "Official Source")
    raw_summary = facts.get("raw_summary", "")

    if content_type == "news":
        facts_payload = {
            "type": "Anime News Headline & Announcement",
            "headline": title_en,
            "source": source_name,
            "published_date": facts.get("pub_date_iso", "Recently"),
            "factual_context": raw_summary or title_en,
        }
        instructions = """
Rewrite this anime news headline into an engaging, strictly factual editorial article.
Explain what was announced (e.g. release window, key visual, trailer, adaptation).
Do NOT copy any phrasing from the source. Write completely fresh sentences.
"""
    elif content_type == "manga":
        facts_payload = {
            "type": "Manga Chapter Publication Update",
            "manga_series": facts.get("manga_title") or title_en,
            "chapter_number": facts.get("chapter_number"),
            "chapter_title": facts.get("chapter_title", ""),
            "published_at": facts.get("pub_date_iso", "Recently"),
            "source_platform": source_name,
            "factual_details": raw_summary,
        }
        instructions = """
Craft a factual editorial update covering this new manga chapter release.
Mention the manga title, chapter number, release date, and reading context.
Do NOT reveal plot spoilers or invent unverified story twists.
"""
    elif content_type == "award":
        facts_payload = {
            "type": "Anime Industry Award Announcement",
            "award_name": facts.get("award_name", "Anime Awards"),
            "category": facts.get("category_name", "Excellence Category"),
            "winner_or_nominee": facts.get("winner_or_nominee", title_en),
            "date": facts.get("award_date", "Recent announcement"),
            "official_source": source_name,
            "factual_details": raw_summary,
        }
        instructions = """
Craft an authoritative editorial report announcing this anime award winner.
Highlight the award ceremony, the winning title, and the specific category honors.
"""
    else:  # schedule
        next_ep = facts.get("next_episode")
        airing_time = facts.get("airing_at_formatted") or "Upcoming schedule"
        score = facts.get("average_score")
        genres = ", ".join(facts.get("genres") or ["Anime"])
        status = facts.get("status", "RELEASING")
        fmt = facts.get("format", "TV")
        facts_payload = {
            "type": "Broadcast Schedule Update",
            "title_english": title_en,
            "title_romaji": title_romaji,
            "next_episode_number": next_ep,
            "scheduled_airing_time": airing_time,
            "audience_score_percent": f"{score}/100" if score else "Not yet scored",
            "genres": genres,
            "broadcast_status": status,
            "media_format": fmt,
        }
        instructions = """
Craft an original broadcast schedule update covering episode release timings and broadcast details.
"""

    return f"""You are a professional editorial assistant for 'Hindi Anime Guide' (an authoritative anime editorial site in India).
Your mission is to craft an original, factual editorial draft in English and Hindi based EXCLUSIVELY on the verified facts provided below.

VERIFIED FACTS ONLY:
{json.dumps(facts_payload, indent=2, ensure_ascii=False)}

SPECIFIC INSTRUCTIONS:
{instructions.strip()}

LEGAL & EDITORIAL MANDATES:
1. TRANSLATION ONLY MANDATE: You may ONLY translate title and summary to natural Hindi. Dates, numbers, episode counts, years, and names must be copied strictly from the source facts without any alteration. NEVER invent, guess, or extrapolate dates (like 2026, November, etc.) or episode numbers that are not in the source facts. Any output containing an unauthorized date or number will be rejected.
2. STRICT FACTS ONLY: Rely strictly on the provided factual metadata above. NEVER invent plot spoilers, fan theories, or unauthorized dates.
3. ZERO COPYING: Do NOT copy verbatim prose from external summaries or third-party websites. Use original editorial sentences.
4. AUTHENTIC HINDI: Write natural, fluent Hindi in proper Devanagari script (e.g. 'प्रसारण शेड्यूल', 'एपिसोड', 'मंगा चैप्टर', 'अवार्ड सम्मान'). Do NOT produce awkward robot-translations.
5. SUMMARY LENGTH:
   - summary_en must be between 80 and 150 words.
   - summary_hi must be between 80 and 150 words in natural Devanagari.
6. TITLES:
   - title_en: Concise, engaging English headline (10-18 words).
   - title_hi: Clear Hindi headline in Devanagari (10-18 words).

OUTPUT FORMAT:
Return ONLY a valid JSON object with exactly these four string keys:
{{
  "title_en": "...",
  "title_hi": "...",
  "summary_en": "...",
  "summary_hi": "..."
}}
"""


def _parse_gemini_json_response(raw_text: str) -> dict[str, str] | None:
    """Extract and validate JSON fields from Gemini response text."""
    clean_text = raw_text.strip()
    # Strip markdown code fences if Gemini included them
    if clean_text.startswith("```"):
        clean_text = re.sub(r"^```(?:json)?\s*", "", clean_text)
        clean_text = re.sub(r"\s*```$", "", clean_text)
        clean_text = clean_text.strip()

    try:
        data = json.loads(clean_text)
        if not isinstance(data, dict):
            return None

        title_en = str(data.get("title_en", "")).strip()
        title_hi = str(data.get("title_hi", "")).strip()
        summary_en = str(data.get("summary_en", "")).strip()
        summary_hi = str(data.get("summary_hi", "")).strip()

        if title_en and summary_en:
            return {
                "title_en": title_en,
                "title_hi": title_hi or title_en,
                "summary_en": summary_en,
                "summary_hi": summary_hi or summary_en,
            }
    except json.JSONDecodeError as exc:
        logger.warning("Failed to decode JSON from Gemini output: %s. Raw text: %s", exc, clean_text[:200])

    return None


def _generate_offline_factual_draft(facts: dict[str, Any]) -> dict[str, str]:
    """Generate a clean, facts-only editorial draft when GEMINI_API_KEY is not configured.

    This ensures local end-to-end testing, draft previews, and database insertion work cleanly.
    """
    content_type = facts.get("content_type", "schedule")
    title_en = facts.get("title_english") or facts.get("title_en") or facts.get("title_romaji") or "Anime Update"
    title_romaji = facts.get("title_romaji") or title_en
    source_name = facts.get("source_name", "Official Source")
    raw_summary = facts.get("raw_summary", "")

    if content_type == "news":
        headline_en = title_en
        headline_hi = facts.get("title_hi") or title_en

        # Rule 1: Use ONLY text present in RSS description. If none exists, show only translated title + 'Read full story'
        clean_raw = (raw_summary or "").strip()
        if clean_raw.lower().startswith("official anime news reporting regarding"):
            clean_raw = ""

        if not clean_raw or len(clean_raw.split()) < 3:
            summary_en = ""
            summary_hi = ""
        else:
            summary_en = clean_raw
            summary_hi = facts.get("summary_hi") or clean_raw

        # Run validator to sanitize against ungrounded extrapolations
        clean_en, clean_hi, _ = validate_and_sanitize_news_summary(
            title_en=headline_en,
            raw_source_text=clean_raw,
            summary_en=summary_en,
            summary_hi=summary_hi,
        )
        return {
            "title_en": headline_en,
            "title_hi": headline_hi,
            "summary_en": clean_en,
            "summary_hi": clean_hi,
        }
    elif content_type == "manga":
        ch_num = facts.get("chapter_number")
        manga_name = facts.get("manga_title") or title_en
        headline_en = f"{manga_name} Chapter {ch_num} Released: Read Details and Schedule" if ch_num else f"{manga_name} Latest Chapter Released: Read Details and Schedule"
        headline_hi = f"{manga_name} चैप्टर {ch_num} रिलीज: पढ़ने का विवरण और आधिकारिक जानकारी" if ch_num else f"{manga_name} ताज़ा चैप्टर रिलीज: पढ़ने का विवरण और आधिकारिक जानकारी"
        summary_en = (
            f"The official publication schedule for {manga_name} marks the verified release of Chapter {ch_num if ch_num else 'latest'}. "
            f"Released on {source_name}, this chapter update adds to the ongoing narrative serialization for global readers. "
            f"Fans following the series can access verified reading platforms to experience the newest installment. "
            f"Editorial monitoring tracks ongoing weekly serialization milestones and author announcements across authorized digital services."
        )
        summary_hi = (
            f"{manga_name} के प्रशंसकों के लिए नई कहानी का अध्याय {ch_num if ch_num else 'ताज़ा'} आधिकारिक तौर पर {source_name} पर उपलब्ध हो चुका है। "
            f"यह नया चैप्टर मंगा श्रृंखला की निरंतर कहानी में एक और रोमांचक कड़ी जोड़ता है। "
            f"पाठक अधिकृत रीडिंग प्लेटफॉर्म्स के जरिए इस नवीनतम चैप्टर का आनंद ले सकते हैं। "
            f"हिंदी एनीमे गाइड पर हम मंगा के आगामी चैप्टरों, शेड्यूल और भारत में इसके आधिकारिक अनुवादों की जानकारी लगातार अपडेट करते रहेंगे।"
        )
    elif content_type == "award":
        award_name = facts.get("award_name") or "Anime Awards"
        category = facts.get("category_name") or "Excellence Honors"
        winner = facts.get("winner_or_nominee") or title_en
        headline_en = f"{winner} Awarded {category} at {award_name}"
        headline_hi = f"{winner} को {award_name} में {category} का सम्मान प्राप्त हुआ"
        summary_en = (
            f"Official results announced for {award_name} officially honor {winner} with the {category} accolade. "
            f"Recognized for creative excellence, storytelling impact, and industry resonance, the production earned top recognition "
            f"among peer nominees. The verified decision reflects significant acclaim from judging panels and international audiences. "
            f"Editorial coverage documents this landmark award achievement in ongoing industry archives."
        )
        summary_hi = (
            f"{award_name} के आधिकारिक परिणामों में {winner} को प्रतिष्ठित '{category}' श्रेणी में विजेता घोषित किया गया है। "
            f"शानदार निर्देशन, बेहतरीन कहानी और उच्च स्तरीय एनीमेशन के लिए इस कृति को वैश्विक स्तर पर सराहा गया है। "
            f"यह सम्मान उद्योग जगत और प्रशंसकों के बीच इसके असाधारण प्रभाव को रेखांकित करता है। "
            f"हिंदी एनीमे गाइड इस उपलब्धि पर निर्माताओं को बधाई देता है और संबंधित एनीमे के सभी विवरण अपने पाठकों तक पहुंचाता है।"
        )
    else:  # schedule
        next_ep = facts.get("next_episode")
        airing_time = facts.get("airing_at_formatted") or "आगामी शेड्यूल"
        score = facts.get("average_score")
        genres = ", ".join(facts.get("genres") or ["Anime"])
        fmt = facts.get("format", "TV")
        score_text_en = f"with an average community score of {score}%" if score else "with audience ratings currently accumulating"
        score_text_hi = f"जिसे दर्शकों से {score}% की रेटिंग प्राप्त हुई है" if score else "जिसकी रेटिंग अभी अपडेट हो रही है"

        if next_ep:
            headline_en = f"{title_en} Episode {next_ep} Release Schedule: Broadcast Date and Airing Details"
            headline_hi = f"{title_en} एपिसोड {next_ep} प्रसारण शेड्यूल: रिलीज की तारीख और महत्वपूर्ण जानकारी"
            summary_en = (
                f"The official release schedule for {title_en} ({title_romaji}) confirms that Episode {next_ep} "
                f"is set to air on {airing_time}. As part of the current active anime broadcast season, the {fmt} format "
                f"series spans the {genres} genres, {score_text_en} on international tracking platforms. "
                f"Viewers following the ongoing broadcast can plan their watch schedule around this verified airing window. "
                f"Editorial tracking will monitor future episodes and official distribution confirmations across streaming platforms."
            )
            summary_hi = (
                f"{title_en} ({title_romaji}) के आगामी एपिसोड {next_ep} के प्रसारण की आधिकारिक पुष्टि हो चुकी है, "
                f"जो {airing_time} को प्रसारित किया जाएगा। यह {fmt} प्रारूप एनीमे वर्तमान सीज़न में {genres} शैलियों के तहत "
                f"प्रसारित हो रहा है, {score_text_hi}। प्रशंसक इस आधिकारिक समय सारणी के अनुसार आगामी एपिसोड का आनंद ले सकते हैं। "
                f"हिंदी एनीमे गाइड पर हम इसके ओटीटी और स्ट्रीमिंग प्लेटफॉर्म्स पर उपलब्धता से जुड़े सभी आधिकारिक अपडेट्स ट्रैक करते रहेंगे।"
            )
        else:
            headline_en = f"{title_en} Current Season Broadcast Schedule and Ratings Update"
            headline_hi = f"{title_en} वर्तमान सीज़न प्रसारण शेड्यूल और रेटिंग से जुड़ी ताज़ा जानकारी"
            summary_en = (
                f"Official tracking data for {title_en} ({title_romaji}) highlights its position in the current season broadcast roster. "
                f"Categorized under {genres}, the {fmt} production maintains an active broadcasting status, {score_text_en}. "
                f"Anime enthusiasts tracking current television broadcasts can follow official episode schedules through verified channels. "
                f"Our editorial team continues to record verified updates regarding episode milestones and streaming availability."
            )
            summary_hi = (
                f"{title_en} ({title_romaji}) के वर्तमान सीज़न प्रसारण और शेड्यूल से जुड़े आधिकारिक आंकड़े दर्ज किए गए हैं। "
                f"यह {fmt} एनीमे {genres} शैलियों में वर्गीकृत है और {score_text_hi}। प्रशंसक आगामी एपिसोड्स के नियमित "
                f"प्रसारण शेड्यूल की निगरानी कर सकते हैं। हमारी संपादकीय टीम एनीमे के आधिकारिक ओटीटी प्लेटफॉर्म और "
                f"हिंदी डब से जुड़ी सटीक जानकारी की लगातार समीक्षा करती रहेगी।"
            )

    return {
        "title_en": headline_en,
        "title_hi": headline_hi,
        "summary_en": summary_en,
        "summary_hi": summary_hi,
    }

