"""Tests for jobs + bids CLI command groups."""

import json
import pytest
from unittest.mock import AsyncMock, patch
from click.testing import CliRunner


def _env_with_key(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SOTA_API_KEY=sk_test\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SOTA_API_KEY", raising=False)


class TestJobsList:
    def test_jobs_list_json(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = [
            {"id": "j-uuid-11111111", "status": "open",
             "budget_usdc": 5.0, "description": "scrape X"},
            {"id": "j-uuid-22222222", "status": "assigned",
             "budget_usdc": 10.0, "description": "scrape Y"},
        ]
        with patch("sota_sdk.cli_commands.jobs_bids.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_jobs = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["jobs", "list", "--json"])
            assert result.exit_code == 0, result.output
            parsed = json.loads(result.output)
            assert parsed == {"jobs": fake}

    def test_jobs_list_limit_truncates(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = [
            {"id": f"j-{i}", "status": "open", "budget_usdc": 1.0,
             "description": f"job {i}"}
            for i in range(5)
        ]
        with patch("sota_sdk.cli_commands.jobs_bids.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_jobs = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(
                main, ["jobs", "list", "--limit", "2", "--json"],
            )
            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert len(parsed["jobs"]) == 2

    def test_jobs_list_empty_prints_friendly(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.jobs_bids.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_jobs = AsyncMock(return_value=[])
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["jobs", "list"])
            assert result.exit_code == 0
            assert "No jobs" in result.output


class TestJobShow:
    def test_job_show_found(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = [
            {"id": "j-aaaa-1111", "status": "open",
             "budget_usdc": 5.0, "description": "scrape X"},
        ]
        with patch("sota_sdk.cli_commands.jobs_bids.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_jobs = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["job", "j-aaaa-1111", "--json"])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == fake[0]

    def test_job_show_not_found_exits_4(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        with patch("sota_sdk.cli_commands.jobs_bids.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_jobs = AsyncMock(return_value=[])
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["job", "no-such"])
            assert result.exit_code == 4


class TestBidsList:
    def test_bids_list_status_passes_through(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = {"bids": [
            {"id": "b-uuid-11111111", "job_id": "j-uuid-22222222",
             "amount_usdc": 2.0, "status": "won",
             "created_at": "2026-04-20T00:00:00Z"},
        ]}
        captured = {}

        async def fake_list(**kwargs):
            captured.update(kwargs)
            return fake

        with patch("sota_sdk.cli_commands.jobs_bids.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_bids = fake_list
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(
                main, ["bids", "list", "--status", "won", "--json"],
            )
            assert result.exit_code == 0, result.output
            assert captured.get("status") == "won"

    def test_bids_list_no_filters(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        fake = {"bids": []}
        with patch("sota_sdk.cli_commands.jobs_bids.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.list_bids = AsyncMock(return_value=fake)
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, ["bids", "list"])
            assert result.exit_code == 0
            assert "No bids" in result.output


class TestBidSubmit:
    def test_bid_submit_passes_args(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        captured = {}

        async def fake_submit(**kwargs):
            captured.update(kwargs)
            return {"id": "b-new-uuid"}

        with patch("sota_sdk.cli_commands.jobs_bids.SOTAClient") as Mock:
            inst = Mock.return_value
            inst.submit_bid = fake_submit
            inst.close = AsyncMock()

            from sota_sdk.cli import main
            runner = CliRunner()
            result = runner.invoke(main, [
                "bid", "submit", "j-uuid-1234",
                "--amount", "2.5", "--eta", "300",
            ])
            assert result.exit_code == 0, result.output
            assert captured["job_id"] == "j-uuid-1234"
            assert captured["amount_usdc"] == 2.5
            assert captured["estimated_seconds"] == 300
            assert "Submitted bid" in result.output


class TestBidCancel:
    def test_bid_cancel_stub(self, tmp_path, monkeypatch):
        _env_with_key(tmp_path, monkeypatch)
        from sota_sdk.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["bid", "cancel", "b-uuid", "--yes"])
        assert result.exit_code == 1
        assert "not yet available" in result.output.lower()
