# Spec: data model and scenarios

Status: draft for review. This covers steps 1 and 2 of the build order. No generator code exists yet, and section 8 lists the decisions that need an answer before it does.

## 1. Scope of this milestone

One synthetic hospital with about 300 staff, about 3,500 patients and 30 days of EHR access logs. The output has to serve four systems later (rules, explanation templates, an LLM on raw log lines, an LLM grounded on an identity graph), so the main job of this spec is to fix what each of them is allowed to see.

## 2. Output layout and visibility

```
data/generated/<run_id>/
  logs/access_events.csv     the audit log
  context/                   what a hospital would hold about staff and patients
  labels/                    ground truth, read only by evaluation code
  manifest.json              config hash, seeds, Synthea version, row counts
```

| System | logs/ | context/ | labels/ |
|---|---|---|---|
| 1. Rules | yes | staff, patients | never |
| 2. Explanation templates | yes | encounters, care_team, orders, staff, departments | never |
| 3. LLM on raw log lines | yes | none | never |
| 4. LLM with identity graph | yes | all | never |
| Evaluation | yes | all | yes |

Rules that keep labels from leaking.

- `event_id` is assigned after background and injected events are merged and sorted by time, so an ID says nothing about how an event was made.
- No column in `logs/` or `context/` is derived from a label. A test checks column names and checks that no scenario ID appears outside `labels/`.
- Scenario actors and targets are sampled from the general population. There are no special ID ranges.
- Injected events draw duration, device and action from the same distributions as background events, except where the scenario row in section 5 says otherwise.

## 3. Data model

### 3.1 logs/access_events.csv

| Field | Type | Notes |
|---|---|---|
| event_id | int | Ours. Assigned after the final sort |
| timestamp | ISO 8601, 1 s resolution | Hospital local time |
| user_id | string | Joins to staff.csv |
| patient_id | string | Joins to patients.csv |
| device_id | string | Joins to devices.csv |
| action_code | string | Joins to actions.csv |
| duration_s | int | Seconds spent in the action |

The six fields after `event_id` mirror the Herrera Montano field list (timestamp, user, patient, device, routine, duration), so real data could slot in later.

### 3.2 context/

| Table | Fields | Built from |
|---|---|---|
| patients.csv | patient_id, first_name, last_name, birth_date, gender, address, city, zip, lat, lon, emergency_contact_id, vip | Synthea patients.csv plus the household overlay (3.4). Synthea's synthetic SSN, licence and passport columns are dropped |
| staff.csv | user_id, patient_id, role, department_id, usual_shift | Ours. Every staff member is also a person in patients.csv, sampled from Synthea adults of working age |
| departments.csv | department_id, name, kind | Ours. Kinds are inpatient unit, emergency, clinic, ancillary and back office |
| devices.csv | device_id, department_id, kind | Ours. Kinds are workstation, shared workstation, mobile and remote |
| schedule.csv | user_id, date, shift_start, shift_end, department_id | Ours. The documented assignment for each shift |
| encounters.csv | encounter_id, patient_id, class, start, stop, department_id, reason_code, reason_description, synthea_encounter_id | Synthea encounters, re-dated (3.5) |
| care_team.csv | encounter_id, user_id, team_role, start, stop | Ours. Documented assignments only |
| orders.csv | order_id, encounter_id, patient_id, kind, ordered_by, performing_department_id, timestamp, code, description | Synthea medications, procedures and imaging studies attached to the encounter |
| actions.csv | action_code, category, description | Ours. A long-tailed code vocabulary |

Staff who are also patients come for free, since every staff member has a patient record. That is what makes the coworker scenario and self-access possible.

### 3.3 labels/

| Table | Fields |
|---|---|
| event_labels.csv | event_id, reason, documented, scenario_id, part |
| scenarios.csv | scenario_id, type, class, actor_user_id, target_patient_id, start, end, covered, evidence_in_context |
| relationships.csv | patient_id_a, patient_id_b, kind, same_household |
| public_events.csv | public_event_id, patient_id, timestamp |

