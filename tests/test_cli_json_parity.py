"""Parity tests: CLI --json output must match the goldens in golden/cli_json/.

The TypeScript SDK (Plan 3) runs the same tests against the same goldens;
any drift fails both suites."""

import json
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from click.testing import CliRunner


GOLDEN_DIR = Path(__file__).parent / "golden" / "cli_json"


def _load_fixture(name: str) -> dict:
    return json.loads((GOLDEN_DIR / f"{name}.fixture.json").read_text())


def _load_expected(name: str) -> dict:
    return json.loads((GOLDEN_DIR / f"{name}.expected.json").read_text())


def _env_with_key(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SOTA_API_KEY", raising=False)


def _home_with_creds(tmp_path, monkeypatch):
    cred_dir = tmp_path / ".sota"
    cred_dir.mkdir(exist_ok=True)
    (cred_dir / "credentials").write_text(
        '{"email":"a@b.com","jwt":"fake-jwt"}'
    )
    monkeypatch.setenv("HOME", str(tmp_path))


class TestParity:
    def test_agent_list_json_matches_golden(self, tmp_path, monkeypatch):
        _home_with_creds(tmp_path, monkeypatch)
        fixture = _load_fixture("agent_list")
        expected = _load_expected("agent_list")

        with patch("sota_sdk.cli_commands.agent.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.set_jwt = MagicMock()
            inst.list_agents = AsyncMock(return_value=fixture)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["agent", "list", "--json"])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == expected

    def test_bids_list_json_matches_golden(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fixture = _load_fixture("bids_list")
        expected = _load_expected("bids_list")

        with patch("sota_sdk.cli_commands.jobs_bids.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_bids = AsyncMock(return_value=fixture)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["bids", "list", "--json"])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == expected

    def test_keys_list_json_matches_golden(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fixture = _load_fixture("keys_list")
        expected = _load_expected("keys_list")

        with patch("sota_sdk.cli_commands.keys.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_keys = AsyncMock(return_value=fixture)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["keys", "list", "--json"])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == expected

    def test_logs_ndjson_matches_golden(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fixture = _load_fixture("activity_log")
        expected_lines = [
            json.loads(line) for line in
            (GOLDEN_DIR / "activity_log.expected.ndjson")
            .read_text().splitlines() if line.strip()
        ]

        with patch("sota_sdk.cli_commands.runtime.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.get_activity_log = AsyncMock(return_value=fixture)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(
                main, ["logs", "--json", "--no-follow"],
            )
            assert result.exit_code == 0, result.output
            lines = [json.loads(l) for l in result.output.splitlines() if l.strip()]
            assert lines == expected_lines
