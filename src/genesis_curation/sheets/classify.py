"""Rule-based classes for archive runs: genotype, treatment, tissue and assay.

Sample classes come from the sample's attributes and the run's ENA fields; run flags come
from the run's own experiment and sample titles. Assay and title patterns are bounded by
non-alphanumerics, not `\\b`: an underscore is a word character, so `\\b` misses
`RNAseq_Col0_Polysome`. Species differences (reference genotype, whether a bare gene name
means a mutant) come from `species.yaml`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import cache

import yaml

from .archives import Run, Sample
from .species import DATA, Species

SHORT_READ = {"ILLUMINA", "DNBSEQ", "BGISEQ", "ELEMENT"}
EMPTY = {"missing", "not applicable", "n/a", "na", "-", "not collected", "not provided"}
L, R = r"(?<![a-z0-9])(", r")(?![a-z0-9])"

MUTANT = re.compile(
    r"mutant|knock[- ]?(out|down)|\bko\b|overexpress|\boe\b|\b\w+[- ]ox\b|crispr|cas9|\brnai\b|"
    r"amirna|t-dna|transgen|\bsalk[_ ]|\bsail[_ ]|\bgk[-_]\d|\bwisc|\bflag[_ ]\d|"
    r"\b[a-z]{2,6}\d{1,3}-\d{1,3}\b|\b[a-z]{2,6}\d{1,3}\s*x\s*[a-z]|"  # alleles: abi3-1, pif4-101
    r"complement|reporter|gfp|gus\b|::|\bline\s*\d|\w-/-|"  # wrky22-/-: homozygous mutant
    r"\bp[a-z0-9]{2,8}:[a-z]|\bntf\d?\b|\bintact\b",  # promoter fusions: p35S:NTF2, pCMZ:NTF
    re.I,
)
# BioSample `isolate` is free text ("WT biorep 3", "iso1", "Col-rep2"), so it counts only when
# it names a mutant or transgene outright and does not also say wild type ("CRISPR wide type").
ISOLATE_MUTANT = re.compile(
    r"mutant|knock[- ]?(out|down)|\bko\b|overexpress|\boe\b|crispr|cas9|\brnai\b|amirna|"
    r"t-dna|transgen",
    re.I,
)
ISOLATE_WILD_TYPE = re.compile(r"wil?de?[- ]?type|\bwt\b", re.I)
GENE_ALLELE = re.compile(r"\b(at)?[a-z]{3,6}\d{1,3}[a-z]?\b", re.I)
WILD_TYPE = re.compile(r"wild[- ]?type|\bwt\b|wildtype", re.I)
ONLY_WILD_TYPE = re.compile(r"(\s*\|?\s*(wild[- ]?type( genotype)?|wt|wildtype)\s*)+", re.I)
OTHER_GENOTYPE = re.compile(r"natural|ecotype|accession|cultivar|landrace", re.I)
CROSS = re.compile(r"\bx\b|×|\w\*\w|crossed|\bf1\b|hybrid", re.I)
GERMPLASM = re.compile(r"(?<![a-z])pi ?\d{5,6}\b", re.I)
CONTROL = re.compile(
    r"^\s*(none|no|n/?a|control|ctrl|mock|untreated|normal|non[- ]?treated|well[- ]?watered|ww|"
    r"standard|optimal|ambient|day ?0|0 ?h|time ?0|baseline|unstressed|no treatment|"
    r"water(?!\s+(deficit|stress|limit\w*|withh\w*|deprivation|logg\w*)))\b"
    r"|\b(control|mock|untreated)\b",
    re.I,
)
AFTER_TREATMENT = re.compile(
    r"(?<![\w.])(?!0+\s)\d+(\.\d+)?\s*\w*\s+after\s+(\w+\s+){0,2}"
    r"(infestation|infection|inoculation|treatment|exposure|spraying)",
    re.I,
)
# Mock inoculations name the buffer they used ("inoculated with 2ul PBS").
MOCK = re.compile(r"\bmock\b|\bpbs|buffer (only|alone)|water (only|alone)", re.I)
# Growth protocols describe routine conditions too ("16 h light, 22 C, half MS"), so only
# words that name a deprivation or stress count there.
PROTOCOL_TREATED = re.compile(
    r"deficit|deficien|starvation|deprivation|limitation|drought|salt stress|\bnacl\b|"
    r"heat stress|cold stress|chilling|freezing|flood|submergence|hypoxia|infect|inocul",
    re.I,
)
TREATED = re.compile(
    r"stress|drought|salt|nacl|cold|heat|hypoxia|flood|osmotic|mannitol|pathogen|infect|infest|"
    r"pseudomonas|botrytis|fungal|virus|aphid|insect|elicitor|flg22|chitin|aba\b|abscisic|"
    r"auxin|iaa|naa|cytokinin|gibberell|ga3|ethylene|acc\b|jasmon|meja|salicyl|brassino|"
    r"treat|inhibitor|dex|estradiol|herbicide|cadmium|metal|nitrogen|phosphate|deficien|deficit|"
    r"light|dark|uv|ozone|wound|deprivation|limitation|starvation|hours? after|h after",
    re.I,
)
# Bulk ATAC treatments: no bare light/dark (sample protocols state the photoperiod, "16 h
# light") and `dex` only as a word (not "Index" in kit names); adds regeneration designs
# (callus- and shoot-inducing media, explants), 2,4-D and infection time courses.
ATAC_TREATED = re.compile(
    r"stress|drought|salt|nacl|cold|heat|hypoxia|flood|osmotic|mannitol|pathogen|infect|infest|"
    r"pseudomonas|botrytis|fungal|virus|aphid|insect|elicitor|flg22|chitin|aba\b|abscisic|"
    r"auxin|iaa|naa|\b2,?4-?d\b|cytokinin|gibberell|ga3|ethylene|acc\b|jasmon|meja|salicyl|"
    r"brassino|treat|inhibitor|\bdex\b|dexamethasone|estradiol|herbicide|cadmium|metal|nitrogen|"
    r"phosphate|deficien|deficit|submerg|waterlog|uv|ozone|wound|deprivation|limitation|"
    r"starvation|hours? after|h after|"
    r"\bcim\b|\bsim\b|regenerat|callus induc|hypocotyl explant|infiltrat|inoculat|\bhpi\b|\bdpi\b",
    re.I,
)
# First match wins, in this order; "shoot" alone is checked after leaf and stem.
TISSUES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.I))
    for name, pattern in (
        # "suspension" alone also names bacterial inocula in protocol text (PRJEB13938).
        (
            "cell culture",
            r"protoplast|cell culture|cell suspension|suspension cultur\w*|suspension cells?|"
            r"callus|cell line",
        ),
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
# Libraries in a bulk ATAC search that are not plain bulk ATAC: controls (input, genomic DNA,
# Tn5 on naked DNA), other chromatin assays and single-cell or capture designs. The right
# boundary allows digits ("input1"); "gDNA" is often glued to a sample name ("Col0_WTgDNA").
ATAC_ASSAY = re.compile(
    r"(?<![a-z0-9])(input|gdna|genomic dna|naked dna|purified dna|tn5 (only|control)|"
    r"chip|cut&(run|tag)|cut ?and ?(run|tag)|mnase|dnase|faire|moa-?seq|hi-?c|bs-?seq|bisulfite|"
    r"rna-?seq|sc-?atac|snatac|sci-?atac|single[- ]?(cell|nucle)|10x|plate ?\d|"
    r"ribo|nanopore|long[- ]read)(?![a-z])",
    re.I,
)
GLUED_GDNA = re.compile(r"(gdna)(?![a-z])", re.I)
ATAC_SELECTIONS = {"ChIP", "DNase", "MNase"}
SHAPE = re.compile(r"(?<![A-Za-z0-9])SHAPE(?![A-Za-z0-9])")  # upper case only: "leaf shape"
TRANSGENE = re.compile(
    L + r"expressing|overexpress\w*|over ?expression|over-?expressing|transgenic|35s|"
    r"crispr|cas9|amirna|rnai|knock-?down|knock-?out|complemented|complementation|gfp|yfp|"
    r"mcherry|oe|ox|ko|kd|mutants?|t-?dna|ems|irradiat\w*|"
    # inducible and tagged lines: VP16-GR, pER8-LEC2, GVG-NtMEK2DD, HyperTRIBE, VPS34 DN line
    r"[a-z0-9]+-gr|per8|gvg|hypertribe|dn|dominant[- ]negative" + R,
    re.I,
)
# A sample alias that is an accession or a GEO/ArrayExpress id says nothing about the sample.
ACCESSION_ALIAS = re.compile(r"^(sam[nde][a-z]?\d|gsm\d|[sed]rs\d|e-[a-z]+-\d+:?$)", re.I)
# A mutant allele as aliases write it, in lower case (pad4-1 in bon123pad4-1, gsnor1-3, ga1-13).
# Upper case is a protein, element or treatment (ENO2-1 protein, Pb0-1 lead, MAC5-1), and
# replicate, batch, control and treatment tokens look the same (rep1-2, ck1-0, flg22-2).
ALIAS_ALLELE = re.compile(
    r"(?<![a-zA-Z])((?!(rep|flg|ck|np|set|run|day|exp|br|bio|tech|lane|plate|batch|sample|block|"
    r"time|pgp|pp|col|s|r|t)\d)[a-z]{2,6}\d{1,3}-\d{1,3})(?![a-zA-Z0-9])"
)
# Within-study title rules (from the ATAC curation): markers other titles of the study use.
TITLE_WILD_TYPE = r"(?<![a-z])(wt|wild[- ]?type|wildtype)(?![a-z])"
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


def classify_sample(
    species: Species,
    sample: Sample,
    run: Run,
    treated: re.Pattern[str] = TREATED,
    more_genotype: Sequence[str] = (),
    more_descriptive: Sequence[str] = (),
) -> SampleClass:
    """Genotype, treatment and tissue of a sample from its attributes and one of its runs.
    `treated` names treatments; `more_genotype` adds attribute names read as genotype and
    `more_descriptive` run fields read as description (titles)."""
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
        *(a.get(name) or run.get(name) for name in more_genotype),
    )
    descriptive = joined(
        sample.title,
        sample.description,
        a.get("sample comment"),
        a.get("source_name"),
        run.get("sample_title"),
        run.get("experiment_title"),
        *(run.get(name) for name in more_descriptive),
    )
    gene_allele = (
        species.gene_names_mean_mutant
        and GENE_ALLELE.search(genotype_text)
        and not re.search(r"\bcol\d|wild[- ]?type|\bwt\b", genotype_text, re.I)
    )
    isolate = a.get("isolate") or ""
    mutant = (
        MUTANT.search(genotype_text)
        or any(term.search(genotype_text) for term in species.mutant_terms)
        or (ISOLATE_MUTANT.search(isolate) and not ISOLATE_WILD_TYPE.search(isolate))
    )
    # a bare "MT" or "mut" in the genotype field, beside "WT" samples of the same study
    bare_mutant = re.fullmatch(r"\s*(mt|mut)\s*", a.get("genotype") or "", re.I)
    if mutant or gene_allele or bare_mutant or (not genotype_text and MUTANT.search(descriptive)):
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
    # A growth protocol or stage can state a treatment that the treatment field denies
    # ("treatment: No treatment", "growth_protocol: Phosphorous deficit"; "dev_stage: 4 days
    # after aphid infestation").
    condition = joined(a.get("growth_protocol"), a.get("growth protocol"))
    stage = joined(a.get("dev_stage"), a.get("development stage"), run.get("dev_stage"))
    if (
        condition
        and PROTOCOL_TREATED.search(condition)
        and not CONTROL.search(condition)
        and not MOCK.search(condition)
    ) or (AFTER_TREATMENT.search(stage)):
        treatment = "treated"
        treatment_text = joined(treatment_text, condition, stage)
    elif treatment_text:
        treatment = (
            "control / untreated"
            if CONTROL.search(treatment_text)
            else "treated"
            if treated.search(treatment_text)
            else "other condition"
        )
    else:
        treatment = "treated" if treated.search(descriptive) else "not stated"

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
    curated = curated_treatment(species.folder, run)
    if curated:
        treatment = "treated"
        treatment_text = joined(curated, treatment_text)
    return SampleClass(
        genotype=genotype,
        treatment=treatment,
        tissue=tissue,
        genotype_text=joined(genotype_text, isolate and f"isolate: {isolate}")[:200],
        treatment_text=treatment_text[:200],
        tissue_text=next((s for s in sources if s), "")[:200],
    )


@cache
def curated_treatments(folder: str) -> dict[str, str]:
    """data/treatments.yaml for one species: run or study accession -> treatment."""
    data = yaml.safe_load((DATA / "treatments.yaml").read_text()) or {}
    table: dict[str, str] = {}
    for entry in data.get(folder) or []:
        for key in entry.get("runs") or [entry["study"]]:
            table[key] = entry["treatment"]
    return table


def curated_treatment(folder: str, run: Run) -> str:
    """A treatment that only the study text states, from data/treatments.yaml, or ''."""
    table = curated_treatments(folder)
    return table.get(run.get("run_accession", ""), "") or table.get(
        run.get("study_accession", ""), ""
    )


def is_reference(value: SampleClass) -> bool:
    return value.genotype.startswith("reference")


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


def atac_assay_flag(run: Run, *titles: str) -> str:
    """'not bulk ATAC (...)' when the library source or selection, the run's titles (plus
    `titles`, such as the sample's own title) or the study title name another design, or ''."""
    if "SINGLE CELL" in run.get("library_source", ""):
        return f"not bulk ATAC (library_source {run['library_source']})"
    if run.get("library_selection") in ATAC_SELECTIONS:
        return f"not bulk ATAC (selection {run['library_selection']})"
    text = joined(
        run.get("experiment_title"), run.get("sample_title"), run.get("sample_alias"), *titles
    )
    match = ATAC_ASSAY.search(text) or GLUED_GDNA.search(text)
    if match:
        return f"not bulk ATAC (title: {match.group(1)})"
    if re.search(r"capture ATAC", run.get("study_title", ""), re.I):
        return "not bulk ATAC (study: capture ATAC)"
    return ""


