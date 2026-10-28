# AGENTS.md

## Project overview
Python tooling for the Genesis project that sits around the processing pipelines:
metadata/LLM extraction, curation and review, QC policies, record contracts and the
registry, job execution (the `genesis` command), scATAC, and method benchmarks.
The Nextflow pipelines live and run in
[genesis-preprocessing](https://github.com/kundajelab/genesis-preprocessing); this
repository does not launch them. `genesis benchmark` takes that checkout from
`GENESIS_REPO`. Pipeline logic belongs there, not here.

The project is managed by uv (`uv sync --frozen`). `benchmarks/environment/` is a separate
Pixi workspace for the benchmark tools.

## Coding standards
- Follow Python best practices and use type annotations.
- Follow bash script best practices.
- Prefer type narrowing or runtime validation. Use `typing.cast` when the type is already
  established but the checker cannot infer it. Avoid blanket ignores.

## Verification
- Add focused tests for changed behavior, using the existing test scripts in `tests/`.
  Ask before adding a test framework, or tests that need external services or substantial
  resources.
- Run `scripts/check.sh` before completing a code change (set `GENESIS_REPO` to also run
  the launcher checks). If it is not run, say why and list the checks that were run.
- Do not weaken assertions or suppress diagnostics to pass checks.

## Boundaries
- Keep credentials, private data, machine paths and assistant configuration out of Git.
- Benchmark comparisons never change pipeline defaults.
- Avoid adding new dependencies.
