"""PDF records — honest verbatim JSON for the cases whose HTML report is wrong.

Covers the 13 cases in investigation/pdf_needed.json via the verified PDFs in
data/pdf/ (for AUTH-2063 the 4-page extract stands in for the 170-page Review
issue it was cut from). One record per case, written to
data/l1/pdf_records.jsonl.

Same charter as build.py: observations only, verbatim glyphs, structure as
parallel offsets, absence is a value, no repairs. Mechanisms adapted from the
sylico papers pipeline (packages/api/app/papers/), measured against this
corpus:

  * TextPiece-style spans with lines-as-printed AND a continuous flow text.
  * Reading order is column-major with full-width lines as barriers. On the
    older Review-typeset PDFs both columns share baselines, so each printed
    row is CUT at the measured gutter first -- grouping by baseline alone
    interleaves the columns sentence by sentence (running heads straddle the
    gutter and are handled as barriers, not evidence against it).
  * Column structure is measured per page (rows supporting an empty x-band),
    and REFUSED in words when ambiguous rather than guessed.
  * ink_source declares per page whether glyphs come from print or from an
    OCR layer under a scan (invisible render mode + image cover).
  * The build refuses to run if a PDF no longer hashes to the value pinned in
    data/pdf/manifest.jsonl.
  * Verification is against an independent witness: the case's HTML summary
    pane (correct even where the report pane is wrong) is word-aligned
    against the extracted flow text, and the expected case number must appear.

pdfium gotchas encoded below (probed 2026-08-01 on this corpus):
  * FPDFText_GetCharBox writes (left, RIGHT, bottom, top) -- not x0,y0,x1,y1.
  * FPDFText_GetFontSize is the /Tf operand alone; multiply by the vertical
    magnitude of FPDFText_GetMatrix.
  * Per-char render mode is reached via FPDFText_GetTextObject ->
    FPDFTextObj_GetTextRenderMode.
  * The Review house style sets the whole body in 'Univers-Bold' (bold is a
    typeface fact, not emphasis) and modern CID fonts report weights like
    3472 -- so `bold` derives from the font NAME and in-range weights only,
    and the raw font name travels on every span.

    uv run --with pypdfium2 python l1/build_pdf.py
"""

import ctypes
import difflib
import hashlib
import json
import math
import pathlib
import re
import sys

import pypdfium2 as pdfium
import pypdfium2.raw as C

ROOT = pathlib.Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data" / "pdf"
PDF_MANIFEST = PDF_DIR / "manifest.jsonl"
RECORDS = ROOT / "data" / "l1" / "records.jsonl"
OUT = ROOT / "data" / "l1" / "pdf_records.jsonl"

SCHEMA_VERSION = "l1p.1"

# The full Review issue is superseded by its 4-page extract for the same case.
SKIP = {"AUTH-2063-10-07.pdf": "full 170-page Review issue; the case is read from AUTH-2063-10-07__pages144-147.pdf"}

CASE_NUM_RE = re.compile(
    r"\b([A-Z]{3,})\s*/?\s*(\d{2,5})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\b"
)

# ---- measured thresholds -------------------------------------------------
BASELINE_MERGE_PT = 3.5   # row pitch is >=11pt corpus-wide; superscripts sit ~2pt up
SCRIPT_OFFSET_PT = 1.2    # bottom offset from the row baseline that marks sub/super
SPACE_GAP_FALLBACK_EM = 0.5  # no space glyph at all, but a gap this wide still reads as one
GUTTER_MIN_PT = 6.0       # a printed gutter is empty; rivers in justified text are narrower
# Measured over all 60 pages of this corpus (2026-08-01): single-column pages
# reach at most 0.537 row support (an outcome-table page), true two-column
# Review pages never fall below 0.983. Both thresholds sit in the empty band.
GUTTER_ROW_SUPPORT = 0.9   # two columns are claimed above this
GUTTER_AMBIGUOUS = 0.6     # 0.6-0.9 = refused in words; empty on this corpus
FULL_WIDTH_MIN_SIDES = 4  # rows required EACH side before two columns are claimed


def _bold(font, weight):
    if "bold" in font.lower():
        return True
    return 100 <= weight <= 1000 and weight >= 600


def _italic(font, flags):
    return bool(flags & 64) or "italic" in font.lower() or "oblique" in font.lower()


