"""Bounded artifact inspection, independent of execution and scientific acceptance."""

from __future__ import annotations

import copy
import gzip
import hashlib
import math
import subprocess
import time
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

import pysam

from .records import (
    Finding,
    Result,
    SourceEvidence,
    Subject,
    canonical,
    fingerprint,
    identity,
    load,
    loads,
    now,
    record,
    validate,
)


class Exhausted(Exception):
    """A declared scan budget was exhausted; this is not evidence of corruption."""


@dataclass
class Budget:
    max_bytes: int = 1024 * 1024 * 1024
    max_records: int = 1_000_000
    max_seconds: float = 300
    bytes_scanned: int = 0
    records_scanned: int = 0
    started: float = 0

    def __post_init__(self) -> None:
        if (
            self.max_bytes < 0
            or self.max_records < 0
            or self.max_seconds <= 0
            or not math.isfinite(self.max_seconds)
        ):
            raise ValueError("Byte/record budgets must be nonnegative and time must be positive")
        self.started = time.monotonic()

    def consume(self, size: int = 0, records: int = 0) -> None:
        if (
            (self.max_bytes and self.bytes_scanned + size > self.max_bytes)
            or (self.max_records and self.records_scanned + records > self.max_records)
            or time.monotonic() - self.started > self.max_seconds
        ):
            raise Exhausted("Validation budget exhausted")
        self.bytes_scanned += size
        self.records_scanned += records

    def digest(self, path: Path) -> str:
        result = hashlib.sha256()
        with path.open("rb") as stream:
            while data := stream.read(1024 * 1024):
                self.consume(len(data))
                result.update(data)
        return result.hexdigest()


