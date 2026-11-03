"""Backups of the Hub's data: the database and the uploaded papers (finding F16, docs/architecture/findings.md).

Two kinds, both written under settings.BACKUP_DIR:
- nightly (the "backup" job on the Hub's scheduler): a copy of the database taken with SQLite's own backup
  (consistent while the Hub is running), checked with PRAGMA integrity_check, plus every uploaded paper copied
  into a mirror folder. Papers are never changed once stored, so only new ones are copied. NIGHTLY_KEEP copies
  of the database are kept.
- before a migration: `python -m app.jobs.backup before-migration`, run by the deploy right before
  `alembic upgrade head`, so a migration that goes wrong can be undone. PRE_MIGRATION_KEEP are kept.

A copy that fails its integrity check is deleted and reported, never kept as if it were good."""

from __future__ import annotations

import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session

from app.config import settings
from app.jobs.outcome import JobOutcome
from app.problems import Problem

NIGHTLY_KEEP = 30
PRE_MIGRATION_KEEP = 10


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class BackupReport:
    database_copy: Path
    papers_copied: int
    papers_total: int
    removed: int


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d_%H%M")


def copy_database(db_path: Path, target: Path) -> Path:
    """A consistent copy of the live database at `target`, checked before it is trusted."""
    if not db_path.is_file():
        raise BackupError(f"the database file {db_path} does not exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(partial)
        try:
            source.backup(copy)
            verdict = copy.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            copy.close()
    finally:
        source.close()
    if verdict != "ok":
        partial.unlink(missing_ok=True)
        raise BackupError(f"the copy failed its integrity check ({verdict})")
    partial.replace(target)
    return target


def mirror_papers(documents_dir: Path, mirror: Path) -> tuple[int, int]:
    """Copy every stored paper not yet in the mirror. Returns (copied now, total in the source)."""
    copied = total = 0
    if not documents_dir.is_dir():
        return 0, 0
    for path in documents_dir.rglob("*"):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        total += 1
        target = mirror / path.relative_to(documents_dir)
        if target.is_file() and target.stat().st_size == path.stat().st_size:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied, total


def prune(folder: Path, pattern: str, keep: int) -> int:
    """Keep the `keep` newest files matching `pattern` (names sort by date); delete the rest."""
    files = sorted(folder.glob(pattern))
    old = files[:-keep] if len(files) > keep else []
    for path in old:
        path.unlink()
    return len(old)


def nightly(db_path: Path, documents_dir: Path, backup_dir: Path, now: datetime) -> BackupReport:
    database_copy = copy_database(db_path, backup_dir / "database" / f"home_family_{_stamp(now)}.db")
    copied, total = mirror_papers(documents_dir, backup_dir / "papers")
    removed = prune(backup_dir / "database", "home_family_*.db", NIGHTLY_KEEP)
    return BackupReport(database_copy, copied, total, removed)


def before_migration(db_path: Path, backup_dir: Path, now: datetime) -> Path:
    folder = backup_dir / "before-migration"
    path = copy_database(db_path, folder / f"home_family_{_stamp(now)}.db")
    prune(folder, "home_family_*.db", PRE_MIGRATION_KEEP)
    return path


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


async def run_backup(session: Session, trigger: str) -> JobOutcome:
    try:
        report = nightly(settings.DATABASE_PATH, settings.DOCUMENTS_DIR, settings.BACKUP_DIR, datetime.now(timezone.utc))
    except (BackupError, OSError, sqlite3.Error) as exc:
        return JobOutcome("failed", Problem(
            "Tonight's backup wasn't made.",
            f"Copying the Hub's data failed ({exc}). The earlier copies are untouched.",
            "Press Run now on the Scheduled jobs page to try again; if it fails again, tell Pedro.").text)
    return JobOutcome("ok", f"Done: database copied and checked ({report.database_copy.name}); "
                            f"{_plural(report.papers_copied, 'new paper', 'new papers')} copied, "
                            f"{_plural(report.papers_total, 'paper', 'papers')} kept in all. "
                            f"The last {NIGHTLY_KEEP} nightly copies are kept.")


def main(argv: list[str]) -> int:
    if argv[1:] == ["before-migration"]:
        path = before_migration(settings.DATABASE_PATH, settings.BACKUP_DIR, datetime.now(timezone.utc))
        print(f"database copied before the migration: {path}")
        return 0
    print("usage: python -m app.jobs.backup before-migration", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
