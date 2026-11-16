# LIGHT-LAG · Game Design Document

## 1. Pitch
Two people who need each other can't talk in real time. One is in Houston, one is on the way to Mars, and every word between them crosses the real distance at the speed of light. You plan the mission together, then you fly it apart.

## 2. Pillars (the brief's five boxes)

| Pillar | How LIGHT-LAG ticks it |
|---|---|
| **Highly technical** | SPICE ephemeris, a real SPK written for a fictional ship, Lambert porkchops, DSN geometry, a DTN bundle layer, Parker-spiral proton physics, local LLMs with tool calls and a grounding guardrail, a QLoRA fine-tune with a 64-prompt automatic eval, an ONNX anomaly autoencoder, and a deterministic autonomy engine. |
| **Educational** | You learn by *suffering* the physics, not by reading it. Every mechanic is a real constraint: light time, uplink/downlink asymmetry, handover gaps, the moratorium, magnetic connectivity, shielding asymmetry, the autonomy inequality. "Science Desk" cards appear in context with a source (`data/science_cards.yaml`). |
| **Social** | Two-player co-op with asymmetric information: neither side can win alone. A spectator view puts both sides on one big screen and phones (QR join). Spectators can "wave at Imani" with a ping that crosses space at light speed. There's a family line where Lantern has to decide what to tell a worried husband, and a shareable after-action report. |
| **Visual** | Orrery from true positions, light-speed packets in flight, a spacetime "what the ship knew when" diagram, a porkchop heatmap, and a ship viewport where Earth is drawn at its true angular size, shrinking to a dot. The cinematic pass (WebGPU + NASA imagery) is next; see `engine-study.md`. |
| **Fun** | Tension from waiting, dramatic irony (Lantern sees the flare first; the ship sees it later; the protons come later still), real decisions under delay, and an ending the player authors: the order in which to hear twelve messages. |

## 3. Roles

| | Lantern Control (Player A) | Meridian-1 (Player B) |
|---|---|---|
| Fantasy | Flight Director: sees everything, *late* | Commander: sees *now*, but only her ship |
| Knows | Telemetry that has arrived (age-stamped), space weather from Earth (GOES/DONKI), the DSN schedule, the family | Local sensors, the view, TERN, what Earth said minutes ago |
| Tools | Uplink composer (message / alert / command), DSN planner + emergency pass, policy editor, request approvals, FIDO assistant, DSN Now | Tasks (power, thermal, CO₂, antenna), shelter, TERN, letters, downlink |
| Owns in design | Launch window, comms plan | Shielding, solar monitor |
| Both | Autonomy policy, GO/NO-GO | |

**Asymmetry rule:** each client receives only its own view from the server (`Universe.snapshot(role)`). Control literally cannot see the ship's present state, and the ship cannot see Earth's.

## 4. Session structure

```
Mission Design (~2 min) → Flight Readiness Review (~20 s) → Act I "Close" → Act II "The Gap" → Act III "Conjunction" → Debrief
```
The phases follow NASA's own lifecycle: concept, design, **review**, flight, debrief. The review is a gate: five deterministic reviewers read the design bay's numbers plus the cases it does not run (one array string lost, the reference storm, the cosmic-ray dose). A pass may carry liens that come back in flight; a failure costs ten days of the launch window (MODEL), which changes the trip; a slip past the window scrubs the mission. In flight, **design-to-flight callouts** ("Your design") name the choice behind an outcome, with the number and a MODEL counterfactual, and the debrief rebuilds the mission as chains of decision, flight event and outcome.
The mission is 15 months long; a session is ~30 minutes (8 in demo mode). The Director cuts it into **scenes** anchored to real computed events and skips time between them. Inside a scene the universe runs at 1/DELAY_SCALE (10× by default).

| Scene | Real date (default plan) | One-way light time | What happens | Teaches |
|---|---|---|---|---|
| Design | | | Porkchop, comms plan, shielding vs mass, solar monitor, autonomy policy, GO poll | Windows, C3, mass trades, delegation |
| Review | | | Five reviewers, one at a time; PASS, PASS WITH LIEN or FAIL. The default design passes with two liens (a 7% power margin that sheds the payload one string short; cosmic-ray dose over the career limit) | Margins, degraded cases, accepted risk |
| s1 Day 1 | 2020-07-31 | 1.2 s | Comm check, read-back drill, antenna drift fault | Protocol, read-backs, pointing |
| s2 Day 5 | 2020-08-05, 8 pm Houston | 6.1 s | **Beat 1: the last live goodnight** ("twelve Mississippi", "over" is born) | Round trip = 2 × one-way |
| s3 Day 62 | 2020-10-01 | 78 s | **Beat 2: Lila's first confused message**: "Where is it right now?" (visible in the lane) | Messages are physical things in flight |
| s4 Day 98 | 2020-11-06 | 172 s | Coolant pump fault vs **Beat 3: Lila waiting up**. You probably can't do both | Delay turns conversation into letters |
| s5 Day 122 | 2020-11-29 | 262 s | **The storm** (replay of SOL2017-09-10): flare light reaches Earth, then the ship ~3 min later; protons arrive ~34 min after; the DSN gap holds Lantern's warning; storm attitude request; Theo calls Lantern. **Beat 4: a storm night** | Light vs particles, connectivity, handover gaps, shielding |
| s6 Day 425 | 2021-09-29 | 21.9 min | The last command window: get the final policy aboard before the moratorium. **Beat 5 begins** | Plan before the door closes |
| s7 | 2021-10-02 → 10-13 | 21.9 min | **The silence**: an 11.5-day blackout. Array loss, then a stuck scrubber valve; TERN acts under policy and must break it. Lila's nightly messages queue in Houston | Autonomy; why "ask" fails at Mars |
| s8 | 2021-10-13 | 21.8 min | **Beat 6: the flood**: everything arrives at once; you choose the order | |
| Debrief | | | Cause and effect: 5 to 7 chains (decision → flight event → outcome, with counterfactuals), the mission on one line, the crew, science, what scored and what didn't, TERN's decisions, the order you chose, fly it again | Every design choice has a consequence |

