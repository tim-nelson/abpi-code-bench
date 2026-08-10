"""Derived classifier verdicts over L1 — computed from records.jsonl ONLY.

L1 (build.py) records observations: verbatim text, offsets, per-source slots,
measured evidence. This pass holds every judgment call that used to live
inside the records:

  * heading_confidence   -- the graded high/medium/low model over heading_evidence
  * heading_normalised   -- the nine-token vocabulary (+ SUBHEADING_OR_CAPTION,
                            which depends on the confidence model); l1d.4 widens
                            the PANEL_RULING spelling and REFUSES the build when
                            a PANEL-initial heading is left undecided (R30)
  * heading_normalised_v1 / heading_v1_would_emit -- the v1 verdicts, kept
                            so every generation stays diffable
  * abstract_boundary    -- summary-pane oracle, with regex fallbacks that are
                            inference (restart 10.6% error, last_ruling 22.9%)
  * source_integrity     -- does the report pane belong to this case
  * banner_headings, panel_ruling_standalone_heading -- location rules that
                            depend on heading_normalised

The split exists because these can be WRONG (each has a measured error rate),
while records.jsonl is asserted to be a 100% honest representation of the
HTML. Keeping them here lets the classifiers iterate at L2 cadence without
ever rebuilding — or casting doubt on — L1.

Everything below reads data/l1/records.jsonl and nothing else. That is the
point: these classifiers need no HTML, so the HTML stays retired.

Writes data/l1/derived.jsonl (one object per record, same order).
"""

import difflib
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECORDS = ROOT / "data" / "l1" / "records.jsonl"
OUT = ROOT / "data" / "l1" / "derived.jsonl"

DERIVED_VERSION = "l1d.4"

# Conservative heading normalisation. Only unambiguous matches map; everything
# else stays null. This is deliberately NOT a controlled vocabulary for the
# 3010 observed strings -- collapsing those is an L2 decision.
#
# FROZEN at the v1 six. `normalise_heading_v1` reads this list and nothing else,
# so every generation stays diffable against v1; additions go in the lists below.
NORMALISE = [
    ("PANEL_RULING", re.compile(r"^PANEL RULING\b", re.I)),
    ("APPEAL_BOARD_RULING", re.compile(r"^(APPEAL BOARD(?:'S)?\s+RULING|RULING OF THE APPEAL BOARD)\b", re.I)),
    ("CASE_SUMMARY", re.compile(r"^CASE SUMMARY\s*$", re.I)),
    ("FULL_CASE_REPORT", re.compile(r"^FULL CASE REPORT\s*$", re.I)),
    ("COMPLAINT", re.compile(r"^COMPLAINT\s*$", re.I)),
    ("RESPONSE", re.compile(r"^RESPONSE\s*$", re.I)),
]

# Added in l1d.2 (bench/review/DEFECTS.md D2 and D3). Both are SECTION openers
# that the v1 vocabulary missed, so they are tried before the v2.2 catch-alls:
# a real section beats 'structural but not a section'.
#
# RESPONSE, possessive form. The site restyled the defence heading from a bare
# 'RESPONSE' to "<COMPANY>'S RESPONSE" and the old pattern never matched it, so
# from 2021 the response ran on inside the COMPLAINT segment: 274 headings over
# 257 files (2021 1, 2022 6, 2023 84, 2024 113, 2025 70), and on 249 of those
# files there is no bare 'RESPONSE' heading at all. Measured against a looser
# `.*S RESPONSE$` scan, which additionally caught one in-text subheading
# ('Clinical data cited in Napp's response'); every prefix word is therefore
# required to be capitalised, which excludes it and costs nothing else.
#
# APPEAL_GROUNDS. 'APPEAL BY <COMPANY>' / 'APPEAL FROM THE COMPLAINANT' opens
# the APPELLANT'S grounds -- appeal-side material, not the Panel's ruling. 352
# headings corpus-wide. It is a distinct token rather than APPEAL_BOARD_RULING
# because the Appeal Board has not spoken yet at that point; what it gives L2 is
# the boundary that TERMINATES panel_ruling (DEFECTS D3: 33/35 T3 extracts ran
# past it into the appellant's grounds).
NORMALISE_L1D2 = [
    ("RESPONSE", re.compile(
        r"^[A-Z][A-Za-z0-9&.\-]*(?:[ ][A-Z&][A-Za-z0-9&.\-]*){0,3}"
        r"['’]?[Ss]\s+(?:RESPONSE|Response|response)\s*$")),
    ("APPEAL_GROUNDS", re.compile(r"^APPEAL\s+(?:BY|FROM)\b", re.I)),
]

