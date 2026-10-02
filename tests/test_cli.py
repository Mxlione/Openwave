"""Tests for the command line interface.

The command line is what somebody runs first, and its error messages are the project's first
impression. Two things are checked: that each command does what it says, and that the advice it
prints is actually correct -- a message telling somebody to run the wrong command is worse than
no message.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import welch
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


class TestScanFm:
    def test_the_demonstration_band_produces_a_station_list(self) -> None:
        # The first thing somebody runs after cloning the repository, on a machine with no
        # receiver attached. It has to show something.
        result = runner.invoke(app, ["scan", "fm", "--demo"])
        assert result.exit_code == 0
        text = " ".join(result.output.split())
        assert "8 stations" in text
        for frequency in ("88.1 MHz", "98.0 MHz", "107.9 MHz"):
            assert frequency in text

    def test_it_reports_which_stations_are_stereo(self) -> None:
        text = output("scan", "fm", "--demo", "--no-rds")
        assert "stereo" in text
        assert "mono" in text

    def test_skipping_the_stereo_check_says_so_rather_than_guessing(self) -> None:
        text = output("scan", "fm", "--demo", "--no-stereo", "--no-rds")
        assert "unchecked" in text
        assert "mono" not in text

    def test_an_empty_band_explains_what_to_try(self) -> None:
        # The plain simulator carries nothing, so this is what a first run without --demo
        # looks like. It must not read as a failure.
        result = runner.invoke(app, ["scan", "fm", "--device", "mock"])
        assert result.exit_code == 0
        text = " ".join(result.output.split())
        assert "No stations found" in text
        assert "--demo" in text

    def test_json_output_is_machine_readable(self) -> None:
        result = runner.invoke(app, ["scan", "fm", "--demo", "--json", "--no-rds"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["band"] == "FM broadcast"
        assert payload["complete"] is True
        assert payload["channels_measured"] == 206
        assert len(payload["stations"]) == 8
        assert {"freq_hz", "snr_db", "stereo"} <= set(payload["stations"][0])

    def test_a_higher_threshold_finds_fewer_stations(self) -> None:
        lenient = json.loads(
            runner.invoke(app, ["scan", "fm", "--demo", "--json", "--no-rds", "-t", "10"]).output
        )
        strict = json.loads(
            runner.invoke(app, ["scan", "fm", "--demo", "--json", "--no-rds", "-t", "45"]).output
        )
        assert len(strict["stations"]) < len(lenient["stations"])

    def test_an_unknown_band_lists_the_valid_ones(self) -> None:
        result = runner.invoke(app, ["scan", "fm", "--demo", "--no-rds", "--band", "shortwave"])
        assert result.exit_code == 2
        text = " ".join(result.output.split())
        assert "Unknown band" in text
        assert "fm-japan" in text

    def test_the_japanese_band_plan_is_available(self) -> None:
        payload = json.loads(
            runner.invoke(
                app, ["scan", "fm", "--demo", "--json", "--no-rds", "--band", "fm-japan"]
            ).output
        )
        assert payload["band"] == "FM broadcast (Japan)"

    def test_station_names_are_read_from_rds(self) -> None:
        # The demonstration band carries RDS, so a scan of it shows names as a real one would.
        text = output("scan", "fm", "--demo")
        assert "OPENWAVE" in text
        assert "CITY FM" in text

    def test_switching_rds_off_leaves_the_name_column_out(self) -> None:
        # No names to show, so the column is omitted rather than left permanently blank.
        text = output("scan", "fm", "--demo", "--no-rds")
        assert "OPENWAVE" not in text
        assert "No station names" in text

    def test_a_receiver_that_cannot_reach_the_band_fails_cleanly(self) -> None:
        result = runner.invoke(app, ["scan", "fm", "--device", "hackrf"])
        assert result.exit_code == 1
        assert "no driver called" in " ".join(result.output.split())


class TestListen:
    def test_recording_writes_a_playable_wav_file(self, tmp_path: Path) -> None:
        # Recording rather than playing, because a test machine has no audio device and the
        # standard library can check the file independently.
        destination = tmp_path / "station.wav"
        result = runner.invoke(
            app,
            ["listen", "88.1", "--demo", "--seconds", "0.4", "--record", str(destination)],
        )
        assert result.exit_code == 0, result.output
        assert destination.is_file()

        with wave.open(str(destination)) as reader:
            assert reader.getnchannels() == 2
            assert reader.getframerate() == 48_000
            assert reader.getsampwidth() == 2
            assert reader.getnframes() == pytest.approx(0.4 * 48_000, rel=0.02)

    def test_the_recorded_audio_is_the_station_that_was_asked_for(self, tmp_path: Path) -> None:
        # 88.1 MHz in the demonstration band carries 1 kHz on the left and 400 Hz on the right.
        destination = tmp_path / "station.wav"
        runner.invoke(
            app,
            ["listen", "88.1", "--demo", "--seconds", "0.5", "--record", str(destination)],
        )
        with wave.open(str(destination)) as reader:
            raw = reader.readframes(reader.getnframes())
            rate = reader.getframerate()

        samples = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
        left, right = samples[0::2], samples[1::2]

        def level_at(channel: np.ndarray, target_hz: float) -> float:
            freqs, psd = welch(channel, fs=rate, nperseg=min(8192, len(channel)))
            return float(psd[int(np.argmin(np.abs(freqs - target_hz)))])

        assert level_at(left, 1000.0) > 10 * level_at(left, 400.0)
        assert level_at(right, 400.0) > 10 * level_at(right, 1000.0)

    def test_it_reports_stereo_in_the_summary(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "listen",
                "88.1",
                "--demo",
                "--seconds",
                "0.3",
                "--record",
                str(tmp_path / "s.wav"),
            ],
        )
        assert "stereo" in " ".join(result.output.split())

    def test_a_frequency_in_hertz_is_understood_as_well_as_megahertz(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "listen",
                "88100000",
                "--demo",
                "--seconds",
                "0.3",
                "--record",
                str(tmp_path / "s.wav"),
            ],
        )
        assert result.exit_code == 0
        assert "88.100 MHz" in " ".join(result.output.split())

    def test_recording_without_a_duration_is_refused(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["listen", "98.0", "--demo", "--record", str(tmp_path / "s.wav")]
        )
        assert result.exit_code == 2
        assert "needs --seconds" in " ".join(result.output.split())

    def test_a_frequency_outside_the_band_is_flagged_but_attempted(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "listen",
                "120.0",
                "--demo",
                "--seconds",
                "0.2",
                "--record",
                str(tmp_path / "s.wav"),
            ],
        )
        # Recording skips the warning, but the command still has to work: the FM band plan is
        # not the same everywhere, and a receiver can tune outside it.
        assert result.exit_code == 0

    def test_an_unknown_driver_fails_cleanly(self) -> None:
        result = runner.invoke(app, ["listen", "98.0", "--device", "hackrf"])
        assert result.exit_code == 1
        assert "no driver called" in " ".join(result.output.split())


class TestScanTv:
    def test_the_demonstration_multiplexes_produce_a_channel_list(self) -> None:
        result = runner.invoke(app, ["scan", "tv", "--demo", "--lock-timeout", "0.1"])
        assert result.exit_code == 0, result.output
        text = " ".join(result.output.split())
        assert "9 services in 3 multiplexes" in text
        for name in ("OpenWave One", "OpenWave HD", "Local Radio"):
            assert name in text

    def test_television_and_radio_are_distinguished(self) -> None:
        text = output("scan", "tv", "--demo", "--lock-timeout", "0.1")
        assert "television" in text
        assert "radio" in text

    def test_a_scrambled_service_is_listed_and_marked(self) -> None:
        # Listing it matters: a list that silently omits what cannot be watched leaves a viewer
        # wondering where a channel went.
        text = output("scan", "tv", "--demo", "--lock-timeout", "0.1")
        assert "Premium Sport" in text
        assert "scrambled" in text

    def test_scrambled_services_can_be_left_out(self) -> None:
        text = output("scan", "tv", "--demo", "--lock-timeout", "0.1", "--no-scrambled")
        assert "Premium Sport" not in text

    def test_json_output_is_machine_readable(self) -> None:
        result = runner.invoke(app, ["scan", "tv", "--demo", "--lock-timeout", "0.1", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["band"] == "UHF"
        assert payload["channels_tried"] == 28
        assert len(payload["multiplexes"]) == 3
        assert len(payload["channels"]) == 9
        assert {"name", "service_id", "mux_freq_hz", "logical_channel"} <= set(
            payload["channels"][0]
        )

    def test_the_multiplexes_are_reported_with_their_channel_numbers(self) -> None:
        payload = json.loads(
            runner.invoke(app, ["scan", "tv", "--demo", "--lock-timeout", "0.1", "--json"]).output
        )
        assert {mux["channel"] for mux in payload["multiplexes"]} == {24, 27, 31}

    def test_an_empty_band_explains_what_to_try(self) -> None:
        result = runner.invoke(app, ["scan", "tv", "--tuner", "mockdvb", "--lock-timeout", "0.05"])
        assert result.exit_code == 0
        text = " ".join(result.output.split())
        assert "No services found" in text
        assert "--demo" in text

    def test_an_unknown_band_lists_the_valid_ones(self) -> None:
        result = runner.invoke(app, ["scan", "tv", "--demo", "--band", "satellite"])
        assert result.exit_code == 2
        text = " ".join(result.output.split())
        assert "Unknown band" in text
        assert "vhf" in text

    def test_the_vhf_plan_is_available(self) -> None:
        payload = json.loads(
            runner.invoke(
                app,
                ["scan", "tv", "--demo", "--band", "vhf", "--lock-timeout", "0.05", "--json"],
            ).output
        )
        assert payload["band"] == "VHF band III"

    def test_an_unknown_tuner_fails_cleanly(self) -> None:
        result = runner.invoke(app, ["scan", "tv", "--tuner", "hauppauge"])
        assert result.exit_code == 1
        assert "no DVB tuner driver" in " ".join(result.output.split())

    def test_a_real_tuner_with_no_hardware_fails_cleanly(self) -> None:
        # Which is what somebody without a tuner sees, so it has to read as advice rather than
        # as a crash.
        result = runner.invoke(app, ["scan", "tv", "--tuner", "linuxdvb", "--lock-timeout", "0.05"])
        assert result.exit_code == 1
        assert "no DVB frontend" in " ".join(result.output.split())
