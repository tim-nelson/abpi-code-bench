"""L1 — standardise every case page into one JSON shape.

L1 standardises LOCATION and SHAPE. It does not standardise VALUE.

Rules held throughout:
  * every key present on every record; absence is a value, never an omission
      null = genuinely absent in the source
      ""   = present but empty
      []   = parsed, found nothing
  * same type for the same key everywhere (`case_numbers` is always a list)
  * where two locations could DISAGREE, each source gets its own fixed slot and
    L1 leaves the conflict standing. Where they cannot disagree (a heading is a
    heading whether it sits in <p>, <h3> or <td>) L1 folds them into one slot
    and keeps the carrier as an attribute.
  * L1 never repairs a value. AUTH-3303-1-20's case-number field really does say
    'Anonymous complainant v Vifor'; that is what gets recorded, alongside the
    <h1> that holds the real number. Resolving them is L2's job.
  * L1 records OBSERVATIONS only. Classifier verdicts (heading confidence and
    normalisation, the abstract boundary, source integrity) are computed by
    l1/derive.py from records.jsonl alone -- they never touch the HTML and are
    versioned separately in data/l1/derived.jsonl.

Writes data/l1/records.jsonl (one record per case page).
"""

import argparse
import html as html_mod
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML_DIR = ROOT / "data" / "html"
MANIFEST = ROOT / "data" / "manifest.jsonl"
CASE_URLS = ROOT / "data" / "case_urls.jsonl"
OUT = ROOT / "data" / "l1" / "records.jsonl"

SCHEMA_VERSION = "l1.5"

# Every cludo:* property observed across all 1902 files. Fixed so the `meta`
# object has identical keys on every record even if a page omits a tag.
CLUDO_KEYS = [
    "cludo:additional_sanctions", "cludo:appeal", "cludo:applicable_code_year",
    "cludo:case_number", "cludo:case_reference", "cludo:clause_breach",
    "cludo:clause_no_breach", "cludo:complainant", "cludo:completed_date",
    "cludo:date", "cludo:description", "cludo:display_date", "cludo:is_completed",
    "cludo:keywords", "cludo:overview", "cludo:recieved_date",  # sic, misspelt in source
    "cludo:related_clauses", "cludo:respondent", "cludo:review",
    "cludo:sanctions_applied", "cludo:status", "cludo:title",
]

# info-holder labels observed across the corpus. Used only to give the
# named slots below a fixed home; unrecognised labels still land in the
# ordered `info_holder` list untouched.
INFO_BREACH = "Breach Clause(s)"
INFO_NO_BREACH = "No breach Clause(s)"

META_RE = re.compile(r"<meta\b([^>]*)>", re.I)
ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
INFO_NAME_RE = re.compile(
    r'<div class="info-holder-name"\s*>(.*?)</div>\s*<div class="info-holder-text"\s*>',
    re.S | re.I,
)
INFO_DIV_RE = re.compile(r"<div\b|</div\s*>", re.I)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
# The hero block above the tabs: <h1> then, on 1892 of 1902 pages, an <h2>
# subject line. The <h2> disagrees with cludo:description on 850 pages
# (including semantic flips like 'Alleged breach' vs 'No breach'), so by the
# could-they-disagree rule it gets its own slot. The div nests nothing, so the
# non-greedy match is safe.
HERO_RE = re.compile(r'<div class="heading">(.*?)</div>', re.S)
H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
# <title> disagrees with <h1> on 45 pages (extra procedural detail, accents,
# spacing), so it too is its own slot.
TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
# The aside carries the case-report download links (some are .docx -- invisible
# to the .pdf-only markup.pdf_links), advertised-sanctions links, and link TEXT
# that can state a case number (wrongly, on AUTH-2102-2-08). Every page has the
# aside followed by <footer> (measured corpus-wide 2026-08-01).
ASIDE_RE = re.compile(r'<div[^>]*\bclass="page-layout-aside".*?(?=<footer\b)', re.S | re.I)
ASIDE_A_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
# Exactly one page (AUTH-2789-8-15) puts a heading in the aside -- 'RELATED
# ADVICE' above a related-case link. Case information, so it is captured; the
# coverage check found it.
ASIDE_H_RE = re.compile(r"<(h[1-6])\b[^>]*>(.*?)</\1>", re.S | re.I)
HREF_ATTR_RE = re.compile(r'href="([^"]*)"', re.I)
TABLE_RE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.S | re.I)
TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
CELL_RE = re.compile(r"<(t[dh])\b[^>]*>(.*?)</\1>", re.S | re.I)
PDF_RE = re.compile(r'href="([^"]*\.pdf)"', re.I)
BREACH_CELL_RE = re.compile(r"^\s*(?:No\s+)?Breach of Clause\b", re.I)
OUTCOME_INTRO_RE = re.compile(r"The outcome under the .{0,40}?Code(?: of Practice)? was", re.I)

