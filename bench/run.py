"""Offline elicitation planner and durable result ledger.

    python3 bench/run.py                                      # DRY RUN (default)
    python3 bench/run.py --protocol P1 --through-items 3
    python3 bench/run.py --protocol P2 --through-items 3 --through-repeats 7 \
      --run-dir bench/runs/my-run --export-batch /tmp/requests.jsonl
    python3 bench/run.py --run-dir bench/runs/my-run \
      --import-results /tmp/results.jsonl

DRY RUN IS THE DEFAULT AND MAKES NO NETWORK CALL OR FILE WRITE. This module has
no live execution path: requests are exported for an external batch executor,
and normalized results are imported afterwards. ``--live`` is retained only
to fail closed for old command lines.

The cumulative horizons are ranks, not fresh samples. ``--through-items N``
means ranks 1..N *within every selected task*. ``--through-repeats K`` means
P2 repeats 1..K. Increasing either value produces stable existing call IDs and
exports only calls which do not already have a completed ledger entry.

Protocols (DESIGN.md §3):

  P1  verbalized -- one call per item; the model returns an answer and a
      stated probability in [0, 1], via structured output. The mentalist foil.

  P2  behaviourist -- K byte-identical requests per item at one fixed
      presentation and fixed model configuration. Confidence is the frequency
      of the modal answer. An optional single temperature is fixed across the
      run; presentation perturbations and temperature sweeps are outside this
      cumulative repeat ledger.

  P3  pooled stated confidence -- K byte-identical P1-style requests per item;
      oriented probabilities are combined by an equal-weight linear pool.
      Planned and imported by ``p3_plan.py``, not this runner.

  SP  selective prediction -- an offline risk--coverage score computed from
      P1/P2/P3 results (formerly presented as P4; the P4 label is reserved
      for a planned incentivized-deferral protocol). It is not a runner
      protocol and makes no provider calls.

Historical response-only runs retain their original identifiers: legacy P2
means stated confidence, legacy P1 means repeated verdicts, and legacy P3 means
the retired lottery/revealed-preference experiment. ``score.py`` applies that
mapping from the run contract without rewriting those archived files.

Batch interchange is intentionally provider-neutral. Each exported JSONL row
contains a stable ``custom_id``/``call_id`` and the canonical request object.
Each imported row must contain that ID plus a normalized ``parsed`` object (or
``output`` alias), and may carry a raw ``response`` and/or ``error`` receipt.

A run directory's identity is its creation config (``config_hash``), which
pins the sha256 of the planner code that created it. Growing a catalog's item
or repeat horizon after a reviewed code edit is a first-class operation, not a
workaround: the old and new code hashes must both be registered in
``bench/code_lineage.json`` (the reviewed lineage registry), and every stored
catalog row must re-render byte-identically -- same ``request_sha256`` and
``prompt_sha256`` -- under the exporting code before a single new call is
planned. The re-render proof is the load-bearing guarantee; the registry only
gates which hashes are eligible to attempt it. Every export against an
existing catalog (same-hash or lineage-crossing alike) runs the proof and
appends an auditable ``growth_events`` entry to the manifest; the run keeps
its original config and config_hash throughout.
"""

import argparse
import hashlib
import itertools
import json
import pathlib
import re
import sys
import time

BENCH = pathlib.Path(__file__).resolve().parent
DEFAULT_ITEMS = BENCH / "items.jsonl"
RUNS = BENCH / "runs"
# v2 is the first durable contract with the ordered active protocol namespace:
# P1 stated confidence, P2 repeated identical verdicts, P3 offline selective
# prediction. No v1 run is active; score.py retains an explicit archive map.
RUN_CONTRACT = "pmcpa.zero-provider.v2"

UNSELECTED_MODEL = "MODEL_NOT_SELECTED"
# Compatibility for the offline recall-probe planner. Provider exports still
# require an explicit --model in main().
DEFAULT_MODEL = UNSELECTED_MODEL

# Models that reject non-default sampling parameters (400). Prefix match.
# Models that reject `thinking: {"type": "adaptive"}` (measured 2026-08-02:
# haiku-4-5 400s on it -- adaptive thinking is a 5-family / 4.8+ feature).
# For these the thinking param is silently omitted, mirroring the sampling
# mechanism below; the manifest records what was actually sent.
NO_ADAPTIVE_THINKING = (
    "claude-haiku-4-5", "claude-sonnet-4-6", "claude-3", "claude-opus-4-1",
)

NO_SAMPLING_PARAMS = (
    "claude-opus-5", "claude-opus-4-7", "claude-opus-4-8",
    "claude-sonnet-5", "claude-fable-5", "claude-mythos-5",
    "gpt-5", "o1", "o3", "o4",   # OpenAI reasoning models: temperature rejected
)

ANSWERS = {
    "T1": ("breach", "no_breach"),
    # T2 is the active name; T1-triage remains readable for frozen v2 banks and
    # archived prompt artefacts during the rename transition.
    "T2": ("breach", "no_breach"),
    "T1-triage": ("breach", "no_breach"),
    "T3": ("upheld", "overturned"),
    "T4": ("in_scope", "outwith_scope"),
    # T5 (bench/T5_DESIGN.md): the model always answers the breach question; a
    # T5-cf-natural item's label is compliant_by_construction and scoring maps
    # it to the expected answer no_breach, reported SEPARATELY (design D9).
    "T5-material": ("breach", "no_breach"),
    "T5-cf-natural": ("breach", "no_breach"),
}

# T5 rendering lives in t5_generate.py (single source of truth for the T5
# system base, question and material/setting blocks); imported lazily below to
# avoid the circular import (t5_generate imports this module).
T5_TASKS = ("T5-material", "T5-cf-natural")

BLOCK_ORDERS = tuple(itertools.permutations(("clause", "metadata", "extract")))

SYSTEM_BASE = (
    "You are assessing pharmaceutical promotional material under the ABPI Code of Practice "
    "for the Pharmaceutical Industry, as the PMCPA Panel would.\n\n"
    "The Panel decides on the balance of probabilities. The complainant bears the burden of "
    "proving the matter complained of; an allegation that is plausible but unproven fails.\n\n"
    "You will be shown an extract from one case - the complaint, and where it is provided the "
    "respondent company's written response - together with limited case details. You will not "
    "be shown any ruling, and you cannot ask for more information. Decide on what you are given."
)

