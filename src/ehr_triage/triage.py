"""Systems 3 and 4: an LLM that triages a first access and drafts the case summary.

System 3 (`llm_raw`) is shown log lines only. System 4 (`llm_graph`) is
shown the same lines plus facts from the identity graph. Both get their
data through `graph.load_visible`, so neither can reach labels/.

Every fact shown to the model carries an ID (L1, L2 for log lines, G1, G2
for graph facts). The summary must cite those IDs, which is what makes
faithfulness checkable.

`run` triages the case set written by `python -m ehr_triage.evaluate cases`
and `judge` checks the claims of the summaries. Both append to JSON lines
files under results/, skip what is already there, and stop at a cost cap.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import functools
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

from .baselines import first_access_units
from .config import Config, load_config
from .graph import build_graph, evidence_paths, load_visible

SYSTEMS = ("llm_raw", "llm_graph")
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
WAITS = (2, 5, 15, 30, 60)  # seconds to wait before each new try of a failed call
DISPOSITIONS = ["clear", "verify", "escalate"]
PRIORITIES = ["low", "medium", "high"]

INSTRUCTIONS = """You are assisting a hospital Privacy Officer. One user opened one patient's record. \
Decide what the officer should do with it and draft the case summary.

Use only the numbered facts below. Every claim you make must cite the fact IDs it rests on. \
If the facts do not settle the question, say so. Do not guess a purpose or a relationship that no fact shows.

Dispositions:
- clear: the facts show a job-related purpose, or nothing in them calls for review
- verify: the facts neither show a purpose nor show a concern that stands on its own. Ask the supervisor
- escalate: the facts show a concern, such as a personal connection with no job-related purpose

Answer with one JSON object and nothing else:
{"what_happened": "one or two sentences",
 "purpose_evidence": [{"claim": "...", "sources": ["L1", "G2"]}],
 "risk_indicators": [{"claim": "...", "sources": ["G4"]}],
 "missing_information": ["what the officer would need to confirm"],
 "disposition": "clear | verify | escalate",
 "priority": "low | medium | high",
 "rationale": "at most 60 words"}"""


JUDGE = """You are checking a draft case summary against its sources. Each claim below is followed by the facts it cites. \
For each claim, say whether the cited facts alone support it. A claim is supported when a careful reader could confirm it \
from those facts and nothing else. A claim that goes beyond them, or that needs a fact it does not cite, is not supported.

