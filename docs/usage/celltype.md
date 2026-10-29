# Plant cell-type annotation with Alex Borowsky's workflow

`genesis celltype` connects [Alex Borowsky's plant-celltype-annotation
workflow](https://github.com/alexborowsky/plant-celltype-annotation) to Genesis
cell records. Alex's scripts gather RNA expression evidence; the workflow proposes
labels for a person to review. Genesis records the upstream revision, imports the
ordered annotation table and exports reviewed labels while retaining original
labels and QC selections.

The adapter targets the v0.2.1 table contract inspected at revision
`7c99b333b60e235b57e2a45efba42c032544037b`. The upstream checkout stays separate:
Genesis does not vendor its scripts or reference tables, install its dependencies,
run a model, or download data. Synthetic adapter tests establish software behavior;
they do not establish biological accuracy or superiority to label transfer.

## Prepare a reproducible handoff

Use a clean checkout of the upstream repository and a clustered **RNA expression**
AnnData input. ATAC gene activity is not an RNA input. For paired multiome data,
use the RNA component and an explicit mapping to Genesis library/barcode identities.
For separate RNA and ATAC experiments, a shared barcode or cell-type name does not
establish a matched cell. Cross-assay label transfer is outside this adapter.

```bash
git clone https://github.com/alexborowsky/plant-celltype-annotation external/plant-celltype-annotation
git -C external/plant-celltype-annotation checkout 7c99b333b60e235b57e2a45efba42c032544037b
genesis celltype prepare --skill-repo external/plant-celltype-annotation \
  --h5ad data/clustered-rna.h5ad --context context.json --output handoff.json
```

`context.json` identifies the dataset; for example:

```json
{
  "dataset_id": "study-library-1",
  "species": "Arabidopsis thaliana",
  "tissue": "leaf",
  "developmental_stage": "seedling",
  "source": "dataset accession or publication DOI",
  "assay": "multiome-RNA"
}
```

Assay choices are `scRNA-seq`, `snRNA-seq`, and `multiome-RNA`. The handoff hashes
the input bytes without loading AnnData. It is an identity record, not a check of
expression normalization, clustering, marker specificity or data quality.

Follow the pinned checkout's `SKILL.md`: evaluate published labels, establish
expected tissue cell types, inspect lineage-appropriate markers and homology,
collect cluster evidence, and distinguish cell states, doublets and stress from
cell identity. Its Scanpy environment is separate from the Genesis environment.
Preserve the original h5ad and write upstream outputs to new paths. Review proposed
annotations before applying them to a biological dataset.

## Import the evidence and labels

Supply the upstream tab-separated `CellTypeAnnotations.txt` with exactly named
columns `Resolution`, `Cluster`, `CellType`, and `Evidence`. Evidence must be
nonempty, including for `Unknown` exclusions. Legacy whitespace tables are not
accepted. Rows are applied in file order, and the first matching row claims a cell,
even when its label is `Unknown`. Comma-separated cluster selectors are supported.

Export the clustering columns from the same RNA h5ad to an observations TSV, adding
**explicit** `library_id` and `barcode` columns matching your existing Genesis
cell-contract JSONL. Keep cluster values as strings, including leading zeros.
For example:

```text
library_id	barcode	leiden_res0.5
a	AAAC-1	0
a	AAAG-1	1
```

`Resolution` may name an exact column or a numeric Leiden/Seurat resolution.
Ambiguous numeric resolutions require an explicit column name. No barcode prefix
stripping, suffix guessing, cluster recomputation, or color-based label
canonicalization occurs. Canonicalize the input table explicitly if needed.
Every observation must match one existing cell with `rna_present: true`.
Duplicate or unknown library/barcode pairs and annotation rows matching no cells
are errors. Cells absent from observations remain unmatched. Inputs are bounded
to 32 MiB per table/cell file; split larger imports by library. Compressed cell
inputs are rejected; use plain JSONL here.

```bash
genesis celltype import --handoff handoff.json \
  --annotations CellTypeAnnotations.txt --observations observations.tsv \
  --cells cells.jsonl --output annotation-proposals
```

The bundle retains source tables and original cell records, per-rule evidence,
matched/claimed/shadowed counts, source hashes and the upstream handoff. Inspect
`proposal.json` for shadowed rules and unmatched cells. The original expression
file must still match its handoff checksum. This does not prove the observations
were correctly exported: the explicit mapping remains part of human review.

## Review and export

Copy `annotation-proposals/review.tsv` outside the immutable proposal bundle.
Review each rule's evidence and fill in `decision` (`APPROVED`, `REJECTED` or
`PENDING`), `reviewer`, and `reason`. Keep `proposal_version` and `rule_id` intact.
A rule decision covers the cells claimed by that rule. There is no approve-all
command. Named reviewers and reasons are required for both approval and rejection;
missing decisions remain pending. The local decision file is an audit record,
not authentication of the named reviewer.

```bash
cp annotation-proposals/review.tsv decisions.tsv
# Edit decisions.tsv after reviewing proposal.json and the RNA evidence.
genesis celltype export --input annotation-proposals --decisions decisions.tsv \
  --output reviewed-annotations
```

`reviewed-annotations/cells.jsonl` uses the existing Genesis cell contract and can
be retained as the reviewed cell-label handoff. The former
`genesis scatac pseudobulk` command has been removed; processing belongs to
`genesis-preprocessing`. Explicitly map this JSONL contract into that workflow’s
cell metadata input before processing. This adapter does not launch the pipeline. Approved non-Unknown
labels update only the harmonized label, annotation source/status and ontology ID
(the old ontology is cleared, not guessed). Original author labels and QC fields
are retained. Approval never turns a previously excluded cell on: the original
ATAC/RNA inclusion flags still govern selection. Pending, rejected, unmatched and
Unknown cells have both inclusion flags disabled; their original labels are retained
for audit. Review cluster composition and QC separately before changing inclusion.

The exported bundle records proposals and review decisions. Modified proposal
files, stale review versions, duplicate decisions and existing output directories
are rejected. Use a new output path for each revision. The complete manifest hashes
the exported cells and review record; keep the whole bundle with downstream results.
