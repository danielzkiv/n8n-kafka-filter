#!/usr/bin/env bash
# =============================================================================
# VPN Setup — Step 2: Create tunnels + connect Cloud Run
# Run this after AWS team gives you the tunnel IPs and pre-shared keys.
# =============================================================================

set -euo pipefail

# ── Config — fill these in ───────────────────────────────────────────────────
PROJECT_ID="YOUR_GCP_PROJECT_ID"
REGION="us-central1"
AWS_VPC_CIDR="10.0.0.0/16"          # Same as in step 1
CLOUD_RUN_SERVICE="kafka-n8n-forwarder"

# From AWS VPN config (given to you by AWS team):
AWS_TUNNEL_1_IP="YOUR_AWS_TUNNEL_1_OUTSIDE_IP"
AWS_TUNNEL_1_PSK="YOUR_AWS_TUNNEL_1_PRE_SHARED_KEY"
AWS_TUNNEL_2_IP="YOUR_AWS_TUNNEL_2_OUTSIDE_IP"
AWS_TUNNEL_2_PSK="YOUR_AWS_TUNNEL_2_PRE_SHARED_KEY"
# ─────────────────────────────────────────────────────────────────────────────

echo "==> Setting project to $PROJECT_ID"
gcloud config set project "$PROJECT_ID"

# ── VPN Tunnels ───────────────────────────────────────────────────────────────

echo "==> Creating VPN tunnel 1"
gcloud compute vpn-tunnels create aws-tunnel-1 \
  --vpn-gateway=aws-vpn-gateway \
  --vpn-gateway-interface=0 \
  --peer-address="$AWS_TUNNEL_1_IP" \
  --shared-secret="$AWS_TUNNEL_1_PSK" \
  --ike-version=2 \
  --region="$REGION" \
  --router=vpn-router \
  --quiet 2>/dev/null || echo "    (already exists)"

echo "==> Creating VPN tunnel 2"
gcloud compute vpn-tunnels create aws-tunnel-2 \
  --vpn-gateway=aws-vpn-gateway \
  --vpn-gateway-interface=1 \
  --peer-address="$AWS_TUNNEL_2_IP" \
  --shared-secret="$AWS_TUNNEL_2_PSK" \
  --ike-version=2 \
  --region="$REGION" \
  --router=vpn-router \
  --quiet 2>/dev/null || echo "    (already exists)"

# ── Router interfaces + BGP peers ────────────────────────────────────────────

echo "==> Adding router interfaces for tunnels"
gcloud compute routers add-interface vpn-router \
  --interface-name=if-tunnel-1 \
  --vpn-tunnel=aws-tunnel-1 \
  --region="$REGION" \
  --quiet 2>/dev/null || echo "    (already exists)"

gcloud compute routers add-interface vpn-router \
  --interface-name=if-tunnel-2 \
  --vpn-tunnel=aws-tunnel-2 \
  --region="$REGION" \
  --quiet 2>/dev/null || echo "    (already exists)"

# ── Static route to AWS VPC ───────────────────────────────────────────────────

echo "==> Adding static route to AWS VPC ($AWS_VPC_CIDR)"
gcloud compute routes create route-to-aws \
  --network=default \
  --destination-range="$AWS_VPC_CIDR" \
  --next-hop-vpn-tunnel=aws-tunnel-1 \
  --next-hop-vpn-tunnel-region="$REGION" \
  --priority=100 \
  --quiet 2>/dev/null || echo "    (already exists)"

# ── Attach VPC connector to Cloud Run ────────────────────────────────────────

echo "==> Updating Cloud Run to route all traffic through VPC connector"
gcloud run services update "$CLOUD_RUN_SERVICE" \
  --region="$REGION" \
  --vpc-connector=kafka-connector \
  --vpc-egress=all-traffic \
  --quiet

# ── Check tunnel status ───────────────────────────────────────────────────────

echo ""
echo "==> Checking tunnel status (may take 1-2 min to come up)..."
sleep 10
gcloud compute vpn-tunnels list --region="$REGION" \
  --format="table(name,status,detailedStatus)"

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Done. Both tunnels should show status: ESTABLISHED"
echo "  If they show FIRST_HANDSHAKE — wait 60s and check again:"
echo "  gcloud compute vpn-tunnels list --region=$REGION"
echo ""
echo "  Next: update PIPELINES_JSON with MSK private broker endpoints"
echo "  e.g. kafka_bootstrap_servers: b-1.cluster.xxx.kafka.us-east-1.amazonaws.com:9092"
echo "════════════════════════════════════════════════════════════"
