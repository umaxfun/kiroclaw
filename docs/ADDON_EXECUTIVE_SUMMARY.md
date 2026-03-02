# Per-User Add-on Installation: Executive Summary

**Date:** February 16, 2026  
**Status:** Proposal / Design Complete  
**Estimated Effort:** 10-12 days  
**Risk Level:** Medium

---

## Problem Statement

Currently, KiroClaw supports only **global skills** that are shared across all users. This creates several limitations:

1. **No User Customization**: All users must use the same set of tools
2. **Admin Bottleneck**: Every new skill requires bot redeployment
3. **No Discovery**: Users cannot explore available add-ons
4. **No Versioning**: Skills are static, no updates without redeployment
5. **Conflicts**: Cannot have different versions of same skill for different users

**Impact**: Limited functionality, high admin overhead, poor user experience.

---

## Proposed Solution

Enable **per-user add-on installation** using the KiroHub registry, allowing users to:

- 🔍 **Search** for add-ons directly in chat: `/addon search weather`
- 📦 **Install** add-ons instantly: `/addon install weather-api`
- 📋 **Manage** their own toolset: `/addon list`, `/addon remove`
- ⬆️ **Update** add-ons to latest versions: `/addon update`
- 💡 **Discover** new capabilities without admin help

---

## Key Benefits

### For Users
- ✅ **Self-Service**: Install add-ons instantly, no waiting for admin
- ✅ **Personalization**: Each user chooses their own tools
- ✅ **Discovery**: Browse hundreds of add-ons from KiroHub
- ✅ **Always Updated**: Get latest add-on versions on demand

### For Administrators
- ✅ **Zero Intervention**: Users manage their own add-ons
- ✅ **No Redeployments**: Add-ons installed without bot restart
- ✅ **Reduced Support**: Self-service reduces support requests
- ✅ **Backward Compatible**: Existing global skills continue to work

### For the Platform
- ✅ **Ecosystem Growth**: Encourages add-on development
- ✅ **User Engagement**: More capabilities = higher usage
- ✅ **Competitive Advantage**: First Telegram bot with add-on marketplace
- ✅ **Scalability**: Decentralized add-on distribution

---

## How It Works

### User Experience

```
User: /addon search weather

Bot: 🔍 Found 3 add-ons matching "weather":

     1️⃣ weather-api v2.1.0 ⭐ 450
        Real-time weather data from OpenWeather API
     
     Use /addon install weather-api to install

---

User: /addon install weather-api

Bot: 📦 Installing weather-api...
     ✅ Successfully installed weather-api v2.1.0
     
     💡 Try asking: "What's the weather in San Francisco?"

---

User: What's the weather in San Francisco?

Bot: [Uses weather-api to provide current weather]
```

### Technical Implementation

1. **Per-User Workspaces**: Each user gets `./workspaces/{user_id}/.kiro/skills/`
2. **KiroHub Integration**: Wrapper for `npx kirohub` CLI to search/install
3. **Bot Commands**: New `/addon` command with 6 subcommands
4. **Process Isolation**: Kiro CLI uses user-specific `KIRO_HOME` environment variable
5. **Registry Tracking**: JSON file tracks installed add-ons per user

---

## Architecture Overview

```
Telegram User
    ↓ /addon install weather-api
Bot Handlers (parse command)
    ↓
AddonManager (business logic)
    ↓
KiroHubClient (npx kirohub install...)
    ↓
KiroHub Registry (kirohub.dev)
    ↓ Downloads add-on package
User's Skill Directory (./workspaces/{user_id}/.kiro/skills/)
    ↓
Kiro CLI (loads skills when processing messages)
```

**Key Insight**: Users get isolated skill directories, just like they already have isolated thread workspaces.

---

## Implementation Plan

### Phase 1: Per-User Skill Support (2-3 days)
- Create user-specific skill directories
- Update Kiro CLI to use custom `KIRO_HOME`
- Test skill isolation

**Milestone**: Users have isolated skill directories

---

### Phase 2: Add-on Management Commands (2-3 days)
- Create AddonManager class
- Add bot commands: `/addon list|remove|info`
- Manual add-on installation (file-based)

**Milestone**: Users can manually manage add-ons

---

### Phase 3: KiroHub Integration (3-4 days)
- Create KiroHub client wrapper
- Add `/addon search|install|update` commands
- Error handling and validation

**Milestone**: Users can discover and install from KiroHub

---

### Phase 4: Polish & Documentation (2 days)
- Rich formatting for responses
- User and developer guides
- Testing and monitoring

**Milestone**: Production-ready feature

---

## Technical Requirements

### Infrastructure
- ✅ Python 3.12 (already in use)
- ✅ uv package manager (already in use)
- ✅ kiro-cli (already in use)
- ✅ Existing workspace structure
- 🆕 Node.js + npm (for `npx kirohub`)

### New Dependencies
- None (uses existing stack + Node.js)

### Configuration
Add to `.env`:
```bash
KIROHUB_REGISTRY_URL=https://kirohub.dev
MAX_ADDONS_PER_USER=20
MAX_ADDON_SIZE_MB=50
```

---

## Security Considerations

### Built-In Protections
1. **Input Validation**: Add-on names limited to alphanumeric + hyphens
2. **Quota Limits**: Max 20 add-ons per user, 50MB per add-on
3. **Path Isolation**: Users cannot access other users' add-ons
4. **Rate Limiting**: Max installations per time period

### Future Enhancements
- Package signature verification
- Add-on sandboxing (resource limits)
- Security audit of popular add-ons

