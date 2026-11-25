import json
import pytest
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

import fallback
import mike_tools
from self_coding import DevSession, ProjectDetector, SelfCodingAgent, TaskManager, ToolExecutor


def test_autonomous_loop_executes_model_driver_and_requires_passing_tests(tmp_path):
    source = tmp_path / "calculator.py"
    source.write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    events = []

    async def record_delta(text):
        events.append(text)

    async def record_tool(name, args):
        events.append((name, args))

    async def noop_done(info):
        pass

    async def allow_permission(tool, args):
        return True

    async def drive(prompt, call_tool):
        events.append(prompt)
        await call_tool("code_edit", {"path": "calculator.py", "old": "return a - b", "new": "return a + b"})
        await call_tool("code_test", {})
        return "Implemented addition and ran the test."

    result = asyncio.run(SelfCodingAgent(
        tmp_path, record_delta, record_tool, noop_done, allow_permission, model_driver=drive
    ).run("Fix the broken program", max_steps=10, task_dir=str(tmp_path / "task-state")))

    assert result["blocked"] is False, json.dumps(result, default=str)[:3000]
    assert result["changes"] == 1
    assert result["verification"]["tests"]["status"] == "passed"
    assert result["task_persistence"]["status"] == "completed"
    assert TaskManager(tmp_path / "task-state").task_history[0]["changes"] == 1
    assert source.read_text() == "def add(a, b):\n    return a + b\n"
    assert any(isinstance(event, tuple) and event[0] == "code_edit" for event in events)


def test_autonomous_loop_does_not_complete_when_final_tests_fail(tmp_path):
    (tmp_path / "broken.py").write_text("def value():\n    return 0\n")
    (tmp_path / "test_broken.py").write_text("from broken import value\n\ndef test_value():\n    assert value() == 1\n")

    async def noop(*args):
        pass

    async def allow(*args):
        return True

    async def no_op_driver(prompt, call_tool):
        return "I made no changes."

    result = asyncio.run(SelfCodingAgent(
        tmp_path, noop, noop, noop, allow, model_driver=no_op_driver
    ).run("Fix the broken test", task_dir=str(tmp_path / "task-state")))

    assert result["blocked"] is True
    assert result["verification"]["tests"]["status"] == "failed"
    assert result["task_persistence"]["status"] == "blocked"


def test_autonomous_loop_resumes_saved_progress_after_interruption(tmp_path):
    source = tmp_path / "calculator.py"
    source.write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )

    async def noop(*args):
        pass

    async def allow(*args):
        return True

    async def interrupted_driver(prompt, call_tool):
        await call_tool("code_edit", {"path": "calculator.py", "old": "return a - b", "new": "return a + b"})
        raise RuntimeError("simulated process interruption")

    task_dir = str(tmp_path / "task-state")
    first = asyncio.run(SelfCodingAgent(
        tmp_path, noop, noop, noop, allow, model_driver=interrupted_driver
    ).run("Fix addition", task_dir=task_dir))

    resumed_prompts = []

    async def resumed_driver(prompt, call_tool):
        resumed_prompts.append(prompt)
        await call_tool("code_test", {})
        return "Verified the saved implementation."

    second = asyncio.run(SelfCodingAgent(
        tmp_path, noop, noop, noop, allow, model_driver=resumed_driver
    ).run("Fix addition", task_dir=task_dir))

    assert first["task_persistence"]["status"] == "blocked"
    assert "Saved progress from the previous run" in resumed_prompts[0]
    assert second["blocked"] is False, json.dumps(second, default=str)[:3000]
    assert second["verification"]["tests"]["status"] == "passed"
    assert second["task_persistence"]["status"] == "completed"


def test_autonomous_tool_rejects_paths_outside_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("safe = True\n")

    async def noop(*args):
        pass

    async def allow(*args):
        return True

    async def attempt_escape(prompt, call_tool):
        return await call_tool("code_write", {"path": str(outside), "content": "pwned = True\n"})

    result = asyncio.run(SelfCodingAgent(
        project, noop, noop, noop, allow, model_driver=attempt_escape
    ).run("Try the scoped writer", task_dir=str(project / "task-state")))

    assert result["changes"] == 0
    assert outside.read_text() == "safe = True\n"