# T3 needs its own base: SYSTEM_BASE above is written for T1 and is FALSE for a
# T3 item, in the direction that matters. It says "You will not be shown any
# ruling" and asks the reader to decide "as the PMCPA Panel would", while all
# 316 T3 items quote a [PANEL RULING UNDER APPEAL] section (measured over
# items.jsonl: every T3 item carries complaint + response + panel_ruling
# provenance) and ask an Appeal Board question. A blinded reviewer on the
# 2026-08-06 decidability panel reported having to discard the framing
# consciously; a careless reader is pulled toward re-deciding the Panel's
# question instead of the appeal's -- see
# bench/review/audits/2026-08-06_t3_decidability_panel.md, "Incidental prompt
# defects found by reviewers". Same shape as T5's separate base
# (t5_generate.T5_SYSTEM_BASE), but it lives here because T3 items live in the
# main bank.
#
# Wording receipts, each checked rather than assumed:
#   "including the ruling under appeal" -- 246 of the 316 T3 items quote ONE
#   Panel ruling, 70 quote 2-17 (multi-clause reports, whose extract runs the
#   report's whole clause-by-clause sequence), so a bare "the Panel's ruling"
#   would misdescribe 70 of them.
#   The burden sentence is carried over from the T1 base unchanged because it
#   is the Appeal Board's own standard too, witnessed in the Constitution and
#   Procedure section 4 (Appeal Board) in all six scraped editions: "Rulings
#   are made on the basis that a complainant has the burden of proving their
#   complaint on the balance of probabilities" (para 4.3 in 2014/2015/2016/
#   2019/2021, renumbered 4.7 in 2024; data/code/constitution.jsonl).
#   The not-shown list is the structural truth of the task, not a hint: the
#   appeal stage has written submissions from both parties, additional material
#   filed with them, and a hearing at which both may appear (2024 edition paras
#   4.10-4.11; 4.6 in the earlier five), and the bank quotes none of it -- T3
#   may never quote appeal_* segments (DESIGN.md 5).
# Deliberately absent: any base rate, prior or hint about how often appeals
# succeed (that is the quantity the benchmark measures), and any nudge to hedge
# or to commit (that is what the protocol block below elicits).
T3_SYSTEM_BASE = (
    "You are predicting the outcome of an appeal under the ABPI Code of Practice for the "
    "Pharmaceutical Industry. The PMCPA Panel has already ruled on the clause under test and a "
    "party has appealed that ruling to the Appeal Board. Your task is to say what the Appeal "
    "Board did with the Panel's ruling, not to decide the clause yourself at first instance.\n\n"
    "The Appeal Board decides on the balance of probabilities. The complainant bears the burden "
    "of proving the matter complained of; an allegation that is plausible but unproven fails.\n\n"
    "You will be shown the clause under test, limited case details, and an extract from one "
    "case - the complaint, where it is provided the respondent company's written response, and "
    "the Panel's ruling, including the ruling under appeal. You will not be shown anything from "
    "the appeal itself - the parties' written appeal submissions, any material put to the Appeal "
    "Board at that stage, or the hearing - and you cannot ask for more information. Decide on "
    "what you are given."
)

PROTOCOL_INSTRUCTION = {
    "P1": (
        "Reply with your verdict and the probability that your verdict is the one the "
        "adjudicator actually reached. The probability is a number between 0 and 1: use 0.5 "
        "when you would be guessing and values near 1 only when you would be surprised to be "
        "wrong. Do not explain."
    ),
    "P2": (
        "Reply with your verdict and nothing else. Do not explain, qualify or hedge; "
        "the answer field is the entire response."
    ),
}


def question_body(item):
    """The question itself: no QUESTION header, no answer-format line.

    Split from :func:`question` so task wording has one source of truth.
    """
    task = item["task"]
    clause = item["inputs"]["clause_ref"].get("clause")
    year = item["inputs"]["clause_ref"].get("code_year")
    year_txt = f"the {year} Code" if year else "the Code"
    if task in ("T1", "T2", "T1-triage"):
        return (f"Did the Panel rule a breach of Clause {clause} of {year_txt} "
                f"in this case?")
    if task == "T3":
        # KNOWN AMBIGUITY, not fixed here: where the Panel ruled TWICE on one
        # clause in different regards (e.g. no breach in one regard, breach in
        # another), this sentence names one ruling and the reader cannot tell
        # which of the two is under appeal. The 2026-08-06 decidability panel
        # hit it on an AUTH/2647-style case; DEFECTS R18 carries the register
        # entry, including two T3 labels that are wrong for the same reason.
        # The fix is blocked on L2 representing dual rulings (R18 option (c)) --
        # it is not a wording change here, so do not attempt one.
        meta = item["inputs"]["metadata_shown"]
        ruling = "a breach" if meta.get("panel_ruling_for_clause") == "breach" else "no breach"
        who = {"respondent": "The respondent company", "complainant": "The complainant",
               "both": "Both parties"}.get(meta.get("appellant"), "A party")
        return (f"The Panel ruled {ruling} of Clause {clause} of {year_txt}. "
                f"{who} appealed that ruling to the Appeal Board.\n"
                f"Did the Appeal Board uphold the Panel's ruling, or overturn it?")
    if task == "T4":
        return "Is the matter complained of within the scope of the ABPI Code at all?"
    if task in T5_TASKS:
        import t5_generate as t5
        return t5.t5_question_body(item)
    raise ValueError(f"unknown task {task!r}")


def answer_line(item):
    """How to format the verdict. T4's is not a bare either/or, hence the table."""
    task = item["task"]
    if task in ("T1", "T2", "T1-triage") or task in T5_TASKS:
        return "Answer 'breach' or 'no_breach'."
    if task == "T3":
        return "Answer 'upheld' or 'overturned'."
    if task == "T4":
        return ("Answer 'in_scope', or 'outwith_scope' if the Panel would rule that the complaint "
                "falls outwith the scope of the Code.")
    raise ValueError(f"unknown task {task!r}")


def question(item):
    return f"QUESTION\n{question_body(item)}\n{answer_line(item)}"


def clause_block(item):
    ref = item["inputs"]["clause_ref"]
    if ref.get("clause") is None:
        return None  # T4: no clause under test
    year = ref.get("code_year")
    lines = [f"CLAUSE UNDER TEST\nClause {ref['clause']}" + (f" of the {year} ABPI Code" if year else "")]
    if ref.get("clause_text"):
        lines.append(ref["clause_text"])
    else:
        lines.append("(The text of this clause is not available to you; rely on your knowledge "
                     "of the Code as it stood in that year.)")
    return "\n".join(lines)


def metadata_block(item):
    meta = item["inputs"]["metadata_shown"]
    labels = [
        ("respondent", "Respondent company"),
        ("code_year", "Applicable Code year"),
        ("date_received", "Complaint received"),
        ("complainant_category", "Complainant type"),
        ("complainant_anonymous", "Complainant anonymous"),
        ("complainant_contactable", "Complainant contactable"),
    ]
    lines = ["CASE DETAILS"]
    for key, label in labels:
        value = meta.get(key)
        if value is None:
            value = "not recorded"
        elif isinstance(value, bool):
            value = "yes" if value else "no"
        lines.append(f"{label}: {value}")
    return "\n".join(lines)


def extract_block(item, rendition_index):
    if rendition_index == 0:
        text = item["inputs"]["extract_text"]
    else:
        text = item["inputs"]["renditions"][rendition_index - 1]["extract_text"]
    return f"EXTRACT FROM THE CASE\n{text}"


