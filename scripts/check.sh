#!/usr/bin/env bash
# Metadata-only offline verification. Live provider checks run separately.
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
run=(uv run --frozen --extra web --project "$root")
"${run[@]}" ruff check "$root/src" "$root/tests" "$root/examples/curation"
"${run[@]}" ruff format --check "$root/src" "$root/tests" "$root/examples/curation"
"${run[@]}" ty check --project "$root" "$root/src"
"${run[@]}" ty check --project "$root" --extra-search-path "$root/tests" "$root/tests"
for test in verify_v1 verify_local_models verify_model_manager verify_release_results verify_workspace \
    verify_web verify_study_review verify_study_web verify_table_web verify_trustworthy_web verify_mining \
    verify_ai verify_llm_deployment evaluate_metadata; do
    "${run[@]}" python "$root/tests/$test.py"
done