def test_task_manager_persists_notes_and_progress(tmp_path):
    manager = TaskManager(tmp_path / "task")
    manager.active_goal = "repair parser"
    manager.completed_steps = [1, 2]
    manager.notes = "parser fixed; run regression suite next"
    manager.save_state()

    restored = TaskManager(tmp_path / "task")
    assert restored.active_goal == "repair parser"
    assert restored.completed_steps == [1, 2]
    assert restored.notes == "parser fixed; run regression suite next"
    assert restored.status == "active"


def test_task_tools_save_and_load_per_project(tmp_path, monkeypatch):
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    monkeypatch.setattr(mike_tools, "_get_project_path", lambda: project_a)
    monkeypatch.setattr(mike_tools, "TASK_STATE_ROOT", tmp_path / "states")

    saved = mike_tools.code_task_save("repair parser", 2, "resume at tests")
    loaded = mike_tools.code_task_load()
    assert "through step 2" in saved
    assert "Completed steps: [1, 2]" in loaded
    assert "Notes: resume at tests" in loaded

    monkeypatch.setattr(mike_tools, "_get_project_path", lambda: project_b)
    assert mike_tools.code_task_load() == "No saved task state found."


def test_fallback_registers_context_and_persistence_tools():
    registered = {func.__name__ for func in fallback.DEV_FUNCS}
    assert {
        "code_search_relevant",
        "code_task_save",
        "code_task_load",
        "code_subagent_spawn",
        "code_subagent_results",
    } <= registered
    assert sum(name.startswith("code_") for name in registered) == 23


def test_subagent_tools_keep_background_job_status(tmp_path, monkeypatch):
    import json
    import subagents

    monkeypatch.setattr(mike_tools, "_get_project_path", lambda: tmp_path)

    def finish_immediately(agent_id):
        subagents._update(agent_id, status="completed", result="done")

    monkeypatch.setattr(subagents, "_thread_main", finish_immediately)
    response = mike_tools.code_subagent_spawn("change parser")
    agent_id = response.split()[2]
    state = json.loads(mike_tools.code_subagent_results(agent_id))
    assert state[0]["agent_id"] == agent_id
    assert state[0]["status"] == "completed"
    assert state[0]["result"] == "done"


def test_edit_invalidates_python_bytecode_cache(tmp_path):
    source = tmp_path / "module.py"
    source.write_text("value = 1\n")
    executor = ToolExecutor(tmp_path, DevSession("edit", "edit", tmp_path))
    import py_compile
    py_compile.compile(str(source), doraise=True)
    cache_dir = tmp_path / "__pycache__"
    assert list(cache_dir.glob("module.*.pyc"))

    result = executor.edit_file("module.py", "value = 1", "value = 2")
    assert result.success
    assert not list(cache_dir.glob("module.*.pyc"))


def test_detector_prefers_pytest_for_function_style_tests(tmp_path, monkeypatch):
    (tmp_path / "test_example.py").write_text("def test_example():\n    assert True\n")
    monkeypatch.setattr("self_coding.shutil.which", lambda name: "/usr/bin/pytest" if name == "pytest" else None)
    detector = ProjectDetector(tmp_path)
    assert detector._detect_test_framework() == "pytest"
    assert detector._get_test_command().startswith("python -m pytest")


def test_concurrent_same_region_edits_report_conflict(tmp_path, monkeypatch):
    target = tmp_path / "module.py"
    target.write_text("value = 1\n")
    original_read_text = Path.read_text

    def delayed_read(path, *args, **kwargs):
        result = original_read_text(path, *args, **kwargs)
        if path == target:
            time.sleep(0.05)
        return result

    monkeypatch.setattr(Path, "read_text", delayed_read)
    first = ToolExecutor(tmp_path, DevSession("a", "edit", tmp_path))
    second = ToolExecutor(tmp_path, DevSession("b", "edit", tmp_path))
    start = threading.Barrier(3)

    def edit(executor, replacement):
        start.wait()
        return executor.edit_file("module.py", "value = 1", replacement)

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(edit, first, "value = 2"), pool.submit(edit, second, "value = 3")]
        start.wait()
        results = [job.result() for job in jobs]

    assert sum(result.success for result in results) == 1
    assert target.read_text() in {"value = 2\n", "value = 3\n"}
    assert any("Text not found" in (result.error or "") for result in results)


