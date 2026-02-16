# Multi-User Security Hardening Proposal

## Executive Summary

This document proposes security enhancements to make KiroClaw safe for multi-user deployments by implementing per-user Kiro-cli session isolation. The current architecture shares Kiro-cli sessions and workspace state across users, creating security and privacy risks. This proposal outlines changes to enforce strict per-user isolation.

## Current Security Issues

### 1. Shared Kiro-CLI Session Storage

**Issue**: All Kiro-cli sessions are stored in a shared `~/.kiro/sessions/cli/` directory on the host system.

**Risk**: 
- Users can potentially access other users' conversation history
- Session files are stored with the system user's permissions, not isolated by Telegram user
- Session IDs are predictable and could be guessed or enumerated

**Current Implementation**:
```python
# src/tg_acp/session_store.py
# Sessions mapped by (user_id, thread_id) → session_id
# But all session files end up in shared ~/.kiro/sessions/cli/
```

### 2. Process Pool Session Affinity Across Users

**Issue**: The process pool uses thread-level session affinity but not user-level isolation.

**Risk**:
- A kiro-cli process that handled User A's session could be reused for User B
- While separate sessions are maintained, the same process memory space is shared
- Potential for information leakage through process memory or cached state

**Current Implementation**:
```python
# src/tg_acp/process_pool.py
# _session_affinity tracks thread_id → slot_id
# But doesn't enforce user-level boundaries
self._session_affinity: dict[int, int] = {}  # thread_id → slot_id
```

### 3. Workspace Directory Structure

**Issue**: While workspaces are organized by `{user_id}/{thread_id}`, they're all under a single base directory with no access control.

**Risk**:
- File paths could potentially be traversed to access other users' workspaces
- No enforcement of user boundaries at the filesystem level
- All files owned by the same system user

**Current Implementation**:
```python
# src/tg_acp/session_store.py
def create_workspace_dir(workspace_base_path: str, user_id: int, thread_id: int) -> str:
    path = Path(workspace_base_path) / str(user_id) / str(thread_id)
    path.mkdir(parents=True, exist_ok=True)
    return str(path.resolve())
```

### 4. Global Agent Configuration

**Issue**: All users share the same `~/.kiro/agents/` configuration directory.

**Risk**:
- Changes to agent configuration affect all users
- No per-user customization without workspace-local overrides
- Potential for configuration conflicts between users

## Proposed Solution: Per-User Kiro-CLI Session Isolation

### Overview

Implement a **per-user session isolation model** where each Telegram user gets:
1. Dedicated Kiro-cli session storage directory
2. Isolated process pool slots (processes never shared across users)
3. Per-user workspace isolation with stricter path validation
4. Per-user session ID namespacing

### Design Changes

#### 1. Per-User Session Directory Structure

**Proposed Directory Layout**:
```
~/.kiro/
  sessions/
    cli/
      user-{telegram_user_id}/     ← NEW: per-user isolation
        sess_abc123.json
        sess_abc123.jsonl
        sess_xyz789.json
        sess_xyz789.jsonl

./workspaces/
  {telegram_user_id}/
    {thread_id}/                   ← existing structure
```

**Implementation**:
- Modify kiro-cli invocation to use per-user session directory via environment variable or config
- Store user_id in session_id to prevent cross-user session loading
- Update SessionStore to validate user_id matches session ownership

#### 2. Per-User Process Pool Isolation

**Proposed Changes**:
```python
# src/tg_acp/process_pool.py

@dataclass
class ProcessSlot:
    slot_id: int
    client: ACPClient | None
    status: SlotStatus
    last_used: float
    user_id: int | None           # NEW: track slot owner
    session_id: str | None = None
    thread_id: int | None = None

class ProcessPool:
    def __init__(self, config: Config) -> None:
        # ... existing fields ...
        # NEW: user-aware affinity
        self._session_affinity: dict[tuple[int, int], int] = {}  # (user_id, thread_id) → slot_id
        self._user_slots: dict[int, set[int]] = {}  # user_id → set of slot_ids
```

