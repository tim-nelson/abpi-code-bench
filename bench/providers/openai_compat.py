#!/usr/bin/env python3
"""Strict OpenAI-compatible chat-completions adapter for canonical exports.

This module serves providers that expose an OpenAI-compatible
``POST {base_url}/chat/completions`` endpoint but no batch API (xAI,
DeepSeek, OpenRouter).  The benchmark planner (``bench/run.py``) owns prompt
construction, stable call identities and the append-only result ledger; this
adapter only:

* validates canonical ``pmcpa.zero-provider.v2`` JSONL exports;
* translates them mechanically to chat-completion bodies pinned per
  ``(provider, model)`` in ``MODEL_CONFIGS`` (byte-identical canonical
  repeats map to byte-identical provider bodies);
* executes one explicitly authorised smoke call, or a resumable ``run-live``
  pass under a required ``--max-calls`` hard cap — strictly sequential by
  default, optionally up to ``--concurrency N`` calls in flight on worker
  threads with every receipt still appended whole-line + fsync by exactly
  one thread at a time; and
* appends normalized receipts that ``bench/run.py --import-results`` and
  ``bench/p3_plan.py --import-results`` accept unchanged.

Live commands require ``--execute`` and read only the registered provider
key (``XAI_API_KEY`` / ``DEEPSEEK_API_KEY`` / ``OPENROUTER_API_KEY``) from
the process environment or a literal repo ``.env``.  Keys and request bodies
are never printed.  No third-party SDK is used; transport is stdlib urllib.

Retry policy (run-live and smoke): exponential backoff ONLY on HTTP 429,
HTTP 5xx and transport errors, at most ``RETRY_MAX_ATTEMPTS`` total attempts
per call, honoring a numeric ``Retry-After`` header.  Any other 4xx is a
terminal receipt: recorded once, never retried, never re-billed.  Responses
that fail JSON/schema validation become quarantined receipts (fail closed,
never guessed); resume never re-submits a call_id that already has any
receipt in the output file, regardless of receipt order.

Under ``--concurrency N`` (default 1 = the original sequential path) the
per-call retry policy is unchanged; additionally all workers share one 429
cooldown deadline so they back off collectively, and a call that exhausts
its retries (429/5xx/transport) stops NEW submissions while in-flight calls
finish and are receipted.  Receipts are only ever written after a response
(or terminal failure) arrives — never pre-written — so a killed run leaves
unreceipted calls retriable, exactly as in the sequential path.

Provider facts verified against vendor docs on 2026-08-16:

* xAI - https://docs.x.ai/docs/models , https://docs.x.ai/docs/guides/structured-outputs ,
  https://docs.x.ai/docs/api-reference
  Base URL ``https://api.x.ai/v1``; ``grok-4.6`` is the current flagship and
  supports strict ``response_format: json_schema``.  ``reasoning_effort`` is
  documented "Only supported by grok-4.3", so no reasoning field is pinned
  for ``grok-4.6``.  ``max_tokens`` is deprecated there in favour of
  ``max_completion_tokens``.  List price: $2/$6 per Mtok in/out (<200k
  context; $4/$12 above).
* DeepSeek - https://api-docs.deepseek.com/ , https://api-docs.deepseek.com/guides/json_mode ,
  https://api-docs.deepseek.com/guides/reasoning_model ,
  https://api-docs.deepseek.com/quick_start/pricing
  Current models ``deepseek-v4-flash`` and ``deepseek-v4-pro``; both support
  thinking (default) via the documented ``{"thinking": {"type": "enabled"}}``
  request field, which this adapter pins explicitly.  DeepSeek documents
  only ``response_format: {"type": "json_object"}`` (no JSON-schema
  enforcement) and requires the word "json" plus the desired format in the
  prompt, so these models are pinned ``json_object_prompt_schema``: the
  JSON Schema itself is appended deterministically to the system message.
  A worked example instance is deliberately NOT embedded because a concrete
  answer/probability would anchor the elicited verdict.  List price
  (cache-miss input/output per Mtok): v4-flash $0.14/$0.28, v4-pro
  $0.435/$0.87.
* OpenRouter - https://openrouter.ai/docs/features/structured-outputs ,
  https://openrouter.ai/docs/features/provider-routing ,
  https://openrouter.ai/moonshotai/kimi-k3 , https://openrouter.ai/z-ai/glm-5.2
  Base URL ``https://openrouter.ai/api/v1``; ``response_format`` with
  ``{"type": "json_schema", "json_schema": {name, strict, schema}}`` is
  passed through, and ``provider.require_parameters: true`` restricts
  routing to upstream hosts that support every request parameter (fail
  closed; always sent).  A single upstream host is pinned with the
  documented ``provider`` routing object ``{"order": [slug],
  "allow_fallbacks": false}`` (``--pin-host``); the pinned host is recorded
  in every receipt.  Verified slugs: ``moonshotai/kimi-k3`` ($2.80/$14 per
  Mtok list) and ``z-ai/glm-5.2`` (listed average $0.3248/$1.021 per Mtok,
  flagged as promotional; re-verify at the paid gate).

Examples::

    python3 -B bench/providers/openai_compat.py smoke \
      --input /tmp/one-canonical-call.jsonl --output /tmp/one-receipt.jsonl \
      --provider xai --execute

    python3 -B bench/providers/openai_compat.py run-live \
      --canonical /tmp/canonical.jsonl --output /tmp/normalized.jsonl \
      --provider openrouter --pin-host moonshotai --max-calls 25 --execute
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any


ADAPTER_CONTRACT = "pmcpa.openai-compat.v1"
NORMALIZED_CONTRACT = "pmcpa.openai-compat-normalized.v1"
CANONICAL_CONTRACT = "pmcpa.zero-provider.v2"

CHAT_ENDPOINT = "/chat/completions"
MAX_OUTPUT_TOKENS = 4096
STRUCTURED_OUTPUT_NAME = "pmcpa_bench_output"
P3_AGGREGATION = "linear_probability_pool"
P4_AGGREGATION = "cost_sweep"
P4_DECISIONS = ["answer", "refer"]

RETRY_MAX_ATTEMPTS = 4
RETRY_BASE_SECONDS = 2.0
RETRY_MAX_SLEEP_SECONDS = 300.0
PROGRESS_EVERY = 25

PROVIDERS: dict[str, dict[str, Any]] = {
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "env_key": "XAI_API_KEY",
        "provider_routing": False,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
        "provider_routing": False,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        # Documented request field for pinning one upstream host:
        # {"provider": {"order": [slug], "allow_fallbacks": false}}.
        "provider_routing": True,
    },
}

# Fail-closed registry: a (provider, model) pair absent from this table is
# refused.  ``structured_output`` is either strict "json_schema" or the
# documented DeepSeek fallback "json_object_prompt_schema"; models whose
# JSON-schema support could not be verified are excluded, never degraded.
MODEL_CONFIGS: dict[tuple[str, str], dict[str, Any]] = {
    ("xai", "grok-4.6"): {
        "structured_output": "json_schema",
        "max_tokens_field": "max_completion_tokens",
        "thinking": None,
        "reasoning_effort": None,  # documented only for grok-4.3
    },
    ("deepseek", "deepseek-v4-flash"): {
        "structured_output": "json_object_prompt_schema",
        "max_tokens_field": "max_tokens",
        "thinking": {"type": "enabled"},
        "reasoning_effort": None,
    },
    ("deepseek", "deepseek-v4-pro"): {
        "structured_output": "json_object_prompt_schema",
        "max_tokens_field": "max_tokens",
        "thinking": {"type": "enabled"},
        "reasoning_effort": None,
    },
    ("openrouter", "moonshotai/kimi-k3"): {
        "structured_output": "json_schema",
        "max_tokens_field": "max_tokens",
        "thinking": None,
        "reasoning_effort": None,
    },
    ("openrouter", "z-ai/glm-5.2"): {
        "structured_output": "json_schema",
        "max_tokens_field": "max_tokens",
        "thinking": None,
        "reasoning_effort": None,
    },
    # Google-authored models routed via OpenRouter (verified 2026-08-16:
    # openrouter.ai/google/gemini-3.7-flash at $0.375/$1.875 promo,
    # openrouter.ai/google/gemini-3.1-pro-preview at $2/$12).  Upstream is
    # Google itself, so no --pin-host is required for provenance; the native
    # gemini_generate adapter remains the batch-discount path.
    ("openrouter", "google/gemini-3.7-flash"): {
        "structured_output": "json_schema",
        "max_tokens_field": "max_tokens",
        "thinking": None,
        "reasoning_effort": None,
    },
    ("openrouter", "google/gemini-3.1-pro-preview"): {
        "structured_output": "json_schema",
        "max_tokens_field": "max_tokens",
        "thinking": None,
        "reasoning_effort": None,
    },
    # DeepSeek via OpenRouter (verified 2026-08-16:
    # openrouter.ai/deepseek/deepseek-v4-pro at $0.3969/$0.7938 promo, below
    # the native API's cache-miss rate).  require_parameters routes to
    # json_schema-capable hosts, unlike the native endpoint's json_object.
    ("openrouter", "deepseek/deepseek-v4-pro"): {
        "structured_output": "json_schema",
        "max_tokens_field": "max_tokens",
        "thinking": None,
        "reasoning_effort": None,
    },
}

# Documented DeepSeek JSON-mode contract: the word "json" must appear in the
# prompt together with the desired output format.  The schema is embedded
# byte-deterministically; no example instance is included (anchoring risk).
JSON_OBJECT_SCHEMA_INSTRUCTION = (
    "\n\nOUTPUT FORMAT\n"
    "Reply with a single json object on one line and nothing else. The json "
    "object must contain exactly the fields required by this JSON Schema, "
    "with no additional fields:\n"
)

EXPECTED_ANSWERS = {
    "T1": ["breach", "no_breach"],
    "T2": ["breach", "no_breach"],
    "T3": ["upheld", "overturned"],
}

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


class AdapterError(ValueError):
    """A local contract, identity, or provider-response validation failure."""


class ProviderHTTPError(RuntimeError):
    """A sanitized provider HTTP failure which never embeds credentials."""

    def __init__(self, status: int | None, request_id: str | None,
                 payload: Any, message: str, *,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.request_id = request_id
        self.payload = payload
        self.safe_message = message
        self.retry_after = retry_after


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def strict_json_loads(text: str, context: str) -> Any:
    def reject_constant(value: str) -> None:
        raise AdapterError(f"{context}: non-standard JSON constant {value!r}")

    try:
        return json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"{context}: invalid JSON: {exc}") from exc


def read_jsonl(path: pathlib.Path | str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path = pathlib.Path(path)
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            row = strict_json_loads(line, f"{path}:{lineno}")
            if not isinstance(row, dict):
                raise AdapterError(f"{path}:{lineno}: each JSONL row must be an object")
            rows.append(row)
    if not rows:
        raise AdapterError(f"{path}: no JSONL rows")
    return rows


def reserve_text_output(path: pathlib.Path | str):
    """Reserve an immutable receipt path before any provider action."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise AdapterError(f"refusing to overwrite {path}") from exc