# Added in l1d.3 (bench/review/DEFECTS.md residual R2). Two more appeal-stage
# heading forms. Both are named here and NEITHER is a boundary by itself: what
# they open depends on WHERE they sit, and that decision belongs to L2's segment
# assembly, which is the layer that knows the document's stage structure. Naming
# them is L1-derived work (this is the heading vocabulary); using them is not.
#
# APPEAL_COMMENTS_HEADING. 'COMMENTS FROM <party>' -- 256 headings, 253 of them
# high-confidence. It is genuinely ambiguous by name: 237 sit in the appeal
# stage (after an 'APPEAL BY ...' heading, or between the Panel's ruling and a
# later Appeal Board heading) and terminate the Panel's ruling; the other 19 are
# Paragraph-17 / interim-case comments gathered at PANEL stage
# ('COMMENTS FROM OTSUKA EUROPE ON THE REPORT FROM THE PANEL') and must not.
# L2 applies the positional test; see `html_boundaries` there.
#
# APPEAL_BOARD_CONSIDERATION. 'APPEAL BOARD CONSIDERATION' standing alone -- 31
# headings. Deliberately anchored to end-of-string: the same words open 80 DATE
# TRAILER lines ('Appeal Board consideration 22 February 2007', 'Appeal Board
# Consideration 15 October 2013, 9 April 2014, 10 December 2014') and three
# 'Appeal Board consideration Interim case report first' lines, none of which is
# a section opener. Unlike COMMENTS FROM this one names the ruling body, so it
# needs no positional test -- there is no Panel-stage form of it.
NORMALISE_L1D3 = [
    ("APPEAL_COMMENTS_HEADING", re.compile(r"^COMMENTS\s+FROM\s+\S")),
    ("APPEAL_BOARD_CONSIDERATION",
     re.compile(r"^APPEAL\s+BOARD\s+CONSIDERATION\s*[:\-–—]?\s*$", re.I)),
]

# Added in l1d.4 (bench/review/DEFECTS.md R30). The Panel's ruling section does
# not always call itself PANEL RULING. `^PANEL RULING\b` alone left 14
# item-bearing cases with NO panel_ruling segment at all, so their labels rested
# on the outcome lists and a dual ruling was undetectable there (AUTH/2107/3/08
# is the live example: three Panel rulings on Clause 7.2, two breach and one
# no breach, none of them read).
#
# THE CLOSED SET IS CHECKABLE, and `check_panel_heading_coverage` checks it: the
# slot is every report heading starting with PANEL or PANEL'S -- 66 distinct
# strings over 2,508 occurrences in 1,779 files. 55 of the 66 are decided as the
# ruling section by the two patterns; the other 11 are decided the other way and
# named in PANEL_HEADING_NOT_A_RULING below. Nothing in the slot is left
# undecided, and a 67th string stops the build.
#
# The new pattern is anchored to END OF STRING, unlike the frozen v1 one. That
# is what separates 'Panel's Ruling' (a heading, 2 occurrences in one file) from
# "Panel's ruling of a breach of Clause 9.1. The appeal on this point was
# unsuccessful." (a sentence): with the possessive admitted and no anchor, the
# sentence would have opened a phantom Panel ruling section on the appeal side.
# The v1 pattern keeps its `\b` so that PANEL RULING IN CASE AUTH/2546/11/12,
# PANEL RULING - GENERAL COMMENTS and the rest of the 2,489 keep matching
# exactly as they did.
#
# What each new spelling costs, measured: PANEL MINUTE 3 headings/3 files
# (AUTH/2076/12/07, AUTH/2088/1/08, AUTH/2107/3/08 -- all three of the
# no-segment cases), Panel decision 3/3 (CASE/0395/12/24, CASE/0440/01/25,
# CASE/0466/01/25), Panel's Ruling 2 headings in 1 file (AUTH/3777/6/23).
NORMALISE_L1D4 = [
    ("PANEL_RULING", re.compile(r"^PANEL(?:['’]S)?\s+(?:RULING|MINUTE|DECISION)\s*$", re.I)),
]

