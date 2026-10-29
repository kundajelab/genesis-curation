#!/usr/bin/env python3
"""Build exact, unambiguous Plant Ontology aliases from the pinned OBO snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def build(source: Path, output: Path) -> None:
    raw = source.read_bytes()
    expected = "d90949737a9925bdb775ba7dd4a9521cb63ca4acd40211e4c7661655f379f4c9"
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Use the pinned Plant Ontology snapshot; digest differs")
    baseline = (
        Path(__file__).parents[1] / "src/genesis_curation/curation/vocabularies/plant-v3.json"
    )
    value = json.loads(baseline.read_text())
    value.update(
        version="genesis-plant-terms/4",
        description=(
            "Plant Ontology 2026-01-09 names and unambiguous EXACT synonyms. "
            "Obsolete terms and ambiguous aliases are excluded; unmapped values remain raw."
        ),
    )
    value["sources"][0].update(
        sha256=expected, license="CC-BY-4.0", attribution="Planteome / Plant Ontology Consortium"
    )
    aliases: dict[str, dict[str, dict[str, dict[str, str]]]] = {}
    for block in raw.decode().split("[Term]\n")[1:]:
        if "is_obsolete: true" in block:
            continue
        item = dict(re.findall(r"^(id|name|namespace): (.+)$", block, re.M))
        if not item.get("id", "").startswith("PO:"):
            continue
        field = {
            "plant_anatomy": "tissue",
            "plant_structure_development_stage": "developmental_stage",
        }.get(item.get("namespace", ""))
        if not field:
            continue
        names = [item["name"]] + re.findall(r'^synonym: "([^"\\]+)" EXACT', block, re.M)
        for name in names:
            aliases.setdefault(field, {}).setdefault(name.casefold(), {})[item["id"]] = {
                "id": item["id"],
                "label": item["name"],
            }
    for field, entries in aliases.items():
        value["terms"][field] = {
            name: next(iter(options.values()))
            for name, options in sorted(entries.items())
            if len(options) == 1
        }
    output.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    print({field: len(entries) for field, entries in value["terms"].items()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.output)
