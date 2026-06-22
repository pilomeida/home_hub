# Recipe Recommendation App — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a personal web app that ingests recipe URLs via Telegram bot, extracts structured data with Claude API, and provides a faceted-filter dashboard to decide what to cook.

**Architecture:** Monolithic FastAPI app on SQLite. Telegram bot polls in-process. Dual scraper (instaloader + httpx/BS4) fetches recipe text. Claude API extracts structured JSON. Cross-link detector finds similar recipes by ingredient overlap.

**Tech Stack:** Python 3.12+, FastAPI, SQLite (WAL), SQLModel, Jinja2, `python-telegram-bot`, `instaloader`, `httpx`, `beautifulsoup4`, `anthropic` SDK, uvicorn, nginx, systemd.

## Global Constraints

- Single-user, personal tool — no auth, no multi-tenancy
- SQLite in WAL mode, single writer (FastAPI process)
- IP-only access (port 80), no TLS, no domain
- Telegram bot via polling (not webhook), fire-and-forget flow
- Calorie tiers: <300 = low, <600 = mid, ≥600 = high
- Cross-link threshold: ≥0.6 Jaccard on ingredients OR ≥0.6 Levenshtein on dish name
- Python 3.12+, all deps pinned in requirements.txt
- One extraction at a time (asyncio.Lock)

---

### Task 1: Project skeleton and configuration

**Files:**
- Create: `requirements.txt`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `.env.example`

**Interfaces:**
- Produces: `app.config.Settings` class (pydantic-settings or plain os.environ) exposing `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `IG_USERNAME`, `IG_PASSWORD`, `ALLOWED_TELEGRAM_USER_ID`, `VPS_IP`, `DATABASE_PATH`, `PHOTOS_DIR`

- [ ] **Step 1: Create project directory structure**

```bash
mkdir -p app app/templates app/static/photos app/static/css data deploy
touch app/__init__.py
```

- [ ] **Step 2: Write `requirements.txt`**

```text
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sqlmodel>=0.0.22
python-telegram-bot>=21.3
instaloader>=4.13
httpx>=0.27.0
beautifulsoup4>=4.12.0
anthropic>=0.39.0
python-dotenv>=1.0.0
jinja2>=3.1.0
python-multipart>=0.0.9
aiofiles>=24.0.0
pytest>=8.0.0
pytest-asyncio>=0.24.0
```

- [ ] **Step 3: Write `.env.example`**

```ini
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
ANTHROPIC_API_KEY=sk-ant-your_key_here
IG_USERNAME=your_ig_username
IG_PASSWORD=your_ig_password
ALLOWED_TELEGRAM_USER_ID=123456789
VPS_IP=1.2.3.4
DATABASE_PATH=data/recipes.db
PHOTOS_DIR=app/static/photos
```

- [ ] **Step 4: Write `app/config.py`**

```python
"""Application configuration from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Settings:
    TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
    ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
    IG_USERNAME: str = os.environ["IG_USERNAME"]
    IG_PASSWORD: str = os.environ["IG_PASSWORD"]
    ALLOWED_TELEGRAM_USER_ID: int = int(os.environ["ALLOWED_TELEGRAM_USER_ID"])
    VPS_IP: str = os.environ.get("VPS_IP", "127.0.0.1")
    DATABASE_PATH: Path = Path(os.environ.get("DATABASE_PATH", "data/recipes.db"))
    PHOTOS_DIR: Path = Path(os.environ.get("PHOTOS_DIR", "app/static/photos"))

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.DATABASE_PATH}"


settings = Settings()
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .env.example app/__init__.py app/config.py
git commit -m "feat: project skeleton with config and dependencies"
```

---

### Task 2: Database models

**Files:**
- Create: `app/models.py`
- Create: `app/database.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `app.config.settings` (for database_url)
- Produces: `app.models.Recipe` (SQLModel table), `app.models.CrossLink` (SQLModel table), `app.models.RecipeCreate` (pydantic model for creation), `app.models.RecipeUpdate` (pydantic model for updates), `app.database.init_db()` (creates tables), `app.database.get_session()` (yields SQLModel Session)

- [ ] **Step 1: Write failing tests for models and calorie tier logic**

Create `tests/__init__.py` (empty) and `tests/conftest.py`:

```python
import pytest
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
```

Create `tests/test_models.py`:

```python
import json
import pytest
from sqlmodel import Session, select

from app.models import Recipe, CrossLink, compute_calorie_tier


class TestCalorieTier:
    def test_low_calorie(self):
        assert compute_calorie_tier(150) == "low"
        assert compute_calorie_tier(299) == "low"

    def test_mid_calorie(self):
        assert compute_calorie_tier(300) == "mid"
        assert compute_calorie_tier(599) == "mid"

    def test_high_calorie(self):
        assert compute_calorie_tier(600) == "high"
        assert compute_calorie_tier(1200) == "high"

    def test_null_calorie(self):
        assert compute_calorie_tier(None) is None


class TestRecipe:
    def test_create_recipe(self, db_session):
        recipe = Recipe(
            title="Bolo de Chocolate (whey protein)",
            dish_name="Bolo de Chocolate",
            distinguisher="whey protein",
            type="sweet",
            subtype="dessert",
            calories_per_portion=450,
            macro_tags=json.dumps(["protein-rich", "low-carb"]),
            ingredients=json.dumps(["egg", "flour", "chocolate", "whey protein"]),
            prep_time=15,
            cook_time=45,
            portions=8,
            instructions="1. Mix ingredients\n2. Bake at 180C\n3. Serve",
            source_url="https://www.instagram.com/p/example/",
        )
        db_session.add(recipe)
        db_session.commit()
        db_session.refresh(recipe)

        assert recipe.id is not None
        assert recipe.calorie_tier == "mid"
        assert recipe.total_time == 60
        assert json.loads(recipe.ingredients) == ["egg", "flour", "chocolate", "whey protein"]
        assert json.loads(recipe.macro_tags) == ["protein-rich", "low-carb"]

    def test_recipe_without_cook_time(self, db_session):
        recipe = Recipe(
            title="Salada Caesar (low-cal)",
            dish_name="Salada Caesar",
            distinguisher="low-cal",
            type="savory",
            subtype="salad",
            calories_per_portion=200,
            prep_time=10,
            cook_time=None,
            portions=2,
            source_url="https://example.com/salad",
        )
        db_session.add(recipe)
        db_session.commit()
        db_session.refresh(recipe)

        assert recipe.total_time == 10
        assert recipe.calorie_tier == "low"


class TestCrossLink:
    def test_create_cross_link(self, db_session):
        r1 = Recipe(title="Bolo Choc 1", dish_name="Bolo de Chocolate", type="sweet",
                     source_url="https://ig.com/1")
        r2 = Recipe(title="Bolo Choc 2", dish_name="Bolo de Chocolate", type="sweet",
                     source_url="https://ig.com/2")
        db_session.add_all([r1, r2])
        db_session.commit()
        db_session.refresh(r1)
        db_session.refresh(r2)

        link = CrossLink(recipe_id=r1.id, similar_to_id=r2.id, auto_generated=True)
        db_session.add(link)
        db_session.commit()

        # Query both directions
        stmt = select(CrossLink).where(
            (CrossLink.recipe_id == r1.id) | (CrossLink.similar_to_id == r1.id)
        )
        results = db_session.exec(stmt).all()
        assert len(results) == 1

    def test_cross_link_cascade_delete(self, db_session):
        r1 = Recipe(title="A", dish_name="A", type="sweet", source_url="https://ig.com/a")
        r2 = Recipe(title="B", dish_name="B", type="sweet", source_url="https://ig.com/b")
        db_session.add_all([r1, r2])
        db_session.commit()
        db_session.refresh(r1)
        db_session.refresh(r2)

        link = CrossLink(recipe_id=r1.id, similar_to_id=r2.id)
        db_session.add(link)
        db_session.commit()

        db_session.delete(r1)
        db_session.commit()

        remaining = db_session.exec(select(CrossLink)).all()
        assert len(remaining) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_models.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Write `app/database.py`**

```python
"""Database engine and session management."""

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},  # SQLite single-thread workaround
)


def init_db():
    """Create all tables if they don't exist. Enable WAL mode."""
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def get_session():
    """Yield a database session. Used as FastAPI dependency."""
    with Session(engine) as session:
        yield session
```

- [ ] **Step 4: Write `app/models.py`**

```python
"""SQLModel database models for recipes and cross-links."""

import json
from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


def compute_calorie_tier(calories: Optional[int]) -> Optional[str]:
    """Map calories per portion to a tier bucket."""
    if calories is None:
        return None
    if calories < 300:
        return "low"
    if calories < 600:
        return "mid"
    return "high"


class CrossLink(SQLModel, table=True):
    __tablename__ = "cross_links"

    recipe_id: int = Field(foreign_key="recipes.id", primary_key=True, ondelete="CASCADE")
    similar_to_id: int = Field(foreign_key="recipes.id", primary_key=True, ondelete="CASCADE")
    auto_generated: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    recipe: "Recipe" = Relationship(
        back_populates="links_from",
        sa_relationship_kwargs={"foreign_keys": "[CrossLink.recipe_id]"},
    )
    similar_to: "Recipe" = Relationship(
        back_populates="links_to",
        sa_relationship_kwargs={"foreign_keys": "[CrossLink.similar_to_id]"},
    )


class Recipe(SQLModel, table=True):
    __tablename__ = "recipes"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    dish_name: str
    distinguisher: Optional[str] = None
    type: str  # 'sweet' | 'savory'
    subtype: Optional[str] = None  # 'main'|'dessert'|'snack'|'soup'|'salad'|'breakfast'|'side'|'drink'
    calories_per_portion: Optional[int] = None
    calorie_tier: Optional[str] = None  # computed on save
    macro_tags: str = Field(default="[]")  # JSON array
    ingredients: str = Field(default="[]")  # JSON array
    prep_time: Optional[int] = None
    cook_time: Optional[int] = None
    total_time: Optional[int] = None  # computed on save
    portions: Optional[int] = None
    instructions: Optional[str] = None  # markdown
    photo_path: Optional[str] = None
    source_url: str = Field(unique=True)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    links_from: list[CrossLink] = Relationship(
        back_populates="recipe",
        sa_relationship_kwargs={"foreign_keys": "[CrossLink.recipe_id]", "cascade": "all, delete-orphan"},
    )
    links_to: list[CrossLink] = Relationship(
        back_populates="similar_to",
        sa_relationship_kwargs={"foreign_keys": "[CrossLink.similar_to_id]", "cascade": "all, delete-orphan"},
    )

    def compute_derived_fields(self):
        """Compute calorie_tier and total_time before save."""
        if self.cook_time is not None:
            self.total_time = (self.prep_time or 0) + self.cook_time
        else:
            self.total_time = self.prep_time
        self.calorie_tier = compute_calorie_tier(self.calories_per_portion)

    @property
    def ingredients_list(self) -> list[str]:
        return json.loads(self.ingredients)

    @property
    def macro_tags_list(self) -> list[str]:
        return json.loads(self.macro_tags)

    @property
    def all_linked_ids(self) -> set[int]:
        """Return all recipe IDs linked to this recipe (both directions)."""
        ids = set()
        for link in (self.links_from or []):
            ids.add(link.similar_to_id)
        for link in (self.links_to or []):
            ids.add(link.recipe_id)
        return ids


class RecipeCreate(SQLModel):
    """Input model for creating/updating a recipe (all fields)."""
    title: str
    dish_name: str
    distinguisher: Optional[str] = None
    type: str
    subtype: Optional[str] = None
    calories_per_portion: Optional[int] = None
    macro_tags: str = "[]"
    ingredients: str = "[]"
    prep_time: Optional[int] = None
    cook_time: Optional[int] = None
    portions: Optional[int] = None
    instructions: Optional[str] = None
    photo_path: Optional[str] = None
    source_url: str
    rating: Optional[int] = None


class RecipeUpdate(SQLModel):
    """Input model for partial updates."""
    title: Optional[str] = None
    dish_name: Optional[str] = None
    distinguisher: Optional[str] = None
    type: Optional[str] = None
    subtype: Optional[str] = None
    calories_per_portion: Optional[int] = None
    macro_tags: Optional[str] = None
    ingredients: Optional[str] = None
    prep_time: Optional[int] = None
    cook_time: Optional[int] = None
    portions: Optional[int] = None
    instructions: Optional[str] = None
    photo_path: Optional[str] = None
    rating: Optional[int] = None
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_models.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/database.py tests/test_models.py tests/__init__.py tests/conftest.py
git commit -m "feat: database models with calorie tier and cross-link support"
```

---

### Task 3: LLM Extractor

**Files:**
- Create: `app/extractor.py`
- Test: `tests/test_extractor.py`

**Interfaces:**
- Consumes: `app.config.settings` (for ANTHROPIC_API_KEY)
- Produces: `app.extractor.extract_recipe(text: str, source_url: str) -> dict` — returns a dict with keys matching the LLM response contract. `app.extractor.ExtractionError` exception class.

- [ ] **Step 1: Write failing tests for extractor**

Create `tests/test_extractor.py`:

```python
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.extractor import ExtractionError, extract_recipe


