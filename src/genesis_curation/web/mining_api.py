"""Authenticated mining endpoints using the existing origin, session and CSRF boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response

from ..contracts.records import canonical
from . import mining, study_api


def mount(app: FastAPI, root: Path, config_path: Path) -> None:
    @app.get("/api/mining-runs")
    def listing() -> list[dict[str, Any]]:
        return mining.listing(root)

    @app.post("/api/mining-runs")
    async def create(request: Request) -> dict[str, Any]:
        body = await request.json()
        config = study_api.provider(root, config_path, requested=body.get("provider"))
        if not config:
            raise ValueError("Select a configured model before starting mining")
        return mining.summary(mining.create(root, body, config, request.state.account["actor"]))

    @app.get("/api/mining-runs/{key}")
    def detail(key: str) -> dict[str, Any]:
        run = mining.read(root, key)
        return {
            **mining.summary(run),
            "candidates": run["candidates"],
            "ledger": run["ledger"],
            "plan": run.get("plan"),
            "queries": run["queries"],
        }

    @app.post("/api/mining-runs/{key}/control")
    async def control(key: str, request: Request) -> dict[str, Any]:
        return mining.summary(
            mining.control(root, key, await request.json(), request.state.account["actor"])
        )

    @app.post("/api/mining-runs/{key}/candidates/{cid}/decision")
    async def decide(key: str, cid: str, request: Request) -> dict[str, Any]:
        return mining.summary(
            mining.decide(root, key, cid, await request.json(), request.state.account["actor"])
        )

    @app.get("/api/mining-runs/{key}/candidates/{cid}/draft")
    def read_draft(key: str, cid: str, request: Request) -> dict[str, Any]:
        return mining.draft(root, key, cid, request.state.account["actor"])

    @app.post("/api/mining-runs/{key}/candidates/{cid}/draft")
    async def save_draft(key: str, cid: str, request: Request) -> dict[str, Any]:
        return mining.draft(root, key, cid, request.state.account["actor"], await request.json())

    @app.get("/api/mining-runs/{key}/inventory")
    def inventory(key: str) -> Response:
        run = mining.read(root, key)
        result = {
            "goal": run["goal"],
            "status": run["status"],
            "downloaded": False,
            "scientific_qc": "NOT_RUN",
            "studies": [
                {
                    "accession": c["accession"],
                    "status": c["status"],
                    "aliases": c["aliases"],
                    "reason": c["reason"],
                    "files": c["files"],
                    "selection": c.get("selected"),
                    "errors": c["errors"],
                    "evidence": c["evidence"],
                    "metadata_attachments": c.get("attachments", []),
                }
                for c in run["candidates"]
            ],
        }
        return Response(
            canonical(result),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="mined-data-inventory.json"'},
        )
