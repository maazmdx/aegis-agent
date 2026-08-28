#!/usr/bin/env python
"""diagnoser.py - Diagnoser sub-agent: root-cause analysis via the Gemini API.

The Diagnoser is one of the specialist sub-agents the Aegis supervisor
orchestrates. Given an incident ID it fetches the Firestore document, builds a
structured prompt, calls Gemini with retries, parses the JSON response, writes
the diagnosis back to Firestore, and records an audit-trail entry.

Environment variables
---------------------
PROJECT_ID     - GCP project (default: aegis-hackathon-506413).
GEMINI_API_KEY - AI Studio API key (required; no default committed).
MODEL          - Gemini model to use (default: gemini-3.1-flash-lite).
"""

import json
import logging
import os
import re

from google import genai
from google.cloud import firestore
from tenacity import retry, stop_after_attempt, wait_exponential

import governance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("MODEL", "gemini-3.1-flash-lite")
COLLECTION = "incidents"

_client = None
_db = None


def _get_client() -> genai.Client:
    """Lazily construct and cache the Gemini client.

    Deferring construction lets pure helpers (parse_diagnosis, build_prompt)
    import without a valid API key, e.g. in CI unit tests.
    """
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _get_db() -> firestore.Client:
    """Lazily construct and cache the Firestore client."""
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID)
    return _db

# Used when Gemini fails or returns unparseable output.
_SAFE_DIAGNOSIS: dict = {
    "root_cause": "Unable to determine root cause (Gemini parse error)",
    "severity": "medium",
    "confidence": 0.0,
    "recommended_action": "escalate",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def ask_gemini(prompt: str) -> str:
    """Call Gemini with exponential-backoff retries.

    Args:
        prompt: The fully-formed user prompt to send.

    Returns:
        Raw text response from the model.
    """
    response = _get_client().models.generate_content(
        model=MODEL,
        contents=prompt,
        config={
            "system_instruction": (
                "You are Aegis, an AI reliability supervisor. "
                "Respond with STRICT JSON."
            )
        },
    )
    return response.text


def parse_diagnosis(raw: str) -> dict:
    """Strip markdown fences and parse JSON. Fall back to safe defaults.

    Args:
        raw: The raw string returned by :func:`ask_gemini`.

    Returns:
        A validated diagnosis dictionary.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        result = json.loads(cleaned)
        for key in ("root_cause", "severity", "confidence", "recommended_action"):
            if key not in result:
                logger.warning(
                    "Missing key '%s' in Gemini response, using safe default", key
                )
                return dict(_SAFE_DIAGNOSIS)
        return result
    except json.JSONDecodeError:
        logger.warning("Failed to parse Gemini JSON, using safe default")
        return dict(_SAFE_DIAGNOSIS)


def build_prompt(incident: dict) -> str:
    """Construct the Gemini prompt for a given incident.

    Args:
        incident: Incident document dictionary from Firestore.

    Returns:
        A formatted prompt string.
    """
    raw_event = incident.get("raw_event", {})
    return (
        "Analyze this incident from an AI agent fleet and provide a "
        "root-cause diagnosis.\n\n"
        f"Incident type: {incident.get('type')}\n"
        f"Agent: {incident.get('agent')}\n"
        f"Raw event:\n{json.dumps(raw_event, indent=2)}\n\n"
        "Respond with STRICT JSON only (no markdown, no explanation outside the JSON):\n"
        '{\n'
        '  "root_cause": "one clear sentence",\n'
        '  "severity": "low | medium | high",\n'
        '  "confidence": <float 0-1>,\n'
        '  "recommended_action": "retry | quarantine | escalate"\n'
        "}"
    )


def diagnose_incident(incident_id: str) -> dict:
    """Diagnose a single incident and persist the result to Firestore.

    Args:
        incident_id: Firestore document ID of the incident to diagnose.

    Returns:
        The diagnosis dictionary written to Firestore.

    Raises:
        ValueError: If the incident document does not exist.
    """
    doc_ref = _get_db().collection(COLLECTION).document(incident_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise ValueError(f"Incident {incident_id} not found")

    incident = doc.to_dict()
    prompt = build_prompt(incident)

    try:
        raw_response = ask_gemini(prompt)
        diagnosis = parse_diagnosis(raw_response)
    except Exception as exc:
        logger.error("Gemini call failed for %s: %s", incident_id, exc)
        diagnosis = dict(_SAFE_DIAGNOSIS)

    doc_ref.update({
        "diagnosis": diagnosis,
        "status": "diagnosed",
        "diagnosed_at": firestore.SERVER_TIMESTAMP,
    })
    governance.record_audit(
        incident_id,
        actor="diagnoser",
        action="diagnosed",
        detail=f"{diagnosis.get('severity', '?')} severity — {diagnosis.get('root_cause', '?')}",
    )
    logger.info("Diagnosed %s → %s", incident_id, diagnosis.get("root_cause"))
    return diagnosis