# The other 11 strings in the slot, each read and decided as NOT the ruling
# section. Verbatim, because a near-miss must not be quietly absorbed.
#
# The four 'Panel reconvened ...' markers, 'Panel consideration of additional
# information (12 June 2018)' and 'Panel's conclusion' are Panel-stage
# sub-headings INSIDE a ruling that already has its own PANEL RULING heading;
# promoting them would cut that ruling into pieces and change the prose the
# verdict resolver reads on cases that have no defect. 'Panel's Comments' and
# 'PANEL'S GENERAL COMMENTS' are the preamble, not the ruling. The last two are
# body prose that the over-inclusive candidate finder emitted with evidence, as
# it is designed to.
PANEL_HEADING_NOT_A_RULING = (
    "Panel’s Comments",
    "PANEL’S GENERAL COMMENTS",
    "Panel consideration of additional information (12 June 2018)",
    "Panel in its ruling made no finding of fact in relation to this allegation.",
    "Panel reconvened",
    "Panel reconvened 12 June 2018",
    "Panel reconvened 2 May 2019",
    "Panel reconvened 24 February 2016",
    "Panel;",
    "Panel’s conclusion",
    "Panel’s ruling of a breach of Clause 9.1. The appeal on this point was unsuccessful.",
)
PANEL_HEADING_SLOT_RE = re.compile(r"^PANEL(?:['’]S)?\b", re.I)

# Added in v2.2, each measured. CASE_TITLE and OUTCOME_BANNER exist so that
# things which are NOT sections can be dropped BY NAME -- a null is
# indistinguishable from "unclassified". SUBHEADING_OR_CAPTION separates "we
# know what this is and it is not a section" from "we do not know".
NORMALISE_V2 = [
    ("OUTCOME_BANNER", re.compile(
        r"^(?:CASES?\s+[A-Z/\d\s,and]+?\s+)?(?:NO\s+)?BREACH(?:ES)?\s+OF\s+THE\s+CODE\b"
        r"|^CLAUSE\s+\d+\s+BREACH\b", re.I)),
    ("CASE_TITLE", re.compile(
        r"^(?:CASES?\s+)?[A-Z]{3,}\s*/\s*\d+\s*/\s*\d+\s*/\s*\d+"
        r"|^CASE\s*/\s*\d+"
        r"|^[A-Z][A-Z0-9\-’'&/. ]{2,60}\s+v\s+[A-Z][A-Z0-9\-’'&/. ]{2,60}$"
        r"|^VOLUNTARY ADMISSION BY\b", re.I)),
]

RULING_RE = re.compile(
    r"\b(?:The\s+)?(?:Panel|Appeal Board)\b[^.]{0,90}?"
    r"\b(?:rul\w*|consider\w*|noted|accept\w*|uph\w*|decid\w*)\b"
    r"|\b(?:no\s+)?breach(?:es)?\s+of\s+(?:Clause|the Code)\b[^.]{0,70}?"
    r"\bw(?:as|ere)\s+ruled\b",
    re.I,
)
# No '.' in the class: allowing it lets the match span a sentence boundary.
RESTART_RE = re.compile(r"\b[\w][\w \-’'(),]{2,70}?\s+complained about\b", re.I)
SENT_END_RE = re.compile(r"(?<=[.!?])\s+")

CASE_NUM_RE = re.compile(
    r"\b([A-Z]{3,})\s*/?\s*(\d{2,5})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\b"
)

BODY_TOKENS = ("COMPLAINT", "RESPONSE", "PANEL_RULING", "FULL_CASE_REPORT")


def parse_case_numbers(text):
    found = []
    for m in CASE_NUM_RE.finditer(text or ""):
        norm = "/".join(m.group(1, 2, 3, 4))
        if norm not in found:
            found.append(norm)
    return found


