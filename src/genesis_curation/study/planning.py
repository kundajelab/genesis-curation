"""Deterministic campaign compilation from prepared study revisions."""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import re
from pathlib import Path
from typing import Any

from ..atac.inputs import digest, identifier
from ..contracts.records import (
    canonical,
    dump,
    fingerprint,
    identity,
    load,
    loads,
    record,
    validate,
)
from ..curation import review
from ..execution.transport import validate_worker
from ..registry import store
from ..samples import SAMPLE_HEADER
from .inputs import input_head, read_study

DEFAULT_SCAN = {
    "level": "metadata",
    "max_bytes": 1024**3,
    "max_records": 1_000_000,
    "max_seconds": 300,
}
WORKER_KEYS = {
    "transport",
    "host",
    "slots",
    "repo",
    "python",
    "root",
    "pixi",
    "run_root",
    "profile",
    "config",
    "path_map",
    "bigwig_python",
}


def validate_scan(scan: dict[str, Any]) -> None:
    if set(scan) != set(DEFAULT_SCAN) or scan["level"] not in {"metadata", "full"}:
        raise ValueError("Invalid study validation scan options")
    if any(type(scan[k]) is not int or scan[k] < 1 for k in ("max_bytes", "max_records")):
        raise ValueError("Study scans require positive finite byte/record limits")
    if type(scan["max_seconds"]) not in (int, float) or not 0 < scan["max_seconds"] <= 3600:
        raise ValueError("Study scan duration must be positive and at most 3600 seconds")


def absolute(value: str) -> Path:
    if (
        not isinstance(value, str)
        or not Path(value).is_absolute()
        or any(c in value for c in "\r\n\x00")
    ):
        raise ValueError("Execution paths must be absolute without control characters")
    if ".." in Path(value).parts or value == "/":
        raise ValueError("Execution paths must name an explicit directory or file")
    return Path(value)


