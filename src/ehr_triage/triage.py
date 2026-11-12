"""Systems 3 and 4: an LLM that triages a first access and drafts the case summary.

System 3 (`llm_raw`) is shown log lines only. System 4 (`llm_graph`) is
shown the same lines plus facts from the identity graph. Both get their
data through `graph.load_visible`, so neither can reach labels/.

Every fact shown to the model carries an ID (L1, L2 for log lines, G1, G2
for graph facts). The summary must cite those IDs, which is what makes
faithfulness checkable.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

import pandas as pd

from .baselines import first_access_units
from .config import Config, load_config
from .graph import build_graph, evidence_paths, load_visible

DISPOSITIONS = ["clear", "verify", "escalate"]
PRIORITIES = ["low", "medium", "high"]

INSTRUCTIONS = """You are assisting a hospital Privacy Officer. One user opened one patient's record. \
Decide what the officer should do with it and draft the case summary.

Use only the numbered facts below. Every claim you make must cite the fact IDs it rests on. \
If the facts do not settle the question, say so. Do not guess a purpose or a relationship that no fact shows.

Dispositions:
- clear: the facts show a job-related purpose, or nothing in them calls for review
- verify: the facts neither show a purpose nor show a concern that stands on its own. Ask the supervisor
- escalate: the facts show a concern, such as a personal connection with no job-related purpose

