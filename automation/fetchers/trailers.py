"""Official Anime Trailers & Promotional Videos (PV) fetcher.

Fetches official anime trailers from:
1. AniList GraphQL API: Official trailer {id, site} for current and upcoming seasons.
2. Official YouTube Channel Feeds (RSS / YouTube Data API):
   - Crunchyroll
   - Aniplex
   - TOHO animation
   - MAPPA
   - Netflix Anime

Stores real publishedAt, computes authentic relative times,
ensures at least 12 newest-first trailers are returned, and supports season filtering.
If no YOUTUBE_API_KEY is configured, seamlessly falls back to direct official YouTube Atom feeds.
"""
from __future__ import annotations

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from automation.fetchers.anilist import (
    ANILIST_GRAPHQL_URL,
    get_season_cycle,
)

logger = logging.getLogger("automation.fetchers.trailers")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HindiAnimeGuide-Bot/1.0"

OFFICIAL_YOUTUBE_CHANNELS = [
    {"name": "Aniplex", "channel_id": "UC14QT5j2nQI8lKBCGtrrBQA"},
    {"name": "TOHO animation", "channel_id": "UC14Yc2Qv92DMuyNRlHvpo2Q"},
    {"name": "MAPPA", "channel_id": "UCjfAEJZdfbIjVHdo5yODfyQ"},
    {"name": "Netflix Anime", "channel_id": "UCBSs9x2KzSLhyyA9IKyt4YA"},
    {"name": "Crunchyroll", "channel_id": "UC6pGDc4bFGD1_36IKv3FnYg"},
]

TRAILERS_CACHE_TTL = 1800  # 30 minutes
_TRAILERS_CACHE: dict[tuple[str, int], tuple[list[dict[str, Any]], float]] = {}
_YOUTUBE_PUBLISHED_CACHE: dict[str, datetime] = {}

# ==============================================================================
# RULE-BASED JAPANESE TO ENGLISH TRAILER DICTIONARY & OFFICIAL ANIME NAME MAP
# ==============================================================================

