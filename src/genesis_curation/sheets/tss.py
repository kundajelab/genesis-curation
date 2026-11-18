"""Build a tss-seq sample sheet for one species from ENA, with a reason for every run left out.

Candidates are runs of the species whose library selection is CAGE or whose study, experiment
or sample title or library name names a TSS method, plus every other run of those studies (so
RNA-seq and control runs inside a TSS study are seen and explained). Reasons are checked in this
order; the first that applies is recorded:
 1. not short-read transcriptomic, or no sample accession;
 2. an unsupported 5'-end method (nanoPARE/degradome, NAD-RNA capture, TIF-seq, PEAT, nascent
    capped RNA, differential TSS RNA-seq) or a cap-less control library;
 3. no supported method (the run's own text names another assay such as RNA-seq or small RNA,
    or nothing names a TSS method); the method comes from the run's text, then the study title,
    then a CAGE library selection, then a per-study entry in `data/tss.yaml`;
 4. genotype (sample attributes, alleles and mutants in titles, or a missing wild-type marker
    that other titles of the study carry); 5. treatment named in the titles or attributes;
 6. a per-study exclusion in `data/tss.yaml`; 7. read files (files.py; the method's mates);
 8. duplicate submission of another kept run (same first reads);
 9. TSS signal of the first 20,000 reads at the chosen trim: at least 10,000 reads sampled,
    at least 20% uniquely mapped, at least 30% of 5' ends within 100 bases of an annotated
    transcript start.
trim_5p is the trim that puts the offset mode of 5' ends at 0 (-1 for cap-trapping methods,
whose added G the workflow removes); adapter_3p is the most frequent known 3' adapter in the
reads (TruSeq when none reaches 1%). See measure/tss.py.
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import classify, files, writers
from .archives import RUN_FIELDS, Archive, Run, Sample
from .measure import duplicates
from .measure import tss as signal
from .species import DATA, Species

WORKFLOW = "tss-seq"
MIN_SAMPLED, MIN_UNIQUE, MIN_NEAR = 10_000, 0.2, 0.3
FIELDS = (*RUN_FIELDS, "library_name")
L, R = classify.L, classify.R

# Text searched in ENA (case-insensitive, as substrings) to find TSS studies.
SEARCH_TERMS = (
    "TSS",
    "transcription start",
    "start site",
    "CAGE",
    "csRNA",
    "cap analysis",
    "capped",
    "cap-trap",
    "cap trap",
    "oligo-cap",
    "oligocap",
    "RAMPAGE",
    "Smar2C2",
    "5' cap",
    # Unsupported 5'-end methods, so that their runs are seen and explained. ENA wildcards
    # match nothing once the term holds "+" or "'", so NAD+ capture is found by its "NAD_"
    # sample names and 5'GRO-seq is not searched.
    "nanoPARE",
    "NAD_",
    "GRO-cap",
)
SEARCH_FIELDS = ("study_title", "experiment_title", "sample_title", "library_name")

# Supported methods (the schema's), first match wins. Specific protocol names come before the
# generic "TSS-seq", which some studies use for nanoCAGE or oligo-capping libraries alike.
METHODS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, re.compile(L + pattern + R, re.I))
    for method, pattern in (
        ("Smar2C2", r"smart?-?2-?c-?2"),
        ("RAMPAGE", r"rampage"),
        ("nAnT-iCAGE", r"nant-?icage"),
        ("csRNA-seq", r"csrna(-?seq)?"),
        ("oligo-capping", r"oligo-?cap\w*"),
        (
            "CAGE",
            r"(nano)?cage(-?(seq|xl|\d+))?|cap[-_ ]?trap\w*|cap analysis of gene expression",
        ),
        ("TSS-seq", r"tss[-_ ]?seq\w*|tss sequencing"),
    )
)
CAP_TRAPPING = {"CAGE", "nAnT-iCAGE"}
TEMPLATE_SWITCHING = {"RAMPAGE", "Smar2C2"}
# 5'-end methods the workflow does not support, named in a run's text or its study title.
UNSUPPORTED: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(L + pattern + R, re.I), reason)
    for pattern, reason in (
        (
            r"nanopare|pare|degradome|gmuct|uncapped",
            "5' ends of capped and uncapped RNA (nanoPARE, degradome); not a supported method",
        ),
        (
            r"nad\+?[-_ ]?(capture-?seq|captureseq|tag-?seq|seq|rnas?|capped)|nad\+?",
            "NAD-RNA capture, not 5' cap TSS mapping",
        ),
        (r"tif-?seq", "TIF-seq (paired 5'-3' ends of full transcripts); not a supported method"),
        (
            r"peat",
            "PEAT (paired-end analysis of TSSs, other read structure); not a supported method",
        ),
        (
            r"5'?[-_ ]?gro-?seq|gro-?cap|pro-?cap|start-?seq",
            "nascent capped RNA (5'GRO-seq, GRO-cap); not a supported method",
        ),
        (
            r"tss[-_ ]rna-?seq|d-?rna-?seq",
            "differential TSS RNA-seq (TAP/TEX); not a supported method",
        ),
    )
)
CAP_LESS = re.compile(
    L + r"cap[-_ ]?less|no[-_ ]?cap|minus[-_ ]?cap|without[-_ ]cap|non[-_ ]?cap\w*" + R, re.I
)
RNA_SEQ = re.compile(L + r"m?rna-?seq\w*|rna-?sequencing|transcriptome" + R, re.I)
# Treatments in titles. Light and dark conditions of photomorphogenesis studies count only by
# colour (the dark or white-light samples are the controls), and only minutes count as a time
# point (hours in seed and seedling titles are developmental stages).
TREATMENT = re.compile(
    L + r"heat|cold|chill\w*|freez\w*|drought|dehydrat\w*|salt|nacl|osmotic|mannitol|aba|"
    r"mej?a|jasmon\w*|salicyl\w*|flg22|elf18|pamp|chitin|infect\w*|inocul\w*|pathogen|"
    r"blue|red|far-?red|uv-?[ab]?|high-?light|stress\w*|treat\w*|\d+ ?min" + R,
    re.I,
)
CONTROL = re.compile(L + r"mock|control|untreated|0 ?min" + R, re.I)
SUFFIX_STRATEGY = re.compile(r"\s+(rna-seq|other|ncrna-seq|mirna-seq|cloneend|fl-cdna)\s*$", re.I)


@dataclass
class Options:
    cache: Path
    measure: bool = True
    measure_from: Path | None = None
    references: Path | None = None
    star_index: Path | None = None
    tools: signal.Tools | None = None
    probe_duplicates: bool = True
    timings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Signal:
    """TSS signal of a run at its chosen trim, measured now or reused from a TSV."""

    reads_sampled: int
    unique_fraction: float
    near_close: float | None
    near: float
    offset_mode: str
    trim: int
    adapter: str
    first_g: str = "NA"
    source: str = "measured"


@dataclass
class Candidate:
    run: Run
    study: str
    method: str = ""
    method_source: str = ""
    sample_id: str = ""
    sample: classify.SampleClass | None = None
    reads: files.ReadFiles = field(default_factory=files.ReadFiles)
    reason: str = ""
    signal: Signal | None = None


def study_of(run: Run) -> str:
    return run.get("secondary_study_accession") or run.get("study_accession") or ""


def run_text(run: Run, species: Species) -> str:
    """The run's own text: experiment title without the instrument prefix and the GEO
    "<species> <strategy>" suffix, the sample title and the library name."""
    title = classify.SEQUENCING_PREFIX.sub("", run.get("experiment_title", ""))
    title = SUFFIX_STRATEGY.sub("", title)
    title = re.sub(
        r"\s*" + re.escape(species.scientific_name) + r"\s*$", "", title.strip(), flags=re.I
    )
    return " | ".join(
        value
        for value in (title, run.get("sample_title", ""), run.get("library_name", ""))
        if value
    )


def named_method(text: str) -> str:
    return next((method for method, pattern in METHODS if pattern.search(text)), "")


def unsupported(text: str) -> str:
    return next((reason for pattern, reason in UNSUPPORTED if pattern.search(text)), "")


def without_methods(text: str) -> str:
    for _, pattern in METHODS:
        text = pattern.sub(" ", text)
    return text


def other_assay(text: str) -> str:
    """Another assay named in a run's own text (TSS method names removed first, so that
    "Cap_trap" is not a translatome TRAP), or ''."""
    rest = without_methods(text)
    for pattern, label in classify.ASSAYS:
        if label != "TSS" and pattern.search(rest):
            return label
    return "RNA-seq" if RNA_SEQ.search(rest) else ""


def usable(run: Run) -> str:
    if run.get("library_source") != "TRANSCRIPTOMIC":
        return f"library source {run.get('library_source') or 'not stated'}"
    if run.get("instrument_platform") not in classify.SHORT_READ:
        return f"not short-read ({run.get('instrument_platform') or 'platform not stated'})"
    if not run.get("sample_accession"):
        return "no sample accession"
    return ""


def method_of(run: Run, species: Species, overrides: dict[str, Any]) -> tuple[str, str, str]:
    """(method, where it came from, reason the run is not a supported TSS library)."""
    text = run_text(run, species)
    study_title = run.get("study_title", "")
    entry = overrides.get(study_of(run)) or overrides.get(run.get("study_accession", "")) or {}
    if entry.get("runs") and run["run_accession"] not in entry["runs"]:
        entry = {}
    if reason := unsupported(text):
        return "", "", reason
    if CAP_LESS.search(text):
        return "", "", "cap-less control library (no cap selection)"
    method, source = named_method(text), "run text"
    if not method:
        if assay := other_assay(text):
            return "", "", f"not a TSS library ({assay} run in the study)"
        if reason := unsupported(study_title):
            return "", "", reason
        method, source = named_method(study_title), "study title"
    if not method and run.get("library_selection") == "CAGE":
        if RNA_SEQ.search(study_title):
            return "", "", "not a TSS library (CAGE selection, but the study is RNA-seq)"
        method, source = "CAGE", "library selection"
    if entry.get("method"):
        method, source = entry["method"], "data/tss.yaml"
    if not method:
        return "", "", "not a TSS library (no TSS method named)"
    return method, source, ""


class StudyTitles:
    """Genotype and treatment named in a run's own text, with the study's other runs as
    context: a run is a mutant when it names an allele or mutant, or when other runs of its
    study carry a wild-type marker that its own text lacks."""

    def __init__(self, species: Species, texts: list[str]) -> None:
        self.wild_type = re.compile(
            f"{classify.TITLE_WILD_TYPE}|{species.title_marker.pattern}", re.I
        )
        self.study_wild_type = any(self.wild_type.search(text) for text in texts)

    def genotype(self, text: str) -> str:
        mutant = classify.MUTANT.search(text)
        if mutant:
            return f"genotype: mutant ({mutant.group(0)})"
        if self.study_wild_type and not self.wild_type.search(text):
            return "genotype: no wild-type marker, which other runs of the study have"
        return ""


def treatment(text: str) -> str:
    """A treatment named in titles or a treatment attribute, unless a control is named too."""
    found = TREATMENT.search(text)
    return f"treatment: {found.group(1)}" if found and not CONTROL.search(text) else ""


def genotype_reason(species: Species, value: classify.SampleClass, text: str) -> str:
    """The sample's genotype class, where a line derived from the reference genotype (such as
    a cell line) counts as the reference and a genotype nobody states leaves the run out."""
    line = species.reference_lines
    if line and line.search(f"{value.genotype_text} | {text}"):
        return ""
    if value.genotype in ("mutant / transgenic", "other accession / cultivar", "not stated"):
        return f"genotype: {value.genotype}"
    return ""


def overrides(species: Species) -> dict[str, Any]:
    data = yaml.safe_load((DATA / "tss.yaml").read_text()) or {}
    return data.get(species.folder) or {}


def discover(species: Species, archive: Archive, log: Callable[[str], None]) -> list[Run]:
    """Runs of TSS studies of the species (see the module docstring)."""
    taxon = species.tax_query
    text = " OR ".join(f'{f}="*{t}*"' for f in SEARCH_FIELDS for t in SEARCH_TERMS)
    found: dict[str, Run] = {}
    for query in (f'{taxon} AND library_selection="CAGE"', f"{taxon} AND ({text})"):
        for run in archive.search(query, FIELDS):
            found[run["run_accession"]] = run
    studies = sorted({run["study_accession"] for run in found.values() if run["study_accession"]})
    for start in range(0, len(studies), 50):
        chunk = " OR ".join(f'study_accession="{s}"' for s in studies[start : start + 50])
        for run in archive.search(f"{taxon} AND ({chunk})", FIELDS):
            found.setdefault(run["run_accession"], run)
    log(f"{species.folder}: {len(found)} runs in {len(studies)} candidate TSS studies")
    return sorted(found.values(), key=lambda run: run["run_accession"])


def metadata(species: Species, archive: Archive, log: Callable[[str], None]) -> list[Candidate]:
    runs = discover(species, archive, log)
    table = overrides(species)
    candidates = []
    for run in runs:
        item = Candidate(run, study_of(run))
        item.reason = usable(run)
        if not item.reason:
            item.method, item.method_source, item.reason = method_of(run, species, table)
        candidates.append(item)
    by_sample: dict[str, list[Candidate]] = defaultdict(list)
    for item in candidates:
        if not item.reason:
            by_sample[item.run["sample_accession"]].append(item)
    samples = archive.samples(by_sample)
    by_study: dict[str, list[str]] = defaultdict(list)
    for item in candidates:
        by_study[item.study].append(run_text(item.run, species))
    titles = {study: StudyTitles(species, texts) for study, texts in by_study.items()}
    for accession, items in by_sample.items():
        sample = samples.get(accession) or Sample(accession, missing=True)
        for item in items:
            run = item.run
            value = classify.classify_sample(species, sample, run)
            entry = table.get(item.study) or {}
            text = run_text(run, species)
            item.sample = value
            item.sample_id = (
                f"{item.study}_{classify.tissue_label(value.tissue)}_{run['run_accession']}"
            )
            item.reads = tss_files(run, item.method)
            item.reason = next(
                (
                    reason
                    for reason in (
                        genotype_reason(species, value, text),
                        titles[item.study].genotype(text),
                        treatment(text),
                        treatment(value.treatment_text),
                        entry.get("reason", "")
                        if not entry.get("runs") or run["run_accession"] in entry["runs"]
                        else "",
                        item.reads.reason,
                    )
                    if reason
                ),
                "",
            )
    return candidates


def tss_files(run: Run, method: str) -> files.ReadFiles:
    """The file whose reads start at the TSS: read 1, RAMPAGE's read 2, both mates for
    Smar2C2."""
    reads = files.resolve(run)
    if reads.reason:
        return reads
    if method == "Smar2C2":
        return reads if reads.read2 != "-" else files.ReadFiles(reason="Smar2C2 run without mates")
    if method == "RAMPAGE":
        if reads.read2 == "-":
            return files.ReadFiles(reason="RAMPAGE run without read 2")
        return files.ReadFiles(reads.read2)
    return files.ReadFiles(reads.read1)


def reused_signals(path: Path) -> dict[str, Signal]:
    """Signals measured earlier: a runs-qc TSV with run, reads_sampled,
    mapped_unique_fraction, near_TSS_100 and trim_5p/adapter_3p (and optionally near_TSS_10,
    offset_mode), such as the curated runs-qc table or an earlier build's."""
    found = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            reads = row.get("reads_sampled", "")
            if not reads.isdigit() or not row.get("trim_5p"):
                continue
            try:
                unique = float(row["mapped_unique_fraction"])
                near = float(row["near_TSS_100"])
            except KeyError, ValueError:
                continue
            close = row.get("near_TSS_10", "")
            found[row["run"]] = Signal(
                reads_sampled=int(reads),
                unique_fraction=unique,
                near_close=float(close) if re.fullmatch(r"[0-9.]+", close) else None,
                near=near,
                offset_mode=row.get("offset_mode") or "NA",
                trim=0 if row["trim_5p"] == "-" else int(row["trim_5p"]),
                adapter=row.get("adapter_3p") or signal.TRUSEQ,
                first_g=row.get("first_g") or "NA",
                source="reused",
            )
    return found


