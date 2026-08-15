"""Generate benchmark items from L2 case objects.

    python3 bench/generate.py                        # requires the real L2 corpus
    python3 bench/generate.py --use-fixture --out /tmp/pmcpa-fixture-items.jsonl \
        --exclusions /tmp/pmcpa-fixture-exclusions.jsonl
    python3 bench/generate.py --cases data/l2/cases.jsonl --out bench/items.jsonl
    python3 bench/generate.py --tasks T1,T2,T3

Reads `data/l2/cases.jsonl` (l2/SPEC.md §2) and emits one JSON object per line
conforming to `bench/item_schema.json`. Stdlib only; byte-deterministic.

Four hard rules, from bench/DESIGN.md §5 and §6. They are enforced here and
re-checked independently by bench/validate.py:

  1. Quoted text comes only from segments whose `leakage_attest.clean` is true
     AND whose `kind` is `complaint` or `response`. Nothing is ever trimmed to
     make a dirty segment usable -- the item is not generated.
  2. `metadata_shown` is an allowlist. Outcome fields, sanction fields, the
     procedure flags, the case number, the subject line and the meta
     description are excluded by construction, not by filtering.
  3. T1 shows complaint + response; T2 shows complaint only.
  4. Cases that share a source report (siblings) always land in the same split.

The attest is *not* recomputed here. DESIGN.md §1.3: leakage is a data
property, and no generator re-implements safety. This module only checks that
`clean` agrees with its own `checks` map, and refuses a segment whose attest is
malformed.
"""

import argparse
import hashlib
import json
import os
import pathlib
import sys
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench"
DEFAULT_CASES = ROOT / "data" / "l2" / "cases.jsonl"
FIXTURE_CASES = BENCH / "fixtures" / "cases.fixture.jsonl"
FIXTURE_PANES = BENCH / "fixtures" / "l1_panes.fixture.json"
L1_RECORDS = ROOT / "data" / "l1" / "records.jsonl"
L1_PDF_RECORDS = ROOT / "data" / "l1" / "pdf_records.jsonl"
DEFAULT_OUT = BENCH / "items.jsonl"
DEFAULT_EXCLUSIONS = BENCH / "exclusions.jsonl"
# N2. L2's record of every outcome-slot clause number a reader corrected or
# deleted. Only the DELETIONS matter here; a correction moves a candidate to a
# different clause and the ordinary machinery handles it.
DEFAULT_SLOT_CORRECTIONS = ROOT / "data" / "l2" / "clause_slot_corrections.jsonl"

# The active benchmark has three tasks. T2 is the complaint-only condition
# formerly emitted as `T1-triage`; giving it a first-class task id makes the
# public sequence match the experiment. Historical banks and run archives keep
# their old ids and are never rewritten.
TASKS = ("T1", "T2", "T3")
WITHDRAWN_TASKS = {}
QUOTABLE_KINDS = ("complaint", "response")
# T3 quotes the Panel ruling under appeal (DESIGN.md §5 table): panel_ruling
# segments are NEVER attest-clean -- they are rulings -- so their gate is
# different: the span must not reveal the APPEAL outcome. The bare fact that
# an appeal happened is the item's stated premise, so only outcome-coupled
# appeal language disqualifies.
APPEAL_OUTCOME_RE = re.compile(
    r"\bappeal\s+board\b[^.]{0,120}?\b(ruled|upheld|overturn\w*|no\s+breach|breach)\b"
    r"|\b(?:up)?on\s+appeal\b[^.]{0,80}?\b(ruled|upheld|overturn\w*)\b",
    re.I | re.S)
ATTEST_CHECKS = (
    "no_ruling_language",
    "no_outcome_banner",
    "no_outcome_table",
    "outside_abstract",
    "no_sanctions_text",
    # DEFECTS R26, added 2026-08-10: the publisher's outcome-stating headline
    # above the first body section, which l1/derive's literal banner strings
    # miss ('Breach of undertaking Clause 2'). Listed here so a case object
    # built before the check existed is REFUSED as malformed rather than
    # silently attested on five checks out of six.
    "no_outcome_heading",
)

# DEFECTS R25. Parity with l2/build.py MIN_RENDITION_CHARS: an extract shorter
# than this is not worth a benchmark item. See the refusal for the counts.
MIN_EXTRACT_CHARS = 200

# Rule 2. Everything the model may see about the case besides the extract.
# Anything not listed is withheld; the reasons for the notable exclusions:
#   verdicts, appeal, sanctions   - the labels
#   procedure.*                   - abridged/voluntary_admission/outwith_scope
#                                   are outcome-bearing (outwith_scope IS the
#                                   and are outcome-bearing)
#   subject                       - L2 C4: the hero h2 routinely states the
#                                   outcome ("No breach of the Code")
#   title                         - carries procedural suffixes
#   case_number, sibling_cases    - memorisation handles
#   dates.completed               - correlates with appeal and procedure
#   quality, entities, renditions - build metadata
SAFE_METADATA = (
    "respondent",
    "complainant_category",
    "complainant_anonymous",
    "complainant_contactable",
    "code_year",
    "date_received",
)
# T3 additionally shows the Panel ruling and the appellant. Those are the
# premise of "does this ruling survive appeal?" (DESIGN.md §2 T3), not its
# label -- the label is the Appeal Board outcome. This is the one place where
# an outcome field is shown, and it is task-specific by design; see
# bench/README.md, "Deviations and open questions".
T3_METADATA = ("panel_ruling_for_clause", "appellant")

SPLITS = (("train", 0.60), ("dev", 0.80), ("test", 1.00))


# --- text resolution -------------------------------------------------------
# L2 segments carry offsets, not text (SPEC §1.3, "offsets, not copies"), so a
# consumer must re-slice L1 pane text. Two backends, one interface.

class FixtureResolver:
    """Pane text from bench/fixtures/l1_panes.fixture.json."""

    def __init__(self, path):
        self.panes = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        self.origin = str(path)

    def pane(self, file, pane):
        try:
            return self.panes[file][pane]
        except KeyError:
            raise KeyError(f"fixture has no pane {file}:{pane}")


class L1Resolver:
    """Pane text from data/l1/records.jsonl (+ pdf_records.jsonl for pane 'flow').

    Only the (file, pane) pairs actually referenced are held in memory --
    records.jsonl is ~200 MB and we typically need a fraction of it.
    """

    def __init__(self, records, pdf_records, wanted):
        self.origin = str(records)
        self.panes = {}
        want_files = {f for f, _ in wanted}
        want_html = {(f, p) for f, p in wanted if p != "flow"}
        want_flow = {f for f, p in wanted if p == "flow"}

        if want_html:
            self._scan(records, want_files, want_html, flow=False)
        if want_flow:
            if not pathlib.Path(pdf_records).exists():
                raise SystemExit(f"segments reference pdf panes but {pdf_records} is absent")
            self._scan(pdf_records, want_flow, {(f, "flow") for f in want_flow}, flow=True)

        missing = sorted(p for p in wanted if p[1] not in self.panes.get(p[0], {}))
        if missing:
            raise SystemExit(
                f"{len(missing)} segment pane(s) referenced by L2 are not in {records}:\n"
                + "\n".join(f"  {f}:{p}" for f, p in missing[:10])
                + (f"\n  ... and {len(missing) - 10} more" if len(missing) > 10 else "")
                + "\n(L2 segments carry offsets, not text -- the referenced L1 record must exist.)"
            )

    def _scan(self, path, want_files, want_pairs, flow):
        with pathlib.Path(path).open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                name = rec.get("file")
                if name not in want_files:
                    continue
                if flow:
                    # SPEC §2 does not name the pane for source="pdf" segments;
                    # we assume "flow" -> pdf_records.flow_text. See README.
                    self.panes.setdefault(name, {})["flow"] = rec["flow_text"]
                else:
                    for _, pane in [p for p in want_pairs if p[0] == name]:
                        self.panes.setdefault(name, {})[pane] = rec["panes"][pane]["text"]

    def pane(self, file, pane):
        try:
            return self.panes[file][pane]
        except KeyError:
            raise KeyError(f"L1 has no pane {file}:{pane}")


def slice_ref(resolver, ref):
    text = resolver.pane(ref["file"], ref["pane"])
    start, end = ref["char_start"], ref["char_end"]
    if not (0 <= start <= end <= len(text)):
        raise ValueError(f"ref {ref} does not lie inside {ref['file']}:{ref['pane']} ({len(text)} chars)")
    return text[start:end]


# --- helpers ---------------------------------------------------------------