def build_prompt(item, protocol, variant):
    """Return (system, user). Tags, labels and the case number never appear."""
    instruction = PROTOCOL_INSTRUCTION[protocol]
    if item["task"] in T5_TASKS:
        import t5_generate as t5
        system = f"{t5.T5_SYSTEM_BASE}\n\n{instruction}"
        blocks = {
            "clause": clause_block(item),
            "metadata": t5.t5_setting_block(item),
            "extract": t5.t5_material_block(item, variant["rendition"]),
        }
    else:
        # T3 is served under its own base (see T3_SYSTEM_BASE); active T1/T2
        # keep SYSTEM_BASE. Legacy T4 remains renderable for old artefacts.
        # Reading the base as a module global here is what lets
        # t5_generate.register_with_run swap it in memory.
        base = T3_SYSTEM_BASE if item["task"] == "T3" else SYSTEM_BASE
        system = f"{base}\n\n{instruction}"
        blocks = {
            "clause": clause_block(item),
            "metadata": metadata_block(item),
            "extract": extract_block(item, variant["rendition"]),
        }
    ordered = [blocks[name] for name in variant["block_order"] if blocks[name]]
    return system, "\n\n".join(ordered + [question(item)])


def output_schema(item, protocol):
    props = {"answer": {"type": "string", "enum": list(ANSWERS[item["task"]])}}
    required = ["answer"]
    if protocol == "P1":
        # No minimum/maximum: structured outputs reject numeric constraints.
        # The range is stated in the prompt and enforced when parsing.
        props["probability"] = {"type": "number", "description": "Probability between 0 and 1 that the answer is correct."}
        required.append("probability")
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def effective_orders(item):
    """Block orders that actually produce different prompts for this item.

    T4 has no clause block, so permutations differing only in where the absent
    block sits render identically. Counting those as perturbations would inflate
    apparent agreement in historical perturbation runs, so they are collapsed
    here for archive tooling.
    """
    present = {"metadata", "extract"}
    if clause_block(item) is not None:
        present.add("clause")
    seen, out = set(), []
    for bo in BLOCK_ORDERS:
        key = tuple(n for n in bo if n in present)
        if key not in seen:
            seen.add(key)
            out.append(bo)
    return tuple(out)


def plan_variants(item, protocol, k, seed, temperatures):
    """Deterministic call plan. Same item + cumulative horizon -> same prefix.

    P2 deliberately emits byte-identical requests. ``index`` is ledger
    metadata only and never enters :func:`build_prompt` or request parameters.
    This makes a later K=10 plan a strict superset of an earlier K=7 plan.
    """
    if protocol == "P1":
        return [{"index": 0, "rendition": 0, "block_order": BLOCK_ORDERS[0], "temperature": None}]
    if protocol != "P2":
        raise ValueError(f"unknown active runner protocol {protocol!r}")
    if temperatures:
        raise ValueError("P2 cumulative repeats require one fixed configuration; "
                         "temperature sweeps belong in a separate run")
    base = {"rendition": 0, "block_order": BLOCK_ORDERS[0], "temperature": None}
    return [{"index": i, "repeat_index": i + 1, **base} for i in range(k)]


