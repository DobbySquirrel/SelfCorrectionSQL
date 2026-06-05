#!/usr/bin/env bash
# R1 taxonomy in detached screen (pure read, ~1–2 min)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SESSION="recall_r1_taxonomy"
LOG="${DIR}/r1_taxonomy.log"

screen -S "${SESSION}" -X quit 2>/dev/null || true
screen -dmS "${SESSION}" bash -c "
  set -euo pipefail
  cd '${DIR}'
  echo '[start]' \"\$(date -Iseconds)\" | tee '${LOG}'
  PYTHONUNBUFFERED=1 python3 recall_lost_taxonomy.py 2>&1 | tee -a '${LOG}'
  echo '[done]' \"\$(date -Iseconds)\" | tee -a '${LOG}'
"

echo "Started screen session: ${SESSION}"
echo "  tail -f ${LOG}"
echo "  screen -r ${SESSION}"
echo "  screen -ls | grep ${SESSION}"
