"""401 from the backend must stop the agent, not spin forever.

When an API key is rotated or revoked mid-run, the SDK used to log a
warning every 25s and keep going — the agent appeared alive but was
actually dead. The heartbeat loop now signals a fatal stop on 401.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from sota_sdk import SOTAAgent
from sota_sdk.client import APIError


def _make_agent(monkeypatch) -> SOTAAgent:
    monkeypatch.setenv("SOTA_API_KEY", "sk_test")
    return SOTAAgent(api_key="sk_test", base_url="http://localhost:3001")


@pytest.mark.asyncio
async def test_heartbeat_401_stops_agent(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._running = True
    agent._stop_event = asyncio.Event()
    agent._client.heartbeat = AsyncMock(
        side_effect=APIError(401, "API key revoked")
    )

    import sota_sdk.agent as agent_mod
    monkeypatch.setattr(agent_mod, "HEARTBEAT_INTERVAL", 0.01)

    await asyncio.wait_for(agent._heartbeat_loop(), timeout=1.0)

    assert agent._running is False
    assert agent._stop_event.is_set()
    assert isinstance(agent._fatal_error, APIError)
    assert agent._fatal_error.status == 401


@pytest.mark.asyncio
async def test_heartbeat_5xx_keeps_running(monkeypatch):
    """Non-auth errors should not stop the agent — they're probably transient."""
    agent = _make_agent(monkeypatch)
    agent._running = True
    agent._stop_event = asyncio.Event()

    calls = {"n": 0}

    async def fake_hb():
        calls["n"] += 1
        if calls["n"] >= 3:
            agent._running = False
            return {"status": "ok"}
        raise APIError(503, "upstream down")

    agent._client.heartbeat = fake_hb

    import sota_sdk.agent as agent_mod
    monkeypatch.setattr(agent_mod, "HEARTBEAT_INTERVAL", 0.01)

    await asyncio.wait_for(agent._heartbeat_loop(), timeout=1.0)

    assert calls["n"] >= 3
    assert agent._fatal_error is None
    assert agent._stop_event.is_set() is False
