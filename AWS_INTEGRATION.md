# AWS MSK → Cloud Run Integration

This document covers what both teams need to do to connect AWS MSK Kafka to the Cloud Run event forwarder.

The flow is: **MSK Kafka → AWS Lambda → HTTPS → Cloud Run → Filter → n8n**

---

## Step 1 — GCP Team (us): Create service account for Lambda

Lambda needs a GCP service account key to authenticate with Cloud Run.

1. Go to: https://console.cloud.google.com/iam-admin/serviceaccounts?project=bdi-apps-491216
2. Click **Create Service Account**
   - Name: `lambda-invoker`
   - Click **Create and Continue**
3. Grant role: **Cloud Run Invoker** → Continue → Done
4. Click the new service account → **Keys** tab → **Add Key** → **Create new key** → JSON → **Create**
   - This downloads a `.json` file — keep it safe
5. Go to Cloud Run service → **Security** tab → **Add Principal**
   - Principal: `lambda-invoker@bdi-apps-491216.iam.gserviceaccount.com`
   - Role: **Cloud Run Invoker**
   - Save

6. Choose an `INGEST_SECRET` — any random string, e.g.:
   ```
   openssl rand -hex 32
   ```
   Save this value — you'll need to put it in Cloud Run AND send it to the AWS team.

7. Update `PIPELINES_JSON` in Cloud Run env vars — add `ingest_secret` to the target pipeline:
   ```json
   [
     {
       "name": "dev",
       "ingest_secret": "YOUR_INGEST_SECRET_HERE",
       "n8n_webhook_url": "https://your-n8n.com/webhook/abc"
     }
   ]
   ```

8. **Send to AWS team:**
   - The downloaded `.json` service account key file
   - `INGEST_SECRET` value
   - Cloud Run URL: `https://n8n-kafka-filter-674519918276.us-central1.run.app`
   - Target pipeline name: `dev` (or `stage`/`prod`)

---

## Step 2 — AWS Team: Deploy Lambda

### What you receive from GCP team
- GCP service account key (`.json` file)
- `INGEST_SECRET` value
- Cloud Run URL
- Pipeline name (`dev` / `stage` / `prod`)

### A. Store the GCP key in Secrets Manager

1. AWS Console → **Secrets Manager** → **Store a new secret**
2. Secret type: **Other type of secret** → **Plaintext**
3. Paste the entire contents of the `.json` key file
4. Secret name: `gcp-lambda-invoker-key`
5. Click **Next** → **Store**

### B. Package the Lambda function

Run on any machine with Python 3.12 and pip:

```bash
mkdir lambda_package
pip install google-auth requests -t lambda_package/
cp scripts/lambda_handler.py lambda_package/lambda_handler.py
cd lambda_package && zip -r ../lambda.zip . && cd ..
```

### C. Create the Lambda function

1. AWS Console → **Lambda** → **Create function**
2. **Author from scratch**
   - Runtime: **Python 3.12**
   - Architecture: x86_64
3. Upload `lambda.zip` → **Upload from** → **.zip file**
4. Set **Handler**: `lambda_handler.lambda_handler`

### D. Set environment variables

In Lambda → **Configuration** → **Environment variables**:

| Key | Value |
|---|---|
| `CLOUD_RUN_URL` | `https://n8n-kafka-filter-674519918276.us-central1.run.app` |
| `INGEST_ENV` | `dev` (or `stage` / `prod`) |
| `INGEST_SECRET` | *(value from GCP team)* |
| `GOOGLE_SA_SECRET` | `gcp-lambda-invoker-key` |

### E. Grant Lambda access to Secrets Manager

In Lambda → **Configuration** → **Permissions** → click the execution role → Add inline policy:

```json
{
  "Effect": "Allow",
  "Action": "secretsmanager:GetSecretValue",
  "Resource": "arn:aws:secretsmanager:*:*:secret:gcp-lambda-invoker-key*"
}
```

### F. Add MSK trigger

1. Lambda → **Add trigger** → **MSK**
2. MSK cluster: *(your cluster)*
3. Topic: *(your Kafka topic)*
4. Starting position: **LATEST**
5. Batch size: `10` (tune later based on volume)
6. Click **Add**

### G. Send back to GCP team

- Confirmation that Lambda is deployed and trigger is active
- MSK topic name(s)
- Any test event you can produce to verify the flow

---

## Step 3 — GCP Team (us): Verify the flow

Once AWS team confirms Lambda is deployed:

1. Ask AWS team to produce a test event to the MSK topic
2. Check Cloud Run logs:
   - Go to: https://console.cloud.google.com/run/detail/us-central1/n8n-kafka-filter/logs?project=bdi-apps-491216
   - Look for: `Ingest event filtered out` or `forwarded`
3. Check n8n for received webhooks

### Test manually (without Lambda)

```bash
curl -X POST https://n8n-kafka-filter-674519918276.us-central1.run.app/ingest/dev \
  -H "Content-Type: application/json" \
  -H "X-Ingest-Secret: YOUR_INGEST_SECRET" \
  -d '{"event_type": "order.created", "orderId": "123"}'
```

Expected responses:
- `{"status": "forwarded"}` — event matched a rule and was sent to n8n
- `{"status": "filtered", "reason": "..."}` — event was skipped by filter
- `{"error": "unauthorized"}` — wrong `INGEST_SECRET`

---

## Checklist

### GCP Team
- [ ] Create `lambda-invoker` service account with Cloud Run Invoker role
- [ ] Download service account key JSON
- [ ] Choose and set `INGEST_SECRET` in Cloud Run `PIPELINES_JSON`
- [ ] Send key file + `INGEST_SECRET` + Cloud Run URL to AWS team
- [ ] Verify events appear in Cloud Run logs after Lambda is live

### AWS Team
- [ ] Store GCP key in Secrets Manager as `gcp-lambda-invoker-key`
- [ ] Package and deploy Lambda with `lambda_handler.py`
- [ ] Set all 4 environment variables
- [ ] Grant Secrets Manager access to Lambda execution role
- [ ] Add MSK trigger
- [ ] Produce a test event and confirm it reaches Cloud Run
