"""Memorisation probe: ask a model whether it recognises a case (DESIGN.md §6).

    python3 bench/probe.py                                  # DRY RUN (default)
    python3 bench/probe.py --limit 3 --items bench/subsets/phase_a.jsonl
    uv run --with anthropic python bench/probe.py --limit 10 --live
    python3 bench/probe.py --provider openrouter --model moonshotai/kimi-k3 \
        --limit 1 --live                                    # OpenAI-compat path
    python3 bench/probe.py --score --run bench/runs/probe-<ts>-<pid>

DRY RUN IS THE DEFAULT AND MAKES NO NETWORK CALL, exactly as in bench/run.py: it
prints the full system prompt, user prompt and request parameters for every
planned call and exits. A live run needs BOTH --live and the relevant API key --
ANTHROPIC_API_KEY, OPENAI_API_KEY for a gpt-* model, or the registered provider
key with --provider -- taken from the process environment or read literally from
the repo-root .env (never printed, never interpolated). It writes
bench/runs/probe-<ts>-<pid>/ containing manifest.json and responses.jsonl.

OPENAI-COMPATIBLE PROVIDERS (--provider xai|openrouter|deepseek) route live
calls through the exact pinned (provider, model) configuration of
bench/providers/openai_compat.py: the same MODEL_CONFIGS pin (max-tokens field
name, thinking, reasoning_effort), the same structured-output encoding (strict
json_schema, or DeepSeek's documented json_object with the schema appended to
the system prompt), the same stdlib transport and the same 429/5xx/transport
retry policy. A (provider, model) pair absent from MODEL_CONFIGS is refused,
never degraded. Responses are parsed against PROBE_SCHEMA exactly as for native
models; unparseable or truncated responses are recorded as failures, never
guessed.

WHAT IS PROBED, AND AT WHAT GRAIN
Contamination is a property of a CASE, not of an item: every item drawn from
AUTH/1234/5/67 is contaminated or not together. So items are deduped to cases
and each case is probed exactly once. The extract shown is complaint-only, the
same span T2 serves, because the complaint is the part of the report a
model would have to recognise from -- the response and the ruling are not shown.
Cases whose only items are T3 are skipped rather than probed with a
panel_ruling span, which would hand the model the outcome.

STATUSES (assigned by --score, written to bench/probes.jsonl)

    contaminated           the stated case number matches this case's real one,
                           or any of its folded sibling numbers, after
                           normalisation
    recognised_unverified  the model claimed recognition but gave a wrong
                           number or none
    clean                  no recognition claimed and no matching number

`clean` means "this probe did not catch it", NOT "proven unseen in training".
The probe prompt tells the model not to guess, which is right for measuring
recall rather than inference, but it biases the count DOWNWARD -- the failure
direction that keeps contaminated items in the bank. Treat `clean` as weak
evidence and the post-cutoff holdout (DESIGN §6) as the strong answer.
`recognised_unverified` is kept as its own bucket precisely because collapsing
it into either neighbour would hide that trade-off. Every raw response is
archived in the run dir, so a stricter or looser rule can be re-scored later
without spending the calls again.

JOINING TO ITEMS
This script does NOT modify bench/generate.py and does not rewrite
bench/items.jsonl. Items ship `contamination.probe_status: "untested"`; the
result of a probe lives in bench/probes.jsonl keyed by `case_number`, and the
main loop joins the two on that key when it is ready to wire it up:

    probes = {r["case_number"]: r["probe_status"] for r in load_jsonl("bench/probes.jsonl")
              if r["model"] == MODEL}
    status = probes.get(item["case_number"], "untested")

Note the vocabulary gap that join has to resolve: item_schema.json's
`contamination.probe_status` enum is untested|clean|contaminated and has no slot
for `recognised_unverified`. Widening the enum is a schema change and therefore
someone else's call, so probes.jsonl records the finer status and the joiner
decides how to fold it. Rows are keyed by (case_number, model): probing a second
model appends rows rather than overwriting the first model's verdict, because
contamination is a property of a case AND a model, never of the case alone.

WHY THIS PROBE CANNOT POISON A LATER RUN
The prompt contains the complaint extract and a recall question. It contains no
case number, no ruling, no outcome, no clause list -- strictly LESS than
T2 already shows a model, plus a question about identity. The true case
number is held only on our side and is never sent; it enters the picture for the
first time in --score, offline, comparing our label against what the model
volunteered. So no model can learn the answer by being probed, repeated probes
of the same case do not interact, and the only model-visible text any artefact
stores (`request` in responses.jsonl) is the prompt itself. probes.jsonl is
keyed by the true case number by construction, but it is an analysis artefact:
nothing in this repo feeds it back to a model.
"""

import argparse
import json
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run as runner  # noqa: E402  (same directory, deliberate -- see validate.py)
from providers import openai_compat  # noqa: E402  (same directory, deliberate)

BENCH = pathlib.Path(__file__).resolve().parent
DEFAULT_ITEMS = BENCH / "items.jsonl"
DEFAULT_PROBES = BENCH / "probes.jsonl"
RUNS = BENCH / "runs"

