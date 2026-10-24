"""Offline curation acceptance: actual bytes, transactions, stale review and CLI boundaries."""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

from genesis_curation.contracts.adapters import artifact, reference, snapshot
from genesis_curation.contracts.records import (
    ArtifactManifest,
    MetadataProposal,
    QCMeasurement,
    SourceEvidence,
    dump,
    fingerprint,
    identity,
    loads,
    record,
    validate,
)
from genesis_curation.contracts.validation import Budget, bundle
from genesis_curation.curation import review
from genesis_curation.curation.status_page import report
from genesis_curation.qc_policy.evaluate import diagnostic_policy, evaluate
from genesis_curation.registry import store


def rejects(function: Callable[[], object], message: str = "") -> None:
    try:
        function()
    except (ValueError, sqlite3.IntegrityError) as error:
        assert not message or message in str(error), str(error)
    else:
        raise AssertionError("Expected rejection: " + message)


def fixture(
    root: Path,
    name: str = "library",
    assay: str = "bulk-ATAC",
    worker: str = "local",
    source: str = "synthetic-study",
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    output = root / "output"
    output.mkdir(exist_ok=True)
    (root / "reference.fa").write_text(">chr1\n" + "A" * 100 + "\n")
    (root / "tss.tsv").write_text("chr1\t30\t+\n")
    (root / "source.json").write_text('{"FRiP":0,"synthetic":true}\n')
    (output / "peaks.bed").write_text("")
    (output / "report.html").write_text("<html>Known synthetic fixture</html>\n")
    dataset = identity("dataset", source, name)
    evidence = snapshot(root / "source.json", worker)
    measured = QCMeasurement(
        dataset,
        "FRiP",
        "fragment-frip/v1",
        "fraction",
        "all usable fragments",
        0,
        SourceEvidence(evidence["data"]["location"], evidence["data"]["sha256"], "/FRiP", worker),
        depth="synthetic-full",
    ).export()
    ref = reference(
        {
            "reference_id": "tiny-v1",
            "fasta": str(root / "reference.fa"),
            "tss": str(root / "tss.tsv"),
            "contigs": {"chr1": 100},
        }
    )
    return ArtifactManifest(
        source,
        dataset,
        name,
        assay,
        "synthetic plant",
        "study",
        None,
        name,
        [{"lane_id": "L1"}],
        ref,
        worker,
        {
            "campaign_id": None,
            "job_id": None,
            "attempt": None,
            "execution_state": "SUCCEEDED",
            "git_sha": "0" * 40,
            "parameters": {"fixture": True},
            "provenance_complete": True,
            "root": str(root),
        },
        {"tissue": "unknown", "protocol": assay, "synthetic": True},
        [
            artifact(output, "peaks.bed", worker, dataset, "peaks", "bed", "tiny-v1"),
            artifact(output, "report.html", worker, dataset, "report", "html", "tiny-v1"),
        ],
        [evidence],
        [measured],
    ).export()


def altered(value: dict[str, Any], change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    data = copy.deepcopy(value["data"])
    change(data)
    return record(value["kind"], data, value["id"])


def full(value: dict[str, Any]) -> dict[str, Any]:
    return bundle([value], level="full")


def contracts(root: Path) -> dict[str, Any]:
    manifest = fixture(root)
    checked = full(manifest)
    result = checked["data"]["validations"][0]["data"]
    assert result["complete"] and all(f["data"]["result"] == "PASS" for f in result["findings"])
    assert result["records_scanned"] > 0
    assert not bundle([manifest])["data"]["validations"][0]["data"]["complete"]
    for change in (
        lambda v: v.update(schema_version=True),
        lambda v: v.update(schema_version=2),
        lambda v: v.update(unrecognized=True),
        lambda v: v["data"].update(worker=3),
    ):
        bad = copy.deepcopy(manifest)
        change(bad)
        rejects(lambda candidate=bad: validate(candidate))
    rejects(lambda: loads('{"a":1,"a":2}'), "Duplicate")
    rejects(lambda: loads('{"a":NaN}'), "Nonfinite")
    bad = copy.deepcopy(checked)
    bad["data"]["manifests"][0]["data"]["name"] = "modified"
    # Resealing only the outer envelope cannot hide a stale nested record.
    bad["version"] = fingerprint({k: v for k, v in bad.items() if k != "version"})
    rejects(lambda: validate(bad), "stale")
    limited = bundle([manifest], level="full", budget=Budget(max_bytes=1))
    assert not limited["data"]["validations"][0]["data"]["complete"]
    peak = root / "output/peaks.bed"
    peak.write_text("chr1\t0\t101\n")
    assert any(
        f["data"]["result"] == "ERROR"
        for f in full(manifest)["data"]["validations"][0]["data"]["findings"]
    )
    peak.write_text("chr1\t0\t1\n")
    stale = full(checked["data"]["manifests"][0])
    assert any(
        "checksum" in str(f["data"]["observed"])
        for f in stale["data"]["validations"][0]["data"]["findings"]
    )
    peak.write_text("")
    optional = altered(
        manifest,
        lambda d: d["artifacts"].append(
            {
                **artifact(
                    root,
                    "pruned.bam",
                    "local",
                    manifest["id"],
                    "alignment",
                    "bam",
                    "tiny-v1",
                    required=False,
                ),
                "availability": "intentionally_pruned",
                "retention": "intermediate",
            }
        ),
    )
    assert full(optional)["data"]["validations"][0]["data"]["complete"]
    wrong_ref = altered(manifest, lambda d: d["reference"]["contigs"].update(chr1=99))
    assert any(
        f["data"]["result"] == "ERROR"
        for f in full(wrong_ref)["data"]["validations"][0]["data"]["findings"]
    )
    original_digest = Budget.digest
    reference_path = root / "reference.fa"
    reference_bytes = reference_path.read_bytes()

    def change_during_scan(budget: Budget, path: Path) -> str:
        checksum = original_digest(budget, path)
        if path == reference_path:
            path.write_bytes(reference_bytes.replace(b"A", b"C"))
        return checksum

    with patch.object(Budget, "digest", change_during_scan):
        findings = full(manifest)["data"]["validations"][0]["data"]["findings"]
        assert any("changed during validation" in str(f["data"]["observed"]) for f in findings)
    reference_path.write_bytes(reference_bytes)
    return checked


def assess(
    db: sqlite3.Connection, dataset: str, policy: dict[str, Any] | None = None
) -> dict[str, Any]:
    row = store.current(db, dataset)
    value = evaluate(
        store.get(db, "manifest", row["manifest"]),
        store.get(db, "validation", row["validation"]),
        policy or diagnostic_policy(),
        loads(row["metadata"]),
    )
    store.put(db, value["data"]["policy"], dataset)
    store.put(db, value, dataset)
    db.execute("UPDATE datasets SET assessment=? WHERE id=?", (value["version"], dataset))
    return value


def approval(
    directory: Path,
    dataset: str,
    category: str,
    evidence: list[dict[str, Any]],
    target: str | None = None,
) -> dict[str, Any]:
    with contextlib.closing(store.connect(directory)) as db:
        token = review.token(db, dataset)
    return review.decide(
        directory,
        dataset,
        category,
        "approve",
        token,
        "Reviewed synthetic evidence; unknown biological thresholds acknowledged",
        evidence,
        target,
    )


def catalog(root: Path, checked: dict[str, Any]) -> None:
    directory = root / "registry"
    initialized = store.initialize(directory)
    manifest = checked["data"]["manifests"][0]
    dataset = manifest["id"]
    assert store.import_bundle(directory, checked)["imported"] == 1
    assert store.import_bundle(directory, checked)["imported"] == 0
    source = manifest["data"]["sources"][0]["data"]
    evidence = [
        {
            "location": source["location"],
            "worker": source["worker"],
            "sha256": source["sha256"],
            "locator": "/FRiP",
        }
    ]
    with contextlib.closing(store.connect(directory)) as db:
        assert store.search(db, filters={"species": "synthetic plant"})
        assert not store.search(db, text='" OR 1=1 --')
        rejects(lambda: store.search(db, filters={"unsafe SQL": "x"}))
        assert review.status(db, dataset)["execution"] == "SUCCEEDED"
        assert review.status(db, dataset)["structural"] == "PASS"
        assert review.status(db, dataset)["qc"] == "NOT_EVALUATED"
        initial_token = review.token(db, dataset)
    approval(directory, dataset, "metadata", evidence)
    rejects(
        lambda: review.decide(
            directory,
            dataset,
            "metadata",
            "reject",
            initial_token,
            "Conflicting reviewer",
            evidence,
        ),
        "Stale",
    )
    with store.write(directory) as db:
        assessed = assess(db, dataset)
        assert assessed["data"]["result"] == "NOT_EVALUATED"
    approval(directory, dataset, "qc", evidence)
    approval(directory, dataset, "eligibility", evidence)
    selection = record(
        "selection",
        {
            "registry_id": initialized["registry_id"],
            "candidates": [{"id": dataset, "manifest_version": manifest["version"]}],
        },
        identity("selection", dataset),
    )
    with contextlib.closing(store.connect(directory)) as db:
        exported = review.reviewed_export(db, selection, review.profile())
        assert exported["denominators"] == {
            "candidates": 1,
            "included": 1,
            "excluded": 0,
        }
        assert len(exported["exceptions"]) == 1
        roundtrip = store.export_bundle(db, [dataset])
        assert len(store.history(db, dataset)) >= 9
    other = root / "roundtrip"
    store.initialize(other)
    assert store.import_bundle(other, roundtrip)["imported"] == 1
    route = "/reports/" + dataset + "/" + manifest["data"]["artifacts"][1]["id"]
    assert b"synthetic fixture" in report(directory, route)
    rejects(lambda: report(directory, "/reports/../../etc/passwd"))
    proposal = MetadataProposal(
        dataset,
        manifest["version"],
        {"tissue": "leaf"},
        evidence,
        "Manual synthetic annotation",
    ).export()
    review.submit(directory, proposal)
    approval(directory, dataset, "metadata", evidence, proposal["version"])
    with contextlib.closing(store.connect(directory)) as db:
        assert loads(store.current(db, dataset)["metadata"])["tissue"] == "leaf"
        assert store.search(db, text="leaf")
        rejects(
            lambda: review.check_evidence(db, dataset, [{**evidence[0], "locator": "/fabricated"}]),
            "locator",
        )
        assert review.status(db, dataset)["qc"] == "STALE"
        assert review.effective(db, dataset, "eligibility") == "STALE"
        assert (
            review.reviewed_export(db, selection, review.profile())["denominators"]["excluded"] == 1
        )
        assert len(store.history(db, dataset)) >= 11
    with store.write(directory) as db:
        assess(db, dataset)
    approval(directory, dataset, "qc", evidence)
    approval(directory, dataset, "eligibility", evidence)
    with contextlib.closing(store.connect(directory)) as db:
        revised = review.reviewed_export(db, selection, review.profile())
        assert revised["curation"][0]["metadata"]["tissue"] == "leaf"
        assert revised["curation"][0]["proposals"][0] == proposal
    # Importing the same bundle does not overwrite canonical metadata or decisions.
    store.import_bundle(directory, checked)
    with contextlib.closing(store.connect(directory)) as db:
        assert loads(store.current(db, dataset)["metadata"])["tissue"] == "leaf"
    backup = root / "backup.sqlite"
    store.backup(directory, backup)
    restored = root / "restored"
    store.restore(backup, restored)
    with contextlib.closing(store.connect(restored)) as db:
        assert loads(store.current(db, dataset)["metadata"])["tissue"] == "leaf"
    with store.write(directory) as db:
        rejects(lambda: db.execute("DELETE FROM records"), "immutable")
    # Inject an interrupted multi-record transaction and confirm all changes roll back.
    second = full(fixture(root / "second", source="study-two", worker="remote-b"))
    original_put = store.put
    calls = 0

    def failing_put(
        db: sqlite3.Connection, value: dict[str, Any], dataset: str | None = None
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("interrupted import")
        original_put(db, value, dataset)

    with patch.object(store, "put", failing_put):
        rejects(lambda: store.import_bundle(directory, second), "interrupted")
    with contextlib.closing(store.connect(directory)) as db:
        assert len(store.search(db)) == 1
    store.import_bundle(directory, second)
    # A WAL reader retains its snapshot while another connection commits a new dataset.
    with contextlib.closing(store.connect(directory)) as reader:
        reader.execute("BEGIN")
        count = len(store.search(reader))
        third = full(
            fixture(
                root / "third",
                name="DAP-control",
                assay="DAP-seq",
                source="study-three",
            )
        )
        store.import_bundle(directory, third)
        assert len(store.search(reader)) == count
    # Migration from the first checkpoint schema, with a real pre-migration backup.
    old = root / "v1"
    old.mkdir()
    with sqlite3.connect(old / "registry.sqlite") as db:
        for sql in store.MIGRATIONS[1]:
            db.execute(sql)
        db.execute("INSERT INTO meta VALUES('registry_id','older-fixture')")
        db.execute("PRAGMA user_version=1")
    assert store.initialize(old)["registry_id"] == "older-fixture"
    assert list(old.glob("before-v1-to-v6-*.sqlite"))


def policies(checked: dict[str, Any]) -> None:
    manifest, validation = (
        checked["data"]["manifests"][0],
        checked["data"]["validations"][0],
    )
    policy = diagnostic_policy()
    policy = altered(policy, lambda p: p["rules"][0].update(threshold=0.1, severity="ERROR"))
    assessed = evaluate(manifest, validation, policy, manifest["data"]["metadata"])
    assert assessed["data"]["result"] == "ERROR"  # zero is a measured value, not missing.
    missing = altered(manifest, lambda d: d.update(measurements=[]))
    missing_validation = altered(
        validation, lambda d: d.update(manifest_version=missing["version"])
    )
    assert (
        evaluate(missing, missing_validation, policy, missing["data"]["metadata"])["data"]["result"]
        == "NOT_EVALUATED"
    )
    unsupported = altered(policy, lambda p: p.update(species=["different-species"]))
    assert (
        evaluate(manifest, validation, unsupported, manifest["data"]["metadata"])["data"]["result"]
        == "NOT_APPLICABLE"
    )


def commands(root: Path, checked: dict[str, Any]) -> None:
    source = root / "bundle.json"
    dump(source, checked)
    directory = root / "cli-registry"
    command = [sys.executable, "-m", "genesis_curation.execution.controller"]
    for argv in (
        ["--help"],
        ["validate", "manifest", str(source), "--json"],
        ["registry", "init", str(directory), "--json"],
        ["registry", "import", str(directory), "--bundle", str(source), "--json"],
        ["registry", "search", str(directory), "--json"],
        ["review", "queue", str(directory), "--json"],
    ):
        response = subprocess.run(
            command + argv, text=True, capture_output=True, check=False, cwd=root
        )
        assert response.returncode == 0, response.stderr
        if argv != ["--help"]:
            json.loads(response.stdout)
    response = subprocess.run(
        command + ["registry", "search", str(directory), "--limit", "0", "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert response.returncode == 2 and "error" in json.loads(response.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="genesis-curation-") as temporary:
        root = Path(temporary)
        checked = contracts(root / "fixture")
        policies(checked)
        catalog(root, checked)
        commands(root, checked)
    print("PASS: offline contracts, registry, QC, accountable review, export, migration and CLI")


if __name__ == "__main__":
    main()
