#!/usr/bin/env bash
# Reproducible deploy for the Aegis ADK supervisor to Cloud Run.
#
# `adk deploy cloud_run <dir>` only bundles the agent package directory, so the
# root-level specialist modules (diagnoser, decider, remediator, reporter,
# governance, fleet) that tools.py imports must be copied into the package
# before deploy or the container crashes on ModuleNotFoundError. This script
# copies them in, deploys, wires the runtime env vars + secrets, and finally
# removes the copies so the repo tree stays clean.
#
# Run from the repo root, ideally in Google Cloud Shell:
#   bash scripts/deploy_supervisor.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-aegis-hackathon-506413}"
REGION="${REGION:-us-east1}"
SERVICE="aegis-supervisor"
PKG="aegis_supervisor"
MODULES=(diagnoser.py decider.py remediator.py reporter.py governance.py fleet.py)

echo "==> Copying specialist modules into ${PKG}/ ..."
for m in "${MODULES[@]}"; do
  cp "${m}" "${PKG}/${m}"
done

cleanup() {
  echo "==> Cleaning up copied modules ..."
  for m in "${MODULES[@]}"; do
    rm -f "${PKG}/${m}"
  done
}
trap cleanup EXIT

echo "==> Deploying ${SERVICE} to Cloud Run (${REGION}) with the ADK web UI ..."
adk deploy cloud_run \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --service_name="${SERVICE}" \
  --with_ui \
  "${PKG}"

echo "==> Configuring runtime env vars + secrets ..."
# NOTE: deliberately do NOT set GOOGLE_GENAI_USE_VERTEXAI. On Cloud Run the ADK
# agent uses Vertex for its own model regardless; setting the var breaks the
# diagnoser's key-based Developer-API client. GOOGLE_CLOUD_LOCATION=global is
# required because gemini-3.5-flash-lite is not served from the regional Vertex
# endpoint (us-east1) and would 404.
gcloud run services update "${SERVICE}" --region "${REGION}" \
  --update-env-vars "PROJECT_ID=${PROJECT_ID},MODEL=gemini-3.5-flash-lite,AEGIS_AUTO_APPROVE=true,GOOGLE_CLOUD_LOCATION=global" \
  --update-secrets "GEMINI_API_KEY=gemini-api-key:latest,GOOGLE_API_KEY=gemini-api-key:latest"

echo "==> Done. Service URL:"
gcloud run services describe "${SERVICE}" --region "${REGION}" --format='value(status.url)'
