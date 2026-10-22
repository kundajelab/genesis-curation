"""Prepared-study acceptance: input review, immutable execution and result collection."""

from __future__ import annotations

import argparse
import contextlib
import copy
import csv
import gzip
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from verify_curation import altered as revised
from verify_curation import fixture, rejects

from genesis_curation.atac.inputs import FIELDS, digest
from genesis_curation.contracts.records import dump, fingerprint, identity, load, loads, record
from genesis_curation.contracts.validation import bundle
from genesis_curation.curation import cli as review_cli
from genesis_curation.curation import harmonize, review
from genesis_curation.curation.status_page import summary
from genesis_curation.execution import controller
from genesis_curation.llm import assistant
from genesis_curation.registry import store
from genesis_curation.study import collector, inputs, planning, staging, workflow


def prepared(root: Path, assay: str = "bulk-ATAC") -> tuple[Path, Path]:
    root.mkdir(parents=True)
    fasta = root / "tiny.fa.gz"
    fasta.write_bytes(gzip.compress(b">chr1\n" + b"A" * 100 + b"\n", mtime=0))
    sheet = root / "samples.tsv"
    if assay == "DAP-seq":
        sheet.write_text(
            "sample_id\tspecies\tread1_url\tread2_url\tcontrol_sample\treference_fasta\n"
            + "".join(
                f"{name}\tplant\thttps://example.invalid/{name}.fq.gz\t-\t{control}\ttiny.fa.gz\n"
                for name, control in (
                    ("TF1", "control"),
                    ("TF2", "control"),
                    ("control", "-"),
                )
            )
        )
        return sheet, root
    tss = root / "tss.bed"
    tss.write_text("chr1\t20\t+\n")
    registry = root / "references.json"
    dump(
        registry,
        {
            "schema_version": 1,
            "references": [
                {
                    "reference_id": "tiny",
                    "species": "synthetic plant",
                    "assembly": "synthetic-v1",
                    "annotation_release": "synthetic-v1",
                    "genome_size_method": "fixture length",
                    "genome_size": 100,
                    "fasta": str(fasta),
                    "fasta_sha256": digest(fasta),
                    "tss": str(tss),
                    "tss_sha256": digest(tss),
                    "mitochondrial_contigs": [],
                    "plastid_contigs": [],
                }
            ],
        },
    )
    rows = []
    for name, lanes in (("lib-a", ("lane1", "lane2")), ("lib-b", ("lane1",))):
        for lane in lanes:
            row = {
                "library_id": name,
                "sample_id": name,
                "biological_replicate": name,
                "lane_id": lane,
                "reference_id": "tiny",
                "mapq": "30",
                "duplicates": "exclude",
                "adapter_r1": "-",
                "adapter_r2": "-",
            }
            for mate in ("read1", "read2"):
                path = root / f"{name}-{lane}-{mate}.fq.gz"
                path.write_bytes(gzip.compress(b"@read\nAAAA\n+\nIIII\n", mtime=0))
                row[mate], row[mate + "_sha256"] = str(path), digest(path)
            rows.append(row)
    with sheet.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=sorted(FIELDS), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return sheet, registry


def approve_inputs(catalog: Path, dataset: str, root: Path) -> dict[str, Any]:
    source = root / "curator.json"
    dump(source, {"tissue": "leaf", "treatment": "explicit synthetic treatment"})
    harmonize.ingest(catalog, dataset, [source], [], "local-only", scope="inputs")
    proposal = harmonize.propose(catalog, dataset, scope="inputs")
    with contextlib.closing(store.connect(catalog, scope="inputs")) as db:
        token = review.token(db, dataset)
    review.decide(
        catalog,
        dataset,
        "metadata",
        "approve",
        token,
        "Review of this synthetic fixture only",
        proposal["data"]["evidence"],
        proposal["version"],
        scope="inputs",
    )
    return harmonize.apply(
        catalog, dataset, proposal["version"], root / "approved.json", scope="inputs"
    )


