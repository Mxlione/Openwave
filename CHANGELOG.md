# Changelog

Notable changes to OpenWave. The format follows [Keep a Changelog](https://keepachangelog.com),
and the project follows [semantic versioning](https://semver.org) once it reaches 1.0 — before
then, a minor version may change the API.

## Unreleased

Everything so far. OpenWave has not been released yet.

### Scanning

- FM band scan: measures the power in every channel on the 100 kHz grid, 87.5 to 108 MHz, and
  reports the stations present with their signal-to-noise ratio. Channel power rather than
  spectral peaks, because wideband FM suppresses its own carrier — see
  [Signals](docs/signals.md).
- FM demodulation, with stereo decoding from the 19 kHz pilot and de-emphasis.
- RDS decoding: programme identification, programme type, the station name and RadioText, with
  error correction for bursts of up to five bits.
- DVB-T scan: tunes each channel of the UHF or VHF plan, locks on, and reads the multiplex's own
  PAT, PMT, SDT and NIT to list its services and their channel numbers.

### Receivers

- `SdrDevice` and `DvbDevice` interfaces, with the state and validation in the base classes so a
  driver implements only what touches hardware.
- Simulated receivers that synthesise real signals: FM with stereo multiplexes and RDS, and
  MPEG transport streams with correct tables. Fully reproducible from a seed and a position.
- Recorded capture replay, in `.cf32`, `.cu8` and `.cs16`, with a metadata sidecar.
- RTL-SDR driver, **never run on hardware**.
- Linux DVB-T driver, **never run on hardware**.

### Playing and serving

- Playback through libVLC, pulling a live stream through media callbacks.
- `openwave listen`, with `--record` for a machine with no audio output.
- An HTTP API under `/api/v1`: scans as background jobs, station and channel lists, favourites,
  audio over HTTP, and WebSockets for scan progress and a live spectrum.
- An Angular interface served by the same process, with a station list, a channel list and a
  live spectrum with a waterfall.

### Known limitations

- The RTL-SDR and Linux DVB drivers are unverified. See
  [Compatibility](docs/compatibility.md).
- No authentication. `openwave serve` listens on loopback only by default for that reason.
- A scrambled television service is listed and marked, never decrypted. See
  [Scope of use](docs/legal.md).
- RDS character sets outside printable ASCII are not implemented, so an accented station name
  loses its accents.
- SoapySDR is not implemented, so receivers other than RTL-SDR are unsupported.
