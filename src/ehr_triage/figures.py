"""Figures for the reading of the results, written as SVG to docs/figures/.

Every number in a figure comes from the same functions that write the
reports (`evaluate.py` and `report.py`), so a figure and a table cannot
disagree. Nothing here reads labels/ directly and nothing calls a model.
A figure whose inputs are not there yet, such as LLM answers that have not
been run, is skipped and named in the output.

    python -m ehr_triage.figures
"""
from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import evaluate, report  # noqa: E402
from .config import Config, load_config  # noqa: E402
from .graph import VISIBLE  # noqa: E402

# One hue for data, grey for context. Colour never carries a meaning alone.
INK = "#222222"
MUTED = "#8a8a8a"
LIGHT = "#d9d9d9"
HUE = "#2f6690"
HUE_LIGHT = "#a9c4da"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 11, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "axes.edgecolor": MUTED, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": INK, "ytick.color": INK, "axes.spines.top": False, "axes.spines.right": False,
    "svg.fonttype": "none", "svg.hashsalt": "ehr-triage",  # text stays text, ids stay the same from run to run
})

SYSTEM_NAMES = {"rules": "Rules", "templates": "Templates", "llm_raw": "LLM", "llm_graph": "LLM + Graph"}

# Readable names for every kind of first access, in the order of the matrix in spec 5.2.
KIND_NAMES = {
    "M1": "Coworker snooping (M1)",
    "M2a": "Relative in the same household (M2a)",
    "M2b": "Relative elsewhere (M2b)",
    "M2b+": "Relative elsewhere, emergency contact recorded (M2b)",
    "M2b-": "Relative elsewhere, no emergency contact (M2b)",
    "M3": "Neighbor (M3)",
    "M4": "VIP (M4)",
    "M5": "Curiosity after a news event (M5)",
    "M6a": "Shared credentials, overlapping sessions (M6a)",
    "M6b": "Shared credentials, off shift (M6b)",
    "own": "Misuse with the target seen in the actor's department",
    "direct": "Explained by direct evidence",
    "order": "Order handled by a department",
    "care_team": "Care team, on another encounter",
    "consult": "Consult (H1)",
    "coverage": "Coverage (H2)",
    "H2a": "Coverage, documented (H2a)",
    "H2b": "Coverage, undocumented (H2b)",
    "undocumented_team": "Undocumented care team (H3)",
    "H4a": "Documented care, relationship present (H4a)",
    "H4b": "Undocumented work, relationship present (H4b)",
    "back_office": "Back-office work (H5)",
    "hospital_wide": "Hospital-wide role (H6)",
}

CONTEXT_NAMES = {
    "staff": "Staff", "patients": "Patients", "departments": "Departments", "devices": "Devices",
    "schedule": "Schedule", "encounters": "Encounters", "care_team": "Care team", "orders": "Orders", "actions": "Actions",
}


def save(fig, out_dir: Path, name: str) -> Path:
    path = out_dir / f"{name}.svg"
    fig.savefig(path, format="svg", bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)
    return path


def note(fig, text: str, y: float = 0.0) -> None:
    """A source line under the figure, in muted type."""
    fig.text(0.0, y, text, ha="left", va="top", fontsize=7.5, color=MUTED, wrap=True)


# ---- figure 1 ----

def fig01_visibility(out_dir: Path) -> Path:
    """What each system may see: the visibility table of spec section 2, drawn from `graph.VISIBLE`."""
    tables = list(CONTEXT_NAMES)
    columns = ["Audit log"] + [CONTEXT_NAMES[t] for t in tables] + ["Labels"]
    rows = [(SYSTEM_NAMES[s], [True] + [t in VISIBLE[s] for t in tables] + [False]) for s in VISIBLE]
    rows.append(("Evaluation", [True] * (len(tables) + 1) + [True]))

    fig, ax = plt.subplots(figsize=(8.2, 3.4))
    n_rows, n_cols = len(rows), len(columns)
    for i, (_, reads) in enumerate(rows):
        y = n_rows - 1 - i
        for j, r in enumerate(reads):
            if r:
                ax.add_patch(plt.Rectangle((j - 0.36, y - 0.32), 0.72, 0.64, color=HUE if i < n_rows - 1 else MUTED, lw=0))
                ax.text(j, y, "reads", ha="center", va="center", fontsize=7, color="white")
            else:
                ax.add_patch(plt.Rectangle((j - 0.36, y - 0.32), 0.72, 0.64, fill=False, ec=LIGHT, lw=0.8))
                ax.text(j, y, "-", ha="center", va="center", fontsize=8, color=MUTED)
    ax.axhline(0.5, color=MUTED, lw=0.6, ls=(0, (2, 2)))
    for x in (0.5, n_cols - 1.5):
        ax.axvline(x, color=LIGHT, lw=0.8)
    ax.set_xlim(-0.6, n_cols - 0.4)
    ax.set_ylim(-0.6, n_rows - 0.4 + 1.1)
    ax.set_yticks(range(n_rows), [name for name, _ in rows][::-1])
    ax.set_xticks(range(n_cols), columns, rotation=35, ha="right", rotation_mode="anchor")
    ax.tick_params(length=0)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    top = n_rows - 0.4 + 0.25
    for label, lo, hi in (("logs/", 0, 0), ("context/", 1, n_cols - 2), ("labels/", n_cols - 1, n_cols - 1)):
        ax.plot([lo - 0.36, hi + 0.36], [top, top], color=INK, lw=0.8)
        ax.text((lo + hi) / 2, top + 0.15, label, ha="center", va="bottom", fontsize=8.5, family="DejaVu Sans Mono")
    ax.set_title("What each system may see", pad=14)
    note(fig, "Systems load data only through graph.load_visible, which enforces this table (spec section 2). "
              "No system reads labels/, only the evaluation code.", y=-0.2)
    return save(fig, out_dir, "fig01-what-each-system-may-see")


