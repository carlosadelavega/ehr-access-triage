"""Load and validate the calibration config."""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

BASES = {"published", "derived", "assumption", "setting"}
USES = {"level", "shape", "reported", "later"}


class Config:
    """Parameter values by name, plus the raw entries for the report."""

    def __init__(self, raw: dict, digest: str = ""):
        self.targets: dict = raw["targets"]
        self.params: dict = raw["parameters"]
        self.digest = digest

    def __getitem__(self, name: str):
        return self.params[name]["value"]


def validate(raw: dict) -> list[str]:
    """Return a list of problems. Empty means the config is usable."""
    problems = []
    for name, entry in raw.get("parameters", {}).items():
        if "value" not in entry:
            problems.append(f"parameter {name} has no value")
        basis = entry.get("basis")
        if basis not in BASES:
            problems.append(f"parameter {name} has basis {basis!r}")
        if basis in ("published", "derived") and not entry.get("source"):
            problems.append(f"parameter {name} is {basis} but names no source")
        if basis == "assumption" and not entry.get("note"):
            problems.append(f"parameter {name} is an assumption with no note")
    for name, entry in raw.get("targets", {}).items():
        if "value" not in entry or not entry.get("source"):
            problems.append(f"target {name} needs a value and a source")
        if entry.get("use") not in USES:
            problems.append(f"target {name} has use {entry.get('use')!r}")
    return problems


def load_config(path: str | Path) -> Config:
    data = Path(path).read_bytes()
    raw = yaml.safe_load(data)
    problems = validate(raw)
    if problems:
        raise ValueError("Invalid config:\n  " + "\n  ".join(problems))
    return Config(raw, hashlib.sha256(data).hexdigest()[:12])
