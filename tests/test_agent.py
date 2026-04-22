"""Tests for SOTA SDK client, models, errors, and webhook verification."""
import hashlib
import hmac
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sota_sdk.client import SOTAClient, verify_webhook_signature
from sota_sdk.errors import AgentError, ErrorCode
from sota_sdk.models import AutoBidConfig, Job, JobContext, WebhookEvent


# -- ErrorCode enum tests --


class TestErrorCode:
    def test_has_timeout(self):
        assert ErrorCode.TIMEOUT == "timeout"

    def test_has_resource_unavailable(self):
        assert ErrorCode.RESOURCE_UNAVAILABLE == "resource_unavailable"

    def test_has_authentication_failed(self):
        assert ErrorCode.AUTHENTICATION_FAILED == "authentication_failed"

    def test_has_invalid_input(self):
        assert ErrorCode.INVALID_INPUT == "invalid_input"

    def test_has_internal_error(self):
        assert ErrorCode.INTERNAL_ERROR == "internal_error"

    def test_has_rate_limited(self):
        assert ErrorCode.RATE_LIMITED == "rate_limited"

    def test_all_six_codes(self):
        assert len(ErrorCode) == 6


# -- AgentError tests --


class TestAgentError:
    def test_basic_raise(self):
        err = AgentError(code=ErrorCode.TIMEOUT, message="Timed out")
        assert err.code == ErrorCode.TIMEOUT
        assert err.message == "Timed out"
        assert err.partial_result is None
        assert err.retryable is False
        assert err.debug_info == {}

    def test_with_all_fields(self):
        err = AgentError(
            code=ErrorCode.RESOURCE_UNAVAILABLE,
            message="URL not reachable",
            partial_result="partial data",
            retryable=True,
            debug_info={"url": "https://example.com"},
        )
        assert err.partial_result == "partial data"
        assert err.retryable is True
        assert err.debug_info == {"url": "https://example.com"}

    def test_is_exception(self):
        err = AgentError(code=ErrorCode.INTERNAL_ERROR, message="oops")
        assert isinstance(err, Exception)
        assert str(err) == "oops"


# -- Job model tests --


class TestJobModel:
    def test_basic_job(self):
        job = Job(
            id="job-1",
            description="Test job",
            parameters={"key": "value"},
            budget_usdc=5.0,
            tags=["echo"],
            status="bidding",
        )
        assert job.id == "job-1"
        assert job.description == "Test job"
        assert job.parameters == {"key": "value"}
        assert job.budget_usdc == 5.0
        assert job.tags == ["echo"]
        assert job.status == "bidding"

    def test_optional_fields(self):
        job = Job(id="job-2", description="Minimal", budget_usdc=3.0)
        assert job.bid_window_seconds is None
        assert job.winner_agent_id is None
        assert job.tags == []
        assert job.status == "open"


# -- WebhookEvent model test --


class TestWebhookEvent:
    def test_construction(self):
        evt = WebhookEvent(
            id="evt-1",
            event_type="job_assigned",
            payload={"job_id": "j1"},
            status="pending",
            created_at="2026-01-01T00:00:00Z",
        )
        assert evt.event_type == "job_assigned"
        assert evt.payload["job_id"] == "j1"


# -- AutoBidConfig test --


class TestAutoBidConfig:
    def test_construction(self):
        cfg = AutoBidConfig(max_price=10.0, capabilities=["echo", "scrape"])
        assert cfg.max_price == 10.0
        assert cfg.capabilities == ["echo", "scrape"]


# -- Webhook signature verification tests --


class TestWebhookSignature:
    def test_valid_signature(self):
        secret = "test-webhook-secret"
        payload = b'{"event":"job_assigned"}'
        ts = str(int(time.time()))
        signed_content = f"{ts}.".encode() + payload
        digest = hmac.new(
            secret.encode(), signed_content, hashlib.sha256
        ).hexdigest()
        signature = f"t={ts},v1={digest}"
        assert verify_webhook_signature(payload, signature, secret) is True

    def test_wrong_secret(self):
        secret = "correct-secret"
        payload = b'{"event":"job_assigned"}'
        ts = str(int(time.time()))
        signed_content = f"{ts}.".encode() + payload
        digest = hmac.new(
            "wrong-secret".encode(), signed_content, hashlib.sha256
        ).hexdigest()
        signature = f"t={ts},v1={digest}"
        assert verify_webhook_signature(payload, signature, secret) is False

    def test_invalid_format(self):
        assert verify_webhook_signature(b"data", "invalid", "secret") is False


# -- SOTAClient tests --


