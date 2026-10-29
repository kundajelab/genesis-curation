"""Import Alex Borowsky's ordered annotation tables without inferring cell identity."""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, dump, fingerprint, load
from ..scatac.common import complete, digest, publication, verify_output
from .cells import cells

UPSTREAM = "https://github.com/alexborowsky/plant-celltype-annotation"
MAX_TABLE = 32 * 1024 * 1024


def table(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_TABLE + 1)
    if len(raw) > MAX_TABLE:
        raise ValueError("Table exceeds 32 MiB; split by library")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter="\t")
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or not required.issubset(fields):
        raise ValueError(f"Unique TSV headers required, including {sorted(required)}")
    rows = []
    for row in reader:
        if None in row or any(v is None for v in row.values()):
            raise ValueError("Malformed TSV row")
        if any(not row[k].strip() for k in required):
            raise ValueError("Required TSV values must not be empty")
        rows.append(dict(row))
    if not rows:
        raise ValueError("Empty table")
    return rows


def prepare(skill_repo: Path, h5ad: Path, context: Path, output: Path) -> dict[str, Any]:
    """Record a local, clean upstream checkout and an explicit RNA dataset handoff."""

    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(skill_repo), *args], text=True).strip()

    metadata = load(context)
    for key in ("dataset_id", "species", "tissue", "developmental_stage", "source", "assay"):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip():
            raise ValueError(f"Context requires {key}")
    if metadata["assay"] not in {"scRNA-seq", "snRNA-seq", "multiome-RNA"}:
        raise ValueError("This workflow requires RNA expression, not ATAC gene activity")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Use a clean upstream checkout for a reproducible handoff")
    origin = git("remote", "get-url", "origin").removesuffix(".git").rstrip("/")
    if origin not in {UPSTREAM, "git@github.com:alexborowsky/plant-celltype-annotation"}:
        raise ValueError("Expected Alex Borowsky upstream checkout")
    paths = ("SKILL.md", "VERSION", "references/file_formats.md", "scripts/apply_annotations.py")
    value = {
        "schema_version": 1,
        "kind": "plant-celltype-handoff",
        "context": metadata,
        "upstream": {
            "url": UPSTREAM,
            "revision": git("rev-parse", "HEAD"),
            "version": (skill_repo / "VERSION").read_text().strip(),
            "files": {name: digest(skill_repo / name) for name in paths},
        },
        "h5ad": {"path": str(h5ad.resolve()), "sha256": digest(h5ad)},
        "review_status": "UNREVIEWED",
    }
    value["version"] = fingerprint(value)
    dump(output, value, immutable=True)
    return value


def checked_handoff(path: Path) -> dict[str, Any]:
    value = load(path)
    if (
        value.get("kind") != "plant-celltype-handoff"
        or value.get("schema_version") != 1
        or value.get("version") != fingerprint({k: v for k, v in value.items() if k != "version"})
    ):
        raise ValueError("Invalid or changed handoff")
    if digest(Path(value["h5ad"]["path"])) != value["h5ad"]["sha256"]:
        raise ValueError("Handoff expression input changed")
    return value


def resolve_key(spec: str, columns: set[str]) -> str:
    if spec in columns:
        return spec
    try:
        float(spec)
    except ValueError:
        raise ValueError(f"Missing observation column: {spec}") from None
    candidates = [
        f"leiden_res{spec}",
        f"snn_res{spec}",
        f"SCT_snn_res.{spec}",
        f"RNA_snn_res.{spec}",
        f"integrated_snn_res.{spec}",
        f"louvain_res{spec}",
    ]
    found = [key for key in candidates if key in columns]
    if len(found) != 1:
        raise ValueError(f"Missing or ambiguous resolution {spec}; use an explicit column name")
    return found[0]


