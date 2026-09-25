import json

from app.domains.entries import domain_entries, record_for_document
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.record import Record


def test_entries_merge_documents_and_records_without_double_counting_attachments(session, fake_domain):
    manual = Document(filename="m.pdf", file_path="/tmp/m.pdf", content_hash="e1", source=DocumentSource.MANUAL,
                      status=DocumentStatus.PROCESSED, domain=Domain.HOUSE, category="manual",
                      fields_json=json.dumps({"item_name": "Boiler"}))
    attachment = Document(filename="receipt.pdf", file_path="/tmp/r.pdf", content_hash="e2", source=DocumentSource.MANUAL,
                          status=DocumentStatus.PROCESSED, domain=Domain.HOUSE, category="visit")
    pending = Document(filename="p.pdf", file_path="/tmp/p.pdf", content_hash="e3", source=DocumentSource.MANUAL)
    session.add_all([manual, attachment, pending])
    session.commit()
    with_file = Record(domain=Domain.HOUSE, category="visit", document_id=attachment.id,
                       fields_json=json.dumps({"item_name": "Boiler", "visit_date": "2026-01-01"}))
    by_hand = Record(domain=Domain.HOUSE, category="visit", fields_json=json.dumps({"item_name": "Boiler", "visit_date": "2026-05-01"}))
    retired = Record(domain=Domain.HOUSE, category="visit", retired_at=manual.created_at)
    session.add_all([with_file, by_hand, retired])
    session.commit()

    entries = domain_entries(session, Domain.HOUSE)

    assert [(e.kind.value, e.title) for e in entries] == [
        ("document", "m.pdf"), ("record", "receipt.pdf"), ("record", "Visit — entered by hand"),
    ]
    assert entries[1].url == f"/fake/records/{with_file.id}" and entries[1].fields["visit_date"] == "2026-01-01"
    assert record_for_document(session, attachment).id == with_file.id
    assert record_for_document(session, manual) is None
