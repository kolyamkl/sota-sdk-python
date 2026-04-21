"""Data models for the SOTA Agent SDK."""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import SOTAClient


class Job(BaseModel):
    """A job posted on the SOTA marketplace."""

    id: str
    description: str
    parameters: dict = Field(default_factory=dict)
    budget_usdc: float
    tags: list[str] = Field(default_factory=list)
    status: str = "open"
    bid_window_seconds: int | None = None
    winner_agent_id: str | None = None
    created_at: str = ""


class Bid(BaseModel):
    """A bid submitted by an agent."""

    id: str | None = None
    job_id: str
    amount_usdc: float
    estimated_seconds: int
    status: str | None = None


class BidOpportunity(BaseModel):
    """Wraps a job for the on_bid_opportunity handler."""

    job: Job


class ProgressUpdate(BaseModel):
    """Progress update for an executing job."""

    job_id: str
    percent: int
    message: str | None = None


class WebhookEvent(BaseModel):
    """An event from the webhook event log."""

    id: str
    event_type: str
    payload: dict = Field(default_factory=dict)
    status: str
    created_at: str


class AutoBidConfig(BaseModel):
    """Configuration for automatic bidding."""

    max_price: float
    capabilities: list[str]
    estimated_seconds: int = 300


class JobContext:
    """Context passed to job handlers with convenience methods."""

    def __init__(self, job: Job, agent_id: str, _client: SOTAClient):
        self.job = job
        self.agent_id = agent_id
        self._client = _client
        self._delivered = False

    async def update_progress(self, percent: int, message: str | None = None):
        """Report progress on the current job."""
        await self._client.report_progress(self.job.id, percent, message)

    async def deliver(self, result: str, result_hash: str | None = None):
        """Deliver the job result."""
        await self._client.deliver(self.job.id, result, result_hash)
        self._delivered = True

    async def fail(
        self,
        error_code: str,
        error_message: str,
        partial_result: str | None = None,
        retryable: bool = False,
    ):
        """Report job failure with error details."""
        await self._client.deliver_error(
            job_id=self.job.id,
            error_code=error_code,
            error_message=error_message,
            partial_result=partial_result,
            retryable=retryable,
        )
        self._delivered = True


class TestJobContext(JobContext):
    """JobContext variant for sandbox test jobs.

    Overrides `deliver` to hit the test-jobs endpoint (which validates
    against the template's expected JSON schema) and makes
    `update_progress` a no-op — the backend test-job flow has no
    progress channel.
    """

    __test__ = False  # pytest: this is an SDK class, not a test class

    def __init__(
        self, job: Job, agent_id: str, _client: SOTAClient, test_job_id: str
    ):
        super().__init__(job=job, agent_id=agent_id, _client=_client)
        self._test_job_id = test_job_id
        self.last_validation: dict | None = None

    async def update_progress(self, percent: int, message: str | None = None):
        # Progress is not tracked for sandbox tests — accept the call
        # silently so the same handler works in both modes.
        return None

    async def deliver(self, result: str, result_hash: str | None = None):
        self.last_validation = await self._client.deliver_test_job(
            self._test_job_id, result
        )
        self._delivered = True
        if not self.last_validation.get("passed", False):
            # Surface the verdict to the handler so a failed test is visible
            # in developer logs immediately, not only after checking the portal.
            from .errors import AgentError, ErrorCode
            reason = self.last_validation.get("reason", "validation failed")
            raise AgentError(
                code=ErrorCode.INVALID_INPUT,
                message=f"Test job validation failed: {reason}",
                debug_info=self.last_validation,
            )
