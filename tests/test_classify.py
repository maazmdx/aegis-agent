"""Tests for event classification logic in detector.py.

Covers all classification rules including priority ordering and
boundary conditions at exact threshold values.
"""

from detector import classify


# ── tool_failure (status == "error") ────────────────────────────────────────

def test_error_classified_as_tool_failure() -> None:
    assert classify({"status": "error"}) == "tool_failure"


def test_error_with_extra_fields() -> None:
    """Error status should classify even with other healthy fields."""
    event = {"status": "error", "cost": 0.01, "tokens": 10, "confidence": 0.99}
    assert classify(event) == "tool_failure"


# ── budget_exceeded (cost > 1.0 or tokens > 10_000) ────────────────────────

def test_high_cost_classified_as_budget_exceeded() -> None:
    assert classify({"status": "success", "cost": 1.5, "tokens": 100}) == "budget_exceeded"


def test_high_tokens_classified_as_budget_exceeded() -> None:
    assert classify({"status": "success", "cost": 0.5, "tokens": 15000}) == "budget_exceeded"


def test_cost_exactly_at_threshold_is_healthy() -> None:
    """cost == 1.0 is NOT > 1.0, so it should be healthy."""
    assert classify({"status": "success", "cost": 1.0, "tokens": 100}) is None


def test_tokens_exactly_at_threshold_is_healthy() -> None:
    """tokens == 10000 is NOT > 10000, so it should be healthy."""
    assert classify({"status": "success", "cost": 0.5, "tokens": 10000}) is None


def test_cost_just_above_threshold() -> None:
    assert classify({"status": "success", "cost": 1.01, "tokens": 100}) == "budget_exceeded"


def test_tokens_just_above_threshold() -> None:
    assert classify({"status": "success", "cost": 0.5, "tokens": 10001}) == "budget_exceeded"


# ── pii_leak (pii_leak == True) ─────────────────────────────────────────────

def test_pii_leak_classified() -> None:
    event = {"status": "success", "cost": 0.5, "tokens": 100, "pii_leak": True}
    assert classify(event) == "pii_leak"


def test_pii_false_not_classified() -> None:
    event = {"status": "success", "cost": 0.5, "tokens": 100, "pii_leak": False}
    assert classify(event) is None


# ── low_confidence (confidence < 0.5) ───────────────────────────────────────

def test_low_confidence_classified() -> None:
    assert classify({"status": "success", "confidence": 0.3}) == "low_confidence"


def test_confidence_exactly_at_threshold_is_healthy() -> None:
    """confidence == 0.5 is NOT < 0.5, so it should be healthy."""
    assert classify({"status": "success", "confidence": 0.5}) is None


def test_confidence_just_below_threshold() -> None:
    assert classify({"status": "success", "confidence": 0.49}) == "low_confidence"


def test_zero_confidence() -> None:
    assert classify({"status": "success", "confidence": 0.0}) == "low_confidence"


# ── healthy (no classification) ─────────────────────────────────────────────

def test_healthy_event_returns_none() -> None:
    event = {
        "status": "success",
        "cost": 0.5,
        "tokens": 100,
        "confidence": 0.9,
        "pii_leak": False,
    }
    assert classify(event) is None


def test_empty_event_is_healthy() -> None:
    """An event with no triggering fields should be healthy."""
    assert classify({}) is None


def test_minimal_healthy_event() -> None:
    assert classify({"status": "success"}) is None


# ── Priority ordering ──────────────────────────────────────────────────────

def test_error_takes_priority_over_pii() -> None:
    """tool_failure classification should fire before pii_leak."""
    event = {"status": "error", "pii_leak": True}
    assert classify(event) == "tool_failure"


def test_budget_takes_priority_over_pii() -> None:
    """budget_exceeded fires before pii_leak in the rule chain."""
    event = {"status": "success", "cost": 5.0, "tokens": 100, "pii_leak": True}
    assert classify(event) == "budget_exceeded"


def test_error_takes_priority_over_budget() -> None:
    """tool_failure fires before budget_exceeded."""
    event = {"status": "error", "cost": 5.0, "tokens": 50000}
    assert classify(event) == "tool_failure"


def test_pii_takes_priority_over_low_confidence() -> None:
    """pii_leak fires before low_confidence."""
    event = {"status": "success", "pii_leak": True, "confidence": 0.1}
    assert classify(event) == "pii_leak"


def test_budget_takes_priority_over_low_confidence() -> None:
    """budget_exceeded fires before low_confidence."""
    event = {"status": "success", "cost": 5.0, "confidence": 0.1}
    assert classify(event) == "budget_exceeded"
