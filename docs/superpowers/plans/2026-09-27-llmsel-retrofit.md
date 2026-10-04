# Hub → LLM gateway (llmsel) retrofit — plan

Date: 2026-09-27 · Branch `feat/llmsel` (off `feat/polish`, stacked; unpushed)
Design record: `~/Desktop/Claude_Corner/brainstorms/2026-09-27-hub-to-llm-selector.md` (D1–D7).
Call-site audit (read first, has file:line for everything):
`docs/superpowers/plans/2026-09-27-llmsel-callsite-audit.md` (this folder)

## Why

The Hub's Anthropic key is out of credit (production document reading is
broken) and will not be topped up. Every LLM call moves to Pedro's gateway,
which picks the supplier; the Hub never names a model or holds a key again.
The gateway contract is `~/Desktop/Claude_Corner/Scaffolding/LLM_Provider_Selection/docs/WORKER_CONTRACT.md`
— read it; its "one rule" is binding. Attachments are being added to the
gateway in parallel (brief 07, `/home/pedro/Desktop/Claude_Corner/Scaffolding/LLM_Provider_Selection-attachments/docs/briefs/07-attachments.md`
— read §2 for the wire shape you code against). You code against a **fake**
gateway; nothing here calls the network.

## Contract you code against

`POST {LLMSEL_URL}/run`, `Authorization: Bearer {LLMSEL_TOKEN}`, JSON body:
`workload_type`, `system` (plain string, stable content first), then either
`user` (+ optional `attachments: [Anthropic base64 image/document blocks]`)
or `messages` + `tools` + `job_id` (tool-use turn; `tool_result.content` may
hold image/document blocks). Optional `max_tokens` (may only lower the
type's ceiling), `response_schema` (JSON Schema; gateway validates and
retries — **you never retry**). Response: `text`, `stop_reason`, `usage`,
`catalog_key`, `warnings`, and on tool turns `tool_calls`,
`assistant_content`. Non-2xx → error with a JSON `detail`.

## Workload mapping (decided — D4)

| Call site | workload_type |
|---|---|
| `extraction.extract_bill`, `extract_statement_transactions`, `extract_utility_detail`; `domains/house/warranty.extract_warranty_dates` | `vision_extraction` |
| `extraction.classify_document`; `domain_classifier.suggest_domain_and_category` | `vision_classification` |
| `classification_engine.resolve_merchant_via_llm`; `wiki_engine.assess_document_for_wiki` | `classification` |
| `wiki_lint/llm_checks` | `critique_review` |
| `ask/engine.run_turn` (tool loop) | `interactive_research` |

Put the names in one module of constants. All calls use `run` (no `submit`).

## Tasks (TDD: failing test first, then code, then green — every task)

1. **Client.** `app/llm_gateway.py`: async, `httpx.AsyncClient`, mirrors the
   deliberately-thin eu_track client
   (`~/Desktop/Claude_Corner/CareerOutreachCRM/eu_track/llmsel_client.py` — read
   it): `run(...) -> GatewayResult`, `GatewayError(status, detail)`; no retry,
   no fallback, no model names; timeout 600 s. One injectable seam (a
   dependency/provider function) replacing every `client or AsyncAnthropic(...)`.
   A shared `tests/fakes/fake_gateway.py` that records requests and returns
   scripted results/errors — replace the 6+ ad-hoc fakes with it.
2. **Config.** `LLMSEL_URL`, `LLMSEL_WORKER` (=`hub`), `LLMSEL_TOKEN` in
   `app/config.py`; delete `ANTHROPIC_API_KEY` everywhere (config,
   `.env.example`, scripts). Remove the `anthropic` package from requirements
   if nothing imports it after Task 5 (check `grep -rn anthropic`).
3. **Document jobs** (extraction ×4, warranty, domain classifier): send
   `build_content_block(...)` output as `attachments`, the old user text as
   `user`. Drop `model`, `thinking`, SDK params. Express each JSON output as a
   `response_schema` (the audit proposes them); keep parsing into the same
   dataclasses and keep the typed `*Error` on unparseable text. Statement
   extraction: drop `max_tokens=16384` (the type ceiling, 16000, governs).
4. **Text jobs** (merchant, wiki-worthiness): same, no attachments.
5. **Ask loop.** One `job_id` (uuid4) per question, re-sent every turn;
   same tools in the same order each turn; append `assistant_content`
   verbatim; tool results (including `read_file`'s content blocks) go back as
   `tool_result` blocks as today; keep the 8-turn and 2-file-read caps;
   accumulate usage from `result.usage`. Adaptive thinking is the gateway's
   now — remove it. Catch `GatewayError` where `anthropic.APIError` was caught.
6. **Wiki lint** (`critique_review`), both the timer job and "Run now".
7. **Errors the family sees.** Rewrite `app/services/presentation.py`'s
   Anthropic-specific classification for `GatewayError`: 5xx/timeout/connect
   → "temporarily unavailable, try again later"; 4xx refusal (e.g. no model
   paired, request too large) → a plain "couldn't be read automatically"
   message, detail logged not shown. Keep today's failure *behaviour* (web
   upload shows the error; email/Telegram leave mail for the next poll) — no
   new retry queue (D5).
8. **Docs.** `docs/SYSADMIN.md` secrets table: replace the
   `ANTHROPIC_API_KEY` row with `LLMSEL_URL`/`LLMSEL_WORKER`/`LLMSEL_TOKEN`
   (gateway on the same VPS at `127.0.0.1:8010`; token issued by the gateway
   admin; not a provider key). Append a build-log section to
   `docs/superpowers/plans/2026-09-25-build-log.md` ("LLM gateway retrofit —
   2026-09-27"). In the go-live runbook
   (`2026-09-24-inbox-and-ingestion-channels.md`, Task 10) add the gateway
   token to the `.env` step.

## Rules

- Work only inside `/home/pedro/Desktop/Claude_Corner/Personal-llmsel`. Do
  not touch other subprojects' files.
- **Never** `git commit`, `git push`, SSH, or call any real API. Leave all
  changes uncommitted; the coordinating session reviews and commits.
- Full suite green at the end: `cd "/home/pedro/Desktop/Claude_Corner/Personal-llmsel/Home & Family" &&
  python3 -m pytest -q` (was 581 passing before this branch).
- After Tasks 1–2, and again after Task 5, **stop and report** (checkpoint):
  what changed, test counts, anything you were unsure about. Do not skip a
  checkpoint.
- If the plan is wrong about the code, say so at the checkpoint rather than
  improvising a workaround.
