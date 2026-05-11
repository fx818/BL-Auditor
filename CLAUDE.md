# CLAUDE.md - 12-rule template

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

## Rule 1 - Think Before Coding
State assumptions explicitly. If uncertain, ask rather than guess.
Present multiple interpretations when ambiguity exists.
Push back when a simpler approach exists.
Stop when confused. Name what is unclear.

## Rule 2 - Simplicity First
Minimum code that solves the problem. Nothing speculative.
No features beyond what was asked. No abstractions for single-use code.
Test: would a senior engineer say this is overcomplicated? If yes, simplify.

## Rule 3 - Surgical Changes
Touch only what you must. Clean up only your own mess.
Do not improve adjacent code, comments, or formatting.
Do not refactor what is not broken. Match existing style.

## Rule 4 - Goal-Driven Execution
Define success criteria. Loop until verified.
Do not follow steps. Define success and iterate.
Strong success criteria let you loop independently.

## Rule 5 - Use the model only for judgment calls
Use me for: classification, drafting, summarization, extraction.
Do NOT use me for: routing, retries, deterministic transforms.
If code can answer, code answers.

## Rule 6 - Token budgets are not advisory
Per-task: 4,000 tokens. Per-session: 30,000 tokens.
If approaching budget, summarize and start fresh.
Surface the breach. Do not silently overrun.

## Rule 7 - Surface conflicts, do not average them
If two patterns contradict, pick one (more recent / more tested).
Explain why. Flag the other for cleanup.
Do not blend conflicting patterns.

## Rule 8 - Read before you write
Before adding code, read exports, immediate callers, shared utilities.
Looks orthogonal is dangerous. If unsure why code is structured a way, ask.

## Rule 9 - Tests verify intent, not just behavior
Tests must encode WHY behavior matters, not just WHAT it does.
A test that cannot fail when business logic changes is wrong.

## Rule 10 - Checkpoint after every significant step
Summarize what was done, what is verified, what is left.
Do not continue from a state you cannot describe back.
If you lose track, stop and restate.

## Rule 11 - Match the codebase conventions, even if you disagree
Conformance > taste inside the codebase.
If you genuinely think a convention is harmful, surface it. Do not fork silently.

## Rule 12 - Fail loud
Completed is wrong if anything was skipped silently.
Tests pass is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.
