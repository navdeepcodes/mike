#!/usr/bin/env python3
"""Setup fixture for Task 2 - creates the buggy 4-file module."""
import tempfile
from pathlib import Path


def setup_task2(base_dir: Path):
    """Create the buggy module files for debugging task."""
    base_dir.mkdir(parents=True, exist_ok=True)

    # calculator.py - has off-by-one bug in sum_range
    calculator_py = '''"""Calculator module with bugs."""
def sum_range(start: int, end: int) -> int:
    """Sum integers from start to end inclusive. BUG: off-by-one."""
    total = 0
    for i in range(start, end):  # BUG: should be range(start, end + 1)
        total += i
    return total


def factorial(n: int) -> int:
    """Compute factorial."""
    if n <= 1:
        return 1
    return n * factorial(n - 1)


def average(numbers: list) -> float:
    """Compute average. BUG: mutable default argument."""
    return sum(numbers) / len(numbers) if numbers else 0
'''

    # processor.py - has race condition and silent exception swallow
    processor_py = '''"""Processor module with threading bugs."""
import threading
from typing import List, Callable, Dict, Any


def process_items_thread_safe(items: List[int], num_threads: int = 2) -> Dict[str, Any]:
    """Process items in parallel. BUG: race condition on shared counter."""
    counter = {"processed": 0, "sum": 0}
    lock = threading.Lock()

    def worker(chunk: List[int]):
        for item in chunk:
            # BUG: No lock around counter updates
            counter["processed"] += 1
            counter["sum"] += item

    # Split items among threads
    chunk_size = max(1, len(items) // num_threads)
    threads = []
    for i in range(0, len(items), chunk_size):
        chunk = items[i:i + chunk_size]
        t = threading.Thread(target=worker, args=(chunk,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    return counter


def process_with_callback(items: List[int], callback: Callable[[int], int]) -> Dict[str, int]:
    """Process items with callback. BUG: exceptions silently swallowed."""
    import threading
    results = {"succeeded": 0, "failed": 0}

    def worker(item: int):
        try:
            callback(item)
            results["succeeded"] += 1
        except Exception:
            # BUG: Exception silently swallowed, no increment of failed
            pass

    threads = [threading.Thread(target=worker, args=(item,)) for item in items]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results
'''

    # models.py - has timezone bug and mutable default argument
    models_py = '''"""Data models with bugs."""
from datetime import datetime, timezone
from typing import List, Optional


class Event:
    """Event with timestamp. BUG: naive datetime."""
    def __init__(self, name: str, timestamp: datetime):
        self.name = name
        # BUG: Should ensure timezone-aware, but doesn't
        self.timestamp = timestamp


class User:
    """User with tags. BUG: mutable default argument."""
    def __init__(self, name: str, tags: List[str] = []):  # BUG: mutable default
        self.name = name
        self.tags = tags

    def add_tag(self, tag: str):
        self.tags.append(tag)
'''

    # utils.py - has silent exception swallow
    utils_py = '''"""Utility functions with bugs."""
import yaml


def parse_config_file(path: str) -> dict:
    """Parse YAML config file. BUG: returns empty dict on any exception."""
    try:
        with open(path, 'r') as f:
            return yaml.safe_load(f)
    except Exception:
        # BUG: Silently swallows all exceptions, returns empty dict
        return {}


def safe_divide(a: float, b: float) -> float:
    """Safe division. BUG: swallows ZeroDivisionError."""
    try:
        return a / b
    except Exception:
        # BUG: Returns 0 instead of propagating or handling properly
        return 0
'''

    # Write all files
    (base_dir / "calculator.py").write_text(calculator_py)
    (base_dir / "processor.py").write_text(processor_py)
    (base_dir / "models.py").write_text(models_py)
    (base_dir / "utils.py").write_text(utils_py)

    return base_dir


if __name__ == "__main__":
    setup_task2(Path("/tmp/test_task2"))