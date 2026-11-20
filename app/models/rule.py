"""Rule: one memory of past answers (PLAN step 2, work-list C2/C3).

A rule says: when a paper or a bank line shows THIS (what it looks at, how it matches, the pattern), the answer is
THAT (a merchant, a spending leaf, a kind of paper, a subject). Every classifier asks the rules first, through one
reader (app/services/rules.py), in one order: a person's rule, then the AI's, then the system's; then priority.

Sources: `person` (a person decided, e.g. confirmed a merchant or ticked "remember" in the Inbox), `ai` (the AI
suggested it; below the confidence bar it is saved switched off and waits for a person), `system` (shipped with the
code, e.g. Pedro's supermarket ruling). Adapted from maciejb2k/open-banking-rails `merchant_rule.rb` (MIT)."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.strict import RefusesUnknownFields

# What a rule looks at.
DESCRIPTION, COUNTERPARTY, IBAN, ENTITY, SENDER, WORDS, FILENAME = (
    "description", "counterparty", "iban", "entity", "sender", "words", "filename")
LOOKS_AT = (DESCRIPTION, COUNTERPARTY, IBAN, ENTITY, SENDER, WORDS, FILENAME)
# How it matches. `key`: the text's provider key (lowercase, accents and digit runs folded) equals the pattern.
CONTAINS, EXACT, REGEX, PREFIX, KEY = "contains", "exact", "regex", "prefix", "key"
HOW = (CONTAINS, EXACT, REGEX, PREFIX, KEY)
PERSON, AI, SYSTEM = "person", "ai", "system"
SOURCES = (PERSON, AI, SYSTEM)


class Rule(RefusesUnknownFields, SQLModel, table=True):
    __tablename__ = "rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    looks_at: str = Field(index=True)
    how: str
    pattern: str = Field(index=True)
    # The answer: any of these, as the rule's purpose needs.
    merchant: Optional[str] = None
    # A payee rule (PLAN step 9 D8) whose payee fixes the merchant: every line it matches is the one merchant with
    # this key (`entity:27134`). Empty for every other rule, a supermarket chain included.
    merchant_key: Optional[str] = None
    node_id: Optional[int] = Field(default=None, foreign_key="category_nodes.id")   # spending leaf
    category: Optional[str] = None                                                   # kind of paper: category ...
    paper_kind: Optional[str] = None                                                 # ... and kind
    subject_id: Optional[int] = Field(default=None, foreign_key="subjects.id")
    priority: int = 0
    source: str = PERSON
    confidence: Optional[float] = None
    enabled: bool = True
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    example: Optional[str] = None          # one real text it was made from, for display
    note: Optional[str] = None             # where it came from, in plain words
    hits: int = 0
    last_hit_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
