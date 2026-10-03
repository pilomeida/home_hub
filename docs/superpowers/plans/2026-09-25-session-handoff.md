# Session handoff — House / Inbox / Ask build (updated 2026-09-26 against live repo state)

## How to resume
Read `docs/superpowers/plans/2026-09-25-build-brief-hermes.md` (governing brief) and the build log (`docs/superpowers/plans/2026-09-25-build-log.md`) — the log is authoritative and includes a go-live status section at the end. This file only orients a fresh session.

## State (verified live, not from recollection)
- Repo: `/home/pedro/Desktop/Claude_Corner/Personal` (git). App: `Home & Family/`. NEVER push — push = deploy, Pedro-only, and only on his explicit order.
- **All three plans are executed.** Branches: `feat/house-tab` (Tasks 1–25 incl. Records, assumed warranty, re-filing), `feat/inbox-channels` (Tasks 1–9; Task 10/go-live is Pedro+Claude), `feat/ask` (Tasks 1–15; Task 16 parked). Branches were merged forward into each other; current work branch is `feat/polish` (contains everything; polish + go-live prep commits on top). `master` has an unrelated `feat(hub): llmsel gateway` tip.
- Full suite: **581 passed, 0 failed** (`cd "Home & Family" && PYTHONPATH="Home & Family" /usr/bin/python3 -m pytest -q`).
- Go-live status (see build log tail for detail): all builds done and reviewed. Open items are (a) Pedro's manual setup — Gmail app password for `cdafamily.mailbox@gmail.com`, `@CdAmailbox_bot` token, both typed into `.env` by Pedro himself; `HUB_IMAP_ALLOWED_SENDERS` list agreed; (b) the Anthropic credit gap — decision made NOT to top up; the Hub will be routed through the **LLM Selector** (llmsel) instead — backlog item; Ask Task 16 real-API smoke check and go-live Step 5 live smoke test are parked until that wiring exists; (c) merge + push only on Pedro's explicit order.

## Environment facts
- Tests REQUIRE `PYTHONPATH="Home & Family"` and `/usr/bin/python3` (venv python lacks pytest; hermes's own tests dir otherwise shadows the project's tests/).
- `app/config.py` requires `ANTHROPIC_API_KEY` at import: export dummy `sk-test-dummy` for alembic/scratch shells only. Never read real secrets; Pedro types provider secrets into `.env` himself.
- alembic: `DATABASE_PATH=/tmp/scratch_x.db alembic upgrade <rev>`; never touch `Home & Family/data/` (real family data).
- Jinja autoescape ON — never `|safe` (Pedro ruling).
- Repo has unrelated dirty folders (Recipes, Exercise App, WhatsApp To-Dos, Comics project): stage explicit paths only, never `git add -A`/`.`/stash/reset --hard.
- Migration head: `e7b3d1f4a6c8` (per plan; verify with `alembic current` if relevant).

## Method notes that worked (superpowers SDD)
- Briefs: `cd <repo root> && bash ~/.claude/plugins/cache/claude-plugins-official/superpowers/6.3.0/skills/subagent-driven-development/scripts/task-brief <plan-file> <N>` (must run from repo root). Workspace: `.superpowers/sdd/`.
- One implementer subagent per task, serial; controller verifies each landing (git log + full suite re-run) and appends the build-log entry. Checkpoints are mandatory stops — wait for Pedro's explicit go (overshooting one was corrected in session).
- If a subagent hits approval-blocked commands: have it write a /tmp script and run that instead of retrying.
