"""The two schedule kinds added for bank sync and the mailbox check: times_per_day (several fixed Lisbon
times a day) and every_n_minutes, the daily-limit guard, and how the scheduler and the /schedules page
treat them. Lisbon is UTC+1 (summer time) until 02:00 on Sun 25 Oct 2026, then UTC+0; it goes forward
again at 01:00 UTC on Sun 28 Mar 2027."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

import app.routers.schedules  # noqa: F401  (import before any test patches the job list)
from app.jobs import registry, scheduler
from app.jobs.registry import JobDef, JobOutcome
from app.models.schedule import JobRun, JobSchedule
from app.services import schedule as s
from app.services.schedule import Spec

BANK = Spec("times_per_day", time_of_day="07:30,13:00,18:30")
BANK_LIMIT = dict(max_runs_per_day=4, limit_reason="the bank allows only 4 requests per account per day", job_label="Bank sync")


def utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def lisbon(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=s.LISBON)


# --- times_per_day ------------------------------------------------------------------------------

def test_times_per_day_runs_at_each_time_and_waits_between():
    last = utc(2026, 10, 15, 6, 30)                                    # the 07:30 Lisbon run
    assert not s.is_due(BANK, last, utc(2026, 10, 15, 11, 59))         # 12:59 Lisbon
    assert s.is_due(BANK, last, utc(2026, 10, 15, 12, 0))              # 13:00 Lisbon
    ran = utc(2026, 10, 15, 12, 0) + timedelta(seconds=3)
    assert not s.is_due(BANK, ran, utc(2026, 10, 15, 17, 29))          # 18:29
    assert s.is_due(BANK, ran, utc(2026, 10, 15, 17, 30))              # 18:30
    # after the last time of the day, the next one is tomorrow's first
    assert s.next_run(BANK, utc(2026, 10, 15, 17, 30), utc(2026, 10, 15, 22, 0)) == lisbon(2026, 10, 16, 7, 30)
    # before the first time of the day
    assert not s.is_due(BANK, utc(2026, 10, 14, 17, 30), utc(2026, 10, 15, 5, 0))
    assert s.next_run(BANK, utc(2026, 10, 14, 17, 30), utc(2026, 10, 15, 5, 0)) == lisbon(2026, 10, 15, 7, 30)
    assert s.is_due(BANK, None, utc(2026, 10, 15))                     # never run: once, then to plan


def test_times_per_day_slots_missed_while_the_server_was_off_run_once():
    last = utc(2026, 10, 15, 6, 30)                                    # 07:30 ran; 13:00 and 18:30 were missed
    back_on = utc(2026, 10, 15, 18, 0)                                 # 19:00 Lisbon
    assert s.is_due(BANK, last, back_on)
    ran_at = back_on + timedelta(seconds=2)
    assert not s.is_due(BANK, ran_at, back_on + timedelta(minutes=5))  # not once per missed slot
    assert not s.is_due(BANK, ran_at, utc(2026, 10, 15, 23, 0))
    assert s.next_run(BANK, ran_at, back_on + timedelta(minutes=5)) == lisbon(2026, 10, 16, 7, 30)


def test_times_per_day_when_the_clocks_go_back():
    # 07:30 Lisbon is 06:30 UTC on the 24th but 07:30 UTC on the 25th
    last = utc(2026, 10, 24, 17, 30)                                   # the 18:30 run on the 24th (summer time)
    assert not s.is_due(BANK, last, utc(2026, 10, 25, 6, 31))          # 06:31 Lisbon (winter time): too early
    assert s.is_due(BANK, last, utc(2026, 10, 25, 7, 31))
    nxt = s.next_run(BANK, last, utc(2026, 10, 25, 3, 0))
    assert nxt == lisbon(2026, 10, 25, 7, 30) and nxt.astimezone(timezone.utc) == utc(2026, 10, 25, 7, 30)
    after = utc(2026, 10, 25, 7, 30) + timedelta(seconds=4)
    assert not s.is_due(BANK, after, utc(2026, 10, 25, 12, 59)) and s.is_due(BANK, after, utc(2026, 10, 25, 13, 0))


def test_a_time_in_the_hour_that_repeats_runs_once():
    spec = Spec("times_per_day", time_of_day="01:30,05:00")
    ran = utc(2026, 10, 25, 0, 30) + timedelta(seconds=5)              # 01:30 Lisbon (summer time), the first time it shows
    assert s.is_due(spec, utc(2026, 10, 24, 4, 0), utc(2026, 10, 25, 0, 31))
    for later in (utc(2026, 10, 25, 0, 40), utc(2026, 10, 25, 1, 30), utc(2026, 10, 25, 1, 45)):   # incl. 01:30 and 01:45 again
        assert not s.is_due(spec, ran, later)
    assert s.is_due(spec, ran, utc(2026, 10, 25, 5, 1))                # 05:01 winter time
    # a run at 01:20 UTC (the second 01:20 on the clock) is already after the 01:30 slot: a wall-clock comparison would run it again
    assert not s.is_due(Spec("daily", time_of_day="01:30"), utc(2026, 10, 25, 1, 20), utc(2026, 10, 25, 1, 45))


def test_a_time_that_does_not_exist_when_the_clocks_go_forward_runs_once_just_after():
    spec = Spec("daily", time_of_day="01:30")                          # there is no 01:30 on 28 Mar 2027
    last = utc(2027, 3, 27, 1, 30)
    assert not s.is_due(spec, last, utc(2027, 3, 28, 1, 29))
    assert s.is_due(spec, last, utc(2027, 3, 28, 1, 31))
    assert not s.is_due(spec, utc(2027, 3, 28, 1, 31), utc(2027, 3, 28, 12, 0))


# --- every_n_minutes ----------------------------------------------------------------------------

def test_every_n_minutes_counts_from_the_last_scheduled_start():
    spec = Spec("every_n_minutes", every_n=10)
    last = utc(2026, 10, 15, 10, 0) + timedelta(seconds=3)             # started a few seconds after its check
    assert not s.is_due(spec, last, utc(2026, 10, 15, 10, 5))          # the check 5 minutes later: too soon
    assert s.is_due(spec, last, utc(2026, 10, 15, 10, 10))             # the check 10 minutes later: due (not 15)
    assert s.next_run(spec, last, utc(2026, 10, 15, 10, 5)) == lisbon(2026, 10, 15, 11, 10) + timedelta(seconds=3)
    assert s.is_due(spec, None, utc(2026, 10, 15))


def test_every_n_minutes_runs_once_after_a_gap_then_keeps_the_pace():
    spec = Spec("every_n_minutes", every_n=10)
    last = utc(2026, 10, 15, 8, 0)
    assert s.is_due(spec, last, utc(2026, 10, 15, 10, 0))              # missed for two hours: runs
    ran = utc(2026, 10, 15, 10, 0) + timedelta(seconds=2)
    assert not s.is_due(spec, ran, utc(2026, 10, 15, 10, 5)) and s.is_due(spec, ran, utc(2026, 10, 15, 10, 10))


def test_every_n_minutes_is_real_minutes_across_a_clock_change():
    spec = Spec("every_n_minutes", every_n=10)
    last = utc(2026, 10, 25, 0, 55)                                    # 01:55 Lisbon summer time; the clock then shows 01:00 again
    assert s.next_slot_after(spec, last).astimezone(timezone.utc) == utc(2026, 10, 25, 1, 5)
    assert not s.is_due(spec, last, utc(2026, 10, 25, 0, 58)) and s.is_due(spec, last, utc(2026, 10, 25, 1, 6))
    forward = utc(2027, 3, 28, 0, 55)                                  # 00:55 Lisbon; ten minutes later it is 02:05
    assert s.next_slot_after(spec, forward).astimezone(timezone.utc) == utc(2027, 3, 28, 1, 5)
    assert s.next_slot_after(spec, forward) == lisbon(2027, 3, 28, 2, 5)


def test_every_n_minutes_off_means_never():
    assert not s.is_due(Spec("every_n_minutes", every_n=10, enabled=False), None, utc(2026, 10, 15))


# --- words and validation -------------------------------------------------------------------------

def test_describe_the_new_kinds_in_plain_words():
    assert s.describe(BANK) == "Every day at 07:30, 13:00 and 18:30"
    assert s.describe(Spec("times_per_day", time_of_day="09:00")) == "Every day at 09:00"
    assert s.describe(Spec("times_per_day", time_of_day="18:30,07:30")) == "Every day at 07:30 and 18:30"
    assert s.describe(Spec("every_n_minutes", every_n=10)) == "Every 10 minutes"
    assert s.describe(Spec("every_n_minutes", every_n=60)) == "Every hour"
    assert s.describe(Spec("every_n_minutes", every_n=360)) == "Every 6 hours"
    assert s.describe(Spec("every_n_minutes", every_n=10, enabled=False)) == "Off"


@pytest.mark.parametrize("bad", [
    Spec("times_per_day", time_of_day=""), Spec("times_per_day", time_of_day="7pm,13:00"), Spec("times_per_day", time_of_day="25:00"),
    Spec("times_per_day", time_of_day="07:30 13:00"),
    Spec("times_per_day", time_of_day=",".join(f"{h:02d}:00" for h in range(24)) + ",23:30"),       # 25 times
    Spec("every_n_minutes", every_n=4), Spec("every_n_minutes", every_n=0), Spec("every_n_minutes", every_n=1441)])
def test_invalid_new_schedules_are_refused_with_a_reason(bad):
    with pytest.raises(s.InvalidSchedule) as e:
        s.validate(bad)
    assert len(str(e.value)) > 40


def test_minutes_bounds_are_five_to_a_day_and_say_why():
    assert s.validate(Spec("every_n_minutes", every_n=5)).every_n == 5 and s.validate(Spec("every_n_minutes", every_n=1440)).every_n == 1440
    with pytest.raises(s.InvalidSchedule) as e:
        s.validate(Spec("every_n_minutes", every_n=4))
    assert "between 5 and 1440" in str(e.value) and "every 5 minutes" in str(e.value)


def test_times_are_tidied_when_validated():
    assert s.validate(Spec("times_per_day", time_of_day="18:30, 7:30 ,13:00,13:00")).time_of_day == "07:30,13:00,18:30"
    assert s.validate(Spec("every_n_minutes", every_n=10, time_of_day="07:30,13:00")).every_n == 10   # the time is not used


def test_runs_per_day():
    assert s.runs_per_day(BANK) == 3 and s.runs_per_day(Spec("daily")) == 1 and s.runs_per_day(Spec("weekly")) == 1
    assert s.runs_per_day(Spec("every_n_minutes", every_n=10)) == 144
    assert s.runs_per_day(Spec("every_n_minutes", every_n=360)) == 4 and s.runs_per_day(Spec("every_n_minutes", every_n=359)) == 5
    assert s.runs_per_day(Spec("every_n_minutes", every_n=1440)) == 1


def test_a_schedule_that_could_exceed_the_daily_limit_is_refused_in_three_parts():
    four = Spec("times_per_day", time_of_day="06:00,10:00,14:00,18:00")
    assert s.validate(four, **BANK_LIMIT) == four                                        # exactly the limit is fine
    assert s.validate(Spec("every_n_minutes", every_n=360), **BANK_LIMIT).every_n == 360
    assert s.validate(Spec("daily"), **BANK_LIMIT)
    five = Spec("times_per_day", time_of_day="06:00,10:00,14:00,18:00,22:00")
    with pytest.raises(s.InvalidSchedule) as e:
        s.validate(five, **BANK_LIMIT)
    msg = str(e.value)
    assert "Bank sync could run up to 5 times a day" in msg                              # what would happen
    assert "the bank allows only 4 requests per account per day" in msg and "refused" in msg   # why
    assert "Keep at most 4 times a day" in msg                                           # what to do
    with pytest.raises(s.InvalidSchedule) as e:
        s.validate(Spec("every_n_minutes", every_n=359), **BANK_LIMIT)
    assert "up to 5 times a day" in str(e.value) and "at least 360 minutes" in str(e.value)
    with pytest.raises(s.InvalidSchedule):
        s.validate(Spec("every_n_minutes", every_n=10), **BANK_LIMIT)
    assert s.validate(five)                                                              # a job without a limit is not held to one


# --- through the scheduler ------------------------------------------------------------------------

def _install(monkeypatch, *jobs):
    table = {j.key: j for j in jobs}
    monkeypatch.setattr(registry, "JOBS", table)
    monkeypatch.setattr(scheduler, "JOBS", table)
    monkeypatch.setattr("app.routers.schedules.JOBS", table)


def _ok_job(key, spec, calls, **kw):
    async def run(session, trigger):
        calls.append(trigger)
        return JobOutcome("ok", "Done.")
    return JobDef(key, key.title(), "A test job.", spec, run, **kw)


@pytest.mark.asyncio
async def test_slots_missed_while_off_start_the_job_once_not_once_per_slot(session, monkeypatch):
    calls = []
    now = datetime.now(timezone.utc)
    local = now.astimezone(s.LISBON)
    times = ",".join(sorted({(local - timedelta(hours=h)).strftime("%H:%M") for h in (3, 2, 1)}))    # three slots already passed
    _install(monkeypatch, _ok_job("slotted", Spec("times_per_day", time_of_day=times), calls))
    session.add(JobRun(job="slotted", trigger="schedule", started_at=(now - timedelta(days=2)).replace(tzinfo=None), outcome="ok"))
    session.commit()
    assert len(await scheduler.run_due(session, now)) == 1
    assert await scheduler.run_due(session, now + timedelta(minutes=5)) == []
    assert await scheduler.run_due(session, now + timedelta(minutes=10)) == [] and calls == ["schedule"]


@pytest.mark.asyncio
async def test_a_manual_run_does_not_move_the_minutes_cycle(session, monkeypatch):
    calls = []
    _install(monkeypatch, _ok_job("often", Spec("every_n_minutes", every_n=10), calls))
    now = datetime.now(timezone.utc)
    await scheduler.run_job(session, "often", "manual")
    assert len(await scheduler.run_due(session, now)) == 1               # the manual run did not count: the cycle starts now
    assert await scheduler.run_due(session, now + timedelta(minutes=5)) == []
    assert len(await scheduler.run_due(session, now + timedelta(minutes=10))) == 1
    assert calls == ["manual", "schedule", "schedule"]


@pytest.mark.asyncio
async def test_a_slow_job_is_never_started_a_second_time_by_the_next_check(engine, monkeypatch):
    """A long bank sync is still going when the next 5-minute check arrives: that check must leave it alone."""
    started, release, calls = asyncio.Event(), asyncio.Event(), []

    async def slow(session, trigger):
        calls.append(trigger)
        started.set()
        await release.wait()
        return JobOutcome("ok", "Done.")
    _install(monkeypatch, JobDef("slow", "Slow job", "A test job.", Spec("every_n_minutes", every_n=5), slow))
    now = datetime.now(timezone.utc)
    with Session(engine) as first, Session(engine) as next_check:
        running = asyncio.create_task(scheduler.run_due(first, now))
        await started.wait()
        try:
            # five and ten minutes on, the job is due again by its schedule, but it has not finished
            assert await asyncio.wait_for(scheduler.run_due(next_check, now + timedelta(minutes=5)), 5) == []
            assert await asyncio.wait_for(scheduler.run_due(next_check, now + timedelta(minutes=10)), 5) == []
            manual = await asyncio.wait_for(scheduler.run_job(next_check, "slow", "manual"), 5)   # nor can a button press start it
            assert manual.outcome == "skipped" and "already running" in manual.summary
        finally:
            release.set()                       # (a missing guard would start a second copy that waits here: fail, don't hang)
        (done,) = await running
        assert done.outcome == "ok"
    assert calls == ["schedule"]
    with Session(engine) as check:
        assert len(check.exec(select(JobRun).where(JobRun.trigger == "schedule")).all()) == 1   # no second or "skipped" scheduled row


@pytest.mark.asyncio
async def test_a_stale_running_record_from_a_crashed_run_does_not_block_the_job_forever(session, monkeypatch):
    calls = []
    _install(monkeypatch, _ok_job("often", Spec("every_n_minutes", every_n=10), calls))
    session.add(JobRun(job="often", trigger="schedule", outcome="running", started_at=datetime.utcnow() - timedelta(hours=2)))
    session.commit()
    assert len(await scheduler.run_due(session, datetime.now(timezone.utc) + timedelta(hours=1))) == 1 and calls == ["schedule"]


@pytest.mark.asyncio
async def test_routine_runs_of_a_quiet_job_stay_out_of_the_history_and_old_ones_are_cleared(session, client, monkeypatch):
    calls = []
    _install(monkeypatch, _ok_job("often", Spec("every_n_minutes", every_n=10), calls, quiet_when_ok=True))
    old = datetime.utcnow() - timedelta(days=3)
    session.add(JobRun(job="often", trigger="schedule", outcome="ok", started_at=old, summary="Nothing new in the mailbox."))
    session.add(JobRun(job="often", trigger="schedule", outcome="failed", started_at=old, summary="Mailbox was unreachable."))
    session.commit()
    await scheduler.run_job(session, "often", "schedule")
    rows = session.exec(select(JobRun)).all()
    assert sorted((r.outcome, r.started_at < datetime.utcnow() - timedelta(days=2)) for r in rows) == [("failed", True), ("ok", False)]
    await scheduler.run_job(session, "often", "manual")
    history = client.get("/schedules").text.split("Recent runs")[1]
    assert "Mailbox was unreachable." in history and "by hand" in history
    assert history.count("Done.") == 1                                                   # the manual run; the routine scheduled one is hidden


# --- the page and the save route ------------------------------------------------------------------

def test_the_form_offers_the_new_kinds_and_its_script_handles_them(client):
    page = client.get("/schedules").text
    assert 'value="times_per_day"' in page and "Several times a day" in page
    assert 'value="every_n_minutes"' in page and "Every few minutes" in page
    assert 'k === "every_n_minutes"' in page and "n.disabled" in page and "times separated by commas" in page


def test_saving_several_times_a_day_stores_them_tidied(client, session):
    client.post("/schedules/bank_sync", data={"kind": "times_per_day", "time_of_day": "18:30, 7:30,13:00", "enabled": "1"})
    row = session.get(JobSchedule, "bank_sync")
    assert (row.kind, row.time_of_day, row.enabled) == ("times_per_day", "07:30,13:00,18:30", True)
    assert "Every day at 07:30, 13:00 and 18:30" in client.get("/schedules").text


def test_saving_every_n_minutes(client, session):
    client.post("/schedules/mailbox_check", data={"kind": "every_n_minutes", "every_n": "15", "enabled": "1"})
    row = session.get(JobSchedule, "mailbox_check")
    assert (row.kind, row.every_n) == ("every_n_minutes", 15)
    assert "Every 15 minutes" in client.get("/schedules").text


@pytest.mark.parametrize("data,expected", [
    ({"kind": "times_per_day", "time_of_day": "7pm"}, "separated by commas"),
    ({"kind": "every_n_minutes", "every_n": "3"}, "between 5 and 1440"),
])
def test_a_bad_new_schedule_is_not_saved_and_says_why(client, session, data, expected):
    r = client.post("/schedules/mailbox_check", data={**data, "enabled": "1"}, follow_redirects=True)
    assert "Not saved." in r.text and expected in r.text and "left as it was" in r.text
    assert session.get(JobSchedule, "mailbox_check") is None


@pytest.mark.parametrize("data", [
    {"kind": "times_per_day", "time_of_day": "06:00,10:00,14:00,18:00,22:00"},
    {"kind": "every_n_minutes", "every_n": "60"},
])
def test_the_bank_sync_save_refuses_a_schedule_over_four_a_day(client, session, data):
    r = client.post("/schedules/bank_sync", data={**data, "enabled": "1"}, follow_redirects=True)
    text = r.text.replace("&#39;", "'")
    assert "Not saved." in text                                                          # what
    assert "the bank allows only 4 requests per account per day" in text                 # why
    assert "at most 4 times a day" in text or "at least 360 minutes" in text             # what to do
    assert "left as it was" in text
    assert session.get(JobSchedule, "bank_sync") is None


def test_the_bank_sync_save_accepts_exactly_four_a_day_and_other_jobs_have_no_such_limit(client, session):
    client.post("/schedules/bank_sync", data={"kind": "times_per_day", "time_of_day": "06:00,10:00,14:00,18:00", "enabled": "1"})
    assert session.get(JobSchedule, "bank_sync").time_of_day == "06:00,10:00,14:00,18:00"
    client.post("/schedules/mailbox_check", data={"kind": "every_n_minutes", "every_n": "5", "enabled": "1"})
    assert session.get(JobSchedule, "mailbox_check").every_n == 5
    assert "at most 4 runs a day" in client.get("/schedules").text                       # the limit is shown on the page