Answer with one JSON object and nothing else:
{"claims": [{"n": 1, "supported": true}, {"n": 2, "supported": false}]}"""


class Views:
    """Fast lookups into the log and, for system 4, the identity graph."""

    def __init__(self, run_dir: str | Path, system: str, cfg: Config):
        self.system, self.cfg = system, cfg
        self.t = load_visible(run_dir, system)
        ev = self.t["events"]
        since = pd.to_datetime(ev["timestamp"]) - pd.Timestamp(cfg["window_start"])
        self.events = ev.assign(week=since.dt.days // 7, t=since.dt.total_seconds())
        self.by_user = self.events.groupby("user_id").indices
        self.by_patient_week = self.events.groupby(["patient_id", "week"]).indices
        if system == "llm_graph":
            t = self.t
            self.graph = build_graph(t, cfg["proximity_miles"])
            self.staff = t["staff"].set_index("user_id")
            self.dept = t["departments"].set_index("department_id")["name"].to_dict()
            self.device = t["devices"].set_index("device_id")
            self.action = t["actions"].set_index("action_code")["category"].to_dict()
            self.vip = set(t["patients"].loc[t["patients"]["vip"].astype(str) == "True", "patient_id"])
            self.schedule = {u: g for u, g in t["schedule"].groupby("user_id")}
            seen = t["encounters"].rename(columns={"class": "kind"})
            self.encounters = {p: g for p, g in seen.groupby("patient_id")}


def case_units(run_dir: str | Path, cfg: Config) -> pd.DataFrame:
    """The first accesses to triage. Chosen by the harness, the same for
    every system, so the systems are compared on identical cases."""
    t = load_visible(run_dir, "rules")
    return first_access_units(t["events"], t["staff"], cfg["window_start"])


def log_facts(v: Views, user: str, patient: str, week: int) -> list[tuple[str, str]]:
    """The access under review, what else the user did around it, and who else opened the patient that week.

    The user's other lines are the ones nearest the access in time, within
    a fixed number of hours either side. A calendar day would cut a night
    shift in two.
    """
    cap, hours = v.cfg["triage"]["context_lines"], v.cfg["triage"]["context_hours"]
    e = v.events
    theirs = e.iloc[v.by_patient_week.get((patient, week), [])]
    case = theirs[theirs["user_id"] == user]
    away = (e["t"].iloc[v.by_user[user]] - case["t"].iloc[0]).abs()
    mine = e.loc[away.index[(away <= hours * 3600).to_numpy()]]
    mine = mine[mine["patient_id"] != patient]
    around = mine.loc[away[mine.index].sort_values(kind="stable").index[:cap]].sort_values("event_id")
    others = theirs[theirs["user_id"] != user].head(cap)
    facts, n = [], 0
    for title, rows, more in (
        ("the access under review", case.head(cap), len(case) - cap),
        (f"the same user, within {hours} hours, other patients", around, len(mine) - len(around)),
        ("the same patient, same week, other users", others, len(theirs) - len(case) - len(others)),
    ):
        facts.append(("", f"-- {title}" + (f" ({more} more lines not shown)" if more > 0 else "")))
        for r in rows.itertuples():
            n += 1
            facts.append((f"L{n}", f"{r.timestamp} user {r.user_id} patient {r.patient_id} device {r.device_id} "
                                   f"action {r.action_code} {r.duration_s}s"))
    return facts


def graph_facts(v: Views, user: str, patient: str, day: int, case_lines: list[str]) -> list[tuple[str, str]]:
    """Who the user is, where the patient was seen, and every connection between them."""
    out: list[str] = []
    at = pd.Timestamp(case_lines[0].split(" ")[0])
    s = v.staff.loc[user]
    out.append(f"user {user} is a {s['role']} in {s['department_id']} ({v.dept[s['department_id']]}), {s['employment']}, "
               f"usual shift {s['usual_shift']}")
    date = (pd.Timestamp(v.cfg["window_start"]) + pd.Timedelta(days=int(day))).strftime("%Y-%m-%d")
    shifts = v.schedule.get(user)
    near = shifts[(shifts["shift_start"].str[:10] <= date) & (shifts["shift_end"].str[:10] >= date)] if shifts is not None else []
    for r in getattr(near, "itertuples", lambda: [])():
        out.append(f"schedule: {user} is scheduled {r.shift_start} to {r.shift_end} in {r.department_id}")
    if not len(near):
        out.append(f"schedule: {user} has no scheduled shift on {date}")
    seen = v.encounters.get(patient)
    if seen is None:
        out.append(f"patient {patient} has no encounter in the period")
    else:
        gap = (pd.to_datetime(seen["start"]) - at).abs()  # show the encounters nearest the access
        for r in seen.loc[gap.sort_values().index[: v.cfg["triage"]["encounters_shown"]]].sort_values("start").itertuples():
            out.append(f"encounter {r.encounter_id}: {patient} {r.kind} in {r.department_id} "
                       f"{r.start} to {r.stop}" + (f", reason {r.reason_description}" if r.reason_description else ""))
    if patient in v.vip:
        out.append(f"patient {patient} carries the VIP flag")
    paths = evidence_paths(v.graph, user, patient, at)
    for name, nodes in paths.items():
        out.append(f"connection '{name}': " + " - ".join(f"{kind} {ident}" for kind, ident in nodes))
    if not paths:
        out.append(f"the identity graph holds no connection between {user} and {patient}")
    devices = sorted({line.split(" device ")[1].split(" ")[0] for line in case_lines})
    for d in devices:
        out.append(f"device {d} is a {v.device.loc[d, 'kind']} in {v.device.loc[d, 'department_id']}")
    actions = sorted({line.split(" action ")[1].split(" ")[0] for line in case_lines})
    out.append("action categories: " + ", ".join(f"{a} {v.action[a]}" for a in actions))
    return [(f"G{i + 1}", text) for i, text in enumerate(out)]


def case_facts(v: Views, unit) -> list[tuple[str, str]]:
    """Every line shown for one first access, as (fact ID, text). Section titles have no ID."""
    facts = log_facts(v, unit.user_id, unit.patient_id, unit.week)
    if v.system == "llm_graph":
        case_lines = []
        for ident, text in facts[1:]:
            if not ident:
                break
            case_lines.append(text)
        facts += [("", "-- identity graph")] + graph_facts(v, unit.user_id, unit.patient_id, unit.day, case_lines)
    return facts


def build_prompt(v: Views, unit) -> tuple[str, list[str]]:
    """The prompt for one first access, and the fact IDs it contains."""
    facts = case_facts(v, unit)
    body = "\n".join(f"{ident} | {text}" if ident else text for ident, text in facts)
    ids = [ident for ident, _ in facts if ident]
    return f"{INSTRUCTIONS}\n\nUser {unit.user_id}, patient {unit.patient_id}.\n\nFacts:\n{body}", ids


def claims_of(summary: dict) -> list[dict]:
    """The sourced claims of a summary, purpose evidence first, then risk indicators."""
    return [c for field in ("purpose_evidence", "risk_indicators")
            for c in (summary.get(field) if isinstance(summary.get(field), list) else []) if isinstance(c, dict)]


def cited(claim: dict, known) -> bool:
    """True when the claim names at least one source and every source it names was shown."""
    sources = claim.get("sources")
    return isinstance(sources, list) and bool(sources) and all(isinstance(x, str) and x in known for x in sources)


def parse_summary(text: str, fact_ids: list[str]) -> dict:
    """Read the model's JSON and check it against the template.

    Returns the summary with `valid`, `problems`, and `cited_ok`, the share
    of claims whose cited IDs all exist among the facts shown. A claim with
    no source, or with a made-up ID, counts against it.
    """
    start, end = text.find("{"), text.rfind("}")
    try:
        s = json.loads(text[start : end + 1])
        assert isinstance(s, dict)
    except (ValueError, TypeError, AssertionError):
        return {"valid": False, "problems": ["not JSON"], "n_claims": 0, "cited_ok": 0.0}
    problems = []
    if s.get("disposition") not in DISPOSITIONS:
        problems.append("disposition")
    if s.get("priority") not in PRIORITIES:
        problems.append("priority")
    for field in ("what_happened", "rationale"):
        if not isinstance(s.get(field), str) or not s[field].strip():
            problems.append(field)
    problems += [field for field in ("purpose_evidence", "risk_indicators") if not isinstance(s.get(field), list)]
    claims = claims_of(s)
    known = set(fact_ids)
    good = [cited(c, known) for c in claims]
    s.update(valid=not problems, problems=problems, n_claims=len(claims),
             cited_ok=sum(good) / len(good) if good else 1.0)
    return s


def judge_prompt(summary: dict, facts: dict[str, str]) -> tuple[str, int]:
    """Ask whether each claim follows from the facts it cites, shown alone.

    Claims that cite nothing, or cite an ID that was never shown, already
    failed the first check and are left out. Returns the prompt and the
    number of claims in it.
    """
    claims = [c for c in claims_of(summary) if cited(c, facts)]
    parts = []
    for n, c in enumerate(claims, 1):
        parts.append(f"Claim {n}: {c.get('claim', '')}\n" + "\n".join(f"  {x} | {facts[x]}" for x in c["sources"]))
    return JUDGE + "\n\n" + "\n\n".join(parts), len(claims)


def parse_judgement(text: str, n: int) -> list:
    """One verdict per claim, in order: True, False, or None where the judge gave none."""
    try:
        items = json.loads(text[text.find("{") : text.rfind("}") + 1])["claims"]
        verdict = {int(i["n"]): i["supported"] for i in items}
    except (ValueError, TypeError, KeyError):
        return [None] * n
    return [verdict[k] if isinstance(verdict.get(k), bool) else None for k in range(1, n + 1)]


# ---- talking to the model ----

class ModelError(RuntimeError):
    """The endpoint refused the request in a way that a retry will not fix."""


def load_env(path: str | Path = ".env") -> None:
    """Read KEY=VALUE lines from a gitignored .env file. Values already set in the environment win."""
    if not Path(path).exists():
        return
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.replace("export ", "").strip(), value.strip().strip("'\""))


def call_model(prompt: str, cfg: Config, model: str | None = None) -> dict:
    """One chat completion against an OpenAI-compatible endpoint.

    The endpoint, key and model come from the environment, so no secret is
    ever written to the repo: EHR_TRIAGE_BASE_URL, EHR_TRIAGE_API_KEY and
    EHR_TRIAGE_MODEL. Returns the text, why the model stopped, and the
    token usage. Raises OSError for failures worth retrying and ModelError
    for the rest.
    """
    body = {"model": model or os.environ["EHR_TRIAGE_MODEL"], "temperature": cfg["triage"]["temperature"],
            "max_tokens": cfg["triage"]["max_output_tokens"], "messages": [{"role": "user", "content": prompt}],
            **cfg["triage_provider"]["request_extras"]}
    request = urllib.request.Request(
        os.environ["EHR_TRIAGE_BASE_URL"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["EHR_TRIAGE_API_KEY"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as e:
        detail = f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}"
        if e.code in RETRY_STATUS:
            raise OSError(detail) from None
        raise ModelError(detail) from None
    except ValueError as e:
        raise OSError(f"reply was not JSON: {e}") from None
    if not reply.get("choices"):
        raise OSError(f"reply had no choices: {json.dumps(reply)[:400]}")
    choice = reply["choices"][0]
    return {"content": choice["message"].get("content") or "", "finish": choice.get("finish_reason") or "",
            "usage": reply.get("usage") or {}}


def attempt(call, prompt: str, cfg: Config) -> dict:
    """Call the model, waiting and trying again after a timeout, a rate limit or a server error."""
    for wait in (*WAITS, None):
        try:
            return call(prompt, cfg)
        except OSError as e:  # covers timeouts, dropped connections and the retryable HTTP statuses
            if wait is None:
                return {"error": str(e)}
            time.sleep(wait)


def cost(usage: dict, cfg: Config) -> float:
    """What one call cost in USD at the configured list prices. Reasoning tokens are part of completion_tokens."""
    price = cfg["triage_provider"]["usd_per_million_tokens"]
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    fresh = (usage.get("prompt_tokens") or 0) - cached
    return (fresh * price["input"] + cached * price["cached_input"] + (usage.get("completion_tokens") or 0) * price["output"]) / 1e6


def run_jobs(jobs: list, out_path: Path, call, cfg: Config, spend: dict, label: str) -> None:
    """Send each job's prompt to the model and append one JSON line per answer.

    A job is (key, prompt, finish). `key` identifies the answer and `finish`
    turns the reply text into the fields to store. A case already in the
    file is skipped, so an interrupted run picks up where it stopped and a
    rerun retries only what failed. `spend` is shared across calls of this
    function. Once it passes the cost cap, or the endpoint refuses a
    request outright, nothing more is sent.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(line)["case_id"] for line in out_path.read_text().splitlines() if line.strip()} if out_path.exists() else set()
    todo = [job for job in jobs if job[0]["case_id"] not in done]
    cap = cfg["triage"]["cost_cap_usd"]
    print(f"{label}: {len(todo)} to send, {len(jobs) - len(todo)} already answered")

    lock = threading.Lock()

    def one(prompt):
        """Runs on a worker thread. Keeps the running spend, so the cap holds however fast answers come back."""
        if spend["stop"]:
            return None
        try:
            reply = attempt(call, prompt, cfg)
        except ModelError as e:
            spend["stop"] = spend["stop"] or f"the endpoint refused the request. {e}"
            return None
        with lock:
            if "error" in reply:
                spend["failed"] += 1
                spend["last_error"] = reply["error"]
                return None
            reply["usd"] = cost(reply["usage"], cfg)
            spend["usd"] += reply["usd"]
            spend["calls"] += 1
            if spend["usd"] >= cap and not spend["stop"]:
                spend["stop"] = f"the cost cap of ${cap} was reached (triage.cost_cap_usd in the config)"
        return reply

    with cf.ThreadPoolExecutor(cfg["triage"]["workers"]) as pool, out_path.open("a") as f:
        futures = {pool.submit(one, prompt): (key, finish) for key, prompt, finish in todo}
        try:
            for n, future in enumerate(cf.as_completed(futures), 1):
                key, finish = futures[future]
                reply = future.result()
                if reply is None:
                    continue
                usage = reply["usage"]
                f.write(json.dumps({
                    **key, "finish": reply["finish"], "prompt_tokens": usage.get("prompt_tokens") or 0,
                    "completion_tokens": usage.get("completion_tokens") or 0,
                    "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0,
                    "usd": round(reply["usd"], 6), "content": reply["content"], **finish(reply["content"])}) + "\n")
                f.flush()
                if n % 100 == 0:
                    print(f"{label}: {n} of {len(todo)}, ${spend['usd']:.2f} spent by this command")
        except BaseException:  # Ctrl-C or a bug: let the calls still queued return at once, then pass it on
            spend["stop"] = spend["stop"] or "interrupted"
            raise