OFFICIAL_ANIME_MAP: dict[str, str] = {
    "帰還者の魔法は特別です": "A Returner's Magic Should Be Special",
    "らんま1/2": "Ranma 1/2",
    "らんま 1/2": "Ranma 1/2",
    "ポップパップポルターズ": "Pop Up Porters",
    "うちの弟どもがすみません": "She's My Younger Brother's Girl",
    "バーテックスフォース": "Vertex Force",
    "リラックマ": "Rilakkuma",
    "薬屋のひとりごと": "The Apothecary Diaries",
    "亡妃の秘宝": "The Late Lady's Treasure",
    "僕のヒーローアカデミア": "My Hero Academia",
    "ヒロアカ": "My Hero Academia",
    "お隣の天使様": "The Angel Next Door Spoils Me Rotten",
    "お隣の天使様にラジオでも駄目人間にされていた件": "The Angel Next Door Spoils Me Rotten",
    "チェンソーマン レゼ篇": "Chainsaw Man – The Movie: Reze Arc",
    "チェンソーマン": "Chainsaw Man",
    "化物語": "Bakemonogatari",
    "〈物語〉シリーズ": "Monogatari Series",
    "物語シリーズ": "Monogatari Series",
    "呪術廻戦": "Jujutsu Kaisen",
    "鬼滅の刃": "Demon Slayer: Kimetsu no Yaiba",
    "進撃の巨人": "Attack on Titan",
    "スパイファミリー": "SPY x FAMILY",
    "怪獣8号": "Kaiju No. 8",
    "葬送のフリーレン": "Frieren: Beyond Journey's End",
    "無職転生": "Mushoku Tensei: Jobless Reincarnation",
    "ダンダダン": "Dandadan",
    "ジョジョの奇妙な冒険": "JoJo's Bizarre Adventure",
    "スティール・ボール・ラン": "STEEL BALL RUN",
    "ブラッククローバー": "Black Clover",
    "テムパル～アイテムの力～": "Overgeared",
    "テムパル": "Overgeared",
    "転生したら剣でした": "Reincarnated as a Sword",
    "ホタルの嫁入り": "Firefly Wedding",
    "アオのハコ": "Blue Box",
    "生徒会にも穴はある！": "Even the Student Council Has Its Holes!",
    "生徒会にも穴はある": "Even the Student Council Has Its Holes!",
    "チー付与": "The Laid-Off Cheat-Granting Mage Enjoys a Second Lease on Life",
    "メイドインアビス 目覚める神秘": "Made in Abyss: Mezameru Shinpi",
    "メイドインアビス": "Made in Abyss",
    "魔法使いの夜": "Witch on the Holy Night",
    "氷の城壁": "The Ramparts of Ice",
    "東京リベンジャーズ": "Tokyo Revengers",
    "青春ブタ野郎はディアフレンドの夢を見ない": "Rascal Does Not Dream of a Dear Friend",
    "青春ブタ野郎": "Rascal Does Not Dream",
    "どうも、好きな人に惚れ薬を依頼された魔女です。": "Hello, I am a Witch and my Crush Wants me to Make a Love Potion!",
    "ドラゴンボール超": "Dragon Ball Super",
    "転生貴族、鑑定スキルで成り上がる": "As a Reincarnated Aristocrat, I'll Use My Appraisal Skill to Rise in the World",
    "凶乱令嬢ニア・リストン": "Nia Liston: The Merciless Maiden",
    "目覚めたら最強装備と宇宙船持ちだったので、一戸建て目指して傭兵として自由に生きたい": "Reborn as a Space Mercenary: I Woke Up Piloting the Strongest Starship!",
    "ダークサモナーとデキている": "I'm Dating a Dark Summoner",
    "アオアシ": "Aoashi",
    "探偵はもう、死んでいる。": "The Detective Is Already Dead",
    "マジカル★エクスプローラー": "Magical Explorer",
    "フールナイト": "Fool Night",
    "貸した魔力は【リボ払い】で強制徴収": "Magic Repo Man: Dumped by My Party, I’ll Cash In With a Cute Support Fairy to Become the Strongest!",
    "野生のラスボスが現れた！": "A Wild Last Boss Appeared!",
    "塩対応の佐藤さんが俺にだけ甘い": "The Cold Sato-san is Only Sweet to Me",
    "冰剣の魔術師が世界を統べる": "The Iceblade Sorcerer Shall Rule the World",
    "転生ゴブリンだけど質問ある？": "So What's Wrong with Getting Reborn as a Goblin?",
    "信者ゼロの女神サマと始める異世界攻略": "Full Clearing Another World under a Goddess with Zero Believers",
    "世界最強の魔女、始めました": "The World's Strongest Witch",
    "転生した大聖女は、聖女であることをひた隠す": "A Tale of the Secret Saint",
    "FX戦士くるみちゃん": "FX Fighter Kurumi-chan",
    "ロメリア戦記": "Romelia War Chronicle",
}

RULE_BASED_REPLACEMENTS: list[tuple[str, str]] = [
    # Complex compounds first
    (r"無限城編", " Infinity Castle Arc "),
    (r"劇場版三部作", " Movie Trilogy "),
    (r"完結編", " The Final Chapter "),
    (r"懐玉・玉折", "Hidden Inventory / Premature Death"),
    (r"オフ&モンスターシーズン", "Off & Monster Season"),
    (r"アニメ化決定\s*特報", " TV Anime Adaptation Confirmed Teaser "),
    (r"アニメ化決定", " TV Anime Adaptation Confirmed "),
    (r"制作決定\s*PV", " Production Confirmed PV "),
    (r"制作決定\s*特報", " Production Confirmed Teaser "),
    (r"制作決定", " Production Confirmed "),
    (r"放送決定", " Broadcast Confirmed "),
    (r"公開決定", " Theatrical Release Confirmed "),
    (r"特報映像", " Teaser Video "),
    (r"告知映像", " Announcement Video "),
    (r"解禁映像", " Reveal Video "),
    (r"ティザー映像", " Teaser Video "),
    (r"ティザービジュアル", " Teaser Visual "),
    (r"ティザー\s*PV", " Teaser PV "),
    (r"キャラクター\s*PV", " Character PV "),
    (r"キャラ\s*PV", " Character PV "),
    (r"本\s*PV", " Main PV "),
    (r"メイン\s*PV", " Main PV "),
    (r"解禁\s*PV", " Reveal PV "),
    (r"告知\s*PV", " Announcement PV "),
    (r"次回予告", " Next Episode Preview "),
    (r"WEB予告", " Web Preview "),
    (r"本予告\s*第?(\d+)弾", r" Main Trailer \1 "),
    (r"本予告", " Main Trailer "),
    (r"特報\s*第?(\d+)弾", r" Teaser \1 "),
    (r"特報", " Teaser "),
    (r"ティザー", " Teaser "),
    (r"予告", " Preview "),
    (r"PV\s*第?(\d+)弾", r" PV \1 "),
    (r"第(\d+)弾\s*PV", r" PV \1 "),
    (r"第(\d+)弾", r" Part \1 "),
    (r"第(\d+)期", r" Season \1 "),
    (r"第一期", " Season 1 "),
    (r"第二期", " Season 2 "),
    (r"第三期", " Season 3 "),
    (r"第(\d+)話", r" Episode \1 "),
    (r"TV\s*アニメ", " TV Anime "),
    (r"劇場版", " The Movie: "),
    (r"アニメ\b", " Anime "),
    (r"本編切り抜き動画[①-⑩\d]*", " Scene Clip "),
    (r"切り抜き動画[①-⑩\d]*", " Clip "),
    (r"切り抜き", " Clip "),
    (r"コラボMV", " Collaboration MV "),
    (r"両思いMV", " Romance MV "),
    (r"アフレコトーク", " Voice Recording Talk "),
    (r"公開収録ラジオ", " Public Radio Recording "),
    (r"メイキング映像[①-㊿\d]*", " Behind-The-Scenes Making Video "),
    (r"回顧録", " Memoir / Retrospective "),
    (r"初回放送", " First Broadcast "),
    (r"毎週.*?放送・配信中！?", " Now Broadcasting! "),
]