def write_jsonl_atomic(path, rows):
    """Write, then rename into place.

    items.jsonl is 124 MB and takes seconds to write; a truncate-and-rewrite
    leaves a window in which every reader -- the site exporter, the validators,
    an audit reading the bank while a build runs -- sees a half-file and
    reports defects that are not there. os.replace is atomic within a
    filesystem, so a reader sees either the old bank or the new one, never a
    prefix of either.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def read_slot_corrections(path):
    """L2's clause-slot corrections, or [] when the file is absent.

    Absent is a real state: the fixture path (`--use-fixture`) builds from
    invented cases that have no such artefact, and a build of a corpus in which
    no slot was ever corrected writes an empty file. Neither is an error.
    """
    p = pathlib.Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def exclude(skips, case_number, task, clause, reason, detail):
    """Record one excluded item-candidate.

    Every skip goes here and nowhere else (DEFECTS D5). They used to be printed
    to stdout as prose, which meant the one exclusion that was WRONG -- a real
    item dropped by a rule that should not have fired -- was invisible to every
    audit that read the bank rather than watched the build. `bench/exclusions.
    jsonl` makes the negative space of the bank a queryable artefact: what was
    NOT generated, for which case and clause, under which rule.
    """
    skips.append({"case_number": case_number, "task": task,
                  "clause": None if clause is None else str(clause),
                  "reason": reason, "detail": detail})


def value_of(receipt, default=None):
    """L2 canonical scalars are {value, basis, sources} objects (SPEC §1.2)."""
    if isinstance(receipt, dict) and "value" in receipt:
        return receipt["value"]
    return default if receipt is None else receipt


def attest_ok(segment, case_number, problems):
    """Trust `clean`; refuse a malformed attest. Never recompute the checks."""
    att = segment.get("leakage_attest")
    if not isinstance(att, dict) or "clean" not in att or not isinstance(att.get("checks"), dict):
        problems.append(f"{case_number}: segment {segment.get('kind')} has no usable leakage_attest")
        return False
    missing = [k for k in ATTEST_CHECKS if k not in att["checks"]]
    if missing:
        problems.append(f"{case_number}: attest missing checks {missing}")
        return False
    if att["clean"] != all(bool(v) for v in att["checks"].values()):
        problems.append(f"{case_number}: attest.clean disagrees with its own checks")
        return False
    return bool(att["clean"])


def ref_key(ref):
    return (ref["file"], ref["pane"], ref["char_start"], ref["char_end"])


# --- reading the report's own words about matters, clauses and appeals ------
# DEFECTS R31 (task 1b), R32 (i) and (ii), R18 (b). These four repairs all ask
# the same three questions of the report -- which matter is this ruling in, does
# this span name this clause, and was THIS ruling appealed -- so they share the
# readers below rather than growing four screens.
#
# The readers live here and not in l2/build.py deliberately. None of them
# produces a canonical case value: they decide what bench QUOTES and what header
# it prints over a quoted block, which is a bench-layer question about
# presentation. The one thing they do read from L2 is its receipts -- the
# `rulings[]` offsets and `regard_ref` matter headings that R28 stage 1 added --
# so the matter structure is L2's reading, not a second one.

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def sentences(text):
    """Whitespace-collapsed sentences. Collapsed first, because the panes wrap
    mid-sentence and a split on the raw text yields fragments."""
    return [s for s in (t.strip() for t in SENTENCE_SPLIT_RE.split(" ".join((text or "").split()))) if s]


def sentence_key(sentence):
    return hashlib.sha256(" ".join((sentence or "").split()).encode("utf-8")).hexdigest()[:12]


# A clause number is only "named" where a Clause/Code cue introduces it. The
# bare number is not enough: reports quote SPC section numbers, study arms and
# percentages in the same prose, and '7.2' inside 'Section 7.2 of the SPC' says
# nothing about Clause 7.2. This is the sweep the round-2 audit used to measure
# the 268 at-risk renditions and the 32 wrong-matter items, re-typed here.
CLAUSE_CUE_RE = re.compile(r"\bClauses?\b|\bCode\b", re.I)
CLAUSE_CUE_WINDOW = 120


def names_clause(text, clause):
    """Does `text` name this clause, introduced by a Clause/Code cue?

    The boundaries matter more than the cue: without them '3.2' matches inside
    '13.2' and '3.21', which would turn the coverage test below into a rubber
    stamp on the corpus's commonest clause numbers.
    """
    if not clause or not text:
        return False
    pat = re.compile(r"(?<![\d.])" + re.escape(str(clause)) + r"(?![\d.]*\d)")
    flat = " ".join(text.split())
    for m in pat.finditer(flat):
        if CLAUSE_CUE_RE.search(flat, max(0, m.start() - CLAUSE_CUE_WINDOW), m.start()):
            return True
    return False


def matter_starts(case):
    """Sorted char_starts of the report's MATTER headings, from L2's receipts.

    R28 stage 1 attached `regard_ref` to every attributed ruling: the matter
    heading the report itself writes above the matter that ruling belongs to
    (`matter_headings` in l2/build.py -- 642 headings over 216 files, null on
    every single-matter report). Collecting them across the case's verdict rows
    reconstructs the matter partition of the report pane without re-deriving it.
    """
    return sorted({r["regard_ref"]["char_start"]
                   for v in case.get("verdicts") or []
                   for r in (v.get("rulings") or [])
                   if r.get("regard_ref")})


def item_matter_spans(case, verdict):
    """[(start, end)] of the matters this clause's PANEL ruling(s) were made in.

    Panel rulings only. The Board's rulings sit in the appeal sections, whose
    nearest preceding matter heading is the same matter or a later one, and the
    question this answers is which COMPLAINT and RESPONSE set the Panel's ruling
    up -- that is the matter the item is about.
    """
    starts = matter_starts(case)
    if not starts:
        return []
    mine = sorted({r["regard_ref"]["char_start"]
                   for r in (verdict.get("rulings") or [])
                   if r.get("body") == "panel" and r.get("regard_ref")})
    spans = []
    for s in mine:
        later = [x for x in starts if x > s]
        spans.append((s, later[0] if later else float("inf")))
    return spans


def in_any_span(seg, spans):
    """A segment belongs to a matter if it opens inside that matter's region.
    Report pane only: `regard_ref` offsets are report-pane offsets, and the 13
    PDF substitutions carry no section structure to partition (SPEC's own
    `regard: null` case)."""
    if seg["ref"]["pane"] != "report":
        return False
    start = seg["ref"]["char_start"]
    return any(lo <= start < hi for lo, hi in spans)


# --- R32(i): is THIS panel_ruling block's ruling under appeal? --------------
# The old header was a constant -- every panel_ruling span in every T3 extract
# was stamped '[PANEL RULING UNDER APPEAL]'. 72 of the 316 T3 items carry more
# than one such block and 37 carry more blocks than the report has 'APPEAL BY'
# headings, so the constant asserted appeals that were never made:
# AUTH/1941/1/07's matter-2 block is followed straight by matter 3's heading and
# was provably not appealed (round-2 audit, lead 11).
#
# The report answers the question itself, in a fixed sentence right after the
# ruling: 'This ruling was appealed.' / 'These rulings were not appealed.' Over
# the 553 panel_ruling segments of appealed cases the loose screen (a sentence
# carrying both 'ruling' and an appeal verb) finds 309 sentences; the frame
# below decides 283 of them and APPEAL_STATUS_READ decides the other 26 by hand.
# The registry is what makes this DECIDE the block rather than match most of it:
# an unregistered screened sentence stops the build, because the residue holds
# both genuine statements ('The complainants appealed this ruling.') and things
# that must NOT count -- a conditional, two recitals of OTHER cases' appeals,
# and two sentences that are mixed on their face.
PANEL_RULING_HEAD_UNDER_APPEAL = "[PANEL RULING UNDER APPEAL]"
PANEL_RULING_HEAD_NEUTRAL = "[PANEL RULING]"

APPEAL_STATUS_SCREEN_RE = re.compile(r"\bruling", re.I)
APPEAL_VERB_RE = re.compile(r"\bappeal(?:ed|s)\b", re.I)
APPEAL_STATUS_FRAME_RE = re.compile(
    r"^(?:all\s+(?:of\s+)?(?:the|these)\s+|the\s+above\s+|this\s+|these\s+|both\s+|"
    r"the\s+panel(?:['’]s)?\s+)?"
    r"rulings?\s+(?:of\s+(?:a\s+)?(?:no\s+)?breach(?:es)?\s+of\s+[^.]{0,70}?\s+)?"
    r"(?:was|were)\s+(not\s+)?appealed(?:\s+by\s+[^.]{1,60})?\s*[.]?$", re.I)

# The 21 distinct sentences the frame does not decide, each read in place. Values
# are (verdict, quote): 'appealed' / 'not_appealed' / 'mixed' / None, where None
# means "this is not a statement about whether a ruling of THIS case was
# appealed" and contributes nothing.
APPEAL_STATUS_READ = {
    # Plain statements the frame's opening alternatives do not reach.
    sentence_key("All the above rulings were appealed by Johnson & Johnson."):
        ("appealed", "AUTH/2475/1/12 -- 'all the above' is the frame's shape with an extra word"),
    sentence_key("The complainants appealed this ruling."):
        ("appealed", "AUTH/3151/1/19 -- the active voice of 'This ruling was appealed'"),
    sentence_key("These rulings were appealed as above."):
        ("appealed", "AUTH/2448/10/11"),
    sentence_key("All of the Panel’s rulings in Point B were appealed."):
        ("appealed", "AUTH/2394+2395/3/11 -- Point B is the matter this block rules on"),
    sentence_key("A ruling of a breach of Clause 7.4 was appealed."):
        ("appealed", "AUTH/2273/10/09"),
    sentence_key("The ruling of no breach of Clause 22 was appealed by ProStrakan."):
        ("appealed", "AUTH/1862/7/06"),
    sentence_key("This ruling in relation to post H was appealed by Leo."):
        ("appealed", "AUTH/3428/11/20"),
    sentence_key("This ruling of Clause 2 was appealed by GlaxoSmithKline."):
        ("appealed", "AUTH/3515/5/21"),
    sentence_key("The complainant in this case appealed the Panel’s ruling of no breach of Clause 2."):
        ("appealed", "AUTH/2294/1/10, AUTH/2297/1/10, AUTH/2538/10/12 -- 'in this case' is explicit"),
    sentence_key("However, he/she appealed the ruling of no breach of Clause 2."):
        ("appealed", "AUTH/3518/5/21"),
    sentence_key("APPEAL FROM SANOFI Sanofi appealed the Panel’s ruling of a breach of Clause 9.1 in "
                 "relation to the use of an 0845 telephone number for its medical information service."):
        ("appealed", "AUTH/3281/11/19 -- an APPEAL FROM heading the segment boundary absorbed"),
    sentence_key("The Alliance submitted that that the email did not require prescribing information and "
                 "it therefore appealed the Panel’s ruling of a breach of Clause 4.1."):
        ("appealed", "AUTH/3246+3247/9/19 -- the appellant's own statement that it appealed"),
    sentence_key("Summary The Alliance submitted that it appealed the Panel’s rulings of breaches of: "
                 "• Clause 4.1 because the email did not require inclusion of Eliquis prescribing "
                 "information • Clause 7.4 because the heading was capable of substantiation • "
                 "Clause 7.2 because the claim was not misleading • Clause 9.1 because the claim did "
                 "not discourage the rational use of the medicine and high standards were met at all times "
                 "• Clause 2 because all the relevant information was present and patient safety had "
                 "not been compromised"):
        ("appealed", "AUTH/3246+3247/9/19 -- the grounds summary, same appeal as the row above"),
    sentence_key("The complainant stated that whilst he/she was not happy about the decision of no breach "
                 "of Clause 26.2 on the basis of ‘a narrow technical point’ he/she saw little to "
                 "be gained in disputing it at this stage and had no wish to appeal that decision [Please "
                 "note this ruling was subsequently appealed in relation to the second press release "
                 "– see further below]."):
        ("appealed", "AUTH/3518/5/21 -- the publisher's own bracketed correction supersedes the clause"),
    # Mixed on their face: the block holds an appealed ruling and an unappealed
    # one, so no single header is true of it.
    sentence_key("Breaches of Clauses 7.2 (not appealed) and 7.4 (this ruling was appealed) were ruled."):
        ("mixed", "AUTH/2273/10/09 -- states both polarities inside one sentence"),
    sentence_key("These rulings were appealed by Teva except the Panel’s ruling of a breach of "
                 "Clause 15.9 which was accepted."):
        ("mixed", "AUTH/2017/7/07 -- an explicit exception carves one ruling out"),
    # Not statements about whether a ruling of THIS case was appealed.
    sentence_key("The Panel noted its comments above and considered that if its rulings of breaches of the "
                 "Code were appealed, it would require, in accordance with Paragraph 7.1 of the "
                 "Constitution and Procedure, the promotional campaign at issue to be suspended pending "
                 "the final outcome of the case."):
        (None, "AUTH/2723/7/14 -- conditional; 'if its rulings ... were appealed'"),
    sentence_key("AstraZeneca (Case AUTH/3046/6/18) and Pfizer (Case AUTH/3045/6/18) appealed those "
                 "rulings."):
        (None, "AUTH/3043/6/18 -- OTHER cases' appeals, named by case number"),
    sentence_key("The Panel noted that Alexion had not appealed the Panel’s rulings of breaches of "
                 "the Code in Case AUTH/3051/6/18."):
        (None, "AUTH/3163/2/19 -- another case's appeal (AUTH/3051/6/18)"),
    sentence_key("This text had been omitted from the above in error.] * * * * * Following its completion "
                 "of the consideration of all four appeals in the clinical trial cases on 18 September "
                 "2019 (Cases AUTH/3079/9/18, Pfizer, AUTH/3087/9/18 (GlaxoSmithKline), AUTH/3118/11/18 "
                 "(Tesaro) and AUTH/3102/9/18 (Lilly), the Appeal Board noted that the respondent "
                 "companies in Case AUTH/3084/9/18 (Boehringer Ingelheim), Case AUTH/3091/9/18 (UCB), "
                 "Case AUTH/3097/9/18 (Teva), and Case AUTH/3099/9/18 (Allergan), accepted the Panel’s "
                 "rulings of breaches of the Code and had not appealed."):
        (None, "AUTH/3084/9/18 -- the clinical-trial series note; every ruling it names is another case's"),
    sentence_key("[Post consideration note; It was noted that in relation to trial QV-001/2007-Pae, "
                 "Teva’s submission was that it was preparing the results summary and it would be "
                 "posted as soon as possible] * * * * * Following its completion of the consideration of "
                 "all four appeals in the clinical trial cases on 18 September 2019 (Cases AUTH/3079/9/18 "
                 "(Pfizer), AUTH/3087/9/18 (GlaxoSmithKline), AUTH/3118/11/18 (Tesaro) and AUTH/3102/9/18 "
                 "(Lilly), the Appeal Board noted that the respondent companies in Case AUTH/3084/9/18 "
                 "(Boehringer Ingelheim), Case AUTH/3091/9/18 (UCB), Case AUTH/3097/9/18 (Teva), and Case "
                 "AUTH/3099/9/18 (Allergan), accepted the Panel’s rulings of breaches of the Code and "
                 "had not appealed."):
        (None, "AUTH/3097/9/18 -- the same note on the sibling page"),
}


def appeal_status(text, unregistered):
    """'appealed' | 'not_appealed' | 'mixed' | None for one panel_ruling block.

    `unregistered` collects screened sentences neither the frame nor the
    registry decides, for the build-time refusal in `main`. A missed POSITIVE
    only costs a neutral header (an under-claim, which is safe); a missed
    NEGATIVE would let a later witness stamp UNDER APPEAL over a ruling the
    report says was not appealed, which is the defect itself. So the screen
    refuses rather than shrugging.
    """
    seen = set()
    for s in sentences(text):
        if not (APPEAL_STATUS_SCREEN_RE.search(s) and APPEAL_VERB_RE.search(s)):
            continue
        m = APPEAL_STATUS_FRAME_RE.match(s)
        if m:
            seen.add("not_appealed" if m.group(1) else "appealed")
            continue
        row = APPEAL_STATUS_READ.get(sentence_key(s))
        if row is None:
            unregistered.append(s)
            continue
        if row[0] is not None:
            seen.add(row[0])
    if "mixed" in seen or {"appealed", "not_appealed"} <= seen:
        return "mixed"
    if "appealed" in seen:
        return "appealed"
    if "not_appealed" in seen:
        return "not_appealed"
    return None


# --- R18(b): who appealed THIS clause's ruling ------------------------------
# `metadata_shown.appellant` was `case.appeal.by`, one value per case, and
# run.py renders it as 'Both parties appealed that ruling'. On AUTH/1871/7/06
# that sentence is false for both of its T3 items: the report splits the appeals
# by clause ('Sanofi-Aventis appealed the Panel's rulings of breaches of Clauses
# 3.2, 7.2 and 7.4' / 'The complainant appealed the Panel's rulings of no breach
# of Clauses 2 and 9.1'). All 14 `both` items are in eval splits.
#
# Two witnesses, both the report's own words, tried in this order:
#   (a) the status sentence right after the ruling names the appellant --
#       'This ruling was appealed by Genzyme.' This is per-RULING and is the
#       stronger of the two; it decides all seven of AUTH/2528/8/12's items,
#       four to Shire (respondent) and three to Genzyme (complainant).
#   (b) the APPEAL BY/FROM section's own scope sentence names the clause.
# Where a single party appealed, the case-level value is kept: the premise
# 'The respondent company appealed that ruling' cannot be false about a clause
# the Board went on to rule, because nobody else appealed anything.
APPEAL_STATUS_PARTY_RE = re.compile(
    r"rulings?\s+(?:of\s+[^.]{0,70}?\s+)?(?:was|were)\s+appealed\s+by\s+(?P<party>[^.]{1,60})\.?$", re.I)
APPEAL_SCOPE_RE = re.compile(
    r"^(?P<subject>.{1,60}?)\s+appealed\s+(?:all\s+)?the\s+Panel(?:['’]s)?\s+rulings?\s+of\s+"
    r"(?:a\s+)?(?:no\s+)?breach(?:es)?\s+of\s+(?P<clauses>Clauses?\s+[\d.,\s]*\d(?:\s*(?:and|&)\s*"
    r"\d+(?:\.\d+)*)?)", re.I)
CLAUSE_TOKEN_RE = re.compile(r"\d+(?:\.\d+)*")


def party_side(case, phrase):
    """'respondent' | 'complainant' | None for a party named in the report.

    Deliberately narrow. The l2 machinery (`heading_appellant`) folds company
    names against every alias the corpus writes; this one only has to separate
    the two parties of ONE case, and it refuses rather than guessing -- an
    unmatched name leaves the clause undecided and the item is excluded.
    """
    words = {w for w in re.sub(r"[^a-z ]", " ", (phrase or "").casefold()).split() if w != "the"}
    if not words:
        return None
    if words & {"complainant", "complainants"}:
        return "complainant"
    parties = case.get("parties") or {}
    respondent = value_of(parties.get("respondent")) or ""
    complainant = parties.get("complainant") or {}
    named = complainant.get("verbatim") if complainant.get("category") == "company" else None
    sides = set()
    for side, name in (("respondent", respondent), ("complainant", named)):
        other = {w for w in re.sub(r"[^a-z ]", " ", (name or "").casefold()).split() if w != "the"}
        if other and (words == other or words < other or other < words):
            sides.add(side)
    return sides.pop() if len(sides) == 1 else None


def ruling_appellants(case, verdict, resolver):
    """[(side, quote)] read from the status sentence after each Panel ruling."""
    out = []
    for r in verdict.get("rulings") or []:
        if r.get("body") != "panel":
            continue
        try:
            tail = resolver.pane(r["file"], r["pane"])[r["char_end"]:r["char_end"] + 400]
        except KeyError:
            continue
        nxt = sentences(tail)
        if not nxt:
            continue
        m = APPEAL_STATUS_PARTY_RE.search(nxt[0])
        if not m:
            continue
        side = party_side(case, m.group("party"))
        if side:
            out.append((side, nxt[0]))
    return out


def appeal_scopes(case, resolver):
    """[(side, {clauses}, quote)] from the report's own APPEAL BY/FROM sections.

    The join is L2's published heading list against the segment text: an
    APPEAL_GROUNDS section becomes an `appeal_comments` segment whose span opens
    with the heading itself, so a segment starting with one of
    `report_appeal_headings` IS that section. The scope sentence is the
    section's FIRST sentence and only that -- deeper in the body the parties
    recite each other's appeals ('since Astellas had appealed the ruling of a
    breach of Clause 3.2, ...'), which is a statement about the other side.
    """
    headings = ((case.get("appeal") or {}).get("sources") or {}).get("report_appeal_headings") or []
    heads = sorted({" ".join(h.split()) for h in headings if h}, key=len, reverse=True)
    out = []
    for seg in case.get("segments") or []:
        if seg["kind"] != "appeal_comments":
            continue
        try:
            text = " ".join(slice_ref(resolver, seg["ref"]).split())
        except (KeyError, ValueError):
            continue
        head = next((h for h in heads if text.startswith(h)), None)
        if head is None:
            continue
        body = sentences(text[len(head):])
        if not body:
            continue
        m = APPEAL_SCOPE_RE.match(body[0])
        if not m:
            continue
        side = party_side(case, m.group("subject"))
        if side is None:
            continue
        out.append((side, set(CLAUSE_TOKEN_RE.findall(m.group("clauses"))), body[0]))
    return out


def clause_appellant(case, verdict, resolver):
    """(side, basis, quote) for the T3 premise, or (None, reason, None).

    Only `both` cases are read. A single-party case keeps its case-level value
    with the basis naming why that is safe.
    """
    by = (case.get("appeal") or {}).get("by")
    if by != "both":
        return by, "case_level_sole_appellant", None
    read = ruling_appellants(case, verdict, resolver)
    basis = "ruling_status_sentence_names_the_appellant"
    if not read:
        read = [(side, quote) for side, clauses, quote in appeal_scopes(case, resolver)
                if verdict.get("clause") in clauses]
        basis = "appeal_by_heading_scope_names_this_clause"
    sides = {side for side, _ in read}
    if len(sides) == 1:
        return sides.pop(), basis, read[0][1]
    if len(sides) > 1:
        return "both", "both_parties_appealed_this_ruling", read[0][1]
    return None, "both_parties_appealed_the_case_and_no_witness_names_this_clause", None


def doc_order(seg):
    """Where a segment sits in the document it was cut from.

    THE ordering key for quoted chunks (DEFECTS D5). Segments used to be sorted
    by kind, so a report that runs COMPLAINT / RESPONSE / COMPLAINT / RESPONSE
    -- one exchange per allegation, which is how the longer cases are written --
    was re-stitched into every complaint followed by every response, silently
    re-ordering the argument the model is asked to read (522 T1 items). Document
    position restores the exchange; the per-chunk [COMPLAINT] / [RESPONSE ...]
    headers repeat as often as the document alternates.
    """
    return ref_key(seg["ref"])


# DEFECTS R24. `[^.]` cannot cross the decimal point of a clause number, so
# every window in this list was blind to the corpus's commonest ruling form:
# "No breach of Clause 2 was ruled" tripped, "No breach of Clause 9.2 was
# ruled" did not. _GAP allows a '.' only where a digit follows it -- a decimal
# point, never a sentence end, which is the only thing `[^.]` was ever for.
# The same fix runs in l2/build.py's RULING_RE and l2/validate.py's
# independent reading; verify/ruling_battery.py holds the cases all three must
# agree on.
_GAP = r"(?:[^.]|\.(?=\d))"

# The one pattern in the list that names no ruling body. Hoisted out so
# PRECEDENT_EXEMPT can hold the OBJECT rather than a list index -- an index
# silently retargets the exemption the first time someone reorders TRIPWIRE.
BREACH_STATEMENT_RE = re.compile(
    r"\b(?:no\s+)?breach(?:es)?\s+of\s+(?:clauses?|(?:the\s+)?code)\b" + _GAP +
    r"{0,40}?\bw(as|ere)\s+(?:\w+\s+)?ruled\b", re.I)

TRIPWIRE = [
    (re.compile(r"\bruled\s+(a\s+)?(no\s+)?breach\b", re.I), "ruling language"),
    (re.compile(r"\bthe\s+panel\s+ruled\b", re.I), "ruling language"),
    # NOT the bare 'no breach of Clause X': responses legitimately DENY breaches
    # ('AbbVie submitted that there was no breach of Clause 7.2') and complaints
    # allege them -- verified corpus-wide 2026-08-02: every bare-pattern hit is a
    # denial/allegation, and every ruling-ATTRIBUTED hit sits on a T3 item where
    # the Panel ruling is the sanctioned premise. Attribution is what leaks.
    (re.compile(r"(panel|appeal board)" + _GAP + r"{0,60}ruled" + _GAP + r"{0,60}no\s+breach", re.I),
     "ruling language"),
    # R24 widened this one three ways as well as the decimal: it read
    # `no\s+breach\s+of\s+clauses?`, so the POSITIVE ruling ("A breach of
    # Clause 7.10 was thus ruled") had no tripwire at all -- pattern 1 above
    # needs 'ruled' BEFORE 'breach' and never fires on the passive -- and
    # neither did "No breach of Code 7.2 was ruled", the spelling AUTH/1816/3/06
    # uses. Measured over the whole pre-fix bank: the widened form drops 4
    # items and they are exactly R24's four (AUTH/1797/2/06).
    (BREACH_STATEMENT_RE, "ruling language"),
    # NOT the bare 'Appeal Board': complaints cite OTHER cases' appeal decisions
    # as precedent (41 items, verified 2026-08-02 to be citations, not outcomes;
    # this case's appeal outcome cannot appear in complaint/response segments,
    # which end before the ruling sections). Outcome-coupled mentions only.
    (re.compile(r"\bappeal\s+board\b" + _GAP + r"{0,120}?\b(ruled|upheld|overturn\w*)\b" + _GAP +
                r"{0,60}?\bthis\b|\bappeal\s+board\s+ruled\b", re.I), "appeal ruling language"),
    (re.compile(r"\bpublic\s+reprimand\b", re.I), "sanction text"),
    (re.compile(r"\badvertisement\s+in\s+the\s+medical\s+press\b", re.I), "sanction text"),
    (re.compile(r"\bsuspension\s+from\s+the\s+ABPI\b", re.I), "sanction text"),
]

# The one tripwire pattern that names no ruling body, and so the one whose hit
# can belong to ANOTHER case: "In case AUTH/3676/7/22, a breach of clause 25.3
# was ruled" is that case's ruling, quoted by a complainant as precedent -- the
# legitimate class the comments above document. A hit is exempt when a case
# number other than this item's own sits within 120 characters of it. Scoped to
# this pattern alone, deliberately: the others name the Panel or the Appeal
# Board, and a party restating another body's ruling is what leaks (DEFECTS
# D3). Measured at l2 level: exempting the body-naming frames too would have
# flipped 72 complaint/response segments from refused to quotable.
PRECEDENT_EXEMPT = {BREACH_STATEMENT_RE}
PRECEDENT_WINDOW = 120
CASE_NUM_RE = re.compile(r"\b([A-Z]{3,})\s*/?\s*(\d{2,5})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\b")


def normalise_case_number(m):
    prefix, serial, month, year = m.groups()
    return f"{prefix.upper()}/{int(serial)}/{int(month)}/{year[-2:]}"


def own_case_numbers(*names):
    return {normalise_case_number(m)
            for name in names if name
            for m in CASE_NUM_RE.finditer(name)}


def tripwire_hit(text, own_cases):
    """The first tripwire match that is not another case's precedent citation.

    Returns (match, what) or (None, None). bench/validate.py calls this rather
    than looping the patterns itself, so the exemption cannot be applied in the
    generator and forgotten in the checker.
    """
    for pattern, what in TRIPWIRE:
        pos = 0
        while True:
            m = pattern.search(text, pos)
            if m is None:
                break
            if pattern in PRECEDENT_EXEMPT:
                lo = max(0, m.start() - PRECEDENT_WINDOW)
                hi = min(len(text), m.end() + PRECEDENT_WINDOW)
                cited = {normalise_case_number(c) for c in CASE_NUM_RE.finditer(text[lo:hi])}
                if cited - set(own_cases):
                    # Resume one character in, not past the match: a real leak
                    # can begin inside a citation's span.
                    pos = m.start() + 1
                    continue
            return m, what
    return None, None


# --- own-case-number redaction, SERVED TEXT ONLY (decision of 2026-08-11)
#
# Wave C measured 3,156 items carrying their OWN case number in the text the
# model reads. That is an IDENTITY channel, not an outcome leak: 'AUTH/1851/6/06'
# is the lookup key for the published adjudication, so a model that has read the
# corpus can recall the ruling instead of reasoning from the complaint, and the
# prompt's own docstring already claims the case number never appears.
#
# OTHER cases' numbers STAY. 'In Case AUTH/1756/9/05 a breach of Clause 7.2 was
# ruled' is the complainant's argument, put in front of the Panel; deleting it
# would delete the allegation, and it is the very span `tripwire_hit`'s
# precedent exemption above is built around. L1 and L2 keep the raw text -- this
# is a property of bench's served text, applied after every guard has read the
# source span.
#
# The match is anchored on the SERIAL, not on the whole string, because the
# corpus misspells its own numbers in every OTHER position:
#     'Case AUTH/0274/08/24'  for CASE/0274/08/24   (wrong prefix)
#     'CASE AUTH/2102/3/08'   for AUTH/2102/2/08    (wrong month)
#     'AUTH/3166//2/19', 'AUTH/2240/6//09'          (doubled separator)
#     'AUTH/ 3422/11/20'                            (space inside)
#     'Case/0221/07/24', 'Case 0401/12/24'          (prefix without slash)
#     'CASE AUTH2583/3/13'                          (no separator at all)
#     'Case 0216'                                   (no month/year at all)
#     '0216/06/24', '0496/03/25'                    (no prefix at all)
# Serials are globally unique across the 2,004 L2 cases (0 collisions measured)
# and the AUTH range 1789-3926 does not meet the CASE range 209-838, so the
# serial decides the case whatever the rest of the string says.
#
# DECIDED over every own-serial occurrence in the served text of the pre-fix
# bank (10,205 items; extracts + renditions; the scan is `residual_case_ids`
# below, which looks for the serial as a standalone number and asks only
# whether its NEIGHBOURHOOD is case-number shaped):
#   prefix + serial + month + year          3,575 occ    REDACT (CASE_ID_RE)
#   prefix + serial, no month/year              8 occ    REDACT (CASE_ID_RE)
#       one string corpus-wide, 'Case 0216' in CASE/0216/06/24
#   serial + month + year, no prefix           18 occ    REDACT (BARE_CASE_ID_RE)
#       two strings, both CASE-era: '0216/06/24', '0496/03/25'
#   serial inside an all-serial comma run       6 occ    REDACT (SERIAL_RUN_RE)
#       one string corpus-wide -- AUTH/2070/11/07's source artefact
#       '2070, 2072, 2073, 1993, 1994, 1995, 1895, 1908, 1897, 1896', every
#       number in it a known case serial (verified against L2), and no other
#       run of three or more comma-separated four-digit numbers exists
#       anywhere in the bank. Only the OWN and SIBLING serials in the run are
#       replaced; 1993 and the rest are other cases and stay, exactly as a
#       prefixed citation of them would.
#   serial in any other context                 0 occ
# and 182 prefixed tokens (27 distinct) carry a serial NO case in the corpus
# has -- every one of them hosted by a DIFFERENT case (AUTH/1756/9/05 inside
# AUTH/1854/6/06, 'Case 0401/12/24' inside CASE/0428/01/25, and so on), i.e.
# citations of cases we never scraped. So no own-case number hides behind an
# unknown serial, and serial-anchoring loses nothing.
#
# `residual_case_ids` re-derives that measurement at BUILD time and main()
# REFUSES on anything it still sees, naming the string: a spelling nobody has
# decided must stop the build, not ship redacted-except-for-one-form.
REDACTION_TOKEN = "[CASE NO.]"
# One separator: slash, backslash or any dash, doubled or not, spaces allowed.
_ID_SEP = r"[ \t]*[/\\\-‐-―]{1,2}[ \t]*"
# The prefix may be joined to the serial by punctuation, by whitespace, or by
# nothing at all; `\d{2,5}` cannot swallow a following prefix word, so 'Case
# AUTH/1851/6/06' matches at AUTH and prints 'Case [CASE NO.]'.
_ID_PREFIX_SEP = r"(?:[ \t]|[/\\\-‐-―]){0,3}"
CASE_ID_RE = re.compile(
    r"(?i)\b(?:AUTH|CASES|CASE)" + _ID_PREFIX_SEP + r"(\d{2,5})"
    r"(?:" + _ID_SEP + r"\d{1,2}" + _ID_SEP + r"\d{2,4}\b)?")
BARE_CASE_ID_RE = re.compile(
    r"(?<![\w/])(\d{4})[ \t]*/[ \t]*\d{1,2}[ \t]*/[ \t]*\d{2,4}(?![\w/])")
SERIAL_RUN_RE = re.compile(r"(?<![\d/])\d{4}(?:[ \t]*,[ \t]*\d{4}){2,}(?![\d/])")
_FOUR_DIGITS_RE = re.compile(r"\d{4}")
# The residual scan is deliberately NOT the three patterns re-typed: it finds
# the serial as a standalone number token and reads its neighbourhood.
_ID_BEFORE_RE = re.compile(r"(?i)(auth|cases|case)(?:[ \t]|[/\\\-‐-―]){0,3}$")
_ID_AFTER_RE = re.compile(r"[ \t]*[/\\\-‐-―,][ \t]*\d")


def case_serials(*names):
    """The serial field of each canonical case number -- what identifies a case.

    L2 case numbers are canonical `PREFIX/NNNN/M/YY`; anything else is not a
    case number and contributes no serial.
    """
    out = set()
    for name in names:
        parts = str(name or "").split("/")
        if len(parts) == 4 and parts[1].isdigit():
            out.add(int(parts[1]))
    return out


def redact_case_ids(text, own_serials):
    """Replace this item's own and co-reported case numbers with a neutral token."""
    if not text or not own_serials:
        return text
    own = lambda m: REDACTION_TOKEN if int(m.group(1)) in own_serials else m.group(0)  # noqa: E731
    out = CASE_ID_RE.sub(own, text)
    out = BARE_CASE_ID_RE.sub(own, out)
    return SERIAL_RUN_RE.sub(
        lambda m: _FOUR_DIGITS_RE.sub(
            lambda d: REDACTION_TOKEN if int(d.group(0)) in own_serials else d.group(0),
            m.group(0)),
        out)


