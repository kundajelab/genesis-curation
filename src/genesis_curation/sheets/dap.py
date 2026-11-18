"""Build a dap-seq sample sheet for one species from ENA, with a reason for every run left out.

Candidates are ENA runs of the species whose experiment, sample, library or study titles
contain "DAP" or "affinity purif", plus every run of each study that looks like DAP-seq
and of the studies listed for the species in `data/dap_studies.yaml`. Reasons, first that
applies:
 1. in a sheet given with --skip-runs-in (e.g. a repository sheet);
 2. not in a DAP-seq study ("DAP" there means days after pollination, or is part of a word);
 3. the study is excluded (dap_studies.yaml), or its bait is not a plant TF;
 4. DNA of another species (the run's taxon);
 5. another assay of the study (ChIP-seq, RNA-seq, ...);
 6. a study rule leaves the library out; read 2 uploaded as its own run;
 7. another run of the same library (the sheet uses the largest run);
 8. read files (files.py), libraries under 1M reads;
 9. no control in the study, its control is not usable, or a control nobody uses;
10. the first 100 reads differ in length (reads trimmed before upload; probes.py).
Libraries are paired with controls by pairing.py.
"""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..web.mining_sources import ENA as PORTAL
from . import files, pairing, probes, writers
from .archives import RUN_FIELDS, Archive, Run, parse_tsv
from .pairing import Library, StudyRule, Unit
from .species import Species
from .tables import read_tsv, runs_in

WORKFLOW = "dap-seq"
MIN_LIBRARY_READS = 1_000_000
FIELDS = (*RUN_FIELDS, "library_name")
SEARCHES = (
    ("experiment_title", "*DAP*"),
    ("sample_title", "*DAP*"),
    ("library_name", "*DAP*"),
    ("study_title", "*DAP*"),
    ("experiment_title", "*affinity purif*"),
    ("study_title", "*affinity purif*"),
)
# DAP-seq named as a word: not "adaptation" or "DAPI", and not days after pollination
# ("16DAP", "DAP6", "16 DAP", "DAP 6"; a bare number before or after, see names_dap).
DAP = re.compile(
    r"(?<![a-z0-9])(?:amp|seq[-_ ]?)?dap(?:[-_ ]?seq)?(?![a-z0-9])(?! \d)|"
    r"dna affinity purification",
    re.I,
)
DAYS_BEFORE = re.compile(r"(?:^|[^a-z0-9.])\d+(?:\.\d+)?[ _-]?$", re.I)
OTHER_ASSAY = re.compile(
    r"(?<![a-z])chip(?![a-z])|"
    r"(?<![a-z0-9])(?!(?:amp|seq)?dap[-_ ]?seq|(?:hi|next|mi|nova|i|bgi|dnb)seq)"
    r"[a-z0-9]*[a-z][a-z0-9]*[-_ ]?seq"
    r"(?![a-z])|(?<![a-z])mrna|(?<![a-z])atac|bisulfite|wgbs|cut ?& ?(run|tag)|"
    r"(?<![a-z])hi-?c(?![a-z])|small rna",
    re.I,
)
DAYS_REASON = "not DAP-seq: 'DAP' means days after pollination/anthesis or is part of another word"


@dataclass
class Options:
    cache: Path
    probe: bool = True
    skip_runs_in: Sequence[Path] = ()
    fetch_reads: probes.Fetch = probes.http_range  # replaceable for offline tests
    timings: dict[str, float] = field(default_factory=dict)


@dataclass
class Candidate:
    run: Run
    reason: str = ""
    fixed: str = ""  # a library's own reason (files, reads, probe), not its pairing's
    unit: Unit | None = None
    genotype: str = ""
    sample_id: str = ""
    reads: files.ReadFiles = field(default_factory=files.ReadFiles)
    lengths: str = ""
    note: str = ""

    @property
    def accession(self) -> str:
        return self.run["run_accession"]


def own_text(run: Run) -> str:
    keys = ("experiment_title", "sample_title", "library_name", "sample_description")
    return " | ".join(run.get(key, "") for key in keys)


def names_dap(text: str) -> bool:
    return any(not DAYS_BEFORE.search(text[: m.start()]) for m in DAP.finditer(text))


def search(archive: Archive, tax_query: str) -> list[Run]:
    runs: dict[str, Run] = {}
    for name, value in SEARCHES:
        form = {
            "result": "read_run",
            "query": f'{tax_query} AND {name}="{value}"',
            "fields": ",".join(FIELDS),
            "format": "tsv",
            "limit": "0",
        }
        for run in parse_tsv(archive.request(PORTAL + "search", form)):
            runs.setdefault(run["run_accession"], run)
    return list(runs.values())


