# Hindi Anime Guide - Content Automation Engine (Parts 1 - 4: Production Ready)

A legal, draft-first automation pipeline that collects factual anime updates from verified sources, synthesizes them into original English and natural Hindi (Devanagari) editorial drafts, and stages them for human editorial review and publishing without loading the Render web server.

---

## 1. Multi-Source Pipeline Architecture

```
[AniList GraphQL]  ──► [fetchers/anilist.py]       ──┐
[LiveChart RSS]    ──► [fetchers/livechart_rss.py] ──┤
[MangaDex API]     ──► [fetchers/manga.py]         ──┼──► [rewriter.py] ──► [saver.py] ──► [AnimeDraft DB]
[Admin Awards UI]  ──► [fetchers/awards.py]        ──┤   (Gemini AI /     (Daily Cap,        (Draft status /
[Comic Natalie]    ──► [fetchers/natalie_rss.py]   ──┤    No Copying)      Quality Gate,     Needs Review)
                       (Audited & SKIPPED)          │                      Near-Dup Check)
[Oricon News]      ──► [fetchers/oricon_rss.py]    ──┘
                       (Audited & SKIPPED)

                                       ▲
                                       │ Scheduled via
                       [.github/workflows/fetch.yml]
                       (Every 3h on GitHub Actions runner)
```

---

## 2. Source Audit, Status & Legal Decisions

| Source | Content Type | Status | Method / Protocol | Legal & Technical Audit Reason |
| :--- | :--- | :--- | :--- | :--- |
| **AniList** | `schedule` | **ACTIVE** | GraphQL (`graphql.anilist.co`) | Official public API; rate-limited (90 req/min); no API key required; verified cover CDN URLs. |
| **LiveChart.me** | `news` | **ACTIVE** | RSS 2.0 (`/feeds/headlines`) | Verified active public RSS feed (HTTP 200); facts-only ingested (title, date, link); cover images matched via AniList search; never hosts 3rd-party images. |
| **MangaDex** | `manga` | **ACTIVE** | Official REST API (`api.mangadex.org`) | Chosen over Jikan API. Provides granular chapter release timestamps, chapter numbers, and titles with rate limiting (5 req/sec). Jikan timed out and MAL lacks real-time chapter feeds. |
| **Comic Natalie** | `news` | **SKIPPED** | HTTP 405 / AWS WAF Challenge | Gated behind AWS WAF CAPTCHA ("Human Verification" challenge). No open public RSS feed. Scraping is strictly prohibited by site terms and project safety rules. |
| **Oricon News** | `news` | **SKIPPED** | HTTP 410 Gone | Public RSS feeds (`/rss/`, `/news/rss/`) were officially retired by Oricon (HTTP 410 Gone). Direct HTML scraping is prohibited by terms of service and robots.txt. |
| **Anime Awards** | `award` | **MANUAL ENTRY** | Admin Form (`/admin/drafts/create-award`) | Major anime awards (Crunchyroll Anime Awards, Tokyo Anime Award Festival) are annual events without recurring public APIs. Admin enters verified facts (ceremony, category, winner, date, link) and Gemini synthesizes bilingual drafts. |

---

## 3. Production Scheduling (GitHub Actions)

The workflow `.github/workflows/fetch.yml` offloads compute and network traffic completely from the Render free-tier web dyno.

- **Cron Schedule:** `0 */3 * * *` (Runs every 3 hours: 00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00 UTC).
- **Manual Trigger:** Supports `workflow_dispatch` with custom `--source` and `--limit` inputs directly from the GitHub UI.
- **Concurrency Lock:** `group: content-automation-fetch`, `cancel-in-progress: false` prevents two runs from overlapping or racing.
- **Execution Timeout:** Strict 15-minute timeout.
- **Zero Secret Leakage:** Secrets are masked by default in GitHub Actions logs.

---

## 4. How to Add GitHub Secrets (Exact Steps)

Before running the GitHub Actions workflow in production, add the required secrets in your GitHub repository:

1. Open your repository on GitHub: `https://github.com/<your-username>/<your-repo-name>`
2. Click **Settings** (top navigation bar of the repository).
3. In the left sidebar, expand **Secrets and variables** and click **Actions**.
4. Click the green button: **New repository secret**.
5. Add Secret 1:
   - **Name:** `DATABASE_URL`
   - **Secret:** Your Neon PostgreSQL connection string (e.g., `postgresql+asyncpg://...` or standard `postgres://...` / `postgresql://...`).
   - Click **Add secret**.
