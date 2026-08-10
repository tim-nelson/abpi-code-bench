"""Elicitation runner: serve benchmark items under protocol P1, P2 or P3.

    python3 bench/run.py                                   # DRY RUN (default)
    python3 bench/run.py --protocol P1 --k 5 --limit 3
    python3 bench/run.py --protocol P3 --limit 3
    uv run --with anthropic python bench/run.py --protocol P2 --limit 10 --live

DRY RUN IS THE DEFAULT AND MAKES NO NETWORK CALL. It prints the exact system
prompt, user prompt and request parameters for every planned call, then exits.
Read them before spending money: the prompts are where leakage would show up.

A live run needs BOTH `--live` and ANTHROPIC_API_KEY in the environment, and
writes bench/runs/<timestamp>/ containing manifest.json (every parameter of the
run) and responses.jsonl (one line per call: the full request, the raw
response, and the parsed answer). Nothing is written on a dry run.

Protocols (DESIGN.md §3):

  P2  verbalized -- one call per item; the model returns an answer and a
      stated probability in [0, 1], via structured output. The mentalist foil.

  P3  behaviourist, revealed preference -- 1 + len(c_grid) calls per item. Call
      one is a verdict-only call, identical in shape to a single P1 call. Then,
      for each sure amount c in --c-grid (default 0.55,0.65,0.75,0.85,0.95), one
      binary-choice call: the model is told the verdict it gave and chooses
      between a LOTTERY paying 1 if that verdict matches the adjudicator (else
      0) and a SURE payoff of c. The BDM incentive story is stated in the
      prompt: one c is drawn at random afterwards and the choice made at that c
      is enacted, so every choice can be the one that counts. Consistency across
      amounts is explicitly NOT required -- the switching pattern is data, and
      score.py reports how often it is non-monotone.

      Confidence is the implied indifference point, fitted in score.py; run.py
      only records the choices. At the default grid this is 6 calls per item,
      so --limit 10 is 60 calls. Check the arithmetic before --live.

  P1  behaviourist -- K calls per item over perturbed presentations, each
      returning a verdict and nothing else. Confidence is the frequency of the
      modal answer. Phase-1 perturbation axes:

        rendition swap  the item's alternate publisher-written telling of the
                        same complaint/response, where L2 supplies one
        block order    the order of the CLAUSE / CASE DETAILS / EXTRACT blocks
                        in the user turn. DESIGN §4.2 calls this
                        "clause-presentation order"; with one clause under
                        test per item, block order is its faithful analogue
        temperature    decoding temperature -- SEE THE NOTE BELOW

TEMPERATURE IS NOT AVAILABLE ON CURRENT MODELS. `temperature`, `top_p` and
`top_k` are rejected outright by claude-opus-5, claude-sonnet-5, claude-fable-5
and claude-opus-4-7/4-8. On those models this runner drops the temperature axis
and says so; the repeated calls become plain resamples, which still measure
decoding stochasticity but not a temperature sweep. To sweep temperature you
must pick a model that still accepts sampling parameters (e.g.
claude-sonnet-4-6, claude-haiku-4-5) and pass --temperatures.
"""

import argparse
import hashlib
import itertools
import json
import os
import pathlib
import sys
import time

BENCH = pathlib.Path(__file__).resolve().parent
DEFAULT_ITEMS = BENCH / "items.jsonl"
RUNS = BENCH / "runs"

DEFAULT_MODEL = "claude-sonnet-5"

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

# P3 stage-2 answer space, and the default sure-amount grid swept for the
# indifference point. The grid straddles the interesting range: below 0.55 and
# above 0.95 the choice carries almost no information about a binary verdict.
CHOICES = ("lottery", "sure")
DEFAULT_C_GRID = (0.55, 0.65, 0.75, 0.85, 0.95)

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
        "Reply with your verdict and nothing else. Do not explain, qualify or hedge; "
        "the answer field is the entire response."
    ),
    "P2": (
        "Reply with your verdict and the probability that your verdict is the one the "
        "adjudicator actually reached. The probability is a number between 0 and 1: use 0.5 "
        "when you would be guessing and values near 1 only when you would be surprised to be "
        "wrong. Do not explain."
    ),
}

# P3's stage-1 call IS a single P1 call: same system prompt, same schema, same
# canonical presentation. Aliasing rather than copying keeps that literal.
PROTOCOL_INSTRUCTION["P3"] = PROTOCOL_INSTRUCTION["P1"]

