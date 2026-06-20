# Recipe Recommendation App: Brainstorm / Discovery Notes
Date: 2026-06-20 · Goal: Define requirements for a VPS-hosted app that ingests Instagram recipe posts via Telegram, extracts structured recipe data, and serves as a queryable personal recipe decision engine.

## Summary / key decisions
- Telegram bot for ingestion (not WhatsApp)
- Single-user, personal tool
- Pure AI/LLM extraction from Instagram posts, fire-and-forget bot interaction
- FastAPI + SQLite on Hetzner/Ubuntu, bare metal, IP-only
- Web dashboard with faceted filter sidebar for browsing
- Instagram scraping via instaloader

## Q&A log

### Q1 – Ingestion mechanism
- Captured: **Telegram bot** (not WhatsApp). Create via BotFather, webhook to VPS, no business verification.
- Flags: none

### Q2 – Single-user or multi-user?
- Captured: **Single-user, just for Pedro.** No auth, no multi-tenant isolation.
- Flags: none

### Q3 – What the Telegram bot receives
- Captured: Instagram's native share-to-Telegram sends a **link** (with thumbnail preview). Bot receives URL + any media.
- Flags: Instagram scraping feasibility — public posts only.

### Q4 – Extraction approach
- Captured: **Pure AI/LLM (A).** Scrape post content → send to LLM → extract structured fields automatically. No manual confirmation.
- Flags: Cost management. Thin extractions for posts without captions.

### Q5 – Cloud LLM vs local model
- Captured: **Cloud model** (Claude/GPT). Quality matters for inconsistent formats, low volume = negligible cost.
- Flags: specific model choice → implementation detail.

### Q6 – Query and browse interface
- Captured: **Web dashboard (B).** Bot handles ingestion only. Web UI for searching, filtering, browsing. Bot queries can be added later.
- Flags: none

### Q7 – Tech stack
- Captured: **FastAPI + SQLite.** Single process, async-native, rich Python ecosystem. No DB server to manage.
- Flags: none

### Q8 – Instagram scraping
- Captured: **instaloader (A).** Fetches caption, image, metadata from public posts. Accepts occasional breakage.
- Flags: Needs an IG account for auth. Rate limiting risk (low volume mitigates this).

### Q9 – Data model fields
- Captured: **All fields**: title (auto: dish + distinguishing feature), type (sweet/savory), subtype (main/dessert/snack/soup/salad/etc.), macros (multi-tags: protein-rich, no-carbs, fiber-rich, etc.), calories tier (low/mid/high), ingredients (list), prep/cook/total time, photo (snapshot stored locally), source URL, rating (1-5), cross-links, created date, **portions**, **instructions** (full steps).
- Flags: none

### Q10 – Similarity / cross-linking
- Captured: **Automatic detection + manual override (B).** Ingredient overlap + dish name similarity auto-links recipes. User can manually link/unlink.
- Flags: Similarity threshold TBD during implementation (e.g., ≥60% ingredient overlap + fuzzy name match).

### Q11 – Auto-generated title format
- Captured: **(D) LLM decides** the most salient differentiator. "Bolo de Chocolate (whey protein)" vs "Bolo de Chocolate (low-cal)" — whatever makes this version unique.
- Flags: none

### Q12-14 – VPS, deployment, domain
- Captured: **Hetzner + Ubuntu, bare metal, IP-only.** Systemd + uvicorn + nginx. No Docker, no domain.
- Flags: none

### Q15 – Bot interaction flow
- Captured: **Fire and forget (A).** Share link → bot acknowledges → processes → notifies when done with recipe name + web link. Curation on web dashboard.
- Flags: none

### Q16 – Calorie tier thresholds
- Captured: **Absolute thresholds**: <300 cal/portion = low, <600 = mid, ≥600 = high.
- Flags: none

### Q17 – Search / filter experience
- Captured: **Faceted filter sidebar (A).** All dimensions as checkboxes/sliders, recipe grid updates live. No NL search for v1.
- Flags: none

### Q18 – Dashboard layout
- Captured: **Dedicated detail page + nav (B).** Browse page = filters + grid. Click → recipe detail page (full photo, ingredients, instructions, cross-links, edit, rating). Simple nav with "Browse" and "Add Recipe" (manual entry without IG link).
- Flags: none

### Q19 – Ingredients as filter + URL on Add page
- Captured: **Ingredients must be filterable** on Browse sidebar. **Add page must accept any URL** (not just Instagram) — paste a URL from any recipe site, "Fetch & Extract" triggers scrape+LLM pipeline, pre-fills form for review before save. Both web and Telegram ingestion paths.
- Flags: Generic URL scraping needs an HTTP fetcher (httpx + BeautifulSoup) in addition to instaloader for Instagram. The LLM extraction layer handles both uniformly.

### Architecture decision
- Captured: **Approach A — Monolithic single-process.** One FastAPI process: webhook endpoint, web dashboard, background extraction pipeline. One systemd service. SQLite with WAL mode.
- Flags: none

## Open flags (pending input)
- None — all decisions captured
