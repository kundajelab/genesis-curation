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
