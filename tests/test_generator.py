import filecmp
import re

import pandas as pd
import pytest

from ehr_triage.generate import generate
from ehr_triage.population import miles_between, relationship_pull, street_of

LOG_COLUMNS = ["event_id", "timestamp", "user_id", "patient_id", "device_id", "action_code", "duration_s"]
LABEL_ONLY_COLUMNS = {"reason", "documented", "scenario_id", "part", "covered", "evidence_in_context",
                      "borrower_user_id", "same_household", "public_event_id", "group_id"}


# ---- labels stay apart from what a system under test can read ----

def test_log_has_exactly_the_published_fields(run):
    assert list(run["events"].columns) == LOG_COLUMNS


def test_no_label_column_in_logs_or_context(run_dir):
    for folder in ("logs", "context"):
        for f in (run_dir / folder).glob("*.csv"):
            header = set(pd.read_csv(f, nrows=0).columns)
            assert not header & LABEL_ONLY_COLUMNS, f.name


def test_no_scenario_id_appears_outside_labels(run_dir, run):
    ids = "|".join(run["scenarios"]["scenario_id"])
    for folder in ("logs", "context"):
        for f in (run_dir / folder).glob("*.csv"):
            assert not re.search(rf"\b({ids})\b", f.read_text()), f.name


def test_event_ids_follow_time_and_reveal_nothing(run):
    ev = run["events"]
    assert list(ev["event_id"]) == list(range(1, len(ev) + 1))
    assert ev["timestamp"].is_monotonic_increasing


def test_every_event_has_exactly_one_label(run):
    assert list(run["event_labels"]["event_id"]) == list(run["events"]["event_id"])


def test_same_seed_gives_identical_files(slim, cfg, run_dir, tmp_path):
    generate(cfg, slim, tmp_path)
    for folder in ("logs", "context", "labels"):
        names = sorted(f.name for f in (run_dir / folder).glob("*.csv"))
        match, mismatch, errors = filecmp.cmpfiles(run_dir / folder, tmp_path / folder, names, shallow=False)
        assert not mismatch and not errors, (folder, mismatch, errors)


# ---- the log is internally consistent ----

def test_references_resolve(run):
    ev = run["events"]
    assert ev["user_id"].isin(run["staff"]["user_id"]).all()
    assert ev["patient_id"].isin(run["patients"]["patient_id"]).all()
    assert ev["device_id"].isin(run["devices"]["device_id"]).all()
    assert ev["action_code"].isin(run["actions"]["action_code"]).all()
    assert run["care_team"]["encounter_id"].isin(run["encounters"]["encounter_id"]).all()
    assert (ev["duration_s"] >= 1).all()


def test_every_staff_member_is_also_a_patient(run):
    assert run["staff"]["patient_id"].isin(run["patients"]["patient_id"]).all()
    assert run["staff"]["patient_id"].is_unique


# ---- scenarios are what the spec says they are ----

def scenario_events(run, kind):
    labelled = run["events"].merge(run["event_labels"], on="event_id")
    return labelled[labelled["reason"] == kind]


def people(run):
    staff = run["staff"].set_index("user_id")
    patients = run["patients"].set_index("patient_id")
    return staff, patients


def test_every_scenario_type_was_injected(run, cfg):
    made = run["scenarios"]["type"].value_counts()
    for kind in ("M1", "M2a", "M2b", "M3", "M4", "M5", "M6a", "M6b"):
        assert made.get(kind, 0) >= 1, kind
        assert len(scenario_events(run, kind)) >= 1, kind


def test_snooping_has_no_legitimate_access_or_care_team_row(run):
    labelled = run["events"].merge(run["event_labels"], on="event_id")
    enc_patient = dict(zip(run["encounters"]["encounter_id"], run["encounters"]["patient_id"]))
    team = set(zip(run["care_team"]["user_id"], run["care_team"]["encounter_id"].map(enc_patient)))
    snoops = run["scenarios"][run["scenarios"]["type"].isin(["M1", "M2a", "M2b", "M3", "M4", "M5"])]
    for s in snoops.itertuples():
        assert (s.actor_user_id, s.target_patient_id) not in team
        pair = labelled[(labelled["user_id"] == s.actor_user_id) & (labelled["patient_id"] == s.target_patient_id)]
        assert set(pair["reason"]) == {s.type}


