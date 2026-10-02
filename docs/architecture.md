# Architecture

OpenWave turns radio waves into a list of things you can play. This document explains how, and why
the pieces are separated the way they are.

## The one-sentence version

A **device** produces samples, the **core** finds signals in them, **radio** and **tv** turn those
signals into named services, **media** plays them, and the **api** exposes all of it to the
**frontend**.

```
 device  →  core  →  radio / tv  →  media  →  api  →  frontend
(samples)  (peaks)   (services)   (streams)  (JSON)    (UI)
```

Every arrow points one way. Nothing downstream reaches back upstream.

## Layers

### `sdr/` — hardware abstraction

The only layer that knows what a receiver is. It exposes two interfaces:

- **`SdrDevice`** — raw IQ sample source. Tune to a centre frequency, set a sample rate, read
  complex samples. Implemented by `RtlSdrDevice`, `SoapySdrDevice` and `MockSdrDevice`.
- **`DvbDevice`** — demodulating tuner. Tune to a channel, report lock and signal quality, read an
  MPEG transport stream. Implemented by `LinuxDvbDevice` and `MockDvbDevice`.

The split exists because the two kinds of hardware work differently. An RTL-SDR hands you raw IQ
and leaves all demodulation to software. A DVB-T tuner demodulates in hardware and hands you a
finished transport stream. Pretending they are the same thing would mean lying to one of them.

**Nothing outside `sdr/` imports `pyrtlsdr` or opens a `/dev` node.** That rule is what makes the
rest of the codebase testable without hardware, and what lets a new receiver be added without
touching anything else.

### `core/` — the scanning engine

Hardware-agnostic and modulation-agnostic. Given a device and a band plan, it answers: *where is
there energy, and how much?*

- **`frequency_manager`** — band plans. Knows that FM broadcast runs 87.5–108 MHz on a 100 kHz
  raster, that UHF TV channels are 8 MHz wide, and how to slice a band into segments that fit
  inside the device's bandwidth.
- **`signal_detector`** — power spectral density by Welch's method, noise-floor estimation, peak
  detection above a threshold in dB. Pure functions: arrays in, measurements out.
- **`scanner`** — orchestration. Walks the segments, collects peaks, merges duplicates found at
  segment edges, emits candidates.
- **`service_discovery`** — hands candidates to the right decoder and collects what comes back.

### `radio/` — FM

- **`fm_demodulator`** — quadrature demodulation, decimation, de-emphasis, stereo decoding using
  the 19 kHz pilot and the 38 kHz subcarrier.
- **`rds_decoder`** — extracts the 57 kHz subcarrier, demodulates BPSK, recovers the clock, decodes
  differentially, checks the CRC with offset words, and parses groups into a station name (0A/0B)
  and RadioText (2A).
- **`station_detector`** — turns a candidate peak into a `Station`: is this a real FM carrier, is it
  stereo, does it carry RDS, what is it called?

### `tv/` — DVB-T

- **`dvb_scanner`** — walks the UHF channel plan, tunes, waits for lock, records signal quality.
- **`mux_parser`** — demultiplexes the 188-byte transport stream, filters by PID, reassembles PSI/SI
  sections across packets.
- **`service_parser`** — reads PAT, PMT, SDT and NIT to produce the service list.
- **`channel_detector`** — turns services into `Channel` entries with name, type, logical channel
  number and scrambling status.

### `media/` — playback

- **`ffmpeg`** — format conversion and remuxing where needed.
- **`libvlc`** — hands a stream to libVLC through `python-vlc`.
- **`codecs`** — capability detection, so the UI can say *why* something will not play.

### `api/` — the boundary

FastAPI. Pydantic models define the contract, OpenAPI is generated from them, and the Angular
client is generated from that — so the frontend cannot drift from the backend silently.

REST for things that complete (`/scan`, `/stations`, `/channels`), WebSocket for things that stream
(scan progress, live spectrum).

### `frontend/` — Angular

Lists, signal meters, the spectrum display, favourites. It talks only to the API; it has no idea
what a receiver is.

## Design rules

These are the constraints that keep the above honest. They are enforced in review.

**1. DSP functions are pure.** Signal processing takes arrays and returns arrays or measurements.
No device access, no file I/O, no global state. This is what makes a detector testable: inject a
synthetic station at 98.0 MHz, assert it is found within 50 kHz.

**2. Hardware access is confined to `sdr/`.** See above. Non-negotiable.

**3. Units live in names.** `freq_hz`, `power_dbm`, `sample_rate_hz`, `bandwidth_hz`. A bare `freq`
is a bug waiting to happen, because the answer to "MHz or Hz?" is never obvious at the call site.

**4. Everything on the air is untrusted input.** RDS groups and MPEG-TS sections are written by
whoever is transmitting. Parsers assume malformed, truncated and hostile data. A station name never
reaches a shell, a filename or an FFmpeg argument without being sanitised.

**5. Simulated devices are first-class.** They are not scaffolding to be deleted later. They are
how the project is testable in CI, how contributors without hardware can work, and how a detector's
accuracy is measured against a known answer. See below.

## Why simulated devices carry so much weight

The maintainer owns no RTL-SDR dongle and no DVB-T tuner. That constraint shaped the architecture
rather than blocking it:

- **`MockSdrDevice`** synthesises IQ: FM carriers at chosen frequencies, with controllable
  modulation, noise floor and SNR. A test that injects a station at 98.0 MHz with 20 dB SNR knows
  exactly what the detector should find.
- A **synthetic RDS encoder** generates real RDS bitstreams, so the decoder is tested round-trip:
  encode a station name, decode it, compare.
- **`MockDvbDevice`** replays recorded transport streams, so PSI/SI parsing is tested against real
  broadcast data.

This has a real cost: a simulated signal is clean in ways the air never is. Multipath, adjacent
channel interference, dongle DC offset, I/Q imbalance and drifting oscillators are all absent. Code
that passes CI can still fail on a rooftop antenna.

That is why `hardware validation` issues exist, and why the hardware report template asks what you
know is actually on the air. Simulation proves the maths; only hardware proves the product.

## Error handling

Device problems are typed and distinguishable, because the UI needs to say something useful:
`DeviceNotFound`, `DeviceBusy`, `UnsupportedSampleRate`, `TuningFailed`, `NoLock`. "Scan failed" is
not an acceptable message when the real cause is that the dongle is unplugged.

## Adding a new receiver

1. Implement `SdrDevice` or `DvbDevice` in `sdr/`.
2. Register it in `device_manager`.
3. Add a test proving the interface contract holds, using recorded samples if you have hardware.
4. Change nothing else.

If step 4 turns out to be impossible, the abstraction is wrong and that is worth an issue.