# Cases are probed from complaint-only spans. T3 quotes the panel ruling, so a
# T3 item is never an acceptable source; preference order among the rest.
SOURCE_TASKS = ("T2", "T1")

# The per-chunk banners generate.py writes into extract_text. Chunks appear in
# DOCUMENT order, not kind order (bench/review/DEFECTS.md D5), so an extract can
# open with a response chunk and alternate -- splitting at "the first response
# banner" would show the respondent's case. The complaint span has to be
# reassembled from the banners instead.
HEADS = {
    "complaint": "[COMPLAINT]",
    "response": "[RESPONSE FROM THE RESPONDENT COMPANY]",
    "panel_ruling": "[PANEL RULING UNDER APPEAL]",
}
HEAD_RE = re.compile(
    r"^(\[COMPLAINT\]|\[RESPONSE FROM THE RESPONDENT COMPANY\]|\[PANEL RULING UNDER APPEAL\])$",
    re.MULTILINE)

STATUS_CONTAMINATED = "contaminated"
STATUS_RECOGNISED = "recognised_unverified"
STATUS_CLEAN = "clean"

PROBE_SYSTEM = (
    "You are being asked a factual recall question about the PMCPA (the Prescription Medicines "
    "Code of Practice Authority), which adjudicates complaints under the ABPI Code of Practice "
    "for the Pharmaceutical Industry.\n\n"
    "This is a question about what you already know. Answer from prior knowledge of the published "
    "case report only. Do not infer or reconstruct a case number from the content of the extract, "
    "and do not guess: 'unknown' is a complete and acceptable answer, and is better than a case "
    "number you are not confident of.\n\n"
    "Reply with the fields asked for and nothing else. Do not explain."
)

PROBE_QUESTION = (
    "QUESTION\n"
    "Do you recognise this specific PMCPA case? If so state its case number (format "
    "AUTH/NNNN/M/YY or CASE/NNNN/MM/YY) and the Panel's ruling; if you do not recognise this "
    "exact case, answer unknown.\n"
    "Set recognised to true only if you recognise this exact case. If you answer unknown, set "
    "recognised to false and leave case_number and ruling_summary null."
)

PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "recognised": {"type": "boolean",
                       "description": "True only if you recognise this exact published case."},
        "case_number": {"anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "The case number, or null if you do not know it."},
        "ruling_summary": {"anyOf": [{"type": "string"}, {"type": "null"}],
                           "description": "What the Panel ruled, or null if you do not know."},
    },
    "required": ["recognised", "case_number", "ruling_summary"],
    "additionalProperties": False,
}

# AUTH/3572/10/21, CASE/1234/05/19 -- and the sloppier renderings a model may
# produce (spaces, hyphens, a four-digit year, lower case).
CASE_NUM_RE = re.compile(
    r"\b(AUTH|CASE)\s*[/\-\s]\s*(\d{2,5})\s*[/\-\s]\s*(\d{1,3})\s*[/\-\s]\s*(\d{2,4})\b",
    re.IGNORECASE)


# --- case selection ----------------------------------------------------------

def load_jsonl(path):
    out = []
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def complaint_only(item):
    """The complaint chunks of an item's extract, or None if they can't be isolated.

    Keeps only [COMPLAINT] chunks and re-joins them exactly as generate.py would
    have rendered a complaint-only extract, so what the probe shows is
    byte-identical to the T2 span for that case. On any unexpected shape
    it returns None and the case is skipped -- a probe that might be showing the
    respondent's case is worse than no probe.
    """
    kinds = [p.get("kind") for p in item["inputs"].get("extract_provenance", [])]
    if "panel_ruling" in kinds:
        return None
    parts = HEAD_RE.split(item["inputs"]["extract_text"])
    if len(parts) < 3 or parts[0].strip():
        return None
    kept = [body.strip("\n") for head, body in zip(parts[1::2], parts[2::2])
            if head == HEADS["complaint"] and body.strip()]
    if not kept:
        return None
    return "\n\n".join(f"{HEADS['complaint']}\n{body}" for body in kept)


def cases_from_items(items):
    """Dedupe items to cases; pick one complaint-only extract per case.

    Deterministic: candidates are ranked by task preference then item_id, so the
    same bank always probes the same span for the same case.
    """
    by_case = {}
    for item in items:
        if item["task"] not in SOURCE_TASKS:
            continue
        by_case.setdefault(item["case_number"], []).append(item)

    cases, skipped = [], []
    for case_number in sorted(by_case):
        ranked = sorted(by_case[case_number],
                        key=lambda it: (SOURCE_TASKS.index(it["task"]), it["item_id"]))
        for item in ranked:
            extract = complaint_only(item)
            if extract:
                cases.append({
                    "case_number": case_number,
                    "sibling_case_numbers": sorted(item.get("sibling_case_numbers") or []),
                    "item_id": item["item_id"],
                    "task": item["task"],
                    "split": item["split"],
                    "extract": extract,
                })
                break
        else:
            skipped.append((case_number, "no complaint-only extract available"))
    return cases, skipped