def request_params(item, protocol, variant, args):
    schema = output_schema(item, protocol)
    if protocol == "P1" and getattr(args, "rationale", False):
        # rationale FIRST in the schema so it is generated before the verdict
        # (the point: does articulating reasoning change the stated confidence?)
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["rationale"] + schema["required"],
                  "properties": {"rationale": {
                      "type": "string",
                      "description": "Brief reasoning, 2-4 sentences, before deciding."},
                      **schema["properties"]}}
    params = {
        "model": args.model,
        "max_tokens": args.max_tokens,
        "system": None,
        "messages": None,
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    system, user = build_prompt(item, protocol, variant)
    if protocol == "P1" and getattr(args, "rationale", False):
        system = system.replace(
            "Do not explain.",
            "First give a brief rationale (2-4 sentences), then your verdict and probability.")
    params["system"] = system
    params["messages"] = [{"role": "user", "content": user}]
    if args.thinking == "adaptive":
        params["thinking"] = {"type": "adaptive"}
    elif args.thinking == "disabled":
        params["thinking"] = {"type": "disabled"}
    if args.effort:
        params["output_config"]["effort"] = args.effort
    if variant["temperature"] is not None:
        params["temperature"] = variant["temperature"]
    return params


def parse_response(payload, item, protocol):
    """Pull the active protocol's structured answer object from a response."""
    text = None
    for block in payload.get("content") or []:
        if block.get("type") == "text":
            text = block.get("text")
            break
    if text is None:
        return None, "no text block in response"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"response is not JSON: {exc}"
    answer = data.get("answer")
    if answer not in ANSWERS[item["task"]]:
        return None, f"answer {answer!r} not in {ANSWERS[item['task']]}"
    parsed = {"answer": answer}
    if protocol == "P1":
        prob = data.get("probability")
        if not isinstance(prob, (int, float)) or isinstance(prob, bool):
            return None, f"probability {prob!r} is not a number"
        if not 0.0 <= float(prob) <= 1.0:
            return None, f"probability {prob} outside [0, 1]"
        parsed["probability"] = float(prob)
    return parsed, None


def load_items(path, tasks, splits, limit):
    items = []
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if tasks and item["task"] not in tasks:
                continue
            if splits and item["split"] not in splits:
                continue
            items.append(item)
    return items[:limit] if limit else items


def sha256(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(value):
    """Canonical UTF-8 JSON bytes used for every durable identity."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def task_order_key(task):
    # T1-triage is the frozen-bank spelling of active T2. Keep the aliases
    # adjacent but distinct in the impossible event that a transition bank
    # contains both.
    order = {"T1": 0, "T2": 1, "T1-triage": 2, "T3": 3}
    return (order.get(task, 100), task)


def _stable_sort_key(seed, namespace, value):
    raw = f"{seed}\0{namespace}\0".encode("utf-8") + canonical_json(value)
    return (hashlib.blake2b(raw, digest_size=16).hexdigest(), canonical_json(value))


def complaint_pair_key(item):
    """Identity shared by exact T1/T2 information-set counterparts.

    The response-bearing T1 item and complaint-only T2 item have different
    item IDs and extracts. Their complaint provenance is byte-identical. The
    remaining fields prevent distinct clause occurrences from being paired by
    case number alone.
    """
    ref = item["inputs"]["clause_ref"]
    complaint = tuple(
        (p.get("file"), p.get("pane"), p.get("char_start"), p.get("char_end"),
         p.get("text_sha256"))
        for p in item["inputs"].get("extract_provenance", [])
        if p.get("kind") == "complaint"
    )
    # Ground truth is deliberately absent: board order and pair detection are
    # functions of pre-outcome identity/provenance only.
    return (item["case_number"], ref.get("clause"), ref.get("code_year"), complaint)


def case_round_robin(items, seed, namespace):
    """One deterministic item per case before taking a second from any case."""
    by_case = {}
    for item in items:
        by_case.setdefault(item["case_number"], []).append(item)
    cases = sorted(by_case, key=lambda c: _stable_sort_key(seed, namespace + ":case", c))
    for case in cases:
        by_case[case].sort(
            key=lambda i: _stable_sort_key(seed, namespace + ":item", i["item_id"]))
    out = []
    depth = 0
    while True:
        advanced = False
        for case in cases:
            if depth < len(by_case[case]):
                out.append(by_case[case][depth])
                advanced = True
        if not advanced:
            return out
        depth += 1


def paired_task_round_robin(items, common_keys, seed):
    """Rank one T1/T2 task while preserving the shared-pair order.

    Each task gets its own whole-task round robin, so task-specific cases and
    clauses can enter any cumulative prefix.  Both tasks nevertheless use the
    same outcome-blind case order and complaint-pair order.  Common items come
    before task-specific items *within their case*; this keeps the common-pair
    subsequence identical after the two independently ranked boards are
    intersected, without front-loading all common items across the bank.
    """
    by_case = {}
    for item in items:
        by_case.setdefault(item["case_number"], []).append(item)
    cases = sorted(
        by_case,
        key=lambda case: _stable_sort_key(seed, "T1-T2:case", case),
    )
    for case in cases:
        by_case[case].sort(key=lambda item: (
            complaint_pair_key(item) not in common_keys,
            _stable_sort_key(
                seed, "T1-T2:complaint-pair", complaint_pair_key(item)),
        ))
    out = []
    depth = 0
    while True:
        advanced = False
        for case in cases:
            if depth < len(by_case[case]):
                out.append(by_case[case][depth])
                advanced = True
        if not advanced:
            return out
        depth += 1


def canonical_task_ranks(items, seed):
    """Return independent, deterministic, 1-based ranks within each task.

    T1 and T2 are whole-task case round robins.  Exact counterparts therefore
    need not have the same absolute rank, but their relative order is shared so
    an analysis can intersect any two cumulative task prefixes exactly.
    """
    ids = [item["item_id"] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("item bank contains duplicate item_id values")
    by_task = {}
    for item in items:
        by_task.setdefault(item["task"], []).append(item)

    ranks = {}
    secondary = "T2" if "T2" in by_task else (
        "T1-triage" if "T1-triage" in by_task else None)
    paired_tasks = {"T1", secondary} if secondary and "T1" in by_task else set()
    if paired_tasks:
        maps = {}
        for task in ("T1", secondary):
            mapping = {}
            for item in by_task[task]:
                key = complaint_pair_key(item)
                if key in mapping:
                    raise ValueError(f"ambiguous {task} counterpart key in {item['case_number']}")
                mapping[key] = item
            maps[task] = mapping
        common = set(maps["T1"]) & set(maps[secondary])
        for task in ("T1", secondary):
            ordered = paired_task_round_robin(maps[task].values(), common, seed)
            for rank, item in enumerate(ordered, 1):
                ranks[item["item_id"]] = rank

    for task, task_items in by_task.items():
        if task in paired_tasks:
            continue
        ordered = case_round_robin(task_items, seed, f"{task}:all")
        for rank, item in enumerate(ordered, 1):
            ranks[item["item_id"]] = rank
    return ranks


def load_ranked_items(path, tasks, splits, through_items, seed):
    """Rank the whole bank first, then filter, so ranks never depend on a CLI filter."""
    all_items = load_items(path, [], [], 0)
    ranks = canonical_task_ranks(all_items, seed)
    selected = []
    for item in all_items:
        rank = ranks[item["item_id"]]
        if tasks and item["task"] not in tasks:
            continue
        if splits and item["split"] not in splits:
            continue
        if through_items and rank > through_items:
            continue
        selected.append({**item, "_task_rank": rank})
    selected.sort(key=lambda i: (i["_task_rank"], task_order_key(i["task"]),
                                 i["item_id"]))
    return selected


def model_config(args, protocol):
    return {
        "contract": RUN_CONTRACT,
        # A code change cannot silently reuse a durable run directory. This is
        # intentionally broader than a hand-maintained prompt-version string:
        # after the first paid call, even a seemingly harmless runner edit must
        # be reviewed and started under a new immutable config hash.
        "runner_sha256": sha256(pathlib.Path(__file__).resolve()),
        "protocol": protocol,
        "model": args.model,
        "max_tokens": args.max_tokens,
        "thinking": args.thinking,
        "rationale": bool(getattr(args, "rationale", False)),
        "effort": args.effort or None,
        "temperature": getattr(args, "temperature", None),
        "seed": str(args.seed),
    }


def build_call_plan(items, protocol, through_repeats, args, config=None):
    """Build request-bearing call records, with identities stable under top-up.

    ``config`` may be an existing run's frozen creation config (see
    :func:`plan_for_run_dir`): planning then keeps that run's identity while
    requests are rendered by the current code. When omitted, the plan carries
    the current-code config.
    """
    config = dict(config) if config is not None else model_config(args, protocol)
    config_hash = digest(config)
    calls = []
    for item in items:
        k = through_repeats if protocol == "P2" else 1
        variants = plan_variants(item, protocol, k, args.seed, [])
        temperature = getattr(args, "temperature", None)
        if temperature is not None:
            variants = [{**variant, "temperature": temperature} for variant in variants]
        for variant in variants:
            request = request_params(item, protocol, variant, args)
            repeat_index = variant.get("repeat_index") if protocol == "P2" else 1
            prompt_hash = digest({"system": request["system"],
                                  "messages": request["messages"]})
            request_hash = hashlib.sha256(canonical_json(request)).hexdigest()
            identity = {
                "contract": RUN_CONTRACT,
                "task": item["task"],
                "task_rank": item["_task_rank"],
                "item_id": item["item_id"],
                "repeat_index": repeat_index,
                "protocol": protocol,
                "model": args.model,
                "config_hash": config_hash,
                "stage": variant.get("stage", "verdict"),
                "c": variant.get("c"),
                "request_sha256": request_hash,
            }
            safe_task = re.sub(r"[^A-Za-z0-9]+", "-", item["task"]).strip("-").lower()
            call_id = (f"call-{safe_task}-{item['_task_rank']:06d}-"
                       f"r{repeat_index:03d}-{digest(identity)[:20]}")
            calls.append({
                "schema_version": RUN_CONTRACT,
                "call_id": call_id,
                "task": item["task"],
                "item_id": item["item_id"],
                "case_number": item["case_number"],
                "split": item["split"],
                "task_rank": item["_task_rank"],
                "item_rank": item["_task_rank"],
                "repeat_index": repeat_index,
                "protocol": protocol,
                "model": args.model,
                "config_hash": config_hash,
                "prompt_sha256": prompt_hash,
                "request_sha256": request_hash,
                "stage": variant.get("stage", "verdict"),
                "variant": {**variant, "block_order": list(variant["block_order"])},
                "allowed_answers": list(ANSWERS[item["task"]]),
                "request": request,
            })
    calls.sort(key=lambda c: (c["task_rank"], task_order_key(c["task"]),
                              c["repeat_index"], c["call_id"]))
    ids = [call["call_id"] for call in calls]
    if len(ids) != len(set(ids)):
        raise AssertionError("call identity collision")
    return calls, config


def read_jsonl(path):
    rows = []
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return rows


def read_call_catalog(run_dir):
    path = pathlib.Path(run_dir) / "requests.jsonl"
    if not path.exists():
        return {}
    out = {}
    for row in read_jsonl(path):
        call_id = row.get("call_id")
        if not call_id:
            raise ValueError(f"{path}: request row without call_id")
        if call_id in out and canonical_json(out[call_id]) != canonical_json(row):
            raise ValueError(f"{path}: conflicting duplicate {call_id}")
        out[call_id] = row
    return out


def read_completed(run_dir):
    """Completed calls are terminal; merge the ledger and scorer-compatible file."""
    run_dir = pathlib.Path(run_dir)
    completed = {}
    responses = run_dir / "responses.jsonl"
    if responses.exists():
        for row in read_jsonl(responses):
            if row.get("call_id") and row.get("parsed") is not None and not row.get("error"):
                completed[row["call_id"]] = row
    ledger = run_dir / "ledger.jsonl"
    if ledger.exists():
        for event in read_jsonl(ledger):
            if event.get("status") == "completed":
                completed.setdefault(event["call_id"], event)
    return completed


def append_jsonl(path, rows):
    if not rows:
        return
    with pathlib.Path(path).open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()


# --- Code-lineage growth: registry gate + behavioral-identity proof --------
#
# bench/code_lineage.json is the reviewed registry of planner-code editions.
# It exists so that a documented, request-identical code edit does not strand
# every active run directory behind an immutable config_hash: growth across a
# registered hash pair is permitted if and only if the whole existing catalog
# re-renders byte-identically under the exporting code. Anything else refuses
# exactly as before. p3_plan.py and p4_plan.py share this machinery.

CODE_LINEAGE_PATH = BENCH / "code_lineage.json"
# Maps a config key that pins code identity to its registry file key. run.py
# runs bind only their own bytes; planners add their planner_sha256 key.
RUNNER_LINEAGE_KEYS = {"runner_sha256": "bench/run.py"}


def load_code_lineage(path=None):
    """Reviewed {file: set-of-sha256} registry; underscore keys are metadata."""
    path = pathlib.Path(path) if path else CODE_LINEAGE_PATH
    registry = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for file_key, entries in registry.items():
        if file_key.startswith("_"):
            continue
        hashes = []
        for entry in entries:
            value = entry.get("sha256")
            if not (isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)):
                raise ValueError(f"{path}: {file_key}: invalid sha256 {value!r}")
            if not entry.get("basis") or not entry.get("note"):
                raise ValueError(f"{path}: {file_key}: {value[:12]} lacks basis/note")
            hashes.append(value)
        if len(hashes) != len(set(hashes)):
            raise ValueError(f"{path}: {file_key}: duplicate sha256 entries")
        out[file_key] = set(hashes)
    return out


def reconcile_frozen_config(manifest_path, current_config, hash_keys,
                            lineage_path=None):
    """Admit an existing run for growth planning under its creation config.

    Returns ``(frozen_config, code_sha256_used, crossed)``. The frozen config
    is what planning must proceed with -- the run's identity never changes.
    ``code_sha256_used`` records, per registry file, the hash of the code
    actually performing this export. A config difference in anything other
    than the lineage-tracked code-hash keys, or a hash pair not registered in
    bench/code_lineage.json, refuses with the same immutable-mismatch error
    as before. The caller must still prove behavioral identity with
    :func:`verify_catalog_rerender` before trusting the admission.
    """
    manifest_path = pathlib.Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frozen = manifest.get("config")
    if not isinstance(frozen, dict) or digest(frozen) != manifest.get("config_hash"):
        raise ValueError(f"{manifest_path}: config_hash does not bind config")
    code_used = {file_key: current_config.get(key)
                 for key, file_key in hash_keys.items()}
    if digest(current_config) == manifest["config_hash"]:
        return frozen, code_used, False
    diffs = {key for key in set(frozen) | set(current_config)
             if frozen.get(key) != current_config.get(key)}
    if diffs - set(hash_keys):
        raise ValueError(f"{manifest_path}: immutable config_hash mismatch; "
                         f"use a new run directory (fields beyond the code "
                         f"lineage differ: {', '.join(sorted(diffs - set(hash_keys)))})")
    try:
        lineage = load_code_lineage(lineage_path)
    except FileNotFoundError as exc:
        raise ValueError(f"{manifest_path}: immutable config_hash mismatch; "
                         f"use a new run directory (no code lineage registry "
                         f"at {lineage_path or CODE_LINEAGE_PATH})") from exc
    for key in sorted(diffs):
        file_key = hash_keys[key]
        known = lineage.get(file_key, set())
        for role, value in (("recorded", frozen.get(key)),
                            ("current", current_config.get(key))):
            if value not in known:
                raise ValueError(
                    f"{manifest_path}: immutable config_hash mismatch; use a "
                    f"new run directory ({file_key} {role} sha256 {value} is "
                    f"not registered in bench/code_lineage.json)")
    return frozen, code_used, True


def verify_catalog_rerender(catalog, items_by_id, render_request):
    """The load-bearing growth proof: every stored catalog row must re-render
    byte-identically (request_sha256 AND prompt_sha256) under the exporting
    code. Any mismatch refuses, naming the offending call_id. Returns the
    number of verified rows."""
    for call_id in sorted(catalog):
        row = catalog[call_id]
        item = items_by_id.get(row.get("item_id"))
        if item is None:
            raise ValueError(f"growth re-render refused at {call_id}: item "
                             f"{row.get('item_id')!r} is not in the item bank")
        request = render_request(row, item)
        prompt_hash = digest({"system": request["system"],
                              "messages": request["messages"]})
        request_hash = digest(request)
        if (request_hash != row.get("request_sha256")
                or prompt_hash != row.get("prompt_sha256")):
            raise ValueError(
                f"growth re-render refused at {call_id}: stored "
                f"request_sha256={row.get('request_sha256')} "
                f"prompt_sha256={row.get('prompt_sha256')} but current code "
                f"renders {request_hash} / {prompt_hash}")
    return len(catalog)


def lineage_note(frozen_config, current_config, hash_keys, crossed):
    """One audit phrase shared by every planner's growth events."""
    if not crossed:
        return "same-code growth"
    return "lineage crossing " + " ".join(
        f"{file_key} {frozen_config.get(key)} -> {current_config.get(key)}"
        for key, file_key in sorted(hash_keys.items())
        if frozen_config.get(key) != current_config.get(key))


def growth_event_value(code_sha256_used, verified_rows, note):
    """Uniform auditable manifest event for any export against an existing
    catalog, whether or not a lineage hash was crossed."""
    return {
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "code_sha256_used": dict(sorted(code_sha256_used.items())),
        "verified_rows": int(verified_rows),
        "note": note,
    }


def plan_for_run_dir(run_dir, items, protocol, through_repeats, args,
                     items_path, note="", lineage_path=None):
    """Build the call plan for a possibly pre-existing run directory.

    A fresh directory plans under the current-code config. An existing
    directory plans under its frozen creation config: the current code must be
    the creation code or a bench/code_lineage.json-registered successor, and
    EVERY stored catalog row (including rows outside the current task/split
    filter) must re-render byte-identically first. Returns
    ``(calls, config, growth_event)``; growth_event is None only for a fresh
    directory.
    """
    run_dir = pathlib.Path(run_dir)
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        if read_call_catalog(run_dir):
            raise ValueError(f"{run_dir}: requests catalog exists without manifest")
        calls, config = build_call_plan(items, protocol, through_repeats, args)
        return calls, config, None
    current_config = model_config(args, protocol)
    frozen, code_used, crossed = reconcile_frozen_config(
        manifest_path, current_config, RUNNER_LINEAGE_KEYS, lineage_path)
    catalog = read_call_catalog(run_dir)
    items_by_id = {row["item_id"]: row for row in load_items(items_path, [], [], 0)}
    verified = verify_catalog_rerender(
        catalog, items_by_id,
        lambda row, item: request_params(item, row["protocol"], row["variant"], args))
    calls, config = build_call_plan(items, protocol, through_repeats, args,
                                    config=frozen)
    detail = lineage_note(frozen, current_config, RUNNER_LINEAGE_KEYS, crossed)
    event = growth_event_value(code_used, verified,
                               f"{detail}; {note}" if note else detail)
    return calls, config, event


def _write_manifest(run_dir, args, items_path, config, calls, through_items,
                    through_repeats, tasks, splits, growth_event=None):
    run_dir = pathlib.Path(run_dir)
    path = run_dir / "manifest.json"
    config_hash = digest(config)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for key, wanted in (("contract", RUN_CONTRACT),
                            ("items_sha256", sha256(items_path)),
                            ("config_hash", config_hash)):
            if manifest.get(key) != wanted:
                raise ValueError(f"{path}: immutable {key} mismatch; use a new run directory")
    else:
        manifest = {
            "contract": RUN_CONTRACT,
            "created_utc": now,
            "items_path": str(pathlib.Path(items_path).resolve()),
            "items_sha256": sha256(items_path),
            "config": config,
            "config_hash": config_hash,
            "protocol": config["protocol"],
            "model": config["model"],
            "python": sys.version.split()[0],
            "max_through_items_by_task": {},
            "tasks_filter_history": [],
            "splits_filter_history": [],
        }
    # A run directory is a cumulative union. The latest export can select only
    # one task or a higher prefix, so manifest counts/horizons must describe the
    # existing catalog plus this plan, not merely the latest CLI filter.
    catalog = read_call_catalog(run_dir)
    for call in calls:
        old = catalog.get(call["call_id"])
        if old is not None and canonical_json(old) != canonical_json(call):
            raise ValueError(f"request identity {call['call_id']} changed")
        catalog[call["call_id"]] = call
    cumulative_calls = list(catalog.values())

    horizons = manifest.setdefault("max_through_items_by_task", {})
    for task in sorted({call["task"] for call in cumulative_calls}, key=task_order_key):
        actual_horizon = max(call["task_rank"] for call in cumulative_calls
                             if call["task"] == task)
        horizons[task] = max(int(horizons.get(task, 0)), int(actual_horizon))
    manifest["through_items"] = max(horizons.values(), default=0)
    manifest["requested_through_items"] = int(through_items)
    manifest["through_repeats"] = max(int(manifest.get("through_repeats", 0)),
                                      int(through_repeats))
    manifest["k"] = manifest["through_repeats"] if config["protocol"] == "P2" else 1
    task_history = manifest.setdefault("tasks_filter_history", [])
    chosen_tasks = list(tasks) or sorted({call["task"] for call in calls}, key=task_order_key)
    if chosen_tasks not in task_history:
        task_history.append(chosen_tasks)
    split_history = manifest.setdefault("splits_filter_history", [])
    chosen_splits = list(splits)
    if chosen_splits not in split_history:
        split_history.append(chosen_splits)
    manifest["n_items_planned"] = len({call["item_id"] for call in cumulative_calls})
    manifest["n_calls_planned"] = len(cumulative_calls)
    # Compatibility aliases consumed by the existing scorer and archived-run
    # tooling. They describe the latest cumulative plan, not a fresh batch.
    manifest["n_items"] = manifest["n_items_planned"]
    manifest["n_calls"] = manifest["n_calls_planned"]
    manifest["tasks_filter"] = chosen_tasks
    manifest["splits_filter"] = list(splits)
    manifest["seed"] = config["seed"]
    manifest["max_tokens"] = config["max_tokens"]
    manifest["thinking"] = config["thinking"]
    manifest["rationale"] = config["rationale"]
    manifest["effort"] = config["effort"]
    manifest["temperature"] = config.get("temperature")
    manifest["temperatures"] = ([config["temperature"]]
                                if config.get("temperature") is not None else [])
    manifest["provider"] = "offline-export"
    manifest["updated_utc"] = now
    if growth_event is not None:
        manifest.setdefault("growth_events", []).append(growth_event)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return manifest


def persist_call_catalog(run_dir, calls):
    run_dir = pathlib.Path(run_dir)
    catalog = read_call_catalog(run_dir)
    new = []
    for call in calls:
        old = catalog.get(call["call_id"])
        if old is not None:
            if canonical_json(old) != canonical_json(call):
                raise ValueError(f"request identity {call['call_id']} changed")
            continue
        new.append(call)
        catalog[call["call_id"]] = call
    append_jsonl(run_dir / "requests.jsonl", new)
    return catalog, new


def read_retry_ids(path):
    ids = []
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                row = json.loads(line)
                call_id = row.get("call_id") or row.get("custom_id")
            else:
                call_id = line
            if not call_id:
                raise ValueError(f"{path}:{lineno}: missing call_id")
            ids.append(call_id)
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate retry IDs")
    return set(ids)


def export_batch(run_dir, output_path, calls, args, items_path, config,
                 through_items, through_repeats, tasks, splits, retry_ids_path=None,
                 growth_event=None):
    """Persist the plan and export only calls lacking a completed receipt."""
    run_dir = pathlib.Path(run_dir)
    output_path = pathlib.Path(output_path)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite batch file {output_path}")
    allowed = read_retry_ids(retry_ids_path) if retry_ids_path else None
    if allowed is not None:
        unknown = allowed - {call["call_id"] for call in calls}
        if unknown:
            raise ValueError("retry list contains IDs outside the current cumulative plan: " +
                             ", ".join(sorted(unknown)[:5]))
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(run_dir, args, items_path, config, calls, through_items,
                    through_repeats, tasks, splits, growth_event)
    persist_call_catalog(run_dir, calls)
    completed = read_completed(run_dir)
    missing = [call for call in calls
               if call["call_id"] not in completed and
               (allowed is None or call["call_id"] in allowed)]
    with output_path.open("x", encoding="utf-8") as fh:
        for call in missing:
            row = {key: call[key] for key in (
                "schema_version", "call_id", "task", "item_id", "case_number",
                "split", "task_rank", "item_rank", "repeat_index", "protocol",
                "model", "config_hash", "prompt_sha256", "request_sha256",
                "stage", "request")}
            row["custom_id"] = call["call_id"]
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"planned": len(calls), "completed": len(set(completed) &
                                                      {c["call_id"] for c in calls}),
            "exported": len(missing), "path": str(output_path)}


