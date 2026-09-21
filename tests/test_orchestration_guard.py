from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "plugins"
    / "codex-orchestration-guard"
    / "scripts"
    / "orchestration_guard.py"
)
SPEC = importlib.util.spec_from_file_location("orchestration_guard", MODULE_PATH)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)

NOW = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)


def event(
    event_name: str,
    session_id: str = "root",
    turn_id: str = "turn-1",
    tool_name: Optional[str] = None,
    prompt: Optional[str] = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "hook_event_name": event_name,
        "session_id": session_id,
        "turn_id": turn_id,
    }
    if tool_name is not None:
        value["tool_name"] = tool_name
        value["tool_input"] = {}
    if prompt is not None:
        value["prompt"] = prompt
    return value


def denied(response: dict[str, object]) -> bool:
    output = response.get("hookSpecificOutput")
    return isinstance(output, dict) and output.get("permissionDecision") == "deny"


def run_hook(data_dir: Path, hook_input: dict[str, object]) -> dict[str, object]:
    if guard.orchestration_route(hook_input.get("tool_name")) is not None:
        transcript = data_dir / "root.jsonl"
        if not transcript.exists():
            transcript.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": "root"}})
                + "\n",
                encoding="utf-8",
            )
        hook_input = dict(hook_input)
        hook_input["transcript_path"] = str(transcript)
    environment = dict(os.environ)
    environment["PLUGIN_DATA"] = str(data_dir)
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input=json.dumps(hook_input),
        text=True,
        capture_output=True,
        check=True,
        env=environment,
    )
    return json.loads(result.stdout)