def stated_genotype(sample: Sample) -> str:
    """The sample's own genotype or isolate field ("WT biorep 3", "pgdh3-1"), or ''."""
    a = sample.attributes
    return joined(a.get("genotype"), a.get("isolate"))


def states_wild_type(genotype: str) -> bool:
    """A genotype field that says wild type and names no transgene."""
    return bool(re.search(TITLE_WILD_TYPE, genotype, re.I)) and not TRANSGENE.search(genotype)


def transgene_flag(run: Run, reference: re.Pattern[str], genotype: str = "") -> str:
    """A transgene or mutant word in the run's titles. Titles often describe the
    whole study ("RNA-seq of rog mutants"), so a title word does not count when:
    - the sample's own genotype field (`genotype`) says wild type and names no transgene;
    - it is found only in the experiment title and the sample title names the reference or
      wild type (`reference`);
    - it is found only in the sample title, which also names wild type ("WT, arp6 and klu
      mutants"), and the experiment title names wild type without one."""
    if states_wild_type(genotype):
        return ""
    sample_title = run.get("sample_title", "")
    experiment_title = run.get("experiment_title", "")
    sample = TRANSGENE.search(sample_title)
    study = TRANSGENE.search(experiment_title)
    if sample and not study and reference.search(sample_title):
        sample = None if reference.search(experiment_title) else sample
    match = sample or (study if not reference.search(sample_title) else None)
    return f"title suggests a transgene or mutant ({match.group(1)})" if match else ""


