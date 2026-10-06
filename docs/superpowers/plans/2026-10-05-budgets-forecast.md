# Budgets & Forecast (Plan 2 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-sub-category budgets (monthly or yearly), real-time spent-vs-budget, month-end and year-end estimates (from history and from "budget respected"), and a seasonal income range — shown on the Overview page.

**Architecture:** A `budgets` table keyed by (sub-category node, year). Pure forecast functions (`budget_forecast.py`, no database) are fed by one aggregation pass (`budget_service.py`) over transactions filed in the category tree (Plan 1). The Overview gets a "Budget" section rendered by htmx partials, with inline budget editing. No new page, no messages sent.

**Tech Stack:** FastAPI, SQLModel, Alembic (SQLite batch), Jinja2 + htmx, pytest.

**Spec:** `/home/pedro/Desktop/Claude_Corner/brainstorms/2026-10-05-transaction-category-hierarchy.md` (decisions Q1–Q8 and the Plan 2 answers appended there) and the Plan 1 plan `docs/superpowers/plans/2026-10-05-category-taxonomy.md`.

## Global Constraints
- No `git commit` / `git push` without Pedro's explicit order; commit steps are "ready to commit", do not run them.
- Budgets live on **level-3 nodes only**; category/group figures are sums of their children, never separately budgeted.
- Budget starts **empty** (no pre-fill from history). A line with no budget still shows spent and the history-based estimate.
- Everything is shown **on the Overview page only**. No new page. Over-budget is shown by colour on the page only (amber = estimate exceeds budget, red = already over); **no Telegram / notifications**.
- Big yearly bills are **not smoothed**: they appear in the month paid; forecasts place them in their expected due month.
- Income forecast is a **range** (low / expected / high), seasonal: same month of previous years first (Psi is seasonal), trailing months as fallback.
- Own-account transfers (neutral nodes) and loan-cadence nodes are not part of budgets or spend/income totals (loans are Plan 3).
- Spend is scoped by `paid_date`, like every other Overview number (`overview_service.py`).
- Colours: spend/over = muted red, ok = muted green, at-risk = muted amber; light and dark mode.
- Test data comes from production code (`ensure_taxonomy`, `file_transaction`, the budget service itself), not hand-typed lookalikes.

## File structure
- Create `app/models/budget.py` – the `Budget` table.
- Create `alembic/versions/b7d2f5a81c34_budgets.py`.
- Create `app/services/budget_forecast.py` – pure functions: `typical_month`, `month_end_estimate`, `year_end_monthly`, `yearly_line_estimate`, `income_range_for_month`, `income_year_end`, `Range`.
- Create `app/services/budget_service.py` – budget CRUD, aggregation, `get_budget_overview`.
- Create `app/templates/dashboard/_budget_panel.html`, `_budget_rows.html`.
- Modify `app/routers/dashboard.py` (routes), `app/templates/dashboard.html` (include section), `app/templates/base.html` (CSS), `app/models/__init__.py`.
- Tests: `tests/test_budget_model.py`, `tests/test_budget_forecast.py`, `tests/test_budget_service.py`, extend `tests/test_dashboard_router.py`.

---

### Task 1: Budget table, migration, CRUD

**Files:** Create `app/models/budget.py`, `alembic/versions/b7d2f5a81c34_budgets.py`, `app/services/budget_service.py` (CRUD part only); Modify `app/models/__init__.py`; Test `tests/test_budget_model.py`.

**Interfaces:**
- Consumes: `CategoryNode` (Plan 1), `ensure_taxonomy`, `get_node`.
- Produces: `Budget(id, node_id, year, amount, expected_month)`; `set_budget(session, node_id, year, amount, expected_month=None) -> Budget` (upsert; `amount <= 0` deletes; raises `ValueError` if node is not level 3, not `out` kind, or cadence not monthly/yearly; `expected_month` must be 1–12 and only allowed for yearly cadence); `get_budgets(session, year) -> dict[int, Budget]` keyed by node_id.

- [ ] **Step 1: Failing tests** — `tests/test_budget_model.py`

