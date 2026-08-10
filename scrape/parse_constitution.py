"""Parse the PMCPA Constitution and Procedure into structured rows.

scrape/fetch_code.py saved the Constitution and Procedure alongside the Code
clauses -- 128 section pages across the six interactive editions (2014, 2015,
2016, 2019, 2021, 2024) -- but only the numbered Code clauses were ever parsed
into data/code/clauses.jsonl. The C&P is where the adjudication machinery is
defined: Panel and Appeal Board constitution, action on complaints, rulings,
sanctions, appeals, the abridged procedure. Nothing else in the repo states it.

    python3 scrape/parse_constitution.py     # -> data/code/constitution.jsonl

Both templates the site uses collapse to one here: every edition's C&P renders
as `div.section-block.rte-content`, one div per numbered paragraph, with a
`clause-preview-numbers` anchor nav enumerating those numbers. The nav is
written by the CMS from the section list, not from the body copy, so it is used
as a check on the body parse: if the paragraphs we extract disagree with the
numbers the nav advertises, the page is REFUSED rather than half-parsed.
"""

import hashlib
import json
import pathlib
import re
import sys
from collections import Counter
from html.parser import HTMLParser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402

CODE_DIR = common.DATA / "code"
OUT = CODE_DIR / "constitution.jsonl"


