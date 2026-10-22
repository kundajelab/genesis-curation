# Parameter sensitivity and historical workflow comparisons

For current package comparisons, start with the [benchmark guide](README.md).
Version 1 benchmark adapters support post-alignment policy comparisons and two
pinned historical workflows. Use them to reproduce those experiments; the
`genesis-atac` prototype is separate from the supported bulk ATAC pipeline.

These commands are opt-in experiments. They do not alter the production Nextflow
workflow or scientific defaults. Run from the repository with its locked Pixi
and uv environments. Docker images must already exist at the recorded immutable
identities; no image is pulled automatically.

```bash
genesis benchmark fixture --out ../results/benchmarks/my-dap
genesis benchmark plan ../results/benchmarks/my-dap/experiment.toml
genesis benchmark run ../results/benchmarks/my-dap/experiment.toml --out ../results/benchmarks/my-run
genesis benchmark run ../results/benchmarks/my-dap/experiment.toml --out ../results/benchmarks/my-run --resume
genesis benchmark report ../results/benchmarks/my-run
```

The default fixture contains SE, PE, a shared control, MAPQ boundary classes,
unequal peak depths, duplicate flags and explicitly named organellar contigs.
It tests computation, not plant biological quality. Duplicate flags are injected
for known-answer testing, not a simulation of PCR or library complexity.
The generated JSON dataset and TOML experiment are editable examples. Keep the
originals: every changed input or policy defines a new campaign/output directory.

## Experimental adapters

| Adapter | Input / scope | Scientific boundary |
|---|---|---|
| `postalign` | Existing BAMs, DAP or PE ATAC; factorial MAPQ/duplicate experiments | Starts after alignment; cannot establish aligner accuracy or raw-workflow equivalence |
| `genesis-atac` | Raw PE synthetic reads; clean pinned prototype checkout | Fixed duplicate exclusion, BAMPE, q=0.01, keep-dup all; synthetic only |
| `nfcore-atac` | Raw PE reads; clean pinned nf-core/atacseq 2.1.2 checkout | Initial adapter supports BWA, explicit filtering and peak settings, no trimming; configured workflow, not untouched native defaults |

Generate a raw ATAC fixture using `fixture --assay bulk-ATAC --adapter genesis-atac`
(or `nfcore-atac`) with `--checkout /absolute/path/to/pinned/checkout` and `--out`.
The Genesis prototype commit is `0b8850207e94a3d4909bf4ab99d18c03cd010cc2`.
To run the prototype in `experimental/atac/`, use `--checkout "$PWD"` from a clean
committed checkout. The nf-core checkout is `1a1dbe52ffbd82256c941a032b0e22abbd925b8a`.
Container locks are in `locks/`. Local image IDs are immutable but require that
exact local image or an independently verified exported image on another host;
they are not portable registry download locations. No images are published here.
Unknown process names fail closed instead of using upstream floating tags.

Compare completed raw-workflow campaigns with identical source read bytes and
reference sequence:

```bash
genesis benchmark compare ../results/benchmarks/nfcore-run --against ../results/benchmarks/genesis-run
```

This computes common metrics against the selected reference run's fixed peaks
and retains both policies. It does not force numerical agreement. The comparison
is written under the candidate run without replacing either original result.
Different input bytes, sampling seeds/caps, library sets or assemblies are rejected.

## Contracts and definitions

Dataset JSON version 1 records reference FASTA (compressed and decompressed hashes),
chromosome sizes, species/assembly/cultivar, organellar contig names, optional TSS/GTF
assets, named libraries, biological replicate identity, layout, raw template count
and explicit treatment/control edges. Every executable asset needs its SHA256.
Controls are processed once per arm and shared by declared treatments. No implicit
replicate pooling or sample-name matching is performed.

TOML version 1 records `adapter`, `comparison`, baseline arm, seed, parameter axes,
resource limits and optional repetitions. Unknown fields are rejected.
MAPQ zero means **no MAPQ cutoff**; positive cutoffs require both PE mates and exclude
255 (unknown mapping quality). Duplicate exclusion is separate from marking:
marked BAMs must retain the original alignment/read records, and are supplied as
explicit input artifacts with marking-method provenance. BAM duplicate flags do
not prove PCR origin. Caller `keep_dup` is a separate explicit setting.
DAP retains organellar reads; the experimental ATAC policy excludes declared
organelles. SPP accepts a separate independently aligned first-50-bp input; it does
not shorten the primary analysis BAM. Failed SPP execution remains a failure.

