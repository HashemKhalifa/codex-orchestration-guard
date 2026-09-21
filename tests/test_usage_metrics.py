from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "plugins"
    / "codex-orchestration-guard"
    / "scripts"
    / "usage_metrics.py"
)
SPEC = importlib.util.spec_from_file_location("usage_metrics", MODULE_PATH)
assert SPEC and SPEC.loader
metrics = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = metrics
SPEC.loader.exec_module(metrics)


def write_rollout(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def usage_records(
    timestamp: str,
    model: str = "gpt-5.6-terra",
    identity: str = "root",
    depth: int = 0,
    input_tokens: int = 100,
    cached_tokens: int = 80,
    output_tokens: int = 10,
) -> list[dict[str, object]]:
    source: object = "exec"
    if depth:
        source = {"subagent": {"thread_spawn": {"depth": depth}}}
    return [
        {"type": "session_meta", "payload": {"id": identity, "source": source}},
        {"type": "turn_context", "payload": {"model": model, "effort": "high"}},
        {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": input_tokens,
                        "cached_input_tokens": cached_tokens,
                        "output_tokens": output_tokens,
                        "reasoning_output_tokens": 3,
                    },
                    "model_context_window": 258400,
                },
                "rate_limits": {
                    "primary": {"used_percent": 10, "resets_at": 123}
                },
            },
        },
    ]


def comparable_snapshot() -> dict[str, object]:
    return {
        "schema_version": 2,
        "period_closed": True,
        "interval": {
            "start_utc": "2026-08-31T00:00:00Z",
            "end_utc": "2026-09-01T00:00:00Z",
            "timezone": "UTC",
            "duration_seconds": 86400,
        },
        "source_coverage": {
            "scope": metrics.SOURCE_SCOPE,
            "status": "scanned_supported_set",
            "discovered_files": 1,
            "readable_files": 1,
            "parsed_files": 1,
            "interval_bearing_files": 1,
            "unreadable_files": 0,
            "discovery_errors": 0,
            "parse_errors": 0,
            "unsupported_inputs": 0,
        },
        "rate_card": {
            "id": metrics.RATE_CARD_ID,
            "unit": "credits_per_million_tokens",
        },
        "pricing": {
            "rate_card_id": metrics.RATE_CARD_ID,
            "known_rate_subtotal": 20.0,
            "unpriced_calls": 0,
            "unpriced_input_tokens": 0,
            "unpriced_output_tokens": 0,
            "observed_total": 20.0,
        },
        "sessions": 1,
        "child_sessions": 0,
        "model_calls": 100,
        "input_tokens": 1000,
        "cached_input_tokens": 500,
        "output_tokens": 100,
    }