def _append_receipt(fh: Any, row: dict[str, Any]) -> None:
    fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    fh.flush()
    os.fsync(fh.fileno())


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdapterError(f"{field} must be a non-empty string")
    return value


def _validate_schema_definition(schema: Any, path: str = "schema") -> None:
    """Validate the JSON-Schema subset emitted by the canonical runner.

    Local validation exists so a malformed or silently broadened schema never
    reaches a paid call, whether or not the provider validates server-side.
    """
    if not isinstance(schema, dict):
        raise AdapterError(f"{path} must be an object")
    schema_type = schema.get("type")
    if schema_type not in ("object", "array", "string", "number", "integer",
                           "boolean", "null"):
        raise AdapterError(f"{path}.type is unsupported: {schema_type!r}")
    allowed_common = {"type", "description", "enum"}
    if schema_type == "object":
        allowed = allowed_common | {"properties", "required", "additionalProperties"}
        unknown = set(schema) - allowed
        if unknown:
            raise AdapterError(f"{path} has unsupported keyword(s): {sorted(unknown)}")
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict) or not properties:
            raise AdapterError(f"{path}.properties must be a non-empty object")
        if not isinstance(required, list) or any(not isinstance(v, str) for v in required):
            raise AdapterError(f"{path}.required must be a string array")
        if len(required) != len(set(required)):
            raise AdapterError(f"{path}.required contains duplicates")
        if set(required) != set(properties):
            raise AdapterError(f"{path}: every property must be required for strict output")
        if schema.get("additionalProperties") is not False:
            raise AdapterError(f"{path}.additionalProperties must be false")
        for name, child in properties.items():
            _require_string(name, f"{path}.properties key")
            _validate_schema_definition(child, f"{path}.properties.{name}")
    elif schema_type == "array":
        allowed = allowed_common | {"items"}
        unknown = set(schema) - allowed
        if unknown:
            raise AdapterError(f"{path} has unsupported keyword(s): {sorted(unknown)}")
        if "items" not in schema:
            raise AdapterError(f"{path}.items is required")
        _validate_schema_definition(schema["items"], f"{path}.items")
    else:
        unknown = set(schema) - allowed_common
        if unknown:
            raise AdapterError(f"{path} has unsupported keyword(s): {sorted(unknown)}")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise AdapterError(f"{path}.enum must be a non-empty array")


