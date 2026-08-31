# Codex Orchestration Guard

Codex Orchestration Guard is a local Codex plugin that limits recursive delegation, caps direct agents, and adds a small scope contract at session start. It also exports aggregate usage metrics without prompts, paths, or thread IDs.

## What it changes

- A root session chooses task threads or a subagent tree. It cannot use both routes.
- A subagent tree can start four workers and one reviewer.
- Agent-created tasks and subagents cannot create children.
- An operator can allow one exceptional turn with `[allow-agent-orchestration]`.
- Agent-created prompts cannot forge that operator marker.
- Every session receives one short scope contract: complete one requested outcome, skip optional hardening, run one bounded verification set, and stop.

The plugin does not remove the context that every agent needs. It limits fan-out and prevents children from multiplying that context recursively.

## Install

Add the GitHub repository as a Codex marketplace:

```bash
codex plugin marketplace add HashemKhalifa/codex-orchestration-guard
codex plugin add codex-orchestration-guard@codex-orchestration-guard
```

Start a new Codex session. Open `/hooks`, review the plugin commands, and trust them before relying on enforcement.

If you already run a manual copy of this guard from `~/.codex/hooks.json`, remove or disable that hook before installing the plugin. Do not stack both copies.

## Configure efficient subagents

The hooks enforce routing and nesting. Add these settings to `~/.codex/config.toml` to make Terra/High the default for spawned agents:

```toml
[agents]
enabled = true
max_concurrent_threads_per_session = 5
default_subagent_model = "gpt-5.6-terra"
default_subagent_reasoning_effort = "high"
```

To restore Codex-managed context behavior, remove top-level `model_context_window` and `model_auto_compact_token_limit` overrides. Existing sessions keep their loaded settings. Test with a new session.

Explicit spawn settings and custom agent files can override the default model. See the [Codex subagent configuration](https://learn.chatgpt.com/docs/agent-configuration/subagents).

## Measure usage

Create a local aggregate snapshot from a repository checkout:

```bash
python3 plugins/codex-orchestration-guard/scripts/usage_metrics.py snapshot \
  --date 2026-08-31 \
  --timezone Europe/Berlin \
  --output usage-after.json
```

Compare complete, equal-length periods with similar work. The command refuses to calculate savings when either snapshot is partial:

```bash
python3 plugins/codex-orchestration-guard/scripts/usage_metrics.py compare \
  --before usage-before.json \
  --after usage-after.json \
  --attest-comparable \
  --format markdown
```

Use `--attest-comparable` only after checking equal duration, timezone, model mix, and similar completed work.

The command reads local Codex rollout files and writes aggregates only. It does not upload data. Estimated credits use a dated local rate card and are not an invoice.

See [METRICS.md](METRICS.md) for the initial evidence and its limitations.

## Test

```bash
python3 -m unittest discover -s tests -v
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  plugins/codex-orchestration-guard
```

## Security

Codex hooks can run outside the sandbox. Review the exact hook definitions before trusting them. The guard stores route counters and denial metadata under `PLUGIN_DATA`. It does not store prompts.

## License

[MIT](LICENSE)
