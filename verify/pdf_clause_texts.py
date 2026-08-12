"""Acceptance check for the pre-2014 Code text, on a second extractor.

scrape/parse_code_pdfs.py reads the 2001/2003/2006/2008/2011/2012 Code PDFs
with pypdfium2, reconstructing columns from glyph boxes and separating the Code
provisions from the supplementary information by typeface. This checks its
output with pypdf: a different library, a different code base, and no geometry
at all -- pypdf's plain extraction is content-stream order, which is why it can
witness the text without sharing the builder's column reasoning. DEFECTS F1:
an acceptance test that reuses the builder's parser proves only that the parser
is self-consistent. The two engines stay separate on the encrypted document
too: the archived 2003 PDF carries an empty-user-password /Encrypt dict
(R 2, P -60, permissions only), pypdfium2 opens it transparently, and this side
opens it with pypdf's own empty-password decrypt rather than switching engines
to make the check easy.

    uv run --with 'pypdf==6.1.1' python verify/pdf_clause_texts.py

Three checks, all of them exhaustive -- no sampling:

  1. VERBATIM. Every clause title, subclause text and supplementary text in
     data/code/pdf_clauses.jsonl must be findable, whitespace-insensitively, in
     pypdf's text for the page range the row cites -- INCLUDING the per-edition
     renderings parked in `by_edition`, each against its own document, because
     those are served to items too. "Findable" is: the string decomposes into
     at most MAX_BLOCKS runs that each occur contiguously in that text, each at
     least MIN_BLOCK characters. The unit is the printed PARAGRAPH, not the
     field: a clause's `text` is its subclauses joined and pypdf is entitled to
     emit a bullet list one item at a time. Runs, not one substring, because
     the two extractors order a page's columns differently and a paragraph that
     crosses a column break is contiguous for neither in the same place. The
     bound is measured, not assumed: on this corpus 3,884 of 3,948 paragraphs
     need ONE run and the other 64 need two -- while the same paragraphs with
     their characters shuffled need 78 to 275 runs of 1 character (200-sample
     control, texts over 200 folded characters). Re-measured when the archived
     2001 and 2003 editions were added (2026-08-09): 914 of their 917
     paragraphs need one run and 3 need two, and 334 shuffled controls over the
     same haystacks need 78 to 410 runs. Whitespace and punctuation are
     dropped from both sides before comparing:
     two extractors legitimately disagree about where a word space falls (pypdf
     reads the 2011 edition as 'C ode applies'), and about whether a line-break
     hyphen survives.

  2. CHAIN. Every bench/items.jsonl item whose Code year this file owns must
     show exactly the text its clause row implies -- title, body, supplementary
     -- rebuilt here rather than imported from generate.py. Where the year's
     editions disagree the text comes from the case's own row in
     data/code/edition_assignments.jsonl, and that resolution is re-implemented
     here as well, so builder, generator and validator are three
     implementations of one rule rather than one implementation checked twice.

  3. COVERAGE. Every (code_year, clause) pair the bank references for those
     years must either carry text on all of its items or hold a reasoned row in
     data/code/pdf_clauses_exclusions.jsonl. A pair may now resolve for some
     cases and not others -- the 2012 editions differ and not every case's
     edition is decidable -- so the exclusion row carries `cases_unresolved`
     and the check is per case: exactly the items whose case is listed may lack
     text. Neither an unexplained absence nor an exclusion for a pair that DID
     resolve is allowed.

Exits non-zero on any failure, and prints up to three hand-checkable receipts
per source PDF -- a quota, not a global cap, so a newly parsed edition cannot
be crowded out of the receipts by the ones already there (which is what a flat
"first ten" would have done to 2001 and 2003, since the rows are in Code-year
order and those two now come first).
"""

import hashlib
import json
import pathlib
import re
import sys
import unicodedata
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
CODE_DIR = ROOT / "data" / "code"
PDF_CLAUSES = CODE_DIR / "pdf_clauses.jsonl"
EXCLUSIONS = CODE_DIR / "pdf_clauses_exclusions.jsonl"
HTML_CLAUSES = CODE_DIR / "clauses.jsonl"
MANIFEST = CODE_DIR / "manifest.jsonl"
ASSIGNMENTS = CODE_DIR / "edition_assignments.jsonl"
ITEMS = ROOT / "bench" / "items.jsonl"

