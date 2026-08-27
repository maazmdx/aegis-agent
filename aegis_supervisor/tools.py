"""tools.py - ADK tool functions exposed to the Aegis Supervisor agent.

The Aegis supervisor does not do the detailed work itself. It orchestrates a
team of specialist sub-agents, each surfaced here as a callable tool:

* **Diagnoser**  -> :func:`diagnose`         (Gemini root-cause analysis)
* **Decider**    -> :func:`decide_action`    (policy-based action selection)
* **Remediator** -> :func:`remediate`        (executes the fix, enforces the gate)
* **Reporter**   -> :func:`write_postmortem` (documents and closes the incident)

Governance tools let a human open/close the approval gate and inspect the
audit trail. The type hints and docstrings are what the LLM reads to decide
when and how to call each tool, so they must be precise.
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
import governance  # noqa: E402

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
    """Delegate to the Diagnoser sub-agent to root-cause an open incident.

    Uses Gemini and writes the result to Firestore.

    Args:
        incident_id: The Firestore document ID of the incident to diagnose.

    Returns:
        Diagnosis dictionary with keys ``root_cause``, ``severity``,
        ``confidence``, and ``recommended_action``.
    """
    return diagnoser.diagnose_incident(incident_id)


def decide_action(incident_id: str) -> str:
    """Delegate to the Decider sub-agent to choose a remediation action.

    Applies the rule-based policy (PII → quarantine, high severity → escalate,
    etc.), flags whether the action needs human approval, and writes the
    decision back to Firestore.

    Args:
        incident_id: The Firestore document ID of the incident.

    Returns:
        Action string — one of ``"retry"``, ``"quarantine"``, or ``"escalate"``.
    """
    return decider.decide_action(incident_id)


def remediate(incident_id: str, action: str) -> dict:
    """Delegate to the Remediator sub-agent to execute the chosen action.

    High-impact actions (escalate/quarantine or high-severity incidents) are
    held at the governance gate when auto-approve is disabled: the returned
    ``outcome`` will be ``"awaiting_approval"`` and no action is taken until a
    human decides via :func:`apply_human_decision`.

    Args:
        incident_id: The Firestore document ID of the incident.
        action:      The action to perform.  Must be one of ``"retry"``,
                     ``"quarantine"``, or ``"escalate"``.

    Returns:
        Outcome dictionary with keys ``outcome``, ``detail``, and ``at``.
    """
    return remediator.remediate_incident(incident_id, action)


def write_postmortem(incident_id: str) -> str:
    """Delegate to the Reporter sub-agent to write a Markdown postmortem.

    Also posts a compact summary to Slack if ``SLACK_WEBHOOK_URL`` is set.
    Idempotent — safe to call multiple times.

    Args:
        incident_id: The Firestore document ID of the incident.

    Returns:
        The Markdown postmortem string.
    """
    return reporter.write_postmortem(incident_id)


def get_incidents_awaiting_approval() -> list[dict]:
    """List incidents held at the governance gate awaiting a human decision.

    Returns:
        List of incident dictionaries with status ``"awaiting_approval"``.
    """
    docs = _db.collection(COLLECTION).where(
        "status", "==", "awaiting_approval"
    ).stream()
    return [doc.to_dict() for doc in docs]


def apply_human_decision(
    incident_id: str, approver: str, approved: bool, note: str = ""
) -> dict:
    """Record the single human decision that closes the governance gate.

    This represents the deterministic "one human decision" boundary: only a
    human (not the supervisor) may approve or reject a gated action. Approving
    returns the incident to ``"decided"`` so the supervisor can execute it;
    rejecting leaves it ``"escalated"``.

    Args:
        incident_id: The Firestore document ID of the incident.
        approver:    Name or email of the human approver.
        approved:    True to approve the requested action, False to reject.
        note:        Optional reviewer note.

    Returns:
        The updated approval record.
    """
    return governance.resolve_approval(incident_id, approver, approved, note)


def get_audit_trail(incident_id: str) -> list[dict]:
    """Return the append-only audit trail for an incident.

    Args:
        incident_id: The Firestore document ID of the incident.

    Returns:
        Ordered list of audit entries (``actor``, ``action``, ``detail``, ``at``).

    Raises:
        ValueError: If the incident document does not exist.
    """
    doc = _db.collection(COLLECTION).document(incident_id).get()
    if not doc.exists:
        raise ValueError(f"Incident {incident_id} not found")
    return doc.to_dict().get("audit_log", [])
