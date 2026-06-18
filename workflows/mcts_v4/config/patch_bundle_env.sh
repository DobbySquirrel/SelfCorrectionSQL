#!/usr/bin/env bash
# Optional patch bundle for today's experiments (source after bprime_env.sh).
#
#   MCTS_EXEC_TIME_TIEBREAK=1       — cluster tie-break by execution time
#   MCTS_DEDUP_BEFORE_REVISE=1      — normalize-SQL dedup before structural dedupe
#   MCTS_REVERSED_SCHEMA_LINKING=1  — 5th Mode C branch: schema from prior rollout SQL
#   MCTS_FK_PK_CLOSURE=1            — merge reversed tables + FK/PK closure on 0.9 linking

export MCTS_EXEC_TIME_TIEBREAK="${MCTS_EXEC_TIME_TIEBREAK:-1}"
export MCTS_EXEC_TIME_REPEATS="${MCTS_EXEC_TIME_REPEATS:-2}"
export MCTS_DEDUP_BEFORE_REVISE="${MCTS_DEDUP_BEFORE_REVISE:-1}"
export MCTS_REVERSED_SCHEMA_LINKING="${MCTS_REVERSED_SCHEMA_LINKING:-1}"
export MCTS_FK_PK_CLOSURE="${MCTS_FK_PK_CLOSURE:-1}"
