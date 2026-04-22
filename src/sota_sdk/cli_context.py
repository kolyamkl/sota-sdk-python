"""CLI context resolution + .env read/write helpers.

`resolve_api_key()` implements the spec's lookup order:
  1. SOTA_API_KEY env var
  2. SOTA_API_KEY from CWD .env (or an explicit path)
  3. raise NoAgentContextError
"""

import os
import shutil
from pathlib import Path


class NoAgentContextError(Exception):
    """Raised when no API key is resolvable from any source."""


def read_dotenv(path: Path | str = ".env") -> dict[str, str]:
    """Parse KEY=VALUE lines from a .env file. Strips surrounding quotes.

    Comments (lines starting with #) and blank lines are ignored.
    """
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        out[key.strip()] = value
    return out


def write_dotenv(path: Path | str, values: dict[str, str]) -> None:
    """Write a .env file with 0600 perms. Overwrites without backup.

    Use atomic_replace_env_var when updating a single key on an existing
    file — it preserves order, comments, and backs up the previous version.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(f"{k}={v}" for k, v in values.items()) + "\n"
    p.write_text(content)
    os.chmod(p, 0o600)


def atomic_replace_env_var(
    path: Path | str, key: str, new_value: str,
) -> None:
    """Replace (or add) a single key in the given .env file atomically.

    - Preserves all other lines, comments, and order.
    - Backs up the previous version as `.env.bak` in the same directory
      (single generation — overwritten on subsequent calls).
    - Writes to a `.tmp` file and renames for atomicity.
    - Resulting file is 0600.
    """
    p = Path(path)
    # Normalize backup path. For a dotfile like .env we want .env.bak
    # (not ..env.bak); for plain names we want name.bak.
    if p.name == ".env":
        backup = p.parent / ".env.bak"
    elif p.name.startswith("."):
        backup = p.parent / (p.name + ".bak")
    else:
        backup = p.parent / f"{p.name}.bak"

    if p.exists():
        shutil.copy2(p, backup)

    existing_lines = p.read_text().splitlines() if p.exists() else []
    out_lines: list[str] = []
    replaced = False
    for line in existing_lines:
        if line.strip().startswith(f"{key}="):
            out_lines.append(f"{key}={new_value}")
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        out_lines.append(f"{key}={new_value}")

    tmp = p.parent / (p.name + ".tmp")
    tmp.write_text("\n".join(out_lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def resolve_api_key(dotenv_path: Path | str = ".env") -> str:
    """Return the agent API key from env > .env, or raise."""
    env_key = os.environ.get("SOTA_API_KEY")
    if env_key:
        return env_key
    dotenv_values = read_dotenv(dotenv_path)
    if "SOTA_API_KEY" in dotenv_values:
        return dotenv_values["SOTA_API_KEY"]
    raise NoAgentContextError(
        "No SOTA_API_KEY found in env or .env. "
        "Run `sota-agent login` + `sota-agent agent register` first."
    )
