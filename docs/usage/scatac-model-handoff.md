# Loading scATAC exports

A Genesis model bundle contains `genome.fa`, `chrom.sizes`, `signal.bw`, separate
peak/background BEDs for each fold, `loader.json`, `config.json`, QC, source-cell
membership and checksum provenance. Keep the entire directory together.

Before training, verify the bundle, confirm biological identity and annotation
review, and inspect the declared chromosome folds. Genesis exports are marked
`model_ready: false`: they require loader testing and scientific review outside
the export command. File-format compatibility alone is insufficient.

## Cherimoya

Use commit `8ecf7f791ec07c05a701076cc58a0f845bf97609`. Its `PeakGenerator` accepts
one unstranded signal channel for this adapter:

```python
from cherimoya.io import PeakGenerator

loader = PeakGenerator(
    peaks="train.peaks.bed",
    negatives="train.background.bed",
    sequences="genome.fa",
    signals=["signal.bw"],
    controls=None,
    in_window=2114,
    out_window=1000,
    max_jitter=0,
    summits=True,
    negative_ratio=1,
    random_state=7,
    num_workers=0,
)
```

Use the actual window/jitter values in `loader.json`, not these fixture values
when they differ. The model's `fit` function expects the sampler
`loader.dataset`; it constructs its own loader. The validation environment tests
IO only. Install the pinned model repository's full declared environment before
training. Model architecture, loss, optimizer and compute resources remain model
configuration decisions.

## ChromBPNet

Use commit `ece97c93ccaa2d9ee5bc5687e62f4dbf8d055367`. The adapter's narrowPeak-like
regions include a valid summit offset in column 10. Load the exported FASTA and
raw cut-count bigWig with `ChromBPNetBatchGenerator`. With the 2114-base input window
used in the validation fixture, its sequence tensor is `[batch,2114,4]`, whereas
Cherimoya's loader returns `[batch,4,2114]`. Use each model's own tensor layout
and configuration.

ChromBPNet needs a suitable bias model and its own training configuration. No
human bias model is chosen automatically. Exported bigWigs already use the audited
ATAC cut convention. Do not run an additional fragment-to-track shift on them.

## Reproduce loader acceptance

The real-tool fixture generator was `tests/validate_scatac_products.py`, removed with
the pseudobulk commands; run it from a checkout at `ac36afc` or earlier. Export its
products with a config naming the exact target SHA, input window 2114, output
window 1000, jitter 0, no controls, `nonoverlapping-genome-tiles-v1` background,
and folds `chr1` / `chr2` / `chr3`. For ChromBPNet declare the unsupplied fixture
bias model explicitly.

Frozen Cherimoya IO dependencies are in
`validation/environments/scatac-loaders/` of genesis-preprocessing, where these historical
loader checks remain. From that checkout, run:

```bash
pixi run uv run --frozen --project validation/environments/scatac-loaders python validation/experiments/scatac/cherimoya_loader.py --model-root ../benchmarks/cherimoya --bundle ../results/scatac-products-fixture/cherimoya
```

`validation/experiments/scatac/chrombpnet_loader.py` takes the same arguments and
runs in the recorded ChromBPNet container. Both scripts test all three folds with
a positive and a background window. Exact recorded Docker arguments and timings
are in `validation/reports/scatac-command-summary.json`.

The deterministic fixture should yield 600 counts in each positive window and
zero in its background. Those values validate the fixture only; they are not
plant QC thresholds. This check tests data loading, not model training or biological
performance.

## Inspect a bundle

```bash
genesis scatac model inspect --input models/TASK_ID --format json
genesis scatac model inspect --input models/TASK_ID --format markdown --output handoff.md
```

Inspection is read-only. It does not approve a dataset, train a model, launch a
loader, query/migrate a registry or fetch referenced files. Output must be a new
file outside the bundle; otherwise the report goes to stdout. Both formats omit
local paths and URLs from declared metadata. Markdown escapes supplied text.
Source locators identify bundle-relative files and fields, not external paths.

The report separates identity, source scope, reference sequence, annotation,
signal semantics, target revision, artifact checks and missing acceptance.
Missing depth/sampling provenance yields `UNKNOWN`; a name resembling a study or
fixture is not evidence of scope. The exported FASTA checksum and observed contig
lengths are distinct from the original reference file's declared checksum, which
may describe compressed bytes. Annotation suitability and original-author identity
are separate questions; a supported equivalence requires review.

