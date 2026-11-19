# Hand-read of the faithfulness judge

Written by hand on 14 Nov 2026. This is the record the spec asks for before any figure from the second faithfulness check is quoted. It covers pass 1 of the two LLM systems on seed 42, which is now kept as an exploratory run (spec section 11), with its answers in `results/exploratory-pass1/`. The figures below are pass 1's and not those of `llm-results.md` as it stands. The reading was corrected on 18 Nov 2026 after an outside review, as the last section says.

## What the judge reported

The judge is DeepSeek V4.1 Flash 1017 on Sail Research's fp4 endpoint with reasoning effort low. It rated 17,696 claims from 3,341 summaries of the main run of pass 1.

| System | Claims judged | Supported | Partly | Unsupported |
|---|---|---|---|---|
| LLM on log lines | 6,673 | 65.8% | 31.6% | 2.7% |
| LLM with identity graph | 11,023 | 69.5% | 29.3% | 1.2% |

Four judge calls on log lines kept running out of tokens, three of them on all four tries. That leaves 18 claims with no verdict, which are not in the shares. Every claim of the system with the graph has a verdict.

## What a hand-read found

24 verdicts were drawn at random from cases outside the 20 used to try the judge out, and each claim was checked against the facts the author was shown.[^1]

| Verdict | Read | Agreed |
|---|---|---|
| Supported | 6 | 5 |
| Partly | 6 | 6 |
| Unsupported | 12 | 6 |

The supported and partly verdicts held up. The one disagreement was a claim with an interpretive tail that the judge passed as supported, so the supported share is probably a little high and the partly share a little low.

The unsupported verdicts held up half the time. The six that were right caught real slips in the summaries, all small.

- "One update" where the log shows two
- An encounter said to overlap every access, when the last access began four seconds after it ended
- Accesses said to fall within the shift, when the last was five minutes after it
- "The prior 24 hours" for lines that were 27 to 36 hours earlier
- Two groups of log lines put in the wrong order
- "The same workstations" where one of three devices was a different one

The six that were wrong were of three kinds. Three were claims that the facts lack something, which were true and which the judge rejected anyway. One was a plain statement of timing that the cited lines confirm. Two needed one more fact than they cited, or spoke for lines that were not shown, which the rubric calls partly.

## How to read the numbers

- The supported and partly shares can be quoted as the judge's reading, with this review named beside them.
- About half of the unsupported verdicts read held up. That says the judge is too quick to call a claim unsupported. It does not make the unsupported share an upper bound on the rate of wrong claims, because the hand-read also found a wrong claim among the supported verdicts, and errors among supported and partly verdicts were not measured. No corrected error rate follows from twelve verdicts.
- A claim rated partly is not a wrong claim. Most are a correct observation with an inference attached, such as "a pattern consistent with routine work".

## What came before

Two earlier versions of the check were tried on 79 summaries each and dropped. The first had two verdicts and showed the judge only the cited facts. It rejected 205 of 378 claims, mostly for an interpretive tail, one uncited fact, or a claim about absence that it had nothing to check against. The second had the three verdicts used now but left the section titles out of the fact list, so a claim quoting "341 more lines not shown" looked invented. Of its 13 unsupported verdicts at least 8 were wrong. The outputs of both are kept under `data/trials/`.

[^1]: The 24 were read after the first judge run, when the shares were 64.4%, 33.0% and 2.6% on log lines and 69.6%, 29.3% and 1.1% with the graph. Since then 391 cases have been judged again, to cover claims that cite a range of facts such as L2-L31. None of the newer verdicts has been read by hand.

## Corrected after the outside review (18 Nov 2026)

An outside review of the repository pointed out two overclaims in the first version of this page. It called the unsupported share an upper bound and derived corrected rates of 1.3% and 0.6% from it. Neither follows, for the reason now given above. It also matters what the judge rates. It sees only the items of two parts of each summary, the evidence of a job-related purpose and the risk indicators. The narrative, the missing information and the rationale are not checked, so these shares rate those items and not whole summaries. Before any faithfulness figure is quoted as a rate, a larger hand-read is needed, blinded to the verdict and spread across all three verdicts and both systems (spec 11.6).

## The corrected design: a blinded read (19 Nov 2026)

The judge rated the claims of the corrected pass 1 (spec section 11) as in `llm-results.md`. On 19 Nov 2026, 36 of its verdicts were read again, blind. Six claims were drawn at random for each verdict and each system, with seed 20261119, from 17,443 claims with a verdict, and shuffled. Each claim was checked against the facts it cites, and where it rested on an uncited fact, against the full list its author saw. The verdicts were sealed in a separate file until every reading was written down. The reader was Claude, the assistant that built this repository, so this is a second model's check and not a person's. The 36 claims, both readings and a note on each disagreement are in [judge-read-corrected-pass.csv](judge-read-corrected-pass.csv), and anyone can read them again.

The same three verdicts were used for the reading as for the judge.

| Judge said | Read as supported | Read as partly | Read as unsupported |
|---|---|---|---|
| Supported, 12 claims | 10 | 2 | 0 |
| Partly, 12 claims | 3 | 8 | 1 |
| Unsupported, 12 claims | 7 | 4 | 1 |

What this shows.

- **Supported held.** No claim the judge passed as supported was wrong. Two leaned on a fact they did not cite.
- **Unsupported mostly did not hold.** Of 12, one claim was wrong on reading, J16, which put a user on a MED2 workstation that is in LAB. Seven were plainly true, such as "all three actions are A0335, a read" and "15:02 is just past the 15:00 shift end". The judge rejects true claims, the same failure the hand-read of pass 1 found.
- **A wrong claim can hide under partly.** J03 said a user opened at least six other patients where the prompt shows five, and the judge called it partly.
- **The counts were right more often than the citations.** Several claims counted users or patients correctly from the full list while citing only some of the lines. Under the rubric that is partly, not wrong.

So the judge's unsupported share overstates the wrong claims among those it rejects, while wrong claims also sit among those it calls partly. Two of the 36 claims read here were wrong. The sample was drawn six to a cell, so it cannot give a rate for the whole pass to any useful precision, and none is given. What can be said is that the summaries cite real facts, that the judge's "supported" can be trusted on this sample, and that its "unsupported" cannot.
