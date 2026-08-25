# Overview Screen Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current bare-bones dashboard with the validated Overview screen: two KPI-card groups (This month: Income/Expenses/Net flow; Position: Cash/Debt/Yearly Commitments), a narrative insight banner, a 12-month cash-flow chart, a merged category-comparison table, and an actionable Needs Attention list — all server-rendered, all drilling down into the existing Transactions page.

**Architecture:** A new `app/services/overview_service.py` does all finance aggregation (pure Python bucketing over `Transaction`/`Account`/`Commitment`/`Debt`, no new schema), backed by a pure-computation `app/services/overview_charts.py` (no DB access) that turns per-month value dicts into renderable chart data — auto-scaled, dual-direction (Net Flow can go negative), floor-legible, testable in total isolation from the database. The existing "household" bits (todos, recently-changed wiki pages) move into a trimmed, renamed `app/services/household_service.py`. `app/routers/dashboard.py` composes both into one context for a rewritten `app/templates/dashboard.html`. `app/routers/transactions.py` gains three small additive filter params (`commitment_id`, `debt_id`, `transaction_type`) so every KPI/category/needs-attention row has a real, precise drill-down target.

**Tech Stack:** FastAPI, SQLModel, Jinja2, htmx (no new dependencies — charts are pure HTML/CSS with native `title=""` attributes for hover tooltips, no inline-SVG generator needed).

**Spec:** `docs/superpowers/specs/2026-08-21-financial-os-requirements-v2.md` — section "## The Overview screen — validated design" (design approved via 13-iteration mockup brainstorm; not re-litigated here), plus "Decomposition", "Data model additions", "Not yet decided / explicitly deferred" for scope boundaries.

## Global Constraints

These apply to every task below; no task should deviate without flagging it explicitly in a commit message and in the "Rulings" list further down.

1. **No SQLModel `Relationship` anywhere in this codebase.** Every cross-table reference is a plain FK column; templates get related-row display data via `{id: value}` lookup dicts built in the router (see `_lookup_dicts_for` in `app/routers/transactions.py`). The Overview follows this too — no relationship is introduced.
2. **Server-rendered Jinja2 + htmx, no build step, no SPA framework, no npm.** `app/static/htmx.min.js` is the only vendored JS. Charts are pure HTML/CSS (div bars sized via inline `style="height: X%"` / `width: X%"`), matching `app/routers/utilities.py` + `app/templates/utilities/_bar_chart.html`. Hover tooltips use the native HTML `title=""` attribute — zero JS required, resolving the spec's open question in favor of "reusable HTML/CSS", not a charting library. **No new frontend dependency is added.**
3. **Migrations: none needed for this sub-project.** Every KPI is computed from data that already exists (`Account`, `Transaction`, `Commitment`, `Debt`, `Merchant`, `Document`, `Todo`, `WikiPage`). See Ruling R1 below for why Cash needed no new column either. If a future review finds a real need for schema change, it must be additive, `render_as_batch=True`, follow `alembic/versions/` naming (see `git log --oneline -- alembic/versions` for the pattern) — but no task in this plan should need one.
4. **Testing:** pytest, fixtures build schema straight from `SQLModel.metadata` (see `tests/conftest.py`), not via Alembic. One test file per service/router, matching existing organization (`tests/test_<module>.py`).
5. **Interaction model:** `Overview KPI/category → filtered Transactions view → underlying transactions`. There is no Analysis screen yet — every drill-down link in this plan points at `/transactions` with query params from its (slightly extended, see R3) filter contract, never at a nonexistent route.
6. **File structure:** small, single-responsibility files. Pure computation (`overview_charts.py`) is kept separate from DB-aggregation (`overview_service.py`) specifically so the hardest-to-get-right logic (auto-scaling, dual-direction bars, floor legibility) is unit-testable with plain dicts, no database, no fixtures.

### Rulings (product/technical judgment calls made while planning, since no one is available to ask mid-build)

- **R1 — No new migration for Cash.** There is no `Account.balance`/`opening_balance` field, and adding one would require the owner to manually back-fill historical opening balances for both accounts — a task nobody can do autonomously right now. Instead, **Cash is computed as the cumulative net (CREDIT − DEBIT) of every transaction tied to a CHECKING/SAVINGS/WALLET account, from the earliest ingested transaction to today** (TRANSFER-type transactions are excluded so money moving between the two tracked accounts doesn't get double-counted). This is a *tracked net cash flow since ingestion began*, not a live bank balance — it will be offset from the true balance by whatever the account's balance was before the first ingested statement. This is flagged in the KPI card's rendering (a small caption) and here as a known, deliberate limitation, not a bug. Fixing it properly (adding an opening-balance concept) belongs to a future data-model refinement, not this sub-project.
- **R2 — Debt KPI has no historical trend, by design, not by oversight.** The `Debt` table stores only `current_balance` — there is no balance-history/snapshot concept (the spec's own "Not yet decided" list confirms: "The full data model for Debt... not yet schema'd"). So Debt's 7-column mini-chart mechanically receives an **empty monthly-history dict**, which `build_trend_chart` already renders as a "no history yet" empty state (see Task 2) — no special-casing needed elsewhere. This naturally matches the up-front warning that Debt should show "a sensible empty state" today.
- **R3 — Transactions filter contract gains three additive params:** `commitment_id`, `debt_id`, `transaction_type` (all optional, mirroring the existing `account_id` int-equality pattern). Without these, Expenses (a `transaction_type=debit` cut), Debt-related items, and Yearly-Commitment items would have no way to drill down precisely — a plain date-range link would show income and transfers too, which isn't "explaining the number." This is the one deliberate, explicitly-flagged deviation from "reuse the filter contract completely as-is" — it is a small, low-risk, convention-matching extension of an already-generic filter function, not a new page or new concept.
- **R4 — Household (Todos, Wiki) demoted, not deleted; Document needs-attention re-homed.** The Overview's fixed layout (per spec) has no slot for generic household to-dos or wiki-change lists — but removing them from the home page with nothing replacing them would be a real regression. They move to a "Household" panel below the Needs Attention section (`app/services/household_service.py`, renamed from `dashboard_service.py`). Separately, the old dashboard's `needs_attention_documents` (Document extraction failures / stuck pending bills) is **not** a household concern — Documents are bills/bank-statement artifacts, i.e. finance-flavored — so that query moves into `overview_service.get_needs_attention` alongside review-queue/upcoming-bill/category-anomaly items, exactly matching the spec's own Needs Attention list. `spend_this_month`/`spend_last_month` (the old dashboard's simple category totals) are dropped entirely — the new category-comparison table strictly supersedes them.
- **R5 — "New recurring payment confirmation" folds into the review-queue count, not a separate line.** `classification_engine.get_needs_review_queue()` already surfaces `recurring_candidates` as part of one combined queue; Needs Attention shows one summary row ("N items waiting in Needs Review") linking to `/transactions/needs-review`, where the existing UI already breaks recurring candidates out. Duplicating that breakdown on the Overview would fight the spec's own "insight, not six graphs" principle.
- **R6 — Rolling-average semantics:** "N months" = the trailing average of the N most recently **complete** calendar months strictly before the current (in-progress) month. "Now" is always the current month-to-date value (a flow sum for Income/Expenses/Net-Flow, or an as-of-today balance for Cash/Debt). 1M average = last complete month's total itself (N=1).
- **R7 — Category anomaly threshold:** a category's Needs-Attention flag fires when its current-month total is ≥40% above its trailing-3-month average AND that average is at least €30 (avoids noise on categories that are naturally tiny/rare).
- **R8 — Category-comparison table period is fixed at "this month vs. trailing 3-month average"**, not tied to the cash-flow chart's range pills. The spec names an interactive pill control only for the cash-flow chart; giving the comparison table its own independent range selector isn't specified and would add a second interactive control the spec doesn't ask for. This is a deliberate scope-narrowing, noted here explicitly.
- **R9 — Best-available drill-down targets where the spec's ideal doesn't exist yet:** Debt KPI → `/transactions/needs-review` (a dedicated Debt view is explicitly future work per spec: "drilling into a dedicated Debt view... not yet designed"). Cash KPI → unfiltered `/transactions` (Cash spans multiple accounts; no single filter captures "explain this number" better). Net Flow KPI → `/transactions?date_from=...&date_to=...` (no single filter dimension represents "net"; the date-bounded, unfiltered view is the closest available "these are the transactions behind this number").
- **R10 — Debt KPI value is floored at zero for display.** `Debt.direction=OWED_TO_US` legitimately nets *against* what we owe; in the pathological case where informal money owed *to* the family exceeds all debt owed *by* the family, the true net would be negative — but the spec's uniform-red/unfavorable/positive-magnitude treatment assumes debt is normally a liability. `value = max(0.0, net)` in that edge case rather than inventing new positive-net semantics the spec never discusses. With real data (Debts table effectively empty today) this never triggers in practice.

## File Structure

New files:
- `app/services/overview_charts.py` — pure computation: `TrendPoint`, `TrendChart`, `build_trend_chart()`; `CashFlowMonth`, `CashFlowChart`, `build_cash_flow_chart()`. Zero DB access, unit-tested with plain dicts.
- `app/services/overview_service.py` — DB aggregation + orchestration: `KpiCard`, `YearlyCommitmentCard`, `CategoryComparisonRow`, `NeedsAttentionItem`, `OverviewData` dataclasses; `get_overview_data()` entry point plus its private helpers.
- `app/services/household_service.py` — renamed from `app/services/dashboard_service.py`, trimmed to `HouseholdData` (`open_todos`, `recently_changed_wiki_pages`) + `get_household_data()`.
- `app/templates/dashboard/_kpi_card.html` — macro rendering one of the five trend-chart KPI cards (Income/Expenses/Net flow/Cash/Debt).
- `app/templates/dashboard/_yearly_commitments_card.html` — macro for the progress-bar mechanic.
- `app/templates/dashboard/_cash_flow_chart.html` — the 12-month grouped-bar chart + range pills (htmx-swappable fragment).
- `app/templates/dashboard/_category_comparison.html` — the merged category table.
- `app/templates/dashboard/_needs_attention.html` — the actionable list.
- `app/templates/dashboard/_household.html` — the demoted todos/wiki panel.

Modified files:
- `app/routers/transactions.py` — add `commitment_id`, `debt_id`, `transaction_type` filter params (R3).
- `app/templates/transactions/list.html` — preserve the three new params through the pagination querystring.
- `app/routers/dashboard.py` — compose `get_overview_data()` + `get_household_data()`; add `GET /cash-flow-chart` htmx partial route.
- `app/templates/dashboard.html` — full rewrite: KPI groups, narrative banner, cash-flow chart, category table, needs attention, household panel.
- `app/templates/base.html` — new CSS for the KPI grid, colors, progress bar, page max-width.
- `tests/test_dashboard_service.py` → renamed `tests/test_household_service.py`, trimmed.
- `tests/test_dashboard_router.py` — extended to cover the new Overview content.
- `tests/test_transactions_router.py` — extended to cover the three new filters.

Deleted: nothing (renames preserve history via `git mv`).

---

### Task 1: Extend the Transactions filter contract (`commitment_id`, `debt_id`, `transaction_type`)

**Files:**
- Modify: `app/routers/transactions.py`
- Modify: `app/templates/transactions/list.html`
- Test: `tests/test_transactions_router.py`

**Interfaces:**
- Produces: `_apply_transaction_filters(statement, category=None, nature=None, account_id=None, date_from=None, date_to=None, commitment_id=None, debt_id=None, transaction_type=None)` and matching new params threaded through `_filtered_transactions`, `_count_filtered_transactions`, and the `GET /transactions` route — every later task's drill-down URLs assume these three params exist and behave like `account_id` (int equality for `commitment_id`/`debt_id`, enum-value equality for `transaction_type`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_transactions_router.py — add at end of file
from app.models.commitment import Cadence, Commitment
from app.models.debt import Debt, DebtKind
from app.models.transaction import TransactionType


def test_list_transactions_filters_by_commitment(client, session):
    commitment = Commitment(name="IMI 2026", cadence=Cadence.YEARLY, planned_amount=600.0, year=2026)
    session.add(commitment)
    session.commit()
    session.refresh(commitment)

    t1 = _make_transaction(session, "AT IMI", Category.OTHER_EXPENSE, 300.0)
    t1.commitment_id = commitment.id
    session.add(t1)
    session.commit()
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0)

    response = client.get("/transactions", params={"commitment_id": commitment.id})

    assert "AT IMI" in response.text
    assert "EDP" not in response.text


