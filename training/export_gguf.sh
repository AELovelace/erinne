#!/usr/bin/env bash
# Merged HF checkpoint -> GGUF -> Q4_K_M, named erinne-YYYY-MM-DD-q4_k_m.gguf.
#   training/export_gguf.sh training/out/erinne-merged
# Needs a llama.cpp checkout (for convert_hf_to_gguf.py) and a built
# llama-quantize, at the same build the servers run (b11756) or newer.
set -euo pipefail

MERGED=${1:?usage: export_gguf.sh <merged-model-dir>}
LLAMA_SRC=${LLAMA_SRC:-$HOME/llama.cpp}
QUANTIZE=${QUANTIZE:-$LLAMA_SRC/build/bin/llama-quantize}
OUT_DIR=${OUT_DIR:-training/out}
NAME="erinne-$(date +%F)"

if [ ! -f "$LLAMA_SRC/convert_hf_to_gguf.py" ]; then
  echo "Clone llama.cpp first: git clone --depth 1 --branch b11756 https://github.com/ggml-org/llama.cpp $LLAMA_SRC" >&2
  exit 1
fi

python "$LLAMA_SRC/convert_hf_to_gguf.py" "$MERGED" --outtype bf16 --outfile "$OUT_DIR/$NAME-bf16.gguf"
"$QUANTIZE" "$OUT_DIR/$NAME-bf16.gguf" "$OUT_DIR/$NAME-q4_k_m.gguf" Q4_K_M
sha256sum "$OUT_DIR/$NAME-q4_k_m.gguf"
echo "Next: evaluate it (training/README.md, step 5) before deploying."
