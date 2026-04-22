"""Runtime observability commands: status, watch, ping, run, logs."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import click
import httpx

from ..auth import get_api_url
from ..client import SOTAClient
from ..cli_context import resolve_api_key, NoAgentContextError
from ..cli_output import emit, status_tag


def _require_key() -> str:
    try:
        return resolve_api_key()
    except NoAgentContextError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@click.command()
@click.option("--json", "json_mode", is_flag=True,
              help="Machine-readable JSON output.")
def status(json_mode):
    """Show current agent status + last seen + capabilities."""
    api_key = _require_key()

    async def _run():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await c.get_profile()
        finally:
            await c.close()

    p = asyncio.run(_run())

    def render(p):
        return (
            f"{p['name']} [{p['id'][:8]}]\n"
            f"  status:       {status_tag(p['status'])}\n"
            f"  capabilities: {', '.join(p['capabilities'])}\n"
            f"  last seen:    {p.get('last_seen_at') or 'never'}\n"
        )

    emit(json_mode=json_mode, data=p, render=render)


@click.command()
@click.option("--interval", "-i", default=5, type=float,
              help="Seconds between polls.")
@click.option("--forever", is_flag=True,
              help="Keep watching even after status settles.")
def watch(interval, forever):
    """Render live status, refreshing every `--interval` seconds."""
    api_key = _require_key()

    async def _tick():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await c.get_profile()
        finally:
            await c.close()

    prev = None
    try:
        while True:
            p = asyncio.run(_tick())
            if p != prev:
                click.clear()
                click.echo(
                    f"{p['name']} \u2014 {status_tag(p['status'])} "
                    f"({p.get('last_seen_at') or 'never'})"
                )
                if not forever and prev and prev["status"] != p["status"]:
                    click.echo("Status changed \u2014 exiting.")
                    return
                prev = p
            time.sleep(interval)
    except KeyboardInterrupt:
        pass


@click.command()
def ping():
    """Verify backend reachability + current API key."""
    api_key = _require_key()
    url = get_api_url()
    try:
        r = httpx.get(f"{url}/api/health", timeout=5.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        click.echo(f"\u2717 backend unreachable: {e}", err=True)
        raise SystemExit(5)

    async def _check_key():
        c = SOTAClient(api_key=api_key, base_url=url)
        try:
            await c.get_profile()
        finally:
            await c.close()

    try:
        asyncio.run(_check_key())
    except Exception as e:
        click.echo(f"\u2717 API key auth failed: {e}", err=True)
        raise SystemExit(3)
    click.echo(f"\u2713 {url} reachable + key valid.")


@click.command()
def run():
    """Run the agent in CWD (python agent.py / npm start auto-detect)."""
    if Path("agent.py").exists():
        sys.exit(subprocess.call([sys.executable, "agent.py"]))
    if Path("package.json").exists():
        sys.exit(subprocess.call(["npm", "start"]))
    click.echo("Error: no agent.py or package.json in CWD.", err=True)
    raise SystemExit(1)


def _render_log_entry(e: dict) -> None:
    level = e.get("level", "info")
    tag = {"info": "INFO", "warn": "WARN", "error": "ERR!"}.get(level, "INFO")
    ts = e.get("created_at", "")
    evtype = e.get("event_type", "?")
    job = e.get("job_id") or ""
    payload = e.get("payload") or {}
    msg = payload.get("message") or payload.get("reason") or ""
    pct = payload.get("percent")
    line = f"{ts} [{tag}] {evtype}"
    if job:
        line += f" job={job[:8]}"
    if pct is not None:
        line += f" {pct}%"
    if msg:
        line += f" \u2014 {msg}"
    click.echo(line)


@click.command()
@click.option("--follow/--no-follow", default=True,
              help="Keep polling for new entries (default on).")
@click.option("--interval", default=2.0, type=float,
              help="Seconds between polls when --follow.")
@click.option("--job", "job_id", default=None,
              help="Filter to a specific job id.")
@click.option("--since", "since_ts", default=None,
              help="ISO timestamp for initial backfill.")
@click.option("--limit", default=200, type=int,
              help="Max entries per poll (1-1000).")
@click.option("--json", "json_mode", is_flag=True,
              help="NDJSON output for piping to jq etc.")
def logs(follow, interval, job_id, since_ts, limit, json_mode):
    """Stream server-side agent events (Tier 1 logs).

    Does NOT show local stdout \u2014 that lives in your agent's terminal.
    """
    api_key = _require_key()

    if not json_mode:
        click.echo(
            "# Server-side events only \u2014 local stdout "
            "(console.log / print) is in your agent terminal."
        )

    since_id: int | None = None

    async def _poll():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await c.get_activity_log(
                since_id=since_id, since_ts=since_ts,
                job_id=job_id, limit=limit,
            )
        finally:
            await c.close()

    try:
        while True:
            page = asyncio.run(_poll())
            for entry in page.get("entries", []):
                if json_mode:
                    print(json.dumps(entry, default=str))
                else:
                    _render_log_entry(entry)
                since_id = entry["id"]
            if page.get("next_since_id") is not None:
                since_id = page["next_since_id"]
            if not follow:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
