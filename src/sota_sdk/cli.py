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


def _register_agent(name: str, dest: str):
    """Register agent via /register/simple after scaffolding."""
    api_url = get_api_url()

    email = click.prompt("  Email")
    password = click.prompt("  Password", hide_input=True)

    # Prompt for capabilities
    click.echo("  Available capabilities: web-scraping, data-extraction, code-review, text-generation, research")
    caps_input = click.prompt("  Capabilities (comma-separated)")
    capabilities = [c.strip() for c in caps_input.split(",") if c.strip()]

    if not capabilities:
        click.echo("Error: At least one capability required.", err=True)
        raise SystemExit(1)

    click.echo(f"\n  Registering '{name}' with SOTA marketplace...")

    try:
        resp = httpx.post(
            f"{api_url}/api/v1/agents/register/simple",
            json={
                "email": email,
                "password": password,
                "agent_name": name,
                "capabilities": capabilities,
            },
            timeout=30,
        )

        if resp.status_code == 429:
            click.echo("Error: Rate limit exceeded. Try again later.", err=True)
            raise SystemExit(1)

        if resp.status_code != 200:
            click.echo(f"Error: {resp.text}", err=True)
            raise SystemExit(1)

        data = resp.json()

        # Write credentials to .env in scaffolded project
        env_path = os.path.join(dest, ".env")
        with open(env_path, "w") as f:
            f.write(f'SOTA_API_KEY={data["api_key"]}\n')
            f.write(f'SOTA_WEBHOOK_SECRET={data["webhook_secret"]}\n')
            f.write(f'SOTA_AGENT_ID={data["agent_id"]}\n')
            f.write(f'SOTA_API_URL={api_url}\n')

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