def read_cases(run_dir: str | Path, limit: int | None = None) -> pd.DataFrame:
    path = Path(run_dir) / "cases.csv"
    if not path.exists():
        raise SystemExit(f"{path} is missing. Write the case set first with: python -m ehr_triage.evaluate cases")
    cases = pd.read_csv(path, keep_default_na=False)
    return cases.head(limit) if limit else cases


def triage_cases(run_dir: str | Path, cfg: Config, results_dir: str | Path, call, spend: dict, systems=SYSTEMS,
                 limit: int | None = None, pass_no: int = 1, model: str = "") -> None:
    """Systems 3 and 4 on the case set of one run. One results file per system and pass."""
    cases = read_cases(run_dir, limit)
    for system in systems:
        v = Views(run_dir, system, cfg)
        jobs = []
        for unit in cases.itertuples():
            prompt, ids = build_prompt(v, unit)
            key = {"case_id": unit.case_id, "system": system, "pass": pass_no, "model": model}
            jobs.append((key, prompt, lambda text, ids=ids: {"summary": parse_summary(text, ids)}))
        run_jobs(jobs, Path(results_dir) / f"{system}.pass{pass_no}.jsonl", call, cfg, spend, f"{Path(run_dir).name} {system}")
        if spend["stop"]:
            return


