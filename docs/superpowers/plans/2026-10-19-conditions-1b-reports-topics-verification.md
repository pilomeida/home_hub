# Phase 1B: Written Reports, the Topic Baseline and Reading Verification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. The master plan (`2026-10-19-conditions-master-plan.md`) Global Constraints apply to every task. Phase 1 (`...-conditions-1-reader-and-context.md`) is already deployed (home_hub `18c7c22`).

**Goal:** Read written reports (like paper 200) around their own topic hierarchy instead of forcing them into a lab-sheet shape; keep every number a report states as a tracked metric; build a baseline of topics that grows from what recurs across the family's papers; and verify readings so a wrong one is flagged, not silent.

**Architecture:** A registry of canonical topics (`health_topics`, a tree) is the dynamic baseline. A report's printed outline is matched against it when the paper is filed: same name under the same parent = same topic; a new heading becomes a *proposed* topic (with a "looks like X" suggestion when a similar one exists); a person approves, renames or merges (the areas pattern). Each result row of a report points at its topic (`health_measurements.topic_id`), so one topic's conclusions and metrics across years are one query (the topic thread page, with the person chosen). The paper page shows a report as an outline with prose plus a separate Metrics table. Verification: layout-aware reading, checks against the paper's text, a second independent reading for reports and papers with no text layer (disagreements become plain-words notes), a "the outline looks right" confirmation that teaches later readings the provider's outline, and a benchmark that understands prose.

**Tech Stack:** Python/FastAPI/SQLModel/Alembic (SQLite), Jinja2, pytest + pytest-asyncio, `tests/fakes/fake_gateway.py`.

**Spec:** `/home/pedro/Desktop/Claude_Corner/brainstorms/2026-10-19-health-conditions-episodes-and-section-hierarchy.md` (Q11 + the Q12 answer "both, with the topic as the main thread") and the master plan.

## Global Constraints

- No commit, push or deploy without Pedro's explicit word; stage by file name only (shared working tree).
- Reader calls stay `sensitivity=Tier.HIGH.value`; the second reading too.
- Test inputs come from the production path: `extract_health_document` / `read_health_document` with `FakeGateway`, then `store.apply_extraction`; never hand-built stored rows (project CLAUDE.md).
- Messages rule: every note, warning or error a person can read says what / why / what to do (`app/problems.py` for errors; notes are plain-words sentences).
- Nothing is created or merged silently as *approved*: topics the Hub creates are `approved=False` proposals; merges and approvals are a person's tap (Pedro, Q2).
- Additive migrations only. Head today: `d1f6b8c2e547` (re-check with `python3 -m alembic heads`).
- Written reports only get topics in this phase (`HealthEntry.layout == "report"`). Lab sheets keep their current key-based threading (`test_key`); extending the baseline to sheet headings is a later step.
- Tests run from `Home & Family/`: `python3 -m pytest <files> -q`.

## File Structure

| File | Change |
|---|---|
| `app/models/health.py` | new `HealthTopic`, `HealthOutlineProfile`; `HealthMeasurement.topic_id`; `HealthEntry.layout`, `.outline_confirmed_by`, `.outline_confirmed_at` |
| `alembic/versions/<new>_health_topics.py` | the migration |
| `app/domains/health/topics.py` | new: topic registry (resolve, propose, approve, rename, merge, thread, prompt names) |
| `app/domains/health/outlines.py` | new: confirm an outline, build prompt guidance from confirmed outlines + baseline |
| `app/domains/health/extraction.py` | `layout` in prompt/schema/dataclass; `guidance` parameter |
| `app/domains/health/reading.py` | guidance pass-through; second independent reading + comparison |
| `app/domains/health/checks.py` | `compare_readings()` |
| `app/domains/health/store.py` | persist layout, attach topics |
| `app/domains/health/handler.py` | pass guidance |
| `app/domains/health/benchmark.py` | prose similarity, layout |
| `app/routers/health.py` | topics pages/routes, paper page report mode, outline confirm route |
| `app/templates/health/{topics,topic,detail}.html` | new two, changed one |
| `tests/test_health_topics.py`, `tests/test_health_report_layout.py`, `tests/test_health_second_read.py`, `tests/test_health_topic_pages.py` | new |
| `tests/test_health_benchmark.py` | extended |

