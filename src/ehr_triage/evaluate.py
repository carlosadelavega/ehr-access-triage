"""Score the systems against the ground truth.

This is the only module besides the calibration report that reads labels/.
The systems themselves run first, on what they are allowed to see, and
their output is joined to the truth afterwards. It also picks the cases
the LLM systems triage, since a stratified sample needs the truth.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .baselines import expected_top_k, run_rules, run_templates
from .config import Config, load_config
from .triage import DISPOSITIONS, PRIORITIES, SYSTEM_NAMES as LLM_NAMES, SYSTEMS, VERDICTS, Views, case_facts, cited, claims_of, log_facts, read_answers, read_cases

HARD_NEGATIVES = {
    "consult": "H1 consult",
    "coverage": "H2 coverage",
    "undocumented_team": "H3 undocumented care team",
    "back_office": "H5 back office",
    "hospital_wide": "H6 hospital-wide role",
    "order": "order handled by a department",
    "care_team": "care team, on another encounter",
}
LEGITIMATE = {
    "direct": "explained by direct evidence",
    "H4a": "H4a documented, relationship present",
    "H4b": "H4b undocumented, relationship present",
    **HARD_NEGATIVES,
}
TARGETS = ["M1", "M2a", "M2b", "M3", "M4"]  # the scenarios that get covering accesses
MATRIX_ROWS = [  # the rows of the expected-outcome matrix in spec 5.2, in its order
    ("M1", "M1 coworker snooping"),
    ("M2a", "M2a relative in the same household"),
    ("M2b+", "M2b relative elsewhere, emergency contact recorded"),
    ("M2b-", "M2b relative elsewhere, no emergency contact"),
    ("M3", "M3 neighbor"),
    ("M4", "M4 VIP"),
    ("M5", "M5 curiosity after a news event"),
    ("M6a", "M6a shared credentials, overlap"),
    ("M6b", "M6b shared credentials, off shift"),
    ("own", "M1, M3 or M4 with the target seen in the actor's department"),
    ("consult", "H1 consult"),
    ("H2a", "H2a coverage, documented"),
    ("H2b", "H2b coverage, undocumented"),
    ("undocumented_team", "H3 undocumented care team"),
    ("H4a", "H4a documented care, relationship present"),
    ("H4b", "H4b undocumented work, relationship present"),
    ("back_office", "H5 back-office work"),
    ("hospital_wide", "H6 hospital-wide role"),
]


def truth(run_dir: str | Path, units: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """What each first access really was."""
    run = Path(run_dir)
    events = pd.read_csv(run / "logs" / "access_events.csv", keep_default_na=False)
    labels = pd.read_csv(run / "labels" / "event_labels.csv", keep_default_na=False)
    e = events.merge(labels, on="event_id")
    e["week"] = (pd.to_datetime(e["timestamp"]) - pd.Timestamp(cfg["window_start"])).dt.days // 7
    key = ["week", "user_id", "patient_id"]
    injected = e[e["scenario_id"] != ""].drop_duplicates(key).set_index(key)
    misuse, scenario = injected["reason"], injected["scenario_id"]
    first = e.drop_duplicates(key).set_index(key)
    index = pd.MultiIndex.from_frame(units[key])
    out = units.copy()
    out["misuse"] = misuse.reindex(index).fillna("").to_numpy()
    out["scenario_id"] = scenario.reindex(index).fillna("").to_numpy()
    out["reason"] = first["reason"].reindex(index).to_numpy()
    out["documented"] = first["documented"].reindex(index).to_numpy()
    return out


def variants(u: pd.DataFrame) -> dict[str, tuple[pd.Series, pd.Series]]:
    """Each system as (alert raised, priority score). Unranked systems score 0."""
    any_rule = u["rules"].map(len) > 0
    four = u["rules"].map(lambda m: any(r != "organization co-worker" for r in m))
    four_score = u["rules"].map(lambda m: len([r for r in m if r != "organization co-worker"]) > 0) * u["rule_score"]
    flat = pd.Series(0.0, index=u.index)
    return {
        "Rules, all five": (any_rule, u["rule_score"]),
        "Rules, without organization co-worker": (four, four_score),
        "Templates, direct evidence": (~u["direct"], flat),
        "Templates, direct and responsibility": (~(u["direct"] | u["responsible"]), flat),
        "Rules, then direct evidence": (four & ~u["direct"], four_score),
    }


def table(header: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows] + [""]


def score_run(run_dir: str | Path, cfg: Config) -> pd.DataFrame:
    """Run both baselines on a run and join the truth."""
    rules = run_rules(run_dir, cfg)
    templates = run_templates(run_dir, cfg)
    assert (rules["event_id"] == templates["event_id"]).all()
    return truth(run_dir, rules.assign(direct=templates["direct"], responsible=templates["responsible"]), cfg)


def write_results(run_dir: str | Path, cfg: Config, out_path: str | Path, covered_dir: str | Path | None = None) -> dict:
    u = score_run(run_dir, cfg)
    is_cover = u["misuse"] == "cover"
    assert not is_cover.any(), "the main run must be the one without covering accesses"
    systems = variants(u)
    names = list(systems)
    is_misuse = u["misuse"] != ""
    days = cfg["window_days"]
    relationship = u["rules"].map(lambda m: any(r != "organization co-worker" for r in m))

    lines = [
        "# Baseline results: rules and explanation templates",
        "",
        "Generated by `python -m ehr_triage.evaluate`. Do not edit by hand.",
        "",
        f"The unit is a first access: one user opening one patient for the first time in a week, self-accesses left out. "
        f"This run has {len(u):,} of them over {days} days, of which {int(is_misuse.sum())} belong to an injected misuse "
        "scenario. Each system ran on the log and its permitted context tables only. The truth was joined afterwards.",
        "",
        "## Alerts raised",
        "",
    ]
    rows = []
    for name, (alert, _) in systems.items():
        caught = int((alert & is_misuse).sum())
        rows.append([name, f"{int(alert.sum()):,}", f"{alert.sum() / days:,.0f}", f"{alert.mean():.1%}",
                     f"{caught} of {int(is_misuse.sum())}", f"{caught / max(alert.sum(), 1):.2%}"])
    lines += table(["System", "Alerts", "Per day", "Share of first accesses", "Misuse caught", "Precision"], rows)

    lines += ["## Misuse caught, by scenario", "", "First accesses flagged, out of those belonging to the scenario.", ""]
    rows = []
    for kind in sorted(u.loc[is_misuse, "misuse"].unique()):
        m = u["misuse"] == kind
        rows.append([kind] + [f"{int((systems[n][0] & m).sum())} of {int(m.sum())}" for n in names])
    lines += table(["Scenario"] + names, rows)

    lines += ["## False alerts, by kind of legitimate access", "",
              "Share of legitimate first accesses that each system flags. A hard negative has no direct evidence in context.", ""]
    legit = ~is_misuse
    groups = {"explained by direct evidence": legit & u["direct"]}
    for reason, label in HARD_NEGATIVES.items():
        groups[label] = legit & ~u["direct"] & (u["reason"] == reason)
    groups["H4a documented, relationship present"] = legit & u["direct"] & relationship
    groups["H4b undocumented, relationship present"] = legit & ~u["direct"] & relationship
    rows = [[label, f"{int(m.sum()):,}"] + [f"{(systems[n][0] & m).sum() / max(m.sum(), 1):.0%}" for n in names]
            for label, m in groups.items() if m.sum()]
    lines += table(["Legitimate access", "Count"] + names, rows)

    lines += ["## Within a daily review budget", "",
              "A reviewer takes the highest-priority alerts each day, up to the budget. Rules are ranked by how far above "
              "chance each rule fires in Hedda et al. Template alerts have no ranking, so ties are broken at random and the "
              "figures are expected values. Recall is over all misuse first accesses.", ""]
    rows = []
    for k in cfg["review_budget_per_day"]:
        for name, (alert, score) in systems.items():
            a = u[alert]
            hits, shown = expected_top_k(a["day"], score[alert], is_misuse[alert], k)
            rows.append([k, name, f"{shown:,.0f}", f"{hits:.1f}", f"{hits / max(shown, 1):.2%}", f"{hits / is_misuse.sum():.0%}"])
    lines += table(["Budget per day", "System", "Reviewed", "Misuse found", "Precision", "Recall"], rows)

    if covered_dir is not None and Path(covered_dir).exists():
        c = score_run(covered_dir, cfg)
        covered = variants(c)
        before, after, cover = u["misuse"].isin(TARGETS), c["misuse"].isin(TARGETS), c["misuse"] == "cover"
        lines += ["## With covering accesses", "",
                  f"A second run is identical except that each M1-M4 actor first makes {cfg['covering']['accesses']} "
                  "covering accesses, chosen by the heuristic attack of Xu et al. 2023. Target accesses flagged without "
                  "and with them, and how many of the covering accesses themselves are flagged.", ""]
        rows = [[n, f"{int((systems[n][0] & before).sum())} of {int(before.sum())}",
                 f"{int((covered[n][0] & after).sum())} of {int(after.sum())}",
                 f"{int((covered[n][0] & cover).sum())} of {int(cover.sum())}"] for n in names]
        lines += table(["System", "Targets flagged, no cover", "Targets flagged, with cover", "Covering accesses flagged"], rows)
    Path(out_path).write_text("\n".join(lines))
    return {"units": len(u), "misuse_units": int(is_misuse.sum())}


# ---- the case set for the LLM systems ----

def strata(u: pd.DataFrame) -> pd.Series:
    """One kind per first access, used to sample cases and to report by.

    Misuse keeps its scenario type. A legitimate access is H4a or H4b when
    a relationship rule matches it, otherwise `direct` when direct evidence
    explains it, otherwise the mechanism that produced it.
    """
    relationship = u["rules"].map(lambda m: any(r != "organization co-worker" for r in m))
    kind = np.where(relationship, np.where(u["direct"], "H4a", "H4b"), np.where(u["direct"], "direct", u["reason"]))
    return pd.Series(np.where(u["misuse"] != "", u["misuse"], kind), index=u.index)


def build_cases(run_dir: str | Path, cfg: Config, covered_dir: str | Path | None = None) -> dict:
    """Choose the first accesses the LLM systems will triage.

    Triaging all of them costs more than the question needs, so this takes
    every misuse first access and a fixed number of each legitimate kind.
    `cases.csv` goes beside the logs with nothing in it but who opened whom
    and when, in shuffled order. What each case really was, and how many
    first accesses of its kind exist, goes under labels/ for scoring.

    The paired run gets the same M1-M4 cases under the same IDs, plus a
    sample of the covering accesses themselves.
    """
    plan = cfg["case_set"]
    rng = np.random.default_rng([cfg["seed"], 3])
    key = ["user_id", "patient_id", "week"]

    def draw(u, quota):
        picked = []
        for kind, rows in u.groupby("stratum", sort=True):
            n = min(len(rows), quota(kind))
            keep = np.sort(rng.choice(rows.index.to_numpy(), n, replace=False))
            picked.append(rows.loc[keep].assign(population=len(rows)))
        out = pd.concat(picked)
        return out.iloc[rng.permutation(len(out))].reset_index(drop=True)

    def save(folder, cases):
        cases[["case_id", "user_id", "patient_id", "week", "day"]].to_csv(Path(folder) / "cases.csv", index=False)
        cases[["case_id", "stratum", "population"]].to_csv(Path(folder) / "labels" / "case_truth.csv", index=False)
        return {"cases": len(cases), **cases["stratum"].value_counts().sort_index().to_dict()}

    u = score_run(run_dir, cfg)
    u["stratum"] = strata(u)
    misuse = set(u.loc[u["misuse"] != "", "stratum"])
    base = draw(u, lambda kind: len(u) if kind in misuse else plan["larger"].get(kind, plan["per_stratum"]))
    base["case_id"] = [f"C{i + 1:04d}" for i in range(len(base))]
    out = {str(run_dir): save(run_dir, base)}
    if covered_dir is not None and Path(covered_dir).exists():
        c = score_run(covered_dir, cfg)
        c["stratum"] = strata(c)
        cov = draw(c[c["misuse"].isin(TARGETS + ["cover"])], lambda kind: plan["covers"] if kind == "cover" else len(c))
        ids = base.set_index(key)["case_id"]
        cov["case_id"] = [f"K{i + 1:04d}" if r.stratum == "cover" else ids.loc[(r.user_id, r.patient_id, r.week)]
                          for i, r in enumerate(cov.itertuples())]
        out[str(covered_dir)] = save(covered_dir, cov)
    return out


def case_strata(run_dir: str | Path) -> pd.Series:
    """The kind of every case in the case set, by case ID, for code outside this module that may not read labels/."""
    truth = pd.read_csv(Path(run_dir) / "labels" / "case_truth.csv", keep_default_na=False)
    return truth.set_index("case_id")["stratum"]


# ---- scoring systems 3 and 4 ----

RATIONALE_WORDS = 60  # the summary template's limit on the rationale (spec 10)


def answers(results_dir: str | Path, name: str) -> pd.DataFrame | None:
    """One row per case from a results file, or None when the file is not there.

    An answer outside the template still has to be dealt with by someone,
    so it counts as `verify` at medium priority, whatever disposition it
    names. The template caps the rationale at 60 words, so a longer one does
    not fit it (spec 11.6).
    """
    path = Path(results_dir) / f"{name}.jsonl"
    if not path.exists():
        return None
    rows = []
    for line in path.read_text().splitlines():
        r = json.loads(line)
        s = r.get("summary", {})
        fits = bool(s.get("valid")) and len(str(s.get("rationale", "")).split()) <= RATIONALE_WORDS
        rows.append({
            "case_id": r["case_id"], "model": r["model"], "provider": r.get("provider", ""), "valid": fits,
            "disposition": s["disposition"] if fits and s.get("disposition") in DISPOSITIONS else "verify",
            "priority": s["priority"] if fits and s.get("priority") in PRIORITIES else "medium",
            "truncated": r["finish"] == "length", "verdicts": r.get("verdicts"), "absence": r.get("absence"),
            "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"],
            "reasoning_tokens": r["reasoning_tokens"], "usd": r["usd"]})
    return pd.DataFrame(rows).drop_duplicates("case_id", keep="last")  # a case sent again keeps its later answer


def matrix_cells(run_dir: str | Path, u: pd.DataFrame) -> pd.DataFrame:
    """Which row of the spec's expected-outcome matrix each first access falls in.

    The kinds are those of the case set, with two of them split where the
    spec predicts differently: M2b by whether the emergency contact is
    recorded, and coverage by whether the schedule documents it.
    `own_department` marks misuse whose target has an encounter in the
    actor's own department, which the spec leaves as an open question.
    """
    sc = pd.read_csv(Path(run_dir) / "labels" / "scenarios.csv", keep_default_na=False)
    by_pair = sc.drop_duplicates(["actor_user_id", "target_patient_id"]).set_index(["actor_user_id", "target_patient_id"])["evidence_in_context"]
    evidence = pd.Series(by_pair.reindex(pd.MultiIndex.from_frame(u[["user_id", "patient_id"]])).fillna("").to_numpy(), index=u.index)
    evidence = evidence.where(u["misuse"] != "", "")
    cell = strata(u)
    documented = u["documented"].astype(str) == "True"
    cell = cell.mask(cell == "coverage", pd.Series(np.where(documented, "H2a", "H2b"), index=u.index))
    cell = cell.mask(cell == "M2b", pd.Series(np.where(evidence.str.contains("emergency contact"), "M2b+", "M2b-"), index=u.index))
    return u[["user_id", "patient_id", "week"]].assign(cell=cell, own_department=evidence.str.contains("actor's department"))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a share of k out of n. It behaves on small counts, where the usual interval does not."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre, half = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - half) / (1 + z * z / n)), min(1.0, (centre + half) / (1 + z * z / n))


def mcnemar(first_only: int, second_only: int) -> float:
    """Two-sided exact McNemar test for two yes-or-no outcomes on the same cases.

    Only the cases where the two disagree carry information. If the two
    were equally likely to say yes, each disagreement would fall either
    way like a coin toss, so the p-value is a binomial tail.
    """
    n, k = first_only + second_only, min(first_only, second_only)
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def mantel_haenszel(tables: list[tuple[int, int, int, int]]) -> tuple[float, float]:
    """Cochran-Mantel-Haenszel test of one association across several 2x2 tables, with continuity correction.

    Each table is (a, b, c, d): group one yes and no, group two yes and no,
    for one stratum. Returns the common odds ratio (infinite when group two
    never says yes) and the two-sided p-value. Stratifying keeps a kind of
    access that has more of group one from passing for an effect of the group.
    """
    seen = expected = variance = top = bottom = 0.0
    for a, b, c, d in tables:
        n = a + b + c + d
        if n < 2:
            continue
        seen, expected = seen + a, expected + (a + b) * (a + c) / n
        variance += (a + b) * (c + d) * (a + c) * (b + d) / (n * n * (n - 1))
        top, bottom = top + a * d / n, bottom + b * c / n
    if variance == 0:
        return float("nan"), 1.0
    chi2 = max(abs(seen - expected) - 0.5, 0) ** 2 / variance
    return (top / bottom if bottom else float("inf")), math.erfc(math.sqrt(chi2 / 2))


def calls(path: str | Path) -> pd.DataFrame:
    """Every call recorded in a results file, including the earlier tries of a case that was sent again.

    `answers` keeps one row per case, which is right for scoring. Money
    and tokens were spent on every call, so cost is summed over these.
    """
    return pd.DataFrame([json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()])


def citation_counts(run_dir: str | Path, results_dir: str | Path, system: str, cfg: Config, pass_no: int = 1) -> dict:
    """The first faithfulness check, recounted from the stored claims and the facts each case was shown.

    A results file keeps the check as it stood when the answer was parsed,
    and pass 1 was parsed before a range of IDs counted as a citation.
    Recounting here puts every pass under the same rule and lets the
    report show both readings: `exist` counts ranges, `listed` does not.
    """
    units = {u.case_id: u for u in read_cases(run_dir).itertuples()}
    v = Views(run_dir, system, cfg)
    n = {"claims": 0, "exist": 0, "listed": 0}
    for row in read_answers(Path(results_dir) / f"{system}.pass{pass_no}.jsonl"):
        known = {ident for ident, _ in case_facts(v, units[row["case_id"]]) if ident}
        for claim in claims_of(row["summary"]):
            n["claims"] += 1
            n["exist"] += cited(claim, known)
            n["listed"] += cited(claim, known, ranges=False)
    return n


def scored_answers(run_dir: str | Path, results_dir: str | Path, system: str, pass_no: int = 1) -> pd.DataFrame | None:
    """A system's answers joined to the truth, each with the number of first accesses it stands for."""
    a = answers(results_dir, f"{system}.pass{pass_no}")
    if a is None or a.empty:
        return None
    run = Path(run_dir)
    truth = pd.read_csv(run / "labels" / "case_truth.csv", keep_default_na=False).merge(pd.read_csv(run / "cases.csv"), on="case_id")
    f = truth.merge(a, on="case_id")
    f["weight"] = f["population"] / f.groupby("stratum")["case_id"].transform("size")
    f["score"] = f["disposition"].map({"clear": 0, "verify": 1, "escalate": 2}) * 3 + f["priority"].map({"low": 0, "medium": 1, "high": 2})
    f["is_misuse"] = ~f["stratum"].isin(list(LEGITIMATE) + ["cover"])
    return f


