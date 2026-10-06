# Category Taxonomy (Plan 1 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat 16-value `Category` with a 3-level category tree (Group > Category > Sub-category), re-file existing history into it, colour the transactions list by money direction, and make new transactions classify into the tree.

**Architecture:** A new `category_nodes` table holds the tree (seeded, idempotent). `Transaction.category_id` and `Merchant.default_category_id` point at tree leaves. The legacy `category` enum column stays and is **dual-written** from the node, so the overview, Ask and bank-sync code keep working untouched until Plan 2/3 retire it. Money direction (outflow / inflow / neutral) comes from the node, except the "Unsorted" node, where it comes from `transaction_type`.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy, Alembic (SQLite, batch mode), Jinja2 + htmx, pytest.

**Spec:** `/home/pedro/Desktop/Claude_Corner/brainstorms/2026-10-05-transaction-category-hierarchy.md` (decisions) and `.../2026-10-05-category-hierarchy-proposal.html` (approved tree, in its `DATA` object).

## Global Constraints
- No `git commit` / `git push` without Pedro's explicit order. **Commit steps below are written as "ready to commit"; do not run them until told.**
- Every LLM call goes through the llmsel gateway (`app/llm_gateway.py`); no provider SDK, model name or key in this app.
- Test inputs come from production code (`ensure_taxonomy`, `str(GatewayError(...))`, `tests/fakes/fake_gateway.py`), never typed-in lookalikes.
- Budgets are set on **sub-categories (level 3) only**; category/group figures are roll-ups (Plan 2).
- Money borrowed/lent and own-account transfers are **not** income or spending.
- Colours: outflows muted red, inflows muted green, neutral grey.
- Production DB has no staging: test the migration on a scratch copy of `data/home_family.db` first; back up on the VPS before any backfill (see `docs/SYSADMIN.md`).

## Roadmap (separate plans, written after this one lands)
- **Plan 2 – Budgets & forecast:** `budgets` table (per sub-category, monthly or yearly amount, expected due month for yearly), real-time spent-vs-budget, month-end and year-end forecast, Psi seasonal income range.
- **Plan 3 – Loans:** reconcile `Debt` (already exists, both directions) with transactions; balance per loan; repayments tick down; interest/fees to expense.

---

## File structure
- Create `app/models/category_node.py` – the tree table.
- Create `app/services/taxonomy_seed.py` – the approved tree as data + legacy mapping.
- Create `app/services/taxonomy.py` – `ensure_taxonomy`, lookups, `file_transaction`, `flow_of`.
- Create `app/services/taxonomy_refile.py` – history backfill.
- Create `scripts/refile_to_taxonomy.py` – thin CLI wrapper (dry-run by default).
- Create `alembic/versions/a1c4e7b92d10_category_nodes.py`.
- Modify `app/models/transaction.py`, `app/models/merchant.py`, `app/models/__init__.py` – new columns/exports.
- Modify `app/routers/transactions.py`, `app/templates/transactions/{list,_rows,needs_review,_needs_review_rows}.html`, `app/templates/base.html` (CSS).
- Modify `app/services/classification_engine.py`, `app/services/bankapi/sync.py` – classify into the tree.
- Tests: `tests/test_taxonomy.py`, `tests/test_taxonomy_refile.py`, extend `tests/test_transactions_router.py`, `tests/test_classification_engine.py`.

---

### Task 1: Tree model, seed data, `ensure_taxonomy`

**Files:**
- Create: `app/models/category_node.py`, `app/services/taxonomy_seed.py`, `app/services/taxonomy.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_taxonomy.py`

**Interfaces:**
- Produces: `CategoryNode(id, parent_id, level, slug, name, kind, cadence, sort_order)`; `ensure_taxonomy(session) -> int` (nodes created); `get_node(session, slug) -> CategoryNode`; `leaf_slugs(session) -> list[str]`; `descendant_ids(session, node_id) -> list[int]` (includes self); `path_label(session, node) -> str` ("Food › Groceries › Supermarket"); constants `UNSORTED_SLUG`, `LEGACY_TO_SLUG`.

- [ ] **Step 1: Write the failing tests** — `tests/test_taxonomy.py`