---

### Task 1: Tables and columns

**Files:** Modify `app/models/health.py`; Create the migration; Test `tests/test_health_models.py` (add one test).

**Interfaces — Produces:**

```python
class HealthTopic(SQLModel, table=True):          # the baseline: one node per heading, in a tree
    __tablename__ = "health_topics"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    match_key: str = Field(index=True)
    parent_id: Optional[int] = Field(default=None, foreign_key="health_topics.id", index=True)
    approved: bool = Field(default=False)
    merged_into_id: Optional[int] = Field(default=None, foreign_key="health_topics.id")
    similar_to_id: Optional[int] = Field(default=None, foreign_key="health_topics.id")   # "looks like X": a suggestion only
    created_at: datetime = Field(default_factory=datetime.utcnow)

class HealthOutlineProfile(SQLModel, table=True):  # an outline a person confirmed as right for a provider
    __tablename__ = "health_outline_profiles"
    id: Optional[int] = Field(default=None, primary_key=True)
    provider_key: str = Field(index=True)
    provider: str
    layout: str
    outline_json: str                                # JSON list of printed paths, in paper order
    confirmed_by: Optional[str] = None
    confirmed_at: datetime = Field(default_factory=datetime.utcnow)
```

plus `HealthMeasurement.topic_id: Optional[int] = Field(default=None, foreign_key="health_topics.id", index=True)`, `HealthEntry.layout: Optional[str] = None` (`"sheet" | "report"`), `HealthEntry.outline_confirmed_by: Optional[str] = None`, `HealthEntry.outline_confirmed_at: Optional[datetime] = None`.

- [ ] **Step 1: Test** (append to `tests/test_health_models.py`; follow how that file builds a session/entry): create a `HealthTopic` root and a child with `parent_id`, a measurement with `topic_id`, an entry with `layout="report"`; assert they round-trip, `merged_into_id`/`similar_to_id` default `None`, `approved` defaults `False`, and a `HealthOutlineProfile` stores `outline_json`.
- [ ] **Step 2:** run `python3 -m pytest tests/test_health_models.py -q` → FAIL.
- [ ] **Step 3: Implement** the models above and the Alembic migration (revision id of your choosing, `down_revision = "d1f6b8c2e547"` unless `alembic heads` says otherwise; `op.create_table` for the two tables; `batch_alter_table` `add_column` for the four nullable columns, as other migrations in `alembic/versions/` do; downgrade drops them). Make sure the new tables are imported wherever the other health models are, so `SQLModel.metadata` and the test database create them.
- [ ] **Step 4:** the model test PASSES, and `python3 -m alembic heads` shows one head; run `python3 -m pytest tests/test_health_models.py tests/test_health_people_areas.py tests/test_health_delete.py -q` (purge must still work: `store.purge_derived` deletes measurements before the entry; topics are not deleted with an entry).
- [ ] **Step 5:** checkpoint (no commit).

---

### Task 2: The topic registry (the baseline)

**Files:** Create `app/domains/health/topics.py`; Test `tests/test_health_topics.py`.

**Interfaces — Produces** (all take a `Session` first):

