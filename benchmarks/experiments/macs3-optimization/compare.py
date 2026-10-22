"""Compare two MACS3 builds on identical, checksum-bound peak-caller inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def scientific_outputs(folder: Path) -> dict[str, str | None]:
    outputs = {}
    for suffix in ("_peaks.narrowPeak", "_summits.bed", "_peaks.xls"):
        p = folder / ("result" + suffix)
        if not p.exists():
            outputs[suffix] = None
            continue
        data = p.read_bytes()
        if suffix == "_peaks.xls":
            data = b"\n".join(line for line in data.splitlines() if not line.startswith(b"#"))
        outputs[suffix] = hashlib.sha256(data).hexdigest()
    return outputs


def run(args: argparse.Namespace) -> int:
    from genesis_curation.benchmark.runtime import group_rss

    args.out.mkdir(exist_ok=False, parents=True)
    document = json.loads(args.inputs.read_text())
    checked = {}
    for case in document["cases"]:
        for path, expected in case["inputs"].items():
            if path not in checked:
                checked[path] = digest(Path(path))
            if checked[path] != expected:
                raise ValueError("Input checksum mismatch: " + path)
    cpus = sorted(os.sched_getaffinity(0))
    if len(cpus) < 8:
        raise ValueError("Eight allocated CPUs required for the selected panel")
    deadline = time.monotonic() + 8 * 3600
    launcher = REPO / "src/genesis_curation/macs3_launch.py"
    rows = []
    state = {
        "status": "RUNNING",
        "inputs_sha256": digest(args.inputs),
        "launcher_sha256": digest(launcher),
        "cases": document["cases"],
        "runs": rows,
        "memory_scope": (
            "Process-group RSS sum sampled every 50 ms; shared pages may be counted "
            "multiple times; not unique cgroup peak memory"
        ),
        "cache_policy": (
            "Inputs hashed before warm-up; no system cache flushing; versions alternate"
        ),
        "limits": {
            "cpus": [1, 2, 8],
            "per_call_seconds": 1800,
            "memory_bytes": 16 * 1024**3,
            "campaign_seconds": 8 * 3600,
            "output_bytes": 32 * 1024**3,
        },
    }

    def save() -> None:
        p = args.out / "results.partial"
        p.write_text(json.dumps(state, indent=2) + "\n")
        p.replace(args.out / "results.json")

    save()
    try:
        for case in document["cases"]:
            for budget in (1, 2, 8):
                for repeat in range(4):
                    versions = (
                        ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
                    )
                    for version in versions:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Campaign budget exhausted")
                        folder = args.out / case["id"] / f"{budget}cpu" / f"{version}-{repeat}"
                        folder.mkdir(parents=True)
                        python = getattr(args, version).absolute()
                        env = dict(
                            os.environ,
                            PATH=str(python.parent) + os.pathsep + os.environ["PATH"],
                            TMPDIR=str(folder),
                            PYTHONDONTWRITEBYTECODE="1",
                        )
                        command = [
                            "/usr/bin/time",
                            "-f",
                            "%e %U %S %M %x",
                            "-o",
                            str(folder / "time.txt"),
                            "taskset",
                            "-c",
                            ",".join(map(str, cpus[:budget])),
                            str(python),
                            str(launcher),
                            "--cpus",
                            str(budget),
                            "--",
                            *case["argv"],
                        ]
                        start = time.monotonic()
                        peak = 0
                        with (
                            (folder / "stdout").open("w") as out,
                            (folder / "stderr").open("w") as err,
                        ):
                            child = subprocess.Popen(
                                command,
                                cwd=folder,
                                env=env,
                                stdout=out,
                                stderr=err,
                                start_new_session=True,
                            )
                            try:
                                while child.poll() is None:
                                    peak = max(peak, group_rss(child.pid) or 0)
                                    if (
                                        peak > 16 * 1024**3
                                        or time.monotonic() - start > 1800
                                        or time.monotonic() > deadline
                                    ):
                                        raise TimeoutError(
                                            "Per-call memory/time or campaign budget exhausted"
                                        )
                                    time.sleep(0.05)
                            except BaseException:
                                os.killpg(child.pid, signal.SIGKILL)
                                child.wait()
                                raise
                        measured = (folder / "time.txt").read_text().splitlines()[-1].split()
                        row = {
                            "case": case["id"],
                            "cpus": budget,
                            "version": version,
                            "repeat": repeat,
                            "warmup": repeat == 0,
                            "exit": child.returncode,
                            "wall_seconds": float(measured[0]),
                            "cpu_seconds": float(measured[1]) + float(measured[2]),
                            "sampled_group_rss_bytes": peak,
                            "time_max_process_rss_kib": int(measured[3]),
                            "outputs": scientific_outputs(folder),
                            "runtime": json.loads((folder / "macs3.runtime.json").read_text()),
                            "argv": case["argv"],
                        }
                        rows.append(row)
                        save()
                        print(
                            case["id"],
                            budget,
                            version,
                            repeat,
                            row["wall_seconds"],
                            flush=True,
                        )
                size = sum(p.stat().st_size for p in args.out.rglob("*") if p.is_file())
                if size > 32 * 1024**3:
                    raise ValueError("Output storage budget exhausted")
        comparisons = []
        for case in document["cases"]:
            for budget in (1, 2, 8):
                selected = [r for r in rows if r["case"] == case["id"] and r["cpus"] == budget]
                reference = selected[0]["outputs"]
                equivalent = (
                    all(r["exit"] == 0 and r["outputs"] == reference for r in selected)
                    and reference["_peaks.narrowPeak"] is not None
                )
                groups = {
                    v: [r for r in selected if r["version"] == v and not r["warmup"]]
                    for v in ("baseline", "candidate")
                }
                medians = {
                    v: statistics.median(r["wall_seconds"] for r in rs) for v, rs in groups.items()
                }
                comparisons.append(
                    {
                        "case": case["id"],
                        "cpus": budget,
                        "equivalent": equivalent,
                        "wall_medians_seconds": medians,
                        "speedup": medians["baseline"] / medians["candidate"],
                        "sampled_group_rss_max_bytes": {
                            v: max(r["sampled_group_rss_bytes"] for r in rs)
                            for v, rs in groups.items()
                        },
                    }
                )
        state["comparisons"] = comparisons
        state["status"] = "PASS" if all(x["equivalent"] for x in comparisons) else "FAIL"
    except BaseException as e:
        state["status"] = "INCOMPLETE"
        state["error"] = str(e)
        raise
    finally:
        save()
    return 0 if state["status"] == "PASS" else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("inputs", "baseline", "candidate", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    raise SystemExit(run(p.parse_args()))
