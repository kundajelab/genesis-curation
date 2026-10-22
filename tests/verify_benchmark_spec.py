"""Offline experiment planning and cache-integrity checks."""

from __future__ import annotations

import copy
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from genesis_curation.benchmark.runner import spp
from genesis_curation.benchmark.runtime import Runtime, fetch
from genesis_curation.benchmark.spec import load, sha256, source_identity
from genesis_curation.benchmark.workflows import verify_effective_commands, verify_workflow_outputs

TOML = """schema_version = 1
id = "tiny"
assay = "DAP-seq"
adapter = "postalign"
dataset = "dataset.json"
baseline = "mapq0-retained"
comparison = "matched-stage"
seed = 60141
[parameters]
mapq = [0, 10, 30]
duplicates = ["retained"]
keep_dup = "1"
qvalue = 0.05
spp = false
quantify = false
[limits]
cpus = 2
memory_gb = 4
seconds = 1200
disk_bytes = 2000000000
download_bytes = 0
max_arms = 9
"""


def rejects(function: Callable[[], object]) -> None:
    try:
        function()
    except ValueError:
        return
    raise AssertionError("Expected rejection")


def main() -> None:
    identity = source_identity()
    assert identity["source_kind"] == "checkout" and identity["git_sha"]
    assert "benchmark/report.html" in identity["recipe_sha256"]
    with tempfile.TemporaryDirectory() as temp:
        package = Path(temp) / "venv/lib/python3.14/site-packages/genesis_curation"
        benchmark = package / "benchmark"
        benchmark.mkdir(parents=True)
        (package / "atac").mkdir()
        (benchmark / "spec.py").write_text("# installed fixture\n")
        (benchmark / "report.html").write_text("<html>installed template</html>\n")
        (package / "atac/fragments.py").write_text("# installed selection\n")
        with (
            patch("genesis_curation.benchmark.spec.__file__", str(benchmark / "spec.py")),
            patch(
                "genesis_curation.benchmark.spec.subprocess.run",
                side_effect=AssertionError("A wheel must not borrow an enclosing Git revision"),
            ),
        ):
            installed = source_identity()
        assert installed["git_sha"] is None and installed["git_status"] is None
        assert installed["source_kind"] == "installed-package"
        assert installed["recipe_sha256"]["benchmark/report.html"] == sha256(
            benchmark / "report.html"
        )
        source_package = Path(temp) / "source/src/genesis_curation"
        source_benchmark = source_package / "benchmark"
        source_benchmark.mkdir(parents=True)
        (source_package / "atac").mkdir()
        (source_benchmark / "spec.py").write_text("# source archive\n")
        (source_package / "atac/fragments.py").write_text("# selection\n")
        with (
            patch("genesis_curation.benchmark.spec.__file__", str(source_benchmark / "spec.py")),
            patch(
                "genesis_curation.benchmark.spec.subprocess.run",
                side_effect=AssertionError("A source archive has no own Git revision"),
            ),
        ):
            archived = source_identity()
        assert archived["source_kind"] == "source-tree" and archived["git_sha"] is None
    with patch("genesis_curation.benchmark.spec.subprocess.run", side_effect=FileNotFoundError):
        unversioned = source_identity()
    assert unversioned["git_metadata_status"] == "UNAVAILABLE"
    assert unversioned["git_sha"] is None and unversioned["recipe_sha256"]
    policy = {
        "adapter": "nfcore-atac",
        "parameters": {"qvalue": 0.01, "keep_dup": "all", "mapq": [1]},
        "workflow": {"parameters": {"narrow_peak": True}},
    }
    commands = {
        "A:MACS2_CALLPEAK (lib)": {"command": "macs2 callpeak -q 0.01 --keep-dup all -f BAMPE"},
        "A:MULTIQC_CUSTOM_PEAKS (lib)": {"command": "irrelevant"},
        "A:BAMTOOLS_FILTER (lib)": {"command": "samtools view -q 1 -F 1024"},
    }
    verify_effective_commands(policy, commands)
    wrong = copy.deepcopy(commands)
    wrong["A:MACS2_CALLPEAK (lib)"]["command"] += " --broad"
    rejects(lambda: verify_effective_commands(policy, wrong))
    wrong["A:MACS2_CALLPEAK (lib)"]["command"] = "macs2 callpeak -q 0.05 --keep-dup all -f BAMPE"
    rejects(lambda: verify_effective_commands(policy, wrong))
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "ref.fa").write_text(">chr1\n" + "A" * 1000 + "\n")
        (root / "sizes").write_text("chr1\t1000\n")
        (root / "bam").write_bytes(b"checksum fixture; not an executable BAM")
        dataset = {
            "schema_version": 1,
            "id": "known",
            "assay": "DAP-seq",
            "synthetic": True,
            "status": "READY",
            "reference": {
                "id": "toy",
                "species": "synthetic",
                "assembly": "toy1",
                "cultivar": "NA",
                "fasta": "ref",
                "fasta_content_sha256": sha256(root / "ref.fa"),
                "chrom_sizes": "sizes",
                "genome_size": 1000,
                "mitochondrial": [],
                "plastid": [],
            },
            "assets": {
                name: {"path": path, "sha256": sha256(root / path)}
                for name, path in (
                    ("ref", "ref.fa"),
                    ("sizes", "sizes"),
                    ("bam", "bam"),
                )
            },
            "libraries": [
                {
                    "id": "one",
                    "layout": "SE",
                    "control": None,
                    "raw_templates": 1,
                    "biological_replicate": "synthetic",
                    "bam": "bam",
                }
            ],
        }
        manifest = root / "test.toml"
        manifest.write_text(TOML)
        datafile = root / "dataset.json"
        datafile.write_text(json.dumps(dataset))
        plan = load(manifest)
        assert plan["status"] == "READY" and len(plan["arms"]) == 3
        original_id = plan["experiment_id"]
        manifest.write_text(TOML.replace("[0, 10, 30]", "[0, 20, 30]"))
        assert load(manifest)["experiment_id"] != original_id
        manifest.write_text(TOML + "\nunknown = true\n")
        rejects(lambda: load(manifest))
        manifest.write_text(TOML.replace("max_arms = 9", "max_arms = 2"))
        rejects(lambda: load(manifest))
        manifest.write_text(TOML)
        collision = copy.deepcopy(dataset)
        libraries = collision["libraries"]
        assert isinstance(libraries, list)
        libraries.append(libraries[0].copy())
        datafile.write_text(json.dumps(collision))
        rejects(lambda: load(manifest))
        datafile.write_text(json.dumps(dataset))
        (root / "ref.fa").write_text(">chr1\nC\n")
        rejects(lambda: load(manifest))
        (root / "ref.fa").write_text(">chr1\n" + "A" * 1000 + "\n")
        out = root / "results"
        runtime = Runtime(load(manifest), out, resume=False)
        task = out / "task"
        task.mkdir()
        artifact = task / "metric.json"
        artifact.write_text("{}")
        runtime.seal(task, [root / "ref.fa"], [artifact], {"policy": "explicit"})
        resumed = Runtime(load(manifest), out, resume=True)
        assert resumed.cached(task, [root / "ref.fa"], {"policy": "explicit"})
        budget_before = (out / "runtime-budget.json").read_bytes()
        for status in ("PRUNING", "PRUNED"):
            (out / "retention.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "prune_status": status,
                    }
                )
            )
            rejects(lambda: Runtime(load(manifest), out, resume=True))
            assert (out / "runtime-budget.json").read_bytes() == budget_before
            assert artifact.read_text() == "{}"
        (out / "retention.json").unlink()
        artifact.write_text('{"changed":true}')
        assert not resumed.cached(task, [root / "ref.fa"], {"policy": "explicit"})
        rejects(lambda: Runtime(load(manifest), out, resume=False))
        artifact.unlink()
        assert not resumed.cached(task, [root / "ref.fa"], {"policy": "explicit"})
        rejects(lambda: runtime.seal(task, [], [artifact], {}))
        rejects(
            lambda: runtime.command(
                "intentional-failure", ["/bin/sh", "-c", "echo diagnostic >&2; exit 7"], out, []
            )
        )
        logs = list((out / "commands" / "intentional-failure").glob("*/run.json"))
        assert len(logs) == 1
        failure = json.loads(logs[0].read_text())
        assert failure["exit_status"] == 7 and failure["status"] == "FAILED"
        assert (logs[0].parent / "stderr").read_text() == "diagnostic\n"

        filtered = out / "arm" / "library" / "spp"
        filtered.mkdir(parents=True)
        bam = filtered / "selected.bam"
        bam.write_bytes(b"fixture")
        runtime.seal(filtered, [root / "ref.fa"], [bam], {"stage": "filter"})
        runtime.resume = True

        def score(
            _label: str, _command: list[str], cwd: Path, _inputs: list[Path], *, image: str
        ) -> None:
            assert image == "qc"
            (cwd / "spp.tsv").write_text("sample\t10\t100\t0.1\t50\t0.05\t0\t0.01\t2\t1\t0\n")
            (cwd / "spp.pdf").write_bytes(b"fixture")

        with patch.object(runtime, "command", side_effect=score) as command:
            spp(runtime, out / "arm", {"id": "library"}, bam)
            assert runtime.cached(filtered, [root / "ref.fa"], {"stage": "filter"}), (
                "SPP scoring overwrote filtering cache"
            )
            spp(runtime, out / "arm", {"id": "library"}, bam)
            assert command.call_count == 1, "SPP scoring failed stable resume"
        wf = root / "workflow"
        wf.mkdir()
        product = wf / "peaks.bed"
        product.write_text("chr1\t0\t10\n")
        (wf / "results.json").write_text(
            json.dumps(
                {
                    "results": [
                        {"libraries": {"sample": {"artifacts": {str(product): sha256(product)}}}}
                    ]
                }
            )
        )
        verify_workflow_outputs(wf)
        (wf / "invocations").mkdir()
        (wf / "invocations" / "1.json").write_text((wf / "results.json").read_text())
        product.write_text("chr1\t0\t20\n")
        rejects(lambda: verify_workflow_outputs(wf))
        (wf / "results.json").write_text('{"status":"FAILED"}')
        rejects(lambda: verify_workflow_outputs(wf))
        dataset["status"] = "NEEDS_REFERENCE"
        dataset["reason"] = "Exact reference missing"
        datafile.write_text(json.dumps(dataset))
        blocked = load(manifest)
        assert blocked["status"] == "BLOCKED"
        rejects(lambda: fetch(blocked))
    print(
        "PASS: strict manifests, identities, collisions, reference checksums and output-aware cache"
    )


if __name__ == "__main__":
    main()
