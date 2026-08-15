"""The cases every ruling-language reader in the pipeline must agree on.

DEFECTS R24. One character class, `[^.]`, appeared in four places -- l2/build.py
RULING_RE, l2/validate.py's supposedly independent copy of it, bench/generate.py
TRIPWIRE and bench/validate.py through the import -- and none of them could
cross the decimal point of a clause number. "No breach of Clause 2 was ruled"
matched; "No breach of Clause 9.2 was ruled", the corpus's commonest ruling
form, did not. Three layers of defence, one blind spot, and four items shipped
with the answer printed in their own prompt (AUTH/1797/2/06, all four in the
test split).

Nothing in the repo held the pattern to a stated set of cases, so nothing
noticed. This file is that set. It is deliberately small, hand-written and
adversarial, and every row states WHY it is there:

  * the three decimal forms the old pattern could not see;
  * the plural and the "Code N.M" spelling, which it also could not see;
  * the precedent citations of OTHER cases that it must NOT fire on, because a
    complainant quoting an earlier adjudication is the legitimate class
    bench/generate.py's TRIPWIRE comments document;
  * the allegation and denial vocabulary that must stay quotable, or the check
    would refuse every complaint in the corpus and the refusal would look like
    rigour.

THREE readers are held to it, and they are three different pieces of code:
l2/build.py's regex, l2/validate.py's token/window scan (which must never
become a copy of the regex again -- that is what made the guarantee nominal),
and bench/generate.py's item-level tripwire. The last is a coarser net by
design: it fires on ruling vocabulary the attest tolerates, so it is checked
only for the rows this file marks `tripwire=True`.

SECOND SECTION, added 2026-08-10 (DEFECTS R28 / audit round-2A N1). The rows
above hold the LEAKAGE reader; `VERDICT_BATTERY` holds the reader that creates
verdict EVIDENCE. Nothing held it, which is exactly how the fix wave repaired
`RULING_RE`'s adverb slot and gap and left `RULED_PASSIVE_RE` -- the pattern the
labels rest on -- carrying both holes. Two readers there: build's
`sentence_polarities` and validate's `v_passive_statements`, a token scan that
implements the passive frame only, so each row states what EACH must produce.

Proving the guards fire (do this after any change to the patterns):

  * leakage: revert the fix -- put `[^.]` back in place of _GAP in
    l2/build.py's `_RULING_F2` -- and run this. It must name the decimal rows.
  * verdict evidence: put the three-word enumeration back in place of
    `_RULED_ADVERB` in `RULED_PASSIVE_RE`, and/or `[^.]` back in place of
    `_GAP` there. This file must name the 'was thus ruled' and decimal-gap
    rows, AND -- after `python3 l2/build.py` -- `l2/validate.py` must fail,
    because its own reading now states panel polarities the rebuilt receipts
    no longer carry.

Restore, and both must be silent again.

    python3 verify/ruling_battery.py
    python3 verify/ruling_battery.py --verbose
"""

import argparse
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# One file's own case numbers, for the precedent exemption. AUTH/9999/9/99 is
# not a real case; it stands for "this document's own case" so a row can say
# "a ruling on THIS case" and mean it.
OWN = frozenset({"AUTH/9999/9/99"})