Read FRiP counts overlapping aligned read spans; fragment FRiP counts each accepted
SE span or PE outer span once. Both own-peak and fixed-baseline-peak FRiP are reported.
PE fragments require two primary mapped mates on the same contig. Fixed-region
counts can count one fragment in multiple overlapping regions. Interval union
Jaccard uses bases, not peak counts. Peak rank comparisons use reciprocal maximum
overlap matches, with unmatched peaks reported. Correlations with constant or
insufficient observations are undefined, not zero. TSS scoring uses explicit
Tn5 cut positions, +/-2 kb windows, 100 bp flanks and a documented center/flank
ratio; incomplete chromosome-edge windows are excluded. Native metrics remain
separate. Synthetic edge TSSs can therefore yield undefined enrichment.

Post-alignment quantification preserves RPM and RPKM comparisons on fixed regions,
including absolute changes: correlation alone cannot detect scaling changes.
No universal plant QC threshold or composite quality score is assigned.
Baseline peaks are a measure of change, not independent ground truth. These adapters
do not measure motif support, polyploid homoeolog assignment, mappability-stratified
accuracy, library complexity or biological-replicate concordance. Those measurements
require explicit annotations or independent data and additional metric adapters.

## Reproducibility, resources and artifacts

`experiment.json` records resolved inputs, SHA256s, source commit/recipe hashes,
parameters and declared limits. Commands retain stdout/stderr, exit status,
versions, wall time, GNU time output and available container memory/I/O counters.
Container memory includes page cache; wrapper RSS is not container RSS. Workflow
traces retain task status, resources, containers and actual command hashes.
All attempted runs, including failures, remain available. Cached tasks require
matching recipes, input hashes and output hashes. Stable resume is distinct from
a cold run. No OS cache flushing or security configuration changes occur.

Budgets limit campaign wall time, generated bytes, task CPUs/memory, downloads
and matrix size. Tasks run serially; Nextflow concurrency is bounded. Post-alignment
repetitions can use reproducibly shuffled order (`performance.random_order=true`);
raw-workflow adapters currently reject that option. Repeated timings do not imply
biological replicates or confidence intervals. Host load and warm caches affect
runtime; compare those conditions separately. A raw workflow may set
`parameters.template_cap` for deterministic nested paired subsampling, preserving
complete original FASTQ records and recording selected-file hashes. Downloading
still requires the full input file; subsampling is not a transfer shortcut.

Outputs include `results.json`, `metrics.json`, `metrics.tsv`, `counts.tsv`,
`distributions.tsv`, `library-qc.tsv` (including controls), `resources.tsv`, native
outputs and a self-contained filterable `report.html`. Undefined values include
reasons. Full result JSON retains distribution and tool-native details.

## Original candidate catalogs (historical, blocked)

`catalog/arabidopsis-dap.toml` and `catalog/arabidopsis-atac.toml` are **blocked
candidate plans**, not executable presets. `plan` returns exit 2 with the blocker;
`fetch` refuses unresolved catalogs. The catalogs include small official ENA
metadata snapshots; supply verified FASTQs and exact references before execution.

GSE60141 candidates are FUS3 (SRR2926076), ABI5 (SRR2926841), and shared control
SRR2926068. The repository uses R1 only for this control, although ENA declares its
source library PAIRED. This needs an explicit projection decision, not silent mate
removal. GSE85203 candidates are GSM2260231/SRR4000468 and
GSM2260232/SRR4000469; reported replicate labels still require biological provenance.
The exact TAIR10 FASTA, annotation release, input SHA256s and complete library
metadata are required for `READY` status. An accession is not proof of
biological replicate identity. The Sorghum catalog is blocked on its exact requested
reference; no assembly substitution is allowed.

`fetch` is a separate explicit command for READY manifests with public HTTPS URLs,
exact byte sizes and SHA256s. It rejects credentials/query strings, size overruns,
checksum mismatches and unresolved identities. ENA MD5 snapshots aid provenance
but do not substitute for the manifest's SHA256 requirement.

Sources: [ENA API](https://www.ebi.ac.uk/ena/portal/api/),
[GSE60141](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE60141),
[GSE85203](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE85203),
[nf-core/atacseq 2.1.2](https://github.com/nf-core/atacseq/tree/2.1.2).
