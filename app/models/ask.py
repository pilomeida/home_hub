"""Ask chat: an AskConversation holds ordered AskTurns (one question and
its answer each). A turn is also the handle for "Save to wiki"."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class AskStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    FAILED = "failed"


class AskConversation(SQLModel, table=True):
    __tablename__ = "ask_conversations"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str                                   # the first question, truncated
    started_by: Optional[str] = None             # CF Access email
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AskTurn(SQLModel, table=True):
    __tablename__ = "ask_turns"
    __table_args__ = (UniqueConstraint("conversation_id", "position", name="uq_ask_turns_conversation_position"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="ask_conversations.id", index=True)
    position: int                                # 1-based order within the conversation
    question: str
    status: AskStatus = Field(default=AskStatus.PENDING)
    answer_text: Optional[str] = None            # raw model text incl. [[ref]] markers
    citations_json: str = Field(default="[]")    # [{"ref","label","url"}] cited by THIS turn
    used_raw_sources: bool = Field(default=False)
    asked_by: Optional[str] = None
    error: Optional[str] = None
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    saved_wiki_page_id: Optional[int] = Field(default=None, foreign_key="wiki_pages.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    answered_at: Optional[datetime] = None
