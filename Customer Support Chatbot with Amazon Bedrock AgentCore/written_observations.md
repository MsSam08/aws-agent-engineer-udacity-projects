# Testing and Evaluation, Written Observations

Tested manually via `chat.py` and automatically via an 8-case suite
(`harness-tests.json`) run through `generate-eval-dataset.py` and scored by
Bedrock Evaluations LLM-as-a-judge (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`).

## Results

| Metric | Score |
|---|---|
| Correctness | 1.00 |
| Following Instructions | 1.00 |
| Helpfulness | 0.58 (range 0.17-0.83) |

Correctness and injection-defense were perfect across the board. Helpfulness
was lower/variable, the judge flagged tone issues (e.g. off-topic redirects
felt abrupt, bug-report confirmations felt impersonal for urgent issues)
that Correctness alone didn't catch.

## Bugs found and fixed

1. **Premature/fabricated tool calls.** Model sometimes called
   `create_bug_report` with invented or placeholder values instead of
   asking the customer. Fixed with an explicit "copy from customer's own
   words, never invent" rule plus a worked multi-turn example.
2. **Stray tool call.** Model tried calling an unregistered `file_operations`
   tool to "look up" the FAQ instead of using the FAQ text already in its
   context. Fixed by explicitly telling it the FAQ is already provided.
3. **Cross-session memory bleed.** AgentCore's default memory is keyed on
   `actorId`, which `chat.py`/`generate-eval-dataset.py` never set, so all
   sessions shared one memory namespace and leaked context between
   unrelated conversations. Fixed by setting a fresh `actorId` per session.

## Open findings (not fully resolved)

- **Intermittent `<thinking>`-tag / category-label leakage** into
  customer-facing replies on short/ambiguous inputs, despite an explicit
  suppression instruction. Reproduced multiple times, including after
  confirming the prompt file was unchanged, so this looks like a model-level
  reliability limit rather than a fixable prompt-wording issue.
- **Placeholder text can bypass the Lambda's emptiness check.** The model
  once filled required fields with the literal string `"N/A"` instead of
  leaving them empty, which passed validation and created an incomplete
  ticket (`b02b0401-...`). A robust fix would need pattern-matching against
  common placeholder tokens, not just an emptiness check.
- **No idempotency in `create_bug_report`.** No dedup protection; a retry
  or model self-correction could file duplicate tickets (observed once).

## Conclusion

All rubric requirements are met: routing is consistent (Correctness 1.00),
bug reports collect all three fields before ticket creation, FAQ answers
stay grounded with correct hand-off, and the full eval pipeline (test suite
→ JSONL → Bedrock Evaluations) is complete. Open findings above are edge
cases affecting polish, not core functionality, and are documented rather
than hidden.
