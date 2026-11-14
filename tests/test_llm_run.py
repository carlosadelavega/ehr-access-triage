"""The case set, the runner and the scoring of systems 3 and 4, with a made-up model."""
import copy
import json
import shutil

import pandas as pd
import pytest

from ehr_triage import evaluate, triage
from ehr_triage.generate import generate

USAGE = {"prompt_tokens": 2000, "completion_tokens": 500, "completion_tokens_details": {"reasoning_tokens": 200}}


def fake_model(prompt, cfg):
    """Escalates when the graph shows no connection, clears otherwise, and cites the first fact shown."""
    if prompt.startswith(triage.JUDGE):
        n = prompt.count("\nClaim ")
        return {"content": json.dumps({"claims": [{"n": i + 1, "verdict": "supported" if i == 0 else "partly"} for i in range(n)]}), "finish": "stop", "usage": USAGE}
    escalate = "holds no connection" in prompt
    summary = {"what_happened": "One access.", "rationale": "As cited.", "missing_information": [],
               "disposition": "escalate" if escalate else "clear", "priority": "high" if escalate else "low",
               "purpose_evidence": [{"claim": "The access is in the log", "sources": ["L1"]}],
               "risk_indicators": [{"claim": "Made up", "sources": ["G999"]}] if escalate else []}
    return {"content": json.dumps(summary), "finish": "stop", "usage": USAGE}


def new_spend():
    return {"usd": 0.0, "calls": 0, "failed": 0, "last_error": "", "stop": ""}


@pytest.fixture(scope="module")
def runs(slim, cfg, run_dir, tmp_path_factory):
    """A copy of the test run and its paired run with covering accesses, each with a case set."""
    base = tmp_path_factory.mktemp("llm") / "base"
    shutil.copytree(run_dir, base)
    with_cover = copy.deepcopy(cfg)
    with_cover.params["covering"]["value"] = {**cfg["covering"], "enabled": True}
    covered = base.with_name("base-covered")
    generate(with_cover, slim, covered)
    evaluate.build_cases(base, cfg, covered)
    return base, covered


@pytest.fixture(scope="module")
def answered(runs, cfg, tmp_path_factory):
    results = tmp_path_factory.mktemp("results")
    for run in runs:
        triage.triage_cases(run, cfg, results / run.name, fake_model, new_spend())
        triage.judge_cases(run, cfg, results / run.name, fake_model, new_spend())
    return results


def test_case_set_holds_every_misuse_access_and_no_label(runs, cfg):
    base, covered = runs
    cases = pd.read_csv(base / "cases.csv")
    assert list(cases.columns) == ["case_id", "user_id", "patient_id", "week", "day"]
    truth = pd.read_csv(base / "labels" / "case_truth.csv")
    scored = evaluate.score_run(base, cfg)
    misuse = scored[scored["misuse"] != ""]
    picked = cases.merge(misuse, on=["user_id", "patient_id", "week"])
    assert len(picked) == len(misuse) > 0
    assert cases["case_id"].is_unique and set(cases["case_id"]) == set(truth["case_id"])
    assert (truth.groupby("stratum").size() <= truth.groupby("stratum")["population"].first()).all()


def test_paired_run_reuses_the_case_ids_of_its_targets(runs):
    base, covered = runs
    key = ["case_id", "user_id", "patient_id", "week"]
    pair = pd.read_csv(covered / "cases.csv").merge(pd.read_csv(covered / "labels" / "case_truth.csv"), on="case_id")
    targets = pair[pair["stratum"] != "cover"]
    assert len(targets) and len(targets.merge(pd.read_csv(base / "cases.csv"), on=key)) == len(targets)
    assert (pair["stratum"] == "cover").any()


