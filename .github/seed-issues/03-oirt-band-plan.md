---
title: "Add the OIRT FM band (65.8–74 MHz)"
labels: ["good first issue", "help wanted"]
milestone: "v1.2"
---

OpenWave knows two FM band plans: the usual 87.5–108 MHz, and Japan's 76–95 MHz. The OIRT band,
65.8–74 MHz, is still in use in parts of eastern Europe and central Asia, and a receiver pointed
at it currently has no plan to scan.

**Where:** [`src/openwave/radio/band.py`](../../src/openwave/radio/band.py), next to
`FM_BAND_PLAN_JAPAN`. Adding an entry to `FM_BAND_PLANS` is all that is needed for
`--band oirt` to work, because the command line and the API both read that dictionary.

**What to do**

- Add the plan: 65.8 to 74.0 MHz, 30 kHz spacing, and the same 200 kHz channel bandwidth as
  wideband FM elsewhere.
- Check the spacing before you commit to it. Some OIRT assignments are on a 30 kHz grid and some
  on 100 kHz; the scanner costs a little time on a finer grid and finds stations on either, so
  the finer one is the safer default. Say in the pull request which you picked and why.
- Note in the docstring that most RTL-SDR dongles reach below 65 MHz only with a converter or in
  direct-sampling mode, so a plan existing is not the same as a receiver covering it.

**Done when**

- `openwave scan fm --band oirt --demo` runs.
- A test puts a synthetic station in the band and the scan finds it, in the style of the existing
  band plan tests.
- [`docs/cli.md`](../../docs/cli.md) lists the new plan.
