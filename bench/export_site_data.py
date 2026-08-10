"""Export site-ready JSON for the review website in `site/`.

    python3 bench/export_site_data.py                 # default: 200-item sample
    python3 bench/export_site_data.py --n 400 --seed 7

Writes into `site/` (override with --site). Small files the site always needs
go to `src/lib/data/` so they are imported at build time and prerendered; the
heavy per-item payloads go to `static/data/` and are fetched on demand.

  src/lib/data/meta.json          bench-wide counts: tasks, labels, eras,
                     splits, tags, audit-trail summary (from
                     bench/review/DEFECTS.md), source hashes, headline runs
  src/lib/data/items_index.json   one light row per sampled item (client-side
                     filtering; also the prerender entry list)
  src/lib/data/runs.json          every bench/runs/* directory that has a
                     scores.json: its manifest plus its full scores payload
  src/lib/data/id_migrations.json bench/id_migrations.jsonl as a plain
                     old_id -> new_id map, for the browser: reviews are
                     captured under the item_id, and 261 of them were
                     renamed by the 2026-08-09 wave (DEFECTS R16 residual),
                     so site/src/lib/review.js migrates its localStorage
                     store through this map
  static/data/items/<id>.json     per item: the EXACT model-facing prompts
                     (built through bench/run.py's own request builder, so
                     they are byte-identical to a live call), the full
                     untruncated extract, and — separately marked — the
                     withheld label with its receipts and the PMCPA case URL

Nothing here is a new source of truth: labels come from bench/items.jsonl,
receipts from data/l2/cases.jsonl, case URLs from data/manifest.jsonl, and
prompts from bench/run.py. The sample is deterministic in --seed.
"""

import argparse
import collections
import hashlib
import importlib.util
import json
import pathlib
import random
import re
import shutil
import sys
from types import SimpleNamespace

BENCH = pathlib.Path(__file__).resolve().parent
ROOT = BENCH.parent
DEFAULT_SITE = ROOT / "site"

# Items carrying these tags are the ones the measurement story turns on
# (APPROACH.md §3), so the sample guarantees a floor of each rather than
# leaving them to chance.
SPECIAL_TAGS = [
    "appeal_flip",
    "appeal_survived",
    "burden_of_proof_candidate",
    "voluntary_admission",
    "abridged",
    "inter_company",
    "no_clause_text",
    "anonymous_complainant",
    "pdf_substituted",
]
TAG_FLOOR = 6


