# Technical Specification: Per-User Add-on Installation

_See ADDON_ARCHITECTURE_PROPOSAL.md for high-level architecture overview._

## Quick Reference

This document provides implementation details for per-user add-on installation.

### Key Changes Required

1. **Directory Structure**: Add `./workspaces/{user_id}/.kiro/skills/` for user-specific skills
2. **Process Pool**: Pass `user_id` to set custom `KIRO_HOME` per process
3. **Provisioner**: Create `provision_user_kiro_home(user_id)` method
4. **Bot Commands**: Add `/addon search|install|list|remove|info|update`
5. **KiroHub Client**: Wrapper for `npx kirohub` CLI
6. **Addon Manager**: Core logic for install/uninstall/registry management

### Implementation Phases

- **Phase 1**: Per-user skill directories (2-3 days)
- **Phase 2**: Management commands (2-3 days)
- **Phase 3**: KiroHub integration (3-4 days)
- **Phase 4**: Polish & docs (2 days)

**Total Estimate**: 10-12 days

---

## Directory Structure

### Proposed Layout
```
./workspaces/{user_id}/
├── .kiro/
│   ├── agents/  → symlink to ~/.kiro/agents
│   ├── steering/ → symlink to ~/.kiro/steering
│   └── skills/   # User-specific skills
│       ├── weather-api/
│       └── web-search/
├── addons.json   # User's addon registry
└── {thread_id}/  # Thread workspaces
```

### Skill Resolution Order
1. User skills: `./workspaces/{user_id}/.kiro/skills/`
2. Global skills: `~/.kiro/skills/` (fallback)

---

## Core Components

### 1. AddonMetadata & UserAddonRegistry

```python
@dataclass
class AddonMetadata:
    name: str
    version: str
    description: str
    author: str | None
    installed_at: datetime
    source: Literal["kirohub", "local", "url"]
    enabled: bool

@dataclass  
class UserAddonRegistry:
    version: str = "1.0"
    addons: list[AddonMetadata]
    
    @classmethod
    def load(cls, path: Path) -> "UserAddonRegistry"
    
    def save(self, path: Path) -> None
    def find(self, name: str) -> AddonMetadata | None
    def add(self, addon: AddonMetadata) -> None
    def remove(self, name: str) -> bool
```

### 2. KiroHubClient

```python
class KiroHubClient:
    """Wrapper for `npx kirohub` CLI."""
    
    async def search(query: str) -> list[AddonSearchResult]
    async def get_info(name: str) -> AddonInfo
    async def install(name: str, target_dir: Path, version: str | None) -> AddonMetadata
    async def get_versions(name: str) -> list[str]
```

**CLI Interface** (assumed):
```bash
npx kirohub search <query> --json
npx kirohub info <name> --json
npx kirohub install <name> [--version <v>] --target <dir> --json
npx kirohub versions <name> --json
```

### 3. AddonManager

```python
class AddonManager:
    """High-level addon operations for a user."""
    
    def __init__(self, user_id: int, workspace_base: Path)
    
    async def install(name: str, version: str | None) -> AddonMetadata
    async def uninstall(name: str) -> bool
    async def list_installed() -> list[AddonMetadata]
    async def enable(name: str) -> bool
    async def disable(name: str) -> bool
```

---

## Integration Changes

### ProcessPool

Add `user_id` parameter to `execute_request`:

```python
async def execute_request(
    self,
    thread_id: int,
    user_id: int,  # NEW
    user_message: str,
    files: list[str],
    chat_id: int,
    workspace_path: str,
) -> AsyncIterator[dict]:
    # Set user-specific KIRO_HOME
    user_kiro_home = Path(workspace_path).parent / ".kiro"
    slot.client.set_env("KIRO_HOME", str(user_kiro_home))
```

### ACPClient

Add environment variable support:

```python
class ACPClient:
    def __init__(self, agent_name: str, env: dict[str, str] | None = None):
        self._env = env or {}
    
    async def spawn_process(self, workspace_path: str) -> None:
        env = os.environ.copy()
        env.update(self._env)
        # Use env when spawning kiro-cli
```

### WorkspaceProvisioner

Add user provisioning:

```python
def provision_user_kiro_home(self, user_id: int) -> Path:
    """Create user-specific .kiro with symlinks to global config."""
    user_kiro = Path(workspace_base) / str(user_id) / ".kiro"
    (user_kiro / "skills").mkdir(parents=True, exist_ok=True)
    
    # Symlink global agents and steering
    for subdir in ["agents", "steering"]:
        (user_kiro / subdir).symlink_to(Path.home() / ".kiro" / subdir)
    
    return user_kiro
```

---

## Bot Commands

### `/addon search <query>`
Search KiroHub for add-ons.

**Example**: `/addon search weather`

**Response**:
```
🔍 Found 3 add-ons:

1️⃣ weather-api v2.1.0 ⭐ 450
   Real-time weather data
   
2️⃣ weather-alerts v1.3.5 ⭐ 120
   Severe weather notifications
```

### `/addon install <name>[@version]`
Install add-on from KiroHub.

**Example**: `/addon install weather-api`

**Response**:
```
📦 Installing weather-api v2.1.0...
✅ Installed successfully!

📚 Available tools:
  • get_current_weather(location)
  • get_forecast(location, days)
```

### `/addon list`
List installed add-ons.

**Response**:
```
📦 Your installed add-ons (3):

✅ weather-api v2.1.0
   🏷️ weather, api
   📅 Installed 2 days ago
```

### `/addon remove <name>`
Uninstall add-on.

### `/addon info <name>`
Show add-on details.

### `/addon update [name]`
Update add-on(s) to latest version.

---

## Configuration

Add to `.env.example`:

```bash
# KiroHub Configuration
KIROHUB_REGISTRY_URL=https://kirohub.dev
MAX_ADDONS_PER_USER=20
MAX_ADDON_SIZE_MB=50
```

---

## Testing

### Unit Tests
- `tests/test_addon_manager.py` - CRUD operations
- `tests/test_kirohub_client.py` - API wrapper
- `tests/test_addon_registry.py` - Registry persistence
- `tests/test_addon_commands.py` - Bot commands

### Integration Tests
- User skill isolation
- Kiro CLI skill loading with custom KIRO_HOME
- End-to-end installation flow

---

## Security

### Input Validation
```python
def validate_addon_name(name: str) -> bool:
    """Only alphanumeric, hyphens, underscores."""
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", name)) and len(name) <= 64
```

### Limits
- Max 20 addons per user
- Max 50MB per addon
- Per-user async lock for concurrent operations

### Future
- Package signature verification
- Resource limits (CPU, memory)
- Sandboxing

---

## Open Questions

1. **KiroHub CLI**: Does `npx kirohub` exist? What's the actual interface?
   - **Action**: Test `npx kirohub --help` to verify
   
2. **Kiro CLI**: Does it support custom `KIRO_HOME` for skills?
   - **Action**: Test with `KIRO_HOME=/custom/path kiro-cli acp`
   
3. **Skill Format**: What's the expected structure?
   - **Action**: Review agentskills.io specification

---

## Success Criteria

- [ ] Users can search for add-ons
- [ ] Users can install/uninstall add-ons
- [ ] Add-ons are isolated per user
- [ ] Skills load in Kiro CLI with custom KIRO_HOME
- [ ] Installation completes in < 10 seconds
- [ ] All tests pass
- [ ] Documentation complete
- [ ] Backward compatible (global skills still work)

---

## Timeline

- **Phase 1** (Infrastructure): 2-3 days
- **Phase 2** (Commands): 2-3 days  
- **Phase 3** (KiroHub): 3-4 days
- **Phase 4** (Polish): 2 days

**Total**: 10-12 days
