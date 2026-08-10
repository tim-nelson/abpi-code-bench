"""Fetch the case-report PDFs for cases whose HTML report pane is wrong or absent.

Driven by investigation/pdf_needed.json (13 cases as of 2026-08-01: 4 whose
report pane belongs to a different case, 9 whose report exists only as a PDF).
Writes data/pdf/<html-stem>.pdf plus data/pdf/manifest.jsonl with url, sha256,
size and %PDF magic check. Same politeness as the HTML scrape (5s delay,
identifying UA). Idempotent: files already on disk are not re-fetched.

Verification of the downloads (first-page case number vs expected case) was
done by hand on 2026-08-01 and recorded in the manifest's `verification`
field. One caveat lives there too: AUTH-2063-10-07.pdf is the full Code of
Practice Review No 59 (170pp); the case itself is at pdf pages 144-147,
extracted to AUTH-2063-10-07__pages144-147.pdf with ghostscript
(`gs -sDEVICE=pdfwrite -dFirstPage=144 -dLastPage=147`).
"""

import hashlib
import json
import time
import urllib.request

from common import CRAWL_DELAY, DATA, ROOT, USER_AGENT

NEEDED = ROOT / "investigation" / "pdf_needed.json"
OUT_DIR = DATA / "pdf"
MANIFEST = OUT_DIR / "manifest.jsonl"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = json.loads(NEEDED.read_text(encoding="utf-8"))
    existing = {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[row["file"]] = row

    rows = []
    fetched = 0
    for entry in entries:
        stem = entry["file"].removesuffix(".html")
        for i, url in enumerate(entry["pdf_urls"]):
            suffix = "" if len(entry["pdf_urls"]) == 1 else f"__{i}"
            dest = OUT_DIR / f"{stem}{suffix}.pdf"
            if dest.exists():
                # Keep the existing manifest row (it may carry verification
                # notes); fall back to a fresh one if the file predates it.
                row = existing.get(dest.name)
                if row:
                    rows.append(row)
                    continue
                data = dest.read_bytes()
                status = "already_on_disk"
            else:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                try:
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        data = resp.read()
                        status = f"http_{resp.status}"
                except Exception as e:  # noqa: BLE001 -- log and continue, like fetch_cases
                    print(f"FAIL {url}: {e}")
                    rows.append({"file": dest.name, "url": url, "error": str(e)})
                    time.sleep(CRAWL_DELAY)
                    continue
                dest.write_bytes(data)
                fetched += 1
                time.sleep(CRAWL_DELAY)
            rows.append({
                "file": dest.name,
                "html_file": entry["file"],
                "case_number": (entry["case_number"] or "").strip(),
                "reason": entry["reason"].split(" ")[0],
                "url": url,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "magic_ok": data[:5] == b"%PDF-",
                "status": status,
            })
            print(f"{status:>16}  {len(data):>9}  {dest.name}")

    # Preserve rows for derived files (e.g. the 2063 page extract) that are
    # not driven by pdf_needed.json.
    for name, row in existing.items():
        if not any(r["file"] == name for r in rows):
            rows.append(row)

    with MANIFEST.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"{fetched} fetched; manifest: {len(rows)} rows -> {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
