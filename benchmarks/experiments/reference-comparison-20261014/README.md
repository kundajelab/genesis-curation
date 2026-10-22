# Plant reference and method comparisons

Compare assembly, annotation and tool choices using identical sampled reads and
explicit source hashes. These experiments write separate outputs and do not
change normal Genesis processing settings. The
[results report](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/expanded-plant-comparison.md)
lists completed measurements and work still in progress.

## Reference panel

| Species | Comparison | What changes |
| --- | --- | --- |
| Arabidopsis thaliana | TAIR10 and Col-CEN v1.2 | Assembly; Araport11 annotation is supplied for each |
| Arabidopsis thaliana | TAIR10 and Araport11 annotations on Col-CEN v1.2 | Annotation only; exactly the same BAMs |
| Arabidopsis lyrata | Ensembl Plants 19 v1.0 and Phytozome v2.1 bundle | Contig naming/order and annotation; full scans found identical sequence multisets |
| Sorghum bicolor | Sorbi1, BTx623 v5.0 and RTx430 v2.0 | Assembly, and for RTx430 also cultivar |
| Zea mays | B73 AGPv4 and NAM5 | Assembly and associated annotation; common organellar sequences supplied explicitly |

The multiDAP study names **Phytozome v2.1** for A. lyrata and RTx430. Those are
distribution/annotation bundle versions: the FASTA assemblies are v1 and v2.0,
respectively. BTx623 v5.1 likewise names a bundle containing assembly v5.0.
The panel records these separately. See the study metadata in
[cohorts.json](cohorts.json), the
[Col-CEN author release](https://github.com/schatzlab/Col-CEN/tree/abb9b614d91c8a0bbd05a199c694fb5eafb5fe30/v1.2),
[Phytozome](https://phytozome-next.jgi.doe.gov/) and
[MaizeGDB NAM5 files](https://download.maizegdb.org/Genomes/B73/Zm-B73-REFERENCE-NAM-5.0/).

Sorghum RTx430 is the study's cultivar. Comparing those reads against BTx623 tests
a different cultivar as well as a different assembly. A higher mapping rate
cannot separate those effects.

## Inspect and prepare a reference

The installed CLI scans every FASTA base and reports contig lengths, sequence
hashes, ACGT counts and soft masking. These reference commands work from the
Python package alone, including outside a checkout, without Pixi or Nextflow:

```bash
genesis benchmark reference inspect /data/references/genome.fa.gz
genesis benchmark reference prepare /data/reference-panel.json \
  --id tair10-araport11 --out /scratch/tair10-bundle
```

A reference panel is a version 1 JSON object with a `references` list. Each entry
declares an ID, species, cultivar, assembly/accession, FASTA path/SHA256/source URL,
mitochondrial/plastid contig lists and source evidence. An optional annotation
also needs its release and exact file hash. Use the
[panel builder](build_panel.py) to produce the concrete panel for this experiment.
Relative asset paths resolve against the panel file. Preparation refuses changed
assets, unknown versions and annotation coordinates outside the FASTA.

The prepared directory contains `reference.json`, `chrom.sizes`, and, where an
annotation was supplied, `tss.bed` and `tss-genes.tsv`. The TSS file uses Genesis's
three-column **contig, zero-based position, strand** format. It is not BED3.
Transcript-feature and exon-boundary extraction are explicit modes. The lifted
Col-CEN Araport11 file contains exon records without transcript records, so this
panel derives transcript boundaries from those exons. TAIR10's coding and
noncoding transcript feature types are declared explicitly. Gene-level comparisons
count exclusions and keep annotation releases separate.

## Obtain the inputs

Run from the Genesis checkout with its Python tools installed. Install only the
optional environments needed for the comparison:

```bash
pixi install --all --manifest-path benchmarks/environment/pixi.toml
pixi install --manifest-path benchmarks/reference-tools/pixi.toml
pixi install --manifest-path benchmarks/sra-tools/pixi.toml
```

The [original plant panel](../plant-methods-20261013/README.md) supplies the
Arabidopsis reads and legacy reference files. The new DAP cohort uses seed 60141
to select four TF families per species, with each treatment's assigned sheet
control. These TF assays are not biological replicates. Original TF identity,
TF-source organism and assayed DNA species remain separate in the catalog.
The accession snapshots record the selection before measuring outputs.

Acquire either the ENA FASTQs in [acquisition.json](acquisition.json) or the
full-quality SRA archives in [sra-acquisition.json](sra-acquisition.json). These are
explicit alternatives, not automatic fallback providers. The SRA manifest also
includes the second maize replicate. It transfers 65.73 GB before retries.

```bash
genesis benchmark acquire \
  benchmarks/experiments/reference-comparison-20261014/sra-acquisition.json \
  --out /data/sra-sources --connections 4

uv run --frozen python \
  benchmarks/experiments/reference-comparison-20261014/fetch_references.py \
  benchmarks/experiments/reference-comparison-20261014/reference-downloads.json \
  --out /data/reference-work/reference-downloads
```

Check each script's `--help` before choosing paths. The reference manifest records
publisher MD5/Git-blob checksums where available; an observed SHA256 alone is not
publisher authentication. The public Phytozome browser recipe reconstructs every
declared contig from its sequence chunks and records a derived-file receipt. It
does not claim to download the publisher's original FASTA archive or bypass
restricted downloads.

SRA conversion uses `sample_sra.py`: `vdb-validate`, then two complete paired
FASTQ decoding passes with expected template/base counts. It retains full base
qualities, rejects mismatched mates and records the derivation. Use counts from
the stored ENA metadata, not estimates. NCBI distinguishes this normalized format
from [SRA Lite](https://www.ncbi.nlm.nih.gov/sra/docs/sra-data-formats/), which changes
quality scores. This experiment does not use SRA Lite. Normalized FASTQ headers
and compressed bytes differ from the ENA files; the derived samples have their
own hashes. A smaller library keeps its actual count at both requested depth caps.

## Run and evaluate

Use [prepare.py](prepare.py) with a completed `--base-plan` to reuse exactly the
same reads against another reference. For the new cohorts, use `--preselected`
with the catalog and `--sampling` directory containing verified receipts.
Choose a new output directory for every experiment:

```bash
uv run --frozen python \
  benchmarks/experiments/reference-comparison-20261014/prepare.py \
  --base-plan /data/completed-comparison/experiment.json \
  --reference-panel /data/reference-panel.json --reference col-cen-1.2-araport11 \
  --environment /data/genesis/benchmarks/environment \
  --method baseline --repetitions 1 --cpus 14 --out /scratch/colcen-inputs

genesis benchmark run /scratch/colcen-inputs/experiment.json \
  --out /scratch/colcen-results
```

Keep the runner checkout unchanged during execution. The runner checks its source
identity and does not certify a run if that source changes. Develop in another
checkout. `compile_panel.py` and `run_queue.py` provide the recorded sequential
Linux panel recipe, with at most 14 processing CPUs and an 80 GiB request per
experiment. The queue checks the aggregate 150 GB temporary-work budget, including
any additional `--scratch` roots, and a persistent seven-day deadline. Supply
every temporary root. Original downloads have a separate budget. These local
checks do not reserve resources from other users or replace a site scheduler.

Choose a filesystem with enough free space for downloaded inputs and temporary
work together. The queue checks free space against each experiment's disk budget
before launch and stops its processes if less than 1 GB remains. A scratch-size
limit alone cannot detect space consumed by other files on the same filesystem.

Assembly mapping accepts explicit 8, 10 or 48 GiB limits through
`align_assemblies.py --memory-gib`. If a mapping exceeds its limit, retain the
failed attempt and use a new output directory for the revised resource request.
Run a 48 GiB mapping separately from an 80 GiB processing job to stay within the
96 GiB total budget; the alignment parameters remain unchanged.

The recorded total budget is 16 CPUs, 96 GiB RAM, 250 GB of additional downloads
and 150 GB temporary work. Preparation uses CPUs 14–15 and processing uses 0–13.
Publisher-verified originals remain available; completed reference indexes can
be archived and explicitly pruned while retaining BAMs for further assessments:

```bash
uv run --frozen python \
  benchmarks/experiments/plant-methods-20261013/archive.py /scratch/colcen-results \
  --out /data/evidence/colcen --prune-indexes
```

Pruning prevents execution resume for that result directory. To rerun, regenerate
the work in a new directory from the pinned inputs.
The panel queue retains BAMs for reference comparisons. Method matrices already
contain their common assessments; after a successful archive verification, their
reproducible work is pruned to keep subsequent experiments within the disk budget.

After every assessment that needs those BAMs has finished, remove the remaining
reproducible work with a second verified archive. The previous archive and its
manifest remain intact; the new retention record links both generations:

```bash
uv run --frozen python \
  benchmarks/experiments/plant-methods-20261013/archive.py /scratch/colcen-results \
  --previous-archive /data/evidence/colcen \
  --out /data/evidence/colcen-final --prune-work
```

Use the original local archive for this step. Public exports have different
hashes after path replacement and cannot authorize removal of the original work.

## Map coordinates between references

Use the exact reference bundles prepared above. The MaizeGDB chains are checked
against every contig length and alignment block. NAM5's chromosome aliases
(`1` to `chr1`, and similarly through chromosome 10) are explicit in the source
manifest; aliases cannot merge distinct contigs.

```bash
uv run --frozen python \
  benchmarks/experiments/reference-comparison-20261014/fetch_references.py \
  benchmarks/experiments/reference-comparison-20261014/chain-downloads.json \
  --out /data/maize-chains

uv run --frozen python \
  benchmarks/experiments/reference-comparison-20261014/bind_chains.py \
  --left-reference /data/agpv4-bundle/reference.json \
  --right-reference /data/nam5-bundle/reference.json \
  --sources benchmarks/experiments/reference-comparison-20261014/maize-chain-sources.json \
  --chains /data/maize-chains --out /scratch/maize-mapping
```

For identical sequence multisets, omit `--sources` and `--chains` to create
whole-contig identity mappings. Repeated sequence identities are excluded and
listed. This gives 695 unique contig matches for the A. lyrata distributions.

For TAIR10/Col-CEN and sorghum, use `align_assemblies.py` with `--panel`, `--left`,
`--right`, `--minimap2` and `--out`. Run it under `taskset -c 14,15` on Linux.
It aligns both directions with `asm5`, CIGAR output and up to 50 secondary
alignments, within two CPUs and eight hours per reference pair. Memory defaults
to 8 GiB; `--memory-gib 10` selects an explicit larger budget. The first Sorbi1
mapping exceeded 8 GiB, so its separately recorded second attempt uses 10 GiB.
The output
`mapping.json` binds both mappings to the exact FASTA hashes.

```bash
uv run --frozen python \
  benchmarks/experiments/reference-comparison-20261014/compare_references.py \
  --left /scratch/agpv4-results --right /scratch/nam5-results \
  --mapping /scratch/maize-mapping/mapping.json \
  --liftover /data/genesis/benchmarks/reference-tools/.pixi/envs/default/bin/liftOver \
  --out /scratch/maize-concordance
```

The comparison requires identical libraries, controls, sampled reads, filtering
parameters, baseline methods and seeds. A missing library is an error, not an
implicit exclusion. Reference-dependent genome sizes remain explicit in each
experiment. `assess_panel.py` can wait for an explicit version 1 JSON `jobs` list
of `reference`, `annotation` or `diagnostic` assessments and run them sequentially.
For example, save this as `/data/assessments.json` after replacing the paths:

```json
{
  "schema_version": 1,
  "jobs": [{
    "id": "maize-1m",
    "kind": "reference",
    "left": "/scratch/agpv4-results",
    "right": "/scratch/nam5-results",
    "mapping": "/scratch/maize-mapping/mapping.json",
    "liftover": "/data/genesis/benchmarks/reference-tools/.pixi/envs/default/bin/liftOver"
  }]
}
```

```bash
taskset -c 14,15 uv run --frozen python \
  benchmarks/experiments/reference-comparison-20261014/assess_panel.py \
  /data/assessments.json --out /scratch/assessments
```

Annotation jobs use `run` and a two-item `bundles` list of reference JSON paths.
Diagnostic jobs use `run`, `environment` (the method environment prefix) and
`auxiliary` (the reference-tools prefix). Each assessment is limited to two CPUs,
8 GiB, 10 GB of output and six hours, within a persistent seven-day deadline.
Set `deadline_utc` in the list to impose an earlier overall deadline. Failed or
blocked assessments remain recorded; uncertain work is not automatically retried.

| Evaluation | Recipe | Interpretation |
| --- | --- | --- |
| Cross-assembly peaks/counts | `compare_references.py` | Bind bidirectional mappings to exact FASTA hashes; reject ambiguous/unmapped/nonreciprocal peaks; report all exclusions |
| Annotation sensitivity | `compare_annotations.py` | Same BAM and FASTA; all transcript starts and common genes with one unique TSS per annotation |
| Caller diagnostics | `caller_diagnostics.py` | Genrich q-value and native p-value operating points stay separate; neither changes production policy |
| Biological replicate IDR | `caller_diagnostics.py` | Exploratory pairwise IDR on permissive ranked MACS calls; pooled/pseudoreplicate rescue ratios are not calculated |
| DAP motif evidence | `../plant-methods-20261013/motifs.py` | Independent PBM matrices for FUS3/ABI5; exact peak/control counts and unmatched windows |
| Tables and figures | `plot_panel.py` | Per-library observations, technical timing ranges, denominators and missing values |

Raw BED coordinates from different assemblies must never enter a direct overlap
calculation. The cross-assembly recipe requires at least 95% aligned bases each
way, one placement each way, and 95% reciprocal span overlap. Its uniqueness is
relative to the supplied assembly mappings, not a guarantee of global mappability.
The separate UCSC liftOver known-answer test covers strand changes, insertions,
deletions, ambiguous placement and partial mappings.

Public evidence exports replace local path prefixes and retain **both** original
and exported hashes. `export_evidence.py` reads and verifies each original archive
member before creating its public derivative. Keep its path-map JSON local.
No FASTQs, BAMs, credentials or private configuration files belong in the public
evidence directory.

To export the report as PDF, use the optional benchmark environment's Python,
which includes Matplotlib:

```bash
benchmarks/environment/.pixi/envs/default/bin/python \
  benchmarks/experiments/reference-comparison-20261014/report_pdf.py \
  validation/reports/expanded-plant-comparison.md \
  --out /data/evidence/expanded-comparison.pdf
```

The adjacent manifest records the report, figure, renderer and PDF hashes. The
separate figure-only PDF preserves vector plots for reuse.