```python
DOCUMENT_SECTIONS: frozenset[str]        # match_keys of wrapper headings that are not topics: conclusoes, conclusao, impressao,
                                          # resultados, resultado, comentarios, comentario, relatorio, descricao, achados,
                                          # findings, conclusions, impression, results, report
def topic_path(group_path: str) -> list[str]
    # the printed heading path as topic names: split on PATH_SEP, drop LEADING segments whose match_key is in DOCUMENT_SECTIONS
def resolve_topic(session, path: list[str]) -> Optional[HealthTopic]
    # walk the path; for each name find the child of the current parent with the same match_key (following merged_into);
    # if none, create it approved=False and set similar_to_id (below); return the deepest topic, or None for an empty path
def attach_topics(session, entry: HealthEntry) -> None
    # only when entry.layout == "report": for each measurement of the entry set topic_id = resolve_topic(session, topic_path(m.group_path)).id
    # (None when the path is empty); commit
def approve_topic(session, topic_id: int) -> None
def rename_topic(session, topic_id: int, new_name: str) -> None        # AreaError-style Problem if empty or a sibling has that name
def merge_topics(session, source_id: int, target_id: int) -> None
    # moves the source's measurements to the target, re-parents its children (a child whose match_key already exists under the
    # target is merged into that one, recursively), sets source.merged_into_id=target and approved=True; refuses itself/already-merged
def names_for_prompt(session, limit: int = 60) -> list[str]
    # approved, non-merged topics as full paths "Triplex carotídeo › Eixo Carotídeo direito", sorted
def thread(session, topic_id: int, person_id: int) -> dict
    # {"topic": HealthTopic, "path": "A › B", "entries": [{"entry","document","items":[HealthMeasurement,...]}] newest first,
    #  "metrics": [{"label","unit","points":[(date, value),...] oldest first}]}
    # items = the person's non-previous measurements whose topic is this topic or any descendant; metrics = items with a numeric value,
    # grouped by (group_path, test, unit); a metric's points include its is_previous rows with their own dates.
```

`similar_to_id` rule on creation: among other non-merged topics, the one with the highest `difflib.SequenceMatcher(None, a, b).ratio()` of `match_key`s, if that ratio is ≥ 0.88 (an identical `match_key` under a different parent counts, ratio 1.0); else `None`.

Errors use `app.problems.Problem` with a small `TopicError(Exception)` carrying `.problem` and `.status`, as `areas.AreaError` does (messages: what / why / what to do).

- [ ] **Step 1: Write tests** (`tests/test_health_topics.py`). Build entries the production way: use `extract_health_document` with `FakeGateway` on a reply whose `layout` is `"report"` is Task 3's job; here create entries with `tests/test_health_browse._paper`-style helper from `ExtractedHealth` + `apply_extraction`, then set `entry.layout = "report"` and call `attach_topics`. Required tests:
  1. `topic_path("Conclusões › Triplex carotídeo › Eixo Carotídeo direito") == ["Triplex carotídeo", "Eixo Carotídeo direito"]`; `topic_path("Triplex carotídeo")` unchanged; a wrapper that is not leading stays (`"A › Conclusões" → ["A","Conclusões"]`); `topic_path("")==[]`.
  2. `resolve_topic` creates a root and child unapproved; calling again returns the same rows (accent/case-insensitive: "Eixo carotideo direito" finds "Eixo Carotídeo direito"); no duplicates.
  3. A second paper whose path repeats the same chapter attaches to the same `topic_id`; a different-year paper's measurements then appear together in `thread(...)`.
  4. `similar_to_id`: a new topic "Eixo carotídeo dto" (ratio ≥ 0.88 is not guaranteed — use "Eixo Carotideo direito" under a different parent so the key is identical) gets `similar_to_id` pointing at the existing one; a clearly different name gets `None`.
  5. `merge_topics` moves measurements and re-parents/merges children; merging into itself or an already-merged topic raises `TopicError` with non-empty what/why/next_step.
  6. `rename_topic` refuses an empty name and a sibling clash.
  7. `names_for_prompt` lists only approved, non-merged topics as full paths.
  8. `thread` for person A never contains person B's rows; metrics only for numeric rows; previous rows appear as earlier points.
