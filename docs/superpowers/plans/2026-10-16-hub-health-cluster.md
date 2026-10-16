# Hub Health Cluster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Health domain to the Home & Family Hub whose papers get the same pipeline as every other document (extract, structure, wiki, Ask) but always run at the `house_high` level, with a one-way privacy ratchet so any workflow that touches a Health record turns High and stays High to the end.

**Architecture:** (1) A `Ratchet` context (new `app/privacy/ratchet.py`) that the gateway client consults on every call, so once a run has touched High content nothing it sends afterwards can go out lower. (2) `Reach` stops *excluding* High types and instead reports each item's tier; Ask's first call is built from a "private-hidden" view, and every tool that returns private content reports `tier=HIGH`, which raises the ratchet before the next model call. (3) A new `health` domain (always High, not switchable) with its own tables (people, areas/conditions, entries, measurements), a vision extraction step, Ask tools, a database-style tab, a channel guard (administrative facts may leave the Hub, health-record facts never) and a Google Calendar sync for appointments.

**Tech Stack:** Python 3.10+/FastAPI/SQLModel/Alembic (SQLite), Jinja2 + HTMX templates, pytest + pytest-asyncio, httpx (Google Calendar REST, no new dependency), the llmsel gateway (`app/llm_gateway.py`) with Privatemode serving `house_high`.

