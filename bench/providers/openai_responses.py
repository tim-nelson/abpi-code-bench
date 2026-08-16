#!/usr/bin/env python3
"""Strict OpenAI Responses/Batch adapter for canonical benchmark exports.

This module is deliberately separate from :mod:`bench.run`.  ``run.py`` owns
prompt construction, stable call identities, and the append-only result
ledger.  This adapter only:

* validates canonical ``pmcpa.zero-provider.v*`` JSONL exports;
* translates them mechanically to ``POST /v1/responses`` requests;
* executes one explicitly authorised synchronous smoke call, or manages an
  OpenAI Batch job; and
* writes normalized JSONL that ``run.py --import-results`` can consume.

Offline operations make no network call.  Live submission commands require an
explicit ``--execute`` flag and read only ``OPENAI_API_KEY`` from the process
environment or a literal dotenv file.  The key and request bodies are never
printed.

Examples (the protocol flags belong to ``run.py`` and may be renamed without
changing this adapter):

    python3 -B bench/providers/openai_responses.py prepare-batch \
      --input /tmp/canonical.jsonl --output /tmp/openai-batch.jsonl

    python3 -B bench/providers/openai_responses.py smoke \
      --input /tmp/one-canonical-call.jsonl \
      --output /tmp/one-normalized-result.jsonl --execute

    python3 -B bench/providers/openai_responses.py batch-submit \
      --canonical /tmp/canonical.jsonl --input /tmp/openai-batch.jsonl \
      --expect-requests 12 \
      --receipt /tmp/batch-submit.json --execute

No third-party SDK is required; the live transport uses the Python standard
library over HTTPS.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import pathlib
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any


ADAPTER_CONTRACT = "pmcpa.openai-responses.v1"
NORMALIZED_CONTRACT = "pmcpa.openai-normalized.v1"
CANONICAL_CONTRACT = "pmcpa.zero-provider.v2"

MODEL = "gpt-5.6-luna"
TERRA_MODEL = "gpt-5.6-terra"
SOL_MODEL = "gpt-5.6-sol"
SUPPORTED_MODELS = frozenset((MODEL, TERRA_MODEL, SOL_MODEL))
ENDPOINT = "/v1/responses"
REASONING_EFFORT = "medium"
MAX_OUTPUT_TOKENS = 4096
COMPLETION_WINDOW = "24h"
API_BASE = "https://api.openai.com/v1"
P3_AGGREGATION = "linear_probability_pool"
P4_AGGREGATION = "cost_sweep"
P4_DECISIONS = ["answer", "refer"]

EXPECTED_ANSWERS = {
    "T1": ["breach", "no_breach"],
    "T2": ["breach", "no_breach"],
    "T3": ["upheld", "overturned"],
}

TERMINAL_BATCH_STATUSES = frozenset(("completed", "failed", "expired", "cancelled"))
KNOWN_BATCH_STATUSES = TERMINAL_BATCH_STATUSES | frozenset((
    "validating", "in_progress", "finalizing", "cancelling",
))

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


class AdapterError(ValueError):
    """A local contract, identity, or provider-response validation failure."""


class ProviderHTTPError(RuntimeError):
    """A sanitized provider HTTP failure which never embeds credentials."""

    def __init__(self, status: int | None, request_id: str | None,
                 payload: Any, message: str, *, ambiguous: bool = False,
                 client_request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.request_id = request_id
        self.payload = payload
        self.safe_message = message
        self.ambiguous = ambiguous
        self.client_request_id = client_request_id


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def file_sha256(path: pathlib.Path | str) -> str:
    h = hashlib.sha256()
    with pathlib.Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


def write_jsonl_exclusive(path: pathlib.Path | str,
                          rows: list[dict[str, Any]]) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise AdapterError(f"refusing to overwrite {path}") from exc


def write_json_exclusive(path: pathlib.Path | str, value: dict[str, Any]) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
    except FileExistsError as exc:
        raise AdapterError(f"refusing to overwrite {path}") from exc


def reserve_text_output(path: pathlib.Path | str):
    """Reserve an immutable receipt path before any provider action."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise AdapterError(f"refusing to overwrite {path}") from exc


