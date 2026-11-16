# Model card

LIGHT-LAG uses three kinds of model. All run locally, with no cloud calls (see *Airplane mode*).

| | Role | Where | Model | License | Size (quantized) |
|---|---|---|---|---|---|
| **TERN** | Onboard AI: answers the Commander, explains alerts and decisions | Ship machine | `Qwen/Qwen3-4B-Instruct-2507` (Ollama tag: check `ollama list`, e.g. `qwen3:4b`) or the fine-tune `tern-capcom` | Apache-2.0 | ~2.5 GB at Q4_K_M |
| **FIDO** | Ground assistant at Lantern | Control machine | `Qwen/Qwen3-8B` (e.g. `qwen3:8b`) | Apache-2.0 | ~5 GB at Q4_K_M |
| **Anomaly AE** | Decides *when* to alert on ship telemetry | Ship machine | Conv1d autoencoder, ONNX | MIT (ours) | < 100 KB |

Alternatives with permissive licenses if needed: Phi-4-mini-instruct (3.8B, MIT), Mistral-7B-Instruct-v0.3 (Apache-2.0), Granite 3.x 8B (Apache-2.0). Avoid models with non-OSI licenses (some Llama/Gemma terms, and the Qwen2.5 3B/72B research license) if you want to stay "permissive only".

## How TERN and FIDO are used
1. The question goes to the local model with **tool definitions** (`ai/tools.py`). TERN's tools read local sensors (age 0). FIDO's tools read only what has *arrived* at Lantern, so every value carries an age ≥ one light-time.
2. **Grounding guardrail** (`ai/grounding.py`): every number in the reply must match a tool value or the user's own words (allowing display rounding and natural unit conversions). Any value from a stale record must be flagged with its age. Identifiers ("Meridian-1", "DSS-14", "X8.2") are ignored.
3. On violation: one retry with the violations quoted back to the model. Still failing: the **deterministic fallback** answers instead (`ai/fallback.py`). The debug drawer shows every tool call, attempt and verdict.
4. **The models never decide anything that matters.** The autonomy engine (`sim/autonomy.py`) and the anomaly AE decide; the LLM explains.

## Fine-tune: CAPCOM protocol (`ml/finetune/`)
**Goal:** callsigns, brevity, read-backs, GO/NO-GO, and never inventing telemetry.

**Data** (`data/training/MANIFEST.json` records every source and license):
| Source | Use | License |
|---|---|---|
| NASA JSC Mission Transcript Collection: raw technical air-to-ground transcripts (AS07_TEC, AS08_TEC, AS11_TEC …) | Register: callsigns, "Roger", "Over", brevity | NASA work (17 U.S.C. 105); NASA's ALSJ pages state "no copyright is asserted" for the raw transcripts. **The corrected Apollo Flight/Lunar Surface Journal texts are copyrighted and excluded.** |
| Synthetic tool-grounded dialogues (1,600 in this build) | Behaviour: cite tools, flag stale, refuse no-data, read back, poll | Team-generated, MIT |

Mix: up to 30% transcript pairs, 70% synthetic. Transcript fetching needs internet (`corpus.py fetch`) and `pdftotext`; it wasn't run in this build, so `sft.jsonl` currently holds the 1,600 synthetic dialogues only.

**Training:** QLoRA on one RTX 4090. 4-bit NF4 + double quantization, LoRA r=16, α=32, dropout 0.05 on all attention and MLP projections, lr 2e-4 cosine, 2 epochs, effective batch 16, max length 1024, gradient checkpointing (`train_qlora.py`). Export: merge → GGUF → Q4_K_M → Ollama (`export_gguf.py`).

## Protocol eval (`ml/finetune/eval/`)
64 prompts in 8 categories, held out from training by construction: different phrasings, values and instructions, plus two categories that never appear in training (*adversarial guess requests* and *storm brevity*).

| Check | Pass condition |
|---|---|
| callsign | opens "<addressee>, <self>." |
| over | ends with "Over" |
| grounded | no number outside tool results or the prompt |
| stale_flag | stale data comes with its age |
| refusal | no-data prompts are refused, with no numbers |
| readback | every value in an instruction is read back |
| gonogo | the poll answer matches the limits |
| mentions | the key value is actually stated |
| brevity | word limit (45; 30 for storm) |

A prompt passes only if every applicable check passes.

### Results
| Model | Overall pass | Notes |
|---|---|---|
| Rule-based reference (`--reference`) | 100% | **Harness validation only, not a model.** It copies tool values by construction. Unit tests confirm the checks reject missing callsigns, invented numbers, partial read-backs and wrong polls (`test_eval_checks_reject_bad_replies`). |
| Base `Qwen3-4B-Instruct-2507` | **pending** | Needs a GPU machine: `run_eval.py --url http://127.0.0.1:11434/v1 --model qwen3:4b --tag base` |
| Fine-tuned `tern-capcom` | **pending** | `… --model tern-capcom --tag finetuned`, then `--compare base finetuned` |

