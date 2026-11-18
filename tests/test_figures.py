"""The figures, built from the made-up model's answers. Skipped when matplotlib is not installed."""
from pathlib import Path

import pytest

figures = pytest.importorskip("ehr_triage.figures", reason="matplotlib is a dev dependency used only for the figures")


def test_figures_are_written_from_the_answers(runs, answered, cfg, tmp_path):
    base, _ = runs
    out = figures.build(base, cfg, tmp_path, answered / base.name)
    names = sorted(p.name for p in tmp_path.glob("*.svg"))
    assert names == sorted(Path(p).name for p in out["written"])
    assert any(s.startswith("fig12") for s in out["skipped"])
    for start in ("fig01-", "fig02-", "fig03-", "fig04-", "fig05-", "fig06-", "fig07-", "fig08-", "fig09-", "fig10-", "fig11-", "fig13-"):
        assert any(n.startswith(start) for n in names)
    assert all((tmp_path / n).read_text().lstrip().startswith("<?xml") for n in names)
