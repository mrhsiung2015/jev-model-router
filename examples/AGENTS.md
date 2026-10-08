# Jev model routing

For every non-trivial execution task where the user has not explicitly selected a model, use `$jev-model-router` before implementation or investigation.

Keep the parent task as orchestrator. Apply the routed model and reasoning effort only to an execution subagent; do not claim that the parent model changed. Use only the GPT-6-family models supported by the skill and available in the execution tool, including GPT-6.1-Sol when available. The default Strong route is `gpt-6.1-sol` / high; this does not set the parent-session default. If Jev returns `keep_current` or `error`, or the chosen configuration is unavailable, continue with the current model.

Run only the exact Jev router process outside the restricted sandbox so it can reach TypeSafe. Request reusable approval for that command when supported. Send a minimal task summary through stdin; never send secrets, source contents, personal data, or unrelated conversation history. All later commands and subagents retain their normal permission boundaries.

A sandbox DNS failure is an execution-environment failure, not a Jev decision. If the router was accidentally run inside the sandbox, rerun once outside it before falling back. Do not retry other API failures automatically.

Report whether Jev ran, its recommended model, reasoning effort and confidence, and whether the recommendation was accepted. Skip Jev for ordinary conversation, trivial answers, status checks, and tasks with an explicitly selected model.
