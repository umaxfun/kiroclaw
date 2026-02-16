# Architectural Proposal: Per-User Add-on Installation via KiroHub

## Executive Summary

This document proposes an architecture to enable **per-user add-on installation** in KiroClaw, allowing Telegram users to discover, install, and manage Kiro skills (add-ons) directly from chat using the KiroHub registry (`kirohub.dev`, accessed via `npx kirohub`).

### Key Goals

1. **Per-User Isolation**: Each Telegram user gets their own skill directory, preventing conflicts and enabling personalized tool sets
2. **Chat-Native Installation**: Users can install add-ons via bot commands (e.g., `/addon install weather-api`)
3. **KiroHub Integration**: Leverage the KiroHub registry for discovery, versioning, and distribution
4. **Zero Admin Intervention**: Users manage their own add-ons without requiring bot redeployment
5. **Backward Compatibility**: Existing global skills remain available to all users

---

## Current Architecture Analysis

### Existing Structure

```
~/.kiro/
├── agents/       # Agent config (shared, global)
├── skills/       # Skills (shared, global) ← Current limitation
└── steering/     # Steering files (shared, global)
```

### Current Limitations

1. **Global Skills Only**: All skills in `~/.kiro/skills/` are available to all users
2. **Manual Installation**: Admin must manually copy skills to `kiro-config/skills/` and redeploy
3. **No Discovery Mechanism**: Users cannot browse or search for available add-ons
4. **No Versioning**: Skills are static files with no version management
5. **Thread-Level Workspaces**: While workspaces are per-thread (`./workspaces/{user_id}/{thread_id}/`), skills are still global

---

## Proposed Architecture

### 1. Per-User Skill Directories

Extend the workspace structure to include user-specific skill storage:

```
./workspaces/
└── {user_id}/
    ├── .kiro/
    │   └── skills/           # User-specific skills
    │       ├── weather-api/  # Installed via /addon install
    │       ├── web-search/
    │       └── ...
    ├── addons.json           # User's add-on registry (installed, versions, metadata)
    └── {thread_id}/          # Existing thread workspaces
        └── ...
```

### 2. Skill Resolution Order

When Kiro CLI loads skills for a session, use this precedence:

1. **User Skills** (`./workspaces/{user_id}/.kiro/skills/`) — highest priority
2. **Global Skills** (`~/.kiro/skills/`) — fallback for built-in/default skills

Implementation: Set `KIRO_HOME` environment variable per-user when spawning `kiro-cli`:

```python
# Before: Global KIRO_HOME
env = {"KIRO_HOME": str(Path.home() / ".kiro")}

# After: User-specific KIRO_HOME overlay
user_kiro_home = Path(workspace_base) / str(user_id) / ".kiro"
user_kiro_home.mkdir(parents=True, exist_ok=True)
env = {
    "KIRO_HOME": str(user_kiro_home),
    "KIRO_GLOBAL_HOME": str(Path.home() / ".kiro"),  # Fallback for agents/steering
}
```

**Note**: Kiro CLI would need to be updated to support `KIRO_GLOBAL_HOME` for fallback resolution, OR we can symlink global directories into user-specific directories.

### 3. KiroHub Integration Module

Create `src/tg_acp/kirohub_client.py` to interact with KiroHub:

```python
class KiroHubClient:
    """Client for interacting with KiroHub registry via npx kirohub."""
    
    async def search(self, query: str) -> list[AddonMetadata]:
        """Search KiroHub for add-ons matching query."""
        result = await run_command(["npx", "kirohub", "search", query])
        return parse_addon_list(result)
    
    async def install(
        self, 
        addon_name: str, 
        user_skills_dir: Path,
        version: str | None = None
    ) -> AddonMetadata:
        """Install add-on to user's skill directory."""
        cmd = ["npx", "kirohub", "install", addon_name]
        if version:
            cmd.extend(["--version", version])
        cmd.extend(["--target", str(user_skills_dir)])
        
        result = await run_command(cmd)
        return parse_addon_metadata(result)
    
    async def uninstall(self, addon_name: str, user_skills_dir: Path) -> None:
        """Remove add-on from user's skill directory."""
        addon_dir = user_skills_dir / addon_name
        if addon_dir.exists():
            shutil.rmtree(addon_dir)
    
    async def list_installed(self, user_id: int) -> list[AddonMetadata]:
        """List add-ons installed for a user."""
        addons_json = Path(f"./workspaces/{user_id}/addons.json")
        if not addons_json.exists():
            return []
        return json.loads(addons_json.read_text())
    
    async def update(self, addon_name: str, user_skills_dir: Path) -> AddonMetadata:
        """Update add-on to latest version."""
        return await self.install(addon_name, user_skills_dir, version="latest")
```

### 4. Bot Command Interface

Add new commands to `bot_handlers.py`:

```python
@router.message(Command("addon"))
async def cmd_addon(message: Message) -> None:
    """
    Manage add-ons (skills) for your account.
    
    Usage:
      /addon search <query>         - Search KiroHub for add-ons
      /addon install <name>         - Install an add-on
      /addon list                   - Show installed add-ons
      /addon remove <name>          - Uninstall an add-on
      /addon info <name>            - Show add-on details
      /addon update [name]          - Update add-on(s)
    """
    ctx = _get_ctx()
    user_id = message.from_user.id
    
    # Parse subcommand
    parts = (message.text or "").split(maxsplit=2)
    subcommand = parts[1] if len(parts) > 1 else "list"
    arg = parts[2] if len(parts) > 2 else None
    
    kirohub = KiroHubClient()
    
    if subcommand == "search":
        results = await kirohub.search(arg or "")
        # Format and send results
        
    elif subcommand == "install":
        user_skills_dir = Path(f"./workspaces/{user_id}/.kiro/skills")
        addon = await kirohub.install(arg, user_skills_dir)
        await message.answer(f"✅ Installed {addon.name} v{addon.version}")
        
    # ... other subcommands
```

### 5. User Add-on Registry

Store user add-on metadata in `./workspaces/{user_id}/addons.json`:

```json
{
  "version": "1.0",
  "addons": [
    {
      "name": "weather-api",
      "version": "2.1.0",
      "installedAt": "2026-02-16T19:00:00Z",
      "source": "kirohub",
      "enabled": true
    },
    {
      "name": "web-search",
      "version": "1.5.3",
      "installedAt": "2026-02-15T14:30:00Z",
      "source": "kirohub",
      "enabled": true
    }
  ]
}
```

This enables:
- Version tracking
- Enable/disable without uninstalling
- Audit trail of installations
- Future: dependency resolution

---

## Implementation Plan

### Phase 1: Per-User Skill Support (Core Infrastructure)

**Goal**: Enable per-user skill directories without KiroHub integration.

1. **Update `ProcessPool`** to accept `user_id` parameter
   - Pass `user_id` when spawning kiro-cli processes
   - Set `KIRO_HOME` to user-specific path

2. **Update `WorkspaceProvisioner`**
   - Create method `provision_user_kiro_home(user_id: int)`
   - Initialize `./workspaces/{user_id}/.kiro/skills/` directory

3. **Update `acp_client.py`**
   - Modify `spawn_process()` to accept custom `KIRO_HOME` env var
   - Test skill resolution with user-specific directories

4. **Add Tests**
   - Test user-specific skill loading
   - Verify isolation between users
   - Test fallback to global skills

### Phase 2: Add-on Management Commands

**Goal**: Enable manual add-on management via chat commands.

1. **Create `src/tg_acp/addon_manager.py`**
   - `AddonMetadata` dataclass
   - `AddonRegistry` class for CRUD operations on `addons.json`
   - File-based add-on installation (manual copying for now)

2. **Add Bot Commands**
   - `/addon list` — show installed add-ons
   - `/addon install <path>` — install from local file/directory (manual)
   - `/addon remove <name>` — uninstall add-on
   - `/addon info <name>` — show add-on details

3. **Add Tests**
   - Test command parsing
   - Test add-on installation/removal
   - Test registry persistence

### Phase 3: KiroHub Integration

**Goal**: Enable discovery and installation from KiroHub registry.

1. **Create `src/tg_acp/kirohub_client.py`**
   - Implement `npx kirohub` wrapper
   - Handle `search`, `install`, `update` operations
   - Parse KiroHub output (JSON or structured format)

2. **Update Bot Commands**
   - `/addon search <query>` — search KiroHub
   - `/addon install <name>` — install from KiroHub
   - `/addon update [name]` — update from KiroHub

3. **Add Dependency Management**
   - Check Node.js and npm availability
   - Graceful degradation if KiroHub unavailable
   - Add `NODE_PATH` configuration to `.env`

4. **Add Tests**
   - Mock `npx kirohub` calls
   - Test installation flow
   - Test error handling (network, invalid packages, etc.)

### Phase 4: Polish & Documentation

1. **User Experience**
   - Rich formatting for add-on listings
   - Progress indicators for installations
   - Help text and examples
   - Auto-complete for add-on names

2. **Documentation**
   - Update README.md with add-on commands
   - Create user guide: "Installing Add-ons"
   - Create developer guide: "Publishing Add-ons to KiroHub"

3. **Error Handling**
   - Validation for add-on formats
   - Recovery from failed installations
   - Clear error messages

---

## Alternative Approaches Considered

### Alternative 1: MCP Server Approach

**Description**: Instead of skills, use MCP servers as the extensibility mechanism.

**Pros**:
- MCP is more powerful (can expose tools, resources, prompts)
- Already supported by Kiro CLI
- Better for complex integrations

**Cons**:
- Requires running separate server processes (complex)
- Higher resource overhead
- Less suitable for simple tools/utilities
- MCP servers are typically language-specific (Node.js, Python)

**Decision**: Use Skills as primary mechanism, but also support MCP via similar per-user registry.

### Alternative 2: Container-Based Isolation

**Description**: Run each user's kiro-cli in a separate container with isolated filesystem.

**Pros**:
- True isolation (security, resource limits)
- Could support more complex add-ons

**Cons**:
- Significantly more complex deployment
- Higher resource overhead
- Slower session startup
- Overkill for the current use case

