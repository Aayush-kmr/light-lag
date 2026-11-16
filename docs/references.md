# References

Everything the game ships with or quotes, in one place. Agency data sources and
how each is used are described in [data-provenance.md](data-provenance.md).

## Bundled assets

| Asset | Where | Source | Licence |
|---|---|---|---|
| Earth day map and ocean mask (Blue Marble), night lights (Black Marble 2012, VIIRS) | `clients/shared/assets/earth_*.jpg` | NASA Earth Observatory, copies from the MIT-licensed [three-globe](https://github.com/vasturiano/three-globe) project | NASA imagery, not copyrighted; credit NASA Earth Observatory |
| Mars colour map (Viking MDIM-based) | `clients/shared/assets/mars.jpg` | [NASA 3D Resources](https://github.com/nasa/NASA-3D-Resources) | Free for use; credit NASA |
| Yale Bright Star Catalogue, 5th revised ed., 9,096 stars | `clients/shared/assets/stars_bsc5.json` | Hoffleit & Warren (1991), Yale University Observatory, via HEASARC/CDS; JSON by [brettonw/YaleBrightStarCatalog](https://github.com/brettonw/YaleBrightStarCatalog) | Public scientific catalogue; credit the authors |
| NAIF SPICE kernels: `naif0012.tls`, `pck00011.tpc`, `de440s.bsp`, `earthstns_itrf93_201023.bsp`, `earth_topo_201023.tf` | fetched into `data/kernels/` by `tools/fetch_kernels.py` | [NASA/JPL NAIF](https://naif.jpl.nasa.gov/naif/data.html) | US Government work |
| `de421.bsp` (tests and CI only) | `skyfield-data` PyPI package | JPL, repackaged | US Government work |
| Fonts: Jost, IBM Plex Mono, Newsreader | `clients/shared/fonts/` | Google Fonts / Fontsource | SIL OFL 1.1 (licence files bundled) |
| Fonts: B612, B612 Mono | `clients/shared/fonts/` | [The B612 Project Authors](https://github.com/polarsys/b612) | SIL OFL 1.1 (licence files bundled) |
| three.js r187 and addons: OrbitControls, RoomEnvironment, RoundedBoxGeometry, RectAreaLightUniformsLib, RectAreaLightTexturesLib, EffectComposer, RenderPass, ShaderPass, MaskPass, OutputPass, UnrealBloomPass and their shaders | `clients/vendor/three/` | [three.js](https://github.com/mrdoob/three.js) | MIT (`clients/vendor/three/LICENSE`) |
| Sounds: quindar tones, radio static, cabin hum, alarms, clicks | generated at run time in `clients/shared/` and `server/lightlag/voice/` | Synthesized in code | Project code |
| Telemetry anomaly autoencoder | `data/models/anomaly.onnx` | Trained by the team on synthetic telemetry (`ml/anomaly`) | Project code |
| Spacecraft, cabin and mission control interiors, surface textures | `clients/shared/room*.js`, `clients/shared/viewport.js` | Procedural project code | Project code |

No downloaded 3D models, HDRIs, photos of people, or AI-generated images, video, music or voices are used.

## Design catalog sources (`data/catalog/sources.yaml`)

Every number in the design bay cites one of these keys, or is labelled MODEL
with its reasoning in the entry's note. A test fails if an entry cites a key
that is not listed here.

| Key | Source | Publisher |
|---|---|---|
| `nasa_mro_instruments` | [Mars Reconnaissance Orbiter: Science Instruments (NASA Science)](https://science.nasa.gov/mission/mars-reconnaissance-orbiter/science-instruments) | NASA |
| `hirise_cost` | [HiRISE (instrument cost, US$40 million)](https://www.wikiwand.com/en/HiRISE) | Wikipedia (secondary) |
| `odyssey_fact_sheet` | [Mars Odyssey's scientific objectives (NASA fact sheet, April 2001)](https://spaceflightnow.com/mars/odyssey/010405science.html) | NASA, reproduced by Spaceflight Now |
| `rad_hassler_2016` | [Hassler: Space Weather at Mars (MSL/RAD overview, Space Weather Workshop 2016)](https://www.swpc.noaa.gov/sites/default/files/images/u33/Hassler_Space_Weather_at_Mars_SWW_talk_29apr16_FINAL.pdf) | SwRI / NOAA SWPC |
| `rad_rafkin_2006` | [Rafkin: RAD - The Radiation Assessment Detector for MSL (NASA SRAG workshop 2006)](https://srag.jsc.nasa.gov/Publications/Workshop_2006-04/RAD_RAFKIN_2006.pdf) | NASA JSC SRAG |
| `sls_wikipedia` | [Space Launch System: payload capacity table](https://en.wikipedia.org/wiki/Space_Launch_System) | Wikipedia (secondary, cites NASA) |
| `oig_ig_22_003` | [NASA OIG IG-22-003: NASA's Management of the Artemis Missions (Nov 2021)](https://oig.nasa.gov/docs/IG-22-003.pdf) | NASA Office of Inspector General |
| `spacex_falcon_heavy` | [SpaceX: Falcon Heavy](https://spacex.com/vehicles/falcon-heavy) | SpaceX |
| `aiaa_falcon` | [AIAA Vehicle Guide: Falcon (launch prices, SpaceX 2020)](https://www.aiaa.org/vehicle-guide/vehicles/falcon) | AIAA |
| `elonx_falcon_heavy` | [Falcon Heavy Compendium (expendable list price)](https://elonx.net/?p=417) | ElonX (secondary) |
| `orbitalradar_falcon` | [Falcon Heavy / Falcon 9 specifications](https://orbitalradar.com/launch-vehicles/falcon-heavy) | Orbital Radar (secondary) |
| `bvad_rev1` | [Life Support Baseline Values and Assumptions Document, NASA/TP-2015-218570/REV1 (2018)](https://simoc.space/wp-content/uploads/2018/09/BVAD-20180501.pdf) | NASA |
| `bvad_calc` | [Life support mass-balance calculator (BVAD Rev1 default values)](https://lifesupportcalc.neocities.org/) | secondary, cites BVAD Rev1 |
| `ascend_2023_logistics` | [Logistics Beyond LEO (ASCEND 2023), consumable rates from BVAD Rev2](https://ntrs.nasa.gov/api/citations/20230013896/downloads/Logistics_BeyondLEO-ASCEND2023-Pres.pdf) | NASA NTRS |
| `ochmo_eclss` | [NASA-STD-3001 Technical Brief OCHMO-TB-002: ECLSS](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-002-eclss.pdf) | NASA OCHMO |
| `iss_water_98` | [NASA achieves water recovery milestone on International Space Station (June 2023)](https://phys.org/news/2023-06-nasa-recovery-milestone-international-space.html) | NASA, via phys.org |
| `sst_soa_power` | [NASA Small Spacecraft Technology State of the Art: Power](https://www.nasa.gov/smallsat-institute/sst-soa/power-subsystems) | NASA Small Spacecraft Systems Virtual Institute |
| `dra5_drake` | [Drake: Human Exploration of Mars Design Reference Architecture 5.0](https://ntrs.nasa.gov/api/citations/20100028285/downloads/20100028285.pdf) | NASA NTRS |
| `scan_mocs_0001` | [NASA's Mission Operations and Communications Services, SCaN-MOCS-0001 Rev 2 (2019), Appendix F: DSN aperture fee](https://essp.larc.nasa.gov/EVM-3/pdf_files/SCaN-MOCS-0001%20Rev%202_final.pdf) | NASA SCaN |
| `nssdc_mars_fact` | [Mars Fact Sheet (semimajor axis 1.524 AU; solar irradiance 0.431 of Earth's; synodic period 779.94 days)](https://nssdc.gsfc.nasa.gov/planetary/factsheet/marsfact.html) | NASA GSFC NSSDCA |
| `nasa_std_3001_tb020` | [NASA-STD-3001 Technical Brief OCHMO-TB-020: Design for Ionizing Radiation Protection (quotes NASA-STD-3001 Vol. 1 Rev. B [V1 4030] career limit 600 mSv and [V1 4031] solar particle event limit 250 mSv)](https://www.nasa.gov/wp-content/uploads/2023/03/radiation-protection-technical-brief-ochmo.pdf) | NASA Office of the Chief Health and Medical Officer |
| `mro_ssr` | [Mars Reconnaissance Orbiter arrival press kit (2006): 160 Gbit solid-state recorder](https://www.jpl.nasa.gov/news/press_kits/mro-arrival.pdf) | NASA / JPL |
