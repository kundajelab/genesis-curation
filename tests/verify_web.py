"""Offline metadata review, native protocol, session and durable job acceptance."""

from __future__ import annotations

import asyncio
import copy
import tempfile
import threading
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

from verify_ai import FakeHandler, configuration, inference, server
from verify_curation import rejects

from genesis_curation.contracts.records import canonical, loads
from genesis_curation.llm import wire
from genesis_curation.registry import store
from genesis_curation.web import catalog, runtime, skills, worker
from genesis_curation.web.api import create_app


def native_protocols() -> None:
    payload = {
        "model": "test",
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": "Evidence only"},
            {"role": "user", "content": "Question"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "answer",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            },
        },
    }
    for protocol in ("openai-responses", "anthropic-messages"):
        suffix, headers, body = wire.request({"protocol": protocol}, payload, "test-secret")
        raw: dict[str, Any]
        if protocol == "openai-responses":
            assert suffix == "/responses" and body["store"] is False
            assert body["text"]["format"]["strict"] is True
            raw = {
                "model": "test",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"answer":"ok"}'}],
                    }
                ],
            }
        else:
            assert suffix == "/messages" and "Authorization" not in headers
            assert headers["x-api-key"] == "test-secret"
            assert body["output_config"]["format"]["type"] == "json_schema"
            raw = {
                "model": "test",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": '{"answer":"ok"}'}],
            }
        raw["usage"] = {"input_tokens": 10, "output_tokens": 5}
        normalized = loads(wire.response(protocol, canonical(raw)))
        assert normalized["choices"][0]["message"]["content"] == '{"answer":"ok"}'
        assert normalized["usage"]["total_tokens"] == 15
        raw.update(status="incomplete", stop_reason="max_tokens")
        assert (
            loads(wire.response(protocol, canonical(raw)))["choices"][0]["finish_reason"] != "stop"
        )
        rejects(
            lambda protocol=protocol: wire.request(
                {"protocol": protocol}, {**payload, "stream": True}, ""
            )
        )
    refusal = wire.response(
        "openai-responses",
        canonical(
            {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}],
            }
        ),
    )
    assert loads(refusal)["choices"][0]["message"]["refusal"] == "No"
    rejects(lambda: wire.response("openai-responses", "not json"))
    print("PASS native OpenAI/Claude wire formats, truncation, refusal and invalid JSON")


async def http_checks(root: Path, config: Path) -> None:
    # Drive ASGI directly, without adding a new test framework or HTTP client dependency.
    app = create_app(root, config, "https://curation.example:8443")

    async def request(
        path: str, body: object = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        raw = canonical(body).encode() if body is not None else b""
        sent = False
        headers = {"host": "curation.example:8443", **(headers or {})}
        if body is not None:
            headers.setdefault("content-type", "application/json")
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST" if body is not None else "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "server": ("curation.example", 8443),
            "client": ("127.0.0.1", 12345),
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        }

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": raw, "more_body": False}
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            messages.append(dict(message))

        await asyncio.wait_for(app(scope, receive, send), timeout=10)
        start = next(m for m in messages if m["type"] == "http.response.start")
        content = b"".join(m.get("body", b"") for m in messages)
        return (
            start["status"],
            {k.decode(): v.decode() for k, v in start["headers"]},
            loads(content.decode()),
        )

    assert (await request("/api/subjects"))[0] == 401
    credentials = {"username": "curator", "password": "a long test password"}
    assert (await request("/api/login", credentials))[0] == 403
    status, headers, body = await request(
        "/api/login", credentials, {"origin": "https://curation.example:8443"}
    )
    assert status == 200 and "Secure" in headers["set-cookie"]
    assert "HttpOnly" in headers["set-cookie"] and "SameSite=strict" in headers["set-cookie"]
    auth = {
        "origin": "https://curation.example:8443",
        "cookie": headers["set-cookie"].split(";")[0],
    }
    assert (await request("/api/imports/preview", {}, auth))[0] == 403
    auth["x-csrf-token"] = body["csrf"]
    status, headers, result = await request(
        "/api/imports/preview",
        {"text": '[{"id":"browser","organism":"Arabidopsis thaliana"}]', "filename": "x.json"},
        auth,
    )
    assert status == 200 and result["rows"][0]["id"] == "browser"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert (await request("/api/imports/preview", {"text": "x" * (3 * 1024**2)}, auth))[0] == 413
    assert (await request("/api/logout", {}, auth))[0] == 200
    assert (await request("/api/session", headers=auth))[0] == 401
    print("PASS HTTP authentication, secure cookies, origin/CSRF, upload budget and logout")


def native_http(root: Path) -> None:
    with server() as endpoint:
        for protocol in ("openai-responses", "anthropic-messages"):
            config = {
                **configuration("live"),
                "endpoint": endpoint,
                "protocol": protocol,
                "max_retries": 0,
                "format_repair": False,
            }
            raw = {
                "model": "test-model",
                "status": "completed",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 4},
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}
                ],
                "content": [{"type": "text", "text": '{"ok":true}'}],
            }
            FakeHandler.responses = [(200, canonical(raw), 0)]
            result = inference(root, config)
            assert result["data"]["response"] == {"ok": True}
            assert result["data"]["status"] == "SUCCEEDED"
            raw.update(status="incomplete", stop_reason="max_tokens")
            FakeHandler.responses = [(200, canonical(raw), 0)]
            rejects(lambda config=config: inference(root, config))
    print("PASS native OpenAI/Claude HTTP invocation, audit persistence and truncation rejection")


