#!/usr/bin/env python3
"""Export aggregate Codex usage metrics without prompts, paths, or thread IDs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo


RATE_CARD_DATE = "2026-08-31"
RATES = {
    "gpt-5.6-sol": {"input": 100.0, "cached": 10.0, "output": 500.0},
    "gpt-5.6-terra": {"input": 50.0, "cached": 5.0, "output": 300.0},
    "gpt-5.6-luna": {"input": 5.0, "cached": 0.5, "output": 30.0},
}
COMPARISON_FIELDS = (
    "sessions",
    "child_sessions",
    "model_calls",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "estimated_credits",
)


def empty_summary() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "complete_period": False,
        "rate_card": {"date": RATE_CARD_DATE, "unit": "credits_per_million_tokens"},
        "sessions": 0,
        "root_sessions": 0,
        "child_sessions": 0,
        "max_agent_depth": 0,
        "model_calls": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "average_input_tokens_per_call": 0.0,
        "estimated_credits": 0.0,
        "unknown_model_calls": 0,
        "models": {},
        "context_windows": {},
        "quota": {
            "window_minutes": None,
            "first_percent": None,
            "last_percent": None,
            "peak_percent": None,
            "percentage_point_change": None,
        },
    }


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def token_credits(model: str, input_tokens: int, cached: int, output: int) -> Optional[float]:
    rates = RATES.get(model)
    if rates is None:
        return None
    uncached = max(0, input_tokens - cached)
    return (
        uncached * rates["input"]
        + cached * rates["cached"]
        + output * rates["output"]
    ) / 1_000_000


def summarize_rollouts(
    paths: Iterable[Path],
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Dict[str, Any]:
    summary = empty_summary()
    sessions = set()
    root_sessions = set()
    child_sessions = set()
    context_windows = Counter()
    quota_samples = []  # type: List[Tuple[datetime, float, int, Optional[int]]]
    model_totals = defaultdict(
        lambda: {
            "calls": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "estimated_credits": 0.0,
        }
    )

    for path in paths:
        identity = None
        depth = 0
        model = "unknown"
        file_has_usage = False
        try:
            lines = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with lines:
            for line in lines:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                record_type = record.get("type")
                payload = record.get("payload")
                if record_type == "session_meta" and isinstance(payload, dict):
                    candidate = payload.get("id")
                    identity = candidate if isinstance(candidate, str) else None
                    source = payload.get("source")
                    if isinstance(source, dict):
                        subagent = source.get("subagent")
                        spawn = (
                            subagent.get("thread_spawn")
                            if isinstance(subagent, dict)
                            else None
                        )
                        candidate_depth = (
                            spawn.get("depth") if isinstance(spawn, dict) else None
                        )
                        if isinstance(candidate_depth, int):
                            depth = candidate_depth
                elif record_type == "turn_context" and isinstance(payload, dict):
                    candidate_model = payload.get("model")
                    if isinstance(candidate_model, str):
                        model = candidate_model
                elif (
                    record_type == "event_msg"
                    and isinstance(payload, dict)
                    and payload.get("type") == "token_count"
                ):
                    timestamp = parse_timestamp(record.get("timestamp"))
                    if timestamp is None:
                        continue
                    if start is not None and timestamp < start:
                        continue
                    if end is not None and timestamp >= end:
                        continue
                    info = payload.get("info")
                    usage = info.get("last_token_usage") if isinstance(info, dict) else None
                    if not isinstance(usage, dict):
                        continue
                    input_tokens = int(usage.get("input_tokens") or 0)
                    cached = int(usage.get("cached_input_tokens") or 0)
                    output = int(usage.get("output_tokens") or 0)
                    reasoning = int(usage.get("reasoning_output_tokens") or 0)
                    file_has_usage = True
                    summary["model_calls"] += 1
                    summary["input_tokens"] += input_tokens
                    summary["cached_input_tokens"] += cached
                    summary["output_tokens"] += output
                    summary["reasoning_output_tokens"] += reasoning
                    window = info.get("model_context_window") if isinstance(info, dict) else None
                    if isinstance(window, int):
                        context_windows[str(window)] += 1
                    credits = token_credits(model, input_tokens, cached, output)
                    if credits is None:
                        summary["unknown_model_calls"] += 1
                        credits = 0.0
                    summary["estimated_credits"] += credits
                    model_total = model_totals[model]
                    model_total["calls"] += 1
                    model_total["input_tokens"] += input_tokens
                    model_total["cached_input_tokens"] += cached
                    model_total["output_tokens"] += output
                    model_total["estimated_credits"] += credits
                    limits = payload.get("rate_limits")
                    primary = limits.get("primary") if isinstance(limits, dict) else None
                    if isinstance(primary, dict):
                        used = primary.get("used_percent")
                        reset = primary.get("resets_at")
                        minutes = primary.get("window_minutes")
                        if isinstance(used, (int, float)) and isinstance(reset, int):
                            quota_samples.append((timestamp, float(used), reset, minutes))
        if file_has_usage:
            stable_identity = identity or "file-count-only:{}".format(len(sessions))
            sessions.add(stable_identity)
            if depth > 0:
                child_sessions.add(stable_identity)
            else:
                root_sessions.add(stable_identity)
            summary["max_agent_depth"] = max(summary["max_agent_depth"], depth)

    summary["sessions"] = len(sessions)
    summary["root_sessions"] = len(root_sessions)
    summary["child_sessions"] = len(child_sessions)
    summary["uncached_input_tokens"] = max(
        0, summary["input_tokens"] - summary["cached_input_tokens"]
    )
    if summary["model_calls"]:
        summary["average_input_tokens_per_call"] = round(
            summary["input_tokens"] / summary["model_calls"], 2
        )
    summary["estimated_credits"] = round(summary["estimated_credits"], 6)
    for total in model_totals.values():
        total["estimated_credits"] = round(total["estimated_credits"], 6)
    summary["models"] = dict(sorted(model_totals.items()))
    summary["context_windows"] = dict(sorted(context_windows.items()))
    if quota_samples:
        quota_samples.sort(key=lambda row: row[0])
        latest_reset = quota_samples[-1][2]
        current = [sample for sample in quota_samples if sample[2] == latest_reset]
        first = current[0]
        last = current[-1]
        summary["quota"] = {
            "window_minutes": last[3],
            "first_percent": first[1],
            "last_percent": last[1],
            "peak_percent": max(sample[1] for sample in current),
            "percentage_point_change": round(last[1] - first[1], 2),
        }
    return summary


def compare_snapshots(
    before: Dict[str, Any],
    after: Dict[str, Any],
    attested_comparable: bool = False,
) -> Dict[str, Any]:
    if not before.get("complete_period") or not after.get("complete_period"):
        return {
            "comparable": False,
            "reason": "Both snapshots must cover complete periods.",
            "metrics": {},
        }
    if not attested_comparable:
        return {
            "comparable": False,
            "reason": "Use --attest-comparable only after verifying similar work, duration, timezone, and model mix.",
            "metrics": {},
        }
    comparison = {}  # type: Dict[str, Any]
    for field in COMPARISON_FIELDS:
        before_value = before.get(field, 0)
        after_value = after.get(field, 0)
        percent = None
        if isinstance(before_value, (int, float)) and before_value != 0:
            percent = round((after_value - before_value) * 100 / before_value, 2)
        comparison[field] = {
            "before": before_value,
            "after": after_value,
            "absolute_change": after_value - before_value,
            "percent_change": percent,
        }
    return {"comparable": True, "reason": None, "metrics": comparison}


def discover_paths(
    codex_home: Path, target_date: date, zone: ZoneInfo
) -> Tuple[List[Path], datetime, datetime]:
    start_local = datetime.combine(target_date, time.min).replace(tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    paths = []  # type: List[Path]
    for day_offset in (-1, 0, 1):
        candidate = target_date + timedelta(days=day_offset)
        directory = (
            codex_home
            / "sessions"
            / "{:04d}".format(candidate.year)
            / "{:02d}".format(candidate.month)
            / "{:02d}".format(candidate.day)
        )
        if directory.is_dir():
            paths.extend(directory.glob("*.jsonl"))
    return sorted(set(paths)), start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def render_markdown(value: Dict[str, Any]) -> str:
    if "comparison" in value:
        lines = ["# Codex usage comparison", ""]
        comparison = value["comparison"]
        if not comparison.get("comparable"):
            return "\n".join(lines + ["Comparison unavailable: {}".format(comparison["reason"])]) + "\n"
        for field, result in comparison["metrics"].items():
            lines.append(
                "- `{}`: {} → {} ({}%)".format(
                    field,
                    result["before"],
                    result["after"],
                    result["percent_change"],
                )
            )
        return "\n".join(lines) + "\n"
    return (
        "# Codex usage snapshot\n\n"
        "- Sessions: {sessions}\n"
        "- Child sessions: {child_sessions}\n"
        "- Model calls: {model_calls}\n"
        "- Input tokens: {input_tokens}\n"
        "- Cached input tokens: {cached_input_tokens}\n"
        "- Output tokens: {output_tokens}\n"
        "- Estimated credits: {estimated_credits}\n"
    ).format(**value)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("snapshot must be a JSON object")
    return value


def emit(value: Dict[str, Any], output_format: str, output: Optional[Path]) -> None:
    text = (
        render_markdown(value)
        if output_format == "markdown"
        else json.dumps(value, indent=2, sort_keys=True) + "\n"
    )
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot", help="Aggregate one local day")
    snapshot.add_argument("--date", required=True)
    snapshot.add_argument("--timezone", default=os.environ.get("TZ", "UTC"))
    snapshot.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    snapshot.add_argument("--format", choices=("json", "markdown"), default="json")
    snapshot.add_argument("--output", type=Path)
    compare = subparsers.add_parser("compare", help="Compare two JSON snapshots")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.add_argument("--format", choices=("json", "markdown"), default="json")
    compare.add_argument("--output", type=Path)
    compare.add_argument(
        "--attest-comparable",
        action="store_true",
        help="Confirm equal duration, timezone, model mix, and similar completed work",
    )
    args = parser.parse_args()

    if args.command == "snapshot":
        target_date = date.fromisoformat(args.date)
        paths, start, end = discover_paths(
            args.codex_home, target_date, ZoneInfo(args.timezone)
        )
        result = summarize_rollouts(paths, start=start, end=end)
        result["complete_period"] = end <= datetime.now(timezone.utc)
        result["period"] = {
            "date": args.date,
            "timezone": args.timezone,
        }
        emit(result, args.format, args.output)
        return 0

    before = read_json(args.before)
    after = read_json(args.after)
    emit(
        {
            "schema_version": 1,
            "comparison": compare_snapshots(
                before, after, attested_comparable=args.attest_comparable
            ),
        },
        args.format,
        args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