# P3 stage 2. Deliberately says nothing about which option is preferable, and
# nothing about what a high or low c implies -- the whole measurement is where
# the model's own preference reverses. What it MUST say is the BDM incentive
# story (any one choice can be the enacted one, so each is worth answering
# truthfully) and that consistency is not required, so a non-monotone pattern
# is an honest datum rather than a prompt artefact.
P3_CHOICE_INSTRUCTION = (
    "You have already answered a question about this case. You are now offered a choice between "
    "two options, and your task is to say which one you prefer.\n\n"
    "HOW THIS CHOICE IS USED\n"
    "You are asked this same choice at several different sure amounts. Afterwards one of those "
    "amounts is drawn at random and the choice you made at the amount drawn is the one that is "
    "enacted and paid out. Any single choice can therefore be the one that counts, so state your "
    "genuine preference at the amount in front of you. You are not required to be consistent "
    "across amounts or across questions, and nothing you choose here changes the answer you "
    "already gave.\n\n"
    "Reply with your choice and nothing else. Do not explain, qualify or hedge; the choice field "
    "is the entire response."
)


def question_body(item):
    """The question itself: no QUESTION header, no answer-format line.

    Split out of question() so the P3 choice call can restate what was asked
    without also restating how to format a verdict.
    """
    task = item["task"]
    clause = item["inputs"]["clause_ref"].get("clause")
    year = item["inputs"]["clause_ref"].get("code_year")
    year_txt = f"the {year} Code" if year else "the Code"
    if task in ("T1", "T1-triage"):
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
    if task in ("T1", "T1-triage") or task in T5_TASKS:
        return "Answer 'breach' or 'no_breach'."
    if task == "T3":
        return "Answer 'upheld' or 'overturned'."
    if task == "T4":
        return ("Answer 'in_scope', or 'outwith_scope' if the Panel would rule that the complaint "
                "falls outwith the scope of the Code.")
    raise ValueError(f"unknown task {task!r}")


def question(item):
    return f"QUESTION\n{question_body(item)}\n{answer_line(item)}"


def choice_block(item, prior_answer, c):
    """P3 stage 2: restate the model's own verdict, then offer lottery vs sure.

    The verdict is quoted back verbatim so the lottery's payoff condition is
    unambiguous; nothing here reveals the adjudicated label, and neither option
    is presented as the sensible one.
    """
    return ("YOUR PREVIOUS ANSWER\n"
            f"You were asked:\n{question_body(item)}\n"
            f"You answered: {prior_answer}\n\n"
            "THE CHOICE\n"
            f"(A) LOTTERY - pays 1 point if '{prior_answer}' is the answer the adjudicator "
            f"actually reached in this case, and 0 points if it is not.\n"
            f"(B) SURE - pays {c:.2f} points whatever the adjudicator decided.\n\n"
            "Which do you choose? Answer 'lottery' or 'sure'.")


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
    stage = variant.get("stage", "verdict")
    instruction = P3_CHOICE_INSTRUCTION if stage == "choice" else PROTOCOL_INSTRUCTION[protocol]
    if item["task"] in T5_TASKS:
        import t5_generate as t5
        system = f"{t5.T5_SYSTEM_BASE}\n\n{instruction}"
        blocks = {
            "clause": clause_block(item),
            "metadata": t5.t5_setting_block(item),
            "extract": t5.t5_material_block(item, variant["rendition"]),
        }
    else:
        # T3 is served under its own base (see T3_SYSTEM_BASE); T1/T1-triage/T4
        # keep SYSTEM_BASE, and reading it as a module global here is what lets
        # t5_generate.register_with_run swap it in memory.
        base = T3_SYSTEM_BASE if item["task"] == "T3" else SYSTEM_BASE
        system = f"{base}\n\n{instruction}"
        blocks = {
            "clause": clause_block(item),
            "metadata": metadata_block(item),
            "extract": extract_block(item, variant["rendition"]),
        }
    ordered = [blocks[name] for name in variant["block_order"] if blocks[name]]
    tail = (choice_block(item, variant["prior_answer"], variant["c"]) if stage == "choice"
            else question(item))
    return system, "\n\n".join(ordered + [tail])