def _validated_parsed(result, call):
    parsed = result.get("parsed", result.get("output"))
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{call['call_id']}: parsed/output string is not JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{call['call_id']}: completed result needs parsed/output object")
    answer = parsed.get("answer")
    if answer not in call["allowed_answers"]:
        raise ValueError(f"{call['call_id']}: invalid answer {answer!r}")
    out = {"answer": answer}
    if call["protocol"] == "P1":
        probability = parsed.get("probability")
        if (not isinstance(probability, (int, float)) or isinstance(probability, bool)
                or not 0 <= float(probability) <= 1):
            raise ValueError(f"{call['call_id']}: invalid probability {probability!r}")
        out["probability"] = float(probability)
    return out


def import_results(run_dir, results_path):
    """Append normalized receipts; completed calls are immutable and terminal."""
    run_dir = pathlib.Path(run_dir)
    catalog = read_call_catalog(run_dir)
    if not catalog:
        raise ValueError(f"{run_dir}: no requests.jsonl; export a batch first")
    results = read_jsonl(results_path)
    seen = set()
    completed = read_completed(run_dir)
    actions = []
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for result in results:
        call_id = result.get("call_id") or result.get("custom_id")
        if not call_id:
            raise ValueError(f"{results_path}: result without call_id/custom_id")
        if call_id in seen:
            raise ValueError(f"{results_path}: duplicate result {call_id}")
        seen.add(call_id)
        if call_id not in catalog:
            raise ValueError(f"{results_path}: unknown call_id {call_id}")
        call = catalog[call_id]
        error = result.get("error")
        parsed = None if error else _validated_parsed(result, call)
        old = completed.get(call_id)
        if old is not None:
            old_parsed = old.get("parsed")
            if parsed is not None and canonical_json(old_parsed) != canonical_json(parsed):
                raise ValueError(f"{call_id}: completed call cannot be overwritten")
            actions.append(("duplicate", call, result, old_parsed))
        elif error:
            actions.append(("failed", call, result, None))
        else:
            actions.append(("completed", call, result, parsed))

    response_rows, ledger_rows = [], []
    counts = {"completed": 0, "failed": 0, "duplicate": 0}
    for status, call, result, parsed in actions:
        counts[status] += 1
        if status == "duplicate":
            continue
        event = {
            "schema_version": RUN_CONTRACT,
            "call_id": call["call_id"],
            "status": status,
            "task": call["task"],
            "item_id": call["item_id"],
            "task_rank": call["task_rank"],
            "repeat_index": call["repeat_index"],
            "protocol": call["protocol"],
            "model": call["model"],
            "config_hash": call["config_hash"],
            "imported_utc": now,
            "error": result.get("error"),
            "response": result.get("response") if status == "failed" else None,
            "stop_reason": result.get("stop_reason"),
            "requested_utc": result.get("requested_utc"),
        }
        if status == "completed":
            event["parsed"] = parsed
            response_rows.append({
                "schema_version": RUN_CONTRACT,
                "call_id": call["call_id"],
                "item_id": call["item_id"],
                "task": call["task"],
                "case_number": call["case_number"],
                "protocol": call["protocol"],
                "model": call["model"],
                "config_hash": call["config_hash"],
                "task_rank": call["task_rank"],
                "item_rank": call["item_rank"],
                "repeat_index": call["repeat_index"],
                "variant": call["variant"],
                "request": call["request"],
                "response": result.get("response"),
                "parsed": parsed,
                "error": None,
                "stop_reason": result.get("stop_reason"),
                "requested_utc": result.get("requested_utc"),
                "imported_utc": now,
            })
        ledger_rows.append(event)
    # Responses first: a crash between the two appends errs on the side of
    # treating a paid/completed call as terminal rather than re-exporting it.
    append_jsonl(run_dir / "responses.jsonl", response_rows)
    append_jsonl(run_dir / "ledger.jsonl", ledger_rows)
    counts["missing_after_import"] = len(set(catalog) - set(read_completed(run_dir)))
    return counts


