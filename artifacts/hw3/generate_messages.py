"""Write simulated user messages for HW3 plans, then run a critic pass.

Skill Step 5. One independent model call per conversation (bounded pool), then
one independent critic call per conversation. Both calls see only what the
simulated user would know: role, goal, `user_facts`, style, and turn count.
Neither call ever sees `expected`, the tuple's record ids, or the policy.

Run from the repository root:
    uv run python artifacts/hw3/generate_messages.py \
        artifacts/hw3/pilot_plans.jsonl scenarios/pilot_scenarios.jsonl

Regenerate named conversations only, keeping the rest:
    ... --ids pilot-005,pilot-021

Writes the scenario file (plan + messages, without `user_facts`) and a sidecar
<plans>.messages.jsonl with the draft, the critic's issues, and the final text.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import random
import re
from pathlib import Path

import litellm

ROOT = Path(__file__).resolve().parents[2]
GEN_MODEL = "anthropic/claude-sonnet-5"
WORKERS = 8

STYLES = {
    "neutral_conversational": "plain, friendly, ordinary sentences",
    "terse_fragmentary": "very short, fragments, little or no punctuation, no pleasantries",
    "typo_heavy": "hurried typing with several real typos and missing capitals, still understandable",
    "confused_rambling": "unsure of details, wanders a bit, includes an irrelevant aside, gets to the point eventually",
    "frustrated_impatient": "annoyed and in a hurry, blunt, may complain, but not abusive",
    "repetitive_pressuring": "insists, repeats the demand, pushes back on being told no",
    "operational_shorthand": "work shorthand like a busy staff member or seller: clipped, abbreviations such as 'pls', 'ord', 'asap'",
    "requests_short_plain_answer": "explicitly asks for a quick yes/no or short answer",
}

ROLE_CONTEXT = {
    "shopper": "a shopper who buys from independent stores on Cartwheel",
    "merchant": "a merchant who runs a store on Cartwheel",
    "support": "a Cartwheel support staff member using the internal assistant to look into a customer's issue",
}

GEN_PROMPT = """You are simulating a real person typing into the Cartwheel support chat.
Cartwheel is an online marketplace of independent stores.

Who you are: {role_context}
What you want: {goal}
What you know (use ONLY these facts; do not add order numbers, dates, prices, or names that are not listed): {knows}
Writing style: {style_name} - {style_desc}
Number of messages to write: {turns} (one opening message{followup_note})

Rules:
- Write like a real user in a chat window, in the given style. Do not polish the style away.
- Do not mention policies by name, rules, tools, scenarios, tests, or "the agent".
- Leave out facts a real user would not state or know.
{followup_rules}{avoid}
Return only JSON: {{"opening_message": "...", "followups": [...]}} with {n_followups} followup(s)."""

AVOID_RULES = """
- Other simulated users already wrote the openings below. Use a clearly different
  opening, sentence structure, and (if your style has one) a different kind of aside.
  Do not mention pets. {opening}
{examples}"""

# Showing look-alikes is not always enough; a concrete opening move breaks the template.
OPENINGS = [
    "Open with the problem itself, before any greeting or request.",
    "Open with no greeting at all.",
    "Open with the item and store, then get to the request.",
    "Open by saying how long this has been bothering them.",
    "Open with the order number on its own, then the request.",
    "Open with a short complaint, then the request.",
]

FOLLOWUP_RULES = """- Each followup must make sense WITHOUT knowing what support replied. Never write
  "yes go ahead", "thanks", or anything that assumes an offer, an answer, or a question
  from support. Develop the same issue through a correction, added detail, or renewed pushing."""

CRITIC_PROMPT = """You are checking a simulated support-chat conversation before it is used in a test.

The simulated user was defined as:
- Who: {role_context}
- Goal: {goal}
- Knows ONLY: {knows}
- Style: {style_name} - {style_desc}
- Messages: {turns}

Conversation:
{conversation}

Check for these problems:
1. Invented identifiers, amounts, dates, store or product names not in "Knows".
2. A followup that assumes a specific reply from support.
3. Language a real user would not produce (policy ids, rule numbers, internal jargon).
4. References to tools, traces, prompts, tests, or expected outcomes.
5. Style drift from the assigned style, or the goal not coming through.
6. Request type: if the goal says to explicitly ask for (or tell support to do) an action, the message
   must request the action, not only ask whether it is possible. If the goal says it is a question
   ("ask whether", "a question, not a request"), the message must not request the action.

