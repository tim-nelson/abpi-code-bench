"""SP-vs-P4 matched-budget comparison (offline; no model calls).

The question P4 was designed to answer (P4_SPEC.md 7.3): at each core cost
level c, the model self-defers some number of items. A deployer running
selective prediction instead refers the SAME NUMBER of items — those with
the lowest stated confidence — and pays the same c per referral. Whoever
loses fewer points per item under the stated payoffs has the better
deferral policy at that oracle budget.

Rows per (model, c): realized P4 loss; SP loss thresholding P1 stated
probability; SP loss thresholding the P3 pooled probability; the
always-answer baseline; and the oracle-budget-optimal SP loss (best
coverage in hindsight, an upper bound on what thresholding could do).

Deterministic: ties in confidence break by task rank. Reads receipts only.
"""

from __future__ import annotations

import collections
import json
import pathlib

BENCH = pathlib.Path(__file__).resolve().parent
X = 100
CORE_GRID = (5, 15, 25, 35, 45)

MODELS = {
    "claude-sonnet-5": {
        "p4": "runs/claude-sonnet-5-medium-p4",
        "p1": "runs/claude-sonnet-5-medium-p1",
        "p3": "runs/claude-sonnet-5-medium-p3",
    },
    "gpt-5.6-sol": {
        "p4": "runs/gpt-5.6-sol-medium-p4",
        "p1": "runs/gpt-5.6-sol-medium-p1",
        "p3": "runs/gpt-5.6-sol-medium-p3",
    },
}


def read_jsonl(path):
    with (BENCH / path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def labels():
    return {row["item_id"]: row["label"] for row in read_jsonl("items.jsonl")}


def p4_decisions(run_dir):
    per = collections.defaultdict(dict)
    for r in read_jsonl(f"{run_dir}/responses.jsonl"):
        per[r["item_id"]][r["cost_points"]] = {
            "decision": r["parsed"]["decision"], "answer": r["parsed"]["answer"],
            "rank": r["task_rank"],
        }
    return per


def p1_view(run_dir):
    """item -> (answer, confidence in that answer, rank), T1 ranks 1..100."""
    out = {}
    for r in read_jsonl(f"{run_dir}/responses.jsonl"):
        if r["task"] == "T1" and r["task_rank"] <= 100:
            p = r["parsed"]
            out[r["item_id"]] = (p["answer"], float(p["probability"]), r["task_rank"])
    return out


def p3_view(run_dir, positive="breach"):
    """Equal-weight linear pool oriented to the positive answer, then the
    pooled modal answer with the pooled confidence in it."""
    per = collections.defaultdict(list)
    rank = {}
    for r in read_jsonl(f"{run_dir}/responses.jsonl"):
        if r["task"] == "T1" and r["task_rank"] <= 100:
            p = r["parsed"]
            oriented = p["probability"] if p["answer"] == positive else 1.0 - p["probability"]
            per[r["item_id"]].append(oriented)
            rank[r["item_id"]] = r["task_rank"]
    out = {}
    for item_id, oriented in per.items():
        pooled = sum(oriented) / len(oriented)
        if pooled >= 0.5:
            out[item_id] = (positive, pooled, rank[item_id])
        else:
            out[item_id] = ("no_breach", 1.0 - pooled, rank[item_id])
    return out


def sp_loss(view, truth, n_refer, c):
    """Deployer refers the n_refer least-confident items at c points each."""
    ordered = sorted(view.items(), key=lambda kv: (kv[1][1], kv[1][2]))
    referred = {k for k, _ in ordered[:n_refer]}
    loss = n_refer * c
    for item_id, (answer, _, _) in view.items():
        if item_id not in referred and answer != truth[item_id]:
            loss += X
    return loss / len(view)


def sp_optimal(view, truth, c):
    """Best coverage in hindsight for this c (bound on SP thresholding)."""
    return min(sp_loss(view, truth, k, c) for k in range(len(view) + 1))


def main():
    truth = labels()
    print(f"{'model':16s} {'c':>3s} {'P4 self':>8s} {'SP(P1)':>7s} {'SP(P3)':>7s} "
          f"{'always':>7s} {'SP-opt':>7s}  (mean points lost per item, X={X})")
    results = {}
    for model, dirs in MODELS.items():
        per = p4_decisions(dirs["p4"])
        p1 = p1_view(dirs["p1"])
        p3 = p3_view(dirs["p3"])
        assert set(per) == set(p1) == set(p3), f"{model}: item sets differ"
        rows = []
        for c in CORE_GRID:
            p4_loss = 0
            n_refer = 0
            for item_id, by_c in per.items():
                d = by_c[c]
                if d["decision"] == "refer":
                    p4_loss += c
                    n_refer += 1
                elif d["answer"] != truth[item_id]:
                    p4_loss += X
            p4_loss /= len(per)
            always = sp_loss(p1, truth, 0, c)
            row = {
                "c": c, "p4_self": p4_loss, "p4_n_refer": n_refer,
                "sp_p1_matched": sp_loss(p1, truth, n_refer, c),
                "sp_p3_matched": sp_loss(p3, truth, n_refer, c),
                "always_answer": always,
                "sp_p1_optimal": sp_optimal(p1, truth, c),
            }
            rows.append(row)
            print(f"{model:16s} {c:3d} {row['p4_self']:8.2f} {row['sp_p1_matched']:7.2f} "
                  f"{row['sp_p3_matched']:7.2f} {row['always_answer']:7.2f} "
                  f"{row['sp_p1_optimal']:7.2f}   (model referred {n_refer})")
        results[model] = rows
    return results


if __name__ == "__main__":
    main()
