#!/usr/bin/env python
"""scripts/seed_demo.py - Seed realistic demo data into Firestore.

Creates 15 sample incidents with varied agents, types, severities,
and statuses so the dashboard has something to display immediately.
"""

import logging
import random
import uuid
from datetime import datetime, timezone, timedelta

from google.cloud import firestore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

AGENTS = ["invoice-agent", "support-agent", "research-agent"]
TYPES = ["tool_failure", "budget_exceeded", "pii_leak", "low_confidence"]
STATUSES = ["open", "resolved", "escalated"]


def main() -> None:
    """Clear existing data and seed 15 demo incidents."""
    logger.info("Clearing existing data...")
    db = firestore.Client()
    for coll_name in ["incidents", "quarantine"]:
        docs = db.collection(coll_name).stream()
        for doc in docs:
            doc.reference.delete()

    logger.info("Seeding demo data...")
    now = datetime.now(timezone.utc)

    count = 0
    for _ in range(15):
        incident_id = str(uuid.uuid4())
        created_at = now - timedelta(hours=random.uniform(0.1, 24))
        dt = random.uniform(2, 15)
        diagnosed_at = created_at + timedelta(seconds=dt)

        status = random.choice(STATUSES)
        incident_type = random.choice(TYPES)
        agent = random.choice(AGENTS)

        severity = "low"
        if incident_type == "tool_failure":
            severity = "medium"
        elif incident_type == "pii_leak":
            severity = "high"

        action = "retry" if status == "resolved" else "escalate"

        incident = {
            "incident_id": incident_id,
            "agent": agent,
            "type": incident_type,
            "status": status,
            "created_at": created_at,
            "raw_event": {
                "tokens": random.randint(100, 5000),
                "cost": round(random.uniform(0.1, 5.0), 2),
            },
        }

        if status in ["resolved", "escalated"]:
            incident["diagnosed_at"] = diagnosed_at
            incident["diagnosis"] = {
                "severity": severity,
                "root_cause": "Test root cause",
                "recommended_action": action,
                "confidence": round(random.uniform(0.7, 1.0), 2),
            }
            incident["decision"] = {
                "action": action,
                "reason": "Test decision",
            }
            if status == "resolved":
                incident["remediation"] = {
                    "outcome": "success",
                    "detail": "Test remediation",
                }
                incident["postmortem"] = {
                    "summary": (
                        "## Postmortem: {t} — {a}\n\n"
                        "**What happened:** `{t}` incident for agent `{a}`.\n\n"
                        "**Root cause:** Test root cause\n\n"
                        "**Action taken:** {act}\n\n"
                        "**Outcome:** success"
                    ).format(t=incident_type, a=agent, act=action),
                    "slack_ts": None,
                }

        db.collection("incidents").document(incident_id).set(incident)
        count += 1

    logger.info("Seeded %d incidents successfully.", count)


if __name__ == "__main__":
    main()
