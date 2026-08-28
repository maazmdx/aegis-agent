# Aegis — Hackathon Submission

> **All Things Agentic Hackathon** · Google ADK + Gemini 3 + Cloud Run

**Aegis is an AI agent that supervises other AI agents.** It watches a fleet of
worker agents, detects when one fails, and runs an autonomous Gemini-powered
reasoning loop to diagnose the root cause, decide an action, remediate it, and
write a postmortem — with an optional human-in-the-loop governance gate for
high-impact actions.

- **Live dashboard:** _add your Cloud Run dashboard URL_
- **Demo video:** _add your 2-3 min YouTube/Loom link_
- **Repo:** https://github.com/maazmdx/aegis-agent

---

## Inspiration

Teams are shipping fleets of AI agents into production, but **nobody supervises
the supervisors**. When an agent silently fails, burns through its token budget,
leaks PII, or returns low-confidence garbage, it usually goes unnoticed until it
becomes expensive or dangerous. Site Reliability Engineering solved this for
services with on-call humans and runbooks. We asked: *what if the on-call
responder was itself an agent?*

## What it does

Aegis is a **supervisor agent** built on Google ADK. It:

1. **Detects** — ingests events from a fleet of worker agents over Pub/Sub and
   classifies incidents (`tool_failure`, `budget_exceeded`, `pii_leak`,
   `low_confidence`).
2. **Diagnoses** — asks Gemini for a structured root-cause analysis (cause,
   severity, confidence, recommended action).
3. **Decides** — applies a deterministic policy (PII → quarantine, high
   severity → escalate, otherwise follow the model's recommendation).
4. **Governs** — routes high-impact actions through a human approval gate
   (toggleable) and records every step to an append-only audit trail.
5. **Remediates** — retries, quarantines, or escalates automatically.
6. **Reports** — writes a Markdown postmortem, optionally posts to Slack, and
   flips the live dashboard from red back to green.

## How we built it

- **Reasoning agent:** Google ADK `LlmAgent` whose tools (`get_open_incidents`,
  `diagnose`, `decide_action`, `remediate`, `write_postmortem`, plus governance
  tools) each delegate to a specialist sub-module. The docstrings and type hints
  are written *for the model* so it knows when to call each tool.
- **LLM:** Gemini (`gemini-3.7-flash`) via `google-genai`, behind a single
  `ask_gemini()` function with Tenacity retries and a safe-default fallback so a
  bad response never crashes the loop.
- **Event bus:** Cloud Pub/Sub (`fleet-events`) with a dead-letter topic.
- **State store:** Cloud Firestore (Native) — `incidents` and `quarantine`
  collections; each incident document carries its full lifecycle and audit log.
- **Services:** Cloud Run for the detector (Pub/Sub push endpoint), the Flask +
  Chart.js dashboard, and the ADK supervisor.
- **Governance:** an `AEGIS_AUTO_APPROVE` switch that turns the demo from fully
  autonomous into human-in-the-loop with exactly one required approval per
  gated action.

## Architecture

```
Worker agents ──emit──▶ Pub/Sub (fleet-events) ──push──▶ Detector (Cloud Run)
                                                              │ classify + persist
                                                              ▼
                            Aegis Supervisor (ADK) ◀────▶ Firestore (incidents)
                              │ diagnose (Gemini)            │
                              │ decide (policy + gate)       ▼
                              │ remediate / postmortem   Dashboard (Cloud Run)
```

## Challenges we ran into

- **Keeping the loop robust.** LLMs return messy JSON; we strip code fences,
  validate keys, and fall back to a safe diagnosis so one bad call can't halt
  the pipeline.
- **Autonomy vs. safety.** A fully autonomous remediator is a great demo but
  scary in reality, so we added the governance gate and audit trail to make the
  "one human decision" boundary explicit.
- **Testability.** Cloud clients were being constructed at import time, which
  made unit tests require live credentials. We refactored to lazy, cached
  client accessors so the pure logic (classification, policy, metrics) is fast
  and testable in CI.

## Accomplishments we're proud of

- A genuinely **end-to-end autonomous loop** from raw event to postmortem.
- A **governance gate + append-only audit trail** that makes the agent safe to
  actually trust.
- A **live dashboard** that visibly flips red → green as Aegis works.
- **Green CI** with unit tests covering classification, decision policy,
  governance rules, and dashboard metrics.

## What we learned

- Designing tools *for an LLM* is a documentation exercise as much as a coding
  one — precise docstrings drive good tool selection.
- Deterministic policy + LLM judgment is a powerful combination: the model
  explains, the policy decides, the human approves the risky bits.

## What's next

- Real integrations (LangSmith / OpenTelemetry traces as event sources).
- Learned policies that adapt thresholds from historical incidents.
- Multi-tenant fleets and richer escalation routing (PagerDuty, Slack actions).

## Try it locally

```bash
git clone https://github.com/maazmdx/aegis-agent.git
cd aegis-agent
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GEMINI_API_KEY + PROJECT_ID, then: source .env

python scripts/seed_demo.py          # seed a realistic dataset
python detector.py                   # http://localhost:8080
PORT=8081 python dashboard/main.py   # http://localhost:8081
python trigger.py pii                 # inject a failure
adk run aegis_supervisor             # then: "Triage all open incidents"

pytest -v                            # run the tests
```

## Team

- _add team member names / roles here_

## License

MIT — see [LICENSE](LICENSE).
