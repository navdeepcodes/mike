#!/usr/bin/env python3
"""Main benchmark runner - orchestrates the full benchmark suite."""
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add mike to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fallback import FallbackBrain

# Free tiers are slow (Groq: ~2 steps a minute), so both are adjustable.
BENCH_RUNS = int(os.environ.get("MIKE_BENCH_RUNS", "2"))
BENCH_TIMEOUT = int(os.environ.get("MIKE_BENCH_TIMEOUT", "480"))


class BenchmarkResult:
    """Container for a single benchmark run result."""
    def __init__(self, task_name: str, contestant: str, run_id: str):
        self.task_name = task_name
        self.contestant = contestant
        self.run_id = run_id
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.tool_calls: List[Dict[str, Any]] = []
        self.deltas: List[str] = []
        self.done_info: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.claimed_done_while_failing = False
        self.changed_forbidden_files = False
        self.test_results: Dict[str, Any] = {}

    def duration(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time

    def to_dict(self) -> Dict[str, Any]:
        passed = self.test_results.get("passed", 0)
        total = self.test_results.get("total", 0)
        return {
            "task": self.task_name,
            "contestant": self.contestant,
            "run_id": self.run_id,
            "duration_sec": round(self.duration(), 2),
            "tool_calls": len(self.tool_calls),
            "tool_types": list(set(t["name"] for t in self.tool_calls)),
            "tests_passed": passed,
            "tests_total": total,
            "pass_rate": round(passed / total * 100, 1) if total > 0 else 0,
            "claimed_done_while_failing": self.claimed_done_while_failing,
            "changed_forbidden_files": self.changed_forbidden_files,
            "error": self.error,
        }


class MikeRunner:
    """Runs Mike's FallbackBrain on a task in a temp directory."""

    def __init__(self, task_dir: Path, forbidden_paths: List[Path] = None):
        self.task_dir = task_dir
        self.forbidden_paths = forbidden_paths or []
        self.result: Optional[BenchmarkResult] = None
        self._brain: Optional[FallbackBrain] = None

    async def run(self, task_text: str, task_name: str, run_id: str) -> BenchmarkResult:
        """Run a single task with Mike's FallbackBrain."""
        self.result = BenchmarkResult(task_name, "Mike", run_id)

        # Create task directory
        self.task_dir.mkdir(parents=True, exist_ok=True)

        # Build the prompt with context about the working directory
        prompt = f"""Working directory: {self.task_dir}

{task_text}

You have full access to the working directory. Use the tools to create files, run commands, and verify your work. Run tests to confirm everything works before saying you're done."""

        async def on_delta(text: str):
            self.result.deltas.append(text)

        async def on_tool(name: str, inp: Dict[str, Any]):
            self.result.tool_calls.append({"name": name, "input": inp, "time": time.time()})
            # Check for forbidden file changes
            if name in ("write_file", "edit_file"):
                path = inp.get("path", "")
                for forbidden in self.forbidden_paths:
                    try:
                        if Path(path).resolve().is_relative_to(forbidden.resolve()):
                            self.result.changed_forbidden_files = True
                    except Exception:
                        pass

        async def on_done(info: Dict[str, Any]):
            self.result.end_time = time.time()
            self.result.done_info = info
            if info.get("error"):           # e.g. every model rate-limited: not a real finish
                self.result.error = str(info["error"])[:200]

        async def on_permission(tool: str, inp: Dict[str, Any]) -> bool:
            return True  # Auto-approve

        # Like a user saying "work on this project": the coding tools then default to the task folder, not Mike's own.
        import projects
        projects.use(str(self.task_dir))
        self._brain = FallbackBrain(on_delta, on_tool, on_done, on_permission)

        try:
            await self._brain.send(prompt)
            # Wait for completion with timeout
            await asyncio.wait_for(self._wait_for_done(), timeout=BENCH_TIMEOUT)
        except asyncio.TimeoutError:
            self.result.error = f"Timeout after {BENCH_TIMEOUT // 60} minutes"
            self.result.end_time = time.time()
            if self._brain:
                await self._brain.interrupt()
        except Exception as e:
            self.result.error = str(e)
            self.result.end_time = time.time()

        return self.result

    async def _wait_for_done(self):
        """Wait for the brain to signal done."""
        while self.result and not self.result.done_info:
            await asyncio.sleep(0.5)

    async def interrupt(self):
        if self._brain:
            await self._brain.interrupt()


# Task definitions (the text given to the model - tests are HIDDEN)
# The task text each model gets (tests stay hidden). Same wording as the Nov 2026 Claude Code vs Mike run,
# which states the contract the hidden tests check (routes, file names, entry points).
COMMON = "\n\nWork only inside the current directory. Use `python` and `pip` from PATH (an isolated venv). Verify your work by running tests before you say you're done."
TASKS = {
    'task1_fastapi_todo': 'Build a FastAPI todo API in main.py (the FastAPI instance must be named `app`).\n- SQLite storage (a file DB in the current directory is fine).\n- JWT auth: POST /auth/signup and POST /auth/login, both take JSON {"username", "password"} and return {"access_token": ...}.\n  Signing up with an existing username returns 400. Bad login returns 401.\n- Todos, all requiring "Authorization: Bearer <token>" (missing/invalid token -> 401):\n  POST /todos/ (JSON title, optional description, optional completed), GET /todos/ (query: skip, limit, completed),\n  GET /todos/{id}, PUT /todos/{id}, DELETE /todos/{id}. Missing todo or another user\'s todo -> 404.\n- Each todo has: id, title, description, completed, created_at, owner_id.\n- requirements.txt with all dependencies. Write your own tests.' + COMMON,
    'task2_debug_module': 'The 4-file Python module in the current directory (calculator.py, processor.py, models.py, utils.py)\nhas bugs, and test_module.py is failing. Fix the source files so all tests pass. Do not modify test_module.py.\nThe bugs include an off-by-one error, mutable default arguments, a timezone bug (datetimes must be timezone-aware UTC),\na threading race condition, and silently swallowed exceptions.' + COMMON,
    'task3_refactor_script': 'messy_script.py (~300 lines) and its tests test_messy.py are in the current directory.\nRefactor it into 3 clean modules: parser.py (input parsing, public entry `parse`), processor.py (core logic, public\nentry `process`), formatter.py (output formatting, public entry `format`). Replace messy_script.py with a thin main.py\n(with a `main()` function) that uses them, then delete messy_script.py. Update test_messy.py to import from the new\nmodules and keep every one of its tests passing. No duplicate code; one responsibility per module.' + COMMON,
    'task4_expression_evaluator': 'Write evaluator.py with evaluate(expression: str) -> float that handles:\n+, -, *, /, ^ (power, right-associative), parentheses, unary minus (-5, -(3+2)), functions max(a,b), min(a,b), abs(x),\nsqrt(x), pow(x,y), and arbitrary whitespace. Raise ValueError with a helpful message for: division by zero, unmatched\nparentheses, unknown tokens, invalid syntax, and empty input. Also write test_evaluator.py with thorough tests.\nDo not use eval().' + COMMON,
    'task5_duplicate_finder': "Build a CLI in main.py that finds duplicate files: `python main.py FOLDER [--delete] [--dry-run]`.\n- Recursive traversal; duplicates by file content hash (not name). Empty files count as duplicates of each other.\n- Don't follow symlinks (and never loop on symlink cycles). Skip unreadable files gracefully (no crash).\n- Default is a dry run: just report. --delete removes all but one file per group; --dry-run explicitly shows what would be deleted.\n- Output: each group of duplicates with file paths and sizes. Exit code 0 on success.\n- Show progress for large folders (on stderr). Write tests." + COMMON,
}


# Test runners for each task
TEST_MODULES = {
    "task1_fastapi_todo": "bench.test_task1_fastapi",
    "task2_debug_module": "bench.test_task2_debug",
    "task3_refactor_script": "bench.test_task3_refactor",
    "task4_expression_evaluator": "bench.test_task4_evaluator",
    "task5_duplicate_finder": "bench.test_task5_duplicates",
}

# Setup functions
SETUP_FUNCTIONS = {
    "task1_fastapi_todo": "bench.fixtures.task1_fastapi_setup.setup_task1",
    "task2_debug_module": "bench.fixtures.task2_debug_setup.setup_task2",
    "task3_refactor_script": "bench.fixtures.task3_refactor_setup.setup_task3",
    "task4_expression_evaluator": "bench.fixtures.task4_evaluator_setup.setup_task4",
    "task5_duplicate_finder": "bench.fixtures.task5_duplicates_setup.setup_task5",
}


def import_setup_function(func_path: str):
    """Import a setup function."""
    module_path, func_name = func_path.rsplit(".", 1)
    module = __import__(module_path, fromlist=[func_name])
    return getattr(module, func_name)


def import_test_module(module_path: str):
    """Import a test module."""
    return __import__(module_path, fromlist=["*"])


def prepare_task(task_name: str, task_dir: Path):
    """Per-task touches after the fixture is written (kept the same as the Nov 2026 run)."""
    if task_name == "task2_debug_module":
        # As in the Nov 2026 run: drop the fixture's "# BUG: should be ..." comments (they give the answers away)
        # and put the visible failing tests (test_module.py) next to the code, as the task text says.
        for f in task_dir.glob("*.py"):
            src = re.sub(r"[ \t]*#\s*BUG:.*", "", f.read_text())
            f.write_text(re.sub(r"\s*BUG:[^\"\n]*", "", src))
        visible = (Path(__file__).parent / "test_task2_debug.py").read_text()
        visible = re.sub(r"task_dir = Path\(os\.environ.*\n.*\n", "task_dir = Path(__file__).parent\n", visible)
        (task_dir / "test_module.py").write_text(visible)


async def run_mike_task(task_text: str, task_name: str, run_id: str, base_dir: Path) -> BenchmarkResult:
    """Run a single Mike task in a fresh temp directory."""
    task_dir = base_dir / f"{task_name}_{run_id}"

    # Run setup first
    setup_func = import_setup_function(SETUP_FUNCTIONS[task_name])
    setup_func(task_dir)
    prepare_task(task_name, task_dir)
    # Each run gets its own venv on PATH, as the task text says (it also sees Mike's packages, read-only).
    from self_coding import DevSession, ToolExecutor
    ToolExecutor(task_dir, DevSession("bench", "setup", task_dir))._project_venv_bin()

    runner = MikeRunner(task_dir)
    return await runner.run(task_text, task_name, run_id)


def run_hidden_tests(task_name: str, task_dir: Path) -> Dict[str, Any]:
    """Run hidden tests for a task and return results."""
    test_module_path = TEST_MODULES[task_name]
    test_file = Path(__file__).resolve().parent.parent / (test_module_path.replace(".", "/") + ".py")

    if not test_file.exists():
        return {
            "passed": 0, "failed": 0, "errors": 0, "total": 0,
            "returncode": 1, "output_tail": f"Test file not found: {test_file}"
        }

    # Use mike's venv python for tests
    # The task's own .venv if Mike made one (it holds what he installed, and sees Mike's packages too).
    # The benchmark's Python runs the tests (it has pytest-asyncio, see bench/requirements.txt); whatever the
    # project installed into its own venv is added to the path so its code imports.
    mike_python = sys.executable
    import glob
    project_libs = [p for v in (".venv", "venv", "env") for p in glob.glob(str(task_dir / v / "lib" / "python*" / "site-packages"))
                    + glob.glob(str(task_dir / v / "Lib" / "site-packages"))]

    # Run pytest programmatically - the test file imports from task_dir using sys.path
    # We need to add the task_dir to PYTHONPATH so the tests can import the project modules
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(task_dir), *project_libs, env.get("PYTHONPATH", "")])
    env["MIKE_BENCH_TASK_DIR"] = str(task_dir)        # the hidden tests check this folder

    result = subprocess.run([
        mike_python, "-m", "pytest",
        str(test_file),
        "-rA", "--tb=short", "-p", "no:cacheprovider", "-o", "asyncio_mode=auto",
        "--import-mode=importlib"
    ], capture_output=True, text=True, cwd=task_dir, timeout=120, env=env)

    # Parse results
    output = result.stdout + result.stderr

    # Count passed/failed
    # Read pytest's own summary line ("3 failed, 5 passed, 1 error in 0.4s"): per-test lines vary with flags.
    import re
    summary = next((l for l in reversed(output.splitlines())
                    if re.search(r"\b(passed|failed|errors?|skipped|no tests ran)\b.* in [\d.]+s", l)), "")
    def n(word):
        m = re.search(rf"(\d+) {word}", summary)
        return int(m.group(1)) if m else 0
    passed, failed, errors = n("passed"), n("failed"), n("errors?")
    failed += n("skipped")       # tests skip when a required file is missing: that's unfinished work, not a pass

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total": passed + failed + errors,
        "returncode": result.returncode,
        "output_tail": output[-2000:] if output else ""
    }


