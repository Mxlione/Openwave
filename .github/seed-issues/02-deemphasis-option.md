---
title: "No way to ask for 75 µs de-emphasis from the command line"
labels: ["good first issue", "help wanted"]
milestone: "v1.1"
---

FM broadcasting pre-emphasises the treble before transmitting and the receiver has to undo it.
The time constant is 50 µs in Europe and most of the world, and 75 µs in the Americas, Japan and
South Korea. Use the wrong one and the audio is dull or harsh, but never obviously broken, which
is what makes this worth an option rather than a guess.

The signal path already supports it:
[`fm_demodulator.py`](../../src/openwave/radio/fm_demodulator.py) takes `deemphasis_tau_s`, and
so does [`listener.py`](../../src/openwave/radio/listener.py). Nothing exposes it.

**What to do**

- Add an option to `openwave listen` in [`src/openwave/cli.py`](../../src/openwave/cli.py).
  Something a person can type: `--deemphasis 75` meaning microseconds, rather than `0.000075`.
- Pass it through to `ListenSettings`.
- Add the same field to the audio stream route in
  [`src/openwave/api/app.py`](../../src/openwave/api/app.py), as a query parameter.
- Regenerate the schema afterwards: `python scripts/export_openapi.py`. CI fails if you forget.

**Done when**

- `openwave listen 98.0 --demo --deemphasis 75 --seconds 2 --record /tmp/a.wav` works and
  produces audibly brighter output than the 50 µs default.
- A test asserts that the two settings give measurably different high-frequency content, rather
  than only that the option parses.

**Open question worth deciding in the issue:** whether the band plan should pick a default, since
`fm-japan` implies 75 µs. Say what you chose and why in the pull request.
