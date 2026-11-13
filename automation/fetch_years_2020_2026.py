"""Fetch and ingest top anime from 2020 to 2026 into Hindi Anime Guide.

Collects iconic popular anime across 2020, 2021, 2022, 2023, 2024, 2025, and 2026 from AniList GraphQL,
creates bilingual (English + Hindi) editorial entries, and stores them safely with status="published".
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlmodel import Session, select

from app.db import engine
from app.models import AnimeDraft
from app.routers.pages import invalidate_public_caches

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("automation.years_2020_2026")

ANILIST_URL = "https://graphql.anilist.co"

HINDI_GENRE_MAP = {
    "Action": "एक्शन",
    "Adventure": "रोमांच (Adventure)",
    "Comedy": "कॉमेडी",
    "Drama": "ड्रामा",
    "Fantasy": "फैंटेसी",
    "Horror": "हॉरर",
    "Mystery": "रहस्य (Mystery)",
    "Psychological": "साइकोलॉजिकल",
    "Romance": "रोमांस",
    "Sci-Fi": "साइ-फाइ (Sci-Fi)",
    "Slice of Life": "स्लाइस ऑफ लाइफ",
    "Supernatural": "अलौकिक (Supernatural)",
    "Thriller": "थ्रिलर",
}

HINDI_TITLE_OVERRIDES = {
    "JUJUTSU KAISEN": "जुजुत्सु काइसेन (Jujutsu Kaisen)",
    "Demon Slayer -Kimetsu no Yaiba- The Movie: Mugen Train": "डेमन स्लेयर: मुगेन ट्रेन मूवी (Demon Slayer: Mugen Train)",
    "Kaguya-sama: Love is War?": "कागुया-सामा: लव इज़ वॉर? (Kaguya-sama Season 2)",
    "Re:ZERO -Starting Life in Another World- Season 2": "री:ज़ीरो - स्टार्टिंग लाइफ इन अनॉदर वर्ल्ड सीज़न 2",
    "Tower of God": "टावर ऑफ गॉड (Tower of God)",
    "Rent-a-Girlfriend": "रेंट-ए-गर्लफ्रेंड (Rent-a-Girlfriend)",
    "Attack on Titan Final Season": "अटैक ऑन टाइटन: फाइनल सीज़न (Attack on Titan)",
    "Horimiya": "होरिमिया (Horimiya)",
    "Mushoku Tensei: Jobless Reincarnation": "मुशोकु तेन्सेई: जॉबलेस रीइन्कार्नेशन",
    "Tokyo Revengers": "टोक्यो रिवेंजर्स (Tokyo Revengers)",
    "JUJUTSU KAISEN 0": "जुजुत्सु काइसेन 0 (Jujutsu Kaisen 0 Movie)",
    "My Hero Academia Season 5": "माई हीरो एकेडेमिया सीज़न 5 (My Hero Academia)",
    "Chainsaw Man": "चेनसॉ मैन (Chainsaw Man)",
    "SPY x FAMILY": "स्पाई x फैमिली (SPY x FAMILY)",
    "Demon Slayer: Kimetsu no Yaiba Entertainment District Arc": "डेमन स्लेयर: एंटरटेनमेंट डिस्ट्रिक्ट आर्क",
    "Attack on Titan Final Season Part 2": "अटैक ऑन टाइटन: फाइनल सीज़न भाग 2",
    "My Dress-Up Darling": "माई ड्रेस-अप डार्लिंग (My Dress-Up Darling)",
    "Cyberpunk: Edgerunners": "साइबरपंक: एजराॅनर्स (Cyberpunk: Edgerunners)",
    "JUJUTSU KAISEN Season 2": "जुजुत्सु काइसेन सीज़न 2 (हिडन इन्वेंटरी / शिबुया इंसिडेंट)",
    "Frieren: Beyond Journey’s End": "फ़्रीरेन: बियॉन्ड जर्नीज़ एंड (Frieren)",
    "Demon Slayer: Kimetsu no Yaiba Swordsmith Village Arc": "डेमन स्लेयर: स्वॉर्डस्मिथ विलेज आर्क",
    "Hell’s Paradise": "हेल्स पैराडाइज (Hell's Paradise: Jigokuraku)",
    "The Apothecary Diaries": "द अपोथिकेरी डायरीज़ (The Apothecary Diaries)",
    "OSHI NO KO": "ओशी नो को (OSHI NO KO)",
    "Solo Leveling": "सोलो लेवलिंग (Solo Leveling)",
    "DAN DA DAN": "डैन डा डैन (DAN DA DAN)",
    "Kaiju No. 8": "काइजू नंबर 8 (Kaiju No. 8)",
    "Demon Slayer: Kimetsu no Yaiba Hashira Training Arc": "डेमन स्लेयर: हशीरा ट्रेनिंग आर्क",
    "Delicious in Dungeon": "डिलिशियस इन डंजन (Delicious in Dungeon)",
    "Mushoku Tensei: Jobless Reincarnation Season 2 Part 2": "मुशोकु तेन्सेई सीज़न 2 पार्ट 2",
    "Solo Leveling Season 2 -Arise from the Shadow-": "सोलो लेवलिंग सीज़न 2: अराइज फ्रॉम द शैडो",
    "Gachiakuta": "गाचियाकुता (Gachiakuta)",
    "Chainsaw Man – The Movie: Reze Arc": "चेनसॉ मैन: रेज़ आर्क मूवी (Chainsaw Man Movie)",
    "DAN DA DAN Season 2": "डैन डा डैन सीज़न 2 (DAN DA DAN Season 2)",
    "SAKAMOTO DAYS": "साकामोतो डेज़ (SAKAMOTO DAYS)",
    "The Fragrant Flower Blooms With Dignity": "द फ्रैग्रेंट फ्लावर ब्लूम्स विद डिग्निटी",
    "JUJUTSU KAISEN Season 3: The Culling Game Part 1": "जुजुत्सु काइसेन सीज़न 3: द कलिंग गेम पार्ट 1",
    "Witch Hat Atelier": "विच हैट एटेलियर (Witch Hat Atelier)",
    "Frieren: Beyond Journey’s End Season 2": "फ़्रीरेन: बियॉन्ड जर्नीज़ एंड सीज़न 2",
    "Hell’s Paradise Season 2": "हेल्स पैराडाइज सीज़न 2 (Hell's Paradise Season 2)",
    "Mushoku Tensei: Jobless Reincarnation Season 3": "मुशोकु तेन्सेई: जॉबलेस रीइन्कार्नेशन सीज़न 3",
    "Sentenced to Be a Hero": "सेंटेंस्ड टू बी अ हीरो (Sentenced to Be a Hero)",
}

ANILIST_QUERY = """
query ($seasonYear: Int, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(type: ANIME, seasonYear: $seasonYear, sort: POPULARITY_DESC) {
      id
      title {
        romaji
        english
        native
      }
      seasonYear
      season
      format
      episodes
      status
      averageScore
      genres
      description(asHtml: false)
      coverImage {
        large
        extraLarge
      }
      siteUrl
    }
  }
}
"""


def clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_editorial_content(media: dict[str, Any]) -> dict[str, str]:
    title_en = media["title"]["english"] or media["title"]["romaji"]
    title_hi = HINDI_TITLE_OVERRIDES.get(title_en) or f"{title_en} (एनीमे गाइड)"
    year = media.get("seasonYear") or 2024
    season = (media.get("season") or "").capitalize()
    episodes = media.get("episodes")
    ep_str = f"{episodes} एपिसोड" if episodes else "ऑनगोइंग / घोषित एपिसोड्स"
    ep_str_en = f"{episodes} episodes" if episodes else "ongoing / announced episodes"
    score = media.get("averageScore")
    score_str = f"{score}/100" if score else "अपेक्षित"
    genres = media.get("genres") or []
    genres_hi = ", ".join(HINDI_GENRE_MAP.get(g, g) for g in genres[:3]) or "एक्शन, फैंटेसी"
    genres_en = ", ".join(genres[:3]) or "Action, Fantasy"
    raw_desc = clean_text(media.get("description"))

    summary_en = (
        f"{title_en} is one of the most prominent anime releases of {year} ({season} season). "
        f"Categorized under {genres_en}, the series features {ep_str_en} with a critical rating of {score_str} on AniList. "
        f"Synopsis: {raw_desc[:240]}... "
        f"Indian anime fans can discover verified streaming availability, watch order details, and Hindi audio dubbing updates right here on Hindi Anime Guide."
    )

    summary_hi = (
        f"{title_hi} वर्ष {year} ({season} सीज़न) का एक अत्यंत लोकप्रिय और चर्चित एनीमे है। "
        f"यह सीरीज़ मुख्य रूप से {genres_hi} शैली में आती है, जिसमें कुल {ep_str} और एनीलिस्ट पर {score_str} की रेटिंग दर्ज है। "
        f"कहानी का परिचय: {raw_desc[:220]}... "
        f"भारत में एनीमे देखने वाले दर्शकों के लिए इस सीरीज़ की आधिकारिक स्ट्रीमिंग प्लेटफॉर्म, हिंदी डब की उपलब्धता और वॉच ऑर्डर से जुड़ी संपूर्ण जानकारी 'Hindi Anime Guide' पर उपलब्ध है।"
    )

    return {
        "title_en": title_en,
        "title_hi": title_hi,
        "summary_en": summary_en,
        "summary_hi": summary_hi,
    }


def ingest_top_anime_2020_to_2026(per_year: int = 6) -> int:
    """Fetch top anime for each year from 2020 to 2026 and save to database."""
    total_added = 0
    total_updated = 0

    with Session(engine) as session:
        for year in range(2020, 2027):
            logger.info("Fetching Top %d anime for year %d from AniList...", per_year, year)
            try:
                resp = httpx.post(
                    ANILIST_URL,
                    json={"query": ANILIST_QUERY, "variables": {"seasonYear": year, "perPage": per_year}},
                    timeout=20.0,
                    headers={"User-Agent": "HindiAnimeGuide-Bot/1.0"}
                )
                if resp.status_code != 200:
                    logger.warning("AniList query failed for year %d (HTTP %d)", year, resp.status_code)
                    continue

                media_list = resp.json().get("data", {}).get("Page", {}).get("media", [])
                for media in media_list:
                    external_id = f"anilist-{media['id']}"
                    editorial = build_editorial_content(media)
                    image_url = media.get("coverImage", {}).get("extraLarge") or media.get("coverImage", {}).get("large")

                    existing = session.exec(
                        select(AnimeDraft).where(AnimeDraft.external_id == external_id)
                    ).first()

                    if existing:
                        existing.title_en = editorial["title_en"]
                        existing.title_hi = editorial["title_hi"]
                        existing.summary_en = editorial["summary_en"]
                        existing.summary_hi = editorial["summary_hi"]
                        existing.image_url = image_url or existing.image_url
                        existing.status = "published"
                        existing.published_at = existing.published_at or datetime.now(timezone.utc)
                        session.add(existing)
                        total_updated += 1
                    else:
                        draft = AnimeDraft(
                            content_type="schedule",
                            status="published",
                            source_name="AniList",
                            source_url=media.get("siteUrl") or f"https://anilist.co/anime/{media['id']}",
                            external_id=external_id,
                            title_en=editorial["title_en"],
                            title_hi=editorial["title_hi"],
                            summary_en=editorial["summary_en"],
                            summary_hi=editorial["summary_hi"],
                            image_url=image_url,
                            published_at=datetime.now(timezone.utc),
                        )
                        session.add(draft)
                        total_added += 1

                session.commit()
                logger.info("Year %d processed successfully.", year)
                time.sleep(1.0)  # Polite delay
            except Exception as exc:
                logger.error("Error processing year %d: %s", year, exc)
                session.rollback()

    invalidate_public_caches()
    logger.info("Ingestion completed: %d added, %d updated.", total_added, total_updated)
    return total_added + total_updated


if __name__ == "__main__":
    count = ingest_top_anime_2020_to_2026(per_year=6)
    print(f"\n🎉 Successfully ingested and published {count} anime items for 2020-2026!")