### Risk Assessment
- **Risk**: Malicious add-ons could execute arbitrary code
- **Mitigation**: Only allow KiroHub registry (curated), signature verification (future)
- **Impact**: Medium (isolated per user, no cross-user contamination)

---

## Success Metrics

### Adoption Metrics
- **Target**: 30% of active users install at least one add-on in first month
- **Measure**: Track installations per user via logging

### Engagement Metrics
- **Target**: Average 3+ add-ons per active user
- **Measure**: Count add-ons in user registries

### Performance Metrics
- **Target**: < 10 seconds for add-on installation
- **Measure**: Log installation time

### Support Metrics
- **Target**: Reduce skill-related support requests by 80%
- **Measure**: Track support ticket volume

---

## Risks & Mitigation

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| KiroHub unavailable | Medium | Low | Graceful degradation, caching, local installs |
| Malicious add-ons | High | Low | Curated registry, signature verification |
| Disk space exhaustion | Low | Medium | Quotas, size limits, cleanup |
| Performance degradation | Medium | Low | Async operations, caching |
| User confusion | Low | Medium | Clear UX, help text, examples |

**Overall Risk**: **Medium** - Manageable with proper safeguards

---

## Alternatives Considered

### Alternative 1: MCP Server Approach
Use MCP servers instead of Agent Skills.

**Pros**: More powerful, already supported by Kiro CLI  
**Cons**: Complex (requires server processes), higher overhead  
**Decision**: Skills are better for simple tools; MCP for complex integrations

### Alternative 2: Container Isolation
Run each user's kiro-cli in separate container.

**Pros**: True isolation, stronger security  
**Cons**: Complex deployment, high overhead  
**Decision**: Overkill for lightweight Telegram bot

### Alternative 3: Global Skills with User ACLs
Keep global skills, add user permissions in config.

**Pros**: Simple implementation  
**Cons**: Doesn't enable self-service, still requires admin  
**Decision**: Doesn't meet core requirement

---

## Open Questions

### Question 1: KiroHub CLI Interface
**Q**: Does `npx kirohub` exist? What's the actual interface?  
**A**: Needs verification; proposal assumes standard CLI interface  
**Impact**: High - core dependency  
**Action**: Test `npx kirohub --help` before starting Phase 3

### Question 2: Kiro CLI Skill Loading
**Q**: Does Kiro CLI support custom `KIRO_HOME` for skills?  
**A**: Needs testing  
**Impact**: High - required for per-user isolation  
**Action**: Test with `KIRO_HOME=/custom/path kiro-cli acp` before Phase 1

### Question 3: Skill Format
**Q**: What's the expected directory structure for Agent Skills?  
**A**: Likely follows agentskills.io specification  
**Impact**: Medium - affects installation logic  
**Action**: Review spec before Phase 2

---

## Rollback Plan

If critical issues arise:

1. **Disable Feature**: Feature flag to disable `/addon` command
2. **Revert to Global**: Set all users' `KIRO_HOME` back to `~/.kiro/`
3. **Emergency**: Rollback deployment, investigate offline

**Recovery Time**: < 1 hour (simple config change)

---

## Timeline & Resources

### Development Timeline
- **Total**: 10-12 days (2 weeks)
- **Phase 1**: 2-3 days
- **Phase 2**: 2-3 days
- **Phase 3**: 3-4 days
- **Phase 4**: 2 days

### Resource Requirements
- **Developer**: 1 full-time developer
- **Reviewer**: Code reviews after each phase
- **Tester**: Manual testing in Phase 4
- **Documentation**: Included in developer time

### Deployment
- **Staging**: Deploy to test environment for 1 week
- **Beta**: Enable for select users for 1 week
- **Production**: Full rollout after successful beta

**Total Project Timeline**: 4 weeks (2 weeks dev + 2 weeks testing/rollout)

---

## Recommendation

✅ **APPROVE** - Proceed with implementation

**Rationale**:
1. **High User Value**: Solves major pain point (customization)
2. **Low Risk**: Manageable security concerns, graceful degradation
3. **Reasonable Effort**: 2 weeks development time
4. **Strategic**: Enables ecosystem growth, competitive advantage
5. **Scalable**: Self-service reduces admin burden

**Next Steps**:
1. Verify assumptions (KiroHub CLI, Kiro CLI behavior)
2. Approve timeline and resource allocation
3. Begin Phase 1 implementation
4. Regular progress reviews (weekly)

---

## Appendix: Documentation Deliverables

The following documents have been created to support this proposal:

1. **ADDON_ARCHITECTURE_PROPOSAL.md** (High-level architecture)
   - Executive summary
   - Current limitations
   - Proposed architecture
   - Alternatives considered
   - Security considerations
   - Implementation phases
   - Success metrics

2. **docs/PER_USER_ADDONS_TECHNICAL_SPEC.md** (Technical details)
   - Directory structure
   - Data models
   - API specifications
   - Integration points
   - Bot command specs
   - Testing strategy

3. **docs/ADDON_IMPLEMENTATION_GUIDE.md** (Developer guide)
   - Implementation checklist
   - File structure
   - Testing strategy
   - Rollback plan
   - Performance targets

4. **docs/ADDON_ARCHITECTURE_DIAGRAMS.md** (Visual overview)
   - System architecture diagram
   - User workspace structure
   - Skill resolution flow
   - Installation flow
   - Security boundaries

5. **This document** (Executive summary)

---

## Questions?

For technical questions, review the technical spec and implementation guide.

For business/strategic questions, contact the project stakeholders.

---

**Status**: Ready for approval and implementation.
