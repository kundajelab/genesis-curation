"""Small known-answer tests; run with the benchmark environment's Python."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from motifs import HERE, background, exclusion_index, matrix, peak_windows, score


def main() -> None:
    pwm = np.array([[4, 0, 0], [0, 4, 0], [0, 0, 4], [0, 0, 0]], dtype=np.int64)
    assert score("ACG", pwm) == score("CGT", pwm) == 12
    assert score("AAAAA", pwm) == 4
    try:
        score("ANG", pwm)
    except ValueError:
        pass
    else:
        raise AssertionError("Ambiguous sequence was accepted")
    for name in ("MA0565.1", "MA0931.1"):
        actual, threshold, record = matrix(HERE / "sources" / (name + ".json"))
        assert 0 < record["exact_null_tail_probability"] <= 1e-4
        consensus = "".join("ACGT"[i] for i in actual.argmax(axis=0))
        assert score(consensus, actual) >= threshold
    genome = {"chr1": "ACGT" * 1000}
    rows = [{"chrom": "chr1", "start": 1000, "end": 1200, "sequence": genome["chr1"][1000:1200]}]
    index = exclusion_index(rows)
    control = background(rows[0], genome, index)
    assert control is not None and control == background(rows[0], genome, index)
    sequence, start = control
    assert len(sequence) == 200 and sequence.count("G") + sequence.count("C") == 100
    assert start + 200 <= 1000 or start >= 1200
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "peaks.bed"
        path.write_text(
            "chr1\t900\t1300\ta\t1\t.\t10\t2\t2\t200\nchr1\t0\t20\tedge\t1\t.\t20\t2\t2\t10\n"
        )
        windows, excluded = peak_windows(path, genome)
        assert excluded == 1 and len(windows) == 1
        assert windows[0]["start"] == 1000 and windows[0]["end"] == 1200
    print("PASS: independent PBM thresholds, both strands, GC matching, exclusions and windows")


if __name__ == "__main__":
    main()