def test_list_transactions_filters_by_debt(client, session):
    debt = Debt(kind=DebtKind.INFORMAL, original_amount=500.0, current_balance=Decimal("500.00"))
    session.add(debt)
    session.commit()
    session.refresh(debt)

    t1 = _make_transaction(session, "TRANSFER TO JOAO", Category.TRANSFER, 500.0)
    t1.debt_id = debt.id
    session.add(t1)
    session.commit()
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0)

    response = client.get("/transactions", params={"debt_id": debt.id})

    assert "TRANSFER TO JOAO" in response.text
    assert "EDP" not in response.text


def test_list_transactions_filters_by_transaction_type(client, session):
    _make_transaction(session, "SALARIO", Category.INCOME, 2000.0)
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0)
    # _make_transaction defaults transaction_type to DEBIT; give SALARIO a CREDIT type directly.
    salario = session.exec(select(Transaction).where(Transaction.provider == "SALARIO")).first()
    salario.transaction_type = TransactionType.CREDIT
    session.add(salario)
    session.commit()

    response = client.get("/transactions", params={"transaction_type": "credit"})

    assert "SALARIO" in response.text
    assert "EDP" not in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_transactions_router.py -k "commitment or debt or transaction_type" -v`
Expected: FAIL — `commitment_id`/`debt_id`/`transaction_type` aren't recognized query params yet (filters silently no-op, so the "not in response.text" assertions fail because both rows show up).

- [ ] **Step 3: Implement the filter extension**

```python
# app/routers/transactions.py — replace _apply_transaction_filters, _filtered_transactions,
# _count_filtered_transactions, and the list_transactions signature/body as follows.

from app.models.transaction import Category, Nature, Transaction, TransactionType  # extend existing import


def _apply_transaction_filters(
    statement,
    category: Optional[str] = None,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    commitment_id: Optional[int] = None,
    debt_id: Optional[int] = None,
    transaction_type: Optional[str] = None,
):
    if category:
        statement = statement.where(Transaction.category == Category(category))
    if nature:
        statement = statement.where(Transaction.nature == Nature(nature))
    if account_id:
        statement = statement.where(Transaction.account_id == account_id)
    if date_from:
        statement = statement.where(Transaction.paid_date >= date_from)
    if date_to:
        statement = statement.where(Transaction.paid_date <= date_to)
    if commitment_id:
        statement = statement.where(Transaction.commitment_id == commitment_id)
    if debt_id:
        statement = statement.where(Transaction.debt_id == debt_id)
    if transaction_type:
        statement = statement.where(Transaction.transaction_type == TransactionType(transaction_type))
    return statement


def _filtered_transactions(
    session: Session,
    category: Optional[str] = None,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    commitment_id: Optional[int] = None,
    debt_id: Optional[int] = None,
    transaction_type: Optional[str] = None,
    page: int = 1,
):
    statement = select(Transaction).order_by(Transaction.paid_date.desc(), Transaction.id.desc())
    statement = _apply_transaction_filters(
        statement, category=category, nature=nature, account_id=account_id,
        date_from=date_from, date_to=date_to, commitment_id=commitment_id,
        debt_id=debt_id, transaction_type=transaction_type,
    )
    statement = statement.limit(_PAGE_SIZE).offset((page - 1) * _PAGE_SIZE)
    return session.exec(statement).all()


def _count_filtered_transactions(
    session: Session,
    category: Optional[str] = None,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    commitment_id: Optional[int] = None,
    debt_id: Optional[int] = None,
    transaction_type: Optional[str] = None,
) -> int:
    statement = select(Transaction)
    statement = _apply_transaction_filters(
        statement, category=category, nature=nature, account_id=account_id,
        date_from=date_from, date_to=date_to, commitment_id=commitment_id,
        debt_id=debt_id, transaction_type=transaction_type,
    )
    return len(session.exec(statement).all())