# Block elements that can carry a heading. Deliberately excludes <div>: these
# pages wrap the whole body in <div class="rte-content">, and a non-greedy match
# on it would consume every paragraph inside, hiding all the real headings from
# finditer. None of p/h*/td/th nests inside itself in this corpus.
BLOCK_RE = re.compile(r"<(p|h[1-6]|td|th)\b([^>]*)>(.*?)</\1>", re.S | re.I)
CASE_NUM_RE = re.compile(
    r"\b([A-Z]{3,})\s*/?\s*(\d{2,5})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\b"
)

# A few source pages double-escape: the pane HTML literally contains
# '&amp;amp;', which one unescape pass leaves as '&amp;'. 335 occurrences over
# 121 files, and double-escaping accounts for 100% of residual entities.
# The second pass is TARGETED rather than a blanket second unescape --
# html.unescape is lenient about missing semicolons, so running it twice over
# everything risks mangling ordinary text containing '&'.
# After the first unescape a double-escaped '&amp;amp;' has become '&amp;' --
# a well-formed entity that is still standing. We resolve exactly those, by
# explicit substitution rather than a second html.unescape call, so text like
# 'AT&T' or 'R&D' can never be touched.
RESIDUAL_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|#x?[0-9a-fA-F]+);")
NAMED = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
         "&apos;": "'", "&nbsp;": " "}


def _resolve_entity(m):
    e = m.group(0)
    if e in NAMED:
        return NAMED[e]
    body = e[2:-1]
    try:
        return chr(int(body[1:], 16) if body[:1].lower() == "x" else int(body))
    except (ValueError, OverflowError):
        return e


def decode_attr(v):
    """An HTML attribute value, decoded as a conformant parser would, then
    with double-escaping resolved.

    L1 previously stored the RAW attribute bytes and called that 'verbatim'.
    It is not: a parser turns content="&amp;lt;p&amp;gt;" into "&lt;p&gt;", and
    this corpus double-escapes, so the author's value is "<p>". Storing the raw
    bytes meant 1,154 records (60.7%) held a string no HTML consumer would ever
    see -- including 9 clause-label values.
    """
    if v is None:
        return None
    out = html_mod.unescape(v)
    if RESIDUAL_ENTITY.search(out):
        out = RESIDUAL_ENTITY.sub(_resolve_entity, out)
    return out


def txt(s):
    """Tags out, entities decoded, whitespace collapsed."""
    out = html_mod.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()
    if RESIDUAL_ENTITY.search(out):
        out = RESIDUAL_ENTITY.sub(_resolve_entity, out)
    return out


def upper_ratio(s):
    alpha = [c for c in s if c.isalpha()]
    return sum(c.isupper() for c in alpha) / len(alpha) if alpha else 0.0


def parse_case_numbers(text):
    found = []
    for m in CASE_NUM_RE.finditer(text or ""):
        norm = "/".join(m.group(1, 2, 3, 4))
        if norm not in found:
            found.append(norm)
    return found


