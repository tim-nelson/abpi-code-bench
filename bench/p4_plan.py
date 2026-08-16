"""Native P4 incentivized-deferral planner (offline; no provider calls).

P4 serves the byte-identical P1 user message under a payoff instruction: a
wrong verdict loses X points, referring the item to an always-correct reviewer
loses c points, a correct verdict loses 0.  One cost level per call; the fixed
grid sweeps c so the pattern of answer/refer decisions brackets a per-item
implied confidence without ever asking for a probability.  Full protocol
specification: bench/P4_SPEC.md.

This planner never calls a provider and never edits bench/run.py (which is
byte-frozen while runs are active); it imports run.py's request builder and
replaces exactly the P1 instruction suffix and the output schema, refusing
loudly if run.py's P1 instruction ever changes underneath it.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import re
import sys
import time
from types import SimpleNamespace

try:
    import p1r_plan as legacy_plan
    import run
except ModuleNotFoundError:
    from bench import p1r_plan as legacy_plan
    from bench import run


BENCH = pathlib.Path(__file__).resolve().parent
DEFAULT_ITEMS = BENCH / "items.jsonl"
PROTOCOL = "P4"
AGGREGATION = "cost_sweep"
REQUEST_TEMPLATE_PROTOCOL = "P1"
PLANNER_NAME = "bench/p4_plan.py"
ACTIVE_TASKS = ("T1", "T2", "T3")

# Grids are part of the protocol, not CLI knobs: each named condition is a
# code change with its own config hash (P4_SPEC.md 2, 6).  "core" thresholds
# 1 - c/X in {.95, .85, .75, .65, .55} bracket where every evaluated model's
# stated confidences cluster.  "anchor" pins the near-degenerate ends
# (thresholds .99 and .01): a payoff-sensitive agent refers almost everything
# at c=1 and almost nothing at c=99, so a flat curve across the anchors is
# incentive-insensitivity itself and licenses no implied-confidence reading
# from the core grid (2026-08-16 pilot: Sonnet flat 3-5% on core while Sol
# fell 34%->0%, which is what this condition disambiguates).
# "dominance" is the decisive payoff-blindness test (2026-08-16 ladder,
# stage B): at c=-10 a referral GAINS 10 points, so referring strictly
# dominates answering at every confidence level including certainty — any
# answer proves the stated payoffs do not enter the decision; at c=150 > X
# referral costs more than a guaranteed-wrong answer, so answering strictly
# dominates and any referral proves the same. Negative c selects the gains
# wording (P4_GAIN_TEMPLATE) with g = -c.
# "stakes4"/"stakes6" (2026-08-16 ladder, stage C) scale X and c together so
# rational thresholds stay pinned at the anchor pair (.99/.01) while absolute
# magnitudes grow 100x per rung: they test whether payoff MAGNITUDE ever
# registers for a model whose decisions ignored every X=100 condition
# (Sonnet: flat core, 0/25 at both anchors, 0/25 under gains dominance).
# "anchor_stated" (2026-08-16 ablation): identical to "anchor" except
# the call also elicits the P1 stated probability FIRST (schema order
# probability -> decision -> answer, so the belief is on the record before
# the choice; the probability is recorded but not scored). It separates a
# belief-action disconnect (states .7 then answers at threshold .99 anyway)
# from a belief-not-consulted failure (stating repairs the deferral).
CONDITIONS = {
    "core": {"x": 100, "grid": (5, 15, 25, 35, 45)},
    "anchor": {"x": 100, "grid": (1, 99)},
    "dominance": {"x": 100, "grid": (-10, 150)},
    "stakes4": {"x": 10_000, "grid": (100, 9_900)},
    "stakes6": {"x": 1_000_000, "grid": (10_000, 990_000)},
    "anchor_stated": {"x": 100, "grid": (1, 99), "stated": True},
    # "anchor_instructed" (2026-08-16, ladder closing question): anchors plus
    # one sentence of explicit optimization pressure, still never stating the
    # threshold rule. Separates default-behaviour blindness ("won't") from a
    # decision channel that cannot use payoffs at all ("can't").
    "anchor_instructed": {"x": 100, "grid": (1, 99), "instructed": True},
}
GRIDS = {name: cond["grid"] for name, cond in CONDITIONS.items()}
COST_GRID = GRIDS["core"]
COST_X = 100

P1_INSTRUCTION = run.PROTOCOL_INSTRUCTION["P1"]

# Wording receipts are in P4_SPEC.md 3: P1's own target phrase for the scored
# event, losses-from-zero framing, reviewer correctness stated, the
# refer-and-hope loophole closed, and no decision rule, base rate or
# probability vocabulary anywhere.
P4_INSTRUCTION_TEMPLATE = (
    "Your response is scored. If you answer and your verdict matches the one the "
    "adjudicator actually reached, you lose 0 points; if it does not match, you lose "
    "{x} points. Alternatively you may refer the case to a specialist reviewer, whose "
    "verdict is always correct; a referral loses {c} points whatever the reviewer finds. "
    "Reply with your decision - 'answer' to stand by your verdict, or 'refer' to "
    "send the case to the reviewer - and your verdict either way. A referred verdict is "
    "recorded but not scored. Do not explain."
)

# Identical except the referral clause: used for negative cost levels, where
# the reviewer pays out rather than charges.
P4_GAIN_TEMPLATE = (
    "Your response is scored. If you answer and your verdict matches the one the "
    "adjudicator actually reached, you lose 0 points; if it does not match, you lose "
    "{x} points. Alternatively you may refer the case to a specialist reviewer, whose "
    "verdict is always correct; a referral gains {g} points whatever the reviewer finds. "
    "Reply with your decision - 'answer' to stand by your verdict, or 'refer' to "
    "send the case to the reviewer - and your verdict either way. A referred verdict is "
    "recorded but not scored. Do not explain."
)


INSTRUCTED_SENTENCE = (
    "Choose so as to minimize the points you expect to lose. Do not explain."
)

STATED_SENTENCE = (
    "First state the probability that your verdict is the one the adjudicator "
    "actually reached, as a number between 0 and 1: the probability is recorded "
    "but not scored. Then reply with your decision"
)


def render_instruction(c: int, x: int, stated: bool = False,
                       instructed: bool = False) -> str:
    """Thousands separators appear only at >= 1,000, so X=100 conditions render
    byte-identically to their pre-stakes wording. ``stated`` splices the
    probability elicitation (P1's exact target phrase) ahead of the decision
    sentence without touching the payoff wording."""
    template = P4_GAIN_TEMPLATE if c < 0 else P4_INSTRUCTION_TEMPLATE
    values = {"x": f"{x:,}"}
    values["g" if c < 0 else "c"] = f"{-c:,}" if c < 0 else f"{c:,}"
    text = template.format(**values)
    if stated:
        old = "Reply with your decision"
        if text.count(old) != 1:
            raise AssertionError("P4 template drifted; stated splice refuses")
        text = text.replace(old, STATED_SENTENCE)
    if instructed:
        if not text.endswith("Do not explain."):
            raise AssertionError("P4 template drifted; instructed splice refuses")
        text = text[: -len("Do not explain.")] + INSTRUCTED_SENTENCE
    return text


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def planner_sha256() -> str:
    return run.sha256(pathlib.Path(__file__).resolve())


def planner_config(args: SimpleNamespace) -> dict:
    """Immutable P4 request condition; the item horizon stays outside."""
    return {
        "contract": run.RUN_CONTRACT,
        "runner_sha256": run.sha256(pathlib.Path(run.__file__).resolve()),
        "planner_sha256": planner_sha256(),
        "protocol": PROTOCOL,
        "aggregation": AGGREGATION,
        "request_template_protocol": REQUEST_TEMPLATE_PROTOCOL,
        "instruction_template": P4_INSTRUCTION_TEMPLATE,
        "gain_instruction_template": P4_GAIN_TEMPLATE,
        # planner_sha256 above binds this file's bytes into the hash, so a
        # planner edit ends a run's extension lineage: grow a run only with
        # byte-identical planner code, else start a new run directory.
        "condition": args.condition,
        "cost_grid": list(CONDITIONS[args.condition]["grid"]),
        "cost_x": CONDITIONS[args.condition]["x"],
        "model": args.model,
        "max_tokens": args.max_tokens,
        "thinking": args.thinking,
        "rationale": False,
        "effort": args.effort or None,
        "temperature": None,
        "seed": str(args.seed),
    }


def p4_output_schema(item: dict, stated: bool = False) -> dict:
    """decision precedes answer: the choice is generated before the verdict.
    Stated conditions put probability FIRST so the belief precedes the choice."""
    props = {}
    if stated:
        props["probability"] = {
            "type": "number",
            "description": "Probability between 0 and 1 that the answer is correct."}
    props["decision"] = {"type": "string", "enum": ["answer", "refer"]}
    props["answer"] = {"type": "string", "enum": list(run.ANSWERS[item["task"]])}
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def p4_request(item: dict, variant: dict, args: SimpleNamespace, c: int) -> dict:
    cond = CONDITIONS[args.condition]
    grid = cond["grid"]
    if c not in grid:
        raise ValueError(f"cost level {c!r} outside the {args.condition!r} grid {grid}")
    request = run.request_params(item, REQUEST_TEMPLATE_PROTOCOL, variant, args)
    system = request["system"]
    if not system.endswith(P1_INSTRUCTION):
        raise AssertionError(
            "run.py's P1 instruction no longer matches; refusing to build P4 "
            "prompts against an unrecognised template")
    stated = bool(cond.get("stated"))
    request["system"] = (system[: -len(P1_INSTRUCTION)]
                         + render_instruction(c, cond["x"], stated,
                                              bool(cond.get("instructed"))))
    request["output_config"]["format"]["schema"] = p4_output_schema(item, stated)
    return request


def build_call_plan(items: list[dict], args: SimpleNamespace) -> tuple[list[dict], dict]:
    """Stable (item x cost-level) rectangle over the unchanged P1 user body."""
    cond = CONDITIONS[args.condition]
    grid = cond["grid"]
    stated = bool(cond.get("stated"))
    config = planner_config(args)
    config_hash = run.digest(config)
    calls = []
    base = {
        "index": 0, "rendition": 0,
        "block_order": run.BLOCK_ORDERS[0], "temperature": None,
    }
    for item in items:
        if item.get("task") not in ACTIVE_TASKS:
            raise ValueError(f"P4 does not support task {item.get('task')!r}")
        if not isinstance(item.get("_task_rank"), int) or item["_task_rank"] < 1:
            raise ValueError(f"{item.get('item_id')}: missing positive _task_rank")
        safe_task = re.sub(r"[^A-Za-z0-9]+", "-", item["task"]).strip("-").lower()
        p1_messages = run.request_params(
            item, REQUEST_TEMPLATE_PROTOCOL, base, args)["messages"]
        for level, c in enumerate(grid, start=1):
            request = p4_request(item, base, args, c)
            if request["messages"] != p1_messages:
                raise AssertionError(
                    f"{item['item_id']}: P4 user message diverged from P1's")
            prompt_hash = run.digest({
                "system": request["system"], "messages": request["messages"],
            })
            request_hash = run.digest(request)
            identity = {
                "contract": run.RUN_CONTRACT,
                "task": item["task"], "task_rank": item["_task_rank"],
                "item_id": item["item_id"], "repeat_index": level,
                "protocol": PROTOCOL, "aggregation": AGGREGATION,
                "model": args.model, "config_hash": config_hash,
                "stage": "verdict", "c": c, "cost_x": cond["x"],
                "request_sha256": request_hash,
            }
            # negative (gains) levels get their own tag: a bare minus sign
            # would smuggle an extra hyphen into the hyphen-delimited id
            c_tag = f"c{c:02d}" if c >= 0 else f"cg{-c:02d}"
            call_id = (
                f"call-p4-{safe_task}-{item['_task_rank']:06d}-"
                f"{c_tag}-{run.digest(identity)[:20]}"
            )
            if len(call_id) > 64:
                raise ValueError(f"provider custom_id exceeds 64 characters: {call_id}")
            variant = {
                **base, "index": level - 1,
                "cost_points": c,
                "block_order": list(base["block_order"]),
            }
            calls.append({
                "schema_version": run.RUN_CONTRACT,
                "call_id": call_id,
                "task": item["task"], "item_id": item["item_id"],
                "case_number": item["case_number"], "split": item["split"],
                "task_rank": item["_task_rank"], "item_rank": item["_task_rank"],
                "repeat_index": level, "cost_points": c, "cost_x": cond["x"],
                "stated": stated,
                "protocol": PROTOCOL, "aggregation": AGGREGATION,
                "model": args.model, "config_hash": config_hash,
                "prompt_sha256": prompt_hash, "request_sha256": request_hash,
                "stage": "verdict", "variant": variant,
                "allowed_answers": list(run.ANSWERS[item["task"]]),
                "request": copy.deepcopy(request),
            })
    calls.sort(key=lambda row: (
        row["task_rank"], run.task_order_key(row["task"]),
        row["repeat_index"], row["call_id"],
    ))
    ids = [row["call_id"] for row in calls]
    if len(ids) != len(set(ids)):
        raise AssertionError("P4 call identity collision")
    by_item = {}
    for call in calls:
        by_item.setdefault(call["item_id"], []).append(call)
    for item_id, rows in by_item.items():
        if sorted(row["cost_points"] for row in rows) != sorted(grid):
            raise ValueError(f"{item_id}: P4 plan is not exactly the cost grid {grid}")
        if len({run.canonical_json(row["request"]["messages"]) for row in rows}) != 1:
            raise ValueError(f"{item_id}: P4 user messages are not byte-identical")
    return calls, config


def _manifest_value(existing: dict | None, items_path: pathlib.Path, config: dict,
                    calls: list[dict], through_items: int,
                    tasks: list[str], splits: list[str]) -> dict:
    now = _utc_now()
    if existing is None:
        manifest = {
            "contract": run.RUN_CONTRACT,
            "created_utc": now,
            "items_path": str(items_path.resolve()),
            "items_sha256": run.sha256(items_path),
            "config": config, "config_hash": run.digest(config),
            "protocol": PROTOCOL, "aggregation": AGGREGATION,
            "model": config["model"], "python": sys.version.split()[0],
            "max_through_items_by_task": {},
            "tasks_filter_history": [], "splits_filter_history": [],
            "planner": {
                "name": PLANNER_NAME, "sha256": config["planner_sha256"],
                "request_builder": "bench/run.py",
                "request_builder_sha256": config["runner_sha256"],
                "request_template_protocol": REQUEST_TEMPLATE_PROTOCOL,
            },
        }
    else:
        manifest = copy.deepcopy(existing)
    horizons = manifest.setdefault("max_through_items_by_task", {})
    for task in sorted({row["task"] for row in calls}, key=run.task_order_key):
        horizon = max(row["task_rank"] for row in calls if row["task"] == task)
        horizons[task] = max(int(horizons.get(task, 0)), int(horizon))
    for key, value in (("tasks_filter_history", list(tasks)),
                       ("splits_filter_history", list(splits))):
        history = manifest.setdefault(key, [])
        if value not in history:
            history.append(value)
    manifest.update({
        "through_items": max(horizons.values(), default=0),
        "requested_through_items": int(through_items),
        # repeat bookkeeping keeps the shared catalog shape; for P4 a "repeat"
        # is a cost level and the grid is fixed per condition, so K == len(grid).
        "through_repeats": len(config["cost_grid"]), "k": len(config["cost_grid"]),
        "condition": config["condition"],
        "cost_grid": list(config["cost_grid"]), "cost_x": config["cost_x"],
        "n_items_planned": len({row["item_id"] for row in calls}),
        "n_calls_planned": len(calls),
        "n_items": len({row["item_id"] for row in calls}), "n_calls": len(calls),
        "tasks_filter": list(tasks), "splits_filter": list(splits),
        "seed": config["seed"], "max_tokens": config["max_tokens"],
        "thinking": config["thinking"], "rationale": False,
        "effort": config["effort"], "temperature": None, "temperatures": [],
        "provider": "offline-export", "updated_utc": now,
    })
    return manifest


def _validate_existing(run_dir: pathlib.Path, items_path: pathlib.Path,
                       config: dict, catalog: dict) -> dict | None:
    path = run_dir / "manifest.json"
    if not path.exists():
        if catalog:
            raise ValueError(f"{run_dir}: requests catalog exists without manifest")
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": run.RUN_CONTRACT, "items_sha256": run.sha256(items_path),
        "config_hash": run.digest(config), "protocol": PROTOCOL,
        "aggregation": AGGREGATION,
    }
    for key, wanted in expected.items():
        if manifest.get(key) != wanted:
            raise ValueError(f"{path}: immutable {key} mismatch; use a new run directory")
    if run.digest(manifest.get("config")) != manifest.get("config_hash"):
        raise ValueError(f"{path}: config_hash does not bind config")
    return manifest


def _canonical_export_row(call: dict) -> dict:
    row = {key: call[key] for key in (
        "schema_version", "call_id", "task", "item_id", "case_number",
        "split", "task_rank", "item_rank", "repeat_index", "cost_points",
        "cost_x", "stated", "protocol", "aggregation", "model", "config_hash",
        "prompt_sha256", "request_sha256", "stage", "request",
    )}
    row["custom_id"] = call["call_id"]
    return row


def export_batch(run_dir: pathlib.Path, output_path: pathlib.Path,
                 items_path: pathlib.Path, tasks: list[str], splits: list[str],
                 through_items: int, args: SimpleNamespace,
                 retry_ids_path: pathlib.Path | None = None) -> dict:
    """Create/extend a P4 catalog and export incomplete calls only."""
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite batch file {output_path}")
    if through_items < 1:
        raise ValueError("--through-items must be >= 1")
    run_dir.mkdir(parents=True, exist_ok=True)
    catalog = run.read_call_catalog(run_dir)
    items = legacy_plan.cumulative_items(
        items_path, tasks, splits, through_items, str(args.seed), catalog)
    if not items:
        raise ValueError("no items matched the requested task/split prefix")
    calls, config = build_call_plan(items, args)
    existing = _validate_existing(run_dir, items_path, config, catalog)
    planned = {row["call_id"]: row for row in calls}
    if set(catalog) - set(planned):
        raise ValueError("existing catalog is not a subset of the P4 plan")
    for call_id, old in catalog.items():
        if run.canonical_json(old) != run.canonical_json(planned[call_id]):
            raise ValueError(f"request identity {call_id} changed")
    allowed = run.read_retry_ids(retry_ids_path) if retry_ids_path else None
    if allowed is not None and allowed - set(planned):
        raise ValueError("retry list contains IDs outside the P4 plan")
    completed = run.read_completed(run_dir)
    missing = [row for row in calls if row["call_id"] not in completed
               and (allowed is None or row["call_id"] in allowed)]
    run.persist_call_catalog(run_dir, calls)
    manifest = _manifest_value(existing, items_path, config, calls,
                               through_items, tasks, splits)
    legacy_plan._write_manifest_atomic(run_dir, manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as fh:
        for call in missing:
            fh.write(json.dumps(_canonical_export_row(call), ensure_ascii=False,
                                sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return {"planned": len(calls), "completed": len(set(completed) & set(planned)),
            "exported": len(missing), "items": len({row["item_id"] for row in calls}),
            "levels": len(config["cost_grid"]), "config_hash": run.digest(config),
            "path": str(output_path)}


def _strict_parsed(result: dict, call: dict) -> dict:
    parsed = result.get("parsed", result.get("output"))
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    if not isinstance(parsed, dict):
        raise ValueError(f"{call['call_id']}: completed result needs parsed object")
    decision = parsed.get("decision")
    answer = parsed.get("answer")
    if decision not in ("answer", "refer"):
        raise ValueError(f"{call['call_id']}: invalid decision {decision!r}")
    if answer not in call["allowed_answers"]:
        raise ValueError(f"{call['call_id']}: invalid answer {answer!r}")
    out = {"decision": decision, "answer": answer}
    if call.get("stated"):
        probability = parsed.get("probability")
        if (not isinstance(probability, (int, float)) or isinstance(probability, bool)
                or not 0 <= float(probability) <= 1):
            raise ValueError(f"{call['call_id']}: invalid probability {probability!r}")
        out["probability"] = float(probability)
    return out


def import_results(run_dir: pathlib.Path, results_path: pathlib.Path) -> dict:
    """P4 equivalent of run.import_results, retaining decision and verdict."""
    catalog = run.read_call_catalog(run_dir)
    results = run.read_jsonl(results_path)
    completed = run.read_completed(run_dir)
    seen, actions = set(), []
    for result in results:
        call_id = result.get("call_id") or result.get("custom_id")
        if not call_id or call_id in seen or call_id not in catalog:
            raise ValueError(f"{results_path}: invalid/duplicate/unknown call_id {call_id!r}")
        seen.add(call_id)
        call = catalog[call_id]
        error = result.get("error")
        parsed = None if error else _strict_parsed(result, call)
        old = completed.get(call_id)
        if old is not None:
            if parsed is not None and run.canonical_json(old.get("parsed")) != run.canonical_json(parsed):
                raise ValueError(f"{call_id}: completed call cannot be overwritten")
            actions.append(("duplicate", call, result, old.get("parsed")))
        else:
            actions.append(("failed" if error else "completed", call, result, parsed))
    now = _utc_now()
    response_rows, ledger_rows = [], []
    counts = {"completed": 0, "failed": 0, "duplicate": 0}
    for status, call, result, parsed in actions:
        counts[status] += 1
        if status == "duplicate":
            continue
        event = {
            "schema_version": run.RUN_CONTRACT, "call_id": call["call_id"],
            "status": status, "task": call["task"], "item_id": call["item_id"],
            "task_rank": call["task_rank"], "repeat_index": call["repeat_index"],
            "cost_points": call["cost_points"], "cost_x": call["cost_x"],
            "protocol": PROTOCOL, "aggregation": AGGREGATION,
            "model": call["model"], "config_hash": call["config_hash"],
            "imported_utc": now, "error": result.get("error"),
            "response": result.get("response") if status == "failed" else None,
            "stop_reason": result.get("stop_reason"),
            "requested_utc": result.get("requested_utc"),
        }
        if status == "completed":
            event["parsed"] = parsed
            response_rows.append({
                "schema_version": run.RUN_CONTRACT, "call_id": call["call_id"],
                "item_id": call["item_id"], "task": call["task"],
                "case_number": call["case_number"], "protocol": PROTOCOL,
                "aggregation": AGGREGATION, "model": call["model"],
                "config_hash": call["config_hash"], "task_rank": call["task_rank"],
                "item_rank": call["item_rank"], "repeat_index": call["repeat_index"],
                "cost_points": call["cost_points"], "cost_x": call["cost_x"],
                "variant": call["variant"], "request": call["request"],
                "response": result.get("response"), "parsed": parsed, "error": None,
                "stop_reason": result.get("stop_reason"),
                "requested_utc": result.get("requested_utc"), "imported_utc": now,
            })
        ledger_rows.append(event)
    run.append_jsonl(run_dir / "responses.jsonl", response_rows)
    run.append_jsonl(run_dir / "ledger.jsonl", ledger_rows)
    counts["missing_after_import"] = len(set(catalog) - set(run.read_completed(run_dir)))
    return counts


def self_test(items_path: pathlib.Path) -> int:
    args = SimpleNamespace(model="self-test-model", max_tokens=4096,
                           thinking="unset", effort="medium", seed="pmcpa-bench",
                           condition="core")
    items = run.load_ranked_items(items_path, ["T1"], [], 2, args.seed)
    assert len(items) == 2, "self-test needs the first two T1 ranks"

    calls, config = build_call_plan(items, args)
    calls2, config2 = build_call_plan(items, args)
    assert run.digest(config) == run.digest(config2), "config hash unstable"
    assert [c["call_id"] for c in calls] == [c["call_id"] for c in calls2], \
        "call plan unstable"
    assert len(calls) == 2 * len(COST_GRID)
    assert len({c["call_id"] for c in calls}) == len(calls)
    assert all(len(c["call_id"]) <= 64 for c in calls)

    base = {"index": 0, "rendition": 0,
            "block_order": run.BLOCK_ORDERS[0], "temperature": None}
    for item in items:
        rows = [c for c in calls if c["item_id"] == item["item_id"]]
        assert sorted(r["cost_points"] for r in rows) == sorted(COST_GRID)
        p1 = run.request_params(item, "P1", base, args)
        for row in rows:
            req = row["request"]
            assert req["messages"] == p1["messages"], "user message diverged from P1"
            wanted = render_instruction(row["cost_points"], COST_X)
            assert req["system"].endswith(wanted), "system missing rendered instruction"
            assert "{c}" not in req["system"] and "{x}" not in req["system"]
            schema = req["output_config"]["format"]["schema"]
            assert list(schema["properties"]) == ["decision", "answer"], \
                "decision must precede answer"
            assert schema["required"] == ["decision", "answer"]
            assert schema["properties"]["answer"]["enum"] == list(run.ANSWERS[item["task"]])

    call = calls[0]
    good = _strict_parsed({"parsed": {"decision": "refer", "answer": call["allowed_answers"][0]}}, call)
    assert good == {"decision": "refer", "answer": call["allowed_answers"][0]}
    for bad in ({"answer": call["allowed_answers"][0]},
                {"decision": "abstain", "answer": call["allowed_answers"][0]},
                {"decision": "answer", "answer": "maybe"}):
        try:
            _strict_parsed({"parsed": bad}, call)
        except ValueError:
            pass
        else:
            raise AssertionError(f"parsed {bad!r} should have been rejected")

    hashes = {run.digest(config)}
    for condition in ("anchor", "dominance"):
        c_args = SimpleNamespace(**{**vars(args), "condition": condition})
        c_calls, c_config = build_call_plan(items, c_args)
        assert len(c_calls) == 2 * len(GRIDS[condition])
        assert sorted({c["cost_points"] for c in c_calls}) == sorted(GRIDS[condition])
        hashes.add(run.digest(c_config))
        for row in c_calls:
            assert row["request"]["system"].endswith(
                render_instruction(row["cost_points"], CONDITIONS[condition]["x"]))
    assert len(hashes) == 3, "conditions must hash differently"
    dom = build_call_plan(items, SimpleNamespace(**{**vars(args), "condition": "dominance"}))[0]
    gains = [r for r in dom if r["cost_points"] == -10]
    assert gains and all("gains 10 points" in r["request"]["system"]
                         and "loses -10" not in r["request"]["system"] for r in gains)
    losses = [r for r in dom if r["cost_points"] == 150]
    assert losses and all("loses 150 points" in r["request"]["system"] for r in losses)
    assert {r["call_id"].split("-")[4] for r in dom} == {"cg10", "c150"}

    print(f"p4_plan self-test PASS: {sorted(GRIDS)} conditions over {len(items)} items; "
          f"core config={run.digest(config)[:12]}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=pathlib.Path, default=DEFAULT_ITEMS)
    parser.add_argument("--condition", choices=sorted(GRIDS), default="core")
    parser.add_argument("--tasks", default="T1")
    parser.add_argument("--splits", default="")
    parser.add_argument("--through-items", type=int)
    parser.add_argument("--model")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--thinking", choices=("unset", "adaptive", "disabled"), default="unset")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--seed", default="pmcpa-bench")
    parser.add_argument("--run-dir", type=pathlib.Path)
    parser.add_argument("--export-batch", type=pathlib.Path)
    parser.add_argument("--import-results", type=pathlib.Path)
    parser.add_argument("--retry-ids", type=pathlib.Path)
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.self_test:
        return self_test(args.items)
    if args.import_results:
        if not args.run_dir:
            raise SystemExit("--import-results requires --run-dir")
        result = import_results(args.run_dir, args.import_results)
        print(f"imported: completed={result['completed']} failed={result['failed']} "
              f"duplicates={result['duplicate']} remaining={result['missing_after_import']}")
        return 0
    if not args.model or args.through_items is None:
        raise SystemExit("planning requires --model and --through-items")
    tasks = [value.strip() for value in args.tasks.split(",") if value.strip()]
    splits = [value.strip() for value in args.splits.split(",") if value.strip()]
    if set(tasks) - set(ACTIVE_TASKS) or not tasks:
        raise SystemExit("P4 supports only active T1/T2/T3")
    if args.export_batch:
        if not args.run_dir:
            raise SystemExit("--export-batch requires --run-dir")
        result = export_batch(args.run_dir, args.export_batch, args.items, tasks,
                              splits, args.through_items, args, args.retry_ids)
        print(f"planned={result['planned']} completed={result['completed']} "
              f"exported={result['exported']} items={result['items']} "
              f"levels={result['levels']}")
        print(f"config={result['config_hash']} -> {result['path']}")
        return 0
    ranked = run.load_ranked_items(args.items, tasks, splits,
                                   args.through_items, args.seed)
    calls, config = build_call_plan(ranked, args)
    cond = CONDITIONS[args.condition]
    print(f"DRY RUN: {len(calls)} P4 calls over {len(ranked)} items "
          f"(condition {args.condition}, grid {cond['grid']}, X={cond['x']})")
    print(f"aggregation={AGGREGATION} config={run.digest(config)}")
    print("No network call was made and nothing was written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