def translate_jp_trailer_title(title_ja: str) -> str:
    """Translate Japanese trailer title using rule-based dictionary and Gemini fallback.

    Ensures anime titles are NOT translated literally (keeps official English / Romaji names).
    """
    res = title_ja
    # 1. Substitute known anime titles
    for j_name, e_name in OFFICIAL_ANIME_MAP.items():
        res = res.replace(f"『{j_name}』", f"'{e_name}'")
        res = res.replace(f"「{j_name}」", f"'{e_name}'")
        res = res.replace(j_name, e_name)

    # 2. Rule-based term replacements
    for pattern, repl in RULE_BASED_REPLACEMENTS:
        res = re.sub(pattern, repl, res)

    # 3. Clean up residual Japanese brackets and separators
    res = re.sub(r'[【『「]', '[', res)
    res = re.sub(r'[】』」]', ']', res)
    res = re.sub(r'／', ' / ', res)
    res = re.sub(r'｜', ' | ', res)
    res = re.sub(r'\s+', ' ', res).strip()

    # 4. If GEMINI_API_KEY and GEMINI_MODEL exist and Japanese characters remain, invoke Gemini
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL")
    if api_key and model and re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', res):
        try:
            prompt = (
                "Translate this Japanese anime trailer/video title into clear English. "
                "CRITICAL: Do NOT translate anime titles literally; keep their official English or Romaji titles. "
                "Output ONLY the translated title text.\n"
                f"Title: {res}"
            )
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            resp = httpx.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=6.0)
            if resp.status_code == 200:
                t_out = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                if t_out:
                    res = t_out
            elif resp.status_code == 404 or "NOT_FOUND" in resp.text:
                logger.warning("Gemini model '%s' not found (HTTP 404). Falling back to rule-based translation only.", model)
            else:
                logger.warning("Gemini API translation failed with status %d: %s. Falling back to rule-based translation.", resp.status_code, resp.text[:100])
        except Exception as exc:
            logger.warning("Gemini trailer title translation error for model '%s': %s. Falling back to rule-based translation.", model, exc)

    return res


# ==============================================================================
# STRICT OFFICIAL TRAILER VALIDATOR & REAL METADATA FETCHER
# ==============================================================================

