"""Systems 1 and 2: the Hedda et al. rules and explanation-based filtering.

Both judge the same unit, a first access: one user opening one patient for
the first time in a week, self-accesses left out. Both read the log and
their permitted context tables through `graph.load_visible`. Neither can
reach labels/.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config
from .graph import build_graph, evidence_paths, load_visible

RULES = ["organization co-worker", "department co-worker", "last name", "proximity", "same street"]
DIRECT = ["care team", "ordered"]


def first_access_units(events: pd.DataFrame, staff: pd.DataFrame, window_start: str) -> pd.DataFrame:
    """One row per user, patient and week, at the first access."""
    day = (pd.to_datetime(events["timestamp"]) - pd.Timestamp(window_start)).dt.days
    e = events.assign(day=day, week=day // 7)
    own = e["user_id"].map(dict(zip(staff["user_id"], staff["patient_id"])))
    e = e[own != e["patient_id"]]
    first = e.drop_duplicates(["week", "user_id", "patient_id"])
    return first[["user_id", "patient_id", "week", "day", "event_id"]].reset_index(drop=True)


def run_rules(run_dir: str | Path, cfg: Config) -> pd.DataFrame:
    """System 1. Which of the five rules each first access matches, and a priority.

    Priority follows the paper's own proposal: rank a rule by how far its
    observed rate sits above chance.
    """
    t = load_visible(run_dir, "rules")
    g = build_graph(t, cfg["proximity_miles"])
    units = first_access_units(t["events"], t["staff"], cfg["window_start"])
    priority = cfg.targets["rule_observed_to_expected"]["value"]
    matched = []
    for u, p in zip(units["user_id"], units["patient_id"]):
        paths = evidence_paths(g, u, p)
        matched.append([r for r in RULES if r in paths])
    units["rules"] = matched
    units["rule_score"] = [max((priority[r] for r in m), default=0.0) for m in matched]
    return units


def responsibility(units: pd.DataFrame, encounters: pd.DataFrame, staff: pd.DataFrame, cfg: Config) -> set:
    """Departments responsible for a diagnosis, mined as in Fabbri & LeFevre 2013.

    A department is responsible for a diagnosis when it opens the records of
    patients with that diagnosis unusually often (access probability) and
    those patients make up an unusual share of what it opens (treatment
    probability). Both are z-scored across departments, per diagnosis.
    """
    th = cfg["responsibility_thresholds"]
    has = encounters.loc[encounters["reason_description"] != "", ["patient_id", "reason_description"]].drop_duplicates()
    has = has[has.groupby("reason_description")["patient_id"].transform("size") >= th["min_patients"]]
    dept = units["user_id"].map(dict(zip(staff["user_id"], staff["department_id"])))
    opened = pd.DataFrame({"department_id": dept, "patient_id": units["patient_id"]}).drop_duplicates()
    both = opened.merge(has, on="patient_id")
    n = both.groupby(["reason_description", "department_id"]).size().unstack(fill_value=0)
    n = n.reindex(columns=sorted(opened["department_id"].unique()), fill_value=0)
    access = n.div(has.groupby("reason_description").size(), axis=0)  # of patients with c, share opened by d
    treat = n.div(opened.groupby("department_id").size(), axis=1)  # of patients d opened, share with c
    z = lambda m: m.sub(m.mean(axis=1), axis=0).div(m.std(axis=1).replace(0, np.nan), axis=0)
    hit = (z(access) >= th["s"]) & (z(treat) >= th["t"])
    return {(d, c) for c, row in hit.iterrows() for d in row.index[row.to_numpy()]}


def patient_half(patient_id: str, seed: int) -> int:
    """0 or 1, fixed for a patient and a seed whatever set of accesses is being scored."""
    return int(hashlib.sha256(f"{seed}|{patient_id}".encode()).hexdigest(), 16) % 2


def responsible_out_of_sample(units: pd.DataFrame, encounters: pd.DataFrame, staff: pd.DataFrame, cfg: Config) -> pd.Series:
    """Whether departmental responsibility explains each first access, mined out of sample (spec 11.4).

    Patients are split in two halves. Responsibility is mined on the first
    accesses to one half and applied to the other, then the other way round,
    as Fabbri and LeFevre learn it on training patients and test it on the
    rest. No first access is explained by a pattern mined from itself.
    """
    diagnoses = encounters[encounters["reason_description"] != ""].groupby("patient_id")["reason_description"].agg(set).to_dict()
    dept = dict(zip(staff["user_id"], staff["department_id"]))
    half = units["patient_id"].map(lambda p: patient_half(p, cfg["seed"]))
    out = pd.Series(False, index=units.index)
    for test in (0, 1):
        mined = responsibility(units[half != test].reset_index(drop=True), encounters, staff, cfg)
        rows = units[half == test]
        out.loc[rows.index] = [any((dept[u], c) in mined for c in diagnoses.get(p, ()))
                               for u, p in zip(rows["user_id"], rows["patient_id"])]
    return out


def direct_evidence(units: pd.DataFrame, at: pd.Series, g, cfg: Config) -> tuple[list[bool], list[list[str]]]:
    """Whether a care team or an order explains each first access, inside the evidence window (spec 11.2)."""
    after = cfg["evidence_window"]["after_days"]
    direct, evidence = [], []
    for u, p, when in zip(units["user_id"], units["patient_id"], at):
        paths = evidence_paths(g, u, p, when, after)
        direct.append(any(k in paths for k in DIRECT))
        evidence.append(sorted(paths))
    return direct, evidence


def run_templates(run_dir: str | Path, cfg: Config) -> pd.DataFrame:
    """System 2. Whether each first access is explained, and by what."""
    t = load_visible(run_dir, "templates")
    g = build_graph(t, cfg["proximity_miles"])
    units = first_access_units(t["events"], t["staff"], cfg["window_start"])
    at = pd.to_datetime(units["event_id"].map(t["events"].set_index("event_id")["timestamp"]))
    units["direct"], units["evidence"] = direct_evidence(units, at, g, cfg)
    units["responsible"] = responsible_out_of_sample(units, t["encounters"], t["staff"], cfg).to_numpy()
    return units[["user_id", "patient_id", "week", "day", "event_id", "direct", "responsible", "evidence"]]


def expected_top_k(day, score, hit, k: int) -> tuple[float, float]:
    """Expected number of hits and of alerts shown when a reviewer takes the
    k highest-scoring alerts each day and ties are broken at random."""
    frame = pd.DataFrame({"day": np.asarray(day), "score": np.asarray(score, dtype=float), "hit": np.asarray(hit, dtype=float)})
    hits = shown = 0.0
    for _, alerts in frame.groupby("day"):
        left = k
        for _, tie in sorted(alerts.groupby("score"), key=lambda x: -x[0]):
            take = min(left, len(tie))
            hits += take * tie["hit"].mean()
            shown += take
            left -= take
            if left == 0:
                break
    return hits, shown
