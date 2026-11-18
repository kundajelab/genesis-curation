"""Build a scatac-reads sample sheet for one species from NCBI SRA: one row per 10x library
with its runs, and a reason for every candidate run left out.

Candidates are SRA experiments of the species (not its sub-taxa) found by an Entrez search
for ATAC-seq, single-cell genomic libraries and single-cell ATAC terms, plus every other
experiment of a study that holds a 10x-like run (mixed-species pools, BAM resubmissions,
lanes in other experiments). An experiment is a candidate when it looks single-cell: a
single-cell library source, a 16- or 24-base read, three or more reads per spot,
single-cell words in its titles (or, for ATAC-seq, its study title), or a BioSample shared
with such an experiment. Bulk ATAC-seq of two reads is not a candidate.

Runs are grouped into libraries: one SRA experiment, merged with the other experiments of
its study that share its BioSample and whose titles differ only by a lane or sample-index
suffix (`atsht_S1` .. `atsht_S4`), plus the merges in `data/scatac.yaml`.

A library's reason is the first that applies:
 1. a run has no reads in SRA, or is not public;
 2. mixed-species pool (sample of another taxon whose titles name this species);
 3. not ATAC (RNA, CUT&Tag, histone marks, ...) by library source, strategy or titles;
 4. the same library as a 10x experiment, resubmitted without the barcode read (same
    BioSample and spot count, usually a BAM), or listed in `same_as`;
 5. excluded by hand (`exclude` in data/scatac.yaml);
 6. read structure: every run needs exactly one barcode read (16 bases: 10x-atac-v1 with
    the 737K-cratac-v1 whitelist; 24 bases: 10x-multiome-atac, no whitelist) and two
    genomic reads of at least 30 bases, and all runs of a library the same structure;
 7. genotype is not the reference; 8. treated or another condition (shared classifier,
    with study values from data/scatac.yaml);
 9. within-study title rules and transgene words (classify.StudyTitles, transgene_flag);
10. mixed instruments: reads after the first 200,000 come from another instrument family
    (measure/instruments.py), so the workflow's single barcode orientation is wrong for them.
"""

from __future__ import annotations

import dataclasses
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import classify, writers
from .archives import Archive, Run, SraExperiment, SraRun, sra_experiments, sra_search
from .classify import L, R
from .measure import instruments
from .species import DATA, Species

WORKFLOW = "scatac-reads"
# Barcode read length -> (chemistry, whitelist), as workflows/scatac-reads.nf reads them.
BARCODES = {16: ("10x-atac-v1", "737K-cratac-v1.txt"), 24: ("10x-multiome-atac", "-")}
MIN_GENOMIC = 30
SEARCH = (
    '{organism} AND ("atac seq"[Strategy] OR "genomic single cell"[Source] '
    'OR scATAC OR snATAC OR "sci-ATAC" OR multiome OR "single cell ATAC" '
    'OR "single nucleus ATAC" OR "single nuclei ATAC")'
)
ATAC_STRATEGIES = {"ATAC-seq", "OTHER"}
SINGLE_CELL = re.compile(
    L + r"s[cn]-?atac\w*|sci-?atac\w*|s-?nuc-?atac\w*|single[- ]?(cell|nucle\w*)|multiome|"
    r"10x|chromium" + R,
    re.I,
)
NOT_ATAC = re.compile(
    L + r"(sn|sc)?cut ?(&|and) ?(tag|run)|(sn|sc)?his|h3k\d+\w*|chip-?seq|methyl\w*|"
    r"bisulfite|gex|(sc|sn)rna\w*|rna-?seq|rna" + R,
    re.I,
)
LANE = re.compile(r"(?:[_-](?:s|l|lane)0*\d+|[,;]?\s*lane\s*\d+)\s*$", re.I)
REPLICATE = re.compile(r"(?<![a-z])rep(?:licate)?[ _-]?(\d+)", re.I)
TIME = re.compile(r"(?<![a-z0-9.])(\d+) ?(h|hr|hpi|d|dpi)(?![a-z])", re.I)
CONTROL_WORD = re.compile(r"(?<![a-z])(mock|control|untreated)(?![a-z])", re.I)