# ---- figure 2 ----

def kind_order(kinds) -> list[str]:
    order = list(KIND_NAMES)
    return sorted(kinds, key=lambda k: order.index(k) if k in order else len(order))


def fig02_first_accesses(run: Path, cfg: Config, out_dir: Path) -> Path:
    """The first accesses of the run by kind, beside the cases sent to the LLM systems."""
    u = evaluate.score_run(run, cfg)
    population = evaluate.strata(u).value_counts()
    sampled = evaluate.case_strata(run).value_counts()
    misuse = [k for k in population.index if k.startswith("M")]
    legit = [k for k in population.index if k not in misuse]
    # Legitimate kinds ranked by size, then misuse ranked by size, misuse at the bottom.
    kinds = sorted(legit, key=lambda k: -population[k]) + sorted(misuse, key=lambda k: (-population[k], k))
    total, n_misuse = int(population.sum()), int(population[misuse].sum())

    fig, (left, right) = plt.subplots(1, 2, figsize=(8.6, 5.4), sharey=True, gridspec_kw={"width_ratios": [1.5, 1], "wspace": 0.08})
    y = np.arange(len(kinds))[::-1]
    colors = [HUE if k in misuse else MUTED for k in kinds]
    left.barh(y, [population[k] for k in kinds], color=colors, height=0.7)
    left.set_xscale("log")
    left.set_xlim(5, 2e5)
    for yi, k in zip(y, kinds):
        left.text(population[k] * 1.15, yi, f"{population[k]:,}", va="center", fontsize=7.5)
    left.set_yticks(y, [KIND_NAMES.get(k, k) for k in kinds])
    left.set_xlabel("First accesses in the run (log scale)")
    left.set_title(f"All {total:,} first accesses", fontsize=9.5)
    left.tick_params(axis="y", length=0)

    right.barh(y, [sampled.get(k, 0) for k in kinds], color=colors, height=0.7)
    right.set_xlim(0, max(sampled) * 1.25)
    for yi, k in zip(y, kinds):
        n = int(sampled.get(k, 0))
        right.text(n + max(sampled) * 0.02, yi, f"{n:,}" + (" (all)" if n == population[k] else ""), va="center", fontsize=7.5)
    right.set_xlabel("Cases sent to the LLM systems")
    right.set_title(f"The {int(sampled.sum()):,} cases", fontsize=9.5)
    right.tick_params(axis="y", length=0)
    for ax in (left, right):
        ax.axhline(y[len(legit) - 1] - 0.5, color=MUTED, lw=0.6, ls=(0, (2, 2)))
    left.text(1.0, y[len(legit) - 1] - 0.62, f"Misuse, {n_misuse} first accesses ({n_misuse / total:.2%})",
              transform=left.get_yaxis_transform(), ha="right", va="top", fontsize=7.5, color=HUE)
    fig.suptitle("What the hospital's first accesses are made of", x=0.0, ha="left", fontsize=11, fontweight="bold", y=0.98)
    note(fig, "A first access is one user opening one patient's chart for the first time in a week, self-access left out. "
              "Misuse rows in blue and below the dashed line. The case set takes every misuse access and a fixed number "
              "of each legitimate kind, and population figures are weighted back by kind.", y=0.0)
    return save(fig, out_dir, "fig02-first-accesses-by-kind")


# ---- figure 3 ----

