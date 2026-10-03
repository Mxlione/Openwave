# OpenWave task breakdown

The [roadmap](README.md#-roadmap) says *what* each version delivers. This file says *how* — 68
tasks across 10 phases, in build order.

Phases are sequential by dependency, but tasks inside a phase can usually be picked up in
parallel. Anything marked 🟢 is a good entry point for a first contribution; anything marked 📡
needs physical hardware and is therefore **blocked on the community** — the maintainer cannot do
these.

**Legend** — ✅ done · 🚧 in progress · ⬜ not started · 🟡 written, waiting on the maintainer to act outside the repository · 🟢 good first issue · 📡 needs hardware

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
| 35 | ✅ 📡 | Linux DVB backend | `/dev/dvb/*` frontend ioctls, or `dvbv5` bindings |
| 36 | ✅ | `dvb_scanner` | UHF plan 470–694 MHz, 8 MHz channels, tuning and lock detection |
| 37 | ✅ | `mux_parser` | TS demultiplexing, PID filtering, section reassembly |
| 38 | ✅ | PSI/SI tables | PAT, PMT, SDT, NIT parsing |
| 39 | ✅ | `service_parser` | Name, type (TV/radio), LCN, audio/video PIDs, scrambled flag |
| 40 | ✅ | `Channel` model | `name`, `mux_freq_hz`, `service_id`, `type`, `lcn`, `scrambled` |
| 41 | ✅ | Tests | Expected service list from sample TS fixtures |
| 42 | ✅ | CLI `openwave scan tv` | Rich table plus JSON export |

**What Phase 5 delivered.** `openwave scan tv --demo` tunes twenty-eight UHF channels, locks
onto three multiplexes and lists nine services by the number a viewer would type — television
and radio told apart, and the one scrambled service listed and marked rather than quietly
dropped.

As with RDS, the testable part came first: there was no transport stream to work with, so one
had to be built. `tv/synthesis.py` writes real 188-byte packets with correct CRC-32s and
repeating tables, from the standards rather than from the parser, which is what makes the round
trip prove something.

Points worth recording:

- **Every length in a transport stream comes off the air.** An adaptation field can claim to run
  past the end of its packet, a section pointer can point outside its payload, and a section can
  claim to be longer than the standard permits. All three are bounded here, because believing
  any of them lets a transmitter decide how much memory a receiver allocates or read past the
  end of a buffer.
- **The PMT PIDs are only discoverable from the PAT**, so the parser starts listening to PIDs it
  did not know about a moment earlier. It also stops as soon as the tables are complete:
  measured on the demonstration multiplex, eight packets out of forty-eight.
- **A service in one table and not another is normal, not an error.** A multiplex gets
  reconfigured while on the air, so a scan can easily catch an SDT from before a change and a
  PAT from after it. The join is deliberately generous; dropping either side would make a
  channel vanish for reasons a viewer cannot see.
- **The frequency recorded is the tuner's, not the NIT's.** The NIT says where a transmitter
  claims to be; the tuner knows where it actually heard it.
- **MPEG's CRC-32 is not the familiar one.** Same polynomial, bits fed the other way, starting
  from all ones, no final inversion. Using zlib's would reject every real transmission.

The Linux DVB driver is the riskiest code in the project and the one part a test cannot really
check: it lays out kernel structures byte by byte, and a field in the wrong place does not raise
— it tunes to the wrong frequency. Its module docstring lists what to verify first.

## Phase 6 — v0.5 · Stable API

| # | Status | Task | Detail |
|---|---|---|---|
| 43 | ✅ | FastAPI + Pydantic | `/scan`, `/stations`, `/channels`, `/streams`, generated OpenAPI |
| 44 | ✅ | `streams` endpoint | HTTP stream consumable by libVLC or an external player |
| 45 | ✅ | WebSocket | Scan progress. The live spectrum arrives with v0.7 (task 55) |
| 46 | ✅ | Versioning | `/api/v1` prefix, documented contract |
| 47 | ✅ | API tests | Entirely on simulated devices, CI-runnable |

**What Phase 6 delivered.** `openwave serve` runs an HTTP API under `/api/v1`, with the schema
generated from the same Pydantic models the rest of the code uses — which is what the Angular
client will be generated from, and what stops the two drifting apart.

Decisions worth recording:

- **A scan is a job, not a request.** An FM sweep takes seconds and a television sweep up to a
  minute. A request that blocks for a minute times out in proxies, cannot report progress, and
  leaves a client with nothing to show. So `POST /scans` returns an identifier at once, and the
  result is collected afterwards or watched over a WebSocket.
- **One scan at a time, and the second is refused rather than queued.** A receiver cannot be
  tuned to two places at once, and queuing would leave somebody waiting on a scan they did not
  ask for.
- **The device listing says which drivers have never touched hardware.** A client should be able
  to say so rather than implying a reading is trustworthy.
- **The audio stream can be bounded.** `?seconds=2` gives a finite clip whose header states its
  length, which is both a real feature and the only way the endpoint is testable: an endless
  HTTP response cannot be completed, so a test client waits for ever.
- **The scanning runs in a worker thread**, because it is ordinary blocking NumPy code. NumPy
  releases the interpreter lock for the work that matters, and rewriting the signal processing to
  yield would make it harder to read for no benefit.

## Phase 7 — v0.6 · Angular interface

| # | Status | Task | Detail |
|---|---|---|---|
| 48 | ✅ | Angular scaffold | Structure, routing, dark theme |
| 49 | ✅ | Typed API client | Generated from the OpenAPI schema |
| 50 | ✅ | FM station view | List, frequency, strength, RDS name, Listen button |
| 51 | ✅ | TV channel view | Grouped by multiplex, Watch button |
| 52 | ✅ | Signal meter | Strength and SNR gauge |
| 53 | ✅ | Favourites | Server-side persistence done; the interface for them comes with the Angular views |
| 54 | ✅ | Frontend CI | Angular build verified, served statically by FastAPI |

**What Phase 7 delivered.** An interface at the same address as the API: a station list with
favourites and a play button, a channel list grouped by multiplex, and a live spectrum with a
waterfall. Three views, loaded on demand, 83 kB over the wire for the first one.

Decisions worth recording:

- **The API types are generated from the backend's own schema**, which is checked in. A change
  to a response in Python becomes a compile error in the interface rather than a field that is
  quietly `undefined` at run time, and CI fails if the schema and the generated types drift
  apart.
- **One process serves both.** No second port, no proxy configuration, no cross-origin rules,
  and the interface derives its own API address from the page it was loaded from. The catch-all
  that makes browser routes work is careful to exclude the API prefix: handing a client HTML to
  parse as JSON turns a typo into a baffling error a long way from its cause.
- **The spectrum is drawn on a canvas.** A thousand bins twenty times a second is twenty
  thousand DOM updates a second. The waterfall scrolls its own pixels up by one row rather than
  redrawing its history, which does not change.
- **The waterfall's colour scale is pinned, not automatic.** Following the loudest bin uses the
  full range but makes an unchanging signal appear to change colour as something else comes and
  goes elsewhere in the span.
- **Audio is a URL.** The browser's own audio element pulls the stream, so nothing is decoded in
  the interface and VLC on another machine can play the same address.
- **What does not work is said where somebody will see it.** The footer reports the version, how
  many receivers and tuners were found, whether playback is available, and whether the RTL-SDR
  driver is installed — because the honest answer to "why does the scan find nothing?" is
  usually one of those.

Two environment notes for contributors. The Angular CLI installed globally on the maintainer's
machine was version 14, which refuses Node 20; the project pins its own CLI in
`devDependencies`, which is what a repository should do anyway. And the current Angular CLI
needs Node 22, so the project is on Angular 20, which accepts Node 20.19 and above.

## Phase 8 — v0.7 · Real-time spectrum

| # | Status | Task | Detail |
|---|---|---|---|
| 55 | ✅ | Continuous PSD feed | Binary WebSocket frames at 1 kB each, averaged down server-side rather than decimated — a decimated spectrum misses narrow signals, which on a display looks like a station flickering |
| 56 | ✅ | Spectrum display | Canvas/WebGL, dB scale, station markers |
| 57 | ✅ | Waterfall | Time cascade with a colour palette |
| 58 | ✅ | Performance budget | Throughput and latency measured, regression-tested |

**What Phase 8 delivered.** The feed, the display and a budget that fails if either gets
slower. Measured on an ordinary desktop:

| | |
|---|---|
| Frame on the wire | 1064 bytes |
| At 20 frames a second | 20.8 kB/s |
| Samples averaged per frame | 65 536, which is 27 ms at 2.4 MS/s |
| Encode / decode one frame | 11 µs / 3 µs |
| Produce one frame | 21.7 ms |
| Sustainable rate | 46 frames a second, against the 20 the feed asks for |

The budget is a test rather than a note, because this is the one part of OpenWave where being
slow makes it wrong: a waterfall that falls behind is showing the past, and below about ten
frames a second it stops reading as motion. The limits are set at roughly three times the
measured cost — wide enough not to fail on a loaded runner, narrow enough to catch an accidental
copy of every frame or an FFT that grew by a factor of ten.

## Phase 9 — v1.0 · Documentation and packaging

| # | Status | Task | Detail |
|---|---|---|---|
| 59 | ✅ | Docs site | MkDocs: install, usage, architecture, API, contributing |
| 60 | ✅ 📡 | Compatibility matrix | Tested hardware, filled in by the community |
| 61 | ✅ | Linux packaging | PyPI wheel plus AppImage or `.deb`, udev rules for RTL-SDR |
| 62 | ✅ | Release process | `CHANGELOG.md`, publish workflow, version tags |

### What Phase 9 delivered

**The documentation site is ten pages and `mkdocs build --strict` passes**, which means a link
to a page that no longer exists fails the build rather than becoming a dead link somebody finds
later. A CI job builds it on every pull request and deploys it to Pages from `main`.

**The docs dependency is bounded to `mkdocs>=1.6,<2`.** Not caution for its own sake: the
Material theme's own notice says MkDocs 2.0 removes the plugin system with no migration path, so
an unbounded range would break the site the day 2.0 ships.

**The version now lives in one place.** It was declared twice, in `pyproject.toml` and in
`src/openwave/__init__.py`. Nothing enforced that they agreed, so the first release to touch one
and forget the other would have shipped a wheel whose filename disagreed with what
`openwave version` printed. Hatchling now reads it from the module, and the release workflow
refuses to build if the git tag disagrees with it.

**A published wheel has to carry the interface**, because somebody installing from PyPI has no
Node. The release workflow builds the Angular app and copies it into `src/openwave/interface`
before hatchling runs, and `src/openwave/api/app.py` looks in two places: that directory in an
installed package, or `frontend/dist` in a checkout. The directory is in `.gitignore` — it is
build output, and committing it would have been the same class of mistake as commit `0373fd1`,
which claimed to add the interface and carried no code.

**The release smoke test runs the wheel rather than importing it.** It installs into a clean
virtual environment, runs `openwave scan fm --demo --json` and asserts that stations were found
*and named* (a named station means the whole chain down to RDS ran), then starts the server and
fetches both `/api/v1/health` and `/`. Writing it caught three of my own errors: `--version` and
`scan --demo` are not the commands (`version` and `scan fm --demo` are), and `TestClient` needs
a test-only dependency that a released wheel does not install. Each would have failed the first
real release.

**Publishing to PyPI is behind a GitHub environment that does not exist yet**, so pushing a tag
cannot publish by accident. Creating it, with a required reviewer, is a deliberate act.

**The compatibility matrix is honest about being empty.** Every receiver row says *not tested*,
because no receiver has ever been attached. The table's job is to make that a visible gap
somebody can close, with the DVB driver flagged as the riskiest code in the project — it lays
out kernel structures byte by byte, and a field in the wrong place does not raise an error, it
silently tunes somewhere else.

## Phase 10 — Community launch

| # | Status | Task | Detail |
|---|---|---|---|
| 63 | ✅ | `good first issue` set | 10 issues with affected files and acceptance criteria |
| 64 | ✅ | `hardware validation` issues | One per unvalidated driver (RTL-SDR, DVB-T) |
| 65 | ✅ | Labels and milestones | Ten labels, and milestones for the work after v1.0 |
| 66 | ✅ | Showcase | Real CI badges, generated CLI and interface pictures |
| 67 | 🟡 | GitHub topics | Listed in `.github/TOPICS.txt`; a repository setting, so set by hand |
| 68 | 🟡 | Public announcement | Drafted in `.github/ANNOUNCEMENT.md`, to be posted by the maintainer |

### What Phase 10 delivered

**Ten starter issues and two hardware validation issues**, written from the code rather than
imagined. Each names the files it touches, says what done looks like, and warns about the trap
it contains — the SigMF one about `core:datatype` not matching OpenWave's format names, the
noise floor one about dBFS not being dBm. Every gap they describe was checked against the source
first: `rds_text` really does drop accents, there really is no `--deemphasis` option although
the demodulator takes the parameter, there really is no OIRT band plan, and the interface really
does not use `localStorage`.

**A test holds them to that.** `tests/test_seed_issues.py` checks that every file an issue
points at exists, that every label it uses is declared, and that every milestone it names is
real. An issue that sends a newcomer to a file that was renamed wastes the time of the one
person the project most needs to keep.

**Nothing was created on GitHub from here.** `.github/workflows/bootstrap.yml` creates the
labels, milestones and issues, and only when somebody presses the button — it defaults to a dry
run, and it is safe to run twice because it matches on name and title and never closes or
renames anything. Writing it caught two bugs in my own script: splitting `gh api --paginate`
output on brackets comes apart on the nested arrays GitHub puts in an issue's labels, and
`gh api -f` sends a milestone number as a string, so the body now goes in as JSON on standard
input.

**Topics and the announcement are deliberately not automated.** A workflow's token cannot change
repository settings, so the topics are listed in `.github/TOPICS.txt` to be set by hand. The
announcement is drafted for three audiences in `.github/ANNOUNCEMENT.md` and posted by nobody but
the maintainer: it goes out under a person's name, to communities that can tell.

---

## Critical path

The tasks everything else waits on, in order:

**11** (`MockSdrDevice`) → **16** (`signal_detector`) → **18** (`scanner`) → **23**
(`fm_demodulator`) → **29** (synthetic RDS encoder) → **43** (API) → **49** (typed client)

Task 11 is the single most load-bearing item in the project. Without a trustworthy simulated
receiver, nothing can be tested, and with no hardware on hand, nothing can be verified at all.
