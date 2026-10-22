# Public plant method comparisons

This panel compares DAP-seq and bulk ATAC tools on identical, randomly selected
input templates. It uses separate outputs from normal Genesis runs. Package
comparisons, complete-workflow acceptance and biological evidence are reported
separately.

## Data and questions

| Cohort | Libraries | Question |
| --- | --- | --- |
| Arabidopsis SE DAP | FUS3 `SRR2926076`, ABI5 `SRR2926841`, shared control `SRR2926068` | Do aligner/caller changes preserve TF motif support and enrichment? |
| Arabidopsis PE DAP | `SRR31238589`, `SRR31239194`, `SRR31238722`, `SRR31238919`, shared control `SRR31238993` | Do results hold for paired reads and different library yields? |
| Arabidopsis ATAC | `SRR4000468`, `SRR4000469` | How do selection, TSS signal and agreement between biological replicates change? |
| Maize ATAC | `SRR27443454`, `SRR27443453` | How do the same choices behave on a larger plant genome and adapter-containing libraries? |

The SE DAP control is an explicit R1-only projection of an ENA paired-end run,
matching the repository's sample sheet. The PE DAP samples are Halo protocol
optimization libraries, **not confirmed biological replicates**. The two TF groups
remain distinct and share only their declared control. Their TF-source organism
must not be confused with the assayed Arabidopsis thaliana DNA.