class UsageMetricsTests(unittest.TestCase):
    def test_summary_aggregates_known_pricing_and_agent_depth(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "root.jsonl"
            child = Path(directory) / "child.jsonl"
            write_rollout(root, usage_records("2026-08-31T10:00:00Z"))
            write_rollout(
                child,
                usage_records(
                    "2026-08-31T10:01:00Z",
                    identity="child",
                    depth=1,
                    input_tokens=50,
                    cached_tokens=40,
                    output_tokens=5,
                ),
            )

            summary = metrics.summarize_rollouts([root, child])

        self.assertEqual(summary["sessions"], 2)
        self.assertEqual(summary["root_sessions"], 1)
        self.assertEqual(summary["child_sessions"], 1)
        self.assertEqual(summary["model_calls"], 2)
        self.assertEqual(summary["input_tokens"], 150)
        self.assertEqual(summary["cached_input_tokens"], 120)
        self.assertEqual(summary["output_tokens"], 15)
        self.assertEqual(summary["context_windows"], {"258400": 2})
        self.assertAlmostEqual(summary["pricing"]["known_rate_subtotal"], 0.0066)
        self.assertAlmostEqual(summary["pricing"]["observed_total"], 0.0066)

    def test_snapshot_discovers_target_usage_in_older_session_directory(self) -> None:
        with TemporaryDirectory() as directory:
            codex_home = Path(directory)
            rollout = codex_home / "sessions" / "2026" / "08" / "27" / "old.jsonl"
            write_rollout(rollout, usage_records("2026-08-31T10:00:00Z"))

            snapshot = metrics.snapshot_for_day(
                codex_home,
                "2026-08-31",
                "UTC",
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )

        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["model_calls"], 1)
        self.assertEqual(snapshot["source_coverage"]["discovered_files"], 1)
        self.assertEqual(
            snapshot["source_coverage"]["status"], "scanned_supported_set"
        )

    def test_snapshot_distinguishes_valid_empty_from_unavailable_sources(self) -> None:
        with TemporaryDirectory() as directory:
            codex_home = Path(directory)
            rollout = codex_home / "sessions" / "2026" / "08" / "27" / "old.jsonl"
            write_rollout(rollout, usage_records("2026-08-30T10:00:00Z"))

            empty = metrics.snapshot_for_day(
                codex_home,
                "2026-08-31",
                "UTC",
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )
            unavailable = metrics.snapshot_for_day(
                Path(directory) / "missing",
                "2026-08-31",
                "UTC",
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )

        self.assertEqual(empty["model_calls"], 0)
        self.assertEqual(empty["source_coverage"]["status"], "scanned_supported_set")
        self.assertEqual(empty["source_coverage"]["parsed_files"], 1)
        self.assertEqual(empty["source_coverage"]["interval_bearing_files"], 0)
        self.assertEqual(unavailable["model_calls"], 0)
        self.assertEqual(unavailable["source_coverage"]["status"], "unavailable")
        self.assertEqual(unavailable["source_coverage"]["discovered_files"], 0)

    def test_snapshot_reports_partial_parse_coverage(self) -> None:
        with TemporaryDirectory() as directory:
            codex_home = Path(directory)
            rollout = codex_home / "sessions" / "2026" / "08" / "27" / "bad.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text("{not-json}\n", encoding="utf-8")

            snapshot = metrics.snapshot_for_day(
                codex_home,
                "2026-08-31",
                "UTC",
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )

        self.assertEqual(snapshot["source_coverage"]["status"], "partial")
        self.assertEqual(snapshot["source_coverage"]["readable_files"], 1)
        self.assertEqual(snapshot["source_coverage"]["parsed_files"], 0)
        self.assertEqual(snapshot["source_coverage"]["parse_errors"], 1)

    def test_discovery_error_reports_partial_coverage(self) -> None:
        def fail_walk(root: Path, onerror: object = None) -> object:
            assert callable(onerror)
            onerror(OSError("denied"))
            return iter(())

        with TemporaryDirectory() as directory:
            codex_home = Path(directory)
            (codex_home / "sessions").mkdir()
            with mock.patch.object(metrics.os, "walk", side_effect=fail_walk):
                snapshot = metrics.snapshot_for_day(
                    codex_home,
                    "2026-08-31",
                    "UTC",
                    now=datetime(2026, 9, 2, tzinfo=timezone.utc),
                )

        self.assertEqual(snapshot["source_coverage"]["status"], "partial")
        self.assertEqual(snapshot["source_coverage"]["discovery_errors"], 1)

    def test_arbitrary_json_does_not_establish_parsed_source_coverage(self) -> None:
        with TemporaryDirectory() as directory:
            codex_home = Path(directory)
            rollout = codex_home / "sessions" / "2026" / "08" / "27" / "empty.jsonl"
            write_rollout(rollout, [{}])

            snapshot = metrics.snapshot_for_day(
                codex_home,
                "2026-08-31",
                "UTC",
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )

        self.assertEqual(snapshot["source_coverage"]["status"], "partial")
        self.assertEqual(snapshot["source_coverage"]["parsed_files"], 0)
        self.assertEqual(snapshot["source_coverage"]["unsupported_inputs"], 1)

    def test_malformed_usage_and_timestamp_block_complete_coverage(self) -> None:
        with TemporaryDirectory() as directory:
            codex_home = Path(directory)
            rollout = codex_home / "sessions" / "2026" / "08" / "27" / "bad.jsonl"
            records = usage_records("not-a-timestamp", input_tokens=-1)
            records.append(
                {
                    "timestamp": "2026-08-31T10:00:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": True,
                                "cached_input_tokens": 0,
                                "output_tokens": 1,
                                "reasoning_output_tokens": 0,
                            }
                        },
                    },
                }
            )
            write_rollout(rollout, records)

            snapshot = metrics.snapshot_for_day(
                codex_home,
                "2026-08-31",
                "UTC",
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )

        self.assertEqual(snapshot["model_calls"], 0)
        self.assertEqual(snapshot["source_coverage"]["status"], "partial")
        self.assertEqual(snapshot["source_coverage"]["unsupported_inputs"], 2)

    def test_unpriced_usage_keeps_subtotal_but_makes_total_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            known = Path(directory) / "known.jsonl"
            unknown = Path(directory) / "unknown.jsonl"
            write_rollout(known, usage_records("2026-08-31T10:00:00Z"))
            write_rollout(
                unknown,
                usage_records("2026-08-31T10:01:00Z", model="gpt-6-astra"),
            )

            summary = metrics.summarize_rollouts([known, unknown])

        self.assertGreater(summary["pricing"]["known_rate_subtotal"], 0)
        self.assertEqual(summary["pricing"]["unpriced_calls"], 1)
        self.assertEqual(summary["pricing"]["unpriced_input_tokens"], 100)
        self.assertEqual(summary["pricing"]["unpriced_output_tokens"], 10)
        self.assertIsNone(summary["pricing"]["observed_total"])

    def test_compare_separates_token_and_cost_availability(self) -> None:
        before = comparable_snapshot()
        after = copy.deepcopy(before)
        after["model_calls"] = 40
        after["pricing"]["known_rate_subtotal"] = 9.0
        after["pricing"]["observed_total"] = None
        after["pricing"]["unpriced_calls"] = 1

        comparison = metrics.compare_snapshots(before, after, attested_comparable=True)

        self.assertTrue(comparison["token_comparison_available"])
        self.assertFalse(comparison["cost_comparison_available"])
        self.assertEqual(
            comparison["token_metrics"]["model_calls"]["percent_change"], -60.0
        )
        self.assertEqual(comparison["cost_metrics"], {})
        self.assertTrue(any("pricing" in reason for reason in comparison["reasons"]))

    def test_compare_rejects_schema_one_as_completeness_evidence(self) -> None:
        comparison = metrics.compare_snapshots(
            {"schema_version": 1, "complete_period": True},
            comparable_snapshot(),
            attested_comparable=True,
        )

        self.assertFalse(comparison["token_comparison_available"])
        self.assertFalse(comparison["cost_comparison_available"])
        self.assertTrue(any("schema 2" in reason for reason in comparison["reasons"]))

    def test_compare_validates_attestation_interval_zone_and_coverage(self) -> None:
        before = comparable_snapshot()
        cases = [
            ("attestation", comparable_snapshot(), False, "attest"),
            ("duration", comparable_snapshot(), True, "duration"),
            ("timezone", comparable_snapshot(), True, "timezone"),
            ("coverage", comparable_snapshot(), True, "coverage"),
        ]
        cases[1][1]["interval"]["duration_seconds"] = 3600
        cases[2][1]["interval"]["timezone"] = "Europe/Berlin"
        cases[3][1]["source_coverage"]["status"] = "partial"

        for name, after, attested, expected_reason in cases:
            with self.subTest(name=name):
                comparison = metrics.compare_snapshots(
                    before, after, attested_comparable=attested
                )
                self.assertFalse(comparison["token_comparison_available"])
                self.assertFalse(comparison["cost_comparison_available"])
                self.assertTrue(
                    any(expected_reason in reason for reason in comparison["reasons"])
                )

        different_rate = comparable_snapshot()
        different_rate["pricing"]["rate_card_id"] = "different"
        cost_only_block = metrics.compare_snapshots(
            before, different_rate, attested_comparable=True
        )
        self.assertTrue(cost_only_block["token_comparison_available"])
        self.assertFalse(cost_only_block["cost_comparison_available"])
        self.assertTrue(
            any("rate card" in reason for reason in cost_only_block["reasons"])
        )

        different_unit = comparable_snapshot()
        different_unit["rate_card"]["unit"] = "other_unit"
        unit_block = metrics.compare_snapshots(
            before, different_unit, attested_comparable=True
        )
        self.assertTrue(unit_block["token_comparison_available"])
        self.assertFalse(unit_block["cost_comparison_available"])
        self.assertTrue(any("rate card" in reason for reason in unit_block["reasons"]))

    def test_compare_rejects_negative_or_non_finite_metrics(self) -> None:
        before = comparable_snapshot()
        negative_tokens = comparable_snapshot()
        negative_tokens["input_tokens"] = -1
        token_block = metrics.compare_snapshots(
            before, negative_tokens, attested_comparable=True
        )
        self.assertFalse(token_block["token_comparison_available"])

        non_finite_cost = comparable_snapshot()
        non_finite_cost["pricing"]["observed_total"] = float("nan")
        cost_block = metrics.compare_snapshots(
            before, non_finite_cost, attested_comparable=True
        )
        self.assertTrue(cost_block["token_comparison_available"])
        self.assertFalse(cost_block["cost_comparison_available"])

        boolean_pricing = comparable_snapshot()
        boolean_pricing["pricing"]["unpriced_calls"] = False
        boolean_block = metrics.compare_snapshots(
            before, boolean_pricing, attested_comparable=True
        )
        self.assertTrue(boolean_block["token_comparison_available"])
        self.assertFalse(boolean_block["cost_comparison_available"])

        invalid_zone_before = comparable_snapshot()
        invalid_zone_after = comparable_snapshot()
        invalid_zone_before["interval"]["timezone"] = "Mars/Base"
        invalid_zone_after["interval"]["timezone"] = "Mars/Base"
        zone_block = metrics.compare_snapshots(
            invalid_zone_before, invalid_zone_after, attested_comparable=True
        )
        self.assertFalse(zone_block["token_comparison_available"])

    def test_closed_interval_is_distinct_from_source_coverage(self) -> None:
        before = comparable_snapshot()
        after = comparable_snapshot()
        after["period_closed"] = False

        comparison = metrics.compare_snapshots(before, after, attested_comparable=True)

        self.assertFalse(comparison["token_comparison_available"])
        self.assertTrue(
            any("closed" in reason for reason in comparison["reasons"])
        )

    def test_public_snapshot_contains_no_identifiers_or_paths(self) -> None:
        with TemporaryDirectory() as directory:
            codex_home = Path(directory)
            rollout = codex_home / "sessions" / "2026" / "08" / "27" / "old.jsonl"
            write_rollout(rollout, usage_records("2026-08-31T10:00:00Z"))
            snapshot = metrics.snapshot_for_day(
                codex_home,
                "2026-08-31",
                "UTC",
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )

        serialized = json.dumps(snapshot, sort_keys=True).lower()
        for forbidden in ("session_id", "thread_id", "path", "prompt", "hostname"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
