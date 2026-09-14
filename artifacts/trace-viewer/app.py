"""A local, read-only trace viewer for the Cartwheel support agent.

Reads traces from the Langfuse API on localhost and serves a plain HTML page
for reading them. Nothing here writes: no annotations, no scores, no database,
no state beyond the process itself.

Run it (the folder name has a hyphen, so point uvicorn at the directory):
    uv run uvicorn app:app --app-dir artifacts/trace-viewer --port 8020

Then open http://localhost:8020

Credentials come from .env (LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY,
LANGFUSE_SECRET_KEY) - the same values the agent server uses to write traces.

What it reconstructs, and why it has to:

  * The final assistant reply lives on the root span's output, because the
    server records it there deliberately (Homework 2, Part C).
  * Model observations come back with output: null. The assistant's own words
    for a given turn appear inside the *next* model call's input, since that
    call is shown the conversation so far. So the richest single source for a
    conversation is the last model call's input array, with the root span's
    output appended as the final assistant turn.
  * Tool observations carry the arguments, the result, and the cartwheel.*
    attributes. They are matched back to the tool_call ids in the messages so
    a call and its result read as one thing.

Anything unrecognised is preserved and shown in the raw view rather than
dropped.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
MAX_TRACES = 100
LIST_FETCH_WORKERS = 8


def _load_env() -> None:
    """Load .env without requiring python-dotenv. Existing vars win."""
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value:
            os.environ.setdefault(key, value)


_load_env()

LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "http://localhost:3000").rstrip("/")
PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")


def _get(path: str) -> Any:
    """One read-only GET against the Langfuse API."""
    if not PUBLIC_KEY or not SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not set. "
                "They live in .env, alongside LANGFUSE_HOST."
            ),
        )
    token = base64.b64encode(f"{PUBLIC_KEY}:{SECRET_KEY}".encode()).decode()
    request = urllib.request.Request(
        f"{LANGFUSE_HOST}{path}",
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise HTTPException(
            status_code=exc.code,
            detail=f"Langfuse returned {exc.code} for {path}",
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Cannot reach Langfuse at {LANGFUSE_HOST} ({exc.reason}). "
                "Start it with: docker compose -f observability/docker-compose.yml up -d"
            ),
        ) from exc


# ---------------------------------------------------------------------------
# Normalising. Every helper below tolerates a missing field and says so,
# rather than inventing a value.
# ---------------------------------------------------------------------------


def _attrs(obj: dict[str, Any] | None) -> dict[str, Any]:
    if not obj:
        return {}
    metadata = obj.get("metadata")
    if isinstance(metadata, dict):
        attributes = metadata.get("attributes")
        if isinstance(attributes, dict):
            return attributes
        return metadata
    return {}


def _as_messages(value: Any) -> list[dict[str, Any]] | None:
    """Coerce a recorded message payload into a list of messages, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [{"role": "unknown", "parts": [{"type": "text", "content": value}]}]
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, list):
        return [m for m in value if isinstance(m, dict)]
    return None


def _tool_failure(output: Any) -> str | None:
    """The structured error code a Cartwheel tool returns, if it failed."""
    payload = output
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, dict) and payload.get("ok") is False:
        return str(payload.get("error") or "error")
    return None


def _observation_flags(observation: dict[str, Any]) -> list[dict[str, str]]:
    """Anything worth catching the eye on a scan."""
    flags: list[dict[str, str]] = []
    attributes = _attrs(observation)

    if attributes.get("cartwheel.permission_denied") in (True, "true", "True"):
        flags.append(
            {
                "kind": "denied",
                "label": "permission denied",
                "detail": str(attributes.get("cartwheel.permission_denied.reason") or ""),
            }
        )

    error = _tool_failure(observation.get("output"))
    if error and error != "permission_denied":
        flags.append({"kind": "tool-error", "label": error, "detail": ""})

    level = observation.get("level")
    if level and level not in ("DEFAULT", "DEBUG"):
        flags.append(
            {
                "kind": "level",
                "label": str(level).lower(),
                "detail": str(observation.get("statusMessage") or ""),
            }
        )
    elif observation.get("statusMessage"):
        flags.append(
            {
                "kind": "status",
                "label": "status message",
                "detail": str(observation["statusMessage"]),
            }
        )
    return flags


def _summarise(trace: dict[str, Any], observations: list[dict[str, Any]] | None) -> dict[str, Any]:
    attributes = _attrs(trace)
    observations = observations or []
    ordered = sorted(observations, key=lambda o: (o.get("startTime") or ""))

    tools = [o for o in ordered if (o.get("type") == "TOOL") or _attrs(o).get("gen_ai.operation.name") == "execute_tool"]
    models = [o for o in ordered if o.get("type") == "GENERATION"]

    flags: list[dict[str, str]] = []
    for observation in ordered:
        for flag in _observation_flags(observation):
            flag = dict(flag)
            flag["where"] = observation.get("name") or observation.get("type") or "?"
            flags.append(flag)

    if trace.get("output") is None:
        flags.append({"kind": "missing", "label": "no final reply recorded", "detail": "", "where": "trace"})

    return {
        "id": trace.get("id"),
        "name": trace.get("name"),
        "timestamp": trace.get("timestamp"),
        "latency": trace.get("latency"),
        "user_role": attributes.get("cartwheel.user_role"),
        "user_id": attributes.get("cartwheel.user_id"),
        "prompt_version": attributes.get("cartwheel.prompt_version"),
        "session_id": attributes.get("cartwheel.session_id"),
        "scenario_id": attributes.get("cartwheel.scenario_id"),
        "tool_order": [(_attrs(o).get("gen_ai.tool.name") or o.get("name")) for o in tools],
        "model": next((o.get("model") for o in models if o.get("model")), None),
        "model_calls": len(models),
        "observation_count": len(ordered),
        "flags": flags,
        "question": _first_text(_as_messages(trace.get("input"))),
        "answer": _first_text(_as_messages(trace.get("output"))),
    }


