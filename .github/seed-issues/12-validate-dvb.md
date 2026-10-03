---
title: "Hardware validation: has anybody run OpenWave on a DVB-T tuner?"
labels: ["hardware validation", "help wanted", "driver"]
milestone: "v1.1"
---

**The Linux DVB driver has never been run on a tuner**, and it is the riskiest code in the
project.

It talks to the kernel's DVB API directly through `ioctl`, which means it lays out C structures
byte by byte from Python. A field in the wrong place does not raise an error. It tunes to the
wrong frequency, or reports a signal-to-noise ratio that is really part of a pointer, and
everything downstream looks like it is working.

**What to run**

```bash
openwave devices
openwave scan tv --demo          # proves the parsing works, with no tuner
openwave scan tv                 # the real thing
openwave scan tv --band vhf
```

**What to report**

- The tuner, and what `dmesg | grep -i dvb` says about it when you plug it in.
- Which `/dev/dvb/adapterN` it appeared as.
- The full output, including any traceback.
- **Which multiplexes are actually receivable where you are, and on which channels.** Your
  regulator or a site like [KingOfSat](https://en.kingofsat.net) will tell you, and without it
  there is no way to know whether a result is right.
- The services found, and whether the channel numbers match what a television shows.

**What would count as a result**

- It worked, and the numbers match the television.
- It crashed.
- It locked but found no tables — that would point at the section assembly rather than the
  tuning.
- It reported a lock on a channel you know is empty, or no lock on one you know is busy.
- The frequency it tuned was wrong by a fixed amount. That is the classic symptom of a structure
  laid out incorrectly, and it would be the single most valuable thing to learn.

**Please also say what it did not do.** A tuner that never locks is as much of a result as one
that works, and more useful than silence.
