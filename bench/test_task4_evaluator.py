#!/usr/bin/env python3
"""Hidden tests for Task 4: Expression Evaluator."""
import pytest
import sys
import os
from pathlib import Path

# The folder the task ran in (set by run_hidden_tests); a bench/task4_expression_evaluator_run1 copy is the old layout.
task_dir = Path(os.environ["MIKE_BENCH_TASK_DIR"]) if os.environ.get("MIKE_BENCH_TASK_DIR") \
    else Path(__file__).parent / "task4_expression_evaluator_run1"

sys.path.insert(0, str(task_dir))


def import_evaluator():
    import importlib.util
    eval_py = task_dir / "evaluator.py"
    if not eval_py.exists():
        pytest.skip("evaluator.py not found")
    spec = importlib.util.spec_from_file_location("evaluator", eval_py)
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluator"] = module
    spec.loader.exec_module(module)
    return module


class TestBasicArithmetic:
    """Test basic arithmetic operations."""

    def test_addition(self):
        m = import_evaluator()
        assert m.evaluate("1 + 2") == 3
        assert m.evaluate("10 + 20 + 30") == 60

    def test_subtraction(self):
        m = import_evaluator()
        assert m.evaluate("10 - 3") == 7
        assert m.evaluate("100 - 50 - 25") == 25

    def test_multiplication(self):
        m = import_evaluator()
        assert m.evaluate("3 * 4") == 12
        assert m.evaluate("2 * 3 * 4") == 24

    def test_division(self):
        m = import_evaluator()
        assert m.evaluate("10 / 2") == 5
        assert m.evaluate("15 / 3") == 5
        # Float division
        assert abs(m.evaluate("7 / 2") - 3.5) < 0.001

    def test_power(self):
        m = import_evaluator()
        assert m.evaluate("2 ^ 3") == 8
        assert m.evaluate("5 ^ 2") == 25
        assert m.evaluate("2 ^ 10") == 1024

    def test_precedence(self):
        m = import_evaluator()
        # Multiplication before addition
        assert m.evaluate("2 + 3 * 4") == 14
        # Power before multiplication
        assert m.evaluate("2 * 3 ^ 2") == 18
        # Division before subtraction
        assert m.evaluate("10 - 6 / 2") == 7


class TestParentheses:
    """Test parentheses grouping."""

    def test_simple_parentheses(self):
        m = import_evaluator()
        assert m.evaluate("(1 + 2) * 3") == 9
        assert m.evaluate("2 * (3 + 4)") == 14

    def test_nested_parentheses(self):
        m = import_evaluator()
        assert m.evaluate("((1 + 2) * 3)") == 9
        assert m.evaluate("(1 + (2 * 3))") == 7
        assert m.evaluate("((2 + 3) * (4 + 5))") == 45

    def test_deep_nesting(self):
        m = import_evaluator()
        assert m.evaluate("((((1))))") == 1
        assert m.evaluate("(1 + (2 + (3 + 4)))") == 10


class TestUnaryMinus:
    """Test unary minus operator."""

    def test_simple_unary(self):
        m = import_evaluator()
        assert m.evaluate("-5") == -5
        assert m.evaluate("-10") == -10

    def test_unary_with_parentheses(self):
        m = import_evaluator()
        assert m.evaluate("-(3 + 2)") == -5
        assert m.evaluate("-(2 * 3)") == -6

    def test_unary_in_expression(self):
        m = import_evaluator()
        assert m.evaluate("5 + -3") == 2
        assert m.evaluate("10 - -5") == 15
        assert m.evaluate("-5 * -2") == 10

    def test_multiple_unary(self):
        m = import_evaluator()
        assert m.evaluate("--5") == 5
        assert m.evaluate("---5") == -5


