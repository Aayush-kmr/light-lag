# Visual design

How LIGHT-LAG looks, and why. Everything here lives in `clients/shared/style.css` (tokens and components), `clients/shared/viewport.js` (the 3D view) and `clients/shared/viz.js` (the 2D charts).

## The idea

There are two worlds on screen, and they never borrow from each other.

| | The console | Home |
|---|---|---|
| Who | Lantern Control, Meridian-1, TERN, FIDO | Lila and Theo |
| Surface | Cool blue-slate night, thin rules between panels | Warm paper |
| Type | Jost for words, IBM Plex Mono only for numbers | Newsreader, a book serif |
| Colour | One cool accent (signal blue: radio, light, the link) and one warm one (lantern amber: warnings) | Ink on paper, a wax-red seal for Lila, green for Theo |

Only words written by the family are allowed to look like paper. When the flood arrives after conjunction, the screen fills with envelopes, and that contrast is the emotional payoff of the whole game.

**Every number has an age.** On Lantern's side the ship's telemetry column fades as it gets older than the light time (`ageClass()` in `ll.js`: four steps from fresh to stale). During the blackout the whole column dims and its age reads "3.7 days ago". You never have to explain staleness; you can see it.

## Tokens

| Token | Value | Use |
|---|---|---|
| `--night` | `#0a1019` | the darkened control room |
| `--console` / `--console-2` | `#111a26` / `#172334` | raised surfaces, inputs |
| `--rule` / `--rule-2` | `#1e2b3c` / `#2a3a50` | 1 px rules between panels, borders |
| `--phosphor` | `#dce7ef` | primary text |
| `--haze` / `--dim` | `#8d9bad` / `#56657a` | secondary, tertiary text |
| `--signal` | `#86d0f5` | the link, light, primary actions |
| `--lantern` | `#f4b04a` | warnings, Flight's seat, things that wait |
| `--alarm` | `#ff6a5c` | emergencies, blackout |
| `--go` | `#9ed48b` | normal, done, go |
| `--paper` / `--paper-ink` | `#efe4cf` / `#2b2219` | home |

Type scale: 11.5 / 13 / 15 / 18 / 24 / 34 / 56 px. One radius, 6 px, except paper (3 px, it is paper) and floating glass (10 px).

## Making it intuitive

Playtesters found the first build "a bit confusing": too many panels, all equally loud, and no hint of what to do. The graphics pass added four things:

1. **The guide.** A bar under the clock (a floating pill on the ship) always says what to do next in plain words, and the control it means pulses with a blue outline. If that control is inside a closed tab, the tab pulses instead, and the first time a new objective appears its tab opens by itself. The mapping from objective to words and control is the `GUIDE` table in `ll.js`. When nothing is needed it says so, and suggests something to look at.
2. **A briefing on first visit.** Four sentences per seat (`BRIEF` in `ll.js`), shown once and available again from "How to play".
3. **Less on screen at once.** The ship is the 3D view plus one side panel with four tabs (TERN, Systems, Radiation, Lantern). Lantern Control keeps the three things Flight needs constantly (the ship as last heard, the map, the radio) and puts the rest in five tabs.
4. **Plain words.** No internal names in the interface: "Antenna link: open" instead of `LINK OPEN`, "Acts alone / Asks first / Never" instead of `auto/ask/forbid`, "Book an emergency pass" instead of "DSN surge". Buttons say what happens.

Mission design became a five-step sequence (launch date, crew protection, radio time, TERN's rules, go/no-go) with a live summary of the mission beside it. Each step says whose call it is.

## The 3D view (`SpaceView`)

three.js r187 on WebGL2 with ACES tone mapping and a selective bloom (threshold 1.9, so only the Sun, windows, nav lights and city lights glow).

Everything in it is placed from the server's SPICE geometry:

- **Directions** to the Sun, Earth and Mars are the true J2000 unit vectors from the ship.
- **Sizes** are true angular sizes: each body sits at a fixed render distance and is scaled by `radius / true distance`. Earth is a 2° disc on day one, a few pixels by Mars, and a single labelled point after that.
- **Earth** turns with the Earth Rotation Angle (IERS), and the day/night terminator, city lights (Black Marble) and ocean sun-glint follow the real Sun direction. A thin limb glow stands in for the atmosphere.
- **Mars** is oriented by the IAU pole (RA 317.68°, Dec 52.89°) and turns by W = 176.630° + 350.89198226° per day.
- **Stars** are all 9,096 stars of the Yale Bright Star Catalogue at their J2000 positions, sized by magnitude and tinted by colour temperature.
- **The ship** points its high-gain dish at Earth and turns its solar wings to the Sun. In a proton storm it swings its propellant tanks sunward and the Earth link drops: the beam disappears.
- **The beam** from the dish to Earth is blue when an antenna is listening, amber when messages must wait, red in blackout. Messages in flight ride it as coloured dots.
- A faint fill light from the camera keeps the ship's shadowed side readable. That one is a game convention, not physics.

The camera frames the ship with its target body behind it, eases back into frame when the geometry drifts (unless you have touched it in the last 20 s), and has a "Window" mode that looks out from the ship.

## The 2D views (`viz.js`)

- **Light ribbon**: Houston on the left, the ship on the right, one tick per light-minute (per 10 s when close). Messages crawl across at the speed of light: uplinks above, downlinks below, labelled. The line is dashed amber when no dish is listening and dashed red with the Sun on it during blackout.
- **Orrery**: top-down on the ecliptic. On Lantern's screen a ring marks where the ship was when the light you are seeing left it. Labels place themselves to avoid each other.
- **Who can know what**: a spacetime diagram. Each message is a slanted line; the ship can only know what is below its past light cone.
- **Porkchop**: one pixel per Lambert transfer, drawn off-screen and scaled smoothly. Bright is cheap. The trips the rocket can lift are outlined in amber; Perseverance's dates are marked.

## Accessibility

Visible focus on every control, `prefers-reduced-motion` turns off the guide's pulse and other animation, colour is never the only signal (badges have words, alarms have levels), and layouts reflow down to phone width. The 3D view degrades to a message if WebGL is unavailable; everything else keeps working.

## Checking it

Every screen was checked in headless Chrome (SwiftShader WebGL) through a full demo-mode run: three clients, all eight scenes and the debrief, zero JavaScript errors. The screenshots in `docs/img/` come from that run.
