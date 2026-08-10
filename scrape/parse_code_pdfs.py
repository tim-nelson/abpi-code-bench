"""Parse the pre-2014 ABPI Code PDFs into clause text.

data/code/clauses.jsonl covers only the six INTERACTIVE editions (2014, 2015,
2016, 2019, 2021, 2024), so 3,412 of the 10,045 bench items -- every item whose
case was ruled under a 2003-2012 edition -- carried `clause_text: null` and the
prompt's "the text of this clause is not available to you" line. Every edition
the bank reaches is now on disk as a PDF (data/code/pdf/, pinned in
data/code/manifest.jsonl): 2006-2012 came from the PMCPA's own media library
(scrape/fetch_pdfs.py), and the 2001 and 2003 editions -- which the PMCPA has
never published in either form -- came from the Wayback Machine's captures of
abpi.org.uk on 2026-08-09. NO_SOURCE_YEARS is empty as a result.

Three clauses of the INTERACTIVE era are read here too (GAP_BACKFILL): the
PMCPA published exactly three of its 303 clause pages with empty bodies --
2014's Clause 26 and 2015's and 2016's Clause 29, all of them "Compliance with
Undertakings" -- so the site parser refused and logged all three
(data/logs/failures.log carries the only three code-extract entries). Their
printed editions carry the clause, one paragraph, no subclauses, no
supplementary information; from those three documents we emit exactly the
clauses clauses.jsonl lacks and nothing else, so the two extractions still own
disjoint (year, clause) pairs.

    uv run --with 'pypdfium2==4.30.0' python scrape/parse_code_pdfs.py

    -> data/code/pdf_clauses.jsonl            one row per clause per edition
    -> data/code/pdf_clauses_exclusions.jsonl one row per referenced pair we
                                              decline to emit, with the reason

The output NEVER goes into clauses.jsonl. HTML-derived and PDF-derived clause
text live in separate files for the same reason constructed labels do: a reader
must be able to tell, without reading a builder, which extraction produced a
value. bench/generate.py unions the two files and refuses on any overlap.

Eight documents are read: the 2001, 2003, 2006, 2008 and 2011 editions, BOTH
2012 editions, and the 2013 addendum that replaces Clause 16 of the second 2012
edition.

Extraction is pypdfium2, the extractor l1/build_pdf.py and
scrape/extract_publication_text.py already use, so PDF text in this repo still
comes from one engine. The mechanisms below are l1/build_pdf.py's, re-measured
on these eight documents:

  * Rows cluster on the char ORIGIN, not the glyph box bottom (descenders tear
    'y' into its own row).
  * Line-break hyphens start from pdfium's own FPDFText_IsHyphen, which is not
    a heuristic here: these fonts emit a distinct glyph (U+0002) for the
    compositor's hyphen and the flag matches it 1:1 -- 396/396 in 2001, 401/401
    in 2003, 506/506 in 2006, 588/588 in 2008, 17/17 in 2011, 22/22 in 2012,
    20/20 in the second 2012 edition. Whether each of the 1,595 on the clause
    pages should be dropped or written back as a real hyphen is decided
    separately, per instance, against the corpus (decide_hyphens).
  * The gutter is measured per page as the widest zero-ink band in the middle
    40%, and a page where any glyph crosses it is NOT a clause page -- the run
    of clause pages stops there. On this corpus the only crossings are the
    title and contents pages, the Constitution, and the addendum's cover (in
    2001 and 2003, pp1-5 and the Constitution from p32, leaving clause pages
    6-31).
  * A folio that shares a baseline with body copy is stripped by measure, not
    by pattern (drop_furniture); five clause texts carried one before that.
    2001 and 2003 print the folio one lower than the PDF page and set three of
    them on a body baseline IN THE SAME COLUMN (p6 '5', p9 '8', p13 '12'),
    which drop_furniture takes. The other 23 also share a baseline with copy,
    but across the gutter -- the folio is alone on its side of the page, so the
    column split leaves it a whole line and RUNNING takes it. Nothing on a
    clause page of either edition survives into a text with a folio in it.

The Code/Supplementary split is TYPOGRAPHIC, not geometric. Every edition sets
the provisions in Palatino-Roman/Bold and the supplementary information in
Palatino-Italic/BoldItalic -- 2001 and 2003 included, checked rather than
assumed: those two documents' fonts are Palatino-Roman/Italic/Bold/BoldItalic
and nothing else in the body, and not one clause-page line comes out with an
undecidable face. The two streams do NOT occupy fixed columns: on 2259 p13 the
left column holds 4.9 and 4.10 supplementary above Clause 5's provisions while
the right column holds 4.11 supplementary above the tail of 5.4's bullet list.
Splitting left/right would have interleaved them. Each stream is read in the
page's reading order and the result is checked afterwards: clause numbers must
run 1..N and each clause's subclauses must run 1..K under its own major number.

A page is NOT always one two-column flow, and reading all of the left column
and then all of the right was wrong on the pages where it is not (DEFECTS
R23). Where a clause section starts part way down the LEFT column, the section
above it is set as a block balanced across both columns and the new section
starts below it: the page carries two vertical BANDS, and the printed order is
band by band -- each band's left column, then that band's right column. On
2256 p39 the left column's band 1 ends '...set out in Clause 23.7 of the 2011'
and its sentence finishes at the TOP of the right column, 'Code of Practice
and its supplementary information remain applicable.', while band 2 below
carries Clause 24. Column-major reading put all of Clause 24's left column in
between, which is how 23.7 came out cut and how its tail attached to Clause
24.1. page_bands() finds the bands; see the comment there for the rule and the
receipts.

One exception, and it is a measured one: the 2011 and 2012 editions set the
display heading "Clause 24" in Palatino-Italic 24pt (the title beneath it,
"The Internet", is Palatino-Bold 12pt). That numeral is a Code heading wearing
an italic face, so a line reading exactly "Clause N" at >= 20pt is forced into
the code stream. 2001, 2003, 2006 and 2008 have no such lines -- their headings
are bold roman 9.5pt with the title inline -- so the rule is inert there, and
the build refuses if the count of forced numerals ever differs from the number
of clauses parsed (25 and 25 in the second 2012 edition, 1 and 1 in the
addendum, 0 and 0 in 2001, 2003, 2006, 2008, 2011 and the 2012 first edition,
whose numerals are drawn as paths and are not in the text layer at all).
"""

import ctypes
import difflib
import hashlib
import json
import math
import pathlib
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402

CODE_DIR = common.DATA / "code"
MANIFEST = CODE_DIR / "manifest.jsonl"
HTML_CLAUSES = CODE_DIR / "clauses.jsonl"
ASSIGNMENTS = CODE_DIR / "edition_assignments.jsonl"
ITEMS = common.ROOT / "bench" / "items.jsonl"
OUT = CODE_DIR / "pdf_clauses.jsonl"
OUT_EXCL = CODE_DIR / "pdf_clauses_exclusions.jsonl"

PINNED_PYPDFIUM2 = "4.30.0"
EXTRACTOR = "scrape/parse_code_pdfs.py"
EXTRACTOR_VERSION = "pdfclause.2"

# The editions this script is for. 2001 and 2003 joined on 2026-08-09: the
# PMCPA publishes neither, but the Wayback Machine's captures of abpi.org.uk
# do, and both files carry a full text layer. Between them they cover 177 of
# the items that had none -- 6 in 2001 (AUTH/3200/5/19) and 171 in 2003 across
# 33 cases.
PDF_YEARS = (2001, 2003, 2006, 2008, 2011, 2012)

# Code years the bank references and NOTHING on disk carries. EMPTY since
# 2026-08-09, and kept empty rather than deleted for the same reason
# KNOWN_TRUNCATIONS is: the branch below it is what turns a missing edition
# into a reasoned exclusion row instead of a silent gap, and a bank that
# reaches a seventh edition would need it again.
#
# It held 2003 from the start (174 items then, 171 after the code-year
# adjudications) and gained 2001 with the R19 corrections -- AUTH/3200/5/19 is
# a 2019 complaint about a 2002 meeting, and the Panel says so: 'The
# requirements of the 2001 Code therefore applied ... No breach of Clause 19.1
# of the 2001 Code was ruled'. Both are now parsed from the archived PDFs, so
# both rows are gone; what replaces them is text for 175 of the 177 items and
# an `absent_from_edition` row for the other 2.
NO_SOURCE_YEARS = {}

# Every Code year the bank references that predates the interactive editions
# (clauses.jsonl starts at 2014) has to be answered for HERE -- with a
# document, or with a NO_SOURCE_YEARS reason. A year in neither table would
# fall out of `years` below and vanish from the coverage refusal without a
# word, which is the shape of gap that hid the 2001 items for two builds.
PRE_INTERACTIVE_BEFORE = 2014

# The three bodiless interactive clause pages, backfilled from print. Each
# entry names the document and the clauses to take from it; the build refuses
# if clauses.jsonl's gap for that year ever stops being exactly that set, so a
# newly-published page cannot be shadowed by a stale backfill. 2016 is on disk
# twice under two media ids (2010__ and 2257__, same sha256); the row names
# 2257__ because that is the one the PMCPA's 2016 edition page links.
GAP_BACKFILL = {
    2014: ("pdf/2274__pmcpa-code-of-practice-2014.pdf", ("26",)),
    2015: ("pdf/2275__pmcpa-code-of-practice-2015.pdf", ("29",)),
    2016: ("pdf/2257__code-of-practice-2016.pdf", ("29",)),
}

# The three 2012 documents by name. data/code/edition_assignments.jsonl points
# at an edition with one of these tokens, so the mapping has to be one place
# and the build refuses if the manifest's 2012 set ever stops matching it.
EDITION_TOKENS = {
    "pdf/2256__code-of-practice-2012.pdf": "first_2012",
    "pdf/2259__code-of-practice-second-2012-edition.pdf": "second_2012",
    "pdf/2216__addendum-to-second-2012-edition-clause-16-2.pdf": "second_2012_addendum",
}

# ---- measured thresholds (l1/build_pdf.py's, re-measured here) -------------
BASELINE_MERGE_PT = 3.5      # row pitch is >= 5.7pt here; superscripts sit ~2pt up
SPACE_GAP_FALLBACK_EM = 0.5  # a gap this wide reads as a word space with no space glyph
GUTTER_MIN_PT = 6.0          # a printed gutter is empty; rivers in justified text are narrower
GUTTER_SEARCH = (0.30, 0.70)  # fraction of page width searched for the gutter
COVER_STEP = 4               # quarter-point resolution for the ink-coverage scan
FULL_EPS_PT = 3.0            # short of the measure and the line ends a paragraph
DISPLAY_HEADING_PT = 20.0    # "Clause 24" as a display numeral, never body text
HEADING_MIN_PT = 11.0        # body is 9.5pt in every edition; titles are 12pt+
FURNITURE_PT = 4.0           # a folio sits ~17pt outside the measure; copy never does
FURNITURE_MAX_GLYPHS = 3     # folios run to 2 digits in these editions
# Paragraph breaks are set 1.17x-1.5x the body pitch, continuations at exactly
# 1x, and NOTHING in this corpus lands between: the eight editions run 12.0 or
# 11.4pt with the next gap up at 14.0 (2001 and 2003 are 12.0 in both streams,
# and their band comes out empty like the rest). The build refuses if that
# band fills.
PARA_GAP_RATIO = 1.15
PARA_GAP_AMBIGUOUS = 1.02
# How much wider a band boundary must be than the widest strip that would read
# the page DIFFERENTLY (page_bands). Measured over the 138 boundaries the
# eleven documents produce on their clause pages: 39 have no
# differently-reading rival at all, and of the 99 that do the margins are
# 3.94pt once and then 9.83pt and up -- so 6.0 sits in an empty band, the way
# the paragraph threshold does. The guard fires on exactly one boundary,
# declared in BAND_UNDECIDED; narrowing it to 4.0 makes it fire on none, which
# is how the firing was shown to be real. Adding 2001 and 2003 (2026-08-09)
# moved the no-rival count 27 -> 39 and left the rival distribution untouched:
# all 12 of their boundaries have no rival that reads the page differently,
# because in those two editions the two columns ARE the two streams.
BAND_TIE_PT = 6.0

# Running heads and folios, matched on a whole line. A folio that shares its
# baseline with body copy is not one of these -- see drop_furniture.
RUNNING = [
    re.compile(r"^CODE OF PRACTICE$"),
    re.compile(r"^SUPPLEMENTARY INFORMATION$"),
    re.compile(r"^CODE OF PRACTICE SUPPLEMENTARY INFORMATION$"),
    re.compile(r"^PROVISIONS OF THE CODE OF PRACTICE$"),
    re.compile(r"^\d{1,3}$"),                      # folio
    re.compile(r"^\d{1,3} CODE OF PRACTICE$"),     # folio + running head (2012 2nd)
    re.compile(r"^CODE OF PRACTICE \d{1,3}$"),
    re.compile(r"^\d{4,6} Code .*Page \d+$"),      # printer's imposition slug
]

