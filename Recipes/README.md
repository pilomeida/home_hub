# IG Recipes Hub

A personal recipe decision engine — share Instagram recipe posts to a Telegram bot, get structured data extracted automatically, and browse your collection from a web dashboard with faceted filtering.

## How It Works

```
Instagram → Share to Telegram bot → LLM extracts data → SQLite DB → Web dashboard
```

1. **Share** an Instagram post (or any recipe URL) to your Telegram bot
2. The bot **fetches** the post content, downloads the photo
3. Claude API **extracts** structured data: dish name, type, macros, ingredients, times, calories, instructions
4. Recipes are **stored** in SQLite, auto-cross-linked with similar dishes
5. **Browse and filter** via the web dashboard by type, macros, calories, time, rating, ingredients

## Stack

| Layer | Technology |
|-------|-----------|
| Web | FastAPI + Jinja2 + CSS |
| Database | SQLite (WAL mode) |
| Bot | python-telegram-bot (polling) |
| Scraping | instaloader (IG) / httpx + BeautifulSoup (generic URLs) |
| Extraction | Claude API |
| Deployment | systemd + nginx on Hetzner VPS |

## Web Dashboard

### Browse (`/`)
Faceted filter sidebar with 7 dimensions (type, subtype, macros, calories, max time, min rating, ingredients) and a recipe card grid with photos, ratings, times, and calorie badges.

### Recipe Detail (`/recipe/{id}`)
Full photo, interactive star rating, type/macro/calorie badges, prep/cook/total times, portions, ingredients list, instructions, cross-linked similar recipes, edit and delete controls.

### Add Recipe (`/add`)
Two paths: paste a URL → auto-fetch + LLM extract → review → save, or manual entry with full form.

## Telegram Bot

- **Fire and forget** — share a link, bot acknowledges, processes in background, notifies when done
- **Auto-extraction** — scrapes post content, sends to Claude API, stores structured recipe
- **Duplicate detection** — same URL shared twice returns the existing recipe
- **Single-user only** — responds only to your Telegram user ID

## Data Model

Each recipe stores:

| Field | Description |
|-------|-------------|
| Title | Auto-generated: dish name + distinguishing feature |
| Type | Sweet / Savory |
| Subtype | Main, dessert, snack, soup, salad, breakfast, side, drink |
| Macros | Tags: protein-rich, low-carb, keto, vegan, gluten-free, etc. |
| Calories | Tier: low (<300), mid (<600), high (≥600) |
| Ingredients | Normalized list |
| Times | Prep, cook, total (minutes) |
| Portions | Servings |
| Instructions | Full steps (markdown) |
| Photo | Snapshot from source post |
| Rating | 1–5 stars |
| Cross-links | Auto-detected similar recipes |

## Deployment

### Prerequisites
- Ubuntu VPS (tested on 26.04 LTS)
- Python 3.12+
- nginx
- Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Claude API key ([Anthropic Console](https://console.anthropic.com/))
- Instagram account for scraping

### Quick Setup

```bash
# Clone the repo
git clone https://github.com/pilomeida/IG-recipes_hub.git /srv/recipe-app
cd /srv/recipe-app

# Configure environment
cp .env.example .env
# Edit .env with your secrets (see below)

# Run setup script
chmod +x deploy/setup.sh
./deploy/setup.sh
```

### Environment Variables (`.env`)

```ini
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ANTHROPIC_API_KEY=sk-ant-your_key
IG_USERNAME=your_ig_username
IG_PASSWORD=your_ig_password
ALLOWED_TELEGRAM_USER_ID=123456789
VPS_IP=your.vps.ip
DATABASE_PATH=data/recipes.db
PHOTOS_DIR=app/static/photos
```

### Service Management

```bash
systemctl status recipe-app    # Check status
systemctl restart recipe-app   # Restart after config changes
journalctl -u recipe-app -f    # Follow logs
```

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
python3 -m pytest tests/ -v

# Run locally (without Telegram bot)
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
