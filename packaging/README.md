# Packaging

What is here, and what each thing is for.

## `udev/99-openwave-rtlsdr.rules`

Lets an RTL-SDR dongle be opened without root. See
[the installation guide](../docs/installing.md#udev-rules-for-rtl-sdr).

## Wheel and source distribution

Built by `python -m build` and published to PyPI by the release workflow. A released wheel
carries the built Angular interface, so `pip install "openwave[api]"` followed by
`openwave serve` works with no Node installed.

```bash
pip install build
python -m build
twine check dist/*
```

## Debian package

`debian/` holds a `nfpm` configuration, which builds a `.deb` from the wheel without needing the
Debian toolchain:

```bash
pip wheel --no-deps -w dist .
nfpm package --config packaging/debian/nfpm.yaml --packager deb --target dist/
```

The package depends on `python3` and recommends `librtlsdr0` and `libvlc-dev`, and installs the
udev rules. It is a convenience, not a substitute for a proper Debian package reviewed by
Debian — and it is **not tested**, for the same reason the hardware drivers are not: there is no
Debian machine here to install it on. A report from somebody who has one is welcome.

## What is deliberately absent

**No AppImage.** OpenWave is a command-line tool and a local server, not a desktop application.
An AppImage would bundle a Python runtime to run something `pip` installs in a second, and would
make the optional extras — which are the whole point of the installation story — impossible to
choose between.

**No Flatpak or Snap.** Both sandbox USB access, which is the one thing this program needs.
Getting a receiver through either is more configuration than installing a udev rule.
