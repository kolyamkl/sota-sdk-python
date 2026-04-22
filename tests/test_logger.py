"""Tests for JobLogger (Tier 2 structured logs)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from sota_sdk.logger import JobLogger, _NoopJobLogger


@pytest.fixture
def job():
    from sota_sdk.models import Job
    return Job(
        id="j1", description="x", parameters={},
        budget_usdc=1.0, tags=["code-review"],
        status="executing", winner_agent_id="a1",
    )


@pytest.fixture
def client_mock():
    c = MagicMock()
    c.report_progress = AsyncMock(return_value={"status": "ok"})
    return c


class TestJobLoggerMethods:
    @pytest.mark.asyncio
    async def test_info_calls_report_progress_info(self, client_mock):
        log = JobLogger(job_id="j1", client=client_mock)
        await log.info("parsing request")
        client_mock.report_progress.assert_awaited_once()
        call = client_mock.report_progress.await_args
        assert call.kwargs["message"] == "parsing request"
        assert call.kwargs["level"] == "info"
        assert call.kwargs["percent"] == 0  # log-only has no percent
        assert call.kwargs["job_id"] == "j1"

    @pytest.mark.asyncio
    async def test_warn_sends_warn_level(self, client_mock):
        log = JobLogger(job_id="j1", client=client_mock)
        await log.warn("upstream slow")
        assert client_mock.report_progress.await_args.kwargs["level"] == "warn"

    @pytest.mark.asyncio
    async def test_error_sends_error_level(self, client_mock):
        log = JobLogger(job_id="j1", client=client_mock)
        await log.error("failed to connect")
        assert client_mock.report_progress.await_args.kwargs["level"] == "error"


class TestNoopJobLogger:
    @pytest.mark.asyncio
    async def test_info_is_noop(self):
        log = _NoopJobLogger()
        # Should not raise, should not require a client
        result = await log.info("anything")
        assert result is None

    @pytest.mark.asyncio
    async def test_all_levels_are_noop(self):
        log = _NoopJobLogger()
        assert await log.info("x") is None
        assert await log.warn("y") is None
        assert await log.error("z") is None


class TestJobContextLog:
    @pytest.mark.asyncio
    async def test_ctx_log_info_routes_through_client(self, job, client_mock):
        from sota_sdk.models import JobContext
        ctx = JobContext(job=job, agent_id="a1", _client=client_mock)
        await ctx.log.info("hello")
        client_mock.report_progress.assert_awaited_once()
        assert client_mock.report_progress.await_args.kwargs["level"] == "info"

    @pytest.mark.asyncio
    async def test_test_job_context_log_is_noop(self, job, client_mock):
        """Sandbox tests never ship progress — log must be a silent no-op."""
        from sota_sdk.models import TestJobContext
        ctx = TestJobContext(
            job=job, agent_id="a1", _client=client_mock, test_job_id="tj1",
        )
        await ctx.log.info("should not POST")
        client_mock.report_progress.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ctx_log_warn_and_error(self, job, client_mock):
        from sota_sdk.models import JobContext
        ctx = JobContext(job=job, agent_id="a1", _client=client_mock)
        await ctx.log.warn("careful")
        await ctx.log.error("boom")
        assert client_mock.report_progress.await_count == 2
        levels = [c.kwargs["level"]
                  for c in client_mock.report_progress.await_args_list]
        assert levels == ["warn", "error"]