@dataclass
class Options:
    cache: Path
    probe: bool = True
    vdb_dump: str | None = None
    dump: instruments.Dump | None = None  # replaces vdb-dump in tests
    timings: dict[str, float] = field(default_factory=dict)


@dataclass
class Library:
    experiments: list[SraExperiment]
    reason: str = ""
    chemistry: str = ""
    sample: classify.SampleClass | None = None
    sample_id: str = ""
    probes: dict[str, instruments.RunInstruments] = field(default_factory=dict)

    @property
    def lead(self) -> SraExperiment:
        return self.experiments[0]

    @property
    def runs(self) -> list[SraRun]:
        return sorted(
            (run for experiment in self.experiments for run in experiment.runs),
            key=lambda run: (len(run.accession), run.accession),
        )


@dataclass(frozen=True)
class Decisions:
    """data/scatac.yaml for one species."""

    studies: dict[str, dict[str, str]] = field(default_factory=dict)
    merge: list[list[str]] = field(default_factory=list)
    same_as: dict[str, str] = field(default_factory=dict)  # experiment -> reason
    exclude: dict[str, str] = field(default_factory=dict)  # experiment -> reason


def decisions(species: Species, path: Path = DATA / "scatac.yaml") -> Decisions:
    data = (yaml.safe_load(path.read_text()) or {}).get(species.folder) or {}
    for section in ("merge", "same_as", "exclude"):
        for entry in data.get(section) or []:
            if not entry.get("why"):
                raise ValueError(f"{path.name}: {species.folder} {section} entry without why")
    for study, entry in (data.get("studies") or {}).items():
        if not entry.get("why"):
            raise ValueError(f"{path.name}: {species.folder} study {study} without why")
    return Decisions(
        studies={
            study: {k: str(v) for k, v in entry.items() if k in ("genotype", "treatment")}
            for study, entry in (data.get("studies") or {}).items()
        },
        merge=[list(entry["experiments"]) for entry in data.get("merge") or []],
        same_as={
            entry["experiment"]: f"same library as {entry['library']}: {entry['reason']}"
            for entry in data.get("same_as") or []
        },
        exclude={
            experiment: entry["reason"]
            for entry in data.get("exclude") or []
            for experiment in entry["experiments"]
        },
    )


def run_structure(run: SraRun) -> tuple[str, str]:
    """(chemistry, '') for a 10x ATAC run, or ('', why not)."""
    if not run.public:
        return "", "run not public"
    if not run.spots or not run.reads:
        return "", "no reads in SRA (not loaded)"
    lengths = run.lengths
    barcode = [n for n in lengths if n in BARCODES]
    genomic = [n for n in lengths if n >= MIN_GENOMIC]
    if len(barcode) != 1 or len(genomic) < 2:
        shown = ",".join(map(str, lengths))
        return "", f"no 10x barcode read: reads of {shown} bases (sci-ATAC or plate-based)"
    return BARCODES[barcode[0]][0], ""


def ten_x(experiment: SraExperiment) -> bool:
    return any(run_structure(run)[0] for run in experiment.runs)


def names_species(species: Species, value: str) -> bool:
    genus, _, epithet = species.scientific_name.lower().partition(" ")
    pattern = rf"(?<![a-z])({genus}[ _]?{epithet}|{genus[0]}\.?[ _]?{epithet})(?![a-z])"
    return bool(re.search(pattern, value, re.I))


def mixed_pool(species: Species, experiment: SraExperiment) -> bool:
    """A sample of another taxon (or a pool of several) that names this species."""
    if species.has_taxon(experiment.taxon) and len(experiment.pool) <= 1:
        return False
    text = " ".join(
        (experiment.title, experiment.library_name, experiment.sample.title, experiment.organism)
    )
    return names_species(species, text) or species.scientific_name in experiment.pool


def looks_single_cell(experiment: SraExperiment) -> bool:
    titles = " ".join((experiment.title, experiment.library_name, experiment.sample.title))
    return bool(
        "SINGLE CELL" in experiment.source.upper()
        or any(len(run.reads) >= 3 or set(run.lengths) & set(BARCODES) for run in experiment.runs)
        or SINGLE_CELL.search(titles)
        or (experiment.strategy == "ATAC-seq" and SINGLE_CELL.search(experiment.study_title))
    )


