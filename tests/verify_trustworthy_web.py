"""Evidence risk, guided decisions, audit isolation and recoverable schema migration."""

from __future__ import annotations

import copy
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from genesis_curation.contracts.records import canonical, dump, fingerprint
from genesis_curation.llm import providers
from genesis_curation.registry import store
from genesis_curation.web import (
    assessments,
    audit,
    catalog,
    preparation,
    review_queue,
    runtime,
    studies,
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-trust-") as temporary:
        root = Path(temporary)
        runtime.initialize(root, "curator", "synthetic-audit-password")
        registry = root / "catalog"
        ids = catalog.import_rows(
            registry,
            canonical(
                [
                    {
                        "id": "sample-a",
                        "organism": "Arabidopsis thaliana",
                        "assay": "RNA-seq",
                        "tissue": "leaf",
                        "chemistry": "Space Ranger",
                        "description": "Libraries used Smart-seq2 chemistry.",
                    },
                    {
                        "id": "sample-b",
                        "organism": "Arabidopsis thaliana",
                        "assay": "RNA-seq",
                        "tissue": "root",
                        "chemistry": "Space Ranger",
                        "description": "Libraries used Smart-seq2 chemistry.",
                    },
                ]
            ),
            "table.json",
            "quality",
            "id",
            "public",
            "curator",
        )
        key = studies.link(registry, ids, "Quality", "curator")
        old = [catalog.get(registry, sid) for sid in ids]
        assert all(not r.get("assessments") for r in old)
        assert all(t["field"] != "chemistry" for t in studies.get(registry, key)["tasks"])
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
        preparation.run(
            root,
            path,
            {
                "actor": "curator",
                "payload": {"study": key, "provider": "mock", "provider_hash": fingerprint(config)},
            },
        )
        current = studies.get(registry, key)
        risky = [t for t in current["tasks"] if t["field"] == "chemistry"]
        assert len(risky) == 1 and len(risky[0]["targets"]) == 2
        assert "software" in risky[0]["reason"].lower()
        assert current["preparation"]["table_run"]["batches_done"] == 2
        # High confidence and sample majority cannot waive a concrete contradiction.
        fields = copy.deepcopy(current["records"][0]["fields"])
        chemistry = next(f for f in fields if f["field"] == "chemistry")
        chemistry["confidence"] = 0.999
        assert assessments.assess(current["records"][0], fields)["chemistry"]["issues"]
        print("PASS concrete evidence issues override confidence; one bounded verification round")

        queue = review_queue.listing(registry, limit=1)
        assert queue["total"] == 1 and queue["items"][0]["affected"] == 2
        assert "sources" not in canonical(queue)
        detail = review_queue.detail(registry, key, risky[0]["id"])
        assert all(
            not s["data"]["snapshot"] for r in detail["study"]["records"] for s in r["sources"]
        )
        studies.draft(registry, "curator", "guided", {"study": key, "task": risky[0]["id"]})
        assert studies.draft(registry, "curator", "guided")["task"] == risky[0]["id"]
        print("PASS compact cross-study queue, lazy evidence and persisted review position")

        snapshots: list[dict[str, Any]] = [
            {
                "id": r["id"],
                "state": copy.deepcopy(r),
                "study": "synthetic",
                "split": "evaluation" if i else "development",
                "task_fields": ["chemistry"],
            }
            for i, r in enumerate(current["records"])
        ]
        for sample in snapshots:
            sample["candidate"] = copy.deepcopy(sample["state"])
            sample["candidate_task_fields"] = ["chemistry"]
            for field in sample["candidate"]["fields"]:
                if field["field"] == "organism":
                    field["value"] = "Oryza sativa"
        audit_id = audit.create(registry, snapshots, "Synthetic reference", "curator")
        before = fingerprint([catalog.get(registry, sid) for sid in ids])
        first = audit.view(registry, audit_id, ids[0], "curator")
        assert first["prediction"] is None and first["answer"] is None
        assert "recommendations" not in first and "state" not in first
        audit.save_draft(registry, audit_id, ids[0], "curator", {"labels": {}})
        assert audit.view(registry, audit_id, ids[0], "curator")["draft"] == {"labels": {}}
        labels = {
            name: {"status": "cannot_determine", "value": "", "evidence": None}
            for name in first["fields"]
        }
        evidence = next(e for e in first["evidence"] if e["text"] == "Arabidopsis thaliana")
        labels["organism"] = {
            "status": "known",
            "value": "Arabidopsis thaliana",
            "evidence": evidence,
        }
        body = {"source_hash": first["source_hash"], "labels": labels, "version": None}
        try:
            audit.save(registry, audit_id, ids[0], "curator", {**body, "source_hash": "wrong"})
        except ValueError:
            pass
        else:
            raise AssertionError("Changed source accepted")
        revealed = audit.save(registry, audit_id, ids[0], "curator", body)
        assert revealed["prediction"] is not None and revealed["answer"]["actor"] == "curator"
        try:
            audit.save(registry, audit_id, ids[0], "curator", body)
        except ValueError:
            pass
        else:
            raise AssertionError("Stale reference answer accepted")
        report = audit.report(registry, audit_id)
        assert report["metrics"]["judged_fields"] == 1
        assert report["metrics"]["cannot_determine"] == len(first["fields"]) - 1
        assert report["metrics"]["matching_values"] == 1
        assert report["candidate_metrics"]["judged_fields"] == 1
        assert report["candidate_metrics"]["matching_values"] == 0
        assert report["candidate_metrics"]["errors_without_task"] == 1
        assert not report["held_out_labels_used_for_training"]
        assert fingerprint([catalog.get(registry, sid) for sid in ids]) == before
        assert all(not catalog.examples(registry, r) for r in current["records"])
        print(
            "PASS blinded audit, exact evidence, draft/reveal, stale rejection and label isolation"
        )

        corrections = {}
        for r in current["records"]:
            source = next(s for s in r["sources"] if "Smart-seq2" in s["data"]["snapshot"])
            corrections[r["id"]] = {
                "value": "Smart-seq2",
                "status": "known",
                "evidence": [
                    {
                        "source_version": source["version"],
                        "locator": "/description",
                        "quote": "Libraries used Smart-seq2 chemistry.",
                    }
                ],
            }
        updated = studies.resolve(
            registry,
            risky[0]["id"],
            {
                "study": key,
                "version": risky[0]["version"],
                "action": "correct",
                "reason": "Chemistry is explicit in the source",
                "corrections": corrections,
                "remember": True,
            },
            "curator",
        )
        assert not review_queue.listing(registry)["total"]
        release = studies.approve(registry, key, updated["version"], "curator")
        assert all(r["metadata"]["chemistry"] == "Smart-seq2" for r in release["records"])
        assert not review_queue.listing(registry)["ready"]
        exemplar = catalog.get(registry, ids[0])
        assert catalog.examples(registry, exemplar)
        incompatible = copy.deepcopy(exemplar)
        incompatible["metadata"]["assay"] = "ATAC-seq"
        assert not catalog.examples(registry, incompatible)
        print(
            "PASS one correction resolves two samples (50% fewer submissions); assay-scoped memory"
        )

        fields = [
            {"field": "biosample_accession", "value": "SAMN12345", "status": "known"},
            {"field": "organism", "value": "Arabidopsis thaliana", "status": "known"},
        ]
        rows = [
            {"id": "a", "fields": fields},
            {"id": "b", "fields": [fields[0], {**fields[1], "value": "Oryza sativa"}]},
        ]
        assert set(assessments.cross_sample(rows)) == {"a", "b"}
        rows[1]["fields"][0] = {**fields[0], "value": "SAMN99999"}
        assert not assessments.cross_sample(rows)
        print("PASS contradictory identity detection preserves legitimate sample differences")

        legacy = root / "legacy"
        legacy.mkdir()
        with sqlite3.connect(legacy / "registry.sqlite") as db:
            for version in range(1, 7):
                for statement in store.MIGRATIONS[version]:
                    db.execute(statement)
            db.execute("PRAGMA user_version=6")
        store.initialize(legacy)
        assert list(legacy.glob("before-v6-to-v7-*.sqlite"))
        snapshot = root / "backup.sqlite"
        store.backup(registry, snapshot)
        restored = root / "restored"
        store.restore(snapshot, restored)
        assert audit.report(restored, audit_id) == report
        assert catalog.export(restored) == catalog.export(registry)
        print("PASS v6-to-v7 migration backup, audit restore and unchanged signed metadata")


if __name__ == "__main__":
    main()
