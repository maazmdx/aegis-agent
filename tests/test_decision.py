"""Tests for the decision policy in decider.py.

Covers all policy branches, edge cases, and verifies that every
decision includes a non-empty reason string.
"""

from decider import decide


# ── PII always quarantined (highest priority) ──────────────────────────────

def test_pii_always_quarantined() -> None:
    """PII incidents must always quarantine, ignoring severity and recommendation."""
    incident = {
        "type": "pii_leak",
        "diagnosis": {"severity": "low", "recommended_action": "retry"},
    }
    assert decide(incident)["action"] == "quarantine"


def test_pii_quarantined_even_when_high_severity() -> None:
    incident = {
        "type": "pii_leak",
        "diagnosis": {"severity": "high", "recommended_action": "escalate"},
    }
    assert decide(incident)["action"] == "quarantine"


def test_pii_quarantined_with_medium_severity() -> None:
    incident = {
        "type": "pii_leak",
        "diagnosis": {"severity": "medium", "recommended_action": "retry"},
    }
    assert decide(incident)["action"] == "quarantine"


def test_pii_quarantined_with_no_diagnosis() -> None:
    """PII quarantine should work even with an empty diagnosis."""
    incident = {"type": "pii_leak", "diagnosis": {}}
    assert decide(incident)["action"] == "quarantine"


# ── High severity always escalated (when not PII) ──────────────────────────

def test_high_severity_escalated() -> None:
    incident = {
        "type": "tool_failure",
        "diagnosis": {"severity": "high", "recommended_action": "retry"},
    }
    assert decide(incident)["action"] == "escalate"


def test_high_severity_escalated_even_quarantine_recommended() -> None:
    """High severity overrides Gemini quarantine recommendation for non-PII."""
    incident = {
        "type": "tool_failure",
        "diagnosis": {"severity": "high", "recommended_action": "quarantine"},
    }
    assert decide(incident)["action"] == "escalate"


# ── Retry recommendation followed for non-high severity ────────────────────

def test_retry_recommendation_followed() -> None:
    incident = {
        "type": "tool_failure",
        "diagnosis": {"severity": "low", "recommended_action": "retry"},
    }
    assert decide(incident)["action"] == "retry"


def test_retry_on_medium_severity() -> None:
    incident = {
        "type": "budget_exceeded",
        "diagnosis": {"severity": "medium", "recommended_action": "retry"},
    }
    assert decide(incident)["action"] == "retry"


# ── Fallback follows Gemini recommendation ─────────────────────────────────

def test_fallback_follows_recommendation() -> None:
    """Non-retry recommendations fall through to the Gemini recommendation."""
    incident = {
        "type": "tool_failure",
        "diagnosis": {"severity": "medium", "recommended_action": "quarantine"},
    }
    assert decide(incident)["action"] == "quarantine"


def test_fallback_escalate_recommendation() -> None:
    incident = {
        "type": "low_confidence",
        "diagnosis": {"severity": "low", "recommended_action": "escalate"},
    }
    assert decide(incident)["action"] == "escalate"


# ── Edge cases ─────────────────────────────────────────────────────────────

def test_missing_diagnosis_defaults_to_escalate() -> None:
    """No diagnosis at all should default to escalate via fallback."""
    incident = {"type": "tool_failure"}
    assert decide(incident)["action"] == "escalate"


def test_empty_type_with_low_severity_retry() -> None:
    """Empty type should still follow normal severity/recommendation logic."""
    incident = {
        "type": "",
        "diagnosis": {"severity": "low", "recommended_action": "retry"},
    }
    assert decide(incident)["action"] == "retry"


# ── Reason validation ─────────────────────────────────────────────────────

def test_decision_has_reason() -> None:
    """Every decision must include a non-empty reason string."""
    incident = {
        "type": "tool_failure",
        "diagnosis": {"severity": "low", "recommended_action": "retry"},
    }
    result = decide(incident)
    assert "reason" in result
    assert len(result["reason"]) > 0


def test_quarantine_reason_mentions_pii() -> None:
    incident = {
        "type": "pii_leak",
        "diagnosis": {"severity": "low", "recommended_action": "retry"},
    }
    result = decide(incident)
    assert "pii" in result["reason"].lower() or "quarantine" in result["reason"].lower()


def test_escalate_reason_mentions_severity() -> None:
    incident = {
        "type": "tool_failure",
        "diagnosis": {"severity": "high", "recommended_action": "retry"},
    }
    result = decide(incident)
    assert "high" in result["reason"].lower() or "severity" in result["reason"].lower()
