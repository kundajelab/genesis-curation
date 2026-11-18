"""Rule-based classes for archive runs: genotype, treatment, tissue and assay.

Sample classes come from the sample's attributes and the run's ENA fields; run flags come
from the run's own experiment and sample titles. Assay and title patterns are bounded by
non-alphanumerics, not `\\b`: an underscore is a word character, so `\\b` misses
`RNAseq_Col0_Polysome`. Species differences (reference genotype, whether a bare gene name
means a mutant) come from `species.yaml`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .archives import Run, Sample
from .species import Species

SHORT_READ = {"ILLUMINA", "DNBSEQ", "BGISEQ", "ELEMENT"}
EMPTY = {"missing", "not applicable", "n/a", "na", "-", "not collected", "not provided"}
L, R = r"(?<![a-z0-9])(", r")(?![a-z0-9])"

MUTANT = re.compile(
    r"mutant|knock[- ]?(out|down)|\bko\b|overexpress|\boe\b|\b\w+[- ]ox\b|crispr|cas9|\brnai\b|"
    r"amirna|t-dna|transgen|\bsalk[_ ]|\bsail[_ ]|\bgk[-_]\d|\bwisc|\bflag[_ ]\d|"
    r"\b[a-z]{2,6}\d{1,3}-\d{1,3}\b|\b[a-z]{2,6}\d{1,3}\s*x\s*[a-z]|"  # alleles: abi3-1, pif4-101
    r"complement|reporter|gfp|gus\b|::|\bline\s*\d",
    re.I,
)
GENE_ALLELE = re.compile(r"\b(at)?[a-z]{3,6}\d{1,3}[a-z]?\b", re.I)
WILD_TYPE = re.compile(r"wild[- ]?type|\bwt\b|wildtype", re.I)
ONLY_WILD_TYPE = re.compile(r"(\s*\|?\s*(wild[- ]?type( genotype)?|wt|wildtype)\s*)+", re.I)
OTHER_GENOTYPE = re.compile(r"natural|ecotype|accession|cultivar|landrace", re.I)
CROSS = re.compile(r"\bx\b|×|crossed|\bf1\b|hybrid", re.I)
GERMPLASM = re.compile(r"(?<![a-z])pi ?\d{5,6}\b", re.I)
CONTROL = re.compile(
    r"^\s*(none|no|n/?a|control|ctrl|mock|untreated|normal|non[- ]?treated|well[- ]?watered|ww|"
    r"standard|optimal|ambient|day ?0|0 ?h|time ?0|baseline|water|unstressed|no treatment)\b"
    r"|\b(control|mock|untreated)\b",
    re.I,
)
TREATED = re.compile(
    r"stress|drought|salt|nacl|cold|heat|hypoxia|flood|osmotic|mannitol|pathogen|infect|infest|"
    r"pseudomonas|botrytis|fungal|virus|aphid|insect|elicitor|flg22|chitin|aba\b|abscisic|"
    r"auxin|iaa|naa|cytokinin|gibberell|ga3|ethylene|acc\b|jasmon|meja|salicyl|brassino|"
    r"treat|inhibitor|dex|estradiol|herbicide|cadmium|metal|nitrogen|phosphate|deficien|"
    r"light|dark|uv|ozone|wound|deprivation|limitation|starvation|hours? after|h after",
    re.I,
)
# First match wins, in this order; "shoot" alone is checked after leaf and stem.
TISSUES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.I))
    for name, pattern in (
        ("cell culture", r"protoplast|cell culture|suspension|callus|cell line"),
        ("pollen", r"pollen"),
        ("seedling", r"seedling|seeding\b"),
        ("seed", r"\bseeds?\b|seed[ _-]|embryo|endosperm|silique|grain|kernel|caryopsis"),
        (
            "flower",
            r"flower|inflorescence|floral|bud|panicle|anther|pistil|stamen|petal|sepal|carpel|"
            r"meristem.*infl",
        ),
        ("root", r"root"),
        ("shoot", r"shoot apex|apex|apical|meristem|tiller|growing ?point"),
        ("leaf", r"leaf|leaves|rosette|cotyledon|mesophyll|guard cell|blade|sheath"),
        ("stem", r"stem|internode|peduncle|hypocotyl|culm|stalk|rind|pith"),
        ("shoot", r"shoot"),
        ("whole plant", r"whole plant|plant|aerial|above[- ]?ground"),
    )
)

# Library selections that are not RNA-seq of total or poly(A) RNA.
OTHER_SELECTIONS = {"CAGE", "DNase", "Hybrid Selection", "HMPR", "ChIP", "MNase"}
# Assays named in a run's own titles: (pattern, label). First match wins.
ASSAYS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(L + pattern + R, re.I), label)
    for pattern, label in (
        (r"smar-?2?c2|rampage|tss-?seq|csrna|strt|oligo-?capping|cage|cap-?seq", "TSS"),
        (
            r"scrna(-?seq)?|snrna-?seq|single[- ]cell|single[- ]nucle\w*|drop-?seq|"
            r"10x[- ]?(genomics|chromium)|chromium",
            "single-cell",
        ),
        (r"pat-?seq", "PAT-seq, poly(A) tails"),
        (
            r"quant-?seq|3' ?mrna-?seq|3end|3'-?end|pas-?seq|polya-?seq|tail-?seq|flep-?seq",
            "3' end",
        ),
        (r"tif-?seq", "TIF-seq"),
        (r"nanopare|pare|degradome|peat", "degradome"),
        (r"m6a[- ]?ip|merip", "m6A IP"),
        (r"rip|clip|iclip|immunoprecipitat\w*|tert ip|[a-z0-9]+ ip", "IP"),
        (r"ribo-?seq|ribosome profiling|footprint\w*", "ribosome profiling"),
        (r"polysom\w*", "polysome"),
        (r"trap|trap-?seq|translatome", "translatome"),
        (r"nascent|gro-?seq|pro-?seq|net-?seq", "nascent RNA"),
        (r"dms|structure-?seq|icshape", "RNA structure"),
        (r"small ?rna|srna|mirna", "small RNA"),
        (r"delta-?seq", "DeLTa-seq"),
        (r"chip", "ChIP"),
        (r"iso-?seq|nanopore|direct rna", "long-read"),
        (r"circ-?rna|rnase r", "circular RNA"),
        (
            r"nuclear rna|nuclei|nucleus|nucleoplasm\w*|cytosol\w*|"
            r"chromatin[-_ ]*(bound|associated|attached|fraction\w*|rna|rep\w*)",
            "subcellular fraction",
        ),
    )
)
SHAPE = re.compile(r"(?<![A-Za-z0-9])SHAPE(?![A-Za-z0-9])")  # upper case only: "leaf shape"
TRANSGENE = re.compile(
    L + r"expressing|overexpress\w*|over ?expression|over-?expressing|transgenic|35s|"
    r"crispr|cas9|amirna|rnai|knock-?down|knock-?out|complemented|complementation|gfp|yfp|"
    r"mcherry|oe|ox|ko|kd|mutants?|t-?dna|ems|irradiat\w*" + R,
    re.I,
)
# Within-study title rules (from the ATAC curation): markers other titles of the study use.
TITLE_WILD_TYPE = r"(?<![a-z])(wt|wild[- ]?type|wildtype)(?![a-z])"
TITLE_CONTROL = re.compile(
    r"(?<![a-z])(control|cont|ctrl|mock|untreated|uncut|normal|0 ?(h|d|w|min|hr|day)|day ?0|t0)"
    r"(?![a-z])",
    re.I,
)
TITLE_TREATMENT = re.compile(
    r"(?<![a-z])(heat|cold|drought|salt|nacl|aba|mej?a|dark|blue|red|far-red|uv|lethal|recovery|"
    r"stress|treat\w*|infect\w*|cim|sim|dex|\d+ ?(h|hr|hpi|dpi|min))(?![a-z])",
    re.I,
)
TITLE_MUTANT = re.compile(
    r"(?<![a-z])(mutant|gfp|rfp|yfp|ntf|intact|knock-?out|overexpress\w*|oe)(?![a-z])", re.I
)
SEQUENCING_PREFIX = re.compile(r"^.*?sequencing(: GSM\d+:)?:?")


@dataclass(frozen=True)
class SampleClass:
    genotype: str
    treatment: str
    tissue: str
    genotype_text: str = ""
    treatment_text: str = ""
    tissue_text: str = ""


def joined(*values: str | None) -> str:
    return " | ".join(v for v in values if v and v.strip() and v.strip().lower() not in EMPTY)


def usable(run: Run) -> str:
    """Why a run is not bulk short-read RNA-seq by its ENA fields, or ''."""
    if run.get("library_source") != "TRANSCRIPTOMIC":
        return f"library source {run.get('library_source') or 'not stated'}"
    if run.get("instrument_platform") not in SHORT_READ:
        return f"not short-read ({run.get('instrument_platform') or 'platform not stated'})"
    if run.get("library_selection") == "size fractionation":
        return "small RNA (size fractionation)"
    if not run.get("sample_accession"):
        return "no sample accession"
    return ""


def classify_sample(species: Species, sample: Sample, run: Run) -> SampleClass:
    """Genotype, treatment and tissue of a sample from its attributes and one of its runs."""
    a = sample.attributes
    genotype_text = joined(
        a.get("genotype"),
        a.get("genotype/variation"),
        a.get("strain"),
        a.get("ecotype"),
        a.get("cultivar"),
        run.get("ecotype"),
        run.get("cultivar"),
        run.get("strain"),
        a.get("line"),
    )
    descriptive = joined(
        sample.title,
        sample.description,
        a.get("sample comment"),
        a.get("source_name"),
        run.get("sample_title"),
        run.get("experiment_title"),
    )
    gene_allele = (
        species.gene_names_mean_mutant
        and GENE_ALLELE.search(genotype_text)
        and not re.search(r"\bcol\d|wild[- ]?type|\bwt\b", genotype_text, re.I)
    )
    mutant = MUTANT.search(genotype_text) or any(
        term.search(genotype_text) for term in species.mutant_terms
    )
    if mutant or gene_allele or (not genotype_text and MUTANT.search(descriptive)):
        genotype = "mutant / transgenic"
    elif CROSS.search(genotype_text):
        genotype = "other accession / cultivar"
    elif species.reference.search(genotype_text or descriptive):
        genotype = f"reference ({species.reference_genotype})"
    elif ONLY_WILD_TYPE.fullmatch(genotype_text) or (
        not genotype_text and WILD_TYPE.search(descriptive)
    ):
        genotype = "wild type, accession not stated"
    elif (
        genotype_text
        or WILD_TYPE.search(descriptive)
        or OTHER_GENOTYPE.search(descriptive)
        or GERMPLASM.search(descriptive)
    ):
        genotype = "other accession / cultivar"
    else:
        genotype = "not stated"

    treatment_text = joined(
        a.get("treatment"),
        a.get("stress"),
        a.get("condition"),
        a.get("growth protocol"),
        a.get("experimental_factor"),
        run.get("experimental_factor"),
    )
    if treatment_text:
        treatment = (
            "control / untreated"
            if CONTROL.search(treatment_text)
            else "treated"
            if TREATED.search(treatment_text)
            else "other condition"
        )
    else:
        treatment = "treated" if TREATED.search(descriptive) else "not stated"

    # Tissue fields decide; source, stage and titles are fallbacks, since a stage such as
    # "between anthesis and seed set" names other organs.
    sources = [
        joined(
            a.get("tissue"),
            a.get("tissue_type"),
            run.get("tissue_type"),
            a.get("cell type"),
            run.get("cell_type"),
        ),
        joined(a.get("source_name"), a.get("isolation_source")),
        joined(a.get("dev_stage"), a.get("development stage"), run.get("dev_stage")),
        descriptive,
    ]
    tissue = next(
        (
            name
            for source in sources
            if source
            for name, pattern in TISSUES
            if pattern.search(source)
        ),
        "not stated",
    )
    return SampleClass(
        genotype=genotype,
        treatment=treatment,
        tissue=tissue,
        genotype_text=genotype_text[:200],
        treatment_text=treatment_text[:200],
        tissue_text=next((s for s in sources if s), "")[:200],
    )


def is_reference(value: SampleClass) -> bool:
    return value.genotype.startswith("reference")


def is_untreated(value: SampleClass) -> bool:
    return value.treatment in ("control / untreated", "not stated")


def titles(run: Run) -> str:
    return f"{run.get('experiment_title', '')} | {run.get('sample_title', '')}"


def assay_flag(run: Run) -> str:
    """'not standard RNA-seq (...)' when the library selection or the run's own titles name
    another assay, or ''."""
    if run.get("library_selection") in OTHER_SELECTIONS:
        return f"not standard RNA-seq (selection {run['library_selection']})"
    text = titles(run)
    for pattern, label in ASSAYS:
        if pattern.search(text):
            return f"not standard RNA-seq ({label})"
    if SHAPE.search(text):
        return "not standard RNA-seq (RNA structure)"
    return ""


def transgene_flag(run: Run, reference: re.Pattern[str]) -> str:
    """A transgene, mutant or treatment word in the run's titles. The experiment title often
    describes the whole study ("RNA-seq of rog mutants"), so a word found only there does
    not count when the sample title names the reference or wild type (`reference`)."""
    sample = TRANSGENE.search(run.get("sample_title", ""))
    study = TRANSGENE.search(run.get("experiment_title", ""))
    match = sample or (study if not reference.search(run.get("sample_title", "")) else None)
    return f"title suggests a transgene, mutant or treatment ({match.group(1)})" if match else ""


def title_label(run: Run) -> str:
    return f"{SEQUENCING_PREFIX.sub('', run.get('experiment_title', ''))} | " + run.get(
        "sample_title", ""
    )


class StudyTitles:
    """Within-study title rules: a run is flagged when other titles of its study carry a
    wild-type or control marker that its own title lacks, or when its own title names a
    treatment (without a control marker) or a mutant/transgene."""

    def __init__(self, species: Species, study_runs: Sequence[Run]) -> None:
        self.wild_type = re.compile(
            f"{TITLE_WILD_TYPE}|{species.title_marker.pattern}", re.I | species.title_marker.flags
        )
        labels = [title_label(run) for run in study_runs]
        self.study_wild_type = any(self.wild_type.search(label) for label in labels)
        self.study_control = any(TITLE_CONTROL.search(label) for label in labels)

    def flag(self, run: Run) -> str:
        label = title_label(run)
        control = TITLE_CONTROL.search(label)
        reasons = []
        if self.study_wild_type and not self.wild_type.search(label):
            reasons.append("no wild-type marker, which other titles of the study have")
        if self.study_control and not control:
            reasons.append("no mock/untreated marker, which other titles of the study have")
        treatment = TITLE_TREATMENT.search(label)
        if treatment and not control:
            reasons.append(f"names a treatment ({treatment.group(1)})")
        mutant = TITLE_MUTANT.search(label)
        if mutant:
            reasons.append(f"names a mutant or transgene ({mutant.group(1)})")
        return (
            "sample title suggests a mutant or treatment: " + "; ".join(reasons) if reasons else ""
        )


def tissue_label(tissue: str) -> str:
    """Tissue as written in sample ids: 'whole plant' -> 'whole-plant'."""
    return re.sub(r"\W+", "-", tissue)