**Spec:** `/home/pedro/Desktop/Claude_Corner/brainstorms/2026-10-15-hub-health-cluster.md` (Pedro's 15 answers; the "Summary / key decisions (final)" section is the contract). Existing design context: `docs/privacy/DECISIONS.md` (D1–D17), `docs/privacy/03-phase-d-plan.md`.


> **Execution notes (2026-10-16, applied after the plan was written):** the Health tab landing is `/health/records` (`/health` stays the unauthenticated liveness endpoint). `RatchetLog.from_json` fails closed. Task 9 added a Health paper to the shared privacy test world. People merges keep an alias (`health_people.merged_into_id`, migration `c3f6a0d94e15`). `channel_fields` is an allowlist. Past appointments are not put on the calendar. Where a task below says `/health` for the list page, read `/health/records`. Nothing was committed.

## Global Constraints

- **Never `git commit` / `git push` without Pedro's explicit order** (project CLAUDE.md). Every task therefore ends with a *Checkpoint* (tests green, `git status` reviewed), not a commit. Pedro will say when to commit; when he does, commit per task with the message given in each task's last step. Deploy is `git subtree push --prefix="Home & Family" home_hub main` — only on his explicit word.
- **Every LLM call goes through the llmsel gateway** (`app/llm_gateway.py`). No provider SDK, model name or API key in this app.
- **Test data comes from the production path.** Build inputs with the real objects (`save_type`, `make_document`, the real `GatewayClient` over `httpx.MockTransport`, `tests/fakes/fake_gateway.py`), never a hand-typed stand-in for a stored value. Assert on what the wire actually carried.
- **Messages rule (D14):** every warning or error a person can see says *what* didn't happen, *why*, and *what they can do*, in that order, built with `app/problems.py` (`Problem`, `explain`, `record_problem`). Anything that can fail where nobody is looking (calendar sync, background extraction) also calls `record_problem`.
- **Privacy rule (D16):** nothing that shows the AI documents, records, wiki claims, transactions or to-dos may read those tables straight into a prompt. It goes through `app/privacy/reach.py` and runs inside `reachable_scope(session)`.
- **Health is always `house_high`**, whether or not "Levels are binding" is on. It is not configurable.
- **Administrative facts may leave the Hub; health-record facts never do.** Administrative = person, provider name/phone/address, dates, amounts, insurer/policy facts, appointments (when/where/who/contact). Health-record = record kind, summary, areas, conditions, measurements, anything clinical. Only `FieldSpec.private == False` fields may go to Telegram, email or the calendar.
- **Money is stored as given** (floats as elsewhere in this app); user-facing money wording in EUR.
- **Additive-only migrations.** New tables and nullable columns only. Head revision today: `e5b9d7f3a826`.
- Tests are run from `Home & Family/` with `python3 -m pytest <path> -q`. The whole suite takes ~15 minutes; run targeted files per task and the whole suite once at the end (Task 14).

## File Structure

New files:

| File | Responsibility |
|---|---|
| `app/privacy/ratchet.py` | `Ratchet`, `Escalation`, `RatchetLog`, `ratchet_scope`, `current_ratchet`, `apply_ratchet`, `rank`, `highest` |
| `app/privacy/channels.py` | `channel_label`, `channel_fields` — what may leave the Hub |
| `app/services/ask/private.py` | Ask's private-run bookkeeping: `chat_is_private`, `turn_is_private`, `private_notices` |
| `app/domains/health/__init__.py` | package |
| `app/domains/health/categories.py` | `HealthCategory`, `RECORD_KINDS`, `PERSON_PAGE_TYPE`, `ALL_CATEGORIES` |
| `app/domains/health/spec.py` | `SPEC` (DomainSpec for Health) |
| `app/domains/health/text.py` | `fold`, `match_key` |
| `app/domains/health/people.py` | person discovery/matching/merge |
| `app/domains/health/areas.py` | area/condition resolve/approve/rename/merge/tags |
| `app/domains/health/extraction.py` | `extract_health_document` (vision, High) |
| `app/domains/health/store.py` | `apply_extraction`, `purge_derived`, `entry_for` |
| `app/domains/health/handler.py` | `HealthHandler` |
| `app/domains/health/browse.py` | filters and facets for the tab |
| `app/domains/health/ask.py` | `health_*` Ask tools |
| `app/domains/health/overview.py` | Overview card |
| `app/domains/health/appointments.py` | `appointment_event`, `sync_appointment`, `remove_appointment` |
| `app/integrations/__init__.py`, `app/integrations/google_calendar.py` | Google Calendar REST client |
| `app/models/health.py` | `HealthPerson`, `HealthArea`, `HealthEntry`, `HealthEntryTag`, `HealthMeasurement` |
| `app/routers/health.py` | `/health/*` |
| `app/templates/health/*.html` | landing, document, areas, people, upload |
| `alembic/versions/a1d4e8b72c93_ask_turn_escalation.py` (Task 4), `alembic/versions/b2e5f9c83d04_health_tables.py` (Task 7) | `ask_turns.escalation_json`; the five Health tables |
| `scripts/google_calendar_auth.py` | one-time OAuth helper (Pedro runs it) |
| `scripts/backfill_health.py` | bulk-ingest Pedro's existing reports |
| `tests/test_privacy_ratchet.py`, `tests/test_health_*.py`, `tests/test_ask_private.py` | tests |

Modified files: `app/llm_gateway.py`, `app/models/domain.py`, `app/models/ask.py`, `app/models/__init__.py`, `app/domains/base.py` (`FieldSpec.private`), `app/domains/registry.py`, `app/domains/insurance/spec.py` (descriptions), `app/privacy/settings.py`, `app/privacy/scope.py` (none needed, verified in Task 2), `app/privacy/reach.py`, `app/services/ask/{contracts,engine,context,tools,save,messages}.py`, `app/services/wiki_lint/llm_checks.py`, `app/services/inbox_service.py`, `app/channels/telegram_bot.py`, `app/config.py`, `app/main.py`, `app/templates/{ask/_turn.html,privacy/settings.html}`, `app/routers/{ask.py,privacy.py}`, `docs/privacy/DECISIONS.md`, `docs/ARCHITECTURE.md`, `docs/SYSADMIN.md`, existing tests listed per task.

## Out of scope (backlog, per Pedro)

- Backups of health data (must follow the document/data tiers; separate discussion).
- Share a person's records with a doctor (export).
- Hard-deleting the stored file (Task 12 withdraws everything derived and hides the record; "raw sources are immutable" stays).

---

### Task 1: The ratchet (one-way, per run) and the gateway hook

**Files:**
- Create: `app/privacy/ratchet.py`
- Modify: `app/llm_gateway.py:71-96` (inside `GatewayClient.run`)
- Test: `tests/test_privacy_ratchet.py`

**Interfaces:**
- Consumes: `app.privacy.tiers.Tier`, `parse_tier`; `app.privacy.outbound.Redaction`.
- Produces:
  - `rank(tier: Optional[Tier]) -> int`; `highest(tiers: Iterable[Optional[Tier]]) -> Optional[Tier]`
  - `Escalation(step: int, from_tier: Optional[str], to_tier: str, label: str)`
  - `Ratchet(tier: Optional[Tier], started_private: bool, escalations: list[Escalation])` with `raise_to(tier, *, step, label) -> bool`, `private -> bool`, `to_json() -> str`
  - `RatchetLog.from_json(raw) -> RatchetLog` with `.private`, `.started_private`, `.escalations`, `.first_private() -> Optional[Escalation]`
  - `ratchet_scope(start: Optional[Tier] = None)` context manager yielding the `Ratchet`; `current_ratchet() -> Optional[Ratchet]`
  - `apply_ratchet(sensitivity, redaction) -> tuple[sensitivity, redaction]` (used by the gateway)

- [ ] **Step 1: Write the failing test**

Create `tests/test_privacy_ratchet.py`:

```python
"""The ratchet (decision D18): once a run has reached a level, no call it makes goes out lower.
Drives the REAL GatewayClient over an httpx.MockTransport, so what is asserted is what would leave the VPS."""

import json

import httpx
import pytest

from app.llm_gateway import GatewayClient
from app.privacy.outbound import Redaction
from app.privacy.ratchet import (
    RatchetLog, current_ratchet, highest, rank, ratchet_scope,
)
from app.privacy.redactor import Redactor
from app.privacy.tiers import Tier
from app.privacy.vault import Vault


class Wire:
    def __init__(self):
        self.bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"text": "ok", "stop_reason": "end_turn", "usage": {}, "catalog_key": "k"})

    def client(self) -> GatewayClient:
        return GatewayClient("http://gw.test:8010", "tok", transport=httpx.MockTransport(self.handler))


def test_rank_orders_none_low_mid_high():
    assert rank(None) < rank(Tier.LOW) < rank(Tier.MID) < rank(Tier.HIGH)
    assert highest([None, Tier.LOW, Tier.HIGH, Tier.MID]) == Tier.HIGH
    assert highest([]) is None


def test_it_only_goes_up_and_records_each_escalation():
    with ratchet_scope(Tier.MID) as r:
        assert r.raise_to(Tier.HIGH, step=2, label="3 Health records") is True
        assert r.raise_to(Tier.MID, step=3, label="x") is False       # never down
        assert r.raise_to(Tier.HIGH, step=3, label="again") is False  # no change, no second record
        assert r.tier == Tier.HIGH and r.private
        assert [(e.step, e.from_tier, e.to_tier, e.label) for e in r.escalations] == \
               [(2, "house_mid", "house_high", "3 Health records")]


def test_a_run_that_starts_high_is_private_without_an_escalation():
    with ratchet_scope(Tier.HIGH) as r:
        assert r.started_private and r.private and r.escalations == []
    log = RatchetLog.from_json(r.to_json())
    assert log.private and log.started_private and log.first_private() is None


def test_log_round_trips_and_garbage_is_not_private():
    with ratchet_scope(Tier.LOW) as r:
        r.raise_to(Tier.HIGH, step=1, label="a lab report")
    log = RatchetLog.from_json(r.to_json())
    assert log.private and log.first_private().label == "a lab report"
    assert RatchetLog.from_json(None).private is False
    assert RatchetLog.from_json("not json").private is False


def test_the_scope_ends_with_the_block():
    assert current_ratchet() is None
    with ratchet_scope(Tier.MID):
        assert current_ratchet() is not None
    assert current_ratchet() is None


@pytest.mark.asyncio
async def test_without_a_ratchet_a_call_is_untouched():
    wire = Wire()
    await wire.client().run("classification", "sys", "hello")
    assert "sensitivity" not in wire.bodies[0]


@pytest.mark.asyncio
async def test_after_the_ratchet_rises_every_call_goes_out_high_even_when_it_asks_for_less():
    wire = Wire()
    gw = wire.client()
    with ratchet_scope(Tier.MID) as r:
        await gw.run("classification", "sys", "before")                          # no explicit level: the ratchet's Mid
        r.raise_to(Tier.HIGH, step=1, label="private")
        await gw.run("classification", "sys", "after")
        await gw.run("classification", "sys", "explicit low", sensitivity="house_low")
    assert [b.get("sensitivity") for b in wire.bodies] == ["house_mid", "house_high", "house_high"]


@pytest.mark.asyncio
async def test_a_run_that_started_mid_keeps_an_explicit_higher_level():
    wire = Wire()
    with ratchet_scope(Tier.LOW):
        await wire.client().run("classification", "sys", "x", sensitivity="house_mid")
    assert wire.bodies[0]["sensitivity"] == "house_mid"


@pytest.mark.asyncio
async def test_redaction_is_dropped_at_high_because_high_needs_none():
    wire = Wire()
    redaction = Redaction(Redactor({"NAME": ["Ana Exemplo"]}), Vault())
    with ratchet_scope(Tier.HIGH):
        await wire.client().run("classification", "sys", "Ana Exemplo", sensitivity="house_mid", redaction=redaction)
    body = wire.bodies[0]
    assert body["sensitivity"] == "house_high" and "Ana Exemplo" in body["user"]   # sent as is, to the private route only
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_privacy_ratchet.py -q`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'app.privacy.ratchet'`

- [ ] **Step 3: Write the implementation**

Create `app/privacy/ratchet.py`:

```python
"""The one-way privacy ratchet (decision D18, docs/privacy/DECISIONS.md).

A run (an Ask question, the weekly wiki check, an intake read) has a level. When it touches content
whose type is High it is raised to High, and from that moment every call it makes goes out at High
only, until the run ends. It never goes down, and nothing an explicit `sensitivity=` says can lower it.
Content sent at a lower level BEFORE the rise was allowed at that level; nothing after goes out below.

The gateway client calls `apply_ratchet` on every call (app/llm_gateway.py), so no call site has to
remember. Each run enters its own `ratchet_scope` (contextvars keep concurrent runs apart)."""

from __future__ import annotations

import contextvars
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.privacy.tiers import Tier, parse_tier

_RANK = {None: 0, Tier.LOW: 1, Tier.MID: 2, Tier.HIGH: 3}


def rank(tier: Optional[Tier]) -> int:
    return _RANK[tier]


def highest(tiers: Iterable[Optional[Tier]]) -> Optional[Tier]:
    best: Optional[Tier] = None
    for tier in tiers:
        if rank(tier) > rank(best):
            best = tier
    return best


@dataclass(frozen=True)
class Escalation:
    step: int                    # which model round-trip had just run (1 = the first)
    from_tier: Optional[str]
    to_tier: str
    label: str                   # plain words: what was opened ("3 Health records")


@dataclass
class Ratchet:
    tier: Optional[Tier] = None
    started_private: bool = False
    escalations: list[Escalation] = field(default_factory=list)

    def raise_to(self, tier: Optional[Tier], *, step: int, label: str) -> bool:
        if rank(tier) <= rank(self.tier):
            return False
        self.escalations.append(Escalation(step, self.tier.value if self.tier else None, tier.value, label))
        self.tier = tier
        return True

    @property
    def private(self) -> bool:
        return self.tier == Tier.HIGH

    def to_json(self) -> str:
        return json.dumps({"started_private": self.started_private,
                           "escalations": [vars(e) for e in self.escalations]})


@dataclass(frozen=True)
class RatchetLog:
    """A stored ratchet (AskTurn.escalation_json), read back."""
    started_private: bool = False
    escalations: tuple[Escalation, ...] = ()

    @property
    def private(self) -> bool:
        return self.started_private or any(e.to_tier == Tier.HIGH.value for e in self.escalations)

    def first_private(self) -> Optional[Escalation]:
        return next((e for e in self.escalations if e.to_tier == Tier.HIGH.value), None)

    @staticmethod
    def from_json(raw: Optional[str]) -> "RatchetLog":
        if not raw:
            return RatchetLog()
        try:
            data = json.loads(raw)
            return RatchetLog(bool(data.get("started_private")),
                              tuple(Escalation(**e) for e in data.get("escalations", ())))
        except (ValueError, TypeError):
            return RatchetLog()


_current: contextvars.ContextVar = contextvars.ContextVar("hub_privacy_ratchet", default=None)


def current_ratchet() -> Optional[Ratchet]:
    return _current.get()


@contextmanager
def ratchet_scope(start: Optional[Tier] = None):
    ratchet = Ratchet(tier=start, started_private=(start == Tier.HIGH))
    token = _current.set(ratchet)
    try:
        yield ratchet
    finally:
        _current.reset(token)


def apply_ratchet(sensitivity: object, redaction):
    """The level and redaction a call may use inside the current run: never below the ratchet, and no
    redaction at High (the route is end-to-end encrypted; redaction would add nothing, app/privacy/tiers.py)."""
    ratchet = _current.get()
    if ratchet is None or ratchet.tier is None:
        return sensitivity, redaction
    current = parse_tier(sensitivity) if sensitivity is not None else None   # garbled -> TierBlocked (fail closed)
    if rank(ratchet.tier) > rank(current):
        sensitivity = ratchet.tier.value
    if sensitivity == Tier.HIGH.value:
        redaction = None
    return sensitivity, redaction
```

Edit `app/llm_gateway.py`: after the `if sensitivity is None:` block (the one that reads `current_scope()`) and before `out = prepare(`, insert:

```python
        # The run's ratchet (app/privacy/ratchet.py) can only raise the level, never lower it.
        from app.privacy.ratchet import apply_ratchet
        sensitivity, redaction = apply_ratchet(sensitivity, redaction)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_privacy_ratchet.py tests/test_privacy_outbound.py tests/test_config_llmsel.py -q`
Expected: PASS (existing outbound tests are unaffected: no ratchet means no change).

- [ ] **Step 5: Checkpoint**

Run `git status` from the repo root; expect only the new test, `app/privacy/ratchet.py` and `app/llm_gateway.py` changed. Do not commit unless ordered. Commit message when ordered: `Privacy: one-way ratchet; gateway never sends below the run's level (D18)`.

---

### Task 2: The Health domain exists and is always High

**Files:**
- Create: `app/domains/health/__init__.py` (empty), `app/domains/health/categories.py`, `app/domains/health/spec.py`, `app/domains/health/handler.py` (first version), `app/domains/health/overview.py` (first version)
- Modify: `app/models/domain.py`, `app/domains/base.py` (`FieldSpec.private`), `app/domains/registry.py`, `app/domains/insurance/spec.py:30-32`, `app/privacy/settings.py`, `app/templates/privacy/settings.html`
- Test: `tests/test_health_domain.py`, update `tests/test_domain_registry.py`, `tests/test_privacy_settings.py` where they list domains/types

**Interfaces:**
- Consumes: `DomainSpec`, `CategorySpec`, `FieldSpec`, `FieldKind` from `app/domains/base.py`.
- Produces:
  - `Domain.HEALTH = "health"`
  - `FieldSpec.private: bool = False` (True = a health-record field that must never leave the Hub)
  - `HealthCategory` values: `lab_result, imaging_report, medical_report, prescription, referral, medical_bill, claim_statement, vaccination, appointment`
  - `RECORD_KINDS: tuple[tuple[str, str], ...]`, `PERSON_PAGE_TYPE = "health.person"`, `ALL_CATEGORIES: frozenset[str]`
  - `app.privacy.settings.FORCED_TIERS: dict[str, Tier]`, `forced_tier(domain) -> Optional[Tier]`
  - `TypeRow.locked: bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_health_domain.py`:

```python
"""The Health domain is registered, classifiable, and always High whatever the privacy switches say."""

import pytest

from app.domains.registry import get_spec, implemented_domains, is_implemented
from app.models.domain import Domain
from app.privacy import settings as ps
from app.privacy.scope import current_scope, privacy_scope
from app.privacy.tiers import Tier, TierBlocked
from app.services.domain_classifier import build_system_prompt

HEALTH_CATEGORIES = {"lab_result", "imaging_report", "medical_report", "prescription", "referral",
                     "medical_bill", "claim_statement", "vaccination", "appointment"}


def test_health_is_a_registered_domain_with_the_agreed_categories():
    assert Domain.HEALTH.value == "health" and is_implemented(Domain.HEALTH)
    spec = get_spec(Domain.HEALTH)
    assert {c.value for c in spec.categories} == HEALTH_CATEGORIES
    assert spec.home_url == "/health"


def test_the_classifier_can_tell_a_policy_from_a_claim_statement():
    prompt = build_system_prompt(implemented_domains())
    assert 'domain "health"' in prompt and 'category "claim_statement"' in prompt
    insurance_line = next(l for l in prompt.splitlines() if 'category "health_insurance"' in l)
    assert "reimburse" in insurance_line.lower() and "health" in insurance_line.lower()   # points claims to Health


def test_health_fields_mark_what_must_never_leave_the_hub():
    spec = get_spec(Domain.HEALTH)
    private = {f.key for f in spec.fields if f.private}
    admin = {f.key for f in spec.fields if not f.private}
    assert {"record_kind", "summary", "notes"} <= private
    assert {"person", "document_date", "provider", "provider_phone", "provider_address", "appointment_date",
            "appointment_time", "amount_billed", "amount_reimbursed", "amount_owed"} <= admin


def test_health_is_high_even_when_levels_are_not_binding(session):
    assert ps.enforcing(session) is False
    res = ps.resolve(session, Domain.HEALTH, "lab_result")
    assert (res.state, res.tier, res.redact) == ("ready", Tier.HIGH, False)
    assert ps.configured_tier(session, Domain.HEALTH, "lab_result") == "house_high"
    # other domains are unchanged
    assert ps.resolve(session, Domain.HOUSE, "house_appliance").state == "off"


def test_the_privacy_scope_for_health_carries_high_and_never_holds(session):
    with privacy_scope(session, Domain.HEALTH, "lab_result") as scope:
        assert scope.tier == Tier.HIGH and scope.redaction is None
        assert current_scope().tier == Tier.HIGH


def test_health_cannot_be_given_another_level(session):
    with pytest.raises(TierBlocked) as exc:
        ps.save_type(session, "health", "lab_result", "house_mid", False, "test")
    assert "always" in exc.value.reason.lower()


def test_type_rows_show_health_as_locked_high(session):
    rows = {(r.info.domain, r.info.category): r for r in ps.type_rows(session)}
    row = rows[("health", "lab_result")]
    assert row.locked and row.tier == Tier.HIGH and row.shown_tier == Tier.HIGH and row.is_saved
    assert rows[("house", "house_appliance")].locked is False


def test_confirm_suggestions_never_touches_health(session):
    ps.confirm_suggestions(session, "test")
    assert ps._setting(session, "health", "lab_result") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_health_domain.py -q`
Expected: FAIL (`AttributeError: HEALTH` / import errors)

- [ ] **Step 3: Implement**

`app/models/domain.py` — add the member (the column is VARCHAR(10), `"health"` fits; no migration):

```python
    HEALTH = "health"
```

`app/domains/base.py` — in `FieldSpec` add after `help: str = ""`:

```python
    # True: a health-record field (what was wrong, what was done). It stays in the Hub: it is never put in
    # a Telegram message, an email or a calendar entry (decision D21, app/privacy/channels.py).
    private: bool = False
```

`app/domains/health/categories.py`:

```python
"""Health document categories (stored in Document.category): what kind of paper it is."""

from enum import Enum


class HealthCategory(str, Enum):
    LAB_RESULT = "lab_result"
    IMAGING_REPORT = "imaging_report"
    MEDICAL_REPORT = "medical_report"
    PRESCRIPTION = "prescription"
    REFERRAL = "referral"
    MEDICAL_BILL = "medical_bill"
    CLAIM_STATEMENT = "claim_statement"
    VACCINATION = "vaccination"
    APPOINTMENT = "appointment"


ALL_CATEGORIES = frozenset(c.value for c in HealthCategory)

# What kind of event the paper belongs to (the "check-up" facet of the Health tab).
RECORD_KINDS = (
    ("check_up", "Check-up"),
    ("follow_up", "Follow-up"),
    ("test", "Test / exam"),
    ("procedure", "Procedure / surgery"),
    ("emergency", "Emergency"),
    ("dental", "Dental"),
    ("vaccination", "Vaccination"),
    ("other", "Other"),
)
KIND_LABELS = dict(RECORD_KINDS)

PERSON_PAGE_TYPE = "health.person"
```

`app/domains/health/handler.py` (first version; Task 8 replaces `process`):

```python
"""Health domain handler."""

from __future__ import annotations

from sqlmodel import Session

from app.domains.base import DomainHandler
from app.models.document import Document, DocumentStatus
from app.models.wiki import WikiOperation
from app.services.wiki_engine import ingest_into_wiki


class HealthHandler(DomainHandler):
    async def process(self, session: Session, document: Document) -> Document:
        await ingest_into_wiki(session, document, operation=WikiOperation.INGEST)
        document.status = DocumentStatus.PROCESSED
        document.failure_reason = None
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    async def on_fields_changed(self, session: Session, document: Document, previous_fields: dict[str, str]) -> None:
        await ingest_into_wiki(session, document, operation=WikiOperation.EDIT)
```

`app/domains/health/overview.py` (first version):

```python
"""Health's card on the cross-domain '/' Overview. Counts only: nothing clinical."""

from datetime import date

from sqlmodel import Session

from app.domains.base import CardLine, DomainCard
from app.domains.entries import domain_entries
from app.models.domain import Domain


def health_overview_card(session: Session, today: date) -> DomainCard:
    n = len(domain_entries(session, Domain.HEALTH))
    return DomainCard(label="Health", url="/health",
                      lines=[CardLine(text=f"{n} record{'s' if n != 1 else ''} on file", url="/health")])
```

`app/domains/health/spec.py`:

```python
"""Health's DomainSpec. Always house_high (app/privacy/settings.py FORCED_TIERS)."""

from app.domains.base import (
    CategorySpec, DomainSpec, EntityTypeSpec, FactPolicy, FactSpec, FieldKind, FieldSpec, NavLink, WikiSchema,
)
from app.domains.health.categories import ALL_CATEGORIES, PERSON_PAGE_TYPE, RECORD_KINDS, HealthCategory
from app.domains.health.handler import HealthHandler
from app.domains.health.overview import health_overview_card
from app.models.domain import Domain

_GUIDANCE = (
    "Health records of the family. Record standing facts per person that are worth finding later: long-term "
    "conditions and diagnoses, allergies, ongoing medication, vaccination status, the date and outcome of the "
    "last check-up or exam of each kind, and the provider usually seen for each area. Never record one-off "
    "amounts or appointment logistics. Title a page after the person and the topic, e.g. 'Maria: vaccinations'."
)

SPEC = DomainSpec(
    domain=Domain.HEALTH,
    label="Health",
    description=(
        "The family's medical papers: lab results, imaging reports, consultation and discharge letters, "
        "prescriptions, referrals, medical bills, health-insurance reimbursement statements, vaccination "
        "records and appointment letters."
    ),
    home_url="/health",
    categories=(
        CategorySpec(HealthCategory.LAB_RESULT.value, "Lab result", "Blood, urine or other laboratory results."),
        CategorySpec(HealthCategory.IMAGING_REPORT.value, "Imaging report",
                     "An X-ray, ultrasound, MRI, CT or similar report."),
        CategorySpec(HealthCategory.MEDICAL_REPORT.value, "Medical report",
                     "A consultation note, discharge letter, medical certificate or other clinical letter."),
        CategorySpec(HealthCategory.PRESCRIPTION.value, "Prescription", "A prescription for medicines or treatment."),
        CategorySpec(HealthCategory.REFERRAL.value, "Referral", "A referral to a specialist, exam or treatment."),
        CategorySpec(HealthCategory.MEDICAL_BILL.value, "Medical bill",
                     "An invoice or receipt from a doctor, clinic, hospital, dentist or pharmacy."),
        CategorySpec(HealthCategory.CLAIM_STATEMENT.value, "Reimbursement statement",
                     "A health insurer's statement of what it reimbursed or refused for medical expenses."),
        CategorySpec(HealthCategory.VACCINATION.value, "Vaccination record",
                     "A vaccination card, certificate or record."),
        CategorySpec(HealthCategory.APPOINTMENT.value, "Appointment",
                     "A letter, email or message confirming a medical appointment."),
    ),
    fields=(
        FieldSpec("person", "Whose it is", FieldKind.SUGGEST, help="The person the paper is about."),
        FieldSpec("document_date", "Date of the paper", FieldKind.DATE),
        FieldSpec("provider", "Doctor / clinic / lab", FieldKind.SUGGEST),
        FieldSpec("provider_phone", "Provider phone", FieldKind.TEXT),
        FieldSpec("provider_address", "Provider address", FieldKind.TEXT),
        FieldSpec("record_kind", "What kind of event", FieldKind.CHOICE, choices=RECORD_KINDS, private=True),
        FieldSpec("appointment_date", "Appointment date", FieldKind.DATE,
                  categories=frozenset({HealthCategory.APPOINTMENT.value})),
        FieldSpec("appointment_time", "Appointment time (HH:MM)", FieldKind.TEXT,
                  categories=frozenset({HealthCategory.APPOINTMENT.value})),
        FieldSpec("amount_billed", "Amount billed (EUR)", FieldKind.TEXT),
        FieldSpec("amount_reimbursed", "Amount reimbursed (EUR)", FieldKind.TEXT),
        FieldSpec("amount_owed", "Amount still owed (EUR)", FieldKind.TEXT),
        FieldSpec("summary", "Summary", FieldKind.LONGTEXT, private=True),
        FieldSpec("notes", "Notes", FieldKind.LONGTEXT, private=True),
    ),
    handler=HealthHandler(),
    nav_links=(NavLink("Records", "/health"), NavLink("Areas", "/health/areas"), NavLink("People", "/health/people")),
    overview_card=health_overview_card,
    document_url=lambda document: f"/health/documents/{document.id}",
    wiki=WikiSchema(
        guidance=_GUIDANCE,
        entity_types=(
            EntityTypeSpec(
                page_type=PERSON_PAGE_TYPE, label="People", key_field="person", categories=ALL_CATEGORIES,
                facts=(FactSpec("last_record", "Most recent paper", "document_date", policy=FactPolicy.LATEST),),
            ),
        ),
    ),
)
```

`app/domains/registry.py` — append `"app.domains.health.spec",` to `_SPEC_MODULES`.

`app/domains/insurance/spec.py` — replace the `HEALTH` CategorySpec description with:

```python
                     "A health insurance policy for one or more people: conditions, schedule, premium notice or "
                     "renewal letter. A statement of what the insurer reimbursed for medical expenses is NOT this: "
                     "that is a Health reimbursement statement."),
```

`app/privacy/settings.py` — add below `FALLBACK_SUGGESTION`:

```python
# Domains whose types are ALWAYS at this level, whether or not levels are binding and whatever the
# settings table says (decision D19). Health is the only one: nothing about it may ever be read below High.
FORCED_TIERS: dict[str, Tier] = {"health": Tier.HIGH}


def forced_tier(domain: object) -> Optional[Tier]:
    return None if domain is None else FORCED_TIERS.get(_s(domain))
```

Then change the functions listed (keep the rest as is):

```python
def known_types() -> list[TypeInfo]:
    out = []
    for spec in implemented_domains():
        for c in spec.categories:
            key = (spec.domain.value, c.value)
            suggested = FORCED_TIERS.get(spec.domain.value) or SUGGESTED_TIERS.get(key, FALLBACK_SUGGESTION)
            out.append(TypeInfo(spec.domain.value, spec.label, c.value, c.label, c.description, suggested))
    return out


def configured_tier(session: Session, domain: object, category: Optional[str]) -> Optional[str]:
    """The tier saved for a type, whether or not levels are binding yet (None = not set)."""
    if domain is None or category is None:
        return None
    forced = forced_tier(domain)
    if forced is not None:
        return forced.value
    row = _setting(session, domain, category)
    return row.tier if row is not None else None


def resolve(session: Session, domain: object, category: Optional[str]) -> Resolution:
    forced = forced_tier(domain)
    if forced is not None:
        return Resolution("ready", forced, False)
    if not enforcing(session):
        return Resolution("off")
    row = _setting(session, domain, category) if domain is not None and category is not None else None
    if row is None or row.tier is None:
        return Resolution("held")
    return Resolution("ready", parse_tier(row.tier), row.redact)
```

`TypeRow` gets `locked: bool = False` as its last field, and `type_rows` becomes:

```python
def type_rows(session: Session) -> list[TypeRow]:
    saved = {(r.domain, r.category): r for r in session.exec(select(DocumentTypeSetting))}
    rows = []
    for info in known_types():
        forced = FORCED_TIERS.get(info.domain)
        if forced is not None:
            rows.append(TypeRow(info, forced, False, forced, True, locked=True))
            continue
        row = saved.get((info.domain, info.category))
        tier = parse_tier(row.tier) if row is not None and row.tier else None
        rows.append(TypeRow(info, tier, bool(row and row.redact), tier or info.suggested, row is not None and tier is not None))
    return rows
```

`save_type` — first lines after the unknown-type check:

```python
    if forced_tier(domain) is not None:
        raise TierBlocked("Health is always High and cannot be given another level.")
```

`confirm_suggestions` iterates `type_rows` and skips rows with `r.tier is None`; locked rows have a tier, so they are skipped already (the test above pins it). In `retier` nothing changes: Health documents are stamped through `configured_tier` (Task 7 verifies).

`app/templates/privacy/settings.html` — replace the `<td>` cells of a type row so a locked row shows text instead of controls:

```html
    <td>
      {% if r.locked %}
      <strong>High (always)</strong>
      <div style="font-size:12px;color:#666;">Health papers are always private. This cannot be changed.</div>
      {% else %}
      <select name="tier" form="{{ fid }}">
        {% for value, text in tier_labels.items() %}
        <option value="{{ value }}"{% if value == r.shown_tier.value %} selected{% endif %}>{{ text }}</option>
        {% endfor %}
      </select>
      {% if not r.is_saved %}<div style="font-size:12px;color:#b36b00;">Suggested, not set yet</div>{% endif %}
      {% endif %}
    </td>
    <td>{% if r.locked %}<span style="color:#999;">not applicable</span>{% else %}<label><input type="checkbox" name="redact" value="1" form="{{ fid }}"{% if r.redact %} checked{% endif %}> on</label>{% endif %}</td>
    <td>{% if not r.locked %}<button form="{{ fid }}">Save</button>{% endif %}</td>
```

- [ ] **Step 4: Run tests; fix the lists that name domains**

Run: `python3 -m pytest tests/test_health_domain.py tests/test_domain_registry.py tests/test_privacy_settings.py tests/test_domain_classifier.py tests/test_domain_overview.py -q`
Expected: the new file passes. Existing tests that assert the exact set/count of implemented domains or types (grep for `implemented_domains`, `known_types`, `len(` of types) fail: update their expected sets to include `health` / its nine categories, locked and High. Do not loosen any assertion; add the new members.

Also run `python3 -m pytest tests/test_privacy_inbox_flow.py tests/test_privacy.py -q` — they must still pass untouched.

- [ ] **Step 5: Checkpoint**

`git status`: new `app/domains/health/*`, edits as listed. Commit message when ordered: `Health domain: registered, classifiable, always High (D19)`.

---

### Task 3: Reach — High types become reachable, with a tier on every item

Today (D16) a type set to High is *excluded* from Ask and the wiki check. Now High is reachable but ratcheting: the first model call is built from a view that hides private types (`without_private()`), and every tool reports the tier of what it returns.

**Files:**
- Modify: `app/privacy/reach.py`
- Test: `tests/test_privacy_reach.py` (update the High expectations), create `tests/test_privacy_reach_private.py`

**Interfaces:**
- Consumes: `ps.forced_tier`, `ps.known_types`.
- Produces on `Reach`:
  - `hide_private: bool` field; `filtering -> bool`
  - `type_tier(domain, category) -> Optional[Tier]` (forced tier, else the saved tier when binding, else `None`)
  - `is_private_type(domain, category) -> bool`; `domain_has_private(domain) -> bool`; `domain_is_private(domain) -> bool` (every category private); `has_private() -> bool`
  - `tier_of_source(source) -> Optional[Tier]`; `tier_of_todo(session, todo) -> Optional[Tier]`
  - `private_tier(sources: Iterable) -> Optional[Tier]` → `Tier.HIGH` if any source's type is private, else `None`
  - `without_private() -> Reach`
  - `TypeAccess.private: bool`; High is no longer an exclusion reason (`HIGH` constant stays for stored `LeftOut` rows)

- [ ] **Step 1: Write the failing test**

Create `tests/test_privacy_reach_private.py`:

```python
"""Reach with private (High) types: reachable, tiered, and hideable for the first call."""

import pytest

from app.models.domain import Domain
from app.privacy.reach import compute_reach
from app.privacy.tiers import Tier
from tests import privacy_world as pw
from tests.knowledge_factories import make_document, make_record


def test_a_high_type_is_reachable_but_private(session):
    pw.set_levels(session, mid=["financials/bill"], high=["insurance/home_insurance"])
    reach = compute_reach(session)
    a = reach.access[("insurance", "home_insurance")]
    assert a.included and a.private and a.reason is None and a.tier == Tier.HIGH
    assert reach.is_private_type("insurance", "home_insurance")
    assert not reach.is_private_type("financials", "bill")


def test_the_starting_level_ignores_private_types(session):
    pw.set_levels(session, low=["house/house_appliance"], mid=["financials/bill"], high=["insurance/home_insurance"])
    assert compute_reach(session).tier == Tier.MID
    pw.set_levels(session, high=["insurance/home_insurance"])
    assert compute_reach(session).tier == Tier.MID          # nothing else included: the Mid floor


def test_health_is_private_whether_or_not_levels_are_binding(session):
    for on in (False, True):
        pw.set_levels(session, on=on, mid=["financials/bill"])
        reach = compute_reach(session)
        assert reach.type_tier("health", "lab_result") == Tier.HIGH
        assert reach.is_private_type("health", "lab_result")
        assert reach.includes_type("health", "lab_result")


def test_health_is_private_when_levels_are_not_binding_and_everything_else_is_untouched(session):
    pw.set_levels(session, on=False)
    reach = compute_reach(session)
    assert reach.binding is False
    assert reach.type_tier("house", "house_appliance") is None
    assert reach.includes_type("house", "house_appliance")


def test_without_private_hides_private_documents_records_files_and_ids(session):
    pw.set_levels(session, mid=["financials/bill"])
    clean = make_document(session, domain=Domain.FINANCIALS, category="bill")
    health = make_document(session, domain=Domain.HEALTH, category="lab_result")
    rec = make_record(session, domain=Domain.HEALTH, category="appointment")
    full = compute_reach(session)
    assert full.includes_document(health) and full.includes_record(rec)
    view = full.without_private()
    assert view.includes_document(clean) and not view.includes_document(health)
    assert not view.includes_record(rec)
    assert view.included_document_ids(session) == {clean.id}
    assert not view.includes_file_of(session, health)


def test_without_private_filters_even_when_levels_are_not_binding(session):
    pw.set_levels(session, on=False)
    clean = make_document(session, domain=Domain.HOUSE, category="house_appliance")
    health = make_document(session, domain=Domain.HEALTH, category="lab_result")
    view = compute_reach(session).without_private()
    assert view.filtering and view.included_document_ids(session) == {clean.id}
    assert not view.includes_document(health)


def test_private_tier_and_domain_helpers(session):
    pw.set_levels(session, mid=["financials/bill"])
    reach = compute_reach(session)
    clean = make_document(session, domain=Domain.FINANCIALS, category="bill")
    health = make_document(session, domain=Domain.HEALTH, category="medical_bill")
    assert reach.private_tier([clean]) is None
    assert reach.private_tier([clean, health]) == Tier.HIGH
    assert reach.tier_of_source(health) == Tier.HIGH and reach.tier_of_source(clean) is None
    assert reach.domain_is_private("health") and reach.domain_has_private("health")
    assert not reach.domain_is_private("financials") and not reach.domain_has_private("financials")
    assert reach.has_private()


def test_a_wiki_claim_from_a_private_document_is_hidden_in_the_first_call_view(session):
    from tests.knowledge_factories import make_claim, make_page
    pw.set_levels(session, mid=["financials/bill"])
    health = make_document(session, domain=Domain.HEALTH, category="lab_result")
    page = make_page(session, "Maria: tests", domain=Domain.HEALTH)
    claim = make_claim(session, page, "cholesterol", "210", doc_ids=[health.id])
    full = compute_reach(session)
    assert full.wiki_view(session).page_visible(page.id)
    view = full.without_private().wiki_view(session)
    assert not view.page_visible(page.id) and not view.claim_visible(claim.id)
```

Update `tests/test_privacy_reach.py` — three places that encode "High is excluded" (read the file around each before editing):
1. `test_a_type_is_excluded_if_high_or_unset_or_hides_personal_details`: the `insurance/home_insurance` line becomes `== (True, None)`; rename the test to `..._if_unset_or_hides_personal_details` and drop `R_HIGH` from its docstring-level expectations.
2. `test_the_call_level_is_the_strictest_included_level`: comments change from "High is excluded, not strictest" to "High is private, not part of the starting level"; expected values stay.
3. Any test asserting that a High type's sentinel is absent from every request body (`pw.EXCLUDED` contains `HIGH`, `REC`): move `HIGH` and `REC` out of `EXCLUDED` into a new `pw.PRIVATE = [HIGH, REC]` list in `tests/privacy_world.py`, and make the wire-level tripwire assert: *absent from the first request body; present only in requests with `sensitivity == "house_high"`* (Task 4 writes that test; until then keep these two tests skipped with `@pytest.mark.skip(reason="rewritten in Task 4")`).

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_privacy_reach_private.py -q`
Expected: FAIL (`AttributeError: 'TypeAccess' ... private` / `Reach has no attribute is_private_type`)

- [ ] **Step 3: Implement in `app/privacy/reach.py`**

(a) `TypeAccess` gains a field:

```python
@dataclass(frozen=True)
class TypeAccess:
    domain: str
    category: str
    label: str
    included: bool
    reason: Optional[str]            # UNSET | HIDES when excluded (HIGH is no longer an exclusion, see `private`)
    tier: Optional[Tier]             # the saved level (None = not set)
    private: bool = False            # High: reachable, but reading it raises the run to High (app/privacy/ratchet.py)
```

(b) Add `highest`-free helper imports at the top: `from app.privacy.ratchet import rank` is NOT needed; keep `_ORDER = {Tier.LOW: 0, Tier.MID: 1}`.

(c) Replace the `Reach` dataclass header and the methods named below. Everything not mentioned stays.

```python
@dataclass
class Reach:
    binding: bool
    access: dict = field(default_factory=dict)          # (domain, category) -> TypeAccess
    tier: Optional[Tier] = None                           # level of the FIRST AI call (None = not binding)
    hide_private: bool = False                            # the view used to build the first call
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def filtering(self) -> bool:
        """Does this view remove anything? (binding levels, or private types hidden for the first call)"""
        return self.binding or self.hide_private

    @property
    def excluded_keys(self) -> frozenset:
        return frozenset(type_key(a.domain, a.category) for a in self.access.values() if not a.included)

    # tiers
    def type_tier(self, domain: object, category: Optional[str]) -> Optional[Tier]:
        forced = ps.forced_tier(domain)
        if forced is not None:
            return forced
        if not self.binding or domain is None or category is None:
            return None
        a = self.access.get((ps._s(domain), category))
        return a.tier if a else None

    def is_private_type(self, domain: object, category: Optional[str]) -> bool:
        return self.type_tier(domain, category) == Tier.HIGH

    def has_private(self) -> bool:
        return any(a.private for a in self.access.values()) or any(
            i.domain in ps.FORCED_TIERS for i in ps.known_types())

    def domain_has_private(self, domain: object) -> bool:
        return any(self.is_private_type(domain, i.category) for i in ps.known_types() if i.domain == ps._s(domain))

    def domain_is_private(self, domain: object) -> bool:
        cats = [i.category for i in ps.known_types() if i.domain == ps._s(domain)]
        return bool(cats) and all(self.is_private_type(domain, c) for c in cats)

    def tier_of_source(self, source: object) -> Optional[Tier]:
        return None if source is None else self.type_tier(source.domain, source.category)

    def private_tier(self, sources: "Iterable") -> Optional[Tier]:
        """HIGH if any of these sources is of a private type, else None."""
        return Tier.HIGH if any(self.tier_of_source(s) == Tier.HIGH for s in sources) else None

    def tier_of_todo(self, session: Session, todo: "Todo") -> Optional[Tier]:
        sources = []
        if todo.document_id is not None:
            sources.append(session.get(Document, todo.document_id))
        if todo.record_id is not None:
            sources.append(session.get(Record, todo.record_id))
        if todo.transaction_id is not None:
            txn = session.get(Transaction, todo.transaction_id)
            if txn is not None:
                sources.append(session.get(Document, txn.document_id))
        return self.private_tier(s for s in sources if s is not None)

    def without_private(self) -> "Reach":
        """The view the FIRST model call of a run is built from: private types are left out, so nothing
        High reaches a lower-level provider. Tools use the full reach and raise the run's ratchet."""
        return replace(self, hide_private=True, _cache={})

    # types
    def includes_type(self, domain: object, category: Optional[str]) -> bool:
        if self.hide_private and self.is_private_type(domain, category):
            return False
        if not self.binding:
            return True
        if domain is None or category is None:
            return False                                 # no type, no level: fail closed
        a = self.access.get((ps._s(domain), category))
        return a is not None and a.included
```

In the remaining methods, change every `if not self.binding:` guard to `if not self.filtering:` — exactly these methods: `included_document_ids`, `included_record_ids`, `includes_file_of`, `includes_todo`, `filter_entries`, `wiki_view`. Leave `left_out` on `self.binding` (what was left out is a statement about levels, not about the first-call view; the engine computes it on the full reach).

(d) `compute_reach` loop and call tier:

```python
    for info in ps.known_types():
        row = saved.get((info.domain, info.category))
        tier: Optional[Tier] = ps.forced_tier(info.domain)
        if tier is None and row is not None and row.tier is not None:
            try:
                tier = parse_tier(row.tier)
            except TierBlocked:
                tier = None                              # a garbled level counts as "no level"
        reason = None
        if tier is None:
            reason = UNSET
        elif tier != Tier.HIGH and row is not None and row.redact:
            reason = HIDES
        access[(info.domain, info.category)] = TypeAccess(
            info.domain, info.category, info.label, reason is None, reason, tier, private=(tier == Tier.HIGH))
    levels = [a.tier for a in access.values() if a.included and not a.private]
    call_tier = max(levels, key=_ORDER.__getitem__) if levels else Tier.MID
    return Reach(binding=True, access=access, tier=call_tier)
```

Update the module docstring's first paragraph: "EXCLUDED a type with no level, or whose hide-personal-details switch is on. A type at High is INCLUDED but *private*: ..." and the `LeftOut.problem` HIGH branch stays (old stored rows still carry it).

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_privacy_reach_private.py tests/test_privacy_reach.py tests/test_privacy_reach_wiki_check.py tests/test_ask_context.py tests/test_ask_tools.py -q`
Expected: PASS apart from the two tests skipped in Step 1. If another existing test fails because it relied on High being excluded, change it to the new rule (reachable + private) and say so in the Checkpoint note.

- [ ] **Step 5: Checkpoint**

`git status`. Commit message when ordered: `Reach: High types are reachable and carry a tier; first-call view hides them (D18)`.

---

### Task 4: Ask — the first call hides private records; using one raises the run to High and tells the person

**Files:**
- Create: `app/services/ask/private.py`, `alembic/versions/a1d4e8b72c93_ask_turn_escalation.py`
- Modify: `app/models/ask.py`, `app/services/ask/contracts.py`, `app/services/ask/engine.py`, `app/services/ask/context.py`, `app/services/ask/tools.py`, `app/services/ask/save.py`, `app/services/ask/messages.py`, `app/routers/ask.py:34-39`, `app/templates/ask/_turn.html`
- Test: `tests/test_ask_private.py`; unskip the two tests from Task 3 and rewrite them against `pw.PRIVATE`

**Interfaces:**
- Consumes: Task 1 (`ratchet_scope`, `RatchetLog`), Task 3 (`Reach.without_private`, `private_tier`, `domain_is_private`, `has_private`).
- Produces:
  - `ToolOutput.tier: Optional[Tier] = None` (HIGH = the output holds private content) and `ToolOutput.opened: str = ""` (plain words for the notice)
  - `AskTurn.escalation_json: Optional[str]`
  - `turn_is_private(turn) -> bool`, `chat_is_private(session, turn) -> bool`, `private_notices(turn) -> list[Problem]`
  - `build_system_prompt(session, today, left_out=None, reach=None)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ask_private.py`:

```python
"""Ask with private (Health) records: the first call carries none, touching one raises the run to High for
good, the person is told, and nothing private is saved or replayed below High. Real GatewayClient over a
MockTransport (tests/privacy_world.Wire), so the assertions are about what would leave the VPS."""

import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.models.domain import Domain
from app.privacy.ratchet import RatchetLog
from app.services.ask import messages as ask_messages
from app.services.ask.conversation import add_turn, start_conversation
from app.services.ask.engine import run_turn
from app.services.ask.private import chat_is_private, turn_is_private
from app.services.ask.save import AnswerNotSaveable, save_answer_as_wiki_page
from tests import privacy_world as pw
from tests.fakes.fake_gateway import gateway_text_result, gateway_tool_result
from tests.knowledge_factories import make_claim, make_document, make_page

TODAY = date(2026, 10, 16)
HEALTH = "SENTINEL-HEALTHREC"


@pytest.fixture()
def home(session):
    pw.set_levels(session, low=["house/house_appliance"])
    clean = make_document(session, domain=Domain.HOUSE, category="house_appliance",
                          fields={"item_name": "Boiler", "room": "Kitchen"}, filename="boiler.png")
    health = make_document(session, domain=Domain.HEALTH, category="lab_result", filename="lab.pdf",
                           fields={"person": "Maria", "provider": "Lab Norte", "summary": f"{HEALTH}-summary"})
    page = make_page(session, f"Maria: tests {HEALTH}", domain=Domain.HEALTH, summary=f"{HEALTH}-page")
    make_claim(session, page, "cholesterol", f"{HEALTH}-210", doc_ids=[health.id])
    return SimpleNamespace(clean=clean, health=health, page=page)


def _tool(name, **args):
    return gateway_tool_result([{"id": "t1", "name": name, "input": args}])


async def ask(session, wire, script, question="What was Maria's last lab result?"):
    wire.script = list(script)
    turn = start_conversation(session, question, started_by=None)
    return await run_turn(session, turn.id, gateway=wire.client(), today=TODAY)


@pytest.mark.asyncio
async def test_a_question_that_needs_no_private_record_stays_below_high_and_sends_none(session, home):
    wire = pw.Wire()
    turn = await ask(session, wire, [gateway_text_result("The boiler is in the Kitchen.")], "Where is the boiler?")
    bodies = wire.parsed()
    assert [b["sensitivity"] for b in bodies] == ["house_low"]
    assert HEALTH not in json.dumps(bodies) and "Lab Norte" not in json.dumps(bodies)
    assert turn.escalation_json is None and turn_is_private(turn) is False
    assert ask_messages.notices(turn) == []


@pytest.mark.asyncio
async def test_the_first_call_names_no_private_content_but_says_private_records_exist(session, home):
    wire = pw.Wire()
    await ask(session, wire, [gateway_text_result("ok")])
    system = wire.parsed()[0]["system"]
    assert "PRIVATE" in system and "health_" in system
    assert HEALTH not in system and "Maria" not in system and "Lab Norte" not in system


@pytest.mark.asyncio
async def test_using_a_private_record_raises_the_run_to_high_for_every_later_call(session, home):
    wire = pw.Wire()
    turn = await ask(session, wire, [
        _tool("find_sources", query="Maria lab"),
        gateway_text_result(f"Maria's result is in lab.pdf [[doc:{home.health.id}]]."),
    ])
    first, second = wire.parsed()
    assert first["sensitivity"] == "house_low" and HEALTH not in json.dumps(first)
    assert second["sensitivity"] == "house_high" and HEALTH in json.dumps(second)     # the private content went only here
    log = RatchetLog.from_json(turn.escalation_json)
    assert log.private and not log.started_private
    esc = log.first_private()
    assert (esc.step, esc.from_tier, esc.to_tier) == (1, "house_low", "house_high")


@pytest.mark.asyncio
async def test_the_person_is_told_explicitly_that_and_when_it_turned_private(session, home):
    wire = pw.Wire()
    turn = await ask(session, wire, [_tool("find_sources", query="Maria"), gateway_text_result("done [[doc:%d]]" % home.health.id)])
    note = ask_messages.notices(turn)[0]
    assert note.what == "This answer turned private."
    assert "step 1" in note.why and "private" in note.why.lower() and "no other AI service" in note.why
    assert note.next_step


@pytest.mark.asyncio
async def test_it_works_the_same_while_levels_are_not_binding(session, home):
    pw.set_levels(session, on=False)
    wire = pw.Wire()
    await ask(session, wire, [_tool("find_sources", query="Maria"), gateway_text_result("done")])
    first, second = wire.parsed()
    assert "sensitivity" not in first and HEALTH not in json.dumps(first)
    assert second["sensitivity"] == "house_high"


@pytest.mark.asyncio
async def test_reading_a_private_wiki_page_raises_the_run_too(session, home):
    wire = pw.Wire()
    turn = await ask(session, wire, [_tool("read_wiki_pages", page_ids=[home.page.id]), gateway_text_result("ok")])
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_low", "house_high"]
    assert turn_is_private(turn)


@pytest.mark.asyncio
async def test_a_follow_up_in_a_private_chat_starts_high_and_says_so(session, home):
    wire = pw.Wire()
    first = await ask(session, wire, [_tool("find_sources", query="Maria"), gateway_text_result("done")])
    wire.script = [gateway_text_result("And the boiler is in the Kitchen.")]
    second = add_turn(session, first.conversation_id, "And where is the boiler?", None)
    second = await run_turn(session, second.id, gateway=wire.client(), today=TODAY)
    last = wire.parsed()[-1]
    assert last["sensitivity"] == "house_high"                                   # the history carries private content
    assert chat_is_private(session, second) is True
    assert RatchetLog.from_json(second.escalation_json).started_private
    assert ask_messages.notices(second)[0].what == "This answer ran privately from the start."


@pytest.mark.asyncio
async def test_a_private_answer_cannot_be_saved_to_the_wiki(session, home):
    wire = pw.Wire()
    turn = await ask(session, wire, [_tool("find_sources", query="Maria"), gateway_text_result("done [[doc:%d]]" % home.health.id)])
    with pytest.raises(AnswerNotSaveable) as exc:
        save_answer_as_wiki_page(session, turn.id, "Maria")
    text = str(exc.value)
    assert text.startswith("Not saved") and "private" in text and "chat" in text      # what, why, what to do


@pytest.mark.asyncio
async def test_every_tool_that_returns_private_content_reports_high(session, home):
    """The tripwire: a tool whose output contains a private sentinel MUST say tier=HIGH, or the engine
    would send it on at a lower level."""
    from app.privacy.scope import reachable_scope
    from app.privacy.tiers import Tier
    from app.services.ask.tools import available_tools

    todo_doc = home.health
    from app.models.todo import Todo
    session.add(Todo(title=f"{HEALTH}-todo", domain=Domain.HEALTH, document_id=todo_doc.id))
    session.commit()
    args = {
        "read_wiki_pages": {"page_ids": [home.page.id]},
        "find_sources": {"query": "Maria"},
        "read_document_file": {"document_id": home.health.id},
        "list_todos": {},
    }
    with reachable_scope(session):
        for tool in available_tools():
            if tool.name.startswith(("financials_", "house_", "insurance_")):
                continue                                             # their data cannot hold a Health record
            assert tool.name in args or tool.name.startswith("health_"), f"add args for {tool.name}"
            if tool.name not in args:
                continue                                             # health_* tools are covered in Task 9
            out = tool.run(session, args[tool.name])
            if HEALTH in out.text or tool.name == "read_document_file" and not out.is_error:
                assert out.tier == Tier.HIGH, f"{tool.name} returned private content without tier=HIGH"
```

Note for the executor: `read_document_file` needs a real file; in this test the fixture's `file_path` ("/tmp/doc.pdf") does not exist, so the tool returns an error and the last `if` skips it. Keep it that way; Task 9's test covers the file path with a real PNG.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_ask_private.py -q`
Expected: FAIL (`ModuleNotFoundError: app.services.ask.private`)

- [ ] **Step 3: Implement**

`alembic/versions/a1d4e8b72c93_ask_turn_escalation.py`:

```python
"""ask_turns.escalation_json: the private-run log of an Ask answer (D18)

Revision ID: a1d4e8b72c93
Revises: e5b9d7f3a826
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1d4e8b72c93"
down_revision: Union[str, Sequence[str], None] = "e5b9d7f3a826"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ask_turns") as b:
        b.add_column(sa.Column("escalation_json", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ask_turns") as b:
        b.drop_column("escalation_json")
```

`app/models/ask.py` — in `AskTurn`, after `left_out_json`:

```python
    # NULL = the answer never touched a private record and did not start private. Otherwise the ratchet's log
    # (app.privacy.ratchet.RatchetLog): where the run turned High, or that it started High (decision D18).
    escalation_json: Optional[str] = None
```

`app/services/ask/contracts.py` — add imports `from typing import Optional` and `from app.privacy.tiers import Tier`, and two fields at the end of `ToolOutput`:

```python
    tier: Optional[Tier] = None   # HIGH: the output holds private content; the engine raises the run's ratchet
    opened: str = ""              # plain words for the notice, e.g. "3 private records"
```
Also extend the module docstring PRIVACY paragraph with: "A tool whose output contains content of a private (High) type MUST set `tier=Tier.HIGH`."

`app/services/ask/private.py`:

```python
"""Ask's private-run bookkeeping (decision D18): which answers ran private, and how the person is told."""

from __future__ import annotations

from sqlmodel import Session

from app.models.ask import AskTurn
from app.privacy.ratchet import RatchetLog
from app.problems import Problem
from app.services.ask.conversation import turns_of


def turn_is_private(turn: AskTurn) -> bool:
    return RatchetLog.from_json(turn.escalation_json).private


def chat_is_private(session: Session, turn: AskTurn) -> bool:
    """Did an earlier answer in this chat use private records? Then this answer's history carries them,
    so it must start High (the replayed answers are part of every call)."""
    return any(turn_is_private(t) for t in turns_of(session, turn.conversation_id) if t.position < turn.position)


def private_notices(turn: AskTurn) -> list[Problem]:
    log = RatchetLog.from_json(turn.escalation_json)
    if log.started_private:
        return [Problem(
            "This answer ran privately from the start.",
            "An earlier answer in this chat used private Health records, so everything after it stays private "
            "and goes only through the private, encrypted route.",
            "Nothing to do. Start a new chat for a question that doesn't need them.")]
    esc = log.first_private()
    if esc is None:
        return []
    return [Problem(
        "This answer turned private.",
        f"At step {esc.step} of the look-up it opened {esc.label}, which are private Health records. The steps "
        "before that carried no private records. From then on everything was sent only through the private, "
        "encrypted route and to no other AI service.",
        "Nothing to do. Private answers are shown only here in the Hub, never in Telegram or email, and can't "
        "be saved to the wiki.")]
```

`app/services/ask/messages.py` — import and use:

```python
from app.services.ask.private import private_notices
...
def notices(turn: AskTurn) -> list[Problem]:
    """Notes under an answer: it turned private, documents that weren't available, earlier answers that weren't reused."""
    left = LeftOut.from_json(turn.left_out_json)
    return [*private_notices(turn),
            *[p for p in (left.problem("answer"), left.history_problem()) if p is not None]]
```
(`private.py` imports `conversation.py`, which imports `citations`, not `messages`: no cycle.)

`app/services/ask/engine.py` — imports:

```python
from app.privacy.ratchet import Ratchet, ratchet_scope
from app.privacy.tiers import Tier
from app.services.ask.private import chat_is_private
```
Replace `run_turn` and the head of `_answer`:

```python
async def run_turn(session: Session, turn_id: int, gateway=None,
                   today: Optional[date] = None) -> AskTurn:
    """Answer one turn. The scopes are entered HERE, inside the code that actually runs (the router runs
    this in a background task with its own session): the privacy scope gives the first call its level, and
    the ratchet makes sure that once a tool returns private content nothing is sent below High (D18)."""
    with reachable_scope(session) as reach:
        turn = session.get(AskTurn, turn_id)
        start = Tier.HIGH if chat_is_private(session, turn) else reach.tier
        with ratchet_scope(start) as ratchet:
            return await _answer(session, turn_id, gateway, today, reach, ratchet)


async def _answer(session: Session, turn_id: int, gateway, today: Optional[date], reach: Reach,
                  ratchet: Ratchet) -> AskTurn:
    turn = session.get(AskTurn, turn_id)
    left_out = LeftOut(reach.binding, tuple(sorted(reach.excluded_keys)))
    ...
        left_out = reach.left_out(session, history_dropped=privacy_dropped(session, turn))
        system, index_citables = build_system_prompt(session, today or date.today(), left_out,
                                                     reach=reach.without_private())
```
Change the loop header to `for round_no in range(1, _MAX_TURNS + 1):` and, inside the `for call in result.tool_calls:` loop right after `output = ...` is computed (after the file-read bookkeeping, before `known.update`), add:

```python
                if output.tier is not None:
                    ratchet.raise_to(output.tier, step=round_no, label=output.opened or "private records")
```
After the try/except, before `if answer:` persist the log:

```python
    turn.escalation_json = ratchet.to_json() if (ratchet.escalations or ratchet.started_private) else None
```
(the line sits next to `turn.left_out_json = ...`). The `for ... else` clause stays attached to the renamed loop.

`app/services/ask/context.py` — signature and body:

```python
_PRIVATE_NOTE = (
    "Private records: some of the household's records (Health, and any type the family marked High) are private "
    "and are NOT listed above. Tools such as health_find_records read them; read_wiki_pages, find_sources and "
    "read_document_file can also return them if you ask for an id you were given. Using any of them makes this "
    "whole answer private: from then on it is handled only on a separate, encrypted route, and the person is "
    "told. Use them only when the question needs them. If it does not, answer without them."
)


def build_system_prompt(session: Session, today: date, left_out: Optional[LeftOut] = None,
                        reach: Optional[Reach] = None) -> tuple[str, list[Citable]]:
    """Everything the model is told about the household. ... The engine passes `reach.without_private()` here:
    the first call never names, counts or summarises a private record."""
    reach = reach or reach_for(session)
```
In the domain loop replace the body with:

```python
    for spec in implemented_domains():
        if reach.domain_is_private(spec.domain):
            domain_lines.append(f"- {spec.domain.value} ({spec.label}): PRIVATE. Nothing is listed here; use the "
                                f"{spec.domain.value}_* tools if the question needs it (this makes the answer private).")
            continue
        public = [c for c in spec.categories if not reach.is_private_type(spec.domain, c.value)]
        counts = Counter(e.category or "uncategorised" for e in settled_entries(session, spec.domain, reach=reach))
        categories = ", ".join(f"{c.value} ({c.label})" for c in public)
        fields = ", ".join(f.label for f in spec.fields) or "none"
        on_file = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "none"
        domain_lines.append(f"- {spec.domain.value} ({spec.label}): categories [{categories}]; "
                            f"details [{fields}]; sources on file (documents and records) [{on_file}]")
```
and before `return`, after the pending note: `if reach.has_private(): sections.append(_PRIVATE_NOTE)`.

`app/services/ask/tools.py` — add imports `from app.privacy.tiers import Tier` and, near the top, a helper:

```python
def _opened(reach: Reach, sources) -> str:
    n = sum(1 for s in sources if reach.tier_of_source(s) == Tier.HIGH)
    return f"{n} private record{'s' if n != 1 else ''}"
```
(a) `_read_wiki_pages`: change `view = reach_for(session).wiki_view(session)` to `reach = reach_for(session)` then `view = reach.wiki_view(session)`; keep a list `shown_sources: list = []`; inside `for source in {source_ref(s): s ...}.values():` add `shown_sources.append(source)` (before the `is_settled_source` check). The final return becomes:

```python
    tier = reach.private_tier(shown_sources)
    return ToolOutput(text="\n\n".join(parts), citables=citables,
                      tier=tier, opened=_opened(reach, shown_sources) if tier else "")
```
(b) `_find_sources`: `reach = reach_for(session)`; use it in `settled_entries(session, domain, reach=reach)`; at the end:

```python
    tier = reach.private_tier(s for _, _, s, _, _ in shown)
    return ToolOutput(text="\n".join(lines), citables=[citable_for_source(s) for _, _, s, _, _ in shown],
                      used_raw_sources=True, tier=tier,
                      opened=_opened(reach, [s for _, _, s, _, _ in shown]) if tier else "")
```
(c) `_read_document_file`: on success, before the final return:

```python
    attached = session.exec(select(Record).where(Record.document_id == doc.id)).first()
    sources = [doc] + ([attached] if attached is not None else [])
    tier = reach.private_tier(sources)
    ...ToolOutput(..., used_raw_sources=True, tier=tier, opened=_opened(reach, sources) if tier else "")
```
(d) `_list_todos`: after `todos = [...]`:

```python
    tier = Tier.HIGH if any(reach.tier_of_todo(session, t) == Tier.HIGH for t in todos) else None
    n = sum(1 for t in todos if reach.tier_of_todo(session, t) == Tier.HIGH)
    ... ToolOutput(..., used_raw_sources=True, tier=tier, opened=f"{n} private to-do{'s' if n != 1 else ''}" if tier else "")
```

`app/services/ask/save.py` — import `turn_is_private` and add right after the `AnswerNotSaveable` status check:

```python
    if turn_is_private(turn):
        raise AnswerNotSaveable(
            "Not saved to the wiki. This answer used private records, and a saved answer would be readable by "
            "every kind of question, including ones sent below the private level. Keep it in this chat; it is "
            "shown only here in the Hub.")
```

`app/routers/ask.py::_turn_view` add `"private": turn_is_private(turn)` (import from `app.services.ask.private`).

`app/templates/ask/_turn.html` — change `{% elif rendered.sources %}` (the save-form branch) to `{% elif rendered.sources and not private %}` and, directly under the first `<p class="ask-question">` line, add:

```html
  {% if private %}<p class="ask-note"><span title="Handled only on the private, encrypted route">&#128274; Private answer</span></p>{% endif %}
```

`tests/privacy_world.py` — add `PRIVATE = [HIGH, REC]` and remove those two sentinels from `EXCLUDED`.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_ask_private.py tests/test_ask_engine.py tests/test_ask_tools.py tests/test_ask_context.py tests/test_ask_save.py tests/test_ask_router.py tests/test_ask_conversation.py tests/test_privacy_reach.py -q`
Expected: PASS. Unskip the two tests from Task 3 and rewrite them: for each sentinel in `pw.PRIVATE`, assert it is absent from every request body whose `sensitivity != "house_high"` (use `wire.parsed()` after a scripted run that calls `find_sources` with a query matching it), and that the run's later requests are `house_high`.

- [ ] **Step 5: Checkpoint**

Commit message when ordered: `Ask: private records hidden from the first call; touching one raises the run to High and tells the person (D18)`.

---

### Task 5: The weekly wiki check audits private domains at High and everything else without them

**Files:**
- Modify: `app/services/wiki_lint/llm_checks.py:94-160`
- Test: `tests/test_privacy_reach_wiki_check.py` (extend)

**Interfaces:**
- Consumes: `Reach.without_private`, `domain_has_private`, `ratchet_scope`.
- Produces: `build_domain_payload(session, spec, reach: Optional[Reach] = None)`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_privacy_reach_wiki_check.py` (reuse its imports; add the ones missing):

```python
@pytest.mark.asyncio
async def test_the_audit_of_a_private_domain_goes_out_high_and_other_audits_carry_none_of_it(session):
    import json
    from app.models.domain import Domain
    from app.privacy.scope import reachable_scope
    from app.services.wiki_lint.llm_checks import run_llm_checks
    from tests import privacy_world as pw
    from tests.knowledge_factories import make_claim, make_document, make_page

    SENT = "SENTINEL-LINT-HEALTH"
    pw.set_levels(session, low=["house/house_appliance"])
    clean = make_document(session, domain=Domain.HOUSE, category="house_appliance", fields={"item_name": "Boiler"})
    page_clean = make_page(session, "Boiler", domain=Domain.HOUSE)
    make_claim(session, page_clean, "room", "Kitchen", doc_ids=[clean.id])
    health = make_document(session, domain=Domain.HEALTH, category="lab_result", fields={"person": "Maria", "summary": SENT})
    page_h = make_page(session, f"Maria {SENT}", domain=Domain.HEALTH)
    make_claim(session, page_h, "cholesterol", SENT, doc_ids=[health.id])

    wire = pw.Wire()
    with reachable_scope(session):
        drafts, errors = await run_llm_checks(session, gateway=wire.client())
    assert errors == []
    bodies = wire.parsed()
    with_sentinel = [b for b in bodies if SENT in json.dumps(b)]
    assert with_sentinel and all(b["sensitivity"] == "house_high" for b in with_sentinel)
    assert all(SENT not in json.dumps(b) for b in bodies if b["sensitivity"] != "house_high")
    assert any(b["sensitivity"] == "house_low" for b in bodies)            # the House audit still runs at its own level
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_privacy_reach_wiki_check.py -q`
Expected: FAIL (the Health audit leaks the sentinel into a `house_low` body, or sensitivity is not `house_high`)

- [ ] **Step 3: Implement**

In `app/services/wiki_lint/llm_checks.py` add imports `from typing import Optional`, `from app.privacy.ratchet import ratchet_scope`, `from app.privacy.tiers import Tier`, `from app.privacy.reach import Reach`. Change the signature and first lines of `build_domain_payload`:

```python
def build_domain_payload(session: Session, spec: DomainSpec, reach: Optional[Reach] = None) -> DomainPayload:
    """... `reach` is the view the audit may read; default = the run's reach."""
    reach = reach or reach_for(session)
    view = reach.wiki_view(session)
```
(delete the old `reach = reach_for(session)` line below the docstring). In `check_domain`:

```python
async def check_domain(session: Session, spec: DomainSpec, gateway=None) -> list[FindingDraft]:
    reach = reach_for(session)
    private = reach.domain_has_private(spec.domain)
    # A domain that holds private records is audited as a whole at High; every other domain is audited
    # from a view without them (a claim with any private source is left out, D16/D18).
    payload = build_domain_payload(session, spec, reach=reach if private else reach.without_private())
    if not payload.page_ids and not payload.document_ids and not payload.record_ids:
        return []
    gw = gateway or get_gateway()
    with ratchet_scope(Tier.HIGH if private else None):
        result = await gw.run(
            CRITIQUE_REVIEW, _SYSTEM_PROMPT, payload.text,
            max_tokens=_MAX_TOKENS, response_schema=_FINDINGS_SCHEMA,
        )
```
Keep the rest of the function unchanged. (`ratchet_scope(None)` is a no-op ratchet: `tier=None` leaves the call exactly as before.)

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_privacy_reach_wiki_check.py tests/test_wiki_lint*.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint**

Commit message when ordered: `Wiki check: private domains audited at High, other domains audited without private claims (D18)`.

---

### Task 6: Intake — an unrecognised paper gets one private read; channel replies never name a private type

**Files:**
- Modify: `app/services/inbox_service.py:75-105` (`receive_document`), `app/channels/telegram_bot.py:78-113`
- Create: `app/privacy/channels.py` (label part only here; Task 11 adds the field guard)
- Test: `tests/test_privacy_inbox_flow.py` (update), `tests/test_health_intake.py`

**Interfaces:**
- Consumes: `ratchet_scope`, `reach_for`, `Reach.is_private_type`.
- Produces: `channel_label(session, domain, category, label: Optional[str]) -> Optional[str]`; `reply_for(receipt, base_url, session=None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_health_intake.py`:

```python
"""Intake (decision D20): a paper no rule recognises is read ONCE, privately, to decide what it is;
and a Telegram reply never names a private type."""

import pytest

from app.channels.telegram_bot import reply_for
from app.models.document import DocumentSource
from app.models.domain import Domain
from app.privacy.ratchet import current_ratchet
from app.privacy.tiers import Tier
from app.services.domain_classifier import DomainSuggestion
from app.services.ingestion import IncomingFile
from app.services.inbox_service import receive_document
from tests import privacy_world as pw


def _incoming(name="letter.pdf", content=b"%PDF-1.4 unknown"):
    return IncomingFile(filename=name, content=content, source=DocumentSource.EMAIL, uploaded_by="test")


@pytest.fixture(autouse=True)
def docs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", [True, False])
async def test_an_unrecognised_paper_is_classified_inside_a_high_run_whatever_the_switch(session, binding):
    pw.set_levels(session, on=binding)
    seen = []

    async def classify(path, context_text):
        seen.append(current_ratchet().tier if current_ratchet() else None)
        return DomainSuggestion(Domain.HEALTH, "lab_result", 0.93, "looks like a lab result")

    receipt = await receive_document(session, _incoming(), context_text=None, external_ref="x", classify=classify)
    assert seen == [Tier.HIGH]
    assert receipt.inbox_item.suggested_domain == "health" and receipt.inbox_item.suggested_category == "lab_result"


@pytest.mark.asyncio
async def test_a_failed_private_read_leaves_a_three_part_note_and_the_document_in_the_inbox(session):
    pw.set_levels(session, on=True)

    async def classify(path, context_text):
        raise RuntimeError("house_high has no route")

    receipt = await receive_document(session, _incoming(), context_text=None, external_ref="y", classify=classify)
    note = receipt.inbox_item.classifier_note
    assert receipt.inbox_item.suggested_domain is None
    assert note.startswith("Automatic sorting didn't work.") and "Tell me what it is" in note


@pytest.mark.asyncio
async def test_the_telegram_reply_for_a_private_type_does_not_name_it(session):
    pw.set_levels(session, on=False)

    async def classify(path, context_text):
        return DomainSuggestion(Domain.HEALTH, "lab_result", 0.95, "x")

    receipt = await receive_document(session, _incoming(), context_text=None, external_ref="z", classify=classify)
    reply = reply_for(receipt, "https://hub.example", session=session)
    assert "Lab result" not in reply and "Health" not in reply
    assert "private document" in reply and "https://hub.example/inbox" in reply


@pytest.mark.asyncio
async def test_the_telegram_reply_for_an_ordinary_type_still_names_it(session):
    pw.set_levels(session, on=False)

    async def classify(path, context_text):
        return DomainSuggestion(Domain.HOUSE, "house_appliance", 0.95, "x")

    receipt = await receive_document(session, _incoming("a.pdf", b"%PDF other"), context_text=None, external_ref="w", classify=classify)
    assert "Appliance" in reply_for(receipt, "https://hub.example", session=session) or \
           "House" in reply_for(receipt, "https://hub.example", session=session)
```

Update `tests/test_privacy_inbox_flow.py`: any test that asserts the old "A new kind of document — nothing has read it yet. Tell me what it is." held branch while levels are binding now asserts the new behaviour (classified in a High run; the stored note is the classifier's reason). Delete only assertions about that held branch; keep every test about recognition, approval and `TierHeld`.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_health_intake.py -q`
Expected: FAIL (`current_ratchet()` is None inside `classify`, and `reply_for()` rejects `session=`)

- [ ] **Step 3: Implement**

`app/privacy/channels.py`:

```python
"""What may leave the Hub through Telegram, email or the calendar (decision D21).

Administrative facts may; health-record facts never do — for those the message points to the Hub.
Two parts: the label of a document's type (here) and the fields of a document (`channel_fields`, Task 11)."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from app.privacy.reach import reach_for

PRIVATE_LABEL = "a private document"


def channel_label(session: Session, domain: object, category: Optional[str], label: Optional[str]) -> Optional[str]:
    """The type's label for a message that leaves the Hub, or 'a private document' when the type is private."""
    if reach_for(session).is_private_type(domain, category):
        return PRIVATE_LABEL
    return label
```

`app/services/inbox_service.py` — add imports `from app.privacy.ratchet import ratchet_scope`, `from app.privacy.tiers import Tier`, `from app.problems import explain`; drop `from app.privacy import settings as privacy_settings` if nothing else uses it (grep first). Replace branches 2 and 3 of `receive_document` with:

```python
    else:
        # 2. A paper no rule recognises is read ONCE, inside a High run, to decide what it is (decision D20):
        #    it may turn out to be a private Health paper, so it never goes to a lower level first.
        try:
            with ratchet_scope(Tier.HIGH):
                suggestion = await classify(document.file_path, context_text)
        except Exception as exc:
            # Broad by design: a classifier failure (route down, credits, bad response) must never lose the
            # document -- it arrives without a suggestion and a human chooses.
            note = explain("Automatic sorting didn't work.", str(exc)).text + " Tell me what it is."
            suggestion = DomainSuggestion(None, None, 0.0, note)
```
`app/channels/telegram_bot.py::reply_for`:

```python
from app.privacy.channels import channel_label

def reply_for(receipt: InboxReceipt, base_url: str, session=None) -> str:
    inbox_url = f"{base_url.rstrip('/')}/inbox"
    if receipt.duplicate:
        ...unchanged...
    item = receipt.inbox_item
    if item is not None and is_confident(item):
        domain = suggested_domain(item)
        label = describe(domain, item.suggested_category)
        if session is not None:
            label = channel_label(session, domain, item.suggested_category, label)
        return f"Got it — looks like {label}. Approve it in the Inbox: {inbox_url}"
    return f"Got it — I'm not sure where this belongs. Please sort it in the Inbox: {inbox_url}"
```
and in `file_to_inbox` call `reply = reply_for(receipt, base_url, session=session)`.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_health_intake.py tests/test_privacy_inbox_flow.py tests/test_inbox_service.py tests/test_telegram_bot.py tests/test_email_poller.py -q`
Expected: PASS (adjust test files that call `reply_for` positionally — the new parameter is keyword-optional).

- [ ] **Step 5: Checkpoint**

Commit message when ordered: `Intake: unrecognised papers are read once inside a High run; channel replies never name a private type (D20, D21)`.

---

### Task 7: Health data — people, areas/conditions, entries, measurements

**Files:**
- Create: `app/models/health.py`, `alembic/versions/b2e5f9c83d04_health_tables.py`, `app/domains/health/text.py`, `app/domains/health/people.py`, `app/domains/health/areas.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_health_models.py`, `tests/test_health_people_areas.py`

**Interfaces:**
- Produces (models): `HealthPerson(id, name, match_key, confirmed, created_at)`, `HealthArea(id, name, kind["area"|"condition"], match_key, approved, merged_into_id, created_at)`, `HealthEntry(id, document_id, person_id, entry_date, year, kind, provider, summary, full_text, amount_billed, amount_reimbursed, amount_owed, needs_check, calendar_event_id, created_at, updated_at)`, `HealthEntryTag(entry_id, area_id)`, `HealthMeasurement(id, entry_id, person_id, measured_on, test, test_key, value, value_text, unit, ref_low, ref_high, out_of_range, needs_check, source_text)`
- Produces (services):
  - `text.fold(text) -> str`, `text.match_key(text) -> str`
  - `people.find_or_create_person(session, name: Optional[str]) -> Optional[HealthPerson]`; `people.confirm_person(session, person_id)`; `people.merge_people(session, source_id, target_id)`; `people.all_people(session) -> list[HealthPerson]`
  - `areas.resolve_area(session, name, kind) -> HealthArea` (follows `merged_into_id`); `areas.set_tags(session, entry, areas: list[str], conditions: list[str])`; `areas.approve_area(session, area_id)`; `areas.rename_area(session, area_id, new_name)`; `areas.merge_areas(session, source_id, target_id)`; `areas.names_for_prompt(session, kind) -> list[str]`; `areas.tags_of(session, entry_id) -> list[HealthArea]`

- [ ] **Step 1: Write the failing tests**

`tests/test_health_people_areas.py`:

```python
import pytest

from app.domains.health import areas, people
from app.domains.health.text import fold, match_key
from app.models.document import Document
from app.models.health import HealthEntry, HealthEntryTag, HealthMeasurement
from tests.knowledge_factories import make_document
from app.models.domain import Domain


def _entry(session, person=None):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result")
    e = HealthEntry(document_id=doc.id, person_id=person.id if person else None)
    session.add(e)
    session.commit()
    session.refresh(e)
    return e


def test_fold_and_match_key_ignore_case_accents_and_spacing():
    assert fold("João  Évora") == "joao  evora"
    assert match_key("  João   ÉVORA ") == "joao evora"


def test_a_person_is_discovered_once_then_matched_with_spelling_variants(session):
    a = people.find_or_create_person(session, "Maria Santos Almeida")
    assert a.confirmed is False
    assert people.find_or_create_person(session, "maria  santos almeida").id == a.id
    assert people.find_or_create_person(session, "Maria Santos").id == a.id       # a unique name-part match
    assert people.find_or_create_person(session, "Pedro Santos Almeida").id != a.id
    assert people.find_or_create_person(session, "  ") is None
    assert people.find_or_create_person(session, None) is None


def test_two_people_sharing_a_part_are_not_merged_automatically(session):
    people.find_or_create_person(session, "Ana Santos")
    people.find_or_create_person(session, "Rita Santos")
    c = people.find_or_create_person(session, "Santos")                            # one token: never auto-matched
    assert c.name == "Santos"


def test_merging_people_moves_everything_and_removes_the_duplicate(session):
    keep = people.find_or_create_person(session, "Maria Santos Almeida")
    dup = people.find_or_create_person(session, "Mariah Almeida")
    e = _entry(session, dup)
    session.add(HealthMeasurement(entry_id=e.id, person_id=dup.id, test="LDL", test_key="ldl"))
    session.commit()
    people.merge_people(session, dup.id, keep.id)
    session.refresh(e)
    assert e.person_id == keep.id
    assert session.get(type(dup), dup.id) is None
    assert all(m.person_id == keep.id for m in session.query(HealthMeasurement).all())


def test_an_area_is_proposed_unapproved_and_reused_by_name(session):
    eyes = areas.resolve_area(session, "Eyes", "area")
    assert eyes.approved is False
    assert areas.resolve_area(session, " eyes ", "area").id == eyes.id
    assert areas.resolve_area(session, "Eyes", "condition").id != eyes.id          # kinds are separate lists


def test_tags_attach_to_the_entry_and_replace_on_retag(session):
    e = _entry(session)
    areas.set_tags(session, e, ["Eyes", "Skin"], ["Glaucoma"])
    assert {a.name for a in areas.tags_of(session, e.id)} == {"Eyes", "Skin", "Glaucoma"}
    areas.set_tags(session, e, ["Eyes"], [])
    assert {a.name for a in areas.tags_of(session, e.id)} == {"Eyes"}


def test_merging_areas_repoints_tags_without_duplicates_and_redirects_the_name(session):
    e = _entry(session)
    a = areas.resolve_area(session, "Digestive", "area")
    b = areas.resolve_area(session, "Digestive system", "area")
    areas.set_tags(session, e, ["Digestive", "Digestive system"], [])
    areas.merge_areas(session, a.id, b.id)
    assert [x.id for x in areas.tags_of(session, e.id)] == [b.id]
    assert areas.resolve_area(session, "Digestive", "area").id == b.id              # the old name now leads to the kept one


def test_approve_and_rename(session):
    a = areas.resolve_area(session, "Skin", "area")
    areas.approve_area(session, a.id)
    areas.rename_area(session, a.id, "Skin and hair")
    session.refresh(a)
    assert a.approved and a.name == "Skin and hair" and a.match_key == "skin and hair"
    assert areas.names_for_prompt(session, "area") == ["Skin and hair"]


def test_renaming_onto_an_existing_name_is_refused_with_a_three_part_message(session):
    a = areas.resolve_area(session, "Skin", "area")
    areas.resolve_area(session, "Eyes", "area")
    with pytest.raises(areas.AreaError) as exc:
        areas.rename_area(session, a.id, "eyes")
    p = exc.value.problem
    assert p.what and p.why and p.next_step
```

`tests/test_health_models.py`:

```python
from sqlmodel import SQLModel

def test_the_five_tables_exist():
    from app import models  # noqa: F401
    names = set(SQLModel.metadata.tables)
    assert {"health_people", "health_areas", "health_entries", "health_entry_tags", "health_measurements"} <= names
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_health_people_areas.py tests/test_health_models.py -q`
Expected: FAIL (`ModuleNotFoundError: app.domains.health.areas`)

- [ ] **Step 3: Implement**

`app/models/health.py`:

```python
"""Health's own tables. A paper itself is a Document (domain "health"); these hold what was read from it
and what the Health tab filters on: whose it is, what it is about, and every measurement."""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class HealthPerson(SQLModel, table=True):
    __tablename__ = "health_people"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    match_key: str = Field(index=True)
    confirmed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthArea(SQLModel, table=True):
    """A body area/system ("Eyes") or a condition ("Stroke"). Proposed by the AI, approved by a person."""
    __tablename__ = "health_areas"
    __table_args__ = (UniqueConstraint("kind", "match_key", name="uq_health_areas_kind_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    kind: str = Field(index=True)                       # "area" | "condition"
    match_key: str
    approved: bool = Field(default=False)
    merged_into_id: Optional[int] = Field(default=None, foreign_key="health_areas.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthEntry(SQLModel, table=True):
    """What the Health tab lists: one row per filed health paper."""
    __tablename__ = "health_entries"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", unique=True, index=True)
    person_id: Optional[int] = Field(default=None, foreign_key="health_people.id", index=True)
    entry_date: Optional[date] = None
    year: Optional[int] = Field(default=None, index=True)
    kind: Optional[str] = Field(default=None, index=True)
    provider: Optional[str] = None
    summary: Optional[str] = None
    full_text: Optional[str] = None
    amount_billed: Optional[float] = None
    amount_reimbursed: Optional[float] = None
    amount_owed: Optional[float] = None
    needs_check: bool = Field(default=False)
    calendar_event_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class HealthEntryTag(SQLModel, table=True):
    __tablename__ = "health_entry_tags"

    entry_id: int = Field(foreign_key="health_entries.id", primary_key=True)
    area_id: int = Field(foreign_key="health_areas.id", primary_key=True)


class HealthMeasurement(SQLModel, table=True):
    __tablename__ = "health_measurements"

    id: Optional[int] = Field(default=None, primary_key=True)
    entry_id: int = Field(foreign_key="health_entries.id", index=True)
    person_id: Optional[int] = Field(default=None, foreign_key="health_people.id", index=True)
    measured_on: Optional[date] = None
    test: str
    test_key: str = Field(default="", index=True)
    value: Optional[float] = None
    value_text: Optional[str] = None
    unit: Optional[str] = None
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    out_of_range: Optional[bool] = None
    needs_check: bool = Field(default=False)
    source_text: Optional[str] = None                   # the line as printed, shown beside the value
```

`app/models/__init__.py` — append:

```python
from app.models.health import HealthArea, HealthEntry, HealthEntryTag, HealthMeasurement, HealthPerson  # noqa: F401
```

`alembic/versions/b2e5f9c83d04_health_tables.py`:

```python
"""Health tables: people, areas, entries, entry tags, measurements

Revision ID: b2e5f9c83d04
Revises: a1d4e8b72c93
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2e5f9c83d04"
down_revision: Union[str, Sequence[str], None] = "a1d4e8b72c93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_people",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("match_key", sa.String(), nullable=False, index=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "health_areas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, index=True),
        sa.Column("match_key", sa.String(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("merged_into_id", sa.Integer(), sa.ForeignKey("health_areas.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kind", "match_key", name="uq_health_areas_kind_key"),
    )
    op.create_table(
        "health_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False, unique=True, index=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("health_people.id"), nullable=True, index=True),
        sa.Column("entry_date", sa.Date(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True, index=True),
        sa.Column("kind", sa.String(), nullable=True, index=True),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("full_text", sa.String(), nullable=True),
        sa.Column("amount_billed", sa.Float(), nullable=True),
        sa.Column("amount_reimbursed", sa.Float(), nullable=True),
        sa.Column("amount_owed", sa.Float(), nullable=True),
        sa.Column("needs_check", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("calendar_event_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "health_entry_tags",
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("health_entries.id"), primary_key=True),
        sa.Column("area_id", sa.Integer(), sa.ForeignKey("health_areas.id"), primary_key=True),
    )
    op.create_table(
        "health_measurements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("health_entries.id"), nullable=False, index=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("health_people.id"), nullable=True, index=True),
        sa.Column("measured_on", sa.Date(), nullable=True),
        sa.Column("test", sa.String(), nullable=False),
        sa.Column("test_key", sa.String(), nullable=False, server_default="", index=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("value_text", sa.String(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("ref_low", sa.Float(), nullable=True),
        sa.Column("ref_high", sa.Float(), nullable=True),
        sa.Column("out_of_range", sa.Boolean(), nullable=True),
        sa.Column("needs_check", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_text", sa.String(), nullable=True),
    )


def downgrade() -> None:
    for table in ("health_measurements", "health_entry_tags", "health_entries", "health_areas", "health_people"):
        op.drop_table(table)
```

`app/domains/health/text.py`:

```python
"""Name folding for matching people, areas and tests ('Évora' == 'evora')."""

import re
import unicodedata


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def match_key(text: str) -> str:
    return re.sub(r"\s+", " ", fold(text)).strip()
```

`app/domains/health/people.py`:

```python
"""Whose a paper is. People are discovered from the papers and confirmed once (Pedro, Q12); a name that is
exactly or uniquely a part of a known name is the same person, anything else is a new, unconfirmed person."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.domains.health.text import match_key
from app.models.health import HealthEntry, HealthMeasurement, HealthPerson


def all_people(session: Session) -> list[HealthPerson]:
    return list(session.exec(select(HealthPerson).order_by(HealthPerson.name)).all())


def find_or_create_person(session: Session, name: Optional[str]) -> Optional[HealthPerson]:
    key = match_key(name or "")
    if not key:
        return None
    people = all_people(session)
    for p in people:
        if p.match_key == key:
            return p
    tokens = set(key.split())
    if len(tokens) >= 2:
        related = [p for p in people if len(p.match_key.split()) >= 2
                   and (tokens <= set(p.match_key.split()) or set(p.match_key.split()) <= tokens)]
        if len(related) == 1:
            return related[0]
    person = HealthPerson(name=name.strip(), match_key=key, confirmed=False)
    session.add(person)
    session.commit()
    session.refresh(person)
    return person


def confirm_person(session: Session, person_id: int) -> None:
    person = session.get(HealthPerson, person_id)
    if person is not None:
        person.confirmed = True
        session.add(person)
        session.commit()


def merge_people(session: Session, source_id: int, target_id: int) -> None:
    if source_id == target_id:
        return
    for model in (HealthEntry, HealthMeasurement):
        for row in session.exec(select(model).where(model.person_id == source_id)).all():
            row.person_id = target_id
            session.add(row)
    source = session.get(HealthPerson, source_id)
    if source is not None:
        session.delete(source)
    target = session.get(HealthPerson, target_id)
    if target is not None:
        target.confirmed = True
        session.add(target)
    session.commit()
```

`app/domains/health/areas.py`:

```python
"""Areas (eyes, skin, digestive system...) and conditions (stroke, diabetes...). The list is not preset: the
AI proposes names while reading Pedro's papers, a person approves, renames or merges them (Pedro, Q10)."""

from __future__ import annotations

from sqlmodel import Session, select

from app.domains.health.text import match_key
from app.models.health import HealthArea, HealthEntry, HealthEntryTag
from app.problems import Problem

KINDS = ("area", "condition")


class AreaError(Exception):
    def __init__(self, problem: Problem):
        super().__init__(problem.text)
        self.problem = problem


def _follow(session: Session, area: HealthArea) -> HealthArea:
    seen = set()
    while area.merged_into_id is not None and area.id not in seen:
        seen.add(area.id)
        nxt = session.get(HealthArea, area.merged_into_id)
        if nxt is None:
            break
        area = nxt
    return area


def resolve_area(session: Session, name: str, kind: str) -> HealthArea:
    key = match_key(name)
    existing = session.exec(select(HealthArea).where(HealthArea.kind == kind, HealthArea.match_key == key)).first()
    if existing is not None:
        return _follow(session, existing)
    area = HealthArea(name=name.strip(), kind=kind, match_key=key, approved=False)
    session.add(area)
    session.commit()
    session.refresh(area)
    return area


def tags_of(session: Session, entry_id: int) -> list[HealthArea]:
    rows = session.exec(select(HealthArea).join(HealthEntryTag, HealthEntryTag.area_id == HealthArea.id)
                        .where(HealthEntryTag.entry_id == entry_id).order_by(HealthArea.kind, HealthArea.name)).all()
    return list(rows)


def set_tags(session: Session, entry: HealthEntry, areas: list[str], conditions: list[str]) -> None:
    wanted: dict[int, HealthArea] = {}
    for names, kind in ((areas, "area"), (conditions, "condition")):
        for name in names:
            if match_key(name):
                a = resolve_area(session, name, kind)
                wanted[a.id] = a
    for tag in session.exec(select(HealthEntryTag).where(HealthEntryTag.entry_id == entry.id)).all():
        if tag.area_id not in wanted:
            session.delete(tag)
    have = {t.area_id for t in session.exec(select(HealthEntryTag).where(HealthEntryTag.entry_id == entry.id)).all()}
    for area_id in wanted:
        if area_id not in have:
            session.add(HealthEntryTag(entry_id=entry.id, area_id=area_id))
    session.commit()


def approve_area(session: Session, area_id: int) -> None:
    area = session.get(HealthArea, area_id)
    if area is not None:
        area.approved = True
        session.add(area)
        session.commit()


def rename_area(session: Session, area_id: int, new_name: str) -> None:
    area = session.get(HealthArea, area_id)
    key = match_key(new_name)
    if area is None or not key:
        raise AreaError(Problem("Not renamed.", "The new name is empty or the area no longer exists.",
                                "Type a name and try again."))
    clash = session.exec(select(HealthArea).where(HealthArea.kind == area.kind, HealthArea.match_key == key,
                                                  HealthArea.id != area.id)).first()
    if clash is not None:
        raise AreaError(Problem("Not renamed.", f"There is already an area called {clash.name}.",
                                "Use Merge to combine the two, or choose another name."))
    area.name, area.match_key = new_name.strip(), key
    session.add(area)
    session.commit()


def merge_areas(session: Session, source_id: int, target_id: int) -> None:
    if source_id == target_id:
        return
    source = session.get(HealthArea, source_id)
    if source is None or session.get(HealthArea, target_id) is None:
        return
    for tag in session.exec(select(HealthEntryTag).where(HealthEntryTag.area_id == source_id)).all():
        exists = session.get(HealthEntryTag, (tag.entry_id, target_id))
        session.delete(tag)
        if exists is None:
            session.add(HealthEntryTag(entry_id=tag.entry_id, area_id=target_id))
    source.merged_into_id, source.approved = target_id, True
    session.add(source)
    session.commit()


def names_for_prompt(session: Session, kind: str) -> list[str]:
    rows = session.exec(select(HealthArea).where(HealthArea.kind == kind, HealthArea.merged_into_id.is_(None),
                                                 HealthArea.approved.is_(True)).order_by(HealthArea.name)).all()
    return [a.name for a in rows]
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_health_people_areas.py tests/test_health_models.py tests/test_db.py -q`
Also run the migration chain once against a scratch database (it must reach one head and apply cleanly):

```bash
DATABASE_PATH=/tmp/claude-1000/health-migration-check.db python3 -m alembic upgrade head && python3 -m alembic heads
```
Expected: tests PASS; `alembic heads` prints exactly `b2e5f9c83d04 (head)`. (Use the session scratchpad directory instead of `/tmp/claude-1000/...` if it differs.)

- [ ] **Step 5: Checkpoint**

Commit message when ordered: `Health data: people, areas/conditions, entries, measurements (+ migrations)`.

---

### Task 8: Reading a health paper — extraction, structured storage, wiki, "needs checking"

**Files:**
- Create: `app/domains/health/extraction.py`, `app/domains/health/store.py`
- Modify: `app/domains/health/handler.py`
- Test: `tests/test_health_extraction.py`, `tests/test_health_handler.py`

**Interfaces:**
- Consumes: Task 7 services; `ingest_into_wiki`; `VISION_EXTRACTION`, `get_gateway`; `build_content_block`, `is_model_readable`; `strip_json_fences`.
- Produces:
  - `ExtractedMeasurement`, `ExtractedHealth` dataclasses; `HealthExtractionError`
  - `extract_health_document(file_path, category, *, existing_areas=(), existing_conditions=(), gateway=None) -> ExtractedHealth`
  - `store.parse_amount(text) -> Optional[float]`; `store.measurement_needs_check(m) -> bool`
  - `store.apply_extraction(session, document, extracted) -> HealthEntry`; `store.sync_entry_from_fields(session, document) -> Optional[HealthEntry]`; `store.purge_derived(session, document)`; `store.entry_for(session, document_id) -> Optional[HealthEntry]`; `store.wiki_context(extracted) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_health_extraction.py`:

```python
import json

import pytest

from app.domains.health.extraction import HealthExtractionError, extract_health_document
from app.domains.health.store import measurement_needs_check, parse_amount
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result

REPLY = {
    "person_name": "Maria Santos Almeida", "document_date": "2026-03-12", "provider": "Laboratório Norte",
    "provider_phone": "+351 22 000 0000", "provider_address": "Rua A 1, Porto", "record_kind": "test",
    "areas": ["Heart and circulation"], "conditions": ["High cholesterol"],
    "summary": "Annual blood panel; LDL above range.",
    "amount_billed": 45.5, "amount_reimbursed": None, "amount_owed": None,
    "appointment_date": None, "appointment_time": None,
    "measurements": [
        {"test": "LDL cholesterol", "value": 162, "value_text": None, "unit": "mg/dL", "ref_low": None,
         "ref_high": 100, "measured_on": "2026-03-12", "uncertain": False, "source_text": "LDL 162 mg/dL (<100)"},
        {"test": "Glucose", "value": None, "value_text": "illegible", "unit": None, "ref_low": 70, "ref_high": 99,
         "measured_on": "2026-03-12", "uncertain": True, "source_text": "Glucose ??"},
    ],
    "full_text": "LABORATÓRIO NORTE ... LDL 162 mg/dL ...",
}


def _png(tmp_path):
    p = tmp_path / "lab.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nx")
    return str(p)


@pytest.mark.asyncio
async def test_the_reply_is_parsed_into_typed_values(tmp_path):
    fake = FakeGateway([gateway_text_result(json.dumps(REPLY))])
    got = await extract_health_document(_png(tmp_path), "lab_result", existing_areas=["Eyes"],
                                        existing_conditions=["Stroke"], gateway=fake)
    assert got.person_name == "Maria Santos Almeida" and got.document_date.isoformat() == "2026-03-12"
    assert got.areas == ["Heart and circulation"] and got.conditions == ["High cholesterol"]
    ldl, glu = got.measurements
    assert (ldl.value, ldl.unit, ldl.ref_high) == (162.0, "mg/dL", 100.0)
    assert glu.value is None and glu.uncertain is True
    req = fake.requests[0]
    assert req["workload_type"] == "vision_extraction" and req["attachments"]
    assert "Eyes" in req["system"] and "Stroke" in req["system"]               # reuse the existing names


@pytest.mark.asyncio
async def test_a_bad_reply_raises_a_plain_error(tmp_path):
    fake = FakeGateway([gateway_text_result("not json")])
    with pytest.raises(HealthExtractionError):
        await extract_health_document(_png(tmp_path), "lab_result", gateway=fake)


@pytest.mark.asyncio
async def test_a_file_the_model_cannot_read_is_refused_before_any_call(tmp_path):
    video = tmp_path / "x.mp4"
    video.write_bytes(b"x")
    fake = FakeGateway([])
    with pytest.raises(HealthExtractionError):
        await extract_health_document(str(video), "lab_result", gateway=fake)
    assert fake.requests == []


def test_amounts_accept_portuguese_and_english_formats():
    assert parse_amount("1.234,56") == 1234.56
    assert parse_amount("45,5 €") == 45.5
    assert parse_amount("1234.56") == 1234.56
    assert parse_amount("") is None and parse_amount(None) is None and parse_amount("abc") is None


def test_a_measurement_needs_checking_when_unsure_unreadable_or_without_a_unit():
    from app.domains.health.extraction import ExtractedMeasurement as M
    ok = M("LDL", 162.0, None, "mg/dL", None, 100.0, None, False, "")
    assert not measurement_needs_check(ok)
    assert measurement_needs_check(M("LDL", 162.0, None, "mg/dL", None, 100.0, None, True, ""))
    assert measurement_needs_check(M("Glucose", None, "illegible", None, 70.0, 99.0, None, False, ""))
    assert measurement_needs_check(M("LDL", 162.0, None, None, None, 100.0, None, False, ""))
```

`tests/test_health_handler.py`:

```python
"""Filing a health paper end to end through the real ingestion core: privacy scope High, extraction,
tables, wiki, status. Wire = the real GatewayClient over a MockTransport."""

import json

import pytest
from sqlmodel import select

from app.domains.health import store
from app.llm_gateway import set_gateway_provider
from app.models.document import DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.health import HealthEntry, HealthMeasurement
from app.models.wiki import WikiClaim
from app.services.ingestion import Classification, IncomingFile, ingest
from tests import privacy_world as pw
from tests.fakes.fake_gateway import gateway_text_result
from tests.test_health_extraction import REPLY

WIKI_REPLY = {"pages": [{"title": "Maria: cholesterol", "summary": "LDL above range in March 2026",
                         "facts": {"ldl_2026_03": "162 mg/dL"}}]}


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")
    yield
    set_gateway_provider(None)


async def _file(session, wire, content=b"\x89PNG\r\n\x1a\nlab", name="lab.png", reply=REPLY):
    wire.script = [gateway_text_result(json.dumps(reply)), gateway_text_result(json.dumps(WIKI_REPLY))]
    set_gateway_provider(lambda: wire.client())
    return await ingest(session, IncomingFile(name, content, DocumentSource.MANUAL, "test"),
                        Classification(Domain.HEALTH, "lab_result", {}))


@pytest.mark.asyncio
async def test_a_lab_result_is_read_stored_tagged_and_in_the_wiki_all_at_high(session):
    pw.set_levels(session, on=False)                    # Health must not depend on the switch
    wire = pw.Wire()
    result = await _file(session, wire)
    doc = result.document
    assert doc.status == DocumentStatus.PROCESSED and doc.sensitivity_tier == "house_high"
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_high", "house_high"]    # extraction, wiki

    entry = store.entry_for(session, doc.id)
    assert entry.provider == "Laboratório Norte" and entry.year == 2026 and entry.kind == "test"
    assert entry.person_id is not None and entry.amount_billed == 45.5
    ms = session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)).all()
    assert {m.test for m in ms} == {"LDL cholesterol", "Glucose"}
    ldl = next(m for m in ms if m.test_key == "ldl cholesterol")
    assert ldl.out_of_range is True and ldl.needs_check is False
    assert next(m for m in ms if m.test == "Glucose").needs_check is True
    assert entry.needs_check is True                                              # because one value is doubtful
    assert session.exec(select(WikiClaim)).all()                                  # something reached the wiki


@pytest.mark.asyncio
async def test_what_a_person_typed_is_not_overwritten_by_the_reading(session):
    wire = pw.Wire()
    wire.script = [gateway_text_result(json.dumps(REPLY)), gateway_text_result(json.dumps(WIKI_REPLY))]
    set_gateway_provider(lambda: wire.client())
    result = await ingest(session, IncomingFile("lab.png", b"\x89PNG\r\n\x1a\nlab2", DocumentSource.MANUAL, "test"),
                          Classification(Domain.HEALTH, "lab_result", {"provider": "Dr. Costa"}))
    assert store.entry_for(session, result.document.id).provider == "Dr. Costa"


@pytest.mark.asyncio
async def test_a_failed_reading_leaves_the_paper_needing_attention_with_a_plain_reason(session):
    wire = pw.Wire()
    wire.script = [gateway_text_result("not json")]
    set_gateway_provider(lambda: wire.client())
    result = await ingest(session, IncomingFile("lab.png", b"\x89PNG\r\n\x1a\nbad", DocumentSource.MANUAL, "test"),
                          Classification(Domain.HEALTH, "lab_result", {}))
    assert result.document.status == DocumentStatus.NEEDS_ATTENTION
    assert "Health processing failed" in result.document.failure_reason


@pytest.mark.asyncio
async def test_editing_fields_updates_the_entry_columns(session):
    from app.services.ingestion import update_document_fields
    wire = pw.Wire()
    result = await _file(session, wire, content=b"\x89PNG\r\n\x1a\nlab3")
    doc = result.document
    wire.script = [gateway_text_result(json.dumps(WIKI_REPLY))]
    await update_document_fields(session, doc, {"person": "Maria Santos Almeida", "provider": "Clínica Sul",
                                                "document_date": "2025-01-05", "record_kind": "check_up"})
    entry = store.entry_for(session, doc.id)
    assert (entry.provider, entry.year, entry.kind) == ("Clínica Sul", 2025, "check_up")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_health_extraction.py tests/test_health_handler.py -q`
Expected: FAIL (`ModuleNotFoundError: app.domains.health.extraction`)

- [ ] **Step 3: Implement**

`app/domains/health/extraction.py`:

```python
"""Reading a health paper (vision model, always at High: the caller's privacy scope carries the level).

Returns what the paper says, structured: whose it is, when, who issued it, what kind of event, which areas
and conditions it touches, a short summary, amounts, an appointment if there is one, and EVERY measurement
as a number with its unit and reference range. Nothing here decides what leaves the Hub."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from app.domains.health.categories import RECORD_KINDS
from app.llm_gateway import VISION_EXTRACTION, get_gateway
from app.services.document_input import build_content_block, is_model_readable
from app.services.json_utils import strip_json_fences


class HealthExtractionError(Exception):
    pass


@dataclass
class ExtractedMeasurement:
    test: str
    value: Optional[float]
    value_text: Optional[str]
    unit: Optional[str]
    ref_low: Optional[float]
    ref_high: Optional[float]
    measured_on: Optional[date]
    uncertain: bool
    source_text: str


@dataclass
class ExtractedHealth:
    person_name: Optional[str] = None
    document_date: Optional[date] = None
    provider: Optional[str] = None
    provider_phone: Optional[str] = None
    provider_address: Optional[str] = None
    record_kind: Optional[str] = None
    areas: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    summary: str = ""
    amount_billed: Optional[float] = None
    amount_reimbursed: Optional[float] = None
    amount_owed: Optional[float] = None
    appointment_date: Optional[date] = None
    appointment_time: Optional[str] = None
    measurements: list[ExtractedMeasurement] = field(default_factory=list)
    full_text: str = ""


_KINDS = ", ".join(v for v, _ in RECORD_KINDS)

_SYSTEM = f"""You read a family's medical paper (Portuguese or English) and return what it says as JSON.
Be faithful: never invent a value, a date or a name. Use null when the paper does not say.

Fields:
- person_name: the patient the paper is about (full name as printed).
- document_date: the date printed on the paper, YYYY-MM-DD.
- provider: the doctor, clinic, lab, hospital, pharmacy or insurer that issued it; provider_phone, provider_address if printed.
- record_kind: one of {_KINDS}.
- areas: body areas/systems it concerns (e.g. Eyes, Skin, Heart and circulation, Digestive system, Teeth).
- conditions: named conditions or diagnoses it concerns (e.g. Stroke, Diabetes). [] if none.
- summary: two sentences, plain words, what this paper is and its main finding.
- amount_billed, amount_reimbursed, amount_owed: euros as numbers, for bills and reimbursement statements.
- appointment_date (YYYY-MM-DD) and appointment_time (HH:MM, 24h): only for an appointment confirmation.
- measurements: EVERY measured value (test, value as a number, unit, reference range low/high, date measured).
  If a value is unclear or you are unsure of it, set uncertain=true. source_text is the line as printed.
- full_text: the paper's text, transcribed.

Respond with ONLY the JSON object."""

_NUM = {"type": ["number", "null"]}
_STR = {"type": ["string", "null"]}
_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "person_name": _STR, "document_date": _STR, "provider": _STR, "provider_phone": _STR,
        "provider_address": _STR, "record_kind": _STR,
        "areas": {"type": "array", "items": {"type": "string"}},
        "conditions": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "amount_billed": _NUM, "amount_reimbursed": _NUM, "amount_owed": _NUM,
        "appointment_date": _STR, "appointment_time": _STR,
        "measurements": {"type": "array", "items": {"type": "object", "required": ["test"], "properties": {
            "test": {"type": "string"}, "value": _NUM, "value_text": _STR, "unit": _STR, "ref_low": _NUM,
            "ref_high": _NUM, "measured_on": _STR, "uncertain": {"type": ["boolean", "null"]},
            "source_text": _STR}}},
        "full_text": _STR,
    },
}


def _date(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _num(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def _names(values) -> list[str]:
    return [t for t in (str(v).strip() for v in (values or [])) if t]


async def extract_health_document(
    file_path: str, category: Optional[str], *, existing_areas: Sequence[str] = (),
    existing_conditions: Sequence[str] = (), gateway=None,
) -> ExtractedHealth:
    if not is_model_readable(file_path):
        raise HealthExtractionError("This file type can't be read automatically (only PDFs and photos).")
    system = _SYSTEM
    if existing_areas:
        system += "\n\nExisting areas (reuse a name exactly when it fits): " + "; ".join(existing_areas)
    if existing_conditions:
        system += "\nExisting conditions (reuse a name exactly when it fits): " + "; ".join(existing_conditions)
    gw = gateway or get_gateway()
    result = await gw.run(
        VISION_EXTRACTION, system, f"Paper type: {category or 'unknown'}. Read it as JSON.",
        attachments=[build_content_block(file_path)], response_schema=_SCHEMA, max_tokens=12000,
    )
    try:
        data = json.loads(strip_json_fences(result.text))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        kinds = {v for v, _ in RECORD_KINDS}
        measurements = [
            ExtractedMeasurement(
                test=str(m["test"]).strip(), value=_num(m.get("value")), value_text=_text(m.get("value_text")),
                unit=_text(m.get("unit")), ref_low=_num(m.get("ref_low")), ref_high=_num(m.get("ref_high")),
                measured_on=_date(m.get("measured_on")), uncertain=bool(m.get("uncertain")),
                source_text=_text(m.get("source_text")) or "")
            for m in data.get("measurements") or [] if str(m.get("test") or "").strip()
        ]
        kind = _text(data.get("record_kind"))
        return ExtractedHealth(
            person_name=_text(data.get("person_name")), document_date=_date(data.get("document_date")),
            provider=_text(data.get("provider")), provider_phone=_text(data.get("provider_phone")),
            provider_address=_text(data.get("provider_address")), record_kind=kind if kind in kinds else None,
            areas=_names(data.get("areas")), conditions=_names(data.get("conditions")),
            summary=_text(data.get("summary")) or "", amount_billed=_num(data.get("amount_billed")),
            amount_reimbursed=_num(data.get("amount_reimbursed")), amount_owed=_num(data.get("amount_owed")),
            appointment_date=_date(data.get("appointment_date")), appointment_time=_text(data.get("appointment_time")),
            measurements=measurements, full_text=_text(data.get("full_text")) or "")
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HealthExtractionError(f"Could not parse the reading of the paper: {exc}") from exc
```

`app/domains/health/store.py`:

```python
"""Writing what was read from a health paper into Health's tables, and taking it back out."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from sqlmodel import Session, select

from app.domains.fields import dump_fields, load_fields
from app.domains.health import areas as areas_svc
from app.domains.health import people as people_svc
from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.domains.health.text import match_key
from app.models.document import Document
from app.models.health import HealthEntry, HealthEntryTag, HealthMeasurement


def parse_amount(text) -> Optional[float]:
    """'1.234,56' -> 1234.56; '45,5 €' -> 45.5; '1234.56' -> 1234.56; anything else -> None."""
    if text is None:
        return None
    raw = re.sub(r"[^\d,.\-]", "", str(text))
    if not re.search(r"\d", raw):
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def measurement_needs_check(m: ExtractedMeasurement) -> bool:
    """Doubtful: the reader was unsure, the number is missing, or a number has no unit."""
    return bool(m.uncertain or m.value is None or not (m.unit or "").strip())


def _out_of_range(m: ExtractedMeasurement) -> Optional[bool]:
    if m.value is None or (m.ref_low is None and m.ref_high is None):
        return None
    return bool((m.ref_low is not None and m.value < m.ref_low) or (m.ref_high is not None and m.value > m.ref_high))


def entry_for(session: Session, document_id: int) -> Optional[HealthEntry]:
    return session.exec(select(HealthEntry).where(HealthEntry.document_id == document_id)).first()


def _fill(fields: dict[str, str], key: str, value) -> None:
    if value not in (None, "") and not fields.get(key):
        fields[key] = value.isoformat() if isinstance(value, date) else str(value)


def sync_entry_from_fields(session: Session, document: Document, *, full_text: Optional[str] = None) -> HealthEntry:
    """(Re)build the entry's columns from the document's fields, the single source of what a person sees and edits."""
    fields = load_fields(document)
    entry = entry_for(session, document.id) or HealthEntry(document_id=document.id)
    person = people_svc.find_or_create_person(session, fields.get("person"))
    entry.person_id = person.id if person else None
    try:
        entry.entry_date = date.fromisoformat(fields["document_date"]) if fields.get("document_date") else None
    except ValueError:
        entry.entry_date = None
    entry.year = entry.entry_date.year if entry.entry_date else None
    entry.kind = fields.get("record_kind") or None
    entry.provider = fields.get("provider") or None
    entry.summary = fields.get("summary") or None
    entry.amount_billed = parse_amount(fields.get("amount_billed"))
    entry.amount_reimbursed = parse_amount(fields.get("amount_reimbursed"))
    entry.amount_owed = parse_amount(fields.get("amount_owed"))
    if full_text is not None:
        entry.full_text = full_text
    entry.updated_at = datetime.utcnow()
    session.add(entry)
    session.commit()
    session.refresh(entry)
    for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)).all():
        m.person_id = entry.person_id
        session.add(m)
    entry.needs_check = bool(entry.person_id is None or entry.entry_date is None or any(
        m.needs_check for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id))))
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def apply_extraction(session: Session, document: Document, extracted: ExtractedHealth) -> HealthEntry:
    fields = load_fields(document)               # what a person typed stays; the reading only fills blanks
    _fill(fields, "person", extracted.person_name)
    _fill(fields, "document_date", extracted.document_date)
    _fill(fields, "provider", extracted.provider)
    _fill(fields, "provider_phone", extracted.provider_phone)
    _fill(fields, "provider_address", extracted.provider_address)
    _fill(fields, "record_kind", extracted.record_kind)
    _fill(fields, "summary", extracted.summary)
    _fill(fields, "appointment_date", extracted.appointment_date)
    _fill(fields, "appointment_time", extracted.appointment_time)
    for key, value in (("amount_billed", extracted.amount_billed), ("amount_reimbursed", extracted.amount_reimbursed),
                       ("amount_owed", extracted.amount_owed)):
        _fill(fields, key, value)
    document.fields_json = dump_fields(fields)
    session.add(document)
    session.commit()

    entry = sync_entry_from_fields(session, document, full_text=extracted.full_text)
    for old in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)).all():
        session.delete(old)
    for m in extracted.measurements:
        session.add(HealthMeasurement(
            entry_id=entry.id, person_id=entry.person_id, measured_on=m.measured_on or entry.entry_date,
            test=m.test, test_key=match_key(m.test), value=m.value, value_text=m.value_text, unit=m.unit,
            ref_low=m.ref_low, ref_high=m.ref_high, out_of_range=_out_of_range(m),
            needs_check=measurement_needs_check(m), source_text=m.source_text or None))
    session.commit()
    areas_svc.set_tags(session, entry, extracted.areas, extracted.conditions)
    return sync_entry_from_fields(session, document)


