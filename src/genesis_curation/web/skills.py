"""Load packaged and private instruction-only SKILL.md folders."""

from __future__ import annotations

import copy
import re
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from ..contracts.records import fingerprint

BUILTINS = Path(str(files("genesis_curation").joinpath("skills")))
MAX_INSTRUCTIONS = 16000


def read_markdown(path: Path, root: Path) -> str:
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Skill resource must stay inside its skill root")
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Missing or oversized skill resource")
    return path.read_text(encoding="utf-8")


def load_skill(folder: Path, root: Path) -> dict[str, Any]:
    if folder.is_symlink():
        raise ValueError("Skill folders cannot be symlinks")
    lines = read_markdown(folder / "SKILL.md", root).splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("SKILL.md requires YAML frontmatter")
    end = lines.index("---", 1)
    try:
        header = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as error:
        raise ValueError("Invalid skill frontmatter") from error
    if not isinstance(header, dict) or set(header) - {"name", "description", "metadata"}:
        raise ValueError("Skill frontmatter supports name, description and metadata")
    name, description = header.get("name"), header.get("description")
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name)
        or name != folder.name
        or not isinstance(description, str)
        or not description.strip()
        or len(description) > 1024
    ):
        raise ValueError("Invalid skill name, folder name or description")
    metadata = header.get("metadata", {})
    if not isinstance(metadata, dict) or set(metadata) - {
        "version",
        "title",
        "includes",
        "references",
    }:
        raise ValueError("Unsupported skill metadata")
    version = metadata.get("version", "1")
    title = metadata.get("title", name.replace("-", " ").title())
    if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in (version, title)):
        raise ValueError("Skill version and title must be nonempty strings")
    includes = metadata.get("includes", [])
    references = metadata.get("references", [])
    if (
        not isinstance(includes, list)
        or len(includes) > 8
        or any(
            not isinstance(v, str) or Path(v).is_absolute() or Path(v).suffix != ".md"
            for v in includes
        )
    ):
        raise ValueError("Skill includes must be relative Markdown resources")
    if not isinstance(references, list) or any(
        not isinstance(r, dict)
        or set(r) != {"title", "id"}
        or any(not isinstance(v, str) or not v.strip() for v in r.values())
        for r in references
    ):
        raise ValueError("Invalid skill references")
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise ValueError("Skill instructions cannot be empty")
    instructions = "\n\n".join(
        [read_markdown(folder / entry, root).strip() for entry in includes] + [body]
    )
    if len(instructions) > MAX_INSTRUCTIONS:
        raise ValueError("Skill exceeds the instruction budget")
    value = {
        "id": name,
        "description": description,
        "version": version,
        "title": title,
        "instructions": instructions,
        "references": references,
    }
    return {**value, "sha256": fingerprint(value)}


@lru_cache(maxsize=1)
def bundled() -> list[dict[str, Any]]:
    return [load_skill(path.parent, BUILTINS) for path in sorted(BUILTINS.glob("*/SKILL.md"))]


def available(folder: Path | None) -> list[dict[str, Any]]:
    result = copy.deepcopy(bundled())
    if not result:
        raise ValueError("The application package is missing its built-in skills")
    if folder is not None and folder.exists():
        if any(folder.glob("*.json")):
            raise ValueError("Use a named skill folder containing SKILL.md, not JSON skill files")
        result.extend(
            load_skill(path.parent, path.parent) for path in sorted(folder.glob("*/SKILL.md"))
        )
    if len({r["id"] for r in result}) != len(result):
        raise ValueError("Duplicate skill ID")
    return result


def select(folder: Path | None, skill_id: str) -> dict[str, Any]:
    if skill_id == "none":
        return {
            "id": "none",
            "version": "1",
            "instructions": "",
            "references": [],
            "sha256": fingerprint(None),
        }
    for skill in available(folder):
        if skill["id"] == skill_id:
            return skill
    raise ValueError("Unknown skill")
