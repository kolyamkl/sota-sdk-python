"""Tests for keys CLI group."""

import json
import os
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from click.testing import CliRunner


def _env_with_key(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SOTA_API_KEY=sk_old\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SOTA_API_KEY", raising=False)


def _home_with_creds(tmp_path, monkeypatch):
    cred_dir = tmp_path / ".sota"
    cred_dir.mkdir(exist_ok=True)
    (cred_dir / "credentials").write_text(
        '{"email":"a@b.com","jwt":"fake-jwt"}'
    )
    monkeypatch.setenv("HOME", str(tmp_path))


class TestKeysList:
    def test_keys_list_json(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = {
            "keys": [
                {"id": "k1", "label": "primary",
                 "key_prefix": "sk_live_abcd",
                 "created_at": "2026-04-22T00:00:00Z",
                 "expires_at": "2027-04-22T00:00:00Z",
                 "revoked_at": None, "last_used_at": None},
            ],
        }
        with patch("sota_sdk.cli_commands.keys.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_keys = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["keys", "list", "--json"])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == fake

    def test_keys_list_include_revoked(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.keys.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_keys = AsyncMock(return_value={"keys": []})
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(
                main, ["keys", "list", "--include-revoked"],
            )
            assert result.exit_code == 0
            inst.list_keys.assert_awaited_once_with(include_revoked=True)

    def test_keys_list_empty_friendly(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.keys.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_keys = AsyncMock(return_value={"keys": []})
            inst.close = AsyncMock()
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["keys", "list"])
            assert result.exit_code == 0
            assert "no keys" in result.output.lower()


class TestKeysRotate:
    def test_rotate_yes_updates_env_and_verifies(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.keys.SOTAClient") as Mock:
            # Two separate instances: first for rotate, second for verify.
            rotate_inst = MagicMock()
            rotate_inst.rotate_api_key = AsyncMock(return_value={
                "api_key": "sk_new", "token": "t", "expires_in": 900,
            })
            rotate_inst.close = AsyncMock()
            verify_inst = MagicMock()
            verify_inst.get_profile = AsyncMock(return_value={"name": "a"})
            verify_inst.close = AsyncMock()
            Mock.side_effect = [rotate_inst, verify_inst]

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["keys", "rotate", "--yes"])
            assert result.exit_code == 0, result.output

            # .env has the new key
            env_content = (tmp_path / ".env").read_text()
            assert "SOTA_API_KEY=sk_new" in env_content
            # Backup with old key exists
            assert (tmp_path / ".env.bak").exists()
            assert "SOTA_API_KEY=sk_old" in (tmp_path / ".env.bak").read_text()
            # User was warned about restart
            assert "restart" in result.output.lower()
            # Rotate + verify both called
            rotate_inst.rotate_api_key.assert_awaited_once()
            verify_inst.get_profile.assert_awaited_once()

    def test_rotate_declined(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.keys.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.rotate_api_key = AsyncMock()
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["keys", "rotate"], input="n\n")
            # User declined — rotate not called, .env unchanged
            inst.rotate_api_key.assert_not_awaited()
            assert (tmp_path / ".env").read_text().strip() == "SOTA_API_KEY=sk_old"


class TestKeysCreate:
    def test_create_prints_raw_key(self, tmp_path, monkeypatch):
        _home_with_creds(tmp_path, monkeypatch)
        _env_with_key(tmp_path, monkeypatch)

        with patch("sota_sdk.cli_commands.keys.SOTAClient") as Mock:
            # First call: get_profile to find agent_id; second call: create_api_key
            gp_inst = MagicMock()
            gp_inst.get_profile = AsyncMock(return_value={"id": "a1"})
            gp_inst.close = AsyncMock()
            create_inst = MagicMock()
            create_inst.set_jwt = MagicMock()
            create_inst.create_api_key = AsyncMock(return_value={
                "api_key": "sk_brand_new", "key_id": "k2",
                "key_prefix": "sk_brand", "expires_at": "2027-04-22",
            })
            create_inst.close = AsyncMock()
            Mock.side_effect = [gp_inst, create_inst]

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, [
                "keys", "create", "--label", "ci", "--expires-days", "30",
            ])
            assert result.exit_code == 0, result.output
            assert "sk_brand_new" in result.output
            # Warning about one-time display
            assert "only time" in result.output.lower() or "once" in result.output.lower()
            create_inst.create_api_key.assert_awaited_once_with(
                agent_id="a1", label="ci", expires_days=30,
            )
            create_inst.set_jwt.assert_called_once_with("fake-jwt")


class TestKeysRevoke:
    def test_revoke_yes_calls_sdk(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.keys.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.revoke_key = AsyncMock(return_value={
                "revoked": True, "key_id": "k1", "already_revoked": False,
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["keys", "revoke", "k1", "--yes"])
            assert result.exit_code == 0, result.output
            inst.revoke_key.assert_awaited_once_with("k1")
            assert "k1" in result.output

    def test_revoke_already_revoked_note(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.keys.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.revoke_key = AsyncMock(return_value={
                "revoked": True, "key_id": "k1", "already_revoked": True,
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["keys", "revoke", "k1", "--yes"])
            assert result.exit_code == 0
            assert "already" in result.output.lower()

    def test_revoke_declined(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.keys.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.revoke_key = AsyncMock()
            inst.close = AsyncMock()
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["keys", "revoke", "k1"], input="n\n")
            inst.revoke_key.assert_not_awaited()
