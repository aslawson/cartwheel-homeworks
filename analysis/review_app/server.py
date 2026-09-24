"""Review server for HW4 open coding, axial coding, and structured labeling.

Standard library only, like the reference server in analysis/server.py, and it
keeps that server's file-backed API so the same state files and the same agent
watch loop work unchanged:

    GET  /                    the review app
    GET  /api/samples         current sample set   POST to replace it
    GET  /api/annotations     human notes          POST to save (app posts on every change)
    GET  /api/graph           2D projection for the map view
    GET  /api/patterns        the taxonomy         POST to replace it
    GET  /api/suggestions     agent suggestions    POST to replace them

What this server adds for this assignment:

    GET  /api/conversations   whole conversations, grouped by session, with the
                              HW3 case metadata and per-turn steps (narration,
                              tool call, tool result, final reply)
    GET  /api/labels          every structured label, grouped by mode
    POST /api/labels          one {trace_id, mode, label, evidence} decision:
                              appended to analysis/state/labels/<mode>.jsonl and
                              written to Langfuse as a score named after the mode

Labels go to Langfuse through analysis/helpers/langfuse_io.py, so the score
convention (1 = failure present) matches the rest of the course. Langfuse is
best effort: if it is unreachable the label is still written locally and the
response says so, because losing a human judgment to a network error is worse
than a missing score.

    uv run python analysis/review_app/server.py            # http://localhost:8030
    uv run python analysis/review_app/server.py --port 8031 --no-langfuse
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATE = ROOT / "analysis" / "state"
UI = HERE / "ui"

sys.path.insert(0, str(ROOT))

API_FILES: dict[str, Path] = {
    "/api/samples": STATE / "samples.json",
    "/api/annotations": STATE / "annotations.json",
    "/api/graph": STATE / "graph.json",
    "/api/patterns": STATE / "patterns.json",
    "/api/suggestions": STATE / "suggestions.json",
}
API_DEFAULTS: dict[str, Any] = {
    "/api/samples": [],
    "/api/annotations": {"annotations": []},
    "/api/graph": {"nodes": [], "clusters": []},
    "/api/patterns": {"modes": []},
    "/api/suggestions": [],
}

WRITE_LOCK = threading.Lock()
USE_LANGFUSE = True


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    """Parsed JSON at path, or default when missing or half-written."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Write atomically, so a crash mid-save cannot truncate a state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def label_path(mode: str) -> Path:
    safe = "".join(c for c in mode if c.isalnum() or c in "_-")
    if not safe:
        raise ValueError("mode name must contain letters, digits, _ or -")
    return STATE / "labels" / f"{safe}.jsonl"


def read_labels() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    directory = STATE / "labels"
    if not directory.exists():
        return out
    for path in sorted(directory.glob("*.jsonl")):
        rows = []
        for line in path.read_text().splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        # One live decision per (trace, mode): superseded rows are history.
        live: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.get("superseded_by"):
                continue
            live[row.get("trace_id")] = row
        out[path.stem] = list(live.values())
    return out


def save_label(record: dict[str, Any]) -> dict[str, Any]:
    """Append one decision locally, then mirror it to Langfuse.

    Label files are append-only. A flip appends the new record and marks the
    previous live record with ``superseded_by``, which is the convention
    analysis/helpers/tools.py reads, so the flip history stays inspectable.
    """
    mode = str(record.get("mode") or "").strip()
    trace_id = str(record.get("trace_id") or "").strip()
    if not mode or not trace_id:
        raise ValueError("a label needs trace_id and mode")
    label = 1 if record.get("label") in (1, "1", True, "fail", "present") else 0
    row = {
        "trace_id": trace_id,
        "mode": mode,
        "label": label,
        "evidence": record.get("evidence") or "",
        "source": record.get("source") or "human",
        "ts": utcnow(),
    }
    row["id"] = f"{mode}:{trace_id}:{row['ts']}"
    path = label_path(mode)
    with WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    try:
                        existing.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        for old in existing:  # mark the previous live decision as replaced
            if old.get("trace_id") == trace_id and not old.get("superseded_by"):
                old["superseded_by"] = row["id"]
        existing.append(row)
        path.write_text("".join(json.dumps(r) + "\n" for r in existing))

    row["langfuse"] = "skipped"
    if USE_LANGFUSE:
        langfuse_id = record.get("langfuse_trace_id") or trace_id
        try:
            from analysis.helpers import langfuse_io

            langfuse_io.write_label_score(
                langfuse_id, mode, label, comment=row["evidence"] or None
            )
            row["langfuse"] = "written"
        except Exception as exc:  # unreachable Langfuse must not lose the label
            row["langfuse"] = f"failed: {exc}"
    return row


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
        return

    # -- plumbing ---------------------------------------------------------
    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(json.dumps(data).encode(), "application/json", status)

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        return json.loads(self.rfile.read(length))

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(b"", "text/plain")

    # -- routes -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in API_FILES:
            self._json(read_json(API_FILES[path], API_DEFAULTS[path]))
        elif path == "/api/conversations":
            self._json(read_json(STATE / "conversations.json", []))
        elif path == "/api/labels":
            self._json(read_labels())
        elif path in ("/", "/index.html"):
            self._send((UI / "index.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self._json({"error": "not found", "path": path}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        try:
            payload = self._body()
        except json.JSONDecodeError as exc:
            self._json({"error": f"bad JSON: {exc}"}, 400)
            return
        if path in API_FILES:
            # Keep the committed wrapper shapes: patterns.json is {"modes": [...]}
            # (what analysis/helpers reads) and annotations.json is
            # {"annotations": [...]}. The app posts plain lists.
            if path == "/api/patterns" and isinstance(payload, list):
                payload = {"modes": payload}
            if path == "/api/annotations" and isinstance(payload, list):
                payload = {"annotations": payload}
            with WRITE_LOCK:
                write_json(API_FILES[path], payload)
            self._json({"ok": True, "count": len(payload) if hasattr(payload, "__len__") else None})
        elif path == "/api/labels":
            records = payload if isinstance(payload, list) else [payload]
            try:
                saved = [save_label(record) for record in records]
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json({"ok": True, "saved": saved})
        else:
            self._json({"error": "not found", "path": path}, 404)


def main() -> None:
    # The Langfuse helper reads credentials from the environment, so load .env
    # the same way the agent server does; without this every score write fails.
    try:
        from observability.instrument import load_env
        load_env()
    except Exception as exc:  # a missing .env must not stop local review
        print(f"note: could not load .env ({exc}); labels will be local only")

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8030)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-langfuse", action="store_true",
                        help="write labels locally only (offline review)")
    args = parser.parse_args()

    global USE_LANGFUSE
    USE_LANGFUSE = not args.no_langfuse

    conversations = read_json(STATE / "conversations.json", [])
    if not conversations:
        print("No analysis/state/conversations.json yet. Run:")
        print("  uv run python analysis/review_app/load.py")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"review app on http://{args.host}:{args.port}  "
          f"({len(conversations)} conversations, labels -> "
          f"{'Langfuse + ' if USE_LANGFUSE else ''}analysis/state/labels/)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
