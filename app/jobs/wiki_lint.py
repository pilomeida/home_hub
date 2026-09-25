"""Weekly Wiki Lint pass, run by the systemd user timer
deploy/systemd/home-hub-wikilint.timer. Usage: python -m app.jobs.wiki_lint"""

import asyncio
import logging
import sys

from sqlmodel import Session

from app.db import engine
from app.models.wiki_lint import LintRunStatus
from app.services.wiki_lint.runner import LintAlreadyRunning, run_lint

logger = logging.getLogger("wiki_lint")


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    with Session(engine) as session:
        try:
            run = await run_lint(session, trigger="timer")
        except LintAlreadyRunning as exc:
            logger.info("Skipped: %s", exc)
            return 0
    logger.info("Lint run finished: %s", run.status.value)
    return 1 if run.status == LintRunStatus.FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
