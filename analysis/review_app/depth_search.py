"""Search the whole store for more instances of one confirmed failure mode.

This is a retrieval signal, never a label (SKILL.md, phase 5): it over-suggests
on purpose, because a false suggestion costs one click to dismiss while a
missed instance costs coverage. Every hit is written to
analysis/state/suggestions.json for the reviewer to accept or reject in the
review app, anchored to the exact sentence that triggered it.

Anchoring: `idx` must match the block index the interface assigns while
rendering, which counts the user message, then every narration and tool step
(thinking markers do not render as blocks), then the final reply, per turn.

    uv run python analysis/review_app/depth_search.py commits_before_verification
    uv run python analysis/review_app/depth_search.py ambiguity_resolved_by_guess --limit 15
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "analysis" / "state"

# A claim that an outcome holds. "Let me check whether it is still eligible" is
# an announcement, not a claim, so intent-to-check is excluded first; "great
# news" only counts when it is about the outcome, not about finding products.
CLAIM = re.compile(
    r"\b(you'?re (still )?(eligible|good to go|in time|within the)|"
    r"(this |the |your )?order (is|remains) (still )?eligible|"
    r"i can (refund|cancel|process|approve)|i'?ll (refund|cancel|process)|"
    r"(refund|cancellation) (is|has been) (approved|processed|complete|done)|"
    r"you can (still )?(return|cancel)|"
    r"(good|great) news[^.!?]{0,80}(refund|return|cancel|eligib))", re.I)
ANNOUNCE = re.compile(
    r"\b(let me|i'?ll|i will|going to|let's)\b[^.!?]{0,60}\b(check|verify|confirm|pull up|look)\b", re.I)
# The tool results that decide those claims.
DECIDERS = {"get_order", "check_return_eligibility", "issue_refund", "cancel_order", "get_policy",
            "search_help_center"}


def blocks(conversation: dict[str, Any]):
    """Yield (idx, kind, payload) exactly as the interface numbers them."""
    idx = 0
    for turn in conversation["turns"]:
        yield idx, "user", turn["user"]
        idx += 1
        for step in turn["steps"]:
            if step["kind"] == "thinking":
                continue
            yield idx, step["kind"], step
            idx += 1
        yield idx, "reply", turn["reply"]
        idx += 1


def find_commits(conversation: dict[str, Any]) -> tuple[int, str] | None:
    """First claim that lands before any deciding tool result."""
    decided = False
    for idx, kind, payload in blocks(conversation):
        if kind == "tool" and payload.get("name") in DECIDERS:
            decided = True
            continue
        if kind in ("narration", "reply") and not decided:
            text = payload if isinstance(payload, str) else payload.get("text", "")
            match = CLAIM.search(text or "")
            if match:
                sentence_of_match = next(
                    (x for x in re.split(r"(?<=[.!?])\s+", text) if match.group(0) in x), text)
                if ANNOUNCE.search(sentence_of_match):
                    continue  # "let me check whether it is still eligible" is a plan
                sentence = next((s for s in re.split(r"(?<=[.!?])\s+", text) if match.group(0) in s), text)
                return idx, sentence.strip()[:200]
    return None


def find_ambiguity(conversation: dict[str, Any]) -> tuple[int, str] | None:
    """Several matches came back and the next reply names one without asking."""
    multi = False
    for idx, kind, payload in blocks(conversation):
        if kind == "tool" and payload.get("name") in ("find_order", "search_products", "list_my_orders"):
            result = payload.get("result")
            if isinstance(result, dict):
                multi = len(result.get("orders") or result.get("products") or []) > 1
        elif kind in ("narration", "reply") and multi:
            text = payload if isinstance(payload, str) else payload.get("text", "")
            if re.search(r"which (one|order|item)|confirm which|do you (know|remember) which", text or "", re.I):
                return None  # it asked; not this mode
            picked = re.search(r"(order|product) #?\d+", text or "", re.I)
            if picked:
                return idx, (text or "")[max(0, picked.start() - 60):picked.end() + 100].strip()
    return None


FINDERS = {"commits_before_verification": find_commits,
           "ambiguity_resolved_by_guess": find_ambiguity}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=sorted(FINDERS))
    parser.add_argument("--limit", type=int, default=12, help="max suggestions to write")
    parser.add_argument("--keep-existing", action="store_true")
    args = parser.parse_args()

    conversations = json.loads((STATE / "conversations.json").read_text())
    patterns = json.loads((STATE / "patterns.json").read_text())
    mode = next(m for m in patterns["modes"] if m["name"] == args.mode)
    known = set(mode["positive_traces"])

    hits = []
    for conversation in conversations:
        if conversation["id"] in known:
            continue
        found = FINDERS[args.mode](conversation)
        if found:
            idx, quote = found
            hits.append({
                "id": f"sg-{args.mode[:12]}-{conversation['id'][-4:]}",
                "trace_id": conversation["id"], "mode": args.mode, "idx": idx, "quote": quote,
                "note": f"possible {args.mode}: " + (
                    "a claim about the outcome appears before the tool result that decides it"
                    if args.mode == "commits_before_verification"
                    else "several matches came back and the reply names one without asking which"),
                "source": "agent_depth_search", "status": "pending",
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

    existing = []
    path = STATE / "suggestions.json"
    if args.keep_existing and path.exists():
        existing = json.loads(path.read_text() or "[]")
        existing = existing if isinstance(existing, list) else []
    seen = {(s.get("trace_id"), s.get("mode")) for s in existing}
    fresh = [h for h in hits if (h["trace_id"], h["mode"]) not in seen][: args.limit]
    path.write_text(json.dumps(existing + fresh, indent=1))

    print(f"{args.mode}: {len(hits)} candidate(s) across {len(conversations)} conversations; "
          f"wrote {len(fresh)} (limit {args.limit}) to {path.relative_to(ROOT)}")
    for h in fresh:
        print(f"  {h['trace_id']} idx {h['idx']}: {h['quote'][:110]}")


if __name__ == "__main__":
    main()
