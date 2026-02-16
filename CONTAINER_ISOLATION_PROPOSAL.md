# Container-Based Multi-User Isolation Proposal

## Executive Summary

This document proposes a **true multi-user security architecture** using container isolation to prevent users with coding tool access from exfiltrating data from other users or the host system. The current application-layer session isolation is insufficient because users have access to Python, bash, and other tools that can directly read the filesystem.

## Problem Analysis

### Current Approach Limitations

The existing per-user session wrapping and process slot binding provides only **application-layer isolation**, which is trivially bypassed by users with tool access:

```python
# User can bypass application isolation with:
import os
for root, dirs, files in os.walk('/home/user/.kiro/sessions/cli'):
    for file in files:
        print(open(os.path.join(root, file)).read())
```

**Fundamental Issue**: All users share the same process space and filesystem with full tool access. No amount of application-level validation can prevent a malicious user from directly accessing the filesystem.

### Security Requirements for True Isolation

1. **Filesystem Isolation**: Each user's kiro-cli process must run in its own isolated filesystem
2. **Process Isolation**: Users cannot see or interact with other users' processes
3. **Network Isolation**: Optional - limit external network access per user
4. **Resource Limits**: CPU, memory, and disk quotas per user to prevent DoS
5. **Tool Access Preservation**: Users still need Python, bash, and coding tools within their container

## Proposed Architecture: Per-User Docker Containers

### Overview

Each Telegram user gets a dedicated, ephemeral Docker container that runs their kiro-cli session. The main bot process orchestrates container lifecycle and proxies ACP communication.

```
┌─────────────────────────────────────────────────────────────┐
│  Host System                                                 │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  KiroClaw Bot (Python)                               │   │
│  │  - Telegram message handling                         │   │
│  │  - Container orchestration                           │   │
│  │  - ACP proxy                                         │   │
│  └────────┬─────────────────────────────────────────────┘   │
│           │                                                   │
│    ┌──────┴────────┬──────────────┬──────────────┐         │
│    │               │              │              │          │
│  ┌─▼───────────┐ ┌─▼───────────┐ ┌─▼───────────┐ ...      │
│  │ Container   │ │ Container   │ │ Container   │          │
│  │ User 42     │ │ User 99     │ │ User 123    │          │
│  │             │ │             │ │             │          │
│  │ kiro-cli    │ │ kiro-cli    │ │ kiro-cli    │          │
│  │ + tools     │ │ + tools     │ │ + tools     │          │
│  │             │ │             │ │             │          │
│  │ /workspace  │ │ /workspace  │ │ /workspace  │          │
│  │ /sessions   │ │ /sessions   │ │ /sessions   │          │
│  └─────────────┘ └─────────────┘ └─────────────┘          │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Container Specification

**Base Image**: Ubuntu or Alpine with Python 3.12 and kiro-cli

**Per-Container Resources**:
- Read-only root filesystem (except /workspace and /tmp)
- Dedicated volume: `/workspace` (user's files)
- Dedicated volume: `/sessions` (kiro session data)
- No network access (or restricted if needed)
- CPU limit: 1-2 cores
- Memory limit: 2GB
- Disk quota: 5GB

**Dockerfile Example**:
```dockerfile
FROM python:3.12-slim

# Install kiro-cli and dependencies
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://kiro.dev/install.sh | sh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 kirouser

# Set up directories
RUN mkdir -p /workspace /sessions && \
    chown kirouser:kirouser /workspace /sessions

USER kirouser
WORKDIR /workspace

# Keep container running and ready for ACP commands
CMD ["sleep", "infinity"]
```

### Implementation Components

#### 1. Container Manager (`src/tg_acp/container_manager.py`)

Manages container lifecycle for each user:

```python
import docker
from dataclasses import dataclass
from typing import Dict

@dataclass
class UserContainer:
    user_id: int
    container_id: str
    workspace_volume: str
    session_volume: str
    created_at: float
    last_used: float

