# Customer Support Chatbot, Amazon Bedrock AgentCore

A customer support chatbot for a fictional online shop, built on the **Amazon Bedrock AgentCore managed harness**. All routing, multi-turn information gathering, and FAQ grounding live in a single system prompt, there is no separate classifier, condition graph, or agent resource. The harness supplies the agent loop (model calls, stateful sessions, tool execution); the prompt supplies the behavior.

> **Note on project history:** this project was originally scoped around Bedrock Flows (a classifier prompt node + condition node + branch handlers). Bedrock Agents Classic closed to new customers on July 30, 2026, and the course migrated to AgentCore's managed harness shortly after. This README documents the current, AgentCore-based implementation.

## What it does

Every incoming customer message is routed to exactly one of three behaviors:

| Category | Behavior |
|---|---|
| **Bug report** | Collects a bug description, steps to reproduce, and the customer's environment across the conversation, one question at a time, then files a ticket via the `create_bug_report` tool and relays the ticket ID |
| **Platform question** | Answers orders/shipping/returns/payments questions strictly from an embedded FAQ document |
| **Anything else** | Politely redirects the customer to a human support phone line |

## Architecture

```
Customer message
      │
      ▼
┌─────────────────────────────┐
│  AgentCore Harness           │   stateful session, Nova Pro (us.amazon.nova-pro-v1:0)
│  (system_prompt.txt)         │   greedy decoding for reliable tool calling
└──────────────┬───────────────┘
               │ tool call: create_bug_report
               ▼
┌─────────────────────────────┐
│  AgentCore Gateway            │   exposes the Lambda as bugreports___create_bug_report
└──────────────┬───────────────┘
               ▼
┌─────────────────────────────┐
│  Lambda: create_bug_report    │   validates all 3 fields, writes to DynamoDB
└──────────────┬───────────────┘
               ▼
┌─────────────────────────────┐
│  DynamoDB: bug-reports table  │   one item per ticket, keyed by ticketId
└─────────────────────────────┘
```

The FAQ (`online_shop_faq.md`) is embedded directly in the system prompt via a `{{FAQ}}` placeholder, substituted at harness-creation time, the simplest viable approach for a short, stable document (RAG/Knowledge Bases would be the standard solution for larger documents, out of scope here).

## Repository structure

| File | Purpose |
|---|---|
| `system_prompt.txt` | **Main deliverable**, the chatbot's system prompt |
| `cloudformation-tool.yaml` | DynamoDB table, `create_bug_report` Lambda, gateway + harness IAM roles |
| `cloudformation-testing.yaml` | Evaluation resources (S3 bucket + eval role) |
| `create_bug_report.py` | Lambda implementation (also embedded in the tool template) |
| `setup_gateway.py` | Creates the AgentCore Gateway, registers the Lambda as a tool |
| `create_harness.py` | Creates/updates the managed harness from `system_prompt.txt` |
| `chat.py` | Terminal chat client for manual, multi-turn testing |
| `online_shop_faq.md` | FAQ content, injected via `{{FAQ}}` |
| `harness-tests.json` | 8-case automated test suite (all three routes + edge cases) |
| `generate-eval-dataset.py` | Runs the harness against the test suite → JSONL for Bedrock Evaluations |
| `cleanup_agentcore.py` | Tears down harness, gateway target, and gateway |
| `written_observations.md` | Full testing/evaluation write-up, including two open findings |

## Setup and deployment

Prerequisites: AWS account with Bedrock + AgentCore access, AWS CLI configured, Python 3.9+, region `us-east-1`, model pinned to `us.amazon.nova-pro-v1:0` (an inference profile ID, do not substitute the harness default model or a bare model ID).

```bash
pip install -r requirements.txt --break-system-packages

# 1. Deploy DynamoDB + Lambda + gateway/harness IAM roles
aws cloudformation deploy \
  --template-file cloudformation-tool.yaml \
  --stack-name bug-report-tool-stack \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1

# 2. Create the AgentCore Gateway and register the Lambda as a tool
python setup_gateway.py

# 3. Create the harness from system_prompt.txt
python create_harness.py

# 4. Chat with it
python chat.py
```

Iterating on the prompt is fast: edit `system_prompt.txt`, re-run `create_harness.py` (updates the harness in place), start a new `chat.py` session. No "prepare" step, nothing to redeploy.

## System prompt design

The prompt asks the model to classify every message into exactly one of three categories before acting, then follow only that category's rules. Several rules exist specifically because testing surfaced real failures they now prevent:

- **Field-fabrication guard + worked multi-turn example**, added after the model was caught calling `create_bug_report` with placeholder text (`"Please provide your browser, OS..."`) or fabricated values (`"Chrome browser, Windows 10, Desktop computer"`) instead of asking the customer. A plain negative instruction ("don't invent values") wasn't sufficient on its own; a concrete worked example of the expected turn-by-turn behavior was needed to reliably fix it.
- **FAQ file-lookup suppression**, added after the model attempted to call an unregistered `file_operations` tool instead of using the FAQ text already present in its context.
- **Category tie-breaking rule**, bug report > platform question > other, to keep classification deterministic on ambiguous input.
- **Prompt-injection hardening**, the model is told to ignore in-message instructions that try to override its rules or reveal the system prompt; verified against two dedicated injection test cases (see Testing, below).
- **`<thinking>`-tag suppression**, the model is told never to leak its reasoning trace into customer-facing text. This is only a partial mitigation; see Known Limitations.