```python
import pytest

from app.services.budget_service import get_budgets, set_budget
from app.services.taxonomy import ensure_taxonomy, get_node


def test_set_budget_upserts_one_row_per_node_and_year(session):
    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    set_budget(session, node.id, 2026, 500.0)
    set_budget(session, node.id, 2026, 550.0)
    budgets = get_budgets(session, 2026)
    assert list(budgets) == [node.id] and budgets[node.id].amount == 550.0
    assert get_budgets(session, 2027) == {}


def test_zero_amount_removes_the_budget(session):
    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    set_budget(session, node.id, 2026, 500.0)
    set_budget(session, node.id, 2026, 0)
    assert get_budgets(session, 2026) == {}


def test_yearly_budget_keeps_expected_month(session):
    ensure_taxonomy(session)
    node = get_node(session, "housing.property-taxes-insurance.imi-property-tax")
    b = set_budget(session, node.id, 2026, 420.0, expected_month=4)
    assert b.expected_month == 4


@pytest.mark.parametrize("slug", [
    "food",                                                    # group, not a sub-category
    "income.psi.sessions",                                     # inflow
    "loans-debt.loan-repayments.car-loan",                     # loan cadence
    "internal-transfers.between-my-accounts.santander-card",   # neutral
])
def test_budget_rejected_on_non_budgetable_nodes(session, slug):
    ensure_taxonomy(session)
    with pytest.raises(ValueError):
        set_budget(session, get_node(session, slug).id, 2026, 100.0)


def test_expected_month_rejected_for_monthly_nodes_and_bad_values(session):
    ensure_taxonomy(session)
    monthly = get_node(session, "food.groceries.supermarket")
    yearly = get_node(session, "housing.property-taxes-insurance.imi-property-tax")
    with pytest.raises(ValueError):
        set_budget(session, monthly.id, 2026, 100.0, expected_month=3)
    with pytest.raises(ValueError):
        set_budget(session, yearly.id, 2026, 100.0, expected_month=13)
```

- [ ] **Step 2:** `pytest tests/test_budget_model.py -v` → FAIL (no module).
- [ ] **Step 3: Model** — `app/models/budget.py`

```python
"""Budget: the planned amount for one sub-category (level-3 node) in one year.
Monthly-cadence nodes: `amount` is per month. Yearly-cadence nodes: `amount` is
for the whole year and `expected_month` is when the big bill usually lands."""

from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Budget(SQLModel, table=True):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("node_id", "year", name="uq_budgets_node_year"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: int = Field(foreign_key="category_nodes.id", index=True)
    year: int = Field(index=True)
    amount: float
    expected_month: Optional[int] = None
```
Add `from app.models.budget import Budget  # noqa: F401` to `app/models/__init__.py`.

- [ ] **Step 4: CRUD** — start `app/services/budget_service.py`

```python
"""Budgets: CRUD, aggregation of actuals from the category tree, and the data
behind the Overview 'Budget' section."""

from typing import Optional

from sqlmodel import Session, select

from app.models.budget import Budget
from app.models.category_node import CategoryNode


def set_budget(session: Session, node_id: int, year: int, amount: float,
               expected_month: Optional[int] = None) -> Optional[Budget]:
    node = session.get(CategoryNode, node_id)
    if node is None or node.level != 3 or node.kind != "out" or node.cadence not in ("monthly", "yearly"):
        raise ValueError("budgets can only be set on monthly or yearly outflow sub-categories")
    if expected_month is not None and (node.cadence != "yearly" or not 1 <= expected_month <= 12):
        raise ValueError("expected_month applies to yearly sub-categories only (1-12)")
    existing = session.exec(select(Budget).where(Budget.node_id == node_id, Budget.year == year)).first()
    if amount <= 0:
        if existing:
            session.delete(existing)
            session.commit()
        return None
    budget = existing or Budget(node_id=node_id, year=year, amount=amount)
    budget.amount, budget.expected_month = amount, expected_month
    session.add(budget)
    session.commit()
    session.refresh(budget)
    return budget


def get_budgets(session: Session, year: int) -> dict[int, Budget]:
    return {b.node_id: b for b in session.exec(select(Budget).where(Budget.year == year)).all()}
```