class TestExtractRecipe:
    """Tests that the extractor parses LLM responses correctly.

    These mock the Anthropic API to test our parsing logic.
    """

    FAKE_CAPTION = """
    BOLO DE CHOCOLATE PROTEICO 🍫
    Ingredientes:
    - 3 ovos
    - 1 xícara de farinha de aveia
    - 2 scoops de whey protein chocolate
    - 1/2 xícara de cacau em pó
    - 1 colher de fermento
    Modo de preparo: mistura tudo, assa 30min a 180C.
    Rende 8 porções. Prep: 10min.
    """

    def make_mock_response(self, text_content: str):
        """Helper to create a mock Anthropic message with given text."""
        return type("MockMessage", (), {
            "content": [type("MockBlock", (), {"text": text_content})()]
        })

    @pytest.mark.asyncio
    async def test_extracts_full_recipe(self):
        response_json = {
            "dish_name": "Bolo de Chocolate",
            "distinguishing_feature": "whey protein",
            "type": "sweet",
            "subtype": "dessert",
            "macro_tags": ["protein-rich", "low-carb"],
            "calories_per_portion": 350,
            "ingredients": ["egg", "oat flour", "whey protein", "cocoa powder", "baking powder"],
            "prep_time_minutes": 10,
            "cook_time_minutes": 30,
            "portions": 8,
            "instructions": "1. Mix all ingredients\n2. Bake at 180C for 30min\n3. Serve",
            "missing_critical_info": False,
        }

        mock_msg = self.make_mock_response(json.dumps(response_json))
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_msg)

        with patch("app.extractor.client", mock_client):
            result = await extract_recipe(self.FAKE_CAPTION, "https://ig.com/p/test")

        assert result["dish_name"] == "Bolo de Chocolate"
        assert result["distinguishing_feature"] == "whey protein"
        assert result["type"] == "sweet"
        assert result["calories_per_portion"] == 350
        assert result["prep_time_minutes"] == 10
        assert result["cook_time_minutes"] == 30
        assert result["portions"] == 8
        assert "missing_critical_info" in result

    @pytest.mark.asyncio
    async def test_handles_incomplete_extraction(self):
        response_json = {
            "dish_name": "Salada Misteriosa",
            "distinguishing_feature": None,
            "type": "savory",
            "subtype": "salad",
            "macro_tags": [],
            "calories_per_portion": None,
            "ingredients": [],
            "prep_time_minutes": None,
            "cook_time_minutes": None,
            "portions": None,
            "instructions": None,
            "missing_critical_info": True,
        }

        mock_msg = self.make_mock_response(json.dumps(response_json))
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_msg)

        with patch("app.extractor.client", mock_client):
            result = await extract_recipe("Something unhelpful", "https://ig.com/p/test2")

        assert result["missing_critical_info"] is True
        assert result["dish_name"] == "Salada Misteriosa"

    @pytest.mark.asyncio
    async def test_retries_on_invalid_json(self):
        mock_client = AsyncMock()
        # First call returns garbage, second returns valid JSON
        bad_msg = self.make_mock_response("not valid json at all")
        good_msg = self.make_mock_response(json.dumps({
            "dish_name": "Test", "distinguishing_feature": None, "type": "sweet",
            "subtype": None, "macro_tags": [], "calories_per_portion": None,
            "ingredients": [], "prep_time_minutes": None, "cook_time_minutes": None,
            "portions": None, "instructions": None, "missing_critical_info": True,
        }))
        mock_client.messages.create = AsyncMock(side_effect=[bad_msg, good_msg])

        with patch("app.extractor.client", mock_client):
            result = await extract_recipe("caption", "https://ig.com/p/test3")

        assert result["dish_name"] == "Test"
        assert mock_client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_extraction_error_after_two_failures(self):
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=self.make_mock_response("still not json {{{")
        )

        with patch("app.extractor.client", mock_client):
            with pytest.raises(ExtractionError):
                await extract_recipe("caption", "https://ig.com/p/test4")

        assert mock_client.messages.create.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_extractor.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `app/extractor.py`**

```python
"""LLM-based recipe extraction via Claude API."""

import json
import re
from typing import Any

from anthropic import AsyncAnthropic

from app.config import settings

client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

EXTRACTION_PROMPT = """You are a recipe data extractor. Given the text from a recipe website or Instagram post, extract the following structured information. Return ONLY valid JSON — no commentary, no markdown fences.

Return a JSON object with these keys:
- dish_name: string — the canonical dish name (e.g., "Bolo de Chocolate", "Frango Assado")
- distinguishing_feature: string or null — what makes this version unique (e.g., "whey protein", "low-cal", "air fryer", "vegan")
- type: string — "sweet" or "savory"
- subtype: string or null — one of: "main", "dessert", "snack", "soup", "salad", "breakfast", "side", "drink"
- macro_tags: array of strings — any that apply from: "protein-rich", "low-carb", "keto", "vegan", "gluten-free", "fiber-rich", "high-fat", "dairy-free". Include others that fit.
- calories_per_portion: integer or null — best estimate of calories per serving
- ingredients: array of strings — normalized ingredient names, singular form, lowercase (e.g., "egg" not "eggs", "chicken breast" not "chicken breasts")
- prep_time_minutes: integer or null — preparation time in minutes
- cook_time_minutes: integer or null — cooking time in minutes (null for no-cook dishes)
- portions: integer or null — number of servings
- instructions: string or null — full preparation steps as markdown. Include all steps mentioned.
- missing_critical_info: boolean — true if the text is missing most fields (e.g., no ingredients, no instructions, no dish name identifiable)

Be conservative: if a field isn't clearly stated, use null. Don't guess calories unless mentioned.
Normalize ingredient names: lowercase, singular, no quantities (e.g., "200g of eggs" → "egg").

Text to extract from:
---
{text}
---"""


class ExtractionError(Exception):
    """Raised when LLM extraction fails after retries."""
    pass


async def extract_recipe(text: str, source_url: str) -> dict[str, Any]:
    """Extract structured recipe data from raw text using Claude.

    Args:
        text: Raw scraped text from the recipe source.
        source_url: The original URL (for logging only).

    Returns:
        Dict with keys matching the recipe schema.

    Raises:
        ExtractionError: If extraction fails after retries.
    """
    if not text.strip():
        raise ExtractionError("Empty text provided for extraction")

    prompt = EXTRACTION_PROMPT.format(text=text[:8000])  # truncate for safety

    for attempt in range(2):
        try:
            message = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                temperature=0.1,
                system="You are a precise recipe data extractor. Return only valid JSON.",
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = message.content[0].text.strip()

            # Strip markdown code fences if present
            if response_text.startswith("```"):
                response_text = re.sub(r"^```(?:json)?\s*", "", response_text)
                response_text = re.sub(r"\s*```$", "", response_text)

            data = json.loads(response_text)
            _validate_extraction(data)
            return data

        except (json.JSONDecodeError, KeyError, IndexError):
            if attempt == 1:
                raise ExtractionError(
                    f"Failed to extract valid JSON from LLM response after 2 attempts "
                    f"for {source_url}"
                )
            # Retry with a stricter prompt
            prompt = (
                "The previous response was not valid JSON. "
                "You MUST return ONLY valid JSON, no other text.\n\n"
                + prompt
            )

    raise ExtractionError("Unreachable")  # pragma: no cover