def alias_flag(run: Run, reference: re.Pattern[str]) -> str:
    """A transgene word in the sample alias, which submitters often use for the line name
    ("bon123pad4-1_MOCK1", "OE-E2Fa-DPa-3") while the genotype field gives the background. An
    alias that names the reference or wild type, or is an accession, does not count."""
    alias = run.get("sample_alias", "").strip()
    if not alias or ACCESSION_ALIAS.match(alias):
        return ""
    # A promoter fusion names a transgene even beside the background ("WT/p35S::CUC2:GR").
    if "::" in alias:
        return "sample alias suggests a transgene or mutant (::)"
    if reference.search(alias):
        return ""
    # Irradiation and EMS name a treatment of the sample ("Irradiated or unirradiated").
    words = (m for m in TRANSGENE.finditer(alias) if not re.match(r"(?i)irradiat|ems$", m.group(1)))
    match = next(words, None) or ALIAS_ALLELE.search(alias)
    return f"sample alias suggests a transgene or mutant ({match.group(1)})" if match else ""


# Protoplasts and callus named outside the tissue fields ("Pooled protoplasts from ... leaf",
# "At_root_proto_RNA", "Callus_0day_WT", dev_stage "after protoplast"); gold v6.
PROCESSED_CELLS = re.compile(
    r"(?<![a-z])(?:protoplast\w*|proto(?=[_\W])|call(?:us|i))(?![a-z])", re.I
)
# Bulk controls of protoplasting studies, and callus induction on explants (a treatment).
INTACT_TISSUE = re.compile(
    r"before protoplast|without protoplast|no protoplast|non-?dissociat|\bbulk\b|callus[ _-]induc",
    re.I,
)