def residual_case_ids(text, own_serials):
    """Own-serial occurrences in served text that the redaction did not decide."""
    out = []
    for m in re.finditer(r"(?<!\d)(\d{2,5})(?!\d)", text or ""):
        if int(m.group(1)) not in own_serials:
            continue
        # An ISO date is not a case number, and it wears the same shape:
        # metadata_shown.date_received on AUTH/2007/6/07 is '2007-05-31', whose
        # year is that case's serial and whose tail is 2 digits then 2 digits.
        # DECIDED as a date, on the measurement that a DASH never separates a
        # case number anywhere in the corpus -- 0 occurrences of any dash form
        # over all 18k served strings; every spelling in the table above uses
        # slashes. 3 items (the 3 of AUTH/2007/6/07), found by this guard
        # firing on the first build, which is the guard doing its job.
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                        text[m.start():m.end() + 6] if m.end() + 6 <= len(text) else ""):
            continue
        if _ID_BEFORE_RE.search(text[max(0, m.start() - 12):m.start()]) \
                or _ID_AFTER_RE.match(text[m.end():m.end() + 12]):
            out.append(text[max(0, m.start() - 40):m.end() + 40])
    return out


# A page's outcome BANNER, spliced into the middle of the narrative flow.
#
# Some report panes carry the standing banner line inside the body text rather
# than only above it -- '... during which time Ferring was a corporate patron of
# the club. NO BREACH OF THE CODE Under the agreement, Ferring paid ...'. The
# attest's `no_outcome_banner` check cannot see these because it matches the
# banner headings l1/derive.py RECORDED, and derive only collects headings above
# the first body section: on AUTH-1861-7-06 `banner_headings` is empty, so there
# was nothing to match against.
#
# Case-SENSITIVE and deliberately so: the run has to be the banner's own
# upper-case token run, not the ordinary words. Verified corpus-wide over every
# extract and rendition in the bank before enabling -- it fires on exactly the 7
# items the audit found (AUTH/1861/7/06 x3, AUTH/2248/7/09 x4) and on nothing
# else, so collateral damage is zero. Checked on EVERY task including T3, where
# a spliced banner would leak just as hard.
BANNER_TRIPWIRE = [
    (re.compile(r"\b(?:NO\s+BREACH|BREACH(?:ES)?)\s+OF\s+THE\s+CODE\b"), "spliced outcome banner"),
]


