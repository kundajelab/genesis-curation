"""Record bounded bidirectional minimap2 assembly alignments for reference comparisons."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from genesis_curation.benchmark.runtime import Runtime
from genesis_curation.benchmark.spec import identity, sha256, write_json


def align(panel: Path, left: str, right: str, tool: Path, out: Path, memory_gib: int = 8) -> None:
    if out.exists():
        raise FileExistsError("Choose a new assembly alignment directory")
    if len(os.sched_getaffinity(0)) > 2:
        raise ValueError("Run assembly preparation with an explicit two-CPU affinity")
    if memory_gib not in {8, 10, 48}:
        raise ValueError("Choose an explicit 8, 10 or 48 GiB assembly-mapping budget")
    document = json.loads(panel.read_text())
    if document["schema_version"] != 1:
        raise ValueError("Unsupported reference panel")
    entries = {r["id"]: r for r in document["references"]}
    refs = [entries[left], entries[right]]
    if refs[0]["species"] != refs[1]["species"]:
        raise ValueError("This panel compares references of the same species")
    paths = [(panel.parent / r["fasta"]["path"]).resolve() for r in refs]
    hashes = [r["fasta"]["sha256"] for r in refs]
    if [sha256(p) for p in paths] != hashes:
        raise ValueError("Reference changed")
    source = {
        "script_sha256": sha256(Path(__file__)),
        "minimap2_sha256": sha256(tool),
        "fasta_sha256": hashes,
    }
    plan = {
        "experiment_id": identity(source),
        "source": source,
        "spec": {
            "limits": {
                "cpus": 2,
                "memory_gb": memory_gib,
                "seconds": 28800,
                "disk_bytes": 5000000000,
            }
        },
    }
    runtime = Runtime(plan, out, resume=False)
    mapping = {
        "schema_version": 1,
        "left_fasta_sha256": hashes[0],
        "right_fasta_sha256": hashes[1],
        "source": (
            "Minimap2 asm5 in both directions; CIGAR; secondary alignments retained, "
            "up to 50 alternatives"
        ),
        "identities": source,
    }
    try:
        for label, target, query in (
            ("forward", paths[1], paths[0]),
            ("reverse", paths[0], paths[1]),
        ):
            command = [
                str(tool),
                "-x",
                "asm5",
                "-c",
                "--cs",
                "--secondary=yes",
                "-N",
                "50",
                "-t",
                "2",
                str(target),
                str(query),
            ]
            runtime.command(label, command, out, paths + [tool])
            result = out / "commands" / label / "attempt-1/stdout"
            mapping[label] = {
                "path": str(result.relative_to(out)),
                "sha256": sha256(result),
                "format": "paf",
            }
            print(label, "COMPLETE", flush=True)
        if [sha256(p) for p in paths] != hashes or sha256(tool) != source["minimap2_sha256"]:
            raise ValueError("Assembly inputs or tool changed")
        write_json(out / "mapping.json", mapping)
        write_json(
            out / "status.json",
            {"status": "COMPLETE", "mapping_sha256": sha256(out / "mapping.json")},
        )
    except Exception as error:
        write_json(out / "status.json", {"status": "FAILED", "error": str(error)})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--minimap2", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--memory-gib", type=int, choices=(8, 10, 48), default=8)
    args = parser.parse_args()
    align(
        args.panel.resolve(),
        args.left,
        args.right,
        args.minimap2.absolute(),
        args.out.resolve(),
        args.memory_gib,
    )
