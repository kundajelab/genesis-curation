"""Offline release regressions: bounded results and conservative library decisions."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from genesis_curation.contracts.records import canonical
from genesis_curation.curation import harmonize
from genesis_curation.registry import store
from genesis_curation.scatac import workbook
from genesis_curation.web import (
    assessments,
    audit,
    catalog,
    eligibility,
    results,
    review_queue,
    studies,
)


def main() -> None:
    xml = (
        "<SAMPLE><SAMPLE_ATTRIBUTE><TAG>tissue</TAG><VALUE>root</VALUE></SAMPLE_ATTRIBUTE>"
        "<SAMPLE_LINK>SRX999 unrelated library</SAMPLE_LINK></SAMPLE>"
    )
    source = harmonize.source_record(xml, "synthetic:xml", "text/plain")
    excerpts = catalog.evidence_index([source], compact_xml=True)
    assert len(excerpts) == 1 and "unrelated" not in excerpts[0]["text"]
    assert harmonize.locate(source, excerpts[0]["locator"]) == excerpts[0]["text"]
    assert "unrelated" in catalog.evidence_index([source])[0]["text"]
    assert assessments.sample_attributes({"sources": [source]}) == {"tissue": {"root"}}
    assert not assessments.sample_attributes(
        {"sources": [source], "source_scopes": {source["version"]: "study_context"}}
    )
    multiple = harmonize.source_record(
        "<SET>" + xml + xml + "</SET>", "synthetic:multi", "text/plain"
    )
    assert not assessments.sample_attributes({"sources": [multiple]})
    field = {
        "field": "tissue",
        "value": "leaf",
        "status": "known",
        "evidence": [{"quote": "leaf", "locator": "line:1", "source_version": source["version"]}],
    }
    assert assessments.assess({"sources": [source]}, [field])["tissue"]["issues"]
    field["value"] = "root"
    assert not assessments.assess({"sources": [source]}, [field])["tissue"]["issues"]
    assert (
        audit.direct_values({"sources": [source]}, {"tissue"}, excerpts)["tissue"]["value"]
        == "root"
    )
    assert not audit.direct_values(
        {"sources": [source], "source_scopes": {source["version"]: "study_context"}},
        {"tissue"},
        excerpts,
    )
    generic = harmonize.source_record(
        '{"library_strategy":"RNA-Seq"}', "synthetic:strategy", "application/json"
    )
    assert not audit.direct_values(
        {"sources": [generic]}, {"assay"}, catalog.evidence_index([generic])
    )
    vocab = harmonize.vocabulary()
    assert vocab["version"] == "genesis-plant-terms/4"
    assert len({t["id"] for t in vocab["terms"]["tissue"].values()}) > 1000
    assert vocab["terms"]["tissue"]["root"]["id"] == "PO:0009005"
    assert harmonize.vocabulary("genesis-plant-terms/1")["version"] == "genesis-plant-terms/1"
    prompt = harmonize.prompt_vocabulary("Arabidopsis thaliana root, young panicle; control_2h")
    assert "root" in prompt["terms"]["tissue"]
    assert "control_2h" not in prompt["terms"]["tissue"]
    assert all(len(terms) <= 40 for terms in prompt["terms"].values())
    assert (
        eligibility.classify({"assay": "ATAC-seq", "note": "not an ATAC library"}, {})[
            "eligibility"
        ]
        == "excluded"
    )
    assert (
        eligibility.classify({"library_strategy": "AMPLICON"}, {"assay": "ATAC-seq"})["eligibility"]
        == "uncertain"
    )
    assert (
        eligibility.classify({"library_strategy": "RNA-Seq"}, {"assay": "ATAC-seq"})["eligibility"]
        == "uncertain"
    )
    assert (
        eligibility.classify({"assay": "ATAC-seq", "sample_title": "single nucleus"}, {})[
            "modality"
        ]
        == "scATAC"
    )
    assert eligibility.classify({}, {"assay": "CAGE"})["eligibility"] == "uncertain"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store.initialize(root)
        audit_id = audit.create(
            root,
            [
                {
                    "id": "xml-audit",
                    "state": {
                        "name": "xml-audit",
                        "fields": [{**field, "value": "leaf"}],
                        "sources": [source],
                    },
                    "audit_fields": ["tissue"],
                    "study": "synthetic",
                    "split": "evaluation",
                }
            ],
            "XML source-first",
            "test",
        )
        card = audit.view(root, audit_id, "xml-audit", "test")
        assert card["prediction"] is None and card["direct_values"]["tissue"]["value"] == "root"
        audit.save(
            root,
            audit_id,
            "xml-audit",
            "test",
            {
                "source_hash": card["source_hash"],
                "version": None,
                "labels": {"tissue": {"status": "known", "value": "root", "evidence": excerpts[0]}},
            },
        )
        assert audit.report(root, audit_id)["metrics"]["matching_values"] == 0
        with store.write(root) as db:
            for i in range(10000):
                key = f"sample-{i:05d}"
                state = {
                    "id": key,
                    "name": key,
                    "version": "fixture",
                    "original": {
                        "species": "Arabidopsis thaliana",
                        "assay": "ATAC-seq",
                        "study_accession": "SRP001",
                    },
                    "fields": [],
                    "reviews": [],
                    "sources": [],
                }
                state["reviews"] = {}
                db.execute(
                    "INSERT INTO curation_subjects VALUES(?,?,?)",
                    (key, "fixture", canonical(state)),
                )
                results.project(db, state)
            plan = db.execute(
                "EXPLAIN QUERY PLAN SELECT body FROM curation_result_rows WHERE modality=? "
                "AND subject>? ORDER BY subject LIMIT ?",
                ("Bulk ATAC", "", 51),
            ).fetchall()
            assert any("result_modality" in str(tuple(row)) for row in plan)
        with store.write(root) as db:
            db.execute("INSERT INTO curation_queue_owners VALUES('fixture','study',0,'{}')")
            db.executemany(
                "INSERT INTO curation_queue_tasks VALUES(?,?,?,?,?)",
                [("fixture", str(i), "open", 1, canonical({"id": str(i)})) for i in range(10000)],
            )
        with patch.object(
            studies, "view", side_effect=AssertionError("Unchanged studies reloaded")
        ):
            queue = review_queue.listing(root)
            following = review_queue.listing(root, cursor=queue["next_cursor"])
            assert queue["total"] == 10000 and len(queue["items"]) == 25
            assert not {r["id"] for r in queue["items"]} & {r["id"] for r in following["items"]}
        first = results.page(root)
        second = results.page(root, cursor=first["next_cursor"])
        assert first["total"] == 10000 and len(first["items"]) == 50
        assert not {r["subject"] for r in first["items"]} & {r["subject"] for r in second["items"]}
        assert all(r["review_state"] == "draft" for r in first["items"])
        assert results.page(root, species="Other")["total"] == 0
        export = root / "drafts.xlsx"
        export.write_bytes(results.workbook(root))
        sheets = workbook.read(export)
        assert len(sheets["Bulk ATAC"]) == 10001
        assert len(sheets["TSS"]) == 1 and len(sheets["Excluded"]) == 1
        assert "Manifest" in sheets
        assert "'=HYPERLINK" in results.csv_page([{"sample_name": '=HYPERLINK("bad")'}])
        key = studies.link(root, [f"sample-{i:05d}" for i in range(3)], "legacy overlap", "test")
        studies.preparation(
            root,
            key,
            {
                "completed": {"sample-00000": "v", "sample-00001": "v"},
                "failures": {"sample-00001": "citation", "sample-00002": "citation"},
            },
        )
        progress = studies.progress(root, key)
        assert (progress["total"], progress["prepared"], progress["failed"]) == (3, 1, 2)
    print(
        "PASS release results: 10,000 synthetic rows, indexed pagination, no overlap, "
        "draft status, controls and conflicting assay regressions; biological scale NOT EVALUATED"
    )


if __name__ == "__main__":
    main()