def target_offset(method: str) -> int:
    return -1 if method in CAP_TRAPPING else 0


def measured_signal(method: str, value: signal.Measurement) -> Signal | None:
    if not value.profile.reads or not value.sweep:
        return None
    trim = signal.choose_trim(value.sweep, target_offset(method))
    at = value.sweep[trim]
    # Smar2C2 maps only the reads holding the primer, UMI and AT (of both mates).
    mapped = value.profile.reads
    if method == "Smar2C2":
        mapped = round(value.profile.reads * value.profile.smart_rate)
    return Signal(
        reads_sampled=value.profile.reads,
        unique_fraction=round(at.unique_fraction(mapped), 3),
        near_close=round(at.near_close / at.unique, 3) if at.unique else None,
        near=round(at.near_fraction, 3),
        offset_mode="NA" if at.offset_mode is None else str(at.offset_mode),
        trim=trim,
        adapter=value.profile.adapter,
        first_g=f"{value.profile.first_g:.3f}",
    )


def signal_reason(item: Candidate) -> str:
    value = item.signal
    if value is None:
        return "TSS signal not measured"
    if value.reads_sampled < MIN_SAMPLED:
        return f"too few reads sampled ({value.reads_sampled})"
    if value.unique_fraction < MIN_UNIQUE:
        return f"few uniquely mapped reads ({value.unique_fraction:.2f})"
    if value.near < MIN_NEAR:
        return f"no TSS signal (near_TSS_100 {value.near:.3f})"
    return ""


