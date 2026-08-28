#!/usr/bin/env python
"""detector.py - Receives Pub/Sub push events, classifies them, and writes
open incident records to Firestore.

Routes
------
POST /pubsub  - Pub/Sub push endpoint (JSON envelope with base64 ``data``).
GET  /healthz - Health-check used by Cloud Run and load balancers.

Environment variables
---------------------
PROJECT_ID  - GCP project (default: aegis-hackathon-506413).
PORT        - TCP port to listen on (default: 8080).
"""

import base64
import json
import logging
import os
import signal
import sys

from flask import Flask, request
from google.cloud import firestore

import diagnoser
import decider
import remediator
import reporter
import governance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID")
COLLECTION = "incidents"

_db = None
app = Flask(__name__)


def _get_db() -> firestore.Client:
    """Lazily construct and cache the Firestore client.

    Deferring construction keeps :func:`classify` importable without Google
    Cloud credentials (e.g. in CI unit tests).
    """
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID)
    return _db


def classify(event: dict) -> str | None:
    """Apply rule-based classification to an event dict.

    Rules are evaluated in priority order:
    1. status == "error"  -> tool_failure
    2. cost > 1.0 or tokens > 10 000  -> budget_exceeded
    3. pii_leak == True   -> pii_leak
    4. confidence < 0.5   -> low_confidence

    Args:
        event: Raw event dictionary from a fleet agent.

    Returns:
        Incident type string, or ``None`` if the event is healthy.
    """
    if event.get("status") == "error":
        return "tool_failure"

    cost = event.get("cost", 0.0)
    tokens = event.get("tokens", 0)
    if cost > 1.0 or tokens > 10_000:
        return "budget_exceeded"

    if event.get("pii_leak"):
        return "pii_leak"

    if event.get("confidence", 1.0) < 0.5:
        return "low_confidence"

    return None


def save_incident(event: dict, incident_type: str) -> str:
    """Write a new incident document to Firestore.

    Uses ``event_id`` as the Firestore document ID to guarantee
    natural deduplication — repeated deliveries of the same message
    simply overwrite the same document. Seeds the append-only audit trail
    with a ``detected`` entry.

    Args:
        event:         Raw event dictionary.
        incident_type: Classification result from :func:`classify`.

    Returns:
        The Firestore document ID (same as ``event_id``).

    Raises:
        ValueError: If ``event_id`` is missing from the event.
    """
    incident_id = event.get("event_id")
    if not incident_id:
        raise ValueError("Missing event_id in event")

    incident = {
        "incident_id": incident_id,
        "agent": event.get("agent", "unknown"),
        "type": incident_type,
        "raw_event": event,
        "status": "open",
        "created_at": firestore.SERVER_TIMESTAMP,
        "audit_log": [
            governance.make_audit_entry(
                "detector",
                "detected",
                f"{incident_type} for {event.get('agent', 'unknown')}",
            )
        ],
    }

    _get_db().collection(COLLECTION).document(incident_id).set(incident)
    logger.info("Incident saved: %s for %s", incident_type, event.get("agent"))
    return incident_id


@app.route("/pubsub", methods=["POST"])
def pubsub_push() -> tuple[dict, int]:
    """Handle Pub/Sub push messages.

    Expects the standard Pub/Sub push envelope::

        {
          "message": {
            "data": "<base64-encoded JSON event>"
          }
        }

    Returns HTTP 200 for healthy events and successfully saved incidents,
    400 for malformed requests, and 500 if the Firestore write fails.
    """
    envelope = request.get_json()
    if not envelope:
        logger.warning("No JSON payload received")
        return {"error": "Bad Request"}, 400

    message = envelope.get("message")
    if not message:
        logger.warning("No message in envelope")
        return {"error": "Bad Request"}, 400

    try:
        data_bytes = base64.b64decode(message.get("data", ""))
        event = json.loads(data_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as exc:
        logger.warning("Bad message format: %s", exc)
        return {"error": "Bad Request"}, 400

    if not isinstance(event, dict) or "event_id" not in event:
        logger.warning("Event missing event_id, dropping")
        return {"error": "Bad Request"}, 400

    incident_type = classify(event)
    if incident_type is None:
        logger.info("Healthy event from %s", event.get("agent", "unknown"))
        return {"status": "ok"}, 200

    try:
        incident_id = save_incident(event, incident_type)
    except Exception as exc:
        logger.error("Failed to save incident: %s", exc)
        return {"error": "Internal Server Error"}, 500

    auto_pipeline = os.environ.get("AEGIS_AUTO_PIPELINE", "false").lower() == "true"

    if auto_pipeline:
        try:
            diagnoser.diagnose_incident(incident_id)
        except Exception as exc:
            logger.error("Diagnosis failed: %s", exc)

        try:
            action = decider.decide_action(incident_id)
        except Exception as exc:
            logger.error("Decision failed: %s", exc)
            action = None

        if action:
            try:
                remediator.remediate_incident(incident_id, action)
            except Exception as exc:
                logger.error("Remediation failed: %s", exc)

        try:
            reporter.write_postmortem(incident_id)
        except Exception as exc:
            logger.error("Postmortem failed: %s", exc)
    else:
        logger.info("Auto-pipeline disabled; incident %s left open for ADK supervisor", incident_id)

    return {"status": "saved"}, 200


@app.route("/healthz", methods=["GET"])
def healthz() -> tuple[dict, int]:
    """Health-check endpoint used by Cloud Run."""
    return {"healthy": True}, 200


def _signal_handler(sig: int, frame: object) -> None:
    logger.info("Shutting down detector")
    sys.exit(0)


def main() -> None:
    """Start the Flask server bound to 0.0.0.0 on $PORT."""
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    port = int(os.environ.get("PORT", 8080))
    logger.info("Detector listening on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
