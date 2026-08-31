---
name: orchestration-guard
description: Configure or inspect bounded Codex delegation, scope drift controls, native context defaults, and aggregate usage metrics. Use when a user asks why Codex usage is high, wants to prevent recursive agents or over-engineering, or wants before-and-after token data.
---

# Codex orchestration guard

Use this skill to inspect or configure the plugin. The hooks enforce the runtime policy automatically.

## Runtime policy

- Choose one route per root session: task threads or a subagent tree.
- Limit a subagent tree to four workers and one reviewer.
- Block agent-created children from spawning more children.
- Inject one scope contract at session start. Complete one requested outcome, avoid optional hardening, run one bounded verification set, and stop when the outcome is proven.
- Accept a one-turn exception only when the operator writes `[allow-agent-orchestration]` in a direct prompt. Remove this marker from agent-created task prompts.

## Recommended Codex configuration

Inspect `~/.codex/config.toml`. Recommend this `[agents]` block:

```toml
[agents]
enabled = true
max_concurrent_threads_per_session = 5
default_subagent_model = "gpt-5.6-terra"
default_subagent_reasoning_effort = "high"
```

Remove top-level `model_context_window` and `model_auto_compact_token_limit` overrides when the user wants Codex-managed context defaults. Preserve unrelated settings.

Do not edit configuration unless the user asks for the change. New settings apply to new sessions; existing sessions retain their loaded context.

## Metrics

Create an aggregate snapshot without prompts, paths, or thread IDs:

```bash
python3 "$PLUGIN_ROOT/scripts/usage_metrics.py" snapshot \
  --date 2026-08-31 \
  --timezone Europe/Berlin \
  --output usage-2026-08-31.json
```

Compare two snapshots:

```bash
python3 "$PLUGIN_ROOT/scripts/usage_metrics.py" compare \
  --before usage-before.json \
  --after usage-after.json \
  --format markdown
```

Treat estimated credits as a model-rate estimate, not an account invoice. Label non-equivalent workloads and counterfactual replays clearly.
