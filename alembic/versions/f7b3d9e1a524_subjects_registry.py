"""One registry of subjects (work-list B4): who or what a paper or a line is about, once

New tables `subjects` and `subject_matches` (the Hub's proposals that two are the same, and a person's answer), and
a link from each store that names a person to its subject: Loans' people, Health's people, bank connection owners,
accounts (by owner) and payees that are people. Nothing is filled here: scripts/build_subject_registry.py registers
what exists and proposes the matches; nothing is joined without a person's yes.

Revision ID: f7b3d9e1a524
Revises: e5a9c3d1f702
"""
import sqlalchemy as sa
from alembic import op

revision = "f7b3d9e1a524"
down_revision = "e5a9c3d1f702"
branch_labels = None
depends_on = None

_LINKS = (("people", "subject_id"), ("health_people", "subject_id"), ("bank_connections", "owner_subject_id"),
          ("accounts", "owner_subject_id"), ("merchants", "subject_id"))


def upgrade() -> None:
    op.create_table(
        "subjects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.Enum("PERSON", "ACCOUNT", "COUNTERPARTY", "THING", "POLICY", "CONDITION",
                                  name="subjectkind"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("match_key", sa.String(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("merged_into_id", sa.Integer(), sa.ForeignKey("subjects.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_subjects_match_key", "subjects", ["match_key"])
    op.create_table(
        "subject_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("subjects.id"), nullable=False),
        sa.Column("other_subject_id", sa.Integer(), sa.ForeignKey("subjects.id"), nullable=True),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("merchants.id"), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("subject_id", "other_subject_id", "merchant_id", name="uq_subject_matches_pair"),
    )
    op.create_index("ix_subject_matches_subject_id", "subject_matches", ["subject_id"])
    for table, column in _LINKS:
        with op.batch_alter_table(table) as b:
            b.add_column(sa.Column(column, sa.Integer(), nullable=True))
            b.create_foreign_key(f"fk_{table}_{column}", "subjects", [column], ["id"])


def downgrade() -> None:
    for table, column in _LINKS:
        with op.batch_alter_table(table) as b:
            b.drop_constraint(f"fk_{table}_{column}", type_="foreignkey")
            b.drop_column(column)
    op.drop_table("subject_matches")
    op.drop_table("subjects")