def test_m1_target_is_a_colleague_from_the_same_department(run):
    staff, _ = people(run)
    dept_of_person = dict(zip(run["staff"]["patient_id"], run["staff"]["department_id"]))
    for s in run["scenarios"].query("type == 'M1'").itertuples():
        assert dept_of_person[s.target_patient_id] == staff.loc[s.actor_user_id, "department_id"]


def test_m2a_shares_surname_and_address_and_m2b_shares_neither(run):
    staff, patients = people(run)
    for s in run["scenarios"][run["scenarios"]["type"].isin(["M2a", "M2b"])].itertuples():
        me, them = patients.loc[staff.loc[s.actor_user_id, "patient_id"]], patients.loc[s.target_patient_id]
        same = (me["last_name"] == them["last_name"], me["address"] == them["address"])
        assert same == ((True, True) if s.type == "M2a" else (False, False))


def test_m3_target_lives_within_the_hedda_distance_or_on_the_same_street(run, cfg):
    staff, patients = people(run)
    for s in run["scenarios"].query("type == 'M3'").itertuples():
        me, them = patients.loc[staff.loc[s.actor_user_id, "patient_id"]], patients.loc[s.target_patient_id]
        near = miles_between(me["lat"], me["lon"], them["lat"], them["lon"]) <= cfg["proximity_miles"]
        assert near or street_of(me["address"]) == street_of(them["address"])
        assert me["address"] != them["address"]


def test_m4_target_carries_the_vip_flag(run):
    _, patients = people(run)
    for s in run["scenarios"].query("type == 'M4'").itertuples():
        assert bool(patients.loc[s.target_patient_id, "vip"])


def test_m5_is_several_departments_on_one_patient(run):
    staff, _ = people(run)
    for _, group in run["scenarios"].query("type == 'M5'").groupby("group_id"):
        assert group["target_patient_id"].nunique() == 1
        assert staff.loc[group["actor_user_id"], "department_id"].nunique() >= 3


def test_m6a_runs_on_a_device_outside_the_owner_department(run):
    staff, _ = people(run)
    device_dept = dict(zip(run["devices"]["device_id"], run["devices"]["department_id"]))
    ev = scenario_events(run, "M6a")
    assert (ev["device_id"].map(device_dept) != ev["user_id"].map(staff["department_id"])).all()


def test_m6b_happens_while_the_owner_is_not_scheduled(run):
    ev = scenario_events(run, "M6b").merge(run["scenarios"][["scenario_id", "start"]], on="scenario_id")
    schedule = run["schedule"]
    for row in ev.drop_duplicates("scenario_id").itertuples():
        mine = schedule[schedule["user_id"] == row.user_id]
        assert not ((mine["shift_start"] <= row.start) & (row.start < mine["shift_end"])).any()


def test_self_access_is_near_the_published_share(run, cfg):
    own = run["events"]["user_id"].map(dict(zip(run["staff"]["user_id"], run["staff"]["patient_id"])))
    share = (own == run["events"]["patient_id"]).mean()
    assert 0.5 * cfg["self_access_share"] < share < 2 * cfg["self_access_share"]


def test_care_team_rows_match_the_documented_flag(run):
    labelled = run["events"].merge(run["event_labels"], on="event_id")
    enc_patient = dict(zip(run["encounters"]["encounter_id"], run["encounters"]["patient_id"]))
    team = set(zip(run["care_team"]["user_id"], run["care_team"]["encounter_id"].map(enc_patient)))
    pairs = lambda reason: set(zip(*[labelled.loc[labelled["reason"] == reason, c] for c in ("user_id", "patient_id")]))
    assert pairs("care_team") <= team
    assert not pairs("consult") & team  # a consult is only a hard negative if nothing documents it


# ---- users differ from each other ----

