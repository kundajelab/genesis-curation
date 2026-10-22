"""Known-answer checks for preserving evidence before optional work removal."""

from __future__ import annotations

import json
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

from archive import archive


def fixture(root: Path) -> Path:
    source = root / "run"
    source.mkdir()
    (root / "reads.fastq.gz").write_bytes(b"original-input")
    (source / "experiment.json").write_text(
        json.dumps(
            {
                "spec": {"adapter": "methods"},
                "assets": {"reads": {"path": str(root / "reads.fastq.gz")}},
                "experiment_id": "fixture",
            }
        )
    )
    (source / "results.json").write_text(
        json.dumps({"status": "COMPLETE", "results": [{"arm": "baseline", "repetition": 1}]})
    )
    for name in ("metrics.tsv", "processing.tsv", "resources.tsv", "library-qc.tsv"):
        (source / name).write_text("header\nobservation\n")
    peaks = source / "baseline-r1/library/peaks"
    peaks.mkdir(parents=True)
    (peaks / "result.narrowPeak").write_text("chr1\t10\t20\n")
    (peaks / "genrich.query.bam").write_bytes(b"large-genrich-input")
    (peaks.parent / "usable.bam").write_bytes(b"large-regenerable-bam")
    (source / "indexes").mkdir()
    (source / "indexes/reference.index").write_bytes(b"regenerable-index")
    return source


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = fixture(root)
        record = archive(source, root / "indexes-only", prune_indexes=True)
        if (source / "indexes").exists() or not (
            source / "baseline-r1/library/usable.bam"
        ).exists():
            raise AssertionError("Index-only pruning must retain BAMs")
        if record["pruned_work"] != [str(source / "indexes")]:
            raise AssertionError("Unexpected index-only pruning scope")
        original = (root / "indexes-only/manifest.json").read_bytes()
        (root / "indexes-only/evidence.tar.gz").write_bytes(b"damaged archive")
        try:
            archive(
                source,
                root / "must-not-prune",
                prune_work=True,
                previous_archive=root / "indexes-only",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Damaged prior archive allowed further removal")
        assert (source / "baseline-r1/library/usable.bam").exists()
        assert (root / "indexes-only/manifest.json").read_bytes() == original
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = fixture(root)
        prior = archive(source, root / "indexes-only", prune_indexes=True)
        original = (root / "indexes-only/manifest.json").read_bytes()
        final = archive(
            source,
            root / "all-work",
            prune_work=True,
            previous_archive=root / "indexes-only",
        )
        assert final["previous_archive"]["archive_sha256"] == prior["archive_sha256"]
        assert (root / "indexes-only/manifest.json").read_bytes() == original
        assert not (source / "baseline-r1").exists()
        assert (root / "reads.fastq.gz").read_bytes() == b"original-input"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = fixture(root)
        record = archive(source, root / "evidence")
        if record["pruned_work"] or not (source / "baseline-r1/library/usable.bam").exists():
            raise AssertionError("Archiving alone must preserve work")
        with tarfile.open(root / "evidence/evidence.tar.gz") as tar:
            names = tar.getnames()
        if "baseline-r1/library/peaks/result.narrowPeak" not in names or any(
            n.endswith(".bam") for n in names
        ):
            raise AssertionError("Compact evidence must retain peaks and exclude BAMs")
        record = archive(source, root / "pruned-evidence", prune_work=True)
        if record["prune_status"] != "PRUNED" or (source / "indexes").exists():
            raise AssertionError("Completed work was not pruned")
        if (root / "reads.fastq.gz").read_bytes() != b"original-input":
            raise AssertionError("An original input changed")
        if not (source / "results.json").exists() or (source / ".running").exists():
            raise AssertionError("Measurements or exclusive lock lifecycle are incorrect")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = fixture(root)
        (source / ".running").write_text("another writer")
        try:
            archive(source, root / "blocked", prune_work=True)
        except FileExistsError:
            pass
        else:
            raise AssertionError("An active experiment was archived")
        if (source / ".running").read_text() != "another writer":
            raise AssertionError("Another writer's lock changed")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = fixture(root)
        # A removal failure must retain a verified archive and a durable record
        # showing which directory may be only partially removed.
        with patch("archive.shutil.rmtree", side_effect=OSError("interrupted removal")):
            try:
                archive(source, root / "interrupted", prune_work=True)
            except OSError:
                pass
            else:
                raise AssertionError("Injected removal failure was lost")
        record = json.loads((source / "retention.json").read_text())
        if record["prune_status"] != "PRUNING" or not record["current_pruning"]:
            raise AssertionError("Interrupted pruning lacks a durable state")
        with tarfile.open(root / "interrupted/evidence.tar.gz") as tar:
            if "results.json" not in tar.getnames():
                raise AssertionError("Removal started without complete evidence")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = fixture(root)
        (source / "results.json").write_text(json.dumps({"status": "FAILED", "error": "stopped"}))
        (source / "metrics.tsv").unlink()
        record = archive(source, root / "failed-evidence")
        if record["status"] != "FAILED" or record["missing_tables"] != ["metrics.tsv"]:
            raise AssertionError("Incomplete evidence was fabricated or lost")
        try:
            archive(source, root / "refused-pruning", prune_work=True)
        except ValueError:
            pass
        else:
            raise AssertionError("Failed work must remain available for diagnosis")
    print(
        "PASS compact evidence, incomplete runs, explicit pruning, "
        "writer isolation and interruption"
    )


if __name__ == "__main__":
    main()
