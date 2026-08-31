from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


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
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


class UsageMetricsTests(unittest.TestCase):
    def test_summary_aggregates_tokens_models_and_agent_depth(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "root.jsonl"
            child = Path(directory) / "child.jsonl"
            write_rollout(
                root,
                [
                    {
                        "type": "session_meta",
                        "payload": {"id": "root", "source": "exec"},
                    },
                    {
                        "type": "turn_context",
                        "payload": {"model": "gpt-5.6-terra", "effort": "high"},
                    },
                    {
                        "timestamp": "2026-08-31T10:00:00Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": 100,
                                    "cached_input_tokens": 80,
                                    "output_tokens": 10,
                                    "reasoning_output_tokens": 3,
                                },
                                "model_context_window": 258400,
                            },
                            "rate_limits": {
                                "primary": {
                                    "used_percent": 10,
                                    "resets_at": 123,
                                }
                            },
                        },
                    },
                ],
            )
            write_rollout(
                child,
                [
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "child",
                            "source": {
                                "subagent": {
                                    "thread_spawn": {
                                        "parent_thread_id": "root",
                                        "depth": 1,
                                    }
                                }
                            },
                        },
                    },
                    {
                        "type": "turn_context",
                        "payload": {"model": "gpt-5.6-terra", "effort": "high"},
                    },
                    {
                        "timestamp": "2026-08-31T10:01:00Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": 50,
                                    "cached_input_tokens": 40,
                                    "output_tokens": 5,
                                    "reasoning_output_tokens": 2,
                                },
                                "model_context_window": 258400,
                            },
                            "rate_limits": {
                                "primary": {
                                    "used_percent": 11,
                                    "resets_at": 123,
                                }
                            },
                        },
                    },
                ],
            )

            summary = metrics.summarize_rollouts([root, child])

        self.assertEqual(summary["sessions"], 2)
        self.assertEqual(summary["root_sessions"], 1)
        self.assertEqual(summary["child_sessions"], 1)
        self.assertEqual(summary["model_calls"], 2)
        self.assertEqual(summary["input_tokens"], 150)
        self.assertEqual(summary["cached_input_tokens"], 120)
        self.assertEqual(summary["output_tokens"], 15)
        self.assertEqual(summary["reasoning_output_tokens"], 5)
        self.assertEqual(summary["context_windows"], {"258400": 2})
        self.assertAlmostEqual(summary["estimated_credits"], 0.0066)
        self.assertEqual(summary["quota"]["first_percent"], 10)
        self.assertEqual(summary["quota"]["last_percent"], 11)

    def test_compare_reports_percent_change_and_handles_zero(self) -> None:
        before = {
            "complete_period": True,
            "model_calls": 100,
            "estimated_credits": 20.0,
        }
        after = {
            "complete_period": True,
            "model_calls": 40,
            "estimated_credits": 9.0,
        }

        comparison = metrics.compare_snapshots(before, after, attested_comparable=True)

        self.assertTrue(comparison["comparable"])
        self.assertEqual(
            comparison["metrics"]["model_calls"]["percent_change"], -60.0
        )
        self.assertEqual(
            comparison["metrics"]["estimated_credits"]["percent_change"], -55.0
        )
        self.assertIsNone(
            metrics.compare_snapshots(
                {"complete_period": True, "model_calls": 0},
                {"complete_period": True, "model_calls": 1},
                attested_comparable=True,
            )["metrics"]["model_calls"]["percent_change"]
        )

    def test_compare_requires_complete_periods_and_explicit_attestation(self) -> None:
        comparison = metrics.compare_snapshots(
            {"complete_period": True, "model_calls": 100},
            {"complete_period": False, "model_calls": 40},
        )

        self.assertFalse(comparison["comparable"])
        self.assertEqual(comparison["metrics"], {})
        self.assertIn("complete", comparison["reason"])

        unattested = metrics.compare_snapshots(
            {"complete_period": True, "model_calls": 100},
            {"complete_period": True, "model_calls": 40},
        )
        self.assertFalse(unattested["comparable"])
        self.assertIn("attest", unattested["reason"])

    def test_public_snapshot_contains_no_identifiers_or_paths(self) -> None:
        summary = metrics.empty_summary()

        serialized = json.dumps(summary, sort_keys=True).lower()

        for forbidden in ("session_id", "thread_id", "path", "prompt", "hostname"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
