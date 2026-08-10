"""Validate every L1 record against l1/schema.json. Non-zero exit = build fails.

Runs the JSON Schema, then the invariants a schema cannot express: that key
sets are byte-identical across all 1902 records, that section offsets slice
their pane exactly, and that declared lengths match.

If data/l1/derived.jsonl exists it is validated too: against
l1/derived_schema.json, and for alignment -- same files in the same order as
records.jsonl, one verdict per section matched by (pane, index), and boundary
offsets inside the report pane.

    uv run --with jsonschema python l1/validate.py
"""

import json
import pathlib
import sys

from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECORDS = ROOT / "data" / "l1" / "records.jsonl"
DERIVED = ROOT / "data" / "l1" / "derived.jsonl"
PDF_RECORDS = ROOT / "data" / "l1" / "pdf_records.jsonl"
SCHEMA = ROOT / "l1" / "schema.json"
DERIVED_SCHEMA = ROOT / "l1" / "derived_schema.json"
PDF_SCHEMA = ROOT / "l1" / "pdf_schema.json"

MAX_REPORT = 20


def key_signature(obj, prefix=""):
    """Recursive key set for the fixed-shape parts of a record."""
    keys = set()
    for k, v in obj.items():
        path = f"{prefix}.{k}" if prefix else k
        keys.add(path)
        # Recurse into objects only; arrays hold repeated items whose count
        # legitimately varies between records.
        if isinstance(v, dict):
            keys |= key_signature(v, path)
    return keys


def validate_derived(failures, section_shapes, report_lengths, record_order):
    """derived.jsonl: schema, key uniformity, and alignment with records."""
    validator = Draft202012Validator(json.loads(DERIVED_SCHEMA.read_text(encoding="utf-8")))
    signatures = {}
    order = []
    n = 0
    with DERIVED.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            n += 1
            name = d.get("file", f"derived line {lineno}")
            order.append(name)
            for err in validator.iter_errors(d):
                failures.append(
                    (name, "derived-schema", "/".join(str(p) for p in err.absolute_path) or "(root)", err.message)
                )
            signatures.setdefault(frozenset(key_signature(d)), []).append(name)

            got = [(s["pane"], s["index"]) for s in d["sections"]]
            if got != section_shapes.get(name):
                failures.append((name, "derived-invariant", "sections",
                                 "verdicts do not align 1:1 with the record's sections"))
            off = d["abstract_boundary"]["offset"]
            if off is not None and not (0 <= off <= report_lengths.get(name, -1)):
                failures.append((name, "derived-invariant", "abstract_boundary",
                                 f"offset {off} outside report pane text"))
    if order != record_order:
        failures.append(("(corpus)", "derived-invariant", "order",
                         "derived.jsonl does not list the same files in the same order as records.jsonl"))
    if len(signatures) != 1:
        failures.append(("(corpus)", "derived-invariant", "key_signature",
                         "derived records do not share one key set"))
    print(f"derived validated : {n}")


def validate_pdf_records(failures):
    """pdf_records.jsonl: schema, key uniformity, and the invariants a schema
    cannot express — span offsets inside their line, contiguous page numbers,
    counts that add up."""
    validator = Draft202012Validator(json.loads(PDF_SCHEMA.read_text(encoding="utf-8")))
    signatures = {}
    n = 0
    with PDF_RECORDS.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            n += 1
            name = rec.get("file", f"pdf line {lineno}")
            for err in validator.iter_errors(rec):
                failures.append(
                    (name, "pdf-schema", "/".join(str(p) for p in err.absolute_path) or "(root)", err.message)
                )
            signatures.setdefault(frozenset(key_signature(rec)), []).append(name)

            pnos = [p["page_no"] for p in rec["pages"]]
            if pnos != list(range(1, len(pnos) + 1)):
                failures.append((name, "pdf-invariant", "pages", f"page_no not contiguous from 1: {pnos[:8]}"))
            n_lines = 0
            for page in rec["pages"]:
                for i, ln in enumerate(page["lines"]):
                    n_lines += 1
                    for sp in ln["spans"]:
                        if not (0 <= sp["start"] <= sp["end"] <= len(ln["text"])):
                            failures.append((name, "pdf-invariant", f"p{page['page_no']} line {i}",
                                             "span offsets outside line text"))
            if n_lines != rec["counts"]["lines"]:
                failures.append((name, "pdf-invariant", "counts.lines", "does not match the emitted lines"))
            if not rec["verification"]["case_number_found"]:
                failures.append((name, "pdf-invariant", "verification",
                                 "expected case number not found in flow_text"))
    if len(signatures) != 1:
        failures.append(("(pdf corpus)", "pdf-invariant", "key_signature",
                         "pdf records do not share one key set"))
    print(f"pdf records validated : {n}")


