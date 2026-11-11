import json

import pytest

from ehr_triage import triage


@pytest.fixture(scope="module")
def prompts(run_dir, cfg):
    units = triage.case_units(run_dir, cfg).sample(40, random_state=0)
    out = {}
    for system in ("llm_raw", "llm_graph"):
        v = triage.Views(run_dir, system, cfg)
        out[system] = [triage.build_prompt(v, u) for u in units.itertuples()]
    return out


def test_raw_system_is_shown_log_lines_and_nothing_else(prompts):
    for text, ids in prompts["llm_raw"]:
        assert ids and all(i.startswith("L") for i in ids)
        assert "identity graph" not in text


def test_grounded_system_gets_the_same_log_lines_plus_graph_facts(prompts):
    for (raw, raw_ids), (grounded, ids) in zip(prompts["llm_raw"], prompts["llm_graph"]):
        assert [i for i in ids if i.startswith("L")] == raw_ids
        assert any(i.startswith("G") for i in ids)
        assert raw.split("Facts:")[1].strip() in grounded


def test_no_prompt_carries_a_label(prompts, run):
    words = set(run["scenarios"]["scenario_id"]) | {"undocumented_team", "back_office", "hospital_wide", "misuse", "scenario"}
    for system in prompts:
        for text, _ in prompts[system]:
            facts = text.split("Facts:")[1]
            assert not any(w in facts for w in words)


def test_a_well_formed_summary_passes_and_its_citations_are_checked():
    reply = json.dumps({
        "what_happened": "U1 opened P1 once.", "rationale": "Care team member.", "disposition": "clear", "priority": "low",
        "purpose_evidence": [{"claim": "On the care team", "sources": ["G2"]}],
        "risk_indicators": [{"claim": "Same surname", "sources": ["G9"]}, {"claim": "Odd hour", "sources": []}],
        "missing_information": [],
    })
    s = triage.parse_summary("Here you go: " + reply, ["L1", "G1", "G2"])
    assert s["valid"] and s["n_claims"] == 3
    assert s["cited_ok"] == pytest.approx(1 / 3)  # G9 was never shown, and one claim cites nothing


def test_a_summary_outside_the_template_is_rejected():
    assert not triage.parse_summary("I think this is fine.", ["L1"])["valid"]
    bad = triage.parse_summary(json.dumps({"disposition": "ignore", "priority": "low", "what_happened": "x",
                                           "rationale": "y", "purpose_evidence": [], "risk_indicators": []}), ["L1"])
    assert bad["problems"] == ["disposition"]
