#!/usr/bin/env python3
"""Enforce bounded Codex orchestration without making model calls."""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple


DEFAULT_DATA_DIR = Path.home() / ".codex" / "plugin-data" / "codex-orchestration-guard"
DATA_DIR = Path(os.environ.get("PLUGIN_DATA", str(DEFAULT_DATA_DIR)))
STATE_PATH = DATA_DIR / "state.json"
LOCK_PATH = DATA_DIR / "state.lock"
EVENTS_PATH = DATA_DIR / "events.jsonl"
DIRECT_AGENT_LIMIT = 5
AUTHORIZATION_MARKER = "[allow-agent-orchestration]"
CHILD_PROMPT_MARKER = "[codex-agent-created-child:no-spawn]"
RETENTION = timedelta(days=8)
MAX_EVENTS_BYTES = 1024 * 1024
RETAIN_EVENTS_BYTES = 512 * 1024
SCOPE_CONTRACT = (
    "Scope contract: deliver one requested outcome. Do not add adjacent refactors, "
    "extra audits, documentation, new tasks, or optional hardening unless the user "
    "asked for them or they are required to make the requested path work. Do not "
    "repeat discovery after evidence answers the question. Run one bounded verification "
    "set. Stop when the requested outcome is proven."
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def orchestration_route(tool_name: Any) -> Optional[str]:
    if not isinstance(tool_name, str):
        return None
    normalized = re.sub(r"[^a-z0-9]", "", tool_name.lower())
    if "createthread" in normalized:
        return "threads"
    if "spawnagent" in normalized:
        return "subagents"
    return None


def deny(reason: str) -> Dict[str, Any]:
    return {
        "systemMessage": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


def extract_thread_ids(value: Any) -> Set[str]:
    found = set()  # type: Set[str]
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {
                "threadId",
                "thread_id",
                "clientThreadId",
                "client_thread_id",
            } and isinstance(child, str) and child:
                found.add(child)
            found.update(extract_thread_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(extract_thread_ids(child))
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if decoded is not None:
            found.update(extract_thread_ids(decoded))
        pattern = (
            r'"(?:threadId|thread_id|clientThreadId|client_thread_id)"'
            r'\s*:\s*"([^"]+)"'
        )
        for match in re.finditer(pattern, value):
            found.add(match.group(1))
    return found


def allow_thread_creation(tool_input: Any) -> Dict[str, Any]:
    if not isinstance(tool_input, dict):
        return deny("Codex orchestration guard: create_thread input was not an object.")
    updated = dict(tool_input)
    prompt = updated.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return deny("Codex orchestration guard: create_thread prompt was missing.")
    prompt = re.sub(
        re.escape(AUTHORIZATION_MARKER), "", prompt, flags=re.IGNORECASE
    ).lstrip()
    if CHILD_PROMPT_MARKER not in prompt:
        prompt = "{}\n{}".format(CHILD_PROMPT_MARKER, prompt)
    updated["prompt"] = prompt
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated,
        }
    }


def transcript_identity(value: Any) -> Tuple[Optional[str], int]:
    if not isinstance(value, str) or not value:
        return None, 0
    try:
        with Path(value).open("r", encoding="utf-8") as handle:
            for _ in range(20):
                line = handle.readline()
                if not line:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "session_meta":
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    return None, 0
                identity = payload.get("id")
                source = payload.get("source")
                depth = 0
                if isinstance(source, dict):
                    subagent = source.get("subagent")
                    spawn = (
                        subagent.get("thread_spawn")
                        if isinstance(subagent, dict)
                        else None
                    )
                    candidate = spawn.get("depth") if isinstance(spawn, dict) else None
                    if isinstance(candidate, int):
                        depth = candidate
                return identity if isinstance(identity, str) and identity else None, depth
    except OSError:
        return None, 0
    return None, 0


def session_record(
    state: Dict[str, Any], session_id: str, now: datetime
) -> Dict[str, Any]:
    sessions = state.setdefault("sessions", {})
    record = sessions.setdefault(session_id, {})
    record["updated_at"] = iso_time(now)
    return record


def current_turn_authorized(
    state: Dict[str, Any], session_id: str, turn_id: str
) -> bool:
    authorized = state.get("authorized_turns")
    return isinstance(authorized, dict) and authorized.get(session_id) == turn_id


def record_child(
    state: Dict[str, Any], child_id: str, parent_id: str, now: datetime
) -> None:
    state.setdefault("child_threads", {})[child_id] = {
        "parent_session_id": parent_id,
        "at": iso_time(now),
    }


def handle_event(
    state: Dict[str, Any], hook_input: Dict[str, Any], now: datetime
) -> Dict[str, Any]:
    event_name = str(hook_input.get("hook_event_name") or "")
    reported_session_id = str(hook_input.get("session_id") or "unknown")
    transcript_session_id, transcript_depth = transcript_identity(
        hook_input.get("transcript_path")
    )
    session_id = transcript_session_id or reported_session_id
    turn_id = str(hook_input.get("turn_id") or "")

    if event_name == "SessionStart":
        record = session_record(state, session_id, now)
        if record.get("scope_contract_injected") is True:
            return {}
        record["scope_contract_injected"] = True
        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": SCOPE_CONTRACT,
            }
        }

    if event_name == "UserPromptSubmit":
        prompt = hook_input.get("prompt")
        if isinstance(prompt, str):
            child_prompt = CHILD_PROMPT_MARKER in prompt
            if child_prompt:
                record_child(state, session_id, "agent-created-task", now)
            if (
                not child_prompt
                and AUTHORIZATION_MARKER in prompt.lower()
                and turn_id
            ):
                state.setdefault("authorized_turns", {})[session_id] = turn_id
        return {}

    if event_name == "SubagentStart":
        agent_id = hook_input.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            record_child(state, agent_id, session_id, now)
        return {}

    route = orchestration_route(hook_input.get("tool_name"))
    if event_name == "PostToolUse":
        if route == "threads":
            for thread_id in extract_thread_ids(hook_input.get("tool_response")):
                record_child(state, thread_id, session_id, now)
        return {}
    if event_name != "PreToolUse" or route is None:
        return {}

    authorized = current_turn_authorized(state, session_id, turn_id)
    transcript_path = hook_input.get("transcript_path")
    if (
        isinstance(transcript_path, str)
        and transcript_path
        and transcript_session_id is None
        and not authorized
    ):
        return deny(
            "Codex orchestration guard: session identity was unavailable, so spawning "
            "was denied fail-closed. Use {} for an operator-approved one-turn "
            "exception.".format(AUTHORIZATION_MARKER)
        )
    children = state.get("child_threads")
    if (
        transcript_depth > 0
        or (isinstance(children, dict) and session_id in children)
    ) and not authorized:
        return deny(
            "Codex orchestration guard: an agent-created child cannot spawn another "
            "task or agent. Use {} for an operator-approved one-turn exception.".format(
                AUTHORIZATION_MARKER
            )
        )

    record = session_record(state, session_id, now)
    existing_route = record.get("route")
    if existing_route is not None and existing_route != route and not authorized:
        return deny(
            "Codex orchestration guard: this root already chose the {} route and cannot "
            "mix task threads with a subagent tree.".format(existing_route)
        )
    if route == "subagents":
        count = int(record.get("direct_agent_spawns") or 0)
        if count >= DIRECT_AGENT_LIMIT and not authorized:
            return deny(
                "Codex orchestration guard: this root already started five direct "
                "agents. Reuse them or obtain an operator-approved one-turn exception."
            )
        record["direct_agent_spawns"] = count + 1
    record["route"] = route
    return allow_thread_creation(hook_input.get("tool_input")) if route == "threads" else {}


