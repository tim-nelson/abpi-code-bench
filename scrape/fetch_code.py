"""Step 2: fetch the ABPI Code texts themselves, per Code year.

The case scrape tells us *which* clause was ruled on; it does not tell us what
that clause said. Bench T1 needs to display the clause text from the applicable
Code year, T6 compares the same conduct across two years' wording, and L2 C5
needs a commencement table to infer the Code year for the 54 cases that carry
none. This fetches the source material for all three.

What the site actually publishes (measured 2026-08-02, see data/code/README.md):

  * Six *interactive* Codes -- 2014, 2015, 2016, 2019, 2021, 2024 -- one HTML
    page per clause. Two page templates: a legacy one (2014/2015/2016/2019,
    `div.section-block` + `div.rte-content`) and an enhanced one (2021/2024,
    `div.sub-clause-section` + `div.sub-clause-content`). Both carry the same
    `clause-preview-numbers` anchor nav, which is what we enumerate subclauses
    from, so one extractor handles both.
  * Code editions as PDFs on /about-us/publications/ (category "CODE OF
    PRACTICE"). Saved raw and manifested; NOT parsed -- a PDF clause parser is
    a later task.

Outputs (all under data/code/):
  manifest.jsonl   one row per resource: url, file, sha256, bytes, fetched_at,
                   code_year, kind (landing|year_index|clause_page|code_page|pdf)
  html/, pdf/      full response bytes, exactly as received
  clauses.jsonl    one row per clause page of an interactive year
  code_years.json  what the site states about each edition + the corpus's own
                   observed complaint-date range per Applicable Code year

Discovery is deliberately cheap and bounded -- we enumerate before we fetch,
and never crawl blindly:
  1. data/l1/records.jsonl clause chips give ~360 URLs already known to exist
     (free, no requests).
  2. /the-code/ and /the-code/previous-abpi-codes-of-practice/ give the set of
     editions.
  3. Each edition's own pages carry the full clause index for that edition
     (the enhanced template inlines a popup index; the legacy template lists
     clauses on the edition landing page).
The union of those three is the fetch list.

Idempotent and resumable: a resource whose file is already on disk is never
re-requested, and discovery re-reads the saved landing pages from disk rather
than the network, so a second run makes zero requests. Extraction always
re-runs from disk (it is a pure function of the saved HTML). Failures are
logged and never halt the run. 5s crawl delay on every request.

Usage:
  python3 fetch_code.py                  # everything outstanding, then extract
  python3 fetch_code.py --dry-run        # show what would be fetched
  python3 fetch_code.py --limit 5        # stop after 5 fetches
  python3 fetch_code.py --extract-only   # rebuild clauses.jsonl from disk
  python3 fetch_code.py --refresh-landing  # re-fetch landing pages (site changed)
  python3 fetch_code.py --locked         # recover the publication-locked bytes

``--locked`` bypasses discovery and treats the tracked manifest as immutable.
It is the public reproduction path: every response must match the recorded
byte count and SHA-256 before it is written.  The normal discovery mode remains
available for creating a later corpus.
"""

import argparse
import hashlib
import html as html_mod
import json
import re
import sys
import time

from common import (
    CRAWL_DELAY,
    DATA,
    append_jsonl,
    fetch,
    log_failure,
    read_jsonl,
    utc_now,
)

BASE = "https://www.pmcpa.org.uk"
OUT = DATA / "code"
HTML_DIR = OUT / "html"
PDF_DIR = OUT / "pdf"
MANIFEST = OUT / "manifest.jsonl"
CLAUSES = OUT / "clauses.jsonl"
CODE_YEARS = OUT / "code_years.json"
L1_RECORDS = DATA / "l1" / "records.jsonl"

# Entry points. /the-code/ carries the current edition, previous-abpi-codes
# lists the interactive back-catalogue, publications holds the PDF editions.
SEED_LANDING = ["/the-code/", "/the-code/previous-abpi-codes-of-practice/"]
PUBLICATIONS = "/about-us/publications/"