- [ ] **Step 2:** run → FAIL (module missing).
- [ ] **Step 3: Implement** `topics.py` to the interfaces above (follow `areas.py` for structure, `Problem` messages and merge patterns).
- [ ] **Step 4:** `python3 -m pytest tests/test_health_topics.py tests/test_health_models.py -q` → PASS.
- [ ] **Step 5:** checkpoint.

---

### Task 3: The reader knows layouts; the store attaches topics; guidance flows in

**Files:** Modify `app/domains/health/extraction.py`, `store.py`, `handler.py`; Create `app/domains/health/outlines.py`; Test `tests/test_health_report_layout.py`.

**Interfaces**
- Consumes: `topics.attach_topics`, `topics.names_for_prompt`.
- Produces:
  - `ExtractedHealth.layout: str = ""` (`"sheet"|"report"|""`); `_SCHEMA["properties"]["layout"] = _STR`; parsed as `layout if layout in ("sheet","report") else ""`.
  - `extract_health_document(..., guidance: str = "")`: when non-empty it is appended to the system prompt after the existing-names lines. `read_health_document(..., guidance: str = "")` passes it through (signature change in `reading.py`, also forwarded in `_second_look` untouched).
  - `outlines.guidance(session, limit: int = 6, max_chars: int = 3000) -> str` — `""` when nothing; otherwise text like: `Topics already in the family's baseline (reuse a name exactly when the paper has that heading; the paper's own structure always wins):\n- A › B …` plus, for the `limit` most recent confirmed `HealthOutlineProfile`s: `Outline a person confirmed as right for <provider>:\n- path …`.
  - `store.apply_extraction` sets `entry.layout = extracted.layout or None` and, after the rows are written, calls `topics.attach_topics(session, entry)`.
  - `HealthHandler.process` passes `guidance=outlines.guidance(session)`.

Prompt additions (inside `_SYSTEM`, no literal braces except doubled; keep `{full_text_rule}` and the `- sections:` anchors intact; `_RESULTS_RULES` slicing must still work):
1. A new bullet before `- summary:`: `- layout: "sheet" if the paper is mostly a table of results (a laboratory sheet), "report" if it is mostly written text under headings (an imaging, consultation or examination report).`
2. After the numbers sentence in the sections block: `In a written report the headings ARE the topics: keep the report's own outline exactly as printed, give each topic's written conclusion as the result "Conclusão" under it, and make every number the report states (in text or in a table) a separate result under the topic it belongs to, with its unit. A report that prints no numbers has none: never invent a measurement.`

- [ ] **Step 1: Tests** (`tests/test_health_report_layout.py`):
  1. `extract_health_document` on a reply with `"layout": "report"` returns `layout == "report"`; an unknown value gives `""`.
  2. Filing a report through `extract_health_document` → `store.apply_extraction` (document from `make_document(session, domain=Domain.HEALTH, category="imaging_report")`) with sections `[{"path": ["Conclusões","Triplex carotídeo","Eixo Carotídeo direito"], "results":[{"test":"Conclusão","value_text":"…"}]}, {"path":["Conclusões","ECO/DTC"],"results":[{"test":"Conclusão","value_text":"…"},{"test":"Índice de pulsatilidade","value":0.9}]}]` gives `entry.layout == "report"`, each row's `topic_id` set to the deepest topic, `group_path` still stored as printed (with "Conclusões ›"), and three topics created (Triplex carotídeo, Eixo Carotídeo direito, ECO/DTC) all `approved == False`.
  3. A `"sheet"` layout paper gets `topic_id is None` on every row and creates no topics.
  4. Two reports from different years: the second reuses the first's topics (same ids; no new rows).
  5. `outlines.guidance(session)` is `""` on an empty database; after a report is filed and its topics approved it contains the topic path; the guidance text reaches the gateway: run `extract_health_document(..., guidance="X-MARK")` with a `FakeGateway` and assert `"X-MARK"` is in `fake.requests[0]["system"]`.
  6. `HealthHandler.process` sends the guidance: use the existing handler test pattern in `tests/test_health_handler.py` (look at how it fakes the gateway) and assert the baseline topic path is in the system prompt of the first request after a topic is approved.
  7. `reading._RESULTS_RULES` still contains "Conclusão" and ends before `{full_text_rule}`; `ex._SYSTEM` has a `layout` bullet.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement** as specified. Do not change how `_measurements` parses sections.
