# Travel Planner: Amazon Bedrock Converse API

A tool-grounded travel planning assistant built directly on the **Amazon Bedrock Converse API**. Ask it *"I'll be in London this Saturday with my family, what should we do?"* and it calls a weather tool and an attractions tool before answering. Never from memory. If no data exists for a city or date, it says so plainly instead of inventing an answer.

> **Why this matters:** an LLM's training knowledge about a city's attractions or a coming weekend's forecast can be stale, wrong, or simply doesn't exist yet. This project enforces a hard rule at the system-prompt level (call the tools first, answer only from what they return), and verifies that rule holds even when the model is *tempted* to fall back on what it already "knows" (for example, when asked about a city with no data, like Paris).

---

## Table of contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Part 1: AWS account and Bedrock access](#part-1-aws-account-and-bedrock-access)
- [Part 2: Configure credentials (and the temporary-credential trap)](#part-2-configure-credentials-and-the-temporary-credential-trap)
- [Part 3: Project setup](#part-3-project-setup)
- [Part 4: Implementation](#part-4-implementation)
- [Part 5: Fixing the model ID](#part-5-fixing-the-model-id)
- [Part 6: Fixing the single-turn bug](#part-6-fixing-the-single-turn-bug)
- [Part 7: Fixing the hallucination loophole](#part-7-fixing-the-hallucination-loophole)
- [Part 8: Test results](#part-8-test-results)
- [Tool schemas reference](#tool-schemas-reference)
- [Full troubleshooting index](#full-troubleshooting-index)

---

## Architecture

```
User: "I'll be in London this Saturday with my family. What should we do?"
     │
     ▼
┌─────────────────────────────┐
│  Bedrock Converse API         │   model: Amazon Nova 2 Lite
│  (single boto3 client call)   │   system: SYSTEM_PROMPT (grounding rules)
└──────────────┬───────────────┘
               │ tool_use requests
               ▼
      ┌────────┴────────┐
      ▼                 ▼
 get_weather      get_top_attractions
 (local Python)   (local Python)
      │                 │
      ▼                 ▼
   WEATHER_DATA    ATTRACTIONS_DATA
   (mock dicts)     (mock dicts)
      │                 │
      └────────┬────────┘
               ▼
      Grounded final recommendation
```

**Components:**
- **`travel_planner.py`**: a single script, no separate infrastructure. Talks directly to `bedrock-runtime` via `boto3.client("bedrock-runtime").converse(...)`.
- **Two local tools**: `get_weather(city, date)` and `get_top_attractions(city)`, plain Python functions backed by in-memory mock dictionaries, executed locally whenever the model requests them.
- **No Gateway, no Lambda, no Harness.** Unlike an AgentCore-based agent, this project keeps orchestration entirely client-side in the Converse loop, which is appropriate for a single-tool, single-user exercise.

---

## Prerequisites

- An AWS account with Bedrock access in a region that supports Nova 2 (`us-east-1` used throughout)
- Python 3.10+
- `boto3`

Verify credentials before starting:

```bash
aws sts get-caller-identity
```

You should see your Account ID and ARN. If you see `InvalidClientTokenId`, see [Part 2](#part-2-configure-credentials-and-the-temporary-credential-trap) below before doing anything else.

---

## Part 1: AWS account and Bedrock access

Open **Amazon Bedrock** in the AWS Console, in a region that supports Nova 2 Lite (for example, `us-east-1`).

As of the current console, the old **Model access** page (where you used to manually request access per model) has been retired:

> "Model access page has been retired. Serverless foundation models are now automatically enabled across all AWS commercial regions when first invoked in your account. For models served from AWS Marketplace, a user with AWS Marketplace permissions must invoke the model once to enable it account-wide."

In practice this means:
- Most models (including Nova) activate automatically on first invocation. No manual request step is needed.
- **Anthropic models** may prompt for a one-time use-case submission on first use.
- **Marketplace-listed models** may need one account-wide invocation by a user with Marketplace permissions before others can use them.

Find your model in **Build > Model catalog**, note the exact model ID, and move on.

---

## Part 2: Configure credentials (and the temporary-credential trap)

Running `aws configure` and entering an Access Key ID plus Secret Access Key is the standard flow, but it silently fails for **lab or training accounts** (for example, AWS Academy or Vocareum) that issue temporary STS credentials instead of permanent IAM user keys.

**The tell:** if your Access Key ID starts with `ASIA` (not `AKIA`), it's a temporary credential and requires a **third value**, a session token, that `aws configure` has no prompt for.

Symptom if you skip it:

```
$ aws sts get-caller-identity
An error occurred (InvalidClientTokenId) when calling the GetCallerIdentity
operation: The security token included in the request is invalid.
```

**Fix:** set all three values, including the session token, using `aws configure set` (this also works when an editor like `nano` isn't available in a minimal container):

```bash
aws configure set aws_access_key_id ASIA...
aws configure set aws_secret_access_key <your secret key>
aws configure set aws_session_token <your session token>
aws configure set region us-east-1
aws configure set output json
```

Or write `~/.aws/credentials` directly:

```ini
[default]
aws_access_key_id = ASIA...
aws_secret_access_key = <your secret key>
aws_session_token = <your session token>
```

Verify:

```bash
aws sts get-caller-identity
```

> Note: temporary lab credentials typically expire after a few hours. If a previously working script suddenly throws `InvalidClientTokenId` again later, refresh the session token from your lab panel and repeat this step. It isn't a code bug.

---

## Part 3: Project setup

If your environment ships a pre-scaffolded starter (as in an AWS Academy IDE), there's usually a `requirements.txt` already listing the pinned dependencies:

```bash
pip install -r requirements.txt
```

Otherwise, set up from scratch:

```bash
mkdir travel-planner && cd travel-planner
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install boto3
```

---

## Part 4: Implementation

The script has three parts to fill in, matching the exercise's task breakdown:

**1. System prompt.** Instructs the model to never answer from memory, always call both tools first, and base recommendations strictly on tool output.

**2. Tool schemas.** JSON Schema definitions for `get_weather` (`city` and `date`, both required) and `get_top_attractions` (`city`, required), in Bedrock Converse's `toolSpec` / `inputSchema.json` format.

**3. Tool implementations.** `get_weather` and `get_top_attractions` do a lowercase-keyed dictionary lookup against mock data, with a graceful fallback (`"No data available"` or an empty list) when there's no match. This fallback path is what makes the grounding rule actually testable.

See [`travel_planner.py`](./travel_planner.py) for the full implementation.

---

## Part 5: Fixing the model ID

The starter script shipped with:

```python
MODEL_ID = "amazon.nova-lite-v1:0"
```

This is the **original** Nova Lite, not the model actually available in-account. The correct current model, confirmed via the Bedrock console and AWS's own Nova 2 documentation, is **Amazon Nova 2 Lite**, launched December 2, 2025, with a 1M-token context window and 64K max output tokens.

Critically, the bare model ID doesn't resolve directly in most regions. Nova 2 routes through **Cross-Region Inference (CRIS)** endpoints, which require a region prefix:

| Where you're calling from | Model ID to use |
|---|---|
| Any US region | `us.amazon.nova-2-lite-v1:0` |
| Outside the US, region-agnostic | `global.amazon.nova-2-lite-v1:0` |
| Outside the US, region-specific | `eu.amazon.nova-2-lite-v1:0` or `jp.amazon.nova-2-lite-v1:0` |

**Fix:**

```python
MODEL_ID = "us.amazon.nova-2-lite-v1:0"
```

If you see a `ValidationException` complaining the model identifier is invalid or unsupported for on-demand throughput, this prefix is almost always the missing piece.

---

## Part 6: Fixing the single-turn bug

The original `run_chat()` loop called `input()` exactly once, then ran the Converse loop to completion and exited. This works fine when the model has everything it needs in one message, but when the user's first message is under-specified (for example, "Help me plan a visit"), the model correctly asks a clarifying question (which city, which date, who's traveling) and the script exits immediately after printing it, dropping the user back to their shell prompt with no way to answer.

Symptom:

```
You: Help me plan a vist
Assistant: ...Could you please share this information...

starter student$ Paris
bash: Paris: command not found
```

The model behaved correctly. The script's control flow was the bug.

**Fix:** wrap the whole exchange in an outer `while True` loop that keeps prompting for the next `You:` input after every assistant reply, while an inner loop keeps resolving `tool_use` turns until the model reaches `end_turn`. See the `run_chat()` function in `travel_planner.py` for the corrected version.

---

## Part 7: Fixing the hallucination loophole

Testing the "no data" fallback path (asking about Paris, which isn't in the mock data) initially produced this:

```
Assistant: ...I don't have the necessary information... This could be
because the date is too far in the future for current data systems to
handle... For the best planning experience, I'd recommend:
- Disneyland Paris (great for families)
- Eiffel Tower and its surrounding parks
- Louvre Museum...
```

This is a genuine grounding failure, even though it technically obeyed the letter of "tell the user you don't have information." Two problems:
1. It speculated about why the data was missing (it wasn't; Paris was simply never added to the mock dictionaries) instead of admitting it doesn't know the reason.
2. It then fell back on training knowledge to list real Paris attractions anyway, exactly what the system prompt was supposed to prevent.

**Fix:** the system prompt needed to be explicit and exhaustive, not just directionally correct. Added:

```
Do NOT speculate about why the data is missing (e.g. do not claim it's
because the date is too far out or that there's a system issue). You
don't know the reason, so don't invent one. Do NOT then fall back on your
own general knowledge to suggest attractions or facts about that city
anyway. Simply state that no data is available for that city/date and
stop there. You may offer to help with a different city or date that IS
covered by the tools.
```

After the fix, the same Paris query produces:

```
Assistant: I don't have weather or attraction information available for
Paris on 2026-03-14. The tools returned no data for this date and city
combination. Would you like me to help you plan for a different date or
city instead?
```

No speculation, no fallback knowledge, full stop.

---

## Part 8: Test results

| Scenario | Prompt | Result |
|---|---|---|
| Rainy day, family | "I'll be in London on 2026-03-14 with my family. What should we do?" | Passed. Called both tools, led with indoor picks (British Museum, Natural History Museum), sequenced outdoor stops for the afternoon clearing, excluded nightlife. |
| Sunny day, family | "I'll be in London on 2026-03-15 with my kids. What should we do?" | Passed. Opened with Hyde Park given clear weather, offered indoor fallback, all family-friendly. |
| Rainy day, adults | "I'm in London on 2026-03-14 for a night out with friends. What do you suggest?" | Passed. Filtered strictly to `family_friendly: False` venues (Soho, Shoreditch, Comedy Store), excluded museums and the park. |
| Unsupported city | "I'll be in Paris on 2026-03-14 with my family. What should we do?" | Passed after the fix. Plainly stated no data available, no speculation, no fallback knowledge. |
| Multi-turn, repeated misses | Florida, then New York with no date, then New York City plus a date, all returning empty | Passed. Asked for missing specifics each time, admitted it had no way to know what cities are covered rather than guessing. |

---

## Tool schemas reference

<details>
<summary><code>get_weather</code></summary>

```json
{
  "toolSpec": {
    "name": "get_weather",
    "description": "Returns current weather conditions and forecast for a given city and date.",
    "inputSchema": {
      "json": {
        "type": "object",
        "properties": {
          "city": { "type": "string", "description": "The city to get weather for" },
          "date": { "type": "string", "description": "The date in YYYY-MM-DD format" }
        },
        "required": ["city", "date"]
      }
    }
  }
}
```

</details>

<details>
<summary><code>get_top_attractions</code></summary>

```json
{
  "toolSpec": {
    "name": "get_top_attractions",
    "description": "Returns a list of top-rated attractions in a given city.",
    "inputSchema": {
      "json": {
        "type": "object",
        "properties": {
          "city": { "type": "string", "description": "The city to get attractions for" }
        },
        "required": ["city"]
      }
    }
  }
}
```

</details>

---

## Full troubleshooting index

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Old "Model access" page shows a retirement notice | AWS moved to automatic serverless model enablement on first invocation | Use **Model catalog** instead. Invoke once to activate (Anthropic models may need a one-time use-case form; Marketplace models need one admin invocation). |
| 2 | `InvalidClientTokenId: The security token included in the request is invalid` | Access key starts with `ASIA` (temporary STS credential) but no session token was set | Add `aws_session_token` via `aws configure set` or directly in `~/.aws/credentials` |
| 3 | `bash: nano: command not found` | Minimal container or lab image has no text editor installed | Use `aws configure set ...` (no editor needed) or a heredoc (`cat > file << 'EOF' ... EOF`) instead |
| 4 | `ValidationException: model identifier is invalid` | Bare model ID (`amazon.nova-2-lite-v1:0`) used without the region routing prefix | Use `us.amazon.nova-2-lite-v1:0` (or `eu.` / `global.` outside the US) |
| 5 | Script exits after one exchange; typed text lands at the shell instead of the app | `run_chat()` only called `input()` once, before the Converse loop, with no outer loop | Wrap the exchange in an outer `while True` that re-prompts after every assistant reply |
| 6 | Model gives an unsupported-city answer that includes real attractions anyway | System prompt said "tell the user you don't have info" but didn't forbid falling back on general knowledge afterward | Explicitly forbid speculation about why data is missing, and forbid substituting training knowledge once a tool returns empty |

---

## License

MIT (or update to match your course or organization's requirements).
