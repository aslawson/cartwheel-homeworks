"""Push the local labels to Langfuse as scores (1 = failure present).

The review server writes a Langfuse score as each label is saved. This is the
repair path for labels written while Langfuse was unreachable or unconfigured,
so the trace store and analysis/state/labels/ agree. Re-running is safe: a
score is keyed by trace and mode, so an existing one is replaced.

    uv run python analysis/review_app/sync_scores.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
STATE = ROOT / "analysis" / "state"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from observability.instrument import load_env
    load_env()
    from analysis.helpers import langfuse_io

    conversations = {c["id"]: c for c in json.loads((STATE / "conversations.json").read_text())}
    # Only this taxonomy's labels on real traces. analysis/state/labels also
    # carries course demo fixtures (unsupported_policy_claim.jsonl, trace ids
    # like upc-fail-tr-00) that have no Langfuse trace behind them.
    modes = {m["name"] for m in json.loads((STATE / "patterns.json").read_text())["modes"]}
    written = failed = skipped = 0
    for path in sorted((STATE / "labels").glob("*.jsonl")):
        if path.stem not in modes:
            skipped += sum(1 for line in path.read_text().splitlines() if line.strip())
            continue
        live: dict[str, dict] = {}
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("superseded_by"):
                live[row["trace_id"]] = row
        for trace_id, row in live.items():
            if trace_id not in conversations:
                skipped += 1
                continue
            langfuse_id = conversations[trace_id]["trace_ids"][0]
            if args.dry_run:
                written += 1
                continue
            try:
                langfuse_io.write_label_score(langfuse_id, row.get("mode", path.stem), row["label"],
                                              comment=row.get("evidence") or None)
                written += 1
            except Exception as exc:
                failed += 1
                if failed <= 3:
                    print(f"  failed {trace_id}/{path.stem}: {exc}")
    print(f"{'would write' if args.dry_run else 'wrote'} {written} score(s); "
          f"{failed} failed; {skipped} skipped (demo fixtures or traces not in this store)")


if __name__ == "__main__":
    main()