def cell_culture_flag(run: Run) -> str:
    """Protoplasts or callus named in the sample alias, sample title, experiment title or
    dev_stage of a run whose tissue field names an organ. Protocol text is not read: one
    protocol often describes both the protoplasts and the bulk tissue of a study."""
    for field in ("sample_alias", "sample_title", "experiment_title", "dev_stage"):
        text = run.get(field, "")
        if PROCESSED_CELLS.search(text) and not INTACT_TISSUE.search(text):
            return f"{field}: {text[:80]}"
    return ""


def alias_candidates(study_runs: Sequence[Run], reference: re.Pattern[str]) -> set[str]:
    """Runs of one study whose sample alias and sample title both lack the wild-type marker
    that other aliases of the study carry ("hda19-3_rep1" next to "Col-0_rep1"). About 3 in
    4 such runs are mutant or transgenic lines (gold v3); the rest are treated wild type
    ("PFBS_treated_1"), so these runs go to the metadata review, not straight out."""

    def named(run: Run) -> str:
        alias = run.get("sample_alias", "").strip()
        return "" if not alias or ACCESSION_ALIAS.match(alias) else alias

    if not any(named(run) and reference.search(named(run)) for run in study_runs):
        return set()
    return {
        run["run_accession"]
        for run in study_runs
        if named(run)
        and not reference.search(named(run))
        and not reference.search(run.get("sample_title", ""))
    }