def candidates(species: Species, found: Iterable[SraExperiment]) -> list[SraExperiment]:
    """Experiments of the species (or pools naming it) that look single-cell, with the
    experiments sharing their BioSample in the same study."""
    mine = [e for e in found if species.has_taxon(e.taxon) or mixed_pool(species, e)]
    chosen = {e.accession for e in mine if looks_single_cell(e)}
    samples = {(e.study, e.sample.accession) for e in mine if e.accession in chosen}
    chosen |= {e.accession for e in mine if (e.study, e.sample.accession) in samples}
    return sorted((e for e in mine if e.accession in chosen), key=lambda e: e.accession)


def discover(species: Species, archive: Archive, log: Callable[[str], None]) -> list[SraExperiment]:
    uids = sra_search(archive, SEARCH.format(organism=species.ncbi_organism))
    found = {e.accession: e for e in sra_experiments(archive, uids)}
    log(f"{species.folder}: {len(found)} SRA experiments from the search")
    studies = sorted({e.study for e in found.values() if species.has_taxon(e.taxon) and ten_x(e)})
    for study in studies:
        for experiment in sra_experiments(archive, sra_search(archive, f"{study}[Accession]")):
            if experiment.study == study:
                found.setdefault(experiment.accession, experiment)
    chosen = candidates(species, found.values())
    log(f"{species.folder}: {len(chosen)} candidate experiments in {len(studies)} 10x studies")
    return chosen


def lane_free(value: str) -> str:
    previous = None
    while previous != value:
        previous, value = value, LANE.sub("", value)
    return value.strip().lower()


def bam_duplicates(experiments: Sequence[SraExperiment]) -> dict[str, str]:
    """Experiment -> why it repeats a 10x experiment of the same study and BioSample: it
    has no barcode read and the same number of spots (within 1%). Spot counts, not the
    file type, decide: a study can also hold long-read BAMs of the same BioSample."""
    by_sample: dict[tuple[str, str], list[SraExperiment]] = defaultdict(list)
    for experiment in experiments:
        if experiment.sample.accession:
            by_sample[(experiment.study, experiment.sample.accession)].append(experiment)
    found = {}
    for group in by_sample.values():
        tenx = [e for e in group if ten_x(e)]
        for experiment in group:
            spots = sum(run.spots for run in experiment.runs)
            if ten_x(experiment) or not spots:
                continue
            for other in tenx:
                other_spots = sum(run.spots for run in other.runs)
                if abs(spots - other_spots) <= other_spots / 100:
                    form = "BAM" if any(run.bam for run in experiment.runs) else "reads"
                    found[experiment.accession] = (
                        f"same library as {other.accession}, submitted as {form} without the "
                        "barcode read"
                    )
                    break
    return found


def group(experiments: Sequence[SraExperiment], chosen: Decisions) -> list[Library]:
    """Libraries from experiments: hand merges first, then experiments of one study and
    BioSample whose library names and titles differ only by a lane suffix."""
    by_accession = {e.accession: e for e in experiments}
    duplicates = bam_duplicates(experiments)
    owner: dict[str, str] = {}
    for merged in chosen.merge:
        for accession in merged:
            owner[accession] = merged[0]
    keys: dict[tuple[str, ...], str] = {}
    for experiment in experiments:
        if experiment.accession in owner or experiment.accession in duplicates:
            continue
        if not experiment.sample.accession:
            continue
        key = (
            experiment.study,
            experiment.sample.accession,
            lane_free(experiment.library_name),
            lane_free(experiment.title),
            experiment.sample.title.lower(),
        )
        owner[experiment.accession] = keys.setdefault(key, experiment.accession)
    members: dict[str, list[SraExperiment]] = defaultdict(list)
    for experiment in experiments:
        members[owner.get(experiment.accession, experiment.accession)].append(experiment)
    libraries = []
    for lead, items in members.items():
        if lead in by_accession:
            items.sort(key=lambda e: (e.accession != lead, e.accession))
        libraries.append(Library(items))
    return sorted(libraries, key=lambda library: (library.lead.study, library.lead.accession))


