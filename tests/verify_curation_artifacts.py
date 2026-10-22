"""Binary/interval validation and worker boundaries; optional actual bigWig reader tests."""

from __future__ import annotations

import argparse
import copy
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pysam
from verify_curation import altered, fixture

from genesis_curation.contracts.adapters import artifact, signal
from genesis_curation.contracts.records import record
from genesis_curation.contracts.validation import Budget, bundle, fasta


def states(value: dict[str, Any]) -> set[str]:
    return {f["data"]["result"] for f in value["data"]["validations"][0]["data"]["findings"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bigwig-python", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="genesis-curation-artifacts-") as temporary:
        root = Path(temporary)
        manifest = fixture(root)
        data = copy.deepcopy(manifest["data"])
        bam = root / "output/alignments.bam"
        with pysam.AlignmentFile(
            str(bam), "wb", header={"HD": {"SO": "coordinate"}, "SQ": [{"SN": "chr1", "LN": 100}]}
        ) as writer:
            read = pysam.AlignedSegment(writer.header)
            read.query_name = "read"
            read.query_sequence = "AAA"
            read.reference_id = 0
            read.reference_start = 10
            read.cigarstring = "3M"
            writer.write(read)
        entry = artifact(
            root / "output", bam.name, "local", manifest["id"], "bam", "bam", "tiny-v1"
        )
        entry["sorted"] = True
        data["artifacts"].append(entry)
        with_bam = record("manifest", data, manifest["id"])
        assert "ERROR" in states(bundle([with_bam], level="full"))  # Required index missing.
        pysam.index(str(bam))
        assert states(bundle([with_bam], level="full")) == {"PASS"}
        with pysam.AlignmentFile(
            str(bam), "wb", header={"HD": {"SO": "coordinate"}, "SQ": [{"SN": "wrong", "LN": 100}]}
        ):
            pass
        assert "ERROR" in states(bundle([with_bam], level="full"))
        peak = root / "output/peaks.bed"
        narrow = altered(manifest, lambda m: m["artifacts"][0].update(format="narrowPeak"))
        for text in (
            "chr1\t0\t2\tx\t1\t.\tNaN\t1\t1\t0\n",
            "chr1\t0\t2\tx\t1\t.\t1\t1\t1\t4\n",
            "chr1\t-1\t2\tx\t1\t.\t1\t1\t1\t0\n",
        ):
            peak.write_text(text)
            assert "ERROR" in states(bundle([narrow], level="full"))
        peak.write_text("")
        assert states(bundle([narrow], level="full")) == {"PASS"}
        remote = altered(
            manifest,
            lambda m: m.update(
                worker="remote-a", artifacts=[{**a, "worker": "remote-a"} for a in m["artifacts"]]
            ),
        )
        with patch.object(
            Path, "open", side_effect=AssertionError("controller touched worker path")
        ):
            assert "NOT_EVALUATED" in states(bundle([remote], level="full"))
        long = root / "long.fa"
        long.write_text(">unwrapped\n" + "A" * 2_000_000 + "\n")
        assert fasta(long, Budget()) == {"unwrapped": 2_000_000}
        # Declaration checks do not require a binary reader and catch incompatible count units.
        bw = root / "output/cuts.bw"
        bw.write_bytes(b"not a bigWig")
        data = copy.deepcopy(manifest["data"])
        data["artifacts"].append(
            artifact(
                root / "output",
                bw.name,
                "local",
                manifest["id"],
                "cuts_counts",
                "bigwig",
                "tiny-v1",
                signal=signal("cuts_counts"),
            )
        )
        track = record("manifest", data, manifest["id"])

        def track_finding(value: dict[str, Any], rule: str = "artifact.scan") -> dict[str, Any]:
            matches = [
                finding["data"]
                for finding in value["data"]["validations"][0]["data"]["findings"]
                if finding["data"]["rule_id"] == rule
                and finding["data"]["subject"]["id"] == data["artifacts"][-1]["id"]
            ]
            assert len(matches) == 1, matches
            return matches[0]

        assert track_finding(bundle([track], level="full"))["result"] == "NOT_EVALUATED"
        wrong = altered(track, lambda d: d["artifacts"][-1]["signal"].update(unit="fragment"))
        assert track_finding(bundle([wrong], level="full"), "signal.semantics")["result"] == "ERROR"
        if args.bigwig_python:
            corrupt = track_finding(bundle([track], level="full", bigwig_python=args.bigwig_python))
            assert corrupt["result"] == "ERROR", corrupt
            make = """import pyBigWig,sys
with pyBigWig.open(sys.argv[1], 'w') as out:
    out.addHeader([('chr1',100)])
    out.addEntries(['chr1'], [10], ends=[11], values=[float(sys.argv[2])])
"""
            for value, expected, reason in (
                ("1", "PASS", None),
                ("nan", "ERROR", "nonfinite"),
                ("0.5", "ERROR", "Raw counts must be nonnegative integers"),
            ):
                subprocess.run([str(args.bigwig_python), "-c", make, str(bw), value], check=True)
                tested = bundle([track], level="full", bigwig_python=args.bigwig_python)
                finding = track_finding(tested)
                assert finding["result"] == expected, finding
                if reason:
                    assert reason in finding["observed"], finding
                else:
                    assert states(tested) == {"PASS"}, tested
            normalized = altered(
                track,
                lambda d: d["artifacts"][-1].update(role="track_cpm", signal=signal("track_cpm")),
            )
            assert (
                track_finding(bundle([normalized], level="full", bigwig_python=args.bigwig_python))[
                    "result"
                ]
                == "PASS"
            )
            print("PASS: real bigWig readability, finite values and raw-count semantics")
        else:
            print("NOT RUN: binary bigWig checks require explicit --bigwig-python")
    print(
        "PASS: BAM/reference/index, narrowPeak, bounded FASTA, "
        "signal declarations and worker isolation"
    )


if __name__ == "__main__":
    main()
