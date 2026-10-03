# Hardware

## The state of things

| Driver | Status |
|---|---|
| Simulated receiver | ✅ Verified — it is what everything else is tested against |
| Simulated DVB-T tuner | ✅ Verified |
| Recorded capture replay | ✅ Verified |
| libVLC playback | ✅ Verified, and CI installs libVLC to test it |
| RTL-SDR | ⚠️ **Never run on hardware** |
| Linux DVB-T | ⚠️ **Never run on hardware** |
| SoapySDR | ❌ Not written |

## Why two drivers are unverified

The maintainer owns no RTL-SDR dongle and no DVB-T tuner.

That constraint shaped the project rather than blocking it. Every signal-processing stage is
developed against simulated receivers that synthesise real signals — FM with real stereo
multiplexes and real RDS, MPEG transport streams with real tables and correct CRCs — and those
run in CI on every commit. The mathematics is tested. What is not tested is the code that talks
to a USB device or a kernel ioctl, because there is no way to test it here.

The two are unverified in different ways, and the DVB one is worse. `RtlSdrDevice` calls a
Python library whose documented behaviour it assumes; the ways it can be wrong are a wrong unit,
a wrong call, or an assumption about normalisation. `LinuxDvbDevice` lays out kernel structures
byte by byte, and a field in the wrong place does not raise an error — it tunes to the wrong
frequency, or returns a signal-to-noise ratio that is actually a pointer.

## If you have a receiver

Running a driver and saying what happened is the most useful contribution available. Any result
is worth having, including a crash.

```bash
pip install "openwave[rtlsdr]"
openwave devices              # is the dongle seen at all?
openwave probe -d rtlsdr      # can it tune, and do samples come out?
openwave scan fm              # and does a scan find what you know is there?
```

Then open an issue with the **📡 Hardware report** template and paste the output.

The template asks what you know is actually on the air, with frequencies. That is the part that
turns a report into a test: without it, a list of frequencies cannot be told from a list of
plausible-looking noise.

### What to look at especially

For **RTL-SDR**, the questions a simulator cannot answer:

- Does the gain list come back in decibels, or in tenths of a decibel? The driver assumes
  `pyrtlsdr` has already converted, and picks the nearest supported step.
- Are the samples normalised to roughly the unit circle, as documented? If not, every level
  OpenWave reports is wrong by a constant.
- Is the frequency correction applied before or after tuning?
- Does closing and reopening a dongle in the same process work, or does it need a reset?

For **Linux DVB-T**, in rough order of how likely they are to be wrong:

- Is `struct dtv_property` still 76 bytes on your platform? A different size shifts every
  property after the first.
- Does your tuner need `SYS_DVBT` or `SYS_DVBT2`? Try `--tuner linuxdvb2`.
- Does reading the demux give the whole transport stream, or only PIDs a filter selected? The
  driver asks for the whole stream with a filter on PID 0x2000, which is the convention, and a
  tuner that does not honour it returns nothing.
- Do the reported statistics look like decibels, or like nonsense?

### Sharing a capture

A few seconds of recorded samples is the next most useful thing after a report, because it turns
a real band into a test everybody can run:

```bash
rtl_sdr -f 98000000 -s 2400000 -n 7200000 fm-band.cu8
```

Then write a sidecar beside it:

```json
{
  "sample_rate_hz": 2400000,
  "center_freq_hz": 98000000,
  "format": "cu8",
  "receiver": "RTL-SDR Blog V4",
  "antenna": "telescopic dipole, indoor",
  "location": "Dublin",
  "notes": "89.1 strong, 95.6 weak, nothing between 90 and 94"
}
```

Record broadcast bands only, keep it short, and leave out anything identifying a private
individual. See [Scope of use](legal.md).

## Receivers that should work

Untested, but these are what the drivers were written against:

| Receiver | Notes |
|---|---|
| RTL-SDR Blog V3, V4 | R820T2 tuner, 24 MHz to 1.766 GHz |
| Generic RTL2832U with R820T | The same chipset, sold under many names |
| NooElec NESDR series | The same chipset |
| RTL2832U with E4000 | Reaches 1.1 GHz with a gap around 1.1 to 1.25 GHz; the tuning range OpenWave reports will be wrong |
| RTL2832U with FC0013 | Stops at 948 MHz; same caveat |
| Any DVB-T tuner with a Linux driver | Through the kernel's DVB API |
