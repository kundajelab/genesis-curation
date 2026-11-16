"""15,000-row synthetic inventory latency and checkpoint recovery, without inference."""

from __future__ import annotations

import statistics
import tempfile
import time
from pathlib import Path

from genesis_curation.contracts.records import canonical
from genesis_curation.llm import providers
from genesis_curation.registry import store
from genesis_curation.web import campaigns, runtime, workspace


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rna-scale-") as folder:
        root = Path(folder)
        runtime.initialize(root, "tester", "synthetic-scale-password")
        with store.write(root / "catalog") as db:
            db.executemany(
                "INSERT INTO workspace_rows(id,batch,tab,row_number,modality,species,study,"
                "entity,status,body) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f"row-{i:05d}",
                        "synthetic",
                        "RNA-seq",
                        i,
                        "RNA-seq",
                        "Sorghum bicolor",
                        f"SYNTHETIC-STUDY-{i // 10}",
                        f"synthetic-record-{i:05d}",
                        "Imported",
                        canonical(
                            {
                                "original": {"id": str(i)},
                                "values": {"species": "Sorghum bicolor"},
                                "cells": {},
                                "location": "synthetic://scale",
                                "origin": "synthetic",
                            }
                        ),
                    )
                    for i in range(15000)
                ],
            )
        times = []
        for _ in range(20):
            start = time.perf_counter()
            page = workspace.page(root, "RNA-seq")
            times.append(time.perf_counter() - start)
            assert len(page["items"]) == 50 and page["total"] == 15000
        start = time.perf_counter()
        scope = workspace.selection(root, {"filters": {"modality": "RNA-seq"}})
        preview_seconds = time.perf_counter() - start
        assert scope["records"] == 15000 and scope["studies"] == 1500 and "ids" not in scope
        assert len(canonical(scope)) < 1024
        config = providers.select(
            Path(__file__).parents[1] / "examples/curation/providers.mock.json", "mock"
        )
        start = time.perf_counter()
        campaign = campaigns.create(
            root,
            config,
            {"selection_id": scope["selection_id"], "scope_token": scope["scope_token"]},
            "tester",
        )
        queue_seconds = time.perf_counter() - start
        task = runtime.claim(root)
        assert task and task["id"] == campaign["job"]
        with store.write(root / "catalog") as db:
            db.execute(
                "UPDATE workspace_items SET status='COMPLETED' WHERE campaign=? AND entity=?",
                (campaign["id"], "synthetic-record-00000"),
            )
        runtime.recover(root)
        campaigns.recover(root)
        resumed = campaigns.control(root, campaign["id"], "resume", "tester")
        assert resumed["status"] == "RUNNING"
        with store.write(root / "catalog") as db:
            counts = dict(
                db.execute(
                    "SELECT status,count(*) FROM workspace_items WHERE campaign=? GROUP BY status",
                    (campaign["id"],),
                )
            )
        assert counts == {"COMPLETED": 1, "QUEUED": 14999}, counts
        campaigns.control(root, campaign["id"], "pause", "tester")
        p95 = statistics.quantiles(times, n=20, method="inclusive")[18]
        print(
            f"PASS 15,000 synthetic rows: p95 page={p95:.4f}s; preview={preview_seconds:.4f}s; "
            f"queue acknowledgement={queue_seconds:.4f}s; 1,500 studies; checkpoints preserved"
        )
        print("NOT RUN biological accuracy or 15,000 live LLM annotations")


if __name__ == "__main__":
    main()