def filereport(archive: Archive, accession: str) -> list[Run]:
    """Every run of a study (any taxon), or one run."""
    query = {
        "accession": accession,
        "result": "read_run",
        "fields": ",".join(FIELDS),
        "format": "tsv",
    }
    return parse_tsv(archive.request(PORTAL + "filereport?" + urlencode(query)))


def skipped_runs(paths: Iterable[Path]) -> dict[str, str]:
    """run -> 'in repository sheet <name>' for every run named in these sheets (`build`
    extends it to every run of their studies, which those sheets already cover)."""
    found: dict[str, str] = {}
    for path in paths:
        name = path.name.removesuffix(".tsv")
        for row in read_tsv(path):
            for run in runs_in(row.get("read1_url", ""), row.get("read2_url", "")):
                found.setdefault(run, f"in repository sheet {name}")
    return found


def dap_strategies(runs: Sequence[Run]) -> set[str]:
    """Library strategies a study used for DAP-seq: those of runs whose own titles name DAP,
    else every strategy that is not obviously another assay."""
    named = {r.get("library_strategy", "") for r in runs if names_dap(own_text(r))}
    if named:
        return named
    other = {"RNA-Seq", "miRNA-Seq", "ncRNA-Seq", "Bisulfite-Seq", "ATAC-seq", "RIP-Seq", "WGS"}
    return {r.get("library_strategy", "") for r in runs} - other


def assay_reason(run: Run, strategies: set[str]) -> str:
    text = own_text(run)
    if names_dap(text):
        return ""
    other = OTHER_ASSAY.search(text)
    if other:
        return f"not DAP-seq ({other.group(0)})"
    if run.get("library_strategy", "") not in strategies:
        return f"not DAP-seq ({run.get('library_strategy') or 'another assay'})"
    return ""


def genotype(species: Species, rule: StudyRule, run: Run) -> str:
    if rule.genotype:
        return rule.genotype
    for key in ("ecotype", "cultivar", "strain", "variety"):
        value = run.get(key, "").strip()
        if not value or re.match(r"(?i)missing|not (applicable|collected|provided)|n/?a$", value):
            continue
        return species.reference_genotype if species.reference.search(value) else value
    return "unknown"


def short(genotype: str) -> str:
    return genotype.split(" (")[0]


def discover(
    species: Species, archive: Archive, log: Callable[[str], None]
) -> tuple[list[Run], set[str]]:
    """Candidate runs (search hits plus every run of each DAP-seq study) and the DAP studies."""
    hits = search(archive, species.tax_query)
    by_study: dict[str, list[Run]] = defaultdict(list)
    for run in hits:
        by_study[run["study_accession"]].append(run)
    studies = {
        study
        for study, runs in by_study.items()
        if any(names_dap(own_text(r)) or names_dap(r.get("study_title", "")) for r in runs)
    } | {rule.accession for rule in pairing.catalog().for_species(species.folder)}
    log(f"{species.folder}: {len(hits)} runs found by text, {len(studies)} DAP-seq studies")
    runs = {run["run_accession"]: run for run in hits}
    with ThreadPoolExecutor(archive.workers) as pool:
        for found in pool.map(lambda s: filereport(archive, s), sorted(studies)):
            for run in found:
                runs[run["run_accession"]] = run
    return list(runs.values()), studies


