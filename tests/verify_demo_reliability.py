"""Demo-scale selection, fresh activity, archive preservation and live progress contracts."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from verify_study_web import reject

from genesis_curation.contracts.records import canonical, dump, loads
from genesis_curation.llm import progress, providers
from genesis_curation.llm.security import check
from genesis_curation.registry import store
from genesis_curation.web import (
    activity,
    campaigns,
    catalog,
    runtime,
    storage,
    studies,
    table,
    workspace,
)


def streaming() -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            body = loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
            assert body["stream"] is True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for value in ['{"ok":', '"葉"}']:
                chunk = canonical(
                    {
                        "model": "fixture",
                        "choices": [
                            {"index": 0, "delta": {"content": value}, "finish_reason": None}
                        ],
                    }
                )
                raw = ("data: " + chunk + "\n\n").encode()
                for byte in raw:
                    self.wfile.write(bytes([byte]))
                self.wfile.flush()
            final = canonical(
                {
                    "model": "fixture",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"completion_tokens": 7},
                }
            )
            self.wfile.write(("data: " + final + "\n\ndata: [DONE]\n\n").encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    received: list[dict[str, object]] = []
    token = progress.listener.set(received.append)
    try:
        code, raw = providers.post(
            {
                "endpoint": f"http://127.0.0.1:{server.server_port}",
                "external": False,
                "timeout_seconds": 5,
                "capabilities": {"streaming": True},
                "model": "fixture",
            },
            {"model": "fixture", "stream": False},
            "",
        )
        assert code == 200
        assert json.loads(loads(raw)["choices"][0]["message"]["content"]) == {"ok": "葉"}
        assert received[-1]["output_characters"] == len('{"ok":"葉"}')
        assert all("content" not in item for item in received)
    finally:
        progress.listener.reset(token)
        server.shutdown()
        server.server_close()
        thread.join()
    print("PASS real streaming transport: split UTF-8, usage, live counts, no partial annotations")


def expansion() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-expansion-") as temporary:
        root = Path(temporary)
        runtime.initialize(root, "tester", "synthetic-demo-password")
        registry = root / "catalog"
        ids = catalog.import_rows(
            registry,
            canonical(
                [
                    {
                        "run_accession": "SRR123456",
                        "experiment_accession": "SRX123456",
                        "study_accession": "SRP123456",
                        "species": "Zea mays",
                    }
                ]
            ),
            "fixture.json",
            "fixture",
            "run_accession",
            "public",
            "tester",
        )
        key = studies.link(registry, ids, "SRP123456", "tester", accession="SRP123456")
        studies.preparation(registry, key, {"selection": {"subjects": ids, "available_records": 1}})
        with store.write(registry) as db:
            for index in range(2):
                workspace.add_row(
                    db,
                    "leads",
                    "ATAC",
                    index,
                    {"study_accession": "SRP123456", "title": f"Lead {index}"},
                    {},
                    "Bulk ATAC",
                    "https://www.ncbi.nlm.nih.gov/sra/SRP123456",
                )
            rows = [dict(r) for r in db.execute("SELECT * FROM workspace_rows ORDER BY id")]
        campaign = campaigns.create(root, {"id": "fixture"}, {"ids": [rows[0]["id"]]}, "tester")
        with patch.object(campaigns.intake, "import_accession", return_value=key) as imported:
            for row in rows:
                assert campaigns.materialize(root, row, campaign, None) is None
            assert imported.call_count == 1
        with contextlib.closing(store.connect(registry)) as db:
            assert studies.coverage_pending(db, studies.read(db, key)) == 0
            assert (
                db.execute(
                    "SELECT count(*) FROM workspace_rows WHERE status='Expanded'"
                ).fetchone()[0]
                == 2
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM workspace_rows WHERE subject=?", (ids[0],)
                ).fetchone()[0]
                == 1
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM workspace_items WHERE status='EXPANDED'"
                ).fetchone()[0]
                == 2
            )
        with store.write(registry) as db:
            workspace.add_row(
                db,
                "duplicate",
                "ATAC",
                0,
                {"experiment_accession": "SRX123456", "study_accession": "SRP123456"},
                {},
                "Bulk ATAC",
                "https://www.ncbi.nlm.nih.gov/sra/SRX123456",
            )
            representative = dict(
                db.execute("SELECT * FROM workspace_rows WHERE subject=?", (ids[0],)).fetchone()
            )
            assert studies.coverage_pending(db, studies.read(db, key)) == 0
        assert campaigns.materialize(root, representative, campaign, None) == ids[0]
        with contextlib.closing(store.connect(registry)) as db:
            assert (
                db.execute("SELECT subject FROM workspace_rows WHERE batch='duplicate'").fetchone()[
                    0
                ]
                == ids[0]
            )
        print(
            "PASS duplicate references reuse exact experiment identity without false coverage gaps"
        )
        print("PASS UUID study expansion, shared accession checkpoint and complete source coverage")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-demo-reliability-") as temporary:
        root = Path(temporary)
        runtime.initialize(root, "tester", "synthetic-demo-password")
        with store.write(root / "catalog") as db:
            db.executemany(
                "INSERT INTO workspace_rows(id,batch,tab,row_number,modality,species,study,"
                "entity,status,body) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f"{i:064x}",
                        "fixture",
                        "ATAC",
                        i,
                        "Bulk ATAC",
                        "Zea mays",
                        "SRP123456" if i < 4 else "",
                        f"{i:064x}",
                        "Discovered",
                        canonical(
                            {
                                "origin": "discovery",
                                "values": {"title": "Search lead"},
                                "location": "https://www.ncbi.nlm.nih.gov/",
                            }
                        ),
                    )
                    for i in range(100000)
                ],
            )
        first = workspace.page(root, query="SRP123456")
        assert first["total"] == 4
        scope = workspace.selection(root, {"ids": [f"{0:064x}"]})
        assert scope["records"] == 4 and scope["studies"] == 1
        reject(lambda: workspace.selection(root, {}), "Select records")
        reject(lambda: workspace.selection(root, {"filters": {"modality": "Bulk ATAC"}}), "200")
        config = {"id": "fixture"}
        c = campaigns.create(root, config, {"ids": scope["ids"]}, "tester")
        assert campaigns.create(root, config, {"ids": scope["ids"]}, "tester")["id"] == c["id"]
        campaigns.control(root, c["id"], "pause", "tester")
        with store.write(root / "catalog") as db:
            for i in range(300):
                workspace.event(
                    db,
                    c["id"],
                    "",
                    "search-completed",
                    result={"rows": [{"title": "x" * 20000}], "query": str(i)},
                )
        latest = workspace.activity_page(root)
        assert len(latest["items"]) == 50 and len(canonical(latest)) < 64000
        before = workspace.activity_page(root, before=latest["before"])
        assert not {x["sequence"] for x in before["items"]} & {
            x["sequence"] for x in latest["items"]
        }
        detail = workspace.activity_detail(root, latest["cursor"])
        assert len(detail["body"]["result"]["rows"][0]["title"]) == 20000
        # A paused campaign may still have one in-flight worker checkpoint.
        with runtime.connection(root) as db:
            db.execute("UPDATE jobs SET status='RUNNING' WHERE id=?", (c["job"],))
        reject(lambda: campaigns.control(root, c["id"], "archive", "tester"), "current step")
        runtime.finish(root, c["job"], {"pending": False})
        campaigns.control(root, c["id"], "archive", "tester")
        assert not workspace.summary(root)["campaigns"]
        assert not workspace.activity_page(root)["items"]
        assert workspace.summary(root, archived=True)["campaigns"]
        assert workspace.activity_page(root, archived=True)["items"]
        assert workspace.page(root)["total"] == 100000
        reject(lambda: campaigns.control(root, c["id"], "resume", "tester"), "Restore")
        campaigns.control(root, c["id"], "restore", "tester")
        assert workspace.summary(root)["campaigns"][0]["status"] == "PAUSED"
        lead = campaigns.create(root, config, {"ids": [f"{10:064x}"]}, "tester")
        with contextlib.closing(store.connect(root / "catalog")) as db:
            row = dict(
                db.execute("SELECT * FROM workspace_rows WHERE id=?", (f"{10:064x}",)).fetchone()
            )
        assert campaigns.materialize(root, row, lead, None) is None
        with contextlib.closing(store.connect(root / "catalog")) as db:
            assert db.execute("SELECT count(*) FROM curation_subjects").fetchone()[0] == 0
            assert (
                db.execute(
                    "SELECT status FROM workspace_items WHERE campaign=?", (lead["id"],)
                ).fetchone()[0]
                == "SKIPPED"
            )
        print(
            "PASS 100,000 rows: bounded scope, study expansion, duplicate prevention, "
            "unresolved lead isolation"
        )
        print(
            "PASS latest compact activity, lazy full receipts, history pagination, "
            "reversible archiving"
        )
        with activity.track(root, {"id": lead["job"], "kind": "workspace"}):
            progress.report(phase="Generating suggestions", output_characters=12)
            state = activity.read(root, lead["job"])
            assert state["output_characters"] == 12 and not state["stale"]
        assert activity.read(root, "../app.sqlite") == {}
        cache = root / "source-cache"
        cache.mkdir()
        disposable = cache / ("a" * 64 + ".json")
        dump(disposable, {"public": "cache"})
        os.utime(disposable, (1, 1))
        evidence = root / "source-snapshots"
        evidence.mkdir()
        dump(evidence / disposable.name, {"public": "evidence"})
        storage.maintain(root)
        assert not disposable.exists() and (evidence / disposable.name).exists()
        with patch.dict(os.environ, {"GENESIS_SHARED_ROOT": str(root / "absent")}):
            assert storage.status(root)["blocked"]
            reject(lambda: storage.require_space(root), "Shared evidence")
        change = {
            "rows": ["r1"],
            "field": "tissue",
            "value": "leaf",
            "status": "unknown",
            "evidence": [],
            "rationale": "No evidence",
            "confidence": None,
        }
        reject(lambda: check(change, table.CHANGE))
        check({**change, "value": None}, table.CHANGE)
        for branch in table.CHANGE["anyOf"]:
            # Grammar-based servers compile each branch independently.
            required, properties = branch["required"], branch["properties"]
            assert isinstance(required, list) and isinstance(properties, dict)
            assert set(required) == set(properties)
            assert branch["additionalProperties"] is False
            reject(lambda branch=branch: check({"status": "unknown", "value": None}, branch))
        print(
            "PASS heartbeat, shared-volume fail-closed, cache-only eviction, "
            "strict abstention schema"
        )
    streaming()
    expansion()


if __name__ == "__main__":
    main()
