"""A household for the privacy-levels tests: documents, records, wiki claims, transactions and to-dos of
types that Ask and the wiki check may and may not read, built with the real factories and the real
settings service (`save_type`). Excluded content carries unique sentinel strings so a test can prove
they never appear in ANY request body that leaves the server.

The bodies are captured by the REAL GatewayClient over an httpx.MockTransport (the same pattern as
tests/test_privacy_outbound.py), so what is asserted is what would actually be sent."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from types import SimpleNamespace

import httpx
from sqlmodel import select

from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.domains.health.store import apply_extraction
from app.llm_gateway import GatewayClient
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.inbox_item import InboxItem
from app.models.todo import Todo
from app.models.transaction import Category, Transaction, TransactionType
from app.privacy import settings as ps
from tests.fakes.fake_gateway import gateway_text_result, gateway_tool_result
from tests.knowledge_factories import make_claim, make_document, make_link, make_page, make_record

# Sentinels: ASCII only, unique, and never part of any clean text.
HIGH = "SENTINEL-HIGH"           # insurance/home_insurance, level High
UNSET = "SENTINEL-UNSET"         # financials/statement, no level
HIDES = "SENTINEL-HIDES"         # house/ownership_document, Mid with "hide personal details" on
REC = "SENTINEL-RECORD"          # house/maintenance_log record, level High
UNKNOWN = "SENTINEL-UNKNOWN"     # a document whose category is not a registered type
PAGE = "SENTINEL-PAGE"           # a wiki page made only from excluded documents
MIXED = "SENTINEL-MIXED"         # a claim with one included and one excluded source
SUMMARY = "SENTINEL-SUMMARY"     # stored summary of a page that has a hidden claim
HISTORY = "SENTINEL-HISTORY"     # superseded claim from an excluded document
TXN = "SENTINEL-TXN"             # a transaction read from an excluded statement
TODO = "SENTINEL-TODO"           # a to-do made from an excluded document
NEIGHBOUR = "SENTINEL-NEIGHBOUR"  # title of a hidden page linked from a visible one
FILE_BYTES = "SENTINEL-FILEBYTES"
HEALTH_PAPER = "SENTINEL-HEALTHPAPER"  # a Health paper: always High, read into Health's tables (summary)

# High (private) types are reachable, but never in the first request of a run: absent from the first
# request body, present only in requests with sensitivity == "house_high" (see app/privacy/ratchet.py).
PRIVATE = [HIGH, REC, HEALTH_PAPER]
EXCLUDED = [UNSET, HIDES, UNKNOWN, PAGE, MIXED, SUMMARY, HISTORY, TXN, TODO, NEIGHBOUR, FILE_BYTES]

# Read from the High (private) insurance document and record, and what build_world derives from them: readable
# by the AI, but only in calls at house_high. (The to-do made from the High record, and the High file's bytes.)
TODO_FROM_REC = f"{TODO}-from-rec"
PRIVATE_DERIVED = [*PRIVATE, PAGE, MIXED, NEIGHBOUR, TODO_FROM_REC]    # file bytes: see PRIVATE_FILES (they travel as base64)
# Still withheld from every call at every level, with STANDARD levels.
STILL_EXCLUDED = [*(s for s in EXCLUDED if s not in PRIVATE_DERIVED and s not in (TODO, FILE_BYTES)), f"{TODO}-from-doc", f"{TODO}-from-txn"]

CLEAN_ROOM = "Kitchen"
CLEAN_PAYEE = "CLEAN-PAYEE-EDP"
CLEAN_TODO = "CLEAN-TODO-boiler"
CLEAN_ANSWER = "CLEAN-SAVED-ANSWER"
PNG = b"\x89PNG\r\n\x1a\nCLEAN-IMAGE-BYTES"


@dataclass
class Wire:
    """Records every request body; answers Ask turns from a script and wiki audits with no findings."""
    script: list = field(default_factory=list)
    bodies: list = field(default_factory=list)
    lint_reply: dict = field(default_factory=lambda: {"findings": []})

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        self.bodies.append(body)
        if json.loads(body)["workload_type"] == "critique_review":
            return httpx.Response(200, json=gateway_text_result(json.dumps(self.lint_reply)))
        return httpx.Response(200, json=self.script.pop(0))

    def client(self) -> GatewayClient:
        return GatewayClient("http://gw.test:8010", "tok", transport=httpx.MockTransport(self.handler))

    def parsed(self) -> list[dict]:
        return [json.loads(b) for b in self.bodies]


def set_levels(session, *, on: bool = True, low=(), mid=(), high=(), hides=()) -> None:
    """Save levels with the real service. Each item is "domain/category". Types not listed stay unset."""
    for keys, tier, redact in ((low, "house_low", False), (mid, "house_mid", False),
                               (high, "house_high", False), (hides, "house_mid", True)):
        for key in keys:
            domain, category = key.split("/")
            ps.save_type(session, domain, category, tier, redact, "test")
    ps.set_enforcing(session, on, "test")


STANDARD = dict(low=["house/house_appliance"], mid=["financials/bill"], high=["insurance/home_insurance", "house/maintenance_log"],
                hides=["house/ownership_document"])           # financials/statement and the rest: no level


def build_world(session, docs_dir) -> SimpleNamespace:
    docs_dir.mkdir(parents=True, exist_ok=True)

    def file(name: str, content: bytes) -> str:
        path = docs_dir / name
        path.write_bytes(content)
        return str(path)

    w = SimpleNamespace()
    w.a = make_document(session, domain=Domain.HOUSE, category="house_appliance",
                        fields={"item_name": "Boiler", "room": CLEAN_ROOM}, filename="boiler-manual.png",
                        file_path=file("boiler-manual.png", PNG))
    w.b = make_document(session, domain=Domain.FINANCIALS, category="bill", filename="electricity-bill.pdf",
                        file_path=file("electricity-bill.pdf", b"%PDF-1.4 clean"))
    w.c = make_document(session, domain=Domain.INSURANCE, category="home_insurance", filename=f"{HIGH}-file.png",
                        fields={"insurer": f"{HIGH}-insurer", "policy_number": f"{HIGH}-policy", "notes": f"{HIGH}-notes"},
                        file_path=file(f"{HIGH}-file.png", b"\x89PNG\r\n\x1a\n" + FILE_BYTES.encode()))
    w.d = make_document(session, domain=Domain.FINANCIALS, category="statement", filename=f"{UNSET}-statement.pdf",
                        file_path=file("statement.pdf", b"%PDF " + UNSET.encode()))
    w.e = make_document(session, domain=Domain.HOUSE, category="ownership_document", filename=f"{HIDES}-deed.pdf",
                        fields={"item_name": f"{HIDES}-item"}, file_path=file("deed.pdf", b"%PDF " + HIDES.encode()))
    w.unk = make_document(session, domain=Domain.HOUSE, category="legacy_kind", filename=f"{UNKNOWN}.pdf",
                          fields={"item_name": f"{UNKNOWN}-item"}, file_path=file("unknown.pdf", b"%PDF"))
    w.rec = make_record(session, domain=Domain.HOUSE, category="maintenance_log",
                        fields={"item_name": "Boiler", "notes": f"{REC}-notes"})

    # Wiki. page_clean: all claims from an included document. page_high: only excluded sources.
    # page_mixed: a clean claim, a claim with BOTH an included and an excluded source, a superseded
    # excluded claim, and a stored summary that repeats hidden content. page_answer: a saved Ask answer.
    w.page_clean = make_page(session, "Boiler", domain=Domain.HOUSE, summary="Gas boiler")
    w.claim_clean = make_claim(session, w.page_clean, "room", CLEAN_ROOM, doc_ids=[w.a.id])
    w.page_high = make_page(session, f"Policy {PAGE}", domain=Domain.INSURANCE, summary=f"{PAGE} summary")
    w.claim_high = make_claim(session, w.page_high, "insurer", f"{PAGE}-insurer", doc_ids=[w.c.id])
    w.page_neighbour = make_page(session, f"Linked {NEIGHBOUR}", domain=Domain.INSURANCE)
    make_claim(session, w.page_neighbour, "x", f"{NEIGHBOUR}-value", doc_ids=[w.c.id])
    w.page_mixed = make_page(session, "Water supply", domain=Domain.FINANCIALS, summary=f"Aguas · {SUMMARY}")
    w.claim_mixed_clean = make_claim(session, w.page_mixed, "provider", "Aguas", doc_ids=[w.b.id])
    w.claim_mixed = make_claim(session, w.page_mixed, "tariff", f"{MIXED}-tariff", doc_ids=[w.b.id, w.c.id])
    w.claim_history = make_claim(session, w.page_mixed, "old", f"{HISTORY}-old", doc_ids=[w.d.id],
                                 superseded_at=datetime(2026, 2, 1))
    w.page_answer = make_page(session, "Saved answer", domain=None, page_type="answer", summary=CLEAN_ANSWER)
    make_claim(session, w.page_answer, "answer", CLEAN_ANSWER)
    make_link(session, w.page_clean, w.page_neighbour)
    make_link(session, w.page_neighbour, w.page_clean)
    w.pages = [w.page_clean, w.page_high, w.page_neighbour, w.page_mixed, w.page_answer]

    # Transactions: one read from the included bill, two from the statement that has no level.
    for doc, desc, amount in ((w.b, CLEAN_PAYEE, 80.0), (w.d, f"{TXN}-payee", 120.0), (w.d, f"{TXN}-other", 5.0)):
        session.add(Transaction(document_id=doc.id, description=desc, category=Category.OTHER_EXPENSE, amount=amount,
                                currency="EUR", paid_date=date(2026, 3, 1), transaction_type=TransactionType.DEBIT))
    session.commit()

    # To-dos: free-standing, from an excluded document, from an excluded transaction, from an excluded record.
    bad_txn_id = session.exec(select(Transaction.id).where(Transaction.description == f"{TXN}-other")).one()
    session.add_all([Todo(title=CLEAN_TODO),
                     Todo(title=f"{TODO}-from-doc", document_id=w.d.id),
                     Todo(title=f"{TODO}-from-txn", transaction_id=bad_txn_id),
                     Todo(title=f"{TODO}-from-rec", record_id=w.rec.id)])
    session.commit()

    # Health: always High. One filed paper, read into Health's tables (its summary carries HEALTH_PAPER).
    health = make_document(session, domain=Domain.HEALTH, category="lab_result", filename="health-lab.pdf",
                           file_path=file("health-lab.pdf", b"%PDF-1.4 " + HEALTH_PAPER.encode()))
    apply_extraction(session, health, ExtractedHealth(
        person_name="Maria Santos Almeida", document_date=date(2026, 3, 12), provider="Lab Norte", record_kind="test",
        areas=["Heart and circulation"], conditions=[], summary=f"Maria: lipid panel, {HEALTH_PAPER}",
        measurements=[ExtractedMeasurement("LDL cholesterol", 162.0, None, "mg/dL", None, 100.0,
                                           date(2026, 3, 12), False, "LDL")]))
    w.health_paper = health

    # Waiting in the Inbox: one suggested as an excluded type, one as an included type.
    for suggested in ("insurance/home_insurance", "financials/bill"):
        d = Document(filename=f"pending-{suggested.split('/')[1]}.pdf", file_path="/nonexistent", content_hash=suggested,
                     source=DocumentSource.EMAIL, status=DocumentStatus.PENDING_REVIEW)
        session.add(d)
        session.commit()
        session.refresh(d)
        domain, category = suggested.split("/")
        session.add(InboxItem(document_id=d.id, suggested_domain=domain, suggested_category=category))
    session.commit()
    return w


def ask_script(w, final="The boiler is in the kitchen [[wiki:%d]]."):
    """One tool round that uses EVERY Ask tool (and tries every excluded document), then a final answer."""
    calls = [
        {"id": "t1", "name": "read_wiki_pages", "input": {"page_ids": [p.id for p in w.pages] + [9999]}},
        {"id": "t2", "name": "find_sources", "input": {"limit": 60}},
        {"id": "t3", "name": "find_sources", "input": {"query": "insurer policy notes statement deed item", "limit": 60}},
        {"id": "t4", "name": "read_document_file", "input": {"document_id": w.c.id}},
        {"id": "t5", "name": "read_document_file", "input": {"document_id": w.d.id}},
        {"id": "t6", "name": "read_document_file", "input": {"document_id": w.e.id}},
        {"id": "t7", "name": "read_document_file", "input": {"document_id": w.unk.id}},
        {"id": "t8", "name": "read_document_file", "input": {"document_id": w.a.id}},
        {"id": "t9", "name": "list_todos", "input": {"include_done": True}},
        {"id": "t10", "name": "financials_spending", "input": {"date_from": "2026-01-01", "date_to": "2026-12-31", "group_by": "merchant"}},
        {"id": "t11", "name": "financials_find_transactions", "input": {"query": ""}},
        {"id": "t12", "name": "health_find_records", "input": {"person": "Maria", "query": "Maria"}},
        {"id": "t13", "name": "health_measurements", "input": {"person": "Maria", "test": "LDL"}},
    ]
    return [gateway_tool_result(calls), gateway_text_result(final % w.page_clean.id)]


def leaks(bodies: list[str], sentinels=EXCLUDED, files=()) -> list[str]:
    """Every excluded sentinel (and base64 of every excluded file) found in any captured body."""
    found = [s for s in sentinels for b in bodies if s in b]
    for content in files:
        encoded = base64.b64encode(content).decode()
        found += [f"base64:{content[:20]!r}" for b in bodies if encoded in b]
    return sorted(set(found))


PRIVATE_FILES = [b"\x89PNG\r\n\x1a\n" + FILE_BYTES.encode()]              # the High insurance file
EXCLUDED_FILES = [*PRIVATE_FILES, b"%PDF " + UNSET.encode(), b"%PDF " + HIDES.encode()]
STILL_EXCLUDED_FILES = EXCLUDED_FILES[len(PRIVATE_FILES):]
