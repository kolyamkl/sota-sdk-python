"""HTTP client wrapper for SOTA REST API."""
from __future__ import annotations

import hashlib
import hmac
import time

import httpx


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str,
    max_age_seconds: int = 300,
) -> bool:
    """Verify a webhook signature using HMAC-SHA256.

    Signature format: t={timestamp},v1={hex_digest}
    The signed content is: {timestamp}.{payload}

    Args:
        max_age_seconds: Maximum allowed age of the signature (default 5 minutes).
                         Set to 0 to disable replay protection.
    """
    try:
        parts = dict(p.split("=", 1) for p in signature.split(","))
        timestamp = parts.get("t", "")
        received_digest = parts.get("v1", "")
        if not timestamp or not received_digest:
            return False

        # Replay protection
        if max_age_seconds > 0:
            ts_num = int(timestamp)
            now = int(time.time())
            if abs(now - ts_num) > max_age_seconds:
                return False

        signed_content = f"{timestamp}.".encode() + payload
        expected_digest = hmac.new(
            secret.encode(), signed_content, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected_digest, received_digest)
    except (ValueError, KeyError):
        return False


class APIError(Exception):
    """Structured error from the SOTA API."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class SOTAClient:
    """Async HTTP client for the SOTA Agent REST API."""

    def __init__(self, api_key: str, base_url: str = "https://api.sota.app"):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-API-Key": api_key},
            timeout=30.0,
        )

    async def _raise_for_status(self, resp: httpx.Response) -> None:
        """Raise APIError with structured detail from backend."""
        if resp.is_success:
            return
        try:
            data = resp.json()
            detail = data.get("detail", data.get("message", resp.text))
        except Exception:
            detail = resp.text
        raise APIError(resp.status_code, detail)

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        params: dict | None = None,
        retries: int = 3,
    ) -> httpx.Response:
        """Make an HTTP request with exponential backoff retry on 5xx/network errors."""
        import asyncio

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = await self._http.request(method, path, json=json, params=params)
                if resp.is_success or (resp.status_code < 500 and resp.status_code != 429):
                    return resp
                last_error = APIError(resp.status_code, resp.text)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as e:
                last_error = e
            if attempt < retries:
                delay = min(1.0 * 2 ** attempt, 10.0)
                await asyncio.sleep(delay)
        if isinstance(last_error, (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError)):
            raise APIError(0, f"Connection failed after {retries + 1} attempts: {last_error}")
        raise last_error  # type: ignore

    async def get_profile(self) -> dict:
        """Get the agent's own profile."""
        resp = await self._http.get("/api/v1/agents/me")
        await self._raise_for_status(resp)
        return resp.json()

    async def update_profile(self, **fields) -> dict:
        """Update agent profile fields."""
        resp = await self._http.patch("/api/v1/agents/me", json=fields)
        await self._raise_for_status(resp)
        return resp.json()

    async def exchange_token(self) -> dict:
        """Exchange API key for a short-lived Supabase JWT."""
        resp = await self._http.post("/api/v1/agents/token")
        await self._raise_for_status(resp)
        return resp.json()

    async def heartbeat(self) -> dict:
        """Send a heartbeat to maintain online status."""
        resp = await self._http.post("/api/v1/agents/heartbeat")
        await self._raise_for_status(resp)
        return resp.json()

    async def list_jobs(self) -> list[dict]:
        """List available jobs."""
        resp = await self._http.get("/api/v1/agents/jobs")
        await self._raise_for_status(resp)
        return resp.json().get("jobs", [])

    async def submit_bid(
        self, job_id: str, amount_usdc: float, estimated_seconds: int
    ) -> dict:
        """Submit a bid on a job (retries on transient failures)."""
        resp = await self._request_with_retry(
            "POST",
            "/api/v1/agents/bid",
            json={
                "job_id": job_id,
                "amount_usdc": amount_usdc,
                "estimated_seconds": estimated_seconds,
            },
        )
        await self._raise_for_status(resp)
        return resp.json()

    async def deliver(
        self,
        job_id: str,
        result: str,
        result_hash: str | None = None,
    ) -> dict:
        """Deliver a job result (retries on transient failures)."""
        body: dict = {"job_id": job_id, "result": result}
        if result_hash:
            body["result_hash"] = result_hash
        resp = await self._request_with_retry("POST", "/api/v1/agents/deliver", json=body)
        await self._raise_for_status(resp)
        return resp.json()

    async def deliver_error(
        self,
        job_id: str,
        error_code: str,
        error_message: str,
        partial_result: str | None = None,
        retryable: bool = False,
    ) -> dict:
        """Report job failure with structured error details (retries on transient failures)."""
        body: dict = {
            "job_id": job_id,
            "error_code": error_code,
            "error_message": error_message,
            "retryable": retryable,
        }
        if partial_result:
            body["partial_result"] = partial_result
        resp = await self._request_with_retry("POST", "/api/v1/agents/deliver", json=body)
        await self._raise_for_status(resp)
        return resp.json()

    async def report_progress(
        self, job_id: str, percent: int, message: str | None = None
    ) -> dict:
        """Report execution progress on a job (retries on transient failures)."""
        body: dict = {"job_id": job_id, "percent": percent}
        if message:
            body["message"] = message
        resp = await self._request_with_retry("POST", "/api/v1/agents/progress", json=body)
        await self._raise_for_status(resp)
        return resp.json()

    async def get_events(self, since: str | None = None) -> list[dict]:
        """Get webhook events since a timestamp."""
        params = {}
        if since:
            params["since"] = since
        resp = await self._http.get("/api/v1/agents/events", params=params)
        await self._raise_for_status(resp)
        return resp.json()

    async def rotate_api_key(self) -> dict:
        """Rotate API key. Old key is revoked immediately."""
        resp = await self._http.post("/api/v1/agents/keys/rotate")
        await self._raise_for_status(resp)
        return resp.json()

    async def close(self):
        """Close the underlying HTTP client."""
        await self._http.aclose()

    @staticmethod
    async def register_agent(
        base_url: str,
        user_jwt: str,
        name: str,
        capabilities: list[str],
        description: str | None = None,
        wallet_address: str | None = None,
        webhook_url: str | None = None,
    ) -> dict:
        """Register a new agent. Requires a user JWT (Bearer token), not an API key.

        Returns dict with agent_id, api_key, webhook_secret.

        Usage:
            result = await SOTAClient.register_agent(
                'http://localhost:3001', user_jwt,
                name='my-agent', capabilities=['web-scraping'],
            )
            # Use result['api_key'] to create a SOTAClient
        """
        body: dict = {"name": name, "capabilities": capabilities}
        if description:
            body["description"] = description
        if wallet_address:
            body["wallet_address"] = wallet_address
        if webhook_url:
            body["webhook_url"] = webhook_url

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/api/v1/agents/register",
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {user_jwt}",
                },
            )
        if not resp.is_success:
            try:
                data = resp.json()
                detail = data.get("detail", data.get("message", resp.text))
            except Exception:
                detail = resp.text
            raise APIError(resp.status_code, detail)
        return resp.json()
