# Evaluation

These files measure how well `genesis sheets build` reproduces the curated sample sheets.
How to build and score a sheet is in [sample sheets](../docs/usage/sample-sheets.md).

| File | Contents |
| --- | --- |
| `gold.yaml` | The gold sheets and runs-qc tables on the project's Hugging Face bucket, one entry per species and workflow, with SHA-256 checksums. `genesis sheets gold-fetch` downloads them. |
| `traps.yaml` | Runs that earlier curation rounds got wrong or nearly wrong (trimmed uploads, mislabelled layouts, "DAP" meaning days after pollination, duplicate submissions and so on). Each trap says whether its runs must be kept or left out, and why. Every trap must pass. |
| `HISTORY.md` | One row per scored build, appended by `genesis sheets score --history`. |
| `GOALS.md` | Measurable targets for the builders and the model steps, their current values, and the loop that improves them. |

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

## LLM steps

`genesis eval llm` scores the app's model calls against the same gold sheets, on a provider
configured in a runtime (`genesis workspace provider`). Two tasks:

| Task | What it runs | Gold |
| --- | --- | --- |
| `triage` | The mining step that accepts, holds or excludes a study for an assay (`web/mining.py`) | A study with a kept run in a workflow's sheet matches that assay. A study kept for another assay and absent from this workflow's runs-qc, or whose runs were all left out as another assay, is excluded. |
| `adjudicate` | Per-run keep or leave-out from free-text metadata (`sheets/adjudicate.py`) | Kept runs are keep. Runs left out for genotype, assay or tissue are leave (treated samples are kept, issue #13). Read, file and duplicate reasons are skipped. |

```sh
genesis eval llm --task adjudicate --root "$GENESIS_RUNTIME" --gold gold \
    --species Arabidopsis_thaliana --split dev --studies 10 --per-study 20 \
    --out eval-adjudicate --history evals/LLM-HISTORY.md --label "what changed"
```

The gold runs-qc tables decided free-text cases with title rules, and those labels are often
wrong (a third of the disputed runs in the first pilot). `evals/adjudication-verified.tsv` is a
hand-checked set of runs labelled blind from the archive metadata (and, where that was not
enough, the ENA or GEO record) under the criteria in `sheets/adjudicate.py`:

| Species | Workflows | Runs |
| --- | --- | --- |
| Arabidopsis thaliana | RNA-seq, ATAC-seq, TSS-seq, DAP-seq | 149, 46, 46, 40 |
| Sorghum bicolor | RNA-seq, ATAC-seq | 19, 12 |
| Oryza sativa | RNA-seq | 45 |

For RNA-seq, 3' tag expression libraries, treated, stressed or infected samples of the
reference genotype (issue #13), and the wild-type controls of a mutant experiment are kept;
protoplasts (including vector controls), mutants, transgenics and near-isogenic lines are
left out. Runs labelled `uncertain` are questions the
metadata does not answer (for example DAP-seq whose genomic DNA accession is never stated);
they are kept in the file as a record and not scored. Score adjudication against the set with
`--verified evals/adjudication-verified.tsv --studies 100 --per-study 20`.

Studies are split by a hash of the accession: about 80% go to `dev`, the rest to `test`.
Tune prompts on dev and score test only at milestones. `--dry-run` counts the cases without
calling anything. Responses are cached in the runtime by request hash, so rerunning the same
cases costs nothing. Every run writes `report.json` and `cases.jsonl` (one line per case,
with the model's decision, reason and quotes); `--history` appends a row to
`LLM-HISTORY.md`. A decision whose quotes are not in the metadata it was given counts as
uncertain.