def test_part_time_and_occasional_staff_work_fewer_shifts(run):
    shifts = run["schedule"].groupby("user_id").size().reindex(run["staff"]["user_id"]).fillna(0)
    mean = shifts.groupby(run["staff"]["employment"].to_numpy()).mean()
    assert mean["full_time"] > mean["part_time"] > mean["occasional"]


def test_honest_work_also_happens_on_devices_outside_the_home_department(run):
    """Otherwise a foreign device alone would give shared credentials away."""
    labelled = run["events"].merge(run["event_labels"], on="event_id")
    device_dept = labelled["device_id"].map(dict(zip(run["devices"]["device_id"], run["devices"]["department_id"])))
    home = labelled["user_id"].map(dict(zip(run["staff"]["user_id"], run["staff"]["department_id"])))
    reasons = set(labelled.loc[device_dept != home, "reason"])
    assert {"order", "hospital_wide"} <= reasons


def test_care_management_reads_widely_and_is_documented_nowhere(run):
    labelled = run["events"].merge(run["event_labels"], on="event_id")
    wide = labelled[labelled["reason"] == "hospital_wide"]
    assert len(wide) and not wide["documented"].any()
    assert not wide["user_id"].isin(run["care_team"]["user_id"]).any()


# ---- people who know each other ----

def test_relationship_pull_follows_the_published_ratios_and_bars_the_own_chart(cfg):
    patients = pd.DataFrame(
        {
            "last_name": ["Lee", "Lee", "Diaz", "Khan"],
            "address": ["1 Elm Street", "900 Far Road", "7 Elm Street", "5 Other Way"],
            "city": "Testville",
            "lat": [42.100, 42.300, 42.1001, 42.1002],
            "lon": [-72.500, -72.900, -72.5001, -72.5002],
        }
    )
    staff = pd.DataFrame({"pidx": [0]})  # one staff member, who is patient 0
    pull = relationship_pull(staff, patients, cfg)[0]
    want = cfg["relationship_pull"]
    assert pull[0] == 0  # own chart
    assert pull[1] == want["last name"]  # namesake who lives far away
    assert pull[2] == want["same street"]  # same street beats mere proximity
    assert pull[3] == want["proximity"]  # close by, different street and name


def test_nobody_works_on_their_own_chart(run):
    labelled = run["events"].merge(run["event_labels"], on="event_id")
    own = labelled["user_id"].map(dict(zip(run["staff"]["user_id"], run["staff"]["patient_id"])))
    assert set(labelled.loc[own == labelled["patient_id"], "reason"]) <= {"self"}


# ---- covering accesses ----

@pytest.fixture(scope="module")
def covered(slim, cfg, tmp_path_factory):
    import copy
    with_cover = copy.deepcopy(cfg)
    with_cover.params["covering"]["value"] = {**cfg["covering"], "enabled": True}
    out = tmp_path_factory.mktemp("covered")
    generate(with_cover, slim, out)
    from ehr_triage.report import load_run
    return load_run(out)


def test_covering_accesses_change_nothing_else(run, covered):
    labelled = covered["events"].merge(covered["event_labels"], on="event_id")
    cover = labelled["part"] == "cover"
    assert cover.any()
    fields = ["timestamp", "user_id", "patient_id", "device_id", "action_code", "duration_s"]
    assert labelled.loc[~cover, fields].reset_index(drop=True).equals(run["events"][fields])


def test_each_covered_scenario_opens_new_charts_before_the_target(run, covered):
    labelled = covered["events"].merge(covered["event_labels"], on="event_id")
    scenarios = covered["scenarios"]
    done = scenarios[scenarios["covered"].astype(str) == "True"]
    assert set(done["type"]) <= {"M1", "M2a", "M2b", "M3", "M4"} and len(done)
    before = run["events"]
    for s in done.itertuples():
        mine = labelled[(labelled["scenario_id"] == s.scenario_id) & (labelled["part"] == "cover")]
        assert mine["patient_id"].nunique() >= 1 and s.target_patient_id not in set(mine["patient_id"])
        assert (mine["user_id"] == s.actor_user_id).all()
        known = set(before.loc[before["user_id"] == s.actor_user_id, "patient_id"])
        assert not set(mine["patient_id"]) & known  # a covering access opens a chart the actor had no reason to open
