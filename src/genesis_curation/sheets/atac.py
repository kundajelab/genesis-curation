"""Build a bulk atac-seq sheet for one species, with a reason for every run left out.

Candidates are ENA runs with library strategy ATAC-seq, plus the runs of a metadata-sheet tab
export (`--extra-runs`, such as the "Bulk ATAC Fable Curated" tab): INSDC runs only the tab
lists are fetched from ENA, and GSA runs (CRR) take their metadata from the tab and their
files from the GSA download servers.

Reasons are checked in this order; the first that applies is recorded:
 1. another species (the run's taxon); 2. not short reads;
 3. read files: none, a paired run served as one file, FASTQ under 1 MB, or single-end (the
    workflow needs read 2; layout comes from the served files, as in files.py);
 4. not bulk ATAC: single-cell source, ChIP/DNase/MNase selection, or the titles name a
    control or another assay (classify.ATAC_ASSAY);
 5. genotype is not the reference; 6. (treated samples are kept and recorded, issue #13);
 7. a curated exception (data/exceptions.yaml);
 8. within-study title rules (classify.StudyTitles);
 9. reads (measure/atac.py, first 200,000 pairs mapped with chromap): not measured, mapped
    fraction under 0.05, organelle fraction over 0.95, or TSS enrichment under 1.5 (genomic
    DNA and input controls score 0.97-1.16).
"""

from __future__ import annotations

import csv
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from . import classify, files, writers
from .archives import Archive, Run, Sample
from .measure import atac as checks
from .species import DATA, Species
from .tabs import tab_rows

WORKFLOW = "atac-seq"
STRATEGY = "ATAC-seq"
MIN_MAPPED = 0.05
MAX_ORGANELLE = 0.95
MIN_TSS = 1.5
# Read as genotype in addition to classify's fields: ATAC submitters often use these.
GENOTYPE_FIELDS = ("variety", "accession", "background", "genetic background")
METRIC_COLUMNS = (
    "mapped_fraction",
    "organelle_fraction",
    "tss_enrichment",
    "tss_peak",
    "nfr_fraction",
    "median_fragment",
    "genic_fraction",
    "promoter_fraction",
)


@dataclass
class Options:
    cache: Path
    measure: bool = True
    measure_from: Path | None = None
    extra_runs: Path | None = None
    references: Path | None = None
    chromap_index: Path | None = None
    tools: checks.Tools | None = None
    pairs: int = checks.PAIRS
    timings: dict[str, float] = field(default_factory=dict)


@dataclass
class Candidate:
    run: Run
    source: str
    sample_id: str = ""
    sample: classify.SampleClass | None = None
    reads: files.ReadFiles = field(default_factory=files.ReadFiles)
    reason: str = ""
    metrics: checks.Metrics | None = None


def exceptions(species: Species) -> dict[str, str]:
    data = yaml.safe_load((DATA / "exceptions.yaml").read_text()) or {}
    entries = (data.get(WORKFLOW) or {}).get(species.folder) or []
    return {run: entry["reason"] for entry in entries for run in entry["runs"]}


def reused_metrics(path: Path) -> dict[str, checks.Metrics]:
    """Metrics measured earlier: a TSV with `run` and the metric columns (any subset), such
    as a runs-qc table. Rows without a mapped fraction are skipped (not measured)."""
    found = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if checks.number(row.get("mapped_fraction") or "NA") is None:
                continue
            values = {k: row[k] for k in METRIC_COLUMNS if row.get(k)}
            found[row["run"]] = replace(checks.Metrics(row["run"]), **values)
    return found


def gsa_run(row: dict[str, str], run: str, archive: Archive) -> tuple[Run, Sample]:
    """An ENA-shaped record and sample for a GSA run, from its tab row and download folder."""
    study = row.get("study_accession", "")
    served = archive.gsa_files(study, run) if study else []
    instrument = row.get("instrument", "")
    record = {
        "run_accession": run,
        "study_accession": study,
        "sample_accession": row.get("biosample", ""),
        "library_strategy": row.get("library_strategy", ""),
        "library_source": row.get("library_source", ""),
        "library_selection": "",
        "library_layout": row.get("library_layout", ""),
        "instrument_platform": "ILLUMINA" if "illumina" in instrument.lower() else instrument,
        "read_count": row.get("spots", ""),
        "study_title": row.get("study_title", ""),
        "experiment_title": row.get("library_name", ""),
        "sample_title": row.get("sample_title") or row.get("sample_name", ""),
        "sample_description": row.get("sample_attributes", ""),
        "fastq_ftp": ";".join(url for url, _ in served),
        "fastq_bytes": ";".join(str(size) for _, size in served),
    }
    attributes = {
        key.strip().lower(): value.strip()
        for key, _, value in (
            part.partition("=") for part in record["sample_description"].split(";")
        )
        if value.strip()
    }
    return record, Sample(record["sample_accession"], attributes=attributes)


