# AWS Lambda → Cloud Run Integration

Lambda reads from MSK and forwards each event to Cloud Run over HTTPS.

```
AWS MSK Kafka → Lambda (MSK trigger) → HTTPS POST /ingest/{env} → Cloud Run → Filter → n8n
```

---

## Prerequisites

- Cloud Run service deployed and publicly accessible (see README)
- `PIPELINES_JSON` configured with `ingest_secret` for each pipeline, e.g.:
  ```json
  [{"name":"dev","ingest_secret":"dev-secret-change-me","n8n_webhook_url":"https://your-n8n.com/webhook/abc"}]
  ```
- MSK cluster running in your AWS account

---

## Step 1 — Create an IAM execution role for Lambda

Your Lambda needs permission to read from MSK and write logs.

In the AWS Console: **IAM** → **Roles** → **Create role** → **Lambda** → attach:
- `AWSLambdaMSKExecutionRole` (managed policy — grants MSK consumer + ENI + CloudWatch Logs)

Or via CLI:
```bash
aws iam create-role \
  --role-name lambda-msk-to-cloudrun \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam attach-role-policy \
  --role-name lambda-msk-to-cloudrun \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaMSKExecutionRole
```

---

## Step 2 — Package the Lambda function

```bash
cd lambda
zip lambda.zip handler.py
```

No external dependencies — handler uses stdlib only (`urllib`, `json`, `base64`).

---

## Step 3 — Create the Lambda function

```bash
aws lambda create-function \
  --function-name msk-to-cloud-run-dev \
  --runtime python3.12 \
  --handler handler.lambda_handler \
  --zip-file fileb://lambda/lambda.zip \
  --role arn:aws:iam::ACCOUNT_ID:role/lambda-msk-to-cloudrun \
  --environment Variables='{
    "CLOUD_RUN_URL":"https://YOUR_SERVICE.run.app",
    "INGEST_SECRET":"dev-secret-change-me",
    "PIPELINE_ENV":"dev"
  }' \
  --timeout 60 \
  --memory-size 256
```

---

## Step 4 — Add the MSK trigger

```bash
aws lambda create-event-source-mapping \
  --function-name msk-to-cloud-run-dev \
  --event-source-arn arn:aws:kafka:us-east-1:ACCOUNT_ID:cluster/CLUSTER_NAME/CLUSTER_ID \
  --topics your-topic-name \
  --starting-position LATEST \
  --batch-size 10 \
  --enabled
```

> **Batch size**: each Lambda invocation processes up to `batch-size` Kafka messages.
> Each message is forwarded individually to Cloud Run.

---

## Step 5 — Verify

1. Produce a test event to your MSK topic
2. Check Lambda **CloudWatch Logs** for `"Batch complete"` with `forwarded > 0`
3. Check Cloud Run logs for `"source": "http-ingest"` entries
4. Check `https://YOUR_SERVICE.run.app/metrics` for forwarded counts

---

## Multiple environments

Create one Lambda per environment, each with its own env vars and MSK trigger:

| Lambda name | `PIPELINE_ENV` | `INGEST_SECRET` | Topic |
|---|---|---|---|
| `msk-to-cloud-run-dev` | `dev` | `dev-secret` | `dev-events` |
| `msk-to-cloud-run-prod` | `prod` | `prod-secret` | `prod-events` |

---

## Retry / DLQ behaviour

- On a non-200/422 response from Cloud Run, the Lambda handler raises — the MSK trigger retries the batch automatically
- Configure a **Lambda DLQ** (SQS) to capture batches that exhaust all retries
- 422 responses (schema/format errors) are logged and skipped — retrying them won't help

---

## Updating the handler

```bash
cd lambda
zip lambda.zip handler.py
aws lambda update-function-code \
  --function-name msk-to-cloud-run-dev \
  --zip-file fileb://lambda.zip
```