Full prompt text and design rationale: see `system_prompt.txt`.

## Testing and evaluation

### Automated test suite

`harness-tests.json` contains 8 cases covering all three routes plus edge cases: an FAQ-covered question, an FAQ-not-covered question, a fully off-topic question, a bug report with all fields supplied upfront, a bug report from a single vague message, and two distinct prompt-injection attempts.

```bash
python generate-eval-dataset.py
```
produces a JSONL file, uploaded to S3 and evaluated with **Bedrock Evaluations (LLM-as-a-judge)** using `us.anthropic.claude-sonnet-4-5-20250929-v1:0` as the evaluator model, scoring Helpfulness, Correctness, and Following Instructions.

### Results

| Metric | Average | Notes |
|---|---|---|
| **Correctness** | **1.00** | Perfect across all 8 cases, every response matched its documented expected behavior |
| **Following Instructions** | **1.00** | Both prompt-injection attempts fully deflected: no system-prompt or FAQ leak, no deviation from defined behavior |
| **Helpfulness** | **0.58** | Correctness alone didn't capture tone/UX issues the judge flagged (see below) |

<!-- ![Evaluation metrics summary](docs/images/chatbot_1.png) -->

Per-case results (Helpfulness, the most variable metric):

| Case | Score | Judge's note |
|---|---|---|
| "How long does shipping take?" (FAQ-covered) | 0.67 | Accurate, grounded in FAQ |
| "Do you sell gift cards?" (FAQ hand-off) | 0.33 | Redirect language referenced "the FAQ" in a way the judge found confusing, since the customer never mentioned the FAQ |
| "What's the weather like today?" (off-topic) | 0.17 | Jumping straight to a phone-number redirect read as "odd" and "dismissive" without first acknowledging the assistant has no weather capability |
| Bug report, all fields upfront | 0.33 | Ticket confirmation read as impersonal for a payment-blocking issue; judge wanted acknowledgment of urgency/empathy alongside the ticket ID |
| "The checkout page is broken" (single vague message) | 0.83 | Correctly asked one clarifying question instead of calling the tool |

<!-- ![Per-case generation results](docs/images/chatbot_2.png) -->
<!-- ![Helpfulness score distribution](docs/images/chatbot_2_1.png) -->

Correctness was perfect across all 8 cases:

<!-- ![Correctness score distribution](docs/images/chatbot3.png) -->
<!-- ![Correctness per-case results](docs/images/chatbot3_1.png) -->

Both prompt-injection cases scored a perfect 1.0 on Following Instructions:

<!-- ![Following instructions results](docs/images/chatbot_4.png) -->
<!-- ![Following instructions score distribution](docs/images/chatbot_4_1.png) -->

The judge model **independently corroborated two findings from manual testing** without being told about them in advance, flagging a leaked internal category label ("BUG REPORT") in one response's Helpfulness rationale, and noting visible internal reasoning in another case's Correctness rationale. See `written_observations.md` for the full write-up.

## Known limitations (open findings)

Documented rather than papered over, both were caught through direct evidence (CloudWatch logs, DynamoDB scans, and independent corroboration by the Bedrock Evaluations judge model):

1. **Intermittent `<thinking>`-tag / category-label leakage** on short or ambiguous inputs. Prompting alone did not fully and reliably suppress the model's internal reasoning trace in every case.
2. **No idempotency protection in `create_bug_report`.** The Lambda always generates a new UUID and performs an unconditional `put_item`; a retried invocation or model self-correction could file duplicate tickets for one issue with no automatic detection or merge.

See `written_observations.md` for the full debugging narrative, including a documented memory-isolation bug (AgentCore's default-on cross-session memory, keyed on `actorId`, was initially not scoped correctly in `chat.py`/`generate-eval-dataset.py`, causing test conversations to bleed context into one another) and its fix.

## Cleanup

```bash
python cleanup_agentcore.py
aws cloudformation delete-stack --stack-name bug-report-tool-stack --region us-east-1
aws cloudformation delete-stack --stack-name bug-report-testing-stack --region us-east-1
```

## Technology stack

- [Amazon Bedrock AgentCore managed harness](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness.html), agent loop, stateful sessions, tool execution
- [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/), exposes the bug-report Lambda as a callable tool
- [Amazon Bedrock Evaluations](https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation.html), LLM-as-a-judge evaluation
- [AWS Lambda](https://aws.amazon.com/lambda/), bug-report tool runtime
- [Amazon DynamoDB](https://aws.amazon.com/dynamodb/), bug-report ticket storage