def read_chars(page, tp):
    """Every glyph on the page with its geometry and style.

    Generated glyphs (pdfium's own insertions — word spaces derived from TJ
    offsets, line-break markers) are KEPT and flagged: on this corpus the word
    spaces exist only as generated chars, so dropping them fuses words
    ('CASEAUTH/2461/12/11'). They are excluded from glyph counts and from all
    geometry decisions, never from text assembly.

    Rows cluster on the char ORIGIN (the baseline point), not the glyph box
    bottom — a descender's box dips ~2-4pt below the baseline and tears 'y'
    glyphs into their own row ('Daiichi-Sank o … / y y', AUTH-3015).
    """
    out = []
    n_invisible = 0
    n_generated = 0
    for i in range(C.FPDFText_CountChars(tp)):
        generated = C.FPDFText_IsGenerated(tp, i) == 1
        if generated:
            n_generated += 1
        u = C.FPDFText_GetUnicode(tp, i)
        left = ctypes.c_double(); right = ctypes.c_double()
        bottom = ctypes.c_double(); top = ctypes.c_double()
        C.FPDFText_GetCharBox(tp, i, ctypes.byref(left), ctypes.byref(right),
                              ctypes.byref(bottom), ctypes.byref(top))
        ox = ctypes.c_double(); oy = ctypes.c_double()
        C.FPDFText_GetCharOrigin(tp, i, ctypes.byref(ox), ctypes.byref(oy))
        m = C.FS_MATRIX()
        C.FPDFText_GetMatrix(tp, i, ctypes.byref(m))
        size = C.FPDFText_GetFontSize(tp, i) * math.hypot(m.c, m.d)
        obj = C.FPDFText_GetTextObject(tp, i)
        rm = C.FPDFTextObj_GetTextRenderMode(obj) if obj else -1
        if rm == C.FPDF_TEXTRENDERMODE_INVISIBLE and not generated:
            n_invisible += 1
        weight = C.FPDFText_GetFontWeight(tp, i)
        buf = ctypes.create_string_buffer(96)
        fl = ctypes.c_int()
        C.FPDFText_GetFontInfo(tp, i, buf, 96, ctypes.byref(fl))
        font = buf.value.decode("utf-8", "replace")
        out.append({
            "i": i,
            "ch": chr(u), "x0": left.value, "x1": right.value,
            "y0": bottom.value, "y1": top.value, "oy": oy.value, "size": size,
            "font": font, "bold": _bold(font, weight),
            "italic": _italic(font, fl.value),
            "hyphen": C.FPDFText_IsHyphen(tp, i) == 1,
            "generated": generated,
            "invisible": rm == C.FPDF_TEXTRENDERMODE_INVISIBLE,
        })
    return out, n_generated, n_invisible


def ink_source(page, chars, n_invisible):
    n_img = 0
    cover = 0.0
    w, h = page.get_size()
    for j in range(C.FPDFPage_CountObjects(page)):
        obj = C.FPDFPage_GetObject(page, j)
        if C.FPDFPageObj_GetType(obj) == C.FPDF_PAGEOBJ_IMAGE:
            n_img += 1
            l = ctypes.c_float(); b = ctypes.c_float(); r = ctypes.c_float(); t = ctypes.c_float()
            if C.FPDFPageObj_GetBounds(obj, ctypes.byref(l), ctypes.byref(b),
                                       ctypes.byref(r), ctypes.byref(t)):
                area = max(0.0, min(r.value, w) - max(l.value, 0)) * \
                       max(0.0, min(t.value, h) - max(b.value, 0))
                cover += area / (w * h)
    cover = round(min(cover, 1.0), 4)
    real = sum(1 for c in chars if not c["generated"])
    ratio = round(n_invisible / real, 4) if real else 0.0
    is_scan = ratio > 0.9 and cover > 0.9
    decl = None
    if is_scan:
        decl = (f"This page carries no painted text; {n_img} image object(s) cover "
                f"{cover:.0%} of it. Every character comes from an OCR text layer, "
                f"not from the printed page.")
    return {"chars": real, "invisible_ratio": ratio, "image_objects": n_img,
            "image_cover": cover, "is_scan": is_scan, "declaration": decl}


