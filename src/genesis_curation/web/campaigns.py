"""Checkpointed full-workbook curation and recorded discovery campaigns."""

from __future__ import annotations

import contextlib
import fcntl
import json
import re
import sqlite3
import threading
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..contracts.records import canonical, fingerprint, loads, now
from ..llm import progress, providers
from ..registry import store
from . import (
    catalog,
    constraints,
    intake,
    mining,
    mining_sources,
    preparation,
    research_search,
    runtime,
    storage,
    studies,
    workspace,
)
from .discovery_catalog import ASSAYS, VERSION


def create(root: Path, config: dict[str, Any], body: dict[str, Any], actor: str) -> dict[str, Any]:
    with (root / "campaign-create.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _create(root, config, body, actor)


def _create(root: Path, config: dict[str, Any], body: dict[str, Any], actor: str) -> dict[str, Any]:
    storage.require_space(root)
    mode = body.get("mode", "curate")
    if mode not in {"curate", "discover"}:
        raise ValueError("Unknown campaign mode")
    key = str(uuid.uuid4())
    campaign: dict[str, Any] = {
        "id": key,
        "mode": mode,
        "actor": actor,
        "created": now(),
        "provider": config["id"],
        "provider_hash": fingerprint(config),
        "recipe_version": VERSION,
        "group": None,
        "query_index": 0,
        "queries": [],
        "usage": {},
        "ledger": [],
        "planned": False,
        "goal": str(body.get("goal", "Workbook re-check"))[:4000],
        "constraints": constraints.validate(body.get("constraints", {})),
    }
    if mode == "discover":
        species, assays = body.get("species", []), body.get("assays", [])
        if (
            not species
            or len(species) > 500
            or any(not mining_sources.valid_species_name(s) for s in species)
        ):
            raise ValueError("Choose plant taxonomy names")
        if not assays or any(a not in ASSAYS for a in assays):
            raise ValueError("Choose supported data types")
        campaign["queries"] = research_search.recipes(
            species, assays, campaign["goal"], campaign["constraints"]
        )
    scope = workspace.selection(root, body, internal=True) if mode == "curate" else None
    selection = scope["ids"] if scope else []
    if scope:
        if body.get("scope_token") and body["scope_token"] != scope["scope_token"]:
            raise ValueError("Stale selection; preview the affected studies again")
        if scope["records"] > 200 and body.get("scope_token") != scope["scope_token"]:
            raise ValueError("Preview and confirm this selection before starting")
        campaign["scope_token"] = scope["scope_token"]
        campaign["scope"] = {k: scope[k] for k in ("records", "studies")}
    campaign["request_key"] = fingerprint(
        {
            "mode": mode,
            "provider": config["id"],
            "scope": scope["scope_token"] if scope else None,
            "goal": campaign["goal"],
            "species": body.get("species"),
            "assays": body.get("assays"),
            "constraints": campaign["constraints"],
        }
    )
    with store.write(root / "catalog") as db:
        existing = db.execute(
            "SELECT body,status FROM workspace_campaigns WHERE status IN ('QUEUED','RUNNING') "
            "AND json_extract(body,'$.request_key')=?",
            (campaign["request_key"],),
        ).fetchone()
        if existing:
            return loads(existing[0]) | {"status": existing[1]}
        db.execute(
            "INSERT INTO workspace_campaigns VALUES(?,?,?)", (key, "QUEUED", canonical(campaign))
        )
        if mode == "curate":
            placeholders = "SELECT value FROM json_each(?)"
            clause = (
                "id IN (" + placeholders + ") OR (study!='' AND study IN "
                "(SELECT study FROM workspace_rows WHERE id IN (" + placeholders + ")))"
                if selection
                else "1"
            )
            for r in db.execute(
                "SELECT entity,min(id) AS representative FROM workspace_rows WHERE "
                + clause
                + " GROUP BY entity",
                (canonical(selection), canonical(selection)),
            ).fetchall():
                db.execute(
                    "INSERT INTO workspace_items VALUES(?,?,?,?)",
                    (key, r["entity"], "QUEUED", canonical({"row": r["representative"]})),
                )
        workspace.event(
            db, key, "", "campaign-created", mode=mode, provider=config["id"], goal=campaign["goal"]
        )
    try:
        job = runtime.enqueue(
            root,
            "workspace",
            actor,
            {"campaign": key, "provider": config["id"], "interactive": mode == "curate"},
        )
    except ValueError as error:
        save(root, campaign, "FAILED")
        with store.write(root / "catalog") as db:
            workspace.event(db, key, "", "queue-failed", error=str(error))
        raise
    campaign["job"] = job
    save(root, campaign, "RUNNING")
    return campaign | {"status": "RUNNING"}


def save(root: Path, campaign: dict[str, Any], status: str | None = None) -> None:
    with store.write(root / "catalog") as db:
        if status:
            db.execute(
                "UPDATE workspace_campaigns SET status=?,body=? WHERE id=?",
                (status, canonical(campaign), campaign["id"]),
            )
        else:
            db.execute(
                "UPDATE workspace_campaigns SET body=? WHERE id=?",
                (canonical(campaign), campaign["id"]),
            )


def control(root: Path, key: str, action: str, actor: str) -> dict[str, Any]:
    with store.write(root / "catalog") as db:
        row = db.execute(
            "SELECT body,status FROM workspace_campaigns WHERE id=?", (key,)
        ).fetchone()
        if not row:
            raise ValueError("Unknown campaign")
        campaign = loads(row[0])
        if action in {"archive", "restore"}:
            if row[1] in {"RUNNING", "QUEUED"}:
                raise ValueError("Pause this activity before archiving")
            if campaign.get("job") and runtime.job(root, campaign["job"])["status"] in {
                "RUNNING",
                "QUEUED",
            }:
                raise ValueError("Wait for the current step to finish before archiving")
            campaign["archived"] = action == "archive"
            db.execute(
                "UPDATE workspace_campaigns SET body=? WHERE id=?", (canonical(campaign), key)
            )
            workspace.event(db, key, "", action, actor=actor)
            return campaign | {"status": row[1]}
        if campaign.get("archived"):
            raise ValueError("Restore archived activity before resuming")
        if action == "pause":
            db.execute("UPDATE workspace_campaigns SET status='PAUSED' WHERE id=?", (key,))
        elif action != "resume":
            raise ValueError("Choose pause or resume")
        workspace.event(db, key, "", action, actor=actor)
    if action == "pause" and runtime.job(root, campaign["job"])["status"] == "QUEUED":
        runtime.cancel(root, campaign["job"])
    if action == "resume":
        if row[1] == "RUNNING":
            return campaign | {"status": "RUNNING"}
        existing = runtime.job(root, campaign["job"])
        if existing["status"] in {"QUEUED", "RUNNING"}:
            raise ValueError("Wait for the current batch to pause")
        job = runtime.enqueue(
            root,
            "workspace",
            actor,
            {
                "campaign": key,
                "provider": campaign["provider"],
                "interactive": campaign["mode"] == "curate",
            },
        )
        campaign["job"] = job
        save(root, campaign, "RUNNING")
    return campaign | {"status": "PAUSED" if action == "pause" else "RUNNING"}


def recover(root: Path) -> None:
    """Expose interrupted campaigns as resumable without losing checkpoints."""
    with store.write(root / "catalog") as db:
        rows = db.execute("SELECT body FROM workspace_campaigns WHERE status='RUNNING'").fetchall()
        for row in rows:
            campaign = loads(row[0])
            if runtime.job(root, campaign["job"])["status"] not in {"QUEUED", "RUNNING"}:
                db.execute(
                    "UPDATE workspace_campaigns SET status='PAUSED' WHERE id=?",
                    (campaign["id"],),
                )
                workspace.event(db, campaign["id"], "", "worker-interrupted")


def run_search(root: Path, query: dict[str, Any], cancel: threading.Event | None) -> dict[str, Any]:
    if query["source"] == "ena":
        result = mining_sources.search(
            root,
            {
                "source": "ena",
                "species": query["species"],
                "assay": query["assay"],
                "terms": query.get("terms", []),
                "offset": query.get("offset", 0),
            },
            cancel,
        )
        return {
            "source": "ena",
            "query": result["query"],
            "rows": result["candidates"],
            "next_offset": result["next_offset"],
            "coverage": result.get("coverage", {}),
            "sources": [
                {"url": s["url"], "hash": fingerprint(s["text"]), "retrieved_at": s["retrieved_at"]}
                for s in result["sources"]
            ],
        }
    return research_search.search(
        root, query["source"], query["query"], cancel, query.get("offset", 0)
    )


def discover_step(root: Path, campaign: dict[str, Any], cancel: threading.Event | None) -> None:
    index = campaign["query_index"]
    if index >= len(campaign["queries"]):
        save(root, campaign, "COMPLETED")
        return
    query = campaign["queries"][index]
    progress.report(
        phase="Searching",
        source=query["source"],
        query=query["query"][:240],
        completed=index,
        total=len(campaign["queries"]),
    )
    taxonomy = campaign.setdefault("taxonomy", {})
    name = query["species"]
    if name not in taxonomy:
        progress.report(phase="Checking species", species=name)
        try:
            taxonomy[name] = {"taxon": mining_sources.taxon(root, name, cancel)}
        except (ValueError, OSError) as error:
            taxonomy[name] = {"error": str(error)[:300]}
            with store.write(root / "catalog") as db:
                workspace.event(
                    db,
                    campaign["id"],
                    "",
                    "species-needs-review",
                    species=name,
                    error=str(error)[:300],
                )
    if "error" in taxonomy[name]:
        skipped = 0
        while (
            campaign["query_index"] < len(campaign["queries"])
            and campaign["queries"][campaign["query_index"]]["species"] == name
        ):
            campaign["query_index"] += 1
            skipped += 1
        campaign["skipped_queries"] = campaign.get("skipped_queries", 0) + skipped
        save(root, campaign)
        return
    progress.report(phase="Searching", source=query["source"])
    with store.write(root / "catalog") as db:
        workspace.event(db, campaign["id"], "", "search-started", query=query)
    try:
        result = run_search(root, query, cancel)
        with store.write(root / "catalog") as db:
            receipt = workspace.event(
                db,
                campaign["id"],
                "",
                "search-partial" if result.get("errors") else "search-completed",
                result=result,
                error="; ".join(str(e) for e in result.get("errors", []))[:500],
            )
            for i, hit in enumerate(result["rows"]):
                study = research_search.accession(canonical(hit))
                values = {
                    **hit,
                    "study_accession": study,
                    "species": query["species"],
                    "search_assay": query["assay"],
                    "search_query": query["query"],
                    "search_event": receipt,
                    "_constraints": campaign["constraints"],
                }
                workspace.add_row(
                    db,
                    campaign["id"],
                    query["source"],
                    index * 100 + i,
                    values,
                    {},
                    ASSAYS[query["assay"]]["modality"],
                    hit.get("url", ""),
                )
        # Persist bounded pagination; the UI keeps the unvisited cursor visible.
        if result.get("next_offset") is not None and query.get("offset", 0) < 50:
            campaign["queries"].append({**query, "offset": result["next_offset"]})
    except (ValueError, OSError) as exc:
        with store.write(root / "catalog") as db:
            workspace.event(
                db, campaign["id"], "", "search-failed", query=query, error=str(exc)[:700]
            )
    campaign["query_index"] += 1
    save(root, campaign)


def materialize(
    root: Path, row: sqlite_row, campaign: dict[str, Any], cancel: threading.Event | None
) -> str | None:
    progress.report(phase="Retrieving sources", record=row["entity"], study=row["study"])
    registry = root / "catalog"
    raw = loads(row["body"])
    values = raw["values"]
    entity = row["entity"]
    if (
        raw["origin"] == "discovery"
        and not re.fullmatch(r"(?:[SED]RX|CRX)\d+", str(values.get("experiment_accession", "")))
        and not re.fullmatch(
            r"(?:GSE|[SED]RP|PRJ[EDN][AB])\d+", str(values.get("study_accession", ""))
        )
    ):
        with store.write(registry) as db:
            db.execute(
                "UPDATE workspace_items SET status='SKIPPED' WHERE campaign=? AND entity=?",
                (campaign["id"], entity),
            )
            db.execute("UPDATE workspace_rows SET status='Needs source' WHERE entity=?", (entity,))
            workspace.event(
                db,
                campaign["id"],
                entity,
                "source-needed",
                reason="Resolve a public study or experiment accession before sample annotation",
            )
        return None
    if row["subject"]:
        with store.write(registry) as db:
            linked = db.execute(
                "UPDATE workspace_rows SET subject=? WHERE entity=? AND "
                "(subject IS NULL OR subject='')",
                (row["subject"], entity),
            )
            if linked.rowcount:
                workspace.event(
                    db,
                    campaign["id"],
                    entity,
                    "entity-linked",
                    subject=row["subject"],
                    records=linked.rowcount,
                )
                db.execute(
                    "UPDATE curation_queue_owners SET dirty=1 WHERE owner IN "
                    "(SELECT study FROM curation_members WHERE subject=?)",
                    (row["subject"],),
                )
        return row["subject"]
    if (
        raw["origin"] == "discovery"
        and not re.fullmatch(r"(?:[SED]RX|CRX)\d+", str(values.get("experiment_accession", "")))
        and re.fullmatch(r"(?:GSE|[SED]RP|PRJ[EDN][AB])\d+", str(values.get("study_accession", "")))
    ):
        expand_study(root, row, campaign, cancel)
        return None
    sid = catalog.import_rows(
        registry,
        canonical([{"record_id": entity}]),
        "reference-identity.json",
        "reference-identity",
        "record_id",
        "public",
        campaign["actor"],
    )[0]
    current = catalog.get(registry, sid)
    if not current.get("reference_origin"):
        with store.write(registry) as db:
            state = catalog.read(db, sid)
            state["reference_origin"] = raw["location"]
            state["original"] = {**values, "_origin": raw["origin"], "_modality": row["modality"]}
            catalog.save(
                db,
                state,
                "source",
                campaign["actor"],
                {"row": row["id"], "original": values},
            )
        intake.attach(
            registry,
            sid,
            canonical({"external_reference_claims": values}),
            raw["location"],
            "study_context",
            campaign["actor"],
        )
    with store.write(registry) as db:
        db.execute(
            "UPDATE workspace_rows SET subject=?,status='Checking' WHERE entity=?", (sid, entity)
        )
    if current.get("preparation_provenance") or current["reviews"]:
        return sid
    accession = str(values.get("experiment_accession") or values.get("study_accession") or "")
    if not accession:
        with store.write(registry) as db:
            workspace.event(
                db,
                campaign["id"],
                entity,
                "source-unresolved",
                reason="No exact public accession; imported claims require review",
            )
        return sid
    with store.write(registry) as db:
        workspace.event(
            db,
            campaign["id"],
            entity,
            "replay-lookup-started",
            accession=accession,
            mode="accession-seeded replay",
            original=raw["location"],
        )
    try:
        if re.fullmatch(r"[SED]RX\d+", accession):
            url = "https://www.ebi.ac.uk/ena/portal/api/filereport?" + urlencode(
                {
                    "accession": accession,
                    "result": "read_run",
                    "format": "json",
                    "fields": (
                        "run_accession,sample_accession,study_accession,experiment_accessi"
                        "on,scientific_name,tax_id,library_strategy,library_layout,sample_"
                        "title"
                    ),
                }
            )
            report = intake.fetch(root, url, cancel)
            runs = json.loads(report["text"])
            if not runs or any(r.get("experiment_accession") != accession for r in runs):
                raise ValueError("Archive did not return the requested experiment")
            intake.attach(registry, sid, report["text"], report["url"], "sample", campaign["actor"])
            # Keep the raw response and expose exact archive key/value pairs to the
            # deterministic extractor, as native accession intake already does.
            attributes = "\n".join(
                f"{name}: {value}"
                for run in runs
                for name, value in run.items()
                if isinstance(value, (str, int)) and "\n" not in str(value)
            )
            intake.attach(registry, sid, attributes, report["url"], "sample", campaign["actor"])
            for acc in [accession] + sorted(
                {r["sample_accession"] for r in runs if r.get("sample_accession")}
            ):
                source = (
                    intake.fetch(root, "https://www.ebi.ac.uk/ena/browser/api/xml/" + acc, cancel)
                    if re.fullmatch(r"[SED]RS\d+", acc)
                    else intake.archive_xml(root, acc)
                )
                intake.attach(
                    registry, sid, source["text"], source["url"], "sample", campaign["actor"]
                )
                tree = ET.fromstring(source["text"])
                sample_attributes = [
                    n.findtext("TAG", "").replace(" ", "_") + ": " + n.findtext("VALUE", "")
                    for n in tree.findall(".//SAMPLE_ATTRIBUTE")
                ]
                if sample_attributes:
                    intake.attach(
                        registry,
                        sid,
                        "\n".join(sample_attributes),
                        source["url"],
                        "sample",
                        campaign["actor"],
                    )
        elif accession.startswith(("CRA", "CRX", "CRR")):
            source = intake.fetch(root, "https://ngdc.cncb.ac.cn/gsa/browse/" + accession, cancel)
            if accession not in source["text"] or "正在进行安全检查" in source["text"]:
                raise ValueError("GSA metadata unavailable or access challenge")
            intake.attach(
                registry, sid, source["text"], source["url"], "study_context", campaign["actor"]
            )
        else:
            source = (
                intake.fetch(root, intake.geo_url(accession), cancel)
                if accession.startswith("GSE")
                else intake.fetch(
                    root, "https://www.ebi.ac.uk/ena/browser/api/xml/" + accession, cancel
                )
            )
            intake.attach(
                registry, sid, source["text"], source["url"], "study_context", campaign["actor"]
            )
        state = catalog.get(registry, sid)
        with store.write(registry) as db:
            workspace.event(
                db,
                campaign["id"],
                entity,
                "replay-lookup-completed",
                accession=accession,
                sources=[
                    {"url": s["data"]["location"], "hash": s["version"]} for s in state["sources"]
                ],
            )
    except (ValueError, OSError, ET.ParseError) as exc:
        with store.write(registry) as db:
            workspace.event(
                db,
                campaign["id"],
                entity,
                "replay-lookup-failed",
                accession=accession,
                error=str(exc)[:700],
            )
    return sid


# Row is converted to a plain dictionary before leaving its read transaction.
sqlite_row = dict[str, Any]


def step(
    root: Path, config_path: Path, task: dict[str, Any], cancel: threading.Event | None
) -> dict[str, Any]:
    registry = root / "catalog"
    with contextlib.closing(store.connect(registry)) as db:
        record = db.execute(
            "SELECT body,status FROM workspace_campaigns WHERE id=?", (task["payload"]["campaign"],)
        ).fetchone()
    if not record:
        raise ValueError("Unknown campaign")
    campaign = loads(record[0])
    if record[1] != "RUNNING" or campaign["job"] != task["id"]:
        return {"campaign": campaign["id"], "pending": False}
    if cancel and cancel.is_set():
        save(root, campaign, "PAUSED")
        return {"campaign": campaign["id"], "pending": False}
    if storage.status(root)["blocked"]:
        save(root, campaign, "PAUSED")
        with store.write(registry) as db:
            workspace.event(
                db,
                campaign["id"],
                "",
                "storage-pause",
                reason=(
                    "Less than 2 GiB free; completed work preserved. "
                    "Resume manually after freeing space."
                ),
            )
        return {"campaign": campaign["id"], "pending": False}
    config = providers.select(config_path, campaign["provider"])
    if fingerprint(config) != campaign["provider_hash"]:
        raise ValueError("Provider changed; start a new campaign")
    if campaign["mode"] == "discover" and not campaign.get("planned"):
        # Start one real archive query immediately, then let the LLM expand the saved plan.
        if campaign["query_index"] == 0:
            discover_step(root, campaign, cancel)
        else:
            response = mining.invoke(
                root,
                campaign,
                config,
                {
                    "goal": campaign["goal"],
                    "task": (
                        "Extract only explicitly requested tissue, condition and stage phr"
                        "ases; do not invent restrictions."
                    ),
                },
                mining.PLAN_SCHEMA,
                {"terms": [], "summary": campaign["goal"]},
                cancel,
            )
            terms = response.get("terms", [])
            if terms:
                campaign["queries"].extend(
                    [
                        {
                            **q,
                            "query": q["query"]
                            + " "
                            + " ".join('"' + str(t).replace('"', " ") + '"' for t in terms),
                        }
                        for q in campaign["queries"]
                        if q["source"] in {"web", "papers"}
                    ]
                )
            campaign["planned"] = True
            with store.write(registry) as db:
                workspace.event(
                    db,
                    campaign["id"],
                    "",
                    "llm-search-plan",
                    plan=response,
                    invocations=campaign["ledger"],
                    usage=campaign["usage"],
                )
            save(root, campaign)
    elif campaign["mode"] == "discover":
        discover_step(root, campaign, cancel)
    elif not campaign["group"]:
        with contextlib.closing(store.connect(registry)) as db:
            pending = db.execute(
                (
                    "SELECT i.entity,r.* FROM workspace_items i JOIN workspace_rows r "
                    "ON r.id=json_extract(i.body,'$.row') WHERE i.campaign=? AND i.sta"
                    "tus='QUEUED' ORDER BY CASE WHEN r.study='' THEN 1 ELSE 0 END,"
                    "r.study,i.entity LIMIT 1"
                ),
                (campaign["id"],),
            ).fetchall()
        if not pending:
            save(root, campaign, "COMPLETED")
            with contextlib.closing(store.connect(registry)) as db:
                counts = dict(
                    db.execute(
                        "SELECT status,count(*) FROM workspace_items "
                        "WHERE campaign=? GROUP BY status",
                        (campaign["id"],),
                    )
                )
            return {
                "campaign": campaign["id"],
                "pending": False,
                "prepared": counts.get("COMPLETED", 0),
                "failed": counts.get("FAILED", 0),
                "needs_source": counts.get("SKIPPED", 0),
                "expanded": counts.get("EXPANDED", 0),
            }
        first = pending[0]
        with contextlib.closing(store.connect(registry)) as db:
            selected = [
                dict(r)
                for r in db.execute(
                    "SELECT r.* FROM workspace_items i JOIN workspace_rows r ON "
                    "r.id=json_extract(i.body,'$.row') "
                    "WHERE i.campaign=? AND i.status='QUEUED' AND "
                    + ("r.study=?" if first["study"] else "r.entity=?")
                    + " ORDER BY i.entity",
                    (campaign["id"], first["study"] or first["entity"]),
                )
            ]
        replayed = campaign.setdefault("replayed_studies", [])
        if first["study"] and first["study"] not in replayed:
            try:
                receipt = research_search.search(root, "sra", first["study"], cancel)
                with store.write(registry) as db:
                    workspace.event(
                        db,
                        campaign["id"],
                        first["entity"],
                        "replay-search-completed",
                        mode="accession-seeded replay",
                        result=receipt,
                    )
            except (ValueError, OSError) as exc:
                with store.write(registry) as db:
                    workspace.event(
                        db,
                        campaign["id"],
                        first["entity"],
                        "replay-search-failed",
                        query=first["study"],
                        error=str(exc)[:700],
                    )
            replayed.append(first["study"])
        # One source retrieval per worker turn keeps pause and interactive work responsive.
        campaign["group"] = {"rows": selected, "retrieved": [], "index": 0, "study": None}
        save(root, campaign)
    else:
        group = campaign["group"]
        if group["index"] < len(group["rows"]):
            row = group["rows"][group["index"]]
            try:
                sid = materialize(root, row, campaign, cancel)
                if sid:
                    group["retrieved"].append(sid)
                    group.setdefault("prepared_entities", []).append(row["entity"])
            except (ValueError, OSError) as exc:
                with store.write(registry) as db:
                    db.execute(
                        "UPDATE workspace_items SET status='FAILED' WHERE campaign=? AND entity=?",
                        (campaign["id"], row["entity"]),
                    )
                    db.execute(
                        "UPDATE workspace_rows SET status='Needs review' WHERE entity=?",
                        (row["entity"],),
                    )
                    workspace.event(
                        db, campaign["id"], row["entity"], "record-failed", error=str(exc)[:700]
                    )
            group["index"] += 1
            save(root, campaign)
        elif not group["retrieved"]:
            campaign["group"] = None
            save(root, campaign)
        else:
            if not group["study"]:
                group["study"] = studies.link(
                    registry,
                    group["retrieved"],
                    group["rows"][0]["study"] or "Unassigned study: " + group["rows"][0]["entity"],
                    campaign["actor"],
                    accession=group["rows"][0]["study"] or None,
                    group=group["rows"][0]["entity"],
                )
                save(root, campaign)
            result = preparation.run(
                root,
                config_path,
                {
                    **task,
                    "payload": {
                        "study": group["study"],
                        "provider": config["id"],
                        "provider_hash": fingerprint(config),
                        "constraints": campaign.get("constraints", {}),
                    },
                },
                cancel,
            )
            with store.write(registry) as db:
                workspace.event(
                    db, campaign["id"], "", "curation-batch", study=group["study"], result=result
                )
            if not result.get("pending"):
                prepared = studies.get(registry, group["study"])["preparation"]
                failed_subjects = set(prepared.get("failures", {}))
                with store.write(registry) as db:
                    for failed in failed_subjects:
                        db.execute(
                            "UPDATE workspace_items SET status='FAILED' WHERE campaign=? "
                            "AND entity IN (SELECT entity FROM workspace_rows WHERE subject=?)",
                            (campaign["id"], failed),
                        )
                    for row in group["rows"]:
                        if row["entity"] not in group.get("prepared_entities", []):
                            continue
                        db.execute(
                            (
                                "UPDATE workspace_items SET status='COMPLETED' WHERE campaign=? AN"
                                "D entity=? AND status='QUEUED'"
                            ),
                            (campaign["id"], row["entity"]),
                        )
                        db.execute(
                            (
                                "UPDATE workspace_rows SET status='Needs review' WHERE entity=? AN"
                                "D status='Checking'"
                            ),
                            (row["entity"],),
                        )
                campaign["group"] = None
                save(root, campaign)
    with contextlib.closing(store.connect(registry)) as db:
        status = db.execute(
            "SELECT status FROM workspace_campaigns WHERE id=?", (campaign["id"],)
        ).fetchone()[0]
    return {"campaign": campaign["id"], "pending": status == "RUNNING"}


def expand_study(
    root: Path, row: dict[str, Any], campaign: dict[str, Any], cancel: threading.Event | None
) -> None:
    """Expand study leads into actual sample records, ten per checkpoint."""
    registry = root / "catalog"
    values = loads(row["body"])["values"]
    accession = values["study_accession"]
    with contextlib.closing(store.connect(registry)) as db:
        item = db.execute(
            "SELECT body FROM workspace_items WHERE campaign=? AND entity=?",
            (campaign["id"], row["entity"]),
        ).fetchone()
    progress = loads(item[0])
    expansion = campaign.setdefault("study_expansions", {}).get(accession, {})
    if expansion.get("complete"):
        with store.write(registry) as db:
            expanded_parent(db, campaign["id"], row, expansion["study"])
            workspace.event(
                db,
                campaign["id"],
                row["entity"],
                "study-expansion-reused",
                study=expansion["study"],
            )
        return
    offset = expansion.get("offset", progress.get("offset", 0))
    key = intake.import_accession(
        root, accession, campaign["actor"], cancel, sample_limit=10, sample_offset=offset
    )
    study = studies.get(registry, key)
    selection = study["preparation"]["selection"]
    with store.write(registry) as db:
        selected_subjects = set(selection["subjects"])
        for state in study["records"]:
            if state["id"] not in selected_subjects:
                continue
            number = int(fingerprint(state["id"])[:12], 16)
            location = next(
                (
                    s["data"]["location"]
                    for s in state["sources"]
                    if s["data"]["location"].startswith("https:")
                ),
                "https://www.ncbi.nlm.nih.gov/sra/" + accession,
            )
            existing = db.execute(
                "SELECT id FROM workspace_rows WHERE subject=? LIMIT 1", (state["id"],)
            ).fetchone()
            if existing:
                child = existing[0]
            else:
                workspace.add_row(
                    db,
                    campaign["id"],
                    accession,
                    number,
                    state["original"],
                    {},
                    row["modality"],
                    location,
                )
                child = fingerprint([campaign["id"], accession, number])
            db.execute(
                "UPDATE workspace_rows SET subject=?,status='Checking' WHERE id=?",
                (state["id"], child),
            )
            entity = db.execute(
                "SELECT entity FROM workspace_rows WHERE id=?", (child,)
            ).fetchone()[0]
            state["reference_origin"] = location
            state["original"]["_modality"] = row["modality"]
            state["original"]["_constraints"] = values.get("_constraints", {})
            catalog.save(db, state, "source", campaign["actor"], {"parent": row["id"]})
            db.execute(
                "INSERT OR IGNORE INTO workspace_items VALUES(?,?,?,?)",
                (campaign["id"], entity, "QUEUED", canonical({"row": child})),
            )
        more = offset + 10 < selection["available_records"]
        campaign["study_expansions"][accession] = {
            "offset": offset + 10,
            "study": key,
            "complete": not more,
        }
        db.execute(
            "UPDATE workspace_items SET status=?,body=? WHERE campaign=? AND entity=?",
            (
                "QUEUED" if more else "EXPANDED",
                canonical({**progress, "offset": offset + 10}),
                campaign["id"],
                row["entity"],
            ),
        )
        db.execute(
            "UPDATE workspace_rows SET status=? WHERE id=?",
            ("Checking" if more else "Expanded", row["id"]),
        )
        if not more:
            expanded_parent(db, campaign["id"], row, key)
        workspace.event(
            db,
            campaign["id"],
            row["entity"],
            "study-expanded",
            accession=accession,
            offset=offset,
            selection=selection,
        )


def expanded_parent(db: sqlite3.Connection, campaign: str, row: dict[str, Any], study: str) -> None:
    """An expanded search lead is lineage, not an extra sample needing annotation."""
    db.execute(
        "UPDATE workspace_rows SET status='Expanded',body=json_set(body,'$.expanded_study',?) "
        "WHERE id=?",
        (study, row["id"]),
    )
    db.execute(
        "UPDATE workspace_items SET status='EXPANDED' WHERE campaign=? AND entity=?",
        (campaign, row["entity"]),
    )