Every event gets a row in `event_labels.csv`. `reason` is the mechanism that produced it (care team, order, consult, coverage, back office, self, or a misuse type). `documented` says whether that reason left a trace in `context/`. `part` is `target` or `cover` for scenario events. `class` in scenarios.csv is `misuse`, `hard_negative` or `policy`.

True family ties live only in labels. A hospital does not know who is related to whom. It knows surnames, addresses and emergency contacts, so that is what `context/` holds.

### 3.4 Household overlay

Synthea gives every patient an unrelated address and no family links. In a probe run of 369 patients there were 369 distinct addresses. Relatives therefore have to be added on top. The generator groups a share of patients into households, gives members the same address and coordinates, and gives family members the same surname with a set probability. Some relatives live apart, and some keep a different surname. An emergency contact is recorded for a set share of true ties. All of these shares are assumptions and are marked as such in the config.

### 3.5 Encounters in the window

Synthea alone is far too sparse to drive hospital access volumes. The probe run (300 living patients, Springfield MA) had 132 encounters in a 30-day window, spread across many organizations. The busiest one had 32, and one encounter in the whole window was inpatient. Scaled to 3,500 patients that is roughly 1,500 encounters in total.

US volumes need much more. At 375-580 accesses per staff member per week with 92% repeats, each staff member opens about 30-46 distinct patients a week. Vanderbilt's numbers (710K unique non-self accesses across 152K patients in one week) put about 4.7 users on each patient. For 300 staff that means roughly 1,900-2,900 of the 3,500 patients are touched every week.

Proposed approach. Treat the hospital as the only provider for the population. Take each patient's Synthea encounters from the last 5 years as a pool and re-date a sample of them into the 30-day window. The clinical content stays Synthea's (reason, medications, procedures, imaging). The timing, the department and the staffing are ours. The class mix is reweighted toward inpatient and emergency, since the pool is about 1% inpatient. The sampling rate and the class weights are config parameters marked as assumptions.

## 4. How legitimate accesses arise

Each mechanism below produces accesses and either leaves a trace in `context/` or does not. The share that leaves a trace is what moves the explanation-rate targets.

| Mechanism | Trace in context/ | Scenario |
|---|---|---|
| Documented care team works the encounter | care_team row | background |
| Clinician places an order, ancillary department fulfils it | orders row | background |
| Same-department staff work the patient with no assignment recorded | department and schedule only | H3 |
| Specialist consults on a verbal request | none, though specialty may match the encounter reason | H1 |
| Staff cover another unit for a shift | schedule row for some, nothing for others | H2 |
| Coding, billing and records staff work the chart after discharge | role only | H5 |
| Care-team member happens to be a coworker, neighbor or namesake | care_team row, plus a relationship | H4 |
| Staff member opens their own record | name and birth date match | P1 |

Hard negatives H1, H2, H3 and H5 are not injected. They are the undocumented share of normal work, labeled by mechanism. The hard-negative test set is a sample of them.

Within a shift, a user works through patients in visits. Each visit is a short run of consecutive events on one patient, which is what produces the immediate-repeat share. Durations are lognormal.

## 5. Scenarios

### 5.1 What each one looks like and what evidence decides it

