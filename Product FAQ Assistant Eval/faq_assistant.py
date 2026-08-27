import boto3
import json

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

# TODO: Fill in after completing the console steps in the README.
PROMPT_VERSION_ARN = "<YOUR_PROMPT_VERSION_ARN>"

OUTPUT_FILE = "eval_responses.jsonl"

# ---------------------------------------------------------------------------
# Product FAQ (provided)
# ---------------------------------------------------------------------------
PRODUCT_FAQ = """\
Product FAQ

Pricing:
- Individual plan: $29 per month
- Team plan: $99 per month (up to 10 users)
- Enterprise: contact sales for custom pricing

Free Trial:
- 14-day free trial available for all plans
- No credit card required to start

Features:
- Task management with priority levels and due dates
- Time tracking built into each task
- Gantt chart view for project timelines
- Integrations: Slack and Google Workspace only

Storage:
- Individual plan: 10 GB per user
- Team plan: 100 GB shared across the team

Supported Platforms:
- Web browsers (Chrome, Firefox, Safari, Edge)
- iOS and Android mobile apps

Security:
- SOC 2 Type II certified
- All data encrypted at rest and in transit

Support:
- Email support for all plans
- Live chat support for Team and Enterprise plans only\
"""

# ---------------------------------------------------------------------------
# Eval dataset
#
# Each entry has:
#   "prompt"            – the customer question
#   "referenceResponse" – the ideal answer you expect from the assistant
#
# 4 answerable questions (clear answers exist in the FAQ above)
# 3 unanswerable questions (not covered by the FAQ)
# ---------------------------------------------------------------------------
EVAL_QUESTIONS = [
    # --- Answerable ---
    {
        "prompt": "How much does the team plan cost?",
        "referenceResponse": "The team plan is $99 per month for up to 10 users.",
    },
    {
        "prompt": "Is there a free trial?",
        "referenceResponse": (
            "Yes, there is a 14-day free trial available for all plans, "
            "and no credit card is required to start."
        ),
    },
    {
        "prompt": "What integrations do you support?",
        "referenceResponse": "We support Slack and Google Workspace integrations only.",
    },
    {
        "prompt": "Is live chat support available on the individual plan?",
        "referenceResponse": (
            "No, live chat support is only available for Team and "
            "Enterprise plans. The individual plan gets email support."
        ),
    },
    # --- Unanswerable ---
    {
        "prompt": "Do you offer a discount for nonprofits or students?",
        "referenceResponse": "I don't have that information in our FAQ.",
    },
    {
        "prompt": "Can I export my data to a CSV file?",
        "referenceResponse": "I don't have that information in our FAQ.",
    },
    {
        "prompt": "What is your refund policy if I cancel mid-month?",
        "referenceResponse": "I don't have that information in our FAQ.",
    },
]


# ---------------------------------------------------------------------------
# Invoke the stored prompt template
#
# NOTE: uses Converse, not InvokeModel. InvokeModel / InvokeModelWithResponseStream
# only work on Prompt Management prompts configured for Anthropic Claude or Meta
# Llama models. Since this prompt is configured for Amazon Nova Pro, Converse is
# required or the call will fail validation.
# ---------------------------------------------------------------------------
def invoke(question: str) -> str:
    response = bedrock.converse(
        modelId=PROMPT_VERSION_ARN,
        promptVariables={
            "faq": {"text": PRODUCT_FAQ},
            "customer_question": {"text": question},
        },
    )
    return response["output"]["message"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# Main – run eval and write results
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    records = []

    print("Running FAQ Assistant Eval\n")
    print("=" * 60)

    for item in EVAL_QUESTIONS:
        question = item["prompt"]
        reference = item["referenceResponse"]
        response = invoke(question)

        print(f"Question:  {question}")
        print(f"Expected:  {reference}")
        print(f"Response:  {response}")
        print("-" * 60)

        records.append({
            "prompt": question,
            "referenceResponse": reference,
            "modelResponses": [
                {
                    "response": response,
                    "modelIdentifier": "faq-assistant",
                }
            ],
        })

    with open(OUTPUT_FILE, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")

    print(f"\nWrote {len(records)} records to {OUTPUT_FILE}")