def info_items(raw):
    """(label_html, value_html) pairs from the info-holder block.

    The value div can NEST divs -- 'Additional sanctions' renders one
    <div class="tag-label"> chip per sanction -- so its close is found by
    depth walk. The previous regex stopped at the first </div> and silently
    dropped every chip after the first on 957 pages (the coverage check
    caught it; the full list survived only in meta cludo:additional_sanctions).
    """
    out = []
    for m in INFO_NAME_RE.finditer(raw):
        depth = 1
        pos = m.end()
        for t in INFO_DIV_RE.finditer(raw, pos):
            depth += 1 if t.group(0)[1] != "/" else -1
            if depth == 0:
                out.append((m.group(1), raw[pos:t.start()]))
                break
    return out


def pane_html(raw, tab):
    """Return (present, html) for a tab pane."""
    m = re.search(r'<div[^>]*\bid="%s"[^>]*>' % tab, raw, re.I)
    if not m:
        return False, None
    start = m.end()
    nxt = re.search(r'<div[^>]*\bid="tab\d"[^>]*>', raw[start:], re.I)
    # Match the OPENING tag of the aside, not the class attribute inside it --
    # slicing at the attribute leaves a stray '<div' fragment on every pane.
    aside = re.search(r'<div[^>]*\bclass="page-layout-aside"', raw[start:], re.I)
    if nxt:
        end = start + nxt.start()
    elif aside:
        end = start + aside.start()
    else:
        end = len(raw)
    return True, raw[start:end]


NUMBERED_PREFIX = re.compile(r"^\s*(?:\d+|[A-Z]|[ivxIVX]+)\s*[.)–—-]?\s+\S")
DATE_TRAILER = re.compile(
    r"^\s*(?:complaint|case[s]?|proceedings|undertaking[s]?)\s+"
    r"(?:received|completed|commenced|reported)\b",
    re.I,
)
TERMINAL_PUNCT = re.compile(r"[.!?;:,]\s*$")
QUOTE_OPEN = re.compile(r"^\s*[\u201c\u2018\"']")
QUOTE_CLOSE = re.compile(r"[\u201d\u2019\"']")


def unbalanced_quote(s):
    """A heading that opens a quotation and never closes it is a truncated
    fragment of quoted material, not a heading. This replaces the proposed
    `word_count == 1 AND followed_by_body_chars == 0` rule, which would have
    demoted 529 high headings including 417 legitimate COMPLAINT/RESPONSE
    dividers -- a divider legitimately has no body when another heading
    follows it immediately."""
    if not s or not QUOTE_OPEN.match(s):
        return False
    return not QUOTE_CLOSE.search(s[1:])


def find_heading_candidates(chunk):
    """Every element that could plausibly be a heading, with its evidence.

    Deliberately over-inclusive: the evidence fields and confidence tier are
    what exclude, not this filter. Nested duplicates
    (<p><strong>X</strong></p>) collapse to the outer block -- a carrier cannot
    disagree with itself, so it is one slot, not several.
    """
    out = []
    for m in BLOCK_RE.finditer(chunk):
        tag = m.group(1).lower()
        inner = m.group(3)
        # Use the SAME extractor as section bodies. Using txt() here (all tags
        # -> space) while bodies use text_and_spans() (inline tags close up)
        # meant heading text and its own structure offsets disagreed on 11
        # spans, and left artefacts like '1 st' and '\u2018 The' in 2.54% of
        # heading candidates.
        s = text_and_spans(m.group(0))[0]
        # The 90-char cap was v1's cheap proxy for "label, not sentence", from
        # when the rule was binary and needed a discriminator. It is now the
        # only place something is dropped silently rather than emitted with
        # evidence. Length moves into evidence as char_count; the plausibility
        # gate is a CONTENT signal -- a block ending in sentence punctuation is
        # a sentence, not a heading -- with short blocks always admitted so no
        # v2 candidate is lost.
        if len(s) < 2 or not any(c.isalpha() for c in s):
            continue
        if len(s) > 90 and TERMINAL_PUNCT.search(s):
            continue
        ev = {
            "carrier_tag": tag,
            "char_count": len(s),
            "uppercase_ratio": round(upper_ratio(s), 3),
            "word_count": len(s.split()),
            "has_terminal_punctuation": bool(TERMINAL_PUNCT.search(s)),
            "is_standalone_element": True,
            "has_numbered_prefix": bool(NUMBERED_PREFIX.match(s)),
            "is_bold_or_emphasised": bool(
                re.match(r"^\s*<(strong|b|em|u)\b", inner.strip(), re.I)
            ),
            "in_table_cell": tag in ("td", "th"),
            "matches_date_trailer": bool(DATE_TRAILER.match(s)),
            "unbalanced_quote": unbalanced_quote(s),
            "followed_by_body_chars": None,  # filled in by build_sections
        }
        out.append(
            {
                "start": m.start(),
                "end": m.end(),
                "html": m.group(0),
                "text": s,
                "evidence": ev,
            }
        )
    # Outermost wins on overlap.
    kept = []
    for h in out:
        if kept and h["start"] < kept[-1]["end"]:
            continue
        kept.append(h)
    return kept


