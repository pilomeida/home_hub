"""Routes for manual bill/statement upload and browsing."""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.db import get_session
from app.domains.fields import InvalidClassification
from app.domains.registry import document_url
from app.models.account import Account
from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.models.transaction import Transaction
from app.services.ingestion import Classification, IncomingFile, ingest
from app.services.todo_backlog import backlog_context
from app.templating import templates

router = APIRouter(prefix="/financials/bills", tags=["bills"])

@router.get("")
async def list_bills(request: Request, session: Session = Depends(get_session)):
    documents = session.exec(
        select(Document).where(Document.domain == Domain.FINANCIALS).order_by(Document.created_at.desc())
    ).all()
    return templates.TemplateResponse(
        request, "bills/list.html", {"documents": documents, **backlog_context(session, Domain.FINANCIALS)}
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
        domain=Domain.FINANCIALS, category=None,
        fields={"account_id": account_id} if account_id else {},
    )
    try:
        result = await ingest(
            session,
            IncomingFile(file.filename, content, DocumentSource.MANUAL, getattr(request.state, "user_email", None)),
            classification,
        )
    except InvalidClassification as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(document_url(result.document) or "/financials/bills", status_code=303)

@router.get("/{document_id}")
async def bill_detail(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    transactions = session.exec(
        select(Transaction)
        .where(Transaction.document_id == document_id)
        .order_by(Transaction.paid_date, Transaction.id)
    ).all()
    return templates.TemplateResponse(
        request, "bills/detail.html", {"document": document, "transactions": transactions}
    )