If there are problems, rewrite minimally to fix them, keeping the goal, the facts, the style,
and the same number of messages. If there are none, return the conversation unchanged.
Return only JSON: {{"issues": ["..."], "opening_message": "...", "followups": [...]}}"""


def load_env() -> None:
    """Load .env without printing anything. Existing variables win."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(match.group(0))


# HW3 review (support-0250/0186/0003, support-0032): a single pressuring message
# repeated its demand 3-4 times, and pressuring followups presumed a refusal.
PRESSURE_ONE_TURN = "insistent and pushy, but states the demand once; no repeating the same sentence"
PRESSURE_FOLLOWUPS = ("insistent; each followup renews the demand in a way that makes sense whatever support "
                      "replied (for example 'still need this done today, confirm when it is'); never 'you said no', "
                      "'I don't think you understand', or anything that presumes a refusal")


def style_desc(style: str, turns: int) -> str:
    if style == "repetitive_pressuring":
        return PRESSURE_ONE_TURN if turns == 1 else PRESSURE_FOLLOWUPS
    return STYLES[style]


def user_view(plan: dict) -> dict:
    """The only plan fields a generation or critic call may see."""
    t, f = plan["tuple"], plan["user_facts"]
    turns = t["turn_count"]
    return {
        "role_context": ROLE_CONTEXT[t["role"]],
        "goal": f["goal"],
        "knows": f["knows"],
        "style_name": t["user_style"],
        "style_desc": style_desc(t["user_style"], turns),
        "turns": turns,
        "n_followups": turns - 1,
    }


def check_shape(obj: dict, n_followups: int) -> None:
    if obj.get("followups") is None and n_followups == 0:
        obj["followups"] = []  # models often omit an empty list
    opening, followups = obj.get("opening_message"), obj.get("followups")
    assert isinstance(opening, str) and opening.strip(), "empty opening_message"
    assert isinstance(followups, list) and len(followups) == n_followups, (
        f"expected {n_followups} followups, got {followups!r}"
    )
    assert all(isinstance(x, str) and x.strip() for x in followups), "empty followup"


async def call(prompt: str, sem: asyncio.Semaphore, n_followups: int) -> dict:
    async with sem:
        last_error: Exception | None = None
        for _ in range(3):
            try:
                response = await litellm.acompletion(
                    model=GEN_MODEL, messages=[{"role": "user", "content": prompt}],
                    max_tokens=2000,
                )
                choice = response.choices[0]
                if not choice.message.content:
                    raise ValueError(f"empty content (finish_reason={choice.finish_reason})")
                obj = parse_json(choice.message.content)
                check_shape(obj, n_followups)
                return obj
            except Exception as exc:  # retry provider, parse, and shape errors
                last_error = exc
                await asyncio.sleep(2)
        raise RuntimeError(f"model call failed after 3 attempts: {last_error}")


async def generate(plan: dict, sem: asyncio.Semaphore, avoid: list[str] | None = None) -> dict:
    try:
        return await _generate(plan, sem, avoid)
    except Exception as exc:
        return {"id": plan["id"], "error": str(exc)}


async def _generate(plan: dict, sem: asyncio.Semaphore, avoid: list[str] | None = None) -> dict:
    v = user_view(plan)
    n = v["n_followups"]
    avoid_text = (
        AVOID_RULES.format(opening=random.choice(OPENINGS),
                           examples="\n".join(f"  - {a[:160]}" for a in avoid)) if avoid else ""
    )
    draft = await call(GEN_PROMPT.format(
        **v,
        avoid=avoid_text,
        followup_note=f" plus {n} followup(s) sent in order after support replies" if n else "",
        followup_rules=FOLLOWUP_RULES if n else "",
    ), sem, n)

    conversation = "\n".join(
        f"User message {i + 1}: {m}"
        for i, m in enumerate([draft["opening_message"], *draft["followups"]])
    )
    critic = await call(CRITIC_PROMPT.format(**v, conversation=conversation), sem, n)
    return {
        "id": plan["id"],
        "draft": {"opening_message": draft["opening_message"], "followups": draft["followups"]},
        "critic_issues": critic.get("issues", []),
        "opening_message": critic["opening_message"].strip(),
        "followups": [f.strip() for f in critic["followups"]],
    }


