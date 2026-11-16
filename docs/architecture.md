# Architecture

## Principles
1. **One authoritative universe.** All physics, comms and story run in one Python process. Clients are views; they never simulate.
2. **Causality is sacred.** Every cross-space effect is an event scheduled at `transmit_time + light_time`. Nothing arrives early, not even in demo mode.
3. **Two knowledge stores.** The server holds truth, *what Lantern knows* and *what Meridian knows* separately, and sends each client only its own view.
4. **Graceful degradation.** Every external dependency (kernels, LLMs, whisper, Piper, DONKI, DSN Now, ONNX) has a labelled fallback. The demo cannot fail on a missing download.
5. **Engine-agnostic.** The WebSocket API is plain JSON. The web clients, a Godot port and a UE5 cinematic client are all just subscribers.

## Process layout
```
python run.py
 └─ uvicorn (FastAPI, asyncio)
     ├─ Game.loop()      10 Hz: Universe.tick(dt) → drain events → push to sockets; 4 Hz: per-role snapshots
     ├─ Game.dsn_loop()  10 s: DSN Now refresh (if online) + airplane-mode check
     ├─ /ws/{role}       control | ship | spectator | observer
     ├─ /api/*           porkchop, trajectory, after-action, provenance, science cards, model card, voice
     └─ threads          LLM calls (asyncio.to_thread); every tool call re-enters under Game.lock
```
The universe is single-threaded; `Game.lock` (an RLock) protects it from concurrent tool reads by LLM worker threads, so a slow model never stalls the simulation.

## Module map (`server/lightlag/`)
| Module | Responsibility |
|---|---|
| `config.py` | All env knobs (`LL_*`). `time_rate = 1/DELAY_SCALE` |
| `astro/timescale.py` | UTC ↔ ET (SPICE or IERS leap table) |
| `astro/ephemeris.py` | `EphemerisService`: SPICE or Kepler backend; `state`, `light_time` (XCN/CN), SEP/SPE, ecliptic projection |
| `astro/kepler.py` | Standish elements, Kepler's equation, universal-variable propagator |
| `astro/lambert.py` | Universal-variable Lambert solver |
| `astro/trajectory.py` | `ShipTrajectory` (cruise conic + Mars ellipse) and `write_spk` (type 13) |
| `astro/porkchop.py` | Porkchop grid, Δv, mass model |
| `astro/conjunction.py` | SEP-threshold window finder with bisection refinement |
| `astro/dsn.py` | Stations, elevation, visibility, plan-based pass scheduling |
| `comms/bundle.py` | `Bundle`, `BundleStore` (priority heap, expiry, stream limits) |
| `comms/contact_plan.py` | `LinkState`: passes (with uplink/downlink offset), blackout, moratorium, occultation, margin, data rate |
| `comms/network.py` | Transmit at link rate, schedule arrivals at `tx + LT`, status reports, moratorium hold |
| `sim/subsystems.py` | Ship model + faults + operator actions + telemetry frames + limits |
| `sim/radiation.py` | GCR + SEP dose, shielding, attitude, Forbush, coast integration |
| `sim/space_weather.py` | DONKI cache / literature events → `StormTimeline` (Parker spiral, connectivity) |
| `sim/autonomy.py` | Policy, decisions, requests, escalation, decision log |
| `sim/director.py` | Scenes, beats, objectives, prompts, demo-mode cues, flood detection |
| `sim/universe.py` | The universe: clock, events, physics step, knowledge stores, views, actions |
| `sim/scoring.py` | Report card |
| `sim/design.py` | The design bay: four budgets, bands, ripple, the degraded power case |
| `sim/crew.py`, `sim/science.py` | Crew AI; science data in flight |
| `sim/review.py` | Flight Readiness Review: five deterministic reviewers over `design_eval()` (thresholds in `data/review_board.yaml`) |
| `sim/callouts.py` | Design-to-flight callouts: triggers, MODEL counterfactuals, ship and Earth knowledge, one per scene per screen |
| `sim/history.py` | Bounded flight time series (≤ 400 points) for the debrief |
| `sim/debrief.py` | Cause-and-effect debrief: chains, timeline, crew, science, fly it again |
| `sim/factsheet.py` | Mission Fact Sheet data (`/api/factsheet`, page `/factsheet/`) |
| `ai/tools.py` | TERN/FIDO tools (local truth vs. delayed knowledge) |
| `ai/grounding.py` | Numeric grounding + staleness guardrail |
| `ai/llm.py` | OpenAI-compatible local client, loopback/LAN-only by default, airplane check |
| `ai/agents.py` | LLM → tools → grounding → retry → fallback pipeline with full trace |
| `ai/fallback.py` | Deterministic CAPCOM-style responder |
| `ai/anomaly_runtime.py` | ONNX autoencoder (or z-score fallback), persistence, top channels |
| `voice/radio.py` | ffmpeg decode, whisper.cpp STT, Piper TTS, radio filter + Quindar |
| `data_sources.py` | DSN Now fetch/parse/cache |
| `api/app.py` | FastAPI app, WebSocket protocol, REST helpers |

