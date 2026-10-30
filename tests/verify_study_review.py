"""Study checklist scale, partial decisions, membership and storage safeguards."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from verify_study_web import reject

from genesis_curation.contracts.records import canonical
from genesis_curation.registry import store
from genesis_curation.web import campaigns, catalog, review_queue, runtime, storage, studies


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="study-checklist-") as temporary:
        root = Path(temporary)
        store.initialize(root)
        ids = catalog.import_rows(
            root,
            canonical(
                [
                    {"id": f"sample-{i}", "organism": "Zea mays", "assay": "ATAC-seq"}
                    for i in range(1000)
                ]
            ),
            "synthetic.json",
            "synthetic",
            "id",
            "public",
            "tester",
        )
        key = studies.link(root, ids, "SRP123456", "tester", accession="SRP123456")
        original = studies.get(root, key)
        assert original["member_count"] == 1000
        assert studies.link(root, ids[:20], "SRP123456", "tester", accession="SRP123456") == key
        assert studies.get(root, key)["version"] == original["version"]
        studies.preparation(root, key, {"status": "COMPLETED"})
        page = review_queue.study_page(root)
        assert page["total"] == 1 and page["items"][0]["member_count"] == 1000
        assert len(canonical(page)) < 2000
        checklist = review_queue.checklist(root, key)
        assert checklist["total"] > 0 and all("targets" not in t for t in checklist["items"])
        with patch.object(studies, "view", side_effect=AssertionError("Cache missed")):
            assert review_queue.study_page(root) == page
            assert review_queue.checklist(root, key) == checklist
        first = review_queue.sample_page(root, key)
        second = review_queue.sample_page(root, key, first["next_cursor"])
        assert len(first["items"]) == 25 and first["total"] == 1000
        assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
        task = studies.get(root, key)["tasks"][0]
        assert len(task["targets"]) == 1000
        target = task["targets"][0]["subject"]
        body = {
            "study": key,
            "version": task["version"],
            "action": "unknown",
            "reason": "Synthetic unresolved field",
            "targets": [target],
        }
        reject(lambda: studies.resolve(root, task["id"], {**body, "targets": []}, "tester"))
        reject(lambda: studies.resolve(root, task["id"], {**body, "targets": ["other"]}, "tester"))
        studies.resolve(root, task["id"], body, "tester")
        assert catalog.get(root, target)["reviews"]
        assert not catalog.get(root, task["targets"][1]["subject"])["reviews"]
        reject(lambda: studies.resolve(root, task["id"], body, "tester"), "Stale")
        extra = catalog.import_rows(
            root, '[{"id":"extra"}]', "extra.json", "synthetic", "id", "public", "tester"
        )
        studies.link(root, extra, "SRP123456", "tester", accession="SRP123456")
        assert studies.get(root, key)["preparation"]["status"] == "NOT_STARTED"
        reject(lambda: studies.approve(root, key, studies.get(root, key)["version"], "tester"))
        low = SimpleNamespace(total=60 * 1024**3, free=1024**3)
        recovered = SimpleNamespace(total=60 * 1024**3, free=8 * 1024**3)
        with patch.object(storage.shutil, "disk_usage", return_value=low):
            reject(lambda: storage.require_space(root), "2 GiB")
            assert storage.paused(root)
            reject(lambda: storage.resume(root))
        with patch.object(storage.shutil, "disk_usage", return_value=recovered):
            assert storage.paused(root)
            storage.resume(root)
            assert not storage.paused(root)
    with tempfile.TemporaryDirectory(prefix="study-campaign-") as temporary:
        root = Path(temporary)
        runtime.initialize(root, "tester", "synthetic-checklist-password")
        with store.write(root / "catalog") as db:
            for i in range(31):
                db.execute(
                    "INSERT INTO workspace_rows VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(i),
                        "fixture",
                        "fixture",
                        i,
                        "Bulk ATAC" if i < 20 else "RNA-seq",
                        "Zea mays",
                        "SRP123456",
                        "SRX" + str(i),
                        "",
                        "Imported",
                        "{}",
                    ),
                )
        config = {"id": "fixture"}
        campaign = campaigns.create(root, config, {"ids": ["0"]}, "tester")
        with store.write(root / "catalog") as db:
            assert db.execute("SELECT count(*) FROM workspace_items").fetchone()[0] == 31
        with (
            patch.object(campaigns.providers, "select", return_value=config),
            patch.object(campaigns.research_search, "search", return_value={"source": "synthetic"}),
        ):
            campaigns.step(
                root,
                root / "unused",
                {"id": campaign["job"], "payload": {"campaign": campaign["id"]}},
                None,
            )
        with store.write(root / "catalog") as db:
            from genesis_curation.contracts.records import loads

            saved = loads(db.execute("SELECT body FROM workspace_campaigns").fetchone()[0])
            assert len(saved["group"]["rows"]) == 31
    print("PASS study campaign: selecting one row includes 31 known records across assays")
    print(
        "PASS study checklist: 1,000 samples; cached summaries; paginated samples; selected-target "
        "decisions; stale rejection; membership invalidation; disk pause and manual resume"
    )


if __name__ == "__main__":
    main()