def write_jsonl_handle(fh: Any, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    fh.flush()
    os.fsync(fh.fileno())


def write_json_handle(fh: Any, value: dict[str, Any]) -> None:
    json.dump(value, fh, ensure_ascii=False, indent=1, sort_keys=True)
    fh.write("\n")
    fh.flush()
    os.fsync(fh.fileno())


def _require_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdapterError(f"{field} must be a non-empty string")
    return value


def _validate_schema_definition(schema: Any, path: str = "schema") -> None:
    """Validate the JSON-Schema subset emitted by the canonical runner.

    OpenAI validates the schema server-side too.  Local validation exists so a
    malformed or silently broadened schema never reaches a paid call.
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
            _require_nonempty_string(name, f"{path}.properties key")
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
        if unknown and schema.get("additionalProperties") is False:
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


def validate_canonical_row(row: dict[str, Any]) -> None:
    contract = _require_nonempty_string(row.get("schema_version"), "schema_version")
    if contract != CANONICAL_CONTRACT:
        raise AdapterError(
            f"unsupported canonical contract {contract!r}; expected {CANONICAL_CONTRACT!r}")

    call_id = _require_nonempty_string(row.get("call_id"), "call_id")
    custom_id = _require_nonempty_string(row.get("custom_id"), "custom_id")
    if call_id != custom_id:
        raise AdapterError(f"{call_id}: call_id/custom_id mismatch")
    if len(custom_id) > 64:
        raise AdapterError(f"{call_id}: custom_id exceeds the provider's 64-character limit")

    top_model = _require_nonempty_string(row.get("model"), f"{call_id}.model")
    if top_model not in SUPPORTED_MODELS:
        raise AdapterError(
            f"{call_id}: unsupported OpenAI model {top_model!r}; "
            f"expected one of {sorted(SUPPORTED_MODELS)}")
    protocol = _require_nonempty_string(row.get("protocol"), f"{call_id}.protocol")
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
    task = _require_nonempty_string(row.get("task"), f"{call_id}.task")
    if task not in EXPECTED_ANSWERS:
        raise AdapterError(f"{call_id}: unsupported active task {task!r}")
    _require_nonempty_string(row.get("config_hash"), f"{call_id}.config_hash")

    request = row.get("request")
    if not isinstance(request, dict):
        raise AdapterError(f"{call_id}.request must be an object")
    allowed_request_keys = {"model", "max_tokens", "system", "messages", "output_config"}
    unknown = set(request) - allowed_request_keys
    if unknown:
        if "thinking" in unknown:
            raise AdapterError(
                f"{call_id}: OpenAI export requires --thinking unset; "
                "reasoning is pinned separately with --effort medium")
        if "temperature" in unknown:
            raise AdapterError(
                f"{call_id}: OpenAI configuration requires temperature unset")
        raise AdapterError(f"{call_id}.request has unsupported configuration: {sorted(unknown)}")
    if request.get("model") != top_model:
        raise AdapterError(
            f"{call_id}.request.model must match top-level model {top_model!r}")
    if request.get("max_tokens") != MAX_OUTPUT_TOKENS:
        raise AdapterError(
            f"{call_id}.request.max_tokens must be exactly {MAX_OUTPUT_TOKENS}")
    system = _require_nonempty_string(request.get("system"), f"{call_id}.request.system")
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise AdapterError(f"{call_id}.request.messages must be a non-empty array")
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise AdapterError(
                f"{call_id}.request.messages[{index}] must contain only role/content")
        if message["role"] not in ("user", "assistant"):
            raise AdapterError(
                f"{call_id}.request.messages[{index}].role is unsupported")
        _require_nonempty_string(
            message["content"], f"{call_id}.request.messages[{index}].content")

    output_config = request.get("output_config")
    if not isinstance(output_config, dict) or set(output_config) != {"format", "effort"}:
        raise AdapterError(
            f"{call_id}.request.output_config must contain only format/effort")
    if output_config.get("effort") != REASONING_EFFORT:
        raise AdapterError(
            f"{call_id}: reasoning effort must be exactly {REASONING_EFFORT!r}")
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
        raise AdapterError(
            f"{call_id}: answer schema does not match active task {task}")
    if stated_protocol and properties["probability"].get("type") != "number":
        raise AdapterError(f"{call_id}: {protocol} probability schema must be numeric")

    request_hash = _require_nonempty_string(
        row.get("request_sha256"), f"{call_id}.request_sha256")
    if request_hash != digest(request):
        raise AdapterError(f"{call_id}: request_sha256 mismatch")
    prompt_hash = _require_nonempty_string(
        row.get("prompt_sha256"), f"{call_id}.prompt_sha256")
    if prompt_hash != digest({"system": system, "messages": messages}):
        raise AdapterError(f"{call_id}: prompt_sha256 mismatch")


def load_canonical_rows(path: pathlib.Path | str, *, exactly_one: bool = False
                        ) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    seen: set[str] = set()
    for row in rows:
        validate_canonical_row(row)
        call_id = row["call_id"]
        if call_id in seen:
            raise AdapterError(f"{path}: duplicate call ID {call_id}")
        seen.add(call_id)
    models = {row["model"] for row in rows}
    if len(models) != 1:
        raise AdapterError(
            f"{path}: canonical file mixes models {sorted(models)}; "
            "one provider job must bind exactly one model")
    if exactly_one and len(rows) != 1:
        raise AdapterError(f"{path}: smoke execution requires exactly one row, found {len(rows)}")
    return rows


def to_responses_body(row: dict[str, Any]) -> dict[str, Any]:
    """Translate one validated canonical row to a Responses API body."""
    validate_canonical_row(row)
    request = row["request"]
    schema = request["output_config"]["format"]["schema"]
    return {
        "model": request["model"],
        "input": ([{"role": "system", "content": request["system"]}]
                  + request["messages"]),
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "reasoning": {"effort": REASONING_EFFORT},
        "text": {"format": {
            "type": "json_schema",
            "name": "pmcpa_bench_output",
            "strict": True,
            "schema": schema,
        }},
        # Evaluation prompts and provider responses remain in the local ledger.
        "store": False,
    }


def _validate_responses_body(body: Any) -> None:
    if not isinstance(body, dict):
        raise AdapterError("batch request body must be an object")
    expected_keys = {"model", "input", "max_output_tokens", "reasoning", "text", "store"}
    if set(body) != expected_keys:
        raise AdapterError(
            f"batch request body fields do not match adapter contract: {sorted(set(body))}")
    if body.get("model") not in SUPPORTED_MODELS:
        raise AdapterError(
            f"batch body model must be one of {sorted(SUPPORTED_MODELS)}")
    if body.get("max_output_tokens") != MAX_OUTPUT_TOKENS:
        raise AdapterError(f"batch body max_output_tokens must be {MAX_OUTPUT_TOKENS}")
    if body.get("reasoning") != {"effort": REASONING_EFFORT}:
        raise AdapterError(f"batch body reasoning must be {REASONING_EFFORT!r}")
    if body.get("store") is not False:
        raise AdapterError("batch body store must be false")
    inputs = body.get("input")
    if not isinstance(inputs, list) or len(inputs) < 2:
        raise AdapterError("batch body input must include system and user messages")
    if inputs[0].get("role") != "system" or not isinstance(inputs[0].get("content"), str):
        raise AdapterError("batch body first input must be a system message")
    for index, message in enumerate(inputs[1:], 1):
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] not in ("user", "assistant")
                or not isinstance(message["content"], str)):
            raise AdapterError(f"batch body input[{index}] is not canonical")
    text = body.get("text")
    if not isinstance(text, dict) or set(text) != {"format"}:
        raise AdapterError("batch body text must contain only format")
    fmt = text["format"]
    if (not isinstance(fmt, dict)
            or set(fmt) != {"type", "name", "strict", "schema"}
            or fmt.get("type") != "json_schema"
            or fmt.get("name") != "pmcpa_bench_output"
            or fmt.get("strict") is not True):
        raise AdapterError("batch body structured-output format is not canonical")
    _validate_schema_definition(fmt.get("schema"))


def to_batch_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "custom_id": row["call_id"],
        "method": "POST",
        "url": ENDPOINT,
        "body": to_responses_body(row),
    }


def validate_provider_batch_rows(rows: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    models: set[str] = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != {"custom_id", "method", "url", "body"}:
            raise AdapterError(f"batch row {index} has unexpected fields")
        call_id = _require_nonempty_string(row.get("custom_id"), f"batch row {index}.custom_id")
        if len(call_id) > 64:
            raise AdapterError(f"batch row {index}: custom_id exceeds 64 characters")
        if call_id in seen:
            raise AdapterError(f"duplicate batch custom_id {call_id}")
        seen.add(call_id)
        if row.get("method") != "POST" or row.get("url") != ENDPOINT:
            raise AdapterError(f"{call_id}: expected POST {ENDPOINT}")
        _validate_responses_body(row.get("body"))
        models.add(row["body"]["model"])
    if len(models) != 1:
        raise AdapterError(
            f"provider batch mixes models {sorted(models)}; "
            "one provider job must bind exactly one model")


def prepare_batch(canonical_path: pathlib.Path | str,
                  output_path: pathlib.Path | str) -> int:
    rows = load_canonical_rows(canonical_path)
    provider_rows = [to_batch_row(row) for row in rows]
    validate_provider_batch_rows(provider_rows)
    write_jsonl_exclusive(output_path, provider_rows)
    return len(provider_rows)


def validate_batch_binding(canonical_path: pathlib.Path | str,
                           batch_path: pathlib.Path | str,
                           expect_requests: int) -> tuple[list[dict[str, Any]],
                                                          list[dict[str, Any]]]:
    """Prove that a submission file is the exact mechanical canonical export."""
    canonical_rows = load_canonical_rows(canonical_path)
    batch_rows = read_jsonl(batch_path)
    validate_provider_batch_rows(batch_rows)
    if expect_requests < 1:
        raise AdapterError("--expect-requests must be positive")
    if len(canonical_rows) != expect_requests or len(batch_rows) != expect_requests:
        raise AdapterError(
            f"submission expected {expect_requests} requests but canonical/provider files "
            f"contain {len(canonical_rows)}/{len(batch_rows)}")
    expected_rows = [to_batch_row(row) for row in canonical_rows]
    for index, (expected, actual) in enumerate(zip(expected_rows, batch_rows), 1):
        if canonical_json(expected) != canonical_json(actual):
            call_id = expected["custom_id"]
            raise AdapterError(
                f"batch row {index} ({call_id}) differs from the canonical translation")
    return canonical_rows, batch_rows


def load_api_key(env_file: pathlib.Path | str | None = DEFAULT_ENV_FILE) -> str:
    """Load only a literal ``OPENAI_API_KEY`` assignment; execute no shell."""
    from_env = os.environ.get("OPENAI_API_KEY")
    if from_env:
        return from_env
    if env_file is None:
        raise AdapterError("OPENAI_API_KEY is not set")
    path = pathlib.Path(env_file)
    if not path.exists():
        raise AdapterError(
            f"OPENAI_API_KEY is not set and dotenv file does not exist: {path}")
    values: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            candidate = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
            if not re.match(r"OPENAI_API_KEY\s*=", candidate):
                continue
            match = re.fullmatch(r"OPENAI_API_KEY\s*=\s*(.*)", candidate)
            if not match:
                raise AdapterError(f"{path}:{lineno}: malformed OPENAI_API_KEY assignment")
            value = match.group(1).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            elif value.startswith(("'", '"')) or value.endswith(("'", '"')):
                raise AdapterError(f"{path}:{lineno}: unmatched quote in OPENAI_API_KEY")
            if not value:
                raise AdapterError(f"{path}:{lineno}: OPENAI_API_KEY is empty")
            # No interpolation, command substitution, inline comments, or
            # escape processing: the captured bytes are the credential.
            values.append(value)
    if not values:
        raise AdapterError(f"{path}: OPENAI_API_KEY assignment not found")
    if len(values) > 1 and len(set(values)) != 1:
        raise AdapterError(f"{path}: conflicting OPENAI_API_KEY assignments")
    return values[0]


def _safe_headers(headers: Any) -> dict[str, str]:
    wanted = ("x-request-id", "openai-processing-ms", "openai-version", "cf-ray")
    out: dict[str, str] = {}
    for name in wanted:
        value = headers.get(name) if headers is not None else None
        if value:
            out[name] = str(value)
    return out


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


class OpenAITransport:
    """Tiny standard-library HTTPS transport; it never logs requests."""

    def __init__(self, api_key: str, timeout: float = 600.0):
        if not api_key:
            raise AdapterError("OPENAI_API_KEY is empty")
        self._api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, data: bytes | None,
                 content_type: str | None, *, client_request_id: str | None = None
                 ) -> tuple[bytes, dict[str, Any]]:
        if not path.startswith("/"):
            raise AdapterError("provider path must be absolute")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "pmcpa-bench-openai-adapter/1",
        }
        if content_type:
            headers["Content-Type"] = content_type
        if client_request_id is not None:
            try:
                client_request_id.encode("ascii")
            except UnicodeEncodeError as exc:
                raise AdapterError("client request ID must be ASCII") from exc
            if not client_request_id or len(client_request_id) > 512:
                raise AdapterError("client request ID must contain 1..512 ASCII characters")
            headers["X-Client-Request-Id"] = client_request_id
        request = urllib.request.Request(
            API_BASE + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                meta = {
                    "http_status": int(response.status),
                    "headers": _safe_headers(response.headers),
                }
                return raw, meta
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload: Any = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"error": {"message": "non-JSON provider error response"}}
            headers_safe = _safe_headers(exc.headers)
            raise ProviderHTTPError(
                exc.code, headers_safe.get("x-request-id"), payload,
                _provider_error_message(payload, exc.code),
                ambiguous=(exc.code == 408 or exc.code >= 500),
                client_request_id=client_request_id) from exc
        except urllib.error.URLError as exc:
            # ``reason`` can include a hostname/error, but never the request
            # headers or bearer key.  Keep it compact for the durable receipt.
            reason = " ".join(str(exc.reason).split())[:500]
            raise ProviderHTTPError(None, None, None,
                                    f"transport error: {reason}", ambiguous=True,
                                    client_request_id=client_request_id) from exc
        except (TimeoutError, OSError) as exc:
            reason = " ".join(str(exc).split())[:500]
            raise ProviderHTTPError(None, None, None,
                                    f"transport error: {reason}", ambiguous=True,
                                    client_request_id=client_request_id) from exc

    def json(self, method: str, path: str, body: dict[str, Any] | None = None,
             *, client_request_id: str | None = None) -> dict[str, Any]:
        data = canonical_json(body) if body is not None else None
        raw, meta = self._request(
            method, path, data, "application/json" if body is not None else None,
            client_request_id=client_request_id)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderHTTPError(
                meta["http_status"], meta["headers"].get("x-request-id"), None,
                "provider returned non-JSON content", ambiguous=True,
                client_request_id=client_request_id) from exc
        if not isinstance(payload, dict):
            raise ProviderHTTPError(
                meta["http_status"], meta["headers"].get("x-request-id"), payload,
                "provider returned a non-object JSON response", ambiguous=True,
                client_request_id=client_request_id)
        return {"payload": payload, **meta}

    def upload_batch_file(self, path: pathlib.Path | str) -> dict[str, Any]:
        path = pathlib.Path(path)
        boundary = "pmcpa-" + secrets.token_hex(16)
        filename = path.name.replace('"', "_")
        prefix = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="purpose"\r\n\r\n'
            "batch\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/jsonl\r\n\r\n"
        ).encode("utf-8")
        suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        # Batch inputs are bounded provider files.  Holding one N<=300 tranche
        # in memory keeps the implementation dependency-free and auditable.
        data = prefix + path.read_bytes() + suffix
        raw, meta = self._request(
            "POST", "/files", data, f"multipart/form-data; boundary={boundary}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderHTTPError(
                meta["http_status"], meta["headers"].get("x-request-id"), None,
                "provider returned non-JSON file-upload content") from exc
        if not isinstance(payload, dict):
            raise ProviderHTTPError(
                meta["http_status"], meta["headers"].get("x-request-id"), payload,
                "provider returned a non-object file-upload response")
        return {"payload": payload, **meta}

    def bytes(self, path: str) -> dict[str, Any]:
        raw, meta = self._request("GET", path, None, None)
        return {"content": raw, **meta}


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


def _stop_reason(payload: dict[str, Any]) -> Any:
    details = payload.get("incomplete_details")
    if isinstance(details, dict) and details.get("reason"):
        return details["reason"]
    return payload.get("status")


def _extract_output_text(payload: dict[str, Any]) -> str:
    refusals: list[str] = []
    texts: list[str] = []
    output = payload.get("output")
    if not isinstance(output, list):
        raise AdapterError("response.output must be an array")
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise AdapterError("response message content must be an array")
        for block in content:
            if not isinstance(block, dict):
                raise AdapterError("response content block must be an object")
            if block.get("type") == "refusal":
                refusals.append(str(block.get("refusal") or "model refusal"))
            elif block.get("type") == "output_text":
                text = block.get("text")
                if not isinstance(text, str):
                    raise AdapterError("response output_text block has no text")
                texts.append(text)
    if refusals:
        raise AdapterError("model refusal: " + " | ".join(refusals)[:1000])
    if len(texts) != 1:
        raise AdapterError(f"expected exactly one output_text block, found {len(texts)}")
    return texts[0]


def _provider_receipt(payload: Any, meta: dict[str, Any], *,
                      batch_meta: dict[str, Any] | None = None,
                      raw_batch_row: Any = None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else None
    receipt: dict[str, Any] = {
        "provider": "openai",
        "endpoint": ENDPOINT,
        "http_status": meta.get("http_status"),
        "request_id": meta.get("request_id") or (meta.get("headers") or {}).get("x-request-id"),
        "provider_response_id": raw.get("id") if raw else None,
        "model": raw.get("model") if raw else None,
        "usage": raw.get("usage") if raw else None,
        "raw": payload,
    }
    if raw_batch_row is not None:
        receipt["raw_batch_row"] = raw_batch_row
    if batch_meta:
        receipt["batch"] = {
            key: batch_meta.get(key) for key in (
                "batch_id", "input_file_id", "output_file_id", "error_file_id")
        }
    return receipt


def _normalize_payload(canonical: dict[str, Any], payload: dict[str, Any],
                       meta: dict[str, Any], *, batch_meta: dict[str, Any] | None = None,
                       raw_batch_row: Any = None,
                       requested_utc: str | None = None) -> dict[str, Any]:
    call_id = canonical["call_id"]
    receipt = _provider_receipt(payload, meta, batch_meta=batch_meta,
                                raw_batch_row=raw_batch_row)
    base: dict[str, Any] = {
        "schema_version": NORMALIZED_CONTRACT,
        "call_id": call_id,
        "custom_id": call_id,
        "provider": "openai",
        "provider_request_id": receipt.get("request_id"),
        "provider_response_id": receipt.get("provider_response_id"),
        "usage": payload.get("usage"),
        "response": receipt,
        "requested_utc": requested_utc,
        "stop_reason": _stop_reason(payload),
    }
    try:
        if payload.get("status") != "completed":
            details = payload.get("incomplete_details") or payload.get("error")
            raise AdapterError(f"provider response status={payload.get('status')!r}: {details!r}")
        if payload.get("error"):
            raise AdapterError(f"provider response error: {payload['error']!r}")
        text = _extract_output_text(payload)
        parsed = strict_json_loads(text, "structured response")
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
        requested_model = canonical["model"]
        if payload.get("model") != requested_model:
            # The paid response may be legitimate but the provider resolved an
            # undocumented model identity. Never import or automatically retry
            # it; retain the candidate output for owner adjudication.
            base["parsed"] = None
            base["error"] = None
            base["retry_safe"] = False
            base["quarantine"] = {
                "type": "provider_model_identity_mismatch",
                "message": (f"requested {requested_model!r}, provider reported "
                            f"{payload.get('model')!r}"),
                "candidate_parsed": parsed,
            }
            return base
        base["parsed"] = parsed
        base["error"] = None
        base["retry_safe"] = False  # completed calls are immutable
        base["quarantine"] = None
    except AdapterError as exc:
        base["parsed"] = None
        base["error"] = _error_object("response_validation_error", str(exc))
        base["retry_safe"] = True
        base["quarantine"] = None
    return base


def execute_smoke(canonical: dict[str, Any], transport: Any) -> dict[str, Any]:
    validate_canonical_row(canonical)
    requested_utc = utc_now()
    try:
        result = transport.json(
            "POST", "/responses", to_responses_body(canonical),
            client_request_id=canonical["call_id"])
    except ProviderHTTPError as exc:
        base = {
            "schema_version": NORMALIZED_CONTRACT,
            "call_id": canonical["call_id"],
            "custom_id": canonical["call_id"],
            "provider": "openai",
            "provider_request_id": exc.request_id,
            "provider_response_id": None,
            "usage": None,
            "response": {
                "provider": "openai", "endpoint": ENDPOINT,
                "http_status": exc.status, "request_id": exc.request_id,
                "client_request_id": canonical["call_id"],
                "provider_response_id": None, "model": None, "usage": None,
                "raw": exc.payload,
            },
            "parsed": None,
            "requested_utc": requested_utc,
            "stop_reason": None,
        }
        if exc.ambiguous:
            base["error"] = None
            base["retry_safe"] = False
            base["quarantine"] = {
                "type": "ambiguous_provider_transport",
                "message": exc.safe_message,
                "client_request_id": canonical["call_id"],
                "instruction": "Do not retry until provider receipt status is resolved.",
            }
        else:
            base["error"] = _http_error_object(exc)
            base["retry_safe"] = True
            base["quarantine"] = None
        return base
    return _normalize_payload(canonical, result["payload"], result,
                              requested_utc=requested_utc)


def _read_existing_live_receipts(path: pathlib.Path | str,
                                 canonical_ids: set[str]) -> set[str]:
    path = pathlib.Path(path)
    if not path.exists():
        return set()
    done: set[str] = set()
    for row in read_jsonl(path):
        call_id = row.get("call_id")
        if call_id not in canonical_ids:
            raise AdapterError(f"{path}: receipt {call_id!r} is not in the canonical file")
        done.add(call_id)
    return done


def run_live(canonical_path: pathlib.Path | str, output_path: pathlib.Path | str,
             *, max_calls: int, transport: Any, concurrency: int = 1,
             progress_every: int = 25, print_fn=print) -> dict[str, int]:
    """Resumable live Responses executor for small probe runs.

    Same receipt semantics as ``providers.openai_compat.run_live``: append-only
    normalized receipts written whole-line + fsync by one thread at a time;
    resume skips any call_id that already has a receipt in ``output_path``
    (retries of failed calls therefore go through a fresh missing-only export
    and a fresh output file). Live calls pay the undiscounted rate — use the
    batch flow for large arms; this exists so a few-hundred-call probe does
    not sit in the batch queue.
    """
    if not isinstance(max_calls, int) or max_calls < 1:
        raise AdapterError("--max-calls must be a positive integer hard cap")
    if not isinstance(concurrency, int) or concurrency < 1:
        raise AdapterError("--concurrency must be a positive integer")
    rows = load_canonical_rows(canonical_path)
    if len(rows) > max_calls:
        raise AdapterError(
            f"refusing: canonical file contains {len(rows)} rows, which exceeds "
            f"--max-calls {max_calls}; approve a larger cap explicitly")
    canonical_ids = {row["call_id"] for row in rows}
    done = _read_existing_live_receipts(output_path, canonical_ids)
    pending = [row for row in rows if row["call_id"] not in done]
    counts = {"planned": len(rows), "skipped": len(done), "attempted": 0,
              "completed": 0, "failed": 0, "quarantined": 0}
    output_path = pathlib.Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer_lock = threading.Lock()
    with output_path.open("a", encoding="utf-8") as fh:
        def one(row: dict[str, Any]) -> None:
            receipt = execute_smoke(row, transport)
            with writer_lock:
                fh.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                counts["attempted"] += 1
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

        if concurrency == 1:
            for row in pending:
                one(row)
        else:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=concurrency) as pool:
                for future in [pool.submit(one, row) for row in pending]:
                    future.result()
    return counts


def submit_batch(canonical_path: pathlib.Path | str, batch_path: pathlib.Path | str,
                 expect_requests: int, transport: Any) -> dict[str, Any]:
    batch_path = pathlib.Path(batch_path)
    canonical_path = pathlib.Path(canonical_path)
    canonical_rows, rows = validate_batch_binding(
        canonical_path, batch_path, expect_requests)
    receipt: dict[str, Any] = {
        "schema_version": ADAPTER_CONTRACT,
        "provider": "openai",
        "operation": "batch_submit",
        "submitted_utc": utc_now(),
        "canonical_jsonl_sha256": file_sha256(canonical_path),
        "batch_jsonl_sha256": file_sha256(batch_path),
        "n_requests": len(rows),
        "model": canonical_rows[0]["model"],
        "endpoint": ENDPOINT,
        "completion_window": COMPLETION_WINDOW,
        "input_file_id": None,
        "batch_id": None,
        "upload": None,
        "batch": None,
        "error": None,
    }
    try:
        upload = transport.upload_batch_file(batch_path)
        upload_id = upload["payload"].get("id")
        if not isinstance(upload_id, str) or not upload_id.startswith("file-"):
            raise AdapterError("provider upload response has no valid file ID")
        if upload["payload"].get("purpose") not in (None, "batch"):
            raise AdapterError("provider upload response purpose is not batch")
        receipt["input_file_id"] = upload_id
        receipt["upload"] = upload
        created = transport.json("POST", "/batches", {
            "input_file_id": upload_id,
            "endpoint": ENDPOINT,
            "completion_window": COMPLETION_WINDOW,
        })
        batch = created["payload"]
        batch_id = batch.get("id")
        if not isinstance(batch_id, str) or not batch_id.startswith("batch_"):
            raise AdapterError("provider batch response has no valid batch ID")
        if batch.get("input_file_id") != upload_id:
            raise AdapterError("provider batch response input_file_id mismatch")
        if batch.get("endpoint") != ENDPOINT:
            raise AdapterError("provider batch response endpoint mismatch")
        if batch.get("completion_window") != COMPLETION_WINDOW:
            raise AdapterError("provider batch response completion_window mismatch")
        receipt["batch_id"] = batch_id
        receipt["batch"] = created
    except ProviderHTTPError as exc:
        receipt["error"] = _http_error_object(exc)
    except AdapterError as exc:
        receipt["error"] = _error_object("provider_receipt_validation_error", str(exc))
    return receipt


def resolve_batch_id(batch_id: str | None, submit_receipt: pathlib.Path | str | None) -> str:
    if bool(batch_id) == bool(submit_receipt):
        raise AdapterError("provide exactly one of --batch-id or --submit-receipt")
    if submit_receipt:
        path = pathlib.Path(submit_receipt)
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError(f"cannot read submit receipt {path}: {exc}") from exc
        if not isinstance(receipt, dict) or receipt.get("error"):
            raise AdapterError(f"{path}: submit receipt is not successful")
        batch_id = receipt.get("batch_id")
    if not isinstance(batch_id, str) or not re.fullmatch(r"batch_[A-Za-z0-9_-]+", batch_id):
        raise AdapterError("invalid batch ID")
    return batch_id


def retrieve_batch(batch_id: str, transport: Any) -> dict[str, Any]:
    result = transport.json("GET", f"/batches/{batch_id}")
    batch = result["payload"]
    if batch.get("id") != batch_id:
        raise AdapterError("provider batch status ID mismatch")
    if batch.get("endpoint") != ENDPOINT:
        raise AdapterError("provider batch status endpoint mismatch")
    status = batch.get("status")
    if status not in KNOWN_BATCH_STATUSES:
        raise AdapterError(f"provider returned unknown batch status {status!r}")
    created_at = batch.get("created_at")
    submitted_utc = (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created_at))
                     if isinstance(created_at, (int, float)) else None)
    return {
        "schema_version": ADAPTER_CONTRACT,
        "provider": "openai",
        "operation": "batch_status",
        "checked_utc": utc_now(),
        "submitted_utc": submitted_utc,
        "batch_id": batch_id,
        "input_file_id": batch.get("input_file_id"),
        "output_file_id": batch.get("output_file_id"),
        "error_file_id": batch.get("error_file_id"),
        "status": status,
        "request_counts": batch.get("request_counts"),
        "response": result,
        "error": None,
    }


def wait_batch(batch_id: str, transport: Any, *, poll_seconds: float,
               timeout_seconds: float, sleep_fn=time.sleep) -> dict[str, Any]:
    if not 1 <= poll_seconds <= 60:
        raise AdapterError("poll interval must be between 1 and 60 seconds")
    if timeout_seconds <= 0:
        raise AdapterError("wait timeout must be positive")
    started = time.monotonic()
    last_status = None
    while True:
        receipt = retrieve_batch(batch_id, transport)
        status = receipt["status"]
        if status != last_status:
            counts = receipt.get("request_counts") or {}
            print(f"batch {batch_id}: {status} "
                  f"completed={counts.get('completed', 0)} failed={counts.get('failed', 0)}")
            last_status = status
        if status in TERMINAL_BATCH_STATUSES:
            return receipt
        if time.monotonic() - started >= timeout_seconds:
            receipt["error"] = _error_object(
                "batch_wait_timeout", f"batch did not finish within {timeout_seconds:g}s")
            return receipt
        sleep_fn(poll_seconds)


def download_batch(batch_id: str, output_dir: pathlib.Path | str,
                   transport: Any) -> dict[str, Any]:
    output_dir = pathlib.Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise AdapterError(f"refusing to reuse download directory {output_dir}") from exc
    status_receipt = retrieve_batch(batch_id, transport)
    status = status_receipt["status"]
    if status not in TERMINAL_BATCH_STATUSES:
        raise AdapterError(f"batch {batch_id} is not terminal (status={status})")

    downloaded: dict[str, Any] = {}
    for kind, file_id in (("output", status_receipt.get("output_file_id")),
                          ("errors", status_receipt.get("error_file_id"))):
        if not file_id:
            continue
        if not isinstance(file_id, str) or not re.fullmatch(r"file-[A-Za-z0-9_-]+", file_id):
            raise AdapterError(f"provider returned invalid {kind} file ID")
        result = transport.bytes(f"/files/{file_id}/content")
        path = output_dir / f"{kind}.jsonl"
        with path.open("xb") as fh:
            fh.write(result["content"])
        downloaded[kind] = {
            "file_id": file_id,
            "path": path.name,
            "sha256": file_sha256(path),
            "http_status": result.get("http_status"),
            "headers": result.get("headers"),
        }
    receipt = {
        **status_receipt,
        "operation": "batch_download",
        "downloaded_utc": utc_now(),
        "downloaded": downloaded,
    }
    write_json_exclusive(output_dir / "batch.json", receipt)
    return receipt


def _batch_meta_from_download_dir(download_dir: pathlib.Path) -> dict[str, Any]:
    path = download_dir / "batch.json"
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"cannot read batch receipt {path}: {exc}") from exc
    if not isinstance(receipt, dict) or receipt.get("provider") != "openai":
        raise AdapterError(f"{path}: invalid OpenAI batch receipt")
    return {
        "batch_id": receipt.get("batch_id"),
        "input_file_id": receipt.get("input_file_id"),
        "output_file_id": receipt.get("output_file_id"),
        "error_file_id": receipt.get("error_file_id"),
        "requested_utc": receipt.get("submitted_utc") or receipt.get("checked_utc"),
    }


def _normalize_batch_error(canonical: dict[str, Any], provider_row: dict[str, Any],
                           batch_meta: dict[str, Any]) -> dict[str, Any]:
    error = provider_row.get("error")
    response = provider_row.get("response")
    status = response.get("status_code") if isinstance(response, dict) else None
    request_id = response.get("request_id") if isinstance(response, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("code") or "batch request failed")
        code = error.get("code")
    elif error:
        message, code = str(error), None
    elif isinstance(response, dict):
        body = response.get("body")
        message = _provider_error_message(body, status)
        code = (body.get("error") or {}).get("code") if isinstance(body, dict) \
            and isinstance(body.get("error"), dict) else None
    else:
        message, code = "batch request failed without provider error details", None
    receipt = _provider_receipt(
        response.get("body") if isinstance(response, dict) else None,
        {"http_status": status, "request_id": request_id},
        batch_meta=batch_meta, raw_batch_row=provider_row)
    return {
        "schema_version": NORMALIZED_CONTRACT,
        "call_id": canonical["call_id"],
        "custom_id": canonical["call_id"],
        "provider": "openai",
        "provider_batch_request_id": provider_row.get("id"),
        "provider_request_id": request_id,
        "provider_response_id": None,
        "usage": None,
        "response": receipt,
        "parsed": None,
        "error": _error_object("provider_batch_error", " ".join(message.split())[:1000],
                               status=status, request_id=request_id, code=code),
        "requested_utc": batch_meta.get("requested_utc"),
        "stop_reason": None,
    }


def normalize_batch(canonical_path: pathlib.Path | str,
                    download_dir: pathlib.Path | str,
                    output_path: pathlib.Path | str) -> dict[str, int]:
    canonical_rows = load_canonical_rows(canonical_path)
    canonical_by_id = {row["call_id"]: row for row in canonical_rows}
    download_dir = pathlib.Path(download_dir)
    batch_meta = _batch_meta_from_download_dir(download_dir)

    provider_rows: list[dict[str, Any]] = []
    for filename in ("output.jsonl", "errors.jsonl"):
        path = download_dir / filename
        if path.exists():
            provider_rows.extend(read_jsonl(path))
    if not provider_rows:
        raise AdapterError(f"{download_dir}: no output.jsonl or errors.jsonl rows")

    by_id: dict[str, dict[str, Any]] = {}
    for provider_row in provider_rows:
        call_id = _require_nonempty_string(
            provider_row.get("custom_id"), "provider result custom_id")
        if call_id not in canonical_by_id:
            raise AdapterError(f"provider result contains unknown custom_id {call_id}")
        if call_id in by_id:
            raise AdapterError(f"provider results contain duplicate custom_id {call_id}")
        by_id[call_id] = provider_row

    normalized: list[dict[str, Any]] = []
    completed = failed = quarantined = 0
    for canonical in canonical_rows:
        provider_row = by_id.get(canonical["call_id"])
        if provider_row is None:
            continue
        response = provider_row.get("response")
        if provider_row.get("error") or not isinstance(response, dict) \
                or response.get("status_code") != 200:
            normalized.append(_normalize_batch_error(canonical, provider_row, batch_meta))
            failed += 1
            continue
        payload = response.get("body")
        if not isinstance(payload, dict):
            raise AdapterError(f"{canonical['call_id']}: successful batch row has no body object")
        row = _normalize_payload(
            canonical, payload,
            {"http_status": response.get("status_code"),
             "request_id": response.get("request_id")},
            batch_meta=batch_meta, raw_batch_row=provider_row,
            requested_utc=batch_meta.get("requested_utc"))
        row["provider_batch_request_id"] = provider_row.get("id")
        normalized.append(row)
        if row.get("quarantine"):
            quarantined += 1
        elif row.get("error"):
            failed += 1
        else:
            completed += 1
    write_jsonl_exclusive(output_path, normalized)
    return {
        "expected": len(canonical_rows),
        "present": len(normalized),
        "completed": completed,
        "failed": failed,
        "quarantined": quarantined,
        "missing": len(canonical_rows) - len(normalized),
    }


def _add_live_args(parser: argparse.ArgumentParser, *, execute: bool = False) -> None:
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE),
                        help="literal dotenv fallback; only OPENAI_API_KEY is read")
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="per-request HTTPS timeout in seconds")
    if execute:
        parser.add_argument("--execute", action="store_true",
                            help="required acknowledgement for a model/batch submission")


def _transport(args: argparse.Namespace) -> OpenAITransport:
    return OpenAITransport(load_api_key(args.env_file), args.timeout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare-batch", help="canonical JSONL -> OpenAI Batch JSONL")
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--output", required=True)

    smoke = commands.add_parser("smoke", help="execute exactly one Responses API call")
    smoke.add_argument("--input", required=True, help="canonical JSONL containing one row")
    smoke.add_argument("--output", required=True, help="new normalized-result JSONL")
    _add_live_args(smoke, execute=True)

    live = commands.add_parser(
        "run-live", help="resumable live Responses executor for small probe runs")
    live.add_argument("--canonical", required=True, help="canonical JSONL export")
    live.add_argument("--output", required=True,
                      help="append-only normalized-result JSONL (resumable)")
    live.add_argument("--max-calls", required=True, type=int,
                      help="approved hard cap; refused if canonical rows exceed it")
    live.add_argument("--concurrency", type=int, default=1,
                      help="maximum calls in flight (default 1 = sequential)")
    _add_live_args(live, execute=True)

    submit = commands.add_parser("batch-submit", help="upload and create one OpenAI Batch")
    submit.add_argument("--canonical", required=True,
                        help="matching provider-neutral canonical JSONL")
    submit.add_argument("--input", required=True, help="validated OpenAI Batch JSONL")
    submit.add_argument("--expect-requests", required=True, type=int,
                        help="approved exact request count; must match the file")
    submit.add_argument("--receipt", required=True, help="new immutable submission receipt")
    _add_live_args(submit, execute=True)

    status = commands.add_parser("batch-status", help="retrieve one batch status snapshot")
    status.add_argument("--batch-id")
    status.add_argument("--submit-receipt")
    status.add_argument("--receipt", required=True, help="new immutable status receipt")
    _add_live_args(status)

    wait = commands.add_parser("batch-wait", help="poll until a batch is terminal")
    wait.add_argument("--batch-id")
    wait.add_argument("--submit-receipt")
    wait.add_argument("--receipt", required=True, help="new immutable terminal receipt")
    wait.add_argument("--poll-seconds", type=float, default=30.0)
    wait.add_argument("--wait-timeout", type=float, default=90000.0,
                      help="total poll timeout (default 25h)")
    _add_live_args(wait)

    download = commands.add_parser("batch-download", help="download terminal batch files")
    download.add_argument("--batch-id")
    download.add_argument("--submit-receipt")
    download.add_argument("--output-dir", required=True,
                          help="new directory for batch.json/output.jsonl/errors.jsonl")
    _add_live_args(download)

    normalize = commands.add_parser(
        "normalize-batch", help="reconcile downloaded rows to run.py import JSONL")
    normalize.add_argument("--input", required=True, help="matching canonical JSONL")
    normalize.add_argument("--download-dir", required=True)
    normalize.add_argument("--output", required=True, help="new normalized-result JSONL")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare-batch":
            count = prepare_batch(args.input, args.output)
            print(f"prepared {count} request(s) -> {args.output}")
            return 0

        if args.command == "smoke":
            if not args.execute:
                raise AdapterError("smoke requires --execute; no request was sent")
            canonical = load_canonical_rows(args.input, exactly_one=True)[0]
            transport = _transport(args)
            with reserve_text_output(args.output) as receipt_fh:
                result = execute_smoke(canonical, transport)
                write_jsonl_handle(receipt_fh, [result])
            if result.get("quarantine"):
                print(f"smoke quarantined for {canonical['call_id']}; receipt -> {args.output}")
                return 3
            if result.get("error"):
                print(f"smoke failed for {canonical['call_id']}; receipt -> {args.output}")
                return 1
            print(f"smoke completed for {canonical['call_id']} -> {args.output}")
            return 0

        if args.command == "run-live":
            if not args.execute:
                raise AdapterError("run-live requires --execute; no request was sent")
            counts = run_live(args.canonical, args.output,
                              max_calls=args.max_calls,
                              transport=_transport(args),
                              concurrency=args.concurrency)
            print(f"run-live: planned={counts['planned']} skipped={counts['skipped']} "
                  f"attempted={counts['attempted']} completed={counts['completed']} "
                  f"failed={counts['failed']} quarantined={counts['quarantined']} "
                  f"-> {args.output}")
            return 0 if not counts["failed"] and not counts["quarantined"] else 3

        if args.command == "batch-submit":
            if not args.execute:
                raise AdapterError("batch-submit requires --execute; no batch was submitted")
            validate_batch_binding(args.canonical, args.input, args.expect_requests)
            transport = _transport(args)
            with reserve_text_output(args.receipt) as receipt_fh:
                receipt = submit_batch(
                    args.canonical, args.input, args.expect_requests, transport)
                write_json_handle(receipt_fh, receipt)
            if receipt.get("error"):
                print(f"batch submission failed; receipt -> {args.receipt}")
                return 1
            print(f"submitted batch {receipt['batch_id']} ({receipt['n_requests']} requests) "
                  f"-> {args.receipt}")
            return 0

        if args.command in ("batch-status", "batch-wait", "batch-download"):
            batch_id = resolve_batch_id(args.batch_id, args.submit_receipt)
            transport = _transport(args)
            if args.command == "batch-status":
                with reserve_text_output(args.receipt) as receipt_fh:
                    receipt = retrieve_batch(batch_id, transport)
                    write_json_handle(receipt_fh, receipt)
                print(f"batch {batch_id}: {receipt['status']} -> {args.receipt}")
                return 0
            if args.command == "batch-wait":
                with reserve_text_output(args.receipt) as receipt_fh:
                    receipt = wait_batch(
                        batch_id, transport, poll_seconds=args.poll_seconds,
                        timeout_seconds=args.wait_timeout)
                    write_json_handle(receipt_fh, receipt)
                return 1 if receipt.get("error") else 0
            receipt = download_batch(batch_id, args.output_dir, transport)
            print(f"downloaded batch {batch_id} ({receipt['status']}) -> {args.output_dir}")
            return 0

        if args.command == "normalize-batch":
            counts = normalize_batch(args.input, args.download_dir, args.output)
            print("normalized : "
                  f"completed={counts['completed']} failed={counts['failed']} "
                  f"quarantined={counts['quarantined']} missing={counts['missing']} "
                  f"-> {args.output}")
            return 3 if counts["quarantined"] else 0
        raise AdapterError(f"unknown command {args.command}")
    except (AdapterError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
