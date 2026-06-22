# Recipe Recommendation App — Design Spec

**Date:** 2026-06-20  
**Status:** Draft — pending user review  
**Source:** Brainstorm session at `brainstorms/2026-06-20-recipe-app.md`

---

## 1. Overview

A personal, single-user web app running on a Hetzner VPS (Ubuntu, bare metal) that:

1. **Ingests recipes** from Instagram posts or any recipe URL via a Telegram bot
2. **Extracts structured data** using a cloud LLM (Claude API)
3. **Stores everything** in SQLite
4. **Provides a web dashboard** to browse, filter, rate, edit, and cross-link recipes — helping Pedro decide what to cook

---

## 2. Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.12+ | Rich ecosystem for Telegram, scraping, LLM APIs |
| Web framework | FastAPI | Async-native, lightweight, good with background tasks |
| Database | SQLite (WAL mode) | Zero-admin, perfect for single-user scale |
| ORM | SQLModel / SQLAlchemy | Mature, FastAPI-aligned |
| Templates | Jinja2 | FastAPI built-in; server-rendered HTML |
| Telegram | `python-telegram-bot` | Polling mode; no TLS headaches |
| IG scraping | `instaloader` | Mature, handles auth + post metadata |
| Generic scraping | `httpx` + `BeautifulSoup` | Lightweight HTTP fetch + HTML text extraction |
| LLM | Claude API (`anthropic` SDK) | High-quality structured extraction |
| Frontend CSS | Tailwind via CDN or minimal hand-written CSS | No build step needed |
| WSGI/HTTP | `uvicorn` behind `nginx` | Production-grade ASGI serving |
| Process manager | `systemd` | Native Ubuntu service management |

---

## 3. Architecture

Monolithic single-process FastAPI app. One `systemd` service. All components run in the same process, coordinated via `asyncio` background tasks.

```
                          VPS (Hetzner / Ubuntu)
 ┌─────────────────────────────────────────────────────────┐
 │                                                         │
 │  Telegram API ←──polling──→ FastAPI App                 │
 │                                            │            │
 │  ┌─────────────────────────────────────────┤──────────────┐
 │  │                                         │              │
 │  │  ┌──────────┐  ┌───────────┐  ┌────────┴──────┐      │
 │  │  │ Telegram │  │  Scraper  │  │  LLM Extractor │      │
 │  │  │ Handler  │  │ (dual)    │  │  (Claude API)  │      │
 │  │  └────┬─────┘  └─────┬─────┘  └──────┬────────┘      │
 │  │       │              │               │               │
 │  │       └──────────────┼───────────────┘               │
 │  │                      ↓                               │
 │  │                 SQLite DB                            │
 │  │                                                      │
 │  │  ┌──────────┐  ┌───────────┐  ┌──────────────┐     │
 │  │  │  Web     │  │  Jinja2   │  │  /static/    │     │
 │  │  │  Routes  │  │  Templates│  │   photos     │     │
 │  │  └──────────┘  └───────────┘  └──────────────┘     │
 │  └──────────────────────────────────────────────────────┘ │
 │                         ↓                                │
 │  Browser ←── nginx ──→ uvicorn                           │
 └─────────────────────────────────────────────────────────┘
```

### Components

| Component | Responsibility |
|-----------|---------------|
| **Telegram Handler** | Polls for updates, validates sender, dispatches URL to extraction pipeline |
| **Scraper (dual)** | Routes URLs: Instagram → `instaloader`, everything else → `httpx`+`BeautifulSoup`. Returns raw text + downloaded image |
| **LLM Extractor** | Sends raw text to Claude API, returns structured recipe dict (JSON) |
| **Cross-Linker** | After each insert, detects similar recipes by ingredient overlap + name similarity, auto-creates bidirectional links |
| **Web Routes** | Browse (filtered grid), Recipe Detail (view/edit/rate/delete), Add Recipe (manual or URL fetch) |

### Data flow (ingestion)

```
User shares link via Telegram
        │
        ▼
Bot receives URL ──→ Acknowledges: "Got it!"
        │
        ▼
[Background task]
  1. URL routed to correct scraper
  2. Raw text + image downloaded
  3. Text → Claude API → structured JSON
  4. JSON validates → inserted into SQLite
  5. Cross-link detector runs
  6. Bot notifies: "✅ Recipe Name added — /recipe/{id}"
```

