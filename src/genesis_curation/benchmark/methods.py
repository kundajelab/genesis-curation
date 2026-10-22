"""Version-2 component comparisons using locked, separate native tool environments."""

from __future__ import annotations

import gzip
import json
import os
import random
import shlex
import tempfile
import time
from pathlib import Path
from typing import Any

import pysam

from .metrics import assess, compare, metric, tss_enrichment
from .runtime import Runtime
from .spec import SHA, identifier, keys, sha256, write_json

TOOLS = {
    "bwa-mem2",
    "bwa",
    "bowtie2",
    "samtools",
    "macs3",
    "Genrich",
    "cutadapt",
    "fastp",
    "picard",
    "bedtools",
    "bowtie2-build",
}


def validate_methods(spec: dict[str, Any], base: Path) -> list[dict[str, Any]]:
    env = spec["environment"]
    required = {"prefix", "legacy_prefix", "lock", "lock_sha256"}
    keys(env, required | {"bwa_mem2_221"}, required, "environment")
    for name in ("prefix", "legacy_prefix", "lock"):
        env[name] = str((base / env[name]).resolve())
    if not SHA.fullmatch(env["lock_sha256"]) or sha256(Path(env["lock"])) != env["lock_sha256"]:
        raise ValueError("Benchmark environment lock differs")
    if "bwa_mem2_221" in env:
        pinned = env["bwa_mem2_221"]
        keys(
            pinned,
            {"prefix", "lock", "lock_sha256"},
            {"prefix", "lock", "lock_sha256"},
            "bwa_mem2_221",
        )
        for name in ("prefix", "lock"):
            pinned[name] = str((base / pinned[name]).resolve())
        if (
            not SHA.fullmatch(pinned["lock_sha256"])
            or sha256(Path(pinned["lock"])) != pinned["lock_sha256"]
        ):
            raise ValueError("BWA-MEM2 2.2.1 comparison lock differs")
        records = list((Path(pinned["prefix"]) / "conda-meta").glob("bwa-mem2-*.json"))
        if len(records) != 1 or json.loads(records[0].read_text())["version"] != "2.2.1":
            raise ValueError("The older-package comparison requires BWA-MEM2 2.2.1")
    if len(spec["parameters"]["mapq"]) != 1 or len(spec["parameters"]["duplicates"]) != 1:
        raise ValueError("Compare packages separately from MAPQ/duplicate policy matrices")
    if spec["parameters"]["spp"] or spec["parameters"]["quantify"]:
        raise ValueError(
            "Method comparisons use common metrics; legacy SPP/quantify flags must be false"
        )
    if "template_cap" in spec["parameters"]:
        raise ValueError("Method comparisons require previously checksummed sampled inputs")
    if not isinstance(spec["methods"], list) or not spec["methods"]:
        raise ValueError("Nonempty methods list required")
    methods = []
    for method in spec["methods"]:
        fields = {
            "id",
            "aligner",
            "trimmer",
            "duplicate_tool",
            "caller",
            "adapter_r1",
            "adapter_r2",
        }
        keys(method, fields, fields, "method")
        identifier(method["id"])
        choices = {
            "aligner": {"bwa-mem2", "bwa-mem2-2.2.1", "bwa", "bowtie2"},
            "trimmer": {"none", "cutadapt", "fastp"},
            "duplicate_tool": {"none", "samtools", "picard"},
            "caller": {"macs3", "macs2", "genrich"},
        }
        for name, allowed in choices.items():
            if method[name] not in allowed:
                raise ValueError(f"Unsupported method {name}")
        if method["aligner"] == "bwa-mem2-2.2.1" and "bwa_mem2_221" not in env:
            raise ValueError("BWA-MEM2 2.2.1 requires its separately pinned environment")
        for key in ("adapter_r1", "adapter_r2"):
            value = method[key]
            if not isinstance(value, str) or (
                value != "-" and (not value or set(value) - set("ACGT"))
            ):
                raise ValueError("Adapter must be an explicit ACGT sequence or '-'")
        if method["trimmer"] == "none" and (method["adapter_r1"], method["adapter_r2"]) != (
            "-",
            "-",
        ):
            raise ValueError("Untrimmed method must use '-' adapters")
        if method["trimmer"] != "none" and "-" in (method["adapter_r1"], method["adapter_r2"]):
            raise ValueError("Trimmed methods require both declared adapters")
        if spec["assay"] == "DAP-seq" and (
            method["trimmer"] != "none" or method["caller"] == "genrich"
        ):
            raise ValueError("DAP trimming and Genrich DAP calling are outside this comparison")
        if spec["assay"] == "bulk-ATAC" and method["duplicate_tool"] == "none":
            raise ValueError("ATAC comparisons require a duplicate marker")
        if spec["parameters"]["duplicates"] == ["excluded"] and method["duplicate_tool"] == "none":
            raise ValueError("Duplicate exclusion requires marking")
        methods.append(method)
    if len({m["id"] for m in methods}) != len(methods):
        raise ValueError("Duplicate method IDs")
    # Bind the actual installed binaries/package records, not just a claimed version.
    binaries = {name: Path(env["prefix"]) / "bin" / name for name in TOOLS}
    binaries["macs2"] = Path(env["legacy_prefix"]) / "bin/macs2"
    prefixes = [env["prefix"], env["legacy_prefix"]]
    if "bwa_mem2_221" in env:
        prefix = env["bwa_mem2_221"]["prefix"]
        prefixes.append(prefix)
        binaries["bwa-mem2-2.2.1"] = Path(prefix) / "bin/bwa-mem2"
    env["executables"] = {}
    for name, binary in binaries.items():
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError(
                f"Missing benchmark tool {name}; install the locked benchmark environment"
            )
        env["executables"][name] = {"path": str(binary), "sha256": sha256(binary)}
    env["package_records"] = {
        str(p): sha256(p)
        for prefix in prefixes
        for p in sorted((Path(prefix) / "conda-meta").glob("*.json"))
    }
    env["compiled_components"] = {
        str(p): sha256(p)
        for p in [
            *(p for prefix in prefixes for p in sorted((Path(prefix) / "bin").glob("bwa-mem2.*"))),
            *sorted(Path(env["legacy_prefix"]).glob("lib/python*/site-packages/MACS2/**/*.so")),
            *sorted(
                Path(env["legacy_prefix"]).glob(
                    "lib/python*/site-packages/MACS2-*.dist-info/RECORD"
                )
            ),
        ]
        if p.is_file()
    }
    return methods