def input_checks(root: Path) -> None:
    sheet, refs = prepared(root / "raw")
    study = root / "study"
    first = inputs.initialize(study, "test", "bulk-ATAC", sheet, refs)
    assert inputs.initialize(study, "test", "bulk-ATAC", sheet, refs) == first
    catalog = inputs.catalog(study)
    dataset = first["datasets"][0]["id"]
    with contextlib.closing(store.connect(catalog)) as db:
        assert len(store.search(db)) == 2
        assert review.status(db, dataset)["execution"] == "NOT_RUN"
        token = review.token(db, dataset)
    rejects(
        lambda: review.decide(catalog, dataset, "metadata", "approve", token, "test", []),
        "--scope inputs",
    )
    rejects(
        lambda: assistant.ask(catalog, {}, "Fixture question", [dataset], "public"), "downgrade"
    )
    # Input queues retain a cursor even when a scanned page has only legacy results.
    first_id = min(d["id"] for d in first["datasets"])
    source = next(
        f"legacy-{n}"
        for n in range(1000)
        if identity("dataset", f"legacy-{n}", "library") < first_id
    )
    legacy = fixture(root / "mixed-catalog", source=source)
    store.import_bundle(catalog, bundle([legacy]))
    cursor, queued = "", set()
    for _ in range(5):
        page, code = review_cli.execute(
            argparse.Namespace(
                operation="queue",
                directory=catalog,
                scope="inputs",
                category="metadata",
                limit=1,
                after=cursor,
            )
        )
        assert code == 0
        queued.update(r["dataset_id"] for r in page["results"])
        if page["next_after"] is None:
            break
        assert page["next_after"] != cursor
        cursor = page["next_after"]
    assert queued == {d["id"] for d in first["datasets"]}
    revision = approve_inputs(catalog, dataset, root)
    with contextlib.closing(store.connect(catalog, scope="inputs")) as db:
        assert review.effective(db, dataset, "metadata") == "APPROVED"
        assert loads(store.current(db, dataset)["metadata"])["tissue"] == "leaf"
    assert revision["data"]["compiled_inputs"]["parameters"]["mapq"] == 30
    before = sheet.read_text()
    sheet.write_text(before.replace("\t30\t", "\t31\t"))
    second = inputs.initialize(study, "test", "bulk-ATAC", sheet, refs)
    assert second["study"]["version"] != first["study"]["version"]
    with contextlib.closing(store.connect(catalog, scope="inputs")) as db:
        assert review.effective(db, dataset, "metadata") == "STALE"
        assert loads(store.current(db, dataset)["metadata"])["tissue"] == "leaf"
    # Real prior schema, rather than merely changing the version pragma on a new database.
    old = root / "prior"
    with patch.object(store, "SCHEMA_VERSION", 3):
        store.initialize(old)
    with sqlite3.connect(old / "registry.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    store.initialize(old)
    assert list(old.glob("before-v3-to-v4-*.sqlite"))
    backup = root / "backup.sqlite"
    store.backup(catalog, backup)
    store.restore(backup, root / "restored")
    with contextlib.closing(store.connect(root / "restored", scope="inputs")) as db:
        assert review.effective(db, dataset, "metadata") == "STALE"
    dap_sheet, dap_refs = prepared(root / "dap-raw", "DAP-seq")
    dap = inputs.initialize(root / "dap", "dap-test", "DAP-seq", dap_sheet, dap_refs)
    assert len(dap["datasets"]) == 3
    assert load(study / "study.json") == second["study"]
    print(
        "PASS: prepared input registration, scoped review, stale evidence, v3 migration and restore"
    )


def preservation_checks(root: Path) -> None:
    registry = root / "legacy-catalog"
    store.initialize(registry)
    original = fixture(root / "legacy-output")
    store.import_bundle(registry, bundle([original]))
    with store.write(registry) as db:
        store.update_metadata(
            db,
            original["id"],
            {**original["data"]["metadata"], "notes": None, "tissue": "reviewed leaf"},
        )
    newer = revised(original, lambda d: d["run"].update(attempt=2))
    store.import_bundle(registry, bundle([newer]))
    with contextlib.closing(store.connect(registry)) as db:
        retained = loads(store.current(db, original["id"])["metadata"])
        assert "notes" in retained and retained["notes"] is None
        assert retained["tissue"] == "reviewed leaf"


def execution(
    root: Path, *, two: bool = False, bigwig_python: Path | None = None
) -> dict[str, Any]:
    repo = root / "worker-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    driver = root / "synthetic-pixi"
    driver.write_text(
        "#!"
        + sys.executable
        + "\nimport runpy\nrunpy.run_path("
        + repr(str(Path(__file__).with_name("study_pipeline_fixture.py").resolve()))
        + ", run_name='__main__')\n"
    )
    driver.chmod(0o700)
    workers = {
        name: {
            "transport": "local",
            "slots": 1,
            "repo": str(repo),
            "python": sys.executable,
            "root": str(root / (name + "-attempts")),
            "run_root": str(root / (name + "-runs")),
            "pixi": str(driver),
            "profile": "local,test",
            "path_map": {},
            "bigwig_python": str(bigwig_python) if bigwig_python else None,
        }
        for name in (("a", "b") if two else ("a",))
    }
    return {
        "schema_version": 1,
        "git_sha": sha,
        "workers": workers,
        "assignments": {"lib-a": "a", "lib-b": "b"} if two else {},
    }


def wait_study(study: Path, *, collect_only: bool = False) -> dict[str, Any]:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if not workflow.advance(study, collect_only=collect_only):
            return workflow.status(study)
        time.sleep(0.1)
    raise AssertionError("Study integration timed out: " + str(workflow.status(study)))


def reviewed_output(
    study: Path,
    dataset: str,
    root: Path,
    *,
    complete: bool,
    export_profile: dict[str, Any] | None = None,
) -> None:
    catalog = inputs.catalog(study)
    with contextlib.closing(store.connect(catalog)) as db:
        row = store.current(db, dataset)
        source = store.get(db, "manifest", row["manifest"])["data"]["sources"][0]["data"]
        token = review.token(db, dataset)
    evidence = [
        {
            "worker": source["worker"],
            "location": source["location"],
            "sha256": source["sha256"],
            "locator": "$",
        }
    ]
    review.decide(
        catalog,
        dataset,
        "qc",
        "approve",
        token,
        "Synthetic fixture only; diagnostic thresholds remain unspecified",
        evidence,
    )
    with contextlib.closing(store.connect(catalog)) as db:
        token = review.token(db, dataset)
    if complete:
        review.decide(
            catalog,
            dataset,
            "eligibility",
            "approve",
            token,
            "Synthetic fixture export; no biological acceptance claim",
            evidence,
            export_profile=export_profile,
        )
    else:
        rejects(
            lambda: review.decide(
                catalog,
                dataset,
                "eligibility",
                "approve",
                token,
                "Must reject incomplete validation",
                evidence,
            ),
            "complete structural",
        )
    target = root / "reviewed.json"
    extra = []
    if export_profile:
        dump(root / "export-profile.json", export_profile)
        extra = ["--profile", str(root / "export-profile.json")]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "genesis_curation.execution.controller",
            "study",
            "export",
            str(study),
            "--dataset",
            dataset,
            "--output",
            str(target),
            *extra,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = load(target)
    assert result["denominators"] == {
        "candidates": 1,
        "included": int(complete),
        "excluded": int(not complete),
    }
    if complete:
        assert (
            result["curation"][0]["input_curation"]["decision"]["data"]["bindings"]["scope"]
            == "inputs"
        )


def workflow_checks(root: Path, bigwig_python: Path | None = None) -> None:
    root.mkdir()
    sheet, refs = prepared(root / "raw")
    study = root / "study"
    value = inputs.initialize(study, "integration", "bulk-ATAC", sheet, refs)
    catalog = inputs.catalog(study)
    dataset = value["datasets"][0]["id"]
    approve_inputs(catalog, dataset, root)
    # A reader can be configured after scientific execution without changing its plan.
    config = execution(root, two=True)
    shutil.copytree(root / "raw", root / "b-data")
    config["workers"]["b"]["path_map"] = {str(root / "raw"): str(root / "b-data")}
    scan = {**planning.DEFAULT_SCAN, "level": "full"}
    plan = planning.build(study, config, scan)
    assert plan["data"]["status"] == "READY", plan
    assert planning.build(study, config, scan) == plan
    assert len(plan["data"]["campaign"]["jobs"]) == 2
    job = next(
        j
        for j in plan["data"]["campaign"]["jobs"]
        if dataset in j["payload"]["study"]["contract"]["inputs"]
    )
    compiled_rows = list(
        csv.DictReader(io.StringIO(job["payload"]["study"]["documents"][0]["text"]), delimiter="\t")
    )
    assert len(compiled_rows) == 2
    assert {r["library_id"] for r in compiled_rows} == {"lib-a"}
    assert {r["mapq"] for r in compiled_rows} == {"30"}
    remote = copy.deepcopy(config)
    remote["workers"]["a"].update(transport="ssh", host="existing-alias")
    blocked = planning.build(study, remote)
    assert blocked["data"]["status"] == "BLOCKED" and "path_map" in str(blocked["data"]["blockers"])
    remote["workers"]["a"]["path_map"] = {str(root / "raw"): "/worker/data"}
    remote_plan = planning.build(study, remote)
    assert remote_plan["data"]["status"] == "READY", remote_plan["data"].get("blockers")
    assert "/worker/data/" in str(remote_plan["data"]["campaign"]["jobs"])
    rejects(lambda: planning.validate_scan({**scan, "max_bytes": 0}), "positive")
    worker = plan["data"]["campaign"]["workers"][job["worker"]]
    payload = {**job["payload"], "repo": worker["repo"], "python": worker["python"]}
    bad = copy.deepcopy(payload)
    bad["study"]["documents"][0]["name"] = "../escape"
    rejects(lambda: staging.stage(Path(worker["root"]) / "attempt", bad), "document")
    workflow.start(study, plan)
    # First tick launches, then a new coordinator invocation reconnects to the same supervisors.
    workflow.advance(study)
    result = wait_study(study)
    assert all(
        r["execution"] == "SUCCEEDED" and r["collection"] == "COLLECTED" for r in result["datasets"]
    ), result
    with contextlib.closing(store.connect(catalog)) as db:
        assert loads(store.current(db, dataset)["metadata"])["tissue"] == "leaf"
        assert review.effective(db, dataset, "metadata") == "APPROVED"
        assert review.status(db, dataset)["structural"] == "NOT_EVALUATED"
    rejects(
        lambda: workflow.configure_collection(study, None, False, {"missing": "/python"}),
        "unknown worker",
    )
    rejects(
        lambda: workflow.configure_collection(study, None, False, {"a": "relative/python"}),
        "absolute",
    )
    if bigwig_python:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "genesis_curation.execution.controller",
                "study",
                "collect",
                str(study),
                "--reader",
                "a=" + str(bigwig_python),
                "--reader",
                "b=" + str(bigwig_python),
                "--once",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        result = wait_study(study, collect_only=True)
        assert all(r["structural"] == "PASS" for r in result["datasets"]), result
    with contextlib.closing(store.connect(catalog)) as db:
        count = db.execute("SELECT COUNT(*) FROM study_collections").fetchone()[0]
    reviewed_output(study, dataset, root, complete=bool(bigwig_python))
    workflow.advance(study)
    with contextlib.closing(store.connect(catalog)) as db:
        assert db.execute("SELECT COUNT(*) FROM study_collections").fetchone()[0] == count
    for contract in plan["data"]["jobs"].values():
        assert (Path(contract["root"]) / "synthetic-invocations.txt").read_text().count(
            "synthetic process"
        ) == 1
    row = next(
        r for r in controller.rows(workflow.campaign_dir(study, plan)) if r["id"] == job["id"]
    )
    request, worker, folder = workflow.request_for(study, plan, row)
    local = study / "collections" / fingerprint(request)
    receipt = load(local / "receipt.json")
    assert workflow.register(study, plan, request, receipt)["imported"] == 0
    altered = copy.deepcopy(receipt)
    altered["data"]["attempt"] += 1
    altered = record("collection_receipt", altered["data"], altered["id"], version=3)
    rejects(
        lambda: collector.validate_receipt(altered, request, plan["data"]["jobs"][job["id"]]),
        "does not match",
    )
    # A complete transfer lost before its local progress update is recovered without a rerun.
    (local / "status.json").unlink()
    assert not workflow.collect_once(study, plan)
    assert load(local / "status.json")["state"] == "COLLECTED"
    # Revalidate only; even an intentionally tiny scan budget never resubmits sequencing.
    workflow.configure_collection(study, {**scan, "max_bytes": 1}, False)
    result = wait_study(study, collect_only=True)
    assert all(r["collection"] == "COLLECTED" for r in result["datasets"]), result
    assert all(r["structural"] == "NOT_EVALUATED" for r in result["datasets"]), result
    for contract in plan["data"]["jobs"].values():
        assert (
            len((Path(contract["root"]) / "synthetic-invocations.txt").read_text().splitlines())
            == 1
        )
    with contextlib.closing(store.connect(catalog)) as db:
        before = store.current(db, dataset)
    # Restoring an artifact's original bytes must be a new, attributable observation.
    contract = plan["data"]["jobs"][job["id"]]
    output = Path(contract["root"]) / "manifest.json"
    original = output.read_bytes()
    output.write_bytes(original + b"\n")
    workflow.configure_collection(study, scan, True)
    changed = wait_study(study, collect_only=True)
    assert all(r["collection"] in {"COLLECTED", "FAILED"} for r in changed["datasets"])
    output.write_bytes(original)
    workflow.configure_collection(study, scan, True)
    restored = wait_study(study, collect_only=True)
    assert all(r["collection"] == "COLLECTED" for r in restored["datasets"]), restored
    assert all(r["execution"] == "SUCCEEDED" for r in restored["datasets"])
    with contextlib.closing(store.connect(catalog)) as db:
        before = store.current(db, dataset)
    conflict = root / "later-source.json"
    dump(conflict, {"tissue": "root"})
    harmonize.ingest(catalog, dataset, [conflict], [], "local-only", scope="inputs")
    rejects(lambda: planning.recheck(study, plan), "stale")
    late_request = {**request, "generation": 100}
    target = folder / "collections" / fingerprint(late_request)
    payload = load(folder / "payload.json")
    collector.collect(target, payload, late_request, folder)
    late = load(target / "receipt.json")
    original_put = store.put

    def interrupted(
        db: sqlite3.Connection, value: dict[str, Any], linked: str | None = None
    ) -> None:
        if value["kind"] == "collection_receipt":
            raise ValueError("interrupted import fixture")
        original_put(db, value, linked)

    with patch.object(store, "put", side_effect=interrupted):
        rejects(lambda: workflow.register(study, plan, late_request, late), "interrupted")
    with contextlib.closing(store.connect(catalog)) as db:
        assert store.current(db, dataset) == before
        assert not db.execute(
            "SELECT 1 FROM records WHERE kind='collection_receipt' AND version=?",
            (late["version"],),
        ).fetchone()
    late_result = workflow.register(study, plan, late_request, late)
    assert dataset in late_result["historical"] and dataset not in late_result["promoted"]
    with contextlib.closing(store.connect(catalog)) as db:
        assert store.current(db, dataset) == before
        assert review.effective(db, dataset, "metadata") == "STALE"
    print(
        "PASS: two workers, exact campaign compilation, restart, automatic collection, "
        "metadata preservation and bounded revalidation"
    )


def dap_checks(root: Path, bigwig_python: Path | None = None) -> None:
    root.mkdir()
    sheet, refs = prepared(root / "raw", "DAP-seq")
    study = root / "study"
    initial = inputs.initialize(study, "dap-integration", "DAP-seq", sheet, refs)
    dataset = initial["datasets"][0]["id"]
    approve_inputs(inputs.catalog(study), dataset, root)
    config = execution(root, bigwig_python=bigwig_python)
    plan = planning.build(
        study, config, {**planning.DEFAULT_SCAN, "level": "full"} if bigwig_python else None
    )
    assert plan["data"]["status"] == "READY"
    assert len(plan["data"]["campaign"]["jobs"]) == 1
    assert len(next(iter(plan["data"]["jobs"].values()))["inputs"]) == 3
    from genesis_curation.samples import SAMPLE_HEADER, load_samples

    documents = plan["data"]["campaign"]["jobs"][0]["payload"]["study"]["documents"]
    compiled = root / "compiled.tsv"
    compiled.write_text(next(d["text"] for d in documents if d["name"] == "samples.tsv"))
    assert compiled.read_text().splitlines()[0].split("\t") == list(SAMPLE_HEADER)
    assert len(load_samples(compiled, refs)) == 3
    workflow.start(study, plan)
    result = wait_study(study)
    assert len(result["datasets"]) == 3
    assert all(
        r["execution"] == "SUCCEEDED" and r["collection"] == "COLLECTED" for r in result["datasets"]
    ), result
    if bigwig_python:
        with contextlib.closing(store.connect(inputs.catalog(study))) as db:
            row = store.current(db, dataset)
            findings = store.get(db, "validation", row["validation"])["data"]["findings"]
            assert {f["data"]["rule_id"] for f in findings if f["data"]["result"] != "PASS"} == {
                "provenance.complete"
            }
        exception = revised(
            review.profile(),
            lambda d: d.update(
                name="synthetic-DAP-provenance-exception", require_full_validation=False
            ),
        )
        reviewed_output(study, dataset, root, complete=True, export_profile=exception)
    print("PASS: shared-control DAP grouping and three separately cataloged libraries")


def failure_checks(root: Path) -> None:
    root.mkdir()
    sheet, refs = prepared(root / "raw")
    study = root / "study"
    inputs.initialize(study, "failure-isolation", "bulk-ATAC", sheet, refs)
    config = execution(root, two=True)
    replica = root / "failed-repo"
    subprocess.run(
        ["git", "clone", "--quiet", config["workers"]["b"]["repo"], str(replica)], check=True
    )
    (replica / ".git/info/exclude").write_text("fail-fixture\n")
    (replica / "fail-fixture").write_text("Explicitly fail this synthetic analysis\n")
    config["workers"]["b"]["repo"] = str(replica)
    plan = planning.build(study, config)
    workflow.start(study, plan)
    original = sheet.read_bytes()
    sheet.write_bytes(original + b"\n")
    assert not workflow.advance(study)
    assert all(r["attempt"] == 0 for r in controller.rows(workflow.campaign_dir(study, plan)))
    assert load(study / "progress.json")["blockers"]
    sheet.write_bytes(original)
    result = wait_study(study)
    states = {r["name"]: r for r in result["datasets"]}
    assert (
        states["lib-a"]["execution"] == "SUCCEEDED" and states["lib-a"]["collection"] == "COLLECTED"
    )
    assert (
        states["lib-b"]["execution"] == "NEEDS_REVIEW"
        and states["lib-b"]["collection"] == "NOT_STARTED"
    )
    rows = controller.rows(workflow.campaign_dir(study, plan))
    assert all(r["attempt"] == 1 for r in rows)
    page = summary(inputs.catalog(study), workflow.campaign_dir(study, plan))
    assert "Prepared study:" in page and "collection COLLECTED" in page
    progress = workflow.campaign_dir(study, plan) / "study-progress.json"
    cached = load(progress)
    cached["blockers"] = ["<script>unsafe</script>"]
    dump(progress, cached)
    page = summary(inputs.catalog(study), workflow.campaign_dir(study, plan))
    assert "&lt;script&gt;" in page and "<script>unsafe</script>" not in page
    # A temporarily unreachable collector cannot cause another analysis launch.
    healthy = next(r for r in rows if r["state"] == "SUCCEEDED")
    request, worker, folder = workflow.request_for(study, plan, healthy)
    local = study / "collections" / fingerprint(request) / "status.json"
    local.unlink()
    with patch(
        "genesis_curation.study.workflow.transport.request",
        side_effect=ConnectionError("fixture disconnect"),
    ):
        workflow.collect_once(study, plan)
    assert load(local)["state"] == "UNKNOWN"
    workflow.collect_once(study, plan)
    assert load(local)["state"] == "COLLECTED"
    assert controller.rows(workflow.campaign_dir(study, plan)) == rows
    print(
        "PASS: changed-input launch gate, failed-job isolation and interrupted collection recovery"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bigwig-python", type=Path)
    args = parser.parse_args()
    environment = (
        {"GENESIS_STUDY_FIXTURE_BIGWIG_PYTHON": str(args.bigwig_python)}
        if args.bigwig_python
        else {}
    )
    with (
        tempfile.TemporaryDirectory(prefix="genesis-study-") as temporary,
        patch.dict(os.environ, environment),
    ):
        root = Path(temporary)
        input_checks(root)
        preservation_checks(root)
        workflow_checks(root / "integration", args.bigwig_python)
        dap_checks(root / "dap-integration", args.bigwig_python)
        failure_checks(root / "failure-integration")
    print("Scientific pipelines / live SSH / biological accuracy: NOT RUN; fixtures are synthetic")


if __name__ == "__main__":
    main()