The Arabidopsis ATAC libraries represent Col-0, seven-day whole seedlings and
50,000 FANS-isolated nuclei. The maize libraries represent B73, ten-day seedlings
and 2,500 nuclei. Both cohorts have two biological replicates. The maize publication
used gDNA controls; this panel's control-free ATAC comparison does not reproduce
that complete analysis. See [Lu et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC5389718/)
and the [maize study](https://doi.org/10.3389/fpls.2024.1370618).

The first 100 records from each archived Arabidopsis ATAC mate are 31 bases long.
The benchmark processes the archived sequences as supplied. The original sequencing
read length must not be substituted for the length of these files.

[ENA, SRA and BioSample snapshots](sources/) preserve the accession, layout and
sample evidence. The [acquisition manifest](acquisition.json) pins public URLs,
publisher sizes and checksums. It includes complete FASTQs, not HTTP prefixes.
The preparer validates all records before taking nested 1-million and 5-million
template samples. A PE template contains both mates. Smaller libraries retain
their actual complete count; the requested cap is never reported as an observed
count.

References are TAIR10 with Ensembl Plants 59 annotation and B73 RefGen v4 with
Ensembl Plants 46 annotation. The exact files and derived TSS hashes are checked
by [prepare.py](prepare.py). These are explicit benchmark reference bundles;
matching an assembly name does not prove identity with the original paper's files.

## Software and baseline

| Component | Baseline | Alternative |
| --- | --- | --- |
| Alignment | BWA-MEM2 2.3 | BWA 0.7.19, Bowtie2 2.5.5; optional older BWA-MEM2 2.2.1 |
| ATAC adapter handling | Declared full-read policy | Cutadapt 5.2, fastp 1.4.0 |
| ATAC duplicate marking | samtools 1.24 | Picard 3.5.0 |
| Peak calling | MACS3 3.0.5 | MACS2 2.2.9.1; Genrich 0.6.2 for ATAC |
| Independent read-end counts | Python interval counts | bedtools 2.31.1 |

The baseline matches the production aligner binaries and scientific settings.
Its banner prints 2.2.1, but the package is 2.3 and all six executable hashes
match production. [The recorded identity check](sources/production-aligner-identity.json)
and [upstream report](https://github.com/bwa-mem2/bwa-mem2/issues/283) explain this
version-string error. The compiler checks these hashes before writing a comparison.
The [comparison lock](../../environment/pixi.lock) pins the native environment.
The production and native samtools/htslib packages also match. MACS3 is 3.0.5
in both environments, but production uses its Python 3.13 build and the comparison
uses Python 3.12. These are different package artifacts, recorded in the
[package identity check](sources/production-method-package-identity.json).
The native matrix is a component comparison, not a byte-identical copy of every
production container. Complete-workflow results provide a separate check.

An optional [older 2.2.1 environment](../../environment/bwa-mem2-2.2.1/pixi.lock)
is used only with `compile_matrix.py --include-legacy`.

Experiment IDs containing `v4` use the older BWA-MEM2 2.2.1 package as baseline;
`v5` uses the verified production binaries. Keep those identities when reproducing
results, and do not pool measurements from the two baselines.
MACS2 is built from its pinned source distribution in a separate Python 3.11
environment. See the [comparison report](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/pipeline-method-comparison.md)
for the import failure with the tested Bioconda binary and the source-build details.

The [main benchmark guide](../../README.md#what-is-compared) defines the fixed
policies and caller differences. The panel does not lower MAPQ or change q-values
to make a method pass. Zero peaks remain a measured result. One change per method
isolates the component being tested; comparisons of the complete production and
nf-core workflows have a different scope.

## Reproduce a cohort

Install Genesis, then run from its checkout:

```bash
pixi install --all --manifest-path benchmarks/environment/pixi.toml
```

For the optional older aligner comparison, also run
`pixi install --manifest-path benchmarks/environment/bwa-mem2-2.2.1/pixi.toml`
and add `--include-legacy` when compiling the matrix.

The full input manifest downloads **86.01 GB** before retries and software
downloads. Its transfer limit is 100 GB. Review the manifest and choose storage
with room for original inputs plus temporary work. The recorded panel budget is
16 CPUs, 96 GiB RAM, 48 hours of execution and 80 GB of temporary work across all
experiments. Per-experiment limits do not enforce that aggregate automatically.

```bash
genesis benchmark acquire benchmarks/experiments/plant-methods-20261013/acquisition.json \
  --out /data/genesis-method-sources --connections 4

uv run --frozen python \
  benchmarks/experiments/plant-methods-20261013/prepare.py \
  --sources /data/genesis-method-sources --out /scratch/dap-se-inputs \
  --cohort dap-se --depth 1000000 --depth 5000000

uv run --frozen python \
  benchmarks/experiments/plant-methods-20261013/compile_matrix.py \
  /scratch/dap-se-inputs/dap-se-depth-1000000.json \
  --out /scratch/dap-se-inputs/comparison-1m.json --repetitions 3 --cpus 14

genesis benchmark run /scratch/dap-se-inputs/comparison-1m.json \
  --out /scratch/dap-se-comparison-1m
genesis benchmark report /scratch/dap-se-comparison-1m \
  --plot-python benchmarks/environment/.pixi/envs/default/bin/python
```

Other cohort names are `dap-pe`, `atac-arabidopsis` and `atac-maize`. Use a separate
preparation and result directory for each. Repeat compilation with the generated
5-million-template manifest for the depth comparison. For maize, the compiler's
`--group mapping` and `--group processing` split the matrix into smaller campaigns,
each including the identical baseline. Keep the same inputs and sampling seed.
Report every timing repetition and any incomplete group; do not pool replicates.

Preparation can resume completed samples only when their sources, selection seed,
depths and output hashes agree. A failed acquisition can resume its original
destination; partial files do not count as verified sources. Benchmark execution
uses `--resume` only with unchanged source, environments and experiment identity.

## Compare complete ATAC workflows

Component timings omit QC reports and tracks. Run a separate workflow comparison
to include those steps. [prepare_workflows.py](prepare_workflows.py) copies verified
inputs into a Docker-visible directory and writes both Genesis inputs and a pinned
nf-core experiment. It leaves the original inputs unchanged.

The [nf-core container lock](nfcore-containers.json) pins the linux/amd64 images.
Make those images and the Genesis images available locally before execution;
the comparison refuses to pull an unpinned image during a task. The nf-core images
have 2.88 GB of unique compressed layers before local cache reuse.

```bash
git clone --branch 2.1.2 https://github.com/nf-core/atacseq.git /scratch/nfcore-atacseq
pixi run nextflow plugin install nf-validation@1.1.4
uv run --frozen python \
  benchmarks/experiments/plant-methods-20261013/prepare_workflows.py \
  /scratch/atac-inputs/atac-arabidopsis-depth-1000000.json \
  --out /scratch/workflow-comparison \
  --nfcore-checkout /scratch/nfcore-atacseq \
  --container-lock benchmarks/experiments/plant-methods-20261013/nfcore-containers.json

genesis pipeline-atac /scratch/workflow-comparison/samples.tsv \
  --references /scratch/workflow-comparison/references.json \
  --config /scratch/workflow-comparison/genesis.config \
  --outdir /scratch/workflow-comparison/genesis-results
genesis benchmark run /scratch/workflow-comparison/nfcore.json \
  --out /scratch/workflow-comparison/nfcore-results
```

Run the two workflows sequentially. This recipe gives each workflow 14 CPUs,
80 GiB RAM and a two-hour task limit. Genesis processes both libraries in one run;
the existing nf-core adapter launches each library separately and builds its own
index each time. Report those costs explicitly. One execution checks integration
and outputs; it does not establish a stable runtime ranking.

The workflows have different policies: Genesis uses BWA-MEM2, both-mate MAPQ30,
Mt/Pt exclusion and nuclear ACGT genome size. This nf-core configuration uses BWA,
MAPQ1 plus its native filters, Mt removal, and total reference length for MACS2.
Its replicate merging, DESeq2, annotation and profile/fingerprint plots are disabled.
This is a **configured workflow comparison**, not a claim of matched policies or
untouched nf-core defaults.

nf-core's `read_length=50` is a genome-size lookup option, not observed read
metadata. Its schema permits only 50/75/100/150/200; specifying the observed 31
fails validation. The explicit `macs_gsize` bypasses that lookup and its k-mer task,
so this unused option does not change or pad the 31-base reads. The upstream
source revision and exact executed commands are retained with the results.

Score both completed runs with identical definitions:

```bash
uv run --frozen python \
  benchmarks/experiments/plant-methods-20261013/score_workflows.py \
  --genesis-run /scratch/workflow-comparison/genesis-results \
  --nfcore-run /scratch/workflow-comparison/nfcore-results \
  --out /data/evidence/workflow-comparison
```

The scorer verifies matching input and reference hashes, retains the different
workflow policies, and writes `metrics.tsv` and `results.json`. Fixed-region
comparisons use Genesis peaks as a common reference, not as biological truth.

After the native one-million-template ATAC comparison finishes, check its baseline
against those production observations:

```bash
uv run --frozen python \
  benchmarks/experiments/plant-methods-20261013/compare_production.py \
  --native /scratch/atac-comparison-1m \
  --workflow-scores /data/evidence/workflow-comparison \
  --genesis-run /scratch/workflow-comparison/genesis-results \
  --output /data/evidence/native-production-comparison.json
```

This verifies recorded read/reference hashes and common metric definitions, then
compares counts, peak coordinates/scores, MAPQ and fragment distributions, and
the full TSS profile. The output records `EQUAL` or `DIFFERENT` for each
observation and repetition. Changed fixed regions make their counts
`NOT_COMPARABLE`. Equal observations do not imply identical BAM bytes, agreement
at every genomic locus or equal runtime. A difference needs investigation before
treating the native baseline as equivalent to the production workflow.

## Independent DAP motif support

[motifs.py](motifs.py) evaluates FUS3 and ABI5 against the PBM-derived JASPAR
matrices [MA0565.1](https://jaspar.elixir.no/matrix/MA0565.1/) and
[MA0931.1](https://jaspar.elixir.no/matrix/MA0931.1/). Their matrix snapshots, source
URLs, hashes and CC BY 4.0 attribution are in [sources/](sources/). The DAP-derived
FUS3 matrix is deliberately excluded from this independent measurement.

```bash
benchmarks/environment/.pixi/envs/default/bin/python \
  benchmarks/experiments/plant-methods-20261013/check_motifs.py
benchmarks/environment/.pixi/envs/default/bin/python \
  benchmarks/experiments/plant-methods-20261013/motifs.py \
  /scratch/dap-se-comparison-1m --out /scratch/dap-se-motif-support
```

Every eligible peak supplies a 200-base summit window. Ambiguous bases, chromosome
edges and organellar windows are excluded and counted. Each window is paired with
one seeded same-chromosome control, within two GC bases, outside the pooled summit
windows from all compared methods. Unmatched controls are counted explicitly.
The scanner tests both strands. Its threshold has an exact single-position,
single-strand uniform-background tail of at most 0.0001, using a 0.5 pseudocount
and weights rounded to millionths of a bit. This is not a peak-level p-value.

Report peak/control motif-hit counts and fractions for all eligible pairs and
the top 1,000/5,000 eligible summit windows by caller signal. Invalid windows are
excluded before ranking, and their count is retained. An undersized selection is marked
incomplete. A zero control count leaves fold enrichment undefined. Small control
counts make fold ratios unstable; use the counts and fractions when comparing
methods. Motif support is biological plausibility evidence, not in-vivo binding
truth or proof that every extra peak is correct. No independent motif assignment
has been established for the PE DAP groups in this panel.

Keep the motif output directory with the result archive. `motif-metrics.tsv`
contains every selection, `motif-windows.tsv` records individual peak/control
pairs, and `provenance.json` identifies the matrices and source peak files.
The method archive command below does not collect this separate directory.

## Share evidence and manage temporary work

Finish all BAM-dependent measurements and motif evaluation before removing work.
The archive recipe retains tables, peak files, command logs, task hashes and
figures, then verifies every archived file. Reads, BAMs and reference indexes
stay outside Git.

```bash
uv run --frozen python \
  benchmarks/experiments/plant-methods-20261013/archive.py \
  /scratch/dap-se-comparison-1m --out /data/evidence/dap-se-1m
```

To release storage after a **completed** experiment, use a new archive destination
and add `--prune-work`. This explicitly removes that experiment's method work and
indexes after verifying the archive. Original inputs and result tables remain.
It writes a retention record before removal and records any interrupted removal.
The pruned run cannot resume; rerun its manifest in a new result directory.
Active experiments are refused, and failed experiments retain their work for
diagnosis. Without the flag, archiving removes nothing.

For the complete workflow comparison, archive the runs after common scoring:

```bash
uv run --frozen python \
  benchmarks/experiments/plant-methods-20261013/archive_workflows.py \
  --genesis-run /scratch/workflow-comparison/genesis-results \
  --nfcore-run /scratch/workflow-comparison/nfcore-results \
  --scored /data/evidence/workflow-comparison \
  --out /data/evidence/workflow-archive
```

This retains workflow command logs, task traces, peaks and common measurements.
It verifies every archive member and leaves the original work in place.

## Make summary tables and figures

`summarize.py` keeps libraries separate and reports medians, ranges and missing
counts across technical repetitions. It accepts only complete, fresh method runs.
Use a new output directory:

```bash
benchmarks/environment/.pixi/envs/default/bin/python \
  benchmarks/experiments/plant-methods-20261013/summarize.py \
  --run dap-se-1m=/scratch/dap-se-comparison-1m \
  --motifs /scratch/dap-se-motif-support \
  --workflows /data/evidence/workflow-comparison \
  --out /data/evidence/panel-summary
```

Add a `--run NAME=DIRECTORY` for each cohort/depth. Omit `--motifs` or `--workflows`
when that evaluation was not run. The output contains quality and cost TSVs,
workflow/motif figures in SVG, PNG and PDF, and the hashes of every source table.
Index costs remain separate. Missing evaluations must still be listed in the
accompanying report; their omission from a plot is not a successful result.

For a completed ATAC run, inspect TSS shape and fragment lengths alongside the
scalar metrics:

```bash
benchmarks/environment/.pixi/envs/default/bin/python \
  benchmarks/experiments/plant-methods-20261013/plot_profiles.py \
  /scratch/atac-comparison-1m --method baseline --method bwa --method bowtie2 \
  --out /data/evidence/atac-mapping-profiles
```

Select other methods with repeated `--method` arguments. The figure shows one
technical repetition, with separate panels for each library. TSS curves use a
25-base moving mean for display; the reported enrichment value still uses the
unsmoothed center bin. Fragment plots show lengths below 1,000 bp while retaining
all fragments in the denominator. Raw counts and omitted profiles are exported.

## Interpret the comparison

Use the metric definitions in the [benchmark guide](../../README.md#read-the-measurements).
For ATAC, consider replicate peak overlap, held-out replicate FRiP, TSS signal,
retention, width and genome coverage together. A higher FRiP from broader peaks
does not by itself support a replacement caller. For DAP, inspect independent
motif support alongside enrichment, peak coverage and assigned-control behavior.

Check biological evidence before efficiency. Command timings exclude the common
evaluation code and reference indexing; `processing.tsv` includes selection and
hashing. Index construction is a separate cost. Timing repetitions are technical
measurements on the same input, not additional biological replicates. Host cache
and concurrent activity must be disclosed. An unrun comparison provides no
evidence for changing the pipeline.
