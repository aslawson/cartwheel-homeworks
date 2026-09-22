"""Build the 30 HW3 pilot plans: tuples, grounded records, and expected results.

Every order fact, eligibility decision, and deadline below is read from
data/cartwheel.db and recomputed with seed/eligibility.py, then asserted, so
a plan cannot silently drift from the seeded data. User messages are NOT
written here: `user_facts` holds only what the simulated user would know, and
the message generator never sees `expected`.

Run from the repository root on freshly seeded data:
    uv run python artifacts/hw3/build_pilot_plans.py
Writes artifacts/hw3/pilot_plans.jsonl.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from seed.eligibility import effective_return_window_days, is_refund_eligible

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "cartwheel.db"
OUT = Path(__file__).with_name("pilot_plans.jsonl")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
TODAY = date.fromisoformat(
    conn.execute("SELECT value FROM meta WHERE key='world_asof'").fetchone()[0]
)
assert TODAY == date(2026, 7, 1), TODAY
PLATFORM_WINDOW = 30  # facts.yaml return_window_days
THRESHOLD = 100  # facts.yaml refund_auto_approve_threshold_usd
DAMAGED_ORDERS = {
    r[0] for r in conn.execute("SELECT entity_id FROM data_quality_cases WHERE entity_type='order'")
}


def order(oid: int) -> dict:
    row = conn.execute(
        """SELECT o.*, s.name AS store, s.return_window_days_override AS ovr,
                  s.restocking_fee_opt_in AS fee_opt_in, p.title
           FROM orders o JOIN stores s ON s.id = o.store_id
           JOIN products p ON p.id = o.product_id WHERE o.id = ?""",
        (oid,),
    ).fetchone()
    assert row is not None, oid
    o = dict(row)
    o["usd"] = o["total_cents"] / 100
    o["window"] = effective_return_window_days(PLATFORM_WINDOW, o["ovr"])
    dl = date.fromisoformat(o["delivered_at"]) if o["delivered_at"] else None
    o["eligible"] = is_refund_eligible(
        status=o["status"], delivered_at=dl, as_of=TODAY, return_window_days=o["window"]
    )
    # The seeded flag must agree with the oracle, or the plan is built on sand.
    # Damaged records (8001-8003) are exempt: the disagreement is the defect.
    if oid not in DAMAGED_ORDERS:
        assert o["eligible"] == bool(o["refund_eligible"]), (oid, o["eligible"])
    o["age"] = (TODAY - dl).days if dl else None
    o["deadline"] = (dl + timedelta(days=o["window"])).isoformat() if dl else None
    return o


def owned_by(o: dict, user_id: int) -> None:
    assert o["user_id"] == user_id, (o["id"], user_id)


def merchant_of(o: dict, merchant_id: int) -> None:
    store = conn.execute("SELECT store_id FROM users WHERE id=?", (merchant_id,)).fetchone()[0]
    assert store == o["store_id"], (o["id"], merchant_id)


def only_match(user_id: int, needle: str, oid: int) -> None:
    """A product description must pick out exactly one of the user's orders."""
    rows = conn.execute(
        """SELECT o.id FROM orders o JOIN products p ON p.id = o.product_id
           WHERE o.user_id = ? AND p.title LIKE ?""",
        (user_id, f"%{needle}%"),
    ).fetchall()
    assert [r[0] for r in rows] == [oid], (user_id, needle, [r[0] for r in rows])


def newest(where: str, arg: int) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            f"""SELECT o.id, o.status, o.ordered_at, p.title FROM orders o
                JOIN products p ON p.id = o.product_id WHERE {where}
                ORDER BY o.ordered_at DESC, o.id DESC LIMIT 3""",
            (arg,),
        )
    ]


def obj(outcome: str, reason: str, stype: str, ref: str) -> dict:
    return {"evaluation": "objective", "outcome": outcome, "reason": reason,
            "source": {"type": stype, "reference": ref}}


def judged(criterion: str, ref: str) -> dict:
    return {"evaluation": "human_judgment", "criterion": criterion,
            "source": {"type": "specification", "reference": ref}}


def tup(role, uid, intent, record_state, policy, tools, difficulty, style, turns, ref, **ids):
    return {"role": role, "user_id": uid, "intent": intent, "record_state": record_state,
            "applicable_policy": policy, "tools_needed": tools, "difficulty": difficulty,
            "user_style": style, "turn_count": turns, "record_reference": ref, **ids}