def _validate_instance(value: Any, schema: dict[str, Any], path: str = "output") -> None:
    schema_type = schema["type"]
    if schema_type == "object":
        if not isinstance(value, dict):
            raise AdapterError(f"{path} must be an object")
        properties = schema["properties"]
        missing = set(schema["required"]) - set(value)
        if missing:
            raise AdapterError(f"{path} is missing required field(s): {sorted(missing)}")
        unknown = set(value) - set(properties)
        if unknown:
            raise AdapterError(f"{path} has unexpected field(s): {sorted(unknown)}")
        for name, child in properties.items():
            if name in value:
                _validate_instance(value[name], child, f"{path}.{name}")
    elif schema_type == "array":
        if not isinstance(value, list):
            raise AdapterError(f"{path} must be an array")
        for index, child in enumerate(value):
            _validate_instance(child, schema["items"], f"{path}[{index}]")
    elif schema_type == "string":
        if not isinstance(value, str):
            raise AdapterError(f"{path} must be a string")
    elif schema_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise AdapterError(f"{path} must be a number")
    elif schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise AdapterError(f"{path} must be an integer")
    elif schema_type == "boolean":
        if not isinstance(value, bool):
            raise AdapterError(f"{path} must be a boolean")
    elif schema_type == "null":
        if value is not None:
            raise AdapterError(f"{path} must be null")
    if "enum" in schema and value not in schema["enum"]:
        raise AdapterError(f"{path} value {value!r} is outside the schema enum")


