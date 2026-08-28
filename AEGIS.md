# AEGIS — AI Reliability Supervisor

> "A babysitter for other AI agents."
> Watches a fleet of worker agents, catches failures, and uses Gemini to
> explain the root cause of each failure — then diagnoses, decides, remediates,
> and documents each incident autonomously (with an optional human gate).

## Environment
- OS: Linux Mint (Ubuntu/Debian base)
- Python: 3.12+ inside a venv at ./venv
- Cloud: Google Cloud project `aegis-hackathon-506413`, region `us-central1`
- Run `python` and `pip` (venv is active), never `python3`

## Environment variables / config
Configured via a local `.env` (see `.env.example`, then `source .env`):
- `PROJECT_ID` → `aegis-hackathon-506413`
- `LOCATION` → `us-central1`
- `GEMINI_API_KEY` → AI Studio key used by the diagnoser
- `GOOGLE_API_KEY` → read by the ADK; keep in sync with `GEMINI_API_KEY`
- `MODEL` → Gemini model id (default `gemini-3.7-flash`)
- `AEGIS_AUTO_APPROVE` → `"true"` (default) auto-approves gated actions for the
  fully-autonomous demo; `"false"` enforces the human governance gate
- `SLACK_WEBHOOK_URL` → optional, for reporter.py Slack posting

Each module reads `PROJECT_ID` from the environment via
`os.environ.get("PROJECT_ID")`. Set it in `.env` rather than hardcoding it.

## The AI "brain" — two swappable paths
1. **FREE path** (default for local dev): Google AI Studio key
   - lib: `google-genai` (`from google import genai`)
   - model: `gemini-3.7-flash`
   - key from env var `GEMINI_API_KEY`
2. **CLOUD path** (for final deploy): Vertex AI
   - lib: `google-cloud-aiplatform` (vertexai)
   - model: `gemini-3.7-flash`

Keep the diagnoser's LLM call behind a single function `ask_gemini(prompt)`
so you can swap paths by changing only that function. Cloud clients are created
lazily (cached getters) so the pure logic imports without credentials in tests.

## File layout

```
aegis-agent/
├── AEGIS.md               # this design/spec file
├── README.md              # project overview
├── SUBMISSION.md          # hackathon writeup
├── requirements.txt       # top-level deps
├── Dockerfile             # detector container image
├── .env.example           # environment variable template
├── pytest.ini
├── fleet.py               # fake worker agents that emit events to Pub/Sub
├── trigger.py             # CLI to fire specific failures on demand
├── detector.py            # reads events, classifies, writes incidents; auto-pipeline
├── diagnoser.py           # reads incidents, asks Gemini, writes diagnosis
├── decider.py             # reads diagnosed incidents, picks an action
├── remediator.py          # executes the action (retry / quarantine / escalate)
├── reporter.py            # writes a postmortem + posts to Slack
├── governance.py          # human approval gate + append-only audit trail
├── aegis_supervisor/      # Google ADK agent package
│   ├── agent.py           # root_agent (LlmAgent)
│   ├── tools.py           # ADK tools wrapping the sub-modules
│   └── __init__.py
├── dashboard/
│   ├── main.py            # Flask app: live red→green fleet view
│   ├── Procfile           # gunicorn entrypoint for Cloud Run buildpacks
│   └── requirements.txt
├── hello_agent/           # tiny Flask "alive" agent for Cloud Run
│   ├── main.py
│   └── requirements.txt
├── scripts/               # reset_data.py, seed_demo.py
├── tests/                 # pytest unit tests
└── .github/workflows/     # CI (pytest on push / PR)
```

## Google Cloud resources
- Pub/Sub topic: `fleet-events`
- Pub/Sub subscription: `fleet-events-sub` (push → detector)
- Pub/Sub dead-letter topic: `fleet-events-dead`
- Firestore: Native mode, collections `incidents` and `quarantine`
- Cloud Run service (detector): `aegis-detector`
- Cloud Run service (dashboard): `aegis-dashboard`
- Cloud Run service (supervisor): `aegis-supervisor`
- Cloud Run service (hello, optional): `aegis-hello`
- Secret Manager: `gemini-api-key`

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
  "status": "open | diagnosed | decided | awaiting_approval | resolved | escalated",
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
    "reason": "one sentence",
    "requires_approval": false
  },
  "approval": {
    "state": "pending | approved | rejected",
    "requested_action": "string",
    "approver": "string",
    "note": "string"
  },
  "remediation": {
    "outcome": "success | failed | skipped | held | awaiting_approval",
    "detail": "string",
    "at": "timestamp"
  },
  "postmortem": {
    "summary": "markdown string",
    "slack_ts": "string | null"
  },
  "audit_log": [
    { "actor": "string", "action": "string", "detail": "string", "at": 0.0 }
  ]
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
Evaluated in priority order:
1. `type == "pii_leak"` → quarantine (always)
2. `severity == "high"` → escalate
3. `recommended_action == "retry"` AND `severity != "high"` → retry
4. otherwise → follow `recommended_action`

Write `incident.decision` (including `requires_approval`) and set
`status = "decided"`.

## Governance gate (governance.py)
- High-impact actions (`escalate`, `quarantine`) or any `high` severity incident
  are "gated".
- When `AEGIS_AUTO_APPROVE=false`, a gated action is held at
  `status = "awaiting_approval"` until exactly one human calls
  `apply_human_decision(...)` to approve or reject it.
- Every state transition is appended to the incident's `audit_log`
  (append-only via Firestore `ArrayUnion`).

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
                           → awaiting_approval → resolved / escalated
                           → escalated
```

## Full-loop auto-pipeline (detector.py)
After saving an incident, detector calls each stage in sequence:
`diagnoser → decider → remediator → reporter`
Each wrapped in its own `try/except` so one failure can't halt the chain. The
ADK supervisor exposes the same stages as tools so a human (or the agent) can
re-triage `open` / `awaiting_approval` incidents interactively.

## Coding conventions
- Small, readable, single-file scripts. No frameworks beyond what's listed.
- Every file runnable as `python <file>.py`.
- Print human-friendly progress lines (emoji ok).
- Handle Ctrl+C cleanly on long-running scripts.
- Never hardcode secrets; read from env vars.
