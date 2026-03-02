# Per-User Add-on Installation: Implementation Guide

## Overview

This guide provides a step-by-step implementation plan for enabling per-user add-on installation in KiroClaw via the KiroHub registry (`kirohub.dev`, accessed via `npx kirohub`).

## Documentation Structure

1. **ADDON_ARCHITECTURE_PROPOSAL.md** - High-level architecture, alternatives, and rationale
2. **PER_USER_ADDONS_TECHNICAL_SPEC.md** - Detailed technical specifications
3. **This document** - Implementation checklist and getting started guide

## Quick Start

### Prerequisites

Before implementing:
1. ✅ Verify `npx kirohub` CLI exists and document its interface
2. ✅ Test Kiro CLI with custom `KIRO_HOME` environment variable
3. ✅ Review Agent Skills specification at agentskills.io
4. ✅ Set up test KiroHub account/registry (if needed)

### Key Decisions Made

- **Per-User Isolation**: Each user gets `./workspaces/{user_id}/.kiro/skills/`
- **Skill Resolution**: User skills override global skills
- **Registry**: KiroHub via `npx kirohub` wrapper
- **Bot Interface**: `/addon` command with subcommands
- **Storage**: JSON registry at `./workspaces/{user_id}/addons.json`

---

## Implementation Checklist

### Phase 1: Per-User Skill Support (Days 1-3)

#### 1.1 Create Data Models
- [ ] Create `src/tg_acp/addon_models.py`
  - [ ] `AddonMetadata` dataclass
  - [ ] `AddonSource` enum
  - [ ] `UserAddonRegistry` class with load/save methods
- [ ] Add unit tests in `tests/test_addon_models.py`

#### 1.2 Update WorkspaceProvisioner
- [ ] Add `provision_user_kiro_home(user_id: int)` method to `provisioner.py`
  - [ ] Create `./workspaces/{user_id}/.kiro/skills/` directory
  - [ ] Symlink `agents` and `steering` to global directories
- [ ] Add tests in `tests/test_provisioner.py`

#### 1.3 Update ACPClient for Custom Environment
- [ ] Modify `ACPClient.__init__()` to accept `env: dict[str, str]`
- [ ] Update `spawn_process()` to merge custom env vars
- [ ] Add tests in `tests/test_acp_client.py`

#### 1.4 Update ProcessPool
- [ ] Add `user_id: int` parameter to `execute_request()`
- [ ] Set `KIRO_HOME` to user-specific path when spawning processes
- [ ] Update all callers in `bot_handlers.py`
- [ ] Add tests in `tests/test_process_pool.py`

**Milestone**: Users have isolated skill directories, Kiro CLI loads user-specific skills

---

### Phase 2: Add-on Management Commands (Days 4-6)

#### 2.1 Create AddonManager
- [ ] Create `src/tg_acp/addon_manager.py`
  - [ ] `AddonManager` class
  - [ ] `install()` method (manual file copy for now)
  - [ ] `uninstall()` method
  - [ ] `list_installed()` method
  - [ ] `enable()` / `disable()` methods
- [ ] Add tests in `tests/test_addon_manager.py`
  - [ ] Test install/uninstall
  - [ ] Test registry persistence
  - [ ] Test concurrent operations (lock)

#### 2.2 Create Exception Classes
- [ ] Create `src/tg_acp/addon_exceptions.py`
  - [ ] `AddonError` base class
  - [ ] `AddonNotFoundError`
  - [ ] `AddonAlreadyInstalledError`
  - [ ] `AddonLimitExceededError`
  - [ ] `InstallationError`

#### 2.3 Add Bot Commands
- [ ] Update `src/tg_acp/bot_handlers.py`
  - [ ] Create `cmd_addon()` handler with `@router.message(Command("addon"))`
  - [ ] Parse subcommands: `list`, `remove`, `info`
  - [ ] Format responses with rich formatting (emojis, etc.)
