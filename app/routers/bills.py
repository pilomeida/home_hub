"""Routes for manual bill/statement upload and browsing."""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.db import get_session
from app.categories.fields import InvalidClassification
from app.categories.financials.feed import is_feed_statement
from app.categories.registry import document_url
from app.models.account import Account
from app.models.category_node import CategoryNode
from app.models.document import Document, DocumentSource
from app.models.category import Category
from app.models.transaction import Transaction
from app.services.ingestion import Classification, IncomingFile, ingest
from app.services.taxonomy import path_label
from app.services.todo_backlog import backlog_context
from app.templating import templates

router = APIRouter(prefix="/financials/bills", tags=["bills"])

@router.get("")
async def list_bills(request: Request, session: Session = Depends(get_session)):
    documents = session.exec(
        select(Document).where(Document.category == Category.FINANCIALS).order_by(Document.created_at.desc())
    ).all()
    from app.routers.bank import bank_notices
    return templates.TemplateResponse(
        request, "bills/list.html",
        {"documents": documents, "notices": bank_notices(session),
         **backlog_context(session, Category.FINANCIALS)},
    )

@router.get("/upload")
async def upload_form(request: Request, session: Session = Depends(get_session)):
    accounts = session.exec(select(Account)).all()
    return templates.TemplateResponse(request, "bills/upload.html", {"accounts": accounts})

@router.post("/upload")
async def upload_bill(
    request: Request, file: UploadFile,
    account_id: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    content = await file.read()
    classification = Classification(
        category=Category.FINANCIALS, paper_kind=None,
        fields={"account_id": account_id} if account_id else {},
    )
    try:
        result = await ingest(
            session,
            IncomingFile(file.filename, content, DocumentSource.MANUAL, getattr(request.state, "user_email", None)),
            classification,
        )
    except InvalidClassification as exc:
        raise HTTPException(status_code=400, detail={
            "what": "Not uploaded.", "why": f"The Hub can't accept this file: {exc}.",
            "next_step": "Correct what is listed and upload it again."}) from exc
    return RedirectResponse(document_url(result.document) or "/financials/bills", status_code=303)

@router.get("/{document_id}")
async def bill_detail(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = session.get(Document, document_id)
    if document is None:
        # "<thing> not found" is turned into what / why / what to do by the central handler (app/problems.py).
        raise HTTPException(status_code=404, detail="Document not found")
    # keeps held rows: a document's page lists every line read from it; adds up nothing
    transactions = session.exec(
        select(Transaction)
        .where(Transaction.document_id == document_id)
        .order_by(Transaction.paid_date, Transaction.id)
    ).all()
    # Each line's place on the spending tree (work-list B3); a line not filed yet has none.
    node_labels = {t.id: path_label(session, session.get(CategoryNode, t.category_id))
                   for t in transactions if t.category_id is not None}
    return templates.TemplateResponse(
        request, "bills/detail.html", {"document": document, "transactions": transactions, "node_labels": node_labels,
                                       "feed": is_feed_statement(document)}
    )
