# Audio hooks: the adaptive score

`clients/shared/audio.js` · class `Score`. The mixer runs in both player clients and reads every 4 Hz state snapshot. **Silence is a feature**: the conjunction is designed to be the quietest moment in the game.

## Parameters (0..1, smoothed with a one-pole filter, k = 0.03 per frame)
| Param | Source | Curve |
|---|---|---|
| `delay` | `geometry.lt_up_s` | log10(1 + LT) / log10(1 + 1320). 1.2 s → 0.11, 4.4 min → 0.77, 22 min → 1.0 |
| `danger` | max(alarm level, storm dose) | alarms: caution 0.4, warning 0.7, emergency 1.0; dose: (µSv/h − 100) / 3000, clamped |
| `silence` | `comms.link.blackout` | 0 or 1 (smoothed into a slow fade) |

## Stems
Drop files in `clients/shared/audio/stems/` and they replace the procedural placeholders automatically (looped, sample-accurate):

| File | Role | Gain law |
|---|---|---|
| `bed.ogg` | Harmonic bed, always present | (1 − silence) × 0.8 |
| `pulse.ogg` | Rhythmic layer: the "conversation" | (1 − silence) × (1 − delay) × 0.6, so it thins as the gap grows |
| `danger.ogg` | Tension | (1 − 0.7·silence) × danger |
| `room.ogg` | Near-silent room tone (ship hum, ventilation) | 0.15 + 0.85 × silence. It *takes over* in the blackout |

Recommendations: all stems the same length and tempo-locked (e.g. 64 bars), loudness-matched around −23 LUFS, mono-compatible. In the placeholder, `pulse` is a heartbeat whose period goes from 0.6 s to 3 s as the delay grows. Keep that idea if you like it.

## One-shots
- **Quindar tones**: 2525 Hz (start) / 2475 Hz (end), 250 ms, on voice notes and letters (`quindar()` in `audio.js`; also baked into the server's radio filter).
- Suggested additions (hooks exist as events): `objective`, `alert` (level), `tern` (level), `letter`, `ping`, `scene` (act card stinger), `debrief`.

## Review board and callouts
`clients/shared/review.js` dispatches `window` CustomEvents named `ll-audio` with `detail.cue`:

| Cue | When | Extra |
|---|---|---|
| `review.dim` | the room dims (reveal starts) | |
| `review.area` | one of the five areas resolves | `area` (launch, power, comms, crew, cost), `verdict` (pass, lien, fail) |
| `review.verdict` | the board's verdict appears | `verdict` |
| `review.close` | the overlay closes | |

Timings (dim, per-area, verdict hold) live only in `data/review_board.yaml` (`timing`), so a score can be cut to them. Callouts arrive as the WebSocket event `callout`; a soft "design trace" chime on it is a natural Phase 5 addition. Suggested: a low drone under the dim, one muted tick per area (pitch up for pass, down for lien, a held low note for fail), silence before the verdict.

## Scene cue sheet
| Scene | Intent |
|---|---|
| Design | Bed only, low; the porkchop click is a soft tick |
| s1–s2 | Bed + full pulse; delay ~0.1–0.3 keeps it conversational |
| s3–s4 | Pulse slows and thins; first time `danger` rises (pump) |
| s5 storm | `danger` swells with the proton onset, *not* the flare. The flare is silent (light carries no sound), which builds dread. Duck everything under the storm alarm |
| s6 | Bed alone, pulse almost gone (delay ≈ 1.0) |
| s7 blackout | **Silence.** Room tone only. No score for 11 days of game time. The only sounds are TERN's voice and alarms |
| s8 flood | Hold room tone until the first letter plays, then bring the bed back under the letters, one notch per letter |
| Debrief | Bed, no pulse |

## Engine note
Web Audio requires a user gesture, so the score starts on the "Turn sound on" button in each seat's top bar. For the UE5 cinematic client, map the same three parameters to MetaSound inputs from the observer stream.

## Trans-Mars injection burn
`clients/shared/launch.js` plays the end of the TMI burn after a review pass, before the first scene card, and dispatches `ll-audio` events with `detail.cue`:

| Cue | When | Extra |
|---|---|---|
| `tmi.start` | the sequence starts on this screen (Earth seats one light time later) | `side` (ship, earth, spectator) |
| `tmi.count` | each second of the countdown, T-10 to T-1 | `n`, `side` |
| `tmi.cutoff` | engine cutoff: everything stops | `side` |
| `tmi.silence_end` | the silent beat is over; the cabin hum fades back | `side` |
| `tmi.end` | the sequence is over; the first scene card follows | `side` |
| `tmi.earth_quindar` | Houston keys the loop (Earth seats only) | |
| `tmi.earth_line` | the single "Cutoff confirmed" line | `text` |

The placeholder sounds are synthesized in `room-fx.js` (`Sound.burnStart`, `burnCut`, `humBack`): a brown-noise rumble through a rising low-pass plus a 34 to 42 Hz sine that build over the countdown, cut to nothing in 12 ms at engine cutoff together with the cabin hum and fans, then the room tone fades back in over the settle time. Earth seats hear a Quindar open tone, the line through browser speech, and the Quindar close tone. All timings (countdown, silence, settle) live only in `data/story/launch.yaml`. With reduced motion or low effects the rumble is skipped; the counter and the cutoff line still show.

Intent: the cut is the moment. Whatever score plays under the burn should stop on the same frame as `tmi.cutoff`, and nothing should play until `tmi.silence_end`.
