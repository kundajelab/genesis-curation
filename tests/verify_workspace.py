"""Lossless import, truthful provenance, durable campaigns and complete exports."""

from __future__ import annotations

import contextlib
import io
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from genesis_curation.contracts.records import canonical, dump, loads
from genesis_curation.curation import harmonize
from genesis_curation.llm import providers
from genesis_curation.registry import store
from genesis_curation.web import campaigns, catalog, results, runtime, workbook, worker, workspace


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-workspace-") as folder:
        root = Path(folder)
        runtime.initialize(root, "curator", "synthetic-workspace-password")
        # Duplicate header labels must not silently overwrite different columns.
        raw = results.xlsx(
            {
                "TSS": [{"entry": "SRP123456", "other": "first"}],
                "MPRA": [{"entry": "Plant promoter paper"}],
                "DAP-seq": [{"Species": "Arabidopsis thaliana", "Multi": "3", "Multi2": "7"}],
                "Bulk ATAC": [
                    {
                        "species": "Arabidopsis thaliana",
                        "sample_name": "one",
                        "tissue": "leaf",
                        "assay": "ATAC-seq",
                    }
                ],
            },
            [],
        )
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as incoming, zipfile.ZipFile(output, "w") as outgoing:
            for name in incoming.namelist():
                data = incoming.read(name)
                if name == "xl/worksheets/sheet3.xml":
                    data = data.replace(b">Multi2<", b">Multi<")
                outgoing.writestr(name, data)
        path = root / "reference.xlsx"
        path.write_bytes(output.getvalue())
        manifest = workspace.import_workbook(root, path)
        assert manifest["tabs"]["TSS"]["rows"] == 1
        assert manifest["tabs"]["MPRA"]["rows"] == 1
        assert workspace.import_workbook(root, path) == manifest
        dap = workspace.page(root, "DAP-seq")["items"][0]
        detail = workspace.history(root, dap["id"])
        assert detail["record"]["original"]["Multi [B]"] == "3"
        assert detail["record"]["original"]["Multi [C]"] == "7"
        assert [e["body"]["action"] for e in detail["events"]] == ["workbook-import"]
        assert not detail["changes"]
        assert dap["status"] == "Imported" and not dap["subject"]
        print("PASS lossless headers, first data rows, idempotency and import provenance")
        config = providers.select(
            Path(__file__).parents[1] / "examples/curation/providers.mock.json", "mock"
        )
        config.update(
            context_window_tokens=32000,
            max_input_tokens=28000,
            max_tokens=4096,
            token_counter="conservative",
        )
        config_path = root / "providers.json"
        dump(config_path, {"schema_version": 1, "providers": [config]})
        c = campaigns.create(root, config, {"ids": [dap["id"]]}, "curator")
        for _ in range(20):
            if not worker.once(root, config_path):
                break
        job = runtime.job(root, c["job"])
        assert job["status"] == "SUCCEEDED", job
        new = workspace.history(root, dap["id"])
        assert new["status"] == "Needs review"
        assert new["state"], new
        assert new["state"]["preparation_provenance"]["provider"] == "mock"
        assert any(e["body"]["action"] == "source-unresolved" for e in new["events"])
        assert new["curation_events"]
        # Immutable history cannot be rewritten to manufacture a successful discovery.
        try:
            with store.write(root / "catalog") as db:
                db.execute("UPDATE workspace_events SET body='{}'")
        except Exception as exc:
            assert "immutable workspace event" in str(exc)
        else:
            raise AssertionError("History was mutable")
        print("PASS queued table curation, missing-evidence review and immutable history")
        c = campaigns.create(
            root,
            config,
            {
                "mode": "discover",
                "species": ["Arabidopsis thaliana"],
                "assays": ["DAP-seq"],
                "goal": "root binding",
            },
            "curator",
        )
        with patch.object(
            campaigns,
            "run_search",
            return_value={
                "source": "geo",
                "query": "actual query",
                "rows": [
                    {
                        "title": "Dataset GSE123456",
                        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE123456",
                    }
                ],
                "next_offset": None,
            },
        ):
            assert worker.once(root, config_path)
        recorded = workspace.events(root, c["id"])["items"]
        assert any(e["body"]["action"] == "search-completed" for e in recorded)
        campaigns.control(root, c["id"], "pause", "curator")
        worker.once(root, config_path)
        with contextlib.closing(store.connect(root / "catalog")) as db:
            assert (
                db.execute(
                    "SELECT status FROM workspace_campaigns WHERE id=?", (c["id"],)
                ).fetchone()[0]
                == "PAUSED"
            )
        assert workspace.page(root, "DAP-seq")["total"] == 2
        print("PASS actual query receipts, discovery inventory, pause checkpoints")
        resumed = campaigns.control(root, c["id"], "resume", "curator")
        claimed = runtime.claim(root)
        assert claimed is not None and claimed["id"] == resumed["job"]
        runtime.recover(root)
        campaigns.recover(root)
        with contextlib.closing(store.connect(root / "catalog")) as db:
            assert (
                db.execute(
                    "SELECT status FROM workspace_campaigns WHERE id=?", (c["id"],)
                ).fetchone()[0]
                == "PAUSED"
            )
        print("PASS interrupted campaigns retain checkpoints and can resume")
        with store.write(root / "catalog") as db:
            workspace.add_row(
                db,
                "archive-fixture",
                "ATAC",
                1,
                {"experiment_accession": "SRX123456", "species": "Arabidopsis thaliana"},
                {},
                "Bulk ATAC",
                "snapshot:fixture:ATAC:1",
            )
            row = dict(
                db.execute("SELECT * FROM workspace_rows WHERE batch='archive-fixture'").fetchone()
            )
        report = canonical(
            [
                {
                    "experiment_accession": "SRX123456",
                    "sample_accession": "SAMN123456",
                    "scientific_name": "Arabidopsis thaliana",
                    "library_strategy": "ATAC-seq",
                }
            ]
        )
        xml = (
            "<SAMPLE_SET><SAMPLE><SAMPLE_ATTRIBUTES><SAMPLE_ATTRIBUTE>"
            "<TAG>tissue</TAG><VALUE>root</VALUE></SAMPLE_ATTRIBUTE>"
            "</SAMPLE_ATTRIBUTES></SAMPLE></SAMPLE_SET>"
        )
        with (
            patch.object(
                campaigns.intake,
                "fetch",
                return_value={
                    "text": report,
                    "url": "https://www.ebi.ac.uk/ena/portal/api/filereport",
                },
            ),
            patch.object(
                campaigns.intake,
                "archive_xml",
                side_effect=lambda _, acc: {
                    "text": xml,
                    "url": "https://www.ebi.ac.uk/ena/browser/api/xml/" + acc,
                },
            ),
        ):
            sid = campaigns.materialize(root, row, c, None)
        assert sid is not None
        state = catalog.get(root / "catalog", sid)
        extracted = {
            f["field"]: f for f in harmonize.extract({"data": {"sources": state["sources"]}}, {})
        }
        assert extracted["organism"]["value"] == "Arabidopsis thaliana"
        assert extracted["tissue"]["value"] == "root"
        assert any(s["data"]["snapshot"] == report for s in state["sources"])
        print("PASS raw archive retention and deterministic sample-field extraction")
        export = workspace.export_bundle(root, "fixture")
        with zipfile.ZipFile(root / "exports" / export["filename"]) as archive:
            manifest_export = loads(archive.read("manifest.json").decode())
            assert manifest_export["source_data_modified"] is False
            xlsx = root / "roundtrip.xlsx"
            xlsx.write_bytes(archive.read("metadata.xlsx"))
            tabs = workbook.read(xlsx)
            assert len(tabs["DAP-seq"]) == 3
            assert "changes.json" in archive.namelist()
        clean = workspace.export_bundle(root, "clean", clean=True)
        assert clean["rows"] == 0
        state = catalog.get(root / "catalog", new["subject"])
        assert not state["reviews"]
        print("PASS full workbook and history export; unreviewed claims excluded from clean export")


if __name__ == "__main__":
    main()