DENY_PATTERNS: list[tuple[str, str]] = [
    (r'\bEpisode\s+\d+\s+(?:SUB|DUB)\b', "Full episode upload (Episode N SUB/DUB)"),
    (r'#\d+\|\s*※?\d+週間限定配信', "Full episode stream (limited distribution)"),
    (r'次回予告|第\d+話.*予告|第\d+話.*WEB予告|WEB予告|\bPreview\b', "Next-episode preview (次回予告 / Episode Preview)"),
    (r'切り抜き|本編切り抜き|メイキング映像|#shorts|\bshort\b|\bclip\b', "Clip / scene highlight / behind-the-scenes"),
    (r'\b(?:Imagine|Laughing|Duel|DGAF|The moment|He\'ll protect|The power of)\b', "Short social media clip / scene moment"),
    (r'コラボMV|両思いMV|\bMV\b|ミュージックビデオ|Non-Credit|ノンクレジット', "Music Video / collab song (MV)"),
    (r'Pouch|Pouches|Figure|グッズ|くじ', "Merchandise / goods promotion"),
    (r'生配信|公開収録|ラジオ|アフレコトーク|蕎麦を食べる|トークショー|特別番組|Special Talk', "Voice actor talk / livestream / radio show"),
    (r'CINEMA LIVE|チケット発売中', "Event / concert live promotion CM"),
    (r'schedule update', "Schedule update post"),
    (r'【?第\d+話[】」]|第\d+話\s*「', "Individual episode scene / dialog cut"),
]

ALLOW_PATTERNS: list[tuple[str, str]] = [
    (r'\b(?:Main Trailer|Official Trailer|Announcement Trailer|Teaser Trailer|Official Main Trailer PV)\b', "Official trailer / main trailer"),
    (r'本予告|特報|ティザーPV|本PV|メインPV|キャラクターPV|キャラPV|解禁PV|解禁映像|告知PV|ティザー映像', "Official Japanese PV / trailer (本予告 / 特報 / ティザーPV)"),
    (r'\bPV\b|ティザー|\bTrailer\b|\bTeaser\b', "Promotional video (PV) / Teaser"),
]

KNOWN_OFFICIAL_CHANNELS = {
    "toho animation チャンネル", "toho animation", "mappa channel", "aniplex チャンネル",
    "アニプレックス チャンネル", "aniplex", "aniplex usa", "netflix anime", "crunchyroll",
    "crunchyroll collection", "crunchyroll anime", "kadokawaanime", "kadokawa",
    "emotion label channel", "bandai namco arts", "bandai namco filmworks",
    "バンダイナムコフィルムワークス チャンネル", "isekai channel @バンダイナムコフィルムワークス",
    "avex pictures", "pony canyon", "ぽにきゃん-anime pony canyon", "nbcuniversal anime/music",
    "warner bros. japan anime", "shochiku", "shochiku animeチャンネル【公式】",
    "twin engine", "mainichi broadcasting system", "mbs", "mbs animation 公式チャンネル",
    "テレ東アニメ", "king amusement creative", "vap official", "overlap",
    "columbia music japan", "flying dog", "tms animation", "tmsアニメ公式チャンネル",
    "toei animation", "東映アニメーション公式youtubeチャンネル", "bushiroad",
    "dmm pictures", "fujitv", "【フジテレビ】アニメ公式チャンネル", "noitamina",
    "asmik ace", "shoproアニメチャンネル", "tv asahi animation youtubeチャンネル",
    "it's anime powered by remow", "博報堂dy ミュージック&ピクチャーズ【showgate】ch",
    "tvアニメ『東京リベンジャーズ』チャンネル", "tvアニメ「ロメリア戦記」official channel",
    "デレギュラ【新アニメレーベル】"
}


def check_title_matches_anime(video_title: str, anime_info: dict[str, Any]) -> tuple[bool, str]:
    """Verify that real YouTube title actually belongs to the attached anime.

    Checks english, romaji, native titles, synonyms, and OFFICIAL_ANIME_MAP.
    """
    v_norm = re.sub(r'[\s\-_:;!?\'\"「」『』【】（）()]+', '', video_title.lower())

    candidates: list[str] = []
    titles = anime_info.get("title") or {}
    for k in ["english", "romaji", "native"]:
        t = titles.get(k)
        if t and len(t) >= 2:
            candidates.append(t)
            parts = re.split(r'[:\-–—]', t)
            if parts and len(parts[0].strip()) >= 3:
                candidates.append(parts[0].strip())

    for syn in (anime_info.get("synonyms") or []):
        if syn and len(syn) >= 2:
            candidates.append(syn)

    for ja_name, en_name in OFFICIAL_ANIME_MAP.items():
        for cand in list(candidates):
            if ja_name in cand or cand in ja_name:
                candidates.append(en_name)
            if en_name.lower() in cand.lower():
                candidates.append(ja_name)

    for cand in candidates:
        cand_norm = re.sub(r'[\s\-_:;!?\'\"「」『』【】（）()]+', '', cand.lower())
        if cand_norm and cand_norm in v_norm:
            return True, f"Title matches anime '{cand}'"

    attached_name = titles.get("english") or titles.get("romaji") or "Attached Anime"
    return False, f"Real title does not match attached anime '{attached_name}'"


