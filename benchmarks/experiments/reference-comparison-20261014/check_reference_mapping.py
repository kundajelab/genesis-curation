"""Check converted CIGARs against the independently installed UCSC liftOver."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from genesis_curation.benchmark.reference_mapping import chain_record


def check(tool: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bed = root / "input.bed"
        bed.write_text("q\t10\t15\tx\t0\t+\nq\t37\t42\ty\t0\t+\nq\t32\t42\tz\t0\t+\n")
        for strand in ("+", "-"):
            line = f"q\t100\t10\t42\t{strand}\tt\t200\t100\t133\t30\t35\t60\tcg:Z:5M2I15M3D10M"
            chain = root / "test.chain"
            chain.write_text(chain_record(line, 1, {"q": 100}, {"t": 200}))
            subprocess.run(
                [str(tool), str(bed), str(chain), str(root / "mapped"), str(root / "unmapped")],
                check=True,
                capture_output=True,
            )
            expected = (
                "t\t100\t105\tx\t0\t+\nt\t128\t133\ty\t0\t+\nt\t123\t133\tz\t0\t+\n"
                if strand == "+"
                else "t\t128\t133\tx\t0\t-\nt\t100\t105\ty\t0\t-\n"
            )
            assert (root / "mapped").read_text() == expected
            if strand == "-":
                assert "q\t32\t42\tz" in (root / "unmapped").read_text()
            chain.write_text(chain.read_text() + chain_record(line, 2, {"q": 100}, {"t": 200}))
            subprocess.run(
                [
                    str(tool),
                    "-multiple",
                    "-noSerial",
                    str(bed),
                    str(chain),
                    str(root / "mapped"),
                    str(root / "unmapped"),
                ],
                check=True,
                capture_output=True,
            )
            assert (root / "mapped").read_text().count("\tx\t") == 2
    print(
        "PASS: independent UCSC positive/negative strands, indels, "
        "incomplete mapping and duplicate placement"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--liftover", type=Path, required=True)
    args = parser.parse_args()
    check(args.liftover.resolve())
