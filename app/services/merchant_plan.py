"""Apply a reviewed merchant plan: category instructions first (they follow the ORIGINAL merchants), then the merges
and renames, then one category per merchant for the merged ones.

plan = {"cats": [{"id", "credit", "debit"}], "ops": [{"final", "ids", "explicit", "keep"}]}"""

from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models.merchant import Merchant
from app.services.merchant_categories import apply_merchant_category
from app.services.merchant_merge import find_merchant_by_name, merge_merchants, resolve_merchant
from app.services.merchant_reconcile import reconcile_merchants


@dataclass
class PlanReport:
    category_entries_filed: int = 0
    category_entries_skipped: int = 0
    merge_groups: int = 0
    merchants_merged: int = 0
    entries_moved_by_merge: int = 0
    reconciled_moves: int = 0
    survivors: list[int] = field(default_factory=list)


def apply_plan(session: Session, plan: dict, dry_run: bool = False) -> PlanReport:
    report = PlanReport()
    for c in plan.get("cats", []):
        r = apply_merchant_category(session, c["id"], c.get("credit"), c.get("debit"), dry_run=dry_run)
        report.category_entries_filed += r.filed
        report.category_entries_skipped += r.skipped
    if dry_run:
        report.merge_groups = len(plan.get("ops", []))
        report.merchants_merged = sum(max(0, len(o["ids"]) - 1) for o in plan.get("ops", []))
        return report
    to_reconcile: list[int] = []
    for op in plan.get("ops", []):
        members = [i for i in (resolve_merchant(session, session.get(Merchant, i)) for i in op["ids"]) if i is not None]
        unique = list({m.id: m for m in members}.values())
        existing = find_merchant_by_name(session, op["final"])
        survivor = existing if existing is not None else (unique[0] if unique else None)
        if survivor is None:
            continue
        sources = [m.id for m in unique if m.id != survivor.id]
        r = merge_merchants(session, sources, survivor.id, new_name=op["final"]) if sources or survivor.canonical_name != op["final"] else None
        if r is not None:
            report.merchants_merged += r.merchants_merged
            report.entries_moved_by_merge += r.transactions_moved
        report.merge_groups += 1
        report.survivors.append(survivor.id)
        if not op.get("explicit"):
            to_reconcile.append(survivor.id)
    if to_reconcile:
        rec = reconcile_merchants(session, dry_run=False, only_ids=to_reconcile)
        report.reconciled_moves = rec.transactions_moved
    return report
