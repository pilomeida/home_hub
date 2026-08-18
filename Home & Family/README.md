# Home & Family Hub

Family record-keeping hub: bills & bank statements, an auto-maintained wiki
of standing facts, a dashboard, and a to-do list. Gated behind Cloudflare
Access.

## Prerequisites

- Python 3.12+

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