def test_runner_needs_no_labels_folder(runs, cfg, tmp_path):
    base, _ = runs
    for name in ("logs", "context"):
        shutil.copytree(base / name, tmp_path / "run" / name)
    shutil.copy(base / "cases.csv", tmp_path / "run" / "cases.csv")
    spend = new_spend()
    triage.triage_cases(tmp_path / "run", cfg, tmp_path / "out", fake_model, spend, limit=5)
    rows = [json.loads(line) for line in (tmp_path / "out" / "llm_graph.pass1.jsonl").read_text().splitlines()]
    assert len(rows) == 5 and spend["calls"] == 10
    assert all(r["summary"]["valid"] and r["reasoning_tokens"] == 200 for r in rows)
    price = cfg["triage_provider"]["usd_per_million_tokens"]
    assert spend["usd"] == pytest.approx(10 * (2000 * price["input"] + 500 * price["output"]) / 1e6)  # no cost in the reply


def test_a_rerun_sends_only_what_is_missing(runs, cfg, tmp_path, monkeypatch):
    base, _ = runs
    monkeypatch.setattr(triage, "WAITS", (0, 0))
    sent = []

    def flaky(prompt, cfg):
        sent.append(prompt)
        if "L7 |" in prompt and len(sent) <= 40:  # fails on the first run for prompts with seven or more log lines
            raise OSError("HTTP 429: slow down")
        return fake_model(prompt, cfg)

    first = new_spend()
    triage.triage_cases(base, cfg, tmp_path, flaky, first, systems=["llm_raw"], limit=8)
    assert first["failed"] > 0 and first["last_error"] == "HTTP 429: slow down"
    sent.extend([""] * 40)
    before = len(sent)
    second = new_spend()
    triage.triage_cases(base, cfg, tmp_path, flaky, second, systems=["llm_raw"], limit=8)
    assert len(sent) - before == first["failed"] == second["calls"]
    assert len((tmp_path / "llm_raw.pass1.jsonl").read_text().splitlines()) == 8
    triage.triage_cases(base, cfg, tmp_path, flaky, new_spend(), systems=["llm_raw"], limit=8)
    assert len(sent) - before == first["failed"]  # nothing left to send


def test_an_answer_cut_off_by_the_token_limit_is_sent_again(runs, cfg, tmp_path):
    base, _ = runs

    def cut_off(prompt, cfg):
        return {"content": "", "finish": "length", "usage": USAGE}

    triage.triage_cases(base, cfg, tmp_path, cut_off, new_spend(), systems=["llm_raw"], limit=4)
    spend = new_spend()
    triage.triage_cases(base, cfg, tmp_path, fake_model, spend, systems=["llm_raw"], limit=4)
    assert spend["calls"] == 4
    rows = triage.read_answers(tmp_path / "llm_raw.pass1.jsonl")
    assert len(rows) == 4 and all(r["finish"] == "stop" and r["summary"]["valid"] for r in rows)
    assert len(evaluate.answers(tmp_path, "llm_raw.pass1")) == 4
    assert len(evaluate.calls(tmp_path / "llm_raw.pass1.jsonl")) == 8  # the cut-off calls were paid for too


def test_cost_cap_stops_the_run(runs, cfg, tmp_path):
    base, _ = runs
    tight = copy.deepcopy(cfg)
    tight.params["triage"]["value"] = {**cfg["triage"], "cost_cap_usd": 0.0004, "workers": 1}
    spend = new_spend()
    triage.triage_cases(base, tight, tmp_path, fake_model, spend, limit=50)
    assert "cost cap" in spend["stop"] and spend["calls"] < 10
    assert not (tmp_path / "llm_graph.pass1.jsonl").exists()  # the second system never started


def test_a_refused_request_stops_the_run(runs, cfg, tmp_path):
    base, _ = runs

    def refused(prompt, cfg):
        raise triage.ModelError("HTTP 401: bad key")

    spend = new_spend()
    triage.triage_cases(base, cfg, tmp_path, refused, spend, limit=20)
    assert "HTTP 401" in spend["stop"] and spend["calls"] == 0