PINNED_PYPDF = "6.1.1"
# The six editions parsed whole. 2001 (6 items, AUTH/3200/5/19 -- a 2019
# complaint about a 2002 meeting the Panel ruled under the 2001 Code) and 2003
# (171 items across 33 cases) were exclusion rows only until 2026-08-09, when
# the archived PDFs were fetched; the list did not change, but what it means
# did, and every one of their clause texts is now checked here like the rest.
# The three clauses backfilled into 2014/2015/2016 are picked up from
# pdf_clauses.jsonl's own keys, not from a list, so this file cannot disagree
# with the builder about which interactive clauses it took.
PDF_YEARS = (2001, 2003, 2006, 2008, 2011, 2012)

# Measured on this corpus (2026-08-09, per printed paragraph, the archived
# 2001 and 2003 editions included): 3,884 of 3,948 are one contiguous run and
# 64 need two; nothing needs three. Shuffled controls over the same haystacks
# need 78-410 runs of one character. The cap stays at 6 rather than 2 -- it is
# a bound on how badly two extractors may disagree about column order, not a
# fit to the current distribution.
MAX_BLOCKS = 6
MIN_BLOCK = 20
# A short text cannot be asked for a 20-character run.
SHORT_TEXT = 40

# Receipts per source PDF, not per run. See the module docstring.
RECEIPTS_PER_PDF = 3


