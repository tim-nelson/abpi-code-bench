"""Export site-ready JSON for the review website in `site/`.

    python3 bench/export_site_data.py                 # default: whole active bank
    python3 bench/export_site_data.py --n 400 --seed 7

Writes into `site/` (override with --site). Small files the site always needs
go to `src/lib/data/` so they are imported at build time and prerendered; the
heavy per-item payloads go to `static/data/` and are fetched on demand.

  src/lib/data/meta.json          bench-wide counts: tasks, labels, eras,
                     splits, tags, publication scope, source hashes and
                     headline runs
  src/lib/data/items_index.json   one light row per published item (client-side
                     filtering; also the prerender entry list)
  src/lib/data/runs.json          scored runs explicitly activated in
                     bench/active_results.json: manifest + scores payload
  src/lib/data/id_migrations.json bench/id_migrations.jsonl as a plain
                     old_id -> new_id map, for the browser: reviews are
                     captured under the item_id, and 261 of them were
                     renamed by the 2026-08-09 wave (DEFECTS R16 residual),
                     so site/src/lib/review.js migrates its localStorage
                     store through this map
  static/data/items/<id>.json     per item: the EXACT model-facing prompts
                     (built through bench/run.py's own request builder, so
                     they are byte-identical to the canonical exported request), the full
                     untruncated extract, and — separately marked — the
                     withheld label with its receipts and the PMCPA case URL

Nothing here is a new source of truth: labels come from bench/items.jsonl,
receipts from data/l2/cases.jsonl, case URLs from data/manifest.jsonl, and
prompts from bench/run.py. The default export contains the full active bank;
an explicitly requested smaller review subset is deterministic in --seed.
"""

import argparse
import collections
import hashlib
import importlib.util
import json
import pathlib
import random
import shutil
import sys
from types import SimpleNamespace

BENCH = pathlib.Path(__file__).resolve().parent
ROOT = BENCH.parent
DEFAULT_SITE = ROOT / "site"
ACTIVE_RESULTS_PATH = BENCH / "active_results.json"
SCORING_INPUTS_SCHEMA = "pmcpa.score-inputs.v1"
CURRENT_P3_PROTOCOL = "P3"
LEGACY_REPEATED_STATED_CONDITION = "repeated_stated_probability"
LINEAR_PROBABILITY_POOL = "linear_probability_pool"
# Entry-level marker for a P2-board entry computed from an active P3 run's
# verdict votes (modal answer over the K byte-identical calls; confidence =
# modal frequency) rather than from a native P2 run. The marker is what keeps
# native and derived entries from ever being conflated on one board.
DERIVED_VOTE_FROM_P3 = "derived_vote_from_p3"

# An explicitly requested small review subset keeps a floor of each predefined
# descriptive tag so the site remains useful for manual inspection.
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


def manifest_method_field(manifest, field):
    """Read one method discriminator from its durable manifest/config binding."""
    value = manifest.get(field)
    if value is None:
        value = (manifest.get("config") or {}).get(field)
    return value


def is_repeated_stated(value):
    """Whether a record is current P3, including its immutable legacy source."""
    return (value.get("protocol") == CURRENT_P3_PROTOCOL
            or (value.get("protocol") == "P1"
                and manifest_method_field(value, "protocol_condition")
                == LEGACY_REPEATED_STATED_CONDITION))


def public_protocol(value):
    """Current public method id without rewriting paid source provenance."""
    return CURRENT_P3_PROTOCOL if is_repeated_stated(value) else value.get("protocol")


def site_method_key(value):
    """Identity that must never be collapsed in summaries or boards."""
    if is_repeated_stated(value):
        return (CURRENT_P3_PROTOCOL, None, manifest_method_field(value, "aggregation"),
                int(value.get("k") or 0))
    return (public_protocol(value), None, None, None)


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


def local_file_binding(path):
    """The score.py provenance shape for one current local file."""
    path = pathlib.Path(path)
    if not path.exists():
        return {"present": False, "sha256": None, "bytes": None,
                "basename": path.name}
    return {"present": True, "sha256": sha256_of(path),
            "bytes": path.stat().st_size, "basename": path.name}