def _validate_extraction(data: dict) -> None:
    """Ensure the extracted dict has the required top-level keys."""
    required_keys = {
        "dish_name", "distinguishing_feature", "type", "subtype",
        "macro_tags", "calories_per_portion", "ingredients",
        "prep_time_minutes", "cook_time_minutes", "portions",
        "instructions", "missing_critical_info",
    }
    missing = required_keys - set(data.keys())
    if missing:
        raise KeyError(f"Missing keys in extraction response: {missing}")
    if data["type"] not in ("sweet", "savory"):
        raise ValueError(f"Invalid type: {data['type']}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_extractor.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/extractor.py tests/test_extractor.py
git commit -m "feat: LLM extraction via Claude API with retry logic"
```

---

### Task 4: Scraper (dual-mode: Instagram + generic URL)

**Files:**
- Create: `app/scraper.py`
- Test: `tests/test_scraper.py`

**Interfaces:**
- Consumes: `app.config.settings` (for IG_USERNAME, IG_PASSWORD, PHOTOS_DIR)
- Produces: `app.scraper.ScrapedContent` (dataclass with `text`, `image_path`, `source_url`), `app.scraper.fetch_content(url: str) -> ScrapedContent`, `app.scraper.ScrapeError` exception class

- [ ] **Step 1: Write failing tests for scraper**

Create `tests/test_scraper.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.scraper import ScrapedContent, ScrapeError, fetch_content


class TestFetchContent:
    def test_routes_instagram_urls(self):
        """Instagram URLs are routed to instaloader path."""
        with patch("app.scraper._fetch_instagram", AsyncMock()) as mock_ig:
            mock_ig.return_value = ScrapedContent(
                text="caption", image_path="/tmp/img.jpg",
                source_url="https://www.instagram.com/p/abc123/",
            )
            with patch("app.scraper._fetch_generic", AsyncMock()) as mock_gen:
                result = fetch_content("https://www.instagram.com/p/abc123/")

        mock_ig.assert_called_once()
        mock_gen.assert_not_called()
        assert result.text == "caption"

    def test_routes_instagram_reel_urls(self):
        """Instagram reel URLs are also routed to instaloader."""
        with patch("app.scraper._fetch_instagram", AsyncMock()) as mock_ig:
            mock_ig.return_value = ScrapedContent(
                text="reel caption", image_path=None,
                source_url="https://www.instagram.com/reel/xyz789/",
            )
            result = fetch_content("https://www.instagram.com/reel/xyz789/")

        assert result.text == "reel caption"

    def test_routes_generic_urls(self):
        """Non-Instagram URLs go to the generic fetcher."""
        with patch("app.scraper._fetch_generic", AsyncMock()) as mock_gen:
            mock_gen.return_value = ScrapedContent(
                text="recipe text", image_path=None,
                source_url="https://example.com/recipe",
            )
            with patch("app.scraper._fetch_instagram", AsyncMock()) as mock_ig:
                result = fetch_content("https://example.com/recipe")

        mock_gen.assert_called_once()
        mock_ig.assert_not_called()
        assert result.text == "recipe text"

    def test_generic_scraper_raises_on_failure(self):
        """Generic scraper raises ScrapeError on HTTP errors."""
        with patch("app.scraper._fetch_generic", AsyncMock()) as mock_gen:
            mock_gen.side_effect = ScrapeError("HTTP 404")
            with pytest.raises(ScrapeError, match="HTTP 404"):
                fetch_content("https://broken.link/recipe")

    def test_instagram_scraper_raises_on_failure(self):
        """Instagram scraper raises ScrapeError on failures."""
        with patch("app.scraper._fetch_instagram", AsyncMock()) as mock_ig:
            mock_ig.side_effect = ScrapeError("Private account")
            with pytest.raises(ScrapeError, match="Private account"):
                fetch_content("https://www.instagram.com/p/private/")


class TestScrapedContent:
    def test_dataclass_fields(self):
        content = ScrapedContent(
            text="some text",
            image_path="/path/to/photo.jpg",
            source_url="https://example.com",
        )
        assert content.text == "some text"
        assert content.image_path == "/path/to/photo.jpg"

    def test_no_image(self):
        content = ScrapedContent(
            text="text only",
            image_path=None,
            source_url="https://example.com",
        )
        assert content.image_path is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_scraper.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `app/scraper.py`**

```python
"""Dual-mode scraper: Instagram via instaloader, generic via httpx+BeautifulSoup."""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import settings


class ScrapeError(Exception):
    """Raised when content cannot be fetched from a URL."""
    pass


@dataclass
class ScrapedContent:
    text: str
    image_path: str | None
    source_url: str


# ── Public API ────────────────────────────────────────────────────────────

def fetch_content(url: str) -> ScrapedContent:
    """Fetch recipe content from any URL.

    Routes Instagram URLs to instaloader, everything else to the generic
    HTTP scraper. This is a synchronous wrapper that delegates to the async
    implementation internally.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().replace("www.", "")

    if host == "instagram.com":
        return _fetch_instagram(url)
    return _fetch_generic(url)


# ── Instagram fetcher ─────────────────────────────────────────────────────

def _fetch_instagram(url: str) -> ScrapedContent:
    """Fetch an Instagram post's caption and image using instaloader."""
    try:
        from instaloader import Instaloader, Post, BadResponseException, QueryReturnedNotFoundException

        loader = Instaloader(
            download_pictures=True,
            download_videos=False,
            download_video_thumbnails=False,
            save_metadata=False,
            compress_json=False,
            quiet=True,
        )

        # Load cached session or login fresh
        try:
            loader.load_session_from_file(settings.IG_USERNAME)
        except FileNotFoundError:
            loader.login(settings.IG_USERNAME, settings.IG_PASSWORD)
            loader.save_session_to_file()

        # Extract shortcode from URL
        match = re.search(r"(?:p|reel)/([A-Za-z0-9_-]+)", url)
        if not match:
            raise ScrapeError(f"Could not parse Instagram shortcode from {url}")

        shortcode = match.group(1)
        post = Post.from_shortcode(loader.context, shortcode)

        caption = post.caption or ""
        if post.caption_hashtags:
            caption += "\n" + " ".join(post.caption_hashtags)

        # Download image
        image_path = None
        photos_dir = Path(settings.PHOTOS_DIR)
        photos_dir.mkdir(parents=True, exist_ok=True)

        if post.is_video:
            # For reels, grab the thumbnail
            if post.url:
                target = photos_dir / f"{shortcode}.jpg"
                loader.download_pic(target, post.url, post.date_utc)
                image_path = str(target)
        else:
            target = photos_dir / shortcode
            loader.download_post(post, target=shortcode)

        return ScrapedContent(
            text=_clean_text(caption),
            image_path=image_path,
            source_url=url,
        )

    except (QueryReturnedNotFoundException, BadResponseException) as e:
        raise ScrapeError(f"Instagram post not found or private: {e}")
    except Exception as e:
        raise ScrapeError(f"Instagram fetch failed: {e}")


# ── Generic URL fetcher ────────────────────────────────────────────────────

def _fetch_generic(url: str) -> ScrapedContent:
    """Fetch and extract text content from any URL."""
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RecipeBot/1.0)"},
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise ScrapeError(f"HTTP error fetching {url}: {e}")

    soup = BeautifulSoup(response.text, "html.parser")

    # Remove non-content elements
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Try to find the main content area
    content = soup.find("article") or soup.find("main") or soup.find("body")
    if content:
        text = content.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    # Try to download the first large image
    image_path = None
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src and not src.startswith("data:"):
            try:
                img_url = src if src.startswith("http") else _resolve_url(url, src)
                img_response = httpx.get(img_url, timeout=10.0)
                img_response.raise_for_status()

                if len(img_response.content) > 5000:  # skip tiny images/icons
                    import hashlib
                    slug = hashlib.md5(url.encode()).hexdigest()[:12]
                    photos_dir = Path(settings.PHOTOS_DIR)
                    photos_dir.mkdir(parents=True, exist_ok=True)
                    ext = _guess_image_ext(img_url, img_response.headers.get("content-type"))
                    dest = photos_dir / f"{slug}{ext}"
                    dest.write_bytes(img_response.content)
                    image_path = str(dest)
                    break
            except Exception:
                continue  # image fetch failures are non-fatal

    return ScrapedContent(
        text=_clean_text(text),
        image_path=image_path,
        source_url=url,
    )


# ── Helpers ────────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Collapse whitespace, strip empty lines."""
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _resolve_url(base_url: str, img_src: str) -> str:
    """Resolve a relative image URL against the page base URL."""
    from urllib.parse import urljoin
    return urljoin(base_url, img_src)


def _guess_image_ext(url: str, content_type: str | None) -> str:
    """Guess file extension from URL or content-type."""
    if content_type:
        if "jpeg" in content_type or "jpg" in content_type:
            return ".jpg"
        if "png" in content_type:
            return ".png"
        if "webp" in content_type:
            return ".webp"
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if path.endswith(ext):
            return ext
    return ".jpg"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_scraper.py -v
```

Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper.py tests/test_scraper.py
git commit -m "feat: dual-mode scraper (instaloader + generic HTTP)"
```

---

### Task 5: Cross-link detector

**Files:**
- Create: `app/linker.py`
- Test: `tests/test_linker.py`

**Interfaces:**
- Consumes: `app.models.Recipe`, `app.models.CrossLink` (SQLModel tables), `sqlmodel.Session`
- Produces: `app.linker.detect_and_link(recipe: Recipe, session: Session) -> int` — returns number of new links created

- [ ] **Step 1: Write failing tests for linker**

Create `tests/test_linker.py`:

```python
import json
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Recipe, CrossLink
from app.linker import detect_and_link, jaccard_similarity


class TestJaccardSimilarity:
    def test_identical_sets(self):
        assert jaccard_similarity({"a", "b", "c"}, {"a", "b", "c"}) == 1.0

    def test_disjoint_sets(self):
        assert jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        result = jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
        assert result == 2 / 4  # intersection=2, union=4

    def test_empty_sets(self):
        assert jaccard_similarity(set(), set()) == 0.0


class TestDetectAndLink:
    @pytest.fixture
    def session(self):
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        with Session(engine) as s:
            yield s

    def _make_recipe(self, session, dish_name, ingredients, type_="sweet", source_url_suffix="a"):
        r = Recipe(
            title=f"{dish_name} (test)",
            dish_name=dish_name,
            type=type_,
            ingredients=json.dumps(ingredients),
            source_url=f"https://ig.com/{source_url_suffix}",
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        return r

    def test_links_similar_ingredients(self, session):
        r1 = self._make_recipe(session, "Bolo de Chocolate",
                               ["egg", "flour", "chocolate", "butter"], source_url_suffix="1")
        r2 = self._make_recipe(session, "Bolo de Chocolate Fitness",
                               ["egg", "flour", "chocolate", "whey"], source_url_suffix="2")

        count = detect_and_link(r2, session)
        assert count == 1

        links = session.exec(
            select(CrossLink).where(
                (CrossLink.recipe_id == r1.id) | (CrossLink.similar_to_id == r1.id)
            )
        ).all()
        assert len(links) == 1
        assert links[0].auto_generated is True

    def test_no_link_for_different_dishes(self, session):
        r1 = self._make_recipe(session, "Bolo de Chocolate",
                               ["egg", "flour", "chocolate"], source_url_suffix="3")
        r2 = self._make_recipe(session, "Frango Assado",
                               ["chicken", "garlic", "salt"], type_="savory", source_url_suffix="4")

        count = detect_and_link(r2, session)
        assert count == 0

    def test_no_link_duplicate(self, session):
        r1 = self._make_recipe(session, "Bolo", ["egg", "flour"], source_url_suffix="5")
        r2 = self._make_recipe(session, "Bolo Fit", ["egg", "flour", "whey"], source_url_suffix="6")

        # First detection
        detect_and_link(r2, session)

        # Second detection should not duplicate
        count = detect_and_link(r2, session)
        assert count == 0

    def test_respects_same_type_only(self, session):
        r1 = self._make_recipe(session, "Panqueca Doce",
                               ["egg", "flour", "sugar"], type_="sweet", source_url_suffix="7")
        r2 = self._make_recipe(session, "Panqueca Salgada",
                               ["egg", "flour", "cheese"], type_="savory", source_url_suffix="8")

        count = detect_and_link(r2, session)
        # Same dish name but different type — should not link
        assert count == 0

    def test_respects_existing_manual_link(self, session):
        r1 = self._make_recipe(session, "Bolo", ["egg", "flour"], source_url_suffix="9")
        r2 = self._make_recipe(session, "Bolo Fit", ["egg", "flour", "whey"], source_url_suffix="10")

        # Create a manual link from r1 to r2
        manual = CrossLink(recipe_id=r1.id, similar_to_id=r2.id, auto_generated=False)
        session.add(manual)
        session.commit()

        # Auto-detection should not create a duplicate
        count = detect_and_link(r2, session)
        assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_linker.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `app/linker.py`**

```python
"""Cross-link detector — finds similar recipes by ingredient and name overlap."""

import json
from difflib import SequenceMatcher

from sqlmodel import Session, or_, select

from app.models import CrossLink, Recipe


def jaccard_similarity(a: set, b: set) -> float:
    """Compute Jaccard similarity between two sets. Returns 0.0 for empty sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _levenshtein_ratio(a: str, b: str) -> float:
    """Wrapper around SequenceMatcher for a float ratio [0.0, 1.0]."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


_THRESHOLD = 0.6


def detect_and_link(recipe: Recipe, session: Session) -> int:
    """Detect similar recipes and create cross-links.

    Compares the given recipe against all existing recipes of the same type.
    Creates links if ingredient Jaccard OR dish-name Levenshtein ≥ 0.6.

    Skips pairs where a link already exists (auto or manual).

    Returns the number of new cross-links created.
    """
    ingredients_a = set(json.loads(recipe.ingredients)) if recipe.ingredients else set()
    name_a = recipe.dish_name or ""

    existing_ids = recipe.all_linked_ids
    existing_ids.add(recipe.id or 0)

    # Find candidates: same type, not already linked
    candidates = session.exec(
        select(Recipe).where(
            Recipe.type == recipe.type,
            Recipe.id.notin_(list(existing_ids)),
        )
    ).all()

    new_count = 0
    for candidate in candidates:
        if candidate.id == recipe.id:
            continue

        ingredients_b = set(json.loads(candidate.ingredients)) if candidate.ingredients else set()
        name_b = candidate.dish_name or ""

        ing_score = jaccard_similarity(ingredients_a, ingredients_b)
        name_score = _levenshtein_ratio(name_a, name_b)

        if ing_score >= _THRESHOLD or name_score >= _THRESHOLD:
            # Bidirectional row
            link = CrossLink(
                recipe_id=recipe.id,
                similar_to_id=candidate.id,
                auto_generated=True,
            )
            session.add(link)
            new_count += 1

    if new_count > 0:
        session.commit()

    return new_count
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_linker.py -v
```

Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add app/linker.py tests/test_linker.py
git commit -m "feat: cross-link detector with Jaccard + Levenshtein similarity"
```

---

### Task 6: FastAPI app shell with routes skeleton

**Files:**
- Create: `app/main.py`
- Modify: `app/config.py` (add optional VPS_IP default handling for URL generation)
- Test: `tests/test_routes.py`

**Interfaces:**
- Consumes: All prior modules
- Produces: `app.main.app` (FastAPI instance), route stubs for `/`, `/recipe/{id}`, `/add`, `/edit/{id}`, health check, static file mount

- [ ] **Step 1: Write failing route smoke tests**

Create `tests/test_routes.py`:

```python
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.main import app
from app.database import get_session


@pytest.fixture
def client():
    """Test client with an in-memory DB."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestRoutes:
    def test_browse_page_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_add_page_returns_200(self, client):
        response = client.get("/add")
        assert response.status_code == 200

    def test_detail_page_404_for_missing(self, client):
        response = client.get("/recipe/999")
        assert response.status_code == 404

    def test_edit_page_404_for_missing(self, client):
        response = client.get("/edit/999")
        assert response.status_code == 404

    def test_detail_page_shows_recipe(self, client):
        from app.models import Recipe
        from app.database import get_session as real_get_session
        # Use the overridden session
        session = next(client.app.dependency_overrides[get_session]())
        recipe = Recipe(
            title="Test Recipe", dish_name="Test", type="sweet",
            source_url="https://example.com/test-route",
        )
        session.add(recipe)
        session.commit()
        session.refresh(recipe)

        response = client.get(f"/recipe/{recipe.id}")
        assert response.status_code == 200
        assert "Test Recipe" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_routes.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `app/main.py`**

```python
"""FastAPI application — routes, startup, dependency wiring."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Start Telegram bot polling in background
    from app.bot import start_bot
    bot_task = asyncio.create_task(start_bot())
    yield
    bot_task.cancel()
    try:
        await bot_task
    except asyncio.CancelledError:
        pass


# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Recipe App",
    version="0.1.0",
    lifespan=lifespan,
)

static_dir = Path("app/static")
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory="app/templates")


def url_for(path: str) -> str:
    """Generate a full URL for Telegram notifications."""
    return f"http://{settings.VPS_IP}{path}"


# ── Imports to register routes ─────────────────────────────────────────────
# (imported at bottom to avoid circular deps — routes use `app` and `templates`)

from app.routes_browse import router as browse_router  # noqa: E402
from app.routes_detail import router as detail_router  # noqa: E402
from app.routes_add import router as add_router        # noqa: E402

app.include_router(browse_router)
app.include_router(detail_router)
app.include_router(add_router)
```

- [ ] **Step 4: Create stub route files (to make imports work)**

Create `app/routes_browse.py`:

```python
from fastapi import APIRouter, Request

router = APIRouter(tags=["browse"])


@router.get("/")
async def browse_page(request: Request):
    from app.main import templates
    return templates.TemplateResponse("browse.html", {"request": request})
```

Create `app/routes_detail.py`:

```python
from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from app.database import get_session
from app.models import Recipe
from app.main import templates


router = APIRouter(tags=["detail"])


@router.get("/recipe/{recipe_id}")
async def recipe_detail(recipe_id: int, request: Request, session: Session = Depends(get_session)):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
    return templates.TemplateResponse(
        "detail.html", {"request": request, "recipe": recipe}
    )
```

Create `app/routes_add.py`:

```python
from fastapi import APIRouter, Request

router = APIRouter(tags=["add"])


@router.get("/add")
async def add_recipe_page(request: Request):
    from app.main import templates
    return templates.TemplateResponse("add.html", {"request": request})
```

- [ ] **Step 5: Create minimal Jinja2 templates**

Create `app/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Recipe App{% endblock %}</title>
    <link rel="stylesheet" href="/static/css/app.css">
</head>
<body>
    <nav>
        <a href="/">📋 Browse</a>
        <a href="/add">➕ Add Recipe</a>
    </nav>
    <main>
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