| ID | Scenario | In the logs | Deciding evidence |
|---|---|---|---|
| M1 | Coworker snooping. A user opens the record of a colleague from their own department | 1-3 view actions (summary, results, notes), short, on the user's usual device and shift, usually one visit and no return | Target is staff in the actor's department. No care-team, order or department path to the target's encounter |
| M2a | Relative in the same household | Same as M1. Sometimes repeated over several days | Shared surname and shared address. No care path |
| M2b | Relative with a different surname and address | Same as M1 | Emergency contact, when one is recorded. Otherwise nothing in context |
| M3 | Neighbor | Same as M1 | Actor lives within 0.25 miles or on the same street. No care path |
| M4 | VIP | Same as M1. Sometimes several users over a stay | `vip` flag on the patient. No care path |
| M5 | Curiosity after a news event | Several users from several departments open one chart within hours. Each is a single short view | The burst itself, plus no care path for any of them. The news event is not in context |
| M6a | Shared credentials, overlap | One account active on two devices in different departments at overlapping times | Timestamps and devices. Device department differs from the owner's schedule |
| M6b | Shared credentials, off shift | Account active when its owner is not scheduled, with an action mix unusual for the role | Schedule and role |
| H1 | Consult | 2-6 events over 1-2 days, longer durations, includes a note-writing action | No care-team row or order. Specialty matches the encounter reason |
| H2a | Coverage, documented | Many patients of an unfamiliar unit across one shift, on that unit's devices | Schedule row for the covered unit |
| H2b | Coverage, undocumented | Same as H2a | Nothing direct. Devices and timing are consistent with working the unit |
| H3 | Undocumented care team | Repeated accesses through the stay, charting actions, interleaved with the documented team | Same department as the encounter, scheduled on that unit, inside the stay |
| H4 | Legitimate care with a relationship present | Indistinguishable from normal care | care_team row. Also a coworker, proximity or surname match |
| H5 | Back-office work | View and coding actions in the days after discharge | Role and discharge date |
| P1 | Self-access | User opens their own record | First name, last name and birth date match (the Hedda et al. definition) |
| A1 | Covering accesses | Any of M1-M4, plus extra accesses "made to disguise a target access" (Xu et al. 2023, evasion setting) | Same as the base scenario |

A set share of M1-M4 targets have an encounter in the actor's own department. Those look like care to any department-level explanation, which makes them the hard end of the misuse set.

P1 is labeled as `policy`, not misuse. Whether staff may view their own record is a hospital policy choice, so it is reported on its own line and left out of misuse precision and recall.

### 5.2 Which systems should and should not catch it

These are predictions to be tested. Nothing in the generator enforces them, and results that contradict a cell get reported.

- **flag** means the system should put the access in front of a reviewer
- **miss** means it should not be able to
- **clear** means it should correctly leave a legitimate access alone
- **false alert** means it should wrongly raise a legitimate access
- **unexplained** applies to System 2, which does not rank. The access lands in the unexplained pile along with every other unexplained access
- **no basis** means the system has nothing to decide with, so any answer is a guess

| ID | 1. Rules | 2. Templates | 3. LLM raw | 4. LLM + graph |
|---|---|---|---|---|
| M1 | flag (department co-worker) | unexplained | miss | flag |
| M2a | flag (last name, street) | unexplained | miss | flag |
| M2b | miss | unexplained | miss | flag only when the emergency contact is recorded |
| M3 | flag (proximity, street) | unexplained | miss | flag |
| M4 | miss with the published rules | unexplained | miss | flag |
| M5 | miss | unexplained | flag only if shown the patient's other accesses | flag |
| M6a | miss | unexplained | flag | flag |
| M6b | miss | unexplained | miss | flag |
| M1-M4, target in actor's department | as above | miss (explained by department) | miss | open question |
| H1 | clear | false alert, unless a diagnosis-department template applies | no basis | clear, with stated uncertainty |
| H2a | clear | false alert | no basis | clear |
| H2b | clear | false alert | no basis | should say it cannot confirm |
| H3 | clear | false alert, unless a department template applies | no basis | clear |
| H4 | false alert | clear | clear by default (nothing to flag) | clear |
| H5 | clear | false alert, unless a role template applies | no basis | clear |
| A1 | as base scenario | as base scenario | open question | open question |

Three things keep this table from being a foregone conclusion.

- The graph is incomplete on purpose. In M2b without an emergency contact and in H2b, the right output from System 4 is "the evidence does not settle this", and a confident answer either way counts against it.
- In H4 the relationship evidence points the wrong way. It tests whether System 4 over-weights a relationship when a care-team row is present.
- The rules are the ones Hedda et al. evaluated (organization co-worker, department co-worker, last name, proximity within 0.25 miles, same street). Their paper found the organization-wide rule no better than chance, so it is expected to add alerts without adding catches.

