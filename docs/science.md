# The science in LIGHT-LAG

Every mechanic is a real constraint. This page lists each model the game uses, the equation, the source, what we simplified, and how we checked it. Test names refer to `server/tests/`.

---

## 1. Light time
Radio is light: c = 299,792.458 km/s. One-way light time is computed by NASA's SPICE toolkit:

```
spkezr(target, et, "J2000", "XCN", observer)  -> (state, lt)
```
`XCN` is the *transmission* case with converged Newtonian corrections: a signal leaves the observer at `et` and reaches the target where the target *will be* at `et + lt`. Uplink (Earth→ship) and downlink (ship→Earth) therefore differ slightly, because the ship moves while light is in flight (`test_uplink_and_downlink_differ`).

**The ship has a real ephemeris.** Meridian-1 is fictional, so we generate its trajectory and **write it into a SPICE SPK file** (type 13, Hermite; NAIF ID −926) at launch. From then on `spkezr("MERIDIAN-1", …)` works exactly as for a real spacecraft (`astro/trajectory.py: write_spk`).

**Validation** (`test_close_approach_distances`): Earth–Mars distance at three published close approaches: 2003-08-27 (55.76 Mkm), 2018-07-31 (57.59 Mkm), 2020-10-06 (62.07 Mkm). Both backends agree within 0.3%. Over 2020–2022 the light time ranges from ~3.1 to ~22 minutes (`test_light_time_spans_3_to_22_minutes`).

**Fallback** when no kernels are present: E. M. Standish's *Keplerian Elements for Approximate Positions of the Major Planets* (JPL SSD, valid 1800–2050), plus our own converged light-time iteration. It agrees with SPICE to better than 1 s of light time on the dates we use (`test_kepler_and_spice_agree`).

## 2. Time scales
UTC → ET (TDB seconds past J2000) via SPICE `str2et` when the leap-second kernel is loaded. Otherwise ET = (UTC − 2000-01-01T12:00) + (TAI−UTC) + 32.184 s, using the IERS leap-second table (37 s since 2017). TDB−TT (< 2 ms) is ignored. Lila's bedtime is computed in `America/Chicago` with daylight saving, so her messages land at 01:00 UTC in summer and 02:00 UTC in winter.

## 3. Getting there: Lambert and the porkchop
Given Earth's position at launch and Mars's at arrival, Lambert's problem finds the conic that connects them in the time of flight. We use the universal-variable formulation with bisection on ψ (Vallado, Algorithm 58): slower than Izzo's method, but it never diverges.

- `test_lambert_curtis_example_5_2` reproduces the textbook example (Curtis, *Orbital Mechanics for Engineering Students*, Ex. 5.2) to 2 m/s.
- **C3 = v∞²** at departure is the launch-energy figure every Mars mission quotes. Perseverance-like dates (2020-07-30 → 2021-02-18) give **C3 ≈ 14.5 km²/s²**, with v∞ at Mars ≈ 2.55 km/s (`test_perseverance_like_transfer`).
- Δv from a 300 km parking orbit: Δv = √(v∞² + 2μ/r) − √(μ/r). Capture into a 400 × 33,000 km (~1 sol) Mars orbit: Δv = √(v∞² + 2μ/r_p) − √(μ(1+e)/r_p).
- **Why missions come in waves:** the low-energy island repeats every Earth–Mars synodic period, ~780 days.

