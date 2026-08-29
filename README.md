# 🛡️ Aegis — An AI Agent That Supervises AI Agents

> Autonomous supervision for fleets of AI agents. Aegis watches other agents, detects failures, and uses a Gemini-powered [Google ADK](https://google.github.io/adk-docs/) agent to **diagnose → decide → remediate → document** — autonomously, with an optional human-in-the-loop approval gate.

Built for the **All Things Agentic Hackathon** · **Google ADK + Gemini 3.5 Flash-Lite (Vertex AI) + Cloud Run**.

🔗 **Live demo:** <https://aegis-dashboard-519547923286.us-east1.run.app>

---

## The problem

Teams now run fleets of AI agents in production, but nobody supervises them. When an agent silently fails, burns through its token budget, leaks PII, or returns low-confidence garbage, it usually goes unnoticed until it becomes expensive or dangerous.

## What Aegis does

Aegis is a **supervisor agent**. It ingests events from a fleet of worker agents, classifies incidents, and hands each open incident to an autonomous ADK reasoning loop that:

1. **Diagnoses** the root cause with Gemini (via Vertex AI)
2. **Decides** an action against a policy (retry, escalate, quarantine, or follow the model's recommendation)
3. **Governs** the action through an optional human-in-the-loop gate (`AEGIS_AUTO_APPROVE`)
4. **Remediates** automatically and updates a live dashboard
5. **Writes a postmortem** and an immutable `audit_log` for every incident
6. **Answers questions** through a reporting chatbot (`/api/chat`) that reads real-time incident data

All of this is driven by the agent's reasoning loop — not by hard-coded scripts.

---

## Architecture

### System architecture

```mermaid
flowchart LR
    subgraph FLEET["Monitored Agent Fleet"]
        A1["invoice-agent"]
        A2["support-agent"]
        A3["research-agent"]
    end

    A1 --> PS(["Pub/Sub · fleet-events"])
    A2 --> PS
    A3 --> PS
    PS -->|push| DET["Detector<br/>Cloud Run · Flask"]
    PS -. dead-letter .-> DLQ(["Pub/Sub · fleet-events-dead"])
    DET -->|classify + persist| FS[("Firestore<br/>incidents · quarantine")]

    subgraph SUP["Aegis Supervisor · Google ADK LlmAgent"]
        ROOT["root_agent<br/>Gemini 3.5 Flash-Lite"]
    end

    FS -->|open incidents| ROOT
    ROOT -->|diagnose| GEM["Gemini 3.5 Flash-Lite<br/>via Vertex AI"]
    ROOT -->|decide + govern| GOV{"Governance gate<br/>AEGIS_AUTO_APPROVE"}
    ROOT -->|remediate + postmortem| FS
    FS --> DASH["Fleet Dashboard + Chatbot<br/>Cloud Run · Flask · Chart.js"]
    DASH -->|/api/chat| GEM
​
Flow: worker agents emit events → Pub/Sub → the detector (a Cloud Run service) classifies each event and stores an incident in Firestore → the Aegis supervisor (an ADK LlmAgent with function tools) triages every open incident, calling Gemini on Vertex AI to diagnose and a policy/governance layer to decide → the dashboard shows the fleet flip red and back to green in real time, and a reporting chatbot lets admins query incident metrics in natural language.
Agent & tools
flowchart LR
    ROOT["root_agent · LlmAgent<br/>aegis_supervisor/agent.py"]
    subgraph TOOLS["ADK tools · aegis_supervisor/tools.py"]
        t1["get_open_incidents()"]
        t2["diagnose()"]
        t3["decide_action()"]
        t4["remediate()"]
        t5["write_postmortem()"]
    end
    ROOT --> t1
    ROOT --> t2
    ROOT --> t3
    ROOT --> t4
    ROOT --> t5
    t1 --> FS[("Firestore")]
    t2 --> M2["diagnoser.py<br/>Gemini root-cause"]
    t3 --> M3["decider.py<br/>policy engine"]
    t3 --> M6["governance.py<br/>approval gate"]
    t4 --> M4["remediator.py<br/>actions"]
    t5 --> M5["reporter.py<br/>postmortem"]
    M4 --> FS
    M5 --> FS
ADK tools · aegis_supervisor/tools.py

get_open_incidents()

diagnose()

decide_action()

remediate()

write_postmortem()

root_agent · LlmAgent
aegis_supervisor/agent.py

Firestore

diagnoser.py
Gemini root-cause

decider.py
policy engine

governance.py
approval gate

remediator.py
actions

reporter.py
postmortem

​
Incident lifecycle
stateDiagram-v2
    [*] --> open: detector classifies event
    open --> diagnosed: Gemini root-cause (diagnoser)
    diagnosed --> decided: policy engine (decider)
    decided --> resolved: remediate — retry / auto-fix
    decided --> escalated: high severity
    decided --> quarantined: pii_leak
    resolved --> [*]
    escalated --> [*]
    quarantined --> [*]
open

diagnosed

decided

resolved

escalated

quarantined

detector classifies event

Gemini root-cause (diagnoser)

policy engine (decider)

remediate — retry / auto-fix

high severity

pii_leak

​
Supervisor reasoning loop
sequenceDiagram
    autonumber
    participant D as Detector
    participant F as Firestore
    participant S as Aegis Supervisor
    participant G as Gemini · Vertex AI
    participant V as Governance
    participant U as Dashboard
    D->>F: save incident (status = open)
    S->>F: get_open_incidents()
    F-->>S: [incident, ...]
    loop for each open incident
        S->>G: diagnose(incident)
        G-->>S: root cause + severity + action
        S->>S: decide_action() — policy
        S->>V: check approval (AEGIS_AUTO_APPROVE)
        V-->>S: approved / hold
        S->>F: remediate() — update status
        S->>F: write_postmortem() + audit_log
    end
    F-->>U: live fleet status (red → green)
    U->>G: /api/chat incident report
    G-->>U: natural-language summary
Dashboard
Governance
Gemini · Vertex AI
Aegis Supervisor
Firestore
Detector
Dashboard
Governance
Gemini · Vertex AI
Aegis Supervisor
Firestore
Detector
loop
[for each open incident]
save incident (status = open)
1
get_open_incidents()
2
[incident, ...]
3
diagnose(incident)
4
root cause + severity + action
5
decide_action() — policy
6
check approval (AEGIS_AUTO_APPROVE)
7
approved / hold
8
remediate() — update status
9
write_postmortem() + audit_log
10
live fleet status (red → green)
11
/api/chat incident report
12
natural-language summary
13
​
Cloud deployment topology
flowchart TB
    subgraph GCP["Google Cloud · project aegis-hackathon-506413"]
        subgraph CR["Cloud Run · us-east1"]
            S1["aegis-supervisor<br/>ADK + Web UI"]
            S2["aegis-detector"]
            S3["aegis-dashboard"]
        end
        PS1(["Pub/Sub · fleet-events"])
        PS2(["Pub/Sub · fleet-events-dead"])
        FS[("Firestore Native · us-central1<br/>incidents · quarantine")]
        VX["Vertex AI<br/>Gemini 3.5 Flash-Lite"]
        SM["Secret Manager<br/>gemini-api-key (legacy)"]
    end
    PS1 --> S2
    S2 -.-> PS2
    S2 --> FS
    S1 --> FS
    S3 --> FS
    S1 --> VX
    S3 --> VX
    SA["Compute Service Account (ADC)<br/>aiplatform.user · datastore.user<br/>pubsub.publisher · secretAccessor"] -.-> CR
Google Cloud · project aegis-hackathon-506413

Cloud Run · us-east1

aegis-supervisor
ADK + Web UI

aegis-detector

aegis-dashboard

Pub/Sub · fleet-events

Pub/Sub · fleet-events-dead

Firestore Native · us-central1
incidents · quarantine

Vertex AI
Gemini 3.5 Flash-Lite

Secret Manager
gemini-api-key (legacy)

Compute Service Account (ADC)
aiplatform.user · datastore.user
pubsub.publisher · secretAccessor

​
Tech stack
Layer
Technology
Reasoning agent
Google ADK (LlmAgent  • function tools)
LLM
Gemini (gemini-3.5-flash-lite) via google-genai on Vertex AI
Auth
Application Default Credentials (Cloud Run service account) — no API key in production
Event bus
Cloud Pub/Sub (with dead-letter topic)
State store
Cloud Firestore (Native mode)
Services
Cloud Run — supervisor, detector, dashboard
Web
Flask, Material Design 3 UI, Chart.js
Governance
Optional human-in-the-loop approval gate (AEGIS_AUTO_APPROVE)
Reliability
Tenacity retries, Pub/Sub dead-letter topic
CI
GitHub Actions (.github/workflows/ci.yml) running pytest
Repository layout
aegis-agent/
├─ aegis_supervisor/          # Google ADK agent package (deployed as aegis-supervisor)
│  ├─ agent.py                # root_agent (LlmAgent, Gemini 3.5 Flash-Lite via Vertex AI)
│  ├─ tools.py                # ADK tools: get_open_incidents, diagnose, decide_action, remediate, write_postmortem
│  ├─ requirements.txt
│  └─ __init__.py
├─ dashboard/                 # Flask fleet dashboard + reporting chatbot (Material 3 UI)
│  ├─ main.py                 # routes: /, /incident/<id>, /api/trigger, /api/chat, /healthz
│  ├─ templates/index.html
│  ├─ Procfile
│  └─ requirements.txt
├─ scripts/
│  ├─ deploy_supervisor.sh    # builds + deploys the ADK supervisor to Cloud Run
│  ├─ seed_demo.py            # seeds a realistic demo dataset
│  └─ reset_data.py           # clears Firestore incident data
├─ tests/                     # pytest unit tests
│  ├─ test_classify.py        # detector classification
│  ├─ test_decision.py        # policy decisions
│  ├─ test_governance.py      # approval gate
│  └─ test_mttd.py            # mean-time-to-diagnose metric
├─ detector.py                # classify events + persist incidents to Firestore
├─ diagnoser.py               # Gemini root-cause helper (Vertex AI)
├─ decider.py                 # policy-based decision engine
├─ remediator.py              # remediation actions
├─ reporter.py                # postmortem writer
├─ governance.py              # human-in-the-loop approval gate
├─ fleet.py                   # simulates a healthy agent fleet
├─ trigger.py                 # injects a failure (tool | budget | pii)
├─ Dockerfile
├─ requirements.txt
├─ pytest.ini
├─ conftest.py
├─ .github/workflows/ci.yml
├─ .env.example
└─ README.md
​
Getting started
Prerequisites
Python 3.12+
A Google Cloud project with Firestore (Native mode), Pub/Sub, and the Vertex AI API enabled
gcloud CLI authenticated to that project
Setup
git clone https://github.com/maazmdx/aegis-agent.git
cd aegis-agent
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
​
Configure environment
Aegis uses Vertex AI with Application Default Credentials, so no API key is required. For local development, authenticate with ADC:
gcloud auth application-default login
​
Then create a .env (never commit it — see .env.example):
export PROJECT_ID="your-gcp-project"
export GOOGLE_CLOUD_LOCATION="global"     # Vertex AI location for Gemini
export MODEL="gemini-3.5-flash-lite"
export AEGIS_AUTO_APPROVE="true"          # set "false" to require human approval
​
Under the hood the client is created with
genai.Client(vertexai=True, project=PROJECT_ID, location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global")),
which authenticates through the service account (in Cloud Run) or your ADC (locally). No GEMINI_API_KEY is needed.
Run locally
# 1. seed a clean, realistic demo dataset
python scripts/seed_demo.py

# 2. start the detector
python detector.py            # http://localhost:8080

# 3. start the dashboard + chatbot
PORT=8081 python dashboard/main.py    # http://localhost:8081

# 4. inject a failure; the supervisor triages it in the background
#    Option A: click "Simulate Incident" on the dashboard
#    Option B: curl the trigger endpoint
curl -X POST http://localhost:8081/api/trigger
​
Test
pytest -v
​
Incident model
Lifecycle: open → diagnosed → decided → resolved / escalated / quarantined
Signal
Classified as
Decision
tool error
tool_failure
retry if safe
cost > 1.0 or tokens > 10000
budget_exceeded
escalate if high severity
PII detected
pii_leak
quarantine
confidence < 0.5
low_confidence
follow the model's recommended action
otherwise
healthy
no action
Every incident stores an immutable audit_log — an ordered list of {action, actor, at, detail} entries (e.g. diagnosed/diagnoser, decided/decider, remediated/remediator, postmortem/reporter) — so every autonomous action is fully traceable.
Deployment (Cloud Run · us-east1)
Gemini runs on Vertex AI, so deployments authenticate with the Cloud Run service account (no secret key required).
export PROJECT_ID="aegis-hackathon-506413"
export REGION="us-east1"

# Detector
gcloud run deploy aegis-detector --source . --region $REGION --allow-unauthenticated \
  --set-env-vars PROJECT_ID=$PROJECT_ID,LOCATION=$REGION

# Dashboard + chatbot
gcloud run deploy aegis-dashboard --source=dashboard/ --region $REGION --allow-unauthenticated \
  --set-env-vars PROJECT_ID=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=global

# Supervisor (ADK) — builds and deploys with the ADK web UI
bash scripts/deploy_supervisor.sh
# or manually:
adk deploy cloud_run --project=$PROJECT_ID --region=$REGION \
  --service_name=aegis-supervisor --with_ui aegis_supervisor
​
Grant the Cloud Run service account the roles/aiplatform.user, roles/datastore.user, and roles/pubsub.publisher roles, and wire the detector as a Pub/Sub push subscription (fleet-events → detector) with a dead-letter topic (fleet-events-dead) for reliability.
Continuous integration
.github/workflows/ci.yml runs the pytest suite on every push, covering event classification, policy decisions, the governance gate, and the mean-time-to-diagnose metric.
License
MIT — see LICENSE.
