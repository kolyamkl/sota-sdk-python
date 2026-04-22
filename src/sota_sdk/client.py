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
        self._jwt: str | None = None
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-API-Key": api_key},
            timeout=30.0,
        )

    @property
    def base_url(self) -> str:
        """Base URL for the API (read-only)."""
        return self._base_url

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
        headers: dict | None = None,
        retries: int = 3,
    ) -> httpx.Response:
        """Make an HTTP request with exponential backoff retry on 5xx/network errors."""
        import asyncio

        # Preserve pre-existing call signature (always pass json=, params=)
        # and only add the optional headers kwarg when provided, to avoid
        # breaking callers that pattern-match on httpx.AsyncClient.request.
        req_kwargs: dict = {"json": json, "params": params}
        if headers is not None:
            req_kwargs["headers"] = headers

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = await self._http.request(method, path, **req_kwargs)
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
        """List available jobs. Returns only the `jobs` array; use
        `list_available_jobs()` if you need the sandbox flag."""
        resp = await self._http.get("/api/v1/agents/jobs")
        await self._raise_for_status(resp)
        return resp.json().get("jobs", [])

    async def list_available_jobs(self) -> dict:
        """List available jobs including the `sandbox` flag.

        Sandbox agents get test jobs back in a different shape:
          - `sandbox: True` marker
          - jobs have `capability` (singular) instead of `tags`
          - no `budget_usdc`
        Active agents get real marketplace jobs with no `sandbox` key.
        """
        resp = await self._http.get("/api/v1/agents/jobs")
        await self._raise_for_status(resp)
        return resp.json()

    async def deliver_test_job(self, test_job_id: str, result: str) -> dict:
        """Deliver a sandbox test job result.

        Backend validates the JSON result against the template's
        expected_schema (jsonschema). Returns {"passed": bool, "reason": str}.
        """
        resp = await self._request_with_retry(
            "POST",
            f"/api/v1/agents/test-jobs/{test_job_id}/deliver",
            json={"result": result},
        )
        await self._raise_for_status(resp)
        return resp.json()

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

    async def accept_job(self, job_id: str) -> dict:
        """Acknowledge an award and advance the job to ``executing``.

        The payment path is supposed to promote us out of ``assigned`` when
        escrow funds on-chain. When funding is skipped or fails (devnet ATA
        missing, no wallet, RPC outage), we can push the transition from
        the agent side so the handler can run. Safe to call idempotently —
        the backend rejects with 409 if we're already past ``assigned``.
        """
        resp = await self._request_with_retry(
            "POST", f"/api/v1/agents/jobs/{job_id}/accept"
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
        self,
        job_id: str,
        percent: int,
        message: str | None = None,
        level: str = "info",
    ) -> dict:
        """Report execution progress on a job (retries on transient failures).

        `level` must be one of 'info' | 'warn' | 'error'. Defaults to 'info'
        for backward compatibility with existing handlers that don't pass it.
        """
        if level not in ("info", "warn", "error"):
            raise ValueError(
                f"level must be 'info' | 'warn' | 'error', got {level!r}"
            )
        body: dict = {"job_id": job_id, "percent": percent, "level": level}
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
        """Rotate API key. Old key stays valid for 60s so any in-flight
        requests still succeed; after rotation this client automatically
        switches to the new key so subsequent calls use it.

        Returns a dict with `api_key`, and — when the backend supports
        it — a fresh `token` + `expires_in` so callers can refresh their
        Realtime auth without waiting for the next refresh tick.
        """
        resp = await self._http.post("/api/v1/agents/keys/rotate")
        await self._raise_for_status(resp)
        data = resp.json()
        new_key = data.get("api_key")
        if new_key:
            self._api_key = new_key
            self._http.headers["X-API-Key"] = new_key
        return data

    def set_jwt(self, jwt: str | None) -> None:
        """Attach a user JWT for Bearer-auth endpoints like GET /agents.

        JWT-auth endpoints (list_agents, delete_agent,
        register_agent_authenticated) check self._jwt; API-key endpoints
        ignore it.
        """
        self._jwt = jwt

    async def list_agents(
        self,
        status: str | None = None,
        include_deleted: bool = False,
    ) -> dict:
        """GET /api/v1/agents — list agents owned by the JWT user."""
        if not getattr(self, "_jwt", None):
            raise APIError(401, "JWT not set; call set_jwt() first")
        params: dict = {"include_deleted": include_deleted}
        if status:
            params["status"] = status
        resp = await self._request_with_retry(
            "GET",
            f"{self._base_url}/api/v1/agents",
            headers={"Authorization": f"Bearer {self._jwt}"},
            params=params,
        )
        await self._raise_for_status(resp)
        return resp.json()

    async def delete_agent(self, agent_id: str) -> dict:
        """DELETE /api/v1/agents/{id} — soft-delete + revoke keys (JWT auth)."""
        if not getattr(self, "_jwt", None):
            raise APIError(401, "JWT not set; call set_jwt() first")
        resp = await self._request_with_retry(
            "DELETE",
            f"{self._base_url}/api/v1/agents/{agent_id}",
            headers={"Authorization": f"Bearer {self._jwt}"},
        )
        await self._raise_for_status(resp)
        return resp.json()

    async def register_agent_authenticated(
        self,
        name: str,
        capabilities: list[str],
        wallet_address: str,
        description: str | None = None,
        webhook_url: str | None = None,
        icon_url: str | None = None,
    ) -> dict:
        """POST /api/v1/agents/register (JWT-auth). Reuses logged-in user JWT
        — no email/password re-entry (closes project_cli_register_auth_todo).
        """
        if not getattr(self, "_jwt", None):
            raise APIError(401, "JWT not set; call set_jwt() first")
        body: dict = {
            "name": name,
            "capabilities": capabilities,
            "wallet_address": wallet_address,
        }
        if description is not None:
            body["description"] = description
        if webhook_url is not None:
            body["webhook_url"] = webhook_url
        if icon_url is not None:
            body["icon_url"] = icon_url
        resp = await self._request_with_retry(
            "POST",
            f"{self._base_url}/api/v1/agents/register",
            headers={"Authorization": f"Bearer {self._jwt}"},
            json=body,
        )
        await self._raise_for_status(resp)
        return resp.json()

    async def list_bids(
        self,
        status: str | None = None,
        since: str | None = None,
    ) -> dict:
        """GET /api/v1/agents/bids."""
        params: dict = {}
        if status:
            params["status"] = status
        if since:
            params["since"] = since
        kwargs: dict = {"params": params} if params else {}
        resp = await self._request_with_retry(
            "GET",
            f"{self._base_url}/api/v1/agents/bids",
            **kwargs,
        )
        await self._raise_for_status(resp)
        return resp.json()

    async def list_keys(self, include_revoked: bool = False) -> dict:
        """GET /api/v1/agents/keys. Never returns raw key hashes."""
        resp = await self._request_with_retry(
            "GET",
            f"{self._base_url}/api/v1/agents/keys",
            params={"include_revoked": include_revoked},
        )
        await self._raise_for_status(resp)
        return resp.json()

    async def revoke_key(self, key_id: str) -> dict:
        """POST /api/v1/agents/keys/{id}/revoke."""
        resp = await self._request_with_retry(
            "POST",
            f"{self._base_url}/api/v1/agents/keys/{key_id}/revoke",
        )
        await self._raise_for_status(resp)
        return resp.json()

    async def create_api_key(
        self, agent_id: str, label: str | None = None,
        expires_days: int = 365,
    ) -> dict:
        """POST /api/v1/agents/{id}/keys — create an additional API key (JWT-auth).

        Returns the full response including the raw `api_key` string (shown once).
        """
        if not getattr(self, "_jwt", None):
            raise APIError(401, "JWT not set; call set_jwt() first")
        body: dict = {"expires_days": expires_days}
        if label is not None:
            body["label"] = label
        resp = await self._request_with_retry(
            "POST",
            f"{self._base_url}/api/v1/agents/{agent_id}/keys",
            headers={"Authorization": f"Bearer {self._jwt}"},
            json=body,
        )
        return resp.json()

    async def retry_test_job(self, test_job_id: str) -> dict:
        """POST /api/v1/agents/test-jobs/{id}/retry."""
        resp = await self._request_with_retry(
            "POST",
            f"{self._base_url}/api/v1/agents/test-jobs/{test_job_id}/retry",
        )
        return resp.json()

    async def get_activity_log(
        self,
        since_id: int | None = None,
        since_ts: str | None = None,
        job_id: str | None = None,
        limit: int = 200,
    ) -> dict:
        """GET /api/v1/agents/activity-log (Tier 1 logs poll endpoint).

        Use ``since_id`` as the stable cursor between polls.
        """
        params: dict = {"limit": limit}
        if since_id is not None:
            params["since_id"] = since_id
        if since_ts:
            params["since_ts"] = since_ts
        if job_id:
            params["job_id"] = job_id
        resp = await self._request_with_retry(
            "GET",
            f"{self._base_url}/api/v1/agents/activity-log",
            params=params,
        )
        await self._raise_for_status(resp)
        return resp.json()

    async def get_reputation(self, agent_id: str) -> dict:
        """GET /api/v1/agents/{id}/reputation."""
        resp = await self._request_with_retry(
            "GET",
            f"{self._base_url}/api/v1/agents/{agent_id}/reputation",
        )
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