By default inspection reads at most **64 MiB, 100,000 records and 30 seconds**.
These are shared limits, including metadata, interval reads and checksum passes;
bytes can be counted again when a file is read for a different check. Large files
that cannot fit the remaining byte allowance are skipped as `INCOMPLETE`. There
is no hidden full-genome scan. Increase limits explicitly when needed:

```bash
genesis scatac model inspect --input models/TASK_ID --max-bytes 1073741824 --max-records 1000000 --max-seconds 120
```

Region checks inspect ten-column summit BEDs, chromosome membership, full
input-plus-jitter bounds, declared fold/count agreement and positive/background
context overlap. Positive regions may overlap each other within one fold.
Empty files can be valid intervals but fail this adapter's nonempty-fold
requirement. Chromosome separation does not test sequence-homology leakage.

Checksums do not establish binary readability. An optional existing reader can
check bigWig contigs and finite, nonnegative integer counts without installation:

```bash
genesis scatac model inspect --input models/TASK_ID --bigwig-python /path/to/existing/track-env/bin/python
```

Without that reader, binary validation is `NOT_ASSESSED` and overall structural
validation remains incomplete even if the requested metadata/checksum checks
finish. The report lists precisely which checks ran. No expensive new biological
metrics are computed. Raw-count declarations are not inferred from file suffixes.

| Result | Meaning |
| --- | --- |
| PASS | The named check completed; not scientific approval |
| FAIL | An observed artifact or target-contract requirement failed |
| INCOMPLETE | The stated check could not finish, for example within its scan budget |
| NOT_ASSESSED | No supported exact-bundle evidence or reader was available |
| NOT_APPLICABLE | The requirement does not apply to the pinned adapter |

| Exit | Meaning |
| --- | --- |
| 0 | Report generated without detected failure or exhausted checks; unresolved readiness and absent binary-reader evidence may remain |
| 1 | Detected structural or target-contract failure |
| 2 | Malformed/unsupported bundle, invalid invocation or unsafe output destination |
| 3 | Scan budget exhausted or dependent region checks incomplete, without a detected failure |

Failures take precedence over incomplete checks. **Exit 0 is not biological
acceptance.** `model_ready` in the report remains false, regardless of any supplied
claim. `model validate` still checks its original checksum/fold contract; neither
command grants training approval.

The historical loader scripts record shapes, counts and model-source checks, but
not a receipt binding the result to an immutable bundle version. Inspection therefore
reports loader, scientific-review and training evidence as `NOT_ASSESSED`.
An ad hoc `PASS` field, including one copied from a synthetic fixture, is not accepted.
A future receipt needs the bundle version, target/audited SHA, tested file hashes
and folds, command/environment, date, scope and result; review also needs an
accountable reviewer and exact evidence binding. Recorded evidence must still be
labelled as recorded, not independently rerun. This command does not introduce a
new receipt service or approve such records.

ChromBPNet's declared bias plan is not a fitted plant bias model. The current
Cherimoya adapter has no control track and does not inherit ChromBPNet's bias
requirement. Genome-tile backgrounds are not GC matched. scATAC TSS enrichment
is unimplemented in this product stage; filtered-source organellar absence cannot
establish original-library contamination.

## Offline inspection example

From an installed checkout, create a tiny hand-constructed fixture in a new
location. This uses Python and a packaged synthetic bigWig; it needs no network,
Docker, API key or model runtime:

```bash
uv run --frozen python examples/scatac/model_inspection_demo.py --output workspace/meeting-prep/synthetic-bundle
genesis scatac model inspect --input workspace/meeting-prep/synthetic-bundle --format markdown
```

The fixture declares `synthetic`, has one positive and one background region in
each of three chromosome folds, and remains unreviewed. The tiny track has two
unit-count bases per chromosome, generated with pyBigWig 0.3.26. This is an
inspection example, **not** the historical 900-fragment real-tool fixture, a
pipeline output, a model-loader PASS or biological evidence. Existing demos and
real-tool acceptance commands remain unchanged. Use a new output directory when
repeating fixture creation; the generator refuses overwrite.
