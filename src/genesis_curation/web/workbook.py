"""Read spreadsheet evidence without executing formulas or normalizing source values."""

from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Retain cached values, formulas, types, styles and cell addresses; retain XLSX separately."""
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 256 * 1024 * 1024:
            raise ValueError("Workbook exceeds the 256 MiB expanded input budget")

        def xml(name: str) -> ET.Element:
            data = archive.read(name)
            if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
                raise ValueError("XML entities are not supported")
            return ET.fromstring(data)

        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = [
                "".join(n.text or "" for n in item.findall(".//m:t", NS))
                for item in xml("xl/sharedStrings.xml").findall("m:si", NS)
            ]
        links = {r.attrib["Id"]: r.attrib for r in xml("xl/_rels/workbook.xml.rels")}
        result: dict[str, list[dict[str, Any]]] = {}
        for sheet in xml("xl/workbook.xml").findall("m:sheets/m:sheet", NS):
            name = sheet.attrib["name"]
            if name in result:
                raise ValueError("Duplicate worksheet name")
            link = links[
                sheet.attrib[
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                ]
            ]
            if link.get("TargetMode") == "External":
                raise ValueError("External worksheet references are not supported")
            target = link["Target"]
            target = (
                target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            )
            if not target.startswith("xl/"):
                raise ValueError("Worksheet path escapes the workbook")
            rows = []
            for row in xml(target).findall("m:sheetData/m:row", NS):
                cells = {}
                for cell in row.findall("m:c", NS):
                    value = cell.findtext("m:v", default="", namespaces=NS)
                    kind = cell.get("t", "n")
                    if kind == "s":
                        value = strings[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(n.text or "" for n in cell.findall(".//m:t", NS))
                    cells[cell.attrib["r"]] = {
                        "value": value,
                        "type": kind,
                        "formula": cell.findtext("m:f", namespaces=NS),
                        "style": cell.get("s"),
                    }
                if any(c["value"] or c["formula"] is not None for c in cells.values()):
                    rows.append({"row": int(row.attrib["r"]), "cells": cells})
            result[name] = rows
        return result


def table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        raise ValueError("Worksheet is empty")
    columns = {
        address.rstrip("0123456789"): cell["value"] for address, cell in rows[0]["cells"].items()
    }
    names = [v for v in columns.values() if v]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate column headers")
    if "species" not in names or "experiment_accession" not in names:
        raise ValueError("Expected species and experiment_accession headers")
    result = []
    for row in rows[1:]:
        values = {name: "" for name in names}
        for address, cell in row["cells"].items():
            column = address.rstrip("0123456789")
            name = columns.get(column)
            if not name and (cell["value"] or cell["formula"]):
                raise ValueError(f"Populated cell {address} has no header")
            if name:
                values[name] = cell["value"]
        result.append({**row, "values": values})
    return result
