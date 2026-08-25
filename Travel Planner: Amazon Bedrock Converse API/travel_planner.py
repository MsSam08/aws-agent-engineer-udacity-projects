import boto3

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

MODEL_ID = "us.amazon.nova-2-lite-v1:0"  # US CRIS endpoint prefix required in US regions

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

# TODO (Task 1): Write the system prompt.
# The assistant should:
# - Help users plan visits to cities
# - NOT answer from memory   always use tools first
# - Base recommendations only on tool results
SYSTEM_PROMPT = """\
You are a travel planning assistant that helps users plan visits to cities.

You must never answer from memory. You do not have current information about
weather or attractions, so before giving any recommendation you must always
call the get_weather tool and the get_top_attractions tool for the relevant
city and date.

Once you have the tool results, base your entire recommendation only on that
data   do not add attractions, weather details, or other facts that did not
come from a tool result. If a tool returns no data for the requested city or
date, tell the user plainly that you don't have information for it. Do NOT
speculate about why the data is missing (e.g. do not claim it's because the
date is too far out or that there's a system issue)   you don't know the
reason, so don't invent one. Do NOT then fall back on your own general
knowledge to suggest attractions or facts about that city anyway. Simply
state that no data is available for that city/date and stop there   you may
offer to help with a different city or date that IS covered by the tools.

Tailor your recommendation to what the user tells you about their trip (for
example, traveling with family/kids versus a night out with friends), using
only the grounded tool results to decide which attractions fit.

If a user asks generally what cities or dates you can help with, or if you
want to save them a failed lookup, you may mention that your coverage is
currently limited to London (specifically 2026-03-14 and 2026-03-15)   call
the tools to confirm this if you're ever unsure, since your own knowledge of
what's covered could be wrong or out of date.\
"""

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------
WEATHER_DATA = {
    ("london", "2026-03-14"): {
        "city": "London",
        "date": "2026-03-14",
        "condition": "Light rain in the morning, clearing to partly cloudy by afternoon",
        "temperature_celsius": 11,
        "wind_mph": 12,
        "recommendation": "Bring a light jacket and umbrella for the morning",
    },
    ("london", "2026-03-15"): {
        "city": "London",
        "date": "2026-03-15",
        "condition": "Clear and sunny throughout the day",
        "temperature_celsius": 14,
        "wind_mph": 8,
        "recommendation": "Great day to spend time outdoors",
    },
}

ATTRACTIONS_DATA = {
    "london": {
        "city": "London",
        "attractions": [
            {"name": "British Museum",        "type": "indoor",          "family_friendly": True, "avg_visit_hours": 2.0},
            {"name": "Tower of London",       "type": "outdoor/indoor",  "family_friendly": True, "avg_visit_hours": 2.5},
            {"name": "Natural History Museum","type": "indoor",          "family_friendly": True, "avg_visit_hours": 2.0},
            {"name": "Hyde Park",             "type": "outdoor",         "family_friendly": True, "avg_visit_hours": 1.5},
            {"name": "Covent Garden",         "type": "outdoor/indoor",  "family_friendly": True,  "avg_visit_hours": 1.0},
            {"name": "The Comedy Store",      "type": "indoor",          "family_friendly": False, "avg_visit_hours": 2.0},
            {"name": "Soho Nightlife",        "type": "outdoor/indoor",  "family_friendly": False, "avg_visit_hours": 3.0},
            {"name": "Shoreditch Bar Crawl",  "type": "outdoor/indoor",  "family_friendly": False, "avg_visit_hours": 4.0},
        ],
    }
}

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "toolSpec": {
            "name": "get_weather",
            "description": "Returns current weather conditions and forecast for a given city and date.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        # TODO (Task 2): Define the two input properties   city and date
                        "city": {
                            "type": "string",
                            "description": "The city to get weather for",
                        },
                        "date": {
                            "type": "string",
                            "description": "The date in YYYY-MM-DD format",
                        },
                    },
                    "required": [
                        # TODO (Task 2): List the required fields
                        "city",
                        "date",
                    ],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_top_attractions",
            "description": "Returns a list of top-rated attractions in a given city.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        # TODO (Task 2): Define the one input property   city
                        "city": {
                            "type": "string",
                            "description": "The city to get attractions for",
                        },
                    },
                    "required": [
                        # TODO (Task 2): List the required fields
                        "city",
                    ],
                }
            },
        }
    },
]

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------
def get_weather(city: str, date: str) -> dict:
    # TODO (Task 3): Look up (city.lower(), date) in WEATHER_DATA.
    # Return the matching dict, or {"city": city, "date": date, "condition": "No data available"} if not found.
    result = WEATHER_DATA.get((city.lower(), date))
    if result is not None:
        return result
    return {"city": city, "date": date, "condition": "No data available"}


def get_top_attractions(city: str) -> dict:
    # TODO (Task 3): Look up city.lower() in ATTRACTIONS_DATA.
    # Return the matching dict, or {"city": city, "attractions": []} if not found.
    result = ATTRACTIONS_DATA.get(city.lower())
    if result is not None:
        return result
    return {"city": city, "attractions": []}


def execute_tool(name: str, tool_input: dict) -> dict:
    if name == "get_weather":
        return get_weather(tool_input["city"], tool_input["date"])
    elif name == "get_top_attractions":
        return get_top_attractions(tool_input["city"])
    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Converse loop
# ---------------------------------------------------------------------------
def run_chat() -> None:
    messages = []

    print("Travel Planner")
    print("=" * 40)
    print("Ask me to help plan your visit to a city. (Ctrl+C to quit)\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            return

        if not user_input:
            continue

        messages.append({"role": "user", "content": [{"text": user_input}]})

        # Inner loop: keep resolving tool calls until the model gives a final answer
        while True:
            response = bedrock.converse(
                modelId=MODEL_ID,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                toolConfig={"tools": TOOLS},
            )

            stop_reason = response["stopReason"]
            output_message = response["output"]["message"]
            messages.append(output_message)

            if stop_reason == "tool_use":
                tool_results = []

                for block in output_message["content"]:
                    if "toolUse" in block:
                        tool_name = block["toolUse"]["name"]
                        tool_input = block["toolUse"]["input"]
                        tool_use_id = block["toolUse"]["toolUseId"]

                        print(f"  [tool call] {tool_name}({tool_input})")
                        result = execute_tool(tool_name, tool_input)
                        print(f"  [tool result] {result}")

                        tool_results.append({
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "content": [{"json": result}],
                            }
                        })

                messages.append({"role": "user", "content": tool_results})
                continue  # ask the model again now that it has tool results

            # end_turn (or any other non-tool_use stop reason): print and go back
            # to the outer loop to collect the user's next message
            for block in output_message["content"]:
                if "text" in block:
                    print(f"\nAssistant: {block['text']}\n")
            break


if __name__ == "__main__":
    run_chat()
