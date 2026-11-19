"""PLAN step 2a-b: one memory of past answers. One reader, one order: a person's rule, then the AI's, then the
system's; then priority. A switched-off rule answers nothing. The provider rules and the recognition rules both
live here and are read through it."""

from sqlmodel import select

from app.models.rule import AI, PERSON, SYSTEM, Rule
from app.models.transaction import TransactionType
from app.services import rules
from app.services.provider_rules import apply_to_providers, find_rule_node, provider_key
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, get_node
from tests.test_merchant_reconcile import _merchant, _t

FAMILY, REST, SM = "loans-debt.money-lent-out.to-family", "food.eat-out.restaurants", "food.groceries.supermarket"


def _spending(session, source, slug, priority=0, enabled=True, pattern="trf mbway p/matias"):
    rule = rules.remember(session, looks_at="description", how="key", pattern=pattern, source=source,
                          node_id=get_node(session, slug).id, enabled=enabled)
    rule.priority = priority
    session.commit()
    return rule


def test_a_persons_rule_wins_over_ai_and_system_whatever_their_priority(session):
    ensure_taxonomy(session)
    _spending(session, SYSTEM, SM, priority=100)
    _spending(session, AI, REST, priority=50)
    _spending(session, PERSON, FAMILY, priority=0)
    found = rules.find(session, rules.SPENDING, {"description": "trf  MBWAY p/Matias"})
    assert (found.source, found.node_id) == (PERSON, get_node(session, FAMILY).id)
    assert find_rule_node(session, "TRF MBWAY P/MATIAS").slug == FAMILY


def test_without_a_person_the_ai_beats_the_system_then_priority_decides(session):
    ensure_taxonomy(session)
    _spending(session, SYSTEM, SM, priority=100)
    _spending(session, AI, REST)
    assert rules.find(session, rules.SPENDING, {"description": "TRF MBWAY P/MATIAS"}).source == AI
    session.delete(session.exec(select(Rule).where(Rule.source == AI)).one())
    session.commit()
    low = _spending(session, SYSTEM, FAMILY, priority=1, pattern="trf mbway")
    low.how = "prefix"
    session.commit()
    assert rules.find(session, rules.SPENDING, {"description": "TRF MBWAY P/MATIAS"}).node_id == get_node(session, SM).id


def test_a_switched_off_rule_answers_nothing(session):
    ensure_taxonomy(session)
    _spending(session, AI, REST, enabled=False)
    assert rules.find(session, rules.SPENDING, {"description": "TRF MBWAY P/MATIAS"}) is None


def test_each_way_of_matching(session):
    def rule(how, pattern):
        return Rule(looks_at="description", how=how, pattern=pattern)
    assert rules.matches(rule("contains", "vodafone"), "AMIGO (Vodafone Portugal)")
    assert rules.matches(rule("exact", "edp comercial"), " EDP Comercial ")
    assert not rules.matches(rule("exact", "edp"), "EDP Comercial")
    assert rules.matches(rule("prefix", "trf mbway"), "TRF MBWAY P/X")
    assert rules.matches(rule("regex", r"\b27134-\d{9}\b"), "PAG SERVICOS 27134-627088996")
    assert not rules.matches(rule("regex", "(unclosed"), "anything")                       # a bad pattern never matches
    assert rules.matches(rule("key", provider_key("TRF MBWAY P/XXXXX1225")), "trf mbway p/xxxxx1225")


def test_a_person_filing_by_bank_text_writes_one_rule_and_a_second_answer_replaces_it(session):
    ensure_taxonomy(session)
    m = _merchant(session, "MBWay Transfer", REST)
    _t(session, m, UNSORTED_SLUG, TransactionType.TRANSFER, description="TRF MBWAY P/MATIAS")
    apply_to_providers(session, ["trf mbway p/matias"], get_node(session, REST), by="pedro@example.com")
    apply_to_providers(session, ["trf mbway p/matias"], get_node(session, FAMILY), by="rute@example.com")
    [rule] = session.exec(select(Rule)).all()
    assert (rule.source, rule.node_id, rule.approved_by) == (PERSON, get_node(session, FAMILY).id, "rute@example.com")
    assert rule.approved_at is not None and rule.example == "TRF MBWAY P/MATIAS"


def test_recognition_reads_paper_kind_rules_from_the_same_table(session):
    from app.privacy import recognise
    recognise.remember(session, category="financials", paper_kind="bill", by="p@x", sender="faturas@edp.pt")
    found = recognise.recognise(session, filename="x.pdf", content=b"", context_text="Originally from: faturas@edp.pt")
    assert (found.category, found.paper_kind) == ("financials", "bill")
    assert rules.find(session, rules.PAPER_KIND, {"sender": "faturas@edp.pt"}).hits == 1


# --- done when (step 2): confirming a merchant writes one rule, and no batch pass overrides a person ----------

