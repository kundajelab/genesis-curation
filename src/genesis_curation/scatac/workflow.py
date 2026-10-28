"""Group bundles for model export from the genesis-preprocessing scATAC workflow's outputs."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, TextIO

from ..contracts.records import canonical, dump, fingerprint
from .common import (
    complete,
    contigs,
    digest,
    publication,
    reference,
    software,
    text_file,
    verify_output,
)

# The workflow keeps the producer's fragments as written; for 10x these are BED0 half-open with
# the +4/-5 Tn5 offsets applied. It drops organelle fragments and unlisted barcodes.
OFFSETS = [4, -5]
POLICY = {
    "schema_version": 1,
    "version": "genesis-preprocessing-scatac-v1",
    "source": "genesis-preprocessing workflows/scatac-seq.nf",
    "signal_convention": "chrombpnet-atac-v1",
    "organellar": "exclude",
    "boundary_cuts": "exclude-and-report",
}
SIGNAL = {
    "kind": "raw_counts",
    "unit": "cut",
    "normalization": "none",
    "strand": "both",
    # The workflow's own bigWig counts the last fragment base (end - 1), one base left of this.
    "derivation": "chrombpnet-atac-v1 from the workflow's fragments; two endpoints per row, "
    "not per support",
}
QC_COLUMNS = (
    "pseudobulk",
    "cells",
    "fragments",
    "insertions",
    "insertions_in_peaks",
    "FRiP",
    "peaks",
    "TSS_enrichment",
)
# Writes per-base cut counts from a sorted bedGraph, then reads the file back.
BIGWIG_SCRIPT = """
import json, sys
import pyBigWig
sizes, bedgraph, out = sys.argv[1:4]
header = [(c, int(n)) for c, n in (line.split() for line in open(sizes))]
with pyBigWig.open(out, 'w') as bw:
    bw.addHeader(header, maxZooms=0)
    batch = None
    for line in open(bedgraph):
        chrom, start, end, value = line.split()
        if batch is None or batch[0] != chrom or len(batch[1]) == 100000:
            if batch and batch[1]:
                bw.addEntries(batch[0], batch[1], values=batch[2], span=1)
            batch = (chrom, [], [])
        batch[1].append(int(start))
        batch[2].append(float(value))
    if batch and batch[1]:
        bw.addEntries(batch[0], batch[1], values=batch[2], span=1)
total = 0.0
with pyBigWig.open(out) as bw:
    chroms = bw.chroms()
    # Sum in 1 Mb windows: reading whole contigs' intervals takes far more memory.
    for chrom, length in chroms.items():
        for start in range(0, length, 1_000_000):
            end = min(start + 1_000_000, length)
            total += bw.stats(chrom, start, end, type="sum", exact=True)[0] or 0.0
