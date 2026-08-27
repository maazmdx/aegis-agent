"""Unit tests for the governance gate pure functions (no Firestore I/O)."""

import governance


def test_requires_approval_gated_escalate():
    assert governance.requires_approval({"action": "escalate"}, {"severity": "low"})


def test_requires_approval_gated_quarantine():
    assert governance.requires_approval({"action": "quarantine"}, {"severity": "low"})


def test_requires_approval_high_severity():
    assert governance.requires_approval({"action": "retry"}, {"severity": "high"})


def test_requires_approval_false_for_low_retry():
    assert not governance.requires_approval({"action": "retry"}, {"severity": "low"})


def test_requires_approval_handles_missing_diagnosis():
    assert not governance.requires_approval({"action": "retry"}, None)


def test_make_audit_entry_shape():
    entry = governance.make_audit_entry("decider", "decided", "chose retry")
    assert entry["actor"] == "decider"
    assert entry["action"] == "decided"
    assert entry["detail"] == "chose retry"
    assert isinstance(entry["at"], float)
    assert "extra" not in entry


def test_make_audit_entry_with_extra():
    entry = governance.make_audit_entry("human:nex", "gate_closed", "approved", {"k": 1})
    assert entry["extra"] == {"k": 1}


def test_auto_approve_default_true(monkeypatch):
    monkeypatch.delenv("AEGIS_AUTO_APPROVE", raising=False)
    assert governance.auto_approve_enabled() is True


def test_auto_approve_false(monkeypatch):
    monkeypatch.setenv("AEGIS_AUTO_APPROVE", "false")
    assert governance.auto_approve_enabled() is False


def test_auto_approve_true_explicit(monkeypatch):
    monkeypatch.setenv("AEGIS_AUTO_APPROVE", "true")
    assert governance.auto_approve_enabled() is True