class Methods:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime
        self.spec = runtime.plan["spec"]
        self.ref = runtime.plan["dataset"]["reference"]
        self.env = self.spec["environment"]
        self.root = runtime.out
        self.cpu = runtime.limits["cpus"]
        self.native = {
            "PATH": self.env["prefix"] + "/bin:" + os.environ.get("PATH", ""),
            "OMP_NUM_THREADS": str(self.cpu),
            "OPENBLAS_NUM_THREADS": "1",
            "JAVA_TOOL_OPTIONS": (
                f"-Xmx{min(8, max(1, runtime.limits['memory_gb'] // 2))}g "
                f"-XX:ActiveProcessorCount={self.cpu} "
                "-XX:CompressedClassSpaceSize=128m -XX:ReservedCodeCacheSize=128m"
            ),
        }

    def tool(self, name: str) -> str:
        return self.env["executables"][name]["path"]

    def task(
        self, folder: Path, inputs: list[Path], outputs: list[Path], commands: list[list[str]]
    ) -> None:
        recipe = {"commands": commands, "environment": self.env}
        if self.rt.cached(folder, inputs, recipe):
            return
        folder.mkdir(parents=True, exist_ok=True)
        for index, command in enumerate(commands):
            label = str(folder.relative_to(self.root)).replace("/", "-") + f"-{index}"
            self.rt.command(label, command, folder, inputs, env=self.native)
        self.rt.seal(folder, inputs, outputs, recipe)

    def script(self, commands: list[list[str]], *, pipe: bool = False) -> list[str]:
        separator = " | " if pipe else "\n"
        return [
            "bash",
            "-c",
            "set -euo pipefail\n" + separator.join(shlex.join(c) for c in commands),
        ]

    def reference(self, aligner: str) -> Path:
        source = self.rt.asset(self.ref["fasta"])
        folder = self.root / "indexes" / aligner
        fasta = folder / "genome.fa"
        folder.mkdir(parents=True, exist_ok=True)
        if not fasta.exists():
            opener = gzip.open if source.suffix == ".gz" else open
            with opener(source, "rb") as stream, fasta.open("xb") as target:
                import shutil

                shutil.copyfileobj(stream, target, 1024 * 1024)
        if sha256(fasta) != self.ref["fasta_content_sha256"]:
            raise ValueError("Index FASTA identity changed")
        if aligner == "bowtie2":
            command = [
                str(Path(self.env["prefix"]) / "bin/bowtie2-build"),
                "--threads",
                str(self.cpu),
                str(fasta),
                str(fasta),
            ]
            expected = [
                Path(str(fasta) + s)
                for s in (".1.bt2", ".2.bt2", ".3.bt2", ".4.bt2", ".rev.1.bt2", ".rev.2.bt2")
            ]
        else:
            command = [self.tool(aligner), "index", str(fasta)]
            suffixes = (
                (".0123", ".amb", ".ann", ".bwt.2bit.64", ".pac")
                if aligner in {"bwa-mem2", "bwa-mem2-2.2.1"}
                else (".amb", ".ann", ".bwt", ".pac", ".sa")
            )
            expected = [Path(str(fasta) + s) for s in suffixes]
        self.task(folder, [source], [fasta, *expected], [command])
        return fasta

    def reads(self, folder: Path, lib: dict[str, Any], method: dict[str, Any]) -> list[Path]:
        reads = [self.rt.asset(lib[k]) for k in ("read1", "read2") if k in lib]
        if method["trimmer"] == "none":
            return reads
        target = folder / lib["id"] / "trim"
        output = [target / "R1.fastq.gz", target / "R2.fastq.gz"]
        if method["trimmer"] == "cutadapt":
            command = [
                self.tool("cutadapt"),
                "-j",
                str(self.cpu),
                "-a",
                method["adapter_r1"],
                "-A",
                method["adapter_r2"],
                "--json",
                str(target / "trim.json"),
                "-o",
                str(output[0]),
                "-p",
                str(output[1]),
                *map(str, reads),
            ]
        else:
            # Disable unrelated fastp filters/corrections. Overlap-based adapter
            # detection remains part of fastp's algorithm and is reported as such.
            command = [
                self.tool("fastp"),
                "--thread",
                str(self.cpu),
                "--disable_quality_filtering",
                "--disable_length_filtering",
                "--disable_trim_poly_g",
                "--adapter_sequence",
                method["adapter_r1"],
                "--adapter_sequence_r2",
                method["adapter_r2"],
                "--in1",
                str(reads[0]),
                "--in2",
                str(reads[1]),
                "--out1",
                str(output[0]),
                "--out2",
                str(output[1]),
                "--json",
                str(target / "trim.json"),
                "--html",
                str(target / "trim.html"),
            ]
        self.task(target, reads, [*output, target / "trim.json"], [command])
        return output

    def align(self, folder: Path, lib: dict[str, Any], method: dict[str, Any], fasta: Path) -> Path:
        reads = self.reads(folder, lib, method)
        target = folder / lib["id"] / "alignment"
        bam = target / "aligned.bam"
        aligner = method["aligner"]
        if aligner == "bowtie2":
            command = [
                self.tool(aligner),
                "--very-sensitive-local",
                "-p",
                str(self.cpu),
                "-x",
                str(fasta),
            ]
            command += (
                ["-1", str(reads[0]), "-2", str(reads[1]), "-X", "2000"]
                if len(reads) == 2
                else ["-U", str(reads[0])]
            )
            if self.spec["assay"] == "bulk-ATAC":
                command += ["--dovetail", "--no-mixed", "--no-discordant"]
            command += ["--rg-id", lib["id"], "--rg", "SM:" + lib["id"], "--rg", "LB:" + lib["id"]]
        else:
            command = [self.tool(aligner), "mem", "-t", str(self.cpu)]
            if self.spec["assay"] == "DAP-seq":
                command += ["-K", "10000000", "-k", "19", "-c", "10000", "-T", "30"]
            command += [
                "-R",
                "@RG\\tID:" + lib["id"] + "\\tSM:" + lib["id"] + "\\tLB:" + lib["id"],
                str(fasta),
                *map(str, reads),
            ]
        self.task(
            target,
            [fasta, *reads],
            [bam],
            [
                self.script(
                    [command, [self.tool("samtools"), "sort", "-m", "128M", "-o", str(bam)]],
                    pipe=True,
                )
            ],
        )
        return bam

    def selected(
        self, folder: Path, lib: dict[str, Any], method: dict[str, Any], bam: Path
    ) -> Path:
        target = folder / lib["id"] / "selection"
        target.mkdir(parents=True, exist_ok=True)
        marked = target / "marked.bam"
        st = self.tool("samtools")
        if method["duplicate_tool"] == "samtools":
            commands = [
                [st, "sort", "-n", "-m", "128M", "-o", str(target / "names.bam"), str(bam)],
                [st, "fixmate", "-m", str(target / "names.bam"), str(target / "fixed.bam")],
                [
                    st,
                    "sort",
                    "-m",
                    "128M",
                    "-o",
                    str(target / "coordinate.bam"),
                    str(target / "fixed.bam"),
                ],
                [st, "markdup", "-s", str(target / "coordinate.bam"), str(marked)],
            ]
            self.task(target / "mark", [bam], [marked], commands)
            # Only marked.bam is a cached output. These owned sort/fixmate products
            # are temporary and would otherwise multiply storage on plant genomes.
            for name in ("names.bam", "fixed.bam", "coordinate.bam"):
                (target / name).unlink(missing_ok=True)
        elif method["duplicate_tool"] == "picard":
            command = [
                self.tool("picard"),
                "MarkDuplicates",
                f"I={bam}",
                f"O={marked}",
                f"M={target / 'duplicates.txt'}",
                "REMOVE_DUPLICATES=false",
                "ASSUME_SORT_ORDER=coordinate",
                "VALIDATION_STRINGENCY=STRICT",
                "OPTICAL_DUPLICATE_PIXEL_DISTANCE=0",
                f"TMP_DIR={target}",
            ]
            self.task(target / "mark", [bam], [marked, target / "duplicates.txt"], [command])
        else:
            marked = bam
        out = target / "fragments"
        result = out / "usable.bam"
        policy = {
            "mapq": self.spec["parameters"]["mapq"][0],
            "duplicates": "exclude"
            if self.spec["parameters"]["duplicates"] == ["excluded"]
            else "retain",
            "reference": {
                "mitochondrial_contigs": self.ref["mitochondrial"],
                "plastid_contigs": self.ref["plastid"],
            },
        }
        recipe = {"policy": policy, "assay": self.spec["assay"], "layout": lib["layout"]}
        if not self.rt.cached(out, [marked], recipe):
            out.mkdir(parents=True, exist_ok=True)
            start = time.monotonic()
            if self.spec["assay"] == "bulk-ATAC":
                from genesis_curation.atac.fragments import select

                # An interrupted selection may leave a partial SQLite database.
                # Build in an owned temporary directory and publish only on success.
                with tempfile.TemporaryDirectory(dir=target, prefix="select-") as temporary:
                    staged = Path(temporary)
                    select(marked, policy, staged)
                    for path in staged.iterdir():
                        path.replace(out / path.name)
            else:
                from .metrics import filter_bam

                value = filter_bam(
                    marked,
                    result,
                    layout=lib["layout"],
                    mapq=policy["mapq"],
                    exclude_duplicates=policy["duplicates"] == "exclude",
                    organelles=set(),
                )
                write_json(out / "metrics.json", value)
            self.rt.seal(
                out,
                [marked],
                [result, result.with_suffix(".bam.bai"), out / "metrics.json"],
                recipe,
                elapsed=time.monotonic() - start,
            )
        return result

    def peaks(
        self, folder: Path, lib: dict[str, Any], method: dict[str, Any], bams: dict[str, Path]
    ) -> Path:
        target = folder / lib["id"] / "peaks"
        caller = method["caller"]
        inputs = [bams[lib["id"]]] + ([bams[lib["control"]]] if lib["control"] else [])
        params = self.spec["parameters"]
        if caller == "genrich":
            output = target / "peaks.narrowPeak"
            names = target / "names.bam"
            commands = [
                [
                    self.tool("samtools"),
                    "sort",
                    "-n",
                    "-m",
                    "128M",
                    "-o",
                    str(names),
                    str(inputs[0]),
                ],
                [
                    self.tool("Genrich"),
                    "-t",
                    str(names),
                    "-o",
                    str(output),
                    "-j",
                    "-q",
                    str(params["qvalue"]),
                    "-v",
                ],
            ]
        else:
            output = target / "result_peaks.narrowPeak"
            command = [
                self.tool(caller),
                "callpeak",
                "-t",
                str(inputs[0]),
                "-f",
                "BAMPE" if lib["layout"] == "PE" else "BAM",
                "-g",
                str(self.ref.get("effective_genome_size", self.ref["genome_size"])),
                "-q",
                str(params["qvalue"]),
                "--keep-dup",
                params["keep_dup"],
                "-n",
                "result",
            ]
            if lib["control"]:
                command += ["-c", str(inputs[1])]
            commands = [command]
        self.task(target, inputs, [output], commands)
        return output


