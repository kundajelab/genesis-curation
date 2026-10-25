"""Version-checked study APIs using the existing authenticated browser boundary."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response

from ..contracts.records import canonical, fingerprint, loads
from ..llm import providers
from ..registry import store
from . import audit, catalog, intake, review_queue, runtime, skills, studies


def provider(
    root: Path, config_path: Path, key: str | None = None, requested: str | None = None
) -> dict[str, Any] | None:
    if requested == "":
        return None
    configs = providers.configurations(config_path) if config_path.exists() else []
    classifications = (
        {s["classification"] for s in studies.get(root / "catalog", key)["records"]}
        if key
        else {"public"}
    )
    eligible = [
        c
        for c in configs
        if c["mode"] != "disabled"
        and classifications.issubset(c["allowed_classifications"])
        and not ("local-only" in classifications and c.get("external"))
    ]
    if requested:
        match = next((c for c in eligible if c["id"] == requested), None)
        if not match:
            raise ValueError("Selected provider is not permitted for this study")
        return match
    return next((c for c in eligible if not c.get("external")), None)


def queue(
    root: Path, config_path: Path, key: str, actor: str, body: dict[str, Any]
) -> dict[str, str]:
    config = provider(root, config_path, key, body.get("provider"))
    with store.write(root / "catalog") as db:
        study, rows = studies.read(db, key), studies.members(db, key)
        if body.get("version") != studies.revision(study, rows):
            raise ValueError("Stale study: reload before preparation")
        if study["preparation"]["status"] in {"QUEUED", "RUNNING"}:
            raise ValueError("Study preparation is already active")
        payload: dict[str, Any] = {
            "study": key,
            "provider": config["id"] if config else "",
            "provider_hash": fingerprint(config),
            "skill_catalog_hash": fingerprint(skills.available(root / "skills")),
            "versions": {s["id"]: s["version"] for s in rows},
        }
        if body.get("task"):
            if study["preparation"]["status"] != "COMPLETED":
                raise ValueError("Complete full study preparation before targeted investigation")
            _, task = studies.find_task(db, key, body["task"], body["task_version"])
            if task["field"] != "__preparation":
                payload.update(field=task["field"], targets=[t["subject"] for t in task["targets"]])
            payload["guidance"] = str(body.get("guidance", ""))[:4000]
        if body.get("restart"):
            study["preparation"]["completed"] = {}
            payload["restart"] = True
        study["preparation"]["status"] = "QUEUED"
        kind = "study-prepare"
        if study.get("accession") and study["preparation"].get("retrieval_failures"):
            kind = "study-intake"
            payload["accession"] = study["accession"]
        job = runtime.enqueue(root, kind, actor, payload)
        study["preparation"]["job"] = job
        studies.save(db, study, "prepare-request", actor, {"job": job})
        return {"id": job, "study": key}


def mount(app: FastAPI, root: Path, config_path: Path) -> None:
    registry = root / "catalog"
    store.initialize(registry)
    studies.bootstrap(registry)

    @app.get("/api/review-queue")
    def queue_page(cursor: int = 0, limit: int = 25, status: str = "open") -> dict[str, Any]:
        return review_queue.listing(registry, cursor, limit, status)

    @app.get("/api/review-queue/{study}/{task}")
    def queue_task(study: str, task: str) -> dict[str, Any]:
        return review_queue.detail(registry, study, task)

    @app.get("/api/audits")
    def audits() -> list[dict[str, Any]]:
        return audit.listing(registry)

    @app.get("/api/audits/{key}")
    def audit_detail(key: str) -> dict[str, Any]:
        return audit.detail(registry, key)

    @app.get("/api/audits/{key}/report")
    def audit_report(key: str) -> dict[str, Any]:
        return audit.report(registry, key)

    @app.get("/api/audits/{key}/samples/{sid}")
    def audit_sample(key: str, sid: str, request: Request) -> dict[str, Any]:
        return audit.view(registry, key, sid, request.state.account["actor"])

    @app.post("/api/audits/{key}/samples/{sid}/draft")
    async def audit_draft(key: str, sid: str, request: Request) -> dict[str, bool]:
        audit.save_draft(registry, key, sid, request.state.account["actor"], await request.json())
        return {"saved": True}

    @app.post("/api/audits/{key}/samples/{sid}/answer")
    async def audit_answer(key: str, sid: str, request: Request) -> dict[str, Any]:
        return audit.save(registry, key, sid, request.state.account["actor"], await request.json())

    @app.get("/api/studies")
    def listing(cursor: str = "", limit: int = 50) -> dict[str, Any]:
        return studies.listing(registry, cursor, limit)

    @app.post("/api/studies")
    async def create(request: Request) -> dict[str, Any]:
        body, actor = await request.json(), request.state.account["actor"]
        if body.get("accession"):
            accession = str(body["accession"]).strip().upper()
            if not intake.ACCESSION.fullmatch(accession):
                raise ValueError("Enter a supported GEO/ENA/SRA study accession")
            config = provider(root, config_path, requested=body.get("provider"))
            key = runtime.enqueue(
                root,
                "study-intake",
                actor,
                {
                    "accession": accession,
                    "provider": config["id"] if config else "",
                    "provider_hash": fingerprint(config),
                    "skill_catalog_hash": fingerprint(skills.available(root / "skills")),
                },
            )
            return {"job": key}
        ids = catalog.import_rows(
            registry,
            body["text"],
            body["filename"],
            body["namespace"],
            body["id_column"],
            body["classification"],
            actor,
        )
        groups: dict[str, list[str]] = {}
        for sid in ids:
            state = catalog.get(registry, sid)
            group = str(state["original"].get("study_accession") or body["namespace"])
            groups.setdefault(group, []).append(sid)
        keys = []
        for name, subjects in groups.items():
            accession = name if intake.ACCESSION.fullmatch(name) else None
            key = studies.link(
                registry, subjects, name, actor, accession, body["namespace"] + ":" + name
            )
            keys.append(key)
            current = studies.get(registry, key)
            queue(
                root,
                config_path,
                key,
                actor,
                {"version": current["version"], "provider": body.get("provider")},
            )
        return {"studies": keys}

    @app.get("/api/studies/{key}")
    def detail(key: str) -> dict[str, Any]:
        return studies.get(registry, key)

    @app.get("/api/studies/{key}/progress")
    def progress(key: str) -> dict[str, Any]:
        return studies.progress(registry, key)

    @app.post("/api/studies/{key}/prepare")
    async def prepare(key: str, request: Request) -> dict[str, str]:
        return queue(root, config_path, key, request.state.account["actor"], await request.json())

    @app.get("/api/review-tasks")
    def tasks(study: str, status: str = "open", cursor: int = 0, limit: int = 50) -> dict[str, Any]:
        if cursor < 0 or not 1 <= limit <= 200 or status not in {"all", "open", "waiting", "done"}:
            raise ValueError("Invalid task page")
        rows = [
            t
            for t in studies.get(registry, study)["tasks"]
            if status == "all" or t["status"] == status
        ]
        return {
            "items": rows[cursor : cursor + limit],
            "total": len(rows),
            "next_cursor": cursor + limit if cursor + limit < len(rows) else None,
        }

    @app.get("/api/review-tasks/{key}/draft")
    def read_draft(key: str, request: Request) -> dict[str, Any]:
        return studies.draft(registry, request.state.account["actor"], key)

    @app.post("/api/review-tasks/{key}/draft")
    async def save_draft(key: str, request: Request) -> dict[str, Any]:
        return studies.draft(registry, request.state.account["actor"], key, await request.json())

    @app.post("/api/review-tasks/{key}/decision")
    async def decision(key: str, request: Request) -> dict[str, Any]:
        return studies.resolve(registry, key, await request.json(), request.state.account["actor"])

    @app.post("/api/review-tasks/{key}/investigate")
    async def investigate(key: str, request: Request) -> dict[str, str]:
        body = await request.json()
        study = studies.get(registry, body["study"])
        return queue(
            root,
            config_path,
            study["id"],
            request.state.account["actor"],
            {
                "version": study["version"],
                "task": key,
                "task_version": body["version"],
                "guidance": body.get("guidance", ""),
                "provider": body.get("provider"),
            },
        )

    @app.post("/api/studies/{key}/approve")
    async def approve(key: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        return studies.approve(registry, key, body["version"], request.state.account["actor"])

    @app.get("/api/studies/{key}/history")
    def history(key: str) -> list[dict[str, Any]]:
        import contextlib

        with contextlib.closing(store.connect(registry)) as db:
            return [
                loads(r[0])
                for r in db.execute(
                    "SELECT body FROM curation_study_events WHERE study=? ORDER BY sequence", (key,)
                )
            ]

    @app.get("/api/studies/{key}/export")
    def export(key: str, format: str = "json", version: str = "") -> Response:
        release = studies.export(registry, key, version)
        if format == "json":
            content, media = canonical(release), "application/json"
        elif format in {"csv", "tsv"}:
            from ..curation.harmonize import FIELDS

            stream = io.StringIO()
            columns = ["sample", "study_release", "unresolved"] + sorted(FIELDS)
            writer = csv.DictWriter(
                stream, fieldnames=columns, delimiter="," if format == "csv" else "\t"
            )
            writer.writeheader()
            for record in release["records"]:
                row = {
                    "sample": record["name"],
                    "study_release": release["integrity"]["sha256"],
                    "unresolved": ";".join(record["unresolved"]),
                    **record["metadata"],
                }
                writer.writerow(
                    {
                        k: "'" + v
                        if isinstance(v, str) and v.startswith(("=", "+", "-", "@"))
                        else v
                        for k, v in row.items()
                    }
                )
            content, media = (
                stream.getvalue(),
                "text/csv" if format == "csv" else "text/tab-separated-values",
            )
        else:
            raise ValueError("Use json, csv or tsv")
        return Response(
            content,
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="study-{key}.{format}"',
            },
        )
