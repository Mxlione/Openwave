---
title: "The interface forgets the band and the RDS setting on every reload"
labels: ["good first issue", "help wanted", "interface"]
milestone: "v1.1"
---

Choose VHF on the channels page, reload, and it is back to UHF. Turn RDS off to make a scan
quicker, reload, and it is on again. These are per-person, per-browser preferences, and the
browser is the right place for them.

**Where:** the stations and channels components under
[`frontend/src/app/`](../../frontend/src/app/). Nothing in the interface uses `localStorage`
today.

**What to do**

- Remember the band selection and the RDS checkbox in `localStorage`.
- Wrap every read and write in `try`/`catch`. `localStorage` throws in a private window with site
  data blocked, and a preference that cannot be saved must not stop the page rendering.
- Favourites stay where they are: they live on the server on purpose, because a receiver is a
  thing in a room reached from several devices. This issue is only about display preferences.

**Done when**

- Setting a band and reloading keeps it.
- A test asserts the page still renders when `localStorage.getItem` throws.
- `npm test` and `npm run build` pass.