class OrchestrationGuardTests(unittest.TestCase):
    def test_session_start_adds_one_bounded_scope_contract(self) -> None:
        state: dict[str, object] = {}
        response = guard.handle_event(
            state,
            {
                "hook_event_name": "SessionStart",
                "session_id": "root",
                "model": "gpt-5.6-sol",
            },
            NOW,
        )

        context = response["hookSpecificOutput"]["additionalContext"]
        self.assertIn("one requested outcome", context)
        self.assertIn("optional hardening", context)
        self.assertIn("Stop when", context)
        self.assertNotIn("continue", response)
        self.assertEqual(
            guard.handle_event(
                state,
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "root",
                    "model": "gpt-5.6-sol",
                },
                NOW,
            ),
            {},
        )

    def test_root_chooses_one_route(self) -> None:
        state: dict[str, object] = {}
        create = event("PreToolUse", tool_name="mcp__codex_app__create_thread")
        create["tool_input"] = {"prompt": "Do the task", "title": "Task"}

        updated = guard.handle_event(state, create, NOW)
        response = guard.handle_event(
            state, event("PreToolUse", tool_name="collaborationspawn_agent"), NOW
        )

        self.assertEqual(
            updated["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertTrue(denied(response))

    def test_sixth_direct_agent_is_denied(self) -> None:
        state: dict[str, object] = {}
        for index in range(5):
            self.assertEqual(
                guard.handle_event(
                    state,
                    event(
                        "PreToolUse",
                        turn_id=f"turn-{index}",
                        tool_name="collaborationspawn_agent",
                    ),
                    NOW,
                ),
                {},
            )

        response = guard.handle_event(
            state,
            event(
                "PreToolUse",
                turn_id="turn-6",
                tool_name="collaborationspawn_agent",
            ),
            NOW,
        )

        self.assertTrue(denied(response))

    def test_depth_one_child_is_denied_from_transcript(self) -> None:
        with TemporaryDirectory() as directory:
            transcript = Path(directory) / "child.jsonl"
            transcript.write_text(
                json.dumps(
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
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            hook_input = event(
                "PreToolUse", session_id="root", tool_name="collaborationspawn_agent"
            )
            hook_input["transcript_path"] = str(transcript)

            response = guard.handle_event({}, hook_input, NOW)

        self.assertTrue(denied(response))

    def test_unreadable_transcript_fails_closed(self) -> None:
        hook_input = event(
            "PreToolUse", session_id="root", tool_name="collaborationspawn_agent"
        )
        hook_input["transcript_path"] = "/missing/transcript.jsonl"

        self.assertTrue(denied(guard.handle_event({}, hook_input, NOW)))

    def test_prompt_marker_never_authorizes_delegation(self) -> None:
        state: dict[str, object] = {
            "child_threads": {"root": {"parent_session_id": "parent"}}
        }
        guard.handle_event(
            state,
            event(
                "UserPromptSubmit",
                session_id="root",
                turn_id="marker-turn",
                prompt=f"{guard.AUTHORIZATION_MARKER} Spawn anyway",
            ),
            NOW,
        )
        response = guard.handle_event(
            state,
            event(
                "PreToolUse",
                session_id="root",
                turn_id="marker-turn",
                tool_name="collaborationspawn_agent",
            ),
            NOW,
        )

        self.assertTrue(denied(response))

    def test_duplicate_spawn_tool_use_id_consumes_one_attempt(self) -> None:
        state: dict[str, object] = {}
        spawn = event("PreToolUse", tool_name="collaborationspawn_agent")
        spawn["tool_use_id"] = "spawn-1"

        self.assertEqual(guard.handle_event(state, spawn, NOW), {})
        self.assertEqual(guard.handle_event(state, spawn, NOW), {})

        record = state["sessions"]["root"]
        self.assertEqual(record["direct_agent_spawns"], 1)

    def test_existing_attempt_count_is_preserved_when_ids_are_migrated(self) -> None:
        state: dict[str, object] = {
            "sessions": {"root": {"direct_agent_spawns": 4}}
        }
        first = event("PreToolUse", tool_name="collaborationspawn_agent")
        first["tool_use_id"] = "new-1"
        replay = dict(first)
        next_attempt = event("PreToolUse", tool_name="collaborationspawn_agent")
        next_attempt["tool_use_id"] = "new-2"

        self.assertEqual(guard.handle_event(state, first, NOW), {})
        self.assertEqual(guard.handle_event(state, replay, NOW), {})
        self.assertTrue(denied(guard.handle_event(state, next_attempt, NOW)))
        self.assertEqual(state["sessions"]["root"]["direct_agent_spawns"], 5)

    def test_missing_session_identity_denies_delegation(self) -> None:
        response = guard.handle_event(
            {}, event("PreToolUse", session_id="", tool_name="collaborationspawn_agent"), NOW
        )

        self.assertTrue(denied(response))

    def test_malformed_retained_state_denies_without_replacing_it(self) -> None:
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            state_path = data_dir / "state.json"
            state_path.write_text("{not json", encoding="utf-8")

            response = run_hook(
                data_dir, event("PreToolUse", tool_name="collaborationspawn_agent")
            )

            self.assertTrue(denied(response))
            self.assertEqual(state_path.read_text(encoding="utf-8"), "{not json")

    def test_malformed_nested_state_denies_without_pruning_child_record(self) -> None:
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            state_path = data_dir / "state.json"
            original = {"child_threads": {"child": "not-a-record"}}
            state_path.write_text(json.dumps(original), encoding="utf-8")

            response = run_hook(
                data_dir, event("PreToolUse", tool_name="collaborationspawn_agent")
            )

            self.assertTrue(denied(response))
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), original)

    def test_null_sessions_map_denies_without_crashing(self) -> None:
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            state_path = data_dir / "state.json"
            state_path.write_text(json.dumps({"sessions": None}), encoding="utf-8")

            response = run_hook(
                data_dir, event("PreToolUse", tool_name="collaborationspawn_agent")
            )

            self.assertTrue(denied(response))
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), {"sessions": None})

    def test_inconsistent_replay_ledger_denies_without_resetting_counter(self) -> None:
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            state_path = data_dir / "state.json"
            original = {
                "sessions": {
                    "root": {
                        "direct_agent_spawns": 1,
                        "spawn_tool_use_ids": ["first", "second"],
                    }
                }
            }
            state_path.write_text(json.dumps(original), encoding="utf-8")

            response = run_hook(
                data_dir, event("PreToolUse", tool_name="collaborationspawn_agent")
            )

            self.assertTrue(denied(response))
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), original)

    def test_invalid_thread_input_does_not_choose_route(self) -> None:
        state: dict[str, object] = {}
        invalid = event("PreToolUse", tool_name="mcp__codex_app__create_thread")

        self.assertTrue(denied(guard.handle_event(state, invalid, NOW)))
        self.assertEqual(state, {})

    def test_malformed_state_on_session_start_has_no_pre_tool_decision(self) -> None:
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "state.json").write_text("[]", encoding="utf-8")

            response = run_hook(data_dir, event("SessionStart"))

        self.assertIn("systemMessage", response)
        self.assertNotIn("hookSpecificOutput", response)

    def test_missing_transcript_identity_denies_at_executable_boundary(self) -> None:
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            hook_input = event("PreToolUse", tool_name="collaborationspawn_agent")
            environment = dict(os.environ)
            environment["PLUGIN_DATA"] = str(data_dir)
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH)],
                input=json.dumps(hook_input),
                text=True,
                capture_output=True,
                check=True,
                env=environment,
            )

        self.assertTrue(denied(json.loads(result.stdout)))

    def test_invalid_utf8_state_or_transcript_denies_at_executable_boundary(self) -> None:
        for invalid_target in ("state", "transcript"):
            with self.subTest(invalid_target=invalid_target), TemporaryDirectory() as directory:
                data_dir = Path(directory)
                transcript = data_dir / "root.jsonl"
                transcript.write_text(
                    json.dumps({"type": "session_meta", "payload": {"id": "root"}})
                    + "\n",
                    encoding="utf-8",
                )
                if invalid_target == "state":
                    (data_dir / "state.json").write_bytes(b"\xff")
                else:
                    (data_dir / "state.json").write_text("{}", encoding="utf-8")
                    transcript.write_bytes(b"\xff")
                hook_input = event("PreToolUse", tool_name="collaborationspawn_agent")
                hook_input["transcript_path"] = str(transcript)
                environment = dict(os.environ)
                environment["PLUGIN_DATA"] = str(data_dir)

                result = subprocess.run(
                    [sys.executable, str(MODULE_PATH)],
                    input=json.dumps(hook_input),
                    text=True,
                    capture_output=True,
                    env=environment,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(denied(json.loads(result.stdout)))

    def test_irrelevant_hook_does_not_create_plugin_data(self) -> None:
        with TemporaryDirectory() as directory:
            data_dir = Path(directory) / "plugin-data"

            response = run_hook(
                data_dir, event("PreToolUse", tool_name="functions.exec_command")
            )

            self.assertEqual(response, {})
            self.assertFalse(data_dir.exists())

    def test_persistence_failure_replaces_allow_with_deny(self) -> None:
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            original_data_dir = guard.DATA_DIR
            original_state_path = guard.STATE_PATH
            original_lock_path = guard.LOCK_PATH
            original_events_path = guard.EVENTS_PATH
            guard.DATA_DIR = data_dir
            guard.STATE_PATH = data_dir / "state.json"
            guard.LOCK_PATH = data_dir / "state.lock"
            guard.EVENTS_PATH = data_dir / "events.jsonl"
            stream = io.StringIO()
            transcript = data_dir / "root.jsonl"
            transcript.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": "root"}})
                + "\n",
                encoding="utf-8",
            )
            hook_input = event("PreToolUse", tool_name="collaborationspawn_agent")
            hook_input["transcript_path"] = str(transcript)
            try:
                with mock.patch.object(guard, "read_input", return_value=hook_input), mock.patch.object(
                    guard, "save_state", side_effect=OSError("disk full")
                ) as save, redirect_stdout(stream):
                    guard.main()
            finally:
                guard.DATA_DIR = original_data_dir
                guard.STATE_PATH = original_state_path
                guard.LOCK_PATH = original_lock_path
                guard.EVENTS_PATH = original_events_path

        self.assertTrue(denied(json.loads(stream.getvalue())))
        save.assert_called_once()

    def test_concurrent_distinct_spawns_keep_five_attempt_limit(self) -> None:
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "root.jsonl").write_text(
                json.dumps({"type": "session_meta", "payload": {"id": "root"}})
                + "\n",
                encoding="utf-8",
            )

            def invoke(index: int) -> dict[str, object]:
                hook_input = event("PreToolUse", tool_name="collaborationspawn_agent")
                hook_input["tool_use_id"] = "spawn-{}".format(index)
                return run_hook(data_dir, hook_input)

            with ThreadPoolExecutor(max_workers=6) as executor:
                responses = list(executor.map(invoke, range(6)))

        self.assertEqual(sum(not denied(response) for response in responses), 5)
        self.assertEqual(sum(denied(response) for response in responses), 1)

    def test_client_thread_id_is_recorded(self) -> None:
        ids = guard.extract_thread_ids(
            {"structuredContent": {"clientThreadId": "pending-child"}}
        )

        self.assertEqual(ids, {"pending-child"})


if __name__ == "__main__":
    unittest.main()
