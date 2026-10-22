"""Replicate-preserving pseudobulks with disk-backed cell and fragment joins."""

from __future__ import annotations

import sqlite3
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pysam
from jsonschema import Draft202012Validator

from ..contracts.records import canonical, dump, fingerprint, load, loads
from .common import bounded_lines, complete, digest, publication, software, text_file, verify_output

BIO_FIELDS = (
    "study_id",
    "biological_sample_id",
    "biological_replicate_id",
    "species",
    "assembly",
    "reference_id",
    "reference_fasta_sha256",
    "annotation_sha256",
    "accession_cultivar_ecotype",
    "tissue",
    "developmental_stage",
    "treatment",
    "condition",
    "assay",
    "protocol",
    "coordinate_offsets",
)


def cuts(start: int, end: int, offsets: list[int]) -> tuple[int, int]:
    """Existing contract: ChromBPNet +4/-4 interval convention, then end-1."""
    return start + 4 - offsets[0], end - 4 - offsets[1] - 1


def cells(path: Path) -> Iterator[dict[str, Any]]:
    validator = Draft202012Validator(load(Path(__file__).parent / "assets/cell-v1.json"))
    with text_file(path) as stream:
        for number, line in enumerate(bounded_lines(stream), 1):
            if len(line) > 1024 * 1024:
                raise ValueError(f"Cell record too large at line {number}")
            cell = loads(line)
            errors = list(validator.iter_errors(cell))
            if errors:
                raise ValueError(f"Invalid cell contract at line {number}: {errors[0].message}")
            for key in (
                "library_id",
                "barcode",
                "author_original_label",
                "harmonized_label",
                "annotation_source",
                "annotation_status",
            ):
                if not isinstance(cell.get(key), str):
                    raise ValueError(f"Cell field {key} requires a string")
            if (
                type(cell.get("atac_include")) is not bool
                or type(cell.get("atac_present")) is not bool
            ):
                raise ValueError("Explicit Boolean ATAC inclusion/presence is required")
            if cell["atac_include"]:
                if not cell["atac_present"] or any(
                    not cell[k].strip() or cell[k] in {"UNKNOWN", "UNSPECIFIED"}
                    for k in ("harmonized_label", "annotation_source")
                ):
                    raise ValueError("Included cells require present ATAC and source-backed labels")
            yield cell