def load_run_module():
    """Import bench/run.py so prompts are built by the runner, not a copy."""
    spec = importlib.util.spec_from_file_location("bench_run", BENCH / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_score_module():
    """Import bench/score.py so leaderboard metrics are the scorer's, not a copy."""
    spec = importlib.util.spec_from_file_location("bench_score", BENCH / "score.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256_of(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def case_urls():
    """filename -> live PMCPA case page URL."""
    urls = {}
    path = ROOT / "data" / "manifest.jsonl"
    if not path.exists():
        return urls
    for rec in read_jsonl(path):
        if rec.get("filename"):
            urls[rec["filename"]] = rec.get("url") or rec.get("final_url")
    return urls


def verdict_index():
    """(case_number, clause, code_year) -> the L2 verdict record (the receipt)."""
    verd = {}
    path = ROOT / "data" / "l2" / "cases.jsonl"
    if not path.exists():
        return verd
    for case in read_jsonl(path):
        cn = case["case_number"]
        cn = cn.get("value") if isinstance(cn, dict) else cn
        for v in case.get("verdicts", []):
            verd[(cn, v.get("clause"), v.get("code_year"))] = v
    return verd


def receipts_for(item, verd):
    """The withheld side: how the label was derived, and from which signals."""
    ref = item["inputs"]["clause_ref"]
    v = verd.get((item["case_number"], ref.get("clause"), ref.get("code_year")))
    if not v:
        return {"basis": None, "available": False}
    s = v.get("sources", {})
    return {
        "available": True,
        "basis": v.get("basis"),
        "final": v.get("final"),
        "panel": v.get("panel"),
        "appeal_board": v.get("appeal_board"),
        "flipped_on_appeal": v.get("flipped_on_appeal"),
        "dual_ruling": v.get("dual_ruling"),
        "occurrence": v.get("occurrence"),
        "note": v.get("note"),
        "signals": {
            "breach_listed": bool(s.get("info_breach_clauses") or s.get("meta_clause_breach") or s.get("chip_breach")),
            "no_breach_listed": bool(s.get("info_no_breach_clauses") or s.get("meta_clause_no_breach") or s.get("chip_no_breach")),
            "prose_panel_breach": bool(s.get("prose_panel_breach")),
            "prose_panel_no_breach": bool(s.get("prose_panel_no_breach")),
            "prose_appeal_board_breach": bool(s.get("prose_appeal_board_breach")),
            "prose_appeal_board_no_breach": bool(s.get("prose_appeal_board_no_breach")),
        },
    }


def era_of(item):
    y = item["inputs"]["clause_ref"].get("code_year")
    return y


def stratified_sample(items, n, seed):
    """Deterministic sample spread over (task, label, code_year), with a floor
    on the item classes whose appropriate confidence is known in advance."""
    rng = random.Random(seed)
    by_id = {it["item_id"]: it for it in items}

    strata = collections.defaultdict(list)
    for it in items:
        strata[(it["task"], it["label"], era_of(it))].append(it["item_id"])
    for key in strata:
        strata[key].sort()
        rng.shuffle(strata[key])

    picked, order = set(), []

    def take(item_id):
        if item_id not in picked:
            picked.add(item_id)
            order.append(item_id)

    # Phase 1 — floors for the diagnostic classes.
    for tag in SPECIAL_TAGS:
        pool = sorted(it["item_id"] for it in items if tag in it["tags"])
        rng.shuffle(pool)
        # docs/WORKING_RULES.md: no silent caps. A declared diagnostic class that cannot meet
        # its floor is announced -- `abridged` is now an empty pool (DEFECTS R15)
        # and the published policy still claims a floor of 6 for each class.
        if len(pool) < TAG_FLOOR:
            print(f"  NOTE: diagnostic class {tag!r} has {len(pool)} item(s), "
                  f"below the floor of {TAG_FLOOR} — contributing what exists")
        for item_id in pool[:TAG_FLOOR]:
            take(item_id)

    # Phase 2 — round-robin over strata, largest strata first within a pass so
    # the common cells stay represented in proportion without starving rare ones.
    keys = sorted(strata, key=lambda k: (-len(strata[k]), str(k)))
    cursor = {k: 0 for k in keys}
    while len(picked) < n:
        progressed = False
        for k in keys:
            if len(picked) >= n:
                break
            while cursor[k] < len(strata[k]):
                item_id = strata[k][cursor[k]]
                cursor[k] += 1
                if item_id not in picked:
                    take(item_id)
                    progressed = True
                    break
        if not progressed:
            break

    return [by_id[i] for i in order]


def build_prompts(run_mod, item):
    """Exactly what the model is sent, for both live protocols."""
    ns = SimpleNamespace(model="claude-sonnet-5", max_tokens=4096,
                         thinking="adaptive", effort=None, seed=11)
    out = {}
    for protocol in ("P2", "P1"):
        variant = run_mod.plan_variants(item, protocol, 1, ns.seed, [])[0]
        params = run_mod.request_params(item, protocol, variant, ns)
        out[protocol] = {
            "system": params["system"],
            "user": params["messages"][0]["content"],
            "schema": params["output_config"]["format"]["schema"],
            "block_order": list(variant["block_order"]),
            "rendition": variant["rendition"],
        }
    return out


def audit_summary():
    """A pointer-level summary of the defect register — the site links out to
    the file itself rather than restating it."""
    path = BENCH / "review" / "DEFECTS.md"
    if not path.exists():
        return {"available": False}
    text = path.read_text(encoding="utf-8")
    entries = []
    for m in re.finditer(r"^### ((?:D|R)\d+)\s*[—-]\s*(.+)$", text, re.M):
        title = m.group(2).strip()
        status = "fixed" if re.search(r"\bFIXED\b", title) else (
            "removed" if re.search(r"\bREMOVE\b", title, re.I) else "open")
        entries.append({"id": m.group(1), "title": title, "status": status})
    good = re.findall(r"^- (.+)$", text.split("## What is verified GOOD")[1].split("## Defects")[0], re.M) \
        if "## What is verified GOOD" in text else []
    return {
        "available": True,
        "file": "bench/review/DEFECTS.md",
        "headline": text.split("\n\n")[1].strip() if "\n\n" in text else "",
        "entries": entries,
        "verified_good": [re.sub(r"\s+", " ", g).strip() for g in good],
        "companion_docs": [
            {"file": "bench/APPROACH.md", "what": "measurement story: what a confidence is and how it is scored"},
            {"file": "bench/DESIGN.md", "what": "what the items are and how they are built"},
            {"file": "bench/review/SAMPLES.md", "what": "human review sample with labels + receipts"},
            {"file": "bench/review/PROMPTS.md", "what": "exact model-facing prompts, rendered by run.py"},
        ],
    }


def collect_runs():
    runs = []
    runs_dir = BENCH / "runs"
    if not runs_dir.exists():
        return runs
    for d in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        scores_path, manifest_path = d / "scores.json", d / "manifest.json"
        if not scores_path.exists():
            continue
        scores = json.loads(scores_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        # Absolute local paths are not site data.
        manifest.pop("items_path", None)
        scores.pop("run", None)
        runs.append({
            "run_id": d.name,
            "dir": f"bench/runs/{d.name}",
            "created_utc": manifest.get("created_utc"),
            "model": scores.get("model") or manifest.get("model"),
            "protocol": scores.get("protocol") or manifest.get("protocol"),
            "k": scores.get("k") or manifest.get("k"),
            "items": scores.get("items"),
            "manifest": manifest,
            "scores": scores,
        })
    runs.sort(key=lambda r: (r.get("created_utc") or "", r["run_id"]))
    return runs


# --- archived runs against a bank that has moved ------------------------------
# The item_id is a hash over the clause ref, so wave-2's code_year corrections
# (2026-08-09, DEFECTS "Prompt-condition change") RENAMED 261 items and removed
# 14 with exclusion rows. Archived runs still name the old ids. The two cases get
# opposite treatment, and neither is a silent skip:
#
#   renamed  -> map old->new and score normally. The wave's field diff moved
#               labels 0, splits 0, extracts 0, provenance 0 — everything scoring
#               reads is byte-identical — while code_year/clause_text (the PROMPT)
#               did move. That is a comparability caveat, stated on the item page,
#               not a scoring obstacle.
#   absent   -> unscoreable. Score what exists and REPORT the shortfall per board.
#               Refusing the whole board over one id is what broke this exporter
#               (R16); skipping quietly would be worse, because a truncated board
#               reads as "covered everything".

def load_id_migrations():
    """old item_id -> TERMINAL item_id (bench/id_migrations.jsonl).

    The file is a log across repair waves, so an item renamed twice appears
    as two rows (old->mid, mid->new); a single-hop lookup dead-ended three
    T3 board ids on 2026-08-10. Chains are collapsed here, bounded and
    cycle-guarded, so every consumer sees the terminal id in one lookup.
    """
    path = BENCH / "id_migrations.jsonl"
    if not path.exists():
        return {}
    one_hop = {row["old_id"]: row["new_id"] for row in read_jsonl(path)}
    resolved = {}
    for old in one_hop:
        seen, cur = {old}, one_hop[old]
        while cur in one_hop and cur not in seen:
            seen.add(cur)
            cur = one_hop[cur]
        resolved[old] = cur
    return resolved


def exclusion_reasons():
    """(case_number, clause, task) -> the reasons bench/generate.py recorded.

    The exclusions file keys on the case/clause/task, not on the item_id the
    dropped candidate would have had, so an absent id is joined through the
    archived bank's row for it. All 4 ids absent from today's boards join
    (3 dual_ruling, 1 code_year_undecided); a miss leaves the reason null
    rather than inventing one.
    """
    out = collections.defaultdict(set)
    path = BENCH / "exclusions.jsonl"
    if not path.exists():
        return out
    for row in read_jsonl(path):
        out[(row.get("case_number"), row.get("clause"), row.get("task"))].add(row.get("reason"))
    return out


_RUN_ITEMS_CACHE = {}


def run_items(run_name):
    """The item bank a run was SERVED from — the archived witness for its labels.

    manifest.items_path is an absolute path from the run host; what survives in
    the repo is bench/subsets/<basename>, so the basename is what is trusted.
    Runs served straight from bench/items.jsonl (the twelve pre-l2.4 dirs) have
    no archived witness at all: that path resolves to TODAY's bank, which cannot
    witness anything about the bank they ran against. None means "no witness",
    which is a refusal at the board level, not a shrug.
    """
    if run_name in _RUN_ITEMS_CACHE:
        return _RUN_ITEMS_CACHE[run_name]
    manifest_path = BENCH / "runs" / run_name / "manifest.json"
    witness = None
    if manifest_path.exists():
        recorded = json.loads(manifest_path.read_text(encoding="utf-8")).get("items_path")
        if recorded:
            archived = BENCH / "subsets" / pathlib.Path(recorded).name
            if archived.exists():
                witness = {it["item_id"]: it for it in read_jsonl(archived)}
    _RUN_ITEMS_CACHE[run_name] = witness
    return witness


# --- are an archived run's answers still answers to TODAY's prompt? -----------
# Rule (2026-08-09): "old runs should be flagged as old only if the model
# inputs have changed." The caveat used to be date-gated -- every call is from
# 2026-08-02/04, so every answered item printed it -- which over-flags an
# untouched T1 item whose clause text and metadata never moved. It is now a
# per-(run, item) comparison of the fields run.py actually renders, against the
# run's OWN archived item bank (bench/subsets/<items_path>, resolved by
# run_items above).
#
# The fields below are exactly build_prompt's inputs, and nothing else:
#   clause_ref.clause / .code_year / .clause_text  -> clause_block + the question
#   metadata_shown (every key)                     -> metadata_block, plus
#       panel_ruling_for_clause and appellant, which T3's question_body renders
#   extract_text                                   -> extract_block (rendition 0)
#   renditions[*].extract_text                     -> extract_block under P1
#   task                                           -> system base, question,
#                                                     answer_line, schema
# Item id, label, split, tags and provenance are deliberately absent: none of
# them reaches the model, so a change in one is not a change in the prompt.
#
# Two overrides that no field diff can see:
#   T3 framing   run.py gained a T3-specific system base on 2026-08-09 (the
#                shared one told T3 models "you will not be shown any ruling"
#                while every T3 extract quotes a PANEL RULING section). That is
#                a change to the whole task's system prompt, invisible in the
#                item bank. Gated on the call's own timestamp so a rerun under
#                the new framing clears, and the boundary is the day AFTER the
#                change because created_utc cannot place a call before or after
#                a same-day edit -- the caveat is the safe side.
#   no witness   the twelve pre-l2.4 run dirs record items_path=items.jsonl,
#                which resolves to TODAY's bank and can witness nothing about
#                the bank they ran against (R16). Unknowable, so it flags.
T3_FRAMING_CHANGE_UTC = "2026-08-10"


def prompt_inputs(item):
    """The item fields build_prompt() reads, in a comparable shape."""
    ref = item["inputs"]["clause_ref"]
    return {
        "task": item["task"],
        "clause": ref.get("clause"),
        "code_year": ref.get("code_year"),
        "clause_text": ref.get("clause_text"),
        "metadata": item["inputs"]["metadata_shown"],
        # The text itself, not the provenance shas: provenance records where the
        # extract was cut from, and two different cuts can carry one sha set
        # while the rendered block differs (the R23 page-band repair is exactly
        # that shape). What the model saw is the string.
        "extract": hashlib.sha256(
            item["inputs"]["extract_text"].encode("utf-8")).hexdigest(),
        "renditions": [hashlib.sha256(r["extract_text"].encode("utf-8")).hexdigest()
                       for r in (item["inputs"].get("renditions") or [])],
    }


def input_change_reasons(was, now):
    """Compact reasons the prompt for `now` is not the prompt `was` produced.

    Empty list = the model inputs are byte-identical, so an archived answer to
    this item is still an answer to today's prompt.
    """
    a, b = prompt_inputs(was), prompt_inputs(now)
    reasons = []
    if a["task"] != b["task"]:
        reasons.append(f"task {a['task']}→{b['task']}")
    if a["clause"] != b["clause"]:
        reasons.append(f"clause {a['clause']}→{b['clause']}")
    if a["code_year"] != b["code_year"]:
        reasons.append(f"code_year {a['code_year']}→{b['code_year']}")
    if a["clause_text"] != b["clause_text"]:
        reasons.append("clause text added" if not a["clause_text"] else
                       "clause text removed" if not b["clause_text"] else
                       "clause text changed")
    # code_year lives in clause_ref AND metadata_shown, and run.py renders the
    # two copies in the clause line and the CASE DETAILS block. Measured over
    # the archived runs that have a witness: 29 (run, item) groups moved
    # clause_ref's code_year, 14 of them moved metadata_shown's too and always
    # to the same value, and NO group moved metadata_shown's alone. So the
    # metadata half is never news when the clause ref changed — printing it
    # would spend a second reason on one fact. (The other 15 are groups whose
    # archived bank already carried the new year in metadata while clause_ref
    # lagged; the wave closed that disagreement.)
    meta_changed = [k for k in sorted(set(a["metadata"]) | set(b["metadata"]))
                    if a["metadata"].get(k) != b["metadata"].get(k)
                    and not (k == "code_year" and a["code_year"] != b["code_year"])]
    if meta_changed:
        # The field names, not old→new values: date_received and appellant are
        # the two that move (7 and 6 groups), and a date pair doubles the line.
        reasons.append("metadata " + ", ".join(k.replace("_", " ") for k in meta_changed))
    if a["extract"] != b["extract"]:
        reasons.append("extract text changed")
    if a["renditions"] != b["renditions"]:
        reasons.append("renditions changed")
    return reasons


def group_input_change(item, witness, old_id, created_utc):
    """(changed, reason) for one run's calls on one item."""
    reasons = []
    if witness is None:
        reasons.append("no archived witness")
    else:
        was = witness.get(old_id)
        if was is None:
            # The run's own bank does not hold the id its responses name. Not
            # seen in any archived run; it would mean a hand-edited subset.
            reasons.append("not in the run's archived bank")
        else:
            reasons.extend(input_change_reasons(was, item))
    if item["task"] == "T3" and (created_utc or "") < T3_FRAMING_CHANGE_UTC:
        reasons.append("T3 framing")
    # A LIST, not one joined string: "metadata code year, date received" already
    # carries a comma, so a consumer that split the join would tear it in two.
    # The item page joins for display; meta.json joins for its histogram key.
    return bool(reasons), reasons


def model_outputs_for(sample_ids, migrations, items_by_id, score_mod):
    """Every archived, parsed model answer for the sampled items.

    Reads raw responses.jsonl (the archive), not scores.json, so partial runs
    contribute their surviving calls too — an individual archived output is
    honest even when the run's aggregate is selection-biased and unrankable.
    P3 choice-stage calls carry no verdict, so only calls with a parsed
    `answer` are shipped; the stated probability exists only under P2.

    Renamed ids follow their item: 32 answers on 14 renamed ids, which would
    otherwise vanish from those items' pages while the answers sit in the
    archive. Returns the outputs plus the accounting for what did NOT attach —
    which reads as "absent from the bank" only because the caller passes the
    WHOLE bank (main() does; a subset would make `unresolved` mean "unsampled").

    Also returns one GROUP per (run, item). A group is the unit the drawer
    renders and the unit a prompt-condition caveat is true of; under P1 it is
    also the unit of measurement — the K verdict-only calls whose modal-answer
    frequency IS the confidence, so the site must never see K look-alike rows
    with no number on them (bench/APPROACH.md §2). The group's confidence comes
    from score.py's own aggregate(), not from a second implementation here.
    """
    outputs, groups = {}, collections.defaultdict(list)
    seen, mapped, unresolved = set(), set(), collections.Counter()
    runs_dir = BENCH / "runs"
    for d in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        if d.name.startswith("probe-"):
            continue
        resp_path = d / "responses.jsonl"
        manifest_path = d / "manifest.json"
        if not resp_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        witness = run_items(d.name)
        # The archive is append-only, and one anomaly dir (20260802T181912Z,
        # documented in runs/README.md) contains a corrupt line — tolerate and
        # say so rather than either crashing or silently skipping.
        records = []
        with resp_path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"  skipping corrupt line {lineno} in {d.name}/responses.jsonl "
                          "(archived anomaly; see bench/runs/README.md)")
        per_item = collections.defaultdict(list)
        for rec in records:
            parsed = rec.get("parsed")
            if not parsed or "answer" not in parsed:
                continue
            old_id = rec.get("item_id")
            item_id = migrations.get(old_id, old_id)
            seen.add(old_id)
            if item_id not in sample_ids:
                # T5 answers (its items live in a separate file by standing
                # policy) and the pre-l2.4 ids R16 records. Counted, not hidden.
                unresolved[old_id] += 1
                continue
            if item_id != old_id:
                mapped.add(old_id)
            variant = rec.get("variant") or {}
            per_item[item_id].append((old_id, rec, {
                "run_id": d.name,
                "created_utc": manifest.get("created_utc"),
                "model": manifest.get("model"),
                "protocol": rec.get("protocol") or manifest.get("protocol"),
                "thinking": manifest.get("thinking"),
                "rationale_mode": bool(manifest.get("rationale")),
                "variant": {k: variant.get(k) for k in ("rendition", "block_order", "temperature", "stage")
                            if variant.get(k) is not None},
                "answer": parsed["answer"],
                "probability": parsed.get("probability"),
                "rationale": parsed.get("rationale"),
            }))
        for item_id, rows in sorted(per_item.items()):
            old_ids = {old for old, _, _ in rows}
            protocols = {call["protocol"] for _, _, call in rows}
            if len(old_ids) > 1 or len(protocols) > 1:
                # One run serves one id per item and one protocol per call; if
                # that ever stops being true the group's k and modal frequency
                # would be computed over two different questions.
                sys.exit(f"{d.name}: item {item_id} has calls under {sorted(old_ids)} "
                         f"and protocols {sorted(str(p) for p in protocols)}. A (run, item) "
                         "group must be one id under one protocol. Refusing.")
            changed, reasons = group_input_change(
                items_by_id[item_id], witness, rows[0][0], manifest.get("created_utc"))
            group = {
                "run_id": d.name,
                "created_utc": manifest.get("created_utc"),
                "model": manifest.get("model"),
                "protocol": rows[0][2]["protocol"],
                "k": manifest.get("k"),
                "n": len(rows),
                "inputs_changed": changed,
                "inputs_reasons": reasons,
                "_calls": [call for _, _, call in rows],
            }
            if group["protocol"] == "P1":
                # score.py owns the measurement: aggregate() under P1 tallies
                # the parsed answers, takes the modal one (alphabetical
                # tie-break, flagged) and sets p = top/len(parsed). Calling it
                # per (run, item) gives exactly the row score.py would write for
                # a run of this one item. The tally itself is not returned by
                # aggregate, so the distribution — the receipt the drawer prints
                # beside the frequency — is counted here from the same parsed
                # answers, and asserted against score.py's p below.
                recs = [{**rec, "item_id": item_id} for _, rec, _ in rows]
                scored, dropped = score_mod.aggregate(recs, items_by_id, "P1")
                if dropped or len(scored) != 1:
                    sys.exit(f"{d.name}: score.py could not aggregate the P1 calls for "
                             f"{item_id} ({dropped or len(scored)}). Refusing.")
                row = scored[0]
                dist = collections.Counter(call["answer"] for _, _, call in rows)
                top = dist[row["answer"]]
                if abs(row["p"] - top / row["n_parsed"]) > 1e-12:
                    sys.exit(f"{d.name}: the modal frequency for {item_id} disagrees with "
                             f"score.py ({top}/{row['n_parsed']} vs {row['p']}). Refusing.")
                # Modal answer first, then the rest by count and name. The row
                # prints the modal term as its chip and the tail after it, so
                # this is the reading order and it is decided here, once, rather
                # than re-sorted in the browser.
                order = sorted(dist, key=lambda a: (a != row["answer"], -dist[a], a))
                group["measured"] = {
                    "answer": row["answer"],
                    "confidence": row["p"],
                    "n_parsed": row["n_parsed"],
                    "modal_tie": row["modal_tie"],
                    "distribution": [[a, dist[a]] for a in order],
                }
            groups[item_id].append(group)

    # The calls are laid out group by group, so a group is a contiguous run of
    # `n` calls starting at `first` and the drawer can fold one without holding
    # a second copy of them. Order is the one the flat list always had: by the
    # run's timestamp, then its directory name.
    for item_id, gs in groups.items():
        gs.sort(key=lambda g: (g["created_utc"] or "", g["run_id"]))
        calls = []
        for g in gs:
            g["first"] = len(calls)
            calls.extend(g.pop("_calls"))
        outputs[item_id] = calls

    accounting = {
        "n_answers": sum(len(v) for v in outputs.values()),
        "n_items_answered": len(outputs),
        "n_ids_referenced": len(seen),
        "n_ids_mapped": len(mapped),
        "n_ids_unresolved": len(unresolved),
        "n_answers_unresolved": sum(unresolved.values()),
        "unresolved_ids": sorted(unresolved),
    }
    return outputs, groups, accounting


def stale_inputs_summary(groups_by_item):
    """Tracking number: archived answers that are answers to a prompt the
    bank no longer serves. The endgame is zero — every count here is cleared by
    rerunning that run against today's bank, not by editing this file."""
    by_reason, by_cause = collections.Counter(), collections.Counter()
    n_answers = n_stale = n_groups = n_stale_groups = 0
    items, runs = set(), collections.Counter()
    for item_id, gs in groups_by_item.items():
        for g in gs:
            n_groups += 1
            n_answers += g["n"]
            if not g["inputs_changed"]:
                continue
            n_stale_groups += 1
            n_stale += g["n"]
            items.add(item_id)
            runs[g["run_id"]] += g["n"]
            by_reason[", ".join(g["inputs_reasons"])] += g["n"]
            for cause in g["inputs_reasons"]:
                by_cause[cause] += g["n"]
    return {
        "n_answers": n_answers,
        "n_answers_stale": n_stale,
        "n_groups": n_groups,
        "n_groups_stale": n_stale_groups,
        "n_items_affected": len(items),
        # by_reason is the composite string the item page prints; by_cause
        # splits it so one cause can be counted across every combination it
        # appears in (T3 framing rides along with clause-text changes on 320
        # groups, and would otherwise look smaller than it is).
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_cause": dict(sorted(by_cause.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_run": dict(sorted(runs.items(), key=lambda kv: (-kv[1], kv[0]))),
        "policy": ("an archived answer is input-stale when the item fields bench/run.py "
                   "renders into the prompt (clause ref, clause text, metadata_shown, "
                   "extract and rendition text, task) differ between the run's own "
                   "archived item bank and today's — plus two overrides no field diff "
                   "can see: T3's system base changed 2026-08-09, and a run with no "
                   "archived bank cannot be checked at all"),
    }


def headline_runs(runs):
    """One pointer per (model, protocol): the largest scored run, latest wins ties."""
    best = {}
    for r in runs:
        key = (r.get("model"), r.get("protocol"))
        n = (r["scores"].get("overall") or {}).get("n") or 0
        cur = best.get(key)
        if cur is None or (n, r.get("created_utc") or "") > (cur[0], cur[1].get("created_utc") or ""):
            best[key] = (n, r)
    out = []
    for (model, protocol), (n, r) in sorted(best.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        o = r["scores"].get("overall") or {}
        if not n:
            continue
        out.append({
            "run_id": r["run_id"], "model": model, "protocol": protocol, "k": r.get("k"),
            "n": n, "accuracy": o.get("accuracy"), "brier": o.get("brier"), "ece": o.get("ece"),
            "ci": o.get("ci"),
        })
    out.sort(key=lambda r: (-(r["n"] or 0), r["model"] or ""))
    return out


# --- leaderboard -------------------------------------------------------------
# A board is a SAME-ITEMS comparison: every entry answered the identical item
# set, so ranking within a board is meaningful and ranking across boards is
# not (the site says so in words). Entries pool run dirs where FINDINGS §4
# already discloses the pooling (T3 n=297 = the 267-item sweep + the 30 T3
# items inside the Phase A run; item_ids and labels are identical across the
# bank versions involved). Runs that CANNOT be ranked are listed under
# `excluded` with the reason — the opus runs lost 126/150 and 125/150 calls to
# credit exhaustion mid-run (400 "credit balance too low" — the account ran
# dry on 2026-08-02 morning, before the top-up), and the surviving quarter is
# selection-biased, so ranking it would be a silent lie.

LEADERBOARD_BOARDS = [
    {
        "board_id": "t3-appeal",
        "title": "T3 appeal-survival",
        "primary": True,
        "question": "Did the Appeal Board uphold the Panel's ruling, or overturn it?",
        "note": ("The benchmark's central exhibit: the class where the best-informed human "
                 "adjudicators disagreed. Both models answered the same 297 items."),
        "entries": [
            {"model": "claude-sonnet-5",
             "sources": [("20260802T212935Z-64928", None), ("phaseA-sonnet-P2", "T3")]},
            {"model": "gpt-5.1",
             "sources": [("20260804T132819Z-68493", None), ("20260804T132921Z-69318", None)]},
        ],
    },
    {
        "board_id": "phase-a",
        "title": "Phase A dev subset",
        "primary": False,
        "question": "Mixed tasks: T1 breach-verdict, T1-triage (complaint only), T3 appeal-survival.",
        "note": "The 150-item stratified dev subset both Anthropic models ran in full.",
        "entries": [
            {"model": "claude-sonnet-5", "sources": [("phaseA-sonnet-P2", None)]},
            {"model": "claude-haiku-4-5", "sources": [("phaseA-haiku-P2", None)]},
        ],
    },
]

LEADERBOARD_EXCLUDED = [
    {"model": "claude-opus-5", "runs": ["20260802T101118Z", "20260802T101119Z"],
     "reason": ("126/150 and 125/150 calls failed — the Anthropic account ran out of credit "
                "mid-run (400: credit balance too low), so which items completed is an "
                "accident of call order; the surviving quarter's n=24 accuracy of 0.875 is "
                "not comparable to any full run. A clean opus run needs an Anthropic top-up")},
    {"model": "gpt-5.1 (T1)", "runs": ["20260802T101120Z"],
     "reason": ("a clean 150-call T1 run, but it was served from a pre-final bank version "
                "and 38 of its 150 items were dropped in later bank rebuilds; scoring the "
                "112-item remnant would match neither the archived scores nor FINDINGS "
                "§4.2, which reports the run against its contemporaneous bank (0.753)")},
]


def leaderboard(score_mod, items_by_id, migrations, exclusions, draws=1000, seed=20260802):
    """Compute same-items boards from archived responses joined to the CURRENT bank.

    Metrics come from score.py's own functions — one implementation, no drift.
    Refuses (in words) if a board's entries did not score the identical item
    set, because that identity is the claim the board makes.

    Ids that moved under wave-2 are mapped and scored; ids that no longer exist
    are dropped from the board and REPORTED in `accounting` (n_expected,
    n_scored, n_mapped, n_absent, and the ids with the reason generate.py
    excluded them for). Anything else still refuses.
    """
    boards = []
    for spec in LEADERBOARD_BOARDS:
        entries, id_sets, accountings = [], [], []
        for e in spec["entries"]:
            responses, expected, mapped, absent = [], set(), set(), {}
            for run_name, task_filter in e["sources"]:
                # The run's own bank decides the task of an id today's bank no
                # longer holds; without it a removed item would be filtered out
                # by the task test and never counted as absent.
                witness = run_items(run_name)
                if witness is None:
                    sys.exit(f"leaderboard {spec['board_id']} / {e['model']}: run {run_name} "
                             "records no archived item bank (manifest.items_path does not "
                             "resolve under bench/subsets/), so its labels cannot be checked "
                             "against today's bank. Point items_path at the archived subset, "
                             "or drop the source.")
                for rec in read_jsonl(BENCH / "runs" / run_name / "responses.jsonl"):
                    old_id = rec["item_id"]
                    new_id = migrations.get(old_id, old_id)
                    item = items_by_id.get(new_id)
                    was = witness.get(old_id)
                    task = (item or was or {}).get("task")
                    if task_filter and task != task_filter:
                        continue
                    expected.add(old_id)
                    if item is None:
                        key = (was or {}).get("case_number"), (was or {}).get(
                            "inputs", {}).get("clause_ref", {}).get("clause"), task
                        reasons = sorted(exclusions.get(key, ()))
                        absent[old_id] = {
                            "item_id": old_id,
                            "case_number": key[0],
                            "clause": key[1],
                            "task": task,
                            "reason": reasons[0] if len(reasons) == 1 else (reasons or None),
                        }
                        continue
                    # A mapped id must still be the SAME question. The rename is
                    # only defensible because the label did not move with it; if
                    # it did, the board would silently re-score an answer against
                    # a different truth, so this hard-fails rather than reports.
                    if was is not None and was["label"] != item["label"]:
                        sys.exit(f"leaderboard {spec['board_id']} / {e['model']}: {old_id} was "
                                 f"answered against label {was['label']!r} and "
                                 f"{'maps to ' + new_id + ' which' if new_id != old_id else 'now'} "
                                 f"carries {item['label']!r}. An archived answer cannot be "
                                 "re-scored against a different label. Refusing.")
                    if new_id != old_id:
                        mapped.add(old_id)
                        rec = {**rec, "item_id": new_id}
                    responses.append(rec)
            rows, dropped = score_mod.aggregate(responses, items_by_id, "P2")
            if dropped:
                sys.exit(f"leaderboard {spec['board_id']} / {e['model']}: {len(dropped)} of "
                         f"{len(rows) + len(dropped)} items failed to score "
                         f"(first: {dropped[0]}). Absent ids are already accounted for above, "
                         "so this is something else; fix the sources or exclude the model "
                         "with a reason.")
            if not rows:
                sys.exit(f"leaderboard {spec['board_id']} / {e['model']}: every one of "
                         f"{len(expected)} archived items is absent from the bank. A board "
                         "with nothing left to score is not a board.")
            accountings.append({
                "n_expected": len(expected),
                "n_scored": len(rows),
                "n_mapped": len(mapped),
                "n_absent": len(absent),
                "absent": [absent[k] for k in sorted(absent)],
            })
            id_sets.append({r["item_id"] for r in rows})
            by_label = {}
            for lab in sorted({r["label"] for r in rows}):
                sub = [r for r in rows if r["label"] == lab]
                by_label[lab] = {
                    "n": len(sub),
                    "accuracy": score_mod.accuracy(sub),
                    "mean_p": sum(r["p"] for r in sub) / len(sub),
                }
            entries.append({
                "model": e["model"],
                "runs": [name for name, _ in e["sources"]],
                **score_mod.metric_set(rows),
                "mean_p": sum(r["p"] for r in rows) / len(rows),
                "ci": score_mod.bootstrap(rows, draws, seed),
                "by_label": by_label,
                # Pooled over the ENTRY's board rows, not per run — a per-run
                # curve would mix Phase A's non-T3 items into the T3 exhibit.
                "reliability": score_mod.adaptive_bins(rows),
            })
        if len({frozenset(s) for s in id_sets}) > 1:
            sizes = " vs ".join(str(len(s)) for s in id_sets)
            sys.exit(f"leaderboard {spec['board_id']}: entries scored DIFFERENT item sets "
                     f"({sizes}) — the board's same-items claim would be false. Refusing.")
        # One accounting per board, so the entries must agree on what was expected
        # and what is gone; a board whose rows lost different items is not a
        # same-items board even where the scored sets happen to match.
        if len({(a["n_expected"], tuple(r["item_id"] for r in a["absent"]))
                for a in accountings}) > 1:
            sys.exit(f"leaderboard {spec['board_id']}: entries expected different item sets "
                     f"({' vs '.join(str(a['n_expected']) for a in accountings)}) or lost "
                     "different ids. Refusing.")
        entries.sort(key=lambda r: (r["brier"] is None, r["brier"]))
        boards.append({
            "board_id": spec["board_id"],
            "title": spec["title"],
            "primary": spec["primary"],
            "question": spec["question"],
            "note": spec["note"],
            "n_items": len(id_sets[0]) if id_sets else 0,
            "protocol": "P2",
            # What the archived runs answered vs what is scoreable today. Only
            # `n_absent` > 0 is a caveat the site prints; the rest is the receipt.
            "accounting": accountings[0],
            "entries": entries,
        })
    return {"boards": boards, "excluded": LEADERBOARD_EXCLUDED,
            "policy": ("rows on one board answered the identical item set under the same "
                       "protocol; ranked by Brier (proper score: right AND knowing how "
                       "right), CIs case-blocked bootstrap")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=0,
                    help="sampled items to export in full (0, the default, = the whole bank — "
                         "the site's standing policy since 2026-08-05; a bare invocation must "
                         "never silently shrink the site back to a sample). "
                         f"The diagnostic-class floor of {TAG_FLOOR} per class takes precedence, "
                         "so very small values are rounded up to whatever that floor requires.")
    ap.add_argument("--seed", type=int, default=20260802)
    ap.add_argument("--site", type=pathlib.Path, default=DEFAULT_SITE,
                    help="SvelteKit project root (default: site/)")
    ap.add_argument("--items", type=pathlib.Path, default=BENCH / "items.jsonl")
    args = ap.parse_args()
    lib_out = args.site / "src" / "lib" / "data"
    static_out = args.site / "static" / "data"

    if not args.items.exists():
        sys.exit(f"no item bank at {args.items}")

    run_mod = load_run_module()
    urls = case_urls()
    verd = verdict_index()

    items = list(read_jsonl(args.items))
    print(f"read {len(items):,} items from {args.items}")
    # --n 0 means the whole bank (decided 2026-08-04: the audit is to
    # cover every item, not a sample). The sampler degenerates cleanly: the
    # round-robin exhausts every stratum, so order stays deterministic.
    if args.n == 0:
        args.n = len(items)

    # ---- bench-wide counts (over the WHOLE bank, not the sample) ----
    tasks = collections.Counter(it["task"] for it in items)
    splits = collections.Counter(it["split"] for it in items)
    labels = collections.defaultdict(collections.Counter)
    eras = collections.Counter()
    era_by_task = collections.defaultdict(collections.Counter)
    tags = collections.Counter()
    respondents = collections.Counter()
    extract_chars = []
    for it in items:
        labels[it["task"]][it["label"]] += 1
        y = era_of(it)
        eras[y] += 1
        era_by_task[it["task"]][y] += 1
        for t in it["tags"]:
            tags[t.split(":")[0] if ":" in t else t] += 1
        respondents[it["inputs"]["metadata_shown"].get("respondent")] += 1
        extract_chars.append(len(it["inputs"]["extract_text"]))
    extract_chars.sort()

    sample = stratified_sample(items, args.n, args.seed)
    print(f"sampled {len(sample)} items over "
          f"{len({(i['task'], i['label'], era_of(i)) for i in sample})} strata")
    # Emit in case order so the browser's default view groups siblings — the
    # same case under T1 and T1-triage lands adjacent, which is the comparison
    # the design is built around. (Selection order is diagnostic-class-first,
    # which would otherwise put every appeal flip at the top of the table.)
    sample.sort(key=lambda it: (it["case_number"], it["task"], it["item_id"]))

    items_dir = static_out / "items"
    if items_dir.exists():
        shutil.rmtree(items_dir)
    items_dir.mkdir(parents=True, exist_ok=True)
    lib_out.mkdir(parents=True, exist_ok=True)

    # Model outputs are collected for the WHOLE bank first: the audit floor
    # below needs to know which items any model got confidently wrong before
    # the sample is drawn.
    migrations = load_id_migrations()
    by_id = {it["item_id"]: it for it in items}
    score_mod = load_score_module()
    outputs_by_item, groups_by_item, answers_accounting = model_outputs_for(
        {it["item_id"] for it in items}, migrations, by_id, score_mod)
    stale = stale_inputs_summary(groups_by_item)
    confident_wrong = sorted(
        iid for iid, outs in outputs_by_item.items()
        if any(o["answer"] != by_id[iid]["label"]
               and (o["probability"] or 0) >= 0.8 for o in outs))
    sampled_ids = {it["item_id"] for it in sample}
    floor_added = [by_id[i] for i in confident_wrong if i not in sampled_ids]
    sample.extend(floor_added)
    sample.sort(key=lambda it: (it["case_number"], it["task"], it["item_id"]))
    print(f"model outputs: {sum(len(v) for v in outputs_by_item.values())} archived answers; "
          f"audit floor added {len(floor_added)} of {len(confident_wrong)} confidently-wrong "
          f"items (wrong at stated p>=0.8) not already sampled")
    print(f"  archived ids: {answers_accounting['n_ids_referenced']} referenced, "
          f"{answers_accounting['n_ids_mapped']} mapped through id_migrations.jsonl, "
          f"{answers_accounting['n_ids_unresolved']} unresolved "
          f"({answers_accounting['n_answers_unresolved']} answers: T5 items live in a "
          "separate file, plus the pre-l2.4 ids DEFECTS R16 records)")
    print(f"  input-stale: {stale['n_answers_stale']} of {stale['n_answers']} archived "
          f"answers were elicited against inputs that have since moved "
          f"({stale['n_groups_stale']}/{stale['n_groups']} (run, item) groups, "
          f"{stale['n_items_affected']} items) — clears by rerunning, not by editing")
    for cause, n in stale["by_cause"].items():
        print(f"    {n:5d} answers  {cause}")

    index = []
    for it in sample:
        ref = it["inputs"]["clause_ref"]
        meta = it["inputs"]["metadata_shown"]
        prov = it["inputs"].get("extract_provenance") or []
        src_file = prov[0]["file"] if prov else None
        url = urls.get(src_file) or (urls.get(src_file.replace(".pdf", ".html")) if src_file else None)
        rec = receipts_for(it, verd)

        index.append({
            "item_id": it["item_id"],
            "task": it["task"],
            "label": it["label"],
            "split": it["split"],
            "case_number": it["case_number"],
            "clause": ref.get("clause"),
            "code_year": ref.get("code_year"),
            "respondent": meta.get("respondent"),
            "complainant_category": meta.get("complainant_category"),
            "complainant_anonymous": meta.get("complainant_anonymous"),
            "complainant_contactable": meta.get("complainant_contactable"),
            "date_received": meta.get("date_received"),
            "tags": [t for t in it["tags"] if not t.startswith("sibling_group:")],
            "extract_chars": len(it["inputs"]["extract_text"]),
            "has_clause_text": bool(ref.get("clause_text")),
            "n_renditions": len(it["inputs"].get("renditions") or []),
            "basis": rec.get("basis"),
            "case_url": url,
            "model_calls": len(outputs_by_item.get(it["item_id"], [])),
            "model_wrong": sum(1 for o in outputs_by_item.get(it["item_id"], [])
                               if o["answer"] != it["label"]),
            "max_wrong_p": max((o["probability"] for o in outputs_by_item.get(it["item_id"], [])
                                if o["answer"] != it["label"] and o["probability"] is not None),
                               default=None),
        })

        full = {
            "item_id": it["item_id"],
            "task": it["task"],
            "split": it["split"],
            "case_number": it["case_number"],
            "case_url": url,
            "tags": it["tags"],
            "sibling_case_numbers": it.get("sibling_case_numbers"),
            "contamination": it.get("contamination"),
            # What the model is given.
            "shown": {
                "clause_ref": ref,
                "metadata_shown": meta,
                "extract_text": it["inputs"]["extract_text"],
                "renditions": it["inputs"].get("renditions") or [],
            },
            "prompts": build_prompts(run_mod, it),
            # WITHHELD from the model — review only. Model outputs sit here
            # because an answer plus a correctness mark leaks the label.
            "withheld": {
                "label": it["label"],
                "receipts": rec,
                "provenance": prov,
                "model_outputs": outputs_by_item.get(it["item_id"], []),
                # One row per (run, item) over the calls above: `first`/`n`
                # index into model_outputs, so the drawer folds a P1 sweep into
                # its measurement without a second copy of the calls.
                "model_groups": groups_by_item.get(it["item_id"], []),
            },
        }
        (items_dir / f"{it['item_id']}.json").write_text(
            json.dumps(full, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    (lib_out / "items_index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    runs = collect_runs()
    (lib_out / "runs.json").write_text(
        json.dumps(runs, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"exported {len(runs)} scored runs")

    # The rename map, for the browser. Reviews are captured under the item_id in
    # localStorage, so the 261 ids the wave renamed would orphan silently
    # (DEFECTS R16 residual); site/src/lib/review.js migrates its store through
    # this file. Sorted, so the export stays byte-stable.
    (lib_out / "id_migrations.json").write_text(
        json.dumps(dict(sorted(migrations.items())), ensure_ascii=False,
                   separators=(",", ":")), encoding="utf-8")
    print(f"exported {len(migrations)} id migrations for the review store")

    items_by_id = by_id
    board_data = leaderboard(score_mod, items_by_id, migrations, exclusion_reasons())
    (lib_out / "leaderboard.json").write_text(
        json.dumps(board_data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for b in board_data["boards"]:
        a = b["accounting"]
        print(f"exported leaderboard: {b['board_id']} ({len(b['entries'])} models, "
              f"{b['n_items']} items) — {a['n_scored']} scored of {a['n_expected']} "
              f"archived, {a['n_mapped']} id(s) mapped, {a['n_absent']} absent"
              + (": " + ", ".join(f"{r['item_id']} ({r['reason']})" for r in a["absent"])
                 if a["absent"] else ""))

    meta = {
        "generated_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": args.seed,
        "bank": {
            "n_items": len(items),
            "n_cases": len({it["case_number"] for it in items}),
            "tasks": dict(sorted(tasks.items())),
            "splits": dict(sorted(splits.items())),
            "labels": {t: dict(sorted(c.items())) for t, c in sorted(labels.items())},
            "eras": {str(k): v for k, v in sorted(eras.items(), key=lambda kv: (kv[0] is None, kv[0]))},
            "eras_by_task": {t: {str(k): v for k, v in sorted(c.items(), key=lambda kv: (kv[0] is None, kv[0]))}
                             for t, c in sorted(era_by_task.items())},
            "tags": dict(tags.most_common()),
            "top_respondents": dict(respondents.most_common(15)),
            "extract_chars": {
                "min": extract_chars[0], "max": extract_chars[-1],
                "median": extract_chars[len(extract_chars) // 2],
                "mean": round(sum(extract_chars) / len(extract_chars)),
            },
        },
        "sample": {
            "n": len(sample),
            "tasks": dict(sorted(collections.Counter(i["task"] for i in sample).items())),
            "labels": dict(sorted(collections.Counter(f"{i['task']}:{i['label']}" for i in sample).items())),
            "eras": {str(k): v for k, v in sorted(collections.Counter(era_of(i) for i in sample).items(),
                                                  key=lambda kv: (kv[0] is None, kv[0]))},
            "policy": (f"deterministic (seed {args.seed}): a floor of {TAG_FLOOR} items for each "
                       "diagnostic class, then round-robin over (task, label, Code year); plus "
                       "every item any archived run answered wrongly at stated p>=0.8 (the "
                       "audit floor — where a label error would hide)"),
        },
        "tasks_described": {
            "T1": {"question": "Did the Panel rule a breach of this clause?",
                   "answers": ["breach", "no_breach"],
                   "shown": "complaint + respondent's written response",
                   "n": tasks.get("T1", 0)},
            "T1-triage": {"question": "Did the Panel rule a breach of this clause?",
                          "answers": ["breach", "no_breach"],
                          "shown": "complaint only — the defence is hidden",
                          "n": tasks.get("T1-triage", 0)},
            "T3": {"question": "Did the Appeal Board uphold the Panel's ruling, or overturn it?",
                   "answers": ["upheld", "overturned"],
                   "shown": "complaint + response + the Panel's ruling for the clause",
                   "n": tasks.get("T3", 0)},
        },
        "protocols": {
            "P2": "verbalized: one call per item; the model states an answer and a probability",
            "P1": "behaviourist: K verdict-only calls over perturbed presentations; confidence is the modal-answer frequency, computed by us",
        },
        "runs": {
            "n_scored": len(runs),
            "headline": headline_runs(runs),
        },
        # What the archive holds vs what could be attached to an item page.
        # Durable rather than a stdout line: unresolved ids are answers that
        # exist and are shown nowhere, which is exactly the class that has to
        # stay countable (T5 by the separate-file rule, R16's pre-l2.4 ids).
        "archived_answers": answers_accounting,
        # How much of the archive is answering a prompt the bank no longer
        # serves. Durable and countable on purpose: the number is the backlog
        # for the T3/pre-l2.4 reruns, and its endgame is zero.
        "stale_inputs": stale,
        "audit": audit_summary(),
        "sources": [
            {"file": "bench/items.jsonl", "sha256": sha256_of(args.items),
             "bytes": args.items.stat().st_size},
        ],
    }
    (lib_out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    lib_bytes = sum(p.stat().st_size for p in lib_out.glob("*.json"))
    static_bytes = sum(p.stat().st_size for p in items_dir.glob("*.json"))
    print(f"wrote {lib_out} ({lib_bytes/1e3:.0f} KB, bundled at build time)")
    print(f"wrote {items_dir} ({len(index)} files, {static_bytes/1e6:.1f} MB, fetched on demand)")


if __name__ == "__main__":
    main()
