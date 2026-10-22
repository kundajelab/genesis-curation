"""Stream paired templates and spill coordinate counts to disk."""

from __future__ import annotations

import collections
import itertools
import json
import sqlite3
from pathlib import Path
from typing import Any

import pysam


def select(bam: Path, policy: dict[str, Any], out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    mito = set(policy["reference"]["mitochondrial_contigs"])
    plastid = set(policy["reference"]["plastid_contigs"])
    names, temporary = out / "names.bam", out / "unsorted.bam"
    pysam.sort("-n", "-m", "128M", "-o", str(names), str(bam))
    counts: collections.Counter[str] = collections.Counter()
    lengths: collections.Counter[int] = collections.Counter()
    mapqs: collections.Counter[int] = collections.Counter()
    db_path = out / "coordinates.sqlite"
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE positions (chrom TEXT,start INT,end INT,n INT,PRIMARY KEY(chrom,start,end))"
    )
    with (
        pysam.AlignmentFile(str(names), "rb") as source,
        pysam.AlignmentFile(str(temporary), "wb", template=source) as output,
        (out / "fragments.unsorted.bed").open("w") as bed,
        (out / "cuts.unsorted.bed").open("w") as cuts,
    ):
        sizes = dict(zip(source.references, source.lengths, strict=True))
        if (mito | plastid) - sizes.keys():
            raise ValueError("Declared organellar contigs are absent from the alignment reference")
        for name, group in itertools.groupby(source, key=lambda r: r.query_name):
            counts["total_templates"] += 1
            mates = []
            for read in group:
                if read.is_secondary or read.is_supplementary:
                    counts["secondary_or_supplementary_records"] += 1
                elif len(mates) < 3:
                    mates.append(read)
            if (
                len(mates) != 2
                or sum(r.is_read1 for r in mates) != 1
                or sum(r.is_read2 for r in mates) != 1
            ):
                counts["invalid_pair"] += 1
                continue
            if any(r.is_unmapped for r in mates) or mates[0].reference_id != mates[1].reference_id:
                counts["unmapped_or_discordant"] += 1
                continue
            counts["mapped_pairs"] += 1
            chrom = mates[0].reference_name
            for read in mates:
                mapqs[read.mapping_quality] += 1
            category = (
                "mitochondrial" if chrom in mito else "plastid" if chrom in plastid else "nuclear"
            )
            counts[f"raw_{category}_pairs"] += 1
            if any(not r.is_proper_pair or r.is_qcfail for r in mates):
                counts["improper_or_qcfail"] += 1
                continue
            if any(r.mapping_quality == 255 or r.mapping_quality < policy["mapq"] for r in mates):
                counts["low_or_unknown_mapq_pairs"] += 1
                continue
            if category != "nuclear":
                counts[f"excluded_{category}_pairs"] += 1
                continue
            start = min(r.reference_start for r in mates)
            end = max(r.reference_end for r in mates)
            counts["eligible_before_dedup"] += 1
            db.execute(
                "INSERT INTO positions VALUES(?,?,?,1) ON CONFLICT(chrom,start,end) "
                "DO UPDATE SET n=n+1",
                (chrom, start, end),
            )
            duplicate = any(r.is_duplicate for r in mates)
            counts["duplicate_pairs"] += duplicate
            if duplicate and policy["duplicates"] == "exclude":
                continue
            if end - start < 10:
                counts["invalid_shifted_span"] += 1
                continue
            counts["usable_fragments"] += 1
            lengths[end - start] += 1
            for read in mates:
                output.write(read)
            bed.write(f"{chrom}\t{start}\t{end}\t{name}\n")
            for pos in (start + 4, end - 6):
                cuts.write(f"{chrom}\t{pos}\t{pos + 1}\n")
            if counts["total_templates"] % 10000 == 0:
                db.commit()
        (out / "chrom.sizes").write_text("".join(f"{c}\t{n}\n" for c, n in sizes.items()))
    db.commit()
    distinct, singleton, doubleton = db.execute(
        "SELECT COUNT(*),COALESCE(SUM(n=1),0),COALESCE(SUM(n=2),0) FROM positions"
    ).fetchone()
    db.close()
    db_path.unlink()
    pysam.sort("-m", "128M", "-o", str(out / "usable.bam"), str(temporary))
    pysam.index(str(out / "usable.bam"))
    names.unlink()
    temporary.unlink()
    denominator = counts["eligible_before_dedup"]
    metrics = {
        "counts": dict(counts),
        "mapq_read_distribution": dict(sorted(mapqs.items())),
        "fragment_lengths": dict(sorted(lengths.items())),
        "NRF": distinct / denominator if denominator else None,
        "PBC1": singleton / distinct if distinct else None,
        "PBC2": singleton / doubleton if doubleton else None,
        "duplicate_fraction": counts["duplicate_pairs"] / denominator if denominator else None,
        "duplicate_denominator": "nuclear proper pairs passing MAPQ, before duplicate exclusion",
        "organellar_denominator": "all primary mapped same-contig pairs, before MAPQ filtering",
        "policy": {k: policy[k] for k in ("mapq", "duplicates")},
        "plant_thresholds": "UNSPECIFIED",
        "metric_version": 1,
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    return metrics
