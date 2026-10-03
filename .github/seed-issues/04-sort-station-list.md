---
title: "The station list cannot be sorted"
labels: ["good first issue", "help wanted", "interface"]
milestone: "v1.1"
---

The station list comes back in frequency order and stays that way. The most common thing
somebody wants is the strongest station first.

**Where:** [`frontend/src/app/stations/stations.ts`](../../frontend/src/app/stations/stations.ts)
and its template.

**What to do**

- Make the column headings clickable: frequency, signal, name.
- Hold the sort in a signal and derive the displayed rows with `computed`, which is how the rest
  of the component works. Do not sort the array in place; it is the response from the API.
- Clicking the same heading twice reverses it.
- Stations with no RDS name sort last by name rather than first, because a blank first row looks
  like a bug.

**Done when**

- A test in `stations.spec.ts` clicks a heading and asserts the order of the rendered rows.
- Keyboard works: the heading is a `button`, reachable by tab, with `aria-sort` on the column.
- `npm test` and `npm run build` both pass.
