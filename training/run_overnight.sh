#!/usr/bin/env bash
# Overnight fine-tune of the Erynne model on Linux/WSL with an RTX 3080:
# train LoRA -> merge -> GGUF -> Q4_K_M. Logs to training/out/overnight.log;
# re-running skips steps whose output exists.
#
# One-time setup:
#   python3 -m venv .venv-train
#   .venv-train/bin/pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu128
#   .venv-train/bin/pip install -r training/requirements.txt
#   git clone --depth 1 --branch b11756 https://github.com/ggml-org/llama.cpp ~/llama.cpp
#   cmake -S ~/llama.cpp -B ~/llama.cpp/build && cmake --build ~/llama.cpp/build --target llama-quantize -j
set -euo pipefail
cd "$(dirname "$0")/.."

DATA=${DATA:-training/out/sft.jsonl}
PY=${PY:-.venv-train/bin/python}
LLAMA_SRC=${LLAMA_SRC:-$HOME/llama.cpp}
QUANTIZE=${QUANTIZE:-$LLAMA_SRC/build/bin/llama-quantize}
MAX_LEN=${MAX_LEN:-4096}   # out of GPU memory? MAX_LEN=3072 (drops ~1% of examples)
EPOCHS=${EPOCHS:-2}
OUT=training/out
LOG=$OUT/overnight.log
NAME="erynne-$(date +%F)"
export HF_HUB_DISABLE_TELEMETRY=1

step() { echo "[$(date +%T)] === $1" | tee -a "$LOG"; shift; "$@" 2>&1 | tee -a "$LOG"; }

for p in "$DATA" "$PY" "$LLAMA_SRC/convert_hf_to_gguf.py" "$QUANTIZE"; do
  [ -e "$p" ] || { echo "Missing: $p (see setup notes at the top)" >&2; exit 1; }
done
echo "[$(date +%T)] Starting: $(wc -l < "$DATA") examples" | tee -a "$LOG"

[ -f "$OUT/erynne-lora/adapter_model.safetensors" ] || \
  step "Train LoRA" "$PY" training/train_lora.py --data "$DATA" --out "$OUT/erynne-lora" --max-len "$MAX_LEN" --epochs "$EPOCHS"
[ -f "$OUT/erynne-merged/config.json" ] || \
  step "Merge" "$PY" training/merge_lora.py --adapter "$OUT/erynne-lora" --out "$OUT/erynne-merged"
[ -f "$OUT/$NAME-bf16.gguf" ] || \
  step "Convert to GGUF" "$PY" "$LLAMA_SRC/convert_hf_to_gguf.py" "$OUT/erynne-merged" --outtype bf16 --outfile "$OUT/$NAME-bf16.gguf"
step "Quantise Q4_K_M" "$QUANTIZE" "$OUT/$NAME-bf16.gguf" "$OUT/$NAME-q4_k_m.gguf" Q4_K_M
echo "[$(date +%T)] Done: $OUT/$NAME-q4_k_m.gguf  sha256 $(sha256sum "$OUT/$NAME-q4_k_m.gguf" | cut -d' ' -f1)" | tee -a "$LOG"
echo "Next: evaluate it against the stock model (training/README.md, step 5)." | tee -a "$LOG"
