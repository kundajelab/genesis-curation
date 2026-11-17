"""CLI and GUI use identical study decisions, QC and export contracts."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any

from verify_ai import configuration
from verify_study_web import reject

from genesis_curation.application import Application
from genesis_curation.contracts.records import load
from genesis_curation.curation.cli import wait
from genesis_curation.installation import initialize, private_json
from genesis_curation.llm import providers, spending, wire
from genesis_curation.llm.security import check
from genesis_curation.web import runtime, studies, study_api


def main() -> None:
    original = {
        "type": "object",
        "properties": {
            "maxItems": {
                "type": "array",
                "items": {"type": "integer"},
                "maxItems": 1,
                "uniqueItems": True,
            }
        },
        "required": ["maxItems"],
        "additionalProperties": False,
    }
    converted = wire.grammar(original)
    assert isinstance(converted, dict)
    check({"maxItems": [1, 2]}, converted)
    reject(lambda: check({"maxItems": [1, 2]}, original), "Schema")
    assert (
        providers.failure_kind({"data": {"attempts": [{"status": "400", "response": "{}"}]}})
        == "access"
    )
    with tempfile.TemporaryDirectory(prefix="curation-cli-") as tmp:
        root = Path(tmp)
        initialize(root, "tester", "synthetic-cli-password")
        state = root / "state"
        config = root / "config/providers.json"
        mock = configuration()
        mock["context_window_tokens"] = 32768
        private_json(config, {"schema_version": 1, "default_provider": "test", "providers": [mock]})
        app = Application(state, config)

        def call(method: str, path: str, body: object = None) -> dict[str, Any]:
            raw, media = app.request(method, path, body)
            assert "json" in media
            result = json.loads(raw)
            return result if isinstance(result, dict) else {"items": result}

        assert call("GET", "/providers")["items"][0]["default"]
        assert not call("GET", "/workspace")["counts"]
        reject(lambda: app.request("GET", "https://example.org/api/workspace"))
        reject(lambda: app.request("POST", "/login", {}))
        reject(lambda: app.request("POST", "/imports/preview", []))
        result = call(
            "POST",
            "/imports/commit",
            {
                "filename": "records.json",
                "text": json.dumps(
                    [
                        {
                            "id": "one",
                            "organism": "Arabidopsis thaliana",
                            "assay": "RNA-seq",
                            "tissue": "leaf",
                        },
                        {"id": "two", "organism": "Arabidopsis thaliana", "assay": "RNA-seq"},
                    ]
                ),
                "namespace": "synthetic",
                "id_column": "id",
                "classification": "public",
            },
        )
        assert len(result["subjects"]) == 2
        study = call("GET", "/studies")["items"][0]
        before = call("GET", "/studies/" + study["id"])
        reject(
            lambda: call(
                "POST", "/studies/" + study["id"] + "/approve", {"version": before["version"]}
            )
        )
        job = call("POST", "/studies/" + study["id"] + "/prepare", {"version": before["version"]})
        assert wait(state, config, job["id"])["status"] == "SUCCEEDED"
        current = call("GET", "/studies/" + study["id"])
        for task in current["tasks"]:
            body = {
                "study": study["id"],
                "version": task["version"],
                "action": "unknown",
                "reason": "Synthetic missing evidence",
            }
            call("POST", "/review-tasks/" + task["id"] + "/draft", body)
            assert call("GET", "/review-tasks/" + task["id"] + "/draft")["reason"] == body["reason"]
            call("POST", "/review-tasks/" + task["id"] + "/decision", body)
            reject(
                lambda task=task, body=body: call(
                    "POST", "/review-tasks/" + task["id"] + "/decision", body
                ),
                "Stale",
            )
        current = call("GET", "/studies/" + study["id"])
        release = call(
            "POST", "/studies/" + study["id"] + "/approve", {"version": current["version"]}
        )
        assert call("GET", "/studies/" + study["id"] + "/export") == studies.export(
            state / "catalog", study["id"]
        )
        assert len(release["records"]) == 2
        raw, media = app.request("GET", "/studies/" + study["id"] + "/export?format=csv")
        assert "csv" in media and b"one" in raw
        assert call("GET", "/studies/" + study["id"] + "/history")
        assert call("GET", "/skills") and call("GET", "/qc")["metrics"]
        with runtime.connection(state) as db:
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        api = copy.deepcopy(mock)
        api.update(
            id="hosted",
            mode="live",
            external=True,
            endpoint="https://api.anthropic.com/v1",
            allowed_classifications=["public"],
        )
        private_json(
            config, {"schema_version": 1, "default_provider": "hosted", "providers": [mock, api]}
        )
        assert providers.default_id(config) == "hosted"
        selected = study_api.provider(state, config)
        assert selected is not None and selected["id"] == "hosted"
        assert call("GET", "/providers")["items"][1]["default"]
        private_json(
            state / "spending.json", {"limit_usd": 0.05, "reserved_usd": 0, "reservations": []}
        )
        payload = {"messages": [{"role": "user", "content": "test"}], "max_tokens": 100}
        reject(lambda: spending.reserve(state / "catalog", api, payload), "prices")
        api["pricing"] = {"input_per_million": 4, "output_per_million": 20}
        spending.reserve(state / "catalog", api, payload)
        spending.reserve(state / "catalog", api, payload)
        reject(lambda: spending.reserve(state / "catalog", api, payload), "limit")
        assert len(load(state / "spending.json")["reservations"]) == 2
    print(
        "PASS CLI: shared authenticated operations, review/sign-off/export parity, "
        "stale decisions, session cleanup, default provider and spending cap"
    )


if __name__ == "__main__":
    main()
