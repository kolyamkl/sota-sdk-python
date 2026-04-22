"""Jobs + bids CLI groups."""

import asyncio
import click

from ..auth import get_api_url
from ..client import SOTAClient
from ..cli_context import resolve_api_key, NoAgentContextError
from ..cli_output import emit, print_table, status_tag


def _require_key() -> str:
    try:
        return resolve_api_key()
    except NoAgentContextError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@click.group()
def jobs():
    """Inspect jobs this agent has bid on or executed."""


@jobs.command("list")
@click.option("--limit", default=50, type=int,
              help="Max jobs to return (Python-side truncation).")
@click.option("--json", "json_mode", is_flag=True,
              help="Machine-readable JSON output.")
def jobs_list(limit, json_mode):
    """List jobs visible to this agent."""
    api_key = _require_key()

    async def _run():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            items = await c.list_jobs()
            return items[:limit] if items else []
        finally:
            await c.close()

    data = asyncio.run(_run())

    def render(d):
        items = d.get("jobs", [])
        if not items:
            return "No jobs."
        print_table(
            ["id", "status", "budget", "description"],
            [(j["id"][:8], status_tag(j.get("status", "?")),
              j.get("budget_usdc", "\u2014"),
              (j.get("description") or "")[:60])
             for j in items],
        )
        return ""

    emit(json_mode=json_mode, data={"jobs": data}, render=render)


@click.command(name="job-show")
@click.argument("job_id")
@click.option("--json", "json_mode", is_flag=True,
              help="Machine-readable JSON output.")
def job_show(job_id, json_mode):
    """Show details for a specific job (v1 stub \u2014 uses list_jobs + filter)."""
    api_key = _require_key()

    async def _run():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            items = await c.list_jobs()
            for j in items:
                if j.get("id") == job_id or j.get("id", "").startswith(job_id):
                    return j
            return None
        finally:
            await c.close()

    job = asyncio.run(_run())
    if job is None:
        click.echo(
            f"Error: job {job_id} not found in agent's job list.",
            err=True,
        )
        raise SystemExit(4)
    emit(
        json_mode=json_mode, data=job,
        render=lambda j: (
            f"id:          {j.get('id')}\n"
            f"status:      {status_tag(j.get('status', '?'))}\n"
            f"description: {j.get('description')}\n"
            f"budget:      {j.get('budget_usdc')} USDC"
        ),
    )


@click.group()
def bids():
    """Inspect bids placed by this agent."""


@bids.command("list")
@click.option("--status",
              type=click.Choice(["pending", "won", "lost"]))
@click.option("--since", default=None,
              help="ISO timestamp; return bids created after this.")
@click.option("--json", "json_mode", is_flag=True,
              help="Machine-readable JSON output.")
def bids_list(status, since, json_mode):
    """List bids placed by this agent."""
    api_key = _require_key()

    async def _run():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await c.list_bids(status=status, since=since)
        finally:
            await c.close()

    data = asyncio.run(_run())

    def render(d):
        if not d.get("bids"):
            return "No bids."
        print_table(
            ["id", "job", "amount", "status", "created"],
            [(b["id"][:8], b["job_id"][:8], b["amount_usdc"],
              status_tag(b["status"]), b["created_at"])
             for b in d["bids"]],
        )
        return ""

    emit(json_mode=json_mode, data=data, render=render)


@click.group("bid")
def bid_ops():
    """Submit / cancel a single bid."""


@bid_ops.command("submit")
@click.argument("job_id")
@click.option("--amount", "amount_usdc", required=True, type=float)
@click.option("--eta", "estimated_seconds", required=True, type=int)
def bid_submit(job_id, amount_usdc, estimated_seconds):
    """Manually submit a bid on a job."""
    api_key = _require_key()

    async def _run():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            return await c.submit_bid(
                job_id=job_id, amount_usdc=amount_usdc,
                estimated_seconds=estimated_seconds,
            )
        finally:
            await c.close()

    out = asyncio.run(_run())
    click.echo(f"Submitted bid {out.get('id', '?')[:8]} on {job_id[:8]}.")


@bid_ops.command("cancel")
@click.argument("bid_id")
@click.option("--yes", "-y", is_flag=True)
def bid_cancel(bid_id, yes):
    """Cancel an in-flight bid. (v1 stub \u2014 backend endpoint pending.)"""
    click.echo(
        "Error: `bid cancel` is not yet available \u2014 the backend doesn't "
        "expose DELETE /agents/bids/{id} yet. Tracked as a follow-up.",
        err=True,
    )
    raise SystemExit(1)