@router.get("")
async def list_transactions(
    request: Request,
    category: Optional[str] = None,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    commitment_id: Optional[int] = None,
    debt_id: Optional[int] = None,
    transaction_type: Optional[str] = None,
    page: int = 1,
    session: Session = Depends(get_session),
):
    transactions = _filtered_transactions(
        session, category, nature, account_id, date_from, date_to,
        commitment_id, debt_id, transaction_type, page=page,
    )
    total_count = _count_filtered_transactions(
        session, category, nature, account_id, date_from, date_to,
        commitment_id, debt_id, transaction_type,
    )
    total_pages = max(1, -(-total_count // _PAGE_SIZE))
    accounts = session.exec(select(Account)).all()
    merchant_names, account_names = _lookup_dicts_for(session, transactions)
    return templates.TemplateResponse(
        request,
        "transactions/list.html",
        {
            "transactions": transactions,
            "accounts": accounts,
            "categories": list(Category),
            "natures": list(Nature),
            "filters": {
                "category": category, "nature": nature, "account_id": account_id,
                "date_from": date_from, "date_to": date_to,
                "commitment_id": commitment_id, "debt_id": debt_id,
                "transaction_type": transaction_type,
            },
            "page": page,
            "total_pages": total_pages,
            "merchant_names": merchant_names,
            "account_names": account_names,
        },
    )
```

Note: `bulk_edit` is intentionally left untouched — it already forwards its own filter set through hidden form fields for its own re-render, and no KPI/needs-attention drill-down link ever posts to bulk-edit, so there's no path where these three new filters need bulk-edit round-tripping. Out of scope, noted per Global Constraint 6 / R3.

- [ ] **Step 4: Preserve the new filters through pagination links**

```html
<!-- app/templates/transactions/list.html — replace the pagination querystring line -->
{% set qs = "category=" ~ (filters.category or "") ~ "&nature=" ~ (filters.nature or "") ~ "&account_id=" ~ (filters.account_id or "") ~ "&date_from=" ~ (filters.date_from or "") ~ "&date_to=" ~ (filters.date_to or "") ~ "&commitment_id=" ~ (filters.commitment_id or "") ~ "&debt_id=" ~ (filters.debt_id or "") ~ "&transaction_type=" ~ (filters.transaction_type or "") %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_transactions_router.py -v`
Expected: PASS (all transactions-router tests, old and new)

- [ ] **Step 6: Commit**

```bash
git add app/routers/transactions.py app/templates/transactions/list.html tests/test_transactions_router.py
git commit -m "feat: add commitment_id/debt_id/transaction_type filters to Transactions"
```

---

### Task 2: `overview_charts.py` — trend-chart engine (pure, no DB)

**Files:**
- Create: `app/services/overview_charts.py`
- Test: `tests/test_overview_charts.py`

**Interfaces:**
- Produces: `TrendPoint(label: str, value: float, is_now: bool, is_distant: bool, direction: str, height_pct: float)`; `TrendChart(points: list[TrendPoint], bidirectional: bool, has_data: bool)`; `build_trend_chart(monthly_totals: dict[str, float], today: date, mtd_value: float, floor_fraction: float = 0.08) -> TrendChart`. `monthly_totals` keys are `"YYYY-MM"` strings for **complete** months only (never the in-progress current month). Later tasks (3, 5, 6, 7) call this directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_overview_charts.py
from datetime import date

from app.services.overview_charts import build_trend_chart


def test_all_positive_values_scale_between_floor_and_max():
    monthly = {
        "2026-07": 100.0, "2026-06": 100.0, "2026-05": 100.0, "2026-04": 100.0,
        "2026-03": 100.0, "2026-02": 100.0, "2026-01": 100.0, "2025-12": 100.0,
        "2025-11": 100.0, "2025-10": 100.0, "2025-09": 100.0, "2025-08": 200.0,
    }
    chart = build_trend_chart(monthly, today=date(2026, 8, 15), mtd_value=150.0)

    assert chart.has_data is True
    assert chart.bidirectional is False
    labels = [p.label for p in chart.points]
    assert labels == ["Now", "1M", "3M", "6M", "12M", "18M", "24M"]
    assert all(p.direction == "up" for p in chart.points)
    assert all(0.0 <= p.height_pct <= 100.0 for p in chart.points)
    # The highest raw value in the whole set should render at 100%.
    assert max(p.height_pct for p in chart.points) == 100.0
    # 18M/24M are flagged distant for the highlight box; nothing else is.
    assert [p.is_distant for p in chart.points] == [False, False, False, False, False, True, True]
    now_point = chart.points[0]
    assert now_point.is_now is True
    assert now_point.value == 150.0


def test_negative_values_render_bidirectional():
    monthly = {"2026-07": -50.0, "2026-06": 200.0}
    chart = build_trend_chart(monthly, today=date(2026, 8, 1), mtd_value=-10.0)

    assert chart.bidirectional is True
    by_label = {p.label: p for p in chart.points}
    assert by_label["Now"].direction == "down"
    assert by_label["1M"].direction == "down"  # last complete month (2026-07) is negative
    assert by_label["3M"].direction == "up"     # avg of 2026-07 (-50) and 2026-06 (200) = 75, positive


def test_empty_monthly_totals_is_a_graceful_empty_state():
    chart = build_trend_chart({}, today=date(2026, 8, 1), mtd_value=0.0)

    assert chart.has_data is False
    assert len(chart.points) == 7


def test_floor_keeps_smallest_bar_visible():
    monthly = {f"2026-{m:02d}": 1000.0 for m in range(1, 8)}
    monthly["2025-08"] = 1070.0  # only the 24M horizon differs, ~7% swing like real Income data
    chart = build_trend_chart(monthly, today=date(2026, 8, 15), mtd_value=1000.0)

    smallest = min(p.height_pct for p in chart.points)
    assert smallest > 0.0  # never fully disappears
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_overview_charts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.overview_charts'`

- [ ] **Step 3: Implement `overview_charts.py`**

```python
"""Pure-computation chart engine for the Overview screen's trend mini-charts
and 12-month cash-flow chart. No database access here on purpose: the
auto-scaling / dual-direction / floor-legibility math is the hardest part
of this screen to get right, so it's kept testable with plain dicts,
independent of any fixture or session."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

_HORIZONS = [1, 3, 6, 12, 18, 24]
_HORIZON_LABELS = {1: "1M", 3: "3M", 6: "6M", 12: "12M", 18: "18M", 24: "24M"}
_DISTANT_LABELS = {"18M", "24M"}


def _complete_months_before(today: date, n: int) -> list[str]:
    """The n most recent complete calendar months strictly before today's
    (in-progress) month, as 'YYYY-MM', most-recent first."""
    periods = []
    year, month = today.year, today.month
    for _ in range(n):
        month -= 1
        if month == 0:
            month, year = 12, year - 1
        periods.append(f"{year:04d}-{month:02d}")
    return periods


def _rolling_average(monthly_totals: dict[str, float], today: date, n: int) -> float:
    periods = _complete_months_before(today, n)
    values = [monthly_totals[p] for p in periods if p in monthly_totals]
    return sum(values) / len(values) if values else 0.0


@dataclass
class TrendPoint:
    label: str
    value: float
    is_now: bool = False
    is_distant: bool = False
    direction: str = "up"  # "up" or "down" -- which side of the zero-axis this bar sits on
    height_pct: float = 0.0  # 0-100, height within its own direction's region of the chart


@dataclass
class TrendChart:
    points: list[TrendPoint] = field(default_factory=list)
    bidirectional: bool = False
    has_data: bool = False


def build_trend_chart(
    monthly_totals: dict[str, float],
    today: date,
    mtd_value: float,
    floor_fraction: float = 0.08,
) -> TrendChart:
    has_data = bool(monthly_totals)
    labels = ["Now"] + [_HORIZON_LABELS[n] for n in _HORIZONS]
    raw_values = [mtd_value] + [_rolling_average(monthly_totals, today, n) for n in _HORIZONS]

    min_value = min(raw_values)
    max_value = max(raw_values)
    bidirectional = min_value < 0.0

    points: list[TrendPoint] = []
    if bidirectional:
        up_extent = max(max_value, 0.0)
        down_extent = max(-min_value, 0.0)
        for label, value in zip(labels, raw_values):
            direction = "up" if value >= 0 else "down"
            extent = up_extent if direction == "up" else down_extent
            magnitude = abs(value)
            if extent <= 0:
                height_pct = 0.0
            else:
                floor = extent * floor_fraction
                height_pct = 100.0 * max(magnitude, floor if magnitude > 0 else 0.0) / extent
            points.append(TrendPoint(
                label=label, value=value, is_now=(label == "Now"),
                is_distant=(label in _DISTANT_LABELS), direction=direction,
                height_pct=round(min(height_pct, 100.0), 1),
            ))
    else:
        span = max_value - min_value
        if span > 0:
            floor_baseline = min_value - span * floor_fraction
        else:
            floor_baseline = min_value - abs(min_value) * floor_fraction if min_value else -1.0
        denom = max_value - floor_baseline
        for label, value in zip(labels, raw_values):
            height_pct = 100.0 if denom <= 0 else 100.0 * (value - floor_baseline) / denom
            points.append(TrendPoint(
                label=label, value=value, is_now=(label == "Now"),
                is_distant=(label in _DISTANT_LABELS), direction="up",
                height_pct=round(max(0.0, min(100.0, height_pct)), 1),
            ))

    return TrendChart(points=points, bidirectional=bidirectional, has_data=has_data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_overview_charts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/overview_charts.py tests/test_overview_charts.py
git commit -m "feat: add pure trend-chart engine for Overview KPI mini-charts"
```

---

### Task 3: `overview_charts.py` — 12-month cash-flow chart (pure, no DB)

**Files:**
- Modify: `app/services/overview_charts.py`
- Test: `tests/test_overview_charts.py`

**Interfaces:**
- Consumes: nothing from Task 2 directly (independent dataclasses), but lives in the same module.
- Produces: `CashFlowMonth(label: str, income: float, expense: float)`; `CashFlowChart(months: list[CashFlowMonth], max_value: float, range_key: str)`; `build_cash_flow_chart(monthly_income: dict[str, float], monthly_expense: dict[str, float], range_key: str, today: date) -> CashFlowChart`. `range_key` accepts `"1m"|"3m"|"6m"|"9m"|"12m"|"ytd"`, defaulting to `"12m"` on anything else. Task 12's router and Task 11's `get_overview_data` call this.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_overview_charts.py — append
from app.services.overview_charts import build_cash_flow_chart


def test_cash_flow_chart_12m_includes_current_month_to_date():
    income = {"2026-07": 2000.0}
    expense = {"2026-07": 1500.0, "2026-08": 300.0}
    chart = build_cash_flow_chart(income, expense, "12m", today=date(2026, 8, 10))

    assert len(chart.months) == 12
    assert chart.months[-1].label == "2026-08"
    assert chart.months[-1].expense == 300.0
    assert chart.months[-2].label == "2026-07"
    assert chart.months[-2].income == 2000.0


def test_cash_flow_chart_ytd_starts_in_january():
    chart = build_cash_flow_chart({}, {}, "ytd", today=date(2026, 3, 15))

    assert [m.label for m in chart.months] == ["2026-01", "2026-02", "2026-03"]


def test_cash_flow_chart_unknown_range_falls_back_to_12m():
    chart = build_cash_flow_chart({}, {}, "bogus", today=date(2026, 8, 1))

    assert chart.range_key == "12m"
    assert len(chart.months) == 12


def test_cash_flow_chart_max_value_covers_both_series():
    income = {"2026-08": 100.0}
    expense = {"2026-08": 400.0}
    chart = build_cash_flow_chart(income, expense, "1m", today=date(2026, 8, 1))

    assert chart.max_value == 400.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_overview_charts.py -k cash_flow -v`
Expected: FAIL — `build_cash_flow_chart` doesn't exist yet.

- [ ] **Step 3: Implement**

```python
# app/services/overview_charts.py — append

_RANGE_MONTHS = {"1m": 1, "3m": 3, "6m": 6, "9m": 9, "12m": 12}
_VALID_RANGES = set(_RANGE_MONTHS) | {"ytd"}


def _last_n_months_including_current(today: date, n: int) -> list[str]:
    periods = []
    year, month = today.year, today.month
    for _ in range(n):
        periods.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(periods))


@dataclass
class CashFlowMonth:
    label: str
    income: float
    expense: float


@dataclass
class CashFlowChart:
    months: list[CashFlowMonth] = field(default_factory=list)
    max_value: float = 0.0
    range_key: str = "12m"


def build_cash_flow_chart(
    monthly_income: dict[str, float],
    monthly_expense: dict[str, float],
    range_key: str,
    today: date,
) -> CashFlowChart:
    range_key = range_key if range_key in _VALID_RANGES else "12m"
    if range_key == "ytd":
        periods = [f"{today.year:04d}-{m:02d}" for m in range(1, today.month + 1)]
    else:
        periods = _last_n_months_including_current(today, _RANGE_MONTHS[range_key])

    months = [
        CashFlowMonth(label=p, income=monthly_income.get(p, 0.0), expense=monthly_expense.get(p, 0.0))
        for p in periods
    ]
    max_value = max([m.income for m in months] + [m.expense for m in months] + [0.0])
    return CashFlowChart(months=months, max_value=max_value, range_key=range_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_overview_charts.py -v`
Expected: PASS (all, including Task 2's)

- [ ] **Step 5: Commit**

```bash
git add app/services/overview_charts.py tests/test_overview_charts.py
git commit -m "feat: add cash-flow chart builder with range-pill support"
```

---

### Task 4: Rename/trim `dashboard_service.py` → `household_service.py`

**Files:**
- Create (via `git mv`): `app/services/household_service.py` (from `app/services/dashboard_service.py`)
- Modify: `app/routers/dashboard.py`
- Modify: `app/templates/dashboard.html` (temporary trim — full rewrite happens in Task 13)
- Create (via `git mv`): `tests/test_household_service.py` (from `tests/test_dashboard_service.py`)

**Interfaces:**
- Produces: `HouseholdData(open_todos: list[Todo], recently_changed_wiki_pages: list[WikiPage])`; `get_household_data(session: Session, today: Optional[date] = None) -> HouseholdData`. Task 13 consumes this for the demoted "Household" panel.
- This task deliberately leaves the finance side of the home page thin (Task 13 restores it fully) — the app must stay green and runnable at every commit boundary.

- [ ] **Step 1: Rename the files, preserving history**

```bash
git mv app/services/dashboard_service.py app/services/household_service.py
git mv tests/test_dashboard_service.py tests/test_household_service.py
```

- [ ] **Step 2: Rewrite `household_service.py`, trimmed to household concerns only**

```python
# app/services/household_service.py
"""Aggregation for the Overview screen's demoted 'Household' panel: open
to-dos and recently-changed wiki pages. Finance aggregation (KPIs,
cash-flow, category comparison, needs attention) lives in
overview_service.py -- Documents are bill/statement artifacts, so their
needs-attention query moved there too, not here."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.todo import Todo
from app.models.wiki import WikiPage

_RECENTLY_CHANGED_DAYS = 7


@dataclass
class HouseholdData:
    open_todos: list[Todo] = field(default_factory=list)
    recently_changed_wiki_pages: list[WikiPage] = field(default_factory=list)


def get_household_data(session: Session, today: Optional[date] = None) -> HouseholdData:
    open_todos = list(
        session.exec(select(Todo).where(Todo.done == False).order_by(Todo.due_date))  # noqa: E712
    )
    cutoff = datetime.utcnow() - timedelta(days=_RECENTLY_CHANGED_DAYS)
    recently_changed = list(session.exec(select(WikiPage).where(WikiPage.updated_at >= cutoff)))
    return HouseholdData(open_todos=open_todos, recently_changed_wiki_pages=recently_changed)
```

- [ ] **Step 3: Trim the renamed test file to household-only coverage**

```python
# tests/test_household_service.py
from datetime import date, datetime, timedelta

from app.models.todo import Todo
from app.models.wiki import WikiPage
from app.services.household_service import get_household_data


def test_open_todos_and_recent_wiki(session):
    session.add(Todo(title="Pay EDP", due_date=date(2026, 9, 5), done=False))
    session.add(Todo(title="Pay water", due_date=date(2026, 8, 1), done=True))
    session.add(WikiPage(topic="Electricity", facts_json="{}", updated_at=datetime.utcnow()))
    session.add(WikiPage(
        topic="Old topic", facts_json="{}", updated_at=datetime.utcnow() - timedelta(days=30),
    ))
    session.commit()

    data = get_household_data(session, today=date(2026, 8, 17))

    assert len(data.open_todos) == 1
    assert data.open_todos[0].title == "Pay EDP"
    assert len(data.recently_changed_wiki_pages) == 1
    assert data.recently_changed_wiki_pages[0].topic == "Electricity"
```

(The removed `spend_this_month`/`spend_last_month` and `needs_attention_documents` coverage is not lost — it resurfaces, better, in `tests/test_overview_service.py` in Tasks 5 and 11.)

- [ ] **Step 4: Update the router to use the new service (temporary minimal wiring)**

```python
# app/routers/dashboard.py
"""Dashboard home route and health check."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db import get_session
from app.services.household_service import get_household_data

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/")
async def dashboard(request: Request, session: Session = Depends(get_session)):
    household = get_household_data(session)
    return templates.TemplateResponse(request, "dashboard.html", {"household": household})
```

- [ ] **Step 5: Trim the template to match (temporary — Task 13 rewrites this fully)**

```html
<!-- app/templates/dashboard.html -->
{% extends "base.html" %}
{% block title %}Overview — Home & Family{% endblock %}
{% block content %}
<h1>Overview</h1>
<p><em>Finance KPIs are being rebuilt in this branch's later tasks.</em></p>
<section>
  <h2>Open to-dos</h2>
  <ul>
    {% for todo in household.open_todos %}
      <li>{{ todo.title }} — due {{ todo.due_date }}</li>
    {% else %}
      <li>Nothing due.</li>
    {% endfor %}
  </ul>
</section>
<section>
  <h2>Recently changed wiki facts</h2>
  <ul>
    {% for page in household.recently_changed_wiki_pages %}
      <li><a href="/wiki/{{ page.id }}">{{ page.topic }}</a></li>
    {% else %}
      <li>No recent changes.</li>
    {% endfor %}
  </ul>
</section>
{% endblock %}
```

- [ ] **Step 6: Update `tests/test_dashboard_router.py` to match**

```python
# tests/test_dashboard_router.py — unchanged assertions still hold (todos still render on "/"),
# no edit needed here; run it to confirm.
```

- [ ] **Step 7: Run the full suite to verify nothing else broke**

Run: `python3 -m pytest -q`
Expected: PASS, 0 failures (the app is intentionally thin on finance content right now — Tasks 5-13 restore it)

- [ ] **Step 8: Commit**

```bash
git add app/services/household_service.py tests/test_household_service.py app/routers/dashboard.py app/templates/dashboard.html
git commit -m "refactor: rename dashboard_service to household_service, trim to todos/wiki"
```

---

### Task 5: `overview_service.py` — Income / Expenses / Net Flow KPI cards

**Files:**
- Create: `app/services/overview_service.py`
- Test: `tests/test_overview_service.py`

**Interfaces:**
- Consumes: `build_trend_chart` from `app/services/overview_charts.py` (Task 2).
- Produces: `KpiCard(label: str, value: float, color: str, drill_down_url: str, chart: TrendChart)`; `_monthly_flow_totals(session, today) -> tuple[dict[str,float], dict[str,float]]` (income_by_month, expense_by_month, keyed `"YYYY-MM"`, **including** the current in-progress month); `get_flow_kpis(session, today, income_monthly, expense_monthly) -> list[KpiCard]` (returns exactly `[Income, Expenses, Net flow]` in that order). Task 11's orchestrator and Task 12's router consume `get_flow_kpis` and `_monthly_flow_totals`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_overview_service.py
from datetime import date

from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Transaction, TransactionType
from app.services.overview_service import get_flow_kpis, _monthly_flow_totals


def _doc(session, name="doc"):
    document = Document(
        filename=f"{name}.pdf", file_path=f"/tmp/{name}.pdf", content_hash=f"h-{name}",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def _txn(session, document, provider, amount, ttype, paid_date, category=Category.OTHER_EXPENSE):
    t = Transaction(
        document_id=document.id, provider=provider, category=category,
        transaction_type=ttype, amount=amount, currency="EUR", paid_date=paid_date,
    )
    session.add(t)
    session.commit()
    return t


def test_monthly_flow_totals_buckets_by_calendar_month(session):
    document = _doc(session)
    _txn(session, document, "SALARIO", 2000.0, TransactionType.CREDIT, date(2026, 7, 5))
    _txn(session, document, "EDP", 60.0, TransactionType.DEBIT, date(2026, 7, 10))
    _txn(session, document, "CONTINENTE", 40.0, TransactionType.DEBIT, date(2026, 8, 2))
    _txn(session, document, "REVOLUT TOPUP", 100.0, TransactionType.TRANSFER, date(2026, 7, 15))

    income, expense = _monthly_flow_totals(session, today=date(2026, 8, 10))

    assert income == {"2026-07": 2000.0}
    assert expense == {"2026-07": 60.0, "2026-08": 40.0}


def test_flow_kpis_now_and_drill_down_urls(session):
    document = _doc(session)
    _txn(session, document, "SALARIO", 2000.0, TransactionType.CREDIT, date(2026, 7, 5))
    _txn(session, document, "EDP", 60.0, TransactionType.DEBIT, date(2026, 7, 10))
    _txn(session, document, "CONTINENTE", 40.0, TransactionType.DEBIT, date(2026, 8, 2))

    income_monthly, expense_monthly = _monthly_flow_totals(session, today=date(2026, 8, 10))
    kpis = get_flow_kpis(session, date(2026, 8, 10), income_monthly, expense_monthly)

    by_label = {k.label: k for k in kpis}
    assert list(by_label) == ["Income", "Expenses", "Net flow"]

    assert by_label["Income"].value == 0.0  # nothing credited in August yet
    assert by_label["Expenses"].value == 40.0
    assert by_label["Net flow"].value == -40.0

    assert by_label["Income"].color == "green"
    assert by_label["Expenses"].color == "red"
    assert by_label["Net flow"].color == "green"

    assert by_label["Income"].drill_down_url == "/transactions?category=income&date_from=2026-08-01&date_to=2026-08-10"
    assert by_label["Expenses"].drill_down_url == "/transactions?transaction_type=debit&date_from=2026-08-01&date_to=2026-08-10"
    assert by_label["Net flow"].drill_down_url == "/transactions?date_from=2026-08-01&date_to=2026-08-10"

    # July's complete-month totals feed the 1M trend point.
    assert by_label["Income"].chart.points[1].value == 2000.0  # "1M" point
    assert by_label["Expenses"].chart.points[1].value == 60.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_overview_service.py -v`
Expected: FAIL — `app.services.overview_service` doesn't exist yet.

- [ ] **Step 3: Implement**

```python
# app/services/overview_service.py
"""DB aggregation for the Overview screen: KPI cards, cash-flow chart data,
category comparison, narrative insight, and needs attention. Pure chart
math (auto-scaling, dual-direction bars) lives in overview_charts.py and
is imported here, not reimplemented."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.transaction import Transaction, TransactionType
from app.services.overview_charts import TrendChart, build_trend_chart

_TREND_MONTHS_BACK = 24


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _monthly_flow_totals(session: Session, today: date) -> tuple[dict[str, float], dict[str, float]]:
    """Buckets CREDIT/DEBIT transaction amounts by calendar month (paid_date),
    covering the trailing _TREND_MONTHS_BACK complete months plus the
    current in-progress month. TRANSFER-type rows are excluded (money
    moving between our own tracked accounts is neither income nor
    expense)."""
    cutoff = _month_start(today) - timedelta(days=31 * (_TREND_MONTHS_BACK + 1))
    statement = select(Transaction).where(
        Transaction.paid_date >= cutoff, Transaction.paid_date <= today,
    )
    income: dict[str, float] = {}
    expense: dict[str, float] = {}
    for t in session.exec(statement):
        if t.paid_date is None:
            continue
        key = _month_key(t.paid_date)
        if t.transaction_type == TransactionType.CREDIT:
            income[key] = income.get(key, 0.0) + t.amount
        elif t.transaction_type == TransactionType.DEBIT:
            expense[key] = expense.get(key, 0.0) + t.amount
    return income, expense


def _mtd_and_complete(monthly: dict[str, float], today: date) -> tuple[float, dict[str, float]]:
    current_key = _month_key(today)
    mtd = monthly.get(current_key, 0.0)
    complete = {k: v for k, v in monthly.items() if k != current_key}
    return mtd, complete


@dataclass
class KpiCard:
    label: str
    value: float
    color: str  # "green" or "red"
    drill_down_url: str
    chart: TrendChart


def get_flow_kpis(
    session: Session,
    today: date,
    income_monthly: dict[str, float],
    expense_monthly: dict[str, float],
) -> list[KpiCard]:
    income_mtd, income_complete = _mtd_and_complete(income_monthly, today)
    expense_mtd, expense_complete = _mtd_and_complete(expense_monthly, today)

    all_periods = set(income_complete) | set(expense_complete)
    net_complete = {p: income_complete.get(p, 0.0) - expense_complete.get(p, 0.0) for p in all_periods}
    net_mtd = income_mtd - expense_mtd

    month_start = _month_start(today).isoformat()
    today_iso = today.isoformat()

    return [
        KpiCard(
            label="Income", value=income_mtd, color="green",
            drill_down_url=f"/transactions?category=income&date_from={month_start}&date_to={today_iso}",
            chart=build_trend_chart(income_complete, today, income_mtd),
        ),
        KpiCard(
            label="Expenses", value=expense_mtd, color="red",
            drill_down_url=f"/transactions?transaction_type=debit&date_from={month_start}&date_to={today_iso}",
            chart=build_trend_chart(expense_complete, today, expense_mtd),
        ),
        KpiCard(
            label="Net flow", value=net_mtd, color="green",
            drill_down_url=f"/transactions?date_from={month_start}&date_to={today_iso}",
            chart=build_trend_chart(net_complete, today, net_mtd),
        ),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_overview_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/overview_service.py tests/test_overview_service.py
git commit -m "feat: add Income/Expenses/Net-flow KPI aggregation"
```

---

### Task 6: `overview_service.py` — Cash KPI (real trend, reconstructed from transaction history)

**Files:**
- Modify: `app/services/overview_service.py`
- Test: `tests/test_overview_service.py`

**Interfaces:**
- Produces: `get_cash_kpi(session: Session, today: date) -> KpiCard` (label `"Cash"`, color `"green"`, `drill_down_url="/transactions"` per Ruling R9). Task 11's orchestrator consumes this.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_overview_service.py — append
from app.models.account import Account, AccountType
from app.services.overview_service import get_cash_kpi


def test_cash_kpi_nets_credits_and_debits_across_tracked_accounts(session):
    account = Account(name="Santander", institution="Santander", account_type=AccountType.CHECKING)
    card_account = Account(name="Card", institution="Santander", account_type=AccountType.CARD)
    session.add_all([account, card_account])
    session.commit()
    session.refresh(account)
    session.refresh(card_account)

    document = _doc(session)
    t1 = _txn(session, document, "SALARIO", 2000.0, TransactionType.CREDIT, date(2026, 7, 5))
    t1.account_id = account.id
    t2 = _txn(session, document, "EDP", 300.0, TransactionType.DEBIT, date(2026, 7, 10))
    t2.account_id = account.id
    t3 = _txn(session, document, "CARD SPEND", 9999.0, TransactionType.DEBIT, date(2026, 7, 12))
    t3.account_id = card_account.id  # CARD accounts aren't "cash" -- must not count
    session.add_all([t1, t2, t3])
    session.commit()

    kpi = get_cash_kpi(session, today=date(2026, 8, 1))

    assert kpi.label == "Cash"
    assert kpi.value == 1700.0
    assert kpi.color == "green"
    assert kpi.drill_down_url == "/transactions"


def test_cash_kpi_has_no_historical_bars_with_only_one_month_of_data(session):
    account = Account(name="Santander", institution="Santander", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)

    document = _doc(session)
    t1 = _txn(session, document, "SALARIO", 500.0, TransactionType.CREDIT, date(2026, 8, 1))
    t1.account_id = account.id
    session.add(t1)
    session.commit()

    kpi = get_cash_kpi(session, today=date(2026, 8, 15))

    # Only the current month has any transaction -- no COMPLETE month exists yet,
    # so the trend chart is legitimately in its "no history" empty state.
    assert kpi.chart.has_data is False
    assert kpi.value == 500.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_overview_service.py -k cash_kpi -v`
Expected: FAIL — `get_cash_kpi` doesn't exist yet.

- [ ] **Step 3: Implement**

```python
# app/services/overview_service.py — add imports and the following

from app.models.account import Account, AccountType
from app.services.overview_charts import _complete_months_before  # noqa: F401 -- re-exported helper reused for month-end snapshots

_CASH_ACCOUNT_TYPES = (AccountType.CHECKING, AccountType.SAVINGS, AccountType.WALLET)


def _cash_account_ids(session: Session) -> list[int]:
    accounts = session.exec(select(Account).where(Account.account_type.in_(_CASH_ACCOUNT_TYPES))).all()
    return [a.id for a in accounts]


def _cash_transactions(session: Session, account_ids: list[int]) -> list[Transaction]:
    if not account_ids:
        return []
    return session.exec(select(Transaction).where(Transaction.account_id.in_(account_ids))).all()


def _cash_balance_as_of(transactions: list[Transaction], as_of: date) -> float:
    """Cumulative net (CREDIT - DEBIT) across every transaction dated on or
    before `as_of`. This is a tracked net cash flow since ingestion began,
    not a live bank balance -- see plan Ruling R1."""
    balance = 0.0
    for t in transactions:
        if t.paid_date is None or t.paid_date > as_of:
            continue
        if t.transaction_type == TransactionType.CREDIT:
            balance += t.amount
        elif t.transaction_type == TransactionType.DEBIT:
            balance -= t.amount
        # TRANSFER rows excluded: money moving between our own tracked
        # accounts nets to zero across the cash pool as a whole.
    return balance


def _month_end(period: str) -> date:
    year, month = (int(p) for p in period.split("-"))
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def get_cash_kpi(session: Session, today: date) -> KpiCard:
    account_ids = _cash_account_ids(session)
    transactions = _cash_transactions(session, account_ids)
    now_value = _cash_balance_as_of(transactions, today)

    monthly = {
        period: _cash_balance_as_of(transactions, _month_end(period))
        for period in _complete_months_before(today, _TREND_MONTHS_BACK)
        if any(t.paid_date and t.paid_date <= _month_end(period) for t in transactions)
    }
    chart = build_trend_chart(monthly, today, now_value)
    return KpiCard(label="Cash", value=now_value, color="green", drill_down_url="/transactions", chart=chart)
```

Note: `_complete_months_before` is a private (`_`-prefixed) helper in `overview_charts.py` — importing it here is an intentional, narrow exception for code reuse within the same package; it's still not part of that module's public dataclass/function surface used by templates or routers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_overview_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/overview_service.py tests/test_overview_service.py
git commit -m "feat: add Cash KPI reconstructed from tracked-account transaction history"
```

---

### Task 7: `overview_service.py` — Debt KPI (current position only, graceful no-history state)

**Files:**
- Modify: `app/services/overview_service.py`
- Test: `tests/test_overview_service.py`

**Interfaces:**
- Produces: `get_debt_kpi(session: Session, today: date) -> KpiCard` (label `"Debt"`, color `"red"`, `drill_down_url="/transactions/needs-review"` per Ruling R9, `chart.has_data` always `False` today per Ruling R2).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_overview_service.py — append
from decimal import Decimal

from app.models.debt import Debt, DebtDirection, DebtKind
from app.services.overview_service import get_debt_kpi


def test_debt_kpi_sums_formal_and_owed_by_us_informal(session):
    session.add(Debt(kind=DebtKind.FORMAL, original_amount=100000.0, current_balance=Decimal("95000.00")))
    session.add(Debt(
        kind=DebtKind.INFORMAL, direction=DebtDirection.OWED_BY_US,
        original_amount=500.0, current_balance=Decimal("300.00"),
    ))
    session.add(Debt(
        kind=DebtKind.INFORMAL, direction=DebtDirection.OWED_TO_US,
        original_amount=200.0, current_balance=Decimal("200.00"),
    ))
    session.commit()

    kpi = get_debt_kpi(session, today=date(2026, 8, 1))

    assert kpi.label == "Debt"
    assert kpi.value == 95100.0  # 95000 + 300 - 200
    assert kpi.color == "red"
    assert kpi.drill_down_url == "/transactions/needs-review"
    assert kpi.chart.has_data is False  # no balance-history tracking exists (Ruling R2)


def test_debt_kpi_with_no_debts_is_a_clean_zero(session):
    kpi = get_debt_kpi(session, today=date(2026, 8, 1))

    assert kpi.value == 0.0
    assert kpi.chart.has_data is False


def test_debt_kpi_floors_net_negative_at_zero(session):
    session.add(Debt(
        kind=DebtKind.INFORMAL, direction=DebtDirection.OWED_TO_US,
        original_amount=5000.0, current_balance=Decimal("5000.00"),
    ))
    session.commit()

    kpi = get_debt_kpi(session, today=date(2026, 8, 1))

    assert kpi.value == 0.0  # Ruling R10 -- never shown as a negative "debt"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_overview_service.py -k debt_kpi -v`
Expected: FAIL — `get_debt_kpi` doesn't exist yet.

- [ ] **Step 3: Implement**

```python
# app/services/overview_service.py — add import and function

from app.models.debt import Debt, DebtDirection, DebtKind


def _debt_net_position(session: Session) -> float:
    debts = session.exec(select(Debt)).all()
    total = 0.0
    for d in debts:
        balance = float(d.current_balance)
        if d.kind == DebtKind.FORMAL or d.direction == DebtDirection.OWED_BY_US:
            total += balance
        elif d.direction == DebtDirection.OWED_TO_US:
            total -= balance
    return max(0.0, total)  # Ruling R10


def get_debt_kpi(session: Session, today: date) -> KpiCard:
    value = _debt_net_position(session)
    # No balance-history tracking exists for Debt yet (Ruling R2) -- an
    # empty monthly dict makes build_trend_chart render its already-tested
    # "no history yet" empty state, no special-casing needed here.
    chart = build_trend_chart({}, today, value)
    return KpiCard(label="Debt", value=value, color="red", drill_down_url="/transactions/needs-review", chart=chart)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_overview_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/overview_service.py tests/test_overview_service.py
git commit -m "feat: add Debt KPI net-position aggregation with graceful empty history"
```

---

### Task 8: `overview_service.py` — Yearly Commitments progress card

**Files:**
- Modify: `app/services/overview_service.py`
- Test: `tests/test_overview_service.py`

**Interfaces:**
- Produces: `YearlyCommitmentCard(planned_total, actual_total, pct_of_plan: Optional[float], pct_of_year_elapsed: float, next_item_label: Optional[str], next_item_date: Optional[date], next_item_url: Optional[str], drill_down_url: str, has_commitments: bool)`; `get_yearly_commitments_card(session: Session, today: date) -> YearlyCommitmentCard`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_overview_service.py — append
from app.models.commitment import Cadence, Commitment
from app.services.overview_service import get_yearly_commitments_card


def test_yearly_commitments_progress_and_next_item(session):
    imi = Commitment(name="IMI", cadence=Cadence.YEARLY, planned_amount=600.0, year=2026, next_due_date=date(2026, 11, 30))
    vacation = Commitment(name="Vacation", cadence=Cadence.YEARLY, planned_amount=2000.0, year=2026, next_due_date=date(2026, 7, 1))
    session.add_all([imi, vacation])
    session.commit()
    session.refresh(imi)
    session.refresh(vacation)

    document = _doc(session)
    t1 = _txn(session, document, "AT IMI 1st installment", 300.0, TransactionType.DEBIT, date(2026, 4, 30))
    t1.commitment_id = imi.id
    session.add(t1)
    session.commit()

    card = get_yearly_commitments_card(session, today=date(2026, 6, 1))

    assert card.has_commitments is True
    assert card.planned_total == 2600.0
    assert card.actual_total == 300.0
    assert card.pct_of_plan == round(300.0 / 2600.0 * 100.0, 1)
    assert card.pct_of_year_elapsed == round(152 / 365 * 100.0, 1)  # day 152 of 2026 (not a leap year)
    assert card.next_item_label == "IMI"
    assert card.next_item_date == date(2026, 11, 30)
    assert card.next_item_url == f"/transactions?commitment_id={imi.id}"
    assert card.drill_down_url == "/transactions?date_from=2026-01-01&date_to=2026-12-31"


def test_yearly_commitments_empty_state(session):
    card = get_yearly_commitments_card(session, today=date(2026, 6, 1))

    assert card.has_commitments is False
    assert card.planned_total == 0.0
    assert card.pct_of_plan is None
    assert card.next_item_label is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_overview_service.py -k yearly_commitments -v`
Expected: FAIL — `get_yearly_commitments_card` doesn't exist yet.

- [ ] **Step 3: Implement**

```python
# app/services/overview_service.py — add import and code

from app.models.commitment import Cadence, Commitment


@dataclass
class YearlyCommitmentCard:
    planned_total: float
    actual_total: float
    pct_of_plan: Optional[float]
    pct_of_year_elapsed: float
    next_item_label: Optional[str]
    next_item_date: Optional[date]
    next_item_url: Optional[str]
    drill_down_url: str
    has_commitments: bool


def get_yearly_commitments_card(session: Session, today: date) -> YearlyCommitmentCard:
    year = today.year
    commitments = session.exec(
        select(Commitment).where(Commitment.cadence == Cadence.YEARLY, Commitment.year == year)
    ).all()
    planned_total = sum(c.planned_amount for c in commitments)

    commitment_ids = [c.id for c in commitments]
    actual_total = 0.0
    if commitment_ids:
        linked = session.exec(select(Transaction).where(Transaction.commitment_id.in_(commitment_ids))).all()
        actual_total = sum(t.amount for t in linked)

    day_of_year = (today - date(year, 1, 1)).days + 1
    days_in_year = (date(year + 1, 1, 1) - date(year, 1, 1)).days
    pct_of_year_elapsed = round(day_of_year / days_in_year * 100.0, 1)
    pct_of_plan = round(actual_total / planned_total * 100.0, 1) if planned_total else None

    upcoming = sorted(
        (c for c in commitments if c.next_due_date and c.next_due_date >= today),
        key=lambda c: c.next_due_date,
    )
    next_commitment = upcoming[0] if upcoming else None

    return YearlyCommitmentCard(
        planned_total=planned_total,
        actual_total=actual_total,
        pct_of_plan=pct_of_plan,
        pct_of_year_elapsed=pct_of_year_elapsed,
        next_item_label=next_commitment.name if next_commitment else None,
        next_item_date=next_commitment.next_due_date if next_commitment else None,
        next_item_url=f"/transactions?commitment_id={next_commitment.id}" if next_commitment else None,
        drill_down_url=f"/transactions?date_from={date(year, 1, 1).isoformat()}&date_to={date(year, 12, 31).isoformat()}",
        has_commitments=bool(commitments),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_overview_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/overview_service.py tests/test_overview_service.py
git commit -m "feat: add Yearly Commitments progress-bar aggregation"
```

---

### Task 9: `overview_service.py` — Category comparison table

**Files:**
- Modify: `app/services/overview_service.py`
- Test: `tests/test_overview_service.py`

**Interfaces:**
- Produces: `CategoryComparisonRow(category: str, current_value: float, rolling_avg_value: float, delta_pct: Optional[float], bar_pct: float, drill_down_url: str)`; `get_category_comparison(session: Session, today: date) -> list[CategoryComparisonRow]`, sorted by `current_value` descending, excluding rows that are zero in both current and rolling-average (nothing to show).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_overview_service.py — append
from app.services.overview_service import get_category_comparison


def test_category_comparison_ranked_with_delta(session):
    document = _doc(session)
    # This month: groceries 100, restaurants 50
    _txn(session, document, "CONTINENTE", 100.0, TransactionType.DEBIT, date(2026, 8, 5), Category.GROCERIES)
    _txn(session, document, "RESTAURANT A", 50.0, TransactionType.DEBIT, date(2026, 8, 6), Category.RESTAURANTS)
    # Prior 3 months: groceries averages to 80, restaurants to 100
    for m, amt in [(7, 90.0), (6, 80.0), (5, 70.0)]:
        _txn(session, document, "CONTINENTE", amt, TransactionType.DEBIT, date(2026, m, 5), Category.GROCERIES)
    for m, amt in [(7, 100.0), (6, 100.0), (5, 100.0)]:
        _txn(session, document, "RESTAURANT A", amt, TransactionType.DEBIT, date(2026, m, 6), Category.RESTAURANTS)

    rows = get_category_comparison(session, today=date(2026, 8, 10))

    by_cat = {r.category: r for r in rows}
    assert by_cat["groceries"].current_value == 100.0
    assert by_cat["groceries"].rolling_avg_value == 80.0
    assert by_cat["groceries"].delta_pct == 25.0
    assert by_cat["restaurants"].current_value == 50.0
    assert by_cat["restaurants"].rolling_avg_value == 100.0
    assert by_cat["restaurants"].delta_pct == -50.0
    # Ranked by current value descending.
    assert [r.category for r in rows] == ["groceries", "restaurants"]
    assert by_cat["groceries"].bar_pct == 100.0
    assert by_cat["restaurants"].bar_pct == 50.0
    assert by_cat["groceries"].drill_down_url == "/transactions?category=groceries&date_from=2026-08-01&date_to=2026-08-10"


def test_category_comparison_excludes_transfers_and_atm(session):
    document = _doc(session)
    _txn(session, document, "REVOLUT TOPUP", 200.0, TransactionType.TRANSFER, date(2026, 8, 5), Category.TRANSFER)
    _txn(session, document, "ATM", 40.0, TransactionType.DEBIT, date(2026, 8, 5), Category.ATM_WITHDRAWAL)

    rows = get_category_comparison(session, today=date(2026, 8, 10))

    assert rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_overview_service.py -k category_comparison -v`
Expected: FAIL — `get_category_comparison` doesn't exist yet.

- [ ] **Step 3: Implement**

```python
# app/services/overview_service.py — add import and code

from app.models.transaction import Category

_COMPARISON_ROLLING_MONTHS = 3
_COMPARISON_EXCLUDED_CATEGORIES = (Category.TRANSFER, Category.ATM_WITHDRAWAL, Category.INCOME)


@dataclass
class CategoryComparisonRow:
    category: str
    current_value: float
    rolling_avg_value: float
    delta_pct: Optional[float]
    bar_pct: float
    drill_down_url: str


def get_category_comparison(session: Session, today: date) -> list[CategoryComparisonRow]:
    cutoff = _month_start(today) - timedelta(days=31 * (_COMPARISON_ROLLING_MONTHS + 1))
    statement = select(Transaction).where(
        Transaction.transaction_type == TransactionType.DEBIT,
        Transaction.category.notin_(_COMPARISON_EXCLUDED_CATEGORIES),
        Transaction.paid_date >= cutoff,
        Transaction.paid_date <= today,
    )

    per_month_category: dict[tuple[str, str], float] = {}
    for t in session.exec(statement):
        if t.paid_date is None:
            continue
        key = (_month_key(t.paid_date), t.category.value)
        per_month_category[key] = per_month_category.get(key, 0.0) + t.amount

    current_key = _month_key(today)
    history_periods = _complete_months_before(today, _COMPARISON_ROLLING_MONTHS)
    categories = {cat for (_, cat) in per_month_category}

    computed = []
    for cat in categories:
        current_value = per_month_category.get((current_key, cat), 0.0)
        history_values = [per_month_category.get((p, cat), 0.0) for p in history_periods]
        rolling_avg = sum(history_values) / len(history_values) if history_values else 0.0
        if current_value == 0.0 and rolling_avg == 0.0:
            continue
        delta_pct = round((current_value - rolling_avg) / rolling_avg * 100.0, 1) if rolling_avg else None
        computed.append((cat, current_value, rolling_avg, delta_pct))

    computed.sort(key=lambda r: r[1], reverse=True)
    max_value = max((r[1] for r in computed), default=0.0)
    month_start = _month_start(today).isoformat()
    today_iso = today.isoformat()

    return [
        CategoryComparisonRow(
            category=cat, current_value=current_value, rolling_avg_value=rolling_avg,
            delta_pct=delta_pct,
            bar_pct=round(current_value / max_value * 100.0, 1) if max_value else 0.0,
            drill_down_url=f"/transactions?category={cat}&date_from={month_start}&date_to={today_iso}",
        )
        for cat, current_value, rolling_avg, delta_pct in computed
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_overview_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/overview_service.py tests/test_overview_service.py
git commit -m "feat: add merged category-comparison table aggregation"
```

---

### Task 10: `overview_service.py` — Narrative insight

**Files:**
- Modify: `app/services/overview_service.py`
- Test: `tests/test_overview_service.py`

**Interfaces:**
- Consumes: `list[CategoryComparisonRow]` from Task 9.
- Produces: `get_narrative_insight(category_rows: list[CategoryComparisonRow]) -> Optional[str]` — a single deterministic sentence, or `None` when there isn't enough data to say anything meaningful. Rule-based per Global Constraint / this task's own note — **not** an LLM call (see rationale in the docstring below).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_overview_service.py — append
from app.services.overview_service import CategoryComparisonRow, get_narrative_insight


def test_narrative_insight_names_biggest_drop_and_rise():
    rows = [
        CategoryComparisonRow("travel", current_value=0.0, rolling_avg_value=600.0, delta_pct=-100.0, bar_pct=0.0, drill_down_url=""),
        CategoryComparisonRow("restaurants", current_value=220.0, rolling_avg_value=100.0, delta_pct=120.0, bar_pct=100.0, drill_down_url=""),
        CategoryComparisonRow("groceries", current_value=300.0, rolling_avg_value=300.0, delta_pct=0.0, bar_pct=100.0, drill_down_url=""),
    ]
    # total_current = 520, total_avg = 1000 -> -48.0% overall

    sentence = get_narrative_insight(rows)

    assert sentence is not None
    assert "fell 48.0%" in sentence
    assert "Travel" in sentence
    assert "Restaurants" in sentence


def test_narrative_insight_none_when_no_history():
    assert get_narrative_insight([]) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_overview_service.py -k narrative -v`
Expected: FAIL — `get_narrative_insight` doesn't exist yet.

- [ ] **Step 3: Implement**

```python
# app/services/overview_service.py — add

def get_narrative_insight(category_rows: list[CategoryComparisonRow]) -> Optional[str]:
    """A single deterministic sentence: overall spend trend vs. the 3-month
    average, plus whichever category moved the most in each direction.
    Rule-based, not an LLM call -- this runs on every home-page load, and
    an LLM round-trip there would add latency/cost/flakiness to the
    highest-traffic page in the app for no benefit a templated sentence
    over already-computed numbers doesn't already give."""
    if not category_rows:
        return None

    total_current = sum(r.current_value for r in category_rows)
    total_avg = sum(r.rolling_avg_value for r in category_rows)
    if not total_avg:
        return None

    change_pct = (total_current - total_avg) / total_avg * 100.0
    direction = "fell" if change_pct < 0 else "rose"
    sentence = f"Your spending {direction} {abs(round(change_pct, 1))}% vs. your 3-month average."

    deltas = sorted(
        ((r.category, r.current_value - r.rolling_avg_value) for r in category_rows),
        key=lambda d: d[1],
    )
    biggest_drop = deltas[0] if deltas[0][1] < 0 else None
    drop_category = biggest_drop[0] if biggest_drop else None
    biggest_rise = deltas[-1] if deltas[-1][1] > 0 and deltas[-1][0] != drop_category else None

    def _title(cat: str) -> str:
        return cat.replace("_", " ").title()

    if biggest_drop and biggest_rise:
        sentence += (
            f" {_title(biggest_drop[0])} accounted for €{abs(round(biggest_drop[1])):,.0f} of the "
            f"reduction, partly offset by €{round(biggest_rise[1]):,.0f} more in {_title(biggest_rise[0])}."
        )
    elif biggest_drop:
        sentence += f" {_title(biggest_drop[0])} accounted for €{abs(round(biggest_drop[1])):,.0f} of the change."
    elif biggest_rise:
        sentence += f" {_title(biggest_rise[0])} accounted for €{round(biggest_rise[1]):,.0f} of the increase."
    return sentence
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_overview_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/overview_service.py tests/test_overview_service.py
git commit -m "feat: add rule-based narrative insight sentence"
```

---

### Task 11: `overview_service.py` — Needs Attention list + `get_overview_data()` orchestrator

**Files:**
- Modify: `app/services/overview_service.py`
- Test: `tests/test_overview_service.py`

**Interfaces:**
- Consumes: `get_needs_review_queue` from `app/services/classification_engine.py` (already merged, not modified); everything built in Tasks 5-10.
- Produces: `NeedsAttentionItem(kind: str, text: str, url: str)`; `get_needs_attention(session, today, category_rows) -> list[NeedsAttentionItem]`; `OverviewData(flow_kpis, position_kpis, yearly_commitments, narrative, cash_flow_chart, category_comparison, needs_attention)`; `get_overview_data(session: Session, today: Optional[date] = None, cash_flow_range: str = "12m") -> OverviewData` — the single entry point Task 12's router calls.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_overview_service.py — append
from app.models.document import DocumentStatus
from app.models.merchant import Merchant
from app.services.overview_service import get_needs_attention, get_overview_data


def test_needs_attention_combines_review_queue_upcoming_bill_anomaly_and_documents(session):
    # Review queue: one unconfirmed merchant.
    session.add(Merchant(canonical_name="New Shop", normalized_key="new shop", confirmed=False))
    # Upcoming bill within the lookahead window.
    commitment = Commitment(
        name="Car insurance", cadence=Cadence.YEARLY, planned_amount=400.0,
        year=2026, next_due_date=date(2026, 8, 20),
    )
    session.add(commitment)
    # A needs-attention document.
    session.add(Document(
        filename="bad.pdf", file_path="/tmp/bad.pdf", content_hash="hbad",
        source=DocumentSource.MANUAL, status=DocumentStatus.NEEDS_ATTENTION, failure_reason="unreadable",
    ))
    session.commit()
    session.refresh(commitment)

    category_rows = [
        CategoryComparisonRow("shopping", current_value=200.0, rolling_avg_value=100.0, delta_pct=100.0, bar_pct=100.0, drill_down_url="/transactions?category=shopping"),
    ]

    items = get_needs_attention(session, today=date(2026, 8, 10), category_rows=category_rows)
    kinds = {i.kind for i in items}

    assert "review_queue" in kinds
    assert "upcoming_bill" in kinds
    assert "category_anomaly" in kinds
    assert "document" in kinds

    upcoming = next(i for i in items if i.kind == "upcoming_bill")
    assert upcoming.url == f"/transactions?commitment_id={commitment.id}"
    doc_item = next(i for i in items if i.kind == "document")
    assert doc_item.url.startswith("/bills/")


def test_needs_attention_skips_small_anomalies_below_floor():
    category_rows = [
        CategoryComparisonRow("shopping", current_value=10.0, rolling_avg_value=5.0, delta_pct=100.0, bar_pct=100.0, drill_down_url=""),
    ]
    # rolling_avg_value (5.0) is below the €30 noise floor -- must not fire.
    items = get_needs_attention(None, today=date(2026, 8, 10), category_rows=category_rows)
    assert all(i.kind != "category_anomaly" for i in items)


def test_get_overview_data_end_to_end(session):
    data = get_overview_data(session, today=date(2026, 8, 10))

    assert [k.label for k in data.flow_kpis] == ["Income", "Expenses", "Net flow"]
    assert [k.label for k in data.position_kpis] == ["Cash", "Debt"]
    assert data.yearly_commitments.has_commitments is False
    assert data.narrative is None  # no transaction history in this empty DB
    assert data.cash_flow_chart.range_key == "12m"
    assert data.category_comparison == []
    assert isinstance(data.needs_attention, list)
```

Note: `test_needs_attention_skips_small_anomalies_below_floor` passes `session=None` deliberately — with an empty `category_rows` average below the floor, `get_needs_attention` must never reach a code path that queries the database for that category, so `None` never gets touched. If the implementation below ever needs the session for something unconditional, restructure so the review-queue/document/commitment queries stay independent of the anomaly check's early-out (they already are, per the implementation's ordering).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_overview_service.py -k "needs_attention or overview_data" -v`
Expected: FAIL — `get_needs_attention`/`get_overview_data` don't exist yet.

- [ ] **Step 3: Implement**

```python
# app/services/overview_service.py — add imports and code

from app.models.document import Document, DocumentStatus
from app.services.classification_engine import get_needs_review_queue

_UPCOMING_BILL_LOOKAHEAD_DAYS = 14
_ANOMALY_THRESHOLD_PCT = 40.0
_ANOMALY_MIN_AVERAGE = 30.0


@dataclass
class NeedsAttentionItem:
    kind: str
    text: str
    url: str


def get_needs_attention(
    session: Session, today: date, category_rows: list[CategoryComparisonRow]
) -> list[NeedsAttentionItem]:
    items: list[NeedsAttentionItem] = []

    # Category anomalies never need the database -- checked first so the
    # test that passes session=None with a below-floor average never
    # touches `session` at all.
    for row in category_rows:
        if (
            row.rolling_avg_value >= _ANOMALY_MIN_AVERAGE
            and row.delta_pct is not None
            and row.delta_pct >= _ANOMALY_THRESHOLD_PCT
        ):
            items.append(NeedsAttentionItem(
                kind="category_anomaly",
                text=f"{row.category.replace('_', ' ').title()} is {row.delta_pct:.0f}% above its 3-month average this month",
                url=row.drill_down_url,
            ))

    if session is None:
        return items

    queue = get_needs_review_queue(session)
    review_count = (
        len(queue.unconfirmed_merchants) + len(queue.recurring_candidates)
        + len(queue.debt_candidates) + len(queue.unclassified_transactions)
    )
    if review_count:
        items.append(NeedsAttentionItem(
            kind="review_queue",
            text=f"{review_count} item{'s' if review_count != 1 else ''} waiting in Needs Review",
            url="/transactions/needs-review",
        ))

    lookahead = today + timedelta(days=_UPCOMING_BILL_LOOKAHEAD_DAYS)
    upcoming = session.exec(
        select(Commitment).where(
            Commitment.next_due_date.is_not(None),
            Commitment.next_due_date >= today,
            Commitment.next_due_date <= lookahead,
        )
    ).all()
    for c in upcoming:
        items.append(NeedsAttentionItem(
            kind="upcoming_bill",
            text=f"{c.name} due {c.next_due_date.strftime('%d %b')} (€{c.planned_amount:,.2f})",
            url=f"/transactions?commitment_id={c.id}",
        ))

    needs_attention_documents = session.exec(
        select(Document).where(Document.status.in_([DocumentStatus.NEEDS_ATTENTION, DocumentStatus.PENDING]))
    ).all()
    for doc in needs_attention_documents:
        items.append(NeedsAttentionItem(
            kind="document",
            text=f"{doc.filename} — {doc.failure_reason or 'needs attention'}",
            url=f"/bills/{doc.id}",
        ))

    return items


@dataclass
class OverviewData:
    flow_kpis: list[KpiCard]
    position_kpis: list[KpiCard]
    yearly_commitments: YearlyCommitmentCard
    narrative: Optional[str]
    cash_flow_chart: "CashFlowChart"
    category_comparison: list[CategoryComparisonRow]
    needs_attention: list[NeedsAttentionItem]


def get_overview_data(
    session: Session, today: Optional[date] = None, cash_flow_range: str = "12m"
) -> OverviewData:
    today = today or date.today()
    income_monthly, expense_monthly = _monthly_flow_totals(session, today)
    category_rows = get_category_comparison(session, today)

    from app.services.overview_charts import build_cash_flow_chart  # local import avoids a module-load cycle with the CashFlowChart type hint above

    return OverviewData(
        flow_kpis=get_flow_kpis(session, today, income_monthly, expense_monthly),
        position_kpis=[get_cash_kpi(session, today), get_debt_kpi(session, today)],
        yearly_commitments=get_yearly_commitments_card(session, today),
        narrative=get_narrative_insight(category_rows),
        cash_flow_chart=build_cash_flow_chart(income_monthly, expense_monthly, cash_flow_range, today),
        category_comparison=category_rows,
        needs_attention=get_needs_attention(session, today, category_rows),
    )
```

Reviewer note for this task: the local `from app.services.overview_charts import build_cash_flow_chart` inside `get_overview_data` is there only to dodge a forward-reference issue with the `"CashFlowChart"` string type hint on `OverviewData` — the cleaner fix is a top-level `from app.services.overview_charts import CashFlowChart, TrendChart, build_trend_chart, build_cash_flow_chart` (all four names) at the top of the file instead of the string-quoted hint and local import. Prefer that top-level form when implementing; the string-hint/local-import version above is written out only so the diff before this note is unambiguous about which names are needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_overview_service.py -v`
Expected: PASS (full file)

- [ ] **Step 5: Commit**

```bash
git add app/services/overview_service.py tests/test_overview_service.py
git commit -m "feat: add Needs Attention aggregation and get_overview_data orchestrator"
```

---

### Task 12: Router wiring — `GET /` and `GET /cash-flow-chart` htmx partial

**Files:**
- Modify: `app/routers/dashboard.py`
- Test: `tests/test_dashboard_router.py`

**Interfaces:**
- Consumes: `get_overview_data` (Task 11), `get_household_data` (Task 4).
- Produces: `GET /` renders `dashboard.html` with `overview` and `household` in context; `GET /cash-flow-chart?range=<key>` renders `dashboard/_cash_flow_chart.html` alone (htmx partial swap target), reusing `get_overview_data`'s cash-flow-only inputs without recomputing the whole page. This task's template still refers to the Task-4-trimmed `dashboard.html` — Task 13 replaces it with the full layout that actually uses `overview`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_router.py — append
def test_cash_flow_chart_partial_route_responds(client, session):
    response = client.get("/cash-flow-chart", params={"range": "6m"})

    assert response.status_code == 200
    # The partial re-render must not include the full page chrome.
    assert "<html" not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_dashboard_router.py -k cash_flow_chart_partial -v`
Expected: FAIL — 404, route doesn't exist yet.

- [ ] **Step 3: Implement**

```python
# app/routers/dashboard.py
"""Dashboard home route and health check."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db import get_session
from app.services.household_service import get_household_data
from app.services.overview_service import get_overview_data

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/")
async def dashboard(request: Request, session: Session = Depends(get_session)):
    overview = get_overview_data(session)
    household = get_household_data(session)
    return templates.TemplateResponse(
        request, "dashboard.html", {"overview": overview, "household": household},
    )


@router.get("/cash-flow-chart")
async def cash_flow_chart(request: Request, range: str = "12m", session: Session = Depends(get_session)):
    overview = get_overview_data(session, cash_flow_range=range)
    return templates.TemplateResponse(
        request, "dashboard/_cash_flow_chart.html", {"chart": overview.cash_flow_chart},
    )
```

Note: `range` shadows the Python builtin as a FastAPI query-param name — this is fine and scoped to the function signature (consistent with e.g. `list`/`id` shadowing that's common and harmless in route handlers); it is not reused as the builtin inside this function's body.

`dashboard/_cash_flow_chart.html` doesn't exist until Task 13 — this task's test will still fail at Step 2/pass at Step 4 only once Task 13's template exists too. **Sequencing note:** do Task 13 before running Step 4's verification here, or accept that this task's test goes green only after Task 13 lands. Given the two are tightly coupled (a router route and the template it renders), the pragmatic path is to implement Task 12 and Task 13 back-to-back in the same work session and run this test at the end of Task 13, not at the end of Task 12. Record in the task ledger that Task 12's own test run is deferred to immediately after Task 13.

- [ ] **Step 4: Commit (template arrives in Task 13; do not run pytest yet if Task 13 hasn't landed)**

```bash
git add app/routers/dashboard.py tests/test_dashboard_router.py
git commit -m "feat: wire GET / and GET /cash-flow-chart to overview_service"
```

---

### Task 13: Templates — full Overview layout

**Files:**
- Modify: `app/templates/base.html` (new CSS)
- Modify: `app/templates/dashboard.html` (full rewrite)
- Create: `app/templates/dashboard/_kpi_card.html`
- Create: `app/templates/dashboard/_yearly_commitments_card.html`
- Create: `app/templates/dashboard/_cash_flow_chart.html`
- Create: `app/templates/dashboard/_category_comparison.html`
- Create: `app/templates/dashboard/_needs_attention.html`
- Create: `app/templates/dashboard/_household.html`
- Test: `tests/test_dashboard_router.py`

**Interfaces:**
- Consumes: `overview: OverviewData` and `household: HouseholdData` in the `dashboard.html` context (from Task 12).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_router.py — append
from datetime import date

from app.models.account import Account, AccountType
from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Transaction, TransactionType


def test_overview_page_renders_kpi_cards_and_sections(client, session):
    account = Account(name="Santander", institution="Santander", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)

    document = Document(
        filename="s.pdf", file_path="/tmp/s.pdf", content_hash="hh",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    t = Transaction(
        document_id=document.id, provider="SALARIO", category=Category.INCOME,
        transaction_type=TransactionType.CREDIT, amount=2000.0, currency="EUR",
        account_id=account.id, paid_date=date.today(),
    )
    session.add(t)
    session.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert "Income" in response.text
    assert "Expenses" in response.text
    assert "Net flow" in response.text
    assert "Cash" in response.text
    assert "Debt" in response.text
    assert "Yearly" in response.text
    assert "Needs Attention" in response.text
    assert "Household" in response.text


def test_overview_page_kpi_drill_down_links_present(client, session):
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/transactions?category=income' in response.text
    assert 'href="/transactions?transaction_type=debit' in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_router.py -k overview_page -v`
Expected: FAIL — current `dashboard.html` (from Task 4) is still the trimmed placeholder; none of these strings appear.

- [ ] **Step 3: Add CSS to `base.html`**

```html
<!-- app/templates/base.html — inside the existing <style> block, append -->
    .overview-page { max-width: 80rem; width: 80vw; margin: 0 auto; }
    .kpi-groups { display: flex; gap: 2rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
    .kpi-group { flex: 1; min-width: 20rem; }
    .kpi-group h2 { font-size: 0.85rem; text-transform: uppercase; color: #666; margin-bottom: 0.5rem; }
    .kpi-cards { display: flex; gap: 1rem; flex-wrap: wrap; }
    .kpi-card { flex: 1; min-width: 9rem; border: 1px solid #e0e0e0; border-radius: 6px; padding: 0.75rem; }
    .kpi-card-label { font-size: 0.7rem; text-transform: uppercase; color: #888; display: flex; justify-content: space-between; }
    .kpi-card-value { font-size: 23px; font-weight: bold; margin: 0.25rem 0 0.75rem; }
    .kpi-mini-chart { display: flex; align-items: flex-end; height: 4rem; gap: 3px; border-top: 1px solid #ccc; }
    .kpi-mini-chart.bidirectional { align-items: stretch; }
    .kpi-mini-chart-col { flex: 1; display: flex; flex-direction: column; align-items: center; font-size: 9px; color: #999; }
    .kpi-bar-zone-up { flex: 1; display: flex; align-items: flex-end; width: 100%; }
    .kpi-bar-zone-down { flex: 1; display: flex; align-items: flex-start; width: 100%; }
    .kpi-bar { width: 100%; min-height: 2px; }
    .kpi-bar.green { background: #2e7d32; }
    .kpi-bar.red { background: #b00020; }
    .kpi-bar.now { background-image: repeating-linear-gradient(45deg, currentColor 0 4px, transparent 4px 8px); border: 1px solid currentColor; background-color: transparent; color: inherit; }
    .kpi-bar.now.green { color: #2e7d32; }
    .kpi-bar.now.red { color: #b00020; }
    .kpi-mini-chart-col.distant { background: #f2f2f2; padding-top: 0.25rem; }
    .kpi-no-history { font-size: 0.75rem; color: #999; padding: 1rem 0; text-align: center; border-top: 1px solid #ccc; }
    .kpi-card-link { font-size: 0.75rem; text-decoration: none; color: #3a6ea5; }
    .yearly-commitments-card { border: 1px solid #e0e0e0; border-radius: 6px; padding: 0.75rem; }
    .progress-track { background: #f0f0f0; height: 1rem; border-radius: 4px; overflow: hidden; margin: 0.5rem 0; }
    .progress-fill { background: #3a6ea5; height: 100%; }
    .progress-marker { border-left: 2px solid #b00020; height: 1rem; margin-top: -1rem; }
    .narrative-banner { background: #eef4fb; border-radius: 6px; padding: 1rem; margin-bottom: 1.5rem; font-size: 1rem; }
    .range-pills { display: flex; gap: 0.5rem; margin-bottom: 0.5rem; }
    .range-pills a { font-size: 0.8rem; padding: 0.15rem 0.6rem; border: 1px solid #ccc; border-radius: 12px; text-decoration: none; color: #333; }
    .range-pills a.active { background: #3a6ea5; color: #fff; border-color: #3a6ea5; }
    .cash-flow-chart { display: flex; align-items: flex-end; gap: 6px; height: 8rem; border-top: 1px solid #ccc; width: 100%; }
    .cash-flow-month { flex: 1; display: flex; gap: 2px; align-items: flex-end; }
    .cash-flow-bar { flex: 1; min-height: 1px; }
    .cash-flow-bar.income { background: #2e7d32; }
    .cash-flow-bar.expense { background: #b00020; }
    .category-comparison-table { width: 100%; border-collapse: collapse; }
    .category-comparison-table td, .category-comparison-table th { padding: 0.35rem 0.5rem; text-align: left; font-size: 0.9rem; }
    .category-bar-track { background: #f0f0f0; height: 0.75rem; width: 6rem; display: inline-block; }
    .category-bar-fill { background: #3a6ea5; height: 100%; }
```

- [ ] **Step 4: Create `dashboard/_kpi_card.html`**

```html
{% macro kpi_card(card) %}
<div class="kpi-card">
  <div class="kpi-card-label">
    <span>{{ card.label }}</span>
    <a class="kpi-card-link" href="{{ card.drill_down_url }}">↗</a>
  </div>
  <div class="kpi-card-value">€{{ "{:,.0f}".format(card.value) }}</div>
  {% if card.chart.has_data %}
  <div class="kpi-mini-chart {{ 'bidirectional' if card.chart.bidirectional else '' }}">
    {% for point in card.chart.points %}
    <div class="kpi-mini-chart-col {{ 'distant' if point.is_distant else '' }}" title="{{ point.label }}: €{{ '{:,.0f}'.format(point.value) }}">
      {% if card.chart.bidirectional %}
      <div class="kpi-bar-zone-up">
        {% if point.direction == 'up' %}<div class="kpi-bar {{ card.color }} {{ 'now' if point.is_now else '' }}" style="height: {{ point.height_pct }}%;"></div>{% endif %}
      </div>
      <div class="kpi-bar-zone-down">
        {% if point.direction == 'down' %}<div class="kpi-bar {{ card.color }} {{ 'now' if point.is_now else '' }}" style="height: {{ point.height_pct }}%;"></div>{% endif %}
      </div>
      {% else %}
      <div class="kpi-bar-zone-up">
        <div class="kpi-bar {{ card.color }} {{ 'now' if point.is_now else '' }}" style="height: {{ point.height_pct }}%;"></div>
      </div>
      {% endif %}
      <span>{{ point.label }}</span>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="kpi-no-history">No history tracked yet</div>
  {% endif %}
</div>
{% endmacro %}
```

- [ ] **Step 5: Create `dashboard/_yearly_commitments_card.html`**

```html
{% macro yearly_commitments_card(card) %}
<div class="yearly-commitments-card">
  <div class="kpi-card-label">
    <span>Yearly Commitments</span>
    <a class="kpi-card-link" href="{{ card.drill_down_url }}">↗</a>
  </div>
  {% if card.has_commitments %}
  <div class="kpi-card-value">€{{ "{:,.0f}".format(card.actual_total) }} <small>of €{{ "{:,.0f}".format(card.planned_total) }}</small></div>
  <div class="progress-track">
    <div class="progress-fill" style="width: {{ [card.pct_of_plan, 100] | min }}%;"></div>
  </div>
  <p>{{ card.pct_of_plan }}% of plan consumed · {{ card.pct_of_year_elapsed }}% of year elapsed</p>
  {% if card.next_item_label %}
  <p><a href="{{ card.next_item_url }}">Next: {{ card.next_item_label }}, {{ card.next_item_date.strftime('%d %b') }}</a></p>
  {% endif %}
  {% else %}
  <p>No yearly commitments configured yet.</p>
  {% endif %}
</div>
{% endmacro %}
```

- [ ] **Step 6: Create `dashboard/_cash_flow_chart.html`** (also the htmx-swappable fragment for `GET /cash-flow-chart`)

```html
<div id="cash-flow-chart-container">
  <div class="range-pills">
    {% for key, label in [("1m","1M"), ("3m","3M"), ("6m","6M"), ("9m","9M"), ("12m","12M"), ("ytd","YTD")] %}
    <a
      class="{{ 'active' if chart.range_key == key else '' }}"
      href="#"
      hx-get="/cash-flow-chart?range={{ key }}"
      hx-target="#cash-flow-chart-container"
      hx-swap="outerHTML"
    >{{ label }}</a>
    {% endfor %}
  </div>
  <div class="cash-flow-chart">
    {% for month in chart.months %}
    <div class="cash-flow-month" title="{{ month.label }}: income €{{ '{:,.0f}'.format(month.income) }} / expense €{{ '{:,.0f}'.format(month.expense) }}">
      {% set income_pct = (month.income / chart.max_value * 100) if chart.max_value else 0 %}
      {% set expense_pct = (month.expense / chart.max_value * 100) if chart.max_value else 0 %}
      <div class="cash-flow-bar income" style="height: {{ income_pct }}%;"></div>
      <div class="cash-flow-bar expense" style="height: {{ expense_pct }}%;"></div>
    </div>
    {% endfor %}
  </div>
</div>
```

- [ ] **Step 7: Create `dashboard/_category_comparison.html`**

```html
<table class="category-comparison-table">
  <thead>
    <tr><th>Category</th><th></th><th>This month</th><th>3M avg</th><th>Δ</th></tr>
  </thead>
  <tbody>
    {% for row in rows %}
    <tr>
      <td><a href="{{ row.drill_down_url }}">{{ row.category.replace('_', ' ')|title }}</a></td>
      <td><span class="category-bar-track"><span class="category-bar-fill" style="width: {{ row.bar_pct }}%;"></span></span></td>
      <td>€{{ "{:,.0f}".format(row.current_value) }}</td>
      <td>€{{ "{:,.0f}".format(row.rolling_avg_value) }}</td>
      <td>{{ "{:+.1f}%".format(row.delta_pct) if row.delta_pct is not none else "—" }}</td>
    </tr>
    {% else %}
    <tr><td colspan="5">No category spend recorded yet.</td></tr>
    {% endfor %}
  </tbody>
</table>
```

- [ ] **Step 8: Create `dashboard/_needs_attention.html`**

```html
<ul>
  {% for item in items %}
  <li><a href="{{ item.url }}">{{ item.text }}</a></li>
  {% else %}
  <li>All clear.</li>
  {% endfor %}
</ul>
```

- [ ] **Step 9: Create `dashboard/_household.html`**

```html
<section>
  <h3>Open to-dos</h3>
  <ul>
    {% for todo in household.open_todos %}
      <li>{{ todo.title }} — due {{ todo.due_date }}</li>
    {% else %}
      <li>Nothing due.</li>
    {% endfor %}
  </ul>
</section>
<section>
  <h3>Recently changed wiki facts</h3>
  <ul>
    {% for page in household.recently_changed_wiki_pages %}
      <li><a href="/wiki/{{ page.id }}">{{ page.topic }}</a></li>
    {% else %}
      <li>No recent changes.</li>
    {% endfor %}
  </ul>
</section>
```

- [ ] **Step 10: Rewrite `dashboard.html`**

```html
{% extends "base.html" %}
{% import "dashboard/_kpi_card.html" as kpi %}
{% import "dashboard/_yearly_commitments_card.html" as yearly %}
{% block title %}Overview — Home & Family{% endblock %}
{% block content %}
<div class="overview-page">
  <h1>Overview</h1>

  <div class="kpi-groups">
    <div class="kpi-group">
      <h2>This month</h2>
      <div class="kpi-cards">
        {% for card in overview.flow_kpis %}{{ kpi.kpi_card(card) }}{% endfor %}
      </div>
    </div>
    <div class="kpi-group">
      <h2>Position</h2>
      <div class="kpi-cards">
        {% for card in overview.position_kpis %}{{ kpi.kpi_card(card) }}{% endfor %}
        {{ yearly.yearly_commitments_card(overview.yearly_commitments) }}
      </div>
    </div>
  </div>

  {% if overview.narrative %}
  <div class="narrative-banner">{{ overview.narrative }}</div>
  {% endif %}

  <section>
    <h2>Cash flow</h2>
    {% include "dashboard/_cash_flow_chart.html" with context %}
  </section>

  <section>
    <h2>Where it went</h2>
    {% with rows = overview.category_comparison %}
      {% include "dashboard/_category_comparison.html" %}
    {% endwith %}
  </section>

  <section>
    <h2 class="needs-attention">Needs Attention</h2>
    {% with items = overview.needs_attention %}
      {% include "dashboard/_needs_attention.html" %}
    {% endwith %}
  </section>

  <section>
    <h2>Household</h2>
    {% include "dashboard/_household.html" %}
  </section>
</div>
{% endblock %}
```

Note: `dashboard.html` sets `chart = overview.cash_flow_chart` implicitly by relying on `_cash_flow_chart.html`'s `{% include ... with context %}` seeing `overview` in scope — but `_cash_flow_chart.html` (Step 6) expects a top-level `chart` variable, not `overview.cash_flow_chart`. Fix this by including with an explicit `with`:

```html
  <section>
    <h2>Cash flow</h2>
    {% with chart = overview.cash_flow_chart %}
      {% include "dashboard/_cash_flow_chart.html" %}
    {% endwith %}
  </section>
```

Use this corrected form (not the plain `with context` version above) when writing the file.

- [ ] **Step 11: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard_router.py -v`
Expected: PASS (including Task 12's deferred `test_cash_flow_chart_partial_route_responds`)

- [ ] **Step 12: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS, 0 failures

- [ ] **Step 13: Commit**

```bash
git add app/templates/base.html app/templates/dashboard.html app/templates/dashboard/ tests/test_dashboard_router.py
git commit -m "feat: build full Overview screen layout (KPI cards, cash-flow chart, category table, needs attention, household panel)"
```

---

### Task 14: Final end-to-end coverage pass

**Files:**
- Modify: `tests/test_dashboard_router.py`

**Interfaces:** none new — this task only adds coverage for interaction paths not yet exercised end-to-end (multi-account cash aggregation reaching the rendered page, an empty-database first-run render, the range-pill htmx swap actually changing content).

- [ ] **Step 1: Write the additional tests**

```python
# tests/test_dashboard_router.py — append
def test_overview_page_renders_cleanly_with_empty_database(client, session):
    response = client.get("/")

    assert response.status_code == 200
    assert "No yearly commitments configured yet." in response.text
    assert "No history tracked yet" in response.text  # Debt KPI, no Debt rows
    assert "No category spend recorded yet." in response.text
    assert "All clear." in response.text


def test_cash_flow_range_pill_swap_changes_content(client, session):
    document = Document(
        filename="s.pdf", file_path="/tmp/s.pdf", content_hash="hh2", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    session.add(Transaction(
        document_id=document.id, provider="SALARIO", category=Category.INCOME,
        transaction_type=TransactionType.CREDIT, amount=2000.0, currency="EUR",
        paid_date=date(2025, 1, 15),
    ))
    session.commit()

    full_year = client.get("/cash-flow-chart", params={"range": "ytd"})
    twelve_months = client.get("/cash-flow-chart", params={"range": "12m"})

    assert full_year.status_code == 200
    assert twelve_months.status_code == 200
    assert full_year.text != twelve_months.text
```

- [ ] **Step 2: Run tests to verify they fail or pass**

Run: `python3 -m pytest tests/test_dashboard_router.py -v`
Expected: These should already PASS if Tasks 1-13 were implemented correctly — this task is a coverage check, not new functionality. If any fail, that's a real bug surfaced late; fix it in the owning task's file (overview_service.py or the templates), not by weakening the test.

- [ ] **Step 3: Run the entire test suite one final time**

Run: `python3 -m pytest -q`
Expected: PASS, 0 failures, no warnings about missing fixtures/imports

- [ ] **Step 4: Commit**

```bash
git add tests/test_dashboard_router.py
git commit -m "test: add empty-database and range-pill end-to-end coverage for the Overview screen"
```

---

## Self-Review

**1. Spec coverage** — walking every subsection of "The Overview screen — validated design":
- Layout (two KPI groups, page width cap, banner/chart/table/needs-attention order) → Task 13, `dashboard.html`.
- KPI cards mechanic (big value, mini chart, 7 columns, auto-scale+floor, zero-axis, Now styling, 18/24M highlight box, hover tooltip, color-by-favorability, Debt-as-positive-magnitude) → Task 2 (`build_trend_chart`), Tasks 5-7 (data), Task 13 (`_kpi_card.html` CSS/markup).
- Yearly Commitments progress-bar mechanic (actual vs. planned, % of plan vs. % of year elapsed, next item) → Task 8, Task 13 (`_yearly_commitments_card.html`).
- Narrative insight banner → Task 10.
- Cash-flow chart (12-month grouped bars, range pills, responsive width) → Task 3, Task 12 (partial route), Task 13 (`_cash_flow_chart.html`, `width: 100%` container).
- Merged category-comparison table → Task 9, Task 13 (`_category_comparison.html`).
- Needs Attention (review queue, upcoming bill, recurring confirmation, category anomaly) → Task 11 (recurring confirmation folded into the review-queue count per Ruling R5).
- Interaction model / drill-down-to-Transactions → Task 1 (filter extension), drill-down URLs threaded through every KPI/row/item in Tasks 5-11.
- "Not yet decided / explicitly deferred" items (Debt full schema, yearly-cadence tagging mechanism, later screens, chart-library-vs-HTML/CSS question) are explicitly NOT built here — the HTML/CSS question is resolved in favor of no new dependency (Global Constraint 2), and the Debt/yearly-tagging items are correctly left alone (Rulings R1, R2).

**2. Placeholder scan** — every task above contains complete, runnable code for both test and implementation steps; no "TBD"/"similar to Task N"/"add error handling" placeholders appear. The two spots that look like caveats (Task 11's forward-reference note, Task 13's `with context` correction) are not placeholders — they are explicit, fully-specified alternate/corrected code the implementer is told exactly which version to use.

**3. Type consistency** — traced across tasks:
- `TrendChart`/`TrendPoint` (Task 2) are consumed unchanged by `build_trend_chart` callers in Tasks 5, 6, 7 and rendered by the exact field names (`has_data`, `bidirectional`, `points[].label/value/is_now/is_distant/direction/height_pct`) in Task 13's `_kpi_card.html`.
- `KpiCard(label, value, color, drill_down_url, chart)` is constructed identically in Tasks 5, 6, 7 and consumed by the same macro in Task 13.
- `CashFlowChart(months, max_value, range_key)` / `CashFlowMonth(label, income, expense)` (Task 3) flow unchanged into Task 11's `OverviewData.cash_flow_chart`, Task 12's partial route, and Task 13's `_cash_flow_chart.html` (`chart.months`, `chart.max_value`, `chart.range_key`).
- `CategoryComparisonRow` fields (`category, current_value, rolling_avg_value, delta_pct, bar_pct, drill_down_url`) match between Task 9's constructor, Task 10's consumer, and Task 13's template.
- `YearlyCommitmentCard` fields match between Task 8's constructor and Task 13's `_yearly_commitments_card.html`.
- `OverviewData` (Task 11) field names (`flow_kpis, position_kpis, yearly_commitments, narrative, cash_flow_chart, category_comparison, needs_attention`) match every reference in Task 12's router and Task 13's `dashboard.html`.
- `HouseholdData(open_todos, recently_changed_wiki_pages)` (Task 4) matches Task 13's `_household.html`.

No gaps or naming drift found.

## Execution Choice

Per the calling instructions, this plan is executed via **superpowers:subagent-driven-development** — fresh implementer subagent per task, review after each task, a broad final whole-branch review at the end.
