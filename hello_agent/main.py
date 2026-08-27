#!/usr/bin/env python
"""hello_agent/main.py — Tiny Flask 'alive' agent for Cloud Run (Part 1G)."""

import logging
import os

from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def index():
    return jsonify({"agent": "aegis-hello", "status": "alive"})


@app.route("/health")
def health():
    return jsonify({"healthy": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info("🚀 aegis-hello starting on http://localhost:%d", port)
    app.run(host="0.0.0.0", port=port)
