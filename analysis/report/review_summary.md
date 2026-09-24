# Review summary (Homework 4)

## The reviewed sample, and how it falls short

**31 conversations reviewed, against the handout's requirement of 100.** The
reviewer stopped at 30 and said so; the 31st arrived by accepting a depth-search
suggestion. This is the assignment's main shortfall and it is not made up for
elsewhere: every count below is drawn from 31 conversations.

| | |
| --- | --- |
| Conversations reviewed | 31 of 250 in the store |
| Human annotations | 53 (52 written during review, 1 from an accepted suggestion) |
| Roles | 22 shopper · 7 merchant · 2 support |
| Groups | 23 coverage · 8 challenge |
| Intents | policy_question 8 · order_status 6 · refund 5 · product_search 4 · out_of_scope 3 · return_eligibility 2 · cancellation 2 · dispute 1 |
| Multi-turn | 8 |
| Damaged-record cases | 4 |

**How they were selected.** The reviewer read conversations in id order from
`support-0001`, so this is a sequential block, not a sample. The handout's four
batches (15 uniform, 15 cluster representatives, 30 across one dimension, 25
from depth searches, 15 final) were not used. `analysis/state/sample_manifest.json`
records this honestly: the first-pass traces are labelled
`read in id order during the reviewer's first pass`, and the later uniform and
cluster batches were generated but not reviewed.

**What this costs.** Three things the assignment expects cannot be claimed:
theoretical saturation (no final batch was read, so the count of new modes in a
final 15 is unavailable), coverage of the challenge set (8 of 75), and
prevalence-like reading of the fractions below. HW5 requires 30 Fail labels per
mode; the largest mode here has 13 and the smallest has 2, so a targeted
labelling round is needed early in that assignment for every mode.

## The taxonomy

Seven binary modes, each traced to the reviewer's own annotations. Full
definitions, boundaries, positives and close negatives are in
`analysis/state/patterns.json`.

| Mode | Fail | of 31 | Requirement | Evaluator |
| --- | --- | --- | --- | --- |
| `unrequested_narration_and_padding` | 13 | 42% | Specification gap (see below) | LLM judge |
| `scope_handling_overreach` | 8 | 26% | SCOPE-2, RESP-3, ESC-3 | LLM judge |
| `commits_before_verification` | 5 | 16% | RESP-2 | LLM judge |
| `repeats_work_across_turns` | 5 | 16% | No requirement covers conversation state; candidate revision | LLM judge |
| `ambiguity_resolved_by_guess` | 3 | 10% | RESP-3 | LLM judge |
| `exposes_internal_data_defect` | 3 | 10% | RESP-3, RESP-4, ESC-3 | LLM judge |
| `wrong_policy_scope` | 2 | 6% | RESP-1 | LLM judge |

These are **sample fractions on a sequential block of 31 conversations**, not
prevalence estimates. Homework 5 estimates prevalence over the full store.

Every trace carries a judgment for every mode: 217 labels (31 × 7), 39 fail and
178 pass, in `analysis/state/labels/` and mirrored to Langfuse as scores.

**State of the Langfuse mirror.** The 217 scores were written twice: a first
sync run crashed partway through on a course demo fixture
(`labels/unsupported_policy_claim.jsonl`, whose rows have no `mode` field and no
real trace), and the repaired run rewrote them all. Deletion of the 186 extras
was requested through the API, which queues them; the local Langfuse worker
processes score deletions roughly one every two minutes, so the mirror still
holds duplicate scores as of writing. The duplicates carry identical values, so
no judgment is ambiguous, and `analysis/state/labels/` is unaffected and
remains the authoritative copy. `analysis/review_app/sync_scores.py` now skips
demo fixtures and traces outside this store, so the crash cannot recur.

**Provenance of the labels, stated plainly.** The fails were derived from the
reviewer's annotations: a mode is marked present on a trace when one of the
reviewer's notes was grouped under it. The passes were derived from the absence
of such a note, on the reviewer's statement that their per-trace review was
exhaustive rather than stopping at the first failure. They are recorded with
`source: derived_from_annotations` and the originating note ids. The reviewer
did not click through the 217 decisions individually, so a mode that the
reviewer was not yet looking for when they read a trace could be a false
negative.

## The specification gap

The largest mode, `unrequested_narration_and_padding`, is a **specification
failure, not a generalization failure**. The system prompt in `agent/agent.py`
orders the behaviour: "You MUST explain your reasoning in plain text before
every tool call. State what you are about to look up and why, in one sentence."
The agent complies; the reviewer's 17 notes say the product should not do it.
An evaluator against this mode would measure obedience to an instruction the
product does not want. The fix is a prompt and specification change, and the
reviewer chose to leave `SPEC.md` unedited for now, so **no `SPEC.md` revision
is committed with this assignment**. Three other modes
(`scope_handling_overreach`, `exposes_internal_data_defect`,
`repeats_work_across_turns`) also lack a requirement that states the desired
behaviour precisely.

