# Compare preprocessing methods

Use `genesis benchmark` to compare tools on the same reads and reference. It
records the settings, input hashes, commands, failures, measurements and resource
use for each method. Comparisons run separately from normal processing and do
not change pipeline defaults.

Start with a small synthetic comparison to check your installation. For a real
study, choose the evaluation data and scientific questions before looking at
results. More mapped reads, higher FRiP or more peaks alone does not establish
better biological accuracy.

See the [expanded plant comparison report](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/expanded-plant-comparison.md)
for measured results, figures, failures and the current limits of the evidence.
The [public plant recipe](experiments/plant-methods-20261013/README.md) reproduces
the data selection and method matrix.

The [reference comparison recipe](experiments/reference-comparison-20261014/README.md)
covers four species, versioned assemblies, annotation sensitivity and reciprocal
coordinate conversion. Use `genesis benchmark reference inspect` to scan a FASTA
and `genesis benchmark reference prepare` to validate and compile an explicit
reference bundle. Assembly names alone do not establish identical reference files.

The [optimized MACS3 comparison](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/macs3-optimization.md)
compares released 3.0.5 with a pinned upstream source build on identical BAMs
at 1, 2 and 8 CPUs. It separates numerical agreement, runtime and memory use.

## Run a small comparison

Install Genesis first using the [project instructions](../README.md#install-and-check-your-environment).
On Linux x86_64, install the separate, locked comparison environment:

```bash
pixi install --all --manifest-path benchmarks/environment/pixi.toml
```

This environment contains the candidate tools and Matplotlib. It does not change
the pipeline environments. MACS2 uses a separate Python environment because its
requirements differ from MACS3. The locked MACS2 source release is built locally;
your host needs a C compiler. No Docker images are needed for this comparison.

```bash
genesis benchmark fixture --adapter methods --assay bulk-ATAC --out /scratch/atac-benchmark-inputs
genesis benchmark plan /scratch/atac-benchmark-inputs/experiment.json
genesis benchmark run /scratch/atac-benchmark-inputs/experiment.json --out /scratch/atac-benchmark-results
genesis benchmark report /scratch/atac-benchmark-results \
  --plot-python benchmarks/environment/.pixi/envs/default/bin/python
```

Run these examples from the checkout and use new output directories. Open
`report.html` in the result directory; no web server is needed. The `figures/`
directory contains SVG, PDF and PNG plots, plus a checksum manifest. To test DAP,
replace `--assay bulk-ATAC` with `--assay DAP-seq` and choose new directories.

The DAP fixture includes SE and PE treatments and assigned controls. The ATAC
fixture has one PE library. Both use a known synthetic genome and enriched regions.
They test tool integration and known-answer measurements, not plant biological
quality. The synthetic reads contain no adapters; trimming results from this
fixture cannot establish which trimmer is better on real libraries.

## What is compared

The generated version 2 experiment changes one component at a time:

| Component | Baseline | Alternatives | Settings held fixed |
| --- | --- | --- | --- |
| Alignment | BWA-MEM2 | BWA, Bowtie2 | Raw reads, reference, downstream filtering and caller |
| Adapter removal, ATAC only | Full reads | Cutadapt, fastp | Declared adapters; unrelated quality/length filters disabled |
| Duplicate marking, ATAC only | samtools | Picard | Same input alignments and subsequent duplicate exclusion |
| Peak calling | MACS3 | MACS2; Genrich for ATAC | Selected alignments and nominal q-value; caller models remain different |
| Interval counting | Genesis/Python span counts | bedtools coverage | Identical BAM and fixed intervals; read ends counted separately |

The fixture and [public plant panel](experiments/plant-methods-20261013/README.md)
use BWA-MEM2 2.3. Every aligner executable matches the pinned production image
byte for byte. Its version banner incorrectly reports 2.2.1; the comparison
records package metadata and executable hashes as well as that banner. See the
[identity check](experiments/plant-methods-20261013/sources/production-aligner-identity.json)
and [upstream version-string report](https://github.com/bwa-mem2/bwa-mem2/issues/283).
The panel can also compare the older 2.2.1 package with `--include-legacy`.

DAP retains primary mapped alignments including MAPQ 0, uses each treatment's
assigned control and MACS `keep_dup=1`. ATAC applies the production fragment
selector: two proper primary mates, nuclear contigs, both MAPQs at least 30,
no unknown MAPQ 255, and the declared duplicate policy. MACS uses BAMPE and
`keep_dup=all` after selection. These fixture settings are explicit examples;
real comparisons must document their own choices.

Bowtie2 uses `--very-sensitive-local`, with `-X 2000` for paired reads; ATAC also
uses `--dovetail --no-mixed --no-discordant`. Local alignment permits soft clipping;
dovetail allows the mates to extend past each other's starts. See the
[Bowtie2 manual](https://bowtie-bio.sourceforge.net/bowtie2/manual.shtml#mates-can-overlap-contain-or-dovetail-each-other).

For ATAC, MACS uses the paired fragment pileup in `BAMPE` mode, with nuclear ACGT
count as its declared genome size. This differs from Genrich's `-j` cut-site
model. See [MACS format definitions](https://macs3-project.github.io/MACS/docs/callpeak.html).
The Genrich comparison keeps 100-bp cut-site windows, minimum AUC 200 and maximum
gap 100 bp. It retains the BAM header and passes no `-e`, `-E` or `-L`, so its
background genome length includes all header contigs and ambiguous bases even
though organellar alignments were removed before calling. It does not test
Genrich's multimapping or joint-replicate analysis. These are caller-model
differences, not a matched effective-genome-size comparison. See the
[Genrich definitions](https://github.com/jsh58/Genrich#genome-length-calculation).

Equal q-values do not make these callers statistically equivalent. Common TSS
and FRiP measurements use the same evaluation code for every caller; they do not
reuse caller-specific signal profiles. Every executed argument is saved in the
command records.

The `methods` adapter compares scientific components. It does not execute the
complete Nextflow workflows or include FastQC, SPP, MultiQC and track generation
in its timings. Use separate end-to-end acceptance to test those integrations.
The [legacy benchmark guide](legacy.md) documents post-alignment policy matrices
and pinned historical Genesis/nf-core adapters. Its `genesis-atac` adapter is the
frozen prototype, not the current `genesis pipeline-atac` workflow.

## Read the measurements

`metrics.tsv` includes a definition, version, unit, denominator and missing-value
reason for every measurement. Blank/undefined is different from zero.

| Measurement | Definition | Interpretation |
| --- | --- | --- |
| Read retention | Retained primary mapped ends / raw ends | Yield under the declared selection policy |
| Fragment retention | Complete same-contig spans / raw templates | Paired fragments and read ends have different denominators |
| Own FRiP | Retained fragments overlapping any called peak / retained fragments | Depends on both read selection and the caller's peak set |
| Fixed FRiP | The same ratio using baseline peaks | Measures change on fixed regions; baseline peaks are not truth |
| Peak count, union bases, median width | Interval count, nonduplicated genomic coverage and median length | Report together; broad or numerous peaks are not automatically better |
| Peak Jaccard | Base-pair intersection / union with baseline | Agreement with baseline, not independent accuracy |
| Fixed-region count correlation | Pearson/Spearman of fragment counts on identical regions | Undefined for too few regions or zero variance |
| Biological replicate overlap | Peak base-pair intersection / union across an explicitly declared replicate pair | Reproducibility, not binding-site truth; replicates remain separate |
| Held-out replicate FRiP | Fragments in one replicate overlapping peaks called in the other / retained fragments | Avoids calling and evaluating on the same library; still depends on peak width |
| TSS enrichment | Common strand-aware ±2 kb cut profile, center/background ratio | Only with explicit annotation; inspect its definition and eligible TSS count |
| Synthetic alignment recovery | Exact original contig/start/end/strand among mapped or raw simulated ends | Includes ambiguous origins; trimming/soft clipping can alter endpoints without a wrong locus |
| Synthetic peak precision/recall | Called intervals overlapping enriched regions / calls; enriched regions recovered / truth regions | Also report base-level precision/recall to expose width changes |
| Independent count mismatches | Fixed intervals with different Python and bedtools read-end counts | Any mismatch fails the comparison; this checks arithmetic, not biological quality |

PE fragment counts use outer aligned spans. Read-end counts use unsplit alignment
spans and count mates independently. One fragment may contribute to several
individual overlapping regions; FRiP uses the region union and counts it once.
Synthetic truth is available only when the fixture provides known origins.
No universal plant pass/fail threshold or combined quality score is assigned.

`resources.tsv` records command wall time, GNU-time CPU time and available memory
measurements. Native memory is the largest sampled sum of process-group RSS;
shared pages can be counted twice and short peaks can be missed. GNU-time maximum
RSS is a separate process statistic. Container memory, when available, has a
different scope and can include page cache. None is whole-host peak RAM.
The driver's RSS is also separate: it is the Python process's lifetime high-water
value, excluding child tools. Earlier Linux result files named this
`peak_host_rss_kib`; that legacy field still describes the driver, not the host.
Index construction is reported separately from per-library work. CPU and wall
time from a Docker launcher must not be interpreted as container CPU usage.
`processing.tsv` also measures each complete method case, including Python
selection and task hashing, while excluding indexes and common evaluation.
Temporary name-sorted/fixmate BAMs are removed after successful duplicate marking;
aligned, marked and selected BAMs remain available for inspection and resume.

Use three or more technical repetitions for timing comparisons, retaining each
observation and the median/range. Randomize order with a fixed seed, report cache
conditions, and keep biological replicates separate from timing repetitions.
The runner does not flush the OS cache. A resumed task is not a fresh timing.

## Prepare public or local inputs

For files without a known SHA256, supply a publisher size/checksum and a saved
source record in an acquisition manifest. The [plant comparison manifest](experiments/plant-methods-20261013/acquisition.json)
is a complete example. Inspect its size and budgets before downloading:

```bash
genesis benchmark acquire sources.json --out /data/benchmark-sources --connections 4
```

Acquisition accepts public HTTPS and verifies the complete publisher MD5 or
SHA256 before recording a measured SHA256 in `assets.lock.json`. An MD5 is a
transport-integrity check, not authentication. Partial files remain untrusted.
Rerunning the same command resumes exact HTTP byte ranges and verifies the whole
file before use. Changed manifests need a new destination; failed checks never
promote partial data. Cumulative transferred bytes and elapsed time survive retries.

Use seeded nested samples to compare the same templates at multiple depths:

```bash
genesis benchmark sample --read1 /data/R1.fastq.gz --read2 /data/R2.fastq.gz \
  --depth 1000000 --depth 5000000 --seed 60141 --out /scratch/depth-samples
```

For SE reads, omit `--read2`. The command scans and validates the full library,
counts templates, selects the lowest seeded hashes and preserves original record
order and mate pairing. It needs two passes and memory proportional to the largest
sample. The smaller sample is contained in the larger one. Full-file download is
still required; random subsampling does not reduce transfer size. `sampling.json`
records source/output hashes, actual counts and selection rules. If a requested
depth exceeds the library, the actual count is reported.

A dataset manifest names the exact FASTA, decompressed FASTA hash, chromosome
sizes, species/assembly, organellar contigs, optional TSS/GTF, libraries and
controls. Assets stay outside Git. Copy the generated fixture's JSON structure
and replace its synthetic identities and paths with measured source records.
The [public panel preparer](experiments/plant-methods-20261013/prepare.py) shows
how the recorded study inputs are compiled. An accession alone does not establish
biological replication, tissue, TF-source species or the assayed DNA organism.

## Resume and reproduce

```bash
genesis benchmark run /scratch/atac-benchmark-inputs/experiment.json \
  --out /scratch/atac-benchmark-results --resume
```

Resume requires the same experiment identity and verifies cached input/output
hashes. A changed tool, reference, policy or source recipe needs a new experiment
directory. Retain `experiment.json`, `results.json`, the TSVs, command logs and
figure manifest when sharing results. Keep source files separately if another
server must rerun the analysis.

Each experiment declares CPU, memory, time, output-storage, download and matrix
limits. Those are per experiment: when running a panel, also track the total
budget across preparation, indexes and all experiments. Do not launch the whole
panel concurrently under a single experiment's resource allowance.

Adopt a different package only after correctness checks pass and independent
biological endpoints support equal or better quality on representative data.
Then compare efficiency, preserve the old behavior as a reproducible baseline,
update the workflow and rerun its end-to-end acceptance. If evidence is mixed or
incomplete, retain the current default and report what remains unmeasured.
