"""Authenticated QC imports, background evaluation and immutable exports."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import qc, runtime, storage


def mount(app: FastAPI, root: Path) -> None:
    @app.get("/api/qc")
    def summary() -> dict[str, Any]:
        return {
            "analyses": qc.listing(root),
            "default_profile": qc.PROFILE,
            "metrics": sorted(qc.METRICS),
        }

    @app.post("/api/qc/imports")
    async def measurements(request: Request) -> dict[str, Any]:
        storage.require_space(root)
        body = await request.json()
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("text"), str)
            or not isinstance(body.get("filename"), str)
        ):
            raise ValueError("Provide a QC filename and text")
        return await run_in_threadpool(
            qc.import_measurements,
            root,
            body["text"],
            body["filename"],
            request.state.account["actor"],
        )

    @app.post("/api/qc/analyses")
    async def evaluate(request: Request) -> dict[str, str]:
        body, actor = await request.json(), request.state.account["actor"]
        if not isinstance(body, dict):
            raise ValueError("Provide a QC profile")
        key = await run_in_threadpool(qc.start, root, body.get("profile"), actor)
        try:
            job = runtime.enqueue(root, "qc-evaluate", actor, {"analysis": key})
        except ValueError as error:
            await run_in_threadpool(qc.fail, root, key, str(error))
            raise
        return {"analysis": key, "job": job}

    @app.get("/api/qc/analyses/{key}/rows")
    def rows(key: str, study: str = "", status: str = "", cursor: str = "") -> dict[str, Any]:
        return qc.page(root, key, study, status, cursor)

    @app.post("/api/qc/analyses/{key}/decisions")
    async def decision(key: str, request: Request) -> dict[str, int]:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Provide a QC decision object")
        return await run_in_threadpool(qc.decide, root, key, body, request.state.account["actor"])

    @app.post("/api/qc/analyses/{key}/export")
    def export(key: str, request: Request) -> dict[str, str]:
        return {
            "job": runtime.enqueue(
                root, "qc-export", request.state.account["actor"], {"analysis": key}
            )
        }

    @app.get("/api/qc/exports/{key}")
    def download(key: str) -> FileResponse:
        if not re.fullmatch(r"[a-f0-9-]{36}", key):
            raise ValueError("Invalid export identifier")
        path = root / "exports" / (key + ".json")
        if not path.is_file():
            raise ValueError("Export is not ready")
        return FileResponse(path, filename="qc-cohort.json", media_type="application/json")
