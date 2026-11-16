"""Merge the LoRA adapter, convert to GGUF, quantize, and register with Ollama.

Requires a llama.cpp checkout (for convert_hf_to_gguf.py and llama-quantize):
    python ml/finetune/export_gguf.py --base Qwen/Qwen3-4B-Instruct-2507 \
        --adapter runs/tern-capcom --name tern-capcom --llama_cpp ~/src/llama.cpp
Then:  ollama run tern-capcom   and set LL_TERN_MODEL=tern-capcom
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

MODELFILE = """FROM ./{gguf}
PARAMETER temperature 0.2
PARAMETER num_ctx 4096
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--name", default="tern-capcom")
    ap.add_argument("--llama_cpp", default=str(Path.home() / "src" / "llama.cpp"))
    ap.add_argument("--quant", default="Q4_K_M")
    a = ap.parse_args()
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    out = Path(a.adapter) / "merged"
    base = AutoModelForCausalLM.from_pretrained(a.base, torch_dtype=torch.bfloat16)
    PeftModel.from_pretrained(base, a.adapter).merge_and_unload().save_pretrained(out)
    AutoTokenizer.from_pretrained(a.base).save_pretrained(out)
    f16 = Path(a.adapter) / f"{a.name}-f16.gguf"
    q = Path(a.adapter) / f"{a.name}-{a.quant}.gguf"
    subprocess.run(["python", str(Path(a.llama_cpp) / "convert_hf_to_gguf.py"), str(out), "--outfile", str(f16),
                    "--outtype", "f16"], check=True)
    subprocess.run([str(Path(a.llama_cpp) / "build" / "bin" / "llama-quantize"), str(f16), str(q), a.quant], check=True)
    (Path(a.adapter) / "Modelfile").write_text(MODELFILE.format(gguf=q.name))
    subprocess.run(["ollama", "create", a.name, "-f", str(Path(a.adapter) / "Modelfile")], check=True, cwd=a.adapter)
    print("ollama model created:", a.name)


if __name__ == "__main__":
    main()
