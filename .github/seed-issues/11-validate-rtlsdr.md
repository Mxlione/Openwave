---
title: "Hardware validation: has anybody run OpenWave on an RTL-SDR?"
labels: ["hardware validation", "help wanted", "driver"]
milestone: "v1.1"
---

**The RTL-SDR driver has never been run on an RTL-SDR.** It was written from the pyrtlsdr API and
the datasheets, it is covered by tests against a simulated receiver, and it type-checks. None of
that is evidence that it tunes a radio.

If you have a dongle, this issue is the most useful thing in the repository you could pick up.

**What to run**

```bash
pip install "openwave[rtlsdr]"
openwave devices
openwave probe -d rtlsdr
openwave scan fm                 # the whole band
openwave listen 98.0 --seconds 10 --record check.wav   # a station you know
```

**What to report**

- The dongle: what it says on it, and the chipset if you know it (`rtl_test` prints it).
- The output of each command, in full, including any traceback.
- **What you know is actually on the air, with frequencies.** This is the part that matters most.
  Without it a list of frequencies cannot be told from a list of plausible-looking noise, and the
  whole point is to find out which one OpenWave produces.
- Whether `check.wav` is the station you expected, or noise, or silence.

**What would count as a result**

Any of these is worth reporting and none is a waste of anybody's time:

- It worked.
- It crashed — the traceback says where.
- It found stations, but not the ones that are there, or not at the right frequencies.
- It found the right stations but the audio is wrong.
- The gain settings do nothing, or the tuning range it reports is wrong for your chipset.

[`docs/hardware.md`](../../docs/hardware.md) covers permissions and the usual problems, and
[`docs/compatibility.md`](../../docs/compatibility.md) is the table your report goes into.

**Known risks, written down in advance:** the sample-rate and gain calls are the parts most
likely to be wrong, and the driver's module docstring lists exactly what it would like checked.
