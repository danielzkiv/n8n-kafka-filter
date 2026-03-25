#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Create Cloud Monitoring alert policies for n8n-kafka-filter.
#
# Usage:
#   export PROJECT_ID=your-gcp-project-id
#   export ALERT_EMAIL=your@email.com
#   bash scripts/setup_monitoring.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${ALERT_EMAIL:?Set ALERT_EMAIL}"

SERVICE_NAME="kafka-n8n-forwarder"

gcloud config set project "${PROJECT_ID}"

# ── 1. Create notification channel (email) ───────────────────────────────────
echo "Creating email notification channel for ${ALERT_EMAIL}..."
CHANNEL_ID=$(gcloud alpha monitoring channels create \
  --display-name="n8n-kafka-filter alerts" \
  --type=email \
  --channel-labels="email_address=${ALERT_EMAIL}" \
  --format="value(name)" \
  --quiet 2>/dev/null | tail -1)
echo "  Channel: ${CHANNEL_ID}"

# ── 2. Alert: Cloud Run instance count drops to 0 ────────────────────────────
# The consumer must always have at least 1 instance running.
echo "Creating alert: instance count = 0..."
gcloud alpha monitoring policies create \
  --display-name="[${SERVICE_NAME}] No instances running" \
  --condition-filter="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SERVICE_NAME}\" AND metric.type=\"run.googleapis.com/container/instance_count\"" \
  --condition-threshold-value=1 \
  --condition-threshold-comparison=COMPARISON_LT \
  --condition-duration=120s \
  --condition-display-name="Instance count below 1 for 2 minutes" \
  --notification-channels="${CHANNEL_ID}" \
  --severity=CRITICAL \
  --quiet 2>/dev/null || echo "  (alert may already exist)"

# ── 3. Alert: High webhook error rate in logs ────────────────────────────────
echo "Creating log-based metric for webhook errors..."
gcloud logging metrics create webhook_errors \
  --description="Count of webhook delivery failures" \
  --log-filter='resource.type="cloud_run_revision" resource.labels.service_name="${SERVICE_NAME}" jsonPayload.message="Webhook delivery exhausted all retries"' \
  --quiet 2>/dev/null || echo "  (metric may already exist)"

echo "Creating alert: webhook errors > 5 in 5 minutes..."
gcloud alpha monitoring policies create \
  --display-name="[${SERVICE_NAME}] High webhook error rate" \
  --condition-filter="resource.type=\"cloud_run_revision\" AND metric.type=\"logging.googleapis.com/user/webhook_errors\"" \
  --condition-threshold-value=5 \
  --condition-threshold-comparison=COMPARISON_GT \
  --condition-duration=300s \
  --condition-display-name="More than 5 webhook errors in 5 minutes" \
  --notification-channels="${CHANNEL_ID}" \
  --severity=WARNING \
  --quiet 2>/dev/null || echo "  (alert may already exist)"

# ── 4. Alert: Container restart count (consumer crash loop) ──────────────────
echo "Creating alert: container restart loop..."
gcloud alpha monitoring policies create \
  --display-name="[${SERVICE_NAME}] Container restarting" \
  --condition-filter="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SERVICE_NAME}\" AND metric.type=\"run.googleapis.com/container/restart_count\"" \
  --condition-threshold-value=3 \
  --condition-threshold-comparison=COMPARISON_GT \
  --condition-duration=300s \
  --condition-display-name="More than 3 restarts in 5 minutes" \
  --notification-channels="${CHANNEL_ID}" \
  --severity=CRITICAL \
  --quiet 2>/dev/null || echo "  (alert may already exist)"

echo ""
echo "✓ Monitoring setup complete"
echo "  View dashboards: https://console.cloud.google.com/monitoring?project=${PROJECT_ID}"
echo "  View alerts    : https://console.cloud.google.com/monitoring/alerting?project=${PROJECT_ID}"
