"""Large deterministic input, final-record flushing and bounded-memory ingestion."""

from __future__ import annotations

import argparse
from pathlib import Path

import pysam
from verify_scatac_fragments import inputs

from genesis_curation.contracts.records import dump, load
from genesis_curation.scatac import ingest
from genesis_curation.scatac.common import digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    _, manifests = inputs(root)
    data = load(manifests[0])
    fragment = root / "streaming.tsv"
    with fragment.open("w") as stream:
        for i in reversed(range(100000)):
            stream.write(f"chr1\t1\t20\tBC{i:06d}\t2\n")
    data["fragments"] = {"path": str(fragment), "sha256": digest(fragment)}
    dump(root / "input.json", data)
    result = ingest.run(root / "input.json", root / "ingested")
    counts = result["manifest"]["data"]["counts"]
    assert counts["fragments"] == counts["barcodes"] == 100000
    assert counts["support"] == 200000
    with pysam.TabixFile(str(root / "ingested/fragments.tsv.gz")) as reader:
        count = 0
        last = ""
        for record in reader.fetch("chr1", 0, 30):
            last = record
            count += 1
        assert count == 100000 and last.split("\t")[3] == "BC099999"
    assert ingest.run(root / "input.json", root / "ingested", resume=True)["cached"]
    print("PASS: 100000 fragments, 200000 support, indexed final record, verified resume")


if __name__ == "__main__":
    main()
