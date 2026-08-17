# Hub Foundation + Bills/Bank Statements (Manual Channel) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working, deployable slice of the Home & Family hub: Cloudflare-Access-gated FastAPI app with navigation, the manual-upload bills/bank-statements pipeline (extraction → categorization → dedup → wiki-fact pass → to-do generation), a wiki page browser, a to-do list, and a dashboard — all backed by one SQLite database managed with Alembic.

**Architecture:** Single FastAPI service, server-rendered with Jinja2 + htmx (no JS build step), one SQLite database shared across domains, Cloudflare Access JWT verified in middleware. A single `ingest_document` pipeline function orchestrates extraction, categorization, deduplication, to-do generation, and the wiki-fact pass for every document, regardless of how it arrived.

**Tech Stack:** FastAPI, SQLModel, Alembic, Jinja2, htmx, Anthropic SDK (`claude-sonnet-5` for extraction, `claude-haiku-4-5-20251001` for the lighter wiki-worthiness assessment), pdf2image/pdfplumber for PDF handling, pytest + pytest-asyncio.

**Spec:** [`docs/superpowers/specs/2026-08-17-hub-foundation-bills-bank-design.md`](../specs/2026-08-17-hub-foundation-bills-bank-design.md)

## Global Constraints

- One shared SQLite database for the whole hub (not per domain) — schema managed via Alembic migrations, never `create_all` in production code paths.
- Auth: Cloudflare Access JWT read from the `Cf-Access-Jwt-Assertion` header (or `CF_Authorization` cookie), verified against Cloudflare's JWKS endpoint; no in-app login.
- Whole family has full visibility — no per-record permission model.
- Document pipeline failures (extraction, decryption, sync) must set status `needs_attention` and surface on the dashboard — never fail silently.
- Duplicate documents/transactions must be detected (content hash for documents; provider + statement_period for transactions) and never double-counted.
- Claude model IDs: `claude-sonnet-5` for document extraction, `claude-haiku-4-5-20251001` for the wiki-worthiness assessment pass.

## Out of scope for this plan (deferred to a follow-up plan against the same spec)

- Email-forwarding ingestion channel and Cloudflare Email Routing setup.
- Password-protected PDF decryption via family NIFs.
- Enable Banking sync (Santander PT + Revolut) and the `BankAccount`/`BankTransaction` models.
- Deployment scripts (nginx, systemd, Cloudflare Tunnel/Access application setup).

This plan's Task 18 produces a fully working app reachable via `uvicorn` locally: manual upload → extraction → categorization → dedup → to-do → wiki → dashboard, all testable end-to-end. The deferred items are additive ingestion channels and deploy plumbing that plug into this same pipeline without changing it.

---

## File Structure

```
Home & Family/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app, middleware, router wiring
│   ├── config.py                  # env-var settings
│   ├── db.py                      # engine + get_session dependency
│   ├── auth.py                    # Cloudflare Access JWT verification
│   ├── models/
│   │   ├── __init__.py            # imports every model module (registers metadata)
│   │   ├── document.py            # Document, DocumentSource, DocumentStatus
│   │   ├── transaction.py         # Transaction, Category
│   │   ├── todo.py                # Todo
│   │   └── wiki.py                # WikiPage, WikiChange
│   ├── routers/
│   │   ├── dashboard.py
│   │   ├── bills.py
│   │   ├── todos.py
│   │   └── wiki.py
│   ├── services/
│   │   ├── storage.py             # save_upload
│   │   ├── extraction.py          # extract_bill (Claude)
│   │   ├── categorization.py      # normalize_category
│   │   ├── dedup.py               # find_existing_document_by_hash, find_duplicate_transaction
│   │   ├── pipeline.py            # ingest_document — orchestrates the above
│   │   ├── todo_engine.py         # generate_todo_for_transaction
│   │   ├── wiki_engine.py         # assess_and_update_wiki (Claude)
│   │   └── dashboard_service.py   # get_dashboard_data
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── bills/{list,upload,detail}.html
│   │   ├── todos/{list,_lists}.html
│   │   └── wiki/{list,page}.html
│   └── static/
│       ├── htmx.min.js
│       └── documents/             # uploaded files (gitignored)
├── alembic/
│   ├── env.py
│   └── versions/
├── alembic.ini
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── data/                          # sqlite db (gitignored)
└── tests/
    ├── conftest.py
    ├── test_document_model.py
    ├── test_auth.py
    ├── test_main.py
    ├── test_transaction_model.py
    ├── test_storage.py
    ├── test_extraction.py
    ├── test_categorization.py
    ├── test_dedup.py
    ├── test_pipeline.py
    ├── test_bills_router.py
    ├── test_todo_model.py
    ├── test_todo_engine.py
    ├── test_todos_router.py
    ├── test_wiki_model.py
    ├── test_wiki_engine.py
    ├── test_wiki_router.py
    ├── test_dashboard_service.py
    ├── test_dashboard_router.py
    └── test_e2e_bill_flow.py
```

Rationale for `app/models/` and `app/services/` as packages (rather than the single `models.py`/flat-file style used in the Recipes app): Home & Family is explicitly multi-domain and will keep growing (health, home stuff), so splitting by responsibility from the start avoids a repeat of needing large-file cleanup later.

---

### Task 1: Project scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `README.md`

**Interfaces:**
- Produces: `app.config.settings` — a `Settings` instance with `.ANTHROPIC_API_KEY: str`, `.DATABASE_PATH: Path`, `.DOCUMENTS_DIR: Path`, `.CF_ACCESS_TEAM_DOMAIN: str`, `.CF_ACCESS_AUD: str`, `.database_url: str` (property).

- [ ] **Step 1: Create the directory skeleton**

```bash
mkdir -p app/models app/routers app/services app/templates app/static/documents data tests
```

