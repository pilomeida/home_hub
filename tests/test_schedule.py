"""When a job is due (app/services/schedule.py), the scheduler that runs them, and the Scheduled jobs
page. Times: Lisbon is UTC+1 until 25 Oct 2026, so 04:30 Lisbon = 03:30 UTC."""

from datetime import datetime, timezone

import pytest
from sqlmodel import select

import app.routers.schedules  # noqa: F401  (import before any test patches the job list)
from app.jobs import registry, scheduler
from app.jobs.registry import JobDef, JobOutcome
from app.models.schedule import JobRun, JobSchedule, ProblemEntry
from app.services import schedule as s
from app.services.schedule import Spec


def utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_daily():
    spec = Spec("daily", time_of_day="04:30")
    last = utc(2026, 10, 14, 3, 30)
    assert s.is_due(spec, last, utc(2026, 10, 15, 3, 31))
    assert not s.is_due(spec, last, utc(2026, 10, 15, 3, 29))          # 04:29 Lisbon: today's slot not reached
    assert not s.is_due(spec, utc(2026, 10, 15, 3, 31), utc(2026, 10, 15, 20, 0))   # already ran today


def test_weekly_runs_once_after_the_slot_and_a_missed_week_runs_once_late():
    spec = Spec("weekly", weekday=6, time_of_day="04:30")             # Sunday
    last = utc(2026, 10, 11, 3, 30)
    assert not s.is_due(spec, last, utc(2026, 10, 15, 12, 0))          # a Thursday
    assert not s.is_due(spec, last, utc(2026, 10, 18, 3, 29))
    assert s.is_due(spec, last, utc(2026, 10, 18, 3, 31))
    missed = utc(2026, 10, 4, 3, 30)                                   # server was off on 11 Oct
    assert s.is_due(spec, missed, utc(2026, 10, 15, 12, 0))
    assert not s.is_due(spec, utc(2026, 10, 15, 12, 5), utc(2026, 10, 15, 18, 0))   # ...and only once


def test_every_monday_is_a_weekly_schedule():
    spec = Spec("weekly", weekday=0, time_of_day="09:00")
    assert s.is_due(spec, utc(2026, 10, 12, 8, 0), utc(2026, 10, 19, 8, 1))        # Mon 12 -> Mon 19 09:00 L
    assert not s.is_due(spec, utc(2026, 10, 12, 8, 0), utc(2026, 10, 19, 7, 59))


def test_every_n_days():
    spec = Spec("every_n_days", every_n=3, time_of_day="04:30")
    last = utc(2026, 10, 12, 3, 30)
    assert not s.is_due(spec, last, utc(2026, 10, 15, 3, 29))
    assert s.is_due(spec, last, utc(2026, 10, 15, 3, 31))


def test_every_n_weeks():
    spec = Spec("every_n_weeks", every_n=2, weekday=0, time_of_day="04:30")
    last = utc(2026, 10, 5, 3, 30)                                     # Monday
    assert not s.is_due(spec, last, utc(2026, 10, 18, 12, 0))
    assert s.is_due(spec, last, utc(2026, 10, 19, 4, 0))
    assert s.next_run(spec, last, utc(2026, 10, 10, 12, 0)) == datetime(2026, 10, 19, 4, 30, tzinfo=s.LISBON)


def test_monthly_clamps_to_short_months():
    spec = Spec("monthly", day_of_month=31, time_of_day="04:30")
    last = utc(2027, 1, 31, 4, 30)
    assert not s.is_due(spec, last, utc(2027, 2, 27, 12, 0))
    assert s.is_due(spec, last, utc(2027, 2, 28, 5, 0))                # 28 Feb is the 31st's stand-in
    assert s.next_run(spec, last, utc(2027, 2, 1)) == datetime(2027, 2, 28, 4, 30, tzinfo=s.LISBON)


def test_off_is_never_due_and_has_no_next_run():
    spec = Spec("daily", enabled=False)
    assert not s.is_due(spec, None, utc(2026, 10, 15)) and s.next_run(spec, None, utc(2026, 10, 15)) is None
    assert s.describe(spec) == "Off"


def test_never_run_is_due_once():
    assert s.is_due(Spec("weekly"), None, utc(2026, 10, 15))


def test_next_run_for_display():
    spec = Spec("weekly", weekday=6, time_of_day="04:30")
    assert s.next_run(spec, utc(2026, 10, 11, 3, 30), utc(2026, 10, 15, 12)) == datetime(2026, 10, 18, 4, 30, tzinfo=s.LISBON)