def task(library: dict[str, Any], cell: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    for key in (
        "study_id",
        "biological_sample_id",
        "biological_replicate_id",
        "assembly",
        "reference_id",
    ):
        if not library.get(key) or library[key] in {"UNKNOWN", "UNSPECIFIED"}:
            raise ValueError("Explicit biological/reference identity is required: " + key)
    value = {k: library[k] for k in BIO_FIELDS}
    merge = library["technical_merge_group"]
    value.update(
        cell_type=cell["harmonized_label"],
        library_scope={"merge": merge} if merge else {"library": library["library_id"]},
    )
    return "genesis-task-v1-" + fingerprint(value), value


def aggregate(
    inputs: list[Path],
    annotations: Path,
    policy_path: Path,
    output: Path,
    *,
    resume: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    policy = load(policy_path)
    if policy.get("schema_version") != 1 or not policy.get("version"):
        raise ValueError("A versioned explicit cell/fragment policy is required")
    if policy.get("signal_convention") != "chrombpnet-atac-v1":
        raise ValueError("Select the existing tested chrombpnet-atac-v1 cut convention")
    if policy.get("organellar") not in {"retain", "exclude"}:
        raise ValueError("An explicit organellar policy is required")
    if policy.get("boundary_cuts") not in {"error", "exclude-and-report"}:
        raise ValueError("An explicit boundary-cut policy is required")
    approved_merges = policy.get("independent_library_merges", [])
    libraries: dict[str, dict[str, Any]] = {}
    versions = {}
    merges = {}
    merge_columns = {}
    for directory in inputs:
        value = verify_output(directory, "scatac-fragments")
        data = value["data"]["input"]
        library, ref = data["library"], data["reference"]
        lid = library["library_id"]
        if lid in libraries:
            raise ValueError("Duplicate library input")
        if (
            library["reference_fasta_sha256"],
            library["reference_id"],
            library["assembly"],
            library["species"],
            library["coordinate_offsets"],
        ) != (
            ref["fasta"]["sha256"],
            ref["reference_id"],
            ref["assembly"],
            ref["species"],
            data["producer"]["offsets"],
        ):
            raise ValueError("Library identity contradicts the ingested reference/coordinates")
        versions[lid] = value["version"]
        libraries[lid] = {"library": library, "reference": ref, "directory": directory}
        merge = library["technical_merge_group"]
        if merge:
            if merge not in approved_merges:
                raise ValueError(
                    "Technical merging requires an explicit independent-library declaration"
                )
            columns = value["data"]["columns"]
            if merge in merge_columns and merge_columns[merge] != columns:
                raise ValueError("Technical merging requires consistent fragment columns")
            merge_columns[merge] = columns
            biological = {k: library[k] for k in BIO_FIELDS}
            merge_key = (library["study_id"], merge)
            if merge_key in merges and merges[merge_key] != biological:
                raise ValueError("Technical merge crosses biological/reference identity")
            merges[merge_key] = biological
    if not libraries:
        raise ValueError("At least one ingested library is required")
    signature = fingerprint(
        {
            "parents": versions,
            "annotations": digest(annotations),
            "policy": policy,
            "software": software(),
        }
    )
    if output.exists() and resume:
        previous = verify_output(output, "scatac-pseudobulk-core")
        if previous["data"]["signature"] != signature:
            raise ValueError("Inputs/annotations/policy/software changed; use a new output")
        return {"cached": True, "manifest": previous, "output": str(output)}
    counts: Counter[str] = Counter()
    groups: dict[str, dict[str, Any]] = {}
    with publication(output) as stage:
        database = stage / "aggregation.sqlite"
        db = sqlite3.connect(database)
        try:
            db.execute("PRAGMA cache_size=-32768")
            db.execute("PRAGMA temp_store=FILE")
            db.execute(
                "CREATE TABLE cells (library TEXT,barcode TEXT,cell_id TEXT UNIQUE,"
                "task TEXT,body TEXT,PRIMARY KEY(library,barcode))"
            )
            db.execute(
                "CREATE TABLE fragments (task TEXT,rank INT,chrom TEXT,start INT,end INT,"
                "cell_id TEXT,support INT,strand TEXT)"
            )
            db.execute(
                "CREATE TABLE cuts (task TEXT,rank INT,chrom TEXT,pos INT,n INT,"
                "PRIMARY KEY(task,rank,pos)) WITHOUT ROWID"
            )
            for cell in cells(annotations):
                lid = cell["library_id"]
                if lid not in libraries:
                    raise ValueError("Cell annotation refers to unknown library")
                group_id = None
                if cell["atac_include"]:
                    group_id, biological = task(libraries[lid]["library"], cell)
                    groups.setdefault(
                        group_id,
                        {
                            "identity": biological,
                            "reference": libraries[lid]["reference"],
                            "cell_count": 0,
                            "library_composition": {},
                            "unique_fragments": 0,
                            "total_support": 0,
                            "cut_count": 0,
                            "excluded_boundary_cuts": 0,
                        },
                    )
                    group = groups[group_id]
                    group["cell_count"] += 1
                    group["library_composition"][lid] = group["library_composition"].get(lid, 0) + 1
                cell_id = "cell-" + fingerprint([lid, cell["barcode"]])
                try:
                    db.execute(
                        "INSERT INTO cells VALUES(?,?,?,?,?)",
                        (lid, cell["barcode"], cell_id, group_id, canonical(cell)),
                    )
                except sqlite3.IntegrityError:
                    raise ValueError(
                        "Duplicate cell annotation or cell identity collision"
                    ) from None
            db.commit()
            for lid, information in libraries.items():
                sizes = information["reference"]["contigs"]
                ranks = {chrom: i for i, chrom in enumerate(sizes)}
                organellar = set(
                    information["reference"]["mitochondrial_contigs"]
                    + information["reference"]["plastid_contigs"]
                )
                with text_file(information["directory"] / "fragments.tsv.gz") as stream:
                    for line in stream:
                        fields = line.rstrip("\n").split("\t")
                        chrom, start_text, end_text, barcode, support_text = fields[:5]
                        start, end, support = int(start_text), int(end_text), int(support_text)
                        counts["input_fragments"] += 1
                        counts["input_support"] += support
                        selected = db.execute(
                            "SELECT cell_id,task FROM cells WHERE library=? AND barcode=?",
                            (lid, barcode),
                        ).fetchone()
                        reason = (
                            "unannotated"
                            if selected is None
                            else "cell_policy"
                            if selected[1] is None
                            else "organellar"
                            if chrom in organellar and policy["organellar"] == "exclude"
                            else None
                        )
                        if reason:
                            counts["excluded_" + reason] += 1
                            counts["excluded_" + reason + "_support"] += support
                            continue
                        assert selected is not None
                        cell_id, group_id = selected
                        group = groups[group_id]
                        db.execute(
                            "INSERT INTO fragments VALUES(?,?,?,?,?,?,?,?)",
                            (
                                group_id,
                                ranks[chrom],
                                chrom,
                                start,
                                end,
                                cell_id,
                                support,
                                fields[5] if len(fields) == 6 else "",
                            ),
                        )
                        group["unique_fragments"] += 1
                        group["total_support"] += support
                        counts["selected_fragments"] += 1
                        counts["selected_support"] += support
                        for pos in cuts(start, end, information["library"]["coordinate_offsets"]):
                            if not 0 <= pos < sizes[chrom]:
                                if policy["boundary_cuts"] == "error":
                                    raise ValueError("Target cut falls outside the reference")
                                group["excluded_boundary_cuts"] += 1
                                continue
                            db.execute(
                                "INSERT INTO cuts VALUES(?,?,?,?,1) ON CONFLICT(task,rank,pos) "
                                "DO UPDATE SET n=n+1",
                                (group_id, ranks[chrom], chrom, pos),
                            )
                            group["cut_count"] += 1
                        if counts["input_fragments"] % 10000 == 0:
                            db.commit()
                db.commit()
            db.execute("CREATE INDEX grouped_fragments ON fragments(task,rank,start,end,cell_id)")
            db.execute("CREATE INDEX grouped_cells ON cells(task,library,barcode)")
            for group_id, group in sorted(groups.items()):
                destination = stage / group_id
                destination.mkdir()
                sizes = group["reference"]["contigs"]
                (destination / "chrom.sizes").write_text(
                    "".join(f"{c}\t{n}\n" for c, n in sizes.items())
                )
                gz = destination / "fragments.tsv.gz"
                with (
                    pysam.BGZFile(str(gz), "wb", index=None) as stream,
                    (destination / "peaks-input.bedpe").open("w") as bed,
                ):
                    for chrom, start, end, cell_id, support, strand in db.execute(
                        "SELECT chrom,start,end,cell_id,support,strand FROM fragments "
                        "WHERE task=? ORDER BY rank,start,end,cell_id",
                        (group_id,),
                    ):
                        stream.write(
                            (
                                f"{chrom}\t{start}\t{end}\t{cell_id}\t{support}"
                                + ("\t" + strand if strand else "")
                                + "\n"
                            ).encode()
                        )
                        bed.write(f"{chrom}\t{start}\t{end}\n")
                if group["unique_fragments"]:
                    pysam.tabix_index(str(gz), preset="bed", csi=max(sizes.values()) >= 2**29)
                with (destination / "cells.jsonl").open("w") as stream:
                    for cell_id, body in db.execute(
                        "SELECT cell_id,body FROM cells WHERE task=? ORDER BY library,barcode",
                        (group_id,),
                    ):
                        stream.write(canonical({"cell_id": cell_id, "source": loads(body)}) + "\n")
                with (destination / "cuts.bedGraph").open("w") as stream:
                    for chrom, pos, n in db.execute(
                        "SELECT chrom,pos,n FROM cuts WHERE task=? ORDER BY rank,pos", (group_id,)
                    ):
                        stream.write(f"{chrom}\t{pos}\t{pos + 1}\t{n}\n")
                group["status"] = (
                    "EMPTY" if not group["unique_fragments"] else "AWAITING_TRACKS_AND_PEAKS"
                )
                group["plant_thresholds"] = "UNSPECIFIED"
                group["signal"] = {
                    "kind": "raw_counts",
                    "unit": "cut",
                    "normalization": "none",
                    "strand": "both",
                    "derivation": "chrombpnet-atac-v1; two endpoints per row, not per support",
                }
                dump(destination / "metadata.json", group)
                dump(destination / "policy.json", policy)
                complete(
                    destination,
                    "scatac-group-core",
                    {
                        "group": group,
                        "policy": policy,
                        "parents": versions,
                        "annotations_sha256": digest(annotations),
                    },
                    started,
                )
        finally:
            db.close()
        database.unlink()
        dump(stage / "policy.json", policy)
        value = complete(
            stage,
            "scatac-pseudobulk-core",
            {
                "signature": signature,
                "parents": versions,
                "annotations_sha256": digest(annotations),
                "policy": policy,
                "counts": dict(counts),
                "groups": groups,
                "model_ready": False,
            },
            started,
        )
    return {"cached": False, "manifest": value, "output": str(output)}
