"""CPU allocation and provenance checks without installing MACS3."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from genesis_curation.scatac import products

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "launcher", ROOT / "src/genesis_curation/macs3_launch.py"
)
assert SPEC and SPEC.loader
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def launch(
    receipt: Path, arguments: list[str], *, budget: bool, affinity: bool, revision: str | None
) -> tuple[int, list[str] | None, dict[str, str]]:
    """Run main() with a simulated host; return its status, exec argv and environment."""
    real_hasattr = hasattr

    def host_hasattr(value: object, name: str) -> bool:
        return affinity if name == "sched_getaffinity" else real_hasattr(value, name)

    with (
        patch.dict(os.environ, clear=False),
        patch.object(launcher, "hasattr", host_hasattr, create=True),
        patch.object(launcher, "thread_budget", return_value=budget),
        patch.object(launcher, "identity", return_value={"source_revision": revision}),
        patch.object(launcher, "constrain", return_value=[3, 7]),
        patch.object(launcher, "executable", return_value="/env/bin/macs3"),
        patch.object(launcher.os, "execvpe") as execute,
        patch.object(
            launcher.sys,
            "argv",
            ["launcher", "--cpus", "2", "--receipt", str(receipt), "--", *arguments],
        ),
    ):
        status = launcher.main()
        argv = execute.call_args.args[1] if execute.called else None
        return status, argv, dict(os.environ)


def check_thread_budget() -> None:
    """A budget-aware MACS3 is bounded by MACS3_NUM_THREADS, with or without CPU affinity."""
    assert launcher.explicit_threads(["callpeak", "--threads", "3"]) == 3
    assert launcher.explicit_threads(["callpeak", "--threads=1"]) == 1
    assert launcher.explicit_threads(["callpeak", "-f", "BAM"]) is None
    # -m is callvar's legacy budget spelling, but callpeak's --mfold.
    assert launcher.explicit_threads(["callvar", "-m", "4"]) == 4
    assert launcher.explicit_threads(["callvar", "--multiple-processing=3"]) == 3
    assert launcher.explicit_threads(["callpeak", "-m", "5", "50"]) is None
    try:
        launcher.explicit_threads(["callpeak", "--threads", "two"])
    except ValueError:
        pass
    else:
        raise AssertionError("Non-numeric --threads accepted")
    # MACS3 accepts abbreviations and the short -m4 form; these must be checked too.
    assert launcher.explicit_threads(["pileup", "--thr", "3"]) == 3
    assert launcher.explicit_threads(["callpeak", "--thread=2"]) == 2
    assert launcher.explicit_threads(["callvar", "-m4"]) == 4
    assert launcher.explicit_threads(["callvar", "--multiple=5"]) == 5
    # MACS3 uses the last of repeated budgets, and callvar refuses conflicting spellings, so
    # validating only the first would let a later override exceed the allocation.
    for repeated in (
        ["callpeak", "--threads", "2", "--threads", "64"],
        ["callpeak", "--threads=2", "--thr", "64"],
        ["callvar", "--threads", "2", "-m", "64"],
        ["callvar", "-m", "2", "--multiple-processing=2"],
    ):
        try:
            launcher.explicit_threads(repeated)
        except ValueError as error:
            assert "only once" in str(error), repeated
        else:
            raise AssertionError(f"Repeated CPU budget accepted: {repeated}")
    arguments = ["callpeak", "-f", "BAMPE"]
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        # Without affinity (e.g. macOS), the budget alone bounds the source build.
        status, argv, env = launch(
            folder / "macos.json", arguments, budget=True, affinity=False, revision="1" * 40
        )
        assert status == 0 and argv == ["/env/bin/macs3", *arguments]  # Arguments unchanged.
        assert env["MACS3_NUM_THREADS"] == "2"
        assert all(env[k] == "1" for k in launcher.THREAD_VARIABLES)
        receipt = json.loads((folder / "macos.json").read_text())
        assert receipt["affinity"] == [] and receipt["macs3_thread_budget"] == 2
        assert receipt["resource_enforcement"] == "PASS"
        # With affinity, both bounds apply.
        status, _, env = launch(
            folder / "linux.json", arguments, budget=True, affinity=True, revision="1" * 40
        )
        receipt = json.loads((folder / "linux.json").read_text())
        assert status == 0 and env["MACS3_NUM_THREADS"] == "2"
        assert receipt["affinity"] == [3, 7] and receipt["macs3_thread_budget"] == 2
        # An explicit --threads may not exceed the allocation.
        status, argv, _ = launch(
            folder / "over.json",
            [*arguments, "--threads", "4"],
            budget=True,
            affinity=True,
            revision="1" * 40,
        )
        assert status == 2 and argv is None and not (folder / "over.json").exists()
        # ...including through a later override of an in-budget value.
        status, argv, _ = launch(
            folder / "override.json",
            [*arguments, "--threads", "1", "--threads", "4"],
            budget=True,
            affinity=True,
            revision="1" * 40,
        )
        assert status == 2 and argv is None and not (folder / "override.json").exists()
        # A source build without a budget is still refused without affinity...
        status, argv, _ = launch(
            folder / "refused.json", arguments, budget=False, affinity=False, revision="1" * 40
        )
        assert status == 2 and argv is None
        # ...while a released package keeps running, recorded as unassessed.
        status, _, env = launch(
            folder / "release.json", arguments, budget=False, affinity=False, revision=None
        )
        receipt = json.loads((folder / "release.json").read_text())
        assert status == 0 and "MACS3_NUM_THREADS" not in env
        assert receipt["macs3_thread_budget"] is None
        assert receipt["resource_enforcement"] == "NOT_ASSESSED"


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "macs3"
        script.write_text("fixture")
        with patch.object(launcher.sysconfig, "get_path", return_value=directory):
            assert launcher.executable() == str(script)
            script.unlink()
            try:
                launcher.executable()
            except ValueError:
                pass
            else:
                raise AssertionError("Missing environment-local executable accepted")
    if not hasattr(os, "sched_getaffinity"):
        # Without Linux CPU affinity (e.g. macOS), bounded execution must be refused.
        try:
            launcher.constrain(2)
        except ValueError as error:
            assert "requires Linux CPU affinity" in str(error)
        else:
            raise AssertionError("Bounded execution accepted without CPU affinity")
    before = os.environ.copy()
    # create=True simulates Linux affinity on hosts without it, so the selection logic is
    # checked everywhere.
    with (
        patch.object(os, "sched_getaffinity", return_value={3, 7, 11}, create=True),
        patch.object(os, "getpid", return_value=0),
        patch.object(os, "sched_setaffinity", create=True) as set_affinity,
    ):
        assert launcher.constrain(2) == [3, 7]
        set_affinity.assert_called_once_with(0, [3, 7])
        assert all(os.environ[k] == "1" for k in launcher.THREAD_VARIABLES)
        assert launcher.constrain(8) == [3, 7, 11]
        with patch.object(os, "getpid", return_value=1):
            assert launcher.constrain(2) == [7, 11]
        try:
            launcher.constrain(0)
        except ValueError:
            pass
        else:
            raise AssertionError("Zero CPUs accepted")
    os.environ.clear()
    os.environ.update(before)
    with tempfile.TemporaryDirectory() as directory:
        receipt = Path(directory) / "run.json"
        # Simulate a Linux host and a MACS3 without a CPU budget: affinity alone bounds it.
        with (
            patch.object(
                launcher,
                "hasattr",
                lambda value, name: name == "sched_getaffinity" or hasattr(value, name),
                create=True,
            ),
            patch.object(launcher, "thread_budget", return_value=False),
            patch.object(
                launcher,
                "identity",
                return_value={"source_revision": launcher.SOURCE_REVISION},
            ),
            patch.object(launcher, "constrain", return_value=[3, 7]),
            patch.object(launcher, "executable", return_value="/env/bin/macs3"),
            patch.object(launcher.os, "execvpe") as execute,
            patch.object(
                launcher.sys,
                "argv",
                [
                    "launcher",
                    "--cpus",
                    "2",
                    "--receipt",
                    str(receipt),
                    "--",
                    "callpeak",
                    "-f",
                    "BEDPE",
                    "--keep-dup",
                    "all",
                ],
            ),
        ):
            assert launcher.main() == 0
            assert execute.call_args.args[1] == [
                "/env/bin/macs3",
                "callpeak",
                "-f",
                "BEDPE",
                "--keep-dup",
                "all",
            ]
            assert json.loads(receipt.read_text())["affinity"] == [3, 7]
            assert launcher.main() == 2  # Existing provenance cannot be overwritten.
    with tempfile.TemporaryDirectory() as directory:
        receipt = Path(directory) / "wrong-source.json"
        with (
            patch.object(launcher, "identity", return_value={"source_revision": "0" * 40}),
            patch.object(launcher, "executable", return_value="/env/bin/macs3"),
            patch.object(launcher.os, "execvpe") as execute,
            patch.object(
                launcher.sys,
                "argv",
                [
                    "launcher",
                    "--cpus",
                    "2",
                    "--receipt",
                    str(receipt),
                    "--require-source",
                    launcher.SOURCE_REVISION,
                    "--",
                    "--version",
                ],
            ),
        ):
            assert launcher.main() == 2
            execute.assert_not_called()
            assert not receipt.exists()
    check_thread_budget()
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        scientific = ["macs3", "callpeak", "-f", "BEDPE", "--keep-dup", "all"]
        with patch.object(
            products.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
        ) as run:
            products.command(products.IMAGES["peaks"], scientific, folder, "peaks")
            argv = run.call_args.args[0]
            # scATAC calls MACS3 directly, with --threads equal to the container's CPU quota.
            assert argv[-len(scientific) - 2 :] == [*scientific, "--threads", "2"]
            assert argv[argv.index("--cpus") + 1] == "2"
            assert argv[argv.index("--memory") + 1] == "8g"
            assert argv[argv.index("--network") + 1] == "none"
            assert not any("genesis-macs3" in arg for arg in argv)
            products.command(products.IMAGES["peaks"], ["macs3", "--version"], folder, "version")
            assert "--threads" not in run.call_args.args[0]
    print(
        "PASS MACS3 launcher: restricted affinity, MACS3 thread budget, inherited limits, "
        "thread caps, unchanged arguments and exclusive provenance"
    )


if __name__ == "__main__":
    main()