def output_schema(item, protocol, stage="verdict"):
    if stage == "choice":
        return {"type": "object",
                "properties": {"choice": {"type": "string", "enum": list(CHOICES)}},
                "required": ["choice"], "additionalProperties": False}
    props = {"answer": {"type": "string", "enum": list(ANSWERS[item["task"]])}}
    required = ["answer"]
    if protocol == "P2":
        # No minimum/maximum: structured outputs reject numeric constraints.
        # The range is stated in the prompt and enforced when parsing.
        props["probability"] = {"type": "number", "description": "Probability between 0 and 1 that the answer is correct."}
        required.append("probability")
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def effective_orders(item):
    """Block orders that actually produce different prompts for this item.

    T4 has no clause block, so permutations differing only in where the absent
    block sits render identically. Counting those as perturbations would inflate
    apparent agreement under P1, so they are collapsed here.
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


def plan_variants(item, protocol, k, seed, temperatures, c_grid=()):
    """Deterministic K-variant plan. Same item + seed -> same variants."""
    if protocol == "P2":
        return [{"index": 0, "rendition": 0, "block_order": BLOCK_ORDERS[0], "temperature": None}]
    if protocol == "P3":
        # Canonical presentation throughout: P3 measures preference reversal
        # over c, so varying the presentation as well would confound the two.
        base = {"rendition": 0, "block_order": BLOCK_ORDERS[0], "temperature": None}
        plan = [{"index": 0, "stage": "verdict", "c": None, "prior_answer": None, **base}]
        for i, c in enumerate(c_grid, start=1):
            plan.append({"index": i, "stage": "choice", "c": c, "prior_answer": None, **base})
        return plan

    n_rend = 1 + len(item["inputs"].get("renditions", []))
    temps = temperatures or [None]
    combos = [
        {"rendition": r, "block_order": bo, "temperature": t}
        for r in range(n_rend) for bo in effective_orders(item) for t in temps
    ]
    offset = int(hashlib.blake2b(f"{seed}:{item['item_id']}".encode("utf-8"), digest_size=4).hexdigest(), 16)
    step = max(1, len(combos) // k)
    out = []
    for i in range(k):
        c = combos[(offset + i * step) % len(combos)]
        out.append({"index": i, **c})
    return out


def request_params(item, protocol, variant, args):
    schema = output_schema(item, protocol, variant.get("stage", "verdict"))
    if protocol == "P2" and getattr(args, "rationale", False):
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
    if protocol == "P2" and getattr(args, "rationale", False):
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


def parse_response(payload, item, protocol, stage="verdict"):
    """Pull {answer, probability} -- or, on a P3 choice call, {choice} -- out of
    the structured-output text block."""
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
    if stage == "choice":
        choice = data.get("choice")
        if choice not in CHOICES:
            return None, f"choice {choice!r} not in {CHOICES}"
        return {"choice": choice}, None
    answer = data.get("answer")
    if answer not in ANSWERS[item["task"]]:
        return None, f"answer {answer!r} not in {ANSWERS[item['task']]}"
    parsed = {"answer": answer}
    if protocol == "P2":
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


def is_openai_model(name):
    """Route by model name. OpenAI gpt-5* reasoning models reject temperature
    like current Claude models do; gpt-4.1*/gpt-4o* accept it -- which is the
    point: they restore the P1 temperature axis (DESIGN §4.2)."""
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


def parse_openai(payload, item, protocol, stage="verdict"):
    """chat.completions payload -> the same {answer, probability} contract,
    by reshaping to the canonical content-block form and delegating."""
    try:
        choice = payload["choices"][0]
        if choice.get("finish_reason") == "content_filter":
            return None, "finish_reason=content_filter"
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        return None, f"unparseable: {type(exc).__name__}: {exc}"
    return parse_response({"content": [{"type": "text", "text": content}]}, item, protocol, stage)


def load_env():
    """Read KEY=VALUE lines from the repo-root .env into the environment.

    Existing environment variables win; values are never printed. The file is
    optional -- CI or a shell export works the same way."""
    env_file = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items", default=str(DEFAULT_ITEMS))
    ap.add_argument("--protocol", choices=("P1", "P2", "P3"), default="P2")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="default %(default)s -- cheap smoke runs; state the model in any writeup")
    ap.add_argument("--limit", type=int, default=10, help="items to serve (default %(default)s); 0 = all")
    ap.add_argument("--k", type=int, default=5, help="P1 variants per item (default %(default)s)")
    ap.add_argument("--c-grid", default=",".join(f"{c:g}" for c in DEFAULT_C_GRID),
                    help="P3 sure-amount grid (default %(default)s); P3 costs 1 + len(grid) "
                         "calls per item")
    ap.add_argument("--seed", default="abpi-code-bench-v1")
    ap.add_argument("--tasks", default="", help="comma-separated task filter")
    ap.add_argument("--splits", default="", help="comma-separated split filter")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--thinking", choices=("adaptive", "disabled", "unset"), default="adaptive")
    ap.add_argument("--rationale", action="store_true",
                    help="P2 only: elicit a brief visible rationale before the verdict")
    ap.add_argument("--effort", default="", help="output_config.effort (low|medium|high|xhigh|max)")
    ap.add_argument("--temperatures", default="",
                    help="comma-separated P1 temperature axis; ignored on models that reject sampling params")
    ap.add_argument("--dry-run", action="store_true", default=True, help="(default) print prompts, make no call")
    ap.add_argument("--live", dest="dry_run", action="store_false", help="actually call the API")
    ap.add_argument("--run-dir", default="", help="override bench/runs/<timestamp>")
    args = ap.parse_args(argv)

    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    splits = [s.strip() for s in args.splits.split(",") if s.strip()]
    temperatures = [float(t) for t in args.temperatures.split(",") if t.strip()]

    c_grid = sorted({float(c) for c in args.c_grid.split(",") if c.strip()})
    if args.protocol == "P3":
        if not c_grid:
            raise SystemExit("--protocol P3 needs a non-empty --c-grid")
        if not all(0.0 < c < 1.0 for c in c_grid):
            raise SystemExit(f"--c-grid values must lie strictly inside (0, 1); got {c_grid}")
    else:
        c_grid = []

    if args.thinking == "adaptive" and any(args.model.startswith(p) for p in NO_ADAPTIVE_THINKING):
        print(f"NOTE: {args.model} does not support adaptive thinking; sending no thinking param.",
              file=sys.stderr)
        args.thinking = "unset"
    sampling_ok = not any(args.model.startswith(p) for p in NO_SAMPLING_PARAMS)
    if temperatures and not sampling_ok:
        print(f"NOTE: {args.model} rejects sampling parameters; dropping the temperature axis "
              f"({temperatures}). Repeated variants become resamples.", file=sys.stderr)
        temperatures = []

    items = load_items(args.items, tasks, splits, args.limit)
    if not items:
        raise SystemExit(f"no items matched (items={args.items} tasks={tasks} splits={splits})")

    calls = [(item, v) for item in items
             for v in plan_variants(item, args.protocol, args.k, args.seed, temperatures, c_grid)]

    if args.dry_run:
        rendered = set()
        for item, variant in calls:
            if variant.get("stage") == "choice":
                # A dry run has no stage-1 answer to quote. Render with the
                # task's first allowed verdict so the prompt is complete and the
                # output stays deterministic; the banner below says so.
                variant = {**variant, "prior_answer": ANSWERS[item["task"]][0]}
            params = request_params(item, args.protocol, variant, args)
            rendered.add((params["system"], params["messages"][0]["content"]))
            print("=" * 78)
            print(f"item {item['item_id']}  task={item['task']}  case={item['case_number']}  "
                  f"split={item['split']}  label={item['label']}  [WITHHELD FROM THE MODEL]")
            print(f"variant {variant['index']}: rendition={variant['rendition']} "
                  f"block_order={'/'.join(variant['block_order'])} temperature={variant['temperature']}")
            if variant.get("stage") == "verdict":
                print("P3 stage=verdict (one P1-shaped call; its answer is quoted back below)")
            elif variant.get("stage") == "choice":
                print(f"P3 stage=choice c={variant['c']:.2f} "
                      f"prior_answer={variant['prior_answer']!r}  <- DRY-RUN PLACEHOLDER; "
                      f"a live run quotes the model's own stage-1 verdict")
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
        print(f"DRY RUN: {len(calls)} call(s) over {len(items)} item(s), protocol {args.protocol}, "
              f"model {args.model}. No API call was made and nothing was written.")
        if args.protocol == "P3":
            print(f"P3 grid   : c = {', '.join(f'{c:g}' for c in c_grid)}  "
                  f"-> {1 + len(c_grid)} calls per item")
        print(f"distinct prompts: {len(rendered)} of {len(calls)} call(s)"
              + ("" if len(rendered) == len(calls) else
                 "  <- repeats are resamples at identical input, not perturbations"))
        print("Re-run with --live (and ANTHROPIC_API_KEY set) to execute.")
        return 0

    load_env()
    use_openai = is_openai_model(args.model)
    if use_openai:
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("--live with an OpenAI model requires OPENAI_API_KEY in the environment")
        try:
            import openai
        except ImportError:
            raise SystemExit("--live needs the openai SDK: uv run --with openai python bench/run.py --live ...")
        anthropic = None
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("--live requires ANTHROPIC_API_KEY in the environment")
        try:
            import anthropic
        except ImportError:
            raise SystemExit("--live needs the anthropic SDK: uv run --with anthropic python bench/run.py --live ...")
        openai = None

    # pid suffix: two runs launched in the same second collided on the bare
    # timestamp and one clobbered the other's responses.jsonl (found by the
    # Phase A1 ladder, 2026-08-02).
    run_dir = pathlib.Path(args.run_dir) if args.run_dir else RUNS / (
        time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{os.getpid()}")
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "items_path": str(pathlib.Path(args.items).resolve()),
        "items_sha256": sha256(args.items),
        "n_items": len(items),
        "n_calls": len(calls),
        "protocol": args.protocol,
        "model": args.model,
        # k = calls planned per item, whatever the protocol supplies them for.
        "k": {"P1": args.k, "P2": 1, "P3": 1 + len(c_grid)}[args.protocol],
        "c_grid": c_grid,
        "seed": args.seed,
        "max_tokens": args.max_tokens,
        "thinking": args.thinking,
        "rationale": getattr(args, "rationale", False),
        "effort": args.effort or None,
        "temperatures": temperatures,
        "sampling_params_supported": sampling_ok,
        "tasks_filter": tasks,
        "splits_filter": splits,
        "provider": "openai" if use_openai else "anthropic",
        "sdk_version": getattr(openai if use_openai else anthropic, "__version__", "unknown"),
        "python": sys.version.split()[0],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    if use_openai:
        client = openai.OpenAI()
        try:
            client.models.retrieve(args.model)
        except Exception as exc:
            names = sorted(m.id for m in client.models.list() if m.id.startswith(("gpt", "o")))
            raise SystemExit(f"model {args.model!r} not available ({exc}).\nAvailable: {', '.join(names)}")
    else:
        client = anthropic.Anthropic()
    n_ok = n_err = 0
    # P3 is the one protocol whose calls are not independent: every choice call
    # must quote the verdict THIS model gave on THIS item. plan_variants emits
    # the verdict call first and the flat call list preserves item grouping, so
    # one dict of stage-1 answers carried down the loop is enough -- no change
    # to the single-pass architecture. An item whose stage-1 call failed gets
    # its choice calls recorded as skipped rather than sent with a fabricated
    # prior answer, so coverage stays honest.
    stage1_answer = {}
    with (run_dir / "responses.jsonl").open("w", encoding="utf-8") as fh:
        for n, (item, variant) in enumerate(calls, 1):
            stage = variant.get("stage", "verdict")
            if stage == "choice":
                prior = stage1_answer.get(item["item_id"])
                if prior is None:
                    record = {
                        "item_id": item["item_id"], "task": item["task"],
                        "case_number": item["case_number"], "protocol": args.protocol,
                        "variant": {**variant, "block_order": list(variant["block_order"])},
                        "request": None, "response": None, "parsed": None,
                        "error": "stage-1 verdict unavailable; choice call not sent",
                        "requested_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    fh.flush()
                    n_err += 1
                    print(f"[{n}/{len(calls)}] {item['item_id']} v{variant['index']} "
                          f"-> {record['error']}")
                    continue
                variant = {**variant, "prior_answer": prior}
            params = request_params(item, args.protocol, variant, args)
            record = {
                "item_id": item["item_id"],
                "task": item["task"],
                "case_number": item["case_number"],
                "protocol": args.protocol,
                "variant": {**variant, "block_order": list(variant["block_order"])},
                "request": params,
                "requested_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            try:
                if use_openai:
                    resp = client.chat.completions.create(**to_openai(params))
                    payload = resp.model_dump()
                    record["response"] = payload
                    record["stop_reason"] = payload["choices"][0].get("finish_reason") if payload.get("choices") else None
                    record["parsed"], record["error"] = parse_openai(payload, item, args.protocol, stage)
                else:
                    resp = client.messages.create(**params)
                    payload = resp.to_dict()
                    record["response"] = payload
                    record["stop_reason"] = payload.get("stop_reason")
                    if payload.get("stop_reason") == "refusal":
                        record["parsed"], record["error"] = None, "stop_reason=refusal"
                    else:
                        record["parsed"], record["error"] = parse_response(payload, item, args.protocol, stage)
            except Exception as exc:  # network, rate limit, validation -- keep going
                record["response"], record["parsed"] = None, None
                record["error"] = f"{type(exc).__name__}: {exc}"
            if args.protocol == "P3" and stage == "verdict" and record.get("parsed"):
                stage1_answer[item["item_id"]] = record["parsed"]["answer"]
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            if record.get("error"):
                n_err += 1
            else:
                n_ok += 1
            print(f"[{n}/{len(calls)}] {item['item_id']} v{variant['index']} "
                  f"-> {record.get('parsed') or record.get('error')}")

    print(f"\nrun dir : {run_dir}")
    print(f"calls   : {len(calls)}  parsed={n_ok}  errors={n_err}")
    print(f"score   : python3 bench/score.py --run {run_dir} --items {args.items}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
