"""Tests for CLI output helpers."""

import json
import pytest

from sota_sdk.cli_output import (
    print_json,
    print_table,
    status_tag,
    emit,
)


def test_print_json_round_trips(capsys):
    print_json({"a": 1, "b": [1, 2]})
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1, "b": [1, 2]}


def test_print_json_handles_datetime_like_strings(capsys):
    print_json({"created_at": "2026-04-22T00:00:00Z"})
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["created_at"] == "2026-04-22T00:00:00Z"


def test_status_tag_returns_nonempty_string():
    # Non-TTY should just return the raw status.
    # TTY would wrap in rich markup. Either way, non-empty.
    for status in ("active", "sandbox", "deleted", "testing_passed",
                   "pending_review", "suspended", "rejected"):
        assert status_tag(status)


def test_emit_json_mode_prints_json(capsys):
    emit(json_mode=True, data={"status": "ok"})
    out = capsys.readouterr().out
    assert json.loads(out) == {"status": "ok"}


def test_emit_pretty_mode_uses_render(capsys):
    emit(
        json_mode=False,
        data={"status": "ok"},
        render=lambda d: f"status: {d['status']}",
    )
    out = capsys.readouterr().out
    assert "status: ok" in out


def test_emit_pretty_no_render_prints_str(capsys):
    emit(json_mode=False, data="hello")
    out = capsys.readouterr().out
    assert "hello" in out


def test_print_table_smoke(capsys):
    print_table(
        columns=["name", "status"],
        rows=[("alpha", "active"), ("beta", "sandbox")],
    )
    out = capsys.readouterr().out
    assert out
    # In non-TTY mode should be tab-separated; in TTY mode rich formatting.
    # Just verify the row contents appear somewhere.
    assert "alpha" in out
    assert "beta" in out


def test_print_table_empty_rows(capsys):
    # Shouldn't crash on empty rows
    print_table(columns=["a", "b"], rows=[])
    out = capsys.readouterr().out
    # Header should still appear (either plain or in rich)
    assert out  # something was printed
