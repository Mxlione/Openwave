# HTTP API

```bash
openwave serve
```

Everything lives under `/api/v1`. The interactive documentation is at `/docs` and the schema at
`/openapi.json`.

The version is in the path on purpose. The interface's types are generated from this schema, so a
breaking change has to be something a client can detect rather than something it discovers at
run time.

## Levels are in dBFS, not dBm

A consumer receiver has no calibrated power reference: an RTL-SDR's reading depends on its gain
setting, its tuner chip, the cable and the antenna. So OpenWave reports levels in decibels
relative to full scale. Comparisons between stations and signal-to-noise ratios are meaningful;
absolute power is not.

## A scan is a job

An FM sweep takes a few seconds and a television sweep up to a minute. A request that blocks for
a minute times out in proxies, cannot report progress, and leaves a client with nothing to show.
So a scan is started, watched, and collected.

```bash
# Start one
curl -X POST localhost:8000/api/v1/scans \
     -H 'content-type: application/json' \
     -d '{"band": "fm", "rds": true}'
# → {"id": "a1b2c3d4e5f6", "state": "running", ...}

# Collect it
curl localhost:8000/api/v1/scans/a1b2c3d4e5f6
```

**One at a time.** A receiver cannot be tuned to two places at once, so a second scan while one
is running returns `409` rather than being queued — queuing would leave somebody waiting on a
scan they did not ask for.

Watch the progress over a WebSocket rather than polling:

```javascript
const socket = new WebSocket('ws://localhost:8000/api/v1/ws/scans/a1b2c3d4e5f6');
socket.onmessage = (event) => {
  const scan = JSON.parse(event.data);
  console.log(scan.state, scan.progress?.stage, scan.progress?.done, '/', scan.progress?.total);
};
```

## Routes

| | |
|---|---|
| `GET /health` | What this installation can do: version, whether playback works, how many receivers and tuners were found |
| `GET /devices` | Receivers and tuners, each saying whether its driver has ever run on the hardware it targets |
| `POST /scans` | Start a scan. `202` with an identifier |
| `GET /scans` | Every remembered scan, newest first |
| `GET /scans/{id}` | One scan, with its results once finished |
| `DELETE /scans/{id}` | Ask a scan to stop |
| `GET /stations` | What the last completed FM scan found |
| `GET /channels` | What the last completed television scan found |
| `GET /favourites` | Remembered stations and channels. `POST` to add, `DELETE` to forget |
| `GET /streams/fm/{freq_hz}` | A station as WAV over HTTP |
| `GET /spectrum` | How the spectrum feed is configured |
| `WS /ws/scans/{id}` | A scan's progress |
| `WS /ws/spectrum` | A live spectrum, as binary frames |

## Audio is a URL

```html
<audio src="http://localhost:8000/api/v1/streams/fm/98000000" controls></audio>
```

Or in VLC on another machine: **Media → Open Network Stream**, and paste the same address.

Add `?seconds=10` for a finite clip whose header states its length. Without it the stream runs
until the client goes away, which is what live listening wants.

**One frequency at a time.** Several clients can share one station, because they read the same
buffer. A request for a different frequency returns `409` rather than silently retuning the
receiver underneath whoever is already listening.

## The spectrum feed is binary

A thousand numbers as JSON is about 10 kB per frame and a parse in the browser on every one. A
byte per bin over a stated decibel range is 1 kB, which at twenty frames a second is 21 kB/s.

Eight bits is not a compromise: a display is a few hundred pixels tall and a colour ramp has
nowhere near 256 distinguishable steps.

Each frame is a 40-byte header and then one byte per bin:

| Offset | Type | Field |
|---|---|---|
| 0 | `char[4]` | `OWSP` |
| 4 | `uint8` | Format version, currently 1 |
| 5 | `uint8` | Flags, reserved |
| 6 | `uint16` | Number of bins |
| 8 | `float64` | Centre frequency in hertz |
| 16 | `float64` | Span in hertz |
| 24 | `float32` | Reference level in dBFS, which the highest byte value means |
| 28 | `float32` | Decibels the byte range covers, below the reference |
| 32 | `uint64` | Timestamp, milliseconds since the epoch |
| 40 | `uint8[]` | One byte per bin, lowest frequency first |

Everything after the magic is little-endian. A frame carries its own scale, so a client needs no
prior agreement about what the bytes mean — and should refuse a version it does not know rather
than drawing a loud signal that is not there.

```javascript
const socket = new WebSocket('ws://localhost:8000/api/v1/ws/spectrum?freq_hz=98000000');
socket.binaryType = 'arraybuffer';
socket.onmessage = (event) => {
  const view = new DataView(event.data);
  const bins = view.getUint16(6, true);
  const reference = view.getFloat32(24, true);
  const range = view.getFloat32(28, true);
  const magnitudes = new Uint8Array(event.data, 40, bins);
  // level in dBFS = reference - (255 - magnitude) * range / 255
};
```

`frontend/src/app/spectrum/frame.ts` is a complete decoder, and
`openwave/api/spectrum.py` is the other half of the definition.

## When something goes wrong

The common failures are things a person can fix, so the message is meant to be shown rather than
logged:

```json
{
  "error": "DeviceNotFoundError",
  "detail": "no RTL-SDR dongle at index 0: ... Check it is plugged in, and on Linux that the udev rules are installed so it can be opened without root."
}
```

| Status | What it means |
|---|---|
| `409` | Something else has the receiver: a scan is running, or a different frequency is streaming |
| `422` | The request did not match the schema |
| `503` | The receiver could not be opened or tuned |