def build_prompt(case):
    """Return (system, user). No case number, no ruling, no outcome -- ever."""
    return PROBE_SYSTEM, f"{case['extract']}\n\n{PROBE_QUESTION}"


def request_params(case, args):
    system, user = build_prompt(case)
    params = {
        "model": args.model,
        "max_tokens": args.max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": PROBE_SCHEMA}},
    }
    if args.thinking == "adaptive":
        params["thinking"] = {"type": "adaptive"}
    elif args.thinking == "disabled":
        params["thinking"] = {"type": "disabled"}
    if args.effort:
        params["output_config"]["effort"] = args.effort
    return params


def parse_probe(payload):
    """{recognised, case_number, ruling_summary} out of the structured output."""
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
    recognised = data.get("recognised")
    if not isinstance(recognised, bool):
        return None, f"recognised {recognised!r} is not a boolean"
    parsed = {"recognised": recognised}
    for key in ("case_number", "ruling_summary"):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            return None, f"{key} {value!r} is neither a string nor null"
        parsed[key] = value
    return parsed, None


def parse_probe_openai(payload):
    try:
        choice = payload["choices"][0]
        if choice.get("finish_reason") == "content_filter":
            return None, "finish_reason=content_filter"
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        return None, f"unparseable: {type(exc).__name__}: {exc}"
    return parse_probe({"content": [{"type": "text", "text": content}]})


# --- key loading (native path) -----------------------------------------------

def load_env_key(env_key, env_file=None):
    """Ensure env_key is in os.environ; fall back to the repo .env, literally.

    Same contract as openai_compat.load_api_key, which cannot be reused here
    because it only accepts its registered provider keys: the process
    environment wins; the dotenv file is parsed literally with no
    interpolation, command substitution, inline comments or escape processing;
    the value is exported for the SDK and never printed. Returns True when the
    key is now set, False when it is simply absent (the caller owns the error
    message); conflicting or malformed assignments are refused outright.
    """
    if os.environ.get(env_key):
        return True
    path = pathlib.Path(env_file) if env_file else openai_compat.DEFAULT_ENV_FILE
    if not path.exists():
        return False
    values = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            candidate = line.strip()
            if not candidate or candidate.startswith("#"):
                continue
            if candidate.startswith("export "):
                candidate = candidate[7:].lstrip()
            match = re.fullmatch(rf"{re.escape(env_key)}\s*=\s*(.*)", candidate)
            if not match:
                continue
            value = match.group(1).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            elif value.startswith(("'", '"')) or value.endswith(("'", '"')):
                raise SystemExit(f"{path}:{lineno}: unmatched quote in {env_key}")
            if not value:
                raise SystemExit(f"{path}:{lineno}: {env_key} is empty")
            values.append(value)
    if not values:
        return False
    if len(set(values)) != 1:
        raise SystemExit(f"{path}: conflicting {env_key} assignments")
    os.environ[env_key] = values[0]
    return True


# --- openai-compat live path -------------------------------------------------

def compat_request_body(case, args, config):
    """One chat-completions body, built exactly as openai_compat.to_chat_body
    pins it for this (provider, model): same structured-output encoding, same
    max-tokens field name, same pinned thinking/reasoning_effort, same
    OpenRouter require_parameters routing. Only the schema and the prompt
    differ (PROBE_SCHEMA and the probe prompt instead of a canonical row)."""
    system, user = build_prompt(case)
    if config["structured_output"] == "json_schema":
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": openai_compat.STRUCTURED_OUTPUT_NAME,
                "strict": True,
                "schema": json.loads(
                    openai_compat.canonical_json(PROBE_SCHEMA).decode("utf-8")),
            },
        }
    elif config["structured_output"] == "json_object_prompt_schema":
        response_format = {"type": "json_object"}
        system = (system + openai_compat.JSON_OBJECT_SCHEMA_INSTRUCTION
                  + openai_compat.canonical_json(PROBE_SCHEMA).decode("utf-8"))
    else:  # registry invariant, mirrored from to_chat_body
        raise SystemExit(
            f"unsupported structured_output mode {config['structured_output']!r}")
    body = {
        "model": args.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        config["max_tokens_field"]: args.max_tokens,
        "response_format": response_format,
        "stream": False,
    }
    if config["thinking"] is not None:
        body["thinking"] = json.loads(
            openai_compat.canonical_json(config["thinking"]).decode("utf-8"))
    if config["reasoning_effort"] is not None:
        body["reasoning_effort"] = config["reasoning_effort"]
    if openai_compat.PROVIDERS[args.provider].get("provider_routing"):
        body["provider"] = {"require_parameters": True}
    return body


