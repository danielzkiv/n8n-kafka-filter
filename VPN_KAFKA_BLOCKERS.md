# VPN + Kafka Connection — Blockers and Fixes

Every blocker you will likely hit when connecting Google Cloud Run to AWS MSK Kafka via VPN, and exactly how to resolve each one.

---

## What to ask your AWS team before starting

Get these answers before doing anything else:

- [ ] AWS VPC CIDR where MSK lives (e.g. `10.0.0.0/16`)
- [ ] MSK bootstrap broker string — found in AWS Console → MSK → your cluster → View client information (e.g. `b-1.cluster.abc.kafka.us-east-1.amazonaws.com:9092`)
- [ ] MSK authentication method: SASL/SCRAM, IAM, or none
- [ ] SASL username + password (if using SASL/SCRAM)
- [ ] Kafka topic name(s) to consume
- [ ] Confirmation that route propagation is enabled on MSK subnet route tables
- [ ] Confirmation that MSK security group allows TCP 9092/9094 from `10.128.0.0/9` (GCP default network)

---

## Full setup — what you do, what AWS does

### Step 1 — You: create GCP VPN gateway

```bash
PROJECT_ID="your-gcp-project"
REGION="us-central1"
AWS_VPC_CIDR="10.0.0.0/16"       # get from AWS team
VPC_CONNECTOR_CIDR="10.8.0.0/28" # any unused /28 in your GCP network

gcloud config set project $PROJECT_ID

# Enable APIs
gcloud services enable compute.googleapis.com vpcaccess.googleapis.com

# Create VPN gateway
gcloud compute vpn-gateways create aws-vpn-gateway \
  --network=default --region=$REGION

# Create router
gcloud compute routers create vpn-router \
  --network=default --region=$REGION

# Allow Kafka traffic from AWS VPC
gcloud compute firewall-rules create allow-kafka-from-aws \
  --network=default --direction=INGRESS --action=ALLOW \
  --rules=tcp:9092,tcp:9094,tcp:9096 \
  --source-ranges=$AWS_VPC_CIDR

# Create Serverless VPC Access connector (Cloud Run needs this to use the VPN)
gcloud compute networks vpc-access connectors create kafka-connector \
  --network=default --region=$REGION \
  --range=$VPC_CONNECTOR_CIDR \
  --min-instances=2 --max-instances=3 --machine-type=e2-micro

# Get the external IP — send this to your AWS team
gcloud compute vpn-gateways describe aws-vpn-gateway \
  --region=$REGION \
  --format="value(vpnInterfaces[0].ipAddress)"
```

Send the printed IP to your AWS team.

---

### Step 2 — AWS team: create VPN connection

Tell your AWS team:

> 1. **Create a Customer Gateway**
>    - IP: `<GCP external IP from step 1>`
>    - Routing: Static
>
> 2. **Create a Virtual Private Gateway**, attach it to the VPC where MSK lives
>
> 3. **Create a Site-to-Site VPN Connection**
>    - Virtual Private Gateway: the one above
>    - Customer Gateway: the one above
>    - Routing: Static, add route `10.128.0.0/9`
>
> 4. **Enable route propagation** on the route table for MSK subnets
>    - VPC → Route Tables → select MSK subnet route table → Route Propagation → enable VGW
>
> 5. **Update MSK security group** — allow inbound TCP 9092 and 9094 from `10.128.0.0/9`
>
> 6. **Download the VPN config** and send back:
>    - Tunnel 1 outside IP + pre-shared key
>    - Tunnel 2 outside IP + pre-shared key

---

### Step 3 — You: create VPN tunnels

Fill in the 4 values from the AWS team:

```bash
AWS_TUNNEL_1_IP="<tunnel 1 outside IP>"
AWS_TUNNEL_1_PSK="<tunnel 1 pre-shared key>"
AWS_TUNNEL_2_IP="<tunnel 2 outside IP>"
AWS_TUNNEL_2_PSK="<tunnel 2 pre-shared key>"

# Create tunnels
gcloud compute vpn-tunnels create aws-tunnel-1 \
  --vpn-gateway=aws-vpn-gateway --vpn-gateway-interface=0 \
  --peer-address=$AWS_TUNNEL_1_IP --shared-secret=$AWS_TUNNEL_1_PSK \
  --ike-version=2 --region=$REGION --router=vpn-router

gcloud compute vpn-tunnels create aws-tunnel-2 \
  --vpn-gateway=aws-vpn-gateway --vpn-gateway-interface=1 \
  --peer-address=$AWS_TUNNEL_2_IP --shared-secret=$AWS_TUNNEL_2_PSK \
  --ike-version=2 --region=$REGION --router=vpn-router

# Add router interfaces
gcloud compute routers add-interface vpn-router \
  --interface-name=if-tunnel-1 --vpn-tunnel=aws-tunnel-1 --region=$REGION

gcloud compute routers add-interface vpn-router \
  --interface-name=if-tunnel-2 --vpn-tunnel=aws-tunnel-2 --region=$REGION

# Add static route to AWS VPC
gcloud compute routes create route-to-aws \
  --network=default \
  --destination-range=$AWS_VPC_CIDR \
  --next-hop-vpn-tunnel=aws-tunnel-1 \
  --next-hop-vpn-tunnel-region=$REGION \
  --priority=100

# Attach VPC connector to Cloud Run
gcloud run services update kafka-n8n-forwarder \
  --region=$REGION \
  --vpc-connector=kafka-connector \
  --vpc-egress=all-traffic

# Check tunnel status
gcloud compute vpn-tunnels list --region=$REGION
```

Both tunnels must show `ESTABLISHED` before continuing. If they show `FIRST_HANDSHAKE` wait 2 minutes and check again.

---

### Step 4 — You: configure Kafka connection

Update your `PIPELINES_JSON` with the MSK broker endpoints and auth:

```json
[{
  "name": "prod",
  "kafka_bootstrap_servers": "b-1.cluster.abc.kafka.us-east-1.amazonaws.com:9092",
  "kafka_topics": ["your-topic"],
  "kafka_consumer_group_id": "cloud-run-forwarder-prod",
  "kafka_security_protocol": "SASL_SSL",
  "kafka_sasl_mechanism": "SCRAM-SHA-512",
  "kafka_sasl_username": "your-user",
  "kafka_sasl_password": "your-password",
  "n8n_webhook_url": "https://your-n8n/webhook/xxx",
  "event_type_field": "event_type",
  "event_filters": {}
}]
```

Store it in Secret Manager and redeploy:

```bash
echo '[{...}]' | gcloud secrets versions add pipelines-config --data-file=-

gcloud run services update kafka-n8n-forwarder \
  --region=us-central1 \
  --update-secrets=PIPELINES_JSON=pipelines-config:latest
```

---

## Blockers

### Blocker 1 — VPN tunnel stuck at FIRST_HANDSHAKE

Tunnels never reach `ESTABLISHED`.

**Causes and fixes:**

| Cause | Fix |
|-------|-----|
| Wrong pre-shared key | Re-copy PSK from AWS config — no extra spaces or newlines |
| IKE version mismatch | Try `--ike-version=1` instead of `2` |
| Wrong Customer Gateway IP | Must be the GCP VPN gateway external IP, not an internal IP |
| AWS VPN not yet active | AWS VPN connections take 3–5 min after creation — wait and retry |

Check what specifically failed:
```bash
gcloud compute vpn-tunnels describe aws-tunnel-1 --region=us-central1
# Read the "detailedStatus" field
```

---

### Blocker 2 — VPN established but traffic doesn't flow

Tunnels show `ESTABLISHED` but Kafka connections still time out.

**Cause:** AWS route table not updated — return traffic from MSK has no path back through the VPN.

**Fix:** AWS team must enable route propagation on the MSK subnet route table:
- AWS Console → VPC → Route Tables → select the table for MSK subnets → Route Propagation tab → enable the Virtual Private Gateway

---

### Blocker 3 — MSK security group rejects connections

VPN works but Kafka connection is refused.

**Fix:** AWS team adds inbound rules to the MSK security group:

| Protocol | Port | Source |
|----------|------|--------|
| TCP | 9092 | `10.128.0.0/9` |
| TCP | 9094 | `10.128.0.0/9` |
| TCP | 9096 | `10.128.0.0/9` |

---

### Blocker 4 — MSK hostname doesn't resolve from GCP

Error: `UnknownHostException` for broker hostnames like `b-1.cluster.abc.kafka.us-east-1.amazonaws.com`.