**Decision**: Not suitable for the lightweight Telegram bot use case.

### Alternative 3: Global Skills with User Permissions

**Description**: Keep global skill directory but add user-based ACLs in config.

**Pros**:
- Simpler implementation
- Single source of truth

**Cons**:
- Doesn't enable user-driven installation
- Requires admin intervention
- No versioning per user
- Doesn't solve the core requirement

**Decision**: Doesn't meet the "per-user, self-service" requirement.

---

## Security Considerations

### 1. Skill Validation

- **Risk**: Malicious skills could execute arbitrary code
- **Mitigation**: 
  - Only allow installation from trusted KiroHub registry
  - Implement skill signature verification
  - Sandbox skill execution (future: use Kiro's security features)

### 2. Disk Space Limits

- **Risk**: Users could install unlimited add-ons, exhausting disk
- **Mitigation**:
  - Implement per-user quota (e.g., 100MB, 20 add-ons)
  - Clean up unused add-ons after inactivity period

### 3. Dependency Confusion

- **Risk**: Malicious packages with similar names to popular add-ons
- **Mitigation**:
  - Display full package metadata before installation
  - Require confirmation for installations
  - Implement package verification (checksums, signatures)

### 4. Path Traversal

- **Risk**: Malicious add-on names could escape user directory
- **Mitigation**:
  - Validate add-on names (alphanumeric + hyphens only)
  - Use `Path.resolve()` and check result is within user directory

---

## Open Questions

### 1. KiroHub CLI Interface

**Question**: What is the actual CLI interface for `npx kirohub`?

**Action Required**: 
- Test `npx kirohub --help` to understand available commands
- Document exact command structure
- May need to create wrapper if CLI doesn't exist yet

### 2. Kiro CLI Skill Resolution

**Question**: Does Kiro CLI support environment variables for skill directory paths?

**Options**:
- **Option A**: `KIRO_HOME` controls all directories (agents, skills, steering)
- **Option B**: Separate env vars like `KIRO_SKILLS_PATH` for skills only
- **Option C**: Symlink approach (create user-specific `~/.kiro/` with symlinks)

**Action Required**: Test Kiro CLI behavior with custom `KIRO_HOME`

### 3. Skill Format

**Question**: What is the expected directory structure for Agent Skills?

**Action Required**:
- Review agentskills.io specification
- Ensure compatibility with KiroHub package format
- Test loading skills from custom directories

### 4. Concurrent Installation

**Question**: How to handle concurrent installations by same user?

**Options**:
- Lock file per user (e.g., `./workspaces/{user_id}/.addon-lock`)
- Queue installations per user
- Allow concurrent (rely on filesystem atomicity)

**Recommendation**: Use per-user async lock in bot code

---

## Migration Strategy

### For Existing Users

1. **No Breaking Changes**: Existing global skills continue to work
2. **Opt-In**: Users explicitly install add-ons via `/addon install`
3. **Gradual Migration**: Global skills can be deprecated over time

### For Bot Administrators

1. **Global Skills Remain**: Keep `kiro-config/skills/` for default/essential skills
2. **New Config**: Add `KIROHUB_REGISTRY_URL` to `.env` (defaults to `kirohub.dev`)
3. **Node.js Requirement**: Document Node.js/npm as new dependency

---

## Success Metrics

1. **Adoption**: % of active users who install at least one add-on
2. **Engagement**: Average add-ons installed per user
3. **Performance**: Add-on installation time < 10 seconds
4. **Reliability**: Installation success rate > 95%
5. **Support Load**: Reduction in admin requests for skill installations

---

## Timeline Estimate

- **Phase 1** (Per-User Skills): 2-3 days
- **Phase 2** (Management Commands): 2-3 days
- **Phase 3** (KiroHub Integration): 3-4 days
- **Phase 4** (Polish & Docs): 2 days

**Total**: ~10-12 days for full implementation

---

## Appendix: Example User Flow

### Discovering and Installing an Add-on

```
User: /addon search weather

Bot: 🔍 Found 3 add-ons matching "weather":

1. weather-api (v2.1.0) ⭐ 450
   Real-time weather data from OpenWeather API
   🔧 Last updated: 2 days ago

2. weather-alerts (v1.3.5) ⭐ 120
   Severe weather notifications and forecasts
   🔧 Last updated: 1 week ago

3. weather-charts (v0.9.0) ⭐ 45
   Visualize weather trends with charts
   🔧 Last updated: 3 weeks ago

Use /addon install <name> to install.

---

User: /addon install weather-api

Bot: 📦 Installing weather-api...
✅ Successfully installed weather-api v2.1.0

Available tools:
  • get_current_weather(location)
  • get_forecast(location, days)
  • get_weather_alerts(location)

Try asking me "What's the weather in San Francisco?"

---

User: What's the weather in San Francisco?

Bot: [Uses weather-api skill to fetch and return current weather]
```

---

## References

- [Agent Skills Specification](https://agentskills.io/specification)
- [Kiro CLI Documentation](https://kiro.dev/docs/cli/)
- [Agent Client Protocol (ACP)](https://agentclientprotocol.com/)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
