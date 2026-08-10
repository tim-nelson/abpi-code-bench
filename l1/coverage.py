"""Coverage check: is every visible token in <main> present in the L1 record?

This is the check that entitles the corpus to retire the HTML. For each page,
the whole <main> region (hero, info-holder, tab panes, aside -- everything a
reader sees for the case) is run through the SAME extractor the build uses,
tokenised, and compared as a multiset against the record's captured fields:

    panes.summary.text + panes.report.text
    identity.h1_text + identity.h2_text
    info_holder labels and values
    aside_links[].text

The only tolerated difference is the tab chrome, byte-uniform across all
1902 pages ('Case summary' / 'Full report'). Any other token in <main> that
the record cannot account for is reported as a coverage failure.

Trusting text_and_spans here is deliberate: extractor fidelity was verified
externally (verify/report.md, three blind rounds); this check targets REGION
coverage -- whether any part of the page escaped capture entirely.

    python3 l1/coverage.py            # non-zero exit = coverage hole
"""

import json
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML_DIR = ROOT / "data" / "html"
RECORDS = ROOT / "data" / "l1" / "records.jsonl"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build import text_and_spans  # noqa: E402  (the one extractor)

SCRIPT_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
# The tab navigation is the one piece of <main> deliberately not captured:
# measured byte-uniform on all 1902 pages, so it carries no case information.
CHROME = Counter("Case summary Full report".split())

MAX_REPORT = 15


def tokens(s):
    return Counter((s or "").split())


def main_region(raw):
    i = raw.find("<main")
    j = raw.find("</main>")
    if i == -1 or j == -1:
        return None
    return SCRIPT_RE.sub(" ", raw[i:j])


def record_tokens(rec):
    c = Counter()
    c += tokens(rec["panes"]["summary"]["text"])
    c += tokens(rec["panes"]["report"]["text"])
    c += tokens(rec["identity"]["h1_text"])
    c += tokens(rec["identity"]["h2_text"])
    for row in rec["info_holder"]:
        c += tokens(row["label"])
        c += tokens(row["value"])
    for link in rec["aside_links"]:
        c += tokens(link["text"])
    for h in rec["aside_headings"]:
        c += tokens(h["text"])
    return c


def main():
    failures = []
    n = 0
    with RECORDS.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            n += 1
            raw = (HTML_DIR / rec["file"]).read_text(encoding="utf-8", errors="replace")
            region = main_region(raw)
            if region is None:
                failures.append((rec["file"], "<main> region not found"))
                continue
            page = tokens(text_and_spans(region)[0])
            missing = page - record_tokens(rec) - CHROME
            if missing:
                sample = " | ".join(f"{t}×{k}" for t, k in missing.most_common(6))
                failures.append((rec["file"], f"{sum(missing.values())} tokens uncovered: {sample}"))

    print(f"checked {n} files against their records")
    if failures:
        print(f"COVERAGE HOLES in {len(failures)} files:")
        for f, msg in failures[:MAX_REPORT]:
            print(f"  {f}: {msg}")
        if len(failures) > MAX_REPORT:
            print(f"  ... and {len(failures) - MAX_REPORT} more")
        return 1
    print("OK: every visible token in every <main> region is present in its record.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
