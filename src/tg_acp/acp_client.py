"""C1: ACP Client — JSON-RPC 2.0 over stdin/stdout with kiro-cli acp."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from enum import Enum
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)


class ACPClientState(Enum):
    """Protocol state machine states."""

    IDLE = "idle"  # spawned but not initialized
    INITIALIZING = "initializing"  # initialize sent, waiting for response
    READY = "ready"  # can accept session commands
    BUSY = "busy"  # session/prompt in flight
    DEAD = "dead"  # process exited or crashed


# Synthetic update type emitted by this client when the turn ends.
TURN_END = "TurnEnd"


class ACPClient:
    """Manages a single kiro-cli acp subprocess and its JSON-RPC protocol."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._state = ACPClientState.IDLE
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._notification_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._log_level = logging.INFO

    @property
    def state(self) -> ACPClientState:
        return self._state

    @classmethod
    async def spawn(cls, agent_name: str, log_level: str = "INFO") -> ACPClient:
        """Spawn a kiro-cli acp subprocess and return an initialized client."""
        client = cls()
        client._log_level = getattr(logging, log_level.upper(), logging.INFO)

        client._process = await asyncio.create_subprocess_exec(
            "kiro-cli",
            "acp",
            "--agent",
            agent_name,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # own process group — so kill() can kill the whole tree
        )

        client._stdout_task = asyncio.create_task(client._read_stdout())
        client._stderr_task = asyncio.create_task(client._read_stderr())

        logger.info("Spawned kiro-cli acp --agent %s (pid=%s)", agent_name, client._process.pid)
        return client

    def _next_request_id(self) -> int:
        """Monotonically increasing request ID."""
        rid = self._next_id
        self._next_id += 1
        return rid

    async def _send(self, message: dict) -> None:
        """Write a JSON-RPC message to stdin (newline-delimited)."""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Process not running")
        line = json.dumps(message) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()
        logger.debug("-> %s", message.get("method", "response"))

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and wait for the matching response."""
        rid = self._next_request_id()
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[rid] = future

        await self._send({
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params,
        })

        result = await future
        if "error" in result and result["error"] is not None:
            err = result["error"]
            raise RuntimeError(
                f"JSON-RPC error on {method}: [{err.get('code')}] {err.get('message')}"
            )
        return result.get("result", {})

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        await self._send({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        })

    async def initialize(self) -> dict:
        """Send initialize handshake. Returns server capabilities."""
        if self._state != ACPClientState.IDLE:
            raise RuntimeError(f"Cannot initialize in state {self._state}")

        self._state = ACPClientState.INITIALIZING
        result = await self._send_request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": True, "writeTextFile": True},
                "terminal": True,
            },
            "clientInfo": {
                "name": "tg-acp-bot",
                "title": "Telegram ACP Bot",
                "version": "0.1.0",
            },
        })
        self._state = ACPClientState.READY
        logger.info("Initialized — server capabilities: %s", list(result.keys()) if result else "none")
        return result

    async def session_new(self, cwd: str) -> str:
        """Create a new session. Returns session_id."""
        if self._state != ACPClientState.READY:
            raise RuntimeError(f"Cannot create session in state {self._state}")

        result = await self._send_request("session/new", {
            "cwd": cwd,
            "mcpServers": [],
        })
        session_id = result.get("sessionId", "")
        logger.info("Created session %s (cwd=%s)", session_id, cwd)
        return session_id

    async def session_load(self, session_id: str, cwd: str | None = None) -> None:
        """Load an existing session."""
        if self._state != ACPClientState.READY:
            raise RuntimeError(f"Cannot load session in state {self._state}")

        params: dict = {
            "sessionId": session_id,
            "mcpServers": [],
        }
        if cwd is not None:
            params["cwd"] = cwd

        await self._send_request("session/load", params)
        self._drain_notifications("after session/load")
        logger.info("Loaded session %s", session_id)
    def _drain_notifications(self, context: str) -> None:
        """Discard queued notifications (e.g. stale session/load replays)."""
        drained = 0
        while not self._notification_queue.empty():
            try:
                self._notification_queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            logger.debug("Drained %d stale notifications (%s)", drained, context)



    async def session_prompt(
        self, session_id: str, content: list[dict]
    ) -> AsyncGenerator[dict, None]:
        """Send a prompt and yield session/update notifications until turn end.

        Yields dicts with keys: sessionUpdate (str), content (dict|None).
        Real kiro-cli updates use snake_case (e.g. "agent_message_chunk").
        The final yield has sessionUpdate == TURN_END (synthetic, from this client).
        """
        if self._state != ACPClientState.READY:
            raise RuntimeError(f"Cannot prompt in state {self._state}")

        self._state = ACPClientState.BUSY
        self._drain_notifications("before prompt")

        # Send the prompt request
        rid = self._next_request_id()
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[rid] = future

        await self._send({
            "jsonrpc": "2.0",
            "id": rid,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": content,
            },
        })

        # Yield notifications until we get the response
        while True:
            # Check if the response arrived (non-blocking)
            if future.done():
                self._state = ACPClientState.READY
                yield {"sessionUpdate": TURN_END, "content": None}
                return

            try:
                notification = await asyncio.wait_for(
                    self._notification_queue.get(), timeout=0.1
                )
                # Only yield session/update notifications
                method = notification.get("method", "")
                if method == "session/update":
                    params = notification.get("params", {})
                    update = params.get("update", {})
                    yield update
            except asyncio.TimeoutError:
                # Check again if response arrived
                if future.done():
                    self._state = ACPClientState.READY
                    yield {"sessionUpdate": TURN_END, "content": None}
                    return
                # Also check if process died
                if not self.is_alive():
                    self._state = ACPClientState.DEAD
                    raise RuntimeError("kiro-cli process died during prompt")

    async def session_cancel(self, session_id: str) -> None:
        """Cancel an in-flight prompt (notification — no response expected)."""
        await self._send_notification("session/cancel", {"sessionId": session_id})
        logger.info("Sent cancel for session %s", session_id)

    async def session_set_model(self, session_id: str, model: str) -> None:
        """Set the model for a session."""
        await self._send_request("session/set_model", {
            "sessionId": session_id,
            "model": model,
        })
        logger.info("Set model to %s for session %s", model, session_id)

    def is_alive(self) -> bool:
        """Check if the subprocess is still running."""
        return self._process is not None and self._process.returncode is None

    async def kill(self) -> None:
        """Terminate the subprocess and all children. Waits up to 5 seconds, then force-kills."""
        if self._process is None:
            return

        self._state = ACPClientState.DEAD

        try:
            # Kill the entire process group — kiro-cli spawns kiro-cli-chat
            # as a child, and terminate() only kills the parent, leaving the
            # child alive (and holding session lock files).
            pid = self._process.pid
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                self._process.terminate()

            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                self._process.kill()
            await self._process.wait()

        # Cancel reader tasks
        for task in (self._stdout_task, self._stderr_task):
            if task and not task.done():
                task.cancel()

        # Resolve any pending futures
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("Process killed"))
        self._pending.clear()

        logger.info("Killed kiro-cli process")

    # --- Background reader tasks ---

    async def _read_stdout(self) -> None:
        """Read JSON-RPC messages from stdout, route to pending requests or notification queue."""
        assert self._process is not None and self._process.stdout is not None

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break  # EOF

                line_str = line.decode().strip()
                if not line_str:
                    continue

                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON stdout line: %s", line_str[:200])
                    continue

                if "id" in msg:
                    rid = msg["id"]
                    # Check if this is a server-initiated request (has "method")
                    # vs a response to one of our requests (no "method")
                    if "method" in msg:
                        # Server-initiated request — log and ignore for now
                        logger.debug(
                            "Server-initiated request id=%s method=%s",
                            rid, msg.get("method"),
                        )
                        continue

                    # Response — route to pending request
                    fut = self._pending.pop(rid, None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                    else:
                        logger.warning(
                            "Unexpected response id=%s (pending_ids=%s, msg_keys=%s)",
                            rid,
                            list(self._pending.keys()),
                            list(msg.keys()),
                        )
                else:
                    # Notification — queue for consumers
                    await self._notification_queue.put(msg)
                    method = msg.get("method", "?")
                    update_type = (
                        msg.get("params", {}).get("update", {}).get("sessionUpdate", "")
                    )
                    logger.debug("<- notification %s (%s)", method, update_type)
        except asyncio.CancelledError:
            pass
        finally:
            # Process died — transition to DEAD
            if self._state != ACPClientState.DEAD:
                self._state = ACPClientState.DEAD
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(RuntimeError("Process stdout closed"))
                self._pending.clear()

    async def _read_stderr(self) -> None:
        """Read stderr lines and log them."""
        assert self._process is not None and self._process.stderr is not None

        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                logger.log(self._log_level, "[kiro-cli stderr] %s", line.decode().rstrip())
        except asyncio.CancelledError:
            pass
