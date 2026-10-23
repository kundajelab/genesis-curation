"""Native provider protocols normalized to the existing bounded inference contract."""

from __future__ import annotations

from typing import Any

from ..contracts.records import canonical, loads


def request(
    config: dict[str, Any], payload: dict[str, Any], secret: str
) -> tuple[str, dict[str, str], dict[str, Any]]:
    protocol = config.get("protocol", "openai-chat")
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    if protocol == "openai-chat":
        if config.get("chat_template_kwargs"):
            payload = {**payload, "chat_template_kwargs": config["chat_template_kwargs"]}
        return "/chat/completions", headers, payload
    if payload.get("stream") or payload.get("tools"):
        raise ValueError(
            "Native provider tool/stream probes are not supported; disable those capabilities"
        )
    messages = payload["messages"]
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    content = [m for m in messages if m["role"] != "system"]
    format_spec = payload.get("response_format", {}).get("json_schema")
    if protocol == "openai-responses":
        body = {
            "model": payload["model"],
            "instructions": system,
            "input": content,
            "max_output_tokens": payload["max_tokens"],
            "store": False,
        }
        if format_spec:
            body["text"] = {"format": {"type": "json_schema", **format_spec}}
        return "/responses", headers, body
    if protocol == "anthropic-messages":
        headers.pop("Authorization", None)
        headers.update({"x-api-key": secret, "anthropic-version": "2023-06-01"})
        body = {
            "model": payload["model"],
            "system": system,
            "messages": content,
            "max_tokens": payload["max_tokens"],
        }
        if format_spec:
            # Provider grammar supports a subset; original constraints are checked locally.
            body["output_config"] = {
                "format": {"type": "json_schema", "schema": grammar(format_spec["schema"])}
            }
        return "/messages", headers, body
    raise ValueError("Unknown provider protocol")


def grammar(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: {name: grammar(spec) for name, spec in v.items()}
            if k in {"properties", "$defs", "definitions"} and isinstance(v, dict)
            else grammar(v)
            for k, v in value.items()
            if k
            not in {
                "minimum",
                "maximum",
                "minLength",
                "maxLength",
                "pattern",
                "format",
            }
        }
    if isinstance(value, list):
        return [grammar(v) for v in value]
    return value


def response(protocol: str, raw: str) -> str:
    if protocol == "openai-chat":
        return raw
    value = loads(raw)
    usage = value.get("usage") or {}
    refusal = None
    if protocol == "openai-responses":
        blocks = [
            b
            for item in value.get("output", [])
            if item.get("type") == "message"
            for b in item.get("content", [])
        ]
        texts = [b.get("text", "") for b in blocks if b.get("type") == "output_text"]
        refusal = next(
            (b.get("refusal", "Refused") for b in blocks if b.get("type") == "refusal"), None
        )
        stopped = value.get("status") == "completed"
    else:
        texts = [b.get("text", "") for b in value.get("content", []) if b.get("type") == "text"]
        stopped = value.get("stop_reason") == "end_turn"
        if value.get("stop_reason") == "refusal":
            refusal = "Refused"
    incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
    total = incoming + outgoing if type(incoming) is int and type(outgoing) is int else None
    return canonical(
        {
            "model": value.get("model"),
            "choices": [
                {
                    "finish_reason": "stop" if stopped else "incomplete",
                    "message": {"content": "".join(texts), "refusal": refusal},
                }
            ],
            "usage": {
                "prompt_tokens": incoming,
                "completion_tokens": outgoing,
                "total_tokens": total,
            },
        }
    )
