"""Messages on the Transactions, House, Insurance, Documents, To-dos and Wiki check pages (decision D14).

Every message says what did not happen, why, and what the person can do. The inputs come from the real forms
and the production code that refuses them, never from hand-typed stand-ins for stored values."""

import html
import io

import pytest

import app.domains.house.handler as house_handler
from app import problems as pr
from app.domains.registry import get_spec
from app.models.debt import DebtDirection
from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.models.record import Record
from app.models.transaction import Category, Transaction, TransactionType
from app.models.wiki_lint import LintFinding, LintFindingKind, LintFindingStatus, LintRun
from app.services.debt_ledger import create_informal_debt, link_transaction_as_entry
from app.services.taxonomy import ensure_taxonomy
from app.services.wiki_lint.runner import start_run
from app.domains.house.warranty import WarrantyDates

HTML = {"accept": "text/html"}


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    async def fake_extract(file_path, client=None):
        return WarrantyDates(None, None)

    monkeypatch.setattr(house_handler, "extract_warranty_dates", fake_extract)


def assert_three_parts(text, what, why, next_step):
    text = html.unescape(text)
    assert what in text and why in text and next_step in text, text
    assert text.index(what) < text.index(why) < text.index(next_step), text


def _pdf(client, data, content=b"pdf", filename="doc.pdf", mime="application/pdf"):
    return client.post("/house/upload", data=data, files={"file": (filename, io.BytesIO(content), mime)},
                       follow_redirects=False)


def _record_with_file(client, content=b"log-1"):
    r = _pdf(client, {"category": "maintenance_log", "item_name": "Boiler", "service_date": "2026-02-02"}, content)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[-1])


