# AWS MSK → Cloud Run Integration (Site-to-Site VPN)

Cloud Run connects **directly to MSK Kafka** over a Site-to-Site VPN between GCP and AWS.

```
MSK Kafka (AWS VPC) ──VPN tunnel──► GCP VPC ──VPC Connector──► Cloud Run ──► Filter ──► n8n
```

The setup is split into steps because both teams need to exchange information mid-way.

---

## What we need from AWS team first

Before anything can start, ask the AWS team to provide:

| Info needed | Example |
|---|---|
| AWS VPC CIDR | `10.0.0.0/16` |
| MSK broker endpoints (private DNS) | `b-1.cluster-xxx.kafka.us-east-1.amazonaws.com:9092` |
| Kafka security protocol | `PLAINTEXT`, `SSL`, `SASL_SSL` |
| Kafka SASL username + password | (if using SASL auth) |
| Kafka topic name(s) | `my-events-topic` |

---

## Step 1 — GCP Team (us): Set up VPN gateway

1. Open `scripts/setup_vpn_step1.sh` and fill in the top variables:
   ```bash
   PROJECT_ID="bdi-apps-491216"
   REGION="us-central1"
   AWS_VPC_CIDR="<AWS VPC CIDR from AWS team>"
   VPC_CONNECTOR_CIDR="10.8.0.0/28"   # unused /28 in your GCP network
   ```

2. Run it in Cloud Shell:
   ```bash
   bash scripts/setup_vpn_step1.sh
   ```

3. The script prints a **GCP VPN Gateway External IP** at the end.

4. **Send this IP to the AWS team** — they need it to create the VPN on their side.

---

## Step 2 — AWS Team: Set up VPN connection

Using the GCP external IP received from step 1:

1. AWS Console → **VPC** → **Customer Gateways** → **Create Customer Gateway**
   - Routing: **Static**
   - IP address: *(GCP external IP)*
   - Click **Create**

2. AWS Console → **VPC** → **Site-to-Site VPN Connections** → **Create VPN Connection**
   - Customer Gateway: *(the one you just created)*
   - Routing options: **Static**
   - Static IP Prefixes: *(GCP VPC CIDR — typically `10.128.0.0/9` for GCP default network)*
   - Click **Create VPN Connection** (takes ~5 minutes)

3. Once created, click **Download Configuration**
   - Vendor: **Generic**
   - This gives you a file with both tunnel IPs and pre-shared keys

4. In your AWS **VPC Route Tables** — add a route for GCP traffic:
   - Destination: `10.128.0.0/9` (GCP default network CIDR)
   - Target: the VPN connection

5. Make sure MSK security group allows inbound TCP on port `9092` (or `9094`/`9096`) from GCP CIDR `10.128.0.0/9`

6. **Send back to GCP team:**
   - Tunnel 1 Outside IP
   - Tunnel 1 Pre-Shared Key
   - Tunnel 2 Outside IP
   - Tunnel 2 Pre-Shared Key

---

## Step 3 — GCP Team (us): Complete VPN + connect Cloud Run

1. Open `scripts/setup_vpn_step2.sh` and fill in the values from AWS team:
   ```bash
   PROJECT_ID="bdi-apps-491216"
   REGION="us-central1"
   AWS_VPC_CIDR="<same as step 1>"
   CLOUD_RUN_SERVICE="n8n-kafka-filter"

   AWS_TUNNEL_1_IP="<from AWS team>"
   AWS_TUNNEL_1_PSK="<from AWS team>"
   AWS_TUNNEL_2_IP="<from AWS team>"
   AWS_TUNNEL_2_PSK="<from AWS team>"
   ```

2. Run it in Cloud Shell:
   ```bash
   bash scripts/setup_vpn_step2.sh
   ```

3. The script creates both tunnels and attaches the VPC connector to Cloud Run.
   Both tunnels should show status **ESTABLISHED** within 1-2 minutes.

4. Check tunnel status:
   ```bash
   gcloud compute vpn-tunnels list --region=us-central1
   ```

---

## Step 4 — GCP Team (us): Configure PIPELINES_JSON

Update the `PIPELINES_JSON` env var in Cloud Run with the MSK broker details:

```json
[
  {
    "name": "dev",
    "kafka_bootstrap_servers": "b-1.cluster-xxx.kafka.us-east-1.amazonaws.com:9092,b-2.cluster-xxx.kafka.us-east-1.amazonaws.com:9092",
    "kafka_topics": ["my-events-topic"],
    "kafka_consumer_group_id": "n8n-kafka-filter-dev",
    "kafka_security_protocol": "SASL_SSL",
    "kafka_sasl_mechanism": "PLAIN",
    "kafka_sasl_username": "YOUR_USERNAME",
    "kafka_sasl_password": "YOUR_PASSWORD",
    "n8n_webhook_url": "https://your-n8n.com/webhook/abc"
  }
]
```

For plaintext (no auth) Kafka:
```json
{
  "kafka_security_protocol": "PLAINTEXT"
}
```

After saving, Cloud Run will restart and connect to MSK automatically.

---

## Step 5 — Verify

Check Cloud Run logs:
https://console.cloud.google.com/run/detail/us-central1/n8n-kafka-filter/logs?project=bdi-apps-491216

Look for:
- `Pipeline started` with `"mode": "kafka"` — consumer connected
- Events flowing: `Consumer started` with no errors

Check `/health` endpoint:
```bash
curl https://n8n-kafka-filter-674519918276.us-central1.run.app/health
```

Expected:
```json
{"status": "ok", "pipelines": {"dev": {"mode": "kafka", "running": true}}}
```

---

## Checklist

### AWS Team
- [ ] Share: VPC CIDR, MSK broker endpoints, Kafka auth credentials, topic names
- [ ] Create Customer Gateway using GCP external IP
- [ ] Create Site-to-Site VPN connection (static routing)
- [ ] Add route in VPC route table for GCP CIDR → VPN
- [ ] Allow inbound Kafka ports from GCP CIDR in MSK security group
- [ ] Send tunnel IPs + pre-shared keys to GCP team

### GCP Team (us)
- [ ] Get AWS VPC CIDR + MSK details from AWS team
- [ ] Run `setup_vpn_step1.sh` → send GCP external IP to AWS team
- [ ] Wait for AWS team to complete their setup
- [ ] Run `setup_vpn_step2.sh` with tunnel details → verify ESTABLISHED
- [ ] Update `PIPELINES_JSON` with MSK broker endpoints and credentials
- [ ] Verify Cloud Run connects to Kafka and events flow to n8n
