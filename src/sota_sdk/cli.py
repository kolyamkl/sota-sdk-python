"""CLI for SOTA Agent SDK: project scaffolding, auth, and review (D-11)."""
import os
import json

import click
import httpx
from importlib import resources

from .auth import (
    device_code_login,
    load_credentials,
    save_credentials,
    get_api_url,
)
from .cli_commands.identity import logout, whoami, version
from .cli_commands.agent import agent as agent_group
from .cli_commands.runtime import status, watch, ping, run as run_cmd, logs
from .cli_commands.jobs_bids import jobs, bids, bid_ops, job_show
from .cli_commands.sandbox import sandbox, review
from .cli_commands.keys import keys as keys_group
from .cli_commands.reputation_diag import (
    reputation, doctor, capabilities, onboard,
)
from .cli_commands.webhook import webhook


@click.group()
def main():
    """SOTA Agent SDK CLI."""
    pass


@main.command()
def login():
    """Authenticate via device-code flow (D-11: sota-agent login)."""
    click.echo("Starting device-code authentication...")
    try:
        creds = device_code_login()
        click.echo(f"\n  Authenticated as: {creds['email']}")
        click.echo(f"  Credentials saved to ~/.sota/credentials")
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@main.command()
@click.argument("name")
@click.option("--register", is_flag=True, help="Register agent with SOTA marketplace")
def init(name: str, register: bool):
    """Scaffold a new SOTA agent project (D-11: sota-agent init <name> --register)."""
    dest = os.path.join(os.getcwd(), name)
    if os.path.exists(dest):
        click.echo(f"Error: Directory '{name}' already exists.", err=True)
        raise SystemExit(1)

    os.makedirs(dest, exist_ok=True)
    template_dir = resources.files("sota_sdk") / "templates"

    for tpl in template_dir.iterdir():
        if not hasattr(tpl, "read_text"):
            continue
        content = tpl.read_text()
        content = content.replace("{{AGENT_NAME}}", name)
        content = content.replace(
            "{{AGENT_NAME_UPPER}}", name.upper().replace("-", "_")
        )
        out_name = tpl.name.replace(".tpl", "")
        with open(os.path.join(dest, out_name), "w") as f:
            f.write(content)

    click.echo(f"Agent project '{name}' created at ./{name}/")

    if register:
        _register_agent(name, dest)
    else:
        click.echo("Next steps:")
        click.echo(f"  cd {name}")
        click.echo("  pip install -r requirements.txt")
        click.echo("  cp .env.example .env  # Add your SOTA API key")
        click.echo("  python agent.py")


import re

# Base58 alphabet, 32–44 chars. Matches the shape of a Solana pubkey before
# the server does the real base58-decode + 32-byte check via solders.Pubkey.
_SOLANA_PUBKEY_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _register_agent(name: str, dest: str):
    """Register agent after scaffolding.

    If the user has already logged in (``~/.sota/credentials`` exists),
    use the authenticated ``/register`` endpoint so one account can own
    many agents. Otherwise fall back to ``/register/simple`` which
    provisions the Supabase user in the same call.
    """
    api_url = get_api_url()

    creds = load_credentials()
    authed = creds is not None

    if authed:
        click.echo(f"  Using saved login: {creds.get('email')}")
        email = ""
        password = ""
    else:
        email = click.prompt("  Email")
        password = click.prompt("  Password", hide_input=True)

    # Prompt for capabilities
    click.echo("  Available capabilities: web-scraping, data-extraction, code-review")
    caps_input = click.prompt("  Capabilities (comma-separated)")
    capabilities = [c.strip() for c in caps_input.split(",") if c.strip()]

    if not capabilities:
        click.echo("Error: At least one capability required.", err=True)
        raise SystemExit(1)

    description = click.prompt(
        "  Description (one sentence, optional)", default="", show_default=False
    ).strip() or None

    while True:
        wallet_address = click.prompt(
            "  Solana wallet address (for payouts, required)"
        ).strip()
        if _SOLANA_PUBKEY_RE.match(wallet_address):
            break
        click.echo("  Not a valid Solana pubkey. Expected base58, 32–44 chars.", err=True)

    min_fee_raw = click.prompt(
        "  Minimum fee in USDC", default="1", show_default=True
    ).strip()
    try:
        min_fee = float(min_fee_raw) if min_fee_raw else 1.0
        if min_fee < 0:
            raise ValueError
    except ValueError:
        click.echo("Error: min_fee must be a non-negative number.", err=True)
        raise SystemExit(1)

    click.echo(f"\n  Registering '{name}' with SOTA marketplace...")

    if authed:
        endpoint = "/api/v1/agents/register"
        headers = {"Authorization": f"Bearer {creds['access_token']}"}
        payload = {
            "name": name,
            "capabilities": capabilities,
            "description": description,
            "wallet_address": wallet_address,
            "min_fee": min_fee,
        }
    else:
        endpoint = "/api/v1/agents/register/simple"
        headers = {}
        payload = {
            "email": email,
            "password": password,
            "agent_name": name,
            "capabilities": capabilities,
            "description": description,
            "wallet_address": wallet_address,
            "min_fee": min_fee,
        }

    try:
        resp = httpx.post(
            f"{api_url}{endpoint}",
            json=payload,
            headers=headers,
            timeout=30,
        )

        if resp.status_code == 429:
            click.echo("Error: Rate limit exceeded. Try again later.", err=True)
            raise SystemExit(1)

        if resp.status_code != 200:
            click.echo(f"Error: {resp.text}", err=True)
            raise SystemExit(1)

        data = resp.json()

        # Fetch Supabase + API URLs from the backend's public config so the
        # scaffolded .env is ready to run (no "what URL goes here?" step).
        # Best-effort: if the config endpoint fails, we still write the
        # agent-specific values and tell the dev to fill the rest in.
        dev_cfg: dict = {}
        try:
            cfg_resp = httpx.get(f"{api_url}/api/v1/developer/config", timeout=10)
            if cfg_resp.status_code == 200:
                dev_cfg = cfg_resp.json()
        except httpx.RequestError:
            pass

        # Write credentials to .env in scaffolded project
        env_path = os.path.join(dest, ".env")
        with open(env_path, "w") as f:
            f.write(f'SOTA_API_KEY={data["api_key"]}\n')
            f.write(f'SOTA_WEBHOOK_SECRET={data["webhook_secret"]}\n')
            f.write(f'SOTA_AGENT_ID={data["agent_id"]}\n')
            f.write(f'SOTA_API_URL={api_url}\n')
            if dev_cfg.get("supabase_url"):
                f.write(f'SUPABASE_URL={dev_cfg["supabase_url"]}\n')
            if dev_cfg.get("supabase_anon_key"):
                f.write(f'SUPABASE_ANON_KEY={dev_cfg["supabase_anon_key"]}\n')

        click.echo(f"\n  Agent '{name}' registered! (sandbox mode)")
        click.echo(f"  Agent ID: {data['agent_id']}")
        click.echo(f"  API key written to .env")
        click.echo(f"  Webhook secret written to .env")
        click.echo(f"  Complete 3 test jobs to request marketplace approval")
        click.echo(f"\nNext steps:")
        click.echo(f"  cd {name}")
        click.echo(f"  pip install -r requirements.txt")
        click.echo(f"  python agent.py  # starts agent, receives test jobs")

    except httpx.RequestError as e:
        click.echo(f"Error: Could not connect to SOTA API at {api_url}: {e}", err=True)
        raise SystemExit(1)


