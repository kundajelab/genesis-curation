"""Create tiny, explicitly synthetic DAP/ATAC artifact bundles without running a pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from genesis_curation.contracts.adapters import artifact, reference, snapshot
from genesis_curation.contracts.records import ArtifactManifest, dump, identity
from genesis_curation.contracts.validation import bundle


def create(directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "reference.fa").write_text(">synthetic1\n" + "A" * 100 + "\n")
    (directory / "tss.tsv").write_text("synthetic1\t30\t+\n")
    (directory / "source.json").write_text(
        '{"organism":"Arabidopsis thaliana","tissue":"leaf",'
        '"treatment":"explicitly synthetic","synthetic":true}\n'
    )
    (directory / "stderr.log").write_text("Synthetic diagnostic: checksum mismatch\n")
    manifests = []
    for assay, name in (("bulk-ATAC", "synthetic-atac"), ("DAP-seq", "synthetic-dap")):
        output = directory / name / "output"
        output.mkdir(parents=True)
        (output / "peaks.bed").write_text("")
        (output / "report.html").write_text("<html>Synthetic curation software fixture</html>\n")
        dataset = identity("dataset", "offline-tutorial", name)
        ref = reference(
            {
                "reference_id": "synthetic-ref",
                "fasta": str(directory / "reference.fa"),
                "tss": str(directory / "tss.tsv"),
                "contigs": {"synthetic1": 100},
            }
        )
        manifest = ArtifactManifest(
            "offline-tutorial",
            dataset,
            name,
            assay,
            "Arabidopsis thaliana",
            "synthetic-study",
            None,
            name,
            [{"lane_id": "L1"}],
            ref,
            "local",
            {
                "campaign_id": None,
                "job_id": None,
                "attempt": None,
                "execution_state": "SUCCEEDED",
                "git_sha": "0" * 40,
                "parameters": {"synthetic": True},
                "provenance_complete": True,
                "root": str(output.parent),
            },
            {
                "synthetic": True,
                "notes": "Fabricated software fixture; not pipeline/biological evidence",
            },
            [
                artifact(output, "peaks.bed", "local", dataset, "peaks", "bed", "synthetic-ref"),
                artifact(
                    output, "report.html", "local", dataset, "report", "html", "synthetic-ref"
                ),
            ],
            [snapshot(directory / "source.json", "local")],
            [],
        ).export()
        manifests.append(manifest)
    result = bundle(manifests, level="full")
    dump(directory / "bundle.json", result, immutable=True)
    dump(directory / "datasets.json", {"datasets": [m["id"] for m in manifests]}, immutable=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    create(args.directory.resolve())
    print(f"Created synthetic bundles in {args.directory}; no scientific pipeline was run")


if __name__ == "__main__":
    main()
