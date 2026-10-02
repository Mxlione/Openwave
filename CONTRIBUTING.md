# Contributing to OpenWave

Thanks for being here. OpenWave is young, so almost everything is still open — including the parts
that matter most.

## The most valuable contribution right now

The maintainer has **no RTL-SDR dongle and no DVB-T tuner**. Every signal-processing stage is built
and tested against simulated receivers, and the real hardware drivers are written from the vendor
APIs without ever having touched a physical device.

If you own a receiver, look for issues labelled **`hardware validation`**. Running a driver against
real hardware and reporting what happened — even "it crashed with this traceback" — unblocks work
nobody else can do.

Two commands are all it takes to produce a useful report:

```bash
pip install -e ".[dev,rtlsdr]"

openwave devices              # does OpenWave see your dongle at all?
openwave probe -d rtlsdr      # can it tune, and do samples come out?
```

Then open an issue with the **📡 Hardware report** template and paste the output, whatever it
says. A traceback is a result. So is "it worked".

## Ways to contribute

- **Hardware validation** — run the drivers on a real receiver and report back
- **IQ captures** — share short recordings of the FM band so others can test without hardware
- **DSP code** — demodulation, RDS decoding, spectrum analysis
- **DVB-T** — MPEG-TS parsing, PSI/SI tables, service discovery
- **API and frontend** — FastAPI endpoints, Angular interface
- **Documentation and translations** — the repository is English, with a French README
- **Ideas and bug reports** — open an issue, no code required

## Getting set up

OpenWave targets Linux and Python 3.11+.

```bash
git clone https://github.com/Mxlione/Openwave.git
cd Openwave

python3 -m venv .venv
source .venv/bin/activate

# Core + development tooling
pip install -e ".[dev]"

# Optional extras, depending on what you work on
pip install -e ".[dev,api]"      # HTTP API
pip install -e ".[dev,media]"    # libVLC playback
pip install -e ".[dev,rtlsdr]"   # real RTL-SDR hardware
```

The `media` extra installs the Python bindings; libVLC itself comes from your package manager
(`libvlc-dev` on Debian and Ubuntu, `vlc-devel` on Fedora). With it installed you can hear the
demodulator work without owning a receiver:

```bash
openwave scan fm --demo      # scan an invented band
openwave listen 88.1 --demo  # and listen to one of its stations
openwave scan tv --demo      # scan invented television multiplexes
openwave serve --demo        # and serve all of it over HTTP
```

With the server running, the interactive documentation is at http://127.0.0.1:8000/docs.

`pip install -e ".[dev]"` alone is enough to run the whole test suite, because the tests use the
simulated receivers.

## Before you open a pull request

Run the same four checks CI runs:

```bash
ruff check .            # lint
ruff format --check .   # formatting
mypy                    # type checking
pytest                  # tests
```

`ruff format .` fixes formatting for you.

## Code conventions

- **Typed.** Public functions carry type annotations; `mypy` runs in strict mode.
- **Units in names.** `freq_hz`, `power_dbm`, `sample_rate_hz` — never a bare `freq`.
- **DSP stays pure.** Signal-processing functions take arrays in and return arrays out, with no
  device access and no I/O, so they can be tested on synthetic signals.
- **Hardware stays behind the interface.** Code outside `sdr/` talks to `SdrDevice` and
  `DvbDevice`, never to `pyrtlsdr` or a `/dev` node directly.
- **Single-letter DSP names are allowed** where they match the maths (`N`, `fs`, `x`, `y`). The
  linter is configured for this.

## Tests

- Every DSP change needs a test on a **synthetic signal with a known answer**: inject a station at
  a known frequency, assert it is found within a stated tolerance.
- Tests must pass without any hardware attached. Anything that genuinely needs a receiver is marked
  `@pytest.mark.hardware` and skipped in CI.
- Playback tests are marked `@pytest.mark.media` and skipped when libVLC is absent. Unlike the
  SDR drivers, that path *is* verified in CI, which installs libVLC — so a playback change
  should come with a test that really starts it.
- Large captures do not belong in git. Generate signals in the test, or add a small fixture under
  `tests/data/`.

## Commits and pull requests

- Commit messages in English, imperative mood: `add FM peak detector`, not `added` or `adds`.
- One logical change per pull request. A 40-file refactor mixed with a bug fix will be asked to
  split.
- Say in the description how you tested it, and whether real hardware was involved.
- Draft pull requests are welcome if you want feedback early.

## Scope boundary

OpenWave receives **public, freely accessible broadcasts**. Contributions that circumvent
encryption, conditional access or paid services will be declined, regardless of technical quality.
See [docs/legal.md](docs/legal.md).

## Code of conduct

Participating means following the [Code of Conduct](CODE_OF_CONDUCT.md).
