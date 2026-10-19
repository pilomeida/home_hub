# Conditions Phase 1: Reader Outline, Numbers and "Why this was done"

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the paper reader return a paper's context (why it was made, clinical notes, who asked, who did it), keep its printed outline (main sections with chapters inside) and capture every conclusion and number per section; show the context in a "Why this was done" box with a free-text field on every health paper's page; then re-read papers 199 and 200.

**Architecture:** The data model already stores a nested outline (`HealthMeasurement.group_path`, headings joined with `PATH_SEP` " › ") and the paper page already renders it with indentation, so Phase 1 is mostly the *reader prompt* plus four new document fields. The context lives in `documents.fields_json` like every other field (`FieldSpec(private=True)`), the single source a person can edit; the entry is rebuilt from it by `store.sync_entry_from_fields`. No migration.

**Tech Stack:** Python/FastAPI/SQLModel, Jinja2, pytest + pytest-asyncio, `tests/fakes/fake_gateway.py`.

**Spec:** `/home/pedro/Desktop/Claude_Corner/brainstorms/2026-10-19-health-conditions-episodes-and-section-hierarchy.md` (Q4, Q5, Q6) and the master plan `docs/superpowers/plans/2026-10-19-conditions-master-plan.md` (its Global Constraints apply to every task here).

## Global Constraints

- No commit, no push, no deploy without Pedro's explicit word; stage by file name only.
- Reader calls stay `sensitivity=Tier.HIGH.value` (already enforced inside `extract_health_document`).
- Test inputs come from the production path: run the reply through `extract_health_document` with `FakeGateway`, then `apply_extraction`; do not hand-build stored values.
- Messages rule for anything a person can read.
- Run from `Home & Family/`: `python3 -m pytest <file> -q`.

## File Structure

| File | Change |
|---|---|
| `app/domains/health/spec.py` | 4 new `FieldSpec`s: `indication`, `clinical_notes`, `requested_by`, `performed_by` |
| `app/domains/health/extraction.py` | prompt, schema, `ExtractedHealth` fields, parsing |
| `app/domains/health/checks.py` | the 4 new strings count as "accounted for" printed text |
| `app/domains/health/store.py` | `apply_extraction` fills the 4 fields |
| `app/routers/health.py` | `why` context, `POST /documents/{id}/why`, `prepare_reread()` extracted from `reread` |
| `app/templates/health/detail.html` | "Why this was done" box |
| `scripts/reread_health.py` | new: read filed papers again with the current reader (same steps as the button) |
| `tests/test_health_context.py`, `tests/test_health_outline.py`, `tests/test_health_why_box.py` | new |
| `docs/SYSADMIN.md`, `docs/privacy/DECISIONS.md` | notes |

---

### Task 1: The paper's context becomes four fields

**Files:**
- Modify: `app/domains/health/spec.py` (fields tuple, before `summary`)
- Modify: `app/domains/health/extraction.py` (`ExtractedHealth`, `_SYSTEM`, `_SCHEMA`, `extract_health_document`)
- Modify: `app/domains/health/checks.py:55-59` (`_strings_of`)
- Modify: `app/domains/health/store.py:105-123` (`apply_extraction`)
- Test: `tests/test_health_context.py` (create)

**Interfaces:**
- Produces: `ExtractedHealth.indication|clinical_notes|requested_by|performed_by: str` (empty string when absent); document fields with the same four keys in `fields_json`.

- [ ] **Step 1: Write the failing tests**

