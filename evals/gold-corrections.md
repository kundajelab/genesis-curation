# Gold corrections

Changes to the gold sheets after they were first published. Each one was found by
`genesis sheets score` and checked against ENA before the gold was changed. The bucket README
has the same list, and each changed run's `reason_left_out` in the `runs-qc` table gives the
reason. Every correction that reflects a real pitfall also has a trap in `traps.yaml`.

## 2026-11-25, evening: A. thaliana RNA-seq gold v7

The rebuild drops 69 runs from 3 studies (31,281 → 31,212 rows); 25 left-out runs change
only their genotype label. The first reads of kept runs were compared with those of
left-out runs of the same read count (321 groups); 228 kept runs share reads with a left-out
run. About half of those pairs are wild-type controls that a weak title rule left out in
the original study, so they stay kept and no general rule was added. Read by hand, three
studies were wrong:
- PRJNA808745 (54 runs): `fwa` epiallele and zinc-finger fusion lines (`LHP1-ZF`, `YPET-ZF`)
  whose genotype field says Columbia-0; SRR22124051 carries the reads of an `fwa` run of
  PRJNA779072.
- PRJNA918355 (3 runs): `Col-clf` mutants; the reads match `mRNA-seq_C24_clf` of PRJNA264544.
- PRJNA1167210 (12 runs): complemented `wrky22-/-` lines whose genotype field also names
  Col-0. The builder now reads `-/-` as a mutant.
Pairs left as they are because the two submissions disagree: PRJNA478084 (Col_0 vs
`fer4-3`), PRJNA309649 (WT vs `clv3`), PRJNA994122 (LBD40 vs `agl15 agl18`).

## 2026-11-25, later: A. thaliana RNA-seq gold v6

The rebuild drops 24 runs from 6 studies (31,305 → 31,281 rows) and changes nothing else.
These are protoplasts and callus that the cell-culture rule missed because the tissue
field says root, leaf or nothing; the word is only in the sample alias (PRJNA380494, root-tip
protoplasts), sample title (PRJNA881937 `Pooled protoplasts`, PRJNA649267
`At_root_proto_RNA`, PRJDB12627 `Callus_0day_WT`), dev_stage (PRJNA941486 `after
protoplast`) or protocol (PRJEB15204, calli on shoot-induction medium). Found by searching
every kept run's ENA fields for protoplast and callus words and reading each hit; bulk
tissue controls in the same studies (`before protoplast`, `non-dissociated`, `bulk`) are
kept. SRR5378413 was listed as a kept example in the trap
`rna-uppercase-alias-is-not-an-allele` and was removed from it.

A last model batch reviewed about 220 runs in 22 more studies and found nothing to leave out;
the spending cap stopped the rest.

## 2026-11-25, audit: A. thaliana RNA-seq gold v5

