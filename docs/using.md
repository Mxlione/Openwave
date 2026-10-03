# Using it

Every command works against a simulated receiver with `--demo`, so you can try any of this
without hardware.

## Finding a receiver

```bash
openwave devices
```

Lists what can be opened. The simulator is always there; a dongle appears if one is plugged in,
its driver is installed, and [the permissions are right](installing.md#udev-rules-for-rtl-sdr).

```bash
openwave probe -d rtlsdr
```

Opens a receiver, reports what it can do, and reads a few samples. The quickest way to find out
whether a receiver works at all, and the first thing to run when something is wrong.

## Scanning the FM band

```bash
openwave scan fm
```

Sweeps 87.5 to 108 MHz, measures the power in every channel on the 100 kHz grid, then returns to
each occupied one to decide whether it is in stereo and to read its name from RDS.

Reading RDS is what fills in the names, and it needs about a second of signal per station — so
it is what makes a scan slow. Turn it off when you only want frequencies:

```bash
openwave scan fm --no-rds
```

Other options worth knowing:

| Option | What it does |
|---|---|
| `--threshold 15` | How far above the noise a channel must be to count. Raise it to ignore weak signals, lower it to find them |
| `--band fm-japan` | The Japanese band, 76 to 95 MHz |
| `--sample-rate 1024000` | A slower rate, for a receiver that cannot sustain 2.4 MS/s |
| `--no-stereo` | Skip demodulating each station, which is quicker and leaves the mode unknown |
| `--json` | Machine-readable output |

## Listening

```bash
openwave listen 98.0
```

Tunes a station and plays it through libVLC. Press Ctrl-C to stop.

On a machine with no audio output — a server, or a container — write a file instead:

```bash
openwave listen 98.0 --seconds 30 --record station.wav
```

## Scanning for television

```bash
openwave scan tv
```

Tunes each channel of the UHF plan in turn, waits for the demodulator to lock, and reads the
multiplex's own tables to find out what it carries. Most channels are empty, so most of the time
goes on waiting for a lock that never comes — `--lock-timeout` is the dial that matters:

```bash
openwave scan tv --lock-timeout 1.0     # quicker, may miss a marginal multiplex
```

| Option | What it does |
|---|---|
| `--band vhf` | VHF band III, channels 5 to 12 |
| `--tuner linuxdvb2` | Ask for DVB-T2 rather than DVB-T |
| `--no-scrambled` | Leave out services that cannot be watched |
| `--json` | Machine-readable output |

## The interface

```bash
openwave serve
```

Serves the API and the interface at <http://127.0.0.1:8000>, with the documentation at `/docs`.

!!! warning "It listens on this machine only, by default"

    OpenWave has no authentication. `--host 0.0.0.0` makes your receiver available to anybody
    who can reach the port: they can tune it, listen through it, and see what is on the air
    where you are. Put it behind something that asks who is calling before exposing it.

## Replaying a recorded capture

A capture from somebody who owns a receiver can be scanned exactly like hardware:

```bash
openwave scan fm --device file:captures/fm-band.cu8
```

Formats: `.cf32` (GNU Radio), `.cu8` (what `rtl_sdr` writes), and `.cs16`. Each needs a JSON
sidecar beside it saying the sample rate and centre frequency — samples alone cannot be placed
anywhere in the spectrum. `openwave probe -d file:...` prints what a capture says about itself.

A capture only holds one window of spectrum, so a scan of one reports the channels inside that
window and says how much of the band it could not reach.
