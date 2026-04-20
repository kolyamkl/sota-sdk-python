"""Tests for SOTA SDK CLI scaffolding."""
import os

from click.testing import CliRunner

from sota_sdk.cli import main


class TestCLIInit:
    def test_init_creates_project(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, ["init", "test-agent"])
            assert result.exit_code == 0
            assert os.path.isdir("test-agent")

            # Check all expected files exist
            expected_files = [
                "agent.py",
                "Dockerfile",
                "docker-compose.yml",
                ".env.example",
                "requirements.txt",
                "README.md",
            ]
            for fname in expected_files:
                path = os.path.join("test-agent", fname)
                assert os.path.isfile(path), f"Missing: {fname}"

    def test_init_replaces_template_vars(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, ["init", "my-cool-agent"])
            assert result.exit_code == 0

            # Check agent.py contains the name
            with open("my-cool-agent/agent.py") as f:
                content = f.read()
            assert "my-cool-agent" in content
            assert "{{AGENT_NAME}}" not in content

            # Check docker-compose.yml
            with open("my-cool-agent/docker-compose.yml") as f:
                content = f.read()
            assert "my-cool-agent" in content

    def test_init_fails_if_dir_exists(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            os.makedirs("existing-dir")
            result = runner.invoke(main, ["init", "existing-dir"])
            assert result.exit_code != 0
            assert "already exists" in result.output or "already exists" in (
                result.stderr or ""
            )

    def test_init_shows_next_steps(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, ["init", "test-agent"])
            assert "cd test-agent" in result.output
            assert "pip install" in result.output
            assert ".env" in result.output
