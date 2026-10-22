"""Validate and sort fragments using a bounded-memory, disk-backed index."""

from __future__ import annotations

import csv
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import pysam

from ..contracts.records import dump, fingerprint, load
from .common import (
    bounded_lines,
    checked_asset,
    complete,
    publication,
    reference,
    software,
    text_file,
    verify_output,
)


def run(manifest: Path, output: Path, *, resume: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    inputs = load(manifest)
    if inputs.get("schema_version") != 1:
        raise ValueError("Expected fragment-input schema version 1")
    library = inputs["library"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", library["library_id"]):
        raise ValueError("Invalid library ID")
    producer = inputs["producer"]
    if producer["format"] not in {"10x-atac", "10x-arc"}:
        raise ValueError("Unsupported producer; an explicit adapter is required")
    if not producer.get("version") or producer["offsets"] != [4, -5]:
        raise ValueError("10x requires producer version and declared +4/-5 source offsets")
    if (
        producer["interval"] != "BED0-half-open"
        or producer["deduplication_scope"] != "library-barcode"
    ):
        raise ValueError("Unsupported coordinate or duplicate scope")
    if "barcode_correction" not in producer or "whitelist_sha256" not in producer:
        raise ValueError(
            "Declare barcode correction/whitelist provenance, including unknown values"
        )
    source = checked_asset(inputs["fragments"], manifest.parent)
    fasta, sizes = reference(inputs["reference"], manifest.parent)
    normalized = {
        **inputs,
        "fragments": {**inputs["fragments"], "path": str(source)},
        "reference": {
            **inputs["reference"],
            "fasta": {**inputs["reference"]["fasta"], "path": str(fasta)},
        },
    }
    signature = fingerprint({"input": inputs, "software": software()})
    if output.exists() and resume:
        previous = verify_output(output, "scatac-fragments")
        if previous["data"]["signature"] != signature:
            raise ValueError("Inputs or software changed; resume requires a new output")
        return {"cached": True, "output": str(output), "manifest": previous}
    ranks = {chrom: index for index, chrom in enumerate(sizes)}
    mito, plastid = (
        set(inputs["reference"]["mitochondrial_contigs"]),
        set(inputs["reference"]["plastid_contigs"]),
    )
    counts = {"fragments": 0, "support": 0, "nuclear": 0, "mitochondrial": 0, "plastid": 0}
    with publication(output) as stage:
        database = stage / "sorting.sqlite"
        db = sqlite3.connect(database)
        try:
            db.execute("PRAGMA cache_size=-32768")
            db.execute("PRAGMA temp_store=FILE")
            db.execute(
                "CREATE TABLE fragments (rank INT,chrom TEXT,start INT,end INT,barcode TEXT,"
                "support INT,strand TEXT,category TEXT, "
                "PRIMARY KEY(rank,start,end,barcode)) WITHOUT ROWID"
            )
            headers = []
            columns = None
            with text_file(source) as stream:
                for number, line in enumerate(bounded_lines(stream), 1):
                    if len(line) > 1024 * 1024:
                        raise ValueError(f"Oversized fragment line {number}")
                    if line.startswith("#"):
                        if sum(map(len, headers)) + len(line) > 1024 * 1024:
                            raise ValueError("Fragment header budget exceeded")
                        headers.append(line.rstrip("\n"))
                        continue
                    fields = line.rstrip("\r\n").split("\t")
                    if len(fields) not in {5, 6} or columns is not None and columns != len(fields):
                        raise ValueError(
                            f"Expected consistent five/six-column fragments at line {number}"
                        )
                    columns = len(fields)
                    chrom, left, right, barcode, support_text = fields[:5]
                    if chrom not in sizes or not re.fullmatch(r"[A-Za-z0-9_.:+-]+", barcode):
                        raise ValueError(
                            f"Unknown contig or missing/invalid barcode at line {number}"
                        )
                    if not all(re.fullmatch(r"\d+", text) for text in (left, right, support_text)):
                        raise ValueError(f"Noninteger coordinates/support at line {number}")
                    start, end, support = int(left), int(right), int(support_text)
                    if not 0 <= start < end <= sizes[chrom] or not 1 <= support < 2**63:
                        raise ValueError(f"Invalid interval/support at line {number}")
                    strand = fields[5] if columns == 6 else ""
                    if columns == 6 and strand not in {"+", "-"}:
                        raise ValueError(f"Invalid strand at line {number}")
                    category = (
                        "mitochondrial"
                        if chrom in mito
                        else "plastid"
                        if chrom in plastid
                        else "nuclear"
                    )
                    try:
                        db.execute(
                            "INSERT INTO fragments VALUES(?,?,?,?,?,?,?,?)",
                            (ranks[chrom], chrom, start, end, barcode, support, strand, category),
                        )
                    except sqlite3.IntegrityError:
                        raise ValueError(
                            f"Duplicate library/barcode/interval at line {number}"
                        ) from None
                    counts["fragments"] += 1
                    counts["support"] += support
                    counts[category] += 1
                    if counts["fragments"] % 10000 == 0:
                        db.commit()
            db.commit()
            if not counts["fragments"]:
                raise ValueError("Empty fragment input")
            gz = stage / "fragments.tsv.gz"
            with pysam.BGZFile(str(gz), "wb", index=None) as stream:
                for chrom, start, end, barcode, support, strand in db.execute(
                    "SELECT chrom,start,end,barcode,support,strand FROM fragments "
                    "ORDER BY rank,start,end,barcode"
                ):
                    fields = [chrom, str(start), str(end), barcode, str(support)]
                    if columns == 6:
                        fields.append(strand)
                    stream.write(("\t".join(fields) + "\n").encode())
            csi = max(sizes.values()) >= 2**29
            pysam.tabix_index(str(gz), preset="bed", csi=csi)
            with (stage / "barcodes.tsv").open("w", newline="") as stream:
                writer = csv.writer(stream, delimiter="\t")
                writer.writerow(
                    [
                        "library_id",
                        "barcode",
                        "unique_fragments",
                        "support",
                        "mitochondrial_fragments",
                        "plastid_fragments",
                        "annotation_status",
                    ]
                )
                barcode_count = 0
                for row in db.execute(
                    "SELECT barcode,COUNT(*),SUM(support),SUM(category='mitochondrial'),"
                    "SUM(category='plastid') FROM fragments GROUP BY barcode ORDER BY barcode"
                ):
                    writer.writerow([library["library_id"], *row, "UNANNOTATED"])
                    barcode_count += 1
            counts["barcodes"] = barcode_count
        finally:
            db.close()
        database.unlink()
        dump(stage / "input.json", normalized)
        dump(stage / "source-headers.json", {"headers": headers})
        value = complete(
            stage,
            "scatac-fragments",
            {
                "signature": signature,
                "input": normalized,
                "counts": counts,
                "excluded_records": 0,
                "coordinate_changes": 0,
                "index": "csi" if csi else "tbi",
                "columns": columns,
                "software": software(),
                "warnings": ["Ingestion does not establish cell identities or biological quality"],
            },
            started,
        )
    return {"cached": False, "output": str(output), "manifest": value}
