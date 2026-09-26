# Home & Family Hub — SysAdmin Reference

Operational reference: deployment, backups, running the system, secrets, one-off scripts, known issues. See `docs/ARCHITECTURE.md` for design/schema/code-structure.

---

## 1. What the System Is

A single-tenant FastAPI web app that ingests bank statements/bills (PDF/image, via upload or historical backfill), extracts transactions with Claude, classifies them against a merchant/nature taxonomy, and surfaces them through a Dashboard, a filterable Transactions view, a Utilities consumption view, an auto-maintained Wiki of standing facts, and a Todo list. One family's data, one deployment, no multi-tenancy, no public signup — access is gated entirely by Cloudflare Access.

---

## 2. Where Everything Lives

**VPS**: Hetzner, `167.233.51.113`, hostname `ergon-prod-01`. Shared with other apps of Pedro's (each in its own dedicated system user — this VPS is not exclusive to Home & Family Hub).

| What | Where |
|---|---|
| App code | `/srv/home-hub/app/` (a git checkout, `origin` = the `home_hub` GitHub repo, `main` branch) |
| Virtualenv | `/srv/home-hub/venv/` (Python 3.14.4, `python3 -m venv`, no system-site-packages) |
| Database | `/srv/home-hub/app/data/home_family.db` (SQLite, single file) |
| Uploaded/imported documents | `/srv/home-hub/app/app/static/documents/` (~23 MB at last check) |
| Systemd unit | `/etc/systemd/system/home-hub.service` |
| Ingestion channel units | `~home-hub/.config/systemd/user/{home-hub-mailpoll.service,home-hub-mailpoll.timer,home-hub-telegram.service}` — systemd **user** units of the `home-hub` account (no sudo, no root system-unit dir); installed/refreshed from `deploy/systemd/` on every deploy by `deploy/install_user_units.sh` |
| Nginx site | `/etc/nginx/sites-enabled/home-hub` |
| Env file | `/srv/home-hub/app/.env` (loaded both by systemd's `EnvironmentFile=` and by `python-dotenv` at app startup) — also holds the channel settings `HUB_IMAP_*` / `HUB_TELEGRAM_*` (see §6) |
| Hub mailbox | A dedicated Gmail address (e.g. `cdafamily.hub@gmail.com`), created and handed over by Pedro (manual step M1) — never Pedro's personal Gmail |
| Hub Telegram bot | A dedicated bot created via @BotFather (manual step M2), **not** the Recipes bot — its username and token are handed over by Pedro, never committed |
| System user | `home-hub`, uid 995, gid 982, **no root**, no login shell for interactive use — GitHub Actions SSHes in as this user directly |

**Local repo**: `/home/pedro/Desktop/Claude_Corner/Personal/Home & Family/` is a *subtree* of the larger `Claude_Corner/Personal` monorepo — see `git subtree` note in §7.

**Live URL**: `https://hub.cdafamily.casa` — behind Cloudflare (DNS + proxy) and Cloudflare Access (identity-aware auth, see §6). Nginx only accepts connections from Cloudflare's IP ranges (`/etc/nginx/cloudflare-allow.conf`) plus localhost — the origin IP cannot be used to bypass Access. Nginx proxies to uvicorn on `127.0.0.1:9001` (loopback only, never exposed directly).

---

## 3. Deploying

**This release (House tab)** applies **four migrations**: `3b7e9c1d2f40` (House domain, `category` + `fields_json` on documents), `9c4d2a7e5b18` (knowledge layer: wiki claims/claim-sources/log, **the second of which backfills wiki claims from existing wiki pages**), `c5f1a8e3d902` (wiki links, `REVIEW` log operation, legacy ingest log entries) and `e7b3d1f4a6c8` (the `records` table; `wiki_claims.note`; `wiki_claim_sources.record_id`/`withdrawn_at`, made nullable on `document_id`; `wiki_log.record_id`; `todos.record_id` — the final head for this release). Take the usual snapshot **before pushing**:

```bash
ssh root@167.233.51.113
cp /srv/home-hub/app/data/home_family.db /root/home_family_pre_house_tab_$(date +%Y%m%d).db
```

**Fully automatic on every push to `main`** — no manual deploy step exists or should be added.

```
git push  (main branch, home_hub remote)
      │
      ▼
GitHub Actions (.github/workflows/deploy.yml)
      │  appleboy/ssh-action@v1.2.0, as user `home-hub` (no root), key = secrets.DEPLOY_SSH_KEY_HOMEHUB
      ▼
cd /srv/home-hub/app
git pull --ff-only
source ../venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
sudo /bin/systemctl restart home-hub    # home-hub has a narrow, passwordless sudo rule for exactly this
sleep 2
sudo /bin/systemctl is-active home-hub  # step (and this) fails the whole Action if the service didn't come back up
bash deploy/install_user_units.sh       # installs/refreshes the ingestion-channel user units, no sudo
sleep 3
systemctl --user is-failed --quiet home-hub-telegram && exit 1  # fails the Action if the bot is crash-looping
```

**One-time prerequisite, root, before the first deploy of the ingestion channels:**
`ssh root@167.233.51.113 'loginctl enable-linger home-hub && ls -d /run/user/995'` — without this,
`home-hub` has no `/run/user/995` and `install_user_units.sh` fails immediately with a clear message
rather than half-installing anything. Timing gotcha: `/run/user/995` can appear a few seconds after
`enable-linger` returns (`user@995.service` is still starting), so the `ls` half of that one-liner
can fail on first run even though linger was enabled correctly — re-check after a few seconds before
concluding it didn't work. Done on 2026-09-26 (Linger=yes, user@995 active, /run/user/995 present).

A Telegram bot with no token set (`HUB_TELEGRAM_BOT_TOKEN` unset) exits 0 immediately and shows as
`inactive`, not `failed` — so the very first deploy of this feature passes before Pedro has done his
manual setup (M1/M2/M4). Only a bot that's configured but crash-looping shows as `failed` and fails
the Action.

**This repo is a subtree**, not a standalone git repo — the working directory you edit in day-to-day
(`Personal/Home & Family/`) lives inside the larger `Claude_Corner/Personal` monorepo. Pushing to
`main` on GitHub does **not** happen via a normal `git push` from this directory. It happens via:

```bash
git subtree push --prefix="Home & Family" home_hub main
```

run from the `Claude_Corner/Personal` monorepo root, where `home_hub` is a remote pointing at
`github.com/pilomeida/home_hub`. Forgetting `--prefix` or running a plain `git push` from inside
`Home & Family/` will not do what you want — there is no `.git` directory inside `Home & Family/`
itself. See the `ref_home-hub-github` memory entry.

**GitHub secret**: `DEPLOY_SSH_KEY_HOMEHUB` — a private key whose public half is authorized in
`/home/home-hub/.ssh/authorized_keys` on the VPS, scoped to the `home-hub` user only (not root).
This is a **different** deploy key from `github_deploy` (used for the separate `IG-recipes_hub` app
on the same VPS, and the key that was used once, deliberately, as a stopgap to reach this VPS as
**root** — see §9 "Known Issues" — for operational tasks the `home-hub` deploy key can't do, since it
has no sudo beyond restarting its own service).

**Health check**: `GET /health` is the one route excluded from Cloudflare Access middleware, so it's
reachable for uptime checks without an Access session. The deploy workflow itself checks liveness via
`systemctl is-active`, not an HTTP call.

**Known issue / one-time step:** House floor plans accept **videos**. Nginx's
`client_max_body_size` for the `home-hub` site must allow them — e.g.
`client_max_body_size 200M;` in `/etc/nginx/sites-enabled/home-hub`, then
`nginx -t && systemctl reload nginx`, as root. Until that is done, large video uploads fail
**at Nginx** with HTTP 413 before ever reaching the app. Note also that uploads are read fully
into memory by the app — keep videos short.

**Migrations run automatically as part of every deploy** (`alembic upgrade head`, no
`DATABASE_PATH` override — it deploys against the real file, by design, using the env file's own
`DATABASE_PATH`). This means a bad migration ships live the moment it's pushed to `main` — there is
no staging environment. Test migrations against a scratch copy of the database locally before
merging to `main` (see the migration discipline note in ARCHITECTURE.md's Design Decisions).

---

## 4. Running the System

```bash
# Status / logs
sudo systemctl status home-hub
sudo journalctl -u home-hub -f          # live tail
sudo journalctl -u home-hub --since "1 hour ago"

# Restart (normally only the deploy workflow does this)
sudo systemctl restart home-hub

# Manual run for debugging (NOT how production runs — bypasses systemd)
cd /srv/home-hub/app
source ../venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 9001 --reload
```

`Restart=always`, `RestartSec=5` — a crash self-heals within 5s; check `journalctl` after any
unexpected downtime to see why it died, since systemd will have already restarted it by the time
you look.

**Ingestion channels** (email poller, Telegram bot) — systemd **user** units of `home-hub`, so every
command needs `XDG_RUNTIME_DIR` and runs as that user, not root directly:

```bash
# Channel status / logs (as root)
sudo -u home-hub XDG_RUNTIME_DIR=/run/user/995 systemctl --user status home-hub-telegram home-hub-mailpoll.timer
sudo -u home-hub XDG_RUNTIME_DIR=/run/user/995 systemctl --user list-units --failed
journalctl _SYSTEMD_USER_UNIT=home-hub-telegram.service --since "1 hour ago"
journalctl _SYSTEMD_USER_UNIT=home-hub-mailpoll.service --since today
# Poll the mailbox right now
sudo -u home-hub XDG_RUNTIME_DIR=/run/user/995 systemctl --user start home-hub-mailpoll.service
```

Mailbox folders the poller uses (never read/unread flags — a person browsing the mailbox can't
disturb the poller, and content-hash dedup makes reruns harmless):
- Mail that failed stays in the mailbox's `Hub-Failed` folder. To retry it, move it back to Inbox;
  this is safe because duplicates are skipped.
- `Hub-Ignored` holds mail from senders who aren't on the allowlist, and mail with no usable
  attachment (e.g. a link-only e-invoice).
- Successfully filed mail moves to `Hub-Processed`.

**Wiki Lint timer** (weekly, Sunday 03:30) — same systemd **user** units of `home-hub` as the
ingestion channels above:

```bash
# As home-hub (after su -s /bin/bash home-hub, or sudo -u home-hub with XDG_RUNTIME_DIR set)
systemctl --user list-timers home-hub-wikilint.timer
journalctl --user -u home-hub-wikilint

# Manual run, as the home-hub user
cd /srv/home-hub/app && ../venv/bin/python -m app.jobs.wiki_lint
```

**Running a one-off script against production** (see §8 for the script inventory):

```bash
ssh root@167.233.51.113
cd /srv/home-hub/app
cp data/home_family.db /root/home_family_pre_<description>_$(date +%Y%m%d).db   # ALWAYS back up first
sudo -u home-hub /srv/home-hub/venv/bin/python scripts/<script>.py
```

Run as `sudo -u home-hub`, not as root directly — the venv and file ownership are `home-hub`'s;
running as root would leave root-owned files/locks the service user can't clean up.

**Long-running scripts** (a full historical backfill, a large chunked import): the SSH session
itself is not reliable for holding a multi-minute foreground command open — two separate incidents
this project (a classification backfill and the Revolut import) saw the local SSH call hang or drop
mid-run even though the remote process kept running fine. The reliable pattern:

```bash
ssh root@167.233.51.113 "cd /srv/home-hub/app && nohup sudo -u home-hub /srv/home-hub/venv/bin/python scripts/<script>.py > /tmp/<script>.log 2>&1 & disown; echo PID:\$!"
# then poll with FRESH ssh connections, not one held-open channel:
ssh root@167.233.51.113 "ps aux | grep <script>.py; tail -20 /tmp/<script>.log"
```

---

## 5. Backup Strategy

**No automated backup job exists today.** The only backups on file are the manual `cp
data/home_family.db /root/home_family_pre_<...>.db` snapshots taken by hand immediately before each
one-off script run against production (see §8 for the dated list of these as they accumulate under
`/root/` on the VPS). This is a known gap, not a design choice — worth setting up a daily cron
`sqlite3 .backup` (or even a simple timestamped `cp`) rotated off-box, since the whole database is a
single ~1 MB file and would cost nothing to snapshot regularly.

**Restore procedure** (manual, from one of the `/root/home_family_pre_*.db` snapshots):

```bash
ssh root@167.233.51.113
sudo systemctl stop home-hub
cp /srv/home-hub/app/data/home_family.db /root/home_family_before_restore_$(date +%Y%m%d_%H%M%S).db  # snapshot the bad state too, just in case
cp /root/home_family_pre_<description>_<date>.db /srv/home-hub/app/data/home_family.db
chown home-hub:home-hub /srv/home-hub/app/data/home_family.db
sudo systemctl start home-hub
sudo systemctl is-active home-hub
```

Uploaded documents (`app/static/documents/`) are **not** included in any snapshot above — they're
static content-hash-named files that are never mutated after creation, so the exposure is losing
newly-uploaded files since the last full filesystem-level backup (which also doesn't currently
exist). Lower priority than the database, but worth covering in the same future backup job.

---

## 6. API Keys and Secrets

| Secret | Where it lives | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | `/srv/home-hub/app/.env` on the VPS (never committed — `.env.example` in the repo has placeholder values only) | All Claude calls: document classification, bill/statement extraction, merchant resolution, wiki-worthiness assessment. **Out of credit as of 2026-09-26** — every call returns 400 "credit balance is too low"; not being topped up. To be replaced by routing through the LLM Selector (backlog item, see build log) |
| `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` | Same `.env` | Cloudflare Access JWT verification (`app/auth.py`) — team domain for the JWKS endpoint, AUD tag identifying this specific Access application |
| `DEPLOY_SSH_KEY_HOMEHUB` | GitHub repo secret (`pilomeida/home_hub` → Settings → Secrets) | CI/CD SSH auth as the `home-hub` user, used only by `.github/workflows/deploy.yml` |
| `HUB_IMAP_PASSWORD` | Same `.env` | A Gmail **app password** (16 letters) for the dedicated Hub mailbox — not the mailbox's real login password. Revoke or regenerate it from the Hub's own Google account (Security → App passwords), never Pedro's personal account |
| `HUB_TELEGRAM_BOT_TOKEN` | Same `.env` | Auth token for the Hub's dedicated Telegram bot. Regenerated via @BotFather's `/revoke` command if it ever leaks — whoever holds it fully controls the bot |
| `HUB_IMAP_ALLOWED_SENDERS`, `HUB_TELEGRAM_ALLOWED_USERS` | Same `.env` | **Not secret** — plain allowlists (email addresses; Telegram numeric ids mapped to names) gating which senders' attachments reach the LLM classifier at all |

All of the `HUB_*` channel settings live **only** in `/srv/home-hub/app/.env` — same place as
`ANTHROPIC_API_KEY` — and are optional: every channel setting left unset simply keeps that channel
switched off (`imap_configured` false, or an empty bot token), and the web app boots the same either
way.

**Cloudflare Access** gates every route except `/health`. `CloudflareAccessMiddleware`
(`app/auth.py`) verifies the incoming JWT (from the `Cf-Access-Jwt-Assertion` header or
`CF_Authorization` cookie) against Cloudflare's JWKS via `PyJWKClient`, checking signature, audience
(`CF_ACCESS_AUD`), and issuer (derived from `CF_ACCESS_TEAM_DOMAIN`). On success it attaches the
verified email to `request.state.user_email`; on failure, 403. The app itself has **no login system,
no session store, no password anywhere** — identity is entirely Cloudflare's problem.

**No secret has ever been committed to the repo** — `.env` is gitignored; `.env.example` ships
placeholder values only (`sk-ant-your_key_here`, etc.).

---

## 7. Costs

- **Anthropic API**: the only recurring per-use cost. Sonnet 5 for document/statement extraction
  (the expensive calls — full-page PDF vision input), Haiku 4.5 for cheap classification calls
  (document-type detection, merchant resolution, wiki-worthiness). One incident this project: the
  API key hit its credit limit mid-ingestion (see the `account-statement_2022-08-01_...` entry in
  §9) — worth keeping an eye on usage if more large historical backfills are planned, since a
  multi-hundred-page statement import can burn through a lot of extraction calls in one run.
- **Ingestion channels**: one Haiku 4.5 call per channel attachment (email or Telegram), the same
  cheap classifier model the existing document classifier uses. The sender/user allowlists
  (`HUB_IMAP_ALLOWED_SENDERS`, `HUB_TELEGRAM_ALLOWED_USERS`) cap the exposure — mail from an
  unknown sender, or a Telegram message from an unknown user, is ignored before any LLM call.
- **Ask and Lint use `claude-opus-5-5`** — the most expensive model in this app, chosen for
  quality over the two knowledge-layer operations that talk directly to a person. Ask makes one
  call per tool round-trip (usually 2–4 per question, bounded at 8); token counts per question are
  stored on `ask_turns` (`input_tokens`/`output_tokens`). Lint makes one call per implemented
  domain per weekly check (currently 2: Financials, House), plus the deterministic checks which
  make no LLM calls at all.
- **Hetzner VPS**: shared fixed cost across multiple of Pedro's apps — not itemized per-app.
- **Cloudflare**: DNS + Access, on the free/included tier for this domain as far as this app is
  concerned (no paid Cloudflare feature is in use here).
- **No other paid services.**

---

## 8. One-Off Scripts Inventory

Everything in `scripts/` is a **historical/administrative** script, not reviewed application code —
each one exists to backfill or repair real production data once, and each has its own docstring
explaining exactly what it does and whether it's safe to re-run. None of them are wired into any
scheduled job; every run so far has been triggered by hand.

| Script | Purpose | Idempotent? | Run so far (production) |
|---|---|---|---|
| `backfill_electricity_history.py` | Ingests a historical Excel export of electricity readings (ground truth, no LLM calls) into Document + Transaction + UtilityReading | Yes | Run once during the Utilities feature's original rollout |
| `backfill_documents.py` | Historical bulk import of existing documents; now goes through the ingestion core (`receive_file` / `finalize_document`) like every other upload path | Yes | Unknown |
| `backfill_transaction_classification.py` | Runs the shared `classify_transaction()` over every Transaction/Document currently missing classification | Yes — filters on `merchant_id IS NULL` | Run **3 times** against production: (1) full historical Santander backfill after sub-project 3 merged, (2) a resumed continuation after an SSH-drop interrupted run 1 partway through, (3) after the Revolut import, to classify the 1,122 new transactions (1 error both times: transaction #3092, `'parking' is not a valid Category` — pre-existing, not caused by any classification-engine bug; still open, see §9) |
| `merge_duplicate_merchants.py` | Merges exact-case-insensitive-`canonical_name` duplicate `Merchant` rows (repoints their transactions, deletes the duplicate) | Yes, by construction | Run **twice**: after the Santander backfill (503 merged, 761 repointed, 0 errors), and again after the Revolut import (227 merged, 313 repointed, 0 errors) — expected to need re-running after any future large import, since each institution's provider-string format tends to slip past `normalize_provider()`'s rules tier in its own way |
| `backfill_revolut_pedro_account.py` | Chunked import of Pedro's own Revolut current account (Jan 2024–present) out of a 221-page combined 5-sub-account family statement, sliced into per-month PDF page ranges and run through the normal extraction pipeline once per month (the whole statement is far too large for one extraction call) | Yes — skips any `(year, month)` that already has a `Document` row | Run once: 32/32 months succeeded, 1,122 transactions created, 0 errors. **⚠ Currently exists only on the VPS filesystem** (`/srv/home-hub/app/scripts/`), uploaded by `scp` — **not yet committed to the local git repo**. Should be added to the repo to match how the other scripts here are version-controlled; it's the one loose end from this import. Explicitly does **not** cover Matias's or Vicente's Revolut sub-accounts/pockets — out of scope by deliberate user decision, not yet started |

**`pypdf`** was `pip install`ed directly into the VPS's `home-hub` venv to support the Revolut
chunking script — deliberately **not** added to `requirements.txt`, since it's not an application
dependency, only needed to run that one script. Safe to `pip uninstall pypdf` from the venv once
that script is no longer expected to need re-running (e.g. once committed to git and confirmed
stable, or once it's clear no similar chunked import is imminent).

---

## 9. Known Issues

**4 documents in `NEEDS_ATTENTION`** (as of this writing — check `SELECT filename, failure_reason
FROM documents WHERE status='NEEDS_ATTENTION'` for current state, status is stored as the uppercase
enum name):

| Document | Failure reason | Notes |
|---|---|---|
| `EXTCON202208000356157688020.pdf` | `The PDF specified was not valid` (Anthropic API 400) | Likely a genuinely corrupt/malformed source PDF — needs manual inspection, not a retry |
| `EXTCON202403000356157688020.pdf` | `'atm_withdrawal' is not a valid TransactionType` | The extraction returned a transaction-type value the `TransactionType` enum doesn't have — either the enum needs a value added, or the extraction prompt needs tightening |
| `EXTCON202502280031000356157688020.pdf` | `'insurance' is not a valid TransactionType` | Same class of bug as above — looks like the LLM is sometimes returning a *category*-shaped string where a transaction *type* is expected |
| `account-statement_2022-08-01_2026-08-18_en-us_e5ee4c.pdf` | `Your credit balance is too low to access the Anthropic API` | Pure API-credit exhaustion at the time this document was processed, unrelated to the document itself — safe to simply retry once credits are topped up |

**Transaction #3092** (`'PARQUE UNIVERSIDADE LISB'`): fails classification with `'parking' is not a
valid Category` — the LLM proposes a category hint (`parking`) that `normalize_category()` doesn't
map to any `Category` enum value. Only 1 of 4,848 transactions affected; surfaced identically across
multiple backfill runs, so it's a stable, reproducible gap (either add a `parking`-related synonym to
`normalize_category`'s mapping, or add a dedicated `Category` value) rather than a flaky extraction.

**No automated backups** (see §5) — the only safety net today is the manual pre-script snapshot
habit; worth closing before any further destructive-by-nature operation against production.

**Root access to this VPS was regained this project via the `github_deploy` key** (originally
provisioned for a *different* app's deploy pipeline, `IG-recipes_hub`) after the intended
credentials for direct root/SSH login were unavailable. This worked because that key happened to
already be authorized for root on this shared VPS — not because it was provisioned for Home & Family
Hub. Worth deliberately provisioning and documenting a proper root (or sudo-capable) credential for
this project specifically, rather than continuing to depend on a neighboring app's deploy key as a
side door.

**Out of scope, by explicit user decision** (not bugs, just not started): ingesting Matias's or
Vicente's Revolut current accounts/pockets (same source PDF, different page ranges, never
extracted); the Overview dashboard redesign (sub-project 2 of the Financial OS decomposition —
mockups/spec exist, no implementation).