def _first_text(messages: list[dict[str, Any]] | None) -> str | None:
    if not messages:
        return None
    for message in messages:
        for part in message.get("parts") or []:
            if isinstance(part, dict) and part.get("type") == "text" and part.get("content"):
                return str(part["content"])
        if isinstance(message.get("content"), str):
            return message["content"]
    return None


def _conversation(trace: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild the turn as messages, with tool calls matched to their results.

    The last model call's input carries the whole conversation as the model saw
    it. The final assistant reply is not in there - it is that call's answer -
    so it comes from the root span's output.
    """
    ordered = sorted(observations, key=lambda o: (o.get("startTime") or ""))
    generations = [o for o in ordered if o.get("type") == "GENERATION"]

    messages: list[dict[str, Any]] = []
    if generations:
        messages = _as_messages(generations[-1].get("input")) or []
    if not messages:
        messages = _as_messages(trace.get("input")) or []

    final = _as_messages(trace.get("output")) or []
    messages = list(messages) + list(final)

    tools_by_id: dict[str, dict[str, Any]] = {}
    tools_unmatched: list[dict[str, Any]] = []
    for observation in ordered:
        if observation.get("type") != "TOOL" and _attrs(observation).get("gen_ai.operation.name") != "execute_tool":
            continue
        record = {
            "observation_id": observation.get("id"),
            "name": _attrs(observation).get("gen_ai.tool.name") or observation.get("name"),
            "arguments": observation.get("input"),
            "result": observation.get("output"),
            "start": observation.get("startTime"),
            "end": observation.get("endTime"),
            "latency": observation.get("latency"),
            "attributes": _attrs(observation),
            "flags": _observation_flags(observation),
            "error": _tool_failure(observation.get("output")),
        }
        call_id = _attrs(observation).get("gen_ai.tool.call.id")
        if call_id:
            tools_by_id[str(call_id)] = record
        else:
            tools_unmatched.append(record)

    used: set[str] = set()
    rendered: list[dict[str, Any]] = []
    for message in messages:
        entry = {
            "role": message.get("role") or "unknown",
            "parts": [],
            "raw": message,
        }
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                entry["parts"].append({"type": "unknown", "value": part})
                continue
            kind = part.get("type")
            if kind == "tool_call":
                call_id = str(part.get("id") or "")
                record = tools_by_id.get(call_id)
                if record is None and tools_unmatched:
                    record = tools_unmatched.pop(0)
                if record is not None:
                    used.add(record.get("observation_id") or "")
                entry["parts"].append(
                    {
                        "type": "tool_call",
                        "call_id": part.get("id"),
                        "name": part.get("name"),
                        "arguments": part.get("arguments"),
                        "observation": record,
                    }
                )
            else:
                entry["parts"].append({"type": kind or "unknown", **part})
        rendered.append(entry)

    leftovers = [
        record
        for record in list(tools_by_id.values()) + tools_unmatched
        if (record.get("observation_id") or "") not in used
    ]
    for record in leftovers:
        rendered.append(
            {
                "role": "tool",
                "parts": [{"type": "tool_call", "call_id": None, "name": record["name"],
                           "arguments": record["arguments"], "observation": record}],
                "raw": {"note": "tool call recorded but not matched to a message"},
            }
        )
    return rendered


# ---------------------------------------------------------------------------
# Routes. Read-only, all of them.
# ---------------------------------------------------------------------------

app = FastAPI(title="Cartwheel trace viewer (read-only)")


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {
        "langfuse_host": LANGFUSE_HOST,
        "credentials_present": bool(PUBLIC_KEY and SECRET_KEY),
    }


@app.get("/api/traces")
def list_traces(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_TRACES))
    listing = _get(f"/api/public/traces?limit={limit}&page=1")
    traces = listing.get("data") or []

    def with_detail(trace: dict[str, Any]) -> dict[str, Any]:
        try:
            detail = _get(f"/api/public/traces/{trace['id']}")
            return _summarise(detail, detail.get("observations") or [])
        except HTTPException:
            return _summarise(trace, None)

    with ThreadPoolExecutor(max_workers=LIST_FETCH_WORKERS) as pool:
        summaries = list(pool.map(with_detail, traces))

    return {"traces": summaries, "count": len(summaries), "host": LANGFUSE_HOST}


@app.get("/api/traces/{trace_id}")
def get_trace(trace_id: str) -> dict[str, Any]:
    detail = _get(f"/api/public/traces/{trace_id}")
    observations = detail.get("observations") or []
    ordered = sorted(observations, key=lambda o: (o.get("startTime") or ""))
    return {
        "summary": _summarise(detail, ordered),
        "conversation": _conversation(detail, ordered),
        "observations": ordered,
        "trace": {k: v for k, v in detail.items() if k != "observations"},
    }


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")