def compat_call(transport, body):
    """One probe call under openai_compat's retry policy (429/5xx/transport
    only, bounded attempts, Retry-After honoured). Returns (result, attempts,
    error): result is transport.json()'s payload+meta on success, error is a
    sanitized message on terminal failure. Never raises for provider errors."""
    attempts = 0
    while True:
        attempts += 1
        try:
            result = transport.json("POST", openai_compat.CHAT_ENDPOINT, body)
        except openai_compat.ProviderHTTPError as exc:
            if (openai_compat._is_retryable(exc)
                    and attempts < openai_compat.RETRY_MAX_ATTEMPTS):
                time.sleep(openai_compat._retry_sleep_seconds(attempts, exc.retry_after))
                continue
            status = f"HTTP {exc.status}" if exc.status is not None else "transport"
            return None, attempts, f"provider_http_error ({status}): {exc.safe_message}"
        return result, attempts, None


def parse_probe_compat(payload):
    """Compat payload -> parse_probe, holding the adapter's completeness bar:
    exactly one choice, finish_reason 'stop' (a length-truncated reasoning run
    is a failure, not a guess), an assistant message with non-empty content."""
    if payload.get("error"):
        return None, openai_compat._provider_error_message(payload, None)
    try:
        content = openai_compat._extract_message_content(payload)
    except openai_compat.AdapterError as exc:
        return None, str(exc)
    return parse_probe({"content": [{"type": "text", "text": content}]})


