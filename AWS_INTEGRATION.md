# AWS MSK → Cloud Run Integration (Site-to-Site VPN)

Cloud Run connects **directly to MSK Kafka** over a Site-to-Site VPN between GCP and AWS.

```
MSK Kafka (AWS VPC) ──VPN tunnel──► GCP VPC ──VPC Connector──► Cloud Run ──► Filter ──► n8n
```

Scripts are pre-filled with GCP project values. Follow the steps below in order.

---

## Step 1 — Ask AWS team for this info

Before running anything, get these values from the AWS team:

| Info needed | Example |
|---|---|
| AWS VPC CIDR | `10.0.0.0/16` |
| MSK broker endpoints (private DNS) | `b-1.cluster-xxx.kafka.us-east-1.amazonaws.com:9092` |
| Kafka security protocol | `PLAINTEXT`, `SSL`, or `SASL_SSL` |
| Kafka SASL username + password | (if using SASL auth) |
| Kafka topic name(s) | `my-events-topic` |

---

## Step 2 — GCP Team: Run VPN setup step 1

1. Open `scripts/setup_vpn_step1.sh`
2. Replace `FILL_IN_AWS_VPC_CIDR` with the CIDR from AWS team
3. Run in Cloud Shell:
   ```bash
   bash scripts/setup_vpn_step1.sh
   ```
4. The script prints a **GCP VPN Gateway External IP** at the end
5. **Send this IP to the AWS team** — they need it to create the VPN connection

---

## Step 3 — AWS Team: Set up VPN connection

Using the GCP external IP from step 2:

1. AWS Console → **VPC** → **Customer Gateways** → **Create Customer Gateway**
   - Routing: **Static**
   - IP address: *(GCP external IP)*

2. AWS Console → **VPC** → **Site-to-Site VPN Connections** → **Create VPN Connection**
   - Customer Gateway: *(the one you just created)*
   - Routing options: **Static**
   - Static IP Prefixes: `10.128.0.0/9` *(GCP default network CIDR)*
   - Click **Create** (takes ~5 minutes)

3. Once created → **Download Configuration** (Vendor: Generic)
   - This file contains both tunnel IPs and pre-shared keys

4. In your **VPC Route Tables** — add a route:
   - Destination: `10.128.0.0/9`
   - Target: the VPN connection

5. In the **MSK security group** — allow inbound TCP on ports `9092` / `9094` / `9096` from `10.128.0.0/9`

6. **Send back to GCP team:**
   - Tunnel 1 Outside IP + Pre-Shared Key
   - Tunnel 2 Outside IP + Pre-Shared Key

---

## Step 4 — GCP Team: Complete VPN + connect Cloud Run

1. Open `scripts/setup_vpn_step2.sh`
2. Fill in the 4 values from AWS team:
   ```bash
   AWS_VPC_CIDR="..."        # same as step 2
   AWS_TUNNEL_1_IP="..."
   AWS_TUNNEL_1_PSK="..."
   AWS_TUNNEL_2_IP="..."
   AWS_TUNNEL_2_PSK="..."
   ```
3. Run in Cloud Shell:
   ```bash
   bash scripts/setup_vpn_step2.sh
   ```
4. Both tunnels should show **ESTABLISHED** within 1-2 minutes
5. Check status:
   ```bash
   gcloud compute vpn-tunnels list --region=us-central1
   ```

---

## Step 5 — GCP Team: Update PIPELINES_JSON

In Cloud Run → **Edit & Deploy New Revision** → **Variables & Secrets** → update `PIPELINES_JSON`:

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

For plaintext Kafka (no auth), use `"kafka_security_protocol": "PLAINTEXT"` and omit the SASL fields.

---

## Step 6 — Verify

Check Cloud Run logs:
https://console.cloud.google.com/run/detail/us-central1/n8n-kafka-filter/logs?project=bdi-apps-491216

Look for:
- `Pipeline started` with `"mode": "kafka"` — consumer connected
- No connection errors

Check the health endpoint:
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
- [ ] Add route in VPC route table: `10.128.0.0/9` → VPN
- [ ] Allow inbound Kafka ports from `10.128.0.0/9` in MSK security group
- [ ] Send tunnel IPs + pre-shared keys to GCP team

### GCP Team (us) ✓ done
- [x] Cloud Run deployed and accessible
- [x] Google Sign-In configured
- [x] VPN scripts pre-filled with project values (`bdi-apps-491216`, `us-central1`, `n8n-kafka-filter`)

### GCP Team (us) — waiting on AWS
- [ ] Fill in `AWS_VPC_CIDR` in `setup_vpn_step1.sh` and run it → send GCP IP to AWS team
- [ ] Fill in tunnel details in `setup_vpn_step2.sh` and run it → verify ESTABLISHED
- [ ] Update `PIPELINES_JSON` with MSK broker endpoints and credentials
- [ ] Verify Kafka consumer connects and events flow to n8n