def test_request_pins_the_provider_and_the_charge_reported_is_what_counts(cfg, monkeypatch):
    sent = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"provider": "Relace", "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 2000, "completion_tokens": 500, "cost": 0.00042}}).encode()

    def urlopen(request, timeout):
        sent["url"], sent["body"], sent["auth"] = request.full_url, json.loads(request.data), request.get_header("Authorization")
        return Reply()

    monkeypatch.setattr(triage.urllib.request, "urlopen", urlopen)
    for name, value in (("BASE_URL", "https://openrouter.ai/api/v1/"), ("API_KEY", "k"), ("MODEL", "z-ai/glm-5.3-flash")):
        monkeypatch.setenv("EHR_TRIAGE_" + name, value)
    provider = cfg["triage_provider"]
    reply = triage.call_model("hello", cfg, extras=provider["triage_extras"])
    assert sent["url"] == "https://openrouter.ai/api/v1/chat/completions" and sent["auth"] == "Bearer k"
    assert sent["body"]["model"] == "z-ai/glm-5.3-flash" and sent["body"]["reasoning"] == {"enabled": True}
    assert sent["body"]["provider"] == {"only": ["relace/fp4"], "allow_fallbacks": False}
    assert reply["provider"] == "Relace" and triage.cost(reply["usage"], cfg) == pytest.approx(0.00042)
    triage.call_model("hello", cfg, model="deepseek/deepseek-v4.1-flash-1017", extras=provider["judge_extras"])
    assert sent["body"]["model"] == "deepseek/deepseek-v4.1-flash-1017" and sent["body"]["provider"]["only"] == ["sail-research/fp4/us"]
    assert sent["body"]["reasoning"] == {"effort": "low"}


def test_judge_asks_only_about_claims_whose_citations_exist(answered, runs):
    base, _ = runs
    rows = [json.loads(line) for line in (answered / base.name / "judge.llm_graph.pass1.jsonl").read_text().splitlines()]
    assert rows and all(r["n_claims"] == 1 and r["verdicts"] == ["supported"] for r in rows)  # the claim citing G999 was left out
    reply = '{"claims": [{"n": 2, "verdict": "maybe"}, {"n": 1, "verdict": "unsupported", "absence": true}, {"n": 4, "verdict": "partly"}]}'
    assert triage.parse_judgement(reply, 4) == {"verdicts": ["unsupported", None, None, "partly"], "absence": [True, False, False, False]}
    assert triage.parse_judgement("no idea", 2) == {"verdicts": [None, None], "absence": [False, False]}


def test_judge_sees_each_claim_with_its_citations_and_then_the_full_list():
    summary = {"purpose_evidence": [{"claim": "On shift", "sources": ["G2"]}], "risk_indicators": [{"claim": "Made up", "sources": ["G9"]}]}
    shown = [("", "-- the access under review (4 more lines not shown)"), ("L1", "a log line"), ("G2", "a schedule row")]
    prompt, n = triage.judge_prompt(summary, shown)
    assert n == 1 and "Claim 1: On shift\n  G2 | a schedule row" in prompt and "Made up" not in prompt
    assert prompt.endswith("Full list of facts:\n-- the access under review (4 more lines not shown)\nL1 | a log line\nG2 | a schedule row")


def test_judge_is_given_every_fact_of_a_cited_range():
    summary = {"purpose_evidence": [{"claim": "Two lines in a row", "sources": ["L1-L2"]}], "risk_indicators": []}
    prompt, n = triage.judge_prompt(summary, [("L1", "first line"), ("L2", "second line"), ("L3", "third line")])
    assert n == 1 and "Claim 1: Two lines in a row\n  L1 | first line\n  L2 | second line\n\nFull list" in prompt


def test_a_judged_case_is_sent_again_when_its_number_of_claims_changes(runs, cfg, tmp_path):
    base, _ = runs
    triage.triage_cases(base, cfg, tmp_path, fake_model, new_spend(), systems=["llm_raw"], limit=3)
    first, again = new_spend(), new_spend()
    triage.judge_cases(base, cfg, tmp_path, fake_model, first, systems=["llm_raw"], limit=3)
    triage.judge_cases(base, cfg, tmp_path, fake_model, again, systems=["llm_raw"], limit=3)
    assert first["calls"] == 3 and again["calls"] == 0
    path = tmp_path / "judge.llm_raw.pass1.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["n_claims"] = 0  # as if it had been judged when fewer of its claims passed the first check
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    spend = new_spend()
    triage.judge_cases(base, cfg, tmp_path, fake_model, spend, systems=["llm_raw"], limit=3)
    assert spend["calls"] == 1 and len(triage.read_answers(path)) == 3