class ContainerManager:
    """Manages per-user Docker containers for isolation."""
    
    def __init__(self, config: Config):
        self.client = docker.from_env()
        self.containers: Dict[int, UserContainer] = {}
        self.image_name = "kiroclaw-user:latest"
        self.max_containers = config.max_users
        self.idle_timeout = 3600  # 1 hour
        
    async def get_or_create_container(self, user_id: int) -> UserContainer:
        """Get existing container or create new one for user."""
        if user_id in self.containers:
            container = self.containers[user_id]
            # Check if still running
            if self._is_container_alive(container.container_id):
                container.last_used = time.time()
                return container
            else:
                # Container died, remove and recreate
                await self._cleanup_container(user_id)
        
        return await self._create_container(user_id)
    
    async def _create_container(self, user_id: int) -> UserContainer:
        """Create a new isolated container for user."""
        # Create dedicated volumes
        workspace_volume = self.client.volumes.create(
            name=f"kiroclaw-workspace-{user_id}"
        )
        session_volume = self.client.volumes.create(
            name=f"kiroclaw-sessions-{user_id}"
        )
        
        # Create container with security restrictions
        container = self.client.containers.run(
            self.image_name,
            detach=True,
            name=f"kiroclaw-user-{user_id}",
            volumes={
                workspace_volume.name: {'bind': '/workspace', 'mode': 'rw'},
                session_volume.name: {'bind': '/sessions', 'mode': 'rw'},
            },
            environment={
                'KIRO_SESSION_DIR': '/sessions',
                'USER_ID': str(user_id),
            },
            # Security settings
            network_mode='none',  # No network access
            mem_limit='2g',
            cpu_quota=100000,  # 1 CPU
            read_only=True,  # Read-only root filesystem
            tmpfs={'/tmp': 'size=512m,mode=1777'},
            security_opt=['no-new-privileges'],
            cap_drop=['ALL'],  # Drop all capabilities
        )
        
        user_container = UserContainer(
            user_id=user_id,
            container_id=container.id,
            workspace_volume=workspace_volume.name,
            session_volume=session_volume.name,
            created_at=time.time(),
            last_used=time.time(),
        )
        
        self.containers[user_id] = user_container
        return user_container
    
    async def execute_acp_command(
        self, user_id: int, command: str
    ) -> AsyncGenerator[str, None]:
        """Execute ACP command in user's container via docker exec."""
        container = await self.get_or_create_container(user_id)
        
        # Execute kiro-cli acp in container
        exec_instance = self.client.api.exec_create(
            container.container_id,
            ['kiro-cli', 'acp', '--agent', 'tg-acp'],
            stdin=True,
            stdout=True,
            stderr=True,
        )
        
        exec_stream = self.client.api.exec_start(
            exec_instance['Id'],
            stream=True,
            socket=True,
        )
        
        # Send ACP command and yield responses
        exec_stream._sock.sendall(command.encode() + b'\n')
        
        for chunk in exec_stream:
            yield chunk.decode()
    
    async def cleanup_idle_containers(self):
        """Remove containers that have been idle too long."""
        now = time.time()
        to_remove = []
        
        for user_id, container in self.containers.items():
            if now - container.last_used > self.idle_timeout:
                to_remove.append(user_id)
        
        for user_id in to_remove:
            await self._cleanup_container(user_id)
    
    async def _cleanup_container(self, user_id: int):
        """Stop and remove container and its volumes."""
        if user_id not in self.containers:
            return
            
        container = self.containers[user_id]
        
        try:
            # Stop and remove container
            c = self.client.containers.get(container.container_id)
            c.stop(timeout=10)
            c.remove()
        except docker.errors.NotFound:
            pass
        
        # Optionally: remove volumes (or keep for session persistence)
        # self.client.volumes.get(container.workspace_volume).remove()
        # self.client.volumes.get(container.session_volume).remove()
        
        del self.containers[user_id]
```

#### 2. Updated Process Pool (`src/tg_acp/process_pool.py`)

Replace direct kiro-cli subprocess spawning with container execution:

```python
class ProcessPool:
    """Manages per-user container-based kiro-cli instances."""
    
    def __init__(self, config: Config):
        self.container_manager = ContainerManager(config)
        # ... existing fields ...
    
    async def acquire(self, thread_id: int, user_id: int) -> ProcessSlot | None:
        """Acquire a process slot - now backed by user's container."""
        # Check for existing affinity
        affinity_key = (user_id, thread_id)
        
        # Get or create container for this user
        container = await self.container_manager.get_or_create_container(user_id)
        
        # Each container can handle multiple threads for the same user
        # Container provides the isolation boundary
        slot = ProcessSlot(
            slot_id=self._next_slot_id(),
            container=container,
            user_id=user_id,
            thread_id=thread_id,
            status=SlotStatus.BUSY,
        )
        
        return slot
```

#### 3. ACP Client Adapter (`src/tg_acp/acp_container_client.py`)

Adapter to communicate with kiro-cli running in containers:

```python
class ACPContainerClient:
    """ACP client that communicates with kiro-cli in a Docker container."""
    
    def __init__(self, container: UserContainer, container_manager: ContainerManager):
        self.container = container
        self.manager = container_manager
    
    async def session_new(self, cwd: str) -> str:
        """Create new session in container."""
        command = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": cwd, "mcpServers": []}
        })
        
        response = await self.manager.execute_acp_command(
            self.container.user_id,
            command
        )
        
        # Parse response and return session_id
        result = json.loads(response)
        return result['result']['sessionId']
    
    # Similar methods for session_load, session_prompt, etc.
