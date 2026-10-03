---
title: "`openwave probe` does not say how quiet the receiver is"
labels: ["good first issue", "help wanted"]
milestone: "v1.1"
---

`openwave probe` opens a receiver, reports its tuning range and gains, and reads a few samples.
The question it does not answer is the one that decides whether a scan will work at all: how
much noise is there?

A noise floor is also the quickest way to catch a receiver that is connected but has no antenna,
which otherwise looks exactly like a quiet band.

**Where:** [`src/openwave/cli.py`](../../src/openwave/cli.py), the `probe` command. The
measurement already exists:
[`src/openwave/core/signal_detector.py`](../../src/openwave/core/signal_detector.py) has
`power_spectrum` and the lower-quartile estimate used by the scanner.

**What to do**

- After reading the samples, estimate the noise floor in dBFS and print it.
- Print the peak too, and the gap between them. The gap is what a person can act on: a few dB
  means nothing will be detected.
- Say plainly when the gap is small, something like "no signal stands out; check the antenna".
  Do not guess at a cause beyond that.

**Done when**

- `openwave probe --demo` prints a noise floor and a peak, and the numbers are in the right
  order of magnitude against the simulator, whose noise floor is a known value.
- A test asserts the reported floor is within a few dB of the simulator's configured
  `noise_floor_dbfs`.

**One trap:** dBFS is relative to full scale, not to an antenna. It is not dBm and must not be
printed as though it were. [`docs/signals.md`](../../docs/signals.md) explains the difference.