def build(
    species: Species,
    archive: Archive,
    out: Path,
    options: Options,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    started = time.monotonic()
    candidates = metadata(species, archive, log)
    kept = [c for c in candidates if not c.reason]
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

    started = time.monotonic()
    kept = [c for c in kept if not c.reason]
    signals = reused_signals(options.measure_from) if options.measure_from else {}
    todo = [c for c in kept if c.run["run_accession"] not in signals]
    if todo and options.measure:
        if not (options.references and options.tools):
            raise ValueError("Measuring TSS signal needs --references and --star")
        refs = species.references
        index = options.star_index or options.references / refs.star_index
        jobs = [
            signal.Job(
                run=c.run["run_accession"],
                urls=(c.reads.read1, c.reads.read2) if c.method == "Smar2C2" else (c.reads.read1,),
                local=c.method in TEMPLATE_SWITCHING,
                smart=c.method == "Smar2C2",
            )
            for c in todo
        ]
        measured = signal.measure(
            jobs,
            options.cache / "tss" / species.folder,
            index,
            options.references / refs.annotation,
            options.tools,
            log=log,
        )
        for item in todo:
            value = measured_signal(item.method, measured[item.run["run_accession"]])
            if value:
                signals[item.run["run_accession"]] = value
            else:
                item.reason = "download failed"
    options.timings["signal_seconds"] = round(time.monotonic() - started, 1)
    for item in kept:
        item.signal = signals.get(item.run["run_accession"])
        item.reason = item.reason or signal_reason(item)

    rows, qc = sheet_rows(species, candidates)
    writers.write_runs_qc(out / f"{WORKFLOW}.runs-qc.tsv", qc)
    if rows:
        writers.write_sheet(out / f"{WORKFLOW}.tsv", WORKFLOW, rows)
    else:  # the schema wants at least one sample; an empty sheet is only a header
        writers.write_tsv(out / f"{WORKFLOW}.tsv", writers.header(WORKFLOW), [])
    reasons = Counter(re.sub(r" \(.*| of \S+", "", row["reason_left_out"]) for row in qc)
    return {
        "species": species.folder,
        "workflow": WORKFLOW,
        "runs": len(qc),
        "in_sheet": len(rows),
        "studies": len({row["sample_id"].split("_")[0] for row in rows}),
        "methods": dict(Counter(row["method"] for row in rows)),
        "trims": dict(Counter(row["trim_5p"] for row in rows)),
        "reasons": dict(reasons.most_common()),
        "reused_signals": sum(1 for c in kept if c.signal and c.signal.source == "reused"),
        "ena_requests": archive.requests,
        "timings": options.timings,
    }


def sheet_rows(
    species: Species, candidates: list[Candidate]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    refs = species.references
    rows, qc = [], []
    for item in candidates:
        run, value, measured = item.run, item.sample, item.signal
        trim = "NA" if measured is None else str(measured.trim) if measured.trim else "-"
        if not item.reason:
            assert measured is not None
            rows.append(
                {
                    "sample_id": item.sample_id,
                    "species": species.folder,
                    "read1_url": item.reads.read1,
                    "read2_url": item.reads.read2,
                    "method": item.method,
                    "trim_5p": trim,
                    "adapter_3p": measured.adapter,
                    "reference_fasta": refs.fasta,
                    "reference_annotation": refs.annotation,
                    "organelle_contigs": refs.organelle_contigs,
                }
            )
        qc.append(
            {
                "run": run["run_accession"],
                "study": item.study,
                "sample_id": item.sample_id,
                "in_sheet": "no" if item.reason else "yes",
                "reason_left_out": item.reason,
                "sample": run.get("sample_accession", ""),
                "read_count": run.get("read_count", ""),
                "ena_layout": run.get("library_layout", ""),
                "layout": item.reads.layout,
                "library_selection": run.get("library_selection", ""),
                "method": item.method,
                "method_source": item.method_source,
                "trim_5p": trim,
                "adapter_3p": measured.adapter if measured else "NA",
                "reads_sampled": str(measured.reads_sampled) if measured else "NA",
                "mapped_unique_fraction": f"{measured.unique_fraction:.3f}" if measured else "NA",
                "near_TSS_10": (
                    f"{measured.near_close:.3f}"
                    if measured and measured.near_close is not None
                    else "NA"
                ),
                "near_TSS_100": f"{measured.near:.3f}" if measured else "NA",
                "offset_mode": measured.offset_mode if measured else "NA",
                "first_g": measured.first_g if measured else "NA",
                "signal_source": measured.source if measured else "",
                "genotype": value.genotype if value else "",
                "treatment": value.treatment if value else "",
                "tissue": value.tissue if value else "",
                "title": run_text(run, species)[:200],
            }
        )
    return rows, qc


def options(args: argparse.Namespace, cache: Path) -> Options:
    """Options from `genesis sheets build` arguments."""
    measure = not args.no_measure
    return Options(
        cache=cache,
        measure=measure,
        measure_from=args.measure_from,
        references=args.references,
        star_index=args.star_index,
        tools=signal.Tools.find(args.star, args.threads) if measure else None,
        probe_duplicates=not args.no_probe,
    )
