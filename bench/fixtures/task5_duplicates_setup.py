#!/usr/bin/env python3
"""Setup fixture for Task 5 - creates empty duplicate finder project."""
from pathlib import Path


def setup_task5(base_dir: Path):
    """Create initial structure for duplicate finder task."""
    base_dir.mkdir(parents=True, exist_ok=True)
    # No initial files - model creates everything
    return base_dir


if __name__ == "__main__":
    setup_task5(Path("/tmp/test_task5"))