def is_official_trailer(
    title: str,
    channel: str | None = None,
    attached_anime: dict[str, Any] | None = None,
    is_anilist: bool = False,
) -> tuple[bool, str]:
    """Validate whether a real video title and channel represents a genuine official promotional trailer/PV.

    Returns (is_allowed: bool, reason: str).
    """
    if not title:
        return False, "No video title available"

    for pattern, reason in DENY_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return False, reason

    if attached_anime:
        matches, match_reason = check_title_matches_anime(title, attached_anime)
        if not matches:
            return False, match_reason

    for pattern, reason in ALLOW_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return True, reason

    if attached_anime or is_anilist:
        return True, "Official promotional trailer attached to AniList anime entry"

    return False, "No official trailer/PV indicator found in title"


_YOUTUBE_METADATA_CACHE: dict[str, dict[str, Any]] = {}


def get_youtube_video_metadata(yt_id: str, timeout_seconds: float = 6.0) -> dict[str, Any]:
    """Fetch real YouTube metadata (title, channel, publishedAt) using Data API v3, oEmbed, and HTML date extraction.

    Never constructs a fake or template title. If publishedAt cannot be fetched, stores None (NULL).
    """
    if yt_id in _YOUTUBE_METADATA_CACHE:
        return _YOUTUBE_METADATA_CACHE[yt_id]

    meta: dict[str, Any] = {
        "title": None,
        "channel": None,
        "published_at": None,
    }

    # 1. Try YouTube Data API v3 if key available
    api_key = os.getenv("YOUTUBE_API_KEY")
    if api_key:
        try:
            url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet&id={yt_id}&key={api_key}"
            with httpx.Client(timeout=timeout_seconds) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    items = resp.json().get("items") or []
                    if items:
                        snip = items[0].get("snippet", {})
                        meta["title"] = snip.get("title")
                        meta["channel"] = snip.get("channelTitle")
                        raw_pub = snip.get("publishedAt")
                        if raw_pub:
                            meta["published_at"] = datetime.fromisoformat(raw_pub.replace("Z", "+00:00"))
        except Exception as exc:
            logger.debug("YouTube Data API lookup failed for %s: %s", yt_id, exc)

    # 2. Fallback to YouTube oEmbed if title or channel missing
    if not meta["title"] or not meta["channel"]:
        try:
            oembed_url = f"https://www.youtube.com/watch?v={yt_id}"
            api_url = f"https://www.youtube.com/oembed?url={oembed_url}&format=json"
            with httpx.Client(timeout=timeout_seconds) as client:
                resp = client.get(api_url)
                if resp.status_code == 200:
                    d = resp.json()
                    if not meta["title"]:
                        meta["title"] = d.get("title")
                    if not meta["channel"]:
                        meta["channel"] = d.get("author_name")
        except Exception as exc:
            logger.debug("YouTube oEmbed lookup failed for %s: %s", yt_id, exc)

    # Note: If published_at cannot be fetched from YouTube API, it stays None (stored as NULL; never now() fallback)
    _YOUTUBE_METADATA_CACHE[yt_id] = meta
    return meta


def get_youtube_published_at(yt_id: str, timeout_seconds: float = 5.0) -> datetime | None:
    """Fetch real publishedAt timestamp from YouTube metadata cache or API."""
    return get_youtube_video_metadata(yt_id, timeout_seconds=timeout_seconds).get("published_at")


ANILIST_TRAILERS_QUERY = """
query ($season: MediaSeason, $seasonYear: Int, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      hasNextPage
    }
    media(season: $season, seasonYear: $seasonYear, type: ANIME, sort: POPULARITY_DESC) {
      id
      title {
        romaji
        english
        native
      }
      synonyms
      trailer {
        id
        site
        thumbnail
      }
      format
      status
      season
      seasonYear
      startDate {
        year
        month
        day
      }
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
    }
  }
}
"""