def not_atac(experiment: SraExperiment) -> str:
    if experiment.source.upper().startswith("TRANSCRIPTOMIC") or experiment.strategy in (
        "RNA-Seq",
        "ssRNA-seq",
        "miRNA-Seq",
    ):
        return "not ATAC (RNA)"
    match = NOT_ATAC.search(f"{experiment.title} | {experiment.library_name}")
    if match:
        return f"not ATAC ({match.group(1)})"
    if experiment.strategy not in ATAC_STRATEGIES:
        return f"not ATAC (strategy {experiment.strategy or 'not stated'})"
    return ""


def classify_library(
    species: Species, library: Library, study: dict[str, str]
) -> classify.SampleClass:
    lead = library.lead
    sample = lead.sample
    if study:
        sample = dataclasses.replace(sample, attributes=sample.attributes | study)
    return classify.classify_sample(species, sample, lead.run_fields())


def title_flag(flag: str) -> str:
    """StudyTitles' flag, named after its kind so the reason reads as treatment or genotype."""
    if not flag:
        return ""
    detail = flag.split(": ", 1)[1]
    genotype = "mutant or transgene" in detail or "wild-type marker" in detail
    return f"{'genotype' if genotype else 'treatment'} in titles: {detail}"


def sample_ids(libraries: Sequence[Library]) -> None:
    """`<study>_<tissue>[_<time>][_<mock|control>][_rep<N>]` from the titles, with the
    experiment appended where that repeats within the study."""
    bases = {}
    for library in libraries:
        lead = library.lead
        tissue = classify.tissue_label(library.sample.tissue if library.sample else "not stated")
        text = " ".join((lead.title, lead.sample.title, lead.library_name))
        parts = [lead.study, tissue]
        if point := TIME.search(text):
            parts.append(f"{point.group(1)}{point.group(2).lower()}")
        if control := CONTROL_WORD.search(text):
            parts.append(control.group(1).lower())
        if replicate := REPLICATE.search(text):
            parts.append(f"rep{replicate.group(1)}")
        bases[id(library)] = "_".join(parts)
    counts = Counter(bases.values())
    for library in libraries:
        base = re.sub(r"[^A-Za-z0-9_.-]+", "-", bases[id(library)])
        library.sample_id = (
            base if counts[bases[id(library)]] == 1 else f"{base}_{library.lead.accession}"
        )


def metadata(
    species: Species, experiments: Sequence[SraExperiment], chosen: Decisions
) -> list[Library]:
    libraries = group(experiments, chosen)
    duplicates = bam_duplicates(experiments)
    by_study: dict[str, list[Run]] = defaultdict(list)
    for experiment in experiments:
        if not mixed_pool(species, experiment):
            by_study[experiment.study].append(experiment.run_fields())
    titles = {study: classify.StudyTitles(species, runs) for study, runs in by_study.items()}
    for library in libraries:
        lead = library.lead
        library.sample = classify_library(species, library, chosen.studies.get(lead.study, {}))
        structures = [run_structure(run) for run in library.runs]
        chemistry = {c for c, _ in structures}
        library.chemistry = chemistry.pop() if len(chemistry) == 1 else ""
        missing = [why for _, why in structures if why and not why.startswith("no 10x")]
        missing += [] if library.runs else ["no runs in SRA"]
        value = library.sample
        study_titles = titles.get(lead.study)
        reasons = (
            missing[0] if missing else "",
            "mixed-species pool" if mixed_pool(species, lead) else "",
            not_atac(lead),
            chosen.same_as.get(lead.accession, "") or duplicates.get(lead.accession, ""),
            next(
                (
                    chosen.exclude[e.accession]
                    for e in library.experiments
                    if e.accession in chosen.exclude
                ),
                "",
            ),
            next((why for _, why in structures if why), "")
            or ("runs differ in read structure" if not library.chemistry else ""),
            "" if classify.is_reference(value) else f"genotype: {value.genotype}",
            ""
            if classify.is_untreated(value)
            else f"treatment: {value.treatment_text or value.treatment}",
            title_flag(study_titles.flag(lead.run_fields())) if study_titles else "",
            classify.transgene_flag(lead.run_fields(), study_titles.wild_type)
            if study_titles
            else "",
        )
        library.reason = next((reason for reason in reasons if reason), "")
    sample_ids(libraries)
    return libraries


