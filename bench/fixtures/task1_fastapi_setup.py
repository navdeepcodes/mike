#!/usr/bin/env python3
"""Setup fixture for Task 1 - creates initial empty project structure."""
import tempfile
from pathlib import Path


def setup_task1(base_dir: Path):
    """Create initial structure for FastAPI task."""
    base_dir.mkdir(parents=True, exist_ok=True)
    # No initial files - model creates everything
    return base_dir


if __name__ == "__main__":
    setup_task1(Path("/tmp/test_task1"))