def is_openai_model(name):
    """Route by model name. OpenAI gpt-5* reasoning models reject temperature
    like current Claude models do; gpt-4.1*/gpt-4o* accept it -- which is the
    point: they restore the P2 temperature axis (DESIGN §3)."""
    return name.startswith(("gpt-", "o1", "o3", "o4"))


def to_openai(params):
    """Translate the canonical (anthropic-shaped) request record to a
    chat.completions call. The recorded `request` stays anthropic-shaped so
    every run is comparable; the translation is mechanical."""
    schema = params["output_config"]["format"]["schema"]
    out = {
        "model": params["model"],
        "messages": [{"role": "system", "content": params["system"]}]
                    + params["messages"],
        "max_completion_tokens": params["max_tokens"],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "verdict", "strict": True, "schema": schema}},
    }
    if "temperature" in params:
        out["temperature"] = params["temperature"]
    return out


def parse_openai(payload, item, protocol):
    """chat.completions payload -> the same {answer, probability} contract,
    by reshaping to the canonical content-block form and delegating."""
    try:
        choice = payload["choices"][0]
        if choice.get("finish_reason") == "content_filter":
            return None, "finish_reason=content_filter"
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        return None, f"unparseable: {type(exc).__name__}: {exc}"
    return parse_response({"content": [{"type": "text", "text": content}]}, item, protocol)


