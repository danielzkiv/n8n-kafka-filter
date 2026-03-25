#!/usr/bin/env bash
# =============================================================================
# VPN Setup — Step 1: GCP side
# Run this first. It creates the VPN gateway and VPC connector, then prints
# the external IP you need to give to your AWS team.
#
# After running: send GCP_EXTERNAL_IP to AWS team so they can create:
#   - Customer Gateway (pointing to GCP_EXTERNAL_IP)
#   - Site-to-Site VPN connection
#   - They will give you back: TUNNEL_1_IP, TUNNEL_1_PSK, TUNNEL_2_IP, TUNNEL_2_PSK
#
# Then run setup_vpn_step2.sh with those values.
# =============================================================================

set -euo pipefail

# ── Config — fill these in ───────────────────────────────────────────────────
PROJECT_ID="YOUR_GCP_PROJECT_ID"
REGION="us-central1"
NETWORK="default"
AWS_VPC_CIDR="10.0.0.0/16"          # CIDR of the AWS VPC where MSK lives
VPC_CONNECTOR_CIDR="10.8.0.0/28"    # Unused /28 in your GCP network for the connector
# ─────────────────────────────────────────────────────────────────────────────

echo "==> Setting project to $PROJECT_ID"
gcloud config set project "$PROJECT_ID"

echo "==> Enabling required APIs"
gcloud services enable \
  compute.googleapis.com \
  vpcaccess.googleapis.com \
  --quiet

# ── VPN Gateway ──────────────────────────────────────────────────────────────

echo "==> Creating Cloud VPN gateway"
gcloud compute vpn-gateways create aws-vpn-gateway \
  --network="$NETWORK" \
  --region="$REGION" \
  --quiet 2>/dev/null || echo "    (already exists)"

echo "==> Creating Cloud Router"
gcloud compute routers create vpn-router \
  --network="$NETWORK" \
  --region="$REGION" \
  --quiet 2>/dev/null || echo "    (already exists)"

# ── Firewall rule — allow Kafka from VPN ──────────────────────────────────────

echo "==> Adding firewall rule to allow inbound Kafka traffic from AWS VPC"
gcloud compute firewall-rules create allow-kafka-from-aws \
  --network="$NETWORK" \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:9092,tcp:9094,tcp:9096 \
  --source-ranges="$AWS_VPC_CIDR" \
  --description="Allow Kafka from AWS VPC over VPN" \
  --quiet 2>/dev/null || echo "    (already exists)"

# ── Serverless VPC Access connector ──────────────────────────────────────────
# Cloud Run needs this to route traffic through the GCP VPC (and on to AWS via VPN)

echo "==> Creating Serverless VPC Access connector"
gcloud compute networks vpc-access connectors create kafka-connector \
  --network="$NETWORK" \
  --region="$REGION" \
  --range="$VPC_CONNECTOR_CIDR" \
  --min-instances=2 \
  --max-instances=3 \
  --machine-type=e2-micro \
  --quiet 2>/dev/null || echo "    (already exists)"

# ── Print external IP ────────────────────────────────────────────────────────

echo ""
echo "==> Fetching GCP VPN gateway external IP..."
GCP_IP=$(gcloud compute vpn-gateways describe aws-vpn-gateway \
  --region="$REGION" \
  --format="value(vpnInterfaces[0].ipAddress)")

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  GCP VPN Gateway External IP: $GCP_IP"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Send this IP to your AWS team. They need to:"
echo "  1. Create a Customer Gateway pointing to $GCP_IP"
echo "  2. Create a Site-to-Site VPN connection (static routing)"
echo "     - Add this route to the VPN: $AWS_VPC_CIDR"
echo "  3. Download the VPN config and send you:"
echo "     - Tunnel 1 outside IP + pre-shared key"
echo "     - Tunnel 2 outside IP + pre-shared key"
echo ""
echo "Then run: bash scripts/setup_vpn_step2.sh"
