"""Supabase Realtime connection manager with auto-reconnect."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

logger = logging.getLogger("sota_sdk.realtime")

RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30]  # max 30s between retries


class RealtimeManager:
    """Manages Supabase Realtime connection for job event streaming."""

    def __init__(self, supabase_url: str, supabase_anon_key: str):
        self._url = supabase_url
        self._anon_key = supabase_anon_key
        self._jwt: str | None = None
        self._client = None
        self._channels: dict[str, object] = {}
        self._on_job_callback: Callable | None = None
        self._on_update_callback: Callable | None = None
        self._connected = False
        self._capabilities: list[str] = []
        self._reconnect_task: asyncio.Task | None = None

    async def connect(self, jwt: str):
        """Connect to Supabase Realtime with the provided JWT."""
        self._jwt = jwt
        if not self._url or not self._anon_key:
            logger.warning("Supabase URL/key not configured — realtime disabled")
            return
        try:
            from supabase import create_client

            self._client = create_client(self._url, self._anon_key)
            self._client.auth.set_session(jwt, jwt)
            self._connected = True
            logger.info("Connected to Supabase Realtime")
        except Exception as e:
            logger.error(f"Realtime connection failed: {e}")
            self._connected = False
            raise

    async def _reconnect(self):
        """Auto-reconnect with exponential backoff."""
        for attempt, delay in enumerate(RECONNECT_DELAYS):
            logger.info(f"Reconnecting in {delay}s (attempt {attempt + 1}/{len(RECONNECT_DELAYS)})...")
            await asyncio.sleep(delay)
            try:
                # Disconnect old state
                await self._cleanup_channels()
                self._client = None

                # Reconnect
                from supabase import create_client
                self._client = create_client(self._url, self._anon_key)
                if self._jwt:
                    self._client.auth.set_session(self._jwt, self._jwt)
                self._connected = True

                # Re-subscribe
                if self._on_job_callback and self._capabilities:
                    await self.subscribe_jobs(self._capabilities, self._on_job_callback)
                if self._on_update_callback:
                    await self.subscribe_job_updates(self._on_update_callback)

                logger.info("Reconnected to Supabase Realtime")
                return
            except Exception as e:
                logger.error(f"Reconnect attempt {attempt + 1} failed: {e}")

        logger.error("All reconnect attempts exhausted — realtime disabled")
        self._connected = False

    def _schedule_reconnect(self):
        """Schedule a reconnect if not already in progress."""
        if self._reconnect_task and not self._reconnect_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
            self._reconnect_task = loop.create_task(self._reconnect())
        except RuntimeError:
            logger.warning("No running event loop — cannot reconnect")

    async def subscribe_jobs(self, capabilities: list[str], callback: Callable):
        """Subscribe to new job broadcasts (INSERT events)."""
        self._on_job_callback = callback
        self._capabilities = capabilities
        if not self._client:
            logger.warning("Not connected — skipping job subscription")
            return

        channel = self._client.channel("jobs:bidding")
        channel.on_postgres_changes(
            event="INSERT",
            schema="public",
            table="jobs",
            callback=lambda payload: self._dispatch(
                payload, self._handle_job_event
            ),
        )
        channel.subscribe()
        self._channels["jobs"] = channel

    async def subscribe_job_updates(self, callback: Callable):
        """Subscribe to job status updates (UPDATE events — for assignment detection)."""
        self._on_update_callback = callback
        if not self._client:
            logger.warning("Not connected — skipping update subscription")
            return

        channel = self._client.channel("jobs:updates")
        channel.on_postgres_changes(
            event="UPDATE",
            schema="public",
            table="jobs",
            callback=lambda payload: self._dispatch(
                payload, self._handle_update_event
            ),
        )
        channel.subscribe()
        self._channels["updates"] = channel

    def _dispatch(self, payload, handler):
        """Safely dispatch a realtime event to an async handler."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(handler(payload))
        except RuntimeError:
            logger.warning("No running event loop — dropping realtime event")

    async def _handle_job_event(self, payload):
        """Route incoming new job to callback."""
        if self._on_job_callback:
            record = (
                payload.get("new", payload) if isinstance(payload, dict) else payload
            )
            try:
                await self._on_job_callback(record)
            except Exception as e:
                logger.error(f"Job event handler error: {e}")

    async def _handle_update_event(self, payload):
        """Route job update to callback."""
        if self._on_update_callback:
            record = (
                payload.get("new", payload) if isinstance(payload, dict) else payload
            )
            try:
                await self._on_update_callback(record)
            except Exception as e:
                logger.error(f"Update event handler error: {e}")

    async def set_auth(self, jwt: str):
        """Update the Realtime auth token (for refresh)."""
        self._jwt = jwt
        if self._client:
            try:
                self._client.auth.set_session(jwt, jwt)
            except Exception as e:
                logger.warning(f"Token refresh on Realtime failed: {e}")
                self._schedule_reconnect()

    async def _cleanup_channels(self):
        """Unsubscribe from all channels."""
        for channel in self._channels.values():
            try:
                channel.unsubscribe()
            except Exception:
                pass
        self._channels.clear()

    async def disconnect(self):
        """Disconnect from Realtime and clean up resources."""
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        await self._cleanup_channels()
        self._connected = False
        self._client = None
