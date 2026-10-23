# Training the Erynne model

Erynne's answering model is **LFM2.5-2.6B fine-tuned with LoRA**, distilled
from Qwen3-8B (the teacher). It learns *behaviour*, not document content:
answering only from the given sources, citing them inline, saying "not found"
when they don't answer, rewriting follow-ups, and extracting memories. Content
stays in retrieval, so documents can change without retraining.

Everything here stays local: training data quotes confidential documents and
lives in `training/out/` (git-ignored). No hub uploads, no trackers.

## 0. Baseline first

Fine-tune only if the stock model falls short. With the gold set verified:

```sh
mnemerynne eval-answers eval/gold/dev.jsonl --generator http://127.0.0.1:8081 --label qwen3-8b --out runs/answers-qwen3-8b.json
mnemerynne eval-answers eval/gold/dev.jsonl --generator http://127.0.0.1:8083 --label lfm2.5-stock --out runs/answers-lfm2.5-stock.json
```

(8081 = Qwen3-8B teacher, 8083 = stock LFM2.5; the judge defaults to 8081.)

## 1. Generate training data (dev PC, teacher on the TITAN X)

```sh
mnemerynne gen-sft --out training/out/sft.jsonl --gold eval/gold/dev.jsonl
```

Defaults: 2000 cited answers, 500 near-miss + 200 off-topic "not found",
400 follow-up rewrites, 300 memory extractions. Each answer is generated
through the production retrieval and prompt, then kept only if it cites the
passage it came from and a judge pass finds no unsupported claims. Chunks
behind gold questions are excluded so the evaluation stays honest.

Expect most of a day on the TITAN X; the file is appended to, so you can stop
and resume.

**Using the OpenAI API as the teacher instead** (about $2 for this run with
`gpt-6-luna`, Oct 2026 pricing; finishes in under an hour): set
`MNEMERYNNE_TEACHER_FLAVOR=openai`, `MNEMERYNNE_TEACHER_API_KEY`, and
`MNEMERYNNE_TEACHER_ALLOWED_LIBRARIES` (e.g. `Oracle`, or `*` for every
library). Only those libraries' text is ever sent; nothing is sent while it
is unset. Generate examples for confidential libraries with the local
teacher in a second run. Check OpenAI's current terms on
using outputs to train models before doing this. Check a sample before training:

```sh
python -c "import json,collections;r=[json.loads(l) for l in open('training/out/sft.jsonl',encoding='utf-8')];print(collections.Counter(x['task'] for x in r))"
```

## 2. Training environment (machine with the RTX 3080)

Copy `training/` and `training/out/sft.jsonl` over (it contains document
text: use scp/USB, not cloud storage). WSL2 Ubuntu or native Linux:

```sh
python3 -m venv .venv-train && . .venv-train/bin/activate
pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu128
pip install -r training/requirements.txt
```

Optional pipeline check on CPU first (tiny random LFM2; minutes):
`python training/train_lora.py --smoke --data training/out/sft.jsonl --base <tiny-model-dir> --out /tmp/smoke`
(this was verified on the dev PC: train → merge → GGUF → llama-server).

Native Windows works too (also verified); `training/run_overnight.ps1` has
the setup commands at the top.

### Overnight, in one command

Steps 3 and 4 in one go, logged to `training/out/overnight.log`. Re-running
skips steps that already finished.

```powershell
.\training\run_overnight.ps1                 # Windows
```
```sh
training/run_overnight.sh                    # Linux / WSL2
```

Examples longer than the length limit (4096 tokens) are dropped, not cut
short: a cut-off example would teach broken JSON.

## 3. Train

```sh
python training/train_lora.py --data training/out/sft.jsonl --out training/out/erynne-lora
```

bf16 LoRA (r=16, α=32, lr 1e-4, 2 epochs, 4096 tokens, batch 1 × 16
accumulation, gradient checkpointing). Loss covers the assistant replies
only. Downloads `LiquidAI/LFM2.5-2.6B` (~5 GB) on first run. If a 10 GB card
runs out of memory: `--qlora`, or `--max-len 3072`.

## 4. Merge and export to GGUF

```sh
python training/merge_lora.py --adapter training/out/erynne-lora --out training/out/erynne-merged
git clone --depth 1 --branch b11756 https://github.com/ggml-org/llama.cpp ~/llama.cpp
cmake -S ~/llama.cpp -B ~/llama.cpp/build && cmake --build ~/llama.cpp/build --target llama-quantize -j
training/export_gguf.sh training/out/erynne-merged     # -> training/out/erynne-YYYY-MM-DD-q4_k_m.gguf
```

## 5. Evaluate and gate (dev PC)

Serve the new GGUF on a spare port, evaluate, compare with the stock model:

```sh
llama-server -m erynne-YYYY-MM-DD-q4_k_m.gguf --port 8084 -ngl 99 -c 8192 --jinja \n    --chat-template-file deploy/linux/lfm2-no-think.jinja --reasoning-format deepseek
mnemerynne eval-answers eval/gold/dev.jsonl --generator http://127.0.0.1:8084 --label mnemerynne-YYYY-MM-DD --out runs/answers-mnemerynne-YYYY-MM-DD.json
mnemerynne eval-gate runs/answers-lfm2.5-stock.json runs/answers-mnemerynne-YYYY-MM-DD.json
```

Serve every model exactly as production does. LFM2.5 and its fine-tunes think
before answering, and `eval-answers` gives them `MNEMERYNNE_LLM_REASONING_TOKENS`
of room for it, as the app does. Production serves them with
`deploy/linux/lfm2-no-think.jinja`, which opens a `<think>` block only when a
request asks: answers ask (here too), JSON calls don't, so their grammar holds.
LFM2.5's own template thinks on every request, which breaks JSON calls; and
erinne-0.1 answering without thinking refused questions it answers when it may
think, so don't evaluate answers that way.
`scripts/eval_model.sh` does this for you.

The gate fails if any metric drops more than 0.02, if faithfulness or
refusal F1 gets worse at all, or if under 98 % of answers parse. Use the
`test` split (`--split test`) for the final check before deploying.

## 6. Deploy (Fedora box)

```sh
sudo cp erynne-YYYY-MM-DD-q4_k_m.gguf /opt/models/
sudo ln -sfn /opt/models/erynne-YYYY-MM-DD-q4_k_m.gguf /opt/models/mnemerynne-current.gguf
# llama.env: GENERATOR_MODEL=/opt/models/mnemerynne-current.gguf (once)
sudo systemctl restart llama-generator
```

Roll back by pointing the symlink at the previous file and restarting.
Set `MNEMERYNNE_LLM_MODEL=mnemerynne-YYYY-MM-DD` in `/etc/mnemerynne/mnemerynne.env` so
answers record which model wrote them.