plans: list[dict] = []


def add(group, t, facts, expected, dq=None):
    plans.append({"id": f"pilot-{len(plans) + 1:03d}", "scenario_group": group,
                  "data_quality_case_id": dq, "tuple": t, "user_facts": facts,
                  "expected": expected})


ELIG_REF = "seed/eligibility.py is_refund_eligible + facts.yaml return_window_days"

# ------------------------------------------------------------------ coverage
o = order(880); owned_by(o, 277); assert o["status"] == "shipped"
add("coverage", tup("shopper", 277, "order_status", "order_shipped", "none", "one_lookup",
                    "well_specified", "neutral_conversational", 1, "order_number", order_id=880),
    {"goal": "find out where their order is", "knows": f"order number #{o['id']}, a rain shell"},
    obj("report_shipped_not_delivered",
        f"Order 880 ({o['title']}, {o['store']}) has status shipped, shipped {o['shipped_at']}, no delivery date yet.",
        "sql", "orders.id=880"))

latest = newest("o.user_id = ?", 269)[0]
assert latest["id"] == 1532 and latest["status"] == "placed"
add("coverage", tup("shopper", 269, "order_status", "order_placed", "none", "one_lookup",
                    "well_specified", "terse_fragmentary", 1, "none_given", order_id=1532),
    {"goal": "check on the most recent thing they ordered", "knows": "they ordered something today; no order number to hand"},
    obj("report_latest_order_placed_not_shipped",
        "The shopper's newest order is 1532 (Travel Lip Balm), placed 2026-07-01 and not yet shipped.",
        "sql", "orders WHERE user_id=269 ORDER BY ordered_at DESC"))

o = order(1862); owned_by(o, 45); only_match(45, "Headphones", 1862); assert o["eligible"]
add("coverage", tup("shopper", 45, "return_eligibility", "order_in_window", "platform_default",
                    "several_calls", "well_specified", "typo_heavy", 1, "product_description", order_id=1862),
    {"goal": "ask whether they can still return an item", "knows": f"bought {o['title'].lower()} from {o['store']} a little while ago"},
    obj("eligible_with_deadline",
        f"Order 1862 delivered {o['delivered_at']} ({o['age']} days ago); {o['window']}-day platform window; eligible until {o['deadline']}.",
        "eligibility_function", ELIG_REF))

o = order(517); owned_by(o, 283); assert o["eligible"] and o["usd"] <= THRESHOLD
add("coverage", tup("shopper", 283, "refund", "order_in_window", "platform_default", "several_calls",
                    "well_specified", "frustrated_impatient", 1, "order_number", order_id=517),
    {"goal": "get a refund for an order", "knows": f"order #{o['id']}, a card game, arrived broken"},
    obj("refund_auto_approved",
        f"Order 517 is eligible (delivered {o['delivered_at']}, deadline {o['deadline']}); ${o['usd']:.2f} is at or below the $100 threshold, so issue_refund auto-approves. Refund goes to the original payment method in 5-10 business days (cw-refunds).",
        "eligibility_function", ELIG_REF + " + refund_needs_approval"))

o = order(305); owned_by(o, 296); only_match(296, "Matte Pitcher", 305); assert not o["eligible"]
add("coverage", tup("shopper", 296, "refund", "order_past_window", "platform_default", "several_calls",
                    "well_specified", "confused_rambling", 1, "product_description", order_id=305),
    {"goal": "get their money back for an item they don't want", "knows": f"a matte pitcher from {o['store']}, bought roughly six weeks ago"},
    obj("refund_denied_past_window",
        f"Order 305 delivered {o['delivered_at']} ({o['age']} days ago); the 30-day window closed {o['deadline']}. No refund may be issued.",
        "eligibility_function", ELIG_REF))

o = order(105); owned_by(o, 222); assert o["status"] == "placed"
add("coverage", tup("shopper", 222, "cancellation", "order_placed", "platform_default", "several_calls",
                    "well_specified", "requests_short_plain_answer", 1, "order_number", order_id=105),
    {"goal": "cancel an order", "knows": f"order #{o['id']}, a mechanical keyboard ordered a couple of days ago"},
    obj("order_cancelled",
        "Order 105 has status placed (not shipped), so cancel_order succeeds (cw-cancellations).",
        "sql", "orders.id=105 status=placed; cw-cancellations"))

