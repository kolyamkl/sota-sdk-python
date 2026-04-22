"""Tests for CLI context resolution + atomic .env ops."""

import os
import pytest
from pathlib import Path

from sota_sdk.cli_context import (
    resolve_api_key,
    read_dotenv,
    write_dotenv,
    atomic_replace_env_var,
    NoAgentContextError,
)


class TestResolveApiKey:
    def test_env_var_wins_over_dotenv(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SOTA_API_KEY=from-dotenv\n")
        monkeypatch.setenv("SOTA_API_KEY", "from-env")
        monkeypatch.chdir(tmp_path)
        assert resolve_api_key() == "from-env"

    def test_dotenv_fallback(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SOTA_API_KEY=from-dotenv\n")
        monkeypatch.delenv("SOTA_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        assert resolve_api_key() == "from-dotenv"

    def test_no_key_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SOTA_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(NoAgentContextError):
            resolve_api_key()

    def test_explicit_path_overrides_cwd(self, tmp_path, monkeypatch):
        env_file = tmp_path / "subdir" / ".env"
        env_file.parent.mkdir()
        env_file.write_text("SOTA_API_KEY=from-subdir\n")
        monkeypatch.delenv("SOTA_API_KEY", raising=False)
        assert resolve_api_key(env_file) == "from-subdir"


class TestDotenv:
    def test_read_handles_quoted_values(self, tmp_path):
        (tmp_path / ".env").write_text(
            'SOTA_API_KEY="quoted-key"\nOTHER=plain\nSINGLE=\'single-q\'\n'
        )
        values = read_dotenv(tmp_path / ".env")
        assert values["SOTA_API_KEY"] == "quoted-key"
        assert values["OTHER"] == "plain"
        assert values["SINGLE"] == "single-q"

    def test_read_ignores_comments_and_blank(self, tmp_path):
        (tmp_path / ".env").write_text(
            "# comment\n\nKEY=value\n# another comment\n"
        )
        assert read_dotenv(tmp_path / ".env") == {"KEY": "value"}

    def test_read_missing_file_returns_empty(self, tmp_path):
        assert read_dotenv(tmp_path / "missing.env") == {}

    def test_write_creates_0600_file(self, tmp_path):
        p = tmp_path / ".env"
        write_dotenv(p, {"SOTA_API_KEY": "new-key"})
        assert p.read_text().strip() == "SOTA_API_KEY=new-key"
        assert (p.stat().st_mode & 0o777) == 0o600


class TestAtomicReplaceEnvVar:
    def test_replace_preserves_other_lines_and_order(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text(
            "# sota agent env\nSOTA_API_KEY=old\nOTHER=keep\nANOTHER=x\n",
        )
        atomic_replace_env_var(p, "SOTA_API_KEY", "new")
        lines = p.read_text().splitlines()
        assert lines[0] == "# sota agent env"
        assert "SOTA_API_KEY=new" in lines
        assert "OTHER=keep" in lines
        assert "ANOTHER=x" in lines
        # Backup exists
        assert (p.parent / ".env.bak").exists()
        assert "SOTA_API_KEY=old" in (p.parent / ".env.bak").read_text()

    def test_replace_adds_key_when_missing(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("OTHER=x\n")
        atomic_replace_env_var(p, "SOTA_API_KEY", "new")
        content = p.read_text()
        assert "SOTA_API_KEY=new" in content
        assert "OTHER=x" in content

    def test_replace_creates_file_when_missing(self, tmp_path):
        p = tmp_path / ".env"
        atomic_replace_env_var(p, "SOTA_API_KEY", "new")
        assert p.exists()
        assert "SOTA_API_KEY=new" in p.read_text()

    def test_atomic_replace_rewrites_0600(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("SOTA_API_KEY=old\n")
        atomic_replace_env_var(p, "SOTA_API_KEY", "new")
        assert (p.stat().st_mode & 0o777) == 0o600