USE_NAMES = {"level": "Calibrated to the target", "reported": "Reported, not tuned", "shape": "Shape only (UK log)"}


MEASURE_NAMES = {
    "accesses_per_staff_week": "Accesses per staff member per week",
    "repeat_access_share": "Share of accesses that are repeats",
    "self_access_share": "Share of accesses that are self-access",
    "first_access_explained_direct": "First accesses explained by direct evidence",
    "first_access_explained_with_responsibility": "First accesses explained, with responsibility",
    "all_accesses_explained": "All accesses explained",
    "rule_match_share": "Rule match share",
    "rule_observed_to_expected": "Rule observed to expected",
    "rule_match_explained_share": "Rule matches explained",
    "nurse_chart_time_share": "Inpatient nurse chart time, share of shift",
    "physician_ehr_hours_per_day": "Physician EHR hours per day",
    "events_per_user": "Events per user",
    "patients_per_user": "Patients per user",
    "devices_per_user": "Devices per user",
    "patient_index_in_user_day": "Patient index in a user's day",
    "action_top21_share": "Share of events in the top 21 action codes",
    "zero_gap_same_patient_share": "Immediate repeats on the same patient",
    "duration_median_s": "Duration, median (s)",
    "duration_p95_s": "Duration, 95th percentile (s)",
}


def measure_name(name: str) -> str:
    return MEASURE_NAMES.get(name, name.replace("_", " ").capitalize())


def calibration_points(run: Path, cfg: Config) -> list[dict]:
    """Generated over target for every measure in the calibration report, one point per number compared."""
    m = report.measure(report.load_run(run), cfg)
    points = []
    for name, entry in cfg.targets.items():
        target, value, use = entry["value"], m.get(name), entry["use"]
        if use == "later" or value is None:
            continue
        if use == "shape" and isinstance(target, dict):  # the spread, high percentile over median
            (a, b), (c, d) = list(target.values()), list(value.values())
            hi = list(target)[1].replace("p", "")
            points.append(dict(use=use, label=f"{measure_name(name)}, {hi}th percentile over median", ratio=(d / c) / (b / a),
                               shown=f"{d / c:.1f} vs {b / a:.1f}"))
        elif isinstance(target, dict):
            for rule, t in target.items():
                v = value[rule]
                text = f"{v:.2f} vs {t:.2f}" if name == "rule_observed_to_expected" else f"{report.fmt(v)} vs {report.fmt(t)}"
                points.append(dict(use=use, label=f"{measure_name(name)}, {rule}", ratio=v / t, shown=text))
        elif isinstance(target, list):
            lo, hi = target
            inside = lo <= value <= hi
            points.append(dict(use=use, label=measure_name(name), ratio=1.0 if inside else value / (lo if value < lo else hi),
                               shown=f"{value:,.0f} vs {lo:,}-{hi:,}" + (" (inside)" if inside else "")))
        else:
            shown = f"{report.fmt(float(value))} vs {report.fmt(float(target))}"
            points.append(dict(use=use, label=measure_name(name), ratio=value / target, shown=shown))
    return points


def fig03_calibration(run: Path, cfg: Config, out_dir: Path) -> Path:
    points = calibration_points(run, cfg)
    groups = [g for g in USE_NAMES if any(p["use"] == g for p in points)]
    rows, labels, y = [], [], 0.0
    for g in groups:
        labels.append((y, USE_NAMES[g], True))
        y -= 1
        for p in [p for p in points if p["use"] == g]:
            rows.append((y, p))
            labels.append((y, p["label"], False))
            y -= 1
        y -= 0.5

    fig, ax = plt.subplots(figsize=(8.6, 0.22 * len(labels) + 1.0))
    lo, hi = 1 / 6, 48
    ax.set_xscale("log")
    ax.set_xlim(lo, hi)
    ax.axvline(1, color=INK, lw=0.8)
    for x in (0.5, 2):
        ax.axvspan(x if x > 1 else 1, 1 if x > 1 else x, color=LIGHT, alpha=0.35, lw=0)
    for yi, p in rows:
        r = float(np.clip(p["ratio"], lo, hi))
        ax.plot([1, r], [yi, yi], color=HUE_LIGHT, lw=1.2, zorder=1)
        ax.plot(r, yi, "o", color=HUE, ms=4.5, zorder=2)
        ax.text(hi * 1.08, yi, p["shown"], va="center", fontsize=7, color=INK, clip_on=False)
    ax.set_yticks([yy for yy, _, _ in labels], [t for _, t, _ in labels])
    for tick, (_, _, head) in zip(ax.get_yticklabels(), labels):
        if head:
            tick.set_fontweight("bold")
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ticks = [1 / 4, 1 / 2, 1, 2, 4, 8, 16, 32]
    ax.set_xticks(ticks, ["1/4", "1/2", "1", "2", "4", "8", "16", "32"])
    ax.minorticks_off()
    ax.set_xlabel("Generated value over published target (log scale). 1 is on target, the band is within a factor of 2")
    ax.text(hi * 1.08, 0.6, "Generated vs target", fontsize=7, color=MUTED, clip_on=False)
    ax.set_ylim(y + 0.5, 1.2)
    ax.set_title("Calibration against published targets", pad=10)
    note(fig, "Same measures as docs/calibration-report.md. Shape rows compare how spread out the UK log is "
              "(a high percentile over the median), not its level. Gaps are reported and left as they are. "
              "Alerts per access is not measurable yet and is left out.", y=0.0)
    return save(fig, out_dir, "fig03-calibration-against-targets")