_CODE_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "code"
CODE_CLAUSES = _CODE_DIR / "clauses.jsonl"          # the six interactive editions
PDF_CLAUSES = _CODE_DIR / "pdf_clauses.jsonl"       # 2001-2012 + 3 backfilled clauses
EDITION_ASSIGNMENTS = _CODE_DIR / "edition_assignments.jsonl"

# The section heading is part of its segment's span, so extracts began
# '[COMPLAINT]\nCOMPLAINT The complainant...'. The body is the span minus its
# own heading token. ONE helper, used by render and by the validator's
# rebuild, so the equality check cannot drift.
SECTION_HEADING_PREFIX = {
    "complaint": re.compile(r"^COMPLAINT\b[\s:]*", re.I),
    "response": re.compile(r"^RESPONSE\b[\s:]*", re.I),
    "panel_ruling": re.compile(r"^PANEL RULING\b[\s:]*", re.I),
}


def segment_body(kind, text):
    pat = SECTION_HEADING_PREFIX.get(kind)
    return pat.sub("", text, count=1) if pat else text


CLAUSE_LOOKUP = None  # populated lazily in main() / by the validator
EDITION_LOOKUP = None  # ditto; data/code/edition_assignments.jsonl


def _read_clause_file(path):
    lookup = {}
    if not path.exists():
        return lookup
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            lookup.setdefault((int(row["code_year"]), str(row["clause_number"])), row)
    return lookup


def load_clause_texts():
    """(code_year, clause_number) -> row, from BOTH Code-text layers.

    clauses.jsonl is scraped from the interactive editions the PMCPA publishes
    as HTML (2014, 2015, 2016, 2019, 2021, 2024); pdf_clauses.jsonl is parsed
    from the PDFs of the editions that predate them (2001, 2003, 2006, 2008,
    2011, 2012), plus the three clause pages the PMCPA published with EMPTY
    bodies -- 2014's Clause 26 and 2015's and 2016's Clause 29, all "Compliance
    with Undertakings" (DEFECTS R21). They stay in separate files -- one
    extraction per file, the same rule that keeps constructed labels out of
    bench/items.jsonl -- and the union REFUSES on any overlap rather than
    deciding silently which extraction wins. 2001 and 2003 joined the PDF layer
    on 2026-08-09 (delta noted per the docs rule: this paragraph used to say
    2003 was in neither layer and its 174 items kept clause_text null). The
    PMCPA publishes neither edition; both come from the Wayback Machine's
    captures of abpi.org.uk, pinned in data/code/manifest.jsonl like the rest.

    Ownership is per (YEAR, CLAUSE), not per year (2026-08-09; was per
    year). The guard's job is that two extractions must never disagree about
    the same text, and (year, clause) enforces exactly that; the year-level
    rule also refused pairs where there was no disagreement at all, which is
    what blocked the Clause 29 backfill -- 2015 and 2016 live in clauses.jsonl
    and neither carries a Clause 29 to disagree with. Every row carries its own
    `source_pdf`/`extractor` provenance, so which file a value lives in is not
    the only way to tell where it came from.

    Empty dict when neither file is on disk -- items then keep the 'rely on
    your knowledge' fallback and the no_clause_text tag."""
    html_rows = _read_clause_file(CODE_CLAUSES)
    pdf_rows = _read_clause_file(PDF_CLAUSES)
    clash = sorted(set(html_rows) & set(pdf_rows))
    if clash:
        raise SystemExit(
            "REFUSING: %s and %s both carry %d (year, clause) pair(s) -- %s. "
            "One of the two extractions would silently win. Decide which layer "
            "owns each pair and rebuild."
            % (CODE_CLAUSES.name, PDF_CLAUSES.name, len(clash),
               ", ".join("%d/%s" % k for k in clash[:8])))
    lookup = dict(html_rows)
    lookup.update(pdf_rows)
    return lookup


def load_edition_assignments():
    """case_number -> row, from data/code/edition_assignments.jsonl.

    Which printed 2012 edition governed a case, resolved per case with receipts
    (DEFECTS R22). A PROMPT-RENDERING choice, not a canonical-value repair:
    nothing in data/l2 depends on it and no label moves. The governing edition
    is arguably a case-level fact and this file may later migrate into L2
    beside `code_year`; it lives in data/code for now because only the
    Code-text layer consumes it."""
    rows = {}
    if not EDITION_ASSIGNMENTS.exists():
        return rows
    with EDITION_ASSIGNMENTS.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                rows[row["case_number"]] = row
    return rows