def weighted_alerts(f: pd.DataFrame, alert: pd.Series) -> tuple[float, float]:
    """Alerts over the whole population, estimated from the sample, and the 95% margin on that estimate."""
    total = float((f["weight"] * alert).sum())
    variance = 0.0
    for _, rows in f.assign(alert=alert).groupby("stratum"):
        n, size, p = len(rows), rows["population"].iloc[0], rows["alert"].mean()
        if 1 < n < size:
            variance += size ** 2 * (1 - n / size) * p * (1 - p) / (n - 1)
    return total, 1.96 * variance ** 0.5


def average_day_top_k(score, hit, weight, k: int, days: int) -> tuple[float, float]:
    """Misuse found and alerts reviewed over the window when a reviewer takes
    the k highest-priority alerts a day and every day brings the average
    number of alerts at each priority. Ties are broken at random."""
    frame = pd.DataFrame({"score": np.asarray(score, dtype=float), "weight": np.asarray(weight, dtype=float)})
    frame["hits"] = np.asarray(hit, dtype=float) * frame["weight"]
    found = shown = 0.0
    left = float(k)
    for _, tie in sorted(frame.groupby("score"), key=lambda x: -x[0]):
        take = min(left, tie["weight"].sum() / days)
        found += take * tie["hits"].sum() / tie["weight"].sum()
        shown += take
        left -= take
        if left <= 0:
            break
    return found * days, shown * days


