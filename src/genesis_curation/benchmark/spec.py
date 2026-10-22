"""Strict versioned manifests, content identities and read-only experiment planning."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

VERSION = 1
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
IMAGES = {
    "alignment": (
        "kundajelab/dap_seq_alignment@sha256:"
        "4764008ba8fbb5d831bd6f9ed6a720fd494062b39f0bae63ff2e4efd316ad842"
    ),
    "peaks": (
        "kundajelab/dap_seq_peaks@sha256:"
        "10467dd29e3d25ce7769f76f57402bd1a5a582006e911d06479bfcc3e3325df3"
    ),
    "qc": (
        "kundajelab/dap_seq_qc@sha256:"
        "ace177a497934a26adb305bc0dc92cc6a447fe8ca714b79f72a007c7acb53239"
    ),
    "tracks": (
        "kundajelab/dap_seq_tracks@sha256:"
        "e6dd6ceb79e50fc6db83e9346a920e51fd2bf0dcffbc18c9305b1b6e8c806390"
    ),
    "tools": (
        "kundajelab/genesis_tools@sha256:"
        "31458d4a6a83541f463ea4b0e692a0afc65c6df441f70c3f71fc4ccf1a11eb59"
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def keys(value: object, allowed: set[str], required: set[str], where: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: expected an object")
    if extra := value.keys() - allowed:
        raise ValueError(f"{where}: unsupported fields {sorted(extra)}")
    if missing := required - value.keys():
        raise ValueError(f"{where}: missing fields {sorted(missing)}")


def identifier(value: object) -> None:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError("Identifiers must be unique safe filename components")


def positive(value: object, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name}: expected a positive integer")


def public_url(url: str) -> None:
    if not isinstance(url, str):
        raise ValueError("Public URL must be a string")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Fetch supports public HTTPS URLs without credentials only")
    if parsed.query or parsed.fragment:
        raise ValueError("Fetch URLs must not contain query strings or fragments")


def source_identity() -> dict[str, Any]:
    """Hash the executing package; attach Git metadata only for its own checkout."""
    from importlib.metadata import PackageNotFoundError, version

    root = Path(__file__).resolve().parents[3]
    # Resolve once and derive every path from it: relative_to fails when the package and its files
    # are spelled through different symlinks (e.g. macOS /var and /private/var).
    benchmark = Path(__file__).resolve().parent
    package = benchmark.parent
    paths = sorted(p for p in benchmark.iterdir() if p.suffix in {".py", ".html"}) + [
        package / "atac/fragments.py"
    ]
    identity: dict[str, Any] = {
        "git_sha": None,
        "git_status": None,
        "source_kind": "installed-package",
        "recipe_sha256": {str(p.relative_to(package)): sha256(p) for p in paths},
    }
    try:
        identity["distribution_version"] = version("genesis-curation")
    except PackageNotFoundError:
        identity["distribution_version"] = None
    # Git searches parent directories. An installed wheel inside an unrelated
    # checkout must not acquire that checkout's revision as its code identity.
    expected = root / "src/genesis_curation/benchmark/spec.py"
    if expected.resolve() == Path(__file__).resolve():
        if not (root / ".git").exists():
            identity.update(
                source_kind="source-tree",
                git_metadata_status="UNAVAILABLE",
                git_metadata_reason="No Git repository at the source root",
            )
            return identity
        identity["source_kind"] = "checkout"
        try:
            values = []
            for arguments in (("rev-parse", "HEAD"), ("status", "--porcelain")):
                values.append(
                    subprocess.run(
                        ["git", "-C", str(root), *arguments],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    ).stdout.strip()
                )
            identity.update(git_sha=values[0], git_status=values[1])
        except (OSError, subprocess.SubprocessError) as error:
            identity["git_metadata_status"] = "UNAVAILABLE"
            identity["git_metadata_reason"] = type(error).__name__
    return identity


def load(path: Path, *, verify: bool = True) -> dict[str, Any]:
    path = path.resolve()
    with path.open("rb") as stream:
        spec = json.load(stream) if path.suffix == ".json" else tomllib.load(stream)
    if not isinstance(spec, dict):
        raise ValueError("Experiment must be an object")
    allowed = {
        "schema_version",
        "id",
        "assay",
        "adapter",
        "dataset",
        "baseline",
        "comparison",
        "seed",
        "parameters",
        "limits",
        "images",
        "workflow",
        "performance",
    }
    method_comparison = spec.get("schema_version") == 2
    if method_comparison:
        allowed |= {"methods", "environment"}
    keys(spec, allowed, allowed - {"images", "workflow", "performance"}, "experiment")
    if type(spec["schema_version"]) is not int or spec["schema_version"] not in {1, 2}:
        raise ValueError("Unsupported experiment schema version")
    identifier(spec["id"])
    if spec["assay"] not in {"DAP-seq", "bulk-ATAC"}:
        raise ValueError("Only DAP-seq and bulk-ATAC are implemented")
    if spec["adapter"] not in {"postalign", "genesis-atac", "nfcore-atac", "methods"}:
        raise ValueError("Unsupported adapter")
    if method_comparison != (spec["adapter"] == "methods"):
        raise ValueError("Method comparisons require schema version 2 and adapter methods")
    if spec["comparison"] not in {
        "matched-stage",
        "workflow-native",
        "workflow-matched",
        "workflow-configured",
    }:
        raise ValueError("Explicit comparison mode is required")
    if type(spec["seed"]) is not int:
        raise ValueError("seed must be an integer")
    limits = spec["limits"]
    limit_keys = {"cpus", "memory_gb", "seconds", "disk_bytes", "download_bytes", "max_arms"}
    keys(limits, limit_keys, limit_keys, "limits")
    for name, value in limits.items():
        if name == "download_bytes" and value == 0:
            continue
        positive(value, name)
    parameters = spec["parameters"]
    keys(
        parameters,
        {"mapq", "duplicates", "keep_dup", "qvalue", "spp", "quantify", "template_cap"},
        {"mapq", "duplicates", "keep_dup", "qvalue", "spp", "quantify"},
        "parameters",
    )
    if "template_cap" in parameters:
        positive(parameters["template_cap"], "template_cap")
        if spec["adapter"] == "postalign":
            raise ValueError("Raw template subsampling is a separate upstream experiment")
    if not isinstance(parameters["mapq"], list) or not parameters["mapq"]:
        raise ValueError("mapq must be a nonempty list")
    if any(type(q) is not int or q < 0 or q > 254 for q in parameters["mapq"]):
        raise ValueError("MAPQ must be 0..254; 255 denotes unavailable MAPQ, not confidence")
    if not isinstance(parameters["duplicates"], list) or not parameters["duplicates"]:
        raise ValueError("duplicates must be a nonempty list")
    if set(parameters["duplicates"]) - {"retained", "marked", "excluded"}:
        raise ValueError("Unsupported duplicate policy")
    if parameters["keep_dup"] not in {"1", "all"}:
        raise ValueError("Explicit MACS keep_dup must be '1' or 'all'")
    qvalue = parameters["qvalue"]
    if type(qvalue) not in {int, float} or not 0 < qvalue < 1:
        raise ValueError("qvalue must be between zero and one")
    if any(type(parameters[k]) is not bool for k in ("spp", "quantify")):
        raise ValueError("spp and quantify must be booleans")
    if spec["assay"] == "bulk-ATAC" and parameters["spp"]:
        raise ValueError("DAP SPP assumptions cannot be inherited by ATAC")
    arms = [
        {"id": f"mapq{q}-{d}", "mapq": q, "duplicates": d}
        for q, d in itertools.product(parameters["mapq"], parameters["duplicates"])
    ]
    if len({a["id"] for a in arms}) != len(arms):
        raise ValueError("Duplicate experiment arms")
    if method_comparison:
        from .methods import validate_methods

        arms = validate_methods(spec, path.parent)
    if spec["baseline"] not in {a["id"] for a in arms}:
        raise ValueError("Baseline must identify exactly one declared arm")
    performance = spec.get("performance", {"repetitions": 1, "random_order": False})
    keys(
        performance, {"repetitions", "random_order"}, {"repetitions", "random_order"}, "performance"
    )
    positive(performance["repetitions"], "repetitions")
    if type(performance["random_order"]) is not bool:
        raise ValueError("random_order must be boolean")
    if len(arms) * performance["repetitions"] > limits["max_arms"]:
        raise ValueError("Expanded matrix exceeds max_arms")
    dataset_path = (path.parent / spec["dataset"]).resolve()
    dataset = json.loads(dataset_path.read_text())
    dkeys = {
        "schema_version",
        "id",
        "assay",
        "synthetic",
        "status",
        "reason",
        "reference",
        "assets",
        "libraries",
        "description",
        "source",
        "thresholds",
    }
    keys(dataset, dkeys, dkeys - {"reason", "description", "source", "thresholds"}, "dataset")
    identifier(dataset["id"])
    if dataset["schema_version"] != VERSION or dataset["assay"] != spec["assay"]:
        raise ValueError("Dataset schema/assay mismatch")
    if type(dataset["synthetic"]) is not bool:
        raise ValueError("synthetic must be boolean")
    blockers = []
    if dataset["status"] != "READY":
        if dataset["status"] not in {"NEEDS_REFERENCE", "NEEDS_METADATA", "NEEDS_FILES"}:
            raise ValueError("Unknown dataset status")
        blockers.append(dataset.get("reason", dataset["status"]))
    assets = {}
    for name, asset in dataset["assets"].items():
        identifier(name)
        keys(asset, {"path", "sha256", "url", "bytes", "role"}, {"path", "sha256"}, name)
        if not isinstance(asset["sha256"], str) or not SHA.fullmatch(asset["sha256"]):
            raise ValueError(f"{name}: exact SHA256 required")
        if "url" in asset:
            public_url(asset["url"])
            positive(asset.get("bytes"), f"{name}.bytes")
        local = (dataset_path.parent / asset["path"]).resolve()
        assets[name] = dict(asset, path=str(local))
        if not local.is_file():
            blockers.append(f"Missing asset: {name}")
        elif verify and sha256(local) != asset["sha256"]:
            raise ValueError(f"Input checksum mismatch: {name}")
    if dataset["status"] == "READY":
        validate_dataset(dataset, assets, spec)
    if spec["adapter"] not in {"postalign", "methods"}:
        workflow = spec.get("workflow", {})
        keys(
            workflow,
            {"checkout", "git_sha", "container_lock", "container_lock_sha256", "parameters"},
            {"checkout", "git_sha", "parameters", "container_lock", "container_lock_sha256"},
            "workflow",
        )
        lock_path = (path.parent / workflow["container_lock"]).resolve()
        if not lock_path.is_file() or sha256(lock_path) != workflow["container_lock_sha256"]:
            raise ValueError("Workflow container lock missing or checksum mismatch")
        workflow["container_lock"] = str(lock_path)
        checkout = (path.parent / workflow["checkout"]).resolve()
        spec["workflow"] = dict(workflow, checkout=str(checkout))
        if not re.fullmatch(r"[0-9a-f]{40}", workflow["git_sha"]):
            raise ValueError("Workflow requires an exact git SHA")
        if not checkout.is_dir():
            blockers.append("Missing pinned workflow checkout")
        else:
            actual = subprocess.check_output(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
            ).strip()
            dirty = subprocess.check_output(
                ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"],
                text=True,
            ).strip()
            if actual != workflow["git_sha"] or dirty:
                raise ValueError("Workflow checkout differs from pinned clean source")
        if spec["adapter"] == "genesis-atac" and not dataset["synthetic"]:
            raise ValueError("Genesis ATAC prototype is validated for synthetic inputs only")
        if len(arms) != 1:
            raise ValueError("Workflow adapters use one explicitly configured arm per manifest")
    elif spec["comparison"] != "matched-stage":
        raise ValueError("Post-alignment experiments must declare matched-stage")
    images = dict(IMAGES, **spec.get("images", {}))
    if images.keys() != IMAGES.keys():
        raise ValueError("Unsupported image role")
    for value in images.values():
        if not re.fullmatch(r"(?:[a-zA-Z0-9_./:-]+@)?sha256:[0-9a-f]{64}", value):
            raise ValueError("Images must use immutable repository digests or local image IDs")
    spec["images"] = images
    spec["performance"] = performance
    result = {
        "spec": spec,
        "dataset": dataset,
        "assets": assets,
        "arms": arms,
        "blockers": blockers,
        "status": "BLOCKED" if blockers else "READY",
        "manifest_sha256": sha256(path),
        "dataset_sha256": sha256(dataset_path),
        "source": source_identity(),
        "manifest": str(path),
    }
    # Git labels and dirty-status text are provenance, not recipe identity: committing
    # identical source bytes must not invalidate an otherwise identical experiment.
    result["experiment_id"] = identity(
        {k: result[k] for k in ("spec", "dataset_sha256", "arms")}
        | {"recipe": result["source"]["recipe_sha256"]}
    )
    if spec["adapter"] not in {"postalign", "methods"} and dataset["status"] == "READY":
        from .workflows import validate_options

        validate_options(result)
    return result


def validate_dataset(dataset: dict[str, Any], assets: dict[str, Any], spec: dict[str, Any]) -> None:
    ref = dataset["reference"]
    rkeys = {
        "id",
        "species",
        "assembly",
        "cultivar",
        "fasta",
        "fasta_content_sha256",
        "chrom_sizes",
        "genome_size",
        "mitochondrial",
        "plastid",
        "tss",
        "annotation_release",
        "gtf",
        "effective_genome_size",
    }
    keys(
        ref,
        rkeys,
        rkeys - {"tss", "annotation_release", "gtf", "effective_genome_size"},
        "reference",
    )
    if "effective_genome_size" in ref:
        positive(ref["effective_genome_size"], "effective_genome_size")
    if not SHA.fullmatch(ref["fasta_content_sha256"]):
        raise ValueError("Decompressed FASTA SHA256 required")
    positive(ref["genome_size"], "genome_size")
    for key in ("mitochondrial", "plastid"):
        if not isinstance(ref[key], list) or any(not isinstance(x, str) for x in ref[key]):
            raise ValueError(
                "Explicit organellar contig lists required (empty only if known absent)"
            )
    for key in ("fasta", "chrom_sizes", "tss", "gtf"):
        if key in ref and ref[key] not in assets:
            raise ValueError(f"Missing reference asset {key}")
    if not dataset["libraries"]:
        raise ValueError("At least one library required")
    libraries = {}
    for lib in dataset["libraries"]:
        lkeys = {
            "id",
            "layout",
            "control",
            "raw_templates",
            "bam",
            "marked_bam",
            "spp_bam",
            "spp_marked_bam",
            "read1",
            "read2",
            "biological_replicate",
            "tissue",
            "assay_subtype",
            "tf",
            "fastqc",
            "marking_method",
        }
        keys(
            lib,
            lkeys,
            {"id", "layout", "control", "raw_templates", "biological_replicate"},
            "library",
        )
        identifier(lib["id"])
        if lib["id"] in libraries:
            raise ValueError("Duplicate library ID; entries cannot overwrite one another")
        libraries[lib["id"]] = lib
        if lib["layout"] not in {"SE", "PE"} or (
            spec["assay"] == "bulk-ATAC" and lib["layout"] != "PE"
        ):
            raise ValueError("Unsupported library layout")
        positive(lib["raw_templates"], "raw_templates")
        for key in ("bam", "marked_bam", "spp_bam", "spp_marked_bam", "read1", "read2"):
            if key in lib and lib[key] not in assets:
                raise ValueError(f"Unknown library asset: {key}")
        if spec["adapter"] == "postalign":
            if "bam" not in lib:
                raise ValueError("Post-alignment adapter requires a BAM per library")
            if set(spec["parameters"]["duplicates"]) - {"retained"} and "marked_bam" not in lib:
                raise ValueError("Duplicate comparisons require a separately marked BAM")
            if "marked_bam" in lib and not lib.get("marking_method"):
                raise ValueError("Marked BAM requires explicit marking method/version provenance")
            if spec["parameters"]["spp"]:
                if "spp_bam" not in lib or ("marked_bam" in lib and "spp_marked_bam" not in lib):
                    raise ValueError("SPP requires independently aligned first-50 R1 artifacts")
        elif "read1" not in lib or (lib["layout"] == "PE" and "read2" not in lib):
            raise ValueError("Workflow adapters require paired raw FASTQs")
    for lib in libraries.values():
        control = lib["control"]
        if control is not None:
            if control not in libraries or control == lib["id"]:
                raise ValueError("Invalid treatment/control association")
            if (
                libraries[control]["control"] is not None
                or libraries[control]["layout"] != lib["layout"]
            ):
                raise ValueError("Controls must be control libraries of the same layout")
        if spec["assay"] == "bulk-ATAC" and control is not None:
            raise ValueError("DAP control assumptions cannot be inherited by ATAC")
    replicate_pairs(dataset)


def replicate_pairs(dataset: dict[str, Any]) -> list[list[str]]:
    """Validate explicit, disjoint replicate pairs before any expensive work."""
    source = dataset.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("Dataset source must be an object")
    pairs = source.get("replicate_pairs", [])
    if not isinstance(pairs, list):
        raise ValueError("replicate_pairs must be a list")
    libraries = {lib["id"] for lib in dataset["libraries"]}
    used: set[str] = set()
    for pair in pairs:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(name, str) for name in pair)
            or len(set(pair)) != 2
            or not set(pair) <= libraries
        ):
            raise ValueError("Replicate pairs must name two distinct recorded libraries")
        if used.intersection(pair):
            raise ValueError("Replicate pairs must be disjoint; no implicit pooled comparison")
        if dataset["synthetic"] or dataset["assay"] != "bulk-ATAC":
            raise ValueError("This biological replicate assessment requires real bulk ATAC")
        used.update(pair)
    return pairs
