"""Bounded local model-bundle inspection; never a training approval or registry write."""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import stat
import subprocess
import time
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from ..contracts.records import MAX_DOCUMENT, canonical, fingerprint, loads
from ..contracts.validation import Budget, Exhausted, fasta, lines
from ..publication import sanitize
from .model import MODELS, folds

Status = Literal["PASS", "FAIL", "NOT_ASSESSED", "NOT_APPLICABLE", "INCOMPLETE"]
SPLITS = ("train", "validation", "test")
REQUIRED = (
    "config.json",
    "loader.json",
    "folds.json",
    "parent.json",
    "metadata.json",
    "qc.json",
    "chrom.sizes",
    "cells.jsonl",
    "signal.bw",
    "genome.fa",
    *(f"{split}.{kind}.bed" for split in SPLITS for kind in ("peaks", "background")),
)


class Problem(ValueError):
    """A controlled diagnostic containing no supplied path or metadata values."""


class ReaderUnavailable(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Problem(message)


def manifest(value: dict[str, Any], kind: str) -> None:
    require(
        type(value.get("schema_version")) is int
        and value.get("schema_version") == 1
        and value.get("kind") == kind,
        "Unsupported manifest kind/version",
    )
    require(
        value.get("version") == fingerprint({k: v for k, v in value.items() if k != "version"}),
        "Manifest fingerprint mismatch",
    )
    require(
        isinstance(value.get("data"), dict) and isinstance(value.get("outputs"), dict),
        "Malformed manifest fields",
    )


def shareable(value: object) -> object:
    """Only declared report fields reach here; omit paths rather than expose their prefixes."""
    if isinstance(value, dict):
        return {
            shareable(str(k)): (
                "[redacted]"
                if re.search(r"(?i)token|secret|password|credential|authorization|api.key", str(k))
                else shareable(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [shareable(v) for v in value]
    if isinstance(value, str):
        if re.search(r"(?:/|\\|https?:|file:)", value):
            return "[path or URL redacted]"
        try:
            return sanitize(value.encode()).decode()
        except ValueError:
            return "[sensitive value redacted]"
    return value


class Inspection:
    def __init__(self, root: Path, budget: Budget) -> None:
        self.root = root.resolve()
        self.budget = budget
        self.documents: dict[str, dict[str, Any]] = {}
        self.report: dict[str, Any] = {
            "schema_version": 1,
            "inspection_version": "scatac-model-inspection/1",
            "inspector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "bundle_version": None,
            "model_ready": False,
            "notice": (
                "Structural checks are not training approval. "
                "Recorded evidence is not rerun evidence."
            ),
            "declared": {},
            "checks": [],
        }

    def path(self, name: str) -> Path:
        path = Path(name)
        require(
            not path.is_absolute() and ".." not in path.parts and path.parts != (),
            "Unsafe bundle member path",
        )
        target = self.root / path
        require(target.resolve().is_relative_to(self.root), "Bundle member escapes input")
        require(
            all(
                not p.is_symlink()
                for p in [target, *target.parents]
                if p != self.root and p.is_relative_to(self.root)
            ),
            "Symlink bundle members are not inspected",
        )
        require(stat.S_ISREG(target.stat().st_mode), "Bundle member is not a regular file")
        return target

    def reserve(self, path: Path) -> None:
        self.budget.consume()
        if path.stat().st_size > self.budget.max_bytes - self.budget.bytes_scanned:
            raise Exhausted("File exceeds remaining byte budget")

    def document(self, name: str) -> dict[str, Any]:
        if name not in self.documents:
            path = self.path(name)
            self.reserve(path)
            require(path.stat().st_size <= MAX_DOCUMENT, "JSON document exceeds 32 MiB")
            with path.open("rb") as stream:
                data = stream.read(
                    min(MAX_DOCUMENT, self.budget.max_bytes - self.budget.bytes_scanned) + 1
                )
            self.budget.consume(len(data))
            self.documents[name] = loads(data.decode("utf-8"))
        return self.documents[name]

    def add(
        self,
        rule: str,
        status: Status,
        source: str,
        reason: str,
        action: str = "",
        observed: object = None,
    ) -> None:
        self.report["checks"].append(
            {
                "rule": rule,
                "status": status,
                "source": source,
                "reason": reason,
                "action": action,
                "observed": shareable(observed),
            }
        )

    def check[T](self, rule: str, source: str, operation: Callable[[], T]) -> T | None:
        try:
            result = operation()
            self.add(rule, "PASS", source, "Checked within the stated scan limits", observed=result)
            return result
        except Exhausted:
            self.add(
                rule,
                "INCOMPLETE",
                source,
                "Scan budget exhausted; no complete claim",
                "Rerun with explicit larger limits if appropriate",
            )
        except ReaderUnavailable:
            self.add(
                rule,
                "NOT_ASSESSED",
                source,
                "Configured local reader is unavailable",
                "Select an existing environment containing pyBigWig",
            )
        except (
            ValueError,
            KeyError,
            TypeError,
            OSError,
            UnicodeError,
            IndexError,
            AttributeError,
        ) as error:
            # Never echo exception messages: they may contain input paths or private metadata.
            self.add(
                rule,
                "FAIL",
                source,
                str(error)
                if isinstance(error, Problem)
                else "Invalid, missing or inconsistent input (" + type(error).__name__ + ")",
                "Inspect the cited artifact and produce a corrected new bundle",
            )
        return None

    def region_checks(
        self, config: dict[str, Any], sizes: dict[str, int], data: dict[str, Any]
    ) -> None:
        mapping = folds(config["folds"], sizes)
        radius = config["input_window"] // 2 + config["max_jitter"]
        contexts: dict[str, list[tuple[int, int, str]]] = {}
        all_complete = True
        for split in SPLITS:
            for kind in ("peaks", "background"):
                name = f"{split}.{kind}.bed"

                def scan(name: str = name, split: str = split, kind: str = kind) -> int:
                    count = 0
                    path = self.path(name)
                    self.reserve(path)
                    for line in lines(path, self.budget):
                        f = line.rstrip("\n").split("\t")
                        require(len(f) == 10, "Expected ten-column summit regions")
                        chrom, start, end, offset = f[0], int(f[1]), int(f[2]), int(f[9])
                        require(chrom in sizes and mapping.get(chrom) == split, "Wrong region fold")
                        require(
                            0 <= start < end <= sizes[chrom] and 0 <= offset < end - start,
                            "Invalid interval or summit",
                        )
                        center = start + offset
                        require(
                            0 <= center - radius and center + radius <= sizes[chrom],
                            "Context/jitter window exceeds reference",
                        )
                        contexts.setdefault(chrom, []).append(
                            (center - radius, center + radius, kind)
                        )
                        count += 1
                    expected = data[
                        "peaks_per_fold" if kind == "peaks" else "backgrounds_per_fold"
                    ][split]
                    require(
                        type(expected) is int and count == expected,
                        "Region count disagrees with manifest",
                    )
                    return count

                count = self.check("regions." + name, name, scan)
                all_complete &= count is not None
                if count == 0:
                    self.add(
                        "target.nonempty." + name,
                        "FAIL",
                        name,
                        "Empty regions are structurally valid but this adapter requires every fold",
                        "Review peak availability; do not lower scientific thresholds to pass",
                    )
        if not all_complete:
            self.add(
                "contexts.disjoint",
                "INCOMPLETE",
                "fold region BEDs",
                "Region scans did not all complete",
                "Resolve region findings first",
            )
            return

        def overlap() -> int:
            count = 0
            for regions in contexts.values():
                ends = {"peaks": -1, "background": -1}
                for left, right, kind in sorted(regions):
                    self.budget.consume()
                    other = "background" if kind == "peaks" else "peaks"
                    require(left >= ends[other], "Positive/background context windows overlap")
                    ends[kind] = max(ends[kind], right)
                    count += 1
            return count

        self.check("contexts.disjoint", "fold region BEDs", overlap)

    def inspect(self, bigwig_python: Path | None) -> None:
        value = self.document("complete.json")
        manifest(value, "scatac-model")
        data, outputs = value["data"], value["outputs"]
        self.report["bundle_version"] = value["version"]
        self.add("manifest.identity", "PASS", "complete.json", "Manifest fingerprint verified")
        self.report["declared"]["model_ready"] = data.get("model_ready", "UNKNOWN")
        require(len(outputs) <= 10_000, "Too many bundle artifacts")
        for name in REQUIRED:
            self.check(
                "artifact." + name,
                name,
                lambda name=name: (
                    require(name in outputs, "Required artifact is not checksummed"),
                    {"bytes": self.path(name).stat().st_size},
                )[1],
            )
        docs = {
            name: self.check("document." + name, name, lambda name=name: self.document(name))
            for name in (
                "config.json",
                "loader.json",
                "folds.json",
                "metadata.json",
                "parent.json",
                "qc.json",
            )
        }
        # Document contents are represented by selected fields below, not echoed wholesale.
        for finding in self.report["checks"]:
            if finding["rule"].startswith("document."):
                finding["observed"] = None
        config, loader, metadata, parent = (
            docs[n] for n in ("config.json", "loader.json", "metadata.json", "parent.json")
        )
        declared = self.report["declared"]
        provenance = data.get("provenance", {})
        scope = (
            provenance.get("input_scope", "UNKNOWN") if isinstance(provenance, dict) else "UNKNOWN"
        )
        declared["input_scope"] = (
            scope if scope in ("synthetic", "public_subset", "full_depth") else "UNKNOWN"
        )
        self.add(
            "provenance.scope",
            "NOT_ASSESSED",
            "complete.json:data.provenance",
            "Scope is a declaration, not inferred from names or independently verified",
            "Supply versioned source/depth/sampling evidence",
            observed=provenance,
        )
        declared["reference"] = data.get("reference", {})
        declared["exported_fasta_declared_sha256"] = outputs.get("genome.fa", "UNKNOWN")
        declared["task_id"] = data.get("task_id", "UNKNOWN")
        if docs["qc.json"] is not None:
            declared["recorded_qc"] = {
                k: docs["qc.json"].get(k, "UNKNOWN")
                for k in (
                    "peak_count",
                    "frip",
                    "overlapping_fragments",
                    "denominator_fragments",
                    "definition",
                    "tss_enrichment",
                    "tss_status",
                )
            }
        declared["source_locations"] = {
            "identity": "metadata.json:identity",
            "signal": "metadata.json:signal",
            "reference": "complete.json:data.reference",
            "target": "config.json",
            "annotation": "metadata.json:identity.annotation_sha256",
            "cell_annotations": "parent.json:data.annotations_sha256",
        }
        if metadata is not None:
            declared["identity"] = metadata.get("identity", {})
            declared["libraries"] = metadata.get("library_composition", {})
            declared["signal"] = metadata.get("signal", {})
        if parent is not None:
            declared["cell_annotations_sha256"] = parent.get("data", {}).get(
                "annotations_sha256", "UNKNOWN"
            )
            declared["recorded_review_status"] = parent.get("data", {}).get(
                "review_status", "UNKNOWN"
            )
        if config is not None:
            declared["target_contract"] = config
        if loader is not None:
            declared["loader"] = loader
        if (
            metadata is not None
            and parent is not None
            and config is not None
            and loader is not None
        ):

            def bindings() -> None:
                manifest(parent, "scatac-group")
                require(parent["version"] == data["parent"], "Stale parent binding")
                require(parent["data"]["group"] == metadata, "Metadata differs from parent")
                require(metadata["reference"] == data["reference"], "Reference differs from parent")
                require(
                    config == data["config"] and docs["folds.json"] == config["folds"],
                    "Configuration differs",
                )
                for name in ("metadata.json", "qc.json", "cells.jsonl", "signal.bw", "chrom.sizes"):
                    require(
                        parent["outputs"][name] == outputs[name],
                        "Copied artifact differs from parent",
                    )

            self.check("parent.bindings", "parent.json; metadata.json; config.json", bindings)

            def contract() -> None:
                require(
                    type(config["schema_version"]) is int
                    and config["schema_version"] == 1
                    and config["target"] in MODELS,
                    "Unknown target contract",
                )
                require(config["model_sha"] == MODELS[config["target"]], "Unaudited model revision")
                for key in (
                    "target",
                    "model_sha",
                    "input_window",
                    "output_window",
                    "max_jitter",
                ):
                    require(loader[key] == config[key], "Loader/config disagreement")
                w, o, j = config["input_window"], config["output_window"], config["max_jitter"]
                require(all(type(n) is int for n in (w, o, j)), "Windows must be integers")
                require(w >= o >= 2 and w % 2 == o % 2 == 0 and j >= 0, "Invalid windows")
                require(
                    loader["sequences"] == "genome.fa"
                    and loader["signals"] == ["signal.bw"]
                    and loader["signal_groups"] == [1]
                    and loader["controls"] is None
                    and config.get("controls") is None
                    and loader["summits"] is True
                    and loader["further_tn5_shift"] is False,
                    "Incompatible channel or shift semantics",
                )
                signal = metadata["signal"]
                require(
                    all(
                        signal[k] == v
                        for k, v in {
                            "kind": "raw_counts",
                            "unit": "cut",
                            "normalization": "none",
                            "strand": "both",
                        }.items()
                    ),
                    "Incompatible signal",
                )
                require(
                    parent["data"]["policy"]["signal_convention"] == "chrombpnet-atac-v1",
                    "Unsupported signal derivation",
                )
                require(
                    metadata["identity"]["coordinate_offsets"] == [4, -5],
                    "Unsupported source offsets",
                )
                require(
                    config["background_method"] == "nonoverlapping-genome-tiles-v1",
                    "Unknown background method",
                )
                require(
                    loader["bias_model"] == config.get("bias_model"), "Bias declaration differs"
                )
                if config["target"] == "chrombpnet":
                    require(isinstance(config.get("bias_model"), dict), "Missing bias-model plan")
                return None

            self.check("target.contract", "config.json; loader.json; metadata.json", contract)
        sizes: dict[str, int] = {}

        def chromosomes() -> dict[str, int]:
            for line in lines(self.path("chrom.sizes"), self.budget):
                fields = line.split()
                require(len(fields) == 2, "Invalid chromosome row")
                chrom, length = fields[0], int(fields[1])
                require(chrom not in sizes and length > 0, "Invalid chromosome sizes")
                sizes[chrom] = length
            require(bool(sizes) and sizes == data["reference"]["contigs"], "Reference sizes differ")
            return {"contigs": len(sizes)}

        sizes_ok = self.check(
            "reference.contigs", "chrom.sizes; complete.json:data.reference", chromosomes
        )
        if config is not None and sizes_ok is not None:

            def regions() -> None:
                w, j = config["input_window"], config["max_jitter"]
                require(
                    type(w) is int and type(j) is int and w > 0 and w % 2 == 0 and j >= 0,
                    "Invalid context windows",
                )
                self.region_checks(config, sizes, data)

            self.check("folds.declarations", "config.json:folds", regions)
        for name, expected in sorted(
            outputs.items(), key=lambda item: (item[0] == "genome.fa", item[0])
        ):

            def checksum(name: str = name, expected: str = expected) -> str:
                require(
                    isinstance(expected, str)
                    and re.fullmatch(r"[a-f0-9]{64}", expected) is not None,
                    "Invalid artifact digest",
                )
                path = self.path(name)
                self.reserve(path)
                observed = self.budget.digest(path)
                require(observed == expected, "Artifact checksum mismatch")
                return observed

            # Only canonical relative artifact names are used as public locations.
            location = name if name in REQUIRED else "additional manifest artifact"
            self.check("checksum." + location, location, checksum)
        if sizes_ok is not None:

            def sequence() -> dict[str, int]:
                path = self.path("genome.fa")
                self.reserve(path)
                observed = fasta(path, self.budget)
                require(observed == sizes, "Exported FASTA lengths differ")
                return {"contigs": len(observed)}

            self.check("reference.exported_sequence", "genome.fa; chrom.sizes", sequence)
        if bigwig_python is None or sizes_ok is None:
            self.add(
                "signal.binary",
                "NOT_ASSESSED",
                "signal.bw",
                "Binary track contents were not read",
                "Use --bigwig-python with an existing local pyBigWig environment",
            )
        else:

            def track() -> dict[str, Any]:
                path = self.path("signal.bw")
                self.reserve(path)
                self.budget.consume(path.stat().st_size)
                remaining = self.budget.max_seconds - (time.monotonic() - self.budget.started)
                records = self.budget.max_records - self.budget.records_scanned
                if remaining <= 0 or records <= 0:
                    raise Exhausted()
                reader = files("genesis_curation.contracts").joinpath("bigwig_reader.py")
                try:
                    result = subprocess.run(
                        [str(bigwig_python), str(reader)],
                        input=canonical(
                            {
                                "path": str(path),
                                "contigs": sizes,
                                "raw": True,
                                "records": records,
                                "seconds": remaining,
                            }
                        ),
                        capture_output=True,
                        text=True,
                        timeout=remaining,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    raise Exhausted() from None
                except OSError:
                    raise ReaderUnavailable() from None
                response = loads(result.stdout)
                if response.get("unavailable"):
                    raise ReaderUnavailable()
                require(
                    result.returncode == 0 and "error" not in response,
                    "Binary track validation failed",
                )
                self.budget.consume(records=response.get("records", 0))
                if not response.get("complete"):
                    raise Exhausted()
                return response

            self.check("signal.binary", "signal.bw", track)
        for rule, source, reason, action in (
            (
                "reference.author_identity",
                "complete.json:data.reference",
                (
                    "A declared source FASTA digest is not proof of the original "
                    "author's reference identity"
                ),
                (
                    "Review sequence identity separately from annotation "
                    "suitability; document any supported equivalence"
                ),
            ),
            (
                "annotation.suitability",
                "metadata.json:identity.annotation_sha256",
                "Annotation identifiers/hashes do not establish suitability",
                (
                    "Review the exact release and extraction rules; do not "
                    "substitute annotations silently"
                ),
            ),
            (
                "qc.tss",
                "qc.json",
                "This scATAC product stage does not calculate TSS enrichment",
                "Agree on required QC evidence",
            ),
            (
                "qc.original_organelles",
                "parent.json",
                "Filtered fragments cannot establish original organellar contamination",
                "Use original-library evidence if needed",
            ),
            (
                "background.matching",
                "config.json:background_method",
                "Genome-tile backgrounds are not GC matched",
                "Agree on target-specific background acceptance",
            ),
            (
                "folds.homology",
                "folds.json",
                "Chromosome separation does not test homology leakage",
                "Define a downstream homology-aware evaluation",
            ),
        ):
            self.add(rule, "NOT_ASSESSED", source, reason, action)
        target = config.get("target") if config else None
        self.add(
            "bias.fitted_model",
            "NOT_ASSESSED"
            if target == "chrombpnet"
            else "NOT_APPLICABLE"
            if target == "cherimoya"
            else "NOT_ASSESSED",
            "config.json:bias_model",
            "A bias plan is not a fitted, validated plant bias model"
            if target == "chrombpnet"
            else "This Cherimoya adapter has no control track or ChromBPNet bias contract"
            if target == "cherimoya"
            else "Target contract unavailable",
            "Agree on bias evidence with the modeling team",
        )
        for name in ("loader", "scientific_review", "training"):
            self.add(
                "evidence." + name,
                "NOT_ASSESSED",
                "complete.json; parent.json",
                (
                    "No supported receipt binds this evidence to this exact "
                    "bundle and audited target revision"
                ),
                (
                    "Record bundle version, target SHA, scope, artifacts, "
                    "command/environment and outcome; do not reuse fixture PASS"
                ),
            )


def inspect_bundle(
    root: Path, *, budget: Budget | None = None, bigwig_python: Path | None = None
) -> tuple[dict[str, Any], int]:
    budget = budget or Budget(64 * 1024 * 1024, 100_000, 30)
    require(budget.max_bytes > 0 and budget.max_records > 0, "Inspection limits must be positive")
    scan = Inspection(root, budget)
    malformed = False
    try:
        scan.inspect(bigwig_python)
    except Exhausted:
        scan.add(
            "inspection",
            "INCOMPLETE",
            "complete.json",
            "Scan budget exhausted",
            "Increase explicit limits",
        )
    except ValueError, KeyError, TypeError, OSError, UnicodeError, IndexError, AttributeError:
        malformed = True
        scan.add(
            "inspection",
            "FAIL",
            "complete.json",
            "Malformed or unsupported bundle",
            "Inspect the input manifest",
        )
    checks = scan.report["checks"]
    failed = any(c["status"] == "FAIL" for c in checks)
    incomplete = any(c["status"] == "INCOMPLETE" for c in checks)
    code = 2 if malformed else 1 if failed else 3 if incomplete else 0
    binary_checked = any(c["rule"] == "signal.binary" and c["status"] == "PASS" for c in checks)
    scan.report.update(
        exit_code=code,
        structural_status="FAIL"
        if failed
        else "INCOMPLETE"
        if incomplete or not binary_checked
        else "PASS",
        validation_complete=not failed and not incomplete and binary_checked,
        scan={
            "max_bytes": budget.max_bytes,
            "max_records": budget.max_records,
            "max_seconds": budget.max_seconds,
            "bytes_read": budget.bytes_scanned,
            "records_read": budget.records_scanned,
            "coverage": "Only listed checks; binary readability is separate from checksums",
        },
        next_actions=list(
            dict.fromkeys(
                c["action"]
                for c in checks
                if c["status"] not in ("PASS", "NOT_APPLICABLE") and c["action"]
            )
        ),
    )
    scan.report["declared"] = shareable(scan.report["declared"])
    return scan.report, code


def markdown(report: dict[str, Any]) -> str:
    def escape(value: object) -> str:
        text = value if isinstance(value, str) else canonical(value)
        return (
            html.escape(text)
            .replace("\\", "\\\\")
            .replace("|", "\\|")
            .replace("`", "\\`")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace("\n", " ")
            .replace("\r", " ")
        )

    result = [
        "# Model-bundle inspection",
        "",
        report["notice"],
        "",
        "Bundle version: " + escape(report["bundle_version"]),
        "",
        "Inspector: "
        + escape(report["inspection_version"])
        + " (source SHA256 "
        + escape(report["inspector_sha256"])
        + ")",
        "",
        "Structural status: " + report["structural_status"] + "; model_ready: false",
        "",
        "## Declared inputs",
        "",
        "| Field | Value |",
        "| --- | --- |",
    ]
    result.extend(f"| {escape(k)} | {escape(v)} |" for k, v in report["declared"].items())
    result.extend(
        [
            "",
            "## Checks and remaining requirements",
            "",
            "| Check | Result | Source | Observed | Reason | Next action |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    result.extend(
        "| "
        + " | ".join(
            escape(c[k]) for k in ("rule", "status", "source", "observed", "reason", "action")
        )
        + " |"
        for c in report["checks"]
    )
    result.extend(["", "Scan coverage: " + escape(report["scan"]), ""])
    return "\n".join(result)


def command(args: argparse.Namespace) -> tuple[Any, int]:
    if args.output:
        destination = args.output.resolve()
        require(
            not destination.is_relative_to(args.input.resolve()),
            "Report output must be outside the input bundle",
        )
        require(
            not args.output.exists() and not args.output.is_symlink(),
            "Report output already exists",
        )
    report, code = inspect_bundle(
        args.input,
        budget=Budget(args.max_bytes, args.max_records, args.max_seconds),
        bigwig_python=args.bigwig_python,
    )
    rendered = markdown(report) if args.format == "markdown" else canonical(report)
    if args.output:
        try:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(rendered.rstrip("\n") + "\n")
        except OSError:
            raise ValueError(
                "Cannot create report; use a new path in an existing writable directory"
            ) from None
    return rendered, code
