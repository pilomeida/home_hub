"""loans & savings: position tables, Debt fields, Savings category group

Revision ID: c3a9d4e7f215
Revises: b7d2f5a81c34
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlmodel import Session

revision: str = "c3a9d4e7f215"
down_revision: Union[str, Sequence[str], None] = "b7d2f5a81c34"
branch_labels = None
depends_on = None

STR = sqlmodel.sql.sqltypes.AutoString


def _fk(col, target):
    return sa.ForeignKeyConstraint([col], [target])


def upgrade() -> None:
    with op.batch_alter_table("debts") as b:
        b.add_column(sa.Column("name", STR(), nullable=True))
        b.add_column(sa.Column("external_number", STR(), nullable=True))
        b.add_column(sa.Column("capital_granted", sa.Float(), nullable=True))
        b.add_column(sa.Column("term_months", sa.Integer(), nullable=True))
        b.add_column(sa.Column("start_date", sa.Date(), nullable=True))
        b.add_column(sa.Column("status", STR(), nullable=False, server_default="active"))
        b.add_column(sa.Column("loan_type", STR(), nullable=True))
        b.add_column(sa.Column("spread_percent", sa.Float(), nullable=True))
        b.create_index("ix_debts_external_number", ["external_number"])

    op.create_table(
        "loan_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("debt_id", sa.Integer(), nullable=False),
        sa.Column("instalment_number", sa.Integer(), nullable=False),
        sa.Column("movement_date", sa.Date(), nullable=False),
        sa.Column("capital", sa.Float(), nullable=False),
        sa.Column("interest", sa.Float(), nullable=False),
        sa.Column("insurance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("insurance_life", sa.Float(), nullable=False, server_default="0"),
        sa.Column("insurance_building", sa.Float(), nullable=False, server_default="0"),
        sa.Column("balance_after", sa.Float(), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=True),
        _fk("debt_id", "debts.id"), _fk("document_id", "documents.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("debt_id", "instalment_number", name="uq_loan_movements_debt_id_instalment_number"),
    )
    op.create_table(
        "loan_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("debt_id", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("capital_remaining", sa.Float(), nullable=False),
        sa.Column("rate_percent", sa.Float(), nullable=True),
        sa.Column("next_rate_percent", sa.Float(), nullable=True),
        sa.Column("next_due_date", sa.Date(), nullable=True),
        sa.Column("next_instalment", sa.Float(), nullable=True),
        sa.Column("next_capital", sa.Float(), nullable=True),
        sa.Column("next_interest", sa.Float(), nullable=True),
        sa.Column("indexante_percent", sa.Float(), nullable=True),
        sa.Column("spread_percent", sa.Float(), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=True),
        _fk("debt_id", "debts.id"), _fk("document_id", "documents.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("debt_id", "as_of", name="uq_loan_snapshots_debt_id_as_of"),
    )
    op.create_table(
        "loan_alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("debt_id", sa.Integer(), nullable=False),
        sa.Column("kind", STR(), nullable=False),
        sa.Column("ref", STR(), nullable=False),
        sa.Column("message", STR(), nullable=False),
        sa.Column("detected_on", sa.Date(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ack_note", STR(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        _fk("debt_id", "debts.id"), _fk("document_id", "documents.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("debt_id", "kind", "ref", name="uq_loan_alerts_debt_id_kind_ref"),
    )
    op.create_table(
        "savings_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("holder", STR(), nullable=True),
        sa.Column("product", STR(), nullable=False),
        sa.Column("label", STR(), nullable=False),
        sa.Column("account_ref", STR(), nullable=False),
        sa.Column("units", sa.Float(), nullable=True),
        sa.Column("invested", sa.Float(), nullable=True),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("periodic_amount", sa.Float(), nullable=True),
        sa.Column("next_periodic_date", sa.Date(), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=True),
        _fk("document_id", "documents.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_ref", "label", "as_of", name="uq_savings_snapshots_account_ref_label_as_of"),
    )
    op.create_table(
        "balance_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("kind", STR(), nullable=False),
        sa.Column("label", STR(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        _fk("document_id", "documents.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "label", "as_of", name="uq_balance_snapshots_kind_label_as_of"),
    )
    op.create_table(
        "position_extractions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("status", STR(), nullable=False),
        sa.Column("error", STR(), nullable=True),
        sa.Column("payload_json", STR(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(), nullable=False),
        _fk("document_id", "documents.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", name="uq_position_extractions_document_id"),
    )
    op.create_table(
        "reminder_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", STR(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("cycle_start", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "debt_match_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("debt_id", sa.Integer(), nullable=False),
        sa.Column("normalized_key", STR(), nullable=False),
        _fk("debt_id", "debts.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "loan_insurance_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("debt_id", sa.Integer(), nullable=False),
        sa.Column("component", STR(), nullable=False),
        sa.Column("normalized_key", STR(), nullable=False),
        _fk("debt_id", "debts.id"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "debt_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("debt_id", sa.Integer(), nullable=False),
        sa.Column("kind", STR(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.Column("note", STR(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _fk("debt_id", "debts.id"), _fk("transaction_id", "transactions.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_id", name="uq_debt_entries_transaction_id"),
    )

    for table, col in [("loan_movements", "debt_id"), ("loan_movements", "document_id"),
                       ("loan_snapshots", "debt_id"), ("loan_snapshots", "document_id"),
                       ("savings_snapshots", "document_id"), ("balance_snapshots", "document_id"),
                       ("loan_alerts", "debt_id"), ("loan_alerts", "document_id"),
                       ("reminder_logs", "key"), ("loan_insurance_rules", "debt_id"), ("debt_entries", "debt_id")]:
        with op.batch_alter_table(table) as b:
            b.create_index(f"ix_{table}_{col}", [col])
    with op.batch_alter_table("debt_match_rules") as b:
        b.create_index("ix_debt_match_rules_normalized_key", ["normalized_key"], unique=True)
    with op.batch_alter_table("loan_insurance_rules") as b:
        b.create_index("ix_loan_insurance_rules_normalized_key", ["normalized_key"], unique=True)

    from app.services.taxonomy import ensure_taxonomy
    session = Session(bind=op.get_bind())
    ensure_taxonomy(session)

    # Informal debts used to be original_amount +/- their linked transactions; they
    # become an explicit ledger (idempotent, never re-files anything).
    from app.services.debt_ledger import rebuild_entries_from_links
    rebuild_entries_from_links(session)
    session.flush()


def downgrade() -> None:
    # Seeded Savings category nodes are left in place (additive data; may be referenced).
    with op.batch_alter_table("debt_match_rules") as b:
        b.drop_index("ix_debt_match_rules_normalized_key")
    with op.batch_alter_table("loan_insurance_rules") as b:
        b.drop_index("ix_loan_insurance_rules_normalized_key")
        b.drop_index("ix_loan_insurance_rules_debt_id")
    # original_amount was the first advance before the ledger existed (it is the
    # sum of the advances now): restore that meaning so an up/down/up round trip is stable.
    op.execute(
        "UPDATE debts SET original_amount = COALESCE((SELECT e.amount FROM debt_entries e "
        "WHERE e.debt_id = debts.id AND e.kind = 'advance' ORDER BY e.id LIMIT 1), original_amount) "
        "WHERE external_number IS NULL"
    )
    with op.batch_alter_table("debt_entries") as b:
        b.drop_index("ix_debt_entries_debt_id")
    for table, col in [("loan_alerts", "document_id"), ("loan_alerts", "debt_id"), ("reminder_logs", "key"), ("balance_snapshots", "document_id"),
                       ("savings_snapshots", "document_id"), ("loan_snapshots", "document_id"),
                       ("loan_snapshots", "debt_id"), ("loan_movements", "document_id"),
                       ("loan_movements", "debt_id")]:
        with op.batch_alter_table(table) as b:
            b.drop_index(f"ix_{table}_{col}")
    for t in ["debt_entries", "loan_insurance_rules", "debt_match_rules", "reminder_logs", "loan_alerts", "position_extractions", "balance_snapshots",
              "savings_snapshots", "loan_snapshots", "loan_movements"]:
        op.drop_table(t)
    with op.batch_alter_table("debts") as b:
        b.drop_index("ix_debts_external_number")
        for c in ["spread_percent", "loan_type", "status", "start_date", "term_months", "capital_granted", "external_number", "name"]:
            b.drop_column(c)