class TestFunctions:
    """Test built-in functions."""

    def test_max(self):
        m = import_evaluator()
        assert m.evaluate("max(3, 7)") == 7
        assert m.evaluate("max(10, 5, 8)") == 10

    def test_min(self):
        m = import_evaluator()
        assert m.evaluate("min(3, 7)") == 3
        assert m.evaluate("min(10, 5, 8)") == 5

    def test_abs(self):
        m = import_evaluator()
        assert m.evaluate("abs(-5)") == 5
        assert m.evaluate("abs(5)") == 5
        assert m.evaluate("abs(0)") == 0

    def test_sqrt(self):
        m = import_evaluator()
        assert abs(m.evaluate("sqrt(16)") - 4.0) < 0.001
        assert abs(m.evaluate("sqrt(2)") - 1.414) < 0.01

    def test_pow(self):
        m = import_evaluator()
        assert m.evaluate("pow(2, 3)") == 8
        assert m.evaluate("pow(5, 2)") == 25
        assert abs(m.evaluate("pow(2, 0.5)") - 1.414) < 0.01

    def test_nested_functions(self):
        m = import_evaluator()
        assert m.evaluate("max(abs(-10), 5)") == 10
        assert m.evaluate("sqrt(pow(3, 2) + pow(4, 2))") == 5


class TestWhitespace:
    """Test whitespace handling."""

    def test_spaces(self):
        m = import_evaluator()
        assert m.evaluate("1 + 2") == 3
        assert m.evaluate("  1  +  2  ") == 3
        assert m.evaluate("\t1\n+\t2") == 3

    def test_no_spaces(self):
        m = import_evaluator()
        assert m.evaluate("1+2") == 3
        assert m.evaluate("2*3+4") == 10


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_string(self):
        m = import_evaluator()
        with pytest.raises(Exception) as exc:
            m.evaluate("")
        # Should raise a meaningful error
        assert len(str(exc.value)) > 0

    def test_only_whitespace(self):
        m = import_evaluator()
        with pytest.raises(Exception) as exc:
            m.evaluate("   ")

    def test_division_by_zero(self):
        m = import_evaluator()
        with pytest.raises(ZeroDivisionError):
            m.evaluate("5 / 0")

    def test_unmatched_parentheses_open(self):
        m = import_evaluator()
        with pytest.raises(Exception) as exc:
            m.evaluate("(1 + 2")
        assert "parenthes" in str(exc.value).lower() or "unmatched" in str(exc.value).lower()

    def test_unmatched_parentheses_close(self):
        m = import_evaluator()
        with pytest.raises(Exception) as exc:
            m.evaluate("1 + 2)")
        assert "parenthes" in str(exc.value).lower() or "unmatched" in str(exc.value).lower()

    def test_unknown_token(self):
        m = import_evaluator()
        with pytest.raises(Exception) as exc:
            m.evaluate("1 + foo")
        assert "unknown" in str(exc.value).lower() or "invalid" in str(exc.value).lower()

    def test_invalid_syntax(self):
        m = import_evaluator()
        with pytest.raises(Exception):
            m.evaluate("1 + + 2")
        with pytest.raises(Exception):
            m.evaluate("1 2")
        with pytest.raises(Exception):
            m.evaluate("* 2")

    def test_function_wrong_args(self):
        m = import_evaluator()
        with pytest.raises(Exception):
            m.evaluate("max()")  # no args
        with pytest.raises(Exception):
            m.evaluate("sqrt(1, 2)")  # too many args


class TestComplexExpressions:
    """Test complex real-world expressions."""

    def test_quadratic_formula(self):
        m = import_evaluator()
        # (-b + sqrt(b^2 - 4ac)) / 2a for a=1, b=-3, c=2 -> roots 2 and 1
        result = m.evaluate("(-(-3) + sqrt((-3)^2 - 4*1*2)) / (2*1)")
        assert abs(result - 2.0) < 0.01

    def test_compound_interest(self):
        m = import_evaluator()
        # P(1 + r/n)^(nt) with P=1000, r=0.05, n=12, t=10
        result = m.evaluate("1000 * (1 + 0.05/12) ^ (12*10)")
        assert abs(result - 1647) < 10

    def test_pythagorean(self):
        m = import_evaluator()
        result = m.evaluate("sqrt(3^2 + 4^2)")
        assert abs(result - 5.0) < 0.001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])