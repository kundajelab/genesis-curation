"""Bounded handoff inspection cannot promote a fixture or mutate scientific evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from genesis_curation.contracts.records import dump, fingerprint, load
from genesis_curation.contracts.validation import Budget
from genesis_curation.scatac import model
from genesis_curation.scatac.common import digest
from genesis_curation.scatac.inspection import inspect_bundle, markdown

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "inspection_demo", ROOT / "examples/scatac/model_inspection_demo.py"
)
assert spec is not None and spec.loader is not None
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def signed(value: dict[str, Any]) -> dict[str, Any]:
    value["version"] = fingerprint({k: v for k, v in value.items() if k != "version"})
    return value


def refresh(root: Path, *, parent: bool = False) -> None:
    """Make mutations checksum-consistent to exercise semantic checks, not only hashing."""
    if parent:
        p = load(root / "parent.json")
        p["data"]["group"] = load(root / "metadata.json")
        for name in p["outputs"]:
            p["outputs"][name] = digest(root / name)
        dump(root / "parent.json", signed(p))
    value = load(root / "complete.json")
    value["data"]["parent"] = load(root / "parent.json")["version"]
    value["data"]["config"] = load(root / "config.json")
    for name in value["outputs"]:
        if (root / name).is_file():
            value["outputs"][name] = digest(root / name)
    dump(root / "complete.json", signed(value))


def status(report: dict[str, Any], rule: str) -> str:
    return next(c["status"] for c in report["checks"] if c["rule"] == rule)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bigwig-python", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        original = demo.fixture(base / "original")
        before = {p.name: (digest(p), p.stat().st_mtime_ns) for p in original.iterdir()}
        with patch("sqlite3.connect", side_effect=AssertionError("Inspection opened a registry")):
            report, code = inspect_bundle(original)
        assert code == 0 and not report["model_ready"] and not report["validation_complete"]
        assert status(report, "contexts.disjoint") == "PASS"
        assert status(report, "evidence.loader") == "NOT_ASSESSED"
        assert status(report, "signal.binary") == "NOT_ASSESSED"
        assert model.validate(original)["checksums"] == "PASS"
        assert before == {p.name: (digest(p), p.stat().st_mtime_ns) for p in original.iterdir()}
        unavailable, exit_code = inspect_bundle(original, bigwig_python=base / "missing-python")
        assert exit_code == 0 and status(unavailable, "signal.binary") == "NOT_ASSESSED"
        assert not unavailable["validation_complete"]
        other = demo.fixture(base / "cherimoya", "cherimoya")
        r, c = inspect_bundle(other)
        assert c == 0 and status(r, "bias.fitted_model") == "NOT_APPLICABLE"

        def variant(name: str) -> Path:
            dest = base / name
            shutil.copytree(original, dest)
            return dest

        optional = variant("omitted-optional-controls")
        config = load(optional / "config.json")
        config.pop("controls")
        dump(optional / "config.json", config)
        refresh(optional)
        assert inspect_bundle(optional)[1] == 0

        real = variant("real-with-fixture-receipt")
        v = load(real / "complete.json")
        v["data"]["provenance"] = {"input_scope": "public_subset", "sampling": "UNKNOWN"}
        v["data"]["loader_evidence"] = {
            "status": "PASS",
            "bundle_version": "synthetic-other-version",
            "model_sha": model.MODELS["cherimoya"],
        }
        v["data"]["model_ready"] = True
        dump(real / "complete.json", signed(v))
        r, c = inspect_bundle(real)
        assert c == 0 and r["model_ready"] is False
        assert (
            status(r, "evidence.loader")
            == status(r, "evidence.scientific_review")
            == "NOT_ASSESSED"
        )
        v["data"].pop("provenance")
        dump(real / "complete.json", signed(v))
        assert inspect_bundle(real)[0]["declared"]["input_scope"] == "UNKNOWN"

        for change, content, rule in (
            ("wrong-fold", "chr2\t4999\t5001\tp\t0\t.\t0\t0\t0\t1\n", "regions.train.peaks.bed"),
            (
                "unknown-contig",
                "chrX\t4999\t5001\tp\t0\t.\t0\t0\t0\t1\n",
                "regions.train.peaks.bed",
            ),
            ("bad-summit", "chr1\t4999\t5001\tp\t0\t.\t0\t0\t0\t-1\n", "regions.train.peaks.bed"),
            ("boundary", "chr1\t1\t3\tp\t0\t.\t0\t0\t0\t1\n", "regions.train.peaks.bed"),
            ("overlap", "chr1\t1999\t2001\tp\t0\t.\t0\t0\t0\t1\n", "contexts.disjoint"),
        ):
            path = variant(change)
            (path / "train.peaks.bed").write_text(content)
            refresh(path)
            r, c = inspect_bundle(path)
            assert c == 1 and status(r, rule) == "FAIL", change
        path = variant("positive-overlap-is-valid")
        bed = path / "train.peaks.bed"
        bed.write_text(bed.read_text() * 2)
        v = load(path / "complete.json")
        v["data"]["peaks_per_fold"]["train"] = 2
        dump(path / "complete.json", signed(v))
        refresh(path)
        assert inspect_bundle(path)[1] == 0
        path = variant("empty")
        (path / "train.peaks.bed").write_text("")
        v = load(path / "complete.json")
        v["data"]["peaks_per_fold"]["train"] = 0
        dump(path / "complete.json", signed(v))
        refresh(path)
        r, c = inspect_bundle(path)
        assert c == 1 and status(r, "regions.train.peaks.bed") == "PASS"
        assert status(r, "target.nonempty.train.peaks.bed") == "FAIL"
        for key, value in (
            ("model_sha", "unaudited"),
            ("max_jitter", 6000),
            ("input_window", True),
        ):
            path = variant("config-" + key)
            for name in ("config.json", "loader.json"):
                v = load(path / name)
                v[key] = value
                dump(path / name, v)
            refresh(path)
            assert inspect_bundle(path)[1] == 1
        path = variant("normalized")
        v = load(path / "metadata.json")
        v["signal"]["normalization"] = "CPM"
        dump(path / "metadata.json", v)
        refresh(path, parent=True)
        r, c = inspect_bundle(path)
        assert c == 1 and status(r, "target.contract") == "FAIL"
        path = variant("stale-parent")
        v = load(path / "metadata.json")
        v["identity"]["cell_type"] = "changed"
        dump(path / "metadata.json", v)
        refresh(path)
        assert status(inspect_bundle(path)[0], "parent.bindings") == "FAIL"
        path = variant("changed-hash")
        (path / "signal.bw").write_bytes(b"corrupt")
        assert inspect_bundle(path)[1] == 1
        path = variant("missing")
        (path / "signal.bw").unlink()
        assert inspect_bundle(path)[1] == 1
        path = variant("escape")
        (path / "signal.bw").unlink()
        (path / "signal.bw").symlink_to(original / "signal.bw")
        assert inspect_bundle(path)[1] == 1
        for budget in (
            Budget(1, 100000, 30),
            Budget(64000000, 1, 30),
            Budget(64000000, 100000, 1e-9),
        ):
            r, c = inspect_bundle(original, budget=budget)
            assert c == 3 and r["structural_status"] == "INCOMPLETE"
        path = variant("malformed")
        (path / "complete.json").write_text("not json")
        assert inspect_bundle(path)[1] == 2

        path = variant("private-metadata")
        v = load(path / "metadata.json")
        v["identity"]["cell_type"] = '<script>alert("x")</script>|[link](bad)'
        v["identity"]["tissue"] = "<img onerror=evil> | `x` [link](bad)"
        v["identity"]["local_note"] = "/private/secret/dataset"
        v["identity"]["api_key"] = "private-example-value"
        dump(path / "metadata.json", v)
        refresh(path, parent=True)
        r, c = inspect_bundle(path)
        text = json.dumps(r) + markdown(r)
        assert c == 0 and "/private/secret" not in text and "private-example-value" not in text
        assert "<img" not in markdown(r) and "&lt;img" in markdown(r)

        argv = [
            sys.executable,
            "-m",
            "genesis_curation.execution.controller",
            "scatac",
            "model",
            "inspect",
        ]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        for fmt in ("json", "markdown"):
            output = base / ("report." + fmt)
            command = [*argv, "--input", str(original), "--format", fmt, "--output", str(output)]
            result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
            assert result.returncode == 0, result.stderr
            assert output.is_file()
            if fmt == "json":
                assert json.loads(result.stdout)["model_ready"] is False
            assert (
                subprocess.run(command, env=env, capture_output=True, check=False).returncode == 2
            )
        bad = subprocess.run(
            [*argv, "--input", str(original), "--output", str(original / "new.md")],
            env=env,
            capture_output=True,
            check=False,
        )
        assert bad.returncode == 2 and not (original / "new.md").exists()
        assert before == {p.name: (digest(p), p.stat().st_mtime_ns) for p in original.iterdir()}
        if args.bigwig_python:
            r, c = inspect_bundle(original, bigwig_python=args.bigwig_python)
            assert c == 0 and status(r, "signal.binary") == "PASS" and r["validation_complete"]
            path = base / "changed-hash"
            refresh(path, parent=True)
            r, c = inspect_bundle(path, bigwig_python=args.bigwig_python)
            assert c == 1 and status(r, "signal.binary") == "FAIL"
            print("PASS actual binary reader: valid and checksum-consistent corrupt bigWig")
        else:
            print("NOT RUN binary reader checks: supply --bigwig-python")
    print(
        "PASS model inspection: identities, semantics, regions, "
        "budgets, evidence isolation, rendering and no mutation"
    )


if __name__ == "__main__":
    main()
