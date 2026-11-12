import shutil

import networkx as nx
import pandas as pd
import pytest

from ehr_triage import baselines, evaluate
from ehr_triage.graph import VISIBLE, build_graph, evidence_paths, load_visible


@pytest.fixture(scope="module")
def scored(run_dir, cfg):
    rules = baselines.run_rules(run_dir, cfg)
    templates = baselines.run_templates(run_dir, cfg)
    return evaluate.truth(run_dir, rules.assign(direct=templates["direct"], responsible=templates["responsible"]), cfg)


def test_each_system_sees_only_what_the_spec_allows():
    assert VISIBLE["rules"] == ["staff", "patients"]
    assert VISIBLE["llm_raw"] == []
    assert "patients" not in VISIBLE["templates"]


def test_both_systems_run_with_no_labels_folder_at_all(run_dir, cfg, tmp_path):
    for folder in ("logs", "context"):
        shutil.copytree(run_dir / folder, tmp_path / folder)
    assert len(baselines.run_rules(tmp_path, cfg)) == len(baselines.run_templates(tmp_path, cfg)) > 0


def test_rules_flag_the_scenarios_that_are_defined_by_a_relationship(scored):
    for kind, rule in (("M1", "department co-worker"), ("M2a", "last name"), ("M3", None)):
        hit = scored[scored["misuse"] == kind]
        assert len(hit)
        if rule:
            assert hit["rules"].map(lambda m: rule in m).all(), kind
        else:
            assert hit["rules"].map(lambda m: "proximity" in m or "same street" in m).all()


def test_rules_have_nothing_on_a_news_event(scored):
    crowd = scored[scored["misuse"] == "M5"]
    assert not crowd["rules"].map(lambda m: any(r != "organization co-worker" for r in m)).all()


def test_direct_evidence_follows_the_care_team_and_never_explains_a_consult(scored):
    legit = scored[scored["misuse"] == ""]
    assert legit.loc[legit["reason"] == "care_team", "direct"].all()
    assert not legit.loc[legit["reason"].isin(["consult", "back_office", "hospital_wide"]), "direct"].any()


def test_snooping_is_never_explained_by_direct_evidence(scored):
    snoop = scored[scored["misuse"].isin(["M1", "M2a", "M2b", "M3", "M4", "M5"])]
    assert len(snoop) and not snoop["direct"].any()


def test_expected_top_k_splits_a_tie_evenly():
    hits, shown = baselines.expected_top_k(day=[0, 0, 0], score=[2, 1, 1], hit=[False, True, False], k=2)
    assert (hits, shown) == (0.5, 2)


def test_evidence_paths_name_each_connection():
    staff = pd.DataFrame({"user_id": ["U1"], "patient_id": ["P1"], "department_id": ["MED"]})
    patients = pd.DataFrame(
        {"patient_id": ["P1", "P2", "P3"], "last_name": ["Lee", "Lee", "Diaz"], "address": ["1 Elm St", "9 Far Rd", "2 Oak Av"],
         "city": "X", "lat": [42.0, 43.0, 44.0], "lon": [-72.0, -73.0, -74.0], "emergency_contact_id": ["", "", "P1"]}
    )
    encounters = pd.DataFrame({"encounter_id": ["E1", "E2"], "patient_id": ["P2", "P3"], "department_id": ["MED", "ED"]})
    care_team = pd.DataFrame({"encounter_id": ["E1"], "user_id": ["U1"], "team_role": ["nurse"]})
    g = build_graph({"staff": staff, "patients": patients, "encounters": encounters, "care_team": care_team}, 0.25)
    assert isinstance(g, nx.MultiDiGraph)
    assert set(evidence_paths(g, "U1", "P1")) == {"self"}
    assert set(evidence_paths(g, "U1", "P2")) == {"last name", "care team", "department of encounter"}
    assert set(evidence_paths(g, "U1", "P3")) == {"emergency contact"}


def test_evidence_paths_pick_the_encounter_nearest_the_access(run_dir, cfg):
    t = load_visible(run_dir, "llm_graph")
    team = t["care_team"].merge(t["encounters"][["encounter_id", "patient_id", "start"]], on="encounter_id", suffixes=("_team", ""))
    several = team.groupby(["user_id", "patient_id"]).filter(lambda g: g["encounter_id"].nunique() > 1)
    assert len(several), "the test population should have a user on two care teams of one patient"
    g = build_graph(t, cfg["proximity_miles"])
    for (u, p), rows in list(several.groupby(["user_id", "patient_id"]))[:20]:
        for r in rows.itertuples():
            path = evidence_paths(g, u, p, pd.Timestamp(r.start))["care team"]
            assert path[1] == ("encounter", r.encounter_id)


def test_results_report_runs_end_to_end(run_dir, cfg, tmp_path):
    evaluate.write_results(run_dir, cfg, tmp_path / "results.md")
    text = (tmp_path / "results.md").read_text()
    assert "Misuse caught, by scenario" in text and "M2a" in text
