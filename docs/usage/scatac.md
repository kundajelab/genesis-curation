# Single-cell ATAC fragments and pseudobulks

Genesis can import existing 10x ATAC or ARC fragments, keep biological replicates
separate, and produce cell-group fragments, cut-count tracks and peaks. It does
not process single-cell FASTQs. Run these commands on a standalone Linux server;
track and peak generation uses the existing pinned Docker images.

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

## Process known libraries

```bash
genesis scatac ingest --manifest library.json --output fragments/library-1
genesis scatac ingest --manifest library.json --output fragments/library-1 --resume
genesis scatac pseudobulk --input fragments/library-1 --input fragments/library-2 --annotations cells.jsonl --policy policy.json --output pseudobulk
genesis scatac products --input pseudobulk/TASK_ID --output products/TASK_ID
genesis scatac qc --input products/TASK_ID
genesis scatac register --input products/TASK_ID --registry catalog
```

Each library manifest declares its biological identity, producer/version, exact
FASTA SHA256, chromosome lengths, organellar contigs and source-fragment checksum.
Source offsets must be explicit. The supported 10x adapters require BED0 half-open
intervals and already-applied +4/−5 offsets. Ingestion never shifts coordinates.
Five-column support counts and optional sixth-column strand are preserved.
Duplicate library/barcode/interval rows are rejected for review, not silently removed.

Cells are keyed by **library + barcode**. The existing
[cell contract](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/design/scatac-metadata-schema.yaml) governs
annotation provenance and explicit ATAC inclusion. Unknown QC values remain null;
plant thresholds remain unspecified. Biological replicates are never pooled.
Independent technical libraries require a named, explicit merge declaration.
Different cells with identical coordinates remain independent molecules.

Pseudobulks count each selected fragment once, regardless of read support. They
retain source-cell membership, library composition, exclusions and support totals.
For the recorded `chrombpnet-atac-v1` signal convention, a 10x interval `[s,e)`
contributes cuts at `s` and `e`. This converts source offsets to the audited model
convention; it does not shift the stored fragments. Out-of-reference cuts require
an explicit error or exclusion-and-report policy.

The policy must specify organellar handling, boundary handling, a peak q-value and
an effective genome size per reference. MACS3 runs in BEDPE mode with all selected
molecules retained. Signals are unstranded raw cut counts. FRiP counts unique
selected fragments overlapping the union of the group's peaks by at least one
base. TSS enrichment is not calculated by this command.

SQLite keeps fragment sorting and cell joins on disk. Plan free space for the
input, temporary database, indexed fragments and each selected group's outputs.
`--resume` verifies input identity and output checksums. Changed inputs, annotations,
policy or implementation require a new output directory. Failed staging directories
retain diagnostics; complete outputs are published atomically.

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
scATAC commands: prepared-study execution and reviewed scATAC release export are
not supported for these records.

See the [model handoff](scatac-model-handoff.md) for loader setup and the [validation report](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/scatac-delivery-validation.md)
for tested inputs, public-data findings and validation limits.
