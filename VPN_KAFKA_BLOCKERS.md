# VPN + Kafka Connection — Blockers and Fixes

Every blocker you will likely hit when connecting Cloud Run to AWS MSK via VPN, and exactly how to resolve each one.

---

## Blocker 1 — MSK is on a private AWS VPC

**Problem:**
MSK Kafka has no public endpoint. Cloud Run (GCP) cannot reach it over the internet.

**Fix:**
Set up a Site-to-Site VPN tunnel between AWS VPC and GCP VPC.
Follow `SETUP.md` Phase 2–4. Once the tunnel is `ESTABLISHED`, Cloud Run can reach MSK brokers using their private DNS names.

---

## Blocker 2 — Cloud Run is not inside any VPC

**Problem:**
Even after the VPN tunnel is up, Cloud Run runs outside the GCP VPC by default. It cannot route traffic through the tunnel.

**Fix:**
`scripts/setup_vpn_step1.sh` creates a **Serverless VPC Access connector**.
`scripts/setup_vpn_step2.sh` attaches it to Cloud Run with:
```bash
gcloud run services update kafka-n8n-forwarder \
  --vpc-connector=kafka-connector \
  --vpc-egress=all-traffic
```
`--vpc-egress=all-traffic` is required — without it only RFC-1918 traffic goes through the connector and Kafka traffic may still bypass the VPN.

---

## Blocker 3 — MSK security group blocks GCP traffic

**Problem:**
Even with the VPN tunnel up, MSK brokers reject connections because the security group only allows traffic from within the AWS VPC.

**Fix:**
AWS team must add an inbound rule to the MSK security group:

| Type | Protocol | Port | Source |
|------|----------|------|--------|
| Custom TCP | TCP | 9092 | `10.128.0.0/9` (GCP default network) |
| Custom TCP | TCP | 9094 | `10.128.0.0/9` |

Port `9092` = plaintext, `9094` = TLS. Use whichever MSK is configured for.

---

## Blocker 4 — AWS route table not updated

**Problem:**
VPN tunnel shows `ESTABLISHED` on both sides but traffic still doesn't flow. Packets from GCP reach AWS but MSK never responds.

**Fix:**
AWS team must enable **route propagation** on the route table attached to the MSK subnets:

1. AWS Console → VPC → Route Tables
2. Select the route table for the subnet where MSK lives
3. Tab: **Route Propagation** → Edit → enable the Virtual Private Gateway

Without this, AWS doesn't know to send return traffic back through the VPN.

---

## Blocker 5 — VPN tunnel stuck at FIRST_HANDSHAKE

**Problem:**
After running `setup_vpn_step2.sh`, tunnels show `FIRST_HANDSHAKE` and never reach `ESTABLISHED`.

**Causes and fixes:**

| Cause | Fix |
|-------|-----|
| Wrong pre-shared key | Double-check PSK copied from AWS config — no extra spaces or newlines |
| IKE version mismatch | AWS default is IKEv1 for older configs; change `--ike-version=2` to `--ike-version=1` in the script |
| AWS Customer Gateway IP is wrong | Must be the GCP VPN gateway external IP from step 5, not an internal IP |
| AWS VPN not yet active | AWS VPN connections take 3–5 minutes to become active after creation |

Check tunnel status:
```bash
gcloud compute vpn-tunnels describe aws-tunnel-1 --region=us-central1
```
The `detailedStatus` field tells you exactly what failed.

---

## Blocker 6 — MSK private DNS doesn't resolve from GCP

**Problem:**
VPN is up but connecting to MSK fails with `UnknownHostException` — the broker hostname like `b-1.cluster.abc.kafka.us-east-1.amazonaws.com` doesn't resolve from GCP.

**Why:**
MSK private endpoints use Route 53 private hosted zones that only resolve inside the AWS VPC. GCP cannot use AWS's internal DNS.

**Fix:**
Set up **DNS forwarding** from GCP to AWS:

1. Get the IP of the AWS VPC DNS resolver — it is always the VPC CIDR base + 2.
   Example: VPC CIDR `10.0.0.0/16` → DNS resolver is `10.0.0.2`

2. Create a GCP Cloud DNS forwarding zone:
```bash
gcloud dns managed-zones create aws-kafka-dns \
  --dns-name="kafka.us-east-1.amazonaws.com." \
  --description="Forward MSK DNS to AWS resolver" \
  --visibility=private \
  --networks=default \
  --forwarding-targets=10.0.0.2
```