def wiki_context(extracted: ExtractedHealth) -> str:
    lines = [f"Person: {extracted.person_name or 'unknown'}",
             f"Date: {extracted.document_date or 'unknown'}",
             f"Provider: {extracted.provider or 'unknown'}",
             f"Areas: {', '.join(extracted.areas) or '-'}; conditions: {', '.join(extracted.conditions) or '-'}",
             f"Summary: {extracted.summary}"]
    for m in extracted.measurements:
        value = m.value if m.value is not None else (m.value_text or "?")
        lines.append(f"- {m.test}: {value} {m.unit or ''} (reference {m.ref_low}–{m.ref_high})")
    return "\n".join(lines)


def purge_derived(session: Session, document: Document) -> None:
    """Take back everything read from this paper: measurements, tags, the entry. (The wiki claims are
    withdrawn by the ingestion core; the calendar entry by Task 11.)"""
    entry = entry_for(session, document.id)
    if entry is None:
        return
    for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)).all():
        session.delete(m)
    for t in session.exec(select(HealthEntryTag).where(HealthEntryTag.entry_id == entry.id)).all():
        session.delete(t)
    session.delete(entry)
    session.commit()
```

`app/domains/health/handler.py` (replace):

```python
"""Health domain handler: read the paper, store what it says, put the standing facts in the wiki."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from app.domains.base import DomainHandler
from app.domains.health import areas as areas_svc
from app.domains.health import store
from app.domains.health.extraction import extract_health_document
from app.models.document import Document, DocumentStatus
from app.models.wiki import WikiOperation
from app.services.wiki_engine import ingest_into_wiki


class HealthHandler(DomainHandler):
    """Runs inside the privacy scope of the paper's type (always High): extraction and the wiki
    assessment both go out at house_high only."""

    async def process(self, session: Session, document: Document) -> Document:
        extracted = await extract_health_document(
            document.file_path, document.category,
            existing_areas=areas_svc.names_for_prompt(session, "area"),
            existing_conditions=areas_svc.names_for_prompt(session, "condition"))
        store.apply_extraction(session, document, extracted)
        await ingest_into_wiki(session, document, context=store.wiki_context(extracted),
                               operation=WikiOperation.INGEST)
        document.status = DocumentStatus.PROCESSED
        document.failure_reason = None
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    async def on_fields_changed(self, session: Session, document: Document, previous_fields: dict[str, str]) -> None:
        store.sync_entry_from_fields(session, document)
        await ingest_into_wiki(session, document, operation=WikiOperation.EDIT)

    async def withdraw(self, session: Session, document: Document) -> None:
        store.purge_derived(session, document)
```

Note for the executor: `on_fields_changed` is called by `update_document_fields` OUTSIDE `privacy_scope`; it calls `ingest_into_wiki` without `context`, so it makes no model call (claims come from fields only). Keep it that way, and add a test line in `test_health_handler.py::test_editing_fields...` asserting `wire.parsed()` gained no new request.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_health_extraction.py tests/test_health_handler.py tests/test_health_domain.py tests/test_wiki_engine.py -q`
Expected: PASS. In `test_editing_fields_updates_the_entry_columns` the scripted `WIKI_REPLY` for the edit is never consumed (no model call): delete that script line and the unused import once the assertion on "no new request" is in place.

- [ ] **Step 5: Checkpoint**

Commit message when ordered: `Health: read papers into entries, measurements, tags and wiki, all at High`.

---

### Task 9: Ask tools for Health — find records, measurements, spending

**Files:**
- Create: `app/domains/health/ask.py`
- Modify: `app/domains/health/spec.py` (`ask_tools=HEALTH_TOOLS`)
- Test: `tests/test_health_ask.py`; extend the tripwire in `tests/test_ask_private.py`

**Interfaces:**
- Consumes: `AskTool`, `ToolOutput`, `Citable`; `settled_entries`; `reach_for`; store/areas/people.
- Produces: tools `health_find_records`, `health_measurements`, `health_spending` (all `tier=Tier.HIGH`, even with no matches: calling one IS touching Health).

- [ ] **Step 1: Write the failing test**

`tests/test_health_ask.py`:

```python
import json
from datetime import date

import pytest

from app.domains.health import areas
from app.domains.health.ask import HEALTH_TOOLS
from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.domains.health.store import apply_extraction
from app.models.domain import Domain
from app.privacy.scope import reachable_scope
from app.privacy.tiers import Tier
from tests import privacy_world as pw
from tests.knowledge_factories import make_document

TOOLS = {t.name: t for t in HEALTH_TOOLS}


def _paper(session, person, when, test_value, billed=None, reimbursed=None, areas_=("Heart and circulation",), name="a.pdf"):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result", filename=name)
    ex = ExtractedHealth(person_name=person, document_date=when, provider="Lab Norte", record_kind="test",
                         areas=list(areas_), conditions=[], summary=f"{person} panel",
                         amount_billed=billed, amount_reimbursed=reimbursed,
                         measurements=[ExtractedMeasurement("LDL cholesterol", test_value, None, "mg/dL", None, 100.0,
                                                            when, False, "LDL")])
    apply_extraction(session, doc, ex)
    return doc


@pytest.fixture()
def world(session):
    pw.set_levels(session, on=False)
    a = _paper(session, "Maria Santos Almeida", date(2024, 3, 1), 150.0, 40.0, 30.0, name="a.pdf")
    b = _paper(session, "Maria Santos Almeida", date(2026, 3, 12), 162.0, 45.5, 0.0, name="b.pdf")
    c = _paper(session, "Pedro Santos Almeida", date(2026, 5, 2), 110.0, 20.0, 20.0, ("Eyes",), name="c.pdf")
    return a, b, c


def run(session, name, **args):
    with reachable_scope(session):
        return TOOLS[name].run(session, args)


def test_every_health_tool_is_prefixed_and_reports_high_even_with_no_match(session):
    for tool in HEALTH_TOOLS:
        assert tool.name.startswith("health_")
    out = run(session, "health_find_records", query="nothing here")
    assert out.tier == Tier.HIGH and "0" in out.text


def test_find_records_filters_by_person_year_and_area_and_cites_the_papers(session, world):
    a, b, c = world
    out = run(session, "health_find_records", person="Maria", year=2026)
    assert out.tier == Tier.HIGH and f"doc:{b.id}" in out.text and f"doc:{a.id}" not in out.text
    assert [c_.ref for c_ in out.citables] == [f"doc:{b.id}"]
    out = run(session, "health_find_records", area="Eyes")
    assert f"doc:{c.id}" in out.text and f"doc:{b.id}" not in out.text


def test_measurements_come_back_oldest_first_with_range_flags(session, world):
    out = run(session, "health_measurements", person="Maria", test="ldl")
    lines = [l for l in out.text.splitlines() if "LDL" in l]
    assert len(lines) == 2 and lines[0].startswith("2024-03-01") and lines[1].startswith("2026-03-12")
    assert "above range" in lines[1] and out.tier == Tier.HIGH


def test_spending_sums_per_person_and_year(session, world):
    out = run(session, "health_spending", person="Maria", year=2026)
    assert "45.50" in out.text and "reimbursed 0.00" in out.text.replace("  ", " ")
    both = run(session, "health_spending", year=2026)
    assert "Maria Santos Almeida" in both.text and "Pedro Santos Almeida" in both.text


def test_the_tools_see_nothing_outside_health(session, world):
    other = make_document(session, domain=Domain.HOUSE, category="house_appliance", fields={"item_name": "Boiler"})
    assert "Boiler" not in run(session, "health_find_records", query="boiler").text
```

Add to the tripwire in `tests/test_ask_private.py` the real PNG case and health tools: create the file under `tmp_path`, set `health.file_path`, call `read_document_file` and assert `tier == Tier.HIGH`; extend `args` with `"health_find_records": {"query": "Maria"}`, `"health_measurements": {}`, `"health_spending": {}` and assert each returns `tier == Tier.HIGH`.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_health_ask.py -q`
Expected: FAIL (`ModuleNotFoundError: app.domains.health.ask`)

- [ ] **Step 3: Implement `app/domains/health/ask.py`**

```python
"""Health's contribution to Ask. Every tool reports tier=HIGH even when nothing matches: calling one IS
touching Health, so the engine raises the run to High before the next model call (decision D18)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.domains.health import areas as areas_svc
from app.domains.health.categories import KIND_LABELS
from app.domains.health.text import match_key
from app.models.document import Document
from app.models.domain import Domain
from app.models.health import HealthEntry, HealthMeasurement, HealthPerson
from app.privacy.reach import reach_for
from app.privacy.tiers import Tier
from app.services.ask.contracts import AskTool, ToolOutput
from app.services.ask.refs import citable_for_document
from app.services.ask.tools import settled_entries

