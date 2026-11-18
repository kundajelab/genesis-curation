"""Sample-sheet campaigns from the app: the same code as `genesis sheets campaign`.

A campaign runs as a background job. It builds every chosen workflow for one species from
archive metadata, reuses the gold sheets' read measurements and scores each build against
the gold where gold exists. Results stay under RUNTIME/sheets/<job id>/.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

from ..llm import progress
from ..sheets import cli as sheets
from ..sheets import species as species_catalog
from . import runtime

KIND = "sheets-campaign"
JOB_ID = re.compile(r"[a-f0-9-]{36}")


def folder(root: Path) -> Path:
    return root / "sheets"


def atac_tab(root: Path) -> Path:
    """The "Bulk ATAC Fable Curated" tab of the Genesis metadata sheet, exported as TSV and
    copied here by the operator (it is private and larger than an upload). It adds the runs
    ENA does not label ATAC-seq and the GSA runs."""
    return folder(root) / "inputs" / "bulk-atac-tab.tsv"


def summary(path: Path) -> dict[str, Any]:
    """A finished campaign without server paths: counts, scores and which files exist."""
    report = json.loads((path / "campaign.json").read_text())
    workflows = {}
    for workflow, item in report["workflows"].items():
        files = [
            name
            for name in (f"{workflow}.tsv", f"{workflow}.runs-qc.tsv", "score.json")
            if (path / workflow / name).is_file()
        ]
        workflows[workflow] = {k: v for k, v in item.items() if k != "sheet"} | {"files": files}
    created = datetime.datetime.fromtimestamp(
        (path / "campaign.json").stat().st_mtime, datetime.UTC
    )
    return {
        "id": path.name,
        "species": report["species"],
        "created": created.isoformat(),
        "workflows": workflows,
    }


def listing(root: Path) -> list[dict[str, Any]]:
    runs = [
        summary(path)
        for path in folder(root).glob("*")
        if JOB_ID.fullmatch(path.name) and (path / "campaign.json").is_file()
    ]
    return sorted(runs, key=lambda run: run["created"], reverse=True)


def run(root: Path, task: dict[str, Any], cancellation: threading.Event | None) -> dict[str, Any]:
    """Worker side of a campaign job."""
    data = task["payload"]
    manifest = sheets.EVALS / "gold.yaml"
    gold = folder(root) / "gold"
    if manifest.is_file():
        progress.report(phase="Fetching gold sheets")
        sheets.fetch(argparse.Namespace(manifest=manifest, to=gold))
    workflows = data["workflows"]

    def step(number: int, workflow: str) -> None:
        if cancellation and cancellation.is_set():
            raise ValueError("Request cancelled")
        progress.report(phase=f"Building {workflow}", completed=number, total=len(workflows))

    args = argparse.Namespace(
        species=data["species"],
        out=folder(root) / task["id"],
        workflows=workflows,
        gold_dir=gold if manifest.is_file() else None,
        cache=folder(root) / "cache",
        offline=False,
        no_probe=False,
        atac_extra_runs=atac_tab(root) if atac_tab(root).is_file() else None,
        dap_skip_runs_in=[],
        manifest=manifest,
        traps=sheets.EVALS / "traps.yaml",
        history=None,
        label=None,
    )
    report, _ = sheets.run_campaign(args, step)
    failed = sum("error" in item for item in report["workflows"].values())
    return {"campaign": task["id"], "failed": failed}


def campaign(body: object) -> dict[str, Any]:
    """The job payload for a campaign request, or ValueError."""
    if not isinstance(body, dict):
        raise ValueError("Provide a species and workflows")
    species, workflows = body.get("species"), body.get("workflows")
    if species not in species_catalog.catalog():
        raise ValueError("Unknown species")
    if (
        not isinstance(workflows, list)
        or not workflows
        or not all(isinstance(w, str) for w in workflows)
        or not set(workflows) <= set(sheets.BUILDERS)
    ):
        raise ValueError("Choose one or more workflows")
    return {"species": species, "workflows": sorted(set(workflows))}


def mount(app: FastAPI, root: Path) -> None:
    @app.get("/api/sheets")
    def overview() -> dict[str, Any]:
        return {
            "species": sorted(species_catalog.catalog()),
            "workflows": sorted(sheets.BUILDERS),
            "campaigns": listing(root),
            "atac_tab": atac_tab(root).is_file(),
        }

    @app.post("/api/sheets/campaigns")
    async def start(request: Request) -> dict[str, str]:
        payload = campaign(await request.json())
        return {"job": runtime.enqueue(root, KIND, request.state.account["actor"], payload)}

    @app.get("/api/sheets/campaigns/{key}/{workflow}/{name}")
    def download(key: str, workflow: str, name: str) -> FileResponse:
        allowed = {f"{workflow}.tsv", f"{workflow}.runs-qc.tsv", "score.json"}
        if not JOB_ID.fullmatch(key) or workflow not in sheets.BUILDERS or name not in allowed:
            raise ValueError("Invalid sheet file")
        path = folder(root) / key / workflow / name
        if not path.is_file():
            raise ValueError("No such file in this campaign")
        media = "application/json" if name.endswith(".json") else "text/tab-separated-values"
        return FileResponse(path, filename=f"{key[:8]}-{name}", media_type=media)
