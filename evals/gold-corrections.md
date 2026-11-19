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

## 2026-11-19

Found by the model review (`genesis sheets build --adjudicate`) of the Sorghum RNA-seq
build; every run was checked in its ENA BioSample record.

| Gold | Change | Runs | Evidence | Trap |
| --- | --- | ---: | --- | --- |
| Sorghum RNA-seq | dropped: nutrient deficit | 6 | `growth_protocol` "Phosphorous deficit" / "Nitrogen deficit" while `treatment` says "No treatment" (PRJNA746310) | `rna-growth-protocol-names-a-deficit` |
| Sorghum RNA-seq | dropped: aphid infestation | 4 | `dev_stage` "1-4 days after aphid infestation" (PRJNA1009505) | `rna-stage-after-infestation` |
| Sorghum RNA-seq | dropped: mutant | 5 | `genotype` "MT" beside "WT" samples (PRJNA1419276) | `rna-bare-mt-genotype` |
| Sorghum RNA-seq | dropped: hybrid | 2 | cultivar BTx623_Y49, titles "hybrid BTx623*Y49" (PRJNA302593) | exceptions.yaml |
| Sorghum RNA-seq | dropped: near-isogenic line | 1 | cultivar Tx623*SV (PRJNA325381) | `rna-cross-with-asterisk` |
| Sorghum RNA-seq | dropped: plasma-activated water | 1 | sample PAW100 (PRJNA1173527) | exceptions.yaml |

Later the same day, rules that read the BioSample `growth_protocol` and `dev_stage` fields
(added after the Sorghum findings) dropped 16 A. thaliana RNA-seq runs that gold kept. Each
treatment is stated in the run's own BioSample record:

| Gold | Change | Runs | Evidence |
| --- | --- | ---: | --- |
| A. thaliana RNA-seq | dropped: heat stress | 3 | "WT plants treated with second heat stress" (PRJNA849331) |
| A. thaliana RNA-seq | dropped: infection | 4 | `dev_stage` "12 days after infection" (PRJNA407426) |
| A. thaliana RNA-seq | dropped: bacterial inoculation | 3 | `growth_protocol` "inoculated with 2ul of 10e6 diluted bacterial" (PRJNA718879); the PBS mock stays |
| A. thaliana RNA-seq | dropped: light treatment | 3 | "24 h after red light treatment" (PRJNA489162) |
| A. thaliana RNA-seq | dropped: cell culture | 3 | "Col-0 cell culture grown in Gamborg's B5 medium" (PRJNA412215) |
