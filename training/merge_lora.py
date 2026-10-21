"""Merge a LoRA adapter into the base model and save a full HF checkpoint.

    python training/merge_lora.py --adapter training/out/erynne-lora --out training/out/erynne-merged
"""

from __future__ import annotations

import argparse

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

from train_lora import BASE


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--adapter", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--base", default=BASE)
    args = p.parse_args()

    base = AutoModelForCausalLM.from_pretrained(args.base, dtype=torch.bfloat16)
    merged = PeftModel.from_pretrained(base, args.adapter).merge_and_unload()
    merged.save_pretrained(args.out, safe_serialization=True)
    AutoTokenizer.from_pretrained(args.adapter).save_pretrained(args.out)
    print(f"Merged model saved to {args.out}")


if __name__ == "__main__":
    main()