def test_project_commands_never_touch_mikes_venv(tmp_path):
    """A project's commands get the project's own venv; Mike's packages are visible but never installed into."""
    import config
    ex = ToolExecutor(tmp_path, DevSession("t", "t", tmp_path))
    out = ex._run_command('python -c "import sys, pytest; print(sys.prefix)"')
    assert out.success, out.error or out.output
    assert Path(out.output.strip().splitlines()[-1]).resolve() == (tmp_path / ".venv").resolve()
    assert str(config.ROOT / ".venv") not in ex._run_command("echo $PATH").output


def test_writes_stay_inside_the_project(tmp_path):
    ex = ToolExecutor(tmp_path / "proj", DevSession("t", "t", tmp_path / "proj"))
    (tmp_path / "proj").mkdir()
    assert ex.write_file("ok.py", "x = 1\n").success
    assert not ex.write_file(str(tmp_path / "outside.py"), "x = 1\n").success
    assert not ex.write_file("../escape.py", "x = 1\n").success
    assert not (tmp_path / "outside.py").exists() and not (tmp_path / "escape.py").exists()


def test_plain_file_tools_resolve_relative_paths_in_the_project(tmp_path, monkeypatch):
    import config, projects
    monkeypatch.setattr(projects, "current", lambda: tmp_path)
    fallback.write_file("made.txt", "hi")
    assert (tmp_path / "made.txt").read_text() == "hi"
    assert not (config.ROOT / "made.txt").exists()
    for where in ("", str(tmp_path)):           # home (no folder given) and a project: never Mike's venv
        assert str(config.ROOT / ".venv") not in fallback.run_command("echo $PATH", where)


def test_coding_turns_send_a_lean_toolset():
    """Every tool description is resent each step: free tiers (Groq 8k tokens/min, Cloudflare daily) can't afford 58."""
    import json, re
    brain = fallback.FallbackBrain.__new__(fallback.FallbackBrain)
    coding = brain._tool_schemas(True)
    names = {s["function"]["name"] for s in coding}
    assert names == fallback.CODING_TOOLS
    assert len(json.dumps(coding)) // 4 < 1500
    taught = set(re.findall(r"\b([a-z]+_[a-z_]+)\b", fallback.DEV_PROMPT)) & set(fallback.TOOL_FUNCS)
    assert taught <= names, f"DEV_PROMPT names tools a coding turn isn't given: {taught - names}"
    assert not names & {"press_key", "type_text", "media", "set_reminder", "ui"}


def test_waits_for_the_first_rate_limited_model_to_free_up():
    import time
    g = {"id": "groq"}
    lanes = [(g, "a"), (g, "b")]
    now = time.time()
    assert fallback._soonest_free({"groq/a": now + 30, "groq/b": now + 10}, lanes) == pytest.approx(10, abs=1)
    assert fallback._soonest_free({"groq/a": now + 30}, lanes) is None          # b is free: no waiting
    assert fallback._soonest_free({"groq": now + 6 * 3600}, lanes) > 90           # daily quota spent: don't wait


def test_long_coding_turns_are_compacted():
    import json
    big = "x" * 5000
    msgs = [{"role": "user", "content": "build it"}]
    for i in range(6):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function",
                     "function": {"name": "write_file", "arguments": json.dumps({"path": f"f{i}.py", "content": big})}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": big})
    out = fallback._compact(msgs)
    assert len(json.dumps(out)) < len(json.dumps(msgs)) * 0.6                # 3 of 6 steps shrunk
    assert out[-1]["content"] == big                                      # newest result whole
    assert "trimmed" in out[2]["content"]                                  # oldest result cut
    first_args = json.loads(out[1]["tool_calls"][0]["function"]["arguments"])
    assert first_args["path"] == "f0.py" and "already applied" in first_args["content"]
    assert msgs[2]["content"] == big                                       # history itself untouched


