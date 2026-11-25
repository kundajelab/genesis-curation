# Sample sheets for genesis-preprocessing

`genesis sheets` builds a run-ready sample sheet for one
[genesis-preprocessing](https://github.com/kundajelab/genesis-preprocessing) workflow and one
plant species, then scores it against curated gold sheets. It works only from the command line
and does not need the web app, a runtime directory or a model provider.

| Command | What it does |
| --- | --- |
| `genesis sheets build` | Finds candidate runs in ENA/SRA (and GSA for ATAC), applies the workflow's rules, probes or measures reads where needed, and writes a sheet plus a runs-qc table |
| `genesis sheets gold-fetch` | Downloads the gold sheets listed in `evals/gold.yaml` and checks their SHA-256 |
| `genesis sheets score` | Compares a built sheet with gold and the known traps in `evals/traps.yaml` |

## Setup

Use a native checkout (see [the development guide](../development-handoff.md)):

```sh
uv sync --frozen --extra web
export TMPDIR="$PWD/../tmp"          # keep downloads and caches out of /tmp
uv run genesis sheets --help
```

Prefix the commands below with `uv run`, or activate `.venv` first.

## Supported workflows and species

| `--workflow` | Source of candidates | Read checks | Extra tools needed for read checks |
| --- | --- | --- | --- |
| `rna-seq` | ENA | Strandedness (sampled reads mapped with STAR, counted with featureCounts); duplicate submissions | STAR, featureCounts, gffread, species references |
| `atac-seq` | ENA, plus GSA runs from `--extra-runs` | Mapped fraction, organelle fraction and TSS enrichment of 200,000 read pairs (chromap) | chromap, species references |
| `tss-seq` | ENA, plus runs from `--extra-runs` | 5' trim, 3' adapter and TSS signal of 20,000 reads (STAR) | STAR, species references |
| `dap-seq` | ENA | Untrimmed reads (first 100 read lengths) | none |
| `scatac-reads` | NCBI SRA, plus experiments from `--extra-runs` | Read structure, chemistry, whitelist, instruments | `vdb-dump` (SRA Toolkit) |

`--species` takes the folder name used in genesis-preprocessing sheets. Species settings live in
[`src/genesis_curation/sheets/data/species.yaml`](../../src/genesis_curation/sheets/data/species.yaml):
taxon, reference genotype, how titles name it, and the reference file names written to the
sheet. `Arabidopsis_thaliana`, `Sorghum_bicolor` and `Oryza_sativa` (Nipponbare, Ensembl Plants
IRGSP-1.0 references) are defined today; rice has no gold sheets yet. Adding a species means
adding an entry there and putting its references in the shared references folder that
genesis-preprocessing runs with. `more_taxa` adds NCBI taxa searched as the same species, such
as rice's japonica group, where many Nipponbare runs are filed.

`--references` points to a folder that holds those reference files (and, for RNA, the STAR index
genesis-preprocessing builds, `<fasta>.<annotation>.star`). It is needed only when reads are
measured.

## Build a sheet

A full build measures reads and needs the tools above:

```sh
genesis sheets build --workflow rna-seq --species Sorghum_bicolor --out sb-rna \
    --references REFS --star STAR --featurecounts FEATURECOUNTS --gffread GFFREAD
```

A metadata-only build skips read measurement. Runs that would need a measurement are left out
as "not measured", unless you reuse measurements from an earlier runs-qc table:

```sh
genesis sheets build --workflow rna-seq --species Sorghum_bicolor --out sb-rna-meta \
    --no-measure --strandedness-from gold/Sorghum_bicolor/rna-seq-curated.runs-qc.tsv
genesis sheets build --workflow tss-seq --species Arabidopsis_thaliana --out at-tss-meta \
    --no-measure --measure-from gold/Arabidopsis_thaliana/tss-seq-curated.runs-qc.tsv
```

More examples:

```sh
# Bulk ATAC: --extra-runs is the "Bulk ATAC Fable Curated" tab of the Genesis metadata sheet,
# exported as TSV. It adds runs ENA does not label ATAC-seq and the GSA (CRR) runs.
genesis sheets build --workflow atac-seq --species Sorghum_bicolor --out sb-atac \
    --extra-runs bulk-atac-tab.tsv --references REFS --chromap CHROMAP

# TSS and scATAC take the "TSS Fable Curated" and "scATAC Fable Curated" tabs the same way
# (the TSV needs species and run_accessions, sra_runs or experiment_accession). Listed runs the
# search missed, such as ENA runs without taxonomy, get a decision; a listed run that ENA or
# SRA does not serve (GSA, withdrawn) is recorded with that reason.
genesis sheets build --workflow tss-seq --species Arabidopsis_thaliana --out at-tss \
    --extra-runs tss-tab.tsv --references REFS --star STAR

# DAP-seq, leaving out studies that a repository sheet already covers
genesis sheets build --workflow dap-seq --species Arabidopsis_thaliana --out at-dap \
    --skip-runs-in repository-dap-seq.tsv

# scATAC from reads
genesis sheets build --workflow scatac-reads --species Sorghum_bicolor --out sb-scatac \
    --vdb-dump VDB_DUMP
```

Useful options for every workflow:

- `--cache DIR`: archive responses and measurements (default `OUT/cache`). Reusing a cache makes
  a rebuild fast; `--offline` uses only the cache.
- `--no-probe`: skip the cheap read probes (duplicate reads, instruments, read lengths).
- `--threads N`: threads for read measurement.

Run `genesis sheets build --help` for the full list.

## Metadata review with a model (RNA-seq, optional)

The rules decide most runs from archive fields. Two groups rest on free text: runs the rules
kept, and runs left out only because the sample title suggests a mutant. With
`--adjudicate RUNTIME`, a model configured in that runtime (`genesis workspace provider`)
reads each study's text and these runs' sample metadata, and answers keep, leave or uncertain
for each run, quoting the metadata it relied on.

```sh
genesis sheets build --workflow rna-seq --species Sorghum_bicolor --out sb-rna-reviewed \
    --no-measure --strandedness-from gold/Sorghum_bicolor/rna-seq-curated.runs-qc.tsv \
    --adjudicate RUNTIME
```

- **keep** clears a title-rule reason; **leave** leaves the run out with the reason
  `metadata review: <class>` (genotype, tissue, assay, qc or other). Treated samples are
  kept, not left out.
- **uncertain**, a failed call, or an answer whose quotes are not in the metadata keeps the
  rules' decision.
- Runs left out by any other rule are never sent. `--adjudicate-limit` (default 2000) stops
  the build before it sends more runs than that; `--adjudicate-provider` picks a provider
  other than the runtime's default.
- `--adjudicate-scope alias` sends only the kept runs whose sample alias and title lack the
  wild-type word that other aliases of the same study carry ("hda19-3_rep1" next to
  "Col-0_rep1"). About 3 in 4 of these are mutant or transgenic lines and the rest are
  treated wild type, so they get the review rather than a rule. This scope is small enough
  for a whole species: about 900 A. thaliana runs, where the default `title` scope sends every
  kept run.
- The runs-qc table gains `adjudication` and `adjudication_reason` columns, and `build.json`
  gains an `adjudication` block with counts of changed decisions and tokens used.
- `--adjudication-from RUNS_QC` reapplies the decisions recorded in an earlier build's
  runs-qc table (its `adjudication` columns) to the same runs, with no model call. With
  `--adjudicate`, only runs that table does not cover are sent. Use it to rebuild a sheet after
  a rule change without paying for the review again; `build.json` counts the runs `reused`
  and those left `unreviewed`.

The criteria the model applies are in `src/genesis_curation/sheets/adjudicate.py`. How well it
agrees with hand-checked answers is measured by `genesis eval llm` (see
[evaluation](../../evals/README.md#llm-steps)).

## Outputs

Each build writes three files to `--out`:

| File | Contents |
| --- | --- |
| `<workflow>.tsv` | The sample sheet, validated against the workflow's schema from genesis-preprocessing |
| `<workflow>.runs-qc.tsv` | Every candidate run with `run`, `study`, `sample_id`, `in_sheet` (`yes`/`no`) and `reason_left_out`, followed by the metadata and measurements behind the decision |
| `build.json` | Counts of runs kept and left out by reason, archive requests and step timings |

Every left-out run has exactly one reason: the first rule that applies. The rule order for each
workflow is documented at the top of its module (`rna.py`, `atac.py`, `tss.py`, `dap.py`,
`scatac.py` under `src/genesis_curation/sheets/`). Read the runs-qc table to check a build; it
is the record of why each run was or was not kept.

## Curated decisions

Some decisions cannot be made from archive metadata or reads. They live in data files under
`src/genesis_curation/sheets/data/`, each entry with its evidence:

| File | Used for |
| --- | --- |
| `species.yaml` | Per-species settings (see above) |
| `exceptions.yaml` | Runs to leave out of RNA or ATAC sheets, with a reason |
| `treatments.yaml` | Treatments stated only in a study description, recorded in runs-qc (treated samples are kept) |
| `tss.yaml` | TSS method or exclusion for studies the archive describes wrongly |
| `dap_studies.yaml` | DAP studies whose libraries and controls need explicit pairing rules |
| `scatac.yaml` | scATAC libraries to merge, mark as duplicates or exclude |

Prefer a general rule in the builder over a data-file entry. Add an entry only when the
evidence is outside the archive record (a paper's methods, a GEO page, a read check).

## Score against gold

```sh
genesis sheets gold-fetch --to gold
genesis sheets score --workflow rna-seq --species Sorghum_bicolor --gold-dir gold \
    --candidate-sheet sb-rna-meta/rna-seq.tsv --candidate-qc sb-rna-meta/rna-seq.runs-qc.tsv \
    --history evals/HISTORY.md --label "sb metadata-only"
```

The gold sheets are the curated sheets on the project's Hugging Face bucket, listed with
checksums in [`evals/gold.yaml`](../../evals/gold.yaml). The score report gives:

- run-level precision and recall (`precision_known` ignores runs gold never considered);
- accuracy of each workflow field (strandedness, TSS trim and adapter, DAP control, scATAC
  chemistry and whitelist, and so on);
- how gold and candidate reasons for left-out runs correspond;
- pass or fail for each trap in [`evals/traps.yaml`](../../evals/traps.yaml).

`--history` appends one summary row to [`evals/HISTORY.md`](../../evals/HISTORY.md).

### Every workflow at once

`campaign` runs a metadata-only build of each workflow for one species and scores each against
gold where gold exists. With `--gold-dir`, it reuses the gold runs-qc measurements
(strandedness for RNA-seq, TSS and ATAC checks), as in the examples above:

```sh
genesis sheets campaign --species Sorghum_bicolor --out sb-all --gold-dir gold \
    --history evals/HISTORY.md --label "sb metadata-only"
```

It writes `OUT/<workflow>/` (the build's three files and `score.json`) and `OUT/campaign.json`,
and exits non-zero if any workflow fails. `--workflows` picks a subset, and `--atac-extra-runs`,
`--tss-extra-runs`, `--scatac-extra-runs` and `--dap-skip-runs-in` pass through to those builds. With `--gold-dir`, DAP-seq skips the
studies of the former repository sheets that `gold-fetch` downloads (listed in `gold.yaml`). For a species without gold, such as
rice, it only builds.

The app's **Sample sheets** page runs the same campaign as a background job: choose a species
and workflows, then download each sheet, runs-qc table and score. The app keeps the archive
cache and results in the runtime's `sheets/` folder. For bulk ATAC, copy the
metadata sheet's Bulk ATAC tab (TSV) to `sheets/inputs/bulk-atac-tab.tsv` in the runtime; the
page says when it is missing. Read measurement is CLI only.

The targets are precision and recall of at least 0.95, at least 0.98 on every field and all
traps passing. Fix each miss in one of three ways: change a rule (and add a trap for it),
add a data-file entry with its evidence, or, when gold is wrong, record the correction with a
note. See [`evals/README.md`](../../evals/README.md).

## Running the sheet

The sheet lists FASTQ URLs and reference file names, so it runs as is with genesis-preprocessing
once the references are in its shared references folder. Before a large run, try a few rows
first and check that they finish.
