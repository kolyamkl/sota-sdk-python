"""Error types for the SOTA Agent SDK."""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """Predefined error categories for agent error reporting."""

    TIMEOUT = "timeout"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    AUTHENTICATION_FAILED = "authentication_failed"
    INVALID_INPUT = "invalid_input"
    INTERNAL_ERROR = "internal_error"
    RATE_LIMITED = "rate_limited"


class AgentError(Exception):
    """Raised by agent handlers to report structured errors."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        partial_result: str | None = None,
        retryable: bool = False,
        debug_info: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.partial_result = partial_result
        self.retryable = retryable
        self.debug_info = debug_info or {}
