# OpenWave task breakdown

The [roadmap](README.md#-roadmap) says *what* each version delivers. This file says *how* — 68
tasks across 10 phases, in build order.

Phases are sequential by dependency, but tasks inside a phase can usually be picked up in
parallel. Anything marked 🟢 is a good entry point for a first contribution; anything marked 📡
needs physical hardware and is therefore **blocked on the community** — the maintainer cannot do
these.

**Legend** — ✅ done · 🚧 in progress · ⬜ not started · 🟢 good first issue · 📡 needs hardware

---

## Phase 0 — Publishable repository

| # | Status | Task | Detail |
|---|---|---|---|
| 1 | ✅ | `git init` + `main` branch | Python `.gitignore`, excludes IQ and TS captures |
| 2 | ✅ | `LICENSE` | MIT |
| 3 | ✅ | English README | Plus `README.fr.md` keeping the French version |
| 4 | ✅ | Community files | `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md` |
| 5 | ✅ | GitHub templates | Issues (bug / feature / **hardware report**) + PR template |
| 6 | ✅ | `pyproject.toml` | Packaging, dependencies, `ruff` + `mypy` + `pytest` config |
| 7 | ✅ | CI | Lint, format, types, tests on Python 3.11 and 3.12, package build |
| 8 | ✅ | `docs/` | `architecture.md` and `legal.md` |

## Phase 1 — Hardware abstraction

| # | Status | Task | Detail |
|---|---|---|---|
| 9 | ✅ | `SdrDevice` interface | ABC: `open`, `close`, `set_center_freq`, `set_sample_rate`, `read_samples` |
| 10 | ✅ | `device_manager` | Enumeration, selection, typed errors (`DeviceNotFoundError`, `DeviceBusyError`) |
| 11 | ✅ | **`MockSdrDevice`** | Synthetic IQ: FM carriers at chosen frequencies, controllable noise floor and SNR |
| 12 | ✅ 📡 | `RtlSdrDevice` | Real implementation via `pyrtlsdr`, written blind — needs validation |
| 13 | ✅ | IQ file I/O | Read and write `.cf32`, `.cu8` and `.cs16` with a metadata sidecar, so community captures can be replayed through the receiver interface |
| 14 | ✅ | `DvbDevice` interface | `tune`, `has_lock`, `read_ts`, quality metrics (SNR, BER) |
| 15 | ✅ | **`MockDvbDevice`** | Replays sample MPEG-TS files, simulates lock and metrics |

**What Phase 1 delivered.** `SdrDevice` and `DvbDevice` with the state and validation in the
base class, so a driver implements only the parts that touch hardware. `MockSdrDevice`, whose
output is fully determined by a seed and a position on its timeline — one read of 4096 samples
is bit-for-bit identical to two reads of 2048, noise included, so any failure reproduces from a
seed and an offset. `MockDvbDevice` replaying transport streams. Capture files in `.cf32`,
`.cu8` and `.cs16` with a metadata sidecar, replayable through the same interface as hardware.
A driver registry behind one `open_device("rtlsdr:1")` call. And `openwave devices` and
`openwave probe` to see it all from a terminal.

## Phase 2 — v0.1 · FM scan

| # | Status | Task | Detail |
|---|---|---|---|
| 16 | ✅ | `signal_detector` | Welch PSD, noise-floor estimation, **channel power** integration over the channel bandwidth. Not peak picking: wideband FM suppresses its own carrier, so peaks land on the deviation edges ([why](docs/architecture.md)) |
| 17 | ✅ | `frequency_manager` | FM plan 87.5–108 MHz, 100 kHz raster, segmentation by device bandwidth |
| 18 | ✅ | `scanner` | Sweep orchestration, strongest-first suppression of a station's spill into neighbouring channels, settling samples discarded after each retune |
| 19 | ✅ | `Station` model | `freq_hz`, `power_dbfs`, `snr_db`, `bandwidth_hz`, `stereo`, `name`. Levels are in dBFS, not dBm: a consumer SDR has no calibrated power reference, so an absolute figure would be invented precision |
| 20 | ✅ | Stereo detection | Via the 19 kHz pilot, measured against the multiplex either side of it |
| 21 | ✅ | Tests | Known stations injected and recovered exactly, with and without noise, including a weak station beside a strong one |
| 22 | ✅ | CLI `openwave scan fm` | Rich table in the terminal, plus JSON export |

**What Phase 2 delivered.** A sweep of the FM band that finds every station and invents none.
Two findings came out of measuring against the simulator rather than reasoning about it. Peak
picking does not work on wideband FM, which suppresses its own carrier and puts its peaks on the
deviation edges — channel power integration does, and the band plan assigns every channel to
exactly one window wide enough to measure it. And suppressing a channel because a stronger one
sits nearby is wrong when that neighbour is itself about to be discarded as spill: it lost a real
station at 98.0 MHz to a spill channel at 98.2 MHz. Accepting strongest-first fixed it. Measured
on an eight-station band, the scan finds 8 of 8 with no false positives, and the lower quartile
estimates the noise floor to within 0.2 dB where the median is 43 dB out.

## Phase 3 — v0.2 · FM demodulation and playback

| # | Status | Task | Detail |
|---|---|---|---|
| 23 | ✅ | `fm_demodulator` | Quadrature demodulation, decimation, 50 µs de-emphasis |
| 24 | ✅ | Stereo decoding | 19 kHz pilot, 38 kHz subcarrier, L/R matrixing |
| 25 | ✅ | Audio chain | Resample to 48 kHz, PCM output |
| 26 | ✅ | libVLC integration | `python-vlc` pulling a live stream through media callbacks. Verified against real libVLC, which CI installs |
| 27 | ✅ | Tests | Modulate a 1 kHz tone, demodulate, assert frequency and distortion |
| 28 | ✅ | CLI `openwave listen 98.0` | Tune and play one frequency, or `--record` it to a WAV file on a machine with no audio output |

**What Phase 3 delivered.** `openwave listen 98.0` tunes a station and plays it. Unlike the SDR
drivers, this path is genuinely verified: libVLC runs on an ordinary machine, so CI installs it
and the playback tests really start it, hand it a live stream and check that it decodes it.

Four findings, all from measurement:

- The demodulation rate has to be sized for the *modulated* signal, not the multiplex inside
  it. Carson's rule makes a full-deviation stereo transmission 270 kHz wide, so decimating to
  240 kHz clipped its outer sidebands, and the clipping imitated a stereo pilot well enough
  that a mono station was reported as stereo. 480 kHz leaves about 60 dB of margin.
- Filter lengths are derived from the transition width they have to achieve. A 129-tap
  low-pass at 15 kHz left the 19 kHz pilot almost untouched, so the "mono" output still
  carried it; designed properly it is rejected by over 100 dB.
- Blocks are demodulated with an overlap, and the amount of output to discard is measured
  rather than computed from a ratio. Without the overlap there is an audible tick nine times a
  second; with a ratio-derived discard count there is a repeated sample at every join.
- The output level comes from the deviation, not from each block's loudest sample, or the
  volume breathes in time with the blocks.

And one real bug: a read callback that waits indefinitely deadlocks `stop()`, because libVLC's
thread is inside the callback while `stop()` waits for that thread. It now gives up after a
timeout.

## Phase 4 — v0.3 · RDS decoding

| # | Status | Task | Detail |
|---|---|---|---|
| 29 | ✅ | **Synthetic RDS encoder** | Required to test without hardware: injects a real RDS signal into the mock |
| 30 | ✅ | 57 kHz extraction | Filtering, BPSK demodulation, clock recovery, differential decoding |
| 31 | ✅ | Error correction | 26-bit blocks, offset words, CRC |
| 32 | ✅ | Group parsing | PI, PTY, 0A/0B (station name), 2A (RadioText) |
| 33 | ✅ | Wire into the model | The RDS name fills `Station.name` during a scan |
| 34 | ✅ | Tests | Encoder → decoder round trip, name and RadioText recovered exactly |

**What Phase 4 delivered.** A scan now reports what stations are called. `openwave scan fm
--demo` shows OPENWAVE, CITY FM and the rest, read off a 57 kHz subcarrier rather than written
into the output.

Three things worth recording:

- **Synchronisation is acquired on undamaged blocks and kept with repair allowed.** The check
  word repairs a burst of up to five bits, which means about a third of all syndromes map to
  some correctable error — so a decoder that repairs while *searching* will repair noise into
  four plausible blocks in a row. Measured on pure noise before the fix: 22 groups, every one
  invented, with the station's identity different in each.
- **The subcarrier's phase is recovered from the data, not from the stereo pilot.** Locking to
  the pilot's third harmonic is the textbook approach and works only for stereo stations; a mono
  station carries RDS perfectly well. The data is real, so its samples lie along a line, and
  squaring them reveals the angle. Verified on a mono transmission with no pilot at all.
- **RDS is generated as one cached period rather than a stream.** Data has no closed-form
  integral, which the FM synthesiser needs to stay exactly reproducible — but it is periodic,
  so one period and its integral are computed once and indexed into. The simulator's
  bit-for-bit reproducibility under re-chunking survives with RDS switched on.

And one bug the tests caught: `rds_text` mapped everything outside printable ASCII to a space,
including the carriage return that marks where a RadioText message ends — so a short message was
never trimmed and appeared padded with the remains of the previous one.

## Phase 5 — v0.4 · DVB-T scan

| # | Status | Task | Detail |
|---|---|---|---|
| 35 | ⬜ 📡 | Linux DVB backend | `/dev/dvb/*` frontend ioctls, or `dvbv5` bindings |
| 36 | ⬜ | `dvb_scanner` | UHF plan 470–694 MHz, 8 MHz channels, tuning and lock detection |
| 37 | ⬜ | `mux_parser` | TS demultiplexing, PID filtering, section reassembly |
| 38 | ⬜ | PSI/SI tables | PAT, PMT, SDT, NIT parsing |
| 39 | ⬜ | `service_parser` | Name, type (TV/radio), LCN, audio/video PIDs, scrambled flag |
| 40 | ⬜ 🟢 | `Channel` model | `name`, `mux_freq_hz`, `service_id`, `type`, `lcn`, `scrambled` |
| 41 | ⬜ | Tests | Expected service list from sample TS fixtures |
| 42 | ⬜ 🟢 | CLI `openwave scan tv` | Rich table plus JSON export |

## Phase 6 — v0.5 · Stable API

| # | Status | Task | Detail |
|---|---|---|---|
| 43 | ⬜ | FastAPI + Pydantic | `/scan`, `/stations`, `/channels`, `/streams`, generated OpenAPI |
| 44 | ⬜ | `streams` endpoint | HTTP stream consumable by libVLC or an external player |
| 45 | ⬜ | WebSocket | Scan progress and live spectrum |
| 46 | ⬜ | Versioning | `/api/v1` prefix, documented contract |
| 47 | ⬜ | API tests | Entirely on simulated devices, CI-runnable |

## Phase 7 — v0.6 · Angular interface

| # | Status | Task | Detail |
|---|---|---|---|
| 48 | ⬜ | Angular scaffold | Structure, routing, dark theme |
| 49 | ⬜ | Typed API client | Generated from the OpenAPI schema |
| 50 | ⬜ | FM station view | List, frequency, strength, RDS name, Listen button |
| 51 | ⬜ | TV channel view | Grouped by multiplex, Watch button |
| 52 | ⬜ 🟢 | Signal meter | Strength and SNR gauge |
| 53 | ⬜ | Favourites | Server-side persistence |
| 54 | ⬜ | Frontend CI | Angular build verified, served statically by FastAPI |

## Phase 8 — v0.7 · Real-time spectrum

| # | Status | Task | Detail |
|---|---|---|---|
| 55 | ⬜ | Continuous PSD feed | Binary WebSocket frames, server-side decimation |
| 56 | ⬜ | Spectrum display | Canvas/WebGL, dB scale, station markers |
| 57 | ⬜ | Waterfall | Time cascade with a colour palette |
| 58 | ⬜ | Performance budget | Throughput and latency measured, regression-tested |

## Phase 9 — v1.0 · Documentation and packaging

| # | Status | Task | Detail |
|---|---|---|---|
| 59 | ⬜ | Docs site | MkDocs: install, usage, architecture, API, contributing |
| 60 | ⬜ 📡 | Compatibility matrix | Tested hardware, filled in by the community |
| 61 | ⬜ | Linux packaging | PyPI wheel plus AppImage or `.deb`, udev rules for RTL-SDR |
| 62 | ⬜ | Release process | `CHANGELOG.md`, publish workflow, version tags |

## Phase 10 — Community launch

| # | Status | Task | Detail |
|---|---|---|---|
| 63 | ⬜ | `good first issue` set | 8–10 issues with affected files and acceptance criteria |
| 64 | ⬜ | `hardware validation` issues | One per unvalidated driver (RTL-SDR, DVB-T) |
| 65 | ⬜ | Labels and milestones | `v0.1` through `v1.0` |
| 66 | ⬜ | Showcase | Real CI badges, CLI GIF, UI screenshots |
| 67 | ⬜ | GitHub topics | `sdr`, `rtl-sdr`, `fm-radio`, `dvb-t`, `signal-processing`, `python`, `angular` |
| 68 | ⬜ | Public announcement | r/RTLSDR, r/opensource, Show HN |

---

## Critical path

The tasks everything else waits on, in order:

**11** (`MockSdrDevice`) → **16** (`signal_detector`) → **18** (`scanner`) → **23**
(`fm_demodulator`) → **29** (synthetic RDS encoder) → **43** (API) → **49** (typed client)

Task 11 is the single most load-bearing item in the project. Without a trustworthy simulated
receiver, nothing can be tested, and with no hardware on hand, nothing can be verified at all.