class TestSOTAClient:
    @pytest.fixture
    def mock_response(self):
        """Create a mock httpx response (json() is sync in httpx)."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        return resp

    @pytest.mark.asyncio
    async def test_get_profile(self, api_key, base_url, mock_response):
        mock_response.json.return_value = {"id": "agent-1", "name": "Test"}
        client = SOTAClient(api_key, base_url)
        with patch.object(client._http, "get", return_value=mock_response) as mock_get:
            result = await client.get_profile()
            mock_get.assert_called_once_with("/api/v1/agents/me")
            assert result == {"id": "agent-1", "name": "Test"}
        await client.close()

    @pytest.mark.asyncio
    async def test_submit_bid(self, api_key, base_url, mock_response):
        mock_response.json.return_value = {"status": "bid_accepted"}
        client = SOTAClient(api_key, base_url)
        with patch.object(
            client._http, "request", return_value=mock_response
        ) as mock_req:
            result = await client.submit_bid("job-1", 5.0, 120)
            mock_req.assert_called_once_with(
                "POST",
                "/api/v1/agents/bid",
                json={
                    "job_id": "job-1",
                    "amount_usdc": 5.0,
                    "estimated_seconds": 120,
                },
                params=None,
            )
            assert result["status"] == "bid_accepted"
        await client.close()

    @pytest.mark.asyncio
    async def test_deliver(self, api_key, base_url, mock_response):
        mock_response.json.return_value = {"status": "delivered"}
        client = SOTAClient(api_key, base_url)
        with patch.object(
            client._http, "request", return_value=mock_response
        ) as mock_req:
            result = await client.deliver("job-1", "result text")
            mock_req.assert_called_once_with(
                "POST",
                "/api/v1/agents/deliver",
                json={"job_id": "job-1", "result": "result text"},
                params=None,
            )
            assert result["status"] == "delivered"
        await client.close()

    @pytest.mark.asyncio
    async def test_exchange_token(self, api_key, base_url, mock_response):
        mock_response.json.return_value = {"token": "jwt-token", "expires_in": 900}
        client = SOTAClient(api_key, base_url)
        with patch.object(
            client._http, "post", return_value=mock_response
        ) as mock_post:
            result = await client.exchange_token()
            mock_post.assert_called_once_with("/api/v1/agents/token")
            assert result["token"] == "jwt-token"
        await client.close()

    @pytest.mark.asyncio
    async def test_heartbeat(self, api_key, base_url, mock_response):
        mock_response.json.return_value = {"status": "ok"}
        client = SOTAClient(api_key, base_url)
        with patch.object(
            client._http, "post", return_value=mock_response
        ) as mock_post:
            result = await client.heartbeat()
            mock_post.assert_called_once_with("/api/v1/agents/heartbeat")
            assert result["status"] == "ok"
        await client.close()

    @pytest.mark.asyncio
    async def test_report_progress(self, api_key, base_url, mock_response):
        mock_response.json.return_value = {"status": "ok"}
        client = SOTAClient(api_key, base_url)
        with patch.object(
            client._http, "request", return_value=mock_response
        ) as mock_req:
            result = await client.report_progress("job-1", 50, "halfway")
            mock_req.assert_called_once_with(
                "POST",
                "/api/v1/agents/progress",
                json={
                    "job_id": "job-1",
                    "percent": 50,
                    "level": "info",
                    "message": "halfway",
                },
                params=None,
            )
            assert result["status"] == "ok"
        await client.close()

    @pytest.mark.asyncio
    async def test_get_events(self, api_key, base_url, mock_response):
        mock_response.json.return_value = [
            {"id": "e1", "event_type": "job_assigned"}
        ]
        client = SOTAClient(api_key, base_url)
        with patch.object(
            client._http, "get", return_value=mock_response
        ) as mock_get:
            result = await client.get_events(since="2026-01-01T00:00:00Z")
            mock_get.assert_called_once_with(
                "/api/v1/agents/events",
                params={"since": "2026-01-01T00:00:00Z"},
            )
            assert len(result) == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_headers_include_api_key(self, api_key, base_url):
        client = SOTAClient(api_key, base_url)
        assert client._http.headers["X-API-Key"] == api_key
        await client.close()


# -- JobContext tests --


class TestJobContext:
    @pytest.mark.asyncio
    async def test_update_progress(self):
        mock_client = AsyncMock()
        job = Job(id="job-1", description="Test", budget_usdc=5.0)
        ctx = JobContext(job=job, agent_id="agent-1", _client=mock_client)
        await ctx.update_progress(50, "halfway")
        mock_client.report_progress.assert_called_once_with("job-1", 50, "halfway")

    @pytest.mark.asyncio
    async def test_deliver(self):
        mock_client = AsyncMock()
        job = Job(id="job-1", description="Test", budget_usdc=5.0)
        ctx = JobContext(job=job, agent_id="agent-1", _client=mock_client)
        await ctx.deliver("result text")
        mock_client.deliver.assert_called_once_with("job-1", "result text", None)
        assert ctx._delivered is True

    @pytest.mark.asyncio
    async def test_fail(self):
        mock_client = AsyncMock()
        job = Job(id="job-1", description="Test", budget_usdc=5.0)
        ctx = JobContext(job=job, agent_id="agent-1", _client=mock_client)
        await ctx.fail("timeout", "Took too long", partial_result="partial")
        mock_client.deliver_error.assert_called_once_with(
            job_id="job-1",
            error_code="timeout",
            error_message="Took too long",
            partial_result="partial",
            retryable=False,
        )
        assert ctx._delivered is True
