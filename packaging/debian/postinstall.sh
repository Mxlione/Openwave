#!/bin/sh
# Reload the udev rules this package installed, so a dongle already plugged in picks them up
# without the user having to know the incantation.
#
# Failures are not fatal. A package that refuses to install because udev was not running — in a
# container, say — is worse than one whose rules take effect at the next boot.
set -e

if command -v udevadm > /dev/null 2>&1; then
    udevadm control --reload-rules || true
    udevadm trigger --subsystem-match=usb || true
fi

cat <<'MESSAGE'

OpenWave installed.

To use an RTL-SDR dongle without root, join the plugdev group and log in again:

    sudo usermod -aG plugdev "$USER"

For a DVB-T tuner, join the video group instead.

Try it without any hardware:

    openwave scan fm --demo

MESSAGE

exit 0