def heading_confidence(ev):
    """THE derivation function. One place, documented, no scattered rules.

    Graded rather than binary because the v1 binary rule dropped ~4.6 genuine
    headings per case, and simply widening it would have inverted the risk into
    inventing headings from table cells and sentence fragments.

    high   unambiguous: an <h*> tag, or shouty standalone text.
    medium plausible section heading: short, mixed case, no terminal
           punctuation, not in a table, and carrying at least one positive
           signal (emphasis, a numbered prefix, or being very short).
    low    everything else that was worth recording as a candidate at all.
    """
    if ev["matches_date_trailer"]:
        return "low"
    if ev.get("unbalanced_quote"):
        return "low"
    # Length as a graded input, not a gate. Measured: `high` headings have a
    # p99 of 71 chars and `medium` p99 of 79, so signal is effectively gone
    # past ~90 -- but long candidates are still EMITTED, at `low`.
    if ev["char_count"] > 90:
        return "low"
    if ev["carrier_tag"] in ("h1", "h2", "h3", "h4", "h5", "h6"):
        # An <h*> is an explicit authoring signal. It is only downgraded when
        # the content is plainly a sentence -- a few sources wrap body prose
        # in <h3>, which is the one way v1 produced false positives.
        if ev["has_terminal_punctuation"] and ev["word_count"] > 8:
            return "low"
        return "high"
    if ev["uppercase_ratio"] >= 0.8 and not ev["in_table_cell"]:
        return "high"
    if ev["has_terminal_punctuation"] or ev["in_table_cell"]:
        return "low"
    if ev["word_count"] <= 12 and (
        ev["is_bold_or_emphasised"] or ev["has_numbered_prefix"] or ev["word_count"] <= 6
    ):
        return "medium"
    return "low"


def normalise_heading(s, evidence=None):
    """Six validated tokens, the two added in l1d.2, the two added in l1d.3,
    then three added in v2.2, then a catch-all for things that are structural
    but not sections."""
    if not s:
        return None
    t = s.strip()
    for token, pat in NORMALISE:
        if pat.match(t):
            return token
    for token, pat in NORMALISE_L1D2:
        if pat.match(t):
            return token
    for token, pat in NORMALISE_L1D3:
        if pat.match(t):
            return token
    for token, pat in NORMALISE_L1D4:
        if pat.match(t):
            return token
    for token, pat in NORMALISE_V2:
        if pat.match(t):
            return token
    # Anything else that still reads as a heading is a sub-heading or caption:
    # structural, but not a top-level section. Only claimed for candidates the
    # confidence model already rates plausible, so `low` noise stays null.
    if evidence is not None:
        conf = heading_confidence(evidence)
        if conf in ("high", "medium"):
            return "SUBHEADING_OR_CAPTION"
    return None


def normalise_heading_v1(s):
    """The six-token verdict, preserved so every generation stays diffable."""
    if not s:
        return None
    for token, pat in NORMALISE:
        if pat.match(s.strip()):
            return token
    return None


def v1_would_emit(ev):
    """The v1 binary rule, recomputed from recorded evidence.

    build.py used to compute this from the unrounded uppercase ratio; the
    evidence stores it rounded to 3 dp. Equality against the last in-record
    generation was verified corpus-wide (2026-08-01): no candidate sits on
    the rounding knife-edge.
    """
    tag = ev["carrier_tag"]
    is_h = len(tag) == 2 and tag.startswith("h") and tag[1].isdigit()
    return bool(is_h or ev["uppercase_ratio"] >= 0.8)


def source_integrity(rec_case_numbers, report_sections, h1_text):
    """Does the report pane belong to this case?

    Four pages carry a correct summary pane and correct labels but a report
    pane belonging to a different case. They are flagged, never repaired: the
    correct report already exists on disk under its own case URL (and, since
    2026-08-01, as a verified PDF in data/pdf/), and re-pairing would
    manufacture a record that exists nowhere in the source.
    """
    if not report_sections:
        return "ok", None
    head = report_sections[0]["heading_text"] or report_sections[0]["text"][:140]
    found = parse_case_numbers(head)
    if not found:
        return "ok", None
    own = set(rec_case_numbers)
    if own & set(found):
        return "ok", None
    # Same serial, different month/year -> a typo in the title line, not a
    # different case.
    own_serial = {c.split("/")[1] for c in own}
    got_serial = {c.split("/")[1] for c in found}
    if own_serial & got_serial:
        return "ok_with_title_typo", f"title line reads {found[0]}"
    # A Paragraph 17 case legitimately arises from another case and names it.
    if re.search(r"Paragraph 17|During (?:its|the) consideration of", h1_text or "", re.I):
        return "ok_arises_from_other_case", f"arises from {found[0]}"
    if re.match(r"^\s*During (?:its|the) consideration of", report_sections[0]["text"], re.I):
        return "ok_arises_from_other_case", f"arises from {found[0]}"
    return "report_pane_mismatch", f"report pane belongs to {found[0]}"


