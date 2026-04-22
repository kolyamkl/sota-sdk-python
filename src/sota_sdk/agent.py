"""SOTAAgent: event-driven agent framework for the SOTA marketplace."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, Awaitable

from .client import APIError, SOTAClient
from .errors import AgentError
from .models import AutoBidConfig, Job, JobContext, TestJobContext
from .realtime import RealtimeManager

logger = logging.getLogger("sota_sdk.agent")

HEARTBEAT_INTERVAL = 25  # seconds (3 beats within backend's 90s offline threshold)
SANDBOX_POLL_INTERVAL = 5  # seconds — how often sandbox agents poll for test jobs


class SOTAAgent:
    """Event-driven agent that connects to the SOTA marketplace.

    Usage:
        agent = SOTAAgent()

        @agent.on_job("echo")
        async def handle_echo(ctx: JobContext):
            return f"Echo: {ctx.job.description}"

        asyncio.run(agent.run())
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        supabase_url: str | None = None,
        supabase_anon_key: str | None = None,
    ):
        self._api_key = api_key or os.environ.get("SOTA_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "API key required: pass api_key argument or set SOTA_API_KEY env var"
            )
        self._base_url = (
            base_url or os.environ.get("SOTA_API_URL", "http://localhost:3001")
        ).rstrip("/")
        self._supabase_url = supabase_url or os.environ.get("SUPABASE_URL", "")
        self._supabase_anon_key = supabase_anon_key or os.environ.get(
            "SUPABASE_ANON_KEY", ""
        )

        self._client = SOTAClient(self._api_key, self._base_url)
        self._realtime = RealtimeManager(self._supabase_url, self._supabase_anon_key)

        self._handlers: dict[str, Callable[..., Awaitable]] = {}
        self._bid_handlers: dict[str, Callable[..., Awaitable]] = {}
        self._auto_bid_config: AutoBidConfig | None = None
        self._agent_info: dict | None = None
        self._jwt: str | None = None
        self._jwt_expires_at: float = 0
        self._running = False
        self._stop_event: asyncio.Event | None = None
        self._fatal_error: Exception | None = None

    def on_job(self, capability: str):
        """Decorator to register a job handler for a capability.

        Example:
            @agent.on_job("web-scraping")
            async def handle(ctx: JobContext):
                return "result"
        """

        def decorator(func):
            self._handlers[capability] = func
            return func

        return decorator

    def on_bid_opportunity(self, capability: str):
        """Decorator to register custom bid logic for a capability.

        Example:
            @agent.on_bid_opportunity("web-scraping")
            async def decide_bid(job: Job):
                if job.budget_usdc > 5:
                    await agent._client.submit_bid(job.id, 4.5, 60)
        """

        def decorator(func):
            self._bid_handlers[capability] = func
            return func

        return decorator

    def set_auto_bid(
        self,
        max_price: float,
        capabilities: list[str],
        estimated_seconds: int = 300,
    ):
        """Configure automatic bidding for matching jobs.

        Args:
            max_price: Maximum bid price in USDC.
            capabilities: List of capabilities to auto-bid on.
            estimated_seconds: Default time estimate for auto-bids (default: 300).
        """
        if max_price <= 0:
            raise ValueError("max_price must be greater than 0")
        if not capabilities:
            raise ValueError("capabilities list cannot be empty")
        self._auto_bid_config = AutoBidConfig(
            max_price=max_price,
            capabilities=capabilities,
            estimated_seconds=estimated_seconds,
        )

    async def run(self):
        """Start the agent event loop.

        Branches on agent status:
          - 'sandbox'        → poll + deliver to test-jobs endpoint
          - 'active'         → Realtime subscription (normal marketplace flow)
          - other (pending_review / rejected / suspended / testing_passed):
            log clearly and poll /agents/me every 60s for a status flip.
        """
        logger.info("Starting SOTA agent...")
        self._stop_event = asyncio.Event()
        self._fatal_error = None

        # Get agent profile (validates API key)
        self._agent_info = await self._client.get_profile()
        status = self._agent_info.get("status", "active")
        caps = self._agent_info.get("capabilities", [])
        name = self._agent_info.get("name", "(unnamed)")
        agent_id = self._agent_info.get("id", "(no-id)")
        caps_str = ", ".join(caps) if caps else "none"
        logger.info(
            f"Connected: {name} [{agent_id}] | "
            f"status={status} | capabilities=[{caps_str}]"
        )

        # Report SDK version (best-effort)
        try:
            await self._client.update_profile(sdk_version="0.1.0")
        except Exception as e:
            logger.debug(f"sdk_version update failed (non-fatal): {e}")

        self._running = True
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            if status == "sandbox":
                await self._run_sandbox_loop()
                # Re-check status after sandbox loop exits — might be
                # ready for active mode.
                self._agent_info = await self._client.get_profile()
                status = self._agent_info.get("status", "")

            if status == "active":
                await self._run_active_loop()
            elif status in {"testing_passed", "pending_review", "rejected", "suspended"}:
                await self._wait_for_active(status)
            elif status != "sandbox":
                logger.warning(
                    f"Unknown agent status '{status}' — idling. "
                    f"Check the developer portal."
                )
                await asyncio.Event().wait()
        finally:
            heartbeat_task.cancel()
            await self._client.close()

        if self._fatal_error is not None:
            raise self._fatal_error

    async def _run_active_loop(self):
        """Active-mode: subscribe to Realtime, wait for jobs."""
        # Exchange API key for JWT
        await self._exchange_token()

        # Wire a fatal callback so exhausted reconnect tells the main loop
        # to shut down instead of heartbeating forever with no jobs arriving.
        self._realtime.set_on_fatal(self._on_realtime_fatal)

        # Connect to Realtime
        capabilities = self._agent_info.get("capabilities", [])
        await self._realtime.connect(self._jwt)
        await self._realtime.subscribe_jobs(capabilities, self._on_job_received)
        await self._realtime.subscribe_job_updates(self._on_job_update)

        refresh_task = asyncio.create_task(self._token_refresh_loop())

        # Handle graceful shutdown
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGTERM", "SIGINT"):
            try:
                import signal
                sig = getattr(signal, sig_name)
                loop.add_signal_handler(
                    sig,
                    lambda: asyncio.create_task(self._shutdown_active(refresh_task)),
                )
            except (NotImplementedError, AttributeError):
                pass  # Windows

        logger.info("Agent running (active). Waiting for jobs...")
        try:
            assert self._stop_event is not None
            await self._stop_event.wait()
            await self._shutdown_active(refresh_task)
        except (KeyboardInterrupt, asyncio.CancelledError):
            await self._shutdown_active(refresh_task)

    async def _run_sandbox_loop(self):
        """Sandbox-mode: poll /agents/jobs, route handlers, deliver to
        test-jobs endpoint. Exits when the backend flips the agent out
        of `sandbox` status (all tests passed, or admin intervened)."""
        logger.info(
            f"Agent in sandbox mode. Polling for test jobs every "
            f"{SANDBOX_POLL_INTERVAL}s. Complete all 3 to unlock review."
        )
        seen: set[str] = set()
        first_poll = True

        while self._running:
            try:
                data = await self._client.list_available_jobs()
            except Exception as e:
                logger.warning(f"[sandbox] Failed to list jobs: {e}")
                await asyncio.sleep(SANDBOX_POLL_INTERVAL)
                continue

            if not data.get("sandbox"):
                logger.info(
                    "[sandbox] Backend no longer reports sandbox mode — "
                    "exiting sandbox loop."
                )
                return

            if first_poll:
                pending = [
                    j for j in data.get("jobs", [])
                    if j.get("status") in ("pending", "assigned")
                ]
                logger.info(
                    f"[sandbox] Ready — {len(pending)} test job(s) pending."
                )
                first_poll = False

            for test_job in data.get("jobs", []):
                tj_id = test_job.get("id")
                if not tj_id or tj_id in seen:
                    continue
                if test_job.get("status") not in ("pending", "assigned"):
                    continue
                seen.add(tj_id)
                try:
                    await self._execute_test_job(test_job)
                except Exception as e:
                    logger.error(
                        f"[sandbox] Unexpected error on test job {tj_id}: {e}"
                    )

            await asyncio.sleep(SANDBOX_POLL_INTERVAL)

    async def _execute_test_job(self, test_job: dict):
        """Run the right handler for a sandbox test job and deliver it."""
        capability = test_job.get("capability", "")
        handler = self._handlers.get(capability) or self._handlers.get("_default")
        tj_id = test_job["id"]

        if not handler:
            logger.warning(
                f"[sandbox] No handler registered for capability "
                f"'{capability}' (and no '_default' fallback). "
                f"Skipping test job {tj_id}."
            )
            return

        synthetic_job = Job(
            id=tj_id,
            description=test_job.get("description", ""),
            parameters=test_job.get("parameters") or {},
            budget_usdc=0.0,
            tags=[capability] if capability else [],
            status="executing",
        )
        ctx = TestJobContext(
            job=synthetic_job,
            agent_id=self._agent_info["id"],
            _client=self._client,
            test_job_id=tj_id,
        )

        try:
            result = await handler(ctx)
            if result is not None and not ctx._delivered:
                payload = result if isinstance(result, str) else str(result)
                await ctx.deliver(payload)
            verdict = ctx.last_validation or {}
            passed = verdict.get("passed", False)
            reason = verdict.get("reason", "")
            status_str = "passed" if passed else "failed"
            logger.info(
                f"[sandbox] Test job {tj_id} ({capability}): {status_str}  "
                f"{reason}".rstrip()
            )
        except Exception as e:
            logger.error(
                f"[sandbox] Handler for capability '{capability}' raised: {e}"
            )

    async def _wait_for_active(self, current_status: str):
        """Idle-poll /agents/me every 60s waiting for `active` status.

        Used for testing_passed / pending_review / rejected / suspended.
        Logs each observed transition so the operator sees what's happening.
        """
        messages = {
            "testing_passed": "all test jobs passed — run `sota-agent request-review`",
            "pending_review": "waiting for admin review",
            "rejected": "admin rejected this agent — fix and resubmit",
            "suspended": "agent is suspended by admin",
        }
        logger.info(
            f"Agent status '{current_status}' — {messages.get(current_status, '')}"
        )
        while self._running:
            try:
                assert self._stop_event is not None
                await asyncio.wait_for(self._stop_event.wait(), timeout=60)
                return  # stop_event fired → agent told to shut down
            except asyncio.TimeoutError:
                pass
            try:
                self._agent_info = await self._client.get_profile()
            except Exception as e:
                logger.debug(f"Status check failed: {e}")
                continue
            new_status = self._agent_info.get("status", "")
            if new_status == current_status:
                continue
            logger.info(f"Agent status: {current_status} -> {new_status}")
            current_status = new_status
            if new_status == "active":
                logger.info("Entering active mode.")
                await self._run_active_loop()
                return
            if new_status == "sandbox":
                await self._run_sandbox_loop()
                return
            messages_str = messages.get(new_status, "")
            if messages_str:
                logger.info(messages_str)

    async def _shutdown_active(self, refresh_task):
        """Graceful shutdown for active loop."""
        logger.info("Shutting down gracefully...")
        self._running = False
        refresh_task.cancel()
        await self._realtime.disconnect()
        logger.info("Agent stopped.")

    def _on_realtime_fatal(self, err: Exception):
        """Called by RealtimeManager when reconnect is exhausted.

        Signal the main loop to exit so run() re-raises and a supervisor
        can restart us cleanly. Anything else and we'd sit heartbeating
        indefinitely while no new jobs ever arrive.
        """
        logger.error(f"Realtime fatal: {err} — stopping agent")
        if self._fatal_error is None:
            self._fatal_error = err
        self._running = False
        if self._stop_event and not self._stop_event.is_set():
            self._stop_event.set()

    async def _shutdown(self, heartbeat_task, refresh_task):
        """Legacy shutdown used by external callers / older tests."""
        logger.info("Shutting down gracefully...")
        self._running = False
        heartbeat_task.cancel()
        refresh_task.cancel()
        await self._realtime.disconnect()
        await self._client.close()
        logger.info("Agent stopped.")

    async def _exchange_token(self):
        """Exchange API key for short-lived Supabase JWT."""
        data = await self._client.exchange_token()
        self._jwt = data["token"]
        expires_in = data.get("expires_in", 900)
        self._jwt_expires_at = time.time() + expires_in - 180  # refresh 3min early

    async def _token_refresh_loop(self):
        """Periodically refresh the JWT before expiry."""
        while self._running:
            sleep_time = max(self._jwt_expires_at - time.time(), 60)
            await asyncio.sleep(sleep_time)
            try:
                await self._exchange_token()
                await self._realtime.set_auth(self._jwt)
                logger.debug("JWT refreshed")
            except Exception as e:
                logger.error(f"JWT refresh failed: {e}")

    async def _heartbeat_loop(self):
        """Send heartbeat every HEARTBEAT_INTERVAL seconds.

        A 401 is fatal: the API key has been revoked or rotated, so
        further attempts will just log the same error forever. Signal
        the main loop to stop and stash the error for run() to re-raise.
        """
        while self._running:
            try:
                await self._client.heartbeat()
            except APIError as e:
                if e.status == 401:
                    logger.error(
                        "Heartbeat rejected with 401 — API key revoked or "
                        "invalid. Stopping agent; rotate credentials and restart."
                    )
                    self._fatal_error = e
                    self._running = False
                    if self._stop_event:
                        self._stop_event.set()
                    return
                logger.warning(f"Heartbeat failed: {e}")
            except Exception as e:
                logger.warning(f"Heartbeat failed: {e}")
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _on_job_received(self, job_data: dict):
        """Handle incoming new job from Realtime broadcast — bid phase."""
        try:
            job = Job(**job_data) if isinstance(job_data, dict) else job_data
            job_tags = set(getattr(job, "tags", []) or [])
            agent_caps = set(self._agent_info.get("capabilities", []))

            if not job_tags.intersection(agent_caps):
                return  # Capability mismatch

            # Check auto-bid config
            if self._auto_bid_config:
                auto_caps = set(self._auto_bid_config.capabilities)
                if (
                    job_tags.intersection(auto_caps)
                    and job.budget_usdc <= self._auto_bid_config.max_price
                ):
                    await self._client.submit_bid(
                        job_id=job.id,
                        amount_usdc=job.budget_usdc,
                        estimated_seconds=self._auto_bid_config.estimated_seconds,
                    )
                    logger.info(f"Auto-bid placed on job {job.id}")
                    return

            # Check for custom bid handler
            for cap in job_tags.intersection(agent_caps):
                if cap in self._bid_handlers:
                    await self._bid_handlers[cap](job)
                    return

        except Exception as e:
            logger.error(f"Error handling new job: {e}")

    async def _on_job_update(self, job_data: dict):
        """Handle job status update — execute when assigned to this agent."""
        try:
            job = Job(**job_data) if isinstance(job_data, dict) else job_data

            # Only react to jobs where we're the declared winner.
            if job.winner_agent_id != self._agent_info.get("id"):
                return

            # On award push the transition to executing ourselves. Escrow
            # may still fund in the background, but handler gating lives on
            # ``executing`` and wallet-less / devnet-ATA-missing agents
            # would otherwise stall indefinitely.
            if job.status == "assigned":
                try:
                    await self._client.accept_job(job.id)
                except Exception as accept_err:
                    logger.error(f"Failed to accept job {job.id}: {accept_err}")
                return

            if job.status != "executing":
                return

            await self._execute_job(job)
        except Exception as e:
            logger.error(f"Error handling job update: {e}")

    async def _execute_job(self, job: Job):
        """Execute a job using the registered handler."""
        job_tags = set(getattr(job, "tags", []) or [])
        agent_caps = set(self._agent_info.get("capabilities", []))

        handler = None
        for cap in job_tags.intersection(agent_caps):
            if cap in self._handlers:
                handler = self._handlers[cap]
                break

        if not handler:
            logger.warning(f"No handler for job {job.id}")
            return

        ctx = JobContext(
            job=job,
            agent_id=self._agent_info["id"],
            _client=self._client,
        )

        try:
            result = await handler(ctx)
            if result and not ctx._delivered:
                await ctx.deliver(str(result))
        except AgentError as e:
            if not ctx._delivered:
                await self._client.deliver_error(
                    job_id=job.id,
                    error_code=e.code.value,
                    error_message=e.message,
                    partial_result=e.partial_result,
                    retryable=e.retryable,
                )
        except Exception as e:
            logger.error(f"Job {job.id} failed: {e}")
            if not ctx._delivered:
                # Unhandled exceptions are almost always transient (timeouts,
                # socket errors, OOM after supervisor restart). Default to
                # retryable=True; developers opt out via AgentError(..., retryable=False).
                await self._client.deliver_error(
                    job_id=job.id,
                    error_code="internal_error",
                    error_message=str(e),
                    retryable=True,
                )
