---
title: "Scan results cannot be written as CSV"
labels: ["good first issue", "help wanted"]
milestone: "v1.1"
---

`openwave scan fm` prints a table for a person and `--json` for a program. A spreadsheet is
neither, and a list of what is on the air in a place, over time, is exactly the kind of thing
people keep in one.

**Where:** [`src/openwave/cli.py`](../../src/openwave/cli.py), the `fm` and `tv` commands under
`scan`.

**What to do**

- Add `--csv`, mutually exclusive with `--json`. Typer will not enforce that for you; check it
  and exit with a clear message rather than silently preferring one.
- Use the `csv` module, not string joining. A station name can contain a comma, and RDS
  RadioText certainly can.
- Write to standard output, so it pipes. `--output FILE` is a separate request; do not fold it in.
- For television, one row per service with its multiplex frequency repeated, rather than a nested
  shape no spreadsheet can read.

**Done when**

- `openwave scan fm --demo --csv` produces a header row and one row per station.
- A test reads the output back with `csv.DictReader` and asserts the station names, including one
  containing a comma.
- `--csv --json` together exits non-zero with a message saying to pick one.
