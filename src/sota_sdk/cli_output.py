"""Shared helpers for CLI output: pretty tables, JSON mode, status tags.

Rich is used for tables and color when stdout is a TTY; plain-text
fallback otherwise (so `sota-agent agent list | jq` sees no ANSI codes).
"""

import json
import sys
from typing import Any, Callable, Iterable

from rich.console import Console
from rich.table import Table

_console = Console()

_STATUS_COLORS = {
    "active": "green",
    "sandbox": "yellow",
    "testing_passed": "cyan",
    "pending_review": "blue",
    "suspended": "red",
    "deleted": "bright_black",
    "rejected": "red",
}


def status_tag(status: str) -> str:
    """Return a short tag for an agent/job status.

    In a TTY, returns a rich-markup string (colored when rendered via rich).
    In a pipe / non-TTY, returns the raw status string.
    """
    if sys.stdout.isatty():
        color = _STATUS_COLORS.get(status, "white")
        return f"[{color}]{status}[/{color}]"
    return status


def print_json(data: Any) -> None:
    """Machine-readable output. Always goes to stdout; no color."""
    print(json.dumps(data, default=str, indent=2))


def print_table(columns: list[str], rows: Iterable[tuple]) -> None:
    """Pretty-print a table. Falls back to TSV when not a TTY."""
    rows_list = list(rows)
    if sys.stdout.isatty():
        t = Table(show_header=True, header_style="bold")
        for col in columns:
            t.add_column(col)
        for row in rows_list:
            t.add_row(*[str(c) for c in row])
        _console.print(t)
    else:
        # TSV for pipes
        print("\t".join(columns))
        for row in rows_list:
            print("\t".join(str(c) for c in row))


def emit(
    *,
    json_mode: bool,
    data: Any,
    render: Callable[[Any], str] | None = None,
) -> None:
    """Emit response data in JSON or pretty mode.

    `json_mode=True` → prints JSON (via print_json).
    `json_mode=False` → calls `render(data)` to get a string and prints it.
       If render is None, falls back to `str(data)`.
    """
    if json_mode:
        print_json(data)
        return
    if render is None:
        print(str(data))
    else:
        print(render(data))
