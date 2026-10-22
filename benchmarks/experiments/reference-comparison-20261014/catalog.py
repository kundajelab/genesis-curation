"""Select public multiDAP libraries by author TF family, before measuring outputs.

Download only metadata here. The emitted acquisition manifest is executed by
``genesis benchmark acquire`` and requires the publisher's complete FASTQ checksums.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.spec import sha256, write_json

COHORTS = {
    "dap-lyrata": ("GSE298980", "15-Arabidopsis_lyrata-PRJNA1177479.tsv"),
    "dap-sorghum": ("GSE299020", "24-Sorghum_bicolor-PRJNA1177471.tsv"),
}


def fetch(url: str, limit: int = 16_000_000) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Genesis-benchmark/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Metadata response exceeds its size limit")
    return data


def samples(text: str) -> dict[str, dict[str, Any]]:
    result = {}
    for block in re.split(r"(?m)^\^SAMPLE = ", text)[1:]:
        accession = block.splitlines()[0].strip()
        fields: dict[str, list[str]] = defaultdict(list)
        for line in block.splitlines()[1:]:
            if line.startswith("!Sample_") and " = " in line:
                name, value = line.split(" = ", 1)
                fields[name.removeprefix("!Sample_")].append(value)
        title = fields.get("title", [])
        if len(title) != 1 or title[0] in result:
            raise ValueError("Missing or duplicate GEO sample title")
        characteristics = {}
        for value in fields.get("characteristics_ch1", []):
            if ": " in value:
                key, term = value.split(": ", 1)
                if key in characteristics and characteristics[key] != term:
                    raise ValueError("Conflicting GEO characteristic: " + key)
                characteristics[key] = term
        result[title[0]] = {
            "geo": accession,
            "title": title[0],
            "characteristics": characteristics,
            "processing": fields.get("data_processing", []),
            "relations": fields.get("relation", []),
        }
    if not result:
        raise ValueError("No GEO sample records received")
    return result


def catalog(checkout: Path, out: Path, *, seed: int = 60141, families: int = 4) -> None:
    if families < 1:
        raise ValueError("Choose a positive number of TF families")
    out.mkdir(parents=True, exist_ok=False)
    assets, cohorts = [], {}
    for cohort, (series, filename) in COHORTS.items():
        url = (
            "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc="
            + series
            + "&targ=gsm&form=text&view=full"
        )
        raw = fetch(url)
        source = samples(raw.decode())
        with (checkout / filename).open() as stream:
            sheet = {r["sample_id"]: r for r in csv.DictReader(stream, delimiter="\t")}
        grouped: dict[str, list[str]] = defaultdict(list)
        excluded = []
        for name, row in sheet.items():
            if row["control_sample"] == "-":
                continue
            family = source.get(name, {}).get("characteristics", {}).get("tf family")
            if not family:
                excluded.append({"sample_id": name, "reason": "No exact GEO title/family match"})
            else:
                grouped[family].append(name)

        def order(value: str) -> str:
            return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()

        if len(grouped) < families:
            raise ValueError("Insufficient source-annotated TF families: " + cohort)
        selected = [
            min(grouped[family], key=order) for family in sorted(grouped, key=order)[:families]
        ]
        controls = {sheet[name]["control_sample"] for name in selected}
        chosen = sorted(controls) + selected
        run_ids = {
            name: sheet[name]["read1_url"].rsplit("/", 1)[1].split("_", 1)[0] for name in chosen
        }
        if len(set(run_ids.values())) != len(chosen):
            raise ValueError("Several sheet entries resolve to the same run")
        libraries = []
        for name in chosen:
            row, run = sheet[name], run_ids[name]
            ena_url = (
                "https://www.ebi.ac.uk/ena/portal/api/filereport?accession="
                + run
                + "&result=read_run&fields=run_accession,experiment_accession,"
                "sample_accession,library_layout,read_count,base_count,fastq_ftp,"
                "fastq_md5,fastq_bytes&format=json"
            )
            ena = json.loads(fetch(ena_url))
            if len(ena) != 1 or ena[0]["run_accession"] != run:
                raise ValueError("Ambiguous ENA run")
            record = ena[0]
            urls = record["fastq_ftp"].split(";")
            if record["library_layout"] != "PAIRED" or len(urls) != 2:
                raise ValueError("Expected exactly two paired FASTQs")
            names = []
            for address, digest, size in zip(
                urls,
                record["fastq_md5"].split(";"),
                record["fastq_bytes"].split(";"),
                strict=True,
            ):
                asset_id = address.rsplit("/", 1)[1]
                names.append(asset_id)
                assets.append(
                    {
                        "id": asset_id,
                        "url": "https://" + address,
                        "bytes": int(size),
                        "checksum": {"algorithm": "md5", "value": digest},
                        "source": {
                            "accession": run,
                            "metadata": record,
                            "url": ena_url,
                        },
                    }
                )
            libraries.append(
                {
                    "id": run,
                    "sample_id": name,
                    "control": None if name in controls else run_ids[row["control_sample"]],
                    "layout": "PE",
                    "reads": names,
                    "metadata": source.get(name),
                    "ena": record,
                    "biological_replicate": "not_established",
                }
            )
        cohorts[cohort] = {
            "assay": "DAP-seq",
            "species": sheet[chosen[0]]["species"],
            "cultivar": "MN47" if cohort == "dap-lyrata" else "RTx430",
            "libraries": libraries,
            "selection": {
                "seed": seed,
                "families": families,
                "method": "lowest SHA256(seed:family), then lowest SHA256(seed:exact sample title)",
                "eligible_families": sorted(grouped),
                "excluded": excluded,
                "sheet": filename,
                "sheet_sha256": sha256(checkout / filename),
                "source_url": url,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "retrieved": datetime.datetime.now(datetime.UTC).isoformat(),
                "control_policy": "repository assigned controls; not author plate-pooled controls",
            },
        }
        print(
            cohort,
            "libraries",
            len(libraries),
            "families",
            [source[n]["characteristics"]["tf family"] for n in selected],
            flush=True,
        )
    write_json(out / "cohorts.json", {"schema_version": 1, "cohorts": cohorts})
    write_json(
        out / "acquisition.json",
        {
            "schema_version": 1,
            "assets": assets,
            "limits": {"download_bytes": 120_000_000_000, "seconds": 86400},
        },
    )
    print("Complete FASTQ bytes:", sum(a["bytes"] for a in assets))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=60141)
    parser.add_argument("--families", type=int, default=4)
    args = parser.parse_args()
    catalog(
        args.checkout.resolve(),
        args.out.resolve(),
        seed=args.seed,
        families=args.families,
    )
