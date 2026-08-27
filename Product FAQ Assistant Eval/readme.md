# Product FAQ Assistant Eval: Amazon Bedrock Prompt Management

A grounded FAQ assistant built with **Amazon Bedrock Prompt Management**, scored end to end with an automated **Bedrock Model Evaluation** job. The assistant answers customer questions strictly from a provided FAQ, and explicitly says so when an answer isn't covered, instead of guessing.

> **Why this matters:** an FAQ assistant that "sounds right" but quietly makes things up when it doesn't know the answer is worse than no assistant at all. This project enforces a grounding rule at the prompt level (answer only from the FAQ, say so when the FAQ doesn't cover it), and verifies that rule holds by running a full evaluation pipeline against real, mixed answerable and unanswerable questions rather than trusting it on the strength of a few manual tests.

---

## Table of contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Part 1: Create the prompt template](#part-1-create-the-prompt-template)
- [Part 2: Configure credentials](#part-2-configure-credentials)
- [Part 3: Fill in the eval dataset and run the script](#part-3-fill-in-the-eval-dataset-and-run-the-script)
- [Part 4: Upload results to S3](#part-4-upload-results-to-s3)
- [Part 5: Run the Bedrock evaluation job](#part-5-run-the-bedrock-evaluation-job)
- [Part 6: Results](#part-6-results)
- [Cleanup](#cleanup)
- [Full troubleshooting index](#full-troubleshooting-index)

---

## Architecture

```
Customer question
      |
      v
+---------------------------------+
|  Bedrock Prompt Management        |   model: Amazon Nova Pro
|  (Converse API, versioned prompt) |   template vars: {{faq}}, {{customer_question}}
+----------------+------------------+
                 |
                 v
      Grounded answer, or an
      explicit "not in the FAQ"
      refusal when unsupported
                 |
                 v
+---------------------------------+
|  faq_assistant.py                 |   loops over eval questions,
|  writes eval_responses.jsonl      |   one record per Q/A pair
+----------------+------------------+
                 |
                 v
      S3 bucket (via CloudFormation)
                 |
                 v
+---------------------------------+
|  Bedrock Model Evaluation job     |   LLM as a judge, Correctness metric
|  (bring your own inference)       |
+---------------------------------+
```

**Components:**
- **A versioned Bedrock prompt** (`faq-assistant-prompt`), holding the grounding instructions and two template variables, `{{faq}}` and `{{customer_question}}`.
- **`faq_assistant.py`**, a single script that invokes the published prompt version once per eval question and writes results to `eval_responses.jsonl`.
- **A CloudFormation-provisioned S3 bucket** (`template.yaml`), used to stage the results file for evaluation.
- **A Bedrock Model Evaluation job**, using LLM as a judge with a single Correctness metric, scored against the "bring your own inference responses" dataset.

---

## Prerequisites

- An AWS account with Bedrock access in a region that supports **Prompt Management** (`us-east-1`, `us-east-2`, `us-west-2`, and others; notably **not** `us-west-1`)
- Python 3.10+
- `boto3`

Verify credentials before starting:

```bash
aws sts get-caller-identity
```

You should see your Account ID and ARN. If you see `NoCredentialsError` or `InvalidClientTokenId`, see [Part 2](#part-2-configure-credentials) before doing anything else.

---

## Part 1: Create the prompt template

Prompt Management is only available in a specific set of AWS regions. `us-west-1` (N. California) is not one of them, it silently doesn't show "Prompt management" in the Bedrock sidebar at all. Switch to a supported region (`us-east-1` used throughout this project) before starting.

In **Bedrock console > Prompt Management > Create prompt**:
1. Name the prompt (`faq-assistant-prompt`) and select **Amazon Nova Pro** as the model.
2. Write the prompt template with a role statement, an instruction to answer only from the FAQ, an instruction to say when the answer isn't available, and exactly two variables: `{{faq}}` and `{{customer_question}}`.
3. Test it in the built-in Test window with a real FAQ and two questions, one answerable and one not, to confirm the grounding rule actually holds before publishing.
4. Click **Create version** to publish an immutable **Version 1**. The draft ARN alone cannot be invoked from code, only a published version can.
5. Copy the version ARN (ends in `:1`) for use in the script.

**[Insert faq_5.png here — Prompts list showing faq-assistant-prompt]**

---

## Part 2: Configure credentials

Running `aws configure` and entering an Access Key ID plus Secret Access Key is the standard flow, but it silently fails for lab or training accounts that issue temporary STS credentials instead of permanent IAM user keys.

**The tell:** if your Access Key ID starts with `ASIA` (not `AKIA`), it's a temporary credential and requires a third value, a session token, that `aws configure`'s interactive prompt doesn't ask for.

Symptom if you skip it:

```
botocore.exceptions.NoCredentialsError: Unable to locate credentials
```

**Fix:** write all three values directly:

```bash
mkdir -p ~/.aws
cat > ~/.aws/credentials << 'EOF'
[default]
aws_access_key_id = ASIA...
aws_secret_access_key = <your secret key>
aws_session_token = <your session token>
EOF

cat > ~/.aws/config << 'EOF'
[default]
region = us-east-1
EOF
```

Verify:

```bash
aws sts get-caller-identity
```

> Note: temporary lab credentials typically expire after a few hours. If a previously working script suddenly throws a credentials error again later, refresh the session token from your lab panel and repeat this step. It isn't a code bug.

---

## Part 3: Fill in the eval dataset and run the script

`faq_assistant.py` holds the FAQ content, the eval dataset, and the invocation logic. Two details matter here:

1. **Use `converse`, not `invoke_model`.** `InvokeModel` and `InvokeModelWithResponseStream` only work on Prompt Management prompts configured for Anthropic Claude or Meta Llama models. Since this prompt is configured for **Amazon Nova Pro**, calling `invoke_model` on the prompt ARN throws a validation error. `converse` with a `promptVariables` argument is the correct call for any other model family.
2. **The eval dataset needs both answerable and unanswerable questions.** Answerable questions confirm the assistant retrieves and states facts correctly. Unanswerable questions (asking about something the FAQ genuinely doesn't cover, like a nonprofit discount or a refund policy) confirm the assistant refuses instead of guessing, which is the actual point of the exercise.

Run it:

```bash
python faq_assistant.py
```

**[Insert faq_2.png here — terminal output of all 7 Q/A pairs running cleanly]**

**[Insert faq_3.png here — eval_responses.jsonl contents]**

---

## Part 4: Upload results to S3

A small CloudFormation template (`template.yaml`) provisions a uniquely-named S3 bucket:

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: S3 bucket for storing FAQ assistant eval results

Resources:
  EvalBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub "faq-assistant-eval-${AWS::AccountId}"

Outputs:
  BucketName:
    Value: !Ref EvalBucket
    Description: Name of the S3 bucket for eval results
```

Deploy it and upload the results:

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name faq-assistant-eval \
  --region us-east-1

BUCKET=$(aws cloudformation describe-stacks --stack-name faq-assistant-eval \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" \
  --region us-east-1 \
  --output text)

aws s3 cp eval_responses.jsonl s3://$BUCKET/eval_responses.jsonl
```

**[Insert faq_1.png here — CloudFormation deploy and S3 upload output]**

---

## Part 5: Run the Bedrock evaluation job

In **Bedrock console > Evaluations > Create > Automatic: LLM as a judge**:
1. Select **Amazon Nova Pro** as the evaluator model.
2. Under **Inference source**, choose **Bring your own inference responses**, source name `faq-assistant`.
3. Under **Metrics**, select only **Correctness**.
4. Under **Datasets > Prompt dataset**, point to `s3://<bucket>/eval_responses.jsonl`.
5. Set an S3 output location in the same bucket.
6. Under IAM role permissions, choose **Create default role** (or reuse an existing one if you hit an `EntityAlreadyExistsException` from a prior attempt with the same auto-generated role name).
7. Click **Create**.

**[Insert faq_4.png here — Model evaluations list showing faq-assistant-eval-v1 as Completed]**

---

## Part 6: Results

The evaluation job scored all 7 prompts at a perfect **1.000 average Correctness**, meaning every answerable question got the right answer, and every unanswerable question correctly triggered a refusal instead of a guess.

**[Insert faq_6.png here — Correctness score chart, average 1.000]**

| Scenario | Question | Result |
|---|---|---|
| Answerable | "How much does the team plan cost?" | Passed, exact price and user cap. |
| Answerable | "Is there a free trial?" | Passed, correct length and no-credit-card detail. |
| Answerable | "What integrations do you support?" | Passed, correctly scoped to Slack and Google Workspace only. |
| Answerable | "Is live chat support available on the individual plan?" | Passed, correctly answered no while still surfacing what tier does get it. |
| Unanswerable | "Do you offer a discount for nonprofits or students?" | Passed, clean refusal, no guess. |
| Unanswerable | "Can I export my data to a CSV file?" | Passed, clean refusal, no guess. |
| Unanswerable | "What is your refund policy if I cancel mid-month?" | Passed, clean refusal, no guess. |

Because the score was already perfect, no prompt iteration (the optional Step 6 in the original exercise) was needed.

---

## Cleanup

Temporary AWS resources created during this project are cheap but not free. To tear them down:

```bash
aws s3 rm s3://faq-assistant-eval-<account-id> --recursive
aws cloudformation delete-stack --stack-name faq-assistant-eval --region us-east-1
```

Optionally, also delete the Bedrock evaluation job, the Bedrock prompt, and the auto-generated IAM role from their respective console pages if a fully clean account matters to you. None of these three carry an ongoing cost sitting idle.

---

## Full troubleshooting index

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | "Prompt management" missing from the Bedrock sidebar | `us-west-1` does not support Prompt Management | Switch to a supported region, `us-east-1` used throughout |
| 2 | `botocore.exceptions.NoCredentialsError: Unable to locate credentials` | No AWS credentials configured in the shell session | Write `~/.aws/credentials` and `~/.aws/config` directly, or run `aws configure` |
| 3 | `InvalidClientTokenId` or credential errors persist after configuring | Access key starts with `ASIA` (temporary STS credential) but no session token was set | Add `aws_session_token` alongside the access key and secret |
| 4 | `ValidationException` calling `invoke_model` on the prompt ARN | `InvokeModel` only supports prompts configured for Claude or Llama models, not Nova | Use `converse` with a `promptVariables` argument instead |
| 5 | `EntityAlreadyExistsException: Role with name ... already exists` | A prior evaluation job attempt already created the auto-generated IAM role | Reuse the existing role via "Use another role," or let the console regenerate a new name |
| 6 | Uncertainty about ongoing AWS cost after the exercise | Bedrock model invocations, evaluation jobs, and S3 storage are all billed, however small at this scale | Delete the S3 bucket contents and the CloudFormation stack when done |

---

## License

MIT (or update to match your course or organization's requirements).