def edition_text_for(lookup, assignments, case_number, code_year, clause):
    """The clause text for a reference the year's editions do not agree on.

    scrape/parse_code_pdfs.py parks each edition's own rendering under
    `by_edition` rather than picking one, because nothing in the Code-text
    layer knows which edition governed a case. This picks the one that case's
    assignment names, and returns None -- so the item keeps its explicit
    not-available line and the no_clause_text tag -- whenever it cannot:

      * the case has no assignment, or its status is undecidable /
        contradictory / pre_dates_editions;
      * the assignment withholds this clause for this case (AUTH/3115/11/18's
        outcome list mixes 2012 and 2016 clause NUMBERING, so the 2012 text
        under 22.1 and 23.1 is about something else -- R22(c));
      * the assigned edition's rendering is marked `attachment_suspect`, i.e.
        it draws supplementary information from a page where the PDF parser
        demonstrably cut a block mid-sentence. Serving the Code's words under
        the wrong clause is worse than serving none.
    """
    try:
        year = int(code_year)
    except (TypeError, ValueError):
        return None
    parent = str(clause).split(".")[0]
    row = lookup.get((year, str(clause))) or lookup.get((year, parent))
    entries = ((row or {}).get("by_edition") or {}).get(str(clause))
    if not entries:
        return None
    assigned = assignments.get(case_number)
    if assigned is None or assigned.get("status") != "assigned":
        return None
    if str(clause) in (assigned.get("withheld_clauses") or {}):
        return None
    want = assigned.get("edition")
    entry = next((e for e in entries if e.get("edition") == want), None)
    if entry is None and want == "second_2012_addendum":
        # "the Second 2012 Edition (amended) Code" is the second edition AS
        # amended; the addendum replaces Clause 16 and nothing else.
        entry = next((e for e in entries if e.get("edition") == "second_2012"), None)
    if entry is None or entry.get("attachment_suspect"):
        return None
    shim = {"clause_title": entry.get("clause_title"),
            "subclauses": [entry["subclause"]] if "subclause" in entry else [],
            "text": entry.get("text"),
            "general_supplementary": None}
    return clause_text_for({(year, parent): shim}, year, clause)


# Wave C. There used to be a silent `out[:6000] + " [truncated]"` at both
# returns below (and at the two mirrors in scrape/parse_code_pdfs.py and
# verify/pdf_clause_texts.py) -- a cap that cut the Code's own words mid-word
# on 643 of 10,195 items and told nobody: no exclusion row, no tag, no build
# line. That is the "no silent caps or guesses" rule broken at the one place
# the prompt quotes the regulation the model is asked to apply
# ('...providing or arranging treatment or c [truncated]', AUTH/1962/2/07
# Clause 19.1).
#
# The cap is REMOVED rather than made loud, because the measurement says there
# is nothing to bound. Every one of the 38 (clause, code_year) renderings it
# was cutting is a legitimate whole clause: 6,605-10,742 characters, the long
# tail being the clauses whose supplementary information is itself a small
# code (18.1 Certification, 19.1 Meetings and Hospitality, 26.2 Compliance
# with Undertakings, 22.x Relations with the Public). Over ALL 2,150
# renderable (year, clause) slots in both Code-text layers the median is 667
# characters, the 95th percentile 4,747 and the maximum 12,501 (Clause 1 of
# the 2021 edition, an undotted whole-clause reference) -- 61 slots exceed
# 6,000 and 2 exceed 12,000. A bound at 6,000 was not protecting against a
# runaway; it was truncating the corpus's longest real clauses.
#
# What replaces it is a REFUSAL, not a cap. A rendering longer than
# CLAUSE_TEXT_REFUSE_CHARS is a symptom of the parser having stapled the wrong
# blocks together, not of a long clause, so the build stops and names the slot
# instead of quietly shipping the first N characters. The threshold sits above
# the measured maximum with room for the Code to grow -- 20,000 is 1.6x the
# largest real rendering -- and the guard was proven to FIRE by narrowing it to
# 12,000 (refuses 2021/1 and 2024/1 by name) before being set back.
CLAUSE_TEXT_REFUSE_CHARS = 20000


def clause_text_refuse(out, code_year, clause):
    """The one place the length bound is applied, so it cannot drift."""
    if len(out) > CLAUSE_TEXT_REFUSE_CHARS:
        raise SystemExit(
            "REFUSING: the rendered text for Clause %s of the %s Code is %d "
            "characters, over the %d-character ceiling. Clause renderings in "
            "this corpus run to 12,501 characters at the most, so a rendering "
            "this long means the Code-text layer has attached the wrong blocks "
            "to this clause. Fix the extraction or raise the ceiling with a "
            "measurement; the text is NOT truncated."
            % (clause, code_year, len(out), CLAUSE_TEXT_REFUSE_CHARS))
    return out


def clause_text_for(lookup, code_year, clause):
    """The regulation text an item shows: the subclause's own text (plus its
    supplementary information -- load-bearing: Clause 2's 'particular censure'
    lives there), or the whole clause for undotted references. None when the
    Code year is not fetched (pre-2014) or the number cannot be found; the
    prompt then keeps its explicit not-available line.

    Never truncated -- see CLAUSE_TEXT_REFUSE_CHARS above."""
    if code_year is None or clause is None:
        return None
    try:
        year = int(code_year)
    except (TypeError, ValueError):
        return None
    parent = str(clause).split(".")[0]
    row = lookup.get((year, str(clause))) or lookup.get((year, parent))
    if row is None:
        return None
    title = row.get("clause_title") or ""

    def flat(v):
        # the extractor stores text as a string OR a list of paragraphs
        if isinstance(v, list):
            return " ".join(flat(x) for x in v if x).strip()
        return (v or "").strip() if isinstance(v, str) else ""

    def supp_text(entries):
        parts = []
        for e in entries or []:
            h = flat(e.get("heading"))
            t = flat(e.get("text"))
            if t:
                parts.append(f"{h}: {t}" if h else t)
        return "\n".join(parts)

    if "." in str(clause):
        for sc in row.get("subclauses") or []:
            if str(sc.get("number")) == str(clause) and flat(sc.get("text")):
                out = f"Clause {clause} ({title}):\n{flat(sc['text'])}"
                supp = supp_text(sc.get("supplementary_information"))
                if supp:
                    out += f"\n\nSupplementary information:\n{supp}"
                return clause_text_refuse(out, year, clause)
        return None
    body = flat(row.get("text"))
    if not body:
        return None
    out = f"Clause {clause} ({title}):\n{body}"
    # PDF-derived and interactive Code sources both sometimes represent an
    # undotted clause as a self-mirroring subclause (number == the parent
    # clause).  Its body is already reflected by row["text"], but its official
    # supplementary information is not: reading only general_supplementary
    # silently dropped Clause 2's "particular censure" guidance (and the same
    # source shape for Clauses 20/21 in some editions).  Keep the two official
    # supplementary slots distinct and include both when present.
    supp_entries = []
    if row.get("general_supplementary"):
        supp_entries.append({"heading": None, "text": row["general_supplementary"]})
    for sc in row.get("subclauses") or []:
        if str(sc.get("number")) == str(clause):
            supp_entries.extend(sc.get("supplementary_information") or [])
    supp = supp_text(supp_entries)
    if supp:
        out += f"\n\nSupplementary information:\n{supp}"
    return clause_text_refuse(out, year, clause)


def quotable(case, problems, resolver):
    """Quotable segments, sorted for determinism.

    complaint/response require a clean leakage attest. panel_ruling (T3's
    sanctioned input) instead requires that the span not reveal the appeal
    outcome -- APPEAL_OUTCOME_RE, checked on the sliced text."""
    out = []
    for seg in case.get("segments", []):
        kind = seg.get("kind")
        if kind in QUOTABLE_KINDS:
            if not attest_ok(seg, value_of(case["case_number"]), problems):
                continue
            out.append(seg)
        elif kind == "panel_ruling":
            try:
                text = slice_ref(resolver, seg["ref"])
            except (KeyError, ValueError):
                continue
            if APPEAL_OUTCOME_RE.search(text):
                continue
            out.append(seg)
    out.sort(key=doc_order)
    return out


SEGMENT_HEADS = {"complaint": "[COMPLAINT]",
                 "response": "[RESPONSE FROM THE RESPONDENT COMPANY]"}


def panel_ruling_heads(case, picked, resolver, unregistered):
    """{ref_key: header} for the panel_ruling spans of one extract (R32(i)).

    The witness ladder, refusing rather than asserting at every rung:

      1. the block's own appeal-status sentences. Positive and no negative ->
         UNDER APPEAL; anything else the sentences say -> neutral.
      2. no status sentence at all, and this extract has ONE ruling block: the
         case was appealed and the Board ruled on this item's clause (T3 mints
         no item otherwise), so the appeal attaches to the only ruling section
         there is. This is the 244-of-316 majority and it keeps their prompts
         byte-identical.
      3. an APPEAL BY/FROM scope sentence names a clause this block rules on.
      4. otherwise neutral. `[PANEL RULING]` claims nothing that is not shown.
    """
    blocks = [s for s in picked if s["kind"] == "panel_ruling"]
    scopes = None
    heads = {}
    for seg in blocks:
        status = appeal_status(slice_ref(resolver, seg["ref"]), unregistered)
        if status == "appealed":
            heads[ref_key(seg["ref"])] = PANEL_RULING_HEAD_UNDER_APPEAL
            continue
        if status is not None:                      # not_appealed / mixed
            heads[ref_key(seg["ref"])] = PANEL_RULING_HEAD_NEUTRAL
            continue
        if len(blocks) == 1:
            heads[ref_key(seg["ref"])] = PANEL_RULING_HEAD_UNDER_APPEAL
            continue
        if scopes is None:
            scopes = appeal_scopes(case, resolver)
        # `rulings[]` carries no clause of its own -- the clause is the row it
        # hangs from -- so the row supplies it.
        here = {v["clause"] for v in case.get("verdicts") or []
                for r in (v.get("rulings") or [])
                if r.get("body") == "panel" and r.get("file") == seg["ref"]["file"]
                and r.get("pane") == seg["ref"]["pane"]
                and seg["ref"]["char_start"] <= r["char_start"] < seg["ref"]["char_end"]}
        covered = any(clauses & here for _side, clauses, _q in scopes)
        heads[ref_key(seg["ref"])] = (PANEL_RULING_HEAD_UNDER_APPEAL if covered
                                      else PANEL_RULING_HEAD_NEUTRAL)
    return heads


def render_extract(resolver, segments, panel_heads=None):
    """Concatenate quoted spans with a structural label per span.

    `panel_heads` carries the per-block R32(i) decision; a caller that passes
    none is quoting no ruling (T1/T2) and the lookup never fires.
    """
    chunks, prov = [], []
    for seg in segments:
        text = segment_body(seg["kind"], slice_ref(resolver, seg["ref"]).strip())
        head = (SEGMENT_HEADS.get(seg["kind"])
                or (panel_heads or {})[ref_key(seg["ref"])])
        chunks.append(f"{head}\n{text}")
        prov.append({"kind": seg["kind"], **seg["ref"]})
    return "\n\n".join(chunks), prov


def pick_segments(segments, kinds, primary_source):
    """Segments for the base extract: one source, all requested kinds present.

    Returned in DOCUMENT order, not kind order (see `doc_order`): the kinds are
    a requirement about what must be PRESENT, never an instruction about how to
    arrange what was found.
    """
    chosen = []
    for kind in kinds:
        same = [s for s in segments if s["kind"] == kind and s.get("source") == primary_source]
        if not same:
            same = [s for s in segments if s["kind"] == kind]
        if not same:
            return None
        chosen.extend(same)
    chosen.sort(key=doc_order)
    return chosen


def rendition_variants(case, kinds, resolver):
    """Alternate tellings usable under the leakage rule.

    l2.1: `renditions.*` are indices into segments[] (no longer bare refs) --
    each points at an ATTESTED paraphrase of the allegation portion
    (summary_rendition / abstract_rendition / the PDF abstract), cut before
    the outcome-stating tail. A rendition retells the ALLEGATION, not the
    defence, so variants attach only to allegation-only extracts
    (T2); swapping one into a complaint+response item would
    silently change the information set."""
    if tuple(kinds) != ("complaint",):
        return []
    out = []
    segs = case.get("segments") or []
    for name in ("summary", "report_abstract", "pdf_flow"):
        idx = (case.get("renditions") or {}).get(name)
        if idx is None or not (0 <= idx < len(segs)):
            continue
        seg = segs[idx]
        if not seg.get("leakage_attest", {}).get("clean"):
            continue
        try:
            text = slice_ref(resolver, seg["ref"]).strip()
        except (KeyError, ValueError):
            continue
        out.append({"name": name, "extract_text": text,
                    "extract_provenance": [{"kind": seg["kind"], **seg["ref"]}]})
    return out


MATTER_ENUMERATOR_RE = re.compile(r"^\s*\d{1,2}\s*[.):]?\s+")
CURLY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def matter_witnessed(text, heading):
    """Does this retelling carry the report's own name for that matter?

    The heading is the report's headline for the matter and quotes the claim at
    issue -- "3 'Given a choice of PDE5 inhibitors, Levitra is the one many men
    prefer'" -- and a rendition that retells the matter repeats the claim. The
    enumerator is stripped (it numbers the matter, it is not part of its name)
    and the curly quotes the panes carry are folded to straight, because the
    summary pane and the report pane spell them differently on the same case.
    """
    name = MATTER_ENUMERATOR_RE.sub("", " ".join((heading or "").split()))
    if not name:
        return False
    return name.translate(CURLY).casefold() in " ".join((text or "").split()).translate(CURLY).casefold()


