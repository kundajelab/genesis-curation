"""Whole-table admission, evidence isolation, bounded recovery and durable scheduling."""

from __future__ import annotations

import copy
import tempfile
import threading
from pathlib import Path
from typing import Any, TypedDict, Unpack
from unittest.mock import patch

from genesis_curation.contracts.records import canonical, dump, fingerprint, load
from genesis_curation.llm import budget, providers
from genesis_curation.web import catalog, preparation, runtime, studies, study_api, table, worker


class InvokeOptions(TypedDict):
    task: str
    classification: str
    source_hashes: list[str]
    specification: dict[str, Any]
    mock: dict[str, Any]
    cancellation: threading.Event | None
    instruction: str


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-table-") as temporary:
        root = Path(temporary)
        runtime.initialize(root, "curator", "synthetic-table-password")
        registry = root / "catalog"
        config = providers.select(
            Path(__file__).parents[1] / "examples/curation/providers.mock.json", "mock"
        )
        config.update(
            context_window_tokens=100000,
            max_input_tokens=90000,
            max_tokens=8192,
            token_counter="conservative",
        )
        path = root / "providers.json"
        dump(path, {"schema_version": 1, "providers": [config]})
        ids = catalog.import_rows(
            registry,
            canonical(
                [
                    {
                        "id": "one",
                        "organism": "Arabidopsis thaliana",
                        "description": "Leaf tissue",
                        "assay": "RNA-seq",
                    },
                    {
                        "id": "two",
                        "organism": "Arabidopsis thaliana",
                        "description": "Root tissue",
                        "assay": "RNA-seq",
                    },
                    {
                        "id": "three",
                        "organism": "Arabidopsis thaliana",
                        "description": "Leaf tissue",
                        "assay": "RNA-seq",
                    },
                ]
            ),
            "table.json",
            "table",
            "id",
            "public",
            "curator",
        )
        key = studies.link(registry, ids, "Table", "curator")
        states = [catalog.get(registry, sid) for sid in ids]
        compiled, actual, limits = table.plan(root, states, config, None, "")
        assert len(compiled["originals"]) == 3 and limits["fits"]
        # Keep actionable questions explicit per row: shared/indirect questions
        # caused supported fields to be overlooked in the live regression replay.
        assert all("tissue" in row["fields"] for row in compiled["data"]["table"])
        assert all("tissue" in row["needs_review"] for row in compiled["data"]["table"])
        leaf = next(
            ref for ref, text in compiled["data"]["evidence"].items() if text == "Leaf tissue"
        )
        assert set(compiled["bindings"][leaf]) == {"r1", "r3"}
        change: dict[str, Any] = {
            "rows": ["r1", "r3"],
            "field": "tissue",
            "status": "known",
            "value": "leaf",
            "evidence": [leaf],
            "rationale": "Explicit tissue",
            "confidence": 0.9,
        }
        response = {"reviewed_rows": ["r1", "r2", "r3"], "changes": [change]}
        good, bad = table.validate_rows(compiled, response)
        assert not bad and len(good) == 3
        crossed: dict[str, Any] = copy.deepcopy(response)
        crossed["changes"][0]["rows"].append("r2")
        good, bad = table.validate_rows(compiled, crossed)
        assert set(good) == {"r1", "r3"} and set(bad) == {"r2"}
        missing = {**response, "reviewed_rows": ["r1", "r3"]}
        assert "r2" in table.validate_rows(compiled, missing)[1]
        duplicate = {**response, "changes": [change, change]}
        assert set(table.validate_rows(compiled, duplicate)[1]) == {"r1", "r3"}
        small = {**config, "max_tokens": 400}
        assert len(table.plan(root, states, small, None, "")[0]["originals"]) < 3
        incoming = limits["input_tokens"]
        capped = {**config, "max_input_tokens": incoming - 1}
        assert len(table.plan(root, states, capped, None, "")[0]["originals"]) < 3
        unicode_payload = table.payload(actual, {"text": "根🌿"})
        count, method = budget.count(actual, unicode_payload)
        assert count >= len(canonical(unicode_payload).encode()) and method == "conservative-utf8"
        print("PASS whole-table planning, input/output splitting, Unicode and shared evidence")

        current = studies.get(registry, key)
        job = study_api.queue(root, path, key, "curator", {"version": current["version"]})
        assert worker.once(root, path)
        assert runtime.job(root, job["id"])["status"] == "QUEUED"
        assert len(studies.get(registry, key)["preparation"]["completed"]) == 3
        interactive = runtime.enqueue(root, "probe", "curator", {"provider": "mock"})
        assert worker.once(root, path)
        assert runtime.job(root, interactive)["status"] == "SUCCEEDED"
        assert worker.once(root, path)
        assert runtime.job(root, job["id"])["status"] == "SUCCEEDED"
        assert studies.progress(registry, key)["status"] == "COMPLETED"
        assert len(canonical(studies.progress(registry, key))) < 2000
        print("PASS durable batch checkpoints, reviewer priority and lightweight progress")

        # Reuse requires the same provider, workflow, vocabulary and skill revision.
        reuse_task = {"actor": "curator", "payload": {"study": key}}
        with patch.object(providers, "invoke", wraps=providers.invoke) as invoked:
            table.run(root, config, reuse_task)
            assert invoked.call_count == 0
            changed_config = {**config, "max_tokens": config["max_tokens"] - 1}
            table.run(root, changed_config, reuse_task)
            assert invoked.call_count == 1
        assert all(
            r["preparation_provenance"]["provider_hash"] == fingerprint(changed_config)
            for r in studies.get(registry, key)["records"]
        )
        print("PASS cached completion invalidates when provider settings change")

        # One initial table call plus one combined follow-up, not one call per field.
        second = catalog.import_rows(
            registry,
            canonical(
                [
                    {"id": "unknown-a", "organism": "Arabidopsis thaliana", "assay": "OTHER"},
                    {"id": "unknown-b", "organism": "Arabidopsis thaliana", "assay": "OTHER"},
                ]
            ),
            "unknown.json",
            "unknown",
            "id",
            "public",
            "curator",
        )
        key2 = studies.link(registry, second, "Unknown", "curator")
        task: dict[str, Any] = {
            "actor": "curator",
            "payload": {"study": key2, "provider": "mock", "provider_hash": fingerprint(config)},
        }
        with patch.object(providers, "invoke", wraps=providers.invoke) as invoked:
            result = preparation.run(root, path, task)
        assert result["prepared"] == 2 and result["failed"] == 0
        assert invoked.call_count == 2, invoked.call_count
        assert studies.get(registry, key2)["preparation"]["table_run"]["phase"] == "followup"
        print("PASS exactly one combined follow-up for unresolved evidence")

        # Valid rows survive a bad citation; only the affected row is retried.
        partial = studies.link(registry, ids, "Partial", "curator", group="partial")
        real_invoke = providers.invoke
        seen: list[list[str]] = []

        def mixed(
            directory: Path,
            provider_config: dict[str, Any],
            *,
            data: dict[str, Any],
            **options: Unpack[InvokeOptions],
        ) -> dict[str, Any]:
            kwargs: dict[str, Any] = dict(options)
            kwargs["data"] = data
            rows = kwargs["data"]["table"]
            seen.append([r["sample"] for r in rows])
            edits = [
                {
                    "rows": [r["id"]],
                    "field": "tissue",
                    "value": "leaf",
                    "status": "known",
                    "evidence": ["missing"],
                    "confidence": 0.9,
                    "rationale": "Invalid fixture",
                }
                for r in rows
                if r["sample"] == "two"
            ]
            kwargs["mock"] = {"reviewed_rows": [r["id"] for r in rows], "changes": edits}
            return real_invoke(directory, provider_config, **kwargs)

        with patch.object(providers, "invoke", side_effect=mixed):
            result = table.run(root, config, {"actor": "curator", "payload": {"study": partial}})
        assert result["prepared"] == 2 and result["failed"] == 1
        assert len(seen) == 3 and seen[1:] == [["two"], ["two"]]
        print("PASS invalid-row isolation; successful rows are never regenerated")

        # A checkpointed QUEUED job must not restart itself after a worker crash.
        resume_job = runtime.enqueue(root, "study-prepare", "curator", {"study": key})
        claimed = runtime.claim(root)
        assert claimed and claimed["id"] == resume_job
        runtime.yield_job(root, claimed, {"study": key, "pending": True})
        runtime.recover(root)
        assert runtime.job(root, resume_job)["status"] == "INTERRUPTED"
        print("PASS worker restart requires explicit resume of checkpointed work")

        for finish, refusal, expected in [
            ("length", None, "length"),
            ("stop", "refused", "refused"),
        ]:
            raw = canonical(
                {"choices": [{"finish_reason": finish, "message": {"refusal": refusal}}]}
            )
            invocation = {"data": {"attempts": [{"status": "200", "response": raw}]}}
            assert providers.failure_kind(invocation) == expected
        print("PASS output truncation and refusal remain distinct")

        # Cancellation before publication does not turn an unfinished table into success.
        stop = threading.Event()
        stop.set()
        task_payload: dict[str, Any] = task["payload"]
        task_payload["restart"] = True
        before = catalog.get(registry, second[0])["version"]
        assert preparation.run(root, path, task, stop)["cancelled"]
        assert catalog.get(registry, second[0])["version"] == before
        stop.clear()
        assert preparation.run(root, path, task, stop)["failed"] == 0
        print("PASS cancellation and explicit resume preserve completed rows")

        # Native counters get the selected model's wire payload; no credentials appear in reports.
        for protocol, method in [
            ("openai-responses", "openai-responses"),
            ("anthropic-messages", "anthropic-messages"),
        ]:
            native = {**config, "mode": "live", "protocol": protocol, "token_counter": method}
            with patch.object(budget, "request", return_value={"input_tokens": 25}) as counted:
                tokens, used = budget.count(native, table.payload(native, {"unique": protocol}))
                assert tokens >= 25 and used == method and counted.call_count == 1
        local = {**config, "mode": "live", "token_counter": "llama"}
        with patch.object(
            budget, "request", return_value={"default_generation_settings": {"n_ctx": 8192}}
        ):
            assert budget.context(local) == 8192
        print("PASS native provider counters and actual local context ceiling")

        # An unavailable service does not multiply attempts across every row.
        isolated = copy.deepcopy(config)
        isolated["max_tokens"] = 513
        isolated["mock_response"] = {"reviewed_rows": [], "changes": []}
        key3 = studies.link(registry, ids, "Omitted", "curator", group="omitted")
        result = table.run(root, isolated, {"actor": "curator", "payload": {"study": key3}})
        assert result["failed"] == 3 and result["prepared"] == 0
        assert studies.get(registry, key3)["preparation"]["table_run"]["batches_done"] <= 9
        print("PASS omitted rows receive bounded recovery and visible failure tasks")
        assert all(
            load(p)["workflow"] == table.WORKFLOW
            for p in (registry / "table-manifests").glob("*.json")
        )


if __name__ == "__main__":
    main()
