# Evaluation

`gold.yaml` lists the curated sample sheets used as gold, with checksums. `traps.yaml`
lists runs that earlier curation rounds got wrong or nearly wrong; every trap must pass.

```bash
genesis sheets gold-fetch --to "$TMPDIR/gold"
genesis sheets score --workflow rna-seq --species Sorghum_bicolor --gold-dir "$TMPDIR/gold" \
    --candidate-sheet sheet.tsv --candidate-qc sheet.runs-qc.tsv --history evals/HISTORY.md
```

The report gives run-level precision and recall (`precision_known` ignores runs gold never
saw), per-field accuracy, how exclusion reasons map between gold and candidate, and trap
results. Targets: precision and recall at least 0.95, every field at least 0.98, all traps
pass. A miss is fixed in the rules, recorded as a trap, or corrected in gold with a note.

## Building a candidate

```bash
genesis sheets build --workflow rna-seq --species Sorghum_bicolor --out "$TMPDIR/sb" \
    --references REFS --star STAR --featurecounts FEATURECOUNTS --gffread GFFREAD
genesis sheets build --workflow rna-seq --species Sorghum_bicolor --out "$TMPDIR/sb-meta" \
    --no-measure --strandedness-from "$TMPDIR/gold/Sorghum_bicolor/rna-seq-curated.runs-qc.tsv"
```

`REFS` holds the species' `reference_fasta`, `reference_annotation` and the STAR index
genesis-preprocessing builds (`<fasta>.<annotation>.star`). The second form reuses earlier
strandedness calls and leaves the other runs out as "strandedness not measured", so it scores
the metadata rules quickly; history rows say which form they used.

### atac-seq

```bash
genesis sheets build --workflow atac-seq --species Sorghum_bicolor --out "$TMPDIR/sb-atac" \
    --extra-runs bulk-atac-tab.tsv --references REFS --chromap CHROMAP
genesis sheets build --workflow atac-seq --species Sorghum_bicolor --out "$TMPDIR/sb-atac-meta" \
    --extra-runs bulk-atac-tab.tsv --no-measure \
    --measure-from "$TMPDIR/gold/Sorghum_bicolor/atac-seq-curated.runs-qc.tsv"
```

`--extra-runs` is the "Bulk ATAC Fable Curated" tab of the Genesis metadata sheet exported as
TSV; it adds INSDC runs with other library strategies and the GSA (CRR) runs, whose files are
found on the GSA download servers. `REFS` holds the species' `reference_fasta` and
`reference_genes`; the chromap index is built into the cache unless `--chromap-index` is given.
Each run's first 200,000 read pairs are mapped (8 runs at a time); a run needs a mapped
fraction of at least 0.05, an organelle fraction of at most 0.95 and TSS enrichment of at
least 1.5. The second form reuses the gold metrics and leaves other runs out as "not measured".