def main():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    failures = []
    signatures = {}
    section_shapes = {}
    report_lengths = {}
    record_order = []
    n = 0

    with RECORDS.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            n += 1
            name = rec.get("file", f"line {lineno}")
            record_order.append(name)
            section_shapes[name] = [(s["pane"], s["index"]) for s in rec["sections"]]
            report_lengths[name] = rec["panes"]["report"]["text_length"]

            for err in validator.iter_errors(rec):
                failures.append(
                    (name, "schema", "/".join(str(p) for p in err.absolute_path) or "(root)", err.message)
                )

            # --- invariants the schema cannot express -----------------------
            sig = frozenset(key_signature(rec))
            signatures.setdefault(sig, []).append(name)

            for pane_name in ("summary", "report"):
                pane = rec["panes"][pane_name]
                if len(pane["text"]) != pane["text_length"]:
                    failures.append((name, "invariant", f"panes.{pane_name}", "text_length != len(text)"))

            per_pane = {}
            for sec in rec["sections"]:
                pane = rec["panes"][sec["pane"]]
                if pane["text"][sec["char_start"]:sec["char_end"]] != sec["text"]:
                    failures.append(
                        (name, "invariant", f"sections[{sec['index']}]",
                         f"offsets do not slice {sec['pane']} pane text exactly")
                    )
                if sec["char_end"] < sec["char_start"]:
                    failures.append((name, "invariant", f"sections[{sec['index']}]", "char_end < char_start"))
                if len(sec["text"]) != sec["text_length"]:
                    failures.append((name, "invariant", f"sections[{sec['index']}]", "text_length != len(text)"))
                per_pane.setdefault(sec["pane"], []).append(sec["index"])

            for pane_name, idxs in per_pane.items():
                if idxs != list(range(len(idxs))):
                    failures.append((name, "invariant", f"sections/{pane_name}", f"indices not contiguous from 0: {idxs[:8]}"))

    print(f"records validated : {n}")
    print(f"distinct key signatures across all records : {len(signatures)}")
    if len(signatures) != 1:
        print("  KEY SHAPE IS NOT UNIFORM -- differing groups:")
        ref = max(signatures.items(), key=lambda kv: len(kv[1]))[0]
        for sig, files in signatures.items():
            if sig == ref:
                continue
            print(f"    {len(files)} file(s), e.g. {files[:3]}")
            print(f"      missing vs majority: {sorted(ref - sig)[:10]}")
            print(f"      extra   vs majority: {sorted(sig - ref)[:10]}")
        failures.append(("(corpus)", "invariant", "key_signature", "records do not share one key set"))

    if DERIVED.exists():
        validate_derived(failures, section_shapes, report_lengths, record_order)
    else:
        print("derived.jsonl not present -- skipped (run l1/derive.py)")

    if PDF_RECORDS.exists():
        validate_pdf_records(failures)
    else:
        print("pdf_records.jsonl not present -- skipped (run l1/build_pdf.py)")

    if failures:
        print(f"\nFAILURES: {len(failures)}")
        for f in failures[:MAX_REPORT]:
            print(f"  {f[0]}  [{f[1]}]  {f[2]}: {f[3]}")
        if len(failures) > MAX_REPORT:
            print(f"  ... and {len(failures) - MAX_REPORT} more")
        return 1

    print("\nOK: records (and derived, if present) conform to their schemas and every invariant holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