---

## 4. Data Model

### Table: `recipes`

```sql
CREATE TABLE recipes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,          -- "Bolo de Chocolate (whey protein)"
    dish_name           TEXT NOT NULL,          -- "Bolo de Chocolate"
    distinguisher       TEXT,                   -- "whey protein"
    type                TEXT NOT NULL,          -- 'sweet' | 'savory'
    subtype             TEXT,                   -- 'main'|'dessert'|'snack'|'soup'|'salad'|'breakfast'|'side'|'drink'
    calories_per_portion INTEGER,              -- estimated by LLM, nullable
    calorie_tier        TEXT,                   -- computed: 'low'|'mid'|'high'
    macro_tags          TEXT DEFAULT '[]',      -- JSON array: ["protein-rich","low-carb",...]
    ingredients         TEXT DEFAULT '[]',      -- JSON array: ["eggs","flour","chocolate",...]
    prep_time           INTEGER,                -- minutes
    cook_time           INTEGER,                -- minutes, nullable (no-cook dishes)
    total_time          INTEGER,                -- computed: prep_time + cook_time
    portions            INTEGER,                -- serves how many
    instructions        TEXT,                   -- full steps as markdown
    photo_path          TEXT,                   -- relative path: 'photos/42.jpg'
    source_url          TEXT,                   -- original instagram.com/p/... or any URL
    rating              INTEGER CHECK(1 <= rating AND rating <= 5),
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Table: `cross_links`

```sql
CREATE TABLE cross_links (
    recipe_id       INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    similar_to_id   INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    auto_generated  BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (recipe_id, similar_to_id)
);
```

- Bidirectional: a row `(1, 3)` means recipe 1 ↔ recipe 3 are similar. Query both directions to find all links.
- `auto_generated`: true if the system detected it, false if Pedro created it manually.

### Calorie tier logic

Computed from `calories_per_portion` before insert/update:

| Range | Tier |
|-------|------|
| `< 300` | `low` |
| `≥ 300 AND < 600` | `mid` |
| `≥ 600` | `high` |
| `NULL` | `NULL` |

### Macro tags

LLM picks freely from a known set. Expected values: `protein-rich`, `low-carb`, `keto`, `vegan`, `gluten-free`, `fiber-rich`, `high-fat`, `dairy-free`. The system does not enforce an enum — it learns from what the LLM produces. The Browse filter sidebar builds its checkbox list dynamically from all tags present in the DB.

### Ingredients

Stored as JSON array. The ingredient overlap detector uses Jaccard similarity. The Browse filter sidebar shows all unique ingredients across all recipes as multi-select checkboxes (AND logic: selecting `eggs` + `chocolate` shows recipes containing both).

---

## 5. Extraction Pipeline

### 5.1 Scraper

Two fetch strategies behind a unified interface `fetch_content(url: str) → ScrapedContent`:

| URL type | Strategy | Library |
|----------|----------|---------|
| `instagram.com/p/*` or `instagram.com/reel/*` | Authenticated IG fetch | `instaloader` |
| Any other URL | HTTP GET → HTML text extraction | `httpx` + `BeautifulSoup` |

`ScrapedContent` is:
```python
@dataclass
class ScrapedContent:
    text: str           # all extracted text
    image_path: str | None  # downloaded image file path, or None
    source_url: str
```

- `instaloader` fetches the post's caption, media metadata, and downloads the best-resolution image
- Generic scraper fetches the page, strips `<script>`, `<style>`, `<nav>`, `<footer>`, then extracts text from `<article>` or `<main>` or `<body>`. Downloads the first large image found
- Requires an Instagram account for auth (throwaway or Pedro's). Credentials stored in `.env`

### 5.2 LLM Extraction

Input: raw text from the scraper.  
Output: structured JSON mapping to the `recipes` table fields.

**Prompt contract** — the LLM is asked to return:
- `dish_name` — the canonical dish name (e.g., "Bolo de Chocolate")
- `distinguishing_feature` — what makes this version unique (e.g., "whey protein", "low-cal", "air fryer")
- `type` — "sweet" or "savory"
- `subtype` — one from the allowed list (null if uncertain)
- `macro_tags` — array of applicable tags
- `calories_per_portion` — best estimate as integer (null if not mentioned)
- `ingredients` — array of strings, normalized (e.g., "egg" not "eggs", lowercase)
- `prep_time_minutes`, `cook_time_minutes` — integers (null if unclear)
- `portions` — integer (null if unclear)
- `instructions` — full markdown (null if not present in text)
- `missing_critical_info` — boolean flag if this extraction is thin (e.g., no ingredients found)

**Error handling:**
- LLM returns unparseable JSON → retry once with stricter prompt
- Still fails → save raw text + URL, flag for manual entry, notify user
- Missing fields → stored as NULL; user fills in on web

### 5.3 Cross-Link Detector

Runs after every successful recipe insertion:

1. Query existing recipes with the same `type`
2. Compute Jaccard similarity on ingredient sets
3. Compute Levenshtein ratio on `dish_name`
4. If either score ≥ 0.6 → auto-create bidirectional `cross_link` row
5. If any manual link already exists between these two, skip

Manual links (created by Pedro on the web dashboard) take precedence — auto-linking never overrides them.

---

## 6. Web Dashboard

### 6.1 Pages

| Route | Template | Purpose |
|-------|----------|---------|
| `GET /` | `browse.html` | Faceted filter sidebar + recipe grid |
| `GET /recipe/{id}` | `detail.html` | Full recipe view, edit, cross-links, delete |
| `GET /add` | `add.html` | Manual entry or URL-based fetch |
| `GET /edit/{id}` | `add.html` | Edit existing recipe (same form, pre-filled) |

### 6.2 Browse Page (`/`)

**Filter sidebar** dimensions:

| Dimension | Widget | Logic |
|-----------|--------|-------|
| Type | Checkboxes: Sweet, Savory | OR within, AND across dimensions |
| Subtype | Checkboxes (dynamic) | OR within |
| Ingredients | Multi-select checkboxes (dynamic, scrollable) | AND: selecting `eggs`+`chocolate` shows recipes with both |
| Macros | Checkboxes (dynamic, from DB) | OR within |
| Calories | Checkboxes: Low, Mid, High | OR within |
| Max Time | Range slider (0–180 min) | Filters on `total_time` |
| Min Rating | Star selector (1–5) | Filters on `rating` |

All filters apply via query parameters (`?type=sweet&type=savory&max_time=60`). Filters toggle instantly — no "Apply" button.

**Recipe grid:** cards show thumbnail photo, title, rating stars, total time, calorie tier badge. Default sort: newest first. Click → detail page.

### 6.3 Recipe Detail (`/recipe/{id}`)

- Full-size photo
- Title with distinguishing feature callout
- Rating (1–5 stars, clickable to change)
- Type + Subtype + Calorie tier + Macro badges
- Times: Prep / Cook / Total + Portions
- Ingredients list (plain text, one per line)
- Instructions (Markdown rendered)
- Source link → opens in new tab
- **Cross-links section:** "Similar Recipes" — card grid of linked recipes. "Link manually" button opens a search to find and link another recipe
- **Edit button** → toggles inline form (all fields editable)
- **Delete button** → confirmation modal → deletes

### 6.4 Add Recipe (`/add`)

Two entry paths:

**URL-based:** paste any URL → click "Fetch & Extract" → spinner while pipeline runs → form pre-fills with extracted data → review and edit → "Save Recipe"

**Manual:** fill all fields directly → "Save Recipe"

URL-based path uses the same scraper + LLM pipeline as the Telegram bot. On success, redirects to `/recipe/{id}`.

### 6.5 Edit Recipe (`/edit/{id}`)

Same form as Add, pre-filled. On save, recalibrates `calorie_tier`, `total_time`, and re-runs the cross-link detector (since ingredients or dish name may have changed).

---

## 7. Telegram Bot

### 7.1 Setup

- Bot created via [@BotFather](https://t.me/BotFather)
- `TELEGRAM_BOT_TOKEN` stored in `.env`
- **Polling mode** (`getUpdates`) — no webhook, no TLS required. IP-only compatible.
- Bot starts its polling loop on FastAPI app startup (`asyncio.create_task`)

### 7.2 Behavior

| Input | Bot response |
|-------|-------------|
| Instagram or recipe URL | "👨‍🍳 Got it! Extracting recipe..." |
| Any other message | Ignored (or "Send me a recipe link!") |
| Non-Pedro sender | Silently ignored (hardcoded user ID filter) |

On extraction success:
> ✅ **Bolo de Chocolate (whey protein)** added!  
> ⏱ 35min · 🔥 high cal · 🍗 protein-rich  
> 📋 View: http://<vps-ip>/recipe/42

On extraction failure:
> ⚠️ Couldn't extract that one — the post may be private, deleted, or rate-limited.

On LLM extraction failure:
> ⚠️ Extraction failed for that link. You can add it manually at http://<vps-ip>/add

**Serialization:** one extraction at a time. A simple `asyncio.Lock` prevents concurrent extractions. No queue needed for personal scale.

---

## 8. Deployment

### 8.1 Directory Layout

```
/srv/recipe-app/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── bot.py
│   ├── scraper.py
│   ├── extractor.py
│   ├── linker.py
│   ├── models.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── browse.html
│   │   ├── detail.html
│   │   └── add.html
│   └── static/
│       ├── photos/          # Downloaded recipe images
│       └── css/
│           └── app.css
├── data/
│   └── recipes.db
├── requirements.txt
├── .env                     # Secrets (TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, IG_USERNAME, IG_PASSWORD)
└── deploy/
    ├── recipe-app.service
    └── nginx.conf
```

### 8.2 systemd Unit

```ini
[Unit]
Description=Recipe App (FastAPI)
After=network.target

[Service]
Type=simple
User=recipe-app
WorkingDirectory=/srv/recipe-app
ExecStart=/srv/recipe-app/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
EnvironmentFile=/srv/recipe-app/.env

[Install]
WantedBy=multi-user.target
```

### 8.3 nginx

Reverse proxy on port 80. Serves `/static/` directly from disk (no Python overhead for images/CSS). Single `server` block. No TLS — IP-only access.

### 8.4 Environment Variables (`.env`)

```
TELEGRAM_BOT_TOKEN=tokengoeshere
ANTHROPIC_API_KEY=sk-ant-...
IG_USERNAME=throwaway_account
IG_PASSWORD=password
ALLOWED_TELEGRAM_USER_ID=123456789
```

### 8.5 Deploy Steps

```
git pull
source venv/bin/activate
pip install -r requirements.txt
pytest                              # pre-deploy check
sudo systemctl restart recipe-app
```

---

## 9. Error Handling & Edge Cases

| Scenario | Handling |
|----------|----------|
| Instagram post is private | `instaloader` raises an auth/visibility error → bot replies "Couldn't fetch" |
| Instagram post deleted | 404 from Instagram → bot replies "That post may be unavailable" |
| Generic URL timeout/404 | `httpx` raises → bot replies with error |
| LLM returns garbage JSON | Retry once. If still garbage → flag for manual entry, notify user |
| Image download fails | Recipe saved without photo. A grey placeholder thumbnail is shown in the recipe card and detail page |
| Scraper text is empty (image-only post, no caption) | LLM gets minimal input. Best-effort extraction (may return mostly nulls). Recipe saved, user fills gaps on web |
| Duplicate Instagram URL shared | Detect by `source_url` uniqueness check → bot replies "That recipe is already saved: /recipe/{id}" |
| Concurrent extraction attempts | `asyncio.Lock` — second request gets "Still processing your previous link, one moment..." |
| DB locked | SQLite WAL mode + single writer avoids this. If it happens, auto-retry up to 3 times |

---

## 10. Testing Strategy

| Scope | Approach | Tools |
|-------|----------|-------|
| LLM Extractor | Fixture texts (5–10 IG captions, 2 generic URLs) → assert structured output correctness | `pytest` |
| Scraper | Mock `instaloader` + mock `httpx`; test URL routing and error modes | `pytest` |
| Cross-Linker | Known recipe sets → assert links created/not created at correct thresholds | `pytest` |
| Routes | FastAPI `TestClient` smoke tests: `/`, `/recipe/1`, `/add` — assert 200 | `pytest` |
| Bot handler | Mock Telegram `Update` objects → assert correct bot response text | `pytest` |

Run `pytest` before each deploy. No CI/CD — manual pre-deploy step.

---

## 11. Out of Scope (v2+)

- Multi-user support
- Telegram bot queries ("show me high-protein mains")
- Natural language search
- Export/backup functionality
- Docker deployment
- TLS / domain
- Notification preferences beyond completion message

---

*Source brainstorm: `brainstorms/2026-06-20-recipe-app.md`*