def compat_live(cases, skipped, splits, cases_filter, args, config):
    """Live probe loop over the OpenAI-compatible chat-completions transport.

    Mirrors the native loop exactly: one call per case, the full payload
    archived in responses.jsonl, every parse failure recorded as a failure.
    The run dir format and --score work unchanged on these runs.
    """
    try:
        transport = openai_compat.OpenAICompatTransport(
            args.provider, openai_compat.load_api_key(args.provider))
    except openai_compat.AdapterError as exc:
        raise SystemExit(str(exc))

    run_dir = pathlib.Path(args.run_dir) if args.run_dir else RUNS / (
        "probe-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{os.getpid()}")
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": "memorisation_probe",
        "items_path": str(pathlib.Path(args.items).resolve()),
        "items_sha256": runner.sha256(args.items),
        "n_cases": len(cases),
        "n_calls": len(cases),
        "n_skipped_cases": len(skipped),
        "model": args.model,
        "max_tokens": args.max_tokens,
        # what is actually sent: the MODEL_CONFIGS pin, not the CLI flags
        "thinking": config["thinking"],
        "effort": config["reasoning_effort"],
        "splits_filter": splits,
        "cases_filter": cases_filter,
        "provider": args.provider,
        "compat": "openai_chat_completions",
        "adapter_contract": openai_compat.ADAPTER_CONTRACT,
        "base_url": openai_compat.PROVIDERS[args.provider]["base_url"],
        "structured_output": config["structured_output"],
        "max_tokens_field": config["max_tokens_field"],
        "sdk_version": "stdlib-urllib",
        "python": sys.version.split()[0],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                                           encoding="utf-8")

    n_ok = n_err = 0
    with (run_dir / "responses.jsonl").open("w", encoding="utf-8") as fh:
        for n, case in enumerate(cases, 1):
            body = compat_request_body(case, args, config)
            record = {
                "case_number": case["case_number"],
                "item_id": case["item_id"],
                "task": case["task"],
                "split": case["split"],
                "sibling_case_numbers": case["sibling_case_numbers"],
                "extract_shown": case["extract"],
                "request": body,
                "requested_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            try:
                result, attempts, err = compat_call(transport, body)
                record["attempts"] = attempts
                if err is not None:
                    record["response"], record["parsed"] = None, None
                    record["error"], record["stop_reason"] = err, None
                else:
                    payload = result["payload"]
                    record["response"] = payload
                    record["http_status"] = result.get("http_status")
                    record["provider_headers"] = result.get("headers") or {}
                    record["stop_reason"] = openai_compat._finish_reason(payload)
                    record["parsed"], record["error"] = parse_probe_compat(payload)
            except Exception as exc:  # sanitized transport already; keep going
                record["response"], record["parsed"] = None, None
                record["error"] = f"{type(exc).__name__}: {exc}"
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            if record.get("error"):
                n_err += 1
            else:
                n_ok += 1
            print(f"[{n}/{len(cases)}] {case['case_number']} "
                  f"-> {record.get('parsed') or record.get('error')}")

    print(f"\nrun dir : {run_dir}")
    print(f"calls   : {len(cases)}  parsed={n_ok}  errors={n_err}")
    print(f"score   : python3 bench/probe.py --score --run {run_dir}")
    return 0


# --- scoring -----------------------------------------------------------------

def normalise_case_number(text):
    """'auth 3572/10/2021' -> 'AUTH/3572/10/21'. None if it is not one."""
    if not text:
        return None
    match = CASE_NUM_RE.search(text)
    return normalise_match(match) if match else None


def normalise_match(match):
    prefix, number, month, year = match.groups()
    return f"{prefix.upper()}/{int(number)}/{int(month)}/{year[-2:]}"


def all_case_numbers(text):
    """Every case-number-shaped token in a blob, normalised."""
    return [normalise_match(m) for m in CASE_NUM_RE.finditer(text or "")]


def classify(parsed, true_numbers, extract=""):
    """Probe answer -> (status, evidence). true_numbers = case + folded siblings.

    A stated number that matches wins outright, even if `recognised` is false:
    producing the right number IS the memorisation signal, and the flag is only
    the model's self-report about it.
    """
    truth = {normalise_case_number(n) for n in true_numbers}
    truth.discard(None)
    stated_raw = (parsed.get("case_number") or "").strip()
    stated = all_case_numbers(stated_raw)
    matched = next((s for s in stated if s in truth), None)
    in_extract = set(all_case_numbers(extract))

    if matched:
        status = STATUS_CONTAMINATED
    elif parsed.get("recognised"):
        status = STATUS_RECOGNISED
    else:
        status = STATUS_CLEAN

    evidence = {
        "recognised": bool(parsed.get("recognised")),
        "stated_case_number": stated_raw or None,
        "stated_normalised": stated,
        "matched_number": matched,
        "ruling_summary": parsed.get("ruling_summary"),
        # Extracts sometimes cite OTHER cases as precedent, and could in
        # principle cite their own. Both flags let the reader tell recall from
        # copying without re-reading the prompt.
        "stated_number_in_extract": bool(set(stated) & in_extract),
        "true_number_in_extract": bool(truth & in_extract),
    }
    return status, evidence


def score_run(run_dir, items_path, probes_path):
    """Classify a probe run and merge it into probes.jsonl. Returns the rows."""
    responses = load_jsonl(run_dir / "responses.jsonl")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    items = load_jsonl(items_path)

    truths = {}
    for item in items:
        truths.setdefault(item["case_number"], set()).add(item["case_number"])
        for sib in item.get("sibling_case_numbers") or []:
            truths[item["case_number"]].add(sib)

    model = manifest.get("model", "unknown")
    rows, unscored = [], []
    for rec in responses:
        case_number = rec["case_number"]
        if not rec.get("parsed"):
            unscored.append((case_number, rec.get("error") or "unparsed"))
            continue
        true_numbers = truths.get(case_number, {case_number})
        status, evidence = classify(rec["parsed"], true_numbers, rec.get("extract_shown", ""))
        evidence["item_id"] = rec.get("item_id")
        evidence["true_numbers"] = sorted(true_numbers)
        rows.append({
            "case_number": case_number,
            "probe_status": status,
            "model": model,
            "run_dir": str(run_dir),
            "evidence": evidence,
            "scored_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
    return rows, unscored, manifest


def merge_probes(rows, probes_path):
    """Append to probes.jsonl, deduping on (case_number, model).

    A re-probe of the same case with the same model replaces the older row --
    otherwise a rerun would leave two contradictory verdicts and the joiner
    would have to guess. A different model appends alongside.
    """
    path = pathlib.Path(probes_path)
    existing = load_jsonl(path) if path.exists() else []
    incoming = {(r["case_number"], r["model"]): r for r in rows}
    kept = [r for r in existing if (r.get("case_number"), r.get("model")) not in incoming]
    n_replaced = len(existing) - len(kept)
    merged = kept + [incoming[k] for k in sorted(incoming)]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                           for r in merged), encoding="utf-8")
    tmp.replace(path)
    return len(merged), n_replaced


# --- self-test ---------------------------------------------------------------

def self_test(items_path):
    """Unit-test number normalisation, classification and extract isolation.

    The extract check runs against the real bank: for every T2 item the
    reconstruction must reproduce extract_text byte for byte (it is already
    complaint-only), and no reconstructed extract may contain a response or
    panel-ruling banner. That is the invariant that keeps the probe honest.
    """
    failures = []

    print("case-number normalisation")
    for raw, want in [
        ("AUTH/3572/10/21", "AUTH/3572/10/21"),
        ("auth 3572/10/2021", "AUTH/3572/10/21"),
        ("Case AUTH-3572-10-21", "AUTH/3572/10/21"),
        ("The case number is AUTH/3572/10/21.", "AUTH/3572/10/21"),
        ("CASE/1234/05/19", "CASE/1234/5/19"),
        ("AUTH/3572/010/21", "AUTH/3572/10/21"),
        ("unknown", None), ("", None), (None, None),
        ("I think it was from 2021", None),
    ]:
        got = normalise_case_number(raw)
        ok = got == want
        failures += [] if ok else [f"normalise({raw!r}) -> {got!r}, want {want!r}"]
        print(f"  {'ok' if ok else 'FAIL':>4}  {str(raw)[:38]:38} -> {got}")

    print("\nclassification")
    truth = {"AUTH/3572/10/21", "AUTH/3573/10/21"}   # case + a folded sibling
    for parsed, want, note in [
        ({"recognised": True, "case_number": "AUTH/3572/10/21"}, STATUS_CONTAMINATED, "exact"),
        ({"recognised": True, "case_number": "auth 3572/10/2021"}, STATUS_CONTAMINATED, "sloppy"),
        ({"recognised": True, "case_number": "AUTH/3573/10/21"}, STATUS_CONTAMINATED, "sibling"),
        ({"recognised": False, "case_number": "AUTH/3572/10/21"}, STATUS_CONTAMINATED,
         "right number, denies recognising"),
        ({"recognised": True, "case_number": "AUTH/9999/1/99"}, STATUS_RECOGNISED, "wrong number"),
        ({"recognised": True, "case_number": None}, STATUS_RECOGNISED, "no number"),
        ({"recognised": False, "case_number": None}, STATUS_CLEAN, "unknown"),
        ({"recognised": False, "case_number": "AUTH/9999/1/99"}, STATUS_CLEAN, "wrong, no claim"),
    ]:
        got, _ = classify(parsed, truth)
        ok = got == want
        failures += [] if ok else [f"classify({note}) -> {got}, want {want}"]
        print(f"  {'ok' if ok else 'FAIL':>4}  {note:32} -> {got}")

    _, ev = classify({"recognised": True, "case_number": "AUTH/3488/3/21"}, {"AUTH/3661/6/22"},
                     extract="...found in breach previously in Case AUTH/3488/3/21 where...")
    ok = ev["stated_number_in_extract"] and not ev["true_number_in_extract"]
    failures += [] if ok else ["copied-from-extract flags wrong"]
    print(f"  {'ok' if ok else 'FAIL':>4}  {'number copied from the extract':32} -> flagged")

    path = pathlib.Path(items_path)
    if not path.exists():
        print(f"\nextract isolation: SKIPPED ({items_path} not found)")
    else:
        items = load_jsonl(path)
        n_triage = n_multi = 0
        for item in items:
            if item["task"] == "T2":
                n_triage += 1
                if complaint_only(item) != item["inputs"]["extract_text"]:
                    failures.append(f"T2 {item['item_id']}: reconstruction != extract_text")
            elif item["task"] == "T1":
                got = complaint_only(item)
                if got is None:
                    failures.append(f"T1 {item['item_id']}: no complaint span recovered")
                    continue
                if HEADS["response"] in got or HEADS["panel_ruling"] in got:
                    failures.append(f"T1 {item['item_id']}: reconstruction leaks a non-complaint chunk")
                if item["inputs"]["extract_text"].count(HEADS["response"]) > 1:
                    n_multi += 1
            elif item["task"] == "T3":
                if complaint_only(item) is not None:
                    failures.append(f"T3 {item['item_id']}: must be refused, it quotes the ruling")
        print(f"\nextract isolation ({path.name}: {len(items)} items)")
        print(f"  ok    {n_triage} T2 reconstructions are byte-identical to extract_text")
        print(f"  ok    no reconstruction contains a response or panel-ruling banner "
              f"({n_multi} T1 item(s) interleave chunks, the case this guards)")

    if failures:
        print(f"\nFAILURES: {len(failures)}")
        for f in failures:
            print(f"  {f}")
        return 1
    print("\nOK: normalisation, classification and extract isolation all hold.")
    return 0


# --- main --------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items", default=str(DEFAULT_ITEMS))
    ap.add_argument("--model", default="",
                    help="exact model identity; required with --live")
    ap.add_argument("--provider", default="", choices=("xai", "openrouter", "deepseek"),
                    help="route live calls through the OpenAI-compat adapter config; "
                         "--model must be pinned in openai_compat.MODEL_CONFIGS")
    ap.add_argument("--limit", type=int, default=10,
                    help="cases to probe (default %(default)s); 0 = all")
    ap.add_argument("--splits", default="", help="comma-separated split filter")
    ap.add_argument("--cases", default="",
                    help="comma-separated case_number filter, applied after dedupe and "
                         "before --limit (for retrying failed cases in a fresh run dir)")
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--thinking", choices=("adaptive", "disabled", "unset"), default="adaptive")
    ap.add_argument("--effort", default="", help="output_config.effort (low|medium|high|xhigh|max)")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="(default) print prompts, make no call")
    ap.add_argument("--live", dest="dry_run", action="store_false", help="actually call the API")
    ap.add_argument("--run-dir", default="", help="override bench/runs/probe-<ts>-<pid>")
    ap.add_argument("--score", action="store_true",
                    help="classify an existing probe run and merge into probes.jsonl")
    ap.add_argument("--run", default="", help="--score: the probe run directory")
    ap.add_argument("--probes", default=str(DEFAULT_PROBES), help="default %(default)s")
    ap.add_argument("--self-test", action="store_true",
                    help="unit-test normalisation, classification and extract isolation, then exit")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test(args.items)
    if args.score:
        return main_score(args)

    if not args.model:
        if not args.dry_run:
            raise SystemExit("--live requires an explicit --model identity")
        args.model = runner.UNSELECTED_MODEL

    if args.thinking == "adaptive" and any(args.model.startswith(p)
                                           for p in runner.NO_ADAPTIVE_THINKING):
        print(f"NOTE: {args.model} does not support adaptive thinking; sending no thinking param.",
              file=sys.stderr)
        args.thinking = "unset"

    config = None
    if args.provider:
        if args.effort:
            raise SystemExit("--effort is unsupported with --provider; reasoning is "
                             "pinned per model in openai_compat.MODEL_CONFIGS")
        if args.thinking == "disabled":
            raise SystemExit("--thinking disabled is unsupported with --provider; thinking "
                             "is pinned per model in openai_compat.MODEL_CONFIGS")
        args.thinking = "unset"  # the compat body carries the MODEL_CONFIGS pin instead
        try:
            config = openai_compat.require_model_config(args.provider, args.model)
        except openai_compat.AdapterError as exc:
            raise SystemExit(str(exc))

    splits = [s.strip() for s in args.splits.split(",") if s.strip()]
    items = [it for it in load_jsonl(args.items) if not splits or it["split"] in splits]
    if not items:
        raise SystemExit(f"no items matched (items={args.items} splits={splits})")
    cases, skipped = cases_from_items(items)
    cases_filter = None
    if args.cases:
        wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
        missing = sorted(wanted - {c["case_number"] for c in cases})
        if missing:
            raise SystemExit(f"--cases: {len(missing)} requested case(s) are not probeable "
                             f"from this item set (e.g. {missing[0]})")
        cases = [c for c in cases if c["case_number"] in wanted]
        cases_filter = sorted(wanted)
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        raise SystemExit("no probeable cases (every candidate lacks a complaint-only extract)")

    if args.dry_run:
        for case in cases:
            print("=" * 78)
            print(f"case {case['case_number']}  split={case['split']}  "
                  f"via item {case['item_id']} ({case['task']})  "
                  f"siblings={case['sibling_case_numbers'] or 'none'}  "
                  f"[CASE NUMBER WITHHELD FROM THE MODEL]")
            print("-" * 78)
            if args.provider:
                body = compat_request_body(case, args, config)
                print("SYSTEM:")
                print(body["messages"][0]["content"])
                print("-" * 78)
                print("USER:")
                print(body["messages"][1]["content"])
                print("-" * 78)
                print("CHAT-COMPLETIONS BODY (messages elided above):")
                print(json.dumps({k: v for k, v in body.items() if k != "messages"},
                                 indent=1, sort_keys=True))
                continue
            params = request_params(case, args)
            print("SYSTEM:")
            print(params["system"])
            print("-" * 78)
            print("USER:")
            print(params["messages"][0]["content"])
            print("-" * 78)
            print("REQUEST PARAMS (prompt fields elided above):")
            print(json.dumps({k: v for k, v in params.items() if k not in ("system", "messages")},
                             indent=1, sort_keys=True))
        print("=" * 78)
        if args.provider:
            env_key = openai_compat.PROVIDERS[args.provider]["env_key"]
            print(f"DRY RUN: {len(cases)} case(s), 1 call each, model {args.model} "
                  f"via {args.provider} (OpenAI-compat). "
                  f"No API call was made and nothing was written.")
            if skipped:
                print(f"skipped   : {len(skipped)} case(s) with no complaint-only extract "
                      f"(e.g. {skipped[0][0]})")
            print(f"Re-run with --live (and {env_key} in the environment or the repo .env) "
                  f"to execute.")
            return 0
        print(f"DRY RUN: {len(cases)} case(s), 1 call each, model {args.model}. "
              f"No API call was made and nothing was written.")
        if skipped:
            print(f"skipped   : {len(skipped)} case(s) with no complaint-only extract "
                  f"(e.g. {skipped[0][0]})")
        print("Re-run with --live (and ANTHROPIC_API_KEY set) to execute.")
        return 0

    if args.provider:
        return compat_live(cases, skipped, splits, cases_filter, args, config)

    use_openai = runner.is_openai_model(args.model)
    if use_openai:
        if not load_env_key("OPENAI_API_KEY"):
            raise SystemExit("--live with an OpenAI model requires OPENAI_API_KEY in the "
                             "environment or the repo .env")
        try:
            import openai
        except ImportError:
            raise SystemExit("--live needs the openai SDK: uv run --with openai python bench/probe.py --live ...")
        anthropic = None
    else:
        if not load_env_key("ANTHROPIC_API_KEY"):
            raise SystemExit("--live requires ANTHROPIC_API_KEY in the environment or the repo .env")
        try:
            import anthropic
        except ImportError:
            raise SystemExit("--live needs the anthropic SDK: uv run --with anthropic python bench/probe.py --live ...")
        openai = None

    # pid suffix for the same reason run.py carries one: two runs launched in
    # the same second must not clobber each other's responses.jsonl.
    run_dir = pathlib.Path(args.run_dir) if args.run_dir else RUNS / (
        "probe-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{os.getpid()}")
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": "memorisation_probe",
        "items_path": str(pathlib.Path(args.items).resolve()),
        "items_sha256": runner.sha256(args.items),
        "n_cases": len(cases),
        "n_calls": len(cases),
        "n_skipped_cases": len(skipped),
        "model": args.model,
        "max_tokens": args.max_tokens,
        "thinking": args.thinking,
        "effort": args.effort or None,
        "splits_filter": splits,
        "cases_filter": cases_filter,
        "provider": "openai" if use_openai else "anthropic",
        "sdk_version": getattr(openai if use_openai else anthropic, "__version__", "unknown"),
        "python": sys.version.split()[0],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                                           encoding="utf-8")

    client = openai.OpenAI() if use_openai else anthropic.Anthropic()
    n_ok = n_err = 0
    with (run_dir / "responses.jsonl").open("w", encoding="utf-8") as fh:
        for n, case in enumerate(cases, 1):
            params = request_params(case, args)
            record = {
                "case_number": case["case_number"],
                "item_id": case["item_id"],
                "task": case["task"],
                "split": case["split"],
                "sibling_case_numbers": case["sibling_case_numbers"],
                # kept so --score can tell recall from copying without
                # re-deriving the extract from the item bank
                "extract_shown": case["extract"],
                "request": params,
                "requested_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            try:
                if use_openai:
                    req = runner.to_openai(params)
                    if args.effort:
                        # runner.to_openai drops output_config.effort (it predates
                        # reasoning models); send what the manifest records.
                        req["reasoning_effort"] = args.effort
                    resp = client.chat.completions.create(**req)
                    payload = resp.model_dump()
                    record["response"] = payload
                    record["stop_reason"] = (payload["choices"][0].get("finish_reason")
                                             if payload.get("choices") else None)
                    record["parsed"], record["error"] = parse_probe_openai(payload)
                else:
                    resp = client.messages.create(**params)
                    payload = resp.to_dict()
                    record["response"] = payload
                    record["stop_reason"] = payload.get("stop_reason")
                    if payload.get("stop_reason") == "refusal":
                        record["parsed"], record["error"] = None, "stop_reason=refusal"
                    else:
                        record["parsed"], record["error"] = parse_probe(payload)
            except Exception as exc:  # network, rate limit, validation -- keep going
                record["response"], record["parsed"] = None, None
                record["error"] = f"{type(exc).__name__}: {exc}"
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            if record.get("error"):
                n_err += 1
            else:
                n_ok += 1
            print(f"[{n}/{len(cases)}] {case['case_number']} "
                  f"-> {record.get('parsed') or record.get('error')}")

    print(f"\nrun dir : {run_dir}")
    print(f"calls   : {len(cases)}  parsed={n_ok}  errors={n_err}")
    print(f"score   : python3 bench/probe.py --score --run {run_dir}")
    return 0


def main_score(args):
    if not args.run:
        raise SystemExit("--score needs --run <probe run dir>")
    run_dir = pathlib.Path(args.run)
    if not (run_dir / "responses.jsonl").exists():
        raise SystemExit(f"{run_dir / 'responses.jsonl'} not found")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")) \
        if (run_dir / "manifest.json").exists() else {}
    items_path = pathlib.Path(args.items if args.items != str(DEFAULT_ITEMS)
                              else manifest.get("items_path") or DEFAULT_ITEMS)
    if not items_path.exists():
        raise SystemExit(f"item bank not found: {items_path}")

    rows, unscored, manifest = score_run(run_dir, items_path, args.probes)
    counts = {}
    for row in rows:
        counts[row["probe_status"]] = counts.get(row["probe_status"], 0) + 1
    n_total, n_replaced = merge_probes(rows, args.probes)

    print(f"run       : {run_dir}")
    print(f"model     : {manifest.get('model', '?')}   items: {items_path}")
    print(f"scored    : {len(rows)} case(s); {len(unscored)} unscored")
    if unscored:
        print(f"unscored  : e.g. {unscored[0][0]} -- {unscored[0][1]}")
    print()
    for status in (STATUS_CONTAMINATED, STATUS_RECOGNISED, STATUS_CLEAN):
        n = counts.get(status, 0)
        share = f"{n / len(rows):.3f}" if rows else "n/a"
        print(f"  {status:<22} {n:>4}  ({share})")

    leaky = [r for r in rows if r["evidence"]["true_number_in_extract"]]
    if leaky:
        print(f"\nWARNING: {len(leaky)} case(s) show their own case number inside the extract "
              f"they were probed with. A 'contaminated' verdict on those is unsafe -- the model "
              f"could have read it off the prompt. e.g. {leaky[0]['case_number']}")
    copied = [r for r in rows if r["evidence"]["stated_number_in_extract"]]
    if copied:
        print(f"NOTE: {len(copied)} case(s) stated a number that appears in the extract "
              f"(complainants cite earlier cases as precedent) -- recall vs copying.")

    print(f"\nprobes.jsonl: {n_total} row(s) total, {len(rows)} written, "
          f"{n_replaced} replaced ({args.probes})")
    print("join to items on case_number; see this file's docstring for the enum caveat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
