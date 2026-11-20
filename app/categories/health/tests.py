"""Known lab tests (PLAN step 5b): the reader returns the paper's own wording, value, unit and range; this code
matches the wording to a known test, so the same blood test from two labs lands on one line of a person's results.

Matching, in order: a wording seen before in the same unit (an alias); then a near match (the same unit, a name
alike enough: "Glicose" and "Glucose"), which becomes a new alias; else a new test. A new test that looks like one
already listed waits on "Waiting for you" (a test of its own, or the same as that one); one with nothing alike is
simply added. Nothing is guessed across units: Neutrófilos in % and in
10^9/L are two tests. Reuses the idea of giovi321/asclepius `normalization/resolver.py` (MIT): exact alias, then a
fuzzy match within a bar, else unresolved."""

from __future__ import annotations

import difflib
import re
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.categories.health.text import PATH_SEP, match_key, split_method
from app.models.health import HealthEntry, HealthMeasurement, HealthPerson, HealthTest, HealthTestAlias
from app.problems import Problem

NEAR_AT_LEAST = 0.9
# A result printed under its test's heading with a generic name ("Hemoglobina glicada A1c › Resultado (IFCC)"):
# the heading names the test.
_GENERIC = frozenset({"resultado", "resultados", "doseamento", "valor", "result", "value", "pesquisa", "contagem",
                      "concentracao", "determinacao", "taxa"})
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


class TestError(Exception):
    __test__ = False                                   # not a pytest class

    def __init__(self, problem: Problem, status: int = 400):
        super().__init__(problem.text)
        self.problem = problem
        self.status = status


def gone(what: str) -> TestError:
    return TestError(Problem(what, "That test no longer exists.", "Reload the page to see what is still there."), 404)


def unit_key(unit: Optional[str]) -> str:
    """'µmol/L' == 'umol/l'; 'x10^9/L' == '10⁹/L' == '10^9/l'; '10³/µL' == '10^3/ul'."""
    key = match_key((unit or "").translate(_SUPERSCRIPT)).replace("μ", "u").replace("µ", "u").replace(" ", "")
    key = re.sub(r"^x(?=10)", "", key)
    key = re.sub(r"10\*\*?(\d+)", r"10^\1", key)
    key = re.sub(r"^10(\d)(?=/)", r"10^\1", key)       # '103/ul' (a lost superscript) -> '10^3/ul'
    return key


def wording(test: str, group_path: str = "") -> str:
    """What names the test on the paper: its own name, or its heading's when the name is generic."""
    name, _ = split_method(test)
    first = match_key(name).split(" ")[0] if name else ""
    leaf = (group_path or "").rsplit(PATH_SEP, 1)[-1].strip()
    if leaf and first in _GENERIC:
        return f"{leaf} {name}"
    return name


def _follow(session: Session, test: HealthTest) -> HealthTest:
    seen = set()
    while test.merged_into_id is not None and test.id not in seen:
        seen.add(test.id)
        nxt = session.get(HealthTest, test.merged_into_id)
        if nxt is None:
            break
        test = nxt
    return test


def _add_alias(session: Session, test: HealthTest, text: str, key: str, ukey: str, source: str) -> None:
    session.add(HealthTestAlias(test_id=test.id, wording=text, wording_key=key, unit_key=ukey, source=source))


def _near(session: Session, key: str, ukey: str) -> Optional[HealthTest]:
    best, best_ratio = None, NEAR_AT_LEAST
    for alias in session.exec(select(HealthTestAlias).where(HealthTestAlias.unit_key == ukey)).all():
        ratio = difflib.SequenceMatcher(None, key, alias.wording_key).ratio()
        if ratio >= best_ratio and (best is None or ratio > best_ratio):
            best, best_ratio = alias, ratio
    return _follow(session, session.get(HealthTest, best.test_id)) if best is not None else None


def _similar(session: Session, key: str, ukey: str) -> Optional[int]:
    """A suggestion for a person ("looks like X"): the closest name in the same unit, looser than a near match."""
    best, best_ratio = None, 0.75
    words = set(key.split())
    for alias in session.exec(select(HealthTestAlias).where(HealthTestAlias.unit_key == ukey)).all():
        ratio = difflib.SequenceMatcher(None, key, alias.wording_key).ratio()
        other = set(alias.wording_key.split())
        if words and other and (words <= other or other <= words):     # "Ferritina" within "Ferritina sérica"
            ratio = max(ratio, 0.8)
        if ratio > best_ratio:
            best, best_ratio = alias, ratio
    return _follow(session, session.get(HealthTest, best.test_id)).id if best is not None else None


