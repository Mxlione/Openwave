---
title: "Write a SigMF sidecar alongside recorded captures"
labels: ["good first issue", "help wanted"]
milestone: "v1.2"
---

OpenWave records captures with its own small metadata sidecar: enough to replay a file, and
nothing more. [SigMF](https://github.com/sigmf/SigMF) is the format the rest of the software
radio world reads, and a capture that other tools can open is worth more than one only OpenWave
can.

**Where:** [`src/openwave/sdr/iq_file.py`](../../src/openwave/sdr/iq_file.py). The docstring
already says SigMF would be a good thing to support.

**What to do**

- When writing a capture, also write a `.sigmf-meta` file: a JSON object with `global`,
  `captures` and `annotations`, as the specification sets out.
- Map what OpenWave already knows: sample rate, centre frequency, the sample format, the time the
  recording started, and `core:recorder` naming OpenWave and its version.
- Reading SigMF back is a separate, larger job. Leave it out, and say so in the pull request.

**Done when**

- Writing a capture produces a `.sigmf-meta` next to the data file.
- A test validates it against the SigMF schema, or at minimum asserts the required `global`
  fields are present and correctly typed.
- The existing capture round-trip tests still pass; the sidecar is an addition, not a
  replacement.

**Careful with one thing:** SigMF's `core:datatype` names formats differently from OpenWave's
`IqFormat`. `cf32` is `cf32_le` on a little-endian machine, and `cu8` is `cu8`. Get the mapping
right or the file is worse than no file.