- [ ] **Step 4:** `python3 -m pytest tests/test_health_report_layout.py tests/test_health_extraction.py tests/test_health_sections.py tests/test_health_outline.py tests/test_health_context.py tests/test_health_handler.py tests/test_health_checks.py -q` → PASS.
- [ ] **Step 5:** checkpoint.

---

### Task 4: Verification — compare two readings; confirm an outline

**Files:** Modify `app/domains/health/checks.py`, `reading.py`; extend `outlines.py`; Test `tests/test_health_second_read.py`.

**Interfaces**
- Produces:
  - `checks.compare_readings(a: ExtractedHealth, b: ExtractedHealth) -> list[str]` (pure). Rows are keyed `(topic_key, test_key, unit_key)` = `match_key` of `group_path` (after `topics.topic_path`, joined), test, unit. Notes, plain words, at most 5 per kind, in this order:
    - `"Only one of two readings found: <path> › <test>. Look at the paper."` for a key in exactly one reading;
    - `"Two readings gave different values for <path> › <test>: <x> and <y>."` for numeric differences (`abs diff > 1e-6`);
    - `"The wording read under <path> differs between two readings. Look at the paper."` when two `value_text`s have `difflib` ratio < 0.85 (compare the whitespace-squeezed `match_key`s; equal after squeezing is no difference);
    - `"The two readings disagree about the date of the paper."` / `"…about whose paper it is."` for `document_date` / `person_name`.
  - `reading.read_health_document(..., second_read: bool = True)`: after the first reading and its text check, if `second_read` and (`x.layout == "report"` or `source is None`): run a second `ex.extract_health_document` with the same arguments inside `try/except Exception`; on success `notes += compare_readings(x, y)`; on failure `notes.append("A second reading to compare against did not work (<ExcType>); this reading is unchecked.")`. The first reading is the one stored. The notes join the existing `check_notes` cap.
  - `outlines.confirm_outline(session, entry: HealthEntry, by: str | None) -> HealthOutlineProfile`: builds the outline = the distinct `group_path`s of the entry's non-previous measurements in `position` order; upserts the profile for `match_key(entry.provider or "")` + `entry.layout or "sheet"` (replacing an existing one); sets `entry.outline_confirmed_by/at`; commits. Raises `TopicError`-style `Problem` (what/why/next step) when the entry has no provider or no measurements.
- [ ] **Step 1: Tests** (`tests/test_health_second_read.py`):
  1. `compare_readings` of two identical extractions → `[]`; wording difference only in spacing → `[]`.
  2. Each kind of difference yields its note (one test per kind, built from two `extract_health_document` runs over two reply dicts that differ in exactly that item).
  3. `read_health_document` on a `"report"` reply with `FakeGateway([reply, reply_with_one_row_missing])` stores the first reading and `check_notes` contains the "Only one of two readings found" sentence; the gateway got exactly 2 `HEALTH_EXTRACTION` requests, both with `sensitivity == "house_high"`.
  4. A `"sheet"` reply on a PDF with a text layer (monkeypatch `ex.paper_text` as `tests/test_health_sections.py` does) makes ONE gateway call; a sheet image (no text layer) makes two.
  5. The second call failing (`FakeGateway(results=[reply], errors=[GatewayError(503, "overloaded")])` — build the error with the production class) leaves the reading intact and adds the "did not work" note.
  6. `confirm_outline` stores a profile for the provider, replaces it on a second confirmation, sets the entry's `outline_confirmed_*`, and `outlines.guidance(session)` then contains that outline; the error case returns a Problem with all three parts.
  7. Existing tests that read an image with a single scripted reply keep passing: where one now needs `second_read=False` (they are not about verification), pass it explicitly in the test call — do not change production defaults.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `python3 -m pytest tests/test_health_second_read.py tests/test_health_report_layout.py tests/test_health_extraction.py tests/test_health_sections.py tests/test_health_checks.py tests/test_health_benchmark.py tests/test_health_router.py tests/test_health_intake.py tests/test_health_handler.py -q` → PASS (adjust only the tests that now need a second scripted reply or `second_read=False`).