_LIMIT = 40


def _readable_entries(session: Session) -> list[HealthEntry]:
    reach = reach_for(session)
    ids = {e.document.id for e in settled_entries(session, Domain.HEALTH, reach=reach) if e.document is not None}
    if not ids:
        return []
    return list(session.exec(select(HealthEntry).where(HealthEntry.document_id.in_(ids))).all())


def _people(session: Session) -> dict[int, HealthPerson]:
    return {p.id: p for p in session.exec(select(HealthPerson)).all()}


def _person_ok(person: Optional[HealthPerson], wanted: Optional[str]) -> bool:
    return not wanted or (person is not None and match_key(wanted) in person.match_key)


def _tags(session: Session, entry: HealthEntry):
    return areas_svc.tags_of(session, entry.id)


def _output(text: str, count: int, citables=()) -> ToolOutput:
    return ToolOutput(text=text, citables=list(citables), used_raw_sources=True, tier=Tier.HIGH,
                      opened=f"{count} private Health record{'s' if count != 1 else ''}")


def _find_records(session: Session, args: dict) -> ToolOutput:
    people = _people(session)
    query = match_key(args.get("query") or "")
    area, condition = match_key(args.get("area") or ""), match_key(args.get("condition") or "")
    limit = max(1, min(int(args.get("limit", _LIMIT)), 60))
    rows = []
    for e in _readable_entries(session):
        if not _person_ok(people.get(e.person_id), args.get("person")):
            continue
        if args.get("year") and e.year != int(args["year"]):
            continue
        if args.get("kind") and e.kind != args["kind"]:
            continue
        tags = _tags(session, e)
        if area and not any(t.kind == "area" and area in t.match_key for t in tags):
            continue
        if condition and not any(t.kind == "condition" and condition in t.match_key for t in tags):
            continue
        doc = session.get(Document, e.document_id)
        hay = match_key(" ".join([doc.filename, e.provider or "", e.summary or "", e.full_text or "",
                                  " ".join(t.name for t in tags)]))
        if query and not all(tok in hay for tok in query.split()):
            continue
        rows.append((e, doc, tags))
    rows.sort(key=lambda r: (r[0].entry_date or date.min), reverse=True)
    shown = rows[:limit]
    lines = [f"{len(rows)} matching private Health records (showing {len(shown)}):"]
    for e, doc, tags in shown:
        person = people.get(e.person_id)
        lines.append(" | ".join([
            f"doc:{doc.id}", str(e.entry_date or "undated"), person.name if person else "unknown person",
            KIND_LABELS.get(e.kind or "", e.kind or "-"), e.provider or "-",
            "areas: " + (", ".join(t.name for t in tags if t.kind == "area") or "-"),
            "conditions: " + (", ".join(t.name for t in tags if t.kind == "condition") or "-"),
            (e.summary or "no summary")]))
    return _output("\n".join(lines), len(shown), [citable_for_document(d) for _, d, _ in shown])


