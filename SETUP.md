# Setup Guide

Complete steps to deploy the app to Google Cloud Run and connect it to AWS MSK Kafka via VPN.

---

## Who does what

| Step | Who |
|------|-----|
| Deploy Cloud Run | You |
| Run setup_vpn_step1.sh, send GCP IP | You |
| Create Customer Gateway + VPN, send tunnel info back | AWS team |
| Run setup_vpn_step2.sh with tunnel info | You |
| Update PIPELINES_JSON with MSK brokers | You |
| Verify tunnels + test Kafka connection | Both |

---

## Phase 1 — Deploy app to Google Cloud Run

**1. Run the GCP setup script**

Fill in your project ID and region inside `scripts/setup_gcp.sh`, then:

```bash
bash scripts/setup_gcp.sh
```

This creates the service account, enables APIs, builds the Docker image, and deploys to Cloud Run.
At the end it prints your Cloud Run URL:
```
https://kafka-n8n-forwarder-xxxx-uc.a.run.app
```

**2. Set PIPELINES_JSON in Secret Manager**

For now use HTTP ingest mode only (no Kafka yet) to verify the app is alive:

```json
[{
  "name": "prod",
  "ingest_secret": "some-secret",
  "n8n_webhook_url": "https://your-n8n/webhook/xxx",
  "event_type_field": "event_type",
  "event_filters": {}
}]
```

```bash
echo '[{...}]' | gcloud secrets versions add pipelines-config --data-file=-
```

**3. Verify the app is running**

```
https://kafka-n8n-forwarder-xxxx-uc.a.run.app/health
```

Should return `{"status": "ok"}`.

---

## Phase 2 — GCP VPN gateway

**4. Edit `scripts/setup_vpn_step1.sh`** and fill in:

| Variable | Value |
|----------|-------|
| `PROJECT_ID` | Your GCP project ID |
| `REGION` | Region where Cloud Run is deployed (e.g. `us-central1`) |
| `AWS_VPC_CIDR` | CIDR of the AWS VPC where MSK lives — ask your AWS team (e.g. `10.0.0.0/16`) |
| `VPC_CONNECTOR_CIDR` | Any unused `/28` in your GCP network (e.g. `10.8.0.0/28`) |

**5. Run it**

```bash
bash scripts/setup_vpn_step1.sh
```

Output:
```
GCP VPN Gateway External IP: 34.120.45.67
```

**6. Send the GCP IP to your AWS team** — see the message template in Phase 3 below.

---

## Phase 3 — AWS VPN connection (AWS team)

Send this to your AWS team:

> Please do the following in the AWS VPC where MSK lives:
>
> 1. **Create a Customer Gateway**
>    - IP address: `<GCP_EXTERNAL_IP from step 5>`
>    - Routing: Static
>
> 2. **Create a Site-to-Site VPN Connection**
>    - Attach to the Virtual Private Gateway on the MSK VPC
>    - Static route: `10.128.0.0/9` (GCP default network CIDR)
>
> 3. **Download the VPN config** and send us:
>    - Tunnel 1 outside IP
>    - Tunnel 1 pre-shared key
>    - Tunnel 2 outside IP
>    - Tunnel 2 pre-shared key
>
> 4. **Update the MSK security group**
>    - Allow inbound TCP ports `9092` and `9094` from `10.128.0.0/9`
>
> 5. **Send us the MSK broker endpoints**
>    - Found in: MSK Console → your cluster → View client information
>    - Example: `b-1.cluster.abc.kafka.us-east-1.amazonaws.com:9092`

AWS team sends back 5 things:
- Tunnel 1 outside IP + pre-shared key
- Tunnel 2 outside IP + pre-shared key
- MSK broker string

---

## Phase 4 — Complete VPN tunnels

**7. Edit `scripts/setup_vpn_step2.sh`** and fill in the 4 tunnel values from the AWS team:

| Variable | Value |
|----------|-------|
| `PROJECT_ID` | Your GCP project ID |
| `REGION` | Same region as step 4 |
| `AWS_VPC_CIDR` | Same as step 4 |
| `AWS_TUNNEL_1_IP` | Tunnel 1 outside IP from AWS |
| `AWS_TUNNEL_1_PSK` | Tunnel 1 pre-shared key from AWS |
| `AWS_TUNNEL_2_IP` | Tunnel 2 outside IP from AWS |
| `AWS_TUNNEL_2_PSK` | Tunnel 2 pre-shared key from AWS |

**8. Run it**

```bash
bash scripts/setup_vpn_step2.sh
```

**9. Verify both tunnels are established**

```bash
gcloud compute vpn-tunnels list --region=us-central1
```

Both tunnels should show `ESTABLISHED`. If they show `FIRST_HANDSHAKE`, wait 2 minutes and check again.

---

## Phase 5 — Connect app to Kafka

**10. Update PIPELINES_JSON** with the MSK broker endpoints from the AWS team:

```json
[{
  "name": "prod",
  "kafka_bootstrap_servers": "b-1.cluster.abc.kafka.us-east-1.amazonaws.com:9092",
  "kafka_topics": ["your-topic"],
  "kafka_sasl_username": "your-user",
  "kafka_sasl_password": "your-password",
  "n8n_webhook_url": "https://your-n8n/webhook/xxx",
  "event_type_field": "event_type",
  "event_filters": {}
}]
```

```bash
echo '[{...}]' | gcloud secrets versions add pipelines-config --data-file=-
```

**11. Redeploy Cloud Run** to pick up the updated secret:

```bash
gcloud run services update kafka-n8n-forwarder \
  --region=us-central1 \
  --update-secrets=PIPELINES_JSON=pipelines-config:latest
```

---

## Phase 6 — Test

**12. Check health**
```
https://kafka-n8n-forwarder-xxxx-uc.a.run.app/health
```
Should show `"mode": "kafka"` and `"running": true` for each pipeline.

**13. Check Kafka is connected**
```
https://kafka-n8n-forwarder-xxxx-uc.a.run.app/ready
```
Should show `"status": "ready"`.

**14. Configure event type filters**

Open the filter UI and add the event types you want to forward:
```
https://kafka-n8n-forwarder-xxxx-uc.a.run.app/ui
```

**15. Publish a test event**

Ask the AWS team to publish a test message to the Kafka topic. It should arrive at your n8n webhook within seconds.

You can also check metrics:
```
https://kafka-n8n-forwarder-xxxx-uc.a.run.app/metrics
```