def prune_state(state: Dict[str, Any], now: datetime) -> None:
    cutoff = now - RETENTION
    for key in ("sessions", "child_threads"):
        values = state.get(key)
        if not isinstance(values, dict):
            continue
        state[key] = {
            item_id: item
            for item_id, item in values.items()
            if isinstance(item, dict)
            and (parse_time(item.get("updated_at") or item.get("at")) or now) >= cutoff
        }
    authorized = state.get("authorized_turns")
    if isinstance(authorized, dict) and len(authorized) > 500:
        state["authorized_turns"] = dict(list(authorized.items())[-500:])


def complete_tail(raw: bytes, max_bytes: int) -> bytes:
    if len(raw) <= max_bytes:
        return raw
    tail = raw[-max_bytes:]
    newline = tail.find(b"\n")
    return tail[newline + 1 :] if newline >= 0 else b""


def append_event(
    hook_input: Dict[str, Any], response: Dict[str, Any], now: datetime
) -> None:
    if not response.get("systemMessage"):
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "at": iso_time(now),
        "event": hook_input.get("hook_event_name"),
        "tool": hook_input.get("tool_name"),
        "decision": "deny",
    }
    try:
        if EVENTS_PATH.stat().st_size > MAX_EVENTS_BYTES:
            retained = complete_tail(EVENTS_PATH.read_bytes(), RETAIN_EVENTS_BYTES)
            temporary = EVENTS_PATH.with_suffix(".{}.tmp".format(os.getpid()))
            temporary.write_bytes(retained)
            os.replace(temporary, EVENTS_PATH)
    except OSError:
        pass
    with EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def load_state() -> Dict[str, Any]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".{}.tmp".format(os.getpid()))
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(temporary, STATE_PATH)


def read_input() -> Dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read() or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def main() -> int:
    hook_input = read_input()
    now = utc_now()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = load_state()
        response = handle_event(state, hook_input, now)
        prune_state(state, now)
        save_state(state)
        append_event(hook_input, response, now)
    print(json.dumps(response, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
