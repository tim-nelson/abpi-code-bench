#!/usr/bin/env python3
"""Strict Google Gemini generateContent/Batch benchmark adapter.

The benchmark planners own prompts and stable call identities.  This module
validates their provider-neutral JSONL, submits an explicitly authorised
single-call validation or Gemini Batch job, preserves raw provider receipts,
and emits normalized JSONL for ``bench/run.py --import-results``.

The two reproducible model conditions supported here are deliberately narrow:

* ``gemini-3.1-pro-preview``: thinkingConfig.thinkingLevel pinned to ``HIGH``
  (the documented vendor default for this model);
* ``gemini-3.6-flash``: thinkingConfig.thinkingLevel pinned to ``MEDIUM``
  (the documented vendor default for this model).

The vendor defaults are pinned explicitly in ``MODEL_CONFIGS`` and sent on
every request so a silent vendor-side default change cannot alter the fixed
condition.  No temperature/topP/topK field is ever sent.

Request mapping (canonical ``pmcpa.zero-provider.v2`` -> GenerateContentRequest):

* canonical ``system``            -> ``systemInstruction.parts[0].text``;
* canonical ``messages``          -> ``contents`` (role ``assistant`` -> ``model``);
* canonical ``max_tokens`` (4096) -> ``generationConfig.maxOutputTokens``;
* canonical JSON schema           -> ``generationConfig.responseMimeType``
  ``application/json`` plus ``generationConfig.responseJsonSchema`` (the
  current JSON-Schema structured-output constraint; ``responseSchema`` is the
  deprecated OpenAPI-subset predecessor).

Batch flow (50% batch discount): the batch input is a JSONL file whose lines
are ``{"key": <call_id>, "request": <GenerateContentRequest>}``.  The ``key``
field is the provider's documented per-request identity; results echo it, so
unordered results reconcile exactly.  The file is uploaded with the File API
resumable-upload protocol, the job is created with
``POST /v1beta/models/{model}:batchGenerateContent`` referencing
``inputConfig.fileName``, and the terminal results file is fetched from
``GET /download/v1beta/{file}:download?alt=media``.

Verified against the official docs retrieved 2026-08-16:

* https://ai.google.dev/gemini-api/docs/batch-api (endpoints, JSONL ``key``
  binding, resumable upload, job states, 50% pricing, 48h expiry);
* https://ai.google.dev/gemini-api/docs/structured-output (JSON-schema
  response constraint);
* https://ai.google.dev/gemini-api/docs/models (exact model IDs);
* https://ai.google.dev/gemini-api/docs/pricing (standard/batch prices);
* https://ai.google.dev/gemini-api/docs/thinking (per-model thinking levels
  and defaults);
* https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta
  (machine-readable field inventory: GenerationConfig.responseJsonSchema,
  ThinkingConfig.thinkingLevel enum MINIMAL/LOW/MEDIUM/HIGH, GenerateContentBatch
  states, InputConfig.fileName, GenerateContentBatchOutput.responsesFile).

The docs render batch states as ``JOB_STATE_*`` while the discovery document
declares ``BATCH_STATE_*``; this adapter accepts either prefix and refuses any
other state string.

No third-party SDK is required.  Live commands require ``--execute`` and read
only ``GEMINI_API_KEY`` from the environment or a literal dotenv file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


ADAPTER_CONTRACT = "pmcpa.gemini-generate.v1"
NORMALIZED_CONTRACT = "pmcpa.gemini-normalized.v1"
CANONICAL_CONTRACT = "pmcpa.zero-provider.v2"
API_BASE = "https://generativelanguage.googleapis.com"
API_HOST = "generativelanguage.googleapis.com"
UPLOAD_PATH = "/upload/v1beta/files"
UPLOAD_MIME_TYPE = "application/json"
MAX_OUTPUT_TOKENS = 4096
P3_AGGREGATION = "linear_probability_pool"

PRO_MODEL = "gemini-3.1-pro-preview"
FLASH_MODEL = "gemini-3.7-flash"
LEGACY_FLASH_MODEL = "gemini-3.6-flash"
# Thinking is always on for Gemini 3 models.  Each condition pins the
# documented vendor-default level explicitly so a provider-side default change
# cannot silently alter the fixed configuration.  3.7 Flash is the current
# stable Flash (pricing identical to 3.6 Flash on the 2026-08-16 pricing
# page, promotional through 2026-12-31); 3.6 Flash stays admitted for
# continuity should a comparison arm need it.
MODEL_CONFIGS = {
    PRO_MODEL: {"thinking_level": "HIGH"},
    FLASH_MODEL: {"thinking_level": "MEDIUM"},
    LEGACY_FLASH_MODEL: {"thinking_level": "MEDIUM"},
}
THINKING_LEVELS = frozenset(("MINIMAL", "LOW", "MEDIUM", "HIGH"))

EXPECTED_ANSWERS = {
    "T1": ["breach", "no_breach"],
    "T2": ["breach", "no_breach"],
    "T3": ["upheld", "overturned"],
}
BATCH_STATE_PATTERN = re.compile(
    r"(?:BATCH_STATE|JOB_STATE)_(PENDING|RUNNING|SUCCEEDED|FAILED|CANCELLED|EXPIRED)")
TERMINAL_BATCH_STATES = frozenset(("SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"))
BATCH_NAME_PATTERN = re.compile(r"batches/[A-Za-z0-9_-]+")
FILE_NAME_PATTERN = re.compile(r"files/[a-z0-9][a-z0-9-]{0,39}")
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


class AdapterError(ValueError):
    """Local contract, identity, or provider-response validation failure."""


class ProviderHTTPError(RuntimeError):
    """Sanitized provider failure; never contains request headers or keys."""

    def __init__(self, status: int | None, request_id: str | None, payload: Any,
                 message: str, *, ambiguous: bool = False):
        super().__init__(message)
        self.status = status
        self.request_id = request_id
        self.payload = payload
        self.safe_message = message
        self.ambiguous = ambiguous


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
    path = pathlib.Path(path)
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            row = strict_json_loads(line, f"{path}:{lineno}")
            if not isinstance(row, dict):
                raise AdapterError(f"{path}:{lineno}: JSONL row must be an object")
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


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdapterError(f"{field} must be a non-empty string")
    return value


def _validate_schema(schema: Any, path: str = "schema") -> None:
    if not isinstance(schema, dict):
        raise AdapterError(f"{path} must be an object")
    typ = schema.get("type")
    common = {"type", "description", "enum"}
    if typ == "object":
        if set(schema) - (common | {"properties", "required", "additionalProperties"}):
            raise AdapterError(f"{path} contains unsupported JSON Schema keywords")
        props, required = schema.get("properties"), schema.get("required")
        if not isinstance(props, dict) or not props:
            raise AdapterError(f"{path}.properties must be a non-empty object")
        if not isinstance(required, list) or set(required) != set(props):
            raise AdapterError(f"{path}: all properties must be required")
        if len(required) != len(set(required)):
            raise AdapterError(f"{path}.required contains duplicates")
        if schema.get("additionalProperties") is not False:
            raise AdapterError(f"{path}.additionalProperties must be false")
        for name, child in props.items():
            _require_string(name, f"{path} property name")
            _validate_schema(child, f"{path}.{name}")
    elif typ == "array":
        if set(schema) - (common | {"items"}) or "items" not in schema:
            raise AdapterError(f"{path}: invalid array schema")
        _validate_schema(schema["items"], f"{path}.items")
    elif typ not in ("string", "number", "integer", "boolean", "null"):
        raise AdapterError(f"{path}.type is unsupported: {typ!r}")
    elif set(schema) - common:
        raise AdapterError(f"{path} contains unsupported JSON Schema keywords")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise AdapterError(f"{path}.enum must be a non-empty array")


def _validate_instance(value: Any, schema: dict[str, Any], path: str = "output") -> None:
    typ = schema["type"]
    if typ == "object":
        if not isinstance(value, dict):
            raise AdapterError(f"{path} must be an object")
        props = schema["properties"]
        if set(value) != set(props):
            raise AdapterError(f"{path} fields must be exactly {sorted(props)}")
        for name, child in props.items():
            _validate_instance(value[name], child, f"{path}.{name}")
    elif typ == "array":
        if not isinstance(value, list):
            raise AdapterError(f"{path} must be an array")
        for index, child in enumerate(value):
            _validate_instance(child, schema["items"], f"{path}[{index}]")
    elif typ == "string" and not isinstance(value, str):
        raise AdapterError(f"{path} must be a string")
    elif typ == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise AdapterError(f"{path} must be a number")
    elif typ == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise AdapterError(f"{path} must be an integer")
    elif typ == "boolean" and not isinstance(value, bool):
        raise AdapterError(f"{path} must be boolean")
    elif typ == "null" and value is not None:
        raise AdapterError(f"{path} must be null")
    if "enum" in schema and value not in schema["enum"]:
        raise AdapterError(f"{path} value {value!r} is outside the enum")


def validate_canonical_row(row: dict[str, Any]) -> None:
    if row.get("schema_version") != CANONICAL_CONTRACT:
        raise AdapterError("unsupported canonical contract")
    call_id = _require_string(row.get("call_id"), "call_id")
    if row.get("custom_id") != call_id:
        raise AdapterError(f"{call_id}: call_id/custom_id mismatch")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", call_id):
        raise AdapterError(
            f"{call_id}: custom_id must match [A-Za-z0-9_-]{{1,64}}")
    model = _require_string(row.get("model"), f"{call_id}.model")
    if model not in MODEL_CONFIGS:
        raise AdapterError(f"{call_id}: unsupported exact Gemini model {model!r}")
    protocol = row.get("protocol")
    if protocol not in ("P1", "P2", "P3"):
        raise AdapterError(f"{call_id}: unsupported protocol {protocol!r}")
    if protocol == "P3" and row.get("aggregation") != P3_AGGREGATION:
        raise AdapterError(f"{call_id}: native P3 requires linear_probability_pool")
    task = row.get("task")
    if task not in EXPECTED_ANSWERS:
        raise AdapterError(f"{call_id}: unsupported task {task!r}")
    _require_string(row.get("config_hash"), f"{call_id}.config_hash")
    request = row.get("request")
    if not isinstance(request, dict):
        raise AdapterError(f"{call_id}.request must be an object")
    allowed = {"model", "max_tokens", "system", "messages", "output_config"}
    unknown = set(request) - allowed
    if unknown:
        if "thinking" in unknown:
            raise AdapterError(
                f"{call_id}: Gemini export requires --thinking unset; the "
                "thinking level is pinned per model by this adapter")
        if "temperature" in unknown:
            raise AdapterError(
                f"{call_id}: Gemini configuration requires temperature unset")
        raise AdapterError(
            f"{call_id}.request has unsupported fields {sorted(unknown)}")
    if request.get("model") != model or request.get("max_tokens") != MAX_OUTPUT_TOKENS:
        raise AdapterError(f"{call_id}: model/max_tokens differs from fixed condition")
    system = _require_string(request.get("system"), f"{call_id}.request.system")
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise AdapterError(f"{call_id}.request.messages must be non-empty")
    for index, message in enumerate(messages):
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message.get("role") not in ("user", "assistant")
                or not isinstance(message.get("content"), str)
                or not message["content"]):
            raise AdapterError(f"{call_id}.request.messages[{index}] is not canonical")
    output_config = request.get("output_config")
    if not isinstance(output_config, dict) or set(output_config) != {"format"}:
        if isinstance(output_config, dict) and "effort" in output_config:
            raise AdapterError(
                f"{call_id}: Gemini export requires --effort unset; Gemini has "
                "no effort field and the thinking level is pinned per model")
        raise AdapterError(f"{call_id}: output_config fields mismatch")
    fmt = output_config.get("format")
    if not isinstance(fmt, dict) or set(fmt) != {"type", "schema"} \
            or fmt.get("type") != "json_schema":
        raise AdapterError(f"{call_id}: output format must be canonical json_schema")
    schema = fmt.get("schema")
    _validate_schema(schema, f"{call_id}.schema")
    props = schema["properties"]
    wanted = {"answer", "probability"} if protocol in ("P1", "P3") else {"answer"}
    if set(props) != wanted or set(schema["required"]) != wanted:
        raise AdapterError(f"{call_id}: {protocol} output fields mismatch")
    if props["answer"].get("enum") != EXPECTED_ANSWERS[task]:
        raise AdapterError(f"{call_id}: answer enum does not match task")
    if protocol in ("P1", "P3") and props["probability"].get("type") != "number":
        raise AdapterError(f"{call_id}: probability must be numeric")
    if row.get("request_sha256") != digest(request):
        raise AdapterError(f"{call_id}: request_sha256 mismatch")
    if row.get("prompt_sha256") != digest({"system": system, "messages": messages}):
        raise AdapterError(f"{call_id}: prompt_sha256 mismatch")


def load_canonical_rows(path: pathlib.Path | str, *, exactly_one: bool = False
                        ) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    seen: set[str] = set()
    models: set[str] = set()
    for row in rows:
        validate_canonical_row(row)
        if row["call_id"] in seen:
            raise AdapterError(f"{path}: duplicate call ID {row['call_id']}")
        seen.add(row["call_id"])
        models.add(row["model"])
    if len(models) != 1:
        raise AdapterError(f"{path}: a provider submission must contain one exact model")
    if exactly_one and len(rows) != 1:
        raise AdapterError(f"{path}: smoke requires exactly one row, found {len(rows)}")
    return rows


GEMINI_ROLES = {"user": "user", "assistant": "model"}


def generate_endpoint(model: str) -> str:
    return f"/v1beta/models/{model}:generateContent"


def batch_create_endpoint(model: str) -> str:
    return f"/v1beta/models/{model}:batchGenerateContent"


def to_generate_body(row: dict[str, Any]) -> dict[str, Any]:
    """Translate one validated canonical row to a GenerateContentRequest."""
    validate_canonical_row(row)
    request = row["request"]
    # Deep-copy mechanically via canonical JSON so no planner record is mutated.
    schema = json.loads(canonical_json(
        request["output_config"]["format"]["schema"]).decode("utf-8"))
    return {
        "contents": [
            {"role": GEMINI_ROLES[message["role"]],
             "parts": [{"text": message["content"]}]}
            for message in request["messages"]
        ],
        "systemInstruction": {"parts": [{"text": request["system"]}]},
        "generationConfig": {
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "thinkingConfig": {
                "thinkingLevel": MODEL_CONFIGS[row["model"]]["thinking_level"]},
        },
    }


def _validate_generate_body(body: Any) -> None:
    if not isinstance(body, dict) or set(body) != {
            "contents", "systemInstruction", "generationConfig"}:
        raise AdapterError("batch request body fields do not match adapter contract")
    contents = body["contents"]
    if not isinstance(contents, list) or not contents:
        raise AdapterError("batch body contents must be non-empty")
    for index, content in enumerate(contents):
        if (not isinstance(content, dict) or set(content) != {"role", "parts"}
                or content.get("role") not in ("user", "model")):
            raise AdapterError(f"batch body contents[{index}] is not canonical")
        parts = content.get("parts")
        if (not isinstance(parts, list) or len(parts) != 1
                or not isinstance(parts[0], dict) or set(parts[0]) != {"text"}
                or not isinstance(parts[0].get("text"), str)):
            raise AdapterError(f"batch body contents[{index}].parts is not canonical")
    system = body["systemInstruction"]
    if (not isinstance(system, dict) or set(system) != {"parts"}
            or not isinstance(system.get("parts"), list) or len(system["parts"]) != 1
            or not isinstance(system["parts"][0], dict)
            or set(system["parts"][0]) != {"text"}
            or not isinstance(system["parts"][0].get("text"), str)):
        raise AdapterError("batch body systemInstruction is not canonical")
    config = body["generationConfig"]
    expected = {"maxOutputTokens", "responseMimeType", "responseJsonSchema",
                "thinkingConfig"}
    if not isinstance(config, dict) or set(config) != expected:
        # The exact key set also forbids temperature/topP/topK by construction.
        raise AdapterError("batch body generationConfig fields are not canonical")
    if config.get("maxOutputTokens") != MAX_OUTPUT_TOKENS:
        raise AdapterError(f"batch body maxOutputTokens must be {MAX_OUTPUT_TOKENS}")
    if config.get("responseMimeType") != "application/json":
        raise AdapterError("batch body responseMimeType must be application/json")
    _validate_schema(config.get("responseJsonSchema"))
    thinking = config.get("thinkingConfig")
    if (not isinstance(thinking, dict) or set(thinking) != {"thinkingLevel"}
            or thinking.get("thinkingLevel") not in THINKING_LEVELS):
        raise AdapterError("batch body thinkingConfig is not a pinned thinking level")


def to_batch_row(row: dict[str, Any]) -> dict[str, Any]:
    return {"key": row["call_id"], "request": to_generate_body(row)}


def validate_provider_batch_rows(rows: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != {"key", "request"}:
            raise AdapterError(f"batch row {index} has unexpected fields")
        key = _require_string(row.get("key"), f"batch row {index}.key")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key):
            raise AdapterError(f"batch row {index}: key is not a canonical call ID")
        if key in seen:
            raise AdapterError(f"duplicate batch key {key}")
        seen.add(key)
        _validate_generate_body(row.get("request"))


def prepare_batch(canonical_path: pathlib.Path | str,
                  output_path: pathlib.Path | str) -> int:
    rows = load_canonical_rows(canonical_path)
    provider_rows = [to_batch_row(row) for row in rows]
    validate_provider_batch_rows(provider_rows)
    write_jsonl_exclusive(output_path, provider_rows)
    return len(provider_rows)


def read_json_object(path: pathlib.Path | str) -> dict[str, Any]:
    path = pathlib.Path(path)
    value = strict_json_loads(path.read_text(encoding="utf-8"), str(path))
    if not isinstance(value, dict):
        raise AdapterError(f"{path}: expected one JSON object")
    return value


def validate_batch_binding(canonical_path: pathlib.Path | str,
                           batch_path: pathlib.Path | str,
                           expect_requests: int) -> tuple[list[dict[str, Any]],
                                                          list[dict[str, Any]]]:
    """Prove that a submission file is the exact mechanical canonical export."""
    rows = load_canonical_rows(canonical_path)
    batch_rows = read_jsonl(batch_path)
    validate_provider_batch_rows(batch_rows)
    if expect_requests < 1:
        raise AdapterError("--expect-requests must be positive")
    if len(rows) != expect_requests or len(batch_rows) != expect_requests:
        raise AdapterError(
            f"expected {expect_requests} requests but canonical/provider files "
            f"contain {len(rows)}/{len(batch_rows)}")
    for index, (canonical, actual) in enumerate(zip(rows, batch_rows), 1):
        expected = to_batch_row(canonical)
        if canonical_json(expected) != canonical_json(actual):
            raise AdapterError(
                f"batch row {index} ({canonical['call_id']}) differs from the "
                "mechanical canonical translation")
    return rows, batch_rows


def load_api_key(env_file: pathlib.Path | str | None = DEFAULT_ENV_FILE) -> str:
    """Load only a literal ``GEMINI_API_KEY`` assignment; execute no shell."""
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    if env_file is None:
        raise AdapterError("GEMINI_API_KEY is not set")
    path = pathlib.Path(env_file)
    if not path.exists():
        raise AdapterError(f"dotenv file does not exist: {path}")
    values: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            candidate = line.strip()
            if not candidate or candidate.startswith("#"):
                continue
            if candidate.startswith("export "):
                candidate = candidate[7:].lstrip()
            match = re.fullmatch(r"GEMINI_API_KEY\s*=\s*(.*)", candidate)
            if not match:
                continue
            value = match.group(1).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            elif value.startswith(("'", '"')) or value.endswith(("'", '"')):
                raise AdapterError(f"{path}:{lineno}: unmatched quote")
            if not value:
                raise AdapterError(f"{path}:{lineno}: empty GEMINI_API_KEY")
            values.append(value)
    if not values:
        raise AdapterError(f"{path}: GEMINI_API_KEY not found")
    if len(set(values)) != 1:
        raise AdapterError(f"{path}: conflicting GEMINI_API_KEY assignments")
    return values[0]


def _safe_headers(headers: Any) -> dict[str, str]:
    out = {}
    for name in ("x-goog-request-id", "server-timing", "retry-after"):
        value = headers.get(name) if headers is not None else None
        if value:
            out[name] = str(value)
    return out


def _provider_message(payload: Any, status: int | None) -> str:
    message = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
    if not isinstance(message, str) or not message.strip():
        message = "provider request failed"
    prefix = f"HTTP {status}: " if status is not None else ""
    return prefix + " ".join(message.split())[:1000]


class GeminiTransport:
    """Tiny standard-library HTTPS transport; it never logs requests."""

    def __init__(self, api_key: str, timeout: float = 600.0):
        if not api_key:
            raise AdapterError("GEMINI_API_KEY is empty")
        self._api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, url: str, data: bytes | None,
                 content_type: str | None,
                 extra_headers: dict[str, str] | None = None
                 ) -> tuple[bytes, dict[str, Any]]:
        if url.startswith("/"):
            if not url.startswith(("/v1beta/", "/upload/v1beta", "/download/v1beta/")):
                raise AdapterError("Gemini provider path must begin /v1beta/")
            url = API_BASE + url
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != API_HOST:
            raise AdapterError("refusing non-Gemini provider URL")
        headers = {
            "x-goog-api-key": self._api_key,
            "user-agent": "pmcpa-bench-gemini-adapter/1",
        }
        if content_type:
            headers["content-type"] = content_type
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read(), {
                    "http_status": int(response.status),
                    "headers": _safe_headers(response.headers),
                    "upload_url": response.headers.get("x-goog-upload-url"),
                }
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload: Any = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"error": {"message": "non-JSON provider error"}}
            safe = _safe_headers(exc.headers)
            raise ProviderHTTPError(
                exc.code, safe.get("x-goog-request-id"), payload,
                _provider_message(payload, exc.code),
                ambiguous=(exc.code == 408 or exc.code >= 500)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = " ".join(str(getattr(exc, "reason", exc)).split())[:500]
            raise ProviderHTTPError(None, None, None, f"transport error: {reason}",
                                    ambiguous=True) from exc

    def _decode_json(self, raw: bytes, meta: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderHTTPError(meta["http_status"],
                                    meta["headers"].get("x-goog-request-id"), None,
                                    "provider returned non-JSON content",
                                    ambiguous=True) from exc
        if not isinstance(payload, dict):
            raise ProviderHTTPError(meta["http_status"],
                                    meta["headers"].get("x-goog-request-id"), payload,
                                    "provider returned non-object JSON", ambiguous=True)
        return {"payload": payload, "http_status": meta["http_status"],
                "headers": meta["headers"]}

    def json(self, method: str, path: str, body: dict[str, Any] | None = None
             ) -> dict[str, Any]:
        raw, meta = self._request(method, path,
                                  canonical_json(body) if body is not None else None,
                                  "application/json" if body is not None else None)
        return self._decode_json(raw, meta)

    def bytes(self, path: str) -> dict[str, Any]:
        raw, meta = self._request("GET", path, None, None)
        return {"content": raw, "http_status": meta["http_status"],
                "headers": meta["headers"]}

    def start_upload(self, num_bytes: int, display_name: str) -> str:
        """File API resumable-upload start; returns the scoped upload URL."""
        raw, meta = self._request(
            "POST", UPLOAD_PATH,
            canonical_json({"file": {"displayName": display_name}}),
            "application/json",
            {
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(num_bytes),
                "X-Goog-Upload-Header-Content-Type": UPLOAD_MIME_TYPE,
            })
        upload_url = meta.get("upload_url")
        if not isinstance(upload_url, str) or not upload_url:
            raise ProviderHTTPError(meta["http_status"], None, None,
                                    "upload start returned no x-goog-upload-url",
                                    ambiguous=True)
        parsed = urllib.parse.urlsplit(upload_url)
        if parsed.scheme != "https" or parsed.netloc != API_HOST:
            raise AdapterError("refusing upload URL outside the Gemini API host")
        return upload_url

    def finish_upload(self, upload_url: str, data: bytes) -> dict[str, Any]:
        raw, meta = self._request(
            "POST", upload_url, data, None,
            {"X-Goog-Upload-Command": "upload, finalize",
             "X-Goog-Upload-Offset": "0"})
        return self._decode_json(raw, meta)


def _error(kind: str, message: str, *, status: int | None = None,
           request_id: str | None = None, code: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": kind, "message": message}
    if status is not None:
        out["http_status"] = status
    if request_id:
        out["request_id"] = request_id
    if code is not None:
        out["code"] = code
    return out


def _http_error(exc: ProviderHTTPError) -> dict[str, Any]:
    code = None
    if isinstance(exc.payload, dict) and isinstance(exc.payload.get("error"), dict):
        code = exc.payload["error"].get("status") or exc.payload["error"].get("code")
    return _error("provider_http_error", exc.safe_message, status=exc.status,
                  request_id=exc.request_id, code=code)


def _stop_reason(payload: Any) -> Any:
    if isinstance(payload, dict):
        candidates = payload.get("candidates")
        if isinstance(candidates, list) and candidates \
                and isinstance(candidates[0], dict):
            return candidates[0].get("finishReason")
    return None


def _provider_receipt(canonical: dict[str, Any], payload: Any, meta: dict[str, Any],
                      *, batch_meta: dict[str, Any] | None = None,
                      raw_batch_row: Any = None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    receipt: dict[str, Any] = {
        "provider": "gemini",
        "endpoint": generate_endpoint(canonical["model"]),
        "http_status": meta.get("http_status"),
        "request_id": meta.get("request_id")
            or (meta.get("headers") or {}).get("x-goog-request-id"),
        "provider_response_id": raw.get("responseId"),
        "model": raw.get("modelVersion"),
        "usage": raw.get("usageMetadata"),
        "canonical_request": canonical["request"],
        "raw": payload,
    }
    if raw_batch_row is not None:
        receipt["raw_batch_row"] = raw_batch_row
    if batch_meta:
        receipt["batch"] = {key: batch_meta.get(key) for key in
                            ("batch_name", "responses_file")}
    return receipt


def _extract_text(payload: dict[str, Any]) -> str:
    feedback = payload.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise AdapterError(f"prompt blocked: {feedback.get('blockReason')!r}")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        found = len(candidates) if isinstance(candidates, list) else "none"
        raise AdapterError(f"expected exactly one candidate, found {found}")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise AdapterError("response candidate must be an object")
    if candidate.get("finishReason") != "STOP":
        raise AdapterError(f"candidate finishReason={candidate.get('finishReason')!r}")
    content = candidate.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
        raise AdapterError("candidate content.parts must be an array")
    texts = []
    for part in content["parts"]:
        if not isinstance(part, dict):
            raise AdapterError("response part must be an object")
        if part.get("thought") is True:
            continue
        if not isinstance(part.get("text"), str):
            raise AdapterError("unexpected non-text response part")
        texts.append(part["text"])
    if len(texts) != 1:
        raise AdapterError(f"expected exactly one text part, found {len(texts)}")
    return texts[0]


def normalize_payload(canonical: dict[str, Any], payload: dict[str, Any],
                      meta: dict[str, Any], *, requested_utc: str | None,
                      batch_meta: dict[str, Any] | None = None,
                      raw_batch_row: Any = None) -> dict[str, Any]:
    receipt = _provider_receipt(canonical, payload, meta, batch_meta=batch_meta,
                                raw_batch_row=raw_batch_row)
    base: dict[str, Any] = {
        "schema_version": NORMALIZED_CONTRACT,
        "call_id": canonical["call_id"], "custom_id": canonical["call_id"],
        "provider": "gemini", "provider_request_id": receipt.get("request_id"),
        "provider_response_id": payload.get("responseId"),
        "usage": payload.get("usageMetadata"),
        "response": receipt, "requested_utc": requested_utc,
        "stop_reason": _stop_reason(payload),
    }
    try:
        parsed = strict_json_loads(_extract_text(payload), "structured response")
        schema = canonical["request"]["output_config"]["format"]["schema"]
        _validate_instance(parsed, schema)
        if not isinstance(parsed, dict):
            raise AdapterError("structured response root must be an object")
        if canonical["protocol"] in ("P1", "P3"):
            probability = parsed["probability"]
            if not math.isfinite(float(probability)) or not 0 <= float(probability) <= 1:
                raise AdapterError(f"probability {probability!r} is outside [0,1]")
            parsed["probability"] = float(probability)
        if payload.get("modelVersion") != canonical["model"]:
            # The paid response may be legitimate but the provider resolved a
            # different model identity (Gemini may report a dated version).
            # Never import or automatically retry it; keep the candidate
            # output for owner adjudication.
            base.update({"parsed": None, "error": None, "retry_safe": False,
                         "quarantine": {
                             "type": "provider_model_identity_mismatch",
                             "message": (f"requested {canonical['model']!r}, provider "
                                         f"reported {payload.get('modelVersion')!r}"),
                             "candidate_parsed": parsed,
                         }})
            return base
        base.update({"parsed": parsed, "error": None, "retry_safe": False,
                     "quarantine": None})
    except AdapterError as exc:
        base.update({"parsed": None,
                     "error": _error("response_validation_error", str(exc)),
                     "retry_safe": True, "quarantine": None})
    return base


def execute_smoke(canonical: dict[str, Any], transport: Any) -> dict[str, Any]:
    validate_canonical_row(canonical)
    requested_utc = utc_now()
    try:
        response = transport.json("POST", generate_endpoint(canonical["model"]),
                                  to_generate_body(canonical))
    except ProviderHTTPError as exc:
        base = {
            "schema_version": NORMALIZED_CONTRACT,
            "call_id": canonical["call_id"], "custom_id": canonical["call_id"],
            "provider": "gemini", "provider_request_id": exc.request_id,
            "provider_response_id": None, "usage": None,
            "response": _provider_receipt(canonical, exc.payload,
                                          {"http_status": exc.status,
                                           "request_id": exc.request_id}),
            "parsed": None, "requested_utc": requested_utc, "stop_reason": None,
        }
        if exc.ambiguous:
            base.update({"error": None, "retry_safe": False,
                         "quarantine": {
                             "type": "ambiguous_provider_transport",
                             "message": exc.safe_message,
                             "instruction": "Do not retry without provider-side reconciliation.",
                         }})
        else:
            base.update({"error": _http_error(exc), "retry_safe": True,
                         "quarantine": None})
        return base
    return normalize_payload(canonical, response["payload"], response,
                             requested_utc=requested_utc)


def normalize_batch_state(state: Any) -> str:
    if isinstance(state, str):
        match = BATCH_STATE_PATTERN.fullmatch(state)
        if match:
            return match.group(1)
    raise AdapterError(f"unknown Gemini batch state {state!r}")


def submit_batch(canonical_path: pathlib.Path | str, batch_path: pathlib.Path | str,
                 expect_requests: int, transport: Any, *,
                 sleep_fn=time.sleep, file_poll_seconds: float = 2.0,
                 file_poll_attempts: int = 30) -> dict[str, Any]:
    rows, _ = validate_batch_binding(canonical_path, batch_path, expect_requests)
    data = pathlib.Path(batch_path).read_bytes()
    batch_sha = file_sha256(batch_path)
    receipt: dict[str, Any] = {
        "schema_version": ADAPTER_CONTRACT, "provider": "gemini",
        "operation": "batch_submit", "submitted_utc": utc_now(),
        "canonical_jsonl_sha256": file_sha256(canonical_path),
        "batch_jsonl_sha256": batch_sha,
        "n_requests": len(rows), "model": rows[0]["model"],
        "input_file_name": None, "batch_name": None,
        "upload": None, "batch": None, "error": None, "quarantine": None,
    }
    try:
        upload_url = transport.start_upload(len(data), f"pmcpa-{batch_sha[:16]}")
        uploaded = transport.finish_upload(upload_url, data)
        file_info = uploaded["payload"].get("file")
        if not isinstance(file_info, dict):
            raise AdapterError("upload response has no file object")
        file_name = file_info.get("name")
        if not isinstance(file_name, str) or not FILE_NAME_PATTERN.fullmatch(file_name):
            raise AdapterError("upload response has no valid file name")
        size = file_info.get("sizeBytes")
        if size is not None and str(size) != str(len(data)):
            raise AdapterError(f"uploaded file size mismatch: {size!r} != {len(data)}")
        state = file_info.get("state")
        attempts = 0
        while state == "PROCESSING" and attempts < file_poll_attempts:
            sleep_fn(file_poll_seconds)
            state = transport.json("GET", f"/v1beta/{file_name}")["payload"].get("state")
            attempts += 1
        if state != "ACTIVE":
            raise AdapterError(f"uploaded batch input file state is {state!r}")
        receipt["input_file_name"] = file_name
        receipt["upload"] = uploaded
        created = transport.json(
            "POST", batch_create_endpoint(rows[0]["model"]),
            {"batch": {"displayName": f"pmcpa-{batch_sha[:16]}",
                       "inputConfig": {"fileName": file_name}}})
        operation = created["payload"]
        batch_name = operation.get("name")
        if not isinstance(batch_name, str) \
                or not BATCH_NAME_PATTERN.fullmatch(batch_name):
            raise AdapterError("invalid Gemini batch creation receipt")
        metadata = operation.get("metadata")
        if isinstance(metadata, dict) and metadata.get("state") is not None:
            normalize_batch_state(metadata.get("state"))
        receipt["batch_name"] = batch_name
        receipt["batch"] = created
    except ProviderHTTPError as exc:
        if exc.ambiguous:
            receipt["quarantine"] = {
                "type": "ambiguous_batch_submission",
                "message": exc.safe_message,
                "instruction": "Do not resubmit; reconcile via the batches listing first.",
            }
        else:
            receipt["error"] = _http_error(exc)
    except AdapterError as exc:
        receipt["error"] = _error("provider_receipt_validation_error", str(exc))
    return receipt


def resolve_batch_name(batch_name: str | None,
                       submit_receipt: pathlib.Path | str | None) -> str:
    if bool(batch_name) == bool(submit_receipt):
        raise AdapterError("provide exactly one of --batch-name or --submit-receipt")
    if submit_receipt:
        receipt = read_json_object(submit_receipt)
        if receipt.get("error") or receipt.get("quarantine"):
            raise AdapterError(f"{submit_receipt}: batch receipt is not resolved-successful")
        batch_name = receipt.get("batch_name")
    if not isinstance(batch_name, str) or not BATCH_NAME_PATTERN.fullmatch(batch_name):
        raise AdapterError("invalid Gemini batch name")
    return batch_name


def retrieve_batch(batch_name: str, transport: Any) -> dict[str, Any]:
    response = transport.json("GET", f"/v1beta/{batch_name}")
    operation = response["payload"]
    if operation.get("name") != batch_name:
        raise AdapterError("Gemini batch status identity mismatch")
    metadata = operation.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    state_raw = metadata.get("state", operation.get("state"))
    state = normalize_batch_state(state_raw)
    op_response = operation.get("response")
    op_response = op_response if isinstance(op_response, dict) else {}
    responses_file = None
    output = op_response.get("output")
    meta_output = metadata.get("output")
    for candidate in (op_response.get("responsesFile"),
                      output.get("responsesFile") if isinstance(output, dict) else None,
                      meta_output.get("responsesFile")
                      if isinstance(meta_output, dict) else None):
        if candidate is not None:
            responses_file = candidate
            break
    return {
        "schema_version": ADAPTER_CONTRACT, "provider": "gemini",
        "operation": "batch_status", "checked_utc": utc_now(),
        "submitted_utc": metadata.get("createTime"), "batch_name": batch_name,
        "state": state, "state_raw": state_raw, "done": bool(operation.get("done")),
        "batch_stats": metadata.get("batchStats"),
        "responses_file": responses_file,
        "operation_error": operation.get("error"),
        "response": response, "error": None,
    }


def wait_batch(batch_name: str, transport: Any, *, poll_seconds: float,
               timeout_seconds: float, sleep_fn=time.sleep) -> dict[str, Any]:
    if not 1 <= poll_seconds <= 60 or timeout_seconds <= 0:
        raise AdapterError("poll interval must be 1..60s and timeout positive")
    started, last = time.monotonic(), None
    while True:
        receipt = retrieve_batch(batch_name, transport)
        state = receipt["state"]
        if state != last:
            stats = receipt.get("batch_stats") or {}
            print(f"batch {batch_name}: {state} "
                  f"pending={stats.get('pendingRequestCount', 0)} "
                  f"succeeded={stats.get('successfulRequestCount', 0)} "
                  f"failed={stats.get('failedRequestCount', 0)}")
            last = state
        if state in TERMINAL_BATCH_STATES:
            return receipt
        if time.monotonic() - started >= timeout_seconds:
            receipt["error"] = _error("batch_wait_timeout",
                                      f"batch did not end within {timeout_seconds:g}s")
            return receipt
        sleep_fn(poll_seconds)


def download_batch(batch_name: str, output_dir: pathlib.Path | str,
                   transport: Any) -> dict[str, Any]:
    output_dir = pathlib.Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise AdapterError(f"refusing to reuse {output_dir}") from exc
    status = retrieve_batch(batch_name, transport)
    if status["state"] != "SUCCEEDED":
        raise AdapterError(
            f"Gemini batch {batch_name} is not SUCCEEDED (state={status['state']}); "
            "there is no results file to download")
    responses_file = status.get("responses_file")
    if not isinstance(responses_file, str) \
            or not FILE_NAME_PATTERN.fullmatch(responses_file):
        raise AdapterError(
            "succeeded batch has no valid responsesFile; this adapter only "
            "supports file-input batches with file output")
    result = transport.bytes(f"/download/v1beta/{responses_file}:download?alt=media")
    raw_path = output_dir / "results.jsonl"
    with raw_path.open("xb") as fh:
        fh.write(result["content"])
    receipt = {
        **status, "operation": "batch_download", "downloaded_utc": utc_now(),
        "downloaded": {"path": raw_path.name, "file_name": responses_file,
                       "sha256": file_sha256(raw_path),
                       "http_status": result.get("http_status"),
                       "headers": result.get("headers")},
    }
    write_json_exclusive(output_dir / "batch.json", receipt)
    return receipt


def _batch_meta(download_dir: pathlib.Path) -> dict[str, Any]:
    receipt = read_json_object(download_dir / "batch.json")
    if receipt.get("provider") != "gemini" or receipt.get("operation") != "batch_download":
        raise AdapterError("invalid Gemini batch download receipt")
    return {
        "batch_name": receipt.get("batch_name"),
        "responses_file": receipt.get("responses_file"),
        "requested_utc": receipt.get("submitted_utc") or receipt.get("checked_utc"),
    }


def _normalize_batch_failure(canonical: dict[str, Any], provider_row: dict[str, Any],
                             batch_meta: dict[str, Any]) -> dict[str, Any]:
    error = provider_row.get("error")
    error = error if isinstance(error, dict) else {}
    message = error.get("message") or "batch request failed without provider details"
    code = error.get("status") or error.get("code")
    receipt = _provider_receipt(canonical, None, {}, batch_meta=batch_meta,
                                raw_batch_row=provider_row)
    return {
        "schema_version": NORMALIZED_CONTRACT,
        "call_id": canonical["call_id"], "custom_id": canonical["call_id"],
        "provider": "gemini", "provider_request_id": None,
        "provider_response_id": None, "usage": None, "response": receipt,
        "parsed": None,
        "error": _error("provider_batch_error", " ".join(str(message).split())[:1000],
                        code=code),
        "retry_safe": True, "quarantine": None,
        "requested_utc": batch_meta.get("requested_utc"), "stop_reason": None,
    }


def normalize_batch(canonical_path: pathlib.Path | str,
                    download_dir: pathlib.Path | str,
                    output_path: pathlib.Path | str) -> dict[str, int]:
    canonical = load_canonical_rows(canonical_path)
    by_id = {row["call_id"]: row for row in canonical}
    download_dir = pathlib.Path(download_dir)
    meta = _batch_meta(download_dir)
    provider_rows = read_jsonl(download_dir / "results.jsonl")
    results: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(provider_rows, 1):
        key = row.get("key")
        if not isinstance(key, str) or not key:
            # Never reconcile positionally: an unkeyed row cannot be bound to
            # a paid call identity, so the whole normalization fails closed.
            raise AdapterError(f"batch result row {index} has no key binding")
        if key not in by_id:
            raise AdapterError(f"batch result has unknown key {key}")
        if key in results:
            raise AdapterError(f"duplicate batch result {key}")
        results[key] = row
    normalized = []
    counts = {"expected": len(canonical), "present": len(results),
              "completed": 0, "failed": 0, "quarantined": 0,
              "missing": len(canonical) - len(results)}
    for call in canonical:
        provider_row = results.get(call["call_id"])
        if provider_row is None:
            continue
        if provider_row.get("error") is not None:
            normalized.append(_normalize_batch_failure(call, provider_row, meta))
            counts["failed"] += 1
            continue
        payload = provider_row.get("response")
        if not isinstance(payload, dict):
            raise AdapterError(
                f"{call['call_id']}: batch result row has neither response nor error")
        row = normalize_payload(call, payload, {}, requested_utc=meta.get("requested_utc"),
                                batch_meta=meta, raw_batch_row=provider_row)
        normalized.append(row)
        if row.get("quarantine"):
            counts["quarantined"] += 1
        elif row.get("error"):
            counts["failed"] += 1
        else:
            counts["completed"] += 1
    write_jsonl_exclusive(output_path, normalized)
    return counts


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    commands = ap.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-batch")
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--output", required=True)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--input", required=True)
    smoke.add_argument("--output", required=True)
    smoke.add_argument("--execute", action="store_true")
    submit = commands.add_parser("batch-submit")
    submit.add_argument("--canonical", required=True)
    submit.add_argument("--input", required=True)
    submit.add_argument("--expect-requests", type=int, required=True)
    submit.add_argument("--receipt", required=True)
    submit.add_argument("--execute", action="store_true")
    for name in ("batch-status", "batch-wait", "batch-download"):
        sub = commands.add_parser(name)
        sub.add_argument("--batch-name")
        sub.add_argument("--submit-receipt")
        if name == "batch-download":
            sub.add_argument("--output-dir", required=True)
        else:
            sub.add_argument("--receipt", required=True)
        if name == "batch-wait":
            sub.add_argument("--poll-seconds", type=float, default=30)
            sub.add_argument("--timeout-seconds", type=float, default=172800)
    normalize = commands.add_parser("normalize-batch")
    normalize.add_argument("--input", required=True)
    normalize.add_argument("--download-dir", required=True)
    normalize.add_argument("--output", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare-batch":
            count = prepare_batch(args.input, args.output)
            print(f"prepared {count} Gemini batch request(s) -> {args.output}")
            return 0
        if args.command == "normalize-batch":
            counts = normalize_batch(args.input, args.download_dir, args.output)
            print(json.dumps(counts, sort_keys=True))
            return 0
        if args.command == "smoke":
            if not args.execute:
                raise AdapterError("smoke requires --execute; no request was sent")
            rows = load_canonical_rows(args.input, exactly_one=True)
            transport = GeminiTransport(load_api_key(args.env_file))
            # Reserve the receipt before the paid call so an interrupted smoke
            # is never silently repeated.
            with reserve_text_output(args.output) as receipt_fh:
                result = execute_smoke(rows[0], transport)
                write_jsonl_handle(receipt_fh, [result])
            print(f"smoke result -> {args.output}")
            return 0 if not result.get("error") and not result.get("quarantine") else 2
        if args.command == "batch-submit":
            if not args.execute:
                raise AdapterError("batch-submit requires --execute; no batch was sent")
            # Reserve the receipt before upload/POST. An ambiguous submission
            # is therefore never silently repeated and is durably quarantined.
            with reserve_text_output(args.receipt) as receipt_fh:
                receipt = submit_batch(args.canonical, args.input,
                                       args.expect_requests,
                                       GeminiTransport(load_api_key(args.env_file)))
                write_json_handle(receipt_fh, receipt)
            if receipt.get("error") or receipt.get("quarantine"):
                print(f"batch submission unresolved/failed -> {args.receipt}")
                return 2
            print(f"submitted {receipt['batch_name']} ({receipt['n_requests']} requests) "
                  f"-> {args.receipt}")
            return 0
        batch_name = resolve_batch_name(args.batch_name, args.submit_receipt)
        transport = GeminiTransport(load_api_key(args.env_file))
        if args.command == "batch-status":
            receipt = retrieve_batch(batch_name, transport)
            write_json_exclusive(args.receipt, receipt)
            print(f"batch {batch_name}: {receipt['state']} -> {args.receipt}")
            return 0
        if args.command == "batch-wait":
            receipt = wait_batch(batch_name, transport, poll_seconds=args.poll_seconds,
                                 timeout_seconds=args.timeout_seconds)
            write_json_exclusive(args.receipt, receipt)
            return 0 if not receipt.get("error") else 2
        receipt = download_batch(batch_name, args.output_dir, transport)
        print(f"downloaded batch {batch_name} -> {args.output_dir}")
        return 0
    except (AdapterError, ProviderHTTPError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
