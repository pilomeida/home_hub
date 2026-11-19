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
