"""Pinned real-tool signal and peak products from one validated pseudobulk group."""

from __future__ import annotations

import bisect
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from ..contracts.records import dump, fingerprint, load
from .common import complete, publication, software, text_file, verify_output

IMAGES = {
    "peaks": "kundajelab/dap_seq_peaks@sha256:"
    "985743417265eebc6bac16a984cd1a9f7dd405668c4a06e68b8cd3e1d334ec1b",
    "tracks": "kundajelab/dap_seq_tracks@sha256:"
    "e6dd6ceb79e50fc6db83e9346a920e51fd2bf0dcffbc18c9305b1b6e8c806390",
}
BIGWIG_SCRIPT = """
import json,pyBigWig
sizes=[(f[0],int(f[1])) for f in (l.split() for l in open('/out/chrom.sizes'))]
with pyBigWig.open('/out/signal.bw','w') as bw:
    bw.addHeader(sizes,maxZooms=0)
    for line in open('/out/cuts.bedGraph'):
        chrom,start,end,n=line.split()
        bw.addEntries([chrom],[int(start)],ends=[int(end)],values=[float(n)])
with pyBigWig.open('/out/signal.bw') as bw:
    header=bw.header()
    result={'pyBigWig':pyBigWig.__version__,'sum_counts':header['sumData'],
            'contigs':bw.chroms()}
with open('/out/bigwig-validation.json','w') as stream:
    json.dump(result,stream,sort_keys=True)
"""


def command(image: str, argv: list[str], directory: Path, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9./_-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("Scientific containers require immutable digests")
    args = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--cpus",
        "2",
        "--memory",
        "8g",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{directory.resolve()}:/out",
        "-w",
        "/out",
        image,
        *argv,
    ]
    if argv[:2] == ["macs3", "callpeak"]:
        # MACS3 uses exactly the container's CPU allocation; it does not detect cores itself.
        args += ["--threads", "2"]
    started = time.monotonic()
    with (
        (directory / (label + ".stdout")).open("w") as out,
        (directory / (label + ".stderr")).open("w") as err,
    ):
        result = subprocess.run(args, stdout=out, stderr=err, timeout=3600, check=False)
    dump(
        directory / (label + ".command.json"),
        {
            "argv": args,
            "image": image,
            "exit_status": result.returncode,
            "elapsed_seconds": time.monotonic() - started,
        },
    )
    if result.returncode:
        raise ValueError(f"{label} failed with exit {result.returncode}; retained full stderr")


def frip(fragments: Path, peaks: Path, sizes: dict[str, int]) -> dict[str, Any]:
    intervals: dict[str, list[tuple[int, int]]] = {}
    number = 0
    widths = []
    for line in peaks.read_text().splitlines():
        fields = line.split("\t")
        if len(fields) != 10:
            raise ValueError("Peak file must be narrowPeak")
        chrom, start, end = fields[0], int(fields[1]), int(fields[2])
        if chrom not in sizes or not 0 <= start < end <= sizes[chrom]:
            raise ValueError("Peak outside reference")
        intervals.setdefault(chrom, []).append((start, end))
        widths.append(end - start)
        number += 1
    merged = {}
    for chrom, regions in intervals.items():
        combined: list[tuple[int, int]] = []
        for start, end in sorted(regions):
            if combined and start <= combined[-1][1]:
                combined[-1] = (combined[-1][0], max(end, combined[-1][1]))
            else:
                combined.append((start, end))
        merged[chrom] = ([r[0] for r in combined], [r[1] for r in combined])
    total = overlaps = 0
    with text_file(fragments) as stream:
        for line in stream:
            fields = line.split("\t")
            chrom, start, end = fields[0], int(fields[1]), int(fields[2])
            total += 1
            if chrom in merged:
                starts, ends = merged[chrom]
                i = bisect.bisect_right(ends, start)
                overlaps += i < len(starts) and starts[i] < end
    return {
        "peak_count": number,
        "peak_widths": dict(sorted(Counter(widths).items())),
        "frip": overlaps / total if total else None,
        "overlapping_fragments": overlaps,
        "denominator_fragments": total,
        "definition": "Unique fragment rows overlapping the union of group peaks by >=1 bp",
        "tss_enrichment": None,
        "tss_status": "NOT_EVALUATED: no validated TSS input in this product stage",
        "plant_thresholds": "UNSPECIFIED",
    }


def run(core: Path, output: Path, *, resume: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    previous = verify_output(core, "scatac-group-core")
    data = previous["data"]
    group, policy = data["group"], data["policy"]
    signature = fingerprint(
        {
            "core": previous["version"],
            "images": IMAGES,
            "software": software(),
        }
    )
    if output.exists() and resume:
        saved = verify_output(output, "scatac-group")
        if saved["data"]["signature"] != signature:
            raise ValueError("Changed group or tool identity; choose a new output")
        return {"cached": True, "manifest": saved}
    peak_policy = policy["peak_calling"]
    genome_size = peak_policy["effective_genome_sizes"][group["reference"]["reference_id"]]
    qvalue = peak_policy["qvalue"]
    if (
        type(genome_size) is not int
        or genome_size <= 0
        or type(qvalue) not in {int, float}
        or not 0 < qvalue < 1
    ):
        raise ValueError("Explicit positive effective genome size and peak q-value are required")
    with publication(output) as stage:
        for path in core.iterdir():
            if path.is_file() and path.name not in {"complete.json", "execution.json"}:
                shutil.copyfile(path, stage / path.name)
        command(IMAGES["tracks"], ["python", "-c", BIGWIG_SCRIPT], stage, "bigwig")
        observed = load(stage / "bigwig-validation.json")
        if (
            observed["sum_counts"] != group["cut_count"]
            or observed["contigs"] != group["reference"]["contigs"]
        ):
            raise ValueError("bigWig counts/reference do not match the independent aggregation")
        if group["unique_fragments"]:
            command(
                IMAGES["peaks"],
                [
                    "macs3",
                    "callpeak",
                    "-t",
                    "/out/peaks-input.bedpe",
                    "-f",
                    "BEDPE",
                    "-g",
                    str(genome_size),
                    "-q",
                    str(qvalue),
                    "--keep-dup",
                    "all",
                    "-n",
                    "peaks",
                ],
                stage,
                "macs3",
            )
        else:
            (stage / "peaks_peaks.narrowPeak").write_text("")
        qc = frip(
            stage / "fragments.tsv.gz",
            stage / "peaks_peaks.narrowPeak",
            group["reference"]["contigs"],
        )
        dump(stage / "qc.json", qc)
        value = complete(
            stage,
            "scatac-group",
            {
                "signature": signature,
                "group": group,
                "policy": policy,
                "parents": data["parents"],
                "annotations_sha256": data["annotations_sha256"],
                "qc": qc,
                "images": IMAGES,
                "review_status": "UNREVIEWED",
                "model_ready": False,
            },
            started,
        )
    return {"cached": False, "manifest": value}