6. Add Secret 2:
   - Click **New repository secret** again.
   - **Name:** `GEMINI_API_KEY`
   - **Secret:** Your Google Gemini API Key from [Google AI Studio](https://aistudio.google.com/app/apikey).
   - Click **Add secret**.

*(Note: Never commit your secrets or `.env` file to git).*

---

## 5. How to Pause or Disable Automation

If you ever need to pause automatic background fetching (e.g. during database maintenance or quota resets):

### Option A: Disable via GitHub Actions Web UI (Recommended)
1. Go to your repository on GitHub.
2. Click the **Actions** tab.
3. In the left sidebar, select **Content Automation Pipeline**.
4. Click the `...` (three dots) icon at the top right of the workflow header.
5. Click **Disable workflow**.
6. To resume later, click **Enable workflow**.

### Option B: Disable via Cron Comment
Comment out the `- cron:` block in `.github/workflows/fetch.yml` and commit.

---

## 6. Safety, Quality Gates & Purge Rules

1. **Zero Auto-Publishing:** Every single ingested item is staged with `status="draft"` or `status="needs_review"`. Nothing goes live without human admin review.
2. **Duplicate & Near-Duplicate Protection:**
   - Exact deduplication via `external_id`.
   - Near-duplicate detection (`is_near_duplicate`): items with matching or nearly identical titles within 48 hours are automatically skipped.
3. **Quality Gate (`status="needs_review"`):**
   A draft is flagged as `needs_review` instead of `draft` if:
   - Summary word count is below 60 words.
   - Summary contains untranslated Japanese characters (`Kanji`, `Hiragana`, `Katakana`).
   - High similarity / token overlap with the raw source text (> 70%).
   - Summary is empty or missing.
   These drafts appear in the admin filter under **"Needs Review"** (`/admin/drafts?status=needs_review`).
4. **Daily Caps:**
   - Per-source daily cap: default 10 new drafts per source.
   - Global daily Gemini request cap: default 40 requests per calendar day across all sources to stay safely inside free tier limits.
5. **Auto-Purge of Old Drafts:**
   - At the beginning of each pipeline run, unreviewed drafts older than 30 days (`status IN ('draft', 'needs_review')`) are purged.
   - **Published items are never deleted.**

---

## 7. How to Add a New Source in 4 Steps

To integrate a new source (e.g. Crunchyroll News RSS or ANN):

1. **Create Fetcher:** Create `automation/fetchers/<source_name>.py`. Implement a function returning standard normalized dicts (`external_id`, `content_type`, `source_name`, `source_url`, `title_en`, `pub_date_iso`, `raw_summary`, `image_url`).
2. **Register in `SOURCE_REGISTRY`:** Open `automation/run_all.py` and add the new source to `SOURCE_REGISTRY`:
   ```python
   "mysource": {
       "display_name": "My Source Name",
       "source_name": "MySource",
       "fetcher": fetch_mysource_items,
       "default_type": "news",
   }
   ```
3. **Add Argument Choice:** Add `"mysource"` to `--source` choices in `main()`.
4. **Test:** Run `python automation/run_all.py --source mysource --limit 3` and verify draft creation.

---

## 8. CLI Commands Quick Reference

```bash
# Run all active sources (AniList, LiveChart.me, MangaDex) with limit 3
python automation/run_all.py --limit 3

# Run a specific source only
python automation/run_all.py --source livechart --limit 5
python automation/run_all.py --source manga --limit 5
python automation/run_all.py --source anilist --limit 5

# Override caps
python automation/run_all.py --daily-cap 20 --limit 10

# Disable offline fallback (strictly require live Gemini API key)
python automation/run_all.py --no-fallback
```

---

## 9. Troubleshooting Guide

- **Rate Limit 429 on AniList / LiveChart / MangaDex:** The pipeline automatically backs off exponentially (5s, 10s, 15s) and retries up to 3 times before safely skipping the item.
- **Gemini Free Quota Exhausted:** If `GEMINI_API_KEY` hits quota or HTTP 429, the pipeline logs the error and automatically uses deterministic factual drafts so the pipeline never crashes.
- **Drafts Flagged as `needs_review`:** Check `/admin/drafts?status=needs_review`. The admin edit page allows direct editing of the text and one-click publishing.
- **GitHub Actions Failures:** Check the **Actions** tab on GitHub for execution logs. Verify that `DATABASE_URL` and `GEMINI_API_KEY` secrets are correctly set without trailing spaces or quotes.