```python
from sqlmodel import select

from app.models.category_node import CategoryNode
from app.services.taxonomy import (
    UNSORTED_SLUG, descendant_ids, ensure_taxonomy, get_node, leaf_slugs, path_label,
)


def test_ensure_taxonomy_builds_three_levels(session):
    created = ensure_taxonomy(session)
    assert created > 100
    assert {n.level for n in session.exec(select(CategoryNode)).all()} == {1, 2, 3}


def test_ensure_taxonomy_is_idempotent(session):
    ensure_taxonomy(session)
    assert ensure_taxonomy(session) == 0


def test_path_label_and_kind(session):
    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    assert path_label(session, node) == "Food › Groceries › Supermarket"
    assert node.kind == "out" and node.cadence == "monthly"


def test_yearly_and_loan_cadences(session):
    ensure_taxonomy(session)
    assert get_node(session, "housing.property-taxes-insurance.imi-property-tax").cadence == "yearly"
    assert get_node(session, "loans-debt.loan-repayments.car-loan").cadence == "loan"
    assert get_node(session, "income.psi.sessions").kind == "in"
    assert get_node(session, "internal-transfers.between-my-accounts.top-ups-card-payments").kind == "neutral"


def test_descendants_include_self(session):
    ensure_taxonomy(session)
    food = get_node(session, "food")
    ids = descendant_ids(session, food.id)
    assert food.id in ids and get_node(session, "food.groceries.supermarket").id in ids
    assert get_node(session, "housing").id not in ids


def test_leaf_slugs_are_only_level_3_and_include_unsorted(session):
    ensure_taxonomy(session)
    slugs = leaf_slugs(session)
    assert UNSORTED_SLUG in slugs
    assert all(s.count(".") == 2 for s in slugs)
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_taxonomy.py -v` → FAIL (`ModuleNotFoundError: app.models.category_node`).

- [ ] **Step 3: Implement the model** — `app/models/category_node.py`

```python
"""CategoryNode: one node of the 3-level category tree (Group > Category >
Sub-category). Budgets (Plan 2) attach to level-3 nodes."""

from typing import Optional

from sqlmodel import Field, SQLModel


class CategoryNode(SQLModel, table=True):
    __tablename__ = "category_nodes"

    id: Optional[int] = Field(default=None, primary_key=True)
    parent_id: Optional[int] = Field(default=None, foreign_key="category_nodes.id", index=True)
    level: int
    slug: str = Field(index=True, unique=True)   # "food.groceries.supermarket"
    name: str
    kind: str                                    # "out" | "in" | "neutral"
    cadence: Optional[str] = None                # "monthly" | "yearly" | "loan" | None (no budget)
    sort_order: int = 0
```

Add to `app/models/__init__.py`: `from app.models.category_node import CategoryNode  # noqa: F401`

- [ ] **Step 4: Implement the seed** — `app/services/taxonomy_seed.py`. Each category line is `(name, cadence, legacy, [subs])`; `legacy` is the old `Category` value dual-written onto `Transaction.category` for anything filed under it.