**The design bay (`sim/design.py`).** Four budgets must close before launch. Margin bands: green ≥ 10%, yellow 0 to 10%, red < 0 (MODEL).
- **Mass.** Tsiolkovsky with Isp = 380 s (methane/oxygen class), 8% tankage per tonne of propellant, two burns (TMI, then MOI). Payload = 30 t habitat (MODEL) + BVAD consumables for 4 crew + storm-shelter water ((g/cm²) × 10 kg/m² × 24 m²) + liner + 0.35 t solar monitor + instruments (flight-heritage masses) + array and battery. IMLEO is bought in whole launches: `launches = ceil(IMLEO / LEO capacity)`, at most 4 (MODEL). Capacities and prices are published figures (`data/catalog/launch_vehicles.yaml`).
- **Power.** Array output at Mars = P(1 AU) / r², r = 1.524 AU (Mars semimajor axis, NASA Mars Fact Sheet: irradiance 0.431 of Earth's). Loads are measured by running the flight model (`ShipSystems.step`) one step at that distance, plus instrument power, so design and flight cannot disagree. Battery reserve = kWh / load.
- **Data.** Downlink capacity uses the contact plan's own link margin and rate (`ContactPlan.link_margin_db`, `data_rate_bps`) at the true Earth to Mars distance for every day of the 260-day stay, zero on blackout days, times the plan's scheduled hours (8, 16, 24 h). Demand is the payload's Mbit/day (MODEL rates).
- **Cost.** Launches (published prices) + instruments + habitat and systems ($2.5 B, MODEL) + DSN aperture fees (AF = $950/h × 2.0 for 70 m dishes, NASA SCaN-MOCS-0001 Rev 2) + flight operations ($100 M/year, MODEL), against a cost cap per objective (MODEL).

## 4. Solar conjunction
The **Sun–Earth–Probe (SEP) angle** is the angle at Earth between the Sun and the spacecraft. As it shrinks, radio passes through denser corona and suffers scintillation.

| Threshold | Game rule | Default plan |
|---|---|---|
| SEP < 3° | **Command moratorium**: commands, policy uploads and authorizations are held | 2021-09-29 07:57 → 10-17 02:53 UTC (17.8 d) |
| SEP < 2° | **Blackout**: nothing gets through | 2021-10-02 11:43 → 10-13 23:26 UTC (11.5 d) |
| Minimum | 0.65° | 2021-10-08 |

**Validation:** NASA stopped commanding its Mars fleet **Oct 2–16, 2021** (NASA/JPL release 2021-200), and conjunction was Oct 7–8. Our SEP < 2° window begins **2021-10-02**: same physics, same day (`test_2021_mars_conjunction`). Margin model: scintillation costs (5° − SEP) × 2 dB below 5°.

## 5. The Deep Space Network
Complexes at Goldstone (DSS-14), Madrid (DSS-63) and Canberra (DSS-43), ~120° apart. Elevation of the ship above a 10° mask, using SPICE's ITRF93 or IAU_EARTH frame when available, else the IAU 2000 Earth Rotation Angle (precession/nutation ignored, < 0.4°). DSS-14's geodetic position reproduces NAIF's ITRF93 coordinates to within 4 m (`test_dss14_geodetic_matches_naif_itrf93`).

**Antenna time is scheduled, not free.** The comms plan buys 1, 2 or 3 complexes' worth of passes per day. **Uplink windows ≠ downlink windows:** Earth may transmit while a station tracks; the ship must transmit so that its signal *arrives* during a pass, which means starting one light-time early.

## 6. Delay-tolerant networking
Store-and-forward bundles modelled on the Bundle Protocol (RFC 9171): source/destination endpoint IDs, creation timestamp, lifetime, class of service (bulk / normal / expedited), optional status reports. Nodes hold bundles until a contact opens; expired bundles are deleted and reported. Delivery receipts are bundles too, so a read receipt takes a second light-time to come back.

## 7. Space weather: light, then particles
DONKI times are as observed at Earth. For a flare first seen at Earth at *T*:

- emission at the Sun: *t_e = T − r_E/c*
- flare light at the ship: *t_e + r_S/c*. The ship may see it before or after Earth does.
- proton release: *t_e* + 18 min (consistent with the 15:53–15:55 UT release estimated for SOL2017-09-10, Kurt et al.)
- protons don't travel straight. They follow the interplanetary field, wound into an Archimedean (Parker) spiral by solar rotation. Path length to radius *r*:

  **L(r) = (a/2)·[x√(1+x²) + asinh x],  a = v_sw/Ω☉,  x = r/a**

  For v_sw = 400 km/s, L(1 AU) ≈ 1.17 AU (`test_parker_spiral_at_1au`).
- first arrivals at 500 MeV (β = 0.758): onset = release + L/(βc) + connectivity delay.
- **Magnetic connectivity.** A field line from the observer reaches the Sun about Ω·r/v_sw ≈ 61° west of the observer's longitude (at 1 AU). Western-limb flares (like W88) are well connected to Earth. The ship's footpoint is computed from its real position: `footpoint = Δλ(ship−Earth) + Ω r_ship / v_sw`. Intensity factor = exp(−Δ²/2σ²) with σ = 38° (55° for widespread events). Delay penalty: +2 min per 10° off-connection.

**Validation against observation (`test_gle72_onset_at_earth`):** placed at its real date, the model's proton onset at Earth for SOL2017-09-10 is 16:03 UT. GLE 72 onset at 1 AU was observed at **16:06–16:08 UT** (Kurt et al., arXiv:1806.00226), so we're within ~4 minutes.

**The storm in the default plan** (replayed at 2020-11-29, ship at 1.36 AU): flare light at Earth 15:35, at the ship 15:38; proton onset at the ship 16:08; peak ~18:44; connectivity 22° (factor 0.92). With the *standard* comms plan, no DSN station is tracking at 15:35 and Madrid rises at 16:25, so **Lantern's warning waits ~50 minutes and arrives after the protons**, unless Lantern books an emergency pass or the ship carries its own solar monitor.

## 8. Radiation dose
| Component | Model | Source / status |
|---|---|---|
| GCR baseline | **1.84 mSv/day** dose equivalent behind MSL-like shielding | Measured: MSL RAD in cruise, Zeitlin et al., *Science* 340:1080 (2013) |
| GCR shielding | factor = max(0.75, 1 − 0.012 × liner g/cm²) | Illustrative; captures "a foot of aluminium wouldn't change the dose very much" (Zeitlin) |
| SEP profile | quadratic rise from onset to peak, exponential decay τ = 10 h | Illustrative parametric model |
| SEP magnitude | peak (mSv/h, behind hull) = 2.0 × √(flux/X1) × (v_CME/2000)² × connectivity / r², capped at 50 | Illustrative; ~100 mSv unsheltered for the X8.2 replay |
| Shelter / liner | SEP factor = exp(−(liner + shelter)/12 g/cm²) | Illustrative; SEP protons are stopped by mass |
| Storm attitude | × 0.6 (tanks sunward), and the HGA can't see Earth | Illustrative |
| Forbush decrease | GCR × (1 − 0.10 e^(−t/1.5 d)) after a CME passes the ship | Phenomenon observed by RAD at Mars, Sept 2017 |

**Limits:** NASA-STD-3001: **600 mSv career** effective dose; **250 mSv per solar particle event**. A 15-month mission at 1.84 mSv/day accumulates ~810 mSv of GCR in our model, above the career limit. NASA has said a Mars mission would need a waiver. We show this and don't score it.

## 9. The ship model
Simple first-order dynamics (`sim/subsystems.py`) chosen for believable *time scales*:
- **Power:** array output P(1 AU)/r² (32 kW standard, chosen in design); at Mars ~13.8 kW. Loads ~11–12.8 kW, 60 kWh battery by default. An array-string failure turns a surplus into a ~1 kW deficit, which takes days to become dangerous. That is exactly the scale where "ask Earth" fails during conjunction.
- **CO₂:** one crew member in 45 m³ produces ~0.43 mmHg/h. A dead scrubber takes ~8 h to reach the 6 mmHg warning: hours, not minutes. Backup LiOH canisters exist.
- **Thermal:** pump wear raises the active loop's temperature over ~40 min.
- **Antenna:** Gaussian beam, 12 dB lost at 0.6° off-axis; gimbal drift ~0.04°/min.

## 10. The autonomy inequality
TERN acts on its own when

**time_to_harm < 1.25 × (wait_for_contact + LT_down + 2 min decision + LT_up)**

with time-to-harm extrapolated linearly from recent telemetry. At 1.2 s of delay this never triggers; at 4 light-minutes it rarely does; behind the Sun it always does. The default Judge Mode run produces a real example: 2021-10-04, battery trending to 12% in ~35 h, earliest answer from Lantern 9.9 days, so TERN sheds the science payload and logs a POLICY BREAK.

## 11. Why the universe runs fast instead of light running slow
`DELAY_SCALE = 0.1` could have been implemented by multiplying every delay by 0.1. That would break causality: protons would outrun the warning in different proportions than physics says. Instead the whole universe clock runs 10× faster than the wall clock. Light, protons, dose rates and orbits stay exact; only the human waits less. Judge Mode compresses further between beats, and the UI still shows the true light time.

## 12. Crew model
The four crew members are simulated people (`sim/crew.py`). Each has fatigue, stress, trust in the ground and morale on a 0 to 1 scale, plus skills. The Science Officer and the Flight Surgeon are always played by the AI; the Commander and the Flight Engineer only while nobody holds their seat. The AI never acts for a seat a person holds; it may only suggest. Every coefficient below is **MODEL**, chosen for believable time scales.

| Quantity | Model (per hour unless stated) |
|---|---|
| Fatigue | +0.055 awake, +0.03 more while working, ×(1 + 0.5 stress); −0.12 asleep. Sleep when above 0.7 (the Science Officer 0.8: her flaw) and nothing is urgent; wake below 0.15 or when needed |
| Task time | base minutes / (0.5 + skill) × (1 + fatigue). Bases: scrubber valve 10, heater relay 8, antenna brake 6, breaker 5, sensor recal 5, loop switch 4 |
| Stress | +0.08 with a warning alarm, +0.25 with an emergency, +0.15 in a proton storm, +0.01 in the blackout; recovers at 0.15 × (0.5 + morale) × (stress − 0.10). +0.1 at a TERN policy break, +0.2 for the Commander when TERN wakes her |
| Morale | +0.01 per letter delivered, +0.03 (Commander) / +0.015 (others) per letter played, +0.01 per contact from Lantern, −0.02 per day of silence beyond one day. **Shown, never scored. Letters are never scored** |
| Trust in the ground | starts 0.6. Order in time and useful +0.06 (Commander) / +0.03; stale −0.06 / −0.03; contradicting −0.10 / −0.05; queried −0.03; a denied request −0.05, an approved one +0.04 |
| Deciding | every 30 universe-seconds or on any change, utility scores per action; act when the best ≥ 0.40 + 0.25 × trust (low trust acts sooner). A repair the ground could still weigh in on (link open, light time under 10 min) loses 0.25 × trust (high trust waits for Lantern). A task is dropped only for something at least 0.25 more urgent |

**Who fixes what.** TERN wakes the Commander for the stuck scrubber valve, and the crew may start that repair only after TERN's call. Everything else is the Flight Engineer's. If a person holds that seat, an AI crew member says so instead of acting.

**Stale orders.** Every Earth command carries the telemetry Lantern was looking at (`context`: sample time, values, modes). On arrival the crew compares it with now: an order whose target is already fixed is held as *stale*; one that would undo what the crew did (a loop switch after they already switched, a toggle already toggled) is held as *contradicting*; one the situation has outrun (battery 20 points lower, both loops hot) is held and *queried* with a downlinked question; otherwise it runs under the autonomy policy, by TERN if `execute_uplink` is auto, else by the crew by hand. Every judgment goes into the crew log, which is downlinked, so Lantern learns of it a light time later. In Judge Mode, Lantern's loop switch in "Waiting up" arrives after TERN already switched: the crew holds it.

**Precedent.** In 1973 the Skylab 4 crew, overloaded by a ground-built schedule, fell behind and pushed back; the ground rebuilt the timeline and the crew finished the mission ahead of plan (NASA SP-4208, *Living and Working in Space*, ch. 16).

## 13. Science data
The payload fills a recorder; the recorder drains through the same antenna as telemetry, alerts and letters (`sim/science.py`).

- **Generation.** Each instrument writes its catalogued `data_mbit_day` while the payload is powered. Radiation instruments (`serves.radiation > 0`) run in cruise and orbit; cameras and spectrometers only after Mars arrival. TERN shedding the payload stops all of it.
- **Recorder: 160 Gbit**, the capacity of MRO's solid-state recorder (NASA MRO launch press kit, 2005; also the 2006 arrival press kit). Full means the oldest data is overwritten and counted lost.
- **Downlink in a scene.** The recorder is packed into 250 Mbit `science` bundles (MODEL), at most two waiting at a time. The network serializes a chunk at the link rate: it leaves only when the link has carried its whole size, and while it waits the same or lower class behind it waits too. Telemetry has its own virtual channel and never waits.
- **Downlink across a time skip** (analytic, one day at a time): Mbit carried = data rate at that distance and SEP × antenna hours per day for the plan (lean 8, standard 16, continuous 24) × science share (crew first 0.60, science first 0.85; MODEL), nothing behind the Sun. Chunks not yet sent go back on the recorder before the skip.
- **Conservation** holds at every step and is tested: generated = delivered + on the recorder + packed or crossing space + lost.
- **The Mission Scientist's lever.** `science_first` sends science at normal priority, ahead of the crew's letters home; `crew_first` keeps it at bulk, behind them. The choice is a command bundle: it changes anything only when it reaches the ship a light time later, never through the blackout or the command moratorium, and Earth knows it is in effect only when the receipt comes back.
- **Science return (scored).** Value = Σ delivered Gbit × the instrument's `serves[objective]` weight. The objective is met when the delivered data from instruments that serve it reaches the objective's `min_data_gbit` and the payload covers the objective at all.

The default payload (GRS, CTX, RAD) delivers about 840 Gbit over the mission for the ice objective; a HiRISE-class camera (20 Gbit/day) overruns the link and the recorder, which is the trade the design bay asks about. Note: our illustrative link (2 Mbit/s cap) is slower than MRO's real 0.5 to 6 Mbit/s, so data volumes here are conservative.

## 14. The Flight Readiness Review
`sim/review.py`, thresholds and text in `data/review_board.yaml`. Each reviewer is a rule set over the design bay's evaluation; no language model is involved.

| Area | Finding | Rule | Source |
|---|---|---|---|
| Launch and mass | Mass margin; launches; C3 | lien below 10%; lien at 4 of 4 launches; lien above 90% of the IMLEO the affordable rockets lift | MODEL |
| Power | Margin at 1.524 AU | lien below 10% | MODEL band; 1.524 AU from the NASA Mars Fact Sheet |
| Power | Life support with one string lost | **fail** if the array one string short cannot carry the load with the payload shed | MODEL rule; the load is measured by the flight model (`design.py` probe) |
| Power | Payload with one string lost | lien if one string short cannot carry the payload | MODEL |
| Communications | Data margin; conjunction dark days; room for an emergency pass | lien below 10%; noted; noted | MODEL |
| Crew health | Storm dose in the shelter | **fail** at 250 mSv, lien above 125 mSv | NASA-STD-3001 Vol. 1 Rev. B [V1 4031] (250 mSv per solar particle event, via OCHMO-TB-020); the half-limit lien is MODEL |
| Crew health | Cosmic-ray dose | lien (noted risk) above 600 mSv, never a failure | NASA-STD-3001 Vol. 1 Rev. B [V1 4030] |
| Crew health | Flare monitor; consumables | lien without a monitor; noted | MODEL; BVAD |
| Cost and schedule | Cost margin; days left in the window | lien below 10%; lien with fewer days left than one slip | MODEL |

**Projected storm dose (MODEL).** The board flies the chosen transfer analytically (no SPK) to place the scenario's reference storm, then integrates its own dose profile: quadratic rise from onset to peak, exponential decay with τ = 10 h, so D_raw = peak × (t_rise / 3 + τ). In the shelter: D_raw × exp(−(liner + shelter) / 12 g/cm²); outside: D_raw × exp(−liner / 12). Same factors as the flight (section 8). The game's storm is a replay of SOL2017-09-10, not NASA's design reference event (the October 1989 events), so the comparison with 250 mSv is illustrative.

**Projected cosmic-ray dose (MODEL).** 1.84 mSv/day (Zeitlin et al. 2013) × GCR factor × mission days (time of flight plus the 260-day stay). The default mission projects about 852 mSv: over the 600 mSv career limit, which is why every Mars design today carries this lien.

**A failure costs time.** The launch date slips 10 days (MODEL) with the same arrival date, and the porkchop is re-evaluated: in the default window C3 rises from 14.5 to 17.6, 23.9, 34.1 and 49.6 km²/s² with each slip, and IMLEO with it. A slip past the window's last day (2020-09-10) scrubs the mission; the next window is one synodic period later, 779.94 days (NASA Mars Fact Sheet), about 26 months.

**The lightweight array** (catalog `array_thin`, MODEL) is the board's teaching case: 30.5 kW at 1 AU and 2.6% margin at Mars, so the design bay accepts it, but it is wired as three large strings, so one failed string takes 34% of its output: 8.7 kW against the 9.6 kW life support needs. The board fails it. Every other array loses 18% per string (the flight model's default).