**Why:** MSK private DNS is served by Route 53 private hosted zones inside the AWS VPC. GCP cannot use AWS's internal DNS resolver.

**Fix:** Create a Cloud DNS forwarding zone pointing at the AWS VPC DNS resolver.
The AWS DNS resolver IP is always: VPC base CIDR + 2 (e.g. VPC is `10.0.0.0/16` → resolver is `10.0.0.2`).

```bash
gcloud dns managed-zones create aws-kafka-dns \
  --dns-name="kafka.us-east-1.amazonaws.com." \
  --description="Forward MSK DNS to AWS" \
  --visibility=private \
  --networks=default \
  --forwarding-targets=10.0.0.2   # replace with your VPC base + 2
```

Redeploy Cloud Run after creating the zone.

---

### Blocker 5 — Kafka authentication fails

Error: `SaslAuthenticationException` or `SSL handshake failed`.

**Fix:** Match the config to what MSK is actually configured for. Ask the AWS team which auth method is enabled.

**SASL/SCRAM (most common):**
```json
"kafka_security_protocol": "SASL_SSL",
"kafka_sasl_mechanism": "SCRAM-SHA-512",
"kafka_sasl_username": "your-user",
"kafka_sasl_password": "your-password"
```

**No auth (plaintext, only safe inside private VPC):**
```json
"kafka_security_protocol": "PLAINTEXT",
"kafka_sasl_mechanism": null,
"kafka_sasl_username": null,
"kafka_sasl_password": null
```

**IAM auth:** Not supported in this app. Ask the AWS team to enable SASL/SCRAM alongside IAM.

---

### Blocker 6 — Wrong broker port

Connection times out even though VPN and DNS are working.

**MSK ports by protocol:**

| Auth method | Port |
|-------------|------|
| Plaintext, no auth | 9092 |
| TLS, no auth | 9094 |
| SASL/SCRAM + TLS | 9096 |
| IAM + TLS | 9098 |

Get the exact broker string from: AWS Console → MSK → your cluster → **View client information**. Use the string listed under the auth method you are using.

---

### Blocker 7 — Consumer receives no messages

Kafka connection succeeds but no events arrive.

**Cause A — Consumer group conflict:**
Another consumer (e.g. Lambda) is using the same consumer group ID on the same topic. Kafka splits partitions between them and Cloud Run may get none.

**Fix:** Use a unique consumer group ID:
```json
"kafka_consumer_group_id": "cloud-run-forwarder-prod"
```

**Cause B — Wrong offset reset:**
Consumer group already has committed offsets far behind, and `auto_offset_reset` is `latest` — it skips old messages.

**Fix:** Set to `earliest` to read from the beginning, or `latest` to only receive new messages going forward:
```json
"kafka_auto_offset_reset": "latest"
```

---

### Blocker 8 — Cloud Run restarts before Kafka connects

Health probe fails and Cloud Run keeps restarting the container before the Kafka connection is established.

**Fix:** The startup probe in `service.yaml` must give enough time for Kafka to connect. Current config allows 120 seconds (`failureThreshold: 24` × `periodSeconds: 5`). If the VPN adds latency and Kafka takes longer, increase `failureThreshold`:

```yaml
startupProbe:
  httpGet:
    path: /ready
  initialDelaySeconds: 5
  periodSeconds: 5
  failureThreshold: 36   # 180 seconds
```

Redeploy after changing:
```bash
gcloud run services replace service.yaml --region=us-central1
```

---

## Diagnostic checklist

Run in order to find where the connection is failing:

```bash
# 1. VPN tunnels up?
gcloud compute vpn-tunnels list --region=us-central1

# 2. VPC connector active?
gcloud compute networks vpc-access connectors describe kafka-connector --region=us-central1

# 3. Cloud Run using the connector?
gcloud run services describe kafka-n8n-forwarder --region=us-central1 \
  --format="value(spec.template.metadata.annotations)"
# Should show: run.googleapis.com/vpc-access-connector: kafka-connector

# 4. App logs — look for "Connecting to Kafka" or error messages
gcloud run services logs read kafka-n8n-forwarder --region=us-central1 --limit=50

# 5. Health check
curl https://your-cloud-run-url/health

# 6. Ready check — fails until Kafka connects
curl https://your-cloud-run-url/ready

# 7. Metrics — messages_consumed goes up when Kafka is working
curl https://your-cloud-run-url/metrics
```
