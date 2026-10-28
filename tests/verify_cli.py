"""Check installation, public launchers and process boundaries without scientific tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from genesis_curation.execution import launch
from genesis_curation.execution.install import install_cli

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / ".venv/bin/genesis"
# The launchers drive a genesis-preprocessing checkout; this test needs one (GENESIS_REPO).
PIPELINE = Path(os.environ.get("GENESIS_REPO", "")).expanduser().resolve()


def run(
    argv: list[str], cwd: Path, env: dict[str, str], code: int = 0
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=45)
    assert result.returncode == code, (argv, result.returncode, result.stdout, result.stderr)
    return result


def script(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o755)


def check_installation(work: Path) -> None:
    link = install_cli(ROOT, work / "user bin")
    assert link.is_symlink() and link.resolve() == CLI
    assert install_cli(ROOT, link.parent) == link
    run([str(link), "--help"], work, dict(os.environ))
    link.unlink()
    for kind in ("file", "directory", "other-link", "broken-link"):
        if kind == "file":
            link.write_text("user's executable\n")
        elif kind == "directory":
            link.mkdir()
        else:
            link.symlink_to(sys.executable if kind == "other-link" else work / "missing")
        try:
            install_cli(ROOT, link.parent)
        except ValueError as error:
            assert "Refusing to overwrite" in str(error)
        else:
            raise AssertionError(f"Replaced {kind}")
        if kind == "file":
            assert link.read_text() == "user's executable\n"
        if kind == "directory":
            link.rmdir()
        else:
            link.unlink()
    try:
        install_cli(work / "uninstalled", work / "unused")
    except ValueError as error:
        assert "uv sync" in str(error)
    else:
        raise AssertionError("Installed a missing command")
    installer = [sys.executable, "-m", "genesis_curation.execution.install", "--repo", str(ROOT)]
    run([*installer, "--bin-dir", str(link.parent)], work, dict(os.environ))
    assert link.resolve() == CLI


def fake_environment(work: Path) -> dict[str, str]:
    binaries = work / "fake tools"
    binaries.mkdir()
    prefix = work / "fake prefix"
    (prefix / "conda-meta").mkdir(parents=True)
    (prefix / "bin").mkdir()
    for name in ("nextflow", "uv", "micromamba"):
        (prefix / "bin" / name).symlink_to(sys.executable)
    script(
        binaries / "pixi",
        """import json, os, sys
from pathlib import Path
if sys.argv[1] == 'info':
    prefix = Path(os.environ['CLI_FAKE_PREFIX'])
    print(json.dumps({'environments_info':[{'name':'default', 'prefix':str(prefix)}]}))
    sys.exit(0)
record = json.dumps({'argv':sys.argv[1:], 'cwd':os.getcwd()})
# The genesis launcher: pixi run --as-is --manifest-path ROOT --executable COMMAND...
assert '--executable' in sys.argv, sys.argv
Path(os.environ['CLI_PIXI_LOG']).write_text(record)
args = sys.argv[sys.argv.index('--executable') + 1:]
os.execv(args[0], args)
""",
    )
    return {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "GENESIS_REPO": str(PIPELINE),
        "CLI_FAKE_PREFIX": str(prefix),
        "CLI_PIXI_LOG": str(work / "pixi.json"),
    }


def check_launchers(work: Path) -> None:
    env = fake_environment(work)
    run([str(CLI), "benchmark", "plan", "missing.json"], work, env, 2)
    pixi = json.loads(Path(env["CLI_PIXI_LOG"]).read_text())
    assert pixi["cwd"] == str(work) and pixi["argv"][:2] == ["run", "--as-is"]
    delegated = pixi["argv"]
    assert delegated[-3:] == ["genesis_curation.benchmark", "plan", "missing.json"]


def check_offline_and_runtime(work: Path) -> None:
    with patch.dict(os.environ, {"GENESIS_REPO": str(work / "missing")}):
        assert launch.inspect_runtime()["status"] == "unavailable"
    with (
        patch.dict(os.environ, {"GENESIS_REPO": str(PIPELINE)}),
        patch.object(launch.shutil, "which", return_value=None),
    ):
        try:
            launch.command("benchmark", [])
        except ValueError as error:
            assert "Pixi" in str(error)
        else:
            raise AssertionError("Accepted missing Pixi")
    bare_env = {**os.environ, "PATH": "/usr/bin:/bin", "GENESIS_REPO": str(work / "missing")}
    run([str(CLI), "--help"], work, bare_env)
    run([str(CLI), "registry", "init", str(work / "catalog")], work, bare_env)
    # Pipelines run from a genesis-preprocessing checkout; this CLI no longer launches them.
    for command in ("pipeline", "pipeline-atac", "study"):
        removed = run([str(CLI), command, "--help"], work, bare_env, 2)
        assert "invalid choice" in removed.stderr, removed.stderr
    run([str(CLI), "benchmark", "run", "--help"], work, bare_env)
    reference = work / "offline-reference.fa"
    reference.write_text(">synthetic\nACGTacgtNN\n")
    inspected = run([str(CLI), "benchmark", "reference", "inspect", str(reference)], work, bare_env)
    assert json.loads(inspected.stdout)["contigs"]["synthetic"]["length"] == 10
    reference.write_text(">synthetic\nINVALID!\n")
    run([str(CLI), "benchmark", "reference", "inspect", str(reference)], work, bare_env, 2)
    # Installed Pixi must preserve argv/cwd too, not only the test double.
    if shutil.which("pixi"):
        uninstalled = work / "missing runtime"
        uninstalled.mkdir()
        for name in ("pixi.toml", "pixi.lock"):
            shutil.copy2(PIPELINE / name, uninstalled / name)
        try:
            launch.managed_command(uninstalled, ["/bin/echo", "must-not-run"])
        except ValueError as error:
            assert "install-all" in str(error)
        else:
            raise AssertionError("Uninstalled Pixi prefix fell back to host tools")
        assert not (uninstalled / ".pixi/envs/default/conda-meta").exists()
        with patch.dict(os.environ, {"GENESIS_REPO": str(PIPELINE)}):
            argv = launch.managed_command(
                PIPELINE,
                [
                    str(launch.python(PIPELINE)),
                    "-c",
                    "import json,os,sys; print(json.dumps([os.getcwd(),sys.argv[1:]]))",
                    "a b",
                    "$(touch injected); 'literal'",
                    "--profile",
                    "local,docker",
                ],
            )
        result = run(argv, work, dict(os.environ))
        assert json.loads(result.stdout) == [str(work), argv[-4:]]
        assert not (work / "injected").exists()
        print("PASS: installed Pixi preserves caller cwd and literal argv")
    else:
        print("NOT RUN: installed Pixi probe (Pixi not on PATH)")


def main() -> None:
    if not (PIPELINE / "workflows/dap-seq.nf").is_file():
        print(
            "NOT RUN: CLI launcher checks need GENESIS_REPO set to a genesis-preprocessing checkout"
        )
        return
    with tempfile.TemporaryDirectory(prefix="genesis cli ") as temporary:
        work = Path(temporary).resolve()
        check_installation(work)
        check_launchers(work)
        check_offline_and_runtime(work)
    print(
        "PASS: CLI installation, conflicts, independent cwd, benchmark delegation, "
        "removed launchers and offline core commands"
    )


if __name__ == "__main__":
    main()
