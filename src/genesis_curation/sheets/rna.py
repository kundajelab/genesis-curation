"""Build an rna-seq sample sheet for one species from ENA, with a reason for every run left out.

Reasons are checked in this order; the first that applies is recorded:
 1. not bulk short-read RNA-seq (library source, platform, small-RNA size selection);
 2. the sample's deepest run has under 1M reads;
 3. genotype is not the reference genotype (treated samples are kept, issue #13);
 5. the run's titles or library selection name another assay (classify.ASSAYS);
 6. a curated exception (data/exceptions.yaml);
 7. read files: paired run served as one file, no files, FASTQ under 1 MB (files.py);
 8. within-study title rules; 9. a transgene or mutant named in the titles;
10. a transgene, promoter fusion or lower-case mutant allele named in the sample alias;
11. duplicate submission of another kept run (same first reads);
12. strandedness: download failed, too few reads on genes, unclear, or not measured.
"""

from __future__ import annotations

import csv
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import adjudicate as adjudication
from . import classify, files, writers
from .archives import Archive, Run, Sample
from .measure import duplicates, strand
from .species import DATA, Species

WORKFLOW = "rna-seq"
MIN_SAMPLE_READS = 1_000_000
REUSED = {
    "too_few_reads": "too few reads on genes",
    "unclear": "strandedness unclear",
}


@dataclass
class Options:
    cache: Path
    measure: bool = True
    strandedness_from: Path | None = None
    references: Path | None = None
    star_index: Path | None = None
    tools: strand.Tools | None = None
    probe_duplicates: bool = True
    adjudicate: adjudication.Settings | None = None
    timings: dict[str, float] = field(default_factory=dict)


@dataclass
class Candidate:
    run: Run
    sample_id: str = ""
    sample: classify.SampleClass | None = None
    reads: files.ReadFiles = field(default_factory=files.ReadFiles)
    reason: str = ""
    call: strand.StrandCall | None = None
    adjudication: tuple[str, str] | None = None


TISSUE_LEFT_OUT = "tissue: cell culture (protoplasts, callus or cultured cells)"


def exceptions(species: Species) -> dict[str, str]:
    data = yaml.safe_load((DATA / "exceptions.yaml").read_text()) or {}
    entries = (data.get(WORKFLOW) or {}).get(species.folder) or []
    return {run: entry["reason"] for entry in entries for run in entry["runs"]}


def reused_calls(path: Path) -> dict[str, strand.StrandCall]:
    """Strandedness measured earlier: a TSV with `run` and `strandedness` columns (and
    optionally reads_sampled, forward_fraction, gene_fraction), such as a runs-qc table."""
    calls = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            value = REUSED.get(row.get("strandedness", ""), row.get("strandedness", ""))
            if not value or value == "NA":
                continue
            reads = row.get("reads_sampled", "")
            calls[row["run"]] = strand.StrandCall(
                run=row["run"],
                strandedness=value,
                reads_sampled=int(reads) if reads.isdigit() else 0,
                forward_fraction=row.get("forward_fraction") or "NA",
                gene_fraction=row.get("gene_fraction") or "NA",
            )
    return calls


