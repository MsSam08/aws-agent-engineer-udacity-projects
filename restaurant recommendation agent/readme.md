# Restaurant Recommendation Agent — AWS Bedrock AgentCore

A restaurant recommendation agent built on **Amazon Bedrock AgentCore**, backed by three AWS Lambda functions and orchestrated through an **AgentCore Gateway** and **Harness**. Given a request like *"Find me an Italian restaurant for tonight,"* the agent searches restaurants by cuisine, checks live availability, and returns a grounded recommendation based only on tool results.

> **Note on platform version:** This project was originally scoped around Amazon Bedrock Agents (Classic) — the point-and-click "action group" agent builder. Partway through the build, it was discovered that Bedrock Agents Classic is now in maintenance mode and closed to new customer accounts. This repo documents the **AgentCore** implementation instead, including the platform migration and the debugging that came with it.

---

## Architecture

```
User prompt
     │
     ▼
AgentCore Harness (model + system prompt)
     │
     ▼
AgentCore Gateway  ──(MCP)──►  Lambda targets
     │                              │
     │                              ├── get-cuisines
     │                              ├── search-restaurants
     │                              └── get-availability
     ▼
Final recommendation (grounded in tool output)
```

- **Lambda functions** (deployed via CloudFormation) — three simple Python functions backing the agent's tools.
- **AgentCore Gateway** — wraps the three Lambdas as MCP-compatible tools, using IAM-based inbound auth.
- **AgentCore Harness** — the managed agent runtime: model + system prompt + Gateway connection, no orchestration code required.

---

## Tools

| Tool | Description | Parameters |
|---|---|---|
| `get_cuisines` | Returns the list of available cuisine types | none |
| `search_restaurants` | Searches restaurants, optionally filtered by cuisine | `cuisine` (string, optional) |
| `get_availability` | Checks whether a restaurant has availability tonight | `restaurant_id` (string, required) |

---

## Agent Instructions

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
   received in this conversation — do not fabricate restaurant names,
   cuisines, or availability.
6. If no restaurants match or none have availability, tell the user
   clearly rather than inventing an alternative.
7. Keep responses concise and focused on the recommendation and the
   reasoning drawn from tool results.
```

---

## Deployment

### 1. Deploy the Lambda functions

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name restaurant-agent \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

Grab the Lambda ARNs from the stack outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name restaurant-agent \
  --region us-east-1 \
  --query "Stacks[0].Outputs"
```

### 2. Create an AgentCore Gateway

- Console: **AgentCore → Gateways → Create gateway**
- Inbound Auth type: **Use IAM permissions** (simplest option for single-user/lab use; avoids provisioning Cognito resources)
- Add three targets, one per Lambda, each as **Target type: Lambda ARN** with an inline JSON schema. See [`schemas/`](#tool-schemas) below.

### 3. Grant the Gateway permission to invoke each Lambda

The Gateway's execution role needs explicit `lambda:InvokeFunction` permission on each function (a Lambda **resource policy**, separate from the Gateway's own IAM role permissions):

```bash
aws lambda add-permission \
  --function-name get-cuisines \
  --statement-id GatewayInvoke \
  --action lambda:InvokeFunction \
  --principal "<GATEWAY_EXECUTION_ROLE_ARN>" \
  --region us-east-1

# repeat for search-restaurants and get-availability
```

Find `<GATEWAY_EXECUTION_ROLE_ARN>` on the Gateway's detail page in the console (an `arn:aws:iam::...:role/service-role/AmazonBedrockAgentCoreGatewayDefaultServiceRole...` ARN).

### 4. Create the Harness

- Console: **AgentCore → Harness → Create harness**
- Model: Claude (see [Known Issues](#known-issues) for why Nova Pro was avoided)
- Instructions: paste the [agent instructions](#agent-instructions) above
- Tools: **Connect a Gateway** → select your gateway

### 5. Test

In the harness playground:

```
Find me an Italian restaurant for tonight.
```

Expected trace: `search_restaurants(cuisine="Italian")` → `get_availability(restaurant_id=...)` → grounded final recommendation.

---

## Tool schemas

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

## Known Issues & Fixes

This build hit several real-world platform issues worth documenting for anyone following a similar path:

### 1. Bedrock Agents Classic is in maintenance mode
New accounts without prior usage can't create Classic agents. **Fix:** built on AgentCore Gateway + Harness instead.

### 2. Cognito quick-create fails on restricted IAM roles
`cognito-idp:CreateResourceServer` is often not granted in sandbox/lab accounts. **Fix:** use **"Use IAM permissions"** as the Gateway's inbound auth type instead of JWT/Cognito — avoids provisioning any Cognito resources.

### 3. Lambda functions written for Bedrock Agents Classic don't work with Gateway
Classic-style Lambdas expect:
```python
event["parameters"]  # list of {"name": ..., "value": ...}
```
and return a wrapped response:
```python
{"messageVersion": "1.0", "response": {"actionGroup": ..., "functionResponse": {...}}}
```

AgentCore Gateway instead passes a **flat object** matching your tool schema directly (e.g. `{"restaurant_id": "r3"}`) and expects a **plain JSON-serializable return value** — no wrapper.

**Fix:** rewrite each Lambda handler to read `event.get("<param>")` directly and `return {...}` directly. See [`lambdas/`](#) for corrected versions.

### 4. Gateway execution role needs explicit Lambda invoke permission
Each Lambda needs a resource policy statement granting `lambda:InvokeFunction` to the Gateway's execution role — this is separate from any Classic-era Bedrock service permission already on the function. See [Deployment step 3](#3-grant-the-gateway-permission-to-invoke-each-lambda).

### 5. Nova Pro tool-calling bug (`modelStreamErrorException`)
Amazon Nova Pro, when invoked through AgentCore Harness, intermittently/consistently fails mid tool-call with:
```
An error occurred (modelStreamErrorException) when calling the ConverseStream operation:
Model produced invalid sequence as part of ToolUse.
```
This is a documented, known issue with Nova models' tool-calling under the Converse API (see AWS's own [Nova tool-use troubleshooting guide](https://docs.aws.amazon.com/nova/latest/userguide/tools-troubleshooting.html)), reproduced independently across multiple SDKs (LangChain, Strands). Setting `temperature=0` and increasing max tokens is AWS's recommended first fix; in this build it did not fully resolve the issue.

**Fix:** switched the Harness's model to a Claude model, which resolved tool-calling immediately with no further errors.

### 6. AWS Marketplace subscription errors on first model invocation
Some Bedrock models (including Anthropic's) are distributed via AWS Marketplace and require a one-time account-wide enablement on first invoke, gated by `aws-marketplace:ViewSubscriptions` / `aws-marketplace:Subscribe` permissions. In a restricted sandbox account this can surface as an intermittent `AccessDeniedException` on some calls even after succeeding on others. If you hit this consistently, it needs an IAM permissions grant from your account administrator.

---

## Cleanup

```bash
aws cloudformation delete-stack --stack-name restaurant-agent --region us-east-1
```

Also manually delete via the console (not managed by CloudFormation):
- AgentCore **Harness**
- AgentCore **Gateway** (and its targets)

---

## License

MIT (or update to match your course/organization's requirements).
