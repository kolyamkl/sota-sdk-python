"""Tests for sandbox + review CLI commands."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from click.testing import CliRunner


def _env_with_key(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SOTA_API_KEY", raising=False)


class TestSandboxStatus:
    def test_sandbox_status_json(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = {
            "id": "a1", "name": "alpha", "status": "sandbox",
            "capabilities": ["code-review"], "last_seen_at": None,
            "wallet_address": "w", "description": None,
            "icon_url": None, "webhook_url": None, "sdk_version": None,
            "created_at": "t", "updated_at": None, "sandbox_regate": False,
        }
        with patch("sota_sdk.cli_commands.sandbox.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["sandbox", "status", "--json"])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == fake

    def test_sandbox_status_mentions_gate_when_in_sandbox(
        self, tmp_path, monkeypatch,
    ):
        _env_with_key(tmp_path, monkeypatch)
        fake = {
            "id": "a1", "name": "alpha", "status": "sandbox",
            "capabilities": ["code-review"], "last_seen_at": None,
            "wallet_address": None, "description": None,
            "icon_url": None, "webhook_url": None, "sdk_version": None,
            "created_at": "t", "updated_at": None, "sandbox_regate": False,
        }
        with patch("sota_sdk.cli_commands.sandbox.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["sandbox", "status"])
            assert result.exit_code == 0
            assert "sandbox" in result.output.lower()


class TestSandboxRetry:
    def test_sandbox_retry_calls_sdk(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.sandbox.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.retry_test_job = AsyncMock(return_value={
                "retried": True, "test_job_id": "tj-abc",
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["sandbox", "retry", "tj-abc"])
            assert result.exit_code == 0, result.output
            inst.retry_test_job.assert_awaited_once_with("tj-abc")
            assert "tj-abc" in result.output


class TestReviewRequest:
    def test_review_request_posts(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.sandbox.httpx.post") as m_post:
            m_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"agent_id": "a1", "status": "pending_review"},
            )
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["review", "request"])
            assert result.exit_code == 0, result.output
            # Should POST to /api/v1/agents/request-review with X-API-Key
            call = m_post.call_args
            assert "/api/v1/agents/request-review" in call.args[0]
            assert call.kwargs["headers"]["X-API-Key"] == "sk_test"
            assert "a1" in result.output

    def test_review_request_4xx_exits_1(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.sandbox.httpx.post") as m_post:
            m_post.return_value = MagicMock(
                status_code=400, text="bad request",
            )
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["review", "request"])
            assert result.exit_code == 1
            assert "bad request" in result.output.lower() or "400" in result.output


class TestReviewStatus:
    def test_review_status_shows_current(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = {
            "id": "a1", "name": "alpha", "status": "pending_review",
            "capabilities": ["code-review"], "last_seen_at": None,
            "wallet_address": None, "description": None,
            "icon_url": None, "webhook_url": None, "sdk_version": None,
            "created_at": "t", "updated_at": None, "sandbox_regate": False,
        }
        with patch("sota_sdk.cli_commands.sandbox.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["review", "status", "--json"])
            assert result.exit_code == 0
            assert json.loads(result.output) == fake
