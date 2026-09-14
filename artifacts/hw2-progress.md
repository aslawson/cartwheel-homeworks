# HW2 progress note (local, not part of the submission)

Style: the handout's own walkthrough pacing - propose each step, wait for a go-ahead.
Branch: hw (HW1 committed as 9244f0e, rebased onto upstream 461fdea)

## Current status
Parts A-F done and committed (023325e). Student has DECLINED hw2-traces.json and
the video; both are on the handout's committed-files list, so the submission is
incomplete by the handout's terms. Not waived by me.

## Deliverables checklist
- [x] Part A: record_tool_result + _set_permission_denied_attributes
      (observability/instrument.py) - verified with an in-memory OTel exporter:
      role/user_id/store_id set, permission_denied True+reason on a denial,
      False on both success and a non-auth error.
- [x] Part B: create_session in server/app.py (400 unknown role, 404 unknown user,
      403 role mismatch, token carries session_id/user_id/role/store_id/issued_at)
- [x] Part C: post_message in server/app.py (root span cartwheel.session_message with
      user_role, user_id, prompt_version, scenario_id when supplied,
      gen_ai.input.messages, gen_ai.output.messages)
- [x] Part D: tests/test_observability.py (8 tests) (role mismatch rejected; token from one
      session cannot authorize another). Must not need Langfuse/Docker/model key.
- [x] Part E: 7 traced requests through the endpoint, all 3 roles, read in Langfuse
- [x] Part F: two prompt versions compared, same request and data, fresh session each:
      d919fdad59ca (HW1 wording) vs 7554069158a9 (after the Part C edit). Replies were
      near-identical - this shows version recording works, NOT that the edit helped.
      (HW1 Part C gives a real revision:
      59b7354250a7 before -> 27356d929fe6 after; current prompt is 7c9370a7b098
      after the upstream rebase added a mandatory-reasoning instruction)
- [ ] hw2-traces.json: DECLINED by the student (still required by the handout).
      Candidates if ever revisited: 86ddb7b462fc384d43ddde42991f5f64 (merchant denial,
      prompt 7554069158a9) and 26eef62a0a9dd070e9d15a8beaf80fed (shopper, d919fdad59ca).
- [ ] Video: DECLINED by the student, as with HW1's (still required by the handout)

## Notes
- prompt_version CHANGED upstream: it now hashes the TEMPLATE, not the rendered
  prompt, and cli.py calls prompt_version() with no argument. The per-role hashes
  recorded in hw1-session.jsonl (378070f017d6 merchant, 6e4b9665c061 support) were
  correct for the code at the time but no longer reproduce. Current template hash:
  7554069158a9.
- post_message gates gen_ai.input/output.messages on TRACELOOP_TRACE_CONTENT, per the
  function docstring (Part C's bullet list does not mention the condition).
- _authorize runs BEFORE the root span opens, so rejected requests produce no trace.
  Deliberate and handout-compliant; the alternative (trace failed auth attempts) was
  noted and not taken.
- Known pre-existing failure in the full suite only:
  tests/test_hw_holes.py::test_m2_run_judge_persists_store_predictions_for_prevalence
  passes in isolation; a test-isolation issue, unrelated to this work.

## Environment
- Docker 29.4.0 + Compose v5.1.2, daemon running
- .env: ANTHROPIC_API_KEY set, CARTWHEEL_MODEL=claude-opus-4-6,
  LANGFUSE_* present, TRACELOOP_TRACE_CONTENT=true
- HW1 tools complete; 8/8 focused + 31/31 regression tests pass after the rebase
