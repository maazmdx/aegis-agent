#!/usr/bin/env python
"""governance.py - Human-in-the-loop approval gate and append-only audit trail.

Aegis is autonomous by default, but high-impact actions (escalations,
quarantines, and any high-severity incident) can be routed through a
deterministic governance gate that requires exactly ONE human decision before
the action is executed. Every state transition an agent makes is also recorded
to an append-only audit trail on the incident document, giving a complete,
reviewable history of who (which sub-agent or which human) did what and when.

Set ``AEGIS_AUTO_APPROVE=false`` to enforce the human gate. The default
(``true``) keeps the fully-autonomous demo flow intact.

Environment variables
---------------------
PROJECT_ID         - GCP project.
AEGIS_AUTO_APPROVE - "true" (default) auto-approves gated actions so the
                     autonomous loop completes end-to-end; "false" holds them
                     for a single human decision.
"""

import logging
import os
import time

from google.cloud import firestore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID")
COLLECTION = "incidents"

# High-impact actions that must pass the governance gate before execution.
GATED_ACTIONS = {"escalate", "quarantine"}

_db = None


def _get_db() -> firestore.Client:
    """Lazily construct and cache the Firestore client.

    Deferring construction keeps the pure helper functions in this module
    importable in environments without Google Cloud credentials (e.g. CI unit
    tests), while still reusing a single client at runtime.
    """
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID)
    return _db


def auto_approve_enabled() -> bool:
    """Return True when the human gate is bypassed for autonomous demos.

    Controlled by ``AEGIS_AUTO_APPROVE`` (default ``"true"``).
    """
    return os.environ.get("AEGIS_AUTO_APPROVE", "true").lower() != "false"


def requires_approval(decision: dict, diagnosis: dict | None = None) -> bool:
    """Decide whether an action must pass the human governance gate.

    Pure function (no I/O) so it is trivially unit-testable.

    An action is gated when it is a high-impact action (escalate/quarantine)
    or when the incident was diagnosed as high severity.

    Args:
        decision:  The ``{"action": ..., "reason": ...}`` dict from the decider.
        diagnosis: Optional diagnosis dict (uses ``severity``).

    Returns:
        True if a human decision is required before executing the action.
    """
    action = (decision or {}).get("action", "")
    severity = (diagnosis or {}).get("severity", "medium")
    return action in GATED_ACTIONS or severity == "high"


def make_audit_entry(
    actor: str, action: str, detail: str, extra: dict | None = None
) -> dict:
    """Build a single append-only audit-trail entry.

    Pure function (no I/O) so tests can assert on its shape and so the detector
    can seed the first entry inside the initial document write.

    Args:
        actor:  Who performed the action, e.g. ``"detector"``, ``"diagnoser"``,
                ``"decider"``, ``"remediator"``, ``"reporter"``, or
                ``"human:<name>"``.
        action: Short machine label, e.g. ``"diagnosed"``, ``"decided"``,
                ``"gate_opened"``, ``"gate_closed"``.
        detail: Human-readable description.
        extra:  Optional extra fields to record.

    Returns:
        An audit-entry dictionary.
    """
    entry = {
        "actor": actor,
        "action": action,
        "detail": detail,
        "at": time.time(),
    }
    if extra:
        entry["extra"] = extra
    return entry


def record_audit(
    incident_id: str, actor: str, action: str, detail: str, extra: dict | None = None
) -> dict:
    """Append an entry to the incident's append-only audit trail in Firestore.

    Uses ``ArrayUnion`` so entries are only ever added, never overwritten.

    Args:
        incident_id: Firestore document ID.
        actor:       Who performed the action.
        action:      Short machine label.
        detail:      Human-readable description.
        extra:       Optional extra fields.

    Returns:
        The audit entry that was appended.
    """
    entry = make_audit_entry(actor, action, detail, extra)
    _get_db().collection(COLLECTION).document(incident_id).update(
        {"audit_log": firestore.ArrayUnion([entry])}
    )
    logger.info("[audit] %s %s: %s", actor, action, detail)
    return entry


def request_approval(incident_id: str, decision: dict) -> dict:
    """Open the governance gate: hold the incident for one human decision.

    Sets status to ``awaiting_approval`` and records a pending approval request
    plus an audit entry.

    Args:
        incident_id: Firestore document ID.
        decision:    The decider's decision dict.

    Returns:
        The pending-approval record.
    """
    approval = {
        "state": "pending",
        "requested_action": decision.get("action"),
        "reason": decision.get("reason"),
        "requested_at": time.time(),
    }
    _get_db().collection(COLLECTION).document(incident_id).update(
        {"approval": approval, "status": "awaiting_approval"}
    )
    record_audit(
        incident_id,
        actor="aegis",
        action="gate_opened",
        detail=f"Awaiting human approval for '{decision.get('action')}'",
    )
    return approval


def resolve_approval(
    incident_id: str, approver: str, approved: bool, note: str = ""
) -> dict:
    """Apply the single human decision that closes the governance gate.

    Args:
        incident_id: Firestore document ID.
        approver:    Name/email of the human approver.
        approved:    True to approve the requested action, False to reject.
        note:        Optional reviewer note.

    Returns:
        The updated approval record.

    Raises:
        ValueError: If the incident or a pending approval does not exist.
    """
    doc_ref = _get_db().collection(COLLECTION).document(incident_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise ValueError(f"Incident {incident_id} not found")

    incident = doc.to_dict()
    approval = incident.get("approval")
    if not approval or approval.get("state") != "pending":
        raise ValueError(f"No pending approval for incident {incident_id}")

    approval.update({
        "state": "approved" if approved else "rejected",
        "approver": approver,
        "note": note,
        "resolved_at": time.time(),
    })
    # Approving returns the incident to "decided" so the supervisor can execute
    # the gated action; rejecting leaves it "escalated" (held, no action taken).
    new_status = "decided" if approved else "escalated"
    doc_ref.update({"approval": approval, "status": new_status})

    verdict = "approved" if approved else "rejected"
    detail = f"{verdict} '{approval.get('requested_action')}'"
    if note:
        detail += f" - {note}"
    record_audit(
        incident_id,
        actor=f"human:{approver}",
        action="gate_closed",
        detail=detail,
    )
    return approval
