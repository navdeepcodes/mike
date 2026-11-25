"""Background, project-scoped coding subagents for Mike's development tools."""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_records: dict[str, dict[str, Any]] = {}
_records_lock = threading.Lock()


def spawn(project_path: Path, task_description: str) -> str:
    """Start a model-backed subagent and retain its status/results by ID."""
    agent_id = f"sub_{uuid.uuid4().hex[:10]}"
    record = {
        "agent_id": agent_id,
        "project_path": str(project_path.resolve()),
        "task_description": task_description,
        "status": "queued",
        "started_at": time.time(),
        "tool_calls": [],
        "result": "",
        "error": "",
        "diff": "",
    }
    with _records_lock:
        _records[agent_id] = record
    threading.Thread(target=_thread_main, args=(agent_id,), name=agent_id, daemon=True).start()
    return f"Started subagent {agent_id} for: {task_description[:160]}"


def results(agent_id: str = "") -> str:
    """Return a snapshot of one subagent or all current subagents."""
    with _records_lock:
        selected = ([dict(_records[agent_id])] if agent_id in _records else []) if agent_id else [dict(v) for v in _records.values()]
    if agent_id and not selected:
        return f"Unknown subagent id: {agent_id}"
    if not selected:
        return "No subagents have been started."
    return json.dumps([
        {key: record[key] for key in ("agent_id", "status", "task_description", "result", "error", "diff", "tool_calls")}
        for record in selected
    ], indent=2)


def _update(agent_id: str, **updates):
    with _records_lock:
        if agent_id in _records:
            _records[agent_id].update(updates)


def _thread_main(agent_id: str):
    _update(agent_id, status="running")
    try:
        record = _records[agent_id]
        result = asyncio.run(_run_agent(agent_id, Path(record["project_path"]), record["task_description"]))
        _update(agent_id, status="completed" if not result.get("error") else "failed",
                result=result.get("result", ""), error=result.get("error", ""), diff=result.get("diff", ""))
    except Exception as exc:
        _update(agent_id, status="failed", error=str(exc))


async def _run_agent(agent_id: str, project_path: Path, task_description: str) -> dict:
    import fallback
    from self_coding import DevSession, ProjectDetector, ToolExecutor

    record = _records[agent_id]
    session = DevSession(agent_id, task_description, project_path)
    executor = ToolExecutor(project_path, session)
    detector = ProjectDetector(project_path)
    names = {
        "code_read", "code_write", "code_edit", "code_search", "code_bash", "code_test",
        "code_lint", "code_type_check", "code_build", "code_git_status", "code_git_diff",
        "code_index", "code_dependents", "code_deps", "code_callers", "code_callees",
        "code_search_relevant",
    }

    def execute(name: str, args: dict) -> str:
        path = str(args.get("path", ""))
        if name in {"code_read", "code_write", "code_edit"} and path:
            target = Path(path) if Path(path).is_absolute() else project_path / path
            try:
                target.resolve().relative_to(project_path.resolve())
            except ValueError:
                return "Failed: path is outside the selected project."
        if name == "code_read":
            return _format(executor.read_file(path, args.get("start", 0), args.get("end", 0)))
        if name == "code_write":
            return _format(executor.write_file(path, args.get("content", "")))
        if name == "code_edit":
            return _format(executor.edit_file(path, args.get("old", ""), args.get("new", ""), args.get("replace_all", False)))
        if name == "code_search":
            return _format(executor.search_code(args.get("pattern", ""), args.get("path", ""), include_mike_root=False))
        if name == "code_bash":
            cwd_arg = args.get("cwd", "")
            cwd = (project_path / cwd_arg).resolve() if cwd_arg else project_path
            try:
                cwd.relative_to(project_path.resolve())
            except ValueError:
                return "Failed: working directory is outside the selected project."
            return _format(executor._run_command(args.get("command", ""), cwd, args.get("timeout", 120)))
        if name == "code_test":
            result = executor.run_tests(detector)
            return f"Tests: {result.passed}/{result.total} passed ({result.failed} failed, {result.errors} errors)\nFramework: {result.framework}\n{result.output[-3000:]}"
        if name == "code_lint":
            return _format(executor.run_lint(detector))
        if name == "code_type_check":
            return _format(executor.run_type_check(detector))
        if name == "code_build":
            return _format(executor.run_build(detector))
        if name == "code_git_status":
            return _format(executor.git_status())
        if name == "code_git_diff":
            return _format(executor.git_diff(args.get("staged", False)))
        if name.startswith("code_index") or name in {"code_dependents", "code_deps", "code_callers", "code_callees"}:
            return "Use code_search and code_read to inspect the project structure."
        return f"Unsupported subagent tool: {name}"

    def _format(result) -> str:
        return result.output if result.success else f"Failed: {result.error or result.output or 'tool failed'}"

    class ScopedBrain(fallback.FallbackBrain):
        def __init__(self, *args):
            super().__init__(*args)
            self.models.update({
                "groq": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"],
                "nvidia": ["nvidia/nemotron-3-ultra-550b-a55b", "mistralai/mistral-large-4-1t-a52b",
                           "nvidia/nemotron-3.5-super-vl-120b-a12b"],
                "openrouter": ["nvidia/nemotron-3-ultra-550b-a55b:free", "google/gemma-4-31b-it:free"],
                "gemini": ["gemini-3.6-flash", "gemini-3.5-flash"],
            })

        async def _run(self):
            self.dev_until = time.time() + 20 * 60
            providers = self.available()
            if not providers:
                raise RuntimeError("No backup model is configured for subagents")
            system = (
                "You are Mike's coding subagent. Work only in the selected project. Make a focused change, "
                "inspect before editing, run relevant tests, and report the exact result. Respect changes "
                "made by other agents; if an exact edit conflicts, reread the file and preserve their work.\n\n"
                + fallback.DEV_PROMPT
            )
            return await self._answer(providers, system)

        async def _call(self, name: str, raw: str) -> str:
            if name not in names:
                return "Subagents cannot start nested agents or call non-coding tools."
            try:
                args = json.loads(raw or "{}")
                if not isinstance(args, dict):
                    return "Tool arguments must be an object."
            except json.JSONDecodeError:
                return "Tool arguments were invalid JSON."
            await self.on_tool(name, args)
            result = await asyncio.get_running_loop().run_in_executor(None, lambda: execute(name, args))
            with _records_lock:
                record["tool_calls"].append({"name": name, "args": args, "result": result[:1200]})
            return result

    async def on_delta(text):
        record["result"] = (record.get("result", "") + text)[-8000:]

    async def on_tool(name, args):
        return None

    async def on_done(info):
        pass

    async def on_permission(tool, args):
        return True

    brain = ScopedBrain(on_delta, on_tool, on_done, on_permission)
    brain.history = [{"role": "user", "content": f"Working directory: {project_path}\n\n{task_description}"}]
    result = await brain._run()
    diff = executor.git_diff().output if (project_path / ".git").exists() else ""
    return {"result": result, "error": "", "diff": diff}
