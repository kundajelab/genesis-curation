"""Archive command evidence from the completed, commonly scored ATAC workflows."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
from pathlib import Path

from genesis_curation.benchmark.spec import sha256


def archive(genesis: Path, nfcore: Path, scored: Path, out: Path) -> None:
    score = json.loads((scored / "results.json").read_text())
    for name, expected in score["source_records"].items():
        if sha256(Path(name)) != expected:
            raise ValueError("A source changed after common scoring: " + name)
    out.mkdir(parents=True, exist_ok=False)
    selected = {}
    for label, root in (("genesis", genesis), ("nfcore", nfcore), ("common", scored)):
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            if (
                (path.parent == root and path.suffix in {".json", ".tsv", ".html", ".log"})
                or path.name
                in {
                    ".command.sh",
                    ".command.run",
                    ".command.log",
                    ".command.err",
                    ".command.out",
                    ".command.trace",
                    ".exitcode",
                    ".nextflow.log",
                    "trace.tsv",
                    "enrichment.json",
                    "fragments.json",
                }
                or relative.parts[0] in {"commands", "invocations"}
                or path.suffix == ".narrowPeak"
            ):
                selected[f"{label}/{relative}"] = path
    manifest = {
        name: {"sha256": sha256(path), "bytes": path.stat().st_size}
        for name, path in selected.items()
    }
    packed = out / "evidence.tar.gz"
    with packed.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|") as tar:
            for name, path in selected.items():
                info = tar.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                with path.open("rb") as stream:
                    tar.addfile(info, stream)
    verified = set()
    with tarfile.open(packed, "r:gz") as tar:
        for entry in tar:
            stream = tar.extractfile(entry)
            if stream is None or entry.name not in manifest:
                raise ValueError("Unexpected archive member")
            with stream:
                digest = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    digest.update(block)
            if digest.hexdigest() != manifest[entry.name]["sha256"]:
                raise ValueError("Archive checksum mismatch")
            verified.add(entry.name)
    if verified != set(manifest):
        raise ValueError("Archive omitted evidence")
    (out / "metrics.tsv").write_bytes((scored / "metrics.tsv").read_bytes())
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "script_sha256": sha256(Path(__file__)),
                "archive_sha256": sha256(packed),
                "archive_bytes": packed.stat().st_size,
                "members": manifest,
                "retention": "No source files removed; reads/BAMs/indexes excluded",
            },
            indent=2,
        )
        + "\n"
    )
    print(len(manifest), "verified files;", packed.stat().st_size, "compressed bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--genesis-run", type=Path, required=True)
    parser.add_argument("--nfcore-run", type=Path, required=True)
    parser.add_argument("--scored", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    archive(
        args.genesis_run.resolve(),
        args.nfcore_run.resolve(),
        args.scored.resolve(),
        args.out.resolve(),
    )
