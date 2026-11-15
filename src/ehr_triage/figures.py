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

# ---- shared by figures 5 to 8 ----

VARIANT_NAMES = {
    "Rules, all five": "Rules",
    "Rules, without organization co-worker": "Rules, without organization co-worker",
    "Templates, direct evidence": "Templates, direct evidence only",
    "Templates, direct and responsibility": "Templates",
    "Rules, then direct evidence": "Best baseline",
}
KEY = ["user_id", "patient_id", "week"]


def llm_frames(run: Path, results: Path) -> dict | None:
    """Both LLM systems' answers joined to the truth, or None unless both are there."""
    frames = {s: evaluate.scored_answers(run, results, s) for s in ("llm_raw", "llm_graph")}
    return None if any(f is None for f in frames.values()) else frames


def staff_patients(run: Path) -> set:
    return set(pd.read_csv(run / "context" / "staff.csv", keep_default_na=False)["patient_id"])


# ---- figure 5 ----

DISPOSITION_SHADES = {"clear": LIGHT, "verify": HUE_LIGHT, "escalate": HUE}


def fig05_dispositions(run: Path, cfg: Config, out_dir: Path, frames: dict, u: pd.DataFrame) -> Path:
    """Clear, verify and escalate on every row of the matrix, centred on verify."""
    cells = evaluate.matrix_cells(run, u)
    rows = [(cell, KIND_NAMES.get(cell, label)) for cell, label in evaluate.MATRIX_ROWS]
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 6.4), sharey=True, gridspec_kw={"wspace": 0.06})
    y = np.arange(len(rows))[::-1]
    for ax, system in zip(axes, ("llm_raw", "llm_graph")):
        f = frames[system]
        mine = f[KEY].merge(cells, on=KEY, how="left").set_axis(f.index)
        counts = []
        for cell, _ in rows:
            part = mine["own_department"].astype(bool) if cell == "own" else mine["cell"] == cell
            d = f.loc[part, "disposition"].value_counts()
            counts.append([int(d.get(x, 0)) for x in ("clear", "verify", "escalate")])
        counts = np.array(counts, dtype=float)
        n = counts.sum(axis=1)
        share = counts / np.where(n == 0, 1, n)[:, None]
        left = -share[:, 1] / 2 - share[:, 0]
        for j, name in enumerate(("clear", "verify", "escalate")):
            ax.barh(y, share[:, j], left=left, color=DISPOSITION_SHADES[name], height=0.72, edgecolor="white", lw=0.5)
            for yi, s, l, c in zip(y, share[:, j], left, counts[:, j]):
                if s >= 0.12:
                    ax.text(l + s / 2, yi, f"{int(c)}", ha="center", va="center", fontsize=7,
                            color="white" if name == "escalate" else INK)
            left = left + share[:, j]
        ax.axvline(0, color=INK, lw=0.6)
        ax.set_xlim(-1.02, 1.02)
        ax.set_xticks([-1, -0.5, 0, 0.5, 1], ["100%", "50%", "0", "50%", "100%"])
        ax.set_title(SYSTEM_NAMES[system], fontsize=9.5)
        ax.tick_params(axis="y", length=0)
        ax.axhline(y[9] - 0.5, color=MUTED, lw=0.6, ls=(0, (2, 2)))
        ax.spines["left"].set_visible(False)
        top = y[0] + 0.75
        for x, word in ((-0.75, "Clear"), (0, "Verify"), (0.75, "Escalate")):
            ax.text(x, top, word, ha="center", va="bottom", fontsize=8, color=MUTED)
    axes[0].set_yticks(y, [f"{label}, {int(c)} cases" for (_, label), c in zip(rows, n)])
    axes[0].set_ylim(y[-1] - 0.6, y[0] + 1.4)
    axes[0].text(-1.0, y[9] - 0.42, "Misuse above", fontsize=7, color=MUTED, va="bottom", ha="left")
    axes[0].text(-1.0, y[9] - 0.58, "Legitimate below", fontsize=7, color=MUTED, va="top", ha="left")
    fig.suptitle("Dispositions by scenario", x=0.0, ha="left", fontsize=11, fontweight="bold", y=0.99)
    note(fig, "Share of cases sent to clear, verify and escalate on each row of the expected-outcome matrix (spec 5.2), "
              "with the bars centred on verify. Numbers in the bars are cases, and the number after each name is the cases "
              "on that row. Counts are in the sample and not weighted. The row on the target seen in the actor's department "
              "is a subset of the misuse rows above it. Same counts as the matrix in docs/llm-results.md.", y=0.0)
    return save(fig, out_dir, "fig05-dispositions-by-scenario")


