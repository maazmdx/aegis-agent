"""tools.py - ADK tool functions exposed to the Aegis Supervisor agent.

Each public function in this module is registered as a callable tool in
``agent.py``.  The type hints and docstrings are what the LLM reads to
decide when and how to call each tool, so they must be precise.
"""

import os
import sys

# Make sibling root-level modules importable from this package directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import firestore  # noqa: E402 (after sys.path manipulation)

import diagnoser   # noqa: E402
import decider     # noqa: E402
import remediator  # noqa: E402
import reporter    # noqa: E402

PROJECT_ID = os.environ.get("PROJECT_ID")
COLLECTION = "incidents"

_db = firestore.Client(project=PROJECT_ID)


def get_open_incidents() -> list[dict]:
    """Retrieve all incidents that currently have status ``"open"``.

    Returns:
        List of incident dictionaries, each containing at minimum
        ``incident_id``, ``agent``, ``type``, and ``status``.
    """
    docs = _db.collection(COLLECTION).where("status", "==", "open").stream()
    return [doc.to_dict() for doc in docs]


def diagnose(incident_id: str) -> dict:
    """Diagnose an open incident using Gemini and write the result to Firestore.

    Args:
        incident_id: The Firestore document ID of the incident to diagnose.

    Returns:
        Diagnosis dictionary with keys ``root_cause``, ``severity``,
        ``confidence``, and ``recommended_action``.
    """
    return diagnoser.diagnose_incident(incident_id)


def decide_action(incident_id: str) -> str:
    """Choose a remediation action for a diagnosed incident.

    Applies the rule-based policy (PII → quarantine, high severity → escalate,
    etc.) and writes the decision back to Firestore.

    Args:
        incident_id: The Firestore document ID of the incident.

    Returns:
        Action string — one of ``"retry"``, ``"quarantine"``, or ``"escalate"``.
    """
    return decider.decide_action(incident_id)


def remediate(incident_id: str, action: str) -> dict:
    """Execute the chosen remediation action for an incident.

    Args:
        incident_id: The Firestore document ID of the incident.
        action:      The action to perform.  Must be one of ``"retry"``,
                     ``"quarantine"``, or ``"escalate"``.

    Returns:
        Outcome dictionary with keys ``outcome``, ``detail``, and ``at``.
    """
    return remediator.remediate_incident(incident_id, action)


def write_postmortem(incident_id: str) -> str:
    """Write a Markdown postmortem for a fully-resolved incident.

    Also posts a compact summary to Slack if ``SLACK_WEBHOOK_URL`` is set.
    Idempotent — safe to call multiple times.

    Args:
        incident_id: The Firestore document ID of the incident.

    Returns:
        The Markdown postmortem string.
    """
    return reporter.write_postmortem(incident_id)
