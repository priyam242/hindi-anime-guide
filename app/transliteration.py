"""Anime title transliteration engine with strict anti-machine-translation rules.

Enforces:
1. Primary title is the official English / Romaji title.
2. Hindi line is TRANSLITERATION ONLY (Devanagari phonetic transliteration).
   NEVER literal machine translations:
   - One Piece -> 'वन पीस' (NOT 'एक टुकड़ा')
   - Oshi no Ko -> 'ओशी नो को' (NOT 'ओशी नहीं को')
   - SPY x FAMILY -> 'स्पाई x फैमिली' (NOT 'जासूस परिवार')
   - Chainsaw Man -> 'चेनसॉ मैन' (NOT 'चेनसॉ आदमी')
   - The Colors Within -> 'द कलर्स विदिन' (NOT 'भीतर के रंग')
   - The Summer Hikaru Died -> 'द समर हिकारू डाइड'
   - Takopi's Original Sin -> 'ताकोपीज़ ओरिजिनल सिन'
   - Suzume -> 'सुज़ुमे'
   - Bocchi the Rock! -> 'बोची द रॉक!'
   - Black Clover -> 'ब्लैक क्लोवर' (NOT 'काला तिपतिया घास')
   - Look Back -> 'लुक बैक' (NOT 'पीछे देखना')
   - Demon Slayer -> 'डेमन स्लेयर' (NOT 'दानव कातिल' / 'दानव हत्यारा')
   - Delicious in Dungeon -> 'डिलीशियस इन डंजन' (NOT 'कालकोठरी में स्वादिष्ट')
3. Central, immutable Protected Brand / Platform / Studio Glossary.
4. Strict blacklist rejecting literal word translations.
5. Automatic model fallback chain: gemini-3.8-flash -> gemini-3.5-flash-lite -> gemini-3.6-flash.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx
import time

logger = logging.getLogger("app.transliteration")

# Primary and fallback Gemini models
PRIMARY_GEMINI_MODEL = "gemini-3.5-flash-lite"
FALLBACK_GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.6-flash"]

_GEMINI_COOLDOWN_UNTIL: float = 0.0


def get_gemini_model_chain() -> list[str]:
    """Get ordered list of Gemini models to attempt with automatic fallback."""
    primary = os.getenv("GEMINI_MODEL") or PRIMARY_GEMINI_MODEL
    chain = [primary]
    for m in FALLBACK_GEMINI_MODELS:
        if m not in chain:
            chain.append(m)
    return chain


# Central, immutable Protected Brand / Platform / Studio Glossary
PROTECTED_GLOSSARY: dict[str, str] = {
    # Streaming Platforms & Channels (keep exact English or approved phonetic, NEVER literal)
    "Prime Video": "Prime Video",
    "Amazon Prime Video": "Prime Video",
    "Amazon Prime": "Prime Video",
    "Crunchyroll": "Crunchyroll",
    "Netflix": "Netflix",
    "JioHotstar": "JioHotstar",
    "Disney+ Hotstar": "JioHotstar",
    "YouTube": "YouTube",
    "Muse Asia": "Muse Asia",
    "Ani-One Asia": "Ani-One Asia",
    "Ani-One": "Ani-One",
    # Publishers & Broadcasters
    "VIZ Media": "VIZ Media",
    "Viz Media": "VIZ Media",
    "MANGA Plus": "MANGA Plus",
    "Manga Plus": "MANGA Plus",
    "TV Tokyo": "TV Tokyo",
    # Animation Studios
    "Aniplex": "Aniplex",
    "MAPPA": "MAPPA",
    "ufotable": "ufotable",
    "Toei Animation": "Toei Animation",
    "Bones": "Bones",
    "Madhouse": "Madhouse",
    "Kadokawa": "Kadokawa",
    "Shueisha": "Shueisha",
    "TOHO animation": "TOHO animation",
    "TOHO": "TOHO",
    "Shaft": "Shaft",
    "CloverWorks": "CloverWorks",
    "Wit Studio": "Wit Studio",
    "Studio Ghibli": "Studio Ghibli",
    "Kyoto Animation": "Kyoto Animation",
    "CoMix Wave Films": "CoMix Wave Films",
    "Pierrot": "Pierrot",
    "Studio Pierrot": "Studio Pierrot",
    "A-1 Pictures": "A-1 Pictures",
    "Production I.G": "Production I.G",
    "Bandai Namco": "Bandai Namco",
    "Kodansha": "Kodansha",
    "Shogakukan": "Shogakukan",
    "Pony Canyon": "Pony Canyon",
    "NBCUniversal": "NBCUniversal",
    "Fuji TV": "Fuji TV",
    "TBS": "TBS",
    "TMS": "TMS",
    "Twin Engine": "Twin Engine",
    "Shochiku": "Shochiku",
    "Bushiroad": "Bushiroad",
    "Science SARU": "Science SARU",
}

# Known literal machine translation corruptions and their clean canonical replacements
CORRUPTED_LITERAL_MAPPINGS: dict[str, str] = {
    "एक टुकड़ा": "वन पीस",
    "काला तिपतिया घास": "ब्लैक क्लोवर",
    "काला तिपतिया": "ब्लैक क्लोवर",
    "जासूस एक्स परिवार": "स्पाई x फैमिली",
    "जासूस x परिवार": "स्पाई x फैमिली",
    "ज़ासूस एक्स परिवार": "स्पाई x फैमिली",
    "ज़ासूस x परिवार": "स्पाई x फैमिली",
    "जासूस परिवार": "स्पाई x फैमिली",
    "ज़ासूस परिवार": "स्पाई x फैमिली",
    "पीछे देखना": "लुक बैक",
    "दानव कातिल": "डेमन स्लेयर",
    "दानव हत्यारा": "डेमन स्लेयर",
    "कालकोठरी में स्वादिष्ट": "डिलीशियस इन डंजन",
    "डिलिशियस इन डंजन": "डिलीशियस इन डंजन",
    "ग्रीष्म हिकारू की मृत्यु हो गई": "द समर हिकारू डाइड",
    "द समर हिकारु डाइड": "द समर हिकारू डाइड",
    "अशांत मीडिया": "VIZ Media",
    "अर्थात": "VIZ Media",
    "कृपया मुझे बताएं": "TV Tokyo",
    "चेनसॉ आदमी": "चेनसॉ मैन",
    "भीतर के रंग": "द कलर्स विदिन",
    "ओशी नहीं को": "ओशी नो को",
    "अकेला स्तर": "सोलो लेवलिंग",
    "आक्रमण पर टाइटन": "अटैक ऑन टाइटन",
    "मौत का नोट": "डेथ नोट",
    "लड़का और बगुला": "द बॉय एंड द हेरॉन",
    "नीला ताला": "ब्लू लॉक",
    "टट्टू घाटी": "Pony Canyon",
    "सर्वज्ञ पाठक का दृष्टिकोण": "ओम्निसिएंट रीडर्स व्यूपॉइंट",
    "सर्वज्ञ पाठक": "ओम्निसिएंट रीडर्स व्यूपॉइंट",
    "महानतम एस्टेट डेवलपर": "द ग्रेटेस्ट एस्टेट डेवलपर",
    "पतन 2026": "फ़ॉल 2026",
    "पतझड़ 2026": "फ़ॉल 2026",
}

# Unit test blacklist: literal machine translation phrases forbidden inside anime titles
BLACKLIST_TRANSLATION_WORDS = [
    "एक टुकड़ा",
    "काला तिपतिया घास",
    "काला तिपतिया",
    "जासूस एक्स परिवार",
    "जासूस x परिवार",
    "ज़ासूस एक्स परिवार",
    "ज़ासूस x परिवार",
    "जासूस परिवार",
    "ज़ासूस परिवार",
    "जासूस",
    "ज़ासूस",
    "परिवार",
    "पीछे देखना",
    "दानव कातिल",
    "दानव हत्यारा",
    "कालकोठरी में स्वादिष्ट",
    "ग्रीष्म हिकारू की मृत्यु हो गई",
    "अशांत मीडिया",
    "अर्थात",
    "कृपया मुझे बताएं",
    "आदमी",
    "भीतर के रंग",
    "भीतर",
    "ओशी नहीं को",
    "नहीं को",
    "मर गया",
    "अकेला स्तर",
    "जादू की लड़ाई",
    "लड़का और बगुला",
    "बगुला",
    "नीला ताला",
    "आक्रमण पर टाइटन",
    "मौत का नोट",
    "टट्टू घाटी",
    "सर्वज्ञ पाठक",
    "महानतम एस्टेट",
]


def sanitize_proper_nouns(text: str) -> str:
    """Sanitize any corrupted machine-translated proper nouns using the central glossary and mapping."""
    if not text:
        return ""
    result = text
    # 1. Replace known corrupted literal translations
    for bad_phrase, clean_replacement in CORRUPTED_LITERAL_MAPPINGS.items():
        if bad_phrase in result:
            result = result.replace(bad_phrase, clean_replacement)

    # 2. Enforce protected glossary brands if standalone or mangled
    for brand, canonical in PROTECTED_GLOSSARY.items():
        # Check if brand appears as a standalone word
        pattern = r'\b' + re.escape(brand) + r'\b'
        result = re.sub(pattern, canonical, result, flags=re.IGNORECASE)

    # 3. Clean up standalone 'मुख्य' when used inappropriately as a platform/brand name
    if result.strip() == "मुख्य":
        result = "Prime Video"

    return result


# Reviewed canonical override dictionary for verified anime titles
REVIEWED_TRANSLITERATION_MAP: dict[str, str] = {
    "renegade immortal": "रेनेगेड इम्मॉर्टल",
    "xian ni": "रेनेगेड इम्मॉर्टल",
    "muse asia": "Muse Asia",
    "ani-one asia": "Ani-One Asia",
    "one piece": "वन पीस",
    "one piece film: red": "वन पीस फिल्म: रेड",
    "oshi no ko": "ओशी नो को",
    "spy x family": "स्पाई x फैमिली",
    "spyxfamily": "स्पाई x फैमिली",
    "chainsaw man": "चेनसॉ मैन",
    "the colors within": "द कलर्स विदिन",
    "the summer hikaru died": "द समर हिकारू डाइड",
    "takopi's original sin": "ताकोपीज़ ओरिजिनल सिन",
    "suzume": "सुज़ुमे",
    "bocchi the rock!": "बोची द रॉक!",
    "bocchi the rock": "बोची द रॉक!",
    "omniscient reader's viewpoint": "ओम्निसिएंट रीडर्स व्यूपॉइंट",
    "omniscient reader": "ओम्निसिएंट रीडर्स व्यूपॉइंट",
    "the greatest estate developer": "द ग्रेटेस्ट एस्टेट डेवलपर",
    "jujutsu kaisen": "जुजुत्सु काइसेन",
    "jujutsu kaisen 0": "जुजुत्सु काइसेन 0",
    "jujutsu kaisen season 2": "जुजुत्सु काइसेन सीज़न 2",
    "solo leveling": "सोलो लेवलिंग",
    "solo leveling season 2 -arise from the shadow-": "सोलो लेवलिंग सीज़न 2 -अराइज फ्रॉम द शैडो-",
    "look back": "लुक बैक",
    "demon slayer": "डेमन स्लेयर",
    "demon slayer: kimetsu no yaiba": "डेमन स्लेयर: किमेत्सु नो याइबा",
    "demon slayer: kimetsu no yaiba infinity castle": "डेमन स्लेयर: इनफिनिटी कैसल",
    "demon slayer: kimetsu no yaiba hashira training arc": "डेमन स्लेयर: हाशिरा ट्रेनिंग आर्क",
    "demon slayer: kimetsu no yaiba swordsmith village arc": "डेमन स्लेयर: स्वॉर्डस्मिथ विलेज आर्क",
    "demon slayer: kimetsu no yaiba entertainment district arc": "डेमन स्लेयर: एंटरटेनमेंट डिस्ट्रिक्ट आर्क",
    "delicious in dungeon": "डिलीशियस इन डंजन",
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
    "dragon ball": "ड्रैगन बॉल",
    "dragon ball super": "ड्रैगन बॉल सुपर",
    "dragon ball super: beerus": "ड्रैगन बॉल सुपर: बीरस",
    "beerus": "बीरस",
    "reincarnated as a sword": "रीइनकार्नेटेड ऐज़ अ स्वॉर्ड",
    "detective conan": "डिटेक्टिव कोनन",
    "steel ball run": "स्टील बॉल रन",
    "jojo's bizarre adventure": "जोजोज़ बिज़ारे एडवेंचर",
    "yu-sanchi! from yu-hachi": "यू-सांची! फ्रॉम यू-हाची",
    "as a reincarnated aristocrat, i'll use my appraisal skill to rise in the world": "एज अ रीइन्कार्नेटेड एरिस्टोक्रेट, आई विल यूज़ माई अप्रेज़ल स्किल टू राइज़ इन द वर्ल्ड",
    "as a reincarnated aristocrat": "एज अ रीइन्कार्नेटेड एरिस्टोक्रेट",
    "even the student council has its holes!": "ईवन द स्टूडेंट काउंसिल हैज़ इट्स होल्स!",
    "even the student council has its holes": "ईवन द स्टूडेंट काउंसिल हैज़ इट्स होल्स!",
    "inherit the winds": "इन्हेरिट द विंड्स",
    "multi-mind mayhem": "मल्टी-माइंड मेहेम",
    "the siren: becoming the villain's family": "द सायरन: बिकमिंग द विलेन्स फैमिली",
    "the siren": "द सायरन",
    "atashin'chi next": "अताशिनची नेक्स्ट",
    "atashin'chi": "अताशिनची",
    "living it up in another world until i get bored": "लिविंग इट अप इन अनदर वर्ल्ड अंटिल आई गेट बोर्ड",
    "debut or die!": "डेब्यू ऑर डाई!",
    "debut or die": "डेब्यू ऑर डाई",
    "nia liston": "निया लिस्टन",
    "fool night": "फूल नाइट",
    "witch on the holy night": "विच ऑन द होली नाइट",
    "the ramparts of ice": "द रैम्पार्ट्स ऑफ आइस",
    "hello, i am a witch and my crush wants me to make a love potion!": "हैलो, आई एम अ विच एंड माई क्रश वॉन्ट्स मी टू मेक अ लव पोशन!",
    "i'm dating a dark summoner": "आई एम डेटिंग अ डार्क समनर",
    "the detective is already dead": "द डिटेक्टिव इज़ ऑलरेडी डेड",
    "a wild last boss appeared!": "अ वाइल्ड लास्ट बॉस अपियर्ड!",
    "the cold sato-san is only sweet to me": "द कोल्ड सातो-सान इज़ ओनली स्वीट टू मी",
    "the iceblade sorcerer shall rule the world": "द आइसब्लेड सॉर्सरर शैल रूल द वर्ल्ड",
    "so what's wrong with getting reborn as a goblin?": "सो व्हॉट्स रॉन्ग विथ गेटिंग रीबॉर्न ऐज़ अ गॉब्लिन?",
    "full clearing another world under a goddess with zero believers": "फुल क्लियरिंग अनदर वर्ल्ड अंडर अ गॉडेस विथ ज़ीरो बिलीवर्स",
    "the world's strongest witch": "द वर्ल्ड्स स्ट्रॉन्गेस्ट विच",
    "a tale of the secret saint": "अ टेल ऑफ द सीक्रेट सेंट",
    "fx fighter kurumi-chan": "एफएक्स फाइटर कुरुमी-चान",
    "romelia war chronicle": "रोमेलिया वॉर क्रॉनिकल",
    # Sunday & Airing Schedule reviewed transliterations
    "reborn as a space mercenary: i woke up piloting the strongest starship!": "रीबॉर्न ऐज़ अ स्पेस मर्सनरी: आई वोक अप पायलटिंग द स्ट्रांगेस्ट स्टारशिप!",
    "reborn as a space mercenary": "रीबॉर्न ऐज़ अ स्पेस मर्सनरी",
    "aoashi": "आओअशी",
    "holo no graffiti": "होलो नो ग्राफीटी",
    "my girlfriend's friend": "माई गर्लफ्रेंड्स फ्रेंड",
    "even though i'm a super timid noble girl, i accepted the bet from my cunning fiancé": "ईवन दो आई ऍम अ सुपर टिमिड नोबल गर्ल, आई एक्सेप्टेड द बेट फ्रॉम माई कनिंग फियांसे",
    "even though i'm a super timid noble girl": "ईवन दो आई ऍम अ सुपर टिमिड नोबल गर्ल",
    "mission: yozakura family": "मिशन: योज़ाकुरा फैमिली",
    "tank chair": "टैंक चेयर",
    "now that i can control reality with a mouse cursor, i'm gonna click away on the girls!": "नाउ दैट आई कैन कंट्रोल रियलिटी विथ अ माउस कर्सर, आई ऍम गोना क्लिक अवे ऑन द गर्ल्स!",
    "star detective precure!": "स्टार डिटेक्टिव प्रीक्योर!",
    "star detective precure": "स्टार डिटेक्टिव प्रीक्योर",
    "from far away": "फ्रॉम फार अवे",
    "uncle's obsession with cute things": "अंकल्स ऑब्सेशन विथ क्यूट थिंग्स",
    "ace of the diamond": "ऐस ऑफ द डायमंड",
    "ace of the diamond act ii -second season-": "ऐस ऑफ द डायमंड एक्ट II - सेकंड सीज़न",
    "ace of the diamond act ii": "ऐस ऑफ द डायमंड एक्ट II",
    "hotel inhumans": "होटल इनह्यूमन्स",
    "koupen-chan": "कौपेन-चान",
    "umayuru: full gate!": "उमायुरू: फुल गेट!",
    "umayuru": "उमायुरू",
    "sazae-san": "साज़ाए-सान",
    "magical sisters luluttolilly": "मैजिकल सिस्टर्स लुलुट्टोलिली",
    "onegai aipri": "ओनेगाई आईप्री",
    "little shark's day out": "लिटिल शार्क्स डे आउट",
    "wushen zhuzai: da wei pian": "वूशेन झूज़ई: दा वेई पियन",
    "wushen zhuzai": "वूशेन झूज़ई",
    "i’m dating a dark summoner omake shorts": "आई ऍम डेटिंग अ डार्क समनर ओमाके शॉर्ट्स",
    "i'm dating a dark summoner omake shorts": "आई ऍम डेटिंग अ डार्क समनर ओमाके शॉर्ट्स",
    "my girlfriend's friend mini anime": "माई गर्लफ्रेंड्स फ्रेंड मिनी एनीमे",
    "yuruyuru zukan": "युरुयुरु ज़ुकान",
    "raccoon dog & fox": "रैकून डॉग एंड फॉक्स",
    "pop pap polters": "पॉप पैप पोल्टर्स",
    "punirunes puni 4": "पुनिरून्स पुनी 4",
    "punirunes": "पुनिरून्स",
    "the exiled heavy knight knows how to game the system": "द एक्साइल्ड हेवी नाइट नोज़ हाउ टू गेम द सिस्टम",
    "the laid-off cheat-granting mage enjoys a second lease on life": "द लेड-ऑफ चीट-ग्रांटिंग मेज एन्जॉयस अ सेकंड लीज ऑन लाइफ",
    "a returner's magic should be special": "अ रिटर्नर्स मैजिक शुड बी स्पेशल",
    "nia liston: the merciless maiden": "निया लिस्टन: द मर्सिलेस मेडन",
    "magic repo man: dumped by my party, i’ll cash in with a cute support fairy to become the strongest!": "मैजिक रीपो मैन: डम्पड बाय माई पार्टी, आई विल कैश इन विथ अ क्यूट सपोर्ट फेयरी टू बिकम द स्ट्रांगेस्ट!",
    "magic repo man": "मैजिक रीपो मैन",
    "psyren": "सायरेन",
    "shin chan": "शिन चैन",
    "the vermilion mask": "द वर्मिलियन मास्क",
    "chitose is in the ramune bottle 2nd cour": "चितोसे इज़ इन द रामुने बॉटल 2nd कोर",
    "chitose is in the ramune bottle": "चितोसे इज़ इन द रामुने बॉटल",
    "sasaki and peeps": "सासाकी एंड पीप्स",
    "magic knight rayearth (2026)": "मैजिक नाइट रेअर्थ (2026)",
    "magic knight rayearth": "मैजिक नाइट रेअर्थ",
    "red river": "रेड रिवर",
    "#i'm looking for a zombie": "आई ऍम लुकिंग फॉर अ ज़ॉम्बी",
    "i'm looking for a zombie": "आई ऍम लुकिंग फॉर अ ज़ॉम्बी",
    "doraemon (2005)": "डोरेमोन (2005)",
    "doraemon": "डोरेमोन",
    "gensou suikoden": "गेनसो सुइकोडेन",
    "magical girl raising project restart": "मैजिकल गर्ल रेज़िंग प्रोजेक्ट रीस्टार्ट",
    "magical girl raising project": "मैजिकल गर्ल रेज़िंग प्रोजेक्ट",
    "super psychic policeman chojo": "सुपर साइकिक पुलिसमैन चोजो",
    "the seven knights of the marronnier kingdom": "द सेवन नाइट्स ऑफ द मैरोनियर किंगडम",
    "horror collector": "हॉरर कलेक्टर",
    "tetsuryo! meet with tetsudou musume": "तेत्सुर्यो! मीट विथ तेत्सुदो मुसुमे",
    "girls und panzer: motto love love sakusen desu!": "गर्ल्स एंड पैंज़र: मोटो लव लव साकुसेन देसु!",
    "dawang raoming 3": "दावांग रावमिंग 3",
    "dawang raoming": "दावांग रावमिंग",
    "dandivine": "डांडीवाइन",
    "dark machine the animation": "डार्क मशीन द एनिमेशन",
    "soul land 2: the peerless tang clan": "सोल लैंड 2: द पीयरलेस तांग क्लैन",
    "soul land": "सोल लैंड",
    "with vengeance, sincerely, your broken saintess": "विथ वेन्जेंस, सिंसियरली, योर ब्रोकन सेंटेस",
    "battle spirits [re] zekkai no ku": "बैटल स्पिरिट्स [री] ज़ेक्काई नो कु",
    "keroro☆": "केरोरो☆",
    "rilakkuma": "रिलाक्कुमा",
    "the prince of tennis ii u-17 world cup: final member selection match": "द प्रिंस ऑफ टेनिस II U-17 वर्ल्ड कप: फाइनल मेंबर सिलेक्शन मैच",
    "the prince of tennis": "द प्रिंस ऑफ टेनिस",
    "mofusand": "मोफूसैंड",
    "yi nian yongheng 4": "यी नियान योंगहेंग 4",
    "yi nian yongheng": "यी नियान योंगहेंग",
    "fanren xiu xian zhuan di wu ji": "फैनरेन श्यू शियान जुआन दी वू जी",
    "beyond time's gaze 2": "बियॉन्ड टाइम्स गेज़ 2",
    "beyond time's gaze": "बियॉन्ड टाइम्स गेज़",
    "maomao no hitorigoto 3rd season": "माओमाओ नो हितोरीगोतो 3rd सीज़न",
    "maomao no hitorigoto": "माओमाओ नो हितोरीगोतो",
    "cardfight!! vanguard divinez: parallactic fate": "कार्डफाइट!! वैनगार्ड डिवाइनिज़: पैरालैक्टिक फेट",
    "cardfight!! vanguard": "कार्डफाइट!! वैनगार्ड",
    "a good day to ascend": "अ गुड डे टू असेंड",
    "hotaru no yomeiri: choitashi": "होतारू नो योमेइरी: चोइताशी",
    "sirotan": "सिरोतान",
    "pan no akachan (tv)": "पैन नो अकाचान (TV)",
    "pan no akachan": "पैन नो अकाचान",
    "dali si rizhi 3": "दाली सी रिझी 3",
    "dali si rizhi": "दाली सी रिझी",
    "da xia shou mu ren": "दा शिया शौ मू रेन",
    "petitcure: precure fairies season 4": "पेटीटक्योर: प्रीक्योर फेयरीज़ सीज़न 4",
    "petitcure: precure fairies": "पेटीटक्योर: प्रीक्योर फेयरीज़",
    "jueshi zhan hun 2": "ज्युएशी झान हून 2",
    "nezumi-kun no chokki (tv) 2nd season": "नेज़ुमी-कुन नो चोक्की (TV) 2nd सीज़न",
    "chibi godzilla raids again season 4": "चिबी गॉडज़िला रेड्स अगेन सीज़न 4",
    "chibi godzilla raids again": "चिबी गॉडज़िला रेड्स अगेन",
    "gelpiyo 2": "गेलपियो 2",
    "gelpiyo": "गेलपियो",
    "sylvanian families: freya no colorful everyday": "सिल्वेनियन फैमिलीज़: फ्रेया नो कलरफुल एवरीडे",
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
    en_words = re.findall(r'[a-zA-Z0-9]+', title_en)
    hi_words = [w for w in re.split(r'[\s:,\-–!|()]+', title_hi) if w.strip()]

    if len(en_words) >= 2 and len(hi_words) > (len(en_words) * 2.5 + 2):
        return False, f"Syllable shape mismatch: Hindi title '{title_hi}' has too many tokens ({len(hi_words)}) compared to English ({len(en_words)})"

    return True, "Valid transliteration"


def transliterate_with_gemini(title: str, timeout_seconds: float = 12.0) -> str | None:
    """Transliterate an anime title into Devanagari phonetically using Gemini with automatic fallback chain.

    Primary: gemini-3.5-flash-lite
    Fallback 1: gemini-3.8-flash
    Fallback 2: gemini-3.6-flash
    Strict prompt ensures ZERO translation of meaning.
    """
    global _GEMINI_COOLDOWN_UNTIL
    if time.time() < _GEMINI_COOLDOWN_UNTIL:
        return None

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        try:
            from app.config import get_settings
            gemini_key = get_settings().gemini_api_key
        except Exception:
            pass
    if not gemini_key:
        return None

    model_chain = get_gemini_model_chain()

    prompt = (
        "You are a specialized phonetic transliteration engine for anime, manga, and entertainment titles into Hindi Devanagari script.\n"
        "CRITICAL MANDATE: Transliterate proper titles phonetically into Devanagari script (e.g., 'One Piece' -> 'वन पीस', 'Demon Slayer' -> 'डेमन स्लेयर', 'Black Clover' -> 'ब्लैक क्लोवर', 'Spy x Family' -> 'स्पाई x फैमिली', 'Look Back' -> 'लुक बैक', 'Delicious in Dungeon' -> 'डिलीशियस इन डंजन', 'The Summer Hikaru Died' -> 'द समर हिकारू डाइड'). NEVER translate the English words of a title into their literal Hindi meanings.\n"
        "Keep proper nouns, brand names, character names, and studio names phonetically accurate or in their original English/Devanagari form.\n\n"
        f"Transliterate this title phonetically into Devanagari script:\n{title}\n\n"
        "Return ONLY the transliterated Devanagari string without explanation or markdown."
    )

    for model_name in model_chain:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"
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
                        text = sanitize_proper_nouns(text)
                        is_ok, reason = validate_transliteration(title, text)
                        if is_ok:
                            logger.info("Transliteration succeeded with model '%s' for '%s' -> '%s'", model_name, title, text)
                            return text
                        logger.warning("Gemini model '%s' output rejected by validator: %s", model_name, reason)
                elif resp.status_code == 429:
                    _GEMINI_COOLDOWN_UNTIL = time.time() + 60.0
                    logger.warning("Gemini model '%s' hit 429 rate limit. Setting 60s cooldown.", model_name)
                    break
                elif resp.status_code in (404, 500, 503):
                    logger.warning("Gemini model '%s' returned HTTP %d. Attempting fallback model...", model_name, resp.status_code)
                else:
                    logger.warning("Gemini model '%s' failed with HTTP %d: %s", model_name, resp.status_code, resp.text[:100])
        except Exception as exc:
            logger.warning("Gemini transliteration call failed for model '%s' on '%s': %s", model_name, title, exc)

    return None


def transliterate_anime_title(title_en: str) -> str:
    """Produce an authentic Hindi Devanagari transliteration for an anime title.

    Resolution Pipeline:
    1. Direct match in reviewed canonical dictionary.
    2. Substring / base-title match in reviewed dictionary with suffix handling.
    3. Gemini phonetic transliteration (with strict anti-translation prompt and validator, model fallback).
    4. Safe Fallback: Original English/Romaji title (NEVER invoke dictionary translation on proper nouns).
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
            result = sanitize_proper_nouns(result)
            is_ok, _ = validate_transliteration(clean_en, result)
            if is_ok:
                return result

    # 3. Gemini transliteration with automatic model fallback
    gemini_out = transliterate_with_gemini(clean_en)
    if gemini_out:
        return gemini_out

    # 4. Deterministic phonetic fallback for remaining suffixes
    result = clean_en
    for s_pat, s_hi in SUFFIX_REPLACEMENTS:
        result = re.sub(s_pat, s_hi, result, flags=re.IGNORECASE)

    result = sanitize_proper_nouns(result)

    # If result contains Devanagari and passes validator, return it
    if any('\u0900' <= ch <= '\u097f' for ch in result):
        is_ok, _ = validate_transliteration(clean_en, result)
        if is_ok:
            return result

    # Absolute fallback: Return the official English title rather than a wrong Hindi word
    return clean_en
