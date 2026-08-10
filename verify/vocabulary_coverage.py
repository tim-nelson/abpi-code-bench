"""Which hand-typed slot values does L2 actually have an opinion about?

Four of the six defects fixed on 2026-08-05 share one root cause: a pattern
written against a hand-typed field, correct when measured, quietly wrong once
more data arrived.

    R3   `outwith_scope` matched ONE of eight spellings -- 25% of the class lost
    R11  `abridged` measured as 'all title lines', later 40% body prose
    R10  `\\bpharma\\b` matched a company name
    R8   `anonymous` attached to the evidence, not the person

None was detectable from inside: the rule and the only witness to it were the
same. But these slots are CLOSED VOCABULARIES -- `cludo:status` has 193 distinct
values over 1,902 files, `cludo:appeal` has 65 -- and a closed vocabulary can be
enumerated. That converts an unanswerable question ("does my regex catch
everything?") into a checkable one ("is every value the corpus contains decided
by some rule?"), and makes the answer a ranked worklist instead of silence.

WHAT THIS IS NOT. It calls the builder's own rules, so it cannot tell you a rule
is RIGHT -- that needs an independent reading, which is what the audits do. It
tells you the rule has an opinion about every value it will ever be shown. R3
and R11 were both coverage failures, not correctness failures, so this is the
check that would have caught them.

Undecided is not automatically wrong: absence is a value, and most statuses say
nothing about scope. What matters is the SHAPE of the undecided tail -- a
frequent value no rule decides is where the next R3 is.

    python3 verify/vocabulary_coverage.py
    python3 verify/vocabulary_coverage.py --slot cludo:status --show 40
"""

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "l2"))
RECORDS = ROOT / "data" / "l1" / "records.jsonl"

import build as l2  # noqa: E402  (the rules under test)


def decide_status(value):
    """-> the decision `outwith_scope` takes, or None where it says nothing."""
    if l2.OUTWITH_STATUS_RE.search(l2.collapse(value)):
        return "outwith_scope"
    if l2.OUTWITH_SCOPE_MENTION_RE.search(l2.collapse(value)):
        return None          # names the scope but undecided -> build refuses
    return "not an outwith disposal"


def decide_appeal(value):
    who, referral = l2.fold_appeal(value)
    if who is None and not referral:
        return None
    return f"appealed_by={who}" + (" (panel referral)" if referral else "")


SLOTS = [
    ("cludo:status", decide_status,
     "outwith-scope disposal (DEFECTS D1/R3); guarded at build time"),
    ("cludo:appeal", decide_appeal,
     "who appealed (C6 fold); drives T3 eligibility"),
]

# Slots L2 reads through PROSE rules on the report body rather than by
# classifying the slot value. Their vocabularies are listed for scale, but a
# slot-value coverage check is the wrong instrument -- the prose is the source.
PROSE_DRIVEN = ["cludo:complainant", "cludo:additional_sanctions",
                "cludo:sanctions_applied", "cludo:applicable_code_year"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot")
    ap.add_argument("--show", type=int, default=12)
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if any slot has an undecided value that "
                         "is not the empty string (i.e. real drift, not silence)")
    args = ap.parse_args()

    if not RECORDS.exists():
        sys.exit(f"REFUSING: {RECORDS} missing; run l1/build.py first.")

    vocab = collections.defaultdict(collections.Counter)
    for line in RECORDS.open(encoding="utf-8"):
        meta = (json.loads(line).get("meta") or {})
        for k, v in meta.items():
            if isinstance(v, str):
                vocab[k][v.strip()] += 1

    failures = 0
    for slot, decide, what in SLOTS:
        if args.slot and slot != args.slot:
            continue
        counts = vocab.get(slot, collections.Counter())
        decided, undecided = collections.Counter(), collections.Counter()
        for value, n in counts.items():
            (decided if decide(value) is not None else undecided)[value] = n

        n_files = sum(counts.values())
        dec_files = sum(decided.values())
        print(f"\n### {slot} — {what}")
        print(f"  distinct values : {len(counts):,}   files: {n_files:,}")
        print(f"  decided         : {len(decided):,} values / {dec_files:,} files "
              f"({dec_files / n_files:.1%})")
        print(f"  undecided       : {len(undecided):,} values / "
              f"{sum(undecided.values()):,} files")
        real = {v: n for v, n in undecided.items() if v}
        if real and args.strict:
            failures += len(real)
        if undecided:
            print(f"  most frequent undecided values (next R3 lives here):")
            for value, n in undecided.most_common(args.show):
                print(f"      {n:>5}  {value[:88]!r}")
            if len(undecided) > args.show:
                print(f"      ... and {len(undecided) - args.show:,} more")

    print("\n### slots L2 reads from PROSE, not from the slot value")
    for slot in PROSE_DRIVEN:
        c = vocab.get(slot, collections.Counter())
        if c:
            print(f"  {slot:32s} {len(c):>5,} distinct values over "
                  f"{sum(c.values()):,} files")
    if failures:
        print(f"\nFAIL (--strict): {failures} non-empty slot value(s) that no rule "
              "decides. Empty strings are silence and are not counted.")
    print("  A slot-value coverage check does not apply to these: the report "
          "prose is the source and the slot is the fallback. Their exposure is "
          "measured by the adversarial audits instead (DEFECTS D6-D8).")
    return failures


if __name__ == "__main__":
    sys.exit(main())
