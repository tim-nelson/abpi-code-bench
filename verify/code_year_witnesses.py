"""Standing check on every case's Code year, read from L1 and from the Code.

    python3 verify/code_year_witnesses.py

DEFECTS R19/R20. `code_year` is the PMCPA's own 'Applicable Code year' slot and
it is wrong on 31 cases in two systematic ways: it sometimes records the
CONSTITUTION AND PROCEDURE year rather than the Code edition ('The complaint
was considered under the 2006 Code using the 2008 Constitution and Procedure',
tagged 2008), and at edition boundaries it takes the filing date where the
Panel took the conduct date -- in BOTH directions, three weeks apart
(AUTH/3148/1/19 tagged 2019 and ruled 2016; AUTH/3166/2/19 tagged 2016 and
ruled 2019). The corrections are adjudications in l2/adjudications.json. This
file is what stops the next one going unnoticed.

Three witnesses:

  (a) EDITION STATEMENTS -- the report's own words about which edition it was
      decided under. RE-IMPLEMENTED here, not imported: l2/build.py runs a list
      of anchored frames over the collapsed pane and reads years out of the
      frame's own capture group; this splits the pane into sentences, decides
      whether a SENTENCE is an adjudicator's determination, and then takes
      every Code year the sentence carries. The two readings disagree about
      recall by design -- see the recall note at the foot -- and that is the
      point of a second implementation (DEFECTS F1).

      Three guards, each earned from a case in the corpus:

        C&P years           a year adjacent to 'Constitution and Procedure' is
                            never a Code edition. This is R19's mode 1 stated
                            as a rule.
        precedent           a sentence citing another case number, or written
                            in the past perfect ('had been ruled'), is about a
                            previous adjudication: AUTH/1939/1/07 recites 'No
                            breach of Clauses 18.1, 9.1 and 2 of the 2003 Code
                            had been ruled. Turning to the case now at issue
                            ...' and its own 2006 tag is CORRECT.
        party voice         a party asking for an edition is not the Authority
                            choosing one: 'It was not Bayer's position that
                            this case should be considered under the ... 2008
                            Code' (AUTH/2908/11/16, tagged 2012, correctly).

      And the tier that makes AUTH/2220/3/09 pass, which the R19 audit names as
      the known false positive of any flat sweep: it says both 'thus the
      requirements of the 2006 Code applied' and 'The case was thus considered
      under the 2008 Code', and its 2008 tag is right. Only DETERMINATION
      sentences count here; an application sentence never reaches this reading.

  (b) CLAUSE STRUCTURE -- every item's (code_year, clause) must exist in that
      edition, and a dotted reference must be present AS a dotted number. The
      2008 Clause 20 is 'The Use of Consultants' with no subclauses at all, so
      a (2008, 20.2) item is proof its year is wrong; 2006's Clause 20 is
      'Relations with the General Public and the Media' and does carry 20.2.
      Editions we hold no text for (2001, 2003) make existence UNKNOWN, and
      unknown is not a failure -- asserting from silence is what the absence
      rule forbids.

  (c) RECEIVED DATE -- flag-only, deliberately. The governing edition attaches
      to the CONDUCT, not to the complaint, so an old edition on a late
      complaint is ordinary and correct: AUTH/2297/1/10 is a January 2010
      complaint about an April 2004 advertisement, adjudicated under the 2003
      Code. It must pass, and it does; this tier prints and never fails.

  (d) REVIEWED PER-CLAUSE YEARS -- added 2026-08-10 with R20's residue round,
      which gave L2 a way to key ONE VERDICT ROW to an edition its case's year
      does not carry (`verdicts[<clause>].code_year` in l2/adjudications.json).
      Tier (a) compares a CASE year and would never look at those, so a read
      decision would otherwise be the one class of year in the corpus with no
      standing check over it. This tier reads each one back:

        promotion (value = a year)  some sentence of the report must attach
                                    that year to THAT clause, in a voice this
                                    file will hear -- not a party's submission,
                                    not a precedent recital. Independent
                                    implementation as always: sentence-level
                                    here, where the builder distributes a year
                                    over a clause LIST with one anchored
                                    pattern.
        refusal (value = null)      the report must attach TWO OR MORE years to
                                    that clause. A refusal is a claim about a
                                    conflict, so a refusal with no conflict in
                                    the text is as much a defect as a wrong
                                    promotion.
        both                        the published row must actually carry the
                                    decision (`code_year_basis` is the
                                    adjudication id), and the (case, clause)
                                    must still exist -- a reviewed row pointing
                                    at nothing is a fix that has silently
                                    stopped applying.

  (e) COMMENCEMENT -- added 2026-08-10 with R29. The tagged edition must have
      been IN OPERATION by the date the case COMPLETED. This is the one tier
      that needs no reading of the report at all: an edition that did not exist
      cannot have governed, so it fails rather than flags. The commencement
      table is read from each edition's own front matter and quoted in full
      beside it, and the three design constraints that keep it one-sided and
      false-positive-free are stated on the table.

Exit is non-zero when (a), (b), (d) or (e) contradicts the published year and
no adjudication covers it, and when the declared-residue table below names a
pair the bank no longer carries.

RECALL, stated rather than implied. This reading is deliberately narrower than
the builder's:

  * The correspondence frame ('the Authority asked it to consider the
    requirements of Clauses 2, 5.1 ... of the 2021 Code') is NOT a
    determination here. It routinely reproduces the COMPLAINANT's citation,
    which the Panel may then correct -- CASE/0654/07/25 is the proof, where the
    respondent writes 'We apologise for incorrectly citing clauses of the 2024
    Code instead of the 2021 Code'. Reading it as a determination flags 7 more
    cases, of which some are real; they need their own reading round, not a
    guess from this file.
  * A determination scoped to PART of a case is read as the whole case's, so a
    genuinely multi-edition case can only be recognised when it states two
    determinations. AUTH/3557/9/21 rules the website under the 2021 Code and
    the journal advertisement under the 2019 Code and states only the second
    in a form this reading hears; it is silent here rather than wrong, because
    the party-voice guard drops the sentence that carries it.
  * Clause-less ruling frames ('a breach of the 2016 Code was ruled') are not
    read. The builder does read them -- it is the only edition statement
    AUTH/3143/1/19 makes -- but they are also how a multi-edition case recites
    each strand, so they are too noisy to FAIL on.
  * Tier (d)'s clause reader is looser than tier (a)'s: any Code year in a
    sentence that names the clause counts, because the question there is
    'is this year witnessed for this clause at all', not 'which year governs'.
    The narrow question was answered by a human, once, in the adjudication's
    justification; this file only refuses to let that answer stand unwitnessed.
"""

