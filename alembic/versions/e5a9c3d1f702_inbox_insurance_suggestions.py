"""Inbox suggestions follow the insurance kinds (work-list B1b, found 2026-11-04)

Migration d2b6f8a1c437 turned the old insurance kinds (home_insurance, car_insurance, health_insurance,
life_insurance) into Policy, but left the Inbox's suggestions for papers still waiting: five on 2026-11-04,
all "insurance/home_insurance". The Inbox drops a suggestion whose kind no longer exists, so those cards
lost theirs. A waiting suggestion now says Policy, which is what an approval would file. (What the policy
covers is asked when the paper is approved, as for any new policy.) Reviewed items keep their history.

Revision ID: e5a9c3d1f702
Revises: d2b6f8a1c437
"""
import sqlalchemy as sa
from alembic import op

revision = "e5a9c3d1f702"
down_revision = "d2b6f8a1c437"
branch_labels = None
depends_on = None

OLD_KINDS = ("home_insurance", "car_insurance", "health_insurance", "life_insurance")


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        "UPDATE inbox_items SET suggested_category = 'policy' "
        "WHERE suggested_domain = 'insurance' AND suggested_category IN :olds AND reviewed_at IS NULL"
    ).bindparams(sa.bindparam("olds", expanding=True)), {"olds": list(OLD_KINDS)})


def downgrade() -> None:
    pass  # the backup taken before the migration holds the old suggestions
