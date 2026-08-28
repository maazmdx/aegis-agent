#!/usr/bin/env python
"""decider.py - Decider sub-agent: applies a rule-based policy to choose an action.

The Decider is one of the specialist sub-agents the Aegis supervisor
orchestrates. It reads a diagnosed incident, applies a deterministic policy,
flags whether the chosen action must pass the human governance gate, and writes
the decision (plus an audit-trail entry) back to Firestore.

Environment variables
---------------------
PROJECT_ID - GCP project (default: aegis-hackathon-506413).
"""

import logging
import os

from google.cloud import firestore

import governance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID")
COLLECTION = "incidents"

_db = None


def _get_db() -> firestore.Client:
    """Lazily construct and cache the Firestore client (see governance._get_db)."""
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID)
    return _db


def decide(incident: dict) -> dict:
    """Apply the decision policy to a diagnosed incident.

    Policy (evaluated in priority order):

    1. ``pii_leak`` type → **quarantine** (always, regardless of diagnosis).
    2. ``severity == "high"`` → **escalate** for human review.
    3. Gemini recommended ``retry`` with non-high severity → **retry**.
    4. Fallback → honour the Gemini recommendation.

    Args:
        incident: Incident document dictionary including a ``diagnosis`` sub-dict.

    Returns:
        A ``{"action": str, "reason": str}`` dictionary.
    """
    diagnosis = incident.get("diagnosis", {})
    incident_type = incident.get("type", "")
    recommended = diagnosis.get("recommended_action", "escalate")
    severity = diagnosis.get("severity", "medium")

    if incident_type == "pii_leak":
        return {
            "action": "quarantine",
            "reason": "PII leak detected — automatic quarantine",
        }

    if severity == "high":
        return {
            "action": "escalate",
            "reason": "High severity incident — escalating for human review",
        }

    if recommended == "retry" and severity != "high":
        return {
            "action": "retry",
            "reason": f"Gemini recommended retry (severity: {severity})",
        }

    return {
        "action": recommended,
        "reason": f"Following Gemini recommendation: {recommended}",
    }


def decide_action(incident_id: str) -> str:
    """Decide and persist the action for a single incident.

    Reads the incident from Firestore, runs :func:`decide`, flags whether the
    action requires human approval, writes the ``decision`` field, updated
    ``status``, and an audit entry back.

    Args:
        incident_id: Firestore document ID.

    Returns:
        The chosen action string (``"retry"``, ``"quarantine"``, or ``"escalate"``).

    Raises:
        ValueError: If the incident document does not exist.
    """
    doc_ref = _get_db().collection(COLLECTION).document(incident_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise ValueError(f"Incident {incident_id} not found")

    incident = doc.to_dict()
    decision = decide(incident)
    decision["requires_approval"] = governance.requires_approval(
        decision, incident.get("diagnosis", {})
    )

    doc_ref.update({"decision": decision, "status": "decided"})

    action = decision["action"]
    governance.record_audit(
        incident_id,
        actor="decider",
        action="decided",
        detail=f"{action} — {decision['reason']}",
    )
    logger.info("Decided %s → %s (%s)", incident_id, action, decision["reason"])
    return action
