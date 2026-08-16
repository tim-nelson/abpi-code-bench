"""Score a run: calibration, discrimination and selective prediction.

    python3 bench/score.py --run bench/runs/20260802T101500Z
    python3 bench/score.py --run <dir> --items bench/items.jsonl --draws 1000
    python3 bench/score.py --run <dir> --through-items 200 --through-repeats 3
    python3 bench/score.py --self-test        # estimator/completeness tests, no run needed

For a cumulative run, reads <run>/requests.jsonl as the planned-call ledger and
joins its in-horizon calls to responses.jsonl and ledger.jsonl. This means an
unfinished run can be scored without pretending that absent calls never
existed: planned, receipted, parsed, failed and pending calls are all reported.
``--through-items N`` takes task ranks 1..N in every task and
``--through-repeats K`` takes P2/P3 repeats 1..K, so a topped-up run can be viewed
at an earlier common prefix. Archived runs without ranked requests retain the
legacy responses-only path.

Confidence is scored in the confidence-of-correctness frame, so P1, P2 and P3
land on the same axis and SP evaluates deployment from any signal:

    p = the model's confidence in the answer it gave
        P1 -> the stated probability
        P2 -> the frequency of the modal answer across K byte-identical calls
        P3 -> a linear pool of K byte-identical stated-probability calls
    o = 1 if that answer matches the adjudicated label, else 0

    Brier = mean((p - o)^2)          lower is better
    ECE   = sum_b (n_b/N) |acc_b - conf_b|   over 10 equal-mass bins
    AUROC = P(p higher on a correct answer than on a wrong one), ties at 0.5

SP — offline selective prediction (risk–coverage over any protocol's
confidence signal): coverage, selective risk and AURC. It makes no provider
call. SP was formerly presented as P4; the P4 label now names the
incentivized-deferral call protocol (bench/P4_SPEC.md, scored by
bench/p4_score.py; P4 runs enter the registry only after qualification).

Archived response-only and ``pmcpa.zero-provider.v1`` files keep their original
names: legacy P2 is stated confidence, legacy P1 is repeated verdicts, and
legacy P3 is the retired lottery/revealed-preference experiment. The scorer
maps those semantics explicitly and never treats legacy P3 as active P3.

Equal-mass ("adaptive") binning matters for P2, where p can only take K+1
values: fixed-width bins would leave most of them empty. Bin edges are extended
so that items sharing a p never straddle two bins.

P2 and P3 are deliberately all-or-nothing per item. A scored item must have one, and
only one, parsed receipt at every repeat index 1..K and the K planned requests
must be identical. Missing and duplicate groups are listed in ``dropped``;
effective K is never allowed to drift from item to item.

Confidence intervals are SIBLING-GROUP-BLOCKED: the bootstrap resamples the
``sibling_group:<root>`` source-report clusters recorded on items, not items.
Legacy/synthetic rows without that tag fall back to case number. Clause-level
and source-report sibling items share narrative text and are strongly
correlated, so item-level resampling would understate the interval.
"""

import argparse
import hashlib
import json
import math
import pathlib
import random
import sys

try:
    # Script execution puts bench/ itself first on sys.path.
    from run import (RUN_CONTRACT as ACTIVE_RUN_CONTRACT,
                     complaint_pair_key as runner_complaint_pair_key)
except ModuleNotFoundError:  # package-style import (for test runners)
    from bench.run import (RUN_CONTRACT as ACTIVE_RUN_CONTRACT,
                           complaint_pair_key as runner_complaint_pair_key)

BENCH = pathlib.Path(__file__).resolve().parent
DEFAULT_ITEMS = BENCH / "items.jsonl"
ACTIVE_TASKS = ("T1", "T2", "T3")
# Old run directories retain the task ids they were actually served. Keeping
# these two names readable costs nothing and does not put them back into the
# active benchmark or leaderboard.
LEGACY_TASKS = ("T1-triage", "T4")
TASKS = ACTIVE_TASKS + LEGACY_TASKS
SCORING_INPUTS_SCHEMA = "pmcpa.score-inputs.v1"
LEGACY_RUN_CONTRACT = "pmcpa.zero-provider.v1"
STATED = "stated_probability"
RESAMPLING = "identical_prompt_resampling"
REPEATED_STATED = "repeated_stated_probability"
P3_AGGREGATION = "linear_probability_pool"
# Compatibility name for the frozen 2026-08-14 planner/source run. New active
# artifacts use P3; the old planner file and paid rows must remain byte-stable.
P1R_AGGREGATION = P3_AGGREGATION
OFFLINE_SELECTIVE = "offline_selective_prediction"
# Claimed 2026-08-16 by the incentivized-deferral protocol (bench/P4_SPEC.md):
# per-call payoff instruction, answer/refer decision, cost-level sweep.
# Scoring lives in bench/p4_score.py; P4 runs stay OUT of the active-results
# registry until a model passes the payoff-sensitivity qualification test
# (P4_SPEC.md 8b) and its arm is explicitly promoted.
INCENTIVIZED_DEFERRAL = "incentivized_deferral"
P4_AGGREGATION = "cost_sweep"
PROTOCOL_RESOLUTION_SCHEMA = "pmcpa.protocol-resolution.v1"
LEGACY_P1R_MAPPING_ID = "pmcpa.protocol-alias.p1-repeated-to-p3.v1"
P3_CONFIG_EQUIVALENCE_FIELDS = (
    "contract", "runner_sha256", "model", "max_tokens", "thinking",
    "rationale", "effort", "temperature", "seed",
)
LEGACY_REVEALED_PREFERENCE = "legacy_revealed_preference"
REPEATED_SEMANTICS = frozenset({RESAMPLING, REPEATED_STATED})
P3_LABELS = {
    "T1": ("breach", "no_breach"),
    "T2": ("breach", "no_breach"),
    # The positive direction is deliberately the minority/escalation outcome,
    # even though the runner's display order is upheld/overturned.
    "T3": ("overturned", "upheld"),
}
P1R_LABELS = P3_LABELS
DIAGNOSTIC_TAGS = (
    "appeal_flip",
    "appeal_survived",
    "burden_of_proof_candidate",
    "voluntary_admission",
)

# How far past the ends of the sure-amount grid a censored item is placed. The
# data cannot locate an indifference point outside the grid, only bound it, so
# the cap is deliberately close to the edge: on the default grid
# (0.55 .. 0.95) this gives 0.53 and 0.97, matching the archived legacy-P3
# estimator.
CAP_MARGIN = 0.02


def resolve_protocol(protocol, run_contract, protocol_condition=None):
    """Resolve immutable storage identifiers into the current active namespace."""
    if run_contract == ACTIVE_RUN_CONTRACT:
        if protocol == "P1" and protocol_condition == REPEATED_STATED:
            return {
                "active_protocol": "P3",
                "semantics": REPEATED_STATED,
                "mapping_id": LEGACY_P1R_MAPPING_ID,
                "source_protocol": "P1",
                "source_protocol_condition": REPEATED_STATED,
            }
        if protocol_condition is not None:
            raise ValueError(
                f"unknown active {protocol} condition {protocol_condition!r}")
        mapping = {
            "P1": STATED,
            "P2": RESAMPLING,
            "P3": REPEATED_STATED,
            # SP (formerly P4) is the offline selective-prediction analysis.
            "SP": OFFLINE_SELECTIVE,
            # P4 was reserved from the 2026-08-16 SP rename until the
            # incentivized-deferral protocol was specified; claimed the same
            # day by bench/P4_SPEC.md + bench/p4_plan.py.
            "P4": INCENTIVIZED_DEFERRAL,
        }
        try:
            semantics = mapping[protocol]
        except KeyError as exc:
            raise ValueError(f"unknown active protocol {protocol!r}") from exc
        return {
            "active_protocol": protocol,
            "semantics": semantics,
            "mapping_id": None,
            "source_protocol": protocol,
            "source_protocol_condition": None,
        }
    if run_contract in (None, LEGACY_RUN_CONTRACT):
        mapping = {
            "P1": RESAMPLING,
            "P2": STATED,
            "P3": LEGACY_REVEALED_PREFERENCE,
        }
        try:
            semantics = mapping[protocol]
        except KeyError as exc:
            raise ValueError(f"unknown legacy protocol {protocol!r}") from exc
        return {
            "active_protocol": protocol,
            "semantics": semantics,
            "mapping_id": None,
            "source_protocol": protocol,
            "source_protocol_condition": protocol_condition,
        }
    raise ValueError(f"unknown run contract {run_contract!r}")


def protocol_semantics(protocol, run_contract, protocol_condition=None):
    """Resolve a protocol id without conflating the active and legacy spaces.

    The identifier order changed before any run was activated. Archived files
    are immutable, so their absent/v1 contract is the namespace discriminator.
    """
    return resolve_protocol(protocol, run_contract, protocol_condition)["semantics"]


def load_jsonl(path):
    out = []
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_optional_jsonl(path):
    """Read a JSONL file when present; an absent ledger means no receipts."""
    path = pathlib.Path(path)
    return load_jsonl(path) if path.exists() else []


def file_binding(path):
    """Deterministic raw-byte binding; absence is an explicit input state."""
    path = pathlib.Path(path)
    if not path.exists():
        return {"present": False, "sha256": None, "bytes": None,
                "basename": path.name}
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"present": True, "sha256": digest.hexdigest(), "bytes": size,
            "basename": path.name}


def scoring_input_bindings(items_path, manifest_path, requests_path,
                           responses_path, ledger_path):
    """Bindings for every file whose bytes the scorer consulted."""
    return {
        "schema_version": SCORING_INPUTS_SCHEMA,
        "items": file_binding(items_path),
        "manifest": file_binding(manifest_path),
        "requests": file_binding(requests_path),
        "responses": file_binding(responses_path),
        "ledger": file_binding(ledger_path),
    }


def canonical_sha256(value):
    """SHA-256 of one semantic JSON value, independent of whitespace/order."""
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def request_config_binding(manifest, requests):
    """Bind the exact manifest config and the config identities on requests.

    Raw manifest/request file hashes remain the primary byte-level provenance.
    This semantic binding makes the request configuration directly inspectable
    and lets the exporter verify that the score was not detached from it.
    """
    payload = {
        "manifest_config": manifest.get("config"),
        "manifest_config_hash": manifest.get("config_hash"),
        "request_config_hashes": sorted({
            row["config_hash"] for row in requests
            if isinstance(row.get("config_hash"), str)
        }),
    }
    return {"sha256": canonical_sha256(payload), **payload}


def _raw_jsonl_index(path):
    """Call-id index retaining each exact raw JSONL row hash."""
    out = {}
    with pathlib.Path(path).open("rb") as fh:
        for raw in fh:
            row = json.loads(raw)
            call_id = row.get("call_id")
            if not call_id or call_id in out:
                raise ValueError(f"{path}: missing/duplicate call_id {call_id!r}")
            out[call_id] = (row, hashlib.sha256(raw).hexdigest())
    return out


