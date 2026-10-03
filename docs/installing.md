# Installing

OpenWave targets Linux and Python 3.11 or later.

## Without a receiver

Everything works against a simulated receiver, so you can start here whatever hardware you have:

```bash
pip install openwave
openwave scan fm --demo
```

## With a receiver

The hardware drivers are optional extras, so that somebody who only wants one of them does not
install the other's dependencies.

=== "RTL-SDR"

    ```bash
    pip install "openwave[rtlsdr]"
    ```

    The extra installs the Python bindings. `librtlsdr` itself comes from your package manager:

    ```bash
    sudo apt install librtlsdr0      # Debian, Ubuntu
    sudo dnf install rtl-sdr         # Fedora
    ```

    Then see [udev rules](#udev-rules-for-rtl-sdr), without which the dongle can only be opened
    as root.

=== "DVB-T tuner"

    No extra is needed: the Linux DVB API is part of the kernel.

    What is needed is the driver and, for most tuners, firmware. `dmesg` after plugging it in
    says whether the firmware was found:

    ```bash
    dmesg | tail -20
    ```

    A line about a missing firmware file means the tuner will not work until you install it.
    Most distributions package these as `linux-firmware`.

=== "Playback"

    ```bash
    pip install "openwave[media]"
    sudo apt install libvlc-dev      # Debian, Ubuntu
    sudo dnf install vlc-devel       # Fedora
    ```

    The extra installs the Python bindings; libVLC itself comes from your package manager.

=== "The HTTP API and interface"

    ```bash
    pip install "openwave[api]"
    openwave serve
    ```

    A released wheel carries the built interface, so there is nothing to build. Working on the
    interface itself needs Node 20.19 or later — see [Contributing](contributing.md).

Install several at once with `pip install "openwave[rtlsdr,media,api]"`.

## udev rules for RTL-SDR

A USB device belongs to root by default, so `openwave probe -d rtlsdr` fails with a permission
error until a rule says otherwise. Running it as root instead is a bad trade: OpenWave opens
network ports, and nothing it does needs privilege.

OpenWave ships the rule. Install it with:

```bash
sudo cp packaging/udev/99-openwave-rtlsdr.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug the dongle and plug it in again. The rule gives the device to the `plugdev` group,
so you need to be in it:

```bash
sudo usermod -aG plugdev "$USER"
```

Group membership is read at login, so log out and in again — or start a new session with
`newgrp plugdev` to test it without logging out.

Check it worked:

```bash
openwave devices
openwave probe -d rtlsdr
```

## DVB-T permissions

The kernel puts DVB devices in the `video` group, so there is usually no rule to install — only
a group to join:

```bash
sudo usermod -aG video "$USER"
```

Then log out and in again, and:

```bash
openwave scan tv --tuner linuxdvb
```

!!! tip "DVB-T or DVB-T2?"

    A DVB-T2 broadcast will not lock with the demodulator set to DVB-T, and most of Europe now
    transmits DVB-T2. If a scan finds nothing, try the other:

    ```bash
    openwave scan tv --tuner linuxdvb2
    ```

## From a checkout

```bash
git clone https://github.com/Mxlione/Openwave.git
cd Openwave
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`[dev]` alone runs the whole test suite, because the tests use the simulated receivers.
