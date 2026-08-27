#!/usr/bin/env python
"""decider.py - Applies a rule-based policy to choose a remediation action.

The module also exposes :func:`decide_action` which reads from and writes back
to Firestore, making it directly callable by the ADK agent tools wrapper.

Environment variables
---------------------
PROJECT_ID – GCP project (default: aegis-hackathon-506413).
"""

import logging
import os

from google.cloud import firestore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID")
COLLECTION = "incidents"

_db = firestore.Client(project=PROJECT_ID)


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

    Reads the incident from Firestore, runs :func:`decide`, and writes
    the ``decision`` field and updated ``status`` back.

    Args:
        incident_id: Firestore document ID.

    Returns:
        The chosen action string (``"retry"``, ``"quarantine"``, or ``"escalate"``).

    Raises:
        ValueError: If the incident document does not exist.
    """
    doc_ref = _db.collection(COLLECTION).document(incident_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise ValueError(f"Incident {incident_id} not found")

    incident = doc.to_dict()
    decision = decide(incident)

    doc_ref.update({"decision": decision, "status": "decided"})

    action = decision["action"]
    logger.info("Decided %s → %s (%s)", incident_id, action, decision["reason"])
    return action