def probe_instruments(
    libraries: Sequence[Library], options: Options, log: Callable[[str], None]
) -> None:
    kept = [library for library in libraries if not library.reason]
    runs = {run.accession: run.spots for library in kept for run in library.runs}
    dump = options.dump or instruments.vdb_dump(instruments.find_vdb_dump(options.vdb_dump))
    found = instruments.measure(runs, options.cache / "instruments", dump, log=log)
    for library in kept:
        library.probes = {run.accession: found[run.accession] for run in library.runs}
        failed = [p for p in library.probes.values() if p.error]
        if failed:
            library.reason = f"instrument probe failed: {failed[0].run}: {failed[0].error}"
            continue
        library.reason = instruments.library_verdict(
            [library.probes[run.accession] for run in library.runs]
        )


def build(
    species: Species,
    archive: Archive,
    out: Path,
    options: Options,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    started = time.monotonic()
    chosen = decisions(species)
    experiments = discover(species, archive, log)
    libraries = metadata(species, experiments, chosen)
    options.timings["metadata_seconds"] = round(time.monotonic() - started, 1)
    started = time.monotonic()
    if options.probe:
        probe_instruments(libraries, options, log)
    options.timings["probe_seconds"] = round(time.monotonic() - started, 1)

    rows, qc = sheet_rows(species, libraries)
    if rows:
        writers.write_sheet(out / f"{WORKFLOW}.tsv", WORKFLOW, rows)
    else:
        log(f"{species.folder}: no library passes; the workflow rejects an empty sheet")
    writers.write_runs_qc(out / f"{WORKFLOW}.runs-qc.tsv", qc)
    reasons = Counter(re.sub(r" (as|of) \S+.*| \(.*|:.*", "", row["reason_left_out"]) for row in qc)
    return {
        "species": species.folder,
        "workflow": WORKFLOW,
        "candidate_experiments": len(experiments),
        "libraries": len(libraries),
        "in_sheet": len(rows),
        "runs_in_sheet": sum(len(row["sra_runs"].split(",")) for row in rows),
        "runs": len(qc),
        "chemistry": dict(Counter(row["chemistry"] for row in rows)),
        "studies": len({library.lead.study for library in libraries if not library.reason}),
        "reasons": dict(reasons.most_common()),
        "requests": archive.requests,
        "timings": options.timings,
    }


def sheet_rows(
    species: Species, libraries: Sequence[Library]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    refs = species.references
    rows, qc = [], []
    for library in libraries:
        runs = ",".join(run.accession for run in library.runs)
        lead, value = library.lead, library.sample
        if not library.reason:
            rows.append(
                {
                    "sample_id": library.sample_id,
                    "species": species.folder,
                    "sra_runs": runs,
                    "chemistry": library.chemistry,
                    "cells_url": "-",
                    "reference_fasta": refs.fasta,
                    "reference_genes": refs.genes,
                    "organelle_contigs": refs.organelle_contigs,
                    "barcode_whitelist": next(
                        w for c, w in BARCODES.values() if c == library.chemistry
                    ),
                }
            )
        for run in library.runs:
            probe = library.probes.get(run.accession)
            qc.append(
                {
                    "run": run.accession,
                    "study": lead.study,
                    "sample_id": library.sample_id,
                    "in_sheet": "no" if library.reason else "yes",
                    "reason_left_out": library.reason,
                    "experiments": ",".join(e.accession for e in library.experiments),
                    "sra_runs": runs,
                    "biosample": lead.sample.accession,
                    "spots": str(run.spots),
                    "read_lengths": ",".join(map(str, run.lengths)),
                    "chemistry": library.chemistry or "NA",
                    "strategy": lead.strategy,
                    "source": lead.source,
                    "instruments": instruments.summary(probe) if probe else "NA",
                    "genotype": value.genotype if value else "",
                    "treatment": value.treatment if value else "",
                    "tissue": value.tissue if value else "",
                    "genotype_text": value.genotype_text if value else "",
                    "treatment_text": value.treatment_text if value else "",
                    "titles": " | ".join(
                        dict.fromkeys(
                            t for t in (lead.title, lead.library_name, lead.sample.title) if t
                        )
                    )[:300],
                    "submitted_files": ",".join(run.files[:4])[:300],
                }
            )
    return rows, qc