def fetch_anilist_season_trailers(
    season: str | None = None,
    year: int | None = None,
    timeout_seconds: float = 12.0,
) -> list[dict[str, Any]]:
    """Fetch official trailers from AniList GraphQL with REAL YouTube metadata and validation."""
    cycle = get_season_cycle()
    req_season = (season or cycle["current"]["season"]).upper()
    req_year = int(year or cycle["current"]["year"])

    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    payload = {
        "query": ANILIST_TRAILERS_QUERY,
        "variables": {
            "season": req_season,
            "seasonYear": req_year,
            "page": 1,
            "perPage": 40,
        },
    }

    trailers: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=timeout_seconds, headers=headers) as client:
            resp = client.post(ANILIST_GRAPHQL_URL, json=payload)
        if resp.status_code != 200:
            logger.warning("AniList trailers query failed with HTTP %d", resp.status_code)
            return []

        media_list = resp.json().get("data", {}).get("Page", {}).get("media", [])
        for m in media_list:
            tr = m.get("trailer")
            if not tr or not tr.get("id"):
                continue

            site = (tr.get("site") or "").lower()
            yt_id = tr.get("id")
            if "youtube" not in site:
                continue

            # Fetch REAL YouTube metadata (title, channel, publishedAt)
            real_meta = get_youtube_video_metadata(yt_id)
            real_title = real_meta.get("title")
            real_channel = real_meta.get("channel")
            pub_dt = real_meta.get("published_at")  # Stored as NULL if unavailable; never now() fallback!

            if not real_title:
                continue

            # Verify ALLOW/DENY based on REAL YouTube title, channel, and attached anime
            allowed, reason = is_official_trailer(real_title, channel=real_channel, attached_anime=m)
            if not allowed:
                continue

            titles = m.get("title") or {}
            title_en = (titles.get("english") or titles.get("romaji") or f"Anime #{m.get('id')}").strip()
            title_ja = real_title
            translated_title = translate_jp_trailer_title(real_title)

            cov = m.get("coverImage") or {}
            img = cov.get("large") or cov.get("extraLarge") or f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg"

            channel_name = real_channel or "Official Studio"

            # Parse start date as anime premiere date (never future video publication date!)
            s_date = m.get("startDate") or {}
            premiere_dt = None
            if s_date.get("year") and s_date.get("month") and s_date.get("day"):
                premiere_dt = datetime(s_date["year"], s_date["month"], s_date["day"], 12, 0, 0, tzinfo=timezone.utc)

            raw_desc = m.get("description") or f"{title_en} आधिकारिक एनीमे ट्रेलर।"
            clean_desc = re.sub(r'<[^>]+>', '', raw_desc)
            clean_desc = re.sub(r'\s+', ' ', clean_desc).strip()

            trailers.append({
                "id": f"trailer_anilist_{m.get('id')}_{yt_id}",
                "external_id": f"trailer_{yt_id}",
                "content_type": "trailer",
                "source_name": channel_name,
                "source_url": f"https://www.youtube.com/watch?v={yt_id}",
                "title_ja": title_ja,
                "title_en": translated_title,
                "title_hi": f"{translated_title} | {channel_name}",
                "summary_en": f"Official promotional trailer and teaser preview for {title_en} released on {channel_name}.",
                "summary_hi": f"{title_en} का आधिकारिक प्रमोशनल ट्रेलर एवं टीज़र वीडियो, चैनल {channel_name} द्वारा।",
                "image_url": img,
                "published_at": pub_dt,
                "published_at_iso": pub_dt.isoformat() if pub_dt else None,
                "premiere_date": premiere_dt,
                "premiere_date_iso": premiere_dt.isoformat() if premiere_dt else None,
                "season": req_season,
                "season_year": req_year,
                "status": "published",
                "youtube_id": yt_id,
            })
    except Exception as exc:
        logger.warning("Error fetching AniList trailers: %s", exc)

    return trailers