- [ ] Add tests in `tests/test_addon_commands.py`
  - [ ] Test command parsing
  - [ ] Test error handling
  - [ ] Mock AddonManager calls

#### 2.4 Update Configuration
- [ ] Add fields to `src/tg_acp/config.py`:
  - [ ] `max_addons_per_user: int`
  - [ ] `max_addon_size_mb: int`
- [ ] Update `.env.example` with new variables

**Milestone**: Users can manually manage add-ons via bot commands

---

### Phase 3: KiroHub Integration (Days 7-10)

#### 3.1 Create KiroHubClient
- [ ] Create `src/tg_acp/kirohub_client.py`
  - [ ] `_run_kirohub_command()` helper for `npx kirohub`
  - [ ] `search(query)` method
  - [ ] `get_info(name)` method
  - [ ] `install(name, target_dir, version)` method
  - [ ] `get_versions(name)` method
- [ ] Add tests in `tests/test_kirohub_client.py`
  - [ ] Mock `subprocess` calls
  - [ ] Test JSON parsing
  - [ ] Test error handling

#### 3.2 Integrate KiroHub into AddonManager
- [ ] Update `AddonManager.install()` to use `KiroHubClient`
- [ ] Add version specification support (`addon_name@version`)
- [ ] Add size limit checking
- [ ] Add quota checking (max addons per user)
- [ ] Update tests

#### 3.3 Add KiroHub Commands
- [ ] Update `cmd_addon()` in `bot_handlers.py`:
  - [ ] `/addon search <query>` - search KiroHub
  - [ ] `/addon install <name>` - install from KiroHub
  - [ ] `/addon info <name>` - show add-on details
  - [ ] `/addon update [name]` - update add-ons
- [ ] Add progress indicators (edit message during install)
- [ ] Update tests

#### 3.4 Add Caching
- [ ] Add cache directory to config (`kirohub_cache_dir`)
- [ ] Cache search results (1 hour TTL)
- [ ] Cache add-on metadata
- [ ] Add cache cleanup logic

#### 3.5 Error Handling
- [ ] Handle KiroHub unavailable (graceful degradation)
- [ ] Handle network errors
- [ ] Handle invalid packages
- [ ] User-friendly error messages

**Milestone**: Users can discover and install add-ons from KiroHub

---

### Phase 4: Polish & Documentation (Days 11-12)

#### 4.1 User Experience
- [ ] Rich formatting for all responses
  - [ ] Search results with stars, tags, descriptions
  - [ ] Installation progress indicators
  - [ ] Color/emoji for status (✅❌⏳📦)
- [ ] Help text for `/addon` command
- [ ] Auto-suggestions for common queries
- [ ] Confirmation for destructive operations

#### 4.2 Documentation
- [ ] Update `README.md`:
  - [ ] Add "Add-on Management" section
  - [ ] Document `/addon` commands
  - [ ] Add example workflow
- [ ] Create `docs/USER_GUIDE_ADDONS.md`:
  - [ ] How to search for add-ons
  - [ ] How to install/manage add-ons
  - [ ] Troubleshooting
- [ ] Create `docs/DEVELOPER_GUIDE_ADDONS.md`:
  - [ ] How to create add-ons
  - [ ] Publishing to KiroHub
  - [ ] Testing locally

#### 4.3 Testing
- [ ] Integration tests:
  - [ ] End-to-end install flow
  - [ ] User isolation verification
  - [ ] Kiro CLI skill loading
- [ ] Manual testing:
  - [ ] Test with real Telegram bot
  - [ ] Test with real KiroHub registry
  - [ ] Performance testing (installation time)

#### 4.4 Monitoring
- [ ] Add logging for add-on operations
- [ ] Track metrics:
  - [ ] Installations per user
  - [ ] Popular add-ons
  - [ ] Success/failure rates
  - [ ] Installation time

**Milestone**: Production-ready feature with complete documentation

---

## File Structure After Implementation

