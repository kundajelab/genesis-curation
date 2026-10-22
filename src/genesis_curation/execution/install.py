"""Expose an installed checkout's console script without modifying shell configuration."""

from __future__ import annotations

import argparse
import os
import shlex
from pathlib import Path


def install_cli(repo: Path, bin_dir: Path) -> Path:
    target = repo.expanduser().resolve() / ".venv/bin/genesis"
    if not target.is_file() or not os.access(target, os.X_OK):
        raise ValueError(f"Missing executable {target}; run `uv sync` in {repo} first.")
    directory = bin_dir.expanduser().resolve()
    link = directory / "genesis"
    if link.is_symlink() and link.resolve() == target:
        return link
    if link.exists() or link.is_symlink():
        raise ValueError(
            f"Refusing to overwrite {link}. Inspect the existing command and choose another "
            "--bin-dir, or remove its link explicitly before switching checkouts."
        )
    directory.mkdir(parents=True, exist_ok=True)
    # symlink creation is exclusive: a concurrent installer cannot replace another file.
    link.symlink_to(target)
    return link


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    args = parser.parse_args()
    try:
        link = install_cli(args.repo, args.bin_dir)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Cannot install Genesis command: {error}\n")
    print(f"Genesis command: {link} -> {link.resolve()}")
    print(
        f'Add this directory to PATH if needed: export PATH={shlex.quote(str(link.parent))}:"$PATH"'
    )
    print("Run `genesis --help` and `genesis doctor`. Shell startup files were not changed.")


if __name__ == "__main__":
    main()