# (text, attest_should_fire, tripwire_should_fire, why)
BATTERY = [
    # --- R24's three decimal forms. All THREE were invisible before the fix.
    ("No breach of Clause 9.2 was ruled.", True, True,
     "R24's own leak: the exact sentence AUTH/1797/2/06 shipped to four test-split items"),
    ("A breach of Clause 7.10 was thus ruled.", True, True,
     "positive polarity, two-digit second component; the tripwire had NO pattern for the "
     "passive form at all ('ruled a breach' needs the verb first)"),
    ("Breaches of Clauses 7.2 and 7.4 were ruled.", True, True,
     "plural: `(?:Clause|the Code)\\b` could not match 'Clauses' -- the \\b fails on the 's'"),
    # --- the same forms without decimals, which always worked. Regression rows.
    ("No breach of Clause 2 was ruled.", True, True,
     "the one form the old pattern DID see; it must keep seeing it"),
    ("The Panel ruled a breach of Clause 9.1 of the 2021 Code.", True, True,
     "F1: a named ruling body plus a ruling verb"),
    # --- the spelling R5 found the pattern could not read.
    ("No breach of Code 7.2 was ruled.", True, True,
     "AUTH/1816/3/06 writes 'Code 7.2' where the pattern wanted 'Clause 7.2' or 'the Code'"),
    ("Breaches of the Code were ruled.", True, True,
     "clause-anonymised form; 'the Code' must keep matching"),
    # --- precedent citations of OTHER cases: quotable, and must stay quotable.
    ("In case AUTH/3676/7/22, a breach of clause 25.3 was ruled as the Roche declaration "
     "was not provided in prominence from the outset.", False, False,
     "AUTH/3893/4/24's complaint. The citing case number precedes the ruling"),
    ("With respect to a reference to NICE in promotional materials, the PMCPA had ruled: "
     "‘Although Clause 9.5 prohibited reference to certain bodies in promotional material, "
     "NICE was not one of them. No breach of Clause 9.5 was ruled.’ (ref AUTH/2052/10/07)",
     False, False,
     "AUTH/3387/9/20's response. Here the attribution FOLLOWS the quote, which is why the "
     "precedent window is symmetric"),
    ("The complainant noted that he had previously complained about an identical "
     "advertisement and a breach of the Code was ruled (Case AUTH/1756/9/05).", False, False,
     "AUTH/1854/6/06's complaint -- three T2 items depend on this staying quotable"),
    # --- and the exemption must NOT swallow this case's own ruling.
    ("In Case AUTH/9999/9/99 no breach of Clause 9.2 was ruled.", True, True,
     "the citation names THIS file's own case, so it is not precedent; it is the leak"),
    # --- F1 is never exempt: a party restating another body's ruling is DEFECTS D3.
    ("Novo Nordisk noted that in Case AUTH/2471/1/12 the Appeal Board ruled no breach "
     "of Clause 2.", True, True,
     "F1 names the ruling body, so the neighbouring case number does not exempt it -- "
     "exempting F1 too would have flipped 72 segments from refused to quotable"),
    # --- allegation and denial: the corpus's ordinary complaint/response voice.
    ("AbbVie submitted that there was no breach of Clause 7.2 of the Code.", False, False,
     "a denial in a response; refusing this would refuse every response in the corpus"),
    ("This information was not provided in a factual manner and so a breach of "
     "Clause 20.2 was alleged.", False, False,
     "an allegation in a complaint: 'was alleged', not 'was ruled'"),
    ("The complainant alleged that the conduct of the employee breached Clause 2.", False, False,
     "allegation with the verb inflected; still no ruling"),
    ("Genzyme alleged a breach of Clause 2 of the Code.", False, False,
     "the commonest allegation shape in the bank (57 rendition hits at a 45-char window)"),
    # --- sentence boundaries: the reason `[^.]` was there in the first place.
    ("A breach of Clause 7.2 was alleged. The company denied it. Nothing was ruled here "
     "at all, on any clause, by anybody, and the sentence runs on.", False, False,
     "'breach of Clause 7.2' and 'ruled' are in DIFFERENT sentences; the decimal fix must "
     "not have turned the window into a free-for-all"),
]

