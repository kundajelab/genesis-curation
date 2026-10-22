"""Pinned raw-read workflow adapters with isolated launch directories and common scoring."""

from __future__ import annotations

import csv
import gzip
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .metrics import assess, compare, tss_enrichment
from .reads import paired, subset
from .runtime import Runtime
from .spec import sha256, write_json


def validate_options(plan: dict[str, Any]) -> None:
    spec = plan["spec"]
    if spec["comparison"] != "workflow-configured":
        raise ValueError(
            "Implemented raw workflow adapters require workflow-configured comparison scope"
        )
    workflow = spec["workflow"]
    options = workflow["parameters"]
    allowed = {
        "aligner",
        "narrow_peak",
        "macs_fdr",
        "skip_trimming",
        "keep_dups",
        "keep_multi_map",
        "read_length",
        "custom_config_version",
        "mito_name",
    }
    if set(options) - allowed:
        raise ValueError("Unsupported workflow parameters; provide an explicit adapter")
    if spec["assay"] != "bulk-ATAC" or spec["parameters"]["spp"] or spec["parameters"]["quantify"]:
        raise ValueError("Raw ATAC adapters expose native QC and common fragment metrics only")
    if spec["adapter"] == "genesis-atac":
        if options or spec["parameters"]["duplicates"] != ["excluded"]:
            raise ValueError(
                "Genesis prototype has fixed duplicate exclusion and no external overrides"
            )
        if spec["parameters"]["keep_dup"] != "all" or spec["parameters"]["qvalue"] != 0.01:
            raise ValueError("Genesis prototype peak policy is fixed at q=0.01 / keep-dup all")
    else:
        required = allowed
        if set(options) != required:
            raise ValueError(
                "nf-core adapter requires all documented workflow parameters explicitly"
            )
        if options["aligner"] != "bwa":
            raise ValueError(
                "Initial nf-core adapter validates BWA; other aligners need locked adapters"
            )
        if any(
            type(options[k]) is not bool
            for k in ("narrow_peak", "skip_trimming", "keep_dups", "keep_multi_map")
        ):
            raise ValueError("Workflow switches must be booleans")
        if not options["skip_trimming"]:
            raise ValueError(
                "Trimming requires a separately validated adapter and adapter-evidence manifest"
            )
        expected_mapq = 0 if options["keep_multi_map"] else 1
        expected_dup = "marked" if options["keep_dups"] else "excluded"
        if spec["parameters"]["mapq"] != [expected_mapq] or spec["parameters"]["duplicates"] != [
            expected_dup
        ]:
            raise ValueError("Declared benchmark arm disagrees with effective nf-core filtering")
        if (
            spec["parameters"]["keep_dup"] != "all"
            or spec["parameters"]["qvalue"] != options["macs_fdr"]
        ):
            raise ValueError("Declared benchmark peak policy disagrees with nf-core")
        if not re.fullmatch(r"[0-9a-f]{40}", options["custom_config_version"]):
            raise ValueError("nf-core institutional configs must be pinned to an exact SHA")
        if type(options["read_length"]) is not int or options["read_length"] <= 0:
            raise ValueError("Explicit positive read length required")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", options["mito_name"]):
            raise ValueError("Explicit mitochondrial contig name required")
        if "gtf" not in plan["dataset"]["reference"]:
            raise ValueError("nf-core requires a versioned GTF asset")
    if "tss" not in plan["dataset"]["reference"]:
        raise ValueError("Common ATAC scoring requires a versioned TSS asset")