def execute(runtime: Runtime) -> list[dict[str, Any]]:
    methods = Methods(runtime)
    if hasattr(os, "sched_getaffinity"):
        available = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, available[: runtime.limits["cpus"]])
    libraries = runtime.plan["dataset"]["libraries"]
    controls = {lib["control"] for lib in libraries if lib["control"]}
    indexes = {
        aligner: methods.reference(aligner)
        for aligner in sorted({m["aligner"] for m in runtime.plan["arms"]})
    }
    cases = [
        (m, n)
        for n in range(1, methods.spec["performance"]["repetitions"] + 1)
        for m in runtime.plan["arms"]
    ]
    if methods.spec["performance"]["random_order"]:
        random.Random(methods.spec["seed"]).shuffle(cases)
    artifacts = []
    for method, repetition in cases:
        case_start = time.monotonic()
        folder = runtime.out / f"{method['id']}-r{repetition}"
        record: dict[str, Any] = {
            "arm": method["id"],
            "repetition": repetition,
            "status": "FAILED",
            "libraries": {},
            "library_qc": {},
        }
        try:
            bams = {}
            peaks = {}
            for lib in libraries:
                bam = methods.align(folder, lib, method, indexes[method["aligner"]])
                bams[lib["id"]] = methods.selected(folder, lib, method, bam)
                record["library_qc"][lib["id"]] = json.loads(
                    (bams[lib["id"]].parent / "metrics.json").read_text()
                )
            for lib in libraries:
                if lib["id"] not in controls:
                    peaks[lib["id"]] = methods.peaks(folder, lib, method, bams)
            record.update(
                status="PROCESSED",
                bams={k: str(v) for k, v in bams.items()},
                peaks={k: str(v) for k, v in peaks.items()},
            )
        except (ValueError, OSError, pysam.SamtoolsError) as error:
            record["reason"] = str(error)
        record["processing_wall_seconds"] = time.monotonic() - case_start
        record["processing_scope"] = (
            "all libraries: trimming, alignment, marking, selection, calling and task hashing; "
            "excludes indexes and common evaluation"
        )
        artifacts.append(record)
        write_json(runtime.out / "progress.json", {"results": artifacts})
        runtime.check()
    baseline = next(
        r for r in artifacts if r["arm"] == methods.spec["baseline"] and r["repetition"] == 1
    )
    if baseline["status"] != "PROCESSED":
        raise ValueError("Baseline failed; inspect progress.json and command logs")
    sizes = dict(
        line.split("\t")
        for line in runtime.asset(methods.ref["chrom_sizes"]).read_text().splitlines()
    )
    for record in artifacts:
        if record["status"] != "PROCESSED":
            continue
        for lib in libraries:
            name = lib["id"]
            if name in controls:
                continue
            bam, peak, fixed = (
                Path(record["bams"][name]),
                Path(record["peaks"][name]),
                Path(baseline["peaks"][name]),
            )
            with tempfile.TemporaryDirectory(dir=runtime.out) as tmp:
                value = assess(
                    bam,
                    peak,
                    fixed,
                    layout=lib["layout"],
                    raw_templates=lib["raw_templates"],
                    organelles=set(methods.ref["mitochondrial"] + methods.ref["plastid"]),
                    temporary=Path(tmp),
                )
                if methods.spec["assay"] == "bulk-ATAC" and "tss" in methods.ref:
                    value["tss"] = tss_enrichment(
                        bam,
                        runtime.asset(methods.ref["tss"]),
                        {c: int(n) for c, n in sizes.items()},
                        Path(tmp),
                    )
                if runtime.plan["dataset"]["synthetic"] and "bam" in lib:
                    from .evaluation import alignment_truth, peak_truth

                    aligned = bam.parents[2] / "alignment" / "aligned.bam"
                    value["metrics"].update(
                        alignment_truth(aligned, runtime.asset(lib["bam"]), Path(tmp))
                    )
                    if "enrichment-truth" in runtime.plan["assets"]:
                        value["metrics"].update(peak_truth(peak, runtime.asset("enrichment-truth")))
            from .evaluation import read_end_counts

            check_folder = bam.parents[2] / "independent-counts"
            counted = check_folder / "bedtools.tsv"
            command = [
                "bash",
                "-c",
                "set -euo pipefail\n"
                + shlex.join(
                    [
                        methods.tool("bedtools"),
                        "coverage",
                        "-a",
                        str(fixed),
                        "-b",
                        str(bam),
                        "-counts",
                    ]
                )
                + " > "
                + shlex.quote(str(counted)),
            ]
            methods.task(check_folder, [bam, fixed], [counted], [command])
            observed = [int(line.split("\t")[-1]) for line in counted.read_text().splitlines()]
            expected = read_end_counts(bam, fixed)
            if observed != expected:
                raise ValueError("Independent bedtools read-end counts differ")
            value["metrics"]["independent_count_mismatches"] = metric(
                0,
                "regions with different read-end overlap counts: bedtools coverage vs Python",
                "regions",
                len(expected),
            )
            value["artifact_roles"] = {
                "bam": str(bam),
                "peaks": str(peak),
                "fixed_peaks": str(fixed),
            }
            value["artifacts"] = {str(p): sha256(p) for p in (bam, peak, fixed)}
            record["libraries"][name] = value
        record["status"] = "COMPLETE"
        runtime.check()
    for record in artifacts:
        if record["status"] == "COMPLETE":
            for name, value in record["libraries"].items():
                value["comparison"] = compare(value, baseline["libraries"][name])
    from .evaluation import replicate_evaluation

    replicate_evaluation(runtime.plan["dataset"], artifacts, runtime.out)
    return artifacts