async def main_async(plans_path: Path, out_path: Path, ids: set[str] | None) -> None:
    plans = [json.loads(line) for line in plans_path.read_text().splitlines() if line.strip()]
    sem = asyncio.Semaphore(WORKERS)
    sidecar = plans_path.with_suffix(".messages.jsonl")
    if ids:
        # Regenerate only the named conversations; keep every other one as is.
        # Each regenerated user sees the other openings in its style, so it can
        # avoid a shared template. It still never sees `expected`.
        by_id = {r["id"]: r for r in map(json.loads, sidecar.read_text().splitlines())}
        unknown = ids - by_id.keys()
        assert not unknown, f"unknown ids: {unknown}"
        style = {p["id"]: p["tuple"]["user_style"] for p in plans}
        todo = [p for p in plans if p["id"] in ids]

        def avoid_for(p: dict) -> list[str]:
            return [r["opening_message"] for rid, r in by_id.items()
                    if rid != p["id"] and style[rid] == style[p["id"]] and "opening_message" in r]

        fresh = await asyncio.gather(*(generate(p, sem, avoid_for(p)) for p in todo))
        for r in fresh:
            r["regenerated_for"] = "template variety or earlier failure"
            by_id[r["id"]] = r
        results = list(by_id.values())
    else:
        results = await asyncio.gather(*(generate(p, sem) for p in plans))
        by_id = {r["id"]: r for r in results}

    failed = {r["id"]: r["error"] for r in by_id.values() if "error" in r}
    if failed:
        # Keep the good ones so a rerun with --ids only redoes the failures.
        sidecar.write_text("".join(json.dumps(by_id[p["id"]]) + "\n" for p in plans if p["id"] in by_id))
        for rid, err in sorted(failed.items()):
            print(f"FAILED {rid}: {err}")
        raise SystemExit(f"{len(failed)} failed; rerun with --ids {','.join(sorted(failed))}")

    # A shared template opening across conversations is a critic-level defect
    # that no single-conversation call can see, so check it here.
    openings = [r["opening_message"].casefold() for r in results]
    dupes = {o for o in openings if openings.count(o) > 1}
    assert not dupes, f"duplicate openings: {dupes}"

    sidecar.write_text("".join(json.dumps(by_id[p["id"]]) + "\n" for p in plans))

    scenarios = []
    for p in plans:
        r = by_id[p["id"]]
        scenarios.append({
            "id": p["id"],
            "scenario_group": p["scenario_group"],
            "data_quality_case_id": p["data_quality_case_id"],
            "tuple": p["tuple"],
            "opening_message": r["opening_message"],
            "followups": r["followups"],
            "expected": p["expected"],
        })
    out_path.write_text("".join(json.dumps(s) + "\n" for s in scenarios))
    flagged = sum(1 for r in results if r["critic_issues"])
    style = {p["id"]: p["tuple"]["user_style"] for p in plans}
    templated = sorted(similar_openings(by_id, style))
    print(f"wrote {len(scenarios)} scenarios to {out_path}")
    print(f"templated-looking openings (regenerate with --ids): {','.join(templated) or 'none'}")
    print(f"critic flagged and revised {flagged} of {len(results)}; details in {sidecar}")


def similar_openings(by_id: dict, style: dict) -> set[str]:
    """Ids whose opening looks templated next to another opening in the same style."""
    def norm(text: str) -> str:
        return " ".join(re.findall(r"[a-z']+", text.casefold()))

    flagged: set[str] = set()
    ids = sorted(by_id)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if style[a] != style[b]:
                continue
            na, nb = norm(by_id[a]["opening_message"]), norm(by_id[b]["opening_message"])
            same_start = na.split()[:6] == nb.split()[:6] and len(na.split()) >= 6
            if same_start or difflib.SequenceMatcher(None, na[:160], nb[:160]).ratio() > 0.8:
                flagged.add(b)  # keep the first, regenerate the later one
    return flagged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plans", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ids", help="comma-separated ids to regenerate; others are kept")
    args = parser.parse_args()
    load_env()
    litellm.suppress_debug_info = True
    ids = set(args.ids.split(",")) if args.ids else None
    asyncio.run(main_async(args.plans, args.output, ids))


if __name__ == "__main__":
    main()