# ---- figure 6 ----

def system_points(u: pd.DataFrame, frames: dict, days: int) -> list[dict]:
    """Every system as alerts per day and misuse caught, the numbers of the alerts table in llm-results.md."""
    is_misuse = u["misuse"] != ""
    points = []
    for name, (alert, _) in evaluate.variants(u).items():
        points.append(dict(name=VARIANT_NAMES[name], alerts=alert.sum() / days, margin=0.0,
                           caught=int((alert & is_misuse).sum()), llm=False))
    for system in ("llm_raw", "llm_graph"):
        f = frames[system]
        for label, alert in (("escalate", f["disposition"] == "escalate"), ("verify or escalate", f["disposition"] != "clear")):
            total, margin = evaluate.weighted_alerts(f, alert)
            points.append(dict(name=f"{SYSTEM_NAMES[system]}, {label}", alerts=total / days, margin=margin / days,
                               caught=int((alert & f["is_misuse"]).sum()), llm=True))
    return points


def fig06_alerts_and_catches(cfg: Config, out_dir: Path, frames: dict, u: pd.DataFrame) -> Path:
    days = cfg["window_days"]
    points = system_points(u, frames, days)
    n_misuse = int((u["misuse"] != "").sum())
    floor = 0.3  # where a system with no alerts at all is drawn on the log scale
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    ax.set_xscale("log")
    ax.set_xlim(0.2, 6000)
    ax.set_ylim(-5, n_misuse + 8)
    ax.axhline(n_misuse, color=LIGHT, lw=0.8)
    ax.text(0.22, n_misuse + 1.5, f"All {n_misuse} misuse first accesses", fontsize=7.5, color=MUTED)
    offsets = {"Rules": (6, 6), "Rules, without organization co-worker": (-6, -11), "Templates": (-8, -9),
               "Templates, direct evidence only": (-8, 8), "Best baseline": (-6, 7), "LLM, verify or escalate": (8, -1)}
    for p in points:
        x = max(p["alerts"], floor)
        if p["llm"]:
            if p["margin"] > 0:
                ax.plot([max(p["alerts"] - p["margin"], floor), p["alerts"] + p["margin"]], [p["caught"]] * 2, color=HUE, lw=1.2)
            ax.plot(x, p["caught"], "o", color=HUE, ms=6, zorder=3)
        else:
            ax.plot(x, p["caught"], "s", color=MUTED, ms=6, zorder=3)
        dx, dy = offsets.get(p["name"], (7, 4) if p["alerts"] < 1000 else (-7, 4))
        label = p["name"] + (" (no alerts)" if p["alerts"] == 0 else "")
        ax.annotate(label, (x, p["caught"]), xytext=(dx, dy), textcoords="offset points", fontsize=7.5,
                    ha="left" if dx > 0 else "right", va="center")
    ax.set_xlabel("Alerts per day, over all first accesses (log scale)")
    ax.set_ylabel(f"Misuse caught, of {n_misuse}")
    ax.set_xticks([1, 10, 100, 1000], ["1", "10", "100", "1,000"])
    ax.set_title("Alerts against catches", pad=10)
    note(fig, "Squares are the rules and templates, counted on every first access. Circles are the LLM systems at two "
              "thresholds, escalate alone and verify or escalate, weighted back from the case set. The bars on the circles "
              "are 95% intervals from the sampling alone, and say nothing about run-to-run variation. The LLM at escalate "
              "raised no alerts and is drawn at the left edge. Same numbers as the alerts table in docs/llm-results.md.", y=-0.04)
    return save(fig, out_dir, "fig06-alerts-against-catches")


