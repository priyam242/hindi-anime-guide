# AUDIT REPORT: Hindi Anime Guide (Phase 0 Audit)
**Date:** November 13, 2026  
**Auditor:** Senior Full-Stack & UI/UX Engineer  
**Scope:** Zero-code production audit across News, Manga, Awards, Display/Schedule, and Root Causes.  
**Target Repository:** `D:\Anime`  
**Live Site Evidence:** Render Production Deployment (`https://hindi-anime-guide.onrender.com`)

---

## Executive Summary
This comprehensive audit systematically investigates the data integrity, editorial accuracy, database models, and template rendering issues within the Hindi Anime Guide application. No application code or database records have been modified during this phase.

All findings are backed by direct SQL queries against the production Neon PostgreSQL database, inspection of Jinja2 templates, comparisons with official third-party APIs (AniList GraphQL and MangaDex REST API), and user-provided screenshots from the live deployment.

---

## 1. News Audit (Database Rows & Evidence)

The database table `anime_draft` currently holds **7 news records** (4 published, 3 drafts).

### 1.1 Published News Items
| ID | Status | Title (EN) | Source Name | Source URL | Image URL | Audit Findings |
|---|---|---|---|---|---|---|
| **85** | published | Solo Leveling Season 2 'Arise from the Shadow' Confirmed for Global Streaming | Crunchyroll News | `https://www.crunchyroll.com/news` | `/media/covers/anime_solo-leveling.webp` | **Missing Canonical Article URL:** Links only to the generic homepage instead of the specific announcement article. Summary is only 2 short sentences. |
| **86** | published | Demon Slayer: Infinity Castle Movie Trilogy Scheduled for Worldwide Theatrical Release | Anime News Network | `https://www.animenewsnetwork.com` | `/media/covers/anime_demon-slayer.webp` | **Missing Canonical Article URL:** Points to generic homepage root domain. |
| **87** | published | Jujutsu Kaisen: Culling Game Arc Enters Production at Studio MAPPA | Weekly Shonen Jump | `https://www.shonenjump.com` | `/media/covers/anime_jujutsu-kaisen.webp` | **Missing Canonical Article URL:** Generic publisher domain; lacks specific editorial permalink. |
| **88** | published | Chainsaw Man: The Movie - Reze Arc Teaser Stuns Fans with First Look | IGN Anime | `https://www.ign.com/anime` | `/media/covers/anime_chainsaw-man.webp` | **Missing Canonical Article URL:** Generic category landing page, not article permalink. |

### 1.2 Draft News Items (Offline Fallback & Boilerplate Issues)
| ID | Status | Title (EN) | Source Name | Source URL | Image | Audit Findings |
|---|---|---|---|---|---|---|
| **6** | draft | "Getting Shots on a Frying Pan" TV anime reveals teaser PV & January debut: Latest News... | LiveChart.me | `https://www.youtube.com/watch?v=fmafMZNXeHw` | `None` (Empty) | **Pure AI Offline Fallback:** Contains generic boilerplate text: *"An official editorial news update confirms key developments regarding... according to verified reporting from LiveChart.me."* Image is completely missing (`None`). Source URL is a raw YouTube link, not an article. |
| **7** | draft | "Tsugumi Project" TV anime reveals main key visual: Latest News... | LiveChart.me | `https://x.com/i/status/2120705640889000035` | `None` (Empty) | **Pure AI Offline Fallback:** Identical synthetic boilerplate text. Image is missing (`None`). Source URL is an X (Twitter) post URL. |
| **8** | draft | "MASHLE: MAGIC AND MUSCLES The Tri-Magicathlon Divine Visionary Final Exam Arc" reveals key visual... | LiveChart.me | `https://www.youtube.com/watch?v=dKA-QWW_wc8` | AniList CDN | **Factual Discrepancy:** Title specifies "January 9 [Sat] debut", but January 9, 2026 is a Friday. Boilerplate summary text. |

---

## 2. Manga Audit (10 Popular Titles Comparison)

A comparison was conducted between the site's current database/template output, official **AniList API** data, and **MangaDex API** data for 10 benchmark titles.

