"""Backups (finding F16): the nightly job and the copy taken before a migration. Real SQLite files and real
paper files in a temporary folder; nothing is faked."""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.jobs import backup, registry
from app.services.schedule import Spec

NOW = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)


def _database(path: Path, rows: int = 3) -> Path:
    con = sqlite3.connect(path)
    con.execute("create table transactions (id integer primary key, amount real)")
    con.executemany("insert into transactions (amount) values (?)", [(float(i),) for i in range(rows)])
    con.commit()
    con.close()
    return path


def _papers(folder: Path) -> Path:
    (folder / "2026").mkdir(parents=True)
    (folder / "2026" / "bill.pdf").write_bytes(b"%PDF-1.4 bill")
    (folder / "scan.jpg").write_bytes(b"\xff\xd8 photo")
    (folder / ".gitkeep").write_text("")
    return folder


def test_the_nightly_copy_is_a_working_database_and_every_paper_is_mirrored(tmp_path):
    db = _database(tmp_path / "home_family.db")
    report = backup.nightly(db, _papers(tmp_path / "documents"), tmp_path / "backups", NOW)

    assert report.database_copy == tmp_path / "backups" / "database" / "home_family_2026-11-03_0300.db"
    con = sqlite3.connect(report.database_copy)
    assert con.execute("select count(*) from transactions").fetchone()[0] == 3
    con.close()
    assert (tmp_path / "backups" / "papers" / "2026" / "bill.pdf").read_bytes() == b"%PDF-1.4 bill"
    assert (report.papers_copied, report.papers_total) == (2, 2)          # .gitkeep is not a paper


def test_papers_already_backed_up_are_not_copied_again(tmp_path):
    db, docs = _database(tmp_path / "home_family.db"), _papers(tmp_path / "documents")
    backup.nightly(db, docs, tmp_path / "backups", NOW)
    (docs / "new.pdf").write_bytes(b"%PDF new")
    report = backup.nightly(db, docs, tmp_path / "backups", NOW + timedelta(days=1))
    assert (report.papers_copied, report.papers_total) == (1, 3)


def test_only_the_newest_nightly_copies_are_kept(tmp_path):
    db, docs = _database(tmp_path / "home_family.db"), _papers(tmp_path / "documents")
    for day in range(backup.NIGHTLY_KEEP + 2):
        backup.nightly(db, docs, tmp_path / "backups", NOW + timedelta(days=day))
    kept = sorted((tmp_path / "backups" / "database").glob("*.db"))
    assert len(kept) == backup.NIGHTLY_KEEP
    assert kept[0].name == "home_family_2026-11-05_0300.db"              # the two oldest went


def test_a_damaged_database_is_reported_and_no_copy_is_kept(tmp_path):
    bad = tmp_path / "home_family.db"
    bad.write_bytes(b"this is not a database at all" * 100)
    with pytest.raises((backup.BackupError, sqlite3.DatabaseError)):
        backup.copy_database(bad, tmp_path / "backups" / "copy.db")
    assert not list((tmp_path / "backups").glob("*.db"))


def test_a_missing_database_is_a_plain_failure_on_the_jobs_page(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "nowhere.db")
    monkeypatch.setattr(settings, "BACKUP_DIR", tmp_path / "backups")
    outcome = asyncio.run(backup.run_backup(None, "schedule"))
    assert outcome.outcome == "failed"
    assert outcome.summary.startswith("Tonight's backup wasn't made.")
    assert "Run now" in outcome.summary and "nowhere.db" in outcome.summary


def test_the_job_reports_what_it_did(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", _database(tmp_path / "home_family.db"))
    monkeypatch.setattr(settings, "DOCUMENTS_DIR", _papers(tmp_path / "documents"))
    monkeypatch.setattr(settings, "BACKUP_DIR", tmp_path / "backups")
    outcome = asyncio.run(backup.run_backup(None, "schedule"))
    assert outcome.outcome == "ok"
    assert "2 new papers copied" in outcome.summary and "2 papers kept in all" in outcome.summary


def test_the_copy_before_a_migration_is_kept_apart_and_limited(tmp_path, monkeypatch, capsys):
    db = _database(tmp_path / "home_family.db")
    for minute in range(backup.PRE_MIGRATION_KEEP + 1):
        backup.before_migration(db, tmp_path / "backups", NOW + timedelta(minutes=minute))
    assert len(list((tmp_path / "backups" / "before-migration").glob("*.db"))) == backup.PRE_MIGRATION_KEEP
    assert not (tmp_path / "backups" / "database").exists()             # never counted against the nightly copies

    monkeypatch.setattr(settings, "DATABASE_PATH", db)
    monkeypatch.setattr(settings, "BACKUP_DIR", tmp_path / "other")
    assert backup.main(["backup", "before-migration"]) == 0
    assert "copied before the migration" in capsys.readouterr().out
    assert backup.main(["backup"]) == 2


def test_the_backup_runs_every_night_on_the_hubs_scheduler():
    job = registry.JOBS["backup"]
    assert job.default == Spec(kind="daily", time_of_day="03:00")
    assert job.run is backup.run_backup


def test_the_deploy_backs_up_before_it_migrates():
    workflow = Path(".github/workflows/deploy.yml").read_text()
    assert workflow.index("python -m app.jobs.backup before-migration") < workflow.index("alembic upgrade head")
