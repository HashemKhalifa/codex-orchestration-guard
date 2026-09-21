#!/usr/bin/env python3
"""Export aggregate Codex usage metrics without prompts, paths, or thread IDs."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


RATE_CARD_ID = "2026-08-31"
RATE_CARD_DATE = RATE_CARD_ID
RATES = {
    "gpt-5.6-sol": {"input": 100.0, "cached": 10.0, "output": 500.0},
    "gpt-5.6-terra": {"input": 50.0, "cached": 5.0, "output": 300.0},
    "gpt-5.6-luna": {"input": 5.0, "cached": 0.5, "output": 30.0},
}
SOURCE_SCOPE = "local_sessions_jsonl"
TOKEN_COMPARISON_FIELDS = (
    "sessions",
    "child_sessions",
    "model_calls",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
)


def empty_summary() -> Dict[str, Any]:
    return {
        "schema_version": 2,
        "period_closed": False,
        "rate_card": {
            "id": RATE_CARD_ID,
            "unit": "credits_per_million_tokens",
        },
        "pricing": {
            "rate_card_id": RATE_CARD_ID,
            "known_rate_subtotal": 0.0,
            "unpriced_calls": 0,
            "unpriced_input_tokens": 0,
            "unpriced_output_tokens": 0,
            "observed_total": 0.0,
        },
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


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def token_credits(
    model: str, input_tokens: int, cached: int, output: int
) -> Optional[float]:
    rates = RATES.get(model)
    if rates is None:
        return None
    uncached = max(0, input_tokens - cached)
    return (
        uncached * rates["input"]
        + cached * rates["cached"]
        + output * rates["output"]
    ) / 1_000_000


def _source_coverage(
    paths: List[Path], discovery_errors: int = 0
) -> Dict[str, Any]:
    return {
        "scope": SOURCE_SCOPE,
        "status": "unavailable",
        "discovered_files": len(paths),
        "readable_files": 0,
        "parsed_files": 0,
        "interval_bearing_files": 0,
        "unreadable_files": 0,
        "discovery_errors": discovery_errors,
        "parse_errors": 0,
        "unsupported_inputs": 0,
    }


def _finish_coverage(coverage: Dict[str, Any]) -> None:
    discovered = coverage["discovered_files"]
    if discovered == 0 and coverage["discovery_errors"] == 0:
        coverage["status"] = "unavailable"
        return
    complete = (
        coverage["readable_files"] == discovered
        and coverage["parsed_files"] == discovered
        and coverage["unreadable_files"] == 0
        and coverage["discovery_errors"] == 0
        and coverage["parse_errors"] == 0
        and coverage["unsupported_inputs"] == 0
    )
    coverage["status"] = "scanned_supported_set" if complete else "partial"


def _usage_value(usage: Dict[str, Any], name: str) -> Optional[int]:
    value = usage.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def summarize_rollouts(
    paths: Iterable[Path],
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    discovery_errors: int = 0,
) -> Dict[str, Any]:
    path_list = sorted(set(paths))
    summary = empty_summary()
    coverage = _source_coverage(path_list, discovery_errors=discovery_errors)
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
            "known_rate_subtotal": 0.0,
            "unpriced_calls": 0,
            "observed_total": 0.0,
        }
    )

    for path in path_list:
        identity = None
        depth = 0
        model = "unknown"
        file_has_usage = False
        file_has_json_record = False
        file_has_supported_record = False
        file_bears_interval = False
        try:
            with path.open("r", encoding="utf-8") as lines:
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, UnicodeError):
                        coverage["parse_errors"] += 1
                        continue
                    if not isinstance(record, dict):
                        coverage["parse_errors"] += 1
                        continue
                    file_has_json_record = True
                    timestamp = parse_timestamp(record.get("timestamp"))
                    if timestamp is not None and (
                        (start is None or timestamp >= start)
                        and (end is None or timestamp < end)
                    ):
                        file_bears_interval = True

                    record_type = record.get("type")
                    payload = record.get("payload")
                    if record_type == "session_meta" and isinstance(payload, dict):
                        candidate = payload.get("id") or payload.get("session_id")
                        if not isinstance(candidate, str) or not candidate:
                            continue
                        file_has_supported_record = True
                        identity = candidate
                        source = payload.get("source")
                        if isinstance(source, dict):
                            subagent = source.get("subagent")
                            spawn = (
                                subagent.get("thread_spawn")
                                if isinstance(subagent, dict)
                                else None
                            )
                            candidate_depth = (
                                spawn.get("depth")
                                if isinstance(spawn, dict)
                                else None
                            )
                            if isinstance(candidate_depth, int):
                                depth = candidate_depth
                        continue
                    if record_type == "turn_context" and isinstance(payload, dict):
                        candidate_model = payload.get("model")
                        if isinstance(candidate_model, str):
                            model = candidate_model
                        continue
                    if (
                        record_type != "event_msg"
                        or not isinstance(payload, dict)
                        or payload.get("type") != "token_count"
                    ):
                        continue
                    if timestamp is None:
                        coverage["unsupported_inputs"] += 1
                        continue
                    info = payload.get("info")
                    usage = (
                        info.get("last_token_usage")
                        if isinstance(info, dict)
                        else None
                    )
                    if not isinstance(usage, dict):
                        coverage["unsupported_inputs"] += 1
                        continue

                    input_tokens = _usage_value(usage, "input_tokens")
                    cached = _usage_value(usage, "cached_input_tokens")
                    output = _usage_value(usage, "output_tokens")
                    reasoning = _usage_value(usage, "reasoning_output_tokens")
                    if (
                        input_tokens is None
                        or cached is None
                        or output is None
                        or reasoning is None
                        or cached > input_tokens
                    ):
                        coverage["unsupported_inputs"] += 1
                        continue
                    file_has_supported_record = True
                    if (start is not None and timestamp < start) or (
                        end is not None and timestamp >= end
                    ):
                        continue
                    file_has_usage = True
                    summary["model_calls"] += 1
                    summary["input_tokens"] += input_tokens
                    summary["cached_input_tokens"] += cached
                    summary["output_tokens"] += output
                    summary["reasoning_output_tokens"] += reasoning
                    window = info.get("model_context_window")
                    if isinstance(window, int):
                        context_windows[str(window)] += 1

                    credits = token_credits(model, input_tokens, cached, output)
                    model_total = model_totals[model]
                    if credits is None:
                        summary["pricing"]["unpriced_calls"] += 1
                        summary["pricing"]["unpriced_input_tokens"] += input_tokens
                        summary["pricing"]["unpriced_output_tokens"] += output
                        summary["unknown_model_calls"] += 1
                        model_total["unpriced_calls"] += 1
                        model_total["observed_total"] = None
                    else:
                        summary["pricing"]["known_rate_subtotal"] += credits
                        model_total["known_rate_subtotal"] += credits
                        if model_total["observed_total"] is not None:
                            model_total["observed_total"] += credits
                    model_total["calls"] += 1
                    model_total["input_tokens"] += input_tokens
                    model_total["cached_input_tokens"] += cached
                    model_total["output_tokens"] += output

                    limits = payload.get("rate_limits")
                    primary = (
                        limits.get("primary") if isinstance(limits, dict) else None
                    )
                    if isinstance(primary, dict):
                        used = primary.get("used_percent")
                        reset = primary.get("resets_at")
                        minutes = primary.get("window_minutes")
                        if isinstance(used, (int, float)) and isinstance(reset, int):
                            quota_samples.append(
                                (timestamp, float(used), reset, minutes)
                            )
        except (OSError, UnicodeError):
            coverage["unreadable_files"] += 1
        else:
            coverage["readable_files"] += 1
            if file_has_supported_record:
                coverage["parsed_files"] += 1
            elif file_has_json_record:
                coverage["unsupported_inputs"] += 1
            if file_bears_interval:
                coverage["interval_bearing_files"] += 1

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

    subtotal = round(summary["pricing"]["known_rate_subtotal"], 6)
    summary["pricing"]["known_rate_subtotal"] = subtotal
    summary["pricing"]["observed_total"] = (
        subtotal if summary["pricing"]["unpriced_calls"] == 0 else None
    )
    for total in model_totals.values():
        total["known_rate_subtotal"] = round(total["known_rate_subtotal"], 6)
        if total["observed_total"] is not None:
            total["observed_total"] = round(total["observed_total"], 6)
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
    _finish_coverage(coverage)
    summary["source_coverage"] = coverage
    return summary


def discover_paths(codex_home: Path) -> Tuple[List[Path], int]:
    sessions_root = codex_home / "sessions"
    if not sessions_root.is_dir():
        return [], 0
    paths = []  # type: List[Path]
    errors = []  # type: List[OSError]
    for directory, _, filenames in os.walk(sessions_root, onerror=errors.append):
        paths.extend(
            Path(directory) / filename
            for filename in filenames
            if filename.endswith(".jsonl")
        )
    return sorted(set(paths)), len(errors)


def snapshot_for_day(
    codex_home: Path,
    target_date: str,
    timezone_name: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    zone = ZoneInfo(timezone_name)
    local_date = date.fromisoformat(target_date)
    start_local = datetime.combine(local_date, time.min).replace(tzinfo=zone)
    end_local = datetime.combine(local_date + timedelta(days=1), time.min).replace(
        tzinfo=zone
    )
    start = start_local.astimezone(timezone.utc)
    end = end_local.astimezone(timezone.utc)
    paths, discovery_errors = discover_paths(codex_home)
    result = summarize_rollouts(
        paths,
        start=start,
        end=end,
        discovery_errors=discovery_errors,
    )
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    result["period_closed"] = end <= current.astimezone(timezone.utc)
    result["interval"] = {
        "start_utc": iso_utc(start),
        "end_utc": iso_utc(end),
        "timezone": timezone_name,
        "duration_seconds": int((end - start).total_seconds()),
    }
    return result


def _change(before: float, after: float) -> Dict[str, Any]:
    percent = None
    if before != 0:
        percent = round((after - before) * 100 / before, 2)
    return {
        "before": before,
        "after": after,
        "absolute_change": after - before,
        "percent_change": percent,
    }


def _valid_interval(snapshot: Dict[str, Any]) -> bool:
    interval = snapshot.get("interval")
    if not isinstance(interval, dict):
        return False
    start = parse_timestamp(interval.get("start_utc"))
    end = parse_timestamp(interval.get("end_utc"))
    duration = interval.get("duration_seconds")
    zone = interval.get("timezone")
    if not isinstance(zone, str) or not zone:
        return False
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        return False
    return (
        start is not None
        and end is not None
        and end > start
        and isinstance(duration, (int, float))
        and not isinstance(duration, bool)
        and math.isfinite(duration)
        and duration > 0
        and int((end - start).total_seconds()) == int(duration)
    )


def _complete_source_coverage(snapshot: Dict[str, Any]) -> bool:
    coverage = snapshot.get("source_coverage")
    if not isinstance(coverage, dict):
        return False
    discovered = coverage.get("discovered_files")
    count_fields = (
        "discovered_files",
        "readable_files",
        "parsed_files",
        "interval_bearing_files",
        "unreadable_files",
        "discovery_errors",
        "parse_errors",
        "unsupported_inputs",
    )
    return (
        coverage.get("scope") == SOURCE_SCOPE
        and coverage.get("status") == "scanned_supported_set"
        and all(_nonnegative_integer(coverage.get(field)) for field in count_fields)
        and discovered > 0
        and coverage.get("readable_files") == discovered
        and coverage.get("parsed_files") == discovered
        and coverage.get("unreadable_files") == 0
        and coverage.get("discovery_errors") == 0
        and coverage.get("parse_errors") == 0
        and coverage.get("unsupported_inputs") == 0
    )


def _nonnegative_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _complete_pricing(snapshot: Dict[str, Any]) -> bool:
    pricing = snapshot.get("pricing")
    return (
        isinstance(pricing, dict)
        and _nonnegative_number(pricing.get("known_rate_subtotal"))
        and _nonnegative_number(pricing.get("observed_total"))
        and pricing.get("observed_total") == pricing.get("known_rate_subtotal")
        and _nonnegative_integer(pricing.get("unpriced_calls"))
        and pricing.get("unpriced_calls") == 0
        and _nonnegative_integer(pricing.get("unpriced_input_tokens"))
        and _nonnegative_integer(pricing.get("unpriced_output_tokens"))
        and isinstance(pricing.get("rate_card_id"), str)
        and bool(pricing.get("rate_card_id"))
    )


def compare_snapshots(
    before: Dict[str, Any],
    after: Dict[str, Any],
    attested_comparable: bool = False,
) -> Dict[str, Any]:
    common_reasons = []  # type: List[str]
    cost_reasons = []  # type: List[str]
    schema_ok = before.get("schema_version") == 2 and after.get("schema_version") == 2
    if not schema_ok:
        common_reasons.append(
            "Both snapshots must use schema 2 with explicit source coverage."
        )
    if schema_ok:
        if not before.get("period_closed") or not after.get("period_closed"):
            common_reasons.append("Both snapshot intervals must be closed.")
        if not _valid_interval(before) or not _valid_interval(after):
            common_reasons.append(
                "Both snapshots must contain valid interval duration and timestamps."
            )
        else:
            before_interval = before["interval"]
            after_interval = after["interval"]
            if (
                before_interval["duration_seconds"]
                != after_interval["duration_seconds"]
            ):
                common_reasons.append("Snapshot interval duration must match.")
            if before_interval["timezone"] != after_interval["timezone"]:
                common_reasons.append("Snapshot timezone must match.")
        if not _complete_source_coverage(
            before
        ) or not _complete_source_coverage(after):
            common_reasons.append("Both snapshots require complete source coverage.")
        if not all(
            _nonnegative_integer(snapshot.get(field))
            for snapshot in (before, after)
            for field in TOKEN_COMPARISON_FIELDS
        ):
            common_reasons.append("Both snapshots require complete token metrics.")
    if not attested_comparable:
        common_reasons.append(
            "Use --attest-comparable after verifying similar completed work "
            "and model mix."
        )

    token_available = not common_reasons
    if schema_ok:
        if not _complete_pricing(before) or not _complete_pricing(after):
            cost_reasons.append("Both snapshots require complete pricing coverage.")
        else:
            before_rate = before["pricing"]["rate_card_id"]
            after_rate = after["pricing"]["rate_card_id"]
            before_card = before.get("rate_card")
            after_card = after.get("rate_card")
            cards_match_snapshots = (
                isinstance(before_card, dict)
                and isinstance(after_card, dict)
                and before_card.get("id") == before_rate
                and after_card.get("id") == after_rate
                and isinstance(before_card.get("unit"), str)
                and bool(before_card.get("unit"))
                and before_card.get("unit") == after_card.get("unit")
            )
            if before_rate != after_rate or not cards_match_snapshots:
                cost_reasons.append("Snapshot rate card must match.")
    cost_available = token_available and not cost_reasons

    token_metrics = {}  # type: Dict[str, Any]
    if token_available:
        for field in TOKEN_COMPARISON_FIELDS:
            token_metrics[field] = _change(before[field], after[field])
    cost_metrics = {}  # type: Dict[str, Any]
    if cost_available:
        cost_metrics["observed_total"] = _change(
            before["pricing"]["observed_total"],
            after["pricing"]["observed_total"],
        )
    return {
        "token_comparison_available": token_available,
        "cost_comparison_available": cost_available,
        "reasons": common_reasons + cost_reasons,
        "token_metrics": token_metrics,
        "cost_metrics": cost_metrics,
    }


def render_markdown(value: Dict[str, Any]) -> str:
    if "comparison" in value:
        comparison = value["comparison"]
        lines = ["# Codex usage comparison", ""]
        if comparison["reasons"]:
            lines.extend(
                "- Unavailable: {}".format(reason)
                for reason in comparison["reasons"]
            )
        for section in ("token_metrics", "cost_metrics"):
            for field, result in comparison[section].items():
                lines.append(
                    "- `{}`: {} -> {} ({}%)".format(
                        field,
                        result["before"],
                        result["after"],
                        result["percent_change"],
                    )
                )
        return "\n".join(lines) + "\n"
    return (
        "# Codex usage snapshot\n\n"
        "- Source coverage: {source_coverage[status]}\n"
        "- Period closed: {period_closed}\n"
        "- Sessions: {sessions}\n"
        "- Child sessions: {child_sessions}\n"
        "- Model calls: {model_calls}\n"
        "- Input tokens: {input_tokens}\n"
        "- Cached input tokens: {cached_input_tokens}\n"
        "- Output tokens: {output_tokens}\n"
        "- Known-rate subtotal: {pricing[known_rate_subtotal]}\n"
        "- Observed total: {pricing[observed_total]}\n"
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
        result = snapshot_for_day(
            args.codex_home,
            args.date,
            args.timezone,
        )
        emit(result, args.format, args.output)
        return 0

    before = read_json(args.before)
    after = read_json(args.after)
    emit(
        {
            "schema_version": 2,
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