- [ ] **Step 5:** checkpoint.

---

### Task 5: Pages — topic baseline, topic thread, report mode on the paper page

**Files:** Modify `app/routers/health.py`, `app/templates/health/detail.html`, `app/templates/health/landing.html` (nav link only if navigation is defined there; else `app/domains/health/spec.py` `nav_links`); Create `app/templates/health/topics.html`, `app/templates/health/topic.html`; Test `tests/test_health_topic_pages.py`.

**Interfaces — Produces (routes, all under `/health`):**
- `GET /topics` → page listing topics as a tree (indented by depth), each with status (proposed/approved), "looks like <name> — Merge?" when `similar_to_id` is set and not merged, Approve / Rename / Merge-into controls (same markup pattern as `areas.html`), and a link to its thread page. Add `NavLink("Topics", "/health/topics")` to the Health spec's `nav_links`.
- `POST /topics/{id}/approve`, `/topics/{id}/rename` (form `name`), `/topics/{id}/merge` (form `into`) → re-render the page (200) or the page with a three-part `Problem` (400/404), exactly like the areas routes.
- `GET /topics/{id}?person=<id>` → the thread page: heading = topic path; a person picker (links for each person who has rows under the topic; default the one with most); "Conclusions over time" (newest first: date, provider, paper link, the prose under the topic and its sub-topics with their sub-headings); "Metrics" (one table per metric: label, unit, values by date). 404 with the three-part detail for an unknown topic.
- `POST /documents/{id}/outline/ok` → `outlines.confirm_outline(session, entry, by=None)`; 303 back to the paper; a `Problem` renders as 409 detail dict.
- Paper page, `entry.layout == "report"`: a line "Written report"; the results section is replaced by (a) the **outline**: headings by depth (use the existing `_measurement_view` headings), each heading linked to `/health/topics/<topic_id>?person=<entry.person_id>` when the row has a `topic_id`, prose rows (`value is none and value_text`) rendered as paragraphs under their heading, and (b) a **Metrics** table (numeric rows only: heading path, metric, value unit, reference, earlier) shown only when there are numeric rows, with the sentence "This report prints no numbers." otherwise; plus the form button "The outline looks right" (hidden once `entry.outline_confirmed_at`, replaced by "Outline confirmed"). Sheets render exactly as today.

- [ ] **Step 1: Tests** (`tests/test_health_topic_pages.py`, using the `client`/`session` fixtures as `tests/test_health_router.py` does; file reports through the production path):
  1. `GET /health/topics` lists proposed topics as a tree with Approve buttons; after `POST /health/topics/{id}/approve` it says approved.
  2. A topic with `similar_to_id` shows "looks like <name>" and the merge control; `POST …/merge` with `into` empty gives the three-part message; a valid merge removes the source from the list and the target's thread page contains the source's rows.
  3. `GET /health/topics/{id}?person=…` shows the conclusions of two reports from different dates under one topic, newest first; another person's rows never appear; metrics table lists a numeric row with both dates; unknown id → 404 with `what/why/next_step`.
  4. The paper page of a report: outline headings appear in order with the prose under the right heading, the heading links to its topic page, "This report prints no numbers." appears when none, the Metrics table appears when a numeric row exists, and `POST /documents/{id}/outline/ok` flips the button to "Outline confirmed". A sheet paper's page still has the results table and no "Written report" line.
  5. Text from the paper is HTML-escaped on both new pages (use a conclusion containing `<b>` and assert it is escaped).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement** (reuse `_areas_page`-style helpers; keep the rendering logic in the router/template like the existing pages).
