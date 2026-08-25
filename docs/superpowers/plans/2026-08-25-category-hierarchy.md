# Category / Sub-category Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat 16-value `Category` enum with a DB-backed two-level hierarchy
(`CategoryGroup` > `SubCategory`), plus a generic `Tag` mechanism for cross-cutting labels
(Insurance is the first), across `Transaction`, `Merchant`, `Commitment`, the classification
engine, the Overview, and every category-selecting UI.

**Architecture:** Two new tables (`category_groups`, `sub_categories`) plus a generic tag/join
pair (`tags`, `sub_category_tags`), seeded from the already-approved hierarchy content. Existing
tables gain nullable FK columns alongside their old enum columns (kept, unused going forward, as
an audit trail). Every UI category dropdown gains a cascading sub-category dropdown wired via
htmx, matching this app's existing dynamic-dropdown pattern.

**Tech Stack:** FastAPI, SQLModel, Alembic (`render_as_batch=True`), Jinja2 + htmx, pytest.

**Spec:** `docs/superpowers/specs/2026-08-25-category-hierarchy-design.md`

## Naming Ruling (read before Task 1)

The spec names the new top-level table `Category`. `app/models/transaction.py` already defines a
Python class named `Category` (the old flat enum), imported by ~15 files across models, services,
routers, and tests. Renaming that enum to free up the name would be a wide, risky mechanical
change for no functional benefit. **Ruling:** the new top-level table is named `CategoryGroup`
instead (table `category_groups`) — same design, same columns, same semantics as the spec's
`Category` table, zero naming collision. The old `Category` enum class is untouched by this plan
except where a task explicitly says otherwise. `SubCategory` keeps its name from the spec (no
collision — nothing in this codebase is currently named `SubCategory`).

## Global Constraints

- Every new column is nullable; every migration is additive (no drops, no renames of existing
  columns).
- No SQLModel `Relationship` anywhere — plain FK columns, resolved via `{id: value}` lookup dicts
  built in routers/services, exactly like every other model in this codebase.
- `alembic/versions/` stays one linear chain; `render_as_batch=True` (SQLite).
- The old `category`/`default_category` enum columns on `Transaction`, `Merchant`, `Commitment`
  stay exactly as they are (including their `Category.OTHER` defaults) — no task in this plan
  changes their write-path. They become a frozen historical snapshot once the app stops reading
  them; dropping them is out of scope.
