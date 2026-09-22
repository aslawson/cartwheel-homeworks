# HW3 progress note (local, not part of the submission)

Style: propose each step, wait for a go-ahead. Student decides at every review gate.
Model: CARTWHEEL_MODEL=claude-opus-4-6 (Anthropic). Same value goes to --model on every run.
Ports: Langfuse 3100, API 8010, my trace viewer 8020, reference review UI 8022 (8021 held by launchd).

## Current status
- [x] Preparation: HW2 done (own implementation, no reference patch); Langfuse up; .env has model + TRACELOOP_TRACE_CONTENT=true
- [x] Skill Step 1 smoke check (2026-09-18): trace ea33a954... has user msg + reply, anthropic/claude-opus-4-6,
      list_my_orders call+result, cartwheel.scenario_id=smoke-test-001. Data reseeded afterwards.
- [x] Part A: dimensions approved by student (2026-09-18), see below
- [~] Part B step 1-3 done: 30 grounded plans in artifacts/hw3/pilot_plans.jsonl (built + asserted by
      artifacts/hw3/build_pilot_plans.py; no model calls yet). Student approved the mix.
- [~] Skill Step 5 done: artifacts/hw3/generate_messages.py (claude-sonnet-5 via litellm, 1 call/convo + 1 critic
      call/convo, 4 workers) -> scenarios/pilot_scenarios.jsonl; validator passes (20 cov / 10 chal / 4 dq).
      Critic changed 0 of 30. My read: rambling scenarios 005/021/029/030 share a template + two "cat" asides.
      Student chose to regenerate the templated ones: 005/021/029/030 redone with --ids (seeing other same-style
      openings, no pets), then 030 again (it had copied 021's new "calendar app" aside). Validator passes.
      Student accepted and ran the pilot themselves.
- [x] Pilot run (2026-09-18 13:35-13:45 MDT): 30/30 completed on claude-opus-4-6 (~452 s), 33 good traces.
      NOTE: an earlier attempt with the literal `--model YOUR_MODEL` left 30 HTTP-500 traces at ~13:30 MDT tagged
      pilot-001..030 with no output - ignore them when reviewing (pilot IDs are not exported, so harmless).
      DB after run: refunds auto_approved for 517 and 246 only; 105 still placed; 161, 961, 1381, 89 untouched.
- [x] Student reviewed 12 pilot traces -> scenarios/pilot_review.jsonl: 6 confirmed failures (018, 021, 022, 023,
      027, 028), 3 invalid (006, 026, 030), 3 pass (024, 025, 029). Student decided each (023-029 accepted from my
      screened proposals). Part B complete; keep claude-opus-4-6 for the final run.
      Lessons for Part C:
        * 006/026/030 invalid: generator turned action goals into yes/no questions. Check every final expected
          result against the ACTUAL message; write explicit-instruction messages for action scenarios.
        * 018 (student): a clear refund request should be filed, not confirmed first.
        * find_order returns no product titles (021, 028) - do not name/group failure modes in HW3.
        * product-description scenarios: only_match() uniqueness check was skipped for 8002; apply it everywhere.
- [~] Part C: mix approved by student. Reseeded (pilot refunds of 517/246 undone). artifacts/hw3/build_final_plans.py
      -> artifacts/hw3/final_plans.jsonl, 250 plans, all asserts pass. Deviations from the promised mix, reported to
      student: coverage roles 129/30/16 (promised ~115/40/20), multi-turn 36 (promised ~50), none_given 12
      (promised ~15%), challenge is refund-heavy (29 of 75). Student accepted my fix: 4 stricter-override refunds ->
      eligibility questions, +17 followups -> 53 multi-turn, challenge refunds 25.
- [x] Messages: generate_messages.py (8 workers, max_tokens 2000, critic item 6 = request type, similar-opening check,
      --ids regen with opening-move hints). Fixed along the way: product-specific refund reasons ("journal stopped
      working") -> 8 generic reasons (same rng draws; tuples/expected unchanged); 0170 laptop goal reworded (writer
      kept turning it into an in-scope product search). `validate --final` passes.
- [x] check_consistency.py (sees expected, flags only): 15 flags -> 7 damaged-record false positives (user can't know
      the defect), 1 amount rounding false positive (0204), 0233 borderline (put in student sample), 0170 real (fixed),
      remaining were empty checker replies (5 damaged/0222 re-checked OK).
- [x] scenarios/support_review.jsonl: 15 records (11 accept, 4 revise). NOTE: student asked me to decide ("you can do
      it") and adopted my proposed decisions without reviewing each; they should skim before submitting.
      Revisions applied: pressuring style split (single-turn pushes once; followups never presume a refusal) + 3
      "after being told no" goals reworded (tuples/expected unchanged) -> 44 messages regenerated; --final passes;
      consistency re-check: 2 flags, both known false-positive types (0063 rounding, 0237 damaged record).
- [ ] VIDEO: student declined ("no video recording"). It is on the handout's deliverables; not waived by me.
- [x] scenarios/monitoring_scenarios.jsonl: 50 full records copied from support_scenarios (35 cov / 15 chal;
      shopper 30, merchant 12, support 8; all 9 intents; 1 per damaged record). Stratified pick by me (seed 50).
      Part C complete.
- [x] Part D (student ran it 2026-09-18 16:02): 250/250 completed on claude-opus-4-6, 175 cov / 75 chal.
- [x] Part E: reports/smoke-output.txt (30 errors in it are the earlier YOUR_MODEL pilot attempt, not this run;
      $6.06 total cost across all traces in the store; tools led by get_order 127, check_return_eligibility 126).
      traces/support_traces.json: 305 traces, 250/250 scenario ids. Checked 3 exported traces (support-0032 3-turn
      challenge, support-0006 damaged record, support-0110 coverage refund): conversation, anthropic/claude-opus-4-6,
      tool spans and cartwheel_scenario_id all present.
      REMAINING: student skims support_review.jsonl; video declined; nothing committed yet.
- [ ] Part B: 30 pilot scenarios -> validate -> run -> student reviews >=10 -> >=5 confirmed failures
- [ ] Part C: 250 final (175 coverage / 75 challenge, 5 per damaged record), student reviews 15, pick 50 monitoring
- [ ] Part D: reseed, run 250, all completed
- [ ] Part E: smoke report, export, check 3 traces
- [ ] Video (student)

## Approved dimensions (Part A)
Tuple fields (validator requires the first eight):
- role: shopper | merchant | support
- intent: order_status | return_eligibility | refund | cancellation | policy_question | product_search | dispute | account_change | out_of_scope
- record_state: order_in_window | order_past_window | order_placed | order_shipped | order_already_refunded_or_cancelled |
  order_above_refund_threshold | product | store_policy_page | damaged_record | none
- applicable_policy: platform_default | stricter_store_override (Juniper 14d, Saltbox 7d, Meridian 21d) |
  looser_store_override (Northwind 45d) | restocking_fee_store (Cascade Audio, Second Stitch) | none
- tools_needed: none | one_lookup | several_calls
- difficulty: well_specified | ambiguous | missing_information | boundary | correction_across_turns
- user_style: the validator's eight values (student's dimension)
- turn_count: 1 + len(followups)
- record_reference (added, approved): order_number | product_description | none_given | not_applicable

Considered and declined by student: access_scope (own / other's / nonexistent record).
Considered and folded in: refund amount (-> order_above_refund_threshold), turn pattern (-> correction_across_turns).

## Deliverables checklist
- [ ] scenarios/pilot_scenarios.jsonl
- [ ] scenarios/pilot-results.jsonl
- [ ] scenarios/pilot_review.jsonl        (student's judgments)
- [ ] scenarios/support_scenarios.jsonl
- [ ] scenarios/support_review.jsonl      (student's judgments)
- [ ] scenarios/monitoring_scenarios.jsonl
- [ ] scenarios/final-results.jsonl
- [ ] reports/smoke-output.txt
- [ ] traces/support_traces.json