- [ ] **Step 4:** `python3 -m pytest tests/test_health_topic_pages.py tests/test_health_router.py tests/test_health_why_box.py tests/test_health_browse.py tests/test_health_domain.py -q` → PASS.
- [ ] **Step 5:** checkpoint. If a dev server is available (`run` skill), look at both new pages once; otherwise the tests are the evidence.

---

### Task 6: The benchmark understands reports

**Files:** Modify `app/domains/health/benchmark.py`, `scripts/health_benchmark.py` docstring only if needed; Test `tests/test_health_benchmark.py`.

**Interfaces**
- `promote` writes `"layout"` (from the entry) next to `"reviewed"` in `expected.json`; `expected_from_reading` does the same.
- `compare`: if `expected.get("layout")` and it differs from `actual.layout` → problem `"layout: expected <x>, read <y>"` (only when expected has one). Text results (`value_text`) longer than 80 characters (both sides, squeezed) are equal when `difflib.SequenceMatcher(...).ratio() >= 0.85` of their `match_key`s; shorter texts keep the exact-after-squeezing rule. The `_earlier` rule is unchanged.
- The whitespace-squeezing helper `_squeezed` and its test already exist in the working tree (uncommitted, from the earlier benchmark fix); keep them.

- [ ] **Step 1: Tests:** (a) a report case round-trips: `expected_from_reading(reading)` contains `layout == "report"`; (b) a conclusion reworded slightly (ratio ≥ 0.85) passes, a conclusion that says something different fails with `wrong value:`; (c) short texts still need an exact match after squeezing; (d) layout mismatch is reported.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** `python3 -m pytest tests/test_health_benchmark.py -q` → PASS. **Step 5:** checkpoint.

---

### Task 7: Notes and whole-suite

**Files:** Modify `docs/SYSADMIN.md` (one paragraph under the Health reading section: topics, outline confirmation, second reading, and that a topic merge cannot be undone except from a database backup), `docs/privacy/DECISIONS.md` (backlog: sheet headings into the topic baseline; "read twice" for sheets with a text layer; Ask tools for topics → Phase 5).

- [ ] **Step 1:** write both notes (plain words). **Step 2:** the whole suite once (`python3 -m pytest tests -q`, about 15–70 minutes; run in the background and wait). Expected: all PASS apart from anything the other session left broken in files this plan did not touch (report those by name).
- [ ] **Step 3: Pedro orders commit and deploy** (stop until he does). Then, on the VPS: back up the database; run the benchmark; read paper 200 again with `scripts/reread_health.py 200`; Pedro opens `/health/documents/200` (outline, no numbers, "This report prints no numbers", topics proposed) and `/health/topics`; he approves/merges; then `scripts/health_benchmark.py promote 200 hsm-doppler-2026-03` on his word.

---

## Self-review

- **Spec coverage:** Q11 — metrics for reports and written-report extraction around the report's hierarchy (Tasks 3, 5), baseline that adjusts to commonalities (Tasks 2, 3, 5), topic as the main thread with metrics threads under it (Task 2 `thread`, Task 5). Suggestions 1–5 — (1) read the paper itself: Task 3 prompt/layout; (2) text check: existing + Task 3; (3) second reading: Task 4; (4) confirm the first paper of a kind: Task 4 + 5; (5) grow the benchmark: Task 6 + Task 7 step 3.
- **Placeholder scan:** the signatures, algorithms, thresholds and test lists are given; the only things left to the implementer are plumbing that the existing code defines (fixtures, template markup).
- **Types:** `HealthTopic`, `HealthOutlineProfile`, `layout`, `topic_id`, `attach_topics`, `topic_path`, `guidance`, `confirm_outline`, `compare_readings`, `second_read` are used with the same names in every task.
