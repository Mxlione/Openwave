# OpenWave interface

The Angular interface for [OpenWave](../README.md): a station list, a channel list and a live
spectrum, served by the same process that serves the API.

## Running it

The backend serves the built interface, so for ordinary use there is nothing to run here:

```bash
npm run build          # once, from this directory
openwave serve --demo  # from the repository root
```

Then open http://127.0.0.1:8000.

For working on the interface itself, run the dev server alongside the backend:

```bash
openwave serve --demo   # terminal one, from the repository root
npm start               # terminal two, from here
```

`npm start` proxies the API to the backend, so the two reload independently.

## The API types are generated, not written

Everything in `src/app/api/schema.d.ts` comes from the backend's own OpenAPI document:

```bash
npm run generate:api
```

The schema is checked in at `openapi.json`, written by `python scripts/export_openapi.py`. That
means two things worth knowing. The interface can be built without a running backend. And a
change to a response in Python becomes a compile error here rather than a field that is quietly
`undefined` at run time — which is the whole reason for generating it rather than writing the
types by hand.

If a build fails with a type error after pulling, regenerate the types.

## Checks

```bash
npm run build   # also the type check: Angular compiles templates as well as TypeScript
npm test        # unit tests, in a headless browser
```

There is no separate lint step. Building is the real check: Angular compiles the templates, so a
template referring to a field the API no longer returns fails the build.

## How it is put together

| Path | What it is |
|---|---|
| `src/app/api/` | The generated types, and the one service that talks to OpenWave |
| `src/app/stations/` | The FM station list, with favourites and playback |
| `src/app/channels/` | The television channel list, grouped by multiplex |
| `src/app/spectrum/` | The live spectrum and waterfall, and the frame decoder |
| `src/app/shared/` | The signal meter, the scan panel, the favourite button |
| `src/styles.scss` | Colour tokens and the base styles, dark by default |

A few decisions that are easier to find here than in the code:

- **Views are loaded on demand.** Somebody who only wants a station list should not wait for the
  canvas drawing the spectrum view needs.
- **The spectrum is a canvas, not elements.** A thousand bins twenty times a second is twenty
  thousand DOM updates a second; one canvas draw is a few hundred microseconds.
- **Audio is a URL, not data.** The browser's own audio element pulls the stream from the API,
  so nothing is decoded here and VLC on another machine can play the same URL.
- **The API address is derived from the page**, never configured. The backend serves this
  interface, so hard-coding a host would break every deployment but the developer's own.
