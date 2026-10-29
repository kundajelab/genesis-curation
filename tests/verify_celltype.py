"""Synthetic contracts for external RNA labels, review identity and ATAC selection."""

from __future__ import annotations

import csv
import io
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from genesis_curation.celltype import adapter
from genesis_curation.celltype.cells import cells
from genesis_curation.contracts.records import canonical, dump, load


def rejects(action: Callable[[], object], message: str) -> None:
    try:
        action()
    except ValueError as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError("Invalid annotation accepted")


def write_table(path: Path, rows: list[dict[str, str]]) -> None:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(stream.getvalue())


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        repo = root / "upstream"
        for name in (
            "SKILL.md",
            "VERSION",
            "references/file_formats.md",
            "scripts/apply_annotations.py",
        ):
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic fixture\n")
        expression = root / "rna.h5ad"
        expression.write_bytes(b"synthetic identity fixture, not an AnnData object")
        context = root / "context.json"
        dump(
            context,
            dict(
                dataset_id="synthetic",
                species="plant",
                tissue="leaf",
                developmental_stage="seedling",
                source="synthetic",
                assay="multiome-RNA",
            ),
        )
        handoff = root / "handoff.json"
        with patch.object(
            adapter.subprocess, "check_output", side_effect=["", adapter.UPSTREAM, "a" * 40]
        ):
            adapter.prepare(repo, expression, context, handoff)
        ann = root / "annotations.tsv"
        ann.write_text(
            "Resolution\tCluster\tCellType\tEvidence\trule_id\n"
            "sub\tx\tUnknown\tSynthetic doublet exclusion\t999\n"
            "0.5\t0,1\tMesophyll\tSynthetic specific marker\t999\n"
        )
        obs = root / "observations.tsv"
        obs.write_text(
            "library_id\tbarcode\tsub\tleiden_res0.5\nA\tBC\tx\t0\nA\tOTHER\ty\t1\nB\tBC\ty\t0\n"
        )
        template = load(
            Path(__file__).parents[1] / "src/genesis_curation/scatac/assets/cell-v1.json"
        )
        original = []
        for library, barcode, included in (
            ("A", "BC", True),
            ("A", "OTHER", False),
            ("B", "BC", True),
            ("B", "UNMATCHED", True),
        ):
            original.append(
                dict(
                    library_id=library,
                    barcode=barcode,
                    author_original_label="Published",
                    harmonized_label="Old",
                    annotation_source="published-fixture",
                    annotation_status="UNREVIEWED",
                    cell_calling_status="PASS",
                    doublet_status="UNKNOWN",
                    ontology_id="old-ontology",
                    atac_present=True,
                    rna_present=True,
                    atac_include=included,
                    rna_include=included,
                    qc_thresholds="UNSPECIFIED",
                    qc_metrics=dict.fromkeys(template["properties"]["qc_metrics"]["required"]),
                )
            )
        cell_file = root / "cells.jsonl"
        cell_file.write_text("".join(canonical(c) + "\n" for c in original))
        proposals = root / "proposals"
        command = subprocess.run(
            [
                sys.executable,
                "-m",
                "genesis_curation.execution.controller",
                "celltype",
                "import",
                "--handoff",
                str(handoff),
                "--annotations",
                str(ann),
                "--observations",
                str(obs),
                "--cells",
                str(cell_file),
                "--output",
                str(proposals),
            ],
            capture_output=True,
            text=True,
        )
        assert command.returncode == 0, command.stderr
        proposal = load(proposals / "proposal.json")
        assert proposal["unmatched_cells"] == 1
        assert proposal["rules"][1]["shadowed"] == 1
        assert [r["proposed_label"] for r in proposal["proposals"]] == [
            "Unknown",
            "Mesophyll",
            "Mesophyll",
        ]
        assert list(cells(proposals / "cells.jsonl")) == original
        decisions = root / "decisions.tsv"
        decisions.write_bytes((proposals / "review.tsv").read_bytes())
        pending = root / "pending"
        adapter.export(proposals, decisions, pending)
        assert all(not c["atac_include"] for c in cells(pending / "cells.jsonl"))
        reviews = adapter.table(decisions, {"decision"})
        for row in reviews:
            row.update(decision="APPROVED", reviewer="Fixture reviewer", reason="Synthetic review")
        write_table(decisions, reviews)
        reviewed = root / "reviewed"
        adapter.export(proposals, decisions, reviewed)
        result = list(cells(reviewed / "cells.jsonl"))
        assert [c["atac_include"] for c in result] == [False, False, True, False]
        assert [c["harmonized_label"] for c in result] == ["Old", "Mesophyll", "Mesophyll", "Old"]
        assert all(c["author_original_label"] == "Published" for c in result)
        assert result[2]["ontology_id"] is None
        assert result[2]["qc_metrics"] == original[2]["qc_metrics"]
        rejects(lambda: adapter.export(proposals, decisions, reviewed), "Output exists")
        reviews[1]["reviewer"] = ""
        write_table(decisions, reviews)
        rejects(lambda: adapter.export(proposals, decisions, root / "bad"), "named reviewer")
        reviews[1]["proposal_version"] = "stale"
        write_table(decisions, reviews)
        rejects(lambda: adapter.export(proposals, decisions, root / "bad"), "Stale")
        for spec, columns in [("0.5", {"leiden_res0.5", "snn_res0.5"}), ("missing", {"sub"})]:
            rejects(
                lambda spec=spec, columns=columns: adapter.resolve_key(spec, columns),
                "ambiguous" if spec == "0.5" else "Missing",
            )
        assert adapter.resolve_key("0.5", {"SCT_snn_res.0.5"}) == "SCT_snn_res.0.5"
        original[0]["rna_present"] = False
        original[0]["rna_include"] = False
        cell_file.write_text("".join(canonical(c) + "\n" for c in original))
        rejects(lambda: adapter.propose(handoff, ann, obs, cell_file, root / "bad"), "matched RNA")
        original[0]["rna_present"] = True
        cell_file.write_text("".join(canonical(c) + "\n" for c in original))
        obs.write_text(obs.read_text() + "A\tBC\tx\t0\n")
        rejects(lambda: adapter.propose(handoff, ann, obs, cell_file, root / "bad"), "duplicate")
        expression.write_bytes(b"changed")
        rejects(
            lambda: adapter.propose(handoff, ann, obs, cell_file, root / "bad"), "input changed"
        )
        (proposals / "cells.jsonl").write_text("tampered\n")
        rejects(lambda: adapter.export(proposals, decisions, root / "bad"), "checksum")
    print(
        "PASS celltype: ordered Unknown exclusions, library joins, review identity, "
        "QC preservation, "
        "CLI import and tamper rejection; biological accuracy NOT EVALUATED"
    )


if __name__ == "__main__":
    main()
