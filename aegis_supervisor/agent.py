"""agent.py - ADK LlmAgent definition for Aegis."""

from google.adk.agents.llm_agent import Agent
from .tools import (
    get_open_incidents,
    diagnose,
    decide_action,
    remediate,
    write_postmortem,
)

INSTRUCTION = """You are Aegis, an AI reliability supervisor. 
When invoked, you MUST follow these exact steps:
1. Call get_open_incidents() to find any incidents needing triage.
2. For EACH incident found, perform the following sequence in order:
   a. Call diagnose(incident_id) to find the root cause.
   b. Call decide_action(incident_id) to pick the remediation.
   c. Call remediate(incident_id, action) using the chosen action.
   d. Call write_postmortem(incident_id) to summarize and resolve it.
3. Process every open incident end-to-end. Do not skip any steps.
"""

root_agent = Agent(
    model='gemini-3.6-flash',
    name='aegis_supervisor',
    description="Supervises an AI agent fleet, diagnosing and remediating incidents.",
    instruction=INSTRUCTION,
    tools=[
        get_open_incidents,
        diagnose,
        decide_action,
        remediate,
        write_postmortem,
    ],
)