def test_capability_is_learned_from_results_not_names(tmp_path, monkeypatch):
    import model_stats
    monkeypatch.setattr(model_stats, "PATH", tmp_path / "stats.json")
    monkeypatch.setattr(model_stats, "_stats", {})
    assert not model_stats.step_ok("Failed (exit code 1): boom")
    assert not model_stats.step_ok("Not changed: that text isn't in a.py exactly")
    assert model_stats.step_ok("Wrote 120 characters to a.py.")
    small, big = model_stats.capability("x/tiny-8b", 8), model_stats.capability("x/huge-120b", 120)
    assert small < big                                                   # before any data: size is the guess
    for _ in range(30):
        model_stats.record("x/tiny-8b", True)
        model_stats.record("x/huge-120b", False)
    assert model_stats.capability("x/tiny-8b", 8) > model_stats.capability("x/huge-120b", 120)   # results win


def test_coding_starts_fast_and_moves_up_after_struggling(tmp_path, monkeypatch):
    import time, model_stats
    monkeypatch.setattr(model_stats, "PATH", tmp_path / "stats.json")
    monkeypatch.setattr(model_stats, "_stats", {"g/a": {"ok": 50, "bad": 0}, "g/b": {"ok": 40, "bad": 10}, "g/c": {"ok": 5, "bad": 45}})
    g = {"id": "g"}
    brain = fallback.FallbackBrain.__new__(fallback.FallbackBrain)
    brain.dev_until, brain._weak, brain._lat = time.time() + 60, set(), {"a": 9.0, "b": 1.0, "c": 0.5}
    lanes = [(g, "a"), (g, "b"), (g, "c")]
    assert [m for _, m in brain._order(lanes)] == ["b", "a", "c"]     # fastest of the able ones; weak one last
    brain._weak = {"g/b"}
    assert [m for _, m in brain._order(lanes)][0] == "a"                # after b struggled: the most capable
    brain.dev_until = 0
    assert brain._order(lanes) == lanes                                 # chat keeps its usual order


def test_history_is_squeezed_to_fit_a_learned_limit():
    import json
    big = "y" * 4000
    msgs = [{"role": "user", "content": "build it"}]
    for i in range(8):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function",
                     "function": {"name": "write_file", "arguments": json.dumps({"path": f"f{i}.py", "content": big})}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": big})
    assert fallback._tokens(fallback._compact(msgs)) > 3000
    squeezed = fallback._compact(msgs, budget=3000)
    assert fallback._tokens(squeezed) <= 3000
    assert len(squeezed) == len(msgs)                                    # every step still there, just shorter


def test_limits_and_waits_are_read_from_every_providers_errors():
    lim, wait = fallback._limit_from_error, fallback._retry_after
    groq = ('HTTP 429 {"error":{"message":"Rate limit reached for model `m` on tokens per minute (TPM): Limit 8000, '
            'Used 722, Requested 7857. Please try again in 4.34s."}}')
    groq_day = "HTTP 429 Rate limit reached on tokens per day (TPD): Limit 200000, Used 197345. Please try again in 12m12.6s."
    openai = "HTTP 400 This model's maximum context length is 16385 tokens. However, your messages resulted in 17000 tokens."
    gemini = ('HTTP 400 The input token count (40000) exceeds the maximum number of tokens allowed (32768). '
              '"retryDelay": "33s"')
    bare = "HTTP 413 Payload Too Large"
    assert lim(groq, 7857) == 8000 and wait(groq) == 4.34
    assert lim(groq_day, 4000) is None and wait(groq_day) == 12 * 60 + 12.6      # a daily cap isn't a size limit
    assert lim(openai, 17000) == 16385
    assert lim(gemini, 40000) == 32768 and wait(gemini) == 33
    assert lim(bare, 10000) == 7500                                               # no number given: learn from what we sent
    assert lim("HTTP 500 internal error", 5000) is None
    assert wait("HTTP 429 slow down (try again in 20s)") == 20                    # the retry-after header, any provider
    assert wait("Please retry in 250ms") == 0.25
