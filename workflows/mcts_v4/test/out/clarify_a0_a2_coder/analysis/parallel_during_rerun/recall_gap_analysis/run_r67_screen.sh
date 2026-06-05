#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
SESSION="selector_r67_replay"
LOG="${DIR}/selector_r67.log"
screen -S "${SESSION}" -X quit 2>/dev/null || true
screen -dmS "${SESSION}" bash -c "
  set -euo pipefail
  cd '${DIR}'
  echo '[start]' \"\$(date -Iseconds)\" | tee '${LOG}'
  PYTHONUNBUFFERED=1 python3 selector_r67_replay.py 2>&1 | tee -a '${LOG}'
  echo '[done]' \"\$(date -Iseconds)\" | tee -a '${LOG}'
"
echo "screen: ${SESSION}  log: ${LOG}"
echo "  tail -f ${LOG}"
