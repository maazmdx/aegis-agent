#!/usr/bin/env python
"""reporter.py - Writes postmortems and optionally notifies Slack.

A postmortem is a short Markdown document summarising what happened, the
root cause, the action taken, and the final outcome.  If ``SLACK_WEBHOOK_URL``
is set the summary is also posted to a Slack channel.

Environment variables
---------------------
PROJECT_ID        – GCP project (default: aegis-hackathon-506413).
SLACK_WEBHOOK_URL – Incoming webhook URL (optional; Slack posting is skipped
                    when not set).
"""

import logging
import os
import time

import requests
from google.cloud import firestore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
COLLECTION = "incidents"

_db = None


def _get_db() -> firestore.Client:
    """Lazily construct and cache the Firestore client (see governance._get_db)."""
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID)
    return _db


def build_postmortem(incident: dict) -> str:
    """Build a short Markdown postmortem for an incident.

    Args:
        incident: Fully resolved incident document dictionary.

    Returns:
        Markdown-formatted postmortem string.
    """
    diagnosis = incident.get("diagnosis", {})
    decision = incident.get("decision", {})
    remediation = incident.get("remediation", {})

    return (
        f"## Postmortem: {incident.get('type', '?')} — {incident.get('agent', '?')}\n\n"
        f"**What happened:** `{incident.get('type')}` incident detected for "
        f"agent `{incident.get('agent')}`.\n\n"
        f"**Root cause:** {diagnosis.get('root_cause', 'Unknown')}\n\n"
        f"**Severity:** {diagnosis.get('severity', '?')} | "
        f"**Confidence:** {diagnosis.get('confidence', '?')}\n\n"
        f"**Action taken:** {decision.get('action', '?')} — "
        f"{decision.get('reason', '?')}\n\n"
        f"**Outcome:** {remediation.get('outcome', '?')} — "
        f"{remediation.get('detail', '?')}\n\n"
        f"**Final status:** {incident.get('status', '?')}\n"
    )


def post_to_slack(incident: dict) -> str | None:
    """POST a compact summary to Slack.

    Does nothing and returns ``None`` if ``SLACK_WEBHOOK_URL`` is not set.

    Args:
        incident: Incident document dictionary.

    Returns:
        A timestamp string if the post succeeded, otherwise ``None``.
    """
    if not SLACK_WEBHOOK_URL:
        return None

    diagnosis = incident.get("diagnosis", {})
    decision = incident.get("decision", {})
    remediation = incident.get("remediation", {})

    prefix = "[OK]" if incident.get("status") == "resolved" else "[ALERT]"
    payload = {
        "text": (
            f"{prefix} Aegis Postmortem: `{incident.get('type')}` "
            f"for `{incident.get('agent')}`\n"
            f"Root cause: {diagnosis.get('root_cause', '?')}\n"
            f"Action: {decision.get('action', '?')} → "
            f"{remediation.get('outcome', '?')}\n"
            f"Severity: {diagnosis.get('severity', '?')}"
        ),
    }

    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        return str(time.time())
    except Exception as exc:
        logger.warning("Slack post failed: %s", exc)
        return None


def write_postmortem(incident_id: str) -> str:
    """Write a postmortem for a single incident and persist it to Firestore.

    Idempotent — returns the existing postmortem if one was already written.

    Args:
        incident_id: Firestore document ID.

    Returns:
        The Markdown postmortem string.

    Raises:
        ValueError: If the incident document does not exist.
    """
    doc_ref = _get_db().collection(COLLECTION).document(incident_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise ValueError(f"Incident {incident_id} not found")

    incident = doc.to_dict()

    if incident.get("postmortem"):
        logger.info("Postmortem already exists for %s", incident_id)
        return incident["postmortem"]["summary"]

    postmortem_md = build_postmortem(incident)
    slack_ts = post_to_slack(incident)

    doc_ref.update({
        "postmortem": {
            "summary": postmortem_md,
            "slack_ts": slack_ts,
        },
    })

    slack_label = "yes" if slack_ts else "no"
    logger.info("Postmortem written for %s (slack: %s)", incident_id, slack_label)
    return postmortem_md
