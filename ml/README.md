# ML

Full details, licenses and results: [`docs/model-card.md`](../docs/model-card.md).

## Anomaly model (CPU, ~1 minute)
```bash
pip install -r ml/requirements.txt
python ml/anomaly/generate.py        # 960 synthetic 2-hour episodes from the game's ship model
python ml/anomaly/train.py           # train Conv1d AE, calibrate, held-out eval, export ONNX
```
Outputs `data/models/anomaly.onnx` (+ `.onnx.data`, `.json`) and `reports/anomaly_eval.{json,md}`. The server picks the model up at start; delete it to fall back to the z-score detector.

## Protocol fine-tune (RTX 4090)
```bash
pip install -r ml/finetune/requirements.txt
python ml/finetune/corpus.py fetch && python ml/finetune/corpus.py parse   # NASA JSC raw transcripts (needs internet + pdftotext)
python ml/finetune/corpus.py synth && python ml/finetune/corpus.py build   # + synthetic tool-grounded dialogues -> sft.jsonl
python ml/finetune/train_qlora.py --base Qwen/Qwen3-4B-Instruct-2507 --out runs/tern-capcom
python ml/finetune/export_gguf.py --base Qwen/Qwen3-4B-Instruct-2507 --adapter runs/tern-capcom --name tern-capcom
```

## Eval (base vs fine-tuned)
```bash
python ml/finetune/eval/run_eval.py --reference                                         # harness check
python ml/finetune/eval/run_eval.py --url http://127.0.0.1:11434/v1 --model qwen3:4b    --tag base
python ml/finetune/eval/run_eval.py --url http://127.0.0.1:11434/v1 --model tern-capcom --tag finetuned
python ml/finetune/eval/run_eval.py --compare base finetuned                            # -> reports/protocol_eval.md
```
If the fine-tune doesn't beat the base, set `LL_TERN_MODEL` back to the base and say so in the README.