def rendition_covers(case, verdict, clause, rendition_text, kinds):
    """(bool, reason) -- may this retelling stand in for the item's extract?

    DEFECTS R32(ii). Renditions are CASE-level and items are clause-level, and
    the historical runner replaced the extract with a rendition under legacy
    P1/P3. On
    AUTH/2015/7/07 both renditions retell only matter 1 (the SEP2/SEP3 claim)
    while the complaint-only item for Clause 7.3 lives in
    matter 3 -- so two legacy-P1 variants showed material that alleges
    7.2 and 7.4 and never touches the preference claim. That is not a paraphrase
    perturbation; it is a different information set, which
    `rendition_variants`' own docstring says must not happen.

    Two positive witnesses, conservative by construction -- an undecided
    rendition is dropped, not kept:
      * the case has one segment of the quoted kind, so there is one matter and
        the retelling is of it (4,600-odd of the 5,200 rendition-carrying items);
      * the retelling names the clause, or carries the report's own heading for
        every matter this clause was ruled in.
    A dropped rendition costs perturbation levels, exactly as the 4,831
    zero-rendition items already do; run.py takes `1 + len(renditions)` and
    needs no change.
    """
    if sum(1 for s in case.get("segments") or [] if s["kind"] in kinds) <= 1:
        return True, "single_matter"
    if names_clause(rendition_text, clause):
        return True, "rendition_names_the_clause"
    regards = {r["regard"] for r in (verdict.get("rulings") or [])
               if r.get("body") == "panel" and r.get("regard")}
    if regards and all(matter_witnessed(rendition_text, g) for g in regards):
        return True, "rendition_carries_every_matter_heading"
    if not regards:
        return False, ("the case runs more than one matter and L2 attributes no matter heading "
                       "to this clause's Panel ruling, so nothing decides what the retelling covers")
    missing = sorted(g for g in regards if not matter_witnessed(rendition_text, g))
    return False, ("the case runs more than one matter; the retelling names neither the clause "
                   f"nor the report's heading for {len(missing)} of the {len(regards)} matter(s) "
                   f"this clause was ruled in (first missing: {missing[0][:70]!r})")


def dedupe_siblings(items, groups, skips):
    """Collapse byte-identical sibling items into one (DEFECTS D4).

    Co-reported cases SHARE one report document, so a clause ruled in both of
    them produces two items with the same task, the same quoted text, the same
    clause and the same label, differing only in the case number they are
    booked against -- 1,164 items, 9.7% of the bank at the audit. Scored as
    written they double-count one piece of evidence, and where a joint ruling
    actually bound one respondent only (the Roche-only ruling in
    AUTH/2160+2161) the second copy is simply wrong.

    One copy survives, booked against the first case number in canonical order,
    and it carries `sibling_case_numbers` so nothing about the collapse is
    hidden. Cases in DIFFERENT sibling groups are never merged even if their
    text somehow matched: identical text across unrelated cases would be a fact
    about the corpus worth keeping, not a duplicate.
    """
    by_key = {}
    for item in items:
        key = (groups.get(item["case_number"], item["case_number"]), item["task"],
               item["inputs"]["clause_ref"]["clause"],
               str(item["inputs"]["clause_ref"]["code_year"]),
               item["label"], item["inputs"]["extract_text"])
        by_key.setdefault(key, []).append(item)
    kept = []
    for key, group in by_key.items():
        group.sort(key=lambda it: it["case_number"])
        winner, rest = group[0], group[1:]
        if rest:
            winner["sibling_case_numbers"] = sorted(it["case_number"] for it in rest)
            for it in rest:
                exclude(skips, it["case_number"], it["task"],
                        it["inputs"]["clause_ref"]["clause"], "sibling_duplicate",
                        f"folded into {winner['case_number']} "
                        f"(identical extract, clause and label)")
        kept.append(winner)
    return kept


def item_id(task, case_number, clause_key, prov):
    payload = "\n".join(
        [task, case_number, clause_key]
        + [f"{p['file']}:{p['pane']}:{p['char_start']}:{p['char_end']}" for p in prov]
    )
    return f"{task}-{hashlib.blake2b(payload.encode('utf-8'), digest_size=8).hexdigest()}"


# --- sibling grouping and splits -------------------------------------------

