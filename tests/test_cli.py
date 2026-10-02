"""Smoke tests for the command line interface.

These exist so the packaging, entry point and CI pipeline are verified from the first commit,
before any signal processing is in place.
"""

from __future__ import annotations

from typer.testing import CliRunner

from openwave import __version__
from openwave.cli import app

runner = CliRunner()


def test_version_command_prints_the_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_bare_invocation_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Scan, detect and play" in result.stdout


def test_scan_fm_reports_it_is_not_implemented_yet() -> None:
    result = runner.invoke(app, ["scan", "fm"])
    assert result.exit_code == 1
    assert "not implemented yet" in result.stdout


def test_scan_tv_reports_it_is_not_implemented_yet() -> None:
    result = runner.invoke(app, ["scan", "tv"])
    assert result.exit_code == 1
    assert "not implemented yet" in result.stdout
