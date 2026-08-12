"""Validate bench/items.jsonl. Non-zero exit = the item bank is not usable.

    uv run --with jsonschema python bench/validate.py
    uv run --with jsonschema python bench/validate.py --items bench/items.jsonl

Runs bench/item_schema.json, then the invariants a schema cannot express:

  * item_ids are the documented hash of (task, case, clause, segment refs)
  * every provenance ref slices its pane exactly, and the concatenation of
    those slices reproduces extract_text character for character
  * every quoted ref belongs to a segment in the source cases whose
    leakage_attest.clean is true and whose kind is complaint or response
  * T1 quotes complaint+response, T1-triage and T4 quote complaint only
  * metadata_shown is exactly the allowlist (plus the two T3-only keys, on T3)
  * cases sharing a source report share a split
  * a leakage TRIPWIRE over the quoted text: ruling/appeal/sanction vocabulary
    that should be impossible if the attest is right. The tripwire is not the
    attest and does not replace it -- a hit means either the attest is wrong or
    this word list is too broad, and a human decides which.
  * the bench/review/DEFECTS.md exclusions, re-derived from the CASES rather
    than trusted from the generator: no item of a withdrawn task (T4, D1), none
    from a multi_case_undeclared case (D4), none for a dual_ruling clause (D3),
    and folded sibling numbers that really are siblings.
"""

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import generate  # noqa: E402  (same directory, deliberate)

from jsonschema import Draft202012Validator  # noqa: E402

BENCH = pathlib.Path(__file__).resolve().parent
SCHEMA = BENCH / "item_schema.json"
DEFAULT_ITEMS = BENCH / "items.jsonl"

MAX_REPORT = 20


TASK_KINDS = {
    "T1": ("complaint", "response"),
    "T1-triage": ("complaint",),
    "T3": ("complaint", "response", "panel_ruling"),
}

EXCLUSION_KEYS = ("case_number", "task", "clause", "reason", "detail")
# Reasons that assert "this candidate has no valid item", as opposed to
# `sibling_duplicate` (which asserts the item exists, booked elsewhere) and
# `no_usable_segments` (which is about one task's shape, not the clause).
# R13 added two reasons that are hard by construction -- there is nothing to
# label -- so they belong in this assertion too. 0 overlap today; this keeps it
# that way rather than leaving 255 rows outside the check.
HARD_EXCLUSION_REASONS = {"dual_ruling", "multi_case_undeclared",
                          "tripwire", "tripwire_outcome_banner",
                          "no_panel_ruling", "t3_neither_ruling_attributed",
                          # R28 stage 1. Hard in the same sense, and its rows
                          # only ever name T3 -- an appeal-axis dual denies the
                          # panel->board transition, not the Panel's ruling, so
                          # the clause's T1/T1-triage items are untouched and
                          # the (case, task, clause) key is what makes the
                          # assertion below exact.
                          "dual_ruling_appeal_board"}


# DEFECTS R32(i). The two headers a panel_ruling block may carry, and this
# file's OWN screen for the negative statement -- a window scan around the
# words, not a re-typing of the generator's frame, so a hole in one is not a
# hole in both. It fires on "was not appealed", "were not appealed", "not
# appealed by ..." and "(not appealed)", which is every negative shape the 553
# ruling segments of appealed cases write.
PANEL_HEAD_UNDER_APPEAL = "[PANEL RULING UNDER APPEAL]"
PANEL_HEAD_NEUTRAL = "[PANEL RULING]"
V_NOT_APPEALED_RE = re.compile(r"\bnot\s+(?:been\s+)?appealed\b", re.I)


def printed_panel_head(extract_text, nth):
    """The header the item PRINTED over its nth panel_ruling block, or None if
    it is neither allowed string. Read off the extract rather than recomputed:
    the rebuild above is checking that the extract equals its spans, and the
    header choice is checked on its own terms by the witness test."""
    seen = [line for line in extract_text.split("\n")
            if line in (PANEL_HEAD_UNDER_APPEAL, PANEL_HEAD_NEUTRAL)]
    return seen[nth] if nth < len(seen) else None


