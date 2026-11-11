"""Compare a generated run with the calibration targets.

This is evaluation code, so it may read labels/. Everything in the
calibration table itself is measured from logs/ and context/ only, the way
it would be measured on a real log.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config, load_config
from .population import miles_between, street_of


def load_run(run_dir: str | Path) -> dict[str, pd.DataFrame]:
    run = Path(run_dir)
    tables = {"events": run / "logs" / "access_events.csv"}
    for folder in ("context", "labels"):
        for f in sorted((run / folder).glob("*.csv")):
            tables[f.stem] = f
    return {name: pd.read_csv(path, keep_default_na=False) for name, path in tables.items()}


# ---- measures taken from the log and context alone ----

def with_week(events: pd.DataFrame, window_start: str, days: int) -> pd.DataFrame:
    """Add day and week numbers, and keep whole weeks only."""
    ts = pd.to_datetime(events["timestamp"])
    day = (ts - pd.Timestamp(window_start)).dt.days
    out = events.assign(day=day, week=day // 7)
    return out[out["week"] < days // 7]


def repeat_share(weekly: pd.DataFrame) -> float:
    """Share of accesses where the same user opened the same patient earlier that week."""
    first = ~weekly.duplicated(["week", "user_id", "patient_id"])
    return 1 - first.mean()


def immediate_repeat_share(events: pd.DataFrame) -> float:
    """Share of events on the same patient as that user's previous event."""
    ordered = events.sort_values(["user_id", "event_id"], kind="stable")
    same = (ordered["patient_id"] == ordered["patient_id"].shift()) & (ordered["user_id"] == ordered["user_id"].shift())
    return same.mean()


def first_accesses(weekly: pd.DataFrame, staff: pd.DataFrame) -> pd.DataFrame:
    """One row per user and patient per week, self-accesses removed."""
    own = dict(zip(staff["user_id"], staff["patient_id"]))
    first = weekly.drop_duplicates(["week", "user_id", "patient_id"])
    return first[first["user_id"].map(own) != first["patient_id"]]


def explained(first: pd.DataFrame, t: dict) -> tuple[pd.Series, pd.Series]:
    """Which first accesses have direct evidence, and which have a department link.

    Direct: the user is on the care team of one of the patient's encounters,
    or ordered something for the patient. Department link: the patient had an
    encounter in the user's department, or an order sent to it.
    """
    enc_patient = dict(zip(t["encounters"]["encounter_id"], t["encounters"]["patient_id"]))
    direct = set(zip(t["care_team"]["user_id"], t["care_team"]["encounter_id"].map(enc_patient)))
    direct |= set(zip(t["orders"]["ordered_by"], t["orders"]["patient_id"]))
    dept_patient = set(zip(t["encounters"]["department_id"], t["encounters"]["patient_id"]))
    dept_patient |= set(zip(t["orders"]["performing_department_id"], t["orders"]["patient_id"]))
    dept = first["user_id"].map(dict(zip(t["staff"]["user_id"], t["staff"]["department_id"])))
    pairs = list(zip(first["user_id"], first["patient_id"]))
    is_direct = pd.Series([p in direct for p in pairs], index=first.index)
    is_linked = pd.Series([p in dept_patient for p in zip(dept, first["patient_id"])], index=first.index)
    return is_direct, is_direct | is_linked


def rule_matches(first: pd.DataFrame, staff: pd.DataFrame, patients: pd.DataFrame, miles: float) -> pd.DataFrame:
    """The five rules from Hedda et al. 2017, one boolean column each."""
    s = staff.set_index("user_id")
    p = patients.set_index("patient_id")
    user_person = p.loc[s.loc[first["user_id"], "patient_id"]]
    target = p.loc[first["patient_id"]]
    patient_dept = dict(zip(staff["patient_id"], staff["department_id"]))
    target_dept = first["patient_id"].map(patient_dept)
    street = lambda d: (d["address"].map(street_of) + "|" + d["city"]).to_numpy()
    distance = miles_between(user_person["lat"].to_numpy(float), user_person["lon"].to_numpy(float),
                             target["lat"].to_numpy(float), target["lon"].to_numpy(float))
    return pd.DataFrame(
        {
            "organization co-worker": target_dept.notna().to_numpy(),
            "department co-worker": (target_dept == s.loc[first["user_id"], "department_id"].to_numpy()).to_numpy(),
            "last name": user_person["last_name"].to_numpy() == target["last_name"].to_numpy(),
            "proximity": distance <= miles,
            "same street": street(user_person) == street(target),
        },
        index=first.index,
    )


