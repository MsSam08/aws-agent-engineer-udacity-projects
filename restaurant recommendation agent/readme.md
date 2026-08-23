# Restaurant Recommendation Agent  -  Amazon Bedrock AgentCore

A restaurant recommendation agent built on **Amazon Bedrock AgentCore**, backed by three AWS Lambda functions and orchestrated through an **AgentCore Gateway** and **Harness**. Ask it *"Find me an Italian restaurant for tonight"* and it searches restaurants by cuisine, checks live availability, and returns a recommendation grounded entirely in tool results  -  never invented.

> **Why AgentCore and not Bedrock Agents?** This project was originally scoped for the classic point-and-click Bedrock Agents builder. Partway through, it turned out that builder ("Agents Classic") is now in maintenance mode and closed to new accounts. This README documents the full pivot to AgentCore, including every platform issue hit along the way and how each was fixed  -  step by step, with the actual commands and code used.

---

## Table of contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Part 1  -  Deploy the Lambda functions](#part-1--deploy-the-lambda-functions)
- [Part 2  -  Discover Bedrock Agents Classic is unavailable](#part-2--discover-bedrock-agents-classic-is-unavailable)
- [Part 3  -  Create the AgentCore Gateway](#part-3--create-the-agentcore-gateway)
- [Part 4  -  Add Lambda targets to the Gateway](#part-4--add-lambda-targets-to-the-gateway)
- [Part 5  -  Fix Lambda invoke permissions](#part-5--fix-lambda-invoke-permissions)
- [Part 6  -  Fix the Lambda code (Classic format → Gateway format)](#part-6--fix-the-lambda-code-classic-format--gateway-format)
- [Part 7  -  Create the Harness (the agent)](#part-7--create-the-harness-the-agent)
- [Part 8  -  Debug the Nova Pro tool-calling bug](#part-8--debug-the-nova-pro-tool-calling-bug)
- [Part 9  -  Test the working agent](#part-9--test-the-working-agent)
- [Tool schemas reference](#tool-schemas-reference)
- [Full troubleshooting index](#full-troubleshooting-index)
- [Cleanup](#cleanup)

---

## Architecture

```
User: "Find me an Italian restaurant for tonight."
     │
     ▼
┌─────────────────────────────┐
│   AgentCore Harness          │   model: Claude
│   (agent runtime)             │   instructions: system prompt below
└──────────────┬───────────────┘
               │ tool calls (MCP)
               ▼
┌─────────────────────────────┐
│   AgentCore Gateway           │   inbound auth: IAM (SigV4)
└──────────────┬───────────────┘
               │
     ┌─────────┼─────────┐
     ▼         ▼         ▼
 get-cuisines  search-   get-
 (Lambda)      restaurants availability
               (Lambda)   (Lambda)
     │         │         │
     ▼         ▼         ▼
        Grounded final recommendation
```

**Components:**
- **3 Lambda functions**  -  deployed via CloudFormation, hold the actual restaurant data and logic
- **AgentCore Gateway**  -  exposes the Lambdas as MCP tools over a single managed endpoint
- **AgentCore Harness**  -  the agent itself: model + system prompt + Gateway connection, fully managed (no orchestration code)

---

## Prerequisites

- AWS CLI v2 configured with credentials that can deploy CloudFormation, manage Lambda, and use Bedrock/AgentCore
- Region: `us-east-1`
- `template.yaml` (CloudFormation template defining the three Lambda functions)  -  see [`/template.yaml`](./template.yaml)

Verify your credentials before starting:

```bash
aws sts get-caller-identity --region us-east-1
```

You should see your Account ID and ARN returned. If this fails with `InvalidClientTokenId`, refresh your credentials before continuing.

---

## Part 1  -  Deploy the Lambda functions

The provided CloudFormation template creates three Lambda functions (`get-cuisines`, `search-restaurants`, `get-availability`) and grants baseline permissions.

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name restaurant-agent \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

Once it completes, pull the Lambda ARNs from the stack outputs  -  you'll need these throughout:

```bash
aws cloudformation describe-stacks \
  --stack-name restaurant-agent \
  --region us-east-1 \
  --query "Stacks[0].Outputs"
```

```json
[
    {
        "OutputKey": "GetAvailabilityFunctionArn",
        "OutputValue": "arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:get-availability"
    },
    {
        "OutputKey": "SearchRestaurantsFunctionArn",
        "OutputValue": "arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:search-restaurants"
    },
    {
        "OutputKey": "GetCuisinesFunctionArn",
        "OutputValue": "arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:get-cuisines"
    }
]
```

---

## Part 2  -  Discover Bedrock Agents Classic is unavailable

Opening **Bedrock console → Agents** (the originally-planned path) revealed no "Agents" item under Build  -  only **AgentCore** and **Guardrails**. Navigating directly to the Agents Classic page surfaced this banner:

> ⚠️ **Bedrock Agents is in Maintenance Mode.** New agent creation is not available for accounts without prior service usage.
>
> **Agents Classic will no longer be open to new customers starting on July 30, 2026.** Amazon Bedrock Agents (launched Nov 2023) is now "Amazon Bedrock Agents Classic" and will no longer be open to new customers starting AgentCore.



**Decision:** build on **AgentCore** (Gateway + Harness) instead, which is what AWS now points new accounts toward for equivalent capability.

---

## Part 3  -  Create the AgentCore Gateway

Navigate to **Bedrock console → AgentCore → Gateways → Create gateway**.

1. **Name:** `restaurant-agent-gateway`
2. **Inbound Auth type:** initially tried **"Quick create configurations with Cognito"** (the recommended default)  -  this failed:

   ```
   Failed to create Cognito resources: User: arn:aws:sts::<ACCOUNT_ID>:assumed-role/...
   is not authorized to perform: cognito-idp:CreateResourceServer on resource:
   arn:aws:cognito-idp:us-east-1:<ACCOUNT_ID>:userpool/... because no identity-based
   policy allows the cognito-idp:CreateResourceServer action
   ```



3. **Fix:** switched Inbound Auth type to **"Use IAM permissions"** instead:

   > *"This gateway will perform authentication and authorization using AWS Signature Version 4 (SigV4). No further configurations are necessary."*

   This avoids provisioning any Cognito resources entirely  -  no extra IAM permissions needed, and it's the simpler choice for single-user/lab use anyway.

   > Gateway created
   > ![Gateway](https://github.com/MsSam08/aws-agent-engineer-udacity-projects/blob/main/restaurant%20recommendation%20agent/udacity%202.png)

4. Saved the gateway with no targets yet (added next).

---

## Part 4  -  Add Lambda targets to the Gateway

Each Lambda becomes a separate **target** on the Gateway. For each target:

- **Target protocol:** MCP target
- **Target type:** Lambda ARN
- **Target schema:** Define an inline schema (JSON Schema describing the tool)

### Target 1  -  `get-cuisines-target`

```json
[
  {
    "name": "get_cuisines",
    "description": "Returns the list of cuisine types available",
    "inputSchema": {
      "type": "object",
      "properties": {},
      "required": []
    }
  }
]
```

### Target 2  -  `search-restaurants-target`

```json
[
  {
    "name": "search_restaurants",
    "description": "Searches for restaurants. Returns all restaurants if no cuisine is specified.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "cuisine": {
          "type": "string",
          "description": "The cuisine type (e.g. Italian, Japanese). If omitted, all are returned."
        }
      },
      "required": []
    }
  }
]
```

### Target 3  -  `get-availability-target`

```json
[
  {
    "name": "get_availability",
    "description": "Checks whether a specific restaurant has availability for tonight.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "restaurant_id": {
          "type": "string",
          "description": "The unique ID of the restaurant"
        }
      },
      "required": ["restaurant_id"]
    }
  }
]
```

First attempt at creating all three at once produced a mixed result:

```
restaurant-agent-gateway-xxxx successfully created, there was an error in creating 3/3 target(s).:
get-cuisines-target: Gateway execution role lacks permission to invoke Lambda function
arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:get-cuisines. Update the permission and retry;
search-restaurants-target: Gateway execution role lacks permission to invoke Lambda function ...;
get-availability-target: 1 validation error detected: Value at 'name' failed to satisfy
constraint: Member must satisfy regular expression pattern: ([0-9a-zA-Z][-]?){1,100}
```

Two distinct problems surfaced here  -  fixed in Parts 5 and 6.

---

## Part 5  -  Fix Lambda invoke permissions

The Gateway has its own **execution role**, separate from any IAM role your Lambdas already trust. Each Lambda needs a resource-based policy explicitly granting that role `lambda:InvokeFunction`.

**Find the Gateway execution role ARN** from the Gateway's detail page in the console (labeled "Service role" or similar):

```
arn:aws:iam::<ACCOUNT_ID>:role/service-role/AmazonBedrockAgentCoreGatewayDefaultServiceRole<...>
```

**Grant invoke permission on all three functions:**

```bash
aws lambda add-permission \
  --function-name get-cuisines \
  --statement-id GatewayInvoke \
  --action lambda:InvokeFunction \
  --principal "arn:aws:iam::<ACCOUNT_ID>:role/service-role/AmazonBedrockAgentCoreGatewayDefaultServiceRole<...>" \
  --region us-east-1

aws lambda add-permission \
  --function-name search-restaurants \
  --statement-id GatewayInvoke \
  --action lambda:InvokeFunction \
  --principal "arn:aws:iam::<ACCOUNT_ID>:role/service-role/AmazonBedrockAgentCoreGatewayDefaultServiceRole<...>" \
  --region us-east-1

aws lambda add-permission \
  --function-name get-availability \
  --statement-id GatewayInvoke \
  --action lambda:InvokeFunction \
  --principal "arn:aws:iam::<ACCOUNT_ID>:role/service-role/AmazonBedrockAgentCoreGatewayDefaultServiceRole<...>" \
  --region us-east-1
```

**Verify each permission landed:**

```bash
aws lambda get-policy --function-name get-cuisines --region us-east-1
aws lambda get-policy --function-name search-restaurants --region us-east-1
aws lambda get-policy --function-name get-availability --region us-east-1
```

Each policy should now contain a statement with `"Sid": "GatewayInvoke"`:

```json
{
  "Sid": "GatewayInvoke",
  "Effect": "Allow",
  "Principal": {
    "AWS": "arn:aws:iam::<ACCOUNT_ID>:role/service-role/AmazonBedrockAgentCoreGatewayDefaultServiceRole<...>"
  },
  "Action": "lambda:InvokeFunction",
  "Resource": "arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:get-cuisines"
}
```

> 📸 *Screenshot: `get-policy` output showing the `GatewayInvoke` statement*

---

## Part 6  -  Fix the Lambda code (Classic format → Gateway format)

The permission fix alone wasn't enough. Inspecting the actual deployed Lambda code revealed it was written for **Bedrock Agents Classic's** invocation contract, not AgentCore Gateway's  -  a completely different input/output shape.

**Pull the deployed code to inspect it:**

```bash
curl -o /tmp/get-availability.zip \
  "$(aws lambda get-function --function-name get-availability --query 'Code.Location' --output text --region us-east-1)"

python3 -c "
import zipfile
with zipfile.ZipFile('/tmp/get-availability.zip') as z:
    z.extractall('/tmp/get-availability-src')
"

cat /tmp/get-availability-src/index.py
```

**Original code (Classic format  -  broken under Gateway):**

```python
import json

AVAILABILITY = {
    "r1": True, "r2": False, "r3": True, "r4": False,
    "r5": True, "r6": True, "r7": False, "r8": True,
}

def lambda_handler(event, context):
    parameters = {p["name"]: p["value"] for p in event.get("parameters", [])}
    restaurant_id = parameters.get("restaurant_id", "")
    available = AVAILABILITY.get(restaurant_id, False)
    result = {"restaurant_id": restaurant_id, "available": available}
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event["actionGroup"],
            "function": event["function"],
            "functionResponse": {
                "responseBody": {"TEXT": {"body": json.dumps(result)}}
            },
        },
    }
```

Two mismatches with what Gateway actually sends/expects:

| | Bedrock Agents Classic | AgentCore Gateway |
|---|---|---|
| **Input** | `event["parameters"]` → list of `{"name": ..., "value": ...}` | Flat object matching the tool schema directly, e.g. `{"restaurant_id": "r3"}` |
| **Output** | Wrapped: `{"messageVersion": ..., "response": {"functionResponse": {"responseBody": {"TEXT": {"body": ...}}}}}` | Plain JSON-serializable value: `{"restaurant_id": ..., "available": ...}` |

**Fixed code:**

```python
import json

AVAILABILITY = {
    "r1": True, "r2": False, "r3": True, "r4": False,
    "r5": True, "r6": True, "r7": False, "r8": True,
}

def lambda_handler(event, context):
    restaurant_id = event.get("restaurant_id", "")
    available = AVAILABILITY.get(restaurant_id, False)
    return {
        "restaurant_id": restaurant_id,
        "available": available,
    }
```

**Deploy the fix:**

```bash
cd /tmp/get-availability-src
cat > index.py << 'EOF'
import json

AVAILABILITY = {
    "r1": True, "r2": False, "r3": True, "r4": False,
    "r5": True, "r6": True, "r7": False, "r8": True,
}

def lambda_handler(event, context):
    restaurant_id = event.get("restaurant_id", "")
    available = AVAILABILITY.get(restaurant_id, False)
    return {
        "restaurant_id": restaurant_id,
        "available": available,
    }
EOF

python3 -c "
import zipfile
with zipfile.ZipFile('get-availability-fixed.zip', 'w') as z:
    z.write('index.py')
"

aws lambda update-function-code \
  --function-name get-availability \
  --zip-file fileb://get-availability-fixed.zip \
  --region us-east-1
```

The same Classic-wrapper pattern was found in **all three** Lambdas and fixed the same way. Full corrected source for each:

<details>
<summary><code>get-cuisines/index.py</code> (fixed)</summary>

```python
import json

RESTAURANTS = [
    {"id": "r1", "name": "Trattoria Bella", "cuisine": "Italian",  "rating": 4.6},
    {"id": "r2", "name": "Osteria Romana",  "cuisine": "Italian",  "rating": 4.4},
    {"id": "r3", "name": "Sakura Garden",   "cuisine": "Japanese", "rating": 4.7},
    {"id": "r4", "name": "Ramen Yuki",      "cuisine": "Japanese", "rating": 4.9},
    {"id": "r5", "name": "El Mercado",      "cuisine": "Mexican",  "rating": 4.3},
    {"id": "r6", "name": "Spice Route",     "cuisine": "Indian",   "rating": 4.6},
    {"id": "r7", "name": "Le Bistro",       "cuisine": "French",   "rating": 4.8},
    {"id": "r8", "name": "The Grill House", "cuisine": "American", "rating": 4.2},
]

def lambda_handler(event, context):
    cuisines = sorted(set(r["cuisine"] for r in RESTAURANTS))
    return {"cuisines": cuisines}
```

</details>

<details>
<summary><code>search-restaurants/index.py</code> (fixed)</summary>

```python
import json

RESTAURANTS = [
    {"id": "r1", "name": "Trattoria Bella", "cuisine": "Italian",  "rating": 4.6},
    {"id": "r2", "name": "Osteria Romana",  "cuisine": "Italian",  "rating": 4.4},
    {"id": "r3", "name": "Sakura Garden",   "cuisine": "Japanese", "rating": 4.7},
    {"id": "r4", "name": "Ramen Yuki",      "cuisine": "Japanese", "rating": 4.9},
    {"id": "r5", "name": "El Mercado",      "cuisine": "Mexican",  "rating": 4.3},
    {"id": "r6", "name": "Spice Route",     "cuisine": "Indian",   "rating": 4.6},
    {"id": "r7", "name": "Le Bistro",       "cuisine": "French",   "rating": 4.8},
    {"id": "r8", "name": "The Grill House", "cuisine": "American", "rating": 4.2},
]

def lambda_handler(event, context):
    cuisine = event.get("cuisine", "").lower()
    if cuisine:
        restaurants = [r for r in RESTAURANTS if r["cuisine"].lower() == cuisine]
        if not restaurants:
            return {"error": f"No {cuisine.title()} restaurants found."}
        return {"restaurants": restaurants}
    return {"restaurants": RESTAURANTS}
```

</details>

**Verify all three deployments succeeded:**

```bash
aws lambda get-function --function-name get-cuisines --query 'Configuration.LastUpdateStatus' --output text --region us-east-1
aws lambda get-function --function-name search-restaurants --query 'Configuration.LastUpdateStatus' --output text --region us-east-1
aws lambda get-function --function-name get-availability --query 'Configuration.LastUpdateStatus' --output text --region us-east-1
```

Each should return `Successful`.

**Re-add the three targets in the Gateway console**  -  this time all three succeed and show status **Ready**:

> Targets created
> ![Targets](https://github.com/MsSam08/aws-agent-engineer-udacity-projects/blob/main/restaurant%20recommendation%20agent/udacity%203.png)

---

## Part 7  -  Create the Harness (the agent)

Navigate to **Bedrock console → AgentCore → Harness → Create harness**.

- **Model:** Nova Pro *(see [Part 8](#part-8--debug-the-nova-pro-tool-calling-bug) for why this changed to Claude)*
- **Instructions:**

  ```
  You are a restaurant recommendation assistant. Your job is to help users
  find a restaurant that fits what they're asking for (cuisine, availability,
  etc.).

  Rules you must follow:
  1. Always use your available tools to gather information before making any
     recommendation. Never guess or rely on prior knowledge about specific
     restaurants, cuisines, or availability.
  2. When a user asks for a type of cuisine, first confirm it against the
     list of available cuisines if there's any ambiguity.
  3. Use the search tool to find candidate restaurants matching the user's
     criteria.
  4. Before recommending a specific restaurant for "tonight" or a specific
     time, check its availability using the availability tool. Do not
     recommend a restaurant without confirming it has availability.
  5. Base your final recommendation strictly on the tool results you
     received in this conversation  -  do not fabricate restaurant names,
     cuisines, or availability.
  6. If no restaurants match or none have availability, tell the user
     clearly rather than inventing an alternative.
  7. Keep responses concise and focused on the recommendation and the
     reasoning drawn from tool results.
  ```

- **Tools:** Add tools → **Connect a Gateway** → select `restaurant-agent-gateway`

> Harness created
> ![Harness](https://github.com/MsSam08/aws-agent-engineer-udacity-projects/blob/main/restaurant%20recommendation%20agent/udacity%201.png)

> **Note:** if you create the Harness before attaching the Gateway (easy to do), just edit the Harness afterward and add the Gateway under its Tools section  -  no need to delete and recreate.

---

## Part 8  -  Debug the Nova Pro tool-calling bug

First test against Nova Pro failed immediately, every time, at the exact same point  -  right as the model finished reasoning and tried to invoke a tool:

```
<thinking> To find an Italian restaurant for tonight, I first need to get the
list of available Italian restaurants. Error: An error occurred
(modelStreamErrorException) when calling the ConverseStream operation:
Model produced invalid sequence as part of ToolUse. Please refer to the
model tool use troubleshooting guide.
```

> Model Error
> ![Error](https://github.com/MsSam08/aws-agent-engineer-udacity-projects/blob/main/restaurant%20recommendation%20agent/udacity%206.png)

**Fixes attempted, in order:**

1. **Set `temperature=0` and raised max tokens** in the Harness's inference configuration  -  AWS's own Nova troubleshooting guide recommends this as it improves tool-call reliability via greedy decoding. Did not resolve it.
2. **Simplified the system prompt** to reduce multi-step chain-of-thought reasoning before tool calls. Did not resolve it  -  same error, shorter `<thinking>` block.
3. **Retried multiple times.** Failed consistently, ruling out a purely transient/non-deterministic cause.
4. **Switched the Harness's model to Claude.** ✅ Resolved immediately  -  no further `modelStreamErrorException` on any subsequent call.

This matches independently reported issues with Nova models' tool-calling behavior under the Converse API streaming interface (reproduced across other SDKs, e.g. LangChain, Strands)  -  a platform-level limitation, not a configuration mistake.

---

## Part 9  -  Test the working agent

With Claude set as the Harness model, sent the test prompt:

```
Find me an Italian restaurant for tonight.
```

**Trace  -  `search_restaurants` call:**

```json
// Input
{ "cuisine": "Italian" }

// Output
{
  "restaurants": [
    { "id": "r1", "name": "Trattoria Bella", "cuisine": "Italian", "rating": 4.6 },
    { "id": "r2", "name": "Osteria Romana",  "cuisine": "Italian", "rating": 4.4 }
  ]
}
// Status: ✅ Success
```

> Agent response
> ![Chat](https://github.com/MsSam08/aws-agent-engineer-udacity-projects/blob/main/restaurant%20recommendation%20agent/udaacity%205.png)

The agent then reasoned about the results and moved on to check availability:

> *"I found 2 Italian restaurants. Now let me check their availability for tonight."*


> **Note:** at this point in testing, the next model call intermittently hit `AccessDeniedException: Model access is denied due to IAM user or service role is not authorized to perform the required AWS Marketplace actions`. This is a one-time-per-account Marketplace subscription enablement quirk (see AWS's [model access documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html))  -  resolvable by retrying after a short wait, or by an administrator granting `aws-marketplace:ViewSubscriptions` / `aws-marketplace:Subscribe` to the role.

---

## Tool schemas reference

<details>
<summary><code>get_cuisines</code></summary>

```json
[
  {
    "name": "get_cuisines",
    "description": "Returns the list of cuisine types available",
    "inputSchema": {
      "type": "object",
      "properties": {},
      "required": []
    }
  }
]
```

</details>

<details>
<summary><code>search_restaurants</code></summary>

```json
[
  {
    "name": "search_restaurants",
    "description": "Searches for restaurants. Returns all restaurants if no cuisine is specified.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "cuisine": {
          "type": "string",
          "description": "The cuisine type (e.g. Italian, Japanese). If omitted, all are returned."
        }
      },
      "required": []
    }
  }
]
```

</details>

<details>
<summary><code>get_availability</code></summary>

```json
[
  {
    "name": "get_availability",
    "description": "Checks whether a specific restaurant has availability for tonight.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "restaurant_id": {
          "type": "string",
          "description": "The unique ID of the restaurant"
        }
      },
      "required": ["restaurant_id"]
    }
  }
]
```

</details>

---

## Full troubleshooting index

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | No "Agents" in Bedrock console sidebar / "Maintenance Mode" banner | Bedrock Agents Classic closed to new accounts (July 30, 2026) | Build on AgentCore (Gateway + Harness) |
| 2 | `Failed to create Cognito resources: ... not authorized to perform cognito-idp:CreateResourceServer` | Sandbox IAM role lacks Cognito provisioning permissions | Set Gateway Inbound Auth to **"Use IAM permissions"** instead of Cognito quick-create |
| 3 | `Gateway execution role lacks permission to invoke Lambda function` | No resource policy granting the Gateway's execution role `lambda:InvokeFunction` | `aws lambda add-permission` with the Gateway's role ARN as principal, for each Lambda |
| 4 | Target creation validation error: `Value at 'name' failed to satisfy constraint` | Transient / resolved on target re-creation | Delete and re-add the failing target |
| 5 | Tool calls return malformed/empty data despite target showing "Ready" | Lambda code written for Bedrock Agents Classic's wrapped input/output format, incompatible with Gateway's flat format | Rewrite handlers: read `event.get("<param>")` directly, `return {...}` directly (no envelope) |
| 6 | `modelStreamErrorException: Model produced invalid sequence as part of ToolUse` | Known Nova Pro limitation in tool-calling under Harness/Converse API | Switch Harness model to Claude |
| 7 | `AccessDeniedException: ... AWS Marketplace actions (aws-marketplace:ViewSubscriptions, aws-marketplace:Subscribe)` | First-time Marketplace-model invocation requires account-wide enablement; can be gated by IAM permissions or propagation lag | Retry after ~2 minutes; if persistent, request Marketplace IAM permissions from account admin |

---

## Cleanup

```bash
aws cloudformation delete-stack --stack-name restaurant-agent --region us-east-1
```

Confirm deletion completed:

```bash
aws cloudformation describe-stacks --stack-name restaurant-agent --region us-east-1
# Expected: "Stack with id restaurant-agent does not exist"  -  this confirms success
```

**Manually delete** (not managed by CloudFormation):
- AgentCore **Harness** → `restaurant_harness`
- AgentCore **Gateway** → `restaurant-agent-gateway` (removes its 3 targets)

---

## License
*MIT License*
