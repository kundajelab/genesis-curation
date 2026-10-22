"""A budget stop must reach scientific tools that started their own session."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run_queue import process_table, require_free_space, stop_owned_tree


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        destination = Path(temporary) / "missing-parent" / "result"
        with patch("run_queue.shutil.disk_usage", return_value=SimpleNamespace(free=99)):
            try:
                require_free_space(destination, 100)
            except ValueError as error:
                assert "Insufficient filesystem space" in str(error)
            else:
                raise AssertionError("An otherwise empty scratch tree hid a full filesystem")
        with patch("run_queue.shutil.disk_usage", return_value=SimpleNamespace(free=100)):
            require_free_space(destination, 100)
        ready = Path(temporary) / "child.pid"
        child_code = "import time; time.sleep(60)"
        code = (
            "import subprocess,sys,pathlib; "
            "p=subprocess.Popen([sys.executable,'-c',sys.argv[2]], start_new_session=True); "
            "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); p.wait()"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(ready), child_code], start_new_session=True
        )
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            if not ready.exists():
                raise AssertionError("Child fixture did not start")
            child = int(ready.read_text())
            assert os.getpgid(child) != os.getpgid(process.pid)
            assert child in process_table(), "Process table cannot see the fixture grandchild"
            stop_owned_tree(process)
            assert process.returncode is not None
            # The separately-sessioned grandchild must be gone (or an unreaped zombie).
            remaining = process_table().get(child)
            assert remaining is None or remaining[2].startswith("Z"), remaining
        finally:
            if process.poll() is None:
                stop_owned_tree(process)
    print("PASS filesystem reservation and queue stop of separately-sessioned descendant tools")


if __name__ == "__main__":
    main()
