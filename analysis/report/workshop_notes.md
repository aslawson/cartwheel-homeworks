# Raindrop Workshop notes (Homework 4, Part C)

Eight scenarios were replayed on 2026-09-23 against a freshly seeded database,
on `claude-opus-4-6`, through the Homework 2 endpoint with Raindrop mirroring
enabled (`CARTWHEEL_WORKSHOP=1`). Twelve turns produced twelve Workshop runs.

**These are hypotheses, not labels.** Nothing here becomes a failure mode until
the reviewer inspects the trace and accepts it. The accept/revise/reject
decision for each item is recorded in the last column and in
`analysis/state/suggestions.json`.

## Setup

* Instrumentation: `raindrop-openai-agents` registers a processor with the
  OpenAI Agents SDK. The existing OpenTelemetry pipeline into Langfuse is
  untouched, and both recorded every run (verified on the smoke run).
* **A Workshop run id is the same string as the Langfuse trace id**, so every
  run below opens in either tool:
  `http://localhost:5899/runs/<id>` and
  `http://localhost:3100/project/cartwheel-dev/traces/<id>`.
* Runs were given fresh scenario ids (`workshop-01` … `workshop-08`) so they
  can never be confused with the Homework 3 export.

## Runs inspected

| Scenario | Source | Role | Workshop run id(s) | Tools |
| --- | --- | --- | --- | --- |
| workshop-01 | support-0144 | merchant | `1e880013f64b4a9c9b4a08fdd50b8e84`, `02ad9a1c6ccf…` | get_order, search_help_center, issue_refund |
| workshop-02 | support-0022 | shopper | `e8772121dec7…`, `02b0df5c88cc…` | get_order, cancel_order |
| workshop-03 | support-0032 | shopper | `6c3b0d5080c6…`, `4567951aa047…`, `9dd4b44ef2b3…` | get_order, escalate_to_human |
| workshop-04 | support-0019 | support | `37a141e277a9…` | search_products |
| workshop-05 | support-0028 | merchant | `27da6cc22bc9df3d34d5ff1bf37f2bb2` | search_products ×2 |
| workshop-06 | support-0026 | shopper | `8ceb7e97cf37…` | find_order |
| workshop-07 | support-0031 | shopper | `c7a315fa4f03…` | list_my_orders |
| workshop-08 | support-0002 | support | `8e5095848bc8…` | search_help_center |

## Candidate findings

### 1. A failed tool call silently drops its scope filter (workshop-05)

Run `27da6cc22bc9df3d34d5ff1bf37f2bb2`. The merchant asks what they priced
their own heavy-duty vase at. The agent calls:

```
search_products {"query": "heavy-duty vase", "store": "1"}  -> ok:false  no store named '1'
search_products {"query": "heavy-duty vase", "store": null} -> ok:true   3 products
```

It then replies "you actually have **three** Heavy-Duty Vase listings in your
store". The second call searches **every** store, so "in your store" is an
assertion the tool result does not support. Here the three products happen to
belong to store 1, so the answer is accidentally right.

Two distinct defects are visible in one run: the store id was passed into a
field the contract defines as a store *name or slug*, and the retry silently
widened the search rather than correcting the argument.

Relates to the reviewer's existing `tool_argument_misuse` group (held out of
the taxonomy) and to `acts_without_authorization_check`.

### 2. `find_order` matches the wrong item, and the store hint is ignored (workshop-06)

Run `8ceb7e97cf37…`. The user asks about "pencil set from atlas stationery".
`find_order` returns order 1943, which is a **Rustic Socket Set from
Copperline Tools** (store 4), not the Atlas Stationery pencil set. The reply
says "Your pencil set order (#1943) was delivered on April 13, 2026."

The tool result carries no product title, so the agent cannot see the
mismatch; it also had the store name from the user and did not use it to
filter. This is the same shape the reviewer already annotated on
support-0021 during the Homework 3 pilot.

Supports `ambiguity_resolved_by_guess`, with a tool-contract cause.

### 3. An unnecessary policy lookup before a routine refund (workshop-01)

Run `1e880013f64b…`. For a $78.50 refund, the agent called
`search_help_center("refund auto-approval threshold")` before `issue_refund`,
although the amount is far below the $100 line and `issue_refund` enforces the
threshold itself. Model time for that turn was 15.9 s.

