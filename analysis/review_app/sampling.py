"""Cluster the conversations, project them for the map, and build a sample batch.

Two outputs the review app reads:

  state/graph.json   one node per conversation: {id, x, y, cluster} for the map
                     view, from a 2-component PCA of the structural features.
  state/samples.json the current review batch, plus state/sample_manifest.json
                     recording how each conversation was picked. The manifest
                     is a committed deliverable, so it records the honest
                     reason, including "reviewer read it in id order" for the
                     first pass that happened before any sampling ran.

Clustering uses the same features as the course helper (turn count, tool-call
count, distinct tools, retrieval, tokens) plus tool identity, because in
Cartwheel *which* tool ran separates a refund from a policy lookup far better
than how many ran. No dependencies: k-means and PCA are both short.

    uv run python analysis/review_app/sampling.py --batch uniform:15,cluster:15
    uv run python analysis/review_app/sampling.py --batch dimension:role:30
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "analysis" / "state"
TOOLS = ["get_order", "check_return_eligibility", "search_help_center", "find_order",
         "search_products", "get_policy", "issue_refund", "escalate_to_human",
         "list_my_orders", "cancel_order"]


def load(name: str, default: Any) -> Any:
    path = STATE / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def save(name: str, payload: Any) -> None:
    (STATE / name).write_text(json.dumps(payload, indent=1))


def features(c: dict[str, Any]) -> list[float]:
    f = c["features"]
    used = set(f.get("tools") or [])
    return [
        float(f.get("turn_count", 0)), float(f.get("tool_call_count", 0)),
        float(f.get("distinct_tools", 0)), float(len(c.get("flags") or [])),
        float(f.get("reply_chars", 0)) / 500.0,
        *[1.0 if t in used else 0.0 for t in TOOLS],
    ]


def standardize(vectors: list[list[float]]) -> list[list[float]]:
    cols = list(zip(*vectors))
    means = [sum(c) / len(c) for c in cols]
    sds = [(sum((v - m) ** 2 for v in c) / len(c)) ** 0.5 or 1.0 for c, m in zip(cols, means)]
    return [[(v - m) / s for v, m, s in zip(row, means, sds)] for row in vectors]


def kmeans(vectors: list[list[float]], k: int, seed: int = 7, iters: int = 40) -> list[int]:
    rng = random.Random(seed)
    k = min(k, len(vectors))
    centroids = [vectors[i][:] for i in rng.sample(range(len(vectors)), k)]
    assign = [0] * len(vectors)
    for _ in range(iters):
        changed = False
        for i, v in enumerate(vectors):
            best, best_d = 0, math.inf
            for c, cen in enumerate(centroids):
                d = sum((a - b) ** 2 for a, b in zip(v, cen))
                if d < best_d:
                    best, best_d = c, d
            if assign[i] != best:
                assign[i], changed = best, True
        for c in range(k):
            members = [vectors[i] for i in range(len(vectors)) if assign[i] == c]
            if members:
                centroids[c] = [sum(m[d] for m in members) / len(members) for d in range(len(members[0]))]
        if not changed:
            break
    return assign


def pca2(vectors: list[list[float]]) -> list[tuple[float, float]]:
    """Two principal components by power iteration (no numpy needed)."""
    n, dim = len(vectors), len(vectors[0])
    means = [sum(v[d] for v in vectors) / n for d in range(dim)]
    centered = [[v[d] - means[d] for d in range(dim)] for v in vectors]

    def component(data: list[list[float]]) -> list[float]:
        rng = random.Random(3)
        vec = [rng.gauss(0, 1) for _ in range(dim)]
        for _ in range(80):
            acc = [0.0] * dim
            for row in data:  # acc += row * (row . vec), i.e. covariance @ vec
                dot = sum(r * v for r, v in zip(row, vec))
                for d in range(dim):
                    acc[d] += row[d] * dot
            norm = math.sqrt(sum(a * a for a in acc)) or 1.0
            vec = [a / norm for a in acc]
        return vec

    first = component(centered)
    residual = []
    for row in centered:  # remove the first component, then repeat
        dot = sum(r * f for r, f in zip(row, first))
        residual.append([r - dot * f for r, f in zip(row, first)])
    second = component(residual)
    return [(sum(r * f for r, f in zip(row, first)), sum(r * s for r, s in zip(row, second)))
            for row in centered]


def annotated_ids() -> set[str]:
    data = load("annotations.json", {"annotations": []})
    rows = data.get("annotations", []) if isinstance(data, dict) else data
    return {r.get("trace_id") for r in rows}


def pick_uniform(pool: list[dict], k: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    chosen = rng.sample(pool, min(k, len(pool)))
    return [{"trace_id": c["id"], "reason": "uniform random", "batch": "uniform"} for c in chosen]


def pick_cluster(pool: list[dict], clusters: dict[str, int], k: int, seed: int) -> list[dict]:
    """One representative per cluster, cycling until k are picked."""
    rng = random.Random(seed)
    by_cluster: dict[int, list[dict]] = defaultdict(list)
    for c in pool:
        by_cluster[clusters.get(c["id"], 0)].append(c)
    for members in by_cluster.values():
        rng.shuffle(members)
    out: list[dict] = []
    while len(out) < k and any(by_cluster.values()):
        for cluster, members in sorted(by_cluster.items()):
            if not members or len(out) >= k:
                continue
            c = members.pop()
            out.append({"trace_id": c["id"], "reason": f"representative of cluster {cluster}",
                        "batch": "cluster", "cluster": cluster})
    return out


def pick_dimension(pool: list[dict], field: str, k: int, seed: int) -> list[dict]:
    """Spread k picks evenly across the values of one product dimension."""
    rng = random.Random(seed)
    get = (lambda c: c["meta"].get(field)) if field in ("role",) else (lambda c: c["case"].get(field))
    groups: dict[Any, list[dict]] = defaultdict(list)
    for c in pool:
        groups[get(c)].append(c)
    for members in groups.values():
        rng.shuffle(members)
    out: list[dict] = []
    while len(out) < k and any(groups.values()):
        for value, members in sorted(groups.items(), key=lambda kv: str(kv[0])):
            if not members or len(out) >= k:
                continue
            c = members.pop()
            out.append({"trace_id": c["id"], "reason": f"{field} = {value}",
                        "batch": f"dimension:{field}", field: value})
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", default="uniform:15,cluster:15",
                        help="comma list: uniform:N, cluster:N, dimension:FIELD:N")
    parser.add_argument("--clusters", type=int, default=8)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--keep-existing", action="store_true",
                        help="append to the current sample instead of replacing it")
    args = parser.parse_args()

    conversations = load("conversations.json", [])
    if not conversations:
        raise SystemExit("run analysis/review_app/load.py first")

    vectors = standardize([features(c) for c in conversations])
    assignment = kmeans(vectors, args.clusters, seed=args.seed)
    coords = pca2(vectors)
    clusters = {c["id"]: a for c, a in zip(conversations, assignment)}
    save("graph.json", {
        "nodes": [{"id": c["id"], "x": round(x, 4), "y": round(y, 4), "cluster": cl,
                   "role": c["meta"].get("role"), "intent": c["case"].get("intent")}
                  for c, (x, y), cl in zip(conversations, coords, assignment)],
        "clusters": sorted(set(assignment)),
        "features": ["turn_count", "tool_call_count", "distinct_tools", "flags", "reply_chars", *TOOLS],
    })

    reviewed = annotated_ids()
    existing = load("samples.json", [])
    existing = existing if isinstance(existing, list) else []
    already = {s.get("trace_id") for s in existing} if args.keep_existing else set()

    # Conversations already read in the first pass are recorded as their own
    # batch, so the manifest states honestly how every reviewed trace arrived.
    picked: list[dict] = list(existing) if args.keep_existing else [
        {"trace_id": tid, "reason": "read in id order during the reviewer's first pass",
         "batch": "first_pass"} for tid in sorted(reviewed)
    ]
    already |= {p["trace_id"] for p in picked}

    for spec in args.batch.split(","):
        if not spec.strip():
            continue
        parts = spec.split(":")
        pool = [c for c in conversations if c["id"] not in already]
        if parts[0] == "uniform":
            new = pick_uniform(pool, int(parts[1]), args.seed)
        elif parts[0] == "cluster":
            new = pick_cluster(pool, clusters, int(parts[1]), args.seed)
        elif parts[0] == "dimension":
            new = pick_dimension(pool, parts[1], int(parts[2]), args.seed)
        else:
            raise SystemExit(f"unknown batch type: {parts[0]}")
        picked.extend(new)
        already |= {p["trace_id"] for p in new}

    for row in picked:
        row["cluster"] = clusters.get(row["trace_id"])
        row["reviewed"] = row["trace_id"] in reviewed
    save("samples.json", picked)
    save("sample_manifest.json", {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "analysis/state/conversations.json (Module 1 traces, grouped by session)",
        "population": len(conversations),
        "clustering": {"k": args.clusters, "seed": args.seed,
                       "features": ["turn_count", "tool_call_count", "distinct_tools",
                                    "flag_count", "reply_chars", "tool identity (10 flags)"],
                       "sizes": dict(Counter(assignment))},
        "batches": dict(Counter(p["batch"] for p in picked)),
        "selections": picked,
    })
    print(f"{len(conversations)} conversations, {args.clusters} clusters")
    print("batches:", dict(Counter(p["batch"] for p in picked)))
    print(f"sample now {len(picked)} conversations ({sum(p['reviewed'] for p in picked)} already reviewed)")


if __name__ == "__main__":
    main()
