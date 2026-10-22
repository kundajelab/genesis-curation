"""Explicit reference bundles for benchmark experiments, separate from run policy."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

from .spec import SHA, identifier, identity, keys, sha256, write_json


def text_file(path: Path) -> TextIO:
    return gzip.open(path, "rt") if path.suffix == ".gz" else path.open()


def inspect_fasta(path: Path) -> dict[str, Any]:
    """Scan every base, retaining only per-contig counts and case-insensitive hashes."""
    contigs: dict[str, dict[str, Any]] = {}
    name = ""
    sequence_hash = hashlib.sha256()
    content_hash = hashlib.sha256()
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        while line := stream.readline(10_000_001):
            if len(line) > 10_000_000:
                raise ValueError("FASTA line exceeds the 10 MB scan limit")
            content_hash.update(line)
            if line.startswith(b">"):
                if name:
                    contigs[name]["sequence_sha256"] = sequence_hash.hexdigest()
                header = line[1:].split()
                if not header:
                    raise ValueError("Empty FASTA identifier")
                name = header[0].decode()
                if name in contigs:
                    raise ValueError("Duplicate FASTA contig: " + name)
                contigs[name] = {"length": 0, "acgt": 0, "softmasked": 0}
                sequence_hash = hashlib.sha256()
            else:
                seq = line.strip()
                upper = seq.upper()
                if not name or not seq or set(upper) - set(b"ACGTURYSWKMBDHVN"):
                    raise ValueError("Invalid FASTA sequence/header order")
                sequence_hash.update(upper)
                contigs[name]["length"] += len(seq)
                contigs[name]["acgt"] += sum(upper.count(c) for c in (b"A", b"C", b"G", b"T"))
                contigs[name]["softmasked"] += sum(seq.count(c) for c in (b"a", b"c", b"g", b"t"))
    if name:
        contigs[name]["sequence_sha256"] = sequence_hash.hexdigest()
    if not contigs or any(not c["length"] for c in contigs.values()):
        raise ValueError("Empty FASTA or contig")
    return {
        "content_sha256": content_hash.hexdigest(),
        "sequence_set_sha256": identity(
            {n: (c["length"], c["sequence_sha256"]) for n, c in contigs.items()}
        ),
        "sequence_multiset_sha256": identity(
            sorted((c["length"], c["sequence_sha256"]) for c in contigs.values())
        ),
        "contigs": contigs,
        "scan": "FULL",
    }


def annotation_tss(
    path: Path,
    contigs: dict[str, Any],
    organelles: set[str],
    aliases: dict[str, str],
    mode: str = "transcript_features",
    transcript_types: list[str] | None = None,
) -> tuple[list[tuple[str, int, str]], list[tuple[str, str, str, int, str]]]:
    """Read transcript TSSs from GTF/GFF3; use BED0 positions and preserve gene IDs."""
    positions: set[tuple[str, int, str]] = set()
    genes: set[tuple[str, str, str, int, str]] = set()
    exons: dict[tuple[str, str], tuple[str, str, str, int, int]] = {}
    if mode not in {"transcript_features", "exon_bounds"}:
        raise ValueError("Unknown annotation TSS mode")
    allowed_types = {
        "transcript",
        "mRNA",
        "tRNA",
        "ncRNA",
        "miRNA",
        "snoRNA",
        "rRNA",
        "snRNA",
        "lnc_RNA",
        "pseudogenic_transcript",
    }
    if transcript_types is not None and (
        not isinstance(transcript_types, list)
        or not transcript_types
        or any(not isinstance(t, str) or t not in allowed_types for t in transcript_types)
        or len(set(transcript_types)) != len(transcript_types)
        or mode != "transcript_features"
    ):
        raise ValueError(
            "Specify unique supported transcript feature types in transcript_features mode"
        )
    types = (
        set(transcript_types or ["transcript", "mRNA"])
        if mode == "transcript_features"
        else {"exon"}
    )
    with text_file(path) as stream:
        for number, line in enumerate(stream, 1):
            if line.startswith("##FASTA"):
                break
            if line.startswith("#") or not line.strip():
                continue
            fields = line.rstrip().split("\t")
            if len(fields) != 9:
                raise ValueError(f"Annotation line {number}: expected nine fields")
            chrom = aliases.get(fields[0], fields[0])
            if chrom not in contigs:
                raise ValueError(f"Annotation contig absent from FASTA: {chrom}")
            start, end = int(fields[3]), int(fields[4])
            if not 1 <= start <= end <= contigs[chrom]["length"]:
                raise ValueError(f"Annotation line {number}: coordinate outside contig")
            if fields[2] not in types or chrom in organelles:
                continue
            if fields[6] not in {"+", "-"}:
                raise ValueError("Transcript requires a known strand")
            if "=" in fields[8] and not re.search(r'\btranscript_id\s+"', fields[8]):
                attrs = dict(x.split("=", 1) for x in fields[8].split(";") if "=" in x)
                transcript, gene = attrs.get("ID", ""), attrs.get("Parent", "")
            else:
                attrs = dict(re.findall(r'(\w+)\s+"([^"]*)"', fields[8]))
                transcript, gene = attrs.get("transcript_id", ""), attrs.get("gene_id", "")
            if mode == "exon_bounds":
                if not gene or not transcript:
                    raise ValueError(
                        "Exon TSS derivation requires explicit gene and transcript IDs"
                    )
                key = (transcript, attrs.get("extra_copy_number", "0"))
                previous = exons.get(key)
                if previous:
                    if previous[:3] != (gene, chrom, fields[6]):
                        raise ValueError("Transcript exons have conflicting gene, contig or strand")
                    start, end = min(start, previous[3]), max(end, previous[4])
                exons[key] = (gene, chrom, fields[6], start, end)
                continue
            position = start - 1 if fields[6] == "+" else end - 1
            positions.add((chrom, position, fields[6]))
            if gene and transcript:
                genes.add((gene, transcript, chrom, position, fields[6]))
    for (transcript, copy_number), (gene, chrom, strand, start, end) in exons.items():
        position = start - 1 if strand == "+" else end - 1
        positions.add((chrom, position, strand))
        genes.add(
            (
                gene,
                transcript if copy_number == "0" else transcript + "@copy=" + copy_number,
                chrom,
                position,
                strand,
            )
        )
    if not positions:
        raise ValueError("Annotation contains no eligible nuclear transcript TSSs")
    return sorted(positions), sorted(genes)


def prepare_reference(panel: Path, reference_id: str, out: Path) -> dict[str, Any]:
    document = json.loads(panel.read_text())
    keys(
        document,
        {"schema_version", "references"},
        {"schema_version", "references"},
        "reference panel",
    )
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("Unsupported reference-panel version")
    if not isinstance(document["references"], list):
        raise ValueError("References must be a list")
    refs = {}
    for item in document["references"]:
        if not isinstance(item, dict) or "id" not in item:
            raise ValueError("Each reference must be an object with an ID")
        identifier(item["id"])
        if item["id"] in refs:
            raise ValueError("Duplicate reference ID")
        refs[item["id"]] = item
    if reference_id not in refs:
        raise ValueError("Unknown reference: " + reference_id)
    entry = refs[reference_id]
    required = {
        "id",
        "species",
        "cultivar",
        "assembly",
        "assembly_accession",
        "fasta",
        "mitochondrial",
        "plastid",
        "source",
    }
    allowed = required | {
        "annotation",
        "annotation_release",
        "annotation_contig_aliases",
        "annotation_tss_mode",
        "annotation_transcript_types",
        "status",
        "reason",
    }
    keys(entry, allowed, required, "reference")
    if entry.get("status", "READY") != "READY":
        raise ValueError("Reference unavailable: " + str(entry.get("reason", "not supplied")))
    for field in ("species", "cultivar", "assembly", "assembly_accession"):
        if not isinstance(entry[field], str) or not entry[field]:
            raise ValueError("Reference requires an explicit " + field)
    if not isinstance(entry["source"], dict) or not entry["source"]:
        raise ValueError("Reference requires source evidence")
    if "annotation" in entry and (
        not isinstance(entry.get("annotation_release"), str) or not entry["annotation_release"]
    ):
        raise ValueError("Annotation requires an explicit release")
    for field in ("mitochondrial", "plastid"):
        if not isinstance(entry[field], list) or any(not isinstance(x, str) for x in entry[field]):
            raise ValueError("Organelles must be explicit contig-name lists")
    assets = {}
    paths = {}
    for role in ("fasta", "annotation"):
        if role not in entry:
            continue
        asset = entry[role]
        keys(asset, {"path", "sha256", "url"}, {"path", "sha256", "url"}, role)
        if (
            not isinstance(asset["path"], str)
            or not asset["path"]
            or not isinstance(asset["url"], str)
        ):
            raise ValueError("Reference asset requires a path and public source URL")
        url = urlsplit(asset["url"])
        if (
            url.scheme not in {"https", "http", "ftp"}
            or not url.hostname
            or url.username
            or url.password
        ):
            raise ValueError("Reference source must be an explicit public URL without credentials")
        if not isinstance(asset["sha256"], str) or not SHA.fullmatch(asset["sha256"]):
            raise ValueError("Reference asset requires a SHA256")
        path = (panel.parent / asset["path"]).resolve()
        if sha256(path) != asset["sha256"]:
            raise ValueError("Reference asset changed: " + role)
        paths[role] = path
        assets[reference_id + "-" + role] = {
            "path": str(path),
            "sha256": asset["sha256"],
            "bytes": path.stat().st_size,
        }
    scan = inspect_fasta(paths["fasta"])
    organelles = set(entry["mitochondrial"] + entry["plastid"])
    if not organelles <= scan["contigs"].keys():
        raise ValueError("Declared organelles are absent")
    if set(entry["mitochondrial"]) & set(entry["plastid"]):
        raise ValueError("A contig cannot be both mitochondrial and plastid")
    out.mkdir(parents=True, exist_ok=True)
    receipt = out / "reference.json"
    fingerprint = identity(entry)
    if receipt.exists():
        previous = json.loads(receipt.read_text())
        if previous["entry_identity"] != fingerprint:
            raise ValueError("Reference preparation changed; choose a new output directory")
        for a in previous["assets"].values():
            if sha256(Path(a["path"])) != a["sha256"]:
                raise ValueError("Prepared reference artifact changed")
        return previous
    if any(out.iterdir()):
        raise ValueError("Reference output directory is not empty")
    sizes = out / "chrom.sizes"
    sizes.write_text(
        "".join(f"{name}\t{value['length']}\n" for name, value in scan["contigs"].items())
    )
    size_id = reference_id + "-sizes"
    assets[size_id] = {
        "path": str(sizes.resolve()),
        "sha256": sha256(sizes),
        "bytes": sizes.stat().st_size,
    }
    reference = {
        k: entry[k] for k in ("id", "species", "cultivar", "assembly", "mitochondrial", "plastid")
    }
    reference.update(
        fasta=reference_id + "-fasta",
        fasta_content_sha256=scan["content_sha256"],
        chrom_sizes=size_id,
        genome_size=sum(c["length"] for c in scan["contigs"].values()),
    )
    if "annotation" in paths:
        aliases = entry.get("annotation_contig_aliases", {})
        if not isinstance(aliases, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in aliases.items()
        ):
            raise ValueError("Annotation aliases must map explicit contig names")
        positions, genes = annotation_tss(
            paths["annotation"],
            scan["contigs"],
            organelles,
            aliases,
            entry.get("annotation_tss_mode", "transcript_features"),
            entry.get("annotation_transcript_types"),
        )
        tss = out / "tss.bed"
        tss.write_text("".join(f"{c}\t{p}\t{s}\n" for c, p, s in positions))
        gene_tss = out / "tss-genes.tsv"
        gene_tss.write_text(
            "gene\ttranscript\tcontig\tposition_bed0\tstrand\n"
            + "".join("\t".join(map(str, row)) + "\n" for row in genes)
        )
        tss_id = reference_id + "-tss"
        assets[tss_id] = {
            "path": str(tss.resolve()),
            "sha256": sha256(tss),
            "bytes": tss.stat().st_size,
        }
        assets[reference_id + "-tss-genes"] = {
            "path": str(gene_tss.resolve()),
            "sha256": sha256(gene_tss),
            "bytes": gene_tss.stat().st_size,
        }
        reference.update(
            tss=tss_id,
            gtf=reference_id + "-annotation",
            annotation_release=entry.get("annotation_release", "UNSPECIFIED"),
        )
    result = {
        "schema_version": 1,
        "entry_identity": fingerprint,
        "reference": reference,
        "assets": assets,
        "scan": scan,
        "nuclear_acgt": sum(c["acgt"] for n, c in scan["contigs"].items() if n not in organelles),
        "source": entry["source"],
        "assembly_accession": entry["assembly_accession"],
        "tss_definition": (
            "unique nuclear transcript start positions; GFF/GTF 1-based closed to BED0; "
            "plus=start-1, minus=end-1"
        ),
        "tss_mode": entry.get("annotation_tss_mode", "transcript_features"),
        "transcript_feature_types": (
            entry.get("annotation_transcript_types", ["transcript", "mRNA"])
            if entry.get("annotation_tss_mode", "transcript_features") == "transcript_features"
            else None
        ),
    }
    write_json(receipt, result)
    return result