def _measurements(session: Session, args: dict) -> ToolOutput:
    people = _people(session)
    test = match_key(args.get("test") or "")
    allowed = {e.id: e for e in _readable_entries(session)}
    start = date.fromisoformat(args["date_from"]) if args.get("date_from") else None
    end = date.fromisoformat(args["date_to"]) if args.get("date_to") else None
    rows = []
    for m in session.exec(select(HealthMeasurement)).all():
        e = allowed.get(m.entry_id)
        if e is None or not _person_ok(people.get(m.person_id), args.get("person")):
            continue
        if test and test not in m.test_key:
            continue
        when = m.measured_on or e.entry_date
        if (start and (when is None or when < start)) or (end and (when is None or when > end)):
            continue
        rows.append((when or date.min, m, e))
    rows.sort(key=lambda r: r[0])
    lines = [f"{len(rows)} measurements, oldest first:"]
    docs = []
    for when, m, e in rows[:200]:
        person = people.get(m.person_id)
        value = f"{m.value:g} {m.unit or ''}".strip() if m.value is not None else (m.value_text or "?")
        rng = f" (reference {m.ref_low if m.ref_low is not None else ''}–{m.ref_high if m.ref_high is not None else ''})" \
            if (m.ref_low is not None or m.ref_high is not None) else ""
        flag = ""
        if m.out_of_range is True:
            flag = " — above range" if m.ref_high is not None and m.value is not None and m.value > m.ref_high else " — below range"
        check = " [doubtful reading]" if m.needs_check else ""
        lines.append(f"{when if when != date.min else 'undated'} | {person.name if person else 'unknown'} | "
                     f"{m.test} | {value}{rng}{flag}{check} | doc:{e.document_id}")
        docs.append(e.document_id)
    cites = [citable_for_document(d) for d in session.exec(select(Document).where(Document.id.in_(set(docs)))).all()] if docs else []
    return _output("\n".join(lines), len(rows[:200]), cites)


