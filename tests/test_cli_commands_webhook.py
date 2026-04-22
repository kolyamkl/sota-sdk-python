"""Tests for webhook CLI commands."""

import hmac
import hashlib
import pytest
from unittest.mock import MagicMock, patch
from click.testing import CliRunner


class TestWebhookVerify:
    def test_valid_signature(self, tmp_path, monkeypatch):
        secret = "s3cr3t"
        body = b'{"event":"test"}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        f = tmp_path / "body.json"
        f.write_bytes(body)
        monkeypatch.setenv("SOTA_WEBHOOK_SECRET", secret)

        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "webhook", "verify", str(f), "--sig", sig,
        ])
        assert result.exit_code == 0, result.output
        assert "valid" in result.output.lower()

    def test_invalid_signature(self, tmp_path, monkeypatch):
        secret = "s3cr3t"
        body = b'{"event":"test"}'
        wrong_sig = "f" * 64
        f = tmp_path / "body.json"
        f.write_bytes(body)
        monkeypatch.setenv("SOTA_WEBHOOK_SECRET", secret)

        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "webhook", "verify", str(f), "--sig", wrong_sig,
        ])
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    def test_no_secret_errors(self, tmp_path, monkeypatch):
        f = tmp_path / "body.json"
        f.write_bytes(b"{}")
        monkeypatch.delenv("SOTA_WEBHOOK_SECRET", raising=False)
        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "webhook", "verify", str(f), "--sig", "x",
        ])
        assert result.exit_code != 0
        assert "secret" in result.output.lower()


class TestWebhookTest:
    def test_sends_signed_post(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOTA_WEBHOOK_SECRET", "s3cr3t")
        with patch("sota_sdk.cli_commands.webhook.httpx.post") as m_post:
            m_post.return_value = MagicMock(
                status_code=200, text="ok",
            )
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, [
                "webhook", "test", "--url", "http://localhost:8787",
                "--job-id", "j-smoke",
            ])
            assert result.exit_code == 0, result.output
            call = m_post.call_args
            assert call.args[0] == "http://localhost:8787"
            # Signature header present
            assert "X-SOTA-Signature" in call.kwargs["headers"]
            # Body contains the job id
            assert b"j-smoke" in call.kwargs["content"]
            assert "200" in result.output
