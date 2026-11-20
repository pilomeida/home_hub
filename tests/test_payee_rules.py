"""PLAN step 9 D8: the Multibanco entities, the payment texts and the supermarket chains are Pedro's person rules in
the rules table (written by the production seed), so a change is made on the table and a person's later answer about
a merchant still wins."""

from sqlmodel import select

from app.models.rule import DESCRIPTION, ENTITY, PERSON, Rule
from app.services import payee_seed, rules
from app.services.merchant_rules import keyword_match
from app.services.payment_entities import payee_of
from app.services.structured_providers import structured_merchant
from app.services.taxonomy import get_node


def test_every_first_ruling_is_a_person_rule_with_its_merchant_and_leaf(payee_rules):
    rows = payee_rules.exec(select(Rule).where(Rule.looks_at.in_((ENTITY, DESCRIPTION)))).all()
    assert len(rows) == len(payee_seed.ENTITIES) + len(payee_seed.TEXTS) + len(payee_seed.CHAINS)
    assert all(r.source == PERSON and r.enabled and r.merchant and r.node_id and r.approved_by == "Pedro" for r in rows)
    keyed = {r.merchant_key for r in rows if r.merchant_key}
    assert keyed == {f"entity:{n}" for n, _, _ in payee_seed.ENTITIES} | {f"entity:{k}" for _, k, _, _ in payee_seed.TEXTS}


def test_the_merchants_the_entities_made_keep_their_keys(payee_rules):
    assert structured_merchant(payee_rules, "PAG SERVICOS 1835 27134-627088996").key == "entity:27134"
    assert structured_merchant(payee_rules, "COMPRA 3315 EUPAGO- INST PAG LDA PORTO").key == "entity:text:fixando"


def test_a_rule_switched_off_on_the_table_stops_answering(payee_rules):
    session = payee_rules
    entity = session.exec(select(Rule).where(Rule.looks_at == ENTITY, Rule.pattern == "27134")).one()
    chain = session.exec(select(Rule).where(Rule.looks_at == DESCRIPTION, Rule.merchant == "Lidl")).one()
    entity.enabled = chain.enabled = False
    session.add_all([entity, chain]); session.commit()
    assert payee_of(session, "PAG SERVICOS 1835 27134-627088996") is None
    assert keyword_match(session, "LIDL MAFRA") is None
    assert keyword_match(session, "ALDI MAFRA") == ("food.groceries.supermarket", "Aldi")


def test_a_text_naming_two_chains_is_the_chain_named_first(payee_rules):
    assert keyword_match(payee_rules, "Continente Modelo Hiper Mafra")[1] == "Continente"
    assert keyword_match(payee_rules, "MODELO CONTINENTE")[1] == "Modelo Hiper"


def test_a_payee_named_on_the_table_answers_without_a_code_change(payee_rules):
    session = payee_rules
    leaf = get_node(session, "housing.utilities.water")
    rules.remember(session, looks_at=ENTITY, how="exact", pattern="99999", source=PERSON, by="Pedro",
                   merchant="SMAS Mafra", node_id=leaf.id, merchant_key="entity:99999")
    session.commit()
    payee = payee_of(session, "PAG SERVICOS 1835 99999-123456789")
    assert (payee.number, payee.name, payee.node_slug) == ("99999", "SMAS Mafra", "housing.utilities.water")


def test_a_persons_later_answer_about_a_chain_merchant_wins_over_the_chain_rule(payee_rules):
    """The chain rule names a new line's merchant; the category then follows that merchant, so confirming Pingo Doce
    under another leaf is not outranked by the chain rule reading the same bank text."""
    from app.models.merchant import Merchant
    session = payee_rules
    pingo = Merchant(canonical_name="Pingo Doce", normalized_key="pingo doce")
    session.add(pingo); session.commit()
    other = get_node(session, "food.eat-out.restaurants")
    rules.remember_merchant(session, pingo, other, by="Rute")
    session.commit()
    hit = rules.person_spending_rule(session, description="PINGO DOCE MAFRA", counterparty="Pingo Doce")
    assert hit is not None and hit.node_id == other.id