def execution_config(value: dict[str, Any]) -> dict[str, Any]:
    if (
        set(value) - {"schema_version", "git_sha", "workers", "assignments"}
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("Expected a version 1 study execution configuration")
    if not re.fullmatch(r"[a-f0-9]{40}", value.get("git_sha", "")):
        raise ValueError("Execution configuration requires a full Git SHA")
    workers = value.get("workers")
    if not isinstance(workers, dict) or not 1 <= len(workers) <= 128:
        raise ValueError("Configure 1–128 explicit workers")
    for name, worker in workers.items():
        identifier(name)
        if not isinstance(worker, dict) or set(worker) - WORKER_KEYS:
            raise ValueError("Unknown worker configuration field")
        validate_worker(worker)
        if type(worker.get("slots")) is not int or not 1 <= worker["slots"] <= 128:
            raise ValueError("Worker slots must be an integer from 1 to 128")
        for key in ("repo", "python", "root", "pixi", "run_root"):
            absolute(worker.get(key, ""))
        if not re.fullmatch(r"[A-Za-z0-9_,.-]+", worker.get("profile", "")):
            raise ValueError("An explicit Nextflow profile is required")
        for key in ("config", "bigwig_python"):
            if worker.get(key):
                absolute(worker[key])
        mapping = worker.get("path_map", {})
        if not isinstance(mapping, dict):
            raise ValueError("path_map must map controller prefixes to worker prefixes")
        for source, destination in mapping.items():
            absolute(source)
            absolute(destination)
    assignments = value.get("assignments", {})
    if not isinstance(assignments, dict) or any(v not in workers for v in assignments.values()):
        raise ValueError("Assignments must name configured workers")
    return value


def mapped(path: str, worker: dict[str, Any]) -> str:
    if path.startswith(("https://", "http://")):
        return path
    value = absolute(path)
    for source in sorted(
        worker.get("path_map", {}), key=lambda s: len(Path(s).parts), reverse=True
    ):
        # Registered inputs are stored resolved, so also match the controller prefix resolved
        # (e.g. macOS /var is /private/var). Worker-side destinations are never resolved here.
        for prefix in dict.fromkeys((Path(source), Path(source).resolve())):
            if value.is_relative_to(prefix):
                return str(Path(worker["path_map"][source]) / value.relative_to(prefix))
    if worker["transport"] == "ssh":
        raise ValueError("SSH input path needs an explicit path_map entry: " + path)
    return str(value)


def text_document(name: str, text: str) -> dict[str, str]:
    if len(text.encode()) > 2 * 1024 * 1024:
        raise ValueError("Generated document exceeds 2 MiB")
    return {"name": name, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}


def sheet_text(rows: list[dict[str, str]], *, dap: bool = False) -> str:
    stream = io.StringIO()
    # Stored JSON sorts object keys. DAP's public six-column interface has a fixed order.
    writer = csv.DictWriter(
        stream,
        fieldnames=SAMPLE_HEADER if dap else sorted(rows[0]),
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def read_config(path: str) -> str:
    with Path(path).open("rb") as stream:
        content = stream.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise ValueError("Site configuration exceeds 2 MiB")
    return content.decode("utf-8")


def build_job(
    study: dict[str, Any],
    members: list[dict[str, Any]],
    metadata: dict[str, Any],
    worker_name: str,
    worker: dict[str, Any],
    generation: str,
    git_sha: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    names = sorted(i["data"]["manifest"]["data"]["name"] for i in members)
    job_id = "job-" + fingerprint(names)[:20]
    input_root = Path(worker["root"]) / "study-inputs" / generation / job_id
    out = Path(worker["run_root"]) / generation / job_id
    rows, assets, refs = [], {}, {}
    for item in members:
        for original in item["data"]["rows"]:
            row = dict(original)
            for key in ("read1", "read2") if study["assay"] == "bulk-ATAC" else ():
                row[key] = mapped(row[key], worker)
            rows.append(row)
        ref = dict(item["data"]["reference_record"])
        ref["fasta"] = mapped(ref["fasta"], worker)
        if study["assay"] == "bulk-ATAC":
            ref["tss"] = mapped(ref["tss"], worker)
        refs[ref["reference_id"]] = ref
        # Original sheet/registry are replaced by exact generated documents; large assets stay put.
        for asset in item["data"]["assets"]:
            if asset["path"] in {study["sheet"], study["references"]}:
                continue
            target = mapped(asset["path"], worker)
            if target in assets and assets[target] != asset["sha256"]:
                raise ValueError("Worker path mapping aliases different input bytes")
            assets[target] = asset["sha256"]
    docs = [
        text_document("samples.tsv", sheet_text(rows, dap=study["assay"] == "DAP-seq")),
        text_document(
            "metadata.json", canonical({i["id"]: metadata[i["id"]] for i in members}) + "\n"
        ),
    ]
    if study["assay"] == "bulk-ATAC":
        references = str(input_root / "references.json")
        docs.append(
            text_document(
                "references.json",
                canonical({"schema_version": 1, "references": list(refs.values())}) + "\n",
            )
        )
        argv = [
            worker["pixi"],
            "run",
            "pipeline-atac",
            str(input_root / "samples.tsv"),
            "--references",
            references,
            "--outdir",
            str(out),
            "--profile",
            worker["profile"],
            "--resume",
        ]
    else:
        parents = {str(Path(r["fasta"]).parent) for r in refs.values()}
        if len(parents) != 1:
            raise ValueError("DAP reference files must share one worker-local reference directory")
        references = parents.pop()
        argv = [
            worker["pixi"],
            "run",
            "pipeline",
            "-w",
            str(out.parent),
            "-n",
            out.name,
            "-p",
            worker["profile"],
            str(input_root / "samples.tsv"),
            "--references",
            references,
            "-resume",
        ]
    if worker.get("config"):
        content = read_config(worker["config"])
        docs.append(text_document("site.config", content))
        argv += [
            "--config" if study["assay"] == "bulk-ATAC" else "-c",
            str(input_root / "site.config"),
        ]
    for doc in docs:
        assets[str(input_root / doc["name"])] = doc["sha256"]
    contract = {
        "schema_version": 1,
        "study_id": study["study_id"],
        "source_id": study["source_id"],
        "assay": study["assay"],
        "worker": worker_name,
        "root": str(out),
        "sheet": str(input_root / "samples.tsv"),
        "references": references,
        "inputs": {i["id"]: i["version"] for i in members},
        "names": names,
        "metadata": {i["id"]: metadata[i["id"]] for i in members},
        "bigwig_python": worker.get("bigwig_python"),
    }
    payload = {
        "git_sha": git_sha,
        "argv": argv,
        "inputs": [{"path": p, "sha256": h} for p, h in sorted(assets.items())],
        "study": {
            "schema_version": 1,
            "input_root": str(input_root),
            "documents": docs,
            "contract": contract,
        },
    }
    return {"id": job_id, "worker": worker_name, "payload": payload}, contract


def build(
    directory: Path,
    configuration: dict[str, Any],
    scan: dict[str, Any] | None = None,
    *,
    verify_assets: bool = True,
) -> dict[str, Any]:
    study_record = read_study(directory)
    study = study_record["data"]
    config = execution_config(configuration)
    scan = dict(DEFAULT_SCAN if scan is None else scan)
    validate_scan(scan)
    blockers, items, metadata, tokens = [], [], {}, {}
    with contextlib.closing(store.connect(Path(study["registry"]), scope="inputs")) as db:
        db.execute("BEGIN")
        for dataset, version in study["members"].items():
            head = input_head(db, dataset)
            if head["version"] != version:
                blockers.append("Study inputs changed; run study init against the current sheet")
            item = store.get(db, "input_manifest", version)
            items.append(item)
            metadata[dataset] = loads(head["metadata"])
            tokens[dataset] = review.token(db, dataset)
            if (
                metadata[dataset] != item["data"]["manifest"]["data"]["metadata"]
                and review.effective(db, dataset, "metadata") != "APPROVED"
            ):
                blockers.append("Metadata edits need current input approval: " + dataset)
    assets = {a["path"]: a["sha256"] for i in items for a in i["data"]["assets"]}
    for path, expected in assets.items():
        if not Path(path).is_file() or (
            (verify_assets or path in {study["sheet"], study["references"]})
            and digest(Path(path)) != expected
        ):
            blockers.append("Prepared input changed or is missing: " + path)
    workers = config["workers"]
    assignments = config.get("assignments", {})
    names = {i["data"]["manifest"]["data"]["name"] for i in items}
    if set(assignments) - names:
        raise ValueError("Assignments contain unknown library names")
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        meta = item["data"]["manifest"]["data"]
        group = (
            meta["name"]
            if study["assay"] == "bulk-ATAC"
            else (
                meta["metadata"]["control"] if meta["metadata"]["control"] != "-" else meta["name"]
            )
        )
        groups.setdefault(group, []).append(item)
    generation = fingerprint(
        {
            "study": study_record["version"],
            "tokens": tokens,
            "execution": config,
            "configs": {
                n: hashlib.sha256(read_config(w["config"]).encode()).hexdigest()
                if w.get("config")
                else None
                for n, w in workers.items()
            },
        }
    )
    jobs, contracts = [], {}
    for members in groups.values():
        selected = {
            assignments.get(
                i["data"]["manifest"]["data"]["name"],
                next(iter(workers)) if len(workers) == 1 else None,
            )
            for i in members
        }
        if None in selected or len(selected) != 1:
            blockers.append("Assign every library/control in an analysis group to one worker")
            continue
        worker_name = next(iter(selected))
        if not isinstance(worker_name, str):
            raise ValueError("Missing worker assignment")
        try:
            job, contract = build_job(
                study,
                members,
                metadata,
                worker_name,
                workers[worker_name],
                generation,
                config["git_sha"],
            )
        except (ValueError, OSError) as error:
            blockers.append(str(error))
            continue
        contract["review_tokens"] = {i["id"]: tokens[i["id"]] for i in members}
        jobs.append(job)
        contracts[job["id"]] = contract
    campaign = {
        "schema_version": 1,
        "workers": {
            n: {
                k: v
                for k, v in w.items()
                if k in {"transport", "host", "slots", "repo", "python", "root"}
            }
            for n, w in workers.items()
        },
        "jobs": sorted(jobs, key=lambda j: j["id"]),
    }
    data = {
        "study_version": study_record["version"],
        "study_id": study["study_id"],
        "source_id": study["source_id"],
        "assay": study["assay"],
        "status": "BLOCKED" if blockers else "READY",
        "blockers": sorted(set(blockers)),
        "execution": config,
        "campaign": campaign,
        "jobs": contracts,
        "validation": scan,
    }
    return record("study_plan", data, identity("study-plan", fingerprint(data)), version=3)


def plan(
    directory: Path, execution: Path, output: Path, scan: dict[str, Any] | None = None
) -> dict[str, Any]:
    value = build(directory, load(execution), scan)
    dump(output, value, immutable=True)
    return value


def recheck(directory: Path, value: dict[str, Any]) -> None:
    validate(value, "study_plan")
    if value["data"]["status"] != "READY":
        raise ValueError("Study plan is blocked; inspect its blockers")
    actual = build(
        directory, value["data"]["execution"], value["data"]["validation"], verify_assets=False
    )
    if actual["version"] != value["version"]:
        raise ValueError("Study plan is stale; inspect current inputs/review and create a new plan")
