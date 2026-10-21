"""Fine-tune LFM2.5-2.6B into the Erynne model with LoRA (bf16) on one GPU.

    python training/train_lora.py --data training/out/sft.jsonl --out training/out/erynne-lora

Expects the chat examples written by `mnemerynne gen-sft`, converted to TRL's
conversational prompt/completion format so the loss covers only the
assistant's reply (not the prompt or the quoted sources). Designed for a 10 GB RTX 3080: bf16 weights (~5.2 GB),
gradient checkpointing, batch 1 x 16 accumulation. Pass --qlora if that runs
out of memory (needs bitsandbytes).

Nothing leaves the machine: no hub uploads, report_to=none.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("WANDB_DISABLED", "true")

import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from peft import LoraConfig  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402

BASE = "LiquidAI/LFM2.5-2.6B"
# LFM2 blocks: attention (q/k/v/out_proj), short convolutions (in_proj/out_proj)
# and SwiGLU feed-forward (w1/w2/w3). The script checks these exist.
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "out_proj", "in_proj", "w1", "w2", "w3"]


def load_examples(path: Path, eval_ratio: float, seed: int) -> tuple[list[dict], list[dict]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rng = random.Random(seed)
    rng.shuffle(rows)
    n_eval = max(1, int(len(rows) * eval_ratio))
    def to_example(r):
        *prompt, reply = r["messages"]
        assert reply["role"] == "assistant", "last message must be the assistant's reply"
        return {"prompt": prompt, "completion": [reply]}

    return [to_example(r) for r in rows[n_eval:]], [to_example(r) for r in rows[:n_eval]]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--base", default=BASE, help="HF model id or local path")
    p.add_argument("--epochs", type=float, default=2.0)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--max-len", type=int, default=4096)
    p.add_argument("--grad-accum", type=int, default=16)
    p.add_argument("--qlora", action="store_true")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--smoke", action="store_true",
                   help="CPU, fp32, 2 steps: check the pipeline on a tiny model, not real training")
    args = p.parse_args()

    if not args.smoke:
        if not torch.cuda.is_available():
            raise SystemExit("No CUDA GPU visible.")
        if not torch.cuda.is_bf16_supported():
            raise SystemExit("This GPU has no bf16 support (Pascal or older); use the RTX 3080.")

    train, evals = load_examples(Path(args.data), eval_ratio=0.03, seed=args.seed)
    tokenizer = AutoTokenizer.from_pretrained(args.base)
    # TRL truncates over-long examples from the end, which would teach cut-off
    # JSON; drop them instead.
    def fits(ex):
        text = tokenizer.apply_chat_template(ex["prompt"] + ex["completion"], tokenize=False)
        return len(tokenizer(text, add_special_tokens=False)["input_ids"]) <= args.max_len
    kept = [ex for ex in train if fits(ex)]
    print(f"{len(kept)} training examples ({len(train) - len(kept)} longer than {args.max_len} tokens dropped)")
    train, evals = kept, [ex for ex in evals if fits(ex)]

    quant = None
    if args.qlora:
        from transformers import BitsAndBytesConfig
        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                   bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(
        args.base, dtype=torch.float32 if args.smoke else torch.bfloat16,
        quantization_config=quant, device_map=None if args.smoke else {"": 0})

    present = {name.rsplit(".", 1)[-1] for name, _ in model.named_modules()}
    targets = [m for m in TARGET_MODULES if m in present]
    missing = sorted(set(TARGET_MODULES) - set(targets))
    print(f"LoRA targets: {targets}" + (f" (not in this model: {missing})" if missing else ""))

    config = SFTConfig(
        output_dir=args.out,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=0.03,          # transformers 5: a float < 1 is a ratio of total steps
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.grad_accum,
        gradient_checkpointing=not args.smoke,
        bf16=not args.smoke,
        use_cpu=args.smoke,
        max_steps=2 if args.smoke else -1,
        max_length=args.max_len,
        completion_only_loss=True,  # learn the answers, not the prompts/sources
        logging_steps=10,
        eval_strategy="steps",
        eval_steps=100,
        save_strategy="steps",
        save_steps=200,
        save_total_limit=2,
        report_to="none",
        push_to_hub=False,
        seed=args.seed,
    )
    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=Dataset.from_list(train),
        eval_dataset=Dataset.from_list(evals),
        processing_class=tokenizer,
        peft_config=LoraConfig(r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05,
                               target_modules=targets, task_type="CAUSAL_LM"),
    )
    trainer.train()
    trainer.save_model(args.out)
    tokenizer.save_pretrained(args.out)
    print(f"LoRA adapter saved to {args.out}")


if __name__ == "__main__":
    main()
