"""Validate exact references and explicit paired-end library policies before execution."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
FIELDS = {
    "library_id",
    "sample_id",
    "biological_replicate",
    "lane_id",
    "reference_id",
    "read1",
    "read2",
    "read1_sha256",
    "read2_sha256",
    "mapq",
    "duplicates",
    "adapter_r1",
    "adapter_r2",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identifier(value: str) -> str:
    if not ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(
            "Identifiers must contain only letters, digits, dots, underscores or dashes"
        )
    return value


def asset(value: str, checksum: str, base: Path, *, remote: bool = False) -> str:
    if not SHA.fullmatch(checksum):
        raise ValueError("Every input requires an explicit SHA256")
    if any(c in value for c in "\r\n\x00"):
        raise ValueError("Input path contains a control character")
    parsed = urlsplit(value)
    if parsed.scheme in {"https", "http", "ftp", "s3"}:
        if not remote or parsed.scheme != "https":
            raise ValueError("Stage this input locally, or use a public HTTPS FASTQ")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "Credentials and signed URLs must not appear in manifests; stage locally"
            )
        return value
    path = (base / value).resolve()
    if not path.is_file() or digest(path) != checksum:
        raise ValueError(f"Missing input or SHA256 mismatch: {path.name}")
    return str(path)


def references(path: Path) -> dict[str, dict[str, Any]]:
    value = json.loads(path.read_text())
    if value.get("schema_version") != 1 or not isinstance(value.get("references"), list):
        raise ValueError("Reference registry requires schema_version 1 and a references list")
    result = {}
    for original in value["references"]:
        ref = dict(original)
        key = identifier(ref["reference_id"])
        if key in result:
            raise ValueError(f"Duplicate reference identity: {key}")
        for field in ("species", "assembly", "annotation_release", "genome_size_method"):
            if not isinstance(ref.get(field), str) or not ref[field].strip():
                raise ValueError(f"Reference {key} requires {field}")
        if type(ref.get("genome_size")) is not int or ref["genome_size"] <= 0:
            raise ValueError("Specify the peak-calling genome size and its derivation")
        ref["fasta"] = asset(ref["fasta"], ref["fasta_sha256"], path.parent)
        ref["tss"] = asset(ref["tss"], ref["tss_sha256"], path.parent)
        for field in ("mitochondrial_contigs", "plastid_contigs"):
            if not isinstance(ref.get(field), list) or any(
                not isinstance(c, str) or not c or any(ch.isspace() for ch in c) for c in ref[field]
            ):
                raise ValueError(f"Reference {key} requires an explicit {field} list")
        mito, plastid = set(ref["mitochondrial_contigs"]), set(ref["plastid_contigs"])
        if mito & plastid:
            raise ValueError("A contig cannot be both mitochondrial and plastid")
        sizes: dict[str, int] = {}
        current = None
        with gzip.open(ref["fasta"], "rt") as stream:
            for line in stream:
                if line.startswith(">"):
                    current = line[1:].split()[0]
                    if current in sizes:
                        raise ValueError("Duplicate FASTA contig")
                    sizes[current] = 0
                elif current is not None:
                    sizes[current] += len(line.strip())
                elif line.strip():
                    raise ValueError("FASTA sequence precedes a header")
        if not sizes or any(n <= 0 for n in sizes.values()) or (mito | plastid) - sizes.keys():
            raise ValueError("FASTA contigs or organellar declarations are invalid")
        with Path(ref["tss"]).open() as stream:
            for line in stream:
                fields = line.rstrip().split("\t")
                if len(fields) != 3 or fields[0] not in sizes or fields[2] not in {"+", "-"}:
                    raise ValueError("TSS requires reference contig, BED0 position and strand")
                if not 0 <= int(fields[1]) < sizes[fields[0]]:
                    raise ValueError("TSS coordinate lies outside its reference contig")
        ref["total_bases"] = sum(sizes.values())
        result[key] = ref
    return result


def libraries(sheet: Path, refs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    lanes: set[tuple[str, str]] = set()
    with sheet.open(newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if set(reader.fieldnames or []) != FIELDS:
            raise ValueError("ATAC sample sheet columns must match the documented contract")
        for row in reader:
            if None in row or any(v is None or not v.strip() for v in row.values()):
                raise ValueError("Sample sheet has missing fields or extra columns")
            for field in (
                "library_id",
                "sample_id",
                "biological_replicate",
                "lane_id",
                "reference_id",
            ):
                identifier(row[field])
            key = row["library_id"]
            if row["reference_id"] not in refs:
                raise ValueError(f"Unknown reference for library {key}")
            if (key, row["lane_id"]) in lanes:
                raise ValueError(f"Duplicate lane in library {key}")
            lanes.add((key, row["lane_id"]))
            mapq = int(row["mapq"])
            if not 0 <= mapq <= 254 or row["duplicates"] not in {"retain", "exclude"}:
                raise ValueError("MAPQ must be 0–254 and duplicates must be retain or exclude")
            adapters = [row["adapter_r1"], row["adapter_r2"]]
            if not (adapters == ["-", "-"] or all(re.fullmatch(r"[ACGTN]+", a) for a in adapters)):
                raise ValueError(
                    "Specify both adapter sequences, or '-' for explicitly untrimmed reads"
                )
            meta = {
                k: row[k]
                for k in (
                    "library_id",
                    "sample_id",
                    "biological_replicate",
                    "reference_id",
                    "duplicates",
                    "adapter_r1",
                    "adapter_r2",
                )
            }
            meta["mapq"] = mapq
            if key in result and any(result[key][k] != v for k, v in meta.items()):
                raise ValueError(f"Inconsistent metadata across lanes of {key}")
            if key not in result:
                result[key] = {**meta, "reference": refs[row["reference_id"]], "lanes": []}
            lane = {"lane_id": row["lane_id"]}
            for mate in ("read1", "read2"):
                lane[mate] = asset(row[mate], row[mate + "_sha256"], sheet.parent, remote=True)
                lane[mate + "_sha256"] = row[mate + "_sha256"]
            if lane["read1"] == lane["read2"]:
                raise ValueError(f"Both mates use the same source in {key}")
            result[key]["lanes"].append(lane)
    if not result:
        raise ValueError("No ATAC libraries supplied")
    for lib in result.values():
        lib["lanes"].sort(key=lambda lane: lane["lane_id"])
    return [result[k] for k in sorted(result)]
