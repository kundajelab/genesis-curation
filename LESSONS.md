# Curation lessons

Mistakes that sample-sheet curation has made or nearly made, and how each is now caught. Add a
row whenever a scoring round finds a new kind of miss, together with the trap in
[`evals/traps.yaml`](evals/traps.yaml) that would have caught it. The loop that finds them is
in [evals/README.md](evals/README.md).

| Symptom | Cause | Fix | Trap |
| --- | --- | --- | --- |
| QuantSeq and m6A-IP runs kept as RNA-seq | `\b` treats `_` as a word character, so `\bQuantSeq\b` misses `sample_QuantSeq` | Match words between non-letters, not with `\b` | `rna-other-assays-behind-underscores` |
| The DAP-seq workflow rejects a library | Reads were trimmed before upload and vary in length | Check read lengths before keeping a run; leave trimmed uploads out | `dap-trimmed-uploads` |
| bwa refuses to pair reads | Read 2 was uploaded as its own SRA run with different read names | Run the study single-end on read 1 | `dap-mates-in-two-runs` |
| A "paired" run has one file, or the reverse | The ENA layout label doesn't match the files ENA serves | Trust the files, not the label | `rna-paired-served-as-one-file` |
| Few cells pass barcode matching in some runs | One study mixes MiSeq/NovaSeq and other instruments, whose barcode reads have opposite orientation | Probe the barcode orientation for each run | `scatac-interleaved-instruments` |
| Seed RNA libraries appear as DAP-seq | "DAP" in a title means days after pollination | Require DAP-seq context, not the bare word | `dap-days-after-pollination`, `dap-days-after-pollination-lowercase` |
| TSS libraries kept as RNA-seq | Smar2C2 runs are submitted with library strategy RNA-Seq | Flag 5'-end methods by title, whatever the strategy | `rna-smar2c2-labelled-rna-seq` |
| The same reads are counted twice | Studies resubmitted under a new accession (e.g. PRJNA377558 = PRJNA376779) | Compare read counts and first reads; keep the earliest | `rna-duplicate-submissions`, `rna-duplicate-keeps-earliest` |
| A same-title library is dropped as a duplicate | A matching title is not proof of the same reads | Call a duplicate only when the reads match | `tss-same-title-not-duplicate` |
| Bulk ATAC passes the sample check but fails the full run | 200k-pair TSS enrichment can't predict a FRiP of 0.07 | Record full-run QC in `sheets/data/exceptions.yaml` | `atac-low-frip-gsa` |
| PAT-seq kept as RNA-seq | Submitted as RNA-Seq with oligo-dT selection; only the title says PAT-seq | Flag PAT-seq in titles as another assay | `rna-pat-seq-labelled-rna-seq` |
| Transgenic lines kept as wild type | The genotype field says Col-0; only the title names the transgene | Flag "expressing" and "over-expressing" titles | `rna-line-expressing-foreign-gene` |
| The gold disagrees with a correct build | The gold sheets had errors of their own | Check against ENA, then correct the gold with the maintainers' OK ([gold corrections](evals/gold-corrections.md)) | — |