- [ ] **Step 5: Migration** — `alembic/versions/b7d2f5a81c34_budgets.py`, `down_revision = "a1c4e7b92d10"`: `op.create_table("budgets", id, node_id (FK category_nodes.id, not null), year, amount Float not null, expected_month Integer nullable, PK, UniqueConstraint("node_id","year", name="uq_budgets_node_year"))`, indexes `ix_budgets_node_id`, `ix_budgets_year`; downgrade drops the table. Follow the style of `a1c4e7b92d10` (batch ops for indexes).
- [ ] **Step 6:** verify the migration up and down on a scratch copy of the DB (never the live file); `pytest tests/test_budget_model.py tests/test_schema_strictness.py -v` → PASS.
- [ ] **Step 7: Ready to commit:** `git commit -m "feat(budgets): budgets table and set/get service"`

---

### Task 2: Forecast maths (pure functions)

**Files:** Create `app/services/budget_forecast.py`; Test `tests/test_budget_forecast.py`.

**Interfaces — Produces** (everything takes plain numbers/dicts so it is testable without a database; month keys are `"YYYY-MM"` strings; `today` is a `date`):
```python
@dataclass(frozen=True)
class Range: low: float; expected: float; high: float
@dataclass(frozen=True)
class LineEstimate:
    expected: float              # history-based estimate for the period end
    if_budget_respected: Optional[float]   # None when there is no budget
    status: str                  # "no_budget" | "ok" | "at_risk" | "over"

def typical_month(history: dict[str, float], today: date, n: int = 6) -> float
def month_end_estimate(spent_mtd: float, typical: float, budget: Optional[float]) -> LineEstimate
def year_end_monthly(ytd: float, spent_mtd: float, typical: float, budget: Optional[float], today: date) -> LineEstimate
def yearly_line_estimate(spent_ytd: float, budget: Optional[float], last_year_total: float,
                         expected_month: Optional[int], today: date) -> LineEstimate
def yearly_line_month_estimate(spent_mtd: float, spent_ytd: float, budget: Optional[float],
                               last_year_total: float, expected_month: Optional[int], today: date) -> float
def income_range_for_month(history: dict[str, float], year: int, month: int, today: date) -> Range
def income_month_end(received_mtd: float, history: dict[str, float], today: date) -> Range
def income_year_end(received_ytd: float, received_mtd: float, history: dict[str, float], today: date) -> Range
```

Rules (these are the spec for the tests):
- `typical_month`: mean of the last `n` **complete** months before `today`'s month (missing months count as 0); 0.0 when `history` is empty.
- `month_end_estimate`: `expected = max(spent_mtd, typical)`; `if_budget_respected = max(spent_mtd, budget)` (None if no budget); status `no_budget` if budget is None, `over` if `spent_mtd > budget`, `at_risk` if `expected > budget`, else `ok`.
- `year_end_monthly`: with `done = ytd - spent_mtd`, `rest = 12 - today.month`: `expected = done + max(spent_mtd, typical) + typical * rest`; `if_budget_respected = done + max(spent_mtd, budget) + budget * rest`; status as above, comparing against `budget * 12` (`over` if `ytd > budget*12`).
- `yearly_line_estimate`: `planned = budget if budget is not None else last_year_total`; `expected = max(spent_ytd, planned)`; `if_budget_respected = max(spent_ytd, budget)`; status `no_budget` / `over` (`spent_ytd > budget`) / `at_risk` (`expected > budget`) / `ok`.
- `yearly_line_month_estimate`: remaining = `max(0, planned - spent_ytd)`; if `expected_month == today.month` (or `expected_month` is None and `spent_mtd > 0`) add remaining; result `spent_mtd + that`. If `expected_month` is earlier than `today.month` and remaining > 0 (overdue), also count it this month. Otherwise just `spent_mtd`.
- `income_range_for_month`: values for `(year-1, month)` and `(year-2, month)` that exist in `history`; if at least one exists: `low=min, high=max, expected=mean`. Else fall back to the last 6 complete months before `today`: `low=min, high=max, expected=mean` (missing months count as 0); if `history` is empty: `Range(0,0,0)`.
- `income_month_end`: range for the current month, floored by what has arrived: each of low/expected/high is `max(received_mtd, value)`.
- `income_year_end`: `done = received_ytd - received_mtd`; `done + income_month_end(...) + sum(income_range_for_month for each remaining month this year)`, summed per field.

