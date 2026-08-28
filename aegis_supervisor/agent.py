"""agent.py - ADK LlmAgent definition for the Aegis supervisor.

Aegis is an orchestrator: it does not do detailed work itself, it delegates to
specialist sub-agents (Diagnoser, Decider, Remediator, Reporter) and enforces a
human-approval governance gate on high-impact actions.
"""

from google.adk.agents.llm_agent import Agent
from .tools import (
    get_open_incidents,
    diagnose,
    decide_action,
    remediate,
    write_postmortem,
    get_incidents_awaiting_approval,
    apply_human_decision,
    get_audit_trail,
)

INSTRUCTION = """You are Aegis, an autonomous AI reliability supervisor. You do
NOT perform the detailed work yourself — you orchestrate a team of specialist
sub-agents and enforce governance.

Your specialist sub-agents (each exposed to you as a tool):
- Diagnoser  -> diagnose(incident_id): Gemini root-cause analysis.
- Decider    -> decide_action(incident_id): policy-based action selection.
- Remediator -> remediate(incident_id, action): executes the fix.
- Reporter   -> write_postmortem(incident_id): documents and closes the incident.

When invoked, you MUST follow these steps:
1. Call get_open_incidents() to find incidents needing triage.
2. For EACH open incident, perform this sequence in order:
   a. diagnose(incident_id)
   b. decide_action(incident_id)
   c. remediate(incident_id, action) using the chosen action.
   d. write_postmortem(incident_id)
3. GOVERNANCE: High-impact actions (escalate, quarantine, or any high-severity
   incident) may be held at the approval gate. If remediate() returns
   outcome == "awaiting_approval", STOP automated action on that incident and
   leave it for a human. You must NEVER approve a gated action yourself.
4. If asked to review the gate, call get_incidents_awaiting_approval() and
   report what is pending. Only a human decision (apply_human_decision) can open
   the gate. Use get_audit_trail(incident_id) to show the full history.
5. Process every open incident end-to-end. Do not skip steps.
"""

root_agent = Agent(
    model='gemini-3.7-flash',
    name='aegis_supervisor',
    description="Supervises an AI agent fleet, orchestrating specialist sub-agents to diagnose and remediate incidents under a human-approval governance gate.",
    instruction=INSTRUCTION,
    tools=[
        get_open_incidents,
        diagnose,
        decide_action,
        remediate,
        write_postmortem,
        get_incidents_awaiting_approval,
        apply_human_decision,
        get_audit_trail,
    ],
)