def main() -> None:
    native_protocols()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        runtime.initialize(root, "curator", "a long test password")
        registry = root / "catalog"
        native_http(registry)
        text = canonical([{"id": "sample1", "organism": "Arabidopsis thaliana", "tissue": "leaf"}])
        ids = catalog.import_rows(registry, text, "samples.json", "test", "id", "public", "curator")
        assert (
            catalog.import_rows(registry, text, "samples.json", "test", "id", "public", "curator")
            == ids
        )
        rejects(lambda: catalog.preview('[{"id":1,"id":2}]', "x.json"))
        rejects(lambda: catalog.preview("id,id\na,b", "x.csv"))
        rejects(lambda: catalog.preview('[{"id":NaN}]', "x.json"))
        subject = ids[0]
        state = catalog.get(registry, subject)
        indexed = catalog.evidence_index(state["sources"])
        assert any(
            e["locator"] == "/organism" and e["text"] == "Arabidopsis thaliana" for e in indexed
        )
        old = state["version"]
        assert catalog.export(registry)["records"][0]["metadata"] == {}
        unknown = next(f for f in state["fields"] if f["status"] == "unknown")
        rejects(
            lambda: catalog.decide(
                registry, subject, old, unknown["field"], "approve", "guess", "curator"
            )
        )
        for field in ("organism", "tissue"):
            state = catalog.decide(
                registry, subject, state["version"], field, "approve", "Source checked", "curator"
            )
        rejects(
            lambda: catalog.decide(
                registry, subject, old, "organism", "approve", "stale", "curator"
            )
        )
        export = catalog.export(registry)["records"][0]
        assert (
            set(export["metadata"]) == {"organism", "tissue"}
            and export["review_status"] == "PARTIAL"
        )
        state = catalog.promote(registry, subject, state["version"], "tissue", "curator")
        assert len(catalog.examples(registry, state)) == 1
        field = next(f for f in state["fields"] if f["field"] == "tissue")
        forged = copy.deepcopy(field)
        forged["evidence"][0]["quote"] = "invented evidence"
        rejects(
            lambda: catalog.decide(
                registry,
                subject,
                state["version"],
                "tissue",
                "approve",
                "bad citation",
                "curator",
                forged,
            )
        )
        config = configuration()
        config_path = root / "providers.json"
        config_path.write_text(canonical({"schema_version": 1, "providers": [config]}))
        cancelled = threading.Event()
        cancelled.set()
        rejects(
            lambda: catalog.run_proposal(
                registry,
                subject,
                state["version"],
                config,
                skills.select(root, "none"),
                "curator",
                cancellation=cancelled,
            )
        )
        assert catalog.get(registry, subject)["version"] == state["version"]
        state = catalog.attach(
            registry,
            subject,
            state["version"],
            "tissue: root",
            "curator:notes",
            "internal",
            "curator",
        )
        assert state["classification"] == "internal" and not state["reviews"]
        assert not catalog.export(registry)["records"][0]["metadata"]
        assert not catalog.examples(registry, state)
        print(
            "PASS idempotent import, strict inputs, evidence validation, stale review, "
            "scoped examples and approved-only export"
        )
        key = runtime.enqueue(
            root,
            "propose",
            "curator",
            {
                "subject": subject,
                "version": state["version"],
                "provider": "test",
                "skill": "plant-metadata",
            },
        )
        assert worker.once(root, config_path)
        assert runtime.job(root, key)["status"] == "SUCCEEDED"
        assert not catalog.get(registry, subject)["reviews"]
        current = catalog.get(registry, subject)
        sparse = catalog.run_proposal(
            registry,
            subject,
            current["version"],
            {**config, "mock_response": {"fields": []}},
            skills.select(root, "none"),
            "curator",
        )
        assert sparse["fields"] == current["fields"] and not sparse["reviews"]
        print("PASS sparse proposals preserve the complete deterministic review table")
        key = runtime.enqueue(root, "probe", "curator", {"provider": "test"})
        entered = threading.Event()

        def blocked_execute(
            _root: Path,
            _config: Path,
            _task: dict[str, Any],
            cancellation: threading.Event | None = None,
        ) -> dict[str, Any]:
            assert cancellation is not None
            entered.set()
            assert cancellation.wait(5), "Worker did not observe cancellation"
            raise ValueError("Request cancelled")

        with patch.object(worker, "execute", blocked_execute):
            thread = threading.Thread(target=worker.once, args=(root, config_path))
            thread.start()
            assert entered.wait(5)
            runtime.cancel(root, key)
            thread.join(5)
            assert not thread.is_alive()
        assert runtime.job(root, key)["status"] == "CANCELLED"
        print("PASS cancellation of an already-running durable job")
        key = runtime.enqueue(root, "probe", "curator", {"provider": "test"})
        runtime.cancel(root, key)
        assert not worker.once(root, config_path)
        assert runtime.job(root, key)["status"] == "CANCELLED"
        key = runtime.enqueue(root, "probe", "curator", {"provider": "test"})
        assert runtime.claim(root)
        runtime.recover(root)
        assert runtime.job(root, key)["status"] == "INTERRUPTED"
        print("PASS durable proposal, queued cancellation and restart interruption")
        store.backup(registry, root / "backup.sqlite")
        store.restore(root / "backup.sqlite", root / "restored")
        assert catalog.get(root / "restored", subject) == catalog.get(registry, subject)
        assert catalog.history(root / "restored", subject) == catalog.history(registry, subject)
        print("PASS metadata state and immutable history backup/restore")
        asyncio.run(http_checks(root, config_path))
    print("PASS web curation acceptance")


if __name__ == "__main__":
    main()