def build_sections(pane_name, chunk):
    """Split a pane into ordered sections and build the pane text they tile.

    pane_text is assembled from the sections so char_start/char_end are exact
    slices by construction; section 0 carries heading_text=null when the pane
    opens with text before any heading.
    """
    if chunk is None:
        return [], ""
    heads = find_heading_candidates(chunk)
    # A pane with no headings and no text yields no sections at all: [] means
    # "parsed, found nothing", which is what a stub's empty report pane is.
    if not heads and not txt(chunk):
        return [], ""
    spans = []
    if not heads:
        spans.append((None, chunk))
    else:
        if txt(chunk[: heads[0]["start"]]):
            spans.append((None, chunk[: heads[0]["start"]]))
        for i, h in enumerate(heads):
            end = heads[i + 1]["start"] if i + 1 < len(heads) else len(chunk)
            spans.append((h, chunk[h["end"]:end]))

    sections = []
    pane_text = ""
    for idx, (head, body_html) in enumerate(spans):
        body, body_spans, body_struct = text_and_spans(body_html)
        htext = head["text"] if head else None
        piece = " ".join(p for p in (htext, body) if p)
        if not piece:
            # Keep the section -- shape is uniform, emptiness is a value.
            piece = ""
        if pane_text and piece:
            pane_text += " "
        start = len(pane_text)
        pane_text += piece
        if head:
            ev = dict(head["evidence"])
            ev["followed_by_body_chars"] = len(body)
        else:
            ev = None
        # Shift body spans past the heading text prefixed onto the piece.
        offset = (len(htext) + 1) if (htext and body) else 0
        def _shift(items):
            return [{**it, "start": it["start"] + offset, "end": it["end"] + offset}
                    for it in items]

        head_spans, head_struct = [], {"list_spans": [], "links": [], "images": [], "line_breaks": []}
        if head:
            # The heading element is not part of body_html, so anything inside it
            # (4,789 <br> and 46 <a href> corpus-wide) would otherwise be lost.
            _, head_spans, head_struct = text_and_spans(head["html"])

        sec_spans = head_spans + _shift(body_spans)
        sec_struct = {
            "list_spans": head_struct["list_spans"] + _shift(body_struct["list_spans"]),
            "links": head_struct["links"] + _shift(body_struct["links"]),
            "images": head_struct["images"]
            + [{**im, "offset": im["offset"] + offset} for im in body_struct["images"]],
            "line_breaks": head_struct["line_breaks"]
            + [b + offset for b in body_struct["line_breaks"]],
        }
        sections.append(
            {
                "index": idx,
                "pane": pane_name,
                "heading_text": htext,
                "emphasis_spans": sec_spans,
                "list_spans": sec_struct["list_spans"],
                "links": sec_struct["links"],
                "images": sec_struct["images"],
                "line_breaks": sec_struct["line_breaks"],
                "carrier_tag": ev["carrier_tag"] if ev else None,
                "carrier_emphasised": ev["is_bold_or_emphasised"] if ev else False,
                "heading_evidence": ev,
                "char_start": start,
                "char_end": len(pane_text),
                "text": piece,
                "text_length": len(piece),
            }
        )
    return sections, pane_text


