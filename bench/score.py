"""Score a run: accuracy, Brier, ECE, AUROC, reliability bins, case-blocked CIs.

    python3 bench/score.py --run bench/runs/20260802T101500Z
    python3 bench/score.py --run <dir> --items bench/items.jsonl --draws 1000
    python3 bench/score.py --self-test        # indifference-fit unit tests, no run needed

Reads <run>/manifest.json and <run>/responses.jsonl, joins them to the item
bank, and writes <run>/scores.json plus a short readable summary.

Confidence is scored in the confidence-of-correctness frame, so P1, P2 and P3
land on the same axis:

    p = the model's confidence in the answer it gave
        P2 -> the stated probability
        P1 -> the frequency of the modal answer across the K perturbed calls
        P3 -> the indifference point implied by the lottery-vs-sure choices
    o = 1 if that answer matches the adjudicated label, else 0

    Brier = mean((p - o)^2)          lower is better
    ECE   = sum_b (n_b/N) |acc_b - conf_b|   over 10 equal-mass bins
    AUROC = P(p higher on a correct answer than on a wrong one), ties at 0.5

P3 scores exactly like P2 once the indifference point is fitted, plus a
P3-specific block: the switching-consistency rate (how often the choice pattern
is monotone in c), the non-monotone items, and how many items were censored at
a cap because the model never switched. A non-monotone pattern is a FINDING --
the prompt explicitly tells the model consistency is not required -- so it is
reported, never dropped.

Equal-mass ("adaptive") binning matters for P1, where p can only take K+1
values: fixed-width bins would leave most of them empty. Bin edges are extended
so that items sharing a p never straddle two bins.

Confidence intervals are CASE-BLOCKED: the bootstrap resamples cases, not
items. Clause-level items from one case share narrative text and are strongly
correlated, so item-level resampling would understate the interval
(DESIGN.md §9.3).
"""

import argparse
import json
import math
import pathlib
import random
import sys

BENCH = pathlib.Path(__file__).resolve().parent
DEFAULT_ITEMS = BENCH / "items.jsonl"
TASKS = ("T1", "T1-triage", "T3", "T4")

# How far past the ends of the sure-amount grid a censored item is placed. The
# data cannot locate an indifference point outside the grid, only bound it, so
# the cap is deliberately close to the edge: on the default grid
# (0.55 .. 0.95) this gives 0.53 and 0.97, exactly as DESIGN's P3 spec asks.
CAP_MARGIN = 0.02


def load_jsonl(path):
    out = []
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# --- metrics ---------------------------------------------------------------

def accuracy(rows):
    return sum(r["correct"] for r in rows) / len(rows) if rows else None


def brier(rows):
    return sum((r["p"] - r["correct"]) ** 2 for r in rows) / len(rows) if rows else None


def adaptive_bins(rows, nbins=10):
    """Equal-mass bins; never split a group of equal p across two bins."""
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: (r["p"], r["item_id"]))
    n = len(ordered)
    bins = []
    start = 0
    for i in range(nbins):
        if start >= n:
            break
        target = round((i + 1) * n / nbins)
        end = max(target, start + 1)
        end = min(end, n)
        while end < n and ordered[end]["p"] == ordered[end - 1]["p"]:
            end += 1
        chunk = ordered[start:end]
        conf = sum(r["p"] for r in chunk) / len(chunk)
        acc = sum(r["correct"] for r in chunk) / len(chunk)
        bins.append({
            "bin": len(bins),
            "n": len(chunk),
            "p_min": chunk[0]["p"],
            "p_max": chunk[-1]["p"],
            "confidence": conf,
            "accuracy": acc,
            "gap": acc - conf,
        })
        start = end
    return bins


def ece(rows, nbins=10):
    if not rows:
        return None
    n = len(rows)
    return sum(b["n"] / n * abs(b["gap"]) for b in adaptive_bins(rows, nbins))


