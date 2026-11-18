"""DAP-seq libraries and their controls: per-study rules from `data/dap_studies.yaml`, and an
automatic pairer for studies the file does not describe.

A study's runs are first named: each run gets a library label (from its experiment title,
sample title, library name or sample description, whichever tells the study's libraries
apart, or the field the study's rule names). Labels are then sorted into controls and
treatments, and each treatment is given a control label:

- declared: the study's `pairs` rules (regex on the treatment label -> control label template),
  first match wins; `controls` (a regex) says which labels are controls.
- automatic: a label is a control when it names a control (input, empty vector, beads, mock,
  IgG, no protein) or only a tag (Halo, pIX-HALO, GST, GFP, GUS, SBP, FLAG, MBP) with no gene
  or TF name left. Each treatment takes the control that best matches it: same sequencing
  instrument (controls are sequenced with their batch), same replicate number, then shared
  words (tissue, gDNA library); ties go to the control with more reads.

`assemble` turns kept treatments and controls into sheet rows, following the DAP workflow's
rules: MACS3 runs BAMPE for paired and BAM for single-end treatments, so a control's layout must
match its treatment's. A paired treatment with a single-end control uses read 1 only; a
single-end treatment with a paired control uses a `<control>_read1` copy of the control (read
1 only). Every `control_sample` must name a row of the sheet.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from .archives import Run
from .species import DATA

LABEL_FIELDS = ("title", "sample_title", "library_name", "sample_description")

CONTROL_WORDS = re.compile(
    r"input|empty[-_ ]?vector|(?<![a-z])vec(tor)?(?![a-z])|(?<![a-z])beads?(?![a-z])|"
    r"bead[-_ ]?only|(?<![a-z])mock(?![a-z])|no[-_ ]?(bait|protein|tf)(?![a-z])|"
    r"(?<![a-z])empty(?![a-z])|(?<![a-z])igg(?![a-z])|"
    r"control|(?<![a-z])ctrl(?![a-z])|negative",
    re.I,
)
TAG = re.compile(r"^(p|pix|pixh)?(halo|halotag|gst|gfp|egfp|gus|sbp|mbp|pix)$|^\d*x?flag$", re.I)
REPLICATE = re.compile(
    r"(?<![a-z])(?:rep(?:licate|eat)?|r|bio)[-_ ]?(\d+)(?![0-9])|[-_ ](\d)$", re.I
)
NOT_NAMES = re.compile(
    r"^(rep(licate|eat)?|r|bio|lib|v)\d*[a-z]?$|^\d+([a-z]{0,6})?$|^[a-z]$|"
    r"^(gs[em]|[sed]r[rxsp]|sam[nde]a?|prj[a-z]+)\d+$",
    re.I,
)
# Words that say nothing about which protein was used (or which control matches).
GENERIC = frozenset(
    """dap seq dapseq ampdap ampdapseq daps sample samples library lib libraries of for and the
    with to in on at ath atha sb sbi arabidopsis thaliana sorghum bicolor col col0 columbia
    btx623 rtx430 dna gdna genomic only tag tagged protein replicate repeat rep sequencing
    illumina tnt amp colamp halo plant plants seedling seedlings root roots leaf leaves shoot
    shoots flower flowers inflorescence meristem meristems sam im ivt invitro vitro method
    data reads raw purification affinity from""".split()
)


@dataclass(frozen=True)
class Pair:
    match: re.Pattern[str]
    control: str


@dataclass(frozen=True)
class Skip:
    match: re.Pattern[str]
    reason: str


@dataclass(frozen=True)
class StudyRule:
    """One study's entry in `dap_studies.yaml`; every key is optional."""

    accession: str
    species: str = ""
    name: str = ""
    exclude: str = ""  # leave every run out with this reason
    genotype: str = ""
    label: str = ""  # one of LABEL_FIELDS
    replace: tuple[tuple[re.Pattern[str], str], ...] = ()
    skip: tuple[Skip, ...] = ()
    controls: re.Pattern[str] | None = None
    pairs: tuple[Pair, ...] = ()
    external_controls: dict[str, str] = field(default_factory=dict)  # label -> run
    keep_unused_controls: bool = False
    read1_only: bool = False
    sample_id: str = "{study}_{label}_{genotype}_{run}"
    note: str = ""
    notes: dict[str, str] = field(default_factory=dict)  # label regex -> note

    @classmethod
    def parse(cls, accession: str, entry: dict[str, Any]) -> StudyRule:
        known = {
            "species",
            "name",
            "exclude",
            "genotype",
            "label",
            "replace",
            "skip",
            "controls",
            "pairs",
            "external_controls",
            "keep_unused_controls",
            "read1_only",
            "sample_id",
            "note",
            "notes",
        }
        unknown = set(entry) - known
        if unknown:
            raise ValueError(f"{accession}: unknown keys {sorted(unknown)} in dap_studies.yaml")
        label = entry.get("label", "")
        if label and label not in LABEL_FIELDS:
            raise ValueError(f"{accession}: label must be one of {LABEL_FIELDS}")
        return cls(
            accession=accession,
            species=entry.get("species", ""),
            name=entry.get("name", ""),
            exclude=entry.get("exclude", ""),
            genotype=entry.get("genotype", ""),
            label=label,
            replace=tuple((re.compile(a), b) for a, b in entry.get("replace") or []),
            skip=tuple(Skip(re.compile(s["match"]), s["reason"]) for s in entry.get("skip") or []),
            controls=re.compile(entry["controls"]) if entry.get("controls") else None,
            pairs=tuple(
                Pair(re.compile(p["match"]), p["control"]) for p in entry.get("pairs") or []
            ),
            external_controls=dict(entry.get("external_controls") or {}),
            keep_unused_controls=bool(entry.get("keep_unused_controls", False)),
            read1_only=bool(entry.get("read1_only", False)),
            sample_id=entry.get("sample_id") or "{study}_{label}_{genotype}_{run}",
            note=entry.get("note", ""),
            notes=dict(entry.get("notes") or {}),
        )


