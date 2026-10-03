---
title: "RDS station names lose their accents"
labels: ["good first issue", "help wanted", "signal processing"]
milestone: "v1.2"
---

A station called `RTL 102.5` decodes fine. A station called `FRANÇAIS` comes out as `FRAN AIS`.

RDS defines its own character sets rather than using ASCII or Latin-1. The default table matches
ASCII across the printable range, which is why Latin-alphabet names work, but anything outside
`0x20`–`0x7E` is currently replaced by a space.

**Where:** [`src/openwave/radio/rds/groups.py`](../../src/openwave/radio/rds/groups.py), the
`rds_text` function. It already says in its docstring that the tables are missing and that
adding them is self-contained.

**What to do**

- Add the three code tables from IEC 62106 (G0, G1, G2) as lookup tables.
- A station says which table it is using, in group 1A. Decoding that is the second half of the
  job and can be a second pull request — defaulting to G0 is what happens today and is right
  for most of the world.
- Space is still the right answer for a code with no character, because a name goes on a display
  and a row of replacement glyphs reads as a fault.

**Done when**

- `rds_text` returns the accented character for the codes that have one.
- A round-trip test encodes a name with accents through
  [`src/openwave/radio/rds/encoder.py`](../../src/openwave/radio/rds/encoder.py) and decodes it
  back unchanged.
- The existing doctests still pass: `pytest --doctest-modules src/openwave/radio/rds/`.

**Worth knowing:** the tables are in the standard, which is not free. They are also printed in
several places online and in the GNU Radio and RDS Surveyor sources, both of which are open.
