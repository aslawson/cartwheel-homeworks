"""Build the reviewable conversation records for the review app.

The HW4 handout asks for one record per conversation, not per turn: Cartwheel
writes one Langfuse trace per user turn, so a multi-turn conversation arrives
as several traces that must be stitched back together in order.

What this adds over analysis/helpers/normalization.py, and why:

* The agent narrates before each tool call ("Let me pull up order #9300"),
  and that narration is NOT in the root span's output - only the final reply
  is. The narration lives inside the *next* model call's input array, because
  that call is shown the conversation so far. So the richest source for one
  turn is its last GENERATION input, with the root span's output appended as
  the final reply. Reviewing without the narration hides the agent's stated
  reason for every tool call.
* Thinking parts carry a signature but no readable text (extended thinking is
  encrypted), so they are recorded as a marker, never rendered as reasoning.
* Each conversation is joined to its HW3 scenario: the tuple (role, intent,
  difficulty, policy, record state, style) and the expected result. The tuple
  is what lets a reviewer filter to one kind of case; the expected result is
  kept in a separate field so the interface can hide it by default.
* Flags (permission denied, a tool result with ok:false, a missing final
  reply, an unusually long tool sequence) are computed here so the header can
  show them without the reviewer opening every step.

    uv run python analysis/review_app/load.py            # from the HW3 export
    uv run python analysis/review_app/load.py --source langfuse

Writes analysis/state/conversations.json.
"""

from __future__ import annotations

import argparse
import ast
import base64
import json
import os
import statistics
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "analysis" / "state"
EXPORT = ROOT / "traces" / "support_traces.json"
SCENARIOS = ROOT / "scenarios" / "support_scenarios.jsonl"
RESULTS = ROOT / "scenarios" / "final-results.jsonl"
OUT = STATE / "conversations.json"