Answer with one JSON object and nothing else:
{"what_happened": "one or two sentences",
 "purpose_evidence": [{"claim": "...", "sources": ["L1", "G2"]}],
 "risk_indicators": [{"claim": "...", "sources": ["G4"]}],
 "missing_information": ["what the officer would need to confirm"],
 "disposition": "clear | verify | escalate",
 "priority": "low | medium | high",
 "rationale": "at most 60 words"}"""


class Views:
    """Fast lookups into the log and, for system 4, the identity graph."""

    def __init__(self, run_dir: str | Path, system: str, cfg: Config):
        self.system, self.cfg = system, cfg
        self.t = load_visible(run_dir, system)
        ev = self.t["events"]
        since = pd.to_datetime(ev["timestamp"]) - pd.Timestamp(cfg["window_start"])
        self.events = ev.assign(week=since.dt.days // 7, t=since.dt.total_seconds())
        self.by_user = self.events.groupby("user_id").indices
        self.by_patient_week = self.events.groupby(["patient_id", "week"]).indices
        if system == "llm_graph":
            t = self.t
            self.graph = build_graph(t, cfg["proximity_miles"])
            self.staff = t["staff"].set_index("user_id")
            self.dept = t["departments"].set_index("department_id")["name"].to_dict()
            self.device = t["devices"].set_index("device_id")
            self.action = t["actions"].set_index("action_code")["category"].to_dict()
            self.vip = set(t["patients"].loc[t["patients"]["vip"].astype(str) == "True", "patient_id"])
            self.schedule = {u: g for u, g in t["schedule"].groupby("user_id")}
            seen = t["encounters"].rename(columns={"class": "kind"})
            self.encounters = {p: g for p, g in seen.groupby("patient_id")}


def case_units(run_dir: str | Path, cfg: Config) -> pd.DataFrame:
    """The first accesses to triage. Chosen by the harness, the same for
    every system, so the systems are compared on identical cases."""
    t = load_visible(run_dir, "rules")
    return first_access_units(t["events"], t["staff"], cfg["window_start"])


def log_facts(v: Views, user: str, patient: str, week: int) -> list[tuple[str, str]]:
    """The access under review, what else the user did around it, and who else opened the patient that week.

    The user's other lines are the ones nearest the access in time, within
    a fixed number of hours either side. A calendar day would cut a night
    shift in two.
    """
    cap, hours = v.cfg["triage"]["context_lines"], v.cfg["triage"]["context_hours"]
    e = v.events
    theirs = e.iloc[v.by_patient_week.get((patient, week), [])]
    case = theirs[theirs["user_id"] == user]
    away = (e["t"].iloc[v.by_user[user]] - case["t"].iloc[0]).abs()
    mine = e.loc[away.index[(away <= hours * 3600).to_numpy()]]
    mine = mine[mine["patient_id"] != patient]
    around = mine.loc[away[mine.index].sort_values(kind="stable").index[:cap]].sort_values("event_id")
    others = theirs[theirs["user_id"] != user].head(cap)
    facts, n = [], 0
    for title, rows, more in (
        ("the access under review", case.head(cap), len(case) - cap),
        (f"the same user, within {hours} hours, other patients", around, len(mine) - len(around)),
        ("the same patient, same week, other users", others, len(theirs) - len(case) - len(others)),
    ):
        facts.append(("", f"-- {title}" + (f" ({more} more lines not shown)" if more > 0 else "")))
        for r in rows.itertuples():
            n += 1
            facts.append((f"L{n}", f"{r.timestamp} user {r.user_id} patient {r.patient_id} device {r.device_id} "
                                   f"action {r.action_code} {r.duration_s}s"))
    return facts


def graph_facts(v: Views, user: str, patient: str, day: int, case_lines: list[str]) -> list[tuple[str, str]]:
    """Who the user is, where the patient was seen, and every connection between them."""
    out: list[str] = []
    at = pd.Timestamp(case_lines[0].split(" ")[0])
    s = v.staff.loc[user]
    out.append(f"user {user} is a {s['role']} in {s['department_id']} ({v.dept[s['department_id']]}), {s['employment']}, "
               f"usual shift {s['usual_shift']}")
    date = (pd.Timestamp(v.cfg["window_start"]) + pd.Timedelta(days=int(day))).strftime("%Y-%m-%d")
    shifts = v.schedule.get(user)
    near = shifts[(shifts["shift_start"].str[:10] <= date) & (shifts["shift_end"].str[:10] >= date)] if shifts is not None else []
    for r in getattr(near, "itertuples", lambda: [])():
        out.append(f"schedule: {user} is scheduled {r.shift_start} to {r.shift_end} in {r.department_id}")
    if not len(near):
        out.append(f"schedule: {user} has no scheduled shift on {date}")
    seen = v.encounters.get(patient)
    if seen is None:
        out.append(f"patient {patient} has no encounter in the period")
    else:
        gap = (pd.to_datetime(seen["start"]) - at).abs()  # show the encounters nearest the access
        for r in seen.loc[gap.sort_values().index[: v.cfg["triage"]["encounters_shown"]]].sort_values("start").itertuples():
            out.append(f"encounter {r.encounter_id}: {patient} {r.kind} in {r.department_id} "
                       f"{r.start} to {r.stop}" + (f", reason {r.reason_description}" if r.reason_description else ""))
    if patient in v.vip:
        out.append(f"patient {patient} carries the VIP flag")
    paths = evidence_paths(v.graph, user, patient, at)
    for name, nodes in paths.items():
        out.append(f"connection '{name}': " + " - ".join(f"{kind} {ident}" for kind, ident in nodes))
    if not paths:
        out.append(f"the identity graph holds no connection between {user} and {patient}")
    devices = sorted({line.split(" device ")[1].split(" ")[0] for line in case_lines})
    for d in devices:
        out.append(f"device {d} is a {v.device.loc[d, 'kind']} in {v.device.loc[d, 'department_id']}")
    actions = sorted({line.split(" action ")[1].split(" ")[0] for line in case_lines})
    out.append("action categories: " + ", ".join(f"{a} {v.action[a]}" for a in actions))
    return [(f"G{i + 1}", text) for i, text in enumerate(out)]


def build_prompt(v: Views, unit) -> tuple[str, list[str]]:
    """The prompt for one first access, and the fact IDs it contains."""
    facts = log_facts(v, unit.user_id, unit.patient_id, unit.week)
    if v.system == "llm_graph":
        case_lines = []
        for ident, text in facts[1:]:
            if not ident:
                break
            case_lines.append(text)
        facts += [("", "-- identity graph")] + graph_facts(v, unit.user_id, unit.patient_id, unit.day, case_lines)
    body = "\n".join(f"{ident} | {text}" if ident else text for ident, text in facts)
    ids = [ident for ident, _ in facts if ident]
    return f"{INSTRUCTIONS}\n\nUser {unit.user_id}, patient {unit.patient_id}.\n\nFacts:\n{body}", ids


def parse_summary(text: str, fact_ids: list[str]) -> dict:
    """Read the model's JSON and check it against the template.

    Returns the summary with `valid`, `problems`, and `cited_ok`, the share
    of claims whose cited IDs all exist among the facts shown. A claim with
    no source, or with a made-up ID, counts against it.
    """
    start, end = text.find("{"), text.rfind("}")
    try:
        s = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return {"valid": False, "problems": ["not JSON"], "cited_ok": 0.0}
    problems = []
    if s.get("disposition") not in DISPOSITIONS:
        problems.append("disposition")
    if s.get("priority") not in PRIORITIES:
        problems.append("priority")
    for field in ("what_happened", "rationale"):
        if not isinstance(s.get(field), str) or not s[field].strip():
            problems.append(field)
    claims = []
    for field in ("purpose_evidence", "risk_indicators"):
        items = s.get(field)
        if not isinstance(items, list):
            problems.append(field)
            continue
        claims += [c for c in items if isinstance(c, dict)]
    known = set(fact_ids)
    good = [bool(c.get("sources")) and all(x in known for x in c["sources"]) for c in claims]
    s.update(valid=not problems, problems=problems, n_claims=len(claims),
             cited_ok=sum(good) / len(good) if good else 1.0)
    return s


def call_model(prompt: str, cfg: Config) -> tuple[str, dict]:
    """One chat completion against an OpenAI-compatible endpoint.

    The endpoint, key and model come from the environment, so no secret is
    ever written to the repo: EHR_TRIAGE_BASE_URL, EHR_TRIAGE_API_KEY and
    EHR_TRIAGE_MODEL.
    """
    body = {"model": os.environ["EHR_TRIAGE_MODEL"], "temperature": cfg["triage"]["temperature"],
            "max_tokens": cfg["triage"]["max_output_tokens"], "messages": [{"role": "user", "content": prompt}]}
    request = urllib.request.Request(
        os.environ["EHR_TRIAGE_BASE_URL"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["EHR_TRIAGE_API_KEY"], "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        reply = json.load(response)
    return reply["choices"][0]["message"]["content"], reply.get("usage", {})


def estimate_tokens(text: str) -> int:
    """Rough token count. IDs and timestamps split into many tokens, so this
    divides by 3 characters where prose would be nearer 4."""
    return len(text) // 3


def dry_run(run_dir: str | Path, cfg: Config, sample: int = 400) -> dict:
    """Average prompt size per system, measured on real prompts, with no model call."""
    out = {}
    every = case_units(run_dir, cfg)
    units = every.sample(min(sample, len(every)), random_state=0)
    for system in ("llm_raw", "llm_graph"):
        v = Views(run_dir, system, cfg)
        sizes = [estimate_tokens(build_prompt(v, u)[0]) for u in units.itertuples()]
        out[system] = {"first_accesses": len(every), "mean_prompt_tokens": int(sum(sizes) / len(sizes)),
                       "max_prompt_tokens": max(sizes)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", default=None)
    ap.add_argument("--config", default="config/calibration.yaml")
    ap.add_argument("--show", default=None, help="print the prompt for one system, llm_raw or llm_graph")
    args = ap.parse_args()
    cfg = load_config(args.config)
    run = args.run or f"data/generated/seed{cfg['seed']}"
    if args.show:
        v = Views(run, args.show, cfg)
        print(build_prompt(v, next(case_units(run, cfg).sample(1, random_state=1).itertuples()))[0])
    else:
        print(json.dumps(dry_run(run, cfg), indent=2))


if __name__ == "__main__":
    main()
