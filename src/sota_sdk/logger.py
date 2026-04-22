"""Structured per-job logger (Tier 2 SDK logs).

Piggybacks on `POST /api/v1/agents/progress` with the `level` field added
by Plan 1 — no new backend endpoint needed. Messages appear in the Tier 1
`agent_activity_log` stream with `[INFO]` / `[WARN]` / `[ERROR]` prefixes
(the backend's submit_progress applies the prefix to chat content).

Use `ctx.log.info(...)` inside handlers instead of `print()` for messages
that should reach marketplace operators via `sota-agent logs`.
"""

from typing import Protocol


class _ClientLike(Protocol):
    async def report_progress(
        self, job_id: str, percent: int, message: str | None = None,
        level: str = "info",
    ) -> dict: ...


class JobLogger:
    """Emit structured log lines for the current job.

    Each call POSTs a progress entry with `percent=0` and the given
    level + message, so the emission is addressable in the Tier 1 stream
    but does NOT register as progress toward completion.
    """

    def __init__(self, job_id: str, client: _ClientLike):
        self._job_id = job_id
        self._client = client

    async def info(self, message: str) -> None:
        await self._client.report_progress(
            job_id=self._job_id, percent=0, message=message, level="info",
        )

    async def warn(self, message: str) -> None:
        await self._client.report_progress(
            job_id=self._job_id, percent=0, message=message, level="warn",
        )

    async def error(self, message: str) -> None:
        await self._client.report_progress(
            job_id=self._job_id, percent=0, message=message, level="error",
        )


class _NoopJobLogger:
    """Sandbox-safe no-op logger.

    Kept behind `ctx.log` in `TestJobContext` so handler code using
    `ctx.log.info(...)` runs unchanged during sandbox tests — consistent
    with how `TestJobContext.update_progress` is already a no-op.
    """

    async def info(self, message: str) -> None:
        return None

    async def warn(self, message: str) -> None:
        return None

    async def error(self, message: str) -> None:
        return None