import json
import pathlib
import re
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECORDS = ROOT / "data" / "l1" / "records.jsonl"
PDF_RECORDS = ROOT / "data" / "l1" / "pdf_records.jsonl"
CASES = ROOT / "data" / "l2" / "cases.jsonl"
ITEMS = ROOT / "bench" / "items.jsonl"
ADJUDICATIONS = ROOT / "l2" / "adjudications.json"
HTML_CLAUSES = ROOT / "data" / "code" / "clauses.jsonl"
PDF_CLAUSES = ROOT / "data" / "code" / "pdf_clauses.jsonl"

YEAR = r"(?:19|20)\d{2}"
# The year token that is talking about the Code rather than about a date.
CODE_YEAR = re.compile(r"\b(" + YEAR + r")\s+(?:edition\s+of\s+the\s+)?Code\b", re.I)
# A DETERMINATION sentence: the adjudicator saying which edition was used.
DETERMINATION = tuple(re.compile(p, re.I) for p in (
    r"(?:would\s+be|was|were|is|are)\s+considered\s+(?:under|in\s+relation\s+to)",
    r"consider(?:ed|ing)\s+(?:this\s+|the\s+|these\s+)?"
    r"(?:case|cases|matter|matters|complaint|complaints)\s+(?:under|in\s+relation\s+to)",
    r"Panel\s+used\s+the",
    r"rulings?\s+(?:below\s+)?(?:were|are|was|is)\s+made\s+under",
    r"outcome\s+under\s+the",
))
CANDP = tuple(re.compile(p, re.I) for p in (
    r"(" + YEAR + r")\s+(?:edition\s+of\s+the\s+Code\s+)?Constitution\s+and\s+Procedure",
    r"Constitution\s+and\s+Procedure\s+(?:for|of|as\s+set\s+out\s+in|in)\s+the\s+(" + YEAR + r")",
))
PRECEDENT = re.compile(r"\bCase\s+(?:AUTH|CASE)/|had\s+been\s+ruled\b|previous\s+case\b|earlier\s+case\b",
                       re.I)
