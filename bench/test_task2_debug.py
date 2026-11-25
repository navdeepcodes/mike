#!/usr/bin/env python3
"""Hidden tests for Task 2: Debug Module with 5 bugs."""
import pytest
import sys
import threading
import time
import os
from pathlib import Path

# The folder the task ran in (set by run_hidden_tests); a bench/task2_debug_module_run1 copy is the old layout.
task_dir = Path(os.environ["MIKE_BENCH_TASK_DIR"]) if os.environ.get("MIKE_BENCH_TASK_DIR") \
    else Path(__file__).parent / "task2_debug_module_run1"

sys.path.insert(0, str(task_dir))


class TestCalculator:
    """Test calculator.py - off-by-one bug."""

    def test_sum_range(self):
        import calculator
        # sum_range(1, 5) should be 1+2+3+4+5 = 15
        # Bug: off-by-one in range
        assert calculator.sum_range(1, 5) == 15
        assert calculator.sum_range(1, 1) == 1
        assert calculator.sum_range(5, 5) == 5

    def test_factorial(self):
        import calculator
        assert calculator.factorial(0) == 1
        assert calculator.factorial(1) == 1
        assert calculator.factorial(5) == 120


class TestProcessor:
    """Test processor.py - race condition and silent exception."""

    def test_process_items_thread_safe(self):
        import processor
        # Multiple threads processing shared counter
        # Bug: race condition on shared counter
        results = processor.process_items_thread_safe([1, 2, 3, 4, 5], num_threads=2)
        # Each item processed once, sum should be 15
        assert results["total_processed"] == 5
        assert results["sum"] == 15

    def test_process_with_callback_exception(self):
        import processor
        # Callback that raises exception should not be silently swallowed
        # Bug: exception swallowed in thread
        def bad_callback(x):
            if x == 3:
                raise ValueError("bad item")
            return x * 2

        # Should propagate or at least log the exception, not swallow silently
        results = processor.process_with_callback([1, 2, 3, 4], bad_callback)
        # Items 1,2,4 should succeed, 3 should be noted as failed
        assert results["succeeded"] == 3
        assert results["failed"] == 1


class TestModels:
    """Test models.py - timezone bug and mutable default."""

    def test_event_timestamp_timezone(self):
        import models
        from datetime import datetime, timezone
        # Event timestamps should be timezone-aware
        # Bug: naive datetime instead of timezone-aware
        event = models.Event(name="Test", timestamp=datetime(2024, 1, 15, 12, 0, 0))
        assert event.timestamp.tzinfo is not None
        # Should be UTC
        assert event.timestamp.tzinfo == timezone.utc or event.timestamp.utcoffset() is not None

    def test_user_default_tags_mutable(self):
        import models
        # Default mutable argument bug
        user1 = models.User(name="Alice")
        user2 = models.User(name="Bob")
        user1.tags.append("admin")
        # user2 should not have "admin" tag
        assert "admin" not in user2.tags
        assert user1.tags != user2.tags


class TestUtils:
    """Test utils.py - silent exception swallow."""

    def test_parse_config_file_not_found(self):
        import utils
        # Should raise or return error, not return empty dict silently
        # Bug: returns {} on any exception
        result = utils.parse_config_file("/nonexistent/path/config.yaml")
        assert result is None or "error" in str(result).lower()

    def test_parse_config_invalid_yaml(self):
        import utils
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("invalid: yaml: content: [")
            fname = f.name

        try:
            result = utils.parse_config_file(fname)
            assert result is None or "error" in str(result).lower()
        finally:
            import os
            os.unlink(fname)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])