def judge_cases(run_dir: str | Path, cfg: Config, results_dir: str | Path, call, spend: dict, systems=SYSTEMS,
                limit: int | None = None, pass_no: int = 1, model: str = "") -> None:
    """Second faithfulness check: does each claim follow from the facts it cites?

    Reads the summaries already written, rebuilds the facts each case was
    shown, and asks a model about every claim whose citations exist. A
    summary with no such claim is not sent.
    """
    units = {u.case_id: u for u in read_cases(run_dir, limit).itertuples()}
    for system in systems:
        answers = Path(results_dir) / f"{system}.pass{pass_no}.jsonl"
        if not answers.exists():
            continue
        v = Views(run_dir, system, cfg)
        jobs = []
        for line in answers.read_text().splitlines():
            row = json.loads(line)
            if row["case_id"] not in units:
                continue
            prompt, n = judge_prompt(row["summary"], dict(f for f in case_facts(v, units[row["case_id"]]) if f[0]))
            if n:
                key = {"case_id": row["case_id"], "system": system, "pass": pass_no, "model": model, "n_claims": n}
                jobs.append((key, prompt, lambda text, n=n: {"supported": parse_judgement(text, n)}))
        run_jobs(jobs, Path(results_dir) / f"judge.{system}.pass{pass_no}.jsonl", call, cfg, spend, f"{Path(run_dir).name} judge {system}")
        if spend["stop"]:
            return


