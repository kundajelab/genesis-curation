# MACS3 version comparison

This recipe compares released MACS3 3.0.5 with upstream commit
`22eb40f7b51f5b20b635614ca0b1759dc5398c5c`. It measures peak calling on existing
alignment files; it does not download reads or repeat alignment.

Use separate environments with identical Python, NumPy, SciPy and other runtime
versions. Install the candidate from Git at the full revision, including its
pinned SIMDe submodule. A GitHub source archive alone omits that submodule.
The candidate Pixi environment is in `environments/macs3-optimized/`.

## Reproduce the native build pair

The observed native runs used Python 3.12.15, GCC 13 and the exact shared packages
in `runtime-requirements.txt`. With that Python already installed and a compiler,
zlib headers and Git available, create two isolated environments:

```bash
export TMPDIR="$PWD/workspace/macs3/tmp"
export UV_CACHE_DIR="$PWD/workspace/macs3/uv-cache"
mkdir -p "$TMPDIR"
uv venv --no-python-downloads --python python3.12 workspace/macs3/baseline
uv venv --no-python-downloads --python python3.12 workspace/macs3/candidate
uv pip install --python workspace/macs3/baseline/bin/python \
  -r benchmarks/experiments/macs3-optimization/runtime-requirements.txt
uv pip install --python workspace/macs3/candidate/bin/python \
  -r benchmarks/experiments/macs3-optimization/runtime-requirements.txt
uv pip install --python workspace/macs3/baseline/bin/python \
  --no-deps --no-build-isolation --no-binary macs3 'macs3==3.0.5'
uv pip install --python workspace/macs3/candidate/bin/python \
  --no-deps --no-build-isolation \
  'git+https://github.com/macs3-project/MACS.git@22eb40f7b51f5b20b635614ca0b1759dc5398c5c'
```

These commands fetch packages and source, but no sequencing data. Record the
actual Python/compiler versions when reproducing on another host; a different
build is a new measurement. The separate Pixi lock reproduces the container
build environment, which is distinct from this matched native build pair.

## Run and report comparisons

The input JSON contains `cases`, each with a unique `id`, `argv` beginning with
`callpeak`, and an `inputs` object mapping absolute filenames to SHA-256 digests.
Record `scope`, `source_receipt` and `source_receipt_sha256` for each case.
Supply absolute paths to the two environments' Python executables and a new
output directory. Historical paths must be translated using the local migration
map in a new input manifest; do not rewrite archived evidence.

```bash
uv run --frozen python \
  benchmarks/experiments/macs3-optimization/compare.py \
  --inputs /path/to/comparison-inputs.json \
  --baseline /path/to/baseline/bin/python \
  --candidate /path/to/candidate/bin/python \
  --out /path/to/new-comparison
```

The runner uses the Genesis Python environment; each caller uses its own Python 3.12
environment. The runner checks input digests, then runs both versions at equal allocations of
1, 2 and 8 CPUs. Each combination has one warm-up and three measured repetitions,
with alternating version order. Linux CPU affinity bounds MACS3's workers;
numerical-library threads are limited to one. Inputs are read before timing and
system caches are not flushed. Affinity is not exclusive reservation of a host.

The budget is eight hours overall, 30 minutes per call, a sampled process-group
RSS limit of 16 GiB and 32 GiB of output. RSS sampling occurs every 50 ms and can
count shared pages more than once; it is neither a hard cgroup memory limit nor
a measurement of unique physical memory. `/usr/bin/time` also records CPU time
and maximum process RSS. Exceeding a budget produces INCOMPLETE.

Scientific equality requires identical narrowPeak and summit bytes and identical
XLS content after removing comment headers. Version, command-path and date
headers are not scientific measurements. Zero peaks are retained as a result.
Speedup is the baseline median divided by the candidate median, measured at the
same CPU allocation. It is not a whole-pipeline speedup.

`fixtures.py` checks BEDPE pseudobulks, duplicate policies, zero peaks, empty and
truncated inputs. Both upstream versions coerce nonnumeric BEDPE coordinates via
`atoi`; the regression records this behavior without certifying input validity.
Genesis must validate fragments before invoking the caller.

`report.py` renders a TSV table, sanitized JSON evidence and SVG/PNG figures from
a completed comparison. It requires Matplotlib, available in the existing
benchmark environment. It never turns an incomplete comparison into a pass.
