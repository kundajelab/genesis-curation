"""Synthetic output writer used by worker integration tests; never runs sequencing tools."""

from __future__ import annotations

import csv
import gzip
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from genesis_curation.atac.inputs import digest, libraries, references
from genesis_curation.contracts.records import dump, load


# Copied from genesis-preprocessing genesis_tools/atac/run.py: how the ATAC pipeline turns a
# sheet into its run manifest, so this stand-in pipeline matches it without importing it.
def write_stable(path: Path, value: object) -> None:
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def prepare(sheet: Path, registry: Path, run_dir: Path) -> Path:
    refs = references(registry)
    records = libraries(sheet, refs)
    run_dir.mkdir(parents=True, exist_ok=True)
    inputs = run_dir / "inputs"
    inputs.mkdir(exist_ok=True)
    for library in records:
        path = inputs / f"{library['library_id']}.json"
        library["manifest"] = str(path)
    value = {
        "schema_version": 1,
        "libraries": records,
        "sample_sheet_sha256": digest(sheet),
        "registry_sha256": digest(registry),
    }
    manifest = run_dir / "manifest.json"
    if manifest.exists() and json.loads(manifest.read_text()) != value:
        raise ValueError(
            "Run inputs changed. Choose a new run directory to preserve the previous results"
        )
    for library in records:
        write_stable(Path(library["manifest"]), library)
    write_stable(manifest, value)
    return manifest


def track(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    python = os.environ.get("GENESIS_STUDY_FIXTURE_BIGWIG_PYTHON")
    if python:
        subprocess.run(
            [
                python,
                "-c",
                "import pyBigWig,sys; b=pyBigWig.open(sys.argv[1],'w'); "
                "b.addHeader([('chr1',100)]); "
                "b.addEntries(['chr1'],[10],ends=[11],values=[1.0]); b.close()",
                str(path),
            ],
            check=True,
        )
    else:
        path.write_bytes(b"UNVALIDATED_SYNTHETIC_BIGWIG")


def zipped(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fixture/fastqc_data.txt", "Explicitly synthetic fixture\n")


def main() -> None:
    args = sys.argv[1:]
    if args[:2] not in (["run", "pipeline-atac"], ["run", "pipeline"]):
        raise ValueError("Only synthetic DAP/ATAC fixture invocations are supported")
    atac = args[1] == "pipeline-atac"
    sheet = Path(args[2] if atac else args[args.index("-p") + 2])
    refs = Path(args[args.index("--references") + 1])
    root = (
        Path(args[args.index("--outdir") + 1])
        if atac
        else Path(args[args.index("-w") + 1]) / args[args.index("-n") + 1]
    )
    with (root / "synthetic-invocations.txt").open("a") as stream:
        stream.write("synthetic process; no sequencing computation\n")
    if (Path.cwd() / "fail-fixture").exists():
        raise SystemExit(2)
    if atac:
        prepare(sheet, refs, root)
        original = load(root / "manifest.json")
        dump(
            root / "provenance.json",
            {
                "manifest_sha256": digest(root / "manifest.json"),
                "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "containers": {"fixture": "NO-SCIENTIFIC-CONTAINER"},
                "source_sha256": {"fixture": digest(Path(__file__))},
                "exit_code": 0,
                "synthetic": True,
            },
        )
        for lib in original["libraries"]:
            out = root / "output" / lib["library_id"]
            (out / "fragments").mkdir(parents=True)
            (out / "fragments/fragments.bed.gz").write_bytes(
                gzip.compress(b"chr1\t10\t20\n", mtime=0)
            )
            (out / "fragments/fragments.bed.gz.tbi").write_text("synthetic index placeholder\n")
            (out / "fragments/chrom.sizes").write_text("chr1\t100\n")
            dump(
                out / "fragments/metrics.json",
                {
                    "metric_version": 1,
                    "counts": {
                        "total_templates": 1,
                        "eligible_before_dedup": 1,
                        "usable_fragments": 1,
                    },
                },
            )
            (out / "peaks").mkdir()
            (out / "peaks/atac_peaks.narrowPeak").write_text("")
            track(out / "tracks/cuts.counts.bw")
            for filename, data in (
                (
                    "qc/enrichment.json",
                    {
                        "metric_version": 1,
                        "FRiP": 0,
                        "peak_count": 0,
                        "usable_fragments": 1,
                        "fragments_in_peaks": 0,
                    },
                ),
                ("qc/adapters.json", {"synthetic": True}),
                ("qc/multiqc_data/multiqc_data.json", {"synthetic": True}),
                ("provenance/acquisition.json", {"synthetic": True}),
            ):
                dump(out / filename, data)
            (out / "qc/multiqc_report.html").write_text("<html>Synthetic study fixture</html>")
            for mate in (1, 2):
                zipped(out / f"qc/fastqc/read{mate}_fastqc.zip")
    else:
        with sheet.open() as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        samples = []
        for row in rows:
            name = row["sample_id"]
            samples.append(
                {
                    "id": name,
                    "species": row["species"],
                    "reference_fasta": row["reference_fasta"],
                    "ref_id": "tiny",
                    "layout": "SE",
                    "control": row["control_sample"],
                }
            )
            out = root / "output" / row["species"] / name
            out.mkdir(parents=True)
            (out / (name + ".metadata.tsv")).write_text("synthetic\ttrue\n")
            for suffix in ("CPM", "plus.CPM", "minus.CPM", "plus.5p.counts", "minus.5p.counts"):
                track(out / (name + ".tracks." + suffix + ".bw"))
            if row["control_sample"] != "-":
                (out / (name + ".macs3_peaks.narrowPeak.gz")).write_bytes(
                    gzip.compress(b"", mtime=0)
                )
                for suffix in ("RPM", "mean_RPKM"):
                    (out / (name + ".peaks." + suffix + ".tsv")).write_text("synthetic\ttrue\n")
            for suffix in ("main.stats.txt", "read1.stats.txt", "flagstat.txt", "idxstats.tsv"):
                (out / (name + ".qc.report." + suffix)).write_text("synthetic\ttrue\n")
            zipped(out / f"qc/fastqc/{name}.read1_fastqc.zip")
        dump(
            root / "output/multiqc/multiqc_data/genesis_provenance.json",
            {
                "run": {
                    "git_sha": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], text=True
                    ).strip()
                },
                "samples": samples,
                "synthetic": True,
            },
        )
        (root / "output/multiqc/multiqc_report.html").write_text(
            "<html>Synthetic DAP fixture</html>"
        )


if __name__ == "__main__":
    main()