def gather(
    species: Species, archive: Archive, extra: Path | None, log: Callable[[str], None]
) -> tuple[list[Candidate], dict[str, Sample]]:
    """Candidate runs and the samples of GSA runs (ENA samples are fetched later)."""
    runs = archive.search_runs(species.tax_query, STRATEGY)
    candidates = {run["run_accession"]: Candidate(run, "ena") for run in runs}
    log(f"{species.folder}: {len(runs)} ENA {STRATEGY} runs")
    gsa_samples: dict[str, Sample] = {}
    if extra:
        tab = tab_rows(extra, species)
        insdc = [run for run in tab if run not in candidates and not run.startswith("CRR")]
        for run in archive.runs(insdc):
            candidates[run["run_accession"]] = Candidate(run, "tab")
        gsa = sorted(run for run in tab if run.startswith("CRR"))
        with ThreadPoolExecutor(archive.workers) as pool:
            found = list(pool.map(lambda run: gsa_run(tab[run], run, archive), gsa))
        for run, (record, sample) in zip(gsa, found, strict=True):
            candidates[run] = Candidate(record, "tab, GSA")
            gsa_samples[sample.accession] = sample
        missing = [run for run in insdc if run not in candidates]
        log(
            f"{species.folder}: {len(tab)} tab runs; {len(insdc) - len(missing)} more from ENA,"
            f" {len(gsa)} from GSA, {len(missing)} not in ENA"
        )
    return list(candidates.values()), gsa_samples


def first_reason(*reasons: str) -> str:
    return next((reason for reason in reasons if reason), "")


def basic_reason(species: Species, item: Candidate) -> str:
    run = item.run
    taxon = run.get("tax_id", "")
    if taxon and not species.has_taxon(taxon):
        return f"other species (taxon {taxon})"
    if run.get("instrument_platform") not in classify.SHORT_READ:
        return f"not short-read ({run.get('instrument_platform') or 'platform not stated'})"
    if not run.get("sample_accession"):
        return "no sample accession"
    item.reads = files.resolve(run)
    if item.reads.reason:
        return item.reads.reason
    return "single-end" if item.reads.layout == "single" else ""


def metadata(
    species: Species, archive: Archive, extra: Path | None, log: Callable[[str], None]
) -> list[Candidate]:
    """Every candidate run with its metadata reason ('' when kept so far)."""
    candidates, gsa_samples = gather(species, archive, extra, log)
    for item in candidates:
        item.reason = basic_reason(species, item)
    wanted = {
        item.run["sample_accession"]
        for item in candidates
        if item.run.get("sample_accession") and item.source != "tab, GSA"
    }
    samples = archive.samples(wanted) | gsa_samples
    log(f"{species.folder}: attributes of {len(samples)} samples")
    sample_titles = {
        item.run["run_accession"]: samples.get(
            item.run.get("sample_accession", ""), Sample("")
        ).title
        for item in candidates
    }
    by_study: dict[str, list[Run]] = defaultdict(list)
    for item in candidates:
        by_study[item.run["study_accession"]].append(item.run)
    experiments = {
        study: {
            classify.SEQUENCING_PREFIX.sub("", r.get("experiment_title", "")).strip() for r in rows
        }
        for study, rows in by_study.items()
    }

    def label(run: Run) -> str:
        """Sample titles, plus the experiment title when it differs within the study (a
        study-wide experiment title describes the study, not the run)."""
        experiment = classify.SEQUENCING_PREFIX.sub("", run.get("experiment_title", "")).strip()
        varied = len(experiments[run["study_accession"]]) > 1
        return classify.joined(
            experiment if varied else "",
            run.get("sample_title"),
            sample_titles[run["run_accession"]],
        )

    titles = {
        study: classify.StudyTitles(species, rows, label=label) for study, rows in by_study.items()
    }
    curated = exceptions(species)
    for item in candidates:
        run = item.run
        accession = run.get("sample_accession", "")
        sample = samples.get(accession) or Sample(accession, missing=True)
        value = classify.classify_sample(
            species,
            sample,
            run,
            treated=classify.ATAC_TREATED,
            more_genotype=GENOTYPE_FIELDS,
            more_descriptive=("sample_alias",),
        )
        if value.genotype == "wild type, accession not stated" and species.reference.search(
            label(run)
        ):
            value = replace(value, genotype=f"reference ({species.reference_genotype})")
        value = titles[run["study_accession"]].record(value, run)
        item.sample = value
        study = run["study_accession"] or accession
        item.sample_id = f"{study}_{classify.tissue_label(value.tissue)}_{run['run_accession']}"
        if item.reason:
            continue
        item.reason = first_reason(
            classify.atac_assay_flag(run, sample.title),
            "" if classify.is_reference(value) else f"genotype: {value.genotype}",
            curated.get(run["run_accession"], ""),
            titles[run["study_accession"]].flag(run),
        )
    return candidates


