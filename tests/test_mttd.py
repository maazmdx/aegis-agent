"""Tests for the MTTD (Mean Time to Diagnose) metric calculation.

The logic under test lives in ``dashboard/main.py`` and is pure Python
with no Firestore dependency, making it fast and safe to unit-test.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))

from main import compute_mttd  # noqa: E402


# ── Basic averaging ─────────────────────────────────────────────────────────

def test_mttd_basic_average() -> None:
    assert compute_mttd([5.0, 10.0, 15.0]) == 10.0


def test_mttd_rounds_to_one_decimal() -> None:
    assert compute_mttd([3.0, 4.0]) == 3.5


def test_mttd_single_value() -> None:
    assert compute_mttd([7.3]) == 7.3


# ── Empty / edge cases ────────────────────────────────────────────────────

def test_mttd_empty_returns_dash() -> None:
    assert compute_mttd([]) == "\u2014"


def test_mttd_returns_string_dash_not_zero() -> None:
    result = compute_mttd([])
    assert result == "\u2014"
    assert result != 0


# ── Precision and rounding ────────────────────────────────────────────────

def test_mttd_rounds_down() -> None:
    """1.14 should round to 1.1."""
    assert compute_mttd([1.14]) == 1.1


def test_mttd_rounds_up() -> None:
    """1.15 should round to 1.1 or 1.2 depending on Python banker's rounding."""
    result = compute_mttd([1.15])
    assert result in (1.1, 1.2)


def test_mttd_large_values() -> None:
    """Test with values near the 3600s filter threshold."""
    assert compute_mttd([3599.0, 3599.0]) == 3599.0


def test_mttd_many_values() -> None:
    """Average of [1, 2, ..., 10] = 5.5."""
    vals = [float(i) for i in range(1, 11)]
    assert compute_mttd(vals) == 5.5


def test_mttd_identical_values() -> None:
    assert compute_mttd([42.0, 42.0, 42.0]) == 42.0


def test_mttd_returns_float_not_int() -> None:
    """Even when the average is a whole number, return type should be numeric."""
    result = compute_mttd([10.0, 10.0])
    assert isinstance(result, (int, float))
    assert result == 10.0
