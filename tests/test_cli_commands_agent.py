"""Tests for `sota-agent agent` commands."""

import json
import os
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from click.testing import CliRunner


def _home_with_creds(tmp_path, monkeypatch):
    cred_dir = tmp_path / ".sota"
    cred_dir.mkdir()
    (cred_dir / "credentials").write_text(
        '{"email":"a@b.com","jwt":"fake-jwt"}'
    )
    monkeypatch.setenv("HOME", str(tmp_path))


def _home_no_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))


# ------- 8a: list / register / delete -------

class TestAgentList:
    def test_json_output(self, tmp_path, monkeypatch):
        _home_with_creds(tmp_path, monkeypatch)
        fake = {
            "agents": [
                {"id": "a1", "name": "alpha", "status": "active",
                 "capabilities": ["code-review"], "last_seen_at": None,
                 "created_at": "2026-04-22T00:00:00Z"},
            ],
            "total": 1,
        }
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.set_jwt = MagicMock()
            inst.list_agents = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["agent", "list", "--json"])
            assert result.exit_code == 0, result.output
            parsed = json.loads(result.output)
            assert parsed == fake
            inst.set_jwt.assert_called_once_with("fake-jwt")

    def test_status_filter_passes_through(self, tmp_path, monkeypatch):
        _home_with_creds(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.set_jwt = MagicMock()
            inst.list_agents = AsyncMock(return_value={"agents": [], "total": 0})
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(
                main, ["agent", "list", "--status", "sandbox", "--json"],
            )
            assert result.exit_code == 0
            inst.list_agents.assert_awaited_once_with(
                status="sandbox", include_deleted=False,
            )

    def test_not_logged_in_exits_3(self, tmp_path, monkeypatch):
        _home_no_creds(tmp_path, monkeypatch)
        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["agent", "list"])
        assert result.exit_code == 3
        assert "not logged in" in result.output.lower()


class TestAgentRegister:
    def test_standalone_register_uses_jwt_endpoint(self, tmp_path, monkeypatch):
        """Uses /register (JWT-auth), NOT /register/simple (password).
        Closes project_cli_register_auth_todo."""
        _home_with_creds(tmp_path, monkeypatch)
        monkeypatch.chdir(tmp_path)

        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.set_jwt = MagicMock()
            inst.register_agent_authenticated = AsyncMock(return_value={
                "agent_id": "a1", "api_key": "sk_new", "webhook_secret": "ws",
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, [
                "agent", "register",
                "--name", "my-new-agent",
                "--caps", "code-review",
                "--wallet", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
                "--desc", "a test agent",
            ])
            assert result.exit_code == 0, result.output
            inst.register_agent_authenticated.assert_awaited_once()
            call = inst.register_agent_authenticated.await_args
            # Never sends password (that's /register/simple path)
            assert "password" not in call.kwargs
            # .env was created in CWD
            env_path = tmp_path / ".env"
            assert env_path.exists()
            content = env_path.read_text()
            assert "SOTA_API_KEY=sk_new" in content
            assert "SOTA_AGENT_ID=a1" in content

    def test_register_splits_caps_on_commas(self, tmp_path, monkeypatch):
        _home_with_creds(tmp_path, monkeypatch)
        monkeypatch.chdir(tmp_path)
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.set_jwt = MagicMock()
            inst.register_agent_authenticated = AsyncMock(return_value={
                "agent_id": "a1", "api_key": "sk", "webhook_secret": "ws",
            })
            inst.close = AsyncMock()
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, [
                "agent", "register",
                "--name", "a", "--caps", "code-review, web-scraping",
                "--wallet", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
            ])
            assert result.exit_code == 0, result.output
            call = inst.register_agent_authenticated.await_args
            assert call.kwargs["capabilities"] == ["code-review", "web-scraping"]


