"""Snapshot tests: help output should list all registered commands and be
side-effect-free. Click handles --help natively, but a regression here
would be a show-stopper (e.g., if someone registers a command name that
collides or misses add_command)."""

import pytest
from click.testing import CliRunner


EXPECTED_TOP_LEVEL_COMMANDS = [
    # Pre-existing
    "login", "init", "config", "request-review",
    # Task 7
    "logout", "whoami", "version",
    # Task 8
    "agent",
    # Task 9-10
    "status", "watch", "ping", "run", "logs",
    # Task 11
    "jobs", "bids", "bid", "job",
    # Task 12
    "sandbox", "review",
    # Task 13
    "keys",
    # Task 14
    "reputation", "doctor", "capabilities", "onboard",
    # Task 15
    "webhook",
]


def test_top_level_help_lists_all_commands():
    from sota_sdk.cli import main
    runner = CliRunner()
    r = runner.invoke(main, ["--help"])
    assert r.exit_code == 0
    for cmd in EXPECTED_TOP_LEVEL_COMMANDS:
        assert cmd in r.output, (
            f"expected top-level command {cmd!r} in --help output; got:\n"
            f"{r.output}"
        )


@pytest.mark.parametrize("argv", [
    ["agent", "--help"],
    ["agent", "list", "--help"],
    ["agent", "edit", "--help"],
    ["agent", "delete", "--help"],
    ["agent", "set", "--help"],
    ["keys", "--help"],
    ["keys", "rotate", "--help"],
    ["keys", "create", "--help"],
    ["logs", "--help"],
    ["sandbox", "--help"],
    ["sandbox", "retry", "--help"],
    ["review", "--help"],
    ["jobs", "--help"],
    ["bids", "--help"],
    ["bid", "--help"],
    ["bid", "submit", "--help"],
    ["webhook", "--help"],
    ["webhook", "verify", "--help"],
])
def test_subcommand_help_exits_cleanly(argv):
    """Subcommand --help must print usage, not execute. Click handles this
    but this regression test catches accidental add_command misregistrations."""
    from sota_sdk.cli import main
    runner = CliRunner()
    r = runner.invoke(main, argv)
    assert r.exit_code == 0, (
        f"--help on {argv!r} exited {r.exit_code}\n{r.output}"
    )
    assert "Usage:" in r.output
