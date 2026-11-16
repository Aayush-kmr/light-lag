# Data provenance

Rule: **real data only, never fabricated.** Where real data is unavailable at runtime, the game says so, using a labelled fallback or skipping the beat. It never invents values. The machine-readable registry is `data/provenance.yaml`, which the in-game *Where the data comes from* drawer renders.

## NASA and agency data

| Data | Used for | Source | Terms | Status |
|---|---|---|---|---|
| **de440s.bsp** (JPL DE440, short) | Earth/Mars/Sun positions, light time, SEP angle, porkchop | NAIF `generic_kernels/spk/planets/` | NASA/JPL public | fetch: `tools/fetch_kernels.py` |
| **naif0012.tls** | UTC ↔ ET | NAIF `lsk/` | public | fetch |
| **pck00011.tpc** | body radii, IAU_EARTH | NAIF `pck/` | public | fetch |
| **earthstns_itrf93_201023.bsp** + **earth_topo_201023.tf** | DSN station positions and topocentric frames | NAIF `spk/stations/`, `fk/stations/`. Filenames confirmed from NAIF's kernel comments, which cite *DSN No. 810-005, 301, Rev. M*. NAIF has since also released `earthstns_fx_260814.bsp` (Aug 2026, `EARTH_FIXED` alias) | public | fetch |
| **Earth high-precision PCK** (`earth_latest_high_prec.bpc` or `earth_000101_yymmdd_yymmdd.bpc`) | ITRF93 orientation for station pointing | NAIF `pck/`. The exact current filename was **not confirmed** in this build; the fetch script falls back to the newest `earth_*.bpc` in the listing | public | fetch |
| Standish approximate elements | Offline fallback ephemeris | JPL SSD *approx_pos* | public | bundled in code |
| **DONKI** FLR / CME / SEP | Storm replay | `api.nasa.gov/DONKI/*` (key) or the CCMC mirror | NASA open API | fetch: `tools/fetch_donki.py`; raw JSON cached verbatim |
| **DSN Now** | Ambient "real world" panel at Lantern | `eyes.nasa.gov/dsn/data/dsn.xml` (~5 s updates) | NASA/JPL-Caltech; credit shown | fetch: `tools/fetch_dsn_now.py` + live refresh |
| **MSL RAD cruise dose**, 1.84 mSv/day | GCR baseline | Zeitlin et al., *Science* 340:1080 (2013) | cited | used |
| **NASA-STD-3001** 600 mSv career, 250 mSv per SPE | Debrief context | NASA OCHMO technical brief | cited | used |
| 2021 Mars conjunction moratorium (Oct 2–16) | Validation of our SEP model | NASA/JPL release 2021-200 | cited | used in tests and docs |
| **JSC Mission Transcript Collection** (raw technical air-to-ground, e.g. `AS11_TEC.PDF`) | Fine-tune register | JSC History Portal | NASA work; "no copyright is asserted" (NASA ALSJ, raw transcripts). **Corrected ALSJ/AFJ texts excluded (copyrighted).** | fetch: `ml/finetune/corpus.py fetch` |
| **Blue Marble** (land, shaded relief, ocean) and **Black Marble 2012** (VIIRS city lights), 4096 × 2048 | Earth day map, night lights, ocean mask for sun glint (`clients/shared/assets/earth_*.jpg`) | NASA Earth Observatory imagery, taken from the copies in the MIT-licensed [three-globe](https://github.com/vasturiano/three-globe/tree/master/example/img) project | NASA imagery, not copyrighted; credit NASA Earth Observatory | used |
| **Mars colour map** (Viking MDIM-based), 1440 × 720 | Mars surface (`mars.jpg`), oriented by the IAU 2015 pole and rotation | [NASA 3D Resources](https://github.com/nasa/NASA-3D-Resources), Images and Textures / Mars | free for use; credit NASA | used |
| **Yale Bright Star Catalogue**, 5th revised ed. (Hoffleit & Warren 1991), 9,096 stars | Every star in the 3D sky at its J2000 direction, brightness and colour (`stars_bsc5.json`, 340 kB) | Yale University Observatory via HEASARC/CDS; JSON by [brettonw/YaleBrightStarCatalog](https://github.com/brettonw/YaleBrightStarCatalog) | public scientific catalogue; credit the authors | used (replaces the planned SVS Deep Star Maps: points stay sharp at any zoom) |
| **NASA-STD-3001 tech brief OCHMO-TB-020** | Review board crew-health thresholds: 250 mSv per solar particle event, 600 mSv career | nasa.gov | NASA public | cited (`data/review_board.yaml`) |
| Mars MOLA elevation | A normal map for Mars | NASA/JPL/USGS Astrogeology. **URL to confirm** | public domain, credit | planned |

Fonts (not data, but shipped): Jost, IBM Plex Mono and Newsreader, all SIL Open Font License 1.1, vendored with their licences in `clients/shared/fonts/`. three.js r187 is MIT.

## Design catalog (`data/catalog/`)
The mission-design choices live in YAML, one file per kind, and every entry lists the sources it draws on (`sources.yaml` defines each key; a test fails if an entry cites a key that doesn't exist). Fields that are engineering assumptions are listed in the entry's `model:` and labelled MODEL in the game.

| File | What | Sourced from |
|---|---|---|
| `launch_vehicles.yaml` | LEO capacity and price per launch for the assembly campaign | SLS: NASA rating via Wikipedia, price from NASA OIG IG-22-003 ($2.2 B rocket, $4.1 B full system). Falcon Heavy / Falcon 9: SpaceX, AIAA vehicle guide |
| `instruments.yaml` | Science payload, each modelled on a flown instrument (mass, power) | HiRISE, CTX, CRISM: NASA MRO instrument pages. GRS, THEMIS, MARIE: NASA Mars Odyssey fact sheet. RAD: SwRI / NASA SRAG |
| `crew.yaml` | Crew of four, seats, per-crew-day consumables, water recovery | NASA BVAD (Rev1, Rev2 via ASCEND 2023), NASA-STD-3001 OCHMO-TB-002, ISS 98% water recovery (NASA, 2023) |
| `power.yaml` | Solar arrays and batteries | NASA Small Spacecraft Technology State of the Art (specific power, Wh/kg); array and battery masses MODEL |
| `objectives.yaml` | What the mission is for | MODEL (game design) |

Consumables are computed, not assumed: crew × (transit + stay + 30-day reserve) × 2.49 kg per crew-day (expendables plus make-up water and oxygen the 98%-recovery loop can't close). The ship's CO2 build-up follows from the BVAD output of 1.04 kg CO2 per crew-day in a 168 m³ cabin: about 0.43 mmHg per hour unscrubbed.

## Literature values (used only without a DONKI cache)
`data/reference/sep_events.yaml` transcribes the 2017-09-10 X8.2 event (start 15:35 UT, peak 16:06 UT, S08W88, CME 3620 km/s, release 15:53–15:55 UT, GLE onset 16:06–16:08 UT) and the 2017-09-06 X9.3 event (start 11:53, peak 12:02, S09W34). Each value is cited. Values not in a cited source (e.g. the X9.3 CME speed) are left `null`, never guessed.

## Development-only data
`de421.bsp` via the `skyfield-data` PyPI package (JPL ephemeris, repackaged) lets the SPICE code path run in CI and on machines without NAIF access. The game prefers `de440s.bsp` whenever present.

## Not data
The ship model, the SEP dose-profile shape, the shielding factors, the mass model and the link-margin model are **engineering assumptions**, documented in `docs/science.md` and labelled MODEL in-game. Story text is fiction, written by the team.

## Attribution string (put on the credits screen)
> Ephemerides: NASA/JPL NAIF SPICE (DE440). Space weather: NASA CCMC DONKI. DSN Now: NASA/JPL-Caltech. Radiation: MSL/RAD (Zeitlin et al. 2013). Transcripts: NASA Johnson Space Center History Portal. Earth imagery: NASA Earth Observatory (Blue Marble, Black Marble). Mars imagery: NASA 3D Resources. Stars: Yale Bright Star Catalogue (Hoffleit & Warren).