Weak on its own: checking policy before acting is defensible, and the reviewer
has annotated the *opposite* failure (acting before checking). Offered mainly
because the timing makes the cost visible.

### 4. Latency is model time, not tool time (all runs)

Across the twelve runs, tool spans take **0 to 8 ms** while model spans take
**3.2 to 16.7 s**. Every span is `status=OK`; the failed `search_products`
above is recorded as OK at the span level because the tool returned a
structured `ok:false` result rather than raising.

Not a failure mode. Worth recording because it says where any latency work
would have to go, and because **span status cannot be used to find failures**
in this application: a refused or errored tool still reports OK.

### 5. A close negative for `repeats_work_across_turns` (workshop-03)

Run `4567951aa047…` is turn 2 of the pressing cancellation. It makes **no tool
calls at all** and answers from the prior turn's context. The reviewer's mode
is about redoing work across turns; this run is the opposite and is a useful
close negative for that definition.

## The case I am least sure about

Finding 1. An alternative reading is that the agent behaved reasonably: the
tool rejected the argument, so it retried with a wider search and reported what
came back, and every returned product did belong to the merchant's store.
Whether this is a failure depends on a judgment the trace cannot settle: does
"in your store" have to be supported by a scoped query, or is it enough that
the answer happens to be correct? The tool layer, not the model, is what
enforces authorization in Cartwheel (AUTH-1), and nothing here crossed that
boundary. I have recorded it as a candidate rather than a finding.

## What Workshop did and did not contribute

It surfaced no failure mode the reviewer's own open coding had not already
produced. Findings 1 to 3 restate patterns already annotated during Homework 3
and Homework 4 review (`tool_argument_misuse`, `ambiguity_resolved_by_guess`,
`unrequested_narration_and_padding`). Its two genuinely new contributions are
structural rather than behavioural: every span reports `status=OK` even when a
tool returned `ok:false`, so span status cannot be used to find failures in
this application; and a failed call and its retry sit adjacent in one view,
which is what made the dropped store filter in finding 1 obvious.

Two reasons the tool contributed less here than the assignment anticipates.
First, its rendering of an assistant message is a raw JSON dump of the whole
message object, including two copies of the encrypted thinking block, so a
reply is harder to read than in either Langfuse or the review interface built
for Part A. Second, Workshop is the local debugger half of Raindrop: the
automatic issue detection (preset and custom signals over production traffic)
belongs to the hosted product, which was not used here. Workshop is aimed at
watching an agent while you build it, not at finding failure modes across a
recorded corpus, which is what this assignment needs.

The run set is still recorded above, and findings 1 and 2 point at a cause the
reviewer's annotations could not see on their own: `find_order` and
`search_products` return results without the fields the agent needs to check
its own answer.

## Limitations of this pass

* Runs show `user_id: unknown`. This wrapper version takes identity only at
  construction, and the server is multi-user, so per-request identity was not
  set. Role and user id remain in Langfuse.
* Per-span token counts arrive empty; totals are on the run record.
* Workshop's span list API returns structure only. Content (messages, tool
  arguments, results) is available in the UI and at
  `/api/runs/detail/<run id>`, which is where the quotes above come from.
* Eight scenarios is a small sample chosen for tool and role coverage, not a
  random draw. It cannot say how often any of this happens.

## Decisions

| # | Finding | Decision | Note |
| --- | --- | --- | --- |
| 1 | Scope filter dropped on retry | rejected as a mode | Kept as evidence for the held-out `tool_argument_misuse` group; the authorization boundary is enforced in the tool layer, and the answer was correct here |
| 2 | find_order matches the wrong item | accepted | Evidence for `ambiguity_resolved_by_guess`; same shape as the Homework 3 pilot finding on support-0021 |
| 3 | Unnecessary policy lookup | rejected | Weak on its own, and in tension with `commits_before_verification`, which penalises the opposite order |
| 4 | Latency and span status observation | accepted, not a mode | Recorded because span status cannot be used to find failures in this application |
| 5 | Close negative for repeats_work_across_turns | accepted | Used as a close negative for that mode |

Decisions recorded by the reviewer on 2026-09-23.
