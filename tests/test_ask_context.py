from datetime import date

from app.models.document import DocumentStatus
from app.services.ask.context import build_system_prompt
from tests.knowledge_factories import make_document, make_page


def test_prompt_has_rules_date_domains_index_and_pending_note(session, fake_domain):
    boiler = make_page(session, "Boiler", summary="Vaillant boiler, utility room")
    make_page(session, "Who does what", domain=None, summary="Chores")
    make_document(session, filename="a.pdf", category="manual")
    make_document(session, filename="b.pdf", domain=None, category=None, status=DocumentStatus.PENDING_REVIEW)

    prompt, citables = build_system_prompt(session, date(2026, 9, 24))

    assert "Today is 2026-09-24" in prompt
    assert f"- wiki:{boiler.id} Boiler — Vaillant boiler, utility room" in prompt
    assert "Who does what" in prompt
    assert "house (Fake)" in prompt and "manual (Manual)" in prompt and "Item" in prompt
    assert "1 document(s) are awaiting review" in prompt
    assert "follow-up" in prompt and "[[rec:" in prompt and "NOTE" in prompt and "find_sources" in prompt
    assert f"wiki:{boiler.id}" in {c.ref for c in citables}


def test_prompt_without_pending_has_no_review_note(session, fake_domain):
    prompt, _ = build_system_prompt(session, date(2026, 9, 24))
    assert "awaiting review" not in prompt and "(the wiki is empty)" in prompt
