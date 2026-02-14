"""C8: Workspace Provisioner — prefix-based sync of ~/.kiro/ from kiro-config/ template."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from tg_acp.config import AGENT_NAME_PATTERN, Config

logger = logging.getLogger(__name__)
_MAX_PREFIX_FILES = 20

# Subdirectories to sync between kiro-config/ template and ~/.kiro/
_MANAGED_SUBDIRS = ("agents", "steering", "skills")


class WorkspaceProvisioner:
    """Syncs ~/.kiro/ with kiro-config/ template using prefix-based matching.

    On every startup: delete all files matching {KIRO_AGENT_NAME}* prefix
    in each managed directory, then copy fresh from the template.
    Files outside the bot's prefix are never touched.
    """

    def __init__(self, config: Config) -> None:
        self._agent_name = config.kiro_agent_name
        self._template_path = Path(config.kiro_config_path)
        self._kiro_home = Path.home() / ".kiro"

    def provision(self) -> None:
        """Run prefix-based sync. Raises RuntimeError on safety check failure."""
        self._safety_checks()

        total_existing = self._count_prefix_files()
        if total_existing > _MAX_PREFIX_FILES:
            raise RuntimeError(
                f"Safety limit exceeded: {total_existing} files match prefix "
                f"{self._agent_name!r} across managed directories (max {_MAX_PREFIX_FILES})"
            )

        for subdir in _MANAGED_SUBDIRS:
            src_dir = self._template_path / subdir
            dst_dir = self._kiro_home / subdir
            dst_dir.mkdir(parents=True, exist_ok=True)
            self._sync_prefix(src_dir, dst_dir)

        # Verify agent config exists after sync
        agent_json = self._kiro_home / "agents" / f"{self._agent_name}.json"
        if not agent_json.is_file():
            raise RuntimeError(
                f"Agent config not found after provisioning: {agent_json}"
            )

        logger.info("Provisioned ~/.kiro/ with prefix %r", self._agent_name)

    def provision_thread_override(
        self, thread_workspace_path: str, agent_config: dict
    ) -> None:
        """Create a local .kiro/agents/{agent_name}.json in a thread directory.

        For per-thread custom steering (rare, on-demand).
        """
        override_dir = Path(thread_workspace_path) / ".kiro" / "agents"
        override_dir.mkdir(parents=True, exist_ok=True)
        override_file = override_dir / f"{self._agent_name}.json"
        override_file.write_text(json.dumps(agent_config, indent=2))
        logger.info("Created thread override at %s", override_file)

    def _safety_checks(self) -> None:
        """Validate preconditions before any delete operation."""
        name = self._agent_name

        if len(name) < 3:
            raise RuntimeError(
                f"KIRO_AGENT_NAME must be >= 3 characters, got: {name!r}"
            )

        if not AGENT_NAME_PATTERN.match(name):
            raise RuntimeError(
                f"KIRO_AGENT_NAME must match ^[a-zA-Z0-9_-]+$, got: {name!r}"
            )

        agent_template = self._template_path / "agents" / f"{name}.json"
        if not agent_template.is_file():
            raise RuntimeError(
                f"Template must contain agent JSON: {agent_template}"
            )

    def _count_prefix_files(self) -> int:
        """Count all entries matching {prefix}* across managed directories."""
        count = 0
        for subdir in _MANAGED_SUBDIRS:
            dst_dir = self._kiro_home / subdir
            if dst_dir.is_dir():
                for entry in dst_dir.iterdir():
                    if entry.name.startswith(self._agent_name):
                        count += 1
        return count

    def _sync_prefix(self, src_dir: Path, dst_dir: Path) -> None:
        """Delete all {prefix}* entries in dst_dir, copy matching from src_dir."""
        prefix = self._agent_name

        # Delete matching entries in destination
        if dst_dir.is_dir():
            for entry in list(dst_dir.iterdir()):
                if entry.name.startswith(prefix):
                    if entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
                    logger.debug("Deleted %s", entry)

        # Copy matching entries from source (with template substitution for .json)
        if src_dir.is_dir():
            for entry in src_dir.iterdir():
                if entry.name.startswith(prefix):
                    dst = dst_dir / entry.name
                    if entry.is_dir():
                        shutil.copytree(entry, dst)
                    elif entry.suffix == ".json":
                        # Substitute {{KIRO_HOME}} placeholder with actual path
                        content = entry.read_text()
                        content = content.replace("{{KIRO_HOME}}", str(self._kiro_home))
                        dst.write_text(content)
                    else:
                        shutil.copy2(entry, dst)
                    logger.debug("Copied %s -> %s", entry, dst)