## Taxonomy revisions

Three revisions are recorded in `patterns.json`; the first is the substantial one.

**1. `acts_without_authorization_check` was retired, taking the taxonomy from 8
modes to 7.** Five annotations (on support-0006, 0009, 0011, 0058) said the
agent showed or promised order details without confirming the caller was
entitled to them. The reviewer questioned whether that was possible, and the
code settles it: `get_order_logic` calls `can_view_order(ctx, ...)` and returns
`permission_denied` when the caller is not entitled (`agent/auth.py:48`), and
`find_order` derives its scope from the auth context. A successful tool result
is therefore evidence that access was authorized, so a reply reporting it cannot
be an authorization failure. Two annotations also misread their traces:
support-0009's `find_order` results were already scoped to that shopper, and in
support-0006 the merchant does own order 8003 — its *product* belongs to another
store, which is the `dq-order-store-mismatch` defect. The five annotations stay
in `annotations.json`, attached to the retired mode.

**2. `scope_handling_overreach` was narrowed to "an outward pointer or an
invented procedure".** Triggered by rejecting support-0143 as a close negative:
it declines, then says "I'd recommend checking that marketplace's own seller
support". Naming its own capabilities, pointing at Cartwheel account settings,
or escalating where ESC-2 requires it is no longer this failure.

**3. `commits_before_verification` gained a verification test**, from the
depth-search decisions: reading a record's own state (order status) counts as
verification, so support-0052 is a pass; asserting refund or return eligibility
requires `check_return_eligibility`, because eligibility depends on the store's
window and the stored `refund_eligible` flag can be stale — it disagrees with
the eligibility function on damaged order 8001.

## Depth search, and the rejected suggestions

`analysis/review_app/depth_search.py` searched all 250 conversations for
`commits_before_verification`, anchoring each hit to the sentence that triggered
it. Three candidates were offered; the reviewer **accepted one** (support-0086,
which asserts refund eligibility from `find_order`'s flag alone) and **rejected
two**:

* **support-0115** — "Before I can process anything, I need to pull up the order
  details" is an announcement, not a claim. A pattern false positive.
* **support-0052** — claims it can cancel after `find_order` returned the order
  status, which the reviewer ruled counts as verification. This rejection is
  what produced revision 3, and support-0052 became a close negative for the mode.

Both rejections are kept in `analysis/state/suggestions.json` with
`status: rejected`.

**What the search showed about the method.** Once the reviewer's verification
test is applied, the search returns no further instances beyond the five known
positives in 250 conversations. Phrase matching either floods (27 hits, mostly
"let me check whether it's still eligible") or misses, because the failure is
semantic: a claim standing ahead of its evidence. This is the argument for an
LLM judge in HW5.

## Comparison with the published taxonomy

AgentErrorTaxonomy (arXiv:2509.25370) organises failures by module — memory,
reflection, planning, action, system-level. Four of the seven modes map onto it:
`repeats_work_across_turns` to memory, `commits_before_verification` to
reflection, `ambiguity_resolved_by_guess` to planning, `wrong_policy_scope` to
action. `unrequested_narration_and_padding` and `exposes_internal_data_defect`
have no equivalent, because that taxonomy classifies causes of task failure
while this one also covers replies that complete the task but are wrong for a
customer.

The gap it points at is **system-level**, where a tool-contract error belongs:
`search_products` called with a store id in the store-name field, then retried
without the filter (support-0028, and again in Workshop run `27da6cc2`). It was
**not added**: one human annotation supports it, and the handout requires a mode
to rest on the reviewer's own traces and notes. It remains in `rejected_groups`
as a code-check candidate for HW5.

## Raindrop Workshop

Eight scenarios were replayed with Raindrop mirroring enabled; five findings
were offered and the reviewer accepted three and rejected two. Workshop
surfaced no failure mode the reviewer's own open coding had not produced. Full
notes, run ids and limitations are in `analysis/report/workshop_notes.md`.

## Deliverables and gaps

| Required | Status |
| --- | --- |
| Review interface under `analysis/review_app/` | ✅ |
| `sample_manifest.json`, `annotations.json`, `patterns.json`, `suggestions.json` | ✅ |
| One label file per mode under `labels/` | ✅ 7 files, 217 labels |
| `review_summary.md`, `workshop_notes.md`, `interface_comparison.md` | ✅ |
| At least 100 traces reviewed | ❌ 31 |
| Four selection batches | ❌ sequential block; batches generated but not read |
| New modes in a final 15 | ❌ no final batch was reviewed |
| `SPEC.md` revision | ❌ not made; the gap is documented above |
| Video | ❌ declined by the reviewer |
