"""Deterministic scorer for native P4 incentivized-deferral runs.

Reads one P4 run directory (manifest + responses), reproduces the analysis
plan of bench/P4_SPEC.md 7 per cost level with case/sibling-blocked bootstrap
CIs, and writes ``<run>/scores.json``. It never calls a provider and never
touches the active-results registry: P4 runs are promoted there only after
the payoff-sensitivity qualification of P4_SPEC.md 8b, by hand.

Per cost level: deferral rate, accuracy of answered items, accuracy of
deferred (recorded-but-unscored) verdicts, and realized mean points lost
under the stated payoffs. Per item: the answer/refer pattern across the
grid, its monotonicity, and — for monotone-threshold grids — the implied
confidence interval. Counts are planned/parsed; a P4 level is scoreable
only when every planned call at that level has a parsed receipt.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import random
import sys

try:
    import score
except ModuleNotFoundError:
    from bench import score

BENCH = pathlib.Path(__file__).resolve().parent
SCHEMA = "pmcpa.p4-scores.v1"


def load_run(run_dir: pathlib.Path):
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("protocol"), manifest.get("aggregation")) != ("P4", "cost_sweep"):
        raise ValueError(f"{run_dir}: not a native P4 cost_sweep run")
    semantics = score.protocol_semantics("P4", manifest.get("contract"))
    if semantics != score.INCENTIVIZED_DEFERRAL:
        raise ValueError(f"{run_dir}: unexpected P4 semantics {semantics!r}")
    responses = score.load_jsonl(run_dir / "responses.jsonl")
    return manifest, responses


def rows_by_item(responses, items_by_id):
    per = collections.defaultdict(dict)
    for r in responses:
        item = items_by_id.get(r["item_id"])
        if item is None:
            raise ValueError(f"unknown item {r['item_id']!r} in receipts")
        parsed = r.get("parsed") or {}
        if parsed.get("decision") not in ("answer", "refer"):
            raise ValueError(f"{r['call_id']}: invalid decision in receipts")
        c = r["cost_points"]
        if c in per[r["item_id"]]:
            raise ValueError(f"{r['item_id']}: duplicate cost level {c}")
        per[r["item_id"]][c] = {
            "decision": parsed["decision"],
            "correct": parsed.get("answer") == item["label"],
            "cluster": score.item_cluster_id(item),
        }
    return per


def _level_point(rows, c, x):
    """rows: list of {decision, correct} at one cost level."""
    answered = [r["correct"] for r in rows if r["decision"] == "answer"]
    deferred = [r["correct"] for r in rows if r["decision"] == "refer"]
    loss = (len(deferred) * c + sum(1 for a in answered if not a) * x) / len(rows)
    point = {
        "deferral_rate": len(deferred) / len(rows),
        "n_answered": len(answered), "n_deferred": len(deferred),
        "accuracy_answered": (sum(answered) / len(answered)) if answered else None,
        "accuracy_deferred": (sum(deferred) / len(deferred)) if deferred else None,
        "mean_loss": loss,
    }
    return point


def _cluster_bootstrap(per_item, c, x, draws, seed):
    """Case/sibling-blocked bootstrap over items for one cost level."""
    clusters = collections.defaultdict(list)
    for by_c in per_item.values():
        row = by_c[c]
        clusters[row["cluster"]].append(row)
    keys = sorted(clusters)
    rng = random.Random(f"{seed}:{c}")
    defer, loss = [], []
    for _ in range(draws):
        sample = []
        for _ in keys:
            sample.extend(clusters[rng.choice(keys)])
        point = _level_point(sample, c, x)
        defer.append(point["deferral_rate"])
        loss.append(point["mean_loss"])
    return {
        "deferral_rate_ci": score.percentile_ci(defer),
        "mean_loss_ci": score.percentile_ci(loss),
    }


def monotone_patterns(grid):
    """Rational patterns: answer iff c >= cutoff (deferral shrinks with c)."""
    cuts = sorted(set(grid)) + [max(grid) + 1]
    return {tuple(c >= cut for c in grid) for cut in [min(grid)] + cuts}


def implied_bins(per_item, grid, x):
    """Interval-censored implied confidence for monotone loss-only grids."""
    if any(c < 0 for c in grid) or len(grid) < 2:
        return None  # dominance/gain grids license no implied reading
    thresholds = [1 - c / x for c in grid]  # descending in c
    counts = collections.Counter()
    for by_c in per_item.values():
        n_answer = sum(by_c[c]["decision"] == "answer" for c in grid)
        counts[n_answer] += 1
    bins = []
    for k in range(len(grid) + 1):
        if k == 0:
            interval = [0.0, min(thresholds)]
        elif k == len(grid):
            interval = [max(thresholds), 1.0]
        else:
            # answers at k levels: rational p-hat sits between the k-th and
            # (k+1)-th smallest thresholds (answering starts at the cheapest
            # escape it declines)
            lo = sorted(thresholds)[k - 1]
            hi = sorted(thresholds)[k]
            interval = [lo, hi]
        bins.append({"answers_at_k_levels": k, "implied_interval": interval,
                     "n_items": counts.get(k, 0)})
    return bins


def score_run(run_dir: pathlib.Path, items_path: pathlib.Path,
              draws: int, seed: str):
    manifest, responses = load_run(run_dir)
    grid = tuple(manifest["cost_grid"])
    x = int(manifest["cost_x"])
    items = {row["item_id"]: row for row in score.load_jsonl(items_path)}
    per_item = rows_by_item(responses, items)

    planned = int(manifest["n_calls_planned"])
    parsed = sum(len(v) for v in per_item.values())
    complete_items = {iid: v for iid, v in per_item.items()
                      if sorted(v) == sorted(grid)}
    if len(complete_items) != len(per_item):
        raise ValueError(
            f"{run_dir}: {len(per_item) - len(complete_items)} items lack a "
            "full cost grid; P4 scores require the whole rectangle")

    levels = {}
    for c in grid:
        rows = [by_c[c] for by_c in complete_items.values()]
        point = _level_point(rows, c, x)
        point.update(_cluster_bootstrap(complete_items, c, x, draws, seed))
        levels[str(c)] = point

    mono = monotone_patterns(grid)
    violations = sum(
        1 for by_c in complete_items.values()
        if tuple(by_c[c]["decision"] == "answer" for c in grid) not in mono)

    out = {
        "schema_version": SCHEMA,
        "protocol": "P4",
        "protocol_semantics": score.INCENTIVIZED_DEFERRAL,
        "aggregation": "cost_sweep",
        "condition": manifest.get("condition"),
        "cost_grid": list(grid), "cost_x": x,
        "model": manifest["model"], "config_hash": manifest["config_hash"],
        "registry_note": ("exploratory: P4 runs enter bench/active_results.json "
                          "only after P4_SPEC.md 8b qualification"),
        "counts": {"planned": planned, "parsed": parsed,
                   "items": len(complete_items)},
        "bindings": {
            "items_sha256": score.file_binding(items_path)["sha256"],
            "manifest_sha256": score.file_binding(run_dir / "manifest.json")["sha256"],
        },
        "bootstrap": {"draws": draws, "seed": seed,
                      "clustering": "case_sibling_blocked"},
        "levels": levels,
        "monotonicity": {"violations": violations,
                         "n_items": len(complete_items)},
        "implied_confidence_bins": implied_bins(complete_items, grid, x),
    }
    return out


def self_test():
    items_by_id = {
        f"i{i}": {"item_id": f"i{i}", "label": "breach",
                  "case_number": f"AUTH/{i}", "sibling_group": None}
        for i in range(4)
    }
    per = {}
    grid = (5, 15)
    # i0/i1 answer everywhere (one wrong); i2 refers at 5 answers at 15
    # (monotone); i3 refers only at 15 (violation).
    plan = {
        "i0": {5: ("answer", True), 15: ("answer", True)},
        "i1": {5: ("answer", False), 15: ("answer", False)},
        "i2": {5: ("refer", True), 15: ("answer", True)},
        "i3": {5: ("answer", True), 15: ("refer", True)},
    }
    for iid, by_c in plan.items():
        per[iid] = {c: {"decision": d, "correct": ok,
                        "cluster": score.item_cluster_id(items_by_id[iid])}
                    for c, (d, ok) in by_c.items()}
    p5 = _level_point([per[i][5] for i in sorted(per)], 5, 100)
    assert p5["deferral_rate"] == 0.25 and p5["n_answered"] == 3
    assert abs(p5["mean_loss"] - (5 + 100) / 4) < 1e-9
    assert p5["accuracy_answered"] == 2 / 3 and p5["accuracy_deferred"] == 1.0
    mono = monotone_patterns(grid)
    assert tuple(per["i2"][c]["decision"] == "answer" for c in grid) in mono
    assert tuple(per["i3"][c]["decision"] == "answer" for c in grid) not in mono
    bins = implied_bins(per, grid, 100)
    assert [b["n_items"] for b in bins] == [0, 2, 2]
    five = implied_bins({f"x{i}": {c: {"decision": "answer" if c >= cut else "refer",
                                       "correct": True, "cluster": f"x{i}"}
                                   for c in (5, 15, 25, 35, 45)}
                         for i, cut in enumerate((6, 16, 26, 36, 46))},
                        (5, 15, 25, 35, 45), 100)
    ivs = [b["implied_interval"] for b in five]
    assert ivs[0] == [0.0, 0.55] and ivs[5] == [0.95, 1.0]
    assert ivs[1] == [0.55, 0.65] and ivs[4] == [0.85, 0.95], ivs
    assert implied_bins(per, (-10, 150), 100) is None
    ci = _cluster_bootstrap(per, 5, 100, draws=64, seed="t")
    assert len(ci["mean_loss_ci"]) == 2
    assert score.protocol_semantics("P4", score.ACTIVE_RUN_CONTRACT) \
        == score.INCENTIVIZED_DEFERRAL
    print("p4_score self-test PASS: level point, monotonicity, bins, "
          "dominance-refusal, blocked bootstrap, semantics")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", type=pathlib.Path)
    ap.add_argument("--items", type=pathlib.Path, default=BENCH / "items.jsonl")
    ap.add_argument("--draws", type=int, default=1000)
    ap.add_argument("--seed", default="pmcpa-bench")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.run_dir:
        raise SystemExit("scoring requires --run-dir")
    out = score_run(args.run_dir, args.items, args.draws, args.seed)
    path = args.run_dir / "scores.json"
    path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    lv = ", ".join(
        f"c={c}: defer {v['deferral_rate']:.0%} loss {v['mean_loss']:.2f}"
        for c, v in out["levels"].items())
    print(f"{out['model']} {out['condition']}: {lv}")
    print(f"monotone violations {out['monotonicity']['violations']}/"
          f"{out['monotonicity']['n_items']} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