**Acquisition Rules**:
1. A slot can only be acquired by the user who owns it (user_id matches)
2. Once a slot is assigned to a user, it stays with that user until reaped
3. Slots are never shared across users
4. Each user gets their own pool of slots within the global max_processes limit

**Benefits**:
- Complete process memory isolation between users
- No risk of cached state or memory leakage across users
- Clearer resource allocation and monitoring per user

#### 3. Enhanced Session ID Security

**Current Format**: `sess_abc123` (kiro-cli generated)

**Proposed Enhancement**:
- Prefix session IDs with user_id: `user-{telegram_user_id}-sess_abc123`
- Validate session ownership on every load operation
- Reject cross-user session load attempts

**Implementation**:
```python
# src/tg_acp/session_store.py

class SessionStore:
    def validate_session_ownership(self, session_id: str, user_id: int) -> bool:
        """Ensure session_id belongs to user_id."""
        prefix = f"user-{user_id}-"
        return session_id.startswith(prefix)
    
    def get_session(self, user_id: int, thread_id: int) -> SessionRecord | None:
        """Lookup session with ownership validation."""
        record = self._fetch_record(user_id, thread_id)
        if record and not self.validate_session_ownership(record.session_id, user_id):
            logger.error("Session ownership violation: session=%s user=%s", 
                        record.session_id, user_id)
            return None
        return record
```

#### 4. Strict Workspace Path Validation

**Enhanced Path Validation**:
```python
# src/tg_acp/file_handler.py

def validate_path_with_user(file_path: str, workspace_path: str, user_id: int) -> bool:
    """Validate path is within user's workspace with additional user_id check."""
    resolved = Path(file_path).resolve()
    workspace_resolved = Path(workspace_path).resolve()
    
    # Must be within workspace
    if not resolved.is_relative_to(workspace_resolved):
        return False
    
    # Additional check: user_id must be in the path
    if f"/{user_id}/" not in str(workspace_resolved):
        logger.error("Workspace path missing user_id: %s", workspace_path)
        return False
    
    return True
```

### Configuration Changes

#### New Environment Variables

Add per-user resource limits:

```bash
# .env additions

# Maximum processes per user (default: 2)
MAX_PROCESSES_PER_USER=2

# Maximum processes globally (existing, default: 5)
MAX_PROCESSES=5

# Enable per-user session isolation (default: true)
ENABLE_USER_ISOLATION=true
```

#### Updated Config Class

```python
# src/tg_acp/config.py

@dataclass(frozen=True)
class Config:
    # ... existing fields ...
    max_processes_per_user: int
    enable_user_isolation: bool
    
    @classmethod
    def load(cls) -> Config:
        # ... existing validation ...
        
        max_processes_per_user = int(os.environ.get("MAX_PROCESSES_PER_USER", "2"))
        if max_processes_per_user < 1:
            raise ValueError("MAX_PROCESSES_PER_USER must be >= 1")
        
        enable_user_isolation = os.environ.get("ENABLE_USER_ISOLATION", "true").lower() == "true"
        
        return cls(
            # ... existing fields ...
            max_processes_per_user=max_processes_per_user,
            enable_user_isolation=enable_user_isolation,
        )
```

## Implementation Plan

### Phase 1: Core Isolation (Priority: High)

1. **Update SessionStore** (1-2 hours)
   - Add session ownership validation
   - Implement user_id-prefixed session IDs
   - Add tests for cross-user session prevention

2. **Modify Process Pool** (2-3 hours)
   - Add user_id to ProcessSlot
   - Update session affinity to use (user_id, thread_id) tuple
   - Implement per-user slot tracking
   - Add user isolation tests

3. **Update ACP Client** (1 hour)
   - Support per-user session directory via environment variable
   - Pass KIRO_SESSION_DIR to kiro-cli subprocess

### Phase 2: Enhanced Validation (Priority: Medium)

