"""Mining discovery, safe retrieval, checkpointing and reviewer handoff regressions."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

from genesis_curation.contracts.records import canonical, dump, fingerprint, now
from genesis_curation.llm import providers
from genesis_curation.registry import store
from genesis_curation.web import (
    catalog,
    intake,
    mining,
    mining_documents,
    mining_sources,
    review_queue,
    runtime,
    studies,
    table,
    worker,
)


def rejected(action: Callable[[], object], phrase: str) -> None:
    try:
        action()
    except ValueError as error:
        assert phrase in str(error), error
    else:
        raise AssertionError("Expected validation failure")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-mining-") as temporary:
        root = Path(temporary)
        runtime.initialize(root, "curator", "synthetic-mining-test-password")
        config = providers.select(
            Path(__file__).parents[1] / "examples/curation/providers.mock.json", "mock"
        )
        config.update(
            context_window_tokens=32768,
            max_input_tokens=32768,
            max_tokens=4096,
            token_counter="conservative",
        )
        config_path = root / "providers.json"
        dump(config_path, {"schema_version": 1, "providers": [config]})
        body = {
            "goal": "Find Arabidopsis RNA-seq leaf studies",
            "species": ["Arabidopsis thaliana"],
            "assays": ["RNA-seq"],
            "limits": {"candidates": 8, "studies": 3, "samples": 10, "minutes": 30},
        }
        rejected(
            lambda: mining.create(root, {**body, "limits": {"samples": -1}}, config, "curator"),
            "limits",
        )
        rejected(
            lambda: mining.create(root, {**body, "species": ["human"]}, config, "curator"),
            "species",
        )
        run = mining.create(root, body, config, "curator")

        def query(
            _root: Path, value: dict[str, Any], _cancel: threading.Event | None = None
        ) -> dict[str, Any]:
            rows = [
                {
                    "accession": key,
                    "title": title,
                    "species": "Arabidopsis thaliana",
                    "summary": title,
                    "url": intake.geo_url(key),
                    "search_assay": "RNA-seq",
                }
                for key, title in [
                    ("GSE100", "leaf RNA-seq"),
                    ("GSE200", "uncertain tissue"),
                    ("GSE300", "root unrelated"),
                    ("GSE400", "mirror"),
                ]
            ]
            return {
                "candidates": rows,
                "total": len(rows),
                "next_offset": None,
                "sources": [],
                "query": value,
            }

        def inspect(
            _root: Path, accession: str, _cancel: threading.Event | None = None
        ) -> dict[str, Any]:
            text = accession + " Arabidopsis leaf RNA-seq evidence"
            return {
                "text": text,
                "aliases": [],
                "sources": [
                    {"text": text, "url": intake.geo_url(accession), "retrieved_at": now()}
                ],
            }

        def ai(
            _root: Path,
            _run: dict[str, Any],
            _config: dict[str, Any],
            data: dict[str, Any],
            schema: dict[str, Any],
            mock: dict[str, Any],
            cancel: threading.Event | None,
        ) -> dict[str, Any]:
            if schema == mining.PLAN_SCHEMA:
                return {"terms": ["leaf"], "summary": body["goal"]}
            text = data["evidence"]
            decision = (
                "uncertain" if "GSE200" in text else "exclude" if "GSE300" in text else "match"
            )
            return {
                "decision": decision,
                "reason": decision + " from supplied evidence",
                "quotes": [text],
            }

        def import_study(
            _root: Path,
            accession: str,
            actor: str,
            cancel: threading.Event | None = None,
            sample_limit: int | None = None,
            sample_offset: int = 0,
        ) -> str:
            ids = catalog.import_rows(
                root / "catalog",
                canonical(
                    [
                        {
                            "accession": accession + "_one",
                            "organism": "Arabidopsis thaliana",
                            "assay": "RNA-seq",
                            "tissue": "leaf",
                        }
                    ]
                ),
                "sample.json",
                accession,
                "accession",
                "public",
                actor,
            )
            key = studies.link(root / "catalog", ids, accession, actor, accession)
            studies.preparation(
                root / "catalog",
                key,
                {
                    "selection": {
                        "available_records": 1,
                        "selected_records": 1,
                        "pilot_limit": sample_limit,
                    }
                },
            )
            return key

        with (
            patch.object(mining_sources, "search", side_effect=query),
            patch.object(mining_sources, "inspect", side_effect=inspect),
            patch.object(mining_sources, "investigate", return_value={"text": "", "ledger": []}),
            patch.object(mining, "invoke", side_effect=ai),
            patch.object(intake, "import_accession", side_effect=import_study),
            patch.object(mining_documents, "gather", return_value=[]),
        ):
            for _ in range(80):
                if not worker.once(root, config_path):
                    break
            result = mining.read(root, run["id"])
            assert result["status"] == "WAITING", result
            assert [c["status"] for c in result["candidates"]] == [
                "ready",
                "uncertain",
                "excluded",
                "ready",
            ]
            assert result["samples"] == 2
            queued = review_queue.listing(root / "catalog")
            decision = next(i for i in queued["items"] if i.get("kind") == "mining-inclusion")
            mining.draft(
                root,
                run["id"],
                decision["id"],
                "curator",
                {"version": decision["version"], "reason": "Check the treatment methods"},
            )
            assert (
                mining.draft(root, run["id"], decision["id"], "curator")["reason"]
                == "Check the treatment methods"
            )
            assert mining.draft(root, run["id"], decision["id"], "other") == {}
            rejected(
                lambda: mining.decide(
                    root,
                    run["id"],
                    decision["id"],
                    {"version": "stale", "action": "include", "reason": "yes"},
                    "curator",
                ),
                "Stale",
            )
            mining.decide(
                root,
                run["id"],
                decision["id"],
                {
                    "version": decision["version"],
                    "action": "include",
                    "reason": "Verified leaf methods",
                },
                "curator",
            )
            for _ in range(40):
                if not worker.once(root, config_path):
                    break
            result = mining.read(root, run["id"])
            assert result["status"] == "COMPLETED", result
            assert result["samples"] == 3
            assert not mining.question_rows(root)
            assert result["candidates"][1]["review"]["actor"] == "curator"
            repeated = mining.create(root, body, config, "curator")
            for _ in range(40):
                if not worker.once(root, config_path):
                    break
            again = mining.read(root, repeated["id"])
            assert sum(c["status"] == "existing" for c in again["candidates"]) == 2
            assert again["samples"] == 0
        print("PASS discovery, automatic curation, inclusion queue and idempotent existing studies")

        candidate: dict[str, Any] = {**result["candidates"][0], "assessment_round": 0}
        with (
            patch.object(mining_sources, "inspect", side_effect=inspect),
            patch.object(mining_sources, "investigate", return_value={"text": "", "ledger": []}),
            patch.object(
                mining,
                "invoke",
                return_value={
                    "decision": "match",
                    "reason": "Unsupported match",
                    "quotes": ["invented evidence"],
                },
            ),
        ):
            mining.assess(root, result, candidate, config, None)
            assert candidate["status"] == "investigating"
            mining.assess(root, result, candidate, config, None)
            assert candidate["status"] == "uncertain"
            assert not candidate["quotes"]
            assert len(candidate["citation_errors"]) == 2
        print(
            "PASS unsupported citations trigger investigation then human review, never acceptance"
        )

        failure_record = {
            "version": "synthetic-failed-invocation",
            "data": {"status": "FAILED", "error": "synthetic", "usage": {"total_tokens": 7}},
        }
        usage_before = result["usage"].get("total_tokens", 0)
        with patch.object(
            providers, "invoke", side_effect=providers.InferenceFailure(failure_record)
        ):
            rejected(
                lambda: mining.invoke(root, result, config, {"task": "synthetic"}, {}, {}, None),
                "synthetic",
            )
        assert result["usage"]["total_tokens"] == usage_before + 7
        last = result["ledger"][-1]
        assert last["invocation"] == "synthetic-failed-invocation"
        assert (root / "mining-inputs" / (last["input_hash"] + ".json")).is_file()
        print("PASS failed inference usage and exact model input remain auditable")

        paused = mining.create(root, body, config, "curator")
        claimed = runtime.claim(root)
        assert claimed and claimed["id"] == paused["job"]
        mining.control(
            root, paused["id"], {"version": paused["version"], "action": "pause"}, "curator"
        )
        assert mining.step(root, config_path, claimed)["cancelled"]
        runtime.finish(root, claimed["id"], {"cancelled": True})
        paused = mining.read(root, paused["id"])
        resumed = mining.control(
            root, paused["id"], {"version": paused["version"], "action": "resume"}, "curator"
        )
        assert runtime.claim(root)
        runtime.recover(root)
        mining.recover(root)
        assert mining.read(root, resumed["id"])["status"] == "INTERRUPTED"
        print("PASS pause, explicit restart recovery and stale decision rejection")

        original = catalog.get(
            root / "catalog",
            studies.get(root / "catalog", result["candidates"][0]["study"])["records"][0]["id"],
        )
        state = catalog.attach(
            root / "catalog",
            original["id"],
            original["version"],
            "\n".join(
                f"Methods paragraph {i}: " + "plants and observations " * 170 for i in range(45)
            ),
            "long-methods",
            "public",
            "curator",
            scope="study_context",
        )
        compiled, actual, limits = table.plan(root, [state], config, None, "")
        assert limits["fits"] and compiled["coverage"]["omitted_passages"] > 0
        assert compiled["coverage"]["complete"] is False
        assert catalog.get(root / "catalog", state["id"])["sources"] == state["sources"]
        for ref, bindings in compiled["bindings"].items():
            assert ref in compiled["data"]["evidence"]
            assert all(
                e["quote"] in s["data"]["snapshot"]
                for es in bindings.values()
                for e in es
                for s in state["sources"]
                if s["version"] == e["source_version"]
            )
        print(
            "PASS oversized evidence fits actual context with explicit coverage and intact sources"
        )

        for url in [
            "https://example.org/sample.csv",
            "https://ftp.ncbi.nlm.nih.gov/geo/sample.fastq.gz",
            "https://ftp.ncbi.nlm.nih.gov/geo/sample_counts.tsv",
            "http://127.0.0.1/sample.csv",
        ]:
            rejected(lambda url=url: mining_documents.location(url), "")
        assert mining_documents.location(
            "ftp://ftp.ncbi.nlm.nih.gov/geo/series/sample_metadata.tsv"
        ).startswith("https:")
        extracted = mining_documents.rows(
            Path("sample.csv"), b"accession,tissue\nGSM123,leaf\nGSM456,root\n"
        )
        assert len(extracted) == 2 and "GSM123" in extracted[0][1]
        with patch.object(
            mining_sources,
            "json_source",
            return_value=(
                [
                    {
                        "run_accession": "SRR123",
                        "sample_accession": "SRS123",
                        "fastq_ftp": "ftp.sra.ebi.ac.uk/a;ftp.sra.ebi.ac.uk/b",
                        "fastq_md5": "abc;def",
                        "fastq_bytes": "12;34",
                    }
                ],
                {"text": "report", "retrieved_at": now()},
            ),
        ):
            inventory = mining_sources.inventory(root, "SRP123")
            assert len(inventory["files"]) == 2
            assert inventory["files"][1]["bytes"] == 34
            assert not inventory["files"][0]["download_verified"]
        bound = catalog.import_rows(
            root / "catalog",
            canonical([{"accession": "GSM123", "study_accession": "GSE100"}]),
            "bound.json",
            "bound",
            "accession",
            "public",
            "curator",
        )[0]
        bound_state = catalog.get(root / "catalog", bound)
        attachment = root / "sample_metadata.csv"
        content = b"sample,tissue\nGSM123,leaf\nGSM456,root\nGSE100,stem\nGSM123 GSM456,shoot\n"
        candidate = {"accession": "GSE100", "files": []}
        with (
            patch.object(
                intake,
                "fetch",
                return_value={
                    "text": "!Series_supplementary_file = https://ftp.ncbi.nlm.nih.gov/geo/sample_metadata.csv"
                },
            ),
            patch.object(mining_documents, "fetch", return_value=(attachment, content)),
        ):
            ledger = mining_documents.gather(root, candidate, [bound_state], "curator", None)
        assert ledger[-1]["matched_rows"] == 1
        attached = catalog.get(root / "catalog", bound)["sources"][-1]["data"]["snapshot"]
        assert (
            "leaf" in attached
            and "root" not in attached
            and "stem" not in attached
            and "shoot" not in attached
        )
        print("PASS metadata-only downloads, exact sample bindings and advertised file inventories")

        legacy = root / "legacy"
        legacy.mkdir()
        with sqlite3.connect(legacy / "registry.sqlite") as db:
            for version in range(1, 8):
                for sql in store.MIGRATIONS[version]:
                    db.execute(sql)
            db.execute("PRAGMA user_version=7")
        store.initialize(legacy)
        assert list(legacy.glob("before-v7-to-v8-*.sqlite"))
        with store.write(root / "catalog") as db:
            events = [json.loads(r[0]) for r in db.execute("SELECT body FROM mining_events")]
            assert any(e["action"] == "candidate-include" and e["candidates"] for e in events)
            try:
                db.execute("DELETE FROM mining_events")
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError("Mining audit history must be immutable")
        first = {
            "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE100",
            "text": "first",
            "retrieved_at": now(),
        }
        second = {**first, "text": "second"}
        intake.retain(root, first)
        intake.retain(root, second)
        assert (
            json.loads((root / "source-snapshots" / (fingerprint("first") + ".json")).read_text())[
                "text"
            ]
            == "first"
        )
        print("PASS migration backup, immutable inclusion history and retained source revisions")


if __name__ == "__main__":
    main()
