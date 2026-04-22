"""Tests for runtime observability CLI commands."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from click.testing import CliRunner


def _env_with_key(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SOTA_API_KEY", raising=False)


class TestStatus:
    def test_status_json(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = {
            "id": "a1", "name": "alpha", "status": "active",
            "capabilities": ["code-review"], "last_seen_at": None,
            "wallet_address": "w", "description": None,
            "icon_url": None, "webhook_url": None, "sdk_version": None,
            "created_at": "t", "updated_at": None, "sandbox_regate": False,
        }
        with patch("sota_sdk.cli_commands.runtime.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["status", "--json"])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == fake

    def test_status_pretty_shows_fields(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = {
            "id": "a-uuid-12345678", "name": "alpha", "status": "active",
            "capabilities": ["code-review"], "last_seen_at": "2026-04-22T00:00:00Z",
            "wallet_address": "w", "description": None,
            "icon_url": None, "webhook_url": None, "sdk_version": None,
            "created_at": "t", "updated_at": None, "sandbox_regate": False,
        }
        with patch("sota_sdk.cli_commands.runtime.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["status"])
            assert result.exit_code == 0
            assert "alpha" in result.output
            # Status / capability should appear in output
            assert "active" in result.output
            assert "code-review" in result.output

    def test_status_no_context_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SOTA_API_KEY", raising=False)
        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 1


class TestWatch:
    def test_watch_exits_on_status_change(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        pages = [
            {"id": "a1", "name": "x", "status": "sandbox",
             "capabilities": [], "last_seen_at": None,
             "wallet_address": None, "description": None, "icon_url": None,
             "webhook_url": None, "sdk_version": None, "created_at": "t",
             "updated_at": None, "sandbox_regate": False},
            {"id": "a1", "name": "x", "status": "active",
             "capabilities": [], "last_seen_at": None,
             "wallet_address": None, "description": None, "icon_url": None,
             "webhook_url": None, "sdk_version": None, "created_at": "t",
             "updated_at": None, "sandbox_regate": False},
        ]
        idx = {"i": 0}

        async def fake_profile():
            i = idx["i"]
            idx["i"] += 1
            if i >= len(pages):
                raise KeyboardInterrupt
            return pages[i]

        with patch("sota_sdk.cli_commands.runtime.SOTAClient") as Mock, \
             patch("sota_sdk.cli_commands.runtime.time.sleep"):
            inst = Mock.return_value
            inst.get_profile = fake_profile
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["watch", "--interval", "0"])
            # Should fetch at least 2 profiles before exiting
            assert idx["i"] >= 2


class TestPing:
    def test_ping_success(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.runtime.httpx.get") as m_get, \
             patch("sota_sdk.cli_commands.runtime.SOTAClient") as Mock:
            m_get.return_value = MagicMock(status_code=200,
                                           raise_for_status=lambda: None)
            inst = Mock.return_value
            inst.get_profile = AsyncMock(return_value={"name": "a"})
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["ping"])
            assert result.exit_code == 0
            assert "reachable" in result.output.lower() or "\u2713" in result.output

    def test_ping_backend_down_exits_5(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        import httpx
        with patch("sota_sdk.cli_commands.runtime.httpx.get") as m_get:
            m_get.side_effect = httpx.HTTPError("down")
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["ping"])
            assert result.exit_code == 5

    def test_ping_bad_key_exits_3(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.runtime.httpx.get") as m_get, \
             patch("sota_sdk.cli_commands.runtime.SOTAClient") as Mock:
            m_get.return_value = MagicMock(status_code=200,
                                           raise_for_status=lambda: None)
            inst = Mock.return_value
            inst.get_profile = AsyncMock(
                side_effect=Exception("Unauthorized"),
            )
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["ping"])
            assert result.exit_code == 3


class TestRun:
    def test_run_executes_agent_py(self, tmp_path, monkeypatch):
        (tmp_path / "agent.py").write_text("print('hello')\n")
        monkeypatch.chdir(tmp_path)
        with patch("sota_sdk.cli_commands.runtime.subprocess.call") as m_call:
            m_call.return_value = 0
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["run"])
            m_call.assert_called_once()
            args = m_call.call_args.args[0]
            assert "agent.py" in args[-1]

    def test_run_no_agent_file_errors(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["run"])
        assert result.exit_code == 1
        assert "no agent" in result.output.lower()


class TestLogs:
    def test_oneshot_prints_banner_and_entries(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = {
            "entries": [
                {"id": 1, "agent_id": "a1", "event_type": "progress",
                 "level": "info", "job_id": "j1-uuid",
                 "payload": {"percent": 50, "message": "halfway"},
                 "created_at": "2026-04-22T00:00:00Z"},
            ],
            "next_since_id": None,
        }
        with patch("sota_sdk.cli_commands.runtime.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_activity_log = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["logs", "--no-follow"])
            assert result.exit_code == 0, result.output
            assert "Server-side events only" in result.output
            assert "halfway" in result.output

    def test_json_mode_is_ndjson(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = {
            "entries": [
                {"id": 1, "agent_id": "a1", "event_type": "progress",
                 "level": "info", "job_id": "j1",
                 "payload": {"percent": 50}, "created_at": "t"},
                {"id": 2, "agent_id": "a1", "event_type": "bid_won",
                 "level": "info", "job_id": "j2",
                 "payload": {}, "created_at": "t2"},
            ],
            "next_since_id": None,
        }
        with patch("sota_sdk.cli_commands.runtime.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_activity_log = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["logs", "--json", "--no-follow"])
            assert result.exit_code == 0
            # NDJSON: each non-empty line is its own JSON object
            lines = [l for l in result.output.splitlines() if l.strip()]
            parsed = [json.loads(l) for l in lines]
            assert len(parsed) == 2
            assert parsed[0]["event_type"] == "progress"
            assert parsed[1]["event_type"] == "bid_won"

    def test_follow_polls_with_cursor(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        pages = [
            {"entries": [{"id": 1, "agent_id": "a1", "event_type": "x",
                          "level": "info", "job_id": None,
                          "payload": {}, "created_at": "t"}],
             "next_since_id": 1},
            {"entries": [{"id": 2, "agent_id": "a1", "event_type": "y",
                          "level": "info", "job_id": None,
                          "payload": {}, "created_at": "t"}],
             "next_since_id": None},
        ]
        call_idx = {"i": 0}

        async def fake_get(**kwargs):
            i = call_idx["i"]
            call_idx["i"] += 1
            if i >= len(pages):
                raise KeyboardInterrupt
            return pages[i]

        with patch("sota_sdk.cli_commands.runtime.SOTAClient") as Mock, \
             patch("sota_sdk.cli_commands.runtime.time.sleep"):
            inst = Mock.return_value
            inst.get_activity_log = fake_get
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["logs", "--follow", "--interval", "0"])
            # Should have fetched at least the 2 pages before interrupt
            assert call_idx["i"] >= 2

    def test_job_filter(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = {"entries": [], "next_since_id": None}
        captured = {}

        async def fake_get(**kwargs):
            captured.update(kwargs)
            return fake

        with patch("sota_sdk.cli_commands.runtime.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_activity_log = fake_get
            inst.close = AsyncMock()
            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, [
                "logs", "--no-follow", "--job", "jjjj", "--limit", "100",
            ])
            assert captured.get("job_id") == "jjjj"
            assert captured.get("limit") == 100