PARTY_VOICE = re.compile(r"\bsubmitted\b|\bsubmission\b|\bposition\s+that\b|’s\s+position\b|"
                         r"'s\s+position\b|asked\s+the\s+PMCPA\b|\bcontended\b|\bargued\b|\bdenied\b",
                         re.I)
SENTENCE = re.compile(r"(?<=[.;])\s+")
# Tier (d). 'Clause 21.3', 'Clauses 2, 9.1 and 21.3', 'the relevant clause was
# 22.1' -- the clause token wherever it stands, which is the difference from the
# builder's one anchored 'Clauses <list> of the YYYY Code' pattern. Bounded on
# both sides so 21.3 does not match inside 121.35 or a date.
CLAUSE_TOKEN = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)(?![\d.])")
YEAR_FIELD = re.compile(r"^verdicts\[(.+)\]\.code_year$")
# Tier (d) reads the PLURAL too -- 'Clause 13.1 in the 2014, 2015 and 2016
# Codes', 'the same in the 2001 and 2003 Codes' -- which is how a report says a
# clause spans editions, and so exactly the sentence a refusal rests on. It is
# NOT added to CODE_YEAR above, deliberately: tier (a) only compares when the
# report makes exactly ONE determination, so teaching it the plural would turn
# single determinations into `multi_edition` and SILENCE the comparison. A
# looser reader is right for 'is this year attached to this clause at all' and
# wrong for 'which edition governs the case'.
CODE_YEAR_ANY = re.compile(r"\b(" + YEAR + r")(?:\s*,\s*(?:19|20)\d{2})*"
                           r"(?:\s+and\s+(?:19|20)\d{2})?\s+"
                           r"(?:editions?\s+of\s+the\s+)?Codes?\b", re.I)
# 'the 2014, 2015 and 2016 Codes' names three; the head match above anchors the
# run and this one enumerates it.
YEAR_RUN = re.compile(r"\b((?:19|20)\d{2})\b")

