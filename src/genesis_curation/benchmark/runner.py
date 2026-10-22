"""Isolated post-alignment sensitivity runner and workflow adapter dispatch."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import random
import resource
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pysam

from .metrics import (
    alignment_identity,
    assess,
    compare,
    compare_quantification,
    filter_bam,
    tss_enrichment,
)
from .runtime import Runtime
from .spec import sha256, source_identity, write_json


def verify_reference(plan: dict[str, Any]) -> None:
    ref = plan["dataset"]["reference"]
    fasta = Path(plan["assets"][ref["fasta"]]["path"])
    digest = hashlib.sha256()
    opener = gzip.open if fasta.suffix == ".gz" else open
    with opener(fasta, "rb") as binary_stream:
        for block in iter(lambda: binary_stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != ref["fasta_content_sha256"]:
        raise ValueError("Decompressed FASTA identity mismatch")
    sizes = {}
    with pysam.FastxFile(str(fasta)) as stream:
        for record in stream:
            if record.name in sizes:
                raise ValueError("Duplicate FASTA sequence ID")
            sequence = record.sequence
            if sequence is None:
                raise ValueError("FASTA record has no sequence")
            sizes[record.name] = len(sequence)
    declared = {}
    for line in Path(plan["assets"][ref["chrom_sizes"]]["path"]).read_text().splitlines():
        chrom, length = line.split("\t")
        if chrom in declared:
            raise ValueError("Duplicate chromosome-size ID")
        declared[chrom] = int(length)
    if sizes != declared or sum(sizes.values()) != ref["genome_size"]:
        raise ValueError("Reference FASTA/chromosome sizes/genome length disagree")
    if set(ref["mitochondrial"] + ref["plastid"]) - sizes.keys():
        raise ValueError("Declared organellar contig absent from reference")
    for library in plan["dataset"]["libraries"]:
        for key in ("bam", "marked_bam", "spp_bam", "spp_marked_bam"):
            if key in library:
                with pysam.AlignmentFile(plan["assets"][library[key]]["path"]) as bam:
                    if dict(zip(bam.references, bam.lengths, strict=True)) != sizes:
                        raise ValueError(
                            f"BAM/reference sequence dictionary mismatch: {library['id']}"
                        )
                    if key.startswith("spp_") and any(
                        read.query_length > 50 or read.is_paired for read in bam
                    ):
                        raise ValueError(
                            "SPP input must contain independently aligned first-50-bp reads"
                        )


def verify_marked(runtime: Runtime) -> None:
    for lib in runtime.plan["dataset"]["libraries"]:
        for original, marked in (("bam", "marked_bam"), ("spp_bam", "spp_marked_bam")):
            if marked in lib:
                with tempfile.TemporaryDirectory(dir=runtime.out) as tmp:
                    root = Path(tmp)
                    if alignment_identity(runtime.asset(lib[original]), root) != alignment_identity(
                        runtime.asset(lib[marked]), root
                    ):
                        raise ValueError(
                            "Marked BAM changed alignment records, read sequences or qualities"
                        )
                runtime.check()


def prepare(
    runtime: Runtime, folder: Path, lib: dict[str, Any], arm: dict[str, Any], *, spp: bool = False
) -> Path:
    kind = "spp" if spp else "primary"
    target = folder / lib["id"] / kind
    target.mkdir(parents=True, exist_ok=True)
    key = "spp_bam" if spp else "bam"
    if arm["duplicates"] != "retained":
        key = "spp_marked_bam" if spp else "marked_bam"
    source = runtime.asset(lib[key])
    ref = runtime.plan["dataset"]["reference"]
    organelles = (
        set(ref["mitochondrial"] + ref["plastid"])
        if runtime.plan["spec"]["assay"] == "bulk-ATAC"
        else set()
    )
    recipe = {
        "arm": arm,
        "layout": "SE" if spp else lib["layout"],
        "organelles": sorted(organelles),
        "representation": "independent-first50-R1" if spp else "primary",
    }
    output = target / "selected.bam"
    inputs = [source]
    if not runtime.cached(target, inputs, recipe):
        started = time.monotonic()
        counts = filter_bam(
            source,
            output,
            layout=recipe["layout"],
            mapq=arm["mapq"],
            exclude_duplicates=arm["duplicates"] == "excluded",
            organelles=organelles,
        )
        raw_ends = lib["raw_templates"] * (2 if recipe["layout"] == "PE" else 1)
        counts.update(
            raw_read_ends=raw_ends,
            raw_templates=lib["raw_templates"],
            raw_mapping_fraction=counts.get("mapped_primary_read_ends", 0) / raw_ends,
            post_policy_read_retention=counts.get("retained_read_ends", 0) / raw_ends,
        )
        write_json(target / "selection.json", counts)
        runtime.seal(
            target,
            inputs,
            [output, output.with_suffix(".bam.bai"), target / "selection.json"],
            recipe,
            elapsed=time.monotonic() - started,
        )
    return output


def peaks(runtime: Runtime, folder: Path, lib: dict[str, Any], bams: dict[str, Path]) -> Path:
    target = folder / lib["id"] / "peaks"
    target.mkdir(parents=True, exist_ok=True)
    bam = bams[lib["id"]]
    inputs = [bam]
    params = runtime.plan["spec"]["parameters"]
    recipe = {
        "qvalue": params["qvalue"],
        "keep_dup": params["keep_dup"],
        "layout": lib["layout"],
        "genome_size": runtime.plan["dataset"]["reference"]["genome_size"],
    }
    name = lib["id"] + ".macs3"
    output = target / f"{name}_peaks.narrowPeak"
    if not runtime.cached(
        target, inputs + ([bams[lib["control"]]] if lib["control"] else []), recipe
    ):
        command = ["macs3", "callpeak", "-t", str(bam)]
        if lib["control"]:
            inputs.append(bams[lib["control"]])
            command += ["-c", str(inputs[-1])]
        command += [
            "-f",
            "BAMPE" if lib["layout"] == "PE" else "BAM",
            "-g",
            str(recipe["genome_size"]),
            "-n",
            name,
            "-q",
            str(params["qvalue"]),
            "--keep-dup",
            params["keep_dup"],
        ]
        runtime.command(
            folder.name + "-" + lib["id"] + "-peaks", command, target, inputs, image="peaks"
        )
        runtime.seal(target, inputs, [output], recipe)
    return output


def spp(runtime: Runtime, folder: Path, lib: dict[str, Any], bam: Path) -> dict[str, Any]:
    target = folder / lib["id"] / "spp-result"
    target.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[3]
    driver = repo / "benchmarks" / "spp.sh"
    script = repo / "vendor" / "phantompeakqualtools" / "run_spp.R"
    inputs = [bam, driver, script]
    recipe = {"representation": "first50-R1", "shift_range": "0:2:400"}
    result = target / "spp.tsv"
    if not runtime.cached(target, inputs, recipe):
        runtime.command(
            folder.name + "-" + lib["id"] + "-spp",
            ["bash", str(driver), str(bam), str(script), str(runtime.limits["cpus"])],
            target,
            inputs,
            image="qc",
        )
        runtime.seal(target, inputs, [result, target / "spp.pdf"], recipe)
    fields = result.read_text().strip().split("\t")
    if len(fields) != 11:
        raise ValueError("SPP output must have eleven fields")
    return {
        "raw_fields": fields,
        "NSC": fields[8],
        "RSC": fields[9],
        "fragment_lengths": fields[2],
        "definition": (
            "phantompeakqualtools on independently aligned first50 R1; no biological threshold"
        ),
    }


def quantify(
    runtime: Runtime, folder: Path, lib: dict[str, Any], bam: Path, own: Path, fixed: Path
) -> dict[str, str]:
    target = folder / lib["id"] / "quantification"
    target.mkdir(parents=True, exist_ok=True)
    sizes = runtime.asset(runtime.plan["dataset"]["reference"]["chrom_sizes"])
    coverage = target / "coverage.RPKM.bedGraph"
    outputs = [
        target / f"{region}.{unit}.tsv" for region in ("own", "fixed") for unit in ("RPM", "RPKM")
    ]
    inputs = [bam, own, fixed, sizes]
    recipe = {"coverage": "RPKM/exactScaling/bin1", "weighting": "primary"}
    if not runtime.cached(target, inputs, recipe):
        runtime.command(
            folder.name + "-" + lib["id"] + "-coverage",
            [
                "bamCoverage",
                "-b",
                str(bam),
                "-o",
                str(coverage),
                "--outFileFormat",
                "bedgraph",
                "--binSize",
                "1",
                "--normalizeUsing",
                "RPKM",
                "--exactScaling",
                "-p",
                str(runtime.limits["cpus"]),
            ],
            target,
            [bam],
            image="tracks",
        )
        for region, bed in (("own", own), ("fixed", fixed)):
            runtime.command(
                folder.name + "-" + lib["id"] + "-quantify-" + region,
                [
                    "genesis-tools",
                    "quantify",
                    "--bed",
                    str(bed),
                    "--bam",
                    str(bam),
                    "--coverage",
                    str(coverage),
                    "--chrom-sizes",
                    str(sizes),
                    "--weighting",
                    "primary",
                    "--threads",
                    str(runtime.limits["cpus"]),
                    "--rpm-output",
                    str(target / f"{region}.RPM.tsv"),
                    "--rpkm-output",
                    str(target / f"{region}.RPKM.tsv"),
                ],
                target,
                inputs + [coverage],
                image="tools",
            )
        runtime.seal(target, inputs, outputs + [coverage], recipe)
    return {p.name: str(p) for p in outputs}


def versions(runtime: Runtime) -> None:
    commands = {"peaks": ["macs3", "--version"]}
    if runtime.plan["spec"]["parameters"]["quantify"]:
        commands["tracks"] = ["bamCoverage", "--version"]
        commands["tools"] = [
            "python",
            "-c",
            "import importlib.metadata as m; print('genesis-tools',m.version('genesis-tools')); "
            "print('pysam',m.version('pysam'))",
        ]
    if runtime.plan["spec"]["parameters"]["spp"]:
        commands["qc"] = [
            "Rscript",
            "-e",
            "cat(R.version.string, '\\n'); cat('spp', as.character(packageVersion('spp')), '\\n')",
        ]
    folder = runtime.out / "versions"
    folder.mkdir(exist_ok=True)
    recipe = {key: runtime.plan["spec"]["images"][key] for key in commands}
    if not runtime.cached(folder, [], recipe):
        for role, command in commands.items():
            runtime.command("versions-" + role, command, folder, [], image=role)
        write_json(folder / "images.json", runtime.images)
        runtime.seal(folder, [], [folder / "images.json"], recipe)
    else:
        runtime.images.update(json.loads((folder / "images.json").read_text()))


def postalign(runtime: Runtime) -> list[dict[str, Any]]:
    plan = runtime.plan
    versions(runtime)
    verify_marked(runtime)
    libraries = plan["dataset"]["libraries"]
    treatments = [
        lib for lib in libraries if lib["control"] or plan["spec"]["assay"] == "bulk-ATAC"
    ]
    if not treatments:
        raise ValueError("No treatment libraries to evaluate")
    perf = plan["spec"]["performance"]
    jobs = [(rep, arm) for rep in range(1, perf["repetitions"] + 1) for arm in plan["arms"]]
    if perf["random_order"]:
        random.Random(plan["spec"]["seed"]).shuffle(jobs)
    results: list[dict[str, Any]] = []
    completed: dict[tuple[int, str], dict[str, Any]] = {}
    for rep, arm in jobs:
        folder = runtime.out / "arms" / f"r{rep}-{arm['id']}"
        item: dict[str, Any] = {"arm": arm["id"], "repetition": rep, "status": "FAILED"}
        try:
            bams = {lib["id"]: prepare(runtime, folder, lib, arm) for lib in libraries}
            called = {lib["id"]: peaks(runtime, folder, lib, bams) for lib in treatments}
            spp_results = {}
            if plan["spec"]["parameters"]["spp"]:
                for lib in libraries:
                    shortened = prepare(runtime, folder, lib, arm, spp=True)
                    spp_results[lib["id"]] = spp(runtime, folder, lib, shortened)
            completed[(rep, arm["id"])] = {
                "bams": bams,
                "peaks": called,
                "folder": folder,
                "spp": spp_results,
            }
            item["status"] = "PREPARED"
            item["library_qc"] = {
                lib["id"]: {
                    "primary": json.loads(
                        (folder / lib["id"] / "primary" / "selection.json").read_text()
                    ),
                    "spp": spp_results.get(lib["id"]),
                }
                for lib in libraries
            }
        except (ValueError, OSError, pysam.SamtoolsError) as error:
            item["reason"] = str(error)
        results.append(item)
        write_json(runtime.out / "progress.json", results)
    baseline_key = (1, plan["spec"]["baseline"])
    if baseline_key not in completed:
        raise ValueError("Baseline failed; fixed-region comparisons cannot proceed")
    ref = plan["dataset"]["reference"]
    for result in results:
        key = result["repetition"], result["arm"]
        if key not in completed:
            continue
        item = completed[key]
        try:
            evaluated = {}
            for lib in treatments:
                sample = lib["id"]
                fixed = completed[baseline_key]["peaks"][sample]
                folder = item["folder"] / sample / "evaluation"
                folder.mkdir(parents=True, exist_ok=True)
                inputs = [item["bams"][sample], item["peaks"][sample], fixed]
                recipe = {
                    "metric_version": 1,
                    "fixed_regions_sha256": sha256(fixed),
                    "spp": item["spp"].get(sample),
                }
                output = folder / "metrics.json"
                if not runtime.cached(folder, inputs, recipe):
                    started = time.monotonic()
                    with tempfile.TemporaryDirectory(dir=folder) as temp:
                        value = assess(
                            *inputs,
                            layout=lib["layout"],
                            raw_templates=lib["raw_templates"],
                            organelles=set(ref["mitochondrial"] + ref["plastid"]),
                            temporary=Path(temp),
                        )
                    if plan["spec"]["assay"] == "bulk-ATAC" and "tss" in ref:
                        with tempfile.TemporaryDirectory(dir=folder) as tss_tmp:
                            sizes = {
                                c: int(n)
                                for c, n in (
                                    line.split("\t")
                                    for line in runtime.asset(ref["chrom_sizes"])
                                    .read_text()
                                    .splitlines()
                                )
                            }
                            value["tss"] = tss_enrichment(
                                inputs[0], runtime.asset(ref["tss"]), sizes, Path(tss_tmp)
                            )
                    value["artifact_roles"] = {
                        "bam": str(inputs[0]),
                        "peaks": str(inputs[1]),
                        "fixed_peaks": str(inputs[2]),
                    }
                    value["artifacts"] = {str(p): sha256(p) for p in inputs}
                    value["spp"] = item["spp"].get(sample)
                    write_json(output, value)
                    runtime.seal(
                        folder, inputs, [output], recipe, elapsed=time.monotonic() - started
                    )
                value = json.loads(output.read_text())
                if plan["spec"]["parameters"]["quantify"]:
                    value["quantification"] = quantify(runtime, item["folder"], lib, *inputs)
                evaluated[sample] = value
            result.update(status="COMPLETE", libraries=evaluated)
        except (ValueError, OSError, pysam.SamtoolsError) as error:
            result.update(status="FAILED", reason=str(error))
    baselines = next(r for r in results if (r["repetition"], r["arm"]) == baseline_key)
    if baselines["status"] != "COMPLETE":
        raise ValueError("Baseline evaluation failed")
    for result in results:
        if result["status"] == "COMPLETE":
            for sample, value in result["libraries"].items():
                baseline_value = baselines["libraries"][sample]
                value["comparison"] = compare(value, baseline_value)
                if "quantification" in value:
                    value["quantification_comparison"] = {
                        unit: compare_quantification(
                            Path(value["quantification"][f"fixed.{unit}.tsv"]),
                            Path(baseline_value["quantification"][f"fixed.{unit}.tsv"]),
                        )
                        for unit in ("RPM", "RPKM")
                    }
    return results


def run(plan: dict[str, Any], out: Path, *, resume: bool = False) -> None:
    if plan["blockers"]:
        raise ValueError("BLOCKED: " + "; ".join(plan["blockers"]))
    out = out.resolve()
    if any(
        out == Path(a["path"]) or out in Path(a["path"]).parents for a in plan["assets"].values()
    ):
        raise ValueError("Campaign output must not contain original input assets")
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".running"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(descriptor, str(os.getpid()).encode())
    os.close(descriptor)
    try:
        run_locked(plan, out, resume=resume)
    finally:
        lock.unlink(missing_ok=True)


def run_locked(plan: dict[str, Any], out: Path, *, resume: bool) -> None:
    """Initialize persistent state only while holding the campaign's writer lock."""
    runtime = Runtime(plan, out, resume=resume)
    started = time.monotonic()
    previous_affinity = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else None
    result: dict[str, Any] = {"status": "FAILED"}
    try:
        # Bound local Python work as well as Docker tasks; never increase existing limits.
        previous = resource.getrlimit(resource.RLIMIT_AS)
        ceiling = runtime.limits["memory_gb"] * 1024**3
        if previous[0] != resource.RLIM_INFINITY:
            ceiling = min(ceiling, previous[0])
        if plan["spec"]["adapter"] in {"postalign", "methods"}:
            resource.setrlimit(resource.RLIMIT_AS, (ceiling, previous[1]))
        verify_reference(plan)
        if plan["spec"]["adapter"] == "postalign":
            values = postalign(runtime)
        elif plan["spec"]["adapter"] == "methods":
            from .methods import execute

            values = execute(runtime)
        else:
            from .workflows import workflow

            values = workflow(runtime)
        if source_identity()["recipe_sha256"] != plan["source"]["recipe_sha256"]:
            raise ValueError("Benchmark source changed during execution; results are not certified")
        unchanged = all(sha256(Path(a["path"])) == a["sha256"] for a in plan["assets"].values())
        if not unchanged:
            raise ValueError("Original input artifact changed during execution")
        result = {
            "status": "COMPLETE"
            if all(v["status"] == "COMPLETE" for v in values)
            else "PARTIAL_FAILURE",
            "results": values,
            "inputs_unchanged": unchanged,
            "resume": resume,
        }
        write_json(out / "results.json", result)
        from .reporting import report

        report(out)
        if result["status"] != "COMPLETE":
            raise ValueError("Some experiment arms failed; preserved results contain their reasons")
    except BaseException as error:
        result["error"] = str(error)
        if result["status"] != "PARTIAL_FAILURE":
            result["status"] = "FAILED"
        raise
    finally:
        result.update(
            wall_seconds=time.monotonic() - started,
            events=runtime.events,
            images=runtime.images,
            python=sys.version,
            pysam=pysam.__version__,
            htslib=pysam.__samtools_version__,
            architecture=platform.machine(),
            resume=resume,
            driver_peak_rss={
                "value": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "unit": "KiB" if sys.platform.startswith("linux") else "platform-native",
                "scope": "Python driver lifetime high-water RSS; excludes child tools; "
                "not whole-host memory",
                "measurement": "getrusage(RUSAGE_SELF).ru_maxrss",
            },
        )
        attempts = out / "invocations"
        attempts.mkdir(exist_ok=True)
        write_json(attempts / f"{time.time_ns()}.json", result)
        write_json(out / "results.json", result)
        resource.setrlimit(resource.RLIMIT_AS, previous)
        if previous_affinity is not None:
            os.sched_setaffinity(0, previous_affinity)
    print(f"COMPLETE: {out / 'report.html'}")
