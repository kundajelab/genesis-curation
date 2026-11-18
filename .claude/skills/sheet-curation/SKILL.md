---
name: sheet-curation
description: Build, score and improve genesis-preprocessing sample sheets with `genesis sheets`. Use when asked to build or refresh a curated sheet, score a build against the gold sheets, explain why runs were left out, fix a curation miss, or add a species or workflow.
---

# Sample-sheet curation loop

The commands and options are in [docs/usage/sample-sheets.md](../../../docs/usage/sample-sheets.md),
and the targets and gold files are in [evals/README.md](../../../evals/README.md). Read both
before starting; this skill gives the order of work and the judgement calls.

## 1. Build and score

1. Run `genesis sheets gold-fetch --to gold` once. It checks every file against
   `evals/gold.yaml`.
2. Build. Start with a metadata-only build (`--no-measure`, reusing measurements with
   `--strandedness-from` or `--measure-from`). It takes minutes and tests the rules. Run a
   measured build only when the reads themselves need checking.
3. Score with `--history evals/HISTORY.md` and a `--label` that says what was reused. The
   targets: precision and recall of at least 0.95, at least 0.98 on every field, and every
   trap passing.

## 2. Triage each miss

The score report lists example false negatives and false positives. For each one, read its row
in the build's `<workflow>.runs-qc.tsv` (`in_sheet`, `reason_left_out`) and the gold's
runs-qc row, then check the run on ENA (the filereport API: experiment and sample titles,
library strategy and selection, read and base counts) or on GEO or GSA. Group misses by
study and by reason; one rule usually explains many runs.

Decide which side is wrong:

| Finding | Fix |
| --- | --- |
| A rule is wrong or missing | Change the builder in `src/genesis_curation/sheets/`, then add a trap |
| The evidence is outside the archive record (a paper, a GEO page, a full pipeline run) | Add an entry with a `why` to `src/genesis_curation/sheets/data/*.yaml` |
| The gold is wrong | Ask the user before changing the gold (see below) |
| It can't be settled from the records | Leave it as it is and note it in `evals/gold-corrections.md` |

A matching title is not a duplicate: call one a duplicate only when the read counts and first
reads match. Don't change a rule only to agree with the gold; check that the gold is right first.

## 3. Correct the gold (with the user's OK)

1. Copy the files to change from the bucket into a new working folder, keep an untouched copy,
   and edit with a script that writes the builder's `reason_left_out` text.
2. Add a dated entry under "Corrections" in the bucket README and in
   `evals/gold-corrections.md`, with one line of evidence per change.
3. Run `hf buckets sync <folder> hf://buckets/Stanford-Genesis/genesis-curation/curated-sample-sheets --plan plan.jsonl`
   and show the user the files and row counts. Upload with `--apply plan.jsonl` only after
   their OK.
4. Update the SHA-256s in `evals/gold.yaml`, fetch the gold again, and check that it matches.

## 4. Record and rescore

- For each new kind of miss, add a trap to `evals/traps.yaml` and a row to `LESSONS.md`
  (symptom, cause, fix, trap).
- Rescore every affected species and workflow and append to `HISTORY.md`.
- Run `scripts/check.sh` before pushing.
- Never put server paths or account names in commits, sheets or bucket files.
