#!/usr/bin/env python
"""trigger.py - CLI to fire specific failures on demand."""

import logging
import sys

from fleet import fail_tool_call, fail_budget, fail_pii

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger(__name__)

FAILURES = {
    "tool":   ("tool_failure",    fail_tool_call),
    "budget": ("budget_exceeded", fail_budget),
    "pii":    ("pii_leak",        fail_pii),
}

def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in FAILURES:
        print("Usage: python trigger.py <tool|budget|pii>\n")
        for name, (desc, _) in FAILURES.items():
            print(f"  {name:10s} -> {desc}")
        sys.exit(1)

    which = sys.argv[1]
    label, fn = FAILURES[which]
    logger.info("Firing failure: %s", label)
    fn()
    logger.info("Triggered: %s", which)

if __name__ == "__main__":
    main()
