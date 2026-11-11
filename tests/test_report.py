import pandas as pd

from ehr_triage.report import first_accesses, gap, immediate_repeat_share, repeat_share, with_week, write_report


def log(rows):
    ev = pd.DataFrame(rows, columns=["timestamp", "user_id", "patient_id"])
    ev.insert(0, "event_id", range(1, len(ev) + 1))
    return ev


def test_repeat_share_uses_the_hedda_weekly_definition():
    ev = log([
        ("2026-09-28T08:00:00", "U1", "P1"),  # first
        ("2026-09-28T09:00:00", "U1", "P1"),  # repeat
        ("2026-09-29T09:00:00", "U2", "P1"),  # first, other user
        ("2026-10-05T09:00:00", "U1", "P1"),  # first again, new week
    ])
    weekly = with_week(ev, "2026-09-28", 14)
    assert repeat_share(weekly) == 0.25


def test_partial_weeks_are_left_out():
    ev = log([("2026-09-28T08:00:00", "U1", "P1"), ("2026-10-12T08:00:00", "U1", "P1")])
    assert len(with_week(ev, "2026-09-28", 16)) == 1


def test_immediate_repeat_is_per_user():
    ev = log([
        ("2026-09-28T08:00:00", "U1", "P1"),
        ("2026-09-28T08:00:01", "U2", "P1"),  # other user in between does not break U1's run
        ("2026-09-28T08:00:02", "U1", "P1"),  # repeat for U1
        ("2026-09-28T08:00:03", "U1", "P2"),
    ])
    assert immediate_repeat_share(ev) == 0.25


def test_first_accesses_drop_self_access():
    ev = log([("2026-09-28T08:00:00", "U1", "P9"), ("2026-09-28T08:01:00", "U1", "P1")])
    staff = pd.DataFrame({"user_id": ["U1"], "patient_id": ["P9"]})
    first = first_accesses(with_week(ev, "2026-09-28", 7), staff)
    assert list(first["patient_id"]) == ["P1"]


def test_gap_reports_distance_and_never_hides_a_miss():
    assert gap(0.92, 0.86, "level") == "-6.0 points"
    assert gap([375, 580], 359, "level") == "-4% from the range"
    assert gap([375, 580], 400, "level") == "inside the range"
    assert gap({"median": 5, "p99": 80}, {"median": 10, "p99": 40}, "shape") == "ratio 4.0 against 16.0"


def test_report_runs_end_to_end(run_dir, cfg, tmp_path):
    result = write_report(run_dir, cfg, tmp_path / "report.md")
    text = (tmp_path / "report.md").read_text()
    for name in cfg.targets:
        assert name.replace("_", " ") in text
    assert 0 < result["repeat_access_share"] < 1
