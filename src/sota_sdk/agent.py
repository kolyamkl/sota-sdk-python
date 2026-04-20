"""SOTAAgent: event-driven agent framework for the SOTA marketplace."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, Awaitable

from .client import SOTAClient
from .errors import AgentError
from .models import AutoBidConfig, Job, JobContext
from .realtime import RealtimeManager

logger = logging.getLogger("sota_sdk.agent")

HEARTBEAT_INTERVAL = 25  # seconds (buffer before 60s offline threshold)


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
        """Start the agent event loop."""
        logger.info("Starting SOTA agent...")

        # Get agent profile (validates API key)
        self._agent_info = await self._client.get_profile()
        logger.info(
            f"Agent: {self._agent_info.get('name')} ({self._agent_info.get('id')})"
        )

        # Report SDK version
        await self._client.update_profile(sdk_version="0.1.0")

        # Exchange API key for JWT
        await self._exchange_token()

        # Connect to Realtime
        capabilities = self._agent_info.get("capabilities", [])
        await self._realtime.connect(self._jwt)
        await self._realtime.subscribe_jobs(capabilities, self._on_job_received)
        await self._realtime.subscribe_job_updates(self._on_job_update)

        # Start background tasks
        self._running = True
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        refresh_task = asyncio.create_task(self._token_refresh_loop())

        # Handle graceful shutdown
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGTERM", "SIGINT"):
            try:
                import signal
                sig = getattr(signal, sig_name)
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._shutdown(heartbeat_task, refresh_task)))
            except (NotImplementedError, AttributeError):
                pass  # Windows doesn't support add_signal_handler

        logger.info("Agent running. Waiting for jobs...")
        try:
            await asyncio.Event().wait()  # Block forever
        except (KeyboardInterrupt, asyncio.CancelledError):
            await self._shutdown(heartbeat_task, refresh_task)

    async def _shutdown(self, heartbeat_task, refresh_task):
        """Graceful shutdown: cancel tasks, disconnect, close client."""
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
        """Send heartbeat every 25 seconds."""
        while self._running:
            try:
                await self._client.heartbeat()
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

            # Only execute if this agent is the assigned winner
            if job.winner_agent_id != self._agent_info.get("id"):
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
                await self._client.deliver_error(
                    job_id=job.id,
                    error_code="internal_error",
                    error_message=str(e),
                    retryable=False,
                )
