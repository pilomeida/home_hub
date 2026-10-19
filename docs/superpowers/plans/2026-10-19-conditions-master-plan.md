# Conditions, Visits and Medication: Master Plan

> **For agentic workers:** This is the map. Each phase below becomes its own detailed plan (same folder, `2026-10-19-conditions-N-*.md`) and ships working software on its own. Phase 1 is already written in full. Write the next phase's plan only when the previous one has landed, because its code depends on what the earlier one actually produced. REQUIRED SUB-SKILL for each detailed plan: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Give every long-running condition of a family member its own page where papers, visits, hand-made notes, medication and the next follow-up come together, and make each health paper say why it was made and show its structure as the paper prints it.

**Architecture:** A condition becomes a real record (`health_conditions`, per person) instead of a label in `health_areas`. Papers, visits and notes attach to it through links that are *suggested by code or the reader and confirmed by a person*; nothing is linked or created without a tap. The paper reader keeps producing sections as it does today (`group_path` already stores a nested outline) but is taught to read narrative conclusions and numbers per section, and to return the paper's context (Indicação, Observações, who asked, who did it). Reminders reuse the existing `todos` table with `domain=HEALTH`.

**Tech Stack:** Python 3.10+/FastAPI/SQLModel/Alembic (SQLite), Jinja2 + HTMX, pytest + pytest-asyncio, the llmsel gateway (`app/llm_gateway.py`, job `health_extraction`, always `house_high`).

**Spec:** `/home/pedro/Desktop/Claude_Corner/brainstorms/2026-10-19-health-conditions-episodes-and-section-hierarchy.md` (Pedro's 10 answers; the "Summary / key decisions" section is the contract).

## Global Constraints

- **Never `git commit` / `git push` without Pedro's explicit order** (project CLAUDE.md). Each task ends with a checkpoint (tests green, `git status` reviewed). When he orders a commit, stage files **by name** (the working tree is shared with another session: never `git add -A`/`.`). Deploy is `git subtree push --prefix="Home & Family" home_hub main`, only on his word.
- **Every LLM call goes through the llmsel gateway** (`app/llm_gateway.py`). Health reads are always `sensitivity=Tier.HIGH.value`.
- **Test data comes from the production path** (project CLAUDE.md): build inputs with the real objects (`apply_extraction`, `make_document`, `FakeGateway`, `GatewayError`), never a hand-typed stand-in for a stored value.
- **Messages rule:** every error or warning a person can see says what didn't happen, why, and what they can do (`app/problems.py`: `Problem`, `explain`, `record_problem`; routes raise `HTTPException(detail={"what","why","next_step"})`). Background jobs also `record_problem`.
- **Privacy rule (D16/D18):** anything showing the AI health records goes through `app/privacy/reach.py` and runs in `reachable_scope(session)`; Health Ask tools report `tier=HIGH`. The tripwire tests (`tests/test_privacy_reach*.py`) must cover any new Ask tool. Fields that are clinical are `FieldSpec(private=True)` so they never reach Telegram, email or the calendar.
- **Additive-only migrations** (new tables, nullable columns). Find the head with `python3 -m alembic heads` (it was `d1f6b8c2e547` on 2026-10-19; re-check before writing a migration).
- **Nothing is created or linked by the Hub on its own** (Pedro, Q2): the Hub only *proposes*; a person confirms.
- **Family-open** (Q10): no per-person privacy; every entry records who wrote it and when (`created_by` = the Cloudflare Access email, as `documents.uploaded_by` does).
- Tests run from `Home & Family/` with `python3 -m pytest <path> -q`. Targeted files per task; the whole suite (~15 min) once at the end of each phase.
- The health reader changes must be run through the benchmark on the VPS before and after (`scripts/health_benchmark.py run`).

## The phases

| # | Plan | Ships | Depends on |
|---|---|---|---|
| 1 | `2026-10-19-conditions-1-reader-and-context.md` (written) | Paper reader returns the context fields; nested outline + narrative conclusions + numbers per section; "Why this was done" box with free text on every paper page; papers 199 and 200 re-read | nothing |
| 2 | conditions foundation | `health_conditions` + `health_entry_conditions`; condition page; suggestion on the paper's box (confirm / pick another / none); "propose a new condition"; one-time review list for filed papers and the old guessed labels | 1 |
| 3 | follow-ups and reminders | `health_follow_ups`; "next due" line; to-dos with `domain=HEALTH` and the health/finance icons; overdue in red; proposal from report text ("repetir em 12 meses") | 2 |
| 4 | visits, notes, medication | `health_visits` (two stages), `health_notes` (note / own reading / episode), `health_medications` + `health_medication_changes`; one timeline merging papers, visits, notes, medication changes | 2, 3 |
| 5 | Ask and overview | Health Ask tools for conditions, visits, medication; overview card; tripwire tests | 2, 4 |

## Data model (target; each phase adds only its own tables)

```
health_conditions        id, person_id → health_people, name, match_key, started_on (date|null), started_note,
                         status ('followed'|'stable'|'resolved'), followed_by (text), created_by, created_at
health_entry_conditions  entry_id → health_entries, condition_id → health_conditions, state ('suggested'|'confirmed'|'rejected'),
                         decided_by, decided_at          PK (entry_id, condition_id)
health_follow_ups        id, condition_id, what, due_on, source ('person'|'proposed_from_paper'), entry_id|null,
                         todo_id → todos|null, done_on|null, created_by
health_visits            id, condition_id|null, person_id, status ('booked'|'done'), visit_on, clinic, doctor, contacts,
                         observations, plan_changes, next_appointment_on|null, document_id|null (the booking letter),
                         calendar_event_id|null, created_by, created_at
health_visit_papers      visit_id, entry_id                 (papers shown as one event on the timeline)
health_notes             id, condition_id, person_id, kind ('note'|'reading'|'episode'), noted_on, text, value|null, unit|null, created_by, created_at
health_medications       id, condition_id, name, dose, frequency, started_on, stopped_on|null, created_by
health_medication_changes id, medication_id, changed_on, change ('new'|'same'|'changed'|'stopped'), dose, frequency, visit_id|null, entry_id|null, created_by
```

Existing `health_areas` rows of `kind="condition"` stay readable (legacy) and become *proposals* in the Phase 2 review list. After Phase 2 the reader stops writing new `kind="condition"` tags; its `conditions` output only feeds suggestions.

## Decisions taken as defaults (Pedro delegated technical choices)

- Suggestion matching is code, not a second model call: the reader's `conditions`/`indication`/`clinical_notes` text is matched against the person's conditions by `match_key` containment and a small alias list (e.g. "AVC" ≈ "AVC isquémico" ≈ "Stroke"); anything unmatched becomes a *proposal to create*.
- Reminders are created when a follow-up is saved: a `Todo(domain=HEALTH, due_date=due_on - 14 days)` linked by `todo_id`; marking the to-do done sets `done_on`.
- Phase 4's "booked" stage reuses `appointments.sync_appointment`; the reason for the visit never goes to the calendar (existing rule).
