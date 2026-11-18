# Evaluation

These files measure how well `genesis sheets build` reproduces the curated sample sheets.
How to build and score a sheet is in [sample sheets](../docs/usage/sample-sheets.md).

| File | Contents |
| --- | --- |
| `gold.yaml` | The gold sheets and runs-qc tables on the project's Hugging Face bucket, one entry per species and workflow, with SHA-256 checksums. `genesis sheets gold-fetch` downloads them. |
| `traps.yaml` | Runs that earlier curation rounds got wrong or nearly wrong (trimmed uploads, mislabelled layouts, "DAP" meaning days after pollination, duplicate submissions and so on). Each trap says whether its runs must be kept or left out, and why. Every trap must pass. |
| `HISTORY.md` | One row per scored build, appended by `genesis sheets score --history`. |

## Targets

- Run-level precision and recall of at least 0.95. `precision_known` ignores runs that gold
  never considered, which separates real mistakes from runs gold missed.
- At least 0.98 accuracy on every workflow field.
- Every trap passes.

## Fixing a miss

Each miss is fixed in one of three ways:

1. **A rule is wrong or missing.** Fix the builder and add a trap to `traps.yaml` that would
   have caught it.
2. **The evidence is outside the archive record** (a paper's methods, a GEO page, a read
   check). Add an entry with that evidence to the matching file in
   `src/genesis_curation/sheets/data/`.
3. **Gold is wrong.** Record the correction and its evidence in
   [`gold-corrections.md`](gold-corrections.md). Gold on Hugging Face is changed
   only with the maintainers' agreement.

Then rescore and append the result to `HISTORY.md`. A history row notes whether reads were
measured or reused from gold (`--no-measure` with `--strandedness-from` or `--measure-from`),
because builds that reuse measurements test only the metadata rules.