def _record_without_file(client):
    r = client.post("/house/upload", data={"category": "maintenance_log", "item_name": "Boiler",
                                           "service_date": "2026-02-02"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[-1])


def _txn(session, provider, amount, ttype=TransactionType.DEBIT):
    document = Document(filename=f"{provider}.pdf", file_path=f"synthetic/{provider}.pdf",
                        content_hash=f"hash-{provider}", source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)
    t = Transaction(document_id=document.id, description=provider, category=Category.OTHER_EXPENSE,
                    amount=amount, currency="EUR", transaction_type=ttype)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


# --- the wording table and its helpers --------------------------------------------------------------

@pytest.mark.parametrize("text", list(pr._NOTICE_TEXT))
def test_every_listed_notice_has_what_why_and_next_step(text):
    p = pr.notice_problem(text)
    assert p.what.strip() and p.why.strip() and p.next_step.strip(), text
    assert p.why != p.what and len(p.why) > 20 and len(p.next_step) > 12, text
    assert p.what.endswith(".") and p.next_step.endswith(".")


def test_an_unlisted_plain_refusal_keeps_its_own_words_and_gets_a_next_step():
    text = pr.notice("Something about the label was unusual", what="Not saved.")
    assert text.startswith("Not saved. Something about the label was unusual. ")
    assert "Check what is on the page" in text and "damaged" not in text


def test_file_type_refusals_are_not_mistaken_for_a_damaged_pdf():
    # a PDF mention in a type refusal must not produce the "file couldn't be read" reason
    text = pr.notice("House appliance accepts image, pdf files only")
    assert "photo or PDF files" in text and "damaged" not in text and "password" not in text


def test_file_notices_writes_out_file_refusals_and_leaves_instructions_alone():
    out = pr.file_notices({"file": "This file is already filed elsewhere", "category": "Choose a category",
                           "domain": "Choose a domain"})
    assert out["file"].startswith("Not filed. ") and out["category"] == "Choose a category"
    assert out["domain"] == "Choose a domain"
    stale = pr.file_notices({"category": "Unknown House category"})["category"]
    assert stale.startswith("Not saved. The House category you chose doesn't exist") and "Reload the page" in stale


def _txn_for_document(session, document, provider):
    t = Transaction(document_id=document.id, description=provider, category=Category.OTHER_EXPENSE,
                    amount=10.0, currency="EUR", transaction_type=TransactionType.DEBIT)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def test_the_dependent_refusal_text_from_the_financials_handler_is_written_out(session):
    document = Document(filename="bill.pdf", file_path="synthetic/bill.pdf", content_hash="hash-bill",
                        source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)
    first = _txn_for_document(session, document, "BILL A")
    second = _txn_for_document(session, document, "BILL B")
    second.linked_transaction_id = first.id
    session.add(second)
    session.commit()
    blocker = get_spec(Domain.FINANCIALS).handler.refile_blocker(session, document)
    assert blocker, "the financials handler should block re-filing a linked document"
    text = pr.notice(blocker, what="Not moved.")
    assert text.startswith("Not moved. ") and "linked to a commitment, debt or transfer" in text
    assert "Remove those links in Transactions first" in text


# --- House, Insurance and Documents: files and records, through the real forms --------------------

def test_house_upload_without_a_file_says_what_why_and_next(client):
    r = client.post("/house/upload", data={"category": "house_appliance", "item_name": "Boiler"})
    assert r.status_code == 400
    assert_three_parts(r.text, "Nothing was uploaded.", "No file was chosen", "Choose a file, then press Upload again.")


def test_house_upload_of_a_kind_the_category_does_not_take(client, session):
    r = _pdf(client, {"category": "house_appliance", "item_name": "Boiler"}, b"v", "demo.mp4", "video/mp4")
    assert r.status_code == 400
    assert_three_parts(r.text, "Not saved.", "House appliance takes only photo or PDF files, and this file is another kind.",
                       "Choose a file of one of those kinds, then try again.")
    assert "damaged" not in r.text


def test_insurance_upload_without_a_file_says_what_why_and_next(client):
    r = client.post("/insurance/upload", data={"category": "car_insurance"})
    assert r.status_code == 400
    assert_three_parts(r.text, "Nothing was uploaded.", "No file was chosen", "Choose a file, then press Upload again.")


def test_attaching_a_file_that_is_already_filed_elsewhere(client, session):
    _pdf(client, {"category": "warranty_invoice", "item_name": "Boiler"}, b"same-bytes", "w.pdf")
    record_id = _record_without_file(client)
    r = client.post(f"/house/records/{record_id}/attachment",
                    files={"file": ("w.pdf", io.BytesIO(b"same-bytes"), "application/pdf")})
    assert r.status_code == 400
    assert_three_parts(r.text, "Not filed.", "The Hub already has this exact file, filed under something else",
                       "Find it under its current place, or choose a different file.")


def test_attaching_a_second_file_to_a_record(client, session):
    record_id = _record_with_file(client, b"log-1")
    r = client.post(f"/house/records/{record_id}/attachment",
                    files={"file": ("second.pdf", io.BytesIO(b"log-2"), "application/pdf")})
    assert r.status_code == 400
    assert_three_parts(r.text, "Not attached.", "A record holds one file, and this one already has a file.",
                       "Open the record to see the file it has.")


def test_attaching_with_no_file_chosen(client, session):
    record_id = _record_without_file(client)
    r = client.post(f"/house/records/{record_id}/attachment")
    assert r.status_code == 400
    assert_three_parts(r.text, "Nothing was attached.", "No file was chosen", "Choose a file, then press Attach again.")


def test_moving_a_file_that_belongs_to_a_hand_entered_record(client, session):
    record_id = _record_with_file(client, b"log-3")
    document_id = session.get(Record, record_id).document_id
    r = client.post(f"/documents/{document_id}/edit", data={"domain": "house", "category": "house_appliance",
                                                             "item_name": "Boiler"})
    assert r.status_code == 409
    assert_three_parts(r.text, "Not moved.", "This file belongs to a record typed in by hand",
                       "Open the record and re-file it from there.")


def test_editing_the_fields_of_a_file_that_belongs_to_a_record(client, session):
    record_id = _record_with_file(client, b"log-4")
    document_id = session.get(Record, record_id).document_id
    r = client.post(f"/house/documents/{document_id}/fields", data={"item_name": "Boiler"}, headers=HTML)
    assert r.status_code == 409
    assert_three_parts(r.text, "Not saved.", "This file belongs to a record typed in by hand",
                       "Open the record and edit it there.")


# --- To-dos ---------------------------------------------------------------------------------------

def test_todo_with_a_bad_date_says_what_why_and_next(client):
    r = client.post("/todos/add", data={"title": "Check smoke alarms", "domain": "financials", "due_date": "10/10"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["what"] == "To-do not added."
    assert "isn't a real date in the form YYYY-MM-DD" in detail["why"]
    assert "add the to-do again" in detail["next_step"]
    assert "Invalid isoformat" not in r.text


def test_todo_with_an_empty_title_says_what_why_and_next(client):
    r = client.post("/todos/add", data={"title": " ", "domain": "financials"}, headers=HTML)
    assert r.status_code == 400
    assert_three_parts(r.text, "To-do not added.", "The title is empty", "Type a title and add the to-do again.")


# --- Wiki check --------------------------------------------------------------------------------------

def test_starting_a_wiki_check_while_one_is_running(client, session):
    start_run(session, "manual")  # the production path that marks a check as running
    r = client.post("/wiki/lint/run", follow_redirects=False)
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert_three_parts(page, "Not started.", "A check is already running, and only one can run at a time.",
                       "Wait for it to finish, then refresh this page.")


def test_adding_a_link_for_a_finding_that_is_not_a_missing_link(client, session):
    run = LintRun(trigger="manual")
    session.add(run)
    session.commit()
    session.refresh(run)
    finding = LintFinding(fingerprint="fp-orphan", kind=LintFindingKind.ORPHAN_PAGE, summary="x",
                          wiki_page_ids_json="[1]", first_seen_run_id=run.id, last_seen_run_id=run.id,
                          status=LintFindingStatus.OPEN)
    session.add(finding)
    session.commit()
    session.refresh(finding)
    r = client.post(f"/wiki/lint/findings/{finding.id}/add-link", headers=HTML)
    assert r.status_code == 400
    assert_three_parts(r.text, "Link not added.", "This finding isn't a missing-link finding",
                       "Use Dismiss or Fixed on it instead.")


# --- Needs Review: debt links and the unclassified card -----------------------------------------------

def test_link_debt_refusals_from_real_requests(client, session):
    r = client.post("/financials/transactions/99999/link-debt", data={"person_name": "Test Person"})
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/plain")
    assert r.text.startswith("Not linked. The transaction isn't in the Hub any more")
    assert r.text.endswith("Reload Needs Review and try again.")

    loan_txn = _txn(session, "TRF SEPA+ P/ TEST PERSON", 100.0)
    debt = create_informal_debt(session, "Test Person", DebtDirection.OWED_TO_US, opening_amount=1000.0)
    session.commit()
    r = client.post(f"/financials/transactions/{loan_txn.id}/link-debt",
                    data={"existing_debt_id": str(debt.id), "role": "adjust_up"})
    assert r.status_code == 400
    assert r.text == ("Not linked. The role has to be either an advance (more lent or borrowed) or a repayment. "
                      "Choose one of the two and press Link Debt again.")


def test_link_debt_refuses_a_payment_already_on_a_loan(client, session):
    t = _txn(session, "TRF SEPA+ P/ TEST A", 50.0)
    a = create_informal_debt(session, "Test A", DebtDirection.OWED_TO_US)
    session.commit()
    link_transaction_as_entry(session, a, t, None)
    session.commit()
    r = client.post(f"/financials/transactions/{t.id}/link-debt", data={"person_name": "Someone else"})
    assert r.status_code == 400
    assert r.text == ("Not linked. This transaction already belongs to a loan, and one transaction can only be on one loan. "
                      "Nothing to do here: reload Needs Review to see what is still waiting.")


def test_link_debt_refuses_a_name_that_is_too_long(client, session):
    t = _txn(session, "TRF SEPA+ P/ TEST D", 700.0)
    r = client.post(f"/financials/transactions/{t.id}/link-debt", data={"person_name": "x" * 81, "direction": "owed_to_us"})
    assert r.status_code == 400
    assert_three_parts(r.text, "Not linked.", "The person's name is longer than 80 characters.",
                       "Shorten the name and press Link Debt again.")


def test_needs_review_card_for_a_payment_no_merchant_could_match(client, session):
    _txn(session, "UNKNOWN SHOP 4411", 23.5)
    page = client.get("/financials/transactions/needs-review").text
    assert_three_parts(page, "Not filed yet:", "the Hub couldn't match this payment to a merchant",
                       "set its category with “Apply to selected”.")


# --- House item card when a file could not be read ----------------------------------------------------

def test_house_item_card_says_when_a_file_was_not_read(client, session, monkeypatch):
    async def fails(file_path, client=None):
        raise RuntimeError("Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error'}}")

    monkeypatch.setattr(house_handler, "extract_warranty_dates", fails)
    _pdf(client, {"category": "warranty_invoice", "item_name": "Boiler"}, b"unreadable")
    page = client.get("/house").text
    assert_three_parts(page, "Not read:", "the Hub couldn't process this file.", "Open it to see why and what to do.")


# --- Errors from the routes themselves (the central handler writes them in three parts) ---------------

@pytest.mark.parametrize("method,url,why", [
    ("post", "/financials/transactions/merchants/9999/dismiss-recurring", "no merchant with that number"),
    ("get", "/wiki/9999", "no wiki page with that number"),
    ("get", "/house/documents/9999", "no house document with that number"),
    ("get", "/insurance/documents/9999", "no insurance document with that number"),
    ("get", "/documents/9999/edit", "no document with that number"),
    ("post", "/wiki/lint/findings/9999/dismiss", "no finding with that number"),
    ("post", "/todos/9999/done", "no todo with that number"),
])
def test_missing_items_say_what_why_and_next(client, method, url, why):
    r = getattr(client, method)(url, headers=HTML)
    assert r.status_code == 404
    assert "Couldn" in r.text and why in r.text and "What you can do:" in r.text


def test_unknown_domain_on_a_to_do_says_what_why_and_next(client):
    r = client.post("/todos/add", data={"title": "x", "domain": "nope"}, headers=HTML)
    assert r.status_code == 400
    assert_three_parts(r.text, "Not done.", "The area you chose doesn't exist", "Reload the page and choose again.")


def test_bulk_apply_without_a_sub_category(client, session):
    ensure_taxonomy(session)
    r = client.post("/financials/transactions/bulk", data={"by": "merchant"}, headers=HTML)
    assert r.status_code == 400
    assert_three_parts(r.text, "Nothing was changed.", "No sub-category was chosen",
                       "Pick one from the list, then press the button again.")


def test_bulk_apply_without_merchants_ticked(client, session):
    ensure_taxonomy(session)
    r = client.post("/financials/transactions/bulk", data={"by": "merchant", "category_node": "food.groceries.supermarket"},
                    headers=HTML)
    assert r.status_code == 400
    assert_three_parts(r.text, "Nothing was changed.", "No merchants were ticked",
                       "Tick the ones you want, then try again.")


def test_bulk_edit_with_an_unknown_category(client, session):
    ensure_taxonomy(session)
    r = client.post("/financials/transactions/bulk-edit",
                    data={"transaction_ids": ["1"], "new_category_node": "made.up.slug"}, headers=HTML)
    assert r.status_code == 400
    assert_three_parts(r.text, "Not saved.", "The category you chose doesn't exist",
                       "Reload the page and choose again.")


def test_api_callers_still_get_the_original_detail_text(client):
    r = client.get("/wiki/9999")
    assert r.status_code == 404 and r.json() == {"detail": "Wiki page not found"}
