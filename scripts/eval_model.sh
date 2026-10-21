#!/usr/bin/env bash
# Evaluate a fine-tuned GGUF against stock LFM2.5 on the gold set, then gate.
# Each model is served in turn on :8084; the judge (Qwen3-8B on :8081) and the
# embedder (:8082) must already be running.
#
#   scripts/eval_model.sh <new.gguf> [label]
set -euo pipefail
cd "$(dirname "$0")/.."

NEW=$1
LABEL=${2:-$(basename "$NEW" .gguf)}
STOCK=${STOCK:-C:/llama.cpp/models/LFM2.5-2.6B-Q4_K_M.gguf}
SERVER=${SERVER:-C:/llama.cpp/bin/llama-server.exe}
PY=.venv/Scripts/python
PORT=8084
mkdir -p runs

run() {  # gguf label
  # Same template and reasoning setting as production (deploy/linux/mnemerynne-generator.service):
  # LFM2.5's own template forces a <think> block that fine-tuning never trains.
  "$SERVER" -m "$1" --port $PORT -ngl 99 -c 8192 --jinja --chat-template-file deploy/linux/lfm2-no-think.jinja \
    --reasoning-format none > "runs/server-$2.log" 2>&1 &
  local pid=$!
  until curl -sf http://127.0.0.1:$PORT/health > /dev/null; do
    kill -0 $pid 2> /dev/null || { echo "llama-server failed; see runs/server-$2.log" >&2; exit 1; }
    sleep 2
  done
  PYTHONIOENCODING=utf-8 $PY -m mnemerynne eval-answers eval/gold/dev.jsonl --generator http://127.0.0.1:$PORT \
    --judge http://127.0.0.1:8081 --label "$2" --out "runs/answers-$2.json"
  kill $pid; wait $pid 2> /dev/null || true
}

# v3: measured without the forced <think> (v2 and earlier were served with it).
[ -f "runs/answers-lfm2.5-stock-v3.json" ] || run "$STOCK" lfm2.5-stock-v3
run "$NEW" "$LABEL"
$PY -m mnemerynne eval-gate runs/answers-lfm2.5-stock-v3.json "runs/answers-$LABEL.json"
