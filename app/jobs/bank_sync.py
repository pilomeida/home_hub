"""Fetch new bank transactions. Scheduled on the Hub (job "Bank sync" on /schedules, run by
app.jobs.scheduler through app/jobs/runners.py, which calls run_all below); this module's
main() is for running it by hand. Usage: python -m app.jobs.bank_sync [--dry-run]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app.config import settings
from app.db import engine
from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus
from app.problems import record_problem, resolve_for
from app.services.bankapi import messages as msg
from app.services.bankapi.client import EnableBankingClient
from app.services.bankapi.connect import consent_ended, consent_warning, latest_connections
from app.services.bankapi.sync import SyncResult, sync_link
from app.services.bankapi.topups import link_revolut_topups

logger = logging.getLogger("bank_sync")


def check_consents(session: Session, now: Optional[datetime] = None) -> None:
    """Keep the Problems log truthful about each bank's permission: an expired or soon-to-expire one is
    logged (nobody may be looking at the bank page), and an entry clears itself once it is renewed."""
    for conn in latest_connections(session).values():
        if conn.status == BankConnectionStatus.EXPIRED:
            record_problem(session, area=msg.AREA, problem=msg.consent_expired(conn.bank_name),
                           ref=msg.connection_ref(conn.id))
            resolve_for(session, msg.expiring_ref(conn.id))
        elif conn.status == BankConnectionStatus.ACTIVE:
            warning = consent_warning(conn, now)
            if warning is None:
                resolve_for(session, msg.expiring_ref(conn.id))
                resolve_for(session, msg.connection_ref(conn.id))
            elif not consent_ended(conn, now):
                record_problem(session, area=msg.AREA, problem=warning, ref=msg.expiring_ref(conn.id))
            # Past its end time but still marked active: the sync below finds out from the bank and
            # records the expiry itself, so this check doesn't guess.


def note_setup(session: Session, configured: bool) -> None:
    """Without the bank keys nothing syncs. That is only a problem once a bank is actually connected."""
    ref = "bank-sync:setup"
    connected = session.exec(select(BankConnection).where(
        BankConnection.status == BankConnectionStatus.ACTIVE)).first() is not None
    if not configured and connected:
        record_problem(session, area=msg.AREA, problem=msg.sync_setup_missing(), ref=ref,
                       technical="ENABLE_BANKING_APP_ID / ENABLE_BANKING_KEY_PATH not set")
    else:
        resolve_for(session, ref)


async def run_all(session: Session, client, *, dry_run: bool = False,
                  scheduled: bool = True) -> dict:
    """Sync every link whose connection is ACTIVE and has an account mapped."""
    results = {}
    if not dry_run:
        check_consents(session)
    links = session.exec(
        select(BankAccountLink).join(BankConnection).where(  # type: ignore[call-arg]
            BankConnection.status == BankConnectionStatus.ACTIVE,
            BankAccountLink.account_id.is_not(None),  # type: ignore[attr-defined]
        )
    ).all()
    for link in links:
        logger.info("Syncing bank account link %s (%s)", link.id, link.bank_account_uid)
        try:
            result = await sync_link(session, client, link, dry_run=dry_run,
                                     scheduled=scheduled)
        except Exception:
            # One broken account must not stop the others in the same run.
            logger.exception("Unexpected failure syncing link %s", link.id)
            session.rollback()
            problem = msg.unexpected_failure(msg.label(_bank_name(session, link), link.display_name))
            if not dry_run:
                record_problem(session, area=msg.AREA, problem=problem, ref=msg.link_ref(link.id),
                               technical=traceback.format_exc())
            result = SyncResult(error=problem.text, problem=problem)
            results[link.id] = result
            continue
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


def _bank_name(session: Session, link: BankAccountLink) -> str:
    conn = session.get(BankConnection, link.connection_id)
    return conn.bank_name if conn else "The bank"


async def _main(dry_run: bool) -> int:
    logging.basicConfig(level=logging.INFO)
    if not settings.bank_configured:
        logger.info("bank sync not configured; nothing to do")
        if not dry_run:
            try:
                with Session(engine) as session:
                    note_setup(session, False)
            except Exception:
                logger.exception("could not record that bank sync is not configured")
        return 0
    with Session(engine) as session:
        if not dry_run:
            note_setup(session, True)
        try:
            client = EnableBankingClient(settings.ENABLE_BANKING_APP_ID,
                                         Path(settings.ENABLE_BANKING_KEY_PATH).read_bytes())
        except OSError as exc:      # the key file is missing or unreadable
            logger.exception("cannot read the bank key file")
            if not dry_run:
                record_problem(session, area=msg.AREA, problem=msg.sync_setup_missing(), ref="bank-sync:setup",
                               technical=f"{type(exc).__name__}: {exc}")
            return 1
        results = await run_all(session, client, dry_run=dry_run, scheduled=True)
        if not dry_run:
            try:
                link_revolut_topups(session)
                resolve_for(session, "bank-topups")
            except Exception:
                logger.exception("Linking Revolut top-ups failed")
                session.rollback()
                record_problem(session, area=msg.AREA, problem=msg.topups_not_linked(), ref="bank-topups",
                               technical=traceback.format_exc())
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
