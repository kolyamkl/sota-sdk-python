"""SOTA Agent SDK - Build AI agents for the SOTA marketplace."""
from .agent import SOTAAgent
from .client import SOTAClient, APIError, verify_webhook_signature
from .errors import AgentError, ErrorCode
from .models import AutoBidConfig, Bid, Job, JobContext, TestJobContext, WebhookEvent

__version__ = "0.1.0"

__all__ = [
    "SOTAAgent",
    "SOTAClient",
    "APIError",
    "AgentError",
    "ErrorCode",
    "Job",
    "Bid",
    "JobContext",
    "TestJobContext",
    "AutoBidConfig",
    "WebhookEvent",
    "verify_webhook_signature",
]
