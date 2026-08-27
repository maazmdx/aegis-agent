#!/usr/bin/env python
"""scripts/reset_data.py - Delete all documents in incidents and quarantine collections.

Prompts for confirmation unless ``--yes`` is passed.
"""

import argparse
import logging
import sys

from google.cloud import firestore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Delete all Firestore documents from incidents and quarantine."""
    parser = argparse.ArgumentParser(
        description="Delete all documents in incidents and quarantine collections.",
    )
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if not args.yes:
        confirm = input("Are you sure you want to delete all data? (y/N) ")
        if confirm.lower() != "y":
            logger.info("Aborted.")
            sys.exit(0)

    db = firestore.Client()
    deleted_count = 0

    for coll_name in ["incidents", "quarantine"]:
        coll_ref = db.collection(coll_name)
        docs = coll_ref.stream()
        for doc in docs:
            doc.reference.delete()
            deleted_count += 1

    logger.info("Deleted %d documents.", deleted_count)


if __name__ == "__main__":
    main()
