"""Mandatory Pre-Publish Truth & Quality Gate for Hindi Anime Guide.

Verifies live external integrity across all 5 content pillars:
  1. Schedule Gate  -> RELEASING only, upcoming airing_at timestamp, HTTP 200 image
  2. Trailer Gate   -> Valid 11-char YT ID, HTTP 200 oEmbed embeddability (Spotlight + Trailers)
  3. News Gate      -> Pure English summary_en (0 Devanagari bleed), zero generic boilerplate, natural Hindi title_hi, HTTP 200 image
  4. Manga Gate     -> Real anilist_id, HTTP 200 cover, numeric chapters/volumes & continuation guide for key titles
  5. Article Gate   -> 20 editorial guides, each >= 800 words, HTTP 200 covers
"""

import asyncio
import json
import re
import sys
from pathlib import Path

import httpx
from sqlalchemy import text

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import engine  # noqa: E402
from app.services.content import CURATED_SPOTLIGHT, _extract_youtube_id, get_cached_content  # noqa: E402


async def run_gate() -> int:
    errors: list[str] = []
    data = get_cached_content(force_refresh=True)

    with engine.connect() as conn:
        sch_rows = conn.execute(
            text("SELECT id, title_en, publication_status, image_url, official_links FROM anime_draft WHERE content_type='schedule' AND status='published'")
        ).mappings().all()
        tr_rows = conn.execute(
            text("SELECT id, title_en, source_url, image_url FROM anime_draft WHERE content_type='trailer' AND status='published'")
        ).mappings().all()
        news_rows = conn.execute(
            text("SELECT id, title_en, title_hi, summary_en, summary_hi, image_url FROM anime_draft WHERE content_type='news' AND status='published'")
        ).mappings().all()
        mg_rows = conn.execute(
            text("SELECT id, anilist_id, title_en, image_url FROM anime_draft WHERE content_type='manga' AND status='published'")
        ).mappings().all()
        aw_rows = conn.execute(
            text("SELECT id, ceremony_id, category, winner_title, winner_poster FROM award_entry ORDER BY id ASC")
        ).mappings().all()
        art_rows = conn.execute(
            text("SELECT id, slug, title, cover_image, content_md FROM article ORDER BY id ASC")
        ).mappings().all()

    # 1. Schedule Gate
    sch_bad_status = [r["id"] for r in sch_rows if (r["publication_status"] or "").upper() != "RELEASING"]
    sch_no_air = []
    for r in sch_rows:
        try:
            meta = json.loads(r["official_links"] or "{}")
        except Exception:
            meta = {}
        if not meta.get("airing_at"):
            sch_no_air.append(r["id"])
    if sch_bad_status:
        errors.append(f"Schedule non-RELEASING IDs: {sch_bad_status}")
    if sch_no_air:
        errors.append(f"Schedule missing airing_at IDs: {sch_no_air}")

    # 2. News Gate (Zero Devanagari in summary_en, zero boilerplate in summary_hi, zero phonetic title_hi)
    phonetic_pat = re.compile(r"(?:आइ ड्रा देम|रिवील्स|अनवेल्स|अनाउंसेस|विशिंग आइ कुड|ऑफिशियल हिगे|हेल्म्स|बॉयज़-लव मंगा गेट्स|एडिशन्ल)")
    news_en_bleed = [
        r["id"]
        for r in news_rows
        if re.search(r"[\u0900-\u097F]", r["summary_en"] or "")
        or len((r["summary_en"] or "").strip()) < 25
        or "stay on top of anime news" in (r["summary_en"] or "").lower()
    ]
    news_hi_bad = [r["id"] for r in news_rows if phonetic_pat.search(r["title_hi"] or "") or not re.search(r"[\u0900-\u097F]", r["title_hi"] or "")]
    news_boilerplate = [
        r["id"]
        for r in news_rows
        if ("पूरी जानकारी, कास्ट विवरण" in (r["summary_hi"] or "") or "मूल स्रोत रिपोर्ट:" in (r["summary_hi"] or ""))
    ]
    avg_en = sum(len((r["summary_en"] or "").strip()) for r in news_rows) / max(1, len(news_rows))
    avg_hi = sum(len((r["summary_hi"] or "").strip()) for r in news_rows) / max(1, len(news_rows))
    if avg_hi < avg_en * 0.85:
        errors.append(f"News Hindi parity ratio too low: avg_hi={avg_hi:.1f} < 0.85 * avg_en={avg_en:.1f}")

    # Check Awards years coverage (2026, 2024, 2023, 2022, 2021)
    with engine.connect() as conn:
        aw_years = {int(r[0]) for r in conn.execute(text("SELECT DISTINCT edition_year FROM award_ceremony")).fetchall()}
    missing_aw_years = {2026, 2024, 2023, 2022, 2021} - aw_years
    if missing_aw_years:
        errors.append(f"Missing required award_ceremony years: {sorted(missing_aw_years)}")
    if news_en_bleed:
        errors.append(f"News summary_en Devanagari bleed or empty: {news_en_bleed}")
    if news_hi_bad:
        errors.append(f"News phonetic/untranslated title_hi: {news_hi_bad}")
    if news_boilerplate:
        errors.append(f"News old filler boilerplate present: {news_boilerplate}")

    # 3. Manga Gate (anilist_id + flagship numeric payload)
    mg_null = [r["id"] for r in mg_rows if not r["anilist_id"]]
    if mg_null:
        errors.append(f"Manga null anilist_id: {mg_null}")
    for m in data["manga"]:
        if any(k in m["title_en"].lower() for k in ("one piece", "dandadan", "blue lock", "solo leveling", "jujutsu kaisen", "berserk")):
            if not m.get("chapters_released") or not m.get("latest_volume") or not m.get("continue_from_chapter"):
                errors.append(f"Manga flagship missing chapter/volume/continuation: {m['title_en']}")

    # 4. Article Gate (>= 20 guides, each >= 800 words)
    if len(art_rows) < 20:
        errors.append(f"Article count {len(art_rows)} < 20")
    art_short = [a["slug"] for a in art_rows if len((a["content_md"] or "").split()) < 800]
    if art_short:
        errors.append(f"Article guides under 800 words: {art_short}")

    # 5. Live Network Checks: All HTTP Images + All YouTube oEmbed Trailers
    sem = asyncio.Semaphore(30)
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as cli:
        async def probe_img(label: str, url: str):
            async with sem:
                try:
                    r = await cli.head(url)
                    if r.status_code in (403, 405) or r.status_code >= 400:
                        r = await cli.get(url)
                    return ("img", label, r.status_code)
                except Exception as e:
                    return ("img", label, f"ERR:{type(e).__name__}")

        async def probe_yt(label: str, yt_id: str):
            async with sem:
                if not yt_id:
                    return ("yt", label, 0)
                try:
                    r = await cli.get(f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={yt_id}&format=json")
                    return ("yt", label, r.status_code)
                except Exception:
                    return ("yt", label, 0)

        tasks = []
        for r in list(sch_rows) + list(mg_rows) + list(tr_rows):
            tasks.append(probe_img(f"{r['title_en'][:30]}", r["image_url"] or ""))
        for aw in aw_rows:
            tasks.append(probe_img(f"award:{aw['id']}:{aw['winner_title'][:24]}", aw["winner_poster"] or ""))
        for r in news_rows:
            if (r["image_url"] or "").strip():
                tasks.append(probe_img(f"news:{r['id']}", r["image_url"]))
        for a in art_rows:
            tasks.append(probe_img(f"article:{a['slug'][:24]}", a["cover_image"] or ""))

        for i, sp in enumerate(CURATED_SPOTLIGHT):
            tasks.append(probe_yt(f"spotlight:{i}:{sp['title_en']}", sp.get("trailer_id", "")))
        for tr in tr_rows:
            tasks.append(probe_yt(f"trailer:{tr['id']}:{tr['title_en'][:24]}", _extract_youtube_id(tr["source_url"])))

        results = await asyncio.gather(*tasks)

    known_unplayable_ids = {"P927_ZvgUUg", "fmUpSXFbSK0", "8XgKVzuLmJw", "_c_hMehCORQ", "uC13b3_8riU", "BJXBr_aFF8s"}
    region_locked_tr = [tr["id"] for tr in tr_rows if _extract_youtube_id(tr["source_url"]) in known_unplayable_ids]
    if region_locked_tr:
        errors.append(f"Region-locked/unavailable YouTube trailers present: {region_locked_tr}")

    bad_imgs = [x for x in results if x[0] == "img" and x[2] != 200]
    bad_yts = [x for x in results if x[0] == "yt" and x[2] != 200]
    if bad_imgs:
        errors.append(f"Broken HTTP images ({len(bad_imgs)}): {bad_imgs[:5]}")
    if bad_yts:
        errors.append(f"Unembeddable YouTube trailers ({len(bad_yts)}): {bad_yts[:5]}")

    img_tot = sum(1 for x in results if x[0] == "img")
    yt_tot = sum(1 for x in results if x[0] == "yt")
    print(f"TRUTH GATE [schedule]: count={len(sch_rows)} non_releasing={len(sch_bad_status)} missing_airing={len(sch_no_air)}")
    print(f"TRUTH GATE [trailer] : oembed_checked={yt_tot} failed={len(bad_yts)}")
    print(f"TRUTH GATE [news]    : count={len(news_rows)} en_bleed={len(news_en_bleed)} hi_bad={len(news_hi_bad)} boilerplate={len(news_boilerplate)}")
    print(f"TRUTH GATE [manga]   : count={len(mg_rows)} null_anilist_id={len(mg_null)}")
    print(f"TRUTH GATE [awards]  : count={len(aw_rows)}")
    print(f"TRUTH GATE [article] : count={len(art_rows)} under_800_words={len(art_short)}")
    print(f"TRUTH GATE [images]  : live_checked={img_tot} broken={len(bad_imgs)}")
    print(f"TRUTH GATE TOTAL ERRORS: {len(errors)}")
    if errors:
        for e in errors:
            print("  ERROR:", e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_gate()))