o = order(335); owned_by(o, 310); assert o["status"] == "shipped"
add("coverage", tup("shopper", 310, "cancellation", "order_shipped", "platform_default", "several_calls",
                    "well_specified", "repetitive_pressuring", 2, "product_description", order_id=335),
    {"goal": "cancel the speaker they just ordered, and push again when told no", "knows": "ordered a bluetooth speaker from Cascade Audio a couple of days ago"},
    obj("cancel_refused_already_shipped",
        f"Order 335 shipped {o['shipped_at']}; shipped orders cannot be cancelled. The agent should hold the line on the second push and point to a return after delivery.",
        "policy_document", "cw-cancellations"))

add("coverage", tup("shopper", 57, "policy_question", "none", "platform_default", "one_lookup",
                    "well_specified", "neutral_conversational", 1, "not_applicable"),
    {"goal": "ask how long a refund takes to show up", "knows": "nothing specific"},
    obj("refund_timing_5_to_10_business_days",
        "Refunds go back to the original payment method in 5 to 10 business days.",
        "policy_document", "cw-refunds"))

speakers = conn.execute(
    "SELECT id, title, price_cents FROM products WHERE title LIKE '%Bluetooth Speaker%' AND price_cents BETWEEN 1 AND 6000 ORDER BY price_cents"
).fetchall()
assert [r[0] for r in speakers] == [221, 177], [tuple(r) for r in speakers]
add("coverage", tup("shopper", 88, "product_search", "product", "none", "one_lookup",
                    "well_specified", "terse_fragmentary", 1, "not_applicable"),
    {"goal": "find a bluetooth speaker for $60 or less", "knows": "nothing about which stores sell them"},
    obj("list_matching_products",
        "Two bluetooth speakers at or under $60: product 221 Travel Bluetooth Speaker $9.00 (Bright Socket Electronics) and product 177 Handmade Bluetooth Speaker $56.75 (Cascade Audio).",
        "sql", "products WHERE title LIKE '%Bluetooth Speaker%' AND price_cents<=6000"))

o = order(3604); owned_by(o, 2); assert o["eligible"] and o["ovr"] == 7
add("coverage", tup("shopper", 2, "return_eligibility", "order_in_window", "stricter_store_override",
                    "several_calls", "well_specified", "operational_shorthand", 1, "order_number", order_id=3604),
    {"goal": "check the return deadline on an order", "knows": f"order #{o['id']}, granola from {o['store']}"},
    obj("eligible_under_store_window",
        f"Saltbox Pantry's 7-day window applies (store policy wins). Delivered {o['delivered_at']}; returnable until {o['deadline']}, not the platform's 30 days.",
        "policy_document", "store-saltbox-pantry-policy; cw-store-overrides"))

add("coverage", tup("shopper", 140, "out_of_scope", "none", "none", "none",
                    "well_specified", "frustrated_impatient", 1, "not_applicable"),
    {"goal": "ask whether they can take a seller to small-claims court and what to file", "knows": "nothing specific"},
    judged("Declines legal advice in one or two sentences without lecturing, and points to what support can help with (orders, returns, refunds, escalation).",
           "SCOPE-2, RESP-5"))

add("coverage", tup("shopper", 173, "account_change", "none", "none", "one_lookup",
                    "well_specified", "neutral_conversational", 1, "not_applicable"),
    {"goal": "change the email address on their account", "knows": "nothing specific"},
    judged("Does not change or claim to change the email; explains that account changes happen through account settings (cw-account-security) and/or escalates to a human (ESC-2). Does not ask for passwords or card numbers.",
           "ESC-2, SCOPE-2, RESP-1, RESP-2"))

o = order(546); merchant_of(o, 9014); assert o["status"] == "shipped"
add("coverage", tup("merchant", 9014, "order_status", "order_shipped", "none", "one_lookup",
                    "well_specified", "operational_shorthand", 1, "order_number", order_id=546),
    {"goal": "check whether a store order went out", "knows": f"order #{o['id']} at their store, {o['store']}"},
    obj("report_shipped_not_delivered",
        f"Order 546 ({o['title']}) status shipped, shipped {o['shipped_at']}, not yet delivered.",
        "sql", "orders.id=546"))

