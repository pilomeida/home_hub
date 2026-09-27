# Review of the Hub retrofit (Claude, 2026-09-27)

Verified: 602 passed (re-run independently), no `anthropic` imports left,
client is thin (no retry, no model names, no key). The Ask loop is right
(job_id per question, tools computed once, assistant_content verbatim, usage keys match the
gateway's). Fix 1 and 2, then report again.

## 1. BUG — the family-facing error messages never fire in production

`presentation.py` maps reasons matching `gateway returned 5\d\d` /
`gateway returned 4\d\d`. But `GatewayError` stringifies to its `detail`
alone (`super().__init__(detail)`, i.e. the raw response body), so no real failure
reason ever contains "gateway returned …". The tests pass only because
`tests/test_presentation.py:110-125` hand-type strings production never
produces.

Fix: make `GatewayError.__str__` return `gateway returned {status}: {detail}`
(and `gateway unreachable at …` for status 0). Then change the presentation
tests to build each reason the way production does: raise a real
`GatewayError(status, detail)` through the same code path that stores
`failure_reason` (ingestion / handler) and assert on the stored text. Never
hand-type a reason again.

## 2. BUG RISK — response schemas are stricter than the prompts and parsers

Rule: a `response_schema` must be **no stricter than the prompt + the
parser**. Anything stricter makes the gateway retry and then fail a document
that the old code ingested.

Example, `_BILL_SCHEMA`: the prompt says "If a field cannot be determined,
use null", and the parser defaults `currency`→EUR, `category_hint`→other,
`amount`→0.0. Yet the schema requires `currency` and `category_hint` as
non-null strings, and `amount` as a non-null number. A bill with no visible currency now
fails after 3 paid attempts instead of saving as EUR.

Audit all six schemas with that rule: allow `null` wherever the prompt
permits it or the parser tolerates it, and keep `required` only for keys the
parser indexes directly (e.g. `data["provider"]`). Add one test per schema
feeding a null-heavy but parser-valid response through `jsonschema.validate`.

## 3. Minor — constants' home

The workload-type constants live in `extraction.py`, and the Ask engine
imports them from there. Move them to `app/llm_gateway.py` (or a small
`app/llm_workloads.py`) and import them from there everywhere.

## 4. Do NOT create `vision_classification` (or anything) in the live gateway

Creating workload types, registering the `hub` Worker, tokens and pairings
are live steps Claude runs over SSH with Pedro's go-ahead. The builder must
not SSH or touch the live DB.

## Agreed as built

`assess_document_for_wiki` → `classification`; every call uses `run` (no
`submit`). Both match the plan.
