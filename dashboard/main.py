#!/usr/bin/env python
"""dashboard/main.py - Live Aegis fleet dashboard served by Flask.

Reads incident data from Firestore in real time and renders a Material
Design 3 dark-theme UI.  The ``/incident/<id>`` route returns JSON so the
"Review" modal can fetch live triage details without a full page reload.

Routes
------
GET /           – Main dashboard page (auto-refreshes every 5 s via JS).
GET /incident/<id> – JSON detail for one incident (used by the Review modal).
GET /healthz    – Health-check endpoint.

Environment variables
---------------------
PROJECT_ID – GCP project (default: aegis-hackathon-506413).
PORT       – TCP port to bind (default: 8080).
"""

import base64
import json
import logging
import os
import time
import uuid
from collections import defaultdict

import requests as http_requests
from flask import Flask, jsonify, render_template, request
from google.cloud import firestore
from google import genai

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "aegis-hackathon-506413")
COLLECTION = "incidents"

_db = None

def _get_db():
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID)
    return _db

ACTIVE_STATUSES = {"open", "diagnosed", "decided", "remediating"}
AGENTS = ["invoice-agent", "support-agent", "research-agent"]

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Pure helper (no Firestore dependency — easily unit-tested)
# ---------------------------------------------------------------------------

def compute_mttd(deltas: list[float]) -> float | str:
    """Compute mean time to diagnose from a list of valid deltas in seconds.

    Args:
        deltas: List of positive float values, each < 3 600 s.

    Returns:
        Rounded average (1 decimal) or the em-dash string ``"—"`` when empty.
    """
    if not deltas:
        return "\u2014"
    return round(sum(deltas) / len(deltas), 1)


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def get_dashboard_data() -> tuple[list, list, dict]:
    """Fetch agent stats, recent incidents, and summary metrics from Firestore.

    Returns:
        Tuple of (agents list, incidents list, metrics dict).
    """
    db = _get_db()
    docs = (
        db.collection(COLLECTION)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(100)
        .stream()
    )

    incidents: list[dict] = []
    active_counts: dict = defaultdict(int)
    total_counts: dict = defaultdict(int)

    total_incidents = 0
    resolved_incidents = 0
    diagnose_times: list[float] = []
    tokens_flagged = 0
    cost_flagged = 0.0

    for doc in docs:
        data = doc.to_dict()
        agent = data.get("agent", "?")
        status = data.get("status", "?")
        diagnosis = data.get("diagnosis", {})
        decision = data.get("decision", {})
        raw_event = data.get("raw_event", {})

        total_counts[agent] += 1
        if status in ACTIVE_STATUSES:
            active_counts[agent] += 1

        total_incidents += 1
        if status in ("resolved", "escalated"):
            resolved_incidents += 1

        created_at = data.get("created_at")
        diagnosed_at = data.get("diagnosed_at")
        if created_at and diagnosed_at:
            try:
                delta = (diagnosed_at - created_at).total_seconds()
                if 0 < delta < 3600:
                    diagnose_times.append(delta)
            except Exception:
                pass

        tokens_flagged += raw_event.get("tokens", 0)
        cost_flagged += raw_event.get("cost", 0.0)

        incidents.append({
            "incident_id": data.get("incident_id", doc.id),
            "agent": agent,
            "type": data.get("type", "?"),
            "severity": diagnosis.get("severity", "-"),
            "action": decision.get("action", ""),
            "status": status,
        })

    agents = [
        {
            "name": name,
            "active_count": active_counts.get(name, 0),
            "total_count": total_counts.get(name, 0),
        }
        for name in AGENTS
    ]

    auto_resolved_pct = (
        round((resolved_incidents / total_incidents) * 100)
        if total_incidents else 0
    )

    metrics = {
        "total_incidents": total_incidents,
        "auto_resolved_pct": auto_resolved_pct,
        "mttd": compute_mttd(diagnose_times),
        "tokens_flagged": tokens_flagged,
        "cost_flagged": cost_flagged,
    }

    return agents, incidents[:50], metrics


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Render the main dashboard page."""
    try:
        agents, incidents, metrics = get_dashboard_data()
    except Exception as exc:
        logger.error("Failed to load dashboard data: %s", exc)
        agents, incidents = [], []
        metrics = {
            "total_incidents": 0,
            "auto_resolved_pct": 0,
            "mttd": "\u2014",
            "tokens_flagged": 0,
            "cost_flagged": 0.0,
        }
    return render_template(
        "index.html", agents=agents, incidents=incidents, metrics=metrics,
        project_id=PROJECT_ID,
    )


@app.route("/incident/<incident_id>")
def incident_detail(incident_id: str):
    """Return full incident detail as JSON for the Review modal.

    Args:
        incident_id: Firestore document ID.
    """
    db = _get_db()  # reuse cached client — no new connection per request
    doc = db.collection(COLLECTION).document(incident_id).get()
    if not doc.exists:
        return jsonify({"error": "not found"}), 404

    data = doc.to_dict()

    # Serialise all Firestore timestamp objects (not JSON-serialisable).
    def _ts(val):
        if val is None:
            return None
        if hasattr(val, "isoformat"):
            return val.isoformat()
        return str(val)

    data["created_at"]   = _ts(data.get("created_at"))
    data["diagnosed_at"] = _ts(data.get("diagnosed_at"))

    # Serialise timestamps inside audit_log entries.
    for entry in data.get("audit_log", []):
        if isinstance(entry, dict) and "ts" in entry:
            entry["ts"] = _ts(entry["ts"])

    data.setdefault("incident_id", incident_id)
    return jsonify(data)

@app.route("/api/trigger", methods=["POST"])
def trigger_incident():
    """Trigger a simulated PII-leak incident by pushing directly to the local detector.

    This bypasses Google Cloud Pub/Sub so the full triage pipeline can be
    tested locally without any cloud routing.
    """
    try:
        event = {
            "event_id": str(uuid.uuid4()),
            "agent": "support-agent",
            "status": "success",
            "tokens": 800,
            "cost": 0.10,
            "tool_call": "lookup_customer",
            "confidence": 0.95,
            "pii_leak": True,
            "timestamp": time.time(),
        }
        # Wrap in a standard Pub/Sub push envelope so detector.py can decode it.
        payload = json.dumps(event).encode("utf-8")
        envelope = {"message": {"data": base64.b64encode(payload).decode("utf-8")}}

        detector_url = os.environ.get("DETECTOR_URL", "http://localhost:8080")
        resp = http_requests.post(
            f"{detector_url}/pubsub",
            json=envelope,
            timeout=60,
        )
        if resp.ok:
            return jsonify({"status": "Incident injected and triage pipeline triggered"}), 200
        else:
            return jsonify({"error": f"Detector returned {resp.status_code}: {resp.text}"}), 502
    except Exception as exc:
        logger.error("trigger_incident error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/healthz")
def healthz():
    """Health-check endpoint."""
    return jsonify({"healthy": True})


@app.route("/api/chat", methods=["POST"])
def chat():
    """Reporting Agent: Compiles incidents from Firestore and uses Gemini to generate a report."""
    try:
        user_msg = request.json.get("message", "")
        if not user_msg:
            return jsonify({"error": "Empty message"}), 400
        
        # 1. Invoke Tool: Fetch all incidents from Firestore
        db = _get_db()
        docs = db.collection(COLLECTION).stream()
        
        # 2. Compile data context for the agent
        compiled_data = []
        for doc in docs:
            d = doc.to_dict()
            compiled_data.append({
                "id": d.get("incident_id", doc.id),
                "agent": d.get("agent"),
                "type": d.get("type"),
                "status": d.get("status"),
                "severity": d.get("diagnosis", {}).get("severity", "unknown")
            })
            
        import json
        context_str = json.dumps(compiled_data, indent=2)
        
        # 3. Use Gemini to reason over the data and generate the report
        from google import genai
        client = genai.Client(vertexai=True, project=os.environ.get("PROJECT_ID"), location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"))
        model_name = os.environ.get("MODEL", "gemini-3.5-flash-lite")
        
        prompt = (
            f"You are the Aegis Reporting Agent. You have access to the live incident database.\n"
            f"Here is the current live data of all incidents in the system:\n"
            f"```json\n{context_str}\n```\n\n"
            f"The user has asked: '{user_msg}'\n\n"
            f"Write a concise, professional report or answer based ONLY on the data provided above. "
            f"Use markdown formatting, bullet points, and highlight key metrics. "
            f"Do not hallucinate any data not in the JSON."
        )
        
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
        )
        
        return jsonify({"reply": response.text}), 200
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the Flask development server."""
    port = int(os.environ.get("PORT", 8080))
    logger.info("Aegis Dashboard starting on http://localhost:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