BULLET_CHARS = "●•▪■∙·"

# The 2013 addendum is not an edition: it is two pages replacing Clause 16 of
# the second 2012 edition, so it starts at clause 16 and the 1..N check is off.
ADDENDUM_MARK = "addendum"

# The residue of the line-break-hyphen decision, each with its receipt. 1,639
# instances across the eleven documents; the corpus decides 1,632 of them, and
# these four patterns (7 instances) are the ones it cannot.
HYPHEN_DECIDED = {
    # 2011 and the 2012 first edition lose the word spaces in this sentence --
    # their text layer reads 'non|interventionalstudiesthat are completed',
    # where the 2012 second edition reads 'non-interventional studies that are
    # completed'. The spaces cannot be restored without inventing them, but the
    # hyphen is decidable: it is the compound's own, as everywhere else in the
    # same clause ('non-interventional' 13x unbroken in each edition).
    ("non", "interventionalstudiesthat"): True,
    # 'the use of audio-cas|settes, films, records, tapes, video recordings'
    # (2006 and 2008, Clause 1.2). The compound is 'audio-cassettes' and its
    # hyphen is already printed before the break, so this one is inside the
    # word 'cassettes'. Dropped from the 2011 edition onwards, which is why
    # neither form is attested anywhere off a line join.
    ("cas", "settes"): False,
    # 'the promotion of over-the|counter medicines' (2006 and 2008, Clause
    # 1.1). 'over-the-counter' appears 103 times unbroken in
    # data/code/clauses.jsonl; the halves fail the whole-word test only
    # because 'counter' never stands alone in the Code.
    ("the", "counter"): True,
    # 'Prizes of a higher value than would ordinar|ily be acceptable for a
    # promotional aid' (2001 and 2003 p26, supplementary information to Clause
    # 18.2 -- the printed line reads 'ordinar-'). The break is inside the word:
    # 'ordinar' is not a word, 'ily' is not a word, and 'ordinar-ily' is not a
    # compound. The Code corpus attests neither form off a line join, which is
    # why the automatic rule refused; the reports the bench is built from
    # attest the closed form 76 times (data/l1/records.jsonl, 'ordinarily',
    # never hyphenated). Added 2026-08-09 with the two archived editions.
    ("ordinar", "ily"): False,
}

# The residue of the line-broken-at-a-solidus decision, the same shape as
# HYPHEN_DECIDED. ELEVEN instances across the eleven documents (delta noted
# per the docs rule: this comment said "eight across the nine documents" and
# the build prints 11 -- 2259, 2274 and 2275 break one line each and 2016
# breaks eight; 2001 and 2003 add none, not one line in either document ends
# in a solidus, and not one ends in a real hyphen either). The corpus decides
# all eleven and this table is empty. Each is a compound the Code sets closed:
# counted in clauses.jsonl, the independent HTML extraction, 'and/or' 691x,
# 'medical/generic' 40x, 'project/activity' 13x, 'his/her' 12x,
# 'activities/materials' 11x, 'employees/agents' 5x, 'multiple/cumulative' 5x,
# 'refreshments/subsistence' 4x -- against a single spaced occurrence anywhere
# ('activities/ materials', 1x). Joining these with a word space is what made
# the two 2012 editions' Clause 1.8 renderings differ on nothing (R22): the
# second edition breaks the line at 'when activities/' on p9 and the first sets
# the same sentence mid-line.
SLASH_DECIDED = {}

# Supplementary blocks that come out TRUNCATED -- their text ends with no
# terminal punctuation because the printed paragraph continues in another
# column and the reading order put other blocks in between. This was six of
# 669 blocks under the column-major order (2011 cl 10.1 + 18.6; 2012 first
# edition cl 10.1 + 18.6 + 23.7; 2012 second edition cl 10.1 -- DEFECTS R23),
# and the page-band reading order resolved ALL SIX, so the set is EMPTY. It is
# kept, and the refusal below is kept, because the test is the regression
# guard on the band rule: a band the rule misses cuts a block, and a block cut
# is what this notices. The build refuses on any truncation not listed here.
# Run over 2001 and 2003 when they were added (2026-08-09): 0 of their 178
# supplementary blocks are cut, which is the independent confirmation that
# their 12 band boundaries changed no stream's order.
KNOWN_TRUNCATIONS = set()

# Pages where the band rule found a boundary it cannot choose: two candidate
# strips within BAND_TIE_PT of each other that would read the page
# differently. One in the eleven documents, and it is in a gap-backfill
# document, where this build emits one clause (2016 Clause 29, pages 42-43)
# and nothing else:
#
#   * 2016 p38, above the 'Clause 25' display numeral: 14.31pt against
#     10.37pt. The narrower strip has 'Clause 24.1 Date of Implementation' and
#     its paragraph beneath it -- Clause 24's guidance, which either band
#     could hold -- and 3.94pt is not a difference the geometry can be read
#     on. No cut is made there; the page keeps one band and every block on it
#     is marked `attachment_suspect`, so nothing from it is served.
#
# 2001 and 2003 add none (2026-08-09). Their clause pages produce 6 boundaries
# each and every one is unrivalled. Both documents DO refuse boundaries outside
# the clause pages -- 2003 p39 'GENERAL PROVISIONS' at 2.42 against 2.41 and
# p43 'OTHER CODES' at 10.87 against 8.40, both inside the Constitution and
# Procedure -- and those never reach this set, because document_streams hands
# back only the undecided bands on the pages it reads. That is the same rule
# that keeps 2016 p38 out of the emitted text, stated the other way round.
#
# The build refuses on an undecided boundary not listed here, so the set
# cannot grow silently.
BAND_UNDECIDED = {
    ("pdf/2257__code-of-practice-2016.pdf", 38, "Clause 25"),
}
# A paragraph that ends a supplementary block ends a sentence. Bullet lists end
# on the list's last item, which carries the stop; a block that ends anywhere
# else was cut by the reading order.
TERMINAL_RE = re.compile(r"[.:;?!)’\"']\s*$")

SUB_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\s+(.*)$")
CLAUSE_INLINE_RE = re.compile(r"^Clause\s+(\d{1,2})\s+(.+)$")
CLAUSE_ALONE_RE = re.compile(r"^Clause\s+(\d{1,2})$")
SUPP_DIVIDER_RE = re.compile(r"^Clause\s+(\d{1,2})\s+Supplementary Information$")
# "Clause 4.1 Electronic Journals" / "Clauses 4.1 and 4.9 Date of ..." /
# "Clause 2 Discredit to, and Reduction of Confidence in, the Industry"
SUPP_HEAD_RE = re.compile(
    r"^Clauses?\s+(\d{1,2}(?:\.\d{1,2})?)"
    r"((?:\s*(?:,|and)\s*(?:Clause\s+)?\d{1,2}(?:\.\d{1,2})?)*)"
    r"\s+(\S.*)$")
SUPP_REF_RE = re.compile(r"\d{1,2}(?:\.\d{1,2})?")


# --- pdfium ----------------------------------------------------------------

def is_addendum(row):
    return ADDENDUM_MARK in (row.get("url") or "").lower()


def _pdfium():
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError:
        sys.exit("REFUSING: pypdfium2 not installed. Run:\n"
                 "  uv run --with 'pypdfium2==4.30.0' python scrape/parse_code_pdfs.py")
    return pdfium, raw


def read_chars(tp, raw):
    """Every glyph with its geometry, face and line-break-hyphen flag.

    Generated glyphs (pdfium's own word spaces, derived from TJ offsets) are
    kept: on this corpus many word spaces exist only as generated chars and
    dropping them fuses words ('Isle ofMan').
    """
    out = []
    for i in range(raw.FPDFText_CountChars(tp)):
        u = raw.FPDFText_GetUnicode(tp, i)
        left = ctypes.c_double(); right = ctypes.c_double()
        bottom = ctypes.c_double(); top = ctypes.c_double()
        raw.FPDFText_GetCharBox(tp, i, ctypes.byref(left), ctypes.byref(right),
                                ctypes.byref(bottom), ctypes.byref(top))
        ox = ctypes.c_double(); oy = ctypes.c_double()
        raw.FPDFText_GetCharOrigin(tp, i, ctypes.byref(ox), ctypes.byref(oy))
        m = raw.FS_MATRIX()
        raw.FPDFText_GetMatrix(tp, i, ctypes.byref(m))
        size = raw.FPDFText_GetFontSize(tp, i) * math.hypot(m.c, m.d)
        buf = ctypes.create_string_buffer(96)
        flags = ctypes.c_int()
        raw.FPDFText_GetFontInfo(tp, i, buf, 96, ctypes.byref(flags))
        font = buf.value.decode("utf-8", "replace")
        weight = raw.FPDFText_GetFontWeight(tp, i)
        out.append({
            "i": i, "ch": chr(u),
            "x0": left.value, "x1": right.value, "oy": oy.value,
            # the glyph BOX's vertical extent, not the origin: the band rule
            # measures zero-ink strips, and a strip has to clear ascenders and
            # descenders, not baselines
            "y0": min(bottom.value, top.value), "y1": max(bottom.value, top.value),
            "size": size, "font": font,
            "bold": ("bold" in font.lower()) or (600 <= weight <= 1000),
            "italic": ("italic" in font.lower()) or ("oblique" in font.lower()),
            "hyphen": raw.FPDFText_IsHyphen(tp, i) == 1,
        })
    return out


def is_blank(ch):
    return ch.isspace() or ch in "\r\n\x00"


