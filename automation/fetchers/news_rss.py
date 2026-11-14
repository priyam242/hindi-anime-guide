"""Multi-source RSS fetcher for verified anime editorial news.

Fetches real-time anime industry news from top verified sources:
1. Anime News Network (ANN)
2. Anime Corner
3. Crunchyroll News
4. Otaku USA
5. LiveChart

Strictly preserves each item's authentic pubDate as published_at,
preserves source name and canonical permalink, and classifies into editorial categories:
Announcement, Trailer, Release Date, Movie, Interview, Event Recap, Industry.
"""
from __future__ import annotations

import email.utils
import hashlib
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from automation.fetchers.anilist import search_anilist_cover

logger = logging.getLogger("automation.fetchers.news_rss")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 HindiAnimeGuide-Bot/1.0"

NEWS_SOURCES = [
    {
        "name": "Anime News Network",
        "url": "https://www.animenewsnetwork.com/news/rss.xml",
        "prefix": "ann",
    },
    {
        "name": "Anime Corner",
        "url": "https://animecorner.me/feed/",
        "prefix": "ac",
    },
    {
        "name": "Crunchyroll News",
        "url": "https://cr-news-api-service.prd.crunchyrollsvc.com/v1/en-US/rss",
        "prefix": "cr",
    },
    {
        "name": "Otaku USA",
        "url": "https://otakuusamagazine.com/feed/",
        "prefix": "otaku",
    },
    {
        "name": "LiveChart",
        "url": "https://www.livechart.me/feeds/headlines",
        "prefix": "livechart",
    },
]

VALID_CATEGORIES = [
    "Announcement",
    "Trailer",
    "Release Date",
    "Movie",
    "Interview",
    "Event Recap",
    "Industry",
]


def classify_category(title: str, summary: str) -> str:
    """Classify news item into one of the 7 editorial categories based on content."""
    text = f"{title} {summary}".lower()
    if any(k in text for k in ["trailer", "teaser", "promo video", "pv", "key visual", "teaser visual", "character visual"]):
        return "Trailer"
    if any(k in text for k in ["release date", "debuts on", "premieres on", "airs on", "broadcast starts", "schedule", "premiere"]):
        return "Release Date"
    if any(k in text for k in ["movie", "film", "theatrical", "box office", "anime film", "cinema"]):
        return "Movie"
    if any(k in text for k in ["interview", "creator speaks", "author shares", "director discusses", "speaks on", "voice actor"]):
        return "Interview"
    if any(k in text for k in ["event", "convention", "panel", "expo", "recap", "festival", "awards", "stage musical"]):
        return "Event Recap"
    if any(k in text for k in ["industry", "sales", "earnings", "merger", "acquisition", "studio", "streaming license", "rankings"]):
        return "Industry"
    return "Announcement"


def parse_rss_date(date_str: str | None) -> datetime | None:
    """Parse standard RFC 822 / 2822 or ISO date string into UTC datetime."""
    if not date_str:
        return None
    date_str = date_str.strip()
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        if dt:
            return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(date_str)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None


def clean_html(raw_html: str | None) -> str:
    """Clean HTML tags and unescape entities from descriptions."""
    if not raw_html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', raw_html)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_image_from_item(item_elem: ET.Element, raw_description: str, title: str) -> str | None:
    """Extract thumbnail image URL from RSS XML elements or description HTML."""
    # 1. Look for <media:thumbnail url="..."> or <media:content url="...">
    for elem in item_elem.iter():
        if "thumbnail" in elem.tag.lower() or "content" in elem.tag.lower():
            url = elem.attrib.get("url")
            if url and ("jpg" in url.lower() or "png" in url.lower() or "webp" in url.lower()):
                return url

    # 2. Look for <enclosure url="..." type="image/...">
    enclosure = item_elem.find("enclosure")
    if enclosure is not None:
        url = enclosure.attrib.get("url")
        if url:
            return url

    # 3. Look for <img> src in description HTML
    if raw_description:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw_description, re.IGNORECASE)
        if m:
            img_url = m.group(1).strip()
            if img_url.startswith("http"):
                return img_url

    # 4. Search AniList cover by clean title
    cover = search_anilist_cover(title)
    if cover:
        return cover

    return None


