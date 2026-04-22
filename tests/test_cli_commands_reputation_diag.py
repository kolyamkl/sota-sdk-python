"""Tests for reputation + diagnostics CLI commands."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from click.testing import CliRunner


def _env_with_key(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SOTA_API_KEY", raising=False)


class TestReputation:
    def test_reputation_json(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.reputation_diag.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value={"id": "a1"})
            inst.get_reputation = AsyncMock(return_value={
                "agent_id": "a1", "avg_rating": 4.2, "jobs_completed": 7,
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["reputation", "--json"])
            assert result.exit_code == 0, result.output
            parsed = json.loads(result.output)
            assert parsed["avg_rating"] == 4.2


class TestDoctor:
    def test_doctor_all_good(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.reputation_diag.httpx.get") as m_get, \
             patch("sota_sdk.cli_commands.reputation_diag.SOTAClient") as Mock:
            # /health and /onboard both 200
            m_get.return_value = MagicMock(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"available_capabilities":
                              ["web-scraping", "code-review"]},
            )
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value={
                "name": "alpha", "status": "active",
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["doctor"])
            assert result.exit_code == 0, result.output
            assert "All checks passed" in result.output
            assert "\u2713" in result.output  # check mark

    def test_doctor_bad_backend_exits_1(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        import httpx as hx
        with patch("sota_sdk.cli_commands.reputation_diag.httpx.get") as m_get:
            m_get.side_effect = hx.HTTPError("connection refused")
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["doctor"])
            assert result.exit_code == 1
            assert "\u2717" in result.output  # cross mark
            assert "Issues found" in result.output

    def test_doctor_bad_key_exits_1(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.reputation_diag.httpx.get") as m_get, \
             patch("sota_sdk.cli_commands.reputation_diag.SOTAClient") as Mock:
            m_get.return_value = MagicMock(
                status_code=200, raise_for_status=lambda: None,
                json=lambda: {"available_capabilities": []},
            )
            inst = Mock.return_value
            inst.get_profile = AsyncMock(
                side_effect=Exception("Unauthorized"),
            )
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["doctor"])
            assert result.exit_code == 1
            assert "API key valid" in result.output


class TestCapabilities:
    def test_capabilities_json(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.reputation_diag.httpx.get") as m_get:
            m_get.return_value = MagicMock(
                status_code=200, raise_for_status=lambda: None,
                json=lambda: {"available_capabilities":
                              ["web-scraping", "code-review"]},
            )
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["capabilities", "--json"])
            assert result.exit_code == 0, result.output
            parsed = json.loads(result.output)
            assert parsed["available_capabilities"] == [
                "web-scraping", "code-review",
            ]

    def test_capabilities_pretty(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.reputation_diag.httpx.get") as m_get:
            m_get.return_value = MagicMock(
                status_code=200, raise_for_status=lambda: None,
                json=lambda: {"available_capabilities": ["web-scraping"]},
            )
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["capabilities"])
            assert result.exit_code == 0
            assert "web-scraping" in result.output


class TestOnboard:
    def test_onboard_prints_markdown(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.reputation_diag.httpx.get") as m_get:
            m_get.return_value = MagicMock(
                status_code=200, raise_for_status=lambda: None,
                text="# SOTA Onboarding\n\nHello!\n",
            )
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["onboard"])
            assert result.exit_code == 0
            assert "SOTA Onboarding" in result.output