Create `app/templates/browse.html`:

```html
{% extends "base.html" %}
{% block title %}Browse Recipes{% endblock %}
{% block content %}
<h1>Browse Recipes</h1>
<p>Filter sidebar + recipe grid coming soon.</p>
{% endblock %}
```

Create `app/templates/detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ recipe.title }}{% endblock %}
{% block content %}
<h1>{{ recipe.title }}</h1>
<p>Detail view coming soon.</p>
<a href="/">← Back to browse</a>
{% endblock %}
```

Create `app/templates/add.html`:

```html
{% extends "base.html" %}
{% block title %}Add Recipe{% endblock %}
{% block content %}
<h1>Add Recipe</h1>
<p>Add form coming soon.</p>
{% endblock %}
```

Create `app/templates/404.html`:

```html
{% extends "base.html" %}
{% block title %}Not Found{% endblock %}
{% block content %}
<h1>404 — Recipe Not Found</h1>
<p><a href="/">← Browse recipes</a></p>
{% endblock %}
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
python -m pytest tests/test_routes.py -v
```

Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/routes_browse.py app/routes_detail.py app/routes_add.py \
    app/templates/base.html app/templates/browse.html app/templates/detail.html \
    app/templates/add.html app/templates/404.html tests/test_routes.py
git commit -m "feat: FastAPI app shell with route stubs and base templates"
```

---

### Task 7: Telegram bot handler

**Files:**
- Create: `app/bot.py`
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `app.config.settings` (TELEGRAM_BOT_TOKEN, ALLOWED_TELEGRAM_USER_ID), `app.main.url_for`
- Produces: `app.bot.start_bot()` (async, runs polling loop), `app.bot._extraction_lock` (asyncio.Lock for serialization), `app.bot.handle_message` (processes a URL message end-to-end)

- [ ] **Step 1: Write failing tests for bot message routing**

Create `tests/test_bot.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot import is_url, process_extraction, ExtractionLock


class TestIsUrl:
    def test_instagram_url(self):
        assert is_url("https://www.instagram.com/p/abc123/") is True

    def test_instagram_reel_url(self):
        assert is_url("https://www.instagram.com/reel/xyz789/") is True

    def test_generic_url(self):
        assert is_url("https://example.com/recipe") is True

    def test_not_a_url(self):
        assert is_url("hello world") is False
        assert is_url("not a url at all") is False

    def test_empty_string(self):
        assert is_url("") is False


class TestProcessExtraction:
    @pytest.mark.asyncio
    async def test_successful_extraction(self):
        with patch("app.bot.fetch_content") as mock_fetch:
            mock_fetch.return_value = MagicMock(
                text="recipe text",
                image_path="/tmp/photo.jpg",
                source_url="https://ig.com/p/test",
            )
            with patch("app.bot.extract_recipe", AsyncMock()) as mock_extract:
                mock_extract.return_value = {
                    "dish_name": "Bolo de Chocolate",
                    "distinguishing_feature": "whey protein",
                    "type": "sweet",
                    "subtype": "dessert",
                    "macro_tags": ["protein-rich"],
                    "calories_per_portion": 350,
                    "ingredients": ["egg", "flour", "chocolate"],
                    "prep_time_minutes": 10,
                    "cook_time_minutes": 30,
                    "portions": 8,
                    "instructions": "Mix and bake",
                    "missing_critical_info": False,
                }
                with patch("app.bot.detect_and_link") as mock_link:
                    mock_link.return_value = 1

                    with patch("app.bot.url_for") as mock_url:
                        mock_url.return_value = "http://1.2.3.4/recipe/1"

                        result = await process_extraction("https://ig.com/p/test")

        assert result is not None
        assert result["title"] == "Bolo de Chocolate (whey protein)"
        assert result["calorie_tier"] == "mid"

    @pytest.mark.asyncio
    async def test_scrape_failure_returns_none(self):
        with patch("app.bot.fetch_content") as mock_fetch:
            mock_fetch.side_effect = Exception("Scrape failed")

            result = await process_extraction("https://broken.link")

        assert result is None

    @pytest.mark.asyncio
    async def test_extraction_failure_returns_none(self):
        with patch("app.bot.fetch_content") as mock_fetch:
            mock_fetch.return_value = MagicMock(
                text="some text", image_path=None,
                source_url="https://example.com",
            )
            with patch("app.bot.extract_recipe", AsyncMock()) as mock_extract:
                from app.extractor import ExtractionError
                mock_extract.side_effect = ExtractionError("LLM failed")

                result = await process_extraction("https://example.com/recipe")

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_extraction_text_returns_none(self):
        """process_extraction raises ExtractionError on empty scraper text."""
        with patch("app.bot.fetch_content") as mock_fetch:
            mock_fetch.return_value = MagicMock(
                text="", image_path=None, source_url="https://broken.link",
            )
            with patch("app.bot.extract_recipe", AsyncMock()) as mock_extract:
                from app.extractor import ExtractionError
                mock_extract.side_effect = ExtractionError("Empty text")

                result = await process_extraction("https://broken.link")
                assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_bot.py::TestIsUrl -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `app/bot.py`**

```python
"""Telegram bot handler — polling, message routing, extraction pipeline call."""

import asyncio
import json
import re
import traceback
from datetime import datetime

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from app.config import settings
from app.database import get_session
from app.models import Recipe, compute_calorie_tier
from app.scraper import fetch_content, ScrapeError
from app.extractor import extract_recipe, ExtractionError
from app.linker import detect_and_link


# ── URL detection ─────────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def is_url(text: str) -> bool:
    """Check if a message text contains a URL."""
    return bool(text) and bool(_URL_RE.search(text))


# ── Extraction lock ────────────────────────────────────────────────────────

_extraction_lock = asyncio.Lock()


# ── Bot application ────────────────────────────────────────────────────────

_tg_app: Application | None = None


async def start_bot():
    """Start the Telegram bot polling loop. Called from FastAPI lifespan."""
    global _tg_app
    _tg_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    _tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Also handle captions (text accompanying media)
    _tg_app.add_handler(MessageHandler(filters.CAPTION & ~filters.COMMAND, handle_message))
    await _tg_app.initialize()
    await _tg_app.start()
    await _tg_app.updater.start_polling()
    # Keep running until cancelled
    while True:
        await asyncio.sleep(3600)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process incoming Telegram messages."""
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id if update.effective_user else 0
    if user_id != settings.ALLOWED_TELEGRAM_USER_ID:
        return  # silently ignore non-owner

    text = update.message.text.strip()

    if not is_url(text):
        await update.message.reply_text("Send me a recipe link! 📎")
        return

    # Extract the URL
    url = _URL_RE.search(text).group(0)

    # Check for duplicate
    with next(get_session()) as session:
        existing = session.query(Recipe).filter_by(source_url=url).first()
        if existing:
            from app.main import url_for
            await update.message.reply_text(
                f"📋 That recipe is already saved:\n"
                f"**{existing.title}**\n"
                f"{url_for(f'/recipe/{existing.id}')}"
            )
            return

    # Try to acquire lock; if busy, tell user to wait
    if _extraction_lock.locked():
        await update.message.reply_text("⏳ Still processing your previous link, one moment...")
        return

    await update.message.reply_text("👨‍🍳 Got it! Extracting recipe...")

    success = False
    async with _extraction_lock:
        try:
            result = await process_extraction(url)
            if result:
                await update.message.reply_text(_success_message(result))
                success = True
            else:
                await update.message.reply_text(
                    "⚠️ Extraction failed for that link. "
                    f"You can add it manually at http://{settings.VPS_IP}/add"
                )
        except ScrapeError:
            await update.message.reply_text(
                "⚠️ Couldn't extract that one — the post may be private, deleted, or rate-limited."
            )
        except Exception:
            traceback.print_exc()
            await update.message.reply_text(
                "⚠️ Something went wrong. Try again or add manually."
            )

    return success


# ── Extraction pipeline ────────────────────────────────────────────────────

async def process_extraction(url: str) -> dict | None:
    """Fetch, extract, store, and link a recipe from a URL.

    Returns a dict with recipe data on success, None on failure.
    """
    from app.main import url_for

    # 1. Scrape
    try:
        content = fetch_content(url)
    except Exception:
        return None

    # 2. LLM extract
    try:
        data = await extract_recipe(content.text, url)
    except ExtractionError:
        return None

    # 3. Build title
    dish_name = data.get("dish_name") or "Unknown"
    distinguisher = data.get("distinguishing_feature")
    title = f"{dish_name} ({distinguisher})" if distinguisher else dish_name

    # 4. Insert into DB
    recipe = Recipe(
        title=title,
        dish_name=dish_name,
        distinguisher=distinguisher,
        type=data.get("type", "sweet"),
        subtype=data.get("subtype"),
        calories_per_portion=data.get("calories_per_portion"),
        macro_tags=json.dumps(data.get("macro_tags") or []),
        ingredients=json.dumps(data.get("ingredients") or []),
        prep_time=data.get("prep_time_minutes"),
        cook_time=data.get("cook_time_minutes"),
        portions=data.get("portions"),
        instructions=data.get("instructions"),
        photo_path=content.image_path,
        source_url=url,
    )
    recipe.compute_derived_fields()

    with next(get_session()) as session:
        session.add(recipe)
        session.commit()
        session.refresh(recipe)

        # 5. Cross-link
        detect_and_link(recipe, session)

        recipe_id = recipe.id
        return {
            "title": recipe.title,
            "id": recipe_id,
            "calorie_tier": recipe.calorie_tier,
            "macro_tags": json.loads(recipe.macro_tags),
            "total_time": recipe.total_time,
            "url": url_for(f"/recipe/{recipe_id}"),
        }


def _success_message(result: dict) -> str:
    """Format the success notification for Telegram."""
    tier = result.get("calorie_tier", "?")
    tier_emoji = {"low": "🟢", "mid": "🟡", "high": "🔴"}.get(tier, "⚪")
    tags = ", ".join(result.get("macro_tags", [])[:2])
    time_str = f"{result.get('total_time', '?')}min" if result.get("total_time") else "? min"

    lines = [
        f"✅ **{result['title']}** added!",
        f"⏱ {time_str} · {tier_emoji} {tier} cal",
    ]
    if tags:
        lines.append(f"🏷 {tags}")
    lines.append(f"📋 View: {result['url']}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_bot.py -v
```

Expected: PASS (tests for is_url and process_extraction)

- [ ] **Step 5: Commit**

```bash
git add app/bot.py tests/test_bot.py
git commit -m "feat: Telegram bot with URL routing and extraction pipeline"
```

---

### Task 8: Browse page (faceted filter + recipe grid)

**Files:**
- Modify: `app/routes_browse.py`
- Modify: `app/templates/browse.html`
- Test: `tests/test_browse.py`

**Interfaces:**
- Consumes: `app.models.Recipe`, `app.database.get_session`
- Produces: `GET /` with query params filtering, template rendering with filter lists + recipe cards

- [ ] **Step 1: Write browse page tests**

Create `tests/test_browse.py`:

```python
import json
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.main import app
from app.database import get_session
from app.models import Recipe


@pytest.fixture
def client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed_recipes(client):
    """Add sample recipes to the test DB."""
    session = next(client.app.dependency_overrides[get_session]())
    recipes = [
        Recipe(title="Bolo de Chocolate (whey)", dish_name="Bolo de Chocolate",
               distinguisher="whey", type="sweet", subtype="dessert",
               calories_per_portion=350, calorie_tier="mid",
               macro_tags=json.dumps(["protein-rich"]),
               ingredients=json.dumps(["egg", "flour", "chocolate", "whey"]),
               prep_time=10, cook_time=30, total_time=40,
               source_url="https://ig.com/1"),
        Recipe(title="Frango Grelhado (keto)", dish_name="Frango Grelhado",
               distinguisher="keto", type="savory", subtype="main",
               calories_per_portion=500, calorie_tier="mid",
               macro_tags=json.dumps(["protein-rich", "keto"]),
               ingredients=json.dumps(["chicken", "garlic", "olive oil"]),
               prep_time=10, cook_time=20, total_time=30,
               source_url="https://ig.com/2"),
        Recipe(title="Salada Caesar (low-cal)", dish_name="Salada Caesar",
               distinguisher="low-cal", type="savory", subtype="salad",
               calories_per_portion=180, calorie_tier="low",
               macro_tags=json.dumps(["fiber-rich"]),
               ingredients=json.dumps(["lettuce", "chicken", "parmesan"]),
               prep_time=15, cook_time=None, total_time=15,
               source_url="https://ig.com/3"),
    ]
    session.add_all(recipes)
    session.commit()


class TestBrowsePage:
    def test_shows_all_recipes(self, client):
        _seed_recipes(client)
        response = client.get("/")
        assert response.status_code == 200
        assert "Bolo de Chocolate" in response.text
        assert "Frango Grelhado" in response.text
        assert "Salada Caesar" in response.text

    def test_filter_by_type(self, client):
        _seed_recipes(client)
        response = client.get("/?type=sweet")
        assert "Bolo de Chocolate" in response.text
        assert "Frango Grelhado" not in response.text
        assert "Salada Caesar" not in response.text

    def test_filter_by_calorie_tier(self, client):
        _seed_recipes(client)
        response = client.get("/?calorie_tier=low")
        assert "Salada Caesar" in response.text
        assert "Bolo de Chocolate" not in response.text

    def test_filter_by_subtype(self, client):
        _seed_recipes(client)
        response = client.get("/?subtype=main&subtype=salad")
        assert "Frango Grelhado" in response.text
        assert "Salada Caesar" in response.text
        assert "Bolo de Chocolate" not in response.text

    def test_filter_by_macro(self, client):
        _seed_recipes(client)
        response = client.get("/?macro=protein-rich")
        assert "Bolo de Chocolate" in response.text
        assert "Frango Grelhado" in response.text
        assert "Salada Caesar" not in response.text

    def test_filter_by_ingredient(self, client):
        _seed_recipes(client)
        response = client.get("/?ingredient=chicken")
        assert "Frango Grelhado" in response.text
        assert "Salada Caesar" in response.text
        assert "Bolo de Chocolate" not in response.text

    def test_filter_by_max_time(self, client):
        _seed_recipes(client)
        response = client.get("/?max_time=20")
        assert "Salada Caesar" in response.text     # 15 min
        assert "Frango Grelhado" not in response.text  # 30 min
        assert "Bolo de Chocolate" not in response.text  # 40 min

    def test_filter_by_min_rating(self, client):
        _seed_recipes(client)
        # Set rating on one recipe
        session = next(client.app.dependency_overrides[get_session]())
        recipe = session.query(Recipe).filter_by(dish_name="Bolo de Chocolate").first()
        recipe.rating = 4
        session.commit()

        response = client.get("/?min_rating=4")
        assert "Bolo de Chocolate" in response.text
        assert "Frango Grelhado" not in response.text

    def test_combined_filters(self, client):
        _seed_recipes(client)
        response = client.get("/?type=savory&calorie_tier=low")
        assert "Salada Caesar" in response.text
        assert "Frango Grelhado" not in response.text

    def test_sidebar_includes_available_filters(self, client):
        _seed_recipes(client)
        response = client.get("/")
        # Available types
        assert "sweet" in response.text.lower()
        assert "savory" in response.text.lower()
        # Available ingredients
        assert "chicken" in response.text.lower()
        assert "chocolate" in response.text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_browse.py -v
```

Expected: FAIL — template rendering wrong / tests assert on content not yet in template

- [ ] **Step 3: Write `app/routes_browse.py` (full implementation)**

```python
"""Browse route — faceted filter sidebar + recipe grid."""

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session, select

from app.database import get_session
from app.models import Recipe
from app.main import templates, url_for

router = APIRouter(tags=["browse"])


@router.get("/")
async def browse_page(
    request: Request,
    type: Optional[list[str]] = Query(default=None, alias="type"),
    subtype: Optional[list[str]] = Query(default=None, alias="subtype"),
    macro: Optional[list[str]] = Query(default=None, alias="macro"),
    calorie_tier: Optional[list[str]] = Query(default=None, alias="calorie_tier"),
    ingredient: Optional[list[str]] = Query(default=None, alias="ingredient"),
    max_time: Optional[int] = Query(default=None, alias="max_time"),
    min_rating: Optional[int] = Query(default=None, alias="min_rating", ge=1, le=5),
    session: Session = Depends(get_session),
):
    # Build base query
    query = select(Recipe)

    if type:
        query = query.where(Recipe.type.in_(type))
    if subtype:
        query = query.where(Recipe.subtype.in_(subtype))
    if calorie_tier:
        query = query.where(Recipe.calorie_tier.in_(calorie_tier))
    if max_time is not None:
        query = query.where(Recipe.total_time <= max_time)
    if min_rating is not None:
        query = query.where(Recipe.rating >= min_rating)

    recipes = session.exec(query.order_by(Recipe.created_at.desc())).all()

    # Post-query filtering (JSON fields)
    if macro:
        recipes = [r for r in recipes if any(m in r.macro_tags_list for m in macro)]
    if ingredient:
        recipes = [r for r in recipes if all(ing in r.ingredients_list for ing in ingredient)]

    # Build filter option lists from ALL recipes in DB (not just filtered)
    all_recipes = session.exec(select(Recipe)).all()
    all_types: set[str] = set()
    all_subtypes: set[str] = set()
    all_macros: set[str] = set()
    all_ingredients: set[str] = set()
    all_tiers: set[str] = set()

    for r in all_recipes:
        all_types.add(r.type)
        if r.subtype:
            all_subtypes.add(r.subtype)
        for m in r.macro_tags_list:
            all_macros.add(m)
        for ing in r.ingredients_list:
            all_ingredients.add(ing)
        if r.calorie_tier:
            all_tiers.add(r.calorie_tier)

    return templates.TemplateResponse("browse.html", {
        "request": request,
        "recipes": recipes,
        "filters": {
            "types": sorted(all_types),
            "subtypes": sorted(all_subtypes),
            "macros": sorted(all_macros),
            "ingredients": sorted(all_ingredients),
            "calorie_tiers": sorted(all_tiers),
        },
        "active": {
            "type": type or [],
            "subtype": subtype or [],
            "macro": macro or [],
            "calorie_tier": calorie_tier or [],
            "ingredient": ingredient or [],
            "max_time": max_time,
            "min_rating": min_rating,
        },
    })
```

- [ ] **Step 4: Write `app/templates/browse.html` (full template)**

```html
{% extends "base.html" %}
{% block title %}Browse Recipes{% endblock %}
{% block content %}
<div class="browse-layout">
    <aside class="filter-sidebar">
        <h2>Filters</h2>

        {% if filters.types %}
        <fieldset class="filter-group">
            <legend>Type</legend>
            {% for t in filters.types %}
            <label class="filter-checkbox {% if t in active.type %}checked{% endif %}">
                <input type="checkbox" name="type" value="{{ t }}"
                       {% if t in active.type %}checked{% endif %}
                       onchange="applyFilters()">
                {{ t.capitalize() }}
            </label>
            {% endfor %}
        </fieldset>
        {% endif %}

        {% if filters.subtypes %}
        <fieldset class="filter-group">
            <legend>Subtype</legend>
            {% for s in filters.subtypes %}
            <label class="filter-checkbox {% if s in active.subtype %}checked{% endif %}">
                <input type="checkbox" name="subtype" value="{{ s }}"
                       {% if s in active.subtype %}checked{% endif %}
                       onchange="applyFilters()">
                {{ s.capitalize() }}
            </label>
            {% endfor %}
        </fieldset>
        {% endif %}

        {% if filters.ingredients %}
        <fieldset class="filter-group scrollable">
            <legend>Ingredients</legend>
            {% for ing in filters.ingredients %}
            <label class="filter-checkbox {% if ing in active.ingredient %}checked{% endif %}">
                <input type="checkbox" name="ingredient" value="{{ ing }}"
                       {% if ing in active.ingredient %}checked{% endif %}
                       onchange="applyFilters()">
                {{ ing }}
            </label>
            {% endfor %}
        </fieldset>
        {% endif %}

        {% if filters.macros %}
        <fieldset class="filter-group">
            <legend>Macros</legend>
            {% for m in filters.macros %}
            <label class="filter-checkbox {% if m in active.macro %}checked{% endif %}">
                <input type="checkbox" name="macro" value="{{ m }}"
                       {% if m in active.macro %}checked{% endif %}
                       onchange="applyFilters()">
                {{ m }}
            </label>
            {% endfor %}
        </fieldset>
        {% endif %}

        {% if filters.calorie_tiers %}
        <fieldset class="filter-group">
            <legend>Calories</legend>
            {% for ct in filters.calorie_tiers %}
            <label class="filter-checkbox {% if ct in active.calorie_tier %}checked{% endif %}">
                <input type="checkbox" name="calorie_tier" value="{{ ct }}"
                       {% if ct in active.calorie_tier %}checked{% endif %}
                       onchange="applyFilters()">
                {{ ct.capitalize() }}
            </label>
            {% endfor %}
        </fieldset>
        {% endif %}

        <fieldset class="filter-group">
            <legend>Max Time (min)</legend>
            <input type="range" name="max_time" min="0" max="180" step="5"
                   value="{{ active.max_time if active.max_time else 180 }}"
                   oninput="this.nextElementSibling.value = this.value; applyFilters()">
            <output>{{ active.max_time if active.max_time else 180 }}</output>
        </fieldset>

        <fieldset class="filter-group">
            <legend>Min Rating</legend>
            <div class="star-filter">
                {% for star in [1, 2, 3, 4, 5] %}
                <button type="button" class="star-btn {% if active.min_rating and star <= active.min_rating %}active{% endif %}"
                        onclick="setMinRating({{ star }})"
                        title="{{ star }} star{{ 's' if star > 1 else '' }}">
                    {{ '★' if (active.min_rating and star <= active.min_rating) else '☆' }}
                </button>
                {% endfor %}
                {% if active.min_rating %}
                <button type="button" class="star-clear" onclick="setMinRating(null)">✕</button>
                {% endif %}
            </div>
        </fieldset>

        <a href="/" class="clear-filters">Clear Filters</a>
    </aside>

    <section class="recipe-grid">
        {% if recipes %}
            {% for recipe in recipes %}
            <a href="/recipe/{{ recipe.id }}" class="recipe-card">
                <div class="card-image">
                    {% if recipe.photo_path %}
                    {% set filename = recipe.photo_path.split('/')[-1] %}
                    <img src="/static/photos/{{ filename }}" alt="{{ recipe.title }}" loading="lazy">
                    {% else %}
                    <div class="no-photo">📷</div>
                    {% endif %}
                </div>
                <div class="card-body">
                    <h3 class="card-title">{{ recipe.title }}</h3>
                    <div class="card-meta">
                        {% if recipe.rating %}
                        <span class="stars">{{ '★' * recipe.rating }}{{ '☆' * (5 - recipe.rating) }}</span>
                        {% endif %}
                        {% if recipe.total_time %}
                        <span class="time">⏱ {{ recipe.total_time }}m</span>
                        {% endif %}
                        {% if recipe.calorie_tier %}
                        <span class="tier tier-{{ recipe.calorie_tier }}">{{ recipe.calorie_tier }}</span>
                        {% endif %}
                    </div>
                </div>
            </a>
            {% endfor %}
        {% else %}
            <div class="empty-state">
                <p>No recipes match your filters. <a href="/">Clear filters</a></p>
            </div>
        {% endif %}
    </section>
</div>

<script>
function applyFilters() {
    const form = document.querySelector('.filter-sidebar');
    const params = new URLSearchParams();

    form.querySelectorAll('input[type=checkbox]:checked').forEach(cb => {
        params.append(cb.name, cb.value);
    });

    const range = form.querySelector('input[type=range]');
    if (range && parseInt(range.value) < 180) {
        params.set('max_time', range.value);
    }

    {% if active.min_rating %}
    params.set('min_rating', '{{ active.min_rating }}');
    {% endif %}

    window.location.search = params.toString();
}

function setMinRating(star) {
    const params = new URLSearchParams(window.location.search);
    if (star === null) {
        params.delete('min_rating');
    } else {
        params.set('min_rating', star);
    }
    window.location.search = params.toString();
}
</script>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_browse.py -v
```

Expected: PASS (10 tests)

- [ ] **Step 6: Commit**

```bash
git add app/routes_browse.py app/templates/browse.html tests/test_browse.py
git commit -m "feat: browse page with faceted filter sidebar and recipe grid"
```

---

### Task 9: Recipe detail page (view, rate, delete, cross-links)

**Files:**
- Modify: `app/routes_detail.py` (full implementation)
- Modify: `app/templates/detail.html`
- Test: `tests/test_detail.py`

- [ ] **Step 1: Write detail page tests**

Create `tests/test_detail.py`:

```python
import json
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.main import app
from app.database import get_session
from app.models import Recipe, CrossLink


@pytest.fixture
def client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed(client):
    session = next(client.app.dependency_overrides[get_session]())
    r1 = Recipe(title="Bolo de Chocolate (whey)", dish_name="Bolo de Chocolate",
                distinguisher="whey", type="sweet", subtype="dessert",
                calories_per_portion=350, calorie_tier="mid",
                macro_tags=json.dumps(["protein-rich", "low-carb"]),
                ingredients=json.dumps(["egg", "flour", "chocolate", "whey"]),
                prep_time=10, cook_time=30, total_time=40,
                portions=8, instructions="1. Mix\n2. Bake",
                source_url="https://ig.com/1")
    r2 = Recipe(title="Bolo de Chocolate (low-cal)", dish_name="Bolo de Chocolate",
                distinguisher="low-cal", type="sweet", subtype="dessert",
                calories_per_portion=200, calorie_tier="low",
                ingredients=json.dumps(["egg", "flour", "chocolate", "stevia"]),
                prep_time=10, cook_time=30, total_time=40,
                source_url="https://ig.com/2")
    session.add_all([r1, r2])
    session.commit()
    session.refresh(r1)
    session.refresh(r2)
    # Add cross-link
    link = CrossLink(recipe_id=r1.id, similar_to_id=r2.id, auto_generated=True)
    session.add(link)
    session.commit()
    return r1, r2


class TestDetailPage:
    def test_shows_recipe(self, client):
        r1, _ = _seed(client)
        response = client.get(f"/recipe/{r1.id}")
        assert response.status_code == 200
        assert "Bolo de Chocolate" in response.text
        assert "whey" in response.text
        assert "protein-rich" in response.text

    def test_shows_cross_links(self, client):
        r1, r2 = _seed(client)
        response = client.get(f"/recipe/{r1.id}")
        assert response.status_code == 200
        assert f"/recipe/{r2.id}" in response.text
        assert "low-cal" in response.text

    def test_delete_recipe(self, client):
        r1, _ = _seed(client)
        response = client.post(f"/recipe/{r1.id}/delete")
        assert response.status_code == 302  # redirect to /
        # Verify deleted
        session = next(client.app.dependency_overrides[get_session]())
        assert session.get(Recipe, r1.id) is None

    def test_update_rating(self, client):
        r1, _ = _seed(client)
        response = client.post(f"/recipe/{r1.id}/rate", json={"rating": 4})
        assert response.status_code == 200
        session = next(client.app.dependency_overrides[get_session]())
        updated = session.get(Recipe, r1.id)
        assert updated.rating == 4
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_detail.py -v
```

