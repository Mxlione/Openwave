"""Tests for the command line interface.

The command line is what somebody runs first, and its error messages are the project's first
impression. Two things are checked: that each command does what it says, and that the advice it
prints is actually correct -- a message telling somebody to run the wrong command is worse than
no message.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from typer.testing import CliRunner

from openwave import __version__
from openwave.cli import app
from openwave.sdr.iq_file import IqMetadata, write_capture

runner = CliRunner()


def output(*args: str) -> str:
    """Run the command line and return everything it printed, with wrapping undone.

    Rich wraps to the terminal width, so a message can be split across lines at any point.
    Collapsing whitespace lets a test assert on the message rather than on the layout.
    """
    result = runner.invoke(app, list(args))
    return " ".join(result.output.split())


class TestVersion:
    def test_it_prints_the_version(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_bare_invocation_shows_help(self) -> None:
        assert "Scan, detect and play" in output()


class TestDevices:
    def test_the_simulator_is_always_listed(self) -> None:
        result = runner.invoke(app, ["devices"])
        assert result.exit_code == 0
        assert "mock:0" in output("devices")

    def test_it_lists_the_available_drivers(self) -> None:
        text = output("devices")
        for name in ("mock", "rtlsdr", "file"):
            assert name in text

    def test_the_install_hint_names_the_extra_it_means(self) -> None:
        # Rich reads [rtlsdr] as a markup tag unless it is escaped, which turns the advice into
        # 'pip install "openwave"' -- a command that installs no driver at all.
        assert 'pip install "openwave[rtlsdr]"' in output("devices")


class TestProbe:
    def test_it_reports_the_simulator_working(self) -> None:
        result = runner.invoke(app, ["probe"])
        assert result.exit_code == 0
        text = " ".join(result.output.split())
        assert "the receiver works" in text
        assert "4096 samples" in text

    def test_it_reports_the_tuning_range(self) -> None:
        assert "24.000 MHz to 1.766 GHz" in output("probe")

    def test_a_missing_driver_fails_with_a_usable_message(self) -> None:
        result = runner.invoke(app, ["probe", "--device", "rtlsdr"])
        assert result.exit_code == 1
        text = " ".join(result.output.split())
        assert "DriverUnavailableError" in text
        assert 'pip install "openwave[rtlsdr]"' in text

    def test_an_unknown_driver_fails_with_a_usable_message(self) -> None:
        result = runner.invoke(app, ["probe", "--device", "hackrf"])
        assert result.exit_code == 1
        assert "no driver called" in " ".join(result.output.split())

    def test_it_probes_a_capture(self, tmp_path: Path) -> None:
        path = tmp_path / "band.cf32"
        write_capture(
            path,
            np.zeros(8192, dtype=np.complex64),
            metadata=IqMetadata(sample_rate_hz=2.4e6, center_freq_hz=98e6),
        )
        result = runner.invoke(app, ["probe", "--device", f"file:{path}"])
        assert result.exit_code == 0
        assert "the receiver works" in " ".join(result.output.split())


class TestNotYetImplemented:
    def test_scanning_fm_says_which_version_brings_it(self) -> None:
        result = runner.invoke(app, ["scan", "fm"])
        assert result.exit_code == 1
        text = " ".join(result.output.split())
        assert "not implemented yet" in text
        assert "v0.1" in text
        assert "task 22" in text

    def test_scanning_tv_says_which_version_brings_it(self) -> None:
        result = runner.invoke(app, ["scan", "tv"])
        assert result.exit_code == 1
        text = " ".join(result.output.split())
        assert "v0.4" in text
        assert "task 42" in text
