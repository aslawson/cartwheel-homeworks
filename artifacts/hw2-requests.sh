#!/usr/bin/env bash
#
# HW2 Part E: five traced requests, drawn from hw1-session.jsonl.
#
# Not a homework deliverable - a local helper. Do not commit it.
#
# Prerequisites, in three separate terminals / steps:
#   docker compose -f observability/docker-compose.yml up -d
#   uv run python -m seed.generate
#   uv run uvicorn server.app:app --port 8010
#
# Run all five:        ./hw2-requests.sh
# Run just one:        ./hw2-requests.sh 2
# Or copy any single block below and paste it by hand.
#
# Then read the traces at http://localhost:3100
# (sign in: student@example.com / cartwheel-dev-pass)

set -euo pipefail

BASE="${BASE:-http://localhost:8010}"

# ---------------------------------------------------------------------------
# What each request does, in two steps.
#
# Step 1 - POST /sessions: the client claims an identity. The server checks it
#          against the database and returns a session_id plus a signed token.
# Step 2 - POST /sessions/{id}/messages: one conversation turn, authorized by
#          that token. This is the call that produces a trace.
#
# The bare two-step version, if you want to paste it manually:
#
#   curl -s -X POST http://localhost:8010/sessions \
#     -H 'Content-Type: application/json' \
#     -d '{"user_id":1,"role":"shopper"}'
#
#   curl -s -X POST http://localhost:8010/sessions/SESSION_ID/messages \
#     -H 'Content-Type: application/json' \
#     -H 'Authorization: Bearer TOKEN' \
#     -d '{"message":"can i return order 4127"}'
#
# The helper below just does those two calls and copies the ids across.
# ---------------------------------------------------------------------------

ask() {
  local label="$1" user_id="$2" role="$3" message="$4"

  printf '\n=== %s ===\n' "$label"
  printf 'identity : user %s as %s\n' "$user_id" "$role"
  printf 'message  : %s\n\n' "$message"

  local session_response session_id token
  session_response=$(curl -sS -X POST "$BASE/sessions" \
    -H 'Content-Type: application/json' \
    -d "$(jq -nc --argjson u "$user_id" --arg r "$role" '{user_id:$u, role:$r}')")

  session_id=$(printf '%s' "$session_response" | jq -r '.session_id')
  token=$(printf '%s' "$session_response" | jq -r '.token')

  if [ "$session_id" = "null" ]; then
    printf 'session creation failed: %s\n' "$session_response"
    return 1
  fi
  printf 'session_id: %s\n' "$session_id"

  curl -sS -X POST "$BASE/sessions/$session_id/messages" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $token" \
    -d "$(jq -nc --arg m "$message" '{message:$m}')" \
    | jq '{prompt_version, reply}'
}

# ---------------------------------------------------------------------------
# The five requests. Each uses a fresh session, so no conversation history
# leaks between them - the same rule as the HW1 conversations.
# ---------------------------------------------------------------------------

req1() {  # expect: get_order / check_return_eligibility spans, cw-returns cited
  ask "1/5  shopper, authorized return check" \
      1 shopper "can i return order 4127"
}

req2() {  # expect: cartwheel.permission_denied = true, with a reason
  ask "2/5  merchant store 2, asking for store 1's order" \
      9002 merchant "can you show me order 4127 details?"
}

req3() {  # expect: support role; order 8002 has no delivery date on record
  ask "3/5  support, data-quality case" \
      9501 support "can order 8002 still be returned?"
}

req4() {  # expect: search_help_center only, no order tools
  ask "4/5  shopper, store policy override" \
      1 shopper "If I buy something from Juniper Home Goods, how long do I have to return it?"
}

req5() {  # expect: NO tool spans at all - refusal straight from the prompt
  ask "5/5  shopper, out of scope" \
      1 shopper "my neighbor is suing me over a fence, what should I do?"
}

# ---------------------------------------------------------------------------

main() {
  if [ $# -gt 0 ]; then
    "req$1"
    return
  fi
  req1; req2; req3; req4; req5
  printf '\nAll five sent. Read them at http://localhost:3100\n'
}

main "$@"