```python
"""A health paper's context (why it was made, clinical notes, who asked, who did it) is read, kept as fields,
counted by the check against the paper, and a person's own words are never overwritten by a reading."""

import json

import pytest

from app.domains.fields import dump_fields, load_fields
from app.domains.health import extraction as ex
from app.domains.health import store
from app.domains.health.checks import _strings_of
from app.models.domain import Domain
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.knowledge_factories import make_document
from tests.test_health_extraction import _png

CONTEXT_REPLY = {
    "category": "imaging_report", "person_name": "Pedro Nuno dos Santos Almeida", "document_date": "2026-03-26",
    "provider": "Hospital de Santa Maria", "record_kind": "test", "summary": "Doppler transcraniano sem alterações.",
    "indication": "Avaliação da Circulação Cervicoencefálica",
    "clinical_notes": "AVC isquémico aos 40 anos. Provável AVC cardioembólico. Pesquisa de MES.",
    "requested_by": "C.E Neurologia", "performed_by": "Cpl. Dra. Mª Fátima Soares",
    "details": "Exame Nº: 12935", "sections": [],
}


async def _read(tmp_path, reply=CONTEXT_REPLY):
    return await ex.extract_health_document(_png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(reply))]))


@pytest.mark.asyncio
async def test_the_four_context_items_are_read(tmp_path):
    got = await _read(tmp_path)
    assert got.indication == "Avaliação da Circulação Cervicoencefálica"
    assert got.clinical_notes.startswith("AVC isquémico aos 40 anos")
    assert (got.requested_by, got.performed_by) == ("C.E Neurologia", "Cpl. Dra. Mª Fátima Soares")


@pytest.mark.asyncio
async def test_a_paper_without_context_gives_empty_strings(tmp_path):
    reply = {k: v for k, v in CONTEXT_REPLY.items() if k not in ("indication", "clinical_notes", "requested_by", "performed_by")}
    got = await _read(tmp_path, reply)
    assert (got.indication, got.clinical_notes, got.requested_by, got.performed_by) == ("", "", "", "")


@pytest.mark.asyncio
async def test_the_context_is_stored_as_fields_and_a_persons_own_words_win(session, tmp_path):
    document = make_document(session, domain=Domain.HEALTH, category="imaging_report")
    document.fields_json = dump_fields({"indication": "Controlo anual pedido pela neurologia"})   # typed by a person
    session.add(document)
    session.commit()
    store.apply_extraction(session, document, await _read(tmp_path))
    fields = load_fields(document)
    assert fields["indication"] == "Controlo anual pedido pela neurologia"          # the reading only fills blanks
    assert fields["clinical_notes"].startswith("AVC isquémico") and fields["requested_by"] == "C.E Neurologia"
    assert fields["performed_by"] == "Cpl. Dra. Mª Fátima Soares"


@pytest.mark.asyncio
async def test_the_check_against_the_paper_counts_the_context_as_read_text(tmp_path):
    got = await _read(tmp_path)
    strings = [s for s in _strings_of(got) if s]
    assert got.clinical_notes in strings and got.indication in strings        # their numbers ("40 anos") are not "missing"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_health_context.py -q`
Expected: FAIL (`AttributeError: ... 'indication'`).

- [ ] **Step 3: Implement**

In `app/domains/health/spec.py`, insert before the `FieldSpec("summary", ...)` line:

```python
        FieldSpec("indication", "Why this was done", FieldKind.LONGTEXT, private=True,
                  help="The reason the paper was requested, as printed (Indicação, Motivo). If the paper gives none, write it here."),
        FieldSpec("clinical_notes", "Clinical notes on the request", FieldKind.LONGTEXT, private=True,
                  help="What the requester wrote about the patient (Observações, Informação clínica)."),
        FieldSpec("requested_by", "Requested by / referred from", FieldKind.TEXT, private=True),
        FieldSpec("performed_by", "Done by", FieldKind.TEXT, private=True),
```

In `app/domains/health/extraction.py`:

1. In `ExtractedHealth`, after `details: str = ""` add:

```python
    indication: str = ""            # why the paper was made, as printed ("Indicação")
    clinical_notes: str = ""        # what the requester wrote about the patient ("Observações")
    requested_by: str = ""          # the service or doctor who asked for it / sent the patient
    performed_by: str = ""          # who did the exam or signed the report
```

