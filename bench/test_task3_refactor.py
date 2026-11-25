#!/usr/bin/env python3
"""Hidden tests for Task 3: Refactor Script."""
import pytest
import sys
import importlib.util
import os
from pathlib import Path

# The folder the task ran in (set by run_hidden_tests); a bench/task3_refactor_script_run1 copy is the old layout.
task_dir = Path(os.environ["MIKE_BENCH_TASK_DIR"]) if os.environ.get("MIKE_BENCH_TASK_DIR") \
    else Path(__file__).parent / "task3_refactor_script_run1"

sys.path.insert(0, str(task_dir))


def load_module(name: str, filename: str):
    """Load a module from the task directory."""
    filepath = task_dir / filename
    if not filepath.exists():
        pytest.skip(f"{filename} not found")
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TestOriginalTestsStillPass:
    """Ensure all original visible tests still pass."""

    def test_main_module_runs(self):
        """main.py should run without error."""
        main = load_module("main", "main.py")
        # Should have a main function or runnable code
        assert hasattr(main, 'main') or hasattr(main, 'run') or hasattr(main, 'process')

    def test_parser_module_exists(self):
        parser = load_module("parser", "parser.py")
        assert hasattr(parser, 'parse') or hasattr(parser, 'Parser')

    def test_processor_module_exists(self):
        processor = load_module("processor", "processor.py")
        assert hasattr(processor, 'process') or hasattr(processor, 'Processor')

    def test_formatter_module_exists(self):
        formatter = load_module("formatter", "formatter.py")
        assert hasattr(formatter, 'format') or hasattr(formatter, 'Formatter')


class TestNoDuplicateCode:
    """Check that code isn't duplicated across modules."""

    def test_modules_are_distinct(self):
        """Each module should have unique functions."""
        parser = load_module("parser", "parser.py")
        processor = load_module("processor", "processor.py")
        formatter = load_module("formatter", "formatter.py")

        # Get public functions/classes
        parser_funcs = {name for name in dir(parser) if not name.startswith('_')}
        processor_funcs = {name for name in dir(processor) if not name.startswith('_')}
        formatter_funcs = {name for name in dir(formatter) if not name.startswith('_')}

        # They should not share function names (except common ones)
        common = parser_funcs & processor_funcs & formatter_funcs
        # Allow only very generic names
        allowed_common = {'__builtins__', '__cached__', '__doc__', '__file__', '__loader__', '__name__', '__package__', '__spec__'}
        unexpected = common - allowed_common
        # This is a soft check - warn but don't fail hard
        if unexpected:
            print(f"Warning: Shared names across modules: {unexpected}")


class TestModuleResponsibilities:
    """Each module should have a clear single responsibility."""

    def test_parser_only_parses(self):
        parser = load_module("parser", "parser.py")
        # Parser should not have format/output functions
        parser_funcs = {name for name in dir(parser) if not name.startswith('_')}
        format_terms = {'format', 'render', 'output', 'print', 'write', 'serialize', 'to_json', 'to_xml'}
        overlap = parser_funcs & format_terms
        assert len(overlap) == 0, f"Parser has formatting functions: {overlap}"

    def test_processor_only_processes(self):
        processor = load_module("processor", "processor.py")
        processor_funcs = {name for name in dir(processor) if not name.startswith('_')}
        parse_terms = {'parse', 'tokenize', 'lex', 'scan'}
        format_terms = {'format', 'render', 'output', 'print', 'write', 'serialize'}
        overlap_parse = processor_funcs & parse_terms
        overlap_format = processor_funcs & format_terms
        # Processor shouldn't do parsing or formatting
        assert len(overlap_parse) == 0, f"Processor has parsing functions: {overlap_parse}"
        assert len(overlap_format) == 0, f"Processor has formatting functions: {overlap_format}"

    def test_formatter_only_formats(self):
        formatter = load_module("formatter", "formatter.py")
        formatter_funcs = {name for name in dir(formatter) if not name.startswith('_')}
        parse_terms = {'parse', 'tokenize', 'lex', 'scan', 'process', 'calculate', 'compute'}
        overlap = formatter_funcs & parse_terms
        assert len(overlap) == 0, f"Formatter has processing/parsing functions: {overlap}"


class TestCleanInterface:
    """Test that modules have clean public APIs."""

    def test_parser_public_api(self):
        parser = load_module("parser", "parser.py")
        # Should have a clear entry point
        assert hasattr(parser, 'parse') or hasattr(parser, 'Parser')

    def test_processor_public_api(self):
        processor = load_module("processor", "processor.py")
        assert hasattr(processor, 'process') or hasattr(processor, 'Processor')

    def test_formatter_public_api(self):
        formatter = load_module("formatter", "formatter.py")
        assert hasattr(formatter, 'format') or hasattr(formatter, 'Formatter')


class TestMessyScriptReplaced:
    """Verify messy_script.py was replaced."""

    def test_messy_script_gone(self):
        messy = task_dir / "messy_script.py"
        assert not messy.exists(), "messy_script.py should be replaced by main.py"

    def test_main_py_exists(self):
        main = task_dir / "main.py"
        assert main.exists(), "main.py should exist as the new entry point"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])