def rule_shares(first: pd.DataFrame, staff: pd.DataFrame, patients: pd.DataFrame, miles: float):
    """Observed rule shares, and how they compare with users and patients paired at random.

    The comparison follows Hedda et al.: the patient column is permuted and
    the rules are applied again.
    """
    rules = rule_matches(first, staff, patients, miles)
    shuffled = first.assign(patient_id=np.random.default_rng(0).permutation(first["patient_id"].to_numpy()))
    expected = rule_matches(shuffled, staff, patients, miles).mean()
    observed = rules.mean()
    ratio = {k: (observed[k] / expected[k] if expected[k] > 0 else float("nan")) for k in observed.index}
    return rules, observed.to_dict(), ratio


def chart_time(t: dict, cfg: Config) -> tuple[float, float]:
    """Inpatient nurses' chart time as a share of scheduled shift time, and
    primary care physicians' chart hours per scheduled day."""
    staff, schedule = t["staff"], t["schedule"]
    ws = pd.Timestamp(cfg["window_start"])
    we = ws + pd.Timedelta(days=cfg["window_days"])
    start = pd.to_datetime(schedule["shift_start"]).clip(lower=ws)
    end = pd.to_datetime(schedule["shift_end"]).clip(upper=we)
    seconds = (end - start).dt.total_seconds().clip(lower=0)
    on_shift = seconds.groupby(schedule["user_id"]).sum()
    days = (seconds > 0).groupby(schedule["user_id"]).sum()
    in_chart = t["events"].groupby("user_id")["duration_s"].sum()
    kind = staff["department_id"].map(dict(zip(t["departments"]["department_id"], t["departments"]["kind"])))
    nurses = staff.loc[(staff["role"] == "nurse") & (kind == "inpatient"), "user_id"]
    primary = t["departments"].loc[t["departments"]["name"].str.contains("Primary care"), "department_id"]
    doctors = staff.loc[(staff["role"] == "physician") & staff["department_id"].isin(primary), "user_id"]
    total = lambda series, users: float(series.reindex(users).fillna(0).sum())
    nurse_share = total(in_chart, nurses) / max(total(on_shift, nurses), 1)
    doctor_hours = total(in_chart, doctors) / 3600 / max(total(days, doctors), 1)
    return nurse_share, doctor_hours


def patient_index(events: pd.DataFrame) -> pd.Series:
    """Per event, the position of its patient among the distinct patients the
    user opened that day (patient_day_user_id in Herrera Montano et al.)."""
    day = events["timestamp"].str[:10]
    ordered = events.assign(day=day).sort_values(["user_id", "day", "event_id"], kind="stable")
    new_patient = ~ordered.duplicated(["user_id", "day", "patient_id"])
    position = new_patient.groupby([ordered["user_id"], ordered["day"]]).cumsum()
    return position.groupby([ordered["user_id"], ordered["day"], ordered["patient_id"]]).transform("first")


def zero_gap_share(events: pd.DataFrame) -> float:
    """Share of events with no time since the user last opened that patient,
    at minute resolution, counting a first access as zero (the UK definition)."""
    ordered = events.sort_values(["user_id", "patient_id", "event_id"], kind="stable")
    minute = ordered["timestamp"].str[:16]
    first = ~ordered.duplicated(["user_id", "patient_id"])
    same = (minute == minute.shift()) & (ordered["user_id"] == ordered["user_id"].shift()) \
        & (ordered["patient_id"] == ordered["patient_id"].shift())
    return float((first | same).mean())


def measure(t: dict, cfg: Config) -> dict:
    events, staff = t["events"], t["staff"]
    days = cfg["window_days"]
    weekly = with_week(events, cfg["window_start"], days)
    weeks = days // 7
    first = first_accesses(weekly, staff)
    direct, linked = explained(first, t)
    rules, rule_share, rule_ratio = rule_shares(first, staff, t["patients"], cfg["proximity_miles"])
    own = events["user_id"].map(dict(zip(staff["user_id"], staff["patient_id"]))) == events["patient_id"]
    per_user = events.groupby("user_id")
    per_day = events.assign(day=events["timestamp"].str[:10]).groupby(["user_id", "day"])
    index = patient_index(events)
    top = events["action_code"].value_counts(normalize=True)
    is_repeat = weekly.duplicated(["week", "user_id", "patient_id"])
    nurse_share, doctor_hours = chart_time(t, cfg)
    return {
        "accesses_per_staff_week": len(weekly) / len(staff) / weeks,
        "repeat_access_share": repeat_share(weekly),
        "self_access_share": own.mean(),
        "first_access_explained_direct": direct.mean(),
        "first_access_explained_with_department_link": linked.mean(),
        "all_accesses_explained": (is_repeat.sum() + linked.sum()) / len(weekly),
        "rule_match_share": rule_share,
        "rule_observed_to_expected": rule_ratio,
        "rule_match_explained_share": {k: float(direct[rules[k]].mean()) for k in rules.columns},
        "nurse_chart_time_share": nurse_share,
        "physician_ehr_hours_per_day": doctor_hours,
        "events_per_user": {"median": per_user.size().median(), "p95": per_user.size().quantile(0.95)},
        "patients_per_user": {"median": per_user["patient_id"].nunique().median(),
                              "p99": per_user["patient_id"].nunique().quantile(0.99)},
        "devices_per_user": {"median": per_user["device_id"].nunique().median(),
                             "p95": per_user["device_id"].nunique().quantile(0.95)},
        "patient_index_in_user_day": {"median": index.median(), "p99": index.quantile(0.99)},
        "action_top21_share": top.head(21).sum(),
        "zero_gap_same_patient_share": zero_gap_share(events),
        "duration_median_s": events["duration_s"].median(),
        "duration_p95_s": events["duration_s"].quantile(0.95),
        "_chart_hours_per_user_day": (per_day["duration_s"].sum() / 3600).describe(percentiles=[0.5, 0.95]).to_dict(),
        "_first_accesses": len(first),
        "_direct": direct,
        "_rules": rules,
        "_first": first,
    }


