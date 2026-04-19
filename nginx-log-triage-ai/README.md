# nginx-log-triage-ai

Automatically ship NGINX access logs from EC2 to S3, trigger a Lambda function every 5 minutes, and use OpenAI GPT-4o-mini to triage logs for anomalies, errors, and suspicious traffic. Results are saved back to S3 as JSON and alerts are sent via SNS on WARNING or CRITICAL findings.

## Architecture

```
EC2 (NGINX)
    │
    │  cron every 5 min — aws s3 sync
    ▼
S3: test-logs-ai-4-19-2026/raw/yyyy/mm/dd/
    │
    │  S3 ObjectCreated event (instant)
    ▼
Lambda: nginx-log-triage
    │
    │  reads log → sends to OpenAI gpt-4o-mini
    ▼
OpenAI API
    │
    ├──→ S3: test-logs-ai-4-19-2026/triage/yyyy/mm/dd/*.json
    │
    └──→ SNS email alert (WARNING / CRITICAL only)
```

## Project Structure

```
nginx-log-triage-ai/
├── lambda/
│   ├── handler.py          # Lambda function
│   └── requirements.txt    # Python dependencies
├── cron/
│   └── nginx-s3-sync       # Cron file for EC2
├── docs/
│   └── iam-policy.json     # IAM inline policy for Lambda role
└── README.md
```

---

## Step-by-Step Setup (AWS Console — Manual)

### Step 1 — S3 Bucket

1. Go to **S3 → Create bucket**
2. Bucket name: `test-logs-ai-4-19-2026`
3. Region: `eu-west-1`
4. Block all public access: ✅ enabled
5. Everything else: leave default
6. Click **Create bucket**

---

### Step 2 — SSM Parameter (OpenAI API Key)

1. Go to **Systems Manager → Parameter Store → Create parameter**
2. Name: `/nginx-triage/openai-api-key`
3. Tier: Standard
4. Type: `SecureString`
5. Value: your OpenAI API key (`sk-...`)
6. Click **Create parameter**

---

### Step 3 — SNS Topic (Alerts)

1. Go to **SNS → Topics → Create topic**
2. Type: Standard
3. Name: `nginx-triage-alerts`
4. Click **Create topic**
5. Copy the **Topic ARN** — you will need it in Steps 4 and 6
6. Click **Create subscription** → Protocol: Email → enter your email
7. Confirm the subscription from your inbox

---

### Step 4 — IAM Role for Lambda

1. Go to **IAM → Roles → Create role**
2. Trusted entity: **AWS service → Lambda**
3. Skip managed policies → Role name: `nginx-triage-lambda-role`
4. Click **Create role**
5. Open the role → **Add permissions → Create inline policy → JSON tab**
6. Paste the contents of `docs/iam-policy.json`
7. Replace `YOUR-SNS-TOPIC-ARN` with your SNS ARN from Step 3
8. Replace `YOUR-ACCOUNT-ID` with your AWS account ID
9. Policy name: `nginx-triage-policy` → **Create policy**

---

### Step 5 — Package Lambda

Run these commands on your machine (requires Python 3.12):

```bash
rm -rf package triage.zip

pip install openai \
  --platform manylinux2014_x86_64 \
  --target ./package \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all:

cp lambda/handler.py ./package/
cd package && zip -r ../triage.zip . && cd ..
```

---

### Step 6 — Create Lambda Function

1. Go to **Lambda → Create function**
2. Author from scratch
3. Function name: `nginx-log-triage`
4. Runtime: `Python 3.12`
5. Architecture: `x86_64`
6. Permissions: **Use an existing role** → `nginx-triage-lambda-role`
7. Click **Create function**
8. Click **Upload from → .zip file** → upload `triage.zip`
9. Go to **Runtime settings → Edit** → Handler: `handler.lambda_handler` → **Save**
10. Go to **Configuration → Environment variables → Edit** → Add:

| Key | Value |
|---|---|
| `OPENAI_API_KEY` | your OpenAI API key |
| `SNS_TOPIC_ARN` | your SNS topic ARN from Step 3 |

11. Go to **Configuration → General configuration → Edit**:
    - Timeout: `2 min 0 sec`
    - Memory: `512 MB`
    - Click **Save**

---

### Step 7 — S3 Event Trigger

1. Go to **S3 → `test-logs-ai-4-19-2026` → Properties**
2. Scroll to **Event notifications → Create event notification**
3. Name: `nginx-log-trigger`
4. Prefix: `raw/`
5. Suffix: `.log`
6. Event type: ✅ `s3:ObjectCreated:All`
7. Destination: **Lambda function** → `nginx-log-triage`
8. Click **Save changes**

---

### Step 8 — EC2 Cron Job

SSH into your EC2 and run:

```bash
which aws
```

Then create the cron file:

```bash
sudo cp cron/nginx-s3-sync /etc/cron.d/nginx-s3-sync
sudo chmod 644 /etc/cron.d/nginx-s3-sync
sudo chown root:root /etc/cron.d/nginx-s3-sync
```

> If `aws` is not at `/usr/local/bin/aws`, edit the file and update the path.

---

### Step 9 — Test End to End

Force a manual sync from EC2:

```bash
aws s3 sync /var/log/nginx/ s3://test-logs-ai-4-19-2026/raw/$(date +%Y/%m/%d)/ \
  --exclude "*.gz" --exclude "*.pid" --region eu-west-1
```

Then verify:
- **S3** → `test-logs-ai-4-19-2026/triage/` → JSON file appears within seconds
- **Lambda → Monitor → Logs** → CloudWatch shows execution
- **Email** → alert received if WARNING or CRITICAL severity detected

---

## How It Works

- Cron runs every 5 minutes on EC2 and syncs `/var/log/nginx/` to S3
- S3 fires an `ObjectCreated` event the moment a file lands
- Lambda reads the log file, takes the last 3000 lines, and sends them to OpenAI
- OpenAI returns a JSON triage with `summary`, `anomalies`, `severity`, and `recommendations`
- Result is saved to `triage/yyyy/mm/dd/<filename>.json` in the same S3 bucket
- If severity is `WARNING` or `CRITICAL`, an SNS email alert is sent

## Triage Output Example

```json
{
  "summary": "Normal traffic with one suspicious path scan detected",
  "anomalies": [
    "Multiple 404s from IP 1.2.3.4 targeting /wp-admin, /.env, /admin",
    "Spike of 42 requests in 10 seconds from single IP"
  ],
  "severity": "WARNING",
  "recommendations": [
    "Block IP 1.2.3.4 at security group level",
    "Add rate limiting in NGINX config"
  ],
  "source_key": "raw/2026/04/19/access.log",
  "source_bucket": "test-logs-ai-4-19-2026"
}
```

## Requirements

- AWS account with EC2 running NGINX
- OpenAI API key with credits
- Python 3.12 (for packaging Lambda)
- AWS CLI configured on EC2 with S3 write permissions via instance profile