@dataclass(frozen=True)
class Catalog:
    studies: dict[str, StudyRule]
    bait_exclusions: tuple[Skip, ...]

    def for_species(self, folder: str) -> list[StudyRule]:
        return [rule for rule in self.studies.values() if rule.species == folder]

    def rule(self, study: str) -> StudyRule:
        return self.studies.get(study) or StudyRule(study)


@cache
def catalog(path: Path = DATA / "dap_studies.yaml") -> Catalog:
    value = yaml.safe_load(path.read_text()) or {}
    return Catalog(
        studies={
            accession: StudyRule.parse(accession, entry or {})
            for accession, entry in (value.get("studies") or {}).items()
        },
        bait_exclusions=tuple(
            Skip(re.compile(item["match"], re.I), item["reason"])
            for item in value.get("bait_exclusions") or []
        ),
    )


def title_label(run: Run) -> str:
    """The library's name inside an experiment title such as 'NextSeq 500 sequencing:
    GSM5746464: A1b-A3 Arabidopsis thaliana OTHER' or 'NextSeq 550 sequencing: DAPseq of
    Sorghum bicolor: SbBES1 rep1' (the part after the last ': ')."""
    text = run.get("experiment_title", "")
    if ": " not in text:
        return ""
    text = text.rsplit(": ", 1)[1]
    name = re.escape(run.get("scientific_name", "")) or "(?!)"
    text = re.sub(rf"[;,]? ({name})(;? [A-Za-z-]+)?;?$", "", text)
    text = re.sub(rf"^({name}) (?=\S)", "", text)
    return re.sub(r"(?i)^(amp)?dap[-_ ]?seq (of|for|with) ", "", text).strip()


def field_value(run: Run, name: str) -> str:
    return title_label(run) if name == "title" else run.get(name, "").strip()


def label_field(runs: Sequence[Run], rule: StudyRule) -> str:
    """The field that tells the study's libraries apart: most values that name something (a
    protein, tissue or control rather than 'lib_8' or 'GSM5958417'), then most distinct
    values, then the earlier field."""
    if rule.label:
        return rule.label

    def rank(name: str) -> tuple[int, int, int]:
        values = {field_value(run, name) for run in runs} - {""}
        named = sum(1 for v in values if names(v) or CONTROL_WORDS.search(v))
        return named, len(values), -LABEL_FIELDS.index(name)

    return max(LABEL_FIELDS, key=rank)


def run_label(run: Run, name: str, rule: StudyRule) -> str:
    label = field_value(run, name) or run["run_accession"]
    for pattern, replacement in rule.replace:
        label = pattern.sub(replacement, label)
    return label.strip()


