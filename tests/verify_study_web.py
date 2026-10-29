"""Study-level review, imports, evidence scope and signed-release invariants."""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from genesis_curation.contracts.records import canonical, fingerprint
from genesis_curation.curation import harmonize
from genesis_curation.registry import store
from genesis_curation.web import catalog, intake, preparation, runtime, studies, study_api, worker


def reject(call: Callable[[], object], message: str = "") -> None:
    try:
        call()
    except (ValueError, sqlite3.IntegrityError) as exc:
        assert not message or message in str(exc), str(exc)
    else:
        raise AssertionError("Invalid operation succeeded")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-study-") as temporary:
        root = Path(temporary)
        runtime.initialize(root, "curator", "synthetic-study-password")
        registry = root / "catalog"
        config = Path(__file__).parents[1] / "examples/curation/providers.mock.json"
        ids = catalog.import_rows(
            registry,
            canonical(
                [
                    {
                        "id": "sample-a",
                        "organism": "Arabidopsis thaliana",
                        "assay": "RNA-seq",
                        "tissue": "leaf",
                    },
                    {"id": "sample-b", "organism": "Arabidopsis thaliana", "assay": "RNA-seq"},
                ]
            ),
            "pilot.json",
            "pilot",
            "id",
            "public",
            "curator",
        )
        before = catalog.export(registry)
        studies.link_imports(registry, ids, "curator")
        assert catalog.export(registry) == before
        key = studies.listing(registry)["items"][0]["id"]
        initial = studies.get(registry, key)
        reject(lambda: studies.approve(registry, key, initial["version"], "curator"), "preparation")
        first_task = initial["tasks"][0]
        reject(
            lambda: study_api.queue(
                root,
                config,
                key,
                "curator",
                {
                    "version": initial["version"],
                    "task": first_task["id"],
                    "task_version": first_task["version"],
                },
            ),
            "full study preparation",
        )
        queued = study_api.queue(root, config, key, "curator", {"version": initial["version"]})
        assert queued["id"]
        assert worker.once(root, config)
        study = studies.get(registry, key)
        assert study["preparation"]["status"] == "COMPLETED", study["preparation"]
        assert len(study["tasks"]) == 1, study["tasks"]
        task = study["tasks"][0]
        assert task["field"] == "tissue" and len(task["targets"]) == 1
        body = {
            "study": key,
            "version": task["version"],
            "action": "defer",
            "reason": "Check methods",
        }
        studies.draft(registry, "curator", task["id"], {**body, "reason": "Saved draft"})
        assert studies.draft(registry, "curator", task["id"])["reason"] == "Saved draft"
        studies.resolve(registry, task["id"], body, "curator")
        study = studies.get(registry, key)
        assert study["pending"] == 1
        reject(lambda: studies.approve(registry, key, study["version"], "curator"), "outstanding")
        task = study["tasks"][0]
        studies.resolve(
            registry,
            task["id"],
            {**body, "version": task["version"], "action": "unknown"},
            "curator",
        )
        study = studies.get(registry, key)
        assert study["status"] == "ready"
        reject(lambda: studies.approve(registry, key, initial["version"], "curator"), "Stale")
        release = studies.approve(registry, key, study["version"], "curator")
        assert studies.get(registry, key)["approval_current"]
        digest = release["integrity"]["sha256"]
        assert fingerprint({k: v for k, v in release.items() if k != "integrity"}) == digest
        assert len(release["records"]) == 2
        assert all(
            r["annotations"]["organism"]["vocabulary_id"] == "NCBITaxon:3702"
            for r in release["records"]
        )
        assert all(r["annotations"]["organism"]["original"] is None for r in release["records"])
        assert all(r["metadata"]["organism"] == "Arabidopsis thaliana" for r in release["records"])
        assert "tissue" not in next(r for r in release["records"] if r["id"] == ids[1])["metadata"]
        state = catalog.get(registry, ids[1])
        catalog.attach(
            registry, ids[1], state["version"], "tissue: root", "new source", "public", "curator"
        )
        assert not studies.get(registry, key)["approval_current"]
        reject(lambda: studies.export(registry, key), "sign-off")
        assert studies.export(registry, key, digest) == release
        with store.write(registry) as db:
            reject(lambda: db.execute("DELETE FROM curation_releases"), "immutable")
            reject(lambda: db.execute("UPDATE curation_study_events SET body='{}'"), "immutable")
        print(
            "PASS import, preparation, drafts, deferred/unknown decisions, "
            "sign-off, invalidation and immutable exports"
        )

        # Low model confidence is an exception even when a value has a valid quote.
        sample = catalog.get(registry, ids[0])
        sample["reviews"] = {}
        next(f for f in sample["fields"] if f["field"] == "tissue")["confidence"] = 0.4
        routed = studies.tasks(studies.get(registry, key), [sample])
        assert any(
            t["field"] == "tissue" and t["kind"] == "uncertain" and t["status"] == "open"
            for t in routed
        )
        print("PASS low-confidence supported values require an explicit decision")

        # Grouped corrections validate all members before committing any mutation.
        missing = catalog.import_rows(
            registry,
            canonical([{"id": "one"}, {"id": "two"}]),
            "missing.json",
            "missing",
            "id",
            "public",
            "curator",
        )
        other = studies.link(registry, missing, "Missing fields", "curator")
        study = studies.get(registry, other)
        task = next(t for t in study["tasks"] if t["field"] == "organism")
        baseline = catalog.export(registry)
        reject(
            lambda: studies.resolve(
                registry,
                task["id"],
                {
                    "study": other,
                    "version": task["version"],
                    "action": "correct",
                    "reason": "Invalid",
                    "corrections": {
                        missing[0]: {"value": "invented", "status": "known", "evidence": []}
                    },
                },
                "curator",
            )
        )
        assert catalog.export(registry) == baseline
        selected, coverage = preparation.select_evidence(
            {"original": {"accession": "GSM12345"}},
            [
                {"source_version": "a", "locator": "line:1", "text": "GSM99999 tissue: leaf"},
                {"source_version": "a", "locator": "line:2", "text": "GSM12345 tissue: root"},
            ],
            ["tissue"],
        )
        assert len(selected) == 1 and "GSM12345" in selected[0]["text"]
        assert coverage["omitted_passages"] == 1
        reject(
            lambda: preparation.verify_scope(
                {"original": {"accession": "GSM12345"}},
                [{"evidence": [{"source_version": "a", "quote": "GSM99999 leaf"}]}],
            ),
            "different sample",
        )
        reject(
            lambda: preparation.verify_scope(
                {
                    "original": {"accession": "GSM12345"},
                    "source_scopes": {"paper": "study_context"},
                },
                [
                    {
                        "status": "known",
                        "evidence": [{"source_version": "paper", "quote": "leaf tissue"}],
                    }
                ],
            ),
            "explicit sample attribution",
        )
        source = {
            "version": "q",
            "data": {"media_type": "text/plain", "snapshot": "kit: QuantSeq 3’\u00a0mRNA"},
        }
        response = [
            {
                "field": "protocol",
                "evidence": [
                    {"source_version": "q", "locator": "line:1", "quote": "QuantSeq 3’ mRNA"}
                ],
            }
        ]
        restored, repairs = preparation.restore_quote_whitespace(
            {"data": {"sources": [source]}}, response
        )
        assert restored[0]["evidence"][0]["quote"] == "QuantSeq 3’\u00a0mRNA" and len(repairs) == 1
        assert response[0]["evidence"][0]["quote"] == "QuantSeq 3’ mRNA"
        response[0]["evidence"][0]["quote"] = "invented kit"
        unchanged, repairs = preparation.restore_quote_whitespace(
            {"data": {"sources": [source]}}, response
        )
        assert unchanged == response and not repairs
        print("PASS exact source whitespace restoration; raw response preserved")
        taxonomy_source = harmonize.source_record(
            "dna_organism: Arabidopsis thaliana\ntf_source_organism: Zea mays",
            "synthetic binding metadata",
            "text/plain",
        )
        taxonomy_bundle = {"data": {"sources": [taxonomy_source]}}
        fields = harmonize.extract(taxonomy_bundle, {})
        harmonize.verify_fields(taxonomy_bundle, fields)
        for name, taxon in (
            ("dna_organism", "NCBITaxon:3702"),
            ("tf_source_organism", "NCBITaxon:4577"),
        ):
            field = next(f for f in fields if f["field"] == name)
            assert field["vocabulary_id"] == taxon
        assert harmonize.vocabulary()["version"] == "genesis-plant-terms/4"
        field["vocabulary_id"] = "NCBITaxon:3702"
        reject(lambda: harmonize.verify_fields(taxonomy_bundle, fields), "mismatched term")
        print("PASS distinct DNA/TF taxa and historical vocabularies; wrong taxon IDs rejected")
        print("PASS grouped correction rollback and wrong-sample evidence rejection")

        # Intake failure retains the missing GSM and supports a subsequent retry.
        def fake_fetch(
            _root: Path, url: str, cancellation: threading.Event | None = None
        ) -> dict[str, str]:
            if "GSE12345" in url:
                return {
                    "text": "!Series_sample_id = GSM12345\n!Series_sample_id = GSM12346",
                    "url": url,
                }
            if "GSM12346" in url:
                raise ValueError("Synthetic retrieval failure")
            return {
                "text": "!Sample_organism_ch1 = Arabidopsis thaliana\n"
                "!Sample_characteristics_ch1 = tissue: leaf",
                "url": url,
            }

        with patch.object(intake, "fetch", fake_fetch):
            imported = intake.import_accession(root, "GSE12345", "curator")
        result = studies.get(registry, imported)
        assert result["member_count"] == 2
        assert len(result["preparation"]["failures"]) == 1
        studies.preparation(registry, imported, {"status": "RUNNING"})
        studies.recover(registry)
        assert studies.get(registry, imported)["preparation"]["status"] == "INTERRUPTED"
        queued_job = runtime.enqueue(
            root, "study-intake", "curator", {"study": imported, "accession": "GSE12345"}
        )
        studies.preparation(registry, imported, {"status": "QUEUED", "job": queued_job})
        studies.recover(registry, {queued_job})
        assert studies.get(registry, imported)["preparation"]["status"] == "QUEUED"
        with patch.object(
            intake, "import_accession", side_effect=ValueError("Unavailable metadata")
        ):
            assert worker.once(root, config)
        assert runtime.job(root, queued_job)["status"] == "FAILED"
        assert studies.get(registry, imported)["preparation"]["status"] == "FAILED"
        studies.preparation(registry, imported, {"status": "QUEUED"})
        studies.recover(registry, set())
        assert studies.get(registry, imported)["preparation"]["status"] == "INTERRUPTED"
        assert (
            preparation.select_skill(
                root / "skills",
                {
                    "fields": [],
                    "sources": [{"version": "sample", "data": {"snapshot": "single cell RNA-seq"}}],
                },
            )["id"]
            == "plant-celltype-metadata"
        )
        print("PASS failed/queued study recovery and cell-type skill selection")
        print("PASS partial public intake preserves failed samples and interrupted preparation")

        # Only the linked article's PMC ID is followed, never its cited references.
        requested = []

        def paper_fetch(
            _root: Path, url: str, cancellation: threading.Event | None = None
        ) -> dict[str, str]:
            requested.append(url)
            xml = (
                "<PubmedArticleSet><PubmedArticle><ArticleTitle>Study</ArticleTitle>"
                '<PubmedData><ArticleIdList><ArticleId IdType="pmc">PMC123</ArticleId>'
                "</ArticleIdList>"
                "<ReferenceList><Reference><ArticleIdList>"
                '<ArticleId IdType="pmc">PMC999</ArticleId>'
                "</ArticleIdList></Reference></ReferenceList></PubmedData></PubmedArticle></PubmedArticleSet>"
            )
            return {
                "url": url,
                "text": xml if "efetch" in url else "<article><p>Methods</p></article>",
            }

        with patch.object(intake, "fetch", paper_fetch):
            documents, ledger = intake.articles(root, "!Series_pubmed_id = 12345")
        assert len(requested) == 2 and len(documents) == 2
        assert not any("PMC999" in url for url in requested)
        print("PASS linked-publication retrieval excludes cited-reference traversal")

        # Prototype registries must be archived, never silently migrated.
        old = root / "prototype"
        old.mkdir()
        with contextlib.closing(sqlite3.connect(old / "registry.sqlite")) as db:
            db.execute("PRAGMA user_version=5")
        try:
            store.initialize(old)
        except ValueError:
            pass
        else:
            raise AssertionError("Prototype database was accepted")
        print("PASS prototype registry rejected")

        # Cursor pagination returns every study without the former global cap.
        for index in range(205):
            studies.link(registry, [missing[0]], f"Page {index}", "curator", group=str(index))
        seen, cursor = set(), ""
        while True:
            page = studies.listing(registry, cursor, 50)
            seen.update(s["id"] for s in page["items"])
            if not page["next_cursor"]:
                break
            cursor = page["next_cursor"]
        assert len(seen) == page["total"]
        print("PASS cursor pagination returns every study")


if __name__ == "__main__":
    main()