def fetch_source_news(source: dict[str, str], limit: int = 15, timeout_seconds: float = 12.0) -> list[dict[str, Any]]:
    """Fetch and parse news items from a single RSS source."""
    name = source["name"]
    url = source["url"]
    prefix = source["prefix"]

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    try:
        with httpx.Client(timeout=timeout_seconds, headers=headers, follow_redirects=True) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            logger.warning("News source '%s' returned HTTP %d", name, resp.status_code)
            return []

        root = ET.fromstring(resp.content)
    except Exception as exc:
        logger.warning("Failed to fetch or parse RSS for '%s': %s", name, exc)
        return []

    channel = root.find("channel")
    items_raw = channel.findall("item") if channel is not None else root.findall("{http://www.w3.org/2005/Atom}entry")

    normalized_items: list[dict[str, Any]] = []

    for item in items_raw[:limit]:
        title = item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title")
        if not title:
            continue
        title = clean_html(title)

        link = item.findtext("link")
        if not link:
            link_elem = item.find("{http://www.w3.org/2005/Atom}link")
            if link_elem is not None:
                link = link_elem.attrib.get("href")
        if not link:
            continue
        link = link.strip()

        # Date parsing
        date_str = (
            item.findtext("pubDate")
            or item.findtext("{http://www.w3.org/2005/Atom}published")
            or item.findtext("{http://www.w3.org/2005/Atom}updated")
            or item.findtext("{http://purl.org/dc/elements/1.1/}date")
        )
        pub_dt = parse_rss_date(date_str)
        # Rule: Items without published_at or older than source date must not be fabricated
        if not pub_dt:
            continue

        raw_desc = (
            item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")
            or item.findtext("description")
            or item.findtext("{http://www.w3.org/2005/Atom}summary")
            or ""
        )
        summary_clean = clean_html(raw_desc)
        # Rule 1: Use ONLY text present in RSS description/content. Never invent boilerplate.
        # If none exists, summary_clean remains empty string.

        # Category
        category = classify_category(title, summary_clean or title)

        # Thumbnail Image
        image_url = extract_image_from_item(item, raw_desc, title)

        # Guid / Article permalink detection (prefer LiveChart article URL when source is a tweet)
        guid_raw = item.findtext("guid") or item.findtext("{http://www.w3.org/2005/Atom}id") or ""
        is_tweet = "x.com" in link.lower() or "twitter.com" in link.lower()
        livechart_article_url = guid_raw.strip() if "livechart.me/headlines/" in guid_raw else None

        if is_tweet:
            final_source_name = "Tweet"
            final_source_url = livechart_article_url or link
        else:
            final_source_name = name
            final_source_url = link

        # Unique external_id based on link hash
        url_hash = hashlib.sha256(link.encode("utf-8")).hexdigest()[:12]
        external_id = f"news_{prefix}_{url_hash}"

        normalized_items.append({
            "external_id": external_id,
            "content_type": "news",
            "news_category": category,
            "source_name": final_source_name,
            "source_url": final_source_url,
            "tweet_url": link if is_tweet else None,
            "published_at": pub_dt,
            "published_at_iso": pub_dt.isoformat(),
            "title_en": title,
            "summary_en": summary_clean,
            "raw_summary": summary_clean,
            "image_url": image_url,
            "status": "published",
            "is_featured": False,
            "is_editor_pick": False,
        })

    return normalized_items


def fetch_all_news_rss(limit_per_source: int = 10) -> list[dict[str, Any]]:
    """Fetch news from all 5 verified RSS sources, deduplicate, and sort by published_at desc."""
    all_items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for source in NEWS_SOURCES:
        items = fetch_source_news(source, limit=limit_per_source)
        for item in items:
            norm_url = item["source_url"].lower().rstrip("/")
            if norm_url not in seen_urls:
                seen_urls.add(norm_url)
                all_items.append(item)

    # Sort newest first by published_at
    all_items.sort(key=lambda x: x["published_at"], reverse=True)
    logger.info("Fetched total of %d unique news items across all 5 RSS sources.", len(all_items))
    return all_items