def require_provider(provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise AdapterError(
            f"unknown provider {provider!r}; registered: {sorted(PROVIDERS)}")
    return PROVIDERS[provider]


def require_model_config(provider: str, model: str) -> dict[str, Any]:
    require_provider(provider)
    config = MODEL_CONFIGS.get((provider, model))
    if config is None:
        supported = sorted(m for (p, m) in MODEL_CONFIGS if p == provider)
        raise AdapterError(
            f"model {model!r} is not pinned for provider {provider!r}; "
            f"supported: {supported}")
    return config


def validate_pinned_host(provider: str, pinned_host: str | None) -> None:
    if pinned_host is None:
        return
    if not require_provider(provider).get("provider_routing"):
        raise AdapterError(
            f"--pin-host is only supported for providers with documented "
            f"provider routing (openrouter), not {provider!r}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9/._-]{0,63}", pinned_host):
        raise AdapterError(f"invalid upstream provider slug {pinned_host!r}")


def validate_canonical_row(row: dict[str, Any], provider: str) -> None:
    require_provider(provider)
    contract = _require_string(row.get("schema_version"), "schema_version")
    if contract != CANONICAL_CONTRACT:
        raise AdapterError(
            f"unsupported canonical contract {contract!r}; expected {CANONICAL_CONTRACT!r}")

    call_id = _require_string(row.get("call_id"), "call_id")
    if row.get("custom_id") != call_id:
        raise AdapterError(f"{call_id}: call_id/custom_id mismatch")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", call_id):
        raise AdapterError(f"{call_id}: custom_id must match [A-Za-z0-9_-]{{1,64}}")

    model = _require_string(row.get("model"), f"{call_id}.model")
    require_model_config(provider, model)
    protocol = _require_string(row.get("protocol"), f"{call_id}.protocol")
    if protocol not in ("P1", "P2", "P3", "P4"):
        raise AdapterError(f"{call_id}: only active P1/P2/P3/P4 calls can be submitted")
    if protocol == "P3":
        if row.get("protocol_condition") is not None:
            raise AdapterError(f"{call_id}: native P3 must not carry protocol_condition")
        if row.get("aggregation") != P3_AGGREGATION:
            raise AdapterError(
                f"{call_id}: native P3 requires aggregation={P3_AGGREGATION!r}")
    if protocol == "P4":
        if row.get("protocol_condition") is not None:
            raise AdapterError(f"{call_id}: native P4 must not carry protocol_condition")
        if row.get("aggregation") != P4_AGGREGATION:
            raise AdapterError(
                f"{call_id}: native P4 requires aggregation={P4_AGGREGATION!r}")
    task = _require_string(row.get("task"), f"{call_id}.task")
    if task not in EXPECTED_ANSWERS:
        raise AdapterError(f"{call_id}: unsupported active task {task!r}")
    _require_string(row.get("config_hash"), f"{call_id}.config_hash")

    request = row.get("request")
    if not isinstance(request, dict):
        raise AdapterError(f"{call_id}.request must be an object")
    allowed_request_keys = {"model", "max_tokens", "system", "messages", "output_config"}
    unknown = set(request) - allowed_request_keys
    if unknown:
        if "temperature" in unknown or "top_p" in unknown:
            raise AdapterError(
                f"{call_id}: this adapter pins no sampling parameters; "
                "temperature/top_p must be unset")
        if "thinking" in unknown:
            raise AdapterError(
                f"{call_id}: canonical thinking must be unset; reasoning is "
                "pinned per model in MODEL_CONFIGS")
        raise AdapterError(f"{call_id}.request has unsupported configuration: {sorted(unknown)}")
    if request.get("model") != model:
        raise AdapterError(f"{call_id}.request.model must match top-level model {model!r}")
    if request.get("max_tokens") != MAX_OUTPUT_TOKENS:
        raise AdapterError(f"{call_id}.request.max_tokens must be exactly {MAX_OUTPUT_TOKENS}")
    system = _require_string(request.get("system"), f"{call_id}.request.system")
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise AdapterError(f"{call_id}.request.messages must be a non-empty array")
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise AdapterError(
                f"{call_id}.request.messages[{index}] must contain only role/content")
        if message["role"] not in ("user", "assistant"):
            raise AdapterError(f"{call_id}.request.messages[{index}].role is unsupported")
        _require_string(message["content"], f"{call_id}.request.messages[{index}].content")

    output_config = request.get("output_config")
    if not isinstance(output_config, dict) or set(output_config) != {"format"}:
        if isinstance(output_config, dict) and "effort" in output_config:
            raise AdapterError(
                f"{call_id}: canonical effort must be unset for this adapter; "
                "reasoning is pinned per model in MODEL_CONFIGS")
        raise AdapterError(f"{call_id}.request.output_config must contain only format")
    fmt = output_config.get("format")
    if not isinstance(fmt, dict) or set(fmt) != {"type", "schema"}:
        raise AdapterError(f"{call_id}.request.output_config.format is not canonical")
    if fmt.get("type") != "json_schema":
        raise AdapterError(f"{call_id}: structured output type must be json_schema")
    schema = fmt.get("schema")
    _validate_schema_definition(schema, f"{call_id}.schema")
    properties = schema["properties"]
    stated_protocol = protocol in ("P1", "P3")
    if protocol == "P4":
        expected_fields = {"decision", "answer"}
    elif stated_protocol:
        expected_fields = {"answer", "probability"}
    else:
        expected_fields = {"answer"}
    if set(properties) != expected_fields or set(schema["required"]) != expected_fields:
        raise AdapterError(
            f"{call_id}: {protocol} output fields must be exactly {sorted(expected_fields)}")
    if protocol == "P4":
        decision_schema = properties["decision"]
        if (decision_schema.get("type") != "string"
                or decision_schema.get("enum") != P4_DECISIONS):
            raise AdapterError(f"{call_id}: P4 decision schema mismatch")
    answer_schema = properties["answer"]
    if (answer_schema.get("type") != "string"
            or answer_schema.get("enum") != EXPECTED_ANSWERS[task]):
        raise AdapterError(f"{call_id}: answer schema does not match active task {task}")
    if stated_protocol and properties["probability"].get("type") != "number":
        raise AdapterError(f"{call_id}: {protocol} probability schema must be numeric")

    if row.get("request_sha256") != digest(request):
        raise AdapterError(f"{call_id}: request_sha256 mismatch")
    if row.get("prompt_sha256") != digest({"system": system, "messages": messages}):
        raise AdapterError(f"{call_id}: prompt_sha256 mismatch")


def load_canonical_rows(path: pathlib.Path | str, provider: str, *,
                        exactly_one: bool = False) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    seen: set[str] = set()
    models: set[str] = set()
    for row in rows:
        validate_canonical_row(row, provider)
        call_id = row["call_id"]
        if call_id in seen:
            raise AdapterError(f"{path}: duplicate call ID {call_id}")
        seen.add(call_id)
        models.add(row["model"])
    if len(models) != 1:
        raise AdapterError(
            f"{path}: canonical file mixes models {sorted(models)}; "
            "one provider job must bind exactly one model")
    if exactly_one and len(rows) != 1:
        raise AdapterError(f"{path}: smoke execution requires exactly one row, found {len(rows)}")
    return rows


def to_chat_body(row: dict[str, Any], provider: str, *,
                 pinned_host: str | None = None) -> dict[str, Any]:
    """Translate one validated canonical row to a chat-completions body.

    The translation is a pure function of (row.request, provider pin,
    pinned_host); byte-identical canonical repeats therefore serialize to
    byte-identical provider bodies via :func:`canonical_json`.
    """
    validate_canonical_row(row, provider)
    validate_pinned_host(provider, pinned_host)
    request = row["request"]
    config = require_model_config(provider, request["model"])
    schema = request["output_config"]["format"]["schema"]
    system = request["system"]

    if config["structured_output"] == "json_schema":
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {
                "name": STRUCTURED_OUTPUT_NAME,
                "strict": True,
                "schema": json.loads(canonical_json(schema).decode("utf-8")),
            },
        }
    elif config["structured_output"] == "json_object_prompt_schema":
        response_format = {"type": "json_object"}
        system = (system + JSON_OBJECT_SCHEMA_INSTRUCTION
                  + canonical_json(schema).decode("utf-8"))
    else:  # pragma: no cover - registry invariant
        raise AdapterError(
            f"unsupported structured_output mode {config['structured_output']!r}")

    body: dict[str, Any] = {
        "model": request["model"],
        "messages": ([{"role": "system", "content": system}]
                     + json.loads(canonical_json(request["messages"]).decode("utf-8"))),
        config["max_tokens_field"]: MAX_OUTPUT_TOKENS,
        "response_format": response_format,
        "stream": False,
    }
    if config["thinking"] is not None:
        body["thinking"] = json.loads(canonical_json(config["thinking"]).decode("utf-8"))
    if config["reasoning_effort"] is not None:
        body["reasoning_effort"] = config["reasoning_effort"]
    if require_provider(provider).get("provider_routing"):
        routing: dict[str, Any] = {"require_parameters": True}
        if pinned_host is not None:
            routing["order"] = [pinned_host]
            routing["allow_fallbacks"] = False
        body["provider"] = routing
    return body


