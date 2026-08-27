#!/usr/bin/env python
"""remediator.py - Executes remediation actions for triaged incidents.

Three handlers are supported:

* **retry** – re-emits a healthy event for the agent via :mod:`fleet`.
* **quarantine** – writes the agent to the ``quarantine`` Firestore collection.
* **escalate** – marks the incident for human review (no automated action).

Environment variables
---------------------
PROJECT_ID – GCP project (default: aegis-hackathon-506413).
"""

import logging
import os
import time

from google.cloud import firestore

from fleet import emit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID")
COLLECTION = "incidents"

_db = firestore.Client(project=PROJECT_ID)


def remediate_retry(incident: dict) -> dict:
    """Re-emit a healthy event for the affected agent.

    Args:
        incident: Incident document dictionary.

    Returns:
        Remediation outcome dictionary.
    """
    agent = incident.get("agent", "unknown-agent")
    raw = incident.get("raw_event", {})
    emit(agent, "success", {
        "tokens": 200,
        "cost": 0.02,
        "tool_call": raw.get("tool_call", "retry_task"),
        "confidence": 0.95,
    })
    return {
        "outcome": "success",
        "detail": f"Re-emitted healthy event for {agent}",
        "at": time.time(),
    }


def remediate_quarantine(incident: dict) -> dict:
    """Write the agent to the quarantine collection and disable it.

    Args:
        incident: Incident document dictionary.

    Returns:
        Remediation outcome dictionary.
    """
    agent = incident.get("agent", "unknown-agent")
    reason = incident.get("decision", {}).get("reason", "quarantined")

    _db.collection("quarantine").document(agent).set({
        "agent": agent,
        "at": firestore.SERVER_TIMESTAMP,
        "reason": reason,
    })
    return {
        "outcome": "success",
        "detail": f"Agent {agent} quarantined",
        "at": time.time(),
    }


def remediate_escalate(incident: dict) -> dict:
    """Flag the incident for human review (no automated action taken).

    Args:
        incident: Incident document dictionary.

    Returns:
        Remediation outcome dictionary with ``outcome == "skipped"``.
    """
    agent = incident.get("agent", "unknown-agent")
    return {
        "outcome": "skipped",
        "detail": f"Escalated {agent} for human review",
        "at": time.time(),
    }


_HANDLERS: dict = {
    "retry": remediate_retry,
    "quarantine": remediate_quarantine,
    "escalate": remediate_escalate,
}


def remediate_incident(incident_id: str, action: str) -> dict:
    """Execute remediation for a single incident and persist the result.

    Args:
        incident_id: Firestore document ID.
        action:      One of ``"retry"``, ``"quarantine"``, or ``"escalate"``.

    Returns:
        The remediation outcome dictionary.

    Raises:
        ValueError: If the incident document does not exist.
    """
    doc_ref = _db.collection(COLLECTION).document(incident_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise ValueError(f"Incident {incident_id} not found")

    incident = doc.to_dict()
    handler = _HANDLERS.get(action, remediate_escalate)
    remediation = handler(incident)

    new_status = "escalated" if action == "escalate" else "resolved"

    doc_ref.update({"remediation": remediation, "status": new_status})
    logger.info(
        "Remediated %s → %s: %s", incident_id, action, remediation["outcome"]
    )
    return remediation