def match_test(session: Session, test: str, unit: Optional[str], group_path: str = "") -> Optional[HealthTest]:
    """The known test this printed result is (a new one, waiting for a person, when nothing matches). Does not
    commit; flushes so the new rows have ids."""
    text = wording(test, group_path)
    key, ukey = match_key(text), unit_key(unit)
    if not key:
        return None
    alias = session.exec(select(HealthTestAlias).where(HealthTestAlias.wording_key == key,
                                                       HealthTestAlias.unit_key == ukey)).first()
    if alias is not None:
        return _follow(session, session.get(HealthTest, alias.test_id))
    near = _near(session, key, ukey)
    if near is not None:
        _add_alias(session, near, text, key, ukey, "system")
        session.flush()
        return near
    like = _similar(session, key, ukey)
    # Asked only when it looks like a test already listed (Claude, 2026-11-19; Pedro may change): a test with
    # nothing to be confused with is simply added, so the first lab sheets do not fill "Waiting for you".
    new = HealthTest(name=text, unit=(unit or "").strip() or None, unit_key=ukey, approved=like is None,
                     similar_to_id=like)
    session.add(new)
    session.flush()
    _add_alias(session, new, text, key, ukey, "system")
    session.flush()
    return new


def attach_tests(session: Session, entry: HealthEntry) -> int:
    """Give each result of a lab sheet (anything not a written report) its known test. Returns how many were
    matched. Does not touch a result a person already moved to another test (its test stays)."""
    if entry.layout == "report":
        return 0
    done = 0
    for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id,
                                                          HealthMeasurement.test_id.is_(None))).all():
        if m.value is None and not m.value_text:
            continue
        test = match_test(session, m.test, m.unit, m.group_path)
        if test is not None:
            m.test_id = test.id
            session.add(m)
            done += 1
    session.commit()
    return done


def approve_test(session: Session, test_id: int) -> None:
    test = session.get(HealthTest, test_id)
    if test is None:
        raise gone("Not approved.")
    test.approved = True
    session.add(test)
    session.commit()


def merge_tests(session: Session, source_id: int, target_id: int) -> None:
    """A person said these are the same test: every result and wording of the first moves to the second."""
    source, target = session.get(HealthTest, source_id), session.get(HealthTest, target_id)
    if source is None or target is None:
        raise gone("Not merged.")
    target = _follow(session, target)
    if source.id == target.id:
        raise TestError(Problem("Not merged.", f"{source.name} cannot be merged into itself.",
                                "Pick a different test to keep, then try again."))
    if source.merged_into_id is not None:
        raise TestError(Problem("Not merged.", f"{source.name} was already merged into another test.",
                                "Reload the page to see what is still listed, then try again."))
    if source.unit_key != target.unit_key:
        raise TestError(Problem("Not merged.", f"{source.name} is in {source.unit or 'no unit'} and {target.name} in "
                                f"{target.unit or 'no unit'}, so their values cannot share one line.",
                                "Approve it as a test of its own instead."))
    for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.test_id == source.id)).all():
        m.test_id = target.id
        session.add(m)
    for a in session.exec(select(HealthTestAlias).where(HealthTestAlias.test_id == source.id)).all():
        a.test_id, a.source = target.id, "person"
        session.add(a)
    source.merged_into_id, source.approved = target.id, True
    session.add(source)
    session.commit()


def results_of(session: Session, person_id: int) -> list[dict]:
    """A person's lab results, one line per known test (whatever lab or wording), each with its points over time
    (an earlier result restated on a later paper is one point), newest first within the line's table."""
    if session.get(HealthPerson, person_id) is None:
        return []
    rows = session.exec(select(HealthMeasurement).where(HealthMeasurement.person_id == person_id,
                                                        HealthMeasurement.test_id.is_not(None))).all()
    entries = {e.id: e for e in session.exec(select(HealthEntry).where(
        HealthEntry.id.in_({m.entry_id for m in rows}))).all()} if rows else {}
    lines: dict[int, dict] = {}
    for m in rows:
        test = _follow(session, session.get(HealthTest, m.test_id))
        entry = entries.get(m.entry_id)
        when = m.measured_on or (None if m.is_previous or entry is None else entry.entry_date)
        line = lines.setdefault(test.id, {"test": test, "points": {}, "papers": set()})
        if when is None:
            continue
        shown = m.value if m.value is not None else m.value_text
        point = line["points"].setdefault(when, {"date": when, "value": shown, "numeric": m.value,
                                                 "out_of_range": m.out_of_range, "reference": m.reference_text,
                                                 "ref": (m.ref_low, m.ref_high),
                                                 "document_id": entry.document_id if entry else None})
        if not m.is_previous:                          # the paper's own result wins over a restated one
            point.update(value=shown, numeric=m.value, out_of_range=m.out_of_range, reference=m.reference_text,
                         ref=(m.ref_low, m.ref_high), document_id=entry.document_id if entry else None)
            line["papers"].add(m.entry_id)
    out = []
    for line in lines.values():
        points = sorted(line["points"].values(), key=lambda p: p["date"], reverse=True)
        if points:
            out.append({"test": line["test"], "points": points, "latest": points[0], "papers": len(line["papers"])})
    out.sort(key=lambda l: match_key(l["test"].name))
    return out


__all__ = ["match_test", "attach_tests", "approve_test", "merge_tests", "results_of", "unit_key", "wording",
           "TestError", "NEAR_AT_LEAST"]
