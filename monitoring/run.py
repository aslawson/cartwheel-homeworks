"""Run the HW7 monitor for one comparison period or a recent time window.

Usage:
    uv run python -m monitoring.run --period before
    uv run python -m monitoring.run --period after
    uv run python -m monitoring.run --last-hours 24

Add ``--plan-only`` to fetch, validate and sample without calling the judge,
and ``--judge JUDGE_ID`` to run a different frozen judge than the one in
``monitoring/config.json`` (its results are kept under its own name).

Steps: fetch traces from Langfuse, build one conversation record per scenario
(or per session for ``--last-hours``), sample, judge the union once, correct
the random-sample rate, write scores to Langfuse, and record the period in
``monitoring/history.jsonl``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from analysis.helpers.normalization import normalize_trace
from analysis.run_judges import _tool_call_text
from monitoring.correct import corrected_mode_prevalence
from monitoring.run_judges import judge_sample, judge_test_data, load_monitoring_judge
from monitoring.sample import DEFAULT_RISK_GROUPS, select_traces
from monitoring.write_scores import build_score_records, post_scores

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "monitoring" / "config.json"
HISTORY_PATH = ROOT / "monitoring" / "history.jsonl"
OUTPUT_DIR = ROOT / "monitoring" / "output"
SCENARIOS_PATH = ROOT / "scenarios" / "monitoring_scenarios.jsonl"


def _parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _same_model(observed: str, configured: str) -> bool:
    """Langfuse records "anthropic/claude-opus-4-6" for "claude-opus-4-6"."""
    return observed == configured or observed.rsplit("/", 1)[-1] == configured


def fetch_window(start: dt.datetime, end: dt.datetime) -> list[dict[str, Any]]:
    """Every Langfuse trace in [start, end], normalized, oldest first."""
    from analysis.helpers.langfuse_io import _client

    client = _client()
    ids: list[str] = []
    page = 1
    while True:
        response = client.api.trace.list(
            from_timestamp=start, to_timestamp=end, page=page, limit=100
        )
        ids.extend(str(trace.id) for trace in response.data)
        if len(response.data) < 100:
            break
        page += 1
    traces = [normalize_trace(client.api.trace.get(trace_id)) for trace_id in ids]
    traces.sort(key=lambda trace: trace.get("timestamp") or "")
    return traces


def _judge_messages(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """The message shape used for the HW5 judge inputs (no labels or metadata)."""
    messages: list[dict[str, Any]] = []
    for message in trace["trace"]:
        role = message.get("role")
        if role == "tool_call":
            messages.append({
                "role": role,
                "name": message.get("name"),
                "arguments": _tool_call_text(message),
            })
        elif role == "tool_result":
            messages.append({
                "role": role,
                "name": message.get("name"),
                "content": message.get("content", message.get("text", "")),
            })
        elif role in ("user", "assistant"):
            messages.append({"role": role, "text": message.get("text", "")})
    return messages


def build_conversations(
    traces: list[dict[str, Any]], group_key: str
) -> list[dict[str, Any]]:
    """One record per conversation, keyed by the final trace id.

    Each record carries the normalized judge text, the tool names and the
    number of user turns observed in its traces, so risk groups come from
    trace evidence rather than from the agent's reply.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        key = trace["meta"].get(group_key)
        if key:
            groups[key].append(trace)

    conversations = []
    for key, group in groups.items():
        group.sort(key=lambda trace: trace.get("timestamp") or "")
        messages = [m for trace in group for m in _judge_messages(trace)]
        final_id = group[-1]["trace_id"]
        conversations.append({
            "id": final_id,
            "conversation": key,
            "trace_ids": [trace["trace_id"] for trace in group],
            "text": normalize_trace({"id": final_id, "trace": messages})["text"],
            "tools": sorted({m["name"] for m in messages if m["role"] == "tool_call" and m.get("name")}),
            "turn_count": sum(m["role"] == "user" for m in messages),
            "models": sorted({model for trace in group for model in trace["models"]}),
        })
    conversations.sort(key=lambda record: record["conversation"])
    return conversations