## 5. Core mechanics

**5.1 Delay.** Every message is a DTN bundle. It leaves when a contact is open and arrives one light-time later (from `spkezr`). The top bar always shows **true light time** and **you wait** (true × DELAY_SCALE).

**5.2 Contacts.** A link exists only if a DSN pass is scheduled (comms plan), the Sun isn't in the way (SEP > 2°), Mars isn't in the way, and the antenna is pointed. **Uplink and downlink windows differ by a light time.** Lantern can buy an emergency pass (10 min to slew).

**5.3 Priority and lifetime.** Expedited > normal > bulk; first-in-first-out within a class. Telemetry is a stream: only the freshest 24 frames are kept. Commands (including policy uploads and authorizations) are held during the moratorium.

**5.4 Staleness.** Lantern's telemetry carries age badges (never below the light time). The orrery draws the ship where its light left it. The spacetime diagram shows what the ship can possibly know yet.

**5.5 The storm.** Flare at Earth-time *T*. The ship sees the flare at *T − r_E/c + r_S/c*, but only if it carries the solar monitor. Protons arrive along the Parker spiral after a path-length delay plus a connectivity penalty. Dose depends on shelter (water wall, chosen in design), storm attitude (tanks sunward, which costs the Earth link), and timing.

**5.6 Autonomy.** The policy grants each of TERN's actions *auto / ask / forbid*. The engine compares **time-to-harm** with the **round trip to Earth**. If harm comes first, TERN acts and logs a POLICY BREAK with its reasoning. Requests and answers are bundles too. Decisions are deterministic; the LLM only explains them.

**5.7 Anomaly detection.** An onboard ONNX autoencoder scores every telemetry frame; persistent high reconstruction error raises an alert. The alert goes to the ship immediately and reaches Lantern after a light time.

**5.8 Letters.** Lila's messages are created in Houston at her real local bedtime (America/Chicago, with DST) and travel like everything else. Control sees only "PERSONAL – contents private". The flood plays in the order the player taps.

## 6. Objectives and scoring
Each scene lists objectives per side (`data/scenarios/*.yaml`). The report card (`sim/scoring.py`) scores what players can control: storm dose (graded), sheltering before onset, objectives, comms discipline (sent/delivered/expired) and autonomy outcomes. Cosmic-ray dose is **shown, not scored**: no design can fix it, and that is the lesson. **Letters are never scored.**

## 7. Difficulty and tuning knobs
`LL_DELAY_SCALE` (0.1 default; 1.0 = True Delay) · comms plan (lean/standard/continuous) · solar monitor on/off · shelter thickness · policy permissions · demo mode (bots + compression). The scenario YAML holds every scene time, fault time and cue; designers never need to touch code.

## 8. Onboarding through play
There is no tutorial screen. Act I *is* the tutorial: 1.2 s of delay makes the protocol (callsign, read-back, "over") feel natural before it becomes necessary. Each new constraint arrives exactly once, in the scene that needs it, with an optional Science Desk card.

## 9. Social design details
- **Co-op by dependency.** Lantern sees the flare; the ship sees the dose. Lantern books the antenna; the ship points it. Neither can finish an act alone.
- **Spectator mode** (`/spectator/`) is made for a shared screen, an audience or a classroom: true positions, both sides, the Houston kitchen table (Lila recording), and hidden faults that only the audience can see, for dramatic irony.
- **Wave at Imani.** Any phone can send a hello; it's a real bundle that takes the real light time, and the ship counts them.
- **The family line.** Theo calls Lantern during the storm. What do you tell a family when your information is four minutes old?

## 10. Accessibility and comfort
Text is always available alongside voice (whisper transcripts, typed fallback). Colour is never the only signal: status words appear in badges. Motion is minimal. Audio defaults off until a click (browser policy), and the score has a silence-first design.

## 11. Content notes
Restrained tone; no on-screen peril to the child; no death states. The worst outcome is a high storm dose and a missed bedtime.
