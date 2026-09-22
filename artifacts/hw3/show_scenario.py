"""Print one scenario for human review: plan, expected result, tool calls, replies.

Reads the scenario file, the runner results, and the scenario's traces from the
local Langfuse ClickHouse (read-only SELECTs). Traces without an output (for
example a failed earlier attempt) are skipped and counted.

    uv run python artifacts/hw3/show_scenario.py pilot-006
    uv run python artifacts/hw3/show_scenario.py support-0042 \
        --scenarios scenarios/support_scenarios.jsonl --results scenarios/final-results.jsonl
"""

from __future__ import annotations

import argparse
import json
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANGFUSE = "http://localhost:3100/project/cartwheel-dev/traces"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "observability" / "docker-compose.yml")]


def clickhouse(query: str) -> list[dict]:
    out = subprocess.run(
        [*COMPOSE, "exec", "-T", "clickhouse", "clickhouse-client", "--user", "clickhouse",
         "--password", "clickhouse", "--database", "default", "--query",
         query + " FORMAT JSONEachRow"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def find(path: Path, key: str, value: str) -> dict:
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record.get(key) == value:
            return record
    raise SystemExit(f"{value} not found in {path}")


def short(value, width: int = 400) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    try:  # tool inputs/outputs are often JSON strings
        text = json.dumps(json.loads(text))
    except (TypeError, ValueError):
        pass
    return text if len(text) <= width else text[:width] + " …"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario_id")
    parser.add_argument("--scenarios", type=Path, default=ROOT / "scenarios" / "pilot_scenarios.jsonl")
    parser.add_argument("--results", type=Path, default=ROOT / "scenarios" / "pilot-results.jsonl")
    args = parser.parse_args()
    sid = args.scenario_id.replace("'", "")

    s = find(args.scenarios, "id", sid)
    r = find(args.results, "scenario_id", sid)
    t, e = s["tuple"], s["expected"]
    print(f"=== {sid}  [{s['scenario_group']}]  {t['role']} user {t['user_id']}  "
          f"intent={t['intent']}  difficulty={t['difficulty']}  style={t['user_style']}")
    if s.get("data_quality_case_id"):
        print(f"damaged record: {s['data_quality_case_id']}")
    print("\n--- EXPECTED")
    if e["evaluation"] == "objective":
        print(f"outcome: {e['outcome']}")
        print(textwrap.fill(f"reason: {e['reason']}", 100, subsequent_indent="        "))
    else:
        print(textwrap.fill(f"criterion: {e['criterion']}", 100, subsequent_indent="           "))
    print(f"source: {e['source']['type']} / {e['source']['reference']}")

    traces = clickhouse(
        "SELECT id, argMax(timestamp, event_ts) AS ts, argMax(output, event_ts) AS output "
        "FROM traces WHERE id IN (SELECT id FROM traces WHERE "
        f"JSONExtractString(metadata['attributes'], 'cartwheel.scenario_id') = '{sid}') "
        "GROUP BY id ORDER BY ts"
    )
    good = [x for x in traces if x["output"]]
    skipped = len(traces) - len(good)
    print(f"\n--- OBSERVED  (runner status: {r['status']}; {len(good)} trace(s)"
          + (f", {skipped} empty trace(s) from an earlier failed attempt skipped" if skipped else "") + ")")

    for turn_no, (turn, trace) in enumerate(zip(r["turns"], good), start=1):
        print(f"\n[turn {turn_no}]  {LANGFUSE}/{trace['id']}")
        print(textwrap.fill(f"USER: {turn['user']}", 100, subsequent_indent="      "))
        tools = clickhouse(
            "SELECT name, argMax(input, event_ts) AS input, argMax(output, event_ts) AS output, "
            "argMax(start_time, event_ts) AS st FROM observations "
            f"WHERE trace_id = '{trace['id']}' AND type = 'TOOL' GROUP BY id, name ORDER BY st"
        )
        if not tools:
            print("TOOLS: (none called)")
        for tool in tools:
            print(f"TOOL {tool['name']}")
            print(f"   in : {short(tool['input'])}")
            print(f"   out: {short(tool['output'])}")
        print("AGENT:")
        print(textwrap.indent(turn["agent"].strip(), "   "))


if __name__ == "__main__":
    main()
