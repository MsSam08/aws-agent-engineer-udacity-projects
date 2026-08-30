# Step-by-Step Implementation Guide
## Customer Support Chatbot with Amazon Bedrock AgentCore

**Note on project history:** this project was originally built on Bedrock Flows (Classifier prompt node + Condition node + branch handlers). Bedrock Agents Classic closed to new customers on July 30, 2026, and the course has since moved to **Amazon Bedrock AgentCore's managed harness**. If you built a Flow earlier, that work isn't part of the graded deliverable — everything below replaces it. You can leave the old Flow in the console or delete it during final cleanup.

---

## Prerequisites Check

```bash
aws sts get-caller-identity
aws configure get region          # must be us-east-1
python3 --version                 # 3.9+
```

**Model:** this project pins `us.amazon.nova-pro-v1:0` (a cross-region inference profile ID — note the `us.` prefix) everywhere. Don't substitute the harness's default model or the bare `amazon.nova-pro-v1:0` model ID — the former needs an AWS Marketplace subscription lab accounts can't complete, and the latter may hit an explicit IAM deny in some lab accounts.

**CLI version note:** if you're on AWS CLI v1 (`aws --version` shows `aws-cli/1.x`), drop any `--cli-binary-format` flags from commands you find elsewhere — that flag is v2-only.

**Clean up any stale resources from an earlier attempt.** If you deployed a `bug-report-tool-stack` before this course version existed, its resources won't match what the current `cloudformation-tool.yaml` expects (different Lambda payload shape, missing gateway/harness IAM roles). Delete it first:

```bash
aws cloudformation delete-stack --stack-name bug-report-tool-stack --region us-east-1
aws cloudformation wait stack-delete-complete --stack-name bug-report-tool-stack --region us-east-1
```

---

## Step 0: Get the Project Files

