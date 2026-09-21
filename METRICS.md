# Initial usage evidence

These historical schema-1 snapshots are retained unchanged. Version 0.2.0 identified that the old scanner could omit older running sessions and that unpriced models contributed zero to estimated credits. Treat the figures below as historical outputs of that implementation, not validated complete usage or current cost. Schema-2 comparisons reject these snapshots.

This report separates measured facts from projections. The periods are not yet a controlled before-and-after experiment.

## Measured incident baseline

The shipped metrics command aggregated a full local day on 2026-08-30 in `Europe/Berlin`:

| Metric | Value |
| --- | ---: |
| Sessions | 72 |
| Child sessions | 62 |
| Maximum agent depth | 6 |
| Model calls | 9,133 |
| Input tokens | 1,694,548,686 |
| Cached input tokens | 1,658,385,920 |
| Output tokens | 3,731,171 |
| Average input per call | 185,541 |
| Estimated credits | 21,463.45 |
| Effective context | 828,400 |
| Quota movement | 2% to 99%, peak 100% |

The aggregate snapshot is [benchmarks/before-2026-08-30.json](benchmarks/before-2026-08-30.json).

## Measured fresh-session smoke

After restoring native context and setting Terra/High defaults, one fresh root started one child:

| Metric | Root | Child |
| --- | ---: | ---: |
| Model | Terra/High | Terra/High |
| Effective context | 258,400 | 258,400 |
| Model calls | 3 | 2 |
| Input tokens | 96,678 | 76,246 |
| Cached input tokens | 69,888 | 58,880 |

The child attempted one nested spawn. `PreToolUse` denied it, and no grandchild ran.

This smoke proves configuration and enforcement. It does not measure a full production day.

## Partial post-change observation

The 2026-08-31 snapshot is incomplete and mostly contains sessions that started before the change:

- 1,952 model calls.
- 1,916 calls still used the old 828,400 context.
- 36 calls used the native 258,400 context.
- Estimated credits were 6,392.42 at snapshot time.

Do not use the partial comparison as an achieved savings claim. The file exists so readers can reproduce the transition: [benchmarks/after-2026-08-31-partial.json](benchmarks/after-2026-08-31-partial.json).

## Produce a comparable after period

Capture a complete 24-hour period after all relevant sessions start with the plugin enabled. Compare it with a 24-hour period that has similar tasks, model mix, and completion criteria. Report task completion alongside token reduction.

The snapshot command exports aggregate values only. It omits prompts, transcript text, file paths, thread IDs, hostnames, and account identifiers.