# The public CLI has no SDK import, credential read, or network branch. The
# request translators above are pure offline adapters retained for external
# executors and archived payload parsing.
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items", default=str(DEFAULT_ITEMS))
    ap.add_argument("--protocol", choices=("P1", "P2"), default="P1")
    ap.add_argument("--model", default="",
                    help="exact model identity recorded in offline requests; required for export")
    ap.add_argument("--through-items", type=int, default=None,
                    help="cumulative 1-based rank horizon PER selected task (default 10; 0 = all)")
    ap.add_argument("--through-repeats", type=int, default=None,
                    help="cumulative P2 repeat horizon (default 7)")
    ap.add_argument("--limit", type=int, default=None,
                    help="deprecated alias for --through-items")
    ap.add_argument("--k", type=int, default=None,
                    help="deprecated alias for --through-repeats")
    ap.add_argument("--seed", default="pmcpa-bench")
    ap.add_argument("--tasks", default="", help="comma-separated task filter")
    ap.add_argument("--splits", default="", help="comma-separated split filter")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--thinking", choices=("adaptive", "disabled", "unset"), default="adaptive")
    ap.add_argument("--rationale", action="store_true",
                    help="P1 only: elicit a brief visible rationale before the verdict")
    ap.add_argument("--effort", default="", help="output_config.effort recorded in the request")
    ap.add_argument("--temperature", type=float, default=None,
                    help="optional one fixed sampling temperature for every call")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="default: print prompts, make no call or write")
    ap.add_argument("--live", action="store_true",
                    help="disabled: fail closed; export a batch instead")
    ap.add_argument("--run-dir", default="",
                    help="durable run directory (required for export/import)")
    ap.add_argument("--export-batch", default="",
                    help="write missing canonical requests to this new JSONL file")
    ap.add_argument("--import-results", default="",
                    help="append normalized result JSONL receipts to --run-dir")
    ap.add_argument("--retry-ids", default="",
                    help="optional plain/JSONL call-ID allowlist; only missing IDs export")
    args = ap.parse_args(argv)

    if args.live:
        raise SystemExit("--live is disabled: this runner makes no provider calls; "
                         "use --export-batch and an external executor")
    if args.export_batch and args.import_results:
        raise SystemExit("choose one of --export-batch or --import-results")
    if args.import_results:
        if not args.run_dir:
            raise SystemExit("--import-results requires --run-dir")
        try:
            result = import_results(args.run_dir, args.import_results)
        except (ValueError, OSError) as exc:
            raise SystemExit(str(exc)) from exc
        print(f"imported  : completed={result['completed']} failed={result['failed']} "
              f"duplicates={result['duplicate']}")
        print(f"remaining : {result['missing_after_import']} catalogued call(s) incomplete")
        return 0

    if not args.model:
        if args.export_batch:
            raise SystemExit("--export-batch requires an explicit --model identity")
        args.model = UNSELECTED_MODEL

    if args.limit is not None and args.through_items is not None:
        raise SystemExit("use --through-items or deprecated --limit, not both")
    if args.k is not None and args.through_repeats is not None:
        raise SystemExit("use --through-repeats or deprecated --k, not both")
    through_items = (args.through_items if args.through_items is not None else
                     args.limit if args.limit is not None else 10)
    through_repeats = (args.through_repeats if args.through_repeats is not None else
                       args.k if args.k is not None else 7)
    if through_items < 0:
        raise SystemExit("--through-items must be >= 0")
    if through_repeats < 1:
        raise SystemExit("--through-repeats must be >= 1")
    if args.protocol != "P2" and (args.through_repeats is not None or args.k is not None):
        raise SystemExit("--through-repeats/--k applies only to P2")
    if args.protocol != "P2":
        through_repeats = 1
    if args.rationale and args.protocol != "P1":
        raise SystemExit("--rationale applies only to P1")

    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    splits = [s.strip() for s in args.splits.split(",") if s.strip()]
    if args.thinking == "adaptive" and any(args.model.startswith(p) for p in NO_ADAPTIVE_THINKING):
        print(f"NOTE: {args.model} does not support adaptive thinking; recording unset.",
              file=sys.stderr)
        args.thinking = "unset"

    items = load_ranked_items(args.items, tasks, splits, through_items, args.seed)
    if not items:
        raise SystemExit(f"no items matched (items={args.items} tasks={tasks} splits={splits})")

    if args.export_batch:
        if not args.run_dir:
            raise SystemExit("--export-batch requires --run-dir")
        try:
            note = (f"export tasks={','.join(tasks) or 'all'} "
                    f"through_items={through_items} through_repeats={through_repeats}")
            calls, config, growth_event = plan_for_run_dir(
                args.run_dir, items, args.protocol, through_repeats, args,
                args.items, note=note)
            result = export_batch(args.run_dir, args.export_batch, calls, args,
                                  args.items, config, through_items,
                                  through_repeats, tasks, splits,
                                  args.retry_ids or None,
                                  growth_event=growth_event)
        except (ValueError, OSError) as exc:
            raise SystemExit(str(exc)) from exc
        print(f"planned   : {result['planned']} call(s)")
        print(f"completed : {result['completed']} call(s) already terminal")
        print(f"exported  : {result['exported']} missing call(s) -> {result['path']}")
        if growth_event is not None:
            print(f"verified  : {growth_event['verified_rows']} existing catalog "
                  f"row(s) re-rendered byte-identically ({growth_event['note']})")
        return 0

    try:
        calls, config = build_call_plan(items, args.protocol, through_repeats, args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    rendered = set()
    for call in calls:
        params = call["request"]
        prompt_key = (params["system"], params["messages"][0]["content"])
        if prompt_key in rendered:
            print(f"repeat {call['repeat_index']:>3}: {call['call_id']} "
                  f"(byte-identical request {call['request_sha256'][:12]})")
            continue
        rendered.add(prompt_key)
        variant = call["variant"]
        print("=" * 78)
        print(f"call {call['call_id']}  item={call['item_id']}  task={call['task']} "
              f"task_rank={call['task_rank']} repeat={call['repeat_index']} "
              f"case={call['case_number']} split={call['split']}")
        print(f"variant {variant['index']}: rendition={variant['rendition']} "
              f"block_order={'/'.join(variant['block_order'])} "
              f"temperature={variant['temperature']}")
        print("-" * 78)
        print("SYSTEM:")
        print(params["system"])
        print("-" * 78)
        print("USER:")
        print(params["messages"][0]["content"])
        print("-" * 78)
        print("REQUEST PARAMS (prompt fields elided above):")
        elided = {k: v for k, v in params.items() if k not in ("system", "messages")}
        print(json.dumps(elided, indent=1, sort_keys=True))
    print("=" * 78)
    by_task = {}
    for item in items:
        by_task[item["task"]] = by_task.get(item["task"], 0) + 1
    counts = ", ".join(f"{task}={n}" for task, n in
                       sorted(by_task.items(), key=lambda x: task_order_key(x[0])))
    print(f"DRY RUN: {len(calls)} call(s) over {len(items)} item(s) ({counts})")
    print(f"protocol : {args.protocol}  model={args.model}  config={digest(config)[:16]}")
    repeat_horizon = through_repeats if args.protocol == "P2" else "n/a"
    print(f"horizons : through-items={through_items} per task  through-repeats={repeat_horizon}")
    print(f"prompts  : {len(rendered)} distinct of {len(calls)} calls")
    print("No network call was made and nothing was written. Use --export-batch with --run-dir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
