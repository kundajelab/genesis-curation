# RNA-seq curation and measured QC

RNA-seq has the most complete support in the curation app: evidence rules, imported
measured QC and cohort selection. The design follows the RNA-seq example in the project
slides (30–35). The workflow is the same as for other assays (Discover,
Curate, Review). The app does not run sequencing pipelines, and the results reported in the
slides are not results of this implementation.

For RNA-seq sample sheets for genesis-preprocessing, use `genesis sheets build --workflow
rna-seq` instead; see [sample sheets](sample-sheets.md).

## Discover and annotate

Choose **17-species RNA-seq pilot** or enter plant scientific names. Optional tissue,
genotype, treatment and library-selection requirements add focused searches while
preserving broad archive searches. Requirements travel with discovery records and are
assessed as supported, contradicted or unresolved. Missing evidence never proves that
a sample satisfies a requirement. Constraints are exact text/ontology comparisons,
not a general natural-language entailment engine.

RNA preparation checkpoints archive annotation, targeted paper retrieval, paper
annotation and validation. Pausing or restarting retains completed batches and source
snapshots. The same sequence serves accession intake and table curation. No live
provider means no automated literature pass; mock runs report this explicitly.

Library source, selection, preparation, strandedness, platform, age, age units,
reference assembly and cell resolution have separate fields. RNA-seq alone does not
prove poly(A) selection or bulk rather than single-cell/nucleus profiling. Unmapped
values remain readable; a live, exact NCBI taxonomy lookup can resolve plant species
beyond the small pinned vocabulary. Lookup identity, retrieval time, source hash and
snapshot are recorded separately from Plant Ontology annotations. No new ontology IDs
are invented. Tissue/stage normalization continues to use the pinned plant vocabulary.

When key fields are missing, archive-linked PubMed papers are retrieved first. Other
Europe PMC hits remain citation leads unless a data-availability section explicitly
names the study accession. A passage can inform a record only if an exact sample/run
ID or a unique, informative archive label maps it to that record. Paragraphs naming
multiple records are not assigned to individual samples. Named GEO metadata supplements
use the existing bounded, exact-accession parser. Literature searches are bounded;
closed papers, failed fetches, unmatched labels and unsupported formats stay visible.
No study-wide tissue or treatment is copied to every sample. Reviewed records are
preserved when new automated evidence arrives.

Curation includes supported-field counts and paper-evidence coverage. Review shows
archive-versus-enriched counts, and groups questions by study. These are evidence
coverage measures, not calibrated accuracy scores. Contradicted/unresolved requested
constraints enter the checklist; human decisions and unknowns remain in the record.

## Import measured QC

In **Curation → RNA-seq → RNA-seq quality**, open **Import measured QC** and upload a
CSV or TSV file (or use `genesis curation qc import FILE`). Measurements must come from an identified external pipeline or report. The
application does not estimate read quality from metadata or run preprocessing.

Required columns:

| Column | Meaning |
| --- | --- |
| `subject` | Exact record UUID from the metadata export; avoids ambiguous accession joins |
| `metric` | `q30_fraction`, `rrna_fraction`, `junction_fraction` or `mapped_fraction` |
| `value`, `unit` | Numeric measurement; `fraction` (0–1) or `percent` (0–100) |
| `method` | Measurement definition/tool; distinguish read-, base- and alignment-level metrics |
| `reference` | Assembly/version, or an explicit `not-applicable` for reference-free measurements |
| `source` | Original report URI or artifact identifier |
| `pipeline_version`, `run_id` | Reproducible measurement provenance |

Each upload is immutable and retains original rows, units, actor and SHA-256. A later
upload updates the current measurement and invalidates affected QC evaluations. Limits
are 2 MiB and 15,000 measurements per file; split larger tables. Invalid or duplicate
subject/metric rows reject the entire upload.

## Selection policy and review

The default policy targets bulk poly(A) RNA-seq. The alternative retains any bulk
RNA library selection. Other library types remain in the metadata inventory. An
explicit non-RNA/genomic source or single-cell protocol is outside this bulk cohort;
missing bulk/selection evidence is **Not evaluated**, not excluded by inference.

