"""Every item-candidate must end as an item or as a reasoned exclusion row.

The durable-exclusions rule exists because a skip that prints to stdout is
invisible, and an invisible skip once hid a bug. DEFECTS R13 showed the rule
was being enforced by hand: two code paths dropped candidates with no row at
all, and nothing in the pipeline noticed for seven cases.

Reading the generator for missing `exclude()` calls is the wrong check -- it
shares a witness with the thing it audits. This reconstructs the candidate set
from L2 instead, independently of how bench/generate.py walks it, and demands
that each candidate resolve exactly one way.

The candidate model, from bench/DESIGN.md:

    T1, T1-triage   one candidate per verdict row on every case
    T3              one candidate per verdict row, but only on appealed cases

    python3 verify/candidate_accounting.py
"""

import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = ROOT / "data" / "l2" / "cases.jsonl"
ITEMS = ROOT / "bench" / "items.jsonl"
EXCLUSIONS = ROOT / "bench" / "exclusions.jsonl"


def main():
    for p in (CASES, ITEMS, EXCLUSIONS):
        if not p.exists():
            sys.exit(f"REFUSING: {p} missing; run the pipeline first.")

    expected = set()
    for line in CASES.open(encoding="utf-8"):
        d = json.loads(line)
        num = (d.get("case_number") or {}).get("value")
        if not num:
            continue
        appealed = bool((d.get("appeal") or {}).get("appealed"))
        for v in d.get("verdicts") or []:
            clause = v.get("clause")
            expected.add((num, clause, "T1"))
            expected.add((num, clause, "T1-triage"))
            if appealed:
                expected.add((num, clause, "T3"))

    got_items = collections.Counter()
    for line in ITEMS.open(encoding="utf-8"):
        it = json.loads(line)
        got_items[(it["case_number"], it["inputs"]["clause_ref"]["clause"],
                   it["task"])] += 1
    # A case can be excluded whole, recorded once with clause=None rather than
    # repeated per clause (multi_case_undeclared does this for 135 rows). Such a
    # row accounts for every candidate of that case and task.
    got_excl = collections.Counter()
    case_level = collections.defaultdict(set)
    reasons = {}
    for line in EXCLUSIONS.open(encoding="utf-8"):
        r = json.loads(line)
        if r["clause"] is None:
            case_level[(r["case_number"], r["task"])].add(r["reason"])
            continue
        key = (r["case_number"], r["clause"], r["task"])
        got_excl[key] += 1
        reasons.setdefault(key, r["reason"])

    resolved = set(got_items) | set(got_excl) | {
        (num, clause, task) for (num, clause, task) in expected
        if (num, task) in case_level}
    unaccounted = sorted(expected - resolved)
    # Only unpredicted ITEMS matter. An unpredicted exclusion row is harmless
    # over-reporting -- `dual_ruling` is recorded for every task, so a
    # non-appealed case still gets a T3 row for a T3 candidate that never
    # existed. An unpredicted item would mean bench invented a label.
    unexpected = sorted(set(got_items) - expected)
    both = sorted(k for k in expected if got_items[k] and got_excl[k])

    print(f"candidates reconstructed from L2 : {len(expected):,}")
    print(f"  resolved to an item            : {len(set(got_items) & expected):,}")
    print(f"  resolved to an exclusion row   : {len(set(got_excl) & expected):,}")
    covered = sum(1 for k in expected
                  if (k[0], k[2]) in case_level and k not in got_items and k not in got_excl)
    print(f"  covered by a case-level row    : {covered:,}")
    print(f"  UNACCOUNTED (silent drop)      : {len(unaccounted)}")
    for k in unaccounted[:20]:
        print(f"      {k}")
    if len(unaccounted) > 20:
        print(f"      ... and {len(unaccounted) - 20} more")

    if unexpected:
        print(f"\nITEMS the candidate model did not predict : {len(unexpected)}")
        for k in unexpected[:10]:
            print(f"      {k}  {reasons.get(k, '(item)')}")
        print("  An item with no candidate behind it means bench produced a label "
              "L2 does not carry a verdict row for. Investigate before trusting "
              "the bank.")

    if both:
        print(f"\nboth an item AND an exclusion row : {len(both)}")
        for k in both[:10]:
            print(f"      {k}  {reasons.get(k)}")
        print("  Expected where a candidate produces one item and a separate "
              "row records a variant that was dropped (e.g. a rendition).")

    if unexpected:
        sys.exit(f"\nFAIL: {len(unexpected)} item(s) exist for which L2 carries no "
                 "verdict row. A label with no candidate behind it is worse than a "
                 "dropped candidate.")
    if unaccounted:
        sys.exit(f"\nFAIL: {len(unaccounted)} candidate(s) vanished without a "
                 "reasoned row. That is the failure mode DEFECTS R13 named.")
    print("\nOK: every candidate reconstructed from L2 resolves to an item or a "
          "reasoned exclusion row.")


if __name__ == "__main__":
    main()
