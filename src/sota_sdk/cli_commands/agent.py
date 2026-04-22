"""`sota-agent agent` command group — CRUD for user-owned agents."""

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

import click
import yaml

from ..client import SOTAClient
from ..auth import get_api_url
from ..cli_context import (
    resolve_api_key, read_dotenv, write_dotenv,
    atomic_replace_env_var, NoAgentContextError,
)
from ..cli_output import emit, print_table, status_tag


def _load_creds_here() -> dict | None:
    """Local credentials loader that re-resolves ~/.sota/credentials at
    invocation time (so tests monkeypatching $HOME work). See Task 7 notes."""
    path = Path(os.path.expanduser("~/.sota/credentials"))
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _require_login() -> dict:
    creds = _load_creds_here()
    if not creds:
        click.echo("Error: not logged in. Run `sota-agent login`.", err=True)
        raise SystemExit(3)
    return creds


def _new_jwt_client(creds: dict) -> SOTAClient:
    c = SOTAClient(api_key="", base_url=get_api_url())
    c.set_jwt(creds["jwt"])
    return c


@click.group(name="agent")
def agent():
    """Manage agents (list, register, edit, delete, etc.)."""


@agent.command(name="list")
@click.option("--status",
              type=click.Choice([
                  "sandbox", "testing_passed", "pending_review",
                  "active", "suspended", "rejected", "deleted",
              ]))
@click.option("--include-deleted", is_flag=True)
@click.option("--json", "json_mode", is_flag=True,
              help="Machine-readable JSON output.")
def list_cmd(status: str | None, include_deleted: bool, json_mode: bool):
    """List agents owned by you."""
    creds = _require_login()

    async def _run():
        client = _new_jwt_client(creds)
        try:
            return await client.list_agents(
                status=status, include_deleted=include_deleted,
            )
        finally:
            await client.close()

    data = asyncio.run(_run())

    def render(d):
        if not d["agents"]:
            print("No agents.")
            return ""
        print_table(
            ["id", "name", "status", "caps", "last_seen"],
            [
                (a["id"][:8], a["name"], status_tag(a["status"]),
                 ",".join(a["capabilities"]),
                 a["last_seen_at"] or "never")
                for a in d["agents"]
            ],
        )
        return ""

    emit(json_mode=json_mode, data=data, render=render)


@agent.command()
@click.option("--name", required=True)
@click.option("--caps", required=True,
              help="Comma-separated capabilities.")
@click.option("--wallet", required=True,
              help="Solana payout wallet (44-char base58).")
@click.option("--desc", default=None)
@click.option("--webhook", default=None, help="Optional webhook URL.")
def register(name, caps, wallet, desc, webhook):
    """Register a new agent via JWT-auth. Writes creds to ./.env."""
    creds = _require_login()
    cap_list = [c.strip() for c in caps.split(",") if c.strip()]

    async def _run():
        client = _new_jwt_client(creds)
        try:
            return await client.register_agent_authenticated(
                name=name,
                capabilities=cap_list,
                wallet_address=wallet,
                description=desc,
                webhook_url=webhook,
            )
        finally:
            await client.close()

    out = asyncio.run(_run())
    env_path = Path.cwd() / ".env"
    write_dotenv(env_path, {
        "SOTA_API_KEY": out["api_key"],
        "SOTA_WEBHOOK_SECRET": out["webhook_secret"],
        "SOTA_AGENT_ID": out["agent_id"],
        "SOTA_API_URL": get_api_url(),
    })
    click.echo(f"Registered agent {name} (id={out['agent_id']}).")
    click.echo(f"Credentials written to {env_path} (0600).")


@agent.command()
@click.argument("agent_id")
@click.option("--yes", "-y", is_flag=True)
def delete(agent_id, yes):
    """Soft-delete an agent. Revokes all its API keys."""
    creds = _require_login()

    if not yes:
        click.confirm(
            f"Delete agent {agent_id!r}? This revokes all API keys.",
            abort=True,
        )

    async def _run():
        client = _new_jwt_client(creds)
        try:
            return await client.delete_agent(agent_id)
        finally:
            await client.close()

    out = asyncio.run(_run())
    msg = f"Deleted: {out['agent_id']}"
    if out.get("already_deleted"):
        msg += " (already deleted)"
    click.echo(msg)


_EDITABLE_FIELDS = {
    "name", "description", "capabilities",
    "wallet_address", "webhook_url", "icon_url",
}


