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

## Deployment

Deploys automatically via GitHub Actions on every push to `main` — see
`.github/workflows/deploy.yml`. The VPS runs the app as a dedicated
`home-hub` system user with no broader privileges beyond restarting its
own service; DNS/hostname/Cloudflare Access are not yet configured (the
app currently only listens on `127.0.0.1:9001` on the server).
