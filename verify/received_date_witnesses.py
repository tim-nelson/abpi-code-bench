"""Audit `dates.received` against two witnesses L2 already had but did not use.

Prompted by a cross-check against outside sources: the PMCPA's own webinar and
2024/2025 annual reports date the abridged complaints procedure to October 2024,
yet two cases carrying that flag had received dates months earlier. Pulling that
thread found a date problem, not an abridged problem.

L2 stores three witnesses for the received date and records a `basis`:

  info                  the site's structured metadata field
  meta                  the site's meta tag
  report_trailer_lines  the case report's own "Complaint received <date>" line

Before R12 the canonical value was labelled `basis: "unanimous"` on rows where
the trailer DISAGREED with it, because the trailer was stored but not compared.
R12 made the builder compare it; this script is the standing check that the
result still holds, and it re-derives the comparison rather than trusting the
recorded basis.

A third witness adjudicates: the PMCPA case number encodes the MONTH AND YEAR
of receipt (AUTH/3891/4/24 -> April 2024). It comes from the listing, not the
report body, so it is downstream of neither. Both components matter -- checking
the year alone once reported AUTH/3293/1/20 as corroborated when the case
number's month contradicted the value.

LIMIT, stated because a validator must not quietly share a witness: this reads
L2's STORED `report_trailer_lines` and `index_case_number`. The parse is
independent of the builder's, the witnesses are not -- it cannot catch L1/L2
dropping or mangling a trailer line at storage time. `l1/coverage.py` is what
covers that.

    python3 verify/received_date_witnesses.py
    python3 verify/received_date_witnesses.py --json   # machine-readable
"""

import argparse
import json
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = ROOT / "data" / "l2" / "cases.jsonl"

MONTHS = ("January|February|March|April|May|June|July|August|September|"
          "October|November|December")
TRAILER_RE = re.compile(rf"Complaint received\s+(\d{{1,2}}\s+(?:{MONTHS})\s+(\d{{4}}))", re.I)
CASENO_RE = re.compile(r"/(\d{1,2})/(\d{2})\s*$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not CASES.exists():
        sys.exit(f"REFUSING: {CASES} missing; run l2/build.py first.")

    rows, agree, no_trailer = [], 0, 0
    for line in CASES.open(encoding="utf-8"):
        d = json.loads(line)
        rec = d["dates"].get("received") or {}
        val = rec.get("value")
        srcs = rec.get("sources") or {}
        caseno = (d["case_number"]["sources"].get("index_case_number") or "").strip()
        if not val:
            continue

        m = TRAILER_RE.search(" ".join(srcs.get("report_trailer_lines") or []))
        if not m:
            no_trailer += 1
            continue
        trailer_year = int(m.group(2))
        value_year = int(val[:4])
        if trailer_year == value_year:
            agree += 1
            continue

        cm = CASENO_RE.search(caseno)
        caseno_year = 2000 + int(cm.group(2)) if cm else None
        caseno_month = int(cm.group(1)) if cm else None
        value_month = int(val[5:7])
        if caseno_year is None:
            verdict = "undecidable: no parsable case number"
        elif caseno_year == value_year and caseno_month == value_month:
            verdict = "value corroborated; the report trailer is the typo"
        elif caseno_year == value_year:
            # The case number encodes MONTH as well as year, and this script used
            # to capture the month and then ignore it -- which let
            # AUTH/3293/1/20 (case number says January 2020, canonical value
            # says 30 December 2020, report says 30 December 2019) report as
            # "corroborated" when no witness actually supports the value.
            verdict = (f"YEAR corroborated but MONTH contradicted: case number "
                       f"says month {caseno_month:02d}, value says "
                       f"{value_month:02d} — no witness supports this value")
        elif caseno_year == trailer_year:
            verdict = "VALUE WRONG: trailer and case number agree against it"
        else:
            verdict = "all three witnesses differ"
        rows.append({"case_number": caseno, "value": val,
                     "trailer": m.group(1), "caseno_year": caseno_year,
                     "basis": rec.get("basis"), "verdict": verdict})

    verdicts = Counter(r["verdict"] for r in rows)
    bases = Counter(r["basis"] for r in rows)

    if args.json:
        print(json.dumps({"disagreements": rows, "agree": agree,
                          "no_trailer": no_trailer,
                          "verdicts": verdicts, "bases": bases}, indent=1))
        return

    print(f"cases with a canonical received date and a trailer statement: "
          f"{agree + len(rows):,}")
    print(f"  trailer states the SAME DATE : {agree:,}")
    print("    (a trailer differing only in the day is a disagreement resolved by "
          "convention,\n     recorded as date_slots_over_trailer_same_year — not an "
          "agreement)")
    print(f"  trailer disagrees         : {len(rows)}")
    print(f"cases with no trailer statement to check against: {no_trailer:,}\n")
    for r in sorted(rows, key=lambda r: r["case_number"]):
        print(f"  {r['case_number']:22s} value={r['value']}  "
              f"trailer={r['trailer']:<20s} case-no={r['caseno_year']}")
        print(f"  {'':22s} basis={r['basis']!r} -> {r['verdict']}")
    print("\nverdicts:", dict(verdicts))
    print("basis recorded on disagreeing rows:", dict(bases))
    unsupported = sum(n for v, n in verdicts.items() if v.startswith("YEAR corroborated but MONTH"))
    wrong = verdicts.get("VALUE WRONG: trailer and case number agree against it", 0)
    if wrong:
        print(f"\n{wrong} canonical received dates are contradicted by BOTH other "
              "witnesses.\nThese shift cases between years, so they touch the era "
              "curve (FINDINGS 4.6)\nand docs/CORPUS_EXTERNAL_CHECK.md.")
        return 1
    if unsupported:
        print(f"\n{unsupported} value(s) have NO supporting witness (year agrees, "
              "month does not).")
        return 1
    if bases and set(bases) <= {"unanimous"}:
        print("\nEvery disagreeing row is labelled basis='unanimous'. The unanimity "
              "check\nis not consulting report_trailer_lines for this field.")


if __name__ == "__main__":
    sys.exit(main() or 0)