class TestAgentDelete:
    def test_delete_prompts_without_yes(self, tmp_path, monkeypatch):
        _home_with_creds(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.set_jwt = MagicMock()
            inst.delete_agent = AsyncMock(return_value={
                "deleted": True, "agent_id": "a1", "already_deleted": False,
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["agent", "delete", "a1"],
                                   input="n\n")
            # User declined
            inst.delete_agent.assert_not_awaited()
            assert result.exit_code != 0

    def test_delete_yes_skips_prompt(self, tmp_path, monkeypatch):
        _home_with_creds(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.set_jwt = MagicMock()
            inst.delete_agent = AsyncMock(return_value={
                "deleted": True, "agent_id": "a1", "already_deleted": False,
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["agent", "delete", "a1", "--yes"])
            assert result.exit_code == 0, result.output
            inst.delete_agent.assert_awaited_once_with("a1")
            assert "Deleted" in result.output

    def test_delete_already_deleted_surfaces_flag(self, tmp_path, monkeypatch):
        _home_with_creds(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.set_jwt = MagicMock()
            inst.delete_agent = AsyncMock(return_value={
                "deleted": True, "agent_id": "a1", "already_deleted": True,
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["agent", "delete", "a1", "--yes"])
            assert result.exit_code == 0
            assert "already" in result.output.lower()


# ------- 8b: show / set / switch -------

class TestAgentShow:
    def test_show_from_cwd_env(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
        monkeypatch.chdir(tmp_path)
        fake = {
            "id": "a1", "name": "alpha", "description": "x",
            "capabilities": ["code-review"],
            "wallet_address": "w", "icon_url": None, "webhook_url": None,
            "sdk_version": None, "status": "active",
            "last_seen_at": None,
            "created_at": "2026-04-22T00:00:00Z",
            "updated_at": None, "sandbox_regate": False,
        }
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["agent", "show", "--json"])
            assert result.exit_code == 0
            assert json.loads(result.output) == fake


class TestAgentSet:
    def test_set_description(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
        monkeypatch.chdir(tmp_path)
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.update_profile = AsyncMock(return_value={
                "description": "new", "sandbox_regate": False,
                "status": "active",
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, [
                "agent", "set", "description", "new description",
            ])
            assert result.exit_code == 0, result.output
            inst.update_profile.assert_awaited_once()
            assert inst.update_profile.await_args.kwargs["description"] == "new description"

    def test_set_capabilities_splits(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
        monkeypatch.chdir(tmp_path)
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.update_profile = AsyncMock(return_value={
                "capabilities": ["code-review", "web-scraping"],
                "sandbox_regate": True, "status": "sandbox",
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, [
                "agent", "set", "capabilities",
                "code-review,web-scraping", "--yes",
            ])
            assert result.exit_code == 0, result.output
            args = inst.update_profile.await_args.kwargs
            assert args["capabilities"] == ["code-review", "web-scraping"]
            assert "re-gated" in result.output.lower() or "sandbox" in result.output.lower()

    def test_set_capabilities_prompts_on_regate(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
        monkeypatch.chdir(tmp_path)
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.update_profile = AsyncMock()
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(
                main, ["agent", "set", "capabilities",
                       "code-review,web-scraping"],
                input="n\n",
            )
            inst.update_profile.assert_not_awaited()


class TestAgentSwitch:
    def test_switch_is_stubbed_v1(self, tmp_path, monkeypatch):
        _home_with_creds(tmp_path, monkeypatch)
        monkeypatch.chdir(tmp_path)

        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["agent", "switch", "a1", "--yes"])
        # v1 stub: should return a clear error explaining the limitation.
        assert result.exit_code != 0
        assert (
            "not yet" in result.output.lower()
            or "not available" in result.output.lower()
            or "credentials" in result.output.lower()
        )


# ------- 8c: edit -------

import stat


class TestAgentEdit:
    def test_edit_no_changes_makes_no_patch(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EDITOR", "true")  # /bin/true exits 0 without edit

        fake = {
            "id": "a1", "name": "alpha", "description": "old",
            "capabilities": ["code-review"],
            "wallet_address": "w", "icon_url": None, "webhook_url": None,
            "sdk_version": None, "status": "active",
            "last_seen_at": None, "created_at": "t",
            "updated_at": None, "sandbox_regate": False,
        }
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value=fake)
            inst.update_profile = AsyncMock()
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["agent", "edit"])
            assert result.exit_code == 0, result.output
            inst.update_profile.assert_not_awaited()
            assert "no changes" in result.output.lower()

    def test_edit_applies_description_change(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
        monkeypatch.chdir(tmp_path)

        # Fake editor: rewrite description old→new
        script = tmp_path / "fake_editor.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            'sed -i.bak "s/description: old/description: new/" "$1"\n'
        )
        script.chmod(0o755)
        monkeypatch.setenv("EDITOR", str(script))

        fake = {
            "id": "a1", "name": "alpha", "description": "old",
            "capabilities": ["code-review"], "wallet_address": "w",
            "icon_url": None, "webhook_url": None, "sdk_version": None,
            "status": "active", "last_seen_at": None, "created_at": "t",
            "updated_at": None, "sandbox_regate": False,
        }
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value=fake)
            inst.update_profile = AsyncMock(return_value={
                **fake, "description": "new",
            })
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["agent", "edit"])
            assert result.exit_code == 0, result.output
            inst.update_profile.assert_awaited_once()
            assert inst.update_profile.await_args.kwargs.get("description") == "new"
            assert "description" in result.output.lower()

    def test_edit_capability_change_prompts_regate(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
        monkeypatch.chdir(tmp_path)

        # Fake editor that adds a second capability
        script = tmp_path / "fake_editor.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            'sed -i.bak "s/- code-review/- code-review\\n- web-scraping/" "$1"\n'
        )
        script.chmod(0o755)
        monkeypatch.setenv("EDITOR", str(script))

        fake = {
            "id": "a1", "name": "alpha", "description": "x",
            "capabilities": ["code-review"], "wallet_address": "w",
            "icon_url": None, "webhook_url": None, "sdk_version": None,
            "status": "active", "last_seen_at": None, "created_at": "t",
            "updated_at": None, "sandbox_regate": False,
        }
        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value=fake)
            inst.update_profile = AsyncMock()
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["agent", "edit"], input="n\n")
            inst.update_profile.assert_not_awaited()