- [ ] **Step 2: Write `requirements.txt`**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sqlmodel>=0.0.22
alembic>=1.13.0
anthropic>=0.39.0
python-dotenv>=1.0.0
jinja2>=3.1.0
python-multipart>=0.0.9
aiofiles>=24.0.0
pdfplumber>=0.11.4
pdf2image>=1.17.0
pyjwt[crypto]>=2.9.0
httpx>=0.27.0
pytest>=8.0.0
pytest-asyncio>=0.24.0
```

- [ ] **Step 3: Write `.env.example`**

```ini
ANTHROPIC_API_KEY=sk-ant-your_key_here
DATABASE_PATH=data/home_family.db
DOCUMENTS_DIR=app/static/documents
CF_ACCESS_TEAM_DOMAIN=yourteam.cloudflareaccess.com
CF_ACCESS_AUD=your_application_aud_tag
```

- [ ] **Step 4: Write `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.env
data/*.db
app/static/documents/*
!app/static/documents/.gitkeep
.pytest_cache/
```

- [ ] **Step 5: Create `app/__init__.py` (empty) and `app/static/documents/.gitkeep` (empty)**

- [ ] **Step 6: Write `app/config.py`**

```python
"""Application configuration from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Settings:
    ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
    DATABASE_PATH: Path = Path(os.environ.get("DATABASE_PATH", "data/home_family.db"))
    DOCUMENTS_DIR: Path = Path(os.environ.get("DOCUMENTS_DIR", "app/static/documents"))
    CF_ACCESS_TEAM_DOMAIN: str = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "")
    CF_ACCESS_AUD: str = os.environ.get("CF_ACCESS_AUD", "")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.DATABASE_PATH}"


settings = Settings()
```

- [ ] **Step 7: Write a minimal `README.md`**

```markdown
# Home & Family Hub

Family record-keeping hub: bills & bank statements, an auto-maintained wiki
of standing facts, a dashboard, and a to-do list. Gated behind Cloudflare
Access.

## Prerequisites

- Python 3.12+
- `poppler-utils` (system package, required by `pdf2image`)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY at minimum
alembic upgrade head
uvicorn app.main:app --reload
```

## Tests

```bash
pytest
```
```

- [ ] **Step 8: Verify the package imports**

Create a local `.env` (copy of `.env.example`, real or placeholder `ANTHROPIC_API_KEY`), then run:

```bash
pip install -r requirements.txt
python -c "from app.config import settings; print(settings.database_url)"
```

Expected: prints `sqlite:///data/home_family.db`, no errors.

- [ ] **Step 9: Commit**

```bash
git add requirements.txt .env.example .gitignore app/__init__.py app/config.py README.md app/static/documents/.gitkeep
git commit -m "chore: scaffold Home & Family project"
```

---

### Task 2: Database engine, Alembic, and the Document model

**Files:**
- Create: `app/db.py`
- Create: `app/models/__init__.py`
- Create: `app/models/document.py`
- Create: `alembic.ini`, `alembic/env.py` (generated by `alembic init`, then edited)
- Create: `tests/conftest.py`
- Test: `tests/test_document_model.py`

**Interfaces:**
- Consumes: `app.config.settings` (Task 1)
- Produces: `app.db.get_session` (FastAPI dependency, `Generator[Session, None, None]`); `app.models.document.Document` (SQLModel table, fields: `id`, `filename: str`, `file_path: str`, `content_hash: str`, `source: DocumentSource`, `status: DocumentStatus`, `password_protected: bool`, `failure_reason: Optional[str]`, `uploaded_by: Optional[str]`, `created_at: datetime`); `DocumentSource` (`MANUAL`/`EMAIL`/`API`); `DocumentStatus` (`PENDING`/`PROCESSED`/`NEEDS_ATTENTION`); `tests/conftest.py` fixtures `engine`, `session` (function-scoped, temp SQLite file per test).

- [ ] **Step 1: Write `app/db.py`**

```python
"""Database engine and session management."""

from sqlmodel import Session, create_engine

from app.config import settings

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


def get_session():
    """Yield a database session. Used as a FastAPI dependency."""
    with Session(engine) as session:
        yield session
```

- [ ] **Step 2: Write `app/models/document.py`**

```python
"""Document: a source file (uploaded, forwarded, or synced)."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class DocumentSource(str, Enum):
    MANUAL = "manual"
    EMAIL = "email"
    API = "api"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    NEEDS_ATTENTION = "needs_attention"


class Document(SQLModel, table=True):
    __tablename__ = "documents"

    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    file_path: str
    content_hash: str = Field(index=True)
    source: DocumentSource
    status: DocumentStatus = Field(default=DocumentStatus.PENDING)
    password_protected: bool = Field(default=False)
    failure_reason: Optional[str] = None
    uploaded_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 3: Write `app/models/__init__.py`**

```python
"""Imports every model module so SQLModel.metadata is fully populated."""

from app.models.document import Document  # noqa: F401
```

- [ ] **Step 4: Write `tests/conftest.py`**

```python
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import pytest
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture()
def engine(tmp_path):
    db_path = tmp_path / "test.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    from app import models  # noqa: F401 — registers tables on SQLModel.metadata

    SQLModel.metadata.create_all(test_engine)
    return test_engine


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s
```

- [ ] **Step 5: Write the failing test `tests/test_document_model.py`**

```python
from app.models.document import Document, DocumentSource, DocumentStatus


def test_create_and_read_document(session):
    document = Document(
        filename="edp-august.pdf",
        file_path="/data/documents/edp-august.pdf",
        content_hash="abc123",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    assert document.id is not None
    assert document.status == DocumentStatus.PENDING
    assert document.password_protected is False
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_document_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.document'` (file not yet created at collection time if steps run out of order — if Step 2 already ran, instead skip to Step 7. Run this check right after Step 4, before Step 2/3, if following strict step order.)

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_document_model.py -v`
Expected: PASS

- [ ] **Step 8: Set up Alembic**

```bash
alembic init alembic
```

Replace the generated `alembic/env.py` content with:

```python
from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

from app.config import settings
from app import models  # noqa: F401 — registers all tables on SQLModel.metadata

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import engine_from_config, pool

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

In `alembic.ini`, delete or comment out the `sqlalchemy.url = ...` line — `env.py` sets it dynamically from `settings.database_url`.

- [ ] **Step 9: Generate and apply the initial migration**

```bash
alembic revision --autogenerate -m "add documents table"
```

Open the generated file under `alembic/versions/` and confirm it creates a `documents` table with the columns from Step 2. Then apply it:

```bash
alembic upgrade head
```

Expected: `data/home_family.db` now exists and contains a `documents` table (verify with `sqlite3 data/home_family.db ".tables"`).

- [ ] **Step 10: Commit**

```bash
git add app/db.py app/models/ alembic.ini alembic/ tests/conftest.py tests/test_document_model.py .gitignore
git commit -m "feat: add DB engine, Alembic migrations, and the Document model"
```

---

### Task 3: Cloudflare Access authentication

**Files:**
- Create: `app/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `app.config.settings.CF_ACCESS_AUD`, `settings.CF_ACCESS_TEAM_DOMAIN` (Task 1)
- Produces: `app.auth.HEADER_NAME: str`; `app.auth.AccessTokenError(Exception)`; `app.auth.verify_access_token(token: str, jwks_client, audience: str) -> dict` (raises `AccessTokenError`); `app.auth.CloudflareAccessMiddleware(app, jwks_client=None, audience: str | None = None)` — a Starlette `BaseHTTPMiddleware` that sets `request.state.user_email` on success, exempts `/health`, and returns 401 JSON on failure.

- [ ] **Step 1: Write the failing tests `tests/test_auth.py`**

```python
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import AccessTokenError, CloudflareAccessMiddleware, HEADER_NAME, verify_access_token


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJWKSClient:
    def __init__(self, public_key):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(self._public_key)


def _make_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _sign_token(private_key, claims):
    return jwt.encode(claims, private_key, algorithm="RS256")


def test_verify_access_token_accepts_valid_token():
    private_key, public_key = _make_keypair()
    claims = {"email": "pedro@example.com", "aud": "test-aud", "exp": int(time.time()) + 3600}
    token = _sign_token(private_key, claims)

    result = verify_access_token(token, _FakeJWKSClient(public_key), audience="test-aud")

    assert result["email"] == "pedro@example.com"


def test_verify_access_token_rejects_wrong_audience():
    private_key, public_key = _make_keypair()
    claims = {"email": "pedro@example.com", "aud": "other-aud", "exp": int(time.time()) + 3600}
    token = _sign_token(private_key, claims)

    with pytest.raises(AccessTokenError):
        verify_access_token(token, _FakeJWKSClient(public_key), audience="test-aud")


def test_verify_access_token_rejects_expired_token():
    private_key, public_key = _make_keypair()
    claims = {"email": "pedro@example.com", "aud": "test-aud", "exp": int(time.time()) - 10}
    token = _sign_token(private_key, claims)

    with pytest.raises(AccessTokenError):
        verify_access_token(token, _FakeJWKSClient(public_key), audience="test-aud")


def test_middleware_blocks_missing_token():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _, public_key = _make_keypair()
    test_app = FastAPI()

    @test_app.get("/protected")
    async def protected():
        return {"ok": True}

    test_app.add_middleware(
        CloudflareAccessMiddleware, jwks_client=_FakeJWKSClient(public_key), audience="test-aud"
    )
    client = TestClient(test_app)

    response = client.get("/protected")

    assert response.status_code == 401


def test_middleware_allows_valid_token_and_exposes_email():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    private_key, public_key = _make_keypair()
    test_app = FastAPI()

    @test_app.get("/protected")
    async def protected(request: Request):
        return {"email": request.state.user_email}

    test_app.add_middleware(
        CloudflareAccessMiddleware, jwks_client=_FakeJWKSClient(public_key), audience="test-aud"
    )
    client = TestClient(test_app)

    token = _sign_token(
        private_key, {"email": "pedro@example.com", "aud": "test-aud", "exp": int(time.time()) + 3600}
    )

    response = client.get("/protected", headers={HEADER_NAME: token})

    assert response.status_code == 200
    assert response.json() == {"email": "pedro@example.com"}


def test_middleware_exempts_health_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _, public_key = _make_keypair()
    test_app = FastAPI()

    @test_app.get("/health")
    async def health():
        return {"status": "ok"}

    test_app.add_middleware(
        CloudflareAccessMiddleware, jwks_client=_FakeJWKSClient(public_key), audience="test-aud"
    )
    client = TestClient(test_app)

    response = client.get("/health")

    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 3: Write `app/auth.py`**

```python
"""Cloudflare Access JWT verification."""

from __future__ import annotations

import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

HEADER_NAME = "Cf-Access-Jwt-Assertion"


class AccessTokenError(Exception):
    pass


def verify_access_token(token: str, jwks_client, audience: str) -> dict:
    """Verify a Cloudflare Access JWT and return its claims.

    Raises AccessTokenError if the token is missing, expired, or has the
    wrong audience/signature.
    """
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=audience)
    except jwt.PyJWTError as exc:
        raise AccessTokenError(str(exc)) from exc


class CloudflareAccessMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, jwks_client=None, audience: str | None = None):
        super().__init__(app)
        self._jwks_client = jwks_client or jwt.PyJWKClient(
            f"https://{settings.CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs"
        )
        self._audience = audience or settings.CF_ACCESS_AUD

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        token = request.headers.get(HEADER_NAME) or request.cookies.get("CF_Authorization")
        if not token:
            return JSONResponse({"detail": "Missing Cloudflare Access token"}, status_code=401)

        try:
            claims = verify_access_token(token, self._jwks_client, self._audience)
        except AccessTokenError as exc:
            return JSONResponse({"detail": f"Invalid Cloudflare Access token: {exc}"}, status_code=401)

        request.state.user_email = claims.get("email", "unknown")
        return await call_next(request)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_auth.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat: add Cloudflare Access JWT verification"
```

---

### Task 4: FastAPI app skeleton, base layout, and navigation

**Files:**
- Create: `app/main.py`
- Create: `app/templates/base.html`
- Create: `app/templates/dashboard.html`
- Create: `app/static/htmx.min.js` (downloaded)
- Modify: `tests/conftest.py` (add `client` fixture)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `app.auth.CloudflareAccessMiddleware` (Task 3), `app.config.settings` (Task 1), `app.db.get_session` (Task 2)
- Produces: `app.main.app` — the FastAPI instance, with `/health` and a placeholder `/` (renders `dashboard.html` with `data=None`); `tests/conftest.py::client` fixture (`TestClient` with `get_session` overridden to a temp per-test DB).

- [ ] **Step 1: Download htmx**

```bash
curl -L -o app/static/htmx.min.js https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js
```

- [ ] **Step 2: Write `app/templates/base.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{% block title %}Home & Family{% endblock %}</title>
  <script src="/static/htmx.min.js"></script>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; display: flex; }
    nav { width: 180px; padding: 1rem; border-right: 1px solid #ddd; }
    nav a { display: block; margin-bottom: 0.5rem; color: #333; text-decoration: none; }
    nav a:hover { text-decoration: underline; }
    main { flex: 1; padding: 1.5rem; }
    .needs-attention { color: #b00020; }
    .recently-changed { background: #fff8dc; }
  </style>
</head>
<body>
  <nav>
    <a href="/">Dashboard</a>
    <a href="/bills">Bills & Bank</a>
    <a href="/wiki">Wiki</a>
    <a href="/todos">To-Dos</a>
  </nav>
  <main>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 3: Write a placeholder `app/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}Dashboard — Home & Family{% endblock %}
{% block content %}
<h1>Dashboard</h1>
{% if data is none %}
<p>Loading…</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Write `app/main.py`**

```python
"""FastAPI application — routes, startup, dependency wiring."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import CloudflareAccessMiddleware
from app.config import settings

app = FastAPI(title="Home & Family Hub", version="0.1.0")

if settings.CF_ACCESS_TEAM_DOMAIN:
    app.add_middleware(CloudflareAccessMiddleware)

static_dir = Path("app/static")
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory="app/templates")
templates.env.cache_size = 0


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def dashboard_placeholder(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"data": None})
```

- [ ] **Step 5: Add the `client` fixture to `tests/conftest.py`**

```python
@pytest.fixture()
def client(engine):
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app

    def override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
```

- [ ] **Step 6: Write the failing test `tests/test_main.py`**

```python
def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_placeholder_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Bills & Bank" in response.text
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 8: Manual smoke check**

```bash
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000/` — confirm the nav renders and no CF Access token is required (since `CF_ACCESS_TEAM_DOMAIN` is unset in local `.env`).

- [ ] **Step 9: Commit**

```bash
git add app/main.py app/templates/base.html app/templates/dashboard.html app/static/htmx.min.js tests/conftest.py tests/test_main.py
git commit -m "feat: add FastAPI app skeleton, base layout, and navigation"
```

---

### Task 5: Category vocabulary and the Transaction model

**Files:**
- Create: `app/models/transaction.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_transaction_model.py`

**Interfaces:**
- Consumes: `app.models.document.Document` (Task 2, for the `document_id` foreign key)
- Produces: `app.models.transaction.Category` enum (`ELECTRICITY`, `WATER`, `GAS`, `TELECOM`, `INSURANCE`, `SUBSCRIPTIONS`, `GROCERIES`, `HEALTH`, `HOME`, `OTHER`); `app.models.transaction.Transaction` (fields: `id`, `document_id: int`, `provider: str`, `category: Category`, `amount: float`, `currency: str`, `due_date: Optional[date]`, `paid_date: Optional[date]`, `statement_period: Optional[str]`, `created_at: datetime`)

- [ ] **Step 1: Write the failing test `tests/test_transaction_model.py`**

```python
from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Transaction


def test_create_and_read_transaction(session):
    document = Document(
        filename="edp-august.pdf", file_path="/tmp/edp.pdf",
        content_hash="hash1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id,
        provider="EDP",
        category=Category.ELECTRICITY,
        amount=87.32,
        currency="EUR",
        statement_period="2026-08",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    assert transaction.id is not None
    assert transaction.category == Category.ELECTRICITY
    assert transaction.currency == "EUR"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transaction_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.transaction'`

- [ ] **Step 3: Write `app/models/transaction.py`**

```python
"""Transaction: an extracted line item from a Document, plus the controlled
category vocabulary."""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class Category(str, Enum):
    ELECTRICITY = "electricity"
    WATER = "water"
    GAS = "gas"
    TELECOM = "telecom"
    INSURANCE = "insurance"
    SUBSCRIPTIONS = "subscriptions"
    GROCERIES = "groceries"
    HEALTH = "health"
    HOME = "home"
    OTHER = "other"


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    provider: str
    category: Category = Field(default=Category.OTHER)
    amount: float
    currency: str = Field(default="EUR")
    due_date: Optional[date] = None
    paid_date: Optional[date] = None
    statement_period: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Update `app/models/__init__.py`**

```python
"""Imports every model module so SQLModel.metadata is fully populated."""

from app.models.document import Document  # noqa: F401
from app.models.transaction import Category, Transaction  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_transaction_model.py -v`
Expected: PASS

- [ ] **Step 6: Generate and apply the migration**

```bash
alembic revision --autogenerate -m "add transactions table"
```

Open the generated file and confirm it creates a `transactions` table with a foreign key to `documents.id`. Then:

```bash
alembic upgrade head
```

- [ ] **Step 7: Commit**

```bash
git add app/models/transaction.py app/models/__init__.py alembic/versions/ tests/test_transaction_model.py
git commit -m "feat: add Category vocabulary and the Transaction model"
```

---

### Task 6: File storage service

**Files:**
- Create: `app/services/storage.py`
- Create: `app/services/__init__.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `app.config.settings.DOCUMENTS_DIR` (Task 1)
- Produces: `app.services.storage.save_upload(filename: str, content: bytes) -> tuple[str, str]` (returns `(file_path, content_hash)`)

- [ ] **Step 1: Write the failing tests `tests/test_storage.py`**

```python
import hashlib
from pathlib import Path

from app.services.storage import save_upload


def test_save_upload_writes_file_and_returns_hash(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path)

    file_path, content_hash = save_upload("bill.pdf", b"hello world")

    assert Path(file_path).exists()
    assert Path(file_path).read_bytes() == b"hello world"
    assert content_hash == hashlib.sha256(b"hello world").hexdigest()


def test_save_upload_generates_unique_filenames(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path)

    path_a, _ = save_upload("bill.pdf", b"content-a")
    path_b, _ = save_upload("bill.pdf", b"content-b")

    assert path_a != path_b
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_storage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services'`

- [ ] **Step 3: Create `app/services/__init__.py` (empty) and write `app/services/storage.py`**

```python
"""Saves uploaded files to disk and computes content hashes for dedup."""

import hashlib
import uuid
from pathlib import Path

from app.config import settings


def save_upload(filename: str, content: bytes) -> tuple[str, str]:
    """Save `content` under DOCUMENTS_DIR with a unique name, returning
    (file_path, content_hash)."""
    settings.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(content).hexdigest()
    suffix = Path(filename).suffix
    unique_name = f"{uuid.uuid4().hex}{suffix}"
    file_path = settings.DOCUMENTS_DIR / unique_name
    file_path.write_bytes(content)
    return str(file_path), content_hash
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/__init__.py app/services/storage.py tests/test_storage.py
git commit -m "feat: add file storage service"
```

---

### Task 7: Claude extraction service

**Files:**
- Create: `app/services/extraction.py`
- Test: `tests/test_extraction.py`

**Interfaces:**
- Consumes: `app.config.settings.ANTHROPIC_API_KEY` (Task 1)
- Produces: `app.services.extraction.ExtractedBill` dataclass (`provider: str`, `category_hint: str`, `amount: float`, `currency: str`, `due_date: Optional[date]`, `paid_date: Optional[date]`, `statement_period: Optional[str]`); `app.services.extraction.ExtractionError(Exception)`; `app.services.extraction.ensure_image(file_path: str) -> str` (converts PDF's first page to PNG, passes through image files); `app.services.extraction.extract_bill(image_path: str, client=None) -> ExtractedBill` (async, `client` injectable for tests)

- [ ] **Step 1: Write the failing tests `tests/test_extraction.py`**

```python
import json

import pytest

from app.services.extraction import ExtractedBill, ExtractionError, extract_bill


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text

    async def create(self, **kwargs):
        return _FakeMessage(self._response_text)


class _FakeAnthropicClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


@pytest.mark.asyncio
async def test_extract_bill_parses_valid_response(tmp_path):
    image_path = tmp_path / "page1.png"
    image_path.write_bytes(b"fake-png-bytes")
    response = json.dumps({
        "provider": "EDP", "category_hint": "electricity", "amount": 87.32,
        "currency": "EUR", "due_date": "2026-09-05", "paid_date": None,
        "statement_period": "2026-08",
    })
    client = _FakeAnthropicClient(response)

    result = await extract_bill(str(image_path), client=client)

    assert isinstance(result, ExtractedBill)
    assert result.provider == "EDP"
    assert result.amount == 87.32
    assert result.due_date.isoformat() == "2026-09-05"
    assert result.paid_date is None


@pytest.mark.asyncio
async def test_extract_bill_raises_on_malformed_json(tmp_path):
    image_path = tmp_path / "page1.png"
    image_path.write_bytes(b"fake-png-bytes")
    client = _FakeAnthropicClient("not json")

    with pytest.raises(ExtractionError):
        await extract_bill(str(image_path), client=client)


@pytest.mark.asyncio
async def test_extract_bill_raises_when_provider_missing(tmp_path):
    image_path = tmp_path / "page1.png"
    image_path.write_bytes(b"fake-png-bytes")
    response = json.dumps({"category_hint": "electricity", "amount": 10.0})
    client = _FakeAnthropicClient(response)

    with pytest.raises(ExtractionError):
        await extract_bill(str(image_path), client=client)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_extraction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.extraction'`

- [ ] **Step 3: Write `app/services/extraction.py`**

```python
"""Claude-based structured extraction from bill/statement documents."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from anthropic import AsyncAnthropic

from app.config import settings

_MODEL = "claude-sonnet-5"

_SYSTEM_PROMPT = """You extract structured billing data from a bill or bank \
statement image. Respond with ONLY a JSON object, no prose, matching this \
shape exactly:

{
  "provider": "string, the company/entity that issued the bill",
  "category_hint": "one lowercase word: electricity, water, gas, telecom, \
insurance, subscriptions, groceries, health, home, or other",
  "amount": 0.00,
  "currency": "3-letter ISO code, default EUR",
  "due_date": "YYYY-MM-DD or null",
  "paid_date": "YYYY-MM-DD or null",
  "statement_period": "YYYY-MM or null"
}

If a field cannot be determined, use null (or 0.0 for amount as a last resort)."""


class ExtractionError(Exception):
    pass


@dataclass
class ExtractedBill:
    provider: str
    category_hint: str
    amount: float
    currency: str
    due_date: Optional[date]
    paid_date: Optional[date]
    statement_period: Optional[str]


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value)


def ensure_image(file_path: str) -> str:
    """Return a path to a PNG image representing the first page of the given
    file, converting from PDF if necessary."""
    path = Path(file_path)
    if path.suffix.lower() == ".pdf":
        from pdf2image import convert_from_path

        pages = convert_from_path(str(path), first_page=1, last_page=1)
        image_path = path.with_suffix(".page1.png")
        pages[0].save(image_path, "PNG")
        return str(image_path)
    return str(path)


async def extract_bill(image_path: str, client: Optional[AsyncAnthropic] = None) -> ExtractedBill:
    """Extract structured billing data from an image of a bill/statement page."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    image_bytes = Path(image_path).read_bytes()
    media_type = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"

    message = await anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_bytes).decode(),
                        },
                    },
                    {"type": "text", "text": "Extract the billing data as JSON."},
                ],
            }
        ],
    )

    raw_text = message.content[0].text
    try:
        data = json.loads(raw_text)
        return ExtractedBill(
            provider=data["provider"],
            category_hint=data.get("category_hint", "other"),
            amount=float(data.get("amount") or 0.0),
            currency=data.get("currency") or "EUR",
            due_date=_parse_date(data.get("due_date")),
            paid_date=_parse_date(data.get("paid_date")),
            statement_period=data.get("statement_period"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ExtractionError(f"Could not parse extraction response: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_extraction.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/extraction.py tests/test_extraction.py
git commit -m "feat: add Claude-based bill extraction service"
```

---

### Task 8: Categorization service

**Files:**
- Create: `app/services/categorization.py`
- Test: `tests/test_categorization.py`

**Interfaces:**
- Consumes: `app.models.transaction.Category` (Task 5)
- Produces: `app.services.categorization.normalize_category(hint: str) -> Category`

- [ ] **Step 1: Write the failing test `tests/test_categorization.py`**

```python
import pytest

from app.models.transaction import Category
from app.services.categorization import normalize_category


@pytest.mark.parametrize("hint,expected", [
    ("electricity", Category.ELECTRICITY),
    ("Power", Category.ELECTRICITY),
    ("water", Category.WATER),
    ("internet", Category.TELECOM),
    ("streaming", Category.SUBSCRIPTIONS),
    ("supermarket", Category.GROCERIES),
    ("pharmacy", Category.HEALTH),
    ("maintenance", Category.HOME),
    ("something-unrecognized", Category.OTHER),
    ("", Category.OTHER),
])
def test_normalize_category(hint, expected):
    assert normalize_category(hint) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_categorization.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.categorization'`

- [ ] **Step 3: Write `app/services/categorization.py`**

```python
"""Normalizes free-text category hints from extraction into the controlled
Category vocabulary."""

from app.models.transaction import Category

_SYNONYMS: dict[str, Category] = {
    "electricity": Category.ELECTRICITY,
    "power": Category.ELECTRICITY,
    "energy": Category.ELECTRICITY,
    "water": Category.WATER,
    "gas": Category.GAS,
    "telecom": Category.TELECOM,
    "telco": Category.TELECOM,
    "internet": Category.TELECOM,
    "phone": Category.TELECOM,
    "mobile": Category.TELECOM,
    "insurance": Category.INSURANCE,
    "subscriptions": Category.SUBSCRIPTIONS,
    "subscription": Category.SUBSCRIPTIONS,
    "streaming": Category.SUBSCRIPTIONS,
    "groceries": Category.GROCERIES,
    "grocery": Category.GROCERIES,
    "supermarket": Category.GROCERIES,
    "health": Category.HEALTH,
    "medical": Category.HEALTH,
    "pharmacy": Category.HEALTH,
    "home": Category.HOME,
    "maintenance": Category.HOME,
}


def normalize_category(hint: str) -> Category:
    """Map a free-text category hint to the closest controlled Category,
    falling back to OTHER when nothing matches."""
    if not hint:
        return Category.OTHER
    return _SYNONYMS.get(hint.strip().lower(), Category.OTHER)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_categorization.py -v`
Expected: PASS (10 parametrized cases)

- [ ] **Step 5: Commit**

```bash
git add app/services/categorization.py tests/test_categorization.py
git commit -m "feat: add category normalization service"
```

---

### Task 9: Deduplication service

**Files:**
- Create: `app/services/dedup.py`
- Test: `tests/test_dedup.py`

**Interfaces:**
- Consumes: `app.models.document.Document`, `app.models.transaction.Transaction` (Tasks 2, 5)
- Produces: `app.services.dedup.find_existing_document_by_hash(session, content_hash: str) -> Optional[Document]`; `app.services.dedup.find_duplicate_transaction(session, provider: str, statement_period: Optional[str]) -> Optional[Transaction]`

- [ ] **Step 1: Write the failing tests `tests/test_dedup.py`**

```python
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction
from app.services.dedup import find_duplicate_transaction, find_existing_document_by_hash


def test_find_existing_document_by_hash_returns_match(session):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="abc123",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()

    found = find_existing_document_by_hash(session, "abc123")

    assert found is not None
    assert found.filename == "bill.pdf"


def test_find_existing_document_by_hash_returns_none_when_missing(session):
    assert find_existing_document_by_hash(session, "does-not-exist") is None


def test_find_duplicate_transaction_matches_provider_and_period(session):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="hash1",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=50.0, currency="EUR", statement_period="2026-08",
    )
    session.add(transaction)
    session.commit()

    found = find_duplicate_transaction(session, provider="EDP", statement_period="2026-08")

    assert found is not None
    assert found.id == transaction.id


def test_find_duplicate_transaction_returns_none_without_statement_period(session):
    assert find_duplicate_transaction(session, provider="EDP", statement_period=None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dedup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.dedup'`

- [ ] **Step 3: Write `app/services/dedup.py`**

```python
"""Duplicate detection for ingested documents and transactions."""

from typing import Optional

from sqlmodel import Session, select

from app.models.document import Document
from app.models.transaction import Transaction


def find_existing_document_by_hash(session: Session, content_hash: str) -> Optional[Document]:
    statement = select(Document).where(Document.content_hash == content_hash)
    return session.exec(statement).first()


def find_duplicate_transaction(
    session: Session, provider: str, statement_period: Optional[str]
) -> Optional[Transaction]:
    """A transaction is a duplicate if the same provider already has a
    transaction for the same statement period."""
    if not statement_period:
        return None
    statement = select(Transaction).where(
        Transaction.provider == provider,
        Transaction.statement_period == statement_period,
    )
    return session.exec(statement).first()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dedup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/dedup.py tests/test_dedup.py
git commit -m "feat: add document and transaction deduplication service"
```

---

### Task 10: To-Do model and generation service

**Files:**
- Create: `app/models/todo.py`
- Modify: `app/models/__init__.py`
- Create: `app/services/todo_engine.py`
- Test: `tests/test_todo_model.py`, `tests/test_todo_engine.py`

**Interfaces:**
- Consumes: `app.models.transaction.Transaction` (Task 5)
- Produces: `app.models.todo.Todo` (fields: `id`, `title: str`, `due_date: Optional[date]`, `done: bool`, `transaction_id: Optional[int]`, `created_at: datetime`); `app.services.todo_engine.generate_todo_for_transaction(session, transaction: Transaction) -> Optional[Todo]`

- [ ] **Step 1: Write the failing test `tests/test_todo_model.py`**

```python
from datetime import date

from app.models.todo import Todo


def test_create_and_read_todo(session):
    todo = Todo(title="Pay EDP", due_date=date(2026, 9, 5))
    session.add(todo)
    session.commit()
    session.refresh(todo)

    assert todo.id is not None
    assert todo.done is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_todo_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.todo'`

- [ ] **Step 3: Write `app/models/todo.py`**

```python
"""Todo: a simple open/done task, optionally generated from a Transaction's
due date."""

from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Todo(SQLModel, table=True):
    __tablename__ = "todos"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    due_date: Optional[date] = None
    done: bool = Field(default=False)
    transaction_id: Optional[int] = Field(default=None, foreign_key="transactions.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Update `app/models/__init__.py`**

```python
"""Imports every model module so SQLModel.metadata is fully populated."""

from app.models.document import Document  # noqa: F401
from app.models.transaction import Category, Transaction  # noqa: F401
from app.models.todo import Todo  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_todo_model.py -v`
Expected: PASS

- [ ] **Step 6: Generate and apply the migration**

```bash
alembic revision --autogenerate -m "add todos table"
alembic upgrade head
```

- [ ] **Step 7: Write the failing test `tests/test_todo_engine.py`**

```python
from datetime import date

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction
from app.services.todo_engine import generate_todo_for_transaction


def _make_transaction(session, due_date=None):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="hash1",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", due_date=due_date,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return transaction


def test_generate_todo_for_transaction_with_due_date(session):
    transaction = _make_transaction(session, due_date=date(2026, 9, 5))

    todo = generate_todo_for_transaction(session, transaction)

    assert todo is not None
    assert todo.due_date == date(2026, 9, 5)
    assert "EDP" in todo.title
    assert todo.transaction_id == transaction.id


def test_generate_todo_for_transaction_without_due_date_returns_none(session):
    transaction = _make_transaction(session, due_date=None)

    todo = generate_todo_for_transaction(session, transaction)

    assert todo is None
```

- [ ] **Step 8: Run test to verify it fails**

Run: `pytest tests/test_todo_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.todo_engine'`

- [ ] **Step 9: Write `app/services/todo_engine.py`**

```python
"""Generates To-Do entries from transaction due dates."""

from typing import Optional

from sqlmodel import Session

from app.models.todo import Todo
from app.models.transaction import Transaction


def generate_todo_for_transaction(session: Session, transaction: Transaction) -> Optional[Todo]:
    """Create a Todo for a transaction's due date, if it has one."""
    if transaction.due_date is None:
        return None
    todo = Todo(
        title=f"Pay {transaction.provider} — {transaction.amount:.2f} {transaction.currency}",
        due_date=transaction.due_date,
        transaction_id=transaction.id,
    )
    session.add(todo)
    session.commit()
    session.refresh(todo)
    return todo
```

- [ ] **Step 10: Run test to verify it passes**

Run: `pytest tests/test_todo_engine.py -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add app/models/todo.py app/models/__init__.py app/services/todo_engine.py alembic/versions/ tests/test_todo_model.py tests/test_todo_engine.py
git commit -m "feat: add Todo model and due-date-based generation"
```

---

### Task 11: Wiki models and the wiki-fact assessment pass

**Files:**
- Create: `app/models/wiki.py`
- Modify: `app/models/__init__.py`
- Create: `app/services/wiki_engine.py`
- Test: `tests/test_wiki_model.py`, `tests/test_wiki_engine.py`

**Interfaces:**
- Consumes: `app.config.settings.ANTHROPIC_API_KEY` (Task 1), `app.models.document.Document`, `app.models.transaction.Transaction` (Tasks 2, 5)
- Produces: `app.models.wiki.WikiPage` (fields: `id`, `topic: str` (unique), `facts_json: str`, `updated_at: datetime`); `app.models.wiki.WikiChange` (fields: `id`, `wiki_page_id: int`, `fact_key: str`, `old_value: Optional[str]`, `new_value: str`, `document_id: Optional[int]`, `changed_at: datetime`); `app.services.wiki_engine.assess_and_update_wiki(session, document: Document, transaction: Transaction, client=None) -> Optional[WikiChange]` (async)

- [ ] **Step 1: Write the failing test `tests/test_wiki_model.py`**

```python
from app.models.wiki import WikiChange, WikiPage


def test_create_wiki_page_and_change(session):
    page = WikiPage(topic="Electricity — provider & contract", facts_json='{"provider": "EDP"}')
    session.add(page)
    session.commit()
    session.refresh(page)

    change = WikiChange(
        wiki_page_id=page.id, fact_key="provider", old_value=None, new_value="EDP",
    )
    session.add(change)
    session.commit()
    session.refresh(change)

    assert page.id is not None
    assert change.wiki_page_id == page.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wiki_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.wiki'`

- [ ] **Step 3: Write `app/models/wiki.py`**

```python
"""WikiPage: current standing facts for a topic. WikiChange: its history."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class WikiPage(SQLModel, table=True):
    __tablename__ = "wiki_pages"

    id: Optional[int] = Field(default=None, primary_key=True)
    topic: str = Field(unique=True, index=True)
    facts_json: str = Field(default="{}")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WikiChange(SQLModel, table=True):
    __tablename__ = "wiki_changes"

    id: Optional[int] = Field(default=None, primary_key=True)
    wiki_page_id: int = Field(foreign_key="wiki_pages.id", index=True)
    fact_key: str
    old_value: Optional[str] = None
    new_value: str
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id")
    changed_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Update `app/models/__init__.py`**

```python
"""Imports every model module so SQLModel.metadata is fully populated."""

from app.models.document import Document  # noqa: F401
from app.models.transaction import Category, Transaction  # noqa: F401
from app.models.todo import Todo  # noqa: F401
from app.models.wiki import WikiChange, WikiPage  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_wiki_model.py -v`
Expected: PASS

- [ ] **Step 6: Generate and apply the migration**

```bash
alembic revision --autogenerate -m "add wiki_pages and wiki_changes tables"
alembic upgrade head
```

- [ ] **Step 7: Write the failing tests `tests/test_wiki_engine.py`**

```python
import json

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction
from app.models.wiki import WikiPage
from app.services.wiki_engine import assess_and_update_wiki


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text

    async def create(self, **kwargs):
        return _FakeMessage(self._response_text)


class _FakeAnthropicClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


def _make_document_and_transaction(session):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="hash1",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-08",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return document, transaction


@pytest.mark.asyncio
async def test_creates_new_wiki_page_when_worthy(session):
    document, transaction = _make_document_and_transaction(session)
    response = json.dumps({
        "wiki_worthy": True,
        "topic": "Electricity — provider & contract",
        "facts": {"provider": "EDP", "tariff": "Bi-horário"},
    })
    client = _FakeAnthropicClient(response)

    change = await assess_and_update_wiki(session, document, transaction, client=client)

    assert change is not None
    page = session.exec(
        select(WikiPage).where(WikiPage.topic == "Electricity — provider & contract")
    ).first()
    assert page is not None
    assert json.loads(page.facts_json) == {"provider": "EDP", "tariff": "Bi-horário"}


@pytest.mark.asyncio
async def test_returns_none_when_not_worthy(session):
    document, transaction = _make_document_and_transaction(session)
    response = json.dumps({"wiki_worthy": False, "topic": "", "facts": {}})
    client = _FakeAnthropicClient(response)

    change = await assess_and_update_wiki(session, document, transaction, client=client)

    assert change is None


@pytest.mark.asyncio
async def test_updates_existing_page_and_records_change(session):
    document, transaction = _make_document_and_transaction(session)
    existing_page = WikiPage(
        topic="Electricity — provider & contract",
        facts_json=json.dumps({"provider": "EDP", "tariff": "Simples"}),
    )
    session.add(existing_page)
    session.commit()

    response = json.dumps({
        "wiki_worthy": True,
        "topic": "Electricity — provider & contract",
        "facts": {"tariff": "Bi-horário"},
    })
    client = _FakeAnthropicClient(response)

    change = await assess_and_update_wiki(session, document, transaction, client=client)

    assert change is not None
    assert change.fact_key == "tariff"
    session.refresh(existing_page)
    assert json.loads(existing_page.facts_json) == {"provider": "EDP", "tariff": "Bi-horário"}


@pytest.mark.asyncio
async def test_no_op_when_facts_unchanged(session):
    document, transaction = _make_document_and_transaction(session)
    existing_page = WikiPage(
        topic="Electricity — provider & contract",
        facts_json=json.dumps({"provider": "EDP"}),
    )
    session.add(existing_page)
    session.commit()

    response = json.dumps({
        "wiki_worthy": True,
        "topic": "Electricity — provider & contract",
        "facts": {"provider": "EDP"},
    })
    client = _FakeAnthropicClient(response)

    change = await assess_and_update_wiki(session, document, transaction, client=client)

    assert change is None
```

- [ ] **Step 8: Run tests to verify they fail**

Run: `pytest tests/test_wiki_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.wiki_engine'`

- [ ] **Step 9: Write `app/services/wiki_engine.py`**

```python
"""Second-pass LLM assessment: decides whether an ingested document should
update a WikiPage, and applies the update if so."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from anthropic import AsyncAnthropic
from sqlmodel import Session, select

from app.config import settings
from app.models.document import Document
from app.models.transaction import Transaction
from app.models.wiki import WikiChange, WikiPage

_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """You decide whether a bill/statement contains a standing \
fact worth remembering (e.g. current provider, contract/policy number, \
tariff, renewal date) as opposed to purely transactional data (an amount \
due this month). Respond with ONLY a JSON object:

{
  "wiki_worthy": true or false,
  "topic": "short human title, e.g. 'Electricity — provider & contract'",
  "facts": {"key": "value", ...}
}

If not wiki-worthy, set wiki_worthy to false and leave topic/facts empty."""


async def assess_and_update_wiki(
    session: Session,
    document: Document,
    transaction: Transaction,
    client: Optional[AsyncAnthropic] = None,
) -> Optional[WikiChange]:
    """Ask Claude whether this transaction/document contains wiki-worthy
    facts, and if so, upsert the relevant WikiPage and record a WikiChange."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = await anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Provider: {transaction.provider}\n"
                    f"Category: {transaction.category.value}\n"
                    f"Statement period: {transaction.statement_period}\n"
                ),
            }
        ],
    )
    data = json.loads(message.content[0].text)
    if not data.get("wiki_worthy"):
        return None

    topic = data["topic"]
    new_facts: dict = data.get("facts", {})

    page = session.exec(select(WikiPage).where(WikiPage.topic == topic)).first()
    if page is None:
        page = WikiPage(topic=topic, facts_json=json.dumps(new_facts))
        session.add(page)
        session.commit()
        session.refresh(page)
        change = WikiChange(
            wiki_page_id=page.id, fact_key="*", old_value=None,
            new_value=json.dumps(new_facts), document_id=document.id,
        )
        session.add(change)
        session.commit()
        session.refresh(change)
        return change

    old_facts: dict = json.loads(page.facts_json)
    merged_facts = {**old_facts, **new_facts}
    changed_keys = {k: v for k, v in new_facts.items() if old_facts.get(k) != v}
    if not changed_keys:
        return None

    page.facts_json = json.dumps(merged_facts)
    page.updated_at = datetime.utcnow()
    session.add(page)

    change = WikiChange(
        wiki_page_id=page.id,
        fact_key=",".join(changed_keys.keys()),
        old_value=json.dumps({k: old_facts.get(k) for k in changed_keys}),
        new_value=json.dumps(changed_keys),
        document_id=document.id,
    )
    session.add(change)
    session.commit()
    session.refresh(change)
    return change
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `pytest tests/test_wiki_engine.py -v`
Expected: PASS (4 tests)

- [ ] **Step 11: Commit**

```bash
git add app/models/wiki.py app/models/__init__.py app/services/wiki_engine.py alembic/versions/ tests/test_wiki_model.py tests/test_wiki_engine.py
git commit -m "feat: add WikiPage/WikiChange models and the wiki-fact assessment pass"
```

---

### Task 12: Pipeline orchestration

**Files:**
- Create: `app/services/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `extract_bill`, `ensure_image`, `ExtractionError` (Task 7); `normalize_category` (Task 8); `find_duplicate_transaction` (Task 9); `generate_todo_for_transaction` (Task 10); `assess_and_update_wiki` (Task 11)
- Produces: `app.services.pipeline.ingest_document(session, document: Document) -> Document` (async) — the single entry point every ingestion channel calls.

- [ ] **Step 1: Write the failing tests `tests/test_pipeline.py`**

```python
from datetime import date

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction
from app.services import pipeline
from app.services.extraction import ExtractedBill, ExtractionError


@pytest.mark.asyncio
async def test_ingest_document_creates_transaction(session, monkeypatch, tmp_path):
    document = Document(
        filename="bill.pdf", file_path=str(tmp_path / "bill.pdf"), content_hash="hash1",
        source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    extracted = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
        due_date=date(2026, 9, 5), paid_date=None, statement_period="2026-08",
    )

    async def fake_extract_bill(image_path, client=None):
        return extracted

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "ensure_image", lambda path: path)
    monkeypatch.setattr(pipeline, "assess_and_update_wiki", fake_assess_and_update_wiki)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.PROCESSED
    transaction = session.exec(
        select(Transaction).where(Transaction.document_id == document.id)
    ).first()
    assert transaction is not None
    assert transaction.provider == "EDP"
    assert transaction.category == Category.ELECTRICITY
    assert transaction.amount == 87.32


@pytest.mark.asyncio
async def test_ingest_document_marks_needs_attention_on_extraction_failure(session, monkeypatch, tmp_path):
    document = Document(
        filename="bad.pdf", file_path=str(tmp_path / "bad.pdf"), content_hash="hash2",
        source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    async def failing_extract_bill(image_path, client=None):
        raise ExtractionError("could not parse")

    monkeypatch.setattr(pipeline, "extract_bill", failing_extract_bill)
    monkeypatch.setattr(pipeline, "ensure_image", lambda path: path)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.NEEDS_ATTENTION
    assert "could not parse" in result.failure_reason


@pytest.mark.asyncio
async def test_ingest_document_skips_duplicate_transaction(session, monkeypatch, tmp_path):
    existing_document = Document(
        filename="first.pdf", file_path=str(tmp_path / "first.pdf"), content_hash="hash-a",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(existing_document)
    session.commit()
    session.refresh(existing_document)

    session.add(Transaction(
        document_id=existing_document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-08",
    ))
    session.commit()

    new_document = Document(
        filename="duplicate.pdf", file_path=str(tmp_path / "duplicate.pdf"), content_hash="hash-b",
        source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
    )
    session.add(new_document)
    session.commit()
    session.refresh(new_document)

    extracted = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
        due_date=None, paid_date=None, statement_period="2026-08",
    )

    async def fake_extract_bill(image_path, client=None):
        return extracted

    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "ensure_image", lambda path: path)

    result = await pipeline.ingest_document(session, new_document)

    assert result.status == DocumentStatus.PROCESSED
    assert "duplicate" in result.failure_reason
    transactions = session.exec(
        select(Transaction).where(Transaction.document_id == new_document.id)
    ).all()
    assert transactions == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.pipeline'`

- [ ] **Step 3: Write `app/services/pipeline.py`**

```python
"""Orchestrates the document ingestion pipeline: extract -> categorize ->
dedup -> persist -> todo -> wiki. Every ingestion channel (manual upload,
email, bank sync) calls ingest_document as its single entry point."""

from __future__ import annotations

from sqlmodel import Session

from app.models.document import Document, DocumentStatus
from app.models.transaction import Transaction
from app.services.categorization import normalize_category
from app.services.dedup import find_duplicate_transaction
from app.services.extraction import ExtractedBill, ExtractionError, ensure_image, extract_bill
from app.services.todo_engine import generate_todo_for_transaction
from app.services.wiki_engine import assess_and_update_wiki


async def ingest_document(session: Session, document: Document) -> Document:
    """Run the full ingestion pipeline for a Document already saved to disk.
    Updates and persists the Document's status before returning it."""
    try:
        image_path = ensure_image(document.file_path)
        extracted: ExtractedBill = await extract_bill(image_path)
    except (ExtractionError, OSError) as exc:
        document.status = DocumentStatus.NEEDS_ATTENTION
        document.failure_reason = str(exc)
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    duplicate = find_duplicate_transaction(
        session, provider=extracted.provider, statement_period=extracted.statement_period
    )
    if duplicate is not None:
        document.status = DocumentStatus.PROCESSED
        document.failure_reason = "duplicate — matched existing transaction"
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    transaction = Transaction(
        document_id=document.id,
        provider=extracted.provider,
        category=normalize_category(extracted.category_hint),
        amount=extracted.amount,
        currency=extracted.currency,
        due_date=extracted.due_date,
        paid_date=extracted.paid_date,
        statement_period=extracted.statement_period,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    generate_todo_for_transaction(session, transaction)
    await assess_and_update_wiki(session, document, transaction)

    document.status = DocumentStatus.PROCESSED
    document.failure_reason = None
    session.add(document)
    session.commit()
    session.refresh(document)
    return document
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/pipeline.py tests/test_pipeline.py
git commit -m "feat: add pipeline orchestration for document ingestion"
```

---

### Task 13: Manual upload route and bills list/detail pages

**Files:**
- Create: `app/routers/__init__.py`
- Create: `app/routers/bills.py`
- Create: `app/templates/bills/upload.html`, `app/templates/bills/list.html`, `app/templates/bills/detail.html`
- Modify: `app/main.py`
- Test: `tests/test_bills_router.py`

**Interfaces:**
- Consumes: `app.db.get_session` (Task 2); `app.services.storage.save_upload` (Task 6); `app.services.dedup.find_existing_document_by_hash` (Task 9); `app.services.pipeline.ingest_document` (Task 12)
- Produces: `app.routers.bills.router` (FastAPI `APIRouter`, prefix `/bills`) — `GET /bills`, `GET /bills/upload`, `POST /bills/upload`, `GET /bills/{document_id}`

- [ ] **Step 1: Write the failing tests `tests/test_bills_router.py`**

```python
import io

import app.routers.bills as bills_router
from app.models.document import DocumentStatus


def test_upload_bill_creates_document_and_redirects(client, monkeypatch):
    async def fake_ingest_document(session, document):
        document.status = DocumentStatus.PROCESSED
        return document

    monkeypatch.setattr(bills_router, "ingest_document", fake_ingest_document)

    response = client.post(
        "/bills/upload",
        files={"file": ("bill.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/bills/")


def test_list_bills_renders(client):
    response = client.get("/bills")
    assert response.status_code == 200
    assert "Bills" in response.text


def test_bill_detail_404_for_missing_document(client):
    response = client.get("/bills/9999")
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bills_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.routers'`

- [ ] **Step 3: Create `app/routers/__init__.py` (empty) and write `app/routers/bills.py`**

```python
"""Routes for manual bill/statement upload and browsing."""

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Transaction
from app.services.dedup import find_existing_document_by_hash
from app.services.pipeline import ingest_document
from app.services.storage import save_upload

router = APIRouter(prefix="/bills", tags=["bills"])
templates = Jinja2Templates(directory="app/templates")


@router.get("")
async def list_bills(request: Request, session: Session = Depends(get_session)):
    documents = session.exec(select(Document).order_by(Document.created_at.desc())).all()
    return templates.TemplateResponse(request, "bills/list.html", {"documents": documents})


@router.get("/upload")
async def upload_form(request: Request):
    return templates.TemplateResponse(request, "bills/upload.html", {})


@router.post("/upload")
async def upload_bill(request: Request, file: UploadFile, session: Session = Depends(get_session)):
    content = await file.read()
    file_path, content_hash = save_upload(file.filename, content)

    existing = find_existing_document_by_hash(session, content_hash)
    if existing is not None:
        return RedirectResponse(f"/bills/{existing.id}", status_code=303)

    document = Document(
        filename=file.filename, file_path=file_path, content_hash=content_hash,
        source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    document = await ingest_document(session, document)
    return RedirectResponse(f"/bills/{document.id}", status_code=303)


@router.get("/{document_id}")
async def bill_detail(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    transaction = session.exec(
        select(Transaction).where(Transaction.document_id == document_id)
    ).first()
    return templates.TemplateResponse(
        request, "bills/detail.html", {"document": document, "transaction": transaction}
    )
```

- [ ] **Step 4: Write the templates**

`app/templates/bills/upload.html`:

```html
{% extends "base.html" %}
{% block title %}Upload — Bills & Bank{% endblock %}
{% block content %}
<h1>Upload a bill or statement</h1>
<form action="/bills/upload" method="post" enctype="multipart/form-data">
  <input type="file" name="file" required>
  <button type="submit">Upload</button>
</form>
{% endblock %}
```

`app/templates/bills/list.html`:

```html
{% extends "base.html" %}
{% block title %}Bills & Bank{% endblock %}
{% block content %}
<h1>Bills & Bank</h1>
<p><a href="/bills/upload">Upload a new document</a></p>
<table>
  <thead><tr><th>File</th><th>Status</th><th>Uploaded</th></tr></thead>
  <tbody>
    {% for document in documents %}
    <tr>
      <td><a href="/bills/{{ document.id }}">{{ document.filename }}</a></td>
      <td class="{{ 'needs-attention' if document.status.value == 'needs_attention' else '' }}">{{ document.status.value }}</td>
      <td>{{ document.created_at }}</td>
    </tr>
    {% else %}
    <tr><td colspan="3">No documents yet.</td></tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

`app/templates/bills/detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ document.filename }} — Bills & Bank{% endblock %}
{% block content %}
<h1>{{ document.filename }}</h1>
<p>Status: {{ document.status.value }}</p>
{% if document.failure_reason %}
<p class="needs-attention">{{ document.failure_reason }}</p>
{% endif %}
{% if transaction %}
<dl>
  <dt>Provider</dt><dd>{{ transaction.provider }}</dd>
  <dt>Category</dt><dd>{{ transaction.category.value }}</dd>
  <dt>Amount</dt><dd>{{ "%.2f"|format(transaction.amount) }} {{ transaction.currency }}</dd>
  <dt>Due date</dt><dd>{{ transaction.due_date or "—" }}</dd>
  <dt>Statement period</dt><dd>{{ transaction.statement_period or "—" }}</dd>
</dl>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Wire the router into `app/main.py`**

Add near the top-level imports:

```python
from app.routers import bills
```

Add after the `/` route definition:

```python
app.include_router(bills.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_bills_router.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/routers/ app/templates/bills/ app/main.py tests/test_bills_router.py
git commit -m "feat: add manual bill upload route and bills list/detail pages"
```

---

### Task 14: To-Dos router and pages

**Files:**
- Create: `app/routers/todos.py`
- Create: `app/templates/todos/list.html`, `app/templates/todos/_lists.html`
- Modify: `app/main.py`
- Test: `tests/test_todos_router.py`

**Interfaces:**
- Consumes: `app.db.get_session` (Task 2), `app.models.todo.Todo` (Task 10)
- Produces: `app.routers.todos.router` (prefix `/todos`) — `GET /todos`, `POST /todos/{todo_id}/done`

- [ ] **Step 1: Write the failing tests `tests/test_todos_router.py`**

```python
from datetime import date

from app.models.todo import Todo


def test_list_todos_renders(client, session):
    session.add(Todo(title="Pay EDP", due_date=date(2026, 9, 5)))
    session.commit()

    response = client.get("/todos")

    assert response.status_code == 200
    assert "Pay EDP" in response.text


def test_mark_done_updates_status(client, session):
    todo = Todo(title="Pay water", due_date=date(2026, 9, 1))
    session.add(todo)
    session.commit()
    session.refresh(todo)

    response = client.post(f"/todos/{todo.id}/done")

    assert response.status_code == 200
    session.refresh(todo)
    assert todo.done is True


def test_mark_done_404_for_missing_todo(client):
    response = client.post("/todos/9999/done")
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_todos_router.py -v`
Expected: FAIL — `404` for `/todos` (route not registered) causing assertion failures / `ModuleNotFoundError` if the router module doesn't exist yet.

- [ ] **Step 3: Write `app/routers/todos.py`**

```python
"""Routes for the To-Do list."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models.todo import Todo

router = APIRouter(prefix="/todos", tags=["todos"])
templates = Jinja2Templates(directory="app/templates")


def _todo_lists(session: Session):
    open_todos = session.exec(
        select(Todo).where(Todo.done == False).order_by(Todo.due_date)  # noqa: E712
    ).all()
    done_todos = session.exec(
        select(Todo).where(Todo.done == True).order_by(Todo.due_date.desc())  # noqa: E712
    ).all()
    return open_todos, done_todos


@router.get("")
async def list_todos(request: Request, session: Session = Depends(get_session)):
    open_todos, done_todos = _todo_lists(session)
    return templates.TemplateResponse(
        request, "todos/list.html", {"open_todos": open_todos, "done_todos": done_todos}
    )


@router.post("/{todo_id}/done")
async def mark_done(request: Request, todo_id: int, session: Session = Depends(get_session)):
    todo = session.get(Todo, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    todo.done = True
    session.add(todo)
    session.commit()
    open_todos, done_todos = _todo_lists(session)
    return templates.TemplateResponse(
        request, "todos/_lists.html", {"open_todos": open_todos, "done_todos": done_todos}
    )
```

- [ ] **Step 4: Write the templates**

`app/templates/todos/list.html`:

```html
{% extends "base.html" %}
{% block title %}To-Dos{% endblock %}
{% block content %}
<h1>To-Dos</h1>
<div id="todo-lists">
  {% include "todos/_lists.html" %}
</div>
{% endblock %}
```

`app/templates/todos/_lists.html`:

```html
<section>
  <h2>Open</h2>
  <ul>
    {% for todo in open_todos %}
    <li>
      {{ todo.title }} — due {{ todo.due_date }}
      <button hx-post="/todos/{{ todo.id }}/done" hx-target="#todo-lists" hx-swap="innerHTML">Done</button>
    </li>
    {% else %}
    <li>Nothing open.</li>
    {% endfor %}
  </ul>
</section>
<section>
  <h2>Done</h2>
  <ul>
    {% for todo in done_todos %}
    <li>{{ todo.title }} — done</li>
    {% else %}
    <li>Nothing done yet.</li>
    {% endfor %}
  </ul>
</section>
```

- [ ] **Step 5: Wire the router into `app/main.py`**

```python
from app.routers import bills, todos
...
app.include_router(bills.router)
app.include_router(todos.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_todos_router.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/routers/todos.py app/templates/todos/ app/main.py tests/test_todos_router.py
git commit -m "feat: add To-Dos router and pages"
```

---

### Task 15: Wiki router and pages

**Files:**
- Create: `app/routers/wiki.py`
- Create: `app/templates/wiki/list.html`, `app/templates/wiki/page.html`
- Modify: `app/main.py`
- Test: `tests/test_wiki_router.py`

**Interfaces:**
- Consumes: `app.db.get_session` (Task 2), `app.models.wiki.WikiPage`, `WikiChange` (Task 11)
- Produces: `app.routers.wiki.router` (prefix `/wiki`) — `GET /wiki`, `GET /wiki/{page_id}`

- [ ] **Step 1: Write the failing tests `tests/test_wiki_router.py`**

```python
import json

from app.models.wiki import WikiChange, WikiPage


def test_list_wiki_pages_renders(client, session):
    session.add(WikiPage(topic="Electricity", facts_json=json.dumps({"provider": "EDP"})))
    session.commit()

    response = client.get("/wiki")

    assert response.status_code == 200
    assert "Electricity" in response.text


def test_wiki_page_detail_renders_facts_and_changes(client, session):
    page = WikiPage(topic="Electricity", facts_json=json.dumps({"provider": "EDP"}))
    session.add(page)
    session.commit()
    session.refresh(page)

    session.add(WikiChange(wiki_page_id=page.id, fact_key="provider", old_value=None, new_value="EDP"))
    session.commit()

    response = client.get(f"/wiki/{page.id}")

    assert response.status_code == 200
    assert "EDP" in response.text


def test_wiki_page_detail_404_for_missing_page(client):
    response = client.get("/wiki/9999")
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_wiki_router.py -v`
Expected: FAIL — `404` on `/wiki` (route not registered)

- [ ] **Step 3: Write `app/routers/wiki.py`**

```python
"""Routes for browsing the auto-maintained wiki."""

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models.wiki import WikiChange, WikiPage

router = APIRouter(prefix="/wiki", tags=["wiki"])
templates = Jinja2Templates(directory="app/templates")

_RECENTLY_CHANGED_DAYS = 7


@router.get("")
async def list_wiki_pages(request: Request, session: Session = Depends(get_session)):
    pages = session.exec(select(WikiPage).order_by(WikiPage.topic)).all()
    cutoff = datetime.utcnow() - timedelta(days=_RECENTLY_CHANGED_DAYS)
    recently_changed_ids = {p.id for p in pages if p.updated_at >= cutoff}
    return templates.TemplateResponse(
        request, "wiki/list.html", {"pages": pages, "recently_changed_ids": recently_changed_ids}
    )


@router.get("/{page_id}")
async def wiki_page_detail(request: Request, page_id: int, session: Session = Depends(get_session)):
    page = session.get(WikiPage, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Wiki page not found")
    changes = session.exec(
        select(WikiChange).where(WikiChange.wiki_page_id == page_id).order_by(WikiChange.changed_at.desc())
    ).all()
    return templates.TemplateResponse(
        request, "wiki/page.html", {"page": page, "facts": json.loads(page.facts_json), "changes": changes}
    )
```

- [ ] **Step 4: Write the templates**

`app/templates/wiki/list.html`:

```html
{% extends "base.html" %}
{% block title %}Wiki{% endblock %}
{% block content %}
<h1>Wiki</h1>
<ul>
  {% for page in pages %}
  <li class="{{ 'recently-changed' if page.id in recently_changed_ids else '' }}">
    <a href="/wiki/{{ page.id }}">{{ page.topic }}</a>
  </li>
  {% else %}
  <li>No wiki pages yet.</li>
  {% endfor %}
</ul>
{% endblock %}
```

`app/templates/wiki/page.html`:

```html
{% extends "base.html" %}
{% block title %}{{ page.topic }} — Wiki{% endblock %}
{% block content %}
<h1>{{ page.topic }}</h1>
<dl>
  {% for key, value in facts.items() %}
  <dt>{{ key }}</dt><dd>{{ value }}</dd>
  {% endfor %}
</dl>
<h2>Change history</h2>
<ul>
  {% for change in changes %}
  <li>{{ change.changed_at }} — {{ change.fact_key }}: {{ change.old_value }} → {{ change.new_value }}</li>
  {% else %}
  <li>No changes recorded.</li>
  {% endfor %}
</ul>
{% endblock %}
```

- [ ] **Step 5: Wire the router into `app/main.py`**

```python
from app.routers import bills, todos, wiki
...
app.include_router(bills.router)
app.include_router(todos.router)
app.include_router(wiki.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_wiki_router.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/routers/wiki.py app/templates/wiki/ app/main.py tests/test_wiki_router.py
git commit -m "feat: add wiki router and pages"
```

---

### Task 16: Dashboard aggregation service

**Files:**
- Create: `app/services/dashboard_service.py`
- Test: `tests/test_dashboard_service.py`

**Interfaces:**
- Consumes: `app.models.document.Document`, `app.models.transaction.Transaction`, `app.models.todo.Todo`, `app.models.wiki.WikiPage` (Tasks 2, 5, 10, 11)
- Produces: `app.services.dashboard_service.DashboardData` dataclass (`spend_this_month: dict[str, float]`, `spend_last_month: dict[str, float]`, `open_todos: list[Todo]`, `recently_changed_wiki_pages: list[WikiPage]`, `needs_attention_documents: list[Document]`); `app.services.dashboard_service.get_dashboard_data(session, today: date | None = None) -> DashboardData`

- [ ] **Step 1: Write the failing tests `tests/test_dashboard_service.py`**

```python
from datetime import date, datetime, timedelta

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.todo import Todo
from app.models.transaction import Category, Transaction
from app.models.wiki import WikiPage
from app.services.dashboard_service import get_dashboard_data


def test_spend_totals_by_period(session):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="h1",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    session.add(Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=50.0, currency="EUR", statement_period="2026-08",
    ))
    session.add(Transaction(
        document_id=document.id, provider="Vodafone", category=Category.TELECOM,
        amount=30.0, currency="EUR", statement_period="2026-08",
    ))
    session.add(Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=45.0, currency="EUR", statement_period="2026-07",
    ))
    session.commit()

    data = get_dashboard_data(session, today=date(2026, 8, 17))

    assert data.spend_this_month == {"electricity": 50.0, "telecom": 30.0}
    assert data.spend_last_month == {"electricity": 45.0}


def test_open_todos_and_needs_attention_and_recent_wiki(session):
    session.add(Todo(title="Pay EDP", due_date=date(2026, 9, 5), done=False))
    session.add(Todo(title="Pay water", due_date=date(2026, 8, 1), done=True))
    session.add(Document(
        filename="bad.pdf", file_path="/tmp/bad.pdf", content_hash="h2",
        source=DocumentSource.MANUAL, status=DocumentStatus.NEEDS_ATTENTION,
    ))
    session.add(WikiPage(topic="Electricity", facts_json="{}", updated_at=datetime.utcnow()))
    session.add(WikiPage(
        topic="Old topic", facts_json="{}", updated_at=datetime.utcnow() - timedelta(days=30),
    ))
    session.commit()

    data = get_dashboard_data(session, today=date(2026, 8, 17))

    assert len(data.open_todos) == 1
    assert data.open_todos[0].title == "Pay EDP"
    assert len(data.needs_attention_documents) == 1
    assert len(data.recently_changed_wiki_pages) == 1
    assert data.recently_changed_wiki_pages[0].topic == "Electricity"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.dashboard_service'`

- [ ] **Step 3: Write `app/services/dashboard_service.py`**

```python
"""Aggregation queries backing the dashboard view."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.document import Document, DocumentStatus
from app.models.todo import Todo
from app.models.transaction import Transaction
from app.models.wiki import WikiPage

_RECENTLY_CHANGED_DAYS = 7


@dataclass
class DashboardData:
    spend_this_month: dict[str, float]
    spend_last_month: dict[str, float]
    open_todos: list[Todo] = field(default_factory=list)
    recently_changed_wiki_pages: list[WikiPage] = field(default_factory=list)
    needs_attention_documents: list[Document] = field(default_factory=list)


def _spend_by_category(session: Session, period: str) -> dict[str, float]:
    statement = select(Transaction).where(Transaction.statement_period == period)
    totals: dict[str, float] = {}
    for txn in session.exec(statement):
        totals[txn.category.value] = totals.get(txn.category.value, 0.0) + txn.amount
    return totals


def get_dashboard_data(session: Session, today: Optional[date] = None) -> DashboardData:
    today = today or date.today()
    this_period = f"{today.year:04d}-{today.month:02d}"
    last_month_date = date(today.year, today.month, 1) - timedelta(days=1)
    last_period = f"{last_month_date.year:04d}-{last_month_date.month:02d}"

    open_todos = list(
        session.exec(select(Todo).where(Todo.done == False).order_by(Todo.due_date))  # noqa: E712
    )
    needs_attention = list(
        session.exec(select(Document).where(Document.status == DocumentStatus.NEEDS_ATTENTION))
    )
    cutoff = datetime.utcnow() - timedelta(days=_RECENTLY_CHANGED_DAYS)
    recently_changed = list(session.exec(select(WikiPage).where(WikiPage.updated_at >= cutoff)))

    return DashboardData(
        spend_this_month=_spend_by_category(session, this_period),
        spend_last_month=_spend_by_category(session, last_period),
        open_todos=open_todos,
        recently_changed_wiki_pages=recently_changed,
        needs_attention_documents=needs_attention,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/dashboard_service.py tests/test_dashboard_service.py
git commit -m "feat: add dashboard aggregation service"
```

---

### Task 17: Real dashboard route and template

**Files:**
- Create: `app/routers/dashboard.py`
- Modify: `app/main.py` (remove the placeholder `/` route, register the real one)
- Modify: `app/templates/dashboard.html`
- Test: `tests/test_dashboard_router.py`

**Interfaces:**
- Consumes: `app.db.get_session` (Task 2), `app.services.dashboard_service.get_dashboard_data` (Task 16)
- Produces: `app.routers.dashboard.router` — `GET /`, `GET /health`

- [ ] **Step 1: Write the failing test `tests/test_dashboard_router.py`**

```python
from datetime import date

from app.models.todo import Todo


def test_dashboard_renders_open_todos(client, session):
    session.add(Todo(title="Pay EDP", due_date=date(2026, 9, 5)))
    session.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert "Pay EDP" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_router.py -v`
Expected: FAIL — `AssertionError` (placeholder dashboard doesn't render todos)

- [ ] **Step 3: Write `app/routers/dashboard.py`**

```python
"""Dashboard home route and health check."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db import get_session
from app.services.dashboard_service import get_dashboard_data

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/")
async def dashboard(request: Request, session: Session = Depends(get_session)):
    data = get_dashboard_data(session)
    return templates.TemplateResponse(request, "dashboard.html", {"data": data})
```

- [ ] **Step 4: Update `app/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}Dashboard — Home & Family{% endblock %}
{% block content %}
<h1>Dashboard</h1>
{% if data %}
  <section>
    <h2>Spend this month</h2>
    <ul>
      {% for category, amount in data.spend_this_month.items() %}
        <li>{{ category }}: {{ "%.2f"|format(amount) }}</li>
      {% else %}
        <li>No spend recorded yet.</li>
      {% endfor %}
    </ul>
  </section>
  <section>
    <h2>Open to-dos</h2>
    <ul>
      {% for todo in data.open_todos %}
        <li>{{ todo.title }} — due {{ todo.due_date }}</li>
      {% else %}
        <li>Nothing due.</li>
      {% endfor %}
    </ul>
  </section>
  <section>
    <h2>Recently changed wiki facts</h2>
    <ul>
      {% for page in data.recently_changed_wiki_pages %}
        <li><a href="/wiki/{{ page.id }}">{{ page.topic }}</a></li>
      {% else %}
        <li>No recent changes.</li>
      {% endfor %}
    </ul>
  </section>
  <section>
    <h2 class="needs-attention">Needs attention</h2>
    <ul>
      {% for doc in data.needs_attention_documents %}
        <li><a href="/bills/{{ doc.id }}">{{ doc.filename }}</a> — {{ doc.failure_reason }}</li>
      {% else %}
        <li>All clear.</li>
      {% endfor %}
    </ul>
  </section>
{% else %}
  <p>Loading…</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Replace the placeholder route in `app/main.py`**

Remove the inline `/health` and `/` route functions and the now-unused `templates` object from `app/main.py`, replacing them with:

```python
from app.routers import bills, dashboard, todos, wiki
...
app.include_router(dashboard.router)
app.include_router(bills.router)
app.include_router(todos.router)
app.include_router(wiki.router)
```

The resulting `app/main.py` should be:

```python
"""FastAPI application — routes, startup, dependency wiring."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import CloudflareAccessMiddleware
from app.config import settings

app = FastAPI(title="Home & Family Hub", version="0.1.0")

if settings.CF_ACCESS_TEAM_DOMAIN:
    app.add_middleware(CloudflareAccessMiddleware)

static_dir = Path("app/static")
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

from app.routers import bills, dashboard, todos, wiki  # noqa: E402

app.include_router(dashboard.router)
app.include_router(bills.router)
app.include_router(todos.router)
app.include_router(wiki.router)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_dashboard_router.py tests/test_main.py -v`
Expected: PASS — `test_main.py`'s dashboard placeholder assertions now exercise the real dashboard; both files should pass since the real dashboard also renders the nav (update `tests/test_main.py`'s `test_dashboard_placeholder_renders` to `test_dashboard_renders`, keeping the same nav assertions).

- [ ] **Step 7: Commit**

```bash
git add app/routers/dashboard.py app/main.py app/templates/dashboard.html tests/test_dashboard_router.py tests/test_main.py
git commit -m "feat: wire up the real dashboard route and template"
```

---

### Task 18: End-to-end smoke test

**Files:**
- Test: `tests/test_e2e_bill_flow.py`

**Interfaces:**
- Consumes: everything from Tasks 1–17. No new production code.

- [ ] **Step 1: Write `tests/test_e2e_bill_flow.py`**

```python
import io
import json
from datetime import date

import app.services.pipeline as pipeline_module
import app.services.wiki_engine as wiki_engine_module
from app.services.extraction import ExtractedBill


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text

    async def create(self, **kwargs):
        return _FakeMessage(self._response_text)


class _FakeAnthropicClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


def test_full_bill_ingestion_flow(client, monkeypatch):
    wiki_response = json.dumps({
        "wiki_worthy": True,
        "topic": "Electricity — provider & contract",
        "facts": {"provider": "EDP"},
    })

    async def fake_extract_bill(image_path, client=None):
        return ExtractedBill(
            provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
            due_date=date(2026, 9, 5), paid_date=None, statement_period="2026-08",
        )

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return await wiki_engine_module.assess_and_update_wiki(
            session, document, transaction, client=_FakeAnthropicClient(wiki_response)
        )

    monkeypatch.setattr(pipeline_module, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline_module, "ensure_image", lambda path: path)
    monkeypatch.setattr(pipeline_module, "assess_and_update_wiki", fake_assess_and_update_wiki)

    upload_response = client.post(
        "/bills/upload",
        files={"file": ("edp-august.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )
    assert upload_response.status_code == 303

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 200
    assert "electricity" in dashboard_response.text.lower()
    assert "EDP" in dashboard_response.text

    todos_response = client.get("/todos")
    assert "EDP" in todos_response.text

    wiki_list_response = client.get("/wiki")
    assert "Electricity" in wiki_list_response.text
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest -v`
Expected: All tests across every task PASS.

- [ ] **Step 3: Manual end-to-end check**

```bash
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000/bills/upload`, upload a real bill PDF (with a real `ANTHROPIC_API_KEY` in `.env`), and confirm: the document appears under Bills & Bank with extracted fields, a To-Do appears if it has a due date, a Wiki page appears if the extraction was assessed as wiki-worthy, and the Dashboard reflects all of it.

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_bill_flow.py
git commit -m "test: add end-to-end manual-upload bill ingestion smoke test"
```

---

## Self-Review Notes

- **Spec coverage:** Auth (Task 3), navigation (Task 4), Document/Transaction/Category (Tasks 2, 5), manual upload channel (Task 13), extraction/categorization/dedup (Tasks 7–9), wiki engine incl. auto-apply + recently-changed marker (Tasks 11, 15), dashboard + to-dos (Tasks 10, 14, 16–17), needs_attention surfacing (Tasks 12, 16). Email ingestion, NIF-based decryption, Enable Banking sync, and deploy scripts are explicitly out of scope (see header) — planned as a follow-up against the same spec.
- **Placeholder scan:** No TBD/TODO markers; every step has runnable code or a concrete command.
- **Type consistency:** `Document.status` (`DocumentStatus`), `Transaction.category` (`Category`), and all service signatures (`ingest_document`, `extract_bill`, `assess_and_update_wiki`, `generate_todo_for_transaction`, `get_dashboard_data`) are used identically across the tasks that define and consume them.