# ---- figure 7 ----

def budget_curves(cfg: Config, u: pd.DataFrame, frames: dict) -> dict:
    """Misuse found over the window at each daily budget, for the four systems drawn."""
    days, budgets = cfg["window_days"], list(cfg["review_budget_per_day"])
    is_misuse = u["misuse"] != ""
    v = evaluate.variants(u)
    curves = {}
    for name in ("Rules, all five", "Rules, then direct evidence"):
        alert, score = v[name]
        curves[VARIANT_NAMES[name]] = [evaluate.average_day_top_k(score[alert], is_misuse[alert], np.ones(int(alert.sum())), k, days)[0]
                                       for k in budgets]
    for system in ("llm_graph", "llm_raw"):
        f = frames[system]
        a = f[f["disposition"] != "clear"]  # ranked by disposition, then priority, so escalations come first
        curves[SYSTEM_NAMES[system]] = [evaluate.average_day_top_k(a["score"], a["is_misuse"], a["weight"], k, days)[0] for k in budgets]
    return {"budgets": budgets, "curves": curves}


def fig07_review_budget(cfg: Config, out_dir: Path, frames: dict, u: pd.DataFrame) -> Path:
    data = budget_curves(cfg, u, frames)
    budgets = data["budgets"]
    n_misuse = int((u["misuse"] != "").sum())
    styles = {"Best baseline": ("s", MUTED, "-"), "Rules": ("s", MUTED, ":"), "LLM + Graph": ("o", HUE, "-"), "LLM": ("o", HUE, ":")}
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    above = {"Best baseline": True, "Rules": False, "LLM + Graph": False, "LLM": True}
    ends = sorted(data["curves"].items(), key=lambda kv: kv[1][-1])
    placed = []
    for name, found in ends:  # end labels from the bottom up, pushed apart so they never touch
        y_end = found[-1] if not placed else max(found[-1], placed[-1] + 2.4)
        placed.append(y_end)
        ax.text(budgets[-1] + 2, y_end, f"{name}, {int(found[-1])}", va="center", fontsize=8, color=INK)
    for name, found in data["curves"].items():
        m, c, ls = styles[name]
        ax.plot(budgets, found, marker=m, color=c, ls=ls, lw=1.4, ms=5)
        for k, v in zip(budgets[:-1], found[:-1]):
            ax.text(k, v + (1.0 if above[name] else -1.0), f"{int(v)}", ha="center", va="bottom" if above[name] else "top",
                    fontsize=7, color=c)
    ax.set_xticks(budgets, [str(k) for k in budgets])
    ax.set_xlim(budgets[0] - 3, budgets[-1] + 18)
    ax.set_ylim(0, max(max(c) for c in data["curves"].values()) * 1.25)
    ax.set_xlabel("Alerts a reviewer takes each day")
    ax.set_ylabel(f"Misuse found over {cfg['window_days']} days, of {n_misuse}")
    ax.set_title("Inside a review budget", pad=10)
    note(fig, "The reviewer takes the highest-ranked alerts each day. Rules are ranked by their observed-to-expected ratios, "
              "the LLM systems by disposition and then the priority the model gave. Every system is scored on an average "
              "day, as in the budget table in docs/llm-results.md, and counts are rounded down. The budgets of 10, 25 and 50 are an assumption.", y=-0.04)
    return save(fig, out_dir, "fig07-inside-a-review-budget")


# ---- figure 8 ----

