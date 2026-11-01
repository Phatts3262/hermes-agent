"""Stale-schedule guard on the cron fire path (#93049).

A direct ``jobs.json`` edit that changes ``schedule.expr`` leaves the stored
``next_run_at`` computed under the *old* expression — e.g. editing a daily
``0 7 * * *`` job down to ``0 7 * * 1-5`` keeps a Saturday 07:00
``next_run_at``. The old due check only compared ``next_run_at <= now``, so
the job fired on the day the new expression excludes. The guard re-anchors
``next_run_at`` from the current expression and skips the fire.

These exercise the real store against a temp ``HERMES_HOME`` (no mocks) per
the E2EE-over-mocks discipline for file-touching code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME so jobs.json doesn't touch the real store."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    yield tmp_path


def _write_cron_job(schedule_expr: str, next_run_at: datetime) -> str:
    """Persist a cron job with a pinned next_run_at (the stale-edit shape)."""
    from cron.jobs import create_job, save_jobs, load_jobs

    job = create_job(prompt="x", schedule="every 5m", name="t")
    jobs = load_jobs()
    for j in jobs:
        if j["id"] == job["id"]:
            j["schedule"] = {"kind": "cron", "expr": schedule_expr}
            j["next_run_at"] = next_run_at.isoformat()
    save_jobs(jobs)
    return job["id"]


# 2026-08-22 is a Saturday; 2026-08-24 is the following Monday.
_SATURDAY_0700 = datetime.fromisoformat("2026-08-22T07:00:00+00:00")
_MONDAY_0700 = datetime.fromisoformat("2026-08-24T07:00:00+00:00")


def test_stale_next_run_on_excluded_dow_does_not_fire(temp_home, monkeypatch):
    """next_run_at computed under the old daily expr must not fire the new
    Mon-Fri schedule on a Saturday — the job re-anchors to Monday instead."""
    from cron.jobs import get_due_jobs, get_job

    monkeypatch.setattr(
        "cron.jobs._hermes_now", lambda: _SATURDAY_0700 + timedelta(seconds=30)
    )
    jid = _write_cron_job("0 7 * * 1-5", _SATURDAY_0700)

    due = get_due_jobs()

    assert [j["id"] for j in due if j["id"] == jid] == []
    stored = get_job(jid)
    assert stored["next_run_at"].startswith(_MONDAY_0700.isoformat())


def test_matching_next_run_still_fires(temp_home, monkeypatch):
    """Control: the same stored instant under an expression it matches (daily)
    still fires — the guard only blocks schedule-drifted instants."""
    from cron.jobs import get_due_jobs

    monkeypatch.setattr(
        "cron.jobs._hermes_now", lambda: _SATURDAY_0700 + timedelta(seconds=30)
    )
    jid = _write_cron_job("0 7 * * *", _SATURDAY_0700)

    due = get_due_jobs()

    assert jid in [j["id"] for j in due]


def test_manual_trigger_bypasses_stale_schedule_guard(temp_home, monkeypatch):
    """An explicit run-now instant need not occur in the cron expression."""
    from cron.jobs import create_job, get_due_jobs, get_job, mark_job_run, trigger_job

    now = _SATURDAY_0700 + timedelta(seconds=30)
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: now)
    job = create_job(prompt="x", schedule="0 7 * * 1-5", name="manual")

    triggered = trigger_job(job["id"])
    due = get_due_jobs()

    assert triggered is not None
    assert job["id"] in [candidate["id"] for candidate in due]
    assert triggered["manual_run_at"] == triggered["next_run_at"]

    mark_job_run(job["id"], success=True)

    assert "manual_run_at" not in get_job(job["id"])


def test_delayed_manual_trigger_is_not_counted_as_catch_up(temp_home, monkeypatch):
    """Run-now intent remains explicit even when the next scan is hours later."""
    from cron.jobs import (
        create_job,
        get_catch_up_occurrence_count,
        get_due_jobs,
        trigger_job,
    )

    trigger_time = _SATURDAY_0700 + timedelta(seconds=30)
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: trigger_time)
    job = create_job(prompt="x", schedule="0 7 * * 1-5", name="delayed")
    trigger_job(job["id"])
    monkeypatch.setattr(
        "cron.jobs._hermes_now", lambda: trigger_time + timedelta(hours=5)
    )

    due = get_due_jobs()

    assert job["id"] in [candidate["id"] for candidate in due]
    assert get_catch_up_occurrence_count() == 0


def test_manual_trigger_survives_timezone_change_before_tick(temp_home, monkeypatch):
    """TZ migration repair must not replace an explicit run-now instant."""
    from cron.jobs import create_job, get_due_jobs, trigger_job

    trigger_time = datetime.fromisoformat("2026-08-22T21:00:00+10:00")
    scan_time = datetime.fromisoformat("2026-08-22T13:00:00+02:00")
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: trigger_time)
    job = create_job(prompt="x", schedule="0 7 * * 1-5", name="tz-manual")
    trigger_job(job["id"])
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: scan_time)

    due = get_due_jobs()

    assert job["id"] in [candidate["id"] for candidate in due]


def test_stale_next_run_skips_even_inside_catchup_window(temp_home, monkeypatch):
    """The catch-up 'run once now' policy must not resurrect a stale instant:
    hours after the stored time, the drifted job re-anchors without firing."""
    from cron.jobs import get_due_jobs, get_job

    monkeypatch.setattr(
        "cron.jobs._hermes_now", lambda: _SATURDAY_0700 + timedelta(hours=5)
    )
    jid = _write_cron_job("0 7 * * 1-5", _SATURDAY_0700)

    due = get_due_jobs()

    assert [j["id"] for j in due if j["id"] == jid] == []
    assert get_job(jid)["next_run_at"].startswith(_MONDAY_0700.isoformat())


def test_helper_reports_match_for_non_cron_and_unvalidatable(temp_home):
    """Best-effort validation: non-cron kinds, missing expr, and croniter
    unavailable all report a match so the fire path is unchanged there."""
    from cron.jobs import _cron_next_run_matches_expr

    assert _cron_next_run_matches_expr({"kind": "interval"}, _SATURDAY_0700) is True
    assert _cron_next_run_matches_expr({"kind": "cron"}, _SATURDAY_0700) is True
    # A croniter-matching instant reports a match; an excluded one does not.
    assert (
        _cron_next_run_matches_expr(
            {"kind": "cron", "expr": "0 7 * * *"}, _SATURDAY_0700
        )
        is True
    )
    assert (
        _cron_next_run_matches_expr(
            {"kind": "cron", "expr": "0 7 * * 1-5"}, _SATURDAY_0700
        )
        is False
    )


def test_stale_edit_without_manual_marker_still_reanchors(temp_home, monkeypatch):
    """#93049 protection intact after the #94010 fix: a hand-edited stale
    next_run_at WITHOUT the manual_run_at marker still re-anchors without
    firing (carried from #94034's suite)."""
    from cron.jobs import get_due_jobs, get_job

    monkeypatch.setattr(
        "cron.jobs._hermes_now", lambda: _SATURDAY_0700 + timedelta(minutes=5)
    )
    jid = _write_cron_job("0 7 * * 1-5", _SATURDAY_0700)

    stored_before = get_job(jid)
    assert "manual_run_at" not in stored_before

    due = get_due_jobs()

    assert [j["id"] for j in due if j["id"] == jid] == []
    after = get_job(jid)
    assert after["next_run_at"] != _SATURDAY_0700.isoformat()
    assert "manual_run_at" not in after


# Clock changes. This guard is how the bug bit: croniter given an aware base
# shifted the result by the offset change, the wrong hour was not an occurrence
# of the expression, and the job was re-anchored without firing. New York goes
# back on 2026-11-01 (02:00 EDT to 01:00 EST) and forward on 2027-03-14 (02:00
# EST to 03:00 EDT).
_ET = ZoneInfo("America/New_York")


class _Clock:
    """A settable cron clock. Steps go through UTC because same-tzinfo addition
    is wall-clock arithmetic and would never cross a clock change."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def step(self) -> None:
        self.now = (self.now.astimezone(timezone.utc) + timedelta(minutes=1)).astimezone(_ET)


def _create_jobs(*exprs: str) -> dict:
    from cron.jobs import create_job

    return {create_job(prompt="x", schedule=expr, name=expr)["id"]: expr for expr in exprs}


def _walk(clock: _Clock, jobs: dict, minutes: int) -> dict:
    """Tick minute by minute in the scheduler's order; return each expression's fires."""
    from cron.jobs import advance_next_runs, get_due_jobs, mark_job_run

    fires = {expr: [] for expr in jobs.values()}
    for _ in range(minutes):
        due = get_due_jobs()
        advance_next_runs([job["id"] for job in due])
        for job in due:
            fires[jobs[job["id"]]].append(clock.now.isoformat())
            mark_job_run(job["id"], success=True)
        clock.step()
    return fires


@pytest.mark.parametrize(
    "expr, computed_at, stored",
    [
        ("0 7 * * 1-5", "2026-10-30T07:00:05", "2026-11-02T07:00:00-05:00"),
        ("0 18 * * 0", "2026-10-25T18:00:05", "2026-11-01T18:00:00-05:00"),
        ("15 1 * * *", "2026-11-01T01:15:05", "2026-11-02T01:15:00-05:00"),
        ("0 7 * * 1-5", "2027-03-12T07:00:05", "2027-03-15T07:00:00-04:00"),
    ],
)
def test_cron_computed_across_a_clock_change_fires_on_the_wall_clock(
    temp_home, monkeypatch, expr, computed_at, stored
):
    """The next run computed before a clock change keeps its wall time, and the
    stale-schedule guard then lets it fire instead of dropping it."""
    from cron.jobs import get_due_jobs, get_job

    clock = _Clock(datetime.fromisoformat(computed_at).replace(tzinfo=_ET))
    monkeypatch.setattr("cron.jobs._hermes_now", clock)
    job_id = next(iter(_create_jobs(expr)))

    assert get_job(job_id)["next_run_at"] == stored

    clock.now = datetime.fromisoformat(stored).astimezone(_ET) + timedelta(seconds=30)

    assert job_id in [job["id"] for job in get_due_jobs()]


def test_repeated_hour_fires_each_wall_time_once(temp_home, monkeypatch):
    """A time in the repeated hour fires on its first occurrence only, and a job
    more frequent than hourly pauses for that hour."""
    clock = _Clock(datetime(2026, 11, 1, 0, 50, tzinfo=_ET))
    monkeypatch.setattr("cron.jobs._hermes_now", clock)
    jobs = _create_jobs("59 1 * * *", "30 1 * * *", "*/30 * * * *")

    fires = _walk(clock, jobs, 200)

    assert fires["59 1 * * *"] == ["2026-11-01T01:59:00-04:00"]
    assert fires["30 1 * * *"] == ["2026-11-01T01:30:00-04:00"]
    assert fires["*/30 * * * *"][:3] == [
        "2026-11-01T01:00:00-04:00",
        "2026-11-01T01:30:00-04:00",
        "2026-11-01T02:00:00-05:00",
    ]


def test_recompute_inside_repeated_hour_does_not_refire(temp_home, monkeypatch):
    """A recompute from now during the second 01:xx must not offer a wall time
    whose first occurrence has already fired."""
    from cron.jobs import get_job, pause_job, resume_job

    clock = _Clock(datetime(2026, 11, 1, 0, 50, tzinfo=_ET))
    monkeypatch.setattr("cron.jobs._hermes_now", clock)
    jobs = _create_jobs("30 1 * * *")
    job_id = next(iter(jobs))

    before = _walk(clock, jobs, 90)

    assert clock.now.isoformat() == "2026-11-01T01:20:00-05:00"
    pause_job(job_id)
    resume_job(job_id)

    assert get_job(job_id)["next_run_at"] == "2026-11-02T01:30:00-05:00"

    after = _walk(clock, jobs, 30)

    assert before["30 1 * * *"] == ["2026-11-01T01:30:00-04:00"]
    assert after["30 1 * * *"] == []


@pytest.mark.parametrize(
    "expr, moved_forward",
    [
        ("30 2 * * *", "2027-03-14T03:30:00-04:00"),
        ("0 2,3 * * *", "2027-03-14T03:00:00-04:00"),
        ("0,30 2 * * *", "2027-03-14T03:00:00-04:00"),
        ("15,45 2 * * *", "2027-03-14T03:15:00-04:00"),
        ("*/10 2 * * *", "2027-03-14T03:00:00-04:00"),
    ],
)
def test_skipped_hour_fires_once_moved_forward(temp_home, monkeypatch, expr, moved_forward):
    """A job scheduled in the skipped hour fires once that day, an hour later,
    stored as a real instant the stale-schedule guard still accepts."""
    from cron.jobs import get_job

    clock = _Clock(datetime(2027, 3, 14, 1, 50, tzinfo=_ET))
    monkeypatch.setattr("cron.jobs._hermes_now", clock)
    jobs = _create_jobs(expr)
    job_id = next(iter(jobs))

    assert get_job(job_id)["next_run_at"] == moved_forward

    fires = _walk(clock, jobs, 100)

    assert fires[expr] == [moved_forward]


@pytest.mark.parametrize(
    "hours_since_last_run, wedged",
    [(25.5, False), (26.5, True)],
)
def test_stale_error_threshold_uses_the_wall_clock_gap(
    temp_home, monkeypatch, hours_since_last_run, wedged
):
    """A daily job counts as wedged after a day plus its two-hour grace. Measured
    across a clock change the gap read 23 hours, so the threshold fell by one and
    a job merely waiting for its next run was re-armed."""
    from cron.jobs import _job_is_stale_error_recurring

    now = datetime(2026, 10, 31, 12, 0, tzinfo=_ET)
    monkeypatch.setattr("cron.jobs._hermes_now", _Clock(now))
    monkeypatch.setattr("cron.jobs._cron_cadence_cache", {})
    schedule = {"kind": "cron", "expr": "0 7 * * *"}
    job = {
        "id": "wedged",
        "last_status": "error",
        "last_run_at": (now - timedelta(hours=hours_since_last_run)).isoformat(),
    }

    assert _job_is_stale_error_recurring(job, schedule, now) is wedged


def test_overdue_across_the_repeated_hour_fires_once(temp_home, monkeypatch):
    """A job due at the first 01:45 and found overdue during the second 01:xx is
    late, not stale: its stored wall clock reads as still ahead, and the zone-change
    repair above the due check must not take that for a moved zone and drop it."""
    from cron.jobs import get_due_jobs, get_job, mark_job_run

    clock = _Clock(datetime(2026, 11, 1, 0, 50, tzinfo=_ET))
    monkeypatch.setattr("cron.jobs._hermes_now", clock)
    job_id = next(iter(_create_jobs("45 1 * * *")))
    assert get_job(job_id)["next_run_at"] == "2026-11-01T01:45:00-04:00"

    # The gateway was down from before 01:45 EDT until 01:10 EST.
    clock.now = datetime.fromisoformat("2026-11-01T01:10:00-05:00").astimezone(_ET)

    assert job_id in [job["id"] for job in get_due_jobs()]
    mark_job_run(job_id, success=True)
    assert get_job(job_id)["next_run_at"] == "2026-11-02T01:45:00-05:00"


@pytest.mark.parametrize(
    "start",
    [datetime(2026, 11, 1, 1, 30, tzinfo=_ET), datetime(2027, 3, 14, 1, 30, tzinfo=_ET)],
    ids=["clocks-back", "clocks-forward"],
)
def test_interval_counts_real_time_across_a_clock_change(temp_home, monkeypatch, start):
    """An interval is elapsed time, so neither change stretches or shortens it. The
    second half is the due check: a stored second 01:00 must not read as due during
    the first. 95m lands past the skipped hour, where wall-clock addition falls short."""
    clock = _Clock(start)
    monkeypatch.setattr("cron.jobs._hermes_now", clock)
    jobs = _create_jobs("every 1m", "every 30m", "every 95m")

    fires = _walk(clock, jobs, 100)

    for minutes in (1, 30, 95):
        stamps = [start.isoformat(), *fires[f"every {minutes}m"]]
        instants = [datetime.fromisoformat(stamp).timestamp() for stamp in stamps]
        gaps = {later - earlier for earlier, later in zip(instants, instants[1:])}
        assert gaps == {minutes * 60.0}


def test_one_shot_in_a_duration_counts_real_time(temp_home, monkeypatch):
    """`in 30m` at the first 01:50 means the second 01:20, and the one-shot grace
    check must not read that as already past and refuse the job."""
    from cron.jobs import create_job

    clock = _Clock(datetime(2026, 11, 1, 1, 50, tzinfo=_ET))
    monkeypatch.setattr("cron.jobs._hermes_now", clock)
    job = create_job(prompt="x", schedule="in 30m", name="once")

    assert job["next_run_at"] == "2026-11-01T01:20:00-05:00"

    fires = _walk(clock, {job["id"]: "in 30m"}, 40)

    assert fires["in 30m"] == ["2026-11-01T01:20:00-05:00"]


_NO_ZONE_SCENARIO = """
import sys
from datetime import datetime, timezone

import cron.jobs as jobs

expr = sys.argv[1]
computed_at, fires_at = (datetime.fromisoformat(value) for value in sys.argv[2:4])
clock = {"now": computed_at}
# hermes_time.now() with no timezone configured: today's fixed offset.
jobs._hermes_now = lambda: clock["now"].astimezone()

job = jobs.create_job(prompt="x", schedule=expr, name="no-zone")
print(job["next_run_at"])
clock["now"] = fires_at
print(job["id"] in [due["id"] for due in jobs.get_due_jobs()])
"""


@pytest.mark.parametrize(
    "expr, computed_at, fires_at, stored",
    [
        ("0 7 * * 1-5", "2027-03-12T12:00:00+00:00", "2027-03-15T11:00:30+00:00", "2027-03-15T07:00:00-04:00"),
        ("0 7 * * 1-5", "2026-10-30T12:00:00+00:00", "2026-11-02T12:00:30+00:00", "2026-11-02T07:00:00-05:00"),
        ("30 2 * * *", "2027-03-13T12:00:00+00:00", "2027-03-14T07:30:30+00:00", "2027-03-14T03:30:00-04:00"),
    ],
    ids=["clocks-forward", "clocks-back", "skipped-hour"],
)
def test_no_configured_timezone_keeps_the_system_clock_rules(
    temp_home, expr, computed_at, fires_at, stored
):
    """With no timezone set the clock carries today's fixed offset, which knows no
    clock changes, so the job would store the wrong hour and vanish. It runs in a child
    because a process reads the system zone's rules from TZ once, at start."""
    root = Path(__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if k != "HERMES_TIMEZONE"}
    env.update(TZ="EST5EDT", HERMES_HOME=str(temp_home), PYTHONPATH=str(root))

    done = subprocess.run(
        [sys.executable, "-c", _NO_ZONE_SCENARIO, expr, computed_at, fires_at],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == [stored, "True"]