# ---- figure 4 ----

# Chosen by hand for seed 42 (Carlos, 15 Nov 2026): a misuse access the LLM + Graph escalated and the LLM
# did not, and a legitimate access with the same kind of tie that the LLM + Graph cleared. On another run,
# such as the tests', the first case in ID order that meets the same test is drawn instead.
FIG04_CASES = {"misuse": "C0033", "legitimate": "C0021"}

EDGE_NAMES = {
    "works_in": "works in", "employed_in": "employed in", "is": "is", "same_surname": "same last name",
    "same_street": "same street", "lives_near": "lives nearby", "emergency_contact": "emergency contact",
    "had": "had", "in": "seen in", "care_team": "care team", "ordered": "ordered", "order_to": "order sent to",
}


def fig04_pick(run: Path, results: Path) -> dict | None:
    """The two cases to draw, with both systems' answers, or None when the answers are not there."""
    graph = evaluate.scored_answers(run, results, "llm_graph")
    raw = evaluate.scored_answers(run, results, "llm_raw")
    if graph is None or raw is None:
        return None
    both = graph.merge(raw[["case_id", "disposition", "priority"]], on="case_id", suffixes=("", "_raw")).sort_values("case_id")
    tests = {
        "misuse": both["is_misuse"] & (both["disposition"] == "escalate") & (both["disposition_raw"] != "escalate"),
        "legitimate": (both["stratum"] == "H4a") & (both["disposition"] == "clear"),
    }
    picked = {}
    for role, ok in tests.items():
        fits = both[ok]
        if fits.empty:
            return None
        chosen = fits[fits["case_id"] == FIG04_CASES[role]]
        picked[role] = (chosen if len(chosen) else fits).iloc[0]
    return picked


def case_graph(v, row) -> tuple[list, list, dict]:
    """The nodes and edges on every evidence path between the case's user and patient, as the LLM + Graph saw them."""
    from .graph import evidence_paths
    from .triage import case_events
    case, _ = case_events(v, row.user_id, row.patient_id, int(row.week))
    paths = evidence_paths(v.graph, row.user_id, row.patient_id, pd.Timestamp(case["timestamp"].iloc[0]))
    nodes, edges = [], {}
    for path in paths.values():
        for n in path:
            if n not in nodes:
                nodes.append(n)
        for a, b in zip(path, path[1:]):
            pair = tuple(sorted([a, b]))
            keys = [k for x, y in ((a, b), (b, a)) for k in (v.graph.get_edge_data(x, y) or {})]
            if not keys:  # a path can name two nodes with no edge between them, as organization co-worker does
                continue
            edges.setdefault(pair, [])
            for k in keys:
                if EDGE_NAMES.get(k, k) not in edges[pair]:
                    edges[pair].append(EDGE_NAMES.get(k, k))
    return nodes, list(edges.items()), paths


def node_text(v, node) -> str:
    kind, ident = node
    if kind == "user":
        s = v.staff.loc[ident]
        return f"User {ident}\n{s['role']}, {s['department_id']}"
    if kind == "patient":
        return f"Patient {ident}"
    if kind == "department":
        return f"Department\n{ident}"
    for rows in v.encounters.values():
        hit = rows[rows["encounter_id"] == ident]
        if len(hit):
            r = hit.iloc[0]
            return f"Encounter {ident}\n{r['kind']}, {r['department_id']}"
    return f"Encounter {ident}"