4. **Strengthen Path Validation** (1 hour)
   - Add user_id validation to path checks
   - Update FileHandler with enhanced validation
   - Add tests for path traversal prevention

5. **Add Configuration** (1 hour)
   - Implement per-user process limits
   - Add enable_user_isolation flag
   - Update .env.example with new variables

### Phase 3: Documentation & Testing (Priority: High)

6. **Comprehensive Testing** (2-3 hours)
   - Multi-user integration tests
   - Session isolation tests
   - Path traversal security tests
   - Process pool isolation tests

7. **Documentation** (1 hour)
   - Update README with security improvements
   - Add deployment security guidelines
   - Document new configuration options

## Security Benefits

### Before (Current State)
- ❌ Shared session storage across all users
- ❌ Process pool can mix user sessions
- ❌ No ownership validation on session access
- ❌ Basic path validation without user context
- ⚠️  Single layer of defense

### After (Proposed State)
- ✅ Per-user isolated session storage
- ✅ Process slots never shared across users
- ✅ Session ownership validated on every access
- ✅ Multi-layer path validation with user context
- ✅ Defense in depth with multiple isolation layers

## Backward Compatibility

### Breaking Changes
- Existing session files will need to be migrated or regenerated
- Process pool behavior changes (users won't share processes)

### Migration Strategy
1. Deploy with `ENABLE_USER_ISOLATION=false` for gradual rollout
2. Run migration script to move sessions to per-user directories
3. Enable user isolation once migration is complete
4. Optionally, let old sessions expire naturally (users start fresh)

### Migration Script (Optional)
```python
# migrate_sessions.py
# Moves ~/.kiro/sessions/cli/*.json to user-specific directories
# Based on session_store.db mappings
```

## Testing Strategy

### Unit Tests
- Session ownership validation
- Process pool user isolation
- Path validation with user context
- Configuration loading and validation

### Integration Tests
- Multi-user concurrent access
- Cross-user session prevention
- Process pool resource limits per user
- Session affinity with user boundaries

### Security Tests
- Attempt to load another user's session (should fail)
- Path traversal to another user's workspace (should fail)
- Session ID guessing (should fail due to validation)
- Process pool slot hijacking (should fail)

## Performance Considerations

### Resource Usage
- **Memory**: Minimal increase (user_id tracking in data structures)
- **Process Count**: Same as before, but better distributed per user
- **Storage**: Organized by user, same total size

### Potential Concerns
- More processes spawned if users don't share pools
  - **Mitigation**: Per-user limits prevent resource exhaustion
  - **Benefit**: Better isolation worth the cost

## Deployment Recommendations

### Production Deployment
1. Enable all isolation features (`ENABLE_USER_ISOLATION=true`)
2. Set conservative per-user limits (`MAX_PROCESSES_PER_USER=2`)
3. Monitor resource usage per user
4. Implement rate limiting at bot level (separate from this proposal)

### Development/Testing
1. Can disable isolation for testing if needed
2. Set higher per-user limits for load testing
3. Test with multiple users concurrently

## Future Enhancements

### Potential Follow-ups (Out of Scope)
1. **Resource Quotas**: Per-user storage limits, message rate limits
2. **Audit Logging**: Log all session access and file operations with user context
3. **Containerization**: Run each user's kiro-cli in a separate container
4. **Encryption**: Encrypt session files at rest per user
5. **User Groups**: Support for team workspaces with shared sessions

## Conclusion

This proposal addresses the critical security gaps in the current multi-user setup by implementing comprehensive per-user isolation at multiple layers:
- File system (session storage, workspaces)
- Process level (dedicated pool slots)
- Application logic (ownership validation)

The changes are designed to be minimal, focused, and backward compatible with a migration path. Implementation can be done incrementally with clear testing milestones.

**Estimated Total Effort**: 8-12 hours for complete implementation, testing, and documentation.

**Security Impact**: High - Eliminates cross-user data access risks and provides defense-in-depth isolation.
