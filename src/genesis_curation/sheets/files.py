"""Read files of a run, from the FASTQ files ENA serves now (not from its layout label).

- Two files `<run>_1` and `<run>_2`: paired, whatever ENA's label says (some SINGLE runs
  are served this way and hold real mates).
- ENA says PAIRED but serves one file: left out; that file holds both mates in two blocks,
  so as single-end strands mix and fragments count twice.
- Otherwise `<run>.fastq.gz`: single-end.
- A file under 1 MB is left out (empty or truncated uploads).
URLs are built only from ENA's current `fastq_ftp` list, so every URL is one ENA serves.
"""

from __future__ import annotations

from dataclasses import dataclass

from .archives import Run

MIN_FILE_BYTES = 1_000_000


@dataclass(frozen=True)
class ReadFiles:
    read1: str = ""
    read2: str = "-"
    reason: str = ""  # why the run has no usable files, or ''

    @property
    def layout(self) -> str:
        return "" if self.reason else "single" if self.read2 == "-" else "paired"


def served(run: Run) -> dict[str, int]:
    """https URL -> size in bytes (-1 when ENA gives none) of each served FASTQ file."""
    urls = [u for u in run.get("fastq_ftp", "").split(";") if u]
    sizes = run.get("fastq_bytes", "").split(";")
    return {
        "https://" + url: int(sizes[i]) if i < len(sizes) and sizes[i].isdigit() else -1
        for i, url in enumerate(urls)
    }


def resolve(run: Run) -> ReadFiles:
    accession = run["run_accession"]
    files = served(run)
    if not files:
        return ReadFiles(reason="no read files")
    mine = {u: n for u, n in files.items() if f"/{accession}/" in u or f"/{accession}_" in u}
    read1 = [u for u in mine if u.endswith(f"/{accession}_1.fastq.gz")]
    read2 = [u for u in mine if u.endswith(f"/{accession}_2.fastq.gz")]
    single = [u for u in mine if u.endswith(f"/{accession}.fastq.gz")]
    if read1 and read2:
        chosen = ReadFiles(read1[0], read2[0])
    elif run.get("library_layout") == "PAIRED" and len(mine) == 1:
        return ReadFiles(reason="paired run served as one file")
    elif single:
        chosen = ReadFiles(single[0])
    else:
        return ReadFiles(reason="no read files")
    used = [chosen.read1] + ([chosen.read2] if chosen.read2 != "-" else [])
    if any(0 <= mine[u] < MIN_FILE_BYTES for u in used):
        return ReadFiles(reason="FASTQ under 1 MB")
    return chosen