def fixture(out: Path, *, assay: str, small: bool, checkout: Path | None) -> Path:
    """Prepare an executable method matrix using the existing raw-read fixture."""
    import tomllib

    from .fixture import create

    original = create(out, assay=assay, small=small)
    if not small:
        truth = out / "enriched-regions.bed"
        truth.write_text(
            "".join(f"chr1\t{20000 + i * 8000}\t{20350 + i * 8000}\n" for i in range(200))
        )
        dataset_path = out / "dataset.json"
        dataset = json.loads(dataset_path.read_text())
        dataset["assets"]["enrichment-truth"] = {
            "path": truth.name,
            "sha256": sha256(truth),
            "bytes": truth.stat().st_size,
        }
        write_json(dataset_path, dataset)
    spec = tomllib.loads(original.read_text())
    environment = (checkout or Path(__file__).resolve().parents[3]) / "benchmarks/environment"
    lock = environment / "pixi.lock"
    if not lock.is_file():
        raise ValueError("Install the separate benchmarks/environment Pixi workspace first")
    baseline = {
        "id": "baseline",
        "aligner": "bwa-mem2",
        "trimmer": "none",
        "duplicate_tool": "samtools" if assay == "bulk-ATAC" else "none",
        "caller": "macs3",
        "adapter_r1": "-",
        "adapter_r2": "-",
    }
    methods = [baseline]
    for key, alternatives in (
        ("aligner", ["bwa", "bowtie2"]),
        ("caller", ["macs2"] + (["genrich"] if assay == "bulk-ATAC" else [])),
        ("duplicate_tool", ["picard"] if assay == "bulk-ATAC" else []),
    ):
        methods += [{**baseline, "id": value, key: value} for value in alternatives]
    if assay == "bulk-ATAC":
        for trimmer in ("cutadapt", "fastp"):
            methods.append(
                {
                    **baseline,
                    "id": trimmer,
                    "trimmer": trimmer,
                    "adapter_r1": "CTGTCTCTTATACACATCT",
                    "adapter_r2": "CTGTCTCTTATACACATCT",
                }
            )
    spec.update(
        schema_version=2,
        adapter="methods",
        baseline="baseline",
        methods=methods,
        environment={
            "prefix": str(environment / ".pixi/envs/default"),
            "legacy_prefix": str(environment / ".pixi/envs/macs2"),
            "lock": str(lock),
            "lock_sha256": sha256(lock),
        },
    )
    spec["parameters"] = {
        "mapq": [30 if assay == "bulk-ATAC" else 0],
        "duplicates": ["excluded" if assay == "bulk-ATAC" else "retained"],
        "keep_dup": "all" if assay == "bulk-ATAC" else "1",
        "qvalue": 0.01 if assay == "bulk-ATAC" else 0.05,
        "spp": False,
        "quantify": False,
    }
    spec["performance"] = {"repetitions": 1, "random_order": True}
    spec["limits"].update(
        cpus=2, memory_gb=8, seconds=3600, disk_bytes=10_000_000_000, max_arms=len(methods)
    )
    destination = original.with_suffix(".json")
    write_json(destination, spec)
    return destination
