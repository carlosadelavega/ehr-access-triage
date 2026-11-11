"""The identity graph and what each system under test is allowed to read.

The graph holds who a user is and how they connect to a patient: their own
record, their department, their care teams and orders, and the people they
share a surname, a street or a neighborhood with. It is built only from the
tables passed in, so a system that may not read a table cannot get its edges.
Nothing here reads labels/.
"""
from __future__ import annotations

from pathlib import Path

import networkx as nx
import pandas as pd

from .population import miles_between, street_of

# Section 2 of the spec. Every system also reads the log.
VISIBLE = {
    "rules": ["staff", "patients"],
    "templates": ["staff", "departments", "encounters", "care_team", "orders"],
    "llm_raw": [],
    "llm_graph": ["staff", "patients", "departments", "devices", "schedule", "encounters", "care_team", "orders", "actions"],
}


def load_visible(run_dir: str | Path, system: str) -> dict[str, pd.DataFrame]:
    """The log plus the context tables this system may read."""
    run = Path(run_dir)
    tables = {"events": pd.read_csv(run / "logs" / "access_events.csv", keep_default_na=False)}
    for name in VISIBLE[system]:
        tables[name] = pd.read_csv(run / "context" / f"{name}.csv", keep_default_na=False)
    return tables


def U(x): return ("user", x)
def P(x): return ("patient", x)
def D(x): return ("department", x)
def E(x): return ("encounter", x)


def build_graph(t: dict[str, pd.DataFrame], proximity_miles: float) -> nx.MultiDiGraph:
    """Typed edges between users, patients, departments and encounters."""
    g = nx.MultiDiGraph()
    staff = t.get("staff")
    if staff is not None:
        for row in staff.itertuples():
            g.add_edge(U(row.user_id), P(row.patient_id), key="is")
            g.add_edge(U(row.user_id), D(row.department_id), key="works_in")
            g.add_edge(P(row.patient_id), D(row.department_id), key="employed_in")
    if staff is not None and "patients" in t:
        people = t["patients"].set_index("patient_id")
        street = (people["address"].map(street_of) + "|" + people["city"]).to_numpy()
        surname = people["last_name"].to_numpy()
        lat, lon = people["lat"].to_numpy(float), people["lon"].to_numpy(float)
        ids = people.index.to_numpy()
        where = {pid: i for i, pid in enumerate(ids)}
        for row in staff.itertuples():
            me = where[row.patient_id]
            miles = miles_between(lat[me], lon[me], lat, lon)
            for kind, mask in (("same_surname", surname == surname[me]), ("same_street", street == street[me]),
                               ("lives_near", miles <= proximity_miles)):
                for i in mask.nonzero()[0]:
                    if i != me:
                        g.add_edge(U(row.user_id), P(ids[i]), key=kind)
        for row in t["patients"].itertuples():
            if row.emergency_contact_id:
                g.add_edge(P(row.patient_id), P(row.emergency_contact_id), key="emergency_contact")
    if "encounters" in t:
        for row in t["encounters"].itertuples():
            g.add_edge(P(row.patient_id), E(row.encounter_id), key="had")
            g.add_edge(E(row.encounter_id), D(row.department_id), key="in")
    if "care_team" in t:
        for row in t["care_team"].itertuples():
            g.add_edge(U(row.user_id), E(row.encounter_id), key="care_team", role=row.team_role)
    if "orders" in t:
        for row in t["orders"].itertuples():
            if row.ordered_by:
                g.add_edge(U(row.ordered_by), E(row.encounter_id), key="ordered", kind=row.kind)
            g.add_edge(E(row.encounter_id), D(row.performing_department_id), key="order_to", kind=row.kind)
    return g


def evidence_paths(g: nx.MultiDiGraph, user_id: str, patient_id: str) -> dict[str, list]:
    """Every known kind of connection between this user and this patient.

    Returns {evidence name: path}, where a path is the list of nodes walked.
    These paths are what the rules and templates test for, and what the
    grounded LLM will be shown.
    """
    u, p = U(user_id), P(patient_id)
    found: dict[str, list] = {}
    if u not in g or p not in g:
        return found

    def has(a, b, key):
        return g.has_edge(a, b, key=key)

    departments = [d for d in g.successors(u) if has(u, d, "works_in")]
    if has(u, p, "is"):
        found["self"] = [u, p]
    for name, key in (("last name", "same_surname"), ("same street", "same_street"), ("proximity", "lives_near")):
        if has(u, p, key):
            found[name] = [u, p]
    workplaces = [d for d in g.successors(p) if d[0] == "department" and has(p, d, "employed_in")]
    if workplaces and not has(u, p, "is"):
        found["organization co-worker"] = [u, p, workplaces[0]]
        shared = [d for d in workplaces if d in departments]
        if shared:
            found["department co-worker"] = [u, shared[0], p]
    own = [x for x in g.successors(u) if has(u, x, "is")]
    for me in own:
        if has(me, p, "emergency_contact") or has(p, me, "emergency_contact"):
            found["emergency contact"] = [u, me, p]
    for e in g.successors(p):
        if e[0] != "encounter":
            continue
        if has(u, e, "care_team"):
            found.setdefault("care team", [u, e, p])
        if has(u, e, "ordered"):
            found.setdefault("ordered", [u, e, p])
        for d in departments:
            if has(e, d, "in"):
                found.setdefault("department of encounter", [u, d, e, p])
            if has(e, d, "order_to"):
                found.setdefault("order sent to department", [u, d, e, p])
    return found