def test_describe_in_plain_words():
    assert s.describe(Spec("daily", time_of_day="06:00")) == "Every day at 06:00"
    assert s.describe(Spec("weekly", weekday=0, time_of_day="09:00")) == "Every Monday at 09:00"
    assert s.describe(Spec("every_n_days", every_n=5)) == "Every 5 days at 04:30"
    assert s.describe(Spec("every_n_weeks", every_n=2, weekday=4, time_of_day="18:00")) == "Every 2 weeks on Friday at 18:00"
    assert s.describe(Spec("monthly", day_of_month=1)) == "Every month on day 1 at 04:30"


@pytest.mark.parametrize("bad", [Spec("hourly"), Spec("daily", time_of_day="25:00"), Spec("daily", time_of_day="soon"),
                                 Spec("every_n_days", every_n=0), Spec("weekly", weekday=9), Spec("monthly", day_of_month=40)])
def test_invalid_schedules_are_refused_with_a_reason(bad):
    with pytest.raises(s.InvalidSchedule) as e:
        s.validate(bad)
    assert len(str(e.value)) > 15


# --- the scheduler ---------------------------------------------------------------------------

@pytest.fixture()
def fake_job(monkeypatch):
    calls = []
    result = {"outcome": JobOutcome("ok", "Done: nothing to report.")}

    async def run(session, trigger):
        calls.append(trigger)
        if isinstance(result["outcome"], Exception):
            raise result["outcome"]
        return result["outcome"]

    job = JobDef("fake", "Fake job", "A test job.", Spec("daily", time_of_day="04:30"), run)
    monkeypatch.setattr(registry, "JOBS", {"fake": job})
    monkeypatch.setattr(scheduler, "JOBS", {"fake": job})
    monkeypatch.setattr("app.routers.schedules.JOBS", {"fake": job})
    return calls, result


@pytest.mark.asyncio
async def test_due_job_runs_once_and_is_recorded(session, fake_job):
    calls, _ = fake_job
    ran = await scheduler.run_due(session, utc(2026, 10, 15, 6, 0))
    assert [r.outcome for r in ran] == ["ok"] and calls == ["schedule"]
    again = await scheduler.run_due(session, utc(2026, 10, 15, 7, 0))
    assert again == [] and calls == ["schedule"]                        # already ran today


@pytest.mark.asyncio
async def test_a_manual_run_does_not_move_the_cycle(session, fake_job):
    calls, _ = fake_job
    await scheduler.run_job(session, "fake", "manual")
    ran = await scheduler.run_due(session, utc(2026, 10, 15, 6, 0))
    assert len(ran) == 1 and calls == ["manual", "schedule"]


@pytest.mark.asyncio
async def test_a_saved_schedule_replaces_the_default(session, fake_job):
    session.add(JobSchedule(job="fake", kind="weekly", weekday=6, time_of_day="04:30")); session.commit()   # Sundays only
    session.add(JobRun(job="fake", trigger="schedule", started_at=datetime(2026, 10, 11, 3, 30), outcome="ok")); session.commit()
    assert await scheduler.run_due(session, utc(2026, 10, 15, 6, 0)) == []                       # a Thursday: not due
    assert len(await scheduler.run_due(session, utc(2026, 10, 18, 3, 31))) == 1


@pytest.mark.asyncio
async def test_a_switched_off_job_never_runs(session, fake_job):
    session.add(JobSchedule(job="fake", kind="daily", enabled=False)); session.commit()
    assert await scheduler.run_due(session, utc(2026, 10, 15, 6, 0)) == []


@pytest.mark.asyncio
async def test_a_crash_becomes_a_three_part_message_and_a_problem_never_an_exception(session, fake_job):
    _, result = fake_job
    result["outcome"] = RuntimeError("boom")
    run = await scheduler.run_job(session, "fake", "manual")
    assert run.outcome == "failed" and run.summary.startswith("Not done.") and "unexpected error" in run.summary
    problem = session.exec(select(ProblemEntry)).one()
    assert problem.area == "Scheduled jobs" and problem.what.startswith("Fake job") and problem.link == "/schedules"


@pytest.mark.asyncio
async def test_a_problem_clears_itself_when_the_job_works_again(session, fake_job):
    _, result = fake_job
    result["outcome"] = JobOutcome("failed", "Not done. Because.")
    await scheduler.run_job(session, "fake", "manual")
    assert session.exec(select(ProblemEntry)).one().resolved_at is None
    result["outcome"] = JobOutcome("ok", "Done.")
    await scheduler.run_job(session, "fake", "manual")
    assert session.exec(select(ProblemEntry)).one().resolved_at is not None


@pytest.mark.asyncio
async def test_two_runs_at_once_are_refused_with_a_reason(session, fake_job):
    session.add(JobRun(job="fake", trigger="schedule", outcome="running")); session.commit()
    run = await scheduler.run_job(session, "fake", "manual")
    assert run.outcome == "skipped" and "already running" in run.summary and run.summary.startswith("Not started.")


