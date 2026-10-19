"""Read filed health papers again with the current reader: the same steps as the "Read again" button, run from the server.
Run on the VPS, from the app folder, as the app's user:
    python scripts/reread_health.py DOCUMENT_ID [DOCUMENT_ID ...]
What was read from each paper is taken back and redone; fields a person typed are lost (as with the button).
The reading goes out at house_high, like every health read."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.routers import health as health_router  # noqa: E402


async def main(ids: list[int]) -> int:
    failed = 0
    for document_id in ids:
        try:
            with Session(engine) as session:
                document = health_router._document_or_404(session, document_id)
                await health_router.prepare_reread(session, document)
        except HTTPException as exc:
            d = exc.detail if isinstance(exc.detail, dict) else {"what": "", "why": str(exc.detail), "next_step": ""}
            print(f"{document_id}: not read again - {d.get('what', '')} {d.get('why', '')} {d.get('next_step', '')}".rstrip())
            failed += 1
            continue
        await health_router._read_in_background(lambda: Session(engine), document_id)
        with Session(engine) as session:
            document = session.get(Document, document_id)
            print(f"{document_id}: {document.status.value} {document.failure_reason or ''}".rstrip())
            failed += document.status.value != "processed"
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main([int(a) for a in sys.argv[1:]])))