```python
"""The approved category tree (proposal of 2026-10-05) as data."""

from app.models.transaction import Category as L

M, Y, LOAN, NONE = "monthly", "yearly", "loan", None

# kind -> [(group, [(category, cadence, legacy, [subs])])]
SEED = {
"out": [
 ("Housing", [
  ("Utilities", M, None, ["Electricity", "Water", "Gas", "Internet & TV", "Mobile phones"]),
  ("Home running costs", M, L.HOME, ["Condominium", "Cleaning", "Household supplies"]),
  ("Home upkeep", M, L.HOME, ["Repairs & maintenance", "Furniture & appliances", "Garden & DIY"]),
  ("Property taxes & insurance", Y, L.INSURANCE, ["IMI property tax", "Home insurance", "Condominium extras"]),
 ]),
 ("Food", [
  ("Groceries", M, L.GROCERIES, ["Supermarket", "Butcher, fish & bakery", "Markets"]),
  ("Dining out", M, L.RESTAURANTS, ["Restaurants", "Cafés & takeaway", "Food delivery"]),
 ]),
 ("Transport", [
  ("Car running", M, None, ["Fuel", "Tolls & parking", "Maintenance & repairs"]),
  ("Car yearly", Y, L.INSURANCE, ["Car insurance", "IUC road tax", "Inspection (IPO)"]),
  ("Other transport", M, None, ["Public transport", "Taxi & rideshare"]),
 ]),
 ("Health", [
  ("Care", M, L.HEALTH, ["Doctors & dental", "Pharmacy", "Therapy & wellness"]),
  ("Health cover", Y, L.INSURANCE, ["Health insurance"]),
 ]),
 ("Family & education", [
  ("Children & school", M, None, ["School & childcare", "Activities & clubs", "Kids' clothes & items"]),
  ("Education", Y, None, ["Courses & training", "Books & materials"]),
 ]),
 ("Personal & lifestyle", [
  ("Personal", M, L.SHOPPING, ["Clothing & shoes", "Personal care", "General shopping"]),
  ("Subscriptions", M, L.SUBSCRIPTIONS, ["Streaming", "Software & apps", "Memberships"]),
  ("Leisure", M, None, ["Hobbies & sport", "Culture & events", "Gifts"]),
 ]),
 ("Holidays & travel", [
  ("Holidays", Y, None, ["Flights", "Accommodation", "On-trip spending"]),
  ("Short breaks", Y, None, ["Weekend trips"]),
 ]),
 ("Taxes & financial costs", [
  ("Taxes", Y, None, ["Income tax (IRS) settlement", "Social security", "Other taxes & fees"]),
  ("Banking", M, None, ["Bank & card fees", "Insurance, life & other"]),
 ]),
 ("Loans & debt", [
  ("Loan repayments", LOAN, None, ["Mortgage", "Car loan", "Personal loans"]),
  ("Interest & fees", M, None, ["Loan interest", "Loan fees"]),
  ("Money lent out", LOAN, None, ["Loan to family", "Loan to friends"]),
 ]),
 ("Cash & giving", [
  ("Cash", M, L.ATM_WITHDRAWAL, ["ATM withdrawals"]),
  ("Giving", Y, None, ["Donations", "Charity"]),
 ]),
 ("Unsorted", [
  ("Needs review", NONE, L.OTHER_EXPENSE, ["Needs review"]),
 ]),
],
"in": [
 ("Income", [
  ("Psi", NONE, L.INCOME, ["Sessions", "Other Psi income"]),
  ("Employment", NONE, L.INCOME, ["Salary", "Bonus"]),
  ("Rental", NONE, L.INCOME, ["House rent"]),
  ("Benefits & refunds from the state", NONE, L.INCOME, ["IRS refund", "Family or other benefits"]),
  ("Investments & interest", NONE, L.INCOME, ["Interest", "Dividends"]),
  ("Gifts & other", NONE, L.INCOME, ["Gifts received", "Other income"]),
 ]),
 ("Refunds & reimbursements", [
  ("Refunds", NONE, L.INCOME, ["Purchase refunds", "Insurance claims", "Expense reimbursements"]),
 ]),
 ("Loans & debt (in)", [
  ("Money borrowed", LOAN, L.INCOME, ["Mortgage drawdown", "Personal loan received"]),
  ("Repayments received", LOAN, L.INCOME, ["From family", "From friends"]),
 ]),
],
"neutral": [
 ("Internal transfers", [
  ("Between my accounts", NONE, L.TRANSFER, ["Santander ↔ card", "Santander ↔ Revolut", "Top-ups & card payments"]),
 ]),
],
}

# Utilities sub-categories carry their own legacy value (old enum had one per utility).
SUB_LEGACY = {
    "Electricity": L.ELECTRICITY, "Water": L.WATER, "Gas": L.GAS,
    "Internet & TV": L.TELECOM, "Mobile phones": L.TELECOM,
}

# Old Category value -> slug of the tree leaf it lands on when history is re-filed.
# None = ambiguous, send to Unsorted for review.
LEGACY_TO_SLUG = {
    L.ELECTRICITY: "housing.utilities.electricity",
    L.WATER: "housing.utilities.water",
    L.GAS: "housing.utilities.gas",
    L.TELECOM: "housing.utilities.internet-tv",
    L.SUBSCRIPTIONS: "personal-lifestyle.subscriptions.streaming",
    L.GROCERIES: "food.groceries.supermarket",
    L.RESTAURANTS: "food.dining-out.restaurants",
    L.ATM_WITHDRAWAL: "cash-giving.cash.atm-withdrawals",
    L.SHOPPING: "personal-lifestyle.personal.general-shopping",
    L.HEALTH: None, L.HOME: None, L.INSURANCE: None, L.INCOME: None,
    L.TRANSFER: None, L.OTHER_EXPENSE: None, L.OTHER: None,
}
```

- [ ] **Step 5: Implement the service** — `app/services/taxonomy.py`

```python
"""Category tree: seeding, lookups, filing a transaction under a node."""

import re
from typing import Optional

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.transaction import Category, Transaction, TransactionType
from app.services.taxonomy_seed import LEGACY_TO_SLUG, SEED, SUB_LEGACY  # noqa: F401

UNSORTED_SLUG = "unsorted.needs-review.needs-review"
_KIND_LEGACY = {"out": Category.OTHER_EXPENSE, "in": Category.INCOME, "neutral": Category.TRANSFER}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("&", " ")).strip("-")


def ensure_taxonomy(session: Session) -> int:
    """Insert any seed node missing by slug. Idempotent. Returns nodes created."""
    existing = {n.slug for n in session.exec(select(CategoryNode)).all()}
    created = 0

    def upsert(slug, parent, level, name, kind, cadence, order):
        nonlocal created
        if slug in existing:
            return session.exec(select(CategoryNode).where(CategoryNode.slug == slug)).one()
        node = CategoryNode(slug=slug, parent_id=parent.id if parent else None, level=level,
                            name=name, kind=kind, cadence=cadence, sort_order=order)
        session.add(node)
        session.flush()
        existing.add(slug)
        created += 1
        return node

    order = 0
    for kind, groups in SEED.items():
        for gname, cats in groups:
            g = upsert(_slug(gname), None, 1, gname, kind, None, order := order + 1)
            for cname, cadence, _legacy, subs in cats:
                c = upsert(f"{g.slug}.{_slug(cname)}", g, 2, cname, kind, cadence, order := order + 1)
                for sname in subs:
                    upsert(f"{c.slug}.{_slug(sname)}", c, 3, sname, kind, cadence, order := order + 1)
    session.commit()
    return created


def get_node(session: Session, slug: str) -> CategoryNode:
    return session.exec(select(CategoryNode).where(CategoryNode.slug == slug)).one()


def leaf_slugs(session: Session) -> list[str]:
    return [n.slug for n in session.exec(
        select(CategoryNode).where(CategoryNode.level == 3).order_by(CategoryNode.sort_order)).all()]


def descendant_ids(session: Session, node_id: int) -> list[int]:
    ids, frontier = [node_id], [node_id]
    while frontier:
        children = session.exec(select(CategoryNode.id).where(CategoryNode.parent_id.in_(frontier))).all()
        ids += children
        frontier = list(children)
    return ids


def path_label(session: Session, node: CategoryNode) -> str:
    parts = [node.name]
    while node.parent_id:
        node = session.get(CategoryNode, node.parent_id)
        parts.append(node.name)
    return " › ".join(reversed(parts))
```