## 6. Calibration targets

All parameters live in `config/calibration.yaml`. Each entry carries its value, its source and a `basis` of `published`, `derived` or `assumption`.

```yaml
repeat_access_share:
  value: 0.92
  basis: published
  source: "Hedda et al. 2017 (6.9M of 7.5M accesses in one week)"
  use: level
  note: "A repeat is the same user opening the same patient earlier in the same week"
```

| Measure | Target | Source | Used as |
|---|---|---|---|
| Accesses per staff member per week | 375-580 | Fabbri & LeFevre 2013; Hedda et al. 2017; Laszka et al. 2017 | level |
| Repeat share (same user, same patient, earlier in the week) | about 92% | Hedda et al. 2017 | level |
| Self-accesses | about 0.3% | Hedda et al. 2017 | level |
| First accesses explained by direct evidence | about 23%, 41% with diagnosis-department links | Fabbri & LeFevre 2013 | level |
| All accesses explained once repeats are filtered | over 94% | Fabbri & LeFevre 2011, 2013 | reported |
| Accesses matching each relationship rule | 0.03-3.8% by rule | Hedda et al. 2017 | reported |
| Alerts per access | about 10% | Laszka et al. 2017 | not measurable until step 4 |
| Access duration | median 45 s, 95th percentile 775 s | Herrera Montano et al. | level |
| Events that immediately repeat the same patient | 55.4% | Herrera Montano et al. | level |
| Distinct patients per user per day | median 5, 99th percentile 80 | Herrera Montano et al. | shape |
| Events per user | median 220, 95th percentile 2,160 over 18 months | Herrera Montano et al. | shape |
| Distinct patients per user | median 149, 99th percentile 3,424 | Herrera Montano et al. | shape |
| Action codes | 21 of 13,723 codes cover about 80% of events | Herrera Montano et al. | shape |

"Level" means the generated value is compared to the number. "Shape" means only ratios are compared (for example the 95th percentile over the median), because the UK log is far sparser than the US ones and a 30-day window at 300 staff cannot reproduce its absolute counts. "Reported" means the value is measured and shown but nothing is set to hit it.

The report lists every target next to the generated value and the gap. If a gap is large, the fix is a visible config change, or the gap stays in the report.

Scenario counts (about 10 instances per misuse type by default) are assumptions. No public source gives base rates for confirmed misuse, so detection is reported per scenario and any precision figure is tied to the injected rate.

## 7. Out of scope for this milestone

The identity graph, the four systems, the Privacy Officer summary format and any LLM call. Covering accesses (A1) get their label fields now and their generator later (see 8.5).

## 8. Decisions needed before generator code

1. **Sizing.** 300 staff at US volumes gives roughly 480,000-740,000 events in 30 days, and most of the 3,500 patients are touched every week. The alternative is to keep 300 staff and accept volumes below the US targets, with the gap reported.
2. **Re-dating Synthea encounters** into the window (3.5). The alternative is a much larger Synthea population, which the probe suggests would need tens of thousands of patients at the least and would still be about 1% inpatient.
3. **The news event stays out of context, the VIP flag stays in.** Hospitals keep VIP flags. They do not reliably know what was on the news, so M5 has to be caught from the burst.
4. **Self-access is labeled as policy,** not as misuse.
5. **Covering accesses are deferred** until the baselines exist. The public abstract of Xu et al. defines the attack but the construction needs the full paper, and a cover is only meaningful against a detector. Each covered scenario will be generated as a pair, with and without cover, so robustness is a paired difference.
6. **Rules.** Hedda et al. has no same-address rule. The nearest are same street and proximity within 0.25 miles. The plan is to implement their five and add same address as a sixth, noted as ours.
7. **ASTM E2147.** The action categories (view, add, change, delete, print, copy) need a check against the standard's text, which is paywalled.

Not needed yet, and reserved for Carlos when step 5 starts. LLM provider, model and budget, and the Privacy Officer summary template.
