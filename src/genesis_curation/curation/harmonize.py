"""Source snapshots, field-level proposals and separately applied metadata revisions."""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import re
import sqlite3
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener

from ..contracts.records import (
    canonical,
    dump,
    fingerprint,
    identity,
    load,
    loads,
    now,
    record,
    reject_constant,
    unique_object,
)
from ..llm import providers
from ..llm.security import check, schema
from ..registry import store
from . import review

MAX_SOURCE = 2 * 1024 * 1024
FIELDS = {
    "organism",
    "taxon",
    "genotype",
    "cultivar",
    "tissue",
    "developmental_stage",
    "treatment",
    "assay",
    "protocol",
    "library_layout",
    "chemistry",
    "biological_replicate",
    "technical_lane",
    "accession",
    "reference_proposal",
    "tf_identity",
    "tf_source_organism",
    "dna_organism",
    "dna_genotype",
    "control_assignment_proposal",
    "dna_context",
    "notes",
}
ALIASES = {
    "organism_ch1": "organism",
    "taxid_ch1": "taxon",
    "scientific_name": "organism",
    "tax_id": "taxon",
    "sample_accession": "accession",
    "run_accession": "accession",
    "library_strategy": "assay",
    "dev_stage": "developmental_stage",
}
CLASSIFICATIONS = {"public": 0, "internal": 1, "local-only": 2}


def vocabulary() -> dict[str, Any]:
    return loads(files(__package__).joinpath("vocabularies/plant-v1.json").read_text())


def source_record(text: str, location: str, media_type: str) -> dict[str, Any]:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return record(
        "source",
        {
            "location": location,
            "worker": "controller",
            "sha256": digest,
            "snapshot": text,
            "media_type": media_type,
        },
        identity("metadata-source", location, digest),
    )


def retrieve(accession: str) -> tuple[str, str, str]:
    """Fixed ENA/NCBI public APIs; no user or model supplied URLs, redirects or supplements."""
    if re.fullmatch(r"[SED]R[RSXAP]\d{3,12}", accession):
        url = (
            "https://www.ebi.ac.uk/ena/portal/api/filereport?accession="
            + quote(accession)
            + "&result=read_run&format=json&fields=run_accession,sample_accession,scientific_name,"
            "tax_id,library_strategy,library_layout"
        )
        media = "application/json"
    elif re.fullmatch(r"G[SM]E?\d{3,12}", accession) or re.fullmatch(r"GSM\d{3,12}", accession):
        url = (
            "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc="
            + quote(accession)
            + "&targ=self&form=text&view=brief"
        )
        media = "text/plain"
    else:
        raise ValueError("Supported public accessions: ENA run/sample/study or GEO GSE/GSM")
    if urlsplit(url).hostname not in {"www.ebi.ac.uk", "www.ncbi.nlm.nih.gov"}:
        raise ValueError("Source host is not allowlisted")
    with build_opener(providers.NoRedirect()).open(
        Request(url, headers={"User-Agent": "Genesis-curation/1"}), timeout=20
    ) as response:
        content = response.read(MAX_SOURCE + 1)
    if len(content) > MAX_SOURCE:
        raise ValueError("Public source exceeds 2 MiB")
    return content.decode("utf-8"), url, media


