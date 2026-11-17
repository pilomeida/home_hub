"""The privacy fail-closed self-test: proves, with the real privacy code, that the tripwires still hold.

It builds a scratch database (never the live one) with synthetic documents filed through the same models, saves
levels with the real service (`ps.save_type`), and asks the production code what the AI may reach:
  - a document with no level yet reads as nothing (no reach, no entry for Ask, no wiki claim);
  - a document of a High type is hidden from the first model call, and a run that opens it is raised to High;
  - when the High route is unavailable, that raised call is refused (TierBlocked) - nothing falls back to a lower level;
  - a call made outside any privacy scope is refused;
  - a document of a level that IS reachable still is (so the test is not passing because everything is closed).
No model is called and nothing is sent anywhere. Run on demand: `python -m app.privacy.selftest` (exit 1 on failure);
the Hub also runs it nightly as the "Privacy self-test" job (app/jobs/registry.py)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from contextlib import contextmanager

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.llm_gateway import call_level
from app.models.category import Category
from app.models.document import Document, DocumentSource, DocumentStatus
from app.privacy import settings as ps
from app.privacy import tiers
from app.privacy.ratchet import ratchet_scope
from app.privacy.scope import reachable_scope
from app.privacy.tiers import Tier, TierBlocked
from app.problems import Problem, record_problem, resolve_for

AREA = "Privacy"
REF = "privacy-selftest"

# Real types (from ps.known_types()): one reachable at Low, one saved High, one with no level, one that does not exist.
OPEN_TYPE = ("house", "house_appliance")
HIGH_TYPE = ("insurance", "policy")
UNSET_TYPE = ("financials", "statement")
UNKNOWN_KIND = ("house", "legacy_kind")
FORCED_HIGH_TYPE = ("health", "lab_result")                  # Health is always High; its level cannot be saved


@contextmanager
def _high_route_unavailable():
    """Switch the High route off the way the code itself does (a tier's policy says route_available=False)."""
    original = tiers.POLICIES[Tier.HIGH]
    tiers.POLICIES[Tier.HIGH] = dataclasses.replace(original, route_available=False)
    try:
        yield
    finally:
        tiers.POLICIES[Tier.HIGH] = original


def _scratch() -> Session:
    from app import models  # noqa: F401  (registers the tables)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _file(session: Session, n: int, kind: tuple[str, str] | None, category_only: bool = False) -> Document:
    category = Category(kind[0]) if kind else None
    doc = Document(filename=f"selftest-{n}.pdf", file_path=f"/nonexistent/selftest-{n}.pdf", content_hash=f"selftest-{n}",
                   source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, category=category,
                   paper_kind=None if category_only or not kind else kind[1])
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def run_selftest() -> list[str]:
    """Returns what is wrong, one plain sentence each; an empty list means every tripwire held."""
    from app.services.ask.tools import settled_entries      # local: the tools import the privacy package

    failures: list[str] = []
    with _scratch() as s:
        for kind, level in ((OPEN_TYPE, "house_low"), (HIGH_TYPE, "house_high")):
            ps.save_type(s, kind[0], kind[1], level, False, "selftest")
        ps.set_enforcing(s, True, "selftest")
        open_doc, high_doc, unset_doc = _file(s, 1, OPEN_TYPE), _file(s, 2, HIGH_TYPE), _file(s, 3, UNSET_TYPE)
        unknown_doc, no_kind_doc, no_type_doc = _file(s, 4, UNKNOWN_KIND), _file(s, 5, HIGH_TYPE, category_only=True), _file(s, 6, None)
        forced_doc = _file(s, 7, FORCED_HIGH_TYPE)

        with reachable_scope(s) as reach:
            if not reach.binding:
                return ["Levels are not binding inside the test, so nothing could be checked."]
            first_call = reach.without_private()
            ids = settled_entries(s, reach=first_call)
            seen = {e.document.id for e in ids if e.document is not None}
            doc_ids = first_call.included_document_ids(s) or set()

            # 1. A document with no level reads as nothing.
            for label, doc in (("with no level yet", unset_doc), ("of a kind the Hub does not know", unknown_doc),
                               ("with no kind", no_kind_doc), ("with no type at all", no_type_doc)):
                if (reach.includes_document(doc) or first_call.includes_document(doc) or doc.id in doc_ids
                        or doc.id in seen or reach.includes_file_of(s, doc)):
                    failures.append(f"A document {label} can be reached by the AI.")

            # 2. A High document is hidden from the first call, and reading it makes the run private.
            for label, doc in (("saved as High", high_doc), ("of Health (always High)", forced_doc)):
                if first_call.includes_document(doc) or doc.id in doc_ids or doc.id in seen:
                    failures.append(f"A document {label} is shown to the AI's first call, which must never see it.")
                if reach.tier_of_source(doc) != Tier.HIGH or not reach.is_private_type(doc.category, doc.paper_kind):
                    failures.append(f"A document {label} is not marked private, so reading it would not raise the run to High.")

            # 3. With the High route unavailable, a run that opened a High document is refused, not downgraded.
            with _high_route_unavailable(), ratchet_scope(Tier.HIGH):
                try:
                    level, _ = call_level(Tier.MID.value, None)          # a caller asking for less is raised to High
                    tiers.policy_for(level)
                    failures.append("With the High route unavailable, a run that opened a High document was still let through.")
                except TierBlocked:
                    pass
            with _high_route_unavailable():
                try:
                    tiers.policy_for(Tier.HIGH)
                    failures.append("The High level was served although its route is switched off.")
                except TierBlocked:
                    pass

            # 4. The control: what IS reachable still is, so the checks above are not passing because everything is shut.
            if open_doc.id not in seen or not first_call.includes_document(open_doc):
                failures.append("A document of a Low type cannot be reached either, so the test proves nothing.")

        # 5. A call made outside every privacy scope is refused.
        try:
            call_level(None, None)
            failures.append("An AI call made outside any privacy scope was not refused.")
        except TierBlocked:
            pass
    return failures


def _run_isolated() -> list[str]:
    """Run the checks in a fresh process. The High-route check switches a global policy off for a moment; inside the
    web app (a manual "Run now") a real High call in that moment would be wrongly refused. A child process cannot
    be seen by any live request, and this also keeps the web server's event loop free."""
    out = subprocess.check_output([sys.executable, "-m", "app.privacy.selftest", "--json"], cwd=Path(__file__).resolve().parents[2],
                                  text=True, timeout=300)
    return json.loads(out)


async def run_privacy_selftest(session: Session, trigger: str, runner=None):
    """The scheduled job. `session` is the live one, used only to log a problem. `runner` is for tests only."""
    from app.jobs.outcome import JobOutcome
    try:
        failures = await asyncio.to_thread(runner or _run_isolated)
    except Exception as exc:                                 # a test that cannot run is a failure, never a pass
        failures = [f"The test itself could not run ({type(exc).__name__}: {exc})."]
    if not failures:
        resolve_for(session, REF)
        return JobOutcome("ok", "Done: documents with no level, and High documents when the High route is unavailable, "
                                "are still kept away from the AI. Nothing was sent anywhere.")
    problem = Problem("The privacy self-test failed.",
                      " ".join(failures) + " The Hub may be letting the AI read something it should not.",
                      "Tell Pedro now, and until it is fixed treat Ask and the wiki check as unsafe for private papers; "
                      "the technical details show which check failed.", "/problems")
    record_problem(session, area=AREA, problem=problem, ref=REF, technical=" | ".join(failures))
    return JobOutcome("failed", f"{problem.what} {problem.why} {problem.next_step}")


def main() -> int:
    failures = run_selftest()
    if "--json" in sys.argv[1:]:                             # used by the job: print the list, exit 0 when the test ran
        print(json.dumps(failures))
        return 0
    if failures:
        print("PRIVACY SELF-TEST FAILED:\n- " + "\n- ".join(failures), file=sys.stderr)
        return 1
    print("privacy self-test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