```

### Security Benefits

#### ✅ True Filesystem Isolation
- User A's container cannot access user B's `/workspace` or `/sessions`
- Even with Python/bash, users are confined to their container

#### ✅ Process Isolation
- Users cannot see or interfere with other users' processes
- `ps aux` only shows processes in their own container

#### ✅ Resource Limits
- Per-user CPU, memory, and disk quotas prevent resource exhaustion
- DoS attacks contained to single user

#### ✅ Tool Access Preserved
- Users still have full Python, bash, and coding tool access
- They just can't escape their container

#### ✅ Session Persistence
- Docker volumes persist sessions across container restarts
- `/sessions` volume mapped to user's isolated storage

### Deployment Architecture

#### Development/Testing
- Docker Desktop or Docker Engine on local machine
- Containers run on same host as bot

#### Production (Small Scale)
- Single host with Docker
- Bot and containers on same machine
- Max 10-20 concurrent users

#### Production (Large Scale)
- Kubernetes cluster
- Bot as deployment, user containers as pods
- Horizontal scaling with pod autoscaling
- Persistent volumes for session storage

### Migration Path

#### Phase 1: Container Infrastructure (Week 1)
- Create Dockerfile for user containers
- Build and test image locally
- Implement ContainerManager class
- Add docker-py dependency

#### Phase 2: Integration (Week 2)
- Replace ProcessPool subprocess spawning with container execution
- Implement ACPContainerClient
- Update bot_handlers to use container-based execution
- Test with multiple users

#### Phase 3: Testing & Validation (Week 3)
- Security testing: verify isolation
- Performance testing: measure overhead
- Load testing: concurrent user limits
- Document deployment procedures

#### Phase 4: Production Deployment (Week 4)
- Deploy to staging environment
- Monitor resource usage
- Gradual rollout to production
- Update documentation

### Configuration

New environment variables:

```bash
# Container settings
DOCKER_IMAGE=kiroclaw-user:latest
MAX_CONTAINERS=50
CONTAINER_IDLE_TIMEOUT=3600
CONTAINER_CPU_LIMIT=1.0
CONTAINER_MEMORY_LIMIT=2g
CONTAINER_DISK_QUOTA=5g

# Network isolation
CONTAINER_NETWORK_MODE=none  # or bridge if external access needed

# Volume management
CONTAINER_VOLUME_DRIVER=local
CONTAINER_VOLUME_BASE=/var/lib/kiroclaw/volumes
```

### Performance Considerations

**Container Startup Time**: 1-3 seconds
- Mitigated by keeping containers running and reusing them

**Memory Overhead**: ~100MB per container
- Manageable for 10-50 concurrent users

**CPU Overhead**: Minimal (<5%)
- Docker has very low overhead for CPU

**Disk Space**: ~500MB per user (container + volumes)
- Need to implement cleanup policies

### Limitations & Trade-offs

#### Pros
- ✅ True security isolation
- ✅ Tool access preserved
- ✅ Resource control per user
- ✅ Industry-standard approach

#### Cons
- ❌ Requires Docker/container runtime
- ❌ More complex deployment
- ❌ Higher resource overhead
- ❌ Container startup latency

### Alternative: Kubernetes Pods

For larger deployments, use Kubernetes instead of raw Docker:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: kiroclaw-user-{{ user_id }}
  labels:
    app: kiroclaw
    user: "{{ user_id }}"
spec:
  containers:
  - name: kiro-cli
    image: kiroclaw-user:latest
    resources:
      limits:
        memory: "2Gi"
        cpu: "1000m"
      requests:
        memory: "512Mi"
        cpu: "250m"
    volumeMounts:
    - name: workspace
      mountPath: /workspace
    - name: sessions
      mountPath: /sessions
    securityContext:
      runAsNonRoot: true
      readOnlyRootFilesystem: true
      allowPrivilegeEscalation: false
  volumes:
  - name: workspace
    persistentVolumeClaim:
      claimName: workspace-{{ user_id }}
  - name: sessions
    persistentVolumeClaim:
      claimName: sessions-{{ user_id }}
```

## Comparison with Application-Layer Isolation

| Feature | Application Layer | Container Layer |
|---------|------------------|-----------------|
| Filesystem isolation | ❌ Bypassable | ✅ Enforced by kernel |
| Process isolation | ❌ Shared | ✅ Separate namespace |
| Tool access security | ❌ Tools can read all | ✅ Confined to container |
| Resource limits | ❌ Not enforced | ✅ cgroups limits |
| Implementation complexity | Low | Medium |
| Runtime overhead | Minimal | Low (~5-10%) |
| Security guarantee | Weak | Strong |

## Recommendation

**Implement container-based isolation** for true multi-user security when users have coding tool access. The application-layer session wrapping should be **removed or demoted to a legacy feature** since it provides a false sense of security.

### Immediate Next Steps

1. **Revert application-layer changes** (current PR)
2. **Create Dockerfile** for user container image
3. **Implement ContainerManager** with basic lifecycle
4. **Prototype ACP-over-container** communication
5. **Test with 2-3 users** to validate isolation
6. **Document deployment requirements** (Docker, resource needs)

## Conclusion

True multi-user isolation requires **kernel-level enforcement** through containers or VMs. Application-layer validation is insufficient when users have filesystem tool access. The proposed container architecture provides:

- Strong security guarantees
- Preserved tool functionality
- Industry-standard approach
- Practical deployment path

The implementation is more complex than application-layer isolation but is the only way to achieve real security in a multi-user environment with unrestricted tool access.
