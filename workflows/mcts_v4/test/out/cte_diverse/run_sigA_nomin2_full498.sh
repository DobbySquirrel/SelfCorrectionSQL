#!/usr/bin/env bash
# Back-compat wrapper: Jun 2026 full498 benchmark used MODEL_TAG=abl5_sigA_nomin2_full498.
# Production default is run_sigA_full498.sh (nomin2 / min_subq=1 via bprime_env).
export MODEL_TAG="${MODEL_TAG:-abl5_sigA_nomin2_full498}"
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_sigA_full498.sh" "$@"
