from pathlib import Path

import yaml

from ehr_triage.config import load_config, validate

CONFIG = Path(__file__).resolve().parents[1] / "config" / "calibration.yaml"

# The measures in section 5.4 of the handover dossier.
PUBLISHED_TARGETS = {
    "accesses_per_staff_week", "repeat_access_share", "self_access_share", "rule_match_share", "alerts_per_access",
    "first_access_explained_direct", "first_access_explained_with_department_link", "all_accesses_explained",
    "events_per_user", "patients_per_user", "patients_per_user_day", "duration_median_s", "duration_p95_s",
    "immediate_repeat_share", "action_top21_share",
}


def test_shipped_config_is_valid_and_covers_every_published_target():
    cfg = load_config(CONFIG)
    assert set(cfg.targets) == PUBLISHED_TARGETS


def test_every_parameter_says_where_it_comes_from():
    raw = yaml.safe_load(CONFIG.read_text())
    for name, entry in raw["parameters"].items():
        assert entry["basis"] in {"published", "derived", "assumption", "setting"}, name
        if entry["basis"] in ("published", "derived"):
            assert entry.get("source"), f"{name} names no source"
        if entry["basis"] == "assumption":
            assert entry.get("note"), f"{name} is an unexplained assumption"


def test_validate_rejects_an_unsourced_published_value():
    raw = {"targets": {}, "parameters": {"x": {"value": 1, "basis": "published"}}}
    assert any("names no source" in p for p in validate(raw))


def test_validate_rejects_an_unexplained_assumption():
    raw = {"targets": {}, "parameters": {"x": {"value": 1, "basis": "assumption"}}}
    assert any("no note" in p for p in validate(raw))
