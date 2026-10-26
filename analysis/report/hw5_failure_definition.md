# HW5 failure definition: scope_handling_overreach

**Question.** When the user asks for something Cartwheel support cannot do, does the
reply send them outside Cartwheel or invent a procedure that Cartwheel's policies
do not describe?

**Fail** if the reply:

- directs the user to an outside party as the solution: the carrier (including
  "check the carrier tracking"), their bank or card issuer, another website or
  marketplace, a lawyer or accountant, or "contact the store/seller"; or
- describes a step, feature, or page that no policy supports, such as "refuse
  delivery", "intercept the package with the carrier", or "update it in your
  merchant dashboard"; or
- implies a route around a policy limit, such as offering to escalate a dispute
  that is past the 60-day window so a human "may review it case by case".

A third-party redirect is a Fail even when the reply also offers escalation.
Mentioning an outside party as information ("refund timing depends on your bank") is
not a redirect; it fails only when the user is told to go to or rely on that party.

Prompt history: `analysis/prompts/scope_handling_overreach-v0-smoke.txt` was run on
3 development traces as a pipeline check (support-0001, -0004, -0009). The
informational-mention rule above was added to v0 after support-0009 showed the judge
treating "timing depends on your bank" as a redirect, before v0's development run.

**Pass** if the reply declines briefly and points to what Cartwheel support can do
or escalates to a human, or if any step it describes is backed by policy:

- account and payment changes happen in account settings (`cw-account-security`)
- tracking is on the order page once shipped (`cw-shipping`)
- merchant payouts run weekly on Fridays, refunds deducted from the next payout (`cw-payouts`)

A policy-backed procedure passes even when the agent did not retrieve the policy
in that trace.

**Not this failure mode** (Pass here): invented numbers on a request Cartwheel can
serve (for example "5–7 business days"), missing policy citations, and a missing
escalation.

**Evidence needed:** the user's request, the final reply, and any
`search_help_center` or `get_policy` results, to tell a grounded procedure from an
invented one.

## Labels

Human labels: 28 Fail, 43 Pass (71 conversations, one per scenario). This is below
the handout's minimum of 30 Fail.

Why labelling stopped here: _(your explanation)_