def configuration(runtime: Runtime, folder: Path, checkout: Path) -> Path:
    lock_path = Path(runtime.plan["spec"]["workflow"]["container_lock"])
    containers = json.loads(lock_path.read_text())
    if not containers:
        raise ValueError("Workflow container lock must not be empty")
    for name, image in containers.items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", name) or not re.fullmatch(
            r"(?:[a-zA-Z0-9_./:-]+@)?sha256:[0-9a-f]{64}", image
        ):
            raise ValueError("Invalid process name or unpinned image in container lock")
        resolved = subprocess.check_output(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{json .Id}} {{json .Architecture}} {{json .RepoDigests}}",
                image,
            ],
            text=True,
        ).strip()
        runtime.images[name] = {"requested": image, "resolved": resolved}
    # A catch-all selector enforces immutable images even when upstream uses tags.
    # Unknown processes fail closed instead of silently fetching an unpinned image.
    mapping = ",\n".join(f"'{k}': '{v}'" for k, v in sorted(containers.items()))
    limits = runtime.limits
    text = f"""def benchmarkContainers = [{mapping}]
process {{
  withName: '.*' {{
    cpus = {limits["cpus"]}
    memory = '{limits["memory_gb"]} GB'
    time = '{limits["seconds"]}s'
    container = {{
      def key = task.process.tokenize(':').last()
      if (!benchmarkContainers.containsKey(key))
        throw new IllegalArgumentException('Missing locked benchmark container for ' + key)
      benchmarkContainers[key]
    }}
  }}
}}
executor.cpus = {limits["cpus"]}
executor.memory = '{limits["memory_gb"]} GB'
docker.runOptions = '-u $(id -u):$(id -g) --pull=never ' +
    '--cpus {limits["cpus"]} --memory {limits["memory_gb"]}g'
trace {{
 enabled = true; overwrite = true; file = '{folder / "trace.tsv"}'
 fields = 'task_id,hash,name,status,exit,realtime,peak_rss,rchar,wchar,container,workdir'
}}
report {{ enabled = true; overwrite = true; file = '{folder / "nextflow-report.html"}' }}
timeline {{ enabled = true; overwrite = true; file = '{folder / "timeline.html"}' }}
dag {{ enabled = true; overwrite = true; file = '{folder / "dag.html"}' }}
"""
    if runtime.plan["spec"]["adapter"] == "nfcore-atac":
        text += "plugins { id 'nf-validation@1.1.4' }\n"
        text += (
            "params { skip_merge_replicates = true; skip_deseq2_qc = true; "
            "skip_peak_annotation = true; skip_plot_profile = true; "
            "skip_plot_fingerprint = true; skip_igv = true; "
            "save_align_intermeds = true }\n"
        )
    config = folder / "benchmark.config"
    if not config.exists() or config.read_text() != text:
        config.write_text(text)
    return config