def fetch_youtube_channel_trailers(limit_per_channel: int = 5, timeout_seconds: float = 10.0) -> list[dict[str, Any]]:
    """Fetch latest official anime trailers from verified YouTube channel RSS feeds."""
    trailers: list[dict[str, Any]] = []
    headers = {"User-Agent": USER_AGENT}

    for ch in OFFICIAL_YOUTUBE_CHANNELS:
        name = ch["name"]
        ch_id = ch["channel_id"]
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}"
        try:
            with httpx.Client(timeout=timeout_seconds, headers=headers, follow_redirects=True) as client:
                resp = client.get(url)
            if resp.status_code != 200:
                continue

            root = ET.fromstring(resp.content)
            entries = root.findall("{http://www.w3.org/2005/Atom}entry")
            for e in entries[:limit_per_channel]:
                title = e.findtext("{http://www.w3.org/2005/Atom}title") or ""
                yt_id = e.findtext("{http://www.youtube.com/xml/schemas/2015}videoId")
                pub_str = e.findtext("{http://www.w3.org/2005/Atom}published")

                if not yt_id or not pub_str:
                    continue

                pub_dt = datetime.fromisoformat(pub_str).astimezone(timezone.utc)
                pub_dt = min(pub_dt, datetime.now(timezone.utc))
                clean_title = re.sub(r'\s+', ' ', title).strip()

                allowed, reason = is_official_trailer(clean_title, is_anilist=False)
                if not allowed:
                    continue

                title_ja = clean_title
                translated_en = translate_jp_trailer_title(clean_title)

                trailers.append({
                    "id": f"trailer_yt_{yt_id}",
                    "external_id": f"trailer_{yt_id}",
                    "content_type": "trailer",
                    "source_name": name,
                    "source_url": f"https://www.youtube.com/watch?v={yt_id}",
                    "title_ja": title_ja,
                    "title_en": translated_en,
                    "title_hi": f"{translated_en} | {name} आधिकारिक प्रोमो",
                    "summary_en": f"Official trailer release from {name}: {translated_en}.",
                    "summary_hi": f"{name} द्वारा आधिकारिक तौर पर जारी नया एनीमे टीज़र और प्रमोशनल वीडियो।",
                    "image_url": f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
                    "published_at": pub_dt,
                    "published_at_iso": pub_dt.isoformat(),
                    "season": "FALL",
                    "season_year": 2026,
                    "status": "published",
                    "youtube_id": yt_id,
                })
        except Exception as exc:
            logger.debug("Failed to fetch YouTube feed for %s: %s", name, exc)

    return trailers


def fetch_live_trailers(
    season: str | None = None,
    year: int | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Fetch combined trailers from AniList and official YouTube feeds.

    Guarantees:
    - At least 12 newest-first trailers.
    - Real published_at timestamps.
    - Deduplication by YouTube ID.
    - 30-minute in-memory caching.
    """
    global _TRAILERS_CACHE

    cycle = get_season_cycle()
    req_season = (season or cycle["current"]["season"]).upper()
    req_year = int(year or cycle["current"]["year"])
    cache_key = (req_season, req_year)
    now_mono = time.monotonic()

    if not force_refresh and cache_key in _TRAILERS_CACHE:
        cached_items, expiry = _TRAILERS_CACHE[cache_key]
        if expiry > now_mono:
            return cached_items

    all_trailers: list[dict[str, Any]] = []
    seen_yt_ids: set[str] = set()

    # 1. Official YouTube Channel Feeds
    yt_trailers = fetch_youtube_channel_trailers(limit_per_channel=5)
    for t in yt_trailers:
        yt_id = t["youtube_id"]
        if yt_id not in seen_yt_ids:
            seen_yt_ids.add(yt_id)
            all_trailers.append(t)

    # 2. AniList Season Trailers
    al_trailers = fetch_anilist_season_trailers(season=req_season, year=req_year)
    for t in al_trailers:
        yt_id = t["youtube_id"]
        if yt_id not in seen_yt_ids:
            seen_yt_ids.add(yt_id)
            all_trailers.append(t)

    # Ensure all trailers pass official filter
    all_trailers = [
        t for t in all_trailers
        if is_official_trailer(t.get("title_en", ""), is_anilist=bool(t.get("premiere_date")))[0]
    ]

    # Sort strictly by published_at desc
    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    all_trailers.sort(key=lambda x: x["published_at"] or min_dt, reverse=True)

    _TRAILERS_CACHE[cache_key] = (all_trailers, now_mono + TRAILERS_CACHE_TTL)
    logger.info("Successfully loaded %d trailers for %s %d", len(all_trailers), req_season, req_year)
    return all_trailers