**Ship rule:** if the fine-tune doesn't beat the base on overall pass rate, ship the base and say so. `--compare` writes `reports/protocol_eval.md` and names the winner.

## Anomaly autoencoder (`ml/anomaly/`)
- **Data:** synthetic telemetry from the game's own ship model (`sim/subsystems.py`), 11 channels at 10 s cadence, 2-hour episodes. Nominal episodes include operator actions, a range of heliocentric distances and solar storms (environment, not equipment failure).
- **Model:** Conv1d encoder (11→32→32→12 channels, two stride-2 stages) and transposed-conv decoder, trained on **nominal data only** (MSE). Score = reconstruction error over the most recent 16 frames of a 64-frame window.
- **Calibration:** threshold = a high percentile of nominal validation scores. Alert persistence is tuned on the TUNE fault types only.
- **Held-out evaluation:** fault types are split into TUNE (scrubber valve stuck, pump degradation, bus short, HGA drift) and HELD-OUT (array string loss, heater stuck on, ppCO₂ sensor stuck, loop-B sensor drift), which are never seen in training or tuning. Episode-level metrics: a TP is an alert at/after onset within 60 minutes; an alert before onset or in a nominal episode is a FP.
- **Results** (`reports/anomaly_eval.md`; 480 held-out test episodes; threshold = p99.8 of nominal validation scores; alert after 12 consecutive frames = 120 s):

| group | precision | recall | TP | FP | FN |
|---|---|---|---|---|---|
| TUNE fault types | 0.939 | 0.675 | 108 | 7 | 52 |
| **HELD-OUT fault types** | **0.911** | **0.450** | 72 | 7 | 88 |
| all | 0.963 | 0.562 | 180 | 7 | 140 |

| fault | group | recall (≤ 60 min) | median latency | why |
|---|---|---|---|---|
| bus_short | tune | 1.00 | 1.8 min | voltage sag + current spikes are unlike anything nominal |
| hga_drift | tune | 1.00 | 17.9 min | link margin falls off a cliff as pointing error grows |
| pump_a_degradation | tune | 0.70 | 15.5 min | loop temperature rises gradually |
| scrubber_valve_stuck | tune | 0.00 | – | CO₂ climbs ~0.4 mmHg/h only *after* the bed saturates, so the signal is still in the noise within an hour. The limit alarm and TERN's `scrubber_cycle` rule (ppCO₂ > 4) are the right detectors for this |
| heater_stuck_on | held-out | 1.00 | 22.2 min | extra load + warmer loop: a combination never seen in nominal data |
| sensor_drift_loop_b | held-out | 0.80 | 41.2 min | slow bias ramp; caught once it leaves the nominal envelope |
| array_string_loss | held-out | 0.00 | – | an 18% power step sits inside nominal variability, because array output ranges 13–32 kW with heliocentric distance. Fix: add an *expected-solar residual* channel (r⁻² model) |
| sensor_stuck_ppco2 | held-out | 0.00 | – | a frozen sensor *removes* noise, so reconstruction error goes **down**. Fix: a rolling-variance channel or a flatline rule |

  False alarms: 7 of 160 nominal episodes (4.4%) over 2 hours each. The game rate-limits alerts to one per 30 minutes of universe time.
  Takeaway, and the reason the architecture is layered: **the autoencoder catches novel multi-channel failures; limit rules catch slow single-channel ones.** TERN uses both.
- **Runtime:** onnxruntime on the ship machine, one inference per telemetry frame. Paused during time warp, because frames would no longer be 10 s apart. Fallback: robust z-score detector, labelled in the UI.
- **Caveat:** the numbers describe performance on our simulator, not on a spacecraft.

## Airplane mode
- The LLM client refuses any endpoint that isn't loopback or a private LAN address unless `LL_ALLOW_REMOTE_LLM=1`.
- `/api/health` and `tools/preflight.py` report whether the machine can reach the public internet. For the demo, unplug the network and show the badge reading *offline*, with TERN still answering.
- Voice (whisper.cpp, Piper) is local binaries only; the browser fallback uses `speechSynthesis` with `localService` voices.

## Known limitations
- Small models sometimes skip the callsign or "Over" under pressure; the guardrail catches invented numbers, not style. The eval measures style.
- Tool calling varies by server: Ollama and `llama-server --jinja` both support OpenAI-style `tools`; older builds may not.
- Qwen3 hybrid-thinking models can emit `<think>` blocks. We add `/no_think` and strip them; the 2507 *Instruct* variant is non-thinking.