def auroc(rows):
    """Discrimination: does p rank correct answers above wrong ones?

    Mann-Whitney U with mid-ranks, so the K+1 distinct values P1 can produce
    (and the handful P3 can) are handled as ties rather than broken arbitrarily.
    None when one class is empty -- AUROC is undefined, not 0.5.
    """
    pos = [r["p"] for r in rows if r["correct"] == 1]
    neg = [r["p"] for r in rows if r["correct"] == 0]
    if not pos or not neg:
        return None
    ordered = sorted(pos + neg)
    ranks, i = {}, 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[i]:
            j += 1
        ranks[ordered[i]] = (i + j) / 2.0 + 1.0
        i = j + 1
    rank_sum = sum(ranks[v] for v in pos)
    return (rank_sum - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def metric_set(rows):
    return {"n": len(rows), "accuracy": accuracy(rows), "brier": brier(rows),
            "ece": ece(rows), "auroc": auroc(rows)}


# --- P3: the indifference point ---------------------------------------------

def logistic_fit(cs, ys, iters=200, ridge=1e-6):
    """Newton-IRLS fit of P(choose sure | c) = sigmoid(a + b*c).

    Ridge-regularised so a near-separable pattern cannot blow the Hessian up;
    deterministic (fixed start, fixed iteration cap). Only used for
    non-monotone patterns -- monotone and degenerate ones are read off directly,
    where the MLE is at infinity and a fit would be meaningless.
    """
    a = b = 0.0
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for c, y in zip(cs, ys):
            z = max(-30.0, min(30.0, a + b * c))
            mu = 1.0 / (1.0 + math.exp(-z))
            g0 += y - mu
            g1 += (y - mu) * c
            w = mu * (1.0 - mu)
            h00 += w
            h01 += w * c
            h11 += w * c * c
        g0 -= ridge * a
        g1 -= ridge * b
        h00 += ridge
        h11 += ridge
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (-h01 * g0 + h00 * g1) / det
        a += da
        b += db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return a, b


def _midpoint_from_count(cs, n_lottery, cap_low, cap_high):
    """Where a monotone switcher who chose the lottery n_lottery times switched."""
    if n_lottery == 0:
        return cap_low          # sure at every c -> indifference below the grid
    if n_lottery == len(cs):
        return cap_high         # lottery at every c -> indifference above the grid
    return (cs[n_lottery - 1] + cs[n_lottery]) / 2.0


def fit_indifference(choices):
    """(c, choice) pairs -> implied P(my answer matches the adjudicator).

    A model that prefers the lottery at c is revealing that it rates its own
    answer's chance of being right above c; preferring the sure amount reveals
    the opposite. The indifference point is that probability.

      monotone with a switch   -> midpoint of the switching interval
      always lottery           -> censored above the grid, capped
      always sure              -> censored below the grid, capped
      non-monotone             -> logistic regression of choice on c, and the
                                  item is FLAGGED (score.py reports the rate)

    Returns None if there are no usable choices.
    """
    pairs = sorted((float(c), ch) for c, ch in choices)
    if not pairs:
        return None
    cs = [c for c, _ in pairs]
    ys = [1 if ch == "sure" else 0 for _, ch in pairs]
    cap_low = max(0.001, min(cs) - CAP_MARGIN)
    cap_high = min(0.999, max(cs) + CAP_MARGIN)
    n_lottery = ys.count(0)
    # monotone == every 'sure' sits at a higher c than every 'lottery'
    monotone = all(ys[i] <= ys[i + 1] for i in range(len(ys) - 1))
    pattern = "".join("S" if y else "L" for y in ys)

    if monotone:
        p = _midpoint_from_count(cs, n_lottery, cap_low, cap_high)
        method = ("cap_low" if n_lottery == 0 else
                  "cap_high" if n_lottery == len(cs) else "midpoint")
    else:
        a, b = logistic_fit(cs, [float(y) for y in ys])
        if b > 1e-9 and math.isfinite(a) and math.isfinite(b):
            p = min(cap_high, max(cap_low, -a / b))
            method = "logistic"
        else:
            # A fit that is flat or runs the wrong way (preferring the SURE
            # payoff at low c and the lottery at high c) carries no indifference
            # point. Fall back to where a monotone switcher with the same number
            # of lottery choices would have switched, and keep the flag.
            p = _midpoint_from_count(cs, n_lottery, cap_low, cap_high)
            method = "count_midpoint"

    censored = ("high" if method == "cap_high" else
                "low" if method == "cap_low" else None)
    switch = None
    if method == "midpoint":
        switch = [cs[n_lottery - 1], cs[n_lottery]]
    return {"p": p, "method": method, "monotone": monotone, "censored": censored,
            "pattern": pattern, "n_choices": len(pairs), "c_grid": cs,
            "switch_interval": switch, "caps": [cap_low, cap_high]}


def bootstrap(rows, draws, seed):
    """Case-blocked percentile CIs. Resample cases with replacement."""
    if not rows:
        return None
    by_case = {}
    for r in rows:
        by_case.setdefault(r["case_number"], []).append(r)
    cases = sorted(by_case)
    rng = random.Random(seed)
    acc, bri, ec, auc = [], [], [], []
    for _ in range(draws):
        drawn = []
        for _ in range(len(cases)):
            drawn.extend(by_case[cases[rng.randrange(len(cases))]])
        acc.append(accuracy(drawn))
        bri.append(brier(drawn))
        ec.append(ece(drawn))
        auc.append(auroc(drawn))

    def ci(values):
        vals = sorted(v for v in values if v is not None)
        if not vals:
            return None
        lo = vals[int(0.025 * (len(vals) - 1))]
        hi = vals[int(round(0.975 * (len(vals) - 1)))]
        return {"lo": lo, "hi": hi}

    return {"draws": draws, "seed": seed, "n_cases": len(cases),
            "accuracy": ci(acc), "brier": ci(bri), "ece": ci(ec), "auroc": ci(auc)}


# --- aggregation -----------------------------------------------------------

def aggregate(responses, items_by_id, protocol):
    """One scored row per item, plus a tally of what could not be scored."""
    by_item = {}
    for rec in responses:
        by_item.setdefault(rec["item_id"], []).append(rec)

    rows, dropped = [], []
    for item_id, recs in sorted(by_item.items()):
        item = items_by_id.get(item_id)
        if item is None:
            dropped.append((item_id, "no such item in the item bank"))
            continue
        parsed = [r["parsed"] for r in recs if r.get("parsed")]
        if not parsed:
            errs = sorted({r.get("error") or "unparsed" for r in recs})
            dropped.append((item_id, f"no parsed response ({'; '.join(errs)[:80]})"))
            continue

        fit = None
        if protocol == "P3":
            # Two call kinds per item: one verdict, then one choice per c. The
            # verdict fixes the answer; the choices fix the confidence.
            verdicts = [r["parsed"]["answer"] for r in recs
                        if (r.get("variant") or {}).get("stage") == "verdict" and r.get("parsed")]
            choices = [((r["variant"] or {}).get("c"), r["parsed"]["choice"]) for r in recs
                       if (r.get("variant") or {}).get("stage") == "choice"
                       and r.get("parsed") and (r["variant"] or {}).get("c") is not None]
            if not verdicts:
                dropped.append((item_id, "no parsed stage-1 verdict"))
                continue
            fit = fit_indifference(choices)
            if fit is None:
                dropped.append((item_id, "no parsed stage-2 choices"))
                continue
            answer, p, tied = verdicts[0], fit["p"], False
        elif protocol == "P2":
            answer = parsed[0]["answer"]
            p = float(parsed[0]["probability"])
            tied = False
        else:
            counts = {}
            for pr in parsed:
                counts[pr["answer"]] = counts.get(pr["answer"], 0) + 1
            top = max(counts.values())
            winners = sorted(a for a, c in counts.items() if c == top)
            tied = len(winners) > 1
            answer = winners[0]  # deterministic tie-break; flagged
            p = top / len(parsed)

        row = {
            "item_id": item_id,
            "task": item["task"],
            "case_number": item["case_number"],
            "split": item["split"],
            "label": item["label"],
            "answer": answer,
            "p": p,
            "correct": 1 if answer == item["label"] else 0,
            "n_calls": len(recs),
            "n_parsed": len(parsed),
            "modal_tie": tied,
        }
        if fit is not None:
            row["p3"] = fit
        rows.append(row)
    return rows, dropped


def p3_summary(rows):
    """The P3-specific block: is revealed preference even coherent on this item?"""
    fits = [r["p3"] for r in rows if r.get("p3")]
    if not fits:
        return None
    n = len(fits)
    methods = {}
    for f in fits:
        methods[f["method"]] = methods.get(f["method"], 0) + 1
    non_monotone = [r["item_id"] for r in rows if r.get("p3") and not r["p3"]["monotone"]]
    return {
        "n_items_fitted": n,
        "switching_consistency_rate": (n - len(non_monotone)) / n,
        "n_non_monotone": len(non_monotone),
        "non_monotone_items": sorted(non_monotone),
        "non_monotone_patterns": sorted({r["p3"]["pattern"] for r in rows
                                         if r.get("p3") and not r["p3"]["monotone"]}),
        "censored_high": sum(1 for f in fits if f["censored"] == "high"),
        "censored_low": sum(1 for f in fits if f["censored"] == "low"),
        "censored_total": sum(1 for f in fits if f["censored"]),
        "method_counts": methods,
        "choices_per_item_min": min(f["n_choices"] for f in fits),
        "choices_per_item_max": max(f["n_choices"] for f in fits),
        "caps": fits[0]["caps"],
    }


def fmt(x, nd=3):
    return "n/a" if x is None else f"{x:.{nd}f}"


# --- self-test ---------------------------------------------------------------

def self_test():
    """Unit-test the indifference fit on synthetic choice patterns.

    No network, no run directory, no item bank -- just the estimator against
    patterns whose answer is known by construction. Run it after touching
    fit_indifference(); a P3 score is only as trustworthy as this function.
    """
    G = [0.55, 0.65, 0.75, 0.85, 0.95]      # the default grid
    L, S = "lottery", "sure"
    cases = [
        # (name, choices, expected p, expected method, expected monotone)
        ("monotone, switches between 0.75 and 0.85",
         list(zip(G, [L, L, L, S, S])), 0.80, "midpoint", True),
        ("monotone, switches at the bottom of the grid",
         list(zip(G, [L, S, S, S, S])), 0.60, "midpoint", True),
        ("monotone, switches at the top of the grid",
         list(zip(G, [L, L, L, L, S])), 0.90, "midpoint", True),
        ("always lottery -> censored above the grid, capped",
         list(zip(G, [L, L, L, L, L])), 0.97, "cap_high", True),
        ("always sure -> censored below the grid, capped",
         list(zip(G, [S, S, S, S, S])), 0.53, "cap_low", True),
        ("non-monotone (one reversal) -> logistic fit, flagged",
         list(zip(G, [L, S, L, S, S])), None, "logistic", False),
        ("non-monotone, perverse slope -> count midpoint, flagged",
         list(zip(G, [S, S, L, L, L])), 0.80, "count_midpoint", False),
        ("single-point grid, lottery -> capped above that point",
         [(0.75, L)], 0.77, "cap_high", True),
        ("custom low grid, always sure -> cap tracks the grid, not 0.53",
         list(zip([0.10, 0.20, 0.30], [S, S, S])), 0.08, "cap_low", True),
    ]

    failures = []
    print("indifference fit -- synthetic choice patterns")
    print(f"  {'pattern':>8} {'p':>7} {'method':>15} {'mono':>5} {'censored':>9}  case")
    for name, choices, want_p, want_method, want_mono in cases:
        got = fit_indifference(choices)
        ok = True
        if got["method"] != want_method or got["monotone"] != want_mono:
            ok = False
        if want_p is not None and abs(got["p"] - want_p) > 1e-9:
            ok = False
        if want_p is None and not (min(c for c, _ in choices) <= got["p"]
                                   <= max(c for c, _ in choices)):
            ok = False   # a fitted point must land inside the swept grid
        # order of the choices must not matter: the fit sorts by c
        shuffled = list(reversed(choices))
        if abs(fit_indifference(shuffled)["p"] - got["p"]) > 1e-12:
            ok = False
            name += "  [ORDER-SENSITIVE]"
        if not ok:
            failures.append((name, got, want_p, want_method, want_mono))
        print(f"  {got['pattern']:>8} {got['p']:>7.4f} {got['method']:>15} "
              f"{str(got['monotone']):>5} {str(got['censored']):>9}  {name} "
              f"{'ok' if ok else 'FAIL'}")

    if fit_indifference([]) is not None:
        failures.append(("empty choice list must return None", None, None, None, None))

    # AUROC is new here too, and cheap to pin down.
    def rows(ps, cs):
        return [{"p": p, "correct": c, "item_id": str(i)}
                for i, (p, c) in enumerate(zip(ps, cs))]
    auroc_cases = [
        ("perfect separation", rows([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]), 1.0),
        ("reversed", rows([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]), 0.0),
        ("all tied", rows([0.5, 0.5, 0.5, 0.5], [1, 1, 0, 0]), 0.5),
        ("one class only", rows([0.9, 0.8], [1, 1]), None),
    ]
    print("\nAUROC")
    for name, rs, want in auroc_cases:
        got = auroc(rs)
        ok = (got is None and want is None) or (got is not None and want is not None
                                                and abs(got - want) < 1e-12)
        if not ok:
            failures.append((f"auroc: {name}", got, want, None, None))
        print(f"  {fmt(got, 4):>7} (want {fmt(want, 4)})  {name} {'ok' if ok else 'FAIL'}")

    if failures:
        print(f"\nFAILURES: {len(failures)}")
        for f in failures:
            print(f"  {f[0]}: got {f[1]}")
        return 1
    print(f"\nOK: {len(cases)} indifference cases + {len(auroc_cases)} AUROC cases pass.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="", help="a bench/runs/<timestamp> directory")
    ap.add_argument("--self-test", action="store_true",
                    help="unit-test the P3 indifference fit and AUROC, then exit")
    ap.add_argument("--items", default="", help="item bank (default: the path recorded in manifest.json)")
    ap.add_argument("--draws", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20260802)
    ap.add_argument("--out", default="", help="default <run>/scores.json")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.run:
        raise SystemExit("--run is required (or use --self-test)")

    run_dir = pathlib.Path(args.run)
    responses_path = run_dir / "responses.jsonl"
    manifest_path = run_dir / "manifest.json"
    if not responses_path.exists():
        raise SystemExit(f"{responses_path} not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    items_path = pathlib.Path(args.items or manifest.get("items_path") or DEFAULT_ITEMS)
    if not items_path.exists():
        raise SystemExit(f"item bank not found: {items_path}")

    responses = load_jsonl(responses_path)
    items = load_jsonl(items_path)
    items_by_id = {it["item_id"]: it for it in items}

    protocol = manifest.get("protocol") or (responses[0].get("protocol") if responses else "P2")
    rows, dropped = aggregate(responses, items_by_id, protocol)

    per_task = {}
    for task in TASKS:
        sub = [r for r in rows if r["task"] == task]
        if sub:
            per_task[task] = {**metric_set(sub),
                              "reliability": adaptive_bins(sub),
                              "ci": bootstrap(sub, args.draws, args.seed)}

    scores = {
        "run": str(run_dir),
        "items": str(items_path),
        "protocol": protocol,
        "model": manifest.get("model"),
        "k": manifest.get("k"),
        "coverage": {
            "items_in_bank": len(items),
            "items_attempted": len({r["item_id"] for r in responses}),
            "items_scored": len(rows),
            "items_dropped": len(dropped),
            "calls": len(responses),
            "calls_parsed": sum(1 for r in responses if r.get("parsed")),
            "modal_ties": sum(1 for r in rows if r["modal_tie"]),
        },
        "overall": {**metric_set(rows),
                    "reliability": adaptive_bins(rows),
                    "ci": bootstrap(rows, args.draws, args.seed)},
        "per_task": per_task,
        "dropped": [{"item_id": i, "reason": why} for i, why in dropped],
        "bootstrap": {"draws": args.draws, "seed": args.seed, "unit": "case"},
    }
    if protocol == "P3":
        scores["p3"] = p3_summary(rows)
        scores["c_grid"] = manifest.get("c_grid")

    out = pathlib.Path(args.out) if args.out else run_dir / "scores.json"
    out.write_text(json.dumps(scores, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    ov, ci = scores["overall"], scores["overall"]["ci"] or {}
    print(f"run       : {run_dir}")
    print(f"protocol  : {protocol}   model: {manifest.get('model', '?')}   "
          f"K: {manifest.get('k', '?')}")
    print(f"coverage  : {ov['n']} item(s) scored of {scores['coverage']['items_attempted']} attempted "
          f"({scores['coverage']['calls_parsed']}/{scores['coverage']['calls']} calls parsed)")
    if dropped:
        print(f"dropped   : {len(dropped)}  e.g. {dropped[0][0]} -- {dropped[0][1]}")
    if scores["coverage"]["modal_ties"]:
        print(f"modal ties: {scores['coverage']['modal_ties']} (broken alphabetically; p = 0.5 either way)")
    print()
    print(f"{'':<11}{'n':>4}  {'acc':>6} {'[95% CI]':>16}  {'Brier':>6} {'[95% CI]':>16}  "
          f"{'ECE':>6}  {'AUROC':>6}")
    def line(name, block):
        c = block.get("ci") or {}
        acc_ci = c.get("accuracy") or {}
        bri_ci = c.get("brier") or {}
        print(f"{name:<11}{block['n']:>4}  {fmt(block['accuracy']):>6} "
              f"{('[' + fmt(acc_ci.get('lo')) + ', ' + fmt(acc_ci.get('hi')) + ']'):>16}  "
              f"{fmt(block['brier']):>6} "
              f"{('[' + fmt(bri_ci.get('lo')) + ', ' + fmt(bri_ci.get('hi')) + ']'):>16}  "
              f"{fmt(block['ece']):>6}  {fmt(block.get('auroc')):>6}")
    line("overall", ov)
    for task in TASKS:
        if task in per_task:
            line(task, per_task[task])
    print(f"\nCIs are case-blocked: {args.draws} draws over "
          f"{(ci.get('n_cases') if ci else 0)} case(s), seed {args.seed}.")
    if scores.get("p3"):
        b = scores["p3"]
        print(f"\nP3 revealed preference ({b['n_items_fitted']} item(s), "
              f"c grid {scores.get('c_grid')})")
        print(f"  switching consistency : {b['switching_consistency_rate']:.3f} "
              f"({b['n_items_fitted'] - b['n_non_monotone']}/{b['n_items_fitted']} monotone in c)")
        print(f"  non-monotone          : {b['n_non_monotone']}"
              + (f"  patterns {', '.join(b['non_monotone_patterns'])}"
                 if b['non_monotone_patterns'] else ""))
        print(f"  censored at a cap     : {b['censored_total']} "
              f"(high {b['censored_high']} = always took the lottery, "
              f"low {b['censored_low']} = always took the sure payoff; "
              f"caps {b['caps'][0]:.2f}/{b['caps'][1]:.2f})")
        print(f"  fit methods           : "
              f"{', '.join(f'{k}={v}' for k, v in sorted(b['method_counts'].items()))}")
        if b["n_non_monotone"]:
            print("  non-monotone items are FLAGGED, not dropped: the prompt tells the model "
                  "consistency is not required, so the rate is a result.")
    print("\nreliability (equal-mass bins, overall)")
    print(f"  {'bin':>3} {'n':>4} {'p range':>14} {'conf':>6} {'acc':>6} {'gap':>7}")
    for b in ov["reliability"]:
        print(f"  {b['bin']:>3} {b['n']:>4} {fmt(b['p_min'], 2) + '-' + fmt(b['p_max'], 2):>14} "
              f"{fmt(b['confidence']):>6} {fmt(b['accuracy']):>6} {b['gap']:>+7.3f}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
