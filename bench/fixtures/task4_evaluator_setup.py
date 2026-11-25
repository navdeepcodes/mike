#!/usr/bin/env python3
"""Setup fixture for Task 4 - creates empty evaluator project."""
from pathlib import Path


def setup_task4(base_dir: Path):
    """Create initial structure for evaluator task."""
    base_dir.mkdir(parents=True, exist_ok=True)
    # No initial files - model creates everything
    return base_dir


if __name__ == "__main__":
    setup_task4(Path("/tmp/test_task4"))