def title_label(run: Run) -> str:
    return f"{SEQUENCING_PREFIX.sub('', run.get('experiment_title', ''))} | " + run.get(
        "sample_title", ""
    )


TITLE_CONTROL = re.compile(
    r"(?<![a-z])(control|cont|ctrl|ck\d*|mock|untreated|normal|0 ?(h|d|w|min|hr|day)|day ?0|t0)"
    r"(?![a-z])",
    re.I,
)
TITLE_TREATMENT = re.compile(
    r"(?<![a-z])(heat|cold|drought|salt|nacl|aba|mej?a|lethal|recovery|stress|treat\w*|"
    r"infect\w*|case|\d+ ?(h|hr|hpi|dpi|min))(?![a-z])",
    re.I,
)


class StudyTitles:
    """Within-study title rules: a run is flagged when other titles of its study carry a
    wild-type marker that its own title lacks, or when its own title names a mutant or
    transgene. Treatments are kept (issue #13): `record` adds a treatment that only the
    titles show (a treatment word, or no control marker where the study's other titles
    have one) to the run's sample class."""

    def __init__(
        self,
        species: Species,
        study_runs: Sequence[Run],
        label: Callable[[Run], str] = title_label,
    ) -> None:
        self.wild_type = re.compile(
            f"{TITLE_WILD_TYPE}|{species.title_marker.pattern}", re.I | species.title_marker.flags
        )
        self.label = label
        labels = [label(run) for run in study_runs]
        self.study_wild_type = any(self.wild_type.search(label) for label in labels)
        self.study_control = any(TITLE_CONTROL.search(label) for label in labels)

    def treatment(self, run: Run) -> str:
        """A treatment the titles show, or ''."""
        label = self.label(run)
        if TITLE_CONTROL.search(label):
            return ""
        named = TITLE_TREATMENT.search(label)
        if named:
            return f"title: {named.group(1)}"
        return (
            "title lacks the control marker of the study's other titles"
            if (self.study_control)
            else ""
        )

    def record(self, value: SampleClass, run: Run) -> SampleClass:
        """The sample class with a treatment from the titles added, when the sample record
        states none."""
        found = self.treatment(run) if value.treatment != "treated" else ""
        if not found:
            return value
        return replace(
            value, treatment="treated", treatment_text=joined(found, value.treatment_text)
        )

    def flag(self, run: Run) -> str:
        label = self.label(run)
        reasons = []
        if self.study_wild_type and not self.wild_type.search(label):
            reasons.append("no wild-type marker, which other titles of the study have")
        mutant = TITLE_MUTANT.search(label)
        if mutant:
            reasons.append(f"names a mutant or transgene ({mutant.group(1)})")
        return "sample title suggests a mutant: " + "; ".join(reasons) if reasons else ""


def tissue_label(tissue: str) -> str:
    """Tissue as written in sample ids: 'whole plant' -> 'whole-plant'."""
    return re.sub(r"\W+", "-", tissue)