Expected: FAIL — routes not yet implemented

- [ ] **Step 3: Write `app/routes_detail.py` (full implementation)**

```python
"""Recipe detail route — view, rate, delete, cross-links."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session, or_, select

from app.database import get_session
from app.models import Recipe, CrossLink
from app.main import templates

router = APIRouter(tags=["detail"])


class RatingUpdate(BaseModel):
    rating: int


@router.get("/recipe/{recipe_id}")
async def recipe_detail(
    recipe_id: int, request: Request, session: Session = Depends(get_session)
):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

    # Fetch linked recipes
    linked_ids = recipe.all_linked_ids
    linked_recipes = []
    if linked_ids:
        linked_recipes = session.exec(
            select(Recipe).where(Recipe.id.in_(list(linked_ids)))
        ).all()

    return templates.TemplateResponse("detail.html", {
        "request": request,
        "recipe": recipe,
        "linked_recipes": linked_recipes,
    })


@router.post("/recipe/{recipe_id}/rate")
async def rate_recipe(
    recipe_id: int,
    body: RatingUpdate,
    session: Session = Depends(get_session),
):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        return {"ok": False}
    from datetime import datetime
    recipe.rating = body.rating
    recipe.updated_at = datetime.utcnow()
    session.commit()
    return {"ok": True}


@router.post("/recipe/{recipe_id}/delete")
async def delete_recipe(recipe_id: int, session: Session = Depends(get_session)):
    recipe = session.get(Recipe, recipe_id)
    if recipe:
        session.delete(recipe)
        session.commit()
    return RedirectResponse(url="/", status_code=302)
```

- [ ] **Step 4: Write `app/templates/detail.html` (full template)**

```html
{% extends "base.html" %}
{% block title %}{{ recipe.title }}{% endblock %}
{% block content %}
<article class="recipe-detail">
    <div class="detail-header">
        <div class="detail-photo">
            {% if recipe.photo_path %}
            {% set filename = recipe.photo_path.split('/')[-1] %}
            <img src="/static/photos/{{ filename }}" alt="{{ recipe.title }}">
            {% else %}
            <div class="no-photo large">📷 No photo</div>
            {% endif %}
        </div>

        <div class="detail-info">
            <h1>{{ recipe.title }}</h1>
            {% if recipe.distinguisher %}
            <p class="distinguisher">{{ recipe.distinguisher }}</p>
            {% endif %}

            <div class="rating" id="rating-widget">
                {% for star in [1, 2, 3, 4, 5] %}
                <button class="star-btn {% if recipe.rating and star <= recipe.rating %}active{% endif %}"
                        onclick="rateRecipe({{ star }})"
                        title="{{ star }} star{{ 's' if star > 1 else '' }}">
                    {{ '★' if recipe.rating and star <= recipe.rating else '☆' }}
                </button>
                {% endfor %}
                <span id="rating-label">
                    {% if recipe.rating %}{{ recipe.rating }}/5{% else %}Not rated{% endif %}
                </span>
            </div>

            <div class="badges">
                <span class="badge type-{{ recipe.type }}">{{ recipe.type }}</span>
                {% if recipe.subtype %}
                <span class="badge subtype">{{ recipe.subtype }}</span>
                {% endif %}
                {% if recipe.calorie_tier %}
                <span class="badge tier-{{ recipe.calorie_tier }}">{{ recipe.calorie_tier }} cal</span>
                {% endif %}
                {% for tag in recipe.macro_tags_list %}
                <span class="badge macro">{{ tag }}</span>
                {% endfor %}
            </div>

            <div class="times">
                <div><strong>Prep:</strong> {{ recipe.prep_time }}m</div>
                <div><strong>Cook:</strong> {{ recipe.cook_time if recipe.cook_time else '—' }}m</div>
                <div><strong>Total:</strong> {{ recipe.total_time }}m</div>
                {% if recipe.portions %}
                <div><strong>Portions:</strong> {{ recipe.portions }}</div>
                {% endif %}
            </div>

            {% if recipe.source_url %}
            <a href="{{ recipe.source_url }}" target="_blank" rel="noopener" class="source-link">
                🔗 View original post
            </a>
            {% endif %}
        </div>
    </div>

    {% if recipe.ingredients_list %}
    <section class="detail-section">
        <h2>Ingredients</h2>
        <ul class="ingredients-list">
            {% for ing in recipe.ingredients_list %}
            <li>{{ ing }}</li>
            {% endfor %}
        </ul>
    </section>
    {% endif %}

    {% if recipe.instructions %}
    <section class="detail-section">
        <h2>Instructions</h2>
        <div class="instructions-text">{{ recipe.instructions }}</div>
    </section>
    {% endif %}

    {% if linked_recipes %}
    <section class="detail-section">
        <h2>Similar Recipes</h2>
        <div class="linked-cards">
            {% for linked in linked_recipes %}
            <a href="/recipe/{{ linked.id }}" class="recipe-card small">
                <div class="card-body">
                    <h4>{{ linked.title }}</h4>
                    <div class="card-meta">
                        {% if linked.calorie_tier %}
                        <span class="tier tier-{{ linked.calorie_tier }}">{{ linked.calorie_tier }}</span>
                        {% endif %}
                    </div>
                </div>
            </a>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    <div class="detail-actions">
        <button onclick="confirmDelete()" class="btn btn-danger">🗑 Delete Recipe</button>
    </div>
</article>

<script>
function confirmDelete() {
    if (confirm("Delete this recipe? This cannot be undone.")) {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/recipe/{{ recipe.id }}/delete';
        document.body.appendChild(form);
        form.submit();
    }
}

function rateRecipe(star) {
    fetch('/recipe/{{ recipe.id }}/rate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({rating: star})
    }).then(r => r.json()).then(data => {
        if (data.ok) {
            location.reload();
        }
    });
}
</script>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_detail.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add app/routes_detail.py app/templates/detail.html tests/test_detail.py
git commit -m "feat: recipe detail page with rating, delete, and cross-links"
```

---

### Task 10: Add / Edit recipe pages

**Files:**
- Modify: `app/routes_add.py` (full implementation with GET + POST, URL fetch, manual save)
- Modify: `app/templates/add.html`
- Test: `tests/test_add.py`

- [ ] **Step 1: Write add page tests**

Create `tests/test_add.py`:

```python
import json
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import app
from app.database import get_session
from app.models import Recipe


@pytest.fixture
def client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestAddPage:
    def test_get_add_page(self, client):
        response = client.get("/add")
        assert response.status_code == 200
        assert "Add Recipe" in response.text

    def test_manual_save(self, client):
        form_data = {
            "title": "Panqueca Proteica",
            "dish_name": "Panqueca",
            "distinguisher": "proteica",
            "type": "sweet",
            "subtype": "breakfast",
            "calories_per_portion": "300",
            "macro_tags": '["protein-rich"]',
            "ingredients": '["egg", "whey", "banana"]',
            "prep_time": "5",
            "cook_time": "10",
            "portions": "2",
            "instructions": "Mix and cook",
            "source_url": "https://example.com/panqueca",
        }
        response = client.post("/add", data=form_data)
        assert response.status_code == 302  # redirect to detail

        # Verify in DB
        session = next(client.app.dependency_overrides[get_session]())
        recipe = session.exec(select(Recipe).where(Recipe.source_url == "https://example.com/panqueca")).first()
        assert recipe is not None
        assert recipe.title == "Panqueca Proteica"
        assert recipe.total_time == 15
        assert recipe.calorie_tier == "mid"

    def test_duplicate_url_rejected(self, client):
        # Insert first
        client.post("/add", data={
            "title": "Test", "dish_name": "Test", "type": "sweet",
            "source_url": "https://example.com/dup",
        })
        # Try duplicate
        response = client.post("/add", data={
            "title": "Test 2", "dish_name": "Test", "type": "sweet",
            "source_url": "https://example.com/dup",
        })
        assert response.status_code == 200  # stays on add page with error
        assert "already exists" in response.text.lower()


class TestEditPage:
    def test_get_edit_page(self, client):
        session = next(client.app.dependency_overrides[get_session]())
        r = Recipe(title="Edit Me", dish_name="Edit", type="sweet", source_url="https://ig.com/edit1")
        session.add(r)
        session.commit()
        session.refresh(r)

        response = client.get(f"/edit/{r.id}")
        assert response.status_code == 200
        assert "Edit Me" in response.text

    def test_edit_save(self, client):
        session = next(client.app.dependency_overrides[get_session]())
        r = Recipe(title="Original", dish_name="Original", type="sweet",
                    source_url="https://ig.com/edit2")
        session.add(r)
        session.commit()
        session.refresh(r)

        form_data = {
            "title": "Updated Title",
            "dish_name": "Original Updated",
            "type": "savory",
            "source_url": "https://ig.com/edit2",
        }
        response = client.post(f"/edit/{r.id}", data=form_data)
        assert response.status_code == 302

        session.refresh(r)
        assert r.title == "Updated Title"
        assert r.type == "savory"

    def test_url_fetch_endpoint(self, client):
        """POST /add/fetch returns extracted data as JSON (non-committal)."""
        with patch("app.routes_add.fetch_content") as mock_fetch:
            mock_fetch.return_value = MagicMock(
                text="recipe text", image_path=None,
                source_url="https://example.com/fetch",
            )
            with patch("app.routes_add.extract_recipe", AsyncMock()) as mock_extract:
                mock_extract.return_value = {
                    "dish_name": "Fetched Dish",
                    "distinguishing_feature": "test",
                    "type": "sweet",
                    "subtype": "dessert",
                    "macro_tags": [],
                    "calories_per_portion": 400,
                    "ingredients": ["a", "b"],
                    "prep_time_minutes": 10,
                    "cook_time_minutes": 20,
                    "portions": 4,
                    "instructions": "Steps here",
                    "missing_critical_info": False,
                }

                response = client.post("/add/fetch", data={"url": "https://example.com/fetch"})
                assert response.status_code == 200
                data = response.json()
                assert data["dish_name"] == "Fetched Dish"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_add.py -v
```

Expected: FAIL — routes not yet implemented

- [ ] **Step 3: Write `app/routes_add.py` (full implementation)**

