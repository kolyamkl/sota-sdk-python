"""Sandbox gate + review commands: sandbox status/retry, review request/status."""

import asyncio
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


@click.group()
def sandbox():
    """Sandbox gate commands."""


@sandbox.command("status")
@click.option("--json", "json_mode", is_flag=True)
def sandbox_status(json_mode):
    """Show current sandbox status."""
    api_key = _require_key()

    async def _run():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await c.get_profile()
        finally:
            await c.close()

    p = asyncio.run(_run())

    def render(p):
        out = (
            f"status: {status_tag(p['status'])}\n"
            f"capabilities: {', '.join(p['capabilities'])}"
        )
        if p['status'] == 'sandbox':
            out += (
                "\n\nAgent is in sandbox — complete test jobs to unlock review."
            )
        return out

    emit(json_mode=json_mode, data=p, render=render)


@sandbox.command("retry")
@click.argument("test_job_id")
def sandbox_retry(test_job_id):
    """Retry a failed sandbox test job."""
    api_key = _require_key()

    async def _run():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await c.retry_test_job(test_job_id)
        finally:
            await c.close()

    asyncio.run(_run())
    click.echo(f"Retry requested for test job {test_job_id}.")


@click.group()
def review():
    """Admin review gate commands."""


@review.command("request")
def review_request():
    """Request admin review."""
    api_key = _require_key()
    r = httpx.post(
        f"{get_api_url()}/api/v1/agents/request-review",
        headers={"X-API-Key": api_key},
        timeout=10.0,
    )
    if r.status_code >= 400:
        click.echo(f"Error: {r.status_code} {r.text}", err=True)
        raise SystemExit(1)
    data = r.json()
    click.echo(f"Review requested for agent {data.get('agent_id', '')}.")


@review.command("status")
@click.option("--json", "json_mode", is_flag=True)
def review_status(json_mode):
    """Show current review status + rejection reason if any."""
    api_key = _require_key()

    async def _run():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await c.get_profile()
        finally:
            await c.close()

    p = asyncio.run(_run())
    emit(json_mode=json_mode, data=p,
         render=lambda p: f"status: {status_tag(p['status'])}")