# --- own-case-number redaction: THIS FILE'S OWN READING -------------------
#
# bench/generate.py redacts the item's own and co-reported case numbers out of
# the served text. Two things have to be checked and they are different checks:
# (1) the extract still equals its provenance spans, which means re-slicing the
# RAW spans and redacting them here before comparing; and (2) no own case number
# survives anywhere in the served text, which is the standing zero-scan.
#
# Neither may be done by calling `generate.redact_case_ids` or by re-typing its
# three patterns. R24 is the register entry for exactly that mistake --
# l2/validate.py held a byte-copy of the builder's RULING_RE and inherited its
# decimal-point hole, so "two independent readings" was nominal. So this walks
# the string: it finds maximal digit runs and reads their neighbourhood with
# str operations, with no regular expression anywhere in the reader. A hole in
# one implementation is then not a hole in both, and the demonstration is the
# one R24's fix used: break the generator's rule and this file names the item.
V_SEPS = "/\\-‐‑‒–—―"
V_SPACE = " \t"
V_PREFIXES = ("auth", "cases", "case")
V_TOKEN = "[CASE NO.]"


def v_case_serials(*names):
    """The serial of each canonical `PREFIX/NNNN/M/YY` -- what names the case."""
    out = set()
    for name in names:
        parts = str(name or "").split("/")
        if len(parts) == 4 and parts[1].isdigit():
            out.add(int(parts[1]))
    return out


def _v_digit_runs(text):
    """(start, end) of every maximal run of digits."""
    runs, i, n = [], 0, len(text)
    while i < n:
        if text[i].isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def _v_prefix_start(text, at):
    """Where the AUTH/CASE word before `at` begins, or None.

    Up to three separator-or-space characters may sit between the word and the
    digits, and there may be none at all ('AUTH2583/3/13').
    """
    i = at
    for _ in range(3):
        if i > 0 and (text[i - 1] in V_SEPS or text[i - 1] in V_SPACE):
            i -= 1
        else:
            break
    j = i
    while j > 0 and text[j - 1].isalpha():
        j -= 1
    return j if text[j:i].lower() in V_PREFIXES else None


def _v_eat_separator(text, at):
    """Index just past one separator (spaces, one or two separator chars,
    spaces) starting at `at`, or None if no separator is there."""
    i = at
    while i < len(text) and text[i] in V_SPACE:
        i += 1
    seen = 0
    while i < len(text) and text[i] in V_SEPS and seen < 2:
        i += 1
        seen += 1
    if not seen:
        return None
    while i < len(text) and text[i] in V_SPACE:
        i += 1
    return i


def _v_tail_end(text, at):
    """Index just past a `/MONTH/YEAR` tail starting at `at`, or None."""
    i = _v_eat_separator(text, at)
    if i is None:
        return None
    j = i
    while j < len(text) and text[j].isdigit() and j - i < 2:
        j += 1
    if j == i:
        return None
    k = _v_eat_separator(text, j)
    if k is None:
        return None
    m = k
    while m < len(text) and text[m].isdigit() and m - k < 4:
        m += 1
    if m - k < 2:
        return None
    return m if m >= len(text) or not text[m].isdigit() else None


def _v_run_serials(text, runs):
    """Digit runs that sit inside a chain of three or more comma-separated
    four-digit numbers -- the one bare-serial spelling the corpus uses
    (AUTH/2070/11/07's source artefact)."""
    inside, chain = set(), []
    for k, (a, b) in enumerate(runs):
        if b - a != 4:
            chain = []
            continue
        if chain:
            gap = text[chain[-1][1]:a]
            if gap.strip(V_SPACE) != ",":
                chain = []
        if not chain:
            before = text[a - 1] if a else ""
            if before.isdigit() or before == "/":
                continue
        chain.append((a, b))
        after = text[b] if b < len(text) else ""
        ends = not (after.isdigit() or after == "/")
        nxt = runs[k + 1] if k + 1 < len(runs) else None
        continues = (nxt is not None and nxt[1] - nxt[0] == 4
                     and text[b:nxt[0]].strip(V_SPACE) == ",")
        if ends and not continues and len(chain) >= 3:
            inside.update(chain)
    return inside


def v_redact_case_ids(text, serials):
    """Re-derive the served text's redaction from the raw span."""
    if not text or not serials:
        return text
    runs = _v_digit_runs(text)
    in_run = _v_run_serials(text, runs)
    out, cut = [], 0
    for a, b in runs:
        if not (2 <= b - a <= 5) or int(text[a:b]) not in serials or a < cut:
            continue
        start = _v_prefix_start(text, a)
        end = _v_tail_end(text, b)
        if start is None and end is None and (a, b) not in in_run:
            continue            # undecided -- left alone, the zero-scan names it
        out.append((a if start is None else start, b if end is None else end))
        cut = out[-1][1]
    if not out:
        return text
    parts, at = [], 0
    for a, b in out:
        parts.append(text[at:a])
        parts.append(V_TOKEN)
        at = b
    parts.append(text[at:])
    return "".join(parts)


