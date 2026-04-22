"""Identity commands: logout, whoami, version."""

import json
import os
from pathlib import Path

import click


def _credentials_path() -> Path:
    """Canonical location of the local credentials file.

    Re-expanded on every call so tests that monkeypatch ``$HOME`` see the
    override (unlike ``auth.CREDENTIALS_FILE`` which is frozen at import).
    """
    return Path(os.path.expanduser("~/.sota/credentials"))


def _load_creds_here() -> dict | None:
    """Read credentials from the runtime-resolved path.

    Mirrors ``auth.load_credentials`` but resolves ``~`` at call time so
    HOME-based sandboxing in tests takes effect.
    """
    path = _credentials_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


@click.command()
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
def logout(yes: bool):
    """Revoke local credentials (delete ~/.sota/credentials)."""
    path = _credentials_path()
    if not path.exists():
        click.echo("Already logged out.")
        return
    if not yes:
        click.confirm(f"Delete credentials at {path}?", abort=True)
    path.unlink()
    click.echo(f"Logged out. Removed {path}.")


@click.command()
def whoami():
    """Print the email of the currently logged-in user."""
    creds = _load_creds_here()
    if not creds:
        click.echo("Error: not logged in. Run `sota-agent login`.", err=True)
        raise SystemExit(3)
    email = creds.get("email", "unknown")
    click.echo(f"Logged in as {email}")


@click.command()
def version():
    """Print SDK version."""
    try:
        # Prefer importlib.metadata for the authoritative version
        from importlib.metadata import version as _v
        pkg_version = _v("sota-sdk")
    except Exception:
        try:
            import sota_sdk
            pkg_version = getattr(sota_sdk, "__version__", "unknown")
        except Exception:
            pkg_version = "unknown"
    click.echo(f"sota-agent (sota-sdk) {pkg_version}")
