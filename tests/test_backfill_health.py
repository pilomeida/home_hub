from pathlib import Path

import pytest

from scripts.backfill_health import backfill
from app.models.domain import Domain
from app.services.domain_classifier import DomainSuggestion


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")


@pytest.mark.asyncio
async def test_papers_land_in_the_inbox_and_confident_health_ones_are_approved_on_request(session, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (folder / "b.pdf").write_bytes(b"%PDF-1.4 b")
    (folder / "notes.txt").write_bytes(b"not a paper")

    async def classify(path, context_text):
        # The stored copy has a uuid name (save_file), so tell the papers apart by their stored bytes.
        if Path(path).read_bytes().endswith(b" a"):
            return DomainSuggestion(Domain.HEALTH, "lab_result", 0.95, "lab")
        return DomainSuggestion(None, None, 0.0, "unsure")

    async def fake_approve(session, document_id, **kwargs):          # filing itself is covered by test_health_handler
        fake_approve.calls.append((document_id, kwargs["domain_value"], kwargs["category"]))
    fake_approve.calls = []

    counts = await backfill(session, folder, auto_approve=True, classify=classify, approve=fake_approve)
    assert counts == {"received": 2, "duplicates": 0, "approved": 1, "waiting": 1, "failed": 0}
    assert [c[1:] for c in fake_approve.calls] == [("health", "lab_result")]

    again = await backfill(session, folder, auto_approve=False, classify=classify, approve=fake_approve)
    assert again["duplicates"] == 2 and again["received"] == 0