## 15. Design-to-flight callouts and counterfactuals
`sim/callouts.py`, text in `data/story/callouts.yaml`. Every counterfactual re-evaluates the same model formula with the other choice; the mission is never re-flown, and every counterfactual is labelled MODEL.

| Callout | When | Counterfactual |
|---|---|---|
| Storm shelter | after the storm (told at the next scene change) | dose = raw dose in the shelter × exp(−(liner + shelter′)/12) + raw dose outside × exp(−liner/12); `Radiation` keeps the two raw integrals |
| Power (distance) | TERN sheds the payload with every system healthy | the oversized array at today's distance: 42 kW / r² − load |
| Power (string) | TERN sheds the payload after a string loss | the oversized array one string short at 1.524 AU: 42 / 1.524² × 0.82 − load |
| Battery | same moment | hours bought scale with capacity at the same deficit: t × 90 / kWh, t × 40 / kWh |
| Emergency pass | Lantern books one | the continuous plan's extra DSN cost: DSN line / hours × (24 − hours) |
| Conjunction | the moratorium starts | dark days for an arrival 30 days earlier, same link model, same stay |
| Recorder | the blackout ends | the payload's daily data with and without the biggest instrument, or with a high-resolution camera added |
| Science milestone | 50% and 100% of the objective's data at Lantern | payload coverage without the instrument that serves the objective best |
| TERN | a rule is broken, or a rule set to Acts alone is used with a round trip over a minute | the round trip an Asks first rule would have waited |
| Lien came true | the power lien when TERN sheds the payload | (debrief only) |

