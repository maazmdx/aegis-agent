#!/usr/bin/env python
"""remediator.py - Remediator sub-agent: executes remediation actions.

The Remediator is one of the specialist sub-agents the Aegis supervisor
orchestrates. It executes the action chosen by the Decider, but first enforces
the governance gate: high-impact actions (escalate/quarantine or any
high-severity incident) are held for one human decision unless auto-approve is
enabled. Every action is written to the incident's append-only audit trail.

Three handlers are supported:

* **retry** - re-emits a healthy event for the agent via :mod:`fleet`.
* **quarantine** - writes the agent to the ``quarantine`` Firestore collection.
* **escalate** - marks the incident for human review (no automated action).

Environment variables
---------------------
PROJECT_ID         - GCP project (default: aegis-hackathon-506413).
AEGIS_AUTO_APPROVE - see :mod:`governance`.
"""

import logging
import os
import time

from google.cloud import firestore

import governance
from fleet import emit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID")
COLLECTION = "incidents"

_db = None


def _get_db() -> firestore.Client:
    """Lazily construct and cache the Firestore client."""
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID)
    return _db


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

    _get_db().collection("quarantine").document(agent).set({
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

    Enforces the governance gate before executing high-impact actions:

    * If the action is gated and auto-approve is disabled, and no human has
      approved yet, the incident is held at status ``awaiting_approval`` and no
      action is taken (returns ``outcome == "awaiting_approval"``).
    * If a human previously rejected the action, it is held
      (``outcome == "held"``).
    * Otherwise (auto-approve on, or a human approved) the action executes.

    Args:
        incident_id: Firestore document ID.
        action:      One of ``"retry"``, ``"quarantine"``, or ``"escalate"``.

    Returns:
        The remediation outcome dictionary.

    Raises:
        ValueError: If the incident document does not exist.
    """
    doc_ref = _get_db().collection(COLLECTION).document(incident_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise ValueError(f"Incident {incident_id} not found")

    incident = doc.to_dict()
    decision = incident.get("decision", {})
    diagnosis = incident.get("diagnosis", {})
    approval = incident.get("approval", {}) or {}

    # --- Governance gate -------------------------------------------------
    if governance.requires_approval(decision, diagnosis) and not governance.auto_approve_enabled():
        if approval.get("state") == "rejected":
            outcome = {
                "outcome": "held",
                "detail": "Action rejected by human reviewer",
                "at": time.time(),
            }
            doc_ref.update({"remediation": outcome, "status": "escalated"})
            governance.record_audit(
                incident_id, "remediator", "held", outcome["detail"]
            )
            return outcome

        if approval.get("state") != "approved":
            governance.request_approval(incident_id, decision)
            outcome = {
                "outcome": "awaiting_approval",
                "detail": f"Gated action '{action}' awaiting one human decision",
                "at": time.time(),
            }
            logger.info("Gate held %s for action %s", incident_id, action)
            return outcome
    # ---------------------------------------------------------------------

    handler = _HANDLERS.get(action, remediate_escalate)
    remediation = handler(incident)

    new_status = "escalated" if action == "escalate" else "resolved"

    doc_ref.update({"remediation": remediation, "status": new_status})
    governance.record_audit(
        incident_id,
        "remediator",
        "remediated",
        f"{action} -> {remediation['outcome']}",
    )
    logger.info(
        "Remediated %s → %s: %s", incident_id, action, remediation["outcome"]
    )
    return remediation