# --------------------------------------------------------------- trace source
def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def from_langfuse(limit: int = 500) -> list[dict[str, Any]]:
    """Pull traces straight from the local Langfuse, newest first."""
    load_env()
    host = os.environ["LANGFUSE_HOST"]
    auth = base64.b64encode(
        f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
    ).decode()

    def get(path: str) -> Any:
        request = urllib.request.Request(host + path, headers={"Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    out: list[dict[str, Any]] = []
    page = 1
    while len(out) < limit:
        listing = get(f"/api/public/traces?limit=50&page={page}")
        rows = listing.get("data") or []
        if not rows:
            break
        for row in rows:
            out.append(get(f"/api/public/traces/{row['id']}"))
        page += 1
    return out


def from_export(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    return data["traces"] if isinstance(data, dict) else data


# ------------------------------------------------------------------ rendering
def attributes(trace: dict[str, Any]) -> dict[str, Any]:
    meta = trace.get("metadata") or {}
    attrs = meta.get("attributes")
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except json.JSONDecodeError:
            attrs = {}
    merged = {k: v for k, v in meta.items() if k.startswith("cartwheel.")}
    if isinstance(attrs, dict):
        merged.update({k: v for k, v in attrs.items() if k.startswith("cartwheel.")})
    return merged


def part_text(part: dict[str, Any]) -> str:
    value = part.get("content", part.get("text"))
    if isinstance(value, str):
        return value
    return json.dumps(value) if value is not None else ""


def message_text(message: Any) -> str:
    """Text of a message in either the parts shape or the plain shape."""
    if not isinstance(message, dict):
        return str(message or "")
    parts = message.get("parts")
    if isinstance(parts, list):
        return "\n".join(part_text(p) for p in parts if p.get("type") == "text").strip()
    content = message.get("content")
    return content if isinstance(content, str) else json.dumps(content or "")


def steps_for_turn(trace: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str]:
    """Return (user message, steps, final reply) for one turn.

    Steps interleave the agent's narration with its tool calls and results, in
    the order they happened, reconstructed from the last model call's input.
    """
    observations = sorted(trace.get("observations") or [], key=lambda o: o.get("startTime") or "")
    generations = [o for o in observations if o.get("type") == "GENERATION"]
    tools = {}
    for obs in observations:
        if obs.get("type") == "TOOL":
            tools[obs.get("id")] = obs

    user = ""
    trace_input = trace.get("input")
    if isinstance(trace_input, list) and trace_input:
        user = message_text(trace_input[0])
    reply = ""
    trace_output = trace.get("output")
    if isinstance(trace_output, list) and trace_output:
        reply = message_text(trace_output[-1])

    steps: list[dict[str, Any]] = []
    history = generations[-1].get("input") if generations else None
    if isinstance(history, list):
        pending: dict[str, dict[str, Any]] = {}
        for message in history:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            parts = message.get("parts") if isinstance(message.get("parts"), list) else []
            if role == "assistant":
                for part in parts:
                    kind = part.get("type")
                    if kind == "text" and part_text(part).strip():
                        steps.append({"kind": "narration", "text": part_text(part).strip()})
                    elif kind == "thinking" or "signature" in part:
                        steps.append({"kind": "thinking", "text": None})
                    elif kind == "tool_call":
                        call = {
                            "kind": "tool",
                            "call_id": part.get("id"),
                            "name": part.get("name") or part.get("tool_name"),
                            "arguments": part.get("arguments") or part.get("args") or part.get("input"),
                            "result": None,
                        }
                        steps.append(call)
                        if call["call_id"]:
                            pending[call["call_id"]] = call
            elif role == "tool":
                for part in parts:
                    target = pending.get(part.get("id"))
                    response = parse_result(part.get("response"))
                    if target is not None:
                        target["result"] = response
                    else:
                        steps.append({"kind": "tool", "call_id": part.get("id"), "name": None,
                                      "arguments": None, "result": response})

    # Fill names and results from the TOOL observations, which are authoritative.
    by_name = defaultdict(list)
    for obs in observations:
        if obs.get("type") == "TOOL":
            by_name[obs.get("name")].append(obs)
    unmatched = [o for o in observations if o.get("type") == "TOOL"]
    for step in steps:
        if step["kind"] != "tool":
            continue
        if step.get("name") is None and unmatched:
            obs = unmatched.pop(0)
            step["name"] = obs.get("name")
            step["arguments"] = step["arguments"] or obs.get("input")
            step["result"] = step["result"] if step["result"] is not None else parse_result(obs.get("output"))
        else:
            for obs in list(unmatched):
                if obs.get("name") == step.get("name"):
                    step["arguments"] = step["arguments"] or obs.get("input")
                    if step["result"] is None:
                        step["result"] = parse_result(obs.get("output"))
                    unmatched.remove(obs)
                    break
    return user, steps, reply


def parse_result(value: Any) -> Any:
    """Tool results arrive as a Python repr string ("{'ok': True, ...}").

    json.loads cannot read that (single quotes, True/None), so fall back to
    literal_eval and keep the raw string when neither parses.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "{[":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return value


def flags_for(steps: list[dict[str, Any]], reply: str, tool_total: int, cutoff: float) -> list[str]:
    flags: list[str] = []
    results = [s.get("result") for s in steps if s["kind"] == "tool"]
    for result in results:
        if isinstance(result, dict):
            if result.get("error") == "permission_denied":
                flags.append("permission denied")
            elif result.get("ok") is False:
                flags.append(f"tool error: {result.get('error', 'ok:false')}")
    if not reply.strip():
        flags.append("no final reply")
    if tool_total >= cutoff:
        flags.append(f"{tool_total} tool calls")
    seen = []
    for flag in flags:  # keep order, drop repeats
        if flag not in seen:
            seen.append(flag)
    return seen


def build(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scenarios = {}
    if SCENARIOS.exists():
        scenarios = {json.loads(l)["id"]: json.loads(l) for l in SCENARIOS.read_text().splitlines() if l.strip()}
    results = {}
    if RESULTS.exists():
        results = {json.loads(l)["scenario_id"]: json.loads(l) for l in RESULTS.read_text().splitlines() if l.strip()}

    # Group turns into conversations. session_id is the handout's grouping key;
    # scenario_id is the fallback for any trace recorded without one.
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        attrs = attributes(trace)
        key = attrs.get("cartwheel.session_id") or attrs.get("cartwheel.scenario_id") or trace["id"]
        groups[key].append(trace)

    prepared = []
    for key, group in groups.items():
        group.sort(key=lambda t: t.get("timestamp") or "")
        attrs = attributes(group[0])
        turns = []
        for trace in group:
            user, steps, reply = steps_for_turn(trace)
            turns.append({"trace_id": trace["id"], "timestamp": trace.get("timestamp"),
                          "user": user, "steps": steps, "reply": reply})
        prepared.append({"key": key, "attrs": attrs, "turns": turns,
                         "cost": sum(t.get("totalCost") or 0 for t in group)})

    tool_counts = [sum(len([s for s in t["steps"] if s["kind"] == "tool"]) for t in c["turns"]) for c in prepared]
    cutoff = statistics.quantiles(tool_counts, n=10)[-1] if len(tool_counts) > 10 else max(tool_counts or [0])

    conversations = []
    for record in prepared:
        attrs, turns = record["attrs"], record["turns"]
        scenario_id = attrs.get("cartwheel.scenario_id")
        scenario = scenarios.get(scenario_id) or {}
        tuple_ = scenario.get("tuple") or {}
        tools_used = [s["name"] for t in turns for s in t["steps"] if s["kind"] == "tool"]
        steps_flat = [s for t in turns for s in t["steps"]]
        last_reply = turns[-1]["reply"] if turns else ""
        conversations.append({
            "id": scenario_id or record["key"],
            "session_id": attrs.get("cartwheel.session_id"),
            "scenario_id": scenario_id,
            "trace_ids": [t["trace_id"] for t in turns],
            "permalink": f"http://localhost:3100/project/cartwheel-dev/traces/{turns[0]['trace_id']}" if turns else None,
            "meta": {
                "role": attrs.get("cartwheel.user_role"),
                "user_id": attrs.get("cartwheel.user_id"),
                "prompt_version": attrs.get("cartwheel.prompt_version"),
                "timestamp": turns[0]["timestamp"] if turns else None,
                "cost": round(record["cost"], 6),
            },
            "case": {  # the HW3 tuple: what kind of case this is
                "group": scenario.get("scenario_group"),
                "intent": tuple_.get("intent"),
                "difficulty": tuple_.get("difficulty"),
                "policy": tuple_.get("applicable_policy"),
                "record_state": tuple_.get("record_state"),
                "style": tuple_.get("user_style"),
                "reference": tuple_.get("record_reference"),
                "data_quality_case": scenario.get("data_quality_case_id"),
            },
            "expected": scenario.get("expected"),  # hidden by default in the UI
            "run_status": (results.get(scenario_id) or {}).get("status"),
            "features": {
                "turn_count": len(turns),
                "tool_call_count": len(tools_used),
                "distinct_tools": len(set(tools_used)),
                "tools": sorted(set(tools_used)),
                "reply_chars": len(last_reply),
            },
            "flags": flags_for(steps_flat, last_reply, len(tools_used), cutoff),
            "turns": turns,
        })

    conversations.sort(key=lambda c: c["id"])
    return conversations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(EXPORT),
                        help="'langfuse' or a Module 1 export path (default: the HW3 export)")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    traces = from_langfuse() if args.source == "langfuse" else from_export(Path(args.source))
    conversations = build(traces)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(conversations, indent=1))

    turns = sum(len(c["turns"]) for c in conversations)
    flagged = sum(1 for c in conversations if c["flags"])
    print(f"{len(conversations)} conversations ({turns} traces) -> {args.out.relative_to(ROOT)}")
    print(f"{flagged} carry at least one flag; "
          f"{sum(1 for c in conversations if not c['case']['intent'])} have no scenario metadata")


if __name__ == "__main__":
    main()
