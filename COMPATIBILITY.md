# Compatibility

## Chat approval in 0.3.0

Approval uses a standalone root `UserPromptSubmit` message containing `[allow-sub-agent]=allow` (or `[allow-sub-agent]`). It never opens a dialog or waits. The next child creation consumes the session-scoped grant under the same lock as attempt accounting. Existing sessions without a grant deny delegation and continue to allow unrelated tools.

Automated checks cover one-use approval, replayed messages and tool calls, concurrent consumption, child self-approval denial, blocked forwarding through recognized message tools, and the preserved policy limits. The prompt hook has no trusted operator-origin field, so the marker is not cryptographic or authenticated evidence of human approval.

The native host interception evidence below remains the v0.2.0 evidence; it is not a new live agent launch in v0.3.0.

## Host evidence from 0.2.0

Validation used Codex CLI 0.154.0 on macOS and the hook's `/usr/bin/python3` interpreter (Python 3.9.6). The unit suite also passed on Python 3.14.7.

## Observed host behavior

A fresh CLI session ran the candidate script through all five configured hook events. Hook trust was bypassed only for this invocation after inspecting the test hook sources; this does not establish persisted plugin trust.

- The root's native `collaborationspawn_agent` request was allowed and counted once.
- The host emitted one `SubagentStart` event.
- The child made a nested spawn request. Its hook input reported the parent session ID, while its transcript identified the child. The guard returned a denial, Codex rejected the tool call, and no grandchild was started.
- A separate session called a local mock MCP `create_thread` tool. Codex's tool-call arguments contained the child marker and omitted the old authorization marker, proving that the host applied `updatedInput`.

The MCP call then stopped at Codex's tool-approval gate under a `never` approval policy. The mock server did not receive the request. This test therefore does not prove MCP delivery, `PostToolUse` handling of a delivered response, or real desktop task creation and follow-up behavior. Those response shapes have unit coverage only.

The observed prompt hook contained no trusted operator-origin field. Version 0.2.0 consequently grants no prompt-marker exceptions.

## Automated coverage

The tests cover route exclusivity, child denial, five-attempt accounting, stable-ID replay, preserved legacy counters, concurrent calls, malformed state, unresolved identity, persistence failures, prompt rewriting, and response ID extraction.

Metrics tests cover usage from older session directories, missing and partial sources, malformed usage, unknown pricing, interval and rate-card compatibility, and separate token/cost comparison gates.

These checks establish the behavior of the tested paths. They do not establish account-wide enforcement, complete account usage, lower costs, or equivalent completed work.