def tokens(label: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", label.lower()) if t]


def names(label: str) -> set[str]:
    """Words of a label that could name a protein, tissue or gDNA batch (not generic words,
    tags, replicate numbers or control words)."""
    return {
        t
        for t in tokens(CONTROL_WORDS.sub(" ", label))
        if t not in GENERIC and not TAG.match(t) and not NOT_NAMES.match(t)
    }


def is_control(label: str) -> bool:
    if CONTROL_WORDS.search(label):
        return True
    words = tokens(label)
    return any(TAG.match(t) for t in words) and not names(label)


def replicate(label: str) -> str:
    found = REPLICATE.search(label)
    return (found.group(1) or found.group(2)) if found else ""


@dataclass
class Unit:
    """One run of a study and what the study's rules made of it."""

    run: Run
    label: str = ""
    role: str = ""  # treatment | control | '' (left out by a study rule)
    control: str = ""  # the control's label, for a treatment
    reason: str = ""
    note: str = ""
    pairing: str = ""  # declared | automatic: why


def match_score(treatment: Unit, control: Unit) -> tuple[int, int, int]:
    score = 0
    if treatment.run.get("instrument_model") and treatment.run.get(
        "instrument_model"
    ) == control.run.get("instrument_model"):
        score += 4
    a, b = replicate(treatment.label), replicate(control.label)
    if a and b:
        score += 2 if a == b else -2
    shared = len(names(treatment.label) & names(control.label))
    return score, shared, int(control.run.get("read_count") or 0)


def auto_control(treatment: Unit, controls: Sequence[Unit]) -> tuple[str, str]:
    """The best control label for a treatment, and how it was chosen."""
    if len(controls) == 1:
        return controls[0].label, "automatic: the study's only control"
    ranked = sorted(controls, key=lambda c: match_score(treatment, c), reverse=True)
    best = ranked[0]
    score, shared, _ = match_score(treatment, best)
    why = []
    if treatment.run.get("instrument_model") == best.run.get("instrument_model"):
        why.append("same instrument")
    if replicate(treatment.label) and replicate(treatment.label) == replicate(best.label):
        why.append("same replicate")
    if shared:
        why.append("shared words")
    tie = match_score(treatment, ranked[1])[:2] == (score, shared)
    if tie:
        why.append("tie broken by read count")
    return best.label, "automatic: " + (", ".join(why) or "most reads")


def assign(runs: Sequence[Run], rule: StudyRule) -> list[Unit]:
    """Label every run of a study, sort labels into controls and treatments and give each
    treatment a control label. Runs already left out are not passed in."""
    name = label_field(runs, rule)
    units = [Unit(run, run_label(run, name, rule)) for run in runs]
    for unit in units:
        for skip in rule.skip:
            if skip.match.search(unit.label):
                unit.reason = skip.reason
                break
        if unit.reason:
            continue
        control = rule.controls.search(unit.label) if rule.controls else is_control(unit.label)
        unit.role = "control" if control else "treatment"
        for pattern, note in rule.notes.items():
            if re.search(pattern, unit.label):
                unit.note = note
    split_mates(units)
    controls = list({u.label: u for u in units if u.role == "control" and not u.reason}.values())
    for unit in units:
        if unit.role != "treatment" or unit.reason:
            continue
        for pair in rule.pairs:
            found = pair.match.search(unit.label)
            if found:
                unit.control, unit.pairing = found.expand(pair.control), "declared"
                break
        else:
            if controls:
                unit.control, unit.pairing = auto_control(unit, controls)
    return units


def split_mates(units: Sequence[Unit]) -> None:
    """Mates uploaded as two runs: two single-end runs of one study with the same read count
    whose labels differ only by a final 1 and 2. The read-2 run is left out; the read-1 run
    stays single-end (two SRA runs are never paired as mates: their read names differ)."""
    by_count: dict[str, list[Unit]] = {}
    for unit in units:
        count = unit.run.get("read_count", "")
        if unit.reason or not count or unit.run.get("library_layout") != "SINGLE":
            continue
        by_count.setdefault(count, []).append(unit)
    for group in by_count.values():
        if len(group) != 2:
            continue
        first, second = sorted(group, key=lambda u: u.label)
        if first.label[:-1] == second.label[:-1] and (first.label[-1:], second.label[-1:]) == (
            "1",
            "2",
        ):
            second.role = ""
            second.reason = (
                f"read 2 of {first.run['run_accession']}, uploaded as its own run; not used"
            )
            first.label = first.label[:-1].rstrip(" _-") or first.label
            first.note = (
                f"single-end: its read 2 was uploaded as run {second.run['run_accession']}; "
                "two runs are never paired as mates"
            )


def largest_runs(units: Iterable[Unit]) -> dict[str, Unit]:
    """label -> the library's largest run (one row per library)."""
    best: dict[str, Unit] = {}
    for unit in units:
        if unit.reason or not unit.role:
            continue
        held = best.get(unit.label)
        if held is None or int(unit.run.get("read_count") or 0) > int(
            held.run.get("read_count") or 0
        ):
            best[unit.label] = unit
    return best


def clean(text: str) -> str:
    text = text.replace("Δ", "d").replace("..", "_")
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9.-]+", "_", text)).strip("_.-")


