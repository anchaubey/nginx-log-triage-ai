import boto3, gzip, json, os
from openai import OpenAI

s3 = boto3.client("s3")
sns = boto3.client("sns")
openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

SYSTEM_PROMPT = """You are an SRE triaging NGINX access logs.
Analyze the provided log lines and return ONLY a JSON object with:
- summary: string, brief traffic pattern description
- anomalies: list of strings, each describing a suspicious finding
- severity: one of INFO | WARNING | CRITICAL
- recommendations: list of strings, suggested actions
No markdown, no explanation, just the JSON object."""

def lambda_handler(event, context):
    record = event["Records"][0]["s3"]
    bucket = record["bucket"]["name"]
    key    = record["object"]["key"]

    obj  = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    if key.endswith(".gz"):
        body = gzip.decompress(body)

    lines  = body.decode("utf-8").strip().splitlines()
    sample = "\n".join(lines[-3000:])

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": f"Log file: {key}\n\n{sample}"}
        ],
        response_format={"type": "json_object"},
        max_tokens=800
    )

    triage = json.loads(response.choices[0].message.content)
    triage["source_key"]    = key
    triage["source_bucket"] = bucket

    result_key = key.replace("raw/", "triage/").rsplit(".", 1)[0] + ".json"
    s3.put_object(
        Bucket      = "test-logs-ai-4-19-2026",
        Key         = result_key,
        Body        = json.dumps(triage, indent=2),
        ContentType = "application/json"
    )

    if triage.get("severity") in ("WARNING", "CRITICAL"):
        sns.publish(
            TopicArn = os.environ["SNS_TOPIC_ARN"],
            Subject  = f"[{triage['severity']}] NGINX anomaly — {key}",
            Message  = json.dumps(triage, indent=2)
        )

    return {"statusCode": 200, "body": json.dumps(triage)}