def verify_receipt_aliases(records, run_dir, manifest):
    """Verify native-P3 receipts aliased to the frozen paid P1R source rows."""
    aliases = [record["receipt_alias"] for record in records
               if isinstance(record.get("receipt_alias"), dict)]
    migrations = manifest.get("receipt_migrations") or []
    if not aliases:
        if migrations:
            raise ValueError("manifest records a receipt migration but this horizon has no aliases")
        return {
            "schema_version": "pmcpa.receipt-alias.v1",
            "n_alias_calls": 0, "n_native_calls": sum(
                isinstance(record.get("parsed"), dict) for record in records),
            "source_runs": [], "registry": None,
        }
    if len(migrations) != 1:
        raise ValueError("receipt aliases require exactly one manifest migration record")
    migration = migrations[0]
    if (migration.get("schema_version") != "pmcpa.receipt-alias.v1"
            or migration.get("provider_bodies_copied") is not False):
        raise ValueError("invalid receipt migration manifest contract")
    registry_name = migration.get("alias_registry")
    if not isinstance(registry_name, str) or pathlib.Path(registry_name).name != registry_name:
        raise ValueError("receipt migration registry must be a local basename")
    registry_path = pathlib.Path(run_dir) / registry_name
    if file_binding(registry_path)["sha256"] != migration.get("alias_registry_sha256"):
        raise ValueError("receipt alias registry hash disagrees with manifest")
    registry_rows = load_jsonl(registry_path)
    registry = {row.get("target_call_id"): row for row in registry_rows}
    if len(registry) != len(registry_rows) or None in registry:
        raise ValueError("receipt alias registry has duplicate/missing target_call_id")

    source_cache = {}
    source_counts = {}
    for record in records:
        alias = record.get("receipt_alias")
        if not isinstance(alias, dict):
            continue
        if (alias.get("schema_version") != "pmcpa.receipt-alias.v1"
                or alias.get("migration_id") != migration.get("migration_id")
                or alias.get("target_run_id") != pathlib.Path(run_dir).name
                or alias.get("target_call_id") != record.get("call_id")
                or registry.get(record.get("call_id")) != alias):
            raise ValueError(f"{record.get('call_id')}: receipt alias/registry mismatch")
        source_run_id = alias.get("source_run_id")
        if (not isinstance(source_run_id, str)
                or pathlib.Path(source_run_id).name != source_run_id
                or source_run_id != migration.get("source_run_id")):
            raise ValueError("receipt alias source_run_id must be a run basename")
        if tuple(alias.get("config_equivalence_fields") or ()) != P3_CONFIG_EQUIVALENCE_FIELDS:
            raise ValueError("receipt alias config-equivalence field contract changed")
        source_run = BENCH / "runs" / source_run_id
        if source_run_id not in source_cache:
            source_manifest = json.loads(
                (source_run / "manifest.json").read_text(encoding="utf-8"))
            if (source_manifest.get("protocol"),
                    source_manifest.get("protocol_condition"),
                    source_manifest.get("aggregation")) != (
                    "P1", REPEATED_STATED, P3_AGGREGATION):
                raise ValueError(f"{source_run_id}: alias source is not frozen P1R")
            mismatches = [
                key for key in P3_CONFIG_EQUIVALENCE_FIELDS
                if (manifest.get("config") or {}).get(key)
                != (source_manifest.get("config") or {}).get(key)
            ]
            if mismatches:
                raise ValueError(
                    f"{source_run_id}: source/target config differs: "
                    + ", ".join(mismatches))
            recorded_files = migration.get("source_files") or {}
            for name in ("manifest.json", "requests.jsonl", "responses.jsonl",
                         "ledger.jsonl"):
                if file_binding(source_run / name)["sha256"] != recorded_files.get(name):
                    raise ValueError(f"{source_run_id}: frozen source {name} hash changed")
            source_cache[source_run_id] = {
                "manifest": source_manifest,
                "requests": _raw_jsonl_index(source_run / "requests.jsonl"),
                "responses": _raw_jsonl_index(source_run / "responses.jsonl"),
                "ledger": _raw_jsonl_index(source_run / "ledger.jsonl"),
            }
            source_counts[source_run_id] = 0
        source = source_cache[source_run_id]
        source_call_id = alias.get("source_call_id")
        request_pair = source["requests"].get(source_call_id)
        response_pair = source["responses"].get(source_call_id)
        ledger_pair = source["ledger"].get(source_call_id)
        if not request_pair or not response_pair or not ledger_pair:
            raise ValueError(f"{record.get('call_id')}: source call binding is absent")
        source_request, request_hash = request_pair
        source_response, response_hash = response_pair
        source_event, ledger_hash = ledger_pair
        expected_hashes = (
            alias.get("source_request_row_sha256"),
            alias.get("source_response_row_sha256"),
            alias.get("source_ledger_row_sha256"),
        )
        if (request_hash, response_hash, ledger_hash) != expected_hashes:
            raise ValueError(f"{record.get('call_id')}: frozen source row hash mismatch")
        if (source_event.get("status") != "completed"
                or source_response.get("parsed") != record.get("parsed")
                or canonical_sha256(record.get("parsed")) != alias.get("parsed_sha256")
                or record.get("request_sha256") != source_request.get("request_sha256")
                or record.get("prompt_sha256") != source_request.get("prompt_sha256")
                or canonical_sha256(record.get("request"))
                != canonical_sha256(source_request.get("request"))):
            raise ValueError(f"{record.get('call_id')}: aliased provider result is not equivalent")
        source_counts[source_run_id] += 1

    return {
        "schema_version": "pmcpa.receipt-alias.v1",
        "migration_id": migration.get("migration_id"),
        "n_alias_calls": len(aliases),
        "n_native_calls": sum(isinstance(record.get("parsed"), dict)
                              and not record.get("receipt_alias") for record in records),
        "source_runs": [{
            "run_id": run_id,
            "source_protocol": "P1",
            "source_protocol_condition": REPEATED_STATED,
            "source_aggregation": P3_AGGREGATION,
            "n_calls": source_counts[run_id],
            "source_files": migration.get("source_files"),
        } for run_id in sorted(source_counts)],
        "registry": {
            "basename": registry_name,
            "sha256": migration.get("alias_registry_sha256"),
            "n_rows": len(registry_rows),
        },
    }


def _rank(record):
    """Return a new-run record's 1-based task rank, or None."""
    for key in ("task_rank", "item_rank"):
        value = record.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value >= 1:
            return value
    return None


def _repeat(record):
    value = record.get("repeat_index")
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def is_cumulative_catalog(requests):
    """Whether requests carry the durable rank/repeat/call identity contract."""
    return bool(requests) and all(
        row.get("call_id") and row.get("item_id") and _rank(row) is not None
        and _repeat(row) is not None
        for row in requests
    )


def validate_item_horizon(requests, through_items):
    """Require N not to exceed any catalogued task's planned rank horizon.

    Ranks can legitimately be sparse when a split filter is applied after the
    outcome-blind whole-bank ordering, so only the recorded maximum is tested.
    """
    if not through_items:
        return
    ranks_by_task = {}
    for row in requests:
        ranks_by_task.setdefault(str(row.get("task") or "unknown"), set()).add(_rank(row))
    defects = []
    for task, ranks in sorted(ranks_by_task.items()):
        available = {rank for rank in ranks if rank is not None}
        horizon = max(available, default=0)
        if horizon < through_items:
            defects.append(f"{task} horizon={horizon}")
    if defects:
        raise ValueError(
            f"--through-items {through_items} exceeds the planned task "
            "horizon: " + "; ".join(defects))


def cumulative_records(requests, responses, ledger, through_items,
                       through_repeats, semantics):
    """Join a ranked request plan to its receipts without hiding missing calls.

    ``responses.jsonl`` is authoritative for parsed output. A parsed completed
    ledger event is used only as a crash-recovery fallback when no parsed
    response row exists; the normal response + ledger mirror is therefore one
    receipt, not a false duplicate. Multiple parsed response rows for one call
    remain visible via ``_parsed_receipt_count`` and make its repeated-protocol
    item invalid.
    """
    catalog = {}
    duplicate_request_ids = []
    for request in requests:
        call_id = request["call_id"]
        if call_id in catalog:
            duplicate_request_ids.append(call_id)
        else:
            catalog[call_id] = request
    if duplicate_request_ids:
        sample = ", ".join(sorted(set(duplicate_request_ids))[:5])
        raise ValueError(f"requests.jsonl has duplicate call_id rows: {sample}")

    planned = []
    for request in requests:
        if through_items and _rank(request) > through_items:
            continue
        if semantics in REPEATED_SEMANTICS and _repeat(request) > through_repeats:
            continue
        planned.append(request)
    planned_ids = {row["call_id"] for row in planned}

    response_by_call = {}
    ledger_by_call = {}
    unlinked_response_rows = unlinked_ledger_rows = 0
    for row in responses:
        call_id = row.get("call_id")
        if call_id:
            response_by_call.setdefault(call_id, []).append(row)
        else:
            unlinked_response_rows += 1
    for row in ledger:
        call_id = row.get("call_id")
        if call_id:
            ledger_by_call.setdefault(call_id, []).append(row)
        else:
            unlinked_ledger_rows += 1

    materialized = []
    calls_receipted = calls_parsed = calls_errors = calls_pending = 0
    parsed_receipts = duplicate_parsed_calls = 0
    failed_receipts = 0
    for request in planned:
        call_id = request["call_id"]
        response_rows = response_by_call.get(call_id, [])
        ledger_rows = ledger_by_call.get(call_id, [])
        response_parsed = [row for row in response_rows
                           if isinstance(row.get("parsed"), dict)]
        # A ledger completion mirrors the normal response row. Consult it only
        # if the scorer-compatible response receipt is absent.
        ledger_parsed = [row for row in ledger_rows
                         if row.get("status") == "completed"
                         and isinstance(row.get("parsed"), dict)]
        parsed_rows = response_parsed if response_parsed else ledger_parsed
        n_parsed_receipts = len(parsed_rows)
        n_raw_receipts = len(response_rows) + len(ledger_rows)
        has_receipt = n_raw_receipts > 0
        failed_receipts += sum(
            1 for row in ledger_rows if row.get("status") == "failed")

        if has_receipt:
            calls_receipted += 1
        else:
            calls_pending += 1
        if n_parsed_receipts:
            calls_parsed += 1
            parsed_receipts += n_parsed_receipts
            duplicate_parsed_calls += n_parsed_receipts > 1
            error = None
        elif has_receipt:
            calls_errors += 1
            messages = [str(row.get("error")) for row in response_rows + ledger_rows
                        if row.get("error")]
            error = "; ".join(dict.fromkeys(messages))[:200] or "unparsed receipt"
        else:
            error = "pending: no receipt"

        materialized.append({
            **request,
            "task_rank": _rank(request),
            "item_rank": _rank(request),
            "repeat_index": _repeat(request),
            "parsed": parsed_rows[0]["parsed"] if parsed_rows else None,
            "receipt_alias": (parsed_rows[0].get("receipt_alias")
                              if parsed_rows else None),
            "error": error,
            "_parsed_receipt_count": n_parsed_receipts,
            "_raw_receipt_count": n_raw_receipts,
            "_receipt_status": ("parsed" if n_parsed_receipts else
                                "error" if has_receipt else "pending"),
        })

    all_receipt_ids = set(response_by_call) | set(ledger_by_call)
    planned_by_task = {}
    for rec in materialized:
        block = planned_by_task.setdefault(rec.get("task", "unknown"), {
            "items": set(), "calls_planned": 0, "calls_receipted": 0,
            "calls_parsed": 0, "calls_errors": 0, "calls_pending": 0,
            "max_task_rank": 0,
        })
        block["items"].add(rec["item_id"])
        block["calls_planned"] += 1
        block["calls_receipted"] += rec["_raw_receipt_count"] > 0
        block["calls_parsed"] += rec["_parsed_receipt_count"] > 0
        block["calls_errors"] += rec["_receipt_status"] == "error"
        block["calls_pending"] += rec["_receipt_status"] == "pending"
        block["max_task_rank"] = max(block["max_task_rank"], rec["task_rank"])
    planned_by_task = {
        task: {**block, "items": len(block["items"])}
        for task, block in sorted(planned_by_task.items())
    }
    coverage = {
        "mode": "planned_ledger",
        "calls_catalogued": len(requests),
        "calls_outside_horizon": len(requests) - len(planned),
        "calls_planned": len(planned),
        "calls_receipted": calls_receipted,
        "calls_parsed": calls_parsed,
        "calls_completed": calls_parsed,
        "calls_errors": calls_errors,
        "calls_failed": calls_errors,
        "calls_pending": calls_pending,
        # Short aliases make the promised accounting explicit in scores.json.
        "planned": len(planned),
        "receipted": calls_receipted,
        "parsed": calls_parsed,
        "completed": calls_parsed,
        "errors": calls_errors,
        "failed": calls_errors,
        "pending": calls_pending,
        "parsed_receipts": parsed_receipts,
        "calls_with_duplicate_parsed_receipts": duplicate_parsed_calls,
        "failed_receipts": failed_receipts,
        "orphan_receipt_call_ids": len(all_receipt_ids - set(catalog)),
        "receipt_calls_outside_horizon": len((all_receipt_ids & set(catalog)) - planned_ids),
        "unlinked_response_rows": unlinked_response_rows,
        "unlinked_ledger_rows": unlinked_ledger_rows,
        "planned_by_task": planned_by_task,
    }
    return materialized, coverage


# --- metrics ---------------------------------------------------------------

def accuracy(rows):
    return sum(r["correct"] for r in rows) / len(rows) if rows else None


def brier(rows):
    return sum((r["p"] - r["correct"]) ** 2 for r in rows) / len(rows) if rows else None


def mean_confidence(rows):
    return sum(r["p"] for r in rows) / len(rows) if rows else None


def adaptive_bins(rows, nbins=10):
    """Equal-mass bins; never split a group of equal p across two bins."""
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: (r["p"], r["item_id"]))
    n = len(ordered)
    bins = []
    start = 0
    for i in range(nbins):
        if start >= n:
            break
        target = round((i + 1) * n / nbins)
        end = max(target, start + 1)
        end = min(end, n)
        while end < n and ordered[end]["p"] == ordered[end - 1]["p"]:
            end += 1
        chunk = ordered[start:end]
        conf = sum(r["p"] for r in chunk) / len(chunk)
        acc = sum(r["correct"] for r in chunk) / len(chunk)
        bins.append({
            "bin": len(bins),
            "n": len(chunk),
            "p_min": chunk[0]["p"],
            "p_max": chunk[-1]["p"],
            "confidence": conf,
            "accuracy": acc,
            "gap": acc - conf,
        })
        start = end
    return bins


def ece(rows, nbins=10):
    if not rows:
        return None
    n = len(rows)
    return sum(b["n"] / n * abs(b["gap"]) for b in adaptive_bins(rows, nbins))


