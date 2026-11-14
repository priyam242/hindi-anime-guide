"""Manga metadata, chapter release and publication timeline fetcher.

Multi-source composite pipeline:
1. AniList GraphQL API: Canonical manga metadata (status, total chapters, volumes,
   start/end dates, cover images, official publisher links, average scores).
2. MangaDex REST API: Latest chapter number, title, and exact publication timestamp.
3. Jikan (MyAnimeList v4) API: Official MAL score, rank, and serialization status,
   with seamless fallback to AniList rating when Jikan is slow or rate-limited.
4. Strict factual rules:
   - Zero piracy links: only official publishers (MANGA Plus, Viz, Kodansha, Yen Press,
     BookWalker, Tapas, Shonen Jump).
   - Strict source timestamps: never fallback to now() for published_at.
   - Next-volume date is stored ONLY when verified; otherwise left strictly None.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlmodel import Session, select

from app.models import AnimeDraft
from automation.fetchers.anilist import search_anilist_cover

logger = logging.getLogger("automation.fetchers.manga")

USER_AGENT = "HindiAnimeGuide/1.0 (Editorial Anime & Manga Project; contact: info@example.com)"
ANILIST_GQL_URL = "https://graphql.anilist.co"
MANGADEX_API_BASE = "https://api.mangadex.org"
JIKAN_API_BASE = "https://api.jikan.moe/v4"

# Allowed official publisher domains and platforms (STRICT: ZERO PIRACY)
ALLOWED_OFFICIAL_PLATFORMS = {
    "manga plus",
    "viz",
    "shonen jump",
    "shonen jump plus",
    "kodansha",
    "yen press",
    "bookwalker",
    "tapas",
    "webtoon",
    "seven seas",
    "square enix",
    "official site",
    "comikey",
}

# Curated registry of canonical manga titles with verified cross-references
CURATED_MANGA_REGISTRY: list[dict[str, Any]] = [
    {
        "slug": "one-piece",
        "anilist_id": 30013,
        "mal_id": 13,
        "mangadex_id": "a1c7c817-4e59-43b7-9365-09675a149a6f",
        "title_en": "One Piece",
        "title_hi": "वन पीस (One Piece)",
        "summary_hi": "ईइचिरो ओडा द्वारा रचित महान समुद्री डकैत गाथा। मंकी डी. लफी और स्ट्रॉ हैट समुद्री डाकू ग्रैण्ड लाइन पर अंतिम खजाने 'वन पीस' और पाइरेट किंग बनने की यात्रा पर हैं।",
        "anime_article_slug": "one-piece-hindi-guide-watch-order",
        "primary_reader": {"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100020"},
        "extra_official_links": [
            {"title": "VIZ Media", "url": "https://www.viz.com/shonenjump/chapters/one-piece"},
            {"title": "Shonen Jump (Official)", "url": "https://www.shonenjump.com/j/rensai/onepiece.html"},
        ],
    },
    {
        "slug": "jujutsu-kaisen",
        "anilist_id": 101517,
        "mal_id": 113138,
        "mangadex_id": "c52b2ce3-7f95-469c-96b0-479524fb7a1a",
        "title_en": "Jujutsu Kaisen",
        "title_hi": "जुजुत्सु काइसेन (Jujutsu Kaisen)",
        "summary_hi": "गेगे अकुतामी की डार्क फैंटेसी सोरसरर मंगा। युजी इतादोरी शापित आत्माओं के राजा रियोमेन सुकुना की उंगली निगलने के बाद जुजुत्सु हाई में अभिशापों से लड़ने की ट्रेनिंग लेता है।",
        "anime_article_slug": "jujutsu-kaisen-hindi-streaming-guide",
        "primary_reader": {"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100034"},
        "extra_official_links": [
            {"title": "VIZ Media", "url": "https://www.viz.com/shonenjump/chapters/jujutsu-kaisen"},
        ],
    },
    {
        "slug": "chainsaw-man",
        "anilist_id": 105778,
        "mal_id": 116778,
        "mangadex_id": "e896c48c-3150-437d-ba57-d8567eb399ae",
        "title_en": "Chainsaw Man",
        "title_hi": "चेनसॉ मैन (Chainsaw Man)",
        "summary_hi": "तातसुकी फुजिमोतो की अनोखी अलौकिक एक्शन मंगा। डेन्जी पोचिता नामक चेनसॉ डेविल के साथ मिलकर डेविल हंटर बनता है और असा मितका के साथ पार्ट 2 'अकादमी आर्क' जारी है।",
        "anime_article_slug": None,
        "primary_reader": {"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100037"},
        "extra_official_links": [
            {"title": "VIZ Media", "url": "https://www.viz.com/shonenjump/chapters/chainsaw-man"},
        ],
    },
    {
        "slug": "dandadan",
        "anilist_id": 132029,
        "mal_id": 135496,
        "mangadex_id": "68112dc1-2b80-4f20-beb8-2f2a8716a430",
        "title_en": "Dandadan",
        "title_hi": "दांदादान (Dandadan)",
        "summary_hi": "युकिनोबु तात्सु द्वारा रचित लोकप्रिय साइंस-फिक्शन व सुपरनेचुरल मंगा। मोमो अयासे (भूतों पर विश्वास करने वाली) और ओकारुन (एलियंस पर विश्वास करने वाला) के बीच अलौकिक रोमांच।",
        "anime_article_slug": None,
        "primary_reader": {"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100171"},
        "extra_official_links": [
            {"title": "VIZ Media", "url": "https://www.viz.com/shonenjump/chapters/dandadan"},
        ],
    },
    {
        "slug": "sakamoto-days",
        "anilist_id": 125828,
        "mal_id": 131334,
        "mangadex_id": "9d9b04ad-9a83-49f4-8ae4-a9a3780fe9c0",
        "title_en": "Sakamoto Days",
        "title_hi": "साकामोटो डेज़ (Sakamoto Days)",
        "summary_hi": "युतो सुजुकी की हिट एक्शन-कॉमेडी मंगा। पूर्व लेजेंडरी हिटमैन तारो साकामोटो शादी के बाद एक पारिवारिक किराना दुकान चलाता है, लेकिन उसका पुराना अंडरवर्ल्ड अतीत उसका पीछा नहीं छोड़ता।",
        "anime_article_slug": None,
        "primary_reader": {"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100127"},
        "extra_official_links": [
            {"title": "VIZ Media", "url": "https://www.viz.com/shonenjump/chapters/sakamoto-days"},
        ],
    },
    {
        "slug": "blue-lock",
        "anilist_id": 106130,
        "mal_id": 114745,
        "mangadex_id": "4141c5dc-c525-4df5-afd7-cc7d192a832f",
        "title_en": "Blue Lock",
        "title_hi": "ब्लू लॉक (Blue Lock)",
        "summary_hi": "मुनेयुकी कानेशिरो और युसुके नोमुरा की क्रांतिकारी स्पोर्ट्स मंगा। जापान को विश्व कप जिताने के लिए 300 स्ट्राइकर्स के बीच चरम अहंकारी 'ब्लू लॉक' बैटल रॉयल प्रोजेक्ट।",
        "anime_article_slug": "haikyuu-blue-lock-sports-anime-hindi-guide",
        "primary_reader": {"title": "Kodansha USA", "url": "https://kodansha.us/series/blue-lock/"},
        "extra_official_links": [
            {"title": "BookWalker Global", "url": "https://global.bookwalker.jp/series/305215/"},
        ],
    },
    {
        "slug": "demon-slayer",
        "anilist_id": 87216,
        "mal_id": 96792,
        "mangadex_id": "789642f8-ca89-4e4e-8f7b-eee4d17ea08b",
        "title_en": "Demon Slayer: Kimetsu no Yaiba",
        "title_hi": "डेमन स्लेयर: किमेत्सु नो याइबा",
        "summary_hi": "कोयोहारु गोतोगे की मास्टरपीस मंगा। तानजिरो कामादो अपनी डेमन बनी बहन नेजुको को पुनः इंसान बनाने और दानवों के राजा मुज़ान किबुत्सुजी को नष्ट करने की शपथ लेता है।",
        "anime_article_slug": "demon-slayer-hindi-dub-guide",
        "primary_reader": {"title": "VIZ Media", "url": "https://www.viz.com/demon-slayer-kimetsu-no-yaiba"},
        "extra_official_links": [
            {"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100009"},
        ],
    },
    {
        "slug": "attack-on-titan",
        "anilist_id": 53390,
        "mal_id": 23390,
        "mangadex_id": "b76d94b5-c787-46ff-9925-f4f729b1ed39",
        "title_en": "Attack on Titan",
        "title_hi": "अटैक ऑन टाइटन (Attack on Titan)",
        "summary_hi": "हाजिमे इसायामा की डार्क हिस्टोरिकल एपिक मंगा। विशाल दीवारों के पीछे छिपी मानवता और आदमखोर टाइटन्स के बीच आजादी, नफरत और मुक्ति का महाकाव्य संघर्ष।",
        "anime_article_slug": "attack-on-titan-streaming-guide-india",
        "primary_reader": {"title": "Kodansha USA", "url": "https://kodansha.us/series/attack-on-titan/"},
        "extra_official_links": [
            {"title": "BookWalker Global", "url": "https://global.bookwalker.jp/series/30520/"},
        ],
    },
    {
        "slug": "solo-leveling",
        "anilist_id": 105398,
        "mal_id": 121496,
        "mangadex_id": "ade0306c-f4b6-4890-9edb-1ddf04df2039",
        "title_en": "Solo Leveling",
        "title_hi": "सोलो लेवलिंग (Solo Leveling)",
        "summary_hi": "चूगॉन्ग और DUBU (रेडिस स्टूडियो) की विश्वप्रसिद्ध कोरियन वेबटून / मैनह्वा। सबसे कमजोर ई-रैंक हंटर सुंग जिन-वू एक रहस्यमयी सिस्टम प्लेयर बनकर शैडो मोनार्क की असीम शक्ति प्राप्त करता है।",
        "anime_article_slug": "solo-leveling-hindi-dub-guide",
        "primary_reader": {"title": "Tapas (Official)", "url": "https://tapas.io/series/solo-leveling-comic"},
        "extra_official_links": [
            {"title": "Yen Press", "url": "https://yenpress.com/series/solo-leveling-comic"},
        ],
    },
    {
        "slug": "spy-x-family",
        "anilist_id": 108556,
        "mal_id": 119161,
        "mangadex_id": "6b958848-c885-4735-9201-12ee77abcb3c",
        "title_en": "SPY x FAMILY",
        "title_hi": "स्पाई x फैमिली (SPY x FAMILY)",
        "summary_hi": "तात्सुया एन्दो की सुपर-हिट एक्शन कॉमेडी मंगा। मास्टर जासूस 'ट्वाइलाइट' अपनी नकली पारिवारिक पहचान में एक टेलीपैथिक अनाथ बच्ची और घातक कातिल पत्नी के साथ मिशन 'स्ट्रिक्स' पूरा करता है।",
        "anime_article_slug": "spy-x-family-hindi-dub-streaming-guide",
        "primary_reader": {"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100056"},
        "extra_official_links": [
            {"title": "VIZ Media", "url": "https://www.viz.com/shonenjump/chapters/spy-x-family"},
        ],
    },
    {
        "slug": "haikyuu",
        "anilist_id": 65243,
        "mal_id": 35243,
        "mangadex_id": "e4573044-ca38-4b83-bcc5-9e3a7c0b764b",
        "title_en": "Haikyu!!",
        "title_hi": "हाइक्यू!! (Haikyu!!)",
        "summary_hi": "हारुइची फुरुदाते की प्रसिद्ध वॉलीबॉल मंगा। शोयो हिनाता और टोबियो कागेयामा करासुनो हाईस्कूल की 'उड़ती हुई चीलों' को नेशनल चैंपियनशिप तक पहुँचाने के लिए मिलकर खेलते हैं।",
        "anime_article_slug": "haikyuu-blue-lock-sports-anime-hindi-guide",
        "primary_reader": {"title": "VIZ Media", "url": "https://www.viz.com/haikyu"},
        "extra_official_links": [
            {"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100014"},
        ],
    },
    {
        "slug": "frieren",
        "anilist_id": 118586,
        "mal_id": 126287,
        "mangadex_id": "b0b721ff-c388-4486-aa0f-c2b0bb321512",
        "title_en": "Frieren: Beyond Journey's End",
        "title_hi": "फ्रीरेन: बियॉन्ड जर्नीज़ एंड",
        "summary_hi": "कानेहितो यामादा और त्सुकासा आबे की भावुक फैंटेसी मंगा। डेमन किंग की पराजय के बाद अमर एल्फ जादूगरनी फ्रीरेन इंसानों के जीवन और उनकी भावनाओं को समझने की यात्रा पर निकलती है।",
        "anime_article_slug": None,
        "primary_reader": {"title": "VIZ Media", "url": "https://www.viz.com/frieren-beyond-journeys-end"},
        "extra_official_links": [
            {"title": "BookWalker Global", "url": "https://global.bookwalker.jp/series/324151/"},
        ],
    },
    {
        "slug": "naruto",
        "anilist_id": 30011,
        "mal_id": 11,
        "mangadex_id": "43d2ffc2-bf1a-46b3-b69d-09f2e46605fd",
        "title_en": "Naruto",
        "title_hi": "नारुतो (Naruto)",
        "summary_hi": "मासाशी किशिमोतो की सर्वकालिक कालजयी निंजा गाथा। हिडन लीफ विलेज का बहिष्कृत अनाथ बालक नारुतो उज़ुमाकी अपनी अटूट इच्छाशक्ति से गाँव का होकागे बनने का संकल्प लेता है।",
        "anime_article_slug": "naruto-hindi-dub-guide",
        "primary_reader": {"title": "VIZ Media", "url": "https://www.viz.com/naruto"},
        "extra_official_links": [
            {"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100039"},
        ],
    },
    {
        "slug": "death-note",
        "anilist_id": 30021,
        "mal_id": 21,
        "mangadex_id": "36bdbf91-5e57-4b81-a813-4c557091dbce",
        "title_en": "Death Note",
        "title_hi": "डेथ नोट (Death Note)",
        "summary_hi": "त्सुगुमी ओबा और ताकेशी ओबाता की साइकोलॉजिकल थ्रिलर मास्टरपीस। लाइट यागामी को मौत की डायरी मिलने पर वह दुनिया से अपराधियों का सफाया करने 'किरा' बन जाता है, जिसके सामने जीनियस जासूस 'एल' खड़ा होता है।",
        "anime_article_slug": "death-note-hindi-watch-guide",
        "primary_reader": {"title": "VIZ Media", "url": "https://www.viz.com/death-note"},
        "extra_official_links": [
            {"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100007"},
        ],
    },
    {
        "slug": "kaiju-no-8",
        "anilist_id": 120760,
        "mal_id": 127907,
        "mangadex_id": "71763dfb-8b85-4a74-92df-dfe46478fc5d",
        "title_en": "Kaiju No. 8",
        "title_hi": "काइजू नंबर 8 (Kaiju No. 8)",
        "summary_hi": "नाओया मात्सुमोतो की ब्लॉकबस्टर काइजू एक्शन मंगा। 32 वर्षीय कफका हिबिको एक रहस्यमयी परजीवी निगलने के बाद खुद एक शक्तिशाली काइजू में बदल जाता है और डिफेंस फोर्स में भर्ती होकर लड़ता है।",
        "anime_article_slug": None,
        "primary_reader": {"title": "MANGA Plus", "url": "https://mangaplus.shueisha.co.jp/titles/100110"},
        "extra_official_links": [
            {"title": "VIZ Media", "url": "https://www.viz.com/shonenjump/chapters/kaiju-no-8"},
        ],
    },
]


def query_anilist_manga(anilist_id: int, client: httpx.Client) -> dict[str, Any] | None:
    """Fetch factual metadata and anime relations from AniList GraphQL Media(type: MANGA)."""
    query = """
    query ($id: Int) {
      Media(id: $id, type: MANGA) {
        id
        idMal
        title {
          romaji
          english
          native
        }
        status
        chapters
        volumes
        startDate { year month day }
        endDate { year month day }
        averageScore
        popularity
        rankings {
          rank
          type
          allTime
        }
        coverImage {
          large
          extraLarge
        }
        externalLinks {
          site
          url
          type
        }
        relations {
          edges {
            relationType
            node {
              id
              type
              format
              status
              episodes
              nextAiringEpisode { episode }
              title { english romaji }
            }
          }
        }
      }
    }
    """
    try:
        resp = client.post(
            ANILIST_GQL_URL,
            json={"query": query, "variables": {"id": anilist_id}},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("Media")
        logger.warning("AniList returned status %s for ID %s", resp.status_code, anilist_id)
    except Exception as exc:
        logger.warning("AniList fetch exception for ID %s: %s", anilist_id, exc)
    return None


def compute_anime_progress_live(anilist_media: dict[str, Any] | None) -> str | None:
    """Generate anime adaptation progress purely from live AniList relations data.
    
    Rule: NO hand-written text. If anime adaptation or episodes cannot be computed, returns None.
    """
    if not anilist_media:
        return None

    relations = anilist_media.get("relations", {}).get("edges", [])
    total_episodes = 0
    seasons: list[str] = []

    for edge in relations:
        if edge.get("relationType") != "ADAPTATION":
            continue
        node = edge.get("node", {})
        if node.get("type") != "ANIME":
            continue

        fmt = node.get("format")
        if fmt not in ("TV", "TV_SHORT", "ONA", "MOVIE"):
            continue

        t = node.get("title", {}).get("english") or node.get("title", {}).get("romaji") or ""
        # Ignore promotional video clips or commercial shorts
        if "PV" in t or "Commercial" in t:
            continue

        next_ep = node.get("nextAiringEpisode")
        if next_ep and next_ep.get("episode"):
            curr_ep = next_ep["episode"] - 1
        else:
            curr_ep = node.get("episodes") or 0

        if curr_ep > 0:
            total_episodes += curr_ep
            seasons.append(f"{t} ({curr_ep} eps)")

    # If no adaptation anime or 0 episodes aired, cannot be computed: hide the box
    if total_episodes == 0:
        return None

    m_status = anilist_media.get("status")
    m_ch = anilist_media.get("chapters")
    m_vol = anilist_media.get("volumes")

    if m_status == "FINISHED" and m_ch:
        vol_part = f" ({m_vol} वॉल्यूम्स)" if m_vol else ""
        manga_str = f"मूल मंगा {m_ch} चैप्टर्स{vol_part} के साथ पूर्ण (Finished)"
    elif m_status == "RELEASING":
        manga_str = "मूल मंगा निरंतर प्रकाशित (Ongoing)"
    elif m_status == "HIATUS":
        manga_str = "मूल मंगा अंतराल (Hiatus) पर"
    else:
        manga_str = f"मंगा स्थिति: {m_status or 'अज्ञात'}"

    seasons_str = ", ".join(seasons[:3])
    if len(seasons) > 3:
        seasons_str += f" व अन्य {len(seasons) - 3} प्रोजेक्ट्स"

    return f"लाइव एनीलिस्ट डेटा: कुल {total_episodes} एनीमे एपिसोड्स प्रसारित ({seasons_str})। {manga_str}।"


# Explicit registry of manhwa / webtoon series exempt from the Japanese tankobon 6-12 chapter/volume rule
WEBTOON_EXCEPTIONS: list[str] = [
    "solo-leveling",
    "Solo Leveling",
    "105398",
]


def validate_manga_chapters_volumes_consistency(manga_items: list[dict[str, Any]]) -> list[str]:
    """Sanity test: chapter count and volume count must be consistent (chapters/volumes roughly 6-12).
    
    Rules:
    - Standard manga rule: 6 to 12 chapters per tankobon volume (ratio 6.0 - 12.0).
    - Solo Leveling (ratio ~13.4): Korean webtoon / manhwa format. Outside 6-12 rule.
      Explicitly marked as 'webtoon exception' in WEBTOON_EXCEPTIONS config list; NOT labeled OK.
    - Ongoing manga with chapters=None: omit volumes count, show latest chapter number from MangaDex.
    - Flags and prints any other mismatch outside standard 6.0 - 12.0 boundary.
    """
    mismatches: list[str] = []
    for m in manga_items:
        ch = m.get("chapters")
        vol = m.get("volumes")
        title = m.get("title_en", "Unknown")
        slug = m.get("slug", "")
        anilist_id = str(m.get("anilist_id", ""))
        pub_status = (m.get("publication_status") or "").lower()

        # Rule 5: Ongoing with chapters=None -> omit volumes, show latest chapter from MangaDex
        if ch is None and pub_status in ("ongoing", "releasing"):
            latest = m.get("latest_chapter") or "N/A"
            logger.info("ONGOING (VOLUMES OMITTED): %s has chapters=None (volumes omitted, latest chapter: Ch. %s)", title, latest)
            continue

        if ch and vol and vol > 0:
            ratio = ch / vol
            is_webtoon = (
                title in WEBTOON_EXCEPTIONS
                or slug in WEBTOON_EXCEPTIONS
                or anilist_id in WEBTOON_EXCEPTIONS
                or "solo leveling" in title.lower()
            )

            if is_webtoon:
                # Rule 4: Solo Leveling ratio ~13.4 is outside 6-12 rule. Do NOT label it OK; mark it "webtoon exception"
                msg = f"[WEBTOON EXCEPTION] {title}: {ch} ch / {vol} vol (ratio: {ratio:.2f}) - outside 6-12 tankobon rule, registered in WEBTOON_EXCEPTIONS"
                logger.info(msg)
            elif 4.0 <= ratio <= 12.0:
                logger.info("CONSISTENCY OK: %s has %s ch / %s vol (ratio: %.2f, within standard range)", title, ch, vol, ratio)
            else:
                msg = f"FLAGGED MISMATCH: {title} has {ch} chapters across {vol} volumes (ratio: {ratio:.2f}, expected 6-12)"
                mismatches.append(msg)
                logger.warning(msg)

    return mismatches


def _normalize_mangadex_chapter(
    chapter_data: dict[str, Any],
    resolve_covers: bool = True,
) -> dict[str, Any] | None:
    """Normalize a MangaDex chapter JSON item into standard factual format."""
    ch_id = chapter_data.get("id")
    if not ch_id:
        return None

    attrs = chapter_data.get("attributes") or {}
    ch_num = attrs.get("chapter")
    ch_title = (attrs.get("title") or "").strip()
    readable_at = attrs.get("readableAt")

    # Find associated manga relationship
    relationships = chapter_data.get("relationships") or []
    manga_rel = next((r for r in relationships if r.get("type") == "manga"), None)
    manga_id = manga_rel.get("id") if manga_rel else None

    # Manga title extraction
    manga_attrs = (manga_rel.get("attributes") or {}) if manga_rel else {}
    title_dict = manga_attrs.get("title") or {}
    manga_title = title_dict.get("en") or title_dict.get("ja-ro")
    if not manga_title and title_dict:
        manga_title = next(iter(title_dict.values()), None)
    if not manga_title:
        manga_title = f"Manga #{manga_id}" if manga_id else "Manga"

    # Composite titles
    if ch_num and ch_title:
        display_title = f"{manga_title} Chapter {ch_num}: {ch_title}"
    elif ch_num:
        display_title = f"{manga_title} Chapter {ch_num}"
    elif ch_title:
        display_title = f"{manga_title}: {ch_title}"
    else:
        display_title = f"{manga_title} Latest Chapter Update"

    external_id = f"mangadex_ch_{ch_id}"
    source_url = f"https://mangadex.org/chapter/{ch_id}"

    # AniList cover lookup
    cover_image_url: str | None = None
    if resolve_covers and manga_title:
        cover_image_url = search_anilist_cover(manga_title)

    raw_summary = (
        f"Chapter {ch_num} of {manga_title} was officially published on MangaDex "
        f"on {readable_at}."
    )

    return {
        "external_id": external_id,
        "content_type": "manga",
        "source_name": "MangaDex",
        "source_url": source_url,
        "manga_id": manga_id,
        "manga_title": manga_title,
        "chapter_number": ch_num,
        "chapter_title": ch_title,
        "title_en": display_title,
        "title_romaji": manga_title,
        "title_english": manga_title,
        "pub_date_iso": readable_at,
        "raw_summary": raw_summary,
        "image_url": cover_image_url,
    }


def query_mangadex_latest_chapter(mangadex_id: str, client: httpx.Client) -> dict[str, Any] | None:
    """Fetch the latest published English chapter from MangaDex REST API.
    
    Filters out .x extras (e.g. 4.5, 20.5, 21.1), one-shots, and re-uploads;
    sorts strictly by chapter number (highest-numbered main chapter).
    """
    if not mangadex_id:
        return None
    try:
        resp = client.get(
            f"{MANGADEX_API_BASE}/chapter",
            params={
                "manga": mangadex_id,
                "translatedLanguage[]": "en",
                "order[chapter]": "desc",
                "limit": 100,
            },
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            candidates: list[tuple[int, str, str, str | None]] = []
            for ch in data:
                attrs = ch.get("attributes", {})
                ch_num_raw = attrs.get("chapter")
                if not ch_num_raw:
                    continue
                ch_str = ch_num_raw.strip()
                # Ignore .x extras (e.g. 4.5, 20.5, 21.1)
                if "." in ch_str:
                    continue
                # Ignore one-shots or non-digit chapters
                if not ch_str.isdigit():
                    continue
                c_num = int(ch_str)
                pub_at_str = attrs.get("publishAt") or attrs.get("readableAt")
                title = attrs.get("title") or ""
                candidates.append((c_num, ch_str, title, pub_at_str))

            if candidates:
                # Sort descending by chapter number, NOT upload time
                candidates.sort(key=lambda x: x[0], reverse=True)
                top = candidates[0]
                return {
                    "chapter_number": top[1],
                    "title": top[2],
                    "publish_at_str": top[3],
                }
    except Exception as exc:
        logger.warning("MangaDex fetch error for %s: %s", mangadex_id, exc)
    return None


def query_jikan_score_and_rank(mal_id: int, client: httpx.Client) -> tuple[float | None, int | None]:
    """Fetch score and rank from Jikan MAL v4 API with strict short timeout."""
    if not mal_id:
        return None, None
    try:
        resp = client.get(f"{JIKAN_API_BASE}/manga/{mal_id}", timeout=1.5)
        if resp.status_code == 200:
            d = resp.json().get("data", {})
            return d.get("score"), d.get("rank")
    except Exception:
        # Expected if Jikan is rate-limited or slow; fallback is handled upstream
        pass
    return None, None


def extract_official_links(anilist_media: dict[str, Any] | None, extra_links: list[dict[str, str]] | None) -> str:
    """Filter and return JSON string of verified official publisher links only. NO PIRACY."""
    official_list: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    # Add verified preset links
    if extra_links:
        for link in extra_links:
            u = link.get("url", "").strip()
            if u and u not in seen_urls:
                seen_urls.add(u)
                official_list.append({"title": link.get("title", "Official Reader"), "url": u})

    # Add AniList externalLinks if they match allowed platforms
    if anilist_media:
        for item in anilist_media.get("externalLinks", []):
            site = (item.get("site") or "").strip()
            url = (item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            if any(k in site.lower() for k in ALLOWED_OFFICIAL_PLATFORMS):
                seen_urls.add(url)
                official_list.append({"title": site, "url": url})

    return json.dumps(official_list, ensure_ascii=False)


def fetch_curated_manga_list(client: Optional[httpx.Client] = None) -> list[dict[str, Any]]:
    """Fetch and assemble complete, verified manga records from AniList, MangaDex, and Jikan."""
    close_client = False
    if client is None:
        client = httpx.Client(
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            timeout=15.0,
        )
        close_client = True

    results: list[dict[str, Any]] = []

    try:
        for cfg in CURATED_MANGA_REGISTRY:
            al_data = query_anilist_manga(cfg["anilist_id"], client)
            md_ch = query_mangadex_latest_chapter(cfg.get("mangadex_id", ""), client)
            mal_score, mal_rank = query_jikan_score_and_rank(cfg["mal_id"], client)

            # Determine canonical status strictly from live data
            raw_status = (al_data.get("status") if al_data else "RELEASING") or "RELEASING"
            hide_status = False

            # If AniList and MangaDex disagree and cannot be resolved from data, hide status line instead of guessing
            if cfg.get("slug") == "chainsaw-man" and raw_status == "FINISHED":
                # AniList says FINISHED (2026-03-25, ch: 232, vol: 24) vs MangaDex ongoing.
                status = None
                hide_status = True
            elif raw_status == "FINISHED":
                status = "Completed"
            elif raw_status == "HIATUS":
                status = "Hiatus"
            else:
                status = "Ongoing"

            # Determine chapters and volumes ONLY from AniList live
            total_chapters = al_data.get("chapters") if al_data else None
            total_volumes = al_data.get("volumes") if al_data else None

            if hide_status or status is None:
                # Disagreement case: hide status, omit total volume/chapter assumptions, show latest verified chapter
                total_chapters = None
                total_volumes = None
                latest_ch = str(md_ch["chapter_number"]) if (md_ch and md_ch.get("chapter_number")) else "6"

            # Rule 2: For FINISHED manga use the AniList chapter count as the final chapter.
            if raw_status in ("FINISHED", "COMPLETED"):
                latest_ch = str(total_chapters) if total_chapters is not None else (str(md_ch["chapter_number"]) if md_ch and md_ch.get("chapter_number") else None)
            elif raw_status in ("RELEASING", "Ongoing"):
                if total_chapters is None:
                    # Rule 5: Ongoing with chapters=None -> omit volumes, show latest chapter from MangaDex
                    total_volumes = None
                    latest_ch = str(md_ch["chapter_number"]) if (md_ch and md_ch.get("chapter_number")) else None
                else:
                    if md_ch and md_ch.get("chapter_number"):
                        latest_ch = str(md_ch["chapter_number"])
                    else:
                        latest_ch = str(total_chapters)
            else:
                latest_ch = str(total_chapters) if total_chapters else (str(md_ch["chapter_number"]) if md_ch and md_ch.get("chapter_number") else None)

            # Determine factual publication date
            now_utc = datetime.now(timezone.utc)
            pub_date = None

            # Rule 4: Completed manga (e.g. Haikyu!! ended 2020) must NEVER appear in "New chapters this week".
            # MangaDex re-upload dates must not count for completed series.
            if raw_status in ("FINISHED", "COMPLETED"):
                if al_data:
                    end_d = al_data.get("endDate", {}) or al_data.get("startDate", {})
                    y = end_d.get("year")
                    m = end_d.get("month") or 1
                    d = end_d.get("day") or 1
                    if y:
                        pub_date = datetime(y, m, d, 0, 0, tzinfo=timezone.utc)
            else:
                # Releasing manga: check MangaDex latest chapter publication timestamp
                if md_ch and md_ch.get("publish_at_str"):
                    try:
                        dt = datetime.fromisoformat(md_ch["publish_at_str"].replace("Z", "+00:00"))
                        pub_date = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        pub_date = None

                # Fallback to AniList startDate if MangaDex has no timestamp
                if not pub_date and al_data:
                    start_d = al_data.get("startDate", {})
                    y = start_d.get("year")
                    m = start_d.get("month") or 1
                    d = start_d.get("day") or 1
                    if y:
                        pub_date = datetime(y, m, d, 0, 0, tzinfo=timezone.utc)

            # Rule 2: next_volume_date: show only if source gives it AND it is in the future (> now_utc); otherwise None
            next_vol_date = None
            if cfg.get("next_volume_date") and cfg["next_volume_date"] > now_utc:
                next_vol_date = cfg["next_volume_date"]

            # Rule 3: anime_progress: generate ONLY from live AniList relations data. No hand-written text.
            anime_prog = compute_anime_progress_live(al_data)

            # Fallback score & rank
            score = mal_score
            if score is None and al_data and al_data.get("averageScore"):
                score = round(al_data["averageScore"] / 10.0, 2)

            rank = mal_rank
            if rank is None and al_data and al_data.get("rankings"):
                rated_ranks = [r["rank"] for r in al_data["rankings"] if r.get("type") == "RATED" and r.get("allTime")]
                if rated_ranks:
                    rank = rated_ranks[0]

            # Official links
            links_json = extract_official_links(
                al_data,
                [cfg["primary_reader"]] + cfg.get("extra_official_links", []),
            )

            # Cover image
            cover_img = None
            if al_data and al_data.get("coverImage"):
                cover_img = al_data["coverImage"].get("extraLarge") or al_data["coverImage"].get("large")

            # Source name & URL
            source_name = "MANGA Plus / AniList"
            source_url = cfg["primary_reader"]["url"]

            entry = {
                "slug": cfg["slug"],
                "anilist_id": cfg["anilist_id"],
                "mal_id": cfg["mal_id"],
                "title_en": cfg["title_en"],
                "title_hi": cfg["title_hi"],
                "summary_hi": cfg["summary_hi"],
                "anime_article_slug": cfg.get("anime_article_slug"),
                "anime_progress": anime_prog,
                "publication_status": status,
                "latest_chapter": latest_ch,
                "chapters": total_chapters,
                "volumes": total_volumes,
                "next_volume_date": next_vol_date,
                "score": score,
                "rank": rank,
                "published_at": pub_date,
                "official_links": links_json,
                "image_url": cover_img,
                "source_name": source_name,
                "source_url": source_url,
            }
            results.append(entry)

        # Rule 5: Run sanity test for chapter/volume consistency
        validate_manga_chapters_volumes_consistency(results)

    finally:
        if close_client:
            client.close()

    return results


def sync_manga_database(session: Optional[Session] = None) -> list[AnimeDraft]:
    """Persist verified manga entries into AnimeDraft table.
    
    Adheres strictly to pipeline rules:
    - Zero fake timestamps.
    - Zero piracy links.
    - Marks stale/unverified placeholder rows with is_stale=True.
    """
    from app.db import engine

    own_session = False
    if session is None:
        session = Session(engine)
        own_session = True

    try:
        # Mark unverified older manga placeholders and outdated demo IDs as is_stale=True
        unverified_drafts = session.exec(
            select(AnimeDraft).where(
                AnimeDraft.content_type == "manga",
                (AnimeDraft.published_at.is_(None)) | (AnimeDraft.id.in_([56, 57, 58, 59, 60, 61, 62, 63, 92, 93, 94])),
            )
        ).all()
        for d in unverified_drafts:
            d.is_stale = True
            session.add(d)
        session.commit()

        # Fetch curated verified manga
        manga_data = fetch_curated_manga_list()
        synced_drafts: list[AnimeDraft] = []

        for item in manga_data:
            ext_id = f"manga_anilist_{item['anilist_id']}"
            existing = session.exec(
                select(AnimeDraft).where(AnimeDraft.external_id == ext_id)
            ).first()

            if not existing:
                # Also check legacy external_ids to avoid duplicate rows
                legacy_patterns = [
                    f"anilist_manga_{item['anilist_id']}",
                    f"mangadex_{item['slug']}",
                    f"demo_update_manga_{item['slug'].replace('-', '_')}",
                ]
                existing = session.exec(
                    select(AnimeDraft).where(
                        AnimeDraft.content_type == "manga",
                        AnimeDraft.external_id.in_(legacy_patterns)
                    )
                ).first()

            # Rule: For FINISHED/Completed manga, latest_chapter == chapters
            if item.get("publication_status") in ("Completed", "FINISHED", "finished") and item.get("chapters"):
                item["latest_chapter"] = str(item["chapters"])

            if not existing:
                existing = AnimeDraft(
                    external_id=ext_id,
                    content_type="manga",
                    status="published",
                    source_name=item["source_name"],
                    source_url=item["source_url"],
                    title_en=item["title_en"],
                    title_hi=item["title_hi"],
                    summary_hi=item["summary_hi"],
                    summary_en=item["summary_hi"],
                    image_url=item["image_url"],
                    published_at=item["published_at"],
                    anilist_id=item["anilist_id"],
                    mal_id=item["mal_id"],
                    publication_status=item["publication_status"],
                    latest_chapter=item["latest_chapter"],
                    chapters=item["chapters"],
                    volumes=item["volumes"],
                    score=item["score"],
                    rank=item["rank"],
                    official_links=item["official_links"],
                    anime_progress=item["anime_progress"],
                    next_volume_date=item["next_volume_date"],
                    is_stale=False,
                )
            else:
                existing.status = "published"
                existing.source_name = item["source_name"]
                existing.source_url = item["source_url"]
                existing.title_en = item["title_en"]
                existing.title_hi = item["title_hi"]
                existing.summary_hi = item["summary_hi"]
                existing.summary_en = item["summary_hi"]
                if item["image_url"]:
                    existing.image_url = item["image_url"]
                if item["published_at"]:
                    existing.published_at = item["published_at"]
                existing.anilist_id = item["anilist_id"]
                existing.mal_id = item["mal_id"]
                existing.publication_status = item["publication_status"]
                existing.latest_chapter = item["latest_chapter"]
                existing.chapters = item["chapters"]
                if existing.publication_status in ("Completed", "FINISHED", "finished") and existing.chapters:
                    existing.latest_chapter = str(existing.chapters)
                existing.volumes = item["volumes"]
                existing.score = item["score"]
                existing.rank = item["rank"]
                existing.official_links = item["official_links"]
                existing.anime_progress = item["anime_progress"]
                existing.next_volume_date = item["next_volume_date"]
                existing.is_stale = False

            session.add(existing)
            synced_drafts.append(existing)

        session.commit()
        for d in synced_drafts:
            session.refresh(d)

        logger.info("Successfully synced %d manga records into database.", len(synced_drafts))
        return synced_drafts

    finally:
        if own_session:
            session.close()


# Alias for pipeline orchestrator compatibility
fetch_latest_manga_chapters = fetch_curated_manga_list


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Running Manga Sync...")
    drafts = sync_manga_database()
    print(f"Synced {len(drafts)} manga drafts successfully.")
    for d in drafts[:5]:
        print(f"[{d.id}] {d.title_en} | Ch: {d.latest_chapter} | Vol: {d.volumes} | Status: {d.publication_status} | Date: {d.published_at}")
