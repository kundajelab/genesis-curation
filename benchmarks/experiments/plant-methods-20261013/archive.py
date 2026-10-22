"""Keep compact method evidence; optionally remove completed, reproducible work.

The archive includes measurements, peaks, command logs, task hashes and figures.
It excludes sequencing reads, BAMs and reference indexes. Pruning is explicit and
prevents execution resume; reproduce that experiment in a new result directory.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.spec import identifier, sha256


def verify_archive(packed: Path, manifest: dict[str, Any]) -> None:
    """Verify every regular member, without extracting filesystem paths."""
    verified = set()
    with tarfile.open(packed, "r:gz") as tar:
        for item in tar:
            if not item.isfile() or item.name not in manifest or item.name in verified:
                raise ValueError("Archive contains an unexpected or repeated member")
            stream = tar.extractfile(item)
            if stream is None or item.size != manifest[item.name]["bytes"]:
                raise ValueError("Archive member size differs")
            with stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != manifest[item.name]["sha256"]:
                raise ValueError("Archive verification failed")
            verified.add(item.name)
    if verified != set(manifest):
        raise ValueError("Archive omitted evidence")


def archive(
    source: Path,
    out: Path,
    *,
    prune_work: bool = False,
    prune_indexes: bool = False,
    previous_archive: Path | None = None,
) -> dict[str, Any]:
    source, out = source.resolve(), out.resolve()
    if out.is_relative_to(source) or out.exists():
        raise ValueError("Choose a new archive directory outside the experiment")
    if (source / "retention.json").exists() and previous_archive is None:
        raise ValueError(
            "Work has a retention record; supply its --previous-archive to extend pruning"
        )
    lock = source / ".running"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(f"archive pid={os.getpid()}\n")
        previous = None
        if previous_archive is not None:
            previous_archive = previous_archive.resolve()
            if previous_archive.is_relative_to(source):
                raise ValueError("Previous archive must be outside the experiment")
            previous_manifest = previous_archive / "manifest.json"
            prior = json.loads(previous_manifest.read_text())
            retention = source / "retention.json"
            if (
                prior != json.loads(retention.read_text())
                or prior["prune_status"] != "PRUNED"
                or sha256(previous_archive / "evidence.tar.gz") != prior["archive_sha256"]
            ):
                raise ValueError("Previous archive/retention identity or completion differs")
            verify_archive(previous_archive / "evidence.tar.gz", prior["members"])
            for name in ("experiment.json", "results.json"):
                if sha256(source / name) != prior["members"][name]["sha256"]:
                    raise ValueError("Experiment changed since the previous archive")
            previous = {
                "archive_sha256": prior["archive_sha256"],
                "manifest_sha256": sha256(previous_manifest),
                "retention_sha256": sha256(retention),
                "pruned_work": prior["pruned_work"],
            }
        plan = json.loads((source / "experiment.json").read_text())
        result = json.loads((source / "results.json").read_text())
        if plan["spec"]["adapter"] != "methods":
            raise ValueError("This archive recipe is for native method experiments")
        if (prune_work or prune_indexes) and result["status"] != "COMPLETE":
            raise ValueError("Keep work from incomplete experiments for diagnosis")
        work = {source / "indexes"}
        for case in result.get("results", []):
            identifier(case["arm"])
            rep = case["repetition"]
            if type(rep) is not int or rep < 1:
                raise ValueError("Invalid repetition")
            if not prune_indexes or prune_work:
                work.add(source / f"{case['arm']}-r{rep}")
        for folder in work:
            if folder.is_symlink():
                raise ValueError("Work must not be a symlink")
            if any(
                Path(a["path"]).resolve().is_relative_to(folder) for a in plan["assets"].values()
            ):
                raise ValueError("An input lies inside work selected for pruning")
        selected = []
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.is_symlink() or path == lock:
                continue
            # Genrich keeps a name-sorted BAM beside its peak output. Directory
            # membership alone must not include that large sequencing artifact.
            if path.suffix in {".bam", ".bai", ".cram", ".crai"} or ".fastq" in path.name:
                continue
            relative = path.relative_to(source)
            if (
                path.parent == source
                or relative.parts[0] in {"commands", "invocations", "figures"}
                or path.suffix == ".json"
                or "peaks" in relative.parts[:-1]
                or path.name == "bedtools.tsv"
            ):
                selected.append(path)
        manifest = {
            str(p.relative_to(source)): {"sha256": sha256(p), "bytes": p.stat().st_size}
            for p in selected
        }
        out.mkdir(parents=True)
        packed = out / "evidence.tar.gz"
        with (
            packed.open("wb") as raw,
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed,
        ):
            with tarfile.open(fileobj=compressed, mode="w|") as tar:
                for path in selected:
                    info = tar.gettarinfo(str(path), arcname=str(path.relative_to(source)))
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
        # Verify all archived bytes before any removal, without extracting paths.
        verify_archive(packed, manifest)
        missing_tables = []
        for name in (
            "metrics.tsv",
            "processing.tsv",
            "resources.tsv",
            "library-qc.tsv",
        ):
            if (source / name).is_file():
                shutil.copyfile(source / name, out / name)
            else:
                missing_tables.append(name)
        if (source / "figures").is_dir():
            shutil.copytree(source / "figures", out / "figures")
        record: dict[str, Any] = {
            "schema_version": 1,
            "experiment_id": plan["experiment_id"],
            "source": str(source),
            "status": result["status"],
            "script_sha256": sha256(Path(__file__)),
            "archive_sha256": sha256(packed),
            "archive_bytes": packed.stat().st_size,
            "members": manifest,
            "pruned_work": [],
            "prune_status": "NOT_REQUESTED",
            "missing_tables": missing_tables,
            "previous_archive": previous,
            "retention": (
                "Inputs stay at their original locations; BAMs/indexes are not in this archive"
            ),
        }

        def save_record() -> None:
            for path in (out / "manifest.json", source / "retention.json"):
                partial = path.with_suffix(path.suffix + ".partial")
                partial.write_text(json.dumps(record, indent=2) + "\n")
                partial.replace(path)

        if prune_work or prune_indexes:
            # The runner uses this same exclusive lock. Recheck identities before
            # pruning, even though cooperating execution cannot write meanwhile.
            for name in ("experiment.json", "results.json"):
                if sha256(source / name) != manifest[name]["sha256"]:
                    raise ValueError("Experiment changed during archiving")
            record["prune_status"] = "PRUNING"
            record["planned_pruning"] = [str(p) for p in sorted(work) if p.exists()]
            record["retention"] += (
                "; listed work intentionally pruned; execution resume unavailable"
            )
            save_record()
            for folder in sorted(work):
                if folder.exists():
                    record["current_pruning"] = str(folder)
                    save_record()
                    shutil.rmtree(folder)
                    record["pruned_work"].append(str(folder))
                    save_record()
            record["prune_status"] = "PRUNED"
            record["current_pruning"] = None
            save_record()
        (out / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
        return record
    finally:
        lock.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prune-work", action="store_true")
    parser.add_argument(
        "--previous-archive",
        type=Path,
        help="Verify a prior completed archive before removing additional work",
    )
    parser.add_argument(
        "--prune-indexes",
        action="store_true",
        help="Retain BAMs for further assessments; remove only completed indexes",
    )
    args = parser.parse_args()
    record = archive(
        args.source,
        args.out,
        prune_work=args.prune_work,
        prune_indexes=args.prune_indexes,
        previous_archive=args.previous_archive,
    )
    print(record["archive_sha256"], record["archive_bytes"], "bytes")