def validate_period(
    traces: list[dict[str, Any]], scenario_ids: list[str], model: str
) -> list[dict[str, Any]]:
    """Keep the monitored scenarios; reject a missing id, a retry or a model mismatch."""
    wanted = set(scenario_ids)
    eligible = [t for t in traces if t["meta"].get("scenario_id") in wanted]
    found = {t["meta"]["scenario_id"] for t in eligible}
    missing = sorted(wanted - found)
    if missing:
        raise SystemExit(f"period rejected: {len(missing)} scenario ids missing, e.g. {missing[:5]}")
    sessions: dict[str, set[str]] = defaultdict(set)
    for trace in eligible:
        sessions[trace["meta"]["scenario_id"]].add(str(trace["meta"].get("session_id")))
    retried = sorted(sid for sid, ids in sessions.items() if len(ids) > 1)
    if retried:
        raise SystemExit(f"period rejected: separate retries combined for {retried[:5]}")
    wrong = sorted({
        m for t in eligible for m in t["models"] if not _same_model(m, model)
    })
    if wrong:
        raise SystemExit(f"period rejected: traces use {wrong}, expected {model}")
    if any(not t["models"] for t in eligible):
        raise SystemExit("period rejected: a trace records no model")
    return eligible


def _write_history(record: dict[str, Any]) -> None:
    """Add the record, replacing an earlier run of the same period and judge."""
    rows = []
    if HISTORY_PATH.exists():
        rows = [json.loads(line) for line in HISTORY_PATH.read_text().splitlines() if line.strip()]
    rows = [
        row for row in rows
        if (row["period"], row["judge_id"]) != (record["period"], record["judge_id"])
    ]
    rows.append(record)
    HISTORY_PATH.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _chart(config: dict[str, Any], judge_id: str, mode: str) -> Path | None:
    from monitoring.chart import prevalence_chart

    order = [period["label"] for period in config["periods"]]
    rows = [
        json.loads(line) for line in HISTORY_PATH.read_text().splitlines() if line.strip()
    ]
    points = [
        {"label": r["period"], "corrected": r["corrected"], "ci_low": r["ci_low"], "ci_high": r["ci_high"]}
        for r in sorted(
            (r for r in rows if r["judge_id"] == judge_id and r["period"] in order),
            key=lambda r: order.index(r["period"]),
        )
    ]
    if not points:
        return None
    name = "prevalence.svg" if judge_id == config["judge_id"] else f"prevalence-{mode}.svg"
    path = ROOT / "monitoring" / name
    path.write_text(prevalence_chart(points, threshold=config["threshold"], mode=mode))
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    when = parser.add_mutually_exclusive_group(required=True)
    when.add_argument("--period", help="a period label from monitoring/config.json")
    when.add_argument("--last-hours", type=float, help="monitor the most recent N hours")
    parser.add_argument("--judge", help="override the frozen judge id in the config")
    parser.add_argument("--plan-only", action="store_true", help="stop before calling the judge")
    parser.add_argument("--no-scores", action="store_true", help="do not write scores to Langfuse")
    args = parser.parse_args()

    from observability.instrument import load_env

    load_env()
    config = json.loads(CONFIG_PATH.read_text())
    judge_id = args.judge or config["judge_id"]
    judge = load_monitoring_judge(judge_id)
    mode = judge["mode"]
    model = config["model"]

    if args.period:
        period = next((p for p in config["periods"] if p["label"] == args.period), None)
        if period is None:
            raise SystemExit(f"no period {args.period!r} in {CONFIG_PATH.name}")
        label = period["label"]
        start, end = _parse_time(period["from"]), _parse_time(period["to"])
        traces = fetch_window(start, end)
        scenario_ids = [json.loads(line)["id"] for line in SCENARIOS_PATH.read_text().splitlines() if line.strip()]
        eligible = validate_period(traces, scenario_ids, model)
        conversations = build_conversations(eligible, "scenario_id")
        if len(conversations) != len(scenario_ids):
            raise SystemExit(f"expected {len(scenario_ids)} conversations, built {len(conversations)}")
    else:
        end = dt.datetime.now(dt.timezone.utc)
        start = end - dt.timedelta(hours=args.last_hours)
        label = f"last-{args.last_hours:g}h-{end:%Y-%m-%dT%H}"
        eligible = fetch_window(start, end)
        conversations = build_conversations(eligible, "session_id")

    base = {
        "period": label,
        "judge_id": judge_id,
        "mode": mode,
        "model": model,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "langfuse_traces": len(eligible),
        "conversations": len(conversations),
    }

    if not conversations:
        record = {**base, "random_sample": 0, "risk_sample": 0, "judge_calls": 0}
        print(json.dumps(record, indent=2))
        print("No eligible conversations in this window; nothing to judge.")
        if args.last_hours is not None:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUTPUT_DIR / f"{label}.json").write_text(json.dumps(record, indent=2) + "\n")
        return

    groups = {name: DEFAULT_RISK_GROUPS[name] for name in config["risk_groups"]}
    plan = select_traces(conversations, config["random_rate"], groups, seed=config.get("seed", 7))
    risk_ids = list(dict.fromkeys(t["id"] for group in plan["risk_groups"].values() for t in group))
    print(f"{label}: {len(eligible)} Langfuse traces, {len(conversations)} conversations")
    print(f"  random sample: {len(plan['random'])}")
    for name, group in plan["risk_groups"].items():
        print(f"  risk group {name}: {len(group)}")
    print(f"  judge calls: {len(plan['to_judge'])} on {judge['model']} ({judge_id})")
    if args.plan_only:
        return

    verdicts = judge_sample(judge_id, plan["to_judge"])
    random_verdicts = {t["id"]: verdicts[t["id"]] for t in plan["random"]}
    risk_verdicts = {trace_id: verdicts[trace_id] for trace_id in risk_ids}

    test_labels, test_preds = judge_test_data(judge_id)
    estimate = corrected_mode_prevalence(list(random_verdicts.values()), test_labels, test_preds)

    by_id = {c["id"]: c for c in conversations}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        **base,
        "estimate": estimate,
        "random": [{"id": i, "conversation": by_id[i]["conversation"], "verdict": v} for i, v in random_verdicts.items()],
        "risk_groups": {
            name: [{"id": t["id"], "conversation": t["conversation"], "verdict": verdicts[t["id"]]} for t in group]
            for name, group in plan["risk_groups"].items()
        },
    }
    out_path = OUTPUT_DIR / f"{mode}-{label}.json"
    out_path.write_text(json.dumps(output, indent=2) + "\n")

    if not args.no_scores:
        records = build_score_records(mode, random_verdicts, risk_verdicts, estimate, label)
        # Langfuse rejects a score attached to nothing, so the period's
        # prevalence hangs on the period's last trace. Its name and stable
        # score_id are unchanged.
        anchor = max(eligible, key=lambda t: t.get("timestamp") or "")["trace_id"]
        for record in records:
            if record["trace_id"] is None:
                record["trace_id"] = anchor
        written = post_scores(records)
        print(f"  wrote {written} Langfuse scores")

    risk_flagged = sum(risk_verdicts.values())
    record = {
        **base,
        "random_sample": len(random_verdicts),
        "risk_sample": len(risk_verdicts),
        "judge_calls": len(plan["to_judge"]),
        "random_flagged": sum(random_verdicts.values()),
        "risk_flagged": risk_flagged,
        "raw": estimate["raw"],
        "corrected": estimate["corrected"],
        "ci_low": estimate["ci_low"],
        "ci_high": estimate["ci_high"],
        "confidence": estimate["confidence"],
        "failure_sensitivity": estimate["failure_sensitivity"],
        "pass_specificity": estimate["pass_specificity"],
        "threshold": config["threshold"],
    }
    print(json.dumps(record, indent=2))
    if args.period:
        _write_history(record)
        chart = _chart(config, judge_id, mode)
        if chart:
            print(f"  chart: {chart.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