def one(runtime: Runtime, lib: dict[str, Any], rep: int) -> dict[str, Any]:
    plan, spec = runtime.plan, runtime.plan["spec"]
    checkout = Path(spec["workflow"]["checkout"])
    folder = runtime.out / "workflows" / f"r{rep}-{lib['id']}"
    folder.mkdir(parents=True, exist_ok=True)
    published = folder / "published"
    config = configuration(runtime, folder, checkout)
    ref = plan["dataset"]["reference"]
    input_paths = [
        runtime.asset(lib["read1"]),
        runtime.asset(lib["read2"]),
        runtime.asset(ref["fasta"]),
        runtime.asset(ref["tss"]),
    ]
    # Verify complete paired inputs before execution; subsets are identical across adapters.
    cap = spec["parameters"].get("template_cap")
    staged = [folder / f"{lib['id']}_R{mate}.fastq.gz" for mate in (1, 2)]
    subset_record = folder / "subsampling.json"
    actual_templates = lib["raw_templates"]
    sampling: dict[str, Any]
    if cap is not None:
        if subset_record.exists():
            sampling = json.loads(subset_record.read_text())
            if any(
                not path.is_file() or sha256(path) != sampling["sha256"][str(path)]
                for path in staged
            ):
                raise ValueError("Cached FASTQ subset changed")
        else:
            sampling = dict(
                subset(
                    *input_paths[:2],
                    *staged,
                    templates=cap,
                    seed=spec["seed"],
                    expected=lib["raw_templates"],
                )
            )
            sampling["sha256"] = {str(path): sha256(path) for path in staged}
            write_json(subset_record, sampling)
        actual_templates = sampling["selected_templates"]
    else:
        count = sum(1 for _ in paired(*input_paths[:2]))
        if count != lib["raw_templates"]:
            raise ValueError("Declared raw template count disagrees with paired FASTQs")
        for path, source in zip(staged, input_paths[:2], strict=True):
            if not path.exists():
                shutil.copyfile(source, path)
            if sha256(path) != sha256(source):
                raise ValueError("Staged FASTQ differs from immutable source")
    if spec["adapter"] == "genesis-atac":
        entry = checkout / "experimental" / "atac" / "main.nf"
        base_config = entry.with_name("nextflow.config")
        args = [
            "--source_sha",
            spec["workflow"]["git_sha"],
            "--read1",
            str(staged[0]),
            "--read2",
            str(staged[1]),
            "--fasta",
            str(input_paths[2]),
            "--tss",
            str(input_paths[3]),
            "--outdir",
            str(published),
            "--mapq",
            str(spec["parameters"]["mapq"][0]),
            "--organelles",
            ",".join(ref["mitochondrial"] + ref["plastid"]) or "NONE",
            "--adapter_policy",
            "none-synthetic-adapter-free",
            "--genome_size",
            str(ref["genome_size"]),
        ]
    else:
        entry, base_config = checkout, checkout / "nextflow.config"
        sheet = folder / "samples.csv"
        with sheet.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["sample", "fastq_1", "fastq_2", "replicate"])
            writer.writerow([lib["id"], str(staged[0]), str(staged[1]), 1])
        reference = folder / "reference.fa"
        if not reference.exists():
            opener = gzip.open if input_paths[2].suffix == ".gz" else open
            with opener(input_paths[2], "rb") as source, reference.open("wb") as target:
                shutil.copyfileobj(source, target)
        if sha256(reference) != ref["fasta_content_sha256"]:
            raise ValueError("Staged reference identity mismatch")
        gtf = runtime.asset(ref["gtf"])
        input_paths.append(gtf)
        args = [
            "-profile",
            "docker",
            "--input",
            str(sheet),
            "--fasta",
            str(reference),
            "--gtf",
            str(gtf),
            "--macs_gsize",
            str(ref["genome_size"]),
            "--outdir",
            str(published),
            "--max_cpus",
            str(runtime.limits["cpus"]),
            "--max_memory",
            f"{runtime.limits['memory_gb']}.GB",
            "--max_time",
            f"{runtime.limits['seconds']}.s",
        ]
        for key, value in sorted(spec["workflow"]["parameters"].items()):
            args += ["--" + key, str(value).lower() if isinstance(value, bool) else str(value)]
    command = [
        "nextflow",
        "-C",
        f"{base_config},{config}",
        "run",
        str(entry),
        "-work-dir",
        str(folder / "work"),
        *args,
    ]
    if runtime.resume:
        # This launch directory belongs to exactly one workflow/library/repetition.
        command.append("-resume")
    runtime.command(
        f"r{rep}-{lib['id']}-workflow", command, folder, input_paths + [config, base_config]
    )
    with (folder / "trace.tsv").open() as stream:
        trace = list(csv.DictReader(stream, delimiter="\t"))
    if not trace or any(r["status"] not in {"COMPLETED", "CACHED"} for r in trace):
        raise ValueError("Missing or incomplete workflow trace")
    native = {}
    if spec["adapter"] == "genesis-atac":
        tasks = [r for r in trace if r["name"].split(" (")[0].split(":")[-1] == "FRAGMENTS"]
        if len(tasks) != 1:
            raise ValueError("Expected exactly one fragment-generation task")
        bam = Path(tasks[0]["workdir"]) / "usable.bam"
        peak = published / "peaks" / "atac_peaks.narrowPeak"
        native = json.loads((published / "metrics" / "enrichment.json").read_text())
    else:
        candidates = list(published.rglob("*.mLb.clN.sorted.bam"))
        peak_candidates = [
            p
            for p in published.rglob("*")
            if p.suffix in {".narrowPeak", ".broadPeak"} and "merged_library" in p.parts
        ]
        if len(candidates) != 1 or len(peak_candidates) != 1:
            raise ValueError(
                "Expected one library BAM and one peak set; ambiguous publication rejected"
            )
        bam, peak = candidates[0], peak_candidates[0]
        for path in published.rglob("*.ataqv.json"):
            native["ataqv"] = json.loads(path.read_text())
    if not bam.is_file() or not peak.is_file():
        raise ValueError("Required BAM/peak output missing despite workflow exit zero")
    # Preserve actual task scripts; their checksums and text verify effective commands.
    scripts = {}
    for row in trace:
        command_file = Path(row["workdir"]) / ".command.sh"
        if command_file.is_file():
            scripts[row["name"]] = {
                "sha256": sha256(command_file),
                "command": command_file.read_text(),
            }
        if row["container"] not in {v["requested"] for v in runtime.images.values()}:
            raise ValueError("Task used a container outside the immutable lock")
    verify_effective_commands(spec, scripts)
    write_json(folder / "executed-commands.json", scripts)
    qc_files = list(published.rglob("multiqc_data.json"))
    if len(qc_files) != 1 or len(list(published.rglob("*_fastqc.zip"))) != 2:
        raise ValueError("Missing machine-readable MultiQC or per-mate FastQC reports")
    parsed = json.loads(qc_files[0].read_text()).get("report_saved_raw_data", {})
    for family in ("fastqc", "samtools_stats", "samtools_flagstat", "samtools_idxstats"):
        if not any(family in name and values for name, values in parsed.items()):
            raise ValueError("Expected QC family missing: " + family)
    if len(parsed.get("multiqc_fastqc", {})) != 2:
        raise ValueError("FastQC mate identities collided or a mate is missing")
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        value = assess(
            bam,
            peak,
            peak,
            layout="PE",
            raw_templates=actual_templates,
            organelles=set(ref["mitochondrial"] + ref["plastid"]),
            temporary=Path(tmp),
        )
        sizes = {
            c: int(n)
            for c, n in (
                line.split("\t")
                for line in runtime.asset(ref["chrom_sizes"]).read_text().splitlines()
            )
        }
        value["tss"] = tss_enrichment(bam, input_paths[3], sizes, Path(tmp))
    value["native_metrics"] = native
    value["actual_templates"] = actual_templates
    value["multiqc_parsed_sha256"] = sha256(qc_files[0])
    value["artifact_roles"] = {"bam": str(bam), "peaks": str(peak)}
    value["artifacts"] = {str(p): sha256(p) for p in (bam, peak)}
    value["workflow_trace"] = trace
    value["comparison"] = compare(value, value)
    value["comparison_scope"] = (
        "own workflow baseline; cross-workflow comparison requires compare --against"
    )
    return value


