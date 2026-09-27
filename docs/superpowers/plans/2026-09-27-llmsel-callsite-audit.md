# Home & Family Hub — Anthropic call-site inventory for llmsel retrofit

Repo: `/home/pedro/Desktop/Claude_Corner/Personal/Home & Family` (branch `feat/polish`, stacked on house-tab/inbox-channels/ask).
Gateway contract read: `LLM_Provider_Selection/docs/WORKER_CONTRACT.md`, `llmsel/gateway/schemas.py` (RunRequest/RunResponse/SubmitRequest/CollectResponse), `llmsel/workload_types.py` (nine seeded types + hard-filter fields).

All 10 direct-SDK call sites use the shared `client: Optional[AsyncAnthropic] = None` injection pattern (`anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)`). None use `submit`/batch today; none stream. No call site does its own retry loop on parse failure — a parse failure raises a typed `*Error` exception immediately and propagates to the caller's broad `except Exception` handler.

---

## 1. `app/services/extraction.py` — 4 calls (Financials, synchronous web request)

Caller chain: `app/routers/bills.py:upload_bill` → `app/services/ingestion.py:ingest()` (awaited directly, not backgrounded) → `finalize_document` → domain handler → `app/domains/financials/handler.py`. **A human is waiting on the HTTP response** for all four — confirmed by reading `bills.py` (`result = await ingest(...)` then `return RedirectResponse(...)`), no `BackgroundTasks` involved in this path.

