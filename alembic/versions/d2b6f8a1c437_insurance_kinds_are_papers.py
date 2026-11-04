"""Insurance kinds are papers (work-list B1b, Pedro 2026-11-04): policy or claim

The old kinds said what a policy covers (home_insurance, car_insurance, health_insurance, life_insurance) and the
field `document_kind` said what the paper is. Now the kind is the paper, `covers` says what the policy is for and
`part` which part of the policy a paper is. On 2026-11-04: 17 policy papers change here. The 6 premium notices are
bills (Pedro): they are moved to Bills afterwards, through the Hub's own re-file path, which takes them off the
policy's wiki page and reads them as bills (scripts/move_premium_notices_to_bills.py). Until then they keep their
old kind. A hand-entered record of a premium (none today) becomes a policy paper, part "other": a record has no file
to read as a bill.

Revision ID: d2b6f8a1c437
Revises: c9e2f4a6b815
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "d2b6f8a1c437"
down_revision = "c9e2f4a6b815"
branch_labels = None
depends_on = None

COVERS = {"home_insurance": "home", "car_insurance": "car", "health_insurance": "health", "life_insurance": "life"}
PARTS = {"policy_schedule", "policy_summary", "general_conditions", "payment_plan", "green_card", "other"}


def _moved(fields: dict, old_kind: str) -> tuple[str, dict]:
    paper = fields.pop("document_kind", None) or "other"
    fields["covers"] = COVERS[old_kind]
    if paper == "claim":
        return "claim", fields
    fields["part"] = paper if paper in PARTS else "other"
    return "policy", fields


def upgrade() -> None:
    bind = op.get_bind()
    olds = tuple(COVERS)
    for table, keep_premiums in (("documents", True), ("records", False)):
        rows = bind.execute(sa.text(
            f"SELECT id, category, fields_json FROM {table} WHERE domain = 'INSURANCE' AND category IN :olds"
        ).bindparams(sa.bindparam("olds", expanding=True)), {"olds": list(olds)}).all()
        for row_id, old_kind, raw in rows:
            fields = json.loads(raw or "{}")
            if keep_premiums and fields.get("document_kind") == "premium_notice":
                continue            # moved to Bills by the script, through the re-file path
            kind, fields = _moved(fields, old_kind)
            bind.execute(sa.text(f"UPDATE {table} SET category = :k, fields_json = :f WHERE id = :i"),
                         {"k": kind, "f": json.dumps(fields, ensure_ascii=False, sort_keys=True), "i": row_id})
    # A privacy level set on an old kind (none on 2026-11-04) carries over to Policy: the strictest one, as only one
    # row per kind may exist. A recognition rule simply points at Policy.
    rank = {"house_low": 0, "house_mid": 1, "house_high": 2}
    settings = bind.execute(sa.text(
        "SELECT id, tier FROM document_type_settings WHERE lower(domain) = 'insurance' AND category IN :olds"
    ).bindparams(sa.bindparam("olds", expanding=True)), {"olds": list(olds)}).all()
    if settings:
        keep = max(settings, key=lambda r: (rank.get(r[1] or "", -1), r[0]))[0]
        bind.execute(sa.text("UPDATE document_type_settings SET category = 'policy' WHERE id = :i"), {"i": keep})
        for row_id, _tier in settings:
            if row_id != keep:
                bind.execute(sa.text("DELETE FROM document_type_settings WHERE id = :i"), {"i": row_id})
    bind.execute(sa.text("UPDATE document_fingerprints SET category = 'policy' WHERE lower(domain) = 'insurance' "
                         "AND category IN :olds").bindparams(sa.bindparam("olds", expanding=True)), {"olds": list(olds)})


def downgrade() -> None:
    pass  # the old kinds no longer exist in the code; the backup taken before the migration holds the old state