def load_api_key(provider: str,
                 env_file: pathlib.Path | str | None = DEFAULT_ENV_FILE) -> str:
    """Load only the registered literal key assignment; execute no shell."""
    env_key = require_provider(provider)["env_key"]
    from_env = os.environ.get(env_key)
    if from_env:
        return from_env
    if env_file is None:
        raise AdapterError(f"{env_key} is not set")
    path = pathlib.Path(env_file)
    if not path.exists():
        raise AdapterError(f"{env_key} is not set and dotenv file does not exist: {path}")
    values: list[str] = []
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
                raise AdapterError(f"{path}:{lineno}: unmatched quote in {env_key}")
            if not value:
                raise AdapterError(f"{path}:{lineno}: {env_key} is empty")
            # No interpolation, command substitution, inline comments, or
            # escape processing: the captured bytes are the credential.
            values.append(value)
    if not values:
        raise AdapterError(f"{path}: {env_key} assignment not found")
    if len(set(values)) != 1:
        raise AdapterError(f"{path}: conflicting {env_key} assignments")
    return values[0]


def _safe_headers(headers: Any) -> dict[str, str]:
    wanted = ("x-request-id", "request-id", "retry-after", "cf-ray",
              "x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens")
    out: dict[str, str] = {}
    for name in wanted:
        value = headers.get(name) if headers is not None else None
        if value:
            out[name] = str(value)
    return out


def _parse_retry_after(headers: dict[str, str]) -> float | None:
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date form: fall back to exponential backoff
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def _provider_error_message(payload: Any, status: int | None) -> str:
    message = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
        elif isinstance(error, str):
            message = error
    if not isinstance(message, str) or not message.strip():
        message = "provider request failed"
    message = " ".join(message.split())[:1000]
    return f"HTTP {status}: {message}" if status is not None else message