def estimate_tokens(text: str) -> int:
    """Rough token count. IDs and timestamps split into many tokens, so this
    divides by 3 characters where prose would be nearer 4."""
    return len(text) // 3


def dry_run(run_dir: str | Path, cfg: Config, sample: int = 400) -> dict:
    """Average prompt size per system, measured on real prompts, with no model call."""
    out = {}
    every = case_units(run_dir, cfg)
    units = every.sample(min(sample, len(every)), random_state=0)
    for system in SYSTEMS:
        v = Views(run_dir, system, cfg)
        sizes = [estimate_tokens(build_prompt(v, u)[0]) for u in units.itertuples()]
        out[system] = {"first_accesses": len(every), "mean_prompt_tokens": int(sum(sizes) / len(sizes)),
                       "max_prompt_tokens": max(sizes)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", nargs="?", default="sizes", choices=["sizes", "show", "run", "judge"],
                    help="sizes: prompt sizes, no model call. show: print one prompt. run: triage the case set. "
                         "judge: check the claims of the summaries already written")
    ap.add_argument("--run", default=None, help="defaults to data/generated/seed<seed>")
    ap.add_argument("--config", default="config/calibration.yaml")
    ap.add_argument("--results", default=None, help="defaults to results/<run folder name>")
    ap.add_argument("--systems", default=",".join(SYSTEMS))
    ap.add_argument("--limit", type=int, default=None, help="only the first N cases of each case set, for a cheap trial")
    ap.add_argument("--pass", dest="pass_no", type=int, default=1, help="number of this pass, for repeat runs")
    args = ap.parse_args()
    cfg = load_config(args.config)
    run = Path(args.run or f"data/generated/seed{cfg['seed']}")
    systems = args.systems.split(",")
    if args.command == "sizes":
        print(json.dumps(dry_run(run, cfg), indent=2))
        return
    if args.command == "show":
        v = Views(run, systems[0], cfg)
        print(build_prompt(v, next(case_units(run, cfg).sample(1, random_state=1).itertuples()))[0])
        return

    load_env()
    missing = [name for name in ("EHR_TRIAGE_BASE_URL", "EHR_TRIAGE_API_KEY", "EHR_TRIAGE_MODEL") if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"Set {', '.join(missing)} in the environment or in a .env file. See .env.example")
    model = os.environ["EHR_TRIAGE_MODEL"]
    if args.command == "judge":
        model = os.environ.get("EHR_TRIAGE_JUDGE_MODEL") or model
    try:  # one small call first, so a bad key, address or setting shows in seconds and not after a round of retries
        call_model('Reply with this JSON object and nothing else: {"ok": true}', cfg, model)
    except (OSError, ModelError) as e:
        raise SystemExit(f"The endpoint did not answer a test call, so nothing was sent. {e}") from None
    step = triage_cases if args.command == "run" else judge_cases
    spend = {"usd": 0.0, "calls": 0, "failed": 0, "last_error": "", "stop": ""}
    runs = [run] + [r for r in [run.with_name(run.name + "-covered")] if (r / "cases.csv").exists()]
    folders = [Path(args.results or "results") / r.name for r in runs]
    for r, results in zip(runs, folders):
        step(r, cfg, results, functools.partial(call_model, model=model), spend, systems, args.limit, args.pass_no, model)
        if spend["stop"]:
            break
    print(f"{spend['calls']} calls answered, ${spend['usd']:.2f} at list prices, {spend['failed']} gave up after retries")
    if spend["failed"]:
        print(f"Last error: {spend['last_error']}\nRun the same command again to retry them.")
    files = [f / f"{s}.pass{args.pass_no}.jsonl" for f in folders for s in systems]
    rows = [json.loads(line) for path in files if path.exists() for line in path.read_text().splitlines()]
    if args.command == "run" and rows:
        print(f"{sum(r['summary']['valid'] for r in rows)} of {len(rows)} answers on file fit the template, "
              f"{sum(r['finish'] == 'length' for r in rows)} were cut off by the token limit")
    if args.command == "run" and args.limit and spend["calls"]:
        total = sum(len(read_cases(r)) for r in runs) * len(systems)
        print(f"At this rate the full case set, {total:,} calls, would cost about ${spend['usd'] / spend['calls'] * total:.2f}")
        print("One answer, as the model wrote it:\n" + rows[-1]["content"])
    if spend["stop"]:
        raise SystemExit(f"Stopped early: {spend['stop']}\nAnswers so far are kept. The same command carries on from here.")


if __name__ == "__main__":
    main()
