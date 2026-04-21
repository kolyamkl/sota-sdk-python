"""RealtimeManager must notify the agent when reconnect is exhausted.

Without this signal the agent would keep heartbeating forever while no
new jobs ever arrive — backend sees a healthy agent, marks it offline
on job assignment timeout, reputation takes a hit.
"""
import sys
import types

import pytest

from sota_sdk.realtime import RealtimeManager


def _install_failing_supabase(monkeypatch):
    """Make `from supabase import create_client` raise inside _reconnect."""
    fake = types.ModuleType("supabase")

    def boom(url, key):
        raise RuntimeError("connection refused")

    fake.create_client = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "supabase", fake)


@pytest.mark.asyncio
async def test_reconnect_exhaustion_fires_on_fatal(monkeypatch):
    rm = RealtimeManager("https://example.supabase.co", "anon-key")

    received: list[Exception] = []
    rm.set_on_fatal(lambda err: received.append(err))

    import sota_sdk.realtime as rt_mod
    monkeypatch.setattr(rt_mod, "RECONNECT_DELAYS", [0, 0])
    _install_failing_supabase(monkeypatch)

    await rm._reconnect()

    assert rm._connected is False
    assert len(received) == 1
    assert isinstance(received[0], RuntimeError)
    assert "exhausted" in str(received[0]).lower()


@pytest.mark.asyncio
async def test_callback_exception_does_not_break_manager(monkeypatch):
    """A buggy user callback shouldn't take down the realtime manager."""
    rm = RealtimeManager("https://example.supabase.co", "anon-key")

    def raiser(err):
        raise ValueError("callback is buggy")

    rm.set_on_fatal(raiser)

    import sota_sdk.realtime as rt_mod
    monkeypatch.setattr(rt_mod, "RECONNECT_DELAYS", [0])
    _install_failing_supabase(monkeypatch)

    # Must not propagate the callback's ValueError
    await rm._reconnect()


def test_no_callback_registered_is_safe():
    """Not registering on_fatal must not cause AttributeError inside _reconnect."""
    rm = RealtimeManager("https://example.supabase.co", "anon-key")
    assert rm._on_fatal_callback is None
