#!/usr/bin/env bash
# Run all offline checks. The CLI launcher checks run only with GENESIS_REPO set to a
# genesis-preprocessing checkout; tests needing live services (tests/accept_*_live.py)
# are run separately.
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
run=(uv run --frozen --extra web --project "$root")
"${run[@]}" ruff check "$root/src" "$root/tests" "$root/examples/curation"
"${run[@]}" ruff format --check "$root/src" "$root/tests" "$root/examples/curation"
"${run[@]}" ty check --project "$root" "$root/src"
"${run[@]}" ty check --project "$root" --extra-search-path "$root/tests" "$root/tests"
for test in verify_publication verify_benchmark verify_benchmark_spec verify_benchmark_methods \
    verify_benchmark_references verify_execution verify_cli verify_curation verify_curation_artifacts \
    verify_curation_adapters verify_celltype verify_local_models verify_model_manager verify_release_results verify_workspace verify_web verify_study_web verify_table_web verify_trustworthy_web verify_mining \
    verify_ai verify_llm_deployment evaluate_metadata \
    verify_scatac_inventory verify_scatac_exports verify_scatac_workflow_import \
    verify_scatac_inspection verify_macs3_launcher; do
    "${run[@]}" python "$root/tests/$test.py"
done
for check in check_reference_comparison check_sra_sampling check_queue_stop; do
    "${run[@]}" python "$root/benchmarks/experiments/reference-comparison-20261014/$check.py"
done
