# 📡 OpenWave

> Open source platform to scan, detect and play publicly broadcast radio and TV services.

[![CI](https://github.com/Mxlione/Openwave/actions/workflows/ci.yml/badge.svg)](https://github.com/Mxlione/Openwave/actions/workflows/ci.yml)
![Status](https://img.shields.io/badge/status-pre--alpha-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![Contributions](https://img.shields.io/badge/contributions-welcome-brightgreen)

*Read this in [French](README.fr.md).*

OpenWave is not another VLC skin. It is a platform that **scans frequencies**, **detects the
services actually on the air** (FM stations, digital TV channels) and **presents them in a clear
interface**, ready to listen to or watch.

---

## 📸 What it looks like

A scan of the simulated band, with no receiver attached. Every picture here comes from running
the real program — `scripts/capture_cli.py` and `scripts/capture_interface.py` regenerate them.

![openwave scan fm --demo](docs/images/cli-scan-fm.svg)

| | |
|---|---|
| ![The station list](docs/images/ui-stations.png) | ![The live spectrum](docs/images/ui-spectrum.png) |
| The stations a scan found, with signal, mode and RDS name | The spectrum and waterfall, live over a WebSocket |

![Television channels](docs/images/ui-channels.png)

---

## 🎯 Goals

- Scan the FM band automatically and detect the stations present
- Scan DVB-T multiplexes and list their channels and services
- Show frequency, signal strength and the live spectrum
- Start listening or watching in one click through libVLC
- Stay modular: a new receiver or a new format plugs in without touching the rest

---

## ✨ The core idea

Plug in a supported receiver (RTL-SDR or a DVB-T tuner), run a scan, then pick what you want to
listen to or watch.

```
RTL-SDR / DVB-T tuner
          ↓
      OpenWave
          ↓
       [SCAN]
          ↓
┌─────────────────────────┐
│ 📻 FM                   │
│  88.1 MHz   Station A   │
│  92.4 MHz   Station B   │
│ 100.7 MHz   Station C   │
│                         │
│ 📺 TV                   │
│ Channel 1               │
│ Channel 2               │
│ Channel 3               │
└─────────────────────────┘
```

Clicking **Watch** or **Listen** hands the stream to libVLC.

---

## 🚦 Project status

OpenWave is in early development. Nothing is released yet.

| Area | Status |
|---|---|
| Repository, CI, contribution guide | 🟢 in place |
| Hardware abstraction layer (`sdr/`) | 🟢 in place |
| Simulated devices (synthetic IQ, MPEG-TS replay) | 🟢 in place |
| FM band scan (v0.1) | 🟢 in place |
| FM demodulation and playback (v0.2) | 🟢 in place |
| RDS decoding (v0.3) | 🟢 in place |
| DVB-T scan (v0.4) | 🟢 in place |
| HTTP API (v0.5) | 🟢 in place |
| Angular interface (v0.6) | 🟢 in place |
| Real-time spectrum (v0.7) | 🟢 in place |
| Documentation site and packaging (v1.0) | 🟢 in place |
| Validation on real hardware | 🔴 nobody has tried |

**Honest note on hardware.** The maintainer currently has no RTL-SDR dongle and no DVB-T tuner.
Every signal-processing stage is therefore developed and tested against **simulated devices** —
synthetic IQ for FM and RDS, recorded transport streams for DVB-T — which run in CI on every
commit. The real drivers are written against the vendor APIs but are **not yet validated on
physical hardware**. If you own a receiver, validating a driver is the single most valuable
contribution you can make right now: see the issues labelled `hardware validation`.

---

## 🏗️ How it fits together

```
                       ┌──────────────────────┐
                       │      OPENWAVE        │
                       │   OPEN SOURCE        │
                       └──────────┬───────────┘
                                  │
                            📡 ANTENNA
                                  │
                   ┌──────────────┴──────────────┐
                   │                             │
              📻 FM RADIO                    📺 TV
                   │                             │
           ┌───────▼───────┐             ┌───────▼──────┐
           │   SDR / FM    │             │  DVB tuner / │
           │    tuner      │             │     SDR      │
           └───────┬───────┘             └───────┬──────┘
                   │                             │
                   ▼                             ▼
            🔎 FM detection              📦 TV multiplex (MUX)
                   │                             │
                   ▼                             ▼
           ┌───────────────┐             🔎 Channel / service
           │ Frequency     │                 detection
           │ Strength      │                     │
           │ Stereo        │                     ▼
           │ RDS*          │             ┌───────────────┐
           └───────┬───────┘             │ TV 1 · TV 2   │
                   │                     │ TV 3 · Radio 1│
                   ▼                     └───────┬───────┘
              🎵 AUDIO                           │
                   │                             │
                   └──────────────┬──────────────┘
                                  ▼
                       ┌──────────────────────┐
                       │       PLAYBACK       │
                       │  WAV framing         │
                       │  libVLC              │
                       └──────────┬───────────┘
                                  ▼
                       ┌──────────────────────┐
                       │     OPENWAVE UI      │
                       │ 📻 FM stations       │
                       │ 📺 TV channels       │
                       │ 📊 Signal / frequency│
                       │ 📡 Spectrum          │
                       │ ⭐ Favourites        │
                       └──────────┬───────────┘
                                  ▼
                           ▶️ VLC / libVLC
                                  ▼
                       🎧 Audio  /  📺 Video
```

\* RDS: the station name and extra data carried alongside the FM signal.

---

## 🧩 Source layout

```
OpenWave/
│
├── src/openwave/
│   ├── core/              # Scanning engine, independent of any band
│   │   ├── frequency_manager.py  # Band plans, and splitting one into tuner-sized segments
│   │   ├── scanner.py            # Sweeping a band and collecting the measurements
│   │   ├── signal_detector.py    # Power spectrum, channel power, occupancy
│   │   └── units.py              # dBFS, frequency formatting
│   │
│   ├── radio/             # FM radio
│   │   ├── band.py               # FM band plans
│   │   ├── constants.py          # Deviation, pilot, de-emphasis
│   │   ├── demo.py               # An invented band, for trying it with no receiver
│   │   ├── fm_demodulator.py     # Quadrature demodulation, stereo, de-emphasis
│   │   ├── listener.py           # Continuous audio from a tuned station
│   │   ├── station.py
│   │   ├── station_detector.py   # Scan, then identify each station found
│   │   ├── synthesis.py          # Synthetic transmissions, for testing without hardware
│   │   └── rds/                  # RDS: blocks, groups, encoder, decoder
│   │
│   ├── tv/                # Digital television
│   │   ├── band.py               # UHF and VHF channel plans
│   │   ├── channel.py
│   │   ├── demo.py               # Invented multiplexes
│   │   ├── dvb_scanner.py
│   │   ├── mux_parser.py         # Collecting a multiplex's tables as they arrive
│   │   ├── psi.py                # PSI sections, their CRC and reassembly
│   │   ├── service_parser.py
│   │   ├── synthesis.py          # Synthetic multiplexes, for testing without a tuner
│   │   ├── tables.py             # PAT, PMT, SDT, NIT
│   │   └── ts.py                 # The 188-byte transport stream packet
│   │
│   ├── sdr/               # Receiver access
│   │   ├── device.py             # SdrDevice / DvbDevice interfaces
│   │   ├── device_manager.py     # Opening a receiver from a name like "rtlsdr:1"
│   │   ├── errors.py
│   │   ├── iq_file.py            # Recording and replaying captures
│   │   ├── linux_dvb.py          # Linux DVB API — never run on hardware
│   │   ├── mock.py               # Simulated receivers, used by the whole test suite
│   │   └── rtl_sdr.py            # RTL-SDR — never run on hardware
│   │
│   ├── media/             # Playback
│   │   ├── libvlc.py             # A live stream into libVLC through media callbacks
│   │   ├── stream.py
│   │   └── wav.py
│   │
│   ├── api/               # HTTP + WebSocket API
│   │   ├── app.py                # Routes, served under /api/v1
│   │   ├── models.py             # Request and response shapes
│   │   ├── scans.py              # Scans as background jobs
│   │   ├── streams.py            # Audio over HTTP
│   │   ├── spectrum.py           # Spectrum frames over a WebSocket
│   │   └── favourites.py         # Remembered stations and channels
│   │
│   └── cli.py             # `openwave` command line
│
├── frontend/              # Angular interface
├── scripts/               # Schema export, and the screenshots in this README
├── packaging/             # udev rules, Debian recipe
├── tests/
└── docs/                  # The documentation site
```

| Module | Role |
|---|---|
| `core` | Sweeps frequencies, measures signal, detects and groups the services found |
| `radio` | Demodulates FM, decodes RDS, identifies stations |
| `tv` | Scans DVB channels, reads multiplexes and extracts their services |
| `sdr` | Hides the hardware behind one common interface (RTL-SDR, Linux DVB, recorded captures, simulated) |
| `media` | Wraps a live stream as WAV and plays it through libVLC |
| `api` | Exposes scans, stations, channels and streams to the interface |
| `frontend` | Angular interface: lists, signal, spectrum, favourites |

---

## 🧰 Technology

- **Receivers**: RTL-SDR and Linux DVB-T tuners, plus recorded captures and a
  simulator. SoapySDR, which would bring HackRF and Airspy, is **not implemented** —
  it is [issue material](docs/compatibility.md), not a current capability.
- **Signal processing**: NumPy / SciPy — FM demodulation, RDS decoding, spectrum analysis
- **Media**: libVLC through `python-vlc`, pulling a live stream through media callbacks
- **API**: FastAPI, local service exposing scan results and streams
- **Interface**: Angular

---

## 🗺️ Roadmap

- [x] **v0.1** — FM band scan and station detection (frequency, strength)
- [x] **v0.2** — FM demodulation and playback through libVLC
- [x] **v0.3** — RDS decoding (station name, RadioText)
- [x] **v0.4** — DVB-T scan, multiplex parsing and channel list
- [x] **v0.5** — Stable API (`scan`, `stations`, `channels`, `streams`)
- [x] **v0.6** — Angular interface (lists, favourites, signal)
- [x] **v0.7** — Real-time spectrum display
- [x] **v1.0** — Full documentation, Linux packaging

The task breakdown behind this roadmap lives in [TASKS.md](TASKS.md).

---

## 🌍 Why "OpenWave"?

- **Open** — open source, transparent, extensible
- **Wave** — radio waves, signals, audio, transmission

Short, technical, and broad enough to cover FM, TV and SDR.

---

## ⚖️ Scope of use

OpenWave is built to receive **public, freely accessible broadcasts**. It does not aim to bypass
protection, encryption or paid services. Check your local regulations on radio reception before
using it. See [docs/legal.md](docs/legal.md).

---

## 📚 Documentation

| | |
|---|---|
| [Installing](docs/installing.md) | What to install, and what each extra pulls in |
| [Using OpenWave](docs/using.md) | From a first scan to listening to a station |
| [Command line](docs/cli.md) | Every command and option |
| [HTTP API](docs/api.md) | Routes, WebSockets, and the generated schema |
| [Hardware](docs/hardware.md) | Attaching a receiver, permissions, troubleshooting |
| [Compatibility](docs/compatibility.md) | Which receivers are known to work |
| [Architecture](docs/architecture.md) | How the pieces fit, and why |
| [Signals](docs/signals.md) | The radio and DSP behind the code |
| [Contributing](docs/contributing.md) | Getting set up, the checks, the rules |

Run `mkdocs serve` to read it as a site.

---

## 🤝 Contributing

The project is just starting and every kind of contribution is welcome: code, testing with real
hardware, documentation, translations, ideas.

**The single most useful thing you can do is run OpenWave on a real receiver and say what
happened**, even if it crashed. Nobody has. See
[the compatibility matrix](docs/compatibility.md) for what is and is not known.

Read [CONTRIBUTING.md](CONTRIBUTING.md) to get set up, then look for the
[`good first issue`](https://github.com/Mxlione/Openwave/labels/good%20first%20issue) label.

---

## 📄 License

MIT — see [LICENSE](LICENSE).