The rebuild drops 339 runs from 49 studies (31,644 → 31,305 rows) and changes nothing else.
They were found by reviewing 5,811 kept runs of 634 more random studies of gold v4 with the
same criteria (Opus 5.5); every leave quotes the run's metadata and was read before being
added to `exceptions.yaml`. Mutant, transgenic, inducible and reporter lines (including
GFP-sorted root cells), other genotypes (Ler, Col-3, autotetraploid Col-0, a Col-0 x C24
triploid), callus and protoplasts, and assays that are not expression RNA-seq (subcellular
fractions, translatome, m6A IP, circular RNA, intron capture, 3' end). For PRJNA1125006 the
p4h5 lines were told apart from Col-0 by the BioSample phenotype field.

About 6% of these audited runs were wrong (7% in the first audit). The two audits
reviewed 11,138 runs in 1,309 studies, about half of the studies in the sheet. Uncertain
answers (about 9%) were left as kept.

## 2026-11-24, audit: A. thaliana RNA-seq gold v4

The rebuild drops 749 runs (32,393 → 31,644 rows); 77 kept rows change only `sample_id`
(tissue label), and no run is added.

- 351 runs of protoplasts, callus and cell cultures (33 studies). The builder recorded these
  as tissue `cell culture` but never left them out; it now does (policy 2026-11-19). Bare
  "suspension" no longer means cell culture: protocol text also uses it for bacterial
  inocula, which had mislabelled the tissue of 77 kept rows.
- 398 runs from 56 studies, found by reviewing 5,359 kept runs of 685 random studies with
  the adjudication criteria (Opus 5.5). Every leave quotes the run's metadata and was read
  before being added to `exceptions.yaml`: mutant, transgenic, inducible and reporter lines
  (including mock samples of inducible lines), other genotypes (Col-2, FRI-Col, tetraploid
  Col-0, two hybrids), and assays that are not expression RNA-seq (5'P-seq, TAIL-seq,
  PAL-seq, RNase R circRNA, nascent, polysome, chromatin-bound, nuclear and organelle RNA).
  The six Col-0 runs of PRJNA1327086 (5P-seq) were found by the builder's alias review.

About 7% of the audited runs were wrong, so the remaining 2,105 studies probably hold a
similar share; the uncertain answers (about 7%) were left as kept.

## 2026-11-24, later: multiome ATAC whitelist (issue #17)

The 36 `10x-multiome-atac` rows of the scATAC-reads gold (25 A. thaliana, 11 Sorghum) name
`737K-arc-v1.txt` instead of `-`: multiome ATAC barcodes are corrected against Cell Ranger ARC's
ATAC list (SRR26989943 needs it). The builder takes the whitelist from the chemistry; the
rebuild differs from the old gold in this column only.

## 2026-11-24: A. thaliana RNA-seq gold v3

The measured rebuild drops 638 more runs (33,031 → 32,393 rows) and changes nothing else;
build and gold agree at 1.0 with 20/20 traps.

How they were found: in 98 studies, some sample aliases name the wild type (`WT_1`,
`Col-0_ctrl_1`) and other kept runs' aliases do not. A rule on that contrast would be right
about three times in four (treated wild type such as `PFBS treated 1 d_1`, `Botrytis-infected`,
or glued markers such as `HWT1` and `R10_WTU6HI` also lack the word), so the 798 runs were
reviewed with the model instead (about $4.40, Opus 5.5): 504 leave, 155 keep, 139 uncertain.
Uncertain groups were settled by their siblings and the study design (`snc1_*`, `tga256_*`,
`m0-*` next to `WT0-*`, `co-9`); doubtful calls were checked against ENA (CNP: BioSample
`sample_type` Mutant1; PRJNA770668 genotype `X0` next to `WT`; PRJNA271927 `FN_*`: ecotype
Col-0/Ped-0). Four calls were overruled and the runs kept: `ColORoot3` (a typo of `Col0Root3`),
`C-C-root-FN` (a Col/Col homograft), `CW-2_S53` and `NPR1 MOD Control` (controls).

The 638 runs, from 81 studies, are curated exceptions in `sheets/data/exceptions.yaml`, one entry
per study with the aliases as evidence. Traps: `rna-mutant-named-against-wild-type-aliases`,
`rna-treated-wild-type-without-wild-type-marker`.

## 2026-11-23, later: A. thaliana RNA-seq gold v2

The builder now does what the review below did by hand, and the A. thaliana RNA-seq gold is
its measured rebuild (33,283 → 33,031 rows; build and gold agree at 1.0 with 18/18 traps):
- `classify.alias_flag` reads the sample alias: a transgene word, a promoter fusion (`::`,
  even beside `WT`), or a mutant allele in lower case (`pad4-1`, `gsnor1-3`; upper case such
  as `ENO2-3` or `Pb0-3` is a protein or a treatment, and irradiation words are a
  treatment). `TRANSGENE` also names GR fusions, pER8, GVG, HyperTRIBE and DN lines.
- The 66 reviewed runs no rule sees (`arr1 ...`, `M_*`, `CB_*`, the Mock of a pER8 line, ...)
  are curated exceptions in `sheets/data/exceptions.yaml`, with their evidence.

Newly left out, each checked against the ENA sample aliases (and BioSample where unclear):
- 249 runs of mutant or transgenic lines from 45 studies that the review had not reached:
  lower-case alleles (gsnor1-3, pepr1-2, ago10-1, ga1-13, elf7-3, ssrp1-2, ap2-12, sdg8-1,
  bbx5-1, ...), OE/OX/KO/kd lines, GFP fusions, `p35S::CUC2:GR`, and inducible lines
  (DOF2.1-GR, PTLg-GR, pER8-LEC2, pER8::YUC6, GVG::TPS1, ECT2 HyperTRIBE).
- 6 duplicate submissions the previous build had missed (PRJNA910862, PRJNA445833,
  PRJNA525432); both copies had been kept.

Back in: the 3 PRJNA412215 runs. A Col-0 cell culture is a kept tissue, and the culture
conditions that led to the 2026-11-19 drop are a treatment, which is kept since #13.

Not flagged, on purpose: PRJNA904293's `pp5-1`-style codes (BioSample ecotype Col-0, no
genotype stated) and uppercase labels such as `MAC5-1`.

DAP-seq: PRJNA1086931 now has `genotype: Col-0` in `dap_studies.yaml`, so the build gives the
gold's sample IDs.

## 2026-11-23: regenerated from the measured rebuilds