2. In `_SYSTEM`, replace the `- details:` bullet (the one starting `- details: ONE string with EVERY other item`) with these five bullets (keep the rest of that bullet's wording, adding the sentence "Do not repeat the four items above here."):

```
- indication: why the paper was requested or made, in the paper's own words (e.g. the line labelled "Indicação" or "Motivo");
  leave the key out if the paper gives none.
- clinical_notes: what the requester wrote about the patient (e.g. "Observações", "Informação clínica": earlier diagnoses,
  the clinical question), word for word; leave it out if none.
- requested_by: the service or doctor who asked for it or sent the patient (e.g. "Proveniência", "Médico requisitante"), as printed.
- performed_by: who did the exam or signed the report (technician, specialist), as printed.
- details: ONE string with EVERY other item printed on the paper that has no field here, one per line (separated by \\n) as "Label: value", using the
  paper's own labels and language: the patient's identification, tax, health-service and beneficiary numbers, age,
  the dates of collection, entry and release, requisition and internal numbers, the laboratory's director, specialists and
  technicians, accreditations, methods, and any printed note or comment. Do not repeat the four items above here.
```

3. In `_SCHEMA["properties"]`, add `"indication": _STR, "clinical_notes": _STR, "requested_by": _STR, "performed_by": _STR,` next to `"provider_address": _STR`.

4. In the `ExtractedHealth(...)` call at the end of `extract_health_document`, after `summary=..., details=...` add:

```python
            indication=_text(data.get("indication")) or "", clinical_notes=_text(data.get("clinical_notes")) or "",
            requested_by=_text(data.get("requested_by")) or "", performed_by=_text(data.get("performed_by")) or "",
```

In `app/domains/health/checks.py` `_strings_of`, extend the first `yield from`:

```python
    yield from (x.person_name, x.provider, x.provider_phone, x.provider_address, x.summary, x.details,
                x.indication, x.clinical_notes, x.requested_by, x.performed_by,
                x.appointment_time, *x.areas, *x.conditions)
```

In `app/domains/health/store.py` `apply_extraction`, after `_fill(fields, "details", extracted.details)` add:

```python
    for key in ("indication", "clinical_notes", "requested_by", "performed_by"):
        _fill(fields, key, getattr(extracted, key))
```

- [ ] **Step 4: Run to verify it passes, then the neighbours**

Run: `python3 -m pytest tests/test_health_context.py tests/test_health_extraction.py tests/test_health_sections.py tests/test_health_checks.py tests/test_health_handler.py tests/test_health_domain.py -q`
Expected: all PASS. If a test enumerates every health field (privacy/channel allowlist), it must still pass because all four are `private=True`; if one fails, read it and extend its expected list, nothing else.

- [ ] **Step 5: Checkpoint**

`git status` shows only the files above plus the new test (and the other session's files, untouched). No commit.

---

### Task 2: The reader keeps the outline, the conclusions and the numbers

**Files:**
- Modify: `app/domains/health/extraction.py` (`_SYSTEM`, the `sections:` block, after the line `Results continue under the same heading until the paper shows another heading.`)
- Test: `tests/test_health_outline.py` (create)

**Interfaces:**
- Consumes: `ex.extract_health_document`, `ex._measurements` (unchanged).
- Produces: a prompt that makes paper 200 come back as nested sections with `test="Conclusão"` rows carrying prose in `value_text` and numbers as ordinary results. (`_RESULTS_RULES` in `reading.py` slices this block, so the second-look prompt inherits the rules.)

The storage and the page already handle depth and prose (`value_text`); this task adds the instruction and pins the behaviour with a test on the shape the model must return.

- [ ] **Step 1: Write the failing test**

```python
"""A paper printed as an outline (main sections with chapters inside) is stored as that outline: conclusions are
results under the section or chapter they describe, and every number is a result in its own section."""

import json

import pytest

from app.domains.health import extraction as ex
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.test_health_extraction import _png

OUTLINE_REPLY = {
    "category": "imaging_report", "person_name": "Pedro Nuno dos Santos Almeida", "document_date": "2026-03-26",
    "provider": "Hospital de Santa Maria", "record_kind": "test", "summary": "Doppler transcraniano.",
    "sections": [
        {"path": ["Triplex carotídeo", "Eixo Carotídeo direito"], "results": [
            {"test": "Velocidade sistólica máxima", "value": 85, "unit": "cm/s"},
            {"test": "Conclusão", "value_text": "Permeabilidade do eixo carotídeo, com pequena placa ateromatosa sem repercussão hemodinâmica."}]},
        {"path": ["Triplex carotídeo", "Eixo Carotídeo esquerdo"], "results": [
            {"test": "Conclusão", "value_text": "Permeabilidade do eixo carotídeo com velocidades normais."}]},
        {"path": ["Triplex carotídeo", "Artérias Vertebrais"], "results": [
            {"test": "Conclusão", "value_text": "Permeáveis; dominância da artéria vertebral direita."}]},
        {"path": ["ECO/DTC"], "results": [
            {"test": "Índice de pulsatilidade", "value": 0.9},
            {"test": "Conclusão", "value_text": "Permeabilidade de todos os segmentos estudados."}]},
        {"path": ["DTC-c"], "results": [
            {"test": "Conclusão", "value_text": "Sem sinais microembólicos nem shunt direito-esquerdo."}]},
    ],
}


@pytest.mark.asyncio
async def test_chapters_sit_under_their_main_section_and_each_conclusion_under_what_it_describes(tmp_path):
    got = await ex.extract_health_document(_png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(OUTLINE_REPLY))]))
    rows = [(m.group_path, m.test) for m in got.measurements]
    assert rows == [
        ("Triplex carotídeo › Eixo Carotídeo direito", "Velocidade sistólica máxima"),
        ("Triplex carotídeo › Eixo Carotídeo direito", "Conclusão"),
        ("Triplex carotídeo › Eixo Carotídeo esquerdo", "Conclusão"),
        ("Triplex carotídeo › Artérias Vertebrais", "Conclusão"),
        ("ECO/DTC", "Índice de pulsatilidade"),
        ("ECO/DTC", "Conclusão"),
        ("DTC-c", "Conclusão"),
    ]
    first = got.measurements[0]
    assert (first.value, first.unit) == (85.0, "cm/s")                              # numbers are tracked like lab values
    conclusion = got.measurements[1]
    assert conclusion.value is None and conclusion.value_text.startswith("Permeabilidade do eixo")


def test_the_prompt_teaches_the_outline_prose_conclusions_and_numbers():
    rules = ex._SYSTEM
    assert "main sections" in rules and "chapters" in rules
    assert 'test "Conclusão"' in rules and "value_text" in rules
    assert "Never put a heading's name inside a test's name" in rules
    assert "Every number printed in a section" in rules
```


- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_health_outline.py -q`
Expected: `test_chapters...` PASSES already (the parser handles depth); `test_the_prompt_teaches...` FAILS (`assert "main sections" in rules`).

- [ ] **Step 3: Implement**

In `_SYSTEM`, directly after the line `  Results continue under the same heading until the paper shows another heading.` add:

```
  The headings are an outline. A paper can have main sections (e.g. "Triplex carotídeo", "ECO/DTC", "DTC-c") and,
  inside a main section, chapters (e.g. "Eixo Carotídeo direito", "Eixo Carotídeo esquerdo" and "Artérias Vertebrais"
  inside "Triplex carotídeo"). Put the whole chain in path, outermost first: ["Triplex carotídeo", "Eixo Carotídeo direito"].
  Never put a heading's name inside a test's name, and never gather conclusions under one invented heading such as
  "Conclusões": each conclusion belongs under the section or chapter it describes.
  Printed prose is a result too: a conclusion, impression or comment under a section is a result with test "Conclusão"
  (or the paper's own word: "Impressão", "Comentário") and the whole text, word for word, in value_text.
  Every number printed in a section (velocities, indices, diameters, percentages) is a separate result in that same
  section, with its unit.
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_health_outline.py tests/test_health_sections.py tests/test_health_checks.py tests/test_health_benchmark.py -q`
Expected: PASS.

- [ ] **Step 5: Checkpoint**

The prompt change cannot be proven offline: the proof is the VPS run in Task 4 (paper 200's outline). No commit.

---

### Task 3: "Why this was done" on the paper's page

**Files:**
- Modify: `app/routers/health.py` (`_detail_context`; new route; import `dump_fields`)
- Modify: `app/templates/health/detail.html` (box after the status banners, before the `<div style="display:grid...">`)
- Test: `tests/test_health_why_box.py` (create)

**Interfaces:**
- Produces: `POST /health/documents/{document_id}/why` (form field `indication`) → 303 to the paper page; template variable `why` = `{"indication","clinical_notes","requested_by","performed_by","details"}` (strings).

- [ ] **Step 1: Write the failing tests**

```python
"""Every health paper opens with a "Why this was done" box: what the paper says, or the person's own words."""

import json
from datetime import date

import pytest

from app.domains.fields import load_fields
from app.domains.health import extraction as ex
from app.domains.health import store
from app.models.domain import Domain
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.knowledge_factories import make_document
from tests.test_health_context import CONTEXT_REPLY
from tests.test_health_extraction import _png


async def _filed(session, tmp_path, reply):
    document = make_document(session, domain=Domain.HEALTH, category="imaging_report", filename="doppler.pdf")
    got = await ex.extract_health_document(_png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(reply))]))
    store.apply_extraction(session, document, got)
    return document


