# Jev Model Router

A Codex skill that uses [TypeSafe Jev](https://typesafe.ai) to recommend an execution subagent's model and reasoning effort, reducing manual model selection for each task.

[中文](README.md) · [Skill instructions](SKILL.md) · [MIT License](LICENSE)

The parent session orchestrates the task. This skill configures an execution subagent; it cannot switch a running parent session's model or grant execution permissions.

## Install

Requires Python 3.10+, a TypeSafe API key, and a Codex environment with skills, subagents, and the selected model configurations. The helper uses only Python's standard library.

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
git clone https://github.com/mrhsiung2015/jev-model-router.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/jev-model-router"
```

If that directory already exists, inspect its origin and back up local changes before proceeding. Start a new Codex session after installation.

Supply `TYPESAFE_API_KEY` or `JEV_API_KEY` through the environment. The former takes precedence. Prefer a secret manager; for a temporary terminal session, enter the key without putting it in shell history:

```bash
read -r -s TYPESAFE_API_KEY
export TYPESAFE_API_KEY
```

After the first command, type the key and press Enter. This affects only that shell and its future child processes. An already running desktop application will not inherit it; use your client's environment configuration. The script does not load `.env` files. Never commit credentials.

## Use in Codex

```text
Use $jev-model-router to choose an execution subagent, then complete this task: …
```

For automatic routing, merge [examples/AGENTS.md](examples/AGENTS.md) into your global or project instructions, preserving existing rules. No change to the default parent model is required. Installing the skill does not change global defaults for parents or subagents.

Optionally, to prefer 6.1 Sol / high for new local parent sessions, set these top-level values in your Codex `config.toml` after checking client support and any active profile or project overrides:

```toml
model = "gpt-6.1-sol"
model_reasoning_effort = "high"
```

This is a personal preference separate from Jev routing; it does not switch an already running parent session mid-turn.

The workflow is: minimize the task summary → ask Jev → apply local policy → delegate with the accepted configuration, or continue with the current model. Skip ordinary conversation, trivial answers, status checks, and tasks for which the user already selected a model.

The helper emits JSON; it does not create subagents itself. Codex follows `SKILL.md` to delegate and report the decision.

## Offline example

From the repository root, this command requires no key and makes no network request:

```bash
printf '%s\n' '{"task":"Implement a contained feature","current_model":"gpt-6-luna","current_reasoning_effort":"low","risk":"normal","expected_scope":"contained"}' \
  | python3 scripts/route.py --offline-choice balanced --offline-confidence 0.92
```

Simulated output, not a live Jev recommendation:

```json
{"status":"routed","tier":"balanced","model":"gpt-6-luna","reasoning_effort":"medium","confidence":0.92,"probabilities":{"balanced":1.0},"policy":["jev_recommendation"]}
```

Remove both `--offline-*` arguments for a live request. In restricted Codex environments, run **only the router process outside the sandbox**, using the permission tool and a reusable approval for the exact command when supported. Supply JSON on stdin or through a permission-safe temporary file. A shell pipeline does not bypass the sandbox. Subagents and subsequent commands retain their normal permission boundaries.

## Policy

| Tier | Default execution model | Reasoning effort |
| --- | --- | --- |
| Fast | `gpt-6-luna` | `low` |
| Balanced | `gpt-6-luna` | `medium` |
| Strong | `gpt-6.1-sol` | `high` |
| Long | `gpt-6-astra` | `max` |

These IDs come from the original execution environment. They are not a promise of universal model availability. See the official [OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-6.1-sol) for 6.1 Sol and its reasoning configuration. Verify support in your execution tool; if a selected configuration is unavailable, continue with the current model and report the limitation.

- Confidence ≥ 0.80: accept the recommendation, subject to the risk floor and Long toggle.
- 0.60 ≤ confidence < 0.80: accept upgrades or the same tier; reject downgrades when the current tier can be identified.
- Confidence < 0.60: retain the current model for ordinary tasks.
- A valid recommendation for high-risk work has a minimum tier of Strong, including at low confidence.
- Long becomes Strong unless explicitly enabled.
- API, network, timeout, or malformed-response failures return an error; the caller continues with the current model, including for high-risk tasks. The risk floor cannot guarantee an upgrade during an API failure.

The caller must set `risk: high` for security-sensitive or destructive work, production changes, deployments, and database migrations.

Fast and Balanced share a model ID. Supply `current_reasoning_effort` to distinguish them. If effort is absent or unrecognized, the helper conservatively assumes the highest matching tier. Exact configured targets take precedence. When no target matches, canonical roles identify Luna as Fast/Balanced, both `gpt-6-sol` and `gpt-6.1-sol` as Strong, and Astra as Long. Existing Sol sessions therefore retain medium-confidence downgrade protection after an upgrade or override. Explicit overrides can reassign a model to another tier. Unknown models cannot be compared; review medium-confidence recommendations before delegating.

## Input and output

stdin is one JSON object:

| Field | Contract |
| --- | --- |
| `task` | Required, non-empty summary string |
| `current_model` | Optional string; local comparison only |
| `current_reasoning_effort` | Optional string; local comparison only |
| `risk` | `low`, `normal`, or `high`; default `normal` |
| `expected_scope` | `single-step`, `contained`, `multi-step`, or `long-running`; default `contained` |

stdout contains a JSON decision: `routed`, `keep_current`, or `error`. The first two exit with code 0; errors exit with code 2. Parse `status`, rather than relying only on the exit code. Argument syntax errors are handled by argparse on stderr.

## Configuration

| Setting | Default / purpose |
| --- | --- |
| `TYPESAFE_API_KEY` / `JEV_API_KEY` | API authentication; not required offline |
| `JEV_MODEL` | `jev-latest`; set to pin a tested Jev version |
| `JEV_ROUTER_ALLOW_LONG` | Set exactly `1` to enable Long |
| `JEV_CODEX_FAST_MODEL` | Override Fast model |
| `JEV_CODEX_BALANCED_MODEL` | Override Balanced model |
| `JEV_CODEX_STRONG_MODEL` | Override Strong model |
| `JEV_CODEX_LONG_MODEL` | Override Long model |
| `--timeout` | 10 seconds; must be positive and finite |

Model overrides accept only `gpt-6-luna`, `gpt-6.1-sol`, `gpt-6-sol`, or `gpt-6-astra`. Other values fall back to the tier's default. Overrides do not change reasoning efforts. The legacy `JEV_CODEX_STRONG_MODEL=gpt-6-sol` override remains supported and is not rewritten to the new model.

## Privacy and permissions

Requests to `https://api.typesafe.ai/v1/systemone` contain only `task`, `risk`, and `expected_scope` as task state, plus fixed tier criteria and the Jev model name. Current model, current effort, and extra input fields are not sent.

Task summaries leave your machine. The caller must exclude credentials, source contents, personal data, and unrelated conversation history. The helper does not write tasks, responses, or keys to files; it emits only structured decisions or sanitized errors, never the key, and refuses HTTP redirects. Clients, terminals, and the external provider may have their own logging or retention policies; this repository makes no promises about them.

A route is a recommendation, not execution authorization. The sandbox exception covers only the router API process.

## Tests and contributions

```bash
python3 -m unittest discover -s tests -v
```

Tests use mocked HTTP responses and the offline CLI. They cover policy thresholds, input and response validation, request minimization, key non-disclosure, failures, and redirect refusal. No real credentials or TypeSafe access are needed. GitHub Actions runs the same suite on Python 3.10, 3.12, and 3.14.

Issues and pull requests are welcome. Add behavioral tests when changing policy. Never submit credentials, private task summaries, or workspace data.

This project packages an existing local skill, with portable documentation, offline tests, CI, input validation, sanitized errors, and redirect protection. The MIT license covers this repository only. Jev / TypeSafe is an external service governed by its own terms and trademark rights; this is an independent integration.