@dataclass(frozen=True)
class Library:
    """A kept library, ready for the sheet."""

    sample_id: str
    read1: str
    read2: str  # '-' for single-end
    control: str = ""  # the control library's sample_id, '' for a control


@dataclass
class Assembled:
    rows: list[dict[str, str]]
    notes: dict[str, str]  # sample_id -> note added while assembling (layout changes)
    read1_copies: set[str]  # control sample_ids used through a _read1 copy


def assemble(
    treatments: Sequence[Library],
    controls: Sequence[Library],
    species: str,
    reference: str,
    keep_unused: Iterable[str] = (),
) -> Assembled:
    """Sheet rows for treatments and the controls they use, with the layout rules above."""
    by_id = {c.sample_id: c for c in controls}
    rows: list[dict[str, str]] = []
    notes: dict[str, str] = {}
    full: set[str] = set(keep_unused) & set(by_id)
    read1: set[str] = set()

    def row(sample_id: str, read1_url: str, read2_url: str, control: str) -> dict[str, str]:
        return {
            "sample_id": sample_id,
            "species": species,
            "read1_url": read1_url,
            "read2_url": read2_url,
            "control_sample": control,
            "reference_fasta": reference,
        }

    for item in treatments:
        control = by_id.get(item.control)
        if control is None:
            raise ValueError(f"{item.sample_id}: control {item.control!r} is not a sheet row")
        read2, control_id = item.read2, control.sample_id
        if read2 != "-" and control.read2 == "-":
            read2 = "-"
            full.add(control.sample_id)
            notes[item.sample_id] = "read 1 only (its control is single-end)"
        elif read2 == "-" and control.read2 != "-":
            control_id += "_read1"
            read1.add(control.sample_id)
            notes[item.sample_id] = "control used as read 1 only (a single-end copy)"
        else:
            full.add(control.sample_id)
        rows.append(row(item.sample_id, item.read1, read2, control_id))
    for control in controls:
        if control.sample_id in full:
            rows.append(row(control.sample_id, control.read1, control.read2, "-"))
        if control.sample_id in read1:
            rows.append(row(control.sample_id + "_read1", control.read1, "-", "-"))
    check_controls(rows)
    return Assembled(rows, notes, read1)


def check_controls(rows: Sequence[dict[str, str]]) -> None:
    """Every control_sample names a control row (control_sample '-') with the same species,
    reference and layout, and every sample_id is unique."""
    by_id: dict[str, dict[str, str]] = {}
    for item in rows:
        if item["sample_id"] in by_id:
            raise ValueError(f"sample_id {item['sample_id']!r} repeats")
        by_id[item["sample_id"]] = item
    for item in rows:
        name = item["control_sample"]
        if name == "-":
            continue
        control = by_id.get(name)
        if control is None:
            raise ValueError(f"{item['sample_id']}: control {name!r} is not a sheet row")
        if control["control_sample"] != "-":
            raise ValueError(f"{item['sample_id']}: control {name!r} has a control itself")
        for key in ("species", "reference_fasta"):
            if control[key] != item[key]:
                raise ValueError(f"{item['sample_id']}: control {name!r} has another {key}")
        if (control["read2_url"] == "-") != (item["read2_url"] == "-"):
            raise ValueError(f"{item['sample_id']}: control {name!r} has another layout")


def summary(units: Iterable[Unit]) -> Counter[str]:
    return Counter(u.role or "left out" for u in units)
