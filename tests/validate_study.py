"""Opt-in real DAP study acceptance on fresh, explicitly synthetic fixtures."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from genesis_curation.contracts.records import dump, load
from genesis_curation.execution import controller
from genesis_curation.registry import store
from genesis_curation.study import inputs, workflow

# End-to-end acceptance of both repos: GENESIS_REPO is the genesis-preprocessing checkout
# whose pipelines run here, and its DAP test fixture and package are borrowed for the inputs.
PIPELINE = Path(os.environ.get("GENESIS_REPO", "")).expanduser().resolve()
sys.path[:0] = [str(PIPELINE / "tests"), str(PIPELINE / "genesis_tools/src")]

# The pipeline checkout provides this module at run time (see PIPELINE above).
from verify_pipeline import create_fixture, serve  # noqa: E402  # ty: ignore[unresolved-import]

ROOT = Path(__file__).resolve().parents[1]
BUDGET = [
    "--level",
    "full",
    "--max-bytes",
    "2147483648",
    "--max-records",
    "50000000",
    "--max-seconds",
    "600",
]


class Acceptance:
    """Run public commands and keep exact commands, outputs and observed durations."""

    def __init__(self, root: Path, revision: str, reader: Path) -> None:
        self.root, self.revision, self.reader = root, revision, reader
        self.events: list[dict[str, Any]] = []
        dump(root / "synthetic-only.json", {"fixture": "genesis-study-acceptance-v1"})
        (root / "logs").mkdir()

    def call(self, *args: str | Path, expected: tuple[int, ...] = (0,)) -> dict[str, Any]:
        command = [
            sys.executable,
            "-m",
            "genesis_curation.execution.controller",
            *map(str, args),
        ]
        start = time.monotonic()
        result = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True, timeout=1800, check=False
        )
        index = len(self.events)
        prefix = self.root / "logs" / f"{index:03d}-{args[0]}-{args[1]}"
        prefix.with_suffix(".stdout").write_text(result.stdout)
        prefix.with_suffix(".stderr").write_text(result.stderr)
        self.events.append(
            {
                "argv": command,
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - start, 3),
                "stdout": str(prefix.with_suffix(".stdout").relative_to(self.root)),
                "stderr": str(prefix.with_suffix(".stderr").relative_to(self.root)),
            }
        )
        dump(self.root / "commands.json", {"commands": self.events})
        if result.returncode not in expected:
            raise AssertionError(
                f"Command failed ({result.returncode}): {command}\n"
                + result.stdout[-4000:]
                + result.stderr[-4000:]
            )
        # Long-running controller calls print JSON progress followed by a final JSON line.
        return json.loads(result.stdout.splitlines()[-1])

    def approve(
        self,
        catalog: Path,
        dataset: str,
        category: str,
        evidence: list[dict[str, Any]],
        *,
        scope: str = "results",
        target: str | None = None,
    ) -> None:
        # This test may never approve a caller-supplied or production registry.
        if not catalog.resolve().is_relative_to(self.root.resolve()) or load(
            self.root / "synthetic-only.json"
        ) != {"fixture": "genesis-study-acceptance-v1"}:
            raise ValueError("Fixture review is restricted to this newly owned test directory")
        status = self.call("review", "show", catalog, dataset, "--scope", scope)["status"]
        evidence_path = self.root / f"evidence-{dataset}-{category}-{scope}.json"
        dump(evidence_path, {"evidence": evidence})
        extra = ["--target", target] if target else []
        self.call(
            "review",
            "approve",
            catalog,
            dataset,
            "--scope",
            scope,
            "--category",
            category,
            "--token",
            status["token"],
            "--reason",
            "Synthetic acceptance fixture only; no biological quality claim or plant threshold",
            "--evidence",
            evidence_path,
            *extra,
        )

    def review_inputs(self, catalog: Path, datasets: list[dict[str, Any]]) -> None:
        source = self.root / "fixture-source.json"
        dump(source, {"treatment": "explicit synthetic treatment"})
        for dataset in datasets:
            key = dataset["id"]
            self.call(
                "metadata",
                "ingest",
                catalog,
                key,
                "--scope",
                "inputs",
                "--source",
                source,
                "--classification",
                "local-only",
            )
            proposal = self.call(
                "metadata",
                "propose",
                catalog,
                key,
                "--scope",
                "inputs",
                "--output",
                self.root / f"proposal-{key}.json",
            )
            # The CLI result is the exported versioned proposal itself.
            self.approve(
                catalog,
                key,
                "metadata",
                proposal["data"]["evidence"],
                scope="inputs",
                target=proposal["version"],
            )
            self.call(
                "metadata",
                "apply",
                catalog,
                key,
                "--scope",
                "inputs",
                "--target",
                proposal["version"],
                "--output",
                self.root / f"canonical-{key}.json",
            )

    def review_results(self, study: Path) -> None:
        catalog = inputs.catalog(study)
        for dataset in self.call("study", "status", study)["datasets"]:
            assert dataset["structural"] == "PASS", dataset
            with contextlib.closing(store.connect(catalog)) as db:
                manifest = store.get(
                    db, "manifest", store.current(db, dataset["dataset_id"])["manifest"]
                )
                source = manifest["data"]["sources"][0]["data"]
            evidence = [
                {
                    "worker": source["worker"],
                    "location": source["location"],
                    "sha256": source["sha256"],
                    "locator": "$",
                }
            ]
            for category in ("qc", "eligibility"):
                self.approve(catalog, dataset["dataset_id"], category, evidence)

    def export(self, study: Path, label: str, included: int) -> dict[str, Any]:
        datasets = self.call("study", "status", study)["datasets"]
        selected = [a for d in datasets for a in ("--dataset", d["dataset_id"])]
        target = study / f"export-{label}.json"
        self.call("study", "export", study, *selected, "--output", target)
        result = load(target)
        assert result["denominators"] == {
            "candidates": len(datasets),
            "included": included,
            "excluded": len(datasets) - included,
        }, result
        return result

    def scenario(
        self,
        assay: str,
        sheet: Path,
        references: Path,
        fixture: Path,
        config: Path,
        assignments: dict[str, str],
    ) -> dict[str, Any]:
        study = self.root / "dap-study"
        registered = self.call(
            "study",
            "init",
            study,
            "--study-id",
            "synthetic-dap",
            "--assay",
            assay,
            "--sheet",
            sheet,
            "--references",
            references,
        )
        self.review_inputs(inputs.catalog(study), registered["datasets"])
        workers = {}
        for name in ("a", "b"):
            node = self.root / ("dap-node-" + name)
            shutil.copytree(fixture, node / "data")
            workers[name] = {
                "transport": "local",
                "slots": 1,
                "repo": str(PIPELINE),
                "python": sys.executable,
                "pixi": shutil.which("pixi"),
                "root": str(node / "attempts"),
                "run_root": str(node / "runs"),
                "profile": "local,test,docker",
                "config": str(config),
                "path_map": {str(fixture): str(node / "data")},
                "bigwig_python": str(self.reader),
            }
        execution = self.root / "dap-execution.json"
        dump(
            execution,
            {
                "schema_version": 1,
                "git_sha": self.revision,
                "workers": workers,
                "assignments": assignments,
            },
        )
        plan_path = study / "plan.json"
        plan = self.call("study", "plan", study, "--execution", execution, "--output", plan_path)
        assert plan["data"]["status"] == "READY", plan
        assert len(plan["data"]["campaign"]["jobs"]) == 2
        # Exit the controller after launch, then reconnect from a separate OS process.
        self.call("study", "run", study, "--plan", plan_path, "--once")
        state = self.call("study", "resume", study)
        assert all(
            d["execution"] == "SUCCEEDED" and d["collection"] == "COLLECTED"
            for d in state["datasets"]
        ), state
        campaign = workflow.campaign_dir(study, plan)
        attempts = [(r["id"], r["attempt"]) for r in controller.rows(campaign)]
        assert len(attempts) == 2 and all(n == 1 for _, n in attempts)
        assert all(d["structural"] == "NOT_EVALUATED" for d in state["datasets"])
        self.export(study, "metadata-only", 0)
        complete = self.call("study", "collect", study, *BUDGET)
        assert all(d["structural"] == "PASS" for d in complete["datasets"]), complete
        for job in plan["data"]["jobs"].values():
            # DAP runs record image pins and the checkout beside their outputs.
            output = Path(job["root"]) / "output"
            status = (output / "git-status.txt").read_text().splitlines()
            assert status[1] == "commit: " + self.revision, status
            assert (output / "genesis-images.lock").read_bytes() == (
                PIPELINE / "conf/genesis-images.lock"
            ).read_bytes()
        self.review_results(study)
        self.export(study, "approved", len(state["datasets"]))
        # Reconnecting/recollecting the same generation imports nothing new and keeps decisions.
        with contextlib.closing(store.connect(inputs.catalog(study))) as db:
            count = db.execute("SELECT COUNT(*) FROM study_collections").fetchone()[0]
        self.call("study", "resume", study)
        self.call("study", "collect", study)
        with contextlib.closing(store.connect(inputs.catalog(study))) as db:
            assert db.execute("SELECT COUNT(*) FROM study_collections").fetchone()[0] == count
        self.export(study, "idempotent", len(state["datasets"]))
        # A genuinely partial full scan cannot retain export eligibility.
        partial = self.call("study", "collect", study, "--max-bytes", "1")
        assert all(d["structural"] == "NOT_EVALUATED" for d in partial["datasets"]), partial
        self.export(study, "budget-limited", 0)
        self.call("study", "collect", study, *BUDGET)
        # Corruption affects one group while the independent group is still collected.
        job = next(iter(plan["data"]["jobs"].values()))
        root = Path(job["root"])
        target = next((root / "output").rglob("*.bw"))
        original = target.read_bytes()
        target.write_bytes(b"corrupt synthetic acceptance artifact")
        broken = self.call("study", "collect", study, "--retry", expected=(3,))
        assert any(d["structural"] == "ERROR" for d in broken["datasets"]), broken
        assert any(d["structural"] == "PASS" for d in broken["datasets"]), broken
        self.export(study, "corrupt", 0)
        target.write_bytes(original)
        self.call("study", "collect", study, "--retry")
        # An invalid run sidecar causes a collection failure, never a sequencing retry.
        sidecar = root / "output/multiqc/multiqc_data/genesis_provenance.json"
        before = sidecar.read_bytes()
        sidecar.write_text("not JSON\n")
        failed = self.call("study", "collect", study, "--retry", expected=(3,))
        assert any(d["collection"] == "FAILED" for d in failed["datasets"]), failed
        assert any(d["collection"] == "COLLECTED" for d in failed["datasets"]), failed
        sidecar.write_bytes(before)
        recovered = self.call("study", "collect", study, "--retry")
        assert all(d["structural"] == "PASS" for d in recovered["datasets"]), recovered
        self.review_results(study)
        final = self.export(study, "recovered", len(state["datasets"]))
        assert attempts == [(r["id"], r["attempt"]) for r in controller.rows(campaign)]
        evidence = {
            "assay": assay,
            "datasets": len(state["datasets"]),
            "attempts": attempts,
            "denominators": final["denominators"],
            "jobs": plan["data"]["jobs"],
            "status": "PASS",
            "scientific_execution_retries": 0,
        }
        dump(self.root / "dap-result.json", evidence)
        print(
            f"PASS: {assay}: processing, reconnect, full collection, review, export and recovery",
            flush=True,
        )
        return evidence


def preflight(reader: Path) -> str:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PIPELINE, text=True
    ).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=PIPELINE, text=True).strip():
        raise ValueError("Real study validation requires a clean committed checkout")
    for program in ("pixi", "nextflow", "docker"):
        if not shutil.which(program):
            raise ValueError(f"Missing required existing executable: {program}")
    subprocess.run(
        [str(reader), "-I", "-c", "import pyBigWig; print(pyBigWig.__version__)"],
        check=True,
    )
    images = {
        line.split("=", 1)[1].strip().strip('"')
        for line in (PIPELINE / "conf/genesis-images.lock").read_text().splitlines()
        if "_IMAGE=" in line and not line.startswith("#")
    }
    for image in sorted(images):
        subprocess.run(["docker", "image", "inspect", image], check=True, capture_output=True)
    return revision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path(tempfile.gettempdir()),
        help="Existing Docker-visible directory outside the checkout",
    )
    parser.add_argument(
        "--bigwig-python",
        type=Path,
        required=True,
        help="Existing separate interpreter with pyBigWig",
    )
    args = parser.parse_args()
    reader = args.bigwig_python.absolute()
    revision = preflight(reader)
    if args.work_root.resolve().is_relative_to(ROOT):
        raise ValueError("Study worker roots must be outside the checkout")
    root = Path(tempfile.mkdtemp(prefix="genesis-real-study-", dir=args.work_root)).resolve()
    print(f"Real study acceptance evidence: {root}", flush=True)
    acceptance = Acceptance(root, revision, reader)
    start = time.monotonic()
    success = False
    try:
        dap = root / "dap-fixture"
        dap.mkdir()
        with serve(dap / "inputs", docker=True) as url:
            sheet = create_fixture(dap, url, docker=True)
            acceptance.scenario(
                "DAP-seq",
                sheet,
                dap / "references",
                dap,
                dap / "limits.config",
                {
                    "se_treatment": "a",
                    "se_second": "a",
                    "se_control": "a",
                    "pe_treatment": "b",
                    "pe_control": "b",
                },
            )
        dump(
            root / "result.json",
            {
                "status": "PASS",
                "git_sha": revision,
                "seconds": round(time.monotonic() - start, 3),
                "commands": len(acceptance.events),
                "live_ssh": "NOT RUN",
                "public_biological_data": "NOT RUN",
                "live_ai": "NOT RUN",
            },
        )
        success = True
        print(f"PASS: real study acceptance at {revision}; evidence {root}", flush=True)
    finally:
        # Keep failed evidence and any owned supervisors for diagnosis; never delete live runs.
        if success and not args.keep:
            shutil.rmtree(root)
        else:
            print(f"Evidence retained: {root}", flush=True)


if __name__ == "__main__":
    main()
