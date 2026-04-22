"""Keys CLI group: list, rotate, create, revoke."""

import asyncio
import json
import os
from pathlib import Path

import click

from ..auth import get_api_url
from ..client import SOTAClient
from ..cli_context import (
    resolve_api_key, atomic_replace_env_var, NoAgentContextError,
)
from ..cli_output import emit, print_table


def _require_key() -> str:
    try:
        return resolve_api_key()
    except NoAgentContextError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


def _load_creds_here() -> dict | None:
    path = Path(os.path.expanduser("~/.sota/credentials"))
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


@click.group()
def keys():
    """Manage agent API keys."""


@keys.command("list")
@click.option("--include-revoked", is_flag=True)
@click.option("--json", "json_mode", is_flag=True)
def keys_list(include_revoked, json_mode):
    """List API keys for the current agent. Raw keys are never returned."""
    api_key = _require_key()

    async def _run():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await c.list_keys(include_revoked=include_revoked)
        finally:
            await c.close()

    data = asyncio.run(_run())

    def render(d):
        items = d.get("keys", [])
        if not items:
            return "No keys."
        print_table(
            ["id", "label", "prefix", "created", "expires", "revoked"],
            [(k["id"][:8], k.get("label") or "\u2014", k["key_prefix"],
              k["created_at"], k.get("expires_at") or "\u2014",
              k.get("revoked_at") or "active")
             for k in items],
        )
        return ""

    emit(json_mode=json_mode, data=data, render=render)


@keys.command("rotate")
@click.option("--yes", "-y", is_flag=True)
def keys_rotate(yes):
    """Rotate the active API key. Updates CWD .env atomically."""
    api_key = _require_key()
    if not yes:
        click.confirm(
            "Rotate will invalidate your current key in 60s. Any running "
            "agent process must be restarted to pick up the new key. "
            "Continue?",
            abort=True,
        )

    async def _rotate():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await c.rotate_api_key()
        finally:
            await c.close()

    result = asyncio.run(_rotate())
    new_key = result["api_key"]

    atomic_replace_env_var(Path.cwd() / ".env", "SOTA_API_KEY", new_key)

    async def _verify():
        c = SOTAClient(api_key=new_key, base_url=get_api_url())
        try:
            await c.get_profile()
        finally:
            await c.close()

    asyncio.run(_verify())
    click.echo("Key rotated + .env updated. Restart your agent within 60s.")


@keys.command("create")
@click.option("--label", default=None)
@click.option("--expires-days", default=365, type=int)
def keys_create(label, expires_days):
    """Create an additional API key for this agent. (Requires JWT login.)"""
    creds = _load_creds_here()
    if not creds:
        click.echo("Error: not logged in. Run `sota-agent login`.", err=True)
        raise SystemExit(3)

    api_key = _require_key()

    async def _get_agent_id():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            p = await c.get_profile()
            return p["id"]
        finally:
            await c.close()

    agent_id = asyncio.run(_get_agent_id())

    async def _create():
        c = SOTAClient(api_key="", base_url=get_api_url())
        c.set_jwt(creds["jwt"])
        try:
            return await c.create_api_key(
                agent_id=agent_id, label=label, expires_days=expires_days,
            )
        finally:
            await c.close()

    out = asyncio.run(_create())
    click.echo(
        f"New key created.\n\n  api_key: {out['api_key']}\n\n"
        "Save it now — this is the only time it will be shown."
    )


@keys.command("revoke")
@click.argument("key_id")
@click.option("--yes", "-y", is_flag=True)
def keys_revoke(key_id, yes):
    """Revoke a specific API key (not the one authenticating this call)."""
    api_key = _require_key()
    if not yes:
        click.confirm(f"Revoke key {key_id}?", abort=True)

    async def _run():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await c.revoke_key(key_id)
        finally:
            await c.close()

    out = asyncio.run(_run())
    msg = f"Revoked key {key_id}"
    if out.get("already_revoked"):
        msg += " (already revoked)"
    click.echo(msg)