async def run_all_mike_tasks(base_dir: Path) -> List[BenchmarkResult]:
    """Run all 5 tasks twice each (use better result)."""
    all_results = []

    only = [t for t in os.environ.get("MIKE_BENCH_TASKS", "").split(",") if t]
    for task_name, task_text in TASKS.items():
        if only and task_name not in only:
            continue
        print(f"\n{'='*60}")
        print(f"Running {task_name} for Mike...")
        print(f"{'='*60}")

        best_result = None
        for run_num in range(1, BENCH_RUNS + 1):
            run_id = f"{task_name}_run{run_num}"
            print(f"  Run {run_num}/{BENCH_RUNS}...")

            result = await run_mike_task(task_text, task_name, run_id, base_dir)
            print(f"    Duration: {result.duration():.1f}s, Tools: {len(result.tool_calls)}, Error: {result.error or 'OK'}")

            # Run hidden tests
            task_dir = base_dir / f"{task_name}_{run_id}"
            test_results = run_hidden_tests(task_name, task_dir)
            result.test_results = test_results
            print(f"    Tests: {test_results['passed']}/{test_results['total']} passed")

            # Check if claimed done while tests failing
            if result.done_info and not result.error:
                if test_results["failed"] > 0 or test_results["errors"] > 0:
                    result.claimed_done_while_failing = True

            if best_result is None:
                best_result = result
            elif result.error is None and best_result.error is not None:
                best_result = result
            elif result.error is None and best_result.error is None:
                # Both succeeded, prefer higher pass rate, then faster
                if result.test_results.get("passed", 0) > best_result.test_results.get("passed", 0):
                    best_result = result
                elif result.test_results.get("passed", 0) == best_result.test_results.get("passed", 0):
                    if result.duration() < best_result.duration():
                        best_result = result

        all_results.append(best_result)

    return all_results