```
kiroclaw/
├── src/tg_acp/
│   ├── addon_models.py          # NEW: AddonMetadata, UserAddonRegistry
│   ├── addon_manager.py         # NEW: AddonManager class
│   ├── addon_exceptions.py      # NEW: Exception classes
│   ├── kirohub_client.py        # NEW: KiroHub API wrapper
│   ├── acp_client.py            # MODIFIED: Add env param
│   ├── bot_handlers.py          # MODIFIED: Add /addon command
│   ├── config.py                # MODIFIED: Add addon config
│   ├── process_pool.py          # MODIFIED: Add user_id param
│   └── provisioner.py           # MODIFIED: Add user provisioning
│
├── tests/
│   ├── test_addon_models.py     # NEW
│   ├── test_addon_manager.py    # NEW
│   ├── test_kirohub_client.py   # NEW
│   ├── test_addon_commands.py   # NEW
│   ├── test_acp_client.py       # MODIFIED
│   ├── test_process_pool.py     # MODIFIED
│   └── test_provisioner.py      # MODIFIED
│
├── docs/
│   ├── ADDON_IMPLEMENTATION_GUIDE.md  # This file
│   ├── PER_USER_ADDONS_TECHNICAL_SPEC.md
│   ├── USER_GUIDE_ADDONS.md           # NEW
│   └── DEVELOPER_GUIDE_ADDONS.md      # NEW
│
├── ADDON_ARCHITECTURE_PROPOSAL.md
├── README.md                           # MODIFIED
└── .env.example                        # MODIFIED
```

---

## Testing Strategy

### Unit Tests

Run after each phase:
```bash
uv run pytest tests/test_addon_*.py -v
```

### Integration Tests

Run after Phase 3:
```bash
uv run pytest tests/test_addon_integration.py -v
```

### Manual Testing

After Phase 3, test with real bot:

1. Start bot: `uv run main.py`
2. In Telegram:
   ```
   /addon search weather
   /addon install weather-api
   /addon list
   Ask: "What's the weather in San Francisco?"
   /addon remove weather-api
   ```

---

## Rollback Plan

If issues arise:

1. **Phase 1-2**: Disable `/addon` command, revert to global skills
2. **Phase 3**: Graceful degradation - if KiroHub unavailable, manual install only
3. **Emergency**: Feature flag to disable entire addon system

---

## Performance Targets

- Add-on search: < 2 seconds
- Add-on installation: < 10 seconds
- Add-on listing: < 1 second
- Bot response time: unchanged (< 500ms)

---

## Security Checklist

- [ ] Validate add-on names (no path traversal)
- [ ] Validate versions (semver only)
- [ ] Enforce size limits
- [ ] Enforce quota limits
- [ ] Rate limit installations (e.g., max 10/hour per user)
- [ ] Log all installations for audit
- [ ] (Future) Verify package signatures

---

## Known Limitations

1. **KiroHub CLI**: Assuming `npx kirohub` exists - needs verification
2. **Kiro CLI**: Assuming `KIRO_HOME` env var controls skill loading
3. **Skill Format**: Assuming Agent Skills format from agentskills.io
4. **No Sandboxing**: Add-ons run with full bot permissions (future improvement)

---

## Next Steps

1. **Verify Assumptions**: Test KiroHub CLI and Kiro CLI behavior
2. **Start Phase 1**: Begin with per-user skill support
3. **Iterate**: Get each phase working before moving to next
4. **Deploy**: Test in staging environment before production

---

## Support & Questions

For implementation questions:
1. Review technical spec: `docs/PER_USER_ADDONS_TECHNICAL_SPEC.md`
2. Review architecture: `ADDON_ARCHITECTURE_PROPOSAL.md`
3. Check existing code patterns in `src/tg_acp/`

---

## Success Metrics

Track these after deployment:
- Number of users installing add-ons
- Average add-ons per active user
- Popular add-ons (most installed)
- Installation success rate
- User feedback/support requests

Goal: 30%+ of active users install at least one add-on within first month.