def abstract_boundary(summary_text, report_text, body_starts_at=None):
    """Locate where the abstract ends and the report body begins.

    Primary method is an ORACLE independent of any ruling regex: the Case
    Summary pane is the abstract verbatim, so aligning it against the leading
    report section locates the boundary directly. This matters because the v1
    splitter used the same RULING regex to place AND to verify the split, so
    its errors were invisible by construction -- it scored 97.1% and was
    actually 85.2%.

    Fallbacks are inference, not measurement, and are marked as such so L2 can
    refuse them. `restart` (10.6% error) is preferred over `last_ruling`
    (22.9%).
    """
    blank = {"offset": None, "method": None, "oracle_available": False,
             "oracle_alignment_score": None, "confidence": None,
             "is_measured": False, "note": None}
    if not report_text:
        return blank
    # Offsets are into panes.report.text, NOT into a section. Graded heading
    # detection splits the abstract across several sections, so the boundary
    # cannot be expressed relative to section 0.
    lead = report_text
    summ = re.sub(r"^Case Summary\s*", "", summary_text or "").strip()

    if len(summ) >= 200:
        lw = [(m.group(0), m.start()) for m in re.finditer(r"\S+", lead)]
        sw = [m.group(0) for m in re.finditer(r"\S+", summ)]
        sm = difflib.SequenceMatcher(a=[w for w, _ in lw], b=sw, autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size >= 20]
        if blocks:
            matched = sum(b.size for b in blocks)
            score = round(matched / max(1, len(sw)), 3)
            endtok = max(b.a + b.size for b in blocks)
            offset = lw[endtok][1] if endtok < len(lw) else len(lead)
            if offset >= len(lead) - 2:
                return {**blank, "oracle_available": True,
                        "oracle_alignment_score": score, "is_measured": True,
                        "note": "leading section is abstract-only; nothing to split"}
            return {"offset": offset, "method": "oracle", "oracle_available": True,
                    "oracle_alignment_score": score,
                    "confidence": "high" if score >= 0.8 else "medium",
                    "is_measured": True, "note": None}

    # ---- fallbacks: inference ------------------------------------------
    # Bound the search to the pre-body region. Without this the "last ruling"
    # is the one in the actual PANEL RULING section near the end of the pane,
    # not the one closing the abstract -- a deliberate-holdout measurement
    # scored the unbounded version at 0.7% correct.
    region = lead[:body_starts_at] if body_starts_at else lead
    if not region.strip():
        return {**blank, "note": "no pre-body region to search"}
    last = None
    for m in RULING_RE.finditer(region):
        last = m
    if not last:
        return {**blank, "note": "no ruling language in the leading section"}
    starts = [0] + [m.end() for m in SENT_END_RE.finditer(region)]
    m = RESTART_RE.search(region, last.end())
    if m:
        cands = [s for s in starts if s <= m.start()]
        return {"offset": cands[-1] if cands else m.start(), "method": "restart",
                "oracle_available": False, "oracle_alignment_score": None,
                "confidence": "medium", "is_measured": False, "note": None}
    nxt = next((s for s in starts if s > last.end()), None)
    if nxt is None:
        return {**blank, "note": "leading section ends on its last ruling; nothing to split"}
    return {"offset": nxt, "method": "last_ruling", "oracle_available": False,
            "oracle_alignment_score": None, "confidence": "low",
            "is_measured": False,
            "note": "inferred; last_ruling had 22.9% error against the oracle"}