# ---- the matrix of spec 5.2, scored as fixed in spec 11.5 ----

MATRIX_SYSTEMS = {"rules": "Rules, all five", "templates": "Templates, direct and responsibility",
                  "llm_raw": LLM_NAMES["llm_raw"], "llm_graph": LLM_NAMES["llm_graph"]}
_CELLS = [cell for cell, _ in MATRIX_ROWS]
# Each prediction of spec 5.2 written as a code, one per row of MATRIX_ROWS in its order. Written on
# 15-16 Nov 2026, after pass 1 and before the corrected pass, from the definitions in 5.2 (spec 11.5).
PREDICTIONS = {
    #             M1      M2a     M2b+    M2b-     M3      M4      M5      M6a     M6b     own        H1         H2a      H2b      H3         H4a      H4b              H5       H6
    "rules":     ["flag", "flag", "miss", "miss",  "flag", "miss", "miss", "miss", "miss", "scenario", "clear",   "clear", "clear", "clear",   "false", "false",         "clear", "clear"],
    "templates": ["flag", "flag", "flag", "flag",  "flag", "flag", "flag", "flag", "flag", "miss",     "no test", "false", "false", "no test", "clear", "no test",       "false", "false"],
    "llm_raw":   ["miss", "miss", "miss", "miss",  "miss", "miss", "flag", "flag", "miss", "miss",     "no test", "no test", "no test", "no test", "clear", "no test",   "no test", "no test"],
    "llm_graph": ["flag", "flag", "flag", "verify", "flag", "flag", "flag", "flag", "flag", "open",    "clear",   "clear", "verify", "clear",   "clear", "clear or verify", "clear", "clear"],
}
PREDICTION_WORDS = {"flag": "flag", "miss": "miss", "clear": "clear", "false": "false alert", "verify": "say it cannot confirm",
                    "clear or verify": "clear, or say it cannot confirm", "no test": "no test", "open": "open question",
                    "scenario": "as each case's own scenario"}


