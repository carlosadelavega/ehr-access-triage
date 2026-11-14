# Spec: data model and scenarios

Status: reviewed on 10 Nov 2026 and revised the same day after the Herrera Montano and Hedda papers were read in full. This covers steps 1 and 2 of the build order. Section 8 records the decisions.

## 1. Scope of this milestone

One synthetic hospital with about 400 staff (308 full-time equivalents), about 3,500 patients and 30 days of EHR access logs. The output has to serve four systems later (rules, explanation templates, an LLM on raw log lines, an LLM grounded on an identity graph), so the main job of this spec is to fix what each of them is allowed to see.

## 2. Output layout and visibility

```
data/generated/<run_id>/
  logs/access_events.csv     the audit log
  context/                   what a hospital would hold about staff and patients
  labels/                    ground truth, read only by evaluation code
  cases.csv                  the first accesses the LLM systems triage (section 10), with no label in it
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
| duration_s | int | Seconds until the user's next action, capped at five minutes |

The six fields after `event_id` mirror the Herrera Montano field list (timestamp, user, patient, device, routine, duration), so real data could slot in later. The fields are the UK log's. The unit of an event is the US one (3.6).

### 3.2 context/

| Table | Fields | Built from |
|---|---|---|
| patients.csv | patient_id, first_name, last_name, birth_date, gender, address, city, zip, lat, lon, emergency_contact_id, vip | Synthea patients.csv plus the household overlay (3.4). Synthea's synthetic SSN, licence and passport columns are dropped |
| staff.csv | user_id, patient_id, role, department_id, usual_shift, employment | Ours. Employment is full-time, part-time or occasional. Every staff member is also a person in patients.csv, sampled from Synthea adults of working age |
| departments.csv | department_id, name, kind | Ours. Kinds are inpatient unit, emergency, clinic, ancillary, back office and hospital-wide |
| devices.csv | device_id, department_id, kind | Ours. Kinds are workstation, shared workstation, mobile and remote |
| schedule.csv | user_id, date, shift_start, shift_end, department_id | Ours. The documented assignment for each shift |
| encounters.csv | encounter_id, patient_id, class, start, stop, department_id, reason_description, description, synthea_encounter_id | Synthea encounters, re-dated (3.5). Class is inpatient, emergency or clinic |
| care_team.csv | encounter_id, user_id, team_role, start, stop | Ours. Documented assignments only |
| orders.csv | order_id, encounter_id, patient_id, kind, ordered_by, performing_department_id, timestamp, description | Synthea medications, imaging studies and laboratory observations attached to the encounter, plus daily orders for inpatients. They route to pharmacy, radiology and the lab |
| actions.csv | action_code, category, description | Ours. A long-tailed code vocabulary |

Staff who are also patients come for free, since every staff member has a patient record. That is what makes the coworker scenario and self-access possible.

### 3.3 labels/

| Table | Fields |
|---|---|
| event_labels.csv | event_id, reason, documented, scenario_id, part |
| scenarios.csv | scenario_id, type, group_id, actor_user_id, borrower_user_id, target_patient_id, start, end, covered, evidence_in_context |
| relationships.csv | patient_id_a, patient_id_b, kind, same_household |
| public_events.csv | public_event_id, patient_id, timestamp |
| case_truth.csv | case_id, stratum, population (written with the case set, section 10) |

Every event gets a row in `event_labels.csv`. `reason` is the mechanism that produced it (care team, order, consult, coverage, back office, self, or a misuse type). `documented` says whether that reason left a trace in `context/`. `part` is `target` or `cover` for scenario events. scenarios.csv holds the injected misuse scenarios only, and `group_id` ties together the users who respond to the same news event. For shared credentials, `actor_user_id` is the account seen in the log and `borrower_user_id` is the person using it. Hard negatives and self-access are not scenarios. They are selected from event_labels.csv by `reason` and `documented`. A hard negative is a first access whose cause is undocumented and whose user and patient have no direct evidence from any other encounter either.

True family ties live only in labels. A hospital does not know who is related to whom. It knows surnames, addresses and emergency contacts, so that is what `context/` holds.

### 3.4 Household overlay

Synthea gives every patient an unrelated address and no family links. In a probe run of 369 patients there were 369 distinct addresses. Relatives therefore have to be added on top. The generator groups a share of patients into households, gives members the same address and coordinates, and gives family members the same surname with a set probability. Some relatives live apart, and some keep a different surname. An emergency contact is recorded for a set share of true ties. All of these shares are assumptions and are marked as such in the config.

### 3.5 Encounters in the window

Synthea alone is far too sparse to drive hospital access volumes. The probe run (300 living patients, Springfield MA) had 132 encounters in a 30-day window, spread across many organizations. The busiest one had 32, and one encounter in the whole window was inpatient. Scaled to 3,500 patients that is roughly 1,500 encounters in total.

US volumes need much more. At 375-580 accesses per staff member per week with 92% repeats, each staff member opens about 30-46 distinct patients a week. Vanderbilt's numbers (710K unique non-self accesses across 152K patients in one week) put about 4.7 users on each patient. For 300 staff that means roughly 1,900-2,900 of the 3,500 patients are touched every week.

Proposed approach. Treat the hospital as the only provider for the population. Take each patient's Synthea encounters from the last 10 years as a pool and re-date a sample of them into the 30-day window. Five years holds too few inpatient stays to fill the beds. The clinical content stays Synthea's (reason, medications, imaging, labs). The timing, the department and the staffing are ours. The class mix is reweighted toward inpatient and emergency, since the pool is about 1% inpatient. The sampling rate and the class weights are config parameters marked as assumptions.

### 3.6 The unit of an access

Two kinds of log sit behind the calibration targets, and they do not count the same thing. The US audit logs in the explanation and rules literature record 375-580 accesses per employee per week, and about 10 per user and patient in a week. The UK log of Herrera Montano et al. records about 9 events per user per week, each a routine with a duration. Raw vendor logs are finer again. One study of inpatient nurses counts about 2,000 logged actions per shift.

This generator uses the US unit, because the rules and the explanation templates are defined on it. Three things follow.

- Volume, repeats, self-access, explanation rates and rule matches are compared with the US papers as numbers.
- The UK log is used only where the unit does not matter, and only by shape. That covers how unevenly events, patients and devices are spread across users, and the long tail of action codes.
- US audit logs carry no duration. The field stays, to keep the UK field list, and holds the time until the user's next action, capped at the five-minute inactivity threshold that audit-log studies use. Its scale is set from published chart time for inpatient nurses, about a third of a shift.

### 3.7 Users differ

In the UK log, activity is far from even. Over 18 months the median user has 220 events and the 99th percentile 7,321. The median user works on 22 devices. Most users are active on a minority of days, and 72% fit no common behavioral role. Device diversity is the strongest marker of an atypical user. Four mechanisms put this into the generator. Their parameters are assumptions, since the paper gives outcomes and not causes.

- **Presence.** Staff are full-time, part-time or occasional, with 5, 3 or 1 shifts a week. The staffing table is in full-time equivalents, so headcount is higher.
- **Uneven load.** Each person has a workload weight. Patients, appointments and orders are handed out in proportion to it.
- **Hospital-wide roles.** Care management (bed management, case management, infection control) opens charts across every unit. Only the role explains it, which makes it hard negative H6.
- **Device roaming.** People habitually use anything from one workstation to most of the unit's. Pharmacists, technicians, consultants, registration staff and care managers sometimes work at a shared workstation in the patient's department.

The last point matters for the evaluation. If honest work never happened on a device outside the user's department, that one fact would give shared credentials away.

### 3.8 People who know each other

In Hedda et al., staff open the charts of namesakes, neighbors and department colleagues more often than random pairing would produce. Last name matches occur 1.5 times as often as chance, proximity 2.5 times, same street 4.0 times and department co-worker 3.2 times. Their five clinical reasons explain only 16-44% of these accesses.

The generator uses those ratios as weights. A staff member who shares a surname, a street or a neighborhood with a patient is that much more likely to be the one who does a given piece of work on that patient, and staff are that much more likely to be seen in their own department. Nobody is assigned to their own chart.

The paper does not say how much of the excess is appropriate. Here all of it is ordinary work, documented or not at the usual rates, and injected misuse comes on top. That is an assumption, and it leans toward more hard negatives. If much of the real excess is snooping, this data overstates how often a relationship is innocent.

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
| Someone who knows the patient is on the care team | care_team row, plus a relationship | H4a |
| Someone who knows the patient does other work on the chart | a relationship, and no care_team row | H4b |
| Care management reviews patients across the house | role only | H6 |
| Staff member opens their own record | name and birth date match | P1 |

Hard negatives H1, H2, H3, H5 and H6 are not injected. They are the undocumented share of normal work, labeled by mechanism. The hard-negative test set is a sample of them.

Within a shift, a user works through patients in visits. Each visit is a short run of consecutive accesses to one patient, 3.3 on average, which with about three visits per patient per week gives the roughly 10 accesses per user and patient that Hedda et al. report. A person works one chart at a time. Durations are lognormal and capped at five minutes.

## 5. Scenarios

### 5.1 What each one looks like and what evidence decides it

| ID | Scenario | In the logs | Deciding evidence |
|---|---|---|---|
| M1 | Coworker snooping. A user opens the record of a colleague from their own department | 1-3 view actions (summary, results, notes), short, on the user's usual device and shift, usually one visit and no return | Target is staff in the actor's department. No care-team, order or department path to the target's encounter |
| M2a | Relative in the same household | Same as M1. Sometimes repeated over several days | Shared surname and shared address (so same street and zero distance). No care path |
| M2b | Relative with a different surname and address | Same as M1 | Emergency contact, when one is recorded. Otherwise nothing in context |
| M3 | Neighbor | Same as M1 | Actor lives within 0.25 miles or on the same street. No care path |
| M4 | VIP | Same as M1. Sometimes several users over a stay | `vip` flag on the patient. No care path |
| M5 | Curiosity after a news event | Several users from several departments open one chart within hours. Each is a single short view | The burst itself, plus no care path for any of them. The news event is not in context |
| M6a | Shared credentials, overlap | One account active on two devices in different departments at overlapping times | The overlap itself. A device outside the owner's department is not enough, since honest work happens there too |
| M6b | Shared credentials, off shift | Account active when its owner is not scheduled, with an action mix unusual for the role | Schedule and role |
| H1 | Consult | 2-6 events over 1-2 days, longer durations, includes a note-writing action | No care-team row or order. Specialty matches the encounter reason |
| H2a | Coverage, documented | Many patients of an unfamiliar unit across one shift, on that unit's devices | Schedule row for the covered unit |
| H2b | Coverage, undocumented | Same as H2a | Nothing direct. Devices and timing are consistent with working the unit |
| H3 | Undocumented care team | Repeated accesses through the stay, charting actions, interleaved with the documented team | Same department as the encounter, scheduled on that unit, inside the stay |
| H4a | Documented care with a relationship present | Indistinguishable from normal care | care_team row or an order. Also a coworker, proximity or surname match |
| H4b | Undocumented work with a relationship present | Same as H3 or H5, by someone who shares a surname, street, neighborhood or department with the patient | Only the weak evidence of H3 or H5, against a relationship that points the other way |
| H5 | Back-office work | View and coding actions in the days after discharge | Role and discharge date |
| H6 | Hospital-wide role | A care manager opens many patients across units, briefly, sometimes on the unit's shared workstation | Role and department. No care-team row, order or department link |
| P1 | Self-access | User opens their own record | First name, last name and birth date match (the Hedda et al. definition) |
| A1 | Covering accesses | Any of M1-M4, plus extra accesses "made to disguise a target access" (Xu et al. 2023, evasion setting, see 5.3) | Same as the base scenario |

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
| M2a | flag (last name, street, proximity) | unexplained | miss | flag |
| M2b | miss | unexplained | miss | flag only when the emergency contact is recorded |
| M3 | flag (proximity, street) | unexplained | miss | flag |
| M4 | miss | unexplained | miss | flag |
| M5 | miss | unexplained | flag only if shown the patient's other accesses | flag |
| M6a | miss | unexplained | flag | flag |
| M6b | miss | unexplained | miss | flag |
| M1-M4, target in actor's department | as above | miss (explained by department) | miss | open question |
| H1 | clear | false alert, unless a diagnosis-department template applies | no basis | clear, with stated uncertainty |
| H2a | clear | false alert | no basis | clear |
| H2b | clear | false alert | no basis | should say it cannot confirm |
| H3 | clear | false alert, unless a department template applies | no basis | clear |
| H4a | false alert | clear | clear by default (nothing to flag) | clear |
| H4b | false alert | false alert, unless a department or role template applies | no basis | clear with stated uncertainty, or say it cannot confirm |
| H5 | clear | false alert, unless a role template applies | no basis | clear |
| H6 | clear | false alert, unless a role template applies | no basis | clear |
| A1 | as base scenario | as base scenario | open question | open question |

Three things keep this table from being a foregone conclusion.

- The graph is incomplete on purpose. In M2b without an emergency contact and in H2b, the right output from System 4 is "the evidence does not settle this", and a confident answer either way counts against it.
- In H4 the relationship evidence points the wrong way. H4a tests whether System 4 over-weights a relationship when a care-team row is present. H4b is the hardest case in the set: a relationship, no direct evidence, and nothing wrong.
- The rules are the ones Hedda et al. evaluated (organization co-worker, department co-worker, last name, proximity within 0.25 miles, same street). Their paper found the organization-wide rule no better than chance, so it is expected to add alerts without adding catches.

### 5.3 Covering accesses

Xu et al. give the attacker one realistic capability, which is making extra accesses from their own account. Their gradient attack needs a trained detector, so it does not apply here. Their heuristic baseline does. For a target access by user u to record v*, every other record v gets the score

```
M(v) = (1 - a) * sim(v, v*) + a * mean over v_i of sim(v, v_i)
```

where the v_i are the records u already accessed, sim is the Jaccard index of the sets of users who accessed each record, and a = 0.75. The K highest-scoring records become the covering accesses. The paper tests K from 2 to 50 within one week. Their unit is the encounter. Ours is the patient, since the log has no encounter field.

The covering accesses look like ordinary work for the actor's role. Xu et al. attack detectors that read a weekly graph, where the timing within the week does not matter. The LLM is shown the log lines nearest the access, so a cover has to be near it to be seen at all. Each week with a target access therefore gets its own 10 covering accesses, placed within an hour either side of that week's first target access, which is the one that gets triaged. The hour is an assumption. Rules and explanation templates do not model behavior, so covering accesses should leave them unchanged. The test is whether they move the LLM.

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
| First accesses explained by direct evidence | about 23% | Fabbri & LeFevre 2013 | level |
| First accesses explained with departmental responsibility | about 41% | Fabbri & LeFevre 2013 | reported |
| All accesses explained once repeats are filtered | over 94% | Fabbri & LeFevre 2011, 2013 | reported |
| First accesses matching each rule | 3.84% organization co-worker, 0.04% department co-worker, 0.14% last name, 0.16% proximity, 0.12% same street | Hedda et al. 2017, Table 3 | reported |
| Rule matches against random pairing | 1.0, 3.2, 1.5, 2.5 and 4.0 times chance, in the same order | Hedda et al. 2017, Table 2 | reported |
| Rule matches explained by direct evidence | 39%, 44%, 21%, 25% and 16%, in the same order | Hedda et al. 2017, Table 4 | reported |
| Alerts per access | about 10% | Laszka et al. 2017 | not measurable until step 4 |
| Inpatient nurse chart time | about 34% of a shift | JMIR Nursing 2026 nursing workload study | level |
| Primary care physician chart time | 5.9 hours per weekday | Arndt et al. 2017 | reported |
| Events per user | median 220, 95th percentile 2,160 over 18 months | Herrera Montano et al. | shape |
| Distinct patients per user | median 149, 99th percentile 3,424 | Herrera Montano et al. | shape |
| Devices per user | median 22, 95th percentile 93 | Herrera Montano et al. | shape |
| Position of a patient among the user's patients that day, per event | median 5, 99th percentile 80 | Herrera Montano et al. | shape |
| Action codes | 21 of 13,723 codes cover about 80% of events | Herrera Montano et al. | shape |
| Events with zero time since the user last opened the patient | 55.4%, of which about 54 points are first accesses | Herrera Montano et al. | reported |
| Duration per routine | median 45 s, 95th percentile 775 s | Herrera Montano et al. | reported |

"Level" means the generated value is compared to the number. "Shape" means only ratios are compared (the 95th or 99th percentile over the median). "Reported" means the value is measured and shown but nothing is set to hit it.

Action categories are create, read, update, delete, copy, print and query. The list comes from a public summary of ASTM E2147-18 in the CASRAI guide to audit-log requirements. The same list has break-the-glass and audit-status events, which are left out because no scenario uses them yet.

The report lists every target next to the generated value and the gap. If a gap is large, the fix is a visible config change, or the gap stays in the report.

Scenario counts (about 10 instances per misuse type by default) are assumptions. No public source gives base rates for confirmed misuse, so detection is reported per scenario and any precision figure is tied to the injected rate.

## 7. Out of scope for this milestone

The identity graph, the four systems, the Privacy Officer summary format and any LLM call. These came later and are in sections 9 and 10.

## 8. Decisions

Reviewed on 10 Nov 2026.

1. **Sizing.** About 400 staff (308 full-time equivalents) at US volumes, roughly 680,000 events in 30 days.
2. **Encounters are re-dated** from each patient's recent Synthea history (3.5).
3. **Households are an overlay,** with true ties in labels only (3.4).
4. **The news event stays out of context, the VIP flag stays in.** Hospitals keep VIP flags. They do not reliably know what was on the news, so M5 has to be caught from the burst.
5. **Self-access is labeled as policy,** not as misuse.
6. **Covering accesses use the heuristic attack from Xu et al.** (5.3). `generate --covered` writes a second run that is identical except for 10 covering accesses around each M1-M4 target, so robustness is a paired difference. The covering accesses use their own random stream, and a test checks that every other event is unchanged.
7. **Rules are the five from Hedda et al.** (organization co-worker, department co-worker, last name, proximity within 0.25 miles, same street). Nothing is added, so results can be compared with theirs directly.
8. **Action categories** come from a public summary of ASTM E2147-18 (section 6).
9. **The log uses the US unit of an access** (3.6). Duration is derived, since US logs do not record one.
10. **Users differ** in presence, load and device use, and care management is a new hard negative, H6 (3.7).
11. **People who know each other meet more often than chance** (3.8), with Hedda's ratios as weights. All of that excess is generated as ordinary work.

Decided since, and recorded in section 10. The model is GLM 5.3 Flash and the Privacy Officer summary template is approved.

## 9. Baselines (build step 4)

Results are in [baseline-results.md](baseline-results.md).

**Unit.** Both baselines judge a first access, one user opening one patient for the first time in a week, with self-accesses left out. That is the unit in Hedda et al. and in Fabbri and LeFevre. A 30-day run has about 64,000.

**What each system reads.** The table in section 2 is enforced in code. Each system loads its data through one function that returns the log and its permitted context tables and nothing else. A test runs both baselines with the labels folder removed.

**Identity graph.** A NetworkX graph of users, patients, departments and encounters, built only from the tables a system may read.

| Edge | From | To | Built from |
|---|---|---|---|
| is | user | their own patient record | staff |
| works_in | user | department | staff |
| employed_in | patient who is staff | department | staff |
| same_surname, same_street, lives_near | user | patient | staff and patients |
| emergency_contact | patient | patient | patients |
| had | patient | encounter | encounters |
| in | encounter | department | encounters |
| care_team | user | encounter | care_team |
| ordered | user | encounter | orders |
| order_to | encounter | department | orders |

For a user and a patient the graph returns every named connection with the nodes it passes through. The rules and the templates test for these connections, and the grounded LLM in step 5 will be shown the same ones.

**System 1, rules.** The five Hedda rules, each a connection in the graph. Alerts are ranked the way their paper proposes, by how far above chance each rule fires in their data (same street 4.0, department co-worker 3.2, proximity 2.5, last name 1.5, organization co-worker 1.0). Results are shown with and without the organization-wide rule, which they found no better than chance.

**System 2, explanation templates.** Two layers, as in Fabbri and LeFevre 2013. Direct evidence is a care-team row or an order placed by the user. Departmental responsibility is mined from the log with their method and thresholds (s = 1, t = 2, diagnoses seen at least 30 times). A first access with no explanation is an alert. These alerts have no ranking.

There is no "patient was seen in the user's department" template, because their method has none. Section 5.2 predicted that misuse aimed at a patient in the actor's own department would be explained away by such a template. That cell is now tested against departmental responsibility.

**Rules, then explanations.** Hedda et al. propose using explanations to cut down what the rules flag. This is reported as a fifth variant.

**Measures.** Misuse caught per scenario, false alerts per kind of legitimate access, and precision and recall when a reviewer takes the top alerts each day up to a budget. The budgets (10, 25 and 50 a day) are an assumption.

**A limit to keep in mind.** M1, M2a and M3 are defined by the relationships the rules test for, so the rules catch them by construction. What the rules results show is everything else: how many legitimate accesses they flag, and which misuse they cannot see.

## 10. LLM triage (build step 5)

Status on 12 Nov 2026: built and tested against a made-up model. No real model has been run yet, so there are no LLM results.

**Systems 3 and 4.** Both triage the same first accesses, chosen by the harness. System 3 is shown log lines only. These are the access under review, up to 30 other lines by the same user, taken nearest the access and within 12 hours either side, and up to 30 lines by other users on the same patient that week. System 4 is shown the same lines plus facts from the identity graph, which are who the user is, their shifts around that date, the patient's encounters nearest the access, the VIP flag, every named connection between the two, and what the devices and action codes are. Prompts average about 2,200 and 2,500 tokens.

**Citable facts.** Every fact in a prompt carries an ID, L1, L2 and so on for log lines and G1, G2 for graph facts. The model must cite the IDs each claim rests on. That is what makes faithfulness measurable.

**Summary template (approved by Carlos on 12 Nov 2026).** The model drafts a triage note, not a finding. A finding needs interviews and a determination that only the officer can make.

| Part | Field | Filled by |
|---|---|---|
| Case | case ID, user, patient, dates of first and last access, number of accesses, devices, which system raised it | the pipeline, from the log |
| Draft | what happened, in one or two sentences | the model |
| Draft | evidence of a job-related purpose, each item with its fact IDs | the model |
| Draft | risk indicators, each item with its fact IDs | the model |
| Draft | missing information the officer would need | the model |
| Draft | disposition: clear, verify or escalate | the model |
| Draft | priority: low, medium or high | the model |
| Draft | rationale, at most 60 words | the model |
| Officer | interview notes, determination, breach risk assessment, violation category and sanction, corrective action, notifications | the officer, never drafted |

The three dispositions are "clear" (the facts show a job-related purpose, or nothing calls for review), "verify" (the facts neither show a purpose nor a concern that stands on its own, so ask the supervisor) and "escalate" (the facts show a concern). "Verify" is the right answer where the evidence is incomplete by design, as in M2b without an emergency contact, H2b and H4b.

**Where the template comes from.** Each part follows a public source. All were read or reread on 13 Nov 2026.

| Part of the template | Source | What the template takes from it |
|---|---|---|
| Case header, what happened, the evidence items | Accountable HQ, [Privacy Officer Incident Reports: Templates, Steps, and Compliance Requirements](https://www.accountablehq.com/post/privacy-officer-incident-reports-templates-steps-and-compliance-requirements) and [How to Triage and Document an Alleged HIPAA Privacy Violation](https://www.accountablehq.com/post/how-to-triage-and-document-an-alleged-hipaa-privacy-violation) | An incident record has an identifier, a narrative of the event, the workforce members, systems and locations involved, and the evidence and logs it rests on, with EHR access reports named as evidence |
| Disposition and priority | The first of those guides | The record classifies the incident and gives it a priority level before the investigation goes further |
| Missing information | Accountable HQ, [Employee Write-Up for HIPAA Violation: Step-by-Step Guide with Templates](https://www.accountablehq.com/post/employee-write-up-for-hipaa-violation-step-by-step-guide-with-templates) | Its initial incident report ends with next steps, which are the investigation plan, the risk assessment and the interviews |
| Breach risk assessment and notifications, left to the officer | HIPAA Breach Notification Rule, [45 CFR 164.400-414](https://www.law.cornell.edu/cfr/text/45/164.400), with the [ChartRequest summary](https://www.chartrequest.com/articles/hipaa-breach-notification-rule) of the rule | An impermissible access is presumed to be a breach unless a risk assessment on four factors shows a low probability that the information was compromised (164.402). Notification of the patient and of the Secretary follows from that |
| Violation category and sanction, left to the officer | Columbia University, [Privacy and Information Security Sanction Policy](https://universitypolicies.columbia.edu/node/510) | The four categories of violation, used as written |

The officer's part is left blank on purpose. The risk assessment and the sanction both follow a determination that an access was impermissible, which a triage note cannot make.

**Violation categories.** When the officer finds a violation, it is placed in one of the four categories of the Columbia policy, taken as they are and not adapted.

| Category | The policy's definition, in short | Sanction in the policy | Scenarios here that would fall under it if substantiated |
|---|---|---|---|
| 1 | Unintentional, from carelessness, lack of training or human error | Remedial education, or a verbal or written warning | none injected |
| 2 | Poor job performance, or failure to understand or follow policy. Sharing user IDs or passwords is one of its examples | Written warning with policy review and remedial education | M6a and M6b, for the owner of the shared login |
| 3 | Intentional, from curiosity, with no intent to harm. Its examples include access without a business need, access to VIP records and snooping | Final written warning, up to suspension or termination | M1 to M5 |
| 4 | Intentional, with malicious or harmful intent, such as fraud, personal gain or retaliation | Termination, and referral to the Risk Committee | none injected, since the data says nothing about intent to harm |

The last column is this project's reading, not Columbia's. Self-access (P1) is not among the policy's examples and is left unmapped. The policy weighs intent, whether the person acted in good faith, how many people were affected and whether it is a repeat. None of that can be read off an access log, so the model never names a category. A second occurrence moves a violation up a category under the policy, which is one more reason the call belongs to the officer, who can see the personnel record.

**The case set.** Sending all 63,872 first accesses to a model costs more than the question needs. `evaluate cases` takes every misuse first access, 123 of them, and a fixed number of each legitimate kind, 1,673 cases in all. The kinds are the hard negatives of section 5, arranged so that each first access falls in exactly one. A legitimate access is H4a or H4b when a relationship rule matches it, otherwise "direct" when direct evidence explains it, otherwise the mechanism that produced it.

| Kind | First accesses on seed 42 | Sampled |
|---|---|---|
| Explained by direct evidence | 14,097 | 200 |
| H4a documented, relationship present | 307 | 100 |
| H4b undocumented, relationship present | 870 | 300 |
| H1 consult | 380 | 100 |
| H2 coverage | 317 | 100 |
| H3 undocumented care team | 15,576 | 200 |
| H5 back office | 14,166 | 200 |
| H6 hospital-wide role | 1,900 | 150 |
| Order handled by a department | 16,136 | 200 |

H4b gets the most because a relationship with no documented purpose is where a false alert is most likely. Figures for the whole population are weighted back by kind, and the sample sizes are assumptions in the config. `cases.csv` sits beside the logs and holds only the user, the patient, the week and the day, in shuffled order. What each case was goes in `labels/case_truth.csv`. The paired run gets the same 58 M1-M4 cases under the same IDs, plus 100 of its 580 covering accesses.

**Running it.** `triage run` sends each case to both systems and appends one line per answer to `results/<run>/<system>.pass<n>.jsonl`. A rerun skips what is already answered, so an interrupted run carries on where it stopped and failed calls are sent again. A call that times out, is rate-limited or hits a server error is retried up to five times. A refused request, such as a bad key, stops the run. So does the cost cap, 5 USD per command unless the config says otherwise. Cost is the amount OpenRouter reports for each call. Each answer is stored with the provider that served it.

**Model.** GLM 5.3 Flash (`z-ai/glm-5.3-flash`) through OpenRouter, pinned to Relace's `relace/fp4` endpoint with fallbacks off, so every answer comes from the same serving of the model. The endpoint address, key and model names come from environment variables or a gitignored `.env` file, so no secret is written to the repo. The request settings that are specific to OpenRouter and to these models are in `triage_provider` in the config. Reasoning is enabled with no effort level named, which leaves the level to OpenRouter's default. The output limit is 16,000 tokens. A first trial of 80 calls on 13 Nov 2026 used 4,000 and had 75 answers cut off before the summary began, because reasoning alone ran to about 3,800 tokens at the median. The five summaries that did complete took 500 to 850 tokens after the reasoning. An answer cut off by the limit is sent again on the next run. Temperature is 0 for repeatability. Z.ai recommends 1 for this model, so if a trial shows poor or malformed output, temperature is the first setting to revisit, as a config change with its own commit.

`fp4` is one of OpenRouter's quantization labels and stands for 4-bit weights. Results therefore describe GLM 5.3 Flash as that endpoint serves it, and may not carry over to the model at full precision.

**Faithfulness.** Level 1 is checked in code. It is the share of claims that cite at least one fact and cite only facts that were shown. Level 2 is `triage judge`. Every claim that passed level 1 goes to a judge model with the facts it cites and, after them, the full list of facts the author saw. The judge gives one of three verdicts.

| Verdict | Meaning |
|---|---|
| supported | The cited facts confirm everything the claim says. Reading a value off a fact, counting and simple arithmetic count as confirming |
| partly | No fact contradicts the claim, but the cited facts do not confirm all of it. It adds an interpretation, or part of it rests on a fact it did not cite |
| unsupported | A fact contradicts the claim, or the cited facts do not bear on it |

A claim that something is missing, such as "no fact shows a care team role", cannot be checked against the facts it cites. The judge checks it against the full list and flags it as an absence claim, so these can be counted apart.

The first version of this check had two verdicts and showed the judge only the cited facts. A trial on 14 Nov 2026 marked 205 of 378 claims as not supported. Reading a sample showed why. A claim with an interpretive tail, such as "consistent with routine work", failed. So did a claim that leaned on one fact it had not cited, and every absence claim had nothing to be checked against. Three verdicts and the full list separate these cases from a claim with no basis.

The judge is a different model from the one that wrote the summary, DeepSeek V4.1 Flash 1017 (`deepseek/deepseek-v4.1-flash-1017`) on Sail Research's `sail-research/fp4/us` endpoint with reasoning effort low, so the two do not share the same blind spots. In the same trial the judge, then on another endpoint with default reasoning, spent about 6,500 reasoning tokens per call on a verdict of about 50, and at least one of its verdicts was plainly wrong. It is still a model, and a sample of its verdicts has to be read by hand before any level 2 figure is quoted.

**Scoring.** `evaluate llm` writes `docs/llm-results.md`. Each system is scored at two thresholds, escalate alone and verify or escalate. An answer that does not fit the template counts as verify, since someone still has to look at it. Population figures carry a 95% margin that reflects the sampling only. The daily budget table ranks alerts by disposition and then by the model's priority, and scores every system on an average day because the sample is too thin to rank day by day. The covering-access table compares the same target cases across the paired runs.