### 1a. `extract_bill` (financials/handler.py:129, extraction.py:57-92)
- Model `claude-sonnet-5` (extraction.py:16,64), `max_tokens=4096`, `thinking={"type":"disabled"}` (extraction.py:65-66). No `tool_choice` (no tools).
- Input: `build_content_block(file_path)` + a text block ("Extract the billing data as JSON."). `build_content_block` (document_input.py:18-36) returns a `document` block (`media_type: application/pdf`, base64) for `.pdf`, or an `image` block (`png`/`jpeg`/`webp`) for images — whichever the uploaded file is. Whole PDF, all pages, no page-splitting or size cap in this code (Anthropic's own PDF/base64 limits apply implicitly, not enforced here).
- Output: `strip_json_fences(message.content[0].text)` → `json.loads` → dataclass fields with manual `float()`/`date.fromisoformat()` coercion (extraction.py:79-92). Any of `IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError` → raises `ExtractionError`, no retry.
- Proposed `response_schema`:
```json
{"type":"object","required":["provider","category_hint","amount","currency"],
 "properties":{
   "provider":{"type":"string"},
   "category_hint":{"type":"string","enum":["electricity","water","gas","telecom","insurance","subscriptions","groceries","health","home","other"]},
   "amount":{"type":"number"},
   "currency":{"type":"string","minLength":3,"maxLength":3},
   "due_date":{"type":["string","null"],"format":"date"},
   "paid_date":{"type":["string","null"],"format":"date"},
   "statement_period":{"type":["string","null"]}}}
```
- Error handling: caller `_ingest_bill` (financials/handler.py:127-135) catches bare `Exception` (comment explicitly: "any extraction failure (Anthropic SDK errors, malformed responses, anything unanticipated)") and calls `mark_needs_attention(session, document, str(exc))`. `str(exc)` of an `anthropic.APIError` is what `presentation.py`'s `_API_PATTERN` regex later recognizes ("Error code: NNN", `\banthropic\b`, `credit balance`, etc.) to show "The AI service couldn't be reached." to the user; a raw PDF-rejection 400 is caught by `_PDF_PATTERN` first → "The file couldn't be read."
- Proposed workload type: **`vision_extraction`** (needs image/PDF modality — no text-only type qualifies since `structured_extraction`'s `required_modalities` is `['text']` only per `workload_types.py:26-27`). Verb: **`run`** (human waiting on upload).

### 1b. `classify_document` (financials/handler.py:63, extraction.py:112-141)
- Model `claude-haiku-4-5-20251001` (`_CLASSIFICATION_MODEL`, line 95), `max_tokens=128`, `thinking={"type":"disabled"}`.
- Input: same `build_content_block` + text prompt.
- Output: JSON `{"document_type": "bill"|"statement"}`, validated against a 2-value enum in code (line 137-138) → `ClassificationError` on parse/validation failure.
- response_schema: `{"type":"object","required":["document_type"],"properties":{"document_type":{"enum":["bill","statement"]}}}`
- Errors: same broad catch in `financials/handler.py` around the classify step (line 63, inside the same try that wraps the whole PENDING→classified flow — verified `_ingest_bill`/`process_financials_document` wrap document-type-detection too).
- Proposed type: **`vision_extraction`** again (image/PDF input, short classification output) — note this is a case where a "classification"-shaped job is forced into `vision_extraction` purely because it's the only modality-eligible type; flagged under Surprises below. Verb **`run`**.

### 1c. `extract_statement_transactions` (financials/handler.py:243, extraction.py:201-246)
- Model `claude-sonnet-5` (`_STATEMENT_MODEL`), `max_tokens=16384`, `thinking={"type":"disabled"}`.
- Input: same PDF/image block ("all pages" per docstring), no chunking.
- Output/retry: explicit **truncation check** at line 224 — `if getattr(message, "stop_reason", None) == "max_tokens": raise StatementExtractionError(...)` — this is a non-retrying hard-fail, not a retry loop, and it is exactly the kind of check the contract says to express as validation-that-raises, not a loop (WORKER_CONTRACT.md "Domain rules too rich to express as a schema stay with you... never as a loop"). Under the gateway, `stop_reason` comes back on `Result`; this check should move to the caller inspecting `result.stop_reason` after `run()`.
- response_schema: object with `statement_period` (string|null) and `transactions` (array of `{date, description, amount:number, currency, type: enum[debit,credit,transfer], category_hint: enum[...]}`, `required: [date,description,amount,type]`). Given statements can be large, `max_tokens` here (16384) is at `structured_extraction`'s/`vision_extraction`'s ceiling (16000) already — flag: **current 16384 exceeds every workload type's `max_tokens_ceiling` of 16000** (workload_types.py, all types capped at 16000 except `classification` at 2000). `max_tokens` in `RunRequest` "may only ever lower the type's ceiling" — so this call site's literal 16384 request would need to drop to ≤16000 or accept truncation risk earlier.
- Proposed type: **`vision_extraction`**, verb **`run`**.

### 1d. `extract_utility_detail` (financials/handler.py:178, extraction.py:295-343)
- Model `claude-sonnet-5` (`_MODEL`), `max_tokens=1024`, `thinking={"type":"disabled"}`.
- Docstring explicitly: "Enrichment only — callers must treat failure as non-fatal" — confirmed at financials/handler.py:178 area (wrapped so utility-detail failure doesn't fail the whole bill).
- response_schema: object, `required:["period_label"]`, numeric fields nullable (`consumption_value`, `energy_cost`, `power_cost`, `fees_taxes_cost`, `vat_cost` all `type:["number","null"]`), plus `billing_period_start/end` (date|null), `invoice_number` (string|null), `consumption_unit` (string|null).
- Proposed type: **`vision_extraction`**, verb **`run`** (still inline in the same synchronous upload request, just non-fatal on failure).

---

## 2. `app/services/classification_engine.py::resolve_merchant_via_llm` (classification_engine.py:65-92)

- Called by `classify_transaction` (line 106-140), itself called from `financials/handler.py:165` (per-transaction, inside the same synchronous statement-ingest web request) **and** by a historical backfill script (per its own docstring: "the single entry point both the live ingestion pipeline and the historical backfill script use" — confirms a non-interactive batch caller also exists for this same function, per Pedro's memory note re: bank-statement backfill scripts).
- Model `claude-haiku-4-5-20251001` (`_MERCHANT_MODEL`), `max_tokens=256`, `thinking={"type":"disabled"}`.
- Input: **plain text only** — `f"Resolve this provider string as JSON: {raw_provider!r}"`, no content block, no `build_content_block` call. This is the one extraction-family call site with no document/image.
- Output: JSON `{"canonical_name","category","nature"}`; `category`/`nature` are coerced via `Category(...)`/`Nature(...)` enum constructors (line 88-89) — an invalid enum value raises `ValueError` → caught by the same broad tuple → `MerchantResolutionError`.
- response_schema: `{"type":"object","required":["canonical_name","category","nature"],"properties":{"canonical_name":{"type":"string"},"category":{"enum":[16 category values]},"nature":{"enum":["essential","discretionary"]}}}`
- Proposed type: **`classification`** (text-only, short record, "high-volume labelling" fits merchant resolution well; note `classification`'s `max_tokens_ceiling` is 2000, comfortably above the 256 used). Verb: **`run`** for the live path (web request, human waiting on statement upload), **`submit`** for the backfill script path (nothing waiting) — same function, two verbs depending on caller, exactly the `submit`/`run` split the contract describes for "classification" (WORKER_CONTRACT.md's own example is literally `classification`).

---

## 3. `app/services/wiki_engine.py::assess_document_for_wiki` (wiki_engine.py:98-133)

- Called from `ingest_into_wiki` (line 151), itself called from `financials/handler.py:213` (synchronous, per-statement-transaction, inside the same upload web request) and from `app/domains/base.py:164-170` (record path). Human waiting.
- Model `claude-haiku-4-5-20251001` (`_MODEL`, line 31), `max_tokens=1024`, `thinking={"type":"disabled"}`.
- Input: **plain text** — `context` string passed straight as `messages=[{"role":"user","content": context}]` (line 121), no content block. System prompt built per-call from domain schema + existing wiki page titles (wiki_engine.py:111-114) — not static, so this is a case where cache-prefix stability partly depends on how often existing-page lists change; not flagged as a problem, just noted for "stable content first" guidance.
- Output: JSON `{"pages":[{"title","summary","facts":{...}}]}` or `{"pages":[]}`. Parse failure (any of the 6 exception types) → `WikiAssessmentError`, uncaught here — propagates to whatever calls `ingest_into_wiki`.
- response_schema:
```json
{"type":"object","required":["pages"],"properties":{"pages":{"type":"array","items":{
  "type":"object","required":["title","summary","facts"],
  "properties":{"title":{"type":"string"},"summary":{"type":"string"},"facts":{"type":"object"}}}}}}
```
- Proposed type: **`structured_extraction`** (text-only, pulling standing facts to a schema; `min_context_length:100000` fits its "existing pages" list growing). Verb **`run`**.

---

## 4. `app/services/domain_classifier.py::suggest_domain_and_category` (domain_classifier.py:65-108)

- Called from `app/services/inbox_service.py:receive_document` (default `classify=suggest_domain_and_category` kwarg, line 71/78), which is invoked by **both** `app/channels/email_poller.py:100` and `app/channels/telegram_bot.py:81` — **background channel pollers, not a synchronous web request**. (Manual upload via `bills.py`/`house.py` does not call this — it's Inbox-only, for channel-received documents.) This is the one classifier call site that is genuinely non-interactive at the point of the LLM call, even though a human later reviews the Inbox suggestion.
- Model `claude-haiku-4-5-20251001` (`_MODEL`), `max_tokens=256`, `thinking={"type":"disabled"}`.
- Input: `build_content_block(file_path)` (document/image block) + optional sender-context text block + a fixed "Classify this document as JSON." text block (domain_classifier.py:77-80) — image/PDF input again.
- Output: JSON `{"domain","category","confidence","reason"}`, `confidence` clamped `min(max(...,0.0),1.0)` in code (line 94) rather than left to the model — this clamping is domain validation that should stay in the Worker per the contract ("validation... passes or raises" — though clamping isn't quite raise/pass, it's a silent correction; worth a design note but not blocking). Parse failure → `DomainClassificationError`, but this whole call is *already* wrapped in a broad `except Exception` one level up in `inbox_service.py:receive_document` (comment: "a classifier failure (API down, credits, bad response) must never lose the document") — so both the module and its only caller independently guard against LLM failure; no retry either way.
- response_schema: `{"type":"object","required":["domain","category","confidence","reason"],"properties":{"domain":{"type":["string","null"]},"category":{"type":["string","null"]},"confidence":{"type":"number","minimum":0,"maximum":1},"reason":{"type":"string"}}}` (domain/category are open — validated against the live registry in code afterward, not enum-able generically).
- Proposed type: **`vision_extraction`** (image/PDF input, forced by modality — same tension as classify_document above, despite being semantically closer to "classification"). Verb: **`submit`** — nothing is synchronously waiting; the poller can use the batch-eligible path (`vision_extraction.batch_permitted: 1`).

---

## 5. `app/domains/house/warranty.py::extract_warranty_dates` (warranty.py:64-82)

- Called from `app/domains/house/handler.py:35` (per its own module docstring: "The only LLM use is extract_warranty_dates, and only for [warranty-invoice category]") — House upload flow, `app/routers/house.py:upload` → same synchronous `ingest()`/`finalize_document` chain as bills. Human waiting.
- Model `claude-haiku-4-5-20251001` (`_MODEL`, line 28), `max_tokens=128`, `thinking={"type":"disabled"}`.
- Input: `build_content_block` (PDF/image), with an early return of `WarrantyDates(None, None)` if `UnsupportedFileTypeError` is raised (line 65-68) — the *only* call site that pre-empts an unsupported file type before ever calling the model, rather than letting `build_content_block` raise mid-call.
- Output: JSON `{"expiry_date","purchase_date"}`, both nullable dates; parse failure → `WarrantyExtractionError`.
- response_schema: `{"type":"object","required":["expiry_date","purchase_date"],"properties":{"expiry_date":{"type":["string","null"],"format":"date"},"purchase_date":{"type":["string","null"],"format":"date"}}}`
- Errors: caller (`house/handler.py`) — not fully traced line-by-line here beyond the docstring, but the pattern in this codebase (per financials) is a broad `except Exception` → `mark_needs_attention`; presentation.py's regex again governs what the user sees.
- Proposed type: **`vision_extraction`**, verb **`run`**.

---

## 6. `app/services/ask/engine.py::run_turn` — the tool-use loop (ask/engine.py:49-114)

- Called from `app/routers/ask.py`'s `_answer_in_background` (line 37-39), scheduled via `BackgroundTasks.add_task` from both `new_chat` (line 52) and `follow_up` (line 79). The HTTP response returns immediately (redirect / HTMX partial); the user then polls `GET /ask/turns/{turn_id}` or the conversation page for the answer. **A human is waiting on the answer, but not synchronously inside the request** — this is the ambiguous case for `run` vs `submit` (see Surprises).
- Model `claude-opus-5-5` (`_MODEL`, line 27, comment: "thinking can't be disabled; default effort (medium); tool_choice stays auto"), `max_tokens=16000` (`_MAX_TOKENS`), `thinking={"type":"adaptive"}` (line 67), `tool_choice` not set explicitly (defaults to `auto` per the code comment), up to `_MAX_TURNS=8` round trips, `_MAX_FILE_READS=2` (a domain rule enforced in the loop, not by the model).
- Input shape: `messages` built from `build_history(session, turn)` (prior turns) + the new question (line 59), `tools=[t.to_api() for t in tools]` where `tools = available_tools()` (ask/engine.py:55) — I did not open `app/services/ask/tools.py`/`contracts.py` for the exact tool schemas (out of scope per the audit's file list), but the loop shape is fully traced below.
- **Exact tool-use loop mechanics** (lines 64-98):
  - Loop up to `_MAX_TURNS` times, calling `messages.create(model=_MODEL, max_tokens=_MAX_TOKENS, thinking={"type":"adaptive"}, system=system, tools=[...], messages=messages)` each turn.
  - Token accounting: `in_tokens += response.usage.input_tokens; out_tokens += response.usage.output_tokens` accumulated **across all turns** of one question (line 70-71) — under the gateway this maps directly to `job_id`-scoped cost rollup ("rolls the job's turns into one cost figure").
  - Stop-reason handling: `"refusal"` → sets `error` and breaks (no answer). Anything **not** `"tool_use"` → treated as final: joins all `text`-type content blocks, strips, sets as `answer` (or `"No answer was produced."` if empty). Otherwise (`"tool_use"`): appends `{"role":"assistant","content": response.content}` **verbatim** (line 79 — exactly the gateway's "append `assistant_content` verbatim" rule; this codebase already does the right thing using the raw SDK response's `.content`, which under the gateway becomes `result.assistant_content`).
  - For each `tool_use` block in `response.content`: enforces the `_MAX_FILE_READS` cap specifically for `READ_FILE_TOOL` by name (line 84-89, a Worker-side domain rule — correctly stays client-side per the contract, not something to push into the gateway); otherwise dispatches via `_run_tool` (line 33-41), which catches **any** exception from a tool and turns it into an `is_error=True` `ToolOutput` rather than letting a tool bug kill the whole answer (`session.rollback()` too, since tools may have touched the session).
  - Builds one `tool_result` block per `tool_use` block via `_tool_result` (line 44-46: `{"type":"tool_result","tool_use_id": block_id, "content": ..., "is_error": ...}`) and appends them all as **one** `{"role":"user","content": results}` message (line 93) — i.e. parallel tool calls in one assistant turn get answered in one batched user turn, matching the contract's expected shape (`tool_result` blocks keyed by `tool_use_id`, matching `result.tool_calls[*]["id"]`).
  - If the loop exhausts `_MAX_TURNS` without a final answer, the `for...else` (line 94-95) sets `error = "Ran out of research steps before finding an answer."` — this is the Worker's own job-length cap, analogous to (but independent of) the gateway's `tool_turn_ceiling`/`tool_spend_ceiling_usd` on the workload type; both exist and would double-cap under the gateway, which is fine (contract doesn't forbid a tighter client-side cap, only forbids retry loops).
- **Mapping to gateway verbs**: `history` → `messages` (sent whole each turn, already done); `tools` → `tools` (already `to_api()`-shaped, i.e. provider wire shape, matches `RunRequest.tools: Optional[list[dict]]` expecting "the provider's own wire shape"); this Worker has no existing `job_id` concept at all — **needs to be invented** (the contract requires "one per logical job, re-sent on every turn of it" — `turn_id` or `conversation_id` would need to become that job_id; `turn_id` is more natural since token accounting and turn caps are already scoped per-turn, not per-conversation). `assistant_content` → append verbatim exactly as it does now with `response.content`. `stop_reason` → `result.stop_reason` (already checked for `"refusal"`/`"tool_use"`/else exactly as `RunResponse.stop_reason` would deliver it). `tool_calls` → `result.tool_calls`, replacing the current `for block in response.content: if block.type != "tool_use": continue` filtering (the gateway pre-filters this for you per schemas.py's `tool_calls: list[dict]`).
- Error handling: `except anthropic.APIError: ... error = "The AI service is unavailable right now."` (line 96-98) — the only call site with an explicit `anthropic.APIError` catch (as opposed to bare `Exception`) — under the gateway this becomes whatever exception `run()` raises on a transport/provider failure (not yet visible in this contract doc beyond "you get an error" language; the audit's requester should confirm the gateway's Python client's exception type before dropping the `import anthropic` here).
- Proposed workload type: **no clean fit** — flagged prominently under Surprises. Verb: **`run`** (tools require a live round trip per turn; `submit`'s 24h batch explicitly cannot serve `tools`/`messages` per `SubmitRequest`'s `extra='forbid'` and WORKER_CONTRACT.md's batching section).

---

## 7. `app/services/wiki_lint/llm_checks.py` + `runner.py` (opus-5-5, adaptive thinking, background)

- `check_domain` (llm_checks.py:101-126), called once per implemented domain from `run_llm_checks` (line 129-138), called from `execute_run` (runner.py:80-99), called from:
  - `run_lint` (runner.py:102-104) ← `app/jobs/wiki_lint.py:21` — a **timer job** (module docstring "timer"), fully background, nothing waiting.
  - `app/routers/wiki_lint.py:87` `_execute_in_background` via `BackgroundTasks` from the "Run now" button (`POST /wiki/lint/run`, line 80-88) — the HTTP response redirects immediately with a "Check started - refresh in a minute" message (line 88); **nothing waits on the response either**, the human is told to come back later. This is a cleaner `submit()` case than Ask's.
- Model `claude-opus-5-5` (`_MODEL`, llm_checks.py:28), `max_tokens=16000` (`_MAX_TOKENS`), `thinking={"type":"adaptive"}` (llm_checks.py:106). No `tools`.
- Input: plain text — `build_domain_payload` (llm_checks.py:72-98) renders wiki pages, claims, and up to `_MAX_SOURCES=500` document/record entries into one big text block (line 80-97), sent as `messages=[{"role":"user","content": payload.text}]` (line 107). System prompt (llm_checks.py:34-50) is fully static (`_SYSTEM_PROMPT`) — good prefix-cache candidate since only the user content varies per domain/run; put system first, which it already does.
- Output: explicit **refusal check** (`if message.stop_reason == "refusal": raise RuntimeError("the model declined the audit")`, line 108-109) before parsing — same non-retry pattern as extraction's truncation check. Then `strip_json_fences` + `json.loads` on the joined text blocks (line 110), building `FindingDraft`s filtered against known ids (lines 111-125) — this filtering is real domain validation that stays client-side either way.
- response_schema:
```json
{"type":"object","required":["findings"],"properties":{"findings":{"type":"array","items":{
  "type":"object","required":["kind","summary"],
  "properties":{"kind":{"enum":["contradiction","stale_claim","gap"]},"summary":{"type":"string"},
   "suggested_action":{"type":["string","null"]},
   "wiki_page_ids":{"type":"array","items":{"type":"integer"}},
   "claim_ids":{"type":"array","items":{"type":"integer"}},
   "document_ids":{"type":"array","items":{"type":"integer"}},
   "record_ids":{"type":"array","items":{"type":"integer"}}}}}}}
```
- Errors: `run_llm_checks` (line 129-138) catches bare `Exception` **per domain** so one domain's audit failure doesn't sink the whole run (`errors.append(f"{spec.label}: {exc}")`), surfaced later as `LintRun.errors` text and a `PARTIAL` status (runner.py:86-87) — this is the one call site whose failure text is shown to the user as a raw string on the Lint page rather than routed through `presentation.py`'s friendly-reason mapping (worth flagging to whoever owns that page, though out of scope for the retrofit itself).
- Proposed type: **`critique_review`** ("judging or scoring a draft; benefits from reasoning" — matches "audits... finds contradictions, stale claims and gaps" almost exactly; `thinking:1`/`effort:high` matches the adaptive-thinking opus call). Verb: **`submit`** for both the timer and the "Run now" button (neither has anything synchronously waiting).

---

## 8. Routers: `app/routers/ask.py`, `app/routers/wiki_lint.py` — client construction only

- `ask.py:22-23` `get_ask_client()` — a FastAPI dependency returning `AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)`, injected into `new_chat`/`follow_up` (lines 50, 71) and passed straight through to `_answer_in_background` → `run_turn`. No call-time logic here; this is purely the DI seam that the retrofit removes (replace with nothing — the gateway client, however it's shaped, would be constructed once at module/app level from `LLMSEL_URL`/`LLMSEL_WORKER`/`LLMSEL_TOKEN` env vars per WORKER_CONTRACT.md's Identity section, not per-request).
- `wiki_lint.py:35-36` `get_lint_client()` — identical pattern, injected into `run_now` (line 82) → `_execute_in_background` → `execute_run`.
- Both routers' only other Anthropic-specific content is the `from anthropic import AsyncAnthropic` import and the type hints (`client: AsyncAnthropic = Depends(...)`) — these disappear entirely once the gateway client type replaces `AsyncAnthropic` everywhere.

---

## 9. `app/config.py` and `app/services/presentation.py`

- `config.py:29`: `ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]` — **required at import time**, no default, no `Optional`. This is the one line every module transitively depends on merely by importing `app.config.settings`, confirmed by `docs/superpowers/plans/2026-09-25-build-log.md:6`'s own deviation note ("scratch migration runs need ANTHROPIC_API_KEY in env... dummy value exported"). Per WORKER_CONTRACT.md step 6, this line should be deleted entirely (replaced by `LLMSEL_URL`/`LLMSEL_WORKER`/`LLMSEL_TOKEN`), not made optional.
- `presentation.py:23-33`: `_API_PATTERN` regex (`error code:\s*\d+|authentication_error|permission_error|rate_limit_error|overloaded_error|insufficient_quota|credit balance|\bapi\b|\btimeout\b|\bunavailable\b|connection error|\banthropic\b`) is matched against any stored `failure_reason`/`classifier_note` string to show "The AI service couldn't be reached." This regex is Anthropic-SDK-specific (`\banthropic\b`, `authentication_error`/`permission_error`/`overloaded_error` are Anthropic's own error-type names) and **will not match** whatever exception text/shape the gateway's `run()`/`submit()` raise on failure — this needs updating in lockstep with the retrofit or every gateway-side failure will fall through to the generic `_GENERIC_TECHNICAL_PATTERN` bucket ("Something went wrong processing this document.") instead of the more specific "AI service unavailable" message. `_PDF_PATTERN` (line 34) is checked *first* specifically because "the API rejects a broken PDF with an Error code: 400 that would otherwise read as an unreachable service" (comment, line 48-49) — this ordering dependency and its reasoning should carry over to whatever error text the gateway produces for a bad-file rejection vs a real outage.

---

## 10. Test injection pattern — no shared fixture, ~10 independent fakes

`tests/conftest.py` only sets `ANTHROPIC_API_KEY=test-key` as an env default (line 3) so `app.config.settings` can import; it defines **no** shared Anthropic-client fixture. Every test module rolls its own minimal fake matching whatever subset of the SDK response shape it needs:

| File | Fake(s) defined | Shape needed | Usages (`client=` sites) |
|---|---|---|---|
| `tests/fake_anthropic.py` | `FakeAnthropic`/`Block`/`Response`/`text_response`/`tool_response`/`refusal_response` — the one *shared* fake, used by ask + wiki_lint tests | full: `stop_reason`, multi-block `content` (`thinking`, `text`, `tool_use`), `usage` | imported by `test_wiki_lint_llm.py`, `test_ask_engine.py`, `test_ask_router.py` |
| `tests/test_extraction.py` | `_FakeAnthropicClient`, `_FakeAnthropicClientEmptyContent` (own, local) | `message.content[0].text`, optional `stop_reason` param | 20 occurrences, 320 lines |
| `tests/test_classification_engine.py` | `_FakeAnthropicClient` (own, local) | same minimal shape | 10 occurrences, 693 lines |
| `tests/test_domain_classifier.py` | `_FakeClient`/`_Msg` (own, local, no shared import) | `.calls` list + `.content[0].text` | inline, 101 lines |
| `tests/test_house_warranty.py` | `_FakeClient`/`_FakeMessages`/`_FakeMessage`/`_FakeContent` (own, local) | same | inline, 127 lines |
| `tests/test_wiki_engine.py` | `_FakeAnthropicClient` (own, local) | same | 4 occurrences, 220 lines |
| `tests/test_wiki_lint_llm.py` | uses shared `fake_anthropic.py` | full shape (needs `stop_reason`/refusal) | 5 occurrences, 70 lines |
| `tests/test_ask_engine.py` | uses shared `fake_anthropic.py`, plus one raw `anthropic.APIConnectionError(request=httpx.Request(...))` construction (line 109) to test the `except anthropic.APIError` path directly | full shape + real SDK exception type | 8 occurrences, 112 lines |
| `tests/test_ask_router.py` | uses shared `fake_anthropic.py` | full shape | 1 occurrence, 108 lines |
| `tests/test_financials_handler.py` | `_FakeAnthropicClient` (own, local) — comment at line 879 confirms it deliberately feeds "the real `classify_transaction` ... a fake Anthropic client" for an integration-style test | minimal shape | 2 occurrences, 927 lines |
| `tests/test_e2e_bill_flow.py` | `_FakeAnthropicClient` (own, local) | minimal shape | 2 occurrences, 210 lines |

**Total: 11 test files touch this, at least 6 independently-defined fake-client classes plus the one shared `fake_anthropic.py` module.** Retrofitting to the gateway's `run()`/`submit()` functions means every one of these fakes needs to either (a) be replaced by a fake `run`/`submit` callable with a *different* call signature (`workload_type, system, user, ...` instead of `model=..., messages=..., system=...`), or (b) the codebase adopts one shared fake gateway client and all ~10 files are edited to use it — the latter is the natural moment to consolidate, since today's duplication (six near-identical `_FakeAnthropicClient` classes) is itself pre-existing debt independent of the retrofit. `test_ask_engine.py:109`'s direct construction of `anthropic.APIConnectionError` is the one test that would break differently — it tests the literal SDK exception type, which won't exist post-retrofit unless the gateway client re-raises/wraps it identically.

---

## Every other ANTHROPIC_API_KEY / anthropic-package reference

- `requirements.txt:5` — `anthropic>=0.85.0` (the only line pinning the SDK; delete once retrofit is done, per contract step 6).
- `.env.example:1` — `ANTHROPIC_API_KEY=sk-ant-your_key_here` (delete).
- `README.md:17` — `cp .env.example .env   # then fill in ANTHROPIC_API_KEY at minimum` (update).
- `docs/ARCHITECTURE.md:87,109` — describes `app/config.py` as reading `ANTHROPIC_API_KEY` (update to describe `LLMSEL_*` vars instead).
- `docs/SYSADMIN.md:233`: table row —

  > `ANTHROPIC_API_KEY` | `/srv/home-hub/app/.env` on the VPS (never committed) | All Claude calls: document classification, bill/statement extraction, merchant resolution, wiki-worthiness assessment. **Out of credit as of 2026-09-26** — every call returns 400 "credit balance is too low"; not being topped up. To be replaced by routing through the LLM Selector (backlog item, see build log)

  — **this confirms the Hub's direct Anthropic key is currently dead/out-of-credit in production**, and that the retrofit to llmsel is already the documented intended fix, not a speculative idea. `docs/SYSADMIN.md:241` also notes it's one of several optional-vs-required env vars (contextually, not itself optional).
- `.github/workflows/deploy.yml` — **no** `ANTHROPIC`/`anthropic` reference at all (checked, zero matches) — the deploy workflow does not currently inject the key as a secret at deploy time; it must be set directly in the VPS `.env` per SYSADMIN.md. This means the retrofit's "remove from deploy workflow secrets" step (contract step 6) has nothing to do here — worth confirming with Pedro whether that's because it's already absent, or because deploy doesn't manage `.env` at all (SYSADMIN.md phrasing "on the VPS" suggests it's hand-placed, not pushed by CI).
- `docs/superpowers/plans/*.md` — numerous historical plan documents (2026-08-17, 2026-08-18 ×2, 2026-08-21, 2026-08-25, 2026-09-24 ×3, 2026-09-25) reference `ANTHROPIC_API_KEY`/`AsyncAnthropic` as design examples; these are historical planning artifacts, not live code — **not** something the retrofit needs to touch, listed here only for completeness since the grep matched them.

---

## Surprises / ambiguities (flagged rather than guessed)

1. **`ANTHROPIC_API_KEY` is already out of credit in production** (SYSADMIN.md:233, "as of 2026-09-26" — one day before this audit) — every Claude call in the live Hub is currently failing with a 400. The retrofit isn't a precaution; it's fixing an active outage.
2. **Ask's tool-use loop (`ask/engine.py`) has no clean workload-type fit.** It needs `tools_permitted=1` (only `long_form_writing` and `critique_review` have it) but is used interactively with a human waiting on the answer (`sort_preference: latency` is what `interactive_assist` declares, but `interactive_assist` forbids tools and isn't one of the two tool-permitted types). Forcing it into `critique_review` (closest: `thinking:1`, matches `adaptive` thinking; tools permitted) means accepting `sort_preference: price` instead of `latency` for a human-waiting job. This looks like exactly the case WORKER_CONTRACT.md tells a Worker to flag rather than force ("If none fits, ask for a new type... A Worker that could mint its own types could mint itself a cheaper one" — i.e., this is a decision for Pedro/the gateway operator in `/admin`, not something to silently resolve here).
3. **Every document/image-carrying call (6 of 10 call sites) is forced into `vision_extraction`** regardless of whether the job is semantically "extraction" or "classification" (`classify_document`, `suggest_domain_and_category` are classification jobs that happen to need image modality) — because `structured_extraction`/`classification` both declare `required_modalities: ['text']` only. This isn't a bug in this audit, it's a real gap in the nine seeded types worth surfacing to whoever owns `/admin` model pairing: today `vision_extraction`'s `min_quality_tier: high` may route short/cheap classification-shaped image jobs (128-256 output tokens) to the same tier of model as full statement extraction (16384 tokens), which may not be the intended cost trade-off.
4. **`extract_statement_transactions`'s current `max_tokens=16384` exceeds every workload type's `max_tokens_ceiling` (16000, or 2000 for `classification`)** in the seeded types — this specific call would need its cap lowered to fit under the gateway (`max_tokens` may only lower the ceiling, never raise it) or `vision_extraction`'s ceiling would need raising in `/admin`, which is an operator decision, not a code change here.
5. **`presentation.py`'s error-classification regex is Anthropic-SDK-specific** (`authentication_error`, `overloaded_error`, `\banthropic\b`, etc.) and will silently stop matching real failures once the gateway's own exception/error text replaces the raw Anthropic SDK error strings — this regex needs a coordinated update, not just the call sites, or every gateway failure will show the generic "Something went wrong" message instead of the more informative "AI service unavailable" one.
6. **No shared test fixture for the Anthropic client exists** — 6+ independently-defined fake client classes across 11 test files (detailed in section 10) will each need updating to the gateway's `run`/`submit` signature; this is a good moment to consolidate onto one shared fake, but that consolidation is optional cleanup, not required by the retrofit itself.
7. **`resolve_merchant_via_llm`/`classify_transaction` has two real callers with opposite waiting-semantics** — the live per-transaction ingestion path (human waiting, wants `run`) and a historical backfill script (nothing waiting, wants `submit`) — same function, confirmed by its own docstring, so the retrofit must thread verb choice through as a parameter/branch rather than hardcoding one verb into `resolve_merchant_via_llm` itself.
8. **The deploy workflow (`.github/workflows/deploy.yml`) has zero Anthropic references already** — nothing to remove there; the key lives only in the VPS's hand-placed `.env` per SYSADMIN.md. Worth confirming with Pedro this is deliberate (CI doesn't manage secrets/`.env` at all for this app) before assuming step 6 of the retrofit checklist is a no-op for this repo.
