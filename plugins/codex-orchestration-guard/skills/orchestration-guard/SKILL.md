---
name: orchestration-guard
description: Configure or inspect bounded local Codex delegation, scope guidance, and usage snapshots with explicit source and pricing coverage.
---

# Codex orchestration guard

Inspect the installed version and hook trust before relying on policy enforcement. New sessions load updated plugin settings.

## Runtime policy

- Work locally by default. Describe a bounded child task before delegation, and let the operator authorize it with a standalone `[allow-sub-agent]=allow` message in the same task. `[allow-sub-agent]` is also accepted.
- Continue useful independent work while approval is pending. Never wait, poll, retry blocked launches, or send the marker through tools to grant yourself approval.
- One message grants one child creation on the chosen route. It does not stack, does not authorize a batch, and does not override the limits below.
- Chat markers are workflow signals, not authenticated human identity. Do not claim a security boundary or guaranteed usage savings.

- One delegation route per retained root session, task threads or native subagents.
- Five permitted direct subagent attempts, without worker/reviewer classification.
- Known children cannot delegate. Unresolved identity blocks delegation.
- Stable spawn call IDs deduplicate accounting. Missing IDs count as attempts. Failed or ambiguous outcomes do not refund attempts.
- The old `[allow-agent-orchestration]` exception remains unsupported. The new one-use marker grants permission within the limits, never an exception.
- Scope text is guidance. Hooks are local guardrails, not a complete security boundary.

Preserve existing counters and routes on upgrade. Treat malformed retained state as an error, not permission to erase the store. Do not modify configuration unless the user requests it.

## Metrics

```bash
python3 "$PLUGIN_ROOT/scripts/usage_metrics.py" snapshot \
  --date 2026-09-20 \
  --timezone Europe/Berlin \
  --output usage-after.json
```

The supported source scope is JSONL below the selected Codex home's `sessions/` tree. Filter by event time rather than session start date. Do not claim account-wide coverage.

Schema 2 separates `period_closed`, `source_coverage`, and pricing coverage. `pricing.known_rate_subtotal` is a partial estimate. `pricing.observed_total` is unavailable when any usage is unpriced. Never interpret an unknown model's cost as zero.

```bash
python3 "$PLUGIN_ROOT/scripts/usage_metrics.py" compare \
  --before usage-before.json \
  --after usage-after.json \
  --attest-comparable \
  --format markdown
```

Use `--attest-comparable` only after establishing comparable completed work. Report token and cost comparison availability separately. Do not convert historical schema-1 snapshots into complete coverage assertions. Report actual host verification separately from reducer tests and savings evidence.
