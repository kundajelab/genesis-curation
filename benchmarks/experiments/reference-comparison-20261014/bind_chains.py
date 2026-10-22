"""Bind published chains or identical contig sequences to audited reference bundles."""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.reference_mapping import rename_chain, validate_chain
from genesis_curation.benchmark.spec import sha256, write_json


def bind(
    left: Path, right: Path, sources: Path | None, out: Path, chains: Path | None = None
) -> None:
    bundles = [json.loads(p.read_text()) for p in (left, right)]
    if any(b["schema_version"] != 1 or b["scan"]["scan"] != "FULL" for b in bundles):
        raise ValueError("Full versioned reference audits are required")
    if bundles[0]["reference"]["species"] != bundles[1]["reference"]["species"]:
        raise ValueError("References must have the same species")
    assets = [b["assets"][b["reference"]["fasta"]] for b in bundles]
    if any(sha256(Path(a["path"])) != a["sha256"] for a in assets):
        raise ValueError("A reference changed after its audit")
    sizes = [{n: c["length"] for n, c in b["scan"]["contigs"].items()} for b in bundles]
    out.mkdir(parents=True, exist_ok=False)
    mapping: dict[str, Any] = {
        "schema_version": 1,
        "left_fasta_sha256": assets[0]["sha256"],
        "right_fasta_sha256": assets[1]["sha256"],
        "reference_audit_sha256": [sha256(p) for p in (left, right)],
        "script_sha256": sha256(Path(__file__)),
    }
    if sources is not None:
        declared = json.loads(sources.read_text())
        if declared["schema_version"] != 1:
            raise ValueError("Unsupported chain source schema")
        mapping["source"] = declared["source"]
        mapping["source_manifest_sha256"] = sha256(sources)
        for index, direction in enumerate(("forward", "reverse")):
            entry = declared[direction]
            original = ((chains or sources.parent) / entry["path"]).resolve()
            if sha256(original) != entry["sha256"]:
                raise ValueError("Published chain checksum changed")
            receipt = rename_chain(
                original,
                out / (direction + ".chain"),
                entry.get("source_aliases", {}),
                entry.get("target_aliases", {}),
                sizes[index],
                sizes[1 - index],
            )
            receipt["source_url"] = entry["url"]
            write_json(out / (direction + ".validation.json"), receipt)
    else:
        if (
            bundles[0]["scan"]["sequence_multiset_sha256"]
            != bundles[1]["scan"]["sequence_multiset_sha256"]
        ):
            raise ValueError("Exact-contig mapping requires identical sequence multisets")
        grouped = []
        for b in bundles:
            groups: dict[tuple[int, str], list[str]] = collections.defaultdict(list)
            for name, contig in b["scan"]["contigs"].items():
                groups[contig["length"], contig["sequence_sha256"]].append(name)
            grouped.append(groups)
        pairs = [
            (names[0], grouped[1][key][0], key[0])
            for key, names in sorted(grouped[0].items())
            if len(names) == len(grouped[1][key]) == 1
        ]
        if not pairs:
            raise ValueError("No uniquely matching complete contigs")
        mapping["source"] = (
            "Full-contig identity: equal length and case-insensitive sequence SHA256"
        )
        mapping["identity_mapping"] = {
            "matched_contigs": len(pairs),
            "matched_bases": sum(n for _, _, n in pairs),
            "excluded_nonunique_contigs": [
                sorted(name for names in groups.values() if len(names) != 1 for name in names)
                for groups in grouped
            ],
            "pairs": [{"left": a, "right": b, "length": n} for a, b, n in pairs],
        }
        for index, direction in enumerate(("forward", "reverse")):
            path = out / (direction + ".chain")
            with path.open("x") as stream:
                for identifier, (a, b, length) in enumerate(pairs, 1):
                    if index:
                        a, b = b, a
                    stream.write(
                        f"chain {length} {a} {length} + 0 {length} "
                        f"{b} {length} + 0 {length} {identifier}\n{length}\n\n"
                    )
            write_json(
                out / (direction + ".validation.json"),
                validate_chain(path, sizes[index], sizes[1 - index]),
            )
    for direction in ("forward", "reverse"):
        path = out / (direction + ".chain")
        mapping[direction] = {
            "path": path.name,
            "sha256": sha256(path),
            "format": "chain",
        }
    write_json(out / "mapping.json", mapping)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left-reference", type=Path, required=True)
    parser.add_argument("--right-reference", type=Path, required=True)
    parser.add_argument(
        "--sources",
        type=Path,
        help="Published chains, hashes and explicit contig aliases",
    )
    parser.add_argument("--chains", type=Path, help="Directory containing the declared chain files")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    bind(
        args.left_reference.resolve(),
        args.right_reference.resolve(),
        args.sources.resolve() if args.sources else None,
        args.out.resolve(),
        args.chains,
    )
