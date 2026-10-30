# Homework 7: monitoring `unsupported_policy_claim`

**Setup.** Both periods are the same 50 scenarios (`scenarios/monitoring_scenarios.jsonl`) on `claude-opus-4-6`. *Before* is the Homework 3 run (2026-09-18, 20:57–22:03 UTC, 56 traces). *After* is one new run of the current agent (2026-10-30, 20:46–20:59 UTC, 56 traces). The agent's prompt, tools and policies did not change between them, so this compares two runs of an unchanged agent.

The configured judge is the course's frozen `unsupported_policy_claim-v3` (Claude Opus 4.6; failure sensitivity 0.83, pass specificity 0.95). My own `scope_handling_overreach-v1` (Claude Haiku 4.5; 0.45 and 0.82) ran on the same samples as a comparison. Each period judged a seeded 20% random sample (10 conversations, the same 10 scenarios in both periods) plus every conversation with a policy lookup. The threshold of 0.15 was set before either period was judged.

| Judge | Period | Random flagged | Raw | Corrected | 95% interval |
| --- | --- | ---: | ---: | ---: | --- |
| unsupported_policy_claim-v3 | before | 2 / 10 | 0.20 | 0.19 | 0.00–0.58 |
| unsupported_policy_claim-v3 | after | 4 / 10 | 0.40 | 0.44 | 0.06–0.93 |
| scope_handling_overreach-v1 | before | 2 / 10 | 0.20 | 0.08 | 0.00–1.00 |
| scope_handling_overreach-v1 | after | 3 / 10 | 0.30 | 0.44 | 0.00–1.00 |

Details per conversation are in `monitoring/output/`, the period records in `history.jsonl`, and the chart in `prevalence.svg`.

## 1. Did the corrected failure estimate move between the two periods?

The point estimate moved from 0.19 to 0.44. Underneath, it is two conversations out of the same ten changing from pass to fail (support-0085 and support-0161). Reading them, the two changes have different causes:

- **support-0161** is a real disagreement within the judge, not the agent. Both replies give the same 21-day Meridian Cycles window, the same 3 days left, and cite the same policies. The frozen judge passed it before and failed it after.
- **support-0085** is a real change in the agent. The after reply adds "a cancelled order that never shipped would normally warrant a refund", a policy claim no document supports. The before reply made no such claim.

So one of the two flips is agent variation and one is judge variation, on an agent that did not change.

## 2. Do the intervals support a conclusion?

No. The intervals overlap almost entirely (0.00–0.58 before, 0.06–0.93 after). With ten random conversations per period, one extra flag moves the corrected estimate by about 13 points, and the interval also carries the uncertainty in the judge's own test-set rates. The honest reading is "no detectable change"; the movement is consistent with run-to-run and judge-to-judge noise.

My own judge shows why judge quality matters for this: its corrected estimates span 0–100% in both periods. Its low sensitivity (0.45) makes the correction divide by 0.28, so it cannot support any conclusion about the rate.

## 3. What did the risk groups reveal that the random estimate did not?

The risk group did the opposite of what I expected. Conversations that looked up a policy were flagged *less* often (1 of 16 before, 1 of 17 after; the same conversation, support-0214, both times) than the random sample (2 and 4 of 10). Of the four random conversations flagged across both periods, only support-0214 looked up a policy; support-0085, for example, answered from `get_order` alone and still stated a refund rule. With these few cases it is a hint, not a finding, but it suggests a retrieved policy keeps the agent grounded and unsupported claims come from answers made without one. A better risk group for this failure might be "states a policy fact without a policy lookup".

## 4. What should happen if the estimate crosses the threshold?

The after estimate (0.44) crosses 0.15, so by my own rule it triggers error analysis, even though question 2 says the crossing is weak evidence:

1. Read the flagged conversations from both selections (support-0085, -0161, -0165, -0214) and label each by hand.
2. Confirmed failures become evaluation cases in the Homework 6 suite. support-0085 is a candidate: a cancelled, unshipped order where the agent should not invent refund policy.
3. support-0161 suggests the judge itself is inconsistent on near-identical replies. Before trusting another crossing, re-check the judge on fresh human labels and look at its critique for that case.
4. Act on a repeated crossing, or an interval entirely above the threshold, rather than on one period of ten conversations; a larger random sample would narrow the interval.

## Part D note

`.github/workflows/monitor.yml` runs `monitoring.run --last-hours 24` daily and on demand, with the Langfuse keys and the judge key as repository secrets and `LANGFUSE_HOST` as a variable. Langfuse runs on my computer, so the workflow needs a self-hosted runner to reach it; I did not register one, so the workflow has not been run on GitHub. The same command was tested locally: an empty window records a zero count and exits without calling the judge, and the 24-hour window finds the after run's 50 conversations.

Monitor scores are dated to the traces they judged, so the Langfuse dashboard plots the before period on 2026-09-18 and the after period on 2026-10-30.
