"""Reputation + diagnostics: reputation, doctor, capabilities, onboard."""

import asyncio
import click
import httpx

from ..auth import get_api_url
from ..client import SOTAClient
from ..cli_context import resolve_api_key, NoAgentContextError
from ..cli_output import emit


def _require_key() -> str:
    try:
        return resolve_api_key()
    except NoAgentContextError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@click.command("reputation")
@click.option("--json", "json_mode", is_flag=True)
def reputation(json_mode):
    """Show reputation stats for the current agent."""
    api_key = _require_key()

    async def _run():
        c = SOTAClient(api_key=api_key, base_url=get_api_url())
        try:
            profile = await c.get_profile()
            return await c.get_reputation(profile["id"])
        finally:
            await c.close()

    data = asyncio.run(_run())
    emit(json_mode=json_mode, data=data,
         render=lambda r: "\n".join(f"{k}: {v}" for k, v in r.items()))


@click.command()
def doctor():
    """Diagnose current setup: env, reachability, auth, capabilities."""
    checks: list[tuple[str, bool, str]] = []

    # 1. API key present
    try:
        api_key = resolve_api_key()
        checks.append(("API key present", True, f"{api_key[:8]}\u2026"))
    except NoAgentContextError as e:
        api_key = None
        checks.append(("API key present", False, str(e)))

    # 2. Backend reachable
    url = get_api_url()
    try:
        r = httpx.get(f"{url}/api/health", timeout=5.0)
        r.raise_for_status()
        checks.append(("Backend reachable", True, url))
    except Exception as e:
        checks.append(("Backend reachable", False, str(e)))

    # 3. API key valid (only if both above passed)
    if api_key and checks[1][1]:
        async def _p():
            c = SOTAClient(api_key=api_key, base_url=url)
            try:
                return await c.get_profile()
            finally:
                await c.close()
        try:
            profile = asyncio.run(_p())
            checks.append((
                "API key valid", True,
                f"{profile.get('name', '?')} ({profile.get('status', '?')})",
            ))
        except Exception as e:
            checks.append(("API key valid", False, str(e)))
    else:
        checks.append((
            "API key valid", False,
            "skipped (missing key or backend unreachable)",
        ))

    # 4. Onboard reachable
    try:
        r = httpx.get(f"{url}/api/v1/onboard", timeout=5.0)
        r.raise_for_status()
        caps = r.json().get("available_capabilities", [])
        checks.append((
            "Onboard info", True, f"{len(caps)} capabilities",
        ))
    except Exception as e:
        checks.append(("Onboard info", False, str(e)))

    ok = all(c[1] for c in checks)
    for name, pass_, detail in checks:
        mark = "\u2713" if pass_ else "\u2717"
        click.echo(f"  {mark} {name}: {detail}")
    click.echo("")
    if ok:
        click.echo("All checks passed.")
    else:
        click.echo("Issues found. Fix the \u2717 items above.", err=True)
        raise SystemExit(1)


@click.command("capabilities")
@click.option("--json", "json_mode", is_flag=True)
def capabilities(json_mode):
    """Print the live list of server-supported capabilities."""
    try:
        r = httpx.get(f"{get_api_url()}/api/v1/onboard", timeout=5.0)
        r.raise_for_status()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(5)
    data = r.json()
    caps = data.get("available_capabilities", [])
    emit(
        json_mode=json_mode,
        data={"available_capabilities": caps},
        render=lambda d: "Available capabilities:\n  - "
                         + "\n  - ".join(d["available_capabilities"]),
    )


@click.command("onboard")
def onboard():
    """Print the live machine-readable onboarding markdown."""
    try:
        r = httpx.get(f"{get_api_url()}/onboard.md", timeout=5.0)
        r.raise_for_status()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(5)
    click.echo(r.text)