@main.command("config")
@click.option("--write", "env_path", help="Append to this .env file instead of printing")
def config(env_path: str | None):
    """Print (or append to .env) the SDK config needed to connect to SOTA.

    Fetches SOTA_API_URL, SUPABASE_URL, SUPABASE_ANON_KEY from the
    backend's /api/v1/developer/config endpoint. Use after `sota-agent
    init` when you need to fill in the Supabase values for an existing
    project.
    """
    api_url = get_api_url()
    try:
        resp = httpx.get(f"{api_url}/api/v1/developer/config", timeout=10)
    except httpx.RequestError as e:
        click.echo(f"Error: Could not reach SOTA API at {api_url}: {e}", err=True)
        raise SystemExit(1)
    if resp.status_code != 200:
        click.echo(f"Error: {resp.text}", err=True)
        raise SystemExit(1)

    cfg = resp.json()
    lines = [
        f'SOTA_API_URL={cfg["api_url"]}',
        f'SUPABASE_URL={cfg["supabase_url"]}',
        f'SUPABASE_ANON_KEY={cfg["supabase_anon_key"]}',
    ]

    if env_path:
        # Append, don't clobber — caller might have SOTA_API_KEY etc.
        with open(env_path, "a") as f:
            f.write("\n# SOTA developer config\n")
            f.write("\n".join(lines) + "\n")
        click.echo(f"  Config appended to {env_path}")
    else:
        for line in lines:
            click.echo(line)


@main.command("request-review")
def request_review():
    """Request admin review after passing all test jobs (D-11)."""
    creds = load_credentials()
    if not creds:
        click.echo("Error: Not logged in. Run 'sota-agent login' first.", err=True)
        raise SystemExit(1)

    # Need agent API key from .env or credentials
    api_key = os.environ.get("SOTA_API_KEY")
    if not api_key:
        # Try reading from current directory .env
        env_path = os.path.join(os.getcwd(), ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("SOTA_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        break

    if not api_key:
        click.echo("Error: No API key found. Set SOTA_API_KEY or run from agent project directory.", err=True)
        raise SystemExit(1)

    api_url = get_api_url()
    try:
        resp = httpx.post(
            f"{api_url}/api/v1/agents/request-review",
            headers={"X-API-Key": api_key},
            timeout=30,
        )

        if resp.status_code == 200:
            data = resp.json()
            click.echo(f"\n  Review requested for agent {data.get('agent_id', '')}")
            click.echo("  An admin will review your agent's test results.")
            click.echo("  You'll be notified when a decision is made.")
        else:
            click.echo(f"Error: {resp.text}", err=True)
            raise SystemExit(1)

    except httpx.RequestError as e:
        click.echo(f"Error: Could not connect to SOTA API: {e}", err=True)
        raise SystemExit(1)


# Identity group: logout, whoami, version
main.add_command(logout)
main.add_command(whoami)
main.add_command(version)

# Agent group: list, register, delete, show, set, switch, edit
main.add_command(agent_group)

# Runtime observability commands: status, watch, ping, run, logs
main.add_command(status)
main.add_command(watch)
main.add_command(ping)
main.add_command(run_cmd, name="run")
main.add_command(logs)

# Jobs + bids groups
main.add_command(jobs)
main.add_command(bids)
main.add_command(bid_ops, name="bid")
main.add_command(job_show, name="job")

# Sandbox + review groups
main.add_command(sandbox)
main.add_command(review)

# Keys group: list, rotate, create, revoke
main.add_command(keys_group)

# Reputation + diagnostics: reputation, doctor, capabilities, onboard
main.add_command(reputation)
main.add_command(doctor)
main.add_command(capabilities)
main.add_command(onboard)

# Webhook helpers: verify + test
main.add_command(webhook)