class OpenAICompatTransport:
    """Tiny stdlib HTTPS transport for one registered provider.

    It never logs requests, keys, or bodies.
    """

    def __init__(self, provider: str, api_key: str, timeout: float = 600.0):
        self.provider = provider
        self.base_url = require_provider(provider)["base_url"]
        if not api_key:
            raise AdapterError(f"{PROVIDERS[provider]['env_key']} is empty")
        self._api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, data: bytes | None,
                 content_type: str | None) -> tuple[bytes, dict[str, Any]]:
        if not path.startswith("/"):
            raise AdapterError("provider path must be absolute")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "pmcpa-bench-openai-compat-adapter/1",
        }
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read(), {
                    "http_status": int(response.status),
                    "headers": _safe_headers(response.headers),
                }
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload: Any = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"error": {"message": "non-JSON provider error response"}}
            safe = _safe_headers(exc.headers)
            raise ProviderHTTPError(
                exc.code, safe.get("x-request-id") or safe.get("request-id"),
                payload, _provider_error_message(payload, exc.code),
                retry_after=_parse_retry_after(safe)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # ``reason`` can include a hostname/error, but never the request
            # headers or bearer key.  Keep it compact for the durable receipt.
            reason = " ".join(str(getattr(exc, "reason", exc)).split())[:500]
            raise ProviderHTTPError(None, None, None,
                                    f"transport error: {reason}") from exc

    def json(self, method: str, path: str,
             body: dict[str, Any] | None = None) -> dict[str, Any]:
        raw, meta = self._request(
            method, path, canonical_json(body) if body is not None else None,
            "application/json" if body is not None else None)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderHTTPError(
                meta["http_status"],
                meta["headers"].get("x-request-id") or meta["headers"].get("request-id"),
                None, "provider returned non-JSON content") from exc
        if not isinstance(payload, dict):
            raise ProviderHTTPError(
                meta["http_status"],
                meta["headers"].get("x-request-id") or meta["headers"].get("request-id"),
                payload, "provider returned a non-object JSON response")
        return {"payload": payload, **meta}


def _error_object(kind: str, message: str, *, status: int | None = None,
                  request_id: str | None = None, code: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": kind, "message": message}
    if status is not None:
        out["http_status"] = status
    if request_id:
        out["request_id"] = request_id
    if code is not None:
        out["code"] = code
    return out


def _http_error_object(exc: ProviderHTTPError) -> dict[str, Any]:
    code = None
    if isinstance(exc.payload, dict) and isinstance(exc.payload.get("error"), dict):
        code = exc.payload["error"].get("code") or exc.payload["error"].get("type")
    return _error_object("provider_http_error", exc.safe_message,
                         status=exc.status, request_id=exc.request_id, code=code)


def _is_retryable(exc: ProviderHTTPError) -> bool:
    """Retry ONLY 429, 5xx and transport errors; every other 4xx is terminal."""
    return exc.status is None or exc.status == 429 or exc.status >= 500


def _retry_sleep_seconds(attempt: int, retry_after: float | None) -> float:
    backoff = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
    if retry_after is not None:
        backoff = max(backoff, retry_after)
    return min(backoff, RETRY_MAX_SLEEP_SECONDS)


class SharedCooldown:
    """Collective 429 backoff shared by concurrent run-live workers.

    Whenever any worker observes HTTP 429 it extends one shared monotonic
    deadline by that call's computed backoff (which already honors a numeric
    ``Retry-After``); every worker waits out the remaining cooldown before
    its next attempt.  This changes nothing about the per-call retry policy;
    it only makes other workers pause instead of piling onto a rate limit.
    """

    def __init__(self, now_fn=time.monotonic):
        self._lock = threading.Lock()
        self._now = now_fn
        self._until = 0.0

    def extend(self, seconds: float) -> None:
        deadline = self._now() + max(0.0, float(seconds))
        with self._lock:
            if deadline > self._until:
                self._until = deadline

    def wait(self, sleep_fn=time.sleep) -> None:
        with self._lock:
            remaining = self._until - self._now()
        if remaining > 0:
            sleep_fn(remaining)


def _provider_receipt(provider: str, payload: Any, meta: dict[str, Any], *,
                      config: dict[str, Any], pinned_host: str | None,
                      attempts: int) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else None
    headers = meta.get("headers") or {}
    return {
        "provider": provider,
        "compat": "openai_chat_completions",
        "base_url": PROVIDERS[provider]["base_url"],
        "endpoint": CHAT_ENDPOINT,
        "http_status": meta.get("http_status"),
        "request_id": (meta.get("request_id") or headers.get("x-request-id")
                       or headers.get("request-id")),
        "provider_response_id": raw.get("id") if raw else None,
        "model": raw.get("model") if raw else None,
        "usage": raw.get("usage") if raw else None,
        "pinned_host": pinned_host,
        "structured_output": config["structured_output"],
        "attempts": attempts,
        "raw": payload,
    }


def _finish_reason(payload: dict[str, Any]) -> Any:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0].get("finish_reason")
    return None


def _extract_message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise AdapterError(
            f"expected exactly one choice, found "
            f"{len(choices) if isinstance(choices, list) else type(choices).__name__}")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise AdapterError("choice must be an object")
    finish = choice.get("finish_reason")
    if finish != "stop":
        raise AdapterError(f"finish_reason={finish!r}; only 'stop' is a complete answer")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise AdapterError("choice.message must be an assistant message")
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal.strip():
        raise AdapterError("model refusal: " + " ".join(refusal.split())[:1000])
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise AdapterError("choice.message.content must be a non-empty string")
    return content


def normalize_payload(canonical: dict[str, Any], provider: str,
                      payload: dict[str, Any], meta: dict[str, Any], *,
                      pinned_host: str | None, attempts: int,
                      requested_utc: str | None) -> dict[str, Any]:
    config = require_model_config(provider, canonical["model"])
    receipt = _provider_receipt(provider, payload, meta, config=config,
                                pinned_host=pinned_host, attempts=attempts)
    base: dict[str, Any] = {
        "schema_version": NORMALIZED_CONTRACT,
        "call_id": canonical["call_id"],
        "custom_id": canonical["call_id"],
        "provider": provider,
        "pinned_host": pinned_host,
        "provider_request_id": receipt.get("request_id"),
        "provider_response_id": receipt.get("provider_response_id"),
        "usage": payload.get("usage"),
        "response": receipt,
        "requested_utc": requested_utc,
        "stop_reason": _finish_reason(payload),
    }
    try:
        error = payload.get("error")
        if error:
            raise AdapterError(f"provider response error: "
                               f"{_provider_error_message(payload, None)}")
        parsed = strict_json_loads(_extract_message_content(payload),
                                   "structured response")
        schema = canonical["request"]["output_config"]["format"]["schema"]
        _validate_instance(parsed, schema)
        if not isinstance(parsed, dict):
            raise AdapterError("structured response root is not an object")
        if canonical["protocol"] in ("P1", "P3"):
            probability = parsed["probability"]
            if not math.isfinite(float(probability)) or not 0.0 <= float(probability) <= 1.0:
                raise AdapterError(
                    f"{canonical['protocol']} probability {probability!r} is outside [0, 1]")
            parsed["probability"] = float(probability)
        if payload.get("model") != canonical["model"]:
            # The paid response may be legitimate but the provider reported a
            # different model identity (for example a dated snapshot).  Never
            # import or automatically retry it; retain the candidate output
            # for owner adjudication.
            base.update({
                "parsed": None, "error": None, "retry_safe": False,
                "quarantine": {
                    "type": "provider_model_identity_mismatch",
                    "message": (f"requested {canonical['model']!r}, provider "
                                f"reported {payload.get('model')!r}"),
                    "candidate_parsed": parsed,
                },
            })
            return base
        base.update({"parsed": parsed, "error": None,
                     "retry_safe": False,  # completed calls are immutable
                     "quarantine": None})
    except AdapterError as exc:
        # Fail closed: a paid response that fails JSON/schema validation is
        # quarantined for owner adjudication, never guessed and never
        # silently re-billed.
        base.update({
            "parsed": None, "error": None, "retry_safe": False,
            "quarantine": {
                "type": "response_validation_failure",
                "message": str(exc),
            },
        })
    return base


def _http_failure_receipt(canonical: dict[str, Any], provider: str,
                          exc: ProviderHTTPError, *, pinned_host: str | None,
                          attempts: int, requested_utc: str | None) -> dict[str, Any]:
    config = require_model_config(provider, canonical["model"])
    receipt = _provider_receipt(
        provider, exc.payload, {"http_status": exc.status, "request_id": exc.request_id},
        config=config, pinned_host=pinned_host, attempts=attempts)
    return {
        "schema_version": NORMALIZED_CONTRACT,
        "call_id": canonical["call_id"],
        "custom_id": canonical["call_id"],
        "provider": provider,
        "pinned_host": pinned_host,
        "provider_request_id": exc.request_id,
        "provider_response_id": None,
        "usage": None,
        "response": receipt,
        "parsed": None,
        "error": _http_error_object(exc),
        # Exhausted 429/5xx/transport failures may be re-planned; terminal
        # 4xx request defects must be repaired, not resubmitted verbatim.
        "retry_safe": _is_retryable(exc),
        "quarantine": None,
        "requested_utc": requested_utc,
        "stop_reason": None,
    }


def execute_call(canonical: dict[str, Any], transport: Any, provider: str, *,
                 pinned_host: str | None = None, sleep_fn=time.sleep,
                 cooldown: SharedCooldown | None = None) -> dict[str, Any]:
    """Execute one canonical call with bounded retry; always return a receipt.

    ``cooldown`` (optional, used by the pooled run-live path) is a
    :class:`SharedCooldown`: each attempt first waits out any shared 429
    cooldown, and any observed 429 extends it.  When ``cooldown`` is None the
    behavior is byte-identical to the original sequential executor.
    """
    body = to_chat_body(canonical, provider, pinned_host=pinned_host)
    requested_utc = utc_now()
    attempts = 0
    while True:
        attempts += 1
        if cooldown is not None:
            cooldown.wait(sleep_fn)
        try:
            result = transport.json("POST", CHAT_ENDPOINT, body)
        except ProviderHTTPError as exc:
            delay = _retry_sleep_seconds(attempts, exc.retry_after)
            if cooldown is not None and exc.status == 429:
                cooldown.extend(delay)
            if _is_retryable(exc) and attempts < RETRY_MAX_ATTEMPTS:
                sleep_fn(delay)
                continue
            return _http_failure_receipt(
                canonical, provider, exc, pinned_host=pinned_host,
                attempts=attempts, requested_utc=requested_utc)
        return normalize_payload(
            canonical, provider, result["payload"], result,
            pinned_host=pinned_host, attempts=attempts,
            requested_utc=requested_utc)


def read_existing_receipts(path: pathlib.Path | str,
                           canonical_ids: set[str]) -> dict[str, dict[str, Any]]:
    """Parse the append-only output; any receipt makes its call_id terminal here."""
    path = pathlib.Path(path)
    done: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            row = strict_json_loads(line, f"{path}:{lineno}")
            if not isinstance(row, dict):
                raise AdapterError(f"{path}:{lineno}: receipt row must be an object")
            if row.get("schema_version") != NORMALIZED_CONTRACT:
                raise AdapterError(
                    f"{path}:{lineno}: existing row is not a {NORMALIZED_CONTRACT} receipt")
            call_id = _require_string(row.get("call_id"), f"{path}:{lineno}.call_id")
            if call_id in done:
                raise AdapterError(f"{path}: duplicate receipt for {call_id}")
            if call_id not in canonical_ids:
                raise AdapterError(
                    f"{path}: receipt {call_id} does not belong to this canonical file")
            done[call_id] = row
    return done


def _run_live_pooled(fh: Any, pending: list[dict[str, Any]],
                     counts: dict[str, int], *, provider: str, transport: Any,
                     pinned_host: str | None, max_calls: int, sleep_ms: int,
                     sleep_fn, progress_every: int, print_fn,
                     concurrency: int) -> None:
    """Execute pending calls with up to ``concurrency`` in flight.

    Guarantees relative to the sequential path:

    * Single writer: one lock serialises receipt appends; each receipt is
      written as one whole line, flushed and fsynced before the lock is
      released, so lines are never interleaved or partial.  A worker never
      starts its next call before its previous receipt is durable, bounding
      billed-but-unreceipted calls at any crash to the <= N then in flight.
    * Receipts are written only after a response or terminal failure arrives
      (never pre-written); resume keys on ``call_id`` and is independent of
      receipt order in the file.
    * ``sleep_ms`` applies per worker between that worker's own calls.
    * A receipt for an exhausted retryable failure (429/5xx/transport,
      ``retry_safe`` true) stops NEW submissions; calls already in flight
      finish and are receipted, and unattempted calls stay retriable.
    * Terminal 4xx and quarantined receipts do not stop the run, exactly as
      in the sequential path.
    """
    work: queue.Queue = queue.Queue()
    for row in pending:
        work.put(row)
    state_lock = threading.Lock()  # single-writer receipt append + counters
    stop_new_submissions = threading.Event()
    cooldown = SharedCooldown()
    worker_errors: list[BaseException] = []
    written = 0

    def _record(receipt: dict[str, Any]) -> None:
        nonlocal written
        with state_lock:
            _append_receipt(fh, receipt)
            written += 1
            if receipt.get("quarantine"):
                counts["quarantined"] += 1
            elif receipt.get("error"):
                counts["failed"] += 1
                if receipt.get("retry_safe"):
                    stop_new_submissions.set()
            else:
                counts["completed"] += 1
            if written % progress_every == 0:
                print_fn(
                    f"progress: {written}/{len(pending)} calls "
                    f"(completed={counts['completed']} failed={counts['failed']} "
                    f"quarantined={counts['quarantined']} skipped={counts['skipped']})")

    def _worker() -> None:
        first = True
        while True:
            if stop_new_submissions.is_set() or worker_errors:
                return
            try:
                row = work.get_nowait()
            except queue.Empty:
                return
            try:
                if not first and sleep_ms:
                    sleep_fn(sleep_ms / 1000.0)
                first = False
                with state_lock:
                    if counts["attempted"] >= max_calls:  # defence in depth
                        raise AdapterError(
                            "hard call cap reached; refusing further submissions")
                    counts["attempted"] += 1
                _record(execute_call(row, transport, provider,
                                     pinned_host=pinned_host, sleep_fn=sleep_fn,
                                     cooldown=cooldown))
            except BaseException as exc:  # re-raised in the caller after join
                with state_lock:
                    worker_errors.append(exc)
                stop_new_submissions.set()
                return

    threads = [threading.Thread(target=_worker, name=f"openai-compat-{index + 1}")
               for index in range(min(concurrency, len(pending)))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if worker_errors:
        raise worker_errors[0]


def run_live(canonical_path: pathlib.Path | str, output_path: pathlib.Path | str,
             provider: str, *, max_calls: int, transport: Any,
             pinned_host: str | None = None, sleep_ms: int = 0,
             sleep_fn=time.sleep, progress_every: int = PROGRESS_EVERY,
             print_fn=print, concurrency: int = 1) -> dict[str, int]:
    """Resumable live executor under a required hard call cap.

    ``concurrency=1`` (the default) is the original strictly sequential
    path, unchanged.  ``concurrency>1`` executes up to that many calls in
    flight via :func:`_run_live_pooled`, which preserves the append-only
    single-writer receipt contract and resume semantics.
    """
    require_provider(provider)
    validate_pinned_host(provider, pinned_host)
    if not isinstance(max_calls, int) or max_calls < 1:
        raise AdapterError("--max-calls must be a positive integer hard cap")
    if sleep_ms < 0:
        raise AdapterError("--sleep-ms must be >= 0")
    if not isinstance(concurrency, int) or concurrency < 1:
        raise AdapterError("--concurrency must be a positive integer")
    rows = load_canonical_rows(canonical_path, provider)
    if len(rows) > max_calls:
        raise AdapterError(
            f"refusing: canonical file contains {len(rows)} rows, which exceeds "
            f"--max-calls {max_calls}; approve a larger cap explicitly")
    canonical_ids = {row["call_id"] for row in rows}
    done = read_existing_receipts(output_path, canonical_ids)
    pending = [row for row in rows if row["call_id"] not in done]
    counts = {"planned": len(rows), "skipped": len(done), "attempted": 0,
              "completed": 0, "failed": 0, "quarantined": 0}
    output_path = pathlib.Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as fh:
        if concurrency > 1:
            _run_live_pooled(
                fh, pending, counts, provider=provider, transport=transport,
                pinned_host=pinned_host, max_calls=max_calls,
                sleep_ms=sleep_ms, sleep_fn=sleep_fn,
                progress_every=progress_every, print_fn=print_fn,
                concurrency=concurrency)
            return counts
        for index, row in enumerate(pending):
            if index and sleep_ms:
                sleep_fn(sleep_ms / 1000.0)
            if counts["attempted"] >= max_calls:  # defence in depth
                raise AdapterError("hard call cap reached; refusing further submissions")
            counts["attempted"] += 1
            receipt = execute_call(row, transport, provider,
                                   pinned_host=pinned_host, sleep_fn=sleep_fn)
            _append_receipt(fh, receipt)
            if receipt.get("quarantine"):
                counts["quarantined"] += 1
            elif receipt.get("error"):
                counts["failed"] += 1
            else:
                counts["completed"] += 1
            if counts["attempted"] % progress_every == 0:
                print_fn(
                    f"progress: {counts['attempted']}/{len(pending)} calls "
                    f"(completed={counts['completed']} failed={counts['failed']} "
                    f"quarantined={counts['quarantined']} skipped={counts['skipped']})")
    return counts


def _add_live_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS),
                        help="registered OpenAI-compatible provider")
    parser.add_argument("--pin-host", default=None,
                        help="OpenRouter upstream provider slug pinned via the "
                             "documented provider-routing field")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE),
                        help="literal dotenv fallback; only the registered "
                             "provider key is read")
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="per-request HTTPS timeout in seconds")
    parser.add_argument("--execute", action="store_true",
                        help="required acknowledgement for live model submission")