def build(
    species: Species,
    archive: Archive,
    out: Path,
    options: Options,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    started = time.monotonic()
    runs, studies = discover(species, archive, log)
    catalog = pairing.catalog()
    skip = skipped_runs(options.skip_runs_in)
    # A sheet covers its whole study: runs it left out are not candidates here either.
    covered = {
        r["study_accession"]: skip[r["run_accession"]] for r in runs if r["run_accession"] in skip
    }
    candidates = {run["run_accession"]: Candidate(run) for run in runs}
    by_study: dict[str, list[Candidate]] = defaultdict(list)
    for item in candidates.values():
        by_study[item.run["study_accession"]].append(item)

    for study, items in by_study.items():
        rule = catalog.rule(study)
        strategies = dap_strategies([c.run for c in items])
        for item in items:
            run = item.run
            text = own_text(run) + " | " + run.get("study_title", "")
            bait = next((b.reason for b in catalog.bait_exclusions if b.match.search(text)), "")
            other_taxon = bool(run.get("tax_id")) and not species.has_taxon(run["tax_id"])
            item.reason = next(
                (
                    reason
                    for reason in (
                        skip.get(item.accession, "") or covered.get(study, ""),
                        "" if study in studies else DAYS_REASON,
                        rule.exclude,
                        f"DNA of another species ({run.get('scientific_name') or 'taxon '})"
                        if other_taxon
                        else "",
                        assay_reason(run, strategies),
                        bait,
                    )
                    if reason
                ),
                "",
            )
            item.genotype = genotype(species, rule, run)
    options.timings["metadata_seconds"] = round(time.monotonic() - started, 1)

    started = time.monotonic()
    external: dict[str, Candidate] = {}
    for study, items in by_study.items():
        rule = catalog.rule(study)
        kept = [c for c in items if not c.reason]
        if not kept:
            continue
        units = pairing.assign([c.run for c in kept], rule)
        for item, unit in zip(kept, units, strict=True):
            item.unit = unit
            item.reason = unit.reason
            item.note = "; ".join(n for n in (rule.note, unit.note) if n)
    for study, items in by_study.items():
        rule = catalog.rule(study)
        for label, accession in rule.external_controls.items():
            if label in external or not any(c.unit for c in items):
                continue
            own = candidates.get(accession)
            if own and own.unit and own.unit.role == "control" and not own.reason:
                continue  # a control of its own study, used from there
            found = filereport(archive, accession)
            if not found:
                raise ValueError(f"{study}: external control run {accession} not in ENA")
            unit = Unit(found[0], label, "control")
            external[label] = Candidate(found[0], unit=unit, genotype=rule.genotype)
    libraries = choose_libraries(by_study, external)
    lengths = probes.LengthCache(
        options.cache / "read-lengths.json", options.fetch_reads, offline=archive.offline
    )
    rows = settle(species, by_study, libraries, lengths if options.probe else None, log)
    options.timings["pairing_and_probe_seconds"] = round(time.monotonic() - started, 1)

    writers.write_sheet(out / f"{WORKFLOW}.tsv", WORKFLOW, rows)
    qc = runs_qc(candidates.values())
    writers.write_runs_qc(out / f"{WORKFLOW}.runs-qc.tsv", qc)
    reasons = Counter(
        re.sub(r" \(.*|: .*", "", row["reason_left_out"]) for row in qc if row["reason_left_out"]
    )
    treatments = [row for row in rows if row["control_sample"] != "-"]
    return {
        "species": species.folder,
        "workflow": WORKFLOW,
        "runs": len(qc),
        "in_sheet": len(rows),
        "treatments": len(treatments),
        "paired": sum(row["read2_url"] != "-" for row in rows),
        "studies": len({c.run["study_accession"] for c in candidates.values() if not c.reason}),
        "reasons": dict(reasons.most_common()),
        "ena_requests": archive.requests,
        "timings": options.timings,
    }


def choose_libraries(
    by_study: dict[str, list[Candidate]], external: dict[str, Candidate]
) -> dict[tuple[str, str], Candidate]:
    """(study, label) -> the library's largest run. Other runs of a library get their reason
    here; libraries without usable files or with under 1M reads get a fixed reason."""
    catalog = pairing.catalog()
    chosen: dict[tuple[str, str], Candidate] = {}
    for study, items in by_study.items():
        kept = [c for c in items if c.unit and not c.reason]
        if kept and not catalog.rule(study).external_controls:
            if not any(c.unit and c.unit.role == "control" for c in kept):
                for item in kept:
                    item.reason = "no control library in the study"
                continue
        best = pairing.largest_runs(c.unit for c in kept if c.unit)
        for item in kept:
            unit = item.unit
            assert unit is not None
            keep = best[unit.label]
            if keep is not unit:
                item.reason = (
                    f"another run of library {unit.label} (the sheet uses its largest run, "
                    f"{keep.run['run_accession']})"
                )
            else:
                chosen[(study, unit.label)] = item
    for label, item in external.items():
        chosen[("", label)] = item
    for (study, label), item in chosen.items():
        rule = catalog.rule(study)
        reads = files.resolve(item.run)
        if rule.read1_only and reads.read2 != "-" and not reads.reason:
            reads = files.ReadFiles(reads.read1)
        item.reads = reads
        count = int(item.run.get("read_count") or 0)
        item.fixed = reads.reason or (
            f"too few reads ({count:,})" if count < MIN_LIBRARY_READS else ""
        )
        template = rule.sample_id if study else "{label}"
        item.sample_id = pairing.clean(
            template.format(
                study=study, label=label, genotype=short(item.genotype), run=item.accession
            )
        )
    return chosen


def settle(
    species: Species,
    by_study: dict[str, list[Candidate]],
    libraries: dict[tuple[str, str], Candidate],
    lengths: probes.LengthCache | None,
    log: Callable[[str], None],
) -> list[dict[str, str]]:
    """Pair treatments with controls, probe the files the sheet would use, and repeat until
    the probe leaves no further library out; returns the sheet rows."""
    while True:
        assembled = assemble(species, link(by_study, libraries))
        if lengths is None:
            break
        urls = [u for row in assembled.rows for u in (row["read1_url"], row["read2_url"])]
        found = lengths.lengths(urls)
        changed = False
        for item in libraries.values():
            used = [u for u in (item.reads.read1, item.reads.read2) if u in found]
            if not used or item.fixed:
                continue
            probed = [found[u] for u in used]
            item.lengths = ",".join(
                str(p.shortest) if p.distinct == 1 else f"{p.shortest}-{p.longest}" for p in probed
            )
            problem = next((p.problem for p in probed if p.problem), "")
            if problem:
                item.fixed = item.reason = problem
                changed = True
        if not changed:
            break
        log(f"{species.folder}: the read-length probe left libraries out; pairing again")
    for item in libraries.values():
        note = assembled.notes.get(item.sample_id, "")
        if note and not item.reason:
            item.note = "; ".join(n for n in (item.note, note) if n)
    return assembled.rows


@dataclass
class Pairs:
    treatments: list[tuple[Candidate, Candidate]]  # (treatment, its control)
    controls: list[Candidate]
    keep_unused: set[str]


def link(
    by_study: dict[str, list[Candidate]], libraries: dict[tuple[str, str], Candidate]
) -> Pairs:
    """Give each usable treatment its control library, or a reason; controls no treatment
    uses are left out unless the study keeps them."""
    catalog = pairing.catalog()
    for item in libraries.values():
        item.reason = item.fixed
    used: set[str] = set()
    pairs: list[tuple[Candidate, Candidate]] = []
    for (study, _label), item in libraries.items():
        unit = item.unit
        if not study or unit is None or unit.role != "treatment" or item.reason:
            continue
        rule = catalog.rule(study)
        has_controls = bool(rule.external_controls) or any(
            c.unit.role == "control" for c in by_study[study] if c.unit
        )
        control = libraries.get((study, unit.control))
        borrowed = rule.external_controls.get(unit.control, "")
        if control is None and borrowed:
            control = next((c for c in libraries.values() if c.accession == borrowed), None)
        if not unit.control or not has_controls:
            item.reason = "no control library in the study"
        elif control is None:
            item.reason = f"its control ({unit.control}) is not in the study"
        elif control.fixed:
            item.reason = f"its control ({unit.control}) is not usable: {control.fixed}"
        else:
            used.add(control.sample_id)
            pairs.append((item, control))
    controls: list[Candidate] = []
    keep_unused: set[str] = set()
    for (study, _label), item in libraries.items():
        unit = item.unit
        if unit is None or unit.role != "control" or item.reason:
            continue
        keep = bool(study) and catalog.rule(study).keep_unused_controls
        if item.sample_id in used or keep:
            controls.append(item)
            if keep:
                keep_unused.add(item.sample_id)
        else:
            item.reason = "unused control (no treatment in the study uses it)"
    return Pairs(pairs, controls, keep_unused)


def assemble(species: Species, pairs: Pairs) -> pairing.Assembled:
    treatments = [
        Library(t.sample_id, t.reads.read1, t.reads.read2, c.sample_id) for t, c in pairs.treatments
    ]
    controls = [Library(c.sample_id, c.reads.read1, c.reads.read2) for c in pairs.controls]
    return pairing.assemble(
        treatments, controls, species.folder, species.references.fasta, pairs.keep_unused
    )


def runs_qc(candidates: Iterable[Candidate]) -> list[dict[str, str]]:
    rows = []
    for item in candidates:
        run, unit = item.run, item.unit
        rows.append(
            {
                "run": item.accession,
                "study": run["study_accession"],
                "sample_id": "" if item.reason else item.sample_id,
                "in_sheet": "no" if item.reason else "yes",
                "reason_left_out": item.reason,
                "role": unit.role if unit else "",
                "label": unit.label if unit else "",
                "control": unit.control if unit and unit.role == "treatment" else "",
                "pairing": unit.pairing if unit else "",
                "genotype": item.genotype,
                "read_count": run.get("read_count", ""),
                "ena_layout": run.get("library_layout", ""),
                "layout": item.reads.layout if unit and not item.reason else "",
                "read_lengths": item.lengths,
                "note": item.note,
                "title": run.get("experiment_title", "")[:160],
            }
        )
    return rows