print(json.dumps({"sum_counts": total, "contigs": chroms}))
"""


def cuts(start: int, end: int) -> tuple[int, int]:
    """The chrombpnet-atac-v1 cut positions of one fragment, as in the earlier pseudobulk step."""
    return start + 4 - OFFSETS[0], end - 4 - OFFSETS[1] - 1


def table(stream: TextIO) -> list[dict[str, str]]:
    rows = [line.rstrip("\n").split("\t") for line in stream if not line.startswith("#")]
    if not rows or len(set(rows[0])) != len(rows[0]):
        raise ValueError("Expected a table with a unique header")
    if any(len(row) != len(rows[0]) for row in rows[1:]):
        raise ValueError("Ragged table row")
    return [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]


def aggregate(
    fragments: Path, groups: dict[str, str], sizes: dict[str, int], scratch: Path
) -> dict[str, dict[str, int]]:
    """Stream the sorted fragments once, writing each group's per-base cut counts as bedGraph.

    Rows are sorted by contig name, then start, so every later cut lies at or after the current
    start; counts left of it are final and are written out as the scan advances.
    """
    names = sorted(set(groups.values()))
    stats = {
        g: {"unique_fragments": 0, "total_support": 0, "cut_count": 0, "excluded_boundary_cuts": 0}
        for g in names
    }
    pending: dict[str, dict[int, int]] = {g: {} for g in names}
    outputs = {g: (scratch / f"{g}.bedGraph").open("w") for g in names}

    def flush(chrom: str, limit: int | None) -> None:
        for group, counts in pending.items():
            ready = sorted(p for p in counts if limit is None or p < limit)
            outputs[group].writelines(f"{chrom}\t{p}\t{p + 1}\t{counts.pop(p)}\n" for p in ready)

    current: str | None = None
    done: set[str] = set()
    previous = horizon = 0
    try:
        with text_file(fragments) as stream:
            for line in stream:
                if line.startswith("#"):
                    continue
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 5:
                    raise ValueError("Fragment rows need chrom, start, end, barcode and support")
                chrom, barcode = fields[0], fields[3]
                start, end, support = int(fields[1]), int(fields[2]), int(fields[4])
                group = groups.get(barcode)
                if group is None:
                    raise ValueError("Fragment barcode is not in the workflow's cell list")
                if chrom not in sizes or not 0 <= start < end <= sizes[chrom] or support < 1:
                    raise ValueError("Fragment is not an interval on the reference")
                if chrom != current:
                    if current is not None:
                        flush(current, None)
                        done.add(current)
                    if chrom in done or (current is not None and chrom < current):
                        raise ValueError("Fragments are not sorted by contig name")
                    current, previous, horizon = chrom, 0, 0
                if start < previous:
                    raise ValueError("Fragments are not sorted by start")
                previous = start
                if start >= horizon:
                    flush(chrom, start)
                    horizon = start + 65536
                stat = stats[group]
                stat["unique_fragments"] += 1
                stat["total_support"] += support
                counts = pending[group]
                for position in cuts(start, end):
                    if not 0 <= position < sizes[chrom]:
                        stat["excluded_boundary_cuts"] += 1
                        continue
                    counts[position] = counts.get(position, 0) + 1
                    stat["cut_count"] += 1
        if current is not None:
            flush(current, None)
    finally:
        for output in outputs.values():
            output.close()
    return stats


def peaks(source: Path, destination: Path, sizes: dict[str, int]) -> int:
    count = 0
    with text_file(source) as stream, destination.open("w") as out:
        for line in stream:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 10:
                raise ValueError("Peaks must be narrowPeak")
            chrom, start, end, summit = fields[0], int(fields[1]), int(fields[2]), int(fields[9])
            if chrom not in sizes or not 0 <= start < end <= sizes[chrom]:
                raise ValueError("Peak is not an interval on the reference")
            if not 0 <= summit < end - start:
                raise ValueError("Model peaks require a defined in-peak summit")
            out.write("\t".join(fields) + "\n")
            count += 1
    return count


def quality(path: Path, group_id: str, peak_count: int) -> dict[str, Any]:
    with path.open() as stream:
        rows = table(stream)
    if len(rows) != 1 or tuple(rows[0]) != QC_COLUMNS or rows[0]["pseudobulk"] != group_id:
        raise ValueError("Unexpected workflow QC table")
    row = rows[0]
    if int(row["peaks"]) != peak_count:
        raise ValueError("QC peak count differs from the peak file")
    tss = row["TSS_enrichment"]
    return {
        "source": "genesis-preprocessing SCATAC_QC",
        "cells": int(row["cells"]),
        "fragments": int(row["fragments"]),
        "insertions": int(row["insertions"]),
        "insertions_in_peaks": int(row["insertions_in_peaks"]),
        "frip": float(row["FRiP"]),
        "peak_count": peak_count,
        "definition": "Tn5 insertions in the group's MACS3 peaks over all its insertions",
        "tss_enrichment": None if tss == "NA" else float(tss),
        "tss_status": "NOT_COMPUTED" if tss == "NA" else "COMPUTED",
    }


def import_library(
    run: Path,
    reference_fields: dict[str, Any],
    bigwig_python: Path,
    output: Path,
    *,
    resume: bool = False,
) -> dict[str, Any]:
    """Make one signed scatac-group bundle per cell group of one workflow library.

    run is the workflow's output/<species>/<sample_id> folder. Peaks, cells and QC come from
    the workflow; the signal is rebuilt from its fragments with the chrombpnet-atac-v1 cuts.
    """
    started = time.monotonic()
    library = run.name
    fragments = run / f"{library}.fragments.tsv.gz"
    cell_table = run / "qc" / f"{library}.cells.tsv"
    with cell_table.open() as stream:
        listed = table(stream)
    barcodes = [row["barcode"] for row in listed]
    if not listed or len(set(barcodes)) != len(barcodes):
        raise ValueError("Each listed cell must appear once")
    # Cells with no nuclear fragments are in the workflow's cell table but in none of its
    # groups' fragments, QC or peaks; a group of only such cells has no outputs at all.
    cells = [row for row in listed if int(row["fragments"]) > 0]
    if not cells:
        raise ValueError("No listed cell has nuclear fragments")
    groups = {row["barcode"]: row["group"] for row in cells}
    empty_groups = sorted({row["group"] for row in listed} - set(groups.values()))
    fasta = Path(reference_fields["fasta"])
    sizes = contigs(fasta)
    value = {
        "reference_id": reference_fields["reference_id"],
        "assembly": reference_fields["assembly"],
        "species": reference_fields["species"],
        "fasta": {"path": str(fasta.resolve()), "sha256": digest(fasta)},
        "contigs": sizes,
        "mitochondrial_contigs": list(reference_fields["mitochondrial_contigs"]),
        "plastid_contigs": list(reference_fields["plastid_contigs"]),
    }
    reference(value, Path("/"))
    names = sorted(set(groups.values()))
    group_files = {
        g: {
            "peaks": run / "groups" / g / f"{library}.{g}.macs3_peaks.narrowPeak.gz",
            "qc": run / "groups" / g / f"{library}.{g}.scatac_qc_mqc.tsv",
        }
        for g in names
    }
    inputs = {
        "fragments": digest(fragments),
        "cells": digest(cell_table),
        "groups": {g: {k: digest(p) for k, p in files.items()} for g, files in group_files.items()},
    }
    signature = fingerprint(
        {"inputs": inputs, "reference": value, "policy": POLICY, "software": software()}
    )
    if output.exists() and resume:
        saved = verify_output(output, "scatac-workflow-import")
        if saved["data"]["signature"] != signature:
            raise ValueError("Changed workflow outputs, reference or software; use a new output")
        return {"cached": True, "manifest": saved}
    with publication(output) as stage, tempfile.TemporaryDirectory(dir=stage.parent) as scratch:
        stats = aggregate(fragments, groups, sizes, Path(scratch))
        sizes_text = "".join(f"{c}\t{n}\n" for c, n in sizes.items())
        # pyBigWig wants entries in header order; the fragments are sorted by contig name.
        (Path(scratch) / "header.sizes").write_text(
            "".join(f"{c}\t{sizes[c]}\n" for c in sorted(sizes))
        )
        versions = {}
        for g in names:
            group_id = f"{library}.{g}"
            members = sorted(
                (row for row in cells if row["group"] == g), key=lambda r: r["barcode"]
            )
            stat = stats[g]
            if stat["unique_fragments"] != sum(int(row["fragments"]) for row in members):
                raise ValueError("Fragments disagree with the workflow's per-cell counts")
            destination = stage / g
            destination.mkdir()
            (destination / "chrom.sizes").write_text(sizes_text)
            peak_count = peaks(
                group_files[g]["peaks"], destination / "peaks_peaks.narrowPeak", sizes
            )
            qc = quality(group_files[g]["qc"], group_id, peak_count)
            if qc["fragments"] != stat["unique_fragments"] or qc["cells"] != len(members):
                raise ValueError("Workflow QC disagrees with the fragments")
            with (destination / "cells.jsonl").open("w") as stream:
                for row in members:
                    source = {"library": library, **row}
                    stream.write(
                        canonical({"cell_id": f"{library}:{row['barcode']}", "source": source})
                        + "\n"
                    )
            result = subprocess.run(
                [
                    str(bigwig_python),
                    "-I",
                    "-c",
                    BIGWIG_SCRIPT,
                    str(Path(scratch) / "header.sizes"),
                    str(Path(scratch) / f"{g}.bedGraph"),
                    str(destination / "signal.bw"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise ValueError("bigWig writer failed: " + result.stderr.strip()[-2000:])
            observed = json.loads(result.stdout)
            if observed["sum_counts"] != stat["cut_count"] or observed["contigs"] != sizes:
                raise ValueError("bigWig counts or contigs differ from the aggregation")
            group = {
                "group_id": group_id,
                "library": library,
                "group": g,
                "cells": len(members),
                **stat,
                "reference": value,
                "identity": {
                    "producer": POLICY["source"],
                    "coordinate_offsets": OFFSETS,
                    "fragments_sha256": inputs["fragments"],
                    "annotation_sha256": inputs["cells"],
                },
                "library_composition": {library: len(members)},
                "signal": SIGNAL,
                "plant_thresholds": "UNSPECIFIED",
                "status": "IMPORTED",
            }
            dump(destination / "metadata.json", group)
            dump(destination / "qc.json", qc)
            manifest = complete(
                destination,
                "scatac-group",
                {
                    "signature": signature,
                    "group": group,
                    "policy": POLICY,
                    "qc": qc,
                    "parents": {"workflow": inputs},
                    "annotations_sha256": inputs["cells"],
                    "review_status": "UNREVIEWED",
                    "model_ready": False,
                },
                started,
            )
            versions[g] = manifest["version"]
        value_out = complete(
            stage,
            "scatac-workflow-import",
            {
                "signature": signature,
                "library": library,
                "groups": versions,
                "policy": POLICY,
                "num_cells_without_fragments": len(listed) - len(cells),
                "groups_without_fragments": empty_groups,
            },
            started,
        )
    return {"cached": False, "manifest": value_out, "output": str(output)}