def matches(code: str, flagged: np.ndarray, disposition: np.ndarray | None) -> np.ndarray:
    """Whether each case's outcome is the one the code predicts. `flagged` is a rule firing, an access left
    unexplained, or an answer of verify or escalate, which puts the access in front of a reviewer (spec 5.2)."""
    if code in ("flag", "false"):
        return flagged
    if code in ("miss", "clear"):
        return ~flagged
    if code == "verify":
        return disposition == "verify"
    if code == "clear or verify":
        return disposition != "escalate"
    raise ValueError(code)


def mark(code: str, matching: int, n: int) -> str:
    """Held at 90% or more of the cases matching, partly at 50-89%, contradicted below (spec 11.5)."""
    if code in ("no test", "open"):
        return PREDICTION_WORDS[code]
    if n == 0:
        return "no cases"
    share = matching / n
    return "held" if share >= 0.9 else "partly" if share >= 0.5 else "contradicted"


def matrix_scores(run_dir: str | Path, u: pd.DataFrame, frames: dict) -> pd.DataFrame:
    """Every cell of the matrix: cases, share flagged, share escalated, cases matching the prediction, and the mark."""
    cells = matrix_cells(run_dir, u)
    key = ["user_id", "patient_id", "week"]
    v = variants(u)
    counted = {"rules": (cells, v["Rules, all five"][0].to_numpy(), None),
               "templates": (cells, v["Templates, direct and responsibility"][0].to_numpy(), None)}
    for s, f in frames.items():
        mine = f[key].merge(cells, on=key, how="left").set_axis(f.index)
        d = f["disposition"].to_numpy()
        counted[s] = (mine, d != "clear", d)
    rows = []
    for system, (mine, flagged, disposition) in counted.items():
        for (cell, label), code in zip(MATRIX_ROWS, PREDICTIONS[system]):
            part = (mine["own_department"].astype(bool) if cell == "own" else mine["cell"] == cell).to_numpy()
            n = int(part.sum())
            if code == "scenario":  # each case takes the prediction of its own scenario
                own = mine["cell"].to_numpy()
                hit = sum(bool(matches(PREDICTIONS[system][_CELLS.index(own[i])], flagged[i:i + 1], None)[0])
                          for i in np.flatnonzero(part))
            elif code in ("no test", "open"):
                hit = 0
            else:
                d = disposition[part] if disposition is not None else None
                hit = int(matches(code, flagged[part], d).sum())
            rows.append({"system": system, "cell": cell, "label": label, "predicted": PREDICTION_WORDS[code], "code": code,
                         "cases": n, "flagged": int(flagged[part].sum()),
                         "escalated": int((disposition[part] == "escalate").sum()) if disposition is not None else None,
                         "matching": hit, "mark": mark(code, hit, n)})
    return pd.DataFrame(rows)


# ---- whether a scenario's defining cue reached the prompt (spec 11.3) ----

OVERLAP_MINUTES = 15  # two lines by one account this close, on devices in different departments, read as two people


def cue_shown(run_dir: str | Path, cfg: Config, u: pd.DataFrame, frames: dict) -> pd.DataFrame:
    """For every news-event and shared-credential case, whether its cue was among the lines each system saw.

    News event: a line by another user who opened the patient after the same news event.
    Shared credentials: two lines by the account within OVERLAP_MINUTES on devices in different
    departments, or a line under review outside every shift the account's owner was scheduled for.
    The schedule is shown only to the grounded system, so only it can see the second cue.
    """
    run = Path(run_dir)
    sc = pd.read_csv(run / "labels" / "scenarios.csv", keep_default_na=False)
    group = dict(zip(sc["scenario_id"], sc["group_id"]))
    actors = sc.groupby("group_id")["actor_user_id"].agg(set).to_dict()
    where = pd.read_csv(run / "context" / "devices.csv", keep_default_na=False).set_index("device_id")["department_id"].to_dict()
    schedule = pd.read_csv(run / "context" / "schedule.csv", keep_default_na=False)
    key = ["user_id", "patient_id", "week"]
    sid = u.set_index(key)["scenario_id"]
    f = frames["llm_graph"]
    cases = f[f["stratum"].isin(["M5", "M6a", "M6b"])]
    v = Views(run_dir, "llm_raw", cfg)
    rows = []
    for c in cases.itertuples():
        section, lines = None, []
        for ident, text in log_facts(v, c.user_id, c.patient_id, c.week):
            if not ident:
                section = text
                continue
            parts = text.split(" ")
            lines.append((section, pd.Timestamp(parts[0]), parts[2], parts[4], parts[6]))
        scenario = sid.get((c.user_id, c.patient_id, c.week), "")
        if c.stratum == "M5":
            others = actors.get(group.get(scenario, ""), set()) - {c.user_id}
            seen = any(user in others for sec, _, user, _, _ in lines if "other users" in sec)
            rows.append({"case_id": c.case_id, "stratum": c.stratum, "llm_raw": seen, "llm_graph": seen})
            continue
        mine = sorted((t, where.get(dev, "")) for sec, t, user, _, dev in lines if user == c.user_id and "other users" not in sec)
        overlap = any((b[0] - a[0]).total_seconds() <= OVERLAP_MINUTES * 60 and a[1] != b[1] for a, b in zip(mine, mine[1:]))
        shifts = schedule[schedule["user_id"] == c.user_id]
        starts, ends = pd.to_datetime(shifts["shift_start"]).to_numpy(), pd.to_datetime(shifts["shift_end"]).to_numpy()
        off = any(not ((starts <= np.datetime64(t)) & (np.datetime64(t) <= ends)).any()
                  for sec, t, user, _, _ in lines if "under review" in sec)
        rows.append({"case_id": c.case_id, "stratum": c.stratum, "llm_raw": overlap, "llm_graph": overlap or off})
    return pd.DataFrame(rows, columns=["case_id", "stratum", "llm_raw", "llm_graph"])


def repeat_agreement(run_dir: str | Path, results_dir: str | Path, system: str, passes: list[int]) -> pd.DataFrame:
    """Each case's kind, weight and whether it got the same disposition in every pass."""
    runs = [scored_answers(run_dir, results_dir, system, p) for p in passes]
    together = pd.concat([r.set_index("case_id")["disposition"] for r in runs], axis=1, join="inner")
    first = runs[0].set_index("case_id").loc[together.index]
    return first[["stratum", "weight", "is_misuse"]].assign(same=together.nunique(axis=1).eq(1))


def zero_bound(f: pd.DataFrame) -> float:
    """A 95% upper bound on population alerts when none was seen: each kind's size times the Wilson upper bound of 0 of n."""
    return float(sum(rows["population"].iloc[0] * wilson(0, len(rows))[1] for _, rows in f.groupby("stratum")))


def injection_of(run_dir: str | Path) -> dict:
    """The injection each misuse scenario row belongs to: the news event for M5, the scenario itself otherwise."""
    sc = pd.read_csv(Path(run_dir) / "labels" / "scenarios.csv", keep_default_na=False)
    return {r.scenario_id: (r.group_id if r.type == "M5" and r.group_id else r.scenario_id) for r in sc.itertuples()}