def _v_is_iso_date(text, a, b):
    """`2007-05-31` -- a date whose year is a case serial and whose tail wears
    the case-number shape. Decided as a date, on the measurement that no case
    number anywhere in the served corpus is spelled with dashes."""
    tail = text[b:b + 6]
    return (b - a == 4 and len(tail) == 6 and tail[0] == "-" and tail[3] == "-"
            and tail[1:3].isdigit() and tail[4:6].isdigit()
            and not text[b + 6:b + 7].isdigit())


def v_surviving_case_ids(text, serials):
    """Own-serial occurrences still readable as a case number in served text."""
    found = []
    for a, b in _v_digit_runs(text or ""):
        if not (2 <= b - a <= 5) or int(text[a:b]) not in serials:
            continue
        if _v_is_iso_date(text, a, b):
            continue
        after = text[b:b + 12]
        tail = after[:1] in (",",) or _v_eat_separator(text, b) is not None
        if _v_prefix_start(text, a) is not None or tail:
            found.append(text[max(0, a - 40):b + 40])
    return found


def load_jsonl(path):
    out = []
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items", default=str(DEFAULT_ITEMS))
    ap.add_argument("--cases", default=str(generate.DEFAULT_CASES))
    ap.add_argument("--fixture", default=str(generate.FIXTURE_CASES))
    ap.add_argument("--panes", default=str(generate.FIXTURE_PANES))
    ap.add_argument("--l1", default=str(generate.L1_RECORDS))
    ap.add_argument("--pdf-records", default=str(generate.L1_PDF_RECORDS))
    ap.add_argument("--exclusions", default=str(generate.DEFAULT_EXCLUSIONS))
    ap.add_argument("--use-fixture", action="store_true",
                    help="check against the fixture cases, even if real L2 exists")
    args = ap.parse_args(argv)

    items = load_jsonl(args.items)
    if not items:
        raise SystemExit(f"{args.items} is empty")

    cases_path = pathlib.Path(args.cases)
    using_fixture = args.use_fixture or not cases_path.exists()
    if using_fixture:
        cases_path = pathlib.Path(args.fixture)
    cases = load_jsonl(cases_path)

    wanted = {(s["ref"]["file"], s["ref"]["pane"]) for c in cases for s in c.get("segments", []) if "ref" in s}
    resolver = (generate.FixtureResolver(args.panes) if using_fixture
                else generate.L1Resolver(args.l1, args.pdf_records, wanted))

    # (file, pane, start, end) -> kind, for clean complaint/response segments only
    clean_refs = {}
    case_group = generate.sibling_groups(cases)
    # Members of each sibling group, for the redaction's own-number set. The
    # GROUPING is the same structural fact the split rule below already shares
    # (SPEC/DESIGN §6, source_files + declared siblings); what this file does
    # not share is the READING -- which characters spell a case number -- and
    # that is the part `v_redact_case_ids` re-implements from scratch.
    co_reported = {}
    for num, key in case_group.items():
        co_reported.setdefault(key, []).append(num)
    for case in cases:
        for seg in generate.quotable(case, [], resolver):
            clean_refs[generate.ref_key(seg["ref"])] = seg["kind"]

    # bench/review/DEFECTS.md: three whole classes of item must not exist any
    # more, and a validator that cannot see them cannot hold the fix in place.
    # Each is checked against the CASES, not against generate.py's control flow.
    by_number = {generate.value_of(c["case_number"]): c for c in cases}
    excluded_cases = {n for n, c in by_number.items()
                      if (c.get("quality") or {}).get("multi_case_undeclared")}
    dual_rows = {(n, str(v.get("clause")))
                 for n, c in by_number.items() for v in (c.get("verdicts") or [])
                 if v.get("dual_ruling")}
    dual_bases = {(n, str(v.get("clause"))): v.get("basis")
                  for n, c in by_number.items() for v in (c.get("verdicts") or [])
                  if v.get("dual_ruling")}
    # R28 stage 1, the appeal axis. Re-derived from the CASES the same way, and
    # scoped to T3 because that is the only task whose label is the transition:
    # the Board ruling a clause both ways says nothing about the Panel's own
    # ruling, which is what T1/T1-triage are labelled with.
    dual_board_rows = {(n, str(v.get("clause")))
                       for n, c in by_number.items() for v in (c.get("verdicts") or [])
                       if v.get("dual_ruling_appeal_board")}

    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    failures = []
    seen_ids = {}
    group_splits = {}

    for item in items:
        iid = item.get("item_id", "(no id)")
        for err in validator.iter_errors(item):
            failures.append((iid, "schema", "/".join(str(p) for p in err.absolute_path) or "(root)", err.message))

        if iid in seen_ids:
            failures.append((iid, "invariant", "item_id", "duplicate item_id"))
        seen_ids[iid] = True

        inputs = item.get("inputs", {})
        prov = inputs.get("extract_provenance", [])
        clause_ref = inputs.get("clause_ref", {})
        own_serials = v_case_serials(
            item.get("case_number"), *(item.get("sibling_case_numbers") or []),
            *co_reported.get(case_group.get(item.get("case_number"),
                                            item.get("case_number")), []))

        # --- id determinism
        clause_key = f"{clause_ref.get('code_year')}/{clause_ref.get('clause')}"
        expect = generate.item_id(item.get("task", ""), item.get("case_number", ""), clause_key, prov)
        if expect != iid:
            failures.append((iid, "invariant", "item_id", f"not the documented hash (expected {expect})"))

        # --- provenance slices, and the concatenation reproduces the extract
        rebuilt = []
        for p in prov:
            ref = {k: p[k] for k in ("file", "pane", "char_start", "char_end")}
            try:
                text = generate.slice_ref(resolver, ref)
            except (KeyError, ValueError) as exc:
                failures.append((iid, "invariant", "extract_provenance", str(exc)))
                rebuilt = None
                break
            key = generate.ref_key(ref)
            if key not in clean_refs:
                failures.append((iid, "leakage", "extract_provenance",
                                 f"{key} is not a quotable segment (clean complaint/response, or guarded panel_ruling) in {cases_path.name}"))
            elif clean_refs[key] != p.get("kind"):
                failures.append((iid, "leakage", "extract_provenance",
                                 f"kind {p.get('kind')!r} does not match segment kind {clean_refs[key]!r}"))
            rebuilt.append({"kind": p["kind"], "ref": ref, "text": text})

        if rebuilt is not None:
            # DEFECTS R32(i). The panel_ruling header is no longer a constant --
            # it is UNDER APPEAL only where a witness says this block's ruling
            # was appealed -- so the rebuild reads the header the item actually
            # printed and checks two things separately: that it is one of the
            # two allowed strings, and (below) that the OVER-CLAIMING direction
            # is witnessed. Re-deriving the choice by calling the generator's
            # own reader would validate the builder with its own parser.
            heads = {"complaint": "[COMPLAINT]", "response": "[RESPONSE FROM THE RESPONDENT COMPANY]"}
            chunks, printed = [], []
            for r in rebuilt:
                head = heads.get(r["kind"])
                if head is None:
                    head = printed_panel_head(inputs.get("extract_text", ""), len(printed))
                    printed.append((head, r["text"]))
                    if head is None:
                        failures.append((iid, "invariant", "extract_text",
                                         "a panel_ruling block carries neither allowed header"))
                        head = ""
                chunks.append(f"{head}\n{generate.segment_body(r['kind'], r['text'].strip())}")
            # The served extract is the re-sliced spans WITH the own-case-number
            # redaction applied; re-derived here rather than skipped, so the
            # comparison still proves the extract is the report's own words and
            # additionally proves the redaction is the documented rule and not
            # an arbitrary edit.
            joined = v_redact_case_ids("\n\n".join(chunks), own_serials)
            if joined != inputs.get("extract_text"):
                failures.append((iid, "invariant", "extract_text", "does not equal the re-sliced provenance spans"))
            # The defect's own direction, checked independently: a block the
            # report says was NOT appealed must not be headed UNDER APPEAL.
            for head, text in printed:
                if head == PANEL_HEAD_UNDER_APPEAL and V_NOT_APPEALED_RE.search(" ".join(text.split())):
                    failures.append((iid, "invariant", "extract_text",
                                     "a panel_ruling block headed UNDER APPEAL states that a "
                                     "ruling was NOT appealed"))

        # --- task shape
        kinds_seen = [p.get("kind") for p in prov]
        required = TASK_KINDS.get(item.get("task"))
        if required and set(kinds_seen) != set(required):
            failures.append((iid, "invariant", "extract_provenance",
                             f"task {item.get('task')} needs {required}, extract has {sorted(set(kinds_seen))}"))

        # --- chunk order is DOCUMENT order, not kind order (DEFECTS D5)
        order = [generate.ref_key(p) for p in prov]
        if order != sorted(order):
            failures.append((iid, "invariant", "extract_provenance",
                             "quoted chunks are not in document order -- an extract must "
                             "preserve the exchange as the report writes it"))

        # --- metadata allowlist
        meta = set(inputs.get("metadata_shown", {}))
        allowed = set(generate.SAFE_METADATA) | (set(generate.T3_METADATA) if item.get("task") == "T3" else set())
        if meta - allowed:
            failures.append((iid, "leakage", "metadata_shown", f"fields outside the allowlist: {sorted(meta - allowed)}"))
        if set(generate.SAFE_METADATA) - meta:
            failures.append((iid, "invariant", "metadata_shown", f"missing: {sorted(set(generate.SAFE_METADATA) - meta)}"))

        # --- tripwire over quoted text only (metadata legitimately names a
        #     Panel ruling on T3; that is the premise, not leakage)
        quoted = [inputs.get("extract_text", "")]
        quoted += [r.get("extract_text", "") for r in inputs.get("renditions", [])]
        is_t3 = item.get("task") == "T3"
        for text in quoted:
            # The spliced outcome banner is checked on every task, T3 included.
            for pattern, what in generate.BANNER_TRIPWIRE:
                m = pattern.search(text)
                if m:
                    failures.append((iid, "TRIPWIRE", what, f"quoted text contains {m.group(0)!r}"))
            if is_t3:
                # ruling language is T3's premise; the appeal OUTCOME is the leak
                m = generate.APPEAL_OUTCOME_RE.search(text)
                if m:
                    failures.append((iid, "TRIPWIRE", "appeal outcome", f"quoted text contains {m.group(0)!r}"))
                continue
            # generate.tripwire_hit, not a loop over generate.TRIPWIRE: the
            # precedent exemption (DEFECTS R24) lives inside it, and a checker
            # that applied the patterns raw would fail every item the generator
            # legitimately kept.
            m, what = generate.tripwire_hit(
                text, generate.own_case_numbers(item.get("case_number"),
                                                *(item.get("sibling_case_numbers") or [])))
            if m:
                failures.append((iid, "TRIPWIRE", what, f"quoted text contains {m.group(0)!r}"))

        # --- the standing zero-scan (the redaction decision, 2026-08-11)
        # Independent of the equality check above: two implementations that
        # shared a hole would agree with each other and still ship the number.
        # This one asks a different question of the SHIPPED string only -- is
        # any own serial still sitting somewhere a reader would read as a case
        # number -- and it covers metadata_shown as well, which carries no case
        # number by construction and is asserted rather than assumed.
        for field, text in ([("extract_text", inputs.get("extract_text", ""))]
                            + [("renditions", r.get("extract_text", ""))
                               for r in inputs.get("renditions", [])]
                            + [(f"metadata_shown.{k}", v)
                               for k, v in (inputs.get("metadata_shown") or {}).items()
                               if isinstance(v, str)]):
            for hit in v_surviving_case_ids(text, own_serials):
                failures.append((iid, "leakage", field,
                                 f"served text still carries this case's own or a co-reported "
                                 f"case number: {hit!r}"))

        # --- sibling rule
        num = item.get("case_number")
        group = case_group.get(num, num)
        prev = group_splits.setdefault(group, item.get("split"))
        if prev != item.get("split"):
            failures.append((iid, "invariant", "split",
                             f"sibling group {group} spans splits {prev} and {item.get('split')}"))

        # --- the three DEFECTS.md exclusions, re-derived from the cases
        if item.get("task") in generate.WITHDRAWN_TASKS:
            failures.append((iid, "invariant", "task",
                             f"{item['task']} is withdrawn: {generate.WITHDRAWN_TASKS[item['task']]}"))
        if num in excluded_cases:
            failures.append((iid, "invariant", "case_number",
                             f"{num} is a multi_case_undeclared case; its items are excluded (D4)"))
        clause = (item.get("inputs") or {}).get("clause_ref", {}).get("clause")
        if clause is not None and (num, str(clause)) in dual_rows:
            failures.append((iid, "invariant", "clause_ref",
                             f"clause {clause} is a dual_ruling row on {num}: it has no single "
                             f"label and must not be an item (D3)"))
        if item.get("task") == "T3" and clause is not None \
                and (num, str(clause)) in dual_board_rows:
            failures.append((iid, "invariant", "clause_ref",
                             f"clause {clause} is a dual_ruling_appeal_board row on {num}: the "
                             f"Appeal Board ruled it both ways, so there is no single "
                             f"panel->board transition to label (R28)"))
        for sib in item.get("sibling_case_numbers") or []:
            if sib == num:
                failures.append((iid, "invariant", "sibling_case_numbers",
                                 "an item lists its own case as a folded sibling"))
            elif case_group.get(sib, sib) != group:
                failures.append((iid, "invariant", "sibling_case_numbers",
                                 f"{sib} is not in this item's sibling group -- identical text "
                                 f"across unrelated cases must not be folded"))

    # --- the durable exclusion log (DEFECTS D5)
    # The bank's negative space is part of the bank. Checked for shape, and for
    # the one thing it could contradict: a candidate recorded as excluded under
    # a HARD rule must not also be sitting in items.jsonl.
    excl_path = pathlib.Path(args.exclusions)
    n_excl = 0
    if not excl_path.exists():
        failures.append(("(corpus)", "invariant", "exclusions",
                         f"{excl_path} is absent -- bench/generate.py must write it on every run"))
    else:
        rows = load_jsonl(excl_path)
        n_excl = len(rows)
        item_keys = {(it.get("case_number"), it.get("task"),
                      str((it.get("inputs") or {}).get("clause_ref", {}).get("clause")))
                     for it in items}
        for i, row in enumerate(rows):
            missing = [k for k in EXCLUSION_KEYS if k not in row]
            if missing:
                failures.append((f"exclusions:{i}", "invariant", "exclusions",
                                 f"row is missing {missing}"))
                continue
            if row["reason"] in HARD_EXCLUSION_REASONS and row["clause"] is not None:
                key = (row["case_number"], row["task"], row["clause"])
                if key in item_keys:
                    failures.append((f"exclusions:{i}", "invariant", "exclusions",
                                     f"{key} is recorded as excluded ({row['reason']}) yet an "
                                     f"item of that case/task/clause exists"))
            # Assurance batch 1: a list-only dual is not evidence that the
            # Panel *ruled* both ways. Check the durable receipt against L2's
            # basis independently rather than accepting arbitrary detail text.
            if row["reason"] == "dual_ruling" and row["clause"] is not None:
                dual_key = (row["case_number"], row["clause"])
                basis = dual_bases.get(dual_key)
                if basis is None:
                    failures.append((f"exclusions:{i}", "invariant", "exclusions",
                                     f"{dual_key} is recorded as dual_ruling but L2 has no "
                                     f"matching dual row"))
                elif basis == "verdict_unappealed_dual_listed":
                    expected = (f"listed both ways in the published outcome lists ({basis}), "
                                f"no single label exists")
                    if row["detail"] != expected:
                        failures.append((f"exclusions:{i}", "invariant", "detail",
                                         f"list-only dual must read {expected!r}"))
                else:
                    expected = (f"ruled both ways in this case ({basis}), "
                                f"no single label exists")
                    if row["detail"] != expected:
                        failures.append((f"exclusions:{i}", "invariant", "detail",
                                         f"prose/reviewed dual must read {expected!r}"))

    n_task = {}
    for item in items:
        n_task[item["task"]] = n_task.get(item["task"], 0) + 1
    print(f"items validated : {len(items)}  ({', '.join(f'{k}={v}' for k, v in sorted(n_task.items()))})")
    print(f"cases           : {cases_path}{'  (FIXTURE)' if using_fixture else ''}")
    print(f"clean quotable segments in source : {len(clean_refs)}")
    print(f"exclusion rows  : {n_excl}  ({excl_path})")

    if failures:
        print(f"\nFAILURES: {len(failures)}")
        for f in failures[:MAX_REPORT]:
            print(f"  {f[0]}  [{f[1]}]  {f[2]}: {f[3]}")
        if len(failures) > MAX_REPORT:
            print(f"  ... and {len(failures) - MAX_REPORT} more")
        return 1

    print("\nOK: items conform to item_schema.json, provenance re-slices exactly, "
          "the leakage rules hold and siblings share a split.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