# ---- label-side summary ----

HONEST_REASONS = ["care_team", "undocumented_team", "order", "consult", "coverage", "back_office", "hospital_wide"]


def label_summary(t: dict, m: dict) -> dict:
    labels = t["events"].merge(t["event_labels"], on="event_id")
    first = m["_first"].merge(t["event_labels"], on="event_id")
    rules = m["_rules"].loc[m["_first"].index]
    relationship = rules.drop(columns="organization co-worker").any(axis=1).to_numpy()
    # A hard negative has an undocumented cause and no direct evidence from
    # any other encounter either.
    unexplained = ~m["_direct"].to_numpy()
    by_reason = first[unexplained].groupby(["reason", "documented"]).size()
    honest = first["reason"].isin(HONEST_REASONS).to_numpy()
    get = lambda reason, documented: int(by_reason.get((reason, documented), 0))
    return {
        "events_by_reason": labels["reason"].value_counts().to_dict(),
        "scenarios_by_type": t["scenarios"]["type"].value_counts().sort_index().to_dict(),
        "hard_negative_first_accesses": {
            "H1 consult": get("consult", False),
            "H2a coverage, documented": get("coverage", True),
            "H2b coverage, undocumented": get("coverage", False),
            "H3 undocumented care team": get("undocumented_team", False),
            "H4a documented care with a rule match": int((honest & relationship & ~unexplained).sum()),
            "H4b undocumented work with a rule match": int((honest & relationship & unexplained).sum()),
            "H5 back office": get("back_office", False),
            "H6 hospital-wide role": get("hospital_wide", False),
        },
    }


# ---- report ----

def fmt(x) -> str:
    if isinstance(x, dict):
        return ", ".join(f"{k} {fmt(v)}" for k, v in x.items())
    if isinstance(x, (list, tuple)):
        return "-".join(fmt(v) for v in x)
    if isinstance(x, float) or isinstance(x, np.floating):
        if abs(x) >= 100 or float(x).is_integer():
            return f"{x:,.0f}"
        if abs(x) >= 1:
            return f"{x:.1f}"
        return f"{x:.2%}" if abs(x) < 0.01 else f"{x:.1%}"
    return f"{x:,}" if isinstance(x, (int, np.integer)) else str(x)


def gap(target, value, use: str) -> str:
    if use == "later":
        return "not measurable yet"
    if use == "shape" and isinstance(target, dict):
        (a, b), (c, d) = list(target.values()), list(value.values())
        return f"ratio {d / c:.1f} against {b / a:.1f}"
    if isinstance(target, dict):
        return ", ".join(f"{k} {value[k] / target[k]:.1f}x" for k in target)
    if isinstance(target, list):
        lo, hi = target
        if isinstance(value, dict):
            inside = sum(lo <= v <= hi for v in value.values())
            return f"{inside} of {len(value)} rules inside the range"
        return "inside the range" if lo <= value <= hi else f"{(value - (lo if value < lo else hi)) / (lo if value < lo else hi):+.0%} from the range"
    if abs(target) < 1:
        points = (value - target) * 100
        points = round(points, 2 if abs(points) < 0.1 else 1) + 0.0  # no negative zero
        return f"{points:+.2f} points" if abs(points) < 0.1 else f"{points:+.1f} points"
    return f"{(value - target) / target:+.0%}"