- [ ] **Step 1: Failing tests** — `tests/test_budget_forecast.py` (write each rule above as a test; these are the key ones — add one per remaining rule in the same style):

```python
from datetime import date

from app.services.budget_forecast import (
    Range, income_month_end, income_range_for_month, income_year_end, month_end_estimate,
    typical_month, year_end_monthly, yearly_line_estimate, yearly_line_month_estimate,
)

TODAY = date(2026, 10, 15)
HIST = {"2026-04": 100.0, "2026-05": 200.0, "2026-06": 300.0, "2026-07": 100.0,
        "2026-08": 200.0, "2026-09": 100.0, "2026-10": 999.0}   # Oct = current month, ignored


def test_typical_month_is_mean_of_last_six_complete_months():
    assert typical_month(HIST, TODAY) == 1000.0 / 6          # Apr..Sep = 1000/6; Oct ignored


def test_typical_month_counts_missing_months_as_zero_and_empty_is_zero():
    assert typical_month({"2026-09": 60.0}, TODAY) == 10.0
    assert typical_month({}, TODAY) == 0.0


def test_month_end_estimate_statuses():
    assert month_end_estimate(50, 100, None).status == "no_budget"
    ok = month_end_estimate(50, 100, 200)
    assert (ok.expected, ok.if_budget_respected, ok.status) == (100, 200, "ok")
    assert month_end_estimate(50, 100, 80).status == "at_risk"
    assert month_end_estimate(90, 100, 80).status == "over"
    assert month_end_estimate(150, 100, 200).expected == 150      # already past typical


def test_year_end_monthly_projects_remaining_months():
    est = year_end_monthly(ytd=1000, spent_mtd=50, typical=100, budget=120, today=TODAY)
    # done=950, this month max(50,100)=100, 2 months left * 100 = 200
    assert est.expected == 950 + 100 + 200
    assert est.if_budget_respected == 950 + 120 + 240
    assert est.status == "ok"                                      # 1250 < 120*12 and ytd<1440


def test_yearly_line_places_remaining_in_expected_month():
    # IMI budget 420 due in October, nothing paid yet -> this month's estimate includes it
    assert yearly_line_month_estimate(0, 0, 420, 0, 10, TODAY) == 420
    # due in April, still unpaid in October -> overdue, counted now
    assert yearly_line_month_estimate(0, 0, 420, 0, 4, TODAY) == 420
    # due in December -> not this month
    assert yearly_line_month_estimate(0, 0, 420, 0, 12, TODAY) == 0
    # already paid -> nothing remaining
    assert yearly_line_month_estimate(420, 420, 420, 0, 10, TODAY) == 420


def test_yearly_line_without_budget_uses_last_year():
    est = yearly_line_estimate(0, None, 380, 4, TODAY)
    assert (est.expected, est.if_budget_respected, est.status) == (380, None, "no_budget")


def test_income_range_prefers_same_month_of_previous_years():
    hist = {"2025-10": 1000.0, "2024-10": 3000.0, "2026-09": 50.0}
    assert income_range_for_month(hist, 2026, 10, TODAY) == Range(1000.0, 2000.0, 3000.0)


def test_income_range_falls_back_to_trailing_months():
    hist = {"2026-07": 600.0, "2026-08": 0.0, "2026-09": 300.0}
    assert income_range_for_month(hist, 2026, 10, TODAY) == Range(0.0, 150.0, 600.0)   # Apr-Sep, missing = 0


def test_income_month_end_is_floored_by_received():
    hist = {"2025-10": 1000.0, "2024-10": 3000.0}
    assert income_month_end(2500.0, hist, TODAY) == Range(2500.0, 2500.0, 3000.0)


def test_income_year_end_adds_remaining_months():
    hist = {"2025-10": 1000.0, "2025-11": 1000.0, "2025-12": 2000.0}
    r = income_year_end(received_ytd=5000.0, received_mtd=400.0, history=hist, today=TODAY)
    # done 4600 + Oct max(400,1000)=1000 + Nov 1000 + Dec 2000
    assert r.expected == 4600 + 1000 + 1000 + 2000
```