def fig08_staff_charts(run: Path, out_dir: Path, frames: dict) -> Path:
    """Share escalated by the LLM + Graph on staff charts and on other charts, per kind of legitimate access."""
    f = frames["llm_graph"]
    staff = f["patient_id"].isin(staff_patients(run))
    kinds = [k for k in evaluate.LEGITIMATE if k in set(f["stratum"])]
    rows = []
    for kind in kinds:
        part = f["stratum"] == kind
        row = {"kind": kind}
        for side, mask in (("staff", part & staff), ("other", part & ~staff)):
            n, k = int(mask.sum()), int((mask & (f["disposition"] == "escalate")).sum())
            row[side] = (k, n, *evaluate.wilson(k, n))
        rows.append(row)
    rows.sort(key=lambda r: -(r["staff"][0] / max(r["staff"][1], 1)))
    fig, ax = plt.subplots(figsize=(8.2, 0.42 * len(rows) + 1.4))
    y = np.arange(len(rows))[::-1]
    for yi, r in zip(y, rows):
        (ks, ns, ls, hs), (ko, no, lo, ho) = r["staff"], r["other"]
        ps, po = ks / max(ns, 1), ko / max(no, 1)
        ax.plot([po, ps], [yi, yi], color=LIGHT, lw=3, zorder=1)
        ax.plot([ls, hs], [yi + 0.14] * 2, color=HUE, lw=1, zorder=2)
        ax.plot([lo, ho], [yi - 0.14] * 2, color=MUTED, lw=1, zorder=2)
        ax.plot(ps, yi + 0.14, "o", color=HUE, ms=6, zorder=3)
        ax.plot(po, yi - 0.14, "o", mfc="white", mec=MUTED, mew=1.4, ms=6, zorder=3)
        ax.text(1.03, yi, f"{ks} of {ns}", va="center", fontsize=7.5, color=HUE, transform=ax.get_yaxis_transform())
        ax.text(1.16, yi, f"{ko} of {no}", va="center", fontsize=7.5, color=MUTED, transform=ax.get_yaxis_transform())
    ax.text(1.03, y[0] + 0.7, "Staff", fontsize=7.5, color=HUE, transform=ax.get_yaxis_transform(), fontweight="bold")
    ax.text(1.16, y[0] + 0.7, "Other", fontsize=7.5, color=MUTED, transform=ax.get_yaxis_transform(), fontweight="bold")
    ax.set_yticks(y, [KIND_NAMES.get(r["kind"], r["kind"]) for r in rows])
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(0, 1)
    ax.set_ylim(y[-1] - 0.6, y[0] + 1.0)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1], ["0", "25%", "50%", "75%", "100%"])
    ax.set_xlabel("Share of legitimate cases the LLM + Graph escalated")
    ax.set_title("Staff charts and other charts", pad=10)
    note(fig, "Filled circles are cases where the patient is also staff, open circles the rest. Thin bars are 95% Wilson "
              "intervals. Counts are cases in the sample, from the staff split in docs/llm-results.md. Staff are 11% of "
              "patients here, about three times as many staff charts as the published data, a gap reported and not tuned.", y=-0.02)
    return save(fig, out_dir, "fig08-staff-charts-and-other-charts")


# ---- figure 9 ----

# Whether each prediction of spec 5.2 held, one mark per row of evaluate.MATRIX_ROWS in its order. Grouped from the
# leading words of the Reading column of docs/results-reading-draft.md, as Carlos reviewed it (15 Nov 2026):
# "As predicted", "A miss in substance, as predicted" and "As the scenarios themselves" are held (H), "Mostly as
# predicted" and "Partly contradicted" are partly (P), "Contradicted" is contradicted (C), and "Open question" is
# open (O). When a reading in the draft changes, change its mark here.
MATRIX_MARKS = {
    #             M1   M2a  M2b+ M2b- M3   M4   M5   M6a  M6b  own  H1   H2a  H2b  H3   H4a  H4b  H5   H6
    "rules":     "H    H    H    P    H    P    H    H    P    H    P    P    P    P    H    H    P    P",
    "templates": "H    H    H    H    H    H    H    H    H    C    H    H    H    H    H    H    H    H",
    "llm_raw":   "H    H    H    H    H    H    C    C    H    H    H    H    H    H    C    H    H    H",
    "llm_graph": "H    H    H    H    H    P    P    P    C    O    C    H    P    H    H    P    C    C",
}
MARK_WORDS = {"H": "✓ held", "P": "~ partly", "C": "✗ contradicted", "O": "? open"}