def lines(path: Path, budget: Budget) -> Iterator[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as stream:
        while line := stream.readline(1024 * 1024 + 1):
            if len(line) > 1024 * 1024:
                raise Exhausted("Interval/text record exceeds the 1 MiB line budget")
            budget.consume(len(line.encode()), 1)
            yield line


def fasta(path: Path, budget: Budget) -> dict[str, int]:
    sizes: dict[str, int] = {}
    current = None
    opener = gzip.open if path.suffix == ".gz" else open
    at_start = True
    with opener(path, "rt") as stream:
        while line := stream.readline(65536):
            budget.consume(len(line.encode()), int(at_start))
            if at_start and line.startswith(">"):
                if len(line) == 65536 and not line.endswith("\n"):
                    raise Exhausted("FASTA header exceeds 64 KiB")
                parts = line[1:].split()
                if not parts or parts[0] in sizes:
                    raise ValueError("Missing or duplicate FASTA contig")
                current = parts[0]
                sizes[current] = 0
            elif line.strip():
                if current is None or any(c.isspace() for c in line.strip()):
                    raise ValueError("Malformed FASTA sequence")
                sizes[current] += len(line.strip())
            at_start = line.endswith("\n")
    if not sizes or any(n <= 0 for n in sizes.values()):
        raise ValueError("Empty FASTA contig")
    return sizes


def intervals(path: Path, fmt: str, sizes: dict[str, int], ordered: bool, budget: Budget) -> None:
    previous: tuple[str, int, int] | None = None
    for number, line in enumerate(lines(path, budget), 1):
        fields = line.rstrip("\r\n").split("\t")
        count = 10 if fmt == "narrowPeak" else 3
        if len(fields) < count or fields[0] not in sizes:
            raise ValueError(f"Line {number}: invalid fields or unknown contig")
        chrom, start = fields[0], int(fields[1])
        end = start + 1 if fmt == "tss" else int(fields[2])
        if not 0 <= start < end <= sizes[chrom]:
            raise ValueError(f"Line {number}: interval outside reference bounds")
        if fmt == "tss" and (len(fields) != 3 or fields[2] not in {"+", "-"}):
            raise ValueError("TSS requires contig, BED0 position and strand")
        if fmt == "narrowPeak":
            if len(fields) != 10 or fields[5] not in {"+", "-", "."}:
                raise ValueError("narrowPeak requires ten fields and a valid strand")
            if any(not math.isfinite(float(x)) for x in fields[4:5] + fields[6:9]):
                raise ValueError("Nonfinite narrowPeak value")
            offset = int(fields[9])
            if offset != -1 and not 0 <= offset < end - start:
                raise ValueError("Summit offset outside peak")
        key = (chrom, start, end)
        if ordered and previous is not None and key < previous:
            raise ValueError("Declared coordinate sorting is violated")
        previous = key


def bam(path: Path, sizes: dict[str, int], ordered: bool, budget: Budget) -> None:
    with pysam.AlignmentFile(str(path), "rb") as reader:
        if dict(zip(reader.references, reader.lengths, strict=True)) != sizes:
            raise ValueError("BAM header/reference identity mismatch")
        if ordered and (
            reader.header.to_dict().get("HD", {}).get("SO") != "coordinate"
            or not reader.has_index()
        ):
            raise ValueError("Declared coordinate BAM requires sorting and a readable index")
        previous = (-1, -1)
        for alignment in reader.fetch(until_eof=True):
            budget.consume(records=1)
            if alignment.reference_id < 0:
                key = (len(sizes), 0)
            else:
                key = (alignment.reference_id, alignment.reference_start)
                if alignment.reference_end and alignment.reference_end > reader.lengths[key[0]]:
                    raise ValueError("BAM alignment exceeds reference")
            if ordered and key < previous:
                raise ValueError("BAM records are not coordinate sorted")
            previous = key


def bigwig(
    path: Path, item: dict[str, Any], sizes: dict[str, int], budget: Budget, python: Path | None
) -> None:
    if python is None:
        raise Exhausted("Configure --bigwig-python from the pinned track runtime")
    budget.consume()
    seconds = max(0.1, budget.max_seconds - (time.monotonic() - budget.started))
    remaining = max(0, budget.max_records - budget.records_scanned) if budget.max_records else 0
    if budget.max_records and not remaining:
        raise Exhausted("Record budget exhausted")
    with as_file(files(__package__).joinpath("bigwig_reader.py")) as script:
        response = subprocess.run(
            [
                str(python.absolute()),
                "-I",
                str(script),
            ],
            input=canonical(
                {
                    "path": str(path),
                    "contigs": sizes,
                    "seconds": seconds,
                    "records": remaining,
                    "raw": item["signal"]["kind"] == "raw_counts",
                }
            ),
            text=True,
            capture_output=True,
            timeout=seconds + 1,
            check=False,
        )
    result = loads(response.stdout)
    if result.get("unavailable"):
        raise Exhausted(result["error"])
    if response.returncode or result.get("error"):
        raise ValueError(result.get("error", "bigWig reader failed"))
    budget.consume(records=result["records"])
    if not result["complete"]:
        raise Exhausted(result["reason"])


def inspect_manifest(
    original: dict[str, Any],
    *,
    level: str = "metadata",
    budget: Budget | None = None,
    bigwig_python: Path | None = None,
    worker_identity: str = "local",
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate(original, "manifest")
    if level not in {"metadata", "full"}:
        raise ValueError("Unsupported validation level")
    budget = budget or Budget()
    data = copy.deepcopy(original["data"])
    findings: list[dict[str, Any]] = []
    incomplete = level != "full"

    def finding(
        rule: str,
        result: Result,
        expected: str,
        observed: object,
        item: dict[str, Any] | None = None,
        *,
        required: bool = True,
    ) -> None:
        nonlocal incomplete
        if result == "NOT_EVALUATED" and required:
            incomplete = True
        target = item["id"] if item else data["dataset_id"]
        checksum = item.get("sha256") if item else None
        findings.append(
            Finding(
                rule,
                Subject(target, checksum or original["version"], "artifact" if item else "library"),
                result,
                expected,
                observed,
                SourceEvidence(
                    item["path"] if item else data["run"]["root"],
                    checksum,
                    "$",
                    item["worker"] if item else data["worker"],
                ),
            ).export()
        )

    finding(
        "provenance.complete",
        "PASS" if data["run"]["provenance_complete"] else "NOT_EVALUATED",
        "Recorded code, input and scientific-parameter provenance",
        data["run"],
    )
    if data["metadata"].get("output_contract") == "genesis-published-v1":
        roles = {a["role"] for a in data["artifacts"] if a["required"]}
        required_roles = {"metadata", "track_cpm", "counts_5p", "report", "fastqc", "alignment_qc"}
        if data["assay"] == "bulk-ATAC":
            required_roles = {
                "fragments",
                "fragment_index",
                "metrics",
                "chrom_sizes",
                "cuts_counts",
                "peaks",
                "enrichment",
                "adapters",
                "report",
                "multiqc",
                "acquisition",
                "fastqc",
            }
        elif data["metadata"].get("role") != "control":
            required_roles |= {"peaks", "quantification"}
        finding(
            "outputs.assay_role",
            "ERROR" if required_roles - roles else "PASS",
            "Required published roles for this assay/library role",
            sorted(required_roles - roles),
        )
    ref = data["reference"]
    sizes = ref["contigs"]
    if level == "full" and data["worker"] == worker_identity:
        try:
            if not ref["fasta"]:
                raise Exhausted("Exact FASTA location is absent")
            path = Path(ref["fasta"])
            before_reference = path.stat()
            checksum = budget.digest(path)
            if ref["fasta_sha256"] and checksum != ref["fasta_sha256"]:
                raise ValueError("Reference FASTA checksum mismatch")
            actual = fasta(path, budget)
            after_reference = path.stat()
            if (
                before_reference.st_size,
                before_reference.st_mtime_ns,
                before_reference.st_ino,
            ) != (after_reference.st_size, after_reference.st_mtime_ns, after_reference.st_ino):
                raise ValueError("Reference FASTA changed during validation")
            if sizes and actual != sizes:
                raise ValueError("Declared FASTA contigs/lengths mismatch")
            if set(ref["mitochondrial_contigs"]) & set(ref["plastid_contigs"]):
                raise ValueError("Overlapping organellar declarations")
            if (set(ref["mitochondrial_contigs"]) | set(ref["plastid_contigs"])) - actual.keys():
                raise ValueError("Organellar contig absent from FASTA")
            sizes = ref["contigs"] = actual
            ref["fasta_sha256"] = ref["version"] = checksum
            if ref["tss"]:
                tss_path = Path(ref["tss"])
                before_tss = tss_path.stat()
                checksum = budget.digest(tss_path)
                if ref["tss_sha256"] and checksum != ref["tss_sha256"]:
                    raise ValueError("TSS checksum mismatch")
                intervals(tss_path, "tss", sizes, False, budget)
                after_tss = tss_path.stat()
                if (before_tss.st_size, before_tss.st_mtime_ns, before_tss.st_ino) != (
                    after_tss.st_size,
                    after_tss.st_mtime_ns,
                    after_tss.st_ino,
                ):
                    raise ValueError("Reference TSS changed during validation")
                ref["tss_sha256"] = checksum
            finding("reference.identity", "PASS", "Exact FASTA/contigs and declared TSS", ref)
        except Exhausted as error:
            finding("reference.identity", "NOT_EVALUATED", "Exact reference validation", str(error))
        except (OSError, ValueError, EOFError) as error:
            finding("reference.identity", "ERROR", "Exact reference validation", str(error))
    else:
        finding(
            "reference.identity",
            "NOT_EVALUATED",
            "Full reference scan on the selected worker",
            "metadata level or a different worker",
        )

    seen: set[str] = set()
    for item in data["artifacts"]:
        required = item["required"]
        if item["id"] in seen:
            finding("artifact.unique", "ERROR", "Unique artifact IDs", item["id"], item)
        seen.add(item["id"])
        if item["worker"] != worker_identity:
            finding(
                "artifact.worker",
                "NOT_EVALUATED",
                "Run validation on artifact worker",
                item["worker"],
                item,
                required=required,
            )
            continue
        if item["availability"] in {"intentionally_pruned", "not_required"}:
            result = "ERROR" if required else "NOT_APPLICABLE"
            finding(
                "artifact.retention",
                result,
                "Required outputs retained",
                item["availability"],
                item,
            )
            continue
        path = Path(item["path"])
        if not path.is_absolute() or not path.is_file():
            finding(
                "artifact.available",
                "ERROR" if required else "NOT_APPLICABLE",
                "Declared artifact available",
                "unavailable",
                item,
            )
            item["availability"] = "unavailable"
            continue
        item["availability"] = "available"
        if item["reference_id"] != ref["id"]:
            finding(
                "artifact.reference",
                "ERROR",
                "Declared reference identity",
                item["reference_id"],
                item,
            )
        if item["format"] == "bigwig":
            sem = item["signal"]
            expected = {
                "cuts_counts": ("raw_counts", "cut", "none"),
                "counts_5p": ("raw_counts", "read_end", "none"),
                "track_cpm": ("normalized_coverage", "base_coverage", "CPM"),
            }
            if not sem or (
                item["role"] in expected
                and tuple(sem[k] for k in ("kind", "unit", "normalization"))
                != expected[item["role"]]
            ):
                finding("signal.semantics", "ERROR", "Signal role and provenance agree", sem, item)
                continue
        if level != "full":
            finding(
                "artifact.scan", "NOT_EVALUATED", "Digest and format scan", "metadata level", item
            )
            continue
        try:
            before = path.stat()
            checksum = budget.digest(path)
            if item["sha256"] and item["sha256"] != checksum:
                raise ValueError("Artifact checksum changed")
            item["sha256"] = checksum
            item["size"] = before.st_size
            fmt = item["format"]
            if fmt in {"bed", "narrowPeak", "tss", "bam", "bigwig"} and not sizes:
                raise Exhausted("Reference contigs are unavailable")
            if fmt in {"bed", "narrowPeak", "tss"}:
                intervals(path, fmt, sizes, item.get("sorted", False), budget)
            elif fmt == "bam":
                bam(path, sizes, item.get("sorted", False), budget)
            elif fmt == "bigwig":
                bigwig(path, item, sizes, budget, bigwig_python)
            elif fmt == "json":
                load(path)
            elif fmt == "zip":
                with zipfile.ZipFile(path) as archive:
                    for member in archive.infolist():
                        with archive.open(member) as stream:
                            while block := stream.read(65536):
                                budget.consume(len(block))
            elif fmt == "fasta":
                if fasta(path, budget) != sizes:
                    raise ValueError("FASTA/reference mismatch")
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ino,
            ):
                raise ValueError("Artifact changed during validation")
            finding(
                "artifact.scan", "PASS", "Digest, format and declared semantics", checksum, item
            )
        except (Exhausted, subprocess.TimeoutExpired) as error:
            finding(
                "artifact.scan",
                "NOT_EVALUATED",
                "Complete scan",
                str(error),
                item,
                required=required,
            )
        except (ValueError, OSError, EOFError, RuntimeError, zipfile.BadZipFile) as error:
            finding("artifact.scan", "ERROR", "Readable and compatible artifact", str(error), item)

    for measurement in data["measurements"]:
        validate(measurement, "measurement")
        metric = measurement["data"]
        sources = [
            s["data"]
            for s in data["sources"]
            if (
                s["data"]["sha256"] == metric["evidence"]["sha256"]
                and s["data"]["location"] == metric["evidence"]["location"]
                and s["data"]["worker"] == metric["evidence"]["worker"]
            )
        ]
        metric_result: Result = "NOT_EVALUATED"
        observed: object = "Metric source snapshot or locator is unavailable"
        if sources and metric["evidence"]["locator"].startswith("/"):
            try:
                value: Any = loads(sources[0]["snapshot"])
                for key in metric["evidence"]["locator"][1:].split("/"):
                    key = key.replace("~1", "/").replace("~0", "~")
                    value = value[int(key)] if isinstance(value, list) else value[key]
                metric_result = (
                    "PASS"
                    if value == metric["value"] and type(value) is type(metric["value"])
                    else "ERROR"
                )
                observed = {"stored": metric["value"], "source": value}
            except ValueError, KeyError, IndexError, TypeError:
                metric_result, observed = (
                    "ERROR",
                    "Metric locator does not resolve in the stored source",
                )
        if metric["units"] == "fraction" and metric["value"] is not None:
            if not 0 <= metric["value"] <= 1 or metric["denominator_value"] == 0:
                metric_result, observed = (
                    "ERROR",
                    "Invalid fraction or zero denominator with a defined value",
                )
        if metric["subject_id"] != data["dataset_id"]:
            metric_result, observed = "ERROR", "Metric targets a different dataset"
        finding(
            "metric.evidence",
            metric_result,
            "Versioned metric agrees with its source and denominator",
            observed,
        )
    manifest = record("manifest", data, original["id"])
    for value in findings:
        if value["data"]["subject"]["scope"] == "library":
            value["data"]["subject"]["version"] = manifest["version"]
    findings = [
        Finding(
            f["data"]["rule_id"],
            Subject(**f["data"]["subject"]),
            f["data"]["result"],
            f["data"]["expected"],
            f["data"]["observed"],
            SourceEvidence(**f["data"]["evidence"]),
            f["data"]["rule_version"],
            f["data"]["validator_version"],
        ).export()
        for f in findings
    ]
    assessment = record(
        "validation",
        {
            "dataset_id": manifest["id"],
            "manifest_version": manifest["version"],
            "level": level,
            "findings": findings,
            "complete": not incomplete,
            "bytes_scanned": budget.bytes_scanned,
            "records_scanned": budget.records_scanned,
            "seconds": time.monotonic() - budget.started,
            "budgets": {k: v for k, v in asdict(budget).items() if k.startswith("max_")},
            "observed_at": now(),
        },
        identity("validation", manifest["id"], now()),
    )
    return manifest, assessment


def bundle(
    manifests: list[dict[str, Any]],
    *,
    level: str = "metadata",
    budget: Budget | None = None,
    bigwig_python: Path | None = None,
    worker_identity: str = "local",
) -> dict[str, Any]:
    checked, validations = [], []
    for manifest in manifests:
        value, result = inspect_manifest(
            manifest,
            level=level,
            budget=budget,
            bigwig_python=bigwig_python,
            worker_identity=worker_identity,
        )
        checked.append(value)
        validations.append(result)
    data = {"manifests": checked, "validations": validations}
    return record("bundle", data, identity("bundle", fingerprint(data)))