def canonical_sha256(value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rename_selective_prediction_to_sp(value):
    """Rename retired selective-prediction keys to `sp` in exported copies.

    Two legacy spellings exist: `p3` from before the protocol renumbering and
    `p4` from before SP was split out of the P-code namespace (2026-08-16).
    Current P3 owns the top-level repeated stated-confidence diagnostics, so a
    blanket key rename would be wrong.  Both retired keys are unambiguous by
    their risk–coverage payload (`aurc` + `curve`).
    """
    if isinstance(value, list):
        return [_rename_selective_prediction_to_sp(row) for row in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, child in value.items():
        normalized = _rename_selective_prediction_to_sp(child)
        target = ("sp" if key in ("p3", "p4") and isinstance(child, dict)
                  and "aurc" in child and "curve" in child else key)
        if target in out and out[target] != normalized:
            raise ValueError(f"conflicting {target} score blocks during site normalization")
        out[target] = normalized
    return out


def public_scores(scores, manifest):
    """Site copy using current P1/P2/P3 and SP names.

    The immutable paid P3 source was originally catalogued as a P1 repeated
    condition and scored under a `p1r` diagnostics key.  That evidence remains
    unchanged on disk; only this publication copy resolves it to current P3.
    """
    out = _rename_selective_prediction_to_sp(scores)
    if is_repeated_stated(manifest):
        legacy = out.pop("p1r", None)
        if legacy is not None:
            if "p3" in out and out["p3"] != legacy:
                raise ValueError("conflicting current P3 and legacy p1r diagnostics")
            out["p3"] = legacy
        out["protocol"] = CURRENT_P3_PROTOCOL
        out["protocol_condition"] = None
        out["aggregation"] = LINEAR_PROBABILITY_POOL
    return out


def local_request_config_binding(manifest_path, requests_path):
    """Reconstruct score.py's inspectable semantic request-config binding."""
    manifest_path = pathlib.Path(manifest_path)
    requests_path = pathlib.Path(requests_path)
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else {})
    requests = list(read_jsonl(requests_path)) if requests_path.exists() else []
    payload = {
        "manifest_config": manifest.get("config"),
        "manifest_config_hash": manifest.get("config_hash"),
        "request_config_hashes": sorted({
            row["config_hash"] for row in requests
            if isinstance(row.get("config_hash"), str)
        }),
    }
    return {"sha256": canonical_sha256(payload), **payload}


def verify_scoring_input_bindings(run_id, run_dir, scores, current_items_path):
    """Fail closed if a modern score no longer binds its exact input bytes."""
    recorded = scores.get("scoring_inputs")
    if not isinstance(recorded, dict):
        sys.exit(f"active result {run_id!r} has no scoring_inputs provenance")
    if recorded.get("schema_version") != SCORING_INPUTS_SCHEMA:
        sys.exit(
            f"active result {run_id!r} has unsupported scoring_inputs schema "
            f"{recorded.get('schema_version')!r}")

    actual_paths = {
        "items": pathlib.Path(current_items_path),
        "manifest": run_dir / "manifest.json",
        "requests": run_dir / "requests.jsonl",
        "responses": run_dir / "responses.jsonl",
        "ledger": run_dir / "ledger.jsonl",
    }
    defects = []
    for role, path in actual_paths.items():
        expected = recorded.get(role)
        if not isinstance(expected, dict):
            defects.append(f"{role}=missing binding")
            continue
        actual = local_file_binding(path)
        # Exact dict equality also checks explicit absence, byte length and the
        # fixed role's basename; no silently ignored provenance fields exist.
        if expected != actual:
            defects.append(
                f"{role} binding mismatch "
                f"(recorded present/sha/bytes={expected.get('present')}/"
                f"{expected.get('sha256')}/{expected.get('bytes')}, current="
                f"{actual.get('present')}/{actual.get('sha256')}/{actual.get('bytes')})")
    expected_config = recorded.get("request_config")
    actual_config = local_request_config_binding(
        actual_paths["manifest"], actual_paths["requests"])
    if not isinstance(expected_config, dict):
        defects.append("request_config=missing binding")
    elif expected_config != actual_config:
        defects.append(
            "request_config binding mismatch "
            f"(recorded sha={expected_config.get('sha256')}, "
            f"current={actual_config.get('sha256')})")
    if defects:
        sys.exit(f"active result {run_id!r} has stale scoring provenance: "
                 + "; ".join(defects))


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_active_results(path=ACTIVE_RESULTS_PATH):
    """Load the single publication registry for model results.

    `bench/runs/` is the append-only archive. A run appears in site exports only
    after its id is added here; an empty registry therefore means a genuinely
    empty active results surface without deleting or moving archive evidence.
    """
    empty = {
        "active_run_ids": [],
        "leaderboard_boards": [],
        "leaderboard_excluded": [],
    }
    if not path.exists():
        return {**empty, "p4": {"core_run_ids": [], "qualification_run_ids": []}}
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in empty:
        if key not in data:
            data[key] = []
        if not isinstance(data[key], list):
            sys.exit(f"{path}: {key} must be a JSON list")

    run_ids = data["active_run_ids"]
    if any(not isinstance(run_id, str) or not run_id for run_id in run_ids):
        sys.exit(f"{path}: active_run_ids must contain non-empty strings")
    if len(run_ids) != len(set(run_ids)):
        sys.exit(f"{path}: active_run_ids contains a duplicate")

    declared = set(run_ids)
    referenced = set()
    for board in data["leaderboard_boards"]:
        for entry in board.get("entries", []):
            referenced.update(source[0] for source in entry.get("sources", []))
    for row in data["leaderboard_excluded"]:
        referenced.update(row.get("runs", []))
    unknown = sorted(referenced - declared)
    if unknown:
        sys.exit(f"{path}: leaderboard references run(s) not activated: {unknown}")

    # The P4 block is a separate publication surface with its own scorer
    # (bench/p4_score.py). Its runs must never enter active_run_ids: score.py
    # cannot read a cost_sweep run, and an apparently active P4 id would fail
    # the ordinary refresh in a misleading place.
    p4 = data.get("p4")
    if p4 is None:
        data["p4"] = {"core_run_ids": [], "qualification_run_ids": []}
    else:
        if not isinstance(p4, dict):
            sys.exit(f"{path}: p4 must be a JSON object")
        p4_keys = {"core_run_ids", "qualification_run_ids"}
        unknown_p4 = sorted(set(p4) - p4_keys)
        if unknown_p4:
            sys.exit(f"{path}: unknown p4 registry key(s): {unknown_p4}")
        for key in sorted(p4_keys):
            ids = p4.get(key)
            if (not isinstance(ids, list)
                    or any(not isinstance(r, str) or not r for r in ids)):
                sys.exit(f"{path}: p4.{key} must be a list of non-empty strings")
            if len(ids) != len(set(ids)):
                sys.exit(f"{path}: p4.{key} contains a duplicate")
        overlap = sorted(
            (set(p4["core_run_ids"]) | set(p4["qualification_run_ids"]))
            & set(run_ids))
        if overlap:
            sys.exit(f"{path}: P4 run(s) may not enter active_run_ids — the "
                     f"P1-P3 scorer cannot read them: {overlap}")
    return data


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
        # L2 notes are audit receipts, not task identifiers. Keep their substance
        # while presenting the current public task name after the T1-triage -> T2
        # rename; the archival L2 source remains untouched.
        "note": (v.get("note") or "").replace("T1-triage", "T2") or None,
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
    on predefined descriptive review tags."""
    if n >= len(items):
        return list(items)
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
    for protocol in ("P1", "P2"):
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


def require_complete_active_run(run_id, run_dir, current_items_path=None):
    """Fail closed unless ``scores.json`` covers the whole current call catalog.

    Historical/partial runs may stay in ``bench/runs`` indefinitely, but the
    active registry is publication state. A stale prefix score or a run with
    pending, duplicate or unscoreable calls must never become an apparently
    complete active result.
    """
    manifest_path = run_dir / "manifest.json"
    scores_path = run_dir / "scores.json"
    requests_path = run_dir / "requests.jsonl"
    for path in (manifest_path, scores_path, requests_path):
        if not path.exists():
            sys.exit(f"active result {run_id!r} is missing {path.name}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    coverage = scores.get("coverage") or {}
    if coverage.get("mode") != "planned_ledger":
        sys.exit(f"active result {run_id!r} is not a fresh planned-ledger run")
    verify_scoring_input_bindings(
        run_id, run_dir, scores, current_items_path or (BENCH / "items.jsonl"))

    request_count = sum(1 for _ in read_jsonl(requests_path))
    planned = coverage.get("planned")
    expected_calls = manifest.get("n_calls_planned")
    expected_items = manifest.get("n_items_planned")
    defects = []
    if scores.get("protocol_namespace") != "active":
        defects.append(
            f"protocol_namespace={scores.get('protocol_namespace')} (want active)")
    if planned != request_count or planned != expected_calls:
        defects.append(
            f"score planned={planned}, catalog={request_count}, manifest={expected_calls}")
    for key in ("parsed", "receipted", "parsed_receipts"):
        if coverage.get(key) != planned:
            defects.append(f"{key}={coverage.get(key)} (want {planned})")
    for key in ("pending", "errors", "calls_with_duplicate_parsed_receipts",
                "orphan_receipt_call_ids", "unlinked_response_rows",
                "unlinked_ledger_rows", "calls_outside_horizon",
                "receipt_calls_outside_horizon"):
        if coverage.get(key, 0) != 0:
            defects.append(f"{key}={coverage.get(key)}")
    if (coverage.get("items_planned") != expected_items
            or coverage.get("items_scored") != expected_items):
        defects.append(
            f"items scored/planned/manifest={coverage.get('items_scored')}/"
            f"{coverage.get('items_planned')}/{expected_items}")
    if scores.get("dropped"):
        defects.append(f"dropped={len(scores['dropped'])}")
    if public_protocol(manifest) not in {"P1", "P2", CURRENT_P3_PROTOCOL}:
        defects.append(
            f"active provider-run protocol={public_protocol(manifest)!r}; "
            "SP (selective prediction) is derived offline and is not an "
            "activatable call run")
    if scores.get("protocol") != manifest.get("protocol"):
        # The immutable paid precursor is resolved by score.py to active P3
        # while retaining its P1 source identity in protocol_provenance.
        if not (is_repeated_stated(manifest)
                and scores.get("protocol") == CURRENT_P3_PROTOCOL):
            defects.append(
                f"score/manifest protocol={scores.get('protocol')!r}/"
                f"{manifest.get('protocol')!r}")
    protocol_condition = manifest_method_field(manifest, "protocol_condition")
    aggregation = manifest_method_field(manifest, "aggregation")
    if protocol_condition not in (None, LEGACY_REPEATED_STATED_CONDITION):
        defects.append(f"unknown protocol_condition={protocol_condition!r}")
    if (protocol_condition == LEGACY_REPEATED_STATED_CONDITION
            and manifest.get("protocol") != "P1"):
        defects.append(
            "legacy repeated-stated condition is valid only on immutable P1 provenance")
    expected_score_condition = None if is_repeated_stated(manifest) else protocol_condition
    if scores.get("protocol_condition") != expected_score_condition:
        defects.append(
            f"score/manifest protocol_condition={scores.get('protocol_condition')!r}/"
            f"{protocol_condition!r} (resolved want {expected_score_condition!r})")
    if scores.get("aggregation") != aggregation:
        defects.append(
            f"score/manifest aggregation={scores.get('aggregation')!r}/{aggregation!r}")
    protocol_provenance = scores.get("protocol_provenance") or {}
    if protocol_provenance.get("schema_version") != "pmcpa.protocol-resolution.v1":
        defects.append("protocol_provenance schema is missing or unsupported")
    if protocol_provenance.get("active_protocol") != public_protocol(manifest):
        defects.append(
            f"protocol_provenance active_protocol="
            f"{protocol_provenance.get('active_protocol')!r} "
            f"(want {public_protocol(manifest)!r})")
    if protocol_provenance.get("source_protocol") != manifest.get("protocol"):
        defects.append("protocol_provenance source_protocol disagrees with manifest")
    if protocol_provenance.get("source_protocol_condition") != protocol_condition:
        defects.append(
            "protocol_provenance source_protocol_condition disagrees with manifest")
    if protocol_provenance.get("storage_identity_preserved") is not True:
        defects.append("protocol_provenance does not attest storage identity preservation")
    alias_summary = protocol_provenance.get("receipt_aliases") or {}
    if alias_summary.get("schema_version") != "pmcpa.receipt-alias.v1":
        defects.append("protocol_provenance receipt-alias summary is missing or unsupported")
    n_alias = alias_summary.get("n_alias_calls")
    n_native = alias_summary.get("n_native_calls")
    if (not isinstance(n_alias, int) or isinstance(n_alias, bool) or n_alias < 0
            or not isinstance(n_native, int) or isinstance(n_native, bool)
            or n_native < 0):
        defects.append("receipt-alias call counts are invalid")
    else:
        if n_alias + n_native != coverage.get("parsed"):
            defects.append(
                f"receipt alias/native counts={n_alias}+{n_native} "
                f"(want parsed={coverage.get('parsed')})")
        if coverage.get("receipt_aliases") != n_alias:
            defects.append(
                f"coverage receipt_aliases={coverage.get('receipt_aliases')} "
                f"(want {n_alias})")
        if coverage.get("native_receipts") != n_native:
            defects.append(
                f"coverage native_receipts={coverage.get('native_receipts')} "
                f"(want {n_native})")
        source_runs = alias_summary.get("source_runs") or []
        registry = alias_summary.get("registry")
        if n_alias:
            if manifest.get("protocol") != CURRENT_P3_PROTOCOL:
                defects.append("receipt aliases are valid only in a native P3 target run")
            if (not isinstance(registry, dict)
                    or registry.get("n_rows") != n_alias
                    or not registry.get("sha256")):
                defects.append("receipt-alias registry summary does not bind every alias")
            if (not isinstance(source_runs, list) or not source_runs
                    or not all(isinstance(row, dict) for row in source_runs)
                    or sum(row.get("n_calls", 0) for row in source_runs) != n_alias):
                defects.append("receipt-alias source-run counts do not sum to alias count")
        elif registry is not None or source_runs:
            defects.append("zero-alias summary unexpectedly names a registry/source run")
    repeated_stated = is_repeated_stated(manifest)
    if repeated_stated and aggregation != LINEAR_PROBABILITY_POOL:
        defects.append(
            f"P3 repeated stated-confidence aggregation={aggregation!r} "
            f"(want {LINEAR_PROBABILITY_POOL!r})")
    if (manifest.get("protocol") == "P2" or repeated_stated) and (
            scores.get("k") != manifest.get("through_repeats")):
        defects.append(
            f"score K={scores.get('k')} but manifest K={manifest.get('through_repeats')}")
    current_runner_hash = sha256_of(BENCH / "run.py")
    # Reviewed runner lineage. Runs stay bound to the runner that planned
    # them; a hash is admitted here only when the diff to the current runner
    # is reviewed as request-identical. 2026-08-15: docstring-only edit
    # (P3/P4 protocol naming); 2026-08-16: docstring-only edit (P4 renamed
    # SP; P4 reserved) — no request-building change either time; register
    # entries in bench/review/DEFECTS.md ("runner docstring lineage").
    accepted_runner_hashes = {
        current_runner_hash,
        "c2d603af374afba7dad5e226259d63061a7362732774201638617804799f90ba",
        "e35f47bb8ab4a485efde85de7dcd65ef2a1483f7dcc3906302a59f11358ebe9d",
    }
    recorded_runner_hash = (manifest.get("config") or {}).get("runner_sha256")
    if recorded_runner_hash not in accepted_runner_hashes:
        defects.append(
            f"runner_sha256={recorded_runner_hash} (current {current_runner_hash})")
    if defects:
        sys.exit(f"active result {run_id!r} is incomplete or stale: " + "; ".join(defects))
    return scores, manifest


def collect_runs(active_run_ids, current_items_path=None):
    """Site-ready scores for explicitly activated archive runs only."""
    runs = []
    runs_dir = BENCH / "runs"
    if not active_run_ids:
        return runs
    for run_id in sorted(active_run_ids):
        d = runs_dir / run_id
        if not d.is_dir():
            sys.exit(f"active result {run_id!r} has no archive directory at {d}")
        source_scores, manifest = require_complete_active_run(
            run_id, d, current_items_path or (BENCH / "items.jsonl"))
        scores = public_scores(source_scores, manifest)
        # Absolute local paths are not site data.
        manifest.pop("items_path", None)
        scores.pop("run", None)
        score_items = scores.pop("items", None)
        source_protocol = manifest.get("protocol")
        source_condition = manifest_method_field(manifest, "protocol_condition")
        protocol = public_protocol(manifest)
        protocol_condition = None if is_repeated_stated(manifest) else source_condition
        aggregation = manifest_method_field(manifest, "aggregation")
        runs.append({
            "run_id": d.name,
            "dir": f"bench/runs/{d.name}",
            "created_utc": manifest.get("created_utc"),
            "model": scores.get("model") or manifest.get("model"),
            "protocol": protocol,
            "protocol_condition": protocol_condition,
            "aggregation": aggregation,
            "source_protocol": source_protocol,
            "source_protocol_condition": source_condition,
            "k": scores.get("k") or manifest.get("k"),
            "items": pathlib.Path(score_items).name if score_items else None,
            "manifest": manifest,
            "scores": scores,
        })
    runs.sort(key=lambda r: (r.get("created_utc") or "", r["run_id"]))
    return runs


# --- P4 incentivized deferral (own scorer, own surface) -----------------------
# P4 reads confidence off answer/refer decisions under explicit costs
# (bench/P4_SPEC.md). Its runs are registered under the registry's `p4` block,
# never under active_run_ids, and are scored by bench/p4_score.py. The
# payoff-sensitivity qualification of P4_SPEC.md 8b is recomputed here at
# export time from the anchor/dominance receipts themselves; the implied-
# confidence reading of a core deferral curve is licensed only for models
# that pass it.

P4_SITE_SCHEMA = "pmcpa.site-p4.v1"
P4_X = 100
P4_CONDITION_GRIDS = {
    "core": (5, 15, 25, 35, 45),
    "anchor": (1, 99),
    "dominance": (-10, 150),
}
P4_ANCHOR_COST = 1    # rational threshold .99
P4_GAINS_COST = -10   # referring GAINS 10 points: 100% deferral is the only rational rate
P4_CLASSES = ("responsive", "weakly_responsive", "payoff_blind")
# Classes are thresholded on the anchor c=1 deferral rate. FINDINGS 0.4: the
# c=1 anchor is the discriminating cell — every model that defers at all still
# violates gains-dominance, and gemini-3.7-flash defers under gains while its
# anchor row is blind, so the gains cell is reported but does not classify.
P4_RESPONSIVE_MIN_C1 = 0.40
P4_BLIND_MAX_C1 = 0.05
# Ground truth: the qualification table of docs/FINDINGS.md 0.4 (2026-08-16).
# Every cell is recomputed from receipts; a computed class that contradicts
# this table refuses to publish rather than silently rewriting the finding.
P4_EXPECTED_CLASSES = {
    "gpt-5.6-luna": "responsive",
    "gpt-5.6-sol": "responsive",
    "gpt-5.6-terra": "responsive",
    "grok-4.6": "weakly_responsive",
    "moonshotai/kimi-k3": "weakly_responsive",
    "claude-opus-5": "payoff_blind",
    "google/gemini-3.7-flash": "payoff_blind",
    "z-ai/glm-5.2": "payoff_blind",
    "deepseek/deepseek-v4-pro": "payoff_blind",
    "claude-sonnet-5": "payoff_blind",
    "claude-haiku-4-5-20251001": "payoff_blind",
}


def load_p4_score_module():
    """Import bench/p4_score.py so core metrics are the P4 scorer's, not a copy."""
    spec = importlib.util.spec_from_file_location(
        "bench_p4_score", BENCH / "p4_score.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def p4_run_receipts(run_id, allowed_conditions):
    """Manifest plus verified parsed decisions for one registered P4 run.

    Fail-closed structural checks: the directory and manifest exist; the run
    is P4/cost_sweep on the named condition's exact (X, grid); every receipt
    joins a catalogued request, carries one parsed answer/refer decision and
    names a unique (item, cost level). Quarantined calls have no receipt row
    by design (Kimi/GLM truncations, FINDINGS 0.4's dagger note): parsed
    counts are the denominators, and full-rectangle completeness is enforced
    by the caller only where the core curve requires it.
    """
    run_dir = BENCH / "runs" / run_id
    if not run_dir.is_dir():
        sys.exit(f"p4 registry run {run_id!r} has no directory at {run_dir}")
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"p4 registry run {run_id!r} is missing manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    condition = manifest.get("condition")
    if condition is None and "core" in allowed_conditions:
        # The two pilot core dirs predate named conditions (P4_SPEC.md 8b)
        # and carry condition null on the identical (X, grid) pair.
        condition = "core"
    if condition not in allowed_conditions:
        sys.exit(f"p4 registry run {run_id!r} has condition "
                 f"{manifest.get('condition')!r}; expected one of "
                 f"{sorted(allowed_conditions)}")
    defects = []
    if (manifest.get("protocol"), manifest.get("aggregation")) != ("P4", "cost_sweep"):
        defects.append(f"protocol/aggregation={manifest.get('protocol')!r}/"
                       f"{manifest.get('aggregation')!r} (want P4/cost_sweep)")
    grid = tuple(manifest.get("cost_grid") or ())
    if grid != P4_CONDITION_GRIDS[condition] or int(manifest.get("cost_x") or 0) != P4_X:
        defects.append(f"grid/X={list(grid)}/{manifest.get('cost_x')} (want "
                       f"{list(P4_CONDITION_GRIDS[condition])}/{P4_X})")
    requests_path = run_dir / "requests.jsonl"
    responses_path = run_dir / "responses.jsonl"
    for path in (requests_path, responses_path):
        if not path.exists():
            sys.exit(f"p4 registry run {run_id!r} is missing {path.name}")
    request_ids = {row["call_id"] for row in read_jsonl(requests_path)}
    if len(request_ids) != manifest.get("n_calls_planned"):
        defects.append(f"request catalog holds {len(request_ids)} calls; "
                       f"manifest plans {manifest.get('n_calls_planned')}")
    rows, seen_calls, seen_cells = [], set(), set()
    for receipt in read_jsonl(responses_path):
        call_id = receipt.get("call_id")
        if call_id not in request_ids:
            defects.append(f"orphan receipt {call_id!r}")
            continue
        if call_id in seen_calls:
            defects.append(f"duplicate receipt {call_id!r}")
            continue
        seen_calls.add(call_id)
        parsed = receipt.get("parsed") or {}
        if parsed.get("decision") not in ("answer", "refer"):
            defects.append(f"receipt {call_id!r} has no parsed answer/refer decision")
            continue
        cell = (receipt.get("item_id"), receipt.get("cost_points"))
        if cell in seen_cells:
            defects.append(f"duplicate (item, cost) receipt {cell!r}")
            continue
        seen_cells.add(cell)
        rows.append({"item_id": receipt["item_id"],
                     "cost_points": receipt["cost_points"],
                     "decision": parsed["decision"]})
    if defects:
        sys.exit(f"p4 registry run {run_id!r} failed receipt verification: "
                 + "; ".join(defects))
    return manifest, condition, rows


def p4_deferral_cell(rows, cost):
    """{refer, n} at one cost level, over parsed decisions only."""
    at = [row for row in rows if row["cost_points"] == cost]
    return {"refer": sum(1 for row in at if row["decision"] == "refer"),
            "n": len(at)}


def p4_classify(c1_rate):
    if c1_rate >= P4_RESPONSIVE_MIN_C1:
        return "responsive"
    if c1_rate <= P4_BLIND_MAX_C1:
        return "payoff_blind"
    return "weakly_responsive"


def p4_site_data(p4_registry, items_path, draws=1000, seed="pmcpa-bench"):
    """The site's P4 surface: qualification cells, classes and core curves.

    Deterministic in its inputs (receipts, item bank, registry) — no
    timestamps, stable ordering — so two exports byte-compare equal.
    """
    core_ids = list(p4_registry["core_run_ids"])
    qual_ids = list(p4_registry["qualification_run_ids"])
    payload = {
        "schema_version": P4_SITE_SCHEMA,
        "protocol": "P4",
        "task": "T1",
        "aggregation": "cost_sweep",
        "x": P4_X,
        "grids": {name: list(grid) for name, grid in P4_CONDITION_GRIDS.items()},
        "core_thresholds": [round(1 - c / P4_X, 4)
                            for c in P4_CONDITION_GRIDS["core"]],
        "qualification": {
            "anchor_cost": P4_ANCHOR_COST,
            "gains_cost": P4_GAINS_COST,
            "classes": list(P4_CLASSES),
            "rule": {
                "cell": "anchor_c1_deferral_rate",
                "responsive_min": P4_RESPONSIVE_MIN_C1,
                "payoff_blind_max": P4_BLIND_MAX_C1,
                "source": "docs/FINDINGS.md 0.4 qualification table (2026-08-16)",
            },
        },
        "models": [],
        "generated_from": {
            "registry": {"core_run_ids": core_ids,
                         "qualification_run_ids": qual_ids},
            "items_sha256": sha256_of(items_path),
            "config_hashes": {},
            "bootstrap": {"draws": draws, "seed": seed},
        },
    }
    if not qual_ids:
        if core_ids:
            sys.exit("p4 registry lists core runs without qualification runs; "
                     "a core curve without the P4_SPEC.md 8b gate licenses an "
                     "unqualified implied-confidence reading")
        return payload

    config_hashes = payload["generated_from"]["config_hashes"]
    by_model = {}
    for run_id in qual_ids:
        manifest, condition, rows = p4_run_receipts(
            run_id, ("anchor", "dominance"))
        config_hashes[run_id] = manifest.get("config_hash")
        slots = by_model.setdefault(manifest["model"], {})
        if condition in slots:
            sys.exit(f"p4 registry lists two {condition} runs for "
                     f"{manifest['model']!r}")
        slots[condition] = rows

    roster = set(by_model)
    if roster != set(P4_EXPECTED_CLASSES):
        sys.exit("p4 qualification roster disagrees with docs/FINDINGS.md 0.4: "
                 f"missing {sorted(set(P4_EXPECTED_CLASSES) - roster)}, "
                 f"unexpected {sorted(roster - set(P4_EXPECTED_CLASSES))}")

    p4_mod = load_p4_score_module()
    core_by_model = {}
    for run_id in core_ids:
        manifest, _, _ = p4_run_receipts(run_id, ("core",))
        config_hashes[run_id] = manifest.get("config_hash")
        model = manifest["model"]
        if model not in by_model:
            sys.exit(f"p4 core run {run_id!r} has no qualification runs for "
                     f"{model!r}")
        if model in core_by_model:
            sys.exit(f"p4 registry lists two core runs for {model!r}")
        scores = p4_mod.score_run(BENCH / "runs" / run_id, items_path,
                                  draws, seed)
        counts = scores["counts"]
        if counts["parsed"] != counts["planned"]:
            sys.exit(f"p4 core run {run_id!r} parsed {counts['parsed']} of "
                     f"{counts['planned']} planned calls; the core curve "
                     "requires the whole rectangle")
        core_by_model[model] = {
            "run_id": run_id,
            "n_items": counts["items"],
            "monotone_violations": scores["monotonicity"]["violations"],
            "levels": [
                {"c": c,
                 "threshold": round(1 - c / P4_X, 4),
                 "deferral_rate": scores["levels"][str(c)]["deferral_rate"],
                 "mean_loss": scores["levels"][str(c)]["mean_loss"]}
                for c in P4_CONDITION_GRIDS["core"]
            ],
        }

    class_rank = {name: rank for rank, name in enumerate(P4_CLASSES)}
    models = []
    for model in sorted(by_model):
        slots = by_model[model]
        missing = sorted(c for c in ("anchor", "dominance") if c not in slots)
        if missing:
            sys.exit(f"p4 qualification for {model!r} is missing {missing}")
        c1 = p4_deferral_cell(slots["anchor"], P4_ANCHOR_COST)
        gains = p4_deferral_cell(slots["dominance"], P4_GAINS_COST)
        for name, cell in (("anchor c=1", c1), ("gains", gains)):
            if not cell["n"]:
                sys.exit(f"p4 qualification for {model!r} has no parsed "
                         f"decisions at the {name} level")
        computed = p4_classify(c1["refer"] / c1["n"])
        expected = P4_EXPECTED_CLASSES[model]
        if computed != expected:
            sys.exit(
                f"p4 classification for {model!r} computed {computed!r} from "
                f"anchor c=1 deferral {c1['refer']}/{c1['n']}, but "
                f"docs/FINDINGS.md 0.4 records {expected!r}. Receipts and the "
                "findings table disagree; resolve that before publishing")
        models.append({
            "model": model,
            "class": computed,
            "qual": {"c1": c1, "gains": gains},
            "core": core_by_model.get(model),
        })
    models.sort(key=lambda row: (
        class_rank[row["class"]],
        -(row["qual"]["c1"]["refer"] / row["qual"]["c1"]["n"]),
        row["model"]))
    payload["models"] = models
    payload["generated_from"]["config_hashes"] = dict(
        sorted(config_hashes.items()))
    return payload


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
    archived bank's row for it. Every current board absence joins to one or
    more durable exclusion reasons; a miss leaves the reason null rather than
    inventing one.
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
#   renditions[*].extract_text                     -> historical legacy-P1 prompt variants
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


def model_outputs_for(sample_ids, migrations, items_by_id, score_mod, active_run_ids,
                      current_items_sha256, current_items_path=None):
    """Every active, parsed model answer for the selected published items.

    Reads raw responses.jsonl from explicitly activated archive runs after
    :func:`require_complete_active_run` proves the canonical score covers the
    whole current call catalog.
    Only calls with a parsed `answer` are shipped; a stated probability exists
    under P1 and each P3 draw.

    Renamed ids follow their item: 32 answers on 14 renamed ids, which would
    otherwise vanish from those items' pages while the answers sit in the
    archive. Returns the outputs plus the accounting for what did NOT attach —
    which reads as "absent from the bank" only because the caller passes the
    WHOLE bank (main() does; a subset would make `unresolved` mean "unsampled").

    Also returns one GROUP per (run, item). A group is the unit the drawer
    renders and the unit a prompt-condition caveat is true of. Under P2 and P3
    it is also the unit of measurement: respectively the K-call modal-answer
    frequency and K-draw stated-confidence linear pool. The group's confidence
    comes from score.py's own aggregate(), not from a second implementation.
    """
    outputs, groups = {}, collections.defaultdict(list)
    seen, mapped, unresolved = set(), set(), collections.Counter()
    runs_dir = BENCH / "runs"
    for run_id in sorted(active_run_ids):
        d = runs_dir / run_id
        if not d.is_dir():
            sys.exit(f"active result {run_id!r} has no archive directory at {d}")
        _, manifest = require_complete_active_run(
            run_id, d, current_items_path or (BENCH / "items.jsonl"))
        resp_path = d / "responses.jsonl"
        if not resp_path.exists():
            sys.exit(f"active result {run_id!r} has no responses.jsonl")
        request_by_call = {
            row["call_id"]: row for row in read_jsonl(d / "requests.jsonl")
        }
        witness = run_items(d.name)
        records = []
        with resp_path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    receipt = json.loads(line)
                except json.JSONDecodeError as exc:
                    sys.exit(f"active result {run_id!r} has corrupt responses.jsonl "
                             f"line {lineno}: {exc}")
                call_id = receipt.get("call_id")
                request = request_by_call.get(call_id)
                if request is None:
                    sys.exit(
                        f"active result {run_id!r} response line {lineno} has no "
                        f"catalogued request for call_id={call_id!r}"
                    )
                # Provider receipts deliberately do not duplicate the full
                # canonical request. Reattach its identity fields before asking
                # score.py to verify a P2 group is truly K byte-identical calls.
                # Parsed output and provider metadata remain receipt-owned.
                joined = {**receipt}
                for key in (
                    "call_id", "item_id", "task", "protocol", "task_rank",
                    "item_rank", "repeat_index", "config_hash", "request_sha256",
                    "prompt_sha256", "protocol_condition", "aggregation",
                    "request", "variant",
                ):
                    joined[key] = request.get(key)
                records.append(joined)
        exact_current_bank = manifest.get("items_sha256") == current_items_sha256
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
            source_method = {
                "protocol": rec.get("protocol") or manifest.get("protocol"),
                "protocol_condition": (rec.get("protocol_condition")
                                       if "protocol_condition" in rec
                                       else manifest_method_field(
                                           manifest, "protocol_condition")),
                "aggregation": (rec.get("aggregation")
                                or manifest_method_field(manifest, "aggregation")),
            }
            current_protocol = public_protocol(source_method)
            per_item[item_id].append((old_id, rec, {
                "run_id": d.name,
                "created_utc": manifest.get("created_utc"),
                "model": manifest.get("model"),
                "protocol": current_protocol,
                "protocol_condition": None,
                "aggregation": source_method["aggregation"],
                "source_protocol": source_method["protocol"],
                "source_protocol_condition": source_method["protocol_condition"],
                "repeat_index": rec.get("repeat_index"),
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
            protocol_conditions = {call.get("protocol_condition")
                                   for _, _, call in rows}
            aggregations = {call.get("aggregation") for _, _, call in rows}
            source_protocols = {call.get("source_protocol") for _, _, call in rows}
            source_conditions = {call.get("source_protocol_condition")
                                 for _, _, call in rows}
            if (len(old_ids) > 1 or len(protocols) > 1
                    or len(protocol_conditions) > 1 or len(aggregations) > 1
                    or len(source_protocols) > 1 or len(source_conditions) > 1):
                # One run serves one id per item and one protocol per call; if
                # that ever stops being true the group's k and modal frequency
                # would be computed over two different questions.
                sys.exit(f"{d.name}: item {item_id} has calls under {sorted(old_ids)}, "
                         f"protocols {sorted(str(p) for p in protocols)}, conditions "
                         f"{sorted(str(p) for p in protocol_conditions)} and aggregations "
                         f"{sorted(str(p) for p in aggregations)}; source protocols "
                         f"{sorted(str(p) for p in source_protocols)} and conditions "
                         f"{sorted(str(p) for p in source_conditions)}. A (run, item) "
                         "group must be one id under one protocol. Refusing.")
            if exact_current_bank:
                changed, reasons = False, []
            else:
                changed, reasons = group_input_change(
                    items_by_id[item_id], witness, rows[0][0], manifest.get("created_utc"))
            group = {
                "run_id": d.name,
                "created_utc": manifest.get("created_utc"),
                "model": manifest.get("model"),
                "protocol": rows[0][2]["protocol"],
                "protocol_condition": rows[0][2].get("protocol_condition"),
                "aggregation": rows[0][2].get("aggregation"),
                "source_protocol": rows[0][2].get("source_protocol"),
                "source_protocol_condition": rows[0][2].get(
                    "source_protocol_condition"),
                "k": manifest.get("k"),
                "n": len(rows),
                "inputs_changed": changed,
                "inputs_reasons": reasons,
                "_calls": [call for _, _, call in sorted(
                    rows, key=lambda value: int(value[1].get("repeat_index") or 1))],
            }
            try:
                semantics = score_mod.protocol_semantics(
                    group["source_protocol"], manifest.get("contract"),
                    group["source_protocol_condition"])
            except ValueError as exc:
                raise SystemExit(f"{d.name}: {exc}") from exc
            if semantics in score_mod.REPEATED_SEMANTICS:
                # score.py owns both K-call measurements. P2 returns a modal
                # answer frequency; repeated P1 returns the equal-weight pool
                # of probabilities after orienting every draw to one label.
                # Calling aggregate per (run,item) keeps the drawer identical
                # to the canonical score rather than reimplementing either.
                recs = [{**rec, "item_id": item_id} for _, rec, _ in rows]
                expected_repeats = (manifest.get("through_repeats")
                                    or manifest.get("k"))
                # Fresh repeated runs are all-or-nothing at K. Passing the
                # manifest horizon makes the exporter refuse a partial group.
                scored, dropped = score_mod.aggregate(
                    recs, items_by_id, group["source_protocol"],
                    expected_repeats=expected_repeats,
                    run_contract=manifest.get("contract"),
                    protocol_condition=group["source_protocol_condition"],
                    aggregation=group["aggregation"])
                if dropped or len(scored) != 1:
                    sys.exit(f"{d.name}: score.py could not aggregate the repeated calls for "
                             f"{item_id} ({dropped or len(scored)}). Refusing.")
                row = scored[0]
                dist = collections.Counter(call["answer"] for _, _, call in rows)
                if semantics == score_mod.RESAMPLING:
                    top = dist[row["answer"]]
                    if abs(row["p"] - top / row["n_parsed"]) > 1e-12:
                        sys.exit(f"{d.name}: the modal frequency for {item_id} disagrees "
                                 f"with score.py ({top}/{row['n_parsed']} vs "
                                 f"{row['p']}). Refusing.")
                order = sorted(dist, key=lambda a: (a != row["answer"], -dist[a], a))
                group["measured"] = {
                    "kind": (LINEAR_PROBABILITY_POOL
                             if semantics == score_mod.REPEATED_STATED
                             else "modal_answer_frequency"),
                    "answer": row["answer"],
                    "confidence": row["p"],
                    "n_parsed": row["n_parsed"],
                    "modal_tie": row["modal_tie"],
                    "distribution": [[a, dist[a]] for a in order],
                }
                if semantics == score_mod.REPEATED_STATED:
                    p3 = row.get("p3") or row.get("p1r")
                    if not p3:
                        sys.exit(f"{d.name}: P3 diagnostics missing for {item_id}. Refusing.")
                    group["measured"].update({
                        "positive_label": p3["positive_label"],
                        "positive_probability": p3["positive_probability"],
                        "pool_tie": p3["pool_tie"],
                        "vote": p3["vote"],
                        "draws": p3.get("draws"),
                        "single_draw": p3["single_draw"],
                        "dispersion": p3["dispersion"],
                    })
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
    """Tracking number for active answers elicited against an older prompt the
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
        "policy": ("an active answer is input-stale when the item fields bench/run.py "
                   "renders into the prompt differ between the run's bound item bank "
                   "and the active bank, or when equivalent prompt provenance cannot "
                   "be established"),
    }


def headline_runs(runs):
    """One pointer per model/method: the largest scored run, latest wins ties."""
    best = {}
    for r in runs:
        key = (r.get("model"), *site_method_key(r))
        n = (r["scores"].get("overall") or {}).get("n") or 0
        cur = best.get(key)
        if cur is None or (n, r.get("created_utc") or "") > (cur[0], cur[1].get("created_utc") or ""):
            best[key] = (n, r)
    out = []
    for _, (n, r) in sorted(best.items(), key=lambda kv: tuple(str(v) for v in kv[0])):
        o = r["scores"].get("overall") or {}
        if not n:
            continue
        out.append({
            "run_id": r["run_id"], "model": r.get("model"),
            "protocol": public_protocol(r),
            "protocol_condition": (None if is_repeated_stated(r)
                                   else r.get("protocol_condition")),
            "aggregation": r.get("aggregation"), "k": r.get("k"),
            "n": n, "accuracy": o.get("accuracy"), "brier": o.get("brier"), "ece": o.get("ece"),
            "ci": o.get("ci"),
        })
    out.sort(key=lambda r: (-(r["n"] or 0), r["model"] or ""))
    return out


# --- automatic cumulative-run boards ----------------------------------------


def _automatic_board_condition(manifest):
    """Prompt/order conditions which must match before runs share a board.

    Model-specific settings (model, thinking and effort) deliberately remain
    entry attributes: comparing those configurations is the point.  These
    fields instead determine which items were selected and whether the prompt
    itself changed.
    """
    config = manifest.get("config") or {}
    return {
        "seed": str(config.get("seed", manifest.get("seed", ""))),
        "rationale": bool(config.get("rationale", manifest.get("rationale", False))),
        "temperature": config.get("temperature", manifest.get("temperature")),
        "splits": list(manifest.get("splits_filter") or []),
        # The immutable paid precursor encoded P3 as a P1 condition. That
        # storage fact is provenance, not a different current board condition.
        "protocol_condition": (None if is_repeated_stated(manifest)
                               else manifest_method_field(
                                   manifest, "protocol_condition")),
        "aggregation": manifest_method_field(manifest, "aggregation"),
    }


def _automatic_board_candidates(score_mod, active_runs, current_items_path):
    """Materialize provenance-checked cumulative calls, one candidate per task."""
    candidates, excluded = [], []
    for run_rec in active_runs:
        run_id = run_rec["run_id"]
        run_dir = BENCH / "runs" / run_id
        _, manifest = require_complete_active_run(
            run_id, run_dir, current_items_path)
        contract = manifest.get("contract")
        source_protocol = manifest.get("protocol")
        source_protocol_condition = manifest_method_field(
            manifest, "protocol_condition")
        protocol = public_protocol(manifest)
        protocol_condition = None
        aggregation = manifest_method_field(manifest, "aggregation")
        try:
            semantics = score_mod.protocol_semantics(
                source_protocol, contract, source_protocol_condition)
        except ValueError as exc:
            raise SystemExit(f"automatic leaderboard {run_id}: {exc}") from exc
        if contract != score_mod.ACTIVE_RUN_CONTRACT:
            sys.exit(f"automatic leaderboard {run_id}: only active cumulative runs are eligible")

        requests = list(read_jsonl(run_dir / "requests.jsonl"))
        responses = list(read_jsonl(run_dir / "responses.jsonl"))
        ledger_path = run_dir / "ledger.jsonl"
        ledger = list(read_jsonl(ledger_path)) if ledger_path.exists() else []
        repeats = int(manifest.get("through_repeats") or manifest.get("k") or 1)
        try:
            records, coverage = score_mod.cumulative_records(
                requests, responses, ledger, None, repeats, semantics)
        except ValueError as exc:
            raise SystemExit(f"automatic leaderboard {run_id}: {exc}") from exc
        if (coverage["calls_pending"] or coverage["calls_errors"]
                or coverage["calls_parsed"] != coverage["calls_planned"]):
            sys.exit(f"automatic leaderboard {run_id}: cumulative receipts are incomplete")

        for task in sorted({rec["task"] for rec in records}):
            task_records = [rec for rec in records if rec["task"] == task]
            rank_items = collections.defaultdict(set)
            for rec in task_records:
                rank_items[int(rec["task_rank"])].add(rec["item_id"])
            ranks = sorted(rank_items)
            horizon = max(ranks, default=0)
            if ranks != list(range(1, horizon + 1)):
                excluded.append({
                    "model": run_rec.get("model"), "runs": [run_id],
                    "reason": (f"{protocol}/{task} is not a contiguous cumulative task-rank "
                               "prefix, so it is shown under Results but not auto-ranked."),
                })
                continue
            if any(len(ids) != 1 for ids in rank_items.values()):
                sys.exit(f"automatic leaderboard {run_id} {task}: one rank names multiple items")
            candidates.append({
                "run_id": run_id,
                "model": run_rec.get("model") or manifest.get("model"),
                "protocol": protocol,
                "protocol_condition": protocol_condition,
                "aggregation": aggregation,
                "source_protocol": source_protocol,
                "source_protocol_condition": source_protocol_condition,
                "source_aggregation": aggregation,
                "semantics": semantics,
                "task": task,
                "contract": contract,
                "condition": _automatic_board_condition(manifest),
                "records": task_records,
                "sequence": tuple(next(iter(rank_items[rank]))
                                  for rank in range(1, horizon + 1)),
                "horizon": horizon,
                "k": repeats if semantics in score_mod.REPEATED_SEMANTICS else 1,
            })
    candidates.extend(_derived_vote_candidates(score_mod, candidates))
    return candidates, excluded


def _derived_vote_candidates(score_mod, candidates):
    """P2-board candidates derived from active P3 runs' verdict votes.

    A P3 run already holds K byte-identical verdict draws per item, so its
    modal verdict (confidence = modal frequency) measures the same estimand a
    native P2 run does. The derived candidate keeps the P3 run's records and
    receipts untouched — score.py's own repeated-stated aggregation and
    p3_vote_rows produce the per-item vote rows downstream — and changes only
    the board method identity, wearing DERIVED_VOTE_FROM_P3 so a derived entry
    can never be read as a native P2 run. A model that has its own native P2
    candidate for the task keeps only the native entry; the derived duplicate
    is skipped, never merged.
    """
    native_p2 = {(candidate["model"], candidate["task"])
                 for candidate in candidates if candidate["protocol"] == "P2"}
    derived = []
    for candidate in candidates:
        if candidate["protocol"] != CURRENT_P3_PROTOCOL:
            continue
        if candidate["semantics"] != score_mod.REPEATED_STATED:
            sys.exit(
                f"automatic leaderboard {candidate['run_id']}: a "
                f"{CURRENT_P3_PROTOCOL} candidate carries semantics "
                f"{candidate['semantics']!r}, not {score_mod.REPEATED_STATED!r}, "
                "so no verdict-vote view can be derived from it. Refusing.")
        if (candidate["model"], candidate["task"]) in native_p2:
            continue
        # The vote view uses no probability pool, so its board condition is the
        # P3 run's own ordering/prompt condition with P2's aggregation identity
        # (None). It joins a native P2 board exactly when every remaining
        # condition field matches; _automatic_cumulative_boards refuses a
        # derived-only P2 board rather than letting a mismatch split silently.
        condition = dict(candidate["condition"])
        condition["aggregation"] = None
        derived.append({
            **candidate,
            "protocol": "P2",
            "protocol_condition": None,
            "aggregation": None,
            # Pin the source identity before the relabel: the records stay P3
            # records and are aggregated as such before the vote is read off.
            "source_protocol": candidate.get(
                "source_protocol", candidate["protocol"]),
            "source_protocol_condition": candidate.get(
                "source_protocol_condition", candidate["protocol_condition"]),
            "source_aggregation": candidate.get(
                "source_aggregation", candidate["aggregation"]),
            "condition": condition,
            "derived": DERIVED_VOTE_FROM_P3,
        })
    return derived


def _automatic_cumulative_boards(score_mod, items_by_id, candidates,
                                 active_models, draws=1000, seed=20260802):
    """Build exact common-prefix boards without conflating protocols or tasks."""
    grouped = collections.defaultdict(list)
    for candidate in candidates:
        candidate.setdefault("protocol_condition", None)
        candidate.setdefault("aggregation", None)
        candidate.setdefault("source_protocol", candidate["protocol"])
        candidate.setdefault(
            "source_protocol_condition", candidate["protocol_condition"])
        candidate.setdefault("source_aggregation", candidate["aggregation"])
        candidate.setdefault(
            "semantics", score_mod.protocol_semantics(
                candidate["source_protocol"], candidate["contract"],
                candidate["source_protocol_condition"]))
        candidate.setdefault("derived", None)
        if candidate["derived"] not in (None, DERIVED_VOTE_FROM_P3):
            sys.exit(f"automatic leaderboard {candidate['run_id']}: unknown derived "
                     f"marker {candidate['derived']!r}. Refusing.")
        if candidate["derived"] == DERIVED_VOTE_FROM_P3 and (
                candidate["protocol"] != "P2"
                or candidate["source_protocol"] != CURRENT_P3_PROTOCOL
                or candidate["semantics"] != score_mod.REPEATED_STATED):
            sys.exit(f"automatic leaderboard {candidate['run_id']}: a derived vote "
                     "candidate must present as P2 over repeated-stated P3 records "
                     f"(got protocol {candidate['protocol']!r}, source "
                     f"{candidate['source_protocol']!r}, semantics "
                     f"{candidate['semantics']!r}). Refusing.")
        if (candidate["semantics"] == score_mod.REPEATED_STATED
                and candidate["protocol"] != CURRENT_P3_PROTOCOL
                and candidate["derived"] != DERIVED_VOTE_FROM_P3):
            sys.exit(f"automatic leaderboard {candidate['run_id']}: repeated-stated "
                     f"records may only present as {CURRENT_P3_PROTOCOL} or as an "
                     "explicitly derived P2 vote view. Refusing.")
        condition_hash = canonical_sha256(candidate["condition"])[:10]
        # P3 is a separately declared K-call linear-pool system. Unlike P2's
        # shared-prefix comparison, P3@5 must never silently become P3@3 merely
        # because another model stopped earlier. A derived vote candidate
        # presents as P2 and follows P2's shared-repeat-prefix rule instead.
        exact_k = (candidate["k"]
                   if candidate["protocol"] == CURRENT_P3_PROTOCOL else None)
        key = (candidate["protocol"], candidate["protocol_condition"],
               candidate["aggregation"], exact_k,
               candidate["task"], condition_hash)
        grouped[key].append(candidate)

    boards = []
    for (protocol, protocol_condition, aggregation, exact_k,
         task, condition_hash), cohort in sorted(
            grouped.items(), key=lambda pair: tuple(str(value) for value in pair[0])):
        cohort.sort(key=lambda row: (row["model"] or "", row["run_id"]))
        common_n = min(row["horizon"] for row in cohort)
        common_sequence = cohort[0]["sequence"][:common_n]
        if any(row["sequence"][:common_n] != common_sequence for row in cohort[1:]):
            sys.exit(
                f"automatic leaderboard {protocol}/{task}: runs with the same ordering "
                "condition disagree on their rank-1..N item identities. Refusing.")
        if protocol == CURRENT_P3_PROTOCOL:
            common_k = int(exact_k)
        else:
            common_k = min(row["k"] for row in cohort) if protocol == "P2" else 1

        entries, prompt_sequences = [], collections.defaultdict(set)
        for candidate in cohort:
            selected = [
                rec for rec in candidate["records"]
                if int(rec["task_rank"]) <= common_n
                and (candidate["semantics"] not in score_mod.REPEATED_SEMANTICS
                     or int(rec["repeat_index"]) <= common_k)
            ]
            try:
                rows, dropped = score_mod.aggregate(
                    selected, items_by_id, candidate["source_protocol"],
                    expected_repeats=(common_k
                                      if candidate["semantics"] in score_mod.REPEATED_SEMANTICS
                                      else None),
                    run_contract=candidate["contract"],
                    # The candidate's own declared aggregation, not the board's:
                    # a derived vote entry still aggregates its records as the
                    # P3 linear pool before the vote view is read off it.
                    protocol_condition=candidate["source_protocol_condition"],
                    aggregation=candidate["source_aggregation"],
                )
            except ValueError as exc:
                sys.exit(
                    f"automatic leaderboard {protocol}/{task}/{candidate['run_id']}: "
                    f"{exc}. Refusing.")
            if dropped or {row["item_id"] for row in rows} != set(common_sequence):
                sys.exit(
                    f"automatic leaderboard {protocol}/{task}/{candidate['run_id']}: "
                    f"common prefix did not score exactly ({dropped[:1]}). Refusing.")
            if candidate["derived"] == DERIVED_VOTE_FROM_P3:
                # The vote view score.py already computed for these exact calls:
                # answer = modal verdict, p = modal frequency, correct re-marked
                # against the same label. Metrics and the case-blocked bootstrap
                # below then run on these rows through the identical path every
                # other entry takes. No pool value ever substitutes for a
                # missing vote — an incomplete vote block refuses instead.
                incomplete = [
                    row["item_id"] for row in rows
                    if not isinstance((row.get("p3") or {}).get("vote"), dict)
                    or not {"answer", "p", "correct", "modal_tie"}
                    <= set(row["p3"]["vote"])]
                if incomplete:
                    sys.exit(
                        f"automatic leaderboard {protocol}/{task}/{candidate['run_id']}: "
                        f"{len(incomplete)} scored row(s) lack a complete P3 vote "
                        f"block (first: {incomplete[0]}), so the derived P2 vote "
                        "view cannot be computed. Refusing.")
                rows = score_mod.p3_vote_rows(rows)

            prompt_by_item = collections.defaultdict(set)
            for rec in selected:
                prompt_by_item[rec["item_id"]].add(rec.get("prompt_sha256"))
            if any(len(prompt_by_item[item_id]) != 1 for item_id in common_sequence):
                sys.exit(
                    f"automatic leaderboard {protocol}/{task}/{candidate['run_id']}: "
                    "one item has multiple prompt identities. Refusing.")
            # Prompt identity is claimed per SOURCE protocol: native entries of
            # one board share byte-identical prompts, and every derived vote
            # entry shares the byte-identical P3 prompt, but a P3 prompt also
            # asks for a stated probability so it can never equal the verdict-
            # only P2 prompt. That difference is declared in the board note and
            # caveats rather than pretended away.
            prompt_sequences[
                (candidate["source_protocol"],
                 candidate["source_protocol_condition"])].add(tuple(
                    next(iter(prompt_by_item[item_id]))
                    for item_id in common_sequence))

            by_label = {}
            for label in sorted({row["label"] for row in rows}):
                subset = [row for row in rows if row["label"] == label]
                by_label[label] = {
                    "n": len(subset),
                    "accuracy": score_mod.accuracy(subset),
                    "mean_p": score_mod.mean_confidence(subset),
                }
            entries.append({
                "entry_id": candidate["run_id"],
                "model": candidate["model"],
                "runs": [candidate["run_id"]],
                "source_prefix_n": candidate["horizon"],
                "source_k": candidate["k"],
                "source_protocol": candidate["source_protocol"],
                "source_protocol_condition": candidate[
                    "source_protocol_condition"],
                # The derived marker lives on the entry: the board-level
                # condition stays shared, and this field is what the site and
                # any reader use to tell a derived vote entry from a native run.
                "protocol_condition": (DERIVED_VOTE_FROM_P3
                                       if candidate["derived"] == DERIVED_VOTE_FROM_P3
                                       else protocol_condition),
                "aggregation": aggregation,
                **score_mod.metric_set(rows),
                "mean_p": score_mod.mean_confidence(rows),
                "ci": score_mod.bootstrap(rows, draws, seed),
                "by_label": by_label,
                "reliability": score_mod.adaptive_bins(rows),
                "sp": score_mod.selective_prediction(rows),
            })

        for family, sequences in sorted(prompt_sequences.items(),
                                        key=lambda pair: str(pair[0])):
            if len(sequences) != 1:
                sys.exit(
                    f"automatic leaderboard {protocol}/{task}: entries sourced from "
                    f"{family[0]} received different prompts on the claimed common "
                    "prefix. Refusing.")
        entries.sort(key=lambda row: (
            row["brier"] is None, row["brier"] if row["brier"] is not None else 0,
            row["model"] or "", row["entry_id"]))
        n_derived = sum(1 for entry in entries
                        if entry["protocol_condition"] == DERIVED_VOTE_FROM_P3)
        if n_derived and n_derived == len(entries):
            sys.exit(
                f"automatic leaderboard {protocol}/{task}: every entry is a derived "
                "P3 vote view and no native P2 run shares the board condition. A "
                "derived-only P2 board would rest on a condition mismatch, not a "
                "comparison; fix the conditions or drop the derivation. Refusing.")

        cohort_models = {entry["model"] for entry in entries}
        caveats = []
        if common_n < 100:
            caveats.append(
                f"N={common_n} is an early cumulative prefix; estimates are unstable.")
        if len(cohort_models) < 2:
            caveats.append(
                "Only one model is active on this board, so no cross-model ranking exists yet.")
        if cohort_models != set(active_models):
            caveats.append(
                f"Comparable data cover {len(cohort_models)} of {len(set(active_models))} "
                "active models.")
        if any(entry["source_prefix_n"] > common_n for entry in entries):
            caveats.append("Longer runs are truncated to the exact shared item prefix.")
        if protocol == "P2" and any(entry["source_k"] > common_k for entry in entries):
            caveats.append(f"Repeated runs are compared at their shared repeat prefix K={common_k}.")
        if n_derived:
            caveats.append(
                f"{n_derived} of {len(entries)} entries are derived vote views of "
                "that model's active P3 run (modal verdict over the same K "
                "byte-identical calls; confidence = modal frequency), marked "
                f"{DERIVED_VOTE_FROM_P3}. Their P3 prompts also requested a stated "
                "probability; native P2 prompts are verdict-only.")

        condition = cohort[0]["condition"]
        if protocol == CURRENT_P3_PROTOCOL:
            method = f"repeated stated-confidence linear pool · K={common_k}"
            method_slug = f"p3-linear-pool-k{common_k}"
            note = ("Exact common cumulative task-rank prefix 1..N; every entry uses "
                    f"K={common_k} byte-identical stated-probability calls per item and "
                    "the same predeclared linear probability pool.")
        elif protocol == "P1":
            method = "one-shot stated probability"
            method_slug = "p1-one-shot"
            note = ("Exact common cumulative task-rank prefix 1..N; every entry uses "
                    "the same one-shot stated-probability prompt.")
        else:
            method = f"verdict-repeat agreement · K={common_k}"
            method_slug = "p2-agreement"
            if n_derived:
                note = ("Exact common cumulative task-rank prefix 1..N; native "
                        "entries use the same items and byte-identical verdict "
                        "prompt under P2, and entries marked 'vote from P3' apply "
                        "the same modal-verdict rule to their model's K "
                        "byte-identical P3 calls on the same items.")
            else:
                note = ("Exact common cumulative task-rank prefix 1..N; every entry uses "
                        "the same items and byte-identical verdict prompt under P2.")
        boards.append({
            "board_id": f"cumulative-{method_slug}-{task.lower()}-{condition_hash}",
            "title": f"{task} · {protocol} {method}",
            "primary": False,
            "origin": "automatic_cumulative",
            "tasks": [task],
            "task": task,
            "protocol": protocol,
            "protocol_condition": protocol_condition,
            "aggregation": aggregation,
            "k": common_k,
            "n_items": common_n,
            "question": (f"How do active model configurations perform on {task} under "
                         f"{method}?"),
            "note": note,
            "condition": condition,
            "comparison": {
                # Multiple archived executions of one model configuration are
                # still a single-model board, not a model ranking.
                "rankable": len(cohort_models) > 1,
                "cross_model": len(cohort_models) > 1,
                "n_entries": len(entries),
                "n_derived_entries": n_derived,
                "n_models": len(cohort_models),
                "n_active_models": len(set(active_models)),
                "common_prefix_n": common_n,
                "complete_across_active_models": cohort_models == set(active_models),
                "caveats": caveats,
            },
            "accounting": {
                "n_expected": common_n, "n_scored": common_n,
                "n_mapped": 0, "n_absent": 0, "absent": [],
            },
            "entries": entries,
        })
    return boards


def automatic_cumulative_leaderboard(score_mod, items_by_id, active_runs,
                                     current_items_path, draws=1000, seed=20260802):
    candidates, excluded = _automatic_board_candidates(
        score_mod, active_runs, current_items_path)
    models = [run.get("model") for run in active_runs if run.get("model")]
    return {
        "boards": _automatic_cumulative_boards(
            score_mod, items_by_id, candidates, models, draws, seed),
        "excluded": excluded,
    }


# --- leaderboard -------------------------------------------------------------
# A board is a SAME-ITEMS comparison: every entry answered the identical item
# set, so ranking within a board is meaningful and ranking across boards is
# not. Board membership is declared in active_results.json; archived runs are
# inert until explicitly activated there.


def leaderboard(score_mod, items_by_id, migrations, exclusions, board_specs,
                excluded_specs, draws=1000, seed=20260802):
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
    for spec in board_specs:
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
                "sp": score_mod.selective_prediction(rows),
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
            "tasks": spec.get("tasks", []),
            "question": spec["question"],
            "note": spec["note"],
            "n_items": len(id_sets[0]) if id_sets else 0,
            "protocol": "P2",
            # What the archived runs answered vs what is scoreable today. Only
            # `n_absent` > 0 is a caveat the site prints; the rest is the receipt.
            "accounting": accountings[0],
            "entries": entries,
        })
    return {"boards": boards, "excluded": excluded_specs,
            "policy": ("rows on one board answered the identical item set under the same "
                       "protocol; ranked by Brier (proper score: right AND knowing how "
                       "right), CIs source-report sibling-group-blocked bootstrap "
                       "with primary-case fallback")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=0,
                    help="items to export in full (0, the default, = the whole bank — "
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
    active_results = load_active_results()
    active_run_ids = active_results["active_run_ids"]
    print(f"active results registry: {len(active_run_ids)} run(s)")

    items = list(read_jsonl(args.items))
    print(f"read {len(items):,} items from {args.items}")
    # --n 0 means the whole bank. Full export bypasses subset selection; the
    # published browser order is applied explicitly below.
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

    published = stratified_sample(items, args.n, args.seed)
    print(f"selected {len(published)} published items over "
          f"{len({(i['task'], i['label'], era_of(i)) for i in published})} strata")
    # Emit in case order so the browser's default view groups siblings — the
    # same case under T1 and T2 lands adjacent, which is the comparison
    # the design is built around. (Selection order is diagnostic-class-first,
    # which would otherwise put every appeal flip at the top of the table.)
    published.sort(key=lambda it: (it["case_number"], it["task"], it["item_id"]))

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
        {it["item_id"] for it in items}, migrations, by_id, score_mod, active_run_ids,
        sha256_of(args.items), args.items)
    stale = stale_inputs_summary(groups_by_item)
    if stale["n_answers_stale"]:
        sys.exit(
            f"active results include {stale['n_answers_stale']} answer(s) elicited "
            "against prompts that no longer match the active bank; rerun them before activation")
    confident_wrong = sorted(
        iid for iid, outs in outputs_by_item.items()
        if any(o["answer"] != by_id[iid]["label"]
               and (o["probability"] or 0) >= 0.8 for o in outs))
    published_ids = {it["item_id"] for it in published}
    floor_added = [by_id[i] for i in confident_wrong if i not in published_ids]
    published.extend(floor_added)
    published.sort(key=lambda it: (it["case_number"], it["task"], it["item_id"]))
    print(f"model outputs: {sum(len(v) for v in outputs_by_item.values())} active answers; "
          f"added {len(floor_added)} of {len(confident_wrong)} active confidently-wrong "
          f"items outside the requested selection")
    print(f"  active ids: {answers_accounting['n_ids_referenced']} referenced, "
          f"{answers_accounting['n_ids_mapped']} mapped through id_migrations.jsonl, "
          f"{answers_accounting['n_ids_unresolved']} unresolved "
          f"({answers_accounting['n_answers_unresolved']} answers not in the active bank)")
    print(f"  input-stale: {stale['n_answers_stale']} of {stale['n_answers']} active "
          f"answers were elicited against inputs that have since moved "
          f"({stale['n_groups_stale']}/{stale['n_groups']} (run, item) groups, "
          f"{stale['n_items_affected']} items) — clears by rerunning, not by editing")
    for cause, n in stale["by_cause"].items():
        print(f"    {n:5d} answers  {cause}")

    index = []
    for it in published:
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
                # index into model_outputs, so the drawer folds a P2 sweep into
                # its measurement without a second copy of the calls.
                "model_groups": groups_by_item.get(it["item_id"], []),
            },
        }
        (items_dir / f"{it['item_id']}.json").write_text(
            json.dumps(full, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    (lib_out / "items_index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    runs = collect_runs(active_run_ids, args.items)
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
    declared_board_data = leaderboard(
        score_mod, items_by_id, migrations, exclusion_reasons(),
        active_results["leaderboard_boards"], active_results["leaderboard_excluded"])
    automatic_board_data = automatic_cumulative_leaderboard(
        score_mod, items_by_id, runs, args.items)
    board_data = {
        "boards": automatic_board_data["boards"] + declared_board_data["boards"],
        "excluded": (automatic_board_data["excluded"]
                     + declared_board_data["excluded"]),
        "policy": (
            "automatic boards isolate one active task and exact method condition and "
            "recompute every entry on the shared cumulative item prefix; P1 stated "
            "confidence, P2 verdict-repeat agreement and P3 repeated stated-confidence "
            "linear pools never mix (P2 uses "
            "its shared repeat prefix); a model with no native P2 run may appear on a "
            "P2 board as the verdict-vote view of its active P3 run, always marked "
            "derived_vote_from_p3 and never replacing or merging with a native entry; "
            "declared boards retain their explicit same-items "
            "policy; SP risk–coverage is computed offline from each exact board; no rank "
            "is implied by a one-entry board"),
    }
    (lib_out / "leaderboard.json").write_text(
        json.dumps(board_data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for b in board_data["boards"]:
        a = b["accounting"]
        print(f"exported leaderboard: {b['board_id']} ({len(b['entries'])} entries, "
              f"{b['n_items']} items) — {a['n_scored']} scored of {a['n_expected']} "
              f"archived, {a['n_mapped']} id(s) mapped, {a['n_absent']} absent"
              + (": " + ", ".join(f"{r['item_id']} ({r['reason']})" for r in a["absent"])
                 if a["absent"] else ""))

    # P4 rides its own registry block and scorer; the write is byte-
    # deterministic (sort_keys, stable model order, no timestamps) so a
    # double export must compare equal.
    p4_payload = p4_site_data(active_results["p4"], args.items)
    (lib_out / "p4.json").write_text(
        json.dumps(p4_payload, ensure_ascii=False, separators=(",", ":"),
                   sort_keys=True), encoding="utf-8")
    print(f"exported P4 deferral surface: {len(p4_payload['models'])} model(s), "
          f"{sum(1 for row in p4_payload['models'] if row['class'] != 'payoff_blind')} "
          f"pass qualification, "
          f"{sum(1 for row in p4_payload['models'] if row['core'])} core curve(s)")

    meta = {
        "generated_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
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
        "published": {
            "n": len(published),
            "is_full_bank": len(published) == len(items),
            "tasks": dict(sorted(collections.Counter(i["task"] for i in published).items())),
            "labels": dict(sorted(collections.Counter(f"{i['task']}:{i['label']}" for i in published).items())),
            "eras": {str(k): v for k, v in sorted(collections.Counter(era_of(i) for i in published).items(),
                                                  key=lambda kv: (kv[0] is None, kv[0]))},
            "policy": ("The full active item bank, ordered by case, task and item id for browsing; "
                       "evaluation prefixes are fixed independently by bench/run.py"
                       if len(published) == len(items) else
                       f"An explicitly requested deterministic review subset (seed {args.seed}), "
                       "stratified by task, label and Code year"),
        },
        "tasks_described": {
            "T1": {"question": "Did the Panel rule a breach of this clause?",
                   "answers": ["breach", "no_breach"],
                   "shown": "complaint + respondent's written response",
                   "n": tasks.get("T1", 0)},
            "T2": {"question": "Did the Panel rule a breach of this clause?",
                   "answers": ["breach", "no_breach"],
                   "shown": "complaint only, with the defence hidden",
                   "n": tasks.get("T2", 0)},
            "T3": {"question": "Did the Appeal Board uphold or overturn the Panel's ruling?",
                   "answers": ["upheld", "overturned"],
                   "shown": "complaint + response + the Panel's ruling for the clause",
                   "n": tasks.get("T3", 0)},
        },
        "protocols": {
            "P1": "one-shot stated confidence: one answer-and-probability call per item",
            "P2": "verdict-repeat agreement: K byte-identical verdict-only calls; confidence is the modal-answer frequency",
            "P3": "repeated stated-confidence linear pool: K byte-identical answer-and-probability calls; equally weighted oriented probabilities are averaged",
            "SP": "offline selective prediction: threshold, risk–coverage and AURC analysis over any completed P1/P2/P3 confidence view; no new model calls (formerly presented as P4)",
        },
        "runs": {
            "n_scored": len(runs),
            "active_run_ids": active_run_ids,
            "headline": headline_runs(runs),
        },
        # What the activated archive runs hold vs what could be attached to an
        # item page. Historical inactive runs remain untouched under bench/runs/.
        "active_answers": answers_accounting,
        # How much of the active result set is answering a prompt the bank no longer
        # serves. Durable and countable on purpose: the number is the backlog
        # for the T3/pre-l2.4 reruns, and its endgame is zero.
        "stale_inputs": stale,
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