def test_confirming_a_merchant_writes_one_rule_and_a_batch_pass_keeps_it(session):
    from app.models.merchant import Merchant
    from app.services.merchant_assign import assign_category
    from app.services.merchant_reconcile import reconcile_merchants
    ensure_taxonomy(session)
    m = _merchant(session, "Padaria Central", REST)
    t1 = _t(session, m, REST, description="COMPRA 3315 PADARIA CENTRAL")
    assign_category(session, [m.id], get_node(session, "food.groceries.butcher-fish-bakery"), by="rute@example.com")
    assign_category(session, [m.id], get_node(session, "food.groceries.butcher-fish-bakery"), by="rute@example.com")
    [rule] = session.exec(select(Rule)).all()                                           # one rule, not two
    assert (rule.looks_at, rule.pattern, rule.source, rule.approved_by) == (
        "counterparty", "padaria central", PERSON, "rute@example.com")
    # A batch pass that would follow where most entries sit cannot move it back.
    stray = session.get(Merchant, m.id)
    stray.default_category_id, stray.confirmed = get_node(session, REST).id, False   # e.g. an old relabel script
    session.add(stray)
    for _ in range(3):
        _t(session, m, REST, description="COMPRA 3315 PADARIA CENTRAL")
    session.commit()
    reconcile_merchants(session, dry_run=False)
    session.refresh(t1)
    assert t1.category_id == get_node(session, "food.groceries.butcher-fish-bakery").id


def test_a_new_line_follows_the_persons_counterparty_rule_over_the_merchants_stored_default(session):
    import asyncio
    from app.models.transaction import Transaction
    from app.models.document import Document, DocumentSource
    from app.services.classification_engine import classify_transaction
    ensure_taxonomy(session)
    m = _merchant(session, "Padaria Central", REST)
    m.confirmed = True
    rules.remember_merchant(session, m, get_node(session, "food.groceries.butcher-fish-bakery"), by="p@x")
    session.commit()
    doc = Document(filename="s.pdf", file_path="/tmp/s.pdf", content_hash="h-new", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, description="Padaria Central", amount=3.2, transaction_type=TransactionType.DEBIT)
    session.add(t); session.commit()
    asyncio.run(classify_transaction(session, t))
    assert t.category_id == get_node(session, "food.groceries.butcher-fish-bakery").id


# --- c. an AI answer below the bar is saved switched off and waits for a person --------------------------------

def _classify_new(session, text, confidence):
    import asyncio
    import json
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction
    from app.services.classification_engine import classify_transaction
    from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
    ensure_taxonomy(session)
    reply = {"canonical_name": "Loja Nova", "node_slug": REST, "nature": "discretionary", "confidence": confidence}
    doc = Document(filename="s.pdf", file_path="/tmp/s.pdf", content_hash=f"h-{text}", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, description=text, amount=12.0, transaction_type=TransactionType.DEBIT)
    session.add(t); session.commit()
    asyncio.run(classify_transaction(session, t, gateway=FakeGateway([gateway_text_result(json.dumps(reply))])))
    session.commit()
    return t, session.exec(select(Rule).where(Rule.source == AI)).one()


def test_a_doubtful_ai_answer_is_saved_switched_off_and_the_line_waits_for_review(session, levels_not_binding):
    t, rule = _classify_new(session, "LOJA NOVA LDA", 0.55)
    assert (rule.enabled, rule.confidence, rule.node_id) == (False, 0.55, get_node(session, REST).id)
    assert t.category_id == get_node(session, UNSORTED_SLUG).id


def test_a_sure_ai_answer_is_saved_switched_on_and_files_the_line(session, levels_not_binding):
    t, rule = _classify_new(session, "LOJA NOVA LDA", 0.93)
    assert rule.enabled and t.category_id == get_node(session, REST).id


# --- e. one merchant for a provider written two ways --------------------------------------------------------

def test_a_provider_written_two_ways_resolves_to_one_merchant_through_a_rule(session, levels_not_binding):
    from app.models.merchant import Merchant
    ensure_taxonomy(session)
    known = _merchant(session, "Amigo (Vodafone)", "housing.utilities.telecom", confirmed=True)
    rules.remember_alias(session, "Amigo (Vodafone Portugal)", "Amigo (Vodafone)", by="p@x")
    session.commit()
    import asyncio, json
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction
    from app.services.classification_engine import classify_transaction
    from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
    reply = {"canonical_name": "Amigo (Vodafone Portugal)", "node_slug": "housing.utilities.telecom",
             "nature": "essential", "confidence": 0.95}
    doc = Document(filename="s.pdf", file_path="/tmp/s.pdf", content_hash="h-amigo", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, description="AMIGO VODAFONE PT 0042", amount=15.0,
                    transaction_type=TransactionType.DEBIT)
    session.add(t); session.commit()
    result = asyncio.run(classify_transaction(session, t, gateway=FakeGateway([gateway_text_result(json.dumps(reply))])))
    assert result.merchant.id == known.id and not result.created_new_merchant
    assert session.exec(select(Merchant).where(Merchant.merged_into_id.is_(None))).all() == [known]


def test_merging_merchants_remembers_each_spelling(session):
    from app.services.merchant_merge import merge_merchants
    ensure_taxonomy(session)
    keep = _merchant(session, "Amigo (Vodafone)")
    other = _merchant(session, "Amigo EasyPay")
    merge_merchants(session, [other.id], keep.id, by="pedro@example.com")
    assert rules.merchant_alias(session, "AMIGO EASYPAY") == "Amigo (Vodafone)"
    assert rules.merchant_alias(session, "Amigo (Vodafone)") is None