@agent.command()
@click.argument("agent_id", required=False)
@click.option("--json", "json_mode", is_flag=True)
def show(agent_id, json_mode):
    """Show the profile of an agent. Defaults to CWD's agent."""
    if agent_id:
        # v1 doesn't have per-id show yet (needs GET /agents/{id}).
        click.echo(
            "Error: specifying an agent id is not yet supported. "
            "Run `sota agent show` from the agent's project directory, "
            "or use `sota agent list` to see all owned agents.",
            err=True,
        )
        raise SystemExit(1)

    try:
        api_key = resolve_api_key()
    except NoAgentContextError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    async def _run():
        client = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await client.get_profile()
        finally:
            await client.close()

    profile = asyncio.run(_run())

    def render(p):
        lines = [
            f"name:         {p['name']}",
            f"id:           {p['id']}",
            f"status:       {status_tag(p['status'])}",
            f"capabilities: {', '.join(p['capabilities'])}",
            f"wallet:       {p.get('wallet_address') or '—'}",
            f"description:  {p.get('description') or '—'}",
            f"webhook:      {p.get('webhook_url') or '—'}",
            f"last_seen:    {p.get('last_seen_at') or 'never'}",
        ]
        return "\n".join(lines)

    emit(json_mode=json_mode, data=profile, render=render)


@agent.command("set")
@click.argument("field",
                type=click.Choice(sorted(_EDITABLE_FIELDS)))
@click.argument("value")
@click.option("--yes", "-y", is_flag=True,
              help="Skip re-gate confirmation when changing capabilities.")
def set_field(field, value, yes):
    """Set a single profile field. Changing capabilities re-gates sandbox."""
    try:
        api_key = resolve_api_key()
    except NoAgentContextError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    payload: dict = {}
    if field == "capabilities":
        caps = [c.strip() for c in value.split(",") if c.strip()]
        if not yes:
            click.confirm(
                "Changing capabilities will re-trigger sandbox testing. "
                "Continue?",
                abort=True,
            )
        payload["capabilities"] = caps
    else:
        payload[field] = value

    async def _run():
        client = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await client.update_profile(**payload)
        finally:
            await client.close()

    out = asyncio.run(_run())
    if out.get("sandbox_regate"):
        click.echo(f"{field} updated. Agent re-gated to sandbox.")
    else:
        click.echo(f"{field} updated.")


@agent.command()
@click.argument("agent_id")
@click.option("--yes", "-y", is_flag=True)
def switch(agent_id, yes):
    """Replace CWD .env with the specified agent's credentials.

    v1 limitation: the backend doesn't expose per-agent credentials
    fetch. This command currently errors with guidance — a future
    backend endpoint will make it fully functional.
    """
    _require_login()
    click.echo(
        "Error: `agent switch` is not yet available. The backend needs a "
        "GET /agents/{id}/credentials endpoint before the CLI can swap "
        "credentials locally. For now, cd into the target agent's project "
        "directory (each agent's .env is already there from registration).",
        err=True,
    )
    raise SystemExit(1)


_EDITABLE_KEYS = [
    "name", "description", "capabilities",
    "wallet_address", "webhook_url", "icon_url",
]


def _profile_to_yaml(profile: dict) -> str:
    editable = {k: profile.get(k) for k in _EDITABLE_KEYS}
    header = (
        "# Edit fields and save. Close the editor without changes to abort.\n"
        "# Changing `capabilities` re-triggers sandbox testing.\n\n"
    )
    return header + yaml.safe_dump(editable, sort_keys=False)


@agent.command()
@click.option("--yes", "-y", is_flag=True,
              help="Skip confirmation before re-gating sandbox.")
def edit(yes):
    """Open $EDITOR with the agent's editable fields as YAML."""
    try:
        api_key = resolve_api_key()
    except NoAgentContextError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    async def _get():
        client = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await client.get_profile()
        finally:
            await client.close()

    profile = asyncio.run(_get())

    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False,
    ) as f:
        f.write(_profile_to_yaml(profile))
        temp_path = f.name
    os.chmod(temp_path, 0o600)

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    subprocess.run([editor, temp_path], check=False)

    try:
        with open(temp_path) as fh:
            edited = yaml.safe_load(fh) or {}
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass

    diff = {}
    for k in _EDITABLE_KEYS:
        before = profile.get(k)
        after = edited.get(k)
        if after != before:
            diff[k] = after

    if not diff:
        click.echo("No changes.")
        return

    if "capabilities" in diff and not yes:
        click.confirm(
            "Capability change will re-gate sandbox. Continue?", abort=True,
        )

    async def _patch():
        client = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await client.update_profile(**diff)
        finally:
            await client.close()

    out = asyncio.run(_patch())
    changed = sorted(diff.keys())
    click.echo(f"Updated: {', '.join(changed)}.")
    if out.get("sandbox_regate"):
        click.echo("Agent re-gated to sandbox.")