# R19 residue, declared with a refusal on any fifth. Four (case, year, clause)
# pairs the bank carries that the edition does not, on three cases the R19 audit
# never ruled and this file will not rule either, because none of the three
# reports names an edition for the clause in question and the only repair left
# is a date inference -- which is exactly what that audit proved unsafe, the
# tag's two failure modes pointing in opposite directions.
#
# This oracle can only see what the Code-text layer carries, which is why R21
# recorded a caution against trusting it while 2003 had no document: 13 of its
# 17 case-hits then were the coverage gap, not tag defects. The gap closed on
# 2026-08-09 when the archived 2001 and 2003 editions were parsed, and the
# oracle produced exactly ONE new hit on 171 newly-covered items -- the third
# case below.
#
#   AUTH/2135/6/08   received 2008-06-23, eight days before the 2008 Code came
#       into operation. The rulings are about an advertisement to the public,
#       which is 2006's Clause 20; 2008's Clause 20 is 'The Use of Consultants'
#       and has no subclauses. Very likely a 2006 case -- but the report says
#       nothing, so it stays a flag rather than a repair.
#   AUTH/2190/12/08  REPAIRED 2026-08-11 and no longer in KNOWN_ABSENT; kept
#       here as the record of how this table is meant to empty. This entry read
#       "here it is the CLAUSE that is doubtful rather than the year: the report
#       rules 'no breach of Clauses 3.2 and 15.2' and never mentions 3.3, which
#       only the outcome slots state. A different defect, and not this file's."
#       That different defect was DEFECTS N2, and its reading round corrected
#       the slot (adj-0119/adj-0120, for both siblings of the shared report):
#       clause 3.3 became 3.2, which has text in the 2008 edition, so the pair
#       stopped being textless and this table's stale-residue check failed --
#       correctly -- until the row was removed. A residue that has been
#       repaired must leave the table, and it took a guard to make it.
#   AUTH/1846/6/06   received 2006-06-01, completed 2006-07-05 -- astride the
#       2003/2006 boundary. The report rules 'No breaches of Clauses 15.9, 18.1
#       and 18.4 of the Code were ruled' and its prose names the 2003 Code for
#       a different clause ('in breach of Clause 9.1 of the 2003 Code'), but
#       the printed 2003 Clause 18 'Gifts and Inducements' runs 18.1-18.3 and
#       stops -- p24, left column, nothing below 18.3's bullet list and Clause
#       19 three pages later. 18.4 first exists in the 2006 edition, whose
#       Clause 18 is 'Gifts, Inducements, Promotional Aids and the Provision of
#       Medical and Educational Goods and Services'. So the report is either a
#       wrong-year tag (R19) or one outcome list in two numberings (R22(c),
#       AUTH/3115/11/18's shape), and nothing in it decides which. Its 2 items
#       hold an `absent_from_edition` row in
#       data/code/pdf_clauses_exclusions.jsonl and no clause text.
# ---------------------------------------------------------------------------
# TIER (e), added 2026-08-10 (DEFECTS R29): THE COMMENCEMENT IMPOSSIBILITY.
#
# A case cannot have been adjudicated under an edition that did not exist when
# it finished. That is not an inference about which of two live editions
# governs -- the R19 caution that "date-only inference errs in both directions"
# does not bite, because both directions point the same way -- so this tier
# FAILS rather than flags. 15 cases / 38 items were tagged this way; all are
# now adjudicated (adj-0091..0105, and the four adj-0037..0040 the R19/R22 wave
# caught first).
#
# Every commencement date is read from the edition's OWN front matter, quoted
# here so the table can be checked against the PDFs without running anything.
# (data/code/pdf/, page 2 or 3, verbatim.)
COMMENCEMENT = {
    2001: ((2001, 7, 1), "wb20040623__codeofpractice2001.pdf p2: 'This edition of the Code of "
                         "Practice comes into operation on 1 July 2001.'"),
    2003: ((2003, 7, 1), "wb20050519__codeofpractice2003.pdf p2: 'This edition of the Code of "
                         "Practice comes into operation on 1 July 2003.'"),
    2006: ((2006, 1, 1), "2253__code-of-practice-2006.pdf p2: 'This edition of the Code of "
                         "Practice comes into operation on 1 January 2006.'"),
    2008: ((2008, 7, 1), "2254__code-of-practice-2008.pdf p2: 'This edition of the Code of "
                         "Practice comes into operation on 1 July 2008.'"),
    2011: ((2011, 1, 1), "2255__code-of-practice-2011.pdf p2: 'This edition of the Code of "
                         "Practice comes into operation on 1 January 2011.'"),
    2012: ((2012, 1, 1), "2256__code-of-practice-2012.pdf p2: 'This edition of the Code of "
                         "Practice comes into operation on 1 January 2012.' (the SECOND 2012 "
                         "edition commenced 1 July 2012 -- 2259__code-of-practice-second-2012-"
                         "edition.pdf p2 -- but both are tagged 2012, so the earlier date is "
                         "the one that makes this tier one-sided)"),
    2014: ((2014, 1, 1), "2274__pmcpa-code-of-practice-2014.pdf p2: 'This edition of the Code of "
                         "Practice comes into operation on 1 January 2014.'"),
    2015: ((2015, 1, 1), "2275__pmcpa-code-of-practice-2015.pdf p2: 'This edition of the Code of "
                         "Practice comes into operation on 1 January 2015.'"),
    2016: ((2016, 1, 1), "2257__code-of-practice-2016.pdf p3: 'This edition of the Code of "
                         "Practice comes into operation on 1 January 2016.'"),
    2019: ((2019, 1, 1), "2258__code-of-practice-2019.pdf p3: 'This edition of the Code of "
                         "Practice comes into operation on 1 January 2019.'"),
    2021: ((2021, 7, 1), "3406__2021-abpi-code-of-practice.pdf p3: 'This edition of the Code of "
                         "Practice comes into operation on 1 July 2021.'"),
    2024: ((2024, 10, 1), "r0anf5ya__2024-abpi-code.pdf p3: 'This edition of the Code of "
                          "Practice comes into operation on 1 October 2024.'"),
}
# Three design constraints, each measured before this tier was written:
#
#   IT KEYS ON `completed`, NEVER ON `received`. Switching to received adds 7
#   cases / 16 items that are legitimate STRADDLERS -- received before
#   commencement, completed after it, which is the ordinary shape at every
#   edition boundary (AUTH/2125, 2126, 2131, 2134, 2135, 2137 at 5-6/08 and
#   AUTH/2422/7/11). A 32% false-positive rate, and it would "decide"
#   AUTH/2135/6/08, which the register REFUSES on the merits.
#
#   IT IS ONE-SIDED. The mirror rule -- "the tag is not the edition in force at
#   completion" -- flags 669 cases, overwhelmingly the legitimate
#   old-edition-for-old-conduct pattern tier (c) already prints and never fails
#   on (AUTH/2297/1/10 is the witnessed example: a January 2010 complaint about
#   an April 2004 advertisement, adjudicated under the 2003 Code).
#
#   ITS ONLY FAILURE MODE IS A WRONG `completed` VALUE, and that surface is
#   already guarded: l2/build.py's `check_date_coherence()` refuses a build
#   where received > completed, and R12's adjudications fixed the five cases
#   where the witnesses disagreed.
KNOWN_ABSENT = {
    ("AUTH/2135/6/08", 2008, "20.1"):
        "R19 residue: report names no edition, so only a date inference is left -- refused",
    ("AUTH/2135/6/08", 2008, "20.2"):
        "R19 residue: report names no edition, so only a date inference is left -- refused",
    ("AUTH/1846/6/06", 2003, "18.4"):
        "R19-or-R22(c), undecided: the 2003 Clause 18 stops at 18.3 and the report "
        "names the 2003 Code for another clause -- refused, not repaired",
}
LATE_COMPLAINT_YEARS = 3


