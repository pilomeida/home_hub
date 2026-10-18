from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction
from app.services.dedup import find_duplicate_transaction, find_existing_document_by_hash


def test_find_existing_document_by_hash_returns_match(session):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="abc123",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()

    found = find_existing_document_by_hash(session, "abc123")

    assert found is not None
    assert found.filename == "bill.pdf"


def test_find_existing_document_by_hash_returns_none_when_missing(session):
    assert find_existing_document_by_hash(session, "does-not-exist") is None


def test_find_duplicate_transaction_matches_provider_and_period(session):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="hash1",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, description="EDP", category=Category.ELECTRICITY,
        amount=50.0, currency="EUR", statement_period="2026-08",
    )
    session.add(transaction)
    session.commit()

    found = find_duplicate_transaction(session, description="EDP", statement_period="2026-08")

    assert found is not None
    assert found.id == transaction.id


def test_find_duplicate_transaction_returns_none_without_statement_period(session):
    assert find_duplicate_transaction(session, description="EDP", statement_period=None) is None


def test_find_duplicate_transaction_ignores_statement_derived_transactions(session):
    """A statement line item (Document.category == 'statement') sharing
    provider/period with an unrelated bill must not be treated as a
    duplicate — only bill-derived transactions count."""
    statement_document = Document(
        filename="statement.pdf", file_path="/tmp/statement.pdf", content_hash="hash-stmt",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, category="statement",
    )
    session.add(statement_document)
    session.commit()
    session.refresh(statement_document)

    session.add(Transaction(
        document_id=statement_document.id, description="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-07",
    ))
    session.commit()

    found = find_duplicate_transaction(session, description="EDP", statement_period="2026-07")

    assert found is None


def test_find_duplicate_transaction_matches_bill_derived_transaction(session):
    """A bill-derived transaction (Document.category == 'bill') sharing
    provider/period must still be detected as a duplicate."""
    bill_document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="hash-bill-typed",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, category="bill",
    )
    session.add(bill_document)
    session.commit()
    session.refresh(bill_document)

    transaction = Transaction(
        document_id=bill_document.id, description="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-07",
    )
    session.add(transaction)
    session.commit()

    found = find_duplicate_transaction(session, description="EDP", statement_period="2026-07")

    assert found is not None
    assert found.id == transaction.id


def test_find_duplicate_transaction_matches_when_category_is_null(session):
    """A pre-migration Document with category=None must still participate in
    dedup — NULL means 'not a statement' (every document created before
    this branch was necessarily a bill), not 'excluded'."""
    legacy_document = Document(
        filename="legacy-bill.pdf", file_path="/tmp/legacy-bill.pdf", content_hash="hash-legacy-typed",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    assert legacy_document.category is None
    session.add(legacy_document)
    session.commit()
    session.refresh(legacy_document)

    transaction = Transaction(
        document_id=legacy_document.id, description="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-07",
    )
    session.add(transaction)
    session.commit()

    found = find_duplicate_transaction(session, description="EDP", statement_period="2026-07")

    assert found is not None
    assert found.id == transaction.id


def _bill(session, description, amount, period, issue, name, h):
    from datetime import date
    doc = Document(filename=name, file_path=f"/tmp/{name}", content_hash=h, source=DocumentSource.EMAIL,
                   status=DocumentStatus.PROCESSED)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    tx = Transaction(document_id=doc.id, description=description, category=Category.ELECTRICITY, amount=amount,
                     currency="EUR", statement_period=period, issue_date=issue)
    session.add(tx)
    session.commit()
    return tx


def test_the_same_bill_with_the_provider_written_another_way_is_a_duplicate(session):
    """Invoice and detail sheet of one bill: the model wrote 'amigo (Vodafone Portugal)' and 'amigo (Vodafone)'
    (2026-10-17: the second became a second bill and a second 'Pay' to-do)."""
    from datetime import date
    first = _bill(session, "amigo (Vodafone Portugal)", 9.98, "2026-09", date(2026, 9, 25), "fatura.pdf", "h-a")
    found = find_duplicate_transaction(session, "amigo (Vodafone)", "2026-09", amount=9.98, issue_date=date(2026, 9, 25))
    assert found is not None and found.id == first.id
    assert find_duplicate_transaction(session, "Amigo - Vodafone Portugal, S.A.", "2026-09", amount=9.98) is not None


def test_consecutive_monthly_bills_of_the_same_amount_are_two_bills_even_with_the_same_billing_period(session):
    """2026-10-17: the March invoice was dropped as a 'duplicate' of April's because the model gave both the same
    statement period; same provider, same amount, but issued a month apart."""
    from datetime import date
    _bill(session, "amigo (Vodafone Portugal)", 9.98, "2026-03", date(2026, 4, 25), "fatura_abril.pdf", "h-c")
    assert find_duplicate_transaction(session, "amigo (Vodafone Portugal)", "2026-03", amount=9.98,
                                      issue_date=date(2026, 3, 25)) is None


def test_an_invoice_and_its_detail_sheet_match_whatever_billing_period_the_model_gave_each(session):
    from datetime import date
    invoice = _bill(session, "amigo (Vodafone Portugal)", 15.07, "2024-06", date(2024, 7, 27), "fatura.pdf", "h-d")
    for period in ("2024-06", "2024-07", None):                                  # the detail sheet's period varies or is missing
        found = find_duplicate_transaction(session, "amigo (Vodafone)", period, amount=15.07, issue_date=date(2024, 7, 22))
        assert found is not None and found.id == invoice.id, period


def test_a_similar_name_alone_is_not_enough_amount_and_date_must_agree(session):
    from datetime import date
    _bill(session, "amigo (Vodafone Portugal)", 9.98, "2026-09", date(2026, 9, 25), "fatura.pdf", "h-b")
    assert find_duplicate_transaction(session, "amigo (Vodafone)", "2026-09", amount=11.02, issue_date=date(2026, 9, 25)) is None
    assert find_duplicate_transaction(session, "amigo (Vodafone)", "2026-09", amount=9.98, issue_date=date(2026, 10, 25)) is None
    assert find_duplicate_transaction(session, "MEO", "2026-09", amount=9.98, issue_date=date(2026, 9, 25)) is None   # another provider
    # with an unknown date, the same statement period is required
    assert find_duplicate_transaction(session, "amigo (Vodafone)", "2026-09", amount=9.98, issue_date=None) is not None
    assert find_duplicate_transaction(session, "amigo (Vodafone)", "2026-10", amount=9.98, issue_date=None) is None