@pytest.mark.asyncio
async def test_a_job_that_ran_under_the_old_fixed_timer_is_not_rerun_at_once(session, monkeypatch):
    seen = []

    async def run(sess, trigger):
        seen.append(trigger); return JobOutcome("ok", "x")
    job = JobDef("legacy", "Legacy", "d", Spec("weekly", weekday=6, time_of_day="04:30"), run,
                 legacy_last_start=lambda sess: datetime(2026, 10, 11, 3, 30))
    monkeypatch.setattr(scheduler, "JOBS", {"legacy": job})
    assert await scheduler.run_due(session, utc(2026, 10, 15, 6, 0)) == [] and seen == []


# --- the real wiki job's wiring --------------------------------------------------------------

def test_the_wiki_check_is_registered_with_a_weekly_default():
    job = registry.JOBS["wiki_lint"] if "wiki_lint" in registry.JOBS else None
    assert job is not None and job.default.kind == "weekly" and job.default.weekday == 6


# --- the page --------------------------------------------------------------------------------

def test_page_lists_every_job_with_its_plain_schedule_and_no_fixed_timers_section(client):
    page = client.get("/schedules").text
    assert "Wiki check" in page and "Every Sunday at 04:30" in page and "the usual setting" in page
    assert "Bank sync" in page and "Every day at 07:30, 13:00 and 18:30" in page
    assert "Mailbox check" in page and "Every 10 minutes" in page
    assert "Statement reminder" in page and "Every day at 09:00" in page
    assert "Still on fixed timers" not in page and "can't be changed here yet" not in page   # nothing left on fixed timers


def test_page_shows_the_fixed_timers_section_only_when_something_is_still_on_one(client, monkeypatch):
    monkeypatch.setattr("app.routers.schedules.FIXED_JOBS", [("Old job", "Every hour", "Does a thing.")])
    page = client.get("/schedules").text
    assert "Still on fixed timers" in page and "Old job" in page


def test_saving_a_schedule_stores_it_and_shows_it(client, session):
    client.post("/schedules/wiki_lint", data={"kind": "every_n_weeks", "every_n": "2", "weekday": "0",
                                              "time_of_day": "06:15", "enabled": "1"})
    row = session.get(JobSchedule, "wiki_lint")
    assert (row.kind, row.every_n, row.weekday, row.time_of_day, row.enabled) == ("every_n_weeks", 2, 0, "06:15", True)
    assert "Every 2 weeks on Monday at 06:15" in client.get("/schedules").text


def test_switching_a_job_off(client, session):
    client.post("/schedules/wiki_lint", data={"kind": "weekly", "weekday": "6", "time_of_day": "04:30"})   # no 'enabled'
    assert session.get(JobSchedule, "wiki_lint").enabled is False
    assert "Off" in client.get("/schedules").text


@pytest.mark.parametrize("data,expected", [
    ({"kind": "daily", "time_of_day": "9pm"}, "Enter the time as HH:MM"),
    ({"kind": "every_n_days", "every_n": "0", "time_of_day": "09:00"}, "between 1 and 365"),
    ({"kind": "monthly", "day_of_month": "x", "time_of_day": "09:00"}, "Enter whole numbers"),
])
def test_a_bad_schedule_is_not_saved_and_says_why(client, session, data, expected):
    r = client.post("/schedules/wiki_lint", data=data, follow_redirects=True)
    assert "Not saved." in r.text and expected in r.text and "left as it was" in r.text
    assert session.get(JobSchedule, "wiki_lint") is None


def test_run_now_starts_the_job_and_records_it(client, session, fake_job):
    client.post("/schedules/fake/run")
    run = session.exec(select(JobRun)).one()
    assert (run.job, run.trigger, run.outcome) == ("fake", "manual", "ok")


# --- the real entry point (a bug the unit tests missed: results were read after the session closed) ---

@pytest.mark.asyncio
async def test_main_runs_due_jobs_and_reports_them_after_its_session_closes(engine, monkeypatch, fake_job, caplog):
    import logging
    monkeypatch.setattr("app.db.engine", engine)           # main() opens its own session on the real engine
    with caplog.at_level(logging.INFO, logger="scheduler"):
        code = await scheduler.main()
    assert code == 0
    assert any("fake: ok" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_main_exits_1_when_a_job_failed_but_still_does_not_crash(engine, monkeypatch, fake_job):
    monkeypatch.setattr("app.db.engine", engine)
    _, result = fake_job
    result["outcome"] = JobOutcome("failed", "Not done. Because.")
    assert await scheduler.main() == 1
