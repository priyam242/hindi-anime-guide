"""Anime title transliteration engine with strict anti-machine-translation rules.

Enforces:
1. Primary title is the official English / Romaji title.
2. Hindi line is TRANSLITERATION ONLY (Devanagari phonetic transliteration).
   NEVER literal machine translations:
   - One Piece -> 'वन पीस' (NOT 'एक टुकड़ा')
   - Oshi no Ko -> 'ओशी नो को' (NOT 'ओशी नहीं को')
   - SPY x FAMILY -> 'स्पाई x फ़ैमिली' (NOT 'जासूस परिवार')
   - Chainsaw Man -> 'चेनसॉ मैन' (NOT 'चेनसॉ आदमी')
   - The Colors Within -> 'द कलर्स विदिन' (NOT 'भीतर के रंग')
   - The Summer Hikaru Died -> 'द समर हिकारु डाइड'
   - Takopi's Original Sin -> 'ताकोपीज़ ओरिजिनल सिन'
   - Suzume -> 'सुज़ुमे'
   - Bocchi the Rock! -> 'बोची द रॉक!'
3. Strict blacklist rejecting literal word translations.
4. Syllable shape / token ratio validation.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger("app.transliteration")

# Unit test blacklist: literal machine translation phrases forbidden inside anime titles
BLACKLIST_TRANSLATION_WORDS = [
    "एक टुकड़ा",
    "जासूस",
    "ज़ासूस",
    "परिवार",
    "आदमी",
    "भीतर के रंग",
    "भीतर",
    "ओशी नहीं को",
    "नहीं को",
    "मर गया",
    "दानव हत्यारा",
    "अकेला स्तर",
    "जादू की लड़ाई",
    "लड़का और बगुला",
    "बगुला",
    "नीला ताला",
    "आक्रमण पर टाइटन",
    "मौत का नोट",
]

# Reviewed canonical override dictionary for verified anime titles
REVIEWED_TRANSLITERATION_MAP: dict[str, str] = {
    "one piece": "वन पीस",
    "one piece film: red": "वन पीस फिल्म: रेड",
    "oshi no ko": "ओशी नो को",
    "spy x family": "स्पाई x फ़ैमिली",
    "spyxfamily": "स्पाई x फ़ैमिली",
    "chainsaw man": "चेनसॉ मैन",
    "the colors within": "द कलर्स विदिन",
    "the summer hikaru died": "द समर हिकारु डाइड",
    "takopi's original sin": "ताकोपीज़ ओरिजिनल सिन",
    "suzume": "सुज़ुमे",
    "bocchi the rock!": "बोची द रॉक!",
    "bocchi the rock": "बोची द रॉक!",
    "jujutsu kaisen": "जुजुत्सु काइसेन",
    "jujutsu kaisen 0": "जुजुत्सु काइसेन 0",
    "jujutsu kaisen season 2": "जुजुत्सु काइसेन सीज़न 2",
    "solo leveling": "सोलो लेवलिंग",
    "solo leveling season 2 -arise from the shadow-": "सोलो लेवलिंग सीज़न 2 -अराइज फ्रॉम द शैडो-",
    "look back": "लुक बैक",
    "demon slayer: kimetsu no yaiba": "डेमन स्लेयर: किमेत्सु नो याइबा",
    "demon slayer: kimetsu no yaiba infinity castle": "डेमन स्लेयर: इनफिनिटी कैसल",
    "demon slayer: kimetsu no yaiba hashira training arc": "डेमन स्लेयर: हाशिरा ट्रेनिंग आर्क",
    "demon slayer: kimetsu no yaiba swordsmith village arc": "डेमन स्लेयर: स्वॉर्डस्मिथ विलेज आर्क",
    "demon slayer: kimetsu no yaiba entertainment district arc": "डेमन स्लेयर: एंटरटेनमेंट डिस्ट्रिक्ट आर्क",
    "my hero academia": "माई हीरो एकेडेमिया",
    "my hero academia final season": "माई हीरो एकेडेमिया फाइनल सीज़न",
    "frieren: beyond journey's end": "फ़्रीरेन: बियॉन्ड जर्नीज़ एंड",
    "gachiakuta": "गाचियाकुता",
    "mobile suit gundam gquuuuuux": "मोबाइल सूट गुंडम GQuuuuuuX",
    "mobile suit gundam": "मोबाइल सूट गुंडम",
    "the first slam dunk": "द फर्स्ट स्लैम डंक",
    "cyberpunk: edgerunners": "साइबरपंक: एजरनर्स",
    "dandadan": "दांदादान",
    "sakamoto days": "साकामोटो डेज़",
    "blue lock": "ब्लू लॉक",
    "attack on titan": "अटैक ऑन टाइटन",
    "naruto": "नारुतो",
    "death note": "डेथ नोट",
    "kaiju no. 8": "काइजू नंबर 8",
    "haikyu!!": "हाइक्यू!!",
    "bleach": "ब्लीच",
    "black clover": "ब्लैक क्लोवर",
    "overgeared": "ओवरगियर्ड",
    "blue box": "ब्लू बॉक्स",
    "ao no hako": "आओ नो हाको",
    "firefly wedding": "फायरफ्लाई वेडिंग",
    "hotaru no yomeiri": "होतारू नो योमेइरी",
    "the boy and the heron": "द बॉय एंड द हेरॉन",
    "tokyo revengers": "टोक्यो रिवेंजर्स",
    "the apothecary diaries": "द एपोथेकेरी डायरीज़",
    "kusuriya no hitorigoto": "कुसुरिया नो हितोरीगोतो",
    "kimi no na wa.": "किमी नो ना वा",
    "your name.": "योर नेम",
    "danmachi": "डैनमाची",
    "re:zero -starting life in another world-": "री:ज़ीरो",
    "mushoku tensei": "मुशोकु तेन्सेई",
    "dr. stone": "डॉक्टर स्टोन",
    "vinland saga": "विनलैंड सागा",
    "hunter x hunter": "हंटर x हंटर",
    "fullmetal alchemist": "फुलमेटल अल्केमिस्ट",
    "steins;gate": "स्टाइन्स;गेट",
    "death parade": "डेथ परेड",
    "cowboy bebop": "काउबॉय बीबॉप",
    "neon genesis evangelion": "नियोन जेनेसिस इवान्गेलियन",
    "ranma 1/2": "रानमा 1/2",
    "ranma": "रानमा",
    "bakemonogatari": "बाकेमोनोगातारी",
    "monogatari": "मोनोगातारी",
    "the angel next door spoils me rotten": "द एंजल नेक्स्ट डोर स्पॉइल्स मी रोटन",
    "gushing over magical girls": "गशिंग ओवर मैजिकल गर्ल्स",
    "magical explorer": "मैजिकल एक्सप्लोरर",
    "vertex force": "वर्टेक्स फोर्स",
    "tsugumi project": "त्सुगुमी प्रोजेक्ट",
    "patlabor": "पैटलेबर",
    "berserk of gluttony": "बरसर्क ऑफ ग्लटनी",
}

# Common subtitle / anime descriptor suffixes and their transliterations
SUFFIX_REPLACEMENTS = [
    (r'\bfinal\s+season\b', "फाइनल सीज़न"),
    (r'\bseason\s*(\d+)\b', r"सीज़न \1"),
    (r'\bpart\s*(\d+)\b', r"पार्ट \1"),
    (r'\b2nd\s*season\b', "2nd सीज़न"),
    (r'\b3rd\s*season\b', "3rd सीज़न"),
    (r'\bmovie\b', "फिल्म"),
    (r'\bfilm\b', "फिल्म"),
    (r'\barc\b', "आर्क"),
    (r'\bepisode\s*(\d+)\b', r"एपिसोड \1"),
    (r'\bchapter\s*(\d+)\b', r"चैप्टर \1"),
]


def validate_transliteration(title_en: str, title_hi: str) -> tuple[bool, str]:
    """Validate that title_hi is a phonetic transliteration, not a machine translation.

    Checks:
    1. Blacklist check: Rejects forbidden literal translation words (e.g. 'एक टुकड़ा', 'जासूस', etc.).
    2. Character check: Ensures title_hi contains Devanagari script characters.
    3. Syllable/token count shape: Rejects verbose machine-translation expansions.
    """
    if not title_hi or not title_hi.strip():
        return False, "Empty Hindi title"

    # 1. Blacklist validation
    for bad_word in BLACKLIST_TRANSLATION_WORDS:
        if bad_word in title_hi:
            return False, f"Blacklisted literal translation word detected: '{bad_word}' in '{title_hi}'"

    # 2. Devanagari presence
    has_devanagari = any('\u0900' <= ch <= '\u097f' for ch in title_hi)
    if not has_devanagari:
        return False, f"Title does not contain Devanagari characters: '{title_hi}'"

    # 3. Syllable / word token shape ratio check
    # Machine translations typically blow up in word count compared to transliteration
    en_words = re.findall(r'[a-zA-Z0-9]+', title_en)
    hi_words = [w for w in re.split(r'[\s:,\-–!|()]+', title_hi) if w.strip()]

    if len(en_words) >= 2 and len(hi_words) > (len(en_words) * 2.5 + 2):
        return False, f"Syllable shape mismatch: Hindi title '{title_hi}' has too many tokens ({len(hi_words)}) compared to English ({len(en_words)})"

    return True, "Valid transliteration"


def transliterate_with_gemini(title: str, timeout_seconds: float = 6.0) -> str | None:
    """Transliterate an anime title into Devanagari phonetically using Gemini.

    Strict prompt ensures ZERO translation of meaning.
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        return None

    model_name = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"

    prompt = (
        "You are a specialized phonetic transliteration engine for anime titles into Hindi Devanagari script.\n"
        "STRICT MANDATE: TRANSLITERATE ONLY. DO NOT TRANSLATE MEANING INTO HINDI WORDS.\n"
        "Examples of correct transliteration:\n"
        "- One Piece -> वन पीस (NEVER एक टुकड़ा)\n"
        "- SPY x FAMILY -> स्पाई x फ़ैमिली (NEVER जासूस परिवार)\n"
        "- Chainsaw Man -> चेनसॉ मैन (NEVER चेनसॉ आदमी)\n"
        "- The Colors Within -> द कलर्स विदिन (NEVER भीतर के रंग)\n"
        "- Oshi no Ko -> ओशी नो को (NEVER ओशी नहीं को)\n"
        "- The Summer Hikaru Died -> द समर हिकारु डाइड\n"
        "- Takopi's Original Sin -> ताकोपीज़ ओरिजिनल सिन\n"
        "- Suzume -> सुज़ुमे\n"
        "- Bocchi the Rock! -> बोची द रॉक!\n"
        "- Solo Leveling -> सोलो लेवलिंग\n\n"
        f"Transliterate this anime title into Hindi Devanagari script:\n{title}\n\n"
        "Return ONLY the transliterated Devanagari string without explanation or markdown."
    )

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.post(
                url,
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            if resp.status_code == 200:
                data = resp.json()
                cand = data.get("candidates", [])
                if cand:
                    text = cand[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                    # Strip any markdown quotes or code blocks
                    text = re.sub(r'["`]', '', text).strip()
                    is_ok, reason = validate_transliteration(title, text)
                    if is_ok:
                        return text
                    logger.warning("Gemini transliteration rejected by validator: %s", reason)
    except Exception as exc:
        logger.warning("Gemini transliteration call failed for '%s': %s", title, exc)

    return None


def transliterate_anime_title(title_en: str) -> str:
    """Produce an authentic Hindi Devanagari transliteration for an anime title.

    Resolution Pipeline:
    1. Direct match in reviewed canonical dictionary.
    2. Substring / base-title match in reviewed dictionary with suffix handling.
    3. Gemini phonetic transliteration (with strict anti-translation prompt and validator).
    4. Fallback: Clean transliterated title or English original (NEVER literal machine translation).
    """
    if not title_en or not title_en.strip():
        return ""

    clean_en = title_en.strip()
    norm_en = clean_en.lower()

    # 1. Direct dictionary match
    if norm_en in REVIEWED_TRANSLITERATION_MAP:
        return REVIEWED_TRANSLITERATION_MAP[norm_en]

    # Clean punctuation for dictionary check
    stripped_norm = re.sub(r'[!?:,\-–]+', ' ', norm_en).strip()
    stripped_norm = re.sub(r'\s+', ' ', stripped_norm)
    if stripped_norm in REVIEWED_TRANSLITERATION_MAP:
        return REVIEWED_TRANSLITERATION_MAP[stripped_norm]

    # 2. Check for base title match in dictionary + suffix handling
    for base_en, base_hi in sorted(REVIEWED_TRANSLITERATION_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        pattern = r'\b' + re.escape(base_en) + r'\b'
        if re.search(pattern, norm_en):
            # Replace base part with Hindi
            result = re.sub(pattern, base_hi, clean_en, flags=re.IGNORECASE)
            # Apply suffix transliterations
            for s_pat, s_hi in SUFFIX_REPLACEMENTS:
                result = re.sub(s_pat, s_hi, result, flags=re.IGNORECASE)
            is_ok, _ = validate_transliteration(clean_en, result)
            if is_ok:
                return result

    # 3. Gemini transliteration
    gemini_out = transliterate_with_gemini(clean_en)
    if gemini_out:
        return gemini_out

    # 4. Deterministic phonetic fallback for remaining suffixes
    result = clean_en
    for s_pat, s_hi in SUFFIX_REPLACEMENTS:
        result = re.sub(s_pat, s_hi, result, flags=re.IGNORECASE)

    # If result contains Devanagari and passes validator, return it
    if any('\u0900' <= ch <= '\u097f' for ch in result):
        is_ok, _ = validate_transliteration(clean_en, result)
        if is_ok:
            return result

    # Absolute fallback: Return the official English title rather than a wrong Hindi word
    return clean_en