@pytest.mark.asyncio
async def test_the_box_shows_the_papers_reason_notes_and_people(client, session, tmp_path):
    doc = await _filed(session, tmp_path, CONTEXT_REPLY)
    html = client.get(f"/health/documents/{doc.id}").text
    assert "Why this was done" in html
    assert "Avaliação da Circulação Cervicoencefálica" in html and "AVC isquémico aos 40 anos" in html
    assert "C.E Neurologia" in html and "Cpl. Dra. Mª Fátima Soares" in html
    assert "Exame Nº: 12935" in html                                   # administrative numbers sit in the collapsed Details


@pytest.mark.asyncio
async def test_a_paper_that_gives_no_reason_invites_the_person_to_write_one(client, session, tmp_path):
    reply = {k: v for k, v in CONTEXT_REPLY.items() if k != "indication"}
    doc = await _filed(session, tmp_path, reply)
    assert "The paper does not say why it was made. Write it here." in client.get(f"/health/documents/{doc.id}").text


@pytest.mark.asyncio
async def test_a_persons_own_reason_is_saved_and_shown(client, session, tmp_path):
    reply = {k: v for k, v in CONTEXT_REPLY.items() if k != "indication"}
    doc = await _filed(session, tmp_path, reply)
    r = client.post(f"/health/documents/{doc.id}/why", data={"indication": "Controlo anual do AVC de 2015"}, follow_redirects=False)
    assert r.status_code == 303
    session.refresh(doc)
    assert load_fields(doc)["indication"] == "Controlo anual do AVC de 2015"
    assert "Controlo anual do AVC de 2015" in client.get(f"/health/documents/{doc.id}").text


