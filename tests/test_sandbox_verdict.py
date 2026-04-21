"""Sandbox test-job failures must be visible to the handler.

Previously a failed validation ({passed: false, reason: "..."}) was stored
on ctx.last_validation and silently logged — developers had to hit the
portal to notice. Now deliver() raises AgentError on failure so the
handler sees it in stderr immediately.
"""
from unittest.mock import AsyncMock

import pytest

from sota_sdk import SOTAAgent, JobContext
from sota_sdk.errors import AgentError, ErrorCode
from sota_sdk.models import Job, TestJobContext


def _make_agent(monkeypatch) -> SOTAAgent:
    monkeypatch.setenv("SOTA_API_KEY", "sk_test")
    return SOTAAgent(api_key="sk_test", base_url="http://localhost:3001")


@pytest.mark.asyncio
async def test_test_job_deliver_raises_on_failure(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._client.deliver_test_job = AsyncMock(
        return_value={"passed": False, "reason": "expected 'ok', got 'maybe'"}
    )

    ctx = TestJobContext(
        job=Job(id="tj-1", description="...", budget_usdc=0.0),
        agent_id="agent-1",
        _client=agent._client,
        test_job_id="tj-1",
    )

    with pytest.raises(AgentError) as err:
        await ctx.deliver('{"status":"maybe"}')

    assert err.value.code == ErrorCode.INVALID_INPUT
    assert "expected 'ok'" in err.value.message
    assert ctx.last_validation == {
        "passed": False,
        "reason": "expected 'ok', got 'maybe'",
    }


@pytest.mark.asyncio
async def test_test_job_deliver_silent_on_pass(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._client.deliver_test_job = AsyncMock(
        return_value={"passed": True, "reason": "ok"}
    )

    ctx = TestJobContext(
        job=Job(id="tj-2", description="...", budget_usdc=0.0),
        agent_id="agent-1",
        _client=agent._client,
        test_job_id="tj-2",
    )

    # Must not raise
    await ctx.deliver('{"status":"ok"}')
    assert ctx._delivered is True
    assert ctx.last_validation == {"passed": True, "reason": "ok"}
