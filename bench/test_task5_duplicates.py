#!/usr/bin/env python3
"""Hidden tests for Task 5: Duplicate File Finder CLI."""
import pytest
import tempfile
import os
import sys
import hashlib
import subprocess
from pathlib import Path

# The folder the task ran in (set by run_hidden_tests); a bench/task5_duplicate_finder_run1 copy is the old layout.
task_dir = Path(os.environ["MIKE_BENCH_TASK_DIR"]) if os.environ.get("MIKE_BENCH_TASK_DIR") \
    else Path(__file__).parent / "task5_duplicate_finder_run1"

sys.path.insert(0, str(task_dir))


def run_cli(args: list, cwd: Path = None) -> subprocess.CompletedProcess:
    """Run the CLI tool."""
    main_py = task_dir / "main.py"
    if not main_py.exists():
        # Try to find the entry point
        for f in task_dir.glob("*.py"):
            if f.name != "__init__.py" and "test" not in f.name:
                main_py = f
                break

    cmd = [sys.executable, str(main_py)] + args
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or task_dir, timeout=30)


def create_test_files(base: Path):
    """Create test files with known duplicates."""
    # File A - content "hello world"
    (base / "a.txt").write_text("hello world")
    # File B - same content as A
    (base / "subdir" / "b.txt").write_text("hello world")
    # File C - different content
    (base / "c.txt").write_text("different content")
    # File D - empty file
    (base / "empty1.txt").write_text("")
    # File E - another empty file (duplicate of D)
    (base / "empty2.txt").write_text("")
    # File F - binary-ish content
    (base / "data.bin").write_bytes(b"\x00\x01\x02\x03" * 100)
    # File G - same binary content
    (base / "copy.bin").write_bytes(b"\x00\x01\x02\x03" * 100)

    return base


class TestBasicFunctionality:
    """Test basic duplicate finding."""

    def test_finds_text_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            create_test_files(base)

            result = run_cli([str(base)])
            assert result.returncode == 0

            output = result.stdout
            # Should find the "hello world" duplicate group
            assert "hello world" in output or "a.txt" in output
            assert "b.txt" in output
            # Should find empty file duplicates
            assert "empty1.txt" in output
            assert "empty2.txt" in output
            # Should find binary duplicates
            assert "data.bin" in output
            assert "copy.bin" in output

    def test_dry_run_default(self):
        """Default mode should be dry run (not delete)."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            create_test_files(base)

            # First run
            result = run_cli([str(base)])
            assert result.returncode == 0

            # Files should still exist
            assert (base / "a.txt").exists()
            assert (base / "b.txt").exists()

    def test_delete_flag(self):
        """--delete should actually delete duplicates."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            create_test_files(base)

            # Run with --delete
            result = run_cli([str(base), "--delete"])
            assert result.returncode == 0

            # One of each duplicate pair should remain, the other deleted
            # We can't predict which, but total count should be reduced
            remaining = list(base.rglob("*"))
            remaining = [f for f in remaining if f.is_file()]
            # Original: 7 files, 3 duplicate pairs = 4 unique
            # After delete: should have 4 files
            assert len(remaining) <= 5  # Allow some variance

    def test_dry_run_explicit(self):
        """--dry-run should be explicit and not delete."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            create_test_files(base)

            result = run_cli([str(base), "--dry-run"])
            assert result.returncode == 0

            # All files should still exist
            assert (base / "a.txt").exists()
            assert (base / "b.txt").exists()


class TestSymlinks:
    """Test symlink handling."""

    def test_symlink_not_followed_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "real.txt"
            target.write_text("target content")

            link = base / "link.txt"
            link.symlink_to(target)

            result = run_cli([str(base)])
            assert result.returncode == 0

            # Should detect the symlink and real file as duplicates
            # (or treat symlink specially - either way, shouldn't crash)

    def test_symlink_cycle_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Create a symlink cycle: dir1 -> dir2 -> dir1
            dir1 = base / "dir1"
            dir2 = base / "dir2"
            dir1.mkdir()
            dir2.mkdir()
            (dir1 / "file.txt").write_text("content")

            try:
                (dir1 / "cycle").symlink_to(dir2)
                (dir2 / "cycle").symlink_to(dir1)

                result = run_cli([str(base)])
                # Should not hang or crash
                assert result.returncode in (0, 1)  # 1 is OK if it detects cycle
            except OSError:
                # Symlinks might not work on all systems
                pytest.skip("Symlinks not supported")


class TestPermissionErrors:
    """Test permission error handling."""

    def test_permission_denied_handled(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            secret = base / "secret.txt"
            secret.write_text("secret")

            try:
                # Remove read permission
                secret.chmod(0o000)

                result = run_cli([str(base)])
                # Should not crash, might report error
                assert result.returncode in (0, 1)
            except PermissionError:
                pytest.skip("Cannot test permissions on this system")
            finally:
                try:
                    secret.chmod(0o644)
                except:
                    pass


class TestHashing:
    """Test content-based hashing."""

    def test_same_content_different_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "file1.txt").write_text("same content")
            (base / "file2.dat").write_text("same content")
            (base / "file3.log").write_text("different")

            result = run_cli([str(base)])
            assert result.returncode == 0

            output = result.stdout
            # file1 and file2 should be grouped
            assert "file1.txt" in output
            assert "file2.dat" in output

    def test_large_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Create 10MB files
            large_content = "x" * (10 * 1024 * 1024)
            (base / "big1.txt").write_text(large_content)
            (base / "big2.txt").write_text(large_content)

            result = run_cli([str(base)])
            assert result.returncode == 0

            output = result.stdout
            assert "big1.txt" in output
            assert "big2.txt" in output


class TestOutputFormat:
    """Test output format."""

    def test_groups_shown(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "a.txt").write_text("dup")
            (base / "b.txt").write_text("dup")
            (base / "c.txt").write_text("unique")

            result = run_cli([str(base)])
            assert result.returncode == 0

            output = result.stdout
            # Should show groups
            assert "dup" in output.lower() or "group" in output.lower() or "a.txt" in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])