def test_writing_a_reason_for_a_non_health_paper_is_a_404(client, session):
    other = make_document(session, domain=Domain.HOUSE, category="house_appliance")
    assert client.post(f"/health/documents/{other.id}/why", data={"indication": "x"}).status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_health_why_box.py -q`
Expected: FAIL (box text missing; POST gives 404/405).

- [ ] **Step 3: Implement**

In `app/routers/health.py`:

- add `dump_fields` to the existing `from app.domains.fields import (...)` list;
- in `_detail_context`, add to the returned dict (the `fields = load_fields(document)` call goes at the top of the function):

```python
    fields = load_fields(document)
    ...
            "why": {k: fields.get(k, "") for k in ("indication", "clinical_notes", "requested_by", "performed_by", "details")},
```

- add the route after `update_fields`:

```python
@router.post("/documents/{document_id}/why")
async def update_why(document_id: int, indication: str = Form(""), session: Session = Depends(get_session)):
    """The person's own words for why this paper was made (for papers that do not say). Kept as the field `indication`."""
    document = _document_or_404(session, document_id)
    fields = load_fields(document)
    fields["indication"] = indication.strip()
    document.fields_json = dump_fields(fields)
    session.add(document)
    session.commit()
    if store.entry_for(session, document.id) is not None:
        store.sync_entry_from_fields(session, document)
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)
```

In `app/templates/health/detail.html`, insert immediately before `<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">`:

