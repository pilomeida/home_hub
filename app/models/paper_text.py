"""The text of a filed paper (PLAN step 1a): what search over papers reads.

One row per document. `method` says how the text was got: `pdf_text` (the PDF's own text layer, at filing), `ai_read`
(the AI read the page because there was no text layer, at the paper's own level, by the "Paper text" job), `none`
(a file the AI cannot read, such as a video or a spreadsheet: nothing to search but its title and details) or
`failed` (the AI read failed; retried by the job up to MAX_ATTEMPTS)."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.strict import RefusesUnknownFields

PDF_TEXT, AI_READ, NO_TEXT, FAILED = "pdf_text", "ai_read", "none", "failed"
MAX_ATTEMPTS = 3


class PaperText(RefusesUnknownFields, SQLModel, table=True):
    __tablename__ = "paper_texts"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", unique=True, index=True)
    text: str = ""
    method: str
    attempts: int = 0
    error: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