def read_reason(metrics: checks.Metrics | None) -> str:
    if metrics is None:
        return "not measured"
    if metrics.failed:
        return f"not measured ({metrics.failed})"
    mapped = checks.number(metrics.mapped_fraction)
    organelle = checks.number(metrics.organelle_fraction)
    tss = checks.number(metrics.tss_enrichment)
    if mapped is None or mapped < MIN_MAPPED:
        return f"few fragments (mapped fraction {metrics.mapped_fraction})"
    if organelle is not None and organelle > MAX_ORGANELLE:
        return f"mostly organelle reads ({metrics.organelle_fraction})"
    if tss is None or tss < MIN_TSS:
        return f"no accessibility signal (TSS enrichment {metrics.tss_enrichment})"
    return ""


def measure(
    species: Species,
    kept: list[Candidate],
    options: Options,
    log: Callable[[str], None],
) -> dict[str, checks.Metrics]:
    found = reused_metrics(options.measure_from) if options.measure_from else {}
    todo = {
        c.run["run_accession"]: (c.reads.read1, c.reads.read2)
        for c in kept
        if c.run["run_accession"] not in found
    }
    if not (todo and options.measure):
        return found
    if not (options.references and options.tools):
        raise ValueError("Measuring ATAC runs needs --references and chromap")
    refs = species.references
    fasta = options.references / refs.fasta
    index = options.chromap_index or checks.chromap_index(
        fasta, options.cache / "chromap" / f"{refs.fasta}.index", options.tools
    )
    organelles = set(refs.organelle_contigs.split(","))
    log(f"{species.folder}: measuring {len(todo)} runs")
    return found | checks.measure(
        todo,
        options.cache / "atac" / species.folder,
        fasta,
        index,
        options.references / refs.genes,
        organelles,
        options.tools,
        pairs=options.pairs,
        log=log,
    )


def build(
    species: Species,
    archive: Archive,
    out: Path,
    options: Options,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    started = time.monotonic()
    candidates = metadata(species, archive, options.extra_runs, log)
    kept = [c for c in candidates if not c.reason]
    log(f"{species.folder}: {len(kept)} runs kept on metadata")
    options.timings["metadata_seconds"] = round(time.monotonic() - started, 1)

    started = time.monotonic()
    found = measure(species, kept, options, log)
    options.timings["measure_seconds"] = round(time.monotonic() - started, 1)
    for item in candidates:
        item.metrics = found.get(item.run["run_accession"])
    for item in kept:
        item.reason = read_reason(item.metrics)

    rows, qc = sheet_rows(species, candidates)
    # Runs-qc first: when no run is kept, the sheet is refused but the reasons are written.
    writers.write_runs_qc(out / f"{WORKFLOW}.runs-qc.tsv", qc)
    writers.write_sheet(out / f"{WORKFLOW}.tsv", WORKFLOW, rows)
    reasons = Counter(re.sub(r" \(.*|:.*", "", row["reason_left_out"]) for row in qc)
    return {
        "species": species.folder,
        "workflow": WORKFLOW,
        "runs": len(qc),
        "in_sheet": len(rows),
        "studies": len({row["sample_id"].split("_")[0] for row in rows}),
        "sources": dict(Counter(c.source for c in candidates)),
        "reasons": dict(reasons.most_common()),
        "reused_metrics": sum(
            1 for c in kept if options.measure_from and c.metrics and c.metrics.pairs == 0
        ),
        "ena_requests": archive.requests,
        "timings": options.timings,
    }


def sheet_rows(
    species: Species, candidates: list[Candidate]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    refs = species.references
    rows, qc = [], []
    for item in candidates:
        run, value, metrics = item.run, item.sample, item.metrics
        if not item.reason:
            rows.append(
                {
                    "sample_id": item.sample_id,
                    "species": species.folder,
                    "read1_url": item.reads.read1,
                    "read2_url": item.reads.read2,
                    "reference_fasta": refs.fasta,
                    "reference_genes": refs.genes,
                    "organelle_contigs": refs.organelle_contigs,
                }
            )
        measured = {name: getattr(metrics, name) if metrics else "NA" for name in METRIC_COLUMNS}
        qc.append(
            {
                "run": run["run_accession"],
                "study": run["study_accession"],
                "sample_id": item.sample_id,
                "in_sheet": "no" if item.reason else "yes",
                "reason_left_out": item.reason,
                "source": item.source,
                "sample": run.get("sample_accession", ""),
                "read_count": run.get("read_count", ""),
                "ena_layout": run.get("library_layout", ""),
                "layout": item.reads.layout,
                "pairs_sampled": str(metrics.pairs) if metrics and metrics.pairs else "NA",
                **measured,
                "genotype": value.genotype if value else "",
                "treatment": value.treatment if value else "",
                "tissue": value.tissue if value else "",
                "genotype_text": value.genotype_text if value else "",
                "treatment_text": value.treatment_text if value else "",
                "tissue_text": value.tissue_text if value else "",
                "titles": classify.joined(run.get("experiment_title"), run.get("sample_title"))[
                    :200
                ],
            }
        )
    return rows, qc
