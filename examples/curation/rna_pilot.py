"""Bounded public-metadata pilot. No sequencing files, automatic sign-off or API fallback.

Run with --help. Keep --root outside the repository; resume uses immutable source snapshots.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from genesis_curation.contracts.records import canonical, dump, fingerprint, load, loads, now
from genesis_curation.llm import providers
from genesis_curation.registry import store
from genesis_curation.web import catalog, intake, preparation, studies, workflow
from genesis_curation.web.discovery_catalog import RNA_PILOT_SPECIES

FIELDS = (
    "run_accession,study_accession,experiment_accession,sample_accession,scientific_name,"
    "tax_id,library_strategy,library_source,library_selection,library_layout,library_name,"
    "instrument_platform,sample_title,study_title"
)


def collect(root: Path) -> None:
    manifest_path = root / "pilot-manifest.json"
    if manifest_path.exists():
        return
    selected, extra, searches = [], [], []
    for species in RNA_PILOT_SPECIES:
        url = "https://www.ebi.ac.uk/ena/portal/api/search?" + urlencode(
            {
                "result": "read_run",
                "query": 'scientific_name="' + species + '" AND library_strategy="RNA-Seq"',
                "fields": FIELDS,
                "format": "json",
                "limit": 50,
            }
        )
        try:
            source = intake.fetch(root, url)
            rows = json.loads(source["text"])
            if not isinstance(rows, list):
                raise ValueError("Unexpected archive response")
            rows = [
                r
                for r in rows
                if r.get("scientific_name") == species
                and r.get("library_strategy", "").lower() == "rna-seq"
            ]
            selected.extend(
                {**r, "_source_url": url, "_pilot_stratum": "species"} for r in rows[:10]
            )
            extra.append(
                [
                    {**r, "_source_url": url, "_pilot_stratum": "difficult-library"}
                    for r in rows[10:]
                    if r.get("library_selection", "").lower() not in {"polya", "cdna_oligo_dt"}
                ]
            )
            searches.append(
                {
                    "species": species,
                    "url": url,
                    "returned": len(rows),
                    "snapshot_hash": fingerprint(source["text"]),
                    "status": "retrieved",
                }
            )
        except (ValueError, OSError) as error:
            searches.append(
                {
                    "species": species,
                    "url": url,
                    "status": "unavailable",
                    "reason": str(error)[:500],
                }
            )
            extra.append([])
        print(species + ": " + searches[-1]["status"], flush=True)
    difficult = [pool[i] for i in range(50) for pool in extra if i < len(pool)][:30]
    selected.extend(difficult)
    selected = list({r["run_accession"]: r for r in selected}.values())
    dump(
        manifest_path,
        {
            "created": now(),
            "target": 200,
            "selected": len(selected),
            "rows": selected,
            "searches": searches,
            "selection": "10 archive rows per species + 30 non-polyA/unknown-selection cases; "
            "not random or gold",
        },
        immutable=True,
    )


def ingest(root: Path) -> None:
    manifest = load(root / "pilot-manifest.json")
    ids = catalog.import_rows(
        root / "catalog",
        canonical(manifest["rows"]),
        "pilot.json",
        "rna-pilot",
        "run_accession",
        "public",
        "pilot",
    )
    receipts = []
    groups: dict[str, list[str]] = {}
    for sid in ids:
        state = catalog.get(root / "catalog", sid)
        row = state["original"]
        groups.setdefault(row["study_accession"], []).append(sid)
        intake.attach(root / "catalog", sid, canonical(row), row["_source_url"], "sample", "pilot")
        for accession in (row["sample_accession"], row["experiment_accession"]):
            url = "https://www.ebi.ac.uk/ena/browser/api/xml/" + accession
            try:
                source = intake.fetch(root, url)
                intake.attach(root / "catalog", sid, source["text"], url, "sample", "pilot")
                tree = ET.fromstring(source["text"])
                attributes = [
                    n.findtext("TAG", "").replace(" ", "_") + ": " + n.findtext("VALUE", "")
                    for n in tree.findall(".//SAMPLE_ATTRIBUTE")
                ]
                protocol = tree.findtext(".//LIBRARY_CONSTRUCTION_PROTOCOL", "")
                if protocol:
                    attributes.append("library_preparation: " + protocol)
                if attributes:
                    intake.attach(
                        root / "catalog", sid, "\n".join(attributes), url, "sample", "pilot"
                    )
                receipts.append({"subject": sid, "url": url, "status": "retrieved"})
            except (ValueError, OSError, ET.ParseError) as error:
                receipts.append(
                    {
                        "subject": sid,
                        "url": url,
                        "status": "unavailable",
                        "reason": str(error)[:500],
                    }
                )
        print("Retrieved " + row["run_accession"], flush=True)
    keys = [
        studies.link(root / "catalog", members, accession, "pilot", accession=accession)
        for accession, members in groups.items()
    ]
    dump(root / "pilot-intake.json", {"studies": keys, "receipts": receipts})


def report(root: Path, seconds: float | None = None) -> dict[str, Any]:
    manifest = load(root / "pilot-manifest.json")
    with contextlib.closing(store.connect(root / "catalog")) as db:
        states = [loads(r[0]) for r in db.execute("SELECT body FROM curation_subjects")]
        entries = [loads(r[0]) for r in db.execute("SELECT body FROM curation_studies")]
        invocations = [
            loads(r[0])["data"]
            for r in db.execute("SELECT body FROM records WHERE kind='invocation'")
        ]
    usage = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [i.get("usage", {}).get(name) for i in invocations]
        usage[name] = sum(values) if all(isinstance(v, int) for v in values) else None
    result = {
        "target": 200,
        "records": len(states),
        "studies": len(entries),
        "species": dict(Counter(r["scientific_name"] for r in manifest["rows"])),
        "workflow_stages": dict(
            Counter(
                s["preparation"].get("workflow", {}).get("stage", "not_started") for s in entries
            )
        ),
        "annotation_fields": workflow.counts(states),
        "invocations": len(invocations),
        "invocation_statuses": dict(Counter(i["status"] for i in invocations)),
        "invocation_tasks": dict(Counter(i["task"] for i in invocations)),
        "preparation_statuses": dict(Counter(s["preparation"]["status"] for s in entries)),
        "usage": usage,
        "cost": None,
        "models": dict(Counter(i["model_id"] for i in invocations)),
        "inference_seconds": sum(i["latency_seconds"] for i in invocations),
        "elapsed_seconds": seconds,
        "accuracy": "NOT EVALUATED: no expert gold labels",
        "read_level_qc": "NOT EVALUATED: metadata only; no measurements imported",
    }
    if (root / "pilot-intake.json").exists():
        result["retrieval_statuses"] = dict(
            Counter(r["status"] for r in load(root / "pilot-intake.json")["receipts"])
        )
    dump(root / "pilot-report.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("collect", "ingest", "curate", "report"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--providers", type=Path)
    parser.add_argument("--provider")
    args = parser.parse_args()
    root = args.root.resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("Use a private runtime outside the repository")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (root / "catalog/registry.sqlite").exists():
        store.initialize(root / "catalog")
    started = time.monotonic()
    if args.action == "collect":
        collect(root)
    elif args.action == "ingest":
        ingest(root)
    elif args.action == "curate":
        if not args.providers or not args.provider:
            parser.error("curate requires --providers and --provider")
        config = providers.select(args.providers, args.provider)
        if config["external"] or config["mode"] != "live":
            raise ValueError("This pilot requires an explicitly configured live local provider")
        for key in load(root / "pilot-intake.json")["studies"]:
            task = {
                "id": "pilot-checkpoint",
                "actor": "pilot",
                "payload": {
                    "study": key,
                    "provider": args.provider,
                    "provider_hash": fingerprint(config),
                },
            }
            while True:
                value = preparation.run(root, args.providers, task)
                report(root, time.monotonic() - started)
                print(canonical({"study": key, **value}), flush=True)
                if not value.get("pending"):
                    break
    if (root / "pilot-manifest.json").exists():
        print(
            canonical(report(root, None if args.action == "report" else time.monotonic() - started))
        )


if __name__ == "__main__":
    main()
