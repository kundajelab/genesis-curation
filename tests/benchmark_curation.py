"""Opt-in bounded synthetic catalog benchmark; no scale guarantee or sequencing execution."""

from __future__ import annotations

import argparse
import contextlib
import copy
import resource
import tempfile
import time
from pathlib import Path

from verify_curation import fixture

from genesis_curation.contracts.records import dump, fingerprint, identity, record
from genesis_curation.registry import store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libraries", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.libraries <= 10000 or not 1 <= args.batch_size <= 100:
        raise ValueError("Bounded benchmark: 1–10000 libraries, batches of 1–100")
    started = time.perf_counter()
    startup_rusage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    imported_seconds = 0.0
    with tempfile.TemporaryDirectory(prefix="genesis-catalog-benchmark-") as temporary:
        root = Path(temporary)
        base = fixture(root / "fixture")["data"]
        directory = root / "registry"
        store.initialize(directory)
        for offset in range(0, args.libraries, args.batch_size):
            manifests = []
            for i in range(offset, min(offset + args.batch_size, args.libraries)):
                data = copy.deepcopy(base)
                name = f"synthetic-{i:05d}"
                dataset = identity("dataset", "scale-fixture", name)
                data.update(dataset_id=dataset, name=name, library=name, measurements=[])
                data["metadata"].update(tissue="synthetic leaf" if i % 2 else "synthetic root")
                data["run"]["provenance_complete"] = False
                data["artifacts"] = [
                    {
                        **base["artifacts"][0],
                        "id": identity("artifact", dataset, str(n)),
                        "path": f"/synthetic/catalog/{name}/file{n}.bed",
                        "worker": f"node-{i % 4}",
                        "availability": "unavailable",
                        "sha256": None,
                        "size": None,
                    }
                    for n in range(10)
                ]
                manifests.append(record("manifest", data, dataset))
            content = {"manifests": manifests, "validations": []}
            value = record("bundle", content, identity("bundle", fingerprint(content)))
            begin = time.perf_counter()
            assert store.import_bundle(directory, value)["imported"] == len(manifests)
            imported_seconds += time.perf_counter() - begin
        with contextlib.closing(store.connect(directory)) as db:
            begin = time.perf_counter()
            rows = store.search(db, text="synthetic leaf", limit=100)
            query_seconds = time.perf_counter() - begin
            assert len(rows) == min(100, args.libraries // 2)
            counts = {
                table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in ("datasets", "locations", "records")
            }
            assert counts["datasets"] == args.libraries
            assert counts["locations"] == args.libraries * 10
            fts = db.execute("SELECT value FROM meta WHERE key='fts5'").fetchone()[0]
        evidence = {
            "fixture": "synthetic-only, no sequencing bytes scanned",
            "counts": counts,
            "batch_size": args.batch_size,
            "import_seconds": imported_seconds,
            "query_seconds": query_seconds,
            "query_returned": len(rows),
            "fts5": fts,
            "total_seconds": time.perf_counter() - started,
            "process_peak_rss_platform_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "startup_rusage_maxrss": startup_rusage,
            "process_vm_hwm_kib": next(
                (
                    int(line.split()[1])
                    for line in Path("/proc/self/status").read_text().splitlines()
                    if line.startswith("VmHWM:")
                ),
                None,
            )
            if Path("/proc/self/status").exists()
            else None,
            "memory_limitation": (
                "RUSAGE_SELF can retain a pre-exec launcher high-water value on Linux; "
                "VmHWM describes this process image, never whole-host RAM"
            ),
            "rss_unit": "KiB on Linux; bytes on macOS; process only",
            "database_bytes": sum(p.stat().st_size for p in directory.glob("registry.sqlite*")),
        }
        dump(args.output, evidence, immutable=True)
    print("PASS: bounded synthetic catalog benchmark; measurements: " + str(args.output))


if __name__ == "__main__":
    main()