def test_render_fills_the_header_from_the_log_and_writes_out_the_cited_facts(runs, cfg, tmp_path):
    base, _ = runs
    for name in ("logs", "context"):  # rendering needs no labels folder
        shutil.copytree(base / name, tmp_path / name)
    shutil.copy(base / "cases.csv", tmp_path / "cases.csv")
    v = triage.Views(tmp_path, "llm_graph", cfg)
    unit = next(triage.read_cases(tmp_path).itertuples())
    shown = triage.case_facts(v, unit)
    ids = [ident for ident, _ in shown if ident]
    reply = json.dumps({"what_happened": "One access.", "rationale": "As cited.", "disposition": "verify", "priority": "low",
                        "missing_information": ["Who assigned the work"],
                        "purpose_evidence": [{"claim": "On shift", "sources": ["G1-G2"]}],
                        "risk_indicators": [{"claim": "Made up", "sources": ["G999"]}]})
    row = {"summary": triage.parse_summary(reply, ids), "content": reply, "model": "some-model", "pass": 1}
    text = triage.render_case(v, unit, row)
    mine = v.events[(v.events["user_id"] == unit.user_id) & (v.events["patient_id"] == unit.patient_id) & (v.events["week"] == unit.week)]
    assert f"# Case {unit.case_id}" in text and f"| Accesses | {len(mine)} |" in text
    assert f"| First access | {mine['timestamp'].min()} |" in text and "| Raised by | LLM with identity graph (some-model, pass 1) |" in text
    facts = dict(shown)
    assert f"G1 | {facts['G1']}\nG2 | {facts['G2']}\n```" in text  # the range, fact by fact, and nothing uncited
    assert "1. On shift [G1-G2]" in text and "never shown to the model, so not checkable. G999" in text
    assert "**Disposition.** verify, priority low" in text and "- Who assigned the work" in text
    assert all(f"- {item}" in text for item in triage.OFFICER_PART)
    assert "No answer is on file" in triage.render_case(v, unit, None)
    broken = {"summary": triage.parse_summary("no idea", ids), "content": "no idea", "model": "some-model", "pass": 1}
    assert "did not fit the template (not JSON)" in triage.render_case(v, unit, broken)


def test_average_day_budget_splits_a_tie_by_weight():
    # one misuse alert and one legitimate alert standing for 9, all at the same priority, over 2 days
    found, shown = evaluate.average_day_top_k([1, 1], [True, False], [1, 9], k=1, days=2)
    assert shown == pytest.approx(2) and found == pytest.approx(0.2)
    found, shown = evaluate.average_day_top_k([2, 1], [True, False], [1, 9], k=1, days=2)
    assert found == pytest.approx(1.0)  # ranked first, the misuse alert is always reviewed


def test_llm_results_report_runs_end_to_end(answered, runs, cfg, tmp_path):
    base, covered = runs
    out = tmp_path / "llm.md"
    done = evaluate.write_llm_results(base, cfg, out, answered / base.name, covered, answered / covered.name)
    text = out.read_text()
    assert done["llm_raw"] == done["llm_graph"] == done["sampled"]
    for heading in ("## Alerts raised", "## Dispositions, by kind of access", "## When the patient is also staff", "## Within a daily review budget",
                    "## Faithfulness of the summaries", "## With covering accesses", "## Tokens and cost"):
        assert heading in text
    assert "## Repeat passes" not in text
    assert "Without counting ranges" in text and "Not yet judged" in text
    raw = evaluate.scored_answers(base, answered / base.name, "llm_raw")
    assert (raw["disposition"] == "clear").all()  # the made-up model only escalates on a graph fact
    by_kind = raw.groupby("stratum")
    assert by_kind["weight"].sum().to_numpy() == pytest.approx(by_kind["population"].first().to_numpy())
