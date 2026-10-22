"""Known-answer workbook import without assuming accession-to-replicate equivalence."""

from __future__ import annotations

import contextlib
import csv
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from genesis_curation.contracts.records import load
from genesis_curation.registry import store
from genesis_curation.scatac import inventory, workbook


def fixture(path: Path) -> None:
    columns = [
        "species",
        "experiment_accession",
        "run_accessions",
        "bioproject",
        "biosample",
        "tissue",
        "multiome",
        "sample_name",
        "genotype",
    ]
    a = [
        "Arabidopsis thaliana",
        "SRX12345",
        "SRR12345;SRR12346",
        "PRJNA12345",
        "SAMN12345",
        "root",
        "no",
        "rep1",
        "001",
    ]
    rows = [
        columns,
        a,
        a,
        [*a[:5], "leaf", "yes", "rep2", "001"],
        [
            "Sorghum bicolor",
            "invalid",
            "SRR34567",
            "PRJNA34567",
            "SAMN34567",
            "leaf",
            "yes",
            "rep1",
            "",
        ],
    ]
    data = []
    for number, row in enumerate(rows, 1):
        cells = "".join(
            f'<c r="{chr(65 + i)}{number}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
            for i, v in enumerate(row)
        )
        data.append(f'<row r="{number}">{cells}</row>')
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{ns}" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="scATAC Fable Curated" sheetId="1" r:id="r1"/>'
            "</sheets></workbook>",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{ns}"><sheetData>{"".join(data)}</sheetData></worksheet>',
        )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source, directory, registry = (
            root / "source.xlsx",
            root / "inventory",
            root / "registry",
        )
        fixture(source)
        first = inventory.import_workbook(source, directory, registry, "test")
        assert first == inventory.import_workbook(source, directory, registry, "test")
        with contextlib.closing(store.connect(registry)) as db:
            assert (
                db.execute("SELECT COUNT(*) FROM records WHERE kind='scatac_inventory'").fetchone()[
                    0
                ]
                == 1
            )
            assert db.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0
        result = inventory.audit(directory)
        codes = {i["code"] for i in result["metadata_issues"]}
        assert {
            "DUPLICATE_ROW",
            "REPEATED_ACCESSION",
            "CONFLICTING_RELATIONSHIP",
            "INVALID_ACCESSION",
            "REFERENCE_UNRESOLVED",
            "REPLICATE_UNRESOLVED",
        } <= codes
        assert len(result["libraries"]) == 4
        assert all(r["library_id"] == "" for r in result["libraries"])
        assert {r["multiome"] for r in result["libraries"]} == {"yes", "no"}
        assert result["summary"]["Arabidopsis thaliana"]["run_accessions"] == 2
        assert all(r["fragments"] == "UNKNOWN" for r in result["readiness"])
        output = root / "export"
        inventory.export(directory, output)
        with (output / "scATAC_libraries.tsv").open() as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        assert rows[0]["genotype"] == "001"
        assert load(output / "original-values.json")["data"]["rows"] == workbook.table(
            workbook.read(source)["scATAC Fable Curated"]
        )
        assert next(directory.glob("*.xlsx")).read_bytes() == source.read_bytes()
        source.write_bytes(b"not a workbook")
        try:
            inventory.import_workbook(source, directory, registry, "test")
        except zipfile.BadZipFile:
            pass
        else:
            raise AssertionError("Invalid workbook accepted")
        assert load(directory / "inventory.json")["version"] == first["inventory"]
    print("PASS: lossless values, idempotent import, unresolved identities and conflicts")


if __name__ == "__main__":
    main()
