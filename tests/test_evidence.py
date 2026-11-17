"""The evidence window of spec 11.2 and the out-of-sample responsibility of spec 11.4."""
import networkx as nx
import pandas as pd

from ehr_triage import baselines
from ehr_triage.graph import E, P, U, evidence_paths, in_window


def small_graph() -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    g.add_node(E("e1"), start=pd.Timestamp("2026-10-10T09:00"), stop=pd.Timestamp("2026-10-10T10:00"))
    g.add_edge(P("p1"), E("e1"), key="had")
    g.add_edge(U("u1"), E("e1"), key="care_team")
    return g


def test_chart_preparation_on_the_day_of_the_visit_is_explained():
    g = small_graph()
    assert in_window(g, E("e1"), pd.Timestamp("2026-10-10T07:30"), 7)
    assert "care team" in evidence_paths(g, "u1", "p1", pd.Timestamp("2026-10-10T07:30"), 7)


def test_a_visit_weeks_away_explains_nothing():
    g = small_graph()
    before = pd.Timestamp("2026-09-18T12:00")  # 22 days before, as in the case that prompted the rule
    assert not in_window(g, E("e1"), before, 7)
    assert "care team" not in evidence_paths(g, "u1", "p1", before, 7)
    assert "care team" in evidence_paths(g, "u1", "p1", before)  # without a window, pass 1's behavior


def test_follow_up_counts_for_a_week_after_the_visit_and_not_after():
    g = small_graph()
    assert in_window(g, E("e1"), pd.Timestamp("2026-10-17T09:59"), 7)
    assert not in_window(g, E("e1"), pd.Timestamp("2026-10-17T10:01"), 7)


def test_no_access_is_explained_by_a_pattern_mined_from_itself(monkeypatch):
    # A stand-in for the mining step: a department is responsible for every diagnosis it opened.
    def mined(units, encounters, staff, cfg):
        diagnosis = dict(zip(encounters["patient_id"], encounters["reason_description"]))
        return {(staff.set_index("user_id").loc[u, "department_id"], diagnosis[p]) for u, p in zip(units["user_id"], units["patient_id"])}

    monkeypatch.setattr(baselines, "responsibility", mined)
    cfg = {"seed": 42}
    patients = [f"p{i}" for i in range(40)]
    halves = {p: baselines.patient_half(p, 42) for p in patients}
    a = [p for p in patients if halves[p] == 0][:2]
    b = [p for p in patients if halves[p] == 1][:2]
    encounters = pd.DataFrame({"patient_id": a + b, "reason_description": ["only in half A", "only in half A", "in both", "in both"]})
    encounters.loc[1, "reason_description"] = "in both"
    staff = pd.DataFrame({"user_id": ["u1"], "department_id": ["CARD"]})
    units = pd.DataFrame({"user_id": ["u1"] * 4, "patient_id": a + b})
    out = baselines.responsible_out_of_sample(units, encounters, staff, cfg)
    assert list(out) == [False, True, True, True]  # the half-A-only diagnosis cannot explain its own access