Every gold set was replaced by its measured rebuild (`genesis sheets build` with read
measurements), so the gold follows the policy of keeping treated samples (#13) and the
scATAC chemistry rules (#14, #16). Rows, old → new:

| Gold | Before | After |
| --- | ---: | ---: |
| A. thaliana RNA-seq | 14,215 | 33,283 |
| A. thaliana bulk ATAC | 208 | 356 |
| A. thaliana scATAC (libraries) | 18 | 35 |
| A. thaliana TSS mapping | 62 | 93 |
| A. thaliana DAP-seq | 120 | 125 |
| Sorghum RNA-seq | 682 | 1,653 |
| Sorghum bulk ATAC | 8 | 8 |
| Sorghum scATAC (libraries) | 8 | 13 |
| Sorghum DAP-seq | 15 | 15 |

Every run of the earlier gold is kept, except:
- 22 A. thaliana RNA-seq runs and 1 Sorghum one (SRR6205694) that duplicate another
  submission. The rebuild keeps the original run and leaves out the resubmission; the old gold
  had kept the resubmission.
- 10 A. thaliana RNA-seq runs, part of the review below.

**Review of the A. thaliana RNA-seq additions.** Treated runs from studies whose untreated runs
the old gold had left out were never checked for genotype. The ENA sample alias, sample title
and experiment title of every study with added runs (1,763 of 1,764 studies) were read, and 104
runs were changed to left out:
- 99 runs of mutant or transgenic lines named only in the alias or title, while the genotype
  field says Col-0: GR and pER8 inducible lines, HyperTRIBE, dominant-negative (DN) and DD
  lines, and mutant aliases such as bon123pad4-1, mta and nramp1. Their reason is
  `genotype: mutant / transgenic (<evidence>)`. They include SRR27606461-2 (PRJNA1065151),
  whose reads are those of the study's nramp1 mutant runs.
- 5 runs of subcellular fractions (PRJNA843332): `not standard RNA-seq (subcellular fraction)`.

The 3 PRJNA412215 cell-culture runs dropped on 2026-11-19 stay out (`tissue: cell culture`).
Per study: PRJNA729190 27, PRJDB35644 15; PRJNA1000649, PRJNA602051, PRJNA907415, PRJDB33703,
PRJNA1271688 and PRJNA625276 6 each; PRJNA843332, PRJEB56577 and PRJEB28655 5 each;
PRJNA1025919 and PRJNA1064056 3 each; PRJNA1065151 and PRJNA867415 2 each; PRJNA1199402 1.
Traps
`rna-mutant-named-only-in-alias`, `rna-transgenic-line-in-title` and
`rna-subcellular-fraction` record them; the builder catches them since gold v2 (above).
A random check of 60 other added runs found 1 to 3 more such lines; gold v2 removed 249 of
them.

**DAP-seq label.** PRJNA1086931's rows keep genotype Col-0, as in the old gold (BioSample
genotype Col-0); the rebuild names them `unknown` because it read no genotype.

## 2026-11-23: Sorghum sci-ATAC added

Sorghum scATAC, 2 libraries added (6 → 8 rows): SRP483298 leaf rep 1 and rep 2 (SRR27704581,
SRR27704580; Mendieta 2024). They were left out only because the workflow did not read their
28-base index read. genesis-preprocessing #54 added the `sci-atac` chemistry for it. On 200k
spots of SRR27704581, 80% of reads mapped and 1,224 barcodes had at least 20 fragments.

## 2026-11-20: treated samples are kept (issue #13)

A policy change, not a correction of single runs. Treatment is no longer a reason to leave a
run out: treated, stressed and infected samples of the reference genotype are usable data, and
their treatment is recorded in the runs-qc `treatment` and `treatment_text` columns. The gold
sheets published before this date leave those runs out, so measured rebuilds score lower
precision against them (Sorghum RNA-seq 0.41, A. thaliana ATAC 0.58) while recall stays 1.0
(Sorghum RNA-seq 0.9985, as before). The gold sheets were regenerated from the measured
rebuilds on 2026-11-23 (above).

| Gold | Before | After the rebuild |
| --- | ---: | ---: |
| Sorghum RNA-seq | 681 | 1,653 |
| Sorghum scATAC (libraries) | 6 | 11 |
| Sorghum TSS mapping | 0 | 4 |
| A. thaliana bulk ATAC | 208 | 356 |
| A. thaliana scATAC (libraries) | 18 | 35 |
| A. thaliana DAP-seq | 120 | 124 |
| A. thaliana TSS mapping | 62 | 93 |

Issue #14 changed scATAC reasons only: sci-ATAC runs now name their 28-base barcode read, and a
library mixing chemistries keeps its main chemistry's runs (rice SRP438549).

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