**Causality.** A callout about something aboard exists only in the ship's knowledge at first. It reaches Lantern as its own downlink bundle (kind `callout`) through the normal network, so it waits for a pass and never crosses the Sun. The spectator screen is omniscient. At most one callout per scene appears on any one screen; all are kept for the debrief.

**The design record.** At launch the universe freezes the design as flown (`Universe.design_record`): every choice, the budgets, the liens, the derived factors. Callouts, the debrief and the fact sheet read only this record.

## References
- NAIF SPICE toolkit and generic kernels, NASA/JPL.
- E. M. Standish, *Keplerian Elements for Approximate Positions of the Major Planets*, JPL SSD.
- D. A. Vallado, *Fundamentals of Astrodynamics and Applications*; H. D. Curtis, *Orbital Mechanics for Engineering Students*.
- C. Zeitlin et al., "Measurements of Energetic Particle Radiation in Transit to Mars on the Mars Science Laboratory," *Science* 340(6136):1080–1084, 2013.
- J. Guo et al., arXiv:1803.00461 (2017-09-10 CMEs and SEPs at Mars); M. Ackermann et al. (Fermi-LAT), arXiv:1803.07654; V. Kurt et al., arXiv:1806.00226; arXiv:2004.00355.
- NASA-STD-3001 Vol. 1 Rev. B (2022); OCHMO Radiation Protection Technical Brief OCHMO-TB-020 ([V1 4030] career limit 600 mSv, [V1 4031] 250 mSv per solar particle event).
- NASA GSFC NSSDCA, Mars Fact Sheet (synodic period 779.94 days).
- NASA/JPL, "NASA's Mars Fleet Lies Low with Sun Between Earth and Red Planet," release 2021-200.
- IETF RFC 9171, Bundle Protocol Version 7.
- NASA, *Mars Reconnaissance Orbiter Launch* press kit (2005): 160 Gbit solid-state recorder.
- W. D. Compton and C. D. Benson, *Living and Working in Space: A History of Skylab*, NASA SP-4208 (1983).
- E. N. Parker, "Dynamics of the Interplanetary Gas and Magnetic Fields," *ApJ* 128:664, 1958.