- [ ] **Step 2:** run → FAIL. **Step 3:** implement `app/services/budget_forecast.py` exactly to the rules above (a helper `_complete_months_before(today, n)` already exists in `app/services/overview_charts.py` — reuse it for the month keys). **Step 4:** run → PASS.
- [ ] **Step 5: Ready to commit:** `git commit -m "feat(budgets): forecast functions (month-end, year-end, yearly bills, seasonal income range)"`

---

### Task 3: Aggregation and overview data

**Files:** Modify `app/services/budget_service.py`; Test `tests/test_budget_service.py`.

**Interfaces:**
- Consumes: Task 1 (`get_budgets`), Task 2 (forecast functions), Plan 1 (`flow_of`, `CategoryNode`, `ensure_taxonomy`).
- Produces:
```python
@dataclass
class BudgetLine:        # one level-3 node
    node_id: int; name: str; cadence: str          # "monthly" | "yearly"
    budget: Optional[float]; expected_month: Optional[int]
    spent: float                                   # month view: spent this month; year view: spent YTD
    expected: float; if_budget_respected: Optional[float]; status: str
@dataclass
class BudgetCategory: name: str; lines: list[BudgetLine]; spent: float; budget: float; expected: float; status: str
@dataclass
class BudgetGroup: name: str; categories: list[BudgetCategory]; spent: float; budget: float; expected: float; status: str
@dataclass
class BudgetOverview:
    view: str                      # "month" | "year"
    income: Range                  # month-end (month view) or year-end (year view)
    income_received: float
    spend_expected: float; spend_if_budget_respected: float; spend_spent: float
    net: Range                     # income range minus spend_expected (low = income.low - spend_expected, high = income.high - spend_expected)
    groups: list[BudgetGroup]
    unsorted_spent: float          # spend not yet filed in the tree (shown as its own row)
def get_budget_overview(session, today: date, view: str = "month") -> BudgetOverview
```
Rules:
- One query: all transactions with `paid_date` in the trailing 36 complete months + current year; bucket per `(node_id, "YYYY-MM")`. Signed amount: for an **out** node `+amount` if DEBIT, `-amount` if CREDIT; for an **in** node `+amount` if CREDIT, `-amount` if DEBIT; TRANSFER-type rows and neutral nodes are ignored. Rows with no `category_id` or filed under Unsorted go to `unsorted_spent` by `flow_of` (out only; ignore unsorted inflows in the income range).
- Lines exist for every level-3 **out** node with cadence monthly or yearly **that has a budget or any spend in the last 12 months**; others are hidden (keeps the page short while budgets are empty). Loan-cadence nodes are excluded.
- Month view: monthly lines use `month_end_estimate(spent_mtd, typical_month(history), budget)`; yearly lines use `yearly_line_month_estimate` for `expected` and `yearly_line_estimate` for status/if-respected, **shown only if** spent this month > 0, or remaining due this month/overdue, else hidden. Year view: monthly lines use `year_end_monthly`, yearly lines `yearly_line_estimate` (`last_year_total` = that node's spend in `today.year - 1`).
- Roll-ups: category/group `spent`, `budget`, `expected` are sums of children; `status` is the worst child status (`over` > `at_risk` > `ok` > `no_budget`). `budget` for a line without a budget counts as 0 in sums.
- Income: sum of the per-node income ranges for all `in` level-3 nodes (month view: `income_month_end` per node; year view: `income_year_end` per node), then summed field-wise; `income_received` = received MTD/YTD.
- `net.expected = income.expected - spend_expected`; low/high use the income low/high.

- [ ] **Step 1: Failing tests** — `tests/test_budget_service.py`. Build data with `ensure_taxonomy`, `file_transaction` and a local helper that creates a `Document` + `Transaction` with `paid_date`/`transaction_type`. Cover each rule:
  1. spend per sub-category: three DEBITs under `food.groceries.supermarket` in the current month sum to `spent`; a TRANSFER-type row is ignored; a CREDIT filed under an out node reduces spend.
  2. group/category roll-up sums and "worst status wins" (one line over, one ok → category `over`).
  3. no-budget line still gets `expected` from history and `status == "no_budget"`; a node with no spend and no budget is absent.
  4. a transaction filed under Unsorted (DEBIT) lands in `unsorted_spent`, not in any line.
  5. yearly line (`housing.property-taxes-insurance.imi-property-tax`, budget 420, expected_month 10, nothing paid) in month view for `today=2026-10-15` has `expected == 420`; in November it is overdue-counted only if still unpaid; hidden in a month where it is neither paid nor due.
  6. income month view: seeded Psi income in Oct 2025 and Oct 2024 gives a range for Oct 2026 (seasonality); `income.low <= expected <= high`.
  7. year view: `spend_expected` equals the sum of line `expected`; `net.expected == income.expected - spend_expected`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement `get_budget_overview` and the dataclasses above in `budget_service.py`, calling the Task 2 functions (no forecast logic in this file). **Step 4:** run → PASS; `pytest tests/test_overview_service.py -q` still PASS (untouched).
- [ ] **Step 5: Ready to commit:** `git commit -m "feat(budgets): actuals aggregation and Overview budget data"`

---

### Task 4: The Overview "Budget" section (view + inline editing)

**Files:** Modify `app/routers/dashboard.py`, `app/templates/dashboard.html`, `app/templates/base.html`; Create `app/templates/dashboard/_budget_panel.html`, `_budget_rows.html`; Test `tests/test_dashboard_router.py` (append).

**Interfaces:**
- Consumes: `get_budget_overview`, `set_budget`, `get_budgets`.
- Produces: `GET /overview/budget?view=month|year` → the `#budget-panel` partial; `POST /overview/budget/{node_id}` with form fields `amount` (float, required), `expected_month` (optional int), `view` → saves for `date.today().year`, returns the re-rendered `#budget-panel`; invalid input → HTTP 400 with the message.

**Layout (mirror the existing `fc-*` look of `_period_panel.html`):**
- Section title "Budget" with two pills **Month** / **Year** (same htmx pill pattern as the cash-flow pills: `hx-get="/overview/budget?view=…" hx-target="#budget-panel" hx-swap="outerHTML"`), placed between the "Position" cards and the cash-flow section in `dashboard.html`.
- Three summary cards: **Income** (shows `low – high`, expected underneath, "received so far"), **Spending** (spent so far, estimate, "if budgets are respected"), **Net** (expected, range).
- Tree: one `<details>` per group → categories → sub-category lines. Each line: name, a bar (spent vs budget; width capped at 100%), `spent`, `budget` (or "set budget"), estimate. Bar/status colour: ok = muted green, at_risk = muted amber, over = muted red, no_budget = grey. A final grey row "Not filed yet" for `unsorted_spent` linking to `/financials/transactions/needs-review`.
- Inline edit: clicking the budget figure (or "set budget") reveals a small form (`<form hx-post="/overview/budget/{{ line.node_id }}" hx-target="#budget-panel" hx-swap="outerHTML">`) with an amount input, a month select for yearly lines, and Save. Monthly lines: label the input "per month"; yearly lines: "per year, due in".
- Empty state while no budgets exist: a one-line hint "No budgets yet — click a line's 'set budget' to add one"; lines still show spent and the history-based estimate.
- Escape/format numbers like the existing panel (`"{:,.0f}"`), euros.

- [ ] **Step 1: Failing tests** (append to `tests/test_dashboard_router.py`; use `ensure_taxonomy`/`file_transaction` to create data):
```python
def test_overview_shows_budget_section_with_spend_and_status(client, session): ...
    # seed groceries spend this month + a budget via set_budget(...200)
    # GET "/" -> "Budget" section present, "Supermarket" line, a status class (e.g. "bud-ok"/"bud-over")
def test_budget_partial_route_month_and_year(client, session): ...
    # GET /overview/budget?view=year -> 200, no "<html", contains id="budget-panel"
def test_post_budget_saves_and_returns_panel(client, session): ...
    # POST /overview/budget/{node_id} amount=300 -> 200; get_budgets(session, today.year)[node_id].amount == 300; panel shows 300
def test_post_budget_rejects_bad_input(client, session): ...
    # amount for an inflow node, expected_month=13 on yearly, non-numeric amount -> 400
def test_overview_still_renders_with_no_taxonomy_data(client): ...
    # empty DB: GET "/" -> 200 (existing dashboard tests must still pass)
```
(Write full test bodies with real assertions when implementing; the five behaviours above are the required cases. Date-dependent assertions use `date.today()`, as the dashboard route does.)
- [ ] **Step 2:** run → FAIL. **Step 3:** routes in `dashboard.py` (`get_budget_overview(session, date.today(), view)`; validate `view in {"month","year"}` else 400; `set_budget` `ValueError` → 400). **Step 4:** templates + CSS (light/dark tokens like Plan 1's flow colours: ok `#4d7a56`, at-risk `#a2792f`, over `#a8504a`, grey `#76716a`). **Step 5:** `pytest tests/test_dashboard_router.py tests/test_budget_service.py -v` → PASS, then the full suite → PASS.
- [ ] **Step 6: Look at it** on a scratch DB copy after Plan 1's refile has been applied: set a few budgets through the inline form in the browser pane, switch Month/Year, check dark mode and phone width (the page has an existing mobile layout).
- [ ] **Step 7: Ready to commit:** `git commit -m "feat(overview): budget section with month/year estimates and inline budget editing"`

---

### Task 5: Roll out (needs Pedro's explicit word to push/deploy)
Order matters: deploy runs `alembic upgrade head` on push.
- [ ] Full suite green; migration up/down on a fresh scratch copy of the live DB.
- [ ] **Before the push:** back up the live DB on the VPS (path the SSH user can write; see `docs/SYSADMIN.md`).
- [ ] Pedro says "push" → `git subtree push --prefix="Home & Family" home_hub main`.
- [ ] Pedro opens the Overview: sets first budgets (starts empty), checks Month/Year, colours, and that estimates look sane against his own sense of the numbers. **Note:** estimates are only meaningful after Plan 1's refile + `--reclassify` have run, since spending is read from the category tree.

## Self-review
- **Spec coverage:** monthly vs yearly budgets ✔ (T1, T2) · budgets at sub-category, roll-up above ✔ (T1 rule, T3) · real-time status ✔ (T3/T4 spent + status) · month-end and year-end estimates ✔ (T2/T3, both history-based and budget-respected) · big bills not smoothed, forecast on due month ✔ (`yearly_line_month_estimate`) · seasonal income range ✔ (`income_range_for_month`) · start empty, Overview only, on-page warnings only ✔ (Global Constraints, T4) · transfers/loans excluded ✔ (loans reserved for Plan 3).
- **Placeholder scan:** Task 3 tests and Task 4 test bodies are specified as required cases with the data to build rather than full code, because exact fixtures depend on Plan 1's helpers; implementers write them to those cases. Task 1's migration is described against the style of `a1c4e7b92d10`, not pasted.
- **Type consistency:** `Range`, `LineEstimate`, `BudgetLine/Category/Group/Overview`, `set_budget`, `get_budgets`, `get_budget_overview`, the route paths and form-field names are used identically across tasks.
- **Known limitation to confirm with Pedro after rollout:** monthly estimates use "mean of the last 6 complete months"; lumpy monthly bills (e.g. bi-monthly water) will make single months look high/low. If that proves noisy, switch `typical_month` to a median — a one-function change.