def auroc(rows):
    """Discrimination: does p rank correct answers above wrong ones?

    Mann-Whitney U with mid-ranks, so the K+1 distinct values P2 can produce
    (and the handful P3 can) are handled as ties rather than broken arbitrarily.
    None when one class is empty -- AUROC is undefined, not 0.5.
    """
    pos = [r["p"] for r in rows if r["correct"] == 1]
    neg = [r["p"] for r in rows if r["correct"] == 0]
    if not pos or not neg:
        return None
    ordered = sorted(pos + neg)
    ranks, i = {}, 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[i]:
            j += 1
        ranks[ordered[i]] = (i + j) / 2.0 + 1.0
        i = j + 1
    rank_sum = sum(ranks[v] for v in pos)
    return (rank_sum - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def metric_set(rows):
    return {"n": len(rows), "accuracy": accuracy(rows), "brier": brier(rows),
            "mean_confidence": mean_confidence(rows),
            "ece": ece(rows), "auroc": auroc(rows)}


def case_weighted_metrics(rows):
    """Sensitivity where each primary PMCPA case contributes equal weight.

    This intentionally differs from the sibling-group cluster used for the
    bootstrap. It asks whether item-level results are driven by cases that
    contribute many clause items; within each case, items retain equal weight.
    """
    by_case = {}
    for row in rows:
        by_case.setdefault(row["case_number"], []).append(row)
    if not by_case:
        return {"weighting": "equal_primary_case", "n_cases": 0, "n_items": 0,
                "accuracy": None, "brier": None, "mean_confidence": None}
    blocks = list(by_case.values())
    return {
        "weighting": "equal_primary_case",
        "n_cases": len(blocks),
        "n_items": len(rows),
        "accuracy": sum(accuracy(block) for block in blocks) / len(blocks),
        "brier": sum(brier(block) for block in blocks) / len(blocks),
        "mean_confidence": (
            sum(mean_confidence(block) for block in blocks) / len(blocks)),
    }


def percentile_ci(values):
    vals = sorted(value for value in values if value is not None)
    if not vals:
        return None
    lo = vals[int(0.025 * (len(vals) - 1))]
    hi = vals[int(round(0.975 * (len(vals) - 1)))]
    return {"lo": lo, "hi": hi}


def paired_delta_metrics(pairs):
    """Mean within-pair T1-minus-T2 deltas."""
    if not pairs:
        return {"accuracy": None, "brier": None, "mean_confidence": None}
    n = len(pairs)
    return {
        "accuracy": sum(left["correct"] - right["correct"]
                        for left, right in pairs) / n,
        "brier": sum(
            (left["p"] - left["correct"]) ** 2
            - (right["p"] - right["correct"]) ** 2
            for left, right in pairs) / n,
        "mean_confidence": sum(left["p"] - right["p"]
                               for left, right in pairs) / n,
    }


def paired_delta_bootstrap(pairs, draws, seed):
    """Sibling/source-report-blocked CIs for paired T1-minus-T2 deltas."""
    by_cluster = {}
    cases = set()
    for left, right in pairs:
        left_cluster = (left.get("cluster_id")
                        or f"case_number:{left['case_number']}")
        right_cluster = (right.get("cluster_id")
                         or f"case_number:{right['case_number']}")
        if left_cluster != right_cluster:
            raise ValueError(
                f"T1/T2 pair {left['item_id']}/{right['item_id']} crosses "
                f"bootstrap clusters {left_cluster!r}/{right_cluster!r}")
        by_cluster.setdefault(left_cluster, []).append((left, right))
        cases.add(left["case_number"])
        cases.add(right["case_number"])
    clusters = sorted(by_cluster)
    sampled = {key: [] for key in ("accuracy", "brier", "mean_confidence")}
    rng = random.Random(seed)
    for _ in range(draws):
        draw = []
        for _ in range(len(clusters)):
            draw.extend(by_cluster[clusters[rng.randrange(len(clusters))]])
        metrics = paired_delta_metrics(draw)
        for key in sampled:
            sampled[key].append(metrics[key])
    return {
        "draws": draws,
        "seed": seed,
        "n_pairs": len(pairs),
        "n_cases": len(cases),
        "n_clusters": len(clusters),
        "cluster_unit": "sibling_group_or_case_fallback",
        **{key: percentile_ci(values) for key, values in sampled.items()},
    }


def paired_t1_t2_summary(rows, items_by_id, planned_records=None, dropped=(),
                         draws=1000, seed=20260802):
    """Planned-prefix T1/T2 intersection, with completion made explicit."""
    if planned_records is None:
        planned_records = rows

    def pair_map(records, values_are_rows):
        result = {"T1": {}, "T2": {}}
        seen_item_ids = set()
        for record in records:
            task = record.get("task")
            item_id = record.get("item_id")
            if task not in result or (task, item_id) in seen_item_ids:
                continue
            seen_item_ids.add((task, item_id))
            item = items_by_id.get(item_id)
            if item is None:
                raise ValueError(f"planned/scored item {item_id!r} is absent from item bank")
            key = runner_complaint_pair_key(item)
            old = result[task].get(key)
            value = record if values_are_rows else item_id
            old_item_id = old.get("item_id") if values_are_rows and old else old
            if old is not None and old_item_id != item_id:
                raise ValueError(
                    f"ambiguous {task} complaint-pair identity in {item['case_number']}")
            result[task][key] = value
        return result

    planned = pair_map(planned_records, False)
    scored = pair_map(rows, True)
    planned_keys = sorted(set(planned["T1"]) & set(planned["T2"]), key=repr)
    complete_keys = [key for key in planned_keys
                     if key in scored["T1"] and key in scored["T2"]]
    pairs = [(scored["T1"][key], scored["T2"][key]) for key in complete_keys]
    t1 = [left for left, _ in pairs]
    t2 = [right for _, right in pairs]
    dropped_by_item = {item_id: reason for item_id, reason in dropped}
    dropped_pairs = []
    for key in planned_keys:
        missing_tasks = [task for task in ("T1", "T2") if key not in scored[task]]
        if not missing_tasks:
            continue
        members = []
        for task in missing_tasks:
            item_id = planned[task][key]
            members.append({
                "task": task,
                "item_id": item_id,
                "reason": dropped_by_item.get(item_id, "planned item was not scored"),
            })
        sample_item = items_by_id[planned["T1"][key]]
        ref = sample_item["inputs"]["clause_ref"]
        dropped_pairs.append({
            "pair_id": canonical_sha256(key)[:16],
            "case_number": sample_item["case_number"],
            "clause": ref.get("clause"),
            "code_year": ref.get("code_year"),
            "missing": members,
        })

    n_complete = len(pairs)
    answer_changes = sum(left["answer"] != right["answer"] for left, right in pairs)
    planned_cases = {
        items_by_id[planned["T1"][key]]["case_number"] for key in planned_keys
    }
    complete_cases = {left["case_number"] for left, _ in pairs}
    return {
        "pair_identity": "run.complaint_pair_key (outcome-blind)",
        "pair_denominator": "selected-prefix planned T1/T2 intersection",
        # Compatibility aliases are explicitly the complete-pair denominator.
        "n_pairs": n_complete,
        "n_cases": len(complete_cases),
        "n_pairs_planned": len(planned_keys),
        "n_pairs_complete": n_complete,
        "n_pairs_dropped": len(dropped_pairs),
        "n_cases_planned": len(planned_cases),
        "n_cases_complete": len(complete_cases),
        "dropped_pairs": dropped_pairs,
        "label_mismatches": sum(left["label"] != right["label"]
                                for left, right in pairs),
        "answer_changes": answer_changes,
        "answer_change_rate": answer_changes / n_complete if n_complete else None,
        "per_task": {
            "T1": {**metric_set(t1), "case_weighted": case_weighted_metrics(t1)},
            "T2": {**metric_set(t2), "case_weighted": case_weighted_metrics(t2)},
        },
        "t1_minus_t2": paired_delta_metrics(pairs),
        "t1_minus_t2_ci": paired_delta_bootstrap(pairs, draws, seed),
    }


def selective_prediction(rows):
    """SP threshold sweep over an existing confidence signal.

    No model call is involved. At threshold ``tau`` the system accepts every
    item with confidence >= tau and defers the rest. Equal-confidence items
    enter together: breaking a P2 tie by item id would invent operating points
    the confidence method cannot actually select.

    AURC is the area under the attainable right-step selective-risk curve.
    Every operating point retains task/label-generic error transitions and an
    error rate within each accepted true-label stratum. The breach/no-breach
    false-negative/false-positive convenience fields are valid only when the
    entire evaluated row set is from T1/T2 and uses that label space; T3, legacy
    T4, or a mixed pool marks them inapplicable rather than reporting zero.
    """
    if not rows:
        return {"n": 0, "aurc": None,
                "breach_directional_metrics_applicable": False, "curve": []}

    by_confidence = {}
    for row in rows:
        by_confidence.setdefault(float(row["p"]), []).append(row)

    def stratum(row):
        return (str(row.get("task") or "unspecified"), str(row["label"]))

    strata = sorted({stratum(row) for row in rows})
    transition_space = sorted({
        (*stratum(row), str(row["answer"]))
        for row in rows if not row["correct"]
    })
    accepted_by_stratum = {key: 0 for key in strata}
    errors_by_stratum = {key: 0 for key in strata}
    transition_counts = {key: 0 for key in transition_space}

    def generic_error_snapshot():
        transitions = [
            {"task": task, "true_label": true_label,
             "predicted_answer": predicted_answer, "count": transition_counts[key]}
            for key in transition_space
            for task, true_label, predicted_answer in [key]
        ]
        rates = []
        for key in strata:
            task, true_label = key
            n_accepted = accepted_by_stratum[key]
            n_errors = errors_by_stratum[key]
            rates.append({
                "task": task,
                "true_label": true_label,
                "accepted": n_accepted,
                "errors": n_errors,
                "error_rate": n_errors / n_accepted if n_accepted else None,
            })
        return transitions, rates

    breach_space = {"breach", "no_breach"}
    breach_directional = all(
        row.get("task") in {"T1", "T2"}
        and row["label"] in breach_space and row["answer"] in breach_space
        for row in rows
    )

    total = len(rows)
    accepted = errors = missed_breaches = false_flags = 0
    accepted_breaches = accepted_no_breaches = 0
    previous_coverage = 0.0
    aurc = 0.0
    transitions, label_rates = generic_error_snapshot()
    curve = [{
        "threshold": None,
        "coverage": 0.0,
        "accepted": 0,
        "deferred": total,
        "risk": None,
        "accuracy": None,
        "misclassifications": 0,
        "misclassification_transitions": transitions,
        "true_label_error_rates": label_rates,
        "breach_directional_metrics_applicable": breach_directional,
        "missed_breaches": 0 if breach_directional else None,
        "false_flags": 0 if breach_directional else None,
        "breach_false_negative_rate": None,
        "no_breach_false_positive_rate": None,
    }]

    for threshold in sorted(by_confidence, reverse=True):
        group = by_confidence[threshold]
        accepted += len(group)
        errors += sum(1 - row["correct"] for row in group)
        for row in group:
            key = stratum(row)
            accepted_by_stratum[key] += 1
            if not row["correct"]:
                errors_by_stratum[key] += 1
                transition_counts[(*key, str(row["answer"]))] += 1
        if breach_directional:
            accepted_breaches += sum(row["label"] == "breach" for row in group)
            accepted_no_breaches += sum(row["label"] == "no_breach" for row in group)
            missed_breaches += sum(
                row["label"] == "breach" and row["answer"] == "no_breach"
                for row in group)
            false_flags += sum(
                row["label"] == "no_breach" and row["answer"] == "breach"
                for row in group)

        coverage = accepted / total
        risk = errors / accepted
        aurc += (coverage - previous_coverage) * risk
        previous_coverage = coverage
        transitions, label_rates = generic_error_snapshot()
        curve.append({
            "threshold": threshold,
            "coverage": coverage,
            "accepted": accepted,
            "deferred": total - accepted,
            "risk": risk,
            "accuracy": 1.0 - risk,
            "misclassifications": errors,
            "misclassification_transitions": transitions,
            "true_label_error_rates": label_rates,
            "breach_directional_metrics_applicable": breach_directional,
            "missed_breaches": missed_breaches if breach_directional else None,
            "false_flags": false_flags if breach_directional else None,
            "breach_false_negative_rate": (
                missed_breaches / accepted_breaches
                if breach_directional and accepted_breaches else None),
            "no_breach_false_positive_rate": (
                false_flags / accepted_no_breaches
                if breach_directional and accepted_no_breaches else None),
        })

    return {"n": total, "aurc": aurc,
            "breach_directional_metrics_applicable": breach_directional,
            "curve": curve}


# --- legacy P3: revealed-preference indifference point ----------------------

def logistic_fit(cs, ys, iters=200, ridge=1e-6):
    """Newton-IRLS fit of P(choose sure | c) = sigmoid(a + b*c).

    Ridge-regularised so a near-separable pattern cannot blow the Hessian up;
    deterministic (fixed start, fixed iteration cap). Only used for
    non-monotone patterns -- monotone and degenerate ones are read off directly,
    where the MLE is at infinity and a fit would be meaningless.
    """
    a = b = 0.0
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for c, y in zip(cs, ys):
            z = max(-30.0, min(30.0, a + b * c))
            mu = 1.0 / (1.0 + math.exp(-z))
            g0 += y - mu
            g1 += (y - mu) * c
            w = mu * (1.0 - mu)
            h00 += w
            h01 += w * c
            h11 += w * c * c
        g0 -= ridge * a
        g1 -= ridge * b
        h00 += ridge
        h11 += ridge
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (-h01 * g0 + h00 * g1) / det
        a += da
        b += db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return a, b


def _midpoint_from_count(cs, n_lottery, cap_low, cap_high):
    """Where a monotone switcher who chose the lottery n_lottery times switched."""
    if n_lottery == 0:
        return cap_low          # sure at every c -> indifference below the grid
    if n_lottery == len(cs):
        return cap_high         # lottery at every c -> indifference above the grid
    return (cs[n_lottery - 1] + cs[n_lottery]) / 2.0


def fit_indifference(choices):
    """(c, choice) pairs -> implied P(my answer matches the adjudicator).

    A model that prefers the lottery at c is revealing that it rates its own
    answer's chance of being right above c; preferring the sure amount reveals
    the opposite. The indifference point is that probability.

      monotone with a switch   -> midpoint of the switching interval
      always lottery           -> censored above the grid, capped
      always sure              -> censored below the grid, capped
      non-monotone             -> logistic regression of choice on c, and the
                                  item is FLAGGED (score.py reports the rate)

    Returns None if there are no usable choices.
    """
    pairs = sorted((float(c), ch) for c, ch in choices)
    if not pairs:
        return None
    cs = [c for c, _ in pairs]
    ys = [1 if ch == "sure" else 0 for _, ch in pairs]
    cap_low = max(0.001, min(cs) - CAP_MARGIN)
    cap_high = min(0.999, max(cs) + CAP_MARGIN)
    n_lottery = ys.count(0)
    # monotone == every 'sure' sits at a higher c than every 'lottery'
    monotone = all(ys[i] <= ys[i + 1] for i in range(len(ys) - 1))
    pattern = "".join("S" if y else "L" for y in ys)

    if monotone:
        p = _midpoint_from_count(cs, n_lottery, cap_low, cap_high)
        method = ("cap_low" if n_lottery == 0 else
                  "cap_high" if n_lottery == len(cs) else "midpoint")
    else:
        a, b = logistic_fit(cs, [float(y) for y in ys])
        if b > 1e-9 and math.isfinite(a) and math.isfinite(b):
            p = min(cap_high, max(cap_low, -a / b))
            method = "logistic"
        else:
            # A fit that is flat or runs the wrong way (preferring the SURE
            # payoff at low c and the lottery at high c) carries no indifference
            # point. Fall back to where a monotone switcher with the same number
            # of lottery choices would have switched, and keep the flag.
            p = _midpoint_from_count(cs, n_lottery, cap_low, cap_high)
            method = "count_midpoint"

    censored = ("high" if method == "cap_high" else
                "low" if method == "cap_low" else None)
    switch = None
    if method == "midpoint":
        switch = [cs[n_lottery - 1], cs[n_lottery]]
    return {"p": p, "method": method, "monotone": monotone, "censored": censored,
            "pattern": pattern, "n_choices": len(pairs), "c_grid": cs,
            "switch_interval": switch, "caps": [cap_low, cap_high]}


def bootstrap(rows, draws, seed):
    """Sibling-group-blocked percentile CIs; legacy rows fall back to case."""
    if not rows:
        return None
    by_cluster = {}
    for r in rows:
        cluster = r.get("cluster_id") or f"case_number:{r['case_number']}"
        by_cluster.setdefault(cluster, []).append(r)
    clusters = sorted(by_cluster)
    rng = random.Random(seed)
    acc, bri, ec, auc = [], [], [], []
    for _ in range(draws):
        drawn = []
        for _ in range(len(clusters)):
            drawn.extend(by_cluster[clusters[rng.randrange(len(clusters))]])
        acc.append(accuracy(drawn))
        bri.append(brier(drawn))
        ec.append(ece(drawn))
        auc.append(auroc(drawn))

    return {"draws": draws, "seed": seed, "n_clusters": len(clusters),
            "n_cases": len({r["case_number"] for r in rows}),
            "cluster_unit": "sibling_group_or_case_fallback",
            "accuracy": percentile_ci(acc), "brier": percentile_ci(bri),
            "ece": percentile_ci(ec), "auroc": percentile_ci(auc)}


def diagnostic_tag_metrics(rows, draws, seed):
    """Predeclared secondary strata, using the same blocked CIs as primary metrics."""
    out = {}
    for tag in DIAGNOSTIC_TAGS:
        sub = [row for row in rows if tag in row.get("tags", [])]
        if not sub:
            continue
        out[tag] = {
            **metric_set(sub),
            "task_counts": {
                task: sum(row["task"] == task for row in sub)
                for task in sorted({row["task"] for row in sub})
            },
            "case_weighted": case_weighted_metrics(sub),
            "ci": bootstrap(sub, draws, seed),
        }
    return out


def evaluation_view(rows, draws, seed):
    """Complete metric/SP view for one fixed item-level aggregation."""
    per_task = {}
    for task in TASKS:
        sub = [row for row in rows if row["task"] == task]
        if sub:
            per_task[task] = {
                **metric_set(sub),
                "case_weighted": case_weighted_metrics(sub),
                "reliability": adaptive_bins(sub),
                "ci": bootstrap(sub, draws, seed),
                "sp": selective_prediction(sub),
            }
    return {
        "overall": {
            **metric_set(rows),
            "case_weighted": case_weighted_metrics(rows),
            "reliability": adaptive_bins(rows),
            "ci": bootstrap(rows, draws, seed),
            "sp": selective_prediction(rows),
        },
        "per_task": per_task,
        "diagnostic_tags": diagnostic_tag_metrics(rows, draws, seed),
    }


def p3_vote_rows(rows):
    """Secondary verdict-frequency view from the same repeated P3 calls."""
    out = []
    for row in rows:
        vote = row["p3"]["vote"]
        out.append({
            **row,
            "answer": vote["answer"],
            "p": vote["p"],
            "correct": vote["correct"],
            "modal_tie": vote["modal_tie"],
        })
    return out


def _mean(values):
    return sum(values) / len(values) if values else None


def _p3_aux_point(rows):
    """Item-weighted repeated-draw and within-item dispersion estimands."""
    if not rows:
        return {
            "n_items": 0, "n_calls": 0,
            "single_draw": {"accuracy": None, "brier": None,
                            "mean_confidence": None},
            "dispersion": {
                "answer_disagreement_rate": None,
                "mean_modal_answer_frequency": None,
                "mean_answer_entropy_bits": None,
                "mean_stated_probability_sd": None,
                "mean_positive_probability_sd": None,
            },
        }
    single = [row["p3"]["single_draw"] for row in rows]
    dispersion = [row["p3"]["dispersion"] for row in rows]
    return {
        "n_items": len(rows),
        "n_calls": sum(block["n_calls"] for block in single),
        "single_draw": {
            "accuracy": _mean([block["accuracy"] for block in single]),
            "brier": _mean([block["brier"] for block in single]),
            "mean_confidence": _mean([block["mean_confidence"] for block in single]),
        },
        "dispersion": {
            "answer_disagreement_rate": _mean([
                float(block["has_answer_disagreement"]) for block in dispersion]),
            "mean_modal_answer_frequency": _mean([
                block["modal_answer_frequency"] for block in dispersion]),
            "mean_answer_entropy_bits": _mean([
                block["answer_entropy_bits"] for block in dispersion]),
            "mean_stated_probability_sd": _mean([
                block["stated_probability_sd"] for block in dispersion]),
            "mean_positive_probability_sd": _mean([
                block["positive_probability_sd"] for block in dispersion]),
        },
    }


def p3_aux_summary(rows, draws, seed):
    """Point estimates plus sibling/case-blocked CIs for P3 diagnostics."""
    point = _p3_aux_point(rows)
    if not rows:
        return {**point, "ci": None}
    by_cluster = {}
    for row in rows:
        cluster = row.get("cluster_id") or f"case_number:{row['case_number']}"
        by_cluster.setdefault(cluster, []).append(row)
    clusters = sorted(by_cluster)
    rng = random.Random(seed)
    sampled = []
    for _ in range(draws):
        drawn = []
        for _ in clusters:
            drawn.extend(by_cluster[clusters[rng.randrange(len(clusters))]])
        sampled.append(_p3_aux_point(drawn))

    def intervals(section):
        return {
            key: percentile_ci([sample[section][key] for sample in sampled])
            for key in point[section]
        }

    return {
        **point,
        "ci": {
            "draws": draws,
            "seed": seed,
            "n_clusters": len(clusters),
            "n_cases": len({row["case_number"] for row in rows}),
            "cluster_unit": "sibling_group_or_case_fallback",
            "single_draw": intervals("single_draw"),
            "dispersion": intervals("dispersion"),
        },
    }


def p3_item_diagnostics(rows):
    """Auditable per-item draw summaries without embedding every raw receipt."""
    return [{
        "item_id": row["item_id"],
        "task": row["task"],
        "case_number": row["case_number"],
        "cluster_id": row["cluster_id"],
        "label": row["label"],
        "k": row["p3"]["k"],
        "pool": {
            "answer": row["answer"],
            "confidence": row["p"],
            "correct": row["correct"],
            "positive_probability": row["p3"]["positive_probability"],
            "tie": row["p3"]["pool_tie"],
        },
        "vote": row["p3"]["vote"],
        "draws": row["p3"]["draws"],
        "single_draw": row["p3"]["single_draw"],
        "dispersion": row["p3"]["dispersion"],
    } for row in sorted(rows, key=lambda value: (value["task"], value["item_id"]))]


# Narrow code-level aliases for callers reading the frozen P1R source run.
# New score artifacts and active documentation use P3 exclusively.
p1r_vote_rows = p3_vote_rows
p1r_aux_summary = p3_aux_summary
p1r_item_diagnostics = p3_item_diagnostics


# --- aggregation -----------------------------------------------------------

def item_cluster_id(item):
    """Source-report sibling block, with an explicit legacy case fallback."""
    groups = [tag for tag in item.get("tags", [])
              if isinstance(tag, str) and tag.startswith("sibling_group:")]
    if len(groups) > 1:
        raise ValueError(f"{item['item_id']}: multiple sibling_group tags")
    return groups[0] if groups else f"case_number:{item['case_number']}"


def aggregate(responses, items_by_id, protocol, expected_repeats=None,
              run_contract=None, protocol_condition=None, aggregation=None):
    """One scored row per item, plus a tally of what could not be scored.

    ``run_contract`` separates the active ordered namespace from immutable
    archived identifiers. ``expected_repeats`` activates the cumulative
    repeated-protocol completeness checks; it is left as None for archived
    response-only runs that predate durable repeat identities.
    """
    semantics = protocol_semantics(protocol, run_contract, protocol_condition)
    if semantics == OFFLINE_SELECTIVE:
        raise ValueError("SP is an offline analysis, not an elicitation response protocol")
    if semantics == REPEATED_STATED:
        if aggregation != P3_AGGREGATION:
            raise ValueError(
                f"P3 requires aggregation={P3_AGGREGATION!r}")
        if expected_repeats is None or expected_repeats < 1 or expected_repeats % 2 == 0:
            raise ValueError(
                f"{REPEATED_STATED} requires a positive odd repeat horizon")
    by_item = {}
    for rec in responses:
        by_item.setdefault(rec["item_id"], []).append(rec)

    rows, dropped = [], []
    for item_id, recs in sorted(by_item.items()):
        item = items_by_id.get(item_id)
        if item is None:
            dropped.append((item_id, "no such item in the item bank"))
            continue

        # Modern request-ledger rows carry an explicit parsed-receipt count.
        # One-call protocols used to fall through to parsed[0], which made a
        # corrupt duplicate receipt look valid. Archived rows do not carry this
        # field and retain their historical responses-only path.
        if (semantics not in REPEATED_SEMANTICS
                and any("_parsed_receipt_count" in rec for rec in recs)):
            bad_receipts = []
            for rec in recs:
                count = rec.get("_parsed_receipt_count", 0)
                if count != 1:
                    call = rec.get("call_id") or f"repeat {_repeat(rec) or '?'}"
                    status = rec.get("_receipt_status") or rec.get("error") or "unparsed"
                    bad_receipts.append(f"{call} ({count} parsed; {status})")
            if bad_receipts:
                dropped.append((
                    item_id,
                    f"{protocol} requires exactly one parsed receipt for every planned call: "
                    + ", ".join(bad_receipts),
                ))
                continue

        if semantics in REPEATED_SEMANTICS and expected_repeats is not None:
            by_repeat = {}
            for rec in recs:
                by_repeat.setdefault(_repeat(rec), []).append(rec)
            wanted = set(range(1, expected_repeats + 1))
            missing = sorted(wanted - set(by_repeat))
            duplicate_plans = sorted(
                repeat for repeat, group in by_repeat.items()
                if repeat in wanted and len(group) != 1)
            unexpected = sorted(repeat for repeat in by_repeat if repeat not in wanted)
            if missing or duplicate_plans or unexpected:
                parts = []
                if missing:
                    parts.append(f"missing planned repeats {missing}")
                if duplicate_plans:
                    parts.append(f"duplicate planned repeats {duplicate_plans}")
                if unexpected:
                    parts.append(f"unexpected repeats {unexpected}")
                dropped.append((item_id, f"{protocol} plan is not exactly 1..{expected_repeats}: "
                                        + "; ".join(parts)))
                continue

            ordered_recs = [by_repeat[repeat][0]
                            for repeat in range(1, expected_repeats + 1)]
            missing_receipts = []
            duplicate_receipts = []
            for repeat, rec in enumerate(ordered_recs, 1):
                n_parsed = rec.get("_parsed_receipt_count",
                                   1 if rec.get("parsed") else 0)
                if n_parsed == 0:
                    detail = rec.get("_receipt_status") or rec.get("error") or "unparsed"
                    if detail == "error" and rec.get("error"):
                        detail = f"error: {rec['error']}"
                    missing_receipts.append(f"{repeat} ({detail})")
                elif n_parsed != 1:
                    duplicate_receipts.append(f"{repeat} ({n_parsed} parsed receipts)")
            if missing_receipts or duplicate_receipts:
                parts = []
                if missing_receipts:
                    parts.append("missing " + ", ".join(missing_receipts))
                if duplicate_receipts:
                    parts.append("duplicate " + ", ".join(duplicate_receipts))
                dropped.append((item_id, f"{protocol} requires one parsed receipt at every repeat "
                                        f"1..{expected_repeats}: " + "; ".join(parts)))
                continue

            request_identities = []
            for rec in ordered_recs:
                declared = rec.get("request_sha256")
                request = rec.get("request")
                if not isinstance(declared, str) or request is None:
                    request_identities.append(None)
                    continue
                actual = canonical_sha256(request)
                if declared != actual:
                    request_identities.append(("stale", declared, actual))
                else:
                    request_identities.append(("sha256", actual))
            if any(identity is None for identity in request_identities):
                dropped.append((item_id, f"{protocol} request/request_sha256 identity missing; "
                                         "cannot verify exact repeats"))
                continue
            stale = [identity for identity in request_identities
                     if identity[0] == "stale"]
            if stale:
                dropped.append((item_id, f"{protocol} request_sha256 does not bind the actual "
                                         "canonical request"))
                continue
            if len(set(request_identities)) != 1:
                dropped.append((item_id, f"{protocol} requests differ; repeats must be byte-identical"))
                continue

            if semantics == REPEATED_STATED:
                prompt_identities = []
                for rec in ordered_recs:
                    declared = rec.get("prompt_sha256")
                    request = rec.get("request")
                    if (not isinstance(declared, str) or not isinstance(request, dict)
                            or "system" not in request or "messages" not in request):
                        prompt_identities.append(None)
                        continue
                    actual = canonical_sha256({
                        "system": request["system"], "messages": request["messages"],
                    })
                    prompt_identities.append(("sha256", actual) if declared == actual
                                             else ("stale", declared, actual))
                if any(identity is None for identity in prompt_identities):
                    dropped.append((item_id, f"{protocol} prompt_sha256 identity missing; "
                                             "cannot verify exact prompts"))
                    continue
                if any(identity[0] == "stale" for identity in prompt_identities):
                    dropped.append((item_id, f"{protocol} prompt_sha256 does not bind the "
                                             "actual prompt"))
                    continue
                if len(set(prompt_identities)) != 1:
                    dropped.append((item_id, f"{protocol} prompt hashes differ across repeats"))
                    continue

                conditions = {rec.get("protocol_condition") for rec in ordered_recs}
                aggregations = {rec.get("aggregation") for rec in ordered_recs}
                expected_condition = (REPEATED_STATED if protocol == "P1" else None)
                if conditions != {expected_condition}:
                    dropped.append((item_id, f"{protocol} request rows do not all bind "
                                             f"protocol_condition={expected_condition!r}"))
                    continue
                if aggregations != {P3_AGGREGATION}:
                    dropped.append((item_id, f"{protocol} request rows do not all bind "
                                             f"aggregation={P3_AGGREGATION!r}"))
                    continue
            recs = ordered_recs

        parsed = [r["parsed"] for r in recs if r.get("parsed")]
        if not parsed:
            errs = sorted({r.get("error") or "unparsed" for r in recs})
            dropped.append((item_id, f"no parsed response ({'; '.join(errs)[:80]})"))
            continue

        fit = None
        if semantics == LEGACY_REVEALED_PREFERENCE:
            # Two call kinds per item: one verdict, then one choice per c. The
            # verdict fixes the answer; the choices fix the confidence.
            verdicts = [r["parsed"]["answer"] for r in recs
                        if (r.get("variant") or {}).get("stage") == "verdict" and r.get("parsed")]
            choices = [((r["variant"] or {}).get("c"), r["parsed"]["choice"]) for r in recs
                       if (r.get("variant") or {}).get("stage") == "choice"
                       and r.get("parsed") and (r["variant"] or {}).get("c") is not None]
            if not verdicts:
                dropped.append((item_id, "no parsed stage-1 verdict"))
                continue
            fit = fit_indifference(choices)
            if fit is None:
                dropped.append((item_id, "no parsed stage-2 choices"))
                continue
            answer, p, tied = verdicts[0], fit["p"], False
        elif semantics == STATED:
            answer = parsed[0]["answer"]
            p = float(parsed[0]["probability"])
            tied = False
        elif semantics == REPEATED_STATED:
            labels = P3_LABELS.get(item["task"])
            if labels is None:
                dropped.append((item_id, f"no repeated-P1 label orientation for {item['task']}"))
                continue
            positive, negative = labels
            if item["label"] not in labels:
                dropped.append((item_id, f"item label {item['label']!r} is outside {labels}"))
                continue
            answers, stated, oriented = [], [], []
            malformed = None
            for repeat, pr in enumerate(parsed, 1):
                draw_answer = pr.get("answer")
                q = pr.get("probability")
                if draw_answer not in labels:
                    malformed = f"repeat {repeat} has invalid answer {draw_answer!r}"
                    break
                if (not isinstance(q, (int, float)) or isinstance(q, bool)
                        or not math.isfinite(float(q)) or not 0 <= float(q) <= 1):
                    malformed = f"repeat {repeat} has invalid probability {q!r}"
                    break
                q = float(q)
                answers.append(draw_answer)
                stated.append(q)
                oriented.append(q if draw_answer == positive else 1.0 - q)
            if malformed:
                dropped.append((item_id, malformed))
                continue

            k = len(parsed)
            counts = {label: answers.count(label) for label in labels}
            vote_answer = positive if counts[positive] > counts[negative] else negative
            vote_p = max(counts.values()) / k
            vote_tie = counts[positive] == counts[negative]
            p_positive = sum(oriented) / k
            pool_tie = math.isclose(p_positive, 0.5, rel_tol=0.0, abs_tol=1e-15)
            if pool_tie:
                p_positive = 0.5
            answer = (positive if p_positive > 0.5 else negative
                      if p_positive < 0.5 else vote_answer)
            p = max(p_positive, 1.0 - p_positive)
            tied = pool_tie

            def population_sd(values):
                mean = sum(values) / len(values)
                return math.sqrt(sum((value - mean) ** 2 for value in values)
                                 / len(values))

            nonzero = [count / k for count in counts.values() if count]
            entropy = -sum(freq * math.log2(freq) for freq in nonzero)
            single_correct = [int(draw_answer == item["label"])
                              for draw_answer in answers]
            p_correct = [q for draw_answer, q in zip(answers, stated)]
            p3 = {
                "k": k,
                "positive_label": positive,
                "negative_label": negative,
                "positive_probability": p_positive,
                "pool_tie": pool_tie,
                "vote": {
                    "answer": vote_answer,
                    "p": vote_p,
                    "correct": int(vote_answer == item["label"]),
                    "modal_tie": vote_tie,
                    "positive_frequency": counts[positive] / k,
                },
                "single_draw": {
                    "n_calls": k,
                    "accuracy": sum(single_correct) / k,
                    "brier": sum((q - correct) ** 2
                                 for q, correct in zip(p_correct, single_correct)) / k,
                    "mean_confidence": sum(stated) / k,
                },
                "draws": [{
                    "repeat_index": repeat,
                    "answer": draw_answer,
                    "confidence": q,
                    "positive_probability": positive_q,
                    "correct": correct,
                    "receipt_alias": recs[repeat - 1].get("receipt_alias"),
                } for repeat, (draw_answer, q, positive_q, correct) in enumerate(
                    zip(answers, stated, oriented, single_correct), 1)],
                "dispersion": {
                    "answer_counts": counts,
                    "n_unique_answers": len(nonzero),
                    "has_answer_disagreement": len(nonzero) > 1,
                    "modal_answer_frequency": max(counts.values()) / k,
                    "answer_entropy_bits": entropy,
                    "stated_probability_mean": sum(stated) / k,
                    "stated_probability_sd": population_sd(stated),
                    "stated_probability_min": min(stated),
                    "stated_probability_max": max(stated),
                    "positive_probability_mean": p_positive,
                    "positive_probability_sd": population_sd(oriented),
                    "positive_probability_min": min(oriented),
                    "positive_probability_max": max(oriented),
                },
            }
        else:
            counts = {}
            for pr in parsed:
                counts[pr["answer"]] = counts.get(pr["answer"], 0) + 1
            top = max(counts.values())
            winners = sorted(a for a, c in counts.items() if c == top)
            tied = len(winners) > 1
            answer = winners[0]  # deterministic tie-break; flagged
            p = top / len(parsed)

        row = {
            "item_id": item_id,
            "task": item["task"],
            "case_number": item["case_number"],
            "cluster_id": item_cluster_id(item),
            "tags": [tag for tag in item.get("tags", []) if isinstance(tag, str)],
            "split": item["split"],
            "label": item["label"],
            "answer": answer,
            "p": p,
            "correct": 1 if answer == item["label"] else 0,
            "n_calls": len(recs),
            "n_parsed": len(parsed),
            "modal_tie": tied,
        }
        if fit is not None:
            row["legacy_p3"] = fit
        if semantics == REPEATED_STATED:
            row["p3"] = p3
        rows.append(row)
    return rows, dropped


def legacy_p3_summary(rows):
    """Archived revealed-preference coherence summary; never active P3."""
    fits = [r["legacy_p3"] for r in rows if r.get("legacy_p3")]
    if not fits:
        return None
    n = len(fits)
    methods = {}
    for f in fits:
        methods[f["method"]] = methods.get(f["method"], 0) + 1
    non_monotone = [r["item_id"] for r in rows
                    if r.get("legacy_p3") and not r["legacy_p3"]["monotone"]]
    return {
        "n_items_fitted": n,
        "switching_consistency_rate": (n - len(non_monotone)) / n,
        "n_non_monotone": len(non_monotone),
        "non_monotone_items": sorted(non_monotone),
        "non_monotone_patterns": sorted({r["legacy_p3"]["pattern"] for r in rows
                                         if r.get("legacy_p3")
                                         and not r["legacy_p3"]["monotone"]}),
        "censored_high": sum(1 for f in fits if f["censored"] == "high"),
        "censored_low": sum(1 for f in fits if f["censored"] == "low"),
        "censored_total": sum(1 for f in fits if f["censored"]),
        "method_counts": methods,
        "choices_per_item_min": min(f["n_choices"] for f in fits),
        "choices_per_item_max": max(f["n_choices"] for f in fits),
        "caps": fits[0]["caps"],
    }


def fmt(x, nd=3):
    return "n/a" if x is None else f"{x:.{nd}f}"


def score_output_path(run_dir, requested, prefix_requested):
    """Keep an exploratory prefix from replacing the canonical full score."""
    if prefix_requested and not requested:
        raise ValueError("--out is required with --through-items/--through-repeats; "
                         "a prefix score must not overwrite the run's canonical scores.json")
    return pathlib.Path(requested) if requested else pathlib.Path(run_dir) / "scores.json"


# --- self-test ---------------------------------------------------------------

def self_test():
    """Unit-test active estimators and the archived indifference fit.

    No network, no run directory, no item bank -- just the estimator against
    patterns whose answer is known by construction. Run it after touching
    fit_indifference(); legacy P3 archaeology is only as trustworthy as this
    function.
    """
    G = [0.55, 0.65, 0.75, 0.85, 0.95]      # the default grid
    L, S = "lottery", "sure"
    cases = [
        # (name, choices, expected p, expected method, expected monotone)
        ("monotone, switches between 0.75 and 0.85",
         list(zip(G, [L, L, L, S, S])), 0.80, "midpoint", True),
        ("monotone, switches at the bottom of the grid",
         list(zip(G, [L, S, S, S, S])), 0.60, "midpoint", True),
        ("monotone, switches at the top of the grid",
         list(zip(G, [L, L, L, L, S])), 0.90, "midpoint", True),
        ("always lottery -> censored above the grid, capped",
         list(zip(G, [L, L, L, L, L])), 0.97, "cap_high", True),
        ("always sure -> censored below the grid, capped",
         list(zip(G, [S, S, S, S, S])), 0.53, "cap_low", True),
        ("non-monotone (one reversal) -> logistic fit, flagged",
         list(zip(G, [L, S, L, S, S])), None, "logistic", False),
        ("non-monotone, perverse slope -> count midpoint, flagged",
         list(zip(G, [S, S, L, L, L])), 0.80, "count_midpoint", False),
        ("single-point grid, lottery -> capped above that point",
         [(0.75, L)], 0.77, "cap_high", True),
        ("custom low grid, always sure -> cap tracks the grid, not 0.53",
         list(zip([0.10, 0.20, 0.30], [S, S, S])), 0.08, "cap_low", True),
    ]

    failures = []
    print("indifference fit -- synthetic choice patterns")
    print(f"  {'pattern':>8} {'p':>7} {'method':>15} {'mono':>5} {'censored':>9}  case")
    for name, choices, want_p, want_method, want_mono in cases:
        got = fit_indifference(choices)
        ok = True
        if got["method"] != want_method or got["monotone"] != want_mono:
            ok = False
        if want_p is not None and abs(got["p"] - want_p) > 1e-9:
            ok = False
        if want_p is None and not (min(c for c, _ in choices) <= got["p"]
                                   <= max(c for c, _ in choices)):
            ok = False   # a fitted point must land inside the swept grid
        # order of the choices must not matter: the fit sorts by c
        shuffled = list(reversed(choices))
        if abs(fit_indifference(shuffled)["p"] - got["p"]) > 1e-12:
            ok = False
            name += "  [ORDER-SENSITIVE]"
        if not ok:
            failures.append((name, got, want_p, want_method, want_mono))
        print(f"  {got['pattern']:>8} {got['p']:>7.4f} {got['method']:>15} "
              f"{str(got['monotone']):>5} {str(got['censored']):>9}  {name} "
              f"{'ok' if ok else 'FAIL'}")

    if fit_indifference([]) is not None:
        failures.append(("empty choice list must return None", None, None, None, None))

    # AUROC is new here too, and cheap to pin down.
    def rows(ps, cs):
        return [{"p": p, "correct": c, "item_id": str(i)}
                for i, (p, c) in enumerate(zip(ps, cs))]
    auroc_cases = [
        ("perfect separation", rows([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]), 1.0),
        ("reversed", rows([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]), 0.0),
        ("all tied", rows([0.5, 0.5, 0.5, 0.5], [1, 1, 0, 0]), 0.5),
        ("one class only", rows([0.9, 0.8], [1, 1]), None),
    ]
    print("\nAUROC")
    for name, rs, want in auroc_cases:
        got = auroc(rs)
        ok = (got is None and want is None) or (got is not None and want is not None
                                                and abs(got - want) < 1e-12)
        if not ok:
            failures.append((f"auroc: {name}", got, want, None, None))
        print(f"  {fmt(got, 4):>7} (want {fmt(want, 4)})  {name} {'ok' if ok else 'FAIL'}")

    weighted_rows = [
        {"item_id": f"cw-{i}", "case_number": "CASE/A", "p": 0.9,
         "correct": 1} for i in range(3)
    ] + [{"item_id": "cw-3", "case_number": "CASE/B", "p": 0.8,
          "correct": 0}]
    item_weighted = metric_set(weighted_rows)
    equal_case = case_weighted_metrics(weighted_rows)
    cluster_rows = [
        {**weighted_rows[0], "case_number": "CASE/A1", "cluster_id": "sibling_group:A"},
        {**weighted_rows[1], "case_number": "CASE/A2", "cluster_id": "sibling_group:A"},
        {**weighted_rows[3], "case_number": "CASE/B", "cluster_id": "sibling_group:B"},
    ]
    clustered = bootstrap(cluster_rows, 10, 7)
    weighting_ok = (
        abs(item_weighted["accuracy"] - 0.75) < 1e-12
        and abs(item_weighted["brier"] - 0.1675) < 1e-12
        and abs(item_weighted["mean_confidence"] - 0.875) < 1e-12
        and equal_case["n_cases"] == 2
        and abs(equal_case["accuracy"] - 0.5) < 1e-12
        and abs(equal_case["brier"] - 0.325) < 1e-12
        and abs(equal_case["mean_confidence"] - 0.85) < 1e-12
        and clustered["n_clusters"] == 2
        and clustered["n_cases"] == 3
    )
    if not weighting_ok:
        failures.append(("case weighting / sibling bootstrap", {
            "item": item_weighted, "equal_case": equal_case, "clustered": clustered,
        }, "equal-case metrics and two sibling clusters", None, None))
    print(f"\nWeighting and bootstrap units\n  {'ok' if weighting_ok else 'FAIL'}  "
          "item/equal-case metrics and sibling-group clusters")

    diagnostic_rows = [
        {**cluster_rows[0], "task": "T1",
         "tags": ["appeal_flip", "voluntary_admission"]},
        {**cluster_rows[1], "task": "T2", "tags": ["appeal_flip"]},
        {**cluster_rows[2], "task": "T3", "tags": ["appeal_survived"]},
    ]
    diagnostics = diagnostic_tag_metrics(diagnostic_rows, 10, 9)
    diagnostics_ok = (
        set(diagnostics) == {"appeal_flip", "appeal_survived",
                             "voluntary_admission"}
        and diagnostics["appeal_flip"]["n"] == 2
        and diagnostics["appeal_flip"]["task_counts"] == {"T1": 1, "T2": 1}
        and diagnostics["appeal_flip"]["ci"]["n_clusters"] == 1
        and diagnostics["appeal_survived"]["case_weighted"]["n_cases"] == 1
        and "burden_of_proof_candidate" not in diagnostics
    )
    if not diagnostics_ok:
        failures.append(("diagnostic tag strata", diagnostics,
                         "tag metrics with sibling-blocked CIs", None, None))
    print(f"\nDiagnostic tag strata\n  {'ok' if diagnostics_ok else 'FAIL'}  "
          "secondary metrics and sibling-blocked CIs")

    def paired_item(task, suffix, case, clause, complaint_hash, label):
        return {
            "item_id": f"{task}-{suffix}", "task": task, "case_number": case,
            "label": label, "split": "test", "tags": [f"sibling_group:{case}"],
            "inputs": {
                "clause_ref": {"clause": clause, "code_year": 2021},
                "extract_provenance": [{
                    "kind": "complaint", "file": f"{case}.html", "pane": "report",
                    "char_start": 1, "char_end": 2, "text_sha256": complaint_hash,
                }],
            },
        }

    pair_items = [
        paired_item("T1", "a", "CASE/A", "1", "hash-a", "breach"),
        paired_item("T2", "a", "CASE/A", "1", "hash-a", "breach"),
        paired_item("T1", "b", "CASE/B", "2", "hash-b", "no_breach"),
        paired_item("T2", "b", "CASE/B", "2", "hash-b", "no_breach"),
        paired_item("T1", "unpaired", "CASE/C", "3", "hash-c", "breach"),
    ]
    pair_items_by_id = {item["item_id"]: item for item in pair_items}
    paired_rows = [
        {"item_id": "T1-a", "task": "T1", "case_number": "CASE/A",
         "cluster_id": "sibling_group:CASE/A",
         "label": "breach", "answer": "breach", "p": 0.8, "correct": 1},
        {"item_id": "T2-a", "task": "T2", "case_number": "CASE/A",
         "cluster_id": "sibling_group:CASE/A",
         "label": "breach", "answer": "no_breach", "p": 0.6, "correct": 0},
        {"item_id": "T1-b", "task": "T1", "case_number": "CASE/B",
         "cluster_id": "sibling_group:CASE/B",
         "label": "no_breach", "answer": "no_breach", "p": 0.7, "correct": 1},
        {"item_id": "T2-b", "task": "T2", "case_number": "CASE/B",
         "cluster_id": "sibling_group:CASE/B",
         "label": "no_breach", "answer": "no_breach", "p": 0.9, "correct": 1},
        {"item_id": "T1-unpaired", "task": "T1", "case_number": "CASE/C",
         "label": "breach", "answer": "breach", "p": 0.5, "correct": 1},
    ]
    pair_plan = [{"item_id": item["item_id"], "task": item["task"]}
                 for item in pair_items]
    paired = paired_t1_t2_summary(
        paired_rows, pair_items_by_id, pair_plan, [], 20, 11)
    incomplete_pairing = paired_t1_t2_summary(
        [row for row in paired_rows if row["item_id"] != "T2-b"],
        pair_items_by_id, pair_plan,
        [("T2-b", "pending: no receipt")], 20, 11)
    pairing_ok = (
        runner_complaint_pair_key(pair_items_by_id["T1-a"])
        == runner_complaint_pair_key(pair_items_by_id["T2-a"])
        and paired["n_pairs"] == 2 and paired["n_cases"] == 2
        and paired["n_pairs_planned"] == 2
        and paired["n_pairs_complete"] == 2
        and paired["n_pairs_dropped"] == 0
        and paired["t1_minus_t2_ci"]["n_clusters"] == 2
        and paired["t1_minus_t2_ci"]["accuracy"] is not None
        and paired["label_mismatches"] == 0
        and paired["answer_changes"] == 1
        and abs(paired["answer_change_rate"] - 0.5) < 1e-12
        and paired["per_task"]["T1"]["accuracy"] == 1.0
        and paired["per_task"]["T2"]["accuracy"] == 0.5
        and abs(paired["t1_minus_t2"]["accuracy"] - 0.5) < 1e-12
        and abs(paired["t1_minus_t2"]["brier"] - (-0.12)) < 1e-12
        and abs(paired["t1_minus_t2"]["mean_confidence"]) < 1e-12
        and incomplete_pairing["n_pairs_planned"] == 2
        and incomplete_pairing["n_pairs_complete"] == 1
        and incomplete_pairing["n_pairs_dropped"] == 1
        and incomplete_pairing["dropped_pairs"][0]["missing"] == [{
            "task": "T2", "item_id": "T2-b", "reason": "pending: no receipt",
        }]
    )
    if not pairing_ok:
        failures.append(("T1/T2 exact paired intersection", paired,
                         "planned/complete/drop pairs and blocked paired deltas",
                         None, None))
    print(f"\nT1/T2 paired intersection\n  {'ok' if pairing_ok else 'FAIL'}  "
          "planned denominator, visible drops and blocked paired deltas")

    # SP: two items at the same confidence must enter together, and the two
    # binary error directions must remain distinguishable for later costs.
    sp_rows = [
        {"item_id": "a", "task": "T1", "p": 0.9, "correct": 1,
         "label": "breach", "answer": "breach"},
        {"item_id": "b", "task": "T1", "p": 0.7, "correct": 0,
         "label": "breach", "answer": "no_breach"},
        {"item_id": "c", "task": "T1", "p": 0.7, "correct": 0,
         "label": "no_breach", "answer": "breach"},
        {"item_id": "d", "task": "T1", "p": 0.5, "correct": 1,
         "label": "no_breach", "answer": "no_breach"},
    ]
    sp = selective_prediction(sp_rows)
    sp_mid_transitions = {
        (row["task"], row["true_label"], row["predicted_answer"]): row["count"]
        for row in sp["curve"][2]["misclassification_transitions"]
    }
    sp_mid_rates = {
        (row["task"], row["true_label"]): row
        for row in sp["curve"][2]["true_label_error_rates"]
    }
    sp_ok = (
        [point["accepted"] for point in sp["curve"]] == [0, 1, 3, 4]
        and abs(sp["curve"][2]["risk"] - (2 / 3)) < 1e-12
        and sp["breach_directional_metrics_applicable"] is True
        and sp["curve"][2]["missed_breaches"] == 1
        and sp["curve"][2]["false_flags"] == 1
        and sp["curve"][2]["misclassifications"] == 2
        and sp_mid_transitions == {
            ("T1", "breach", "no_breach"): 1,
            ("T1", "no_breach", "breach"): 1,
        }
        and sp_mid_rates[("T1", "breach")]["accepted"] == 2
        and abs(sp_mid_rates[("T1", "breach")]["error_rate"] - 0.5) < 1e-12
        and sp_mid_rates[("T1", "no_breach")]["error_rate"] == 1.0
        and abs(sp["aurc"] - (0.25 * 0 + 0.5 * (2 / 3) + 0.25 * 0.5)) < 1e-12
    )

    # T3 and a pooled T1/T3 result must expose their errors through the same
    # generic transition/rate fields. Breach-specific names are not meaningful
    # for either evaluated row set and must therefore be null, never zero.
    t3_rows = [
        {"item_id": "u", "task": "T3", "p": 0.95, "correct": 1,
         "label": "upheld", "answer": "upheld"},
        {"item_id": "v", "task": "T3", "p": 0.80, "correct": 0,
         "label": "overturned", "answer": "upheld"},
        {"item_id": "w", "task": "T3", "p": 0.60, "correct": 0,
         "label": "upheld", "answer": "overturned"},
    ]
    sp_t3 = selective_prediction(t3_rows)
    t3_final = sp_t3["curve"][-1]
    t3_transitions = {
        (row["task"], row["true_label"], row["predicted_answer"]): row["count"]
        for row in t3_final["misclassification_transitions"]
    }
    t3_rates = {
        (row["task"], row["true_label"]): row
        for row in t3_final["true_label_error_rates"]
    }
    t3_ok = (
        sp_t3["breach_directional_metrics_applicable"] is False
        and t3_final["misclassifications"] == 2
        and sum(t3_transitions.values()) == 2
        and t3_transitions == {
            ("T3", "overturned", "upheld"): 1,
            ("T3", "upheld", "overturned"): 1,
        }
        and abs(t3_rates[("T3", "upheld")]["error_rate"] - 0.5) < 1e-12
        and t3_rates[("T3", "overturned")]["error_rate"] == 1.0
        and t3_final["missed_breaches"] is None
        and t3_final["false_flags"] is None
        and t3_final["breach_false_negative_rate"] is None
        and t3_final["no_breach_false_positive_rate"] is None
    )

    sp_pooled = selective_prediction(sp_rows + t3_rows)
    pooled_final = sp_pooled["curve"][-1]
    pooled_transition_tasks = {
        row["task"] for row in pooled_final["misclassification_transitions"]
        if row["count"]
    }
    pooled_ok = (
        sp_pooled["breach_directional_metrics_applicable"] is False
        and pooled_final["misclassifications"] == 4
        and sum(row["count"] for row in pooled_final["misclassification_transitions"]) == 4
        and pooled_transition_tasks == {"T1", "T3"}
        and pooled_final["missed_breaches"] is None
        and all(
            sum(row["count"] for row in point["misclassification_transitions"])
            == point["misclassifications"]
            for point in sp_pooled["curve"]
        )
    )
    legacy_t4 = selective_prediction([{**sp_rows[1], "task": "T4"}])
    legacy_t4_final = legacy_t4["curve"][-1]
    legacy_t4_ok = (
        legacy_t4["breach_directional_metrics_applicable"] is False
        and legacy_t4_final["misclassifications"] == 1
        and legacy_t4_final["missed_breaches"] is None
        and sum(row["count"] for row in
                legacy_t4_final["misclassification_transitions"]) == 1
    )
    sp_ok = sp_ok and t3_ok and pooled_ok and legacy_t4_ok
    if not sp_ok:
        failures.append(("SP generic risk/coverage", {
            "breach_space": sp, "t3": sp_t3, "pooled": sp_pooled,
            "legacy_t4": legacy_t4,
        }, "generic transitions/rates retain every T1/T3 error", None, None))
    print(f"\nSP selective prediction\n  {'ok' if sp_ok else 'FAIL'}  "
          f"tie-aware generic T1/T3/pooled errors, AURC={sp['aurc']:.4f}")

    # New P2 runs use exact repeated requests and a fixed K. Missing or
    # duplicate receipts must invalidate the whole item instead of silently
    # changing its denominator.
    p1_item = {
        "item_id": "p1-item", "task": "T1", "case_number": "TEST/1",
        "split": "test", "label": "breach",
    }

    def p1_rec(repeat, answer="breach", receipt_count=1,
               request_body="same", request_hash=None):
        request = {"body": request_body}
        if request_hash is None:
            request_hash = canonical_sha256(request)
        return {
            "call_id": f"call-{repeat}", "item_id": "p1-item",
            "task_rank": 1, "item_rank": 1, "repeat_index": repeat,
            "request_sha256": request_hash,
            "request": request,
            "parsed": {"answer": answer} if receipt_count else None,
            "_parsed_receipt_count": receipt_count,
            "_receipt_status": "parsed" if receipt_count else "pending",
            "error": None if receipt_count else "pending: no receipt",
        }

    complete = [p1_rec(1), p1_rec(2, "no_breach"), p1_rec(3)]
    p1_rows, p1_dropped = aggregate(
        complete, {"p1-item": p1_item}, "P2", 3, ACTIVE_RUN_CONTRACT)
    complete_ok = (len(p1_rows) == 1 and not p1_dropped
                   and p1_rows[0]["n_parsed"] == 3
                   and abs(p1_rows[0]["p"] - 2 / 3) < 1e-12)

    pending = [p1_rec(1), p1_rec(2, receipt_count=0), p1_rec(3)]
    pending_rows, pending_dropped = aggregate(
        pending, {"p1-item": p1_item}, "P2", 3, ACTIVE_RUN_CONTRACT)
    pending_ok = (not pending_rows and len(pending_dropped) == 1
                  and "one parsed receipt" in pending_dropped[0][1]
                  and "2 (pending)" in pending_dropped[0][1])

    duplicate = [p1_rec(1), p1_rec(2, receipt_count=2), p1_rec(3)]
    duplicate_rows, duplicate_dropped = aggregate(
        duplicate, {"p1-item": p1_item}, "P2", 3, ACTIVE_RUN_CONTRACT)
    duplicate_ok = (not duplicate_rows and len(duplicate_dropped) == 1
                    and "2 parsed receipts" in duplicate_dropped[0][1])

    incomplete_rows, incomplete_dropped = aggregate(
        [p1_rec(1), p1_rec(3)], {"p1-item": p1_item}, "P2", 3,
        ACTIVE_RUN_CONTRACT)
    incomplete_ok = (not incomplete_rows and len(incomplete_dropped) == 1
                     and "missing planned repeats [2]" in incomplete_dropped[0][1])

    changed = [p1_rec(1), p1_rec(2), p1_rec(3, request_body="different")]
    changed_rows, changed_dropped = aggregate(
        changed, {"p1-item": p1_item}, "P2", 3, ACTIVE_RUN_CONTRACT)
    exact_ok = (not changed_rows and len(changed_dropped) == 1
                and "byte-identical" in changed_dropped[0][1])

    shared_stale_hash = canonical_sha256({"body": "same"})
    stale_hash = [p1_rec(1),
                  p1_rec(2, request_body="different",
                         request_hash=shared_stale_hash),
                  p1_rec(3)]
    stale_rows, stale_dropped = aggregate(
        stale_hash, {"p1-item": p1_item}, "P2", 3, ACTIVE_RUN_CONTRACT)
    hash_ok = (not stale_rows and len(stale_dropped) == 1
               and "does not bind" in stale_dropped[0][1])

    p1_ok = (complete_ok and pending_ok and duplicate_ok and incomplete_ok
             and exact_ok and hash_ok)
    if not p1_ok:
        failures.append(("P2 exact-K completeness", {
            "complete": [p1_rows, p1_dropped],
            "pending": [pending_rows, pending_dropped],
            "duplicate": [duplicate_rows, duplicate_dropped],
            "incomplete": [incomplete_rows, incomplete_dropped],
            "changed": [changed_rows, changed_dropped],
            "stale_hash": [stale_rows, stale_dropped],
        }, "complete exact K scores; all malformed groups drop", None, None))
    print(f"\nP2 cumulative completeness\n  {'ok' if p1_ok else 'FAIL'}  "
          "exact prompts, exact repeats 1..K, one parsed receipt per repeat")

    # P1 must not pick parsed[0] from a duplicated provider receipt.
    p2_single = [{
        "call_id": "p2-call", "item_id": "p1-item", "repeat_index": 1,
        "parsed": {"answer": "breach", "probability": 0.8},
        "_parsed_receipt_count": 1, "_receipt_status": "parsed",
    }]
    p2_rows, p2_dropped = aggregate(
        p2_single, {"p1-item": p1_item}, "P1",
        run_contract=ACTIVE_RUN_CONTRACT)
    duplicate_call = [{**p2_single[0], "_parsed_receipt_count": 2}]
    got_rows, got_dropped = aggregate(
        duplicate_call, {"p1-item": p1_item}, "P1",
        run_contract=ACTIVE_RUN_CONTRACT)
    duplicate_non_p1_ok = (
        len(p2_rows) == 1 and not p2_dropped and not got_rows
        and len(got_dropped) == 1
        and "exactly one parsed receipt" in got_dropped[0][1]
    )
    if not duplicate_non_p1_ok:
        failures.append(("P1 duplicate receipts", [got_rows, got_dropped],
                         "active P1 duplicate calls must drop", None, None))
    print(f"\nP1 receipt integrity\n  {'ok' if duplicate_non_p1_ok else 'FAIL'}  "
          "one parsed receipt per planned call")

    namespace_ok = (
        protocol_semantics("P1", ACTIVE_RUN_CONTRACT) == STATED
        and protocol_semantics("P2", ACTIVE_RUN_CONTRACT) == RESAMPLING
        and protocol_semantics("P3", ACTIVE_RUN_CONTRACT) == REPEATED_STATED
        and protocol_semantics("SP", ACTIVE_RUN_CONTRACT) == OFFLINE_SELECTIVE
        and resolve_protocol("P1", ACTIVE_RUN_CONTRACT,
                             REPEATED_STATED)["active_protocol"] == "P3"
        and protocol_semantics("P1", None) == RESAMPLING
        and protocol_semantics("P2", None) == STATED
        and protocol_semantics("P3", None) == LEGACY_REVEALED_PREFERENCE
    )
    # P4 resolves ONLY in the active namespace (claimed 2026-08-16 by the
    # incentivized-deferral protocol); the legacy namespace never had a P4.
    namespace_ok = namespace_ok and (
        protocol_semantics("P4", ACTIVE_RUN_CONTRACT) == INCENTIVIZED_DEFERRAL)
    try:
        protocol_semantics("P4", None)
        namespace_ok = False
    except ValueError:
        pass
    if not namespace_ok:
        failures.append(("protocol namespace mapping", None,
                         "active and archived identifiers remain distinct", None, None))
    print(f"\nProtocol namespace mapping\n  {'ok' if namespace_ok else 'FAIL'}  "
          "active P1/P2/P3/P4 + SP, explicit P1R alias, and legacy ids "
          "remain distinct")

    horizon_plan = [
        {"task": "T1", "task_rank": rank} for rank in (1, 3)
    ] + [
        {"task": "T2", "task_rank": rank} for rank in (1, 2)
    ]
    horizon_ok = True
    try:
        # Sparse ranks are valid after split filtering; the available maximum
        # is the contract being checked.
        validate_item_horizon(horizon_plan[:2], 3)
        validate_item_horizon(horizon_plan, 2)
    except ValueError:
        horizon_ok = False
    try:
        validate_item_horizon(horizon_plan, 3)
        horizon_ok = False
    except ValueError as exc:
        horizon_ok = horizon_ok and "T2 horizon=2" in str(exc)
    if not horizon_ok:
        failures.append(("prefix item horizon", horizon_plan,
                         "sparse T1 passes; shallow T2 fails", None, None))
    print(f"\nPrefix item horizon\n  {'ok' if horizon_ok else 'FAIL'}  "
          "explicit N cannot exceed any planned task horizon")

    output_guard_ok = False
    try:
        score_output_path("run", "", True)
    except ValueError as exc:
        output_guard_ok = "--out is required" in str(exc)
    output_guard_ok = (
        output_guard_ok
        and score_output_path("run", "", False) == pathlib.Path("run/scores.json")
        and score_output_path("run", "prefix.json", True) == pathlib.Path("prefix.json")
    )
    if not output_guard_ok:
        failures.append(("prefix output guard", None,
                         "prefix flags require an explicit --out", None, None))
    print(f"\nPrefix output safety\n  {'ok' if output_guard_ok else 'FAIL'}  "
          "explicit --out required for prefix scores")

    if failures:
        print(f"\nFAILURES: {len(failures)}")
        for f in failures:
            print(f"  {f[0]}: got {f[1]}")
        return 1
    print(f"\nOK: {len(cases)} legacy-indifference cases + {len(auroc_cases)} AUROC cases "
          "+ weighting/clustering + paired T1/T2 + SP risk/coverage + P2 exact-K "
          "completeness + protocol namespace + receipt/output guards pass.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="", help="a bench/runs/<timestamp> directory")
    ap.add_argument("--self-test", action="store_true",
                    help="unit-test estimators, including archived legacy P3, then exit")
    ap.add_argument("--items", default="", help="item bank (default: the path recorded in manifest.json)")
    ap.add_argument("--through-items", type=int, default=None,
                    help="score cumulative task ranks 1..N (0/all by default)")
    ap.add_argument("--through-repeats", type=int, default=None,
                    help="score cumulative P2/P3 repeats 1..K (default: run maximum)")
    ap.add_argument("--draws", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20260802)
    ap.add_argument("--out", default="",
                    help="default <run>/scores.json; required with either prefix flag")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.run:
        raise SystemExit("--run is required (or use --self-test)")
    if args.through_items is not None and args.through_items < 0:
        raise SystemExit("--through-items must be >= 0")
    if args.through_repeats is not None and args.through_repeats < 1:
        raise SystemExit("--through-repeats must be >= 1")

    run_dir = pathlib.Path(args.run)
    responses_path = run_dir / "responses.jsonl"
    requests_path = run_dir / "requests.jsonl"
    ledger_path = run_dir / "ledger.jsonl"
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    items_path = pathlib.Path(args.items or manifest.get("items_path") or DEFAULT_ITEMS)
    if not items_path.exists():
        raise SystemExit(f"item bank not found: {items_path}")
    file_input_bindings = scoring_input_bindings(
        items_path, manifest_path, requests_path, responses_path, ledger_path)

    responses = load_optional_jsonl(responses_path)
    requests = load_optional_jsonl(requests_path)
    ledger = load_optional_jsonl(ledger_path)
    items = load_jsonl(items_path)
    items_by_id = {it["item_id"]: it for it in items}
    config_binding = request_config_binding(manifest, requests)
    input_bindings = {**file_input_bindings, "request_config": config_binding}

    source_protocol = (manifest.get("protocol")
                or (requests[0].get("protocol") if requests else None)
                or (responses[0].get("protocol") if responses else "P2"))
    run_contract = manifest.get("contract")
    protocol_condition = manifest.get("protocol_condition")
    aggregation_name = manifest.get("aggregation")
    try:
        resolution = resolve_protocol(
            source_protocol, run_contract, protocol_condition)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    protocol = resolution["active_protocol"]
    semantics = resolution["semantics"]
    repeated = semantics in REPEATED_SEMANTICS
    cumulative = is_cumulative_catalog(requests)
    prefix_requested = (args.through_items is not None
                        or args.through_repeats is not None)
    try:
        out = score_output_path(run_dir, args.out, cumulative and prefix_requested)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if requests and not cumulative and (args.through_items is not None
                                        or args.through_repeats is not None):
        raise SystemExit("prefix scoring needs requests.jsonl rows with call_id, "
                         "task_rank/item_rank and repeat_index")

    expected_repeats = None
    receipt_alias_summary = {
        "schema_version": "pmcpa.receipt-alias.v1",
        "n_alias_calls": 0, "n_native_calls": 0,
        "source_runs": [], "registry": None,
    }
    if cumulative:
        request_protocols = {row.get("protocol", source_protocol) for row in requests}
        if len(request_protocols) != 1 or source_protocol not in request_protocols:
            raise SystemExit("requests.jsonl and manifest disagree on protocol")
        manifest_config = config_binding["manifest_config"]
        manifest_config_hash = config_binding["manifest_config_hash"]
        request_config_hashes = config_binding["request_config_hashes"]
        if (manifest_config is None or not isinstance(manifest_config_hash, str)
                or canonical_sha256(manifest_config) != manifest_config_hash):
            raise SystemExit("manifest config_hash does not bind manifest config")
        if (not isinstance(manifest_config_hash, str)
                or request_config_hashes != [manifest_config_hash]
                or any(row.get("config_hash") != manifest_config_hash
                       for row in requests)):
            raise SystemExit("requests.jsonl config hashes disagree with manifest config")
        if semantics == REPEATED_STATED:
            config_condition = manifest_config.get(
                "protocol_condition", manifest_config.get("condition"))
            if config_condition != protocol_condition:
                raise SystemExit("manifest protocol_condition disagrees with manifest config")
            if aggregation_name != P3_AGGREGATION:
                raise SystemExit(
                    f"P3 requires aggregation={P3_AGGREGATION!r}")
            if manifest_config.get("aggregation") != aggregation_name:
                raise SystemExit("manifest aggregation disagrees with manifest config")
            if {row.get("protocol_condition") for row in requests} != {protocol_condition}:
                raise SystemExit("requests.jsonl protocol_condition disagrees with manifest")
            if {row.get("aggregation") for row in requests} != {aggregation_name}:
                raise SystemExit("requests.jsonl aggregation disagrees with manifest")
        if not repeated and args.through_repeats is not None:
            raise SystemExit(f"--through-repeats does not apply to {protocol} "
                             f"({semantics})")
        if repeated:
            expected_repeats = (args.through_repeats
                                or manifest.get("through_repeats")
                                or manifest.get("k")
                                or max(_repeat(row) for row in requests))
            try:
                expected_repeats = int(expected_repeats)
            except (TypeError, ValueError) as exc:
                raise SystemExit(f"run has no valid {protocol} repeat horizon") from exc
            if expected_repeats < 1:
                raise SystemExit(f"run has no valid {protocol} repeat horizon")
            if semantics == REPEATED_STATED and expected_repeats % 2 == 0:
                raise SystemExit(
                    f"{REPEATED_STATED} requires an odd K (supported prefixes: 1, 3, 5, 7)")
        through_items = args.through_items or 0
        try:
            validate_item_horizon(requests, through_items)
            records, call_coverage = cumulative_records(
                requests, responses, ledger, through_items,
                expected_repeats if repeated else 1, semantics)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        try:
            receipt_alias_summary = verify_receipt_aliases(
                records, run_dir, manifest)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"receipt alias verification failed: {exc}") from exc
        rows, dropped = aggregate(records, items_by_id, source_protocol, expected_repeats,
                                  run_contract, protocol_condition, aggregation_name)
        item_receipts = {
            rec["item_id"] for rec in records if rec.get("_raw_receipt_count")
        }
        coverage = {
            **call_coverage,
            "items_in_bank": len(items),
            "items_planned": len({rec["item_id"] for rec in records}),
            "items_receipted": len(item_receipts),
            "items_scored": len(rows),
            "items_dropped": len(dropped),
            # Compatibility: in a planned-ledger run, calls means in-horizon
            # planned calls rather than only successful response rows.
            "calls": call_coverage["calls_planned"],
            "modal_ties": sum(1 for row in rows if row["modal_tie"]),
            "receipt_aliases": receipt_alias_summary["n_alias_calls"],
            "native_receipts": receipt_alias_summary["n_native_calls"],
        }
    else:
        if not responses_path.exists():
            raise SystemExit(f"{responses_path} not found (and no ranked requests.jsonl plan)")
        if args.through_items is not None or args.through_repeats is not None:
            raise SystemExit("archived response-only runs cannot be prefix-filtered: "
                             "task ranks/repeat indices were not recorded")
        records = responses
        rows, dropped = aggregate(records, items_by_id, source_protocol,
                                  run_contract=run_contract)
        coverage = {
            "mode": "legacy_responses_only",
            "items_in_bank": len(items),
            "items_attempted": len({r["item_id"] for r in responses}),
            "items_scored": len(rows),
            "items_dropped": len(dropped),
            "calls": len(responses),
            "calls_parsed": sum(1 for r in responses if r.get("parsed")),
            "modal_ties": sum(1 for r in rows if r["modal_tie"]),
        }

    primary_view = evaluation_view(rows, args.draws, args.seed)

    scores = {
        "run": str(run_dir),
        "items": str(items_path),
        "protocol": protocol,
        "protocol_condition": None if protocol == "P3" else protocol_condition,
        "aggregation": aggregation_name,
        "protocol_semantics": semantics,
        "protocol_namespace": ("active" if run_contract == ACTIVE_RUN_CONTRACT
                               else "legacy"),
        "source_protocol": source_protocol,
        "source_protocol_condition": protocol_condition,
        "protocol_provenance": {
            "schema_version": PROTOCOL_RESOLUTION_SCHEMA,
            "active_protocol": protocol,
            "source_contract": run_contract,
            "source_protocol": source_protocol,
            "source_protocol_condition": protocol_condition,
            "source_aggregation": aggregation_name,
            "mapping_id": resolution["mapping_id"],
            "storage_identity_preserved": True,
            "receipt_aliases": receipt_alias_summary,
        },
        "model": manifest.get("model"),
        "k": (expected_repeats if cumulative and repeated
              else manifest.get("k")),
        "through_items": (args.through_items if cumulative else None),
        "through_repeats": (expected_repeats
                            if cumulative and repeated else None),
        "scoring_inputs": input_bindings,
        "coverage": coverage,
        "overall": primary_view["overall"],
        "per_task": primary_view["per_task"],
        "diagnostic_tags": primary_view["diagnostic_tags"],
        "dropped": [{"item_id": i, "reason": why} for i, why in dropped],
        "bootstrap": {"draws": args.draws, "seed": args.seed,
                      "unit": "sibling_group_or_case_fallback"},
    }
    planned_tasks = {rec.get("task") for rec in records} if cumulative else set()
    if {"T1", "T2"} <= planned_tasks:
        try:
            scores["t1_t2_paired"] = paired_t1_t2_summary(
                rows, items_by_id, records, dropped, args.draws, args.seed)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if semantics == REPEATED_STATED:
        vote_rows = p3_vote_rows(rows)
        vote_view = evaluation_view(vote_rows, args.draws, args.seed)
        pool_view = {
            "overall": primary_view["overall"],
            "per_task": primary_view["per_task"],
            "diagnostic_tags": primary_view["diagnostic_tags"],
        }
        if "t1_t2_paired" in scores:
            pool_view["t1_t2_paired"] = scores["t1_t2_paired"]
            try:
                vote_view["t1_t2_paired"] = paired_t1_t2_summary(
                    vote_rows, items_by_id, records, dropped, args.draws, args.seed)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc

        aux_overall = p3_aux_summary(rows, args.draws, args.seed)
        aux_per_task = {
            task: p3_aux_summary(
                [row for row in rows if row["task"] == task], args.draws, args.seed)
            for task in TASKS if any(row["task"] == task for row in rows)
        }

        def aux_view(block, section):
            ci = block.get("ci")
            return {
                "n_items": block["n_items"],
                "n_calls": block["n_calls"],
                **block[section],
                "ci": (None if ci is None else {
                    key: value for key, value in ci.items() if key != "single_draw"
                    and key != "dispersion"
                } | ci[section]),
            }

        scores["p3"] = {
            "schema_version": "pmcpa.p3-score.v1",
            "aggregation": P3_AGGREGATION,
            "primary_view": "pool",
            "positive_labels": {task: labels[0] for task, labels in P3_LABELS.items()},
            "views": {"pool": pool_view, "vote": vote_view},
            "single_draw": {
                "estimand": "mean performance of one repeated stated-probability draw",
                "overall": aux_view(aux_overall, "single_draw"),
                "per_task": {task: aux_view(block, "single_draw")
                             for task, block in aux_per_task.items()},
            },
            "dispersion": {
                "estimand": "item-weighted within-item repeat variation",
                "overall": aux_view(aux_overall, "dispersion"),
                "per_task": {task: aux_view(block, "dispersion")
                             for task, block in aux_per_task.items()},
            },
            "item_diagnostics": p3_item_diagnostics(rows),
        }
    if semantics == LEGACY_REVEALED_PREFERENCE:
        scores["legacy_p3_revealed_preference"] = legacy_p3_summary(rows)
        scores["c_grid"] = manifest.get("c_grid")

    if scoring_input_bindings(
            items_path, manifest_path, requests_path, responses_path,
            ledger_path) != file_input_bindings:
        raise SystemExit("a scoring input changed while score.py was running; retry")
    if cumulative:
        try:
            if verify_receipt_aliases(records, run_dir, manifest) != receipt_alias_summary:
                raise SystemExit("receipt alias inputs changed while score.py was running; retry")
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"receipt alias re-verification failed: {exc}") from exc
    out.write_text(json.dumps(scores, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    ov, ci = scores["overall"], scores["overall"]["ci"] or {}
    print(f"run       : {run_dir}")
    source_note = (f"; source {source_protocol}+{protocol_condition}"
                   if resolution["mapping_id"] else "")
    print(f"protocol  : {protocol} ({semantics}{source_note})   model: {manifest.get('model', '?')}   "
          f"K: {scores.get('k') if scores.get('k') is not None else '?'}")
    if cumulative:
        cov = scores["coverage"]
        item_horizon = ("all catalogued" if not args.through_items
                        else f"1..{args.through_items} per task")
        print(f"horizon   : items {item_horizon}"
              + (f"   repeats 1..{expected_repeats}"
                 if repeated else ""))
        print(f"coverage  : {ov['n']} item(s) scored of {cov['items_planned']} planned; "
              f"calls planned={cov['planned']} receipted={cov['receipted']} "
              f"parsed={cov['parsed']} errors={cov['errors']} pending={cov['pending']}")
        if cov["calls_with_duplicate_parsed_receipts"]:
            print(f"duplicates: {cov['calls_with_duplicate_parsed_receipts']} call(s) have "
                  "more than one parsed response receipt")
        if cov["orphan_receipt_call_ids"] or cov["unlinked_response_rows"] \
                or cov["unlinked_ledger_rows"]:
            print(f"orphans   : {cov['orphan_receipt_call_ids']} unknown call ID(s), "
                  f"{cov['unlinked_response_rows'] + cov['unlinked_ledger_rows']} "
                  "receipt row(s) without call_id")
    else:
        print(f"coverage  : {ov['n']} item(s) scored of "
              f"{scores['coverage']['items_attempted']} attempted "
              f"({scores['coverage']['calls_parsed']}/"
              f"{scores['coverage']['calls']} calls parsed; legacy responses-only run)")
    if dropped:
        print(f"dropped   : {len(dropped)}  e.g. {dropped[0][0]} -- {dropped[0][1]}")
    if scores["coverage"]["modal_ties"]:
        tie_rule = ("pool p = 0.5; answer resolved by the odd-K modal verdict"
                    if semantics == REPEATED_STATED
                    else "broken alphabetically; p = 0.5 either way")
        print(f"modal ties: {scores['coverage']['modal_ties']} ({tie_rule})")
    print()
    print(f"{'':<11}{'n':>4}  {'acc':>6} {'[95% CI]':>16}  {'Brier':>6} {'[95% CI]':>16}  "
          f"{'ECE':>6}  {'AUROC':>6}")
    def line(name, block):
        c = block.get("ci") or {}
        acc_ci = c.get("accuracy") or {}
        bri_ci = c.get("brier") or {}
        print(f"{name:<11}{block['n']:>4}  {fmt(block['accuracy']):>6} "
              f"{('[' + fmt(acc_ci.get('lo')) + ', ' + fmt(acc_ci.get('hi')) + ']'):>16}  "
              f"{fmt(block['brier']):>6} "
              f"{('[' + fmt(bri_ci.get('lo')) + ', ' + fmt(bri_ci.get('hi')) + ']'):>16}  "
              f"{fmt(block['ece']):>6}  {fmt(block.get('auroc')):>6}")
    line("overall", ov)
    for task in TASKS:
        if task in scores["per_task"]:
            line(task, scores["per_task"][task])
    sensitivity = ov["case_weighted"]
    print(f"\nequal-case sensitivity: n_cases={sensitivity['n_cases']} "
          f"acc={fmt(sensitivity['accuracy'])} Brier={fmt(sensitivity['brier'])} "
          f"mean confidence={fmt(sensitivity['mean_confidence'])}")
    print(f"CIs are sibling-group-blocked: {args.draws} draws over "
          f"{(ci.get('n_clusters') if ci else 0)} cluster(s) "
          f"from {(ci.get('n_cases') if ci else 0)} primary case(s), seed {args.seed}.")
    if scores.get("t1_t2_paired"):
        pair = scores["t1_t2_paired"]
        print(f"T1/T2 planned intersection: {pair['n_pairs_complete']}/"
              f"{pair['n_pairs_planned']} complete pair(s), "
              f"{pair['n_pairs_dropped']} dropped; answer-change rate "
              f"{fmt(pair['answer_change_rate'])}")
    if scores.get("legacy_p3_revealed_preference"):
        b = scores["legacy_p3_revealed_preference"]
        print(f"\nlegacy P3 revealed preference ({b['n_items_fitted']} item(s), "
              f"c grid {scores.get('c_grid')})")
        print(f"  switching consistency : {b['switching_consistency_rate']:.3f} "
              f"({b['n_items_fitted'] - b['n_non_monotone']}/{b['n_items_fitted']} monotone in c)")
        print(f"  non-monotone          : {b['n_non_monotone']}"
              + (f"  patterns {', '.join(b['non_monotone_patterns'])}"
                 if b['non_monotone_patterns'] else ""))
        print(f"  censored at a cap     : {b['censored_total']} "
              f"(high {b['censored_high']} = always took the lottery, "
              f"low {b['censored_low']} = always took the sure payoff; "
              f"caps {b['caps'][0]:.2f}/{b['caps'][1]:.2f})")
        print(f"  fit methods           : "
              f"{', '.join(f'{k}={v}' for k, v in sorted(b['method_counts'].items()))}")
        if b["n_non_monotone"]:
            print("  non-monotone items are FLAGGED, not dropped: the prompt tells the model "
                  "consistency is not required, so the rate is a result.")
    print("\nreliability (equal-mass bins, overall)")
    print(f"  {'bin':>3} {'n':>4} {'p range':>14} {'conf':>6} {'acc':>6} {'gap':>7}")
    for b in ov["reliability"]:
        print(f"  {b['bin']:>3} {b['n']:>4} {fmt(b['p_min'], 2) + '-' + fmt(b['p_max'], 2):>14} "
              f"{fmt(b['confidence']):>6} {fmt(b['accuracy']):>6} {b['gap']:>+7.3f}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