# ---------------------------------------------------------------- html bits

MAIN_RE = re.compile(r"<main\b.*?</main>", re.S | re.I)
HREF_RE = re.compile(r'href="([^"]+)"', re.I)
A_RE = re.compile(r"<a\b[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S | re.I)
H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.S | re.I)
TAG_DIV = re.compile(r"<(/?)div\b", re.I)
PAGER_RE = re.compile(r'href="\?page=(\d+)"')

# Present in both page templates: the little "Clause | 7.1 7.2 7.3 ..." nav.
# It is the only place that maps a section's anchor id to its subclause number,
# so it is the enumeration hook for the extractor.
ANCHOR_RE = re.compile(r'<a href="#(\d+)" class="js-anchor-link">\s*([^<]*?)\s*</a>')
# Legacy template (2014/2015/2016/2019) vs enhanced (2021/2024).
BLOCK_RE = re.compile(r'<div id="(\d+)" class="(section-block|sub-clause-section)"')
SUBCLAUSE_CONTENT_RE = re.compile(r'<div class="sub-clause-content rte-content"')
RTE_RE = re.compile(r'<div class="rte-content"')
SLIDE_RE = re.compile(r'<div class="slide rte-content"')
SLIDE_HEADING_RE = re.compile(r"<a\b[^>]*>(.*?)</a>\s*<div class=\"slide rte-content\"", re.S)

BLOCK_TAGS = re.compile(r"</(p|div|li|tr|h[1-6]|blockquote)>|<br\s*/?>", re.I)

# "Clause 2 Upholding Confidence in the Industry"  (enhanced)
# "Clause 7 - Information, Claims and Comparisons" (legacy)
CLAUSE_H1_RE = re.compile(r"^Clause\s+(\d+[A-Za-z]?)\s*[-–—:]?\s*(.*)$", re.I)