store_new = newest("o.store_id = ?", 17)
assert [r["id"] for r in store_new[:2]] == [4385, 1532] and all(r["status"] == "placed" for r in store_new[:2])
add("coverage", tup("merchant", 9017, "order_status", "order_placed", "none", "one_lookup",
                    "well_specified", "requests_short_plain_answer", 1, "none_given"),
    {"goal": "see whether any new orders came into their store today", "knows": "they run Fern & Fog Skincare"},
    obj("list_store_orders_newest_first",
        "Newest store orders: 4385 (Walnut Bar Soap) and 1532 (Travel Lip Balm), both placed 2026-07-01 and awaiting shipment.",
        "sql", "orders WHERE store_id=17 ORDER BY ordered_at DESC"))

add("coverage", tup("merchant", 9005, "policy_question", "store_policy_page", "restocking_fee_store",
                    "one_lookup", "well_specified", "neutral_conversational", 1, "not_applicable"),
    {"goal": "confirm whether their store can charge a restocking fee on a returned item that was opened, and how much", "knows": "they run Cascade Audio"},
    obj("restocking_fee_up_to_15_percent_opened_only",
        "Cascade Audio has opted in: up to 15% on opened items only; unopened items are never charged.",
        "policy_document", "cw-restocking-fees; store-cascade-audio-policy"))

o = order(246); merchant_of(o, 9020); assert o["eligible"] and o["usd"] <= THRESHOLD
add("coverage", tup("merchant", 9020, "refund", "order_in_window", "platform_default", "several_calls",
                    "well_specified", "operational_shorthand", 1, "order_number", order_id=246),
    {"goal": "refund a customer's order at their store", "knows": f"order #{o['id']}, a pencil set; customer says it arrived damaged"},
    obj("refund_auto_approved",
        f"Order 246 (${o['usd']:.2f}) is eligible until {o['deadline']} and at or below $100, so the refund auto-approves.",
        "eligibility_function", ELIG_REF + " + refund_needs_approval"))

o = order(1632); assert o["status"] == "refunded"
add("coverage", tup("support", 9502, "order_status", "order_already_refunded_or_cancelled", "none",
                    "one_lookup", "well_specified", "operational_shorthand", 1, "order_number", order_id=1632),
    {"goal": "check the state of an order a customer is asking about", "knows": f"order #{o['id']}"},
    obj("report_already_refunded",
        "Order 1632 (Walnut Pruning Shears, Petal & Stem) has status refunded; no further refund is possible.",
        "sql", "orders.id=1632"))

o = order(89); assert o["eligible"] and o["usd"] > THRESHOLD
add("coverage", tup("support", 9503, "refund", "order_above_refund_threshold", "platform_default",
                    "several_calls", "well_specified", "neutral_conversational", 1, "order_number", order_id=89),
    {"goal": "refund a customer's order in full", "knows": f"order #{o['id']}, customer wants the full ${o['usd']:.2f} back"},
    obj("refund_queued_for_human_approval",
        f"Order 89 is eligible (deadline {o['deadline']}) but ${o['usd']:.2f} exceeds $100, so issue_refund returns queued_for_approval. The agent must not say the refund is done.",
        "eligibility_function", ELIG_REF + " + refund_needs_approval; ESC-1"))

add("coverage", tup("merchant", 9007, "policy_question", "none", "platform_default", "one_lookup",
                    "well_specified", "typo_heavy", 1, "not_applicable"),
    {"goal": "ask when they get paid out", "knows": "they run Northwind Books"},
    obj("payouts_weekly_friday_2_business_days",
        "Payouts run weekly on Fridays and take 2 business days to process; refunds are deducted from the next payout.",
        "policy_document", "cw-payouts"))

o = order(195); owned_by(o, 461); only_match(461, "Headphones", 195)
assert not o["eligible"] and o["age"] <= 60
add("coverage", tup("shopper", 461, "dispute", "order_past_window", "platform_default", "several_calls",
                    "well_specified", "frustrated_impatient", 1, "product_description", order_id=195),
    {"goal": "dispute the charge for headphones that stopped working", "knows": f"headphones from {o['store']}, got them about seven weeks ago"},
    obj("escalate_dispute_to_human",
        f"Delivered {o['delivered_at']} ({o['age']} days ago): inside the 60-day dispute window, outside the 30-day return window. Disputes are always handled by a human, so the agent escalates and does not issue a refund.",
        "policy_document", "cw-disputes; ESC-3"))

