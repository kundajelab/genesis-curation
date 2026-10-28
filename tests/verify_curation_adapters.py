"""Legacy DAP import boundaries and controller-local provenance observations."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from verify_curation import altered, fixture

from genesis_curation.contracts.adapters import (
    DAP_PROVENANCE_NOTE,
    dap_run_record,
    from_dap,
)
from genesis_curation.contracts.records import dump, fingerprint, record
from genesis_curation.contracts.validation import bundle
from genesis_curation.curation.status_page import summary
from genesis_curation.execution.controller import initialize
from genesis_curation.registry import store


def legacy(root: Path) -> None:
    dap = root / "dap"
    provenance = {
        "run": {"git_sha": "0" * 40},
        "samples": [
            {
                "id": name,
                "species": "plant",
                "reference_fasta": "plant.fa.gz",
                "ref_id": "plant_ref",
                "layout": layout,
                "control": control,
            }
            for name, layout, control in (
                ("control", "PE", "-"),
                ("TF1", "PE", "control"),
                ("TF2", "SE", "control"),
            )
        ],
    }
    dump(dap / "output/multiqc/multiqc_data/genesis_provenance.json", provenance)
    dump(
        dap / "output/multiqc/multiqc_data/genesis_flagstat.json",
        {
            "TF1.qc.report.flagstat.txt": {"flagstat_total": 100},
        },
    )
    manifests = from_dap(dap, "dap-study", "local", "study", None, None)
    assert len({m["id"] for m in manifests}) == 3
    control, tf1, tf2 = (m["data"] for m in manifests)
    assert not any(a["role"] == "peaks" for a in control["artifacts"])
    assert tf1["metadata"]["control"] == tf2["metadata"]["control"] == "control"
    assert sum(a["role"] == "fastqc" for a in tf1["artifacts"]) == 2
    assert sum(a["role"] == "fastqc" for a in tf2["artifacts"]) == 1
    assert tf1["measurements"][0]["data"]["value"] == 100
    # Provenance is the Nextflow run's record; the adapter never marks DAP runs complete.
    note = [DAP_PROVENANCE_NOTE]
    assert all(m["data"]["run"]["provenance_complete"] is False for m in manifests)
    assert all(m["data"]["metadata"]["provenance_limitations"] == note for m in manifests)
    # The run's recorded commit, engine, image pins and dirty files are carried over as-is.
    output = dap / "output"
    commit = "a" * 40
    digest = "kundajelab/genesis_macs3@sha256:" + "b" * 64
    (output / "genesis-images.lock").write_text(f"GENESIS_MACS3_IMAGE={digest}\n")
    (output / "git-status.txt").write_text(
        f"branch: main\ncommit: {commit}\ncontainer engine: docker\n"
        "dirty files:\n M main.nf\n?? notes.txt\n"
    )
    recorded = from_dap(dap, "dap-study", "local", "study", None, None)
    assert all(m["data"]["run"]["provenance_complete"] is False for m in recorded)
    assert all(m["data"]["metadata"]["provenance_limitations"] == note for m in recorded)
    run = recorded[0]["data"]["run"]
    assert run["git_sha"] == commit
    assert run["parameters"] == {
        "container_engine": "docker",
        "images": {"GENESIS_MACS3_IMAGE": digest},
        "dirty_files": [" M main.nf", "?? notes.txt"],
    }
    # Runs from before the lock rename recorded their pins in dap-images.lock.
    (output / "genesis-images.lock").unlink()
    old = "kundajelab/dap_seq_peaks@sha256:" + "c" * 64
    (output / "dap-images.lock").write_text(f"DAP_SEQ_PEAKS_IMAGE={old}\n")
    assert dap_run_record(output)[1]["images"] == {"DAP_SEQ_PEAKS_IMAGE": old}
    # A malformed or missing commit is not recorded as one.
    (output / "git-status.txt").write_text("git status unavailable: not a checkout\n")
    assert dap_run_record(output)[0] is None


def observations(root: Path) -> None:
    directory = root / "registry"
    store.initialize(directory)
    base = fixture(root / "source")

    # Equal local paths on two nodes remain separate physical locations.
    def remote(worker: str) -> dict[str, Any]:
        data = json.loads(json.dumps(base["data"]))
        data["worker"] = worker
        for item in data["artifacts"]:
            item["worker"] = worker
        return record("manifest", data, base["id"])

    first = bundle([remote("node-a")])
    second = bundle([remote("node-b")])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.import_bundle(directory, first), range(2)))
    assert sorted(r["imported"] for r in results) == [0, 1]
    store.import_bundle(directory, second)
    with contextlib.closing(store.connect(directory)) as db:
        assert db.execute("SELECT COUNT(DISTINCT worker) FROM locations").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(DISTINCT path) FROM locations").fetchone()[0] == 2
    pruned = altered(
        second["data"]["manifests"][0],
        lambda d: d["artifacts"][0].update(
            required=False, availability="intentionally_pruned", retention="intermediate"
        ),
    )
    store.import_bundle(directory, bundle([pruned]))
    with store.write(directory) as db:
        assert store.search(db, filters={"availability": "intentionally_pruned"})
        db.execute("UPDATE meta SET value='false' WHERE key='fts5'")
        assert store.search(db, text="library") and not store.search(db, text="%")

    plan = {
        "schema_version": 1,
        "workers": {
            "local": {
                "transport": "local",
                "slots": 1,
                "repo": str(root),
                "root": str(root / "worker"),
                "python": sys.executable,
            }
        },
        "jobs": [
            {
                "id": "job",
                "worker": "local",
                "payload": {
                    "git_sha": "0" * 40,
                    "argv": ["true"],
                    "inputs": [{"path": str(root / "input"), "sha256": "0" * 64}],
                },
            }
        ],
    }
    dump(root / "plan.json", plan)
    campaign = root / "campaign"
    initialize(campaign, root / "plan.json")  # Initialize only; never launch the job.
    assert store.import_campaign(directory, campaign)["imported"] == 1
    assert store.import_campaign(directory, campaign)["imported"] == 0

    def link(data: dict[str, Any]) -> None:
        data.update(name="<script>alert(1)</script>")
        data["run"].update(campaign_id=fingerprint(plan), job_id="job", attempt=1)

    linked = altered(base, link)
    store.import_bundle(directory, bundle([linked]))
    page = summary(directory, campaign)
    assert "&lt;script&gt;" in page and "<script>" not in page
    with sqlite3.connect(campaign / "state.sqlite") as db:
        assert db.execute("SELECT state,attempt FROM jobs").fetchone() == ("QUEUED", 0)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-curation-adapters-") as temporary:
        root = Path(temporary)
        legacy(root)
        observations(root)
    print("PASS: legacy adapters, worker paths, concurrent imports, campaign evidence and HTML")


if __name__ == "__main__":
    main()