def write_llm_results(run_dir: str | Path, cfg: Config, out_path: str | Path, results_dir: str | Path,
                      covered_dir: str | Path | None = None, covered_results: str | Path | None = None) -> dict:
    frames = {s: scored_answers(run_dir, results_dir, s) for s in SYSTEMS}
    frames = {s: f for s, f in frames.items() if f is not None}
    if not frames:
        raise SystemExit(f"No answers under {results_dir}. Run the model first with: python -m ehr_triage.triage run")
    days = cfg["window_days"]
    sampled = pd.read_csv(Path(run_dir) / "labels" / "case_truth.csv", keep_default_na=False)
    u = score_run(run_dir, cfg)
    is_misuse = u["misuse"] != ""

    def llm_variants(fs):
        out = {}
        for s, f in fs.items():
            out[f"{LLM_NAMES[s]}, escalate"] = (f, f["disposition"] == "escalate")
            out[f"{LLM_NAMES[s]}, verify or escalate"] = (f, f["disposition"] != "clear")
        return out

    llm = llm_variants(frames)
    models = sorted({m for f in frames.values() for m in f["model"] if m})
    providers = sorted({p for f in frames.values() for p in f["provider"] if p})
    lines = [
        "# LLM triage results",
        "",
        "Generated by `python -m ehr_triage.evaluate llm`. Do not edit by hand.",
        "",
        f"Model {', '.join(models) or 'not recorded'}, served by {', '.join(providers) or 'a provider that was not recorded'}. "
        f"The systems triaged a sample of {len(sampled):,} first accesses out of "
        f"{len(u):,}, made of every misuse first access ({int(is_misuse.sum())}) and a fixed number of each legitimate kind. "
        "Figures for the whole population are estimates weighted back from that sample, and the margin given is a 95% "
        "interval from the sampling alone. It says nothing about how the model would do on a second run. "
        "An answer outside the template counts as verify, since someone still has to look at it. A count of zero is shown "
        "with a 95% upper bound, since seeing none in the sample does not mean there are none.",
        "",
        "The rules and templates rows are computed on every first access, as in `baseline-results.md`.",
        "",
        "## Alerts raised",
        "",
    ]
    rows = []
    for name, (alert, _) in variants(u).items():
        caught = int((alert & is_misuse).sum())
        rows.append([name, f"{alert.sum() / days:,.0f}", f"{alert.mean():.1%}", f"{caught} of {int(is_misuse.sum())}",
                     f"{caught / max(alert.sum(), 1):.2%}"])
    for name, (f, alert) in llm.items():
        total, margin = weighted_alerts(f, alert)
        caught = int((alert & f["is_misuse"]).sum())
        per_day = f"0 (95% upper bound {zero_bound(f) / days:,.0f})" if total == 0 else f"{total / days:,.0f} ± {margin / days:,.0f}"
        rows.append([name, per_day, f"{total / f['weight'].sum():.1%}",
                     f"{caught} of {int(f['is_misuse'].sum())}", f"{caught / max(total, 1):.2%}"])
    lines += table(["System", "Alerts per day", "Share of first accesses", "Misuse caught", "Precision"], rows)

    lines += ["## Dispositions, by kind of access", "",
              "Cases sent to clear, verify and escalate, in that order. These are counts in the sample, not weighted.", ""]
    kinds = sorted(set(sampled["stratum"]) - set(LEGITIMATE)) + [k for k in LEGITIMATE if k in set(sampled["stratum"])]
    rows = []
    for kind in kinds:
        row = [LEGITIMATE.get(kind, kind), int((sampled["stratum"] == kind).sum())]
        for f in frames.values():
            d = f.loc[f["stratum"] == kind, "disposition"].value_counts()
            row.append(" / ".join(str(int(d.get(x, 0))) for x in DISPOSITIONS))
        rows.append(row)
    lines += table(["Kind of access", "Cases"] + [LLM_NAMES[s] for s in frames], rows)

    staff = set(pd.read_csv(Path(run_dir) / "context" / "staff.csv", keep_default_na=False)["patient_id"])
    n_patients = len(pd.read_csv(Path(run_dir) / "context" / "patients.csv", usecols=["patient_id"]))
    on_staff = u["patient_id"].isin(staff)
    lines += ["## When the patient is also staff", "",
              f"{len(staff):,} of the {n_patients:,} patients in this run are staff, and {on_staff.mean():.1%} of first accesses "
              "open the chart of a staff member. In Hedda et al. the organization co-worker rule matches "
              f"{cfg.targets['rule_match_share']['value']['organization co-worker']:.2%} of first accesses and fires no more often than chance. "
              "So this run opens staff charts about three times as often as their data, a calibration gap that is reported "
              "and not tuned, and any alert count that rests on staff charts is inflated with it. The tables split every "
              "system's alerts by whether the patient is staff. The split was added after pass 1 showed that most of the "
              "grounded LLM's escalations fall on staff charts. A system that sees context can tell that a patient is "
              "staff. The LLM on log lines cannot. Margins are 95% intervals from the sampling alone.", ""]
    rows = []
    for name, (alert, _) in variants(u).items():
        row = [name]
        for part in (on_staff, ~on_staff):
            caught = int((alert & part & is_misuse).sum())
            row += [f"{(alert & part).sum() / days:,.0f}", f"{caught} of {int((part & is_misuse).sum())}",
                    f"{caught / max((alert & part).sum(), 1):.2%}"]
        rows.append(row)
    for name, (f, alert) in llm.items():
        row = [name]
        for part in (f["patient_id"].isin(staff), ~f["patient_id"].isin(staff)):
            total, margin = weighted_alerts(f, alert & part)
            caught = int((alert & part & f["is_misuse"]).sum())
            row += [f"{total / days:,.0f} ± {margin / days:,.0f}", f"{caught} of {int((part & f['is_misuse']).sum())}",
                    f"{caught / max(total, 1):.2%}"]
        rows.append(row)
    lines += table(["System", "Staff charts, alerts per day", "Misuse caught", "Precision",
                    "Other charts, alerts per day", "Misuse caught", "Precision"], rows)
    lines += ["Dispositions by kind of access, split the same way. Cases sent to clear, verify and escalate, in that order, "
              "as counts in the sample.", ""]
    rows = []
    for kind in kinds:
        row = [LEGITIMATE.get(kind, kind)]
        for is_staff in (True, False):
            cells = []
            for f in frames.values():
                d = f.loc[(f["stratum"] == kind) & (f["patient_id"].isin(staff) == is_staff), "disposition"].value_counts()
                cells.append(" / ".join(str(int(d.get(x, 0))) for x in DISPOSITIONS))
            row += [int(d.sum())] + cells
        rows.append(row)
    names = [LLM_NAMES[s] for s in frames]
    lines += table(["Kind of access", "Staff charts"] + names + ["Other charts"] + names, rows)

    cells = matrix_cells(run_dir, u)
    key = ["user_id", "patient_id", "week"]
    counted = {"Rules, all five": (cells, None, variants(u)["Rules, all five"][0]),
               "Templates, direct and responsibility": (cells, None, variants(u)["Templates, direct and responsibility"][0])}
    for s, f in frames.items():
        mine = f[key].merge(cells, on=key, how="left").set_axis(f.index)
        counted[LLM_NAMES[s]] = (mine, f["disposition"] == "verify", f["disposition"] == "escalate")
    lines += ["## Every row of the expected-outcome matrix", "",
              "Section 5.2 of the spec predicts an outcome for each system on each kind of access. This table counts what "
              "happened, so each prediction can be checked against a number. Left alone means that no rule fired, that an "
              "explanation was found, or that the model answered clear. Alerted means that a rule fired, that no explanation "
              "was found, or that the model answered escalate. Only the two LLM systems can answer verify. The rules and the "
              "templates are counted on every first access and the LLM systems on the cases they were sent, so the number of "
              "cases differs between them. Counts are not weighted. The last column is the share alerted with its 95% Wilson "
              "interval, which shows how little ten cases can pin down.", "",
              "Two kinds are split where the spec predicts differently. M2b is split by whether the emergency contact is "
              "recorded, and coverage by whether the schedule documents it. One row is a subset of the rows above it, the "
              "coworker, neighbor and VIP cases whose target was seen in the actor's own department.", ""]
    rows = []
    for name, (mine, verify, alert) in counted.items():
        for cell, label in MATRIX_ROWS:
            part = (mine["own_department"] if cell == "own" else mine["cell"] == cell).to_numpy()
            n, hit = int(part.sum()), int((alert.to_numpy() & part).sum())
            asked = int((verify.to_numpy() & part).sum()) if verify is not None else 0
            low, high = wilson(hit, n)
            rows.append([name, label, f"{n:,}", f"{n - hit - asked:,}", f"{asked:,}" if verify is not None else "-", f"{hit:,}",
                         f"{hit / n:.0%} ({low:.0%}-{high:.0%})" if n else "no cases"])
    lines += table(["System", "Kind of access", "Cases", "Left alone", "Verify", "Alerted", "Share alerted (95% interval)"], rows)

    scores = matrix_scores(run_dir, u, frames)
    lines += ["## Predictions against outcomes", "",
              "Each cell is scored on the words section 5.2 of the spec defined before any results existed. Flag means "
              "the access is put in front of a reviewer: a rule fires, no explanation is found, or the model answers verify "
              "or escalate. Clear and miss mean it is not. A prediction that the model should say it cannot confirm is met "
              "by verify. A cell holds when at least 90% of its cases match the prediction, holds partly at 50% to 89%, "
              "and is contradicted below 50%. \"No basis, any answer is a guess\" fits any answer, and so does a "
              "false alert that only departmental responsibility could prevent, so those cells are marked no test. These "
              "codes were written on 15 and 16 Nov 2026, after pass 1 and before the corrected pass (spec 11.5). Escalated "
              "is shown for the LLM systems because escalate is what reaches the Privacy Officer.", ""]
    rows = []
    for r in scores.itertuples():
        rows.append([MATRIX_SYSTEMS[r.system], r.label, r.predicted, f"{r.cases:,}",
                     f"{r.flagged / r.cases:.0%}" if r.cases else "-",
                     "-" if pd.isna(r.escalated) or not r.cases else f"{r.escalated / r.cases:.0%}",
                     "-" if r.code in ("no test", "open") or not r.cases else f"{r.matching:,} of {r.cases:,}", r.mark])
    lines += table(["System", "Kind of access", "Predicted", "Cases", "Flagged", "Escalated", "Matching", "Mark"], rows)

    cues = cue_shown(run_dir, cfg, u, frames)
    if len(cues):
        lines += ["## Was the scenario's cue in the prompt?", "",
                  "A news-event or shared-credential access can only be recognized if the prompt holds the sign of it. "
                  "For a news-event case that is a line by another user who opened the patient after the same news. For a "
                  f"shared-credential case it is two lines by the account within {OVERLAP_MINUTES} minutes on devices in "
                  "different departments, or, for the grounded system, which is shown the schedule, a line under review "
                  f"outside every shift the account's owner had. The {OVERLAP_MINUTES} minutes are an assumption. Answers are "
                  "clear, verify and escalate, in that order.", ""]
        rows = []
        for kind in ("M5", "M6a", "M6b"):
            part = cues[cues["stratum"] == kind]
            if part.empty:
                continue
            row = [kind, len(part)]
            for s in SYSTEMS:
                d = frames[s].set_index("case_id")["disposition"]
                for shown in (True, False):
                    ids = part.loc[part[s] == shown, "case_id"]
                    counts = d.reindex(ids).value_counts()
                    row.append(f"{len(ids)}: " + " / ".join(str(int(counts.get(x, 0))) for x in DISPOSITIONS) if len(ids) else "0")
            rows.append(row)
        lines += table(["Scenario", "Cases"] + [f"{LLM_NAMES[s]}, {w}" for s in SYSTEMS for w in ("cue shown", "cue not shown")], rows)

    inj = injection_of(run_dir)
    bad_units = u[u["misuse"] != ""].assign(injection=lambda x: x["scenario_id"].map(inj))
    caught = {"Rules, all five": variants(u)["Rules, all five"][0], "Rules, then direct evidence": variants(u)["Rules, then direct evidence"][0]}
    key = ["user_id", "patient_id", "week"]
    for s, f in frames.items():
        hit = set(map(tuple, f.loc[f["is_misuse"] & (f["disposition"] == "escalate"), key].to_numpy()))
        caught[f"{LLM_NAMES[s]}, escalate"] = pd.Series([tuple(k) in hit for k in u[key].to_numpy()], index=u.index)
    lines += ["## Misuse by injection", "",
              "Several first accesses can come from one injected scenario, such as the accounts of one shared login or "
              "the users who read about the same news event, so they are not independent. Each cell gives first accesses "
              "caught, then injections with at least one first access caught.", ""]
    rows = []
    for kind, part in bad_units.groupby("misuse"):
        row = [kind, len(part), part["injection"].nunique()]
        for name, flag in caught.items():
            got = flag.loc[part.index]
            row.append(f"{int(got.sum())} of {len(part)}; {part.loc[got, 'injection'].nunique()} of {part['injection'].nunique()}")
        rows.append(row)
    lines += table(["Scenario", "First accesses", "Injections"] + list(caught), rows)

    lines += ["## Within a daily review budget, pooled over days", "",
              "This is pooled-budget performance, not a daily queue. A reviewer takes the highest-priority alerts each day, up to the budget. LLM alerts are ranked by disposition, "
              "then by the priority the model gave. The sample is too thin to rank day by day, so every system here is scored "
              "on an average day, which is why the rules and templates figures differ a little from `baseline-results.md`. "
              "Recall is over all misuse first accesses.", ""]
    rows = []
    for k in cfg["review_budget_per_day"]:
        for name, (alert, score) in variants(u).items():
            hits, shown = average_day_top_k(score[alert], is_misuse[alert], np.ones(int(alert.sum())), k, days)
            rows.append([k, name, f"{shown:,.0f}", f"{hits:.1f}", f"{hits / max(shown, 1):.2%}", f"{hits / is_misuse.sum():.0%}"])
        for name, (f, alert) in llm.items():
            a = f[alert]
            hits, shown = average_day_top_k(a["score"], a["is_misuse"], a["weight"], k, days)
            rows.append([k, name, f"{shown:,.0f}", f"{hits:.1f}", f"{hits / max(shown, 1):.2%}", f"{hits / max(f['is_misuse'].sum(), 1):.0%}"])
    lines += table(["Budget per day", "System", "Reviewed", "Misuse found", "Precision", "Recall"], rows)

    lines += ["## Faithfulness of the summaries", "",
              "Claims are the items of two parts of each summary, the evidence of a job-related purpose and the risk "
              "indicators. The narrative, the missing information and the rationale are not checked, so these figures rate "
              "those items and not whole summaries. The first check is mechanical. A claim passes when it cites at least one fact and every fact it cites was in the "
              "prompt. A range such as L2-L31 counts as citing every fact from L2 to L31. The prompt asks for a list of IDs, "
              "so the number that pass when a range is not counted is shown beside it. The second check gives each claim "
              "that passed to a judge model, with the facts it cites and the full "
              "list the author saw. Supported means the cited facts confirm all of it. Partly means nothing contradicts it "
              "but it adds an interpretation or leans on a fact it did not cite. Unsupported means a fact contradicts it or "
              "the cited facts do not bear on it. A claim that something is absent is checked against the full list. A claim "
              "is not yet judged when it passes the first check and has no verdict, because the judge's answer was cut off "
              "or the case has not been sent to the judge since ranges began to count. Counts are over the sample, not "
              "weighted.", ""]
    rows = []
    for s, f in frames.items():
        judged = answers(results_dir, f"judge.{s}.pass1")
        verdicts = [v for item in judged["verdicts"] for v in item] if judged is not None else []
        ruled = [v for v in verdicts if v is not None]
        absent = sum(a for item in judged["absence"] for a in item) if judged is not None else 0
        n = citation_counts(run_dir, results_dir, s, cfg)
        share = [f"{ruled.count(v) / len(ruled):.1%}" if ruled else "not run" for v in VERDICTS]
        tries = calls(Path(results_dir) / f"{s}.pass1.jsonl")
        rows.append([LLM_NAMES[s], f"{len(f):,}", f"{f['valid'].mean():.1%}", int((tries["finish"] == "length").sum()), f"{n['claims']:,}",
                     f"{n['exist']:,}", f"{n['listed']:,}", f"{len(ruled):,}",
                     f"{n['exist'] - len(ruled):,}", *share, f"{absent:,}"])
    lines += ["Fit the template includes the 60-word limit on the rationale. Cut off counts every try that hit the token "
              "limit, including tries that were sent again and later answered.", ""]
    lines += table(["System", "Summaries", "Fit the template", "Cut off by the token limit", "Claims", "Citations exist",
                    "Without counting ranges", "Claims judged", "Not yet judged", "Supported", "Partly", "Unsupported",
                    "Of which absence claims"], rows)

    covered = {}
    if covered_dir is not None and covered_results is not None and Path(covered_dir, "cases.csv").exists():
        covered = {s: scored_answers(covered_dir, covered_results, s) for s in frames}
        covered = {s: f for s, f in covered.items() if f is not None}
    if covered:
        lines += ["## With covering accesses", "",
                  f"The paired run is identical except that each M1-M4 actor also makes {cfg['covering']['accesses']} covering "
                  "accesses around the target, chosen by the heuristic attack of Xu et al. 2023. The same target cases are "
                  "triaged in both runs. The last column is a sample of the covering accesses themselves. Each side is one "
                  "answer from one pass, so a difference mixes the effect of the covers with the model changing its answer "
                  "from run to run, and a test that finds no difference is not evidence that the covers have no effect. "
                  "Repeat passes on the base run give the variation to compare against.", ""]
        rows = []
        for (name, (f, alert)), (_, (c, c_alert)) in zip(llm_variants({s: frames[s] for s in covered}).items(), llm_variants(covered).items()):
            target = c["stratum"] != "cover"
            both = set(c.loc[target, "case_id"]) & set(f["case_id"])
            rows.append([name, f"{int((alert & f['case_id'].isin(both)).sum())} of {len(both)}",
                         f"{int((c_alert & c['case_id'].isin(both)).sum())} of {len(both)}",
                         f"{int((c_alert & ~target).sum())} of {int((~target).sum())}"])
        lines += table(["System", "Targets flagged, no cover", "Targets flagged, with cover", "Covering accesses flagged"], rows)

    if set(frames) == set(SYSTEMS):
        lines += ["## Tests", "",
                  "Each row asks one question of two things measured on the same cases. Where the two are yes-or-no answers "
                  "on the same cases, the test is McNemar's exact test. It looks only at the cases where the two disagree and "
                  "asks whether the disagreements lean one way more than a coin would. The staff row asks whether an escalation "
                  "is tied to the patient being staff once the kind of access is held fixed, with the Cochran-Mantel-Haenszel "
                  "test. Every p-value is two-sided. Counts are over the sample and not weighted, so a test says whether two "
                  "things differ on these cases. It does not estimate a rate for the hospital. The staff question was asked "
                  "after the results were seen, which makes it exploratory. The others follow from the hypotheses in the spec.", ""]
        raw, graph = (frames[s].set_index("case_id") for s in SYSTEMS)
        graph = graph.loc[raw.index]

        def paired(question, n, a_name, a, b_name, b):
            only_a, only_b = int((a & ~b).sum()), int((~a & b).sum())
            p = mcnemar(only_a, only_b)
            return [question, f"{n:,}", f"{a_name} {int(a.sum()):,}; {b_name} {int(b.sum()):,}",
                    f"{only_a:,} one way, {only_b:,} the other", "McNemar, exact", f"{p:.3f}" if p >= 0.001 else "under 0.001"]

        bad, legit = graph["is_misuse"].to_numpy(), ~graph["is_misuse"].to_numpy() & (graph["stratum"] != "cover").to_numpy()
        esc_g, esc_r = (graph["disposition"] == "escalate").to_numpy(), (raw["disposition"] == "escalate").to_numpy()
        clear_g, clear_r = (graph["disposition"] == "clear").to_numpy(), (raw["disposition"] == "clear").to_numpy()
        best = variants(u)["Rules, then direct evidence"][0]
        flagged = set(map(tuple, u.loc[best, key].to_numpy()))
        by_rules = np.array([tuple(r) in flagged for r in graph[key].to_numpy()])
        rows = [
            paired("Does the identity graph change which misuse is escalated?", int(bad.sum()),
                   LLM_NAMES["llm_graph"], esc_g[bad], LLM_NAMES["llm_raw"], esc_r[bad]),
            paired("Does the identity graph change which legitimate work is cleared?", int(legit.sum()),
                   LLM_NAMES["llm_graph"], clear_g[legit], LLM_NAMES["llm_raw"], clear_r[legit]),
            paired("Does the grounded LLM escalate more misuse than rules followed by direct evidence?", int(bad.sum()),
                   LLM_NAMES["llm_graph"], esc_g[bad], "Rules, then direct evidence", by_rules[bad]),
        ]
        is_staff = graph["patient_id"].isin(staff).to_numpy()
        tables = []
        for kind in sorted(set(graph.loc[legit, "stratum"])):
            k = legit & (graph["stratum"] == kind).to_numpy()
            tables.append((int((k & is_staff & esc_g).sum()), int((k & is_staff & ~esc_g).sum()),
                           int((k & ~is_staff & esc_g).sum()), int((k & ~is_staff & ~esc_g).sum())))
        odds, p = mantel_haenszel(tables)
        rows.append(["Is a legitimate access more likely to be escalated by the grounded LLM when the patient is staff, "
                     "within the same kind of access?", f"{int(legit.sum()):,}",
                     f"staff charts {int((legit & is_staff & esc_g).sum())} of {int((legit & is_staff).sum()):,}; "
                     f"other charts {int((legit & ~is_staff & esc_g).sum())} of {int((legit & ~is_staff).sum()):,}",
                     "common odds ratio " + ("not finite" if not math.isfinite(odds) else f"{odds:.0f}"),
                     "Cochran-Mantel-Haenszel", f"{p:.3f}" if p >= 0.001 else "under 0.001"])
        if "llm_graph" in covered:
            c = covered["llm_graph"].set_index("case_id")
            both = [i for i in c.index[c["stratum"] != "cover"] if i in graph.index]
            for question, flag in (("Do covering accesses change which targets the grounded LLM escalates?", lambda d: d == "escalate"),
                                   ("Do covering accesses change which targets it flags at verify or escalate?", lambda d: d != "clear")):
                rows.append(paired(question, len(both), "without cover", flag(graph.loc[both, "disposition"]).to_numpy(),
                                   "with cover", flag(c.loc[both, "disposition"]).to_numpy()))
        lines += table(["Question", "Cases", "Yes answers", "Disagreements", "Test", "p"], rows)
        lines += ["The common odds ratio of the staff row pools kinds whose own odds ratios differ widely. Kind by kind, "
                  "with 0.5 added to every cell of a table that has a zero:", ""]
        kinds = sorted(set(graph.loc[legit, "stratum"]))
        rows = []
        for kind, (a, b, c, d) in zip(kinds, tables):
            ratio = (a * d / (b * c)) if min(a, b, c, d) > 0 else ((a + 0.5) * (d + 0.5) / ((b + 0.5) * (c + 0.5)))
            rows.append([LEGITIMATE.get(kind, kind), f"{a} of {a + b}", f"{c} of {c + d}",
                         "none escalated" if a + c == 0 else f"{ratio:.1f}"])
        lines += table(["Kind of access", "Staff charts escalated", "Other charts escalated", "Odds ratio"], rows)

    repeats = {s: sorted(int(p.stem.split("pass")[1]) for p in Path(results_dir).glob(f"{s}.pass*.jsonl")) for s in frames}
    if any(len(p) > 1 for p in repeats.values()):
        lines += ["## Repeat passes", "",
                  "The same cases sent again with the same prompts and settings, temperature 0 included. Agreement is the "
                  "share of cases given the same disposition in every pass, over the sample and weighted back to all first "
                  "accesses. Margins are 95% intervals from the sampling alone.", ""]
        rows = []
        for s, passes in repeats.items():
            agree = repeat_agreement(run_dir, results_dir, s, passes)
            for p in passes:
                r = scored_answers(run_dir, results_dir, s, p)
                total, margin = weighted_alerts(r, r["disposition"] == "escalate")
                per_day = f"0 (95% upper bound {zero_bound(r) / days:,.0f})" if total == 0 else f"{total / days:,.0f} ± {margin / days:,.0f}"
                rows.append([LLM_NAMES[s], p, f"{int(((r['disposition'] == 'escalate') & r['is_misuse']).sum())} of {int(r['is_misuse'].sum())}",
                             per_day, f"{agree['same'].mean():.1%}", f"{(agree['same'] * agree['weight']).sum() / agree['weight'].sum():.1%}"])
        lines += table(["System", "Pass", "Misuse escalated", "Escalations per day", "Agreement, sample", "Agreement, weighted"], rows)
        both = {s: repeat_agreement(run_dir, results_dir, s, passes) for s, passes in repeats.items() if len(passes) > 1}
        kinds = sorted(set().union(*[set(a["stratum"]) for a in both.values()]) - set(LEGITIMATE)) + \
            [k for k in LEGITIMATE if any(k in set(a["stratum"]) for a in both.values())]
        lines += ["Agreement by kind of access, as the share of cases with the same disposition in every pass.", ""]
        rows = []
        for kind in kinds:
            row = [LEGITIMATE.get(kind, kind)]
            for s, a in both.items():
                part = a[a["stratum"] == kind]
                row.append(f"{int(part['same'].sum())} of {len(part)}" if len(part) else "-")
            rows.append(row)
        lines += table(["Kind of access"] + [LLM_NAMES[s] for s in both], rows)
        for s, passes in repeats.items():
            if len(passes) != 2:
                continue
            a, b = (scored_answers(run_dir, results_dir, s, p).set_index("case_id")["disposition"] for p in passes)
            moved = pd.crosstab(a, b.loc[a.index]).reindex(index=DISPOSITIONS, columns=DISPOSITIONS, fill_value=0)
            lines += [f"{LLM_NAMES[s]}, answers in pass {passes[0]} (rows) against pass {passes[1]} (columns).", ""]
            lines += table(["", *DISPOSITIONS], [[d, *[f"{int(moved.loc[d, e]):,}" for e in DISPOSITIONS]] for d in DISPOSITIONS])
        if covered and covered_results is not None:
            c_passes = sorted(int(p.stem.split("pass")[1]) for p in Path(covered_results).glob("llm_graph.pass*.jsonl"))
            b_passes = repeats.get("llm_graph", [])
            if len(c_passes) > 1 and len(b_passes) > 1:
                base = {p: scored_answers(run_dir, results_dir, "llm_graph", p).set_index("case_id") for p in b_passes[:2]}
                cov = {p: scored_answers(covered_dir, covered_results, "llm_graph", p).set_index("case_id") for p in c_passes[:2]}
                first = cov[c_passes[0]]
                targets = [i for i in first.index[first["stratum"] != "cover"] if i in base[b_passes[0]].index]
                esc = lambda d: (d.loc[targets, "disposition"] == "escalate").to_numpy()
                p1, p2 = b_passes[:2]
                q1, q2 = c_passes[:2]
                pairs = [(f"Without cover, pass {p1} against pass {p2}", base[p1], base[p2]),
                         (f"With cover, pass {q1} against pass {q2}", cov[q1], cov[q2]),
                         (f"Without against with cover, pass {p1}", base[p1], cov[q1]),
                         (f"Without against with cover, pass {p2}", base[p2], cov[q2])]
                lines += ["## Covering accesses against run-to-run variation", "",
                          f"The {len(targets)} M1-M4 targets, escalated or not by the grounded LLM. The first two rows change "
                          "nothing but the run, so they show how many escalations move by chance. The last two add the covering "
                          "accesses. A covering effect would show as clearly more changes in the last two rows than in the first two.", ""]
                lines += table(["Comparison", "Escalated, first", "Escalated, second", "Targets that changed"],
                               [[name, int(esc(x).sum()), int(esc(y).sum()), f"{int((esc(x) != esc(y)).sum())} of {len(targets)}"]
                                for name, x, y in pairs])

    lines += ["## Tokens and cost", "",
              "Summed from the usage and the cost each call reported. A case that was sent again is one answer and more "
              "than one call, and every call is counted.", ""]
    rows = []
    for folder in [results_dir] + ([covered_results] if covered else []):
        for path in sorted(Path(folder).glob("*.jsonl")):
            c = calls(path)
            rows.append([f"{Path(folder).name}/{path.name}", f"{c['case_id'].nunique():,}", f"{len(c):,}", f"{c['prompt_tokens'].sum():,}",
                         f"{c['completion_tokens'].sum():,}", f"{c['reasoning_tokens'].sum():,}", f"${c['usd'].sum():.2f}"])
    lines += table(["File", "Answers", "Calls", "Prompt tokens", "Completion tokens", "Of which reasoning", "Cost"], rows)
    Path(out_path).write_text("\n".join(lines))
    return {"sampled": len(sampled), **{s: len(f) for s, f in frames.items()}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("what", nargs="?", default="baselines", choices=["baselines", "cases", "llm"],
                    help="baselines: score rules and templates. cases: write the case set for the LLM systems. "
                         "llm: score the LLM answers under results/")
    ap.add_argument("--run", default=None, help="defaults to data/generated/seed<seed>")
    ap.add_argument("--config", default="config/calibration.yaml")
    ap.add_argument("--out", default=None, help="defaults to docs/baseline-results.md or docs/llm-results.md")
    ap.add_argument("--covered-run", default=None, help="defaults to the run folder with -covered added, if it exists")
    ap.add_argument("--results", default="results", help="folder holding one folder of LLM answers per run")
    args = ap.parse_args()
    cfg = load_config(args.config)
    run = args.run or f"data/generated/seed{cfg['seed']}"
    covered = args.covered_run or f"{run}-covered"
    if args.what == "cases":
        print(json.dumps(build_cases(run, cfg, covered), indent=2))
    elif args.what == "llm":
        results = Path(args.results)
        print(write_llm_results(run, cfg, args.out or "docs/llm-results.md", results / Path(run).name, covered, results / Path(covered).name))
    else:
        print(write_results(run, cfg, args.out or "docs/baseline-results.md", covered))


if __name__ == "__main__":
    main()