def write_report(run_dir: str | Path, cfg: Config, out_path: str | Path) -> dict:
    t = load_run(run_dir)
    m = measure(t, cfg)
    s = label_summary(t, m)
    manifest = json.loads((Path(run_dir) / "manifest.json").read_text())
    lines = [
        "# Calibration report",
        "",
        "Generated by `python -m ehr_triage.report`. Do not edit by hand.",
        "",
        f"Run seed {manifest['seed']}, config digest `{manifest['config_digest']}`, Synthea {manifest['synthea']['version']}. "
        f"{manifest['rows']['events']:,} events, {manifest['rows']['staff']} staff, {manifest['rows']['patients']:,} patients, "
        f"{manifest['rows']['encounters']:,} encounters over {manifest['window_days']} days.",
        "",
        "## Targets against generated values",
        "",
        "Every generated value below is measured from the log and context files only. Gaps are left as they are. "
        "Any change made in response to one is a config change with its own commit.",
        "",
        "| Measure | Target | Generated | Gap | Use | Source |",
        "|---|---|---|---|---|---|",
    ]
    for name, entry in cfg.targets.items():
        value = m.get(name)
        ratio = name == "rule_observed_to_expected"  # plain ratios, not shares
        show = (lambda d: ", ".join(f"{k} {v:.2f}" for k, v in d.items())) if ratio else fmt
        shown = "" if value is None else show(value)
        lines.append(
            f"| {name.replace('_', ' ')} | {show(entry['value'])} | {shown} | "
            f"{gap(entry['value'], value, entry['use'])} | {entry['use']} | {entry['source']} |"
        )
    hours = m["_chart_hours_per_user_day"]
    lines += [
        "",
        "Weekly measures use the four whole weeks of the window. Repeats, first accesses and rule matches follow the "
        "definitions in Hedda et al. 2017. A first access is one user opening one patient for the first time in a week, "
        "with self-accesses removed. Rule shares are over first accesses, and the observed-to-expected ratio permutes "
        "the patients the way their paper does. For the two rule rows the gap is the generated value as a multiple of "
        "the published one.",
        "",
        "The log uses the US unit of an access. The UK log of Herrera Montano et al. records about 9 events per user per "
        "week, so its rows are compared by shape only, as the ratio of a high percentile to the median, and its "
        "duration and zero-gap rows are shown for reference. A duration here is time until the user's next action, "
        "capped at five minutes.",
        "",
        "The department-link row is a stand-in. It counts a first access as explained when the patient had an encounter "
        "in the user's department or an order sent to it. That is broader than the diagnosis-department links in "
        "Fabbri and LeFevre, so it is shown for orientation and is not a like-for-like comparison.",
        "",
        "## Workload check",
        "",
        f"Chart time per user per day is {hours['50%']:.1f} hours at the median and {hours['95%']:.1f} hours at the "
        f"95th percentile, summed from event durations over every user and day with activity.",
        "",
        "## What is in the labels",
        "",
        "| Injected scenario | Count |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in s["scenarios_by_type"].items()]
    lines += ["", "| Hard negative (first accesses) | Count |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in s["hard_negative_first_accesses"].items()]
    lines += ["", "| Events by true reason | Count |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in s["events_by_reason"].items()]
    bases: dict[str, list[str]] = {}
    for name, entry in cfg.params.items():
        bases.setdefault(entry["basis"], []).append(name)
    lines += [
        "",
        "## Where the parameters come from",
        "",
        "| Basis | Parameters |",
        "|---|---|",
    ]
    lines += [f"| {b} | {len(names)} |" for b, names in bases.items()]
    lines += [
        "",
        "Assumptions have no public source. They are " + ", ".join(f"`{n}`" for n in bases.get("assumption", [])) + ".",
        "",
        "Encounter sampling notes from the manifest: "
        f"{manifest['encounter_stats']['inpatient_pool_used']:.0%} of the inpatient pool was used, and the share of clinic "
        "visits whose Synthea reason matched the clinic's specialty was "
        + ", ".join(f"{k} {v:.0%}" for k, v in manifest["encounter_stats"]["clinic_specialty_match"].items()) + ".",
        "",
    ]
    Path(out_path).write_text("\n".join(lines))
    return {k: v for k, v in m.items() if not k.startswith("_")} | {"labels": s}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", default=None, help="defaults to data/generated/seed<seed>")
    ap.add_argument("--config", default="config/calibration.yaml")
    ap.add_argument("--out", default="docs/calibration-report.md")
    args = ap.parse_args()
    cfg = load_config(args.config)
    result = write_report(args.run or f"data/generated/seed{cfg['seed']}", cfg, args.out)
    print(json.dumps(result, indent=2, default=float))


if __name__ == "__main__":
    main()