# ---------------------------------------------------------------------------
# THE VERDICT-EVIDENCE READER (DEFECTS R28, audit round-2A finding N1).
#
# The battery above holds the LEAKAGE reader. Nothing held the reader that
# creates verdict evidence, and that is how the 2026-08-10 fix wave came to
# repair RULING_RE's adverb slot and `[^.]` gap and leave `RULED_PASSIVE_RE`
# -- the pattern the labels rest on -- with a three-word adverb enumeration and
# a gap that cannot cross a decimal point. "No breach of Clause 15.4 was thus
# ruled" stated NOTHING, on 32 sentences over 30 cases, three of which
# published a Panel value their own ruling prose contradicts.
#
# Two readers are held here, and they are two different pieces of code:
# l2/build.py's `sentence_polarities` (five regex frames) and l2/validate.py's
# `v_passive_statements` (a token scan). The validator is a SUBSET reader by
# design -- it implements the passive frame only -- so a row states what each
# must produce, and `subset=True` marks the rows where the validator is
# expected to read less than the builder.
#
# (text, builder_expects, validator_expects, why)
VERDICT_BATTERY = [
    # --- the adverb hole, in both polarities. THE row this file exists for.
    ("No breach of Clause 15.4 was thus ruled.",
     [("no_breach", "15.4")], [("no_breach", "15.4")],
     "AUTH/2220/3/09. Read as stating NOTHING before the parity fix, which is why "
     "L2 published panel=breach on a clause the Panel also ruled no breach of"),
    ("Further, the material had not been certified and a breach of Clause 14.1 was "
     "therefore ruled.",
     [("breach", "14.1")], [("breach", "14.1")],
     "AUTH/3476/2/21. Six sibling clauses of that case were already dual; 14.1 escaped "
     "on one adverb and shipped labelled no_breach"),
    ("No breach of Clauses 3.1, 9.1 and 9.2 was accordingly ruled.",
     [("no_breach", "3.1"), ("no_breach", "9.1"), ("no_breach", "9.2")],
     [("no_breach", "3.1"), ("no_breach", "9.1"), ("no_breach", "9.2")],
     "AUTH/1996/4/07. 'accordingly' -- the third adverb the enumeration missed; an "
     "enumerated list is one statement about every clause in it"),
    # --- the negating adverb, which a GENERIC slot would have swallowed.
    ("However, a breach of Clause 2 was not ruled, because the Panel considered the "
     "statement appeared on a professional networking site.",
     [], [],
     "AUTH/3364/6/20. 'was NOT ruled' is a no-breach statement with a breach-shaped "
     "surface: the slot is generic MINUS not/never, or the fix would invent a breach"),
    # --- the decimal gap between the clause list and the verb.
    ("No breach of Clause 19.2 of the 2019 Code (similar to Clause 23.2 of the 2021 "
     "Code) was ruled in that regard.",
     [("no_breach", "19.2")], [("no_breach", "19.2")],
     "AUTH/3594/12/21. The gap holds the '.' of the parenthetical 'Clause 23.2', so "
     "`[^.]{0,60}` could not reach the verb -- 8 sentences over 6 cases"),
    # --- the swallow rules, both directions.
    ("The Panel considered the claim was not misleading as alleged and thus ruled no "
     "breach of Clause 7.2 and subsequently no breach of Clause 9.1 was ruled.",
     [("no_breach", "7.2"), ("no_breach", "9.1")],
     [("no_breach", "7.2"), ("no_breach", "9.1")],
     "AUTH/3039/5/18. With a decimal-tolerant gap the FIRST statement reaches the verb "
     "and used to consume the second; the scan resumes at the end of the clause list"),
    ("No breach of Clauses 9.1, 15.2, 18.1 and consequently no breach of Clause 2 were "
     "ruled.",
     [("no_breach", "9.1"), ("no_breach", "15.2"), ("no_breach", "18.1"),
      ("no_breach", "2")],
     [("no_breach", "9.1"), ("no_breach", "15.2"), ("no_breach", "18.1"),
      ("no_breach", "2")],
     "AUTH/2230/5/09. 'and consequently no breach of Clause 2' is a second statement, "
     "not a list item -- the list rule stops at 'no'"),
    ("The Appeal Board considered that a ruling of a breach of Clause 2 was not "
     "warranted and no breach of Clause 2 was ruled.",
     [("no_breach", "2")], [("no_breach", "2")],
     "AUTH/3483/3/21, the l2.3 re-read case: a positive match that swallows an inner "
     "'no breach' must be re-read from the inner one, never yielded"),
    # --- frames the validator deliberately does not implement.
    ("The Panel ruled a breach of Clause 9.1 of the Code.",
     [("breach", "9.1")], [],
     "the ACTIVE frame: the builder reads it, the subset reader does not -- which is "
     "what makes the one-directional corpus check safe"),
    ("The Appeal Board upheld the Panel's ruling of a breach of Clause 7.2.",
     [("breach", "7.2")], [],
     "the UPHOLD frame, likewise builder-only"),
    # --- wave C: two word orders of the uphold statement the frame could not read.
    ("The Appeal Board upheld the Panel's ruling a breach of Clause 23.8.",
     [("breach", "23.8")], [],
     "AUTH/2308/4/10. The publisher drops the 'of' after 'ruling'; the frame required "
     "it, so the clause had a Panel value, no Appeal Board one, and its T3 candidate "
     "was excluded as unattributed. One sentence corpus-wide matches this and no other"),
    ("The Panel's ruling of a breach of Clause 9.9 was upheld. The appeal was thus "
     "unsuccessful.",
     [("breach", "9.9")], [],
     "AUTH/2089/1/08. The PASSIVE word order: the active frame needs 'upheld' first "
     "and cannot reach it. 12 sentences match corpus-wide, 10 in appeal_ruling"),
    ("Takeda submitted that if the Panel's ruling of a breach of Clause 7.2 was upheld "
     "it would have a significant impact on the safety reporting requirements.",
     [], [],
     "AUTH/2367/10/10. R1's irrealis hazard in the new frame: a party arguing about a "
     "ruling is not a body making one. The 'if' guard is what refuses it -- narrow the "
     "guard away and this row fires"),
    ("Accordingly, if the Panel's rulings of breaches of Clause 7.2 and 7.3 were upheld "
     "(which were contested by Shire in its own appeal), it did not follow that Shire "
     "had also disparaged Cerezyme.",
     [], [],
     "AUTH/2528/8/12. The same hazard in the form R1's own guard does NOT catch: no "
     "'submitted that ... would', just a bare conditional"),
    # --- allegation vocabulary: no ruling, either reader.
    ("This information was not provided in a factual manner and so a breach of "
     "Clause 20.2 was alleged.", [], [],
     "an allegation states no verdict; a reader that fired here would label every "
     "complaint in the corpus"),
    ("Genzyme alleged a breach of Clause 2 of the Code.", [], [],
     "the commonest allegation shape in the bank"),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    build = _load("_l2_build", ROOT / "l2" / "build.py")
    validate = _load("_l2_validate", ROOT / "l2" / "validate.py")
    generate = _load("_bench_generate", ROOT / "bench" / "generate.py")

    failures = []
    for text, want_attest, want_tripwire, why in BATTERY:
        got_build = build.ruling_language(text, OWN) is not None
        got_valid = validate.v_ruling_language(text, OWN)
        got_trip = generate.tripwire_hit(text, OWN)[0] is not None
        row = (text[:70] + ("..." if len(text) > 70 else ""))
        if got_build != want_attest:
            failures.append(("l2/build.py ruling_language", row, want_attest, got_build, why))
        if got_valid != want_attest:
            failures.append(("l2/validate.py v_ruling_language", row, want_attest, got_valid, why))
        if got_build != got_valid:
            failures.append(("BUILDER vs VALIDATOR DISAGREE", row, got_build, got_valid, why))
        if got_trip != want_tripwire:
            failures.append(("bench/generate.py tripwire_hit", row, want_tripwire, got_trip, why))
        if args.verbose:
            print(f"  attest={got_build!s:<5} tripwire={got_trip!s:<5}  {row}")

    for text, want_build, want_valid, why in VERDICT_BATTERY:
        got_build = build.sentence_polarities(text)
        got_valid = []
        for _, sentence in validate.v_sentences(text):
            for st in validate.v_passive_statements(sentence):
                if st not in got_valid:
                    got_valid.append(st)
        row = (text[:70] + ("..." if len(text) > 70 else ""))
        if sorted(got_build) != sorted(tuple(x) for x in want_build):
            failures.append(("l2/build.py sentence_polarities", row,
                             sorted(tuple(x) for x in want_build), sorted(got_build), why))
        if sorted(got_valid) != sorted(tuple(x) for x in want_valid):
            failures.append(("l2/validate.py v_passive_statements", row,
                             sorted(tuple(x) for x in want_valid), sorted(got_valid), why))
        if args.verbose:
            print(f"  verdict build={got_build}  validator={got_valid}  {row}")

    print(f"battery rows : {len(BATTERY)} leakage + {len(VERDICT_BATTERY)} verdict-evidence")
    print(f"readers held : l2/build.py, l2/validate.py, bench/generate.py")
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for where, row, want, got, why in failures:
            print(f"  {where}\n    text     : {row}\n    expected : {want}\n    got      : {got}\n"
                  f"    why the row exists: {why}")
        return 1
    print("\nOK: all three readers agree with the battery and with each other.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