def read_jsonl(path):
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def clause_inventory():
    """year -> {clause number as printed}. Dotted numbers stay dotted."""
    inv = defaultdict(set)
    for path in (HTML_CLAUSES, PDF_CLAUSES):
        if not path.exists():
            continue
        for row in read_jsonl(path):
            year = int(row["code_year"])
            inv[year].add(str(row["clause_number"]))
            for sub in row.get("subclauses") or []:
                if sub.get("number") is not None:
                    inv[year].add(str(sub["number"]))
            for number in row.get("subclause_numbers") or []:
                inv[year].add(str(number))
            for number in row.get("by_edition") or {}:
                inv[year].add(str(number))
    return inv


def determination(text):
    """{year: the sentence that determined it} for one report.

    Empty where the report makes no determination this reading hears; more than
    one entry where the report determines several editions, which is a real
    thing (AUTH/3876/2/24 lists outcomes under four) and not a claim about the
    case's single tagged year.
    """
    found = {}
    for sentence in SENTENCE.split(" ".join((text or "").split())):
        if PRECEDENT.search(sentence) or PARTY_VOICE.search(sentence):
            continue
        if not any(p.search(sentence) for p in DETERMINATION):
            continue
        skip = {int(m.group(1)) for pat in CANDP for m in pat.finditer(sentence)}
        for m in CODE_YEAR.finditer(sentence):
            year = int(m.group(1))
            if year not in skip:
                found.setdefault(year, sentence)
    return found


def clause_years(text):
    """{clause: {year: the sentence}} -- tier (d)'s reader.

    Every Code year in a sentence that names the clause, minus the two voices
    tier (a) already refuses to hear. Deliberately not the builder's reader:
    that one requires the year to follow the clause list ('Clauses 2, 9.1 and
    21.3 of the 2008 Code') and so cannot see 'until the 2016 Code when the
    relevant clause was 22.1', which is how AUTH/3115/11/18 states half of its
    cross-numbered outcome list.
    """
    found = {}
    for sentence in SENTENCE.split(" ".join((text or "").split())):
        years = {int(y) for m in CODE_YEAR_ANY.finditer(sentence)
                 for y in YEAR_RUN.findall(m.group(0))}
        if not years:
            continue
        skip = {int(m.group(1)) for pat in CANDP for m in pat.finditer(sentence)}
        years -= skip
        if not years:
            continue
        voiced = not (PRECEDENT.search(sentence) or PARTY_VOICE.search(sentence))
        for clause in {m.group(1) for m in CLAUSE_TOKEN.finditer(sentence)}:
            for year in years:
                slot = found.setdefault(clause, {}).setdefault(year, [sentence, voiced])
                slot[1] = slot[1] or voiced
    return found


