---
title: "A television scan always sweeps the whole band"
labels: ["good first issue", "help wanted"]
milestone: "v1.1"
---

`openwave scan tv` tries every channel of the plan: 28 of them for UHF, most empty, each costing
the lock timeout before it gives up. Somebody who knows their local multiplexes are on channels
21 to 30 has no way to say so, and waits anyway.

**Where:** [`src/openwave/cli.py`](../../src/openwave/cli.py), the `tv` command, and
[`src/openwave/tv/dvb_scanner.py`](../../src/openwave/tv/dvb_scanner.py).

**What to do**

- Add `--channels`, taking either a range or a list: `--channels 21-30` and `--channels 21,24,30`
  should both work.
- Parse it into a set of channel numbers and filter the plan before scanning, rather than
  scanning everything and discarding.
- Reject a channel that is not in the chosen plan, naming the valid range. Failing loudly beats
  scanning nothing and reporting no services found.

**Done when**

- `openwave scan tv --demo --channels 21-30` scans ten channels, and the scan reports
  `channels_tried` as ten.
- Tests cover a range, a list, a single channel, and a channel outside the plan.
- [`docs/cli.md`](../../docs/cli.md) documents the option.
