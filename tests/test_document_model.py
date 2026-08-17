from app.models.document import Document, DocumentSource, DocumentStatus


def test_create_and_read_document(session):
    document = Document(
        filename="edp-august.pdf",
        file_path="/data/documents/edp-august.pdf",
        content_hash="abc123",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    assert document.id is not None
    assert document.status == DocumentStatus.PENDING
    assert document.password_protected is False