def ingest(
    directory: Path,
    dataset: str,
    paths: list[Path],
    accessions: list[str],
    classification: str,
    *,
    scope: str = "results",
) -> dict[str, Any]:
    if classification not in CLASSIFICATIONS or not paths and not accessions:
        raise ValueError("Provide source files/accessions and a valid classification")
    if len(paths) + len(accessions) > 64:
        raise ValueError("Select at most 64 source documents per dataset")
    sources = []
    for path in paths:
        with path.open("rb") as stream:
            content = stream.read(MAX_SOURCE + 1)
        if len(content) > MAX_SOURCE:
            raise ValueError("Source exceeds 2 MiB; supply a bounded relevant excerpt")
        media = {
            ".json": "application/json",
            ".csv": "text/csv",
            ".tsv": "text/tab-separated-values",
        }.get(path.suffix, "text/plain")
        text = content.decode("utf-8")
        if media == "application/json":
            # Preserve the original bytes while rejecting duplicate keys/nonfinite JSON.
            json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
            # General arrays from ENA are supported by the extraction function below.
        sources.append(source_record(text, str(path.resolve()), media))
    for accession in accessions:
        text, url, media = retrieve(accession)
        sources.append(source_record(text, url, media))
    if sum(len(s["data"]["snapshot"]) for s in sources) > 8 * MAX_SOURCE:
        raise ValueError("Source bundle exceeds 16 MiB")
    with store.write(directory, scope=scope) as db:
        classification = max(
            (classification, store.classification(db, dataset)), key=CLASSIFICATIONS.__getitem__
        )
        row = store.current(db, dataset)
        head = db.execute(
            f"SELECT source_bundle FROM {store.heads_table(db)} WHERE dataset=?", (dataset,)
        ).fetchone()
        # Re-ingestion extends evidence instead of silently discarding earlier sources.
        if head and head[0]:
            previous = store.get(db, "source_bundle", head[0])["data"]
            sources = previous["sources"] + sources
            classification = max(
                (classification, previous["classification"]), key=CLASSIFICATIONS.__getitem__
            )
            accessions = sorted(set(accessions + previous["accessions"]))
        sources = list({s["version"]: s for s in sources}.values())
        if (
            len(sources) > 64
            or sum(len(s["data"]["snapshot"].encode()) for s in sources) > 8 * MAX_SOURCE
        ):
            raise ValueError(
                "Combined source history exceeds 64 sources/16 MiB; split dataset scope"
            )
        if (
            head
            and head[0]
            and previous["sources"] == sources
            and previous["classification"] == classification
            and previous["manifest_version"] == row["manifest"]
        ):
            return store.get(db, "source_bundle", head[0])
        data = {
            "dataset_id": dataset,
            "manifest_version": row["manifest"],
            "classification": classification,
            "sources": sources,
            "retrieved_at": now(),
            "accessions": accessions,
        }
        value = record(
            "source_bundle", data, identity("source-bundle", dataset, fingerprint(data)), version=2
        )
        for source in sources:
            store.put(db, source, dataset)
        store.put(db, value, dataset)
        if scope == "inputs":
            db.execute(
                "UPDATE input_heads SET source_bundle=? WHERE dataset=?",
                (value["version"], dataset),
            )
        else:
            db.execute(
                f"INSERT INTO {store.heads_table(db)}(dataset,source_bundle,canonical_revision) "
                "VALUES(?,?,NULL) ON CONFLICT(dataset) DO "
                "UPDATE SET source_bundle=excluded.source_bundle",
                (dataset, value["version"]),
            )
    return value


def head(db: sqlite3.Connection, dataset: str) -> dict[str, Any]:
    row = db.execute(
        f"SELECT source_bundle FROM {store.heads_table(db)} WHERE dataset=?", (dataset,)
    ).fetchone()
    if not row or not row[0]:
        raise ValueError("Ingest source evidence for this dataset first")
    result = store.get(db, "source_bundle", row[0])
    if result["data"]["manifest_version"] != store.current(db, dataset)["manifest"]:
        raise ValueError(
            "Source bundle targets an older manifest; ingest against the current revision"
        )
    return result


def locate(source: dict[str, Any], locator: str) -> str:
    text = source["data"]["snapshot"]
    if locator.startswith("line:"):
        line = int(locator[5:])
        if not 1 <= line <= len(text.splitlines()):
            raise ValueError("Evidence line outside source")
        return text.splitlines()[line - 1]
    if locator.startswith("/"):
        value = json.loads(text)
        for key in locator[1:].split("/"):
            key = key.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value if isinstance(value, str) else canonical(value)
    raise ValueError("Field evidence needs an exact JSON pointer or line:N")