def read_jsonl(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def fold(text):
    """Letters and digits only, lower-cased. Drops every difference the two
    extractors are entitled to have: word spaces, line breaks, the line-break
    hyphen, and the shape of a quote mark."""
    return re.sub(r"[^0-9a-z]", "",
                  unicodedata.normalize("NFKD", text or "").lower())


def decompose(haystack, needle):
    """(n_blocks, sizes) or (None, sizes) when a remainder is nowhere in the
    haystack. Greedy longest-run, order NOT required: pypdf emits a page's
    columns in content-stream order, which is not always the printed reading
    order, so a two-column paragraph's halves can arrive either way round."""
    i, sizes = 0, []
    while i < len(needle):
        lo, hi, best = 1, len(needle) - i, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if needle[i:i + mid] in haystack:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best == 0:
            return None, sizes
        sizes.append(best)
        i += best
    return len(sizes), sizes


def render_expected(row, clause, subclauses=None, title=None):
    """What an item's clause_text must be, rebuilt from the clause row.

    Written from bench/items.jsonl's own contract -- 'Clause <n> (<title>):',
    the body, then the supplementary information under its own heading -- and
    deliberately NOT imported from bench/generate.py, which is the code under
    test. `subclauses`/`title` override the row, so the same renderer serves a
    per-edition entry parked in `by_edition`."""
    def prose(value):
        """Collapse the extractor's string-or-paragraph-list representation."""
        if isinstance(value, list):
            return " ".join(prose(part) for part in value if part).strip()
        return (value or "").strip() if isinstance(value, str) else ""

    def render_supplementary(entries):
        rendered = []
        for entry in entries or []:
            heading = prose(entry.get("heading"))
            body = prose(entry.get("text"))
            if body:
                rendered.append((heading + ": " if heading else "") + body)
        return "\n".join(rendered)

    title = row.get("clause_title") or "" if title is None else title
    if "." in str(clause):
        sub = None
        for s in (row.get("subclauses") if subclauses is None else subclauses) or []:
            if s.get("number") == str(clause):
                sub = s
                break
        if sub is None or not (sub.get("text") or "").strip():
            return None
        parts = ["Clause %s (%s):\n%s" % (clause, title, sub["text"].strip())]
        supp = []
        for entry in sub.get("supplementary_information") or []:
            head = (entry.get("heading") or "").strip()
            body = (entry.get("text") or "").strip()
            if body:
                supp.append("%s: %s" % (head, body) if head else body)
        if supp:
            parts.append("\n\nSupplementary information:\n" + "\n".join(supp))
        out = "".join(parts)
    else:
        body = prose(row.get("text"))
        if not body:
            return None
        out = "Clause %s (%s):\n%s" % (clause, title, body)
        # Some source rows model a whole undotted clause as a self-mirroring
        # subclause (number == parent).  Its body is already row["text"], but
        # its official supplementary entries are not.  Reconstruct both
        # supplementary stores independently so the chain check tests the
        # complete text served by the benchmark.
        supplementary = []
        general = prose(row.get("general_supplementary"))
        if general:
            supplementary.append({"heading": None, "text": general})
        for sub in row.get("subclauses") or []:
            if str(sub.get("number")) == str(clause):
                supplementary.extend(sub.get("supplementary_information") or [])
        rendered = render_supplementary(supplementary)
        if rendered:
            out += "\n\nSupplementary information:\n" + rendered
    # Wave C: no 6,000-character truncation. This reading has to agree with
    # bench/generate.py's rendering byte for byte, and the bench no longer
    # truncates -- keeping the cap here would make the independent check fail
    # on the 38 renderings that legitimately run past 6,000 characters.
    return out


def expected_for_case(row, clause, assignment):
    """The text an item of THIS case must show for a reference the year's
    editions dispute, or None. A third implementation of the rule
    scrape/parse_code_pdfs.py's serve_edition and bench/generate.py's
    edition_text_for each carry their own copy of."""
    entries = ((row or {}).get("by_edition") or {}).get(str(clause)) or []
    if not entries or assignment is None:
        return None
    if assignment.get("status") != "assigned":
        return None
    if str(clause) in (assignment.get("withheld_clauses") or {}):
        return None
    want = assignment.get("edition")
    picked = [e for e in entries if e.get("edition") == want]
    if not picked and want == "second_2012_addendum":
        picked = [e for e in entries if e.get("edition") == "second_2012"]
    if not picked or picked[0].get("attachment_suspect"):
        return None
    entry = picked[0]
    if "subclause" in entry:
        return render_expected(row, clause, subclauses=[entry["subclause"]],
                               title=entry.get("clause_title") or "")
    return render_expected({"text": entry.get("text")}, clause,
                           title=entry.get("clause_title") or "")


def main():
    try:
        import pypdf
    except ImportError:
        sys.exit("REFUSING: pypdf not installed. Run:\n"
                 "  uv run --with 'pypdf==%s' python verify/pdf_clause_texts.py"
                 % PINNED_PYPDF)
    if pypdf.__version__ != PINNED_PYPDF:
        sys.exit("REFUSING: this check is pinned to pypdf %s, found %s. The "
                 "builder's engine is pypdfium2; the two must not drift into "
                 "one." % (PINNED_PYPDF, pypdf.__version__))

    from pypdf.errors import FileNotDecryptedError

    rows = read_jsonl(PDF_CLAUSES)
    if not rows:
        sys.exit("REFUSING: %s is empty or missing; run "
                 "scrape/parse_code_pdfs.py first." % PDF_CLAUSES)
    exclusions = read_jsonl(EXCLUSIONS)
    manifest = {r["file"]: r for r in read_jsonl(MANIFEST) if r.get("kind") == "pdf"}
    failures, receipts = [], []

    # --- 0. the two Code-text layers must not overlap ----------------------
    html_keys = {(int(r["code_year"]), str(r["clause_number"]))
                 for r in read_jsonl(HTML_CLAUSES)}
    pdf_keys = {(int(r["code_year"]), str(r["clause_number"])) for r in rows}
    if html_keys & pdf_keys:
        failures.append(("layers", "clauses.jsonl and pdf_clauses.jsonl share %d "
                         "key(s): %s" % (len(html_keys & pdf_keys),
                                         sorted(html_keys & pdf_keys)[:5])))
    print("layers: %d HTML clause keys, %d PDF clause keys, %d shared"
          % (len(html_keys), len(pdf_keys), len(html_keys & pdf_keys)))

    # --- 1. verbatim, against pypdf ---------------------------------------
    cache = {}

    def pdf_pages(rel):
        """pypdf's text for every page, folded. The one encrypted document is
        opened here, not worked around: the archived 2003 edition carries an
        /Encrypt dict with an EMPTY user password and R 2 / P -60 -- a
        permissions flag, not a secret. pypdf 6.1.1 already tries the empty
        password when it opens the file, so this asks it explicitly and
        REFUSES if it ever stops working, rather than leaving the check to
        depend on a library default. Switching this side to pypdfium2, which
        never notices the dict, would have collapsed the two independent
        engines into one (DEFECTS F1)."""
        if rel not in cache:
            path = CODE_DIR / rel
            reader = pypdf.PdfReader(str(path))
            if reader.is_encrypted:
                try:
                    if not reader.decrypt(""):
                        sys.exit("REFUSING: %s is encrypted and the empty "
                                 "password does not open it. This check reads "
                                 "the PDF with pypdf on purpose; do not swap "
                                 "in the builder's engine to get past it."
                                 % rel)
                except FileNotDecryptedError as exc:
                    sys.exit("REFUSING: %s could not be decrypted with the "
                             "empty password: %s" % (rel, exc))
            cache[rel] = [fold(p.extract_text()) for p in reader.pages]
        return cache[rel]

    checked = Counter()
    block_hist = Counter()
    smallest = []
    for row in rows:
        sources = row.get("editions_compared") or [
            {"source_pdf": row["source_pdf"], "sha256_of_source": row["sha256_of_source"],
             "page_first": row["page_first"], "page_last": row["page_last"]}]
        for src in sources:
            rel = src["source_pdf"]
            path = CODE_DIR / rel
            if not path.exists():
                failures.append((rel, "cited source is not on disk"))
                continue
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if sha != src["sha256_of_source"]:
                failures.append((rel, "sha256 on disk %s, row says %s"
                                 % (sha[:12], src["sha256_of_source"][:12])))
            if rel in manifest and manifest[rel]["sha256"] != sha:
                failures.append((rel, "sha256 on disk disagrees with manifest.jsonl"))

        # (source_pdf, page_first, page_last) -> [(what, text)]. The reconciled
        # row's own strings come from row["source_pdf"], but a parked
        # per-edition rendering has to be checked against ITS document: it is
        # served to items whose case is assigned that edition.
        want_texts = defaultdict(list)
        here = (row["source_pdf"], row["page_first"], row["page_last"])
        want_texts[here].append(("title %s" % row["clause_number"],
                                 row.get("clause_title")))
        for sub in row.get("subclauses") or []:
            want_texts[here].append(("clause %s" % sub["number"], sub.get("text")))
            for entry in sub.get("supplementary_information") or []:
                want_texts[here].append(("supplementary to %s" % sub["number"],
                                         entry.get("text")))
        for entry in row.get("general_supplementary") or []:
            want_texts[here].append(("general supplementary to %s"
                                     % row["clause_number"], entry.get("text")))
        for ref, entries in sorted((row.get("by_edition") or {}).items()):
            for entry in entries:
                where = (entry["source_pdf"], entry["page_first"],
                         entry["page_last"])
                tag = "%s %s in %s" % (
                    "clause" if "subclause" in entry else "whole clause", ref,
                    entry["edition"])
                if "subclause" in entry:
                    want_texts[where].append((tag, entry["subclause"].get("text")))
                    for supp in entry["subclause"].get("supplementary_information") or []:
                        want_texts[where].append(("supplementary to " + tag,
                                                  supp.get("text")))
                else:
                    want_texts[where].append((tag, entry.get("text")))

        for (rel, page_first, page_last), texts in sorted(want_texts.items()):
            pages = pdf_pages(rel)
            lo = max(1, page_first - 1)
            hi = min(len(pages), page_last + 1)
            hay = "".join(pages[lo - 1:hi])
            # Per PRINTED PARAGRAPH, not per field. A clause's `text` is its
            # subclauses joined and a subclause's is its paragraphs joined, and
            # the two extractors are entitled to disagree about the order those
            # units arrive in -- the second 2012 edition's bullet lists come
            # out of pypdf one item at a time, which asked 19 runs of a
            # 17-item list read as one string. Each printed unit still has to
            # be findable within MAX_BLOCKS runs, which is the stronger claim:
            # decompose() does not require order, so checking the concatenation
            # never tested it anyway.
            for what, whole in texts:
                for piece in (whole or "").split("\n"):
                    text = piece
                    folded = fold(text)
                    if not folded:
                        continue
                    checked[row["code_year"]] += 1
                    n, sizes = decompose(hay, folded)
                    if n is None:
                        got = sum(sizes)
                        failures.append((
                            "%d %s" % (row["code_year"], what),
                            "not in %s pages %d-%d under pypdf; matched %d of %d "
                            "characters, stuck at %r"
                            % (rel, lo, hi, got, len(folded), folded[got:got + 60])))
                        continue
                    block_hist[n] += 1
                    floor = MIN_BLOCK if len(folded) > SHORT_TEXT else 1
                    if n > MAX_BLOCKS or min(sizes) < floor:
                        failures.append((
                            "%d %s" % (row["code_year"], what),
                            "needs %d run(s) of %s in %s pages %d-%d; the cap is %d "
                            "runs of >= %d"
                            % (n, sizes, rel, lo, hi, MAX_BLOCKS, floor)))
                    smallest.append((min(sizes), row["code_year"], what))
                    per_pdf = sum(1 for r in receipts
                                  if r.startswith("  " + rel.split("/")[-1]))
                    if (n == 1 and len(folded) > 200
                            and per_pdf < RECEIPTS_PER_PDF):
                        words = (text or "").split()
                        receipts.append(
                            "  %s p%d-%d  %d %s: %r ... %r  (%d chars, one run)"
                            % (rel.split("/")[-1], page_first, page_last,
                               row["code_year"], what, " ".join(words[:6]),
                               " ".join(words[-5:]), len(text)))

    print("verbatim: %d texts checked against pypdf (%s), runs needed %s, "
          "smallest run %d chars"
          % (sum(checked.values()), PINNED_PYPDF, dict(sorted(block_hist.items())),
             min(smallest)[0] if smallest else -1))
    print("  by Code year:", dict(sorted(checked.items())))

    # --- 2. the chain into bench/items.jsonl -------------------------------
    lookup = {(int(r["code_year"]), str(r["clause_number"])): r for r in rows}
    assignments = {r["case_number"]: r for r in read_jsonl(ASSIGNMENTS)}
    items = read_jsonl(ITEMS)
    if not items:
        sys.exit("REFUSING: %s is empty or missing." % ITEMS)

    def ours(year, clause):
        """This file answers for the six pre-2014 editions, plus whatever
        clauses of the interactive years pdf_clauses.jsonl actually carries."""
        return year in PDF_YEARS or (year, str(clause).split(".")[0]) in pdf_keys

    seen = Counter()
    with_text = Counter()
    from_edition = Counter()
    mismatched = 0
    pair_cases = defaultdict(lambda: defaultdict(set))
    for item in items:
        ref = (item.get("inputs") or {}).get("clause_ref") or {}
        year, clause = ref.get("code_year"), ref.get("clause")
        if clause is None or not ours(year, clause):
            continue
        clause = str(clause)
        case = item["case_number"]
        seen[(year, clause)] += 1
        shown = ref.get("clause_text")
        pair_cases[(year, clause)][shown is not None].add(case)
        row = lookup.get((year, clause)) or lookup.get((year, clause.split(".")[0]))
        want = render_expected(row, clause) if row is not None else None
        if want is None and row is not None:
            want = expected_for_case(row, clause, assignments.get(case))
            if want is not None:
                from_edition[year] += 1
        if shown != want:
            mismatched += 1
            if mismatched <= 5:
                failures.append((
                    "%s %d/%s" % (item["item_id"], year, clause),
                    "clause_text is not what the clause row implies: shown %r, "
                    "row implies %r" % ((shown or "")[:70], (want or "")[:70])))
        if shown is not None:
            with_text[year] += 1
        # the no_clause_text tag must agree with the field it is derived from
        tagged = "no_clause_text" in (item.get("tags") or [])
        if tagged != (shown is None):
            failures.append((item["item_id"],
                             "no_clause_text tag says %s, clause_text is %s"
                             % (tagged, "null" if shown is None else "present")))
    print("chain: %d items reference the Code years this file owns, %d show "
          "clause text" % (sum(seen.values()), sum(with_text.values())))
    print("  items with text, by Code year:", dict(sorted(with_text.items())))
    print("  of those, served from the case's assigned edition:",
          dict(sorted(from_edition.items())))
    if mismatched:
        print("  MISMATCHED: %d" % mismatched)

    # --- 3. coverage -------------------------------------------------------
    # A pair may resolve for some cases and not others: the two 2012 editions
    # differ on 10 referenced references, and only a case with a decided
    # edition can be served one of them. So the unit is the CASE, and the
    # exclusion row has to name exactly the cases that went without.
    excluded = {(e["code_year"], str(e["clause"])): e for e in exclusions}
    for pair, n in sorted(seen.items()):
        year, clause = pair
        served = pair_cases[pair][True]
        unserved = pair_cases[pair][False]
        row = excluded.get(pair)
        if not unserved:
            if row is not None:
                failures.append(("%d/%s" % pair,
                                 "resolves to text on all %d items AND carries an "
                                 "exclusion row -- one of the two is wrong" % n))
            continue
        if row is None:
            failures.append(("%d/%s" % pair,
                             "%d item(s) show no clause text and no row in %s "
                             "says why" % (len(unserved), EXCLUSIONS.name)))
            continue
        if not (row.get("detail") or "").strip():
            failures.append(("%d/%s" % pair, "exclusion row carries no reason"))
        listed = {c["case_number"] for c in row.get("cases_unresolved") or []}
        if listed or served:
            missing = unserved - listed
            spurious = listed & served
            if missing:
                failures.append(("%d/%s" % pair,
                                 "%d case(s) show no clause text and are not in "
                                 "cases_unresolved: %s"
                                 % (len(missing), sorted(missing)[:4])))
            if spurious:
                failures.append(("%d/%s" % pair,
                                 "cases_unresolved names %d case(s) that DO show "
                                 "text: %s" % (len(spurious), sorted(spurious)[:4])))
        if row.get("n_items") != len(
                [1 for i in items
                 if i["case_number"] in unserved
                 and str(((i.get("inputs") or {}).get("clause_ref") or {})
                         .get("clause")) == clause
                 and ((i.get("inputs") or {}).get("clause_ref") or {})
                     .get("code_year") == year]):
            failures.append(("%d/%s" % pair,
                             "n_items %s does not count the items without text"
                             % row.get("n_items")))
    for pair in sorted(excluded):
        if pair not in seen:
            failures.append(("%d/%s" % pair,
                             "excluded but no item references it"))
    print("coverage: %d referenced (year, clause) pairs, %d excluded with a reason"
          % (len(seen), len(excluded)))
    for e in exclusions:
        print("  EXCLUDED %d clause %-6s %3d items  %s"
              % (e["code_year"], e["clause"], e["n_items"], e["reason"]))

    print("\nreceipts -- open the PDF at the page and read the line:")
    for line in receipts:
        print(line)

    if failures:
        print("\nFAILED: %d problem(s)" % len(failures), file=sys.stderr)
        for what, why in failures[:40]:
            print("  %s: %s" % (what, why), file=sys.stderr)
        if len(failures) > 40:
            print("  ... and %d more" % (len(failures) - 40), file=sys.stderr)
        return 1
    print("\nOK: pdf_clauses.jsonl verified against pypdf %s, the chain into "
          "bench/items.jsonl rebuilt independently, and every referenced pair "
          "either resolved or excluded with a reason." % PINNED_PYPDF)
    return 0


if __name__ == "__main__":
    sys.exit(main())