def _spending(session: Session, args: dict) -> ToolOutput:
    people = _people(session)
    totals: dict[tuple[str, int], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    for e in _readable_entries(session):
        person = people.get(e.person_id)
        if not _person_ok(person, args.get("person")):
            continue
        if args.get("year") and e.year != int(args["year"]):
            continue
        t = totals[(person.name if person else "unknown", e.year or 0)]
        t[0] += e.amount_billed or 0.0
        t[1] += e.amount_reimbursed or 0.0
        t[2] += e.amount_owed or 0.0
        t[3] += 1
    lines = ["Health spending (EUR) per person and year, from the papers on file:"]
    for (name, year), (billed, back, owed, n) in sorted(totals.items()):
        lines.append(f"{name} | {year or 'undated'} | billed {billed:.2f} | reimbursed {back:.2f} | "
                     f"still owed {owed:.2f} | {n} papers")
    if len(lines) == 1:
        lines.append("(nothing on file)")
    return _output("\n".join(lines), sum(int(t[3]) for t in totals.values()))


_PERSON = {"type": "string", "description": "Part of the person's name."}
_YEAR = {"type": "integer"}

HEALTH_TOOLS = (
    AskTool(
        name="health_find_records",
        description=("PRIVATE. Find the family's health papers by person, year, kind (check_up, follow_up, test, "
                     "procedure, emergency, dental, vaccination, other), body area, condition or words. Returns "
                     "date, person, provider, areas, conditions, summary and doc:ID to cite. Using this makes the "
                     "whole answer private."),
        input_schema={"type": "object", "properties": {
            "person": _PERSON, "year": _YEAR, "kind": {"type": "string"}, "area": {"type": "string"},
            "condition": {"type": "string"}, "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 60}}},
        run=_find_records),
    AskTool(
        name="health_measurements",
        description=("PRIVATE. Measured values (lab values, blood pressure, weight...) oldest first, with unit, "
                     "reference range and whether the reading was doubtful. Filter by person, test name and dates. "
                     "Use for trends. Using this makes the whole answer private."),
        input_schema={"type": "object", "properties": {
            "person": _PERSON, "test": {"type": "string"},
            "date_from": {"type": "string", "description": "YYYY-MM-DD"}, "date_to": {"type": "string", "description": "YYYY-MM-DD"}}},
        run=_measurements),
    AskTool(
        name="health_spending",
        description=("PRIVATE. What health costs: billed, reimbursed and still owed (EUR) per person and year, from "
                     "medical bills and reimbursement statements on file. Using this makes the whole answer private."),
        input_schema={"type": "object", "properties": {"person": _PERSON, "year": _YEAR}},
        run=_spending),
)
```

`app/domains/health/spec.py` — import `from app.domains.health.ask import HEALTH_TOOLS` and add `ask_tools=HEALTH_TOOLS,` to the `DomainSpec(...)` call.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_health_ask.py tests/test_ask_private.py tests/test_ask_tools.py tests/test_ask_contracts.py tests/test_privacy_reach.py -q`
Expected: PASS (the registry-wide tool-name tests accept the three new `health_` names).

- [ ] **Step 5: Checkpoint**

Commit message when ordered: `Ask: health_find_records, health_measurements, health_spending (always High)`.

---

### Task 10: The Health tab — a database-style browser, areas and people pages

**Files:**
- Create: `app/domains/health/browse.py`, `app/routers/health.py`, `app/templates/health/{landing,detail,upload,areas,people}.html`
- Modify: `app/main.py` (import + `app.include_router(health.router)`), `app/domains/health/overview.py`
- Test: `tests/test_health_browse.py`, `tests/test_health_router.py`

**Interfaces:**
- Consumes: Tasks 7–8 (`store`, `areas`, `people`), `build_form_fields`, `ingest`, `update_document_fields`, `describe_document`.
- Produces:
  - `browse.Filters` (`person, area, condition: Optional[int]`, `year: Optional[int]`, `kind: Optional[str]`, `q: str`, `unsorted: bool`, `check: bool`) and `browse.parse_filters(params) -> Filters`
  - `browse.rows(session, f) -> list[Row]` where `Row(document, entry, person, areas, conditions, proposed)`
  - `browse.facets(session) -> Facets` (`people, years, kinds, areas, conditions, proposed_areas, unconfirmed_people, needs_check, unsorted` counts/lists)
  - Routes: `GET /health`, `GET/POST /health/upload`, `GET /health/upload/fields`, `GET /health/documents/{id}`, `POST /health/documents/{id}/fields`, `POST /health/documents/{id}/tags`, `POST /health/documents/{id}/measurements/{mid}/ok`, `GET /health/areas`, `POST /health/areas/{id}/{approve|rename|merge}`, `GET /health/people`, `POST /health/people/{id}/{confirm|merge}`

- [ ] **Step 1: Write the failing tests**

`tests/test_health_browse.py`:

```python
from datetime import date

import pytest

from app.domains.health import areas as areas_svc
from app.domains.health.browse import Filters, facets, parse_filters, rows
from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.domains.health.store import apply_extraction
from app.models.domain import Domain
from tests.knowledge_factories import make_document


def _paper(session, person, when, kind, area_names, conditions=(), doubtful=False, name="x.pdf"):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result", filename=name)
    ex = ExtractedHealth(person_name=person, document_date=when, provider="Lab", record_kind=kind,
                         areas=list(area_names), conditions=list(conditions), summary="s",
                         measurements=[ExtractedMeasurement("LDL", 1.0, None, None if doubtful else "mg/dL", None, None,
                                                            when, False, "")])
    apply_extraction(session, doc, ex)
    return doc


@pytest.fixture()
def world(session):
    a = _paper(session, "Maria Santos Almeida", date(2024, 3, 1), "check_up", ["Heart"], ["Stroke"], name="a.pdf")
    b = _paper(session, "Maria Santos Almeida", date(2026, 3, 12), "test", ["Eyes"], name="b.pdf")
    c = _paper(session, "Pedro Santos Almeida", date(2026, 5, 2), "check_up", [], doubtful=True, name="c.pdf")
    for area in session.exec(__import__("sqlmodel").select(areas_svc.HealthArea)).all():
        if area.name in ("Heart", "Eyes"):
            areas_svc.approve_area(session, area.id)
    return a, b, c


def test_rows_are_newest_first_and_filter_by_every_facet(session, world):
    a, b, c = world
    assert [r.document.id for r in rows(session, Filters())] == [c.id, b.id, a.id]
    maria = facets(session).people[0]
    assert {r.document.id for r in rows(session, Filters(person=maria.id))} == {a.id, b.id}
    assert [r.document.id for r in rows(session, Filters(year=2024))] == [a.id]
    assert [r.document.id for r in rows(session, Filters(kind="test"))] == [b.id]
    eyes = next(x for x in facets(session).areas if x.name == "Eyes")
    assert [r.document.id for r in rows(session, Filters(area=eyes.id))] == [b.id]
    stroke = next(x for x in facets(session).conditions if x.name == "Stroke") if facets(session).conditions else None
    assert stroke is None or [r.document.id for r in rows(session, Filters(condition=stroke.id))] == [a.id]


def test_unsorted_means_no_approved_area_and_check_means_a_doubtful_value(session, world):
    a, b, c = world
    assert [r.document.id for r in rows(session, Filters(unsorted=True))] == [c.id]
    assert [r.document.id for r in rows(session, Filters(check=True))] == [c.id]
    f = facets(session)
    assert f.unsorted == 1 and f.needs_check == 1 and f.unconfirmed_people == 2


def test_proposed_areas_show_on_the_row_but_do_not_count_as_sorting(session):
    doc = _paper(session, "Ana Lima", date(2026, 1, 1), "test", ["Teeth"])
    r = rows(session, Filters())[0]
    assert [x.name for x in r.proposed] == ["Teeth"] and r.areas == []
    assert [x.document.id for x in rows(session, Filters(unsorted=True))] == [doc.id]


def test_free_text_search_looks_at_the_provider_summary_and_file_name(session, world):
    assert len(rows(session, Filters(q="b.pdf"))) == 1


def test_a_paper_that_could_not_be_read_is_still_listed(session):
    from app.models.document import DocumentStatus
    make_document(session, domain=Domain.HEALTH, category="lab_result", status=DocumentStatus.NEEDS_ATTENTION, filename="bad.pdf")
    r = rows(session, Filters())
    assert len(r) == 1 and r[0].entry is None


def test_parse_filters_ignores_garbage():
    f = parse_filters({"person": "x", "year": "2026", "unsorted": "1", "kind": "test", "q": " eyes "})
    assert f.person is None and f.year == 2026 and f.unsorted and f.kind == "test" and f.q == "eyes"
```

`tests/test_health_router.py` (uses the `client` fixture and the same `_paper` helper imported from `tests.test_health_browse`):

```python
from datetime import date

from app.domains.health import areas as areas_svc
from app.models.health import HealthMeasurement
from sqlmodel import select
from tests.test_health_browse import _paper


def test_the_landing_page_lists_papers_with_facets(client, session):
    _paper(session, "Maria Santos Almeida", date(2026, 3, 12), "test", ["Eyes"], ["Glaucoma"], name="eye.pdf")
    html = client.get("/health").text
    assert "eye.pdf" in html and "Maria Santos Almeida" in html and "Eyes" in html and "2026" in html


def test_the_filters_work_through_the_url(client, session):
    _paper(session, "Maria Santos Almeida", date(2026, 3, 12), "test", ["Eyes"], name="eye.pdf")
    _paper(session, "Pedro Santos Almeida", date(2024, 1, 5), "check_up", ["Heart"], name="heart.pdf")
    html = client.get("/health?year=2024").text
    assert "heart.pdf" in html and "eye.pdf" not in html


def test_the_detail_page_shows_each_value_beside_the_original(client, session):
    doc = _paper(session, "Maria Santos Almeida", date(2026, 3, 12), "test", ["Eyes"], name="eye.pdf")
    html = client.get(f"/health/documents/{doc.id}").text
    assert "LDL" in html and f"/static/documents/" in html


def test_a_non_health_document_is_a_404(client, session):
    from app.models.domain import Domain
    from tests.knowledge_factories import make_document
    other = make_document(session, domain=Domain.HOUSE, category="house_appliance")
    assert client.get(f"/health/documents/{other.id}").status_code == 404


def test_marking_a_value_as_checked_clears_the_flag(client, session):
    doc = _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], doubtful=True, name="d.pdf")
    m = session.exec(select(HealthMeasurement)).first()
    assert m.needs_check
    r = client.post(f"/health/documents/{doc.id}/measurements/{m.id}/ok", follow_redirects=False)
    assert r.status_code == 303
    session.refresh(m)
    assert m.needs_check is False


def test_retagging_a_paper(client, session):
    doc = _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], name="d.pdf")
    r = client.post(f"/health/documents/{doc.id}/tags", data={"areas": "Eyes, Skin", "conditions": "Glaucoma"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert {a.name for a in session.exec(select(areas_svc.HealthArea)).all()} == {"Eyes", "Skin", "Glaucoma"}


def test_areas_page_approves_renames_and_merges(client, session):
    a = areas_svc.resolve_area(session, "Digestive", "area")
    b = areas_svc.resolve_area(session, "Digestive system", "area")
    assert "Digestive" in client.get("/health/areas").text
    assert client.post(f"/health/areas/{a.id}/approve", follow_redirects=False).status_code == 303
    assert client.post(f"/health/areas/{a.id}/merge", data={"into": str(b.id)}, follow_redirects=False).status_code == 303
    session.refresh(a)
    assert a.merged_into_id == b.id
    r = client.post(f"/health/areas/{b.id}/rename", data={"name": "  "})
    assert r.status_code == 400 and "Not renamed." in r.text


def test_people_page_confirms_and_merges(client, session):
    _paper(session, "Maria Santos Almeida", date(2026, 3, 12), "test", [], name="a.pdf")
    _paper(session, "Mariah Almeida", date(2026, 3, 13), "test", [], name="b.pdf")
    from app.models.health import HealthPerson
    ids = [p.id for p in session.exec(select(HealthPerson).order_by(HealthPerson.id)).all()]
    assert client.post(f"/health/people/{ids[0]}/confirm", follow_redirects=False).status_code == 303
    assert client.post(f"/health/people/{ids[1]}/merge", data={"into": str(ids[0])}, follow_redirects=False).status_code == 303
    assert len(session.exec(select(HealthPerson)).all()) == 1


def test_upload_files_a_paper_through_the_real_pipeline(client, session, monkeypatch):
    import json
    from app.llm_gateway import set_gateway_provider
    from tests import privacy_world as pw
    from tests.fakes.fake_gateway import gateway_text_result
    from tests.test_health_extraction import REPLY
    from tests.test_health_handler import WIKI_REPLY
    wire = pw.Wire()
    wire.script = [gateway_text_result(json.dumps(REPLY)), gateway_text_result(json.dumps(WIKI_REPLY))]
    set_gateway_provider(lambda: wire.client())
    try:
        r = client.post("/health/upload", data={"category": "lab_result"},
                        files={"file": ("lab.png", b"\x89PNG\r\n\x1a\nzz", "image/png")}, follow_redirects=False)
    finally:
        set_gateway_provider(None)
    assert r.status_code == 303 and r.headers["location"].startswith("/health/documents/")
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_high", "house_high"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_health_browse.py tests/test_health_router.py -q`
Expected: FAIL (`ModuleNotFoundError: app.domains.health.browse`)

- [ ] **Step 3: Implement**

`app/domains/health/browse.py`:

```python
"""What the Health tab shows: one row per health paper, filterable like a database."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from sqlmodel import Session, select

from app.domains.health import areas as areas_svc
from app.domains.health.categories import RECORD_KINDS
from app.domains.health.text import match_key
from app.models.document import Document, DocumentStatus
from app.models.domain import Domain
from app.models.health import HealthArea, HealthEntry, HealthPerson


@dataclass
class Filters:
    person: Optional[int] = None
    area: Optional[int] = None
    condition: Optional[int] = None
    year: Optional[int] = None
    kind: Optional[str] = None
    q: str = ""
    unsorted: bool = False
    check: bool = False


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_filters(params: Mapping[str, str]) -> Filters:
    kinds = {v for v, _ in RECORD_KINDS}
    kind = params.get("kind") or None
    return Filters(
        person=_int(params.get("person")), area=_int(params.get("area")), condition=_int(params.get("condition")),
        year=_int(params.get("year")), kind=kind if kind in kinds else None, q=(params.get("q") or "").strip(),
        unsorted=params.get("unsorted") == "1", check=params.get("check") == "1")


@dataclass
class Row:
    document: Document
    entry: Optional[HealthEntry]
    person: Optional[HealthPerson]
    areas: list[HealthArea] = field(default_factory=list)       # approved areas
    conditions: list[HealthArea] = field(default_factory=list)  # approved conditions
    proposed: list[HealthArea] = field(default_factory=list)    # proposed, not yet approved


@dataclass
class Facets:
    people: list[HealthPerson]
    years: list[int]
    kinds: list[tuple[str, str]]
    areas: list[HealthArea]
    conditions: list[HealthArea]
    proposed_areas: int
    unconfirmed_people: int
    needs_check: int
    unsorted: int


def _all_rows(session: Session) -> list[Row]:
    docs = session.exec(select(Document).where(
        Document.domain == Domain.HEALTH,
        Document.status.in_([DocumentStatus.PROCESSED, DocumentStatus.NEEDS_ATTENTION]))).all()
    entries = {e.document_id: e for e in session.exec(select(HealthEntry)).all()}
    people = {p.id: p for p in session.exec(select(HealthPerson)).all()}
    out = []
    for d in docs:
        e = entries.get(d.id)
        row = Row(d, e, people.get(e.person_id) if e else None)
        if e is not None:
            for t in areas_svc.tags_of(session, e.id):
                if t.merged_into_id is not None:
                    continue
                if not t.approved:
                    row.proposed.append(t)
                elif t.kind == "area":
                    row.areas.append(t)
                else:
                    row.conditions.append(t)
        out.append(row)
    return out


def rows(session: Session, f: Filters) -> list[Row]:
    result = []
    q = match_key(f.q)
    for r in _all_rows(session):
        e = r.entry
        if f.person is not None and (e is None or e.person_id != f.person):
            continue
        if f.year is not None and (e is None or e.year != f.year):
            continue
        if f.kind and (e is None or e.kind != f.kind):
            continue
        if f.area is not None and f.area not in {a.id for a in r.areas}:
            continue
        if f.condition is not None and f.condition not in {a.id for a in r.conditions}:
            continue
        if f.unsorted and r.areas:
            continue
        if f.check and not (e is None or e.needs_check):
            continue
        if q:
            hay = match_key(" ".join([r.document.filename, (e.provider or "") if e else "", (e.summary or "") if e else "",
                                      " ".join(a.name for a in r.areas + r.conditions + r.proposed)]))
            if not all(tok in hay for tok in q.split()):
                continue
        result.append(r)
    result.sort(key=lambda r: ((r.entry.entry_date.toordinal() if r.entry and r.entry.entry_date else 0), r.document.id),
                reverse=True)
    return result


def facets(session: Session) -> Facets:
    allr = _all_rows(session)
    areas = list(session.exec(select(HealthArea).where(HealthArea.kind == "area", HealthArea.approved.is_(True),
                                                       HealthArea.merged_into_id.is_(None)).order_by(HealthArea.name)))
    conds = list(session.exec(select(HealthArea).where(HealthArea.kind == "condition", HealthArea.approved.is_(True),
                                                       HealthArea.merged_into_id.is_(None)).order_by(HealthArea.name)))
    people = list(session.exec(select(HealthPerson).order_by(HealthPerson.name)))
    proposed = session.exec(select(HealthArea).where(HealthArea.approved.is_(False), HealthArea.merged_into_id.is_(None))).all()
    return Facets(
        people=people,
        years=sorted({r.entry.year for r in allr if r.entry and r.entry.year}, reverse=True),
        kinds=list(RECORD_KINDS), areas=areas, conditions=conds, proposed_areas=len(proposed),
        unconfirmed_people=sum(1 for p in people if not p.confirmed),
        needs_check=sum(1 for r in allr if r.entry is None or r.entry.needs_check),
        unsorted=sum(1 for r in allr if not r.areas))
```

`app/domains/health/overview.py` — replace with:

```python
"""Health's card on the cross-domain '/' Overview. Counts only: nothing clinical."""

from datetime import date

from sqlmodel import Session

from app.domains.base import CardLine, DomainCard
from app.domains.health.browse import facets
from app.domains.entries import domain_entries
from app.models.domain import Domain


def health_overview_card(session: Session, today: date) -> DomainCard:
    n = len(domain_entries(session, Domain.HEALTH))
    f = facets(session)
    lines = [CardLine(text=f"{n} record{'s' if n != 1 else ''} on file", url="/health")]
    if f.needs_check:
        lines.append(CardLine(text=f"{f.needs_check} to check", url="/health?check=1", attention=True))
    if f.proposed_areas:
        lines.append(CardLine(text=f"{f.proposed_areas} new area{'s' if f.proposed_areas != 1 else ''} to approve",
                              url="/health/areas", attention=True))
    return DomainCard(label="Health", url="/health", lines=lines)
```

`app/routers/health.py`:

```python
"""Health tab: /health/*. Everyone with Hub access sees everything (Pedro, Q5); privacy is about what the AI
and the outbound channels may read, not about who may look."""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.db import get_session
from app.domains.fields import (
    InvalidClassification, build_form_fields, describe_document, load_fields, media_kind_for,
)
from app.domains.health import areas as areas_svc
from app.domains.health import browse, people as people_svc, store
from app.domains.registry import document_url, get_spec
from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.models.health import HealthMeasurement, HealthPerson
from app.problems import file_notices
from app.services.ingestion import Classification, IncomingFile, ingest, update_document_fields
from app.templating import templates

router = APIRouter(prefix="/health", tags=["health"])
_NON_FIELD_KEYS = {"file", "category"}


def _document_or_404(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.domain != Domain.HEALTH:
        raise HTTPException(status_code=404, detail="Health document not found")
    return document


def _upload_context(session: Session, category: Optional[str], values: dict, errors: dict) -> dict:
    spec = get_spec(Domain.HEALTH)
    known = category if category and any(c.value == category for c in spec.categories) else None
    return {"categories": spec.categories, "category": known, "errors": file_notices(errors),
            "form_fields": build_form_fields(session, spec, known, values, errors) if known else []}


def _detail_context(session: Session, document: Document, values: dict, errors: dict) -> dict:
    spec = get_spec(Domain.HEALTH)
    entry = store.entry_for(session, document.id)
    measurements = list(session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)
                                     .order_by(HealthMeasurement.measured_on, HealthMeasurement.test))) if entry else []
    tags = areas_svc.tags_of(session, entry.id) if entry else []
    media_kind = media_kind_for(document.filename)
    return {"document": document, "description": describe_document(document), "media_kind": media_kind.value,
            "entry": entry, "measurements": measurements,
            "area_names": ", ".join(t.name for t in tags if t.kind == "area"),
            "condition_names": ", ".join(t.name for t in tags if t.kind == "condition"),
            "form_fields": build_form_fields(session, spec, document.category, values, errors, media_kind=media_kind)}


@router.get("")
async def landing(request: Request, session: Session = Depends(get_session)):
    f = browse.parse_filters(request.query_params)
    return templates.TemplateResponse(request, "health/landing.html", {
        "rows": browse.rows(session, f), "f": f, "facets": browse.facets(session)})


@router.get("/upload")
async def upload_form(request: Request, category: Optional[str] = None, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "health/upload.html", _upload_context(session, category, {}, {}))


@router.get("/upload/fields")
async def upload_fields(request: Request, category: Optional[str] = None, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "domains/_field_inputs.html", _upload_context(session, category, {}, {}))


@router.post("/upload")
async def upload(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    category = (form.get("category") or "").strip() or None
    values = {k: v for k, v in form.items() if k not in _NON_FIELD_KEYS and isinstance(v, str)}
    upload_file = form.get("file")
    if upload_file is None or isinstance(upload_file, str) or not upload_file.filename:
        return templates.TemplateResponse(request, "health/upload.html", _upload_context(
            session, category, values, {"file": "No file was chosen to upload"}), status_code=400)
    try:
        result = await ingest(session, IncomingFile(upload_file.filename, await upload_file.read(), DocumentSource.MANUAL,
                                                    getattr(request.state, "user_email", None)),
                              Classification(domain=Domain.HEALTH, category=category, fields=values))
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "health/upload.html",
                                          _upload_context(session, category, values, exc.errors), status_code=400)
    return RedirectResponse(document_url(result.document) or "/health", status_code=303)


@router.get("/documents/{document_id}")
async def document_detail(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    return templates.TemplateResponse(request, "health/detail.html",
                                      _detail_context(session, document, load_fields(document), {}))


@router.post("/documents/{document_id}/fields")
async def update_fields(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    values = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    try:
        await update_document_fields(session, document, values)
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "health/detail.html",
                                          _detail_context(session, document, values, exc.errors), status_code=400)
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)


@router.post("/documents/{document_id}/tags")
async def update_tags(document_id: int, areas: str = Form(""), conditions: str = Form(""),
                      session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    entry = store.entry_for(session, document.id)
    if entry is None:
        raise HTTPException(status_code=409, detail={
            "what": "Not tagged.", "why": "This paper has not been read yet, so it has no record to tag.",
            "next_step": "Wait for it to finish, or fix its problem on the Problems page, then try again."})
    split = lambda raw: [p.strip() for p in raw.split(",") if p.strip()]
    areas_svc.set_tags(session, entry, split(areas), split(conditions))
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)


@router.post("/documents/{document_id}/measurements/{measurement_id}/ok")
async def measurement_ok(document_id: int, measurement_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    m = session.get(HealthMeasurement, measurement_id)
    entry = store.entry_for(session, document.id)
    if m is None or entry is None or m.entry_id != entry.id:
        raise HTTPException(status_code=404, detail="Measurement not found")
    m.needs_check = False
    session.add(m)
    session.commit()
    store.sync_entry_from_fields(session, document)
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)


@router.get("/areas")
async def areas_page(request: Request, session: Session = Depends(get_session)):
    from app.models.health import HealthArea
    rows = list(session.exec(select(HealthArea).where(HealthArea.merged_into_id.is_(None))
                             .order_by(HealthArea.approved, HealthArea.kind, HealthArea.name)))
    return templates.TemplateResponse(request, "health/areas.html", {"areas": rows, "error": None})


@router.post("/areas/{area_id}/approve")
async def area_approve(area_id: int, session: Session = Depends(get_session)):
    areas_svc.approve_area(session, area_id)
    return RedirectResponse("/health/areas", status_code=303)


@router.post("/areas/{area_id}/rename")
async def area_rename(request: Request, area_id: int, name: str = Form(""), session: Session = Depends(get_session)):
    from app.models.health import HealthArea
    try:
        areas_svc.rename_area(session, area_id, name)
    except areas_svc.AreaError as exc:
        rows = list(session.exec(select(HealthArea).where(HealthArea.merged_into_id.is_(None))
                                 .order_by(HealthArea.approved, HealthArea.kind, HealthArea.name)))
        return templates.TemplateResponse(request, "health/areas.html", {"areas": rows, "error": exc.problem}, status_code=400)
    return RedirectResponse("/health/areas", status_code=303)


@router.post("/areas/{area_id}/merge")
async def area_merge(area_id: int, into: int = Form(...), session: Session = Depends(get_session)):
    areas_svc.merge_areas(session, area_id, into)
    return RedirectResponse("/health/areas", status_code=303)


@router.get("/people")
async def people_page(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "health/people.html", {"people": people_svc.all_people(session)})


@router.post("/people/{person_id}/confirm")
async def person_confirm(person_id: int, session: Session = Depends(get_session)):
    people_svc.confirm_person(session, person_id)
    return RedirectResponse("/health/people", status_code=303)


@router.post("/people/{person_id}/merge")
async def person_merge(person_id: int, into: int = Form(...), session: Session = Depends(get_session)):
    people_svc.merge_people(session, person_id, into)
    return RedirectResponse("/health/people", status_code=303)
```

`app/main.py` — add `health` to the `from app.routers import (...)` list and `app.include_router(health.router)` after `documents`.

Templates (`app/templates/health/`). All extend `base.html` and use block `content`.

`landing.html`:

```html
{% extends "base.html" %}
{% block title %}Health{% endblock %}
{% block content %}
<h1>Health</h1>
<p><a href="/health/upload">+ Add a paper</a>
  {% if facets.needs_check %} · <a href="/health?check=1" class="needs-attention">{{ facets.needs_check }} to check</a>{% endif %}
  {% if facets.unsorted %} · <a href="/health?unsorted=1">{{ facets.unsorted }} unsorted</a>{% endif %}
  {% if facets.proposed_areas %} · <a href="/health/areas">{{ facets.proposed_areas }} new area{{ "s" if facets.proposed_areas != 1 }} to approve</a>{% endif %}
  {% if facets.unconfirmed_people %} · <a href="/health/people">{{ facets.unconfirmed_people }} person to confirm</a>{% endif %}</p>
<form method="get" action="/health" class="field" style="display:flex;gap:8px;flex-wrap:wrap;align-items:end;">
  <label>Person <select name="person"><option value="">All</option>
    {% for p in facets.people %}<option value="{{ p.id }}"{% if f.person == p.id %} selected{% endif %}>{{ p.name }}</option>{% endfor %}</select></label>
  <label>Area <select name="area"><option value="">All</option>
    {% for a in facets.areas %}<option value="{{ a.id }}"{% if f.area == a.id %} selected{% endif %}>{{ a.name }}</option>{% endfor %}</select></label>
  <label>Condition <select name="condition"><option value="">All</option>
    {% for a in facets.conditions %}<option value="{{ a.id }}"{% if f.condition == a.id %} selected{% endif %}>{{ a.name }}</option>{% endfor %}</select></label>
  <label>Year <select name="year"><option value="">All</option>
    {% for y in facets.years %}<option value="{{ y }}"{% if f.year == y %} selected{% endif %}>{{ y }}</option>{% endfor %}</select></label>
  <label>Kind <select name="kind"><option value="">All</option>
    {% for value, label in facets.kinds %}<option value="{{ value }}"{% if f.kind == value %} selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
  <label>Search <input type="text" name="q" value="{{ f.q }}"></label>
  <button type="submit">Filter</button> <a href="/health">Clear</a>
</form>
<table class="review-table">
  <thead><tr><th>Date</th><th>Person</th><th>Paper</th><th>Provider</th><th>Kind</th><th>Areas</th><th>Conditions</th></tr></thead>
  <tbody>
  {% for r in rows %}
  <tr>
    <td>{{ r.entry.entry_date if r.entry and r.entry.entry_date else "—" }}</td>
    <td>{{ r.person.name if r.person else "—" }}{% if r.person and not r.person.confirmed %} <small>(new)</small>{% endif %}</td>
    <td><a href="/health/documents/{{ r.document.id }}">{{ r.document.filename }}</a>
      {% if r.entry is none %}<span class="needs-attention">couldn't be read</span>{% elif r.entry.needs_check %}<span class="needs-attention">to check</span>{% endif %}</td>
    <td>{{ r.entry.provider if r.entry and r.entry.provider else "—" }}</td>
    <td>{{ r.entry.kind|humanize_key if r.entry and r.entry.kind else "—" }}</td>
    <td>{% for a in r.areas %}<a href="/health?area={{ a.id }}">{{ a.name }}</a>{% if not loop.last %}, {% endif %}{% endfor %}
      {% for a in r.proposed %}<em title="Proposed, not approved yet">{{ a.name }}?</em> {% endfor %}
      {% if not r.areas %}<small>unsorted</small>{% endif %}</td>
    <td>{% for a in r.conditions %}<a href="/health?condition={{ a.id }}">{{ a.name }}</a>{% if not loop.last %}, {% endif %}{% endfor %}</td>
  </tr>
  {% else %}
  <tr><td colspan="7">No papers match. {% if not facets.people %}Add the first one with “Add a paper”.{% endif %}</td></tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

`upload.html` — copy `insurance/upload.html`, replacing `/insurance` with `/health`, `#insurance-fields` with `#health-fields`, heading "Add a health paper", label "What kind of paper *".

`detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ description.filename }} — Health{% endblock %}
{% block content %}
<p><a href="/health">← Health</a></p>
<h1>{{ description.filename }}</h1>
<p>{{ description.category_label }} · {{ entry.entry_date if entry and entry.entry_date else "undated" }}
  {% if document.status.value == "needs_attention" %}<span class="needs-attention"> · {{ document.failure_reason|friendly_reason }}</span>{% endif %}</p>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
  <section>
    <h2>The paper</h2>
    {% if media_kind == "image" %}<img src="{{ description.file_url }}" alt="" style="max-width:100%;">
    {% else %}<iframe src="{{ description.file_url }}" style="width:100%;height:70vh;border:1px solid #ddd;"></iframe>
    <p><a href="{{ description.file_url }}">Open the file</a></p>{% endif %}
  </section>
  <section>
    <h2>What the Hub read</h2>
    <form method="post" action="/health/documents/{{ document.id }}/fields">
      {% include "domains/_field_inputs.html" %}
      <button type="submit">Save</button>
    </form>
    <h3>Areas and conditions</h3>
    <form method="post" action="/health/documents/{{ document.id }}/tags">
      <div class="field"><label>Areas (comma-separated) <input type="text" name="areas" value="{{ area_names }}"></label></div>
      <div class="field"><label>Conditions (comma-separated) <input type="text" name="conditions" value="{{ condition_names }}"></label></div>
      <button type="submit">Save tags</button>
    </form>
  </section>
</div>
<h2>Measurements</h2>
{% if measurements %}
<table class="review-table">
  <thead><tr><th>Date</th><th>Test</th><th>Value</th><th>Reference</th><th>As printed on the paper</th><th></th></tr></thead>
  <tbody>
  {% for m in measurements %}
  <tr{% if m.needs_check %} class="needs-attention"{% endif %}>
    <td>{{ m.measured_on or "—" }}</td><td>{{ m.test }}</td>
    <td>{% if m.value is not none %}{{ "%g"|format(m.value) }} {{ m.unit or "" }}{% else %}{{ m.value_text or "?" }}{% endif %}
      {% if m.out_of_range %}<strong>out of range</strong>{% endif %}</td>
    <td>{{ m.ref_low if m.ref_low is not none else "" }}–{{ m.ref_high if m.ref_high is not none else "" }}</td>
    <td><code>{{ m.source_text or "" }}</code></td>
    <td>{% if m.needs_check %}<form method="post" action="/health/documents/{{ document.id }}/measurements/{{ m.id }}/ok">
      <button type="submit" title="I looked at the paper and this value is right">It's right</button></form>{% endif %}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}<p>No measurements on this paper.</p>{% endif %}
{% if entry and entry.full_text %}<details><summary>Full text as read</summary><pre style="white-space:pre-wrap;">{{ entry.full_text }}</pre></details>{% endif %}
{% endblock %}
```

`areas.html`:

```html
{% extends "base.html" %}
{% block title %}Health areas{% endblock %}
{% block content %}
<p><a href="/health">← Health</a></p>
<h1>Areas and conditions</h1>
<p>The Hub proposes these while reading your papers. Approve a proposal, rename it, or merge it into another.</p>
{% if error %}<p class="needs-attention"><strong>{{ error.what }}</strong> {{ error.why }} {{ error.next_step }}</p>{% endif %}
<table class="review-table">
  <thead><tr><th>Name</th><th>Kind</th><th>Status</th><th></th></tr></thead>
  <tbody>
  {% for a in areas %}
  <tr>
    <td><form method="post" action="/health/areas/{{ a.id }}/rename" style="display:flex;gap:4px;">
      <input type="text" name="name" value="{{ a.name }}"><button type="submit">Rename</button></form></td>
    <td>{{ a.kind }}</td>
    <td>{{ "approved" if a.approved else "proposed" }}</td>
    <td style="display:flex;gap:8px;">
      {% if not a.approved %}<form method="post" action="/health/areas/{{ a.id }}/approve"><button type="submit">Approve</button></form>{% endif %}
      <form method="post" action="/health/areas/{{ a.id }}/merge"><select name="into">
        {% for b in areas if b.id != a.id and b.kind == a.kind %}<option value="{{ b.id }}">{{ b.name }}</option>{% endfor %}
      </select><button type="submit">Merge into</button></form>
    </td>
  </tr>
  {% else %}<tr><td colspan="4">Nothing yet. Areas appear when the first papers are read.</td></tr>{% endfor %}
  </tbody>
</table>
{% endblock %}
```

`people.html`:

```html
{% extends "base.html" %}
{% block title %}Health people{% endblock %}
{% block content %}
<p><a href="/health">← Health</a></p>
<h1>People</h1>
<p>The Hub adds a person the first time a name appears on a paper. Confirm each one, or merge it into someone already listed.</p>
<table class="review-table">
  <thead><tr><th>Name</th><th>Status</th><th></th></tr></thead>
  <tbody>
  {% for p in people %}
  <tr><td><a href="/health?person={{ p.id }}">{{ p.name }}</a></td><td>{{ "confirmed" if p.confirmed else "new" }}</td>
    <td style="display:flex;gap:8px;">
      {% if not p.confirmed %}<form method="post" action="/health/people/{{ p.id }}/confirm"><button type="submit">Confirm</button></form>{% endif %}
      <form method="post" action="/health/people/{{ p.id }}/merge"><select name="into">
        {% for q in people if q.id != p.id %}<option value="{{ q.id }}">{{ q.name }}</option>{% endfor %}
      </select><button type="submit">Merge into</button></form></td></tr>
  {% else %}<tr><td colspan="3">Nobody yet.</td></tr>{% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_health_browse.py tests/test_health_router.py tests/test_domain_overview.py tests/test_dashboard_router.py -q`
Expected: PASS. Then look at it once in the browser pane: start the app per `README.md`, open `/health`, confirm the filters, detail page and areas page render (record the check in the Checkpoint note; if the app cannot be started in this environment, say so).

- [ ] **Step 5: Checkpoint**

Commit message when ordered: `Health tab: filterable records, detail with values beside the original, areas and people`.

---

### Task 11: What may leave the Hub — the field guard, and appointments on the family Google Calendar

**Files:**
- Modify: `app/privacy/channels.py` (add `channel_fields`), `app/config.py`, `app/domains/health/handler.py`
- Create: `app/integrations/__init__.py` (empty), `app/integrations/google_calendar.py`, `app/domains/health/appointments.py`, `scripts/google_calendar_auth.py`
- Test: `tests/test_privacy_channels.py`, `tests/test_google_calendar.py`, `tests/test_health_appointments.py`

**Interfaces:**
- Produces:
  - `channels.channel_fields(spec, category, fields: Mapping[str, str]) -> dict[str, str]` (drops every `FieldSpec.private` field)
  - `Settings.GOOGLE_CALENDAR_ID, GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN`; `Settings.calendar_configured -> bool`
  - `GoogleCalendar(client_id, client_secret, refresh_token, calendar_id, transport=None)` with `async upsert_event(key: str, body: dict) -> str`, `async delete_event(event_id: str) -> None`
  - `appointments.appointment_event(document, public_url) -> Optional[dict]` (the Google event body, built ONLY from administrative fields)
  - `appointments.sync_appointment(session, document, calendar=None) -> Optional[str]`, `appointments.remove_appointment(session, document, calendar=None)`

- [ ] **Step 1: Write the failing tests**

`tests/test_privacy_channels.py`:

```python
from app.domains.registry import get_spec
from app.models.domain import Domain
from app.privacy.channels import channel_fields


def test_private_fields_never_pass_the_channel_guard():
    spec = get_spec(Domain.HEALTH)
    fields = {"person": "Maria", "provider": "Clínica Sul", "document_date": "2026-03-12",
              "summary": "SECRET-SUMMARY", "record_kind": "test", "notes": "SECRET-NOTES", "amount_billed": "45,5"}
    safe = channel_fields(spec, "lab_result", fields)
    assert safe == {"person": "Maria", "provider": "Clínica Sul", "document_date": "2026-03-12", "amount_billed": "45,5"}


def test_a_domain_with_no_private_fields_passes_everything():
    spec = get_spec(Domain.INSURANCE)
    fields = {"insurer": "Zurich", "policy_number": "AP-1"}
    assert channel_fields(spec, "home_insurance", fields) == fields
```

`tests/test_google_calendar.py`:

```python
import json

import httpx
import pytest

from app.integrations.google_calendar import GoogleCalendar


class Google:
    def __init__(self, existing=None):
        self.calls = []
        self.existing = existing or []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, str(request.url), request.content.decode()))
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 3600})
        if request.method == "GET":
            return httpx.Response(200, json={"items": self.existing})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": "EV1"})

    def calendar(self):
        return GoogleCalendar("cid", "secret", "refresh", "family@group.calendar.google.com",
                              transport=httpx.MockTransport(self.handler))


@pytest.mark.asyncio
async def test_a_new_event_is_created_with_the_hub_key_and_a_bearer_token():
    g = Google()
    event_id = await g.calendar().upsert_event("health-doc-7", {"summary": "x"})
    assert event_id == "EV1"
    token_call = g.calls[0]
    assert "grant_type=refresh_token" in token_call[2] and "refresh" in token_call[2]
    post = next(c for c in g.calls if c[0] == "POST" and "/events" in c[1])
    body = json.loads(post[2])
    assert body["extendedProperties"]["private"]["hub_key"] == "health-doc-7"
    assert "family%40group.calendar.google.com" in post[1] or "family@group.calendar.google.com" in post[1]


@pytest.mark.asyncio
async def test_an_existing_event_for_the_same_key_is_updated_not_duplicated():
    g = Google(existing=[{"id": "OLD"}])
    event_id = await g.calendar().upsert_event("health-doc-7", {"summary": "y"})
    assert event_id == "OLD"
    assert any(c[0] == "PATCH" and c[1].endswith("/events/OLD") for c in g.calls)
    assert not any(c[0] == "POST" and "/events" in c[1] for c in g.calls)


@pytest.mark.asyncio
async def test_delete_is_idempotent_for_a_missing_event():
    def handler(request):
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 3600})
        return httpx.Response(404)
    cal = GoogleCalendar("cid", "s", "r", "cal", transport=httpx.MockTransport(handler))
    await cal.delete_event("GONE")                              # must not raise


@pytest.mark.asyncio
async def test_a_failure_raises_a_plain_error():
    def handler(request):
        return httpx.Response(401, json={"error": "invalid_grant"})
    cal = GoogleCalendar("cid", "s", "r", "cal", transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as exc:
        await cal.upsert_event("k", {})
    assert "calendar" in str(exc.value).lower()
```

`tests/test_health_appointments.py`:

```python
import json
from datetime import date

import httpx
import pytest
from sqlmodel import select

from app.domains.health.appointments import appointment_event, remove_appointment, sync_appointment
from app.domains.health.extraction import ExtractedHealth
from app.domains.health.store import apply_extraction, entry_for
from app.models.domain import Domain
from app.models.schedule import ProblemEntry
from tests.knowledge_factories import make_document
from tests.test_google_calendar import Google

SECRET = "SENTINEL-CLINICAL-REASON"


def _appointment(session, with_time="10:30"):
    doc = make_document(session, domain=Domain.HEALTH, category="appointment", filename="consulta.pdf")
    apply_extraction(session, doc, ExtractedHealth(
        person_name="Maria Santos Almeida", document_date=date(2026, 10, 1), provider="Clínica Sul",
        provider_phone="+351 21 000 0000", provider_address="Av. Central 5, Lisboa", record_kind="follow_up",
        summary=f"{SECRET}: oncology follow-up", appointment_date=date(2026, 11, 3), appointment_time=with_time))
    return doc


def test_the_event_has_when_where_who_contact_a_reminder_and_a_link_but_nothing_clinical(session):
    doc = _appointment(session)
    body = appointment_event(doc, "https://hub.example")
    text = json.dumps(body, ensure_ascii=False)
    assert body["start"] == {"dateTime": "2026-11-03T10:30:00", "timeZone": "Europe/Lisbon"}
    assert body["end"]["dateTime"] == "2026-11-03T11:00:00"
    assert "Clínica Sul" in text and "Maria" in text
    assert body["location"] == "Av. Central 5, Lisboa" and "+351 21 000 0000" in body["description"]
    assert f"https://hub.example/health/documents/{doc.id}" in body["description"]
    assert body["reminders"]["overrides"] == [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 120}]
    assert SECRET not in text and "oncology" not in text and "follow" not in text.lower()


def test_without_a_time_it_is_an_all_day_event(session):
    body = appointment_event(_appointment(session, with_time=None), "https://hub.example")
    assert body["start"] == {"date": "2026-11-03"} and body["end"] == {"date": "2026-11-04"}


def test_only_appointments_with_a_date_get_an_event(session):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result")
    assert appointment_event(doc, "https://hub.example") is None


@pytest.mark.asyncio
async def test_sync_stores_the_event_id_and_resync_updates_it(session):
    doc = _appointment(session)
    g = Google()
    assert await sync_appointment(session, doc, calendar=g.calendar(), public_url="https://hub.example") == "EV1"
    assert entry_for(session, doc.id).calendar_event_id == "EV1"
    g2 = Google(existing=[{"id": "EV1"}])
    await sync_appointment(session, doc, calendar=g2.calendar(), public_url="https://hub.example")
    assert any(c[0] == "PATCH" for c in g2.calls)


@pytest.mark.asyncio
async def test_remove_deletes_the_event_and_forgets_the_id(session):
    doc = _appointment(session)
    g = Google()
    await sync_appointment(session, doc, calendar=g.calendar(), public_url="https://hub.example")
    await remove_appointment(session, doc, calendar=g.calendar())
    assert any(c[0] == "DELETE" for c in g.calls) and entry_for(session, doc.id).calendar_event_id is None


@pytest.mark.asyncio
async def test_when_the_calendar_is_not_set_up_or_fails_the_paper_is_still_filed_and_the_problem_is_logged(session):
    doc = _appointment(session)
    assert await sync_appointment(session, doc, calendar=None, public_url="https://hub.example") is None
    p = session.exec(select(ProblemEntry)).first()
    assert p.what.startswith("Not added to the calendar") and p.why and p.next_step

    def handler(request):
        return httpx.Response(401, json={})
    from app.integrations.google_calendar import GoogleCalendar
    bad = GoogleCalendar("c", "s", "r", "cal", transport=httpx.MockTransport(handler))
    assert await sync_appointment(session, doc, calendar=bad, public_url="https://hub.example") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_privacy_channels.py tests/test_google_calendar.py tests/test_health_appointments.py -q`
Expected: FAIL (`ImportError: channel_fields` / `No module named app.integrations`)

- [ ] **Step 3: Implement**

`app/privacy/channels.py` — append:

```python
from typing import Mapping

from app.domains.base import DomainSpec


def channel_fields(spec: DomainSpec, category: Optional[str], fields: Mapping[str, str]) -> dict[str, str]:
    """The fields that may leave the Hub (Telegram, email, the calendar): administrative ones only.
    Anything a domain marks `private` — what was wrong, what was done — never does (decision D21)."""
    private = {f.key for f in spec.fields_for(category) if f.private}
    return {k: v for k, v in fields.items() if k not in private}
```
(`app/domains/base.py` imports nothing from `privacy`, so there is no cycle.)

`app/config.py` — add inside `Settings` after the bank block:

```python
    # --- Google Calendar (appointments from Health papers go on the family calendar) ---
    GOOGLE_CALENDAR_ID: str = os.environ.get("GOOGLE_CALENDAR_ID") or ""
    GOOGLE_OAUTH_CLIENT_ID: str = os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or ""
    GOOGLE_OAUTH_REFRESH_TOKEN: str = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN") or ""

    @property
    def calendar_configured(self) -> bool:
        return bool(self.GOOGLE_CALENDAR_ID and self.GOOGLE_OAUTH_CLIENT_ID
                    and self.GOOGLE_OAUTH_CLIENT_SECRET and self.GOOGLE_OAUTH_REFRESH_TOKEN)
```

`app/integrations/google_calendar.py`:

```python
"""A thin Google Calendar client (REST over httpx, no extra dependency). One refresh token, minted once by
scripts/google_calendar_auth.py, scope calendar.events. Events are found again by a private extended
property `hub_key`, so re-syncing updates instead of duplicating."""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

import httpx

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API = "https://www.googleapis.com/calendar/v3/calendars"


class CalendarError(Exception):
    pass


class GoogleCalendar:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, calendar_id: str,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self._creds = (client_id, client_secret, refresh_token)
        self._calendar_id = calendar_id
        self._transport = transport

    @classmethod
    def from_settings(cls, settings) -> Optional["GoogleCalendar"]:
        if not settings.calendar_configured:
            return None
        return cls(settings.GOOGLE_OAUTH_CLIENT_ID, settings.GOOGLE_OAUTH_CLIENT_SECRET,
                   settings.GOOGLE_OAUTH_REFRESH_TOKEN, settings.GOOGLE_CALENDAR_ID)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=20)

    async def _token(self, client: httpx.AsyncClient) -> str:
        cid, secret, refresh = self._creds
        resp = await client.post(_TOKEN_URL, data={"client_id": cid, "client_secret": secret,
                                                   "refresh_token": refresh, "grant_type": "refresh_token"})
        if resp.status_code != 200:
            raise CalendarError(f"Google Calendar refused the sign-in (HTTP {resp.status_code}).")
        return resp.json()["access_token"]

    @property
    def _events_url(self) -> str:
        return f"{_API}/{quote(self._calendar_id, safe='')}/events"

    async def upsert_event(self, key: str, body: dict) -> str:
        body = {**body, "extendedProperties": {"private": {"hub_key": key}}}
        async with self._client() as client:
            headers = {"Authorization": f"Bearer {await self._token(client)}"}
            found = await client.get(self._events_url, headers=headers,
                                     params={"privateExtendedProperty": f"hub_key={key}", "maxResults": 1})
            if found.status_code != 200:
                raise CalendarError(f"Google Calendar could not be read (HTTP {found.status_code}).")
            items = found.json().get("items") or []
            if items:
                resp = await client.patch(f"{self._events_url}/{items[0]['id']}", headers=headers, json=body)
            else:
                resp = await client.post(self._events_url, headers=headers, json=body)
            if resp.status_code not in (200, 201):
                raise CalendarError(f"Google Calendar did not save the event (HTTP {resp.status_code}).")
            return resp.json().get("id") or items[0]["id"]

    async def delete_event(self, event_id: str) -> None:
        async with self._client() as client:
            headers = {"Authorization": f"Bearer {await self._token(client)}"}
            resp = await client.delete(f"{self._events_url}/{event_id}", headers=headers)
            if resp.status_code not in (200, 204, 404, 410):
                raise CalendarError(f"Google Calendar did not remove the event (HTTP {resp.status_code}).")
```

`app/domains/health/appointments.py`:

```python
"""Appointments on the family Google Calendar. The event is built ONLY from administrative fields
(app/privacy/channels.channel_fields): when, where, with whom, how to reach them, a reminder and a link back
to the paper in the Hub. The reason for the visit, the kind of event, the summary and the areas never go."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session

from app.config import settings
from app.domains.fields import load_fields
from app.domains.health import store
from app.domains.registry import get_spec
from app.integrations.google_calendar import GoogleCalendar
from app.models.document import Document
from app.models.domain import Domain
from app.privacy.channels import channel_fields
from app.problems import Problem, explain, record_problem, resolve_for

logger = logging.getLogger("health.appointments")
_TZ = "Europe/Lisbon"
_DEFAULT_MINUTES = 30


def appointment_event(document: Document, public_url: str) -> Optional[dict]:
    if document.category != "appointment":
        return None
    spec = get_spec(Domain.HEALTH)
    fields = channel_fields(spec, document.category, load_fields(document))
    day = fields.get("appointment_date")
    if not day:
        return None
    who = fields.get("person") or "Family"
    provider = fields.get("provider") or "Appointment"
    lines = [f"Open the paper in the Hub: {public_url.rstrip('/')}/health/documents/{document.id}"]
    if fields.get("provider_phone"):
        lines.insert(0, f"Phone: {fields['provider_phone']}")
    body: dict = {"summary": f"{who}: {provider}", "description": "\n".join(lines),
                  "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 1440},
                                                                   {"method": "popup", "minutes": 120}]}}
    if fields.get("provider_address"):
        body["location"] = fields["provider_address"]
    time = (fields.get("appointment_time") or "").strip()
    try:
        start = datetime.fromisoformat(f"{day}T{time}:00") if time else None
    except ValueError:
        start = None
    if start is not None:
        body["start"] = {"dateTime": start.isoformat(), "timeZone": _TZ}
        body["end"] = {"dateTime": (start + timedelta(minutes=_DEFAULT_MINUTES)).isoformat(), "timeZone": _TZ}
    else:
        day_date = datetime.fromisoformat(day).date()
        body["start"] = {"date": day_date.isoformat()}
        body["end"] = {"date": (day_date + timedelta(days=1)).isoformat()}
    return body


def _key(document: Document) -> str:
    return f"health-doc-{document.id}"


async def sync_appointment(session: Session, document: Document, calendar: Optional[GoogleCalendar] = None,
                           public_url: Optional[str] = None) -> Optional[str]:
    """Create or update the calendar entry. Never raises: the paper is filed either way, and a failure is
    kept on the Problems page in the three-part form."""
    body = appointment_event(document, public_url or settings.PUBLIC_BASE_URL)
    if body is None:
        return None
    ref = f"calendar:{document.id}"
    calendar = calendar or GoogleCalendar.from_settings(settings)
    if calendar is None:
        record_problem(session, area="Calendar", ref=ref, problem=Problem(
            "Not added to the calendar.", "Google Calendar access is not set up yet, so the Hub cannot write to it.",
            "Ask Pedro to finish the calendar set-up (docs/SYSADMIN.md, Google Calendar), then edit this "
            "appointment's date to add it.", link=f"/health/documents/{document.id}"))
        return None
    try:
        event_id = await calendar.upsert_event(_key(document), body)
    except Exception as exc:
        logger.exception("calendar sync failed for document %s", document.id)
        problem = explain("Not added to the calendar.", str(exc))
        record_problem(session, area="Calendar", ref=ref,
                       problem=Problem(problem.what, problem.why, problem.next_step, f"/health/documents/{document.id}"),
                       technical=str(exc))
        return None
    entry = store.entry_for(session, document.id)
    if entry is not None:
        entry.calendar_event_id = event_id
        session.add(entry)
        session.commit()
    resolve_for(session, ref)
    return event_id


async def remove_appointment(session: Session, document: Document, calendar: Optional[GoogleCalendar] = None) -> None:
    entry = store.entry_for(session, document.id)
    if entry is None or not entry.calendar_event_id:
        return
    calendar = calendar or GoogleCalendar.from_settings(settings)
    if calendar is None:
        return
    try:
        await calendar.delete_event(entry.calendar_event_id)
    except Exception as exc:
        problem = explain("The calendar entry was not removed.", str(exc))
        record_problem(session, area="Calendar", ref=f"calendar:{document.id}",
                       problem=Problem(problem.what, problem.why, problem.next_step), technical=str(exc))
        return
    entry.calendar_event_id = None
    session.add(entry)
    session.commit()
```

`app/domains/health/handler.py` — add `from app.domains.health.appointments import sync_appointment` and, at the end of `process` (after the wiki ingest, before setting PROCESSED) and at the end of `on_fields_changed`:

```python
        await sync_appointment(session, document)
```

`scripts/google_calendar_auth.py`:

```python
"""One-time helper (Pedro runs it on his own computer): signs in to Google, asks for permission to write
calendar events, and prints the refresh token to put in the Hub's environment.

Usage:
    python scripts/google_calendar_auth.py <client_id> <client_secret>

Needs: a Google Cloud project with the Calendar API on and an OAuth client of type "Desktop app"
(docs/SYSADMIN.md, Google Calendar). Uses only the standard library and httpx."""

import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import httpx

SCOPE = "https://www.googleapis.com/auth/calendar.events"
PORT = 8765


def main(client_id: str, client_secret: str) -> int:
    redirect = f"http://127.0.0.1:{PORT}/"
    state = secrets.token_urlsafe(16)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state") == [state] and "code" in query:
                result["code"] = query["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"You can close this tab and go back to the terminal.")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state})
    print("Opening your browser to ask Google for permission. If it does not open, visit:\n" + url)
    webbrowser.open(url)
    thread.join(timeout=300)
    if "code" not in result:
        print("No answer from Google within five minutes. Run the script again.")
        return 1
    resp = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": result["code"], "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    token = resp.json().get("refresh_token")
    if resp.status_code != 200 or not token:
        print(f"Google did not give a refresh token (HTTP {resp.status_code}): {resp.text[:300]}")
        return 1
    print("\nGOOGLE_OAUTH_REFRESH_TOKEN=" + token)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_privacy_channels.py tests/test_google_calendar.py tests/test_health_appointments.py tests/test_health_handler.py tests/test_config_channels.py -q`
Expected: PASS. In `tests/test_health_handler.py` the filing tests now also reach `sync_appointment`, which only acts for `appointment` papers; the lab-result tests are unaffected.

- [ ] **Step 5: Checkpoint**

Commit message when ordered: `Health: only administrative fields may leave the Hub; appointments go on the family Google Calendar (D21)`.

---

### Task 12: Delete a health record and everything derived from it

**Files:**
- Modify: `app/domains/health/handler.py` (`withdraw` also removes the calendar entry), `app/routers/health.py`, `app/templates/health/detail.html`
- Test: `tests/test_health_delete.py`

**Interfaces:**
- Produces: `app.domains.health.store.discard_document(session, document, calendar=None) -> Document` and `POST /health/documents/{id}/delete` (303 to `/health`).

- [ ] **Step 1: Write the failing test**

```python
import json

import pytest
from sqlmodel import select

from app.domains.health import store
from app.models.document import DocumentStatus
from app.models.health import HealthEntry, HealthMeasurement
from app.models.wiki import WikiClaimSource
from tests.test_health_handler import _file  # files a real paper through ingest()
from tests import privacy_world as pw
from tests.fakes.fake_gateway import gateway_text_result


@pytest.mark.asyncio
async def test_discarding_removes_the_derived_data_and_hides_the_paper_but_keeps_the_file(session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")
    wire = pw.Wire()
    doc = (await _file(session, wire)).document
    path = doc.file_path
    assert session.exec(select(HealthMeasurement)).all() and session.exec(select(WikiClaimSource)).all()

    await store.discard_document(session, doc)
    session.refresh(doc)
    assert doc.status == DocumentStatus.DISCARDED
    assert session.exec(select(HealthEntry)).all() == [] and session.exec(select(HealthMeasurement)).all() == []
    assert all(l.withdrawn_at is not None for l in session.exec(select(WikiClaimSource)).all())
    import os
    assert os.path.exists(path)                                     # raw sources are immutable


def test_the_delete_route_redirects_to_the_list(client, session):
    from datetime import date
    from tests.test_health_browse import _paper
    doc = _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], name="d.pdf")
    r = client.post(f"/health/documents/{doc.id}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/health"
    assert "d.pdf" not in client.get("/health").text
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_health_delete.py -q`
Expected: FAIL (`AttributeError: module 'app.domains.health.store' has no attribute 'discard_document'`)

- [ ] **Step 3: Implement**

`app/domains/health/store.py` — append:

```python
async def discard_document(session: Session, document: Document, calendar=None) -> Document:
    """Take a health paper out of use: its calendar entry, wiki claims, measurements, tags and entry are
    withdrawn; the row and file are kept (raw sources are immutable) and the paper is marked discarded."""
    from app.domains.health.appointments import remove_appointment
    from app.models.document import DocumentStatus
    from app.services.wiki_engine import withdraw_from_wiki

    await remove_appointment(session, document, calendar=calendar)
    await withdraw_from_wiki(session, document, description=f"{document.filename} deleted from Health")
    purge_derived(session, document)
    document.status = DocumentStatus.DISCARDED
    session.add(document)
    session.commit()
    session.refresh(document)
    return document
```

`app/domains/health/handler.py` — `withdraw` becomes:

```python
    async def withdraw(self, session: Session, document: Document) -> None:
        from app.domains.health.appointments import remove_appointment
        await remove_appointment(session, document)
        store.purge_derived(session, document)
```

`app/routers/health.py` — append:

```python
@router.post("/documents/{document_id}/delete")
async def delete_document(document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    await store.discard_document(session, document)
    return RedirectResponse("/health", status_code=303)
```
(The `domain_entries` listing only returns PROCESSED/NEEDS_ATTENTION documents, so a discarded paper disappears from the tab, Ask and the Overview; `browse._all_rows` filters the same way.)

`app/templates/health/detail.html` — before the closing `{% endblock %}` add:

```html
<form method="post" action="/health/documents/{{ document.id }}/delete" style="margin-top:24px;"
      onsubmit="return confirm('Delete this paper and everything read from it? The original file is kept on the server but is no longer used anywhere.');">
  <button type="submit">Delete this paper</button>
</form>
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_health_delete.py tests/test_health_router.py tests/test_health_handler.py tests/test_financials_refile.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint**

Commit message when ordered: `Health: delete a paper and everything derived from it`.

---

### Task 13: Decisions on record, set-up notes, and the backfill for Pedro's existing reports

**Files:**
- Modify: `docs/privacy/DECISIONS.md`, `docs/ARCHITECTURE.md` (Health section), `docs/SYSADMIN.md` (Google Calendar), `README.md` only if it lists tabs
- Create: `scripts/backfill_health.py`
- Test: `tests/test_backfill_health.py`

**Interfaces:**
- Produces: `scripts/backfill_health.py::backfill(session, folder: Path, auto_approve: bool, classify=suggest_domain_and_category) -> dict[str, int]` (counts `received`, `duplicates`, `approved`, `waiting`, `failed`).

- [ ] **Step 1: Write the failing test**

```python
import pytest

from scripts.backfill_health import backfill
from app.models.domain import Domain
from app.services.domain_classifier import DomainSuggestion


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")


@pytest.mark.asyncio
async def test_papers_land_in_the_inbox_and_confident_health_ones_are_approved_on_request(session, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (folder / "b.pdf").write_bytes(b"%PDF-1.4 b")
    (folder / "notes.txt").write_bytes(b"not a paper")

    async def classify(path, context_text):
        if path.endswith("a.pdf") or "a.pdf" in path:
            return DomainSuggestion(Domain.HEALTH, "lab_result", 0.95, "lab")
        return DomainSuggestion(None, None, 0.0, "unsure")

    async def fake_approve(session, document_id, **kwargs):          # filing itself is covered by test_health_handler
        fake_approve.calls.append((document_id, kwargs["domain_value"], kwargs["category"]))
    fake_approve.calls = []

    counts = await backfill(session, folder, auto_approve=True, classify=classify, approve=fake_approve)
    assert counts == {"received": 2, "duplicates": 0, "approved": 1, "waiting": 1, "failed": 0}
    assert [c[1:] for c in fake_approve.calls] == [("health", "lab_result")]

    again = await backfill(session, folder, auto_approve=False, classify=classify, approve=fake_approve)
    assert again["duplicates"] == 2 and again["received"] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_backfill_health.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'scripts.backfill_health'`; if `scripts` is not importable as a package, add an empty `scripts/__init__.py`)

- [ ] **Step 3: Implement `scripts/backfill_health.py`**

```python
"""Bulk-ingest Pedro's existing health reports. Each file goes through the same path as a Telegram or email
paper: the Inbox (recognised by rule, or read once privately to decide what it is, decision D20). With
--auto-approve, a paper the Hub is confident is Health is approved at once, which reads it (always at High),
fills the Health tab and proposes areas, conditions and people. Anything else waits in the Inbox.
Idempotent: a file already received is skipped by its content hash.

Usage:
    python scripts/backfill_health.py <folder> [--auto-approve]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session

from app.db import engine
from app.models.document import DocumentSource
from app.services.domain_classifier import suggest_domain_and_category
from app.services.document_input import is_model_readable
from app.services.inbox_service import approve, is_confident, receive_document
from app.services.ingestion import IncomingFile

_SKIP = {".txt", ".md", ".ds_store"}


async def backfill(session: Session, folder: Path, auto_approve: bool,
                   classify=suggest_domain_and_category, approve=approve) -> dict[str, int]:
    counts = {"received": 0, "duplicates": 0, "approved": 0, "waiting": 0, "failed": 0}
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() not in _SKIP):
        try:
            receipt = await receive_document(
                session, IncomingFile(path.name, path.read_bytes(), DocumentSource.MANUAL, "backfill"),
                context_text=None, external_ref=f"backfill:{path}", classify=classify)
        except Exception as exc:
            print(f"FAILED {path.name}: {exc}")
            counts["failed"] += 1
            continue
        if receipt.duplicate:
            counts["duplicates"] += 1
            continue
        counts["received"] += 1
        item = receipt.inbox_item
        if auto_approve and item is not None and is_confident(item) and item.suggested_domain == "health":
            try:
                await approve(session, receipt.document.id, domain_value="health", category=item.suggested_category,
                              fields={}, reviewed_by="backfill")
                counts["approved"] += 1
                continue
            except Exception as exc:
                print(f"FAILED to file {path.name}: {exc}")
                counts["failed"] += 1
                continue
        counts["waiting"] += 1
    return counts


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    folder = Path(sys.argv[1])
    with Session(engine) as session:
        counts = asyncio.run(backfill(session, folder, auto_approve="--auto-approve" in sys.argv))
    print(counts)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
```
(`is_model_readable` is imported only so an unreadable file type is reported by the classifier note; remove the import if unused after the run.)

`docs/privacy/DECISIONS.md` — append, in the file's existing style, entries:

- **D18 — The ratchet.** A run's level only rises; it rises to High the moment a tool returns content of a private type; every later call goes out High only; the person is told what, when (step), and that nothing went to another AI service afterwards. A chat that has had a private answer starts every later answer High. Private answers are not saved to the wiki. The first call is built from `Reach.without_private()`: no private record is named, counted or summarised in it.
- **D19 — Health is always High.** Forced in `app/privacy/settings.py::FORCED_TIERS`, independent of "Levels are binding"; not configurable; shown locked on the Privacy page.
- **D20 — Unrecognised papers are read once privately.** The first read of any paper no rule recognises runs inside a High ratchet. (Supersedes D13's "held until a human says what it is" for the AI-read path; recognition by rule is unchanged and sends nothing.) Ask and the wiki check may read High types, under D18 (supersedes the "High types stay excluded" wording of D16).
- **D21 — Administrative vs health-record.** Administrative facts (person, provider name/phone/address, dates, amounts, insurer/policy facts, appointments) may leave the Hub; health-record facts (kind of event, summary, areas, conditions, measurements, anything clinical) never do. Enforced by `FieldSpec.private` + `app/privacy/channels.py`. Telegram replies name a private type only as "a private document". Appointments go on the family Google Calendar with when/where/who/contact, reminders and a link back; never the reason.
- **D22 — Health insurance split.** Policy, terms, premium notices and renewal letters stay in Insurance (Mid); reimbursement/claim statements, medical bills, results, prescriptions, referrals, vaccinations and appointment letters are Health (High).

Also record under "Backlog": backups follow the document/data tiers (to be discussed); share-with-doctor export; hard deletion of stored files.

`docs/ARCHITECTURE.md` — add a "Health" section: the domain, its five tables, the ratchet, where each piece lives (use the File Structure table of this plan).

`docs/SYSADMIN.md` — add a "Google Calendar" section:
1. In Google Cloud Console create a project, enable the Google Calendar API, configure the OAuth consent screen (External, add Pedro's account as a test user), create an OAuth client of type *Desktop app*.
2. On Pedro's computer: `python scripts/google_calendar_auth.py <client_id> <client_secret>`; sign in with the Google account that can edit the family calendar; copy the printed `GOOGLE_OAUTH_REFRESH_TOKEN=...`.
3. In Google Calendar: family calendar → Settings and sharing → *Integrate calendar* → copy the *Calendar ID*.
4. On the VPS, add to the Hub's environment file (same place as `HUB_TELEGRAM_BOT_TOKEN`): `GOOGLE_CALENDAR_ID`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`; restart the Hub service.
5. Check: file an appointment letter; the event appears on the calendar with reminders at 1 day and 2 hours; the Problems page shows no "Calendar" entry.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_backfill_health.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint**

Commit message when ordered: `Docs and backfill for the Health cluster (D18–D22)`.

---

### Task 14: Whole-suite verification and the live check

**Files:** none new.

- [ ] **Step 1: Run the whole Hub suite**

Run (from `Home & Family/`, in the background, ~15 minutes): `python3 -m pytest -q -x --no-header -p no:cacheprovider`
Expected: all green. The baseline before this work was 1,924 passed. Fix any failure at its cause; never loosen an assertion. Typical casualties are tests that listed the implemented domains or Ask tool names, or that assumed High is excluded (Task 3 note).

- [ ] **Step 2: Wire-level end-to-end check (local)**

Run `python3 -m pytest tests/test_ask_private.py tests/test_health_handler.py tests/test_privacy_ratchet.py tests/test_health_appointments.py -q -v` and confirm in the output that: the first Ask request is below High and carries no sentinel; every request after a private tool is `house_high`; a Health paper's extraction and wiki calls are `house_high` with "Levels are binding" off; the calendar event body holds no clinical sentinel.

- [ ] **Step 3: Live smoke on the VPS (read-only first; deploy only on Pedro's explicit word)**

After Pedro orders the push (`git subtree push --prefix="Home & Family" home_hub main`) and the deploy finishes:

```bash
ssh root@167.233.51.113 'cd /srv/home-hub/app && sudo -u home-hub git rev-parse --short HEAD; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:9001/health; sudo -u home-hub sqlite3 -readonly /srv/home-hub/data/home_family.db ".tables" | tr " " "\n" | grep -c "^health_"'
```
Expected: the new commit, `200`, and `5` health tables. (If the data path differs, read it from `docs/SYSADMIN.md`.)

Then Pedro uploads one real report on `/health/upload`; verify on the VPS that the gateway served it through Privatemode:

```bash
ssh root@167.233.51.113 'sqlite3 -readonly /opt/llmsel-data/llmsel.db "select call_provider,sensitivity,count(*) from usage group by 1,2 order by 3 desc limit 6"'
```
Expected: `privatemode|house_high` rows grew by two (extraction, wiki) and no health text appears under any other provider.

- [ ] **Step 4: Pedro's steps (give him these, in order, with exact commands)**
1. Say when to commit and push.
2. Share the existing reports: a folder path. Then run on the VPS or locally against the live database: `python scripts/backfill_health.py <folder> --auto-approve`; then approve the proposed areas on `/health/areas` and confirm people on `/health/people`.
3. Google Calendar: do the five steps added to `docs/SYSADMIN.md` in Task 13.
4. Optional: in the Privacy page nothing needs setting for Health (it shows "High (always)").

- [ ] **Step 5: Report**

Report to Pedro, in plain words: what is live, what he still has to do (steps above), what was deferred (backups, doctor export, hard delete), and the EUR cost of the private reads once the first week of usage exists.