```jinja
<div class="why-box" style="border:1px solid #9bb8e0;background:#f3f8ff;padding:10px 14px;margin:12px 0;">
  <strong>Why this was done</strong>
  {% if why.indication %}<p style="margin:6px 0;">{{ why.indication }}</p>
  {% else %}<p style="margin:6px 0;"><em>The paper does not say why it was made. Write it here.</em></p>{% endif %}
  {% if why.clinical_notes %}<p style="margin:6px 0;"><small>Clinical notes</small><br>{{ why.clinical_notes }}</p>{% endif %}
  {% if why.requested_by or why.performed_by %}<p style="margin:6px 0;">
    {% if why.requested_by %}<small>Requested by</small> {{ why.requested_by }}{% endif %}
    {% if why.requested_by and why.performed_by %} · {% endif %}
    {% if why.performed_by %}<small>Done by</small> {{ why.performed_by }}{% endif %}</p>{% endif %}
  <form method="post" action="/health/documents/{{ document.id }}/why" style="margin:6px 0;">
    <textarea name="indication" rows="2" style="width:100%;" aria-label="Why this was done">{{ why.indication }}</textarea>
    <button type="submit">Save</button>
  </form>
  {% if why.details %}<details><summary>Details</summary><pre style="white-space:pre-wrap;">{{ why.details }}</pre></details>{% endif %}
</div>
```

- [ ] **Step 4: Run to verify it passes, then neighbours**

Run: `python3 -m pytest tests/test_health_why_box.py tests/test_health_router.py tests/test_health_delete.py -q`
Expected: PASS.

- [ ] **Step 5: Checkpoint**

Look at the page once in the browser against the dev data if a dev server is available (`run` skill); otherwise the tests are the evidence and the VPS check in Task 4 is the real look. No commit.

---

### Task 4: Read papers 199 and 200 again (VPS)

**Files:**
- Modify: `app/routers/health.py` (extract `prepare_reread` from `reread`)
- Create: `scripts/reread_health.py`
- Test: `tests/test_health_router.py` (add one test)

