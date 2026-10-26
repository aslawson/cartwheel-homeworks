"""HW5: build and evaluate one LLM judge for scope_handling_overreach.

Run from the repository root:

    uv run python -m analysis.run_judges export-labels   # HW4 labels -> hw5_labels (1 = Pass)
    uv run python -m analysis.run_judges prepare         # judge inputs -> hw5_trace_inputs.json
    uv run python -m analysis.run_judges split           # once: train/dev/test -> splits.json
    uv run python -m analysis.run_judges dev analysis/prompts/scope_handling_overreach-v0.txt
    uv run python -m analysis.run_judges test <judge_id> # once, after choosing the final prompt

Label conventions: the HW4 file (analysis/state/labels/<mode>.jsonl) uses
1 = failure present. The HW5 export flips it to 1 = Pass, 0 = Fail, and the
HW4 file is left unchanged.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MODE = "scope_handling_overreach"
TRACE_EXPORT = ROOT / "traces" / "support_traces.json"
STATE = ROOT / "analysis" / "state"
HW4_LABELS = STATE / "labels" / f"{MODE}.jsonl"
HW5_LABELS = STATE / "hw5_labels" / f"{MODE}.jsonl"
INPUTS = STATE / "hw5_trace_inputs.json"
REPORT = ROOT / "analysis" / "report"
# The handout specifies gpt-4o-mini; this student has only an Anthropic key, so
# the judge uses Claude Haiku 4.5 for both development and the final test.
JUDGE_MODEL = "claude-haiku-4-5-20251001"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _live_hw4_labels(mode: str) -> dict[str, dict[str, Any]]:
    """Current HW4 label per scenario: drop superseded rows, last write wins."""
    live: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(STATE / "labels" / f"{mode}.jsonl"):
        if not row.get("superseded_by"):
            live[row["trace_id"]] = row
    return live


def export_hw5_labels(mode: str = MODE) -> dict[str, int]:
    """Write the HW5 label file with 1 = Pass and 0 = Fail."""
    rows = []
    for trace_id, row in sorted(_live_hw4_labels(mode).items()):
        failure_present = int(str(row["label"]))
        rows.append({
            "trace_id": trace_id,
            "mode": mode,
            "label": 1 - failure_present,
            "evidence": row.get("evidence", ""),
            "source": row.get("source", ""),
            "ts": row.get("ts", ""),
            "hw4_label_id": row.get("id", ""),
        })
    path = STATE / "hw5_labels" / f"{mode}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    counts = collections.Counter("pass" if r["label"] == 1 else "fail" for r in rows)
    return {"pass": counts["pass"], "fail": counts["fail"], "total": len(rows)}


def _tool_call_text(message: dict[str, Any]) -> str:
    return f"{message.get('name', 'tool')}({message.get('arguments', '')})"


def prepare_inputs(mode: str = MODE) -> list[dict[str, Any]]:
    """Save one judge input per labelled conversation to hw5_trace_inputs.json.

    Each scenario is one conversation; multi-turn scenarios span one exported
    trace per turn, joined in time order. Records carry only the messages
    (user turns, assistant text, tool calls and results, which hold the policy
    passages). Labels, review notes, and scenario metadata are left out so the
    judge cannot see the answer.
    """
    from analysis.helpers.normalization import normalize_trace

    data = json.loads(TRACE_EXPORT.read_text())
    traces = data.get("traces", data) if isinstance(data, dict) else data
    turns: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for trace in traces:
        scenario = trace.get("cartwheel_scenario_id")
        if scenario:
            turns[scenario].append(trace)

    labelled = sorted(_live_hw4_labels(mode))
    missing = [s for s in labelled if s not in turns]
    if missing:
        raise ValueError(f"labelled scenarios missing from the trace export: {missing[:5]}")

    records = []
    for scenario in labelled:
        messages: list[dict[str, Any]] = []
        for trace in sorted(turns[scenario], key=lambda t: t["createdAt"]):
            for message in normalize_trace(trace)["trace"]:
                role = message.get("role")
                if role == "tool_call":
                    messages.append({"role": "tool_call", "name": message.get("name"),
                                     "arguments": _tool_call_text(message)})
                elif role == "tool_result":
                    messages.append({"role": "tool_result", "name": message.get("name"),
                                     "content": message.get("content", message.get("text", ""))})
                elif role in ("user", "assistant"):
                    messages.append({"role": role, "text": message.get("text", "")})
        records.append({"trace_id": scenario, "trace": messages})

    INPUTS.write_text(json.dumps(records, indent=1, ensure_ascii=False))
    return records


def split_data(mode: str = MODE) -> dict[str, list[str]]:
    """Split the HW5 labels 20/40/40. Run once; splits.json then stays fixed."""
    from analysis.helpers import split_labels

    records = json.loads(INPUTS.read_text())
    return split_labels(
        mode,
        fractions=(0.20, 0.40, 0.40),
        seed=7,
        min_per_class=10,
        eligible_trace_ids=[record["trace_id"] for record in records],
    )


def _use_saved_inputs() -> None:
    # Judge the saved export, never live Langfuse traces.
    os.environ["CARTWHEEL_JUDGE_TRACE_SOURCE"] = str(INPUTS)


def run_development(mode: str, prompt_path: str) -> dict[str, Any]:
    """Register a prompt version, judge the dev split, and save its metrics."""
    from analysis.helpers import judge_alignment, register_judge, run_judge

    _use_saved_inputs()
    record = register_judge(mode=mode, prompt_text=Path(prompt_path).read_text(),
                            judge_model=JUDGE_MODEL)
    judge_id = record["judge_id"]
    run_judge(judge_id, split="dev", batch_size=10)
    metrics = {"judge_id": judge_id, "prompt_path": prompt_path, "model": JUDGE_MODEL,
               **judge_alignment(judge_id, split="dev")}
    (REPORT / f"dev-{judge_id}.json").write_text(json.dumps(metrics, indent=2))
    return metrics


def run_test(judge_id: str) -> dict[str, Any]:
    """Freeze the chosen judge, judge the test split once, and save its metrics."""
    from analysis.helpers import freeze_judge, judge_alignment, run_judge

    _use_saved_inputs()
    freeze_judge(judge_id)
    run_judge(judge_id, split="test", batch_size=10)
    metrics = {"judge_id": judge_id, "model": JUDGE_MODEL,
               **judge_alignment(judge_id, split="test")}
    (REPORT / f"test-{judge_id}.json").write_text(json.dumps(metrics, indent=2))
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export-labels")
    sub.add_parser("prepare")
    sub.add_parser("split")
    dev = sub.add_parser("dev")
    dev.add_argument("prompt_path")
    test = sub.add_parser("test")
    test.add_argument("judge_id")
    args = parser.parse_args()

    if args.command == "export-labels":
        print(export_hw5_labels())
    elif args.command == "prepare":
        print(f"{len(prepare_inputs())} records -> {INPUTS.relative_to(ROOT)}")
    elif args.command == "split":
        labels = {r["trace_id"]: r["label"] for r in _read_jsonl(HW5_LABELS)}
        for name, ids in split_data().items():
            n_pass = sum(labels[i] == 1 for i in ids)
            print(f"{name}: {n_pass} pass, {len(ids) - n_pass} fail")
    elif args.command == "dev":
        print(json.dumps(run_development(MODE, args.prompt_path), indent=2))
    elif args.command == "test":
        print(json.dumps(run_test(args.judge_id), indent=2))


if __name__ == "__main__":
    main()
