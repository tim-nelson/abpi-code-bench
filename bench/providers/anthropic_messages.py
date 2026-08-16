#!/usr/bin/env python3
"""Strict Anthropic Messages/Message-Batches benchmark adapter.

The benchmark planners own prompts and stable call identities.  This module
validates their provider-neutral JSONL, submits an explicitly authorised
single-message validation or Message Batch, preserves raw provider receipts,
and emits normalized JSONL for ``bench/run.py --import-results``.

The two reproducible model conditions supported here are deliberately narrow:

* ``claude-sonnet-5``: adaptive thinking, medium effort;
* ``claude-haiku-4-5-20251001``: no thinking/effort fields, because Haiku 4.5
  supports manual extended thinking but not adaptive thinking or effort.

No third-party SDK is required.  Live commands require ``--execute`` and read
only ``ANTHROPIC_API_KEY`` from the environment or a literal dotenv file.
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
import urllib.request
from typing import Any


ADAPTER_CONTRACT = "pmcpa.anthropic-messages.v1"
NORMALIZED_CONTRACT = "pmcpa.anthropic-normalized.v1"
CANONICAL_CONTRACT = "pmcpa.zero-provider.v2"
API_BASE = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
MESSAGE_ENDPOINT = "/v1/messages"
BATCH_ENDPOINT = "/v1/messages/batches"
MAX_OUTPUT_TOKENS = 4096
P3_AGGREGATION = "linear_probability_pool"
P4_AGGREGATION = "cost_sweep"
P4_DECISIONS = ["answer", "refer"]

SONNET_MODEL = "claude-sonnet-5"
HAIKU_MODEL = "claude-haiku-4-5-20251001"
OPUS_MODEL = "claude-opus-5"
MODEL_CONFIGS = {
    SONNET_MODEL: {"thinking": {"type": "adaptive"}, "effort": "medium"},
    HAIKU_MODEL: {"thinking": None, "effort": None},
    # Opus 5 mirrors the Sonnet 5 condition (adaptive thinking, medium
    # effort) so the cross-tier comparison holds configuration fixed.
    OPUS_MODEL: {"thinking": {"type": "adaptive"}, "effort": "medium"},
}

EXPECTED_ANSWERS = {
    "T1": ["breach", "no_breach"],
    "T2": ["breach", "no_breach"],
    "T3": ["upheld", "overturned"],
}
KNOWN_BATCH_STATUSES = frozenset(("in_progress", "canceling", "ended"))
TERMINAL_BATCH_STATUSES = frozenset(("ended",))
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
        raise AdapterError(f"{call_id}: unsupported exact Anthropic model {model!r}")
    protocol = row.get("protocol")
    if protocol not in ("P1", "P2", "P3", "P4"):
        raise AdapterError(f"{call_id}: unsupported protocol {protocol!r}")
    if protocol == "P3" and row.get("aggregation") != P3_AGGREGATION:
        raise AdapterError(f"{call_id}: native P3 requires linear_probability_pool")
    if protocol == "P4" and row.get("aggregation") != P4_AGGREGATION:
        raise AdapterError(f"{call_id}: native P4 requires cost_sweep")
    task = row.get("task")
    if task not in EXPECTED_ANSWERS:
        raise AdapterError(f"{call_id}: unsupported task {task!r}")
    _require_string(row.get("config_hash"), f"{call_id}.config_hash")
    request = row.get("request")
    if not isinstance(request, dict):
        raise AdapterError(f"{call_id}.request must be an object")
    allowed = {"model", "max_tokens", "system", "messages", "output_config", "thinking"}
    if set(request) - allowed:
        raise AdapterError(f"{call_id}.request has unsupported fields {sorted(set(request)-allowed)}")
    if request.get("model") != model or request.get("max_tokens") != MAX_OUTPUT_TOKENS:
        raise AdapterError(f"{call_id}: model/max_tokens differs from fixed condition")
    system = _require_string(request.get("system"), f"{call_id}.request.system")
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise AdapterError(f"{call_id}.request.messages must be non-empty")
    for index, message in enumerate(messages):
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message.get("role") not in ("user", "assistant")
                or not isinstance(message.get("content"), str)):
            raise AdapterError(f"{call_id}.request.messages[{index}] is not canonical")
    expected_cfg = MODEL_CONFIGS[model]
    if expected_cfg["thinking"] is None:
        if "thinking" in request:
            raise AdapterError(f"{call_id}: thinking must be omitted for Haiku 4.5")
    elif request.get("thinking") != expected_cfg["thinking"]:
        raise AdapterError(f"{call_id}: thinking configuration mismatch")
    output_config = request.get("output_config")
    expected_keys = {"format", "effort"} if expected_cfg["effort"] else {"format"}
    if not isinstance(output_config, dict) or set(output_config) != expected_keys:
        raise AdapterError(f"{call_id}: output_config fields mismatch")
    if expected_cfg["effort"] and output_config.get("effort") != expected_cfg["effort"]:
        raise AdapterError(f"{call_id}: effort must be medium")
    fmt = output_config.get("format")
    if not isinstance(fmt, dict) or set(fmt) != {"type", "schema"} \
            or fmt.get("type") != "json_schema":
        raise AdapterError(f"{call_id}: output format must be canonical json_schema")
    schema = fmt.get("schema")
    _validate_schema(schema, f"{call_id}.schema")
    props = schema["properties"]
    if protocol == "P4":
        # the stated variant elicits P1's probability ahead of the decision
        wanted = ({"probability", "decision", "answer"}
                  if "probability" in props else {"decision", "answer"})
    elif protocol in ("P1", "P3"):
        wanted = {"answer", "probability"}
    else:
        wanted = {"answer"}
    if set(props) != wanted or set(schema["required"]) != wanted:
        raise AdapterError(f"{call_id}: {protocol} output fields mismatch")
    if protocol == "P4":
        if props["decision"].get("enum") != P4_DECISIONS:
            raise AdapterError(f"{call_id}: P4 decision enum mismatch")
        if "probability" in props and props["probability"].get("type") != "number":
            raise AdapterError(f"{call_id}: P4 stated probability must be numeric")
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


def to_message_params(row: dict[str, Any]) -> dict[str, Any]:
    validate_canonical_row(row)
    # Deep-copy mechanically via canonical JSON so no planner record is mutated.
    return json.loads(canonical_json(row["request"]).decode("utf-8"))


def to_batch_request(row: dict[str, Any]) -> dict[str, Any]:
    return {"custom_id": row["call_id"], "params": to_message_params(row)}


def prepare_batch(canonical_path: pathlib.Path | str,
                  output_path: pathlib.Path | str) -> int:
    rows = load_canonical_rows(canonical_path)
    body = {"requests": [to_batch_request(row) for row in rows]}
    write_json_exclusive(output_path, body)
    return len(rows)


def read_json_object(path: pathlib.Path | str) -> dict[str, Any]:
    path = pathlib.Path(path)
    value = strict_json_loads(path.read_text(encoding="utf-8"), str(path))
    if not isinstance(value, dict):
        raise AdapterError(f"{path}: expected one JSON object")
    return value


def validate_batch_binding(canonical_path: pathlib.Path | str,
                           batch_path: pathlib.Path | str,
                           expect_requests: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = load_canonical_rows(canonical_path)
    body = read_json_object(batch_path)
    if expect_requests < 1 or len(rows) != expect_requests:
        raise AdapterError(f"expected {expect_requests} canonical requests, found {len(rows)}")
    expected = {"requests": [to_batch_request(row) for row in rows]}
    if canonical_json(body) != canonical_json(expected):
        raise AdapterError("provider batch JSON differs from mechanical canonical translation")
    ids = [request.get("custom_id") for request in body.get("requests", [])]
    if len(ids) != expect_requests or len(ids) != len(set(ids)):
        raise AdapterError("provider batch custom IDs are missing or duplicated")
    return rows, body


def load_api_key(env_file: pathlib.Path | str | None = DEFAULT_ENV_FILE) -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    if env_file is None:
        raise AdapterError("ANTHROPIC_API_KEY is not set")
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
            match = re.fullmatch(r"ANTHROPIC_API_KEY\s*=\s*(.*)", candidate)
            if not match:
                continue
            value = match.group(1).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            elif value.startswith(("'", '"')) or value.endswith(("'", '"')):
                raise AdapterError(f"{path}:{lineno}: unmatched quote")
            if not value:
                raise AdapterError(f"{path}:{lineno}: empty ANTHROPIC_API_KEY")
            values.append(value)
    if not values:
        raise AdapterError(f"{path}: ANTHROPIC_API_KEY not found")
    if len(set(values)) != 1:
        raise AdapterError(f"{path}: conflicting ANTHROPIC_API_KEY assignments")
    return values[0]


def _safe_headers(headers: Any) -> dict[str, str]:
    out = {}
    for name in ("request-id", "anthropic-ratelimit-requests-remaining",
                 "anthropic-ratelimit-tokens-remaining", "cf-ray"):
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


class AnthropicTransport:
    def __init__(self, api_key: str, timeout: float = 600.0):
        if not api_key:
            raise AdapterError("ANTHROPIC_API_KEY is empty")
        self._api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, data: bytes | None,
                 content_type: str | None) -> tuple[bytes, dict[str, Any]]:
        if not path.startswith("/v1/"):
            raise AdapterError("Anthropic provider path must begin /v1/")
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "user-agent": "pmcpa-bench-anthropic-adapter/1",
        }
        if content_type:
            headers["content-type"] = content_type
        request = urllib.request.Request(API_BASE + path, data=data, headers=headers, method=method)
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
                payload = {"error": {"message": "non-JSON provider error"}}
            safe = _safe_headers(exc.headers)
            raise ProviderHTTPError(
                exc.code, safe.get("request-id"), payload,
                _provider_message(payload, exc.code),
                ambiguous=(exc.code == 408 or exc.code >= 500)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = " ".join(str(getattr(exc, "reason", exc)).split())[:500]
            raise ProviderHTTPError(None, None, None, f"transport error: {reason}",
                                    ambiguous=True) from exc

    def json(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        raw, meta = self._request(method, path,
                                  canonical_json(body) if body is not None else None,
                                  "application/json" if body is not None else None)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderHTTPError(meta["http_status"],
                                    meta["headers"].get("request-id"), None,
                                    "provider returned non-JSON content", ambiguous=True) from exc
        if not isinstance(payload, dict):
            raise ProviderHTTPError(meta["http_status"],
                                    meta["headers"].get("request-id"), payload,
                                    "provider returned non-object JSON", ambiguous=True)
        return {"payload": payload, **meta}

    def bytes(self, path: str) -> dict[str, Any]:
        raw, meta = self._request("GET", path, None, None)
        return {"content": raw, **meta}


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
        code = exc.payload["error"].get("type")
    return _error("provider_http_error", exc.safe_message, status=exc.status,
                  request_id=exc.request_id, code=code)


def _provider_receipt(canonical: dict[str, Any], payload: Any, meta: dict[str, Any],
                      *, batch_meta: dict[str, Any] | None = None,
                      raw_batch_row: Any = None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    receipt: dict[str, Any] = {
        "provider": "anthropic", "endpoint": MESSAGE_ENDPOINT,
        "http_status": meta.get("http_status"),
        "request_id": meta.get("request_id") or (meta.get("headers") or {}).get("request-id"),
        "provider_response_id": raw.get("id"), "model": raw.get("model"),
        "usage": raw.get("usage"), "canonical_request": canonical["request"],
        "raw": payload,
    }
    if raw_batch_row is not None:
        receipt["raw_batch_row"] = raw_batch_row
    if batch_meta:
        receipt["batch"] = {key: batch_meta.get(key) for key in
                            ("batch_id", "results_url", "request_counts")}
    return receipt


def _extract_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        raise AdapterError("message.content must be an array")
    texts = []
    for block in content:
        if not isinstance(block, dict):
            raise AdapterError("message content block must be an object")
        typ = block.get("type")
        if typ == "text":
            if not isinstance(block.get("text"), str):
                raise AdapterError("text block has no text")
            texts.append(block["text"])
        elif typ not in ("thinking", "redacted_thinking"):
            raise AdapterError(f"unexpected response content block {typ!r}")
    if len(texts) != 1:
        raise AdapterError(f"expected exactly one text block, found {len(texts)}")
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
        "provider": "anthropic", "provider_request_id": receipt.get("request_id"),
        "provider_response_id": payload.get("id"), "usage": payload.get("usage"),
        "response": receipt, "requested_utc": requested_utc,
        "stop_reason": payload.get("stop_reason"),
    }
    try:
        if payload.get("type") != "message" or payload.get("role") != "assistant":
            raise AdapterError("provider payload is not an assistant message")
        if payload.get("stop_reason") != "end_turn":
            raise AdapterError(f"message stop_reason={payload.get('stop_reason')!r}")
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
        if payload.get("model") != canonical["model"]:
            base.update({"parsed": None, "error": None, "retry_safe": False,
                         "quarantine": {
                             "type": "provider_model_identity_mismatch",
                             "message": (f"requested {canonical['model']!r}, provider reported "
                                         f"{payload.get('model')!r}"),
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
        response = transport.json("POST", MESSAGE_ENDPOINT, to_message_params(canonical))
    except ProviderHTTPError as exc:
        base = {
            "schema_version": NORMALIZED_CONTRACT,
            "call_id": canonical["call_id"], "custom_id": canonical["call_id"],
            "provider": "anthropic", "provider_request_id": exc.request_id,
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


def submit_batch(canonical_path: pathlib.Path | str, batch_path: pathlib.Path | str,
                 expect_requests: int, transport: Any) -> dict[str, Any]:
    rows, body = validate_batch_binding(canonical_path, batch_path, expect_requests)
    receipt: dict[str, Any] = {
        "schema_version": ADAPTER_CONTRACT, "provider": "anthropic",
        "operation": "batch_submit", "submitted_utc": utc_now(),
        "canonical_jsonl_sha256": file_sha256(canonical_path),
        "batch_json_sha256": file_sha256(batch_path),
        "n_requests": len(rows), "model": rows[0]["model"],
        "batch_id": None, "batch": None, "error": None, "quarantine": None,
    }
    try:
        response = transport.json("POST", BATCH_ENDPOINT, body)
        batch = response["payload"]
        batch_id = batch.get("id")
        if (not isinstance(batch_id, str) or not batch_id.startswith("msgbatch_")
                or batch.get("type") != "message_batch"
                or batch.get("processing_status") not in KNOWN_BATCH_STATUSES):
            raise AdapterError("invalid Message Batch creation receipt")
        counts = batch.get("request_counts")
        if isinstance(counts, dict) and sum(int(counts.get(k, 0)) for k in
                ("processing", "succeeded", "errored", "canceled", "expired")) != len(rows):
            raise AdapterError("Message Batch creation count mismatch")
        receipt["batch_id"] = batch_id
        receipt["batch"] = response
    except ProviderHTTPError as exc:
        if exc.ambiguous:
            receipt["quarantine"] = {
                "type": "ambiguous_batch_submission",
                "message": exc.safe_message,
                "instruction": "Do not resubmit; reconcile via Message Batch listing first.",
            }
        else:
            receipt["error"] = _http_error(exc)
    except AdapterError as exc:
        receipt["error"] = _error("provider_receipt_validation_error", str(exc))
    return receipt


def resolve_batch_id(batch_id: str | None, submit_receipt: pathlib.Path | str | None) -> str:
    if bool(batch_id) == bool(submit_receipt):
        raise AdapterError("provide exactly one of --batch-id or --submit-receipt")
    if submit_receipt:
        receipt = read_json_object(submit_receipt)
        if receipt.get("error") or receipt.get("quarantine"):
            raise AdapterError(f"{submit_receipt}: batch receipt is not resolved-successful")
        batch_id = receipt.get("batch_id")
    if not isinstance(batch_id, str) or not re.fullmatch(r"msgbatch_[A-Za-z0-9_-]+", batch_id):
        raise AdapterError("invalid Message Batch ID")
    return batch_id


def retrieve_batch(batch_id: str, transport: Any) -> dict[str, Any]:
    response = transport.json("GET", f"{BATCH_ENDPOINT}/{batch_id}")
    batch = response["payload"]
    if batch.get("id") != batch_id or batch.get("type") != "message_batch":
        raise AdapterError("Message Batch status identity mismatch")
    status = batch.get("processing_status")
    if status not in KNOWN_BATCH_STATUSES:
        raise AdapterError(f"unknown Message Batch status {status!r}")
    return {
        "schema_version": ADAPTER_CONTRACT, "provider": "anthropic",
        "operation": "batch_status", "checked_utc": utc_now(),
        "submitted_utc": batch.get("created_at"), "batch_id": batch_id,
        "status": status, "results_url": batch.get("results_url"),
        "request_counts": batch.get("request_counts"),
        "response": response, "error": None,
    }


def wait_batch(batch_id: str, transport: Any, *, poll_seconds: float,
               timeout_seconds: float, sleep_fn=time.sleep) -> dict[str, Any]:
    if not 1 <= poll_seconds <= 60 or timeout_seconds <= 0:
        raise AdapterError("poll interval must be 1..60s and timeout positive")
    started, last = time.monotonic(), None
    while True:
        receipt = retrieve_batch(batch_id, transport)
        status = receipt["status"]
        if status != last:
            counts = receipt.get("request_counts") or {}
            print(f"batch {batch_id}: {status} processing={counts.get('processing', 0)} "
                  f"succeeded={counts.get('succeeded', 0)} errored={counts.get('errored', 0)}")
            last = status
        if status in TERMINAL_BATCH_STATUSES:
            return receipt
        if time.monotonic() - started >= timeout_seconds:
            receipt["error"] = _error("batch_wait_timeout",
                                      f"batch did not end within {timeout_seconds:g}s")
            return receipt
        sleep_fn(poll_seconds)


def download_batch(batch_id: str, output_dir: pathlib.Path | str,
                   transport: Any) -> dict[str, Any]:
    output_dir = pathlib.Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise AdapterError(f"refusing to reuse {output_dir}") from exc
    status = retrieve_batch(batch_id, transport)
    if status["status"] != "ended":
        raise AdapterError(f"Message Batch {batch_id} has not ended")
    result = transport.bytes(f"{BATCH_ENDPOINT}/{batch_id}/results")
    raw_path = output_dir / "results.jsonl"
    with raw_path.open("xb") as fh:
        fh.write(result["content"])
    receipt = {
        **status, "operation": "batch_download", "downloaded_utc": utc_now(),
        "downloaded": {"path": raw_path.name, "sha256": file_sha256(raw_path),
                       "http_status": result.get("http_status"),
                       "headers": result.get("headers")},
    }
    write_json_exclusive(output_dir / "batch.json", receipt)
    return receipt


def _batch_meta(download_dir: pathlib.Path) -> dict[str, Any]:
    receipt = read_json_object(download_dir / "batch.json")
    if receipt.get("provider") != "anthropic" or receipt.get("operation") != "batch_download":
        raise AdapterError("invalid Anthropic batch download receipt")
    return {
        "batch_id": receipt.get("batch_id"), "results_url": receipt.get("results_url"),
        "request_counts": receipt.get("request_counts"),
        "requested_utc": receipt.get("submitted_utc") or receipt.get("checked_utc"),
    }


def _normalize_batch_failure(canonical: dict[str, Any], provider_row: dict[str, Any],
                             batch_meta: dict[str, Any]) -> dict[str, Any]:
    result = provider_row.get("result") if isinstance(provider_row.get("result"), dict) else {}
    kind = result.get("type") or "unknown"
    error_response = result.get("error")
    nested = error_response.get("error") if isinstance(error_response, dict) else None
    message = nested.get("message") if isinstance(nested, dict) else f"batch result {kind}"
    code = nested.get("type") if isinstance(nested, dict) else kind
    receipt = _provider_receipt(canonical, None, {}, batch_meta=batch_meta,
                                raw_batch_row=provider_row)
    return {
        "schema_version": NORMALIZED_CONTRACT,
        "call_id": canonical["call_id"], "custom_id": canonical["call_id"],
        "provider": "anthropic",
        "provider_request_id": (error_response or {}).get("request_id")
            if isinstance(error_response, dict) else None,
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
    for row in provider_rows:
        call_id = _require_string(row.get("custom_id"), "batch result custom_id")
        if call_id not in by_id:
            raise AdapterError(f"batch result has unknown custom_id {call_id}")
        if call_id in results:
            raise AdapterError(f"duplicate batch result {call_id}")
        results[call_id] = row
    normalized = []
    counts = {"expected": len(canonical), "present": len(results),
              "completed": 0, "failed": 0, "quarantined": 0,
              "missing": len(canonical) - len(results)}
    for call in canonical:
        provider_row = results.get(call["call_id"])
        if provider_row is None:
            continue
        result = provider_row.get("result")
        if not isinstance(result, dict) or result.get("type") != "succeeded":
            normalized.append(_normalize_batch_failure(call, provider_row, meta))
            counts["failed"] += 1
            continue
        message = result.get("message")
        if not isinstance(message, dict):
            raise AdapterError(f"{call['call_id']}: succeeded result lacks message")
        row = normalize_payload(call, message, {}, requested_utc=meta.get("requested_utc"),
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
        sub.add_argument("--batch-id")
        sub.add_argument("--submit-receipt")
        if name == "batch-download":
            sub.add_argument("--output-dir", required=True)
        else:
            sub.add_argument("--receipt", required=True)
        if name == "batch-wait":
            sub.add_argument("--poll-seconds", type=float, default=20)
            sub.add_argument("--timeout-seconds", type=float, default=86400)
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
            print(f"prepared {count} Anthropic Message Batch requests -> {args.output}")
            return 0
        if args.command == "normalize-batch":
            counts = normalize_batch(args.input, args.download_dir, args.output)
            print(json.dumps(counts, sort_keys=True))
            return 0
        if args.command == "smoke":
            if not args.execute:
                raise AdapterError("smoke requires --execute; no request was sent")
            rows = load_canonical_rows(args.input, exactly_one=True)
            result = execute_smoke(rows[0], AnthropicTransport(load_api_key(args.env_file)))
            write_jsonl_exclusive(args.output, [result])
            print(f"smoke result -> {args.output}")
            return 0 if not result.get("error") and not result.get("quarantine") else 2
        if args.command == "batch-submit":
            if not args.execute:
                raise AdapterError("batch-submit requires --execute; no batch was sent")
            # Reserve receipt before POST. An ambiguous POST is therefore never
            # silently repeated and is durably quarantined.
            receipt_path = pathlib.Path(args.receipt)
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                receipt_fh = receipt_path.open("x", encoding="utf-8")
            except FileExistsError as exc:
                raise AdapterError(f"refusing to overwrite {receipt_path}") from exc
            with receipt_fh:
                receipt = submit_batch(args.canonical, args.input, args.expect_requests,
                                       AnthropicTransport(load_api_key(args.env_file)))
                json.dump(receipt, receipt_fh, ensure_ascii=False, indent=1, sort_keys=True)
                receipt_fh.write("\n")
                receipt_fh.flush()
                os.fsync(receipt_fh.fileno())
            if receipt.get("error") or receipt.get("quarantine"):
                print(f"batch submission unresolved/failed -> {receipt_path}")
                return 2
            print(f"submitted {receipt['batch_id']} ({receipt['n_requests']} requests) -> {receipt_path}")
            return 0
        batch_id = resolve_batch_id(args.batch_id, args.submit_receipt)
        transport = AnthropicTransport(load_api_key(args.env_file))
        if args.command == "batch-status":
            receipt = retrieve_batch(batch_id, transport)
            write_json_exclusive(args.receipt, receipt)
            print(f"batch {batch_id}: {receipt['status']} -> {args.receipt}")
            return 0
        if args.command == "batch-wait":
            receipt = wait_batch(batch_id, transport, poll_seconds=args.poll_seconds,
                                 timeout_seconds=args.timeout_seconds)
            write_json_exclusive(args.receipt, receipt)
            return 0 if not receipt.get("error") else 2
        receipt = download_batch(batch_id, args.output_dir, transport)
        print(f"downloaded batch {batch_id} -> {args.output_dir}")
        return 0
    except (AdapterError, ProviderHTTPError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
