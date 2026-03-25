# AWS Team & GCP Team — What Each Side Needs to Do

---

## GCP Team

### Step 1 — Deploy the app

```bash
# Clone the repo
git clone https://github.com/danielzkiv/n8n-kafka-filter.git
cd n8n-kafka-filter

# Fill in your project ID and region in scripts/setup_gcp.sh, then run:
bash scripts/setup_gcp.sh
```

This builds the Docker image, pushes it to GCR, and deploys to Cloud Run.
At the end it prints the Cloud Run URL — save it:
```
https://kafka-n8n-forwarder-xxxx-uc.a.run.app
```

---

### Step 2 — Create GCP VPN gateway

Before running, fill in these values in the commands below:

| Variable | What to put |
|----------|-------------|
| `PROJECT_ID` | Your GCP project ID |
| `REGION` | Region where Cloud Run is deployed (e.g. `us-central1`) |
| `AWS_VPC_CIDR` | Get this from the AWS team |
| `VPC_CONNECTOR_CIDR` | Any unused `/28` in your GCP network (e.g. `10.8.0.0/28`) |

```bash
PROJECT_ID="your-gcp-project"
REGION="us-central1"
AWS_VPC_CIDR="10.0.0.0/16"
VPC_CONNECTOR_CIDR="10.8.0.0/28"

gcloud config set project $PROJECT_ID
gcloud services enable compute.googleapis.com vpcaccess.googleapis.com

gcloud compute vpn-gateways create aws-vpn-gateway \
  --network=default --region=$REGION

gcloud compute routers create vpn-router \
  --network=default --region=$REGION

gcloud compute firewall-rules create allow-kafka-from-aws \
  --network=default --direction=INGRESS --action=ALLOW \
  --rules=tcp:9092,tcp:9094,tcp:9096 \
  --source-ranges=$AWS_VPC_CIDR

gcloud compute networks vpc-access connectors create kafka-connector \
  --network=default --region=$REGION \
  --range=$VPC_CONNECTOR_CIDR \
  --min-instances=2 --max-instances=3 --machine-type=e2-micro

# Print the external IP — send this to the AWS team
gcloud compute vpn-gateways describe aws-vpn-gateway \
  --region=$REGION \
  --format="value(vpnInterfaces[0].ipAddress)"
```

**Send the printed IP to the AWS team.** Wait for them to complete their steps and send back the tunnel info.

---

### Step 3 — Create VPN tunnels (after AWS team responds)

Fill in the values the AWS team sends you:

```bash
AWS_TUNNEL_1_IP="<from AWS team>"
AWS_TUNNEL_1_PSK="<from AWS team>"
AWS_TUNNEL_2_IP="<from AWS team>"
AWS_TUNNEL_2_PSK="<from AWS team>"

gcloud compute vpn-tunnels create aws-tunnel-1 \
  --vpn-gateway=aws-vpn-gateway --vpn-gateway-interface=0 \
  --peer-address=$AWS_TUNNEL_1_IP --shared-secret=$AWS_TUNNEL_1_PSK \
  --ike-version=2 --region=$REGION --router=vpn-router

gcloud compute vpn-tunnels create aws-tunnel-2 \
  --vpn-gateway=aws-vpn-gateway --vpn-gateway-interface=1 \
  --peer-address=$AWS_TUNNEL_2_IP --shared-secret=$AWS_TUNNEL_2_PSK \
  --ike-version=2 --region=$REGION --router=vpn-router

gcloud compute routers add-interface vpn-router \
  --interface-name=if-tunnel-1 --vpn-tunnel=aws-tunnel-1 --region=$REGION

gcloud compute routers add-interface vpn-router \
  --interface-name=if-tunnel-2 --vpn-tunnel=aws-tunnel-2 --region=$REGION

gcloud compute routes create route-to-aws \
  --network=default \
  --destination-range=$AWS_VPC_CIDR \
  --next-hop-vpn-tunnel=aws-tunnel-1 \
  --next-hop-vpn-tunnel-region=$REGION \
  --priority=100

gcloud run services update kafka-n8n-forwarder \
  --region=$REGION \
  --vpc-connector=kafka-connector \
  --vpc-egress=all-traffic

# Verify — both must show ESTABLISHED
gcloud compute vpn-tunnels list --region=$REGION
```

---

### Step 4 — Configure Kafka and deploy

Fill in the broker endpoints and credentials the AWS team provides:

```bash
# Create PIPELINES_JSON with real MSK broker info:
cat > pipelines.json << 'EOF'
[{
  "name": "prod",
  "kafka_bootstrap_servers": "<MSK broker string from AWS team>",
  "kafka_topics": ["<topic name from AWS team>"],
  "kafka_consumer_group_id": "cloud-run-forwarder-prod",
  "kafka_security_protocol": "SASL_SSL",
  "kafka_sasl_mechanism": "SCRAM-SHA-512",
  "kafka_sasl_username": "<username from AWS team>",
  "kafka_sasl_password": "<password from AWS team>",
  "n8n_webhook_url": "https://your-n8n/webhook/xxx",
  "event_type_field": "event_type",
  "event_filters": {}
}]
EOF

gcloud secrets versions add pipelines-config --data-file=pipelines.json

gcloud run services update kafka-n8n-forwarder \
  --region=$REGION \
  --update-secrets=PIPELINES_JSON=pipelines-config:latest
```

---

### Step 5 — Verify everything is working

```bash
# App is healthy
curl https://kafka-n8n-forwarder-xxxx-uc.a.run.app/health

# Kafka is connected
curl https://kafka-n8n-forwarder-xxxx-uc.a.run.app/ready

# Events are being consumed (messages_consumed goes up)
curl https://kafka-n8n-forwarder-xxxx-uc.a.run.app/metrics
```

Open the filter UI to configure which event types to forward:
```
https://kafka-n8n-forwarder-xxxx-uc.a.run.app/ui
```

---

---

## AWS Team

### What you need to provide to the GCP team first

Before anything else, send the GCP team:

- **AWS VPC CIDR** where MSK lives (e.g. `10.0.0.0/16`)
- **MSK bootstrap broker string** — AWS Console → MSK → your cluster → View client information
- **Auth method** MSK is configured for (SASL/SCRAM, IAM, or none)
- **SASL username + password** (if using SASL/SCRAM)
- **Kafka topic name(s)** the app should consume

---

### Step 1 — Create a Customer Gateway

Wait for the GCP team to send you their **VPN gateway external IP**, then:

1. AWS Console → VPC → **Customer Gateways** → Create Customer Gateway
   - Routing: **Static**
   - IP address: `<GCP external IP>`

---

### Step 2 — Create a Virtual Private Gateway

1. AWS Console → VPC → **Virtual Private Gateways** → Create
2. Attach it to the VPC where MSK lives:
   - Select the VGW → Actions → **Attach to VPC** → select the MSK VPC

---

### Step 3 — Create a Site-to-Site VPN Connection

1. AWS Console → VPC → **Site-to-Site VPN Connections** → Create
   - Virtual Private Gateway: the one you just created
   - Customer Gateway: the one you just created
   - Routing: **Static**
   - Static IP prefixes: `10.128.0.0/9` (GCP default network)

2. Wait for the connection status to become **Available** (3–5 minutes)

3. Select the connection → **Download Configuration**
   - Vendor: Generic
   - Download and open the file — you need:
     - Tunnel 1 Outside IP address
     - Tunnel 1 Pre-Shared Key
     - Tunnel 2 Outside IP address
     - Tunnel 2 Pre-Shared Key

**Send these 4 values to the GCP team.**

---

### Step 4 — Enable route propagation

1. AWS Console → VPC → **Route Tables**
2. Find the route table attached to the subnets where MSK runs
3. Tab: **Route Propagation** → Edit route propagation → enable the Virtual Private Gateway

Without this, return traffic from MSK cannot reach GCP.

---

### Step 5 — Update MSK security group

1. AWS Console → EC2 → **Security Groups**
2. Find the security group attached to the MSK cluster
3. Add inbound rules:

| Type | Protocol | Port | Source |
|------|----------|------|--------|
| Custom TCP | TCP | 9092 | `10.128.0.0/9` |
| Custom TCP | TCP | 9094 | `10.128.0.0/9` |
| Custom TCP | TCP | 9096 | `10.128.0.0/9` |

---

### Step 6 — Confirm with the GCP team

Let the GCP team know all steps are done. They will verify the tunnels are `ESTABLISHED` and that the app connects to Kafka.

---

---

## Information exchange summary

| What | Direction | When |
|------|-----------|------|
| AWS VPC CIDR | AWS → GCP | Before GCP starts |
| MSK broker string | AWS → GCP | Before GCP starts |
| Kafka auth method + credentials | AWS → GCP | Before GCP starts |
| Kafka topic names | AWS → GCP | Before GCP starts |
| GCP VPN gateway external IP | GCP → AWS | After GCP Step 2 |
| VPN tunnel IPs + pre-shared keys | AWS → GCP | After AWS Step 3 |
