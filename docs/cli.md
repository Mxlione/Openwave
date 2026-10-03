# Command line

Every command takes `--help`.

## `openwave devices`

Lists the receivers and tuners that can be opened. The simulator is always listed. If a dongle
is plugged in and does not appear, the usual causes are a missing driver, a missing optional
extra, or [permissions](installing.md#udev-rules-for-rtl-sdr).

## `openwave probe`

```bash
openwave probe [-d DEVICE]
```

Opens a receiver, reports its tuning range and sample rates, and reads a few samples. The first
thing to run when something is wrong, and the first thing to run when validating a driver
against real hardware.

## `openwave scan fm`

```bash
openwave scan fm [-d DEVICE] [-b BAND] [-t THRESHOLD] [--rds/--no-rds] [--json]
```

| Option | Default | What it does |
|---|---|---|
| `-d`, `--device` | `mock` | Receiver: `rtlsdr`, `rtlsdr:1`, `file:path.cu8` |
| `-b`, `--band` | `fm` | `fm` or `fm-japan` |
| `-t`, `--threshold` | `10` | Signal-to-noise ratio in dB for a channel to count |
| `-r`, `--sample-rate` | fastest usable | Sample rate in Hz |
| `--rds` / `--no-rds` | on | Read station names. Needs about a second per station |
| `--stereo` / `--no-stereo` | on | Demodulate each station to decide stereo |
| `--demo` | off | Scan an invented band |
| `--json` | off | Machine-readable output |

## `openwave scan tv`

```bash
openwave scan tv [-u TUNER] [-b BAND] [--lock-timeout SECONDS] [--json]
```

| Option | Default | What it does |
|---|---|---|
| `-u`, `--tuner` | `mockdvb` | `mockdvb`, `linuxdvb`, `linuxdvb2`, or with an adapter number |
| `-b`, `--band` | `uhf` | `uhf`, `uhf-extended` or `vhf` |
| `--lock-timeout` | `2.0` | Seconds to give the demodulator per channel. Most channels are empty, so this is most of the time a scan takes |
| `--scrambled` / `--no-scrambled` | on | List services that cannot be watched |
| `--demo` | off | Scan invented multiplexes |
| `--json` | off | Machine-readable output |

## `openwave listen`

```bash
openwave listen FREQUENCY [-d DEVICE] [-s SECONDS] [-o FILE]
```

The frequency is in megahertz, or in hertz if it is large enough to be unambiguous — `98.0` and
`98000000` mean the same thing.

| Option | What it does |
|---|---|
| `-s`, `--seconds` | Stop after this long. Without it, until interrupted |
| `-o`, `--record` | Write a WAV file instead of playing. Needs `--seconds` |
| `--demo` | Listen to an invented band |

## `openwave serve`

```bash
openwave serve [--host HOST] [-p PORT] [-d DEVICE] [-u TUNER] [--demo]
```

Serves the API and the interface. Defaults to `127.0.0.1:8000`.

!!! warning
    `--host 0.0.0.0` makes the receiver available to anybody who can reach the port. OpenWave
    has no authentication.

## `openwave version`

Prints the version.
