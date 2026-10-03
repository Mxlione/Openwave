# OpenWave

OpenWave scans the airwaves and tells you what is on them.

Plug in a receiver, run a scan, and you get a list: the FM stations in range with their names and
signal strengths, or the television channels with the numbers a viewer would type. Then listen,
or watch the spectrum move.

```
$ openwave scan fm --demo

┏━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━┓
┃ Frequency ┃     SNR ┃      Power ┃ Mode   ┃ Name      ┃
┡━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━┩
│  88.1 MHz │ 61.1 dB │ -20.0 dBFS │ stereo │ OPENWAVE  │
│  89.9 MHz │ 51.2 dB │ -30.0 dBFS │ mono   │ MONO FM   │
│  92.4 MHz │ 46.2 dB │ -35.0 dBFS │ stereo │ DISTANT   │
│  98.0 MHz │ 59.1 dB │ -22.0 dBFS │ mono   │ LOCAL 98  │
│  98.6 MHz │ 36.1 dB │ -45.0 dBFS │ stereo │ WEAK ONE  │
│ 101.7 MHz │ 53.1 dB │ -28.0 dBFS │ stereo │ CITY FM   │
│ 104.3 MHz │ 48.2 dB │ -33.0 dBFS │ mono   │ TALK 104  │
│ 107.9 MHz │ 51.2 dB │ -30.0 dBFS │ mono   │ EDGE FM   │
└───────────┴─────────┴────────────┴────────┴───────────┘
8 stations, 4 in stereo, in 0.3 s
```

## What it does

- **Scans the FM band** and finds the stations actually transmitting, with their signal strength,
  whether they are in stereo, and what they call themselves over RDS.
- **Scans the television bands**, locks onto each multiplex, and reads its own tables to list the
  services it carries and the channel numbers a viewer types.
- **Plays a station** through libVLC, or serves it as audio over HTTP so a browser or VLC on
  another machine can play it.
- **Shows the spectrum** live, with a waterfall, so you can see a signal come and go.
- **Exposes all of it over HTTP**, with an interface built on the same API.

## Try it without a receiver

Every part of OpenWave can be run against a simulated receiver, which is not a stub but a signal
generator: it synthesises FM transmissions with real stereo multiplexes and real RDS, and MPEG
transport streams with real tables. So this works on any machine:

```bash
pip install openwave
openwave scan fm --demo
openwave listen 88.1 --demo
openwave scan tv --demo
openwave serve --demo        # then open http://127.0.0.1:8000
```

See [Installing](installing.md) to do it with a real receiver.

## An honest note about hardware

!!! warning "The real drivers have never run on hardware"

    The maintainer owns no RTL-SDR dongle and no DVB-T tuner. Every signal-processing stage in
    OpenWave is developed and tested against simulated receivers, which run in CI on every
    commit — but the drivers that talk to real hardware are written from the vendor interfaces
    and have never had a sample come out of them.

    Simulation proves the mathematics. Only hardware proves the product. If you own a receiver,
    running a driver and reporting what happened — including a crash — is the most useful
    contribution available. See [Hardware](hardware.md).

## Where to go next

| | |
|---|---|
| [Installing](installing.md) | Getting it, and getting a receiver working |
| [Using it](using.md) | Scanning, listening and watching |
| [Command line](cli.md) | Every command and option |
| [HTTP API](api.md) | The API and its shapes |
| [Hardware](hardware.md) | What is known to work, and how to report what you have |
| [Architecture](architecture.md) | How the pieces fit together, and why |
| [Signals](signals.md) | The radio engineering, and the mistakes worth knowing about |
| [Contributing](contributing.md) | How to work on it |
| [Scope of use](legal.md) | What OpenWave is for, and what it will not do |
