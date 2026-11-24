# Improvement goals

What "better" means for genesis-curation, as numbers that can be checked, and the loop that
moves them. The targets in [README.md](README.md#targets) are the floor; this file adds the
LLM steps and coverage, and says what to work on next.

## Targets

| Area | Measure | Target | Now (2026-11-19) |
| --- | --- | --- | --- |
| Sheets | run precision and recall, every gold set | ≥ 0.95 | 1.0 on all (2026-11-23, gold v2) |
| Sheets | accuracy on every workflow field | ≥ 0.98 | 1.0 |
| Sheets | traps passing | all | all (2026-11-23: 18/18 RNA-seq) |
| Adjudication | agreement with the verified set, every workflow and species, dev and test | ≥ 0.98 | 1.0 on all (RNA, ATAC, TSS, DAP; rice RNA) |
| Adjudication | runs answered uncertain | ≤ 5% | RNA 1.9% dev, 6.5% test; ATAC 7.1%, TSS 8.3% dev; DAP and rice 0% |
| Triage | `match_not_excluded` (studies that match a gold study and are not excluded) | ≥ 0.98 | 1.0 |
| Triage | `excluded_precision` | ≥ 0.95 | 1.0 |
| Triage | `exclude_excluded` | ≥ 0.98 | 0.961 |
| Coverage | verified set covers every workflow and species with gold | all | RNA, ATAC, TSS, DAP (A. thaliana); RNA, ATAC (Sorghum); RNA (rice); no scATAC |
| Cost | Opus 5.5 spend per scored eval run | ≤ $3 | about $2 triage, $0.5 adjudication |

A target that is met stays in the table, so a regression shows up as a row that is no longer met.

## The loop

Run it after every change to a builder rule, a data file, a prompt or the criteria, and at
least once a week while curation is active.

1. **Rescore.** `genesis sheets campaign` for each species with gold, then
   `genesis eval llm --task adjudicate --verified evals/adjudication-verified.tsv` and
   `--task triage` on the dev split. Append rows to `HISTORY.md` and `LLM-HISTORY.md`.
2. **Pick the largest gap** in the table above. Ties go to the one that affects the most runs.
3. **Triage each miss** against ENA, GEO or the paper. Decide which of three it is: a rule
   (fix the builder), outside evidence (a `sheets/data/*.yaml` entry), or a wrong answer key
   (a gold correction or a verified-set fix, with evidence).
4. **Make it stick.** Add a trap, or a verified-set row, that fails without the fix.
5. **Rescore**, update the "Now" column and note the change in the history row.

Work on the test split only to report: changes are made against dev, and test is scored once
per change to confirm it holds.

## Next

1. Duplicates of left-out runs: the duplicate probe compares kept runs only, so a
   resubmission of a mutant run is kept unless its own metadata says mutant (SRR27606461,
   now an exception). Read the BioSample genotype in DAP-seq instead of per-study entries.
2. Publish measured rice sheets (RNA-seq with the model review, ATAC, DAP, TSS, scATAC) and
   add rice to `gold.yaml`, so rice is scored like the other two species.
3. Bring uncertain answers under 5% for ATAC and TSS; read which runs the model leaves
   uncertain and decide whether the criteria or the verified labels should say more.
4. Triage: show the model each study's experiment counts by library strategy, so studies whose
   RNA-seq runs fall outside the sampled experiments are not excluded.
5. Verified-set policy questions still open: Arabidopsis DAP-seq whose genomic DNA accession is
   never stated; autotetraploid reference lines. Runs that hinge on them are labelled uncertain.