**Interfaces:**
- Produces: `async def prepare_reread(session, document) -> None` in `app/routers/health.py` (the withdraw + reset steps the button runs for a processed paper; sets status PENDING); `scripts/reread_health.py DOCUMENT_ID [...]`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_health_router.py`)

```python
@pytest.mark.asyncio
async def test_preparing_a_reread_takes_back_what_was_read_and_leaves_the_paper_pending(session):
    from app.domains.health import store
    from app.models.document import DocumentStatus
    from app.routers.health import prepare_reread
    doc = _paper(session, "Maria Santos Almeida", date(2026, 3, 12), "test", ["Eyes"], name="eye.pdf")
    doc.status = DocumentStatus.PROCESSED
    session.add(doc)
    session.commit()
    await prepare_reread(session, doc)
    session.refresh(doc)
    assert doc.status == DocumentStatus.PENDING and doc.fields_json == "{}"
    assert store.entry_for(session, doc.id) is None and not session.exec(select(HealthMeasurement)).all()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_health_router.py -q -k preparing_a_reread`
Expected: FAIL (`ImportError: cannot import name 'prepare_reread'`).

- [ ] **Step 3: Implement**

In `app/routers/health.py`, add above `reread`:

```python
async def prepare_reread(session: Session, document: Document) -> None:
    """The steps behind "Read again": take back everything read from a filed paper (calendar entry, wiki claims,
    results, tags), clear its fields so they start again from the new reading, and mark it pending."""
    if document.status == DocumentStatus.PROCESSED:
        await spec_for_health().handler.withdraw(session, document)
        await withdraw_from_wiki(session, document, description=f"{document.filename} read again")
        document.fields_json = "{}"
    elif document.status not in (DocumentStatus.PENDING, DocumentStatus.NEEDS_ATTENTION):
        raise HTTPException(status_code=409, detail={
            "what": "Not read again.", "why": "This paper was deleted, so there is nothing to read.",
            "next_step": "Upload it again if you need it."})
    document.status = DocumentStatus.PENDING
    document.failure_reason = None
    session.add(document)
    session.commit()
