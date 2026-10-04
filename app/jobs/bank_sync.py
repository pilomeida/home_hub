"""Fetch new bank transactions, run by the systemd user timer
deploy/systemd/home-hub-banksync.timer. Usage: python -m app.jobs.bank_sync
[--dry-run]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlmodel import Session, select

from app.config import settings
from app.db import engine
from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus
from app.services.bankapi.client import EnableBankingClient
from app.services.bankapi.sync import sync_link

logger = logging.getLogger("bank_sync")


async def run_all(session: Session, client, *, dry_run: bool = False,
                  scheduled: bool = True) -> dict:
    """Sync every link whose connection is ACTIVE and has an account mapped."""
    results = {}
    links = session.exec(
        select(BankAccountLink).join(BankConnection).where(  # type: ignore[call-arg]
            BankConnection.status == BankConnectionStatus.ACTIVE,
            BankAccountLink.account_id.is_not(None),  # type: ignore[attr-defined]
        )
    ).all()
    for link in links:
        logger.info("Syncing bank account link %s (%s)", link.id, link.bank_account_uid)
        result = await sync_link(session, client, link, dry_run=dry_run,
                                 scheduled=scheduled)
        if result.skipped_quota:
            logger.info("Link %s: skipped, daily call budget used", link.id)
        elif result.error:
            logger.warning("Link %s: %s", link.id, result.error)
        else:
            logger.info(
                "Link %s: %d new, %d already there, %d matched statements",
                link.id, result.created, result.already_synced, result.matched_existing,
            )
        results[link.id] = result
    return results


async def _main(dry_run: bool) -> int:
    logging.basicConfig(level=logging.INFO)
    if not settings.bank_configured:
        logger.info("bank sync not configured; nothing to do")
        return 0
    client = EnableBankingClient(settings.ENABLE_BANKING_APP_ID,
                                 open(settings.ENABLE_BANKING_KEY_PATH, "rb").read())
    with Session(engine) as session:
        results = await run_all(session, client, dry_run=dry_run, scheduled=True)
    errored = [r for r in results.values() if r.error]
    if results and len(errored) == len(results):
        logger.warning("every bank link failed")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch new bank transactions")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and report, but save nothing")
    args = parser.parse_args()
    return asyncio.run(_main(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
