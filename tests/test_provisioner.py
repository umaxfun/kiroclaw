"""Unit tests for C8 Workspace Provisioner — BR-03, BR-04 rules.

Uses a temporary directory structure instead of real ~/.kiro/.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tg_acp.config import Config
from tg_acp.provisioner import WorkspaceProvisioner


@pytest.fixture
def setup_dirs(tmp_path: Path):
    """Create template and fake ~/.kiro/ directory structures."""
    template = tmp_path / "kiro-config"
    kiro_home = tmp_path / "fake-kiro-home"

    # Create template with agent config
    (template / "agents").mkdir(parents=True)
    (template / "steering").mkdir(parents=True)
    (template / "skills").mkdir(parents=True)
    (template / "agents" / "tg-acp.json").write_text(
        json.dumps({"name": "tg-acp", "model": "auto"})
    )

    return template, kiro_home


def _make_config(template: Path, agent_name: str = "tg-acp") -> Config:
    """Create a Config pointing to the test template."""
    return Config(
        bot_token="test",
        workspace_base_path="./workspaces/",
        max_processes=5,
        idle_timeout_seconds=30,
        kiro_agent_name=agent_name,
        log_level="INFO",
        kiro_config_path=str(template),
    )


class TestProvisionerSafety:
    """BR-03: Provisioner safety guardrails."""

    def test_safety_limit_exceeded(self, setup_dirs):
        """BR-03.3: Abort if > 20 files match prefix."""
        template, kiro_home = setup_dirs
        config = _make_config(template)
        provisioner = WorkspaceProvisioner(config)

        # Patch home to use fake kiro home
        with patch.object(Path, "home", return_value=kiro_home.parent):
            provisioner._kiro_home = kiro_home

        # Create 21 matching files in agents/
        agents_dir = kiro_home / "agents"
        agents_dir.mkdir(parents=True)
        for i in range(21):
            (agents_dir / f"tg-acp-extra-{i}.json").write_text("{}")

        with pytest.raises(RuntimeError, match="Safety limit exceeded"):
            provisioner.provision()

    def test_empty_template_rejected(self, setup_dirs):
        """BR-03.4: Refuse to sync if template doesn't contain agent JSON."""
        template, kiro_home = setup_dirs
        # Remove the agent JSON from template
        (template / "agents" / "tg-acp.json").unlink()

        config = _make_config(template)
        provisioner = WorkspaceProvisioner(config)
        provisioner._kiro_home = kiro_home

        with pytest.raises(RuntimeError, match="Template must contain agent JSON"):
            provisioner.provision()

    def test_short_agent_name_rejected(self, setup_dirs):
        """BR-03 safety: agent name < 3 chars rejected."""
        template, kiro_home = setup_dirs
        # Create a template for the short name (won't matter — safety check first)
        (template / "agents" / "ab.json").write_text("{}")

        config = _make_config(template, agent_name="ab")
        provisioner = WorkspaceProvisioner(config)
        provisioner._kiro_home = kiro_home

        with pytest.raises(RuntimeError, match=">= 3 characters"):
            provisioner.provision()

    def test_invalid_agent_name_rejected(self, setup_dirs):
        """BR-03 safety: agent name with special chars rejected."""
        template, kiro_home = setup_dirs

        for bad_name in ["tg.acp", "tg/acp", "tg*acp"]:
            config = _make_config(template, agent_name=bad_name)
            provisioner = WorkspaceProvisioner(config)
            provisioner._kiro_home = kiro_home

            with pytest.raises(RuntimeError, match="must match"):
                provisioner.provision()


class TestProvisionerSync:
    """BR-04: Provisioner sync behavior."""

    def test_sync_creates_and_copies(self, setup_dirs):
        """BR-04.1/BR-04.2: Sync copies template files to target."""
        template, kiro_home = setup_dirs
        config = _make_config(template)
        provisioner = WorkspaceProvisioner(config)
        provisioner._kiro_home = kiro_home

        provisioner.provision()

        agent_file = kiro_home / "agents" / "tg-acp.json"
        assert agent_file.is_file()
        data = json.loads(agent_file.read_text())
        assert data["name"] == "tg-acp"

    def test_sync_replaces_existing(self, setup_dirs):
        """BR-04.2: Sync is delete-then-copy, not merge."""
        template, kiro_home = setup_dirs
        config = _make_config(template)
        provisioner = WorkspaceProvisioner(config)
        provisioner._kiro_home = kiro_home

        # First sync
        provisioner.provision()

        # Modify the target file
        agent_file = kiro_home / "agents" / "tg-acp.json"
        agent_file.write_text(json.dumps({"name": "modified"}))

        # Second sync — should replace with template version
        provisioner.provision()

        data = json.loads(agent_file.read_text())
        assert data["name"] == "tg-acp"  # restored from template

    def test_sync_deletes_extra_prefix_files(self, setup_dirs):
        """BR-04.2: Extra files matching prefix are deleted."""
        template, kiro_home = setup_dirs
        config = _make_config(template)
        provisioner = WorkspaceProvisioner(config)
        provisioner._kiro_home = kiro_home

        # First sync
        provisioner.provision()

        # Add an extra file matching the prefix
        extra = kiro_home / "agents" / "tg-acp-old-steering.json"
        extra.write_text("{}")

        # Second sync — extra file should be deleted
        provisioner.provision()

        assert not extra.exists()

    def test_no_collateral_damage(self, setup_dirs):
        """BR-03.1/BR-03.2: Files without prefix are never touched."""
        template, kiro_home = setup_dirs
        config = _make_config(template)
        provisioner = WorkspaceProvisioner(config)
        provisioner._kiro_home = kiro_home

        # Create a non-matching file in agents/
        agents_dir = kiro_home / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        other_agent = agents_dir / "other-agent.json"
        other_agent.write_text(json.dumps({"name": "other"}))

        provisioner.provision()

        # Non-matching file should still exist
        assert other_agent.is_file()
        data = json.loads(other_agent.read_text())
        assert data["name"] == "other"

    def test_sync_covers_three_dirs(self, setup_dirs):
        """BR-04.3: Sync covers agents/, steering/, skills/."""
        template, kiro_home = setup_dirs

        # Add steering and skills files to template
        (template / "steering" / "tg-acp-rules.md").write_text("# Rules")
        (template / "skills" / "tg-acp-skill.md").write_text("# Skill")

        config = _make_config(template)
        provisioner = WorkspaceProvisioner(config)
        provisioner._kiro_home = kiro_home

        provisioner.provision()

        assert (kiro_home / "agents" / "tg-acp.json").is_file()
        assert (kiro_home / "steering" / "tg-acp-rules.md").is_file()
        assert (kiro_home / "skills" / "tg-acp-skill.md").is_file()

    def test_prefix_match_is_case_sensitive(self, setup_dirs):
        """BR-04.5: Prefix match is case-sensitive."""
        template, kiro_home = setup_dirs
        config = _make_config(template)
        provisioner = WorkspaceProvisioner(config)
        provisioner._kiro_home = kiro_home

        # Create a file with different case
        agents_dir = kiro_home / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        upper_file = agents_dir / "TG-ACP-upper.json"
        upper_file.write_text("{}")

        provisioner.provision()

        # Upper-case file should NOT be deleted (prefix is "tg-acp", not "TG-ACP")
        assert upper_file.is_file()