def metadata(species: Species, archive: Archive, log: Callable[[str], None]) -> list[Candidate]:
    """Every ENA RNA-Seq run of the species with its metadata reason ('' when kept so far)."""
    runs = archive.search_runs(species.tax_query, "RNA-Seq")
    log(f"{species.folder}: {len(runs)} ENA RNA-Seq runs")
    candidates = [Candidate(run, reason=classify.usable(run)) for run in runs]
    by_sample: dict[str, list[Candidate]] = defaultdict(list)
    for item in candidates:
        if not item.reason:
            by_sample[item.run["sample_accession"]].append(item)
    samples = archive.samples(by_sample)
    log(f"{species.folder}: attributes of {len(samples)} samples")
    by_study: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        by_study[run["study_accession"]].append(run)
    titles = {study: classify.StudyTitles(species, rows) for study, rows in by_study.items()}
    curated = exceptions(species)
    # A sample's own genotype field outweighs title words only where it tells the study's
    # samples apart (WT next to pgdh3-1); a study whose every sample says "WT" may be
    # stating the background of a transgenic line (PRJNA684618).
    stated = {
        accession: classify.stated_genotype(samples.get(accession) or Sample(accession))
        for accession in by_sample
    }
    mixed_studies = {
        item.run["study_accession"]
        for accession, items in by_sample.items()
        for item in items
        if stated[accession] and not classify.states_wild_type(stated[accession])
    }
    for accession, items in by_sample.items():
        depth = max(int(c.run["read_count"] or 0) for c in items)
        sample = samples.get(accession) or Sample(accession, missing=True)
        for item in items:
            run = item.run
            # Classify each run: one BioSample can hold both control and treated runs,
            # told apart only by their experiment titles (PRJNA990533).
            value = titles[run["study_accession"]].record(
                classify.classify_sample(species, sample, run), run
            )
            study = run["study_accession"] or accession
            item.sample = value
            item.sample_id = (
                f"{study}_{classify.tissue_label(value.tissue)}_{accession}_{run['run_accession']}"
            )
            item.reads = files.resolve(run)
            item.reason = next(
                (
                    reason
                    for reason in (
                        "sample under 1M reads" if depth < MIN_SAMPLE_READS else "",
                        "" if classify.is_reference(value) else f"genotype: {value.genotype}",
                        classify.assay_flag(run),
                        curated.get(run["run_accession"], ""),
                        item.reads.reason,
                        titles[run["study_accession"]].flag(run),
                        classify.transgene_flag(
                            run,
                            titles[run["study_accession"]].wild_type,
                            stated[accession] if run["study_accession"] in mixed_studies else "",
                        ),
                        classify.alias_flag(run, titles[run["study_accession"]].wild_type),
                        # Protoplasts, callus and cultured cells are not plant tissue (policy
                        # 2026-11-19); checked last, since any other reason is more specific.
                        TISSUE_LEFT_OUT
                        if value.tissue == "cell culture" or classify.cell_culture_flag(run)
                        else "",
                    )
                    if reason
                ),
                "",
            )
    return candidates


def alias_pool(species: Species, candidates: list[Candidate]) -> set[str]:
    """Kept runs whose alias lacks the wild-type marker other aliases of their study carry."""
    reference = classify.StudyTitles(species, []).wild_type
    by_study: dict[str, list[Run]] = defaultdict(list)
    for item in candidates:
        if not item.reason:
            by_study[item.run["study_accession"]].append(item.run)
    found: set[str] = set()
    for runs in by_study.values():
        found |= classify.alias_candidates(runs, reference)
    return found


