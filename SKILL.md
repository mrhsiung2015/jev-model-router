---
name: jev-model-router
description: Use TypeSafe Jev outside the restricted sandbox to choose the Codex model and reasoning effort for a non-trivial execution task, then delegate that work to an explicitly selected subagent. Use when the user asks for automatic model routing or when global instructions require Jev routing. Do not use for trivial conversation, status questions, or when the user already chose a model.
---

# Jev Model Router

Route meaningful execution work through Jev before delegating it. The current parent model is already running and cannot be changed mid-turn; keep it as orchestrator and apply Jev's decision to the execution subagent.

## Route

1. Skip routing when the request is conversational, answer-only and trivial, a status check, or already names a model. Never route work merely to create unnecessary delegation.
2. Summarize only the information needed to classify the task. Do not send credentials, secrets, source-file contents, personal data, or unrelated conversation history to Jev.
3. Build one JSON object:

   ```json
   {
     "task": "Concise task summary",
     "current_model": "Current parent model when known",
     "current_reasoning_effort": "Current parent reasoning effort when known",
     "risk": "low | normal | high",
     "expected_scope": "single-step | contained | multi-step | long-running"
   }
   ```

   Use `risk: high` for security-sensitive work, destructive operations, production changes, deployments, or database migrations.
4. Run `scripts/route.py` **outside the restricted sandbox** because it must reach `https://api.typesafe.ai`. Use the command tool's escalated/unsandboxed execution mode for this exact router process and request a reusable approval for the exact command prefix when supported. Do not first try the live API call in the default sandbox. This exception applies only to the Jev routing process; it does not grant the selected subagent or later project commands unsandboxed access.
5. Send the JSON on stdin. Do not interpolate task text into a shell command. Start the process and write JSON to stdin, or use a permission-safe temporary JSON file.
6. If the helper returns `status: routed`, create one execution subagent using exactly its `model` and `reasoning_effort`. Give the subagent the original task, relevant workspace context, and normal permission boundaries. Keep final integration and user communication in the parent.
7. If it returns `status: keep_current` or `status: error`, continue with the current model. Do not retry automatically or block the task. If the reason is `network_unavailable_or_sandboxed`, verify that the command actually used escalated/unsandboxed execution before treating Jev as unavailable; rerun once outside the sandbox if it did not.
8. Briefly expose the decision to the user, for example: `[Jev] gpt-6.1-sol / high, confidence 0.87.` Do not claim that the parent session model changed.

## Policy

The helper enforces these rules after Jev answers:

- confidence at least `0.80`: accept the tier;
- confidence at least `0.60` and below `0.80`: accept upgrades but refuse downgrades when the current tier is known;
- high-risk work has a minimum tier of Strong;
- confidence below `0.60`: keep the current model for ordinary work; high-risk
  work still routes to the Strong floor so it cannot fall back to a weaker
  current model;
- Long/Astra is disabled unless `JEV_ROUTER_ALLOW_LONG=1`;
- API, timeout, schema, or model-resolution failures keep the current model.

Fast and Balanced share a model. Pass `current_reasoning_effort` when known
so the helper can distinguish them. If multiple tiers match the current model
and effort is absent or unrecognized, it conservatively assumes the highest
matching tier. Exact configured target matches take precedence. When there
is no configured match, the helper recognizes canonical roles: Luna as
Fast/Balanced, both `gpt-6-sol` and `gpt-6.1-sol` as Strong, and Astra as Long.
This preserves downgrade protection for existing Sol sessions after an upgrade
or override. An unknown model has no comparable tier; supply accurate context
and review a medium-confidence recommendation before delegating.

All execution subagents must use a GPT-6-family model. The default tiers are Fast=`gpt-6-luna` / low, Balanced=`gpt-6-luna` / medium, Strong=`gpt-6.1-sol` / high, and Long=`gpt-6-astra` / max. Long/Astra remains disabled unless `JEV_ROUTER_ALLOW_LONG=1`. Optional `JEV_CODEX_FAST_MODEL`, `JEV_CODEX_BALANCED_MODEL`, `JEV_CODEX_STRONG_MODEL`, and `JEV_CODEX_LONG_MODEL` overrides are accepted only for `gpt-6-luna`, `gpt-6.1-sol`, `gpt-6-sol`, or `gpt-6-astra`; any other model falls back to that tier's GPT-6 default. A legacy `JEV_CODEX_STRONG_MODEL=gpt-6-sol` override remains supported and is not rewritten. The helper reads `TYPESAFE_API_KEY` or `JEV_API_KEY` but never prints or stores it.

Parent-session defaults are separate from routing. Installing this skill does not
change Codex configuration; a user may independently select `gpt-6.1-sol` / high
for new parent sessions while keeping Luna for lighter routed tasks.

## Boundaries

- Jev is an external service. Sending a task summary is an external disclosure; minimize the state.
- A sandbox DNS error such as `nodename nor servname provided` is an execution-environment failure, not evidence that the TypeSafe URL or API key is wrong.
- A route is a recommendation, not authorization. Preserve all sandbox, approval, destructive-action, and user-confirmation requirements.
- Do not install packages, alter model-provider configuration, or start persistent services while invoking this skill.
- The API model defaults to `jev-latest`; set `JEV_MODEL` to pin a tested version.
- Model IDs are environment-specific. Verify that the execution tool supports
  the chosen model and effort. If it does not, keep the current model and report
  the limitation; do not silently substitute a different model.