def matrix_counts(run: Path, u: pd.DataFrame, frames: dict) -> dict:
    """(system, row) to (cases, alerted), counted as in the matrix of llm-results.md."""
    cells = evaluate.matrix_cells(run, u)
    v = evaluate.variants(u)
    counted = {"rules": (cells, v["Rules, all five"][0]), "templates": (cells, v["Templates, direct and responsibility"][0])}
    for s, f in frames.items():
        counted[s] = (f[KEY].merge(cells, on=KEY, how="left").set_axis(f.index), f["disposition"] == "escalate")
    out = {}
    for system, (mine, alert) in counted.items():
        for cell, _ in evaluate.MATRIX_ROWS:
            part = (mine["own_department"].astype(bool) if cell == "own" else mine["cell"] == cell).to_numpy()
            out[system, cell] = (int(part.sum()), int((alert.to_numpy() & part).sum()))
    return out


def fig09_predicted_and_observed(run: Path, out_dir: Path, frames: dict, u: pd.DataFrame) -> Path:
    counts = matrix_counts(run, u, frames)
    systems = list(MATRIX_MARKS)
    marks = {s: MATRIX_MARKS[s].split() for s in systems}
    assert all(len(m) == len(evaluate.MATRIX_ROWS) for m in marks.values())
    shades = matplotlib.colors.LinearSegmentedColormap.from_list("share", ["#ffffff", HUE])
    fig, ax = plt.subplots(figsize=(8.8, 8.2))
    n_rows = len(evaluate.MATRIX_ROWS)
    for i, (cell, label) in enumerate(evaluate.MATRIX_ROWS):
        y = n_rows - 1 - i
        for j, s in enumerate(systems):
            n, hit = counts[s, cell]
            share = hit / n if n else 0.0
            ax.add_patch(plt.Rectangle((j + 0.04, y + 0.06), 0.92, 0.88, color=shades(0.12 + 0.88 * share), lw=0))
            ax.add_patch(plt.Rectangle((j + 0.04, y + 0.06), 0.92, 0.88, fill=False, ec=LIGHT, lw=0.6))
            ink = "white" if share > 0.55 else INK
            ax.text(j + 0.5, y + 0.64, f"{share:.0%} of {n:,}" if n else "no cases", ha="center", va="center", fontsize=7.5, color=ink)
            word = MARK_WORDS[marks[s][i]]
            ax.text(j + 0.5, y + 0.32, word, ha="center", va="center", fontsize=7.5, color=ink,
                    fontweight="bold" if marks[s][i] in ("C", "P") else "normal")
    ax.axhline(n_rows - 10, color=INK, lw=0.8)
    ax.set_xlim(0, len(systems))
    ax.set_ylim(0, n_rows)
    ax.set_xticks([j + 0.5 for j in range(len(systems))], [SYSTEM_NAMES[s] for s in systems])
    ax.xaxis.tick_top()
    ax.set_yticks([n_rows - 1 - i + 0.5 for i in range(n_rows)], [KIND_NAMES.get(c, c) for c, _ in evaluate.MATRIX_ROWS])
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.set_title("Predicted against observed", pad=28)
    note(fig, "Each tile is one row of the expected-outcome matrix (spec 5.2) for one system. The share is the cases "
              "alerted: a rule fired, no explanation was found, or the model escalated. Verify is not counted as an alert "
              "here. Darker tiles alert more. Misuse is above the line, legitimate work below it. The mark says whether the prediction held, grouped from the Reading column "
              "of the reading draft. Rules and templates are counted on every first access, the LLM systems on the case "
              "set, as in the matrix of docs/llm-results.md.", y=0.03)
    return save(fig, out_dir, "fig09-predicted-against-observed")


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
    frames = llm_frames(run, Path(results)) if (run / "cases.csv").exists() else None
    if frames is None:
        skipped.append("fig05-fig08, no answers from both LLM systems")
    else:
        u = evaluate.score_run(run, cfg)
        written += [fig05_dispositions(run, cfg, out_dir, frames, u), fig06_alerts_and_catches(cfg, out_dir, frames, u),
                    fig07_review_budget(cfg, out_dir, frames, u), fig08_staff_charts(run, out_dir, frames),
                    fig09_predicted_and_observed(run, out_dir, frames, u)]
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
