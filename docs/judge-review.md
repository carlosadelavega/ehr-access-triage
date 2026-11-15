# Hand-read of the faithfulness judge

Written by hand on 14 Nov 2026. This is the record the spec asks for before any figure from the second faithfulness check is quoted. It covers pass 1 of the two LLM systems on seed 42.

## What the judge reported

The judge is DeepSeek V4.1 Flash 1017 on Sail Research's fp4 endpoint with reasoning effort low. It rated 17,696 claims from 3,341 summaries of the main run. These are the figures in `llm-results.md`.

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
- The unsupported share is an upper bound. On this sample about half of it is real, which would put the rate of contradicted claims near 1.3% on log lines and 0.6% with the graph. Twelve verdicts are too few to pin that down.
- A claim rated partly is not a wrong claim. Most are a correct observation with an inference attached, such as "a pattern consistent with routine work".

## What came before

Two earlier versions of the check were tried on 79 summaries each and dropped. The first had two verdicts and showed the judge only the cited facts. It rejected 205 of 378 claims, mostly for an interpretive tail, one uncited fact, or a claim about absence that it had nothing to check against. The second had the three verdicts used now but left the section titles out of the fact list, so a claim quoting "341 more lines not shown" looked invented. Of its 13 unsupported verdicts at least 8 were wrong. The outputs of both are kept under `data/trials/`.

[^1]: The 24 were read after the first judge run, when the shares were 64.4%, 33.0% and 2.6% on log lines and 69.6%, 29.3% and 1.1% with the graph. Since then 391 cases have been judged again, to cover claims that cite a range of facts such as L2-L31. None of the newer verdicts has been read by hand.
