"""Standalone reader for an explicitly configured track environment (Python >=3.12).

This file intentionally does not import Genesis or its Python 3.14 dependencies.
Only local paths are accepted. It never installs a reader or contacts a service.
"""

from __future__ import annotations

import importlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any


def inspect(request: dict[str, Any]) -> dict[str, Any]:
    pyBigWig = importlib.import_module("pyBigWig")

    path = Path(request["path"])
    if not path.is_absolute() or not path.is_file():
        raise ValueError("An existing absolute local bigWig path is required")
    start = time.monotonic()
    count = 0
    with pyBigWig.open(str(path)) as reader:
        if not reader.isBigWig():
            raise ValueError("Artifact is not a bigWig")
        contigs = reader.chroms()
        if not contigs or any(request["contigs"].get(c) != n for c, n in contigs.items()):
            raise ValueError("bigWig contigs differ from the declared reference")
        for chrom, length in contigs.items():
            previous_end = 0
            for left in range(0, length, 65536):
                if time.monotonic() - start > request["seconds"]:
                    return {"complete": False, "records": count, "reason": "time budget"}
                for a, b, value in reader.intervals(chrom, left, min(length, left + 65536)) or ():
                    if a < left:  # An interval crossing a window boundary was already checked.
                        continue
                    count += 1
                    if request["records"] and count > request["records"]:
                        return {"complete": False, "records": count - 1, "reason": "record budget"}
                    if not (0 <= a < b <= length) or a < previous_end or not math.isfinite(value):
                        raise ValueError("Invalid/overlapping interval or nonfinite bigWig value")
                    if request["raw"] and (value < 0 or not float(value).is_integer()):
                        raise ValueError("Raw counts must be nonnegative integers")
                    previous_end = b
    return {"complete": True, "records": count, "reader": pyBigWig.__version__}


if __name__ == "__main__":
    try:
        print(json.dumps(inspect(json.load(sys.stdin)), allow_nan=False))
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        print(json.dumps({"error": str(error), "unavailable": isinstance(error, ImportError)}))
        raise SystemExit(2) from error
