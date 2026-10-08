"""One-off: the House 'insurance_policy' documents (filed 2026-10-08 before the Insurance section
existed) become Home insurance documents of the Insurance domain. Field keys are renamed
(property -> insured, insurance_notes -> notes); everything else is kept."""

import json

from sqlalchemy import text
from sqlmodel import Session

RENAMED = {"property": "insured", "insurance_notes": "notes"}


def move_house_insurance_documents(session: Session) -> int:
    rows = session.execute(text(
        "SELECT id, fields_json FROM documents WHERE category = 'insurance_policy' AND domain = 'HOUSE'")).all()
    for doc_id, raw in rows:
        fields = json.loads(raw or "{}")
        moved = {RENAMED.get(k, k): v for k, v in fields.items()}
        session.execute(
            text("UPDATE documents SET domain = 'INSURANCE', category = 'home_insurance', fields_json = :f WHERE id = :i"),
            {"f": json.dumps(moved, ensure_ascii=False, sort_keys=True), "i": doc_id})
    session.commit()
    return len(rows)
