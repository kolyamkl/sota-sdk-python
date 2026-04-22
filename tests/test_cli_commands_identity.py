"""Tests for identity CLI commands: logout, whoami, version."""

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner


def _home_with_creds(tmp_path, monkeypatch, email="alice@example.com"):
    cred_dir = tmp_path / ".sota"
    cred_dir.mkdir()
    (cred_dir / "credentials").write_text(
        json.dumps({"email": email, "jwt": "fake-jwt"})
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    return cred_dir


def _home_no_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))


class TestLogout:
    def test_logout_deletes_credentials_file(self, tmp_path, monkeypatch):
        cred_dir = _home_with_creds(tmp_path, monkeypatch)
        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["logout", "--yes"])
        assert result.exit_code == 0, result.output
        assert "Logged out" in result.output
        assert not (cred_dir / "credentials").exists()

    def test_logout_no_creds_already_logged_out(self, tmp_path, monkeypatch):
        _home_no_creds(tmp_path, monkeypatch)
        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["logout", "--yes"])
        assert result.exit_code == 0
        assert "Already logged out" in result.output

    def test_logout_declined_keeps_file(self, tmp_path, monkeypatch):
        cred_dir = _home_with_creds(tmp_path, monkeypatch)
        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["logout"], input="n\n")
        # Click's abort returns exit code 1 (ClickException.Abort)
        assert result.exit_code != 0
        assert (cred_dir / "credentials").exists()


class TestWhoami:
    def test_whoami_prints_email(self, tmp_path, monkeypatch):
        _home_with_creds(tmp_path, monkeypatch, email="alice@example.com")
        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["whoami"])
        assert result.exit_code == 0, result.output
        assert "alice@example.com" in result.output

    def test_whoami_not_logged_in_exits_3(self, tmp_path, monkeypatch):
        _home_no_creds(tmp_path, monkeypatch)
        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["whoami"])
        assert result.exit_code == 3
        assert "not logged in" in result.output.lower()


class TestVersion:
    def test_version_prints_something(self):
        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "sota" in result.output.lower()
