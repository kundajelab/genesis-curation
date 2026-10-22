"""Reconstruct a public Phytozome browser reference with complete chunk evidence.

This uses the publisher's unauthenticated JBrowse sequence track. The result is
a derived FASTA, not a claim to have downloaded the portal's original FASTA file.
Every declared contig and chunk is required; missing sequence is never padded.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import gzip
import hashlib
import json
import re
import threading
import time
import urllib.request
import zlib
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.spec import sha256, write_json


def get(url: str, limit: int) -> bytes:
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "Genesis-benchmark/1"}),
        timeout=30,
    ) as response:
        result = response.read(limit + 1)
        if response.headers.get("Content-Encoding") == "gzip":
            result = gzip.decompress(result)
    if len(result) > limit:
        raise ValueError("Browser response exceeded its size budget")
    return result


def fetch(dataset: str, expected_bases: int, expected_contigs: int, out: Path) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_]+", dataset):
        raise ValueError("Invalid browser dataset")
    base = "https://phytozome-next.jgi.doe.gov/jbrowse/genomes/" + dataset
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = get(base + "/seq/refSeqs.json", 1_000_000)
        refs = json.loads(metadata)
        if len(refs) != expected_contigs or sum(r["length"] for r in refs) != expected_bases:
            raise ValueError("Browser assembly differs from the selected publisher metadata")
        recorded = out / "refSeqs.json"
        if recorded.exists() and recorded.read_bytes() != metadata:
            raise ValueError("Browser reference changed during acquisition")
        recorded.write_bytes(metadata)
        track = get(base + "/trackList.json", 1_000_000)
        track_path = out / "trackList.json"
        if track_path.exists() and track_path.read_bytes() != track:
            raise ValueError("Browser track changed during acquisition")
        track_path.write_bytes(track)
        dna = [t for t in json.loads(track)["tracks"] if t["label"] == "DNA"]
        if (
            len(dna) != 1
            or dna[0]["urlTemplate"] != "seq/{refseq_dirpath}/{refseq}-"
            or dna[0]["compress"] != 1
        ):
            raise ValueError("Unsupported public sequence track")
        chunks = out / "chunks"
        chunks.mkdir(exist_ok=True)
        journal = out / "chunks.jsonl"
        previous = (
            {r["name"]: r for r in map(json.loads, journal.read_text().splitlines())}
            if journal.exists()
            else {}
        )
        jobs = []
        for ref in refs:
            name = ref["name"]
            if (
                not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
                or ref["start"] != 0
                or ref["end"] != ref["length"]
            ):
                raise ValueError("Invalid reference sequence metadata")
            width = ref["seqChunkSize"]
            if not 0 < width <= 100_000:
                raise ValueError("Unsupported chunk length")
            checksum = f"{zlib.crc32(name.encode()):08x}"
            directory = "/".join(checksum[i : i + 3] for i in range(0, 8, 3))
            for start in range(0, ref["length"], width):
                index = start // width
                label = f"{name}-{index}"
                jobs.append(
                    (
                        label,
                        base + f"/seq/{directory}/{label}.txtz",
                        min(width, ref["length"] - start),
                    )
                )
        mutex = threading.Lock()
        counter_path = out / "transfer.json"
        counter = (
            json.loads(counter_path.read_text())
            if counter_path.exists()
            else {"downloaded_bytes": 0, "budget_bytes": 2_500_000_000}
        )

        def chunk(job: tuple[str, str, int]) -> dict[str, Any]:
            label, url, length = job
            path = chunks / label
            if label in previous:
                if (
                    not path.is_file()
                    or sha256(path) != previous[label]["sha256"]
                    or path.stat().st_size != length
                ):
                    raise ValueError("Recorded sequence chunk changed")
                return previous[label]
            if path.is_symlink():
                raise ValueError("Refuse symlink chunk")
            for attempt in range(3):
                try:
                    data = get(url, 128_000)
                    with mutex:
                        counter["downloaded_bytes"] += len(data)
                        write_json(counter_path, counter)
                        if counter["downloaded_bytes"] > counter["budget_bytes"]:
                            raise ValueError("Browser download budget exceeded")
                    if len(data) != length or set(data.upper()) - set(b"ACGTRYSWKMBDHVN"):
                        raise ValueError("Missing, invalid or truncated browser sequence chunk")
                    part = path.with_suffix(".partial")
                    part.write_bytes(data)
                    part.replace(path)
                    row = {
                        "name": label,
                        "url": url,
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    with mutex, journal.open("a") as stream:
                        stream.write(json.dumps(row, sort_keys=True) + "\n")
                    time.sleep(0.5)  # At most four bounded requests at a time.
                    return row
                except OSError, TimeoutError:
                    if attempt == 2:
                        raise
                    time.sleep(5)
            raise RuntimeError("Unreachable retry state")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            for index, _ in enumerate(executor.map(chunk, jobs), 1):
                if index % 500 == 0:
                    print(dataset, index, "/", len(jobs), "verified chunks", flush=True)
        target = out / (dataset + ".fa.gz")
        partial = target.with_suffix(".partial")
        with (
            partial.open("wb") as raw,
            gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as stream,
        ):
            for ref in refs:
                stream.write((">" + ref["name"] + "\n").encode())
                for start in range(0, ref["length"], ref["seqChunkSize"]):
                    data = (chunks / f"{ref['name']}-{start // ref['seqChunkSize']}").read_bytes()
                    for offset in range(0, len(data), 80):
                        stream.write(data[offset : offset + 80] + b"\n")
        partial.replace(target)
        write_json(
            out / "receipt.json",
            {
                "schema_version": 1,
                "dataset": dataset,
                "source": base,
                "bases": expected_bases,
                "contigs": expected_contigs,
                "chunks": len(jobs),
                "fasta": {
                    "name": target.name,
                    "sha256": sha256(target),
                    "bytes": target.stat().st_size,
                },
                "refseqs_sha256": sha256(recorded),
                "track_sha256": sha256(track_path),
                "chunk_manifest_sha256": sha256(journal),
                "integrity": (
                    "Full public browser sequence, all declared lengths checked; "
                    "locally measured hashes, no original-FASTA publisher digest"
                ),
            },
        )
        print(target, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    parser.add_argument("--bases", type=int, required=True)
    parser.add_argument("--contigs", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    fetch(args.dataset, args.bases, args.contigs, args.out)
