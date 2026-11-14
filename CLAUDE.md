# CLAUDE.md

Research repo testing LLM triage of EHR access anomalies on synthetic data. The handover dossier in the claude.ai project knowledge is the source of truth for scope and research. `docs/spec.md` is the source of truth for the data model and scenarios. Read both before planning work.

## Hard constraints

- Synthetic data only. No real patient data and nothing derived from any real system.
- Public sources only for design. Schema, scenarios and calibration come from published papers, the Herrera Montano field list and ASTM E2147. Nothing from non-public sources. Every design choice should be traceable to a source or marked as an assumption.
- Say "EHR", never "EMR".
- The repo is purely academic. Keep personal and professional details about the author out of every file and commit message.
- Rules baseline is the five rules from Hedda et al. 2017 and nothing else, so results stay comparable.
- Present explanation-based auditing as complementary prior work, never as a weakness.

## Evaluation integrity

- Ground-truth labels live in `data/generated/<run>/labels/`, apart from `logs/` and `context/`. No baseline or LLM system reads `labels/`. Only evaluation code does.
- No column in `logs/` or `context/` may be derived from a label.
- The expected-outcome matrix in the spec is a prediction to test. Report results that contradict it.

## Calibration

- Every calibration parameter lives in `config/calibration.yaml` with its source from dossier section 5.4. Parameters with no published source are marked `basis: assumption`.
- When generated values miss a target, report the gap. Do not tune quietly. Any tuning is a config change with a commit message that says why.

## Scope and workflow

- Keep abstractions to what the current milestone needs. A working pipeline and an honest README beat a platform.
- If a session drifts into re-planning instead of shipping, say so.
- Changes to the spec get reviewed by Carlos before code that depends on them.
- Commit in small steps. Messages explain why, not only what. No session links and no AI co-author trailers in commit messages.
- If a paper or standard needed for a design choice is not available in full, tell Carlos straight away.
- Generated data, the Synthea jar and `.env` stay out of git. The API key lives only in `.env` or the environment.
- Never run `triage run` or `triage judge` against a real endpoint without Carlos asking for it. They spend his money.

## Decisions reserved for Carlos

Flag these when they come up. Do not decide them.

- LLM provider, model and budget. Decided for now: GLM 5.3 Flash, key and endpoint supplied by Carlos on his own machine.
- Privacy Officer summary template (fields, length, disposition options). Approved on 12 Nov 2026 as written in spec section 10. Violation categories follow the Columbia University sanction policy directly. Every part of the template names its public source.

## Writing voice (README, docs, article)

No em dashes, no emojis, no colons before lists. Light prose, no corporate jargon. Round numbers down to what is certain, use a plain hyphen for ranges, and claim nothing beyond the evidence.

## Stack and commands

Python 3.10+, pandas, NumPy, PyYAML, NetworkX, pytest, managed with uv. Synthea v3.2.0 jar is downloaded by `scripts/fetch_synthea.sh` into `vendor/` and needs Java 11 or newer.

```
scripts/run_synthea.sh           # population into data/synthea/slim, slow, rarely needed
python -m ehr_triage.generate    # one run into data/generated/seed<seed>
python -m ehr_triage.generate --covered   # the paired run with covering accesses, into seed<seed>-covered
python -m ehr_triage.report      # rewrites docs/calibration-report.md, never edit that file by hand
python -m ehr_triage.evaluate    # rewrites docs/baseline-results.md, same rule
python -m ehr_triage.evaluate cases       # the case set for the LLM systems, cases.csv in each run folder
python -m ehr_triage.triage      # prompt sizes for the two LLM systems, no model call
python -m ehr_triage.triage run --limit 20   # needs .env, calls the model, costs money
python -m ehr_triage.triage judge         # second faithfulness check, also calls the model
python -m ehr_triage.evaluate llm         # rewrites docs/llm-results.md from results/, same rule
pytest                           # runs on a made-up population and a made-up model, no Java or key needed
```

After any change to the generator or the config, rerun generate, report and evaluate and commit the new reports with the change.

Systems under test load data only through `graph.load_visible`. Only `report.py` and `evaluate.py` may read `labels/`.