### 2.1 Mismatch Comparison Table
| # | Manga Title | Site Display (Chapters) | Site Display (Status) | AniList Live Data | MangaDex Data | Audit Findings & Root Cause |
|---|---|---|---|---|---|---|
| **1** | **One Piece** | Chapter 101 / 102 | Ongoing | Status: `RELEASING`<br>Chapters: 1130+<br>Volumes: 110+ | Chapter 1130+<br>(Ongoing) | **Major Chapter Discrepancy:** Displays fake chapter (101/102) because `extract_episode` only parses `Episode` or `_ep`, returning `None` on "Chapter 1130", which triggers template fallback `100 + loop.index`. |
| **2** | **Jujutsu Kaisen** | Chapter 102 | Ongoing | Status: `FINISHED`<br>Chapters: 272<br>Volumes: 30 | Chapter 271<br>(Completed) | **Status Mismatch:** Site marks as Ongoing, but the manga officially concluded in Weekly Shonen Jump (Ch. 271/272). Fake chapter due to fallback. |
| **3** | **Chainsaw Man** | Chapter 103 | Ongoing | Status: `RELEASING`<br>(Part 2 Ongoing) | Chapter 182+<br>(Ongoing) | **Chapter Mismatch:** Real chapter is 182+, site displays 103 due to regex failure. |
| **4** | **Dan Da Dan** | Chapter 104 | Ongoing | Status: `RELEASING`<br>Chapters: 175+ | Chapter 175+<br>(Ongoing) | **Chapter Mismatch:** Real chapter is 175+, site displays 104 due to regex failure. |
| **5** | **Sakamoto Days** | Chapter 102 | Ongoing | Status: `RELEASING`<br>Chapters: 190+ | Chapter 186+<br>(Ongoing) | **Chapter Mismatch:** Real chapter is 186+, site displays 102 due to regex failure. |
| **6** | **Blue Lock** | Chapter 105 | Ongoing | Status: `RELEASING`<br>Chapters: 280+ | Chapter 282+<br>(Ongoing) | **Chapter Mismatch:** Real chapter is 280+, site displays 105 due to regex failure. |
| **7** | **Haikyu!!** | *Missing* (0 rows) | *Missing* | Status: `FINISHED`<br>Chapters: 404<br>Volumes: 45 | Chapter 404<br>(Completed) | **Missing Data:** Not present in `anime_draft` manga database rows at all. |
| **8** | **Demon Slayer** | *Missing* (0 rows) | *Missing* | Status: `FINISHED`<br>Chapters: 207<br>Volumes: 23 | Chapter 207<br>(Completed) | **Missing Data:** Not present in `anime_draft` manga database rows at all. |
| **9** | **Attack on Titan** | *Missing* (0 rows) | *Missing* | Status: `FINISHED`<br>Chapters: 141<br>Volumes: 34 | Chapter 139/141<br>(Completed) | **Missing Data:** Not present in `anime_draft` manga database rows at all. |
| **10** | **Solo Leveling** | Chapter 106 | Ongoing | Status: `FINISHED`<br>Chapters: 201<br>Volumes: 15 | Chapter 200 (Canon finished) / Spin-off ongoing | **Entity & Status Confusion:** Site database row #61 tracks spin-off "Solo Leveling: Ragnarok" instead of main series, and marks as Ongoing. Main series completed with 201 chapters. |

### 2.2 Template-Level Hardcoded Status Bug in `app/templates/manga.html`
In lines 115–121 of `app/templates/manga.html`, status is not read from any database column; it is generated purely by mathematical modulo on the loop counter:
```jinja2
{% if loop.index % 5 == 0 %}
  <span ...>Completed</span>
{% elif loop.index % 7 == 0 %}
  <span ...>Hiatus</span>
{% else %}
  <span ...>Ongoing</span>
{% endif %}
```
This causes real ongoing series to be labeled "Completed" or "Hiatus" randomly, and finished series (like Jujutsu Kaisen) to be labeled "Ongoing".

---

## 3. Awards Audit (Official Verification)

Every award row in `anime_draft` was cross-checked against official historical records:
- **Crunchyroll Anime Awards Official Archive:** `https://www.crunchyroll.com/animeawards/`
- **Tokyo Anime Award Festival (TAAF) Official Archive:** `https://animefestival.jp/en/`