(`file_transaction` and `flow_of` are added in Task 3.)

- [ ] **Step 6: Run** — `pytest tests/test_taxonomy.py -v` → PASS (note: `ensure_taxonomy` slugs for legacy mapping, e.g. `personal-lifestyle.subscriptions.streaming`, are verified in Task 3's test).
- [ ] **Step 7: Ready to commit (do not run until ordered):** `git add app/models app/services/taxonomy*.py tests/test_taxonomy.py && git commit -m "feat(categories): 3-level category tree model, seed and ensure_taxonomy"`

---

### Task 2: Schema — columns on transactions and merchants, migration

**Files:**
- Modify: `app/models/transaction.py`, `app/models/merchant.py`
- Create: `alembic/versions/a1c4e7b92d10_category_nodes.py`
- Test: `tests/test_taxonomy.py` (append)

**Interfaces:**
- Produces: `Transaction.category_id: Optional[int]` (FK `category_nodes.id`, indexed); `Merchant.default_category_id: Optional[int]` (FK).

- [ ] **Step 1: Failing test** (append to `tests/test_taxonomy.py`)

```python
from app.models.document import Document, DocumentSource
from app.models.transaction import Transaction


def test_transaction_and_merchant_accept_node_ids(session):
    from app.models.merchant import Merchant
    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    doc = Document(filename="a.pdf", file_path="/tmp/a.pdf", content_hash="h1", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, provider="PINGO DOCE", amount=10.0, category_id=node.id)
    m = Merchant(canonical_name="Pingo Doce", normalized_key="pingo doce", default_category_id=node.id)
    session.add_all([t, m]); session.commit()
    assert session.get(Transaction, t.id).category_id == node.id
    assert session.get(Merchant, m.id).default_category_id == node.id
```

- [ ] **Step 2:** run → FAIL (`unexpected keyword category_id`).
- [ ] **Step 3: Add fields.** `Transaction`: `category_id: Optional[int] = Field(default=None, foreign_key="category_nodes.id", index=True)`. `Merchant`: `default_category_id: Optional[int] = Field(default=None, foreign_key="category_nodes.id")`.
- [ ] **Step 4: Migration** — `alembic/versions/a1c4e7b92d10_category_nodes.py` (`down_revision = '6d1e9c597432'`, current head):

```python
"""category nodes + category_id on transactions/merchants

Revision ID: a1c4e7b92d10
Revises: 6d1e9c597432
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlmodel import Session

revision: str = "a1c4e7b92d10"
down_revision: Union[str, Sequence[str], None] = "6d1e9c597432"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "category_nodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("slug", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("cadence", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["category_nodes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("category_nodes") as b:
        b.create_index("ix_category_nodes_slug", ["slug"], unique=True)
        b.create_index("ix_category_nodes_parent_id", ["parent_id"])
    with op.batch_alter_table("transactions") as b:
        b.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_transactions_category_id", "category_nodes", ["category_id"], ["id"])
        b.create_index("ix_transactions_category_id", ["category_id"])
    with op.batch_alter_table("merchants") as b:
        b.add_column(sa.Column("default_category_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_merchants_default_category_id", "category_nodes", ["default_category_id"], ["id"])

    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(Session(bind=op.get_bind()))


def downgrade() -> None:
    with op.batch_alter_table("merchants") as b:
        b.drop_constraint("fk_merchants_default_category_id", type_="foreignkey")
        b.drop_column("default_category_id")
    with op.batch_alter_table("transactions") as b:
        b.drop_index("ix_transactions_category_id")
        b.drop_constraint("fk_transactions_category_id", type_="foreignkey")
        b.drop_column("category_id")
    op.drop_table("category_nodes")
```

- [ ] **Step 5: Verify migration on a scratch copy** (never the live file):
```bash
cp data/home_family.db /tmp/scratch.db && DATABASE_PATH=/tmp/scratch.db alembic upgrade head && sqlite3 /tmp/scratch.db "select count(*) from category_nodes; select count(*) from transactions where category_id is null;"
```
Expected: ≈140 nodes (groups + categories + sub-categories); every transaction `category_id` NULL. Then `alembic downgrade -1` on the scratch copy succeeds.
- [ ] **Step 6:** `pytest tests/test_taxonomy.py tests/test_schema_strictness.py -v` → PASS.
- [ ] **Step 7: Ready to commit:** `git commit -m "feat(categories): category_id on transactions and merchants + migration"`

---

### Task 3: Filing, direction, and history re-filing

**Files:**
- Modify: `app/services/taxonomy.py`
- Create: `app/services/taxonomy_refile.py`, `scripts/refile_to_taxonomy.py`
- Test: `tests/test_taxonomy_refile.py`

**Interfaces:**
- Consumes: Task 1/2.
- Produces: `file_transaction(session, txn, node) -> None` (sets `category_id` and dual-writes legacy `category`; no commit); `flow_of(txn, node) -> "in"|"out"|"neutral"`; `refile_all(session, dry_run=True) -> RefileReport(filed_from_merchant, filed_from_legacy, sent_to_review, already_filed)`.

- [ ] **Step 1: Failing tests** — `tests/test_taxonomy_refile.py`

```python
from app.models.document import Document, DocumentSource
from app.models.merchant import Merchant
from app.models.transaction import Category, Transaction, TransactionType
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, file_transaction, flow_of, get_node
from app.services.taxonomy_refile import refile_all


def _txn(session, provider, category, ttype=TransactionType.DEBIT, merchant_id=None):
    doc = Document(filename=f"{provider}.pdf", file_path=f"/tmp/{provider}.pdf",
                   content_hash=f"h-{provider}", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, provider=provider, category=category,
                    transaction_type=ttype, amount=10.0, merchant_id=merchant_id)
    session.add(t); session.commit(); session.refresh(t)
    return t


def test_file_transaction_dual_writes_legacy_category(session):
    ensure_taxonomy(session)
    t = _txn(session, "EDP", Category.OTHER)
    file_transaction(session, t, get_node(session, "housing.utilities.electricity"))
    assert t.category_id == get_node(session, "housing.utilities.electricity").id
    assert t.category == Category.ELECTRICITY


def test_flow_of_uses_kind_and_falls_back_to_type_for_unsorted(session):
    ensure_taxonomy(session)
    out_t = _txn(session, "A", Category.GROCERIES)
    assert flow_of(out_t, get_node(session, "food.groceries.supermarket")) == "out"
    assert flow_of(out_t, get_node(session, "income.psi.sessions")) == "in"
    assert flow_of(out_t, get_node(session, "internal-transfers.between-my-accounts.santander-card")) == "neutral"
    credit = _txn(session, "B", Category.OTHER, TransactionType.CREDIT)
    assert flow_of(credit, get_node(session, UNSORTED_SLUG)) == "in"
    assert flow_of(out_t, get_node(session, UNSORTED_SLUG)) == "out"


def test_refile_prefers_confirmed_merchant_then_legacy_then_review(session):
    ensure_taxonomy(session)
    fuel = get_node(session, "transport.car-running.fuel")
    m = Merchant(canonical_name="Galp", normalized_key="galp", confirmed=True, default_category_id=fuel.id)
    session.add(m); session.commit()
    from_merchant = _txn(session, "GALP", Category.OTHER_EXPENSE, merchant_id=m.id)
    from_legacy = _txn(session, "CONTINENTE", Category.GROCERIES)
    ambiguous = _txn(session, "MYSTERY", Category.OTHER_EXPENSE)

    report = refile_all(session, dry_run=False)

    assert from_merchant.category_id == fuel.id
    assert from_legacy.category_id == get_node(session, "food.groceries.supermarket").id
    assert ambiguous.category_id == get_node(session, UNSORTED_SLUG).id
    assert (report.filed_from_merchant, report.filed_from_legacy, report.sent_to_review) == (1, 1, 1)


def test_refile_dry_run_changes_nothing_and_rerun_skips_filed(session):
    ensure_taxonomy(session)
    t = _txn(session, "CONTINENTE", Category.GROCERIES)
    report = refile_all(session, dry_run=True)
    session.refresh(t)
    assert t.category_id is None and report.filed_from_legacy == 1
    refile_all(session, dry_run=False)
    assert refile_all(session, dry_run=False).already_filed == 1
```

- [ ] **Step 2:** run → FAIL (imports).
- [ ] **Step 3: Add to `app/services/taxonomy.py`**

```python
def file_transaction(session: Session, txn: Transaction, node: CategoryNode) -> None:
    """File txn under node and dual-write the legacy Category so old readers keep working."""
    txn.category_id = node.id
    txn.category = _legacy_for(session, node)
    session.add(txn)


def _legacy_for(session: Session, node: CategoryNode) -> Category:
    if node.level == 3 and node.name in SUB_LEGACY:
        return SUB_LEGACY[node.name]
    cat = session.get(CategoryNode, node.parent_id) if node.level == 3 else node
    for _kind, groups in SEED.items():
        for _g, cats in groups:
            for cname, _cad, legacy, _subs in cats:
                if cname == cat.name and legacy is not None:
                    return legacy
    return _KIND_LEGACY[node.kind]


def flow_of(txn: Transaction, node: Optional[CategoryNode]) -> str:
    """'in' / 'out' / 'neutral' for colouring and totals. Unsorted follows the transaction's own type."""
    if node is None or node.slug.startswith("unsorted"):
        return "in" if txn.transaction_type == TransactionType.CREDIT else (
            "neutral" if txn.transaction_type == TransactionType.TRANSFER else "out")
    return node.kind
```

- [ ] **Step 4: `app/services/taxonomy_refile.py`**

```python
"""One-off (re-runnable) backfill: file existing transactions into the category tree."""

from dataclasses import dataclass

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.merchant import Merchant
from app.models.transaction import Transaction
from app.services.taxonomy import UNSORTED_SLUG, file_transaction, get_node
from app.services.taxonomy_seed import LEGACY_TO_SLUG


@dataclass
class RefileReport:
    filed_from_merchant: int = 0
    filed_from_legacy: int = 0
    sent_to_review: int = 0
    already_filed: int = 0


def refile_all(session: Session, dry_run: bool = True) -> RefileReport:
    report = RefileReport()
    unsorted = get_node(session, UNSORTED_SLUG)
    for t in session.exec(select(Transaction)).all():
        if t.category_id is not None:
            report.already_filed += 1
            continue
        merchant = session.get(Merchant, t.merchant_id) if t.merchant_id else None
        if merchant and merchant.confirmed and merchant.default_category_id:
            node, bucket = session.get(CategoryNode, merchant.default_category_id), "filed_from_merchant"
        elif LEGACY_TO_SLUG.get(t.category):
            node, bucket = get_node(session, LEGACY_TO_SLUG[t.category]), "filed_from_legacy"
        else:
            node, bucket = unsorted, "sent_to_review"
        setattr(report, bucket, getattr(report, bucket) + 1)
        if not dry_run:
            file_transaction(session, t, node)
    if not dry_run:
        session.commit()
    return report
```

Note: filing under `unsorted` dual-writes legacy `OTHER_EXPENSE`; for a credit/transfer row that would lose its old legacy value, so in `file_transaction` skip the legacy overwrite when `node.slug.startswith("unsorted")`. Add that guard (`if node.slug.startswith("unsorted"): return` after setting `category_id`).

- [ ] **Step 5: `scripts/refile_to_taxonomy.py`** (follow the style of the existing scripts in `scripts/`):

```python
"""Re-file all transactions into the category tree. Dry-run unless --apply.
On the VPS: back up the DB first (docs/SYSADMIN.md)."""
import sys

from sqlmodel import Session

from app.db import engine
from app.services.taxonomy import ensure_taxonomy
from app.services.taxonomy_refile import refile_all

with Session(engine) as session:
    ensure_taxonomy(session)
    print(refile_all(session, dry_run="--apply" not in sys.argv))
```
Check `app/db.py` exports `engine`; if it uses a factory instead, mirror how `scripts/` neighbours obtain a session.

- [ ] **Step 6:** `pytest tests/test_taxonomy_refile.py tests/test_taxonomy.py -v` → PASS.
- [ ] **Step 7: Ready to commit:** `git commit -m "feat(categories): file_transaction, flow_of and history re-filing"`

---

### Task 4: Transactions list — tree column, tree filter, colours

**Files:**
- Modify: `app/routers/transactions.py` (list, `_apply_transaction_filters`, bulk-edit, `_lookup_dicts_for`), `app/templates/transactions/list.html`, `_rows.html`, `app/templates/base.html`
- Test: `tests/test_transactions_router.py` (append)

**Interfaces:**
- Consumes: `descendant_ids`, `path_label`, `flow_of`, `file_transaction`.
- Produces: query param `category_node` (slug, matches the node and all descendants); template context `node_options` (list of `{slug, label, level}`) and `row_flow` (dict txn_id → "in"/"out"/"neutral") and `node_labels` (dict txn_id → path); bulk-edit form field `new_category_node` (slug).

- [ ] **Step 1: Failing tests** (append). Reuse `_make_transaction` from this file.

```python
from app.services.taxonomy import ensure_taxonomy, file_transaction, get_node


def _filed(session, provider, slug, amount=10.0, ttype=TransactionType.DEBIT):
    t = _make_transaction(session, provider, Category.OTHER, amount)
    t.transaction_type = ttype
    file_transaction(session, t, get_node(session, slug))
    session.commit()
    return t


def test_list_shows_tree_path_and_flow_class(client, session):
    ensure_taxonomy(session)
    _filed(session, "PINGO DOCE", "food.groceries.supermarket")
    _filed(session, "RENT IN", "income.rental.house-rent", ttype=TransactionType.CREDIT)
    html = client.get("/financials/transactions").text
    assert "Food › Groceries › Supermarket" in html
    assert 'class="flow-out"' in html and 'class="flow-in"' in html


def test_filter_by_node_includes_descendants(client, session):
    ensure_taxonomy(session)
    _filed(session, "PINGO DOCE", "food.groceries.supermarket")
    _filed(session, "GALP", "transport.car-running.fuel")
    html = client.get("/financials/transactions?category_node=food").text
    assert "PINGO DOCE" in html and "GALP" not in html


def test_bulk_edit_files_under_node_and_dual_writes(client, session):
    ensure_taxonomy(session)
    t = _make_transaction(session, "EDP", Category.OTHER, 60.0)
    client.post("/financials/transactions/bulk-edit",
                data={"transaction_ids": [str(t.id)], "new_category_node": "housing.utilities.electricity"})
    session.refresh(t)
    assert t.category_id == get_node(session, "housing.utilities.electricity").id
    assert t.category == Category.ELECTRICITY
```

- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Router changes.**
  1. `_apply_transaction_filters` gains `category_node: Optional[str] = None`; when set: `ids = descendant_ids(session, get_node(session, category_node).id)` and `statement = statement.where(Transaction.category_id.in_(ids))`. This needs `session`; add it as the first parameter and update its callers (`_filtered_transactions`, `_count_filtered_transactions`, which already hold a session). Thread `category_node` through both, `list_transactions` (query param) and the `filters` dict.
  2. In `list_transactions` context add `node_options` built from all `CategoryNode` rows ordered by `sort_order` (`label` = `path_label`, level 3 only selectable; levels 1–2 selectable in the filter only), plus `row_flow` and `node_labels` computed per listed transaction via `session.get(CategoryNode, t.category_id)` / `flow_of` / `path_label` (batch-load nodes into a dict first to avoid per-row queries). Put this in a helper `_node_context(session, transactions)` and reuse it in `bulk_edit`.
  3. `bulk_edit`: read `new_category_node`; when present, `file_transaction(session, t, get_node(session, slug))` instead of the enum assignment (keep the legacy `new_category` branch until Task 5 removes the old select).
- [ ] **Step 4: Templates.** `list.html`: replace the category filter `<select name="category">` with `<select name="category_node">` iterating `node_options` (indent by level; value = slug; keep a hidden `category_node` input in the bulk form); replace the `new_category` select with `new_category_node` over level-3 options. `_rows.html`: `<tr class="flow-{{ row_flow.get(t.id, 'out') }}">` becomes `<tr class="flow-{{ ... }}">` — note the test expects `class="flow-out"` exactly, so render the class attribute with only the flow class; category cell shows `{{ node_labels.get(t.id) or t.category.value }}`; amount cell gets sign: `−` for out, `+` for in, none for neutral.
  `base.html` `<style>`: add
```css
tr.flow-out{background:#f7ebe9}tr.flow-out td.amt{color:#a8504a}
tr.flow-in{background:#e9f2ea}tr.flow-in td.amt{color:#4d7a56}
tr.flow-neutral{color:#76716a}
@media (prefers-color-scheme:dark){tr.flow-out{background:#3a2523}tr.flow-in{background:#22332a}}
```
  and give the amount `<td class="amt">`.
- [ ] **Step 5:** `pytest tests/test_transactions_router.py -v` → PASS (all pre-existing tests included).
- [ ] **Step 6: Look at it** with the `run` skill on a scratch DB copy that has had `refile_all(dry_run=False)` applied; confirm red/green rows and the tree filter in the browser pane.
- [ ] **Step 7: Ready to commit:** `git commit -m "feat(transactions): category tree column/filter and red/green rows"`

---

### Task 5: Classify new and unsorted transactions into the tree

**Files:**
- Modify: `app/services/classification_engine.py`, `app/services/bankapi/sync.py`, `app/routers/transactions.py` (needs-review), `app/templates/transactions/needs_review.html`/`_needs_review_rows.html`
- Test: `tests/test_classification_engine.py` (extend)

**Interfaces:**
- Consumes: `leaf_slugs`, `file_transaction`, `get_node`.
- Produces: `ResolvedMerchant.node_slug: str`; the LLM schema's `category` field becomes `node_slug` with `enum = leaf_slugs`; `classify_transaction` sets `merchant.default_category_id` and files the transaction (unless the merchant resolves to Unsorted, in which case the transaction stays Unsorted and appears in the Needs Review queue).

- [ ] **Step 1: Failing tests** (extend; use `tests/fakes/fake_gateway.py` as the neighbouring tests do, and build the reply through the same JSON the prompt asks for)

```python
@pytest.mark.asyncio
async def test_classify_files_new_merchant_under_tree_leaf(session, fake_gateway):
    ensure_taxonomy(session)
    fake_gateway.reply('{"canonical_name": "Galp", "node_slug": "transport.car-running.fuel", "nature": "essential"}')
    t = make_txn(session, "GALP LISBOA")        # helper already used by this module's tests
    await classify_transaction(session, t, gateway=fake_gateway)
    assert t.category_id == get_node(session, "transport.car-running.fuel").id
    assert t.category == Category.OTHER_EXPENSE   # dual-write via kind default


@pytest.mark.asyncio
async def test_unknown_slug_from_llm_is_a_resolution_error(session, fake_gateway):
    ensure_taxonomy(session)
    fake_gateway.reply('{"canonical_name": "X", "node_slug": "made.up.slug", "nature": "essential"}')
    with pytest.raises(MerchantResolutionError):
        await resolve_merchant_via_llm("X", gateway=fake_gateway)
```
Before writing these, read the top of `tests/test_classification_engine.py` and `tests/fakes/fake_gateway.py` and use the helpers/fixtures that exist there (the names `fake_gateway`, `make_txn`, `.reply` above are the shape to match, not guaranteed names — use the real ones).

- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Engine.** `_MERCHANT_SYSTEM_PROMPT` lists the leaf slugs (built at call time from `leaf_slugs(session)` and passed in; add `session` param to `resolve_merchant_via_llm`) with the instruction "choose the single best `node_slug`; use `unsorted.needs-review.needs-review` if unsure; income/refund/loan slugs only for credits". Schema `node_slug` enum = the slug list. After parsing, a slug not in the list raises `MerchantResolutionError`. `classify_transaction` stores `merchant.default_category_id`, keeps `merchant.default_category` dual-written via `_legacy_for`, and calls `file_transaction` when the node is not Unsorted. Existing merchants with `default_category_id` also file new transactions the same way (this is the "merchant memory").
- [ ] **Step 4: Bank sync.** Replace the two `merchant.default_category != Category.OTHER` blocks (`sync.py:225`, `:243`) with: after `classify`, file via the merchant's node if present (`txn.category_id`/legacy already handled inside `classify_transaction`); in the exception fallback leave `category_id` NULL (shows as unfiled, caught by Needs Review). Keep legacy `Category.OTHER` default in the constructors.
- [ ] **Step 5: Needs Review UI.** `confirm_merchant` accepts `category_node` (slug): sets `merchant.default_category_id`, `merchant.confirmed = True`, and files **all** that merchant's currently-Unsorted/unfiled transactions under the chosen node (one click fixes the whole history for that merchant). The review page's select lists level-3 options. Add a query to `get_needs_review_queue`: transactions with `category_id IS NULL` or in Unsorted, grouped by merchant.
  Test: posting `confirm` with a slug files every unsorted transaction of that merchant.
- [ ] **Step 6: Reclassify the 36% pile.** Add `reclassify_unsorted(session, gateway)` to `taxonomy_refile.py`: for each distinct merchant with Unsorted transactions, call `resolve_merchant_via_llm` once and, if the result is not Unsorted, set the merchant node and file its transactions (merchant stays `confirmed=False` so Pedro can still correct it). `scripts/refile_to_taxonomy.py --reclassify` runs it. Test with the fake gateway: two transactions of one merchant cost one LLM call.
- [ ] **Step 7:** `pytest -q` (whole suite) → PASS. No regression in overview/Ask because the legacy column is still written.
- [ ] **Step 8: Ready to commit:** `git commit -m "feat(categories): classify into the tree; one-click merchant review; reclassify unsorted"`

---

### Task 6: Roll out (needs Pedro's explicit word to push/deploy)

Order matters: the deploy runs `alembic upgrade head` on push, so the migration and seed hit the live DB at push time.

- [ ] Full suite green; migration verified up/down on a fresh scratch copy of the live DB (done in Task 2/4; repeat once more).
- [ ] **Before the push**: back up the live DB on the VPS (`cp /srv/home-hub/app/data/home_family.db <backup>`; use a path the SSH user can write — see `docs/SYSADMIN.md`).
- [ ] Count unconfirmed merchants and merchants with Unsorted rows on the VPS; if in the hundreds, fix the Needs Review page weight (render the option list once) before shipping.
- [ ] Pedro says "push" → `git subtree push --prefix="Home & Family" home_hub main` (auto-deploys; migration seeds the tree).
- [ ] On the VPS: `refile_to_taxonomy.py` (dry-run, read the report), then `--apply`, then `--reclassify` in the background with `nohup` and poll (per `docs/SYSADMIN.md` long-running-script guidance).
- [ ] Pedro opens `/financials/transactions`: check colours, the tree filter, and the Needs Review list size.

## Self-review
- **Spec coverage:** hierarchy 3 levels ✔ (T1) · budgets-on-sub-categories data shape ✔ (level-3 leaves, `cadence`; amounts in Plan 2) · monthly/yearly/loan cadence ✔ · income by source ✔ · internal transfers neutral ✔ · loans both directions (nodes now, reconciliation Plan 3) ✔ · red/green list ✔ (T4) · re-filing history ✔ (T3, T5.6) · auto-file confident, review unsure, merchant memory ✔ (T5) · forecasts/budgets → Plan 2 · loan balances → Plan 3.
- **Placeholder scan:** Task 4 steps 3–4 and Task 5 steps 3–5 describe edits to existing functions in prose because the exact surrounding code was read for the plan but the signatures thread through several callers; the implementer must open `app/routers/transactions.py` and `classification_engine.py` and apply them with the tests as the guide. Test helper names in Task 5 must be matched to the real fixtures.
- **Type consistency:** `category_node` (query/form slug), `node_slug` (LLM field), `UNSORTED_SLUG`, `file_transaction`, `flow_of`, `descendant_ids`, `path_label` are used with the same names throughout.
