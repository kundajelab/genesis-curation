# Single-cell ATAC inventory and model export

This repository keeps the scATAC study inventory and the model export. Pseudobulk
processing, from published fragment files to peaks, cut-count tracks and QC, is the
`scatac-seq` workflow in
[genesis-preprocessing](https://github.com/kundajelab/genesis-preprocessing/blob/main/docs/usage/scatac-seq.md).

## Discover studies

```bash
genesis scatac inventory import inventory --source 'Plant Data Sets.xlsx' --source-id plant-data-sets --registry catalog
genesis scatac inventory audit inventory
genesis scatac inventory evidence inventory --source evidence.json
genesis scatac inventory export inventory --output inventory-tables
```

The importer retains the workbook and original cell values, including formulas
and cached values. It focuses on Arabidopsis and Sorghum in `scATAC Fable Curated`.
Accessions remain separate from biological identities. Repeated imports do not
create duplicate registry records. Six TSVs describe studies, candidate libraries,
BioSamples, accessions, readiness and metadata issues.

Availability evidence records a source URL, retrieval date, snapshot checksum and
review status. It never overwrites spreadsheet values. Readiness gives equal
weight to fragments, cell annotations, reference compatibility, biological
replicate evidence and metadata completeness. The first four components earn
points only with approved evidence. This is a processing-priority score, not a
biological quality score. A named reviewer is required for approved evidence.

## Earlier pseudobulk bundles

The `genesis scatac ingest`, `pseudobulk` and `products` commands that made
fragment and pseudobulk bundles were removed after preprocessing gained the
`scatac-seq` workflow. To make new bundles of that kind, use a checkout at `ac36afc`
or earlier. Bundles already made remain readable:

```bash
genesis scatac qc --input products/TASK_ID
genesis scatac register --input products/TASK_ID --registry catalog
```

`qc` prints a bundle's recorded QC; `register` records it as an **unreviewed**
artifact. The model export below also reads these bundles.

## Import the scatac-seq workflow's outputs

`import-workflow` turns one library of a `scatac-seq` run into one group bundle per
cell group, which `qc`, `register` and the model export read like the earlier bundles:

```bash
genesis scatac import-workflow --run RUN/output/Arabidopsis_thaliana/SAMPLE_ID \
    --fasta references/TAIR10.fa.gz --reference-id TAIR10 --assembly TAIR10 \
    --species Arabidopsis_thaliana --mitochondrial Mt --plastid Pt \
    --bigwig-python /path/to/env/bin/python --output bundles/SAMPLE_ID
```

Peaks, cells and QC come from the workflow; the cut signal does not. The workflow's
bigWig counts each fragment's first and last base (`start`, `end - 1`), while the model
export's `chrombpnet-atac-v1` contract puts the second cut at `end`, one base to the
right. The import therefore rebuilds `signal.bw` from the published fragments with
those cuts, and records `chrombpnet-atac-v1` and the 10x `+4/-5` source offsets. A cut
that would fall past a contig end is left out and counted. `--bigwig-python` names an
existing Python with pyBigWig (for example the workflow's `genesis_deeptools`
environment), so the import adds no dependency. Fragment counts, cells and peak counts
are checked against the workflow's own tables. The FASTA must be the one the run used;
the organelle contigs are recorded, since the workflow has already dropped their
fragments. `--resume` reuses an unchanged import.

## Export model inputs

```bash
genesis scatac model export --input products/TASK_ID --config model.json --output models/TASK_ID
genesis scatac model validate --input models/TASK_ID
genesis scatac model inspect --input models/TASK_ID --format markdown
```

Adapters target Cherimoya `8ecf7f791ec07c05a701076cc58a0f845bf97609` and ChromBPNet
`ece97c93ccaa2d9ee5bc5687e62f4dbf8d055367`. The config names one target, its audited
SHA, even input/output window sizes, jitter, and explicit train/validation/test
chromosomes. No human chromosome defaults are supplied. The export includes a
FASTA, sizes, raw-count bigWig, summit-centered peak regions, deterministic
nonoverlapping genomic background tiles, cell membership, QC and parent provenance.
Background tiles are not GC matched. Chromosome folds prevent coordinate-window
leakage; sequence-homology leakage remains a separate modeling concern.

Both adapters expose one unstranded channel without a control track. ChromBPNet
requires a recorded bias-model plan; exporting files does not supply or validate
a plant bias model. Do not apply another Tn5 shift to exported tracks.

The export command always writes `model_ready: false`. Test the model loader and
complete scientific review separately before training. Registry registration records
an immutable **unreviewed** artifact; it does not approve a dataset. Use the standalone
scATAC commands; reviewed scATAC release export is not supported for these
records.

See the [model handoff](scatac-model-handoff.md) for loader setup and the [validation report](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/scatac-delivery-validation.md)
for tested inputs, public-data findings and validation limits.