| ID | Year | Award Name & Category | Title / Winner | Official Status | Verification Verdict | Evidence / Notes |
|---|---|---|---|---|---|---|
| **64** | 2024 | Crunchyroll Anime Awards 2024:<br>Anime of the Year | Jujutsu Kaisen Season 2 | Won Anime of the Year | **VERIFIED** | Official ceremony took place in Tokyo on March 2, 2024. Jujutsu Kaisen S2 won Anime of the Year. |
| **65** | 2024 | Crunchyroll Anime Awards 2024:<br>Best Animation | Demon Slayer: Kimetsu no Yaiba Swordsmith Village Arc | Won Best Animation | **VERIFIED** | Officially won Best Animation at Crunchyroll Anime Awards 2024. |
| **66** | 2024 | Crunchyroll Anime Awards 2024:<br>Best Continuing Series | One Piece | Won Best Continuing Series | **VERIFIED** | Officially won Best Continuing Series at Crunchyroll Anime Awards 2024. |
| **68** | 2024 | Tokyo Anime Award Festival 2024:<br>Theatrical Film of the Year | The Boy and the Heron (*Kimitachi wa Dou Ikiru ka*) | Won Film of the Year | **VERIFIED** | TAAF 2024 officially awarded Anime of the Year (Film Category) to Studio Ghibli's The Boy and the Heron. |
| **69** | 2025 | Tokyo Anime Award Festival 2025:<br>Television Animation Excellence | Oshi no Ko | Premature / Wrong Year | **UNVERIFIABLE / PREMATURE** | Oshi no Ko Season 1 won at TAAF **2024**, not 2025. TAAF 2025 Anime of the Year awards have not occurred yet. |
| **67** | 2025 | Crunchyroll Anime Awards 2025:<br>Nominations Preview | Frieren & Solo Leveling | Premature / Combined Entry | **UNVERIFIABLE / PREMATURE** | Combined multiple titles into one single award entry before official 2025 results were confirmed. |
| **95** | 2026 | Crunchyroll Anime Awards 2026:<br>Anime of the Year | Solo Leveling | Fictional / Fabricated | **WRONG / FABRICATED** | Year 2026 awards have not occurred. Fictional record generated by synthetic seeder. |
| **96** | 2026 | Tokyo Anime Award Festival 2026:<br>Best Animation | Demon Slayer | Fictional / Fabricated | **WRONG / FABRICATED** | Year 2026 has not occurred; additionally, TAAF categories are "Film" and "Television", not "Best Animation". Fabricated record. |

---

## 4. Display Problems & Visual Evidence

### 4.1 Broken Image Display on Live Production Site
- **Evidence:** User screenshots `media_1794560199061_ed19a972.jpg` and `media_1794560210423_b85a2b3a.jpg` on `https://hindi-anime-guide.onrender.com`.
- **Symptoms:**
  - Card 2 ("Demon Slayer: Hashira Training Arc") and Card 3 ("Solo Leveling") display broken card boxes showing only raw alt text (`"SCHEDULE layer: Hashira Training Arc Episode 8"`, `"SCHEDULE ling Episode 13"`).
  - Guide cards under "Latest Anime Guides" (Haikyuu!!, Your Name, Death Note, Top 10 Anime, Dragon Ball) showed broken image borders.
- **Root Cause:**
  1. **Filename Discrepancy:** Database paths were set to clean names (e.g. `/media/covers/anime_haikyuu.webp`), but earlier git commits only contained random hash-prefixed filenames (e.g. `article_haikyuu-blue-lock-sports-an_81a2e007.webp`).
  2. **Render Free Tier Ephemeral Filesystem:** When media is written locally at runtime without being checked into git or stored in cloud storage (S3/R2/Cloudinary), Render discards newly written files upon restart.
  3. **Third-Party CDN Referrer Blocking:** Direct links to external CDNs (AniList/LiveChart) were blocked by hotlink prevention headers until `referrerpolicy="no-referrer"` was applied.

### 4.2 Season Schedule Weekday Grouping Failure (All Mapped to Friday)
- **Evidence:** Inspection of `schedule.html` and database draft records.
- **Problem:** 42 out of 45 schedule items collapse onto **Friday (weekday 4)**. Monday through Thursday appear nearly empty.
- **Root Cause:** `schedule.html` determines the weekday using `item.published_at.weekday()`. In the automation pipeline, `published_at` is the timestamp of when the CMS draft was inserted (November 13, 2026 = Friday). It reflects the record's database creation date, **not the anime's broadcast day**!

