"""Tests for SOTAClient HTTP methods."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from sota_sdk.client import SOTAClient


@pytest.fixture
def client():
    c = SOTAClient(api_key="test-key", base_url="http://localhost:3001")
    return c


class TestReportProgressLevel:
    @pytest.mark.asyncio
    async def test_default_level_is_info(self, client):
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m_req:
            m_req.return_value = MagicMock(status_code=200,
                                           json=lambda: {"status": "ok"})
            await client.report_progress(
                job_id="j1", percent=50, message="halfway",
            )
            call = m_req.await_args
            body = call.kwargs.get("json")
            assert body is not None, "report_progress should send a JSON body"
            assert body["level"] == "info"

    @pytest.mark.asyncio
    async def test_warn_level_is_sent(self, client):
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m_req:
            m_req.return_value = MagicMock(status_code=200,
                                           json=lambda: {"status": "ok"})
            await client.report_progress(
                job_id="j1", percent=60, message="slow", level="warn",
            )
            body = m_req.await_args.kwargs.get("json")
            assert body["level"] == "warn"

    @pytest.mark.asyncio
    async def test_error_level_is_sent(self, client):
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m_req:
            m_req.return_value = MagicMock(status_code=200,
                                           json=lambda: {"status": "ok"})
            await client.report_progress(
                job_id="j1", percent=80, message="failed", level="error",
            )
            body = m_req.await_args.kwargs.get("json")
            assert body["level"] == "error"

    @pytest.mark.asyncio
    async def test_rejects_bad_level(self, client):
        with pytest.raises(ValueError, match="level"):
            await client.report_progress(
                job_id="j1", percent=1, message="x", level="badlevel",
            )

    @pytest.mark.asyncio
    async def test_passes_message_through(self, client):
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m_req:
            m_req.return_value = MagicMock(status_code=200,
                                           json=lambda: {"status": "ok"})
            await client.report_progress(
                job_id="j1", percent=50, message="parsing request",
            )
            body = m_req.await_args.kwargs.get("json")
            assert body["job_id"] == "j1"
            assert body["percent"] == 50
            assert body["message"] == "parsing request"


class TestClientNewMethods:
    @pytest.mark.asyncio
    async def test_list_agents_sends_bearer(self, client):
        client.set_jwt("fake-jwt")
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m:
            m.return_value = MagicMock(
                status_code=200,
                json=lambda: {"agents": [], "total": 0},
            )
            await client.list_agents(status="active")
            call = m.await_args
            # Path should hit /api/v1/agents
            assert "/api/v1/agents" in call.args[1]
            # Bearer header carried
            assert call.kwargs["headers"]["Authorization"] == "Bearer fake-jwt"
            # Query param sent
            assert call.kwargs["params"]["status"] == "active"
            # include_deleted defaults to False
            assert call.kwargs["params"]["include_deleted"] is False

    @pytest.mark.asyncio
    async def test_list_agents_requires_jwt(self, client):
        # Fresh client with no JWT
        from sota_sdk.client import SOTAClient, APIError
        c = SOTAClient(api_key="k", base_url="http://x")
        with pytest.raises(APIError) as exc:
            await c.list_agents()
        assert exc.value.status == 401

    @pytest.mark.asyncio
    async def test_delete_agent_sends_delete_method(self, client):
        client.set_jwt("fake-jwt")
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m:
            m.return_value = MagicMock(
                status_code=200,
                json=lambda: {"deleted": True, "agent_id": "a1"},
            )
            result = await client.delete_agent("a1")
            assert m.await_args.args[0] == "DELETE"
            assert "/api/v1/agents/a1" in m.await_args.args[1]
            assert result["deleted"] is True

    @pytest.mark.asyncio
    async def test_list_bids_with_status(self, client):
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m:
            m.return_value = MagicMock(
                status_code=200,
                json=lambda: {"bids": [], "total": 0},
            )
            await client.list_bids(status="won", since="2026-04-20T00:00:00Z")
            assert "/api/v1/agents/bids" in m.await_args.args[1]
            assert m.await_args.kwargs["params"]["status"] == "won"
            assert m.await_args.kwargs["params"]["since"] == "2026-04-20T00:00:00Z"

    @pytest.mark.asyncio
    async def test_list_bids_no_filter(self, client):
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m:
            m.return_value = MagicMock(
                status_code=200,
                json=lambda: {"bids": [], "total": 0},
            )
            await client.list_bids()
            # No filter params when none provided
            params = m.await_args.kwargs.get("params", {})
            assert "status" not in params
            assert "since" not in params

    @pytest.mark.asyncio
    async def test_list_keys(self, client):
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m:
            m.return_value = MagicMock(
                status_code=200, json=lambda: {"keys": []},
            )
            await client.list_keys(include_revoked=True)
            assert "/api/v1/agents/keys" in m.await_args.args[1]
            assert m.await_args.kwargs["params"]["include_revoked"] is True

    @pytest.mark.asyncio
    async def test_revoke_key(self, client):
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m:
            m.return_value = MagicMock(
                status_code=200,
                json=lambda: {"revoked": True, "key_id": "k1",
                              "already_revoked": False},
            )
            result = await client.revoke_key("k1")
            assert m.await_args.args[0] == "POST"
            assert "/api/v1/agents/keys/k1/revoke" in m.await_args.args[1]
            assert result["revoked"] is True

    @pytest.mark.asyncio
    async def test_get_activity_log_passes_cursor(self, client):
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m:
            m.return_value = MagicMock(
                status_code=200,
                json=lambda: {"entries": [], "next_since_id": None},
            )
            await client.get_activity_log(since_id=42, limit=50)
            params = m.await_args.kwargs["params"]
            assert params["since_id"] == 42
            assert params["limit"] == 50

    @pytest.mark.asyncio
    async def test_get_activity_log_all_filters(self, client):
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m:
            m.return_value = MagicMock(
                status_code=200,
                json=lambda: {"entries": [], "next_since_id": None},
            )
            await client.get_activity_log(
                since_id=10, since_ts="2026-04-22T00:00:00Z",
                job_id="j1", limit=100,
            )
            params = m.await_args.kwargs["params"]
            assert params["since_id"] == 10
            assert params["since_ts"] == "2026-04-22T00:00:00Z"
            assert params["job_id"] == "j1"
            assert params["limit"] == 100

    @pytest.mark.asyncio
    async def test_register_agent_authenticated_uses_jwt(self, client):
        """Closes project_cli_register_auth_todo: the standalone register
        command should go through JWT-auth /register, not /register/simple."""
        client.set_jwt("fake-jwt")
        with patch.object(client, "_request_with_retry",
                          new_callable=AsyncMock) as m:
            m.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "agent_id": "a1", "api_key": "sk_new",
                    "webhook_secret": "ws",
                },
            )
            await client.register_agent_authenticated(
                name="my-agent",
                capabilities=["code-review"],
                wallet_address="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
                description="hello",
            )
            call = m.await_args
            # Hits the JWT-auth register endpoint, NOT register/simple
            assert "/api/v1/agents/register" in call.args[1]
            assert "/register/simple" not in call.args[1]
            assert call.kwargs["headers"]["Authorization"] == "Bearer fake-jwt"
            body = call.kwargs["json"]
            assert body["name"] == "my-agent"
            # Body does NOT include password (that's the /register/simple path)
            assert "password" not in body
            assert "email" not in body