```python
"""Add and Edit recipe routes — URL fetch + manual entry."""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.models import Recipe
from app.scraper import fetch_content
from app.extractor import extract_recipe
from app.linker import detect_and_link
from app.main import templates

router = APIRouter(tags=["add"])


# ── Add Recipe ────────────────────────────────────────────────────────────

@router.get("/add")
async def add_recipe_page(request: Request):
    return templates.TemplateResponse("add.html", {
        "request": request,
        "recipe": None,  # None = create mode
        "error": None,
    })


@router.post("/add/fetch")
async def fetch_from_url(request: Request):
    """POST endpoint: given a URL, scrape + LLM-extract and return JSON.
    Does NOT save to DB — returns data for the form to pre-fill.
    """
    form = await request.form()
    url = form.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "No URL provided"}, status_code=400)

    try:
        content = fetch_content(url)
    except Exception as e:
        return JSONResponse({"error": f"Could not fetch URL: {e}"}, status_code=400)

    try:
        data = await extract_recipe(content.text, url)
    except Exception as e:
        return JSONResponse({"error": f"Extraction failed: {e}"}, status_code=422)

    # Return as form-prefill data
    return {
        "dish_name": data.get("dish_name"),
        "distinguisher": data.get("distinguishing_feature"),
        "type": data.get("type"),
        "subtype": data.get("subtype"),
        "calories_per_portion": data.get("calories_per_portion"),
        "macro_tags": json.dumps(data.get("macro_tags") or []),
        "ingredients": json.dumps(data.get("ingredients") or []),
        "prep_time": data.get("prep_time_minutes"),
        "cook_time": data.get("cook_time_minutes"),
        "portions": data.get("portions"),
        "instructions": data.get("instructions"),
        "source_url": url,
        "photo_path": content.image_path,
    }


@router.post("/add")
async def save_recipe(
    request: Request,
    title: str = Form(...),
    dish_name: str = Form(...),
    distinguisher: str = Form(default=""),
    type: str = Form(...),
    subtype: str = Form(default=""),
    calories_per_portion: str = Form(default=""),
    macro_tags: str = Form(default="[]"),
    ingredients: str = Form(default="[]"),
    prep_time: str = Form(default=""),
    cook_time: str = Form(default=""),
    portions: str = Form(default=""),
    instructions: str = Form(default=""),
    source_url: str = Form(...),
    photo_path: str = Form(default=""),
    rating: str = Form(default=""),
    session: Session = Depends(get_session),
):
    # Check duplicate
    existing = session.exec(select(Recipe).where(Recipe.source_url == source_url)).first()
    if existing:
        return templates.TemplateResponse("add.html", {
            "request": request,
            "recipe": None,
            "error": f"A recipe from this URL already exists: /recipe/{existing.id}",
        })

    recipe = Recipe(
        title=title,
        dish_name=dish_name,
        distinguisher=distinguisher or None,
        type=type,
        subtype=subtype or None,
        calories_per_portion=int(calories_per_portion) if calories_per_portion else None,
        macro_tags=macro_tags,
        ingredients=ingredients,
        prep_time=int(prep_time) if prep_time else None,
        cook_time=int(cook_time) if cook_time else None,
        portions=int(portions) if portions else None,
        instructions=instructions or None,
        source_url=source_url,
        photo_path=photo_path or None,
        rating=int(rating) if rating else None,
    )
    recipe.compute_derived_fields()

    session.add(recipe)
    session.commit()
    session.refresh(recipe)

    detect_and_link(recipe, session)

    return RedirectResponse(url=f"/recipe/{recipe.id}", status_code=302)


# ── Edit Recipe ────────────────────────────────────────────────────────────

@router.get("/edit/{recipe_id}")
async def edit_recipe_page(recipe_id: int, request: Request, session: Session = Depends(get_session)):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
    return templates.TemplateResponse("add.html", {
        "request": request,
        "recipe": recipe,  # not None = edit mode
        "error": None,
    })


@router.post("/edit/{recipe_id}")
async def update_recipe(
    recipe_id: int,
    request: Request,
    title: str = Form(...),
    dish_name: str = Form(...),
    distinguisher: str = Form(default=""),
    type: str = Form(...),
    subtype: str = Form(default=""),
    calories_per_portion: str = Form(default=""),
    macro_tags: str = Form(default="[]"),
    ingredients: str = Form(default="[]"),
    prep_time: str = Form(default=""),
    cook_time: str = Form(default=""),
    portions: str = Form(default=""),
    instructions: str = Form(default=""),
    source_url: str = Form(...),
    photo_path: str = Form(default=""),
    rating: str = Form(default=""),
    session: Session = Depends(get_session),
):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

    recipe.title = title
    recipe.dish_name = dish_name
    recipe.distinguisher = distinguisher or None
    recipe.type = type
    recipe.subtype = subtype or None
    recipe.calories_per_portion = int(calories_per_portion) if calories_per_portion else None
    recipe.macro_tags = macro_tags
    recipe.ingredients = ingredients
    recipe.prep_time = int(prep_time) if prep_time else None
    recipe.cook_time = int(cook_time) if cook_time else None
    recipe.portions = int(portions) if portions else None
    recipe.instructions = instructions or None
    recipe.source_url = source_url
    if photo_path:
        recipe.photo_path = photo_path
    if rating:
        recipe.rating = int(rating)
    recipe.updated_at = datetime.utcnow()

    recipe.compute_derived_fields()
    session.commit()
    session.refresh(recipe)

    # Re-run cross-link detection (ingredients/dish may have changed)
    detect_and_link(recipe, session)

    return RedirectResponse(url=f"/recipe/{recipe.id}", status_code=302)
```

- [ ] **Step 4: Write `app/templates/add.html` (full template)**

```html
{% extends "base.html" %}
{% block title %}{% if recipe %}Edit Recipe{% else %}Add Recipe{% endif %}{% endblock %}
{% block content %}
<h1>{% if recipe %}Edit Recipe{% else %}Add Recipe{% endif %}</h1>

{% if error %}
<div class="error-banner">{{ error }}</div>
{% endif %}

{% if not recipe %}
<section class="url-fetch">
    <h2>From URL</h2>
    <form id="url-fetch-form" onsubmit="fetchFromUrl(event)">
        <input type="url" name="url" placeholder="Paste Instagram or recipe URL..." required
               style="flex:1; padding: 0.5rem;">
        <button type="submit" class="btn btn-primary">Fetch &amp; Extract</button>
        <span id="fetch-spinner" class="spinner" style="display:none">⏳ Extracting...</span>
    </form>
    <div id="fetch-error" class="error-banner" style="display:none"></div>
</section>

<hr>
{% endif %}

<form method="post" class="recipe-form" id="recipe-form"
      action="{% if recipe %}/edit/{{ recipe.id }}{% else %}/add{% endif %}">
    <fieldset>
        <legend>Basics</legend>
        <label>Title: <input name="title" value="{{ recipe.title if recipe else '' }}" required></label>
        <label>Dish Name: <input name="dish_name" value="{{ recipe.dish_name if recipe else '' }}" required></label>
        <label>Distinguishing Feature: <input name="distinguisher" value="{{ recipe.distinguisher or '' if recipe else '' }}"></label>
        <label>Type:
            <select name="type" required>
                <option value="sweet" {% if recipe and recipe.type == 'sweet' %}selected{% endif %}>Sweet</option>
                <option value="savory" {% if recipe and recipe.type == 'savory' %}selected{% endif %}>Savory</option>
            </select>
        </label>
        <label>Subtype:
            <select name="subtype">
                <option value="">—</option>
                {% for opt in ['main', 'dessert', 'snack', 'soup', 'salad', 'breakfast', 'side', 'drink'] %}
                <option value="{{ opt }}" {% if recipe and recipe.subtype == opt %}selected{% endif %}>{{ opt.capitalize() }}</option>
                {% endfor %}
            </select>
        </label>
        <label>Source URL: <input type="url" name="source_url" value="{{ recipe.source_url if recipe else '' }}" required></label>
    </fieldset>

    <fieldset>
        <legend>Times &amp; Portions</legend>
        <label>Prep Time (min): <input type="number" name="prep_time" min="0" value="{{ recipe.prep_time if recipe else '' }}"></label>
        <label>Cook Time (min): <input type="number" name="cook_time" min="0" value="{{ recipe.cook_time if recipe else '' }}"></label>
        <label>Portions: <input type="number" name="portions" min="1" value="{{ recipe.portions if recipe else '' }}"></label>
    </fieldset>

    <fieldset>
        <legend>Nutrition</legend>
        <label>Calories per Portion: <input type="number" name="calories_per_portion" min="0" value="{{ recipe.calories_per_portion if recipe else '' }}"></label>
        <label>Macro Tags (JSON array):
            <input name="macro_tags" value='{{ recipe.macro_tags if recipe else "[]" }}' placeholder='["protein-rich","low-carb"]'>
        </label>
    </fieldset>

    <fieldset>
        <legend>Ingredients (JSON array)</legend>
        <textarea name="ingredients" rows="3" placeholder='["egg","flour","chocolate"]'>{{ recipe.ingredients if recipe else "[]" }}</textarea>
    </fieldset>

    <fieldset>
        <legend>Instructions (Markdown)</legend>
        <textarea name="instructions" rows="8" placeholder="1. Step one&#10;2. Step two">{{ recipe.instructions if recipe else '' }}</textarea>
    </fieldset>

    {% if recipe %}
    <fieldset>
        <legend>Rating</legend>
        <input type="number" name="rating" min="1" max="5" value="{{ recipe.rating if recipe.rating else '' }}">
    </fieldset>
    <input type="hidden" name="photo_path" value="{{ recipe.photo_path or '' }}">
    {% endif %}

    <button type="submit" class="btn btn-primary">
        {% if recipe %}Save Changes{% else %}Save Recipe{% endif %}
    </button>
</form>

<script>
async function fetchFromUrl(event) {
    event.preventDefault();
    const url = document.getElementById('url-fetch-form').url.value;
    const spinner = document.getElementById('fetch-spinner');
    const error = document.getElementById('fetch-error');
    spinner.style.display = 'inline';
    error.style.display = 'none';

    try {
        const resp = await fetch('/add/fetch', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: new URLSearchParams({url}),
        });
        const data = await resp.json();
        if (resp.ok) {
            // Pre-fill form
            for (const [key, val] of Object.entries(data)) {
                const el = document.querySelector(`[name="${key}"]`);
                if (el) { el.value = val ?? ''; }
            }
        } else {
            error.textContent = data.error || 'Fetch failed';
            error.style.display = 'block';
        }
    } catch (e) {
        error.textContent = 'Network error. Try again.';
        error.style.display = 'block';
    } finally {
        spinner.style.display = 'none';
    }
}
</script>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_add.py -v
```

Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add app/routes_add.py app/templates/add.html tests/test_add.py
git commit -m "feat: add/edit recipe pages with URL fetch and manual entry"
```

---

### Task 11: CSS styling

**Files:**
- Create: `app/static/css/app.css`

- [ ] **Step 1: Write `app/static/css/app.css`**

```css
/* ── Reset & Base ───────────────────────────────────────────────────── */

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
    --bg: #f8f9fa;
    --surface: #ffffff;
    --text: #212529;
    --text-muted: #6c757d;
    --border: #dee2e6;
    --primary: #4361ee;
    --primary-hover: #3a56d4;
    --danger: #dc3545;
    --danger-hover: #c82333;
    --sweet: #e83e8c;
    --savory: #fd7e14;
    --low-cal: #28a745;
    --mid-cal: #ffc107;
    --high-cal: #dc3545;
    --radius: 8px;
    --shadow: 0 1px 3px rgba(0,0,0,0.08);
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
}

/* ── Navigation ──────────────────────────────────────────────────────── */

nav {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 0.75rem 1.5rem;
    display: flex;
    gap: 1.5rem;
    position: sticky;
    top: 0;
    z-index: 100;
}
nav a {
    text-decoration: none;
    color: var(--primary);
    font-weight: 500;
    font-size: 1rem;
}
nav a:hover { text-decoration: underline; }

main {
    max-width: 100%;
    padding: 1.5rem;
}

/* ── Browse Layout ───────────────────────────────────────────────────── */

.browse-layout {
    display: grid;
    grid-template-columns: 260px 1fr;
    gap: 1.5rem;
    align-items: start;
}

/* ── Filter Sidebar ──────────────────────────────────────────────────── */

.filter-sidebar {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1rem;
    position: sticky;
    top: 4rem;
}
.filter-sidebar h2 {
    font-size: 1.1rem;
    margin-bottom: 0.75rem;
}
.filter-group {
    border: none;
    margin-bottom: 1rem;
}
.filter-group legend {
    font-weight: 600;
    font-size: 0.85rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 0.35rem;
}
.filter-group.scrollable {
    max-height: 200px;
    overflow-y: auto;
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.5rem;
}
.filter-checkbox {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.2rem 0;
    font-size: 0.9rem;
    cursor: pointer;
}
.filter-checkbox input { accent-color: var(--primary); }
.filter-group input[type=range] { width: 100%; }

.star-filter { display: flex; gap: 2px; }
.star-btn {
    background: none;
    border: none;
    font-size: 1.3rem;
    cursor: pointer;
    color: #ccc;
    padding: 0;
}
.star-btn.active { color: #f0ad4e; }
.star-clear {
    background: none;
    border: none;
    cursor: pointer;
    color: var(--text-muted);
    font-size: 0.9rem;
}

.clear-filters {
    display: block;
    text-align: center;
    color: var(--danger);
    font-size: 0.85rem;
    text-decoration: none;
    margin-top: 0.5rem;
}
.clear-filters:hover { text-decoration: underline; }

/* ── Recipe Grid ──────────────────────────────────────────────────────── */

.recipe-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 1rem;
}

.recipe-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    text-decoration: none;
    color: inherit;
    box-shadow: var(--shadow);
    transition: box-shadow 0.15s;
}
.recipe-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.12); }

.card-image {
    width: 100%;
    height: 160px;
    overflow: hidden;
    background: #e9ecef;
}
.card-image img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}
.no-photo {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    font-size: 2rem;
    color: #adb5bd;
}
.no-photo.large { height: 300px; font-size: 3rem; }