3. Restart the app — MSK hostnames will now resolve correctly.

---

## Blocker 7 — Kafka authentication fails

**Problem:**
Tunnel is up, DNS resolves, but Kafka connection fails with `SaslAuthenticationException` or `SSL handshake failed`.

**Fix:**
MSK supports several auth modes. Match your `PIPELINES_JSON` to what MSK is configured for:

**SASL/SCRAM (username + password):**
```json
{
  "kafka_security_protocol": "SASL_SSL",
  "kafka_sasl_mechanism": "SCRAM-SHA-512",
  "kafka_sasl_username": "your-user",
  "kafka_sasl_password": "your-password"
}
```

**IAM auth (no password — uses AWS IAM roles):**
Not supported in this app's current config. If MSK uses IAM auth, ask the AWS team to enable SASL/SCRAM as well, or switch to unauthenticated (only safe inside a private VPC).

**No auth (unauthenticated):**
```json
{
  "kafka_security_protocol": "PLAINTEXT",
  "kafka_sasl_mechanism": null,
  "kafka_sasl_username": null,
  "kafka_sasl_password": null
}
```

Ask your AWS team: *"What authentication method is MSK configured for?"*

---

## Blocker 8 — Wrong broker port

**Problem:**
Connection times out. VPN and DNS are fine but the port is wrong.

**MSK port reference:**

| Protocol | Port |
|----------|------|
| Plaintext (no TLS) | 9092 |
| TLS | 9094 |
| SASL/SCRAM + TLS | 9096 |
| IAM + TLS | 9098 |

Get the exact broker string from: **AWS Console → MSK → your cluster → View client information**.
Use the string under "Bootstrap servers" that matches your auth method.

---

## Blocker 9 — Consumer group already has active members

**Problem:**
Cloud Run starts consuming but receives no messages, or gets `RebalanceInProgress` errors in logs.

**Cause:**
If a Lambda or another consumer is already using the same consumer group ID on the same topic, Kafka splits partitions between them. Cloud Run may get 0 partitions assigned.

**Fix:**
Use a unique consumer group ID in `PIPELINES_JSON`:
```json
{
  "kafka_consumer_group_id": "cloud-run-forwarder-prod"
}
```
Each consumer (Lambda, Cloud Run, etc.) that needs to see all messages must use a **different** group ID.

---

## Blocker 10 — App marked unhealthy while Kafka is connecting

**Problem:**
Cloud Run restarts the container immediately because the `/health` or `/ready` probe fails before Kafka connects.

**Fix:**
`service.yaml` already has this configured correctly:
```yaml
startupProbe:
  httpGet:
    path: /ready
  initialDelaySeconds: 5
  periodSeconds: 5
  failureThreshold: 24   # 120 seconds to connect
```
This gives the app 2 minutes to establish the Kafka connection before Cloud Run marks it unhealthy. If Kafka takes longer (e.g. slow VPN), increase `failureThreshold`.

---

## Quick diagnostic checklist

Run these in order to find where the connection is failing:

```bash
# 1. Are VPN tunnels established?
gcloud compute vpn-tunnels list --region=us-central1

# 2. Can Cloud Run reach the AWS VPC at all? (check logs after deploy)
gcloud run services logs read kafka-n8n-forwarder --region=us-central1 --limit=50

# 3. Is the app trying to connect to Kafka?
# Look for: "Connecting to Kafka" or "Kafka connection failed" in logs

# 4. Health check
curl https://your-cloud-run-url/health

# 5. Ready check (fails until Kafka connects)
curl https://your-cloud-run-url/ready

# 6. Metrics (shows messages_consumed once working)
curl https://your-cloud-run-url/metrics
```

---

## What to ask your AWS team

Before starting, get these from the AWS team:

- [ ] AWS VPC CIDR where MSK lives (e.g. `10.0.0.0/16`)
- [ ] MSK bootstrap broker string (from MSK Console → View client information)
- [ ] MSK authentication method (SASL/SCRAM, IAM, or none)
- [ ] SASL username + password (if using SASL/SCRAM)
- [ ] Kafka topic name(s) to consume
- [ ] Confirmation that route propagation is enabled on MSK subnet route tables
- [ ] Confirmation that MSK security group allows TCP 9092/9094 from GCP CIDR
