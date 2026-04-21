"""Unhandled handler exceptions default to retryable=True.

Transient failures (timeouts, socket errors, OOM after supervisor restart)
are far more common than permanent ones. Defaulting to retryable=False made
every network blip during execution a permanent job failure.
"""
from unittest.mock import AsyncMock

import pytest

from sota_sdk import SOTAAgent, JobContext
from sota_sdk.errors import AgentError, ErrorCode
from sota_sdk.models import Job


def _make_agent(monkeypatch) -> SOTAAgent:
    monkeypatch.setenv("SOTA_API_KEY", "sk_test")
    return SOTAAgent(api_key="sk_test", base_url="http://localhost:3001")


@pytest.mark.asyncio
async def test_unhandled_exception_defaults_retryable_true(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._agent_info = {"id": "agent-1", "capabilities": ["scrape"]}

    @agent.on_job("scrape")
    async def handle(ctx: JobContext):
        raise RuntimeError("connection reset by peer")

    agent._client.deliver_error = AsyncMock(return_value={"status": "failed"})

    job = Job(
        id="job-1",
        description="scrape example.com",
        budget_usdc=1.0,
        tags=["scrape"],
        status="executing",
        winner_agent_id="agent-1",
    )
    await agent._execute_job(job)

    agent._client.deliver_error.assert_called_once()
    kwargs = agent._client.deliver_error.call_args.kwargs
    assert kwargs["error_code"] == "internal_error"
    assert kwargs["retryable"] is True


@pytest.mark.asyncio
async def test_agent_error_respects_explicit_retryable_false(monkeypatch):
    """Developers opt out of retry by raising AgentError with retryable=False."""
    agent = _make_agent(monkeypatch)
    agent._agent_info = {"id": "agent-1", "capabilities": ["scrape"]}

    @agent.on_job("scrape")
    async def handle(ctx: JobContext):
        raise AgentError(
            code=ErrorCode.INVALID_INPUT,
            message="URL param missing",
            retryable=False,
        )

    agent._client.deliver_error = AsyncMock(return_value={"status": "failed"})

    job = Job(
        id="job-2",
        description="scrape",
        budget_usdc=1.0,
        tags=["scrape"],
        status="executing",
        winner_agent_id="agent-1",
    )
    await agent._execute_job(job)

    kwargs = agent._client.deliver_error.call_args.kwargs
    assert kwargs["error_code"] == "invalid_input"
    assert kwargs["retryable"] is False