All files live in `project/starter/` in the project repository. Get that repo locally (clone or download per your course's instructions) and run every command from inside `project/starter/`.

```bash
cd project/starter/
pip install -r requirements.txt --break-system-packages   # boto3 1.43+
```

| File | Purpose |
|---|---|
| `cloudformation-tool.yaml` | DynamoDB table, `create_bug_report` Lambda, gateway + harness IAM roles |
| `cloudformation-testing.yaml` | Evaluation resources (S3 bucket + eval role) |
| `create_bug_report.py` | Lambda code (also embedded in the tool template) |
| `setup_gateway.py` | Creates the AgentCore Gateway, registers the Lambda as a tool |
| `system_prompt.txt` | **Your main deliverable** — the chatbot's system prompt |
| `create_harness.py` | Creates/updates the managed harness from `system_prompt.txt` |
| `chat.py` | Terminal chat client for manual testing |
| `online_shop_faq.md` | FAQ content, injected via `{{FAQ}}` placeholder |
| `harness-tests-template.json` | Copy this to build your test suite |
| `generate-eval-dataset.py` | Runs the harness against your test suite → JSONL |
| `cleanup_agentcore.py` | Tears down harness, gateway target, and gateway |

---

## Step 1: Deploy the Tool Stack and Create the Gateway

**1. Deploy DynamoDB + Lambda + IAM roles:**

```bash
aws cloudformation deploy \
  --template-file cloudformation-tool.yaml \
  --stack-name bug-report-tool-stack \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

`CAPABILITY_NAMED_IAM` is required because the template creates named IAM roles: a Lambda execution role, a **gateway role** (lets the Gateway invoke the Lambda), and a **harness role** (lets the harness call Bedrock models and invoke the gateway).

This creates, with fixed names:

| Resource | Name |
|---|---|
| DynamoDB table | `bug-report-tool-stack-bug-reports` |
| Lambda function | `bug-report-tool-stack-create-bug-report` |
| IAM role (Lambda) | `bug-report-tool-stack-lambda-role` |
| IAM role (Gateway) | `bug-report-tool-stack-gateway-role` |
| IAM role (Harness) | `bug-report-tool-stack-harness-role` |

**2. Create the gateway and register the tool:**

```bash
python setup_gateway.py
```

This reads the stack outputs itself (no manual copy-pasting) and saves everything later steps need into `agentcore_config.json`. The Lambda is registered behind a Gateway target named `bugreports`, so the model will see the tool as `bugreports___create_bug_report`.

⚠️ If this fails right after the stack finishes, with an access/validation error mentioning a role, that's IAM propagation delay. The script already retries — if it still fails, just run it again a minute later.

**3. Test the Lambda in isolation before wiring it into your prompt.**

In the Lambda console, open `bug-report-tool-stack-create-bug-report` → **Test** tab → create a new test event. The Gateway sends tool arguments as a **flat JSON object with no wrapper envelope** (this is different from Agents Classic's old `messageVersion`/`parameters` structure):

```json
{
    "description": "The checkout page crashes when I click the Pay button",
    "stepsToReproduce": "1. Add an item to the cart. 2. Go to checkout. 3. Click Pay.",
    "environment": "Chrome 120 on macOS Sonoma"
}
```

Click **Test**. You should get back a `ticketId` and `"status": "OPEN"`.

**4. Confirm the record landed in DynamoDB:**

```bash
aws dynamodb scan --table-name bug-report-tool-stack-bug-reports --region us-east-1
```

Or via console: DynamoDB → `bug-report-tool-stack-bug-reports` → **Explore table items**.

**Troubleshooting:**
- `AccessDeniedException` → check the IAM policy is attached to the correct execution role
- `ResourceNotFoundException` → verify the Lambda's `TABLE_NAME` env var matches `bug-report-tool-stack-bug-reports`
- The Lambda logs every event it receives to CloudWatch (`/aws/lambda/bug-report-tool-stack-create-bug-report`) — this is ground truth for what actually reached it if something looks wrong

---

## Step 2: Build the Harness — Design the System Prompt

This is the core of the project. **There are no condition nodes or classifiers anymore — the routing, information-gathering, and grounding behavior all live in one prompt.**

Open `system_prompt.txt` and write instructions covering all three request types:

- **Bug reports** — collect info conversationally, call `create_bug_report` once complete
- **Platform questions** — answer only from the embedded FAQ
- **Other requests** — politely redirect to human support by phone

### Bug report tool parameters (all required)

| Parameter | Description |
|---|---|
| `description` | Bug description, in the customer's words |
| `stepsToReproduce` | Steps to reproduce the issue |
| `environment` | Browser/OS/device |

Customers rarely give all three up front. Because the harness keeps session state across turns, your prompt just needs to tell the model to **ask for what's missing, one question at a time**, before calling the tool — and to relay the `ticketId` back to the customer afterward.

### Embed the FAQ

Keep the literal `{{FAQ}}` placeholder somewhere in `system_prompt.txt` — `create_harness.py` automatically substitutes it with the full contents of `online_shop_faq.md` at harness-creation time. You don't paste the FAQ in by hand.

### Suggested prompt structure

```
You are a customer support assistant for an online shop. Classify every
customer message into exactly one of these three categories before doing
anything else:

1. BUG_REPORT — the customer describes something on the site that isn't
   working correctly.
2. FAQ_QUESTION — the question is about orders, shipping, returns, payments,
   products, account, or privacy, and is answerable from the FAQ below.
3. OTHER — anything else.

## Handling BUG_REPORT
Collect all three of the following before calling create_bug_report:
- description (the bug, in the customer's own words)
- stepsToReproduce
- environment (browser/OS/device)
Ask for missing fields one at a time — do not ask for all three at once.
Do not call the tool until you have all three. After the tool succeeds,
tell the customer their ticket ID.

## Handling FAQ_QUESTION
Answer using only the FAQ below. If the FAQ doesn't cover the question,
tell the customer you don't have that information and direct them to
contact support.

## Handling OTHER
Politely explain this chatbot can't help with that request, and direct
the customer to contact human support by phone.

FAQ:
{{FAQ}}
```

### Create the harness and iterate

```bash
python create_harness.py     # first run: ~2-3 minutes
python chat.py                # each run = one fresh conversation
```

Iteration loop: edit `system_prompt.txt` → re-run `create_harness.py` (updates the existing harness in place) → start a new `chat.py` session. **No "prepare" step, nothing to redeploy** — changes apply as soon as `create_harness.py` finishes.

### Tips

- Treat routing as a classification problem: crisp category definitions → predictable routing. Vague definitions → vague routing.
- Explicitly forbid calling the tool until all three bug-report fields are collected.
- Tell the model what to do when the FAQ doesn't cover a question (the hand-off case) — don't leave this implicit.
- Watch for `[tool call] bugreports___create_bug_report` in `chat.py` output — if you never see it during a bug-report conversation, your prompt isn't clearly telling the model when to use the tool.
- Verify tickets really land in DynamoDB (not just that the model *says* it created one):
  ```bash
  aws dynamodb scan --table-name bug-report-tool-stack-bug-reports --region us-east-1
  ```

---

## Step 3: Testing and Evaluation

1. **Build your test suite** — copy `harness-tests-template.json` to `harness-tests.json`, fill in test cases covering all three routes (bug report, FAQ, other), plus edge cases:
   - A bug report with all three fields up front (single-turn)
   - A bug report missing fields, to exercise the multi-turn collection flow
   - An FAQ question actually covered by the doc
   - An FAQ question *not* covered by the doc (tests the hand-off case)
   - A clearly off-topic message
   - Optional (stand-out): ambiguous messages, very short messages, prompt injection attempts (e.g. "ignore your previous instructions")

2. **Run the harness against the test suite:**
   ```bash
   python generate-eval-dataset.py
   ```
   (Check `--help` for exact flags if they're not positional — this generates a JSONL file for Bedrock Evaluations.)

3. **Create a Bedrock Evaluations job** (LLM-as-a-judge) pointing at that JSONL output, per your course's Testing Framework page.

4. **Write observations** on the results — this is an explicit submission requirement, not optional. Look specifically at:
   - Did classification stay consistent across ambiguous/edge-case prompts?
   - Did the bug-report flow actually collect all three fields before calling the tool, across multi-turn conversations?
   - Did FAQ hand-off trigger correctly when the FAQ didn't cover a question?

---

## Submission Checklist

| Criterion | What's being checked |
|---|---|
| **Routing** | Every message routes to exactly one of the three behaviors, predictably across the test suite |
| **Bug Report Handling** | All three fields collected across the conversation before the tool is called; ticket ID relayed to customer; record actually exists in `bug-report-tool-stack-bug-reports` |
| **FAQ and Hand-Off Handling** | Platform questions answered only from the `{{FAQ}}`-embedded content; hand-off when FAQ doesn't cover it; off-topic requests get a polite phone-line redirect |
| **Testing and Evaluation** | `harness-tests.json` covers all three routes; `generate-eval-dataset.py` produces JSONL; a Bedrock Evaluations job is created; written observations are provided |

### Stand-out suggestions (optional)
- Edge-case test prompts: ambiguous messages, very short messages, prompt injection attempts
- Harden the system prompt against prompt injection (instructions that try to override it mid-conversation)
- Multi-turn bug-report tests scripted in `chat.py`, with DynamoDB fields checked against what the customer actually said
- Extend the FAQ with your own entries and confirm the chatbot picks them up after just re-running `create_harness.py` (no redeploy)

---

## Cleanup

```bash
python cleanup_agentcore.py       # deletes harness, gateway target, gateway
aws cloudformation delete-stack --stack-name bug-report-tool-stack --region us-east-1
aws cloudformation delete-stack --stack-name bug-report-testing-stack --region us-east-1
```

If you also built a Bedrock Flow earlier during the pre-pivot version of this project, delete it manually from the Bedrock console (Flows are not managed by these CloudFormation stacks).

---

## Quick Troubleshooting Reference

| Symptom | Likely cause |
|---|---|
| `setup_gateway.py` fails right after stack creation | IAM propagation delay — wait a minute, run again |
| Lambda test returns `AccessDeniedException` | IAM policy not attached to the correct execution role |
| Lambda test returns `ResourceNotFoundException` | `TABLE_NAME` env var doesn't match `bug-report-tool-stack-bug-reports` |
| `[tool call]` never appears in `chat.py` | System prompt doesn't clearly instruct the model when to call the tool |
| Ticket "created" per the chatbot but missing from DynamoDB | Trust the DB scan over the model's claim — check CloudWatch logs for the Lambda to see what it actually received |
| Nova Pro invoke fails with explicit IAM deny | Make sure you're using the inference profile ID `us.amazon.nova-pro-v1:0`, not the bare model ID `amazon.nova-pro-v1:0` |
| `Unknown options: --cli-binary-format` | You're on AWS CLI v1 — drop that flag, it's v2-only |
| Old `bug-report-tool-stack` resources have unexpected names/suffixes | You deployed the pre-AgentCore template — delete the stack and redeploy with the current `cloudformation-tool.yaml` from `project/starter/` |