def group_rows(chars):
    """Printed rows, clustered on the baseline origin. `ink` is x-ordered (the
    gutter maths needs leftmost-first); `chars` keeps stream order for text."""
    rows = []
    for ch in sorted(chars, key=lambda c: (-c["oy"], c["x0"])):
        for row in rows:
            if abs(row["oy"] - ch["oy"]) <= BASELINE_MERGE_PT:
                row["chars"].append(ch)
                break
        else:
            if is_blank(ch["ch"]):
                continue          # a space never opens a row
            rows.append({"oy": ch["oy"], "chars": [ch]})
    keep = []
    for row in rows:
        row["ink"] = sorted((c for c in row["chars"]
                             if not is_blank(c["ch"]) and not c["hyphen"]),
                            key=lambda c: c["x0"])
        if not row["ink"]:
            continue
        o = sorted(c["oy"] for c in row["ink"])
        row["baseline"] = o[len(o) // 2]
        keep.append(row)
    return keep


def make_line(chars):
    """One printed line. Word spaces come from the page's own space glyphs
    (generated ones included), with a wide glyph gap as fallback; the
    line-break hyphen is dropped and recorded on the line instead."""
    ink = [c for c in chars if not is_blank(c["ch"]) and not c["hyphen"]]
    if not ink:
        return None
    text = ""
    prev = None
    pending = False
    ends_hyphen = False
    for c in sorted(chars, key=lambda c: c["i"]):
        if is_blank(c["ch"]):
            pending = True
            continue
        if c["hyphen"]:
            ends_hyphen = True
            continue
        gap = (c["x0"] - prev["x1"]) if prev is not None else 0.0
        if text and (pending or gap >= SPACE_GAP_FALLBACK_EM * max(c["size"], 1.0)):
            text += " "
        pending = False
        ends_hyphen = False
        text += c["ch"]
        prev = c
    ital = sum(1 for c in ink if c["italic"])
    rom = sum(1 for c in ink if not c["italic"]
              and ("palatino" in c["font"].lower() or "times" in c["font"].lower()))
    return {
        "text": text,
        "hyphen": ends_hyphen,
        "x0": min(c["x0"] for c in ink),
        "x1": max(c["x1"] for c in ink),
        "y0": min(c["y0"] for c in ink),
        "y1": max(c["y1"] for c in ink),
        "size": round(sorted(c["size"] for c in ink)[len(ink) // 2], 1),
        "bold": sum(1 for c in ink if c["bold"]) * 2 >= len(ink),
        "italic_ink": ital, "roman_ink": rom,
        "baseline": sorted(c["oy"] for c in ink)[len(ink) // 2],
    }


def is_running(text):
    t = text.strip()
    return any(p.match(t) for p in RUNNING)


def gutter_centre(rows, width):
    """Centre of the widest zero-ink band in the middle 40% of the page, or
    None. Zero-ink by construction, so nothing can straddle the band we pick
    unless the page is not two-column at all."""
    long_rows = [r for r in rows if len(r["ink"]) >= 10]
    lo = int(width * GUTTER_SEARCH[0] * COVER_STEP)
    hi = int(width * GUTTER_SEARCH[1] * COVER_STEP)
    cov = [0] * (int(width * COVER_STEP) + 2)
    for r in long_rows:
        for c in r["ink"]:
            a = max(0, int(c["x0"] * COVER_STEP))
            b = min(len(cov) - 1, int(c["x1"] * COVER_STEP) + 1)
            for k in range(a, b):
                cov[k] += 1
    bands, start = [], None
    for k in range(lo, hi):
        if cov[k] == 0:
            if start is None:
                start = k
        elif start is not None:
            bands.append((start / COVER_STEP, k / COVER_STEP)); start = None
    if start is not None:
        bands.append((start / COVER_STEP, hi / COVER_STEP))
    bands = [b for b in bands if b[1] - b[0] >= GUTTER_MIN_PT]
    if not bands:
        return None
    bands.sort(key=lambda b: (-(b[1] - b[0]), b[0]))
    return (bands[0][0] + bands[0][1]) / 2


def zero_ink_strips(lines):
    """[(bottom, top)] -- every maximal horizontal strip that crosses the WHOLE
    page without a glyph in it, running heads and folios already dropped."""
    spans = sorted((ln["y0"], ln["y1"]) for ln in lines)
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(a[1], b[0]) for a, b in zip(merged, merged[1:])]


def section_heads(lines):
    """The LEFT column's clause headings, top to bottom, a wrapped title
    counted once.

    A band opens where a clause section opens in the left column, so the
    heading is the witness that a strip is a band boundary and not a wide
    paragraph gap. It has to be the CODE face: 2006 and 2008 set a
    clause-level supplementary head in the same words and the same weight
    ('Clause 6 Journal Advertisements' against the provisions' 'Clause 6
    Journal Advertising'), and only the face tells them apart. is_heading and
    the face are the same two tests parse_code_stream opens a clause on, and
    check_sequence then proves the set: the numbers run 1..N.
    """
    out, prev_head = [], False
    for ln in sorted((l for l in lines if l["side"] == "left"),
                     key=lambda l: -l["baseline"]):
        head = ln["face"] == "code" and is_heading(ln)
        if head and not prev_head:
            out.append(ln)
        prev_head = head
    return out


def order_by_bands(lines, cuts):
    """The page's lines in reading order, and `band` stamped on each: band by
    band down the page, and within a band the left column then the right."""
    cuts = sorted(cuts, reverse=True)
    for ln in lines:
        ln["band"] = sum(1 for c in cuts if ln["baseline"] < c)
    out = []
    for b in range(len(cuts) + 1):
        for side in ("left", "right"):
            out.extend(sorted((ln for ln in lines
                               if ln["band"] == b and ln["side"] == side),
                              key=lambda l: -l["baseline"]))
    return out


def _face_order(order):
    return tuple(tuple(id(ln) for ln in order if ln["face"] == f)
                 for f in ("code", "supp"))


def page_bands(lines):
    """(cuts, undecided) -- the y of each place the page's reading order
    restarts, and the headings where the geometry does not decide it.

    The rule, and every part of it is measured on the eleven documents:

      * A band boundary is a full-width zero-ink STRIP. Nothing else will do:
        the two columns of a band are not aligned -- on 2256 p39 the right
        column of band 2 starts 35pt above the left column's title, because
        the title carries space over it -- so the boundary is not a line drawn
        at the heading.
      * A strip is a boundary only where a clause section opens in the left
        column below it (section_heads). Strip size alone cannot decide it,
        and no threshold can: the two populations OVERLAP. 2253 p36's 22.9pt
        strip sits inside Clause 21's provisions and 2275 p36's 33.4pt one
        sits inside Clause 24's guidance, while the smallest real boundary
        here is 14.3pt and 2256 p39's is 30.9pt.
      * The boundary is the WIDEST strip between that heading and the one
        above it. On 2253 p21 the two candidates are 20.5pt and 22.9pt and the
        wider one is right: the narrower one has Clause 11.4's guidance
        beneath it, which is Clause 11's, not Clause 12's.
      * A boundary must CUT the left column -- left-column copy above it as
        well as below. 33 of the 206 headings have a strip above them with no
        left-column copy above THAT: the section simply starts at the top of
        the column and the right column above it is the SAME band's
        continuation (2256 p19's right column opens with Clause 10.1's second
        half). Cutting there would put that continuation before the text it
        continues. A further 47 have no strip above them at all.
      * Rivals that would read the page the same way do not compete: on 2253
        p21 either candidate gives both streams the same order, because the
        lines between them are all in the right column. Only a rival that
        changes the code or the supplementary order has to be beaten, and it
        has to be beaten by BAND_TIE_PT.

    What comes out, on the clause pages this build reads: 138 section starts
    get a boundary decision, 137 are cut and 1 is refused (BAND_UNDECIDED).
    Per document: 2001 6, 2003 6, 2006 7, 2008 12, 2011 14, 2012 14, second
    2012 13, addendum 0, 2014 17, 2015 25, 2016 23. Only 24 pages change a
    stream's order at all -- 2001, 2003, 2006 and 2008 contribute 31
    boundaries and NONE, because in those four editions the two columns ARE
    the two streams (left/right code lines 739/0 in 2001, 741/0 in 2003, 837/0
    in 2006, 1027/1 in 2008; no clause page of 2001 or 2003 puts both streams
    in one column), so nothing interleaves. The 6 order-changing pages in the
    six pre-2014 documents are 2011 p19+p32, 2012 p19+p32+p39 and the second
    2012 edition p19: exactly the six pages KNOWN_TRUNCATIONS was declared on,
    found independently by the terminal-punctuation test, and all six now read
    continuously.
    """
    strips = zero_ink_strips(lines)
    cuts, undecided = [], []
    limit = None
    for h in section_heads(lines):
        cands = [s for s in strips if s[0] >= h["y1"]
                 and (limit is None or s[1] <= limit)]
        limit = h["y0"]
        cands = [s for s in cands
                 if any(l["side"] == "left" and l["y0"] >= s[1] for l in lines)]
        if not cands:
            continue
        cands.sort(key=lambda s: -(s[1] - s[0]))
        pick, width = cands[0], cands[0][1] - cands[0][0]
        chosen = _face_order(order_by_bands(lines, cuts + [sum(pick) / 2]))
        rivals = [s for s in cands[1:]
                  if _face_order(order_by_bands(lines, cuts + [sum(s) / 2]))
                  != chosen]
        if rivals and width - (rivals[0][1] - rivals[0][0]) <= BAND_TIE_PT:
            undecided.append((h["text"].strip(), round(width, 2),
                              round(rivals[0][1] - rivals[0][0], 2)))
            continue
        cuts.append(sum(pick) / 2)
    return cuts, undecided


def page_lines(page, raw):
    """(lines in reading order, n_straddling_glyphs, n_forced_numerals,
    undecided_bands).

    Every line carries page, side, band, face and `full` (does it reach the
    measure).
    """
    width, _h = page.get_size()
    rows = group_rows(read_chars(page.get_textpage(), raw))
    rows = [r for r in rows if not is_running(make_line(r["chars"])["text"])]
    mid = gutter_centre(rows, width)
    if mid is None:
        mid = width / 2
    straddle = 0
    per_side = {"left": [], "right": []}
    # The column's measured text block. LEFTMOST start and RIGHTMOST end that
    # at least three body lines share: the mode would be the bullet indent on a
    # list page and would then clip the first letter of every unindented line.
    measure = {}
    for side in ("left", "right"):
        firsts, lasts = Counter(), Counter()
        for row in rows:
            ink = [c for c in row["ink"]
                   if ((c["x0"] + c["x1"]) / 2 < mid) == (side == "left")]
            if ink and ink[0]["size"] < HEADING_MIN_PT:
                firsts[round(ink[0]["x0"])] += 1
                lasts[round(ink[-1]["x1"])] += 1
        lo = sorted(x for x, n in firsts.items() if n >= 3)
        hi = sorted(x for x, n in lasts.items() if n >= 3)
        measure[side] = (lo[0], hi[-1]) if lo and hi else None
    for row in sorted(rows, key=lambda r: -r["baseline"]):
        for c in row["ink"]:
            if c["x0"] < mid < c["x1"]:
                straddle += 1
        for side in ("left", "right"):
            sel = [c for c in row["chars"]
                   if (((c["x0"] + c["x1"]) / 2 < mid) == (side == "left"))]
            sel = drop_furniture(sel, measure[side])
            ln = make_line(sel) if sel else None
            if ln is None or is_running(ln["text"]):
                continue
            ln["side"] = side
            per_side[side].append(ln)
    lines = per_side["left"] + per_side["right"]
    # The face is settled BEFORE the bands, because section_heads reads it: a
    # 2006 supplementary head is a clause heading in every respect but its
    # face. (This loop used to live in document_streams; it moved here whole,
    # and its count still travels back so the forced-numeral refusal is
    # unchanged.)
    forced = 0
    for ln in lines:
        ln["face"] = face_of(ln)
        if ln["size"] >= DISPLAY_HEADING_PT and CLAUSE_ALONE_RE.match(ln["text"].strip()):
            if ln["face"] != "code":
                forced += 1
            ln["face"] = "code"
    cuts, undecided = page_bands(lines)
    return order_by_bands(lines, cuts), straddle, forced, undecided


def drop_furniture(chars, measure):
    """Strip a folio that shares a baseline with body copy.

    In the 2006 and 2008 editions the page number sits ~12-17pt outside the
    text block -- left margin on verso pages, right margin on recto -- and on
    the SAME baseline as the first or last line of the column, so the whole-row
    filter (which matches a line that is only a number) never sees it. Before
    this, 2006 clause 1.2 read 'pharmaceutical companies 6 to national public
    organizations' and the 2008 supplementary information to 5.4 read 'if the
    information pro 13 vided'. Thirteen clause and supplementary texts carried
    a stray folio, each caught by verify/pdf_clause_texts.py against a second
    extractor.

    Only a run that is entirely OUTSIDE the column's measured text block, is
    all digits, and is no longer than a folio can be. Subclause numbers carry a
    dot ('16.4'), list markers carry brackets ('vii)', '(a)'), and neither
    starts outside the measure.
    """
    if measure is None:
        return chars
    left_edge, right_edge = measure
    # x-ordered on purpose: row["chars"] is in (-baseline, x) order and a
    # folio's baseline sits a fraction off the line it shares, which put it
    # anywhere in that order.
    ink = sorted((c for c in chars if not is_blank(c["ch"]) and not c["hyphen"]),
                 key=lambda c: c["x0"])
    if len(ink) < 2:
        return chars
    drop = set()
    for run in (_edge_run(ink, lambda c: c["x1"] <= left_edge - FURNITURE_PT),
                _edge_run(ink[::-1], lambda c: c["x0"] >= right_edge + FURNITURE_PT)):
        if (run and len(run) <= FURNITURE_MAX_GLYPHS and len(run) < len(ink)
                and "".join(c["ch"] for c in sorted(run, key=lambda c: c["x0"])).isdigit()
                and all(c["size"] < HEADING_MIN_PT for c in run)):
            drop |= {id(c) for c in run}
    if not drop:
        return chars
    return [c for c in chars if id(c) not in drop]


def _edge_run(ink, outside):
    run = []
    for c in ink:
        if not outside(c):
            break
        run.append(c)
    return run


WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]*[A-Za-z]|[A-Za-z]")


def vocabulary(streams, extra_paths=()):
    """Words attested WITHOUT crossing a line join. A line that ends in a
    line-break hyphen contributes everything but its last token, and the line
    after it everything but its first: those two fragments are the question,
    they cannot also be the answer."""
    vocab = Counter()
    for lines in streams:
        prev_broke = False
        for ln in lines:
            toks = WORD_RE.findall(ln["text"])
            if prev_broke and toks:
                toks = toks[1:]
            if ln["hyphen"] and toks:
                toks = toks[:-1]
            for w in toks:
                vocab[w.lower()] += 1
            prev_broke = bool(ln["hyphen"])
    for path in extra_paths:
        for row in common.read_jsonl(path):
            for w in WORD_RE.findall(json.dumps(row, ensure_ascii=False)):
                vocab[w.lower()] += 1
    return vocab


def decide_hyphens(streams, doc_vocab, corpus_vocab):
    """Set `hyphen_keep` on every line that ends in a line-break hyphen.

    pdfium's FPDFText_IsHyphen marks the glyph the compositor used to break a
    line, and these fonts emit a distinct character for it (U+0002, matching
    the flag 1:1 -- 396/396 in 2001, 401/401 in 2003, 506/506 in 2006, 588/588
    in 2008, 17/17 in 2011, 22/22 in 2012, 20/20 in the second 2012 edition).
    But the SAME glyph was used
    whether the compositor broke a word that has no hyphen ('avail-/able') or
    broke a compound at its own hyphen ('over-the-/counter'). Dropping all of
    them gave 'overthecounter', 'contraindications', 'upto-date' and
    'nonpromotional' -- the last of which made two 2012 edition comparisons
    diverge on nothing. So each of the 1,639 instances is decided against text
    that did NOT cross a line join, the document's own first and the whole Code
    corpus second: 1,513 joined, 126 written back as the compound's own hyphen,
    and 7 left to HYPHEN_DECIDED. The build refuses naming any pair neither
    the corpus nor that table can decide -- and it did on the two archived
    editions, which is how the fourth HYPHEN_DECIDED entry got there: the
    Code corpus attests neither 'ordinarily' nor 'ordinar-ily' off a line join,
    so 2001 and 2003 p26 stopped the build rather than let it pick.

    Returns (n_join, n_keep, undecided[(left, right)]).
    """
    n_join = n_keep = 0
    undecided = Counter()
    for lines in streams:
        for a, b in zip(lines, lines[1:]):
            if not a["hyphen"]:
                continue
            left = re.findall(r"[A-Za-z]+$", a["text"])
            right = re.findall(r"^[A-Za-z]+", b["text"])
            if not left or not right:
                a["hyphen_keep"] = False      # a digit or a bracket either side
                n_join += 1
                continue
            l, r = left[0], right[0]
            joined, hyphened = (l + r).lower(), (l + "-" + r).lower()
            keep = None
            for vocab in (doc_vocab, corpus_vocab):
                if vocab[hyphened] and not vocab[joined]:
                    keep = True
                elif vocab[joined] and not vocab[hyphened]:
                    keep = False
                elif vocab[joined] and vocab[hyphened]:
                    keep = vocab[hyphened] > vocab[joined]
                if keep is not None:
                    break
            if keep is None and corpus_vocab[l.lower()] and corpus_vocab[r.lower()]:
                # Neither compound is attested, but both halves are whole words
                # in their own right: the break is AT a hyphen, not inside a
                # word. 'adverse-/reaction', 'up-/to-date', 'benefit-/risk'.
                keep = True
            if keep is None:
                keep = HYPHEN_DECIDED.get((l.lower(), r.lower()))
            if keep is None:
                undecided[(l, r)] += 1
                continue
            a["hyphen_keep"] = keep
            n_keep += bool(keep)
            n_join += not keep
    return n_join, n_keep, undecided


def face_of(line):
    """'supp' for the italic stream, 'code' for the roman one, None for a line
    of symbols only. The display-numeral exception is applied by the caller."""
    if line["italic_ink"] == line["roman_ink"] == 0:
        return None
    return "supp" if line["italic_ink"] > line["roman_ink"] else "code"


def document_streams(path, raw, pdfium):
    """(code_lines, supp_lines, (first_page, last_page), forced, refusals,
    undecided_bands).

    Only the clause pages are read: the run of pages, starting at the first one
    that carries both a clause heading and a numbered subclause, over which no
    glyph crosses the gutter. The front matter and the Constitution are
    single-column and stop it.
    """
    doc = pdfium.PdfDocument(str(path))
    pages, straddlers, bands_undecided = {}, {}, {}
    # the forced display numerals decide which pages count as clause pages, and
    # page_lines settles the face (and so the numerals) before it bands a page
    forced = 0
    for pi in range(len(doc)):
        lines, straddle, forced_here, undecided = page_lines(doc[pi], raw)
        for ln in lines:
            ln["page"] = pi + 1
        pages[pi + 1] = lines
        straddlers[pi + 1] = straddle
        forced += forced_here
        if undecided:
            bands_undecided[pi + 1] = undecided

    anchor = None
    for p in sorted(pages):
        if straddlers[p]:
            continue
        code_lines = [ln for ln in pages[p] if ln["face"] == "code"]
        if (any(is_heading(ln) for ln in code_lines)
                and any(SUB_RE.match(ln["text"].strip()) for ln in code_lines)):
            anchor = p
            break
    if anchor is None:
        return [], [], (None, None), forced, [
            "no page carries both a clause heading and a numbered subclause"], {}
    lo = hi = anchor
    while hi + 1 <= len(doc) and straddlers[hi + 1] == 0:
        hi += 1

    code, supp = [], []
    for p in range(lo, hi + 1):
        # per (page, side, face) measure: the two streams are set to different
        # measures in the 2011/2012 editions (286.4 vs 283.4 in the addendum)
        marg = defaultdict(float)
        for ln in pages[p]:
            marg[(ln["side"], ln["face"])] = max(marg[(ln["side"], ln["face"])], ln["x1"])
        for ln in pages[p]:
            ln["full"] = ln["hyphen"] or ln["x1"] >= marg[(ln["side"], ln["face"])] - FULL_EPS_PT
            if ln["face"] == "code":
                code.append(ln)
            elif ln["face"] == "supp":
                supp.append(ln)
    # only the clause pages are read, so only their undecided bands are ours
    return code, supp, (lo, hi), forced, [], {p: u for p, u in
                                             bands_undecided.items()
                                             if lo <= p <= hi}


def is_heading(line):
    """A clause heading in any of the three forms this corpus uses."""
    t = line["text"].strip()
    if line["size"] >= DISPLAY_HEADING_PT and CLAUSE_ALONE_RE.match(t):
        return True                      # 2012 2nd ed: 'Clause 5' at 24pt
    if line["size"] >= HEADING_MIN_PT and line["bold"]:
        return True                      # 2011/2012: the title alone at 12pt bold
    return bool(line["bold"] and CLAUSE_INLINE_RE.match(t))   # 2006/2008, 9.5pt bold


def line_pitch(lines):
    """(pitch, gaps) -- the modal baseline gap between two body lines in the
    same column, and the full distribution for the ambiguity check."""
    gaps = Counter()
    for a, b in zip(lines, lines[1:]):
        if (a["page"] == b["page"] and a["side"] == b["side"]
                and a["band"] == b["band"]
                and a["size"] < HEADING_MIN_PT and b["size"] < HEADING_MIN_PT):
            gaps[round(a["baseline"] - b["baseline"], 1)] += 1
    if not gaps:
        return None, gaps
    return gaps.most_common(1)[0][0], gaps


def pitch_band_clear(pitch, gaps):
    """The paragraph threshold has to sit in an EMPTY band, the way
    l1/build_pdf.py's gutter thresholds do. Measured on all eight editions:
    body pitch is 12.0 (2001/2003/2006/2008/2011/2012 1st) or 11.4 (2012 2nd,
    addendum) and the next gap up is 14.0 -- nothing lands between 1.02x and
    1.15x."""
    lo, hi = pitch * PARA_GAP_AMBIGUOUS, pitch * PARA_GAP_RATIO
    return sorted(g for g in gaps if lo < g <= hi)


def starts_paragraph(line, prev, pitch):
    """Extra leading is the signal, and it is unambiguous here: paragraph
    breaks are 1.17x-1.5x the pitch, continuations are exactly 1x. Across a
    page or column break there is no gap to read, so a previous line that did
    not reach the measure is what ends the paragraph instead.

    A BAND break is not a column break: text flows out of a band's last column
    into the next page, never into the band below it, because the band below
    is a new clause section (page_bands). So a band change always ends the
    paragraph. Measured: 142 same-page band transitions inside the two
    streams (2001 and 2003 contribute 10 each and none of the four below), 4
    of which the column rule would read as continuations (2006 p20
    + p24, 2008 p20 + p35, all four with a full line before the break) -- and
    on all four the line AFTER the break is a bold head, which opens a block
    or a clause before the paragraph rule is consulted. Dropping this branch
    leaves pdf_clauses.jsonl byte-identical; it is here to say which reading
    is meant, not to move a value.
    """
    if prev is None:
        return True
    if prev["size"] >= HEADING_MIN_PT or line["size"] >= HEADING_MIN_PT:
        return True
    if prev["page"] == line["page"] and prev["band"] != line["band"]:
        return True
    if prev["page"] != line["page"] or prev["side"] != line["side"]:
        return not prev["full"]
    return (prev["baseline"] - line["baseline"]) > pitch * PARA_GAP_RATIO


# --- grammar ---------------------------------------------------------------

def flow(lines):
    """Join lines into one string.

    Four ways a line can end. A line-break hyphen (pdfium's own flag, decided
    per instance by decide_hyphens) either closes the gap or is written back as
    a real hyphen. A line ending in a REAL hyphen closes the gap too and keeps
    it -- a compound broken at its own hyphen ('non-' / 'promotional'); a space
    there would print 'non- promotional', which is what the first pass did and
    what made two of the 2012 edition comparisons diverge on nothing. A line
    ending in a SOLIDUS closes the gap when decide_slashes says the corpus sets
    that compound closed ('activities/' + 'materials'), and otherwise takes the
    word space. Otherwise the lines are separated by a word space."""
    text = ""
    join_next = None
    for ln in lines:
        t = ln["text"].strip()
        if not t:
            continue
        if not text:
            text = t
        elif join_next is not None:
            text += join_next + t
        else:
            text += " " + t
        if ln["hyphen"]:
            join_next = "-" if ln.get("hyphen_keep") else ""
        elif t.endswith("-"):
            join_next = ""
        elif t.endswith("/"):
            join_next = "" if ln.get("slash_join") else None
        else:
            join_next = None
    return text


SLASH_RE = re.compile(r"[A-Za-z]+/ ?[A-Za-z]+")


def slash_vocabulary(streams, extra_paths=()):
    """(closed, spaced): how the corpus sets each 'a/b' compound, counted only
    where it does NOT cross a line join -- the same discipline vocabulary()
    uses for hyphens, and for the same reason."""
    closed, spaced = Counter(), Counter()

    def add(text):
        for m in SLASH_RE.finditer(text or ""):
            left, right = m.group(0).split("/")
            key = (left.strip().lower(), right.strip().lower())
            (spaced if right.startswith(" ") else closed)[key] += 1

    for lines in streams:
        for ln in lines:
            add(ln["text"])
    for path in extra_paths:
        for row in common.read_jsonl(path):
            add(json.dumps(row, ensure_ascii=False))
    return closed, spaced


def decide_slashes(streams, doc_vocab, corpus_vocab):
    """Set `slash_join` on every line the compositor broke at a solidus.

    'activities/' + 'materials' has to close up and 'Section 3/' + 'Section 4'
    would not, so the compound is decided against text that did NOT cross a
    line join -- the document's own first, the whole Code corpus second.
    Eleven instances across the eleven documents, all eleven decided closed by
    clauses.jsonl (counts in SLASH_DECIDED's comment). Returns
    (n_close, n_space, undecided)."""
    n_close = n_space = 0
    undecided = Counter()
    for lines in streams:
        for a, b in zip(lines, lines[1:]):
            if not a["text"].rstrip().endswith("/"):
                continue
            left = re.findall(r"([A-Za-z]+)/$", a["text"].rstrip())
            right = re.findall(r"^[A-Za-z]+", b["text"].strip())
            if not left or not right:
                a["slash_join"] = False       # a digit or a bracket either side
                n_space += 1
                continue
            key = (left[0].lower(), right[0].lower())
            keep = None
            for closed, spaced in (doc_vocab, corpus_vocab):
                if closed[key] and not spaced[key]:
                    keep = True
                elif spaced[key] and not closed[key]:
                    keep = False
                elif closed[key] and spaced[key]:
                    keep = closed[key] > spaced[key]
                if keep is not None:
                    break
            if keep is None:
                keep = SLASH_DECIDED.get(key)
            if keep is None:
                undecided[key] += 1
                continue
            a["slash_join"] = keep
            n_close += bool(keep)
            n_space += not keep
    return n_close, n_space, undecided


def strip_bullet(text):
    t = text.lstrip()
    while t and t[0] in BULLET_CHARS:
        t = t[1:].lstrip()
    return t


def resumes_at_column_break(line, prev, cur_sub, m_sub):
    """True where a column or page break leaves starts_paragraph nothing to
    read and the SUBCLAUSE NUMBER decides instead.

    Across a column break there is no leading, so starts_paragraph falls back
    to 'did the previous column's last line reach the measure'. That fallback
    is silent on the one case where a paragraph ENDS on a full last line at the
    foot of the column: 2001 and 2003 p14 open with '7.9 Information and claims
    about side-effects must' under a p13 whose last line, 'are relevant to the
    claims or comparisons being made.', measures x1 284.08 against a 286.25
    measure -- full by 2.17pt, and the paragraph is over. Read as a
    continuation, 7.9's five lines were appended to 7.8 and the edition came
    out with subclauses 7.1-7.8, 7.10, 7.11; check_sequence refused it, which
    is how this was found.

    The number is the evidence the leading cannot give, and the slot is small
    enough to enumerate. Over the eleven documents there are 39 code-stream
    lines that match SUB_RE with a forward-moving number at a column, page or
    band start, and ALL 39 open a printed subclause -- every one is its
    clause's immediate next minor (4.1->4.2, 9.9->9.10, 27.7->27.8, 7.8->7.9).
    Only 5 lines in the eleven documents match SUB_RE with a forward-moving
    number that the paragraph rule reads as a continuation, and the two tests
    part them cleanly: two are the 7.9 above (2001 and 2003 p14), and the other
    three are the same wrapped cross-reference set mid-column -- '...as
    described in Clause / 18.4 and paragraph 8 of its supplementary...' on 2259
    and 2274 p21, '/ 19.1 and paragraph 8...' on 2275 p21 -- which are neither
    at a column start nor an immediate next minor (14.3 -> 18.4). Both
    conditions are required, so either one alone would still refuse them.

    Nothing else moves: on the nine documents parsed before 2001 and 2003
    arrived, pdf_clauses.jsonl is byte-identical with this branch and without
    it. check_sequence stays the guard -- a subclause opened here that the
    printed edition does not carry shows up as a hole in 1..K.
    """
    if prev is None:
        return False
    # a same-page band change is already a paragraph start (starts_paragraph),
    # so the only way here is the column/page branch and its `full` fallback
    if prev["page"] == line["page"] and prev["side"] == line["side"]:
        return False
    return (int(m_sub.group(1)), int(m_sub.group(2))) == (cur_sub[0], cur_sub[1] + 1)


def parse_code_stream(lines, pitch):
    """[{number, title, paras: [(subnumber|None, text)], pages, numbered_by}].

    Three heading forms, all present in this corpus:
      * 2006, 2008        'Clause 1 Scope of the Code and Definition of' in
                          bold 9.5pt, the title inline and wrapping onto
                          further bold lines.
      * 2012 second ed,   'Clause 5' as a 24pt display numeral, the title
        addendum          ('Abbreviated Advertisements') beneath it in bold 12pt.
      * 2011, 2012 first  the title ALONE in bold 12pt -- those two files carry
        ed                no numeral in their text layer at all (the display
                          figure is drawn as paths), so the number is taken
                          from the running sequence and then CHECKED against
                          the first subclause the clause contains.
    """
    clauses = []
    cur = None
    cur_sub = (0, 0)
    in_title = False
    para = None
    prev = None

    def close_para():
        nonlocal para
        if para is not None and cur is not None:
            txt = flow(para["lines"])
            if para["bullet"]:
                txt = strip_bullet(txt)
            if txt:
                cur["paras"].append((para["sub"], txt))
        para = None

    def open_clause(number, title, how, line):
        nonlocal cur, cur_sub, in_title
        close_para()
        cur = {"number": number, "title": title, "paras": [],
               "pages": [line["page"]], "numbered_by": how}
        clauses.append(cur)
        cur_sub = (int(number), 0)
        in_title = True

    for ln in lines:
        t = ln["text"].strip()
        big = ln["size"] >= HEADING_MIN_PT
        m_alone = CLAUSE_ALONE_RE.match(t)
        m_inline = CLAUSE_INLINE_RE.match(t) if ln["bold"] else None

        if big and m_alone:
            open_clause(m_alone.group(1), "", "display numeral", ln)
            prev = ln
            continue
        if ln["bold"] and not big and m_inline:
            open_clause(m_inline.group(1), m_inline.group(2).strip(),
                        "inline heading", ln)
            prev = ln
            continue
        if ln["bold"] and (big or (in_title and cur is not None)):
            if in_title and cur is not None:
                cur["title"] = (cur["title"] + " " + t).strip()
                cur["pages"].append(ln["page"])
            else:
                nxt = str(int(clauses[-1]["number"]) + 1) if clauses else "1"
                open_clause(nxt, t, "sequence", ln)
            prev = ln
            continue
        in_title = False
        if cur is None:
            prev = ln            # front matter ahead of the first heading
            continue
        cur["pages"].append(ln["page"])

        bullet = bool(t) and t[0] in BULLET_CHARS
        new_para = starts_paragraph(ln, prev, pitch) or bullet
        m_sub = SUB_RE.match(t)
        if m_sub and not new_para and resumes_at_column_break(ln, prev, cur_sub,
                                                             m_sub):
            new_para = True
        # A subclause STARTS only at a paragraph start whose number moves
        # forward. Both halves earn their keep: '16.4. They must be entered...'
        # continues 16.3 mid-paragraph (no extra leading) and '24.1 and 24.2
        # above which is provided...' continues 24.3 (number goes backwards).
        if m_sub and new_para and (int(m_sub.group(1)), int(m_sub.group(2))) > cur_sub:
            close_para()
            cur_sub = (int(m_sub.group(1)), int(m_sub.group(2)))
            para = {"sub": "%d.%d" % cur_sub, "lines": [ln], "bullet": False}
        elif new_para or para is None:
            close_para()
            para = {"sub": ("%d.%d" % cur_sub) if cur_sub[1] else None,
                    "lines": [ln], "bullet": bullet}
        else:
            para["lines"].append(ln)
        prev = ln
    close_para()
    for c in clauses:
        c["pages"] = (min(c["pages"]), max(c["pages"]))
    return clauses


def parse_supp_stream(lines, pitch):
    """[{refs, heading, text, pages}] -- one block per bold-italic head.

    'Clause 24 Supplementary Information' is the section divider the 2011 and
    2012 editions print above each clause's guidance; it names no subclause and
    opens no block, it only closes the one before it.
    """
    blocks = []
    cur = None
    para = None
    prev = None

    def close_para():
        nonlocal para
        if para is not None and cur is not None:
            txt = flow(para["lines"])
            if para["bullet"]:
                txt = strip_bullet(txt)
            if txt:
                cur["paras"].append(txt)
        para = None

    for ln in lines:
        t = ln["text"].strip()
        if SUPP_DIVIDER_RE.match(t):
            close_para()
            cur = None
            prev = ln
            continue
        m = SUPP_HEAD_RE.match(t) if ln["bold"] else None
        if m:
            close_para()
            refs = [m.group(1)] + SUPP_REF_RE.findall(m.group(2) or "")
            cur = {"refs": refs, "heading": t, "paras": [], "pages": [ln["page"]],
                   "_open": True}
            blocks.append(cur)
            prev = ln
            continue
        if cur is not None and cur.get("_open") and ln["bold"]:
            cur["heading"] += " " + t          # the head wrapped onto a second line
            cur["pages"].append(ln["page"])
            prev = ln
            continue
        if cur is None:
            prev = ln
            continue
        cur.pop("_open", None)
        cur["pages"].append(ln["page"])
        bullet = bool(t) and t[0] in BULLET_CHARS
        if para is None or starts_paragraph(ln, prev, pitch) or bullet:
            close_para()
            para = {"lines": [ln], "bullet": bullet}
        else:
            para["lines"].append(ln)
        prev = ln
    close_para()
    for b in blocks:
        b.pop("_open", None)
        b["text"] = "\n".join(b["paras"])
        b["pages"] = (min(b["pages"]), max(b["pages"]))
    return blocks


def mark_truncated(blocks, band_pages=()):
    """Flag the reading-order casualties. Returns (truncated_headings, pages).

    A supplementary block whose text ends without terminal punctuation was cut
    by the reading order: the printed paragraph continues at the top of
    another column and other blocks were read in between. This is the guard
    the page-band repair left armed -- it is what found R23 in the first
    place, and it is what would find a band the rule misses. The cut is at the
    block's LAST page, and every block overlapping that page is marked
    `attachment_suspect`: it is the set that may have RECEIVED the orphan.

    `band_pages` are pages whose band boundary the geometry did not decide
    (BAND_UNDECIDED). Nothing there is known to be wrong, but nothing there is
    known to be right either, so their blocks are marked the same way."""
    pages, heads = set(), []
    for b in blocks:
        t = (b["text"] or "").strip()
        if t and not TERMINAL_RE.search(t):
            b["truncated"] = True
            heads.append((b["heading"], b["refs"][0].split(".")[0]))
            pages.add(b["pages"][1])
    suspect = pages | set(band_pages)
    for b in blocks:
        lo, hi = b["pages"]
        if any(lo <= p <= hi for p in suspect):
            b["attachment_suspect"] = True
    return heads, sorted(pages)


def assemble(clauses, supp_blocks, meta):
    """clauses.jsonl's row shape, plus PDF provenance."""
    by_number = {}
    rows = []
    for c in clauses:
        subs = []
        order = []
        for sub, txt in c["paras"]:
            if sub is None:
                if subs:
                    subs[-1]["_paras"].append(txt)
                else:
                    subs.append({"number": c["number"], "_paras": [txt],
                                 "supplementary_information": []})
                    order.append(c["number"])
                continue
            if sub not in [s["number"] for s in subs]:
                subs.append({"number": sub, "_paras": [txt],
                             "supplementary_information": []})
                order.append(sub)
            else:
                next(s for s in subs if s["number"] == sub)["_paras"].append(txt)
        for s in subs:
            s["text"] = "\n".join(s["_paras"])
            del s["_paras"]
        row = {
            "code_year": meta["code_year"],
            "clause_number": c["number"],
            "clause_title": c["title"],
            "text": "\n\n".join(s["text"] for s in subs),
            "subclause_numbers": order,
            "subclauses": subs,
            "general_supplementary": None,
            "attachment_suspect": [],
        }
        row.update(meta["provenance"])
        row["page_first"], row["page_last"] = c["pages"]
        rows.append(row)
        by_number[c["number"]] = row

    general = defaultdict(list)
    unattached = []
    for b in supp_blocks:
        ref = b["refs"][0]
        parent = ref.split(".")[0]
        row = by_number.get(parent)
        if row is None:
            unattached.append((b["heading"], "no clause %s in this edition" % parent))
            continue
        entry = {"heading": b["heading"], "text": b["text"]}
        if b.get("attachment_suspect"):
            entry["attachment_suspect"] = True
            # the reference this block hangs off, not the clause: only the
            # subclause whose guidance may have gained or lost a paragraph
            if ref not in row["attachment_suspect"]:
                row["attachment_suspect"].append(ref)
        target = next((s for s in row["subclauses"] if s["number"] == ref), None)
        if target is not None:
            target["supplementary_information"].append(entry)
        elif "." in ref:
            unattached.append((b["heading"],
                               "clause %s has no subclause %s" % (parent, ref)))
        else:
            # A clause-level head on a subdivided clause. clauses.jsonl parks
            # these in general_supplementary, where generate.py's renderer
            # drops them -- reproduced rather than 'improved', so a pre-2014
            # item and a 2016 item show the same shape of text.
            general[parent].append(entry)
        row["page_last"] = max(row["page_last"], b["pages"][1])
    for num, entries in general.items():
        by_number[num]["general_supplementary"] = entries
    return rows, unattached


# --- checks ----------------------------------------------------------------

def check_sequence(clauses, whole_edition):
    """Clause numbers are exactly 1..N; subclause numbers are contiguous from
    1 within each clause and carry their own clause's major number.

    The major-number test is what makes the 2011/2012 'numbered by sequence'
    heading safe: those two files print no clause numeral, so the number comes
    from the running count and is then confirmed by the clause's own body.
    Only Clause 2 has no subclause to confirm it, and it sits between two that
    do."""
    bad = []
    nums = [int(c["number"]) for c in clauses]
    if whole_edition and nums != list(range(1, len(nums) + 1)):
        bad.append("clause numbers are %s, not 1..%d" % (nums, len(nums)))
    for a, b in zip(nums, nums[1:]):
        if b <= a:
            bad.append("clause numbers go %d -> %d" % (a, b))
    for c in clauses:
        got = [s for s in c["subclause_numbers"] if "." in s]
        minors = [int(s.split(".")[1]) for s in got]
        if minors and minors != list(range(1, len(minors) + 1)):
            bad.append("clause %s subclauses are %s, not 1..%d"
                       % (c["number"], got, len(minors)))
        if any(s.split(".")[0] != c["number"] for s in got):
            bad.append("clause %s carries foreign subclauses %s" % (c["number"], got))
    return bad


def _supp_text(entries):
    parts = []
    for e in entries or []:
        h = (e.get("heading") or "").strip()
        t = (e.get("text") or "").strip()
        if t:
            parts.append("%s: %s" % (h, t) if h else t)
    return "\n".join(parts)


def rendered(row, clause):
    """EXACTLY what bench/generate.py's clause_text_for would show for this
    reference, supplementary information included.

    Rendering, not the raw field, is what the 2012 edition comparison has to
    be run on: a subclause whose provisions are word-identical across the two
    editions can still carry different guidance beneath it, and the item would
    then show different text depending on which edition we picked.

    Wave C: the silent 6,000-character truncation both returns used to carry is
    gone here for the same reason it is gone in bench/generate.py -- it cut 38
    legitimate whole-clause renderings (6,605-10,742 chars) mid-word and told
    nobody. This function's job is to be the same rendering the bench shows, so
    it must not cap where the bench does not. There is no ceiling check here:
    the bench holds the one that refuses, and duplicating it would be a second
    copy to drift."""
    if row is None:
        return None
    title = row.get("clause_title") or ""
    if "." in str(clause):
        sub = next((s for s in row["subclauses"] if s["number"] == str(clause)), None)
        if sub is None or not (sub.get("text") or "").strip():
            return None
        out = "Clause %s (%s):\n%s" % (clause, title, sub["text"].strip())
        supp = _supp_text(sub.get("supplementary_information"))
        if supp:
            out += "\n\nSupplementary information:\n" + supp
        return out
    body = (row.get("text") or "").strip()
    if not body:
        return None
    return "Clause %s (%s):\n%s" % (clause, title, body)


EDITION_STATUSES = {
    "assigned",           # an edition is named in `edition`
    "undecidable",        # received AND conduct inside the 1 Jul-31 Oct 2012 transition
    "contradictory",      # the parties and the Panel's own wording disagree
    "pre_dates_editions",  # tagged 2012 but completed or received before either
}


def load_edition_assignments():
    """case_number -> row, from data/code/edition_assignments.jsonl.

    A PROMPT-RENDERING choice, not a canonical-value repair: it decides which
    printed edition's words an item shows, and nothing in data/l2 depends on
    it. The governing edition is arguably a case-level fact and this file may
    later migrate into L2 alongside `code_year`; it lives here for now because
    only the Code-text layer consumes it, and because the evidence behind it is
    an audit of the reports, not of the L2 slots.

    The build refuses on a row it cannot read rather than skipping it: a case
    with no row and a case with an unreadable row must not look the same."""
    rows, bad = {}, []
    if not ASSIGNMENTS.exists():
        return rows
    for row in common.read_jsonl(ASSIGNMENTS):
        num = row.get("case_number")
        status = row.get("status")
        edition = row.get("edition")
        if not num:
            bad.append((str(row)[:60], "no case_number"))
            continue
        if num in rows:
            bad.append((num, "listed twice"))
        if status not in EDITION_STATUSES:
            bad.append((num, "status %r is not one of %s"
                        % (status, sorted(EDITION_STATUSES))))
        elif status == "assigned":
            if edition not in set(EDITION_TOKENS.values()):
                bad.append((num, "edition %r is not one of %s"
                            % (edition, sorted(set(EDITION_TOKENS.values())))))
            if row.get("evidence_level") not in (1, 2, 3):
                bad.append((num, "evidence_level %r is not 1, 2 or 3"
                            % row.get("evidence_level")))
            if not (row.get("receipt") or "").strip():
                bad.append((num, "no receipt quote"))
        elif edition is not None:
            bad.append((num, "status %r must not name an edition" % status))
        rows[num] = row
    if bad:
        print("REFUSING: %d row(s) in %s cannot be read:"
              % (len(bad), ASSIGNMENTS.name), file=sys.stderr)
        for num, why in bad:
            print("    %s: %s" % (num, why), file=sys.stderr)
        sys.exit(1)
    return rows


def edition_rendered(entry, clause):
    """rendered(), applied to one parked per-edition entry from `by_edition`."""
    if "subclause" in entry:
        row = {"clause_title": entry.get("clause_title"),
               "subclauses": [entry["subclause"]]}
    else:
        row = {"clause_title": entry.get("clause_title"), "text": entry.get("text")}
    return rendered(row, clause)


def serve_edition(row, clause, assignment):
    """Which edition's rendering a case gets for a reference the editions
    dispute. (None, edition_token) when it is servable; (reason, detail) when
    it is not.

    Mirrored -- deliberately, not shared -- by bench/generate.py's own
    edition_text_for, and checked item by item against the bank by
    verify/pdf_clause_texts.py on a third implementation. Two of the four
    refusals are worth naming here:

      * `builder_block_attachment` -- the assigned edition's rendering of this
        reference sits on a page where THIS parser demonstrably mis-attached a
        paragraph (KNOWN_TRUNCATIONS). Serving it would put the Code's words
        under the wrong clause, which is worse than serving none.
      * `clause_withheld_for_case` -- the case's own report says this clause
        number belongs to a different Code's numbering (AUTH/3115/11/18 mixes
        2012 and 2016 numbers in one outcome list), so the 2012 text under that
        number is about something else.
    """
    if assignment is None:
        return ("no_edition_assignment",
                "no row in %s decides which 2012 edition governs this case"
                % ASSIGNMENTS.name)
    status = assignment.get("status")
    if status != "assigned":
        return ("edition_" + status,
                assignment.get("status_detail") or assignment.get("receipt") or "")
    withheld = assignment.get("withheld_clauses") or {}
    if clause in withheld:
        return ("clause_withheld_for_case", withheld[clause])
    entries = ((row or {}).get("by_edition") or {}).get(clause) or []
    want = assignment["edition"]
    entry = next((e for e in entries if e["edition"] == want), None)
    if entry is None and want == "second_2012_addendum":
        # "the Second 2012 Edition (amended) Code" (AUTH/2705/3/14) is the
        # second edition AS AMENDED: the addendum's own cover says "Changes to
        # Clause 16 ... were agreed on 25 April 2013", so every clause it does
        # not touch is still the second edition's.
        entry = next((e for e in entries if e["edition"] == "second_2012"), None)
    if entry is None:
        return ("absent_from_assigned_edition",
                "the %s edition carries no %s" % (want, clause))
    if entry.get("attachment_suspect"):
        return ("builder_block_attachment",
                "the %s edition's %s draws supplementary information from a page "
                "where this parser cut a block mid-sentence, so its rendering may "
                "carry or be missing a paragraph" % (assignment["edition"], clause))
    if not edition_rendered(entry, clause):
        return ("empty_in_assigned_edition",
                "the %s edition's %s came out empty" % (assignment["edition"], clause))
    return (None, assignment["edition"])


def referenced_cases(path, years):
    """(code_year, clause) -> {case_number: n_items}, for the years given.

    The case breakdown is load-bearing now that a 2012 reference can resolve
    for one case and not another: the coverage refusal has to account for
    ITEMS, and after the per-case edition assignment a pair is no longer all
    text or all null."""
    pairs = defaultdict(Counter)
    if not path.exists():
        return pairs
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            ref = (row["inputs"] or {}).get("clause_ref") or {}
            if ref.get("code_year") in years and ref.get("clause") is not None:
                pairs[(ref["code_year"], str(ref["clause"]))][row["case_number"]] += 1
    return pairs


def referenced_pairs(path, years=PDF_YEARS):
    """(code_year, clause) -> n_items, for the years given."""
    return Counter({k: sum(v.values())
                    for k, v in referenced_cases(path, years).items()})


def squeeze(text):
    return re.sub(r"\s+", " ", text or "").strip()


def nowhite(text):
    return re.sub(r"\s+", "", text or "")


def agreement_tier(texts):
    """How far two or more editions' renderings have to be normalised before
    they are the same string: 'identical', 'whitespace_runs' (a break the
    extractor placed in one file and not the other), 'whitespace_only' (a word
    space present in one and absent in the other), or None.

    The third tier exists because the SOURCE GLYPHS differ, not our reading of
    them, and the pages were opened to check it (R22, 2026-08-09):

      * the first 2012 edition PRINTS 'vii)the information provided should not
        include mock up drafts' -- p10, right column, and the items i)-vi) and
        viii) around it all carry their space. So do 2006 and 2008. The second
        2012 edition sets 'vii) the'. There is no space glyph in the first
        edition's text layer and the printed gap is 1.9pt where a word space
        advances 2.4pt.
      * the second 2012 edition's text layer carries a real space after the
        hyphen of 'non-promotional' (p20) and 'non-proprietary' (p36), and the
        line is justified, so the compositor stretched it: the page reads
        'non- promotional'. The first edition sets the same words closed up.

    No extraction can make those two files agree, so the comparison is what has
    to change. Exactly one of the recorded 'divergences' WAS ours -- a line
    broken at a solidus -- and that one is fixed in flow(), not normalised
    away."""
    vals = [t for _f, t in texts]
    if len(set(vals)) == 1:
        return "identical"
    if len({squeeze(t) for t in vals}) == 1:
        return "whitespace_runs"
    if len({nowhite(t) for t in vals}) == 1:
        return "whitespace_only"
    return None


def word_diff(a, b, limit=3):
    """A short, quotable account of how two renderings differ."""
    out = []
    aw, bw = (a or "").split(), (b or "").split()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=aw, b=bw,
                                                       autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        out.append("%s: %r -> %r" % (tag, " ".join(aw[i1:i2])[:120],
                                     " ".join(bw[j1:j2])[:120]))
        if len(out) >= limit:
            break
    return "; ".join(out)


def reconcile(rows_by_file, order, references):
    """Reduce several editions of ONE code year to one row per clause.

    Returns (rows, divergences). A subclause is kept where every edition that
    carries the clause renders it the same TEXT -- identically, or after
    normalising whitespace (agreement_tier: the two files' word spaces and line
    breaks genuinely differ and no extraction can make them agree). The
    verbatim string kept is the LAST edition's in `order`, never a merge:
    nothing here synthesises a string no printed edition carries. `editions_agree`
    records the reference, the tier and the file the string came from, and
    `editions_compared` carries every source PDF, so a reader can see at a
    glance that the editions agreed and on what basis.

    Where the editions really differ, the reference is dropped from the
    reconciled row and each edition's own rendering is parked in `by_edition`,
    for bench/generate.py to serve per case from
    data/code/edition_assignments.jsonl. This file still decides nothing about
    which edition governs: it publishes both, named, and lets the case-level
    evidence choose. The corpus says plainly that no single edition governs the
    year. AUTH/2528/8/12: "Genzyme had mistakenly referred to the revised
    definition of promotion in Clause 1.2 Second 2012 edition of the Code.
    That edition of the Code, however, did not come into operation until
    1 July 2012 (with a transitional period until 31 October 2012)."
    AUTH/2648/10/13 rules on "Clause 16.3 of the Addendum to the Second 2012
    Edition". 28 of the 180 cases the corpus marks code_year 2012 name the
    Second Edition; the applicable-Code-year field itself distinguishes none
    of them.
    """
    primary = rows_by_file[order[0]]
    others = [rows_by_file[f] for f in order[1:]]
    rows, diverged = [], []

    def row_for(table, number):
        return next((r for r in table if r["clause_number"] == number), None)

    def suspect(table_row, ref):
        return ref in (table_row.get("attachment_suspect") or [])

    for base in primary:
        num = base["clause_number"]
        cands = [(order[0], base)] + [(f, row_for(t, num))
                                      for f, t in zip(order[1:], others)]
        cands = [(f, r) for f, r in cands if r is not None]
        latest_file, latest_row = cands[-1]
        row = json.loads(json.dumps(base))       # deep copy; base stays intact
        row["editions_compared"] = [
            {"source_pdf": r["source_pdf"], "sha256_of_source": r["sha256_of_source"],
             "page_first": r["page_first"], "page_last": r["page_last"],
             "edition": EDITION_TOKENS.get(f)}
            for f, r in cands]
        row["divergent_subclauses"] = []
        row["editions_agree"] = []
        row["by_edition"] = {}
        row["attachment_suspect"] = []

        def park(ref, kind):
            """Every edition's own rendering of a reference they disagree on."""
            row["by_edition"][ref] = []
            for f, r in cands:
                entry = {"edition": EDITION_TOKENS.get(f), "source_pdf": f,
                         "sha256_of_source": r["sha256_of_source"],
                         "page_first": r["page_first"], "page_last": r["page_last"],
                         "clause_title": r.get("clause_title") or "",
                         "attachment_suspect": suspect(r, ref)}
                if kind == "sub":
                    sub = next((s for s in r["subclauses"]
                                if s["number"] == ref), None)
                    if sub is None:
                        continue
                    entry["subclause"] = json.loads(json.dumps(sub))
                else:
                    entry["text"] = r.get("text")
                row["by_edition"][ref].append(entry)

        keep = []
        for sub in base["subclauses"]:
            ref = sub["number"]
            texts = [(f, rendered(r, ref)) for f, r in cands]
            tier = agreement_tier(texts)
            if tier is not None:
                kept = next(s for s in latest_row["subclauses"]
                            if s["number"] == ref)
                keep.append(json.loads(json.dumps(kept)))
                if tier != "identical":
                    row["editions_agree"].append(
                        {"ref": ref, "tier": tier, "kept_from": latest_file,
                         "difference": word_diff(texts[0][1], texts[-1][1])})
                if suspect(latest_row, ref):
                    row["attachment_suspect"].append(ref)
                continue
            row["divergent_subclauses"].append(ref)
            park(ref, "sub")
            if (row["code_year"], ref) in references:
                diverged.append((row["code_year"], ref, [f for f, _ in cands],
                                 word_diff(texts[0][1], texts[-1][1])))
        row["subclauses"] = keep
        row["subclause_numbers"] = [s["number"] for s in keep]

        whole = [(f, rendered(r, num)) for f, r in cands]
        tier = agreement_tier(whole)
        if tier is not None:
            row["text"] = latest_row["text"]
            if tier != "identical":
                row["editions_agree"].append(
                    {"ref": num, "tier": tier, "kept_from": latest_file,
                     "difference": word_diff(whole[0][1], whole[-1][1])})
        else:
            row["text"] = None
            park(num, "whole")
            if (row["code_year"], num) in references:
                diverged.append((row["code_year"], num, [f for f, _ in cands],
                                 word_diff(whole[0][1], whole[-1][1])))
        rows.append(row)
    return rows, diverged


# --- driver ----------------------------------------------------------------

def main():
    pdfium, raw = _pdfium()
    ver = "pypdfium2==%s (libpdfium %s)" % (pdfium.V_PYPDFIUM2, pdfium.V_LIBPDFIUM)
    if pdfium.V_PYPDFIUM2 != PINNED_PYPDFIUM2:
        sys.exit("REFUSING: pinned to pypdfium2 %s, found %s. Determinism of the "
                 "glyph geometry depends on the pdfium build."
                 % (PINNED_PYPDFIUM2, pdfium.V_PYPDFIUM2))

    # The gap backfill has to know what clauses.jsonl already carries, and it
    # has to be exactly the complement of what we take. A newly-published
    # interactive page would otherwise be shadowed by a print row.
    html_by_year = defaultdict(set)
    for row in common.read_jsonl(HTML_CLAUSES):
        html_by_year[int(row["code_year"])].add(str(row["clause_number"]))
    gap_wanted = {}
    for year, (rel, clauses) in sorted(GAP_BACKFILL.items()):
        have = html_by_year.get(year) or set()
        if not have:
            sys.exit("REFUSING: %s carries no %d rows, so the gap this backfill "
                     "is meant to fill cannot be measured." % (HTML_CLAUSES.name, year))
        highest = max(int(c) for c in have)
        missing = tuple(str(n) for n in range(1, highest + 2) if str(n) not in have)
        if missing != tuple(clauses):
            sys.exit("REFUSING: %s is missing clause(s) %s for %d, but GAP_BACKFILL "
                     "names %s. The interactive layer moved; decide which layer "
                     "owns each clause before rebuilding."
                     % (HTML_CLAUSES.name, list(missing) or "none", year,
                        list(clauses)))
        gap_wanted[year] = set(clauses)

    manifest = [r for r in common.read_jsonl(MANIFEST) if r.get("kind") == "pdf"]
    wanted = {r["file"] for r in manifest if r.get("code_year") in PDF_YEARS}
    wanted |= {rel for rel, _cl in GAP_BACKFILL.values()}
    manifest = [r for r in manifest if r["file"] in wanted]
    if not manifest:
        sys.exit("REFUSING: no pre-2014 pdf rows in %s; run scrape/fetch_pdfs.py first."
                 % MANIFEST)
    manifest.sort(key=lambda r: (r["code_year"], r["file"]))
    on_disk_2012 = {r["file"] for r in manifest if r["code_year"] == 2012}
    if on_disk_2012 != set(EDITION_TOKENS):
        sys.exit("REFUSING: the 2012 documents in %s are %s, but EDITION_TOKENS "
                 "names %s. data/code/edition_assignments.jsonl points at editions "
                 "by those tokens." % (MANIFEST.name, sorted(on_disk_2012),
                                       sorted(EDITION_TOKENS)))

    read, refusals, undecided_bands = [], [], []
    for r in manifest:
        path = CODE_DIR / r["file"]
        if not path.exists():
            refusals.append((r["file"], "missing on disk"))
            continue
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha != r["sha256"]:
            refusals.append((r["file"], "sha256 %s differs from the manifest's %s"
                             % (sha[:12], r["sha256"][:12])))
            continue
        code, supp, (lo, hi), forced, notes, bands_und = document_streams(
            path, raw, pdfium)
        for n in notes:
            refusals.append((r["file"], n))
        if notes:
            continue
        for page, entries in sorted(bands_und.items()):
            for head, width, rival in entries:
                undecided_bands.append((r["file"], page, head, width, rival))
        pitch_code, gaps_code = line_pitch(code)
        pitch_supp, gaps_supp = line_pitch(supp)
        ambiguous = []
        for name, pitch, gaps in (("code", pitch_code, gaps_code),
                                  ("supplementary", pitch_supp, gaps_supp)):
            if pitch is None:
                continue
            band = pitch_band_clear(pitch, gaps)
            if band:
                ambiguous.append("%s stream: pitch %.1fpt but gaps %s fall in the "
                                 "band the paragraph rule needs empty"
                                 % (name, pitch, band))
        if ambiguous:
            for a in ambiguous:
                refusals.append((r["file"], a))
            continue
        read.append({"row": r, "path": path, "sha": sha, "pages": (lo, hi),
                     "code": code, "supp": supp, "forced": forced, "engine": ver,
                     "pitch": (pitch_code, pitch_supp),
                     "band_pages": sorted(bands_und)})

    if refusals:
        print("REFUSING: %d document(s) could not be read:" % len(refusals),
              file=sys.stderr)
        for f, why in refusals:
            print("    %s: %s" % (f, why), file=sys.stderr)
        sys.exit(1)

    # Every line-break hyphen is decided before any text is joined, against
    # words the corpus attests WITHOUT crossing a line join.
    corpus_vocab = vocabulary([d["code"] for d in read] + [d["supp"] for d in read],
                              extra_paths=[CODE_DIR / "clauses.jsonl"])
    hyphen_totals = Counter()
    undecided_hyphens = []
    for d in read:
        doc_vocab = vocabulary([d["code"], d["supp"]])
        nj, nk, und = decide_hyphens([d["code"], d["supp"]], doc_vocab, corpus_vocab)
        hyphen_totals["join"] += nj
        hyphen_totals["keep"] += nk
        for pair, n in und.items():
            undecided_hyphens.append((d["row"]["file"], pair, n))
    if undecided_hyphens:
        print("REFUSING: the corpus does not decide %d line-break hyphen(s):"
              % len(undecided_hyphens), file=sys.stderr)
        for f, (l, r), n in undecided_hyphens:
            print("    %s: %r + %r (x%d) -- neither %r nor %r is attested off a "
                  "line join, and one half is not a word"
                  % (f, l, r, n, l + r, l + "-" + r), file=sys.stderr)
        sys.exit("Add each to HYPHEN_DECIDED with the evidence, or widen the "
                 "corpus; do not let the build pick.")
    print("  line-break hyphens: %d joined, %d kept as the compound's own"
          % (hyphen_totals["join"], hyphen_totals["keep"]))

    # A line the compositor broke at a solidus is decided the same way, and
    # against the same kind of evidence.
    corpus_slash = slash_vocabulary(
        [d["code"] for d in read] + [d["supp"] for d in read],
        extra_paths=[HTML_CLAUSES])
    slash_totals = Counter()
    undecided_slashes = []
    for d in read:
        doc_slash = slash_vocabulary([d["code"], d["supp"]])
        nc, ns, und = decide_slashes([d["code"], d["supp"]], doc_slash, corpus_slash)
        slash_totals["close"] += nc
        slash_totals["space"] += ns
        for pair, n in und.items():
            undecided_slashes.append((d["row"]["file"], pair, n))
    if undecided_slashes:
        print("REFUSING: the corpus does not decide %d line(s) broken at a solidus:"
              % len(undecided_slashes), file=sys.stderr)
        for f, (l, r), n in undecided_slashes:
            print("    %s: %r + %r (x%d) -- neither %r nor %r is attested off a "
                  "line join" % (f, l, r, n, l + "/" + r, l + "/ " + r),
                  file=sys.stderr)
        sys.exit("Add each to SLASH_DECIDED with the evidence, or widen the "
                 "corpus; do not let the build pick.")
    print("  lines broken at a solidus: %d closed up, %d given a word space"
          % (slash_totals["close"], slash_totals["space"]))

    docs = []
    truncations = []
    out_of_scope_cuts = {}
    for d in read:
        r = d["row"]
        pitch_code, pitch_supp = d["pitch"]
        clauses = parse_code_stream(d["code"], pitch_code or 12.0)
        if d["forced"] and d["forced"] != len(clauses):
            refusals.append((r["file"],
                             "%d display 'Clause N' numerals were forced into the "
                             "code stream but %d clauses were parsed"
                             % (d["forced"], len(clauses))))
            continue
        # A gap-backfill document contributes exactly the clauses clauses.jsonl
        # lacks, so only those clauses' own numbering is this build's business;
        # the clause SEQUENCE is still checked whole, because that is what
        # confirms a heading numbered by running count. (2015's clause 16 comes
        # out with subclauses 16.1/16.3/16.4 -- a real hole in that parse, and
        # a reason not to emit 2015's clause 16 from this file.)
        emitting = gap_wanted.get(r["code_year"])
        seq = check_sequence(
            [{"number": c["number"],
              "subclause_numbers": ([] if emitting is not None
                                    and c["number"] not in emitting
                                    else list(dict.fromkeys(
                                        sub for sub, _ in c["paras"] if sub)))}
             for c in clauses], whole_edition=not is_addendum(r))
        blocks = parse_supp_stream(d["supp"], pitch_supp or pitch_code or 12.0)
        cut, cut_pages = mark_truncated(blocks, d["band_pages"])
        # A gap-backfill document contributes one clause with no supplementary
        # information at all, so a block cut elsewhere in it cannot reach
        # anything we emit; before the page-band repair, 10 of the 16 cuts
        # across the nine documents then read were in that out-of-scope
        # remainder (2014 x1, 2015 x5, 2016 x4), and all 16 are gone now.
        # They are counted and printed, not declared: this build makes no
        # claim about the rest of those files, clauses.jsonl does.
        in_scope = [h for h, parent in cut
                    if gap_wanted.get(r["code_year"]) is None
                    or parent in gap_wanted[r["code_year"]]]
        out_of_scope_cuts[r["file"]] = len(cut) - len(in_scope)
        for head in in_scope:
            truncations.append((r["file"], head))
        d.update({"clauses": clauses, "supp_blocks": blocks, "seq_problems": seq})
        docs.append(d)
        print("  %-52s pages %2d-%2d  clauses %2d  supp blocks %3d  pitch %s%s"
              % (r["file"].split("/")[-1], d["pages"][0], d["pages"][1],
                 len(clauses), len(blocks), pitch_code,
                 "  TRUNCATED on p%s" % cut_pages if cut_pages else ""))

    # A band boundary the geometry cannot choose is a declared page, not a
    # silent guess (BAND_UNDECIDED). One in the eleven documents; a second
    # means the rule has met a layout it was not measured on. 2001 and 2003
    # were put through this unchanged when they were added (2026-08-09) --
    # 12 boundaries, 12 cut, 0 refused, R23's standing caution answered.
    got_bands = {(f, p, h) for f, p, h, _w, _r in undecided_bands}
    if got_bands != BAND_UNDECIDED:
        print("REFUSING: the set of undecided band boundaries is not the "
              "declared one:", file=sys.stderr)
        for f, p, h, w, riv in undecided_bands:
            if (f, p, h) not in BAND_UNDECIDED:
                print("    NEW  %s p%d above %r: %.2fpt against a rival %.2fpt "
                      "that reads the page differently" % (f, p, h, w, riv),
                      file=sys.stderr)
        for f, p, h in sorted(BAND_UNDECIDED - got_bands):
            print("    GONE %s p%d above %r was declared undecided and is no "
                  "longer" % (f, p, h), file=sys.stderr)
        sys.exit("Read the page and either widen the evidence or declare it; "
                 "do not let the build pick between two strips.")
    print("  page-band boundaries undecided (declared, never served): %d"
          % len(undecided_bands))

    # A supplementary block cut by the reading order is the band rule's own
    # regression guard, and the declared set is now EMPTY (KNOWN_TRUNCATIONS).
    # A cut means the reading order has moved, and that has to be read before
    # it is shipped.
    new_cuts = [t for t in truncations if t not in KNOWN_TRUNCATIONS]
    gone = sorted(KNOWN_TRUNCATIONS - set(truncations))
    if new_cuts or gone:
        print("REFUSING: the set of truncated supplementary blocks is not the "
              "declared one:", file=sys.stderr)
        for f, h in new_cuts:
            print("    NEW  %s: %r ends without terminal punctuation" % (f, h),
                  file=sys.stderr)
        for f, h in gone:
            print("    GONE %s: %r was declared truncated and no longer is"
                  % (f, h), file=sys.stderr)
        sys.exit("Update KNOWN_TRUNCATIONS with what changed and why; the flag "
                 "gates which renderings may be served per case.")
    print("  supplementary blocks cut by the reading order: %d in emitted "
          "clauses (all declared), %d in text this build does not emit"
          % (len(truncations), sum(out_of_scope_cuts.values())))

    if refusals:
        print("REFUSING: %d document(s) could not be read:" % len(refusals),
              file=sys.stderr)
        for f, why in refusals:
            print("    %s: %s" % (f, why), file=sys.stderr)
        sys.exit(1)

    hard = [(d["row"]["file"], p) for d in docs for p in d["seq_problems"]]
    if hard:
        print("REFUSING: clause numbering did not come out ordered:", file=sys.stderr)
        for f, why in hard:
            print("    %s: %s" % (f, why), file=sys.stderr)
        sys.exit("The stream order or the paragraph rule is wrong; fix it rather "
                 "than emitting text under a number it may not belong to.")

    # This file is answerable for the six pre-2014 years in full, and for the
    # three backfilled clauses of the interactive years -- nothing else in
    # 2014/2015/2016 is its business, clauses.jsonl owns it.
    years = set(PDF_YEARS) | set(GAP_BACKFILL) | set(NO_SOURCE_YEARS)
    # ...and it has to be answerable for ALL of them. A pre-interactive year in
    # neither table would be filtered out one line below and never reach the
    # coverage refusal, so its items would carry clause_text: null with no row
    # anywhere saying why. That is what 2001 looked like before the R19
    # corrections named it, and it is a silence, not a value.
    unanswered = sorted({y for y, _c in referenced_cases(
        ITEMS, set(range(1900, PRE_INTERACTIVE_BEFORE)))} - set(PDF_YEARS)
        - set(NO_SOURCE_YEARS))
    if unanswered:
        sys.exit("REFUSING: %s references Code year(s) %s, which predate the "
                 "interactive editions and are in neither PDF_YEARS nor "
                 "NO_SOURCE_YEARS. Put the edition on disk or give the year a "
                 "reason; do not let its items go quiet."
                 % (ITEMS.name, unanswered))
    ref_cases = {k: v for k, v in referenced_cases(ITEMS, years).items()
                 if k[0] in PDF_YEARS or k[0] in NO_SOURCE_YEARS
                 or k[1].split(".")[0] in gap_wanted.get(k[0], set())}
    references = Counter({k: sum(v.values()) for k, v in ref_cases.items()})
    if not references:
        sys.exit("REFUSING: no clause references read from %s. The coverage "
                 "refusal has nothing to check against; run bench/generate.py "
                 "first." % ITEMS)

    assignments = load_edition_assignments()

    by_file, unattached_all = {}, []
    for d in docs:
        meta = {
            "code_year": d["row"]["code_year"],
            "provenance": {
                "source_pdf": d["row"]["file"],
                "source_url": d["row"]["url"],
                "source_title": d["row"].get("title"),
                "sha256_of_source": d["sha"],
                "extractor": EXTRACTOR,
                "extractor_version": EXTRACTOR_VERSION,
                "pdf_engine": d["engine"],
            },
        }
        rows, unattached = assemble(d["clauses"], d["supp_blocks"], meta)
        emitting = gap_wanted.get(d["row"]["code_year"])
        if emitting is not None:
            rows = [r for r in rows if r["clause_number"] in emitting]
            unattached = []          # the rest of the document is not ours
        by_file[d["row"]["file"]] = rows
        unattached_all.extend((d["row"]["file"], h, why) for h, why in unattached)

    # One row per (code_year, clause_number). 2012 is the only year with more
    # than one document, and clause 16 the only clause the addendum touches.
    out, divergences = [], []
    for year in sorted({d["row"]["code_year"] for d in docs}):
        files = [d["row"]["file"] for d in docs if d["row"]["code_year"] == year]
        editions = [f for f in files if not is_addendum(
            next(d["row"] for d in docs if d["row"]["file"] == f))]
        addenda = [f for f in files if f not in editions]
        if len(files) == 1:
            out.extend(by_file[files[0]])
            continue
        table = {f: by_file[f] for f in files}
        # the addendum replaces one clause, so it only joins the comparison for
        # the clauses it actually contains
        add_clauses = {r["clause_number"] for f in addenda for r in table[f]}
        base_rows, div = reconcile(
            {f: [r for r in table[f] if r["clause_number"] not in add_clauses]
             for f in editions},
            editions,
            references)
        add_rows, div2 = reconcile(
            {f: [r for r in table[f] if r["clause_number"] in add_clauses]
             for f in files},
            editions + addenda,
            references)
        out.extend(base_rows + add_rows)
        divergences.extend(div + div2)
    out.sort(key=lambda r: (r["code_year"],
                            [int(x) for x in r["clause_number"].split(".")]))

    # The layer guard is (year, clause) OWNERSHIP, so this file has to be able
    # to state its own half of it: one row per pair, here and in
    # clauses.jsonl. Within a year the reconcile above collapses the editions,
    # and 2001 and 2003 are one document each -- 22 clauses apiece, colliding
    # with nothing (clauses.jsonl starts at 2014 and no other PDF year carries
    # those numbers under those years). verify/pdf_clause_texts.py checks the
    # other half, across the two files, on its own reading of both.
    dup = [k for k, n in Counter((r["code_year"], r["clause_number"])
                                 for r in out).items() if n > 1]
    if dup:
        sys.exit("REFUSING: %d (code_year, clause) pair(s) would be written "
                 "twice: %s. One row per pair is what makes the layer guard "
                 "checkable." % (len(dup), sorted(dup)[:6]))
    html_pairs = {(int(r["code_year"]), str(r["clause_number"]))
                  for r in common.read_jsonl(HTML_CLAUSES)}
    clash = sorted({(r["code_year"], r["clause_number"]) for r in out} & html_pairs)
    if clash:
        sys.exit("REFUSING: %d (code_year, clause) pair(s) are carried by BOTH "
                 "%s and this file: %s. Decide which extraction owns each "
                 "before rebuilding." % (len(clash), HTML_CLAUSES.name, clash[:6]))

    # --- the coverage refusal -------------------------------------------
    lookup = {(r["code_year"], r["clause_number"]): r for r in out}
    exclusions, undecided, resolved = [], [], Counter()
    div_by_pair = {(y, c): (files, diff) for y, c, files, diff in divergences}
    for (year, clause), by_case in sorted(ref_cases.items()):
        n_items = sum(by_case.values())
        parent = clause.split(".")[0]
        if year in NO_SOURCE_YEARS:
            exclusions.append({
                "code_year": year, "clause": clause, "n_items": n_items,
                "reason": "no_source_document",
                "detail": NO_SOURCE_YEARS[year], "sources": [],
            })
            continue
        row = lookup.get((year, clause)) or lookup.get((year, parent))
        if row is not None and rendered(row, clause):
            resolved[year] += n_items
            continue
        if (year, clause) in div_by_pair:
            files, diff = div_by_pair[(year, clause)]
            served, unresolved = [], []
            for case in sorted(by_case):
                kind, why = serve_edition(row, clause, assignments.get(case))
                if kind is None:
                    served.append({"case_number": case,
                                   "n_items": by_case[case], "edition": why})
                else:
                    unresolved.append({"case_number": case,
                                       "n_items": by_case[case],
                                       "reason": kind, "detail": why})
            resolved[year] += sum(s["n_items"] for s in served)
            if not unresolved:
                continue
            kinds = {u["reason"] for u in unresolved}
            exclusions.append({
                "code_year": year, "clause": clause,
                "n_items": sum(u["n_items"] for u in unresolved),
                "reason": kinds.pop() if len(kinds) == 1 else "edition_unresolved",
                "detail": "the %d %s editions on disk render this reference "
                          "differently (%s); %d of %d items are served from their "
                          "case's assigned edition, %d are not"
                          % (len(files), year, diff, sum(s["n_items"] for s in served),
                             n_items, sum(u["n_items"] for u in unresolved)),
                "sources": files,
                "cases_served": served,
                "cases_unresolved": unresolved,
            })
            continue
        present = lookup.get((year, parent))
        sources = [f for f in by_file
                   if by_file[f] and by_file[f][0]["code_year"] == year]
        # 'Absent from the edition' has to be SHOWN, not assumed. A reference
        # that falls inside the numbering the edition demonstrably does carry
        # is a hole in this parse, not a fact about the Code, and the build
        # refuses rather than filing a reason it has not earned.
        if present is None:
            highest = max((int(r["clause_number"]) for (y, _c), r in lookup.items()
                           if y == year), default=0)
            if int(parent) <= highest:
                undecided.append((year, clause,
                                  "clause %s is inside the %d edition's parsed "
                                  "range (1..%d) but no row exists for it"
                                  % (parent, year, highest)))
                continue
            detail = ("the %d edition runs to clause %d; it has no clause %s"
                      % (year, highest, parent))
        elif "." in clause:
            minors = [int(n.split(".")[1]) for n in present["subclause_numbers"]
                      if "." in n]
            if minors and int(clause.split(".")[1]) <= max(minors):
                undecided.append((year, clause,
                                  "clause %s of the %d edition was parsed with "
                                  "subclauses %s, so %s should be among them"
                                  % (parent, year, present["subclause_numbers"],
                                     clause)))
                continue
            detail = ("the %d edition's clause %s has subclauses %s -- it has no "
                      "%s" % (year, parent, present["subclause_numbers"] or "none",
                              clause))
        else:
            undecided.append((year, clause,
                              "clause %s of the %d edition was parsed but its "
                              "whole-clause text came out empty" % (parent, year)))
            continue
        exclusions.append({
            "code_year": year, "clause": clause, "n_items": n_items,
            "reason": "absent_from_edition",
            # A clause number the edition does not carry is far more often a
            # wrong Code-year tag than a wrong number: all three of these are
            # 2008-tagged cases that the reports themselves place under the
            # 2006 Code (AUTH/2147/7/08: "The complaint was considered under
            # the 2006 Code using the 2008 Constitution and Procedure"), and
            # 2006 carries 3.3, 20.1 and 20.2. DEFECTS R19. The tag is L2's to
            # repair; this file only declines to invent the text.
            "detail": detail + " -- a reference the edition does not carry is a "
                               "wrong Code-year tag more often than a wrong "
                               "clause number (DEFECTS R19)",
            "sources": sources,
        })
    exclusions.sort(key=lambda e: (e["code_year"],
                                   [int(x) for x in e["clause"].split(".")]))

    if undecided:
        print("REFUSING: %d referenced pair(s) resolve to no text and cannot be "
              "given a reason:" % len(undecided), file=sys.stderr)
        for year, clause, why in undecided:
            print("    %d clause %s: %s" % (year, clause, why), file=sys.stderr)
        sys.exit("Nothing written. Fix the parse rather than filing these as "
                 "absent from the Code.")

    with OUT.open("w", encoding="utf-8") as fh:
        for row in out:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with OUT_EXCL.open("w", encoding="utf-8") as fh:
        for row in exclusions:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print("\nwrote %s: %d clause rows" % (OUT.relative_to(common.ROOT), len(out)))
    per_year = Counter(r["code_year"] for r in out)
    print("  clauses per edition:", dict(sorted(per_year.items())))
    print("wrote %s: %d rows" % (OUT_EXCL.relative_to(common.ROOT), len(exclusions)))
    for e in exclusions:
        print("  %d clause %-6s %3d items  %-20s %s"
              % (e["code_year"], e["clause"], e["n_items"], e["reason"],
                 e["detail"][:110]))
    print("\nreferenced pairs resolved to text, by Code year:")
    for year in sorted(set(PDF_YEARS) | set(GAP_BACKFILL) | set(NO_SOURCE_YEARS)):
        want = sum(v for (y, _c), v in references.items() if y == year)
        print("  %d: %d of %d items" % (year, resolved[year], want))
    ag = [(r["code_year"], r["clause_number"], a)
          for r in out for a in r.get("editions_agree") or []]
    if ag:
        print("\neditions agreed up to whitespace (the LAST edition's verbatim "
              "rendering kept; no string is synthesised):")
        for y, c, a in ag:
            print("  %d clause %-6s ref %-6s %-16s from %s"
                  % (y, c, a["ref"], a["tier"], a["kept_from"].split("/")[-1]))
            print("        %s" % a["difference"][:150])
    sus = [(r["code_year"], r["clause_number"], r["attachment_suspect"])
           for r in out if r.get("attachment_suspect")]
    if sus:
        # empty since the page-band repair: the six cut pages read continuously
        # now, and the one undecided band (2016 p38) is in text this build does
        # not emit. Kept because the marking is what keeps a bad rendering off
        # a case, and a regression would put pages back in this list.
        print("\nclauses whose supplementary information sits on a page this "
              "parser cut a block on, or whose band boundary it could not "
              "choose (declared, never served per case):")
        for y, c, refs in sus:
            print("  %d clause %-6s %s" % (y, c, refs))
    if unattached_all:
        print("\nsupplementary blocks not attached to any subclause: %d"
              % len(unattached_all))
        for f, h, why in unattached_all:
            print("  %-28s %-58s %s" % (f.split("/")[-1][:28], h[:58], why))
    return out, exclusions


if __name__ == "__main__":
    main()
