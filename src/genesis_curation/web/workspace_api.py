"""Authenticated workspace tables, background campaigns and provenance exports."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

from . import campaigns, runtime, study_api, workspace
from .discovery_catalog import ASSAYS, KNOWN_REPOSITORIES, SOURCES, VERSION


def mount(app: FastAPI, root: Path, config_path: Path) -> None:
    @app.get("/api/workspace")
    def summary() -> dict[str, Any]:
        return workspace.summary(root)

    @app.get("/api/workspace/recipes")
    def recipes() -> dict[str, Any]:
        return {
            "version": VERSION,
            "assays": ASSAYS,
            "sources": SOURCES,
            "repositories": KNOWN_REPOSITORIES,
        }

    @app.get("/api/workspace/rows")
    def rows(
        modality: str = "Bulk ATAC",
        species: str = "",
        status: str = "",
        query: str = "",
        cursor: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return workspace.page(root, modality, species, status, query, cursor, limit)

    @app.get("/api/workspace/rows/{key}/history")
    def history(key: str) -> dict[str, Any]:
        return workspace.history(root, key)

    @app.get("/api/workspace/events")
    def events(campaign: str = "", after: int = 0) -> dict[str, Any]:
        return workspace.events(root, campaign, after)

    @app.post("/api/workspace/campaigns")
    async def create(request: Request) -> dict[str, Any]:
        body = await request.json()
        config = study_api.provider(root, config_path, requested=body.get("provider"))
        if not config:
            raise ValueError("Select an available model")
        return campaigns.create(root, config, body, request.state.account["actor"])

    @app.post("/api/workspace/campaigns/{key}/control")
    async def control(key: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        return campaigns.control(root, key, body.get("action", ""), request.state.account["actor"])

    @app.post("/api/workspace/exports")
    async def export(request: Request) -> dict[str, str]:
        body = await request.json()
        return {
            "id": runtime.enqueue(
                root,
                "workspace-export",
                request.state.account["actor"],
                {"clean": body.get("clean") is True},
            )
        }

    @app.get("/api/workspace/exports/{key}")
    def download(key: str) -> FileResponse:
        if not re.fullmatch(r"[a-f0-9-]{36}", key):
            raise ValueError("Invalid export identifier")
        path = root / "exports" / (key + ".zip")
        if not path.exists():
            raise ValueError("Export is not ready")
        return FileResponse(path, filename="genesis-metadata.zip", media_type="application/zip")