- One-off scripts (the seed migration's data, the production backfill script) follow
  `scripts/backfill_transaction_classification.py` / `scripts/merge_duplicate_merchants.py`'s
  convention: own docstring explaining why it exists and whether it's re-runnable, idempotent,
  tested against a scratch copy of the real ~4,850-row database before running against production.
- Real production data as of this plan: ~4,850 transactions, ~1,160 merchants, 0 Commitments.
- `UtilityType` (`app/models/utility_reading.py`) has exactly three members: `ELECTRICITY`,
  `WATER`, `TELECOM` — no `GAS` member. Any new Gas-vs-utility-detail-extraction logic must
  preserve this existing omission exactly (Gas bills never trigger utility-detail extraction
  today; this plan does not change that).

---

### Task 1: `CategoryGroup` / `SubCategory` / `Tag` / `SubCategoryTag` models + schema migration

**Files:**
- Create: `app/models/category.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_category_models.py`

**Interfaces:**
- Produces: `Direction` enum (`INCOME`, `EXPENSE`, `INFLOW`, `OUTFLOW`, `INTERNAL`), `CategoryGroup`
  model (`id`, `name` unique, `counts_as_spend: bool` default `True`, `created_at`), `SubCategory`
  model (`id`, `category_group_id` FK, `name`, `direction: Direction`, `created_at`), `Tag` model
  (`id`, `name` unique), `SubCategoryTag` model (`sub_category_id` FK, `tag_id` FK, composite PK).
  Every later task that touches categories imports from `app.models.category`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_category_models.py
from app.models.category import CategoryGroup, Direction, SubCategory, SubCategoryTag, Tag


def test_category_group_has_expected_fields(session):
    group = CategoryGroup(name="Housing", counts_as_spend=True)
    session.add(group)
    session.commit()
    session.refresh(group)

    assert group.id is not None
    assert group.name == "Housing"
    assert group.counts_as_spend is True
    assert group.created_at is not None


def test_sub_category_links_to_its_category_group(session):
    group = CategoryGroup(name="Utilities")
    session.add(group)
    session.commit()
    session.refresh(group)

    sub = SubCategory(category_group_id=group.id, name="Electricity", direction=Direction.EXPENSE)
    session.add(sub)
    session.commit()
    session.refresh(sub)

    assert sub.category_group_id == group.id
    assert sub.direction == Direction.EXPENSE


def test_tag_links_to_sub_category_via_join_table(session):
    group = CategoryGroup(name="Housing")
    session.add(group)
    session.commit()
    session.refresh(group)
    sub = SubCategory(category_group_id=group.id, name="Property Insurance", direction=Direction.EXPENSE)
    session.add(sub)
    session.commit()
    session.refresh(sub)

    tag = Tag(name="Insurance")
    session.add(tag)
    session.commit()
    session.refresh(tag)

    link = SubCategoryTag(sub_category_id=sub.id, tag_id=tag.id)
    session.add(link)
    session.commit()

    from sqlmodel import select
    found = session.exec(
        select(SubCategoryTag).where(SubCategoryTag.sub_category_id == sub.id)
    ).first()
    assert found.tag_id == tag.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_category_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.category'`

- [ ] **Step 3: Write the model file**

```python
# app/models/category.py
"""CategoryGroup / SubCategory: the two-level classification hierarchy
(e.g. Housing > Mortgage) that replaces the old flat Category enum, plus
a generic Tag mechanism for cross-cutting labels that span multiple
sub-categories (Insurance is the first: Property Insurance, Health
Insurance, and Vehicle Insurance all carry it, so spend can be queried
across all three regardless of which top-level group they sit under)."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class Direction(str, Enum):
    INCOME = "income"
    EXPENSE = "expense"
    INFLOW = "inflow"
    OUTFLOW = "outflow"
    INTERNAL = "internal"


class CategoryGroup(SQLModel, table=True):
    __tablename__ = "category_groups"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    counts_as_spend: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SubCategory(SQLModel, table=True):
    __tablename__ = "sub_categories"

    id: Optional[int] = Field(default=None, primary_key=True)
    category_group_id: int = Field(foreign_key="category_groups.id", index=True)
    name: str
    direction: Direction
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Tag(SQLModel, table=True):
    __tablename__ = "tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)


class SubCategoryTag(SQLModel, table=True):
    __tablename__ = "sub_category_tags"

    sub_category_id: int = Field(foreign_key="sub_categories.id", primary_key=True)
    tag_id: int = Field(foreign_key="tags.id", primary_key=True)
```

- [ ] **Step 4: Register the module in `app/models/__init__.py`**

Add this line alongside the other model imports (anywhere in the list — order doesn't matter,
`noqa: F401` matches the existing convention):

```python
from app.models.category import CategoryGroup, Direction, SubCategory, SubCategoryTag, Tag  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_category_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Generate the Alembic migration**

```bash
cd "Home & Family"
DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic upgrade head
DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic revision --autogenerate -m "add category_groups, sub_categories, tags, sub_category_tags tables"
```

Open the generated file in `alembic/versions/`. Verify it contains four `op.create_table(...)`
calls (`category_groups`, `sub_categories`, `tags`, `sub_category_tags`) with the columns above,
and that `sub_category_tags`' primary key is the composite `(sub_category_id, tag_id)`. Verify
`down_revision` points at the actual current head (check with
`DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic heads` beforehand if unsure).

- [ ] **Step 7: Verify the migration round-trips cleanly**

```bash
rm -f /tmp/category_hierarchy_scratch.db
DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic upgrade head
DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic downgrade -1
DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic upgrade head
sqlite3 /tmp/category_hierarchy_scratch.db ".tables" | grep -E "category_groups|sub_categories|tags|sub_category_tags"
```

Expected: all four tables present after the final upgrade, no errors during downgrade/re-upgrade.

- [ ] **Step 8: Commit**

```bash
git add app/models/category.py app/models/__init__.py alembic/versions/*.py tests/test_category_models.py
git commit -m "feat: add CategoryGroup/SubCategory/Tag models and schema migration"
```

---

### Task 2: Seed data migration

**Files:**
- Create: `alembic/versions/<new-revision>_seed_category_hierarchy.py`
- Test: `tests/test_category_hierarchy_seed.py`

**Interfaces:**
- Consumes: `CategoryGroup`, `SubCategory`, `Tag`, `SubCategoryTag`, `Direction` (Task 1)
- Produces: the fully-populated hierarchy in the database — every later task that reads
  `CategoryGroup`/`SubCategory` by name (classification engine, categorization, overview,
  backfill script, UI) depends on these exact rows existing with these exact names.

This is a **data-only** migration (no schema changes) — it runs `op.bulk_insert`-style inserts in
`upgrade()` and deletes them in `downgrade()`. The full hierarchy, exactly as approved (A-Z at both
levels), as `(category_group_name, counts_as_spend, [(sub_category_name, direction, tag_or_None)])`:

```python
HIERARCHY = [
    ("Debt & Transfers", False, [
        ("ATM Withdrawal", "outflow", None),
        ("Internal Transfer", "internal", None),
        ("Loan Received", "inflow", None),
        ("Loan Repayment Made", "outflow", None),
        ("Loan Repayment Received", "inflow", None),
    ]),
    ("Education", True, [
        ("Books", "expense", None),
        ("Extracurricular Activities", "expense", None),
        ("Stationery & Supplies", "expense", None),
        ("Tuition", "expense", None),
    ]),
    ("Groceries", True, [
        ("Specialty & Local", "expense", None),
        ("Supermarket", "expense", None),
    ]),
    ("Health", True, [
        ("Dental", "expense", None),
        ("Doctor Consultations", "expense", None),
        ("Health Insurance", "expense", "Insurance"),
        ("Medication & Pharmacy", "expense", None),
        ("Therapy & Psychology", "expense", None),
    ]),
    ("Housing", True, [
        ("Condo Fees", "expense", None),
        ("Furniture & Appliances", "expense", None),
        ("Maintenance & Repairs", "expense", None),
        ("Mortgage", "expense", None),
        ("Property Insurance", "expense", "Insurance"),
        ("Property Tax", "expense", None),
    ]),
    ("Income", False, [
        ("Freelance & Practice Income", "income", None),
        ("Government & Social Security", "income", None),
        ("Investment Income", "income", None),
        ("Other Income", "income", None),
        ("Refunds & Reimbursements", "income", None),
        ("Rental Income", "income", None),
        ("Salary & Employment", "income", None),
    ]),
    ("Other", True, [
        ("Other Expense", "expense", None),
        ("Uncategorized", "expense", None),
    ]),
    ("Restaurants", True, [
        ("Cafes & Coffee", "expense", None),
        ("Restaurants", "expense", None),
        ("Takeout & Delivery", "expense", None),
    ]),
    ("Shopping", True, [
        ("Clothing", "expense", None),
        ("Electronics", "expense", None),
        ("Gifts", "expense", None),
        ("Home Goods", "expense", None),
    ]),
    ("Subscriptions", True, [
        ("Memberships", "expense", None),
        ("Software", "expense", None),
        ("Streaming", "expense", None),
    ]),
    ("Transport", True, [
        ("Fuel", "expense", None),
        ("Public Transport", "expense", None),
        ("Tolls & Parking", "expense", None),
        ("Vehicle Insurance", "expense", "Insurance"),
        ("Vehicle Maintenance", "expense", None),
    ]),
    ("Utilities", True, [
        ("Electricity", "expense", None),
        ("Gas", "expense", None),
        ("Internet & Mobile", "expense", None),
        ("Water", "expense", None),
    ]),
]
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_category_hierarchy_seed.py
"""Verifies the seed migration's data, run against a scratch copy of the
schema (not the real migration history -- this test builds the schema
from SQLModel.metadata like every other test in this suite, then
replicates the seed migration's insert logic directly against it, since
Alembic migrations themselves aren't executed by the test suite)."""
from sqlmodel import select

from app.models.category import CategoryGroup, SubCategory, Tag, SubCategoryTag
from app.services.category_seed_data import HIERARCHY, seed_category_hierarchy


def test_seed_creates_all_category_groups(session):
    seed_category_hierarchy(session)
    groups = session.exec(select(CategoryGroup)).all()
    assert len(groups) == 12
    assert {g.name for g in groups} == {name for name, _, _ in HIERARCHY}


def test_seed_sets_counts_as_spend_correctly(session):
    seed_category_hierarchy(session)
    income = session.exec(select(CategoryGroup).where(CategoryGroup.name == "Income")).first()
    housing = session.exec(select(CategoryGroup).where(CategoryGroup.name == "Housing")).first()
    assert income.counts_as_spend is False
    assert housing.counts_as_spend is True


def test_seed_creates_sub_categories_under_correct_parent(session):
    seed_category_hierarchy(session)
    housing = session.exec(select(CategoryGroup).where(CategoryGroup.name == "Housing")).first()
    subs = session.exec(select(SubCategory).where(SubCategory.category_group_id == housing.id)).all()
    assert {s.name for s in subs} == {
        "Condo Fees", "Furniture & Appliances", "Maintenance & Repairs",
        "Mortgage", "Property Insurance", "Property Tax",
    }


def test_seed_creates_insurance_tag_on_exactly_three_sub_categories(session):
    seed_category_hierarchy(session)
    tag = session.exec(select(Tag).where(Tag.name == "Insurance")).first()
    assert tag is not None
    links = session.exec(select(SubCategoryTag).where(SubCategoryTag.tag_id == tag.id)).all()
    assert len(links) == 3
    tagged_sub_names = set()
    for link in links:
        sub = session.get(SubCategory, link.sub_category_id)
        tagged_sub_names.add(sub.name)
    assert tagged_sub_names == {"Property Insurance", "Health Insurance", "Vehicle Insurance"}


def test_seed_is_idempotent(session):
    seed_category_hierarchy(session)
    seed_category_hierarchy(session)
    groups = session.exec(select(CategoryGroup)).all()
    assert len(groups) == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_category_hierarchy_seed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.category_seed_data'`

- [ ] **Step 3: Write `app/services/category_seed_data.py`**

Contains the exact `HIERARCHY` literal shown above, plus:

```python
"""The approved Category/Sub-category hierarchy content, and the
idempotent function that inserts it. Used by both the Alembic seed
migration and this module's own tests -- the migration imports
seed_category_hierarchy() rather than duplicating the insert logic."""

from sqlmodel import Session, select

from app.models.category import CategoryGroup, Direction, SubCategory, SubCategoryTag, Tag

HIERARCHY = [
    # ... exact literal from above ...
]


def seed_category_hierarchy(session: Session) -> None:
    """Inserts the full hierarchy if it isn't already present. Idempotent:
    checks for each CategoryGroup/SubCategory/Tag by name before creating
    it, so re-running (e.g. a second `alembic upgrade` attempt after a
    partial failure) never creates duplicates."""
    for group_name, counts_as_spend, subs in HIERARCHY:
        group = session.exec(select(CategoryGroup).where(CategoryGroup.name == group_name)).first()
        if group is None:
            group = CategoryGroup(name=group_name, counts_as_spend=counts_as_spend)
            session.add(group)
            session.commit()
            session.refresh(group)

        for sub_name, direction, tag_name in subs:
            sub = session.exec(
                select(SubCategory).where(
                    SubCategory.category_group_id == group.id, SubCategory.name == sub_name
                )
            ).first()
            if sub is None:
                sub = SubCategory(category_group_id=group.id, name=sub_name, direction=Direction(direction))
                session.add(sub)
                session.commit()
                session.refresh(sub)

            if tag_name:
                tag = session.exec(select(Tag).where(Tag.name == tag_name)).first()
                if tag is None:
                    tag = Tag(name=tag_name)
                    session.add(tag)
                    session.commit()
                    session.refresh(tag)
                existing_link = session.exec(
                    select(SubCategoryTag).where(
                        SubCategoryTag.sub_category_id == sub.id, SubCategoryTag.tag_id == tag.id
                    )
                ).first()
                if existing_link is None:
                    session.add(SubCategoryTag(sub_category_id=sub.id, tag_id=tag.id))
                    session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_category_hierarchy_seed.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Write the Alembic data migration**

```python
"""seed category hierarchy data

Revision ID: <generated>
Revises: <Task 1's revision id>
Create Date: ...
"""
from alembic import op
from sqlmodel import Session

from app.services.category_seed_data import seed_category_hierarchy
from app.models.category import CategoryGroup, SubCategory, Tag, SubCategoryTag

revision = "<generated>"
down_revision = "<Task 1's revision id>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    seed_category_hierarchy(session)


def downgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    session.query(SubCategoryTag).delete()
    session.query(Tag).delete()
    session.query(SubCategory).delete()
    session.query(CategoryGroup).delete()
    session.commit()
```

Generate the file with `alembic revision -m "seed category hierarchy data"` (not
`--autogenerate` — there's no schema change to detect, only data), then fill in the body above,
setting `down_revision` to Task 1's migration's revision id (check with
`DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic heads`).

- [ ] **Step 6: Verify against the scratch DB**

```bash
rm -f /tmp/category_hierarchy_scratch.db
DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic upgrade head
sqlite3 /tmp/category_hierarchy_scratch.db "select count(*) from category_groups;"
sqlite3 /tmp/category_hierarchy_scratch.db "select count(*) from sub_categories;"
sqlite3 /tmp/category_hierarchy_scratch.db "select count(*) from sub_category_tags;"
```

Expected: 12 category_groups, 46 sub_categories (sum the per-group counts above: 5+4+2+5+6+7+2+3+4+3+5+4 = 46), 3 sub_category_tags. Then verify downgrade/re-upgrade round-trips cleanly the same way as Task 1's Step 7.

- [ ] **Step 7: Commit**

```bash
git add app/services/category_seed_data.py alembic/versions/*.py tests/test_category_hierarchy_seed.py
git commit -m "feat: seed the approved Category/Sub-category hierarchy data"
```

---

### Task 3: Add `category_group_id`/`sub_category_id` FKs to Transaction, Merchant, Commitment

**Files:**
- Modify: `app/models/transaction.py`, `app/models/merchant.py`, `app/models/commitment.py`
- Test: `tests/test_transaction_model.py`, `tests/test_merchant_model.py` (or wherever each
  model's existing field tests live — check `tests/` for the actual current filenames before
  adding; follow whatever pattern already exists there)

**Interfaces:**
- Consumes: `CategoryGroup`, `SubCategory` (Task 1)
- Produces: `Transaction.category_group_id`, `Transaction.sub_category_id`,
  `Merchant.default_category_group_id`, `Merchant.default_sub_category_id`,
  `Commitment.category_group_id`, `Commitment.sub_category_id` — every later task reads/writes
  these exact field names.

- [ ] **Step 1: Write the failing tests**

Add to whichever existing transaction-model test file already exists (find it with
`ls tests/ | grep -i transaction`):

```python
def test_transaction_has_new_category_group_and_sub_category_fields(session):
    from app.models.category import CategoryGroup, SubCategory, Direction
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction

    document = Document(
        filename="x.pdf", file_path="/tmp/x.pdf", content_hash="new-fk-test", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    group = CategoryGroup(name="New FK Test Group")
    session.add(group)
    session.commit()
    session.refresh(group)
    sub = SubCategory(category_group_id=group.id, name="New FK Test Sub", direction=Direction.EXPENSE)
    session.add(sub)
    session.commit()
    session.refresh(sub)

    transaction = Transaction(
        document_id=document.id, provider="X", amount=10.0,
        category_group_id=group.id, sub_category_id=sub.id,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    assert transaction.category_group_id == group.id
    assert transaction.sub_category_id == sub.id
```

Add analogous tests for `Merchant.default_category_group_id`/`default_sub_category_id` and
`Commitment.category_group_id`/`sub_category_id` in their respective existing test files, following
the same shape.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ -k "new_category_group_and_sub_category or default_category_group_and or commitment_has_new_category" -v`
Expected: FAIL — `category_group_id` is not a valid field for `Transaction`/`Merchant`/`Commitment`

- [ ] **Step 3: Add the fields**

`app/models/transaction.py`, inside the `Transaction` class, alongside `linked_transaction_id`:

```python
    category_group_id: Optional[int] = Field(default=None, foreign_key="category_groups.id")
    sub_category_id: Optional[int] = Field(default=None, foreign_key="sub_categories.id")
```

`app/models/merchant.py`, inside the `Merchant` class:

```python
    default_category_group_id: Optional[int] = Field(default=None, foreign_key="category_groups.id")
    default_sub_category_id: Optional[int] = Field(default=None, foreign_key="sub_categories.id")
```

`app/models/commitment.py`, inside the `Commitment` class:

```python
    category_group_id: Optional[int] = Field(default=None, foreign_key="category_groups.id")
    sub_category_id: Optional[int] = Field(default=None, foreign_key="sub_categories.id")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ -k "new_category_group_and_sub_category or default_category_group_and or commitment_has_new_category" -v`
Expected: PASS

- [ ] **Step 5: Generate and verify the migration**

```bash
rm -f /tmp/category_hierarchy_scratch.db
DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic upgrade head
DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic revision --autogenerate -m "add category_group_id and sub_category_id FKs to transactions, merchants, commitments"
```

Open the generated file. Verify it adds exactly 6 columns across 3 `batch_alter_table` blocks
(`transactions`: `category_group_id`, `sub_category_id`; `merchants`:
`default_category_group_id`, `default_sub_category_id`; `commitments`: `category_group_id`,
`sub_category_id`), each with an explicit named foreign key constraint (name the constraints
explicitly, e.g. `fk_transactions_category_group_id_category_groups`, matching this project's
existing migration-naming convention — see `d63cba4c90e6_add_merchant_id_to_transactions.py` for
the pattern), all columns `nullable=True`.

- [ ] **Step 6: Verify round-trip**

```bash
rm -f /tmp/category_hierarchy_scratch.db
DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic upgrade head
DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic downgrade -1
DATABASE_PATH=/tmp/category_hierarchy_scratch.db alembic upgrade head
pytest -q
```

Expected: clean round-trip, full test suite still green.

- [ ] **Step 7: Commit**

```bash
git add app/models/transaction.py app/models/merchant.py app/models/commitment.py alembic/versions/*.py tests/
git commit -m "feat: add category_group_id/sub_category_id FKs to Transaction, Merchant, Commitment"
```

---

### Task 4: `category_service.py` — tree, valid-subs lookup, pair resolution, tag lookup

**Files:**
- Create: `app/services/category_service.py`
- Test: `tests/test_category_service.py`

**Interfaces:**
- Consumes: `CategoryGroup`, `SubCategory`, `Tag`, `SubCategoryTag` (Task 1), seeded data (Task 2)
- Produces: `CategoryGroupNode` dataclass (`id`, `name`, `sub_categories: list[SubCategory]`),
  `get_category_tree(session) -> list[CategoryGroupNode]`,
  `get_valid_sub_categories(session, category_group_id) -> list[SubCategory]`,
  `ResolvedCategoryPair` dataclass (`category_group_id: Optional[int]`,
  `sub_category_id: Optional[int]`),
  `resolve_category_pair(session, category_name, sub_category_name) -> ResolvedCategoryPair`,
  `get_sub_category_ids_for_tag(session, tag_name) -> list[int]`. Every later task that resolves a
  category by name (classification engine, categorization, backfill script) or needs the tree for
  a dropdown (UI tasks) uses these functions — do not re-implement the same queries elsewhere.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_category_service.py
from app.services.category_seed_data import seed_category_hierarchy
from app.services.category_service import (
    get_category_tree, get_valid_sub_categories, resolve_category_pair, get_sub_category_ids_for_tag,
)


def test_get_category_tree_returns_all_groups_with_their_subs(session):
    seed_category_hierarchy(session)
    tree = get_category_tree(session)
    assert len(tree) == 12
    housing = next(node for node in tree if node.name == "Housing")
    assert {s.name for s in housing.sub_categories} == {
        "Condo Fees", "Furniture & Appliances", "Maintenance & Repairs",
        "Mortgage", "Property Insurance", "Property Tax",
    }


def test_get_valid_sub_categories_scoped_to_one_group(session):
    seed_category_hierarchy(session)
    tree = get_category_tree(session)
    utilities = next(node for node in tree if node.name == "Utilities")
    subs = get_valid_sub_categories(session, utilities.id)
    assert {s.name for s in subs} == {"Electricity", "Gas", "Internet & Mobile", "Water"}


def test_resolve_category_pair_valid_pair(session):
    seed_category_hierarchy(session)
    result = resolve_category_pair(session, "Housing", "Mortgage")
    assert result.category_group_id is not None
    assert result.sub_category_id is not None


def test_resolve_category_pair_sub_category_belongs_to_different_parent(session):
    """A sub-category name that's real but under the WRONG parent must not
    silently attach -- category_group_id still resolves, sub_category_id
    does not."""
    seed_category_hierarchy(session)
    result = resolve_category_pair(session, "Utilities", "Mortgage")
    assert result.category_group_id is not None
    assert result.sub_category_id is None


def test_resolve_category_pair_unknown_category_name(session):
    seed_category_hierarchy(session)
    result = resolve_category_pair(session, "Not A Real Category", "Also Not Real")
    assert result.category_group_id is None
    assert result.sub_category_id is None


def test_resolve_category_pair_no_sub_category_given(session):
    seed_category_hierarchy(session)
    result = resolve_category_pair(session, "Housing", None)
    assert result.category_group_id is not None
    assert result.sub_category_id is None


def test_get_sub_category_ids_for_tag_returns_the_three_insurance_subs(session):
    seed_category_hierarchy(session)
    ids = get_sub_category_ids_for_tag(session, "Insurance")
    assert len(ids) == 3


def test_get_sub_category_ids_for_tag_unknown_tag_returns_empty(session):
    seed_category_hierarchy(session)
    assert get_sub_category_ids_for_tag(session, "Not A Real Tag") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_category_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.category_service'`

- [ ] **Step 3: Write `app/services/category_service.py`**

```python
"""Read-side helpers for the Category/Sub-category hierarchy: the full
tree (for building dropdowns and LLM prompts), a single group's valid
sub-categories (for cascading-dropdown endpoints), name-based pair
resolution (for classification), and tag-based sub-category lookup (for
cross-cutting queries like 'all insurance spend')."""

from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session, select

from app.models.category import CategoryGroup, SubCategory, SubCategoryTag, Tag


@dataclass
class CategoryGroupNode:
    id: int
    name: str
    sub_categories: list[SubCategory] = field(default_factory=list)


def get_category_tree(session: Session) -> list[CategoryGroupNode]:
    groups = session.exec(select(CategoryGroup).order_by(CategoryGroup.name)).all()
    subs_by_group: dict[int, list[SubCategory]] = {}
    for sub in session.exec(select(SubCategory).order_by(SubCategory.name)).all():
        subs_by_group.setdefault(sub.category_group_id, []).append(sub)
    return [
        CategoryGroupNode(id=g.id, name=g.name, sub_categories=subs_by_group.get(g.id, []))
        for g in groups
    ]


def get_valid_sub_categories(session: Session, category_group_id: int) -> list[SubCategory]:
    return session.exec(
        select(SubCategory)
        .where(SubCategory.category_group_id == category_group_id)
        .order_by(SubCategory.name)
    ).all()


@dataclass
class ResolvedCategoryPair:
    category_group_id: Optional[int]
    sub_category_id: Optional[int]


def resolve_category_pair(
    session: Session, category_name: Optional[str], sub_category_name: Optional[str]
) -> ResolvedCategoryPair:
    """Validates a (category_name, sub_category_name) pair against the real
    hierarchy. A sub_category_name that doesn't belong to category_name (or
    doesn't exist at all) is dropped -- category_group_id is still set if
    category_name alone is valid, never silently attached to the wrong
    parent. Both None if category_name itself doesn't exist."""
    if not category_name:
        return ResolvedCategoryPair(category_group_id=None, sub_category_id=None)

    group = session.exec(select(CategoryGroup).where(CategoryGroup.name == category_name)).first()
    if group is None:
        return ResolvedCategoryPair(category_group_id=None, sub_category_id=None)

    if not sub_category_name:
        return ResolvedCategoryPair(category_group_id=group.id, sub_category_id=None)

    sub = session.exec(
        select(SubCategory).where(
            SubCategory.category_group_id == group.id, SubCategory.name == sub_category_name
        )
    ).first()
    return ResolvedCategoryPair(category_group_id=group.id, sub_category_id=sub.id if sub else None)


def get_sub_category_ids_for_tag(session: Session, tag_name: str) -> list[int]:
    tag = session.exec(select(Tag).where(Tag.name == tag_name)).first()
    if tag is None:
        return []
    links = session.exec(select(SubCategoryTag).where(SubCategoryTag.tag_id == tag.id)).all()
    return [link.sub_category_id for link in links]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_category_service.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/category_service.py tests/test_category_service.py
git commit -m "feat: add category_service read helpers (tree, valid subs, pair resolution, tag lookup)"
```

---

### Task 5: Classification engine — dynamic prompt, `(category, sub_category)` resolution

**Files:**
- Modify: `app/services/classification_engine.py`
- Test: `tests/test_classification_engine.py`

**Interfaces:**
- Consumes: `resolve_category_pair`, `get_category_tree` (Task 4); seeded hierarchy (Task 2)
- Produces: `ResolvedMerchant` gains `category_group_id: Optional[int]` and
  `sub_category_id: Optional[int]` fields (replacing its old `category: Category` field);
  `classify_transaction` sets `Transaction.category_group_id`/`sub_category_id` (in addition to
  the existing `merchant_id`/`account_id`/`nature` it already sets); `detect_debt_candidates`'s
  category filter moves from the old enum tuple to `CategoryGroup.name.in_(["Debt & Transfers", "Other"])`.

- [ ] **Step 1: Write the failing tests**

Find the existing test file (`ls tests/ | grep classification_engine`) and add:

```python
def test_resolve_merchant_via_llm_sets_category_group_and_sub_category(session, monkeypatch):
    from app.services.category_seed_data import seed_category_hierarchy
    seed_category_hierarchy(session)

    async def fake_create(*args, **kwargs):
        class FakeContent:
            text = '{"canonical_name": "EDP", "category": "Utilities", "sub_category": "Electricity", "nature": "essential"}'
        class FakeMessage:
            content = [FakeContent()]
        return FakeMessage()

    from app.services import classification_engine as ce

    class FakeClient:
        class messages:
            create = staticmethod(fake_create)

    resolved = await ce.resolve_merchant_via_llm("EDP ENERGIA", client=FakeClient())

    assert resolved.canonical_name == "EDP"
    assert resolved.category_group_id is not None
    assert resolved.sub_category_id is not None


def test_resolve_merchant_via_llm_hallucinated_pair_falls_back_gracefully(session, monkeypatch):
    from app.services.category_seed_data import seed_category_hierarchy
    seed_category_hierarchy(session)

    async def fake_create(*args, **kwargs):
        class FakeContent:
            text = '{"canonical_name": "Weird Co", "category": "Utilities", "sub_category": "Not A Real Sub", "nature": "essential"}'
        class FakeMessage:
            content = [FakeContent()]
        return FakeMessage()

    from app.services import classification_engine as ce

    class FakeClient:
        class messages:
            create = staticmethod(fake_create)

    resolved = await ce.resolve_merchant_via_llm("WEIRD CO CHARGE", client=FakeClient())

    assert resolved.category_group_id is not None  # "Utilities" is real
    assert resolved.sub_category_id is None  # "Not A Real Sub" isn't a child of Utilities


def test_detect_debt_candidates_uses_new_category_groups(session):
    from app.services.category_seed_data import seed_category_hierarchy
    from app.services.category_service import get_category_tree
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction
    from app.services.classification_engine import detect_debt_candidates

    seed_category_hierarchy(session)
    tree = get_category_tree(session)
    debt_group = next(n for n in tree if n.name == "Debt & Transfers")
    loan_repayment_sub = next(s for s in debt_group.sub_categories if s.name == "Loan Repayment Made")

    document = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="debt-test", source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="TRF CRED SEPA+ P/ SOME PERSON", amount=600.0,
        category_group_id=debt_group.id, sub_category_id=loan_repayment_sub.id,
    )
    session.add(transaction)
    session.commit()

    candidates = detect_debt_candidates(session)
    assert len(candidates) == 1
```

Note: the existing test suite likely mocks the Anthropic client differently already — **read the
existing tests in this file first** and match whatever mocking convention is already established
there (the shape above is illustrative of the assertions needed, not necessarily the exact mock
plumbing already in use).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_classification_engine.py -k "category_group_and_sub_category or hallucinated_pair or new_category_groups" -v`
Expected: FAIL — `ResolvedMerchant` has no `category_group_id` attribute yet

- [ ] **Step 3: Rework `resolve_merchant_via_llm` and `ResolvedMerchant`**

Replace the existing `ResolvedMerchant` dataclass and `_MERCHANT_SYSTEM_PROMPT`/`resolve_merchant_via_llm`:

```python
from app.services.category_service import get_category_tree, resolve_category_pair


def _build_merchant_system_prompt(session: Session) -> str:
    tree = get_category_tree(session)
    lines = []
    for node in tree:
        sub_names = ", ".join(s.name for s in node.sub_categories)
        lines.append(f"- {node.name}: {sub_names}")
    categories_block = "\n".join(lines)
    return f"""You resolve a raw bank statement provider string to a canonical merchant identity. \
Respond with ONLY a JSON object, no prose, matching this shape exactly:

{{
  "canonical_name": "string, a clean human-readable merchant name, e.g. 'Modelo Hiper'",
  "category": "one of the top-level categories below",
  "sub_category": "one of that category's sub-categories below",
  "nature": "essential or discretionary"
}}

Valid categories and their sub-categories:
{categories_block}"""


@dataclass
class ResolvedMerchant:
    canonical_name: str
    category_group_id: Optional[int]
    sub_category_id: Optional[int]
    nature: Nature


async def resolve_merchant_via_llm(
    session: Session, raw_provider: str, client: Optional[AsyncAnthropic] = None
) -> ResolvedMerchant:
    """Ask Claude to resolve a raw provider string to a canonical merchant
    name and a (category, sub_category) pair drawn from the live DB tree
    (never a hardcoded list), falling back gracefully if Claude's answer
    doesn't validate against the real hierarchy. Called only when the
    rules tier (normalize_provider + a Merchant lookup) finds no existing
    match."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    system_prompt = _build_merchant_system_prompt(session)

    message = await anthropic_client.messages.create(
        model=_MERCHANT_MODEL,
        max_tokens=256,
        thinking={"type": "disabled"},
        system=system_prompt,
        messages=[
            {"role": "user", "content": f"Resolve this provider string as JSON: {raw_provider!r}"}
        ],
    )

    try:
        raw_text = strip_json_fences(message.content[0].text)
        data = json.loads(raw_text)
        pair = resolve_category_pair(session, data.get("category"), data.get("sub_category"))
        return ResolvedMerchant(
            canonical_name=data["canonical_name"],
            category_group_id=pair.category_group_id,
            sub_category_id=pair.sub_category_id,
            nature=Nature(data["nature"]),
        )
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise MerchantResolutionError(f"Could not resolve merchant: {exc}") from exc
```

Note the function signature gained a leading `session: Session` parameter — update its one caller
(`classify_transaction`, in the same file) accordingly.

- [ ] **Step 4: Update `classify_transaction` to pass `session` through and set the new fields**

In `classify_transaction`, the `resolve_merchant_via_llm(transaction.provider, client=client)` call
becomes `resolve_merchant_via_llm(session, transaction.provider, client=client)`, and where a new
`Merchant` is constructed from `resolved`:

```python
        merchant = Merchant(
            canonical_name=resolved.canonical_name,
            default_category_group_id=resolved.category_group_id,
            default_sub_category_id=resolved.sub_category_id,
            default_nature=resolved.nature,
            normalized_key=normalized_key,
        )
```

And where `classify_transaction` sets fields on `transaction` from the resolved `merchant`, add:

```python
    if transaction.category_group_id is None:
        transaction.category_group_id = merchant.default_category_group_id
    if transaction.sub_category_id is None:
        transaction.sub_category_id = merchant.default_sub_category_id
```

placed alongside the existing `if transaction.nature is None: transaction.nature = merchant.default_nature`
line.

- [ ] **Step 5: Update `detect_debt_candidates`'s category filter**

Replace:

```python
_DEBT_CANDIDATE_CATEGORIES = (Category.TRANSFER, Category.OTHER_EXPENSE)
```

and the query's `.where(Transaction.category.in_(_DEBT_CANDIDATE_CATEGORIES))` with a query joined
through the new `category_group_id`:

```python
_DEBT_CANDIDATE_GROUP_NAMES = ("Debt & Transfers", "Other")


def detect_debt_candidates(session: Session) -> list[Transaction]:
    debt_group_ids = [
        g.id for g in session.exec(
            select(CategoryGroup).where(CategoryGroup.name.in_(_DEBT_CANDIDATE_GROUP_NAMES))
        ).all()
    ]
    statement = (
        select(Transaction)
        .where(Transaction.category_group_id.in_(debt_group_ids))
        .where(Transaction.amount > _DEBT_CANDIDATE_MIN_AMOUNT)
        .where(Transaction.debt_id.is_(None))
        .where(Transaction.debt_candidate_reviewed.isnot(True))
    )
    transactions = session.exec(statement).all()
    return [t for t in transactions if _DEBT_TRANSFER_MARKER_RE.search(t.provider)]
```

Add `from app.models.category import CategoryGroup` to the file's imports.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_classification_engine.py -v`
Expected: PASS — full file, including every pre-existing test in it (several will need their own
fixture updates to seed the hierarchy first and construct `ResolvedMerchant`/assert against the new
fields instead of the old `category` field; update each one you find failing for this reason, same
mechanical shape as the new tests above).

- [ ] **Step 7: Commit**

```bash
git add app/services/classification_engine.py tests/test_classification_engine.py
git commit -m "feat: classification engine resolves (category, sub_category) from the live DB tree"
```

---

### Task 6: `categorization.py` — free-text hint to `(category_name, sub_category_name)`

**Files:**
- Modify: `app/services/categorization.py`
- Test: `tests/test_categorization.py`

**Interfaces:**
- Produces: `normalize_category(hint: str) -> tuple[Optional[str], Optional[str]]` — returns
  `(category_name, sub_category_name)`, replacing the old `Category` enum return type. Task 7
  (pipeline) is the only caller and gets updated to match.

- [ ] **Step 1: Write the failing test**

```python
def test_normalize_category_electricity_maps_to_utilities_electricity():
    from app.services.categorization import normalize_category
    assert normalize_category("electricity") == ("Utilities", "Electricity")


def test_normalize_category_unknown_hint_returns_none_none():
    from app.services.categorization import normalize_category
    assert normalize_category("something nobody would ever say") == (None, None)


def test_normalize_category_empty_hint_returns_none_none():
    from app.services.categorization import normalize_category
    assert normalize_category("") == (None, None)


def test_normalize_category_ambiguous_hint_returns_category_only():
    from app.services.categorization import normalize_category
    # "shopping" can't confidently pick Clothing vs Electronics vs Gifts vs
    # Home Goods from the hint alone.
    assert normalize_category("shopping") == ("Shopping", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_categorization.py -v`
Expected: FAIL — `normalize_category("electricity")` currently returns `Category.ELECTRICITY`, not a tuple

- [ ] **Step 3: Rewrite `app/services/categorization.py`**

```python
"""Normalizes free-text category hints from extraction into a
(category_name, sub_category_name) pair drawn from the approved
hierarchy. Confident mappings return both; anything ambiguous (the hint
names a top-level concept but not which sub-category) returns the
category alone; anything unrecognized returns (None, None)."""

from typing import Optional

_SYNONYMS: dict[str, tuple[str, Optional[str]]] = {
    "electricity": ("Utilities", "Electricity"),
    "power": ("Utilities", "Electricity"),
    "energy": ("Utilities", "Electricity"),
    "water": ("Utilities", "Water"),
    "gas": ("Utilities", "Gas"),
    "telecom": ("Utilities", "Internet & Mobile"),
    "telco": ("Utilities", "Internet & Mobile"),
    "internet": ("Utilities", "Internet & Mobile"),
    "phone": ("Utilities", "Internet & Mobile"),
    "mobile": ("Utilities", "Internet & Mobile"),
    "insurance": ("Health", None),  # ambiguous parent (Housing/Health/Transport) -- see spec
    "subscriptions": ("Subscriptions", None),
    "subscription": ("Subscriptions", None),
    "streaming": ("Subscriptions", "Streaming"),
    "groceries": ("Groceries", None),
    "grocery": ("Groceries", None),
    "supermarket": ("Groceries", "Supermarket"),
    "health": ("Health", None),
    "medical": ("Health", "Medication & Pharmacy"),
    "pharmacy": ("Health", "Medication & Pharmacy"),
    "home": ("Housing", None),
    "maintenance": ("Housing", "Maintenance & Repairs"),
    "income": ("Income", None),
    "salary": ("Income", "Salary & Employment"),
    "transfer": ("Debt & Transfers", "Internal Transfer"),
    "atm_withdrawal": ("Debt & Transfers", "ATM Withdrawal"),
    "atm": ("Debt & Transfers", "ATM Withdrawal"),
    "withdrawal": ("Debt & Transfers", "ATM Withdrawal"),
    "restaurants": ("Restaurants", None),
    "restaurant": ("Restaurants", "Restaurants"),
    "dining": ("Restaurants", None),
    "shopping": ("Shopping", None),
    "retail": ("Shopping", None),
    "other_expense": ("Other", "Other Expense"),
}


def normalize_category(hint: str) -> tuple[Optional[str], Optional[str]]:
    """Map a free-text category hint to the closest (category_name,
    sub_category_name) pair, falling back to (None, None) when nothing
    matches. The caller (app.services.pipeline) resolves these names
    against the real hierarchy via category_service.resolve_category_pair,
    which is what actually validates them and sets FKs -- this function
    only proposes names."""
    if not hint:
        return (None, None)
    return _SYNONYMS.get(hint.strip().lower(), (None, None))
```

Note: `"insurance"` deliberately maps to `("Health", None)` rather than `(None, None)` — an
arbitrary-but-documented choice among three valid parents (Housing/Health/Transport), matching the
spec's acknowledgment that Insurance's parent is genuinely ambiguous from a bare hint alone;
`"other_expense"` maps to `("Other", "Other Expense")` since that synonym key is itself the exact
name of a real sub-category, an unambiguous 1:1 case unlike the others left with `None` sub.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_categorization.py -v`
Expected: PASS — including every pre-existing test in the file (update any that asserted against
the old `Category` enum return value to assert against the new tuple instead).

- [ ] **Step 5: Commit**

```bash
git add app/services/categorization.py tests/test_categorization.py
git commit -m "feat: normalize_category returns a (category_name, sub_category_name) pair"
```

---

### Task 7: Wire the new categorization into `pipeline.py`

**Files:**
- Modify: `app/services/pipeline.py`
- Test: `tests/test_e2e_bill_flow.py` (or wherever the existing pipeline end-to-end tests live —
  check with `ls tests/ | grep -i pipeline` and `ls tests/ | grep -i e2e` first)

**Interfaces:**
- Consumes: `normalize_category` (Task 6, new tuple return), `resolve_category_pair` (Task 4)
- Produces: `_ingest_bill`/`_ingest_statement` set `Transaction.category_group_id`/`sub_category_id`
  on every newly-created Transaction, in addition to the old `category` enum field (which keeps
  getting its harmless default value, per Global Constraints — this task does not touch the old
  field's write path at all, it simply stops being the field anything downstream reads).

- [ ] **Step 1: Write the failing test**

```python
async def test_ingest_bill_sets_category_group_and_sub_category(session, monkeypatch):
    from app.services.category_seed_data import seed_category_hierarchy
    from app.models.document import Document, DocumentSource, DocumentStatus
    from app.services.pipeline import ingest_document
    from app.services import pipeline as pipeline_module
    from app.services.extraction import ExtractedBill
    from datetime import date

    seed_category_hierarchy(session)

    async def fake_classify_document(path):
        return "bill"

    async def fake_extract_bill(path):
        return ExtractedBill(
            provider="EDP", category_hint="electricity", amount=45.0, currency="EUR",
            due_date=date(2026, 9, 1), paid_date=None, statement_period=None,
        )

    async def fake_classify_transaction(session, transaction, client=None):
        # Simulate no merchant match found, so this test isolates
        # category_hint-based resolution from merchant-based resolution.
        return None

    monkeypatch.setattr(pipeline_module, "classify_document", fake_classify_document)
    monkeypatch.setattr(pipeline_module, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline_module, "classify_transaction", fake_classify_transaction)

    document = Document(
        filename="edp.pdf", file_path="/tmp/edp.pdf", content_hash="pipeline-cat-test",
        source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    result = await ingest_document(session, document)

    from sqlmodel import select
    from app.models.transaction import Transaction
    transaction = session.exec(select(Transaction).where(Transaction.document_id == result.id)).first()
    assert transaction.category_group_id is not None
    assert transaction.sub_category_id is not None
```

Check the existing test file's actual monkeypatching/fixture conventions first (this illustrates
the assertions needed; match whatever mocking helpers already exist there rather than introducing
a new pattern).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_e2e_bill_flow.py -k "category_group_and_sub_category" -v`
Expected: FAIL — `Transaction.category_group_id` is `None` (pipeline doesn't set it yet)

- [ ] **Step 3: Update `_ingest_bill`**

Add the import `from app.services.category_service import resolve_category_pair` at the top of
`pipeline.py`. Where the `Transaction` is constructed, resolve the hint-derived pair first and pass
both new fields through:

```python
    category_name, sub_category_name = normalize_category(extracted.category_hint)
    pair = resolve_category_pair(session, category_name, sub_category_name)

    transaction = Transaction(
        document_id=document.id,
        provider=extracted.provider,
        category_group_id=pair.category_group_id,
        sub_category_id=pair.sub_category_id,
        transaction_type=TransactionType.DEBIT,
        amount=extracted.amount,
        currency=extracted.currency,
        due_date=extracted.due_date,
        paid_date=extracted.paid_date,
        statement_period=extracted.statement_period,
    )
```

(Note: the old `category=normalize_category(extracted.category_hint)` line is removed entirely —
per Global Constraints, the old column is left to its Field default rather than actively written.)

- [ ] **Step 4: Fix the utility-detail-extraction gate, which read the old field**

The existing code checks `if transaction.category.value in _UTILITY_CATEGORY_VALUES:` to decide
whether to run utility-detail extraction. Since the old field is no longer actively set, this must
switch to checking the new `sub_category_id` against a name lookup. Replace
`_UTILITY_CATEGORY_VALUES` and the gate:

```python
_UTILITY_SUB_CATEGORY_TO_TYPE = {
    "Electricity": UtilityType.ELECTRICITY,
    "Water": UtilityType.WATER,
    "Internet & Mobile": UtilityType.TELECOM,
    # Deliberately no "Gas" entry: UtilityType has no GAS member (see
    # Global Constraints) -- Gas bills have never triggered utility-detail
    # extraction, and this preserves that exactly.
}
```

and where the gate is checked:

```python
    utility_type = None
    if transaction.sub_category_id is not None:
        sub_category = session.get(SubCategory, transaction.sub_category_id)
        if sub_category is not None:
            utility_type = _UTILITY_SUB_CATEGORY_TO_TYPE.get(sub_category.name)

    utility_detail_failure_reason = None
    if utility_type is not None:
        try:
            detail = await extract_utility_detail(document.file_path, utility_type.value)
            reading = UtilityReading(
                document_id=document.id,
                utility_type=utility_type,
                period_label=detail.period_label,
                billing_period_start=detail.billing_period_start,
                billing_period_end=detail.billing_period_end,
                invoice_number=detail.invoice_number,
                consumption_value=detail.consumption_value,
                consumption_unit=detail.consumption_unit,
                cost_total=transaction.amount,
                cost_per_unit=(
                    transaction.amount / detail.consumption_value
                    if detail.consumption_value
                    else None
                ),
                energy_cost=detail.energy_cost,
                power_cost=detail.power_cost,
                fees_taxes_cost=detail.fees_taxes_cost,
                vat_cost=detail.vat_cost,
            )
            session.add(reading)
            session.commit()
        except Exception as exc:
            session.rollback()
            print(f"utility detail extraction failed for document {document.id}: {exc}")
            utility_detail_failure_reason = f"utility detail extraction failed: {exc}"
```

Add `from app.models.category import SubCategory` to the imports.

- [ ] **Step 5: Update `_ingest_statement`'s Transaction construction identically**

Same `category_name, sub_category_name = normalize_category(item.category_hint)` +
`pair = resolve_category_pair(session, category_name, sub_category_name)` +
`category_group_id=pair.category_group_id, sub_category_id=pair.sub_category_id` treatment, in
place of the old `category=normalize_category(item.category_hint)` line. `_ingest_statement` never
had utility-detail-extraction logic (that's bill-only), so no equivalent Step 4 change is needed
there.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_e2e_bill_flow.py -v` and `pytest tests/ -k statement -v`
Expected: PASS, including every pre-existing test in both files (update any that asserted on the
old `.category` field).

- [ ] **Step 7: Commit**

```bash
git add app/services/pipeline.py tests/test_e2e_bill_flow.py
git commit -m "feat: wire category_group_id/sub_category_id through the ingestion pipeline"
```

---

### Task 8: Overview service — `counts_as_spend` replaces hardcoded category exclusion

**Files:**
- Modify: `app/services/overview_service.py`
- Test: `tests/test_overview_service.py`

**Interfaces:**
- Consumes: `CategoryGroup.counts_as_spend` (Task 1)
- Produces: `get_category_comparison` excludes transactions whose `CategoryGroup.counts_as_spend
  == False` (via `Transaction.category_group_id`), instead of the old
  `_COMPARISON_EXCLUDED_CATEGORIES` enum tuple. Drill-down URLs and displayed category names in
  `CategoryComparisonRow` now come from the joined `CategoryGroup.name`, not
  `Transaction.category.value`.

- [ ] **Step 1: Write the failing test**

```python
def test_category_comparison_excludes_non_spend_groups(session):
    from app.services.category_seed_data import seed_category_hierarchy
    from app.services.category_service import get_category_tree
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction, TransactionType
    from app.services.overview_service import get_category_comparison
    from datetime import date

    seed_category_hierarchy(session)
    tree = get_category_tree(session)
    income_group = next(n for n in tree if n.name == "Income")
    housing_group = next(n for n in tree if n.name == "Housing")
    mortgage_sub = next(s for s in housing_group.sub_categories if s.name == "Mortgage")
    salary_sub = next(s for s in income_group.sub_categories if s.name == "Salary & Employment")

    document = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="overview-cat-test", source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)

    session.add(Transaction(
        document_id=document.id, provider="Mortgage payment", amount=800.0,
        transaction_type=TransactionType.DEBIT, category_group_id=housing_group.id,
        sub_category_id=mortgage_sub.id, paid_date=date(2026, 8, 5),
    ))
    session.add(Transaction(
        document_id=document.id, provider="Salary", amount=2000.0,
        transaction_type=TransactionType.CREDIT, category_group_id=income_group.id,
        sub_category_id=salary_sub.id, paid_date=date(2026, 8, 1),
    ))
    session.commit()

    rows = get_category_comparison(session, today=date(2026, 8, 10))

    category_names = {r.category for r in rows}
    assert "Housing" in category_names
    assert "Income" not in category_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_overview_service.py -k "excludes_non_spend_groups" -v`
Expected: FAIL — the query still filters on the old `Transaction.category` enum column, which is
`Category.OTHER` (the default) for both rows in this test, so both or neither get excluded
incorrectly

- [ ] **Step 3: Rework `get_category_comparison`**

Replace `_COMPARISON_EXCLUDED_CATEGORIES` and the query that uses it:

```python
def get_category_comparison(
    session: Session, today: date, rolling_months: int = _COMPARISON_ROLLING_MONTHS
) -> list[CategoryComparisonRow]:
    cutoff = _month_start(today) - timedelta(days=31 * (rolling_months + 1))
    non_spend_group_ids = [
        g.id for g in session.exec(
            select(CategoryGroup).where(CategoryGroup.counts_as_spend == False)  # noqa: E712
        ).all()
    ]
    statement = select(Transaction).where(
        Transaction.transaction_type == TransactionType.DEBIT,
        Transaction.category_group_id.is_not(None),
        Transaction.category_group_id.notin_(non_spend_group_ids) if non_spend_group_ids else True,
        Transaction.paid_date >= cutoff,
        Transaction.paid_date <= today,
    )

    category_group_ids_seen = set()
    per_month_category: dict[tuple[str, int], float] = {}
    for t in session.exec(statement):
        if t.paid_date is None:
            continue
        key = (_month_key(t.paid_date), t.category_group_id)
        per_month_category[key] = per_month_category.get(key, 0.0) + t.amount
        category_group_ids_seen.add(t.category_group_id)

    group_names = {
        g.id: g.name
        for g in (session.exec(select(CategoryGroup).where(CategoryGroup.id.in_(category_group_ids_seen))).all() if category_group_ids_seen else [])
    }

    current_key = _month_key(today)
    history_periods = _complete_months_before(today, rolling_months)

    computed = []
    for group_id in category_group_ids_seen:
        current_value = per_month_category.get((current_key, group_id), 0.0)
        history_values = [per_month_category.get((p, group_id), 0.0) for p in history_periods]
        rolling_avg = sum(history_values) / len(history_values) if history_values else 0.0
        if current_value == 0.0 and rolling_avg == 0.0:
            continue
        delta_pct = round((current_value - rolling_avg) / rolling_avg * 100.0, 1) if rolling_avg else None
        computed.append((group_id, current_value, rolling_avg, delta_pct))

    computed.sort(key=lambda r: r[1], reverse=True)
    max_value = max((r[1] for r in computed), default=0.0)
    month_start = _month_start(today).isoformat()
    today_iso = today.isoformat()

    return [
        CategoryComparisonRow(
            category=group_names[group_id], current_value=current_value, rolling_avg_value=rolling_avg,
            delta_pct=delta_pct,
            bar_pct=round(current_value / max_value * 100.0, 1) if max_value else 0.0,
            drill_down_url=f"/transactions?category={group_names[group_id]}&date_from={month_start}&date_to={today_iso}",
        )
        for group_id, current_value, rolling_avg, delta_pct in computed
    ]
```

Add `from app.models.category import CategoryGroup` to the file's imports; remove the now-unused
`_COMPARISON_EXCLUDED_CATEGORIES` constant and `Category` import if nothing else in the file needs
it (check first — `Category` may still be used elsewhere in this file for an unrelated reason;
only remove what's actually dead).

Note the drill-down URL now passes the plain group **name** (e.g. `category=Housing`) rather than
the old enum's lowercase `.value` (e.g. `category=home`) — Task 11 in this same plan updates the
Transactions filter to match on `CategoryGroup.name` instead of the old enum, so this is
consistent, not a dangling mismatch.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_overview_service.py -v`
Expected: PASS — including every pre-existing test that builds `Transaction(category=Category.X, ...)`
fixtures for category-comparison tests; update each to use `category_group_id`/`sub_category_id`
instead (seed the hierarchy first via `seed_category_hierarchy(session)`, look up the relevant
group/sub via `get_category_tree`, same pattern as the new test above).

- [ ] **Step 5: Commit**

```bash
git add app/services/overview_service.py tests/test_overview_service.py
git commit -m "feat: category-comparison exclusion driven by CategoryGroup.counts_as_spend"
```

---

### Task 9: Production backfill script for the ~4,850 existing transactions

**Files:**
- Create: `scripts/backfill_category_hierarchy.py`
- Test: `tests/test_backfill_category_hierarchy.py`

**Interfaces:**
- Consumes: seeded hierarchy (Task 2), `CategoryGroup`/`SubCategory` (Task 1)
- Produces: every existing `Transaction` gets `category_group_id` set wherever the old flat
  `category` maps confidently (per the spec's mapping table), `sub_category_id` set only where
  genuinely unambiguous, both left `NULL` where the old category was itself ambiguous.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backfill_category_hierarchy.py
from datetime import date

from app.models.category import CategoryGroup, SubCategory
from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Transaction
from app.services.category_seed_data import seed_category_hierarchy
from scripts.backfill_category_hierarchy import backfill_category_hierarchy


def _txn(session, document, category: Category) -> Transaction:
    t = Transaction(document_id=document.id, provider="X", amount=10.0, category=category, paid_date=date(2026, 1, 1))
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def test_backfill_maps_electricity_to_utilities_electricity(session):
    seed_category_hierarchy(session)
    document = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="backfill-1", source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)
    t = _txn(session, document, Category.ELECTRICITY)

    backfill_category_hierarchy(session)
    session.refresh(t)

    group = session.get(CategoryGroup, t.category_group_id)
    sub = session.get(SubCategory, t.sub_category_id)
    assert group.name == "Utilities"
    assert sub.name == "Electricity"


def test_backfill_leaves_ambiguous_categories_sub_category_null(session):
    seed_category_hierarchy(session)
    document = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="backfill-2", source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)
    t = _txn(session, document, Category.HOME)

    backfill_category_hierarchy(session)
    session.refresh(t)

    group = session.get(CategoryGroup, t.category_group_id)
    assert group.name == "Housing"
    assert t.sub_category_id is None


def test_backfill_leaves_other_expense_fully_unset(session):
    seed_category_hierarchy(session)
    document = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="backfill-3", source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)
    t = _txn(session, document, Category.OTHER_EXPENSE)

    backfill_category_hierarchy(session)
    session.refresh(t)

    assert t.category_group_id is None
    assert t.sub_category_id is None


def test_backfill_is_idempotent(session):
    seed_category_hierarchy(session)
    document = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="backfill-4", source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)
    t = _txn(session, document, Category.ELECTRICITY)

    backfill_category_hierarchy(session)
    backfill_category_hierarchy(session)
    session.refresh(t)

    group = session.get(CategoryGroup, t.category_group_id)
    assert group.name == "Utilities"


def test_backfill_skips_transactions_already_migrated(session):
    """A transaction that already has category_group_id set (e.g. ingested
    fresh after this plan's other tasks landed) must not be touched, even
    if its old flat category would map somewhere else -- this script only
    fills gaps, it never overwrites an existing assignment."""
    seed_category_hierarchy(session)
    tree_groups = {g.name: g for g in session.exec(select(CategoryGroup)).all()}
    document = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="backfill-5", source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)
    t = Transaction(
        document_id=document.id, provider="X", amount=10.0, category=Category.ELECTRICITY,
        category_group_id=tree_groups["Shopping"].id, paid_date=date(2026, 1, 1),
    )
    session.add(t)
    session.commit()
    session.refresh(t)

    backfill_category_hierarchy(session)
    session.refresh(t)

    assert t.category_group_id == tree_groups["Shopping"].id
```

(Add `from sqlmodel import select` to the test file's imports for the last test.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backfill_category_hierarchy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.backfill_category_hierarchy'`

- [ ] **Step 3: Write the script**

```python
"""One-off historical migration: sets Transaction.category_group_id (and
sub_category_id where genuinely unambiguous) from each transaction's old
flat Category enum value, for every real transaction ingested before the
Category/Sub-category hierarchy existed.

Mapping (see docs/superpowers/specs/2026-08-25-category-hierarchy-design.md
"Migrating the ~4,850 Existing Transactions" for the full rationale):
unambiguous old categories (one obvious new sub-category) get both fields
set; categories whose old value doesn't distinguish between multiple real
sub-categories (Groceries, Restaurants, Subscriptions) get only
category_group_id set; categories that were themselves a catch-all
(Insurance -- could be Housing/Health/Transport; Other Expense -- could be
almost anything, including brand-new Education/Transport spend) are left
fully unset, surfaced via the Needs Review queue's "Needs sub-category"
section (for the category_group_id-only cases) for Pedro to assign by
hand -- no guessed defaults.

Idempotent: only touches transactions where category_group_id IS NULL, so
re-running after a partial failure, or after new transactions have since
been ingested with the new fields already set by the live pipeline, is
safe -- it only ever fills gaps, never overwrites.

Not part of the reviewed application code.

Usage:
    python scripts/backfill_category_hierarchy.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from app.db import engine
from app.models.category import CategoryGroup, SubCategory
from app.models.transaction import Category, Transaction

# old Category value -> (new CategoryGroup name, new SubCategory name or None)
_MAPPING: dict[Category, tuple[str, str | None]] = {
    Category.ELECTRICITY: ("Utilities", "Electricity"),
    Category.WATER: ("Utilities", "Water"),
    Category.GAS: ("Utilities", "Gas"),
    Category.TELECOM: ("Utilities", "Internet & Mobile"),
    Category.GROCERIES: ("Groceries", None),
    Category.RESTAURANTS: ("Restaurants", None),
    Category.SUBSCRIPTIONS: ("Subscriptions", None),
    Category.TRANSFER: ("Debt & Transfers", "Internal Transfer"),
    Category.ATM_WITHDRAWAL: ("Debt & Transfers", "ATM Withdrawal"),
    Category.HOME: ("Housing", None),
    Category.INCOME: ("Income", None),
    Category.HEALTH: ("Health", None),
    Category.SHOPPING: ("Shopping", None),
    Category.OTHER: ("Other", "Uncategorized"),
    # Category.INSURANCE and Category.OTHER_EXPENSE deliberately absent --
    # both left fully unset (see module docstring).
}


def backfill_category_hierarchy(session: Session) -> None:
    groups_by_name = {g.name: g for g in session.exec(select(CategoryGroup)).all()}
    subs_by_group_and_name: dict[tuple[int, str], SubCategory] = {}
    for sub in session.exec(select(SubCategory)).all():
        subs_by_group_and_name[(sub.category_group_id, sub.name)] = sub

    transactions = session.exec(
        select(Transaction).where(Transaction.category_group_id.is_(None))
    ).all()

    updated = 0
    skipped_no_mapping = 0
    for t in transactions:
        mapping = _MAPPING.get(t.category)
        if mapping is None:
            skipped_no_mapping += 1
            continue
        group_name, sub_name = mapping
        group = groups_by_name.get(group_name)
        if group is None:
            skipped_no_mapping += 1
            continue
        t.category_group_id = group.id
        if sub_name:
            sub = subs_by_group_and_name.get((group.id, sub_name))
            if sub is not None:
                t.sub_category_id = sub.id
        session.add(t)
        updated += 1
    session.commit()

    print(f"Transactions updated:              {updated}")
    print(f"Transactions left unset (no mapping): {skipped_no_mapping}")


if __name__ == "__main__":
    with Session(engine) as _session:
        backfill_category_hierarchy(_session)
```

The function takes an explicit `session: Session` argument so tests can inject their own (matching
the test code in Step 1); the `if __name__ == "__main__":` block is the only place that opens a
real session against the module-level `engine`, matching every other one-off script's convention
in this repo.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill_category_hierarchy.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_category_hierarchy.py tests/test_backfill_category_hierarchy.py
git commit -m "feat: add backfill script mapping existing transactions to the new category hierarchy"
```

*(Running this script against the real production database, and running
`scripts/merge_duplicate_merchants.py`-style verification afterward, is a deployment step — not
part of this plan's task loop. It happens after the branch merges and deploys, the same way this
project has always run its production backfills, directly by the project owner or a follow-up
session, not automatically as part of implementation.)*

---

### Task 10: Needs Review — new "Needs sub-category" section

**Files:**
- Modify: `app/services/classification_engine.py` (the `NeedsReviewQueue` dataclass and
  `get_needs_review_queue`), `app/templates/transactions/_needs_review_rows.html`,
  `app/routers/transactions.py`
- Test: `tests/test_classification_engine.py`, `tests/test_transactions_router.py`

**Interfaces:**
- Consumes: `get_valid_sub_categories` (Task 4)
- Produces: `NeedsReviewQueue.needs_sub_category: list[Transaction]`; a new
  `POST /transactions/{transaction_id}/set-sub-category` route

- [ ] **Step 1: Write the failing test**

```python
def test_needs_review_queue_includes_needs_sub_category(session):
    from app.services.category_seed_data import seed_category_hierarchy
    from app.services.category_service import get_category_tree
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction
    from app.services.classification_engine import get_needs_review_queue

    seed_category_hierarchy(session)
    tree = get_category_tree(session)
    housing = next(n for n in tree if n.name == "Housing")

    document = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="needs-sub-test", source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)

    session.add(Transaction(document_id=document.id, provider="X", amount=10.0, category_group_id=housing.id))
    session.commit()

    queue = get_needs_review_queue(session)
    assert len(queue.needs_sub_category) == 1
```

Add to `tests/test_transactions_router.py`:

```python
def test_set_sub_category_route_updates_transaction(client, session):
    from app.services.category_seed_data import seed_category_hierarchy
    from app.services.category_service import get_category_tree

    seed_category_hierarchy(session)
    tree = get_category_tree(session)
    housing = next(n for n in tree if n.name == "Housing")
    mortgage = next(s for s in housing.sub_categories if s.name == "Mortgage")

    transaction = _make_transaction(session, "MORTGAGE PAYMENT", Category.OTHER_EXPENSE, 800.0)
    transaction.category_group_id = housing.id
    session.add(transaction)
    session.commit()

    response = client.post(
        f"/transactions/{transaction.id}/set-sub-category", data={"sub_category_id": mortgage.id}
    )

    assert response.status_code == 200
    session.refresh(transaction)
    assert transaction.sub_category_id == mortgage.id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_classification_engine.py -k needs_sub_category -v` and
`pytest tests/test_transactions_router.py -k set_sub_category -v`
Expected: FAIL — `NeedsReviewQueue` has no `needs_sub_category` field; the route doesn't exist

- [ ] **Step 3: Extend `NeedsReviewQueue` and `get_needs_review_queue`**

```python
@dataclass
class NeedsReviewQueue:
    unconfirmed_merchants: list[Merchant]
    recurring_candidates: list[Merchant]
    debt_candidates: list[Transaction]
    unclassified_transactions: list[Transaction]
    needs_sub_category: list[Transaction]


def get_needs_review_queue(session: Session) -> NeedsReviewQueue:
    unconfirmed = session.exec(
        select(Merchant).where(Merchant.confirmed == False)  # noqa: E712
    ).all()
    unclassified = session.exec(
        select(Transaction).where(Transaction.merchant_id.is_(None))
    ).all()
    needs_sub_category = session.exec(
        select(Transaction).where(
            Transaction.category_group_id.is_not(None),
            Transaction.sub_category_id.is_(None),
        )
    ).all()
    return NeedsReviewQueue(
        unconfirmed_merchants=unconfirmed,
        recurring_candidates=detect_recurring_candidates(session),
        debt_candidates=detect_debt_candidates(session),
        unclassified_transactions=unclassified,
        needs_sub_category=needs_sub_category,
    )
```

- [ ] **Step 4: Add the router endpoint**

In `app/routers/transactions.py`, add (near the other per-transaction POST routes):

```python
@router.post("/{transaction_id}/set-sub-category")
async def set_sub_category(request: Request, transaction_id: int, session: Session = Depends(get_session)):
    transaction = session.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    form = await request.form()
    sub_category_id = form.get("sub_category_id")
    if sub_category_id:
        transaction.sub_category_id = int(sub_category_id)
        session.add(transaction)
        session.commit()

    return templates.TemplateResponse(
        request, "transactions/_needs_review_rows.html", _needs_review_context(session)
    )
```

- [ ] **Step 5: Update `_needs_review_context` and the template**

`_needs_review_context` needs the full category tree available to render each row's sub-category
`<select>`, scoped to that transaction's already-known `category_group_id`:

```python
def _needs_review_context(session: Session) -> dict:
    return {
        "queue": get_needs_review_queue(session),
        "categories": list(Category),
        "natures": list(Nature),
        "category_tree": get_category_tree(session),
    }
```

Add `from app.services.category_service import get_category_tree, get_valid_sub_categories` to the
router's imports.

Add a new section to `app/templates/transactions/_needs_review_rows.html`, following the existing
`<section id="...">` pattern used by every other section in that file:

```html
<section id="needs-sub-category">
  <h2>Needs sub-category</h2>
  {% if queue.needs_sub_category %}
  <table class="review-table">
    <thead>
      <tr><th>Provider</th><th>Category</th><th>Sub-category</th><th></th></tr>
    </thead>
    <tbody>
      {% for t in queue.needs_sub_category %}
      {% set group = (category_tree | selectattr("id", "equalto", t.category_group_id) | first) %}
      <tr>
        <td>{{ t.provider }}</td>
        <td>{{ group.name if group else "" }}</td>
        <td>
          <select name="sub_category_id">
            {% for sub in (group.sub_categories if group else []) %}
            <option value="{{ sub.id }}">{{ sub.name }}</option>
            {% endfor %}
          </select>
        </td>
        <td>
          <button
            hx-post="/transactions/{{ t.id }}/set-sub-category"
            hx-include="closest tr"
            hx-target="#needs-review-body"
            hx-swap="innerHTML"
          >Save</button>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p>Nothing needs a sub-category.</p>
  {% endif %}
</section>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_classification_engine.py -v` and `pytest tests/test_transactions_router.py -v`
Expected: PASS, full files

- [ ] **Step 7: Commit**

```bash
git add app/services/classification_engine.py app/routers/transactions.py app/templates/transactions/_needs_review_rows.html tests/test_classification_engine.py tests/test_transactions_router.py
git commit -m "feat: add Needs sub-category section to the Needs Review queue"
```

---

### Task 11: Cascading dropdown endpoint + wire into Transactions bulk-edit

**Files:**
- Create: `app/routers/categories.py`
- Modify: `app/main.py` (register the new router), `app/routers/transactions.py` (bulk-edit),
  `app/templates/transactions/list.html` (the bulk-edit fieldset)
- Test: `tests/test_categories_router.py`, `tests/test_transactions_router.py`

**Interfaces:**
- Consumes: `get_valid_sub_categories`, `get_category_tree` (Task 4)
- Produces: `GET /category-groups/sub-categories?category_group_id=X` returning an HTML `<option>`
  fragment; `bulk_edit` accepts `new_sub_category_id` and sets
  `Transaction.sub_category_id`/`category_group_id` on selected rows.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_categories_router.py
def test_sub_categories_endpoint_returns_options_for_the_given_group(client, session):
    from app.services.category_seed_data import seed_category_hierarchy
    from app.services.category_service import get_category_tree

    seed_category_hierarchy(session)
    tree = get_category_tree(session)
    utilities = next(n for n in tree if n.name == "Utilities")

    response = client.get("/category-groups/sub-categories", params={"category_group_id": utilities.id})

    assert response.status_code == 200
    assert "Electricity" in response.text
    assert "Gas" in response.text
    assert "Mortgage" not in response.text  # belongs to Housing, not Utilities


def test_sub_categories_endpoint_unknown_group_returns_only_the_placeholder_option(client, session):
    response = client.get("/category-groups/sub-categories", params={"category_group_id": 999999})
    assert response.status_code == 200
    assert response.text.count("<option") == 1  # just the "(no change)" placeholder


def test_sub_categories_endpoint_no_group_selected_returns_only_the_placeholder_option(client, session):
    response = client.get("/category-groups/sub-categories")
    assert response.status_code == 200
    assert response.text.count("<option") == 1
```

Add to `tests/test_transactions_router.py`:

```python
def test_bulk_edit_applies_new_category_group_and_sub_category(client, session):
    from app.services.category_seed_data import seed_category_hierarchy
    from app.services.category_service import get_category_tree

    seed_category_hierarchy(session)
    tree = get_category_tree(session)
    utilities = next(n for n in tree if n.name == "Utilities")
    electricity = next(s for s in utilities.sub_categories if s.name == "Electricity")

    t1 = _make_transaction(session, "SHOP Z", Category.OTHER_EXPENSE, 10.0)

    response = client.post("/transactions/bulk-edit", data={
        "transaction_ids": [str(t1.id)],
        "new_category_group_id": str(utilities.id),
        "new_sub_category_id": str(electricity.id),
    })

    assert response.status_code == 200
    session.refresh(t1)
    assert t1.category_group_id == utilities.id
    assert t1.sub_category_id == electricity.id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_categories_router.py -v` and `pytest tests/test_transactions_router.py -k bulk_edit_applies_new_category_group -v`
Expected: FAIL — router/route don't exist yet; `new_category_group_id` isn't handled by `bulk_edit`

- [ ] **Step 3: Write `app/routers/categories.py`**

The route takes `category_group_id` as a **query** parameter (not a path parameter) with a fixed
name, deliberately decoupled from whatever `name` attribute the calling `<select>` needs for its
own form's real submission (Task 11's select is named `new_category_group_id` for `bulk_edit` to
read; Task 12's is named `category_group_id` to match the existing filter convention; Task 13's is
also `category_group_id`). Every caller sends the query parameter under this endpoint's fixed name
via `hx-vals`, shown in Step 6 below — that decoupling is what lets one endpoint serve all three
forms.

```python
"""Shared cascading-dropdown endpoint: given a CategoryGroup id, returns
the <option> HTML for its valid SubCategories, always prefixed with a
"(no change)" placeholder option. Used by every UI surface that offers a
category dropdown (Transactions bulk-edit, the Transactions list filter,
Needs Review's confirm-merchant form) so a top-level category selection
always restricts which sub-categories are then selectable. Takes
category_group_id as a query parameter (not a path parameter) under a
fixed name, so one endpoint can serve callers whose own <select> needs a
different `name` for its real form submission -- see Task 11 Step 6."""

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.db import get_session
from app.services.category_service import get_valid_sub_categories

router = APIRouter(prefix="/category-groups", tags=["categories"])

_PLACEHOLDER_OPTION = '<option value="">(no change)</option>'


@router.get("/sub-categories", response_class=HTMLResponse)
async def sub_categories_options(category_group_id: int = 0, session: Session = Depends(get_session)):
    if category_group_id == 0:
        return HTMLResponse(content=_PLACEHOLDER_OPTION)
    subs = get_valid_sub_categories(session, category_group_id)
    options = _PLACEHOLDER_OPTION + "".join(f'<option value="{s.id}">{s.name}</option>' for s in subs)
    return HTMLResponse(content=options)
```

- [ ] **Step 4: Register the router in `app/main.py`**

Find where the other routers are included (`app.include_router(...)` calls) and add:

```python
from app.routers import categories
...
app.include_router(categories.router)
```

matching the exact existing import/include style already used for the other routers in that file
(read `app/main.py` first to match the precise pattern).

- [ ] **Step 5: Update `bulk_edit` in `app/routers/transactions.py`**

```python
    new_category_group_id = form.get("new_category_group_id") or None
    new_sub_category_id = form.get("new_sub_category_id") or None
```

alongside the existing `new_category`/`new_nature`/`new_account_id` reads, and inside the update
loop:

```python
            if new_category_group_id:
                t.category_group_id = int(new_category_group_id)
            if new_sub_category_id:
                t.sub_category_id = int(new_sub_category_id)
```

- [ ] **Step 6: Wire the cascading dropdown into `list.html`'s bulk-edit fieldset**

Replace the existing `new_category`/flat dropdown pair in the `<fieldset>` with a category-group
select plus an htmx-populated sub-category select. The select's own `name` stays
`new_category_group_id` (that's what `bulk_edit` reads from the real form POST), but the GET
request that fetches sub-category options sends it under the endpoint's fixed `category_group_id`
query-parameter name via `hx-vals` — this is what lets the one shared endpoint serve every caller
regardless of that caller's own field-naming needs:

```html
    <select name="new_category_group_id"
            hx-get="/category-groups/sub-categories"
            hx-vals="js:{category_group_id: event.target.value}"
            hx-target="#new-sub-category-select" hx-swap="innerHTML"
            hx-trigger="change">
      <option value="">(no change)</option>
      {% for group in category_tree %}<option value="{{ group.id }}">{{ group.name }}</option>{% endfor %}
    </select>
    <select name="new_sub_category_id" id="new-sub-category-select">
      <option value="">(no change)</option>
    </select>
```

- [ ] **Step 7: Pass `category_tree` into the `list_transactions` route's template context**

In `app/routers/transactions.py`'s `list_transactions`, add `"category_tree": get_category_tree(session)`
to the template context dict passed to `transactions/list.html`.

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_categories_router.py -v` and `pytest tests/test_transactions_router.py -v`
Expected: PASS, full files

- [ ] **Step 9: Commit**

```bash
git add app/routers/categories.py app/main.py app/routers/transactions.py app/templates/transactions/list.html tests/test_categories_router.py tests/test_transactions_router.py
git commit -m "feat: cascading category->sub-category dropdown, wired into Transactions bulk-edit"
```

---

### Task 12: Cascading dropdown in the Transactions list filter

**Files:**
- Modify: `app/routers/transactions.py`, `app/templates/transactions/list.html`
- Test: `tests/test_transactions_router.py`

**Interfaces:**
- Consumes: `/category-groups/sub-categories` (Task 11), `get_category_tree` (Task 4)
- Produces: `_apply_transaction_filters`/`_filtered_transactions`/`_count_filtered_transactions`/
  `list_transactions` gain `category_group_id`/`sub_category_id` filter params (alongside, not
  replacing, the existing `category` param — Task 8 already changed what values flow into
  `category`, e.g. drill-down URLs from Overview now pass `category=Housing`; this task's new
  params are the more precise cascading pair, additive to that).

- [ ] **Step 1: Write the failing test**

```python
def test_list_transactions_filters_by_category_group_and_sub_category(client, session):
    from app.services.category_seed_data import seed_category_hierarchy
    from app.services.category_service import get_category_tree

    seed_category_hierarchy(session)
    tree = get_category_tree(session)
    utilities = next(n for n in tree if n.name == "Utilities")
    electricity = next(s for s in utilities.sub_categories if s.name == "Electricity")
    gas = next(s for s in utilities.sub_categories if s.name == "Gas")

    t1 = _make_transaction(session, "EDP", Category.ELECTRICITY, 45.0)
    t2 = _make_transaction(session, "GALP", Category.GAS, 30.0)
    t1.category_group_id = utilities.id
    t1.sub_category_id = electricity.id
    t2.category_group_id = utilities.id
    t2.sub_category_id = gas.id
    session.add(t1)
    session.add(t2)
    session.commit()

    response = client.get("/transactions", params={"category_group_id": utilities.id, "sub_category_id": electricity.id})

    assert "EDP" in response.text
    assert "GALP" not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transactions_router.py -k category_group_and_sub_category -v`
Expected: FAIL — `category_group_id`/`sub_category_id` aren't recognized filter params yet

- [ ] **Step 3: Add the two new params through the filter chain**

In `_apply_transaction_filters`, `_filtered_transactions`, `_count_filtered_transactions`, and
`list_transactions` (same pattern as every other filter param already threaded through these four
functions this session — `commitment_id`, `debt_id`, `transaction_id`, `merchant_id` — add
`category_group_id: Optional[int] = None` and `sub_category_id: Optional[int] = None` to each
signature, and in `_apply_transaction_filters`'s body:

```python
    if category_group_id:
        statement = statement.where(Transaction.category_group_id == category_group_id)
    if sub_category_id:
        statement = statement.where(Transaction.sub_category_id == sub_category_id)
```

Thread both through every call site exactly like `merchant_id` already is (see the current file
for the precise pattern — every function that accepts `merchant_id` should gain these two
parameters alongside it, and every call to `_apply_transaction_filters`/`_filtered_transactions`/
`_count_filtered_transactions` should pass them through).

- [ ] **Step 4: Add the cascading dropdown to the filter form in `list.html`**

Replace the existing flat `<select name="category">` in the top filter `<form>`:

```html
    <select name="category_group_id"
            hx-get="/category-groups/sub-categories"
            hx-vals="js:{category_group_id: event.target.value}"
            hx-target="#filter-sub-category-select" hx-swap="innerHTML"
            hx-trigger="change">
      <option value="">All categories</option>
      {% for group in category_tree %}
      <option value="{{ group.id }}" {% if filters.category_group_id == group.id %}selected{% endif %}>{{ group.name }}</option>
      {% endfor %}
    </select>
    <select name="sub_category_id" id="filter-sub-category-select">
      <option value="">All sub-categories</option>
    </select>
```

`category_tree` is already in `list_transactions`'s template context from Task 11 Step 7 — no
change needed there. Note this filter form's placeholder option text ("All categories") differs
from the shared endpoint's own placeholder ("(no change)", used for the bulk-edit/confirm-merchant
forms) — the endpoint's returned `<option value="">(no change)</option>` is fine to leave as-is
inside the sub-category select here too; the minor label mismatch between "All sub-categories"
(this select's own initial markup) and "(no change)" (what htmx swaps in after a category is
picked) is cosmetic and not worth a special-cased endpoint response for.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_transactions_router.py -v`
Expected: PASS, full file

- [ ] **Step 6: Commit**

```bash
git add app/routers/transactions.py app/templates/transactions/list.html tests/test_transactions_router.py
git commit -m "feat: cascading category->sub-category filter in the Transactions list"
```

---

### Task 13: Cascading dropdown in Needs Review's confirm-merchant form

**Files:**
- Modify: `app/routers/transactions.py` (`confirm_merchant`, `_needs_review_context`),
  `app/templates/transactions/_needs_review_rows.html` (the "New merchants" table)
- Test: `tests/test_transactions_router.py`

**Interfaces:**
- Consumes: `/category-groups/sub-categories` (Task 11), `get_category_tree` (Task 4)
- Produces: `confirm_merchant` accepts `category_group_id`/`sub_category_id` form fields and sets
  `Merchant.default_category_group_id`/`default_sub_category_id`.

- [ ] **Step 1: Write the failing test**

```python
def test_confirm_merchant_applies_category_group_and_sub_category(client, session):
    from app.services.category_seed_data import seed_category_hierarchy
    from app.services.category_service import get_category_tree

    seed_category_hierarchy(session)
    tree = get_category_tree(session)
    utilities = next(n for n in tree if n.name == "Utilities")
    electricity = next(s for s in utilities.sub_categories if s.name == "Electricity")

    merchant = _make_unconfirmed_merchant(session, name="Confirm With Sub", key="confirm-with-sub-router-test")

    response = client.post(
        f"/transactions/merchants/{merchant.id}/confirm",
        data={"category_group_id": str(utilities.id), "sub_category_id": str(electricity.id)},
    )

    assert response.status_code == 200
    session.refresh(merchant)
    assert merchant.confirmed is True
    assert merchant.default_category_group_id == utilities.id
    assert merchant.default_sub_category_id == electricity.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transactions_router.py -k confirm_merchant_applies_category_group -v`
Expected: FAIL — `confirm_merchant` doesn't read/apply these form fields yet

- [ ] **Step 3: Update `confirm_merchant`**

```python
    new_category_group_id = form.get("category_group_id") or None
    new_sub_category_id = form.get("sub_category_id") or None
    if new_category_group_id:
        merchant.default_category_group_id = int(new_category_group_id)
    if new_sub_category_id:
        merchant.default_sub_category_id = int(new_sub_category_id)
```

alongside the existing `new_category`/`new_nature` handling in that function.

- [ ] **Step 4: Update `_needs_review_context`**

Already gained `category_tree` in Task 10 — no change needed here if Task 10 landed first; if
executed out of order, add `"category_tree": get_category_tree(session)` now.

- [ ] **Step 5: Update the "New merchants" table in `_needs_review_rows.html`**

Add a category-group select (cascading to a sub-category select, same htmx pattern as Tasks 11-12)
alongside the existing `category`/`nature` selects in each row, scoped via `hx-include="closest tr"`
on the existing "Save & Confirm" button (already wired that way — no change needed to the button
itself, only to what's inside its `<tr>`):

```html
        <td>
          <select name="category_group_id"
                  hx-get="/category-groups/sub-categories"
                  hx-vals="js:{category_group_id: event.target.value}"
                  hx-target="#sub-category-select-{{ m.id }}"
                  hx-swap="innerHTML"
                  hx-trigger="change">
            <option value="">(no change)</option>
            {% for group in category_tree %}
            <option value="{{ group.id }}" {% if group.id == m.default_category_group_id %}selected{% endif %}>{{ group.name }}</option>
            {% endfor %}
          </select>
        </td>
        <td>
          <select name="sub_category_id" id="sub-category-select-{{ m.id }}">
            <option value="">(no change)</option>
            {% if m.default_category_group_id %}
            {% set group = (category_tree | selectattr("id", "equalto", m.default_category_group_id) | first) %}
            {% for sub in (group.sub_categories if group else []) %}
            <option value="{{ sub.id }}" {% if sub.id == m.default_sub_category_id %}selected{% endif %}>{{ sub.name }}</option>
            {% endfor %}
            {% endif %}
          </select>
        </td>
```

placed as two new `<td>` cells alongside the table's existing Category/Nature cells (update the
`<thead>` row to add two matching `<th>Category Group</th><th>Sub-category</th>` headers). The
sub-category select's `id` is scoped per-row via the merchant's own id (`sub-category-select-{{ m.id
}}`), so each row's category-group select targets only its own row's sub-category select, not
every row's at once — the existing "Save & Confirm" button's `hx-include="closest tr"` still
correctly picks up both selects' current values when the row is actually submitted, unaffected by
this change.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_transactions_router.py -v`
Expected: PASS, full file

- [ ] **Step 7: Commit**

```bash
git add app/routers/transactions.py app/templates/transactions/_needs_review_rows.html tests/test_transactions_router.py
git commit -m "feat: cascading category->sub-category selection in Needs Review's confirm-merchant form"
```

---

### Task 14: Final verification pass

**Files:** none created — verification only.

**Interfaces:** none new.

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: every test passes, including all pre-existing tests from before this plan started.

- [ ] **Step 2: Grep for any remaining direct use of the old `Category` enum in application code
  (not tests, not the deliberately-untouched legacy columns) that this plan should have updated**

```bash
grep -rn "Category\." app/services/ app/routers/ | grep -v "CategoryGroup\|category_service\|category_seed_data"
```

Review the output by hand. Anything found that reads `Transaction.category`/`Merchant.default_category`/
`Commitment.category` (the OLD enum columns) for a real decision (not just passing them through
unused, or a place this plan explicitly decided to leave alone, like the Field defaults) is a gap —
open a note in the plan's ledger and fix it before considering this task done. Anywhere the OLD
enum is only used to satisfy a required constructor argument (its default `Category.OTHER` is
still a valid, harmless value) is fine as-is, per Global Constraints.

- [ ] **Step 3: Verify the migration chain end-to-end against a scratch copy of production**

```bash
rm -f /tmp/category_hierarchy_full_scratch.db
DATABASE_PATH=/tmp/category_hierarchy_full_scratch.db alembic upgrade head
sqlite3 /tmp/category_hierarchy_full_scratch.db "select count(*) from category_groups;"
sqlite3 /tmp/category_hierarchy_full_scratch.db "select count(*) from sub_categories;"
```

Expected: 12 and 46 respectively, matching Task 2's Step 6 verification, confirming nothing in
Tasks 3-13 broke the migration chain.

- [ ] **Step 4: Confirm no new frontend dependency was introduced**

```bash
git diff master... -- app/static/ | head -20
```

Expected: empty diff (or only htmx.min.js untouched) — Global Constraints required no new JS
dependency; this is the final check that held.

- [ ] **Step 5: Commit** (only if Step 2 found something to fix; otherwise this task is
  verification-only and produces no commit)