def derive_record(rec):
    r_sections = [s for s in rec["sections"] if s["pane"] == "report"]

    sec_out = []
    normalised_by = {}
    for s in rec["sections"]:
        ev = s["heading_evidence"]
        htext = s["heading_text"]
        conf = heading_confidence(ev) if ev else None
        norm = normalise_heading(htext, ev)
        sec_out.append(
            {
                "index": s["index"],
                "pane": s["pane"],
                "heading_confidence": conf,
                "heading_normalised": norm,
                "heading_normalised_v1": normalise_heading_v1(htext),
                "heading_v1_would_emit": v1_would_emit(ev) if ev else False,
            }
        )
        normalised_by[(s["pane"], s["index"])] = norm

    # ---- outcome banner headings (above the first COMPLAINT / PANEL RULING)
    banner = []
    first_body = None
    for s in r_sections:
        if normalised_by[("report", s["index"])] in ("COMPLAINT", "RESPONSE", "PANEL_RULING"):
            first_body = s["index"]
            break
    for s in r_sections:
        if first_body is not None and s["index"] >= first_body:
            break
        h = s["heading_text"]
        if h and re.search(r"\b(NO BREACH|BREACH OF THE CODE|BREACH OF CLAUSE)\b", h, re.I):
            banner.append(h)

    status, note = source_integrity(
        rec["identity"]["filename_case_numbers"], r_sections, rec["identity"]["h1_text"]
    )
    body_at = next(
        (s["char_start"] for s in r_sections
         if normalised_by[("report", s["index"])] in BODY_TOKENS),
        None,
    )
    boundary = abstract_boundary(
        rec["panes"]["summary"]["text"], rec["panes"]["report"]["text"], body_at
    )

    return {
        "schema_version": DERIVED_VERSION,
        "file": rec["file"],
        "source_schema_version": rec["schema_version"],
        "sections": sec_out,
        "abstract_boundary": boundary,
        "source_integrity": {"status": status, "note": note},
        "banner_headings": banner,
        "panel_ruling_standalone_heading": any(
            normalised_by[("report", s["index"])] == "PANEL_RULING" for s in r_sections
        ),
    }


def check_panel_heading_coverage(seen):
    """R30. Every report heading that starts with PANEL must be DECIDED.

    A hand-typed slot with 66 distinct values, so 'does the pattern catch
    everything?' (unanswerable) becomes 'is every value decided?' (checkable).
    It is decided when it normalises to PANEL_RULING or is named in
    PANEL_HEADING_NOT_A_RULING. Anything else is a spelling nobody has read,
    and reading it is the only way to know whether a case's Panel ruling is
    about to go missing -- which is exactly what R30 was.
    """
    undecided = sorted(h for h in seen
                       if normalise_heading_panel(h) is None
                       and h not in PANEL_HEADING_NOT_A_RULING)
    if undecided:
        raise SystemExit(
            "REFUSING: report heading(s) starting with PANEL that the ruling vocabulary "
            "does not decide:\n  "
            + "\n  ".join(repr(h) for h in undecided)
            + "\nAdd the spelling to NORMALISE_L1D4, or record it in "
              "PANEL_HEADING_NOT_A_RULING with the reason it is not the ruling section.")


def normalise_heading_panel(s):
    """PANEL_RULING iff one of the two ruling patterns matches; else None."""
    t = (s or "").strip()
    for token, pat in NORMALISE + NORMALISE_L1D4:
        if token == "PANEL_RULING" and pat.match(t):
            return token
    return None


def main():
    n = 0
    panel_headings = set()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Written to a sibling temp file and renamed into place, like every other
    # regenerated artefact in the pipeline: an audit reading derived.jsonl while
    # a build runs must see the old generation or the new one, never a prefix.
    # The coverage refusal runs BEFORE the rename, so a build that stops leaves
    # the previous good file on disk.
    tmp = OUT.with_name(OUT.name + ".tmp")
    with RECORDS.open(encoding="utf-8") as fh, tmp.open("w", encoding="utf-8") as out:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            for s in rec["sections"]:
                h = (s["heading_text"] or "").strip()
                if s["pane"] == "report" and h and PANEL_HEADING_SLOT_RE.match(h):
                    panel_headings.add(h)
            out.write(json.dumps(derive_record(rec), ensure_ascii=False) + "\n")
            n += 1
    check_panel_heading_coverage(panel_headings)
    os.replace(tmp, OUT)
    print(f"derived {n} records -> {OUT}  ({OUT.stat().st_size/1e6:.1f} MB)")
    print(f"PANEL-initial report headings: {len(panel_headings)} distinct, all decided "
          f"({len(panel_headings) - len(PANEL_HEADING_NOT_A_RULING)} ruling, "
          f"{len(PANEL_HEADING_NOT_A_RULING)} not)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
