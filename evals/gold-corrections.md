# Gold corrections

Changes to the gold sheets after they were first published. Each one was found by
`genesis sheets score` and checked against ENA before the gold was changed. The bucket README
has the same list, and each changed run's `reason_left_out` in the `runs-qc` table gives the
reason. Every correction that reflects a real pitfall also has a trap in `traps.yaml`.

## 2026-11-18

| Gold | Change | Runs | Evidence | Trap |
| --- | --- | ---: | --- | --- |
| A. thaliana RNA-seq | dropped: duplicate submissions | 193 | The original run is also in the sheet. ENA lists the same read and base counts, and the first reads match. Most are PRJNA377558, a resubmission of PRJNA376779. | `rna-duplicate-keeps-earliest` |
| A. thaliana RNA-seq | dropped: PAT-seq | 51 | The experiment title says PAT-seq (poly(A) tails), while the library strategy says RNA-Seq (PRJNA559744, PRJNA866703, PRJNA552176, PRJNA1064557). | `rna-pat-seq-labelled-rna-seq` |
| A. thaliana RNA-seq | dropped: NanoPARE | 2 | RNA 5' end libraries (PRJNA704712). | — |
| A. thaliana RNA-seq | dropped: transgene | 23 | The lines express a rust effector or a cotton gene (PRJNA1072118, PRJNA765119). | `rna-line-expressing-foreign-gene` |
| Sorghum bulk ATAC | dropped: CRR145290 | 1 | FRiP 0.066 in a full pipeline run. The 200k-pair check passed it. | `atac-low-frip-gsa` |
| A. thaliana bulk ATAC | added: CRR1760445 | 1 | It was left out only because its download had failed. On 200k pairs: 51% mapped, TSS enrichment 3.26. | — |
| A. thaliana TSS mapping | added: DRR095710 | 1 | Same title as DRR072208, but a different sample, instrument and depth (231M vs 125M reads). | `tss-same-title-not-duplicate` |

Left as they were after review:

- **Two duplicates whose original failed to download** (SRR5304431, SRR29221330). The copy
  stands in for the original.
- **Ambiguous genotype or library** (mutant-titled studies, pollen complementation lines,
  sorted nuclei): checked against BioSample and GEO and settled in the second batch below.
- **Builder misses, where the gold is right** (PRJNA990533 controls read as treated;
  SRR6262794 flagged by a sample title that lists the mutants): fixed in the builder, with
  traps `rna-control-and-treated-runs-share-a-sample` and `rna-wild-type-field-beats-study-title`.
- **A. thaliana DAP-seq PRJNA1177481:** the gold and the builder agree, so no change.

## 2026-11-18, second batch

Found after the builder began reading BioSample `isolate` fields and classifying each run on
its own titles. Per-run evidence is in the runs-qc table's `reason_left_out`.

| Gold | Change | Runs | Evidence | Trap |
| --- | --- | ---: | --- | --- |
| A. thaliana RNA-seq | dropped: mutant or transgenic line | 64 | BioSample `isolate` names it ("T-DNA mutant", "CRISPR mutant", "transgenic plants", "OE line"), while the genotype field gives only the background | `rna-isolate-names-the-mutant` |
| A. thaliana RNA-seq | dropped: mutants | 12 | BioSample `isolate`: pgdh3-1, pgdh3-2 (PRJNA1099156), serk1-1, serk1-L326F (PRJNA430092). The studies' wild-type runs stay | `rna-wild-type-field-beats-study-title` |
| A. thaliana RNA-seq | dropped: sorted nuclei | 8 | BioSample cell type: stem cell nuclei, sorted by FANS (GSE223914) | — |

Kept after checking: the wild-type runs of PRJNA1216402 (genotype: Wt) and the Col-0 pollen
of PRJNA989636 (genotype: WT). SRR27606461 duplicates SRR27557211, but the two
submissions disagree on its genotype, so it stays as it was.
