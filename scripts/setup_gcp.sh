#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# GCP infrastructure setup for n8n-kafka-filter
#
# Run once per GCP project. Idempotent — safe to re-run.
#
# Usage:
#   export PROJECT_ID=your-gcp-project-id
#   export REGION=europe-west1          # or us-central1, etc.
#   bash scripts/setup_gcp.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID environment variable}"
: "${REGION:=us-central1}"

SERVICE_NAME="kafka-n8n-forwarder"
SA_NAME="${SERVICE_NAME}-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "▶ Project : ${PROJECT_ID}"
echo "▶ Region  : ${REGION}"
echo "▶ Image   : ${IMAGE}"
echo ""

# ── 1. Set active project ─────────────────────────────────────────────────────
gcloud config set project "${PROJECT_ID}"

# ── 2. Enable required APIs ───────────────────────────────────────────────────
echo "Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  containerregistry.googleapis.com \
  --quiet

# ── 3. Create service account ─────────────────────────────────────────────────
echo "Creating service account ${SA_NAME}..."
gcloud iam service-accounts create "${SA_NAME}" \
  --display-name="n8n Kafka Forwarder" \
  --quiet 2>/dev/null || echo "  (already exists, skipping)"

# ── 4. Grant SA permission to read secrets ────────────────────────────────────
echo "Granting Secret Manager access to ${SA_EMAIL}..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --quiet

# ── 5. Create the pipelines-config secret (placeholder) ──────────────────────
echo "Creating pipelines-config secret..."
if ! gcloud secrets describe pipelines-config --project="${PROJECT_ID}" &>/dev/null; then
  echo "[]" | gcloud secrets create pipelines-config \
    --data-file=- \
    --replication-policy=automatic \
    --quiet
  echo "  Created. Update it with your real config:"
  echo "  gcloud secrets versions add pipelines-config --data-file=your-pipelines.json"
else
  echo "  (already exists, skipping)"
fi

# ── 6. Build and push Docker image ────────────────────────────────────────────
echo "Building and pushing Docker image..."
gcloud builds submit \
  --tag "${IMAGE}:latest" \
  --quiet

# ── 7. Update service.yaml with real values and deploy ───────────────────────
echo "Deploying to Cloud Run..."
sed \
  -e "s|YOUR_PROJECT_ID|${PROJECT_ID}|g" \
  -e "s|YOUR_SA@YOUR_PROJECT|${SA_EMAIL}|g" \
  service.yaml | \
gcloud run services replace - \
  --region="${REGION}" \
  --quiet

# ── 8. Allow unauthenticated health checks (internal only) ───────────────────
# The service is not publicly callable — Cloud Run still needs to reach /health
gcloud run services add-iam-policy-binding "${SERVICE_NAME}" \
  --region="${REGION}" \
  --member="allUsers" \
  --role="roles/run.invoker" \
  --quiet 2>/dev/null || true

echo ""
echo "✓ Setup complete"
echo ""
echo "Next steps:"
echo "  1. Build your pipelines config (see .env.example for format)"
echo "  2. gcloud secrets versions add pipelines-config --data-file=pipelines.json"
echo "  3. gcloud run services update ${SERVICE_NAME} --region=${REGION} --update-secrets=PIPELINES_JSON=pipelines-config:latest"
echo ""
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --region="${REGION}" --format="value(status.url)" 2>/dev/null || echo "<not deployed yet>")
echo "  Service URL : ${SERVICE_URL}"
echo "  Health      : ${SERVICE_URL}/health"
echo "  Metrics     : ${SERVICE_URL}/metrics"
