"""QLoRA fine-tune on one RTX 4090 (24 GB).

Default base: Qwen/Qwen3-4B-Instruct-2507 (Apache-2.0; non-thinking chat
model with native tool calling). Also tested config-wise for 7-8B models
(Qwen/Qwen3-8B, Apache-2.0) - lower the batch size.

    pip install -r ml/finetune/requirements.txt
    python ml/finetune/corpus.py synth && python ml/finetune/corpus.py build
    python ml/finetune/train_qlora.py --base Qwen/Qwen3-4B-Instruct-2507 --out runs/tern-capcom
    python ml/finetune/export_gguf.py --base ... --adapter runs/tern-capcom --name tern-capcom

Expected on a 4090: ~1,600-2,300 examples x 2 epochs, 4-bit NF4, r=16
-> roughly 15-30 minutes, ~10-12 GB VRAM for the 4B model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="Qwen/Qwen3-4B-Instruct-2507")
    ap.add_argument("--data", default=str(ROOT / "data" / "training" / "sft.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "runs" / "tern-capcom"))
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--r", type=int, default=16)
    ap.add_argument("--bs", type=int, default=4)
    ap.add_argument("--accum", type=int, default=4)
    ap.add_argument("--max_len", type=int, default=1024)
    a = ap.parse_args()

    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    tok = AutoTokenizer.from_pretrained(a.base)
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                             bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(a.base, quantization_config=bnb, torch_dtype=torch.bfloat16,
                                                 device_map="auto", attn_implementation="sdpa")
    lora = LoraConfig(r=a.r, lora_alpha=2 * a.r, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    ds = load_dataset("json", data_files=a.data, split="train")
    ds = ds.remove_columns([c for c in ds.column_names if c != "messages"])
    cfg = SFTConfig(output_dir=a.out, num_train_epochs=a.epochs, per_device_train_batch_size=a.bs,
                    gradient_accumulation_steps=a.accum, learning_rate=a.lr, lr_scheduler_type="cosine",
                    warmup_ratio=0.03, logging_steps=10, save_strategy="epoch", bf16=True,
                    gradient_checkpointing=True, max_length=a.max_len, assistant_only_loss=False, report_to=[])
    trainer = SFTTrainer(model=model, args=cfg, train_dataset=ds, peft_config=lora, processing_class=tok)
    trainer.train()
    trainer.save_model(a.out)
    Path(a.out, "lightlag_train_args.json").write_text(json.dumps(vars(a), indent=1))
    print("saved adapter to", a.out)


if __name__ == "__main__":
    main()
