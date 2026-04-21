"""Sandbox-mode behaviour for the SOTA Python SDK agent.

Covers the pieces added in step 6: status branching in `run()`,
polling loop, handler-matching (capability + `_default` fallback),
routing to /test-jobs/{id}/deliver, and the exit condition when the
backend stops reporting sandbox mode.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from sota_sdk import SOTAAgent, JobContext


# ---------------------------------------------------------------------------
# Handler matching
# ---------------------------------------------------------------------------


def _make_agent(monkeypatch) -> SOTAAgent:
    monkeypatch.setenv("SOTA_API_KEY", "sk_test")
    return SOTAAgent(api_key="sk_test", base_url="http://localhost:3001")


@pytest.mark.asyncio
async def test_execute_test_job_uses_capability_handler(monkeypatch):
    """If a registered handler matches the test job's capability,
    the SDK calls that handler and delivers via deliver_test_job."""
    agent = _make_agent(monkeypatch)
    agent._agent_info = {"id": "agent-1", "status": "sandbox"}

    async def ws(ctx: JobContext) -> str:
        return json.dumps({"title": "Example", "meta_description": "..."})

    agent.on_job("web-scraping")(ws)

    agent._client.deliver_test_job = AsyncMock(return_value={"passed": True})

    await agent._execute_test_job({
        "id": "tj-1",
        "capability": "web-scraping",
        "description": "Scrape example.com",
        "parameters": {"url": "https://example.com"},
        "status": "pending",
    })

    agent._client.deliver_test_job.assert_called_once()
    call_args = agent._client.deliver_test_job.call_args
    assert call_args.args[0] == "tj-1"
    payload = json.loads(call_args.args[1])
    assert payload["title"] == "Example"


@pytest.mark.asyncio
async def test_execute_test_job_falls_back_to_default(monkeypatch):
    """When the test job's capability has no specific handler,
    the SDK falls back to the `_default` handler."""
    agent = _make_agent(monkeypatch)
    agent._agent_info = {"id": "agent-1", "status": "sandbox"}

    called_with: dict = {}

    async def default_handler(ctx: JobContext) -> str:
        called_with["desc"] = ctx.job.description
        return json.dumps({"status": "ok", "message": "hi"})

    agent.on_job("_default")(default_handler)

    agent._client.deliver_test_job = AsyncMock(return_value={"passed": True})

    await agent._execute_test_job({
        "id": "tj-d",
        "capability": "some-unknown-cap",
        "description": "Return a JSON object with a 'status' field set to 'ok'",
        "parameters": {},
        "status": "pending",
    })

    assert called_with["desc"] != ""
    agent._client.deliver_test_job.assert_called_once()


@pytest.mark.asyncio
async def test_execute_test_job_skips_when_no_handler(monkeypatch, caplog):
    """No handler and no `_default` → skip + log warning, no deliver."""
    import logging
    caplog.set_level(logging.WARNING, logger="sota_sdk.agent")

    agent = _make_agent(monkeypatch)
    agent._agent_info = {"id": "agent-1", "status": "sandbox"}
    agent._client.deliver_test_job = AsyncMock()

    await agent._execute_test_job({
        "id": "tj-2",
        "capability": "unsupported",
        "description": "...",
        "parameters": {},
        "status": "pending",
    })

    agent._client.deliver_test_job.assert_not_called()
    assert any("No handler registered" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Sandbox loop exit conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sandbox_loop_exits_when_backend_flips_status(monkeypatch):
    """The loop must exit cleanly when the backend response no longer
    carries `sandbox: True` — this is how the SDK learns the agent
    moved to `testing_passed` / `active`."""
    agent = _make_agent(monkeypatch)
    agent._agent_info = {"id": "agent-1", "status": "sandbox"}
    agent._running = True

    # First call: sandbox=True with one test job. Second call: sandbox=False.
    responses = [
        {"sandbox": True, "jobs": [{
            "id": "tj-9",
            "capability": "_default",
            "description": "Return 'status' 'ok'",
            "parameters": {},
            "status": "pending",
        }]},
        {"sandbox": False, "jobs": []},
    ]
    agent._client.list_available_jobs = AsyncMock(side_effect=responses)
    agent._client.deliver_test_job = AsyncMock(return_value={"passed": True})

    async def default_handler(ctx: JobContext) -> str:
        return json.dumps({"status": "ok", "message": "..."})
    agent.on_job("_default")(default_handler)

    # Speed up the poll so the test doesn't wait 5 real seconds
    import sota_sdk.agent as agent_mod
    monkeypatch.setattr(agent_mod, "SANDBOX_POLL_INTERVAL", 0.01)

    await asyncio.wait_for(agent._run_sandbox_loop(), timeout=2.0)

    # The handler ran for the one pending job
    agent._client.deliver_test_job.assert_called_once()
    # Both poll calls happened (sandbox True, then sandbox False → exit)
    assert agent._client.list_available_jobs.call_count == 2


@pytest.mark.asyncio
async def test_sandbox_loop_dedupes_seen_jobs(monkeypatch):
    """If the same test job appears in consecutive polls (pre-delivery),
    the handler must run exactly once."""
    agent = _make_agent(monkeypatch)
    agent._agent_info = {"id": "agent-1", "status": "sandbox"}
    agent._running = True

    test_job = {
        "id": "tj-dup",
        "capability": "_default",
        "description": "Return status ok",
        "parameters": {},
        "status": "pending",
    }
    responses = [
        {"sandbox": True, "jobs": [test_job]},
        {"sandbox": True, "jobs": [test_job]},  # same job again
        {"sandbox": False, "jobs": []},
    ]
    agent._client.list_available_jobs = AsyncMock(side_effect=responses)
    agent._client.deliver_test_job = AsyncMock(return_value={"passed": True})

    call_count = {"n": 0}

    async def default_handler(ctx: JobContext) -> str:
        call_count["n"] += 1
        return json.dumps({"status": "ok", "message": "..."})
    agent.on_job("_default")(default_handler)

    import sota_sdk.agent as agent_mod
    monkeypatch.setattr(agent_mod, "SANDBOX_POLL_INTERVAL", 0.01)

    await asyncio.wait_for(agent._run_sandbox_loop(), timeout=2.0)

    assert call_count["n"] == 1, "handler must run exactly once per test job"
    assert agent._client.deliver_test_job.call_count == 1


# ---------------------------------------------------------------------------
# Client wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_deliver_test_job_hits_correct_path():
    """SOTAClient.deliver_test_job must POST to
    /api/v1/agents/test-jobs/{id}/deliver with {result}."""
    from sota_sdk.client import SOTAClient

    client = SOTAClient("sk_test", "http://localhost:3001")

    captured: dict = {}

    async def fake_request(method, path, json=None, params=None, retries=3):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = json

        class R:
            is_success = True
            status_code = 200
            text = "{}"

            def json(self):
                return {"passed": True, "reason": "ok"}

        return R()

    client._request_with_retry = fake_request  # type: ignore[attr-defined]

    result = await client.deliver_test_job("tj-42", '{"foo":"bar"}')

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/agents/test-jobs/tj-42/deliver"
    assert captured["json"] == {"result": '{"foo":"bar"}'}
    assert result == {"passed": True, "reason": "ok"}
    await client.close()
