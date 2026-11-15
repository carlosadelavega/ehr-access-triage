# Hand-read of the faithfulness judge

Written by hand on 14 Nov 2026. This is the record the spec asks for before any figure from the second faithfulness check is quoted. It covers pass 1 of the two LLM systems on seed 42.

## What the judge reported

The judge is DeepSeek V4.1 Flash 1017 on Sail Research's fp4 endpoint with reasoning effort low. It rated 16,479 claims from 3,263 summaries of the main run. `llm-results.md` has the shares.

| System | Claims judged | Supported | Partly | Unsupported |
|---|---|---|---|---|
| LLM on log lines | 5,943 | 64.4% | 33.0% | 2.6% |
| LLM with identity graph | 10,536 | 69.6% | 29.3% | 1.1% |

Across the main run and the paired run, 44 of 3,578 judge calls ran out of tokens before giving a verdict. Their claims are not in the shares.

## What a hand-read found

24 verdicts were drawn at random from cases outside the 20 used to try the judge out, and each claim was checked against the facts the author was shown.

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
- The unsupported share is an upper bound. On this sample about half of it is real, which would put the rate of contradicted claims near 1.3% on log lines and 0.6% with the graph. Twelve verdicts are too few to pin that down.
- A claim rated partly is not a wrong claim. Most are a correct observation with an inference attached, such as "a pattern consistent with routine work".

## What came before

Two earlier versions of the check were tried on 79 summaries each and dropped. The first had two verdicts and showed the judge only the cited facts. It rejected 205 of 378 claims, mostly for an interpretive tail, one uncited fact, or a claim about absence that it had nothing to check against. The second had the three verdicts used now but left the section titles out of the fact list, so a claim quoting "341 more lines not shown" looked invented. Of its 13 unsupported verdicts at least 8 were wrong. The outputs of both are kept under `data/trials/`.

## Addendum, 14 Nov 2026: later judge runs

The figures above describe the first judge run. Two things happened after this review was written.

- The 44 calls that ran out of tokens were sent again. 37 came back complete.
- A range of fact IDs such as L2-L31 began to count as a citation, so 1,034 more claims passed the first check. The judge was run again on the 391 cases this touched. Each of those cases was judged again as a whole, so a verdict read by hand above may since have been replaced by a newer one.

The shares now in `llm-results.md` are 65.8% supported, 31.6% partly and 2.6% unsupported on log lines (6,653 claims), and 69.6%, 29.3% and 1.1% with the graph (10,993 claims). 68 claims still have no verdict, because 13 calls were cut off by the token limit again.

No verdict from the later runs has been read by hand. The advice above on how to read the numbers stands, and the unsupported share is still an upper bound resting on twelve verdicts.
