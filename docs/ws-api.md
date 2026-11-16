# WebSocket state API (protocol 1.0)

One JSON message per frame, UTF-8, no binary. Any engine that speaks WebSockets can be a LIGHT-LAG client: the web clients, a Godot 4 port (`WebSocketPeer`), Unreal Engine 5 (the built-in `WebSockets` module) or Unity (`ClientWebSocket`).

```
ws://<server>:8765/ws/<role>      role ∈ control | scientist | ship | engineer | spectator | observer
```
**Rooms.** Every game is a room with a four-letter code (letters A to Z without I, L and O), each with its own universe, clock, seats and voice clips. Connect with `?room=CODE`; without it you join `MAIN`, the shared room every server starts with, so older clients keep working. A code that does not exist gets one `{"type":"error","code":"no_room","error":"…"}` and the socket closes with 4404; don't reconnect. `hello` carries `room` and a `token`: the seat token for this connection, needed for `POST /api/fly-again` (header `X-Seat-Token`). Rooms other than `MAIN` close after `LL_ROOM_IDLE_MIN` minutes with nobody connected.

`observer` is the role for render-only clients (the planned UE5 cinematic pass): it receives truth geometry and every event, and can't act.

**Seats.** Four player seats: `control` (Flight Director) and `scientist` (Mission Scientist) on Earth, `ship` (Commander) and `engineer` (Flight Engineer) aboard. A seat reads its side's knowledge store, so `scientist` gets the same `control` section as Flight and `engineer` the same `ship` section as the Commander. State messages carry `seat` (the seat you connected as), `role` (its side) and `seats` (each seat's title, crew name, current vote and `ai: true` when nobody holds it). A seat with no human is played by AI: in design, AI seats vote GO once every human seat has; a human may make the design calls of an AI-held seat on the same side. `hello` also carries `seat`, `side` and `seats`. `GET /api/seats` returns the same table, `GET /api/catalog` the sourced design catalog.

## Server → client

### `hello` (once, on connect)
```json
{"type":"hello","role":"ship","protocol":"1.0",
 "config":{"delay_scale":0.1,"time_rate":10,"true_delay_mode":false,"scenario":"meridian_2020","judge_mode":false},
 "meta":{"channels":["bus_v",...],"units":{...},"labels":{...},"limits":{...}},
 "ephemeris":{"backend":"spice","label":"SPICE (NAIF)","kernels":{...}},
 "agents":{"ship":{"name":"TERN","model":"qwen3:4b","llm_available":true,"mode":"local LLM + grounding"},...},
 "voice":{"ffmpeg":true,"whisper":true,"piper":false}}
```