def propose(
    handoff: Path, annotations: Path, observations: Path, cell_file: Path, output: Path
) -> dict[str, Any]:
    """Apply first-match semantics to explicit library/barcode joins; emit proposals only."""
    started = time.monotonic()
    source = checked_handoff(handoff)
    rules = table(annotations, {"Resolution", "Cluster", "CellType", "Evidence"})
    obs = table(observations, {"library_id", "barcode"})
    if cell_file.stat().st_size > MAX_TABLE:
        raise ValueError("Cell input exceeds 32 MiB; split by library")
    with cell_file.open("rb") as stream:
        if stream.read(2) == b"\x1f\x8b":
            raise ValueError("Use uncompressed cell JSONL for bounded imports")
    original = list(cells(cell_file))
    indexed = {(c["library_id"], c["barcode"]): c for c in original}
    if not original or len(indexed) != len(original):
        raise ValueError("Cell records must be nonempty with unique library/barcode identities")
    observed: dict[tuple[str, str], dict[str, str]] = {}
    for row in obs:
        key = (row["library_id"], row["barcode"])
        if key in observed or key not in indexed:
            raise ValueError("Observation has duplicate or unknown library/barcode identity")
        if not indexed[key]["rna_present"]:
            raise ValueError("RNA annotations require explicitly matched RNA cells")
        observed[key] = row
    proposals: dict[tuple[str, str], dict[str, Any]] = {}
    rule_records = []
    columns = set(obs[0]) - {"library_id", "barcode"}
    for number, rule in enumerate(rules, 1):
        rule = {key: value.strip() for key, value in rule.items()}
        column = resolve_key(rule["Resolution"], columns)
        clusters = {c.strip() for c in rule["Cluster"].split(",")}
        if "" in clusters:
            raise ValueError("Empty cluster selector")
        matches = [key for key, row in observed.items() if row[column] in clusters]
        if not matches:
            raise ValueError(f"Annotation rule {number} matches no cells")
        new = [key for key in matches if key not in proposals]
        rule_records.append(
            {
                **rule,
                "rule_id": str(number),
                "obs_key": column,
                "matched": len(matches),
                "claimed": len(new),
                "shadowed": len(matches) - len(new),
            }
        )
        for key in new:
            proposals[key] = {
                "library_id": key[0],
                "barcode": key[1],
                "rule_id": str(number),
                "proposed_label": rule["CellType"],
                "evidence": rule["Evidence"],
                "review_status": "UNREVIEWED",
            }
    data = {
        "adapter_sha256": digest(Path(__file__)),
        "handoff": source,
        "rules": rule_records,
        "proposals": list(proposals.values()),
        "unmatched_cells": len(original) - len(proposals),
        "sources": {
            name: digest(path)
            for name, path in (
                ("annotations", annotations),
                ("observations", observations),
                ("cells", cell_file),
            )
        },
        "review_status": "UNREVIEWED",
    }
    if len(canonical(data).encode("utf-8")) > MAX_TABLE:
        raise ValueError("Proposal exceeds 32 MiB; split by library")
    with publication(output) as staging:
        dump(staging / "proposal.json", data)
        for name, path, target in (
            ("cells", cell_file, "cells.jsonl"),
            ("annotations", annotations, "annotations.tsv"),
            ("observations", observations, "observations.tsv"),
        ):
            shutil.copyfile(path, staging / target)
            if digest(staging / target) != data["sources"][name]:
                raise ValueError("Input changed during import")
        version = fingerprint(data)
        with (staging / "review.tsv").open("w") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
            writer.writerow(["proposal_version", "rule_id", "decision", "reviewer", "reason"])
            for rule in rule_records:
                writer.writerow([version, rule["rule_id"], "PENDING", "", ""])
        return complete(staging, "celltype-proposals", {"proposal_version": version}, started)


def export(directory: Path, decisions: Path, output: Path) -> dict[str, Any]:
    """Export reviewed labels; inclusion still requires the original explicit QC selection."""
    started = time.monotonic()
    manifest = verify_output(directory, "celltype-proposals")
    proposal = load(directory / "proposal.json")
    version = fingerprint(proposal)
    if version != manifest["data"]["proposal_version"]:
        raise ValueError("Proposal identity mismatch")
    reviews = table(decisions, {"proposal_version", "rule_id", "decision"})
    rules = {r["rule_id"]: r for r in proposal["rules"]}
    reviewed = {}
    for row in reviews:
        rule_id = row["rule_id"]
        if row["proposal_version"] != version or rule_id not in rules or rule_id in reviewed:
            raise ValueError("Stale, duplicate or unknown review decision")
        if row["decision"] not in {"APPROVED", "REJECTED", "PENDING"}:
            raise ValueError("Invalid review decision")
        if row["decision"] != "PENDING" and (
            not row.get("reviewer", "").strip() or not row.get("reason", "").strip()
        ):
            raise ValueError("Decisions require a named reviewer and reason")
        reviewed[rule_id] = row
    candidates = {(r["library_id"], r["barcode"]): r for r in proposal["proposals"]}
    result = []
    approved = 0
    for original in cells(directory / "cells.jsonl"):
        cell = dict(original)
        candidate = candidates.get((cell["library_id"], cell["barcode"]))
        decision = reviewed.get(candidate["rule_id"], {}) if candidate else {}
        label = candidate["proposed_label"] if candidate else "Unknown"
        accepted = decision.get("decision") == "APPROVED" and label.casefold() not in {
            "unknown",
            "unspecified",
            "unassigned",
        }
        if accepted and candidate is not None:
            cell.update(
                harmonized_label=label,
                annotation_status="APPROVED",
                ontology_id=None,
                annotation_source=f"plant-celltype:{version}:rule:{candidate['rule_id']}",
            )
            approved += 1
        else:
            # Retain previous labels for audit, but do not select unreviewed/unknown cells.
            cell.update(atac_include=False, rna_include=False)
        result.append(cell)
    with publication(output) as staging:
        (staging / "cells.jsonl").write_text("".join(canonical(c) + "\n" for c in result))
        dump(staging / "review.json", {"proposal": proposal, "decisions": reviews})
        return complete(
            staging,
            "celltype-reviewed",
            {
                "parent_version": manifest["version"],
                "proposal_version": version,
                "approved_cells": approved,
                "unapproved_or_unknown_cells": len(result) - approved,
                "selected_atac_cells": sum(c["atac_include"] for c in result),
                "selected_rna_cells": sum(c["rna_include"] for c in result),
                "adapter_sha256": digest(Path(__file__)),
                "decisions_sha256": digest(decisions),
            },
            started,
        )