def build(
    species: Species,
    archive: Archive,
    out: Path,
    options: Options,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    started = time.monotonic()
    candidates = metadata(species, archive, log)
    kept = [c for c in candidates if c.sample and not c.reason]
    log(f"{species.folder}: {len(kept)} runs kept on metadata")
    if options.probe_duplicates:
        found = duplicates.find(
            {c.run["run_accession"]: c.run["read_count"] for c in kept},
            {c.run["run_accession"]: c.reads.read1 for c in kept},
            options.cache,
        )
        for item in kept:
            if item.run["run_accession"] in found:
                item.reason = (
                    f"duplicate submission of {found[item.run['run_accession']]} (same reads)"
                )
    options.timings["metadata_seconds"] = round(time.monotonic() - started, 1)

    reviewed: dict[str, Any] = {}
    if options.adjudicate:
        started = time.monotonic()
        pool = [c for c in candidates if c.sample]
        only = None
        if options.adjudicate.scope == "alias":
            only = alias_pool(species, pool)
            log(f"{species.folder}: {len(only)} kept runs named against wild-type aliases")
        reviewed = adjudication.apply(
            pool, species, WORKFLOW, archive, options.adjudicate, log, only=only
        )
        options.timings["adjudication_seconds"] = round(time.monotonic() - started, 1)

    started = time.monotonic()
    kept = [c for c in candidates if c.sample and not c.reason]
    calls = reused_calls(options.strandedness_from) if options.strandedness_from else {}
    unmeasured = {c.run["run_accession"]: c.reads.read1 for c in kept}
    unmeasured = {run: url for run, url in unmeasured.items() if run not in calls}
    if unmeasured and options.measure:
        if not (options.references and options.tools):
            raise ValueError("Measuring strandedness needs --references and the tool paths")
        refs = species.references
        index = options.star_index or options.references / refs.star_index
        calls |= strand.measure(
            unmeasured,
            options.cache / "strandedness" / species.folder,
            index,
            options.references / refs.annotation,
            options.tools,
            log=log,
        )
    options.timings["strandedness_seconds"] = round(time.monotonic() - started, 1)
    for item in kept:
        item.call = calls.get(item.run["run_accession"])
        value = item.call.strandedness if item.call else "strandedness not measured"
        if value not in strand.CALLS:
            item.reason = value

    rows, qc = sheet_rows(species, candidates)
    writers.write_runs_qc(out / f"{WORKFLOW}.runs-qc.tsv", qc)
    writers.write_sheet(out / f"{WORKFLOW}.tsv", WORKFLOW, rows)
    reasons = Counter(
        re.sub(r" of \S+", "", row["reason_left_out"].split(" (")[0].split(":")[0]) for row in qc
    )
    summary = {
        "species": species.folder,
        "workflow": WORKFLOW,
        "runs": len(qc),
        "in_sheet": len(rows),
        "paired": sum(row["read2_url"] != "-" for row in rows),
        "studies": len({row["sample_id"].split("_")[0] for row in rows}),
        "strandedness": dict(Counter(row["strandedness"] for row in rows)),
        "reasons": dict(reasons.most_common()),
        "reused_strandedness": len(calls) if options.strandedness_from else 0,
        **({"adjudication": reviewed} if reviewed else {}),
        "ena_requests": archive.requests,
        "timings": options.timings,
    }
    return summary


def sheet_rows(
    species: Species, candidates: list[Candidate]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    refs = species.references
    rows, qc = [], []
    reviewed = any(c.adjudication for c in candidates)
    for item in candidates:
        run, value, call = item.run, item.sample, item.call
        if not item.reason:
            assert call is not None
            rows.append(
                {
                    "sample_id": item.sample_id,
                    "species": species.folder,
                    "read1_url": item.reads.read1,
                    "read2_url": item.reads.read2,
                    "strandedness": call.strandedness,
                    "reference_fasta": refs.fasta,
                    "reference_annotation": refs.annotation,
                    "organelle_contigs": refs.organelle_contigs,
                }
            )
        qc.append(
            {
                "run": run["run_accession"],
                "study": run["study_accession"],
                "sample_id": item.sample_id,
                "in_sheet": "no" if item.reason else "yes",
                "reason_left_out": item.reason,
                "sample": run["sample_accession"],
                "read_count": run["read_count"],
                "ena_layout": run["library_layout"],
                "layout": item.reads.layout,
                "strandedness": call.strandedness if call else "NA",
                "reads_sampled": str(call.reads_sampled) if call else "NA",
                "forward_fraction": call.forward_fraction if call else "NA",
                "gene_fraction": call.gene_fraction if call else "NA",
                "genotype": value.genotype if value else "",
                "treatment": value.treatment if value else "",
                "tissue": value.tissue if value else "",
                "genotype_text": value.genotype_text if value else "",
                "treatment_text": value.treatment_text if value else "",
                "tissue_text": value.tissue_text if value else "",
                **(
                    {
                        "adjudication": item.adjudication[0] if item.adjudication else "",
                        "adjudication_reason": item.adjudication[1] if item.adjudication else "",
                    }
                    if reviewed
                    else {}
                ),
            }
        )
    return rows, qc
