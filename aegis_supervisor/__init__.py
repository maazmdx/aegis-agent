"""aegis_supervisor - ADK-based AI reliability supervisor agent package.

This package exposes the ``root_agent`` used by the Google ADK runner.
The agent orchestrates incident triage by calling tools that delegate
to the core Aegis modules (diagnoser, decider, remediator, reporter).
"""

from .agent import root_agent  # noqa: F401 — re-export for `adk run`

__all__ = ["root_agent"]