Thresholds start empty because the slides do not supply numerical Q30, rRNA or
junction thresholds. Enter scientific criteria as fractions. An empty threshold set,
a missing required measurement or an unknown protocol cannot produce a QC pass.
Policy content is versioned; a new evaluation creates a new snapshot.

Outlier checks use 1.5 × IQR and at least 20 comparable observations. Cohorts share
species, assay, selection, preparation/protocol, source, platform, strandedness,
reference, measurement method, units and pipeline version. Only library-eligible
records passing configured thresholds enter the comparison. Unknown preparation,
small cohorts and zero IQR remain **Not evaluated**. Outliers are flags for human
review, not automatic scientific exclusions. Turn outlier checks off explicitly if
your policy only requires fixed thresholds; that choice is preserved in the profile.

The funnel counts metadata records, not unique biological samples. Separate identity
counts are retained in the evaluation. The current scope is RNA-seq candidates present
at evaluation time, including records originally assigned to that modality. Species
counts show pass, excluded, needs review and not evaluated.

In **Review**, select a study and examine its **Quality decisions**. Select explicit
records and retain, exclude or defer them with a reason. Retention can resolve an
outlier flag only when library and fixed-quality checks pass. Decisions are append-only;
a stale review cannot replace a newer review. Changed QC-relevant metadata, evidence
or measurements require reevaluation. Unsaved QC choices block navigation.

## Releases

Study sign-off releases metadata independently of measured QC. **Export QC-filtered
cohort** additionally requires current metadata sign-off and passing configured QC
(or an explicitly retained outlier). Its JSON bundle contains retained records,
excluded/unresolved records, profiles, measurements, original imports, all QC decisions
and signed-study release references. A metadata-only export never asserts read-level
QC. Original source data and decisions are not overwritten.

## Reproduce the pilot

Use a fresh runtime outside the checkout; set `TMPDIR` to the workspace temporary
directory. This script retrieves public metadata, never FASTQ/BAM files:

```sh
uv run --frozen --extra web python examples/curation/rna_pilot.py collect --root /private/rna-pilot
uv run --frozen --extra web python examples/curation/rna_pilot.py ingest --root /private/rna-pilot
uv run --frozen --extra web python examples/curation/rna_pilot.py curate --root /private/rna-pilot \
  --providers /private/providers.json --provider LOCAL_PROVIDER_ID
uv run --frozen --extra web python examples/curation/rna_pilot.py report --root /private/rna-pilot
```

The selected manifest targets 10 records for each of 17 species plus 30 records with
non-poly(A)/unknown selection. This is a deterministic convenience sample, not an
expert-labelled or random accuracy set. Retrieval failures remain recorded. Annotation
is checkpointed and uses the configured local model; the pilot command refuses hosted
providers. Live accuracy and larger-corpus throughput need separate evaluation.

`tests/verify_rna_scale.py` exercises 15,000 synthetic rows, compact selection submission,
pagination and restart recovery. `tests/verify_qc.py` checks measured-QC safety, while
`tests/verify_rna_workflow.py` checks paper binding, enrichment checkpoints and taxonomy.
These checks are included in `scripts/check.sh`. `tests/browser_rna.cjs` verifies the
GUI in an isolated loopback deployment with synthetic records and measurements.

## Slide-to-feature checklist

| Slides | Implemented | Validation still required |
| --- | --- | --- |
| 30–32: discovery, metadata annotation, literature | 17-species preset; shared staged workflow; exact paper binding; study review | Expert-labelled tissue, genotype and condition accuracy |
| 33: library, measured quality and IQR filtering | Versioned selection profiles; imported measurements; explicit missing-data state; reviewable outliers | Agreed scientific thresholds and actual measured QC corpus |
| 34: different failure patterns by species | Species counts, explicit denominator and reproducible evaluation snapshot | Representative per-species evaluation |
| 35: 15,034 annotations in two days | Persisted large selections; checkpointed preparation; usage/runtime reporting | An independently measured large live run |

The slides also report 15,912 records entering QC and 10,782 passing. Their relationship
to the 15,034 annotation count is not established here. None of these figures are
claimed as results of this implementation.
