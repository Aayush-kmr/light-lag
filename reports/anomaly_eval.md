# Anomaly model evaluation

Conv1d autoencoder, 16,343 parameters, trained on nominal telemetry only.
Threshold: p99.8 of nominal validation scores; alert after 12 consecutive frames (120 s).

| group | precision | recall | TP | FP | FN |
|---|---|---|---|---|---|
| tune_types | 0.939 | 0.675 | 108 | 7 | 52 |
| held_out_types | 0.911 | 0.45 | 72 | 7 | 88 |
| all | 0.963 | 0.562 | 180 | 7 | 140 |

| fault type | group | n | recall | median latency (min) | false alarms |
|---|---|---|---|---|---|
| none | nominal | 160 | - | - | 7 |
| scrubber_valve_stuck | tune | 40 | 0.0 | - | 0 |
| pump_a_degradation | tune | 40 | 0.7 | 15.5 | 0 |
| bus_short | tune | 40 | 1.0 | 1.8 | 0 |
| hga_drift | tune | 40 | 1.0 | 17.9 | 0 |
| array_string_loss | held-out | 40 | 0.0 | - | 0 |
| heater_stuck_on | held-out | 40 | 1.0 | 22.2 | 0 |
| sensor_stuck_ppco2 | held-out | 40 | 0.0 | - | 0 |
| sensor_drift_loop_b | held-out | 40 | 0.8 | 41.2 | 0 |

Synthetic data from the game's own ship model. These numbers say the detector works on this simulator, not on spacecraft.