def extract(bundle: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    found: dict[str, list[dict[str, Any]]] = {}
    vocab = vocabulary()
    for source in bundle["data"]["sources"]:
        data = source["data"]
        items: list[tuple[str, Any, str, str]] = []
        if data["media_type"] == "application/json":
            raw = json.loads(data["snapshot"])
            rows = raw if isinstance(raw, list) else [raw]
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                for name, value in row.items():
                    locator = (
                        (f"/{index}" if isinstance(raw, list) else "")
                        + "/"
                        + name.replace("~", "~0").replace("/", "~1")
                    )
                    items.append((name, value, locator, locate(source, locator)))
        elif data["media_type"] in {"text/csv", "text/tab-separated-values"}:
            reader = csv.DictReader(
                io.StringIO(data["snapshot"]),
                delimiter="\t" if data["media_type"].endswith("values") else ",",
            )
            for row in reader:
                # csv.line_num is the ending physical line; multiline values are left for manual
                # review.
                line = reader.line_num
                for name, value in row.items():
                    if name and isinstance(value, str) and "\n" not in value:
                        items.append(
                            (name, value, f"line:{line}", data["snapshot"].splitlines()[line - 1])
                        )
        else:
            for index, line in enumerate(data["snapshot"].splitlines(), 1):
                match = re.match(r"(?:!Sample_)?([A-Za-z_][A-Za-z_0-9]*)\s*[:=]\s*(.+)", line)
                if match:
                    name, value = match[1], match[2]
                    if name == "characteristics_ch1" and ":" in value:
                        name, value = value.split(":", 1)
                        name, value = name.strip().replace(" ", "_"), value.strip()
                    items.append((name, value, f"line:{index}", line))
        for raw_name, value, locator, quoted in items:
            name = ALIASES.get(raw_name.lower(), raw_name.lower())
            if name not in FIELDS or value in (None, "") or type(value) not in (str, int):
                continue
            if sum(len(candidates) for candidates in found.values()) >= 4096:
                raise ValueError("Too many field observations; select sources for one library")
            term = vocab["terms"].get(name, {}).get(str(value).casefold())
            found.setdefault(name, []).append(
                {
                    "field": name,
                    "original": metadata.get(name),
                    "value": term["label"] if term else value,
                    "status": "known",
                    "vocabulary_id": term["id"] if term else None,
                    "vocabulary_version": vocab["version"],
                    "evidence": [
                        {"source_version": source["version"], "locator": locator, "quote": quoted}
                    ],
                    "rationale": "Explicit source field; biological meaning still requires review",
                    "confidence": None,
                }
            )
    result = []
    for name, candidates in found.items():
        candidate = candidates[0]
        candidate["evidence"] = [e for c in candidates for e in c["evidence"]]
        values = {canonical(c["value"]) for c in candidates}
        if len(values) > 1:
            candidate.update(
                value=None,
                status="conflicting",
                vocabulary_id=None,
                rationale="Source fields conflict; curator resolution required",
            )
        # Library strategy/title alone never assigns bulk versus single-cell or genuine 5-prime
        # protocols.
        if name in {"assay", "protocol"} and str(candidate["value"]).casefold() in {
            "atac-seq",
            "gro-seq",
            "gro-cap",
            "5-prime",
            "5'",
        }:
            candidate.update(
                value=None,
                status="unknown",
                vocabulary_id=None,
                rationale="Protocol needs explicit bulk/single-cell and library evidence",
            )
        result.append(candidate)
    for name in sorted(FIELDS - set(found)):
        result.append(
            {
                "field": name,
                "original": metadata.get(name),
                "value": None,
                "status": "unknown",
                "vocabulary_id": None,
                "vocabulary_version": vocab["version"],
                "evidence": [],
                "rationale": "No explicit source evidence",
                "confidence": None,
            }
        )
    return result


def verify_fields(bundle: dict[str, Any], fields: list[dict[str, Any]]) -> None:
    check({"fields": fields}, schema("metadata-response-v1"))
    if len({f["field"] for f in fields}) != len(fields) or any(
        f["field"] not in FIELDS for f in fields
    ):
        raise ValueError("Duplicate or unsupported metadata field")
    sources = {s["version"]: s for s in bundle["data"]["sources"]}
    vocab = vocabulary()
    for field in fields:
        if field["status"] == "known" and field["value"] in (None, ""):
            raise ValueError("A known field needs an explicit nonempty value")
        if field["status"] == "not_applicable" and field["value"] is not None:
            raise ValueError("Not-applicable fields must use null and cite explicit evidence")
        if field["status"] in {"unknown", "conflicting"} and field["value"] is not None:
            raise ValueError("Unknown/conflicting fields must abstain with a null value")
        if field["status"] in {"known", "not_applicable"} and not field["evidence"]:
            raise ValueError("A proposed value requires exact source evidence")
        for evidence in field["evidence"]:
            if evidence["source_version"] not in sources or evidence["quote"] not in locate(
                sources[evidence["source_version"]], evidence["locator"]
            ):
                raise ValueError("Fabricated or stale field evidence")
        if field["vocabulary_version"] != vocab["version"]:
            raise ValueError("Unknown vocabulary version")
        if field["vocabulary_id"] and not any(
            t["id"] == field["vocabulary_id"] and t["label"] == field["value"]
            for t in vocab["terms"].get(field["field"], {}).values()
        ):
            raise ValueError("Unknown ontology ID or mismatched term")


def check_proposal(db: sqlite3.Connection, proposal: dict[str, Any]) -> None:
    data = proposal["data"]
    bundle = head(db, data["dataset_id"])
    row = store.current(db, data["dataset_id"])
    if (
        bundle["version"] != data["source_bundle"]
        or data["metadata_version"] != fingerprint(loads(row["metadata"]))
        or row["manifest"] != data["manifest_version"]
    ):
        raise ValueError("Stale metadata proposal/source/manifest")
    if data["source_hashes"] != sorted(s["data"]["sha256"] for s in bundle["data"]["sources"]):
        raise ValueError("Source digests do not match the current bundle")
    verify_fields(bundle, data["fields"])
    expected = {
        f["field"]: f["value"] for f in data["fields"] if f["status"] in {"known", "not_applicable"}
    }
    if expected != data["changes"]:
        raise ValueError("Changes differ from evidence-validated fields")
    if any(f["original"] != loads(row["metadata"]).get(f["field"]) for f in data["fields"]):
        raise ValueError("Original values differ from canonical metadata")


def propose(
    directory: Path,
    dataset: str,
    config: dict[str, Any] | None = None,
    fields_file: Path | None = None,
    *,
    scope: str = "results",
) -> dict[str, Any]:
    with contextlib.closing(store.connect(directory, scope=scope)) as db:
        row = store.current(db, dataset)
        bundle = head(db, dataset)
        restriction = max(
            (bundle["data"]["classification"], store.classification(db, dataset)),
            key=CLASSIFICATIONS.__getitem__,
        )
    fields = extract(bundle, loads(row["metadata"]))
    invocation = None
    if fields_file:
        fields = load(fields_file)["fields"]
    elif config:
        invocation = providers.invoke(
            directory,
            config,
            task="metadata",
            data={"sources": bundle["data"]["sources"], "extracted": fields},
            classification=restriction,
            source_hashes=sorted(s["data"]["sha256"] for s in bundle["data"]["sources"]),
            specification=schema("metadata-response-v1"),
            mock={"fields": fields},
            dataset=dataset,
        )
        proposed = invocation["data"]["response"]["fields"]
        # An LLM cannot override an authoritative deterministic value or conceal a conflict.
        authoritative = {f["field"]: f for f in fields if f["status"] in {"known", "conflicting"}}
        fields = list({**{f["field"]: f for f in proposed}, **authoritative}.values())
    verify_fields(bundle, fields)
    evidence = [
        {
            "location": s["data"]["location"],
            "worker": s["data"]["worker"],
            "sha256": s["data"]["sha256"],
            "locator": "$",
        }
        for s in bundle["data"]["sources"]
    ]
    data = {
        "dataset_id": dataset,
        "manifest_version": row["manifest"],
        "metadata_version": fingerprint(loads(row["metadata"])),
        "source_bundle": bundle["version"],
        "source_hashes": sorted(s["data"]["sha256"] for s in bundle["data"]["sources"]),
        "fields": fields,
        "changes": {
            f["field"]: f["value"] for f in fields if f["status"] in {"known", "not_applicable"}
        },
        "evidence": evidence,
        "reason": "Evidence-backed metadata proposal; field confidence is "
        "self-reported, not calibrated",
        "invocation": invocation["version"] if invocation else None,
    }
    proposal = record("proposal", data, identity("proposal", fingerprint(data)), version=2)
    return review.submit(directory, proposal, scope=scope)


def apply(
    directory: Path, dataset: str, target: str, output: Path, *, scope: str = "results"
) -> dict[str, Any]:
    """Publish a new immutable metadata/input revision; never rewrite a campaign or raw source."""
    with store.write(directory, scope=scope) as db:
        proposal = store.get(db, "proposal", target)
        if proposal["schema_version"] != 2 or proposal["data"]["dataset_id"] != dataset:
            raise ValueError("Apply requires this dataset's version 2 proposal")
        decision = review.latest(db, dataset, "metadata")
        if (
            not decision
            or decision["data"]["action"] != "approve"
            or decision["data"]["target"] != target
        ):
            raise ValueError(
                "An explicit current human approval of this exact proposal is required"
            )
        prior = db.execute(
            f"SELECT canonical_revision FROM {store.heads_table(db)} WHERE dataset=?", (dataset,)
        ).fetchone()
        if prior and prior[0]:
            previous = store.get(db, "metadata_revision", prior[0])
            if previous["data"]["decision"] == decision["version"]:
                if review.effective(db, dataset, "metadata") != "APPROVED":
                    raise ValueError("Applied metadata approval became stale")
                dump(output, previous, immutable=True)
                return previous
        check_proposal(db, proposal)
        if decision["data"]["bindings"] != review.bindings(db, dataset, "metadata"):
            raise ValueError("Approval became stale")
        row = store.current(db, dataset)
        metadata = {**loads(row["metadata"]), **proposal["data"]["changes"]}
        manifest = store.get(db, "manifest", row["manifest"])
        # Compiled input metadata references existing scientific inputs verbatim. It is not
        # executable
        # until the user supplies a new campaign; control/reference proposals are never substituted.
        compiled = {
            "schema_version": 1,
            "kind": "curated-inputs",
            "dataset_id": dataset,
            "parent_manifest": manifest["version"],
            "assay": manifest["data"]["assay"],
            "reference": manifest["data"]["reference"],
            "lanes": manifest["data"]["lanes"],
            "parameters": manifest["data"]["run"]["parameters"],
            "original_metadata": manifest["data"]["metadata"],
            "metadata": metadata,
            "requires_new_campaign": True,
        }
        data = {
            "dataset_id": dataset,
            "manifest_version": row["manifest"],
            "proposal": target,
            "decision": decision["version"],
            "parent_metadata": fingerprint(loads(row["metadata"])),
            "metadata": metadata,
            "metadata_version": fingerprint(metadata),
            "source_bundle": proposal["data"]["source_bundle"],
            "compiled_inputs": compiled,
            "created": now(),
        }
        revision = record(
            "metadata_revision", data, identity("metadata-revision", fingerprint(data)), version=2
        )
        # Atomic file publication precedes DB commit. A crash may leave an orphan immutable export;
        # the registry is authoritative and reapplying must use a fresh output path if content
        # differs.
        dump(output, revision, immutable=True)
        store.put(db, revision, dataset)
        store.update_metadata(db, dataset, metadata)
        db.execute(
            f"UPDATE {store.heads_table(db)} SET canonical_revision=? WHERE dataset=?",
            (revision["version"], dataset),
        )
    return revision
