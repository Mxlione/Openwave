# Contributing

The full guide is in [CONTRIBUTING.md](https://github.com/Mxlione/Openwave/blob/main/CONTRIBUTING.md).
This page is the short version.

## The most valuable thing you can do

If you own an RTL-SDR dongle or a DVB-T tuner, run a driver and report what happened. Those two
drivers have never touched hardware, and no amount of simulation can change that. See
[Hardware](hardware.md).

## Getting set up

```bash
git clone https://github.com/Mxlione/Openwave.git
cd Openwave
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

`[dev]` alone runs the whole test suite, because the tests use the simulated receivers. No
hardware, no libVLC, no Node.

## The checks

```bash
ruff check .            # lint
ruff format --check .   # formatting
mypy                    # types, in strict mode
pytest                  # tests
```

`ruff format .` fixes the formatting for you.

For the interface:

```bash
cd frontend
npm ci
npm run build           # also the type check
npm test                # unit tests, headless
```

## The rules that matter

- **Typed.** `mypy` runs in strict mode.
- **Units in names.** `freq_hz`, `power_dbfs`, `sample_rate_hz` — never a bare `freq`. The
  answer to "MHz or Hz?" is never obvious at the call site.
- **Signal processing stays pure.** Arrays in, arrays out. No device access, no I/O, no global
  state — which is what makes a detector testable by injecting a station at a known frequency.
- **Hardware access stays in `sdr/`.** Nothing outside it imports a vendor library or opens a
  device node. That rule is what lets the rest be tested without hardware.
- **Everything off the air is untrusted.** RDS groups and MPEG sections are written by whoever
  is transmitting. Parsers assume malformed, truncated and hostile data.
- **A DSP change needs a test with a known answer.** Inject a station at a known frequency,
  assert it is found within a stated tolerance.

## Out of scope

OpenWave receives public, freely accessible broadcasts. Contributions that circumvent
encryption, conditional access or paid services will be declined regardless of their quality.
See [Scope of use](legal.md).

## The screenshots

The pictures in the README and on these pages are generated, not taken by hand:

```bash
python scripts/capture_cli.py        # the terminal output, as SVG
python scripts/capture_interface.py  # the interface, needs Chrome and a built frontend
```

Both run the real program against the simulator. The command line one records what Rich printed
and exports it, so the image is the same characters a terminal would show, and `docs/index.md`
had a table pasted in by hand that had already drifted from what the program prints.

They are not run in CI. A screenshot differs in a few bytes between browser versions, so a
diff check would fail for reasons nobody can act on. Run them when you change the interface or
the output of a command, and commit what comes out.

## Releasing

Only a maintainer can do this, but it is written down so that it is not a secret.

The version lives in exactly one place, `src/openwave/__init__.py`. Everything else reads it:
the packaging metadata, `openwave version`, and the API's `/health`.

1. Update `__version__` and move the `Unreleased` section of
   [the changelog](https://github.com/Mxlione/Openwave/blob/main/CHANGELOG.md) under the new
   version with the date.
2. Commit, then tag: `git tag -a v0.1.0 -m "OpenWave 0.1.0"` and `git push --tags`.
3. The release workflow takes over. It refuses to continue if the tag and `__version__`
   disagree, builds the interface into the wheel, installs that wheel in a clean environment,
   runs a scan and starts the server against it, and attaches the result to the GitHub release.
4. Publishing to PyPI waits for an approval in the `pypi` environment, so a pushed tag never
   publishes on its own.