def _transport(args: argparse.Namespace) -> OpenAICompatTransport:
    return OpenAICompatTransport(
        args.provider, load_api_key(args.provider, args.env_file), args.timeout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    smoke = commands.add_parser("smoke", help="execute exactly one live chat completion")
    smoke.add_argument("--input", required=True, help="canonical JSONL containing one row")
    smoke.add_argument("--output", required=True, help="new normalized-result JSONL")
    _add_live_args(smoke)

    live = commands.add_parser(
        "run-live", help="resumable live executor with a hard call cap "
                         "(sequential by default; see --concurrency)")
    live.add_argument("--canonical", required=True, help="canonical JSONL export")
    live.add_argument("--output", required=True,
                      help="append-only normalized-result JSONL (resumable)")
    live.add_argument("--max-calls", required=True, type=int,
                      help="approved hard cap; refused if canonical rows exceed it")
    live.add_argument("--sleep-ms", type=int, default=0,
                      help="milliseconds to sleep between consecutive live calls "
                           "(per worker when --concurrency > 1)")
    live.add_argument("--concurrency", type=int, default=1,
                      help="maximum calls in flight (default 1 = strictly "
                           "sequential; receipts are always appended whole-line "
                           "with fsync by one writer at a time)")
    _add_live_args(live)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "smoke":
            if not args.execute:
                raise AdapterError("smoke requires --execute; no request was sent")
            canonical = load_canonical_rows(args.input, args.provider,
                                            exactly_one=True)[0]
            validate_pinned_host(args.provider, args.pin_host)
            transport = _transport(args)
            with reserve_text_output(args.output) as receipt_fh:
                result = execute_call(canonical, transport, args.provider,
                                      pinned_host=args.pin_host)
                _append_receipt(receipt_fh, result)
            if result.get("quarantine"):
                print(f"smoke quarantined for {canonical['call_id']}; "
                      f"receipt -> {args.output}")
                return 3
            if result.get("error"):
                print(f"smoke failed for {canonical['call_id']}; receipt -> {args.output}")
                return 1
            print(f"smoke completed for {canonical['call_id']} -> {args.output}")
            return 0

        if args.command == "run-live":
            if not args.execute:
                raise AdapterError("run-live requires --execute; no request was sent")
            # Validate the plan and the cap before any credential is read.
            rows = load_canonical_rows(args.canonical, args.provider)
            validate_pinned_host(args.provider, args.pin_host)
            if len(rows) > args.max_calls:
                raise AdapterError(
                    f"refusing: canonical file contains {len(rows)} rows, which "
                    f"exceeds --max-calls {args.max_calls}")
            if args.concurrency < 1:
                raise AdapterError("--concurrency must be a positive integer")
            transport = _transport(args)
            counts = run_live(
                args.canonical, args.output, args.provider,
                max_calls=args.max_calls, transport=transport,
                pinned_host=args.pin_host, sleep_ms=args.sleep_ms,
                concurrency=args.concurrency)
            print(f"run-live: planned={counts['planned']} skipped={counts['skipped']} "
                  f"attempted={counts['attempted']} completed={counts['completed']} "
                  f"failed={counts['failed']} quarantined={counts['quarantined']} "
                  f"-> {args.output}")
            if counts["quarantined"]:
                return 3
            if counts["failed"]:
                return 1
            return 0

        raise AdapterError(f"unknown command {args.command}")
    except (AdapterError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
