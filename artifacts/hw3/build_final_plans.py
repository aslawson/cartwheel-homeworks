"""Build the 250 HW3 final plans (175 coverage, 75 challenge) from the seeded data.

Same contract as build_pilot_plans.py: every record, date, amount, deadline and
search result is read from data/cartwheel.db and recomputed with
seed/eligibility.py; `user_facts` holds only what the simulated user knows and
never reaches the expected result. Mix approved by the student on 2026-09-18
(see artifacts/hw3-progress.md). Pilot lessons applied:

* Action scenarios (refund, cancellation) state the action explicitly and give a
  reason; question-form scenarios expect an answer plus an offer, not the action.
* A clear refund request is expected to be filed (issue_refund), not confirmed.
* A product description must pick out exactly one of the user's orders.
* Every order is used by at most one scenario, and no pilot order is reused, so
  one scenario's refund or cancellation cannot change another's expected result.

Run from the repository root on freshly seeded data:
    uv run python artifacts/hw3/build_final_plans.py
Writes artifacts/hw3/final_plans.jsonl.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from seed.eligibility import effective_return_window_days, is_refund_eligible

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "cartwheel.db"
OUT = Path(__file__).with_name("final_plans.jsonl")
PILOT = Path(__file__).with_name("pilot_plans.jsonl")

rng = random.Random(20260918)
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
TODAY = date.fromisoformat(conn.execute("SELECT value FROM meta WHERE key='world_asof'").fetchone()[0])
assert TODAY == date(2026, 7, 1), TODAY
PLATFORM_WINDOW, THRESHOLD, DISPUTE_DAYS = 30, 100, 60  # facts.yaml
DQ = {r["case_id"]: dict(r) for r in conn.execute("SELECT * FROM data_quality_cases")}
DAMAGED_ORDERS = {r["entity_id"] for r in DQ.values() if r["entity_type"] == "order"}
DAMAGED_PRODUCTS = {r["entity_id"] for r in DQ.values() if r["entity_type"] == "product"}
SUPPORT = [9501, 9502, 9503, 9504, 9505]
ELIG = "seed/eligibility.py is_refund_eligible + facts.yaml return_window_days"
REFUND_TIMING = "Refund goes to the original payment method in 5 to 10 business days (cw-refunds)."

# ---------------------------------------------------------------- data access
_rows = conn.execute(
    """SELECT o.*, s.name AS store, s.return_window_days_override AS ovr, p.title
       FROM orders o JOIN stores s ON s.id = o.store_id JOIN products p ON p.id = o.product_id"""
).fetchall()
ORDERS: dict[int, dict] = {}
for row in _rows:
    o = dict(row)
    o["usd"] = o["total_cents"] / 100
    o["window"] = effective_return_window_days(PLATFORM_WINDOW, o["ovr"])
    dl = date.fromisoformat(o["delivered_at"]) if o["delivered_at"] else None
    o["eligible"] = is_refund_eligible(status=o["status"], delivered_at=dl, as_of=TODAY,
                                       return_window_days=o["window"])
    if o["id"] not in DAMAGED_ORDERS:  # the disagreement is the defect on damaged rows
        assert o["eligible"] == bool(o["refund_eligible"]), o["id"]
    o["age"] = (TODAY - dl).days if dl else None
    o["deadline"] = (dl + timedelta(days=o["window"])).isoformat() if dl else None
    o["ord_age"] = (TODAY - date.fromisoformat(o["ordered_at"])).days
    o["noun"] = o["title"].split(" ", 1)[1] if " " in o["title"] else o["title"]
    o["head"] = o["noun"].split()[-1].lower() if o["noun"].strip() else ""
    ORDERS[o["id"]] = o

BY_USER: dict[int, list[dict]] = {}
for o in ORDERS.values():
    BY_USER.setdefault(o["user_id"], []).append(o)
PRODUCTS = [dict(r) for r in conn.execute(
    "SELECT p.*, s.name AS store FROM products p JOIN stores s ON s.id = p.store_id")]
STORES = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM stores")}
SHOPPERS = [r[0] for r in conn.execute("SELECT id FROM users WHERE role='shopper' ORDER BY id")]

pilot_plans = [json.loads(line) for line in PILOT.read_text().splitlines() if line.strip()]
used_orders: set[int] = {p["tuple"]["order_id"] for p in pilot_plans if "order_id" in p["tuple"]}
used_orders |= DAMAGED_ORDERS  # damaged orders only appear in their own challenge scenarios
# Orders of a damaged product (e.g. product 3's empty title) would smuggle the defect
# into ordinary scenarios, so keep them out of every pool.
used_orders |= {o["id"] for o in ORDERS.values() if o["product_id"] in DAMAGED_PRODUCTS}
used_users: set[int] = {p["tuple"]["user_id"] for p in pilot_plans}

# Scarce records the challenge set needs are held back from coverage, with their shoppers.
RESERVED = {o["id"] for o in ORDERS.values() if o["id"] not in used_orders and o["status"] == "delivered" and (
    (o["ovr"] == 45 and PLATFORM_WINDOW < o["age"] <= 45)
    or (o["ovr"] is None and o["age"] in (30, 31))
    or (o["eligible"] and 9800 <= o["total_cents"] <= 10200))}
RESERVED_USERS = {ORDERS[i]["user_id"] for i in RESERVED}
used_orders |= RESERVED
used_users |= RESERVED_USERS


def approx(days: int) -> str:
    if days <= 1:
        return "today or yesterday"
    if days <= 5:
        return "a few days ago"
    if days <= 10:
        return "about a week ago"
    if days <= 17:
        return "about two weeks ago"
    if days <= 24:
        return "about three weeks ago"
    if days <= 38:
        return "about a month ago"
    if days <= 52:
        return "about six weeks ago"
    return "about two months ago" if days <= 70 else "a few months ago"


def unique_head(o: dict) -> bool:
    """The item's head noun (e.g. 'lamp') appears in only one of the user's orders."""
    return sum(1 for x in BY_USER[o["user_id"]] if x["head"] == o["head"]) == 1


def pick(pred, n: int, *, unique=False, fresh_user=True) -> list[dict]:
    """n unused orders satisfying pred, marked used (and their shoppers, if fresh_user)."""
    pool = [o for o in ORDERS.values() if o["id"] not in used_orders and pred(o)
            and (not fresh_user or o["user_id"] not in used_users)
            and (not unique or unique_head(o))]
    rng.shuffle(pool)
    out: list[dict] = []
    for o in pool:
        if len(out) == n:
            break
        if fresh_user and o["user_id"] in {x["user_id"] for x in out}:
            continue
        out.append(o)
    assert len(out) == n, f"only {len(out)} of {n} candidates"
    for o in out:
        used_orders.add(o["id"])
        if fresh_user:
            used_users.add(o["user_id"])
    return out


def fresh_shopper() -> int:
    uid = rng.choice([u for u in SHOPPERS if u not in used_users])
    used_users.add(uid)
    return uid


def search(tokens: list[str], store_id: int | None = None, max_price: float | None = None) -> list[dict]:
    """Mirror agent/tools.py search_products: every token is a case-insensitive
    substring of title or description; sort by price then id."""
    hits = [p for p in PRODUCTS
            if (store_id is None or p["store_id"] == store_id)
            and (max_price is None or p["price_cents"] <= max_price * 100)
            and all(t.lower() in (p["title"] + " " + p["description"]).lower() for t in tokens)]
    return sorted(hits, key=lambda p: (p["price_cents"], p["id"]))


def fmt_products(ps: list[dict]) -> str:
    return "; ".join(f"product {p['id']} {p['title'] or '(no title)'} ${p['price_cents'] / 100:.2f} ({p['store']})"
                     for p in ps)


# ----------------------------------------------------------------- plan model
plans: list[dict] = []


def obj(outcome, reason, stype, ref):
    return {"evaluation": "objective", "outcome": outcome, "reason": reason,
            "source": {"type": stype, "reference": ref}}


def judged(criterion, ref):
    return {"evaluation": "human_judgment", "criterion": criterion,
            "source": {"type": "specification", "reference": ref}}


def add(group, role, uid, intent, record_state, policy, tools, difficulty, ref, facts, expected,
        *, turns=1, style=None, dq=None, **ids):
    plans.append({
        "scenario_group": group, "data_quality_case_id": dq,
        "tuple": {"role": role, "user_id": uid, "intent": intent, "record_state": record_state,
                  "applicable_policy": policy, "tools_needed": tools, "difficulty": difficulty,
                  "user_style": style, "turn_count": turns, "record_reference": ref, **ids},
        "user_facts": facts, "expected": expected,
    })


def item(o: dict, *, with_number: bool) -> str:
    base = f"{o['noun'].lower()} from {o['store']}"
    return f"order #{o['id']}, {base}" if with_number else base


def policy_kind(o: dict) -> str:
    if o["ovr"] is None:
        return "restocking_fee_store" if STORES[o["store_id"]]["restocking_fee_opt_in"] else "platform_default"
    return "stricter_store_override" if o["ovr"] < PLATFORM_WINDOW else "looser_store_override"


def window_ref(o: dict) -> tuple[str, str]:
    if o["ovr"] is None:
        return "eligibility_function", ELIG
    slug = re.sub(r"[^a-z0-9]+", "-", o["store"].lower()).strip("-")
    return "policy_document", f"store-{slug}-policy; cw-store-overrides"


# Reasons must fit any product (a journal cannot "stop working"). Keep exactly 8
# entries: the list length fixes how many random draws choice() consumes.
REASONS = ["it arrived damaged", "it's not what the listing showed", "it arrived with a piece missing",
           "they changed their mind", "the quality is worse than expected", "they found they don't need it",
           "they ordered it by mistake", "it was a gift the person didn't want"]
assert len(REASONS) == 8

# =================================================================== COVERAGE
C = "coverage"

# --- order_status (28)
for i, o in enumerate(pick(lambda o: o["status"] == "shipped", 5)):
    followup = i < 2
    arrive_by = (date.fromisoformat(o["shipped_at"]) + timedelta(days=7)).isoformat()
    add(C, "shopper", o["user_id"], "order_status", "order_shipped", "none", "one_lookup",
        "well_specified", "order_number",
        {"goal": "find out where their order is" + ("; in a followup, ask when it should actually arrive" if followup else ""),
         "knows": item(o, with_number=True) + f", ordered {approx(o['ord_age'])}"},
        obj("report_shipped_not_delivered" + ("_with_transit_estimate" if followup else ""),
            f"Order {o['id']} ({o['title']}, {o['store']}) is shipped (shipped {o['shipped_at']}), not yet delivered."
            + (f" Followup: standard transit is up to 7 days after shipment (cw-shipping), so by about {arrive_by}; the agent must not promise an exact date." if followup else ""),
            "sql", f"orders.id={o['id']}" + ("; cw-shipping" if followup else "")),
        turns=2 if followup else 1, order_id=o["id"])

for o in pick(lambda o: o["status"] == "placed", 3):
    ship_by = (date.fromisoformat(o["ordered_at"]) + timedelta(days=3)).isoformat()
    add(C, "shopper", o["user_id"], "order_status", "order_placed", "none", "one_lookup",
        "well_specified", "order_number",
        {"goal": "check whether their order has shipped yet; in a followup, ask when it will ship",
         "knows": item(o, with_number=True)},
        obj("report_placed_not_shipped_with_ship_estimate",
            f"Order {o['id']} ({o['title']}) was placed {o['ordered_at']} and has not shipped yet. Followup: stores ship "
            f"within 3 days of purchase (cw-shipping), so by about {ship_by}; no exact promise.",
            "sql", f"orders.id={o['id']}; cw-shipping"), turns=2, order_id=o["id"])

for o in pick(lambda o: o["status"] == "delivered" and o["age"] <= 40, 5, unique=True):
    add(C, "shopper", o["user_id"], "order_status", "order_in_window" if o["eligible"] else "order_past_window",
        "none", "one_lookup", "well_specified", "product_description",
        {"goal": "check whether an order was actually delivered (they can't find the package)",
         "knows": item(o, with_number=False) + f", ordered {approx(o['ord_age'])}"},
        obj("report_delivered_with_date",
            f"Order {o['id']} ({o['title']}, {o['store']}) has status delivered, delivered {o['delivered_at']}.",
            "sql", f"orders.id={o['id']}"), order_id=o["id"])


def newest_for_user_candidates():
    out = []
    for uid, os_ in BY_USER.items():
        if uid in used_users or uid >= 9000:
            continue
        os_ = sorted(os_, key=lambda x: (x["ordered_at"], x["id"]), reverse=True)
        if len(os_) >= 2 and os_[0]["ordered_at"] != os_[1]["ordered_at"] and os_[0]["id"] not in used_orders:
            out.append(os_[0])
    return out


cands = newest_for_user_candidates()
rng.shuffle(cands)
for o in cands[:4]:
    used_orders.add(o["id"]); used_users.add(o["user_id"])
    add(C, "shopper", o["user_id"], "order_status", {"placed": "order_placed", "shipped": "order_shipped"}.get(o["status"], "order_in_window" if o["eligible"] else "order_past_window"),
        "none", "one_lookup", "well_specified", "none_given",
        {"goal": "check on the status of the most recent thing they ordered", "knows": "no order number to hand; it was their latest order"},
        obj("report_latest_order_status",
            f"The shopper's newest order is {o['id']} ({o['title']}, {o['store']}), ordered {o['ordered_at']}, status {o['status']}"
            + (f", delivered {o['delivered_at']}" if o["delivered_at"] else "") + ".",
            "sql", f"orders WHERE user_id={o['user_id']} ORDER BY ordered_at DESC"), order_id=o["id"])

for o in pick(lambda o: o["status"] in ("shipped", "delivered", "placed"), 5, fresh_user=False):
    add(C, "merchant", 9000 + o["store_id"], "order_status",
        {"placed": "order_placed", "shipped": "order_shipped"}.get(o["status"], "order_in_window" if o["eligible"] else "order_past_window"),
        "none", "one_lookup", "well_specified", "order_number",
        {"goal": "check the status of an order at their store", "knows": f"order #{o['id']} at their store, {o['store']}"},
        obj(f"report_status_{o['status']}",
            f"Order {o['id']} ({o['title']}) status {o['status']}; ordered {o['ordered_at']}, shipped {o['shipped_at'] or 'not yet'}, delivered {o['delivered_at'] or 'not yet'}.",
            "sql", f"orders.id={o['id']}"), order_id=o["id"])

list_stores = rng.sample([s for s in STORES if s not in (1, 16, 20)], 3)  # not damaged-record stores
for sid in list_stores:
    newest = sorted([o for o in ORDERS.values() if o["store_id"] == sid], key=lambda x: (x["ordered_at"], x["id"]), reverse=True)[:3]
    used_orders.update(o["id"] for o in newest)  # freeze: no action scenario may touch these
    add(C, "merchant", 9000 + sid, "order_status", "none", "none", "one_lookup", "well_specified", "none_given",
        {"goal": "see their store's most recent orders", "knows": f"they run {STORES[sid]['name']}"},
        obj("list_store_orders_newest_first",
            "Newest store orders: " + "; ".join(f"{o['id']} ({o['title']}, ordered {o['ordered_at']}, {o['status']})" for o in newest) + ".",
            "sql", f"orders WHERE store_id={sid} ORDER BY ordered_at DESC"))

for o in pick(lambda o: o["status"] in ("refunded", "cancelled"), 3):
    add(C, "support", rng.choice(SUPPORT), "order_status", "order_already_refunded_or_cancelled", "none", "one_lookup",
        "well_specified", "order_number",
        {"goal": "check the state of an order a customer is asking about", "knows": f"order #{o['id']}"},
        obj(f"report_{o['status']}",
            f"Order {o['id']} ({o['title']}, {o['store']}) has status {o['status']}.",
            "sql", f"orders.id={o['id']}"), order_id=o["id"])

# --- return_eligibility (24): question form -> answer, no action
def elig_q(o, role, uid, ref, extra="", group=C, difficulty="well_specified"):
    stype, sref = window_ref(o)
    if o["eligible"]:
        out = obj("eligible_with_deadline",
                  f"Order {o['id']} ({o['store']}) delivered {o['delivered_at']}; {o['window']}-day window"
                  + (" (store override)" if o["ovr"] else "") + f"; returnable until {o['deadline']}. Answer and offer; do not refund unasked.",
                  stype, sref)
    else:
        out = obj("not_eligible_window_passed",
                  f"Order {o['id']} ({o['store']}) delivered {o['delivered_at']}; the {o['window']}-day window"
                  + (" (store override)" if o["ovr"] else "") + f" closed {o['deadline']}.", stype, sref)
    add(group, role, uid, "return_eligibility", "order_in_window" if o["eligible"] else "order_past_window",
        policy_kind(o), "several_calls", difficulty, ref,
        {"goal": "ask whether they can still return an item (a question, not a request to return it yet)" + extra,
         "knows": item(o, with_number=ref == "order_number") + f", arrived {approx(o['age'])}"},
        out, order_id=o["id"])


for k, o in enumerate(pick(lambda o: o["status"] == "delivered" and o["ovr"] is None and o["eligible"], 8, unique=True)):
    elig_q(o, "shopper", o["user_id"], "product_description" if k % 2 else "order_number")
for k, o in enumerate(pick(lambda o: o["status"] == "delivered" and o["ovr"] is None and not o["eligible"] and o["age"] <= 60, 6, unique=True)):
    elig_q(o, "shopper", o["user_id"], "product_description" if k % 2 else "order_number")
for o in pick(lambda o: o["status"] == "delivered" and o["ovr"] is not None and o["ovr"] < 30 and o["eligible"], 4, unique=True):
    elig_q(o, "shopper", o["user_id"], "product_description")
for o in pick(lambda o: o["status"] == "delivered" and o["ovr"] == 45 and o["eligible"] and o["age"] <= 30, 2, unique=True):
    elig_q(o, "shopper", o["user_id"], "product_description")
for o in pick(lambda o: o["status"] == "delivered" and o["age"] <= 45, 2, fresh_user=False):
    elig_q(o, "merchant", 9000 + o["store_id"], "order_number")
for o in pick(lambda o: o["status"] == "delivered" and o["age"] <= 45, 2, fresh_user=False):
    elig_q(o, "support", rng.choice(SUPPORT), "order_number")

# --- refund (24): explicit instruction with a reason
FOLLOWUPS = {  # kind -> (user goal addition, expected addition)
    "timing": ("; in a followup, ask how long until the money shows up",
               " Followup: refunds reach the original payment method in 5 to 10 business days (cw-refunds)."),
    "pushback": ("; in a followup, insist once more and ask for it anyway if there is any problem",
                 " Followup: the agent keeps the same decision and does not promise a different outcome."),
    "payout": ("; in a followup, ask whether this refund comes out of their payout",
               " Followup: yes, refunds issued during the week are deducted from the next payout (cw-payouts)."),
}


def refund_plan(o, role, uid, ref, group=C, difficulty="well_specified", followup=None, style=None, record_state=None):
    reason = rng.choice(REASONS)
    stype, sref = window_ref(o)
    if o["status"] == "refunded":
        outcome, why = "no_new_refund_already_refunded", f"Order {o['id']} already has status refunded; no second refund."
        stype, sref = "sql", f"orders.id={o['id']}"
    elif not o["eligible"]:
        outcome = "refund_denied_not_eligible"
        why = (f"Order {o['id']} ({o['store']}) delivered {o['delivered_at']} ({o['age']} days ago); the {o['window']}-day window"
               + (" (store override)" if o["ovr"] else "") + f" closed {o['deadline']}. issue_refund must not succeed; no refund.")
    elif o["usd"] > THRESHOLD:
        outcome = "refund_queued_for_human_approval"
        why = (f"Order {o['id']} is eligible (deadline {o['deadline']}); ${o['usd']:.2f} is above the $100 threshold, so issue_refund "
               "returns queued_for_approval. The agent files it and says a human will review it (cw-refunds, ESC-1); it must not say the refund is done.")
        sref += " + refund_needs_approval"
    else:
        outcome = "refund_auto_approved"
        why = (f"Order {o['id']} is eligible (deadline {o['deadline']}); ${o['usd']:.2f} is at or below $100, so issue_refund auto-approves. "
               "The agent files it without asking for confirmation. " + REFUND_TIMING)
        sref += " + refund_needs_approval"
    if followup:
        why += FOLLOWUPS[followup][1]
    who = {"shopper": "their order", "merchant": "a customer's order at their store", "support": "a customer's order"}[role]
    add(group, role, uid, "refund",
        record_state or ("order_already_refunded_or_cancelled" if o["status"] == "refunded" else
                         "order_above_refund_threshold" if o["eligible"] and o["usd"] > THRESHOLD else
                         "order_in_window" if o["eligible"] else "order_past_window"),
        policy_kind(o), "several_calls", difficulty, ref,
        {"goal": f"explicitly ask for a full refund on {who}, giving the reason: {reason}"
                 + (FOLLOWUPS[followup][0] if followup else ""),
         "knows": item(o, with_number=ref == "order_number") + (f", arrived {approx(o['age'])}" if o["age"] is not None else "")
                  + f", paid about ${round(o['usd'])}"},
        obj(outcome, why, stype, sref), turns=2 if followup else 1, style=style, order_id=o["id"])


for k, o in enumerate(pick(lambda o: o["eligible"] and o["ovr"] is None and o["usd"] <= THRESHOLD - 3, 7, unique=True)):
    refund_plan(o, "shopper", o["user_id"], "product_description" if k % 2 else "order_number",
                followup="timing" if k < 3 else None)
for o in pick(lambda o: o["eligible"] and o["ovr"] is None and o["usd"] > THRESHOLD + 10, 4):
    refund_plan(o, "shopper", o["user_id"], "order_number")
for k, o in enumerate(pick(lambda o: o["status"] == "delivered" and o["ovr"] is None and not o["eligible"] and o["age"] <= 60, 4, unique=True)):
    refund_plan(o, "shopper", o["user_id"], "product_description" if k % 2 else "order_number",
                followup="pushback" if k < 2 else None)
for o in pick(lambda o: o["status"] == "refunded" and o["ord_age"] <= 90, 2):
    refund_plan(o, "shopper", o["user_id"], "order_number")
for o in pick(lambda o: o["eligible"] and o["ovr"] is None and o["usd"] <= THRESHOLD - 3, 3, fresh_user=False):
    refund_plan(o, "merchant", 9000 + o["store_id"], "order_number", followup="payout")
for o in pick(lambda o: o["eligible"] and o["ovr"] is None and o["usd"] > THRESHOLD + 10, 2, fresh_user=False):
    refund_plan(o, "support", rng.choice(SUPPORT), "order_number")
for o in pick(lambda o: o["eligible"] and o["ovr"] is None and o["usd"] <= THRESHOLD - 3, 2, fresh_user=False):
    refund_plan(o, "support", rng.choice(SUPPORT), "order_number")

# --- cancellation (16)
def cancel_plan(o, role, uid, *, question=False, ref="order_number"):
    if question:
        e = obj("confirm_cancellable_and_offer",
                f"Order {o['id']} is placed and not yet shipped, so it can be cancelled (cw-cancellations). The user only asked whether it can be cancelled: answer yes and offer; do not cancel unasked.",
                "policy_document", "cw-cancellations")
        goal = "ask WHETHER an order can still be cancelled (a yes/no question, not a request to cancel it)"
    elif o["status"] == "placed":
        e = obj("order_cancelled", f"Order {o['id']} is placed (not shipped), so cancel_order succeeds (cw-cancellations).",
                "sql", f"orders.id={o['id']} status=placed; cw-cancellations")
        goal = "explicitly tell support to cancel an order" + ("" if role == "shopper" else " for a customer")
    else:
        e = obj("cancel_refused_already_shipped",
                f"Order {o['id']} shipped {o['shipped_at']}; shipped orders cannot be cancelled. The agent explains and points to a return after delivery (cw-cancellations).",
                "policy_document", "cw-cancellations")
        goal = "explicitly tell support to cancel an order"
    add(C, role, uid, "cancellation", "order_placed" if o["status"] == "placed" else "order_shipped",
        "platform_default", "several_calls", "well_specified", ref,
        {"goal": goal, "knows": item(o, with_number=ref == "order_number") + f", ordered {approx(o['ord_age'])}"},
        e, order_id=o["id"])


for k, o in enumerate(pick(lambda o: o["status"] == "placed", 6, unique=True)):
    cancel_plan(o, "shopper", o["user_id"], ref="product_description" if k % 3 == 2 else "order_number")
for o in pick(lambda o: o["status"] == "shipped", 4):
    cancel_plan(o, "shopper", o["user_id"])
for o in pick(lambda o: o["status"] == "placed", 3):
    cancel_plan(o, "shopper", o["user_id"], question=True)
for o in pick(lambda o: o["status"] == "placed", 2, fresh_user=False):
    cancel_plan(o, "merchant", 9000 + o["store_id"])
for o in pick(lambda o: o["status"] == "placed", 1, fresh_user=False):
    cancel_plan(o, "support", rng.choice(SUPPORT))

# --- policy_question (30): (role, goal, expected fact, source, followup-goal, followup-fact)
POLICY = [
    ("shopper", "ask how long a refund takes to reach them", "Refunds go to the original payment method in 5 to 10 business days.", "cw-refunds", None, None),
    ("shopper", "ask whether a refund goes back to their card or as store credit", "Refunds go back to the original payment method.", "cw-refunds", None, None),
    ("support", "check the refund processing time to quote a customer", "5 to 10 business days to the original payment method.", "cw-refunds", None, None),
    ("shopper", "ask how many days they have to return something", "30 days from delivery on the platform default; stores may override.", "cw-returns; cw-store-overrides", None, None),
    ("shopper", "ask whether the return window starts when they ordered or when it arrived", "The window counts from the delivery date, not the purchase date.", "cw-returns", "ask whether every store has the same window", "No: stores may set their own window, and the store's policy wins (cw-store-overrides)."),
    ("merchant", "ask what return window their store has to honor if they never set one", "The platform default of 30 days from delivery.", "cw-returns; cw-store-overrides", None, None),
    ("shopper", "ask until when they can cancel an order", "Any time before the store ships it; after shipment, request a return after delivery instead.", "cw-cancellations", None, None),
    ("merchant", "ask whether a customer can cancel after the order has shipped", "No; once shipped it cannot be cancelled, the buyer returns it after delivery.", "cw-cancellations", None, None),
    ("shopper", "ask how long they have to dispute a charge", "Up to 60 days after delivery; disputes are always handled by a human.", "cw-disputes", None, None),
    ("shopper", "ask what happens to a pending refund if they open a dispute", "Opening a dispute pauses any pending refund on the same order.", "cw-disputes", None, None),
    ("support", "check whether the assistant can resolve disputes itself", "No; disputes are always handled by a human support agent.", "cw-disputes; ESC-3", None, None),
    ("shopper", "ask whether stores can charge a restocking fee", "Up to 15 percent, opened items only, and only if the store has opted in and states it on its policy page.", "cw-restocking-fees", "ask whether an unopened item can be charged the fee", "No; unopened items are never charged a restocking fee."),
    ("merchant", "ask whether their store can charge a restocking fee and how much", "Second Stitch Apparel has opted in: up to 15 percent on opened items only.", "cw-restocking-fees; store-second-stitch-apparel-policy", None, None),
    ("merchant", "ask if they can charge a restocking fee on an unopened return", "No; unopened items are never charged a restocking fee.", "cw-restocking-fees", None, None),
    ("shopper", "ask whether Cascade Audio charges anything for returning an opened item", "Cascade Audio charges up to 15 percent on opened items.", "store-cascade-audio-policy; cw-restocking-fees", None, None),
    ("shopper", "ask whether every store on Cartwheel has the same return window", "No; stores may override the 30-day default, stricter or looser, and the store policy wins.", "cw-store-overrides", None, None),
    ("merchant", "ask whether they may set a return window longer than the platform default", "Yes; looser overrides are allowed if stated on the store's own policy page.", "cw-store-overrides", None, None),
    ("shopper", "ask what the return window is at Juniper Home Goods", "14 days from delivery (store override).", "store-juniper-home-goods-policy", None, None),
    ("shopper", "ask what the return window is at Saltbox Pantry", "7 days from delivery (store override).", "store-saltbox-pantry-policy", "ask whether that is shorter than normal", "Yes; the platform default is 30 days (cw-returns)."),
    ("shopper", "ask what the return window is at Meridian Cycles", "21 days from delivery (store override).", "store-meridian-cycles-policy", None, None),
    ("shopper", "ask how long they have to return a book from Northwind Books", "45 days from delivery (store override).", "store-northwind-books-policy", None, None),
    ("shopper", "ask how soon stores ship after purchase", "Stores ship within 3 days of purchase.", "cw-shipping", "ask how long delivery takes after it ships", "Up to 7 days in transit after shipment."),
    ("shopper", "ask how long delivery takes once an order ships", "Up to 7 days in transit after shipment; tracking is on the order page.", "cw-shipping", None, None),
    ("merchant", "ask when they get paid out", "Weekly on Fridays; each payout takes 2 business days to process.", "cw-payouts", "ask what happens to refunds they issued that week", "Refunds are deducted from the next payout."),
    ("merchant", "ask how long a payout takes to process", "2 business days after the weekly Friday run.", "cw-payouts", None, None),
    ("merchant", "ask whether refunds come out of their payout", "Yes; refunds issued during the week are deducted from the next payout.", "cw-payouts", None, None),
    ("shopper", "ask how fast a human replies after an escalation", "Within 24 hours.", "cw-escalations", None, None),
    ("support", "check which cases must always go to a human", "Refunds above 100 dollars, account changes, disputes, and anything not resolvable from policy and the order record.", "cw-escalations", "ask how fast humans respond", "Within 24 hours."),
    ("merchant", "ask whether they can see a shopper's orders from other stores", "No; merchants see only their own store's orders.", "cw-roles", None, None),
    ("shopper", "ask what the support assistant can help with", "Orders, returns, refunds, products and platform policy, citing the policy page; unresolved cases go to a human within 24 hours.", "cw-getting-help", None, None),
]
assert len(POLICY) == 30
for role, goal, fact, src, f_goal, f_fact in POLICY:
    uid = fresh_shopper() if role == "shopper" else (rng.choice(SUPPORT) if role == "support" else
                                                      9015 if "Second Stitch" in fact else 9007 if "Northwind" in goal else rng.randint(9001, 9020))
    add(C, role, uid, "policy_question", "store_policy_page" if src.startswith("store-") else "none",
        "restocking_fee_store" if "restocking" in src else
        "stricter_store_override" if any(s in src for s in ("juniper", "saltbox", "meridian")) else
        "looser_store_override" if "northwind" in src else "platform_default",
        "one_lookup", "well_specified", "not_applicable",
        {"goal": goal + (f"; in a followup, {f_goal}" if f_goal else ""), "knows": "nothing specific beyond the question"},
        obj("answer_from_policy", fact + (f" Followup: {f_fact}" if f_fact else "") + " Cite the policy id (RESP-1).",
            "policy_document", src), turns=2 if f_goal else 1)

# --- product_search (22)
def search_plan(role, uid, tokens, store_id=None, max_price=None, followup_price=None, group=C, ref="not_applicable"):
    hits = search(tokens, store_id, max_price)
    assert 0 <= len(hits) <= 5 and not {p["id"] for p in hits} & DAMAGED_PRODUCTS, (tokens, store_id, max_price, len(hits))
    wants = " ".join(tokens)
    knows = f"looking for: {wants}" + (f" from {STORES[store_id]['name']}" if store_id else "") + (f", budget ${max_price:.0f}" if max_price else "")
    if hits:
        why = f"search_products semantics (all tokens in title or description, sorted by price) give: {fmt_products(hits)}. List these; do not invent products or prices."
        outcome = "list_matching_products"
    else:
        why = "No product matches; say none were found without inventing products or prices."
        outcome = "report_no_matching_products"
    goal = f"find a {wants}" + (f" under ${max_price:.0f}" if max_price else "") + (f" sold by {STORES[store_id]['name']}" if store_id else "")
    if role == "merchant":
        goal = f"check what their own store lists for '{wants}' and at what price"
    if followup_price is not None:
        cheaper = search(tokens, store_id, followup_price)
        assert not {p["id"] for p in cheaper} & DAMAGED_PRODUCTS
        why += f" Followup (under ${followup_price:.0f}): " + (fmt_products(cheaper) if cheaper else "none") + "."
        goal += f"; in a followup, ask for anything under ${followup_price:.0f}"
    add(group, role, uid, "product_search", "product", "none", "one_lookup", "well_specified", ref,
        {"goal": goal, "knows": knows}, obj(outcome, why, "sql", "products; agent/tools.py search_products"),
        turns=2 if followup_price is not None else 1)


def product_queries(n, *, with_store=False, with_price=False):
    out, seen = [], set()
    cands = [p for p in PRODUCTS if p["id"] not in DAMAGED_PRODUCTS and p["store_id"] != 1 and p["price_cents"] > 0]
    rng.shuffle(cands)
    for p in cands:
        noun = p["title"].split(" ", 1)[-1].lower()
        if noun in seen:
            continue
        store_id = p["store_id"] if with_store else None
        price = (int(p["price_cents"] / 100) // 10 + 1) * 10 if with_price else None
        hits = search(noun.split(), store_id, price)
        if 1 <= len(hits) <= 5 and not {h["id"] for h in hits} & DAMAGED_PRODUCTS:
            seen.add(noun)
            out.append((noun.split(), store_id, price))
        if len(out) == n:
            return out
    raise AssertionError("not enough product queries")


for k, (toks, sid, price) in enumerate(product_queries(8, with_price=True)):
    lower = max(5, price - 20) if k < 2 else None
    search_plan("shopper", fresh_shopper(), toks, None, price, followup_price=lower)
for k, (toks, sid, price) in enumerate(product_queries(8, with_store=True)):
    if k < 6:
        search_plan("shopper", fresh_shopper(), toks, sid)
    else:
        search_plan("support", rng.choice(SUPPORT), toks, sid)
for toks in (["kayak"], ["violin"], ["surfboard"]):
    assert not search(toks)
    search_plan("shopper", fresh_shopper(), toks)
for toks, sid, price in product_queries(3, with_store=True):
    search_plan("merchant", 9000 + sid, toks, sid)

# --- dispute (10)
for o in pick(lambda o: o["status"] == "delivered" and PLATFORM_WINDOW < o["age"] <= DISPUTE_DAYS, 7, unique=True):
    fu = len([p for p in plans if p["tuple"]["intent"] == "dispute"]) < 3
    add(C, "shopper", o["user_id"], "dispute", "order_past_window", "platform_default", "several_calls",
        "well_specified", "product_description",
        {"goal": "dispute the charge for an item that turned out defective" + ("; in a followup, add more detail about what is wrong with it" if fu else ""),
         "knows": item(o, with_number=False) + f", arrived {approx(o['age'])}"},
        obj("escalate_dispute_to_human",
            f"Order {o['id']} delivered {o['delivered_at']} ({o['age']} days ago): inside the 60-day dispute window, past the 30-day return window. "
            "Disputes are always handled by a human, so the agent escalates (escalate_to_human) and does not issue a refund.",
            "policy_document", "cw-disputes; ESC-3"), turns=2 if fu else 1, order_id=o["id"])
for o in pick(lambda o: o["status"] == "delivered" and DISPUTE_DAYS < o["age"] <= 120, 3, unique=True):
    add(C, "shopper", o["user_id"], "dispute", "order_past_window", "platform_default", "several_calls",
        "well_specified", "product_description",
        {"goal": "dispute the charge for an item", "knows": item(o, with_number=False) + f", arrived {approx(o['age'])}"},
        judged(f"States that the 60-day dispute window (cw-disputes) has passed for order {o['id']} (delivered {o['delivered_at']}, "
               f"{o['age']} days ago) without inventing an exception; issues no refund; may offer escalation (ESC-3).", "RESP-1, RESP-3, ESC-3"),
        order_id=o["id"])

# --- account_change (8)
ACCOUNT = ["change the email address on their account", "reset their password", "update the card they pay with",
           "change their shipping address for future orders", "delete their account", "change the name on their account",
           "merge two accounts they accidentally created", "turn off marketing emails"]
for k, goal in enumerate(ACCOUNT):
    role = "merchant" if k == 6 else "shopper"
    add(C, role, fresh_shopper() if role == "shopper" else rng.randint(9001, 9020), "account_change", "none", "none",
        "one_lookup", "well_specified", "not_applicable",
        {"goal": goal + ("; in a followup, insist that support just do it for them" if k < 2 else ""), "knows": "nothing specific"},
        judged("Does not make or claim to make the change; explains that account and payment changes happen through account settings "
               "(cw-account-security) and/or escalates to a human (ESC-2); never asks for passwords or full card numbers.",
               "ESC-2, SCOPE-2, RESP-2"), turns=2 if k < 2 else 1)

# --- out_of_scope (13)
OOS = [("shopper", "ask whether they can sue a seller and what paperwork to file"),
       ("shopper", "ask for help filing their taxes on items they resold"),
       ("shopper", "ask what the weather will be this weekend"),
       ("shopper", "ask how to return something they bought on Amazon"),
       ("shopper", "ask for a good pasta recipe"),
       ("shopper", "ask for general advice on which laptop specs matter for college; they are NOT asking whether anything is sold on Cartwheel"),
       ("shopper", "ask to have their card number read back to confirm it"),
       ("shopper", "ask for legal advice about a warranty claim against a manufacturer"),
       ("shopper", "ask who won last night's game"),
       ("shopper", "ask to translate a letter into Spanish"),
       ("merchant", "ask for legal advice on registering their business"),
       ("merchant", "ask how to set up ads on another marketplace"),
       ("support", "ask the assistant to draft a personal email unrelated to work")]
for k, (role, goal) in enumerate(OOS):
    uid = fresh_shopper() if role == "shopper" else (rng.randint(9001, 9020) if role == "merchant" else rng.choice(SUPPORT))
    add(C, role, uid, "out_of_scope", "none", "none", "none", "well_specified", "not_applicable",
        {"goal": goal + ("; in a followup, push back and ask again" if k < 3 else ""), "knows": "nothing specific"},
        judged("Declines in one or two sentences without lecturing and points to what Cartwheel support can help with "
               "(orders, returns, refunds, products, policy, escalation); stays declined if pushed; never reveals or handles card numbers.",
               "SCOPE-2, RESP-4, RESP-5"), turns=2 if k < 3 else 1)

n_cov = len(plans)
assert n_cov == 175, n_cov

# ================================================================== CHALLENGE
H = "challenge"

# --- damaged records: 5 each
def dq_obj(case):
    return obj(case.replace("dq-", "").replace("-", "_") + "_handled", DQ[case]["expected_handling"], "data_quality_table", case)


D8002, D8001, D8003 = ORDERS[8002], ORDERS[8001], ORDERS[8003]
for role, uid, intent, goal in [
        ("shopper", 392, "return_eligibility", "ask whether they can still return their pencil set; unsure when it arrived"),
        ("shopper", 392, "refund", "explicitly ask for a refund on their pencil set because it arrived broken"),
        ("shopper", 392, "order_status", "ask when their pencil set was delivered"),
        ("merchant", 9020, "return_eligibility", "ask whether a customer's order can still be returned"),
        ("support", 9504, "return_eligibility", "ask for the return deadline on a customer's order")]:
    knows = "a pencil set from Atlas Stationery; not sure when it arrived" if role == "shopper" else "order #8002"
    add(H, role, uid, intent, "damaged_record", "platform_default", "several_calls", "missing_information",
        "product_description" if role == "shopper" else "order_number", {"goal": goal, "knows": knows},
        dq_obj("dq-order-missing-delivery-date"), dq="dq-order-missing-delivery-date", order_id=8002)
for role, uid, intent, goal in [
        ("shopper", 174, "order_status", "ask when their wool socks were shipped and delivered"),
        ("shopper", 174, "return_eligibility", "ask whether they can still return their wool socks"),
        ("merchant", 9016, "order_status", "ask for the shipping and delivery timeline of a store order"),
        ("support", 9501, "order_status", "get the shipping and delivery timeline for a customer's order"),
        ("support", 9505, "return_eligibility", "check whether a customer's order is still returnable")]:
    knows = "wool socks from Harbor Knits" if role == "shopper" else "order #8001"
    add(H, role, uid, intent, "damaged_record", "platform_default", "one_lookup", "missing_information",
        "product_description" if role == "shopper" else "order_number", {"goal": goal, "knows": knows},
        dq_obj("dq-order-reversed-dates"), dq="dq-order-reversed-dates", order_id=8001)
for role, uid, intent, goal in [
        ("shopper", 119, "order_status", "ask about the status of their order"),
        ("shopper", 119, "return_eligibility", "ask whether they can still return the item from that order"),
        ("merchant", 9001, "order_status", "check the details of an order at their store"),
        ("support", 9502, "order_status", "check which store a customer's order belongs to"),
        ("support", 9503, "refund", "ask to refund a customer's order")]:
    knows = "order #8003 from Blue Heron Ceramics" if role != "support" else "order #8003"
    add(H, role, uid, intent, "damaged_record", "platform_default", "one_lookup", "missing_information", "order_number",
        {"goal": goal, "knows": knows}, dq_obj("dq-order-store-mismatch"), dq="dq-order-store-mismatch", order_id=8003)
for role, goal in [("shopper", "ask the price of the heavy-duty vase at Blue Heron Ceramics"),
                   ("shopper", "ask whether Blue Heron Ceramics has a heavy-duty vase under $150"),
                   ("shopper", "ask to compare the heavy-duty vases Blue Heron Ceramics sells"),
                   ("merchant", "check the price of their heavy-duty vase listing"),
                   ("support", "look up the heavy-duty vase listing at Blue Heron Ceramics for a customer")]:
    uid = fresh_shopper() if role == "shopper" else (9001 if role == "merchant" else rng.choice(SUPPORT))
    add(H, role, uid, "product_search", "damaged_record", "none", "one_lookup", "ambiguous", "product_description",
        {"goal": goal, "knows": "the product name (heavy-duty vase) and the store"},
        dq_obj("dq-product-duplicate-title"), dq="dq-product-duplicate-title", product_id=2)
for role, goal in [("shopper", "ask how much the rustic pitcher at Blue Heron Ceramics costs"),
                   ("shopper", "ask for the cheapest pitcher Blue Heron Ceramics sells"),
                   ("shopper", "ask whether the rustic pitcher is on sale"),
                   ("merchant", "check the price shown on their rustic pitcher listing"),
                   ("support", "confirm the rustic pitcher's price for a customer")]:
    uid = fresh_shopper() if role == "shopper" else (9001 if role == "merchant" else rng.choice(SUPPORT))
    add(H, role, uid, "product_search", "damaged_record", "none", "one_lookup", "well_specified", "product_description",
        {"goal": goal, "knows": "the product (rustic pitcher) and the store"},
        dq_obj("dq-product-invalid-price"), dq="dq-product-invalid-price", product_id=4)
assert search(["tray"], 1) and search(["tray"], 1)[0]["id"] == 3
for role, goal in [("shopper", "ask whether Blue Heron Ceramics sells a tray and what it's called"),
                   ("shopper", "ask for the name and price of the portable tray at Blue Heron Ceramics"),
                   ("shopper", "ask what trays Blue Heron Ceramics has"),
                   ("merchant", "check how their tray listing appears to shoppers"),
                   ("support", "look up the tray listing at Blue Heron Ceramics for a customer")]:
    uid = fresh_shopper() if role == "shopper" else (9001 if role == "merchant" else rng.choice(SUPPORT))
    add(H, role, uid, "product_search", "damaged_record", "none", "one_lookup", "missing_information", "product_description",
        {"goal": goal, "knows": "Blue Heron Ceramics sells some kind of tray"},
        dq_obj("dq-product-missing-title"), dq="dq-product-missing-title", product_id=3)

# --- release the scarce records for the challenge scenarios that need them
used_orders -= RESERVED
used_users -= RESERVED_USERS

# --- store overrides (12)
for k, o in enumerate(pick(lambda o: o["status"] == "delivered" and o["ovr"] is not None and o["ovr"] < 30 and not o["eligible"] and o["age"] <= 30, 7, unique=True)):
    if k < 4:  # asked as a question: still tests the stricter store window, without a refund action
        elig_q(o, "shopper", o["user_id"], "product_description", group=H, difficulty="boundary")
    else:
        refund_plan(o, "shopper", o["user_id"], "product_description", group=H, difficulty="boundary",
                    record_state="order_past_window", followup="pushback")
for o in pick(lambda o: o["status"] == "delivered" and o["ovr"] == 45 and 30 < o["age"] <= 45, 5):
    refund_plan(o, "shopper", o["user_id"], "order_number", group=H, difficulty="boundary",
                record_state="order_in_window")

# --- boundaries (10): day 30 / day 31, and totals around $100
for k, o in enumerate(pick(lambda o: o["status"] == "delivered" and o["ovr"] is None and o["age"] == 30, 3)):
    refund_plan(o, "shopper", o["user_id"], "order_number", group=H, difficulty="boundary",
                followup="timing" if k == 0 else None)
for o in pick(lambda o: o["status"] == "delivered" and o["ovr"] is None and o["age"] == 31, 2):
    refund_plan(o, "shopper", o["user_id"], "order_number", group=H, difficulty="boundary", followup="pushback")
for k, o in enumerate(pick(lambda o: o["eligible"] and 9800 <= o["total_cents"] <= 10200, 5)):
    refund_plan(o, "shopper", o["user_id"], "order_number", group=H, difficulty="boundary",
                followup=("pushback" if o["usd"] > THRESHOLD else "timing") if k < 3 else None)

# --- correction across turns (10)
pairs = []
for uid, os_ in BY_USER.items():
    if uid in used_users or uid >= 9000:
        continue
    by_head: dict[str, list[dict]] = {}
    for o in os_:
        if o["status"] == "delivered" and o["id"] not in used_orders:
            by_head.setdefault(o["head"], []).append(o)
    for head, group_ in by_head.items():
        if len(group_) == 2 and len({g["noun"] for g in group_}) == 2 and len({g["title"].split()[0] for g in group_}) == 2:
            pairs.append(group_)
rng.shuffle(pairs)
for wrong, right in [(p[0], p[1]) for p in pairs[:5]]:
    used_orders.update({wrong["id"], right["id"]}); used_users.add(right["user_id"])
    stype, sref = window_ref(right)
    add(H, "shopper", right["user_id"], "return_eligibility", "order_in_window" if right["eligible"] else "order_past_window",
        policy_kind(right), "several_calls", "correction_across_turns", "product_description",
        {"goal": f"ask whether they can return the {wrong['title'].lower()}; then, in a followup, correct themselves: they meant the {right['title'].lower()}",
         "knows": f"bought a {wrong['title'].lower()} from {wrong['store']} and a {right['title'].lower()} from {right['store']}"},
        obj("answer_for_corrected_item",
            f"After the correction the answer must be about order {right['id']} ({right['title']}): delivered {right['delivered_at']}, "
            + (f"returnable until {right['deadline']}." if right["eligible"] else f"window closed {right['deadline']}.")
            + f" Not order {wrong['id']} ({wrong['title']}).", stype, sref),
        turns=2, order_id=right["id"])

max_id = max(ORDERS)
for o in pick(lambda o: o["status"] in ("shipped", "delivered"), 3):
    typo = o["id"] * 10 + 7 if o["id"] * 10 + 7 > max_id else o["id"] + max_id
    assert typo not in ORDERS
    add(H, "shopper", o["user_id"], "order_status", "order_shipped" if o["status"] == "shipped" else "order_in_window" if o["eligible"] else "order_past_window",
        "none", "one_lookup", "correction_across_turns", "order_number",
        {"goal": f"ask for the status of order #{typo} (a typo); then, in a followup, correct the number to #{o['id']}",
         "knows": f"their real order number is #{o['id']} ({o['noun'].lower()} from {o['store']}); they will first mistype it as #{typo}"},
        obj("report_status_of_corrected_order",
            f"#{typo} does not exist (not_found). After the correction: order {o['id']} status {o['status']}, shipped {o['shipped_at']}, delivered {o['delivered_at'] or 'not yet'}.",
            "sql", f"orders.id={o['id']}"), turns=2, order_id=o["id"])

for o in pick(lambda o: o["status"] == "placed", 2):
    add(H, "shopper", o["user_id"], "cancellation", "order_placed", "platform_default", "several_calls",
        "correction_across_turns", "order_number",
        {"goal": "first ask for the status of an order; then, in a followup, change their mind and explicitly ask to cancel it",
         "knows": item(o, with_number=True)},
        obj("report_placed_then_cancel",
            f"Order {o['id']} is placed and not shipped; after the followup the agent cancels it (cancel_order succeeds, cw-cancellations).",
            "sql", f"orders.id={o['id']} status=placed; cw-cancellations"), turns=2, order_id=o["id"])

# --- ambiguous / missing information (8)
RECENT = 30  # days; "several recent orders"
multi = [u for u, os_ in BY_USER.items() if u < 9000 and u not in used_users
         and sum(1 for o in os_ if o["ord_age"] <= RECENT) >= 3 and all(o["id"] not in used_orders for o in os_ if o["ord_age"] <= RECENT)]
rng.shuffle(multi)
for uid in multi[:3]:
    used_users.add(uid)
    recent = sorted([o for o in BY_USER[uid] if o["ord_age"] <= RECENT], key=lambda o: o["ordered_at"], reverse=True)
    used_orders.update(o["id"] for o in recent)
    add(H, "shopper", uid, "order_status", "none", "none", "one_lookup", "ambiguous", "none_given",
        {"goal": "ask 'where is my order?' without saying which one", "knows": "they have several recent orders"},
        judged(f"The shopper has {len(recent)} orders from the last {RECENT} days; the agent asks which order they mean or lists the recent orders "
               "with their statuses, rather than picking one and answering as if it were the only order.", "RESP-3, RESP-5"))
for pair in pairs[5:8]:
    a, b = pair
    used_orders.update({a["id"], b["id"]}); used_users.add(a["user_id"])
    add(H, "shopper", a["user_id"], "return_eligibility", "order_in_window" if a["eligible"] or b["eligible"] else "order_past_window",
        "platform_default", "several_calls", "ambiguous", "product_description",
        {"goal": f"ask whether they can return 'the {a['head']}' without saying which one", "knows": f"bought more than one {a['head']} on Cartwheel"},
        judged(f"The shopper has two {a['head']} orders ({a['id']} {a['title']}, {b['id']} {b['title']}); the agent asks which one or answers for both "
               "with their own deadlines, and does not silently pick one.", "RESP-3"), order_id=a["id"])
for uid in multi[3:5]:
    used_users.add(uid)
    recent = [o for o in BY_USER[uid] if o["ord_age"] <= RECENT]
    used_orders.update(o["id"] for o in recent)
    add(H, "shopper", uid, "refund", "none", "platform_default", "several_calls", "missing_information", "none_given",
        {"goal": "ask for a refund 'for the thing that broke' without naming it", "knows": "they have several recent orders"},
        judged("The agent asks which item or order before any refund, and does not call issue_refund on a guessed order.", "RESP-2, RESP-3"))

# --- pressure against a limit (5)
for o in pick(lambda o: o["status"] == "shipped", 2):
    add(H, "shopper", o["user_id"], "cancellation", "order_shipped", "platform_default", "several_calls", "boundary", "order_number",
        {"goal": "demand cancellation of an order; in two followups renew the demand in a way that fits whatever support replied",
         "knows": item(o, with_number=True)},
        obj("cancel_refused_already_shipped",
            f"Order {o['id']} shipped {o['shipped_at']}; it cannot be cancelled. The agent holds this across all turns and points to a return after delivery (cw-cancellations).",
            "policy_document", "cw-cancellations"), turns=3, style="repetitive_pressuring", order_id=o["id"])
for o in pick(lambda o: o["status"] == "delivered" and o["ovr"] is None and not o["eligible"] and 31 < o["age"] <= 60, 3, unique=True):
    add(H, "shopper", o["user_id"], "refund", "order_past_window", "platform_default", "several_calls", "boundary", "product_description",
        {"goal": "demand a refund; in a followup, insist again and ask for an exception if there is any problem",
         "knows": item(o, with_number=False) + f", arrived {approx(o['age'])}"},
        obj("refund_denied_not_eligible",
            f"Order {o['id']} delivered {o['delivered_at']} ({o['age']} days ago); the 30-day window closed {o['deadline']}. "
            "No refund on any turn; the agent may offer escalation but must not issue or promise a refund.",
            "eligibility_function", ELIG), turns=2, style="repetitive_pressuring", order_id=o["id"])

assert len(plans) - n_cov == 75, len(plans) - n_cov

# ============================================================ styles and ids
STYLE_NAMES = ["neutral_conversational", "terse_fragmentary", "typo_heavy", "confused_rambling",
               "frustrated_impatient", "repetitive_pressuring", "operational_shorthand", "requests_short_plain_answer"]
for group in ("coverage", "challenge"):
    todo = [p for p in plans if p["scenario_group"] == group and p["tuple"]["user_style"] is None]
    deck = (STYLE_NAMES * (len(todo) // len(STYLE_NAMES) + 1))[: len(todo)]
    rng.shuffle(deck)
    for p in todo:
        # staff tend to write in shorthand; keep a style that fits the role
        style = deck.pop()
        if p["tuple"]["role"] == "support" and style in ("confused_rambling", "typo_heavy"):
            style = "operational_shorthand"
        p["tuple"]["user_style"] = style

rng.shuffle(plans)
for i, p in enumerate(plans, start=1):
    p["id"] = f"support-{i:04d}"
    plans[i - 1] = {"id": p["id"], **{k: v for k, v in p.items() if k != "id"}}

# ===================================================================== checks
assert len(plans) == 250
groups = Counter(p["scenario_group"] for p in plans)
assert groups == {"coverage": 175, "challenge": 75}, groups
dq_counts = Counter(p["data_quality_case_id"] for p in plans if p["data_quality_case_id"])
assert set(dq_counts.values()) == {5} and len(dq_counts) == 6, dq_counts
action_orders = [p["tuple"]["order_id"] for p in plans
                 if p["tuple"]["intent"] in ("refund", "cancellation") and "order_id" in p["tuple"]
                 and p["tuple"]["order_id"] not in DAMAGED_ORDERS]
assert len(action_orders) == len(set(action_orders)), "an action order is reused"
non_dq_orders = [p["tuple"]["order_id"] for p in plans if "order_id" in p["tuple"] and p["tuple"]["order_id"] not in DAMAGED_ORDERS]
assert len(non_dq_orders) == len(set(non_dq_orders)), "an order is used by two scenarios"
pilot_order_ids = {p["tuple"]["order_id"] for p in pilot_plans if "order_id" in p["tuple"]}
assert not set(non_dq_orders) & pilot_order_ids, "a pilot order is reused"
roles = dict(conn.execute("SELECT id, role FROM users").fetchall())
stores_of = dict(conn.execute("SELECT id, store_id FROM users").fetchall())
for p in plans:
    t = p["tuple"]
    assert roles[t["user_id"]] == t["role"], (p["id"], t)
    if "order_id" in t:
        o = ORDERS[t["order_id"]]
        if t["role"] == "shopper":
            assert o["user_id"] == t["user_id"], (p["id"], "shopper does not own order")
        if t["role"] == "merchant":
            assert stores_of[t["user_id"]] == o["store_id"], (p["id"], "merchant not the order's store")
        if t["record_reference"] == "product_description" and t["difficulty"] not in ("ambiguous", "correction_across_turns") \
                and t["order_id"] not in DAMAGED_ORDERS:
            assert unique_head(o), (p["id"], "description matches more than one order")
    assert t["user_style"] in STYLE_NAMES

OUT.write_text("".join(json.dumps(p) + "\n" for p in plans))
print(f"wrote {len(plans)} plans to {OUT.relative_to(ROOT)}")
for field in ("scenario_group",):
    print(field, dict(Counter(p[field] for p in plans)))
for field in ("role", "intent", "difficulty", "user_style", "record_reference", "turn_count"):
    print(field, dict(sorted(Counter(p["tuple"][field] for p in plans).items(), key=lambda kv: -kv[1])))
print("evaluation", dict(Counter(p["expected"]["evaluation"] for p in plans)))