class BlockExtractor(HTMLParser):
    """Collect (a) nav paragraph numbers, (b) text of each section-block div.

    Depth-tracked rather than regex-sliced: section-block divs contain nested
    markup and a non-greedy /<div.*?<\\/div>/ would truncate at the first inner
    close tag.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.nav = [], []
        self._depth = 0          # div depth inside the current block, 0 = outside
        self._buf = []
        self._nav_tag = None     # tag that opened the nav list, closes it too
        self._nav_buf = []
        self._h1 = []
        self._in_h1 = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "div":
            if self._depth:
                self._depth += 1
            elif "section-block" in cls:
                self._depth = 1
                self._buf = []
        # The nav is a <ul class="clause-preview-numbers">, not a div -- match on
        # the class whatever the element, and close on that same tag name.
        if self._nav_tag is None and "clause-preview-numbers" in cls:
            self._nav_tag = tag
        elif self._nav_tag and tag == "a":
            self._nav_buf = []
        if tag == "h1":
            self._in_h1 = True
        elif self._depth and tag in ("p", "li", "br"):
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag == "div":
            if self._depth:
                self._depth -= 1
                if self._depth == 0:
                    self.blocks.append("".join(self._buf))
        if tag == self._nav_tag:
            self._nav_tag = None
        elif tag == "a" and self._nav_tag:
            t = "".join(self._nav_buf).strip()
            if t:
                self.nav.append(t)
        if tag == "h1":
            self._in_h1 = False

    def handle_data(self, data):
        if self._depth:
            self._buf.append(data)
        if self._nav_tag:
            self._nav_buf.append(data)
        if self._in_h1:
            self._h1.append(data)

    @property
    def h1(self):
        return re.sub(r"\s+", " ", "".join(self._h1)).strip()


def clean(text):
    text = text.replace("\xa0", " ").replace("•", "- ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# "5.1 When the Authority receives..." / "5.15 ..." -- the paragraph number is
# the leading token, emitted by the CMS inside <strong>.
# The separator after the number is not always whitespace: 2015 section 18
# renders "18.1The Authority is willing...". Accept a following capital or
# opening bracket as the boundary too.
NUM_RE = re.compile(r"^(\d+\.\d+[a-z]?)(?:\s+|(?=[A-Z(“\"]))(.*)$", re.S)
H1_RE = re.compile(r"^(\d+)\s+(.*)$")


def main():
    rows = [r for r in common.read_jsonl(CODE_DIR / "manifest.jsonl")
            if "constitution-and-procedure" in (r.get("url") or "")
            and r.get("file") and r.get("http_status") == 200]
    if not rows:
        sys.exit("REFUSING: no Constitution and Procedure rows in "
                 f"{CODE_DIR/'manifest.jsonl'}; run scrape/fetch_code.py first.")

    out, refused, stats = [], [], Counter()
    for r in rows:
        path = CODE_DIR / r["file"]
        if not path.exists():
            refused.append((r["file"], "file missing on disk"))
            continue
        raw = path.read_bytes()
        s = raw.decode("utf-8", errors="replace")

        # The manifest carries the edition the page was crawled under; trust it
        # over the filename prefix, which is only a naming convention.
        year = r["code_year"]
        p = BlockExtractor()
        p.feed(s)
        p.close()

        hm = H1_RE.match(p.h1)
        if not hm:
            # The C&P index page has no section number; it is a table of
            # contents, so it carries no paragraphs and is skipped by design.
            stats["index_pages_skipped"] += 1
            continue
        section_number, section_title = hm.group(1), hm.group(2).strip()

        # A block is not always "number, then text": several open with a bolded
        # sub-heading ("Complaints Not Proceeding") and carry the numbered
        # paragraph beneath it. So scan line by line for the number rather than
        # only matching the head of the block -- matching only the head silently
        # dropped 5.5, 5.7 and eight others.
        # Continuation lines may only attach to a paragraph started in the SAME
        # block. Attaching across blocks misattributed 2.2 to 2.1 in the 2019
        # and 2021 editions, where the source renders 2.2 without its number.
        paragraphs = []
        for b in p.blocks:
            t = clean(b)
            if not t:
                continue
            here, pending_heading = [], None
            for line in t.split("\n"):
                line = line.strip()
                if not line:
                    continue
                m = NUM_RE.match(line)
                if m:
                    here.append({"number": m.group(1), "heading": pending_heading,
                                 "text": m.group(2).strip()})
                    pending_heading = None
                elif here:
                    here[-1]["text"] += "\n" + line
                elif pending_heading is None and len(line) < 80:
                    pending_heading = line          # sub-heading above para 1
                else:
                    here.append({"number": None, "heading": pending_heading,
                                 "text": line})
                    pending_heading = None
            if not here and pending_heading:
                here.append({"number": None, "heading": None,
                             "text": pending_heading})
            paragraphs.extend(here)

        nav = [n for n in p.nav if re.fullmatch(r"\d+\.\d+[a-z]?", n)]
        got = [x["number"] for x in paragraphs if x["number"]]
        nav_note = None
        unnumbered = sum(1 for x in paragraphs if x["number"] is None)
        if not nav:
            # Legitimate only for the single-paragraph sections (Annual Report,
            # Time Periods): no sub-numbering exists, so the CMS emits no nav.
            if got:
                refused.append((path.name,
                                f"body yields numbered paragraphs {got} but the "
                                "page has no nav to check them against"))
                continue
            nav_note = "section has no numbered paragraphs and no nav"
            stats["sections_without_numbering"] += 1
        elif got != nav:
            # The nav is CMS-generated and is not always right: 2024 section 8
            # lists 8.4 twice and omits 8.3, while the body runs 8.1-8.7 clean.
            # Accept the body only when it is a strictly increasing, duplicate-free
            # run and the nav is the malformed one; otherwise this is our bug.
            nav_bad = len(set(nav)) != len(nav)
            body_ok = len(set(got)) == len(got) and sorted(
                got, key=lambda x: [int(v) for v in x.rstrip("abc").split(".")]) == got
            missing = [n for n in nav if n not in got]
            if nav_bad and body_ok:
                nav_note = f"source nav malformed: advertises {nav}, body yields {got}"
                stats["nav_defects_in_source"] += 1
            elif missing and len(missing) == unnumbered:
                # The text is on the page but the source omits its number, so we
                # keep the text and refuse to guess which number it carries.
                nav_note = (f"source omits the paragraph number for {missing}; "
                            f"{unnumbered} unnumbered block(s) retained, "
                            "number NOT inferred")
                stats["unnumbered_in_source"] += 1
            else:
                refused.append((path.name,
                                f"nav advertises {nav} but body yields {got}"))
                continue

        stats[f"year_{year}"] += 1
        out.append({
            "code_year": year,
            "section_number": section_number,
            "section_title": section_title,
            "paragraph_numbers": got,
            "paragraphs": paragraphs,
            "text": "\n\n".join(
                (f"{x['number']} {x['text']}" if x["number"] else x["text"])
                for x in paragraphs),
            "source_url": r["url"],
            "sha256_of_source": hashlib.sha256(raw).hexdigest(),
            "page_title": p.h1,
            "source_file": r["file"],
            "nav_note": nav_note,
        })

    if refused:
        print(f"REFUSING: {len(refused)} page(s) failed the nav cross-check:",
              file=sys.stderr)
        for f, why in refused:
            print(f"    {f}: {why}", file=sys.stderr)
        sys.exit("Fix the parser rather than emitting a partial file.")

    out.sort(key=lambda x: (x["code_year"], int(x["section_number"])))
    with OUT.open("w", encoding="utf-8") as fh:
        for rec in out:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    n_par = sum(len(r["paragraphs"]) for r in out)
    words = sum(len(r["text"].split()) for r in out)
    print(f"wrote {OUT.relative_to(common.ROOT)}: {len(out)} sections, "
          f"{n_par} paragraphs, {words:,} words")
    print("  sections per edition:",
          {k.replace('year_', ''): v for k, v in sorted(stats.items())
           if k.startswith('year_')})
    print(f"  index pages skipped (no section number): {stats['index_pages_skipped']}")


if __name__ == "__main__":
    main()