def text_of(fragment):
    """Visible text, block structure kept as newlines, whitespace collapsed."""
    s = BLOCK_TAGS.sub("\n", fragment)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    s = s.replace(" ", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def div_extent(text, start):
    """Inner span of the <div ...> whose '<' is at `start`, brace-matched."""
    try:
        open_end = text.index(">", start) + 1
    except ValueError:
        return None
    depth = 1
    for m in TAG_DIV.finditer(text, open_end):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return open_end, m.start()
    return None


def main_of(html):
    m = MAIN_RE.search(html)
    return m.group(0) if m else html


# ---------------------------------------------------------------- url bits

# /the-code/2024-interactive-abpi-code-of-practice/...
# /the-code/previous-abpi-codes-of-practice/2021-interactive-abpi-code-of-practice/...
# /the-code/previous-abpi-codes-of-practice/interactive-2014-code/...
YEAR_SEG_RE = re.compile(r"/((?:(\d{4})-interactive|interactive-(\d{4}))[^/]*)/")


def split_year_url(path):
    """(code_year, edition_root, remainder) for a Code page path, else (None,..)."""
    m = YEAR_SEG_RE.search(path)
    if not m:
        return None, None, None
    year = int(m.group(2) or m.group(3))
    root = path[: m.end()]
    return year, root, path[m.end():]


def norm_path(href, referer_path="/"):
    """Absolute site path, fragment and query dropped, trailing slash forced."""
    href = html_mod.unescape(href).strip()
    if href.startswith(BASE):
        href = href[len(BASE):]
    if href.startswith("http://") or href.startswith("https://"):
        return None  # off-site
    href = href.split("#")[0].split("?")[0]
    if not href or href == "/":
        return href or None
    if not href.startswith("/"):
        href = referer_path.rsplit("/", 1)[0] + "/" + href
    if "." not in href.rsplit("/", 1)[-1] and not href.endswith("/"):
        href += "/"
    return href


def slugify(path):
    s = path.strip("/").replace("/", "_")
    return re.sub(r"[^A-Za-z0-9._-]", "-", s) or "index"


def html_filename(path):
    year, root, rest = split_year_url(path)
    if year:
        tail = slugify(rest) if rest.strip("/") else "index"
        return f"{year}__{tail}.html"
    return "site__" + slugify(path) + ".html"


def pdf_filename(path):
    """/media/3406/2021-abpi-code-of-practice.pdf -> 3406__2021-abpi-code-of-practice.pdf

    The media id is kept because it is the only thing guaranteeing uniqueness --
    the site has re-uploaded the same basename under different ids.
    """
    parts = [p for p in path.split("/") if p]
    stem = parts[-1]
    ident = parts[-2] if len(parts) >= 2 else ""
    return re.sub(r"[^A-Za-z0-9._-]", "-", f"{ident}__{stem}" if ident else stem)


# ---------------------------------------------------------------- discovery


def harvest_from_l1():
    """Clause URLs the corpus itself links to -- free, and proof they exist."""
    found = {}
    if not L1_RECORDS.exists():
        return found
    with L1_RECORDS.open(encoding="utf-8") as fh:
        for line in fh:
            if "/the-code/" not in line:
                continue
            rec = json.loads(line)
            for item in rec.get("info_holder") or []:
                vh = item.get("value_html") or ""
                if "/the-code/" not in vh:
                    continue
                for href in HREF_RE.findall(vh):
                    p = norm_path(href)
                    if p and "/the-code/" in p:
                        found[p] = found.get(p, 0) + 1
    return found


def links_in(html, keep):
    """Site paths from <main>, filtered by predicate `keep`."""
    out = []
    for href in HREF_RE.findall(main_of(html)):
        p = norm_path(href)
        if p and keep(p) and p not in out:
            out.append(p)
    return out


# ---------------------------------------------------------------- fetching


class Run:
    """Fetch bookkeeping: disk is the source of truth, manifest is the index."""

    def __init__(self, args):
        self.args = args
        self.prior = {}
        for row in read_jsonl(MANIFEST):
            self.prior[row["url"]] = row
        self.rows = {}
        self.fetched = self.skipped = self.failed = 0

    def budget_spent(self):
        return self.args.limit is not None and self.fetched + self.failed >= self.args.limit

    def get(self, path, kind, code_year=None, extra=None, binary=False):
        """Fetch `path` unless its file is on disk. Returns bytes or None."""
        url = BASE + path
        directory = PDF_DIR if binary else HTML_DIR
        name = pdf_filename(path) if binary else html_filename(path)
        dest = directory / name

        if url in self.rows:  # already handled this run
            return self.rows[url].get("_data")

        if dest.exists() and not (kind in ("landing", "year_index") and self.args.refresh_landing):
            data = dest.read_bytes()
            row = dict(self.prior.get(url) or {})
            row.update({
                "url": url, "file": f"{directory.name}/{name}", "kind": kind,
                "code_year": code_year, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
            row.setdefault("fetched_at", None)
            row.setdefault("http_status", None)
            if extra:
                row.update(extra)
            row["_data"] = data
            self.rows[url] = row
            self.skipped += 1
            return data

        if self.args.dry_run:
            print(f"WOULD FETCH [{kind}] {url}")
            self.fetched += 1
            return None
        if self.budget_spent():
            return None

        try:
            status, raw, final_url = fetch(url)
        except Exception as exc:  # noqa: BLE001 -- never halt the run on one page
            log_failure("code", url, f"{type(exc).__name__}: {exc}")
            self.rows[url] = {
                "url": url, "file": None, "kind": kind, "code_year": code_year,
                "error": f"{type(exc).__name__}: {exc}", "fetched_at": utc_now(),
            }
            self.failed += 1
            time.sleep(CRAWL_DELAY)
            return None

        row = {
            "url": url, "final_url": final_url, "file": None, "kind": kind,
            "code_year": code_year, "http_status": status, "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "fetched_at": utc_now(),
        }
        if extra:
            row.update(extra)

        if status != 200:
            log_failure("code", url, f"HTTP {status}")
            self.rows[url] = row
            self.failed += 1
            time.sleep(CRAWL_DELAY)
            return None

        if binary and raw[:5] != b"%PDF-":
            row["magic_ok"] = False
            log_failure("code", url, "not a PDF (magic bytes)")
        elif binary:
            row["magic_ok"] = True

        directory.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)  # raw bytes, unmodified
        row["file"] = f"{directory.name}/{name}"
        row["_data"] = raw
        self.rows[url] = row
        self.fetched += 1
        print(f"  {status}  {len(raw):>8,}b  [{kind}] {name}")
        time.sleep(CRAWL_DELAY)
        return raw

    def write_manifest(self):
        rows = []
        for url, row in self.rows.items():
            row = {k: v for k, v in row.items() if k != "_data"}
            rows.append(row)
        rows.sort(key=lambda r: (r.get("code_year") or 0, r.get("kind") or "", r["url"]))
        OUT.mkdir(parents=True, exist_ok=True)
        with MANIFEST.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return rows


def as_text(data):
    return data.decode("utf-8", errors="replace") if data else ""


def recover_locked(args):
    """Recover exactly the raw Code resources pinned by the tracked manifest."""
    manifest_rows = read_jsonl(MANIFEST)
    latest = {}
    for row in manifest_rows:
        latest[row["url"]] = row
    manifest_rows = sorted(
        latest.values(),
        key=lambda r: (r.get("code_year") or 0, r.get("kind") or "", r["url"]),
    )
    # Three discovered CMS links are pinned 404s.  They are durable exclusions,
    # not missing source files.  Older on-disk rows can have http_status null;
    # file+bytes+hash is their complete publication lock.
    rows = [row for row in manifest_rows if row.get("file")]
    exclusions = [row for row in manifest_rows if not row.get("file")]
    malformed = [
        row.get("url")
        for row in rows
        if not isinstance(row.get("bytes"), int)
        or not re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", ""))
    ]
    malformed.extend(
        row.get("url")
        for row in exclusions
        if row.get("http_status") in (None, 200)
    )
    if malformed:
        print(f"Publication lock has {len(malformed)} incomplete row(s).")
        return manifest_rows, 0, 0, len(malformed), False

    fetched = skipped = failed = requests = 0
    stopped_early = False
    for row in rows:
        destination = OUT / row["file"]
        data = destination.read_bytes() if destination.exists() else None
        if data is not None and (
            len(data) == row["bytes"]
            and hashlib.sha256(data).hexdigest() == row["sha256"]
        ):
            skipped += 1
            continue

        if args.limit is not None and requests >= args.limit:
            stopped_early = True
            break
        if args.dry_run:
            state = "corrupt" if data is not None else "missing"
            print(f"WOULD FETCH [{state}; {row['kind']}] {row['url']} -> {row['file']}")
            requests += 1
            continue

        try:
            status, candidate, _ = fetch(row["url"])
        except Exception as exc:  # noqa: BLE001 - report the complete gap set
            log_failure(
                "code-locked", row["url"], f"{type(exc).__name__}: {exc}"
            )
            failed += 1
            requests += 1
            time.sleep(CRAWL_DELAY)
            continue
        requests += 1
        got = hashlib.sha256(candidate).hexdigest()
        if status != 200:
            log_failure("code-locked", row["url"], f"HTTP {status}")
            failed += 1
        elif len(candidate) != row["bytes"] or got != row["sha256"]:
            log_failure(
                "code-locked", row["url"], "response differs from publication lock"
            )
            failed += 1
        elif row["kind"] == "pdf" and candidate[:5] != b"%PDF-":
            log_failure("code-locked", row["url"], "response is not a PDF")
            failed += 1
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(candidate)
            fetched += 1
            print(
                f"  {status}  {len(candidate):>8,}b  [{row['kind']}] "
                f"{row['file']}  [locked]"
            )
        time.sleep(CRAWL_DELAY)

    complete = fetched + skipped == len(rows)
    if args.dry_run:
        print(
            f"publication-locked Code plan: {len(rows)} resources, "
            f"{len(exclusions)} pinned exclusion(s), {skipped} present, "
            f"{requests} fetch(es) needed"
        )
    else:
        print(f"fetched {fetched}, present {skipped}, failed {failed}")
        if stopped_early:
            print("stopped at --limit; rerun the same command to resume")
    return manifest_rows, fetched, skipped, failed, complete


def discover_and_fetch(run):
    """Enumerate editions, then clause pages, then PDFs -- fetching as we go."""
    seeded = harvest_from_l1()
    print(f"{len(seeded)} clause URLs harvested from L1 records (0 requests)")

    # 1. Landing pages -> which editions exist.
    editions = {}  # code_year -> edition root path
    for path in SEED_LANDING:
        html = as_text(run.get(path, "landing"))
        for p in links_in(html, lambda p: "/the-code/" in p):
            year, root, rest = split_year_url(p)
            if year and not rest.strip("/"):
                editions.setdefault(year, root)

    # Editions the corpus links to but the landing pages no longer list.
    for p in seeded:
        year, root, _ = split_year_url(p)
        if year:
            editions.setdefault(year, root)

    print(f"editions discovered: {sorted(editions)}")

    # 2. Edition index pages -> the full clause list for that edition.
    pages = {}  # path -> code_year
    for year, root in sorted(editions.items()):
        html = as_text(run.get(root, "year_index", code_year=year))
        for p in links_in(html, lambda p, r=root: p.startswith(r) and p != r):
            pages[p] = year

    for p, n in seeded.items():
        year, root, rest = split_year_url(p)
        if year and rest.strip("/"):
            pages.setdefault(p, year)

    # The enhanced template inlines the whole edition index on every clause
    # page, so one fetched page per edition completes that edition's list.
    # Iterate to a fixed point over pages already on disk.
    for _ in range(3):
        grown = False
        for p, year in list(pages.items()):
            dest = HTML_DIR / html_filename(p)
            if not dest.exists():
                continue
            html = dest.read_bytes().decode("utf-8", errors="replace")
            root = editions[year]
            for q in links_in(html, lambda q, r=root: q.startswith(r) and q != r):
                if q not in pages:
                    pages[q] = year
                    grown = True
        if not grown:
            break

    print(f"{len(pages)} edition pages to fetch/verify")

    # 3. Fetch every edition page.
    for p, year in sorted(pages.items(), key=lambda kv: (kv[1], kv[0])):
        if run.budget_spent():
            break
        kind = "clause_page" if re.search(r"/clause-\d", p) else "code_page"
        run.get(p, kind, code_year=year)

    # One more index pass: pages fetched just now may name pages we had not
    # seen (relevant for the enhanced template's inlined index).
    extra = {}
    for p, year in pages.items():
        dest = HTML_DIR / html_filename(p)
        if not dest.exists():
            continue
        html = dest.read_bytes().decode("utf-8", errors="replace")
        root = editions[year]
        for q in links_in(html, lambda q, r=root: q.startswith(r) and q != r):
            if q not in pages:
                extra[q] = year
    for p, year in sorted(extra.items(), key=lambda kv: (kv[1], kv[0])):
        if run.budget_spent():
            break
        kind = "clause_page" if re.search(r"/clause-\d", p) else "code_page"
        run.get(p, kind, code_year=year)
        pages[p] = year

    # 4. Code PDFs. Two independent sources, unioned:
    #    (a) the per-edition "download the Code" button on every clause page,
    #    (b) the paginated publications listing.
    pdfs = {}  # path -> {code_year, title}
    for p, year in pages.items():
        dest = HTML_DIR / html_filename(p)
        if not dest.exists():
            continue
        html = dest.read_bytes().decode("utf-8", errors="replace")
        for href, label in A_RE.findall(main_of(html)):
            if not href.lower().endswith(".pdf"):
                continue
            q = norm_path(href)
            if q:
                pdfs.setdefault(q, {"code_year": year, "title": text_of(label)})

    first = as_text(run.get(PUBLICATIONS, "landing"))
    last_page = max([int(n) for n in PAGER_RE.findall(first)] or [1])
    print(f"publications listing: {last_page} pages")
    pub_html = [first]
    for n in range(2, last_page + 1):
        if run.budget_spent():
            break
        pub_html.append(as_text(run.get(f"{PUBLICATIONS}?page={n}", "landing")))

    for html in pub_html:
        for href, label in A_RE.findall(main_of(html)):
            if not href.lower().endswith(".pdf"):
                continue
            title = text_of(label)
            # Code editions only -- the listing also carries annual reports,
            # Code of Practice Review archives and guidance.
            if not re.search(r"\bcode\b", title, re.I):
                continue
            if re.search(r"review|annual report|guidance|template|framework", title, re.I):
                continue
            m = re.search(r"(19|20)\d{2}", title)
            q = norm_path(href)
            if q:
                pdfs.setdefault(q, {"code_year": int(m.group(0)) if m else None,
                                    "title": title})

    print(f"{len(pdfs)} Code PDFs to fetch/verify")
    for q, meta in sorted(pdfs.items(), key=lambda kv: (kv[1]["code_year"] or 0, kv[0])):
        if run.budget_spent():
            break
        run.get(q, "pdf", code_year=meta["code_year"],
                extra={"title": meta["title"]}, binary=True)

    return editions, pages


# ---------------------------------------------------------------- extraction


def parse_clause_page(html, url, sha256):
    """-> (row, note). row is None when the page's structure defeats us.

    Both templates are handled: the anchor nav gives (anchor id -> subclause
    number); the numbered blocks give the text. Nothing is repaired -- if a
    block's text does not come out, the subclause is recorded with text None
    and the page is flagged, never silently patched.
    """
    body = main_of(html)
    h1 = H1_RE.search(body)
    if not h1:
        return None, "no <h1>"
    page_title = text_of(h1.group(1))
    m = CLAUSE_H1_RE.match(page_title)
    if not m:
        return None, f"h1 is not a clause heading: {page_title!r}"
    clause_number, clause_title = m.group(1), m.group(2).strip()

    anchors = dict(ANCHOR_RE.findall(body))  # id -> "7.1"
    blocks = list(BLOCK_RE.finditer(body))
    if not blocks:
        return None, "no clause blocks (section-block / sub-clause-section)"

    subclauses, general = [], []
    for i, blk in enumerate(blocks):
        start = blk.start()
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(body)
        slice_ = body[start:end]
        anchor_id = blk.group(1)

        # The subclause text: the first content div of the block. The Cases
        # tab uses cases-* markup, and supplementary text uses
        # `slide rte-content`, so neither can be mistaken for it.
        cm = SUBCLAUSE_CONTENT_RE.search(slice_) or RTE_RE.search(slice_)
        text = None
        if cm:
            span = div_extent(slice_, cm.start())
            if span:
                text = text_of(slice_[span[0]:span[1]]) or None

        supp = []
        for sm in SLIDE_RE.finditer(slice_):
            span = div_extent(slice_, sm.start())
            if not span:
                continue
            hm = SLIDE_HEADING_RE.search(slice_, max(0, sm.start() - 400), sm.end())
            supp.append({
                "heading": text_of(hm.group(1)) if hm else None,
                "text": text_of(slice_[span[0]:span[1]]),
            })

        entry = {"number": anchors.get(anchor_id), "anchor": anchor_id,
                 "text": text, "supplementary_information": supp or None}
        if entry["number"] is None:
            # A block outside the anchor nav is the clause-level general
            # block (e.g. "Clause 7 General" supplementary info), not a
            # subclause. It carries no clause text of its own.
            general.append(entry)
        else:
            subclauses.append(entry)

    if not subclauses:
        return None, "anchor nav matched no clause block"
    missing = [s["number"] for s in subclauses if not s["text"]]

    full = "\n\n".join(s["text"] for s in subclauses if s["text"])
    row = {
        "code_year": None,  # filled by caller
        "clause_number": clause_number,
        "clause_title": clause_title,
        "text": full,
        "source_url": url,
        "sha256_of_source": sha256,
        "page_title": page_title,
        "subclause_numbers": [s["number"] for s in subclauses],
        "subclauses": subclauses,
        "general_supplementary": [g for g in general if g["supplementary_information"]] or None,
    }
    note = None
    if missing:
        note = f"subclauses with no extractable text: {missing}"
    if not full:
        return None, "no subclause text extracted"
    return row, note


def extract(manifest_rows):
    """Rebuild clauses.jsonl from the HTML on disk. Pure function of disk."""
    rows, skipped = [], []
    for m in manifest_rows:
        if m.get("kind") != "clause_page" or not m.get("file"):
            continue
        path = OUT / m["file"]
        if not path.exists():
            skipped.append({"url": m["url"], "reason": "file missing on disk"})
            continue
        html = path.read_bytes().decode("utf-8", errors="replace")
        row, note = parse_clause_page(html, m["url"], m["sha256"])
        if row is None:
            skipped.append({"url": m["url"], "reason": note})
            log_failure("code-extract", m["url"], note)
            continue
        row["code_year"] = m["code_year"]
        if note:
            row["note"] = note
        rows.append(row)

    rows.sort(key=lambda r: (r["code_year"], clause_sort_key(r["clause_number"])))
    with CLAUSES.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows, skipped


def clause_sort_key(n):
    m = re.match(r"(\d+)(.*)", n or "")
    return (int(m.group(1)), m.group(2)) if m else (999, n or "")


# ------------------------------------------------- commencement / year dates

# Only sentences the site actually states. We never infer a date here.
DATE_PHRASE_RE = re.compile(
    r"[^.\n]{0,200}?\b(?:come[s]? into (?:operation|effect|force)|came into "
    r"(?:operation|effect|force)|operative from|effective from|with effect from|"
    r"takes effect|took effect|commence[sd]? on|applies from)\b[^.\n]{0,200}\.",
    re.I,
)


def collect_code_years(manifest_rows, editions):
    """What the site states per edition + what the corpus itself shows.

    Two strictly separated things:
      `stated`   verbatim sentences scraped from the fetched pages -- evidence.
      `observed` the complaint-received date range per Applicable Code year in
                 our own 1,902 cases -- a measurement of how the years were
                 applied in practice, NOT an authority statement, and not a
                 commencement date. L2 C5 should treat it as a prior, and say
                 so in its `basis`.
    """
    stated = {}
    for m in manifest_rows:
        if m.get("kind") not in ("year_index", "clause_page", "code_page", "landing"):
            continue
        if not m.get("file") or not m["file"].startswith("html/"):
            continue
        path = OUT / m["file"]
        if not path.exists():
            continue
        txt = text_of(main_of(path.read_bytes().decode("utf-8", errors="replace")))
        for match in DATE_PHRASE_RE.finditer(txt):
            sent = match.group(0).strip()
            key = str(m.get("code_year"))
            stated.setdefault(key, [])
            if not any(e["sentence"] == sent for e in stated[key]):
                stated[key].append({"sentence": sent, "source_url": m["url"]})

    observed = {}
    if L1_RECORDS.exists():
        months = {n: i for i, n in enumerate(
            ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"], 1)}

        def iso(v):
            mm = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", (v or "").strip())
            if not mm or mm.group(2).lower() not in months:
                return None
            return f"{mm.group(3)}-{months[mm.group(2).lower()]:02d}-{int(mm.group(1)):02d}"

        with L1_RECORDS.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                fields = {i.get("label"): i.get("value") for i in rec.get("info_holder") or []}
                year = (fields.get("Applicable Code year") or "").strip()
                d = iso(fields.get("Complaint received"))
                if not re.fullmatch(r"\d{4}", year) or not d:
                    continue
                o = observed.setdefault(year, {"n": 0, "first_complaint_received": d,
                                               "last_complaint_received": d})
                o["n"] += 1
                o["first_complaint_received"] = min(o["first_complaint_received"], d)
                o["last_complaint_received"] = max(o["last_complaint_received"], d)

    pdfs = [{"code_year": m.get("code_year"), "title": m.get("title"),
             "file": m.get("file"), "url": m["url"]}
            for m in manifest_rows if m.get("kind") == "pdf"]

    doc = {
        "generated_at": utc_now(),
        "note": ("`stated` is verbatim from pmcpa.org.uk. `observed` is measured "
                 "from data/l1/records.jsonl (Applicable Code year vs Complaint "
                 "received) and describes how the PMCPA applied each edition to "
                 "the cases in our corpus -- it is NOT a commencement date and "
                 "must not be cited as one. No date on this page is inferred."),
        "interactive_editions": {str(y): BASE + r for y, r in sorted(editions.items())},
        "pdf_editions": sorted(pdfs, key=lambda p: (p["code_year"] or 0)),
        "stated": stated,
        "observed": dict(sorted(observed.items())),
    }
    CODE_YEARS.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    return doc


# ---------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="stop after N fetches")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--extract-only", action="store_true",
                    help="rebuild clauses.jsonl from disk, no requests")
    ap.add_argument("--refresh-landing", action="store_true",
                    help="re-fetch landing/index pages even if on disk")
    ap.add_argument(
        "--locked",
        action="store_true",
        help="recover exact raw bytes from the tracked publication manifest",
    )
    args = ap.parse_args()

    if args.locked and (args.extract_only or args.refresh_landing):
        ap.error("--locked cannot be combined with --extract-only/--refresh-landing")

    OUT.mkdir(parents=True, exist_ok=True)
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    if args.locked:
        rows, _fetched, _skipped, failed, complete = recover_locked(args)
        if args.dry_run or not complete:
            return 1 if failed else 0
        editions = {}
        for row in rows:
            if row.get("kind") == "year_index" and row.get("code_year"):
                editions[row["code_year"]] = row["url"][len(BASE):]
        clauses, skipped = extract(rows)
        collect_code_years(rows, editions)
    elif args.extract_only:
        rows = read_jsonl(MANIFEST)
        editions = {}
        for m in rows:
            if m.get("kind") == "year_index" and m.get("code_year"):
                editions[m["code_year"]] = m["url"][len(BASE):]
        clauses, skipped = extract(rows)
    else:
        run = Run(args)
        editions, _pages = discover_and_fetch(run)
        if args.dry_run:
            print(f"\ndry run: {run.fetched} would be fetched, {run.skipped} on disk")
            return 0
        rows = run.write_manifest()
        print(f"\nfetched {run.fetched}, on disk {run.skipped}, failed {run.failed}")
        clauses, skipped = extract(rows)
        collect_code_years(rows, editions)

    per_year = {}
    for r in clauses:
        per_year[r["code_year"]] = per_year.get(r["code_year"], 0) + 1
    print(f"clauses.jsonl: {len(clauses)} clause rows -> {CLAUSES}")
    for y in sorted(per_year):
        print(f"  {y}: {per_year[y]} clauses")
    if skipped:
        print(f"{len(skipped)} clause pages skipped (recorded in manifest, no row):")
        for s in skipped:
            print(f"  {s['url']}\n     {s['reason']}")
    print(f"manifest -> {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
