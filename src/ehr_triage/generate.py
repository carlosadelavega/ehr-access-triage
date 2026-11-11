"""Generate one run: logs, context and labels, each in its own folder."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import encounters, events, population, scenarios
from .config import Config, load_config


def generate(cfg: Config, slim_dir: str | Path, out_dir: str | Path) -> dict:
    rng = np.random.default_rng(cfg["seed"])
    syn = population.load_synthea(slim_dir)
    patients, rel = population.build_patients(syn["patients"], cfg, rng)
    staff = population.build_staff(patients, cfg, rng)
    devices, usual, usual_cum, by_dept, shared = population.build_devices(staff, cfg, rng)
    shifts = population.build_shifts(staff, cfg, rng)
    actions, action_probs = population.build_actions(cfg, rng)
    enc, stats = encounters.build_encounters(syn, patients, staff, cfg, rng)
    work, care, orders, _ = encounters.assign_work(enc, staff, shifts, syn, cfg, rng)
    legit = work.frame()
    extra, scen, public, notes = scenarios.inject(legit, staff, patients, rel, enc, orders, shifts, cfg, rng)
    req = pd.concat([legit, extra], ignore_index=True)
    ev = events.expand(req, staff, usual, usual_cum, by_dept, shared, action_probs, cfg, rng)

    ws = pd.Timestamp(cfg["window_start"])

    def stamp(seconds) -> pd.Series:
        t = ws + pd.to_timedelta(np.floor(np.asarray(seconds, dtype=float)), unit="s")
        return pd.Series(t).dt.strftime("%Y-%m-%dT%H:%M:%S").to_numpy()

    pid = patients["patient_id"].to_numpy()
    uid = staff["user_id"].to_numpy()
    did = devices["device_id"].to_numpy()
    eid = enc["encounter_id"].to_numpy()
    out = Path(out_dir)
    logs, context, labels = out / "logs", out / "context", out / "labels"
    for d in (logs, context, labels):
        d.mkdir(parents=True, exist_ok=True)

    # ---- logs: what every system sees ----
    pd.DataFrame(
        {
            "event_id": ev["event_id"], "timestamp": stamp(ev["ts"]), "user_id": uid[ev["u"]], "patient_id": pid[ev["p"]],
            "device_id": did[ev["device"]], "action_code": actions["action_code"].to_numpy()[ev["action"]],
            "duration_s": ev["duration_s"],
        }
    ).to_csv(logs / "access_events.csv", index=False)

    # ---- context: what a hospital holds about its people and their care ----
    cols = ["patient_id", "first_name", "last_name", "birth_date", "gender", "address", "city", "zip", "lat", "lon",
            "emergency_contact_id", "vip"]
    patients[cols].to_csv(context / "patients.csv", index=False)
    pd.DataFrame(
        {"user_id": uid, "patient_id": pid[staff["pidx"]], "role": staff["role"], "department_id": staff["dept"],
         "usual_shift": staff["usual_shift"], "employment": staff["employment"]}
    ).to_csv(context / "staff.csv", index=False)
    pd.DataFrame(
        [(k, v["name"], v["kind"]) for k, v in cfg["departments"].items()], columns=["department_id", "name", "kind"]
    ).to_csv(context / "departments.csv", index=False)
    devices.rename(columns={"dept": "department_id"})[["device_id", "department_id", "kind"]].sort_values(
        "device_id").to_csv(context / "devices.csv", index=False)
    shown = np.where(shifts["floated"] & shifts["float_documented"], shifts["work_dept"], shifts["dept"])
    pd.DataFrame(
        {"user_id": uid[shifts["u"]], "date": stamp(shifts["day"] * 86400), "shift_start": stamp(shifts["start"]),
         "shift_end": stamp(shifts["end"]), "department_id": shown}
    ).assign(date=lambda d: d["date"].str[:10]).to_csv(context / "schedule.csv", index=False)
    pd.DataFrame(
        {"encounter_id": eid, "patient_id": pid[enc["p"]], "class": enc["cls"], "start": stamp(enc["start"]),
         "stop": stamp(enc["stop"]), "department_id": enc["dept"], "reason_description": enc["reason"],
         "description": enc["description"], "synthea_encounter_id": enc["syn_id"]}
    ).to_csv(context / "encounters.csv", index=False)
    care = care.sort_values(["e", "start", "u"], kind="stable")
    pd.DataFrame(
        {"encounter_id": eid[care["e"]], "user_id": uid[care["u"]], "team_role": care["team_role"],
         "start": stamp(care["start"]), "stop": stamp(care["stop"])}
    ).to_csv(context / "care_team.csv", index=False)
    orders = orders.sort_values("t", kind="stable").reset_index(drop=True)
    by = orders["ordered_by"].to_numpy()
    pd.DataFrame(
        {"order_id": [f"O{i + 1:06d}" for i in range(len(orders))], "encounter_id": eid[orders["e"]],
         "patient_id": pid[orders["p"]], "kind": orders["kind"], "ordered_by": np.where(by >= 0, uid[by], ""),
         "performing_department_id": orders["performing_dept"], "timestamp": stamp(orders["t"]),
         "description": orders["description"]}
    ).to_csv(context / "orders.csv", index=False)
    actions[["action_code", "category", "description"]].sort_values("action_code").to_csv(
        context / "actions.csv", index=False)

    # ---- labels: ground truth, for evaluation code only ----
    pd.DataFrame(
        {"event_id": ev["event_id"], "reason": ev["reason"], "documented": ev["documented"],
         "scenario_id": ev["scenario"], "part": np.where(ev["scenario"] != "", "target", "")}
    ).to_csv(labels / "event_labels.csv", index=False)
    borrower, target = scen["borrower_user"].to_numpy(), scen["target_patient"].to_numpy()
    pd.DataFrame(
        {"scenario_id": scen["scenario_id"], "type": scen["type"], "group_id": scen["group_id"],
         "actor_user_id": uid[scen["actor_user"]], "borrower_user_id": np.where(borrower >= 0, uid[borrower], ""),
         "target_patient_id": np.where(target >= 0, pid[target], ""), "start": stamp(scen["start"]),
         "end": stamp(scen["end"]), "covered": scen["covered"], "evidence_in_context": scen["evidence_in_context"]}
    ).to_csv(labels / "scenarios.csv", index=False)
    pd.DataFrame(
        {"patient_id_a": pid[rel["a"]], "patient_id_b": pid[rel["b"]], "kind": rel["kind"],
         "same_household": rel["same_household"]}
    ).to_csv(labels / "relationships.csv", index=False)
    pd.DataFrame(
        {"public_event_id": public["public_event_id"], "patient_id": pid[public["p"]], "timestamp": stamp(public["t"])}
    ).to_csv(labels / "public_events.csv", index=False)

    manifest = {
        "config_digest": cfg.digest,
        "seed": cfg["seed"],
        "synthea": cfg["synthea"],
        "window_start": cfg["window_start"],
        "window_days": cfg["window_days"],
        "rows": {"events": len(ev), "patients": len(patients), "staff": len(staff), "devices": len(devices),
                 "encounters": len(enc), "care_team": len(care), "orders": len(orders), "scenarios": len(scen)},
        "encounter_stats": stats,
        "scenario_notes": notes,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=float) + "\n")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config/calibration.yaml")
    ap.add_argument("--synthea", default="data/synthea/slim")
    ap.add_argument("--out", default=None, help="defaults to data/generated/seed<seed>")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = args.out or f"data/generated/seed{cfg['seed']}"
    manifest = generate(cfg, args.synthea, out)
    print(json.dumps(manifest["rows"]))
    print(f"Written to {out}")


if __name__ == "__main__":
    main()