def group_rows(chars):
    """Printed rows: clusters on the baseline ORIGIN. A row can span both
    columns. Space glyphs (real or generated) attach to a row but never
    define one and never count in its geometry."""
    rows = []
    for ch in sorted(chars, key=lambda c: (-c["oy"], c["x0"])):
        for row in rows:
            if abs(row["oy"] - ch["oy"]) <= BASELINE_MERGE_PT:
                row["chars"].append(ch)
                break
        else:
            if ch["ch"].isspace():
                continue  # a space never opens a row
            rows.append({"oy": ch["oy"], "chars": [ch]})
    for row in rows:
        row["chars"].sort(key=lambda c: c["x0"])
        row["ink"] = [c for c in row["chars"] if not c["ch"].isspace()]
        origins = sorted(c["oy"] for c in row["ink"])
        row["baseline"] = origins[len(origins) // 2]
    return [r for r in rows if r["ink"]]


def find_gutter(rows, page_width):
    """The x-band a printed gutter occupies, or None, or a refusal string.

    A row SUPPORTS a band when it has ink on one side only, or a gap of at
    least GUTTER_MIN_PT spanning it. Straddling rows (running heads, the case
    title) are expected and are not counted against the band.
    """
    long_rows = [r for r in rows if len(r["ink"]) >= 10]
    if len(long_rows) < FULL_WIDTH_MIN_SIDES * 2:
        return None, None, None
    lo, hi = page_width * 0.30, page_width * 0.70
    best = None
    for centre_pt in range(int(lo), int(hi)):
        band = (centre_pt, centre_pt + GUTTER_MIN_PT)
        support = left_side = right_side = 0
        for r in long_rows:
            x0, x1 = r["ink"][0]["x0"], r["ink"][-1]["x1"]
            if x1 <= band[0]:
                support += 1; left_side += 1
            elif x0 >= band[1]:
                support += 1; right_side += 1
            else:
                gap_ok = False
                prev = r["ink"][0]["x1"]
                for c in r["ink"][1:]:
                    if prev <= band[0] and c["x0"] >= band[1]:
                        gap_ok = True
                        break
                    prev = max(prev, c["x1"])
                if gap_ok:
                    support += 1; left_side += 1; right_side += 1
        frac = support / len(long_rows)
        if best is None or frac > best[0]:
            best = (frac, band, left_side, right_side)
    frac, band, left_side, right_side = best
    if frac >= GUTTER_ROW_SUPPORT and left_side >= FULL_WIDTH_MIN_SIDES and right_side >= FULL_WIDTH_MIN_SIDES:
        return band, round(frac, 3), None
    if GUTTER_AMBIGUOUS <= frac < GUTTER_ROW_SUPPORT:
        return None, round(frac, 3), (
            f"column structure ambiguous: {frac:.0%} of {len(long_rows)} long rows "
            f"break at the best band x=[{band[0]:.0f},{band[1]:.0f}]; "
            "lines are emitted row-major, unordered across any real columns")
    return None, round(frac, 3), None


def make_line(chars):
    """One printed line: text with word spaces from the page's own space
    glyphs (generated ones included — that is where TJ-offset producers keep
    them), a large glyph gap as fallback; style and script as offsets."""
    ink = [c for c in chars if not c["ch"].isspace()]
    origins = sorted(c["oy"] for c in ink)
    baseline = origins[len(origins) // 2]
    text = ""
    spans = []
    cur = None
    last_hyphen = False
    pending_space = False
    prev = None
    for c in sorted(chars, key=lambda c: c["i"]):
        if c["ch"].isspace():
            pending_space = True
            continue
        gap = (c["x0"] - prev["x1"]) if prev is not None else 0.0
        if text and (pending_space or gap >= SPACE_GAP_FALLBACK_EM * max(c["size"], 1.0)):
            text += " "
            cur = None
        pending_space = False
        off = c["oy"] - baseline
        script = "super" if off > SCRIPT_OFFSET_PT else ("sub" if off < -SCRIPT_OFFSET_PT else "baseline")
        key = (c["font"], round(c["size"], 1), c["bold"], c["italic"], script)
        if cur is None or cur["key"] != key:
            cur = {"key": key, "start": len(text), "end": len(text)}
            spans.append(cur)
        text += c["ch"]
        cur["end"] = len(text)
        last_hyphen = c["hyphen"]
        prev = c
    bbox = [round(min(c["x0"] for c in ink), 2), round(min(c["y0"] for c in ink), 2),
            round(max(c["x1"] for c in ink), 2), round(max(c["y1"] for c in ink), 2)]
    return {
        "bbox": bbox,
        "text": text,
        "spans": [{"start": s["start"], "end": s["end"], "font": s["key"][0],
                   "size": s["key"][1], "bold": s["key"][2], "italic": s["key"][3],
                   "script": s["key"][4]} for s in spans],
        "_ends_with_linebreak_hyphen": last_hyphen and text.endswith("-"),
    }


def page_lines(rows, gutter):
    """Lines in reading order: column-major, full-width rows as barriers."""
    out = []
    pending_left = []
    pending_right = []

    def flush():
        out.extend(pending_left); pending_left.clear()
        out.extend(pending_right); pending_right.clear()

    for row in sorted(rows, key=lambda r: -r["baseline"]):
        if gutter is None:
            line = make_line(row["chars"])
            line["column"] = None
            out.append(line)
            continue
        ink = row["ink"]
        mid = (gutter[0] + gutter[1]) / 2
        left_ink = [c for c in ink if c["x1"] <= gutter[0]]
        right_ink = [c for c in ink if c["x0"] >= gutter[1]]
        if len(left_ink) + len(right_ink) == len(ink) and (left_ink or right_ink):
            # spaces go to the side their centre falls on
            left = [c for c in row["chars"] if c["ch"].isspace() and (c["x0"] + c["x1"]) / 2 < mid] + left_ink
            right = [c for c in row["chars"] if c["ch"].isspace() and (c["x0"] + c["x1"]) / 2 >= mid] + right_ink
            if left_ink and right_ink:
                l, r = make_line(left), make_line(right)
                l["column"] = "left"; r["column"] = "right"
                pending_left.append(l); pending_right.append(r)
            elif left_ink:
                line = make_line(left); line["column"] = "left"
                pending_left.append(line)
            else:
                line = make_line(right); line["column"] = "right"
                pending_right.append(line)
        else:
            # crosses the gutter: a barrier (running head, title, full-measure text)
            flush()
            line = make_line(row["chars"])
            line["column"] = "full"
            out.append(line)
    flush()
    return out


def flow_text(pages):
    """The document as one continuous string; line-break hyphens rejoined."""
    text = ""
    hyphen_pending = False
    for page in pages:
        for line in page["lines"]:
            t = line["text"]
            if not t:
                continue
            if hyphen_pending and text.endswith("-"):
                text = text[:-1] + t  # pdfium marked it a line-break hyphen
            elif text:
                text += " " + t
            else:
                text = t
            hyphen_pending = line["_ends_with_linebreak_hyphen"]
    return text


def summary_oracle(flow, summary_text):
    """Word-align the HTML summary pane (an independent, correct witness)
    against the extracted flow. Scrambled column order fails this."""
    summ = re.sub(r"^Case Summary\s*", "", summary_text or "").strip()
    sw = summ.split()
    if len(sw) < 8:
        return {"available": False, "alignment_score": None,
                "matched_words": None, "summary_words": len(sw)}
    fw = flow.split()
    sm = difflib.SequenceMatcher(a=fw, b=sw, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks() if b.size >= 5)
    return {"available": True,
            "alignment_score": round(matched / len(sw), 3),
            "matched_words": matched, "summary_words": len(sw)}


def load_summaries(files):
    """panes.summary.text for the needed HTML files, streamed from records.jsonl."""
    want = set(files)
    found = {}
    with RECORDS.open(encoding="utf-8") as fh:
        for line in fh:
            probe = line[:200]
            if not any(f in probe for f in want):
                continue
            rec = json.loads(line)
            if rec["file"] in want:
                found[rec["file"]] = rec["panes"]["summary"]["text"]
    return found


def build_one(row, summary_text):
    path = PDF_DIR / row["file"]
    data = path.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != row["sha256"]:
        raise SystemExit(f"REFUSING: {row['file']} hashes to {got[:12]}…, "
                         f"manifest pins {row['sha256'][:12]}…")

    pdf = pdfium.PdfDocument(str(path))
    pages = []
    total_chars = 0
    total_generated = 0
    for pno in range(len(pdf)):
        page = pdf[pno]
        tp = page.get_textpage()
        chars, n_gen, n_inv = read_chars(page, tp)
        total_chars += len(chars) - n_gen
        total_generated += n_gen
        w, h = page.get_size()
        rows = group_rows(chars)
        gutter, support, refused = find_gutter(rows, w)
        lines = page_lines(rows, gutter)
        # Glyph conservation -- the PDF analogue of coverage.py. Every
        # non-space glyph on the page must appear in exactly one emitted line;
        # rows partition ink by construction, and this turns the construction
        # into a checked invariant rather than a claim.
        page_ink = sum(1 for c in chars if not c["ch"].isspace())
        emitted = sum(len(l["text"]) - l["text"].count(" ") for l in lines)
        if emitted != page_ink:
            raise SystemExit(
                f"GLYPH CONSERVATION FAILED: {row['file']} p{pno + 1} prints "
                f"{page_ink} non-space glyphs but the lines carry {emitted}")
        pages.append({
            "page_no": pno + 1,
            "width": round(w, 2), "height": round(h, 2),
            "ink_source": ink_source(page, chars, n_inv),
            "columns": {
                "two_column": gutter is not None,
                "gutter": [round(gutter[0], 2), round(gutter[1], 2)] if gutter else None,
                "row_support": support,
                "refused": refused,
            },
            "lines": lines,
        })

    flow = flow_text(pages)
    for page in pages:
        for line in page["lines"]:
            del line["_ends_with_linebreak_hyphen"]

    expected = [
        "/".join(m) for m in CASE_NUM_RE.findall(
            pathlib.Path(row["html_file"]).stem.replace("__", " ").replace("-", "/"))
    ]
    found_nums = ["/".join(m) for m in CASE_NUM_RE.findall(flow.upper())]

    import importlib.metadata as md
    # Always the full object -- absence is a value, and every record keeps
    # one key signature (same rule as records.jsonl).
    derived_from = {"file": None, "pages": None, "tool": None}
    if row.get("status", "").startswith("extracted"):
        derived_from = {"file": "AUTH-2063-10-07.pdf",
                        "pages": row.get("case_pages_in_pdf") or [144, 147],
                        "tool": "ghostscript pdfwrite"}
    return {
        "schema_version": SCHEMA_VERSION,
        "file": row["file"],
        "html_file": row["html_file"],
        "case_number": row["case_number"],
        "reason": row["reason"],
        "source": {
            "url": row.get("url"),
            "sha256": row["sha256"],
            "bytes": row["bytes"],
            "n_pages": len(pages),
            "derived_from": derived_from,
            "extractor": {"tool": "pypdfium2", "version": md.version("pypdfium2")},
            "transforms": ["skip_generated_glyphs", "space_from_glyph_gap",
                           "flow_joins_lines_with_space", "flow_rejoins_linebreak_hyphens"],
        },
        "pages": pages,
        "flow_text": flow,
        "counts": {
            "pages": len(pages),
            "lines": sum(len(p["lines"]) for p in pages),
            "chars": total_chars,
            "generated_glyphs_skipped": total_generated,
            "two_column_pages": sum(1 for p in pages if p["columns"]["two_column"]),
            "refused_pages": sum(1 for p in pages if p["columns"]["refused"]),
        },
        "verification": {
            "case_numbers_expected": expected,
            "case_number_found": any(e in found_nums for e in expected),
            "summary_oracle": summary_oracle(flow, summary_text),
        },
    }


def main():
    rows = [json.loads(l) for l in PDF_MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip()]
    todo = [r for r in rows if r.get("html_file") and r["file"] not in SKIP]
    summaries = load_summaries([r["html_file"] for r in todo])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with OUT.open("w", encoding="utf-8") as fh:
        for row in sorted(todo, key=lambda r: r["file"]):
            rec = build_one(row, summaries.get(row["html_file"]))
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            v = rec["verification"]
            oracle = v["summary_oracle"]["alignment_score"]
            print(f"  {rec['file']:<44} pages={rec['counts']['pages']:>3} "
                  f"2col={rec['counts']['two_column_pages']} refused={rec['counts']['refused_pages']} "
                  f"case#={'ok' if v['case_number_found'] else 'MISSING'} oracle={oracle}")
            n += 1
    for name, why in SKIP.items():
        print(f"  skipped {name}: {why}")
    print(f"wrote {n} records -> {OUT}  ({OUT.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
