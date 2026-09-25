import dataclasses
from datetime import date

from app.domains import registry
from app.models.domain import Domain
from app.models.todo import Todo
from app.services.domain_overview import build_domain_cards
from tests.domain_fakes import make_fake_spec


def test_one_card_per_domain_with_open_todo_count(session, fake_domain):
    session.add(Todo(title="a", domain=Domain.HOUSE))
    session.add(Todo(title="b", domain=Domain.HOUSE, done=True))
    session.commit()

    cards = build_domain_cards(session, date(2026, 9, 24))

    assert [(c.label, c.open_todos) for c in cards] == [("Fake", 1)]
    assert cards[0].lines[0].text == "fake line"


def test_a_failing_card_does_not_break_the_overview(session, monkeypatch):
    def broken(session, today):
        raise RuntimeError("boom")

    spec = dataclasses.replace(make_fake_spec(), overview_card=broken)
    monkeypatch.setattr(registry, "_specs_cache", {spec.domain: spec})

    cards = build_domain_cards(session, date(2026, 9, 24))

    assert cards[0].label == "Fake" and cards[0].url == "/fake"
    assert cards[0].lines[0].attention is True