def draw_case(ax, v, row, title: str) -> None:
    nodes, edges, paths = case_graph(v, row)
    xy = {}
    middle = {"department": [n for n in nodes if n[0] == "department"], "encounter": [n for n in nodes if n[0] == "encounter"]}
    for n in nodes:
        if n[0] == "user":
            xy[n] = (0.0, 0.0)
        elif n[0] == "patient":
            xy[n] = (2.0, 0.0)
    for kind, y in (("department", 1.0), ("encounter", -1.0)):
        group = middle[kind]
        for i, n in enumerate(group):
            xy[n] = (1.0 + (i - (len(group) - 1) / 2) * 0.7, y)
    for (a, b), names in edges:
        (x1, y1), (x2, y2) = xy[a], xy[b]
        ax.plot([x1, x2], [y1, y2], color=MUTED, lw=1.2, zorder=1)
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx, my, "\n".join(names), ha="center", va="center", fontsize=7, color=INK, zorder=3,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none"))
    markers = {"user": ("o", HUE), "patient": ("o", HUE), "department": ("s", MUTED), "encounter": ("D", MUTED)}
    for n in nodes:
        m, c = markers[n[0]]
        ax.plot(*xy[n], m, color=c, ms=13, zorder=2)
        x, y = xy[n]
        if n[0] in ("user", "patient"):  # outside the drawing, clear of the edges
            ax.text(x + (-0.17 if n[0] == "user" else 0.17), y, node_text(v, n), ha="right" if n[0] == "user" else "left",
                    va="center", fontsize=7.5)
        else:
            up = n[0] == "department"
            ax.text(x, y + (0.2 if up else -0.2), node_text(v, n), ha="center", va="bottom" if up else "top", fontsize=7.5)
    ax.set_xlim(-0.9, 2.9)
    ax.set_ylim(-1.75, 1.65)
    ax.axis("off")
    ax.set_title(title, fontsize=9.5, loc="left")
    answers = (f"LLM: {row['disposition_raw']}, {row['priority_raw']} priority\n"
               f"LLM + Graph: {row['disposition']}, {row['priority']} priority")
    named = textwrap.fill("Connections named in the graph: " + ", ".join(paths), 52, break_on_hyphens=False) if paths else "No connection in the graph"
    ax.text(-0.9, -1.95, named + "\n\n" + answers, ha="left", va="top", fontsize=7.5, transform=ax.transData)


def fig04_identity_graph(run: Path, cfg: Config, out_dir: Path, results: Path) -> Path | None:
    picked = fig04_pick(run, results)
    if picked is None:
        return None
    from .triage import Views
    v = Views(run, "llm_graph", cfg)
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 3.9), gridspec_kw={"wspace": 0.25})
    for ax, role in zip(axes, ("misuse", "legitimate")):
        row = picked[role]
        draw_case(ax, v, row, f"{KIND_NAMES.get(row['stratum'], row['stratum'])}, case {row['case_id']}")
    fig.suptitle("One case in the identity graph", x=0.0, ha="left", fontsize=11, fontweight="bold", y=1.02)
    note(fig, "Each drawing is every edge on the evidence paths the LLM + Graph was shown for that case, at the "
              "encounter nearest the access. The organization co-worker connection is the patient being employed in a department, so it adds no edge of its own here. The LLM saw the same log lines and no graph. Both cases carry the same "
              "co-worker tie. Only the legitimate one also has a care-team path to the patient's stay.", y=-0.2)
    return save(fig, out_dir, "fig04-one-case-in-the-identity-graph")

# ---- the command ----

def build(run: str | Path, cfg: Config, out_dir: str | Path, results: str | Path = "results") -> dict:
    """Write every figure whose inputs exist. Returns written paths and skipped figures."""
    run, out_dir = Path(run), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [fig01_visibility(out_dir), fig03_calibration(run, cfg, out_dir)]
    skipped = []
    if (run / "cases.csv").exists():
        written.insert(1, fig02_first_accesses(run, cfg, out_dir))
    else:
        skipped.append("fig02, no case set in the run folder")
    fig04 = fig04_identity_graph(run, cfg, out_dir, Path(results)) if (run / "cases.csv").exists() else None
    if fig04 is None:
        skipped.append("fig04, no answers from both LLM systems, or no case that fits")
    else:
        written.append(fig04)
    return {"written": [str(p) for p in written], "skipped": skipped}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default=None, help="defaults to data/generated/seed<seed>")
    ap.add_argument("--config", default="config/calibration.yaml")
    ap.add_argument("--out", default="docs/figures")
    ap.add_argument("--results", default="results", help="folder holding one folder of LLM answers per run")
    args = ap.parse_args()
    cfg = load_config(args.config)
    run = args.run or f"data/generated/seed{cfg['seed']}"
    print(json.dumps(build(run, cfg, args.out, Path(args.results) / Path(run).name), indent=2))


if __name__ == "__main__":
    main()
