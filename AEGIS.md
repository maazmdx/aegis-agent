# AEGIS — AI Reliability Supervisor

> "A babysitter for other AI agents."
> Watches a fleet of worker agents, catches failures, and uses Gemini to
> explain the root cause of each failure.

## Environment
- OS: Linux Mint (Ubuntu/Debian base)
- Python: 3.11+ inside a venv at ./venv
- Cloud: Google Cloud project `aegis-hackathon-506413`, region `us-central1`
- Run `python` and `pip` (venv is active), never `python3`

## Environment variables / config
- `PROJECT_ID` → `aegis-hackathon-506413`
- `LOCATION` → `us-central1`
- `GEMINI_API_KEY` → only used in the free AI Studio path
- `SLACK_WEBHOOK_URL` → optional, for reporter.py Slack posting

Prefer reading PROJECT_ID from env with a hardcoded fallback constant
at the top of each file, clearly marked with `# TODO: set your project id`.

## The AI "brain" — two swappable paths
1. **FREE path** (default for local dev): Google AI Studio key
   - lib: `google-generativeai`
   - model: `gemini-3.6-flash`
   - key from env var `GEMINI_API_KEY`
2. **CLOUD path** (for final deploy): Vertex AI
   - lib: `google-cloud-aiplatform` (vertexai)
   - model: `gemini-3.6-flash`

Keep the diagnoser's LLM call behind a single function `ask_gemini(prompt)`
so you can swap paths by changing only that function.

## File layout

```
aegis/
├── AEGIS.md              # this file
├── venv/                 # virtual env (not committed)
├── requirements.txt      # top-level deps
├── fleet.py              # fake worker agents that emit events to Pub/Sub
├── trigger.py            # CLI to fire specific failures on demand
├── detector.py           # reads events, classifies, writes incidents to Firestore
├── diagnoser.py          # reads open incidents, asks Gemini, writes diagnosis
├── decider.py            # reads diagnosed incidents, picks an action
├── remediator.py         # executes the action (retry / quarantine / escalate)
├── reporter.py           # writes a postmortem + posts to Slack
├── test_gemini.py        # one-off Gemini connectivity test
├── dashboard/
│   ├── main.py           # Flask app: live red→green fleet view
│   └── requirements.txt
└── hello_agent/
    ├── main.py           # tiny Flask "alive" agent for Cloud Run
    └── requirements.txt
```

## Google Cloud resources
- Pub/Sub topic: `fleet-events`
- Pub/Sub subscription: `fleet-events-sub`
- Firestore: Native mode, collection `incidents`
- Cloud Run service (hello): `aegis-hello`
- Cloud Run service (dashboard): `aegis-dashboard`

## Event schema (what fleet.py emits)

Every event is JSON published to the `fleet-events` topic:

```json
{
  "event_id": "uuid",
  "agent": "invoice-agent | support-agent",
  "status": "success | error",
  "tokens": 0,
  "cost": 0.0,
  "tool_call": "string",
  "confidence": 1.0,
  "pii_leak": false,
  "timestamp": 0.0
}
```

## Incident schema (Firestore `incidents`)

```json
{
  "incident_id": "uuid",
  "agent": "string",
  "type": "tool_failure | budget_exceeded | pii_leak | low_confidence",
  "raw_event": { "...the original event..." },
  "status": "open | diagnosed | decided | remediating | resolved | escalated",
  "created_at": "server timestamp",
  "diagnosis": {
    "root_cause": "one clear sentence",
    "severity": "low | medium | high",
    "confidence": 0.0,
    "recommended_action": "retry | quarantine | escalate"
  },
  "diagnosed_at": "timestamp",
  "decision": {
    "action": "retry | quarantine | escalate",
    "reason": "one sentence"
  },
  "remediation": {
    "outcome": "success | failed | skipped",
    "detail": "string",
    "at": "timestamp"
  },
  "postmortem": {
    "summary": "markdown string",
    "slack_ts": "string | null"
  }
}
```

## Classification rules (detector.py)
- `status == "error"` → `tool_failure`
- `cost > 1.0 OR tokens > 10000` → `budget_exceeded`
- `pii_leak == true` → `pii_leak`
- `confidence < 0.5` → `low_confidence`
- otherwise → healthy (ignore)

## Diagnosis output (diagnoser.py)
Gemini must return STRICT JSON:

```json
{
  "root_cause": "one clear sentence",
  "severity": "low | medium | high",
  "confidence": 0.0,
  "recommended_action": "retry | quarantine | escalate"
}
```

Strip ``` fences before `json.loads`, fall back to safe default dict on failure.

## Decision policy (decider.py)
- `recommended_action == "retry"` AND `severity != "high"` → retry
- `type == "pii_leak"` → quarantine (always)
- `severity == "high"` → escalate
- otherwise → follow `recommended_action`

Write `incident.decision` and set `status = "decided"`.

## Remediation (remediator.py) — simulated, demo-safe
- **retry:** re-emit the original agent's task as a healthy event; outcome success
- **quarantine:** write a doc in Firestore `quarantine` collection; agent marked disabled
- **escalate:** flag for human; outcome skipped

Write `incident.remediation`, set status `"resolved"` (or `"escalated"` for escalate).

## Reporter (reporter.py)
For each resolved/escalated incident without a postmortem:
- Build a short markdown postmortem
- If `SLACK_WEBHOOK_URL` env is set, POST a compact summary to Slack
- Save under `incident.postmortem` (idempotent — never re-post)

## Dashboard (dashboard/main.py)
- Flask app; reads Firestore `incidents`
- Shows each agent as a card: GREEN if no active incidents, RED if active
- Lists recent incidents with type, severity, action, status
- Auto-refresh every 5s

## Incident lifecycle
```
open → diagnosed → decided → resolved
                           → escalated
```

## Full-loop auto-pipeline (detector.py)
After saving an incident, detector calls each stage in sequence:
`diagnoser.run() → decider.run() → remediator.run() → reporter.run()`
Each wrapped in its own `try/except` so one failure can't halt the chain.

## Coding conventions
- Small, readable, single-file scripts. No frameworks beyond what's listed.
- Every file runnable as `python <file>.py`.
- Print human-friendly progress lines (emoji ok).
- Handle Ctrl+C cleanly on long-running scripts.
- Never hardcode secrets; read from env vars.
