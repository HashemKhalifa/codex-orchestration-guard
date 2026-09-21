# Codex Orchestration Guard

Local Codex hooks require one-use chat approval for delegation, restrict recursive delegation, cap direct subagent attempts, and add scope guidance. An offline command reports local usage with source and pricing coverage.

## Policy

- Each new child requires one unused chat approval in its root session. Ordinary work is not blocked while approval is pending.
- Each retained root session chooses task threads or native subagents. It cannot mix routes.
- A root can make five permitted direct subagent attempts. The guard does not classify workers or reviewers and does not cap the task-thread route.
- Known children cannot delegate again. Unresolved actor identity blocks delegation.
- A repeated stable spawn call ID does not consume another attempt. Calls without an ID count as attempts. Failed or interrupted launches do not refund attempts.
- Each session receives one short scope instruction. This is guidance, not enforced task completion.

The policy depends on Codex invoking the hook and supplying recognizable events. It is not an account-wide limit or a security boundary against an agent that can modify local files or use unsupported tool paths.

## Upgrade to 0.3.0

Work locally by default. When an agent proposes a bounded delegation, approve one child by sending this as a standalone message in that same task:

```text
[allow-sub-agent]=allow
```

`[allow-sub-agent]` is also accepted. The next child creation consumes the approval, whether the selected route uses a native subagent or a Codex task. Repeated approval messages do not build up a batch allowance, and replaying the same message cannot grant another launch. Another child needs another approval message. Failed launches do not refund the approval.

There is no popup, blocking wait, polling loop, or background process. The guard blocks only unapproved delegation; the assistant is instructed to continue independent local work. The guard cannot force an assistant to keep working. If delegation is the only remaining action, it still requires your approval.

Approval never bypasses the nesting, route, or five-attempt limits. Quoted examples and messages containing additional prose do not grant approval. Child sessions cannot approve themselves. Recognized agent message tools cannot forward a standalone approval marker; child creation prompts have markers stripped.

The marker is a local workflow control, not authenticated proof of human identity. Codex's prompt hook does not expose a trusted operator-origin field. Unsupported message paths or software that can change local files can bypass these local controls. Do not claim an account-wide security or spending boundary.

Existing state migrates with no pending approval. An unused approval is scoped to its retained root session and follows the existing eight-day state retention.

## Earlier changes in 0.2.0

`[allow-agent-orchestration]` no longer grants an exception. Prompt text cannot establish operator origin, and agent-sent follow-ups must not acquire authority by containing a marker. This is a deliberate behavior change from 0.1.0.

Existing retained counters, routes, and child records survive migration. A missing state file starts a new local store; malformed existing state blocks delegation instead of silently resetting it. State retention remains eight days.

Usage snapshots now use schema 2. Historical schema-1 benchmark files remain unchanged and cannot be used as evidence of complete source or pricing coverage in the new comparison command.

## Install

```bash
codex plugin marketplace add HashemKhalifa/codex-orchestration-guard
codex plugin add codex-orchestration-guard@codex-orchestration-guard
```

For an existing installation, refresh the marketplace and reinstall:

```bash
codex plugin marketplace upgrade codex-orchestration-guard
codex plugin add codex-orchestration-guard@codex-orchestration-guard
```

Start a new Codex session. Open `/hooks`, review the commands, and trust them before relying on enforcement. Installation and hook trust are separate.

Disable any manual copy in `~/.codex/hooks.json` before enabling the plugin. Do not stack both copies.

## Configure subagents

The hooks do not select a model. These optional settings configure the host defaults:

```toml
[agents]
enabled = true
max_concurrent_threads_per_session = 5
default_subagent_model = "gpt-5.6-terra"
default_subagent_reasoning_effort = "high"
```

The host's concurrency limit and the guard's retained attempt count are different limits. Explicit spawn settings and agent profiles can override model defaults. See the [Codex subagent configuration](https://learn.chatgpt.com/docs/agent-configuration/subagents).

## Measure local usage

```bash
python3 plugins/codex-orchestration-guard/scripts/usage_metrics.py snapshot \
  --date 2026-09-20 \
  --timezone Europe/Berlin \
  --output usage-after.json
```

Discovery scans supported JSONL files recursively below the selected Codex home's `sessions/` directory and filters events by their timestamps. A session can contribute usage even if it started days before the requested period. Archived or remote data outside that source tree is not included.

A schema-2 snapshot separates:

- `period_closed`, which says whether the interval has ended.
- `source_coverage`, which reports discovered, readable, parsed, interval-bearing, unreadable, and unsupported inputs, plus traversal and parse errors.
- `pricing.known_rate_subtotal`, which prices only models in the dated rate card.
- `pricing.observed_total`, which is `null` when any observed usage has an unknown rate.
- Unpriced call and token counts, which make missing price coverage visible.

No supported input is different from a successfully observed period with no usage. Local source coverage is not proof of complete account usage. The dated rate card is an estimate, not an invoice, and does not invent prices for new models.

```bash
python3 plugins/codex-orchestration-guard/scripts/usage_metrics.py compare \
  --before usage-before.json \
  --after usage-after.json \
  --attest-comparable \
  --format markdown
```

Comparison checks schema, closed intervals, duration, timezone, source coverage, and rate-card compatibility. Token and cost comparisons have separate availability flags. Unknown pricing prevents a cost comparison. `--attest-comparable` still requires you to establish similar completed work; the tool cannot infer it from token totals.

Snapshots export aggregates without prompts, transcript text, file paths, thread IDs, or account identifiers. Nothing is uploaded. See [METRICS.md](METRICS.md) for the historical evidence and its limitations.

## Validate

The hook command uses `/usr/bin/python3`. Python 3.9 is supported, including the macOS system interpreter.

```bash
/usr/bin/python3 -m unittest discover -s tests -v
python3 -m unittest discover -s tests -v
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  plugins/codex-orchestration-guard
```

Tests cover policy decisions and executable state handling. Actual host compatibility and its limits are recorded in [COMPATIBILITY.md](COMPATIBILITY.md). A returned denial object alone does not prove that a host prevented a launch.

## License

[MIT](LICENSE)