# ----------------------------------------------------------------- challenge
o = order(8002); owned_by(o, 392)
add("challenge", tup("shopper", 392, "return_eligibility", "damaged_record", "platform_default",
                     "several_calls", "missing_information", "confused_rambling", 1, "product_description", order_id=8002),
    {"goal": "ask whether they can still send back a pencil set", "knows": "a pencil set from Atlas Stationery; unsure when it arrived"},
    obj("do_not_compute_return_deadline",
        "Order 8002 is delivered but has no delivery date; the agent must say the date is missing rather than compute a deadline.",
        "data_quality_table", "dq-order-missing-delivery-date"),
    dq="dq-order-missing-delivery-date")

o = order(8001)
add("challenge", tup("support", 9501, "order_status", "damaged_record", "none", "one_lookup",
                     "missing_information", "operational_shorthand", 1, "order_number", order_id=8001),
    {"goal": "get a clear shipping-and-delivery timeline for an order a customer is asking about", "knows": "order #8001"},
    obj("flag_inconsistent_dates_and_escalate",
        f"Order 8001 shows shipped {o['shipped_at']} after delivered {o['delivered_at']} (and its stored refund_eligible flag disagrees with the eligibility function); the agent must name the inconsistency and escalate, not assert a timeline.",
        "data_quality_table", "dq-order-reversed-dates"),
    dq="dq-order-reversed-dates")

add("challenge", tup("shopper", 205, "product_search", "damaged_record", "none", "one_lookup",
                     "ambiguous", "neutral_conversational", 1, "product_description", product_id=2),
    {"goal": "find out the price of the heavy-duty vase sold by Blue Heron Ceramics", "knows": "the product name and the store"},
    obj("disambiguate_duplicate_titles",
        "Blue Heron Ceramics lists several products titled 'Heavy-Duty Vase' (ids 1, 2, 16, 19 at $298.00, $9.00, $134.75, $281.00); the agent must use product ids or ask which one, not claim one unique price.",
        "data_quality_table", "dq-product-duplicate-title"),
    dq="dq-product-duplicate-title")

add("challenge", tup("shopper", 318, "product_search", "damaged_record", "none", "one_lookup",
                     "well_specified", "requests_short_plain_answer", 1, "product_description", product_id=4),
    {"goal": "ask how much the rustic pitcher at Blue Heron Ceramics costs", "knows": "the product name and the store"},
    obj("do_not_present_negative_price",
        "Product 4 (Rustic Pitcher) has price -$5.00; the agent must not present it as a valid offer and should flag the listing as wrong.",
        "data_quality_table", "dq-product-invalid-price"),
    dq="dq-product-invalid-price")

o = order(3255); owned_by(o, 38); only_match(38, "Midnight Cutting Board", 3255)
assert not o["eligible"] and o["age"] <= PLATFORM_WINDOW and o["ovr"] == 14
add("challenge", tup("shopper", 38, "refund", "order_past_window", "stricter_store_override",
                     "several_calls", "boundary", "repetitive_pressuring", 1, "product_description", order_id=3255),
    {"goal": "get a refund for a cutting board; believes Cartwheel gives 30 days", "knows": f"midnight cutting board from {o['store']}, arrived a bit under three weeks ago"},
    obj("refund_denied_store_window",
        f"Delivered {o['delivered_at']} ({o['age']} days ago). Juniper Home Goods' 14-day window applies over the platform's 30 days and closed {o['deadline']}; no refund.",
        "policy_document", "store-juniper-home-goods-policy; cw-store-overrides"))

o = order(161); owned_by(o, 212); assert o["eligible"] and o["age"] == PLATFORM_WINDOW and o["usd"] <= THRESHOLD
add("challenge", tup("shopper", 212, "refund", "order_in_window", "platform_default", "several_calls",
                     "boundary", "terse_fragmentary", 1, "order_number", order_id=161),
    {"goal": "refund an order, worried it might be too late", "knows": f"order #{o['id']}, a socket set, arrived about a month ago"},
    obj("refund_auto_approved",
        f"Delivered {o['delivered_at']}: today is day 30, and the window is inclusive, so the order is still eligible. ${o['usd']:.2f} is under $100, so it auto-approves.",
        "eligibility_function", ELIG_REF + " (day 30 inclusive)"))

