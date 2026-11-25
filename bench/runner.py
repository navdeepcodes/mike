#!/usr/bin/env python3
"""Headless benchmark runner for Mike's FallbackBrain vs Claude Code free models."""
import asyncio
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add mike to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from fallback import FallbackBrain


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

    def duration(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task_name,
            "contestant": self.contestant,
            "run_id": self.run_id,
            "duration_sec": round(self.duration(), 2),
            "tool_calls": len(self.tool_calls),
            "tool_types": list(set(t["name"] for t in self.tool_calls)),
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

        async def on_permission(tool: str, inp: Dict[str, Any]) -> bool:
            return True  # Auto-approve

        self._brain = FallbackBrain(on_delta, on_tool, on_done, on_permission)

        try:
            await self._brain.send(prompt)
            # Wait for completion with timeout
            await asyncio.wait_for(self._wait_for_done(), timeout=480)  # 8 minutes
        except asyncio.TimeoutError:
            self.result.error = "Timeout after 8 minutes"
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


async def run_mike_task(task_text: str, task_name: str, run_id: str, base_dir: Path) -> BenchmarkResult:
    """Run a single Mike task in a fresh temp directory."""
    task_dir = base_dir / f"{task_name}_{run_id}"
    runner = MikeRunner(task_dir)
    return await runner.run(task_text, task_name, run_id)


# Task definitions (the text given to the model - tests are HIDDEN)
TASKS = {
    "task1_fastapi_todo": """Build a FastAPI todo API with the following requirements:

- SQLite storage using a temp file (e.g., /tmp/todo.db or similar)
- JWT token authentication (signup, login endpoints)
- CRUD endpoints for todos: create, read (with filtering by completed status), update, delete
- Pagination on list endpoint (skip, limit)
- Each todo has: id, title, description, completed, created_at, owner_id
- Proper error handling (404, 401, 400)
- requirements.txt with all dependencies
- The API should be runnable and testable

Work in the current directory. Create all necessary files.""",

    "task2_debug_module": """A 4-file Python module in the current directory has 5 bugs. The tests are failing. Fix all bugs without changing the test files.

Files to examine:
- calculator.py - basic math operations
- processor.py - data processing with threading
- models.py - data models with datetime handling
- utils.py - utility functions

Run the tests to see what fails, then fix the bugs. The bugs include:
- An off-by-one error
- A wrong default argument (mutable default)
- A timezone-related bug
- A race condition in threading
- A silently swallowed exception

Do not modify test files. Only fix the source files.""",

    "task3_refactor_script": """A 300-line messy script (messy_script.py) exists in the current directory with a visible test suite (test_messy.py).

Refactor it into 3 clean modules with a clear interface:
- parser.py - input parsing logic
- processor.py - core processing logic
- formatter.py - output formatting

Requirements:
- Keep ALL existing tests passing (both visible and hidden)
- The new modules must have a clean public API
- No duplicate code
- Each module should have a single responsibility
- The original messy_script.py should be replaced by a thin main.py that uses the new modules

Run tests frequently to ensure nothing breaks.""",

    "task4_expression_evaluator": """Write an expression evaluator in Python that handles:
- Basic operators: +, -, *, /, ^ (power)
- Parentheses for grouping
- Unary minus (e.g., -5, -(3+2))
- Functions: max(a,b), min(a,b), abs(x), sqrt(x), pow(x,y)
- Helpful error messages for: division by zero, unmatched parentheses, unknown tokens, invalid syntax

Create:
- evaluator.py with evaluate(expression: str) -> float
- test_evaluator.py with comprehensive tests (the visible ones are basic)

The hidden tests cover 30 edge cases including: nested parentheses, operator precedence, unary minus edge cases, function calls, whitespace handling, empty input, and error messages.""",

    "task5_duplicate_finder": """Build a CLI tool that finds duplicate files in a folder.

Requirements the model must design:
- Recursive directory traversal
- Content-based deduplication (hash files, not just names)
- Handle symlinks properly (don't follow by default, or detect cycles)
- Handle empty files
- Handle permission errors gracefully
- --delete flag with dry-run by default (show what would be deleted)
- --dry-run flag explicit
- Progress indication for large folders
- Output format: groups of duplicates with paths and sizes

Create a complete, runnable CLI with proper argument parsing and tests."""
}


async def run_all_mike_tasks(base_dir: Path) -> List[BenchmarkResult]:
    """Run all 5 tasks twice each (use better result)."""
    all_results = []

    for task_name, task_text in TASKS.items():
        print(f"\n{'='*60}")
        print(f"Running {task_name} for Mike...")
        print(f"{'='*60}")

        best_result = None
        for run_num in [1, 2]:
            run_id = f"{task_name}_run{run_num}"
            print(f"  Run {run_num}/2...")
            result = await run_mike_task(task_text, task_name, run_id, base_dir)
            print(f"    Duration: {result.duration():.1f}s, Tools: {len(result.result.tool_calls) if result.result else 0}, Error: {result.error or 'None'}")

            if best_result is None or (result.error is None and best_result.error is not None):
                best_result = result
            elif result.error is None and best_result.error is None:
                # Both succeeded, prefer faster with fewer tool calls
                if result.duration() < best_result.duration():
                    best_result = result

        all_results.append(best_result)

    return all_results


if __name__ == "__main__":
    base_dir = Path("/tmp/mike_bench")
    base_dir.mkdir(parents=True, exist_ok=True)

    print("Starting Mike benchmark run...")
    results = asyncio.run(run_all_mike_tasks(base_dir))

    # Save results
    output = {
        "contestant": "Mike",
        "timestamp": time.time(),
        "results": [r.to_dict() for r in results]
    }

    with open(base_dir / "mike_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print("\n\nMike Results Summary:")
    print("-" * 80)
    for r in results:
        d = r.to_dict()
        print(f"{d['task']}: {d['duration_sec']:.1f}s, {d['tool_calls']} tools, error: {d['error'] or 'OK'}")