def parse_tables(pane_name, chunk):
    out = []
    if chunk is None:
        return out
    for i, m in enumerate(TABLE_RE.finditer(chunk)):
        rows = []
        for tr in TR_RE.finditer(m.group(1)):
            cells = [txt(c.group(2)) for c in CELL_RE.finditer(tr.group(1))]
            if cells:
                rows.append(cells)
        out.append(
            {
                "index": i,
                "pane": pane_name,
                "n_rows": len(rows),
                "n_cols": max((len(r) for r in rows), default=0),
                "rows": rows,
            }
        )
    return out


def outcome_table_rows(tables):
    """Rows of any table whose first cell reads '(No) Breach of Clause ...'.

    This is a location rule, not a value judgement -- it identifies WHERE the
    site states an outcome in tabular form. Nothing is resolved here.
    """
    rows = []
    order = 0
    for t in tables:
        for r in t["rows"]:
            if r and BREACH_CELL_RE.match(r[0]):
                rows.append(
                    {
                        "order": order,
                        "pane": t["pane"],
                        "table_index": t["index"],
                        "verdict_text": r[0],
                        "description_text": r[1] if len(r) > 1 else None,
                    }
                )
                order += 1
    return rows


def load_side_tables():
    manifest = {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("filename") and r.get("http_status") == 200:
                    manifest[r["filename"]] = r
    index_by_url = {}
    if CASE_URLS.exists():
        for line in CASE_URLS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                index_by_url[r["url"]] = r
    return manifest, index_by_url


def build_record(path, manifest, index_by_url):
    raw = path.read_text(encoding="utf-8", errors="replace")
    man = manifest.get(path.name, {})
    url = man.get("url")
    idx = index_by_url.get(url, {}) if url else {}

    # ---- <meta> ----------------------------------------------------------
    meta = {k: None for k in CLUDO_KEYS}  # null = tag genuinely absent
    meta_other = []
    for m in META_RE.finditer(raw):
        attrs = dict(ATTR_RE.findall(m.group(1)))
        key = attrs.get("property") or attrs.get("name")
        if not key:
            continue
        content = decode_attr(attrs.get("content", ""))
        if key in meta:
            meta[key] = content  # "" = tag present but empty
        else:
            meta_other.append({"key": key, "content": content})

    # ---- info-holder ------------------------------------------------------
    info_holder = []
    info_by_label = {}
    for i, (label_html, value_html) in enumerate(info_items(raw)):
        label = txt(label_html)
        value = txt(value_html)
        info_holder.append(
            {"order": i, "label": label, "value": value, "value_html": value_html.strip()}
        )
        info_by_label.setdefault(label, value)

    # ---- panes and sections ----------------------------------------------
    s_present, s_html = pane_html(raw, "tab1")
    r_present, r_html = pane_html(raw, "tab2")
    s_sections, s_text = build_sections("summary", s_html)
    r_sections, r_text = build_sections("report", r_html)
    # Pane spans are DERIVED from section spans, not recomputed from the pane
    # HTML: the pane text is assembled by joining section pieces, so a fresh
    # extraction over the whole pane produces a subtly different string and the
    # offsets do not line up (3,893 mismatches when this was recomputed).
    def _pane_spans(secs):
        return [{**sp,
                 "start": sp["start"] + sec["char_start"],
                 "end": sp["end"] + sec["char_start"]}
                for sec in secs for sp in sec["emphasis_spans"]]

    s_pane_spans = _pane_spans(s_sections)
    r_pane_spans = _pane_spans(r_sections)

    s_tables = parse_tables("summary", s_html)
    r_tables = parse_tables("report", r_html)
    all_tables = s_tables + r_tables

    h1 = H1_RE.search(raw)
    h1_text = txt(h1.group(1)) if h1 else None

    # ---- hero <h2>, <title>, aside links (added v2.4) ---------------------
    hero = HERO_RE.search(raw)
    h2_text = None
    if hero:
        h2 = H2_RE.search(hero.group(1))
        if h2:
            h2_text = txt(h2.group(1))

    title = TITLE_TAG_RE.search(raw)
    title_text = txt(title.group(1)) if title else None

    aside_links = []
    aside_headings = []
    aside = ASIDE_RE.search(raw)
    if aside:
        for i, a in enumerate(ASIDE_A_RE.finditer(aside.group(0))):
            href = HREF_ATTR_RE.search(a.group(1))
            aside_links.append(
                {
                    "order": i,
                    # null = no href attribute at all; "" = href="" in source.
                    "href": decode_attr(href.group(1)) if href else None,
                    "text": txt(a.group(2)),
                }
            )
        for i, h in enumerate(ASIDE_H_RE.finditer(aside.group(0))):
            aside_headings.append(
                {"order": i, "tag": h.group(1).lower(), "text": txt(h.group(2))}
            )

    # ---- date trailer lines ----------------------------------------------
    trailer = []
    for sec in r_sections:
        for m in re.finditer(
            r"((?:Complaint|Case|Cases)\s+(?:received|completed)[^.]{0,60})", sec["text"], re.I
        ):
            t = m.group(1).strip()
            if t not in trailer:
                trailer.append(t)

    fn_numbers = [
        n for part in path.stem.split("__")
        for n in parse_case_numbers(part.replace("-", "/"))
    ]

    record = {
        "schema_version": SCHEMA_VERSION,
        "file": path.name,
        "source": {
            "url": url,
            "final_url": man.get("final_url"),
            "sha256": man.get("sha256"),
            "html_bytes": len(raw.encode("utf-8", errors="replace")),
            "fetched_at": man.get("fetched_at"),
            "transforms": ["strip_tags", "unescape", "resolve_residual_entities"],
        },
        # Five places a case number can be stated. They demonstrably disagree,
        # so each keeps its own slot and L1 resolves none of them.
        "identity": {
            "filename_stem": path.stem,
            "filename_case_numbers": fn_numbers,
            "meta_case_number": meta.get("cludo:case_number"),
            "meta_case_numbers_parsed": parse_case_numbers(meta.get("cludo:case_number")),
            "info_case_number": info_by_label.get("Case number"),
            "info_case_numbers_parsed": parse_case_numbers(info_by_label.get("Case number")),
            "h1_text": h1_text,
            "h1_case_numbers_parsed": parse_case_numbers(h1_text),
            # The hero <h2> subject line. Not a case-number slot, but it
            # disagrees with meta['cludo:description'] on 850 pages, so it is
            # its own source. null = the hero block carries no <h2> (10 pages).
            "h2_text": h2_text,
            # <title> and <h1> disagree on 45 pages.
            "title_text": title_text,
            "title_case_numbers_parsed": parse_case_numbers(title_text),
            "index_case_number": idx.get("case_number"),
            "url_slug": (url.rstrip("/").rsplit("/", 1)[-1] if url else None),
        },
        "meta": meta,
        "meta_other": meta_other,
        "info_holder": info_holder,
        "aside_links": aside_links,
        "aside_headings": aside_headings,
        "panes": {
            "summary": {
                "present": s_present,
                "emphasis_spans": s_pane_spans,
                "text": s_text,
                "text_length": len(s_text),
                "html_length": len(s_html) if s_html is not None else 0,
            },
            "report": {
                "present": r_present,
                "emphasis_spans": r_pane_spans,
                "text": r_text,
                "text_length": len(r_text),
                "html_length": len(r_html) if r_html is not None else 0,
            },
        },
        "sections": s_sections + r_sections,
        "tables": all_tables,
        # Every distinct place the site states an outcome. They disagree in the
        # corpus (post-appeal meta vs Panel prose; 322 files list a clause as
        # both breach and no-breach), so all of them stay side by side.
        "outcomes": {
            "meta_clause_breach": meta.get("cludo:clause_breach"),
            "meta_clause_no_breach": meta.get("cludo:clause_no_breach"),
            "meta_status": meta.get("cludo:status"),
            "meta_sanctions_applied": meta.get("cludo:sanctions_applied"),
            "meta_additional_sanctions": meta.get("cludo:additional_sanctions"),
            "info_breach_clauses": info_by_label.get(INFO_BREACH),
            "info_no_breach_clauses": info_by_label.get(INFO_NO_BREACH),
            "info_sanctions_applied": info_by_label.get("Sanctions applied"),
            "info_additional_sanctions": info_by_label.get("Additional sanctions"),
            "report_table_rows": outcome_table_rows(all_tables),
        },
        "dates": {
            "meta_received": meta.get("cludo:recieved_date"),
            "meta_completed": meta.get("cludo:completed_date"),
            "info_received": info_by_label.get("Complaint received"),
            "info_completed": info_by_label.get("Completed"),
            "report_trailer_lines": trailer,
        },
        "appeal": {
            "meta_appeal": meta.get("cludo:appeal"),
            "info_appeal_hearing": info_by_label.get("Appeal hearing"),
        },
        # Purely structural observations about the markup. No interpretation.
        "markup": {
            "has_mso": bool(re.search(r'class="Mso|mso-', raw)),
            "has_case_item": 'class="caseItem"' in raw,
            "has_sharepoint_residue": "ctl00_PlaceHolderMain" in raw,
            "has_rte_content": 'class="rte-content"' in raw,
            "n_tables_summary": len(s_tables),
            "n_tables_report": len(r_tables),
            "heading_carrier_tags": sorted(
                {s["carrier_tag"] for s in r_sections if s["carrier_tag"]}
            ),
            "panel_ruling_in_report_text": "PANEL RULING" in r_text,
            "outcome_intro_in_report": bool(OUTCOME_INTRO_RE.search(r_text)),
            "pdf_links": sorted({m for m in PDF_RE.findall(raw)}),
        },
    }
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    manifest, index_by_url = load_side_tables()
    files = sorted(HTML_DIR.glob("*.html"))
    if args.limit:
        files = files[: args.limit]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print(f"building L1 for {len(files)} files ...")
    n = 0
    # Temp file + rename, like every other regenerated artefact: records.jsonl
    # is 202 MB and takes a minute to write, and an audit reading L1 while a
    # build runs must see one whole generation, never a prefix of the new one.
    tmp = OUT.with_name(OUT.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for f in files:
            rec = build_record(f, manifest, index_by_url)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            if n % 400 == 0:
                print(f"  {n}")
    os.replace(tmp, OUT)
    size = OUT.stat().st_size
    print(f"wrote {n} records -> {OUT}  ({size/1e6:.1f} MB)")
    return 0


# ---------------------------------------------------------------------------
# Emphasis-aware text extraction (v2.2)
#
# v2.1 replaced EVERY tag with a space, so `<em>other </em>individual` became
# " other individual" -- a stray space where the tag closed, and no record of
# which word carried the emphasis. That is worse than ordinary loss: the text
# still asserts "(emphasis added by Panel)" while making the referent
# unrecoverable.
#
# Inline tags now close up (replaced with nothing); only block-level tags
# become a space. Emphasis runs are recorded as parallel offsets so `text`
# needs no in-band markers.
# ---------------------------------------------------------------------------
EMPH_TAGS = {"em", "strong", "b", "i", "u"}
INLINE_TAGS = EMPH_TAGS | {
    "span", "a", "sup", "sub", "abbr", "cite", "code", "small", "font",
    "mark", "s", "strike", "del", "ins", "q", "var", "kbd", "samp", "time", "wbr",
}
ANY_TAG = re.compile(r"<[^>]+>")
TAG_NAME = re.compile(r"</?\s*([a-zA-Z0-9]+)")


def text_and_spans(frag):
    """Return (text, emphasis_spans, structure) for an HTML fragment.

    `text` is unchanged from v2.2 -- every structure below is recorded as
    PARALLEL OFFSETS so nothing in the text moves. This is what lets L1 stand
    in for the HTML: without it, `<li>` boundaries, `<br>`, image sources and
    link targets were unrecoverable and a consumer had to reopen the source.

    structure = {"list_spans": [...], "line_breaks": [...],
                 "images": [...], "links": [...]}
    """
    empty = {"list_spans": [], "line_breaks": [], "images": [], "links": []}
    if not frag:
        return "", [], empty

    chars = []          # ("T", ch, emph, listctx, href) | ("P", kind, payload)
    emph_stack = []
    list_stack = []     # (list_type, uid, next_ordinal)
    item = None         # (list_type, uid, ordinal)
    href = None
    uid = 0
    pos = 0

    def push_text(raw):
        t = html_mod.unescape(raw)
        if RESIDUAL_ENTITY.search(t):
            t = RESIDUAL_ENTITY.sub(_resolve_entity, t)
        k = emph_stack[-1] if emph_stack else None
        for ch in t:
            chars.append(("T", ch, k, item, href))

    for m in ANY_TAG.finditer(frag):
        seg = frag[pos:m.start()]
        if seg:
            push_text(seg)
        tag = m.group(0)
        nm = TAG_NAME.match(tag)
        name = nm.group(1).lower() if nm else ""
        closing = tag.startswith("</")
        void = tag.rstrip().endswith("/>")

        if name in EMPH_TAGS:
            if closing:
                if emph_stack and emph_stack[-1] == name:
                    emph_stack.pop()
                elif name in emph_stack:
                    emph_stack.remove(name)
            elif not void:
                emph_stack.append(name)
        elif name in ("ul", "ol"):
            if closing:
                if list_stack:
                    list_stack.pop()
                item = None
            else:
                uid += 1
                list_stack.append([name, uid, 0])
        elif name == "li":
            if closing:
                item = None
            elif list_stack:
                list_stack[-1][2] += 1
                item = (list_stack[-1][0], list_stack[-1][1], list_stack[-1][2])
            else:
                uid += 1
                item = ("ul", uid, 1)
        elif name == "br":
            chars.append(("P", "br", None))
        elif name == "img":
            src = re.search(r'src="([^"]*)"', tag, re.I)
            alt = re.search(r'alt="([^"]*)"', tag, re.I)
            chars.append(("P", "img", {"src": html_mod.unescape(src.group(1)) if src else None,
                                       "alt": html_mod.unescape(alt.group(1)) if alt else None}))
        elif name == "a":
            if closing:
                href = None
            else:
                h = re.search(r'href="([^"]*)"', tag, re.I)
                href = html_mod.unescape(h.group(1)) if h else None

        if name not in INLINE_TAGS:
            chars.append(("T", " ", None, item, href))
        pos = m.end()

    seg = frag[pos:]
    if seg:
        push_text(seg)

    # --- collapse whitespace, translating point events to final offsets -----
    out = []
    points = []
    prev_ws = False
    for e in chars:
        if e[0] == "P":
            points.append((len(out), e[1], e[2]))
            continue
        _, ch, k, it, hf = e
        if ch.isspace():
            if not prev_ws:
                out.append((" ", k, it, hf))
                prev_ws = True
        else:
            out.append((ch, k, it, hf))
            prev_ws = False
    lead = 0
    while lead < len(out) and out[lead][0] == " ":
        lead += 1
    tail = len(out)
    while tail > lead and out[tail - 1][0] == " ":
        tail -= 1
    shift = lead
    out = out[lead:tail]
    text = "".join(c for c, _, _, _ in out)

    def runs(idx):
        spans = []
        i = 0
        while i < len(out):
            v = out[i][idx]
            if v is None:
                i += 1
                continue
            j = i
            while j < len(out) and out[j][idx] == v:
                j += 1
            a, b = i, j
            while a < b and text[a] == " ":
                a += 1
            while b > a and text[b - 1] == " ":
                b -= 1
            if b > a:
                spans.append((a, b, v))
            i = j
        return spans

    emphasis = [{"start": a, "end": b, "kind": v, "text": text[a:b]} for a, b, v in runs(1)]
    list_spans = [{"start": a, "end": b, "list_type": v[0], "list_id": v[1],
                   "item_index": v[2], "text": text[a:b]} for a, b, v in runs(2)]
    links = [{"start": a, "end": b, "href": v, "text": text[a:b]} for a, b, v in runs(3)]

    line_breaks = []
    images = []
    for off, kind, payload in points:
        o = min(max(off - shift, 0), len(text))
        if kind == "br":
            line_breaks.append(o)
        else:
            images.append({"offset": o, "src": payload.get("src"), "alt": payload.get("alt")})

    return text, emphasis, {"list_spans": list_spans, "line_breaks": line_breaks,
                            "images": images, "links": links}


if __name__ == "__main__":
    sys.exit(main())
