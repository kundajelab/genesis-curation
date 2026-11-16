"""Measured QC, cohort boundaries, immutable decisions and signed release safety."""

from __future__ import annotations

import copy
import csv
import io
import sqlite3
import tempfile
from pathlib import Path

from verify_study_web import reject

from genesis_curation.contracts.records import canonical
from genesis_curation.registry import store
from genesis_curation.web import catalog, qc, runtime, studies, workspace


def fixture(root: Path) -> tuple[str, dict[str, str]]:
    rows = []
    for group, count in (
        ("varied", 25),
        ("constant", 20),
        ("small", 19),
        ("missing", 1),
        ("depleted", 1),
        ("single", 1),
    ):
        for i in range(count):
            rows.append(
                {
                    "id": f"{group}_{i}",
                    "organism": "Sorghum bicolor",
                    "assay": "RNA-seq",
                    "tissue": "leaf",
                    "library_selection": "rRNA depletion" if group == "depleted" else "polyA",
                    "library_preparation": group,
                    "cell_resolution": "single-cell" if group == "single" else "bulk",
                    "study_accession": "SRP123456",
                }
            )
    ids = catalog.import_rows(
        root / "catalog", canonical(rows), "synthetic.json", "synthetic", "id", "public", "tester"
    )
    key = studies.link(root / "catalog", ids, "SRP123456", "tester", accession="SRP123456")
    return key, {r["id"]: sid for r, sid in zip(rows, ids, strict=True)}


def measurements(ids: dict[str, str], changed: bool = False) -> str:
    stream = io.StringIO()
    fields = "subject metric value unit method reference source pipeline_version run_id".split()
    writer = csv.DictWriter(stream, fields)
    writer.writeheader()
    for name, sid in ids.items():
        if name.startswith("missing"):
            continue
        value = 0.7 + int(name.split("_")[1]) * 0.005 if name.startswith("varied") else 0.8
        if name == "varied_24":
            value = 0.99
        if changed and name == "varied_0":
            value = 0.2
        writer.writerow(
            dict(
                zip(
                    fields,
                    [
                        sid,
                        "q30_fraction",
                        value * 100,
                        "percent",
                        "synthetic",
                        "synthetic-reference/1",
                        "synthetic://qc",
                        "1",
                        name,
                    ],
                    strict=True,
                )
            )
        )
    return stream.getvalue()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rna-qc-") as folder:
        root = Path(folder)
        runtime.initialize(root, "tester", "synthetic-qc-password")
        study, ids = fixture(root)
        assert workspace.page(root, "RNA-seq")["total"] == 67
        raw = measurements(ids)
        reject(
            lambda: qc.import_measurements(
                root, raw.replace("percent", "reads"), "qc.csv", "tester"
            )
        )
        reject(
            lambda: qc.import_measurements(root, raw.replace(",70.0,", ",nan,"), "qc.csv", "tester")
        )
        imported = qc.import_measurements(root, raw, "qc.csv", "tester")
        assert imported["measurements"] == 66
        assert qc.import_measurements(root, raw, "qc.csv", "tester")["reused"]
        policy = copy.deepcopy(qc.PROFILE)
        policy["thresholds"] = {"q30_fraction": {"min": 0.5}}
        key = qc.start(root, policy, "tester")
        result = qc.analyse(root, key)
        assert result["counts"] == {
            "pass": 24,
            "needs_review": 1,
            "not_evaluated": 40,
            "excluded": 2,
        }, result
        assert result["funnel"]["quality_pass"] == 64
        assert (
            qc.analyse(root, key) == result
        )  # Safe replay after worker loss before job completion.
        records = {r["name"]: r for r in qc.page(root, key, limit=100)["items"]}
        assert records["constant_0"]["outliers"][0]["fence"]["reason"].startswith("Zero IQR")
        assert records["small_0"]["outliers"][0]["fence"]["n"] == 19
        assert records["missing_0"]["quality"] == "not_evaluated"
        assert records["single_0"]["library"]["status"] == "excluded"
        flagged = records["varied_24"]
        decision = {
            "targets": {flagged["subject"]: flagged["review_version"]},
            "action": "retain",
            "reason": "Synthetic outlier adjudication",
        }
        reject(
            lambda: qc.decide(
                root, key, {**decision, "targets": {flagged["subject"]: "old"}}, "tester"
            )
        )
        unknown = records["missing_0"]
        reject(
            lambda: qc.decide(
                root,
                key,
                {**decision, "targets": {unknown["subject"]: unknown["review_version"]}},
                "tester",
            )
        )
        qc.decide(root, key, {**decision, "action": "defer"}, "tester")
        reject(lambda: qc.decide(root, key, decision, "tester"), "Stale")
        latest = next(
            r
            for r in qc.page(root, key, status="needs_review")["items"]
            if r["subject"] == flagged["subject"]
        )
        decision["targets"] = {flagged["subject"]: latest["review_version"]}
        qc.decide(root, key, decision, "tester")
        assert not qc.export(root, key)["retained"]  # QC never substitutes for metadata sign-off.
        studies.preparation(root / "catalog", study, {"status": "COMPLETED"})
        current = studies.get(root / "catalog", study)
        assert not current["pending"], current["tasks"]
        studies.approve(root / "catalog", study, current["version"], "tester")
        assert workspace.page(root, "RNA-seq", status="Reviewed")["total"] == 67
        bundle = qc.export(root, key)
        assert len(bundle["retained"]) == 25 and len(bundle["excluded"]) == 42
        assert bundle["measurement_imports"][imported["id"]]["text"] == raw
        exported = next(r for r in bundle["retained"] if r["name"] == "varied_24")
        assert [d["action"] for d in exported["decision_history"]] == ["defer", "retain"]
        assert exported["observations"]["q30_fraction"]["unit"] == "percent"
        with store.write(root / "catalog") as db:
            for table in ("qc_results", "qc_imports", "qc_profiles", "qc_decisions"):
                try:
                    db.execute("UPDATE " + table + " SET body='{}'")
                except sqlite3.IntegrityError:
                    pass
                else:
                    raise AssertionError(table + " was mutable")
        qc.import_measurements(root, measurements(ids, True), "qc-new.csv", "tester")
        reject(lambda: qc.export(root, key), "stale")
        reject(lambda: qc.decide(root, key, decision, "tester"), "changed")
        fresh = qc.start(root, policy, "tester")
        qc.analyse(root, fresh)
        state = catalog.get(root / "catalog", ids["varied_0"])
        with store.write(root / "catalog") as db:
            state["reviews"]["assay"]["action"] = "reject"
            catalog.save(db, state, "decision", "tester", {"reason": "Synthetic invalidation"})
        reject(lambda: qc.export(root, fresh), "stale")
        empty = qc.start(root, qc.PROFILE, "tester")
        assert not qc.analyse(root, empty)["counts"].get("pass")
        reject(lambda: qc.export(root, "missing"), "Unknown")
        reject(lambda: qc.analyse(root, "missing"), "Unknown")
    print(
        "PASS measured QC: units, missing data, cohort separation, "
        "small/zero-IQR cohorts, outlier review"
    )
    print(
        "PASS QC releases: human sign-off, source imports, decision history, "
        "atomic stale-input blocking"
    )


if __name__ == "__main__":
    main()
