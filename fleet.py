#!/usr/bin/env python
"""fleet.py - Simulated worker agents that publish events to a Pub/Sub topic.

Each agent function emits one event. The module-level ``emit`` function
handles serialisation and publishing so callers just pass structured dicts.
"""

import json
import logging
import os
import random
import signal
import sys
import time
import uuid

from google.cloud import pubsub_v1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID")
TOPIC_ID = "fleet-events"

_publisher = pubsub_v1.PublisherClient()
_topic_path = _publisher.topic_path(PROJECT_ID, TOPIC_ID)


def emit(agent: str, status: str, extra: dict | None = None) -> dict:
    """Build one event dict, publish it to Pub/Sub, and return the event.

    Args:
        agent:  Logical agent name (e.g. ``"invoice-agent"``).
        status: ``"success"`` or ``"error"``.
        extra:  Optional fields merged into the event before publishing.

    Returns:
        The full event dictionary that was published.
    """
    event: dict = {
        "event_id": str(uuid.uuid4()),
        "agent": agent,
        "status": status,
        "tokens": 0,
        "cost": 0.0,
        "tool_call": "",
        "confidence": 1.0,
        "pii_leak": False,
        "timestamp": time.time(),
    }
    if extra:
        event.update(extra)

    data = json.dumps(event).encode("utf-8")
    msg_id = _publisher.publish(_topic_path, data).result()
    logger.info("%s | %s | msg_id=%s", agent, status, msg_id)
    return event


def invoice_agent() -> dict:
    """Emit a healthy success event for invoice-agent."""
    return emit("invoice-agent", "success", {
        "tokens": random.randint(100, 2000),
        "cost": round(random.uniform(0.01, 0.50), 4),
        "tool_call": "extract_total",
        "confidence": round(random.uniform(0.85, 1.0), 2),
    })


def support_agent() -> dict:
    """Emit a healthy success event for support-agent."""
    return emit("support-agent", "success", {
        "tokens": random.randint(100, 2000),
        "cost": round(random.uniform(0.01, 0.50), 4),
        "tool_call": "classify_ticket",
        "confidence": round(random.uniform(0.85, 1.0), 2),
    })


def fail_tool_call() -> dict:
    """Emit an error event from support-agent (tool failure)."""
    return emit("support-agent", "error", {
        "tool_call": "classify_ticket",
        "confidence": 0.3,
        "tokens": 500,
        "cost": 0.05,
    })


def fail_budget() -> dict:
    """Emit a budget-exceeded event from invoice-agent."""
    return emit("invoice-agent", "success", {
        "tool_call": "extract_total",
        "tokens": 50000,
        "cost": 5.00,
        "confidence": 0.9,
    })


def fail_pii() -> dict:
    """Emit a PII-leak event from support-agent."""
    return emit("support-agent", "success", {
        "tool_call": "lookup_customer",
        "pii_leak": True,
        "tokens": 800,
        "cost": 0.10,
        "confidence": 0.95,
    })


def _signal_handler(sig: int, frame: object) -> None:
    logger.info("Shutting down fleet simulator")
    sys.exit(0)


def main() -> None:
    """Run the fleet simulator loop, emitting two events every 3 seconds."""
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("Fleet simulator publishing to %s", _topic_path)
    while True:
        invoice_agent()
        support_agent()
        time.sleep(3)


if __name__ == "__main__":
    main()
