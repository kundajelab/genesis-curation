"""Synthetic output writer used by worker integration tests; never runs sequencing tools."""

from __future__ import annotations

import csv
import gzip
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from genesis_curation.contracts.records import dump


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
    if args[:2] != ["run", "pipeline"]:
        raise ValueError("Only synthetic DAP fixture invocations are supported")
    sheet = Path(args[args.index("-p") + 2])
    root = Path(args[args.index("-w") + 1]) / args[args.index("-n") + 1]
    with (root / "synthetic-invocations.txt").open("a") as stream:
        stream.write("synthetic process; no sequencing computation\n")
    if (Path.cwd() / "fail-fixture").exists():
        raise SystemExit(2)
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
            (out / (name + ".macs3_peaks.narrowPeak.gz")).write_bytes(gzip.compress(b"", mtime=0))
            for suffix in ("RPM", "mean_RPKM"):
                (out / (name + ".peaks." + suffix + ".tsv")).write_text("synthetic\ttrue\n")
        for suffix in ("main.stats.txt", "read1.stats.txt", "flagstat.txt", "idxstats.tsv"):
            (out / (name + ".qc.report." + suffix)).write_text("synthetic\ttrue\n")
        zipped(out / f"qc/fastqc/{name}.read1_fastqc.zip")
    dump(
        root / "output/multiqc/multiqc_data/genesis_provenance.json",
        {
            "run": {
                "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
            },
            "samples": samples,
            "synthetic": True,
        },
    )
    (root / "output/multiqc/multiqc_report.html").write_text("<html>Synthetic DAP fixture</html>")


if __name__ == "__main__":
    main()
