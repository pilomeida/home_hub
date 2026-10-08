"""Move the live data onto the current category tree (see taxonomy_slugmap.py)."""

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy
from app.services.taxonomy_seed import SEED
from app.services.taxonomy_slugmap import SLUG_MAP


@dataclass
class RemapReport:
    nodes_created: int = 0
    transactions_moved: int = 0
    to_unsorted: int = 0  # of those, from retired nodes
    merchants_repointed: int = 0
    merchants_defaults_cleared: int = 0
    nodes_deleted: int = 0
    nodes_kept_with_data: list[str] = field(default_factory=list)


def seed_slugs() -> set[str]:
    from app.services.taxonomy import _slug
    slugs = set()
    for _kind, groups in SEED.items():
        for gname, cats in groups:
            g = _slug(gname)
            slugs.add(g)
            for cname, _cad, _legacy, subs in cats:
                c = f"{g}.{_slug(cname)}"
                slugs.add(c)
                for sub in subs:
                    slugs.add(f"{c}.{_slug(sub[0] if isinstance(sub, tuple) else sub)}")
    return slugs


def _count(session: Session, sql: str, **params) -> int:
    return session.execute(text(sql), params).scalar_one()


def remap_to_current_tree(session: Session) -> RemapReport:
    report = RemapReport()
    report.nodes_created = ensure_taxonomy(session)
    by_slug = {n.slug: n for n in session.exec(select(CategoryNode)).all()}
    unsorted_id = by_slug[UNSORTED_SLUG].id

    for old_slug, new_slug in SLUG_MAP.items():
        old = by_slug.get(old_slug)
        if old is None or old_slug == new_slug:
            continue
        target_id = by_slug[new_slug].id if new_slug else unsorted_id
        moved = session.execute(text("UPDATE transactions SET category_id = :t WHERE category_id = :o"),
                                {"t": target_id, "o": old.id}).rowcount
        report.transactions_moved += moved
        if new_slug is None:
            report.to_unsorted += moved
            report.merchants_defaults_cleared += session.execute(
                text("UPDATE merchants SET default_category_id = NULL WHERE default_category_id = :o"),
                {"o": old.id}).rowcount
        else:
            report.merchants_repointed += session.execute(
                text("UPDATE merchants SET default_category_id = :t WHERE default_category_id = :o"),
                {"t": target_id, "o": old.id}).rowcount
        # Budgets: one per (node, year); on a merge the amounts add up.
        for bid, year, amount in session.execute(
                text("SELECT id, year, amount FROM budgets WHERE node_id = :o"), {"o": old.id}).all():
            if new_slug is None:
                session.execute(text("DELETE FROM budgets WHERE id = :b"), {"b": bid})
                continue
            clash = session.execute(text("SELECT id FROM budgets WHERE node_id = :t AND year = :y"),
                                    {"t": target_id, "y": year}).first()
            if clash:
                session.execute(text("UPDATE budgets SET amount = amount + :a WHERE id = :c"),
                                {"a": amount, "c": clash[0]})
                session.execute(text("DELETE FROM budgets WHERE id = :b"), {"b": bid})
            else:
                session.execute(text("UPDATE budgets SET node_id = :t WHERE id = :b"), {"t": target_id, "b": bid})

    # Drop the nodes the current seed no longer has, leaves first, only when nothing points at them.
    keep = seed_slugs()
    stale = [n for n in by_slug.values() if n.slug not in keep]
    for node in sorted(stale, key=lambda n: -n.level):
        refs = (_count(session, "SELECT count(*) FROM transactions WHERE category_id = :i", i=node.id)
                + _count(session, "SELECT count(*) FROM merchants WHERE default_category_id = :i", i=node.id)
                + _count(session, "SELECT count(*) FROM budgets WHERE node_id = :i", i=node.id)
                + _count(session, "SELECT count(*) FROM category_nodes WHERE parent_id = :i", i=node.id))
        if refs:
            report.nodes_kept_with_data.append(node.slug)
            continue
        session.execute(text("DELETE FROM category_nodes WHERE id = :i"), {"i": node.id})
        report.nodes_deleted += 1
    session.commit()
    return report
