"""Study orchestration delegates analysis scheduling to the existing campaign controller."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, dump, fingerprint, load, loads, validate
from ..curation import review
from ..execution import controller, transport
from ..qc_policy.evaluate import diagnostic_policy, evaluate
from ..registry import store
from . import collector, planning
from .inputs import catalog, input_head, read_study


def active(directory: Path) -> dict[str, Any]:
    return validate(load(directory / "active-plan.json"), "study_plan")


def campaign_dir(directory: Path, plan: dict[str, Any]) -> Path:
    return directory / "campaigns" / fingerprint(plan["data"]["campaign"])


def start(directory: Path, plan: dict[str, Any]) -> None:
    planning.recheck(directory, plan)
    if (directory / "active-plan.json").exists():
        old = active(directory)
        if old["version"] != plan["version"] and any(
            r["state"] in {"RUNNING", "UNKNOWN"}
            for r in controller.rows(campaign_dir(directory, old))
        ):
            raise ValueError(
                "An earlier study campaign is active/uncertain; resume or reconcile it first"
            )
    plans = directory / "plans"
    dump(plans / (plan["version"] + ".json"), plan, immutable=True)
    campaign_path = plans / (plan["version"] + "-campaign.json")
    dump(campaign_path, plan["data"]["campaign"], immutable=True)
    controller.initialize(campaign_dir(directory, plan), campaign_path)
    registry = catalog(directory)
    with store.write(registry) as db:
        store.put(db, plan)
        db.execute(
            "INSERT OR REPLACE INTO meta VALUES(?,?)",
            ("study-active:" + read_study(directory)["id"], plan["version"]),
        )
    dump(directory / "active-plan.json", plan)


def options(directory: Path, plan: dict[str, Any]) -> dict[str, Any]:
    path = directory / "collection-options" / (plan["version"] + ".json")
    return (
        load(path)
        if path.is_file()
        else {
            "scan": plan["data"]["validation"],
            "generation": 0,
            "readers": {
                n: w.get("bigwig_python") for n, w in plan["data"]["execution"]["workers"].items()
            },
        }
    )


def configure_collection(
    directory: Path, scan: dict[str, Any] | None, retry: bool, readers: dict[str, str] | None = None
) -> None:
    plan = active(directory)
    value = options(directory, plan)
    selected = {**value["readers"], **(readers or {})}
    if set(selected) - set(plan["data"]["campaign"]["workers"]):
        raise ValueError("Collection reader refers to an unknown worker")
    for path in selected.values():
        if path is not None:
            planning.absolute(path)
    if scan is not None:
        planning.validate_scan(scan)
    if scan is not None and scan != value["scan"] or retry or selected != value["readers"]:
        if value["generation"] >= 100:
            raise ValueError(
                "Collection retry budget exhausted; investigate before creating a new plan"
            )
        value = {
            "scan": scan if scan is not None else value["scan"],
            "generation": value["generation"] + 1,
            "readers": selected,
        }
        dump(directory / "collection-options" / (plan["version"] + ".json"), value)


def request_for(
    directory: Path, plan: dict[str, Any], row: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    campaign = plan["data"]["campaign"]
    worker = campaign["workers"][row["worker"]]
    job = next(j for j in campaign["jobs"] if j["id"] == row["id"])
    payload = {**job["payload"], "repo": worker["repo"], "python": worker["python"]}
    prefix = hashlib.sha256(
        (campaign_dir(directory, plan) / "campaign.json").read_bytes()
    ).hexdigest()[:16]
    folder = Path(worker["root"]) / f"{prefix}-{row['id']}-{row['attempt']}"
    selected = options(directory, plan)
    request = {
        "plan": plan["version"],
        "campaign": fingerprint(campaign),
        "job": row["id"],
        "attempt": row["attempt"],
        "worker": row["worker"],
        "payload_hash": fingerprint(payload),
        "scan": selected["scan"],
        "generation": selected["generation"],
        "reader": selected["readers"].get(row["worker"]),
    }
    return request, worker, folder


def download(
    worker: dict[str, Any], folder: Path, request: dict[str, Any], observed: dict[str, Any]
) -> dict[str, Any]:
    size = observed["size"]
    if type(size) is not int or not 0 < size <= collector.MAX_RECEIPT:
        raise ValueError("Invalid receipt transfer length")
    content = bytearray()
    while len(content) < size:
        result = transport.request(
            worker,
            "genesis_curation.study.collector",
            "read",
            folder,
            {"request": request, "offset": len(content)},
        )
        if result["offset"] != len(content):
            raise ValueError("Receipt chunk offset mismatch")
        chunk = bytes.fromhex(result["hex"])
        if not 0 < len(chunk) <= min(256 * 1024, size - len(content)):
            raise ValueError("Truncated or oversized receipt chunk")
        content.extend(chunk)
    if hashlib.sha256(content).hexdigest() != observed["sha256"]:
        raise ValueError("Transferred receipt digest mismatch")
    return loads(content.decode())


def register(
    directory: Path, plan: dict[str, Any], request: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, Any]:
    contract = plan["data"]["jobs"][request["job"]]
    collector.validate_receipt(receipt, request, contract)
    registry = catalog(directory)
    with store.write(registry) as db:
        prior = db.execute(
            "SELECT receipt FROM study_collections WHERE study=? AND plan=? AND job=? "
            "AND attempt=? AND spec=?",
            (
                read_study(directory)["id"],
                plan["version"],
                request["job"],
                request["attempt"],
                fingerprint(request),
            ),
        ).fetchone()
        if prior:
            if prior[0] != receipt["version"]:
                raise ValueError("Immutable collection identity returned different evidence")
            return {"imported": 0, "receipt": receipt["version"]}
        current = db.execute(
            "SELECT value FROM meta WHERE key=?", ("study-active:" + read_study(directory)["id"],)
        ).fetchone()
        promote = set()
        for dataset, version in contract["inputs"].items():
            head = input_head(db, dataset)
            with store.scoped(db, "inputs"):
                unchanged = review.token(db, dataset) == contract["review_tokens"][dataset]
            if (
                current
                and current[0] == plan["version"]
                and head["version"] == version
                and unchanged
            ):
                promote.add(dataset)
        result = store.import_bundle_db(db, receipt["data"]["bundle"], promote=promote)
        validations = {
            v["data"]["dataset_id"]: v for v in receipt["data"]["bundle"]["data"]["validations"]
        }
        policy = diagnostic_policy()
        for manifest in receipt["data"]["bundle"]["data"]["manifests"]:
            dataset = manifest["id"]
            store.put(db, receipt, dataset)
            if dataset not in promote:
                continue
            row = store.current(db, dataset)
            if row["manifest"] != manifest["version"]:
                raise ValueError("Collection did not become the current result revision")
            db.execute(
                "UPDATE input_heads SET result_manifest=?,result_input=? WHERE dataset=?",
                (manifest["version"], contract["inputs"][dataset], dataset),
            )
            assessment = evaluate(manifest, validations[dataset], policy, loads(row["metadata"]))
            for item in (policy, assessment, *assessment["data"]["findings"]):
                store.put(db, item, dataset)
            db.execute(
                "UPDATE datasets SET assessment=? WHERE id=?", (assessment["version"], dataset)
            )
        db.execute(
            "INSERT INTO study_collections VALUES(?,?,?,?,?,?)",
            (
                read_study(directory)["id"],
                plan["version"],
                request["job"],
                request["attempt"],
                fingerprint(request),
                receipt["version"],
            ),
        )
        return {
            **result,
            "receipt": receipt["version"],
            "promoted": sorted(promote),
            "historical": sorted(set(contract["inputs"]) - promote),
        }


def collect_once(directory: Path, plan: dict[str, Any]) -> bool:
    running = False
    occupied = set()
    for row in controller.rows(campaign_dir(directory, plan)):
        if row["state"] != "SUCCEEDED":
            continue
        request, worker, folder = request_for(directory, plan, row)
        local = directory / "collections" / fingerprint(request)
        progress = local / "status.json"
        previous = load(progress) if progress.exists() else {}
        if previous.get("state") in {"COLLECTED", "FAILED"}:
            continue
        if row["worker"] in occupied:
            running = True
            continue
        try:
            observed = transport.request(
                worker, "genesis_curation.study.collector", "status", folder, request
            )
            state = observed["state"]
            if state == "ABSENT":
                observed = transport.request(
                    worker, "genesis_curation.study.collector", "start", folder, request
                )
                state = observed["state"]
            if state == "SUCCEEDED":
                if "size" not in observed:
                    observed = transport.request(
                        worker, "genesis_curation.study.collector", "status", folder, request
                    )
                receipt = download(worker, folder, request, observed)
                collector.validate_receipt(receipt, request, plan["data"]["jobs"][row["id"]])
                dump(local / "receipt.json", receipt, immutable=True)
                imported = register(directory, plan, request, receipt)
                dump(progress, {"state": "COLLECTED", "request": request, "result": imported})
            elif (
                state in {"RUNNING", "STARTING"} and time.time() - observed.get("updated", 0) < 120
            ):
                occupied.add(row["worker"])
                running = True
                dump(progress, {"state": state, "request": request})
            else:
                dump(
                    progress,
                    {
                        "state": "FAILED" if state == "FAILED" else "UNKNOWN",
                        "request": request,
                        "reason": "Inspect collection supervisor; analysis is not resubmitted",
                    },
                )
        except (
            ValueError,
            OSError,
            ConnectionError,
            subprocess.SubprocessError,
            sqlite3.Error,
        ) as error:
            dump(progress, {"state": "UNKNOWN", "request": request, "reason": str(error)})
    return running


def status(directory: Path) -> dict[str, Any]:
    study = read_study(directory)
    plan = active(directory) if (directory / "active-plan.json").exists() else None
    jobs = controller.rows(campaign_dir(directory, plan)) if plan else []
    result = []
    with contextlib.closing(store.connect(catalog(directory))) as db:
        db.execute("BEGIN")
        for dataset in study["data"]["members"]:
            state = review.status(db, dataset)
            name = store.current(db, dataset)["name"]
            with store.scoped(db, "inputs"):
                state["input_metadata"] = review.effective(db, dataset, "metadata")
            head = input_head(db, dataset)
            state["input_version"] = head["version"]
            state["result_input_version"] = head["result_input"]
            row = (
                next((r for r in jobs if dataset in plan["data"]["jobs"][r["id"]]["inputs"]), None)
                if plan
                else None
            )
            collection = "NOT_STARTED"
            detail: dict[str, Any] = {}
            if plan and row and row["state"] == "SUCCEEDED":
                request, _, _ = request_for(directory, plan, row)
                path = directory / "collections" / fingerprint(request) / "status.json"
                detail = load(path) if path.is_file() else {}
                collection = detail.get("state", "PENDING")
            state.update(
                name=name,
                job=row["id"] if row else None,
                execution=row["state"] if row else "NOT_RUN",
                collection=collection,
            )
            state["collection_detail"] = detail
            if collection == "UNKNOWN":
                state["next_command"] = f"genesis study collect {directory} --once"
            elif collection == "FAILED":
                state["next_command"] = f"genesis study collect {directory} --retry"
            elif not row:
                state["next_command"] = (
                    f"genesis study plan {directory} --execution EXECUTION.json --output PLAN.json"
                )
            elif plan and row["state"] in {"UNKNOWN", "NEEDS_REVIEW"}:
                state["next_command"] = f"genesis issues {campaign_dir(directory, plan)}"
            elif collection == "COLLECTED":
                state["next_command"] = f"genesis review show {catalog(directory)} {dataset}"
            else:
                state["next_command"] = f"genesis study resume {directory}"
            result.append(state)
    return {
        "study_id": study["data"]["study_id"],
        "registry": str(catalog(directory)),
        "plan": plan["version"] if plan else None,
        "campaign_directory": str(campaign_dir(directory, plan)) if plan else None,
        "datasets": result,
    }


def advance(directory: Path, *, collect_only: bool = False) -> bool:
    plan = active(directory)
    folder = campaign_dir(directory, plan)
    allowed = None
    blockers = []
    if not collect_only:
        if any(r["state"] == "QUEUED" for r in controller.rows(folder)):
            try:
                planning.recheck(directory, plan)
            except (ValueError, OSError) as error:
                allowed = set()
                blockers.append(str(error))
        controller.tick(folder, allowed_jobs=allowed)
    store.import_campaign(catalog(directory), folder)
    collecting = collect_once(directory, plan)
    dump(directory / "progress.json", {"blockers": blockers, "updated": time.time()})
    dump(
        folder / "study-progress.json",
        {
            "schema_version": 1,
            "updated": time.time(),
            "blockers": blockers,
            "study": status(directory),
        },
    )
    return (
        collecting
        or not collect_only
        and any(
            r["state"] == "RUNNING" or r["state"] == "QUEUED" and allowed is None
            for r in controller.rows(folder)
        )
    )


def run(
    directory: Path,
    plan: dict[str, Any] | None = None,
    *,
    once: bool = False,
    collect_only: bool = False,
    scan: dict[str, Any] | None = None,
    retry: bool = False,
    readers: dict[str, str] | None = None,
) -> dict[str, Any]:
    with (directory / "study.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if plan:
            start(directory, plan)
        if scan is not None or retry or readers:
            configure_collection(directory, scan, retry, readers)
        current = active(directory)
        with (campaign_dir(directory, current) / "controller.lock").open("a") as campaign_lock:
            fcntl.flock(campaign_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            while True:
                pending = advance(directory, collect_only=collect_only)
                if once or not pending:
                    break
                print(canonical(status(directory)), flush=True)
                time.sleep(2)
    result = status(directory)
    result["blockers"] = load(directory / "progress.json")["blockers"]
    return result
