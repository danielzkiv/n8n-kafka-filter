#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Connect Cloud Build to GitHub and create a trigger for the main branch.
#
# Prerequisites:
#   1. Run scripts/setup_gcp.sh first
#   2. Connect your GitHub repo to Cloud Build manually once in the console:
#      https://console.cloud.google.com/cloud-build/triggers/connect
#      (Cloud Build needs OAuth access to GitHub — can't be done via CLI)
#
# Usage:
#   export PROJECT_ID=your-gcp-project-id
#   export REGION=us-central1
#   export GITHUB_OWNER=danielzkiv
#   export GITHUB_REPO=n8n-kafka-filter
#   bash scripts/setup_cicd.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${REGION:=us-central1}"
: "${GITHUB_OWNER:?Set GITHUB_OWNER (your GitHub username)}"
: "${GITHUB_REPO:=n8n-kafka-filter}"

gcloud config set project "${PROJECT_ID}"

echo "Creating Cloud Build trigger for ${GITHUB_OWNER}/${GITHUB_REPO} → main..."

gcloud builds triggers create github \
  --name="deploy-on-push-main" \
  --repo-name="${GITHUB_REPO}" \
  --repo-owner="${GITHUB_OWNER}" \
  --branch-pattern="^main$" \
  --build-config="cloudbuild.yaml" \
  --substitutions="_REGION=${REGION}" \
  --description="Build, test, and deploy on push to main" \
  --region="${REGION}" \
  --quiet

# Grant Cloud Build SA permission to deploy to Cloud Run
CB_SA="$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')@cloudbuild.gserviceaccount.com"

echo "Granting Cloud Build SA (${CB_SA}) Cloud Run deploy permission..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${CB_SA}" \
  --role="roles/run.admin" \
  --quiet

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${CB_SA}" \
  --role="roles/iam.serviceAccountUser" \
  --quiet

echo ""
echo "✓ CI/CD trigger created"
echo "  Every push to main will: run tests → build image → deploy to Cloud Run"
echo ""
echo "  View triggers: https://console.cloud.google.com/cloud-build/triggers?project=${PROJECT_ID}"
