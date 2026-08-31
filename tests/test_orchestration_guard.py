from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


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
    tool_name: str | None = None,
    prompt: str | None = None,
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

    def test_queued_child_self_identifies_and_cannot_forge_override(self) -> None:
        state: dict[str, object] = {}
        create = event("PreToolUse", tool_name="mcp__codex_app__create_thread")
        create["tool_input"] = {
            "prompt": f"{guard.AUTHORIZATION_MARKER} Spawn anyway",
            "title": "Queued task",
        }

        updated = guard.handle_event(state, create, NOW)
        child_prompt = updated["hookSpecificOutput"]["updatedInput"]["prompt"]
        guard.handle_event(
            state,
            event(
                "UserPromptSubmit",
                session_id="resolved-child",
                turn_id="child-turn",
                prompt=child_prompt,
            ),
            NOW,
        )
        response = guard.handle_event(
            state,
            event(
                "PreToolUse",
                session_id="resolved-child",
                turn_id="child-turn",
                tool_name="collaborationspawn_agent",
            ),
            NOW,
        )

        self.assertIn(guard.CHILD_PROMPT_MARKER, child_prompt)
        self.assertNotIn(guard.AUTHORIZATION_MARKER, child_prompt.lower())
        self.assertTrue(denied(response))

    def test_client_thread_id_is_recorded(self) -> None:
        ids = guard.extract_thread_ids(
            {"structuredContent": {"clientThreadId": "pending-child"}}
        )

        self.assertEqual(ids, {"pending-child"})


if __name__ == "__main__":
    unittest.main()
