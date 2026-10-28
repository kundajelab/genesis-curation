"""Read-only, escaped summaries and ID-based approved report downloads."""

from __future__ import annotations

import contextlib
import hashlib
import html
from pathlib import Path
from urllib.parse import quote, unquote

from ..registry import store
from . import review


def summary(directory: Path, campaign: Path) -> str:
    from ..contracts.records import fingerprint, load

    campaign_id = fingerprint(load(campaign / "campaign.json"))
    result = [
        "<h2>Scientific curation</h2><p>Independent of execution status; review in the CLI.</p><ul>"
    ]
    with contextlib.closing(store.connect(directory)) as db:
        db.execute("BEGIN")
        for row in store.search(db, limit=1000):
            manifest = store.get(db, "manifest", row["manifest"])["data"]
            if manifest["run"]["campaign_id"] != campaign_id:
                continue
            state = review.status(db, row["id"])
            result.append(
                "<li>"
                + html.escape(row["name"])
                + ": "
                + html.escape(
                    f"structure {state['structural']}; QC {state['qc']}; "
                    f"metadata {state['reviews']['metadata']}; "
                    f"export {state['reviews']['eligibility']}"
                )
            )
            if state["reviews"]["eligibility"] == "APPROVED":
                for artifact in manifest["artifacts"]:
                    if (
                        artifact["role"] == "report"
                        and artifact["format"] == "html"
                        and artifact["sha256"]
                    ):
                        url = (
                            "/reports/"
                            + quote(row["id"], safe="")
                            + "/"
                            + quote(artifact["id"], safe="")
                        )
                        result.append(' <a href="' + url + '">Download approved report</a>')
            result.append("</li>")
    return "".join(result) + "</ul>"


def report(directory: Path, route: str) -> bytes:
    parts = route.split("/")
    if len(parts) != 4 or parts[:2] != ["", "reports"]:
        raise ValueError("Unknown report route")
    dataset, artifact_id = map(unquote, parts[2:])
    with contextlib.closing(store.connect(directory)) as db:
        db.execute("BEGIN")
        if review.effective(db, dataset, "eligibility") != "APPROVED":
            raise ValueError("Report is not currently approved")
        row = store.current(db, dataset)
        manifest = store.get(db, "manifest", row["manifest"])["data"]
        item = next(
            (
                a
                for a in manifest["artifacts"]
                if a["id"] == artifact_id
                and a["role"] == "report"
                and a["format"] == "html"
                and a["availability"] == "available"
            ),
            None,
        )
        if not item or item["worker"] != "local":
            raise ValueError("Only controller-local registered reports can be downloaded")
        path = Path(item["path"]).resolve()
        root = (Path(manifest["run"]["root"]) / "output").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Report is outside the registered output root")
        with path.open("rb") as stream:
            body = stream.read(32 * 1024 * 1024 + 1)
        if len(body) > 32 * 1024 * 1024 or hashlib.sha256(body).hexdigest() != item["sha256"]:
            raise ValueError("Report digest changed or report exceeds download limit")
        return body
