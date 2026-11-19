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


def test_a_range_of_ids_counts_as_citing_every_fact_in_it():
    shown = {"L1", "L2", "L3", "L4", "G1", "G2"}
    assert triage.expand(["L2-L4", "G1"]) == ["L2", "L3", "L4", "G1"]
    assert triage.cited({"sources": ["L2-L4", "G1"]}, shown)
    assert not triage.cited({"sources": ["L2-L4"]}, shown, ranges=False)  # the stricter reading, one ID per source
    assert not triage.cited({"sources": ["L3-L5"]}, shown)  # L5 was never shown
    for not_a_range in ("L4-L2", "L2-G2", "L2-4", "L1-L99999"):
        assert not triage.cited({"sources": [not_a_range]}, shown)
    reply = json.dumps({"what_happened": "x", "rationale": "y", "disposition": "verify", "priority": "low",
                        "purpose_evidence": [{"claim": "Routine work around the access", "sources": ["L1-L4"]}], "risk_indicators": []})
    assert triage.parse_summary(reply, sorted(shown))["cited_ok"] == 1.0


def test_a_summary_outside_the_template_is_rejected():
    assert not triage.parse_summary("I think this is fine.", ["L1"])["valid"]
    bad = triage.parse_summary(json.dumps({"disposition": "ignore", "priority": "low", "what_happened": "x",
                                           "rationale": "y", "purpose_evidence": [], "risk_indicators": []}), ["L1"])
    assert bad["problems"] == ["disposition"]


def test_context_lines_are_the_nearest_to_any_of_the_weeks_accesses(run_dir, cfg):
    """Spec 11.3. No line left out may be nearer the week's accesses than a line that was shown."""
    import copy
    few = copy.deepcopy(cfg)
    few.params["triage"]["value"]["context_lines"] = 3  # so that some lines are always left out
    v = triage.Views(run_dir, "llm_raw", few)
    checked = 0
    units = triage.case_units(run_dir, few).sample(60, random_state=1)
    for u in units.itertuples():
        case, theirs = triage.case_events(v, u.user_id, u.patient_id, u.week)
        shown = {text.split(" ")[0] + text.split(" user ")[1] for ident, text in triage.log_facts(v, u.user_id, u.patient_id, u.week) if ident}
        anchors = case["t"].to_numpy()
        other = theirs[theirs["user_id"] != u.user_id]
        dist = triage.nearest(other["t"], anchors)
        key = other["timestamp"] + other["user_id"] + " patient " + other["patient_id"] + " device " + other["device_id"] \
            + " action " + other["action_code"] + " " + other["duration_s"].astype(str) + "s"
        is_shown = key.isin(shown)
        if is_shown.any() and (~is_shown).any():
            assert dist[is_shown].max() <= dist[~is_shown].min()
            checked += 1
    assert checked > 0


def test_the_declared_variant_words_employment_as_a_fact_and_changes_nothing_else(run_dir, cfg):
    """Spec 11.9. Same log lines and graph facts as the grounded system, except the organization co-worker line."""
    units = triage.case_units(run_dir, cfg)
    g, e = triage.Views(run_dir, "llm_graph", cfg), triage.Views(run_dir, triage.VARIANT, cfg)
    changed = 0
    for u in units.sample(300, random_state=2).itertuples():
        a, b = triage.case_facts(g, u), triage.case_facts(e, u)
        assert [t for i, t in a if i.startswith("L")] == [t for i, t in b if i.startswith("L")]
        assert not any("connection 'organization co-worker'" in t for _, t in b)
        other_a = [t for _, t in a if "organization co-worker" not in t and "holds no connection" not in t]
        other_b = [t for _, t in b if "is a member of staff" not in t and "holds no connection" not in t]
        assert other_a == other_b
        if any("organization co-worker" in t for _, t in a):
            changed += 1
            assert any(f"patient {u.patient_id} is a member of staff" in t for _, t in b)
    assert changed > 0
    assert triage.VARIANT not in triage.SYSTEMS