```

and reduce the body of `reread` to:

```python
    document = _document_or_404(session, document_id)
    await prepare_reread(session, document)
    background_tasks.add_task(_read_in_background, session_factory, document.id)
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)
```

Create `scripts/reread_health.py`:

```python
"""Read filed health papers again with the current reader: the same steps as the "Read again" button, run from the server.
Run on the VPS, from the app folder, as the app's user:
    python scripts/reread_health.py DOCUMENT_ID [DOCUMENT_ID ...]
What was read from each paper is taken back and redone; fields a person typed are lost (as with the button).
The reading goes out at house_high, like every health read."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.routers import health as health_router  # noqa: E402


async def main(ids: list[int]) -> int:
    failed = 0
    for document_id in ids:
        with Session(engine) as session:
            document = health_router._document_or_404(session, document_id)
            await health_router.prepare_reread(session, document)
        await health_router._read_in_background(lambda: Session(engine), document_id)
        with Session(engine) as session:
            document = session.get(Document, document_id)
            print(f"{document_id}: {document.status.value} {document.failure_reason or ''}".rstrip())
            failed += document.status.value != "processed"
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main([int(a) for a in sys.argv[1:]])))
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_health_router.py tests/test_health_why_box.py tests/test_health_context.py tests/test_health_outline.py -q`
Expected: PASS.

- [ ] **Step 5: Whole-suite run before anything is deployed**

Run (about 15 min, background): `python3 -m pytest tests -q`
Expected: PASS, apart from anything the other session has left broken (report such failures by name; do not fix the other session's files).

- [ ] **Step 6: Pedro orders commit and deploy** (stop here until he does)

Stage by name: `app/domains/health/{spec,extraction,checks,store}.py`, `app/routers/health.py`, `app/templates/health/detail.html`, `scripts/reread_health.py`, the three new tests, `tests/test_health_router.py`. Message: "Health: papers say why they were made; the reader keeps sections, chapters, conclusions and numbers". Then `git subtree push --prefix="Home & Family" home_hub main`; wait for the Deploy run; check the VPS head and `alembic_version` (no new migration, so unchanged).

- [ ] **Step 7: Benchmark before touching papers**

Run: `ssh root@167.233.51.113 'cd /srv/home-hub/app && sudo -u home-hub /srv/home-hub/venv/bin/python scripts/health_benchmark.py run'`
Expected: `PASS cintramedica-lab-2024-11` (55/55). If it fails on rows that a lab paper has nothing to do with outlines or conclusions, the new prompt text is the cause: revert the Task 2 paragraph, re-run, and report to Pedro before going on.

- [ ] **Step 8: Back up, then read 200 first, then 199**

```bash
ssh root@167.233.51.113 'cp /srv/home-hub/app/data/home_family.db /root/home_family_pre_reread_200_199_$(date +%Y%m%d).db && cd /srv/home-hub/app && sudo -u home-hub /srv/home-hub/venv/bin/python scripts/reread_health.py 200 199'
```
Expected: `200: processed` and `199: processed`, about 3 minutes in all.

- [ ] **Step 9: Check 199 still equals the reviewed reading**

199's reviewed content (Pedro's adjustments, earlier results with their own dates) was wiped by the re-read; the benchmark case is a frozen copy of it. Compare:

```bash
ssh root@167.233.51.113 'cd /srv/home-hub/app && mkdir -p /tmp/bench_check && chown home-hub /tmp/bench_check && sudo -u home-hub /srv/home-hub/venv/bin/python - <<EOF
import json
from pathlib import Path
from sqlmodel import Session
from app.db import engine
from app.domains.health import benchmark
with Session(engine) as s:
    folder = benchmark.promote(s, 199, "check199", directory=Path("/tmp/bench_check"))
new = json.loads((folder / "expected.json").read_text())["results"]
old = json.loads(Path("data/health_benchmark/cintramedica-lab-2024-11/expected.json").read_text())["results"]
key = lambda r: (r["path"], r["test"], r["unit"])
nd, od = {key(r): r for r in new}, {key(r): r for r in old}
print("results new/old:", len(new), len(od))
print("only in the new read:", [k for k in nd if k not in od][:10])
print("only in the reviewed case:", [k for k in od if k not in nd][:10])
print("differing:", [(k, [(f, od[k][f], nd[k][f]) for f in nd[k] if nd[k][f] != od[k][f]]) for k in nd if k in od and nd[k] != od[k]][:10])
EOF
rm -rf /tmp/bench_check'
```
Expected: the same 55 results with, at most, spacing differences in references (the benchmark ignores those). Any real difference is shown to Pedro as a list ("these rows changed after the re-read") with his reviewed value beside the new one; he decides, and the backup above is the way back.

- [ ] **Step 10: Pedro looks at paper 200**

Pedro opens `https://hub.cdafamily.casa/health/documents/200`. He should see: the "Why this was done" box (Avaliação da Circulação Cervicoencefálica; the stroke note; Neurologia; the operator), the outline Triplex carotídeo › Eixo Carotídeo direito/esquerdo/Artérias Vertebrais, ECO/DTC, DTC-c, each with its conclusion and its numbers. What is wrong goes back as a list; fixes go into the prompt (Task 2) and, once he calls the page right, `scripts/health_benchmark.py promote 200 hsm-doppler-2026-03` makes it the second benchmark case (his word, as with 199).

---

### Task 5: Notes

**Files:**
- Modify: `docs/SYSADMIN.md` (section "Health reading: checks and the benchmark"): one paragraph: the four context fields, the `scripts/reread_health.py` command and its warning that typed fields are lost.
- Modify: `docs/privacy/DECISIONS.md` ("Backlog (open, per Pedro)"): tick off the outline and context items, add: "Paper 200 has no text layer: its numbers are not cross-checked against the paper".

- [ ] **Step 1: Write both notes** (plain words; no new sections)
- [ ] **Step 2: Checkpoint** (`git status`; stage by name only when Pedro orders the commit)

---

## Self-review

- **Spec coverage:** Q4 (box, free text, Details collapsed, admin numbers out): Task 1 + 3. Q5 (outline, conclusions under their section, all numbers tracked): Task 2, proven on the VPS in Task 4. Q6 (re-read 199 and 200; protect 199's reviewed content): Task 4 steps 7–9. Q1–Q3, Q7–Q10 belong to Phases 2–5 (master plan). The condition link on the box ("Done for: …") is Phase 2.
- **Placeholder scan:** none.
- **Types:** `ExtractedHealth.indication|clinical_notes|requested_by|performed_by` (Task 1) are the names used in `store.apply_extraction`, `checks._strings_of`, the router's `why` dict and the template; `prepare_reread(session, document)` (Task 4) is what the script and the route both call.