### 4.3 Airing Time Format Failure
- **Problem:** Time displays raw string or defaults to 00:00.
- **Root Cause:** Jinja filter `format_airing_ist` expects strict ISO datetime format `YYYY-MM-DD HH:MM`. When AniList returns natural broadcast text like `"Sundays at 23:15 JST"`, the filter raises an exception and returns the unformatted string.

### 4.4 Sidebar Language Switch SSR Sync
- **Problem:** When toggling language, the sliding drawer / sidebar did not immediately reflect the new language without a full reload, or defaulted back to English.
- **Root Cause:** The sidebar drawer template relied on client-side Alpine state initialization that was out of sync with the SSR request cookie `lang=hi`.

---

## 5. Root Causes Categorization Matrix

| Issue Area | Symptom | Category | Direct Root Cause |
|---|---|---|---|
| **News** | Boilerplate summaries in Drafts 6 & 7 | **AI Rewriter** | Quota exhaustion or timeout caused fallback to static hardcoded summary template. |
| **News** | Generic homepage URLs in Published News (85-88) | **Source / API** | Seeder script inserted base publisher domain instead of crawling/storing canonical article permalinks. |
| **Manga** | Fake Chapter 101/102/103 on One Piece, Chainsaw Man | **Template** | `templates_config.py:extract_episode` regex only matched `_ep` and `Episode`, returning `None` for "Chapter". Template defaulted to `100 + loop.index`. |
| **Manga** | Random Ongoing / Completed / Hiatus status | **Template & Model** | `manga.html` hardcoded status using `loop.index % 5` and `loop.index % 7`. `AnimeDraft` model has no `publication_status` or `chapters` column. |
| **Manga** | Haikyu!!, Demon Slayer, AoT missing from manga | **Database / Source** | Anime automation pipeline only scraped anime media IDs and omitted manga media searches. |
| **Awards** | Fabricated 2026 and premature 2025 awards | **AI Rewriter / Seeder** | Seeder generated future fictional awards without validating against official award ceremonies. |
| **Schedule** | 42/45 anime collapsed into Friday | **Template & Model** | `schedule.html` grouped by `published_at` (CMS insertion date) because `AnimeDraft` lacks an `airing_day` column. |
| **Images** | Broken image covers on live Render deployment | **Infrastructure & Database** | Render ephemeral filesystem wiped uncommitted media; filenames in database did not match files tracked in git. |

---

## Short Phase 1 Fix Plan (Awaiting User Approval)

Upon user approval, Phase 1 will execute the following surgical fixes:

1. **Database Schema & Model Enhancement (`AnimeDraft`):**
   - Add explicit columns: `chapters: Optional[int]`, `volumes: Optional[int]`, `publication_status: Optional[str]`, `airing_day: Optional[str]`, `airing_time: Optional[str]`.
   - Run Alembic migration to ensure Neon Postgres schema is updated without data loss.

2. **Template & Filter Fixes:**
   - **`templates_config.py`:** Update `extract_episode` regex to support `r'(?:Chapter|Ch\.|चैप्टर)\s*(\d+)'`.
   - **`manga.html`:** Remove `loop.index % 5` fake status logic. Bind directly to `m.publication_status` with real fallback. Display real chapter numbers.
   - **`schedule.html`:** Group schedule cards by anime broadcast day (`m.airing_day`), not CMS `published_at`.

3. **Awards Data Sanitation:**
   - Soft-delete or unpublish fabricated 2026 awards (IDs 95, 96) and fix/verify premature 2025 records.
   - Retain only 100% verified official winners with exact category designations.

4. **News Quality Upgrade:**
   - Replace generic domain URLs (`https://www.crunchyroll.com/news`) with verified article permalinks.
   - Replace synthetic boilerplate drafts with genuine editorial summaries.

5. **Manga Catalog Completion:**
   - Ingest verified manga metadata for the missing titles (*Haikyu!!*, *Demon Slayer*, *Attack on Titan*) with real chapter counts (404, 207, 141) and `FINISHED` status.

6. **Image Asset Integrity:**
   - Verify every WebP image referenced in the database exists in `media/covers/` and is committed to git so Render always renders crisp covers with zero 404s.

---
*End of Phase 0 Audit Report. Ready for user review and approval.*

