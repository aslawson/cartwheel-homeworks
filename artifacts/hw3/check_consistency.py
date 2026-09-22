"""Flag scenarios whose final messages do not support their expected result.

Pilot lesson (006, 026, 030): a goal of "cancel the order" came out as "can I
cancel?", so the answer key expected an action the user never asked for. This
runs AFTER message generation, so it may see the expected result; it only
flags, it never edits. A human decides what to do with each flag.

    uv run python artifacts/hw3/check_consistency.py scenarios/support_scenarios.jsonl
Writes <scenarios>.consistency.jsonl and prints the flagged ids.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import litellm

from generate_messages import GEN_MODEL, load_env, parse_json

PROMPT = """You are auditing a test case for a customer-support agent before it runs.
The user messages below are fixed. The expected result is what the agent will be graded against.

User role: {role}
User messages, in order:
{messages}

Expected result:
{expected}

Is the expected result a fair target for exactly these messages? Flag it if any of these hold:
1. The expected result assumes the user asked for an action (refund, cancel, escalate) but the
   messages only ask whether it is possible, or the reverse.
2. The messages point to a different item, store, order, or amount than the expected result.
3. A fact in the messages contradicts the expected result in a way the agent could not resolve.
4. A followup no longer makes sense for the expected result.
Do not flag style, tone, typos, or missing details the agent can look up.
Return only JSON: {{"consistent": true or false, "problem": "one sentence, empty if consistent"}}"""


async def check(s: dict, sem: asyncio.Semaphore) -> dict:
    e = s["expected"]
    expected = (f"{e['outcome']}: {e['reason']}" if e["evaluation"] == "objective" else f"criterion: {e['criterion']}")
    messages = "\n".join(f"{i + 1}. {m}" for i, m in enumerate([s["opening_message"], *s["followups"]]))
    async with sem:
        for _ in range(3):
            try:
                r = await litellm.acompletion(model=GEN_MODEL, max_tokens=400, messages=[{
                    "role": "user", "content": PROMPT.format(role=s["tuple"]["role"], messages=messages, expected=expected)}])
                out = parse_json(r.choices[0].message.content or "")
                return {"id": s["id"], "consistent": bool(out["consistent"]), "problem": out.get("problem", "")}
            except Exception as exc:  # retry provider and parse errors
                last = exc
                await asyncio.sleep(2)
    return {"id": s["id"], "consistent": None, "problem": f"check failed: {last}"}


async def main_async(path: Path) -> None:
    scenarios = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    sem = asyncio.Semaphore(8)
    results = await asyncio.gather(*(check(s, sem) for s in scenarios))
    out = path.with_suffix(".consistency.jsonl")
    out.write_text("".join(json.dumps(r) + "\n" for r in results))
    flagged = [r for r in results if r["consistent"] is not True]
    print(f"checked {len(results)}; flagged {len(flagged)}; details in {out}")
    for r in flagged:
        print(f"  {r['id']}: {r['problem']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenarios", type=Path)
    args = parser.parse_args()
    load_env()
    litellm.suppress_debug_info = True
    asyncio.run(main_async(args.scenarios))


if __name__ == "__main__":
    main()