.card-body { padding: 0.75rem; }
.card-title {
    font-size: 0.95rem;
    font-weight: 600;
    margin-bottom: 0.4rem;
    line-height: 1.3;
}
.card-meta {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    font-size: 0.8rem;
    color: var(--text-muted);
    flex-wrap: wrap;
}
.stars { color: #f0ad4e; }

.recipe-card.small .card-body { padding: 0.5rem 0.75rem; }
.recipe-card.small .card-title { font-size: 0.85rem; }

/* ── Badges ───────────────────────────────────────────────────────────── */

.badges { display: flex; gap: 0.4rem; flex-wrap: wrap; margin: 0.5rem 0; }
.badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 99px;
    font-size: 0.75rem;
    font-weight: 500;
    background: #e9ecef;
}
.badge.type-sweet { background: #fce4f0; color: var(--sweet); }
.badge.type-savory { background: #fff3e6; color: var(--savory); }
.badge.subtype { background: #e7f0ff; color: var(--primary); }
.tier, .badge[class*="tier-"] { }
.tier-low, .badge.tier-low { background: #d4edda; color: var(--low-cal); }
.tier-mid, .badge.tier-mid { background: #fff3cd; color: #856404; }
.tier-high, .badge.tier-high { background: #f8d7da; color: var(--high-cal); }
.badge.macro { background: #f0e6ff; color: #6f42c1; }

/* ── Detail Page ──────────────────────────────────────────────────────── */

.recipe-detail { max-width: 800px; margin: 0 auto; }
.detail-header {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2rem;
    margin-bottom: 2rem;
}
@media (max-width: 600px) { .detail-header { grid-template-columns: 1fr; } }

.detail-photo img {
    width: 100%;
    border-radius: var(--radius);
    object-fit: cover;
    max-height: 400px;
}

.detail-info h1 { font-size: 1.6rem; margin-bottom: 0.25rem; }
.distinguisher { color: var(--text-muted); font-style: italic; margin-bottom: 0.5rem; }
.times { display: grid; grid-template-columns: 1fr 1fr; gap: 0.25rem 1rem; margin: 0.75rem 0; font-size: 0.9rem; }
.source-link { display: inline-block; margin-top: 0.5rem; color: var(--primary); font-size: 0.9rem; }

.detail-section { margin-bottom: 1.5rem; }
.detail-section h2 {
    font-size: 1.1rem;
    margin-bottom: 0.5rem;
    padding-bottom: 0.25rem;
    border-bottom: 2px solid var(--border);
}
.ingredients-list {
    list-style: none;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 0.25rem;
}
.ingredients-list li::before { content: "• "; color: var(--primary); }
.instructions-text { white-space: pre-wrap; line-height: 1.7; }

.linked-cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 0.75rem;
}
.detail-actions { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--border); }

/* ── Forms ────────────────────────────────────────────────────────────── */

.recipe-form fieldset {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1rem;
    margin-bottom: 1rem;
}
.recipe-form legend {
    font-weight: 600;
    padding: 0 0.5rem;
}
.recipe-form label {
    display: block;
    margin-bottom: 0.5rem;
    font-size: 0.9rem;
}
.recipe-form input,
.recipe-form select,
.recipe-form textarea {
    width: 100%;
    padding: 0.4rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 0.9rem;
    font-family: inherit;
}
.recipe-form textarea { resize: vertical; }

.url-fetch {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1rem;
    margin-bottom: 1rem;
}
.url-fetch h2 { font-size: 1rem; margin-bottom: 0.5rem; }
.url-fetch form { display: flex; gap: 0.5rem; }

/* ── Buttons ──────────────────────────────────────────────────────────── */

.btn {
    display: inline-block;
    padding: 0.5rem 1rem;
    border-radius: 4px;
    font-size: 0.9rem;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid transparent;
    text-decoration: none;
}
.btn-primary { background: var(--primary); color: #fff; border-color: var(--primary); }
.btn-primary:hover { background: var(--primary-hover); }
.btn-danger { background: var(--danger); color: #fff; border-color: var(--danger); }
.btn-danger:hover { background: var(--danger-hover); }

/* ── Misc ─────────────────────────────────────────────────────────────── */

.error-banner {
    background: #f8d7da;
    color: #721c24;
    padding: 0.75rem 1rem;
    border-radius: var(--radius);
    margin-bottom: 1rem;
    border: 1px solid #f5c6cb;
}
.spinner { margin-left: 0.5rem; color: var(--text-muted); }
.empty-state { grid-column: 1 / -1; text-align: center; padding: 3rem 1rem; color: var(--text-muted); }
hr { border: none; border-top: 1px solid var(--border); margin: 1.5rem 0; }
```

- [ ] **Step 2: Commit**

```bash
git add app/static/css/app.css
git commit -m "feat: CSS styling for browse, detail, and add pages"
```

---

### Task 12: Integration tests (end-to-end pipeline)

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration tests**

Create `tests/test_integration.py`:

```python
"""Integration tests: full pipeline from URL to DB entry."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import app
from app.database import get_session
from app.models import Recipe, CrossLink


@pytest.fixture
def client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestBotIntegration:
    """Test the full bot ingestion pipeline end-to-end."""

    def test_full_pipeline_creates_recipe_and_links(self, client):
        from app.bot import process_extraction

        # Mock scraper
        with patch("app.bot.fetch_content") as mock_fetch:
            mock_fetch.return_value = MagicMock(
                text="Bolo de Chocolate proteico. Ingredientes: ovos, farinha, whey, chocolate.",
                image_path="/tmp/test.jpg",
                source_url="https://ig.com/integration-test-1",
            )

            # Mock extractor
            with patch("app.bot.extract_recipe", new_callable=AsyncMock) as mock_extract:
                mock_extract.return_value = {
                    "dish_name": "Bolo de Chocolate",
                    "distinguishing_feature": "whey protein",
                    "type": "sweet",
                    "subtype": "dessert",
                    "macro_tags": ["protein-rich"],
                    "calories_per_portion": 400,
                    "ingredients": ["egg", "flour", "whey", "chocolate"],
                    "prep_time_minutes": 10,
                    "cook_time_minutes": 30,
                    "portions": 8,
                    "instructions": "Mix and bake",
                    "missing_critical_info": False,
                }

                with patch("app.bot.detect_and_link") as mock_link:
                    mock_link.return_value = 0

                    result = process_extraction("https://ig.com/integration-test-1")

        assert result is not None
        assert result["title"] == "Bolo de Chocolate (whey protein)"
        assert result["calorie_tier"] == "mid"

    def test_duplicate_url_detected_at_add_page(self, client):
        """Submitting a duplicate URL on /add returns error, not duplicate."""
        # Insert via direct DB
        session = next(client.app.dependency_overrides[get_session]())
        r = Recipe(title="Exists", dish_name="Exists", type="sweet",
                    source_url="https://example.com/duplicate-test")
        session.add(r)
        session.commit()

        # Try to add again
        response = client.post("/add", data={
            "title": "Duplicate", "dish_name": "Dup", "type": "sweet",
            "source_url": "https://example.com/duplicate-test",
        })
        assert response.status_code == 200
        assert "already exists" in response.text.lower()


class TestCrossLinkIntegration:
    """Test cross-links end-to-end from add to browse."""

    def test_similar_recipes_linked_on_add(self, client):
        # Insert first recipe
        client.post("/add", data={
            "title": "Bolo Choc 1", "dish_name": "Bolo de Chocolate",
            "type": "sweet", "subtype": "dessert",
            "ingredients": '["egg","flour","chocolate","butter"]',
            "source_url": "https://ig.com/link-test-1",
        })
        # Insert second with overlapping ingredients
        client.post("/add", data={
            "title": "Bolo Choc 2", "dish_name": "Bolo de Chocolate Fit",
            "type": "sweet", "subtype": "dessert",
            "ingredients": '["egg","flour","chocolate","whey"]',
            "source_url": "https://ig.com/link-test-2",
        })

        # Check cross-links created
        session = next(client.app.dependency_overrides[get_session]())
        links = session.exec(select(CrossLink)).all()
        assert len(links) >= 1
        assert links[0].auto_generated is True

    def test_browse_page_shows_linked_recipes_indicator(self, client):
        """After linking, browse page still works correctly."""
        client.post("/add", data={
            "title": "A", "dish_name": "Dish A", "type": "sweet",
            "ingredients": '["a","b","c"]', "source_url": "https://ig.com/browse-link-1",
        })
        client.post("/add", data={
            "title": "B", "dish_name": "Dish A Variant", "type": "sweet",
            "ingredients": '["a","b","c","d"]', "source_url": "https://ig.com/browse-link-2",
        })

        response = client.get("/")
        assert response.status_code == 200
        assert "Dish A" in response.text
        assert "Dish A Variant" in response.text
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
python -m pytest tests/test_integration.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration tests for full pipeline and cross-linking"
```

---

### Task 13: Deployment configuration

**Files:**
- Create: `deploy/recipe-app.service`
- Create: `deploy/nginx.conf`
- Modify: `app/main.py` (add health endpoint)

- [ ] **Step 1: Add health check endpoint to `app/main.py`**

At the end of the route imports section in `app/main.py`, add:

```python
@app.get("/health")
async def health_check():
    return {"status": "ok"}
```

- [ ] **Step 2: Write `deploy/recipe-app.service`**

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
RestartSec=5
EnvironmentFile=/srv/recipe-app/.env

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Write `deploy/nginx.conf`**

```nginx
server {
    listen 80 default_server;
    server_name _;

    # Serve static files directly (photos, CSS)
    location /static/ {
        alias /srv/recipe-app/app/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    # Proxy everything else to FastAPI
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;  # extractions can take time
    }
}
```

- [ ] **Step 4: Write `deploy/setup.sh` (convenience script for first-time VPS setup)**

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Recipe App: VPS Setup ==="

# Install system dependencies
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv nginx

# Create app user
if ! id -u recipe-app >/dev/null 2>&1; then
    sudo useradd -r -s /bin/false recipe-app
fi

# Create app directory
sudo mkdir -p /srv/recipe-app
sudo chown -R $USER:$USER /srv/recipe-app

# Copy files (run from repo root)
cp -r app data requirements.txt /srv/recipe-app/

# Python venv
python3.12 -m venv /srv/recipe-app/venv
/srv/recipe-app/venv/bin/pip install -r /srv/recipe-app/requirements.txt

# Environment file (edit this!)
if [ ! -f /srv/recipe-app/.env ]; then
    cp .env.example /srv/recipe-app/.env
    echo "⚠️  Edit /srv/recipe-app/.env with your secrets before starting!"
fi

# systemd
sudo cp deploy/recipe-app.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable recipe-app

# nginx
sudo cp deploy/nginx.conf /etc/nginx/sites-available/recipe-app
sudo ln -sf /etc/nginx/sites-available/recipe-app /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# Start
sudo systemctl start recipe-app

echo "=== Done! ==="
echo "Check status: sudo systemctl status recipe-app"
echo "Check logs: sudo journalctl -u recipe-app -f"
```

Make it executable:

```bash
chmod +x deploy/setup.sh
```

- [ ] **Step 5: Verify full test suite passes**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass (should be ~35 tests across all test files)

- [ ] **Step 6: Commit**

```bash
git add deploy/recipe-app.service deploy/nginx.conf deploy/setup.sh app/main.py
git commit -m "feat: deployment config (systemd, nginx, setup script)"
```

---

## Dependency Order

```
Task 1 (skeleton)
 └─→ Task 2 (models)
      ├─→ Task 3 (extractor)
      ├─→ Task 4 (scraper)
      ├─→ Task 5 (linker)
      └─→ Task 6 (app shell + route stubs)
           ├─→ Task 7 (bot handler)
           ├─→ Task 8 (browse page)
           ├─→ Task 9 (detail page)
           ├─→ Task 10 (add/edit pages)
           └─→ Task 11 (CSS)
                └─→ Task 12 (integration tests)
                     └─→ Task 13 (deployment config)
```

Tasks 3, 4, 5 can be built in parallel. Tasks 8–11 can be built in parallel once 6 is done.

---

## File Map

| File | Created in Task | Modified in Task |
|------|----------------|-----------------|
| `requirements.txt` | 1 | — |
| `.env.example` | 1 | — |
| `app/__init__.py` | 1 | — |
| `app/config.py` | 1 | 6 |
| `app/models.py` | 2 | — |
| `app/database.py` | 2 | — |
| `app/extractor.py` | 3 | — |
| `app/scraper.py` | 4 | — |
| `app/linker.py` | 5 | — |
| `app/main.py` | 6 | 13 |
| `app/routes_browse.py` | 6 | 8 |
| `app/routes_detail.py` | 6 | 9 |
| `app/routes_add.py` | 6 | 10 |
| `app/bot.py` | 7 | — |
| `app/templates/base.html` | 6 | — |
| `app/templates/404.html` | 6 | — |
| `app/templates/browse.html` | 6 | 8 |
| `app/templates/detail.html` | 6 | 9 |
| `app/templates/add.html` | 6 | 10 |
| `app/static/css/app.css` | 11 | — |
| `deploy/recipe-app.service` | 13 | — |
| `deploy/nginx.conf` | 13 | — |
| `deploy/setup.sh` | 13 | — |
| `tests/__init__.py` | 2 | — |
| `tests/conftest.py` | 2 | — |
| `tests/test_models.py` | 2 | — |
| `tests/test_extractor.py` | 3 | — |
| `tests/test_scraper.py` | 4 | — |
| `tests/test_linker.py` | 5 | — |
| `tests/test_routes.py` | 6 | — |
| `tests/test_bot.py` | 7 | — |
| `tests/test_browse.py` | 8 | — |
| `tests/test_detail.py` | 9 | — |
| `tests/test_add.py` | 10 | — |
| `tests/test_integration.py` | 12 | — |
