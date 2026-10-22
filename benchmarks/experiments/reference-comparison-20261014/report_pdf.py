"""Render the benchmark report, tables and measured figures as a standalone PDF."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def plain(value: str) -> str:
    return re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", value).replace("**", "").replace("`", "")


def render(report: Path, out: Path, preview: Path | None = None) -> None:
    if out.exists():
        raise FileExistsError("Choose a new report PDF")
    out.parent.mkdir(parents=True, exist_ok=True)
    if preview:
        preview.mkdir(parents=True, exist_ok=False)
    lines = report.read_text().splitlines()
    sources = {report.name: digest(report)}
    plt.rcParams.update({"font.family": "DejaVu Sans", "pdf.fonttype": 42})
    with PdfPages(
        out,
        metadata={
            "Title": plain(lines[0].lstrip("# ")),
            "Author": "Genesis",
            "CreationDate": None,
            "ModDate": None,
        },
    ) as pdf:
        figure = None
        y = 0.0
        page = 0

        def finish() -> None:
            nonlocal figure
            if figure is not None:
                pdf.savefig(figure)
                if preview:
                    figure.savefig(preview / f"page-{page:02d}.png", dpi=120)
                plt.close(figure)
                figure = None

        def new_page() -> None:
            nonlocal figure, y, page
            finish()
            page += 1
            figure = plt.figure(figsize=(11.69, 8.27))
            figure.text(0.055, 0.96, "GENESIS  /  PLANT BENCHMARKS", color="#36556b", fontsize=9)
            figure.text(
                0.055,
                0.035,
                f"Report SHA256 {sources[report.name][:16]}  ·  page {page}",
                color="#555555",
                fontsize=8,
            )
            figure.text(
                0.945,
                0.035,
                "Sources and reproducible commands",
                url="https://github.com/kundajelab/genesis-preprocessing/blob/main/"
                "validation/reports/expanded-plant-comparison.md",
                ha="right",
                color="#36556b",
                fontsize=8,
            )
            y = 0.90

        def text(value: str, size: int = 11, weight: str = "normal") -> None:
            nonlocal y
            wrapped = textwrap.wrap(plain(value), width=112 if size <= 11 else 85)
            needed = len(wrapped) * (size + 5) / (8.27 * 72) + 0.018
            if figure is None or y - needed < 0.08:
                new_page()
            if figure is None:
                raise RuntimeError("Missing PDF page")
            figure.text(
                0.055,
                y,
                "\n".join(wrapped),
                va="top",
                fontsize=size,
                weight=weight,
                linespacing=1.45,
            )
            y -= needed

        index = 0
        while index < len(lines):
            line = lines[index].strip()
            if not line:
                index += 1
                continue
            image = re.fullmatch(r"!\[([^]]*)\]\(([^)]+)\)", line)
            if image:
                path = (report.parent / image[2]).resolve().with_suffix(".png")
                sources[image[2]] = digest(path)
                new_page()
                if figure is None:
                    raise RuntimeError("Missing PDF page")
                figure.text(0.055, 0.91, image[1], fontsize=12, weight="bold")
                axis = figure.add_axes((0.04, 0.075, 0.92, 0.80))
                axis.imshow(plt.imread(path))
                axis.axis("off")
                finish()
                index += 1
                continue
            if line.startswith("|"):
                table = []
                while index < len(lines) and lines[index].strip().startswith("|"):
                    cells = [plain(c.strip()) for c in lines[index].strip().strip("|").split("|")]
                    if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                        table.append(cells)
                    index += 1
                widths = [
                    max(14, min(42, max(len(r[c]) for r in table))) for c in range(len(table[0]))
                ]
                total = sum(widths)
                fractions = [w / total for w in widths]
                wrapped = [
                    [
                        "\n".join(textwrap.wrap(c, max(12, int(140 * f))))
                        for c, f in zip(row, fractions, strict=True)
                    ]
                    for row in table
                ]
                heights = [max(c.count("\n") + 1 for c in row) * 0.021 + 0.012 for row in wrapped]
                if figure is None or y - sum(heights[:2]) < 0.09:
                    new_page()

                def table_row(
                    row: list[str], height: float, header: bool, fractions: list[float] = fractions
                ) -> None:
                    nonlocal y
                    if figure is None:
                        raise RuntimeError("Missing PDF page")
                    x = 0.055
                    for cell, fraction in zip(row, fractions, strict=True):
                        figure.text(
                            x,
                            y,
                            cell,
                            fontsize=9,
                            va="top",
                            weight="bold" if header else "normal",
                            linespacing=1.25,
                        )
                        x += fraction * 0.89
                    y -= height

                for number, (row, height) in enumerate(zip(wrapped, heights, strict=True)):
                    if y - height < 0.08:
                        new_page()
                        table_row(wrapped[0], heights[0], True)
                    table_row(row, height, number == 0)
                y -= 0.02
                continue
            if line.startswith("#"):
                text(line.lstrip("# "), 16 if line.startswith("# ") else 13, "bold")
                index += 1
                continue
            paragraph = [line]
            index += 1
            while (
                index < len(lines)
                and lines[index].strip()
                and not lines[index].startswith(("#", "|", "!"))
            ):
                paragraph.append(lines[index].strip())
                index += 1
            text(" ".join(paragraph))
        finish()
    out.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": sources,
                "pdf_sha256": digest(out),
                "renderer_sha256": digest(Path(__file__)),
                "matplotlib": matplotlib.__version__,
                "scope": (
                    "Same report text and tables; measured PNG figures embedded; "
                    "source links in footer"
                ),
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    render(args.report.resolve(), args.out.resolve(), args.preview)