def sibling_groups(cases):
    """Union-find over shared source files and declared siblings (DESIGN §6).

    SPEC §2 does not promise `sibling_cases` is symmetric or transitively
    complete, so shared `source_files` is unioned as well -- a report shared by
    two cases is shared narrative text whatever the sibling list says.
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_file = {}
    for case in cases:
        num = value_of(case["case_number"])
        find(num)
        for f in case.get("source_files") or []:
            by_file.setdefault(f, []).append(num)
        for sib in case.get("sibling_cases") or []:
            if any(value_of(c["case_number"]) == sib for c in cases):
                union(num, sib)
    for members in by_file.values():
        for other in members[1:]:
            union(members[0], other)

    return {num: find(num) for num in parent}


def split_for(group_key, seed):
    digest = hashlib.blake2b(f"{seed}:{group_key}".encode("utf-8"), digest_size=8).hexdigest()
    frac = int(digest[:8], 16) / float(1 << 32)
    for name, upper in SPLITS:
        if frac < upper:
            return name
    return SPLITS[-1][0]


# --- item construction -----------------------------------------------------

def base_metadata(case):
    complainant = (case.get("parties") or {}).get("complainant") or {}
    return {
        "respondent": value_of((case.get("parties") or {}).get("respondent")),
        "complainant_category": complainant.get("category"),
        "complainant_anonymous": complainant.get("anonymous"),
        "complainant_contactable": complainant.get("contactable"),
        "code_year": value_of(case.get("code_year")),
        "date_received": value_of((case.get("dates") or {}).get("received")),
    }


def case_tags(case, group_key):
    tags = [f"sibling_group:{group_key}"]
    proc = case.get("procedure") or {}
    for flag in ("voluntary_admission", "abridged", "paragraph_17", "outwith_scope", "inter_company", "no_report"):
        if proc.get(flag):
            tags.append(flag)
    complainant = (case.get("parties") or {}).get("complainant") or {}
    if complainant.get("anonymous"):
        tags.append("anonymous_complainant")
    if complainant.get("anonymous") and complainant.get("contactable") is False:
        tags.append("burden_of_proof_candidate")
    if (case.get("appeal") or {}).get("appealed"):
        tags.append("appealed")
    year = value_of(case.get("code_year"))
    if year is not None:
        tags.append(f"code_year:{year}")
    if (case.get("quality") or {}).get("pdf_substituted"):
        tags.append("pdf_substituted")
    return tags


# A span `pick_segments` must not serve for one named clause of one named case,
# because the report's own matter heading puts it in a matter that clause was
# never ruled in. Quote-pinned: if the text at the span moves, the build stops.
#
# AUTH/1851/6/06. The report runs two matters. Its own heading '2 Journal
# advertisement' opens the second at report 8076-8099, and the complaint that
# follows (8100-9926) alleges Clauses 7, 7.2 and 7.3 and never mentions 3.2 --
# it also carries a spliced running header, '71 Code of Practice Review November
# 2006'. It was served anyway because bench reconstructs the matter partition
# from ruling receipts (`matter_starts` reads verdicts[].rulings[].regard_ref),
# and matter 2 has NO verdict row at all: the Panel ruled the Transplant
# International advertisement outside the UK Code's scope, so it never entered
# an outcome list (R27's shape). With one start, matter 1's span is unbounded,
# `own_matter_refusal` cannot fire, and `pick_segments` takes every segment of
# the requested kind.
#
# This is a REGISTRY and not a rule because the general form was measured and is
# a wave, not a fix: partitioning from L2's full computed heading list and
# scoping `pick_segments` to the clause's own matter takes a served segment away
# from 1,120 items across 156 cases (11% of the bank), and the heading list is
# incomplete in both directions -- it misses letter-enumerated matters ('B
# Letter to a hospital consultant', AUTH/2162/8/08) and headings split across
# two L1 sections, and it carries 3 running-header false positives out of 642.
# That wave needs its own round, a re-split and a board re-derivation. Until it
# runs, a case whose defect has actually been read is repaired by receipt.
#
# Cost of this row: matter 1's complaint (4194-5205) and response (5205-6686)
# each name only Clause 3.2 and stand alone at 1,011 and 1,481 characters, so
# the two items keep a complete allegation and a complete response. Both are
# dev-split and neither is on a published board; both RENAME, because the
# provenance spans are hashed into item_id.
MATTER_SCOPE_REFUSALS = {
    ("AUTH/1851/6/06", "3.2"): [
        {"file": "AUTH-1851-6-06.html", "pane": "report",
         "char_start": 8100, "char_end": 9926,
         "opens": "[COMPLAINT]",
         "why": "matter 2 ('2 Journal advertisement', report heading 8076-8099), which "
                "alleges Clauses 7, 7.2 and 7.3 and never names 3.2"},
    ],
}


def scope_out_of_matter(case_number, clause, picked, resolver, refused):
    """Drop the spans MATTER_SCOPE_REFUSALS names for this (case, clause).

    Refuses the build if a named span is not where the registry says it is: the
    row was written against one reading of one report and must not survive the
    text moving under it (the AUTH/1941 registry's discipline).
    """
    rows = MATTER_SCOPE_REFUSALS.get((case_number, str(clause)))
    if not rows:
        return picked
    keep = []
    for seg in picked:
        row = next((r for r in rows
                    if all(seg["ref"].get(k) == r[k]
                           for k in ("file", "pane", "char_start", "char_end"))), None)
        if row is None:
            keep.append(seg)
            continue
        text = slice_ref(resolver, seg["ref"]).strip()
        if not text.startswith(row["opens"].strip("[]")) and row["opens"] not in text[:40]:
            raise SystemExit(
                f"REFUSING: MATTER_SCOPE_REFUSALS names {case_number} clause {clause} span "
                f"{row['file']}:{row['pane']}:{row['char_start']}-{row['char_end']}, which was "
                f"read as {row['why']}; the text there now opens {text[:60]!r}. Re-read the "
                f"case before trusting the row.")
        refused.append((case_number, str(clause), row["why"]))
    return keep


def own_matter_refusal(case, verdict, kinds, picked, dropped, resolver):
    """Why this item may not be built from the segments that survived, or None.

    DEFECTS R31, the outcome half. `quotable()` drops a segment whose attest is
    dirty and `pick_segments` then takes whatever is left of that kind -- which,
    on a multi-matter report, is ANOTHER MATTER'S complaint. AUTH/2008/6/07 is
    the proof: matter 2's segments failed `no_sanctions_text` on the chip needle
    'Advertisement' (the needle floor above), so all four of its items quoted
    matter 1 (char 11633-15582) and asked about Clause 3.2, which matter 1 never
    mentions. Falling back silently is the defect; the fix is to refuse.

    Two witnesses, either sufficient, both comparing what was SERVED against
    what was DROPPED -- a served matter is never a defect however dirty its
    siblings are:
      (a) L2's matter partition: no served segment of a requested kind opens
          inside a matter this clause was ruled in, and a dropped one does;
      (b) the clause name: no served segment of a requested kind names the
          clause, and a dropped one does. This is the audit's own probe, and a
          lower bound -- it needs the number printed in the complaint.
    """
    for kind in kinds:
        served = [s for s in picked if s["kind"] == kind]
        gone = [s for s in dropped if s["kind"] == kind]
        if not gone or not served:
            continue
        spans = item_matter_spans(case, verdict)
        if spans and not any(in_any_span(s, spans) for s in served) \
                and any(in_any_span(s, spans) for s in gone):
            return ("matter", f"every {kind} segment of this clause's own matter is attest-dirty; "
                              f"the {len(served)} served {kind} segment(s) belong to other matters")
        clause = verdict.get("clause")
        if not any(names_clause(slice_ref(resolver, s["ref"]), clause) for s in served) \
                and any(names_clause(slice_ref(resolver, s["ref"]), clause) for s in gone):
            return ("clause_name", f"no served {kind} segment names Clause {clause}; a dropped "
                                   f"{kind} segment does")
    return None


def build_case_items(case, group_key, split, resolver, tasks, problems, skips, unregistered,
                     co_reported=(), scoped=None):
    num = value_of(case["case_number"])
    # DEFECTS D4. The page reports a case it never declares as a sibling, so its
    # segments mix parties and its outcome lists cover more than this case.
    # Every item from it is excluded -- separating the rulings inside a shared
    # pane is explicitly out of scope, so there is nothing to salvage here.
    if (case.get("quality") or {}).get("multi_case_undeclared"):
        for task in tasks:
            exclude(skips, num, task, None, "multi_case_undeclared",
                    "report pane carries undeclared case banner(s) "
                    f"{', '.join((case['quality'].get('multi_case_banners') or []) or ['?'])}")
        return []
    segments = quotable(case, problems, resolver)
    # The redaction covers this case and every case CO-REPORTED with it: the
    # sibling group, which unions declared siblings with cases sharing a source
    # file (`sibling_groups`), is exactly the set whose numbers can appear in
    # this page's own narrative as its own identity rather than as a citation.
    own_serials = case_serials(num, *co_reported)
    primary = "pdf" if (case.get("quality") or {}).get("pdf_substituted") else "html"
    tags_base = case_tags(case, group_key)
    meta_base = base_metadata(case)
    items = []

    def make(task, kinds, clause_ref, label, verdict, extra_meta=None, extra_tags=()):
        picked = pick_segments(segments, kinds, primary)
        if picked is not None:
            # Before anything judges the served set: drop the spans a read
            # registry says belong to another matter (MATTER_SCOPE_REFUSALS).
            picked = scope_out_of_matter(num, clause_ref["clause"], picked, resolver,
                                         scoped if scoped is not None else [])
        if picked is None:
            # Wave C. This line used to read "clean segments available: ...",
            # which was false for one of the two kinds it lists: `quotable`
            # admits complaint/response on a CLEAN LEAKAGE ATTEST and
            # panel_ruling on a different test entirely (APPEAL_OUTCOME_RE --
            # the span must not reveal the appeal outcome), so a case whose
            # panel_ruling attest says `clean: false` still had its
            # panel_ruling listed as clean (AUTH/2443/10/11 x4 rows,
            # AUTH/2169/9/08 x1). The exclusion outcome was right and its
            # stated reason was not; an exclusion row is a description of the
            # report and has to be true.
            have = sorted({s["kind"] for s in segments})
            shown = ", ".join(
                f"{k} ({'no appeal-outcome text' if k == 'panel_ruling' else 'attest-clean'})"
                for k in have) or "none"
            exclude(skips, num, task, clause_ref["clause"], "no_usable_segments",
                    f"needs {'+'.join(kinds)}, quotable segments available: {shown}")
            return None
        kept = {ref_key(s["ref"]) for s in segments}
        dropped = [s for s in case.get("segments") or []
                   if s["kind"] in kinds and ref_key(s["ref"]) not in kept]
        refusal = own_matter_refusal(case, verdict, kinds, picked, dropped, resolver)
        if refusal is not None:
            witness, detail = refusal
            exclude(skips, num, task, clause_ref["clause"], "own_matter_unquotable",
                    f"{detail} ({witness} witness); {len(dropped)} segment(s) of the requested "
                    f"kind(s) were refused by `quotable` (a dirty leakage attest, or -- on a "
                    f"panel_ruling -- a span that reveals the appeal outcome)")
            return None
        text, prov = render_extract(
            resolver, picked, panel_ruling_heads(case, picked, resolver, unregistered))
        # DEFECTS R25. A rendition shorter than MIN_RENDITION_CHARS has been
        # refused since l2.1 ("not worth a benchmark item"); the PRIMARY
        # extract -- the thing the model is actually asked to rule on -- had no
        # floor at all, and AUTH/1798/2/06 shipped two complaint-only items whose
        # entire complaint was 194 characters, truncated mid-sentence and
        # spliced with a clause traceable to AUTH/1797/2/06's response (the
        # neighbouring column of the same two-column Review page).
        #
        # The floor is on the ASSEMBLED extract, not on each segment, and the
        # difference is the whole measurement: 160 items in the pre-fix bank
        # sit on a segment under 200 chars, but only 2 have a total under 200.
        # A 63-char response inside a 5,610-char exchange ("Novartis denied
        # this.") is a short answer, not a truncation -- flooring per segment
        # would have excluded 160 items across 26 cases to catch 2. That
        # per-segment count is reported rather than acted on.
        if len(text) < MIN_EXTRACT_CHARS:
            exclude(skips, num, task, clause_ref["clause"], "extract_below_floor",
                    f"assembled extract is {len(text)} chars over {len(picked)} segment(s), "
                    f"under the {MIN_EXTRACT_CHARS}-char floor (l2 MIN_RENDITION_CHARS parity)")
            return None
        meta = dict(meta_base)
        if extra_meta:
            meta.update(extra_meta)
        tags = sorted(set(tags_base) | set(extra_tags) | ({"no_clause_text"} if clause_ref["clause_text"] is None else set()))
        # DEFECTS R32(ii). Coverage is decided PER ITEM, because a rendition is
        # a retelling of the case and the item is about one clause of it. A
        # dropped rendition gets its own exclusion row -- it is not an
        # item-candidate, but the durable-exclusions rule is about invisible
        # skips, and verify/candidate_accounting.py already names this shape
        # ("one item and a separate row records a variant that was dropped").
        rends = []
        for rend in rendition_variants(case, kinds, resolver):
            ok, why = rendition_covers(case, verdict, clause_ref["clause"],
                                       rend["extract_text"], kinds)
            if ok:
                rends.append(rend)
            else:
                exclude(skips, num, task, clause_ref["clause"], "rendition_not_covering",
                        f"the {rend['name']} rendition does not cover this item: {why}")
        # The redaction runs LAST, after `quotable`, the extract floor,
        # `rendition_covers` and `own_matter_refusal` have all read the source
        # span: those guards decide whether the span may be quoted at all, and
        # they must judge the report, not our edit of it (a matter heading that
        # carries a case number would stop matching its own retelling). The
        # tripwire below is the one guard that runs AFTER, deliberately -- it is
        # the last thing to see the text the model sees, and bench/validate.py
        # re-runs it on the shipped string, so generator and checker cannot
        # disagree about which bytes were screened. Redaction cannot change what
        # it finds: the precedent exemption fires on `cited - own`, and taking
        # own numbers out of the window cannot empty that difference (it also
        # only ever SHORTENS the text, so a citation inside the +/-120 window
        # stays inside it).
        text = redact_case_ids(text, own_serials)
        for rend in rends:
            rend["extract_text"] = redact_case_ids(rend["extract_text"], own_serials)
        clause_key = f"{clause_ref['code_year']}/{clause_ref['clause']}"
        item = {
            # DEFECTS: the id hashes task + case number + clause key + the
            # provenance SPANS, never the quoted characters, so redacting the
            # served text renames nothing (verified: 0 renames over the whole
            # bank).
            "item_id": item_id(task, num, clause_key, prov),
            "task": task,
            "inputs": {
                "extract_text": text,
                "extract_provenance": prov,
                "clause_ref": clause_ref,
                "metadata_shown": meta,
            },
            "label": label,
            "case_number": num,
            "split": split,
            "tags": tags,
            "contamination": {"probe_status": "untested"},
        }
        if rends:
            item["inputs"]["renditions"] = rends
        # Final tripwire, same patterns the validator re-checks: an item whose
        # quoted text trips (a complaint citing another case's ruling or
        # sanction against the same company) is DROPPED, not argued about --
        # 23 of 11,550 at introduction (2026-08-02), all prior-case citations.
        quoted = [item["inputs"]["extract_text"]] + [
            r["extract_text"] for r in item["inputs"].get("renditions", [])]
        for text in quoted:
            # The spliced-banner tripwire runs on EVERY task: T3's premise is
            # the Panel's ruling under appeal, never the page's outcome banner.
            hit = next((p.search(text) for p, _ in BANNER_TRIPWIRE if p.search(text)), None)
            reason = "tripwire_outcome_banner"
            if hit is None:
                reason = "tripwire"
                if task == "T3":
                    hit = APPEAL_OUTCOME_RE.search(text)
                else:
                    hit, _what = tripwire_hit(text, own_case_numbers(num, *case.get("sibling_cases") or []))
            if hit:
                exclude(skips, num, task, clause_ref["clause"], reason,
                        f"quoted text contains {hit.group(0)!r}")
                return None
        items.append(item)
        return item

    for verdict in case.get("verdicts") or []:
        clause = verdict.get("clause")
        # DEFECTS D3. A dual row has no single label -- usually because the
        # Panel ruled both ways in different regards, but in one deliberately
        # distinct L2 class because an unappealed case's published outcome
        # lists name the clause under both polarities. Keep those evidence
        # claims distinct in the durable exclusion receipt.
        if verdict.get("dual_ruling"):
            basis = verdict.get("basis")
            if basis == "verdict_unappealed_dual_listed":
                detail = (f"listed both ways in the published outcome lists ({basis}), "
                          f"no single label exists")
            else:
                detail = (f"ruled both ways in this case ({basis}), "
                          f"no single label exists")
            for task in tasks:
                exclude(skips, num, task, clause, "dual_ruling", detail)
            continue
        # DEFECTS R20. L2 now arbitrates the row's Code edition instead of
        # preferring one witness, and it is allowed to REFUSE: a clause the
        # report rules under two editions in one case (AUTH/3722/1/23 rules
        # Clause 9.1 under the 2016 Code for wave 1 and the 2019 Code for waves
        # 2-3) has no single edition to quote.
        year_basis = verdict.get("code_year_basis")
        v_year = verdict.get("code_year", value_of(case.get("code_year")))
        # The refusal test is the NULL, not the basis spelling. L2's rule bases
        # say `year_undecided_*`, but R20's residue round added a second way to
        # refuse -- a reviewed row in l2/adjudications.json, which lands its own
        # adjudication id in `code_year_basis` the way every other adjudicated
        # value lands one in `basis`. Both refusals have the null in common, and
        # `year_uncontested` is the one basis whose null means something else:
        # 57 cases simply never state a Code year, and they keep their items.
        if v_year is None and year_basis != "year_uncontested":
            src = verdict.get("sources") or {}
            years = sorted((src.get("clause_code_year_prose") or {})
                           or (src.get("case_code_year_prose_decisive") or {}))
            for task in tasks:
                exclude(skips, num, task, clause, "code_year_undecided",
                        f"{year_basis}: the report names Code year(s) "
                        f"{years or 'none'} for this ruling and the chip says "
                        f"{src.get('chip_code_year')!r}; no single edition to quote")
            continue
        clause_ref = {
            "code_year": v_year,
            "clause": clause,
            # The reconciled row first -- one text every edition of that year
            # agrees on. Only where they disagree does the case's own assigned
            # edition decide, and only for a case that has one.
            "clause_text": (
                clause_text_for(_ensure_clause_lookup(), v_year, clause)
                or edition_text_for(_ensure_clause_lookup(),
                                    _ensure_edition_lookup(), num, v_year, clause)
                if v_year is not None and clause is not None else None),
        }
        panel = verdict.get("panel")
        # DEFECTS R13. A row L2 could not attribute to the Panel yields no T1/T2
        # label, and until now yielded no exclusion row either: the branch below
        # simply did not fire and the candidate vanished. Seven cases -- every
        # verdict row unattributed -- left neither an item nor a reasoned row,
        # which is exactly the invisible skip the durable-exclusions rule exists
        # to stop. The row is a legitimate non-item; it just has to say so.
        if panel not in ("breach", "no_breach"):
            for task in ("T1", "T2"):
                if task in tasks:
                    exclude(skips, num, task, clause, "no_panel_ruling",
                            f"L2 attributes no Panel ruling to this clause "
                            f"({verdict.get('basis')}); nothing to label")
        if panel in ("breach", "no_breach"):
            if "T1" in tasks:
                make("T1", ("complaint", "response"), clause_ref, panel, verdict)
            if "T2" in tasks:
                make("T2", ("complaint",), clause_ref, panel, verdict)

        # T3 eligibility, tightened per DEFECTS D3: the case was appealed AND
        # both rulings are attributed from the respective body's own prose. L2
        # will not set either field from the outcome lists any more, so "both
        # non-null" now means the report demonstrably states what each body
        # decided about THIS clause -- which is what the task claims to test.
        appeal_board = verdict.get("appeal_board")
        if "T3" in tasks and (case.get("appeal") or {}).get("appealed"):
            if appeal_board not in ("breach", "no_breach"):
                # R13: the `if panel` guard used to make this row vanish when
                # NEITHER side was attributed -- the one case where the least is
                # known was the one case that left no trace. Both states are
                # reported now, distinguished so the register stays diagnostic.
                if verdict.get("dual_ruling_appeal_board"):
                    # DEFECTS R28 stage 1. Distinguished from the row below
                    # because the two states are opposites and the row below
                    # would state the false one: the appeal-side prose here
                    # states TWO rulings on this clause, in different regards,
                    # so there is no single panel->board transition to label.
                    #
                    # Q3: the count can be ZERO, and that is not a
                    # contradiction. On AUTH/1902+1903 cl 18.1 the flag comes
                    # from a REVIEWED attribution, because neither half names
                    # the clause ('The Appeal Board upheld the Panel's ruling
                    # of a breach of the Code'; 'there was no breach of the
                    # Code in relation to arrangements for the TOPCAT
                    # service') and so neither half is attributable by any
                    # reader. The row says which reading, so the exclusion
                    # says so too rather than printing a bare 0.
                    n_board = len([r for r in (verdict.get("rulings") or [])
                                   if r["body"] == "appeal_board"])
                    how = (f"{n_board} attributed ruling(s)" if n_board
                           else f"neither half is attributable by any reader; read by "
                                f"{verdict.get('attribution_basis') or 'a registry row'}")
                    exclude(skips, num, "T3", clause, "dual_ruling_appeal_board",
                            f"the Appeal Board ruled this clause both ways in different regards "
                            f"({how}); no single transition to label")
                elif panel in ("breach", "no_breach"):
                    exclude(skips, num, "T3", clause, "t3_no_appeal_board_ruling",
                            "the appeal-side prose does not state an Appeal Board ruling "
                            "on this clause")
                else:
                    exclude(skips, num, "T3", clause, "t3_neither_ruling_attributed",
                            f"neither the Panel nor the Appeal Board ruling is "
                            f"prose-attributed for this clause ({verdict.get('basis')})")
                continue
            if panel not in ("breach", "no_breach"):
                exclude(skips, num, "T3", clause, "t3_no_panel_ruling",
                        "no prose-attributed Panel ruling to test")
                continue
            by = (case.get("appeal") or {}).get("by")
            if by not in ("respondent", "complainant", "both"):
                exclude(skips, num, "T3", clause, "t3_appellant_unresolved",
                        f"appellant is {by!r}")
                continue
            # DEFECTS R18(b). The premise names the appellant, so it has to be
            # the appellant of THIS ruling. Case-level `both` renders as "Both
            # parties appealed that ruling", which AUTH/1871/7/06 proves false
            # for its two items -- Sanofi appealed 3.2/7.2/7.4, the complainant
            # appealed 2/9.1. Where the report does not say, the item goes:
            # a premise nobody can check is not a cheaper item, it is a wrong one.
            by, appellant_basis, appellant_quote = clause_appellant(case, verdict, resolver)
            if by is None:
                exclude(skips, num, "T3", clause, "t3_appellant_undecided_for_clause",
                        f"{appellant_basis}; neither a 'was appealed by <party>' sentence after "
                        f"the Panel's ruling nor an APPEAL BY/FROM scope sentence names Clause "
                        f"{clause}")
                continue
            flipped = verdict.get("flipped_on_appeal")
            if flipped is None:
                flipped = panel != appeal_board
            label = "overturned" if flipped else "upheld"
            make("T3", ("complaint", "response", "panel_ruling"), clause_ref, label, verdict,
                 extra_meta={"panel_ruling_for_clause": panel, "appellant": by},
                 extra_tags=("appeal_flip",) if flipped else ("appeal_survived",))

    return items


# --- driver ----------------------------------------------------------------

def read_cases(path):
    cases = []
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{lineno}: {exc}")
    return cases


def _ensure_clause_lookup():
    global CLAUSE_LOOKUP
    if CLAUSE_LOOKUP is None:
        CLAUSE_LOOKUP = load_clause_texts()
    return CLAUSE_LOOKUP


def _ensure_edition_lookup():
    global EDITION_LOOKUP
    if EDITION_LOOKUP is None:
        EDITION_LOOKUP = load_edition_assignments()
    return EDITION_LOOKUP


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=str(DEFAULT_CASES), help="L2 cases.jsonl (default: %(default)s)")
    ap.add_argument("--fixture", default=str(FIXTURE_CASES),
                    help="invented cases used only with --use-fixture (default: %(default)s)")
    ap.add_argument("--panes", default=str(FIXTURE_PANES), help="fixture pane text")
    ap.add_argument("--l1", default=str(L1_RECORDS), help="L1 records.jsonl for text resolution")
    ap.add_argument("--pdf-records", default=str(L1_PDF_RECORDS))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--exclusions", default=str(DEFAULT_EXCLUSIONS),
                    help="durable log of every excluded item-candidate (default: %(default)s)")
    ap.add_argument("--slot-corrections", default=str(DEFAULT_SLOT_CORRECTIONS),
                    help="L2's clause-slot corrections, for the deleted rows' exclusion rows")
    ap.add_argument("--tasks", default=",".join(TASKS))
    ap.add_argument("--split-seed", default="abpi-code-bench-v1")
    ap.add_argument("--use-fixture", action="store_true",
                    help="ignore --cases and build from the fixture, even if real L2 exists")
    args = ap.parse_args(argv)

    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    unknown = [t for t in tasks if t not in TASKS]
    if unknown:
        raise SystemExit(f"unknown task(s): {unknown}; known: {list(TASKS)}")

    using_fixture = args.use_fixture
    if using_fixture:
        cases_path = pathlib.Path(args.fixture)
        if not cases_path.exists():
            raise SystemExit(f"--use-fixture requested but fixture cases are absent: {cases_path}")
        print("!" * 72)
        print("!! --use-fixture: ignoring", args.cases)
        print("!! Building from the FIXTURE:", cases_path)
        print("!! Items generated from invented TEST/xxxx cases. Not a benchmark.")
        print("!" * 72)
    else:
        cases_path = pathlib.Path(args.cases)
        if not cases_path.exists():
            raise SystemExit(
                f"real L2 cases not found at {cases_path}; refusing to substitute the invented "
                "fixture. Build/retrieve L2 first, or pass --use-fixture explicitly for a "
                "fixture-only smoke build."
            )

    cases = read_cases(cases_path)
    if not cases:
        raise SystemExit(f"{cases_path} is empty")

    wanted = {
        (s["ref"]["file"], s["ref"]["pane"])
        for case in cases for s in case.get("segments", []) if "ref" in s
    }
    if using_fixture:
        resolver = FixtureResolver(args.panes)
    else:
        if not pathlib.Path(args.l1).exists():
            raise SystemExit(f"L2 cases present but {args.l1} is absent -- segment offsets cannot be resolved")
        resolver = L1Resolver(args.l1, args.pdf_records, wanted)

    groups = sibling_groups(cases)
    problems, skips, items, unregistered = [], [], [], []
    # N2. A verdict row an adjudication DELETED (the outcome slot's clause
    # number is not a Code clause of this case: AUTH/1921/11/06's Paragraph 17
    # of the Constitution, AUTH/2790/8/15's Paragraph 5.2) leaves no candidate
    # for the loop below to skip, so its item-candidates would vanish leaving
    # neither an item nor a reasoned row -- exactly the silent drop the
    # exclusions file exists to prevent (DEFECTS R13). L2 writes the deletions
    # down; this reads them and books one row per task.
    if not using_fixture:
        for row in read_slot_corrections(args.slot_corrections):
            if row["to_clause"] is not None:
                continue
            for task in tasks:
                exclude(skips, row["case_number"], task, row["from_clause"],
                        "clause_not_in_case_text",
                        f"the outcome slot names Clause {row['from_clause']}, which the case's own "
                        f"text never mentions (it names {', '.join(row['clause_names_in_text'])}); "
                        f"the verdict row is deleted by {row['adjudication']} and there is no ruling "
                        f"to label")
    co_reported = {}
    for num, key in groups.items():
        co_reported.setdefault(key, []).append(num)
    scoped = []
    for case in cases:
        num = value_of(case["case_number"])
        group_key = groups.get(num, num)
        split = split_for(group_key, args.split_seed)
        items.extend(build_case_items(case, group_key, split, resolver, tasks,
                                      problems, skips, unregistered,
                                      sorted(co_reported.get(group_key, [num])), scoped))

    # DEFECTS R32(i), the decide-every-value rule applied to the appeal-status
    # screen. Refused BEFORE anything is written: a screened sentence neither
    # the frame nor APPEAL_STATUS_READ decides might be the negative form ("This
    # ruling was not appealed"), and going quiet on it is how a block that says
    # so would still be stamped UNDER APPEAL.
    if unregistered:
        distinct = sorted(set(unregistered))
        raise SystemExit(
            "REFUSING: %d screened sentence(s) (%d distinct) inside quoted panel_ruling blocks "
            "state something about a ruling and an appeal that neither APPEAL_STATUS_FRAME_RE "
            "nor APPEAL_STATUS_READ decides:\n  %s\nRead each one and register it."
            % (len(unregistered), len(distinct),
               "\n  ".join(repr(s[:180]) for s in distinct[:10])))

    before_dedupe = len(items)
    items = dedupe_siblings(items, groups, skips)

    order = {t: i for i, t in enumerate(TASKS)}
    items.sort(key=lambda it: (order[it["task"]], it["item_id"]))

    ids = [it["item_id"] for it in items]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise SystemExit(f"non-unique item_ids (verdict occurrence collision?): {dupes[:5]}")

    # The redaction's own decide-every-value guard, run over the WHOLE bank
    # after the fold (a folded winner inherits its siblings' numbers, so this is
    # the first point at which every served string and its full own-set exist
    # together). It refuses rather than reports: a spelling of the case number
    # nobody has decided is the whole defect, and shipping 3,153 items redacted
    # except for one form would be worse than not redacting at all.
    undecided, redacted_items = [], 0
    for it in items:
        own = case_serials(it["case_number"], *(it.get("sibling_case_numbers") or []),
                           *co_reported.get(groups.get(it["case_number"], it["case_number"]), []))
        served = [it["inputs"]["extract_text"]]
        served += [r["extract_text"] for r in it["inputs"].get("renditions", [])]
        # metadata_shown is an allowlist of case FACTS (respondent, code year,
        # date received) and carries no case number by construction; asserted
        # here rather than assumed, because the assertion is free.
        served += [v for v in it["inputs"]["metadata_shown"].values() if isinstance(v, str)]
        if any(REDACTION_TOKEN in s for s in served):
            redacted_items += 1
        for s in served:
            undecided += [(it["item_id"], c) for c in residual_case_ids(s, own)]
    if undecided:
        raise SystemExit(
            "REFUSING: %d served string(s) still carry the item's own or a co-reported case "
            "number in a spelling `redact_case_ids` does not decide. Read each one and add its "
            "form to the decided table above:\n  %s"
            % (len(undecided), "\n  ".join(f"{i}: {c!r}" for i, c in undecided[:10])))

    out = pathlib.Path(args.out)
    write_jsonl_atomic(out, items)

    per_task = {t: sum(1 for it in items if it["task"] == t) for t in TASKS}
    per_split = {}
    for it in items:
        per_split[it["split"]] = per_split.get(it["split"], 0) + 1
    with_rend = sum(1 for it in items if it["inputs"].get("renditions"))

    print(f"source     : {cases_path}{'  (FIXTURE)' if using_fixture else ''}")
    print(f"text from  : {resolver.origin}")
    print(f"cases      : {len(cases)} in {len(set(groups.values()))} sibling group(s)")
    print(f"items      : {len(items)} -> {out}")
    for t in TASKS:
        if t in tasks:
            print(f"  {t:<10} {per_task[t]}")
    print(f"splits     : " + ", ".join(f"{k}={per_split.get(k, 0)}" for k, _ in SPLITS))
    print(f"renditions : {with_rend} item(s) carry an alternate rendition")
    print(f"redaction  : {redacted_items} item(s) serve {REDACTION_TOKEN} in place of their own "
          f"or a co-reported case number; 0 undecided spellings")
    # A read registry that fires on nothing is dead code claiming to be a
    # repair, so its firing is printed and asserted, never assumed.
    # The registry names real PMCPA cases and cannot fire on the invented
    # fixture. Its load-bearing assertion therefore belongs to real builds;
    # applying it to --use-fixture made the documented offline smoke path
    # refuse after writing its items.
    if not using_fixture and len(MATTER_SCOPE_REFUSALS) and not scoped:
        raise SystemExit("REFUSING: MATTER_SCOPE_REFUSALS has rows and none of them fired -- "
                         "the spans have moved, or the registry is stale.")
    for case_number, clause, why in sorted(set(scoped)):
        print(f"matter     : {case_number} clause {clause} does not serve {why}")
    folded = sum(1 for it in items if it.get("sibling_case_numbers"))
    print(f"dedupe     : {before_dedupe} built -> {len(items)} kept "
          f"({before_dedupe - len(items)} sibling duplicates folded into {folded} item(s))")
    # The durable exclusion log. Written even when empty, so its absence means
    # "the generator did not run", never "nothing was excluded".
    excl = pathlib.Path(args.exclusions)
    skips.sort(key=lambda s: (s["reason"], s["case_number"], s["task"], s["clause"] or ""))
    write_jsonl_atomic(excl, skips)
    print(f"exclusions : {len(skips)} -> {excl}")
    if skips:
        reasons = {}
        for row in skips:
            reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
        for reason, n in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {reason:<28} {n}")
            for row in [s for s in skips if s["reason"] == reason][:2]:
                print(f"      e.g. {row['case_number']} {row['task']} "
                      f"clause {row['clause']}: {row['detail']}")
    if problems:
        print(f"\nATTEST PROBLEMS: {len(problems)}")
        for line in problems[:20]:
            print(f"  {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
