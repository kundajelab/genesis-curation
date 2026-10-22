"""Convert CIGAR-bearing assembly PAF alignments into coordinate-checked chains.

The chain target is the input/query genome, as required by UCSC liftOver. All
reported alignments are retained, including secondary mappings, so ambiguity
can be counted instead of silently selecting one location.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .spec import sha256


def validate_chain(
    path: Path, source_sizes: dict[str, int], target_sizes: dict[str, int]
) -> dict[str, Any]:
    """Validate every UCSC header, alignment block and terminal coordinate."""
    original = sha256(path)
    identifiers: set[int] = set()
    active = False
    left = right = left_end = right_end = aligned = blocks = 0
    with path.open() as stream:
        for number, line in enumerate(stream, 1):
            fields = line.split()
            if not fields or fields[0].startswith("#"):
                continue
            if fields[0] == "chain":
                if active or len(fields) != 13:
                    raise ValueError(f"Malformed or unterminated chain at line {number}")
                score, length1, start1, end1, length2, start2, end2, identifier = (
                    int(fields[i]) for i in (1, 3, 5, 6, 8, 10, 11, 12)
                )
                if (
                    score < 0
                    or identifier < 0
                    or identifier in identifiers
                    or source_sizes.get(fields[2]) != length1
                    or target_sizes.get(fields[7]) != length2
                    or fields[4] != "+"
                    or fields[9] not in {"+", "-"}
                    or not 0 <= start1 < end1 <= length1
                    or not 0 <= start2 < end2 <= length2
                ):
                    raise ValueError(
                        f"Chain header disagrees with reference coordinates at line {number}"
                    )
                identifiers.add(identifier)
                left, right, left_end, right_end = start1, start2, end1, end2
                active = True
            else:
                if not active or len(fields) not in {1, 3}:
                    raise ValueError(f"Unexpected chain block at line {number}")
                size = int(fields[0])
                if size <= 0:
                    raise ValueError("Chain blocks must have positive aligned length")
                left += size
                right += size
                aligned += size
                blocks += 1
                if len(fields) == 3:
                    gap1, gap2 = int(fields[1]), int(fields[2])
                    if gap1 < 0 or gap2 < 0:
                        raise ValueError("Chain gaps must be nonnegative")
                    left += gap1
                    right += gap2
                    if left >= left_end or right >= right_end:
                        raise ValueError("Chain block exceeds its declared span")
                else:
                    if (left, right) != (left_end, right_end):
                        raise ValueError("Chain terminal coordinates disagree with its blocks")
                    active = False
    if active or not identifiers or sha256(path) != original:
        raise ValueError("Empty, unterminated or changing chain file")
    return {
        "chain_sha256": original,
        "chains": len(identifiers),
        "blocks": blocks,
        "aligned_bases_with_multiplicity": aligned,
    }


def rename_chain(
    source: Path,
    target: Path,
    source_aliases: dict[str, str],
    target_aliases: dict[str, str],
    source_sizes: dict[str, int],
    target_sizes: dict[str, int],
) -> dict[str, Any]:
    """Apply explicitly declared contig aliases, then check all lengths and blocks."""
    original = sha256(source)
    seen: list[dict[str, str]] = [{}, {}]
    with source.open() as stream, target.open("x") as output:
        for line in stream:
            if line.startswith("chain "):
                fields = line.split()
                if len(fields) != 13:
                    raise ValueError("Malformed chain header")
                for index, column, aliases in ((0, 2, source_aliases), (1, 7, target_aliases)):
                    name = fields[column]
                    renamed = aliases.get(name, name)
                    if renamed in seen[index] and seen[index][renamed] != name:
                        raise ValueError("Contig aliases must not merge distinct sequences")
                    seen[index][renamed] = name
                    fields[column] = renamed
                line = " ".join(fields) + "\n"
            output.write(line)
    validation = validate_chain(target, source_sizes, target_sizes)
    if sha256(source) != original:
        raise ValueError("Original chain changed")
    return {
        "schema_version": 1,
        "original_sha256": original,
        "source_aliases": source_aliases,
        "target_aliases": target_aliases,
        **validation,
    }


def chain_record(
    line: str, number: int, source_sizes: dict[str, int], target_sizes: dict[str, int]
) -> str:
    fields = line.rstrip().split("\t")
    if len(fields) < 12:
        raise ValueError("PAF needs twelve columns and a CIGAR tag")
    query, target, strand = fields[0], fields[5], fields[4]
    qlen, qs, qe = map(int, fields[1:4])
    tlen, ts, te = map(int, fields[6:9])
    if (
        strand not in {"+", "-"}
        or source_sizes.get(query) != qlen
        or target_sizes.get(target) != tlen
    ):
        raise ValueError("PAF sequence dictionaries or strand differ from the declared references")
    if not 0 <= qs < qe <= qlen or not 0 <= ts < te <= tlen:
        raise ValueError("PAF alignment is outside its reference")
    tags = [value[5:] for value in fields[12:] if value.startswith("cg:Z:")]
    if len(tags) != 1:
        raise ValueError("One CIGAR tag is required")
    cigar = tags[0]
    operations = re.findall(r"([1-9][0-9]*)([MID=X])", cigar)
    if "".join(n + op for n, op in operations) != cigar:
        raise ValueError("Unsupported or malformed assembly CIGAR")
    q, t = (qs if strand == "+" else qlen - qe), ts
    blocks = []
    for raw, op in operations:
        size = int(raw)
        if op in {"M", "=", "X"}:
            if strand == "+":
                blocks.append((q, q + size, t, t + size))
            else:
                blocks.append((qlen - q - size, qlen - q, tlen - t - size, tlen - t))
        if op != "D":
            q += size
        if op != "I":
            t += size
    if q != (qe if strand == "+" else qlen - qs) or t != te or not blocks:
        raise ValueError("CIGAR does not consume the declared alignment spans")
    if strand == "-":
        blocks.reverse()
    first, last = blocks[0], blocks[-1]
    score = int(fields[9])
    if score < 0:
        raise ValueError("Negative PAF match count")
    result = [
        f"chain {score} {query} {qlen} + {first[0]} {last[1]} "
        f"{target} {tlen} {strand} {first[2]} {last[3]} {number}\n"
    ]
    for index, (start, end, dest_start, dest_end) in enumerate(blocks):
        if index + 1 == len(blocks):
            result.append(f"{end - start}\n\n")
        else:
            following = blocks[index + 1]
            dt, dq = following[0] - end, following[2] - dest_end
            if dt < 0 or dq < 0 or end - start != dest_end - dest_start:
                raise ValueError("Nonmonotone alignment blocks")
            result.append(f"{end - start}\t{dt}\t{dq}\n")
    return "".join(result)


def paf_to_chain(
    paf: Path, chain: Path, source_sizes: dict[str, int], target_sizes: dict[str, int]
) -> dict[str, Any]:
    if chain.exists():
        raise ValueError("Choose a new chain output")
    chain.parent.mkdir(parents=True, exist_ok=True)
    original = sha256(paf)
    count = 0
    with paf.open() as source, chain.open("x") as target:
        for line in source:
            if line.strip():
                count += 1
                target.write(chain_record(line, count, source_sizes, target_sizes))
    if not count or sha256(paf) != original:
        raise ValueError("Empty or changing assembly alignment")
    return {
        "schema_version": 1,
        "paf_sha256": original,
        "chain_sha256": sha256(chain),
        "alignments": count,
        "filter": "all CIGAR-bearing reported alignments; secondary mappings retained",
    }
