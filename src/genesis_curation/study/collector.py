"""Bounded, owned result collection over the existing authenticated worker transport."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from ..contracts.adapters import run_manifests
from ..contracts.records import canonical, dump, fingerprint, identity, load, record, validate
from ..contracts.validation import Budget, bundle
from ..curation.harmonize import source_record
from ..execution import worker

MAX_RECEIPT = 32 * 1024 * 1024


def checked_request(folder: Path, request: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    required = {
        "plan",
        "campaign",
        "job",
        "attempt",
        "worker",
        "payload_hash",
        "scan",
        "generation",
        "reader",
    }
    if set(request) != required:
        raise ValueError("Malformed collection request")
    for key in ("plan", "campaign", "payload_hash"):
        if not isinstance(request[key], str) or not re.fullmatch(r"[a-f0-9]{64}", request[key]):
            raise ValueError("Invalid collection content identity")
    if (
        type(request["attempt"]) is not int
        or request["attempt"] < 1
        or type(request["generation"]) is not int
        or not 0 <= request["generation"] <= 100
    ):
        raise ValueError("Invalid collection attempt/generation")
    if not isinstance(request["scan"], dict) or set(request["scan"]) != {
        "level",
        "max_bytes",
        "max_records",
        "max_seconds",
    }:
        raise ValueError("Malformed validation budget")
    if request["reader"] is not None:
        from .planning import absolute

        absolute(request["reader"])
    scan = request["scan"]
    if (
        scan["level"] not in {"metadata", "full"}
        or any(type(scan[k]) is not int or scan[k] < 1 for k in ("max_bytes", "max_records"))
        or type(scan["max_seconds"]) not in (int, float)
        or not 0 < scan["max_seconds"] <= 3600
    ):
        raise ValueError("Collection requires finite positive scan budgets")
    if not re.fullmatch(
        r"[a-f0-9]{16}-" + re.escape(request["job"]) + "-" + str(request["attempt"]), folder.name
    ):
        raise ValueError("Collection folder does not match the requested attempt")
    payload = load(folder / "payload.json")
    if fingerprint(payload) != request["payload_hash"] or "study" not in payload:
        raise ValueError("Collection targets a different execution payload")
    contract = payload["study"]["contract"]
    if contract["worker"] != request["worker"]:
        raise ValueError("Collection worker identity mismatch")
    if worker.inspect(folder).get("state") != "SUCCEEDED":
        raise ValueError("Collection requires a confirmed successful analysis")
    target = folder / "collections" / fingerprint(request)
    if target.resolve() != folder.resolve() / "collections" / fingerprint(request):
        raise ValueError("Collection namespace contains a symlink")
    return payload, target


def start(folder: Path, request: dict[str, Any]) -> dict[str, Any]:
    payload, target = checked_request(folder, request)
    documents = {
        str(Path(payload["study"]["input_root"]) / d["name"]) for d in payload["study"]["documents"]
    }
    # Raw FASTQs can have been pruned after processing. Verify compiled documents, not raw reads.
    collection_payload = {
        "repo": payload["repo"],
        "python": payload["python"],
        "git_sha": payload["git_sha"],
        "inputs": [i for i in payload["inputs"] if i["path"] in documents],
        "argv": [
            payload["python"],
            "-m",
            "genesis_curation.study.collector",
            "execute",
            str(target),
        ],
        "collection": {"source": str(folder), "request": request},
    }
    return worker.launch(target, collection_payload)


def status(folder: Path, request: dict[str, Any]) -> dict[str, Any]:
    _, target = checked_request(folder, request)
    result = worker.inspect(target)
    receipt = target / "receipt.json"
    if result.get("state") == "SUCCEEDED":
        if not receipt.is_file() or receipt.stat().st_size > MAX_RECEIPT:
            raise ValueError("Missing or oversized collection receipt")
        content = receipt.read_bytes()
        result.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    return result


def read(folder: Path, request: dict[str, Any]) -> dict[str, Any]:
    if (
        set(request) != {"request", "offset"}
        or type(request["offset"]) is not int
        or not 0 <= request["offset"] <= MAX_RECEIPT
    ):
        raise ValueError("Invalid receipt chunk request")
    _, target = checked_request(folder, request["request"])
    if worker.inspect(target).get("state") != "SUCCEEDED":
        raise ValueError("Collection has not finished")
    receipt = target / "receipt.json"
    if receipt.stat().st_size > MAX_RECEIPT:
        raise ValueError("Receipt exceeds transfer budget")
    with receipt.open("rb") as stream:
        stream.seek(request["offset"])
        content = stream.read(256 * 1024)
    return {"offset": request["offset"], "hex": content.hex()}


def execute(target: Path) -> None:
    task = load(target / "payload.json")["collection"]
    folder, request = Path(task["source"]), task["request"]
    payload, expected = checked_request(folder, request)
    if expected.resolve() != target.resolve():
        raise ValueError("Collection supervisor identity mismatch")
    # The lock serializes scans across studies sharing this worker root. Heartbeat continues
    # in the existing supervisor; a bounded lock wait leaves a confirmed collection failure.
    import time

    with (folder.parent / "study-collection.lock").open("a") as lock:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "Collection scanner busy; retry collection after it finishes"
                    ) from None
                time.sleep(0.1)
        collect(target, payload, request, folder)


def collect(target: Path, payload: dict[str, Any], request: dict[str, Any], folder: Path) -> None:
    contract = payload["study"]["contract"]
    root = Path(contract["root"])
    owner = load(root / ".genesis-study-owner.json")
    if owner != {
        "schema_version": 1,
        "contract": fingerprint(contract),
        "git_sha": payload["git_sha"],
    }:
        raise ValueError("Collection output ownership changed")
    manifests = run_manifests(
        root,
        contract["assay"],
        contract["source_id"],
        request["worker"],
        contract["study_id"],
        Path(contract["sheet"]),
        Path(contract["references"]),
    )
    if {m["id"] for m in manifests} != set(contract["inputs"]) or sorted(
        m["data"]["name"] for m in manifests
    ) != contract["names"]:
        raise ValueError("Published libraries differ from the planned analysis group")
    observed = worker.inspect(folder)
    evidence = source_record(
        canonical(
            {
                "status": observed,
                "payload_hash": request["payload_hash"],
                "plan": request["plan"],
                # A new scan is a new observation, even if repaired files return to
                # earlier bytes. Retain history without rolling a manifest back.
                "collection_request": request,
            }
        ),
        str(folder / "status.json"),
        "application/json",
    )
    evidence["data"]["worker"] = request["worker"]
    evidence = record("source", evidence["data"], evidence["id"])
    for index, manifest in enumerate(manifests):
        data = manifest["data"]
        if data["run"]["provenance_complete"] and data["run"]["git_sha"] != payload["git_sha"]:
            raise ValueError("Published provenance differs from the planned pipeline revision")
        data["run"].update(
            campaign_id=request["campaign"],
            job_id=request["job"],
            attempt=request["attempt"],
            execution_state="SUCCEEDED",
        )
        data["metadata"] = {**data["metadata"], **contract["metadata"][manifest["id"]]}
        expected_hash = next(
            (i["sha256"] for i in payload["inputs"] if i["path"] == data["reference"]["fasta"]),
            None,
        )
        if expected_hash:
            if data["reference"]["fasta_sha256"] not in {None, expected_hash}:
                raise ValueError("Published reference differs from the planned FASTA checksum")
            data["reference"]["fasta_sha256"] = expected_hash
            data["reference"]["version"] = expected_hash
        data["sources"].append(evidence)
        manifests[index] = record("manifest", data, manifest["id"])
    scan = request["scan"]
    checked = bundle(
        manifests,
        level=scan["level"],
        budget=Budget(scan["max_bytes"], scan["max_records"], scan["max_seconds"]),
        bigwig_python=Path(request["reader"]) if request["reader"] else None,
        worker_identity=request["worker"],
    )
    data = {k: request[k] for k in ("plan", "campaign", "job", "attempt", "worker", "payload_hash")}
    data.update(
        spec={"scan": scan, "generation": request["generation"], "reader": request["reader"]},
        inputs=contract["inputs"],
        bundle=checked,
    )
    receipt = record(
        "collection_receipt", data, identity("collection", fingerprint(request)), version=3
    )
    if len(canonical(receipt).encode()) > MAX_RECEIPT - 1:
        raise ValueError("Collection receipt exceeds 32 MiB; split the study analysis groups")
    dump(target / "receipt.json", receipt, immutable=True)


def validate_receipt(
    value: dict[str, Any], request: dict[str, Any], contract: dict[str, Any]
) -> dict[str, Any]:
    validate(value, "collection_receipt")
    data = value["data"]
    if (
        any(
            data[k] != request[k]
            for k in ("plan", "campaign", "job", "attempt", "worker", "payload_hash")
        )
        or data["spec"]
        != {
            "scan": request["scan"],
            "generation": request["generation"],
            "reader": request["reader"],
        }
        or data["inputs"] != contract["inputs"]
    ):
        raise ValueError("Collection receipt does not match the requested job/version")
    manifests = data["bundle"]["data"]["manifests"]
    if {m["id"] for m in manifests} != set(contract["inputs"]) or len(manifests) != len(
        contract["inputs"]
    ):
        raise ValueError("Receipt contains unexpected or duplicate datasets")
    for manifest in manifests:
        d = manifest["data"]
        if (
            d["source_id"] != contract["source_id"]
            or d["study"] != contract["study_id"]
            or d["assay"] != contract["assay"]
            or identity("dataset", d["source_id"], d["name"]) != manifest["id"]
        ):
            raise ValueError("Receipt scientific dataset identity changed")
        if any(d["metadata"].get(k) != v for k, v in contract["metadata"][manifest["id"]].items()):
            raise ValueError("Receipt lost planned metadata")
        if (
            d["worker"] != request["worker"]
            or d["run"]["root"] != contract["root"]
            or any(
                d["run"][k] != request[key]
                for k, key in (
                    ("campaign_id", "campaign"),
                    ("job_id", "job"),
                    ("attempt", "attempt"),
                )
            )
        ):
            raise ValueError("Artifact provenance does not match this collection")
        for artifact in d["artifacts"]:
            if (
                artifact["worker"] != request["worker"]
                or not Path(artifact["path"]).is_relative_to(contract["root"])
                or ".." in Path(artifact["path"]).parts
            ):
                raise ValueError("Receipt artifact is outside the declared worker/run")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "read", "execute"))
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    if args.action == "execute":
        execute(args.folder)
        return
    request = json.load(sys.stdin)
    handler = {"start": start, "status": status, "read": read}[args.action]
    print(canonical(handler(args.folder, request)))


if __name__ == "__main__":
    main()