def print_comparison_table(results: List[BenchmarkResult]):
    """Print a formatted comparison table."""
    print("\n\n" + "="*100)
    print("BENCHMARK RESULTS: MIKE (FallbackBrain with free models)")
    print("="*100)

    header = f"{'Task':<30} {'Duration':>8} {'Tools':>6} {'Passed':>8} {'Total':>6} {'Rate':>6} {'Claimed Done While Failing':>25} {'Error'}"
    print(header)
    print("-" * 100)

    for r in results:
        d = r.to_dict()
        error_str = d['error'][:30] if d['error'] else "OK"
        claimed = "YES" if d['claimed_done_while_failing'] else "NO"
        print(f"{d['task']:<30} {d['duration_sec']:>7.1f}s {d['tool_calls']:>6} {d['tests_passed']:>8} {d['tests_total']:>6} {d['pass_rate']:>5.1f}% {claimed:>25} {error_str}")

    print("-" * 100)

    # Summary
    total_tasks = len(results)
    total_passed = sum(r.test_results.get("passed", 0) for r in results)
    total_tests = sum(r.test_results.get("total", 0) for r in results)
    avg_duration = sum(r.duration() for r in results) / total_tasks
    total_tools = sum(len(r.tool_calls) for r in results)
    false_claims = sum(1 for r in results if r.claimed_done_while_failing)

    print(f"\nSUMMARY:")
    print(f"  Tasks completed: {total_tasks}")
    print(f"  Overall pass rate: {total_passed}/{total_tests} ({total_passed/total_tests*100:.1f}%)" if total_tests > 0 else "  Overall pass rate: N/A")
    print(f"  Average duration: {avg_duration:.1f}s")
    print(f"  Total tool calls: {total_tools}")
    print(f"  False 'done' claims: {false_claims}")


async def main():
    base_dir = Path("/tmp/mike_bench_main")
    base_dir.mkdir(parents=True, exist_ok=True)

    print("Starting Mike benchmark run (5 tasks, 2 runs each)...")
    results = await run_all_mike_tasks(base_dir)

    # Save results
    output = {
        "contestant": "Mike",
        "timestamp": time.time(),
        "results": [r.to_dict() for r in results]
    }

    with open(base_dir / "mike_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print_comparison_table(results)

    return results


if __name__ == "__main__":
    asyncio.run(main())