def main():
    for path in (RECORDS, CASES):
        if not path.exists():
            raise SystemExit(f"{path} is absent; run the pipeline first")

    all_adjudications = json.loads(ADJUDICATIONS.read_text(encoding="utf-8"))
    adjudicated = {a["case"]: a for a in all_adjudications if a["field"] == "code_year"}
    # Tier (d): the reviewed per-clause year decisions, keyed (case, clause).
    reviewed = {}
    for a in all_adjudications:
        m = YEAR_FIELD.match(a["field"])
        if m:
            reviewed[(a["case"], m.group(1))] = a
    pdf_flow = ({p["html_file"]: p["flow_text"] for p in read_jsonl(PDF_RECORDS)}
                if PDF_RECORDS.exists() else {})

    cases = {c["case_number"]["value"]: c for c in read_jsonl(CASES)}
    # Tier (d) needs the TEXT, not just the determination, for the ~50 reports a
    # reviewed row names. Kept for those files only: the whole corpus's panes
    # would be ~40 MB held for a check that reads 3% of them.
    reviewed_files = {f for (num, _) in reviewed
                      for f in cases.get(num, {}).get("source_files", [])}
    claims, clause_text = {}, {}
    for rec in read_jsonl(RECORDS):
        text = pdf_flow.get(rec["file"]) or rec["panes"]["report"]["text"]
        claims[rec["file"]] = determination(text)
        if rec["file"] in reviewed_files:
            clause_text[rec["file"]] = text
    items_by_case = defaultdict(list)
    if ITEMS.exists():
        for item in read_jsonl(ITEMS):
            items_by_case[item["case_number"]].append(item)

    inv = clause_inventory()
    failures, flags = [], []
    kinds = Counter()
    agree = covered = 0
    checked_commencement = covered_commencement = 0
    no_completed = 0

    for num, case in sorted(cases.items()):
        year = case["code_year"]["value"]
        found = claims.get(case["source_files"][0], {})
        adj = adjudicated.get(num)
        kinds["silent" if not found else ("single" if len(found) == 1 else "multi_edition")] += 1

        if len(found) == 1 and year is not None:
            stated, quote = next(iter(found.items()))
            if stated == year:
                agree += 1
            elif adj is not None and adj["value"] == year:
                covered += 1
            else:
                failures.append((num, "edition_statement",
                                 f"published code_year {year}, the report determines {stated}, "
                                 f"and no adjudication covers it: {quote[:200]!r}"))
        elif adj is not None and not found:
            flags.append((num, "adjudication_without_determination",
                          f"{adj['id']} sets {adj['value']}; this reading hears no determination "
                          f"in the report, so the decision rests on other evidence"))

        for item in items_by_case.get(num, []):
            ref = item["inputs"]["clause_ref"]
            iy, clause = ref["code_year"], str(ref["clause"])
            if iy is None or iy not in inv or clause in inv[iy]:
                continue
            if (num, iy, clause) in KNOWN_ABSENT:
                continue
            failures.append((num, "clause_absent_from_edition",
                             f"item {item['item_id']} references Clause {clause} of the {iy} Code, "
                             f"an edition that does not contain it"))

        received = (case["dates"].get("received") or {}).get("value")
        if year and received and int(received[:4]) - year >= LATE_COMPLAINT_YEARS:
            flags.append((num, "old_edition_late_complaint",
                          f"received {received}, ruled under the {year} Code -- legitimate where "
                          f"the CONDUCT is that old (AUTH/2297/1/10 is the witnessed example)"))

        # -- tier (e): the tagged edition did not exist yet ------------------
        completed = (case["dates"].get("completed") or {}).get("value")
        if year in COMMENCEMENT and completed:
            (cy, cm, cd), quote = COMMENCEMENT[year]
            try:
                done = tuple(int(x) for x in completed.split("-"))
            except ValueError:
                done = None
            checked_commencement += 1
            if done and done < (cy, cm, cd):
                covered_commencement += 1
                failures.append((num, "edition_not_yet_in_operation",
                                 f"published code_year {year}, but the case COMPLETED "
                                 f"{completed}, before that edition came into operation on "
                                 f"{cy}-{cm:02d}-{cd:02d}. {quote}"))
        elif year in COMMENCEMENT:
            no_completed += 1

    # -- tier (d): every reviewed per-clause year, read back ------------------
    clause_claims = {num: clause_years(clause_text.get(case["source_files"][0], ""))
                     for num, case in cases.items()
                     if case["source_files"] and case["source_files"][0] in reviewed_files}
    for (num, clause), adj in sorted(reviewed.items()):
        case = cases.get(num)
        row = next((v for v in (case or {}).get("verdicts", []) if v["clause"] == clause), None)
        if row is None:
            failures.append((num, "reviewed_year_row_absent",
                             f"{adj['id']} decides verdicts[{clause}].code_year and L2 "
                             f"publishes no such row -- the fix has stopped applying"))
            continue
        if row.get("code_year_basis") != adj["id"]:
            failures.append((num, "reviewed_year_not_applied",
                             f"{adj['id']} decides {adj['value']!r} for Clause {clause} but the "
                             f"row's basis is {row.get('code_year_basis')!r}"))
            continue
        seen = clause_claims.get(num, {}).get(clause, {})
        if adj["value"] is None:
            if len(seen) < 2:
                failures.append((num, "reviewed_year_refusal_unwitnessed",
                                 f"{adj['id']} refuses Clause {clause} as multi-edition, but "
                                 f"this reading finds {sorted(seen) or 'no'} Code year(s) "
                                 f"attached to it"))
        else:
            hit = seen.get(adj["value"])
            if hit is None:
                failures.append((num, "reviewed_year_unwitnessed",
                                 f"{adj['id']} keys Clause {clause} to the {adj['value']} Code; "
                                 f"this reading finds only {sorted(seen) or 'no'} year(s) "
                                 f"attached to that clause"))
            elif not hit[1]:
                failures.append((num, "reviewed_year_party_voice_only",
                                 f"{adj['id']} keys Clause {clause} to the {adj['value']} Code, "
                                 f"and every sentence attaching it is a party's submission or a "
                                 f"precedent recital: {hit[0][:200]!r}"))

    live = {(num, i["inputs"]["clause_ref"]["code_year"], str(i["inputs"]["clause_ref"]["clause"]))
            for num, items in items_by_case.items() for i in items}
    stale = sorted(k for k in KNOWN_ABSENT if k not in live)
    if stale:
        failures.append(("(corpus)", "known_absent_stale",
                         f"the declared-residue table names pair(s) the bank no longer carries: "
                         f"{stale}. A residue that has been repaired must leave the table."))

    print(f"cases                      : {len(cases)}")
    print(f"report determination       : "
          + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    print(f"determination == published : {agree}")
    print(f"differs, adjudication covers it : {covered}")
    print(f"commencement checked (tier e)        : {checked_commencement} cases with a known "
          f"edition and a completed date; {covered_commencement} completed before their tagged "
          f"edition existed; {no_completed} have no completed date to check against")
    print(f"declared absent (year, clause) pairs : {len(KNOWN_ABSENT)}")
    print(f"reviewed per-clause years read back  : {len(reviewed)} "
          f"({sum(1 for a in reviewed.values() if a['value'] is not None)} promotions, "
          f"{sum(1 for a in reviewed.values() if a['value'] is None)} refusals)")
    print(f"flags, never failures      : {len(flags)}")
    for num, kind, detail in flags[:6]:
        print(f"    {num}  [{kind}] {detail}")
    if len(flags) > 6:
        print(f"    ... and {len(flags) - 6} more")

    if failures:
        print(f"\nFAILURES: {len(failures)}")
        for num, kind, detail in failures[:40]:
            print(f"  {num}  [{kind}] {detail}")
        if len(failures) > 40:
            print(f"  ... and {len(failures) - 40} more")
        return 1
    print("\nOK: every case's Code year is witnessed by its own report, structurally "
          "consistent with the edition it names, or adjudicated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