def workflow(runtime: Runtime) -> list[dict[str, Any]]:
    validate_options(runtime.plan)
    if runtime.resume:
        verify_workflow_outputs(runtime.out)
    results = []
    if runtime.plan["spec"]["performance"]["random_order"]:
        raise ValueError("Randomized workflow scheduling is not supported by this adapter")
    for rep in range(1, runtime.plan["spec"]["performance"]["repetitions"] + 1):
        result = {
            "arm": runtime.plan["spec"]["baseline"],
            "repetition": rep,
            "status": "COMPLETE",
            "libraries": {},
        }
        for library in runtime.plan["dataset"]["libraries"]:
            result["libraries"][library["id"]] = one(runtime, library, rep)
        results.append(result)
    return results


def verify_effective_commands(spec: dict[str, Any], scripts: dict[str, Any]) -> None:
    callers = [
        v["command"]
        for key, v in scripts.items()
        if key.split(" (")[0].split(":")[-1] in ("MACS2_CALLPEAK", "PEAKS")
    ]
    if len(callers) != 1:
        raise ValueError("Expected exactly one inspectable peak-calling command")
    text = callers[0].replace("\\\n", " ")
    qvalue = re.search(r"(?:^|\s)(?:-q|--qvalue)\s+([0-9.eE+-]+)", text)
    keep = re.search(r"--keep-dup\s+(\w+)", text)
    fmt = re.search(r"(?:^|\s)(?:-f|--format)\s+(\w+)", text)
    if not qvalue or float(qvalue[1]) != spec["parameters"]["qvalue"]:
        raise ValueError("Peak command did not apply the declared q-value")
    if not keep or keep[1] != spec["parameters"]["keep_dup"] or not fmt or fmt[1] != "BAMPE":
        raise ValueError("Peak command did not apply duplicate/fragment-format policy")
    if spec["adapter"] == "nfcore-atac":
        narrow = spec["workflow"]["parameters"]["narrow_peak"]
        if ("--broad" in text) == narrow:
            raise ValueError("Peak-calling mode differs from declared narrow/broad policy")
        filters = [v["command"] for key, v in scripts.items() if "BAMTOOLS_FILTER" in key]
        if len(filters) != 1:
            raise ValueError("Expected one inspectable alignment-filter command")
        cutoff = re.search(r"(?:^|\s)-q\s+(\d+)", filters[0])
        effective_mapq = int(cutoff[1]) if cutoff else 0
        if effective_mapq != spec["parameters"]["mapq"][0]:
            raise ValueError("Alignment command did not apply the declared MAPQ policy")


def verify_workflow_outputs(out: Path) -> None:
    """Nextflow cache existence alone cannot certify unchanged scientific products."""
    records = [out / "results.json", *sorted((out / "invocations").glob("*.json"))]
    expected = {}
    for previous in records:
        if not previous.is_file():
            continue
        value = json.loads(previous.read_text())
        for arm in value.get("results", []):
            for library in arm.get("libraries", {}).values():
                for filename, checksum in library.get("artifacts", {}).items():
                    if filename in expected and expected[filename] != checksum:
                        raise ValueError(
                            "Scientific output history contains inconsistent checksums"
                        )
                    expected[filename] = checksum
    for filename, checksum in expected.items():
        path = Path(filename)
        if not path.is_file() or sha256(path) != checksum:
            raise ValueError(
                "Workflow cached scientific artifact changed; use a fresh output directory"
            )
