"""Routes for manual bill/statement upload and browsing."""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models.account import Account
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.transaction import Transaction
from app.services.dedup import find_existing_document_by_hash
from app.services.pipeline import ingest_document
from app.services.storage import save_upload

router = APIRouter(prefix="/bills", tags=["bills"])
templates = Jinja2Templates(directory="app/templates")


@router.get("")
async def list_bills(request: Request, session: Session = Depends(get_session)):
    documents = session.exec(select(Document).order_by(Document.created_at.desc())).all()
    return templates.TemplateResponse(request, "bills/list.html", {"documents": documents})


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
    file_path, content_hash = save_upload(file.filename, content)

    existing = find_existing_document_by_hash(session, content_hash)
    if existing is not None:
        return RedirectResponse(f"/bills/{existing.id}", status_code=303)

    document = Document(
        filename=file.filename, file_path=file_path, content_hash=content_hash,
        source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
        uploaded_by=getattr(request.state, "user_email", None),
        account_id=int(account_id) if account_id else None,
        domain=Domain.FINANCIALS,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    document = await ingest_document(session, document)
    return RedirectResponse(f"/bills/{document.id}", status_code=303)


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
