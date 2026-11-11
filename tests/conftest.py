"""A small made-up population in Synthea's slim format, and one generated run.

The tests never need Java or the real Synthea export.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ehr_triage.config import load_config
from ehr_triage.generate import generate
from ehr_triage.report import load_run

ROOT = Path(__file__).resolve().parents[1]
SURNAMES = ["Abbott", "Baker", "Castro", "Diaz", "Evans", "Flores", "Garcia", "Hughes", "Ito", "Jones",
            "Khan", "Lopez", "Moore", "Nguyen", "Ortiz", "Patel", "Quinn", "Reyes", "Silva", "Tran"]
STREETS = ["Elm Street", "Oak Avenue", "Pine Road", "Maple Lane", "Cedar Court", "Birch Way"]
REASONS = ["", "", "", "Fracture of bone (disorder)", "Ischemic heart disease (disorder)", "Viral sinusitis (disorder)",
           "Hyperlipidemia", "Injury of neck (disorder)"]


def make_slim(folder: Path, n_patients: int = 500, seed: int = 7) -> Path:
    rng = np.random.default_rng(seed)
    ids = [f"pat-{i:04d}" for i in range(n_patients)]
    birth = pd.Timestamp("2026-09-01") - pd.to_timedelta(rng.integers(1, 85 * 365, n_patients), unit="D")
    pd.DataFrame(
        {
            "Id": ids, "BIRTHDATE": birth.strftime("%Y-%m-%d"), "FIRST": [f"First{i}" for i in range(n_patients)],
            "LAST": rng.choice(SURNAMES, n_patients), "GENDER": rng.choice(["M", "F"], n_patients),
            "ADDRESS": [f"{rng.integers(1, 999)} {rng.choice(STREETS)}" for _ in range(n_patients)],
            "CITY": "Testville", "ZIP": "01000",
            "LAT": 42.10 + rng.random(n_patients) * 0.02, "LON": -72.55 + rng.random(n_patients) * 0.02,
        }
    ).to_csv(folder / "patients.csv", index=False)
    n = n_patients * 40
    start = pd.Timestamp("2026-09-01") - pd.to_timedelta(rng.integers(1, 9 * 365 * 24 * 60, n), unit="min")
    cls = rng.choice(["ambulatory", "wellness", "outpatient", "emergency", "urgentcare", "inpatient", "virtual"],
                     n, p=[0.45, 0.2, 0.1, 0.07, 0.07, 0.08, 0.03])
    hours = np.where(cls == "inpatient", rng.integers(24, 200, n), 1)
    enc_ids = [f"enc-{i:06d}" for i in range(n)]
    pd.DataFrame(
        {
            "Id": enc_ids, "START": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "STOP": (start + pd.to_timedelta(hours, unit="h")).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "PATIENT": rng.choice(ids, n), "ENCOUNTERCLASS": cls, "DESCRIPTION": "Encounter for problem",
            "REASONDESCRIPTION": rng.choice(REASONS, n),
        }
    ).to_csv(folder / "encounters.csv", index=False)
    some = lambda share: [e for e in enc_ids if rng.random() < share]
    pd.DataFrame({"ENCOUNTER": some(0.4), "DESCRIPTION": "Test medication"}).to_csv(folder / "medications.csv", index=False)
    pd.DataFrame({"ENCOUNTER": some(0.03), "DESCRIPTION": "X-ray, Arm"}).to_csv(folder / "imaging.csv", index=False)
    pd.DataFrame({"ENCOUNTER": some(0.3), "N_RESULTS": 5}).to_csv(folder / "labs.csv", index=False)
    return folder


def small_config():
    """The real config, shrunk to a hospital of 81 full-time equivalents and two weeks."""
    cfg = load_config(ROOT / "config" / "calibration.yaml")
    small = {
        "window_days": 14,
        "staffing": {
            "MED1": {"physician": 2, "nurse": 6, "clerk": 1}, "MED2": {"physician": 2, "nurse": 6, "clerk": 1},
            "SURG": {"physician": 2, "nurse": 4, "clerk": 1}, "ICU": {"physician": 2, "nurse": 4, "clerk": 1},
            "ED": {"physician": 3, "nurse": 6, "clerk": 2}, "PCP": {"physician": 2, "nurse": 3, "clerk": 1},
            "CARD": {"physician": 1, "nurse": 2, "clerk": 1}, "ORTH": {"physician": 1, "nurse": 2, "clerk": 1},
            "LAB": {"technician": 4}, "RAD": {"physician": 1, "technician": 3},
            "PHARM": {"pharmacist": 3, "technician": 1}, "REG": {"clerk": 4}, "HIM": {"coder": 3}, "BILL": {"coder": 3},
            "CM": {"coordinator": 2},
        },
        "inpatient_census": {"MED1": 6, "MED2": 6, "SURG": 3, "ICU": 2},
        "ed_visits_per_day": 8,
        "clinic_visits_per_physician_day": {"PCP": 6, "CARD": 4, "ORTH": 4},
        "vip_share": 0.03,
        "scenario_counts": {"M1": 2, "M2a": 2, "M2b": 2, "M3": 2, "M4": 2, "M5_events": 1, "M6a": 1, "M6b": 1},
        "m5_users_per_event": [3, 5],
    }
    for name, value in small.items():
        cfg.params[name]["value"] = value
    return cfg


@pytest.fixture(scope="session")
def slim(tmp_path_factory) -> Path:
    return make_slim(tmp_path_factory.mktemp("slim"))


@pytest.fixture(scope="session")
def cfg():
    return small_config()


@pytest.fixture(scope="session")
def run_dir(slim, cfg, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("run")
    generate(cfg, slim, out)
    return out


@pytest.fixture(scope="session")
def run(run_dir) -> dict:
    return load_run(run_dir)