## The clock
```
wall dt  ──×rate──▶  universe dt  ──substeps──▶  physics (ship, dose, network, telemetry)
                                    ▲
                     next event in the heap caps each substep, so events fire at their exact ET
```
`rate` = base (1/DELAY_SCALE) × scene factor (demo-mode compression, blackout warp, storm warp) or a temporary slowdown when TERN needs the crew. **Coasts** between scenes jump time but still respect physics: queued bundles go out when the link allows (never through the blackout, never commands in a moratorium), every arrival waits its light time, and GCR and SEP dose are integrated across the skip.

## Event queue
A min-heap of `(et, seq, kind, data)`. Kinds: `bundle_arrival`, `flare_earth`, `flare_ship`, `protons_earth`, `protons_ship`, `storm_peak`, `cme_ship`, `director` (beats, faults, cues). Telemetry is generated on a cadence (10 universe-seconds at normal speed) and is itself a stream of bulk bundles.

## Knowledge stores
| Store | Updated by | Read by |
|---|---|---|
| `design_record` (frozen at launch) | `launch()` only | callouts, debrief, fact sheet |
| `callouts.shipk` / `callouts.earthk` | callouts at event time / `callout` bundles on arrival | ship seats / Earth seats |
| Truth (`Universe` + `ShipSystems`) | physics | spectator/observer, tests |
| `ctrl` (Lantern) | arriving downlink bundles only | `/ws/control`, FIDO tools |
| `shipk` + local sensors (Meridian) | local frames + arriving uplink bundles | `/ws/ship`, TERN tools |

## Per-role views (`Universe.snapshot(role)`)
- **design** phase: design evaluation (transfer, mass, votes) + policy, and the last review if it failed.
- **review** phase: the same, plus the review in progress (`review.active`). The universe clock does not run; the wall clock times the reveal. Pass → `launch()`; fail → back to design with the launch date slipped; past the window → **scrubbed**.
- **mission**: geometry (true and *apparent* positions: Earth as the ship sees it, ship as Lantern sees it), comms (link, passes, in-flight packets, queues), director (scene, objectives, prompt), plus `control` or `ship` sections. Spectator/observer also get `truth` (hidden faults, storm timeline).

## Failure and fallback matrix
| Missing | Behaviour | UI label |
|---|---|---|
| NAIF kernels | Analytic ephemeris + Newtonian light time | `ANALYTIC EPHEMERIS` badge |
| Earth binary PCK | IAU_EARTH, else Earth Rotation Angle | DSN method in health |
| DONKI cache | Literature reference events (cited) | storm label says so |
| No storm data at all | Storm beat skipped | note in Lantern |
| LLM server | Deterministic grounded fallback | `fallback · grounded` |
| LLM reply ungrounded twice | Fallback reply, trace kept | debug drawer |
| ONNX model | z-score detector | detector name |
| whisper.cpp | Typed transcript | toast |
| Piper | Browser `speechSynthesis` (local voices) | – |
| DSN Now offline | Cached snapshot with age, else "unavailable" | panel badge |

## Performance
A full demo-mode mission (≈6.5 minutes of play) simulates in ~5 s of CPU headless. Live, the server ticks at 10 Hz, using well under 10% of a laptop core. SPK generation at launch takes ~0.5 s; the porkchop (44×44 Lambert solves) takes ~0.2 s and is cached.