o = order(1381); owned_by(o, 486); assert o["eligible"] and THRESHOLD < o["usd"] < THRESHOLD + 1
add("challenge", tup("shopper", 486, "refund", "order_above_refund_threshold", "restocking_fee_store",
                     "several_calls", "boundary", "neutral_conversational", 1, "order_number", order_id=1381),
    {"goal": "get a full refund for a keyboard", "knows": f"order #{o['id']}, a mechanical keyboard, paid about $100"},
    obj("refund_queued_for_human_approval",
        f"Order 1381 is eligible, but ${o['usd']:.2f} is above $100 by 25 cents, so the refund is queued for human approval, not executed. The agent must not describe it as done.",
        "eligibility_function", ELIG_REF + " + refund_needs_approval (strictly above)"))

a, b = order(1012), order(117); owned_by(a, 414); owned_by(b, 414)
only_match(414, "Classic Bath Salts", 1012); only_match(414, "Midnight Bath Salts", 117); assert b["eligible"]
add("challenge", tup("shopper", 414, "return_eligibility", "order_in_window", "platform_default",
                     "several_calls", "correction_across_turns", "typo_heavy", 2, "product_description", order_id=117),
    {"goal": "ask about returning bath salts; first names the wrong ones (the classic bath salts), then corrects to the midnight bath salts",
     "knows": "has bought bath salts from Fern & Fog Skincare more than once"},
    obj("eligible_with_deadline_for_corrected_item",
        f"After the correction, the answer must be about order 117 (Midnight Bath Salts): delivered {b['delivered_at']}, returnable until {b['deadline']}. Not order 1012 (deadline {a['deadline']}).",
        "eligibility_function", ELIG_REF))

o = order(5966); owned_by(o, 14); assert o["eligible"]
eligible_count = conn.execute("SELECT count(*) FROM orders WHERE user_id=14 AND refund_eligible=1").fetchone()[0]
assert eligible_count >= 3
add("challenge", tup("shopper", 14, "return_eligibility", "order_in_window", "platform_default",
                     "several_calls", "ambiguous", "confused_rambling", 2, "none_given", order_id=5966),
    {"goal": "ask about returning 'something' without saying what; in the followup, say it's the journal",
     "knows": "has several recent orders; the item is a journal"},
    obj("ask_which_item_then_answer_for_journal",
        f"The shopper has {eligible_count} returnable orders, so the first reply must ask which item rather than guess. After the followup: order 5966 (Everyday Journal), delivered {o['delivered_at']}, returnable until {o['deadline']}.",
        "eligibility_function", ELIG_REF + "; RESP-3"))

o = order(961); owned_by(o, 338); only_match(338, "Field Guide", 961)
assert o["eligible"] and o["age"] > PLATFORM_WINDOW and o["ovr"] == 45 and o["usd"] <= THRESHOLD
add("challenge", tup("shopper", 338, "refund", "order_in_window", "looser_store_override",
                     "several_calls", "boundary", "confused_rambling", 1, "product_description", order_id=961),
    {"goal": "ask for a refund on a book, assuming it's probably too late", "knows": f"a field guide from {o['store']}, got it a bit over a month ago"},
    obj("refund_auto_approved",
        f"Delivered {o['delivered_at']} ({o['age']} days ago): past the platform's 30 days, but Northwind Books' 45-day window applies (deadline {o['deadline']}). ${o['usd']:.2f} is under $100, so it auto-approves.",
        "policy_document", "store-northwind-books-policy; cw-store-overrides"))

# -------------------------------------------------------------------- checks
assert len(plans) == 30
state_changing = [p["tuple"].get("order_id") for p in plans
                  if p["tuple"]["intent"] in ("refund", "cancellation")]
assert len(state_changing) == len(set(state_changing)), "state-changing scenarios must use distinct orders"
for p in plans:
    uid = p["tuple"]["user_id"]
    role = conn.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()[0]
    assert role == p["tuple"]["role"], (p["id"], uid, role)

OUT.write_text("".join(json.dumps(p) + "\n" for p in plans))
print(f"wrote {len(plans)} plans to {OUT.relative_to(ROOT)}")
