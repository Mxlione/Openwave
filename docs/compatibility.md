# Compatibility

Which receivers are known to work, and what happened when somebody tried.

!!! info "This table is filled in by the community"

    Every row below that says **not tested** says so because the maintainer owns no receiver.
    There is nothing to be done about that from here — only a report from somebody who has one
    can change a row.

    If you have a receiver, see [Hardware](hardware.md) for what to run and what to report. A
    crash is a result worth filing.

## Software radio receivers

| Receiver | Chipset | Status | Reported by | Notes |
|---|---|---|---|---|
| Simulated receiver | — | ✅ Works | CI, every commit | What everything else is tested against |
| Recorded capture replay | — | ✅ Works | CI, every commit | `.cf32`, `.cu8`, `.cs16` |
| RTL-SDR Blog V4 | R828D + RTL2832U | ⬜ Not tested | — | The driver was written against this |
| RTL-SDR Blog V3 | R820T2 + RTL2832U | ⬜ Not tested | — | |
| NooElec NESDR Smart | R820T2 + RTL2832U | ⬜ Not tested | — | |
| Generic RTL2832U | R820T / R820T2 | ⬜ Not tested | — | Sold under many names |
| RTL2832U with E4000 | E4000 | ⬜ Not tested | — | Has a gap around 1.1–1.25 GHz that OpenWave does not model |
| RTL2832U with FC0013 | FC0013 | ⬜ Not tested | — | Stops at 948 MHz; the reported range will be wrong |
| HackRF One | MAX2837 | ❌ No driver | — | Would need a SoapySDR driver |
| Airspy | R820T2 | ❌ No driver | — | Likewise |
| SDRplay RSP1A | — | ❌ No driver | — | Likewise |

## Television tuners

| Tuner | Status | Reported by | Notes |
|---|---|---|---|
| Simulated DVB-T tuner | ✅ Works | CI, every commit | |
| Any tuner with a Linux DVB driver | ⬜ Not tested | — | Through the kernel's DVB API |
| Hauppauge WinTV-dualHD | ⬜ Not tested | — | |
| August DVB-T210 | ⬜ Not tested | — | |

!!! warning "The DVB driver is the riskiest code in the project"

    It lays out kernel structures byte by byte from Python. A field in the wrong place does not
    raise an error — it tunes to the wrong frequency, or returns a signal-to-noise ratio that is
    actually a pointer. If you try it, a report saying "it tuned 8 MHz low" is as valuable as one
    saying it crashed.

## Playback

| | Status | Notes |
|---|---|---|
| libVLC 3.0 | ✅ Works | CI installs it and tests against it |
| libVLC 4.0 | ⬜ Not tested | The media callbacks API changed; may need adjusting |

## Operating systems

| | Status | Notes |
|---|---|---|
| Linux, Python 3.11 and 3.12 | ✅ Works | CI tests both |
| Linux, Python 3.13 | ⬜ Not tested | Should work; nothing in the code is version-specific |
| macOS | ⬜ Not tested | RTL-SDR should work; the DVB driver is Linux-only by construction |
| Windows | ⬜ Not tested | RTL-SDR may work; the DVB driver will not, and the udev rules are meaningless |

## How to add a row

Open an issue with the **📡 Hardware report** template, or a pull request editing this page. Both
are equally welcome — a pull request is faster for you and an issue is easier to discuss.

What makes a report useful: the receiver and its chipset, what you ran, the full output
including any traceback, and **what you know is actually on the air** with frequencies. That last
part is what turns a report into something testable: without it, a list of frequencies cannot be
told from a list of plausible-looking noise.