### `state` (4 Hz)
Common fields:
```json
{"type":"state","role":"control","phase":"design|review|scrubbed|mission|debrief","et":659836800.0,"utc":"2020-11-29T15:35:00Z",
 "home_local":"Sun Nov 29, 9:35 AM CST","rate":10.0,"delay_scale":0.1,"judge":false,
 "director":{"scene":{"id":"s5","act":2,"title":"Day 122 / The storm","card":"…","audio":null},"act_title":"The Gap",
             "objectives":[{"id":"storm_warning","text":"…","side":"control","done":false}],"prompt":null,"next":"…"}}
```
Design phase adds `design` (the design bay's evaluation, `server/lightlag/sim/design.py`) and `policy`:
```json
"design":{
  "launch_et":…,"arrival_et":…,"comms_plan":"standard","shelter_gcm2":20,"hull_liner_gcm2":0,"solar_monitor":true,
  "objective":"ice","payload":["grs_class","ctx_class","rad_class"],"vehicle":"falcon_heavy_exp","array":"array_std","battery":"batt_60",
  "valid":true,"reasons":[],"reason":"","changes":["+65 kg instruments","+0.2 t propellant","+$40 M","data margin 64% → -150% (red)"],
  "budgets":{"mass":{"label":"Mass","value":172.8,"limit":255.2,"unit":"t","margin":0.32,"band":"green","model":true},
             "power":{…},"data":{…},"cost":{…}},
  "mass":{"lines":[{"id":"habitat","label":"Habitat and systems","t":30.0,"model":true},{"id":"consumables","t":4.91,"sources":["bvad_rev1",…]},…],
          "payload_t":40.8,"propellant_t":132.0,"imleo_t":172.8,"budget_t":255.2,"margin_t":82.4,"feasible":true,"instruments_kg":34.8,"crew":4},
  "campaign":{"vehicle":"falcon_heavy_exp","name":"Falcon Heavy (expendable)","leo_t":63.8,"price_musd":150,"launches":3,"limit":4,
              "cost_musd":450,"capacity_t":255.2,"max_imleo_t":255.2,"text":"3 × Falcon Heavy (expendable), $450 M","sources":[…]},
  "power":{"supply_kw":13.78,"ship_load_kw":12.75,"instruments_w":43.2,"load_kw":12.8,"margin":0.07,"short_kw":0,"string_loss_kw":-1.5,
           "batt_kwh":60,"reserve_h":4.7,"r_au":1.524,"array":"array_std","battery":"batt_60"},
  "data":{"demand_mbit_day":3401,"capacity_mbit_day":9342,"margin":0.64,"plan":"standard","hours":16,"rate_kbps_mean":162.2,
          "stay_days":260,"returned_gbit":836.1,"dark_days":11,"by_plan":{"lean":{"capacity_mbit_day":4671,"margin":0.27,"band":"green","hours":8},…}},
  "cost":{"lines":[{"id":"launch","musd":450,…},{"id":"instruments",…},{"id":"habitat","model":true,…},{"id":"dsn",…},{"id":"ops","model":true,…}],
          "total_musd":3134,"cap_musd":3500,"margin":0.10,"mission_days":463},
  "science":{"objective":"ice","coverage":1.2,"verdict":"covers|partial|no","text":"Covers the objective.","need_gbit":810,"returned_gbit":836.1},
  "constants":{"band_green":0.1,"launch_limit":4,"mars_au":1.524},
  "tof_days":203.3,"c3":…,"dv_tmi":…,"dv_moi":…,"dv_total":…,"gcr_factor":…,"sep_factor_shelter":…,"votes":{…},"seats":{…}}
```
- **Bands:** `green` margin ≥ 10%, `yellow` 0 to 10%, `red` < 0. Any red budget makes the design invalid; `reasons` says why in plain words.
- **`changes`** is the ripple of the last `design.set`: what moved, in order (added mass, propellant, IMLEO, launches, cost, margins that moved).
- Numbers carry their provenance: `sources` (keys into `GET /api/catalog` → `sources`) or `model: true`.

**Phase order:** `design → review → mission → debrief`. When every seat votes GO on a valid design the universe enters `review` (the Flight Readiness Review, `server/lightlag/sim/review.py`, thresholds in `data/review_board.yaml`) instead of launching. After the reveal (its length is `review.timing.total_s` of wall time) a pass launches; a failure returns to `design` with the launch date slipped and every vote cleared; a failure whose slip would run past the launch window goes to `scrubbed`. `LL_REVIEW=0` skips the review and launches straight after the vote, as before.

Design, review and scrubbed phases add `review` and `scrub`:
```json
"review":{"n":1,"active":true,"elapsed_s":3.2,"verdict":"pass|lien|fail","verdict_text":"PASS WITH LIEN",
  "timing":{"dim_s":1.0,"area_s":3.0,"verdict_s":5.0,"total_s":21.0},
  "areas":[{"id":"power","title":"Power","persona":"Power reviewer","owner":"engineer","verdict":"lien","verdict_text":"PASS WITH LIEN",
            "lead":{"label":"Power margin at Mars","number":7.1,"unit":"%","text":"…"},
            "findings":[{"id":"power_margin","label":"…","status":"pass|lien|fail","number":7.1,"unit":"%","threshold":"lien below 10%",
                         "text":"…","model":true,"source":null,"sources":["nssdc_mars_fact"],"note":"…"}]}],
  "liens":[{"id":"power","area":"Power","text":"…","number":7.1,"unit":"%","findings":["power_margin","degraded_payload"],"model":true,"sources":[]}],
  "fails":[{"area":"Power","id":"degraded_life_support","text":"…"}],"launch_iso":"…","arrival_iso":"…","window_days_left":42.0,"slip_days":10},
"scrub":{"text":"…","synodic_days":779.94,"source":"nssdc_mars_fact","reviews":5,"window_end_iso":"…","next_window_days":779.94}
```
`review` stays in the state after a failed review (`active: false`) so the bay can show the findings. `design.power` also carries the board's degraded case: `degraded_kw`, `essential_kw`, `life_support_kw`, `string_loss_frac`.

Mission phase adds:
```json
"geometry":{"lt_up_s":262.2,"lt_down_s":262.3,"wait_up_s":26.2,"dist_km":7.86e7,"dist_au":0.525,"sep_deg":126.0,
  "spe_deg":38.1,"r_ship_au":1.36,"ship_phase":"cruise","mission_day":122.1,
  "pos":{"EARTH":[x,y,z],"MARS":[…],"SHIP":[…],"VENUS":[…],"MERCURY":[…]},      // AU, ECLIPJ2000, heliocentric
  "apparent_earth":[…],"apparent_ship":[…],                                        // where light left them
  "viewport":{"sun":{"dir":[…],"dist_km":…,"ang_diam_deg":…},"earth":{…},"mars":{…}}},  // ship-centred J2000 unit vectors
"comms":{"link":{"uplink_open":true,"downlink_open":false,"sep_deg":126,"moratorium":false,"blackout":false,
  "occulted":false,"pass_now":"Madrid","margin_db":9.4,"data_rate_bps":182000,"reason":"…"},
  "passes":[{"complex":"Madrid","start":…,"end":…,"start_iso":"…","end_iso":"…","max_el":59.8}],
  "plan":"standard","in_flight":[{"id":"b00042","kind":"alert","direction":"up","frac":0.37,"tx_et":…,"arrive_et":…,"label":"STORM WARNING"}],
  "queue":[…],"queue_counts":{"control":2,"ship":0},"stats":{"sent":…,"delivered":…,"expired":…}},
"anchors":{"launch":…,"arrival":…,"storm":…,"moratorium_start":…,"blackout_start":…,"blackout_end":…},
"windows":{"blackout":{…},"moratorium":{…}}
```
Role sections: `control` (delayed telemetry `telemetry`, `sample_et`, `age_s`, `stale`, `modes`, `dose`, `anomaly`, `requests`, `tern_log`, `space_weather`, `inbox`, `policy`, `dsn_now`) or `ship` (`telemetry` = local truth, `alarms`, `modes`, `dose`, `anomaly`, `shelter`, `attitude`, `inbox`, `letters`, `pings`, `policy`, `commands`). Spectator and observer get both, plus `truth` (`faults`, `storm`).

### `event` (as they happen)
`{"type":"event","kind":…,"et":…,"iso":…, …}`. Kinds and payloads:

| kind | payload | audience |
|---|---|---|
| `launched` | anchors, windows | all |
| `scene` | `scene` (director view) | all |
| `vote`, `design_nogo` | role, vote / reason | players |
| `bundle_tx` | bundle (no payload) | players, spectator |
| `bundle_rx` | id, kind, direction, true_lt_s, state | spectator |
| `message` | message | receiving side, spectator |
| `letter` / `letter_created` / `letter_home` | letter | ship / spectator |
| `tern` | text, level, source | ship, spectator |
| `tern_decision` / `tern_log` | decision | ship / control (delayed) |
| `policy_request` | request | control |
| `alert` | level, title, text | control, spectator |
| `objective` | id, text | all players |
| `prompt` | prompt {id, text, suggestion} | ship, spectator |
| `slowdown`, `receipt`, `ping`, `note`, `fault`, `family_reply` | various | various |
| `debrief` | full after-action report (now with `score.science`, and `score.crew`, shown not scored) | all |
| `crew_say` | who, name, role, text, trigger, ai | ship seats, spectator. Earth hears crew lines only through `crew_log` |
| `crew_log` | entries [{et, iso, kind (said, action, order_held, order_query, order_ok, ...), who, name, text}] | control seats (a light time late), spectator |
| `science_rx` | mbit, instruments, delivered_gbit, lt_s | control seats, spectator |
| `review` | review (as in the state, `active: true`) | all |
| `review_done` | verdict, liens (pass) or fails, slip_days, launch_iso (fail) | all |
| `scrubbed` | scrub | all |
| `callout` | callout {id, trigger, side, seat, step, et, iso, day, scene, text, choice, counter, model, sources, lien, shown_scene; arrived_et and lt_s at Earth} | ship seats when it happens aboard; control seats when its downlink bundle arrives (or at once for Earth-side events); spectator at once. At most one per scene per screen |
| `tmi_burn` | burn (as in the state) | all, once, when the review passes (`LL_LAUNCH_FX`) |
| `reality` | reality {items [{id, utc, date_label, text, source, source_title, url, scene, shown_et}], coasted, batch, scene} | all, at most once per scene (`LL_REALITY`) |

### `ai.reply`
```json
{"type":"ai.reply","agent":"FIDO","question":"battery status?","text":"Flight, FIDO. Battery charge 93.0 percent; as of 7 seconds ago. Over.",
 "source":"llm|fallback","tool_calls":[{"name":"get_telemetry","args":{},"result":{…}}],
 "attempts":[{"text":"…","grounding":{"ok":false,"violations":[…]}}],"grounding":{"ok":true,…},"latency_ms":812}
```

## Client → server
| type | fields | who |
|---|---|---|
| `design.set` | `changes`: control {launch_et, arrival_et, comms_plan, vehicle}; scientist {objective, payload}; ship {shelter_gcm2, hull_liner_gcm2, solar_monitor}; engineer {array, battery}. Ids must exist in the catalog (`payload` is a list of instrument ids); unknown ids are ignored. A seat may also set an AI-held seat's keys on its own side | owner seat |
| `design.vote` | `vote` GO / NO-GO. GO on an invalid design is refused with a `design_nogo` event carrying `reason` and `reasons` | any seat |
| `policy.set` | `rules` [{id, permission, params}] | both (design); control draft (mission) |
| `policy.sign` | – | control: sign + uplink; ship: countersign |
| `msg.send` | `kind` text/voice/alert/letter, `text`, `priority` normal/expedited, `audio_id`, `audio_bytes`, `to` (home / control) | control, ship |
| `cmd.send` | `command` (hga_lock, pump_switch, science, heaters_eco, sensor_recal), `text` | control |
| `ship.action` | `action` (shelter_enter/exit, scrubber_swap_bed, pump_switch, science, heaters_eco, hga_trim, hga_lock, breaker_reset, heater_relay_open, sensor_recal, execute_command), `args` | ship |
| `letter.play` | `id` | ship |
| `request.answer` | `rule`, `approve` | control |
| `dsn.surge` | `hours` | control |
| `comms.plan` | `plan` | control |
| `ai.ask` | `text` | control (FIDO), ship (TERN) |
| `ping` | – | anyone (spectators: "wave at Imani") |
| `science.priority` | `value` science_first / crew_first. Uplinked as a `science_priority` command bundle: in effect aboard only when it arrives, held in the blackout and the moratorium | scientist (control when the scientist seat is AI-held) |
| `crew.assign` | `crew` (crew id), `task` rest / repair / science / talk / medical. The AI crew member may decline with a reason (`reply.data.accepted`, `text`) | ship seats, for AI-held crew only |

**Crew and science state keys.** Mission-phase `state` carries `crew` and `science`. Ship seats get the truth aboard (`crew.members[]`: name, role, seat, ai, task {label, progress, eta_s}, fatigue, stress, trust_ground, morale, said, ask[]; `science.aboard`: generated, stored, in flight, lost, priority, recorder_frac). Earth seats get only what has arrived: `crew.as_of` and `science.as_of` (the summary from the last telemetry frame, `sample_et`, `age_s`), the received `crew.log`, plus Earth's own truth: `science.earth` (delivered_gbit, the `lever` order with state queued / in_flight / arrived_unconfirmed / confirmed, `may_set`). Spectators get both. `config.crew_ai` reports `LL_CREW_AI`.

**Review and callout state keys.** Mission-phase `state` carries `liens` (from the review) and `callouts`: what this screen has shown (ship seats: the ship's knowledge; Earth seats: what has arrived; spectator: everything shown to it). `config.review` and `config.callouts` report `LL_REVIEW` and `LL_CALLOUTS`. The `debrief` event's report and `GET /api/after-action` carry `debrief` (verdict, 5 to 7 `chains` of decision, flight event and outcome, `timeline`, `crew` with stress and morale series, `science`, `scored`, `not_scored`, `liens` with `came_true`, `fly_again`). Callouts travel as `callout` bundles (ship to Lantern) through the normal network.

**Burn and reality state keys.** In the `review` phase, after a pass, `state` carries `burn`: the trans-Mars injection burn every client plays before launch (`title`, `timing` {countdown_s, silence_s, settle_s, total_s}, `elapsed_s`, `active`, `dv_kms` and `c3` from the design, `dv_label`, `dv_note` (MODEL), `lt_s` (Earth seats play it this much later), `cockpit`, `earth`, `vehicle` (the design, for the spectator's ship model)). The universe clock does not run during it; when it ends the mission launches and the director starts scene s1. Mission-phase `state` carries `reality` {`shown` (the last 8 lines shown), `compare` (a line comparing with Perseverance's dates when the team's differ, else null), `pending`}, or null with `LL_REALITY=0`. Reality lines are notes about the real world, not in-world knowledge, so they carry no light delay. `config` reports `launch_fx`, `reality` and `right_now`.

Replies to actions arrive as `{"type":"reply","to":"<type>","data":{…}}`.

## REST helpers
Every endpoint below that reads or changes a game takes `?room=CODE` (default `MAIN`); an unknown code returns 404 `{"error":…}`.

**Rooms.** `POST /api/rooms` with JSON `{"judge":true|false}` → `{"code","judge","links":{"control","scientist","ship","engineer","spectator","factsheet"}}`. `judge:true` runs scripted Judge Mode inside that room only. 429 when one visitor has started `LL_ROOMS_PER_IP_HOUR` missions in the last hour; 503 when `LL_MAX_ROOMS` rooms are all in use. `GET /api/rooms/{code}` → phase, seat table and the same links.

**Limits.** `POST /api/voice`: 413 above `LL_VOICE_MAX_BYTES`, 415 when the bytes are not an audio container (WebM, Ogg, WAV, MP4, MP3, FLAC); each room keeps its newest `LL_VOICE_MAX_CLIPS` clips. `POST /api/fly-again` needs a seat token from this room (403 otherwise); `fresh=1` starts over with the default design. `POST /api/admin/reset` needs header `X-Admin-Token` equal to `LL_ADMIN_TOKEN`; with no token configured it works from localhost only, and never with `LL_PUBLIC=1`.

**Right now.** `GET /api/right-now` → `{"utc","unix","lt_s","lt_rate","dist_km","dist_au","backend","label"}`: the one-way light time for a signal leaving Earth this second, from the game's own ephemeris (`label` is `NASA JPL SPICE DE440` with kernels, else `analytic ephemeris, MODEL`). Cached for 1 s. 503 when the ephemeris is unavailable (the landing page then hides the line), 404 with `LL_RIGHT_NOW=0`.

`GET /api/health` (with `phases`, the last `review`, and `rooms`: live count, cap, tick cost in ms, connected clients, each room's phase) · `GET /api/factsheet` (the Mission Fact Sheet as JSON; the page is `/factsheet/`) · `POST /api/fly-again?suggestion=0|1&fresh=0|1` (back to the design bay with the flown design and TERN's rules loaded; `suggestion=1` applies the debrief's one suggested change; seats are kept, flight state is cleared) · `/api/meta` · `/api/porkchop` · `/api/trajectory` (polylines in AU) · `/api/after-action` · `/api/provenance` · `/api/science-cards` · `/api/model-card` · `/api/lan` (LAN URLs + QR SVGs) · `POST /api/voice` (multipart `file` → `{id, transcript, seconds, bytes, stt}`) · `GET /api/audio/{id}` (radio-filtered WAV) · `GET /api/quindar.wav` · `POST /api/admin/reset?judge=0|1&delay_scale=…` (admin token, see Limits)

## Writing a new client in ~30 lines (Godot 4, GDScript)
```gdscript
extends Node
var ws := WebSocketPeer.new()
func _ready(): ws.connect_to_url("ws://127.0.0.1:8765/ws/observer")
func _process(_dt):
    ws.poll()
    while ws.get_available_packet_count() > 0:
        var m = JSON.parse_string(ws.get_packet().get_string_from_utf8())
        if m.type == "state" and m.has("geometry"):
            var g = m.geometry
            $Earth.position = Vector3(g.pos.EARTH[0], g.pos.EARTH[2], -g.pos.EARTH[1]) * 100.0   # AU → scene units
            $Ship.position  = Vector3(g.pos.SHIP[0],  g.pos.SHIP[2],  -g.pos.SHIP[1])  * 100.0
            $HUD/Delay.text = "%.1f s" % g.lt_up_s
func send(t: String, d := {}): d.type = t; ws.send_text(JSON.stringify(d))
```
For UE5, subscribe as `observer`, map `geometry.viewport.*.dir` (unit vectors) onto the camera rig at true angular size, and drive Niagara light-packets from `comms.in_flight[].frac`.
