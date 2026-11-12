"""Mike's brain: one long-running Claude Code process on your Pro login.

Talks to `claude -p` using the stream-json protocol on stdin/stdout, so the
process (and the conversation) stays warm between voice commands.
Permission prompts arrive as `can_use_tool` control requests that we answer.
"""
import asyncio
import json
import logging
import os
import sys
import uuid
from typing import Awaitable, Callable, Optional

import config
import memory

log = logging.getLogger("mike.brain")

STATE_FILE = config.DATA / "state.json"
MCP_FILE = config.DATA / "mcp.json"


class Brain:
    def __init__(
        self,
        on_delta: Callable[[str], Awaitable[None]],
        on_tool: Callable[[str, dict], Awaitable[None]],
        on_done: Callable[[dict], Awaitable[None]],
        on_permission: Callable[[str, dict], Awaitable[bool]],
        on_log: Callable[[str], Awaitable[None]],
        on_usage: Callable[[dict], Awaitable[None]],
    ):
        self.on_usage = on_usage
        self.on_delta = on_delta
        self.on_tool = on_tool
        self.on_done = on_done
        self.on_permission = on_permission
        self.on_log = on_log
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.session_id: Optional[str] = self._load_session()
        self.busy = False
        self.ignore_output = False  # set while an interrupted turn drains

    # ---- session persistence -------------------------------------------
    def _load_session(self) -> Optional[str]:
        try:
            return json.loads(STATE_FILE.read_text()).get("session_id")
        except Exception:
            return None

    def _save_session(self):
        STATE_FILE.write_text(json.dumps({"session_id": self.session_id}))

    # ---- process lifecycle ---------------------------------------------
    def _write_mcp_config(self):
        MCP_FILE.write_text(json.dumps({
            "mcpServers": {
                "mike": {
                    "command": sys.executable,
                    "args": [str(config.ROOT / "mike_tools.py")],
                    "env": {"MIKE_URL": f"http://{config.HOST}:{config.PORT}"},
                }
            }
        }, indent=2))

    def _args(self) -> list[str]:
        args = [
            config.CLAUDE_BIN, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-prompt-tool", "stdio",
            # Our own short prompt instead of Claude Code's long coding one: faster first word.
            "--system-prompt", memory.system_prompt(config.PERSONA),
            "--tools", ",".join(config.TOOLS),
            "--mcp-config", str(MCP_FILE),
            "--strict-mcp-config",
            # Skip your personal Claude Code plugins/hooks so they don't slow Mike down.
            "--setting-sources", "project,local",
            # Your personal Claude Code hooks are for coding sessions, not a voice assistant.
            "--settings", json.dumps({"disableAllHooks": True}),
        ]
        for d in config.ACCESS_DIRS:
            args += ["--add-dir", str(d)]
        if config.MODEL:
            args += ["--model", config.MODEL]
        if config.EFFORT:
            args += ["--effort", config.EFFORT]
        if self.session_id:
            args += ["--resume", self.session_id]
        # Tools that make no sense for a voice assistant.
        args += ["--disallowedTools", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "EnterWorktree"]
        # Variadic flag goes last so it doesn't swallow other options.
        args += ["--allowedTools", *config.ALLOWED_TOOLS]
        return args

    async def start(self):
        if self.proc and self.proc.returncode is None:
            return
        self._write_mcp_config()
        config.WORKDIR.mkdir(parents=True, exist_ok=True)
        # Drop every ANTHROPIC_*/CLAUDE* variable (API keys, proxy URLs, model overrides,
        # parent-session vars) so Claude Code uses your own Pro login and nothing else.
        env = {k: v for k, v in os.environ.items()
               if not (k.startswith("ANTHROPIC_") or k.startswith("CLAUDE"))}
        # Load Mike's few tools up front instead of searching for them each time (faster).
        env["ENABLE_TOOL_SEARCH"] = "false"
        log.info("starting claude (resume=%s)", self.session_id)
        self.proc = await asyncio.create_subprocess_exec(
            *self._args(),
            cwd=str(config.WORKDIR),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=64 * 1024 * 1024,
        )
        asyncio.create_task(self._read_loop(self.proc))
        asyncio.create_task(self._stderr_loop(self.proc))

    async def stop(self):
        proc, self.proc = self.proc, None
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                proc.kill()
        self.busy = False

    async def reset(self):
        """Forget the conversation and start fresh."""
        await self.stop()
        self.session_id = None
        self._save_session()

    # ---- sending -------------------------------------------------------
    async def _write(self, obj: dict):
        await self.start()
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self.proc.stdin.drain()

    async def send(self, text: str, images: list[str] | None = None):
        """Send a user turn. images = base64 JPEG strings."""
        content: list[dict] = []
        for b64 in images or []:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": b64}})
        content.append({"type": "text", "text": text})
        self.busy = True
        self.ignore_output = False
        await self._write({
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
            "session_id": self.session_id or "default",
        })

    async def interrupt(self):
        if not self.busy:
            return
        self.ignore_output = True
        try:
            await self._write({
                "type": "control_request",
                "request_id": f"int_{uuid.uuid4().hex[:8]}",
                "request": {"subtype": "interrupt"},
            })
        except Exception:
            pass
        # If the turn doesn't wind down quickly, restart the process (conversation is kept).
        for _ in range(30):
            await asyncio.sleep(0.1)
            if not self.busy:
                return
        log.warning("interrupt timed out; restarting claude")
        await self.stop()

    # ---- reading -------------------------------------------------------
    async def _stderr_loop(self, proc):
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            msg = line.decode(errors="replace").rstrip()
            if msg:
                log.warning("claude: %s", msg)
                await self.on_log(f"claude: {msg[:300]}")

    async def _read_loop(self, proc):
        while True:
            try:
                line = await proc.stdout.readline()
            except Exception as e:
                log.exception("read error: %s", e)
                continue
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                await self._handle(msg)
            except Exception:
                log.exception("error handling message")
        code = await proc.wait()
        log.warning("claude exited with %s", code)
        # Only the live process ends a turn. One we stopped on purpose (fresh start, model
        # switch) must not be mistaken for the end of the request now running on its successor.
        if self.proc is proc:
            self.proc = None
            if self.busy:
                self.busy = False
                await self.on_done({"result": "", "error": f"Claude Code exited ({code})."})

    async def _handle(self, msg: dict):
        t = msg.get("type")
        if t == "system" and msg.get("subtype") == "init":
            sid = msg.get("session_id")
            if sid and sid != self.session_id:
                self.session_id = sid
                self._save_session()
            model = msg.get("model", "?")
            if model != getattr(self, "_model", None):
                self._model = model
                await self.on_log(f"brain online · model {model}")
            failed = [s.get("name") for s in msg.get("mcp_servers", []) if s.get("status") != "connected"]
            if failed:
                await self.on_log(f"warning: tools not connected: {', '.join(failed)}")
        elif t == "stream_event":
            ev = msg.get("event", {})
            if self.ignore_output:
                return
            if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                await self.on_delta(ev["delta"]["text"])
        elif t == "assistant":
            for block in msg.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    await self.on_tool(block.get("name", "?"), block.get("input") or {})
        elif t == "rate_limit_event":
            info = msg.get("rate_limit_info") or {}
            await self.on_usage(info)
        elif t == "control_request":
            # Answer in a task so the read loop keeps flowing while the user decides.
            asyncio.create_task(self._handle_control(msg))
        elif t == "result":
            sid = msg.get("session_id")
            if sid and sid != self.session_id:
                self.session_id = sid
                self._save_session()
            self.busy = False
            interrupted = self.ignore_output
            self.ignore_output = False
            await self.on_done({
                "result": "" if interrupted else (msg.get("result") or ""),
                "error": None if (msg.get("subtype") == "success" or interrupted) else msg.get("subtype"),
                "cost": msg.get("total_cost_usd"),
                "duration_ms": msg.get("duration_ms"),
                "interrupted": interrupted,
            })

    async def _handle_control(self, msg: dict):
        req = msg.get("request", {})
        rid = msg.get("request_id")
        if req.get("subtype") == "can_use_tool":
            tool = req.get("tool_name", "?")
            tool_input = req.get("input") or {}
            allowed = False
            if not self.ignore_output:
                try:
                    allowed = await self.on_permission(tool, tool_input)
                except Exception:
                    log.exception("permission handler failed")
            if allowed is True:
                decision = {"behavior": "allow", "updatedInput": tool_input}
            elif isinstance(allowed, str) and allowed:
                decision = {"behavior": "deny", "message": allowed}       # refused with guidance, not by the user
            else:
                decision = {"behavior": "deny",
                            "message": "The user declined this action. Acknowledge briefly and stop."}
            response = {"subtype": "success", "request_id": rid, "response": decision}
        else:
            response = {"subtype": "success", "request_id": rid, "response": {}}
        try:
            await self._write({"type": "control_response", "response": response})
        except Exception:
            log.exception("failed to answer control request")
