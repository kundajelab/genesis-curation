"""Check installation, public launchers and process boundaries without scientific tools."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from study_fixture import create_study_fixture

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
if '--executable' in sys.argv:
    # The genesis launcher: pixi run --as-is --manifest-path ROOT --executable COMMAND...
    Path(os.environ['CLI_PIXI_LOG']).write_text(record)
    args = sys.argv[sys.argv.index('--executable') + 1:]
    os.execv(args[0], args)
# run-pipeline.sh: pixi run --manifest-path ROOT COMMAND...; run COMMAND from PATH.
Path(os.environ['CLI_INNER_PIXI_LOG']).write_text(record)
assert sys.argv[1:3] == ['run', '--manifest-path'], sys.argv
os.execvp(sys.argv[4], sys.argv[4:])
""",
    )
    script(
        binaries / "nextflow",
        """import json, os, shutil, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
record = {'argv':sys.argv[1:], 'cwd':os.getcwd(), 'pid':os.getpid()}
record['genesis_tools'] = shutil.which('genesis-tools')
Path(os.environ['CLI_TOOL_LOG']).write_text(json.dumps(record))
if os.environ.get('CLI_WAIT') == '1':
    while True:
        time.sleep(0.1)
sys.exit(int(os.environ.get('CLI_EXIT', '0')))
""",
    )
    return {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "GENESIS_REPO": str(PIPELINE),
        "CLI_FAKE_PREFIX": str(prefix),
        "CLI_PIXI_LOG": str(work / "pixi.json"),
        "CLI_INNER_PIXI_LOG": str(work / "inner-pixi.json"),
        "CLI_TOOL_LOG": str(work / "tool.json"),
    }


def check_launchers(work: Path) -> None:
    env = fake_environment(work)
    tool_log = Path(env["CLI_TOOL_LOG"])
    sheet = work / "sample sheet.tsv"
    sheet.touch()
    arguments = [
        "pipeline",
        "-p",
        "local,conda",
        "-w",
        "run folders",
        "-n",
        "named",
        sheet.name,
        "--references",
        "a reference folder",
        "--label",
        "$(touch injected); 'quoted'",
    ]
    run([str(CLI), *arguments], work, env)
    fresh = json.loads(tool_log.read_text())
    # Pipeline helpers come from the pipeline checkout's environment, not this one.
    assert fresh["genesis_tools"] == str(launch.python(PIPELINE).parent / "genesis-tools")
    nf_args = fresh["argv"]
    assert nf_args[nf_args.index("--input") + 1] == str(sheet)
    assert nf_args[nf_args.index("--workspace") + 1] == str(work / "run folders")
    assert nf_args[nf_args.index("-profile") + 1] == "local,conda"
    assert nf_args[-4:] == arguments[-4:]
    assert fresh["cwd"] == str(work / "run folders/named")
    pixi = json.loads(Path(env["CLI_PIXI_LOG"]).read_text())
    assert pixi["cwd"] == str(work) and pixi["argv"][:2] == ["run", "--as-is"]
    # run-pipeline.sh runs Nextflow through this checkout's Pixi environment, from the run folder.
    inner = json.loads(Path(env["CLI_INNER_PIXI_LOG"]).read_text())
    assert inner["argv"][:4] == ["run", "--manifest-path", str(PIPELINE), "nextflow"], inner
    assert inner["cwd"] == str(work / "run folders/named"), inner
    assert not (work / "injected").exists()
    run([str(CLI), *arguments, "-resume"], work, env)
    resumed = json.loads(tool_log.read_text())
    assert resumed["cwd"] == fresh["cwd"] and resumed["argv"] == [*nf_args, "-resume"]
    run([str(CLI), *arguments], work, {**env, "CLI_EXIT": "23"}, 23)
    before = tool_log.read_bytes()
    run([str(CLI), "pipeline", "missing.tsv"], work, env, 2)
    assert tool_log.read_bytes() == before

    atac = work / "atac inputs"
    atac.mkdir()
    atac_sheet, references, _ = create_study_fixture(atac)
    atac_args = [
        "pipeline-atac",
        str(atac_sheet.relative_to(work)),
        "--references",
        str(references.relative_to(work)),
        "--outdir",
        "atac results",
    ]
    failed_env = {**env, "CLI_EXIT": "23"}
    run([str(CLI), *atac_args], work, failed_env, 23)
    manifest = (work / "atac results/manifest.json").read_bytes()
    fresh_atac = json.loads(tool_log.read_text())
    run([str(CLI), *atac_args, "--resume"], work, failed_env, 23)
    resumed_atac = json.loads(tool_log.read_text())
    assert resumed_atac["argv"] == [*fresh_atac["argv"], "-resume"]
    assert resumed_atac["cwd"] == str(work / "atac results")
    assert (work / "atac results/manifest.json").read_bytes() == manifest
    assert json.loads((work / "atac results/provenance.json").read_text())["exit_code"] == 23
    run([str(CLI), "benchmark", "plan", "missing.json"], work, env, 2)
    delegated = json.loads(Path(env["CLI_PIXI_LOG"]).read_text())["argv"]
    assert delegated[-3:] == ["genesis_curation.benchmark", "plan", "missing.json"]

    # The DAP CLI/Pixi/wrapper exec chain retains a single owned process.
    tool_log.unlink()
    process = subprocess.Popen(
        [str(CLI), *arguments],
        cwd=work,
        env={**env, "CLI_WAIT": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not tool_log.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert tool_log.exists(), "Signal test did not reach Nextflow stub"
        assert json.loads(tool_log.read_text())["pid"] == process.pid
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, (stdout, stderr)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def check_offline_and_runtime(work: Path) -> None:
    with patch.dict(os.environ, {"GENESIS_REPO": str(work / "missing")}):
        assert launch.inspect_runtime()["status"] == "unavailable"
    with (
        patch.dict(os.environ, {"GENESIS_REPO": str(PIPELINE)}),
        patch.object(launch.shutil, "which", return_value=None),
    ):
        try:
            launch.command("pipeline", [])
        except ValueError as error:
            assert "Pixi" in str(error)
        else:
            raise AssertionError("Accepted missing Pixi")
    bare_env = {**os.environ, "PATH": "/usr/bin:/bin", "GENESIS_REPO": str(work / "missing")}
    run([str(CLI), "--help"], work, bare_env)
    run([str(CLI), "registry", "init", str(work / "catalog")], work, bare_env)
    # The ATAC runner ships with the pipeline checkout, so even its help needs GENESIS_REPO.
    run([str(CLI), "pipeline-atac", "--help"], work, bare_env, 2)
    run([str(CLI), "benchmark", "run", "--help"], work, bare_env)
    reference = work / "offline-reference.fa"
    reference.write_text(">synthetic\nACGTacgtNN\n")
    inspected = run([str(CLI), "benchmark", "reference", "inspect", str(reference)], work, bare_env)
    assert json.loads(inspected.stdout)["contigs"]["synthetic"]["length"] == 10
    reference.write_text(">synthetic\nINVALID!\n")
    run([str(CLI), "benchmark", "reference", "inspect", str(reference)], work, bare_env, 2)
    run([str(CLI), "pipeline", "input.tsv"], work, bare_env, 2)
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
        "PASS: CLI installation, conflicts, independent cwd, DAP/ATAC forwarding, resume, exits, "
        "interrupts, benchmark delegation and offline core commands"
    )


if __name__ == "__main__":
    main()
